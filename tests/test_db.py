import pytest
from app.db import Database


def test_schema_creates_all_tables_and_is_rerunnable(db):
    names = db.table_names()
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


def test_no_untyped_parameter_null_checks_in_sql():
    """Postgres cannot type a bare parameter in '? IS NULL' ("could not determine data type of parameter"),
    while SQLite accepts it. That once made 49 of 50 Hiring Badge leads fail on the live (Postgres) site."""
    import pathlib, re
    bad = []
    for f in pathlib.Path("app").rglob("*.py"):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\?\s+IS\s+(NOT\s+)?NULL", line, re.I):
                bad.append(f"{f}:{n}")
    assert bad == [], "decide NULL-ness of a parameter in Python, not in SQL: " + ", ".join(bad)
