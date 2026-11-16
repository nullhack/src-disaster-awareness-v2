from __future__ import annotations

from typing import Any

from disaster_v2.ai.base import SubmissionClassification
from disaster_v2.models import FetchedArticle
from disaster_v2.records import RecordWriter
from disaster_v2.submissions import _extract_url, _parse_submission_hints, _process_issue


def test_extracts_first_http_url() -> None:
    body = "### Article URL\n\nhttps://example.com/news?id=1\n\n### Notes\n\nSee https://example.com/other"
    assert _extract_url(body) == "https://example.com/news?id=1"


def test_parses_optional_form_hints() -> None:
    body = """### Article URL

https://example.com/news

### Category hint (optional)

🔥 Wildfire

### Country (optional)

🇸🇬 SG - Singapore
"""
    hints = _parse_submission_hints(body)
    assert hints.incident_type == "Wildfire"
    assert hints.country_code == "SG"


def test_ignores_empty_and_ai_category_hints() -> None:
    body = """### Article URL

https://example.com/news

### Category hint (optional)

Let the AI classify

### Country (optional)

None
"""
    hints = _parse_submission_hints(body)
    assert hints.incident_type == ""
    assert hints.country_code == ""


def test_not_disaster_submission_goes_to_review_without_closing(tmp_path, monkeypatch) -> None:
    reviewed: list[tuple[int, str]] = []
    monkeypatch.setattr("disaster_v2.submissions._review", lambda number, reason: reviewed.append((number, reason)))
    monkeypatch.setattr(
        "disaster_v2.submissions.fetch_article",
        lambda _url: FetchedArticle(
            url="https://example.com/news",
            title="A headline",
            description="A description",
            body="",
            published_date="2026-11-16",
            author="Reporter",
            sitename="Example",
        ),
    )

    class FakeDigester:
        def classify_submission(self, **kwargs: Any) -> SubmissionClassification:
            return SubmissionClassification(
                is_disaster=False,
                incident_type="",
                country_code="",
                country_name="",
                summary="",
                event_date="",
            )

    issue = {
        "number": 7,
        "title": "A submission",
        "body": "### Article URL\n\nhttps://example.com/news",
        "author": {"login": "reporter"},
        "createdAt": "2026-11-16T00:00:00Z",
        "url": "https://github.com/nullhack/src-disaster-awareness-v2/issues/7",
    }
    writer = RecordWriter(tmp_path, now="2026-11-16T12:00:00Z")
    outcome = _process_issue(issue, writer, FakeDigester(), set())
    assert outcome == "review:not-disaster"
    assert reviewed and reviewed[0][0] == 7
    assert writer.counts() == {"incidents": 0, "reports": 0, "news": 0, "logs": 0}
