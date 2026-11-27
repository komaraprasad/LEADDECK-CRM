import json
import pytest
from app import enrich as E
from app.config import Settings
from app.db import Database
from app.merge import upsert_records
from app.providers.base import ContactValue, ProviderError, ProviderResult
from app.providers.fake import FakeProvider
from app.providers.rocketreach import RocketReach, parse_profile
from app.records import NormalizedRecord
from app.web import create_app
from tests.test_web import Client, PW, routes


def mkperson(db, name, url=None, company=None):
    cid = db.insert("companies", {"name": company, "name_key": company.lower()}) if company else None
    return db.insert("people", {"full_name": name, "linkedin_url": url, "company_id": cid, "lead_type": "candidate"})


def mkcamp(db, pids):
    c = db.insert("campaigns", {"name": "c"})
    for p in pids:
        db.insert("campaign_leads", {"campaign_id": c, "person_id": p})
    return c


def ok(email="a@x.com", v="valid", phone=None):
    return ProviderResult("matched", [ContactValue(email, v, "A")], [ContactValue(phone)] if phone else [], credits_used=1)


# ---- schema
def test_schema_v2_tables_and_columns(db):
    names = db.table_names()
    assert {"contact_points", "enrichment_runs", "enrichment_items", "credit_ledger", "erasures"} <= names
    pid = mkperson(db, "A", "u1")
    assert db.one("SELECT do_not_contact FROM people WHERE id=?", (pid,))["do_not_contact"] == 0


# ---- RocketReach adapter (offline, fixture shapes are ASSUMED)
def rr(responses, key="SECRETKEY123"):
    seen = []
    def http(url, headers):
        seen.append((url, headers)); r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    return RocketReach(key, http=http, sleep=lambda s: None, max_wait=9, poll=3), seen


def test_parse_profile_verification_mapping():
    r = parse_profile({"emails": [{"email": "A@X.com", "smtp_valid": "valid", "grade": "A"}, {"email": "b@x.com", "smtp_valid": "inconclusive"},
                                  {"email": "c@x.com", "smtp_valid": "invalid"}, {"email": "d@x.com"}, {"email": "nope"}], "phones": [{"number": " +91 99 "}]})
    assert [(e.value, e.verification) for e in r.emails] == [("a@x.com", "valid"), ("b@x.com", "unknown"), ("c@x.com", "invalid"), ("d@x.com", "unverified")]
    assert r.phones[0].value == "+91 99" and r.credits_used == 1 and r.status == "matched"
    assert parse_profile({"emails": [], "phones": []}).status == "not_found"


def test_rocketreach_polls_until_complete_and_sends_key_header_only():
    p, seen = rr([(200, json.dumps({"id": 7, "status": "searching"})), (200, json.dumps({"id": 7, "status": "searching"})),
                  (200, json.dumps([{"id": 7, "status": "complete", "emails": [{"email": "a@x.com", "smtp_valid": "valid"}]}]))])
    res = p.lookup({"linkedin_url": "https://www.linkedin.com/in/a"})
    assert res.status == "matched" and len(seen) == 3
    assert all("SECRETKEY123" not in url for url, _ in seen) and seen[0][1] == {"Api-Key": "SECRETKEY123"}
    assert "linkedin_url=" in seen[0][0] and "checkStatus" in seen[1][0]


def test_rocketreach_timeout_is_error_not_exception():
    p, _ = rr([(200, json.dumps({"id": 1, "status": "searching"}))] * 10)
    res = p.lookup({"linkedin_url": "u"})
    assert res.status == "error" and "timed out" in res.error


def test_rocketreach_errors_never_leak_key_and_missing_key_blocks():
    for status, word in ((401, "API access"), (429, "rate limit")):
        p, _ = rr([(status, "{}")])
        with pytest.raises(ProviderError) as e:
            p.lookup({"linkedin_url": "u"})
        assert word in str(e.value) and "SECRETKEY123" not in str(e.value)
    p, _ = rr([RuntimeError("boom SECRETKEY123 in url")])
    with pytest.raises(ProviderError) as e:
        p.lookup({"linkedin_url": "u"})
    assert "SECRETKEY123" not in str(e.value)
    assert RocketReach("").configured() is False
    with pytest.raises(ProviderError):
        RocketReach("").lookup({"linkedin_url": "u"})


def test_rocketreach_unmatchable_person_is_error_result():
    p, seen = rr([])
    assert p.lookup({"full_name": "Solo"}).status == "error" and not seen


# ---- orchestration
def test_run_stores_contacts_fills_blank_only_and_logs_ledger(db):
    a = mkperson(db, "A", "u1", "Acme")
    b = mkperson(db, "B", "u2", "Acme"); db.update("people", b, {"email": "existing@x.com"})
    c = mkcamp(db, [a, b])
    prov = FakeProvider({a: ok("new@x.com", phone="+91 1"), b: ok("other@x.com")})
    run = E.run_enrichment(db, prov, c, credit_cap=10, lead_cap=10)
    assert run["status"] == "ok" and run["matched"] == 2 and run["credits_used"] == 2
    assert db.one("SELECT email, phone FROM people WHERE id=?", (a,)) == {"email": "new@x.com", "phone": "+91 1"}
    assert db.one("SELECT email FROM people WHERE id=?", (b,))["email"] == "existing@x.com"  # never overwritten
    assert db.one("SELECT COUNT(*) AS n FROM contact_points WHERE person_id=?", (b,))["n"] == 1  # but recorded
    assert db.one("SELECT SUM(credits) AS n FROM credit_ledger")["n"] == 2


def test_unverified_email_is_stored_but_not_copied_to_person(db):
    a = mkperson(db, "A", "u1")
    E.run_enrichment(db, FakeProvider({a: ok("maybe@x.com", v="unverified")}), mkcamp(db, [a]), 5, 5)
    assert db.one("SELECT email FROM people WHERE id=?", (a,))["email"] is None
    assert db.one("SELECT verification FROM contact_points WHERE person_id=?", (a,))["verification"] == "unverified"


def test_primary_prefers_valid_over_unknown(db):
    a = mkperson(db, "A", "u1")
    res = ProviderResult("matched", [ContactValue("u@x.com", "unknown", "A"), ContactValue("v@x.com", "valid", "B"), ContactValue("i@x.com", "invalid")], credits_used=1)
    E.run_enrichment(db, FakeProvider({a: res}), mkcamp(db, [a]), 5, 5)
    prim = db.query("SELECT value FROM contact_points WHERE person_id=? AND is_primary=1", (a,))
    assert [r["value"] for r in prim] == ["v@x.com"]


def _primary(db, pid):
    return [r["value"] for r in db.query("SELECT value FROM contact_points WHERE person_id=? AND kind='email' AND is_primary=1", (pid,))]


@pytest.mark.parametrize("order", [0, 1])
def test_primary_prefers_work_email_over_personal_on_tie(db, order):
    a = mkperson(db, "Sam B", "u1", "Example Corp")
    vals = [ContactValue("sb@gmail.com", "valid", "A"), ContactValue("sb@examplecorp-labs.com", "valid", "A")]
    if order:
        vals.reverse()
    E.run_enrichment(db, FakeProvider({a: ProviderResult("matched", vals, credits_used=1)}), mkcamp(db, [a]), 5, 5)
    assert _primary(db, a) == ["sb@examplecorp-labs.com"]
    assert db.one("SELECT email FROM people WHERE id=?", (a,))["email"] == "sb@examplecorp-labs.com"


def test_primary_prefers_company_domain_over_other_work_domain(db):
    a = mkperson(db, "A", "u1", "Acme")
    db.execute("UPDATE companies SET website='https://www.acme.com/about'")
    res = ProviderResult("matched", [ContactValue("a@oldjob.com", "valid", "A"), ContactValue("a@acme.com", "valid", "A")], credits_used=1)
    E.run_enrichment(db, FakeProvider({a: res}), mkcamp(db, [a]), 5, 5)
    assert _primary(db, a) == ["a@acme.com"]


def test_personal_email_still_used_when_it_is_the_only_valid_one(db):
    a = mkperson(db, "A", "u1", "Acme")
    res = ProviderResult("matched", [ContactValue("a@gmail.com", "valid", "A"), ContactValue("a@acme.com", "invalid", "F")], credits_used=1)
    E.run_enrichment(db, FakeProvider({a: res}), mkcamp(db, [a]), 5, 5)
    assert _primary(db, a) == ["a@gmail.com"]


def test_repick_fixes_personal_primary_but_keeps_manual_email(db):
    a = mkperson(db, "A", "u1", "Acme")
    b = mkperson(db, "B", "u2", "Acme")
    for pid, email in ((a, "a@gmail.com"), (b, "typed-by-user@yahoo.com")):
        db.update("people", pid, {"email": email})
    db.insert("contact_points", {"person_id": a, "kind": "email", "value": "a@gmail.com", "provider": "rocketreach", "provider_grade": "A", "verification": "valid", "is_primary": 1})
    db.insert("contact_points", {"person_id": a, "kind": "email", "value": "a@acme.com", "provider": "rocketreach", "provider_grade": "A", "verification": "valid", "is_primary": 0})
    db.insert("contact_points", {"person_id": b, "kind": "email", "value": "b@acme.com", "provider": "rocketreach", "provider_grade": "A", "verification": "valid", "is_primary": 1})
    changed = E.repick_primary_emails(db)
    assert changed == [a]
    assert _primary(db, a) == ["a@acme.com"] and db.one("SELECT email FROM people WHERE id=?", (a,))["email"] == "a@acme.com"
    assert db.one("SELECT email FROM people WHERE id=?", (b,))["email"] == "typed-by-user@yahoo.com"  # never came from enrichment
    assert E.repick_primary_emails(db) == []  # idempotent


def test_cap_stops_exactly_and_no_extra_calls(db):
    ids = [mkperson(db, f"P{i}", f"u{i}") for i in range(5)]
    prov = FakeProvider(lambda p: ok(f"{p['id']}@x.com"))
    run = E.run_enrichment(db, prov, mkcamp(db, ids), credit_cap=2, lead_cap=10)
    assert run["status"] == "capped" and run["credits_used"] == 2 and len(prov.calls) == 2


def test_lead_cap_limits_people(db):
    ids = [mkperson(db, f"P{i}", f"u{i}") for i in range(5)]
    prov = FakeProvider(lambda p: ok(f"{p['id']}@x.com"))
    run = E.run_enrichment(db, prov, mkcamp(db, ids), credit_cap=50, lead_cap=3)
    assert run["status"] == "ok" and len(prov.calls) == 3


def test_rerun_skips_already_enriched_and_costs_nothing(db):
    a, b = mkperson(db, "A", "u1"), mkperson(db, "B", "u2")
    c = mkcamp(db, [a, b])
    prov = FakeProvider({a: ok("a@x.com"), b: ProviderResult("not_found")})
    E.run_enrichment(db, prov, c, 10, 10)
    prov2 = FakeProvider(lambda p: ok("zzz@x.com"))
    est = E.estimate(db, c, prov2)
    assert est["would_look_up"] == 0 and est["estimated_credits"] == 0 and est["skipped"]["already_enriched"] == 2
    run = E.run_enrichment(db, prov2, c, 10, 10)
    assert run["credits_used"] == 0 and prov2.calls == []


def test_skips_dnc_unmatchable_and_already_verified(db):
    dnc = mkperson(db, "D", "u1"); E.set_do_not_contact(db, dnc, True, "asked")
    nomatch = mkperson(db, "Solo")  # no URL, no company
    have = mkperson(db, "H", "u3"); db.insert("contact_points", {"person_id": have, "kind": "email", "value": "h@x.com", "provider": "x", "verification": "valid"})
    good = mkperson(db, "G", "u4")
    prov = FakeProvider(lambda p: ok("g@x.com"))
    c = mkcamp(db, [dnc, nomatch, have, good])
    est = E.estimate(db, c, prov)
    assert est["skipped"] == {"do_not_contact": 1, "already_enriched": 0, "has_verified_email": 1, "cannot_match": 1} and est["would_look_up"] == 1
    E.run_enrichment(db, prov, c, 10, 10)
    assert prov.calls == [good]


def test_name_and_company_is_enough_to_match(db):
    a = mkperson(db, "Name", None, "Acme")
    assert E.estimate(db, mkcamp(db, [a]), FakeProvider())["would_look_up"] == 1


def test_per_person_error_continues_and_is_not_charged(db):
    a, b = mkperson(db, "A", "u1"), mkperson(db, "B", "u2")
    prov = FakeProvider({a: RuntimeError("weird"), b: ok("b@x.com")})
    run = E.run_enrichment(db, prov, mkcamp(db, [a, b]), 10, 10)
    assert run["errors"] == 1 and run["matched"] == 1 and run["credits_used"] == 1


def test_provider_error_stops_run_as_failed(db):
    a, b = mkperson(db, "A", "u1"), mkperson(db, "B", "u2")
    prov = FakeProvider({a: ProviderError("plan has no API access"), b: ok("b@x.com")})
    run = E.run_enrichment(db, prov, mkcamp(db, [a, b]), 10, 10)
    assert run["status"] == "failed" and "API access" in run["note"] and prov.calls == [a]


def test_unconfigured_provider_refuses_run_but_estimate_works(db):
    a = mkperson(db, "A", "u1"); c = mkcamp(db, [a])
    prov = FakeProvider(configured=False)
    assert E.estimate(db, c, prov)["configured"] is False
    with pytest.raises(ProviderError):
        E.run_enrichment(db, prov, c, 5, 5)


# ---- compliance
def test_erase_removes_everything_and_sync_cannot_recreate(db):
    recs = [NormalizedRecord("talentpulse", "person", "k1", {}, {"full_name": "Zed", "linkedin_url": "https://www.linkedin.com/in/zed", "email": "z@x.com"})]
    upsert_records(db, recs)
    pid = db.one("SELECT id FROM people")["id"]
    db.insert("contact_points", {"person_id": pid, "kind": "email", "value": "z@x.com", "provider": "x"})
    mkcamp(db, [pid])
    E.erase_person(db, pid)
    for t in ("people", "contact_points", "lead_sources", "campaign_leads"):
        assert db.one(f"SELECT COUNT(*) AS n FROM {t}")["n"] == 0
    res = upsert_records(db, recs)
    assert db.one("SELECT COUNT(*) AS n FROM people")["n"] == 0 and res.skipped == 1 and not res.errors
    with pytest.raises(KeyError):
        E.erase_person(db, 999)


# ---- web
@pytest.fixture
def wapp():
    s = Settings(testing=True, admin_email="admin@example.com", admin_password=PW)
    prov = FakeProvider(lambda p: ok(f"{p['id']}@x.com"))
    app = create_app(s, Database("sqlite:///:memory:"), http_get=routes(), provider=prov)
    return app, prov


def logged(app):
    c = Client(app); c.login(); return c


def test_web_requires_login_and_confirm(wapp):
    app, prov = wapp
    anon = Client(app)
    assert anon.get("/api/enrich/config").status_code == 401
    c = logged(app); db = app.config["DB"]
    a = mkperson(db, "A", "u1"); cid = mkcamp(db, [a])
    est = c.post(f"/api/campaigns/{cid}/enrich/estimate", json={}).get_json()
    assert est["would_look_up"] == 1 and est["estimated_credits"] == 1 and "key" not in json.dumps(est).lower()
    assert c.post(f"/api/campaigns/{cid}/enrich", json={"credit_cap": 5, "lead_cap": 5}).status_code == 400  # no confirm
    assert prov.calls == []
    r = c.post(f"/api/campaigns/{cid}/enrich", json={"confirm": True, "credit_cap": 5, "lead_cap": 5})
    assert r.status_code == 200 and r.get_json()["matched"] == 1
    assert c.get("/api/enrich/ledger").get_json()["total_credits"] == 1
    assert c.get("/api/enrich/runs").get_json()["items"][0]["matched"] == 1
    lead = c.get(f"/api/leads/{a}").get_json()
    assert lead["contact_points"][0]["verification"] == "valid"


def test_web_validation_and_missing_key(wapp):
    app, prov = wapp
    c = logged(app); db = app.config["DB"]; cid = mkcamp(db, [mkperson(db, "A", "u1")])
    assert c.post(f"/api/campaigns/{cid}/enrich", json={"confirm": True, "credit_cap": "x"}).status_code == 400
    assert c.post(f"/api/campaigns/{cid}/enrich", json={"confirm": True, "credit_cap": 0, "lead_cap": 5}).status_code == 400
    assert c.post("/api/campaigns/999/enrich/estimate", json={}).status_code == 404
    prov._configured = False
    r = c.post(f"/api/campaigns/{cid}/enrich", json={"confirm": True, "credit_cap": 5, "lead_cap": 5})
    assert r.status_code == 400 and "ROCKETREACH_API_KEY" in r.get_json()["error"]
    assert c.get("/api/enrich/config").get_json()["configured"] is False


def test_web_dnc_erase_and_export_columns(wapp):
    app, _ = wapp
    c = logged(app); db = app.config["DB"]
    a = mkperson(db, "A", "u1")
    assert c.post(f"/api/leads/{a}/do-not-contact", json={"value": True, "reason": "asked"}).status_code == 200
    rows = c.get("/api/leads/export.csv").data.decode("utf-8-sig")
    assert "Email Status" in rows and "Do Not Contact" in rows and "YES" in rows
    assert c.post("/api/leads/999/do-not-contact", json={}).status_code == 404
    assert c.delete(f"/api/leads/{a}").status_code == 200 and c.delete(f"/api/leads/{a}").status_code == 404


# ---- diagnostics + re-check
def test_shape_has_no_values_and_scalar_phone_fallback():
    from app.providers.rocketreach import shape_of
    prof = {"id": 5, "status": "complete", "emails": [{"email": "secret@x.com", "smtp_valid": "valid"}], "current_work_phone": "+91 123"}
    r = parse_profile(prof)
    assert "secret@x.com" not in r.shape and "+91 123" not in r.shape and "smtp_valid" in r.shape
    assert [p.value for p in r.phones] == ["+91 123"]
    assert shape_of({"a": [1], "b": None}) == {"a": ["int"], "b": "null"}


def test_recheck_one_person_ignores_done_and_stores_shape(db):
    a, b = mkperson(db, "A", "u1"), mkperson(db, "B", "u2")
    c = mkcamp(db, [a, b])
    res = ProviderResult("matched", [ContactValue("a@x.com", "valid")], credits_used=1, shape='{"emails": []}')
    prov = FakeProvider({a: res, b: res})
    E.run_enrichment(db, prov, c, 10, 10)
    prov.calls.clear()
    assert E.estimate(db, c, prov)["would_look_up"] == 0
    run = E.run_enrichment(db, prov, c, 1, 1, person_ids=[a], include_done=True)
    assert prov.calls == [a] and run["credits_used"] == 1
    assert db.one("SELECT raw_shape FROM enrichment_items WHERE person_id=? ORDER BY id DESC", (a,))["raw_shape"] == '{"emails": []}'


def test_web_recheck_and_items(wapp):
    app, prov = wapp
    c = logged(app); db = app.config["DB"]
    a = mkperson(db, "A", "u1"); cid = mkcamp(db, [a])
    c.post(f"/api/campaigns/{cid}/enrich", json={"confirm": True, "credit_cap": 5, "lead_cap": 5})
    prov.calls.clear()
    r = c.post(f"/api/campaigns/{cid}/enrich", json={"confirm": True, "credit_cap": 1, "lead_cap": 1, "person_ids": [a], "include_done": True})
    assert r.status_code == 200 and prov.calls == [a]
    assert c.post(f"/api/campaigns/{cid}/enrich", json={"confirm": True, "person_ids": "x"}).status_code == 400
    assert len(c.get(f"/api/enrich/items?person_id={a}").get_json()["items"]) == 2
    assert c.get("/api/enrich/items").status_code == 400


def test_employer_phone_saved_as_company_phone_not_direct(db):
    prof = {"emails": [{"email": "a@x.com", "smtp_valid": "valid"}], "phones": [],
            "current_employer_phone": {"number": "+91 65028 52394", "grade": "B", "validity": "valid", "type": "office"}}
    r = parse_profile(prof)
    assert r.phones == [] and [(c.value, c.verification, c.grade) for c in r.company_phones] == [("+91 65028 52394", "valid", "B")]
    a = mkperson(db, "A", "u1")
    E.run_enrichment(db, FakeProvider({a: r}), mkcamp(db, [a]), 5, 5)
    assert db.one("SELECT phone FROM people WHERE id=?", (a,))["phone"] is None  # never passed off as a direct line
    cp = db.one("SELECT kind, is_primary FROM contact_points WHERE value='+91 65028 52394'")
    assert cp == {"kind": "company_phone", "is_primary": 1}
    from app.leads import export_rows
    assert export_rows(db, ids=[a])[0]["company_phone"] == "+91 65028 52394"
    # company-phone-only profile still counts as matched
    assert parse_profile({"current_employer_phone": {"number": "1"}}).status == "matched"


def test_campaign_stats_counts_rates_and_cost(db):
    a, b, c3, d = (mkperson(db, n, f"u{i}") for i, n in enumerate("ABCD"))
    camp = mkcamp(db, [a, b, c3, d])
    both = ProviderResult("matched", [ContactValue("a@x.com", "valid")], [ContactValue("+91 1")], credits_used=1)
    comp = ProviderResult("matched", [ContactValue("b@x.com", "unknown")], [], [ContactValue("+91 2", "valid")], credits_used=1)
    prov = FakeProvider({a: both, b: comp, c3: ProviderResult("not_found"), d: ProviderResult("error", error="x")})
    E.run_enrichment(db, prov, camp, 10, 10)
    s = E.campaign_stats(db, camp)
    assert (s["looked_up"], s["matched"], s["not_found"], s["errors"], s["credits_used"]) == (4, 2, 1, 1, 2)
    assert (s["with_valid_email"], s["with_direct_phone"], s["company_phone_only"], s["no_phone_at_all"]) == (1, 1, 1, 2)
    assert s["match_rate"] == 0.5 and s["valid_email_rate"] == 0.25 and s["credits_per_valid_email"] == 2.0
    assert E.campaign_stats(db, mkcamp(db, []))["looked_up"] == 0


def test_web_stats_endpoint(wapp):
    app, _ = wapp
    c = logged(app); db = app.config["DB"]
    cid = mkcamp(db, [mkperson(db, "A", "u1")])
    c.post(f"/api/campaigns/{cid}/enrich", json={"confirm": True, "credit_cap": 5, "lead_cap": 5})
    s = c.get(f"/api/campaigns/{cid}/enrich/stats").get_json()
    assert s["looked_up"] == 1 and s["with_valid_email"] == 1
    assert c.get("/api/campaigns/999/enrich/stats").status_code == 404


# ---- wrong-person guard
def prof_for(name, employer, email="x@x.com"):
    r = ProviderResult("matched", [ContactValue(email, "valid")], credits_used=1)
    r.matched_name, r.matched_employer = name, employer
    return r


def test_check_identity_rules():
    p = {"full_name": "Anusha Triprayat", "company_name": "Simplotel"}
    assert E.check_identity(p, prof_for("Anusha Triprayat", "Simplotel")) == (True, None)
    assert E.check_identity(p, prof_for("TRIPRAYAT, Anusha", "simplotel hospitality"))[0] is True  # word order/case/extra words
    same, note = E.check_identity(p, prof_for("Priya Sharma", "Simplotel"))
    assert same is False and "name mismatch" in note
    same, note = E.check_identity(p, prof_for("Anusha Triprayat", "Other Corp"))
    assert same is True and "employer differs" in note
    assert E.check_identity(p, ProviderResult("matched")) == (True, None)  # nothing to compare: cannot disprove
    assert E.check_identity({"full_name": None, "company_name": None}, prof_for("A B", "C")) == (True, None)


def test_mismatch_is_charged_not_saved_and_reported(db):
    a, b, c3 = mkperson(db, "Anusha Triprayat", "u1", "Simplotel"), mkperson(db, "Real Person", "u2", "Acme"), mkperson(db, "Job Changer", "u3", "Acme")
    prov = FakeProvider({a: prof_for("Priya Sharma", "Simplotel", "wrong@x.com"), b: prof_for("Real Person", "Acme", "ok@x.com"),
                         c3: prof_for("Job Changer", "NewCo", "jc@x.com")})
    camp = mkcamp(db, [a, b, c3])
    run = E.run_enrichment(db, prov, camp, 10, 10)
    assert run["matched"] == 2 and run["errors"] == 1 and run["credits_used"] == 3 and "different person" in run["note"]
    assert db.one("SELECT COUNT(*) AS n FROM contact_points WHERE person_id=?", (a,))["n"] == 0  # wrong person's data never stored
    assert db.one("SELECT status FROM enrichment_items WHERE person_id=?", (a,))["status"] == "mismatch"
    assert "employer differs" in db.one("SELECT match_note FROM enrichment_items WHERE person_id=?", (c3,))["match_note"]
    s = E.campaign_stats(db, camp)
    assert s["wrong_person"] == 1 and s["needs_review"] == 1 and s["matched"] == 2
    assert E.estimate(db, camp, prov)["skipped"]["already_enriched"] == 3  # a wrong-person result is not retried blindly


def test_rocketreach_profile_carries_name_and_employer():
    r = parse_profile({"name": "A B", "current_employer": "Acme", "emails": [{"email": "a@x.com"}]})
    assert (r.matched_name, r.matched_employer) == ("A B", "Acme")


def test_leads_report_whether_enrichment_already_looked_them_up(db):
    from app.leads import list_leads, get_lead
    a = mkperson(db, "Never Looked", "u1", "Acme")
    b = mkperson(db, "Looked Nothing", "u2", "Acme")
    E.run_enrichment(db, FakeProvider({b: ProviderResult("not_found")}), mkcamp(db, [b]), 5, 5)
    rows = {r["id"]: r for r in list_leads(db, {})["items"]}
    assert not rows[a]["looked_up"] and rows[a]["score"] == 0
    assert rows[b]["looked_up"] and rows[b]["score"] == 0
    assert get_lead(db, b)["looked_up"] and not get_lead(db, a)["looked_up"]


# ---- full RocketReach contact data (phones need more than the default lookup)
def test_lookup_asks_for_premium_lookup_type_by_default():
    p, seen = rr([(200, json.dumps({"id": 1, "status": "complete", "emails": [{"email": "a@x.com", "smtp_valid": "valid"}]}))])
    p.lookup({"linkedin_url": "https://www.linkedin.com/in/a"})
    assert "lookup_type=premium" in seen[0][0]


def test_lookup_type_not_allowed_on_plan_falls_back_to_default_once():
    p, seen = rr([(403, "{}"), (200, json.dumps({"id": 1, "status": "complete", "emails": [{"email": "a@x.com", "smtp_valid": "valid"}]})),
                  (200, json.dumps({"id": 2, "status": "complete", "emails": [{"email": "b@x.com", "smtp_valid": "valid"}]}))])
    assert p.lookup({"linkedin_url": "u1"}).status == "matched"
    assert "lookup_type" in seen[0][0] and "lookup_type" not in seen[1][0]
    p.lookup({"linkedin_url": "u2"})
    assert "lookup_type" not in seen[2][0]  # remembered: no wasted call per lead
    assert "premium" in p.notice


def test_phone_grade_and_line_type_are_kept(db):
    prof = {"emails": [], "phones": [{"number": "+91 98", "grade": "A", "type": "mobile"}, {"number": "+91 97", "grade": "B", "type": "direct dial"}],
            "current_employer_phone": {"number": "+63 88", "grade": "B", "type": "other"}}
    r = parse_profile(prof)
    assert [(c.value, c.verification, c.grade, c.line_type) for c in r.phones] == [("+91 98", "valid", "A", "mobile"), ("+91 97", "unknown", "B", "direct dial")]
    assert r.company_phones[0].line_type == "office"
    a = mkperson(db, "A", "u1")
    E.run_enrichment(db, FakeProvider({a: r}), mkcamp(db, [a]), 5, 5)
    rows = db.query("SELECT value, line_type, is_primary FROM contact_points WHERE person_id=? AND kind='phone' ORDER BY value", (a,))
    assert rows == [{"value": "+91 97", "line_type": "direct dial", "is_primary": 0}, {"value": "+91 98", "line_type": "mobile", "is_primary": 1}]
    assert db.one("SELECT phone FROM people WHERE id=?", (a,))["phone"] == "+91 98"


def test_company_industry_and_size_are_shown_and_exported(db):
    from app.leads import list_leads, get_lead, export_rows
    from app.exporter import EXPORT_COLUMNS
    a = mkperson(db, "A", "u1", "Acme")
    db.execute("UPDATE companies SET industry='Staffing & Recruiting', size='51-200'")
    for row in (list_leads(db, {})["items"][0], get_lead(db, a), export_rows(db, ids=[a])[0]):
        assert (row["company_industry"], row["company_size"]) == ("Staffing & Recruiting", "51-200")
    labels = [label for _, label in EXPORT_COLUMNS]
    assert labels.index("Company Industry") == labels.index("Company") + 1 and "Company Size" in labels


# ---- contacts a tool gives only as a LinkedIn link (JobHarvest)
def test_url_only_contact_is_searchable_and_gets_a_display_name(db):
    from app.leads import list_leads, get_lead, name_from_linkedin
    a = mkperson(db, None, "https://www.linkedin.com/in/riya-vedpathak-5651a32a3", "Example Retail")
    b = mkperson(db, None, "https://www.linkedin.com/in/samplename", "Example Retail")
    assert name_from_linkedin("https://www.linkedin.com/in/riya-vedpathak-5651a32a3/") == "Riya Vedpathak"
    assert name_from_linkedin("https://www.linkedin.com/in/samplename") == "Samplename"
    assert name_from_linkedin("https://www.linkedin.com/in/ACwAAB12345xyz") is None  # opaque id, not a name
    assert name_from_linkedin(None) is None
    assert list_leads(db, {"q": "samplena"})["total"] == 1
    row = {r["id"]: r for r in list_leads(db, {})["items"]}[a]
    assert row["full_name"] is None and row["display_name"] == "Riya Vedpathak" and row["name_from_url"]
    assert get_lead(db, b)["display_name"] == "Samplename"
    named = mkperson(db, "Real Name", "https://www.linkedin.com/in/something-else")
    r2 = get_lead(db, named)
    assert r2["display_name"] == "Real Name" and not r2["name_from_url"]


def test_enrichment_fills_blank_name_and_title_only(db):
    a = mkperson(db, None, "https://www.linkedin.com/in/samplename", "Example Retail")
    b = mkperson(db, "Kept Name", "u2", "Acme"); db.update("people", b, {"title": "Kept Title"})
    res = lambda n, t: ProviderResult("matched", [ContactValue("x%s@x.com" % n[:1], "valid", "A")], credits_used=1, matched_name=n, matched_title=t)
    E.run_enrichment(db, FakeProvider({a: res("Sample N Ame", "Engineering Manager"), b: res("Kept Name", "Other Title")}), mkcamp(db, [a, b]), 5, 5)
    assert db.one("SELECT full_name, title FROM people WHERE id=?", (a,)) == {"full_name": "Sample N Ame", "title": "Engineering Manager"}
    assert db.one("SELECT full_name, title FROM people WHERE id=?", (b,)) == {"full_name": "Kept Name", "title": "Kept Title"}
    assert db.one("SELECT name_key FROM people WHERE id=?", (a,))["name_key"]  # name now usable for matching
    from app.providers.rocketreach import parse_profile
    assert parse_profile({"name": "A B", "current_title": "CTO", "emails": [{"email": "a@x.com"}]}).matched_title == "CTO"


REAL_SHAPE_PROFILE = {  # field names exactly as in a real RocketReach premium response (all values fictional)
    "name": "Jo Doe", "current_employer": "Acme Labs", "current_title": "Head of Engineering",
    "emails": [{"email": "jo@acme.example", "smtp_valid": "valid", "grade": "A", "type": "professional"},
               {"email": "jo.doe@gmail.com", "smtp_valid": "valid", "grade": "A", "type": "personal"},
               {"email": "old@gone.example", "smtp_valid": "invalid", "grade": "F", "type": "professional"}],
    "current_work_email": "jo@acme.example", "current_personal_email": "jo.doe@gmail.com",
    "recommended_professional_email": "jo@acme-labs.example", "recommended_personal_email": "jo.doe@gmail.com",
    "recommended_email": "jo@acme.example",
    "phones": [{"number": "+91 90000 00001", "type": "mobile", "grade": "A"}],
    "mobile_phone": "+919000000001", "current_work_phone": "+91 80 0000 0002",
    "current_employer_phone": {"number": "+91 90000 00003", "grade": "A", "validity": "valid", "type": "office"},
}


def test_rocketreach_keeps_every_email_and_phone_with_personal_work_labels():
    r = parse_profile(REAL_SHAPE_PROFILE)
    em = {c.value: c for c in r.emails}
    assert set(em) == {"jo@acme.example", "jo.doe@gmail.com", "old@gone.example", "jo@acme-labs.example"}   # scalar-only one kept
    assert em["jo@acme.example"].line_type == "work" and em["jo.doe@gmail.com"].line_type == "personal"
    assert em["jo@acme-labs.example"].line_type == "work" and em["jo@acme-labs.example"].verification == "unverified"
    ph = {c.value: c.line_type for c in r.phones}
    assert ph == {"+91 90000 00001": "mobile", "+91 80 0000 0002": "direct dial"}   # mobile_phone duplicate not repeated
    assert [c.value for c in r.company_phones] == ["+91 90000 00003"]


def test_enriched_lead_exports_all_contacts_and_work_email_is_primary(db):
    from app import leads as L
    a = mkperson(db, "Jo Doe", "https://www.linkedin.com/in/jo-doe", "Acme Labs")
    E.run_enrichment(db, FakeProvider({a: parse_profile(REAL_SHAPE_PROFILE)}), mkcamp(db, [a]), 5, 5)
    row = L.export_rows(db, ids=[a])[0]
    assert row["email"] == "jo@acme.example"                 # work beats personal at equal grade
    assert row["personal_email"] == "jo.doe@gmail.com"
    assert "jo.doe@gmail.com (personal)" in row["all_emails"] and "jo@acme-labs.example (work)" in row["all_emails"]
    assert "old@gone.example" not in row["all_emails"]             # invalid never exported as usable
    assert row["phone"] == "+91 90000 00001" and "+91 80 0000 0002 (direct dial)" in row["all_phones"]
    assert row["company_phone"] == "+91 90000 00003"


def test_jobharvest_contact_shows_job_location(db):
    from app import leads as L
    a = mkperson(db, None, "https://www.linkedin.com/in/riley", "Example Fintech")
    b = mkperson(db, "Own", "u9", "Acme"); db.update("people", b, {"location": "Chennai"})
    for pid in (a, b):
        db.insert("lead_sources", {"tool": "jobharvest", "kind": "person", "source_key": "jh:%d" % pid, "person_id": pid,
                                   "raw_payload": '{"location": "Bengaluru, Karnataka, India", "job_title": "Java Developer", "job_url": "https://www.linkedin.com/jobs/view/1/"}',
                                   "collected_at": "2026-11-26T10:00:00+00:00"})
    row = {r["id"]: r for r in L.export_rows(db, ids=[a, b])}
    assert (row[a]["display_name"], row[a]["name_source"], row[a]["hiring_for"], row[a]["job_url"]) == ("Riley", "LinkedIn link", "Java Developer", "https://www.linkedin.com/jobs/view/1/")
    assert (row[b]["display_name"], row[b]["name_source"], row[b]["hiring_for"]) == ("Own", "", "Java Developer")
    items = {r["id"]: r for r in L.list_leads(db, {})["items"]}
    assert items[a]["job_location"] == "Bengaluru, Karnataka, India" and not items[a]["location"]
    assert items[b]["job_location"] == "" and items[b]["location"] == "Chennai"   # own location wins
    assert L.get_lead(db, a)["job_location"] == "Bengaluru, Karnataka, India"


def test_enrichment_fills_blank_location_only(db):
    from app.providers.rocketreach import parse_profile
    assert parse_profile({"name": "A", "location": "Bengaluru, Karnataka, India"}).matched_location == "Bengaluru, Karnataka, India"
    assert parse_profile({"name": "A", "city": "Pune", "country": "India"}).matched_location == "Pune, India"
    assert parse_profile({"name": "A"}).matched_location is None
    a = mkperson(db, None, "https://www.linkedin.com/in/riley", "Example Fintech")
    b = mkperson(db, "B", "u2", "Acme"); db.update("people", b, {"location": "Chennai"})
    res = ProviderResult("matched", [ContactValue("r@x.com", "valid", "A")], credits_used=1, matched_name="Riley R", matched_location="Bengaluru, India")
    E.run_enrichment(db, FakeProvider({a: res, b: ProviderResult("matched", [ContactValue("b@x.com", "valid", "A")], credits_used=1, matched_name="B", matched_location="Delhi")}), mkcamp(db, [a, b]), 5, 5)
    assert db.one("SELECT location FROM people WHERE id=?", (a,))["location"] == "Bengaluru, India"
    assert db.one("SELECT location FROM people WHERE id=?", (b,))["location"] == "Chennai"  # never overwritten


def test_commentminer_company_name_from_headline_or_url():
    from app.mappers.commentminer import company_name, map_payload
    assert company_name("Recruiter at Example Staffing INC | C2C", None, "https://examplestaffing.com") == "Example Staffing INC"
    # headline names another company than the website: the website wins
    assert company_name("Recruiter at Other Corp", None, "https://samplesoft.com") == "Samplesoft"
    assert company_name("Recruiter", "https://www.linkedin.com/company/sample-group-inc/", None) == "Sample Group Inc"
    assert company_name("Recruiter at Solo Firm", None, None) == "Solo Firm"
    assert company_name("Senior Recruiter", None, None) is None
    rec = map_payload({"comments": [{"author_name": "Pat Q", "author_profile_url": "https://www.linkedin.com/in/pat-q",
                                     "author_headline": "Recruiter at Example Staffing INC", "company_website": "https://examplestaffing.com"}]}).records[0]
    assert rec.fields["company_name"] == "Example Staffing INC"


def test_commentminer_lead_shows_post_and_comment(db):
    from app import leads as L
    a = mkperson(db, "Pat Q", "https://www.linkedin.com/in/pat-q", None)
    for i, (title, text, ts) in enumerate([("Urn:Li:Activity:123", "older comment", "2026-11-25T10:00:00+00:00"),
                                           ("Hi bench sales recruiters", "Interested, please share", "2026-11-26T10:00:00+00:00")]):
        db.insert("lead_sources", {"tool": "commentminer", "kind": "person", "source_key": "cm:%d" % i, "person_id": a, "collected_at": ts,
                                   "raw_payload": json.dumps({"post_title": title, "post_url": "https://www.linkedin.com/feed/update/%d" % i, "comment_text": text})})
    row = L.export_rows(db, ids=[a])[0]
    assert (row["post_title"], row["comment"], row["comment_count"]) == ("Hi bench sales recruiters", "Interested, please share", 2)
    assert L._post_title("Urn:Li:Activity:7530648825317998592") == ""


def test_commentminer_groups_comments_emails_and_type(db):
    from app.mappers.commentminer import map_payload, pick_emails, lead_type_from_headline
    from app import leads as L
    from app.merge import upsert_records
    base = {"author_name": "Dee P", "author_profile_url": "https://www.linkedin.com/in/dee-p", "author_headline": "SR US IT Recruiter at Sampleshaper inc",
            "email": "dee.p.recruits@gmail.com", "company_website": "https://sampleshaper.com"}
    rows = [{**base, "comment_text": "dee.p.recruits@gmail.com", "post_title": "Post A", "post_url": "https://l/a", "commented_at": "2026-11-24T10:00:00Z"},
            {**base, "comment_text": "or dee@sampleshaper.com, cc pat@othervendor.com", "post_title": "Post B", "post_url": "https://l/b", "commented_at": "2026-11-25T10:00:00Z"}]
    recs = map_payload({"comments": rows}).records
    assert len(recs) == 1                                         # two comments, one person, nothing overwritten
    f = recs[0].fields
    assert f["email"] == "dee@sampleshaper.com"                   # work email on their own company site beats gmail
    assert f["lead_type"] == "employer_contact"
    upsert_records(db, recs)
    pid = db.one("SELECT id FROM people WHERE linkedin_url LIKE ?", ("%dee-p%",))["id"]
    row = L.export_rows(db, ids=[pid])[0]
    assert row["comment_count"] == 2 and row["post_title"] == "Post B"
    assert row["comment_emails"] == "pat@othervendor.com; dee.p.recruits@gmail.com"
    # a work email given by the tool is never replaced; nothing on their domain -> keep the tool's email
    assert pick_emails("a@vendor.com", ["x@sampleshaper.com"], "https://sampleshaper.com")[0] == "a@vendor.com"
    assert pick_emails("a@gmail.com", ["b@else.com"], "https://sampleshaper.com") == ("a@gmail.com", ["b@else.com"])
    assert lead_type_from_headline("Bench Sales Recruiter") == "employer_contact"
    assert lead_type_from_headline("Java Developer | Spring") == "unknown"


def test_sales_navigator_link_is_kept_and_not_sent_to_rocketreach():
    from app.normalize import normalize_person_url
    assert normalize_person_url("https://linkedin.com/sales/lead/ACwAAExampleID1,NAME_SEARCH,xYz") == "https://www.linkedin.com/sales/lead/ACwAAExampleID1"
    assert normalize_person_url("https://www.linkedin.com/in/Some-One/") == "https://www.linkedin.com/in/some-one"
    seen = []
    rr = RocketReach("k", http=lambda url, h: (seen.append(url), (200, '{"status":"complete","emails":[{"email":"a@x.com"}]}'))[1])
    rr.lookup({"linkedin_url": "https://www.linkedin.com/sales/lead/ACwAAExampleID1", "full_name": "Ana B", "company_name": "Acme"})
    assert "sales" not in seen[0] and "name=Ana" in seen[0]          # matched on name + company instead


def test_talentpulse_candidate_details(db):
    from app import leads as L
    from app.mappers.talentpulse import map_payload
    from app.merge import upsert_records
    row = {"id": 7, "full_name": "Ana B", "linkedin_url": "https://linkedin.com/sales/lead/ACwAAExampleID2", "current_title": "Python Developer",
           "headline": "Entry-level Python dev, Django and SQL", "company_name": "Sample Academy", "email": "ana.b@gmail.com",
           "email_status": "From profile", "skills": ["Python", "Django"], "years_experience": 1.0, "otw_status": "Yes",
           "signal_type": "Open to Work", "qualification_status": "Qualified", "search_role": "Python Developer", "signal_date": "Not available"}
    upsert_records(db, map_payload({"leads": [row]}).records)
    pid = db.one("SELECT id FROM people WHERE full_name='Ana B'")["id"]
    r = L.export_rows(db, ids=[pid])[0]
    assert r["linkedin_url"] == "https://www.linkedin.com/sales/lead/ACwAAExampleID2"
    assert (r["search_role"], r["experience"], r["skills"], r["open_to_work"], r["qualification"], r["email_status"]) == \
           ("Python Developer", "1 yr", "Python, Django", "Yes", "Qualified", "From profile")
    from app.exporter import _value, TOOL_COLUMNS
    assert _value(r, "headline") == "Entry-level Python dev, Django and SQL" and _value(r, "role") == "Python Developer"
    assert "company_industry" not in TOOL_COLUMNS["talentpulse"] and "skills" in TOOL_COLUMNS["talentpulse"]
