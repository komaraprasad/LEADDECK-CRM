"""Saved filter views for the Leads page (private to the creator, or shared with the team)."""
from __future__ import annotations
import json
from flask import jsonify, request
from app import leads as L
from app.db import Database, now_iso

PAGES = ("leads",)
MAX_PER_USER = 50
VIEW_KEYS = (*L.FILTER_KEYS, "sort", "dir")


def clean_view_filters(raw: dict) -> dict:
    f = L.clean_filters({k: raw[k] for k in L.FILTER_KEYS if k in raw})
    if raw.get("sort") not in (None, "", *L.SORT_KEYS):
        raise ValueError("invalid sort")
    if raw.get("dir") not in (None, "", "asc", "desc"):
        raise ValueError("invalid sort direction")
    for k in ("sort", "dir"):
        if raw.get(k):
            f[k] = raw[k]
    return f


def list_views(db: Database, user: str, page: str = "leads") -> list[dict]:
    rows = db.query("SELECT id, name, filters, owner_email, shared FROM saved_views WHERE page=? AND (owner_email=? OR shared=1) ORDER BY LOWER(name), id", (page, user))
    return [{"id": r["id"], "name": r["name"], "filters": json.loads(r["filters"]), "owner": r["owner_email"], "shared": bool(r["shared"])} for r in rows]


def create_view(db: Database, user: str, name: str, page: str, filters: dict, shared: bool) -> int:
    name = (name or "").strip()
    if not name or len(name) > 60:
        raise ValueError("view name is required (max 60 characters)")
    if page not in PAGES:
        raise ValueError("invalid page")
    if db.one("SELECT COUNT(*) AS n FROM saved_views WHERE owner_email=?", (user,))["n"] >= MAX_PER_USER:
        raise ValueError("too many saved views (limit %d)" % MAX_PER_USER)
    if db.one("SELECT 1 AS x FROM saved_views WHERE owner_email=? AND page=? AND LOWER(name)=?", (user, page, name.lower())):
        raise ValueError("you already have a view with that name")
    return db.insert("saved_views", {"name": name, "page": page, "filters": json.dumps(clean_view_filters(filters or {})),
                                     "owner_email": user, "shared": 1 if shared else 0, "created_at": now_iso()})


def register(app, db, login_required, current_user):
    @app.get("/api/views")
    @login_required
    def views_list():
        return jsonify(items=list_views(db, current_user()["email"], request.args.get("page", "leads")))

    @app.post("/api/views")
    @login_required
    def views_create():
        b = request.get_json(silent=True) or {}
        try:
            vid = create_view(db, current_user()["email"], b.get("name"), b.get("page", "leads"), b.get("filters") or {}, bool(b.get("shared")))
        except ValueError as e:
            return jsonify(error=str(e)), 400
        return jsonify(id=vid), 201

    @app.delete("/api/views/<int:vid>")
    @login_required
    def views_delete(vid):
        u = current_user()
        v = db.one("SELECT id, owner_email FROM saved_views WHERE id=?", (vid,))
        if not v:
            return jsonify(error="not found"), 404
        if v["owner_email"] != u["email"] and u["role"] != "admin":
            return jsonify(error="only the creator or an admin can delete this view"), 403
        db.execute("DELETE FROM saved_views WHERE id=?", (vid,))
        return jsonify(ok=True)
