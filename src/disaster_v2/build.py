from __future__ import annotations

import argparse
import os
import shutil
import tomllib
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import __version__
from .countries import country_label, region_for_country
from .key_links import select_key_links, source_links
from .render import render_site
from .store import Store
from .util import as_dict, as_list, max_date, min_date, utc_now_iso, write_json

_ALERT_RANK = {"green": 1, "yellow": 2, "orange": 3, "red": 4}
_NEWS_KEYS = ("id", "url", "title", "body", "published_date", "source", "domain", "image", "is_aggregator")


def load_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _raw(report: dict[str, Any]) -> dict[str, Any]:
    return as_dict(report.get("raw_fields"))


def physical_facts(reports: list[dict[str, Any]], incident: dict[str, Any]) -> dict[str, Any]:
    mag: float | None = None
    depth: float | None = None
    place = ""
    tsunami = False
    sig = 0
    alert = ""
    lat: float | None = None
    lon: float | None = None
    event_time = ""

    for report in reports:
        raw = _raw(report)
        source = str(report.get("source") or "")
        if source == "USGS":
            value = raw.get("mag")
            if value is not None:
                mag = max(mag or 0.0, float(value))
            value = raw.get("depth")
            if value is not None:
                depth = float(value) if depth is None else min(depth, float(value))
            place = place or str(raw.get("place") or "")
            tsunami = tsunami or bool(raw.get("tsunami"))
            value = raw.get("sig")
            if value is not None:
                sig = max(sig, int(value))
            value = raw.get("alert")
            if value:
                alert = str(value)
            geometry = as_dict(raw.get("geometry"))
            coords = as_list(geometry.get("coordinates"))
            if len(coords) >= 2 and lon is None:
                lon, lat = float(coords[0]), float(coords[1])
            if raw.get("time"):
                event_time = event_time or str(raw.get("time"))
        elif source == "GDACS":
            value = raw.get("alertlevel")
            if value and _ALERT_RANK.get(str(value).lower(), 0) > _ALERT_RANK.get(alert.lower(), 0):
                alert = str(value)
            place = place or str(raw.get("country") or "")
            if raw.get("geo_lat") is not None and lat is None:
                lat = float(raw["geo_lat"])
            if raw.get("geo_long") is not None and lon is None:
                lon = float(raw["geo_long"])
        elif source == "ERCC":
            place = place or str(raw.get("description") or raw.get("title") or "")

    if not place:
        place = str(incident.get("country_name") or "")
    return {
        "max_magnitude": mag,
        "max_depth_km": depth,
        "place": place,
        "tsunami": tsunami,
        "sig": sig or None,
        "official_alert": alert or "",
        "latitude": lat,
        "longitude": lon,
        "event_time": event_time,
    }


def _news_view(news: dict[str, Any], *, log_date: str = "") -> dict[str, Any]:
    return {
        "news_id": str(news.get("id") or ""),
        "url": str(news.get("url") or ""),
        "headline": str(news.get("title") or ""),
        "body": str(news.get("body") or ""),
        "published_date": str(news.get("published_date") or ""),
        "outlet": str(news.get("source") or news.get("domain") or ""),
        "domain": str(news.get("domain") or ""),
        "image": str(news.get("image") or ""),
        "is_aggregator": bool(news.get("is_aggregator", False)),
        "log_date": log_date,
    }


def _status_for(last_verified: str, as_of: str) -> str:
    verified = date.fromisoformat(last_verified) if last_verified else None
    current = date.fromisoformat(as_of) if as_of else date.today()
    if verified is None:
        return "archived"
    if verified >= current - timedelta(days=30):
        return "active"
    if verified >= current - timedelta(days=180):
        return "dormant"
    return "archived"


def _coverage_articles(store: Store, incident_id: str, logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    log_dates_by_news: dict[str, str] = {}
    for log in logs:
        for news_id in as_list(log.get("news_ids")):
            log_dates_by_news.setdefault(str(news_id), str(log.get("log_date") or ""))
    articles: list[dict[str, Any]] = []
    for news in store.news_for_incident(incident_id):
        view = _news_view(news, log_date=log_dates_by_news.get(str(news.get("id")), ""))
        articles.append(view)
    articles.sort(key=lambda item: (item.get("published_date") or "", item.get("headline") or ""), reverse=True)
    return articles


def _cluster_key(article: dict[str, Any]) -> str:
    import re

    title = re.sub(r"[^a-z0-9]+", "", str(article.get("headline") or "").lower())
    return f"{title[:60]}|{str(article.get('published_date') or '')[:10]}"


def _clusters(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for article in articles:
        key = _cluster_key(article)
        if not key.strip("|"):
            key = str(article.get("news_id") or "")
        groups.setdefault(key, []).append(article)
    clusters: list[dict[str, Any]] = []
    for key, items in groups.items():
        items.sort(
            key=lambda item: (
                not bool(item.get("is_aggregator")),
                item.get("published_date") or "",
            ),
            reverse=True,
        )
        representative = items[0]
        outlets = sorted({str(item.get("outlet") or item.get("domain") or "") for item in items})
        dates = sorted(str(item.get("published_date") or "")[:10] for item in items if item.get("published_date"))
        clusters.append(
            {
                "cluster_id": str(abs(hash(key)))[:12],
                "title": representative.get("headline") or "",
                "url": representative.get("url") or "",
                "outlet": representative.get("outlet") or "",
                "published_date": representative.get("published_date") or "",
                "outlets": [outlet for outlet in outlets if outlet],
                "outlet_count": len([outlet for outlet in outlets if outlet]),
                "article_ids": [item.get("news_id") for item in items],
                "count": len(items),
                "date_range": [dates[0], dates[-1]] if dates else [],
            }
        )
    clusters.sort(key=lambda item: (item.get("published_date") or "", item.get("title") or ""), reverse=True)
    return clusters


def _build_incident(store: Store, incident_id: str, *, as_of: str, config: dict[str, Any]) -> dict[str, Any]:
    incident = dict(store.incidents[incident_id])
    reports = store.reports_for_incident(incident_id)
    news = store.news_for_incident(incident_id)
    logs = store.logs_for_incident(incident_id)
    official_links = source_links(reports)
    physical = physical_facts(reports, incident)
    log_views: list[dict[str, Any]] = []
    for log in logs:
        items = [
            _news_view(store.news[news_id], log_date=str(log.get("log_date") or ""))
            for news_id in as_list(log.get("news_ids"))
            if news_id in store.news
        ]
        items.sort(key=lambda item: item.get("published_date") or "")
        log_views.append(
            {
                "log_date": str(log.get("log_date") or ""),
                "summary": str(log.get("summary") or ""),
                "news": items,
            }
        )
    news_views = [_news_view(item) for item in news]
    key_links = select_key_links(
        official_links,
        log_views,
        news_views,
        {**incident, "physical": physical},
    )
    last_verified = max_date(*(log["log_date"] for log in log_views)) or str(incident.get("last_verified_update") or "")
    latest_coverage = max_date(
        *(item.get("published_date") for item in news_views),
        *((log["log_date"] for log in log_views)),
    ) or last_verified
    status = _status_for(last_verified, as_of)
    coverage_dates = [str(item.get("published_date") or "")[:10] for item in news_views if item.get("published_date")]
    source_counts = Counter(str(report.get("source") or "Unknown") for report in reports)
    updates = [
        {
            "log_date": log["log_date"],
            "summary": log["summary"],
            "news_ids": [item["news_id"] for item in log["news"]],
        }
        for log in reversed(log_views)
    ]
    coverage_stats = {
        "news": len(news_views),
        "reports": len(reports),
        "logs": len(log_views),
        "official_sources": len(source_counts),
        "sources": dict(sorted(source_counts.items())),
        "date_range": [min(coverage_dates), max(coverage_dates)] if coverage_dates else [],
    }
    slug = str(incident.get("slug") or "")
    detail = {
        "schema": 2,
        "id": incident_id,
        "slug": slug,
        "url": f"/i/{incident_id}/{slug}/",
        "coverage_url": f"/i/{incident_id}/{slug}/coverage/",
        "display_title": str(incident.get("display_name") or ""),
        "incident_type": str(incident.get("incident_type") or "Unknown"),
        "country_code": str(incident.get("country_code") or ""),
        "country_name": country_label(str(incident.get("country_code") or "")),
        "region": region_for_country(str(incident.get("country_code") or "")),
        "status": status,
        "first_seen": str(incident.get("first_seen") or ""),
        "event_date": str(incident.get("event_date") or ""),
        "last_verified_update": last_verified,
        "latest_coverage_at": latest_coverage,
        "summary": str(incident.get("summary") or ""),
        "summary_updated": str(incident.get("summary_updated") or ""),
        "coverage_stats": coverage_stats,
        "key_links": key_links,
        "official_links": official_links,
        "updates": updates,
        "facts": physical,
        "related": [],
        "correction_url": str(config.get("site", {}).get("repo", ""))
        and f"https://github.com/{config['site']['repo']}/issues/new?template=correction.yml&incident={incident_id}",
    }
    detail["summary_short"] = _short_summary(detail["summary"])
    return detail


def _short_summary(value: str, limit: int = 220) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _add_related(details: dict[str, dict[str, Any]]) -> None:
    incidents = list(details.values())
    for detail in incidents:
        candidates: list[tuple[str, dict[str, Any]]] = []
        for other in incidents:
            if other["id"] == detail["id"]:
                continue
            reason = ""
            if detail["country_code"] and other["country_code"] == detail["country_code"]:
                reason = "Same country"
            elif detail["incident_type"] and other["incident_type"] == detail["incident_type"]:
                reason = "Same type"
            if reason:
                candidates.append((reason, other))
        candidates.sort(
            key=lambda pair: (pair[1].get("last_verified_update") or "", pair[1].get("event_date") or ""),
            reverse=True,
        )
        detail["related"] = [
            {
                "id": other["id"],
                "title": other["display_title"],
                "url": other["url"],
                "reason": reason,
                "last_verified_update": other["last_verified_update"],
            }
            for reason, other in candidates[:3]
        ]


def build(
    data_root: str | Path,
    out_root: str | Path,
    *,
    config_path: str | Path = "config.toml",
    as_of: str = "",
    clean: bool = True,
) -> dict[str, Any]:
    data_root = Path(data_root)
    out_root = Path(out_root)
    config = load_config(Path(config_path))
    store = Store(data_root)
    resolved_as_of = as_of or str(config.get("data", {}).get("as_of_fallback") or "") or store.as_of()
    generated_at = utc_now_iso()
    details: dict[str, dict[str, Any]] = {}
    for incident_id in sorted(store.incidents):
        details[incident_id] = _build_incident(store, incident_id, as_of=resolved_as_of, config=config)
    _add_related(details)

    summaries = sorted(
        details.values(),
        key=lambda item: (
            item.get("last_verified_update") or "",
            item.get("coverage_stats", {}).get("official_sources", 0),
            item.get("event_date") or "",
        ),
        reverse=True,
    )
    index = {
        "schema": 2,
        "generated_at": generated_at,
        "as_of": resolved_as_of,
        "counts": {
            "incidents": len(summaries),
            "active": sum(1 for item in summaries if item["status"] == "active"),
            "countries": len({item["country_code"] for item in summaries if item["country_code"]}),
        },
        "incidents": [
            {
                "id": item["id"],
                "slug": item["slug"],
                "url": item["url"],
                "display_title": item["display_title"],
                "incident_type": item["incident_type"],
                "country_code": item["country_code"],
                "country_name": item["country_name"],
                "region": item["region"],
                "status": item["status"],
                "event_date": item["event_date"],
                "last_verified_update": item["last_verified_update"],
                "latest_coverage_at": item["latest_coverage_at"],
                "summary_short": item["summary_short"],
                "official_count": item["coverage_stats"]["official_sources"],
                "news_count": item["coverage_stats"]["news"],
            }
            for item in summaries
        ],
    }
    status = {
        "schema": 2,
        "generated_at": generated_at,
        "as_of": resolved_as_of,
        "last_successful_ingest_at": store.last_successful_ingest_at(),
        "build_version": __version__,
        "data_sha": os.environ.get("DATA_SHA", ""),
        "counts": store.dump_index(),
        "sources": store.source_status(),
    }

    if clean and out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    write_json(out_root / "data" / "manifest.json", {
        "schema": 2,
        "generated_at": generated_at,
        "as_of": resolved_as_of,
        "counts": index["counts"],
        "build_version": __version__,
        "data_sha": status["data_sha"],
    })
    write_json(out_root / "data" / "index.json", index)
    write_json(out_root / "data" / "search-index.json", [
        {
            "id": item["id"],
            "title": item["display_title"],
            "type": item["incident_type"],
            "country": item["country_name"],
            "region": item["region"],
            "status": item["status"],
            "url": item["url"],
        }
        for item in summaries
    ])
    write_json(out_root / "data" / "status.json", status)
    write_json(out_root / "data" / "redirects.json", {})

    day_entries: dict[str, list[dict[str, Any]]] = {}
    for item in summaries:
        for update in item["updates"]:
            day = str(update.get("log_date") or "")
            if not day:
                continue
            day_entries.setdefault(day, []).append(
                {
                    "id": item["id"],
                    "title": item["display_title"],
                    "type": item["incident_type"],
                    "country": item["country_name"],
                    "status": item["status"],
                    "summary": _short_summary(str(update.get("summary") or "")),
                    "url": item["url"],
                }
            )
    for day, entries in sorted(day_entries.items()):
        write_json(out_root / "data" / "days" / f"{day}.json", {
            "schema": 2,
            "date": day,
            "incidents": entries,
        })

    for item in summaries:
        incident_id = item["id"]
        detail = dict(item)
        detail.pop("summary_short", None)
        write_json(out_root / "data" / "incidents" / f"{incident_id}.json", detail)
        articles = _coverage_articles(store, incident_id, store.logs_for_incident(incident_id))
        write_json(out_root / "data" / "coverage" / f"{incident_id}.json", {
            "schema": 2,
            "incident_id": incident_id,
            "articles": articles,
            "official": item["official_links"],
            "clusters": _clusters(articles),
            "stats": item["coverage_stats"],
        })

    assets_src = Path(__file__).resolve().parents[2] / "web" / "assets"
    if assets_src.is_dir():
        shutil.copytree(assets_src, out_root / "assets", dirs_exist_ok=True)

    render_site(
        out_root,
        config=config,
        index=index,
        details=details,
        summaries=summaries,
        status=status,
    )

    return {
        "out_root": str(out_root),
        "as_of": resolved_as_of,
        "generated_at": generated_at,
        "counts": index["counts"],
        "incidents": len(summaries),
        "data": store.dump_index(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the static v2 site")
    parser.add_argument("--data", default="data", type=Path)
    parser.add_argument("--out", default="site", type=Path)
    parser.add_argument("--config", default="config.toml", type=Path)
    parser.add_argument("--as-of", default="")
    parser.add_argument("--no-clean", action="store_true")
    args = parser.parse_args()
    result = build(
        args.data,
        args.out,
        config_path=args.config,
        as_of=args.as_of,
        clean=not args.no_clean,
    )
    print(
        f"built {result['incidents']} incidents as of {result['as_of']} "
        f"from {result['data']['news']} news / {result['data']['reports']} reports"
    )


if __name__ == "__main__":
    main()
