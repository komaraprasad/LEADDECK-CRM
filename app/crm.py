"""CRM rules: stages, readiness, notes, tasks, timeline, board, event intake. No HTTP in here."""
from __future__ import annotations
import json
from datetime import date, datetime, timezone
from app.db import Database, now_iso

STAGES = ["new", "enriched", "ready", "contacted", "replied", "interested", "meeting", "won", "lost", "not_a_fit"]
LABELS = {"new": "New", "enriched": "Enriched", "ready": "Ready to contact", "contacted": "Contacted", "replied": "Replied",
          "interested": "Interested", "meeting": "Meeting booked", "won": "Won", "lost": "Lost", "not_a_fit": "Not a fit"}
# Days without any activity before a lead is flagged as stale on the board (Pipedrive-style "rotting"); only stages where someone should be acting.
STALE_DAYS = {"ready": 7, "contacted": 7, "replied": 2, "interested": 7, "meeting": 7}
PIPELINE = STAGES[:7]                      # forward-moving part; won/lost/not_a_fit are terminal
CONTACTED_PLUS = {"contacted", "replied", "interested", "meeting", "won"}
NEEDS_REASON = {"lost", "not_a_fit"}
EVENT_TYPES = {"email_sent", "reply", "bounce", "unsubscribe"}


def _event(db: Database, pid, type_: str, detail: dict | None, actor: str) -> None:
    db.insert("lead_events", {"person_id": pid, "type": type_, "detail": json.dumps(detail or {}, ensure_ascii=False, default=str),
                              "actor": actor, "created_at": now_iso()})


def _person(db: Database, pid: int) -> dict:
    p = db.one("SELECT id, do_not_contact, email FROM people WHERE id=?", (pid,))
    if not p:
        raise KeyError(pid)
    return p


def ensure_lead(db: Database, pid: int, actor: str = "system") -> bool:
    """Create the CRM row (stage New) if missing. Returns True when created."""
    _person(db, pid)
    if db.one("SELECT 1 AS x FROM crm_leads WHERE person_id=?", (pid,)):
        return False
    ts = now_iso()
    db.insert("crm_leads", {"person_id": pid, "stage": "new", "stage_changed_at": ts, "created_at": ts})
    _event(db, pid, "stage_change", {"from": None, "to": "new", "auto": True}, actor)
    return True


def peek_crm(db: Database, pid: int) -> dict:
    """Read the CRM row without creating one (previewing a lead must not add it to the pipeline)."""
    return db.one("SELECT * FROM crm_leads WHERE person_id=?", (pid,)) or {"person_id": pid, "stage": "new", "owner_email": None, "lost_reason": None, "next_action_at": None, "stage_changed_at": None, "in_pipeline": False}


def get_crm(db: Database, pid: int) -> dict:
    ensure_lead(db, pid)
    return db.one("SELECT * FROM crm_leads WHERE person_id=?", (pid,))


def set_stage(db: Database, pid: int, stage: str, actor: str, reason: str | None = None, only_forward: bool = False) -> bool:
    """Change a lead's stage. Returns False when nothing changed. Raises ValueError for refused moves."""
    if stage not in STAGES:
        raise ValueError("unknown stage")
    cur = get_crm(db, pid)
    if cur["stage"] == stage:
        return False
    person = _person(db, pid)
    if only_forward:  # automation: never backwards, never out of a terminal stage
        if cur["stage"] not in PIPELINE or stage not in PIPELINE or PIPELINE.index(stage) <= PIPELINE.index(cur["stage"]):
            return False
    if stage in CONTACTED_PLUS and person["do_not_contact"]:
        raise ValueError("this person is marked do not contact")
    reason = (reason or "").strip() or None
    if stage in NEEDS_REASON and not reason:
        raise ValueError("a reason is required for %s" % LABELS[stage])
    db.update("crm_leads", cur["id"], {"stage": stage, "stage_changed_at": now_iso(), "lost_reason": reason if stage in NEEDS_REASON else None})
    _event(db, pid, "stage_change", {"from": cur["stage"], "to": stage, "reason": reason, "auto": only_forward}, actor)
    return True


def refresh_readiness(db: Database, pid: int) -> str:
    """Automatic, forward-only moves up to Ready; the one safety demotion is Ready -> Enriched."""
    cur = get_crm(db, pid)
    person = _person(db, pid)
    has_valid = bool(db.one("SELECT 1 AS x FROM contact_points WHERE person_id=? AND kind='email' AND verification='valid'", (pid,)))
    last = db.one("SELECT status FROM enrichment_items WHERE person_id=? ORDER BY id DESC LIMIT 1", (pid,))
    wrong_person = bool(last and last["status"] == "mismatch")
    ready = has_valid and not person["do_not_contact"] and not wrong_person
    if cur["stage"] == "ready" and not ready:
        db.update("crm_leads", cur["id"], {"stage": "enriched", "stage_changed_at": now_iso()})
        why = "do not contact" if person["do_not_contact"] else "no valid email" if not has_valid else "wrong person"
        _event(db, pid, "stage_change", {"from": "ready", "to": "enriched", "auto": True, "reason": why}, "system")
        return "enriched"
    if cur["stage"] in ("new", "enriched"):
        enriched = bool(db.one("SELECT 1 AS x FROM contact_points WHERE person_id=?", (pid,))) or \
            bool(db.one("SELECT 1 AS x FROM enrichment_items WHERE person_id=? AND status='matched'", (pid,)))
        target = "ready" if ready else "enriched" if enriched else None
        if target:
            set_stage(db, pid, target, "system", only_forward=True)
    return db.one("SELECT stage FROM crm_leads WHERE person_id=?", (pid,))["stage"]


def backfill(db: Database) -> int:
    """Give every campaign lead a CRM row and evaluate readiness. Safe to run repeatedly."""
    ids = [r["person_id"] for r in db.query(
        "SELECT DISTINCT cl.person_id FROM campaign_leads cl LEFT JOIN crm_leads c ON c.person_id = cl.person_id WHERE c.id IS NULL")]
    with db.transaction():
        for pid in ids:
            if db.one("SELECT 1 AS x FROM people WHERE id=?", (pid,)):
                ensure_lead(db, pid)
                refresh_readiness(db, pid)
    return len(ids)


def assign_owner(db: Database, pid: int, email: str | None, actor: str) -> None:
    email = (email or "").strip().lower() or None
    if email and not db.one("SELECT 1 AS x FROM users WHERE email=?", (email,)):
        raise ValueError("unknown user")
    cur = get_crm(db, pid)
    if cur["owner_email"] != email:
        db.update("crm_leads", cur["id"], {"owner_email": email})
        _event(db, pid, "owner_change", {"from": cur["owner_email"], "to": email}, actor)


# ---------------------------------------------------------------- notes
def add_note(db: Database, pid: int, body: str, author: str) -> int:
    body = (body or "").strip()
    if not body or len(body) > 5000:
        raise ValueError("note is required (max 5000 characters)")
    get_crm(db, pid)
    nid = db.insert("notes", {"person_id": pid, "body": body, "author": author, "created_at": now_iso()})
    _event(db, pid, "note", {"note_id": nid, "preview": body[:120]}, author)
    return nid


# ---------------------------------------------------------------- tasks
def _norm_due(v) -> str | None:
    if v in (None, ""):
        return None
    s = str(v).strip()
    try:
        if len(s) == 10:
            return date.fromisoformat(s).isoformat()
        return datetime.fromisoformat(s.replace("Z", "+00:00")).isoformat()
    except ValueError:
        raise ValueError("due date must look like 2026-12-31")


def _sync_next_action(db: Database, pid: int) -> None:
    nxt = db.one("SELECT MIN(due_at) AS d FROM tasks WHERE person_id=? AND done_at IS NULL AND due_at IS NOT NULL", (pid,))
    cur = get_crm(db, pid)
    db.update("crm_leads", cur["id"], {"next_action_at": nxt["d"] if nxt else None})


def create_task(db: Database, pid: int, title: str, due_at, owner: str | None, created_by: str) -> int:
    title = (title or "").strip()
    if not title or len(title) > 200:
        raise ValueError("task title is required (max 200 characters)")
    due = _norm_due(due_at)
    owner = (owner or "").strip().lower() or created_by
    if not db.one("SELECT 1 AS x FROM users WHERE email=?", (owner,)):
        raise ValueError("unknown task owner")
    get_crm(db, pid)
    tid = db.insert("tasks", {"person_id": pid, "title": title, "due_at": due, "owner_email": owner, "created_by": created_by, "created_at": now_iso()})
    _sync_next_action(db, pid)
    _event(db, pid, "task_created", {"task_id": tid, "title": title, "due_at": due}, created_by)
    return tid


def update_task(db: Database, tid: int, actor: str, done: bool | None = None, title=None, due_at="__keep__", owner=None) -> dict:
    t = db.one("SELECT * FROM tasks WHERE id=?", (tid,))
    if not t:
        raise KeyError(tid)
    vals = {}
    if done is not None:
        vals["done_at"] = now_iso() if done else None
    if title is not None:
        title = title.strip()
        if not title or len(title) > 200:
            raise ValueError("task title is required (max 200 characters)")
        vals["title"] = title
    if due_at != "__keep__":
        vals["due_at"] = _norm_due(due_at)
    if owner is not None:
        owner = owner.strip().lower()
        if not db.one("SELECT 1 AS x FROM users WHERE email=?", (owner,)):
            raise ValueError("unknown task owner")
        vals["owner_email"] = owner
    if vals:
        db.update("tasks", tid, vals)
        _sync_next_action(db, t["person_id"])
        if done is True and not t["done_at"]:
            _event(db, t["person_id"], "task_done", {"task_id": tid, "title": t["title"]}, actor)
    return db.one("SELECT * FROM tasks WHERE id=?", (tid,))


def list_tasks(db: Database, owner: str | None = None, status: str = "open", due: str | None = None, today: str | None = None, pid: int | None = None) -> list[dict]:
    today = today or datetime.now(timezone.utc).date().isoformat()
    date.fromisoformat(today)  # validate caller-supplied "today" (client sends its local date)
    w, p = ["1=1"], []
    if owner:
        w.append("t.owner_email=?"); p.append(owner)
    if pid:
        w.append("t.person_id=?"); p.append(pid)
    if status == "open":
        w.append("t.done_at IS NULL")
    elif status == "done":
        w.append("t.done_at IS NOT NULL")
    if due == "overdue":
        w.append("t.done_at IS NULL AND t.due_at IS NOT NULL AND substr(t.due_at,1,10) < ?"); p.append(today)
    elif due == "today":
        w.append("t.done_at IS NULL AND substr(t.due_at,1,10) = ?"); p.append(today)
    elif due == "upcoming":
        w.append("t.done_at IS NULL AND t.due_at IS NOT NULL AND substr(t.due_at,1,10) > ?"); p.append(today)
    rows = db.query("SELECT t.*, p.full_name AS person_name, c.name AS company_name FROM tasks t JOIN people p ON p.id = t.person_id "
                    "LEFT JOIN companies c ON c.id = p.company_id WHERE " + " AND ".join(w) +
                    " ORDER BY (t.due_at IS NULL), t.due_at, t.id LIMIT 500", p)
    for r in rows:
        r["overdue"] = bool(r["due_at"] and not r["done_at"] and r["due_at"][:10] < today)
    return rows


def timeline(db: Database, pid: int, limit: int = 200) -> list[dict]:
    rows = db.query("SELECT type, detail, actor, created_at FROM lead_events WHERE person_id=? ORDER BY id DESC LIMIT ?", (pid, int(limit)))
    for r in rows:
        try:
            r["detail"] = json.loads(r["detail"] or "{}")
        except ValueError:
            r["detail"] = {}
    return rows


# ---------------------------------------------------------------- board
def _days_since(ts: str | None, now: datetime | None = None) -> int | None:
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(0, ((now or datetime.now(timezone.utc)) - t).days)


def board(db: Database, campaign_id: int | None = None, owner: str | None = None, q: str | None = None, per_stage: int = 50) -> dict:
    w, p = ["1=1"], []
    if campaign_id:
        w.append("c.person_id IN (SELECT person_id FROM campaign_leads WHERE campaign_id=?)"); p.append(int(campaign_id))
    if owner == "none":
        w.append("c.owner_email IS NULL")
    elif owner:
        w.append("c.owner_email=?"); p.append(owner)
    if q:
        like = "%" + q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        w.append("(LOWER(COALESCE(p.full_name,'')) LIKE ? ESCAPE '\\' OR LOWER(COALESCE(co.name,'')) LIKE ? ESCAPE '\\')")
        p += [like, like]
    where = " AND ".join(w)
    base = ("FROM crm_leads c JOIN people p ON p.id = c.person_id LEFT JOIN companies co ON co.id = p.company_id "
            f"WHERE {where}")
    cols = {}
    for st in STAGES:
        total = db.one(f"SELECT COUNT(*) AS n {base} AND c.stage=?", p + [st])["n"]
        items = db.query(
            "SELECT p.id AS person_id, p.full_name, p.title, p.do_not_contact, co.name AS company_name, p.email, c.owner_email, c.next_action_at, c.stage_changed_at, "
            "(SELECT cp.verification FROM contact_points cp WHERE cp.person_id = p.id AND cp.kind='email' AND cp.is_primary=1 LIMIT 1) AS email_status, "
            "(SELECT COUNT(*) FROM tasks t WHERE t.person_id = p.id AND t.done_at IS NULL) AS open_tasks, "
            "(SELECT MAX(e.created_at) FROM lead_events e WHERE e.person_id = p.id) AS last_activity "
            f"{base} AND c.stage=? ORDER BY c.stage_changed_at DESC, p.id DESC LIMIT {int(per_stage)}", p + [st])
        for it in items:
            it["idle_days"] = _days_since(it.get("last_activity") or it.get("stage_changed_at"))
            it["stale"] = bool(st in STALE_DAYS and it["idle_days"] is not None and it["idle_days"] >= STALE_DAYS[st])
        cols[st] = {"label": LABELS[st], "count": total, "items": items, "stale_after_days": STALE_DAYS.get(st)}
    return {"stages": STAGES, "columns": cols}


# ---------------------------------------------------------------- Phase 4 intake
def _find_person_by_email(db: Database, email: str):
    email = (email or "").strip().lower()
    if not email:
        return None
    r = db.one("SELECT person_id AS id FROM contact_points WHERE kind='email' AND value=? LIMIT 1", (email,)) \
        or db.one("SELECT id FROM people WHERE LOWER(email)=? LIMIT 1", (email,))
    return r["id"] if r else None


def ingest_event(db: Database, type_: str, email: str, detail: dict | None = None, source: str = "outreach") -> dict:
    """Apply an outreach event (sent/reply/bounce/unsubscribe) to the matching lead. Unknown emails are logged, never fatal."""
    if type_ not in EVENT_TYPES:
        raise ValueError("unknown event type")
    detail = dict(detail or {})
    detail["email"] = (email or "").strip().lower()
    pid = _find_person_by_email(db, email)
    with db.transaction():
        _event(db, pid, type_, detail, source)
        if pid is None:
            return {"matched": False}
        get_crm(db, pid)
        if type_ == "email_sent":
            try:
                set_stage(db, pid, "contacted", source, only_forward=True)
            except ValueError:
                pass
        elif type_ == "reply":
            set_stage(db, pid, "replied", source, only_forward=True)
        elif type_ == "bounce":
            db.execute("UPDATE contact_points SET verification='invalid', is_primary=0 WHERE person_id=? AND kind='email' AND value=?", (pid, detail["email"]))
            refresh_readiness(db, pid)
        elif type_ == "unsubscribe":
            from app.enrich import set_do_not_contact
            set_do_not_contact(db, pid, True, "unsubscribed")
    return {"matched": True, "person_id": pid}
