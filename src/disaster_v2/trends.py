from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any

from .util import as_list, parse_date


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _shift_months(value: date, delta: int) -> date:
    month = value.month - 1 + delta
    year = value.year + month // 12
    month = month % 12 + 1
    return date(year, month, 1)


def _month_key(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def _month_label(value: date) -> str:
    return f"{value.strftime('%b')} {value.year}"


def _ranked(items: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    counts = Counter(str(item.get(field) or "Unknown") for item in items)
    return [{"label": label, "count": count} for label, count in counts.most_common()]


def build_trends(summaries: list[dict[str, Any]], as_of: str, *, months: int = 12) -> dict[str, Any]:
    """Build small, deterministic counts for the home pulse and trends page."""
    current = parse_date(as_of) or date.today()
    current_month = _month_start(current)
    month_starts = [_shift_months(current_month, offset) for offset in range(-(months - 1), 1)]
    month_index = {_month_key(month): index for index, month in enumerate(month_starts)}
    new_counts = [0] * months
    updated_sets: list[set[str]] = [set() for _ in range(months)]
    cutoff_30 = current - timedelta(days=30)
    new_last_30 = 0
    updated_last_30: set[str] = set()

    for item in summaries:
        incident_id = str(item.get("id") or "")
        first_seen = parse_date(item.get("first_seen"))
        if first_seen and cutoff_30 <= first_seen <= current:
            new_last_30 += 1
        if first_seen:
            index = month_index.get(_month_key(first_seen))
            if index is not None:
                new_counts[index] += 1

        for update in as_list(item.get("updates")):
            update_day = parse_date(update.get("log_date"))
            if not update_day or update_day > current:
                continue
            if update_day >= cutoff_30:
                updated_last_30.add(incident_id)
            index = month_index.get(_month_key(update_day))
            if index is not None:
                updated_sets[index].add(incident_id)

    active = [item for item in summaries if str(item.get("status") or "") == "active"]
    status_counts = Counter(str(item.get("status") or "unknown") for item in summaries)
    countries = {str(item.get("country_code")) for item in summaries if item.get("country_code")}
    active_by_type = _ranked(active, "incident_type")
    for item in active_by_type:
        if item["label"] == "Unknown":
            item["label"] = "Unclassified"
    active_by_region = _ranked(active, "region")
    for item in active_by_region:
        if item["label"] == "Global":
            item["label"] = "Unlocated"

    return {
        "schema": 2,
        "as_of": current.isoformat(),
        "months": [
            {
                "key": _month_key(month),
                "label": _month_label(month),
                "short_label": f"{month.strftime('%b')} {month.year % 100:02d}",
                "new_incidents": new_counts[index],
                "updated_incidents": len(updated_sets[index]),
            }
            for index, month in enumerate(month_starts)
        ],
        "new_last_30_days": new_last_30,
        "updated_last_30_days": len(updated_last_30),
        "active_count": len(active),
        "total_incidents": len(summaries),
        "country_count": len(countries),
        "status_counts": dict(sorted(status_counts.items())),
        "active_by_type": active_by_type,
        "active_by_region": active_by_region,
    }
