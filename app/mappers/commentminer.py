"""CommentMiner GET /api/posts/combined -> commenters (type unknown)."""
from __future__ import annotations
import re
from app.records import NormalizedRecord, MapResult
from app.mappers.base import norm_row, rows_from, pick, parse_dt

TOOL = "commentminer"
_SQUASH = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
_SUFFIX = re.compile(r"\b(inc|llc|ltd|pvt|private|limited|corp|corporation|co|technologies|technology|tech|solutions|services|group|global|consulting|systems|software|labs?)\b\.?", re.I)


def _from_headline(headline: str | None) -> str | None:
    """'Bench sales recruiter at Tech Vector Minds INC | Hiring' -> 'Tech Vector Minds INC'."""
    m = re.search(r"(?:\bat\b|@)\s*([^|•·,;/()\[\]]+)", headline or "", re.I)
    if not m:
        return None
    name = re.split(r"\s[-–—]\s", m.group(1))[0].strip(" .-")
    return name if 2 <= len(name) <= 60 else None


def _from_url(curl: str | None, website: str | None) -> str | None:
    slug = re.search(r"linkedin\.com/company/([^/?#]+)", curl or "", re.I)
    if slug:
        words = [w for w in re.split(r"[-_]+", slug.group(1)) if w and not w.isdigit()]
        if words:
            return " ".join(w.capitalize() for w in words)
    host = re.sub(r"^(https?://)?(www\.)?", "", (website or "").strip().lower()).split("/")[0]
    root = host.split(".")[0] if host else ""
    return root.capitalize() if len(root) >= 2 else None


def company_name(headline, curl, website) -> str | None:
    """CommentMiner gives a company page/website but no company name. Take the name from the headline when it
    agrees with the website/page (it reads better), else from the page slug or the website."""
    h, fallback = _from_headline(headline), _from_url(curl, website)
    if h and (curl or website):
        hk, fk = _SQUASH(_SUFFIX.sub("", h)), _SQUASH(_SUFFIX.sub("", fallback or ""))
        if hk and fk and (hk[:5] in fk or fk[:5] in hk):
            return h
        return fallback
    return h or fallback


def map_payload(payload: object) -> MapResult:
    res = MapResult()
    try:
        rows = rows_from(payload, ("comments", "data", "items", "results", "rows"))
    except ValueError as e:
        res.errors.append(str(e))
        return res
    for i, raw in enumerate(rows):
        try:
            if not isinstance(raw, dict):
                raise ValueError("row is not an object")
            r = norm_row(raw)
            name = pick(r, "author_name", "name")
            url = pick(r, "author_profile_url", "profile_url", "linkedin_url")
            if not (name or url):
                raise ValueError("no name or profile url")
            fields = {
                "full_name": name, "headline": pick(r, "author_headline", "headline"),
                "linkedin_url": url, "email": pick(r, "email"), "phone": pick(r, "phone"),
                "company_website": pick(r, "company_website"),
                "company_url": pick(r, "company_linkedin_url"),
                "company_name": company_name(pick(r, "author_headline", "headline"), pick(r, "company_linkedin_url"), pick(r, "company_website")),
                "lead_type": "unknown",
            }
            key = url or f"{name}|{pick(r, 'post_id')}"
            res.records.append(NormalizedRecord(TOOL, "person", f"{TOOL}:{key}", raw, fields,
                                                parse_dt(pick(r, "commented_at", "collected_at", "created_at", "comment_date"))))
        except Exception as e:
            res.errors.append(f"row {i}: {e}")
    return res
