from __future__ import annotations

from ._country_names import country_name
from ._regions import subregion_for_country

_SUBREGION_TO_REGION: dict[str, str] = {
    "Eastern Africa": "Africa",
    "Middle Africa": "Africa",
    "Northern Africa": "Africa",
    "Southern Africa": "Africa",
    "Western Africa": "Africa",
    "Caribbean": "Americas",
    "Central America": "Americas",
    "Northern America": "Americas",
    "South America": "Americas",
    "Central Asia": "Asia",
    "Eastern Asia": "Asia",
    "South-Eastern Asia": "Asia",
    "Southern Asia": "Asia",
    "Western Asia": "Asia",
    "Eastern Europe": "Europe",
    "Northern Europe": "Europe",
    "Southern Europe": "Europe",
    "Western Europe": "Europe",
    "Australia and New Zealand": "Oceania",
    "Melanesia": "Oceania",
    "Micronesia": "Oceania",
    "Polynesia": "Oceania",
    "Antarctica": "Antarctica",
}


def country_label(code: str) -> str:
    code = (code or "").upper()
    if not code:
        return "Global"
    return country_name(code) or code


def region_for_country(code: str) -> str:
    code = (code or "").upper()
    if not code:
        return "Global"
    return _SUBREGION_TO_REGION.get(subregion_for_country(code), "Unknown")


def type_label(value: str) -> str:
    value = (value or "").strip()
    return value or "Unknown"
