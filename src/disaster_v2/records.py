from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .countries import country_label, region_for_country
from .ids import news_uuid, report_uuid
from .models import NewsItem, ReportPlace, SourceReport
from .util import (
    append_jsonl,
    as_dict,
    as_list,
    canonical_url,
    content_hash,
    date_str,
    dump_yaml,
    iter_yaml,
    load_yaml,
    max_date,
    min_date,
    read_jsonl,
    slugify,
    utc_now_iso,
)

_AGGREGATOR_DOMAINS = (
    "msn.com",
    "yahoo.com",
    "aol.com",
    "flipboard.com",
    "news.google.com",
)
_ACTIVE_DAYS = 30
_DORMANT_DAYS = 180


def _as_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _is_aggregator(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in _AGGREGATOR_DOMAINS)


def _status_for(last_verified: str, as_of: str) -> str:
    verified = date.fromisoformat(last_verified) if last_verified else None
    current = date.fromisoformat(as_of) if as_of else date.today()
    if verified is None:
        return "archived"
    if verified >= current - timedelta(days=_ACTIVE_DAYS):
        return "active"
    if verified >= current - timedelta(days=_DORMANT_DAYS):
        return "dormant"
    return "archived"


def _place_dict(place: ReportPlace) -> dict[str, str]:
    return {
        "country_code": place.country_code,
        "subdivision": place.subdivision,
        "locality": place.locality,
    }


def _place_model(place: dict[str, Any]) -> ReportPlace:
    return ReportPlace(
        country_code=str(place.get("country_code") or ""),
        subdivision=str(place.get("subdivision") or ""),
        locality=str(place.get("locality") or ""),
    )


class RecordWriter:
    """Mutable v2 canonical store used by the ingestion and submission pipelines."""

    def __init__(self, root: str | Path, *, now: str | None = None) -> None:
        self.root = Path(root)
        self.now = now or utc_now_iso()
        self.today = self.now[:10]
        self.incidents: dict[str, dict[str, Any]] = {}
        self.reports: dict[str, dict[str, Any]] = {}
        self.news: dict[str, dict[str, Any]] = {}
        self.logs: dict[str, dict[str, dict[str, Any]]] = {}
        self.links: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

        self._report_by_key: dict[tuple[str, str], str] = {}
        self._news_by_url: dict[str, str] = {}
        self._report_incident: dict[str, str] = {}
        self._news_incident: dict[str, str] = {}
        self._news_log: dict[str, str] = {}
        self._link_keys: set[tuple[str, str, str]] = set()
        self._new_links: list[dict[str, Any]] = []
        self._new_events: list[dict[str, Any]] = []
        self._dirty_reports: set[str] = set()
        self._dirty_news: set[str] = set()
        self._dirty_incidents: set[str] = set()
        self._dirty_logs: set[tuple[str, str]] = set()
        self._load()

    # --------------------------------------------------------------- loading

    def _load(self) -> None:
        for path in iter_yaml(self.root / "records" / "incidents", recursive=False):
            data = load_yaml(path)
            incident_id = str(data.get("id") or path.stem)
            data["id"] = incident_id
            self.incidents[incident_id] = data

        for path in iter_yaml(self.root / "records" / "reports", recursive=False):
            data = load_yaml(path)
            report_id = str(data.get("id") or path.stem)
            data["id"] = report_id
            self.reports[report_id] = data
            self._report_by_key[(str(data.get("source") or ""), str(data.get("source_id") or ""))] = report_id

        for path in iter_yaml(self.root / "records" / "news", recursive=False):
            data = load_yaml(path)
            news_id = str(data.get("id") or path.stem)
            data["id"] = news_id
            self.news[news_id] = data
            self._index_news_urls(data, news_id)

        logs_root = self.root / "records" / "logs"
        if logs_root.is_dir():
            for incident_dir in sorted(p for p in logs_root.iterdir() if p.is_dir()):
                for path in iter_yaml(incident_dir, recursive=False):
                    data = load_yaml(path)
                    log_date = str(data.get("log_date") or path.stem)
                    data["log_date"] = log_date
                    data["incident_id"] = str(data.get("incident_id") or incident_dir.name)
                    self.logs.setdefault(incident_dir.name, {})[log_date] = data

        for path in sorted((self.root / "links").glob("*.jsonl")):
            self.links.extend(read_jsonl(path))
        for path in sorted((self.root / "events").glob("*.jsonl")):
            self.events.extend(read_jsonl(path))
        for link in self.links:
            self._index_link(link)

    def _index_news_urls(self, data: dict[str, Any], news_id: str) -> None:
        for value in (data.get("url"), data.get("canonical_url")):
            key = str(value or "")
            if key:
                self._news_by_url.setdefault(key, news_id)

    def _index_link(self, link: dict[str, Any]) -> None:
        kind = str(link.get("kind") or "")
        source = str(link.get("from") or "")
        target = str(link.get("to") or "")
        self._link_keys.add((source, target, kind))
        if kind == "incident_report" and source.startswith("incident:") and target.startswith("report:"):
            self._report_incident[target.split(":", 1)[1]] = source.split(":", 1)[1]
        elif kind == "incident_news" and source.startswith("incident:") and target.startswith("news:"):
            self._news_incident[target.split(":", 1)[1]] = source.split(":", 1)[1]
        elif kind == "news_log" and source.startswith("news:") and target.startswith("log:"):
            self._news_log[source.split(":", 1)[1]] = target.split(":", 1)[1].rsplit(":", 1)[-1]

    # --------------------------------------------------------------- helpers

    @property
    def _reports(self) -> dict[str, dict[str, Any]]:
        return self.reports

    @property
    def _report_incident_map(self) -> dict[str, str]:
        return self._report_incident

    def _record_path(self, kind: str, record_id: str) -> Path:
        return self.root / "records" / kind / f"{record_id}.yaml"

    def _log_path(self, incident_id: str, log_date: str) -> Path:
        return self.root / "records" / "logs" / incident_id / f"{log_date}.yaml"

    def _event(self, event_type: str, **fields: Any) -> None:
        self._new_events.append({"type": event_type, "at": self.now, **fields})

    def _add_link(
        self,
        source: str,
        target: str,
        kind: str,
        *,
        method: str = "pipeline",
        role: str = "",
    ) -> None:
        key = (source, target, kind)
        if key in self._link_keys:
            return
        link: dict[str, Any] = {
            "from": source,
            "to": target,
            "kind": kind,
            "method": method,
            "at": self.today,
        }
        if role:
            link["role"] = role
        self._link_keys.add(key)
        self.links.append(link)
        self._new_links.append(link)
        self._index_link(link)

    def _save_report(self, report_id: str) -> None:
        dump_yaml(self._record_path("reports", report_id), self.reports[report_id])
        self._dirty_reports.add(report_id)

    def _save_news(self, news_id: str) -> None:
        dump_yaml(self._record_path("news", news_id), self.news[news_id])
        self._dirty_news.add(news_id)

    def _save_incident(self, incident_id: str) -> None:
        dump_yaml(self._record_path("incidents", incident_id), self.incidents[incident_id])
        self._dirty_incidents.add(incident_id)

    def _save_log(self, incident_id: str, log_date: str) -> None:
        dump_yaml(self._log_path(incident_id, log_date), self.logs[incident_id][log_date])
        self._dirty_logs.add((incident_id, log_date))

    # ------------------------------------------------------------- incidents

    def ensure_incident(
        self,
        incident_id: str,
        report: SourceReport | None = None,
        *,
        display_name: str = "",
        incident_type: str = "",
        country_code: str = "",
        event_date: str = "",
    ) -> str:
        if incident_id in self.incidents:
            return incident_id
        report = report
        places = report.places if report else []
        code = country_code
        if not code:
            code = next((place.country_code for place in places if place.country_code), "")
        name = display_name or (report.name if report else "") or f"Incident {incident_id[:8]}"
        kind = incident_type or (report.incident_type if report else "") or "Unknown"
        event = event_date or (report.report_date if report else "") or self.today
        incident = {
            "schema": 2,
            "id": incident_id,
            "status": _status_for(event, self.today),
            "display_name": name,
            "slug": slugify(name),
            "incident_type": kind,
            "country_code": code,
            "country_name": country_label(code),
            "region": region_for_country(code),
            "first_seen": event,
            "event_date": event,
            "last_verified_update": event,
            "latest_coverage_at": event,
            "status_changed_at": event,
            "summary": "",
            "summary_updated": "",
            "search_keys": [],
            "extended_monitoring": False,
            "aliases": [],
            "merged_into": None,
            "genesis": None,
            "external_keys": [],
            "coverage_counts": {"reports": 0, "news": 0, "logs": 0, "official_sources": 0},
            "created_at": self.now,
            "pipeline_created_at": self.now,
        }
        self.incidents[incident_id] = incident
        self._save_incident(incident_id)
        self._event("incident_created", incident_id=incident_id, name=name)
        return incident_id

    def _recompute_incident(self, incident_id: str) -> None:
        incident = self.incidents.get(incident_id)
        if incident is None:
            return
        report_ids = self.report_ids_for_incident(incident_id)
        news_ids = self.news_ids_for_incident(incident_id)
        logs = self.logs_for_incident(incident_id)
        reports = [self.reports[rid] for rid in report_ids if rid in self.reports]
        report_dates = [date_str(report.get("report_date")) for report in reports]
        news_dates = [date_str(self.news[nid].get("published_date")) for nid in news_ids if nid in self.news]
        log_dates = [str(log.get("log_date") or "") for log in logs]
        first_seen = min_date(*report_dates, *news_dates, *log_dates) or str(incident.get("first_seen") or "")
        genesis = min(
            reports,
            key=lambda report: (date_str(report.get("report_date")), str(report.get("id") or "")),
            default=None,
        )
        event_date = date_str(genesis.get("report_date")) if genesis else str(incident.get("event_date") or first_seen)
        last_verified = max_date(*log_dates) or str(incident.get("last_verified_update") or event_date)
        latest_coverage = max_date(*report_dates, *news_dates, *log_dates) or last_verified
        status = _status_for(last_verified, self.today)
        if status != incident.get("status"):
            incident["status_changed_at"] = self.now
        incident["status"] = status
        incident["first_seen"] = first_seen
        incident["event_date"] = event_date
        incident["last_verified_update"] = last_verified
        incident["latest_coverage_at"] = latest_coverage
        incident["coverage_counts"] = {
            "reports": len(reports),
            "news": len(news_ids),
            "logs": len(logs),
            "official_sources": len({str(report.get("source") or "") for report in reports}),
        }
        if genesis:
            incident["genesis"] = {
                "report_id": str(genesis.get("id") or ""),
                "source": str(genesis.get("source") or ""),
                "source_id": str(genesis.get("source_id") or ""),
            }
            if not incident.get("display_name") or str(incident.get("display_name")).startswith("Incident "):
                incident["display_name"] = str(genesis.get("name") or incident.get("display_name") or "")
                incident["slug"] = slugify(str(incident["display_name"]))
            if not incident.get("incident_type") or incident.get("incident_type") == "Unknown":
                incident["incident_type"] = str(
                    genesis.get("incident_type") or incident.get("incident_type") or "Unknown"
                )
        if not incident.get("country_code"):
            for report in reports:
                for place in as_list(report.get("places")):
                    code = str(as_dict(place).get("country_code") or "").upper()
                    if len(code) == 2:
                        incident["country_code"] = code
                        incident["country_name"] = country_label(code)
                        incident["region"] = region_for_country(code)
                        break
                if incident.get("country_code"):
                    break
        incident["external_keys"] = [
            {
                "source": str(report.get("source") or ""),
                "source_id": str(report.get("source_id") or ""),
                "report_id": str(report.get("id") or ""),
            }
            for report in reports
        ]
        self._save_incident(incident_id)

    def report_ids_for_incident(self, incident_id: str) -> list[str]:
        return sorted(
            rid for rid, linked in self._report_incident.items() if linked == incident_id and rid in self.reports
        )

    def news_ids_for_incident(self, incident_id: str) -> list[str]:
        return sorted(
            nid for nid, linked in self._news_incident.items() if linked == incident_id and nid in self.news
        )

    def logs_for_incident(self, incident_id: str) -> list[dict[str, Any]]:
        return [self.logs[incident_id][log_date] for log_date in sorted(self.logs.get(incident_id, {}))]

    def incident_for_report(self, report_id: str) -> str | None:
        return self._report_incident.get(report_id)

    def incident_for_news(self, news_id: str) -> str | None:
        return self._news_incident.get(news_id)

    def news_ids_for_log(self, incident_id: str, log_date: str) -> list[str]:
        log = self.logs.get(incident_id, {}).get(log_date)
        return [str(nid) for nid in as_list(log.get("news_ids"))] if log else []

    def summarized_news_ids(self, incident_id: str) -> set[str]:
        linked_logs = {str(log.get("log_date") or "") for log in self.logs_for_incident(incident_id)}
        return {
            nid
            for nid in self.news_ids_for_incident(incident_id)
            if nid in self._news_log and self._news_log[nid] in linked_logs
        }

    # --------------------------------------------------------------- reports

    def ingest_report(self, report: SourceReport) -> str:
        report_id = report_uuid(report.source, report.source_id)
        if report_id in self.reports:
            return report_id
        payload = {
            "schema": 2,
            "id": report_id,
            "source": report.source,
            "source_id": report.source_id,
            "incident_type": report.incident_type,
            "name": report.name,
            "report_date": report.report_date,
            "news_searched_at": report.news_searched_at,
            "places": [_place_dict(place) for place in report.places],
            "raw_fields": report.raw_fields,
            "content_hash": content_hash(report.source, report.source_id, report.raw_fields),
        }
        self.reports[report_id] = payload
        self._report_by_key[(report.source, report.source_id)] = report_id
        self._save_report(report_id)
        self._event("report_ingested", report_id=report_id, source=report.source, source_id=report.source_id)
        return report_id

    def source_report(self, report_id: str) -> SourceReport | None:
        report = self.reports.get(report_id)
        if report is None:
            return None
        return SourceReport(
            source=str(report.get("source") or ""),
            source_id=str(report.get("source_id") or ""),
            incident_type=str(report.get("incident_type") or ""),
            name=str(report.get("name") or ""),
            places=[_place_model(place) for place in as_list(report.get("places")) if isinstance(place, dict)],
            report_date=str(report.get("report_date") or ""),
            raw_fields=as_dict(report.get("raw_fields")),
            news_searched_at=str(report.get("news_searched_at") or ""),
        )

    def report_for_key(self, source: str, source_id: str) -> dict[str, Any] | None:
        report_id = self._report_by_key.get((source, source_id))
        return self.reports.get(report_id) if report_id else None

    def mark_report_searched(self, source: str, source_id: str, timestamp: str) -> None:
        report_id = self._report_by_key.get((source, source_id))
        if not report_id or report_id not in self.reports:
            return
        self.reports[report_id]["news_searched_at"] = timestamp
        self._save_report(report_id)

    def report_searched_recently(self, report: dict[str, Any], days: int = 3) -> bool:
        searched = _as_datetime(report.get("news_searched_at"))
        current = _as_datetime(self.now)
        if searched is None or current is None:
            return False
        return searched >= current - timedelta(days=days)

    def genesis_report(self, incident_id: str) -> SourceReport | None:
        incident = self.incidents.get(incident_id) or {}
        genesis = incident.get("genesis")
        genesis_id = str(genesis.get("report_id") or "") if isinstance(genesis, dict) else ""
        if genesis_id:
            return self.source_report(genesis_id)
        report_ids = self.report_ids_for_incident(incident_id)
        if not report_ids:
            return None
        earliest = min(report_ids, key=lambda rid: (str(self.reports[rid].get("report_date") or ""), rid))
        return self.source_report(earliest)

    # ------------------------------------------------------------------ news

    def ingest_news(self, item: NewsItem) -> str:
        canonical = canonical_url(item.url)
        existing = self._news_by_url.get(canonical) or self._news_by_url.get(item.url)
        if existing:
            return existing
        news_id = item.news_id or news_uuid(item.url)
        if news_id in self.news:
            return news_id
        payload = {
            "schema": 2,
            "id": news_id,
            "url": item.url,
            "title": item.title,
            "body": item.body,
            "published_date": item.published_date,
            "source": item.source,
            "domain": item.domain,
            "image": item.image,
            "author": item.author,
            "sitename": item.sitename,
            "canonical_url": canonical,
            "content_hash": content_hash(item.url, item.title, item.body, item.published_date),
            "is_aggregator": _is_aggregator(item.url),
            "log_dates": [],
        }
        self.news[news_id] = payload
        self._index_news_urls(payload, news_id)
        self._save_news(news_id)
        self._event("news_ingested", news_id=news_id, url=item.url)
        return news_id

    def news_model(self, news_id: str) -> NewsItem:
        data = self.news[news_id]
        return NewsItem(
            url=str(data.get("url") or ""),
            title=str(data.get("title") or ""),
            body=str(data.get("body") or ""),
            published_date=str(data.get("published_date") or ""),
            source=str(data.get("source") or ""),
            domain=str(data.get("domain") or ""),
            image=str(data.get("image") or ""),
            news_id=news_id,
            author=str(data.get("author") or ""),
            sitename=str(data.get("sitename") or ""),
        )

    def news_models_for_incident(self, incident_id: str) -> list[NewsItem]:
        return [self.news_model(nid) for nid in self.news_ids_for_incident(incident_id)]

    def find_news_id_by_url(self, url: str) -> str | None:
        return self._news_by_url.get(canonical_url(url)) or self._news_by_url.get(url)

    def find_existing_incident(self, adapter: Any, report: SourceReport, window_days: int = 7) -> str | None:
        finder = getattr(adapter, "find_existing_incident", None)
        if finder is not None:
            result = finder(self, report, window_days)
            if result:
                return str(result)
        return self.find_active_incident_by_type_country(
            report.incident_type,
            {place.country_code for place in report.places if place.country_code},
            window_days,
        )

    def find_active_incident_by_type_country(
        self, incident_type: str, country_codes: set[str], window_days: int
    ) -> str | None:
        if not incident_type or not country_codes:
            return None
        for incident in self.active_incidents(window_days):
            if str(incident.get("incident_type") or "") != incident_type:
                continue
            genesis = self.genesis_report(str(incident.get("id") or ""))
            if genesis is not None:
                if any(place.country_code in country_codes for place in genesis.places if place.country_code):
                    return str(incident.get("id") or "")
            elif str(incident.get("country_code") or "") in country_codes:
                return str(incident.get("id") or "")
        return None

    def active_incidents(self, window_days: int) -> list[dict[str, Any]]:
        cutoff = (_as_datetime(self.now) or datetime.now(UTC)) - timedelta(days=window_days)
        active: list[dict[str, Any]] = []
        for incident in self.incidents.values():
            if incident.get("merged_into"):
                continue
            reference = _as_datetime(
                str(
                    incident.get("latest_coverage_at")
                    or incident.get("last_verified_update")
                    or incident.get("event_date")
                    or ""
                )
            )
            if reference is not None and reference >= cutoff:
                active.append(incident)
            elif incident.get("extended_monitoring"):
                active.append(incident)
        active.sort(key=lambda item: (str(item.get("first_seen") or ""), str(item.get("id") or "")))
        return active

    def repolllable_incidents(self, window_days: int) -> list[dict[str, Any]]:
        return self.active_incidents(window_days)

    # ---------------------------------------------------------------- links

    def link_report_to_incident(self, report_id: str, incident_id: str, *, role: str = "supporting") -> None:
        self.ensure_incident(incident_id)
        self._add_link(f"incident:{incident_id}", f"report:{report_id}", "incident_report", role=role)
        self._report_incident[report_id] = incident_id
        self._recompute_incident(incident_id)
        self._event("report_linked", report_id=report_id, incident_id=incident_id)

    def link_news_to_incident(self, news_id: str, incident_id: str) -> None:
        if news_id not in self.news:
            return
        self.ensure_incident(incident_id)
        self._add_link(f"incident:{incident_id}", f"news:{news_id}", "incident_news")
        self._news_incident[news_id] = incident_id
        self._recompute_incident(incident_id)
        self._event("news_linked", news_id=news_id, incident_id=incident_id)

    def append_log(self, incident_id: str, log_date: str, summary: str, news_ids: set[str]) -> None:
        bucket = self.logs.setdefault(incident_id, {})
        existing = bucket.get(log_date)
        if existing is None:
            existing = {
                "schema": 2,
                "incident_id": incident_id,
                "log_date": log_date,
                "summary": summary,
                "news_ids": sorted(news_ids),
            }
            bucket[log_date] = existing
        else:
            old_summary = str(existing.get("summary") or "")
            if summary and summary not in old_summary:
                existing["summary"] = f"{old_summary}\n{summary}".strip()
            existing["news_ids"] = sorted(set(as_list(existing.get("news_ids"))) | news_ids)
        for news_id in news_ids:
            if news_id not in self.news:
                continue
            self._add_link(f"news:{news_id}", f"log:{incident_id}:{log_date}", "news_log")
            self._news_log[news_id] = log_date
            log_dates = sorted(set(as_list(self.news[news_id].get("log_dates"))) | {log_date})
            self.news[news_id]["log_dates"] = log_dates
            self._save_news(news_id)
            if self._news_incident.get(news_id) is None:
                self._news_incident[news_id] = incident_id
                self._add_link(f"incident:{incident_id}", f"news:{news_id}", "incident_news")
        self._save_log(incident_id, log_date)
        incident = self.incidents[incident_id]
        incident["summary"] = str(existing.get("summary") or "")
        incident["summary_updated"] = log_date
        self._recompute_incident(incident_id)
        self._event("log_created", incident_id=incident_id, log_date=log_date, news=len(news_ids))

    # -------------------------------------------------------------- metadata

    def set_search_keys(self, incident_id: str, keys: list[str]) -> None:
        incident = self.incidents.get(incident_id)
        if incident is None:
            return
        incident["search_keys"] = list(keys)
        self._save_incident(incident_id)

    def search_keys(self, incident_id: str) -> list[str]:
        return [str(key) for key in as_list((self.incidents.get(incident_id) or {}).get("search_keys"))]

    def set_extended_monitoring(self, incident_id: str, enabled: bool) -> None:
        incident = self.incidents.get(incident_id)
        if incident is None:
            return
        incident["extended_monitoring"] = bool(enabled)
        self._save_incident(incident_id)

    # ---------------------------------------------------------------- flush

    def flush(self) -> None:
        for report_id in sorted(self._dirty_reports):
            if report_id in self.reports:
                dump_yaml(self._record_path("reports", report_id), self.reports[report_id])
        for news_id in sorted(self._dirty_news):
            if news_id in self.news:
                dump_yaml(self._record_path("news", news_id), self.news[news_id])
        for incident_id in sorted(self._dirty_incidents):
            if incident_id in self.incidents:
                dump_yaml(self._record_path("incidents", incident_id), self.incidents[incident_id])
        for incident_id, log_date in sorted(self._dirty_logs):
            if log_date in self.logs.get(incident_id, {}):
                dump_yaml(self._log_path(incident_id, log_date), self.logs[incident_id][log_date])
        links_by_month: dict[str, list[dict[str, Any]]] = {}
        for link in self._new_links:
            month = str(link.get("at") or self.today)[:7]
            links_by_month.setdefault(month, []).append(link)
        for month, rows in sorted(links_by_month.items()):
            append_jsonl(self.root / "links" / f"{month}.jsonl", rows)
        events_by_month: dict[str, list[dict[str, Any]]] = {}
        for event in self._new_events:
            month = str(event.get("at") or self.today)[:7]
            events_by_month.setdefault(month, []).append(event)
        for month, rows in sorted(events_by_month.items()):
            append_jsonl(self.root / "events" / f"{month}.jsonl", rows)
        self._dirty_reports.clear()
        self._dirty_news.clear()
        self._dirty_incidents.clear()
        self._dirty_logs.clear()
        self._new_links.clear()
        self._new_events.clear()

    def counts(self) -> dict[str, int]:
        return {
            "incidents": len(self.incidents),
            "reports": len(self.reports),
            "news": len(self.news),
            "logs": sum(len(logs) for logs in self.logs.values()),
        }


def new_incident_id() -> str:
    return uuid.uuid4().hex
