from __future__ import annotations

import argparse
import shutil
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .countries import country_label, region_for_country
from .util import (
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
    parse_date,
    slugify,
    utc_now_iso,
    write_json,
    write_jsonl,
)

ACTIVE_DAYS = 30
DORMANT_DAYS = 180
_AGGREGATOR_DOMAINS = (
    "msn.com",
    "yahoo.com",
    "aol.com",
    "flipboard.com",
    "news.google.com",
)


@dataclass
class SourceTree:
    incidents: dict[str, dict[str, Any]] = field(default_factory=dict)
    reports: dict[str, dict[str, Any]] = field(default_factory=dict)
    report_incident: dict[str, str | None] = field(default_factory=dict)
    news: dict[str, dict[str, Any]] = field(default_factory=dict)
    news_incidents: dict[str, set[str]] = field(default_factory=dict)
    news_logs: dict[str, set[tuple[str, str]]] = field(default_factory=dict)
    logs: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _relative_parts(path: Path, root: Path) -> tuple[str, ...]:
    try:
        return path.relative_to(root).parts
    except ValueError:
        return path.parts


def _record_report(
    tree: SourceTree, data: dict[str, Any], incident_id: str | None, path: Path
) -> None:
    report_id = str(data.get("id") or path.stem)
    if not report_id:
        tree.warnings.append(f"report without id: {path}")
        return
    if report_id in tree.reports:
        previous = tree.reports[report_id]
        if previous != data:
            tree.warnings.append(f"duplicate report id with different content: {report_id}")
        return
    tree.reports[report_id] = data
    tree.report_incident[report_id] = incident_id


def _record_news(
    tree: SourceTree,
    data: dict[str, Any],
    incident_id: str | None,
    log_date: str | None,
    path: Path,
) -> None:
    news_id = str(data.get("id") or path.stem)
    if not news_id:
        tree.warnings.append(f"news without id: {path}")
        return
    if news_id in tree.news:
        previous = tree.news[news_id]
        if previous != data:
            tree.warnings.append(f"duplicate news id with different content: {news_id}")
    else:
        tree.news[news_id] = data
    if incident_id:
        tree.news_incidents.setdefault(news_id, set()).add(incident_id)
    if incident_id and log_date:
        tree.news_logs.setdefault(news_id, set()).add((incident_id, log_date))


def _scan_tree(source_root: Path) -> SourceTree:
    tree = SourceTree()

    for path in iter_yaml(source_root / "reports", recursive=True):
        data = load_yaml(path)
        if data.get("id"):
            _record_report(tree, data, None, path)

    for path in iter_yaml(source_root / "news", recursive=False):
        data = load_yaml(path)
        if data.get("id"):
            _record_news(tree, data, None, None, path)

    incidents_root = source_root / "incidents"
    if not incidents_root.is_dir():
        return tree

    for incident_dir in sorted(p for p in incidents_root.iterdir() if p.is_dir()):
        incident_id = incident_dir.name
        manifest_path = incident_dir / "incident.yaml"
        manifest = load_yaml(manifest_path) if manifest_path.is_file() else {"id": incident_id}
        manifest["id"] = str(manifest.get("id") or incident_id)
        tree.incidents[incident_id] = manifest

        for path in iter_yaml(incident_dir / "reports", recursive=True):
            data = load_yaml(path)
            if data.get("id"):
                _record_report(tree, data, incident_id, path)

        for path in iter_yaml(incident_dir / "news", recursive=False):
            data = load_yaml(path)
            if data.get("id"):
                _record_news(tree, data, incident_id, None, path)

        logs_root = incident_dir / "logs"
        if not logs_root.is_dir():
            continue
        for log_dir in sorted(p for p in logs_root.iterdir() if p.is_dir()):
            log_date = log_dir.name
            log_path = log_dir / "log.yaml"
            log_data = load_yaml(log_path) if log_path.is_file() else {"log_date": log_date}
            news_ids: list[str] = []
            for path in iter_yaml(log_dir / "news", recursive=False):
                data = load_yaml(path)
                news_id = str(data.get("id") or path.stem)
                if news_id:
                    news_ids.append(news_id)
                _record_news(tree, data, incident_id, log_date, path)
            tree.logs.setdefault(incident_id, {})[log_date] = {
                "log_date": log_date,
                "summary": str(log_data.get("summary") or ""),
                "news_ids": sorted(set(news_ids)),
            }

    return tree


def _is_aggregator(url: str) -> bool:
    from urllib.parse import urlparse

    host = urlparse(url).netloc.lower()
    return any(host == domain or host.endswith("." + domain) for domain in _AGGREGATOR_DOMAINS)


def _report_payload(report_id: str, data: dict[str, Any]) -> dict[str, Any]:
    payload = dict(data)
    payload.setdefault("id", report_id)
    payload["schema"] = 2
    payload["content_hash"] = content_hash(
        payload.get("source"), payload.get("source_id"), payload.get("raw_fields")
    )
    return payload


def _news_payload(news_id: str, data: dict[str, Any], tree: SourceTree) -> dict[str, Any]:
    payload = dict(data)
    payload.setdefault("id", news_id)
    payload["schema"] = 2
    url = str(payload.get("url") or "")
    payload["canonical_url"] = canonical_url(url)
    payload["content_hash"] = content_hash(
        url, payload.get("title"), payload.get("body"), payload.get("published_date")
    )
    payload["is_aggregator"] = _is_aggregator(url)
    log_dates = sorted({log_date for _, log_date in tree.news_logs.get(news_id, set())})
    payload["log_dates"] = log_dates
    return payload


def _incident_payload(
    incident_id: str,
    manifest: dict[str, Any],
    tree: SourceTree,
    *,
    as_of: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    linked_reports = sorted(
        (
            tree.reports[report_id]
            for report_id, linked in tree.report_incident.items()
            if linked == incident_id
        ),
        key=lambda report: (date_str(report.get("report_date")), str(report.get("id") or "")),
    )
    linked_news_ids = sorted(
        news_id
        for news_id, incident_ids in tree.news_incidents.items()
        if incident_id in incident_ids
    )
    linked_news = [tree.news[news_id] for news_id in linked_news_ids]
    log_items = sorted(tree.logs.get(incident_id, {}).values(), key=lambda item: item["log_date"])

    genesis = linked_reports[0] if linked_reports else None
    genesis_id = str(genesis.get("id") or "") if genesis else ""
    genesis_source = str(genesis.get("source") or "") if genesis else ""
    genesis_source_id = str(genesis.get("source_id") or "") if genesis else ""

    report_dates = [date_str(report.get("report_date")) for report in linked_reports]
    news_dates = [date_str(news.get("published_date")) for news in linked_news]
    log_dates = [item["log_date"] for item in log_items]

    first_seen = min_date(*report_dates, *log_dates, *news_dates) or date_str(manifest.get("first_seen"))
    event_date = date_str(genesis.get("report_date")) if genesis else first_seen
    last_verified = max_date(*log_dates) or first_seen
    latest_coverage = max_date(*news_dates, *log_dates, *report_dates) or last_verified

    places: list[dict[str, Any]] = []
    for report in linked_reports:
        for place in as_list(report.get("places")):
            if isinstance(place, dict):
                places.append(place)
    country_code = ""
    for place in places:
        code = str(place.get("country_code") or "").upper()
        if len(code) == 2:
            country_code = code
            break

    display_name = (
        str(genesis.get("name") or "").strip()
        if genesis
        else ""
    )
    if not display_name and linked_news:
        display_name = str(linked_news[0].get("title") or "").strip()
    display_name = display_name or f"Incident {incident_id[:8]}"

    incident_type = str(genesis.get("incident_type") or "").strip() if genesis else ""
    incident_type = incident_type or "Unknown"

    latest_log = log_items[-1] if log_items else None
    summary = str(latest_log.get("summary") or "") if latest_log else ""
    summary_updated = str(latest_log.get("log_date") or "") if latest_log else ""

    as_of_date = parse_date(as_of) or date.today()
    verified_date = parse_date(last_verified)
    if verified_date is None:
        status = "archived"
    elif verified_date >= as_of_date - timedelta(days=ACTIVE_DAYS):
        status = "active"
    elif verified_date >= as_of_date - timedelta(days=DORMANT_DAYS):
        status = "dormant"
    else:
        status = "archived"

    external_keys = [
        {
            "source": str(report.get("source") or ""),
            "source_id": str(report.get("source_id") or ""),
            "report_id": str(report.get("id") or ""),
        }
        for report in linked_reports
    ]

    payload: dict[str, Any] = {
        "schema": 2,
        "id": incident_id,
        "status": status,
        "display_name": display_name,
        "slug": slugify(display_name),
        "incident_type": incident_type,
        "country_code": country_code,
        "country_name": country_label(country_code),
        "region": region_for_country(country_code),
        "first_seen": first_seen,
        "event_date": event_date,
        "last_verified_update": last_verified,
        "latest_coverage_at": latest_coverage,
        "status_changed_at": last_verified or first_seen,
        "summary": summary,
        "summary_updated": summary_updated,
        "search_keys": [str(key) for key in as_list(manifest.get("search_keys"))],
        "extended_monitoring": bool(manifest.get("extended_monitoring", False)),
        "aliases": [str(alias) for alias in as_list(manifest.get("aliases"))],
        "merged_into": manifest.get("merged_into"),
        "genesis": {
            "report_id": genesis_id,
            "source": genesis_source,
            "source_id": genesis_source_id,
        }
        if genesis
        else None,
        "external_keys": external_keys,
        "coverage_counts": {
            "reports": len(linked_reports),
            "news": len(linked_news),
            "logs": len(log_items),
            "official_sources": len({str(report.get("source") or "") for report in linked_reports}),
        },
        "created_at": f"{first_seen}T00:00:00Z" if first_seen else "",
        "migrated_at": utc_now_iso(),
    }

    links: list[dict[str, Any]] = []
    for report in linked_reports:
        report_id = str(report.get("id") or "")
        links.append(
            {
                "from": f"incident:{incident_id}",
                "to": f"report:{report_id}",
                "kind": "incident_report",
                "role": "primary" if report_id == genesis_id else "supporting",
                "method": "migration",
                "at": as_of,
            }
        )
    for news in linked_news:
        news_id = str(news.get("id") or "")
        links.append(
            {
                "from": f"incident:{incident_id}",
                "to": f"news:{news_id}",
                "kind": "incident_news",
                "method": "migration",
                "at": as_of,
            }
        )
    for news_id, log_links in tree.news_logs.items():
        for linked_incident_id, log_date in sorted(log_links):
            if linked_incident_id != incident_id:
                continue
            links.append(
                {
                    "from": f"news:{news_id}",
                    "to": f"log:{incident_id}:{log_date}",
                    "kind": "news_log",
                    "method": "migration",
                    "at": as_of,
                }
            )

    logs = [
        {
            "schema": 2,
            "incident_id": incident_id,
            "log_date": item["log_date"],
            "summary": item["summary"],
            "news_ids": item["news_ids"],
        }
        for item in log_items
    ]
    return payload, logs, links


def migrate(
    source_root: Path,
    target_root: Path,
    *,
    as_of: str = "",
    force: bool = False,
) -> dict[str, Any]:
    source_root = Path(source_root)
    target_root = Path(target_root)
    if not source_root.is_dir():
        raise SystemExit(f"source tree not found: {source_root}")

    records_root = target_root / "records"
    links_root = target_root / "links"
    events_root = target_root / "events"
    if records_root.exists() or links_root.exists():
        if not force:
            raise SystemExit(
                f"{target_root} already has records/links; pass --force to replace them"
            )
        shutil.rmtree(records_root)
        shutil.rmtree(links_root, ignore_errors=True)
        shutil.rmtree(events_root, ignore_errors=True)

    tree = _scan_tree(source_root)
    all_dates = [
        date_str(report.get("report_date")) for report in tree.reports.values()
    ] + [
        date_str(news.get("published_date")) for news in tree.news.values()
    ]
    for logs in tree.logs.values():
        all_dates.extend(logs.keys())
    resolved_as_of = as_of or max_date(*all_dates) or "1970-01-01"

    report_payloads = {
        report_id: _report_payload(report_id, report)
        for report_id, report in sorted(tree.reports.items())
    }
    news_payloads = {
        news_id: _news_payload(news_id, news, tree)
        for news_id, news in sorted(tree.news.items())
    }

    incident_payloads: dict[str, dict[str, Any]] = {}
    log_payloads: dict[str, list[dict[str, Any]]] = {}
    all_links: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for incident_id, manifest in sorted(tree.incidents.items()):
        payload, logs, links = _incident_payload(incident_id, manifest, tree, as_of=resolved_as_of)
        incident_payloads[incident_id] = payload
        log_payloads[incident_id] = logs
        all_links.extend(links)
        events.append(
            {
                "type": "incident_migrated",
                "at": resolved_as_of,
                "incident_id": incident_id,
                "counts": payload["coverage_counts"],
            }
        )

    for report_id, payload in report_payloads.items():
        dump_yaml(records_root / "reports" / f"{report_id}.yaml", payload)
    for news_id, payload in news_payloads.items():
        dump_yaml(records_root / "news" / f"{news_id}.yaml", payload)
    for incident_id, payload in incident_payloads.items():
        dump_yaml(records_root / "incidents" / f"{incident_id}.yaml", payload)
    for incident_id, logs in log_payloads.items():
        for log in logs:
            dump_yaml(
                records_root / "logs" / incident_id / f"{log['log_date']}.yaml",
                log,
            )

    write_jsonl(links_root / f"{resolved_as_of[:7]}.jsonl", all_links)
    write_jsonl(events_root / f"{resolved_as_of[:7]}.jsonl", events)

    counts = {
        "incidents": len(incident_payloads),
        "reports": len(report_payloads),
        "news": len(news_payloads),
        "logs": sum(len(logs) for logs in log_payloads.values()),
        "links": len(all_links),
        "staging_reports": sum(1 for value in tree.report_incident.values() if value is None),
        "staging_news": sum(1 for news_id in tree.news if news_id not in tree.news_incidents),
    }
    report = {
        "schema": 2,
        "as_of": resolved_as_of,
        "migrated_at": utc_now_iso(),
        "counts": counts,
        "warnings": tree.warnings[:200],
        "warning_count": len(tree.warnings),
    }
    write_json(target_root / "migration-report.json", report, pretty=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate a v1 content tree to v2 records")
    parser.add_argument("--source", required=True, type=Path, help="v1 data tree (read-only)")
    parser.add_argument("--target", required=True, type=Path, help="v2 data branch worktree")
    parser.add_argument("--as-of", default="", help="override as-of date (YYYY-MM-DD)")
    parser.add_argument("--force", action="store_true", help="replace existing v2 records")
    args = parser.parse_args()
    report = migrate(args.source, args.target, as_of=args.as_of, force=args.force)
    print(
        "migrated "
        f"{report['counts']['incidents']} incidents, "
        f"{report['counts']['news']} news, "
        f"{report['counts']['reports']} reports, "
        f"{report['counts']['logs']} logs; "
        f"{report['warning_count']} warnings"
    )


if __name__ == "__main__":
    main()
