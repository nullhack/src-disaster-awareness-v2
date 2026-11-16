from __future__ import annotations

import json
from pathlib import Path

import yaml

from disaster_v2.build import build
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
        "url": "https://news.example.test/story",
        "title": "Peru earthquake felt in Lima",
        "body": "A magnitude 6.1 earthquake struck Peru.",
        "published_date": "2026-01-03",
        "source": "Example News",
        "domain": "news.example.test",
    }
    _write_yaml(source / "incidents" / "i1" / "incident.yaml", {"id": "i1", "search_keys": ["Peru earthquake"]})
    _write_yaml(source / "incidents" / "i1" / "reports" / "source=USGS" / "r1.yaml", report)
    _write_yaml(source / "incidents" / "i1" / "logs" / "2026-01-03" / "log.yaml", {"log_date": "2026-01-03", "summary": "Aftershocks continue."})
    _write_yaml(source / "incidents" / "i1" / "logs" / "2026-01-03" / "news" / "n1.yaml", news)
    return source


def test_build_writes_slim_index_and_incident_detail(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    data = tmp_path / "data"
    migrate(source, data, as_of="2026-01-10")
    site = tmp_path / "site"
    result = build(data, site, config_path=Path("config.toml"), as_of="2026-01-10")

    assert result["incidents"] == 1
    index = json.loads((site / "data" / "index.json").read_text())
    assert index["schema"] == 2
    assert index["incidents"][0]["id"] == "i1"
    assert index["incidents"][0]["url"].startswith("/i/i1/")
    detail = json.loads((site / "data" / "incidents" / "i1.json").read_text())
    assert detail["coverage_stats"]["news"] == 1
    assert detail["key_links"][0]["tier"] == "official"
    assert detail["key_links"][0]["url"] == "https://example.test/event"
    coverage = json.loads((site / "data" / "coverage" / "i1.json").read_text())
    assert coverage["articles"][0]["news_id"] == "n1"
    trends = json.loads((site / "data" / "trends.json").read_text())
    assert trends["active_count"] == 1
    assert trends["new_last_30_days"] == 1
    assert trends["updated_last_30_days"] == 1
    assert trends["active_by_type"][0] == {"label": "Earthquake", "count": 1}
    assert trends["months"][-1]["key"] == "2026-01"
    html = (site / "i" / "i1" / detail["slug"] / "index.html").read_text()
    assert "Read the sources" in html
    assert "Last verified update" in html
    assert (site / "assets" / "app.js").is_file()
    assert (site / "feed.xml").read_text().startswith("<?xml")
    assert "Newly tracked incidents" in (site / "trends" / "index.html").read_text()
    assert "Situation at a glance" in (site / "index.html").read_text()
