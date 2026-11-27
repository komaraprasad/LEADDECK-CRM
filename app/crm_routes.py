"""HTTP routes for the CRM. Registered by app.web.create_app."""
from __future__ import annotations
import hmac
from flask import abort, jsonify, request
from app import crm, leads as L


def register(app, db, settings, login_required, current_user):
    def bad(e, code=400):
        return jsonify(error=str(e)), code

    def body():
        return request.get_json(silent=True) or {}

    def person_or_404(pid):
        if not db.one("SELECT 1 AS x FROM people WHERE id=?", (pid,)):
            abort(404)

    def intarg(name):
        v = request.args.get(name, "")
        return int(v) if v.isdigit() else None

    @app.get("/api/crm/stages")
    @login_required
    def crm_stages():
        return jsonify(stages=[{"key": k, "label": crm.LABELS[k]} for k in crm.STAGES])

    @app.get("/api/crm/users")
    @login_required
    def crm_users():
        return jsonify(items=db.query("SELECT email FROM users ORDER BY email"))

    @app.get("/api/crm/board")
    @login_required
    def crm_board():
        per = min(intarg("per_stage") or 50, 200)
        return jsonify(crm.board(db, intarg("campaign_id"), request.args.get("owner") or None, (request.args.get("q") or "").strip() or None, per,
                               (request.args.get("tool") or "").strip() or None))

    @app.get("/api/crm/leads/<int:pid>")
    @login_required
    def crm_lead(pid):
        person_or_404(pid)
        lead = L.get_lead(db, pid)
        lead["crm"] = crm.peek_crm(db, pid)
        lead["notes"] = db.query("SELECT id, body, author, created_at FROM notes WHERE person_id=? ORDER BY id DESC", (pid,))
        lead["tasks"] = crm.list_tasks(db, status="all", pid=pid)
        lead["timeline"] = crm.timeline(db, pid)
        return jsonify(lead)

    @app.post("/api/crm/bulk/add")
    @login_required
    def crm_bulk_add():
        ids = body().get("person_ids")
        if not isinstance(ids, list) or not ids or len(ids) > 500 or not all(isinstance(i, int) for i in ids):
            return bad("person_ids must be a list of 1-500 lead ids")
        added = already = 0
        with db.transaction():
            for pid in ids:
                if not db.one("SELECT 1 AS x FROM people WHERE id=?", (pid,)):
                    continue
                if crm.ensure_lead(db, pid, current_user()["email"]):
                    crm.refresh_readiness(db, pid); added += 1
                else:
                    already += 1
        return jsonify(added=added, already=already, skipped=len(ids) - added - already)

    @app.post("/api/crm/bulk/owner")
    @login_required
    def crm_bulk_owner():
        b = body()
        ids = b.get("person_ids")
        if not isinstance(ids, list) or not ids or len(ids) > 500 or not all(isinstance(i, int) for i in ids):
            return bad("person_ids must be a list of 1-500 lead ids")
        owner = (b.get("owner") or "").strip().lower() or None
        if owner and not db.one("SELECT 1 AS x FROM users WHERE email=?", (owner,)):
            return bad("unknown user")
        done = 0
        with db.transaction():
            for pid in ids:
                if db.one("SELECT 1 AS x FROM people WHERE id=?", (pid,)):
                    crm.assign_owner(db, pid, owner, current_user()["email"]); done += 1
        return jsonify(updated=done, skipped=len(ids) - done)

    @app.post("/api/crm/leads/<int:pid>/stage")
    @login_required
    def crm_stage(pid):
        person_or_404(pid)
        b = body()
        try:
            changed = crm.set_stage(db, pid, b.get("stage", ""), current_user()["email"], b.get("reason"))
        except ValueError as e:
            return bad(e)
        return jsonify(changed=changed, stage=crm.get_crm(db, pid)["stage"])

    @app.post("/api/crm/leads/<int:pid>/owner")
    @login_required
    def crm_owner(pid):
        person_or_404(pid)
        try:
            crm.assign_owner(db, pid, body().get("owner"), current_user()["email"])
        except ValueError as e:
            return bad(e)
        return jsonify(ok=True)

    @app.post("/api/crm/leads/<int:pid>/notes")
    @login_required
    def crm_note(pid):
        person_or_404(pid)
        try:
            return jsonify(id=crm.add_note(db, pid, body().get("body"), current_user()["email"])), 201
        except ValueError as e:
            return bad(e)

    @app.post("/api/crm/tasks")
    @login_required
    def crm_task_create():
        b = body()
        if not isinstance(b.get("person_id"), int):
            return bad("person_id is required")
        person_or_404(b["person_id"])
        try:
            return jsonify(id=crm.create_task(db, b["person_id"], b.get("title"), b.get("due_at"), b.get("owner"), current_user()["email"])), 201
        except ValueError as e:
            return bad(e)

    @app.patch("/api/crm/tasks/<int:tid>")
    @login_required
    def crm_task_update(tid):
        b = body()
        try:
            return jsonify(crm.update_task(db, tid, current_user()["email"], b.get("done"), b.get("title"),
                                           b["due_at"] if "due_at" in b else "__keep__", b.get("owner")))
        except KeyError:
            return bad("not found", 404)
        except ValueError as e:
            return bad(e)

    @app.get("/api/crm/tasks")
    @login_required
    def crm_tasks():
        owner = current_user()["email"] if request.args.get("mine") == "1" else (request.args.get("owner") or None)
        status = request.args.get("status", "open")
        due = request.args.get("due")
        if status not in ("open", "done", "all") or due not in (None, "", "overdue", "today", "upcoming"):
            return bad("invalid filter")
        try:
            return jsonify(items=crm.list_tasks(db, owner, status, due or None, request.args.get("today") or None))
        except ValueError:
            return bad("today must look like 2026-12-31")

    @app.post("/api/crm/events")
    def crm_events():  # token-protected, CSRF-exempt (called by outreach tools, not browsers)
        if not settings.crm_event_token:
            abort(404)
        if not hmac.compare_digest(request.headers.get("X-Event-Token", ""), settings.crm_event_token):
            return bad("invalid token", 401)
        b = body()
        try:
            return jsonify(crm.ingest_event(db, b.get("type", ""), b.get("email", ""), b.get("detail"), str(b.get("source") or "outreach")[:40]))
        except ValueError as e:
            return bad(e)
