"""Lead listing, filtering, export rows and campaigns (shared filter logic)."""
from __future__ import annotations
import json
import re
from app.db import Database, now_iso

LEAD_TYPES = ("candidate", "employer_contact", "unknown")
TOOLS = ("commentminer", "talentpulse", "hiringbadge", "jobharvest")
FILTER_KEYS = ("q", "tool", "lead_type", "company", "location", "title", "stage", "quality", "dnc", "owner", "has_email", "has_phone")
SORT_KEYS = ("name", "title", "company", "location", "score", "stage", "last_seen")
QUALITIES = ("high", "medium", "low")
STAGE_KEYS = ("new", "enriched", "ready", "contacted", "replied", "interested", "meeting", "won", "lost", "not_a_fit")

# Contact quality score (0-100): how reachable a lead is. Transparent and based only on stored contact data.
#   verified email 50 | any other usable email 20 | direct phone 35 | company phone only 10 | found by 2+ tools 15
_VALID_EMAIL = "EXISTS (SELECT 1 FROM contact_points x WHERE x.person_id = p.id AND x.kind='email' AND x.verification='valid')"
_BAD_EMAIL = "EXISTS (SELECT 1 FROM contact_points x WHERE x.person_id = p.id AND x.kind='email' AND x.is_primary=1 AND x.verification='invalid')"
_COMPANY_PHONE = "EXISTS (SELECT 1 FROM contact_points x WHERE x.person_id = p.id AND x.kind='company_phone')"
SCORE_SQL = (f"(CASE WHEN {_VALID_EMAIL} THEN 50 WHEN COALESCE(p.email,'') <> '' AND NOT {_BAD_EMAIL} THEN 20 ELSE 0 END"
             f" + CASE WHEN COALESCE(p.phone,'') <> '' THEN 35 WHEN {_COMPANY_PHONE} THEN 10 ELSE 0 END"
             " + CASE WHEN (SELECT COUNT(DISTINCT s.tool) FROM lead_sources s WHERE s.person_id = p.id) >= 2 THEN 15 ELSE 0 END)")
_STAGE_SQL = "COALESCE(cl.stage, 'new')"
_FROM = "FROM people p LEFT JOIN companies c ON c.id = p.company_id LEFT JOIN crm_leads cl ON cl.person_id = p.id"


def name_from_linkedin(url: str | None) -> str | None:
    """Readable name guessed from a profile URL slug ('riya-vedpathak-5651a32a3' -> 'Riya Vedpathak').
    For display only, never for matching: it is a guess."""
    if not url or "/in/" not in url:
        return None
    slug = url.split("/in/", 1)[1].split("?", 1)[0].strip("/").split("/", 1)[0]
    words = [w for w in slug.replace("_", "-").split("-") if w and w.isalpha()]
    return " ".join(w.capitalize() for w in words) if words and len("".join(words)) >= 2 else None


def _display(r: dict) -> dict:
    guess = None if r.get("full_name") else name_from_linkedin(r.get("linkedin_url"))
    r["display_name"] = r.get("full_name") or guess
    r["name_from_url"] = bool(guess)
    return r


def _job_locations(db: Database, rows: list[dict]) -> None:
    """Leads found on a job posting (JobHarvest hiring contacts): the job they are hiring for (hiring_for,
    job_url) and, when the person has no location of their own, where that job is (job_location).
    Display/export only; the person's own fields are never changed. Also name_source for exports."""
    for r in rows:
        r.setdefault("job_location", ""); r.setdefault("hiring_for", ""); r.setdefault("job_url", "")
        r["name_source"] = "LinkedIn link" if r.get("name_from_url") else ""
    ids = [r["id"] for r in rows]
    if not ids:
        return
    ph = ",".join("?" for _ in ids)
    found: dict[int, dict] = {}
    for s in db.query(f"SELECT person_id, raw_payload FROM lead_sources WHERE tool='jobharvest' AND kind='person' "
                      f"AND person_id IN ({ph}) ORDER BY collected_at DESC, id DESC", ids):
        if s["person_id"] in found:
            continue   # newest posting wins
        try:
            raw = json.loads(s["raw_payload"] or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(raw, dict):
            txt = lambda *ks: next((raw[k].strip() for k in ks if isinstance(raw.get(k), str) and raw[k].strip()), "")
            found[s["person_id"]] = {"loc": txt("location", "Location"), "title": txt("job_title", "title", "Job Title"),
                                     "url": txt("job_url", "Job URL", "url")}
    for r in rows:
        j = found.get(r["id"])
        if j:
            r["hiring_for"], r["job_url"] = j["title"], j["url"]
            if not (r.get("location") or "").strip():
                r["job_location"] = j["loc"]
    _post_context(db, rows, ids, ph)


def _post_title(t: str) -> str:
    # CommentMiner falls back to the post's URN when it has no text; that is not a title anyone can read
    return "" if re.fullmatch(r"(?i)\s*urn:li:\w+:\d+\s*", t or "") else (t or "").strip()


def _post_context(db: Database, rows: list[dict], ids: list, ph: str) -> None:
    """CommentMiner commenters: the post they commented on and what they wrote (newest comment), and how many
    comments they left. Display/export only."""
    for r in rows:
        r.setdefault("post_title", ""); r.setdefault("post_url", ""); r.setdefault("comment", ""); r.setdefault("comment_count", 0)
    found: dict[int, dict] = {}
    for s in db.query(f"SELECT person_id, raw_payload FROM lead_sources WHERE tool='commentminer' AND kind='person' "
                      f"AND person_id IN ({ph}) ORDER BY collected_at DESC, id DESC", ids):
        try:
            raw = json.loads(s["raw_payload"] or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        cur = found.setdefault(s["person_id"], {"n": 0, "emails": []})
        # grouped records carry every comment in _comments; older records were one comment each
        comments = raw["_comments"] if isinstance(raw.get("_comments"), list) else [raw]
        comments = [c for c in comments if isinstance(c, dict)]
        cur["n"] += len(comments)
        for e in raw.get("_comment_emails") or []:
            if isinstance(e, str) and e not in cur["emails"]:
                cur["emails"].append(e)
        if "url" not in cur and comments:   # newest comment wins
            last = max(comments, key=lambda c: str(c.get("commented_at") or ""))
            txt = lambda k: last[k].strip() if isinstance(last.get(k), str) else ""
            cur.update(title=_post_title(txt("post_title")), url=txt("post_url"), comment=txt("comment_text")[:500])
    for r in rows:
        r.setdefault("comment_emails", "")
        c = found.get(r["id"])
        if c and "url" in c:
            r["post_title"] = c["title"] or ("LinkedIn post" if c["url"] else "")
            r["post_url"], r["comment"], r["comment_count"] = c["url"], c["comment"], c["n"]
            r["comment_emails"] = "; ".join(e for e in c["emails"] if e != (r.get("email") or "").lower())
    _candidate_context(db, rows, ids, ph)


_NA = {"", "not available", "n/a", "na", "none", "null", "unknown", "[]"}


def _clean(v) -> str:
    if isinstance(v, list):
        return ", ".join(str(x).strip() for x in v if str(x).strip())
    s = str(v or "").strip()
    if s.startswith("[") and s.endswith("]"):   # "['Python', 'Django']" as text
        s = ", ".join(x.strip().strip("'\"") for x in s[1:-1].split(",") if x.strip().strip("'\""))
    return "" if s.lower() in _NA else s


def _candidate_context(db: Database, rows: list[dict], ids: list, ph: str) -> None:
    """TalentPulse candidates: searched role, years of experience, skills, open-to-work (and why), qualification,
    and where the tool's email came from. Display/export only."""
    for r in rows:
        for k in ("search_role", "experience", "skills", "open_to_work", "qualification"):
            r.setdefault(k, "")
    found: dict[int, dict] = {}
    for s in db.query(f"SELECT person_id, raw_payload FROM lead_sources WHERE tool='talentpulse' AND kind='person' "
                      f"AND person_id IN ({ph}) ORDER BY collected_at DESC, id DESC", ids):
        if s["person_id"] in found:
            continue
        try:
            raw = json.loads(s["raw_payload"] or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(raw, dict):
            found[s["person_id"]] = raw
    for r in rows:
        raw = found.get(r["id"])
        if not raw:
            continue
        g = lambda *ks: next((_clean(raw.get(k)) for k in ks if _clean(raw.get(k))), "")
        r["search_role"] = g("search_role", "target_role")
        yrs = g("years_experience", "experience_years")
        try:
            f = float(yrs)
            r["experience"] = (str(int(f)) if f == int(f) else str(round(f, 1))) + (" yr" if f == 1 else " yrs")
        except ValueError:
            r["experience"] = yrs
        r["skills"] = g("skills", "relevant_skills")
        otw = g("otw_status", "is_open_to_work")
        if otw.lower() in ("yes", "true", "1"):
            why = g("signal_type")
            r["open_to_work"] = "Yes" + (" (" + why + ")" if why and why.lower() != "open to work" else "")
        elif otw:
            r["open_to_work"] = "No" if otw.lower() in ("no", "false", "0") else otw
        r["qualification"] = g("qualification_status")
        if not r.get("email_status") and r.get("email"):
            r["email_status"] = g("email_status")   # e.g. "From profile"


def quality_band(score: int) -> str:
    return "high" if score >= 50 else "medium" if score >= 20 else "low"
MAX_EXPORT = 50000


def _like(s: str) -> str:
    return "%" + s.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _truthy(v) -> bool:
    return str(v).lower() in ("1", "true", "yes", "on")


def clean_filters(raw: dict) -> dict:
    f = {k: str(raw[k]).strip() for k in FILTER_KEYS if raw.get(k) not in (None, "")}
    if f.get("lead_type") not in (None, *LEAD_TYPES):
        raise ValueError("invalid lead_type")
    if f.get("tool") not in (None, *TOOLS):
        raise ValueError("invalid tool")
    if f.get("stage") not in (None, *STAGE_KEYS):
        raise ValueError("invalid stage")
    if f.get("quality") not in (None, *QUALITIES):
        raise ValueError("invalid quality")
    if f.get("dnc") not in (None, "exclude", "only"):
        raise ValueError("invalid dnc")
    return f


def _where(f: dict):
    w, p = ["1=1"], []
    if "q" in f:
        pat = _like(f["q"])
        w.append("(LOWER(COALESCE(p.full_name,'')) LIKE ? ESCAPE '\\' OR LOWER(COALESCE(p.headline,'')) LIKE ? ESCAPE '\\' "
                 "OR LOWER(COALESCE(p.title,'')) LIKE ? ESCAPE '\\' OR LOWER(COALESCE(c.name,'')) LIKE ? ESCAPE '\\' "
                 # the LinkedIn URL too: some tools (JobHarvest contacts) give only a profile link, no name
                 "OR LOWER(COALESCE(p.linkedin_url,'')) LIKE ? ESCAPE '\\')")
        p += [pat] * 5
    if "tool" in f:
        w.append("EXISTS (SELECT 1 FROM lead_sources s WHERE s.person_id = p.id AND s.tool = ?)")
        p.append(f["tool"])
    if "lead_type" in f:
        w.append("p.lead_type = ?"); p.append(f["lead_type"])
    if "company" in f:
        w.append("LOWER(COALESCE(c.name,'')) LIKE ? ESCAPE '\\'"); p.append(_like(f["company"]))
    if "location" in f:
        w.append("LOWER(COALESCE(p.location,'')) LIKE ? ESCAPE '\\'"); p.append(_like(f["location"]))
    if "title" in f:
        w.append("(LOWER(COALESCE(p.title,'')) LIKE ? ESCAPE '\\' OR LOWER(COALESCE(p.headline,'')) LIKE ? ESCAPE '\\')"); p += [_like(f["title"])] * 2
    if "stage" in f:
        w.append(f"{_STAGE_SQL} = ?"); p.append(f["stage"])
    if "owner" in f:
        if f["owner"] == "none":
            w.append("cl.owner_email IS NULL")
        else:
            w.append("cl.owner_email = ?"); p.append(f["owner"].lower())
    if "quality" in f:
        w.append({"high": f"{SCORE_SQL} >= 50", "medium": f"({SCORE_SQL} >= 20 AND {SCORE_SQL} < 50)", "low": f"{SCORE_SQL} < 20"}[f["quality"]])
    if f.get("dnc") == "exclude":
        w.append("p.do_not_contact = 0")
    elif f.get("dnc") == "only":
        w.append("p.do_not_contact = 1")
    if _truthy(f.get("has_email", "")):
        w.append("(p.email IS NOT NULL AND p.email <> '')")
    if _truthy(f.get("has_phone", "")):
        w.append("(p.phone IS NOT NULL AND p.phone <> '')")
    return " AND ".join(w), p


_SELECT = ("SELECT p.id, p.full_name, p.headline, p.title, p.location, p.linkedin_url, p.email, p.phone, p.lead_type, "
           "p.match_confidence, p.do_not_contact, p.first_seen, p.last_seen, c.name AS company_name, c.linkedin_url AS company_url, c.industry AS company_industry, c.size AS company_size, "
           f"{_STAGE_SQL} AS stage, cl.owner_email AS owner, {SCORE_SQL} AS score, "
           # an enrichment provider already answered for this lead (found, not found or wrong person)
           "CASE WHEN EXISTS (SELECT 1 FROM enrichment_items ei WHERE ei.person_id = p.id "
           "AND ei.status IN ('matched','not_found','mismatch')) THEN 1 ELSE 0 END AS looked_up "
           f"{_FROM}")


def _attach_tools(db: Database, rows: list[dict]) -> list[dict]:
    ids = [r["id"] for r in rows]
    tools: dict[int, list[str]] = {}
    if ids:
        ph = ",".join("?" for _ in ids)
        for s in db.query(f"SELECT DISTINCT person_id, tool FROM lead_sources WHERE person_id IN ({ph})", ids):
            tools.setdefault(s["person_id"], []).append(s["tool"])
    status: dict[int, str] = {}
    if ids:
        for c in db.query(f"SELECT person_id, verification FROM contact_points WHERE kind='email' AND is_primary=1 AND person_id IN ({ph})", ids):
            status[c["person_id"]] = c["verification"]
    cphone: dict[int, str] = {}
    if ids:
        for c in db.query(f"SELECT person_id, value FROM contact_points WHERE kind='company_phone' AND is_primary=1 AND person_id IN ({ph})", ids):
            cphone[c["person_id"]] = c["value"]
    # every usable email/phone a provider found (invalid ones left out), best first, labelled
    allc: dict[int, dict[str, list]] = {}
    if ids:
        for c in db.query(f"SELECT person_id, kind, value, line_type, verification, is_primary FROM contact_points "
                          f"WHERE verification <> 'invalid' AND kind IN ('email','phone') AND person_id IN ({ph}) "
                          f"ORDER BY person_id, kind, is_primary DESC, id", ids):
            allc.setdefault(c["person_id"], {"email": [], "phone": []})[c["kind"]].append(c)
    lab = lambda c: c["value"] + (" (" + c["line_type"] + ")" if c.get("line_type") else "")
    for r in rows:
        mine = allc.get(r["id"], {"email": [], "phone": []})
        r["personal_email"] = next((c["value"] for c in mine["email"] if (c.get("line_type") or "") == "personal"), "")
        r["all_emails"] = "; ".join(lab(c) for c in mine["email"])
        r["all_phones"] = "; ".join(lab(c) for c in mine["phone"])
        r["company_phone"] = cphone.get(r["id"], "")
        r["quality"] = quality_band(r.get("score") or 0)
        r["tools"] = sorted(tools.get(r["id"], []))
        r["email_status"] = status.get(r["id"], "")
        _display(r)
    _job_locations(db, rows)
    return rows


_SORT_SQL = {"name": "LOWER(COALESCE(p.full_name,''))", "title": "LOWER(COALESCE(p.title, p.headline, ''))", "company": "LOWER(COALESCE(c.name,''))",
             "location": "LOWER(COALESCE(p.location,''))", "score": SCORE_SQL, "stage": _STAGE_SQL, "last_seen": "p.last_seen"}


def list_leads(db: Database, filters: dict, page: int = 1, page_size: int = 50, sort: str = "last_seen", direction: str = "desc") -> dict:
    f = clean_filters(filters)
    if sort not in SORT_KEYS:
        raise ValueError("invalid sort")
    if direction not in ("asc", "desc"):
        raise ValueError("invalid sort direction")
    page, page_size = max(1, page), min(max(1, page_size), 200)
    where, params = _where(f)
    total = db.one(f"SELECT COUNT(*) AS n {_FROM} WHERE {where}", params)["n"]
    rows = db.query(f"{_SELECT} WHERE {where} ORDER BY {_SORT_SQL[sort]} {direction.upper()}, p.id DESC LIMIT ? OFFSET ?",
                    [*params, page_size, (page - 1) * page_size])
    return {"items": _attach_tools(db, rows), "total": total, "page": page, "page_size": page_size}


SUGGEST_FIELDS = {"company": "c.name", "location": "p.location", "title": "p.title", "name": "p.full_name"}


def suggest(db: Database, field: str, q: str = "", limit: int = 8) -> list[dict]:
    """Autocomplete values for a filter box: most common values, prefix matches first. Empty q returns the top values."""
    limit = min(max(1, limit), 20)
    q = (q or "").strip()[:80]
    if field == "all":  # global search box: a few of each kind
        out = []
        for fld, label in (("name", "Name"), ("company", "Company"), ("title", "Title"), ("location", "Location")):
            for r in suggest(db, fld, q, 3):
                out.append({**r, "field": fld, "label": label})
        return out
    col = SUGGEST_FIELDS.get(field)
    if not col:
        raise ValueError("invalid field")
    w, params = [f"COALESCE({col}, '') <> ''"], []
    order_params = []
    prefix_case = ""
    if q:
        w.append(f"LOWER({col}) LIKE ? ESCAPE '\\'"); params.append(_like(q))
        prefix_case = f"CASE WHEN LOWER({col}) LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END, "
        order_params.append(_like(q)[1:])  # drop the leading % -> prefix pattern
    rows = db.query(f"SELECT {col} AS value, COUNT(*) AS n {_FROM} WHERE {' AND '.join(w)} GROUP BY {col} "
                    f"ORDER BY {prefix_case}n DESC, {col} LIMIT {limit}", [*params, *order_params])
    return [{"value": r["value"], "count": r["n"]} for r in rows]


def lead_ids(db: Database, filters: dict) -> list[int]:
    where, params = _where(clean_filters(filters))
    return [r["id"] for r in db.query(
        f"SELECT p.id {_FROM} WHERE {where} ORDER BY p.id LIMIT {MAX_EXPORT}", params)]


def get_lead(db: Database, pid: int) -> dict | None:
    row = db.one(f"{_SELECT} WHERE p.id = ?", (pid,))
    if not row:
        return None
    row["tools"] = sorted({s["tool"] for s in db.query("SELECT tool FROM lead_sources WHERE person_id=?", (pid,))})
    row["contact_points"] = db.query("SELECT kind, value, provider, provider_grade, line_type, verification, verified_at, is_primary, found_at FROM contact_points WHERE person_id=? ORDER BY kind, is_primary DESC, id", (pid,))
    row["sources"] = db.query("SELECT tool, kind, source_key, collected_at, synced_at FROM lead_sources WHERE person_id=? ORDER BY tool", (pid,))
    _job_locations(db, [row])
    return _display(row)


def export_rows(db: Database, filters: dict | None = None, ids: list[int] | None = None) -> list[dict]:
    if ids is not None:
        if not ids:
            return []
        ph = ",".join("?" for _ in ids)
        rows = db.query(f"{_SELECT} WHERE p.id IN ({ph}) ORDER BY p.id", ids)
    else:
        where, params = _where(clean_filters(filters or {}))
        rows = db.query(f"{_SELECT} WHERE {where} ORDER BY p.last_seen DESC, p.id DESC LIMIT {MAX_EXPORT}", params)
    for r in _attach_tools(db, rows):
        r["tools"] = ", ".join(r["tools"])
        r["do_not_contact_text"] = "YES" if r.get("do_not_contact") else ""
    return rows


# ---------------------------------------------------------------- campaigns
def create_campaign(db: Database, name: str, description: str | None, user: str) -> int:
    name = (name or "").strip()
    if not name or len(name) > 120:
        raise ValueError("campaign name is required (max 120 characters)")
    return db.insert("campaigns", {"name": name, "description": (description or "").strip() or None, "created_by": user, "created_at": now_iso()})


def add_to_campaign(db: Database, cid: int, person_ids: list[int] | None = None, filters: dict | None = None) -> dict:
    if not db.one("SELECT 1 AS x FROM campaigns WHERE id=?", (cid,)):
        raise KeyError(cid)
    ids = lead_ids(db, filters) if person_ids is None else [int(i) for i in person_ids]
    existing = {r["id"] for r in db.query("SELECT id FROM people")} if person_ids is not None else None
    added = 0
    with db.transaction():
        for pid in ids:
            if existing is not None and pid not in existing:
                continue
            added += db.execute("INSERT INTO campaign_leads (campaign_id, person_id, added_at) VALUES (?, ?, ?) ON CONFLICT (campaign_id, person_id) DO NOTHING",
                                (cid, pid, now_iso())) or 0
            if existing is None or pid in existing:
                from app import crm  # every campaign lead gets a CRM row
                crm.ensure_lead(db, pid)
                crm.refresh_readiness(db, pid)
    return {"added": added, "requested": len(ids)}


def campaign_person_ids(db: Database, cid: int) -> list[int]:
    return [r["person_id"] for r in db.query("SELECT person_id FROM campaign_leads WHERE campaign_id=? ORDER BY person_id", (cid,))]
