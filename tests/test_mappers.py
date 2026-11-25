import json, pathlib
from app.mappers import MAPPERS

FX = pathlib.Path(__file__).parent / "fixtures"
load = lambda n: json.loads((FX / f"{n}.json").read_text(encoding="utf-8"))


def by_kind(res, kind):
    return [r for r in res.records if r.kind == kind]


def test_talentpulse():
    res = MAPPERS["talentpulse"](load("talentpulse"))
    people = by_kind(res, "person")
    assert len(people) == 2 and len(res.errors) == 2
    asha = people[0].fields
    assert asha["full_name"] == "Asha Rao" and asha["lead_type"] == "candidate"
    assert asha["email"] == "asha@globex.example" and asha["phone"] is None  # empty -> None, never ""
    assert people[1].fields["full_name"] == "José Ñúñez"


def test_hiringbadge():
    res = MAPPERS["hiringbadge"](load("hiringbadge"))
    people = by_kind(res, "person")
    assert len(people) == 2 and len(res.errors) == 1
    assert people[0].fields["lead_type"] == "employer_contact"
    assert people[0].fields["company_name"] == "Globex"
    assert people[0].collected_at.isoformat().startswith("2026-11-10")


def test_commentminer():
    res = MAPPERS["commentminer"](load("commentminer"))
    people = by_kind(res, "person")
    assert len(people) == 2 and len(res.errors) == 1
    assert people[0].fields["lead_type"] == "unknown"
    assert people[0].fields["company_url"] == "https://www.linkedin.com/company/hooli"
    assert people[1].fields["linkedin_url"] is None


def test_jobharvest():
    res = MAPPERS["jobharvest"](load("jobharvest"))
    assert len(by_kind(res, "company")) == 2 and len(by_kind(res, "job")) == 2
    assert len(res.errors) == 1  # the orphan job has no company: skipped, not guessed
    persons = by_kind(res, "person")
    assert [p.fields["linkedin_url"] for p in persons] == [
        "https://www.linkedin.com/in/asha-rao", "https://www.linkedin.com/in/ravi-k"]
    assert all(p.fields["lead_type"] == "employer_contact" for p in persons)


def test_bad_payload_shapes_never_raise():
    for name, fn in MAPPERS.items():
        for bad in (None, "html page", 42, {"unexpected": 1}):
            res = fn(bad)
            assert res.records == [] and res.errors, name


def test_bad_row_types_skipped():
    res = MAPPERS["hiringbadge"]([1, None, {"name": "OK Person", "profile_url": "https://www.linkedin.com/in/ok"}])
    assert len(res.records) == 1 and len(res.errors) == 2


def test_naive_tool_times_are_india_time_and_converted_to_utc(monkeypatch):
    from app.mappers.base import parse_dt
    monkeypatch.delenv("TOOL_UTC_OFFSET", raising=False)
    assert parse_dt("2026-11-18 10:26:03").isoformat() == "2026-11-18T04:56:03+00:00"
    assert parse_dt("2026-11-18T10:26:03Z").isoformat() == "2026-11-18T10:26:03+00:00"   # explicit zone is kept
    monkeypatch.setenv("TOOL_UTC_OFFSET", "+00:00")
    assert parse_dt("2026-11-18 10:26:03").isoformat() == "2026-11-18T10:26:03+00:00"
