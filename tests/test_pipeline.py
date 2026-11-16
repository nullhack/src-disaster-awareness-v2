from __future__ import annotations

from typing import Any

import pytest

from disaster_v2.ai.base import FilterResult, SummaryResult
from disaster_v2.models import NewsItem, ReportPlace, SourceReport
from disaster_v2.pipeline import PipelineSettings, generate_logs, ingest_source_reports, search_news
from disaster_v2.records import RecordWriter


@pytest.fixture()
def writer(tmp_path):
    return RecordWriter(tmp_path, now="2026-11-16T12:00:00Z")


def _report() -> SourceReport:
    return SourceReport(
        source="USGS",
        source_id="us-test-1",
        incident_type="Earthquake",
        name="M5.6 - Nepal",
        places=[ReportPlace(country_code="NP", subdivision="", locality="Nepal")],
        report_date="2026-11-15",
        raw_fields={"url": "https://earthquake.usgs.gov/eventpage/us-test-1", "mag": 5.6},
    )


def _news() -> NewsItem:
    return NewsItem(
        url="https://news.example/nepal-quake",
        title="Nepal earthquake damages homes",
        body="A magnitude 5.6 earthquake struck Nepal.",
        published_date="2026-11-16T08:00:00+00:00",
        source="Example News",
        domain="news.example",
        image="",
    )


def test_writer_persists_report_news_and_log(writer):
    report = _report()
    report_id = writer.ingest_report(report)
    incident_id = "incident-1"
    writer.ensure_incident(incident_id, report)
    writer.link_report_to_incident(report_id, incident_id)
    news_id = writer.ingest_news(_news())
    writer.link_news_to_incident(news_id, incident_id)
    writer.append_log(incident_id, "2026-11-16", "Homes damaged in Nepal.", {news_id})
    writer.flush()

    reloaded = RecordWriter(writer.root, now="2026-11-16T12:00:00Z")
    assert reloaded.report_ids_for_incident(incident_id) == [report_id]
    assert reloaded.news_ids_for_incident(incident_id) == [news_id]
    assert reloaded.logs_for_incident(incident_id)[0]["summary"] == "Homes damaged in Nepal."
    incident = reloaded.incidents[incident_id]
    assert incident["display_name"] == "M5.6 - Nepal"
    assert incident["coverage_counts"] == {"reports": 1, "news": 1, "logs": 1, "official_sources": 1}
    assert incident["last_verified_update"] == "2026-11-16"
    assert incident["summary"] == "Homes damaged in Nepal."


def test_pipeline_ingests_searches_and_logs(tmp_path, monkeypatch):
    monkeypatch.setattr("disaster_v2.pipeline.fetch_article", lambda _url: None)
    settings = PipelineSettings(model="test/model", min_log_news_threshold=1)
    store = RecordWriter(tmp_path, now="2026-11-16T12:00:00Z")

    class FakeAdapter:
        source = "USGS"

        def fetch(self) -> list[SourceReport]:
            return [_report()]

        def should_monitor(self, _report: SourceReport) -> bool:
            return True

        def derive_keys(self, _report: SourceReport) -> tuple[str, str]:
            return ("Nepal Earthquake November 2026", "")

        def derive_repoll_keys(self, _report: SourceReport) -> list[str]:
            return ["Nepal Earthquake latest 2026"]

    class FakeDDG:
        def search(self, query: str, timelimit: str | None = None) -> list[NewsItem]:
            return [_news()]

    class FakeDigester:
        def filter(self, candidates: list[NewsItem], **kwargs: Any) -> FilterResult:
            return FilterResult(selected_news=list(candidates), relevance_scores={})

        def summarize(self, selected_news: list[NewsItem], prior: list[Any], **kwargs: Any) -> SummaryResult:
            return SummaryResult(summary="Homes were damaged.", has_relevant_updates=True)

    adapter = FakeAdapter()
    assert ingest_source_reports([adapter], store) == 1
    search_news(store, [adapter], FakeDDG(), FakeDigester(), settings)
    generate_logs(store, FakeDigester(), settings)
    store.flush()

    assert store.counts() == {"incidents": 1, "reports": 1, "news": 1, "logs": 1}
    incident_id = next(iter(store.incidents))
    assert store.logs_for_incident(incident_id)[0]["log_date"] == "2026-11-16"
    assert store.incidents[incident_id]["search_keys"] == ["Nepal Earthquake latest 2026"]
