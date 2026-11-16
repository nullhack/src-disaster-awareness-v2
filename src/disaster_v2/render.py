from __future__ import annotations

import html
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlsplit

from .util import as_dict, as_list, load_json


def _h(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _safe_url(value: object) -> str:
    text = str(value or "").strip()
    try:
        parsed = urlsplit(text)
    except ValueError:
        return "#"
    return text if parsed.scheme in {"http", "https"} and parsed.netloc else "#"


def _json(value: object) -> str:
    return html.escape(json.dumps(value, ensure_ascii=False), quote=True)


def _short(value: str, limit: int = 220) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _human_date(value: str) -> str:
    text = (value or "")[:10]
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return "—"
    return parsed.strftime("%d %b %Y").lstrip("0")


def _human_month(value: str) -> str:
    text = (value or "")[:10]
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return "—"
    return parsed.strftime("%b %Y")


def _rfc822(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(f"{text[:10]}T00:00:00+00:00")
        except ValueError:
            return ""
    if parsed.tzinfo is None:

        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")


def _human_datetime(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return "—"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return _human_date(text)
    parsed = parsed.astimezone(UTC)
    return parsed.strftime("%d %b %Y %H:%M UTC").lstrip("0")


def _site_context(config: dict[str, Any]) -> dict[str, Any]:
    site = config.get("site", {}) if isinstance(config.get("site"), dict) else {}
    base_url = str(site.get("base_url") or "").rstrip("/")
    path = urlparse(base_url).path.rstrip("/")
    base_href = (path + "/") if path else "/"
    return {
        "title": str(site.get("title") or "Disaster Awareness"),
        "tagline": str(site.get("tagline") or ""),
        "base_url": base_url,
        "base_href": base_href,
        "repo": str(site.get("repo") or ""),
        "data_branch": str(site.get("data_branch") or "data"),
    }


def _href(path: str) -> str:
    return path.lstrip("/") or "./"


def _canonical(site: dict[str, Any], path: str) -> str:
    return site["base_url"] + (path if path.startswith("/") else "/" + path)


def _nav(site: dict[str, Any], active: str) -> str:
    links = [
        ("", "Situation"),
        ("incidents/", "Incidents"),
        ("trends/", "Trends"),
        ("following/", "Following"),
        ("about/", "About"),
        ("submit/", "Submit"),
    ]
    items = []
    for path, label in links:
        current = ' aria-current="page"' if path.rstrip("/").split("/")[0] == active else ""
        count = ' <span class="nav-count" data-follow-count hidden>0</span>' if path.startswith("following") else ""
        items.append(f'<a href="{_h(path)}"{current}>{_h(label)}{count}</a>')
    return "".join(items)


def _page(
    site: dict[str, Any],
    *,
    title: str,
    description: str,
    body: str,
    path: str,
    active: str = "",
    body_class: str = "",
) -> str:
    full_title = f"{title} · {site['title']}" if title else site["title"]
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<base href="{_h(site['base_href'])}">
<title>{_h(full_title)}</title>
<meta name="description" content="{_h(description)}">
<link rel="canonical" href="{_h(_canonical(site, path))}">
<meta property="og:title" content="{_h(full_title)}">
<meta property="og:description" content="{_h(description)}">
<meta property="og:type" content="website">
<meta name="theme-color" content="#a30f1c">
<link rel="stylesheet" href="assets/styles.css">
<script defer src="assets/app.js"></script>
</head>
<body class="{_h(body_class)}">
<a class="skip-link" href="#main">Skip to content</a>
<header class="site-header">
  <div class="wrap header-inner">
    <a class="wordmark" href="./"><span class="wordmark__mark" aria-hidden="true">DA</span><span>{_h(site['title'])}</span></a>
    <nav class="site-nav" aria-label="Primary">{_nav(site, active)}</nav>
    <form class="header-search" action="incidents/" method="get" role="search">
      <label class="visually-hidden" for="global-search">Search incidents</label>
      <input id="global-search" name="q" type="search" placeholder="Search" autocomplete="off">
      <button type="submit" class="button button--small">Search</button>
    </form>
  </div>
</header>
<main id="main" class="wrap">
{body}
</main>
<footer class="site-footer">
  <div class="wrap footer-inner">
    <p><strong>{_h(site['title'])}</strong> is an independent reference, not an official warning service. It is not affiliated with the Red Cross or Red Crescent Movement. Always follow local authorities for emergencies.</p>
    <p><a href="about/">About &amp; method</a> · <a href="trends/">Trends</a> · <a href="status/">Data status</a> · <a href="submit/">Submit a correction</a> · <a href="feed.xml">RSS</a> · <a href="https://github.com/{_h(site['repo'])}">GitHub</a></p>
    <p class="small">Data checked <span data-data-as-of>—</span>. Built from the <code>{_h(site['data_branch'])}</code> branch.</p>
  </div>
</footer>
<noscript><p class="noscript">JavaScript is off. The core pages and links still work; filtering and Following need JavaScript.</p></noscript>
</body>
</html>
"""


def _follow_button(
    incident_id: str, *, title: str = "", url: str = "", compact: bool = False
) -> str:
    label = "Follow" if not compact else ""
    class_name = "follow-button follow-button--icon" if compact else "follow-button"
    return (
        f'<button class="{class_name}" type="button" data-follow-id="{_h(incident_id)}" '
        f'data-follow-title="{_h(title)}" data-follow-url="{_h(url)}" '
        f'aria-pressed="false" aria-label="Follow this incident">{label}</button>'
    )


def _incident_row(item: dict[str, Any], *, show_follow: bool = True) -> str:
    status = str(item.get("status") or "")
    verified = _human_date(str(item.get("last_verified_update") or ""))
    official = int(item.get("coverage_stats", {}).get("official_sources", 0))
    news = int(item.get("coverage_stats", {}).get("news", 0))
    follow = (
        _follow_button(
            str(item["id"]),
            title=str(item.get("display_title") or ""),
            url=str(item.get("url") or ""),
        )
        if show_follow
        else ""
    )
    return f"""<article class="incident-row" data-incident-id="{_h(item['id'])}" data-title="{_h(str(item.get('display_title') or '').lower())}" data-type="{_h(item.get('incident_type'))}" data-region="{_h(item.get('region'))}" data-status="{_h(status)}" data-event="{_h(item.get('event_date'))}" data-verified="{_h(item.get('last_verified_update'))}" data-first="{_h(item.get('first_seen'))}" data-official="{official}" data-url="{_h(item.get('url'))}">
  <div class="row-main">
    <p class="kicker">{_h(item.get('incident_type'))}{(' · ' + _h(item.get('country_name'))) if item.get('country_name') else ''}</p>
    <h3 class="row-title"><a href="{_h(_href(item['url']))}">{_h(item.get('display_title'))}</a></h3>
    <p class="row-meta"><span class="status status--{_h(status)}">{_h(status)}</span> · {_h(item.get('country_name') or 'Global')} · event {_human_date(str(item.get('event_date') or ''))}</p>
    <p class="row-summary">{_h(item.get('summary_short') or 'No verified summary yet. See the official record.')}</p>
    <p class="row-provenance">Verified {verified} · {official} official record{'s' if official != 1 else ''} · {news} article{'s' if news != 1 else ''}</p>
  </div>
  {follow}
</article>"""


def _latest_panel(summaries: list[dict[str, Any]], limit: int = 8) -> str:
    rows = "".join(_incident_row(item) for item in summaries[:limit])
    if not rows:
        rows = '<p class="empty">No incidents are currently tracked. The last successful data update is shown below.</p>'
    return f"""<section class="section" aria-labelledby="latest-heading">
  <div class="section-head">
    <div>
      <p class="eyebrow">Change feed</p>
      <h2 id="latest-heading">Latest verified updates</h2>
    </div>
    <a class="text-link" href="incidents/">All incidents →</a>
  </div>
  <p class="section-note">Ordered by the most recent verified update, not by article volume.</p>
  <div class="incident-list">{rows}</div>
</section>"""


def _following_panel() -> str:
    return """<section class="section following-panel" data-following-panel hidden aria-labelledby="following-heading">
  <div class="section-head">
    <div><p class="eyebrow">Saved in this browser</p><h2 id="following-heading">Following</h2></div>
    <a class="text-link" href="following/">Manage →</a>
  </div>
  <div class="incident-list" data-following-list></div>
</section>"""


def _visit_panel() -> str:
    return """<section class="visit-panel" data-visit-panel hidden aria-labelledby="visit-heading">
  <div class="section-head">
    <div><p class="eyebrow">Since your last visit</p><h2 id="visit-heading">What changed</h2></div>
    <button class="button button--ghost button--small" type="button" data-dismiss-visit>Dismiss</button>
  </div>
  <p data-visit-summary></p>
  <div data-visit-list></div>
</section>"""


def _browse_regions(summaries: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for item in summaries:
        region = str(item.get("region") or "Unknown")
        counts[region] = counts.get(region, 0) + 1
    links = "".join(
        f'<li><a href="incidents/region/{_h(region_slug(region))}/">{_h(region)} <span>{count}</span></a></li>'
        for region, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    )
    return f"""<section class="section" aria-labelledby="browse-heading">
  <div class="section-head"><div><p class="eyebrow">Browse</p><h2 id="browse-heading">Regions</h2></div></div>
  <ul class="region-list">{links}</ul>
</section>"""


def region_slug(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-") or "unknown"


def type_slug(value: str) -> str:
    return region_slug(value)


def _chart_columns(months: list[dict[str, Any]], value_key: str, *, label: str, compact: bool = False) -> str:
    values = [int(month.get(value_key) or 0) for month in months]
    peak = max(values, default=0) or 1
    total = sum(values)
    rows: list[str] = []
    table_rows: list[str] = []
    for month, value in zip(months, values, strict=False):
        height = min(100, max(3, round(value / peak * 100))) if value else 0
        short_label = str(month.get("short_label") or month.get("label") or "")
        full_label = str(month.get("label") or short_label)
        rows.append(
            f'<li class="chart-column" aria-label="{_h(full_label)}: {value}">'
            f'<span class="chart-column__plot"><span class="chart-column__bar" style="--bar-height:{height}%"></span></span>'
            f'<span class="chart-column__value">{value}</span>'
            f'<span class="chart-column__label">{_h(short_label)}</span></li>'
        )
        table_rows.append(f"<tr><th scope=\"row\">{_h(full_label)}</th><td>{value}</td></tr>")
    rows_html = "".join(rows)
    table_html = "".join(table_rows)
    empty = '<p class="chart-empty">No activity in this period.</p>' if total == 0 else ""
    compact_class = " chart--compact" if compact else ""
    return f"""<figure class="chart{compact_class}">
  <figcaption class="chart__caption">{_h(label)}</figcaption>
  {empty}
  <ol class="chart-columns" aria-label="{_h(label)}">{rows_html}</ol>
  <details class="chart__table"><summary>View as table</summary>
    <table><caption>{_h(label)}</caption><thead><tr><th>Month</th><th>Count</th></tr></thead><tbody>{table_html}</tbody></table>
  </details>
</figure>"""


def _chart_bars(items: list[dict[str, Any]], *, label: str, tone: str = "red", limit: int | None = None) -> str:
    selected = items[:limit] if limit else items
    if not selected:
        return f'<p class="chart-empty">{_h(label)}: no active incidents.</p>'
    peak = max((int(item.get("count") or 0) for item in selected), default=0) or 1
    rows: list[str] = []
    table_rows: list[str] = []
    for item in selected:
        name = str(item.get("label") or "Unknown")
        count = int(item.get("count") or 0)
        width = max(2, min(100, round(count / peak * 100)))
        rows.append(
            f'<li class="chart-bar" aria-label="{_h(name)}: {count}">'
            f'<span class="chart-bar__label">{_h(name)}</span>'
            f'<span class="chart-bar__track"><span class="chart-bar__fill" style="--bar-width:{width}%"></span></span>'
            f'<span class="chart-bar__value">{count}</span></li>'
        )
        table_rows.append(f"<tr><th scope=\"row\">{_h(name)}</th><td>{count}</td></tr>")
    rows_html = "".join(rows)
    table_html = "".join(table_rows)
    hidden = len(items) - len(selected)
    note = f'<p class="chart-note">Showing the top {len(selected)} of {len(items)}.</p>' if hidden > 0 else ""
    return f"""<figure class="chart chart--bars chart--{_h(tone)}">
  <figcaption class="chart__caption">{_h(label)}</figcaption>
  <ul class="chart-bars" aria-label="{_h(label)}">{rows_html}</ul>
  {note}
  <details class="chart__table"><summary>View as table</summary>
    <table><caption>{_h(label)}</caption><thead><tr><th>Group</th><th>Active incidents</th></tr></thead><tbody>{table_html}</tbody></table>
  </details>
</figure>"""


def _pulse_panel(trends: dict[str, Any]) -> str:
    months = as_list(trends.get("months"))
    active_by_type = as_list(trends.get("active_by_type"))
    active_by_region = as_list(trends.get("active_by_region"))
    new_last_30 = int(trends.get("new_last_30_days") or 0)
    active = int(trends.get("active_count") or 0)
    regions = len(active_by_region)
    return f"""<section class="section pulse" aria-labelledby="pulse-heading">
  <div class="section-head">
    <div><p class="eyebrow">Pulse</p><h2 id="pulse-heading">Situation at a glance</h2></div>
    <a class="text-link" href="trends/">All trends →</a>
  </div>
  <p class="section-note">These counts describe what this site tracks, not disaster frequency or severity.</p>
  <div class="pulse-grid">
    <article class="pulse-card">
      <p class="kicker">Newly tracked · 30 days</p>
      <p class="pulse-number">{new_last_30}</p>
      <p class="pulse-note">Incidents added to the record in the last 30 days.</p>
      {_chart_columns(months, "new_incidents", label="Newly tracked incidents per month", compact=True)}
    </article>
    <article class="pulse-card">
      <p class="kicker">Active by hazard</p>
      <p class="pulse-number">{active}</p>
      <p class="pulse-note">Active incidents across {len(active_by_type)} hazard types.</p>
      {_chart_bars(active_by_type, label="Active incidents by hazard type", tone="red", limit=5)}
    </article>
    <article class="pulse-card">
      <p class="kicker">Active by region</p>
      <p class="pulse-number">{regions}</p>
      <p class="pulse-note">Regions with at least one active incident.</p>
      {_chart_bars(active_by_region, label="Active incidents by region", tone="blue", limit=5)}
    </article>
  </div>
</section>"""


def render_home(
    site: dict[str, Any],
    index: dict[str, Any],
    summaries: list[dict[str, Any]],
    status: dict[str, Any],
    trends: dict[str, Any],
) -> str:
    counts = index.get("counts", {})
    body = f"""<section class="hero">
  <p class="eyebrow">Situation</p>
  <h1>What changed, and what deserves your attention.</h1>
  <p class="lede">Tracking <strong>{int(counts.get('active', 0))}</strong> active incidents across <strong>{int(counts.get('countries', 0))}</strong> countries. Data checked <time datetime="{_h(index.get('as_of'))}">{_human_date(str(index.get('as_of') or ''))}</time>.</p>
  <p class="hero-note"><a href="trends/">Trends</a> · <a href="status/">Data status</a> · <a href="about/">How this works</a></p>
</section>
{_visit_panel()}
{_pulse_panel(trends)}
{_latest_panel(summaries)}
{_following_panel()}
{_browse_regions(summaries)}"""
    return _page(
        site,
        title="Situation",
        description="A change feed of verified disaster incident updates, with official sources and recent coverage.",
        body=body,
        path="/",
        active="",
    )


def _filter_options(values: list[str]) -> str:
    return "".join(f'<option value="{_h(value)}">{_h(value)}</option>' for value in values)


def render_incidents_index(
    site: dict[str, Any],
    summaries: list[dict[str, Any]],
    *,
    heading: str = "Incidents",
    description: str = "Browse tracked disaster incidents by place, type, and freshness.",
    filtered: list[dict[str, Any]] | None = None,
    path: str = "/incidents/",
    active: str = "incidents",
) -> str:
    items = filtered if filtered is not None else summaries
    regions = sorted({str(item.get("region") or "Unknown") for item in summaries})
    types = sorted({str(item.get("incident_type") or "Unknown") for item in summaries})
    rows = "".join(_incident_row(item) for item in items)
    if not rows:
        rows = '<p class="empty">No incidents match this view.</p>'
    body = f"""<section class="page-head">
  <p class="eyebrow">Reference</p>
  <h1>{_h(heading)}</h1>
  <p class="lede">{_h(description)}</p>
</section>
<form class="filter-bar" data-incident-filters>
  <div class="field field--search">
    <label for="incident-search">Search</label>
    <input id="incident-search" name="q" type="search" placeholder="Place, type, incident" autocomplete="off">
  </div>
  <div class="field"><label for="filter-status">Status</label><select id="filter-status" name="status"><option value="active">Active</option><option value="">All</option><option value="dormant">Dormant</option><option value="archived">Archived</option></select></div>
  <div class="field"><label for="filter-region">Region</label><select id="filter-region" name="region"><option value="">All regions</option>{_filter_options(regions)}</select></div>
  <div class="field"><label for="filter-type">Type</label><select id="filter-type" name="type"><option value="">All types</option>{_filter_options(types)}</select></div>
  <div class="field"><label for="filter-sort">Sort</label><select id="filter-sort" name="sort"><option value="verified">Recently verified</option><option value="event">Event date</option><option value="first">First tracked</option><option value="title">A–Z</option><option value="official">Most official records</option></select></div>
  <button class="button button--ghost" type="button" data-clear-filters>Clear</button>
</form>
<p class="result-count" data-result-count aria-live="polite">{len(items)} incidents</p>
<noscript><p class="notice">Filtering needs JavaScript. All incidents are listed below; use your browser's find function.</p></noscript>
<div class="incident-list" data-incident-list>{rows}</div>"""
    return _page(site, title=heading, description=description, body=body, path=path, active=active)


def _tier_badge(tier: str) -> str:
    label = "Official" if tier == "official" else "News"
    return f'<span class="tier tier--{_h(tier)}">{label}</span>'


def _key_links_html(detail: dict[str, Any]) -> str:
    links = as_list(detail.get("key_links"))
    if not links:
        return '<p class="empty">No source links are available yet.</p>'
    cards = []
    for link in links:
        cards.append(
            f"""<li class="key-link">
  <div class="key-link__top">{_tier_badge(str(link.get('tier') or 'news'))}<span class="reason">{_h(link.get('reason'))}</span></div>
  <a href="{_h(_safe_url(link.get('url')))}" rel="noopener noreferrer">{_h(link.get('label'))}</a>
  <p class="meta">{_h(link.get('source'))}{(' · ' + _human_date(str(link.get('date') or ''))) if link.get('date') else ''}</p>
</li>"""
        )
    rule = (
        "Newest official record plus up to two recent articles, de-duplicated."
        if any(link.get("tier") == "official" for link in links)
        else "Up to three recent articles, de-duplicated."
    )
    return f"""<ul class="key-links">{''.join(cards)}</ul>
<details class="quiet-details"><summary>How we choose these links</summary><p>{_h(rule)} Ranking prefers the latest verified update, then relevance to the incident, primary outlets, and recency. It is not a claim that every linked article is independent or correct.</p></details>"""


def _facts_html(detail: dict[str, Any]) -> str:
    facts = as_dict(detail.get("facts"))
    rows = [
        ("Event date", _human_date(str(detail.get("event_date") or ""))),
        ("First tracked", _human_date(str(detail.get("first_seen") or ""))),
        ("Last verified", _human_date(str(detail.get("last_verified_update") or ""))),
        ("Location", str(detail.get("country_name") or "Global")),
        ("Official records", str(detail["coverage_stats"]["official_sources"])),
        ("Verified updates", str(detail["coverage_stats"]["logs"])),
    ]
    if facts.get("max_magnitude") is not None:
        rows.append(("Max magnitude", f"M {facts['max_magnitude']}"))
    if facts.get("max_depth_km") is not None:
        rows.append(("Min depth", f"{facts['max_depth_km']} km"))
    if facts.get("official_alert"):
        rows.append(("Official alert", str(facts["official_alert"]).title()))
    if facts.get("place"):
        rows.append(("Place", str(facts["place"])))
    body = "".join(f"<div><dt>{_h(label)}</dt><dd>{_h(value)}</dd></div>" for label, value in rows)
    return f'<dl class="facts">{body}</dl>'


def _freshness(detail: dict[str, Any], as_of: str) -> tuple[str, str]:
    verified = str(detail.get("last_verified_update") or "")
    if not verified:
        return "No verified update", "The official record is the best available source."
    try:
        delta = (date.fromisoformat(as_of[:10]) - date.fromisoformat(verified[:10])).days
    except ValueError:
        return "Unknown", "Freshness could not be calculated."
    if delta < 7:
        return "Current", "Verified within the last week."
    if delta <= 30:
        return "Aging", "No verified update in the last week; check the official record."
    return "Stale", "No verified update in over 30 days; treat the summary as historical."


def _sourcing(detail: dict[str, Any]) -> str:
    count = int(detail["coverage_stats"]["official_sources"])
    if count == 0:
        return "News only — no official record is linked yet."
    if count == 1:
        return "Official record linked — one source family is represented."
    return f"Multiple official records — {count} source families are represented."


def _updates_html(detail: dict[str, Any]) -> str:
    updates = as_list(detail.get("updates"))
    if not updates:
        return '<p class="empty">No verified update has been generated yet. The official record is the best available source.</p>'
    visible = updates[:10]
    hidden = updates[10:]
    items = []
    for update in visible:
        items.append(_update_item(update))
    more = ""
    if hidden:
        more = f'<button class="button button--ghost" type="button" data-load-updates data-incident-id="{_h(detail["id"])}" data-loaded="0" aria-expanded="false">Show {min(10, len(hidden))} more updates</button><div data-extra-updates></div>'
    return f'<div class="update-list">{"".join(items)}</div>{more}'


def _update_item(update: dict[str, Any]) -> str:
    log_date = str(update.get("log_date") or "")
    count = len(as_list(update.get("news_ids")))
    return f"""<article class="update" id="u-{_h(log_date)}">
  <h3><time datetime="{_h(log_date)}">{_human_date(log_date)}</time></h3>
  <p>{_h(update.get('summary') or 'No summary text was generated for this update.')}</p>
  {f'<p class="meta">{count} linked article{'' if count == 1 else 's'}</p>' if count else ''}
</article>"""


def _related_html(detail: dict[str, Any]) -> str:
    related = as_list(detail.get("related"))
    if not related:
        return '<p class="empty">No closely related incidents.</p>'
    cards = "".join(
        f'<li><a href="{_h(_href(item["url"]))}">{_h(item["title"])}</a><span class="meta">{_h(item["reason"])} · verified {_human_date(str(item["last_verified_update"]))}</span></li>'
        for item in related
    )
    return f'<ul class="related-list">{cards}</ul>'


def render_incident(site: dict[str, Any], detail: dict[str, Any], *, as_of: str) -> str:
    stats = detail["coverage_stats"]
    freshness, freshness_note = _freshness(detail, as_of)
    latest_update = as_list(detail.get("updates"))[0] if detail.get("updates") else None
    changed = (
        _short(str(latest_update.get("summary") or ""), 500)
        if latest_update
        else "No verified update yet — see the official record."
    )
    coverage_range = stats.get("date_range") or []
    coverage_text = (
        f"{_human_date(coverage_range[0])} – {_human_date(coverage_range[1])}"
        if len(coverage_range) == 2
        else "No dated coverage"
    )
    body = f"""<nav class="breadcrumb" aria-label="Breadcrumb"><a href="incidents/">Incidents</a><span aria-hidden="true">/</span><span>{_h(detail.get('incident_type'))}</span></nav>
<article class="incident-page">
  <header class="incident-head">
    <p class="eyebrow">{_h(detail.get('incident_type'))}{(' · ' + _h(detail.get('country_name'))) if detail.get('country_name') else ''} · event {_human_date(str(detail.get('event_date') or ''))}</p>
    <h1>{_h(detail.get('display_title'))}</h1>
    <p class="status-line"><span class="status status--{_h(detail.get('status'))}">{_h(detail.get('status'))}</span> · Last verified update <time datetime="{_h(detail.get('last_verified_update'))}">{_human_date(str(detail.get('last_verified_update') or ''))}</time></p>
    <p class="meta">Data checked <time datetime="{_h(as_of)}">{_human_datetime(as_of)}</time>{f' · New coverage {_human_date(str(detail.get("latest_coverage_at") or ""))}' if detail.get('latest_coverage_at') and detail.get('latest_coverage_at') > detail.get('last_verified_update', '') else ''}</p>
    <div class="incident-actions">{_follow_button(str(detail['id']), title=str(detail.get('display_title') or ''), url=str(detail.get('url') or ''))}<button class="button button--ghost" type="button" data-copy-link>Copy link</button><a class="button button--ghost" href="{_h(_href(detail['coverage_url']))}">Coverage</a></div>
  </header>

  <section class="section prose" aria-labelledby="summary-heading">
    <h2 id="summary-heading">Summary</h2>
    <p class="automated-label">Automated summary{f' — updated {_human_date(str(detail.get("summary_updated") or ""))}' if detail.get('summary_updated') else ''}. <a href="about/">How summaries work</a>.</p>
    <p>{_h(detail.get('summary') or 'No verified summary yet. The official record is the best available source.')}</p>
  </section>

  <section class="section prose" aria-labelledby="changed-heading">
    <h2 id="changed-heading">What changed</h2>
    <p>{_h(changed)}</p>
    <p><a href="#updates">Read the verified updates →</a></p>
  </section>

  <section class="section" aria-labelledby="sources-heading">
    <div class="section-head"><div><p class="eyebrow">Provenance</p><h2 id="sources-heading">Read the sources</h2></div><span class="section-count">Up to 3</span></div>
    {_key_links_html(detail)}
  </section>

  <section class="section" aria-labelledby="glance-heading">
    <h2 id="glance-heading">At a glance</h2>
    {_facts_html(detail)}
  </section>

  <section class="section" aria-labelledby="trust-heading">
    <h2 id="trust-heading">Sourcing &amp; freshness</h2>
    <div class="trust-grid">
      <div><h3>Sourcing</h3><p>{_h(_sourcing(detail))}</p></div>
      <div><h3>Freshness</h3><p><strong>{_h(freshness)}</strong> — {_h(freshness_note)}</p></div>
    </div>
  </section>

  <section class="section" id="updates" aria-labelledby="updates-heading">
    <div class="section-head"><div><p class="eyebrow">Timeline</p><h2 id="updates-heading">Verified updates ({stats['logs']})</h2></div></div>
    {_updates_html(detail)}
  </section>

  <section class="section" aria-labelledby="coverage-heading">
    <div class="section-head"><div><p class="eyebrow">Evidence</p><h2 id="coverage-heading">Coverage at a glance</h2></div><a class="text-link" href="{_h(_href(detail['coverage_url']))}">Open coverage →</a></div>
    <p>{stats['news']} article{'s' if stats['news'] != 1 else ''} · {stats['official_sources']} official source famil{'ies' if stats['official_sources'] != 1 else 'y'} · {coverage_text} · {stats['logs']} verified update{'s' if stats['logs'] != 1 else ''}.</p>
    <p class="notice">Coverage is collected automatically and may include unrelated or syndicated items. The three links above are selected; the coverage page keeps everything one click away.</p>
  </section>

  <section class="section" aria-labelledby="related-heading">
    <h2 id="related-heading">Related incidents</h2>
    {_related_html(detail)}
  </section>

  <section class="section prose correction" aria-labelledby="correction-heading">
    <h2 id="correction-heading">Report a problem</h2>
    <p>If this incident combines unrelated events, links a bad article, or has the wrong place or date, open a correction request. Corrections are reviewed by a person.</p>
    <p><a class="button" href="{_h(detail.get('correction_url') or '#')}" rel="noopener noreferrer">Open a correction request ↗</a></p>
  </section>
</article>"""
    return _page(
        site,
        title=str(detail.get("display_title") or "Incident"),
        description=str(detail.get("summary_short") or detail.get("summary") or "Incident detail"),
        body=body,
        path=detail["url"],
        active="incidents",
        body_class="page-incident",
    )


def _coverage_clusters_items(clusters: list[dict[str, Any]]) -> str:
    if not clusters:
        return '<p class="empty">No coverage has been linked yet.</p>'
    items = []
    for cluster in clusters:
        outlets = cluster.get("outlets") or []
        outlet_text = f"{cluster.get('outlet_count', 0)} outlets" if cluster.get("outlet_count", 0) > 1 else (outlets[0] if outlets else "News")
        dates = cluster.get("date_range") or []
        date_text = _human_date(str(dates[0])) if dates else "Undated"
        article_links = ""
        if cluster.get("article_ids"):
            article_links = (
                f'<details class="cluster-details"><summary>Show {cluster.get("count", 0)} article link{"s" if cluster.get("count", 0) != 1 else ""}</summary>'
                f'<div data-cluster-articles="{_h(cluster.get("cluster_id"))}"></div></details>'
            )
        items.append(
            f"""<li class="coverage-cluster">
  <div class="cluster-main">
    <a class="cluster-title" href="{_h(_safe_url(cluster.get('url')))}" rel="noopener noreferrer">{_h(cluster.get('title'))}</a>
    <p class="meta">{_h(cluster.get('outlet'))} · {_h(outlet_text)} · {_h(date_text)}</p>
  </div>
  {article_links}
</li>"""
        )
    return f'<ul class="coverage-list">{"".join(items)}</ul>'


def render_coverage(
    site: dict[str, Any],
    detail: dict[str, Any],
    coverage: dict[str, Any],
    *,
    page_size: int = 25,
) -> str:
    clusters = as_list(coverage.get("clusters"))[:page_size]
    stats = detail["coverage_stats"]
    coverage_range = stats.get("date_range") or []
    range_text = (
        f"{_human_date(coverage_range[0])} – {_human_date(coverage_range[1])}"
        if len(coverage_range) == 2
        else "No dated coverage"
    )
    official = as_list(coverage.get("official"))
    official_items = "".join(
        f'<li><a href="{_h(_safe_url(item.get("url")))}" rel="noopener noreferrer">{_h(item.get("label"))}</a><span class="meta">{_h(item.get("type"))} · {_human_date(str(item.get("date") or ""))}</span></li>'
        for item in official
    ) or '<li class="empty">No official source links are available.</li>'
    updates = as_list(detail.get("updates"))
    update_items = "".join(
        f'<li><a href="#u-{_h(item.get("log_date"))}">{_human_date(str(item.get("log_date") or ""))}</a> — {_h(_short(str(item.get("summary") or ""), 160))}</li>'
        for item in updates[:30]
    ) or '<li class="empty">No verified updates yet.</li>'
    body = f"""<nav class="breadcrumb" aria-label="Breadcrumb"><a href="{_h(_href(detail['url']))}">{_h(detail.get('display_title'))}</a><span aria-hidden="true">/</span><span>Coverage</span></nav>
<section class="page-head">
  <p class="eyebrow">Evidence</p>
  <h1>Coverage</h1>
  <p class="lede">{stats['news']} articles · {stats['official_sources']} official source families · {range_text} · {stats['logs']} verified updates.</p>
  <p class="notice">Automatically collected; may include unrelated, duplicated, or syndicated items. We keep the full trail, not just the selected links.</p>
</section>
<div class="coverage-toolbar">
  <div class="tab-list" role="tablist" aria-label="Coverage views">
    <button role="tab" type="button" id="tab-clusters" aria-controls="panel-clusters" aria-selected="true" data-coverage-tab="clusters">All articles</button>
    <button role="tab" type="button" id="tab-official" aria-controls="panel-official" aria-selected="false" data-coverage-tab="official">Key sources</button>
    <button role="tab" type="button" id="tab-updates" aria-controls="panel-updates" aria-selected="false" data-coverage-tab="updates">Updates</button>
  </div>
  <div class="field field--search coverage-search"><label for="coverage-search">Search coverage</label><input id="coverage-search" type="search" placeholder="Search article titles" data-coverage-search autocomplete="off"></div>
</div>
<section id="panel-clusters" role="tabpanel" aria-labelledby="tab-clusters" data-coverage-panel="clusters">
  <p class="meta" data-coverage-count>{len(clusters)} story clusters shown. {stats['news']} articles in total.</p>
  <div data-coverage-list data-coverage-json="{_h(detail['id'])}">{_coverage_clusters_items(clusters)}</div>
  <nav class="pagination" data-coverage-pagination aria-label="Coverage pages"></nav>
  <noscript><p class="notice">Showing the first {page_size} clusters. Use search to narrow the full set; every article link remains one click away in the data JSON.</p></noscript>
</section>
<section id="panel-official" role="tabpanel" aria-labelledby="tab-official" data-coverage-panel="official" hidden>
  <ul class="official-list">{official_items}</ul>
</section>
<section id="panel-updates" role="tabpanel" aria-labelledby="tab-updates" data-coverage-panel="updates" hidden>
  <ul class="update-index">{update_items}</ul>
</section>
<p class="section"><a href="data/coverage/{_h(detail['id'])}.json" download>Download coverage JSON</a> · <a href="{_h(_href(detail['url']))}">Back to incident</a></p>"""
    return _page(
        site,
        title=f"Coverage · {detail.get('display_title')}",
        description=f"All collected coverage for {detail.get('display_title')}.",
        body=body,
        path=detail["coverage_url"],
        active="incidents",
        body_class="page-coverage",
    )


def render_trends(site: dict[str, Any], trends: dict[str, Any]) -> str:
    months = as_list(trends.get("months"))
    active_by_type = as_list(trends.get("active_by_type"))
    active_by_region = as_list(trends.get("active_by_region"))
    body = f"""<section class="page-head">
  <p class="eyebrow">Reference</p>
  <h1>Trends</h1>
  <p class="lede">A compact view of what this record contains, how it changes, and where the active incidents are. Data as of <time datetime="{_h(trends.get('as_of'))}">{_human_date(str(trends.get('as_of') or ''))}</time>.</p>
</section>
<section class="trend-stats" aria-label="Trend summary">
  <div class="trend-stat"><p class="kicker">Active incidents</p><p class="trend-stat__value">{int(trends.get('active_count') or 0)}</p><p class="trend-stat__note">Currently monitored</p></div>
  <div class="trend-stat"><p class="kicker">Newly tracked · 30 days</p><p class="trend-stat__value">{int(trends.get('new_last_30_days') or 0)}</p><p class="trend-stat__note">Added to the record</p></div>
  <div class="trend-stat"><p class="kicker">Updated · 30 days</p><p class="trend-stat__value">{int(trends.get('updated_last_30_days') or 0)}</p><p class="trend-stat__note">Incidents with a verified update</p></div>
  <div class="trend-stat"><p class="kicker">Countries</p><p class="trend-stat__value">{int(trends.get('country_count') or 0)}</p><p class="trend-stat__note">Represented in the record</p></div>
</section>
<p class="notice">These charts describe the tracked record, not the frequency or severity of disasters. Coverage volume is also not a severity measure.</p>
<section class="section" aria-labelledby="new-trend-heading">
  <div class="section-head"><div><p class="eyebrow">Volume</p><h2 id="new-trend-heading">Newly tracked incidents</h2></div></div>
  <p class="section-note">The month an incident first entered this record. This reflects monitoring and source coverage, not when the event began.</p>
  {_chart_columns(months, "new_incidents", label="Newly tracked incidents per month")}
</section>
<section class="section" aria-labelledby="update-trend-heading">
  <div class="section-head"><div><p class="eyebrow">Activity</p><h2 id="update-trend-heading">Incidents with verified updates</h2></div></div>
  <p class="section-note">An incident counts once per month when it has at least one verified update. Raw article counts are deliberately excluded.</p>
  {_chart_columns(months, "updated_incidents", label="Incidents with verified updates per month")}
</section>
<div class="trends-grid">
  <section class="section" aria-labelledby="type-trend-heading">
    <div class="section-head"><div><p class="eyebrow">Composition</p><h2 id="type-trend-heading">Active by hazard</h2></div></div>
    {_chart_bars(active_by_type, label="Active incidents by hazard type", tone="red")}
  </section>
  <section class="section" aria-labelledby="region-trend-heading">
    <div class="section-head"><div><p class="eyebrow">Geography</p><h2 id="region-trend-heading">Active by region</h2></div></div>
    {_chart_bars(active_by_region, label="Active incidents by region", tone="blue")}
  </section>
</div>
<section class="section prose" aria-labelledby="trend-method-heading">
  <h2 id="trend-method-heading">How to read these charts</h2>
  <p><strong>Active</strong> means an incident has a verified update within the last 30 days. <strong>Newly tracked</strong> is the month it first entered the record. <strong>Updated</strong> counts incidents with at least one verified update that month; it does not count every article.</p>
  <p>“Unclassified” means the record has no hazard type, and “Unlocated” means it has no country. They are kept visible rather than silently dropped.</p>
  <p>The record contains {int(trends.get('total_incidents') or 0)} incidents in total. Every chart has a table alternative, and the underlying counts are available as <a href="data/trends.json">JSON</a>.</p>
</section>"""
    return _page(
        site,
        title="Trends",
        description="Tracked incident volume, update activity, hazard mix, and regional distribution.",
        body=body,
        path="/trends/",
        active="trends",
    )


def render_following(site: dict[str, Any], summaries: list[dict[str, Any]]) -> str:
    body = """<section class="page-head">
  <p class="eyebrow">Saved in this browser</p>
  <h1>Following</h1>
  <p class="lede">No account, no sync. Your followed incidents are stored in this browser only.</p>
</section>
<div class="following-page" data-following-page>
  <p class="empty" data-following-empty>You are not following any incidents yet. Use the Follow button on an incident.</p>
  <div class="incident-list" data-following-list></div>
</div>
<p class="notice">To keep a cross-device list, copy the incident links or subscribe to the <a href="feed.xml">RSS feed</a>.</p>"""
    return _page(site, title="Following", description="Incidents saved in this browser.", body=body, path="/following/", active="following")


def render_submit(site: dict[str, Any]) -> str:
    repo = site["repo"]
    body = f"""<section class="page-head">
  <p class="eyebrow">Contribute</p>
  <h1>Submit a source or correction</h1>
  <p class="lede">Submissions are reviewed by a person before they appear. This is not an emergency channel.</p>
</section>
<div class="submit-grid">
  <section class="card" aria-labelledby="news-submit-heading">
    <h2 id="news-submit-heading">Send a news link</h2>
    <p>Have an article about a disaster that is missing? Send the URL. The pipeline fetches the page, checks whether it describes a disaster event, and either imports it or asks a maintainer to review it.</p>
    <form class="submit-form" data-news-submit data-repo="{_h(repo)}">
      <label for="submit-url">Article URL <span class="required">(required)</span></label>
      <input id="submit-url" name="url" type="url" inputmode="url" placeholder="https://…" required>
      <label for="submit-headline">Headline <span class="optional">(optional)</span></label>
      <input id="submit-headline" name="headline" type="text" autocomplete="off">
      <button class="button" type="submit">Continue to GitHub ↗</button>
      <p class="meta">You will need a GitHub account. The issue is the review record; GitHub notifications are the status channel.</p>
    </form>
  </section>
  <section class="card" aria-labelledby="correction-submit-heading">
    <h2 id="correction-submit-heading">Report a problem with an incident</h2>
    <p>Use this if an incident combines unrelated events, links an unrelated article, has the wrong place or date, or cites a broken link.</p>
    <p><a class="button button--ghost" href="https://github.com/{_h(repo)}/issues/new?template=correction.yml" rel="noopener noreferrer">Open a correction request ↗</a></p>
  </section>
</div>
<section class="section prose" aria-labelledby="outcomes-heading">
  <h2 id="outcomes-heading">What happens next</h2>
  <ol class="outcome-list">
    <li><strong>Pending</strong> — your issue is queued.</li>
    <li><strong>In review</strong> — a maintainer needs to decide; the issue stays open.</li>
    <li><strong>Approved</strong> — a maintainer has approved the import.</li>
    <li><strong>Imported</strong> — the source is linked to an incident.</li>
    <li><strong>Rejected</strong> — the issue is closed with a reason.</li>
  </ol>
  <p>Read the <a href="about/">method</a> for what counts as a source and how incidents are formed.</p>
</section>"""
    return _page(site, title="Submit", description="Submit a news link or correction for review.", body=body, path="/submit/", active="submit")


def render_about(site: dict[str, Any]) -> str:
    body = """<section class="page-head"><p class="eyebrow">Method</p><h1>About this reference</h1><p class="lede">A durable, citable record of disaster incidents and the evidence behind them.</p></section>
<div class="prose">
  <h2>What this is</h2>
  <p>An incident reference with a change feed. Each incident page separates what is known, when it was last verified, and which official record or article supports it.</p>
  <h2>What this is not</h2>
  <p>It is not an official warning service and it is not real-time. For emergencies, follow local authorities. An “official record linked” label means a source is present; it does not mean every fact is confirmed.</p>
  <h2>Sources and cadence</h2>
  <p>Reports come from source adapters such as USGS, GDACS, WHO Disease Outbreak News, and ERCC. News coverage is collected automatically and deduplicated by URL where possible. The data branch is the audit trail; the site is a generated read model.</p>
  <h2>How incidents are formed</h2>
  <p>A source report can birth an incident. Reports and news are linked with explicit edges, so a link can be inspected or reversed without moving files. Merges are recorded with aliases and a <code>merged_into</code> pointer; a merged page keeps a visible scope note.</p>
  <h2>How the three links are chosen</h2>
  <p>The first link is the newest official record specific to the event. The remaining slots prefer articles cited by the latest verified update, then recent independent coverage that names the place or event. Syndicated copies and known aggregators are demoted. The rule is shown on every incident page.</p>
  <h2>Two clocks</h2>
  <p><strong>Last verified update</strong> is when a human or generated log last checked the incident. <strong>Data checked</strong> is when this static site was built. They are deliberately separate.</p>
  <h2>Corrections</h2>
  <p>If an incident is over-merged, a link is wrong, or a date is wrong, open a correction request. Corrections are reviewed before publication.</p>
  <h2>Licence and citation</h2>
  <p>Source records keep their original provenance. Cite the incident page and the official record it links to; do not cite this summary as an official warning.</p>
</div>"""
    return _page(site, title="About", description="Method, sources, and limitations.", body=body, path="/about/", active="about")


def render_status(site: dict[str, Any], status: dict[str, Any], index: dict[str, Any]) -> str:
    counts = status.get("counts", {})
    source_rows = "".join(
        f'<tr><td>{_h(item.get("source"))}</td><td>{int(item.get("reports", 0))}</td><td>{_human_date(str(item.get("latest") or ""))}</td></tr>'
        for item in as_list(status.get("sources"))
    )
    body = f"""<section class="page-head"><p class="eyebrow">Transparency</p><h1>Data status</h1><p class="lede">What the build knows, and when it last knew it.</p></section>
<div class="status-grid">
  <div class="card"><p class="kicker">Data as of</p><p class="big">{_human_date(str(status.get('as_of') or ''))}</p><p class="meta">Build generated {_human_datetime(str(status.get('generated_at') or ''))}</p></div>
  <div class="card"><p class="kicker">Last source activity</p><p class="big">{_human_datetime(str(status.get('last_successful_ingest_at') or ''))}</p><p class="meta">Latest report search or article timestamp.</p></div>
  <div class="card"><p class="kicker">Coverage</p><p class="big">{int(counts.get('incidents', 0))} incidents</p><p class="meta">{int(counts.get('reports', 0))} reports · {int(counts.get('news', 0))} news · {int(counts.get('links', 0))} links</p></div>
</div>
<section class="section" aria-labelledby="sources-status-heading"><h2 id="sources-status-heading">Source status</h2><table><thead><tr><th>Source</th><th>Reports</th><th>Latest record</th></tr></thead><tbody>{source_rows}</tbody></table></section>
<section class="section prose" aria-labelledby="known-heading"><h2 id="known-heading">Known limitations</h2><ul><li>News coverage is collected automatically and can include syndicated or unrelated items.</li><li>This site is built from the data branch and may lag the latest ingestion run.</li><li>A linked official record is not a guarantee that every figure is current.</li></ul><p>Build version <code>{_h(status.get('build_version'))}</code>{f' · data commit <code>{_h(str(status.get("data_sha"))[:12])}</code>' if status.get('data_sha') else ''}.</p></section>"""
    return _page(site, title="Data status", description="Data freshness and source status.", body=body, path="/status/", active="")


def render_404(site: dict[str, Any]) -> str:
    body = """<section class="page-head"><p class="eyebrow">404</p><h1>That page moved or never existed.</h1><p class="lede">The incident may have been merged or renamed. Search the reference, or open the closest incident.</p><p><a class="button" href="incidents/">Browse incidents</a></p></section>"""
    return _page(site, title="Not found", description="Page not found.", body=body, path="/404.html")


def _xml_escape(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def render_feed(site: dict[str, Any], summaries: list[dict[str, Any]], as_of: str) -> str:
    items = []
    for item in summaries[:30]:
        url = _canonical(site, item["url"])
        items.append(
            f"""<item><title>{_xml_escape(item.get('display_title'))}</title><link>{_xml_escape(url)}</link><guid>{_xml_escape(url)}</guid><pubDate>{_xml_escape(_rfc822(str(item.get('last_verified_update') or as_of)))}</pubDate><description>{_xml_escape(item.get('summary_short') or '')}</description></item>"""
        )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>{_xml_escape(site['title'])}</title><link>{_xml_escape(site['base_url'])}/</link><description>{_xml_escape(site['tagline'])}</description><lastBuildDate>{_xml_escape(_rfc822(as_of))}</lastBuildDate>{''.join(items)}</channel></rss>
"""


def render_sitemap(site: dict[str, Any], summaries: list[dict[str, Any]]) -> str:
    urls = ["/", "/incidents/", "/trends/", "/following/", "/submit/", "/about/", "/status/"]
    urls.extend(item["url"] for item in summaries)
    urls.extend(item["coverage_url"] for item in summaries)
    entries = "".join(
        f"<url><loc>{_xml_escape(_canonical(site, path))}</loc></url>" for path in urls
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>\n'


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def render_site(
    out_root: Path,
    *,
    config: dict[str, Any],
    index: dict[str, Any],
    details: dict[str, dict[str, Any]],
    summaries: list[dict[str, Any]],
    status: dict[str, Any],
    trends: dict[str, Any],
) -> None:
    site = _site_context(config)
    as_of = str(index.get("as_of") or "")
    _write(out_root / "index.html", render_home(site, index, summaries, status, trends))
    _write(out_root / "incidents" / "index.html", render_incidents_index(site, summaries))
    for region in sorted({str(item.get("region") or "Unknown") for item in summaries}):
        subset = [item for item in summaries if str(item.get("region") or "Unknown") == region]
        _write(
            out_root / "incidents" / "region" / region_slug(region) / "index.html",
            render_incidents_index(
                site,
                summaries,
                heading=f"{region} incidents",
                description=f"Tracked incidents in {region}.",
                filtered=subset,
                path=f"/incidents/region/{region_slug(region)}/",
                active="incidents",
            ),
        )
    for incident_type in sorted({str(item.get("incident_type") or "Unknown") for item in summaries}):
        subset = [item for item in summaries if str(item.get("incident_type") or "Unknown") == incident_type]
        _write(
            out_root / "incidents" / "type" / type_slug(incident_type) / "index.html",
            render_incidents_index(
                site,
                summaries,
                heading=f"{incident_type} incidents",
                description=f"Tracked {incident_type.lower()} incidents.",
                filtered=subset,
                path=f"/incidents/type/{type_slug(incident_type)}/",
                active="incidents",
            ),
        )
    for incident_id, detail in details.items():
        slug = str(detail.get("slug") or "")
        _write(out_root / "i" / incident_id / slug / "index.html", render_incident(site, detail, as_of=as_of))
        coverage_path = out_root / "data" / "coverage" / f"{incident_id}.json"
        coverage = load_json(coverage_path) if coverage_path.is_file() else {}
        _write(
            out_root / "i" / incident_id / slug / "coverage" / "index.html",
            render_coverage(site, detail, coverage),
        )
    _write(out_root / "trends" / "index.html", render_trends(site, trends))
    _write(out_root / "following" / "index.html", render_following(site, summaries))
    _write(out_root / "submit" / "index.html", render_submit(site))
    _write(out_root / "about" / "index.html", render_about(site))
    _write(out_root / "status" / "index.html", render_status(site, status, index))
    _write(out_root / "404.html", render_404(site))
    _write(out_root / "feed.xml", render_feed(site, summaries, as_of))
    _write(out_root / "sitemap.xml", render_sitemap(site, summaries))
    _write(out_root / "robots.txt", f"User-agent: *\nAllow: /\nSitemap: {site['base_url']}/sitemap.xml\n")
