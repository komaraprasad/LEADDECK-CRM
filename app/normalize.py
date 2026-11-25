"""Pure key-normalization helpers used for de-duplication."""
from __future__ import annotations
import re
import unicodedata
from urllib.parse import urlparse, unquote

_WS = re.compile(r"\s+")


def _parse(url: str | None):
    if not url or not str(url).strip():
        return None
    raw = str(url).strip()
    if "://" not in raw:
        raw = "https://" + raw
    try:
        p = urlparse(raw)
    except ValueError:
        return None
    host = (p.hostname or "").lower()
    if not host or not (host == "linkedin.com" or host.endswith(".linkedin.com")):
        return None
    return p


def _segments(p):
    return [unquote(s) for s in p.path.split("/") if s]


def _slug_after(url: str | None, marker: str) -> str | None:
    p = _parse(url)
    if p is None:
        return None
    segs = _segments(p)
    if len(segs) >= 2 and segs[0].lower() == marker:
        slug = segs[1].strip().lower()
        return slug or None
    return None


def normalize_person_url(url: str | None) -> str | None:
    slug = _slug_after(url, "in")
    return f"https://www.linkedin.com/in/{slug}" if slug else None


def normalize_company_url(url: str | None) -> str | None:
    slug = _slug_after(url, "company")
    return f"https://www.linkedin.com/company/{slug}" if slug else None


def normalize_domain(website: str | None) -> str | None:
    if not website or not str(website).strip():
        return None
    raw = str(website).strip()
    if "://" not in raw:
        raw = "//" + raw
    try:
        host = (urlparse(raw).hostname or "").lower()
    except ValueError:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host if "." in host else None


def normalize_name(name: str | None) -> str:
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return _WS.sub(" ", s).strip().casefold()
