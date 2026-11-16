from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ai.openrouter import OpenRouterDigester
from .fetchers import fetch_article
from .models import IncidentLog, NewsItem, SourceReport
from .records import RecordWriter, new_incident_id
from .sources import adapter_registry
from .sources.ddg_news import derive_title_from_slug, is_generic_title
from .sources.errors import SourceFetchError

logger = logging.getLogger(__name__)

_MAGNITUDE_PREFIX = re.compile(
    r"^\s*(?:M(?:agnitude)?\s*\d+(?:\.\d+)?|Volcano|Earthquake)\s*[-–—:]\s*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PipelineSettings:
    model: str
    active_window_days: int = 7
    min_log_news_threshold: int = 3
    news_search_window_days: int = 3
    news_timelimit: str = "w"


def load_settings(config_path: str | Path) -> PipelineSettings:
    path = Path(config_path)
    if not path.is_file():
        raise SystemExit(f"config not found: {path}")
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    openrouter = data.get("openrouter", {})
    ingest = data.get("ingest", {})
    model = str(openrouter.get("model") or "")
    if not model:
        raise SystemExit("config.toml is missing [openrouter] model")
    return PipelineSettings(
        model=model,
        active_window_days=int(ingest.get("active_window_days", 7)),
        min_log_news_threshold=int(ingest.get("min_log_news_threshold", 3)),
        news_search_window_days=int(ingest.get("news_search_window_days", 3)),
        news_timelimit=str(ingest.get("news_timelimit", "w")),
    )


def _adapter_by_source(adapters: list[Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for adapter in adapters:
        source = getattr(adapter, "source", "")
        if source:
            out[str(source)] = adapter
    return out


def _normalize_incident_name(name: str) -> str:
    stripped = _MAGNITUDE_PREFIX.sub("", name).strip()
    return stripped or name


def _places_payload(report: SourceReport) -> list[dict[str, str]]:
    return [
        {
            "country_code": place.country_code,
            "subdivision": place.subdivision,
            "locality": place.locality,
        }
        for place in report.places
    ]


def _fetch_reports(adapter: Any) -> list[SourceReport]:
    try:
        return list(adapter.fetch())
    except SourceFetchError as exc:
        logger.warning("adapter %s: fetch failed: %s", type(adapter).__name__, exc)
        return []
    except Exception as exc:
        logger.warning("adapter %s: unexpected fetch error: %r", type(adapter).__name__, exc)
        return []


def _passes_gate(adapter: Any, report: SourceReport) -> bool:
    gate = getattr(adapter, "should_monitor", None)
    return bool(gate(report)) if gate is not None else True


def _ddg_strict_loose(adapter: Any, strict: str, loose: str, timelimit: str) -> list[NewsItem]:
    candidates: list[NewsItem] = []
    if strict:
        candidates.extend(adapter.search(query=strict, timelimit=timelimit))
    if not candidates and loose:
        candidates.extend(adapter.search(query=loose, timelimit=timelimit))
    return candidates


def _enrich_one(news: NewsItem) -> NewsItem:
    fetched = fetch_article(news.url)
    if fetched is None:
        if is_generic_title(news.title):
            derived = derive_title_from_slug(news.url)
            if derived:
                return dataclasses.replace(news, title=derived)
        return news
    fetched_title = fetched.title or ""
    if not is_generic_title(fetched_title):
        final_title = fetched_title
    elif not is_generic_title(news.title):
        final_title = news.title
    else:
        final_title = derive_title_from_slug(news.url) or news.title
    return dataclasses.replace(
        news,
        title=final_title,
        body=fetched.description or news.body,
        published_date=fetched.published_date or news.published_date,
        author=fetched.author,
        sitename=fetched.sitename,
    )


def _enrich_news(items: list[NewsItem]) -> list[NewsItem]:
    return [_enrich_one(item) for item in items]


def ingest_source_reports(adapters: list[Any], writer: RecordWriter) -> int:
    existing = {
        (str(report.get("source") or ""), str(report.get("source_id") or ""))
        for report in writer.reports.values()
    }
    kept = 0
    for adapter in adapters:
        reports = _fetch_reports(adapter)
        logger.info("ingest: %s returned %d reports", type(adapter).__name__, len(reports))
        for report in reports:
            key = (report.source, report.source_id)
            if key in existing:
                continue
            writer.ingest_report(report)
            existing.add(key)
            kept += 1
            logger.info("ingest: stored %s:%s — %s", report.source, report.source_id, report.name)
    return kept


def _commit_news_for_report(
    writer: RecordWriter,
    adapter: Any,
    report: SourceReport,
    report_id: str,
    selected: list[NewsItem],
    active_window_days: int,
) -> None:
    existing = writer.incident_for_report(report_id)
    birthed = existing or writer.find_existing_incident(adapter, report, active_window_days)
    for news in selected:
        news_id = writer.ingest_news(news)
        incident_id = writer.incident_for_news(news_id)
        if incident_id is None:
            if birthed is None:
                birthed = new_incident_id()
            incident_id = birthed
            writer.link_news_to_incident(news_id, incident_id)
        elif birthed is None:
            birthed = incident_id
        writer.link_report_to_incident(report_id, incident_id)
    if birthed is not None:
        if not writer.search_keys(birthed):
            keys = getattr(adapter, "derive_repoll_keys", None)
            repoll_keys = keys(report) if keys is not None else []
            if repoll_keys:
                writer.set_search_keys(birthed, [str(key) for key in repoll_keys])


def _commit_news_for_incident(writer: RecordWriter, incident_id: str, selected: list[NewsItem]) -> None:
    for news in selected:
        news_id = writer.ingest_news(news)
        if writer.incident_for_news(news_id) is None:
            writer.link_news_to_incident(news_id, incident_id)
        elif writer.incident_for_news(news_id) != incident_id:
            logger.warning(
                "news %s already linked to %s; not stealing for %s",
                news_id[:8],
                str(writer.incident_for_news(news_id))[:8],
                incident_id[:8],
            )


def _search_one_report(
    writer: RecordWriter,
    adapter: Any,
    report: SourceReport,
    ddg: Any,
    digester: Any,
    settings: PipelineSettings,
) -> None:
    stored = writer.report_for_key(report.source, report.source_id)
    if stored is not None and writer.report_searched_recently(stored, settings.news_search_window_days):
        logger.info("search: skip %s:%s — searched recently", report.source, report.source_id)
        return
    if not _passes_gate(adapter, report):
        logger.info("search: skip %s:%s — gate", report.source, report.source_id)
        return
    report_id = writer.ingest_report(report)
    strict, loose = adapter.derive_keys(report)
    logger.info("search: %s:%s — keys strict=%r loose=%r", report.source, report.source_id, strict, loose)
    candidates = _ddg_strict_loose(ddg, strict, loose, settings.news_timelimit)
    logger.info("search: %s:%s — %d candidates", report.source, report.source_id, len(candidates))
    if candidates:
        result = digester.filter(
            candidates,
            incident_type=report.incident_type,
            incident_name=_normalize_incident_name(report.name),
            incident_places=_places_payload(report),
            incident_date=report.report_date,
        )
        logger.info("search: %s:%s — %d relevant", report.source, report.source_id, len(result.selected_news))
        if result.selected_news:
            _commit_news_for_report(
                writer,
                adapter,
                report,
                report_id,
                _enrich_news(result.selected_news),
                settings.active_window_days,
            )
    writer.mark_report_searched(report.source, report.source_id, writer.now)


def _repoll_one_incident(
    writer: RecordWriter,
    incident: dict[str, Any],
    ddg: Any,
    digester: Any,
    settings: PipelineSettings,
    adapter_by_source: dict[str, Any],
) -> None:
    incident_id = str(incident.get("id") or "")
    genesis = writer.genesis_report(incident_id)
    if genesis is None:
        return
    adapter = adapter_by_source.get(genesis.source)
    keys_fn = getattr(adapter, "derive_repoll_keys", None) if adapter is not None else None
    if keys_fn is not None:
        keys = keys_fn(genesis)
    else:
        from ._search_keys import derive_repoll_keys

        keys = derive_repoll_keys(genesis)
    seen_urls: set[str] = set()
    candidates: list[NewsItem] = []
    for key in keys:
        for item in ddg.search(query=key, timelimit=settings.news_timelimit):
            if item.url not in seen_urls:
                seen_urls.add(item.url)
                candidates.append(item)
    fresh = [item for item in candidates if writer.find_news_id_by_url(item.url) is None]
    logger.info("repoll: %s — %d candidates, %d fresh", incident_id[:8], len(candidates), len(fresh))
    if not fresh:
        return
    result = digester.filter(
        fresh,
        incident_type=str(incident.get("incident_type") or ""),
        incident_name=_normalize_incident_name(str(incident.get("display_name") or "")),
        incident_places=_places_payload(genesis),
        incident_date=str(incident.get("first_seen") or genesis.report_date),
    )
    logger.info("repoll: %s — %d relevant", incident_id[:8], len(result.selected_news))
    if result.selected_news:
        _commit_news_for_incident(writer, incident_id, _enrich_news(result.selected_news))


def search_news(
    writer: RecordWriter,
    adapters: list[Any],
    ddg: Any,
    digester: Any,
    settings: PipelineSettings,
    *,
    repoll: bool = False,
) -> None:
    if adapters and not repoll:
        for adapter in adapters:
            for report in _fetch_reports(adapter):
                _search_one_report(writer, adapter, report, ddg, digester, settings)
        return
    adapter_by_source = _adapter_by_source(adapters)
    incidents = writer.repolllable_incidents(settings.active_window_days)
    logger.info("repoll: %d incidents", len(incidents))
    for incident in incidents:
        _repoll_one_incident(writer, incident, ddg, digester, settings, adapter_by_source)


def _summarize_incident(writer: RecordWriter, digester: Any, incident: dict[str, Any], threshold: int) -> None:
    incident_id = str(incident.get("id") or "")
    summarized = writer.summarized_news_ids(incident_id)
    unsummarized = [news_id for news_id in writer.news_ids_for_incident(incident_id) if news_id not in summarized]
    if len(unsummarized) < threshold:
        return
    news = [writer.news_model(news_id) for news_id in unsummarized]
    prior = [
        IncidentLog(
            incident_id=incident_id,
            log_date=str(log.get("log_date") or ""),
            summary=str(log.get("summary") or ""),
        )
        for log in writer.logs_for_incident(incident_id)
    ]
    genesis = writer.genesis_report(incident_id)
    result = digester.summarize(
        news,
        prior,
        incident_type=str(incident.get("incident_type") or ""),
        incident_name=str(incident.get("display_name") or ""),
        incident_places=_places_payload(genesis) if genesis is not None else [],
        incident_date=str(incident.get("first_seen") or ""),
    )
    if not result.has_relevant_updates:
        logger.info("logs: %s — no relevant updates", incident_id[:8])
        return
    writer.append_log(incident_id, writer.today, result.summary, set(unsummarized))
    logger.info("logs: %s — wrote %s with %d news", incident_id[:8], writer.today, len(unsummarized))


def generate_logs(writer: RecordWriter, digester: Any, settings: PipelineSettings) -> None:
    for incident in sorted(writer.incidents.values(), key=lambda item: str(item.get("id") or "")):
        if incident.get("merged_into"):
            continue
        _summarize_incident(writer, digester, incident, settings.min_log_news_threshold)


def _digester(settings: PipelineSettings, api_key: str) -> OpenRouterDigester:
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is required")
    return OpenRouterDigester(settings.model, api_key)


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data", default="data", type=Path)
    parser.add_argument("--config", default="config.toml", type=Path)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--api-key-env", default="OPENROUTER_API_KEY")
    parser.add_argument("-v", "--verbose", action="store_true")


def _make_digester(args: argparse.Namespace, settings: PipelineSettings) -> OpenRouterDigester:
    api_key = args.api_key or os.environ.get(args.api_key_env, "")
    return _digester(settings, api_key)


def main_ingest() -> None:
    parser = argparse.ArgumentParser(description="Ingest official source reports into v2 records")
    _common_args(parser)
    args = parser.parse_args()
    _configure_logging(args.verbose)
    load_settings(args.config)
    writer = RecordWriter(args.data)
    kept = ingest_source_reports(list(adapter_registry().values()), writer)
    writer.flush()
    print(f"ingested {kept} new reports; {writer.counts()}")


def main_search_news() -> None:
    parser = argparse.ArgumentParser(description="Search and classify news for v2 incidents")
    _common_args(parser)
    parser.add_argument("--repoll", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    _configure_logging(args.verbose)
    settings = load_settings(args.config)
    writer = RecordWriter(args.data)
    adapters = list(adapter_registry().values())
    digester = _make_digester(args, settings)
    from .sources.ddg_news import DuckDuckGoNewsAdapter

    if args.limit:
        adapters = adapters[: args.limit]
    search_news(writer, adapters, DuckDuckGoNewsAdapter(), digester, settings, repoll=args.repoll)
    writer.flush()
    print(f"news search done; {writer.counts()}")


def main_generate_logs() -> None:
    parser = argparse.ArgumentParser(description="Generate v2 incident logs from unsummarized news")
    _common_args(parser)
    args = parser.parse_args()
    _configure_logging(args.verbose)
    settings = load_settings(args.config)
    writer = RecordWriter(args.data)
    digester = _make_digester(args, settings)
    generate_logs(writer, digester, settings)
    writer.flush()
    print(f"log generation done; {writer.counts()}")


def main_pipeline() -> None:
    parser = argparse.ArgumentParser(description="Run the full v2 ingestion, news, and log pipeline")
    _common_args(parser)
    args = parser.parse_args()
    _configure_logging(args.verbose)
    settings = load_settings(args.config)
    writer = RecordWriter(args.data)
    adapters = list(adapter_registry().values())
    digester = _make_digester(args, settings)
    from .sources.ddg_news import DuckDuckGoNewsAdapter

    kept = ingest_source_reports(adapters, writer)
    search_news(writer, adapters, DuckDuckGoNewsAdapter(), digester, settings)
    search_news(writer, adapters, DuckDuckGoNewsAdapter(), digester, settings, repoll=True)
    generate_logs(writer, digester, settings)
    writer.flush()
    print(f"pipeline done: {kept} new reports; {writer.counts()}")


if __name__ == "__main__":
    main_pipeline()
