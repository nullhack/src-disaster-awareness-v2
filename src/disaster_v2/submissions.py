"""Process GitHub news-submission issues into v2 incidents.

The bot imports clear, classified submissions automatically. Uncertain
submissions stay open under ``submission-review`` for a maintainer to approve
or decline. Only malformed submissions with no URL are auto-closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

from .ai.openrouter import OpenRouterDigester
from .fetchers import fetch_article
from .gh_issues import add_label, close, comment, remove_label
from .models import NewsItem, ReportPlace, SourceReport
from .pipeline import load_settings
from .records import RecordWriter, new_incident_id

logger = logging.getLogger(__name__)

PENDING_LABEL = "submission-pending"
REVIEW_LABEL = "submission-review"
APPROVED_LABEL = "submission-approved"
IMPORTED_LABEL = "submission-imported"
REJECTED_LABEL = "submission-rejected"

_INCIDENT_TYPE_MAP: dict[str, str] = {"War": "Conflict"}
_CATEGORY_HINTS: tuple[tuple[str, str], ...] = (
    ("volcanic", "Volcanic eruption"),
    ("earthquake", "Earthquake"),
    ("tsunami", "Tsunami"),
    ("flood", "Flood"),
    ("cyclone", "Cyclone"),
    ("hurricane", "Cyclone"),
    ("typhoon", "Cyclone"),
    ("landslide", "Landslide"),
    ("mudslide", "Landslide"),
    ("wildfire", "Wildfire"),
    ("drought", "Drought"),
    ("disease", "Disease outbreak"),
    ("heat wave", "Heat wave"),
    ("severe storm", "Severe storm"),
    ("other", "Other"),
)
_MONITORING_LABELS = ("monitoring-request", "monitoring-applied", "monitoring-rejected")


@dataclass(frozen=True)
class _SubmissionHints:
    incident_type: str = ""
    country_code: str = ""


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _list_issues(label: str, exclude: tuple[str, ...]) -> list[dict]:
    out = _gh(
        [
            "issue",
            "list",
            "--state",
            "open",
            "--label",
            label,
            "--search",
            " ".join(f"-label:{item}" for item in exclude),
            "--json",
            "number,title,body,author,createdAt,url",
            "--limit",
            "50",
        ]
    )
    return json.loads(out) if out.strip() else []


def _gh(args: list[str]) -> str:
    from .gh_issues import gh

    return gh(args)


def _list_pending_issues() -> list[dict]:
    return _list_issues(
        PENDING_LABEL,
        (IMPORTED_LABEL, REJECTED_LABEL, REVIEW_LABEL, APPROVED_LABEL, *_MONITORING_LABELS),
    )


def _list_approved_issues() -> list[dict]:
    return _list_issues(APPROVED_LABEL, (IMPORTED_LABEL, REJECTED_LABEL, *_MONITORING_LABELS))


def _extract_url(body: str) -> str | None:
    tokens = body.replace("<", " ").replace(">", " ").split()
    for token in tokens:
        if token.startswith("http://") or token.startswith("https://"):
            return token.rstrip(",.;)")
    return None


def _parse_form_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    current = ""
    for line in body.splitlines():
        if line.startswith("### "):
            current = line[4:].strip().lower()
            fields[current] = ""
        elif current:
            value = line.strip()
            if value:
                fields[current] = (fields[current] + " " + value).strip()
    return fields


def _form_field(fields: dict[str, str], prefix: str) -> str:
    for key, value in fields.items():
        if key.startswith(prefix):
            return value
    return ""


def _clean_hint(value: str) -> str:
    if value.strip().lower() in {"", "none", "_no response_"}:
        return ""
    return value.strip()


def _parse_country_hint(value: str) -> str:
    match = re.search(r"\b([A-Z]{2})\b", _clean_hint(value))
    return match.group(1).upper() if match else ""


def _parse_category_hint(value: str) -> str:
    lower = _clean_hint(value).lower()
    if not lower or "let the ai classify" in lower:
        return ""
    for keyword, incident_type in _CATEGORY_HINTS:
        if keyword in lower:
            return incident_type
    return ""


def _parse_submission_hints(body: str) -> _SubmissionHints:
    fields = _parse_form_fields(body)
    return _SubmissionHints(
        incident_type=_parse_category_hint(_form_field(fields, "category hint")),
        country_code=_parse_country_hint(_form_field(fields, "country")),
    )


def _source_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]  # noqa: S324


def _existing_manual_keys(writer: RecordWriter) -> set[str]:
    return {
        str(report.get("source_id") or "")
        for report in writer.reports.values()
        if str(report.get("source") or "") == "MANUAL"
    }


def _reject(number: int, reason: str) -> None:
    for label in (PENDING_LABEL, REVIEW_LABEL, APPROVED_LABEL):
        remove_label(number, label)
    add_label(number, REJECTED_LABEL)
    close(number, f"Rejected: {reason}")


def _review(number: int, reason: str) -> None:
    remove_label(number, PENDING_LABEL)
    remove_label(number, APPROVED_LABEL)
    add_label(number, REVIEW_LABEL)
    comment(
        number,
        f"Needs maintainer review: {reason}\n\n"
        "A maintainer can add `submission-approved` to import this submission, "
        "or add `submission-rejected` and close this issue to decline it.",
    )


def _mark_imported(number: int, message: str) -> None:
    for label in (PENDING_LABEL, REVIEW_LABEL, APPROVED_LABEL):
        remove_label(number, label)
    add_label(number, IMPORTED_LABEL)
    comment(number, message)


def _url_already_tracked(number: int, writer: RecordWriter, url: str) -> str | None:
    news_id = writer.find_news_id_by_url(url)
    if not news_id:
        return None
    existing_inc = writer.incident_for_news(news_id)
    if existing_inc:
        _mark_imported(number, f"URL already tracked as news on incident `{existing_inc[:8]}`. Relabeled.")
    else:
        _mark_imported(number, "URL already in store as pending news. Relabeled.")
    return "imported:existing-news"


def _resolve_incident_type(classified: str, hint: str) -> str:
    raw = classified or hint or "Unknown"
    return _INCIDENT_TYPE_MAP.get(raw, raw)


def _process_issue(
    issue: dict,
    writer: RecordWriter,
    digester: OpenRouterDigester,
    manual_keys: set[str],
    approved: bool = False,
) -> str:
    number = int(issue["number"])
    body = str(issue.get("body") or "")
    hints = _parse_submission_hints(body)
    url = _extract_url(body)
    if not url:
        _reject(number, "no http(s) URL found in issue body")
        return "rejected:no-url"
    sid = _source_id(url)
    if sid in manual_keys:
        _mark_imported(number, f"Already tracked as `MANUAL:{sid}`. Relabeled.")
        return "imported:existing"
    url_short = _url_already_tracked(number, writer, url)
    if url_short:
        return url_short
    fetched = fetch_article(url)
    if fetched is None:
        _review(
            number,
            f"could not fetch article from {url}. "
            "A maintainer can re-add `submission-approved` after the URL is reachable.",
        )
        return "review:fetch-failed"
    classified = digester.classify_submission(url=url, title=fetched.title, body=fetched.description)
    if not classified.is_disaster and not approved:
        _review(number, "DSPy classifier says this is not a disaster event.")
        return "review:not-disaster"
    country_code = classified.country_code or hints.country_code
    if not country_code:
        _review(
            number,
            "neither the DSPy classifier nor the issue form identified the event country. "
            "Edit the Country field, then re-add `submission-approved`.",
        )
        return "review:no-country"
    incident_type = _resolve_incident_type(classified.incident_type, hints.incident_type)
    places = [ReportPlace(country_code=country_code, subdivision="", locality="")]
    report = SourceReport(
        source="MANUAL",
        source_id=sid,
        incident_type=incident_type,
        name=classified.summary or fetched.title or str(issue.get("title", ""))[:120],
        places=places,
        report_date=(classified.event_date or fetched.published_date or str(issue.get("createdAt", ""))[:10]),
        raw_fields={
            "url": url,
            "submitted_by": (
                issue.get("author", {}).get("login", "")
                if isinstance(issue.get("author"), dict)
                else str(issue.get("author", ""))
            ),
            "issue_number": number,
            "issue_url": issue.get("url", ""),
            "fetched_title": fetched.title,
            "fetched_sitename": fetched.sitename,
            "classified_at": _now_iso(),
            "approved_override": bool(approved and not classified.is_disaster),
            "form_category_hint": hints.incident_type,
            "form_country_hint": hints.country_code,
        },
    )
    report_id = writer.ingest_report(report)
    existing_inc = writer.incident_for_report(report_id)
    if existing_inc:
        incident_id = existing_inc
        outcome = "imported:existing"
    else:
        incident_id = new_incident_id()
        writer.ensure_incident(incident_id, report)
        outcome = "imported:new"
    writer.link_report_to_incident(report_id, incident_id)
    news = NewsItem(
        url=url,
        title=fetched.title,
        body=fetched.description,
        published_date=fetched.published_date or str(issue.get("createdAt", ""))[:10],
        source="manual",
        domain=urlparse(url).netloc,
        image="",
        author=fetched.author,
        sitename=fetched.sitename,
    )
    news_id = writer.ingest_news(news)
    current_inc = writer.incident_for_news(news_id)
    if current_inc is None:
        writer.link_news_to_incident(news_id, incident_id)
    elif current_inc != incident_id:
        logger.warning("issue %s: news already on %s, not stealing", number, current_inc[:8])
    _mark_imported(
        number,
        f"Tracking as incident `{incident_id[:8]}`. Type={incident_type}, country={country_code}.",
    )
    manual_keys.add(sid)
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="data")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--model", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--api-key-env", default="OPENROUTER_API_KEY")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = load_settings(args.config)
    api_key = args.api_key or os.environ.get(args.api_key_env, "")
    if not api_key:
        print(f"error: api key missing (set ${args.api_key_env} or pass --api-key)", file=sys.stderr)
        return 2
    digester = OpenRouterDigester(args.model or settings.model, api_key)
    writer = RecordWriter(args.data)
    manual_keys = _existing_manual_keys(writer)

    pending = _list_pending_issues()
    approved = _list_approved_issues()
    work = [(issue, False) for issue in pending]
    seen = {issue["number"] for issue in pending}
    work.extend((issue, True) for issue in approved if issue["number"] not in seen)
    counts = {
        "imported:new": 0,
        "imported:existing": 0,
        "imported:existing-news": 0,
        "review:fetch-failed": 0,
        "review:not-disaster": 0,
        "review:no-country": 0,
        "rejected:no-url": 0,
        "error": 0,
    }
    for issue, is_approved in work[: args.limit]:
        try:
            outcome = _process_issue(issue, writer, digester, manual_keys, approved=is_approved)
        except Exception as exc:
            logger.exception("issue %s failed", issue.get("number"))
            counts["error"] += 1
            try:
                if is_approved:
                    _review(int(issue["number"]), f"bot error while processing approved submission: {exc}")
                else:
                    comment(int(issue["number"]), f"Bot error: {exc}")
            except Exception as comment_exc:
                logger.warning("issue %s: could not post error comment: %s", issue.get("number"), comment_exc)
            continue
        counts[outcome] += 1
    writer.flush()
    print(
        "submissions: "
        f"new={counts['imported:new']} "
        f"existing={counts['imported:existing']} "
        f"existing-news={counts['imported:existing-news']} "
        f"review(fetch)={counts['review:fetch-failed']} "
        f"review(not-disaster)={counts['review:not-disaster']} "
        f"review(no-country)={counts['review:no-country']} "
        f"rejected(no-url)={counts['rejected:no-url']} "
        f"errors={counts['error']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
