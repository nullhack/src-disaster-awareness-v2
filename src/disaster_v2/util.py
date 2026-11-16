from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml

_TRACKING_PREFIXES = ("utm_", "ocid", "cmpid", "fbclid", "gclid", "mc_cid", "mc_eid")
_SAFE_SLUG = re.compile(r"[^a-z0-9]+")


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


def _str_block(dumper: yaml.Dumper, data: str) -> Any:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


class _Dumper(yaml.Dumper):
    def ignore_aliases(self, data: Any) -> bool:  # noqa: ARG002
        return True


_Dumper.add_representer(str, _str_block)


def dump_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.dump(
        data,
        Dumper=_Dumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=1000,
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def iter_yaml(directory: Path, *, recursive: bool = True) -> list[Path]:
    if not directory.is_dir():
        return []
    pattern = "**/*.yaml" if recursive else "*.yaml"
    return sorted(directory.glob(pattern))


def canonical_url(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if not parts.scheme or not parts.netloc:
        return value
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(_TRACKING_PREFIXES)
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), "")
    )


def content_hash(*parts: object) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def slugify(value: str, fallback: str = "incident") -> str:
    slug = _SAFE_SLUG.sub("-", (value or "").lower()).strip("-")
    return (slug or fallback)[:80].strip("-") or fallback


def parse_date(value: object) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def date_str(value: object) -> str:
    parsed = parse_date(value)
    return parsed.isoformat() if parsed else ""


def max_date(*values: object) -> str:
    dates = [parsed for value in values if (parsed := parse_date(value)) is not None]
    return max(dates).isoformat() if dates else ""


def min_date(*values: object) -> str:
    dates = [parsed for value in values if (parsed := parse_date(value)) is not None]
    return min(dates).isoformat() if dates else ""


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else {}


def write_json(path: Path, data: object, *, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    tmp.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                loaded = json.loads(line)
                if isinstance(loaded, dict):
                    rows.append(loaded)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text + ("\n" if text else ""), encoding="utf-8")
    tmp.replace(path)


def as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
