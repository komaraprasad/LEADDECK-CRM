import pytest
from app.db import Database


def test_schema_creates_all_tables_and_is_rerunnable(db):
    names = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"companies", "people", "lead_sources", "job_postings", "campaigns", "campaign_leads", "sync_runs", "users", "schema_version"} <= names
    db.init_schema()  # second run applies nothing, raises nothing
    from app.schema import MIGRATIONS
    assert db.one("SELECT COUNT(*) AS n FROM schema_version")["n"] == len(MIGRATIONS)


def test_person_linkedin_url_unique_but_null_allowed(db):
    db.insert("people", {"full_name": "A", "linkedin_url": "u1"})
    with pytest.raises(Exception):
        db.insert("people", {"full_name": "B", "linkedin_url": "u1"})
    db.insert("people", {"full_name": "C", "linkedin_url": None})
    db.insert("people", {"full_name": "D", "linkedin_url": None})


def test_transaction_rollback_and_savepoint(db):
    with db.transaction(rollback=True):
        db.insert("campaigns", {"name": "x"})
    assert db.one("SELECT COUNT(*) AS n FROM campaigns")["n"] == 0
    with db.transaction():
        db.insert("campaigns", {"name": "keep"})
        with pytest.raises(RuntimeError):
            with db.savepoint():
                db.insert("campaigns", {"name": "drop"})
                raise RuntimeError("boom")
    assert [r["name"] for r in db.query("SELECT name FROM campaigns")] == ["keep"]
