from datetime import datetime, timezone
from app.merge import upsert_records, NormalizedRecord


def P(tool, key, collected=None, **f):
    f.setdefault("lead_type", "unknown")
    return NormalizedRecord(tool, "person", key, {"k": key}, f, collected)


def count(db, t):
    return db.one(f"SELECT COUNT(*) AS n FROM {t}")["n"]


def test_same_person_two_tools_one_row_two_sources(db):
    a = P("talentpulse", "talentpulse:1", full_name="Asha Rao", linkedin_url="https://www.linkedin.com/in/Asha-Rao/", lead_type="candidate", company_name="Globex")
    b = P("hiringbadge", "hiringbadge:1", full_name="Asha Rao", linkedin_url="https://in.linkedin.com/in/asha-rao", lead_type="employer_contact", title="EM")
    r = upsert_records(db, [a, b])
    assert r.created == 1 and r.updated == 1 and not r.errors
    assert count(db, "people") == 1 and count(db, "lead_sources") == 2 and count(db, "companies") == 1
    p = db.one("SELECT * FROM people")
    assert p["lead_type"] == "candidate" and p["title"] == "EM"  # first specific type sticks, new field filled


def test_rerun_is_idempotent(db):
    recs = [P("talentpulse", "t:1", full_name="A B", linkedin_url="https://www.linkedin.com/in/ab", email="A@X.com", company_name="Acme")]
    upsert_records(db, recs)
    r = upsert_records(db, recs)
    assert (r.created, r.updated, r.skipped) == (0, 0, 1)
    assert db.one("SELECT email FROM people")["email"] == "a@x.com"


def test_blank_never_overwrites_value_and_newer_nonempty_wins(db):
    t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 2, 1, tzinfo=timezone.utc)
    url = "https://www.linkedin.com/in/ab"
    upsert_records(db, [P("talentpulse", "t:1", t1, full_name="A B", linkedin_url=url, email="old@x.com", phone="111")])
    upsert_records(db, [P("hiringbadge", "h:1", t2, full_name="A B", linkedin_url=url, email="", phone=None, title="CTO")])
    p = db.one("SELECT * FROM people")
    assert p["email"] == "old@x.com" and p["phone"] == "111" and p["title"] == "CTO"
    upsert_records(db, [P("hiringbadge", "h:1", t2, full_name="A B", linkedin_url=url, email="new@x.com")])
    assert db.one("SELECT email FROM people")["email"] == "new@x.com"
    # an OLDER record does not overwrite a newer value
    upsert_records(db, [P("commentminer", "c:1", t1, full_name="A B", linkedin_url=url, email="stale@x.com")])
    assert db.one("SELECT email FROM people")["email"] == "new@x.com"


def test_duplicate_within_payload_collapses(db):
    url = "https://www.linkedin.com/in/dup"
    r = upsert_records(db, [P("talentpulse", "t:1", full_name="D", linkedin_url=url), P("talentpulse", "t:2", full_name="D", linkedin_url=url + "/")])
    assert count(db, "people") == 1 and count(db, "lead_sources") == 2 and r.created == 1


def test_no_url_matches_name_company_within_tool_only_low_confidence(db):
    a = P("commentminer", "c:1", full_name="No Url", company_name="Acme")
    a2 = P("commentminer", "c:2", full_name="No Url", company_name="Acme")
    b = P("talentpulse", "t:1", full_name="No Url", company_name="Acme")
    upsert_records(db, [a, a2, b])
    people = db.query("SELECT * FROM people ORDER BY id")
    assert len(people) == 2 and all(p["match_confidence"] == "low" for p in people)  # same-tool merged, cross-tool NOT merged


def test_url_upgrades_low_confidence_same_tool(db):
    upsert_records(db, [P("commentminer", "c:1", full_name="Later Found", company_name="Acme")])
    upsert_records(db, [P("commentminer", "c:2", full_name="Later Found", company_name="Acme", linkedin_url="https://www.linkedin.com/in/later")])
    p = db.query("SELECT * FROM people")
    assert len(p) == 1 and p[0]["match_confidence"] == "high" and p[0]["linkedin_url"].endswith("/in/later")


def test_dry_run_writes_nothing_but_reports(db):
    r = upsert_records(db, [P("talentpulse", "t:1", full_name="X", linkedin_url="https://www.linkedin.com/in/x")], dry_run=True)
    assert r.created == 1 and count(db, "people") == 0 and count(db, "lead_sources") == 0


def test_company_matched_by_url_then_domain_then_name(db):
    c = lambda key, **f: NormalizedRecord("jobharvest", "company", key, {}, f)
    upsert_records(db, [c("a", name="Globex", linkedin_url="https://www.linkedin.com/company/globex/", website="https://www.globex.com")])
    upsert_records(db, [c("b", name="Globex Corp", linkedin_url="https://linkedin.com/company/globex")])  # same url
    upsert_records(db, [c("c", name="Globex Inc", website="http://globex.com/careers")])                    # same domain
    upsert_records(db, [c("d", name="GLOBEX")])                                                              # same name, existing has url
    assert count(db, "companies") == 1
    upsert_records(db, [c("e", name="Globex", linkedin_url="https://www.linkedin.com/company/globex-india")])  # different LinkedIn page
    assert count(db, "companies") == 2


def test_bad_record_isolated_others_saved(db):
    bad = P("talentpulse", "t:bad")  # no name, no url
    good = P("talentpulse", "t:ok", full_name="Fine", linkedin_url="https://www.linkedin.com/in/fine")
    r = upsert_records(db, [bad, good])
    assert r.created == 1 and len(r.errors) == 1 and count(db, "people") == 1


def test_jobs_and_job_person_company_links(db):
    j = NormalizedRecord("jobharvest", "job", "jobharvest:job:1", {}, {"title": "SRE", "company_name": "Globex", "location": "Pune"})
    upsert_records(db, [j])
    r = upsert_records(db, [j])
    assert count(db, "job_postings") == 1 and count(db, "companies") == 1 and r.skipped == 1


def test_future_collected_time_is_clamped_to_sync_time():
    from datetime import datetime, timedelta, timezone
    from app.db import Database
    from app.merge import upsert_records
    from app.records import NormalizedRecord
    import inspect
    db = Database("sqlite:///:memory:"); db.init_schema()
    future = datetime.now(timezone.utc) + timedelta(hours=5)
    fields = {"full_name": "Asha Rao", "linkedin_url": "https://www.linkedin.com/in/asha-rao"}
    params = inspect.signature(NormalizedRecord).parameters
    kw = {"tool": "talentpulse", "kind": "person", "source_key": "k1", "fields": fields, "collected_at": future}
    if "raw" in params:
        kw["raw"] = {}
    upsert_records(db, [NormalizedRecord(**{k: v for k, v in kw.items() if k in params})])
    first_seen = db.one("SELECT first_seen FROM people")["first_seen"]
    assert first_seen <= datetime.now(timezone.utc).isoformat(timespec="seconds")
