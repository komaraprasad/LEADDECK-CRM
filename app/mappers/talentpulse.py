"""TalentPulse GET /api/leads -> candidate people (fields read from reference/talentpulse-main)."""
from __future__ import annotations
from datetime import timezone
from app.records import NormalizedRecord, MapResult
from app.mappers.base import norm_row, rows_from, pick, parse_dt

TOOL = "talentpulse"


def map_payload(payload: object) -> MapResult:
    res = MapResult()
    try:
        rows = rows_from(payload, ("leads", "data", "items", "results"))
    except ValueError as e:
        res.errors.append(str(e))
        return res
    for i, raw in enumerate(rows):
        try:
            if not isinstance(raw, dict):
                raise ValueError("row is not an object")
            r = norm_row(raw)
            first, last = pick(r, "first_name"), pick(r, "last_name")
            name = pick(r, "full_name", "name") or (" ".join(x for x in (first, last) if x) or None)
            url = pick(r, "linkedin_url", "linkedin_profile_url", "profile_url", "person_linkedin_url")
            if not (name or url):
                raise ValueError("no name or profile url")
            fields = {
                "full_name": name, "headline": pick(r, "headline"),
                "title": pick(r, "current_title", "title", "current_designation"),
                "location": pick(r, "location"), "linkedin_url": url,
                "email": pick(r, "email"), "phone": pick(r, "phone"),
                "company_name": pick(r, "company_name", "company"),
                "lead_type": "candidate",
            }
            key = pick(r, "id", "lead_id") or url or f"{name}|{fields['company_name']}"
            res.records.append(NormalizedRecord(TOOL, "person", f"{TOOL}:{key}", raw, fields,
                                                parse_dt(pick(r, "first_found_at", "created_at"), timezone.utc)))   # TalentPulse stores UTC (its own export shows them +5:30)
        except Exception as e:  # skip bad row, keep going
            res.errors.append(f"row {i}: {e}")
    return res
