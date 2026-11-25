"""Leads page UX: suggestions, sorting, new filters, contact-quality score, saved views, stale flags, bulk owner."""
import json
from datetime import datetime, timedelta, timezone
import pytest
from app import crm, leads as L
from tests.test_phase2 import mkperson, mkcamp
from tests.test_crm import wapp, logged, user  # noqa: F401  (fixtures)


def person(db, name, company=None, title=None, location=None, email=None, phone=None, url=None):
    cid = None
    if company:
        r = db.one("SELECT id FROM companies WHERE name=?", (company,))
        cid = r["id"] if r else db.insert("companies", {"name": company, "name_key": company.lower()})
    return db.insert("people", {"full_name": name, "company_id": cid, "title": title, "location": location, "email": email, "phone": phone,
                                "linkedin_url": url or f"https://l.in/{name}", "lead_type": "employer_contact"})


# ---------------------------------------------------------------- suggestions
def test_suggest_prefix_first_then_by_count_and_escapes(db):
    for i in range(3):
        person(db, f"A{i}", "Zeta Labs")
    person(db, "B", "Beta Zeta"); person(db, "C", "Zed Inc"); person(db, "D", "100% Pure")
    got = [r["value"] for r in L.suggest(db, "company", "ze")]
    assert got == ["Zeta Labs", "Zed Inc", "Beta Zeta"]          # prefix matches first, busiest first
    assert L.suggest(db, "company", "ze")[0]["count"] == 3
    assert [r["value"] for r in L.suggest(db, "company", "%")] == ["100% Pure"]   # % is literal, not a wildcard
    assert L.suggest(db, "company", "nomatch") == []


def test_suggest_empty_query_returns_top_values_and_respects_limit(db):
    for i in range(12):
        person(db, f"P{i}", f"Co{i:02d}", location="Pune")
    assert len(L.suggest(db, "company", "", 5)) == 5
    assert L.suggest(db, "location", "")[0] == {"value": "Pune", "count": 12}


def test_suggest_all_groups_and_rejects_bad_field(db):
    person(db, "Asha Rao", "Acme", "Recruiter", "Pune")
    kinds = {r["field"] for r in L.suggest(db, "all", "a")}
    assert {"name", "company"} <= kinds
    with pytest.raises(ValueError):
        L.suggest(db, "password", "x")


def test_suggest_endpoint_needs_login_and_validates(wapp):
    assert wapp.test_client().get("/api/leads/suggest?field=company").status_code == 401
    c = logged(wapp)
    person(wapp.config["DB"], "Asha", "Acme")
    assert c.get("/api/leads/suggest?field=company&q=ac").get_json()["items"][0]["value"] == "Acme"
    assert c.get("/api/leads/suggest?field=bogus").status_code == 400


# ---------------------------------------------------------------- sort + filters
def test_sorting_whitelist_and_direction(db):
    person(db, "bob", "Zed"); person(db, "Alice", "Acme"); person(db, "carl", "Mid")
    names = lambda **k: [r["full_name"] for r in L.list_leads(db, {}, **k)["items"]]
    assert names(sort="name", direction="asc") == ["Alice", "bob", "carl"]       # case-insensitive
    assert names(sort="company", direction="desc") == ["bob", "carl", "Alice"]
    with pytest.raises(ValueError):
        L.list_leads(db, {}, sort="p.id; DROP TABLE people")
    with pytest.raises(ValueError):
        L.list_leads(db, {}, sort="name", direction="sideways")


def test_title_stage_owner_and_dnc_filters(db):
    a = person(db, "A", title="Head of Talent"); b = person(db, "B", title="Engineer"); c = person(db, "C", title="Recruiter")
    mkcamp(db, [a, c]); crm.backfill(db); user(db, "o@x.com")
    crm.set_stage(db, a, "contacted", "t"); crm.assign_owner(db, c, "o@x.com", "t")
    ids = lambda **f: sorted(r["id"] for r in L.list_leads(db, f)["items"])
    assert ids(title="talent") == [a]
    assert ids(stage="contacted") == [a] and ids(stage="new") == sorted([b, c])    # people outside the pipeline count as "new"
    assert ids(owner="o@x.com") == [c] and ids(owner="none") == sorted([a, b])
    db.execute("UPDATE people SET do_not_contact=1 WHERE id=?", (b,))
    assert ids(dnc="exclude") == sorted([a, c]) and ids(dnc="only") == [b]
    for bad in ({"stage": "nope"}, {"quality": "x"}, {"dnc": "maybe"}):
        with pytest.raises(ValueError):
            L.list_leads(db, bad)


def test_rows_carry_stage_owner_and_quality(db):
    a = person(db, "A"); mkcamp(db, [a]); crm.backfill(db)
    r = L.list_leads(db, {})["items"][0]
    assert r["stage"] == "new" and r["owner"] is None and r["quality"] == "low" and r["score"] == 0


# ---------------------------------------------------------------- contact quality
def test_contact_score_rules(db):
    none = person(db, "None")
    plain = person(db, "Plain", email="p@x.com")                           # unverified email only -> 20
    verified = person(db, "Verified", email="v@x.com")
    db.insert("contact_points", {"person_id": verified, "kind": "email", "value": "v@x.com", "provider": "rr", "verification": "valid", "is_primary": 1})
    both = person(db, "Both", email="b@x.com", phone="+91 90000 00000")
    db.insert("contact_points", {"person_id": both, "kind": "email", "value": "b@x.com", "provider": "rr", "verification": "valid", "is_primary": 1})
    bad = person(db, "Bad", email="bad@x.com")
    db.insert("contact_points", {"person_id": bad, "kind": "email", "value": "bad@x.com", "provider": "rr", "verification": "invalid", "is_primary": 1})
    office = person(db, "Office")
    db.insert("contact_points", {"person_id": office, "kind": "company_phone", "value": "+91 80 1111", "provider": "rr", "is_primary": 1})
    multi = person(db, "Multi")
    db.insert("lead_sources", {"tool": "talentpulse", "kind": "lead", "source_key": "1", "person_id": multi})
    db.insert("lead_sources", {"tool": "jobharvest", "kind": "lead", "source_key": "2", "person_id": multi})
    score = {r["full_name"]: (r["score"], r["quality"]) for r in L.list_leads(db, {})["items"]}
    assert score == {"None": (0, "low"), "Plain": (20, "medium"), "Verified": (50, "high"), "Both": (85, "high"),
                     "Bad": (0, "low"), "Office": (10, "low"), "Multi": (15, "low")}
    q = lambda band: sorted(r["full_name"] for r in L.list_leads(db, {"quality": band})["items"])
    assert q("high") == ["Both", "Verified"] and q("medium") == ["Plain"] and q("low") == ["Bad", "Multi", "None", "Office"]
    assert [r["full_name"] for r in L.list_leads(db, {}, sort="score", direction="desc")["items"]][:2] == ["Both", "Verified"]


def test_total_matches_filtered_count_with_joins(db):
    for i in range(5):
        pid = person(db, f"P{i}", "Acme")
        db.insert("contact_points", {"person_id": pid, "kind": "email", "value": f"{i}@x.com", "provider": "rr", "verification": "valid", "is_primary": 1})
    mkcamp(db, [1, 2]); crm.backfill(db)
    d = L.list_leads(db, {"quality": "high"}, page_size=2)
    assert d["total"] == 5 and len(d["items"]) == 2          # joins never duplicate rows


# ---------------------------------------------------------------- saved views
def test_saved_views_private_shared_and_permissions(wapp):
    db = wapp.config["DB"]; user(db, "ravi@x.com")
    admin = logged(wapp)
    r = admin.post("/api/views", json={"name": "Hot TA leads", "filters": {"quality": "high", "title": "talent", "sort": "score", "dir": "desc"}})
    assert r.status_code == 201
    admin.post("/api/views", json={"name": "Team view", "filters": {"stage": "ready"}, "shared": True})
    names = [v["name"] for v in admin.get("/api/views").get_json()["items"]]
    assert names == ["Hot TA leads", "Team view"]
    # a second user sees only the shared one
    db.execute("UPDATE users SET password_hash=? WHERE email='ravi@x.com'", (__import__("app.auth", fromlist=["x"]).hash_password("ravipass123"),))
    from tests.test_web import Client
    ravi = Client(wapp); assert ravi.login("ravi@x.com", "ravipass123").status_code == 200
    assert [v["name"] for v in ravi.get("/api/views").get_json()["items"]] == ["Team view"]
    shared_id = ravi.get("/api/views").get_json()["items"][0]["id"]
    assert ravi.delete(f"/api/views/{shared_id}").status_code == 403           # not the creator, not admin
    assert admin.delete(f"/api/views/{shared_id}").status_code == 200
    assert admin.delete(f"/api/views/{shared_id}").status_code == 404


def test_saved_view_validation(wapp):
    c = logged(wapp)
    for body in ({"name": "", "filters": {}}, {"name": "x" * 61, "filters": {}}, {"name": "bad", "filters": {"stage": "zzz"}},
                 {"name": "bad", "filters": {"sort": "evil"}}, {"name": "bad", "page": "admin", "filters": {}}):
        assert c.post("/api/views", json=body).status_code == 400, body
    assert c.post("/api/views", json={"name": "Dup", "filters": {}}).status_code == 201
    assert c.post("/api/views", json={"name": "dup", "filters": {}}).status_code == 400      # same name, any case
    stored = json.loads(wapp.config["DB"].one("SELECT filters FROM saved_views")["filters"])
    assert stored == {}
    assert c.post("/api/views", json={"name": "Keys", "filters": {"company": "Acme", "junk": "x"}}).status_code == 201
    assert json.loads(wapp.config["DB"].query("SELECT filters FROM saved_views WHERE name='Keys'")[0]["filters"]) == {"company": "Acme"}   # unknown keys dropped


def test_views_require_login(wapp):
    assert wapp.test_client().get("/api/views").status_code == 401


# ---------------------------------------------------------------- CRM extras
def iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_board_flags_stale_leads_by_stage(db):
    a, b, c = person(db, "Old"), person(db, "Fresh"), person(db, "NewOld")
    mkcamp(db, [a, b, c]); crm.backfill(db)
    for pid in (a, b):
        crm.set_stage(db, pid, "contacted", "t")
    crm.set_stage(db, c, "enriched", "t")
    db.execute("UPDATE lead_events SET created_at=? WHERE person_id IN (?, ?)", (iso(10), a, c))
    db.execute("UPDATE crm_leads SET stage_changed_at=? WHERE person_id=?", (iso(10), a))
    cols = crm.board(db)["columns"]
    old = next(i for i in cols["contacted"]["items"] if i["person_id"] == a); fresh = next(i for i in cols["contacted"]["items"] if i["person_id"] == b)
    assert old["stale"] and old["idle_days"] >= 10 and not fresh["stale"]
    assert cols["contacted"]["stale_after_days"] == 7
    assert cols["enriched"]["items"][0]["stale"] is False        # stages where nobody must act never go stale
    crm.add_note(db, a, "called", "t")                            # any activity resets the clock
    assert not next(i for i in crm.board(db)["columns"]["contacted"]["items"] if i["person_id"] == a)["stale"]


def test_preview_does_not_add_lead_to_pipeline(wapp):
    db = wapp.config["DB"]; pid = person(db, "Solo"); c = logged(wapp)
    d = c.get(f"/api/crm/leads/{pid}").get_json()
    assert d["crm"]["stage"] == "new" and db.one("SELECT COUNT(*) AS n FROM crm_leads")["n"] == 0
    assert c.get("/api/crm/board").get_json()["columns"]["new"]["count"] == 0
    assert c.post(f"/api/crm/leads/{pid}/stage", json={"stage": "contacted"}).status_code == 200   # acting on it adds it
    assert db.one("SELECT COUNT(*) AS n FROM crm_leads")["n"] == 1


def test_bulk_owner(wapp):
    db = wapp.config["DB"]; user(db, "o@x.com"); a, b = person(db, "A"), person(db, "B"); c = logged(wapp)
    r = c.post("/api/crm/bulk/owner", json={"person_ids": [a, b, 999], "owner": "o@x.com"}).get_json()
    assert r == {"updated": 2, "skipped": 1}
    assert {x["owner_email"] for x in db.query("SELECT owner_email FROM crm_leads")} == {"o@x.com"}
    assert c.post("/api/crm/bulk/owner", json={"person_ids": [a], "owner": "ghost@x.com"}).status_code == 400
    assert c.post("/api/crm/bulk/owner", json={"person_ids": [], "owner": ""}).status_code == 400
    assert c.post("/api/crm/bulk/owner", json={"person_ids": list(range(501)), "owner": ""}).status_code == 400
    assert c.post("/api/crm/bulk/owner", json={"person_ids": [a], "owner": ""}).status_code == 200      # unassign
    assert wapp.test_client().post("/api/crm/bulk/owner", json={"person_ids": [a]}).status_code in (401, 403)


def test_favicon_is_quiet_and_export_still_works(wapp):
    c = logged(wapp); person(wapp.config["DB"], "Asha", "Acme")
    assert c.get("/favicon.ico").status_code == 204
    assert c.get("/api/leads/export.csv?quality=low&stage=new").status_code == 200
    assert c.get("/api/leads?sort=company&dir=asc").status_code == 200 and c.get("/api/leads?sort=zzz").status_code == 400
