import io, json, pathlib
import pytest
from app.config import Settings
from app.db import Database
from app.web import create_app

FX = pathlib.Path(__file__).parent / "fixtures"
PW = "supersecret1"


def routes():
    def get(url, timeout):
        n = ("talentpulse" if "ec2-15-252" in url else "hiringbadge" if "hiring-badge" in url
             else "commentminer" if "/api/posts/combined" in url else "jobharvest")
        if n == "jobharvest" and "jobharvest" not in url:
            raise AssertionError(url)
        return 200, (FX / f"{n}.json").read_text(encoding="utf-8")
    return get


@pytest.fixture
def app():
    s = Settings(testing=True, admin_email="admin@example.com", admin_password=PW, sync_token="")
    fake_health = lambda k, name, url: {"key": k, "name": name, "url": url, "status": "ONLINE", "code": 200, "latency_ms": 5}
    return create_app(s, Database("sqlite:///:memory:"), http_get=routes(), health_fn=fake_health)


class Client:
    def __init__(self, app):
        self.c = app.test_client(); self.csrf = ""
    def login(self, email="admin@example.com", pw=PW):
        r = self.c.post("/api/auth/login", json={"email": email, "password": pw})
        if r.status_code == 200:
            self.csrf = r.get_json()["csrf"]
        return r
    def h(self): return {"X-CSRF-Token": self.csrf}
    def get(self, *a, **k): return self.c.get(*a, **k)
    def post(self, p, **k): return self.c.post(p, headers=self.h(), **k)
    def delete(self, p, **k): return self.c.delete(p, headers=self.h(), **k)


@pytest.fixture
def cl(app):
    c = Client(app); assert c.login().status_code == 200
    return c


def test_everything_but_health_login_assets_needs_login(app):
    c = app.test_client()
    for p in ("/api/leads", "/api/campaigns", "/api/sync/status", "/api/leads/export.csv", "/api/auth/me", "/api/sync/runs"):
        assert c.get(p).status_code == 401, p
    for p in ("/", "/leads", "/campaigns", "/sync"):
        r = c.get(p); assert r.status_code == 302 and r.headers["Location"].endswith("/login"), p
    assert c.get("/login").status_code == 200 and c.get("/healthz").status_code == 200
    assert c.get("/assets/app.js").status_code == 200 and c.get("/assets/../app/web.py").status_code == 404
    r = c.get("/api/health"); assert r.status_code == 401 and "Access-Control-Allow-Origin" not in r.headers  # tool URLs and errors are not public
    c.post("/api/auth/login", json={"email": "admin@example.com", "password": PW})
    h = c.get("/api/health").get_json()
    assert set(h["tools"]) == {"jobharvest", "leadfinder", "commentminer", "talentpulse"} and "timestamp" in h  # legacy shape


def test_login_logout_and_csrf(app):
    c = Client(app)
    assert c.login(pw="wrong").status_code == 401
    assert c.login().status_code == 200
    assert c.get("/api/auth/me").get_json()["email"] == "admin@example.com"
    r = c.c.post("/api/campaigns", json={"name": "x"})            # no CSRF header
    assert r.status_code == 403
    assert c.post("/api/campaigns", json={"name": "x"}).status_code == 201
    c.post("/api/auth/logout")
    assert c.get("/api/leads").status_code == 401


def test_login_rate_limited(app):
    c = Client(app)
    codes = [c.login(pw="bad").status_code for _ in range(7)]
    assert codes[:5] == [401] * 5 and 429 in codes[5:]


def test_pages_render_when_logged_in_and_tools_tab_kept(cl):
    for p in ("/", "/leads", "/campaigns", "/sync"):
        assert cl.get(p).status_code == 200, p
    html = cl.get("/").get_data(as_text=True)
    for name in ("JobHarvest", "CommentMiner", "TalentPulse", "Hiring Badge Finder"):
        assert name in html
    assert "/leads" in html and "WORKSPACE" in html


def test_full_flow_sync_leads_campaign_export(cl):
    # first sync is a dry run by default: nothing saved
    r = cl.post("/api/sync/talentpulse").get_json()
    assert r["dry_run"] == 1 and r["status"] == "ok" and cl.get("/api/leads").get_json()["total"] == 0
    for t in ("talentpulse", "hiringbadge", "commentminer", "jobharvest"):
        assert cl.post(f"/api/sync/{t}?dry_run=false").get_json()["status"] == "ok"
    d = cl.get("/api/leads").get_json()
    assert d["total"] >= 5
    asha = cl.get("/api/leads?q=asha").get_json()
    assert asha["total"] == 1 and asha["items"][0]["tools"] == ["hiringbadge", "jobharvest", "talentpulse"]  # one person, three sources
    detail = cl.get(f"/api/leads/{asha['items'][0]['id']}").get_json()
    assert len(detail["sources"]) == 3
    cid = cl.post("/api/campaigns", json={"name": "Pune hiring"}).get_json()["id"]
    added = cl.post(f"/api/campaigns/{cid}/leads", json={"filter": {"has_email": "true"}}).get_json()
    assert added["added"] == cl.get("/api/leads?has_email=true").get_json()["total"] > 0
    csv_ = cl.get(f"/api/campaigns/{cid}/export.csv")
    assert csv_.status_code == 200 and "attachment" in csv_.headers["Content-Disposition"]
    assert len(csv_.get_data(as_text=True).strip().splitlines()) - 1 == added["added"]
    assert cl.get("/api/leads/export.xlsx?tool=hiringbadge").status_code == 200
    assert cl.get(f"/api/campaigns/{cid}").get_json()["lead_count"] == added["added"]
    st = cl.get("/api/sync/status").get_json()
    assert st["counts"]["people"] == d["total"] and st["db_size_bytes"] > 0


def test_unknown_tool_filter_and_bad_input(cl):
    assert cl.post("/api/sync/nope").status_code == 404
    assert cl.get("/api/leads?lead_type=bogus").status_code == 400
    assert cl.post("/api/campaigns", json={"name": ""}).status_code == 400
    assert cl.post("/api/campaigns/999/leads", json={"person_ids": [1]}).status_code == 404
    assert cl.post("/api/campaigns/1/leads", json={"person_ids": "x"}).status_code in (400, 404)
    assert cl.get("/api/leads/999999").status_code == 404 and cl.get("/api/leads/export.pdf").status_code == 404


def test_import_endpoint_and_errors(cl):
    csv_bytes = b"Person Name,Person LinkedIn URL,Company Name\nAsha Rao,https://www.linkedin.com/in/asha-rao,Globex\n"
    r = cl.post("/api/sync/hiringbadge/import?dry_run=true", data={"file": (io.BytesIO(csv_bytes), "x.csv")}, content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["created"] == 1 and cl.get("/api/leads").get_json()["total"] == 0
    cl.post("/api/sync/hiringbadge/import?dry_run=false", data={"file": (io.BytesIO(csv_bytes), "x.csv")}, content_type="multipart/form-data")
    assert cl.get("/api/leads").get_json()["total"] == 1
    bad = cl.post("/api/sync/hiringbadge/import", data={"file": (io.BytesIO(b"foo,bar\n1,2\n"), "x.csv")}, content_type="multipart/form-data")
    assert bad.status_code == 400 and "missing expected columns" in bad.get_json()["error"]
    assert cl.post("/api/sync/hiringbadge/import", data={}, content_type="multipart/form-data").status_code == 400


def test_cron_endpoint_token_rules(app):
    c = app.test_client()
    assert c.post("/api/sync/cron").status_code == 404                      # disabled when SYNC_TOKEN is empty
    s = Settings(testing=True, sync_token="tok-123", admin_email="a@b.co", admin_password=PW)
    app2 = create_app(s, Database("sqlite:///:memory:"), http_get=routes(), health_fn=lambda *a: {})
    c2 = app2.test_client()
    assert c2.post("/api/sync/cron", headers={"X-Sync-Token": "wrong"}).status_code == 401
    r = c2.post("/api/sync/cron", headers={"X-Sync-Token": "tok-123"})
    assert r.status_code == 200 and all(x["dry_run"] == 1 for x in r.get_json()["runs"])  # safe: dry runs until a first live sync


def test_users_admin_only_and_formula_export(cl, app):
    assert cl.post("/api/users", json={"email": "u@example.com", "password": "password12"}).status_code == 201
    u = Client(app); assert u.login("u@example.com", "password12").status_code == 200
    assert u.post("/api/users", json={"email": "z@example.com", "password": "password12"}).status_code == 403
    db = app.config["DB"]
    from app.merge import upsert_records, NormalizedRecord
    upsert_records(db, [NormalizedRecord("talentpulse", "person", "t", {}, {"full_name": "Evil", "headline": "=HYPERLINK(\"http://x\",\"y\")", "linkedin_url": "https://www.linkedin.com/in/evil"})])
    body = cl.get("/api/leads/export.csv").get_data(as_text=True)
    assert "'=HYPERLINK" in body and ",=HYPERLINK" not in body


def test_refuses_to_start_without_real_secret():
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(Settings(secret_key="", testing=False), Database("sqlite:///:memory:"))
    with pytest.raises(RuntimeError):
        create_app(Settings(secret_key="change-me-to-a-long-random-string"), Database("sqlite:///:memory:"))


def test_healthz_reports_deployed_version(app, monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a22e081f00ba7")
    assert app.test_client().get("/healthz").get_json() == {"ok": True, "version": "a22e081"}
    monkeypatch.delenv("RENDER_GIT_COMMIT")
    assert app.test_client().get("/healthz").get_json() == {"ok": True, "version": "local"}


def test_on_render_a_missing_database_url_stops_startup(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    good_key = "x" * 40
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_app(Settings(secret_key=good_key, database_url="sqlite:///leaddeck.db"), Database("sqlite:///:memory:"))
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_app(Settings(secret_key=good_key, database_url=""), Database("sqlite:///:memory:"))
    monkeypatch.delenv("RENDER")
    create_app(Settings(secret_key=good_key, database_url="sqlite:///leaddeck.db"), Database("sqlite:///:memory:"))  # laptop: fine
