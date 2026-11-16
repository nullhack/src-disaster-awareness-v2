from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

_AGGREGATOR_DOMAINS = (
    "msn.com",
    "yahoo.com",
    "aol.com",
    "flipboard.com",
    "news.google.com",
)

_TERM_STOPWORDS = frozenset(
    {
        "latest",
        "update",
        "event",
        "news",
        "report",
        "with",
        "from",
        "this",
        "that",
        "incident",
    }
)

FRESHNESS_DAYS = 30


def _raw(report: dict[str, Any]) -> dict[str, Any]:
    raw = report.get("raw_fields")
    return raw if isinstance(raw, dict) else {}


def _first_text(raw: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if value:
            return str(value)
    return ""


def report_url(report: dict[str, Any]) -> str:
    source = str(report.get("source") or "")
    source_id = str(report.get("source_id") or "")
    raw = _raw(report)
    direct = _first_text(raw, "link", "url", "document_url")
    if direct:
        return direct
    if source == "USGS":
        return f"https://earthquake.usgs.gov/earthquakes/eventpage/{source_id}"
    if source == "GDACS":
        return f"https://www.gdacs.org/report.aspx?eventid={source_id}"
    if source == "WHO":
        slug = _first_text(raw, "UrlName", "ItemDefaultUrl", "DonId")
        if slug:
            if slug.startswith("/"):
                slug = slug.lstrip("/")
            return f"https://www.who.int/{slug}"
        return ""
    return ""


def report_label(report: dict[str, Any]) -> str:
    raw = _raw(report)
    return (
        _first_text(raw, "title", "Title", "name")
        or str(report.get("name") or "")
        or str(report.get("source") or "Official record")
    )


def source_links(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for report in reports:
        url = report_url(report)
        if not url:
            continue
        links.append(
            {
                "type": str(report.get("source") or "Official"),
                "label": report_label(report),
                "url": url,
                "date": str(report.get("report_date") or ""),
                "meta": str(report.get("source_id") or ""),
            }
        )
    links.sort(key=lambda item: (item.get("date") or "", item.get("label") or ""))
    return links


def _incident_terms(incident: dict[str, Any]) -> list[str]:
    values = [
        str(incident.get("display_name") or ""),
        str(incident.get("country_name") or ""),
        str((incident.get("physical") or {}).get("place") or "")
        if isinstance(incident.get("physical"), dict)
        else "",
    ]
    values.extend(str(key) for key in incident.get("search_keys") or [])
    terms: set[str] = set()
    for value in values:
        for term in re.findall(r"[^\W\d_]{4,}", value):
            lower = term.lower()
            if lower not in _TERM_STOPWORDS:
                terms.add(lower)
    return sorted(terms)


def _news_date(item: dict[str, Any]) -> datetime | None:
    value = str(item.get("published_date") or "")[:10]
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _news_score(
    item: dict[str, Any], terms: list[str], *, log_tier: int = 0
) -> tuple[int, int, int, int, str]:
    headline = str(item.get("headline") or item.get("title") or "").lower()
    body = str(item.get("body") or "").lower()
    headline_matches = sum(1 for term in terms if term in headline)
    body_matches = sum(1 for term in terms if term in body)
    domain = urlparse(str(item.get("url") or "")).netloc.lower()
    aggregator = any(
        domain == blocked or domain.endswith("." + blocked)
        for blocked in _AGGREGATOR_DOMAINS
    )
    return (
        headline_matches,
        int(not aggregator),
        body_matches,
        -log_tier,
        str(item.get("published_date") or ""),
    )


def _headline_key(item: dict[str, Any]) -> str:
    headline = str(item.get("headline") or item.get("title") or "").lower()
    return re.sub(r"[^a-z0-9]+", "", headline)[:80]


def _select_news(
    items: list[dict[str, Any]],
    terms: list[str],
    limit: int,
    *,
    log_tiers: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    tiers = log_tiers or {}
    dated = [item for item in items if item.get("url")]
    latest = max(
        (value for item in dated if (value := _news_date(item)) is not None),
        default=None,
    )
    if latest is not None:
        cutoff = latest - timedelta(days=FRESHNESS_DAYS)
        fresh = [
            item
            for item in dated
            if (value := _news_date(item)) is None or value >= cutoff
        ]
        if fresh:
            dated = fresh

    scored = [
        (item, _news_score(item, terms, log_tier=tiers.get(str(item.get("url") or ""), 99)))
        for item in dated
    ]
    has_relevance = any(score[0] or score[2] for _, score in scored)

    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item, score in sorted(scored, key=lambda pair: pair[1], reverse=True):
        if has_relevance and not (score[0] or score[2]):
            continue
        key = _headline_key(item)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        picked.append(item)
        if len(picked) >= limit:
            break
    return picked


def _latest_official(links: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [link for link in links if link.get("url")]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda link: (link.get("date") or "", link.get("label") or ""),
    )


def _key_link(
    tier: str,
    label: str,
    url: str,
    source: str,
    date: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "tier": tier,
        "label": label,
        "url": url,
        "source": source,
        "date": date,
        "reason": reason,
    }


def select_key_links(
    official_links: list[dict[str, Any]],
    logs: list[dict[str, Any]],
    news: list[dict[str, Any]],
    incident: dict[str, Any],
) -> list[dict[str, Any]]:
    terms = _incident_terms(incident)
    official = _latest_official(official_links)

    log_news: list[dict[str, Any]] = []
    log_tiers: dict[str, int] = {}
    for tier, log in enumerate(reversed(logs or [])):
        for item in log.get("news") or []:
            log_news.append(item)
            url = str(item.get("url") or "")
            if url:
                log_tiers.setdefault(url, tier)
    latest_log_urls = {
        str(item.get("url"))
        for item in ((logs[-1].get("news") if logs else []) or [])
        if item.get("url")
    }
    news_pool = log_news or news
    selected_news = _select_news(
        news_pool, terms, 2 if official else 3, log_tiers=log_tiers
    )

    key_links: list[dict[str, Any]] = []
    if official:
        key_links.append(
            _key_link(
                "official",
                official.get("label") or official.get("type") or "Official record",
                official["url"],
                official.get("type") or "Official",
                official.get("date") or "",
                "Newest official record",
            )
        )
    for item in selected_news:
        if not log_news:
            reason = "News"
        elif str(item.get("url")) in latest_log_urls:
            reason = "Used in latest update"
        else:
            reason = "Recent coverage"
        key_links.append(
            _key_link(
                "news",
                str(item.get("headline") or item.get("title") or ""),
                str(item["url"]),
                str(item.get("outlet") or item.get("source") or ""),
                str(item.get("published_date") or ""),
                reason,
            )
        )
    if len(key_links) < 3:
        extras = sorted(
            (
                link
                for link in official_links
                if link.get("url") and link is not official
            ),
            key=lambda link: (link.get("date") or "", link.get("label") or ""),
            reverse=True,
        )
        for link in extras:
            key_links.append(
                _key_link(
                    "official",
                    link.get("label") or link.get("type") or "Official record",
                    link["url"],
                    link.get("type") or "Official",
                    link.get("date") or "",
                    "Official record",
                )
            )
            if len(key_links) >= 3:
                break
    return key_links
