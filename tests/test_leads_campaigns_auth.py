import pytest
from app import leads as L
from app.auth import hash_password, verify_password, seed_admin, authenticate, create_user, RateLimiter
from app.merge import upsert_records, NormalizedRecord


def seed(db):
    def P(tool, key, **f):
        return NormalizedRecord(tool, "person", key, {}, f)
    upsert_records(db, [
        P("talentpulse", "t1", full_name="Asha Rao", linkedin_url="https://www.linkedin.com/in/asha", lead_type="candidate", company_name="Globex", email="a@g.com", location="Pune"),
        P("hiringbadge", "h1", full_name="Asha Rao", linkedin_url="https://www.linkedin.com/in/asha", lead_type="employer_contact"),
        P("hiringbadge", "h2", full_name="Ravi K", linkedin_url="https://www.linkedin.com/in/ravi", lead_type="employer_contact", company_name="Umbrella", phone="+91 1", location="Mumbai"),
        P("commentminer", "c1", full_name="100%_Real", linkedin_url="https://www.linkedin.com/in/real", headline="Founder"),
    ])


def test_filters_each_and_combined(db):
    seed(db)
    n = lambda **f: L.list_leads(db, f)["total"]
    assert n() == 3
    assert n(q="asha") == 1 and n(q="founder") == 1 and n(q="umbrella") == 1
    assert n(tool="hiringbadge") == 2 and n(tool="talentpulse") == 1
    assert n(lead_type="candidate") == 1
    assert n(company="glob") == 1 and n(location="mumbai") == 1
    assert n(has_email="true") == 1 and n(has_phone="1") == 1
    assert n(tool="hiringbadge", lead_type="employer_contact", has_phone="true") == 1
    assert n(q="%") == 1 and n(q="100%_") == 1 and n(q="a%a") == 0 and n(q="_sha") == 0  # wildcards are literal, not patterns


def test_pagination_and_tools_attached(db):
    seed(db)
    r = L.list_leads(db, {}, page=1, page_size=2)
    assert len(r["items"]) == 2 and r["total"] == 3
    asha = [x for x in L.list_leads(db, {"q": "asha"})["items"]][0]
    assert asha["tools"] == ["hiringbadge", "talentpulse"]
    assert L.list_leads(db, {}, page=1, page_size=9999)["page_size"] == 200


def test_invalid_filter_values_rejected(db):
    with pytest.raises(ValueError):
        L.list_leads(db, {"lead_type": "nonsense"})
    with pytest.raises(ValueError):
        L.list_leads(db, {"tool": "nonsense"})


def test_campaign_add_by_filter_equals_filter_and_is_idempotent(db):
    seed(db)
    cid = L.create_campaign(db, "Q4 hiring managers", None, "admin@x.com")
    r = L.add_to_campaign(db, cid, filters={"lead_type": "employer_contact"})
    assert r["added"] == 1  # Asha keeps her first specific type (candidate), so only Ravi matches
    assert L.add_to_campaign(db, cid, filters={"lead_type": "employer_contact"})["added"] == 0
    ids = L.campaign_person_ids(db, cid)
    assert sorted(ids) == sorted(L.lead_ids(db, {"lead_type": "employer_contact"}))
    rows = L.export_rows(db, ids=ids)
    assert len(rows) == 1 and "hiringbadge" in rows[0]["tools"]


def test_campaign_by_ids_ignores_unknown_and_name_required(db):
    seed(db)
    cid = L.create_campaign(db, "X", "d", "u")
    assert L.add_to_campaign(db, cid, person_ids=[1, 9999])["added"] == 1
    with pytest.raises(ValueError):
        L.create_campaign(db, "  ", None, "u")
    with pytest.raises(KeyError):
        L.add_to_campaign(db, 777, person_ids=[1])


def test_deleting_membership_keeps_person(db):
    seed(db)
    cid = L.create_campaign(db, "X", None, "u")
    L.add_to_campaign(db, cid, person_ids=[1])
    db.execute("DELETE FROM campaign_leads WHERE campaign_id=? AND person_id=?", (cid, 1))
    assert db.one("SELECT COUNT(*) AS n FROM people")["n"] == 3


def test_password_hash_roundtrip_and_salting():
    h1, h2 = hash_password("correct horse"), hash_password("correct horse")
    assert h1 != h2 and verify_password("correct horse", h1) and not verify_password("wrong", h1) and not verify_password("x", "garbage")


def test_seed_admin_once_and_authenticate(db):
    assert seed_admin(db, "Admin@Example.com", "supersecret1") is True
    assert seed_admin(db, "other@example.com", "supersecret1") is False
    assert authenticate(db, "admin@example.com", "supersecret1")["role"] == "admin"
    assert authenticate(db, "admin@example.com", "nope") is None and authenticate(db, "ghost@x.com", "supersecret1") is None
    with pytest.raises(ValueError):
        create_user(db, "short@x.com", "123")
    with pytest.raises(ValueError):
        create_user(db, "admin@example.com", "supersecret1")


def test_rate_limiter():
    r = RateLimiter(limit=5, window=60)
    assert [r.allow("ip") for _ in range(6)] == [True] * 5 + [False]
    assert r.allow("other-ip")
