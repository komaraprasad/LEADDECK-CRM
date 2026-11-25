import json, pathlib, threading, time
import pytest
from app.config import Settings
from app import sync as S
from app.importer import import_export_file

FX = pathlib.Path(__file__).parent / "fixtures"
body = lambda n: (200, (FX / f"{n}.json").read_text(encoding="utf-8"))
ST = Settings(testing=True)


def fake(routes):
    """routes: tool-name substring of base URL -> (status, text) or exception to raise."""
    def get(url, timeout):
        for key, val in routes.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return val
        raise AssertionError(f"unexpected url {url}")
    return get


ALL_OK = {"talentpulse": None}


def routes_ok():
    return {"ecs-or-ec2": None}


def good_get():
    m = {"ec2-15-252": body("talentpulse"), "hiring-badge": body("hiringbadge"), "15.252.131.112/api/posts": body("commentminer"), "jobharvest": body("jobharvest")}
    def get(url, timeout):
        if "ec2-15-252" in url: return m["ec2-15-252"]
        if "hiring-badge" in url: return m["hiring-badge"]
        if "/api/posts/combined" in url: return m["15.252.131.112/api/posts"]
        if "jobharvest" in url: return m["jobharvest"]
        raise AssertionError(url)
    return get


def test_first_sync_defaults_to_dry_run_then_live(db):
    r1 = S.run_sync(db, ST, "talentpulse", None, good_get())
    assert r1["status"] == "ok" and r1["dry_run"] == 1 and r1["created"] == 2
    assert db.one("SELECT COUNT(*) AS n FROM people")["n"] == 0
    r2 = S.run_sync(db, ST, "talentpulse", False, good_get())
    assert r2["dry_run"] == 0 and db.one("SELECT COUNT(*) AS n FROM people")["n"] == 2
    r3 = S.run_sync(db, ST, "talentpulse", None, good_get())  # now live by default, and idempotent
    assert r3["dry_run"] == 0 and r3["created"] == 0 and r3["skipped"] == 2


def test_html_response_fails_only_that_tool(db):
    get = good_get()
    def mixed(url, t):
        return (200, "<html>Service waking up</html>") if "jobharvest" in url else get(url, t)
    runs = S.run_all(db, ST, False, mixed)
    st = {r["tool"]: r["status"] for r in runs}
    assert st == {"commentminer": "ok", "talentpulse": "ok", "hiringbadge": "ok", "jobharvest": "failed"}
    bad = [r for r in runs if r["tool"] == "jobharvest"][0]
    assert "non-JSON" in bad["errors"]
    assert db.one("SELECT COUNT(*) AS n FROM people")["n"] > 0


@pytest.mark.parametrize("resp", [(500, "boom"), (404, "nf"), (200, ""), TimeoutError("timed out"), ConnectionError("refused")])
def test_error_statuses_and_exceptions_mark_failed(db, resp):
    r = S.run_sync(db, ST, "hiringbadge", False, fake({"hiring-badge": resp}))
    assert r["status"] == "failed" and json.loads(r["errors"])


def test_retry_on_503_then_success(db):
    calls = []
    def flaky(url, t):
        calls.append(1)
        return (503, "") if len(calls) == 1 else body("hiringbadge")
    assert S.run_sync(db, ST, "hiringbadge", False, flaky)["status"] == "ok" and len(calls) == 2


def test_second_sync_rejected_while_running(db):
    started, release = threading.Event(), threading.Event()
    def slow(url, t):
        started.set(); release.wait(5)
        return body("hiringbadge")
    out = {}
    th = threading.Thread(target=lambda: out.update(r=S.run_sync(db, ST, "hiringbadge", False, slow)))
    th.start(); started.wait(2)
    with pytest.raises(S.SyncBusy):
        S.run_sync(db, ST, "hiringbadge", False, good_get())
    release.set(); th.join(5)
    assert out["r"]["status"] == "ok" and db.one("SELECT COUNT(*) AS n FROM sync_runs WHERE tool='hiringbadge'")["n"] == 1


def test_stale_running_rows_marked_failed_on_start(db):
    db.insert("sync_runs", {"tool": "talentpulse", "started": "2026-01-01T00:00:00+00:00", "status": "running"})
    S.mark_stale_runs(db)
    assert db.one("SELECT status FROM sync_runs")["status"] == "failed"


def test_import_csv_dry_run_then_live(db):
    csv_bytes = b"Person Name,Person LinkedIn URL,Company Name\nAsha Rao,https://www.linkedin.com/in/asha-rao,Globex\n"
    r = import_export_file(db, "hiringbadge", "x.csv", csv_bytes, dry_run=True)
    assert r["created"] == 1 and r["source"] == "import" and db.one("SELECT COUNT(*) AS n FROM people")["n"] == 0
    import_export_file(db, "hiringbadge", "x.csv", csv_bytes, dry_run=False)
    assert db.one("SELECT COUNT(*) AS n FROM people")["n"] == 1


# ---- endpoint / pagination fixes found by the first live sync ----
def test_endpoints_match_the_tools_real_routes(db):
    seen = {}
    def spy(url, t):
        seen[url] = 1
        return (200, json.dumps({"items": [], "total": 0, "leads": [], "rows": [], "comments": []}))
    for tool in ("talentpulse", "hiringbadge", "commentminer", "jobharvest"):
        S.run_sync(db, ST, tool, True, spy)
    urls = " ".join(seen)
    assert "https://jobharvest-xnac.onrender.com/api/ext/results" in seen       # JobHarvest JSON API ...
    assert "https://jobharvest-xnac.onrender.com/results" not in seen          # ... not the HTML page
    assert "/api/leads?limit=5000" in urls and "/api/posts/combined" in urls
    assert "page=1&page_size=500" in urls                                       # Hiring Badge Finder is paged


def test_hiringbadge_reads_every_page(db):
    people = [{"profile_url": f"https://www.linkedin.com/in/p{i}", "name": f"P{i}", "company": "Acme"} for i in range(1200)]
    pages = []
    def get(url, t):
        page = int(url.split("page=")[1].split("&")[0]); pages.append(page)
        size = 500
        return 200, json.dumps({"total": 1200, "page": page, "page_size": size, "items": people[(page - 1) * size: page * size]})
    r = S.run_sync(db, ST, "hiringbadge", False, get)
    assert pages == [1, 2, 3] and r["fetched"] == 1200 and r["created"] == 1200
    assert db.one("SELECT COUNT(*) AS n FROM people")["n"] == 1200 and not json.loads(r["errors"])


def test_warns_when_tool_reports_more_than_was_read(db):
    body_ = {"total": 10, "page": 1, "page_size": 500, "items": [{"profile_url": "https://www.linkedin.com/in/a", "name": "A"}]}
    r = S.run_sync(db, ST, "hiringbadge", False, lambda u, t: (200, json.dumps(body_)))
    assert r["status"] == "ok" and "reports 10 records but only 1" in json.loads(r["errors"])[0]


def test_jobharvest_real_api_shape_maps_people_company_job(db):
    real = {"rows": [{"job_id": "9", "job_title": "SRE", "company_name": "Globex", "company_url": "https://www.linkedin.com/company/globex",
                      "location": "Pune", "company_industry": "Software", "company_size": "201-500", "job_url": "https://x/9",
                      "contact_profile_links": "https://www.linkedin.com/in/asha-rao", "first_found": "2026-11-01", "last_seen": "2026-11-10"}],
            "roles": ["SRE"]}
    r = S.run_sync(db, ST, "jobharvest", False, lambda u, t: (200, json.dumps(real)))
    assert r["status"] == "ok" and r["fetched"] == 3 and r["created"] == 3
    assert db.one("SELECT industry, size FROM companies")["industry"] == "Software"
    assert db.one("SELECT title FROM job_postings")["title"] == "SRE"


def test_commentminer_real_shape_zero_comments_is_ok_not_failure(db):
    empty = {"success": True, "is_combined": True, "total_posts": 0, "total_comments": 0, "posts": [], "comments": []}
    r = S.run_sync(db, ST, "commentminer", False, lambda u, t: (200, json.dumps(empty)))
    assert r["status"] == "ok" and r["fetched"] == 0
