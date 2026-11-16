from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .util import as_list, iter_yaml, load_yaml, max_date, read_jsonl


class Store:
    """Read-only view of the v2 canonical records on the data branch."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.incidents: dict[str, dict[str, Any]] = {}
        self.reports: dict[str, dict[str, Any]] = {}
        self.news: dict[str, dict[str, Any]] = {}
        self.logs: dict[str, dict[str, dict[str, Any]]] = {}
        self.links: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self._load()

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

        for path in iter_yaml(self.root / "records" / "news", recursive=False):
            data = load_yaml(path)
            news_id = str(data.get("id") or path.stem)
            data["id"] = news_id
            self.news[news_id] = data

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

    def report_ids_for_incident(self, incident_id: str) -> list[str]:
        ids = [
            link["to"].split(":", 1)[1]
            for link in self.links
            if link.get("kind") == "incident_report"
            and link.get("from") == f"incident:{incident_id}"
            and link.get("to", "").startswith("report:")
        ]
        return sorted(set(ids))

    def news_ids_for_incident(self, incident_id: str) -> list[str]:
        ids = [
            link["to"].split(":", 1)[1]
            for link in self.links
            if link.get("kind") == "incident_news"
            and link.get("from") == f"incident:{incident_id}"
            and link.get("to", "").startswith("news:")
        ]
        return sorted(set(ids))

    def news_ids_for_log(self, incident_id: str, log_date: str) -> list[str]:
        log = self.logs.get(incident_id, {}).get(log_date)
        if log is not None:
            return [str(news_id) for news_id in as_list(log.get("news_ids"))]
        ids = [
            link["from"].split(":", 1)[1]
            for link in self.links
            if link.get("kind") == "news_log"
            and link.get("to") == f"log:{incident_id}:{log_date}"
            and link.get("from", "").startswith("news:")
        ]
        return sorted(set(ids))

    def reports_for_incident(self, incident_id: str) -> list[dict[str, Any]]:
        reports = [self.reports.get(report_id, {}) for report_id in self.report_ids_for_incident(incident_id)]
        return [report for report in reports if report]

    def news_for_incident(self, incident_id: str) -> list[dict[str, Any]]:
        items = [self.news.get(news_id, {}) for news_id in self.news_ids_for_incident(incident_id)]
        return [item for item in items if item]

    def logs_for_incident(self, incident_id: str) -> list[dict[str, Any]]:
        logs = list(self.logs.get(incident_id, {}).values())
        logs.sort(key=lambda item: str(item.get("log_date") or ""))
        return logs

    def as_of(self) -> str:
        dates: list[object] = []
        for incident in self.incidents.values():
            dates.extend(
                [
                    incident.get("latest_coverage_at"),
                    incident.get("last_verified_update"),
                    incident.get("event_date"),
                ]
            )
        for report in self.reports.values():
            dates.append(report.get("report_date"))
        for news in self.news.values():
            dates.append(news.get("published_date"))
        for logs in self.logs.values():
            dates.extend(logs.keys())
        return max_date(*dates) or "1970-01-01"

    def last_successful_ingest_at(self) -> str:
        values = [
            str(report.get("news_searched_at") or "")
            for report in self.reports.values()
        ]
        values.extend(
            str(news.get("published_date") or "") for news in self.news.values()
        )
        timestamps = sorted(value for value in values if value)
        return timestamps[-1] if timestamps else ""

    def source_status(self) -> list[dict[str, Any]]:
        sources: dict[str, dict[str, Any]] = {}
        for report in self.reports.values():
            source = str(report.get("source") or "Unknown")
            item = sources.setdefault(source, {"source": source, "reports": 0, "latest": ""})
            item["reports"] += 1
            item["latest"] = max(
                str(item["latest"] or ""), str(report.get("report_date") or "")
            )
        return sorted(sources.values(), key=lambda item: item["source"])

    def dump_index(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of(),
            "incidents": len(self.incidents),
            "reports": len(self.reports),
            "news": len(self.news),
            "logs": sum(len(logs) for logs in self.logs.values()),
            "links": len(self.links),
        }


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else {}
