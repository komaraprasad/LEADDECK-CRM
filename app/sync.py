"""Pull leads from the four tools' existing read endpoints and merge them. Read-only GETs; tools are never modified."""
from __future__ import annotations
import json
import threading
from datetime import datetime, timezone, timedelta
from app.config import Settings, TOOLS
from app.db import Database, now_iso
from app.mappers import MAPPERS
from app.mappers.base import rows_from
from app.merge import upsert_records

# Endpoints verified against each tool's source code (reference/). JobHarvest's /results is an HTML page;
# its JSON lives under /api/ext. Hiring Badge Finder pages its results (max 500 per page);
# TalentPulse caps at 250 unless ?limit= is given (max 5000).
TOOL_PATHS = {
    "talentpulse": "/api/leads?limit=5000",
    "hiringbadge": "/api/leads",
    "commentminer": "/api/posts/combined",
    "jobharvest": "/api/ext/results",
}
HBF_PAGE_SIZE = 500
HBF_MAX_PAGES = 100
TIMEOUT = 90          # free-tier hosts take about a minute to wake
STALE_AFTER = timedelta(minutes=15)
_locks = {t: threading.Lock() for t in TOOLS}


class SyncBusy(Exception):
    pass


class SyncError(Exception):
    pass


def default_http_get(url: str, timeout: int = TIMEOUT):
    import requests
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "LeadDeck-Sync/1.0", "Accept": "application/json"})
    return r.status_code, r.text


def _get_json(url: str, http_get, retries: int = 1):
    last = "unknown error"
    for _ in range(retries + 1):
        try:
            status, text = http_get(url, TIMEOUT)
        except Exception as e:  # timeout, DNS, connection refused
            last = f"{type(e).__name__}: {str(e)[:150]}"
            continue
        if status in (502, 503, 504):
            last = f"HTTP {status} (tool may be waking up)"
            continue
        if status != 200:
            raise SyncError(f"HTTP {status} from {url.split('?')[0]}")
        try:
            return json.loads(text)
        except ValueError:
            raise SyncError("non-JSON response (the tool is asleep or returned an error page)")
    raise SyncError(last)


def fetch_tool_payload(tool: str, base_url: str, http_get=default_http_get, retries: int = 1):
    base = base_url.rstrip("/")
    if tool != "hiringbadge":
        return _get_json(base + TOOL_PATHS[tool], http_get, retries)
    items, total = [], None
    for page in range(1, HBF_MAX_PAGES + 1):
        data = _get_json(f"{base}{TOOL_PATHS[tool]}?page={page}&page_size={HBF_PAGE_SIZE}", http_get, retries)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise SyncError("unexpected Hiring Badge Finder response shape")
        total = data.get("total", total)
        items.extend(data["items"])
        if not data["items"] or len(data["items"]) < HBF_PAGE_SIZE or (isinstance(total, int) and len(items) >= total):
            break
    return {"items": items, "total": total if isinstance(total, int) else len(items)}


def _count_warning(payload, fetched_rows: int) -> list[str]:
    """If the tool states how many records it holds and we read fewer, say so (never silently truncate)."""
    if isinstance(payload, dict):
        for key in ("total", "total_comments"):
            n = payload.get(key)
            if isinstance(n, int) and n > fetched_rows:
                return [f"warning: the tool reports {n} records but only {fetched_rows} were read"]
    return []


def mark_stale_runs(db: Database) -> None:
    db.execute("UPDATE sync_runs SET status='failed', finished=?, errors=? WHERE status='running'",
               (now_iso(), json.dumps(["interrupted: server restarted"])))


def _has_live_success(db: Database, tool: str) -> bool:
    return db.one("SELECT 1 AS x FROM sync_runs WHERE tool=? AND status='ok' AND dry_run=0 LIMIT 1", (tool,)) is not None


def _db_running(db: Database, tool: str) -> bool:
    cutoff = (datetime.now(timezone.utc) - STALE_AFTER).isoformat(timespec="seconds")
    return db.one("SELECT 1 AS x FROM sync_runs WHERE tool=? AND status='running' AND started > ?", (tool, cutoff)) is not None


def _finish(db, run_id, status, fetched=0, res=None, errors=None):
    errs = list(errors or []) + (res.errors if res else [])
    db.update("sync_runs", run_id, {
        "status": status, "finished": now_iso(), "fetched": fetched,
        "created": res.created if res else 0, "updated": res.updated if res else 0,
        "skipped": res.skipped if res else 0, "errors": json.dumps(errs[:50])})
    return db.one("SELECT * FROM sync_runs WHERE id=?", (run_id,))


def run_sync(db: Database, settings: Settings, tool: str, dry_run: bool | None = None, http_get=default_http_get) -> dict:
    """dry_run=None means: dry run if this tool has never had a successful live sync (safe default)."""
    if tool not in MAPPERS:
        raise KeyError(tool)
    lock = _locks[tool]
    if not lock.acquire(blocking=False):
        raise SyncBusy(tool)
    try:
        if _db_running(db, tool):
            raise SyncBusy(tool)
        if dry_run is None:
            dry_run = not _has_live_success(db, tool)
        run_id = db.insert("sync_runs", {"tool": tool, "source": "pull", "started": now_iso(), "status": "running", "dry_run": int(dry_run)})
        try:
            payload = fetch_tool_payload(tool, settings.tool_urls[tool], http_get)
        except SyncError as e:
            return _finish(db, run_id, "failed", errors=[str(e)])
        except Exception as e:
            return _finish(db, run_id, "failed", errors=[f"{type(e).__name__}: {str(e)[:200]}"])
        try:
            mapped = MAPPERS[tool](payload)
            if not mapped.records and mapped.errors:
                return _finish(db, run_id, "failed", errors=mapped.errors)
            res = upsert_records(db, mapped.records, dry_run=dry_run)
            try:
                raw_rows = len(rows_from(payload, ("items", "leads", "comments", "rows", "jobs", "results", "data")))
            except ValueError:
                raw_rows = len(mapped.records)
            return _finish(db, run_id, "ok", fetched=len(mapped.records), res=res,
                           errors=_count_warning(payload, raw_rows) + mapped.errors)
        except Exception as e:
            return _finish(db, run_id, "failed", errors=[f"{type(e).__name__}: {str(e)[:200]}"])
    finally:
        lock.release()


def run_all(db: Database, settings: Settings, dry_run: bool | None = None, http_get=default_http_get) -> list[dict]:
    out = []
    for tool in TOOLS:
        try:
            out.append(run_sync(db, settings, tool, dry_run, http_get))
        except SyncBusy:
            out.append({"tool": tool, "status": "busy"})
    return out
