from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from disaster_v2.migrate import migrate


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _fixture(root: Path) -> Path:
    source = root / "v1"
    report = {
        "id": "r1",
        "source": "USGS",
        "source_id": "us1",
        "incident_type": "Earthquake",
        "name": "M 6.1 Example",
        "report_date": "2026-01-02",
        "places": [{"country_code": "PE", "name": "Peru"}],
        "raw_fields": {"url": "https://example.test/event", "mag": 6.1, "place": "Peru"},
    }
    news = {
        "id": "n1",
        "url": "https://news.example.test/story?utm_source=x",
        "title": "Peru earthquake felt in Lima",
        "body": "A magnitude 6.1 earthquake struck Peru.",
        "published_date": "2026-01-03",
        "source": "Example News",
        "domain": "news.example.test",
    }
    _write_yaml(source / "reports" / "source=USGS" / "r1.yaml", report)
    _write_yaml(source / "incidents" / "i1" / "incident.yaml", {"id": "i1", "search_keys": ["Peru earthquake"]})
    _write_yaml(source / "incidents" / "i1" / "reports" / "source=USGS" / "r1.yaml", report)
    _write_yaml(source / "incidents" / "i1" / "logs" / "2026-01-03" / "log.yaml", {"log_date": "2026-01-03", "summary": "Aftershocks continue."})
    _write_yaml(source / "incidents" / "i1" / "logs" / "2026-01-03" / "news" / "n1.yaml", news)
    return source


def test_migrate_writes_records_and_keeps_source_untouched(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source.rglob("*.yaml")}
    target = tmp_path / "v2"
    report = migrate(source, target, as_of="2026-01-10")
    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source.rglob("*.yaml")}

    assert before == after
    assert report["counts"]["incidents"] == 1
    assert report["counts"]["reports"] == 1
    assert report["counts"]["news"] == 1
    incident = yaml.safe_load((target / "records" / "incidents" / "i1.yaml").read_text())
    assert incident["schema"] == 2
    assert incident["genesis"]["report_id"] == "r1"
    assert incident["country_code"] == "PE"
    assert incident["status"] == "active"
    news = yaml.safe_load((target / "records" / "news" / "n1.yaml").read_text())
    assert news["canonical_url"] == "https://news.example.test/story"
    assert news["content_hash"]
    links = [json.loads(line) for line in (target / "links" / "2026-01.jsonl").read_text().splitlines()]
    assert {link["kind"] for link in links} == {"incident_report", "incident_news", "news_log"}
