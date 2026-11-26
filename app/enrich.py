"""Enrichment orchestration: eligibility, estimate, capped run, contact points, ledger, compliance."""
from __future__ import annotations
from app.db import Database, now_iso
from app.leads import campaign_person_ids
from app.normalize import normalize_name
from app.providers.base import Provider, ProviderError, ProviderResult

_RANK = {"valid": 3, "unknown": 2, "unverified": 1, "invalid": 0}
_GRADE = {"A": 5, "A-": 4, "B": 3, "C": 2, "D": 1, "F": 0}


def _tokens(s: str | None) -> set[str]:
    return {t for t in normalize_name(s).replace(".", " ").replace(",", " ").split() if len(t) > 1}


def check_identity(person: dict, res: ProviderResult) -> tuple[bool, str | None]:
    """Wrong-person guard. Returns (is_same_person, note). A name that shares no word with ours is a mismatch;
    a different employer alone is only a note (people change jobs)."""
    if not res.matched_name:
        return True, None  # the provider gave no name to compare: cannot disprove
    ours, theirs = _tokens(person.get("full_name")), _tokens(res.matched_name)
    if ours and theirs and not (ours & theirs):
        return False, "name mismatch: provider returned a different person"
    note = None
    co, pe = _tokens(person.get("company_name")), _tokens(res.matched_employer)
    if co and pe and not (co & pe):
        note = "employer differs from our record (job change or wrong match): review"
    return True, note


def _people(db: Database, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    ph = ",".join("?" for _ in ids)
    return db.query("SELECT p.id, p.full_name, p.linkedin_url, p.email, p.phone, p.do_not_contact, c.name AS company_name "
                    f"FROM people p LEFT JOIN companies c ON c.id = p.company_id WHERE p.id IN ({ph}) ORDER BY p.id", ids)


def eligible(db: Database, cid: int, provider: Provider, person_ids: list[int] | None = None, include_done: bool = False) -> dict:
    """Split a campaign into people we may look up and people we skip (with the reason)."""
    ids = campaign_person_ids(db, cid)
    if person_ids is not None:
        ids = [i for i in ids if i in set(person_ids)]
    people = _people(db, ids)
    done = {r["person_id"] for r in db.query(
        "SELECT person_id FROM enrichment_items WHERE provider=? AND status IN ('matched','not_found','mismatch')", (provider.name,))}
    has_valid = {r["person_id"] for r in db.query("SELECT person_id FROM contact_points WHERE kind='email' AND verification='valid'")}
    todo, skipped = [], {"do_not_contact": 0, "already_enriched": 0, "has_verified_email": 0, "cannot_match": 0}
    for p in people:
        if p["do_not_contact"]:
            skipped["do_not_contact"] += 1
        elif p["id"] in done and not include_done:
            skipped["already_enriched"] += 1
        elif p["id"] in has_valid and not include_done:
            skipped["has_verified_email"] += 1
        elif not (p["linkedin_url"] or (p["full_name"] and p["company_name"])):
            skipped["cannot_match"] += 1
        else:
            todo.append(p)
    return {"people": todo, "skipped": skipped, "total": len(people)}


def estimate(db: Database, cid: int, provider: Provider, lead_cap: int | None = None, person_ids: list[int] | None = None, include_done: bool = False) -> dict:
    e = eligible(db, cid, provider, person_ids, include_done)
    n = len(e["people"]) if not lead_cap else min(len(e["people"]), lead_cap)
    return {"provider": provider.name, "configured": provider.configured(), "campaign_leads": e["total"],
            "eligible": len(e["people"]), "would_look_up": n, "estimated_credits": provider.estimate(n), "skipped": e["skipped"]}


# Free/personal mailbox domains. On a tie, a work address beats these (B2B outreach should go to the work inbox).
PERSONAL_EMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.in", "ymail.com", "rocketmail.com",
    "outlook.com", "hotmail.com", "live.com", "msn.com", "hotmail.co.in", "live.in", "outlook.in",
    "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "pm.me",
    "rediffmail.com", "zoho.com", "zohomail.com", "zohomail.in", "gmx.com", "gmx.net", "mail.com",
    "yandex.com", "yandex.ru", "tutanota.com", "fastmail.com", "hey.com",
})


def _domain(value: str | None) -> str:
    """'https://www.Acme.com/x' or 'a@Acme.com' -> 'acme.com'."""
    v = (value or "").strip().lower()
    if "@" in v:
        v = v.rsplit("@", 1)[1]
    v = v.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    return v[4:] if v.startswith("www.") else v


def _email_pref(value: str | None, company_domain: str) -> int:
    """2 = on the lead's company domain (or a subdomain), 1 = other work domain, 0 = personal mailbox."""
    d = _domain(value)
    if company_domain and (d == company_domain or d.endswith("." + company_domain)):
        return 2
    return 0 if d in PERSONAL_EMAIL_DOMAINS else 1


def _best_first(cps: list[dict], company_domain: str = "") -> list[dict]:
    return sorted(cps, key=lambda c: (_RANK.get(c["verification"], 0), _GRADE.get((c.get("provider_grade") or "").upper(), -1),
                                      _email_pref(c["value"], company_domain) if c.get("kind") == "email" else 0), reverse=True)


def _company_domain(db: Database, pid: int) -> str:
    c = db.one("SELECT c.domain, c.website FROM people p LEFT JOIN companies c ON c.id = p.company_id WHERE p.id=?", (pid,))
    return _domain((c or {}).get("domain") or (c or {}).get("website"))


def _pick_primary(db: Database, pid: int, kind: str, company_domain: str) -> None:
    """Mark the best contact point primary and fill the person's field. The person's email is replaced only when blank
    or when it was the previous enrichment primary (i.e. we set it); an email from a tool or a user is never overwritten."""
    old = db.one("SELECT value FROM contact_points WHERE person_id=? AND kind=? AND is_primary=1", (pid, kind))
    cps = _best_first(db.query("SELECT * FROM contact_points WHERE person_id=? AND kind=?", (pid, kind)), company_domain)
    db.execute("UPDATE contact_points SET is_primary=0 WHERE person_id=? AND kind=?", (pid, kind))
    if not cps or cps[0]["verification"] == "invalid":
        return
    db.update("contact_points", cps[0]["id"], {"is_primary": 1})
    cur = db.one("SELECT email, phone FROM people WHERE id=?", (pid,))
    if kind == "email" and cps[0]["verification"] == "valid":
        ours = old is not None and cur["email"] == old["value"]
        if not cur["email"] or (ours and cur["email"] != cps[0]["value"]):
            db.update("people", pid, {"email": cps[0]["value"]})
    if kind == "phone" and not cur["phone"]:
        db.update("people", pid, {"phone": cps[0]["value"]})


def repick_primary_emails(db: Database) -> list[int]:
    """One-off repair: re-choose the primary email for every enriched person with the current rules.
    Returns the ids whose primary changed. Safe to run more than once."""
    changed = []
    for r in db.query("SELECT DISTINCT person_id FROM contact_points WHERE kind='email' ORDER BY person_id"):
        pid = r["person_id"]
        before = db.one("SELECT value FROM contact_points WHERE person_id=? AND kind='email' AND is_primary=1", (pid,))
        with db.transaction():
            _pick_primary(db, pid, "email", _company_domain(db, pid))
        after = db.one("SELECT value FROM contact_points WHERE person_id=? AND kind='email' AND is_primary=1", (pid,))
        if (before or {}).get("value") != (after or {}).get("value"):
            changed.append(pid)
    return changed


def _store(db: Database, person: dict, provider: Provider, res: ProviderResult) -> None:
    ts = now_iso()
    # a lead a tool gave only as a profile link (no name/title): take them from the matched profile, blanks only
    cur = db.one("SELECT full_name, title, location FROM people WHERE id=?", (person["id"],))
    fill = {}
    if not (cur["full_name"] or "").strip() and (res.matched_name or "").strip():
        fill.update(full_name=res.matched_name.strip(), name_key=normalize_name(res.matched_name) or None)
    if not (cur["title"] or "").strip() and (getattr(res, "matched_title", None) or "").strip():
        fill["title"] = res.matched_title.strip()
    if not (cur["location"] or "").strip() and (getattr(res, "matched_location", None) or "").strip():
        fill["location"] = res.matched_location.strip()
    if fill:
        db.update("people", person["id"], fill)
    for kind, vals in (("email", res.emails), ("phone", res.phones), ("company_phone", res.company_phones)):
        for v in vals:
            row = db.one("SELECT * FROM contact_points WHERE person_id=? AND kind=? AND value=?", (person["id"], kind, v.value))
            if row is None:
                db.insert("contact_points", {"person_id": person["id"], "kind": kind, "value": v.value, "provider": provider.name,
                                             "provider_grade": v.grade, "verification": v.verification,
                                             "verified_at": ts if v.verification in ("valid", "invalid") else None, "found_at": ts,
                                             "line_type": getattr(v, "line_type", None)})
            else:
                if _RANK.get(v.verification, 0) > _RANK.get(row["verification"], 0):
                    db.update("contact_points", row["id"], {"verification": v.verification, "provider_grade": v.grade, "verified_at": ts})
                if getattr(v, "line_type", None) and not row.get("line_type"):
                    db.update("contact_points", row["id"], {"line_type": v.line_type})
    dom = _company_domain(db, person["id"])
    for kind in ("email", "phone", "company_phone"):
        _pick_primary(db, person["id"], kind, dom)


def run_enrichment(db: Database, provider: Provider, cid: int, credit_cap: int, lead_cap: int, user: str = "",
                   person_ids: list[int] | None = None, include_done: bool = False) -> dict:
    if not provider.configured():
        raise ProviderError("The %s API key is not set." % provider.name)
    e = eligible(db, cid, provider, person_ids, include_done)
    todo = e["people"][: max(0, lead_cap)]
    run_id = db.insert("enrichment_runs", {"campaign_id": cid, "provider": provider.name, "status": "running",
                                           "estimated_credits": provider.estimate(len(todo)), "credit_cap": credit_cap,
                                           "lead_cap": lead_cap, "created_by": user, "started": now_iso(),
                                           "skipped": sum(e["skipped"].values())})
    used = matched = not_found = errors = mismatched = 0
    status, note = "ok", None
    for p in todo:
        if used + provider.credits_per_lookup > credit_cap:
            status, note = "capped", "stopped before exceeding the credit cap"
            break
        try:
            res = provider.lookup(p)
        except ProviderError as ex:  # key/plan/rate-limit problems repeat for every person: stop the run
            status, note = "failed", str(ex)
            break
        except Exception as ex:
            res = ProviderResult("error", error="%s" % type(ex).__name__)
        used += res.credits_used
        item_note = None
        if res.status == "matched":
            same, item_note = check_identity(p, res)
            if not same:
                res = ProviderResult("mismatch", credits_used=res.credits_used, error=item_note, shape=res.shape)
        with db.transaction():
            if res.status == "matched":
                _store(db, p, provider, res); matched += 1
            elif res.status == "not_found":
                not_found += 1
            elif res.status == "mismatch":
                mismatched += 1
            else:
                errors += 1
            from app import crm
            crm.ensure_lead(db, p["id"])
            db.insert("enrichment_items", {"run_id": run_id, "person_id": p["id"], "provider": provider.name, "status": res.status,
                                           "credits_used": res.credits_used, "error": res.error, "raw_shape": res.shape, "match_note": item_note, "created_at": now_iso()})
            crm.refresh_readiness(db, p["id"])
    with db.transaction():
        if used:
            db.insert("credit_ledger", {"provider": provider.name, "run_id": run_id, "credits": used,
                                        "note": "credits counted by LeadDeck (1 per lookup that returned data); compare with your provider account",
                                        "created_at": now_iso()})
        db.update("enrichment_runs", run_id, {"status": status, "credits_used": used, "matched": matched, "not_found": not_found,
                                              "errors": errors + mismatched, "finished": now_iso(), "note": note or getattr(provider, "notice", "") or (f"{mismatched} lookup(s) returned a different person and were not saved" if mismatched else None)})
    return db.one("SELECT * FROM enrichment_runs WHERE id=?", (run_id,))


# ---------------------------------------------------------------- compliance
def set_do_not_contact(db: Database, pid: int, flag: bool, reason: str | None = None) -> None:
    if not db.one("SELECT 1 AS x FROM people WHERE id=?", (pid,)):
        raise KeyError(pid)
    db.update("people", pid, {"do_not_contact": 1 if flag else 0, "dnc_reason": (reason or "").strip() or None if flag else None})
    if db.one("SELECT 1 AS x FROM crm_leads WHERE person_id=?", (pid,)):
        from app import crm
        crm.refresh_readiness(db, pid)


def erase_person(db: Database, pid: int) -> None:
    """Delete a person and everything linked to them; remember the profile URL so a later sync cannot recreate them."""
    row = db.one("SELECT linkedin_url FROM people WHERE id=?", (pid,))
    if not row:
        raise KeyError(pid)
    with db.transaction():
        if row["linkedin_url"]:
            db.execute("INSERT INTO erasures (linkedin_url, erased_at) VALUES (?, ?) ON CONFLICT (linkedin_url) DO NOTHING", (row["linkedin_url"], now_iso()))
        for t in ("contact_points", "enrichment_items", "campaign_leads", "lead_sources", "crm_leads", "notes", "tasks", "lead_events"):
            db.execute(f"DELETE FROM {t} WHERE person_id=?", (pid,))
        db.execute("DELETE FROM people WHERE id=?", (pid,))


# --------------------------------------------------------------------- stats
def campaign_stats(db: Database, cid: int, provider_name: str | None = None) -> dict:
    """Measured results for one campaign: what the provider found, and what it cost per useful contact."""
    ids = campaign_person_ids(db, cid)
    out = {"campaign_leads": len(ids), "looked_up": 0, "matched": 0, "not_found": 0, "errors": 0, "wrong_person": 0, "needs_review": 0, "credits_used": 0,
           "with_valid_email": 0, "with_direct_phone": 0, "company_phone_only": 0, "no_phone_at_all": 0,
           "credits_per_valid_email": None, "match_rate": None, "valid_email_rate": None, "direct_phone_rate": None}
    if not ids:
        return out
    ph = ",".join("?" for _ in ids)
    q = f"SELECT person_id, status, credits_used, match_note FROM enrichment_items WHERE person_id IN ({ph})"
    params = list(ids)
    if provider_name:
        q, params = q + " AND provider=?", params + [provider_name]
    latest: dict[int, dict] = {}
    for r in db.query(q + " ORDER BY id", params):  # last lookup per person decides status; credits add up
        prev = latest.get(r["person_id"], {"credits": 0})
        latest[r["person_id"]] = {"status": r["status"], "credits": prev["credits"] + (r["credits_used"] or 0), "review": bool(r["match_note"]) and r["status"] == "matched"}
    out["looked_up"] = len(latest)
    out["credits_used"] = sum(v["credits"] for v in latest.values())
    for v in latest.values():
        key = {"matched": "matched", "not_found": "not_found", "mismatch": "wrong_person"}.get(v["status"], "errors")
        out[key] += 1
        out["needs_review"] += v["review"]
    looked = list(latest)
    lph = ",".join("?" for _ in looked)
    kinds: dict[int, set] = {}
    for c in db.query(f"SELECT person_id, kind, verification FROM contact_points WHERE person_id IN ({lph})", looked):
        k = kinds.setdefault(c["person_id"], set())
        if c["kind"] == "email" and c["verification"] == "valid":
            k.add("valid_email")
        elif c["kind"] in ("phone", "company_phone"):
            k.add(c["kind"])
    for pid in looked:
        k = kinds.get(pid, set())
        out["with_valid_email"] += "valid_email" in k
        out["with_direct_phone"] += "phone" in k
        out["company_phone_only"] += ("company_phone" in k and "phone" not in k)
        out["no_phone_at_all"] += not ({"phone", "company_phone"} & k)
    n = out["looked_up"]
    if n:
        out["match_rate"] = round(out["matched"] / n, 3)
        out["valid_email_rate"] = round(out["with_valid_email"] / n, 3)
        out["direct_phone_rate"] = round(out["with_direct_phone"] / n, 3)
    if out["with_valid_email"]:
        out["credits_per_valid_email"] = round(out["credits_used"] / out["with_valid_email"], 2)
    return out
