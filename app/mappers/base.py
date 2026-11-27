"""Helpers shared by the four tool mappers. Tolerant by design: a bad row is skipped, never fatal."""
from __future__ import annotations
import re
import os
from datetime import datetime, timedelta, timezone


def _tool_tz() -> timezone:
    """Time zone for tool timestamps that carry none. The four tools record India time (IST) unless TOOL_UTC_OFFSET says otherwise."""
    m = re.fullmatch(r"([+-])(\d{1,2}):?(\d{2})", os.environ.get("TOOL_UTC_OFFSET", "+05:30").strip())
    if not m:
        return timezone(timedelta(hours=5, minutes=30))
    sign = 1 if m.group(1) == "+" else -1
    return timezone(sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3))))

_NONALNUM = re.compile(r"[^a-z0-9]+")
PROFILE_RE = re.compile(r"https?://[^\s,;|\"'<>]*linkedin\.com/in/[^\s,;|\"'<>]+", re.I)


def norm_key(k: object) -> str:
    return _NONALNUM.sub("_", str(k).strip().lower()).strip("_")


def norm_row(row: dict) -> dict:
    return {norm_key(k): v for k, v in row.items()}


def rows_from(payload: object, keys: tuple[str, ...]) -> list:
    """Accept a bare list, or a dict holding the list under one of `keys`."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in keys:
            v = payload.get(k)
            if isinstance(v, list):
                return v
    raise ValueError(f"unexpected payload shape: {type(payload).__name__}")


def clean(v: object) -> str | None:
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        v = ", ".join(str(x).strip() for x in v if str(x).strip())
    s = str(v).strip()
    # tools write placeholders instead of leaving a field empty; they are not data (a company named
    # "Not available" would even group unrelated people under one fake company)
    return None if s.lower() in _PLACEHOLDERS else (s or None)


_PLACEHOLDERS = {"not available", "not provided", "n/a", "na", "none", "null", "nil", "-", "--", "unknown", "not found"}


def pick(row: dict, *names: str) -> str | None:
    """First non-empty value among normalized column names."""
    for n in names:
        s = clean(row.get(n))
        if s:
            return s
    return None


def parse_dt(v: object, assume_tz=None) -> datetime | None:
    """assume_tz: the zone of a timestamp that carries none, when a tool is known to differ from TOOL_UTC_OFFSET."""
    s = clean(v)
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    for candidate in (s, s.replace(" ", "T", 1)):
        try:
            d = datetime.fromisoformat(candidate)
            return (d if d.tzinfo else d.replace(tzinfo=assume_tz or _tool_tz())).astimezone(timezone.utc)
        except ValueError:
            pass
    return None


def profile_urls(v: object) -> list[str]:
    s = clean(v)
    return PROFILE_RE.findall(s) if s else []
