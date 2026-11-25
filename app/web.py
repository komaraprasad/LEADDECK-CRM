"""Flask application: login, leads, campaigns, sync, import, export, legacy health endpoint."""
from __future__ import annotations
import os
import hmac
import json
import secrets
import threading
import time
import urllib.error
import urllib.request
from functools import wraps
from pathlib import Path
from flask import Flask, Response, abort, jsonify, redirect, request, send_from_directory, session
from app import auth, enrich as E, leads as L
from app.config import Settings, TOOLS, TOOL_LABELS, get_settings
from app.db import Database, now_iso
from app.exporter import build_csv, build_xlsx
from app.importer import import_export_file
from app.providers.base import ProviderError
from app.providers.rocketreach import RocketReach
from app.sync import SyncBusy, mark_stale_runs, run_all, run_sync, default_http_get

STATIC = Path(__file__).parent / "static"
PAGES = {"/": "index.html", "/leads": "leads.html", "/campaigns": "campaigns.html", "/sync": "sync.html", "/pipeline": "pipeline.html", "/tasks": "tasks.html", "/analytics": "analytics.html"}
ASSETS = {"app.js", "app.css"}
HEALTH_TIMEOUT = 15
# Legacy dashboard health keys (index.html expects 'leadfinder' for Hiring Badge Finder).
HEALTH_TOOLS = {
    "jobharvest": ("JobHarvest", "jobharvest"),
    "leadfinder": ("LeadFinder (Hiring Badge)", "hiringbadge"),
    "commentminer": ("CommentMiner", "commentminer"),
    "talentpulse": ("TalentPulse", "talentpulse"),
}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def check_tool_health(key: str, name: str, url: str) -> dict:
    t0 = time.time()
    base = {"key": key, "name": name, "url": url}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LeadDeck-HealthCheck/1.0"})
        with urllib.request.urlopen(req, timeout=HEALTH_TIMEOUT) as resp:
            return {**base, "status": "ONLINE", "code": resp.status, "latency_ms": int((time.time() - t0) * 1000)}
    except urllib.error.HTTPError as e:  # the tool answered, so it is up
        return {**base, "status": "ONLINE", "code": e.code, "latency_ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return {**base, "status": "OFFLINE", "error": str(e), "latency_ms": int((time.time() - t0) * 1000)}


def create_app(settings: Settings | None = None, db: Database | None = None, http_get=None, health_fn=None, provider=None) -> Flask:
    settings = settings or get_settings()
    weak = (not settings.secret_key) or settings.secret_key.lower().startswith("change-me")
    if weak and not settings.testing:
        raise RuntimeError("SECRET_KEY is not set (or is the placeholder). Set a long random SECRET_KEY before starting.")
    http_get = http_get or default_http_get
    health_fn = health_fn or check_tool_health
    if db is None:
        db = Database(settings.database_url)
    db.init_schema()
    mark_stale_runs(db)
    db.execute("UPDATE enrichment_runs SET status='failed', note='interrupted by a restart', finished=? WHERE status='running'", (now_iso(),))
    provider = provider or RocketReach(settings.rocketreach_api_key, lookup_type=settings.rocketreach_lookup_type)
    from app import crm as _crm
    _crm.backfill(db)
    enrich_lock = threading.Lock()
    auth.seed_admin(db, settings.admin_email, settings.admin_password)

    app = Flask(__name__, static_folder=None)
    app.secret_key = settings.secret_key or "test-only-secret"
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=settings.secure_cookies, MAX_CONTENT_LENGTH=11 * 1024 * 1024)
    app.config["DB"], app.config["SETTINGS"] = db, settings
    limiter = auth.RateLimiter(5, 60)

    # ------------------------------------------------------------ guards
    def current_user():
        uid = session.get("uid")
        return db.one("SELECT id, email, role FROM users WHERE id=?", (uid,)) if uid else None

    def login_required(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not current_user():
                if request.path.startswith("/api/"):
                    return jsonify(error="login required"), 401
                return redirect("/login")
            return fn(*a, **kw)
        return wrapper

    def admin_required(fn):
        @wraps(fn)
        @login_required
        def wrapper(*a, **kw):
            if current_user()["role"] != "admin":
                return jsonify(error="admin only"), 403
            return fn(*a, **kw)
        return wrapper

    CSRF_EXEMPT = ("/api/auth/login", "/api/sync/cron", "/api/crm/events")

    @app.before_request
    def csrf_guard():
        if request.method in ("GET", "HEAD", "OPTIONS") or request.path in CSRF_EXEMPT:
            return None
        sent, want = request.headers.get("X-CSRF-Token", ""), session.get("csrf", "")
        if not want or not hmac.compare_digest(sent, want):
            if request.path.startswith("/api/") and not current_user():
                return jsonify(error="login required"), 401
            return jsonify(error="missing or invalid CSRF token"), 403
        return None

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ------------------------------------------------------------ public
    @app.get("/healthz")
    def healthz():
        # which commit is running (Render sets RENDER_GIT_COMMIT), so a deploy can be checked without logging in
        return jsonify(ok=True, version=(os.environ.get("RENDER_GIT_COMMIT") or "local")[:7])

    @app.get("/api/health")
    @login_required
    def api_health():
        results, threads = {}, []
        def one(k, name, url):
            results[k] = health_fn(k, name, url)
        for key, (name, tool) in HEALTH_TOOLS.items():
            t = threading.Thread(target=one, args=(key, name, settings.tool_urls[tool]))
            t.start(); threads.append(t)
        for t in threads:
            t.join(timeout=HEALTH_TIMEOUT + 1)
        return jsonify(tools=results, timestamp=time.time())

    @app.get("/assets/<name>")
    def assets(name):
        if name not in ASSETS:
            abort(404)
        return send_from_directory(STATIC, name, max_age=0)

    @app.get("/login")
    def login_page():
        return send_from_directory(STATIC, "login.html", max_age=0)

    def page(path, filename):
        @login_required
        def view():
            if filename == "index.html":  # add the newer workspace links without needing to rewrite the dashboard file
                html = (STATIC / filename).read_text(encoding="utf-8")
                old = "[['/leads','Leads'],['/campaigns','Campaigns'],['/sync','Sync']]"
                new = "[['/leads','Leads'],['/campaigns','Campaigns'],['/pipeline','CRM'],['/tasks','Tasks'],['/analytics','Analytics'],['/sync','Sync']]"
                return Response(html.replace(old, new), mimetype="text/html", headers={"Cache-Control": "no-cache"})
            return send_from_directory(STATIC, filename, max_age=0)
        app.add_url_rule(path, endpoint="page_" + filename, view_func=view)
    for path, filename in PAGES.items():
        page(path, filename)

    # -------------------------------------------------------------- auth
    @app.post("/api/auth/login")
    def login():
        if not limiter.allow(request.remote_addr or "?"):
            return jsonify(error="too many attempts; wait a minute"), 429
        body = request.get_json(silent=True) or {}
        u = auth.authenticate(db, body.get("email", ""), body.get("password", ""))
        if not u:
            return jsonify(error="invalid email or password"), 401
        session.clear()
        session["uid"], session["csrf"] = u["id"], secrets.token_hex(16)
        return jsonify(email=u["email"], role=u["role"], csrf=session["csrf"])

    @app.post("/api/auth/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get("/api/auth/me")
    def me():
        u = current_user()
        if not u:
            return jsonify(error="login required"), 401
        session.setdefault("csrf", secrets.token_hex(16))
        return jsonify(email=u["email"], role=u["role"], csrf=session["csrf"])

    @app.post("/api/users")
    @admin_required
    def add_user():
        b = request.get_json(silent=True) or {}
        try:
            uid = auth.create_user(db, b.get("email", ""), b.get("password", ""), b.get("role", "user"))
        except ValueError as e:
            return jsonify(error=str(e)), 400
        return jsonify(id=uid), 201

    # ------------------------------------------------------------- leads
    def filters_from(args):
        return {k: args.get(k) for k in L.FILTER_KEYS if args.get(k)}

    def intarg(name, default):
        try:
            return int(request.args.get(name, default))
        except ValueError:
            return default

    @app.get("/api/leads")
    @login_required
    def leads_list():
        try:
            return jsonify(L.list_leads(db, filters_from(request.args), intarg("page", 1), intarg("page_size", 50),
                                        request.args.get("sort") or "last_seen", request.args.get("dir") or "desc"))
        except ValueError as e:
            return jsonify(error=str(e)), 400

    @app.get("/api/leads/suggest")
    @login_required
    def leads_suggest():
        try:
            return jsonify(items=L.suggest(db, request.args.get("field", "all"), request.args.get("q", ""), intarg("limit", 8)))
        except ValueError as e:
            return jsonify(error=str(e)), 400

    @app.get("/favicon.ico")
    def favicon():
        return Response(status=204)

    def export_response(rows, fmt, name):
        data = build_csv(rows) if fmt == "csv" else build_xlsx(rows)
        return Response(data, mimetype="text/csv; charset=utf-8" if fmt == "csv" else XLSX,
                        headers={"Content-Disposition": f'attachment; filename="{name}.{fmt}"'})

    @app.get("/api/leads/export.<fmt>")
    @login_required
    def leads_export(fmt):
        if fmt not in ("csv", "xlsx"):
            abort(404)
        try:
            return export_response(L.export_rows(db, filters_from(request.args)), fmt, "leaddeck-leads")
        except ValueError as e:
            return jsonify(error=str(e)), 400

    @app.get("/api/leads/<int:pid>")
    @login_required
    def lead_detail(pid):
        row = L.get_lead(db, pid)
        return (jsonify(row), 200) if row else (jsonify(error="not found"), 404)

    # --------------------------------------------------------- campaigns
    @app.get("/api/campaigns")
    @login_required
    def campaigns_list():
        return jsonify(items=db.query(
            "SELECT c.id, c.name, c.description, c.created_by, c.created_at, "
            "(SELECT COUNT(*) FROM campaign_leads cl WHERE cl.campaign_id = c.id) AS lead_count FROM campaigns c ORDER BY c.id DESC"))

    @app.post("/api/campaigns")
    @login_required
    def campaigns_create():
        b = request.get_json(silent=True) or {}
        try:
            cid = L.create_campaign(db, b.get("name"), b.get("description"), current_user()["email"])
        except ValueError as e:
            return jsonify(error=str(e)), 400
        return jsonify(id=cid), 201

    @app.get("/api/campaigns/<int:cid>")
    @login_required
    def campaign_get(cid):
        c = db.one("SELECT * FROM campaigns WHERE id=?", (cid,))
        if not c:
            return jsonify(error="not found"), 404
        ids = L.campaign_person_ids(db, cid)
        c["lead_count"] = len(ids)
        c["leads"] = L.export_rows(db, ids=ids[:500])
        return jsonify(c)

    @app.post("/api/campaigns/<int:cid>/leads")
    @login_required
    def campaign_add(cid):
        b = request.get_json(silent=True) or {}
        try:
            if "person_ids" in b:
                if not isinstance(b["person_ids"], list):
                    return jsonify(error="person_ids must be a list"), 400
                return jsonify(L.add_to_campaign(db, cid, person_ids=b["person_ids"]))
            return jsonify(L.add_to_campaign(db, cid, filters=b.get("filter") or {}))
        except KeyError:
            return jsonify(error="campaign not found"), 404
        except (ValueError, TypeError) as e:
            return jsonify(error=str(e)), 400

    @app.delete("/api/campaigns/<int:cid>/leads/<int:pid>")
    @login_required
    def campaign_remove(cid, pid):
        n = db.execute("DELETE FROM campaign_leads WHERE campaign_id=? AND person_id=?", (cid, pid))
        return jsonify(removed=n)

    @app.get("/api/campaigns/<int:cid>/export.<fmt>")
    @login_required
    def campaign_export(cid, fmt):
        c = db.one("SELECT * FROM campaigns WHERE id=?", (cid,))
        if fmt not in ("csv", "xlsx") or not c:
            abort(404)
        return export_response(L.export_rows(db, ids=L.campaign_person_ids(db, cid)), fmt, "campaign-%d" % cid)

    # -------------------------------------------------------------- sync
    def tool_or_404(tool):
        if tool not in TOOLS:
            abort(404)

    def dry_arg():
        v = request.args.get("dry_run")
        return None if v is None else v.lower() in ("1", "true", "yes")

    @app.get("/api/sync/status")
    @login_required
    def sync_status():
        out = {}
        for t in TOOLS:
            last = db.one("SELECT * FROM sync_runs WHERE tool=? ORDER BY id DESC LIMIT 1", (t,))
            ok = db.one("SELECT * FROM sync_runs WHERE tool=? AND status='ok' AND dry_run=0 ORDER BY id DESC LIMIT 1", (t,))
            out[t] = {"label": TOOL_LABELS[t], "last_run": last, "last_live_ok": ok}
        counts = {k: db.one(f"SELECT COUNT(*) AS n FROM {k}")["n"] for k in ("people", "companies", "job_postings")}
        return jsonify(tools=out, counts=counts, db_size_bytes=db.size_bytes())

    @app.get("/api/sync/runs")
    @login_required
    def sync_runs():
        return jsonify(items=db.query("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 50"))

    @app.post("/api/sync/all")
    @login_required
    def sync_all():
        return jsonify(runs=run_all(db, settings, dry_arg(), http_get))

    @app.post("/api/sync/cron")
    def sync_cron():
        if not settings.sync_token:
            abort(404)
        if not hmac.compare_digest(request.headers.get("X-Sync-Token", ""), settings.sync_token):
            return jsonify(error="invalid token"), 401
        return jsonify(runs=run_all(db, settings, None, http_get))  # None => dry run until a tool has had a live sync

    @app.post("/api/sync/<tool>")
    @login_required
    def sync_one(tool):
        tool_or_404(tool)
        try:
            return jsonify(run_sync(db, settings, tool, dry_arg(), http_get))
        except SyncBusy:
            return jsonify(error=f"a sync for {tool} is already running"), 409

    @app.post("/api/sync/<tool>/import")
    @login_required
    def sync_import(tool):
        tool_or_404(tool)
        f = request.files.get("file")
        if not f or not f.filename:
            return jsonify(error="attach a .csv or .xlsx file in the 'file' field"), 400
        dry = request.args.get("dry_run", "true").lower() in ("1", "true", "yes")
        try:
            return jsonify(import_export_file(db, tool, f.filename, f.read(), dry))
        except ValueError as e:
            return jsonify(error=str(e)), 400

    # -------------------------------------------------------- enrichment
    def int_body(b, key, default):
        try:
            return max(0, int(b.get(key, default)))
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a whole number")

    def ids_from(b):
        pids = b.get("person_ids")
        if pids is not None and (not isinstance(pids, list) or not all(isinstance(i, int) for i in pids)):
            raise ValueError("person_ids must be a list of numbers")
        return pids, bool(b.get("include_done"))

    def campaign_or_404(cid):
        if not db.one("SELECT 1 AS x FROM campaigns WHERE id=?", (cid,)):
            abort(404)

    @app.get("/api/enrich/config")
    @login_required
    def enrich_config():
        return jsonify(provider=provider.name, configured=provider.configured(),
                       default_credit_cap=settings.enrich_credit_cap, default_lead_cap=settings.enrich_lead_cap)

    @app.post("/api/campaigns/<int:cid>/enrich/estimate")
    @login_required
    def enrich_estimate(cid):
        campaign_or_404(cid)
        b = request.get_json(silent=True) or {}
        try:
            return jsonify(E.estimate(db, cid, provider, int_body(b, "lead_cap", settings.enrich_lead_cap), *ids_from(b)))
        except ValueError as e:
            return jsonify(error=str(e)), 400

    @app.post("/api/campaigns/<int:cid>/enrich")
    @login_required
    def enrich_start(cid):
        campaign_or_404(cid)
        b = request.get_json(silent=True) or {}
        if b.get("confirm") is not True:
            return jsonify(error="confirm must be true: review the estimate first"), 400
        try:
            credit_cap, lead_cap = int_body(b, "credit_cap", settings.enrich_credit_cap), int_body(b, "lead_cap", settings.enrich_lead_cap)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        try:
            pids, include_done = ids_from(b)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        if not provider.configured():
            return jsonify(error="ROCKETREACH_API_KEY is not set on the server. Add it to .env and restart."), 400
        if credit_cap < 1 or lead_cap < 1:
            return jsonify(error="credit_cap and lead_cap must be at least 1"), 400
        if not enrich_lock.acquire(blocking=False):
            return jsonify(error="an enrichment run is already in progress"), 409
        user = current_user()["email"]

        def work():
            try:
                return E.run_enrichment(db, provider, cid, credit_cap, lead_cap, user, pids, include_done)
            except ProviderError as ex:
                return {"status": "failed", "note": str(ex)}
            finally:
                enrich_lock.release()
        if settings.testing:
            return jsonify(work()), 200
        threading.Thread(target=work, daemon=True).start()
        return jsonify(status="started"), 202

    @app.get("/api/enrich/runs")
    @login_required
    def enrich_runs():
        cid = request.args.get("campaign_id")
        q, p = "SELECT * FROM enrichment_runs", ()
        if cid and cid.isdigit():
            q, p = q + " WHERE campaign_id=?", (int(cid),)
        return jsonify(items=db.query(q + " ORDER BY id DESC LIMIT 50", p))

    @app.get("/api/enrich/items")
    @login_required
    def enrich_items():
        pid = request.args.get("person_id", "")
        if not pid.isdigit():
            return jsonify(error="person_id is required"), 400
        return jsonify(items=db.query("SELECT run_id, provider, status, credits_used, error, raw_shape, created_at FROM enrichment_items WHERE person_id=? ORDER BY id DESC LIMIT 10", (int(pid),)))

    @app.get("/api/campaigns/<int:cid>/enrich/stats")
    @login_required
    def enrich_stats(cid):
        campaign_or_404(cid)
        return jsonify(E.campaign_stats(db, cid))

    @app.get("/api/enrich/ledger")
    @login_required
    def enrich_ledger():
        total = db.one("SELECT COALESCE(SUM(credits),0) AS n FROM credit_ledger")["n"]
        return jsonify(total_credits=total, items=db.query("SELECT * FROM credit_ledger ORDER BY id DESC LIMIT 100"))

    @app.post("/api/leads/<int:pid>/do-not-contact")
    @login_required
    def lead_dnc(pid):
        b = request.get_json(silent=True) or {}
        try:
            E.set_do_not_contact(db, pid, bool(b.get("value", True)), b.get("reason"))
        except KeyError:
            return jsonify(error="not found"), 404
        return jsonify(ok=True)

    @app.delete("/api/leads/<int:pid>")
    @admin_required
    def lead_erase(pid):
        try:
            E.erase_person(db, pid)
        except KeyError:
            return jsonify(error="not found"), 404
        return jsonify(erased=True)

    from app import crm_routes
    crm_routes.register(app, db, settings, login_required, current_user)
    from app import views as _views
    _views.register(app, db, login_required, current_user)

    @app.get("/api/analytics")
    @login_required
    def analytics_overview():
        from app import analytics
        return jsonify(analytics.overview(db, request.args.get("days"), local_today=request.args.get("today")))

    @app.errorhandler(413)
    def too_big(_):
        return jsonify(error="file too large (limit 10 MB)"), 413

    return app
