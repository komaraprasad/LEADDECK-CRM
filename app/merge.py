"""De-duplicating upsert of NormalizedRecords into people / companies / jobs / lead_sources.

Rules (spec 7.4): match by normalized LinkedIn URL; a non-empty value is never replaced by an empty one;
newer non-empty wins; every source row is kept; re-running the same input changes nothing.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from app.db import Database, now_iso
from app.normalize import normalize_person_url, normalize_company_url, normalize_domain, normalize_name
from app.records import NormalizedRecord  # re-exported for callers

LEAD_TYPES = ("candidate", "employer_contact", "unknown")


@dataclass
class UpsertResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def _empty(v) -> bool:
    return v is None or v == ""


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _merge(existing: dict, new: dict, ts: str, has_ts: bool) -> dict:
    """Return the column changes to apply to `existing` (never blanks, newer non-empty wins)."""
    changes = {}
    last = existing.get("last_seen") or ""
    for col, val in new.items():
        if _empty(val):
            continue
        cur = existing.get(col)
        if _empty(cur) or (ts >= last and cur != val):
            if cur != val:
                changes[col] = val
    if has_ts and ts > last:
        changes["last_seen"] = ts
    return changes


# ---------------------------------------------------------------- companies
def _resolve_company(db: Database, name, url, website, industry, size, ts, has_ts):
    """Find or create a company. Returns (company_id | None, created, changed)."""
    curl = normalize_company_url(url)
    domain = normalize_domain(website)
    name = name.strip() if isinstance(name, str) and name.strip() else None
    nkey = normalize_name(name)
    if not (curl or domain or nkey):
        return None, False, False
    row = None
    if curl:
        row = db.one("SELECT * FROM companies WHERE linkedin_url=?", (curl,))
    if row is None and domain:
        row = db.one("SELECT * FROM companies WHERE domain=? ORDER BY id LIMIT 1", (domain,))
        if row and curl and row["linkedin_url"] and row["linkedin_url"] != curl:
            row = None  # same site, different LinkedIn page: do not merge
    if row is None and nkey:
        # with a LinkedIn URL, only match a company that has none yet (never one with a different page).
        # Decided here in Python, not by a NULL test on a bare parameter in SQL: Postgres cannot type that parameter.
        url_cond = " AND linkedin_url IS NULL" if curl else ""
        cand = db.one(f"SELECT * FROM companies WHERE name_key=?{url_cond} ORDER BY id LIMIT 1", (nkey,))
        row = cand
    new = {"name": name, "name_key": nkey or None, "linkedin_url": curl, "domain": domain,
           "website": website.strip() if isinstance(website, str) and website.strip() else None,
           "industry": industry, "size": size}
    if row is None:
        cid = db.insert("companies", {**{k: v for k, v in new.items() if not _empty(v)}, "first_seen": ts, "last_seen": ts})
        return cid, True, True
    if not _empty(row.get("name")):  # first-seen company name sticks: it is part of the match key
        new.pop("name", None)
        new.pop("name_key", None)
    changes = _merge(row, new, ts, has_ts)
    db.update("companies", row["id"], changes)
    return row["id"], False, bool(changes)


class Erased(Exception):
    """Person was erased on request; never recreate them."""


# ------------------------------------------------------------------- people
def _find_person(db: Database, url, nkey, company_id, tool):
    if url:
        row = db.one("SELECT * FROM people WHERE linkedin_url=?", (url,))
        if row:
            return row
    if not nkey:
        return None
    cond = "p.company_id IS NULL" if company_id is None else "p.company_id = ?"
    params = [tool, nkey] + ([] if company_id is None else [company_id])
    # Name+company matches are only trusted within one tool, and only for people without a profile URL.
    return db.one(
        "SELECT p.* FROM people p JOIN lead_sources s ON s.person_id = p.id "
        f"WHERE s.tool=? AND p.linkedin_url IS NULL AND p.name_key=? AND {cond} ORDER BY p.id LIMIT 1", params)


def _upsert_person(db, rec: NormalizedRecord, ts, has_ts):
    f = rec.fields
    url = normalize_person_url(f.get("linkedin_url"))
    name = f.get("full_name")
    nkey = normalize_name(name)
    if not (url or nkey):
        raise ValueError("person has neither a profile URL nor a name")
    if url and db.one("SELECT 1 AS x FROM erasures WHERE linkedin_url=?", (url,)):
        raise Erased(url)
    company_id, c_created, c_changed = _resolve_company(
        db, f.get("company_name"), f.get("company_url"), f.get("company_website"),
        f.get("company_industry"), f.get("company_size"), ts, has_ts)
    email = f.get("email").strip().lower() if isinstance(f.get("email"), str) and f.get("email").strip() else None
    new = {"full_name": name, "name_key": nkey or None, "headline": f.get("headline"), "title": f.get("title"),
           "location": f.get("location"), "linkedin_url": url, "email": email, "phone": f.get("phone"),
           "company_id": company_id}
    row = _find_person(db, url, nkey, company_id, rec.tool)
    lead_type = f.get("lead_type") if f.get("lead_type") in LEAD_TYPES else "unknown"
    if row is None:
        data = {k: v for k, v in new.items() if not _empty(v)}
        data.update(lead_type=lead_type, match_confidence="high" if url else "low", first_seen=ts, last_seen=ts)
        pid = db.insert("people", data)
        return pid, True, True
    changes = _merge(row, new, ts, has_ts)
    if row["lead_type"] == "unknown" and lead_type != "unknown":
        changes["lead_type"] = lead_type  # first specific type sticks; later disagreements keep the source row only
    if url and row.get("match_confidence") == "low":
        changes["match_confidence"] = "high"
    db.update("people", row["id"], changes)
    return row["id"], False, bool(changes) or c_changed


# --------------------------------------------------------------------- jobs
def _upsert_job(db, rec: NormalizedRecord, ts, has_ts):
    f = rec.fields
    company_id, _, _ = _resolve_company(db, f.get("company_name"), f.get("company_url"), None, None, None, ts, has_ts)
    new = {"company_id": company_id, "title": f.get("title"), "job_url": f.get("job_url"),
           "location": f.get("location"), "posted": f.get("posted")}
    row = db.one("SELECT * FROM job_postings WHERE tool=? AND source_key=?", (rec.tool, rec.source_key))
    if row is None:
        jid = db.insert("job_postings", {**{k: v for k, v in new.items() if not _empty(v)},
                                         "tool": rec.tool, "source_key": rec.source_key, "first_seen": ts, "last_seen": ts})
        return jid, True, True
    changes = _merge(row, new, ts, has_ts)
    db.update("job_postings", row["id"], changes)
    return row["id"], False, bool(changes)


# ------------------------------------------------------------------ sources
def _upsert_source(db, rec: NormalizedRecord, person_id, company_id, job_id, ts_collected, synced):
    raw = json.dumps(rec.raw, sort_keys=True, default=str, ensure_ascii=False)
    row = db.one("SELECT * FROM lead_sources WHERE tool=? AND kind=? AND source_key=?", (rec.tool, rec.kind, rec.source_key))
    if row is None:
        db.insert("lead_sources", {"tool": rec.tool, "kind": rec.kind, "source_key": rec.source_key, "person_id": person_id,
                                   "company_id": company_id, "job_id": job_id, "raw_payload": raw,
                                   "collected_at": ts_collected, "synced_at": synced})
        return True
    changed = (row["raw_payload"] != raw or row["person_id"] != person_id or row["company_id"] != company_id or row["job_id"] != job_id)
    vals = {"synced_at": synced}
    if changed:
        vals.update(raw_payload=raw, person_id=person_id, company_id=company_id, job_id=job_id, collected_at=ts_collected)
    db.update("lead_sources", row["id"], vals)
    return changed


# -------------------------------------------------------------------- public
def upsert_records(db: Database, records: list[NormalizedRecord], dry_run: bool = False) -> UpsertResult:
    res = UpsertResult()
    synced = now_iso()
    with db.transaction(rollback=dry_run):
        for i, rec in enumerate(records):
            try:
                with db.savepoint():
                    has_ts = rec.collected_at is not None
                    ts = min(_iso(rec.collected_at) or synced, synced)  # a tool clock ahead of ours must not date a lead in the future
                    if rec.kind == "person":
                        pid, created, changed = _upsert_person(db, rec, ts, has_ts)
                        src = _upsert_source(db, rec, pid, None, None, _iso(rec.collected_at), synced)
                    elif rec.kind == "company":
                        f = rec.fields
                        cid, created, changed = _resolve_company(db, f.get("name"), f.get("linkedin_url"), f.get("website"),
                                                                  f.get("industry"), f.get("size"), ts, has_ts)
                        if cid is None:
                            raise ValueError("company has no name, URL or domain")
                        src = _upsert_source(db, rec, None, cid, None, _iso(rec.collected_at), synced)
                    elif rec.kind == "job":
                        jid, created, changed = _upsert_job(db, rec, ts, has_ts)
                        src = _upsert_source(db, rec, None, None, jid, _iso(rec.collected_at), synced)
                    else:
                        raise ValueError(f"unknown record kind {rec.kind}")
                if created:
                    res.created += 1
                elif changed or src:
                    res.updated += 1
                else:
                    res.skipped += 1
            except Erased:
                res.skipped += 1
            except Exception as e:
                res.errors.append(f"record {i} ({rec.tool}/{rec.kind}): {e}")
    return res
