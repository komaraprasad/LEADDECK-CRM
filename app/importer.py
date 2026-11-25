"""Fallback: read a tool's CSV/xlsx export and turn it into NormalizedRecords via that tool's mapper."""
from __future__ import annotations
import csv
import io
from openpyxl import load_workbook
from app.mappers import MAPPERS
from app.mappers.base import norm_key
from app.records import MapResult

MAX_BYTES = 10 * 1024 * 1024
# At least one of these (normalized) headers must exist, else the file is not this tool's export.
REQUIRED_ANY = {
    "talentpulse": {"linkedin_url", "full_name", "name", "linkedin_profile_url", "profile_url"},
    "hiringbadge": {"person_linkedin_url", "person_name", "profile_url", "name"},
    "commentminer": {"author_profile_url", "author_name", "profile_url", "name"},
    "jobharvest": {"company_name", "company_url", "job_title", "title"},
}


def _read_rows(filename: str, content: bytes) -> list[dict]:
    low = filename.lower()
    if low.endswith(".csv"):
        text = content.decode("utf-8-sig", errors="replace")
        return list(csv.DictReader(io.StringIO(text)))
    if low.endswith((".xlsx", ".xlsm")):
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        it = ws.iter_rows(values_only=True)
        header = next(it, None)
        if not header:
            return []
        names = [str(h) if h is not None else "" for h in header]
        return [dict(zip(names, row)) for row in it]
    raise ValueError("unsupported file type; upload .csv or .xlsx")


def parse_export_file(tool: str, filename: str, content: bytes) -> MapResult:
    if tool not in MAPPERS:
        raise ValueError(f"unknown tool: {tool}")
    if len(content) > MAX_BYTES:
        raise ValueError("file too large (limit 10 MB)")
    rows = _read_rows(filename, content)
    headers = {norm_key(k) for r in rows[:1] for k in r}
    if rows and not (headers & REQUIRED_ANY[tool]):
        raise ValueError(f"missing expected columns for {tool}; need one of: {', '.join(sorted(REQUIRED_ANY[tool]))}")
    return MAPPERS[tool](rows)


def import_export_file(db, tool: str, filename: str, content: bytes, dry_run: bool = True) -> dict:
    """Parse an uploaded export and merge it. Recorded in sync_runs with source='import'."""
    import json
    from app.db import now_iso
    from app.merge import upsert_records
    mapped = parse_export_file(tool, filename, content)  # raises ValueError with a clear message
    run_id = db.insert("sync_runs", {"tool": tool, "source": "import", "started": now_iso(), "status": "running", "dry_run": int(dry_run)})
    try:
        res = upsert_records(db, mapped.records, dry_run=dry_run)
    except Exception as e:
        db.update("sync_runs", run_id, {"status": "failed", "finished": now_iso(), "errors": json.dumps([str(e)[:200]])})
        raise
    db.update("sync_runs", run_id, {"status": "ok", "finished": now_iso(), "fetched": len(mapped.records), "created": res.created,
                                    "updated": res.updated, "skipped": res.skipped,
                                    "errors": json.dumps((mapped.errors + res.errors)[:50])})
    return db.one("SELECT * FROM sync_runs WHERE id=?", (run_id,))
