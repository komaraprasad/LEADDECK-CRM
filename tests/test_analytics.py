"""Analytics: numbers come straight from stored data, are read-only, and stay honest when there is nothing to show."""
from datetime import datetime, timedelta, timezone
import pytest
from app import analytics as A, crm
from app.db import now_iso
from tests.test_phase2 import mkperson, mkcamp
from tests.test_crm import wapp, logged  # noqa: F401  (fixtures)
from tests.test_ux import person

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


def ago(days, hours=0):
    return (NOW - timedelta(days=days, hours=hours)).isoformat(timespec="seconds")


def snapshot(db):
    tabs = ("people", "crm_leads", "lead_events", "contact_points", "enrichment_items", "tasks", "credit_ledger")
    return {t: db.one(f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in tabs}


def test_empty_database_gives_zeros_and_nulls_not_errors(db):
    d = A.overview(db, 30, now=NOW)
    assert d["leads"]["total"] == 0 and d["leads"]["valid_email_rate"] is None
    assert d["enrichment"]["match_rate"] is None and d["enrichment"]["credits_per_valid_email"] is None
    assert d["outreach"]["connected"] is False and d["outreach"]["reply_rate"] is None
    assert len(d["enrichment"]["by_day"]) == 30 and all(x["credits"] == 0 for x in d["enrichment"]["by_day"])
    assert d["sources"] == [] and d["tasks"] == {"open": 0, "overdue": 0, "done_in_period": 0}
    assert [f["stage"] for f in d["funnel"]][:3] == ["new", "enriched", "ready"]


def test_days_is_clamped_to_known_ranges(db):
    assert A.clean_days("90") == 90 and A.clean_days("7") == 7
    for bad in (None, "", "abc", "5", "9999", "-1"):
        assert A.clean_days(bad) == 30
    assert len(A.overview(db, "7", now=NOW)["enrichment"]["by_day"]) == 7


def test_contact_coverage_and_quality_bands(db):
    a = person(db, "A", email="a@x.com", phone="+1")
    b = person(db, "B", email="b@x.com")
    c = person(db, "C")
    db.insert("contact_points", {"person_id": a, "kind": "email", "value": "a@x.com", "provider": "rr", "verification": "valid", "is_primary": 1, "found_at": ago(1)})
    d = A.overview(db, 30, now=NOW)["leads"]
    assert d["total"] == 3 and d["valid_email"] == 1 and d["with_phone"] == 1 and d["valid_email_rate"] == 33.3
    q = A.overview(db, 30, now=NOW)["quality"]
    assert q == {"high": 1, "medium": 1, "low": 1}      # A: 50+35, B: 20, C: 0


def test_enrichment_numbers_use_only_the_chosen_period(db):
    a, b, c = (person(db, n, email=f"{n}@x.com") for n in "ABC")
    run = db.insert("enrichment_runs", {"campaign_id": 1, "provider": "rr", "status": "done"})
    db.insert("enrichment_items", {"run_id": run, "person_id": a, "provider": "rr", "status": "matched", "credits_used": 1, "created_at": ago(2)})
    db.insert("enrichment_items", {"run_id": run, "person_id": b, "provider": "rr", "status": "not_found", "credits_used": 0, "created_at": ago(2)})
    db.insert("enrichment_items", {"run_id": run, "person_id": c, "provider": "rr", "status": "matched", "credits_used": 1, "created_at": ago(60)})
    db.insert("contact_points", {"person_id": a, "kind": "email", "value": "a@x.com", "provider": "rr", "verification": "valid", "found_at": ago(2)})
    e30 = A.overview(db, 30, now=NOW)["enrichment"]
    assert (e30["lookups"], e30["matched"], e30["not_found"], e30["credits"]) == (2, 1, 1, 1)
    assert e30["match_rate"] == 50.0 and e30["valid_emails_found"] == 1 and e30["credits_per_valid_email"] == 1.0
    assert e30["credits_all_time"] == 2
    e90 = A.overview(db, 90, now=NOW)["enrichment"]
    assert e90["lookups"] == 3 and e90["credits"] == 2
    day = next(x for x in e30["by_day"] if x["date"] == ago(2)[:10])
    assert day["credits"] == 1 and day["lookups"] == 2


def test_funnel_counts_current_and_ever_reached(db):
    ids = [person(db, n) for n in "ABCD"]
    mkcamp(db, ids); crm.backfill(db)
    crm.set_stage(db, ids[0], "contacted", "t"); crm.set_stage(db, ids[0], "replied", "t"); crm.set_stage(db, ids[0], "lost", "t", reason="no")
    crm.set_stage(db, ids[1], "contacted", "t")
    f = {x["stage"]: x for x in A.overview(db, 30, now=NOW)["funnel"]}
    assert f["new"]["reached"] == 4 and f["new"]["current"] == 2
    assert f["contacted"]["reached"] == 2 and f["contacted"]["current"] == 1       # A moved on but did pass through "contacted"
    assert f["replied"]["reached"] == 1 and f["replied"]["of_previous"] == 50.0
    assert f["lost"]["current"] == 1 and f["lost"]["terminal"] is True


def test_outreach_is_empty_until_events_exist_then_rates_use_sent(db):
    a = person(db, "A", email="a@x.com"); b = person(db, "B", email="b@x.com"); mkcamp(db, [a, b]); crm.backfill(db)
    assert A.overview(db, 30, now=NOW)["outreach"]["connected"] is False
    for email, t in (("a@x.com", "email_sent"), ("b@x.com", "email_sent"), ("a@x.com", "reply"), ("b@x.com", "bounce")):
        crm.ingest_event(db, t, email)
    o = A.overview(db, 30, now=datetime.now(timezone.utc))["outreach"]
    assert o["connected"] and (o["sent"], o["replies"], o["bounces"]) == (2, 1, 1)
    assert o["reply_rate"] == 50.0 and o["bounce_rate"] == 50.0 and o["unsubscribe_rate"] == 0.0


def test_tasks_owners_and_stale(db):
    a = person(db, "A"); b = person(db, "B"); mkcamp(db, [a, b]); crm.backfill(db)
    db.insert("users", {"email": "me@x.com", "password_hash": "x", "role": "user"})
    crm.assign_owner(db, a, "me@x.com", "t")
    db.insert("tasks", {"person_id": a, "title": "late", "due_at": ago(3)[:10], "owner_email": "me@x.com", "created_at": ago(5)})
    db.insert("tasks", {"person_id": a, "title": "future", "due_at": (NOW + timedelta(days=3)).date().isoformat(), "created_at": ago(1)})
    db.insert("tasks", {"person_id": b, "title": "done", "done_at": ago(1), "created_at": ago(4)})
    d = A.overview(db, 30, now=NOW)
    assert d["tasks"] == {"open": 2, "overdue": 1, "done_in_period": 1}
    owners = {o["owner"]: o for o in d["crm"]["owners"]}
    assert owners["me@x.com"]["leads"] == 1 and owners["me@x.com"]["open_tasks"] == 1 and owners[None]["leads"] == 1
    # a lead sitting in "ready" with no recent activity counts as stale
    crm.set_stage(db, a, "ready", "t") if False else db.execute("UPDATE crm_leads SET stage='ready', stage_changed_at=? WHERE person_id=?", (ago(20), a))
    db.execute("DELETE FROM lead_events WHERE person_id=?", (a,))
    assert A.overview(db, 30, now=NOW)["crm"]["stale"] == 1


def test_sources_table_counts_each_person_once_per_tool(db):
    a = person(db, "A", email="a@x.com", phone="+1"); b = person(db, "B")
    for pid, tool, key in ((a, "commentminer", "k1"), (a, "commentminer", "k2"), (a, "talentpulse", "k3"), (b, "talentpulse", "k4")):
        db.insert("lead_sources", {"tool": tool, "kind": "person", "source_key": key, "person_id": pid})
    s = {x["tool"]: x for x in A.overview(db, 30, now=NOW)["sources"]}
    assert s["commentminer"]["leads"] == 1 and s["talentpulse"]["leads"] == 2
    assert s["talentpulse"]["with_phone"] == 1 and s["commentminer"]["label"] == "CommentMiner"


def test_overview_writes_nothing(db):
    a = person(db, "A"); mkcamp(db, [a]); crm.backfill(db)
    before = snapshot(db)
    A.overview(db, 90, now=NOW)
    assert snapshot(db) == before


def test_api_requires_login_and_returns_json(wapp):
    from tests.test_crm import Client
    assert Client(wapp).get("/api/analytics").status_code == 401
    assert Client(wapp).get("/analytics").status_code == 302
    c = logged(wapp)
    r = c.get("/api/analytics?days=7")
    assert r.status_code == 200 and r.get_json()["days"] == 7
    assert c.get("/api/analytics?days=bogus").get_json()["days"] == 30
    assert c.get("/analytics").status_code == 200


def test_compare_previous_period_and_lead_flow():
    from datetime import datetime, timedelta, timezone
    from app.db import Database
    db = Database("sqlite:///:memory:"); db.init_schema()
    now = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
    for days_ago in (1, 2, 10, 40):   # three in the last 30 days, one in the 30 days before
        db.insert("people", {"full_name": f"P{days_ago}", "first_seen": (now - timedelta(days=days_ago)).isoformat(timespec="seconds")})
    d = A.overview(db, 30, now=now)
    assert d["compare"]["current"]["leads_added"] == 3
    assert d["compare"]["previous"]["leads_added"] == 1
    assert len(d["leads"]["by_day"]) == 30 and sum(r["added"] for r in d["leads"]["by_day"]) == 3


def test_overdue_uses_the_viewers_local_date():
    from datetime import datetime, timezone
    from app.db import Database
    db = Database("sqlite:///:memory:"); db.init_schema()
    db.insert("tasks", {"person_id": 1, "title": "call", "due_at": "2026-11-17"})
    now = datetime(2026, 11, 17, 20, tzinfo=timezone.utc)   # 17 Nov in UTC, already 18 Nov in India
    assert A.overview(db, 30, now=now)["tasks"]["overdue"] == 0
    assert A.overview(db, 30, now=now, local_today="2026-11-18")["tasks"]["overdue"] == 1
    assert A.overview(db, 30, now=now, local_today="junk")["tasks"]["overdue"] == 0
