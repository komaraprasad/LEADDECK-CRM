import pytest
from app import crm, enrich as E, leads as L
from app.config import Settings
from app.db import Database
from app.providers.base import ContactValue, ProviderResult
from app.providers.fake import FakeProvider
from app.web import create_app
from tests.test_phase2 import mkperson, mkcamp, ok
from tests.test_web import Client, PW, routes


def user(db, email="me@x.com"):
    db.insert("users", {"email": email, "password_hash": "x", "role": "user"})
    return email


def stage(db, pid):
    return db.one("SELECT stage FROM crm_leads WHERE person_id=?", (pid,))["stage"]


# ---- creation / backfill
def test_adding_to_campaign_creates_crm_row_once(db):
    a = mkperson(db, "A", "u1")
    c = db.insert("campaigns", {"name": "c"})
    L.add_to_campaign(db, c, person_ids=[a]); L.add_to_campaign(db, c, person_ids=[a])
    assert db.one("SELECT COUNT(*) AS n FROM crm_leads")["n"] == 1 and stage(db, a) == "new"
    assert db.one("SELECT COUNT(*) AS n FROM lead_events WHERE type='stage_change'")["n"] == 1


def test_backfill_is_idempotent_and_evaluates_readiness(db):
    a, b = mkperson(db, "A", "u1"), mkperson(db, "B", "u2")
    mkcamp(db, [a, b])
    db.insert("contact_points", {"person_id": a, "kind": "email", "value": "a@x.com", "provider": "x", "verification": "valid"})
    assert crm.backfill(db) == 2 and crm.backfill(db) == 0
    assert stage(db, a) == "ready" and stage(db, b) == "new"
    assert db.one("SELECT COUNT(*) AS n FROM lead_events WHERE person_id=?", (a,))["n"] == 2  # new, then ready


# ---- readiness rules
def run_enrich(db, camp, script):
    return E.run_enrichment(db, FakeProvider(script), camp, 50, 50)


def test_enrichment_drives_stage_forward(db):
    a, b, c3 = mkperson(db, "A", "u1"), mkperson(db, "B", "u2"), mkperson(db, "C", "u3")
    camp = mkcamp(db, [a, b, c3])
    crm.backfill(db)
    run_enrich(db, camp, {a: ok("a@x.com", "valid"), b: ok("b@x.com", "unverified"), c3: ProviderResult("not_found")})
    assert (stage(db, a), stage(db, b), stage(db, c3)) == ("ready", "enriched", "new")


def test_wrong_person_never_ready(db):
    a = mkperson(db, "Anusha T", "u1", "Simplotel")
    camp = mkcamp(db, [a]); crm.backfill(db)
    r = ok("w@x.com"); r.matched_name = "Someone Else"
    run_enrich(db, camp, {a: r})
    assert stage(db, a) == "new"


def test_dnc_demotes_ready_and_blocks_contacted(db):
    a = mkperson(db, "A", "u1")
    camp = mkcamp(db, [a]); crm.backfill(db)
    run_enrich(db, camp, {a: ok("a@x.com")})
    assert stage(db, a) == "ready"
    E.set_do_not_contact(db, a, True, "asked")
    assert stage(db, a) == "enriched"
    with pytest.raises(ValueError, match="do not contact"):
        crm.set_stage(db, a, "contacted", "me@x.com")
    with pytest.raises(ValueError):
        crm.set_stage(db, a, "won", "me@x.com")
    crm.set_stage(db, a, "not_a_fit", "me@x.com", "asked to stop")  # allowed
    assert stage(db, a) == "not_a_fit"


def test_automation_never_goes_backwards_or_out_of_terminal(db):
    a = mkperson(db, "A", "u1")
    mkcamp(db, [a]); crm.backfill(db)
    crm.set_stage(db, a, "meeting", "me@x.com")
    db.insert("contact_points", {"person_id": a, "kind": "email", "value": "a@x.com", "provider": "x", "verification": "valid"})
    assert crm.refresh_readiness(db, a) == "meeting"
    crm.set_stage(db, a, "lost", "me@x.com", "no budget")
    assert crm.set_stage(db, a, "replied", "smartlead", only_forward=True) is False and stage(db, a) == "lost"


# ---- manual stage rules
def test_stage_validation_and_reasons(db):
    a = mkperson(db, "A", "u1"); mkcamp(db, [a])
    with pytest.raises(ValueError, match="unknown"):
        crm.set_stage(db, a, "nope", "me@x.com")
    for st in ("lost", "not_a_fit"):
        with pytest.raises(ValueError, match="reason"):
            crm.set_stage(db, a, st, "me@x.com", "  ")
    assert crm.set_stage(db, a, "interested", "me@x.com") is True and crm.set_stage(db, a, "interested", "me@x.com") is False
    ev = [e for e in crm.timeline(db, a) if e["type"] == "stage_change"][0]
    assert ev["detail"]["to"] == "interested" and ev["actor"] == "me@x.com"
    with pytest.raises(KeyError):
        crm.set_stage(db, 999, "new", "me@x.com")


# ---- notes, owner, tasks
def test_notes_owner_tasks_and_next_action(db):
    me = user(db); a = mkperson(db, "A", "u1"); mkcamp(db, [a])
    with pytest.raises(ValueError):
        crm.add_note(db, a, "  ", me)
    crm.add_note(db, a, "called reception", me)
    with pytest.raises(ValueError, match="unknown user"):
        crm.assign_owner(db, a, "ghost@x.com", me)
    crm.assign_owner(db, a, me, me)
    t1 = crm.create_task(db, a, "Follow up", "2026-12-05", me, me)
    t2 = crm.create_task(db, a, "Send deck", "2026-12-01", None, me)
    assert db.one("SELECT next_action_at AS d FROM crm_leads WHERE person_id=?", (a,))["d"] == "2026-12-01"
    crm.update_task(db, t2, me, done=True)
    assert db.one("SELECT next_action_at AS d FROM crm_leads WHERE person_id=?", (a,))["d"] == "2026-12-05"
    with pytest.raises(ValueError):
        crm.create_task(db, a, "x", "31/12/2026", me, me)
    with pytest.raises(ValueError):
        crm.create_task(db, a, "", None, me, me)
    kinds = [e["type"] for e in crm.timeline(db, a)]
    assert {"note", "owner_change", "task_created", "task_done"} <= set(kinds)


def test_task_filters_overdue_today_upcoming_and_client_date(db):
    me = user(db); a = mkperson(db, "A", "u1"); mkcamp(db, [a])
    for title, due in (("old", "2026-11-10"), ("now", "2026-11-17"), ("later", "2026-12-01"), ("nodate", None)):
        crm.create_task(db, a, title, due, me, me)
    names = lambda **k: [t["title"] for t in crm.list_tasks(db, today="2026-11-17", **k)]
    assert names(due="overdue") == ["old"] and names(due="today") == ["now"] and names(due="upcoming") == ["later"]
    assert names() == ["old", "now", "later", "nodate"]  # dated first, undated last
    assert crm.list_tasks(db, today="2026-11-17")[0]["overdue"] is True
    with pytest.raises(ValueError):
        crm.list_tasks(db, today="17-11-2026")


# ---- board
def test_board_counts_limits_filters_and_empty(db):
    assert all(c["count"] == 0 for c in crm.board(db)["columns"].values())
    ids = [mkperson(db, f"P{i}", f"u{i}", "Acme") for i in range(5)]
    camp = mkcamp(db, ids[:4]); crm.backfill(db)
    other = mkperson(db, "Zed", "uz", "Globex"); mkcamp(db, [other]); crm.backfill(db)
    b = crm.board(db, per_stage=2)
    assert b["columns"]["new"]["count"] == 5 and len(b["columns"]["new"]["items"]) == 2
    assert crm.board(db, campaign_id=camp)["columns"]["new"]["count"] == 4
    assert crm.board(db, q="glob")["columns"]["new"]["count"] == 1
    assert crm.board(db, q="%")["columns"]["new"]["count"] == 0  # LIKE wildcards are literal


# ---- event intake
def test_events_apply_stage_bounce_unsubscribe_and_unknown(db):
    a = mkperson(db, "A", "u1"); mkcamp(db, [a]); crm.backfill(db)
    db.insert("contact_points", {"person_id": a, "kind": "email", "value": "a@x.com", "provider": "x", "verification": "valid", "is_primary": 1})
    crm.refresh_readiness(db, a)
    assert crm.ingest_event(db, "email_sent", "A@X.com")["matched"] and stage(db, a) == "contacted"
    crm.ingest_event(db, "reply", "a@x.com"); assert stage(db, a) == "replied"
    crm.ingest_event(db, "email_sent", "a@x.com"); assert stage(db, a) == "replied"  # never backwards
    assert crm.ingest_event(db, "bounce", "a@x.com")["matched"]
    assert db.one("SELECT verification FROM contact_points WHERE person_id=?", (a,))["verification"] == "invalid"
    assert crm.ingest_event(db, "bounce", "nobody@x.com") == {"matched": False}
    assert db.one("SELECT COUNT(*) AS n FROM lead_events WHERE person_id IS NULL")["n"] == 1
    crm.ingest_event(db, "unsubscribe", "a@x.com")
    assert db.one("SELECT do_not_contact FROM people WHERE id=?", (a,))["do_not_contact"] == 1
    with pytest.raises(ValueError):
        crm.ingest_event(db, "explode", "a@x.com")


def test_bounce_demotes_ready_without_valid_email(db):
    a = mkperson(db, "A", "u1"); mkcamp(db, [a]); crm.backfill(db)
    db.insert("contact_points", {"person_id": a, "kind": "email", "value": "a@x.com", "provider": "x", "verification": "valid", "is_primary": 1})
    crm.refresh_readiness(db, a); assert stage(db, a) == "ready"
    crm.ingest_event(db, "bounce", "a@x.com"); assert stage(db, a) == "enriched"


# ---- erase
def test_erase_removes_all_crm_data(db):
    me = user(db); a = mkperson(db, "A", "u1"); mkcamp(db, [a]); crm.backfill(db)
    crm.add_note(db, a, "n", me); crm.create_task(db, a, "t", None, me, me)
    E.erase_person(db, a)
    for t in ("crm_leads", "notes", "tasks", "lead_events", "people"):
        assert db.one(f"SELECT COUNT(*) AS n FROM {t}")["n"] == 0


# ---- web
@pytest.fixture
def wapp():
    s = Settings(testing=True, admin_email="admin@example.com", admin_password=PW, crm_event_token="tok123")
    return create_app(s, Database("sqlite:///:memory:"), http_get=routes(), provider=FakeProvider(lambda p: ok(f"{p['id']}@x.com")))


def logged(app):
    c = Client(app); c.login(); return c


def seed(app):
    db = app.config["DB"]; a = mkperson(db, "Asha", "u1", "Acme"); mkcamp(db, [a]); crm.backfill(db); return db, a


def test_web_requires_login_everywhere(wapp):
    anon = Client(wapp)
    for path in ("/api/crm/board", "/api/crm/stages", "/api/crm/tasks", "/api/crm/users", "/api/crm/leads/1"):
        assert anon.get(path).status_code == 401
    assert anon.get("/pipeline").status_code == 302 and anon.get("/tasks").status_code == 302


def test_web_stage_notes_tasks_flow(wapp):
    c = logged(wapp); db, a = seed(wapp)
    assert [s["key"] for s in c.get("/api/crm/stages").get_json()["stages"]][:3] == ["new", "enriched", "ready"]
    assert c.get("/api/crm/board").get_json()["columns"]["new"]["count"] == 1
    assert c.post(f"/api/crm/leads/{a}/stage", json={"stage": "lost"}).status_code == 400
    r = c.post(f"/api/crm/leads/{a}/stage", json={"stage": "lost", "reason": "no budget"}).get_json()
    assert r == {"changed": True, "stage": "lost"}
    assert c.post(f"/api/crm/leads/{a}/notes", json={"body": "hello"}).status_code == 201
    assert c.post(f"/api/crm/leads/{a}/notes", json={"body": ""}).status_code == 400
    assert c.post(f"/api/crm/leads/{a}/owner", json={"owner": "admin@example.com"}).status_code == 200
    assert c.post(f"/api/crm/leads/{a}/owner", json={"owner": "x@y.com"}).status_code == 400
    tid = c.post("/api/crm/tasks", json={"person_id": a, "title": "Call", "due_at": "2026-12-01"}).get_json()["id"]
    assert c.post("/api/crm/tasks", json={"title": "x"}).status_code == 400
    assert c.get("/api/crm/tasks?mine=1&today=2026-11-17").get_json()["items"][0]["title"] == "Call"
    assert c.get("/api/crm/tasks?status=bogus").status_code == 400 and c.get("/api/crm/tasks?today=nope").status_code == 400
    r = c.c.patch(f"/api/crm/tasks/{tid}", json={"done": True}, headers=c.h())
    assert r.status_code == 200 and r.get_json()["done_at"]
    assert c.c.patch("/api/crm/tasks/999", json={"done": True}, headers=c.h()).status_code == 404
    d = c.get(f"/api/crm/leads/{a}").get_json()
    assert d["crm"]["stage"] == "lost" and d["notes"][0]["body"] == "hello" and d["tasks"][0]["done_at"] and len(d["timeline"]) >= 4
    assert c.get("/api/crm/leads/999").status_code == 404


def test_web_event_intake_token_rules(wapp):
    db, a = seed(wapp)
    db.insert("contact_points", {"person_id": a, "kind": "email", "value": "asha@x.com", "provider": "x", "verification": "valid"})
    anon = wapp.test_client()
    body = {"type": "reply", "email": "asha@x.com"}
    assert anon.post("/api/crm/events", json=body).status_code == 401
    assert anon.post("/api/crm/events", json=body, headers={"X-Event-Token": "wrong"}).status_code == 401
    r = anon.post("/api/crm/events", json=body, headers={"X-Event-Token": "tok123"})
    assert r.status_code == 200 and r.get_json()["matched"] is True and stage(db, a) == "replied"
    assert anon.post("/api/crm/events", json={"type": "boom", "email": "x"}, headers={"X-Event-Token": "tok123"}).status_code == 400
    off = create_app(Settings(testing=True, admin_email="a@b.c", admin_password=PW), Database("sqlite:///:memory:"))
    assert off.test_client().post("/api/crm/events", json=body, headers={"X-Event-Token": ""}).status_code == 404


def test_dashboard_sidebar_gets_new_links_even_if_file_is_old(wapp):
    c = logged(wapp)
    html = c.get("/").data.decode("utf-8")
    assert "['/pipeline','CRM']" in html and "['/tasks','Tasks']" in html
    assert c.get("/pipeline").status_code == 200 and c.get("/tasks").status_code == 200


def test_board_tool_filter_card_facts_and_summary(db):
    import json as _j
    a, b = mkperson(db, "Ana", "ua", "Acme"), mkperson(db, "Ben", "ub", "Globex")
    mkcamp(db, [a, b]); crm.backfill(db)
    db.insert("lead_sources", {"tool": "talentpulse", "kind": "person", "source_key": "k1", "person_id": a, "raw_payload": _j.dumps({})})
    db.insert("contact_points", {"person_id": b, "kind": "phone", "value": "+10", "provider": "rr"})
    run = db.insert("enrichment_runs", {"campaign_id": 1, "provider": "rr", "status": "done"})
    db.insert("enrichment_items", {"run_id": run, "person_id": a, "provider": "rr", "status": "not_found"})
    crm.set_stage(db, b, "won", "me@x.com")
    full = crm.board(db)
    ana = full["columns"]["new"]["items"][0]
    assert ana["tools"] == ["talentpulse"] and ana["enrich_status"] == "not_found" and ana["has_phone"] == 0
    assert full["columns"]["won"]["items"][0]["has_phone"] == 1
    assert full["summary"]["won_this_month"] == 1 and full["summary"]["open"] == 1 and full["summary"]["closed"] == 1
    only = crm.board(db, tool="talentpulse")
    assert only["columns"]["new"]["count"] == 1 and only["columns"]["won"]["count"] == 0


def test_bulk_add_to_crm(wapp):
    c = logged(wapp); db = wapp.config["DB"]
    a, b = mkperson(db, "Cy", "uc", "Acme"), mkperson(db, "Di", "ud", "Acme")
    r = c.post("/api/crm/bulk/add", json={"person_ids": [a, b, 99999]}).get_json()
    assert r == {"added": 2, "already": 0, "skipped": 1}
    assert c.post("/api/crm/bulk/add", json={"person_ids": [a]}).get_json()["already"] == 1
    assert c.post("/api/crm/bulk/add", json={"person_ids": "x"}).status_code == 400
    assert c.get("/api/crm/board").get_json()["columns"]["new"]["count"] == 2
