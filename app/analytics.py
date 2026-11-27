"""Analytics (Phase 5): read-only numbers computed from data LeadDeck already stores. No HTTP in here.

Every figure is derived from existing tables (people, contact_points, enrichment_items, credit_ledger,
crm_leads, lead_events, tasks, lead_sources). Nothing here writes to the database.
Outreach numbers (sent / replies / bounces / unsubscribes) come from `lead_events` and stay empty until
Phase 4 (or the /api/crm/events intake) records them; `outreach.connected` says whether any exist.
"""
from __future__ import annotations
import json
import re
from datetime import date, datetime, timedelta, timezone
from app import crm
from app.config import TOOL_LABELS
from app.db import Database
from app.leads import SCORE_SQL, _FROM, _VALID_EMAIL

RANGES = (7, 30, 90)
DEFAULT_DAYS = 30
OUTREACH_TYPES = ("email_sent", "reply", "bounce", "unsubscribe")


def clean_days(v) -> int:
    try:
        d = int(v)
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return d if d in RANGES else DEFAULT_DAYS


def _pct(a, b):
    return round(100.0 * a / b, 1) if b else None


def _n(db: Database, sql: str, params=()) -> int:
    r = db.one(sql, params)
    return int((r or {}).get("n") or 0)


def _day_series(rows: list[dict], days: int, today, keys: tuple[str, ...]) -> list[dict]:
    """One entry per calendar day (oldest first, gaps filled with zeros)."""
    by = {r["d"]: r for r in rows}
    out = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        r = by.get(d, {})
        out.append({"date": d, **{k: int(r.get(k) or 0) for k in keys}})
    return out


def _funnel(db: Database) -> list[dict]:
    current = {r["stage"]: r["n"] for r in db.query(f"SELECT COALESCE(cl.stage,'new') AS stage, COUNT(*) AS n {_FROM} GROUP BY COALESCE(cl.stage,'new')")}
    reached: dict[str, set] = {s: set() for s in crm.STAGES}
    for e in db.query("SELECT person_id, detail FROM lead_events WHERE type='stage_change' AND person_id IS NOT NULL"):
        try:
            to = (json.loads(e["detail"] or "{}") or {}).get("to")
        except ValueError:
            continue
        if to in reached:
            reached[to].add(e["person_id"])
    for r in db.query("SELECT person_id, stage FROM crm_leads"):
        reached[r["stage"]].add(r["person_id"])
    total = sum(current.values())
    out, prev = [], None
    for s in crm.PIPELINE:
        # a lead that reached a later stage also passed the earlier ones, so "reached" is cumulative along the pipeline
        later = set().union(*[reached[x] for x in crm.PIPELINE[crm.PIPELINE.index(s):]])
        if s == "meeting":
            later |= reached["won"]
        n = len(later) if s != "new" else total
        out.append({"stage": s, "label": crm.LABELS[s], "current": current.get(s, 0), "reached": n,
                    "of_previous": _pct(n, prev) if prev else None})
        prev = n
    for s in ("won", "lost", "not_a_fit"):
        out.append({"stage": s, "label": crm.LABELS[s], "current": current.get(s, 0), "reached": len(reached[s]), "of_previous": None, "terminal": True})
    return out


def _stale(db: Database) -> int:
    ph = ",".join("?" * len(crm.STALE_DAYS))
    rows = db.query(f"SELECT cl.stage, cl.stage_changed_at, (SELECT MAX(e.created_at) FROM lead_events e WHERE e.person_id=cl.person_id) AS last_event "
                    f"FROM crm_leads cl WHERE cl.stage IN ({ph})", tuple(crm.STALE_DAYS))
    n = 0
    for r in rows:
        idle = crm._days_since(r["last_event"] or r["stage_changed_at"])
        if idle is not None and idle >= crm.STALE_DAYS[r["stage"]]:
            n += 1
    return n


def _period(db: Database, start: str, end: str) -> dict:
    """In-period totals for [start, end) so the current window can be compared with the one before it."""
    rng = (start, end)
    items = db.query("SELECT status, COUNT(*) AS n, COALESCE(SUM(credits_used),0) AS credits FROM enrichment_items "
                     "WHERE created_at >= ? AND created_at < ? GROUP BY status", rng)
    looked = sum(int(r["n"]) for r in items)
    matched = sum(int(r["n"]) for r in items if r["status"] == "matched")
    credits = sum(int(r["credits"]) for r in items)
    valid = _n(db, "SELECT COUNT(DISTINCT person_id) AS n FROM contact_points WHERE kind='email' AND verification='valid' AND found_at >= ? AND found_at < ?", rng)
    meetings = len([1 for r in db.query("SELECT DISTINCT person_id, detail FROM lead_events WHERE type='stage_change' AND created_at >= ? AND created_at < ?", rng)
                    if '"to": "meeting"' in (r["detail"] or "")])
    return {
        "leads_added": _n(db, "SELECT COUNT(*) AS n FROM people WHERE first_seen >= ? AND first_seen < ?", rng),
        "lookups": looked, "credits": credits, "valid_emails_found": valid, "match_rate": _pct(matched, looked),
        "credits_per_valid_email": round(credits / valid, 2) if valid else None,
        "crm_events": _n(db, "SELECT COUNT(*) AS n FROM lead_events WHERE created_at >= ? AND created_at < ?", rng),
        "meetings_booked": meetings,
        "tasks_done": _n(db, "SELECT COUNT(*) AS n FROM tasks WHERE done_at >= ? AND done_at < ?", rng),
    }


def overview(db: Database, days: int = DEFAULT_DAYS, now: datetime | None = None, local_today: str | None = None) -> dict:
    days = clean_days(days)
    now = now or datetime.now(timezone.utc)
    today = now.date()
    since = (now - timedelta(days=days)).isoformat(timespec="seconds")
    day_cut = (today - timedelta(days=days - 1)).isoformat()

    # ---- leads and contact coverage
    total = _n(db, "SELECT COUNT(*) AS n FROM people")
    leads = {
        "total": total,
        "in_campaigns": _n(db, "SELECT COUNT(DISTINCT person_id) AS n FROM campaign_leads"),
        "valid_email": _n(db, f"SELECT COUNT(*) AS n FROM people p WHERE {_VALID_EMAIL}"),
        "with_phone": _n(db, "SELECT COUNT(*) AS n FROM people WHERE COALESCE(phone,'') <> ''"),
        "do_not_contact": _n(db, "SELECT COUNT(*) AS n FROM people WHERE do_not_contact=1"),
        "added_in_period": _n(db, "SELECT COUNT(*) AS n FROM people WHERE first_seen >= ?", (since,)),
    }
    leads["valid_email_rate"] = _pct(leads["valid_email"], total)
    leads["phone_rate"] = _pct(leads["with_phone"], total)

    q = db.one(f"SELECT SUM(CASE WHEN s >= 50 THEN 1 ELSE 0 END) AS high, SUM(CASE WHEN s >= 20 AND s < 50 THEN 1 ELSE 0 END) AS medium, "
               f"SUM(CASE WHEN s < 20 THEN 1 ELSE 0 END) AS low FROM (SELECT {SCORE_SQL} AS s {_FROM}) t") or {}
    quality = {k: int(q.get(k) or 0) for k in ("high", "medium", "low")}

    # ---- enrichment (credits are LeadDeck's own count, not the provider's dashboard)
    items = {r["status"]: r for r in db.query("SELECT status, COUNT(*) AS n, COALESCE(SUM(credits_used),0) AS credits FROM enrichment_items WHERE created_at >= ? GROUP BY status", (since,))}
    looked = sum(r["n"] for r in items.values())
    matched = items.get("matched", {}).get("n", 0)
    credits = sum(int(r["credits"]) for r in items.values())
    valid_found = _n(db, "SELECT COUNT(DISTINCT person_id) AS n FROM contact_points WHERE kind='email' AND verification='valid' AND found_at >= ?", (since,))
    credits_all = _n(db, "SELECT COALESCE(SUM(credits_used),0) AS n FROM enrichment_items")
    enrichment = {
        "lookups": looked, "matched": matched, "not_found": items.get("not_found", {}).get("n", 0),
        "errors": items.get("error", {}).get("n", 0), "wrong_person": items.get("mismatch", {}).get("n", 0), "credits": credits, "credits_all_time": credits_all,
        "valid_emails_found": valid_found, "match_rate": _pct(matched, looked),
        "credits_per_valid_email": round(credits / valid_found, 2) if valid_found else None,
        "by_day": _day_series(db.query("SELECT substr(created_at,1,10) AS d, COALESCE(SUM(credits_used),0) AS credits, COUNT(*) AS lookups FROM enrichment_items "
                                       "WHERE substr(created_at,1,10) >= ? GROUP BY substr(created_at,1,10)", (day_cut,)), days, today, ("credits", "lookups")),
    }

    # ---- CRM activity, tasks, owners
    ev = {r["type"]: r["n"] for r in db.query("SELECT type, COUNT(*) AS n FROM lead_events WHERE created_at >= ? GROUP BY type", (since,))}
    today_s = today.isoformat()
    try:   # "overdue" uses the viewer's own date, the same rule as the Tasks page
        if local_today:
            today_s = date.fromisoformat(local_today).isoformat()
    except ValueError:
        pass
    tasks = {
        "open": _n(db, "SELECT COUNT(*) AS n FROM tasks WHERE done_at IS NULL"),
        "overdue": _n(db, "SELECT COUNT(*) AS n FROM tasks WHERE done_at IS NULL AND due_at IS NOT NULL AND substr(due_at,1,10) < ?", (today_s,)),
        "done_in_period": _n(db, "SELECT COUNT(*) AS n FROM tasks WHERE done_at >= ?", (since,)),
    }
    owners = db.query("SELECT COALESCE(cl.owner_email,'') AS owner, COUNT(*) AS leads FROM crm_leads cl GROUP BY COALESCE(cl.owner_email,'') ORDER BY leads DESC")
    open_by = {r["o"]: r["n"] for r in db.query("SELECT COALESCE(owner_email,'') AS o, COUNT(*) AS n FROM tasks WHERE done_at IS NULL GROUP BY COALESCE(owner_email,'')")}
    meetings = len([1 for r in db.query("SELECT DISTINCT person_id, detail FROM lead_events WHERE type='stage_change' AND created_at >= ?", (since,))
                    if '"to": "meeting"' in (r["detail"] or "")])
    crm_block = {
        "events_in_period": sum(ev.values()), "stage_changes": ev.get("stage_change", 0), "notes": ev.get("note", 0),
        "meetings_booked": meetings, "stale": _stale(db),
        "owners": [{"owner": o["owner"] or None, "leads": o["leads"], "open_tasks": open_by.get(o["owner"], 0)} for o in owners],
        "activity_by_day": _day_series(db.query("SELECT substr(created_at,1,10) AS d, COUNT(*) AS events FROM lead_events WHERE substr(created_at,1,10) >= ? "
                                                "GROUP BY substr(created_at,1,10)", (day_cut,)), days, today, ("events",)),
    }

    # ---- outreach (empty until emails are recorded)
    sent_all = _n(db, "SELECT COUNT(*) AS n FROM lead_events WHERE type='email_sent'")
    o = {t: ev.get(t, 0) for t in OUTREACH_TYPES}
    outreach = {
        "connected": sent_all > 0, "sent": o["email_sent"], "replies": o["reply"], "bounces": o["bounce"], "unsubscribes": o["unsubscribe"],
        "reply_rate": _pct(o["reply"], o["email_sent"]), "bounce_rate": _pct(o["bounce"], o["email_sent"]),
        "unsubscribe_rate": _pct(o["unsubscribe"], o["email_sent"]),
        "by_day": _day_series(db.query("SELECT substr(created_at,1,10) AS d, "
                                       "SUM(CASE WHEN type='email_sent' THEN 1 ELSE 0 END) AS sent, SUM(CASE WHEN type='reply' THEN 1 ELSE 0 END) AS replies "
                                       "FROM lead_events WHERE type IN ('email_sent','reply') AND substr(created_at,1,10) >= ? GROUP BY substr(created_at,1,10)", (day_cut,)),
                              days, today, ("sent", "replies")),
    }

    # ---- sources: which of the four tools produces reachable leads
    src = db.query(f"SELECT s.tool AS tool, COUNT(*) AS leads, SUM(CASE WHEN {_VALID_EMAIL} THEN 1 ELSE 0 END) AS valid_email, "
                   f"SUM(CASE WHEN COALESCE(p.phone,'') <> '' THEN 1 ELSE 0 END) AS with_phone, AVG({SCORE_SQL}) AS avg_score "
                   f"FROM (SELECT DISTINCT tool, person_id FROM lead_sources WHERE person_id IS NOT NULL) s "
                   f"JOIN people p ON p.id = s.person_id LEFT JOIN companies c ON c.id = p.company_id LEFT JOIN crm_leads cl ON cl.person_id = p.id "
                   f"GROUP BY s.tool ORDER BY leads DESC")
    sources = [{"tool": r["tool"], "label": TOOL_LABELS.get(r["tool"], r["tool"]), "leads": r["leads"], "valid_email": int(r["valid_email"] or 0),
                "with_phone": int(r["with_phone"] or 0), "valid_email_rate": _pct(int(r["valid_email"] or 0), r["leads"]),
                "avg_score": round(float(r["avg_score"] or 0), 1)} for r in src]

    # ---- the same window, one period earlier, for "vs previous period" comparisons
    end = (now + timedelta(seconds=1)).isoformat(timespec="seconds")
    prev_since = (now - timedelta(days=2 * days)).isoformat(timespec="seconds")
    compare = {"current": _period(db, since, end), "previous": _period(db, prev_since, since)}
    leads["by_day"] = _day_series(db.query("SELECT substr(first_seen,1,10) AS d, COUNT(*) AS added FROM people WHERE substr(first_seen,1,10) >= ? "
                                           "GROUP BY substr(first_seen,1,10)", (day_cut,)), days, today, ("added",))

    extra = _breakdowns(db)
    leads["has_email"], leads["has_phone"] = extra.pop("has_email"), extra.pop("has_phone")
    leads["has_email_rate"], leads["has_phone_rate"] = _pct(leads["has_email"], total), _pct(leads["has_phone"], total)
    per = extra.pop("per_tool")
    for s in sources:
        s.update(per.get(s["tool"], {}))
        s["has_email_rate"] = _pct(s.get("has_email", 0), s["leads"])
        s["enriched_rate"] = _pct(s.get("enriched", 0), s["leads"])
    return {**extra, "days": days, "compare": compare, "generated_at": now.isoformat(timespec="seconds"), "leads": leads, "quality": quality, "funnel": _funnel(db),
            "enrichment": enrichment, "crm": crm_block, "tasks": tasks, "outreach": outreach, "sources": sources}


_HAS_EMAIL = ("(COALESCE(p.email,'') <> '' OR EXISTS (SELECT 1 FROM contact_points x WHERE x.person_id = p.id AND x.kind='email'))")
_HAS_PHONE = ("(COALESCE(p.phone,'') <> '' OR EXISTS (SELECT 1 FROM contact_points x WHERE x.person_id = p.id AND x.kind='phone'))")
_ENRICHED = "EXISTS (SELECT 1 FROM enrichment_items i WHERE i.person_id = p.id AND i.status='matched')"


_CITY_ALIASES = {"Bangalore": "Bengaluru", "Bombay": "Mumbai", "Gurgaon": "Gurugram", "New Delhi": "Delhi"}


def _city(loc: str) -> str:
    """'Greater Hyderabad Area' and 'Hyderabad, Telangana, India' both count as Hyderabad."""
    c = re.sub(r"^greater\s+|\s+(metropolitan\s+)?(area|region)$", "", (loc or "").split(",")[0].strip(), flags=re.I).strip().title()
    return _CITY_ALIASES.get(c, c)


def _breakdowns(db: Database) -> dict:
    """Any-source contact coverage, per-tool facts, lead types and top companies / locations."""
    out = {
        "has_email": _n(db, f"SELECT COUNT(*) AS n FROM people p WHERE {_HAS_EMAIL}"),
        "has_phone": _n(db, f"SELECT COUNT(*) AS n FROM people p WHERE {_HAS_PHONE}"),
        "lead_types": [{"type": r["t"], "leads": int(r["n"])} for r in db.query(
            "SELECT COALESCE(NULLIF(lead_type,''),'unknown') AS t, COUNT(*) AS n FROM people GROUP BY COALESCE(NULLIF(lead_type,''),'unknown') ORDER BY n DESC")],
        "top_companies": [{"name": r["name"], "leads": int(r["n"])} for r in db.query(
            "SELECT c.name AS name, COUNT(*) AS n FROM people p JOIN companies c ON c.id = p.company_id "
            "WHERE COALESCE(c.name,'') <> '' GROUP BY c.id, c.name ORDER BY n DESC, c.name LIMIT 5")],
    }
    cities: dict[str, int] = {}
    for r in db.query("SELECT location FROM people WHERE COALESCE(location,'') <> ''"):
        c = _city(r["location"])
        if c:
            cities[c] = cities.get(c, 0) + 1
    out["top_locations"] = [{"name": k, "leads": v} for k, v in sorted(cities.items(), key=lambda kv: (-kv[1], kv[0]))[:5]]

    per: dict[str, dict] = {}
    for r in db.query(f"SELECT s.tool AS tool, SUM(CASE WHEN {_HAS_EMAIL} THEN 1 ELSE 0 END) AS e, "
                      f"SUM(CASE WHEN {_HAS_PHONE} THEN 1 ELSE 0 END) AS ph, SUM(CASE WHEN {_ENRICHED} THEN 1 ELSE 0 END) AS en "
                      f"FROM (SELECT DISTINCT tool, person_id FROM lead_sources WHERE person_id IS NOT NULL) s JOIN people p ON p.id = s.person_id GROUP BY s.tool"):
        per[r["tool"]] = {"has_email": int(r["e"] or 0), "has_phone": int(r["ph"] or 0), "enriched": int(r["en"] or 0)}

    # per tool: last sync and the tool's own counts
    tools = []
    for tool, label in TOOL_LABELS.items():
        last = db.one("SELECT finished, status, fetched, created, updated FROM sync_runs WHERE tool=? AND dry_run=0 AND status <> 'running' "
                      "ORDER BY id DESC LIMIT 1", (tool,)) or {}
        t = {"tool": tool, "label": label, "last_sync": last.get("finished"), "last_status": last.get("status"),
             "fetched": int(last.get("fetched") or 0), "leads": _n(db, "SELECT COUNT(DISTINCT person_id) AS n FROM lead_sources WHERE tool=? AND person_id IS NOT NULL", (tool,)),
             "facts": []}
        if tool == "jobharvest":
            jobs = _n(db, "SELECT COUNT(*) AS n FROM job_postings WHERE tool=?", (tool,))
            t["facts"] = [["Jobs", jobs], ["Companies hiring", _n(db, "SELECT COUNT(DISTINCT company_id) AS n FROM job_postings WHERE tool=? AND company_id IS NOT NULL", (tool,))]]
        elif tool in ("commentminer", "talentpulse"):
            comments, posts, otw = 0, set(), 0
            for r in db.query("SELECT raw_payload FROM lead_sources WHERE tool=? AND person_id IS NOT NULL", (tool,)):
                try:
                    raw = json.loads(r["raw_payload"] or "{}") or {}
                except ValueError:
                    continue
                if tool == "commentminer":
                    cs = raw.get("_comments") or [raw]
                    comments += len(cs)
                    posts.update(c.get("post_url") or c.get("post_title") for c in cs if isinstance(c, dict) and (c.get("post_url") or c.get("post_title")))
                elif str(raw.get("otw_status") or raw.get("is_open_to_work") or "").strip().lower() in ("yes", "true", "1"):
                    otw += 1
            t["facts"] = [["Comments", comments], ["Posts", len(posts)]] if tool == "commentminer" else [["Open to work", otw]]
        else:
            t["facts"] = [["Companies", _n(db, "SELECT COUNT(DISTINCT p.company_id) AS n FROM lead_sources s JOIN people p ON p.id=s.person_id WHERE s.tool=?", (tool,))]]
        tools.append(t)
    out["tools"], out["per_tool"] = tools, per
    return out
