"""CommentMiner GET /api/posts/combined -> commenters (type unknown)."""
from __future__ import annotations
from app.records import NormalizedRecord, MapResult
from app.mappers.base import norm_row, rows_from, pick, parse_dt

TOOL = "commentminer"


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
                "lead_type": "unknown",
            }
            key = url or f"{name}|{pick(r, 'post_id')}"
            res.records.append(NormalizedRecord(TOOL, "person", f"{TOOL}:{key}", raw, fields,
                                                parse_dt(pick(r, "commented_at", "collected_at", "created_at", "comment_date"))))
        except Exception as e:
            res.errors.append(f"row {i}: {e}")
    return res
