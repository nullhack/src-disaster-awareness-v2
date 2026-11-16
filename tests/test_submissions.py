from __future__ import annotations

from disaster_v2.submissions import _extract_url, _parse_submission_hints


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
