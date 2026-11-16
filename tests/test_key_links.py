from __future__ import annotations

from disaster_v2.key_links import source_links


def test_manual_reports_are_not_official_links() -> None:
    reports = [
        {
            "source": "MANUAL",
            "source_id": "abc",
            "name": "Submitted article",
            "report_date": "2026-11-16",
            "raw_fields": {"url": "https://news.example/story"},
        },
        {
            "source": "USGS",
            "source_id": "us1",
            "name": "M5.6 - Nepal",
            "report_date": "2026-11-15",
            "raw_fields": {"url": "https://earthquake.usgs.gov/eventpage/us1"},
        },
    ]
    links = source_links(reports)
    assert [link["type"] for link in links] == ["USGS"]
