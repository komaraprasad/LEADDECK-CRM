"""JobHarvest /results (and export columns) -> company + job, plus a person only when a profile link exists."""
from __future__ import annotations
from app.records import NormalizedRecord, MapResult
from app.mappers.base import norm_row, rows_from, pick, parse_dt, profile_urls

TOOL = "jobharvest"


def map_payload(payload: object) -> MapResult:
    res = MapResult()
    try:
        rows = rows_from(payload, ("jobs", "results", "data", "items", "rows"))
    except ValueError as e:
        res.errors.append(str(e))
        return res
    for i, raw in enumerate(rows):
        try:
            if not isinstance(raw, dict):
                raise ValueError("row is not an object")
            r = norm_row(raw)
            cname = pick(r, "company_name", "company")
            curl = pick(r, "company_url", "company_linkedin_url")
            title = pick(r, "title", "job_title")
            if not (cname or curl):
                raise ValueError("no company name or url")
            when = parse_dt(pick(r, "collected_at", "date_collected", "last_seen"))
            res.records.append(NormalizedRecord(TOOL, "company", f"{TOOL}:company:{curl or cname}", raw, {
                "name": cname, "linkedin_url": curl,
                "industry": pick(r, "job_industry", "company_industry"),
                "size": pick(r, "job_size", "company_size"),
            }, when))
            jid = pick(r, "job_id")
            if title or jid:
                res.records.append(NormalizedRecord(TOOL, "job", f"{TOOL}:job:{jid or title + '|' + (cname or '')}", raw, {
                    "title": title, "job_url": pick(r, "job_url", "url"), "location": pick(r, "location"),
                    "posted": pick(r, "posted"), "company_name": cname, "company_url": curl,
                }, when))
            for p in profile_urls(pick(r, "contacts", "contact_profile_link_s", "contact_profile_links")):
                res.records.append(NormalizedRecord(TOOL, "person", f"{TOOL}:{p}", raw, {
                    "linkedin_url": p, "company_name": cname, "company_url": curl,
                    "lead_type": "employer_contact",
                }, when))
        except Exception as e:
            res.errors.append(f"row {i}: {e}")
    return res
