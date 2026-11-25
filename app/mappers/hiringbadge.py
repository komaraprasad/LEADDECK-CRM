"""Hiring Badge Finder GET /api/leads and its export columns -> employer contacts."""
from __future__ import annotations
from app.records import NormalizedRecord, MapResult
from app.mappers.base import norm_row, rows_from, pick, parse_dt

TOOL = "hiringbadge"


def map_payload(payload: object) -> MapResult:
    res = MapResult()
    try:
        rows = rows_from(payload, ("leads", "people", "data", "items", "results"))
    except ValueError as e:
        res.errors.append(str(e))
        return res
    for i, raw in enumerate(rows):
        try:
            if not isinstance(raw, dict):
                raise ValueError("row is not an object")
            r = norm_row(raw)
            name = pick(r, "name", "person_name")
            url = pick(r, "profile_url", "person_linkedin_url", "linkedin_url")
            if not (name or url):
                raise ValueError("no name or profile url")
            company = pick(r, "company", "company_name")
            fields = {
                "full_name": name, "headline": pick(r, "headline"),
                "title": pick(r, "title", "current_designation"),
                "location": pick(r, "location"), "linkedin_url": url,
                "company_name": company, "company_website": pick(r, "company_website"),
                "company_industry": pick(r, "company_industry"), "company_size": pick(r, "company_size"),
                "lead_type": "employer_contact",
            }
            key = url or f"{name}|{company}"
            res.records.append(NormalizedRecord(TOOL, "person", f"{TOOL}:{key}", raw, fields,
                                                parse_dt(pick(r, "last_seen", "first_found", "date_collected_ist"))))
        except Exception as e:
            res.errors.append(f"row {i}: {e}")
    return res
