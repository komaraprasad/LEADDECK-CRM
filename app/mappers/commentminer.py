"""CommentMiner GET /api/posts/combined -> commenters (type unknown)."""
from __future__ import annotations
import re
from app.records import NormalizedRecord, MapResult
from app.mappers.base import norm_row, rows_from, pick, parse_dt

TOOL = "commentminer"
_SQUASH = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
_SUFFIX = re.compile(r"\b(inc|llc|ltd|pvt|private|limited|corp|corporation|co|technologies|technology|tech|solutions|services|group|global|consulting|systems|software|labs?)\b\.?", re.I)


def _from_headline(headline: str | None) -> str | None:
    """'Bench sales recruiter at Tech Vector Minds INC | Hiring' -> 'Tech Vector Minds INC'."""
    m = re.search(r"(?:\bat\b|@)\s*([^|•·,;/()\[\]]+)", headline or "", re.I)
    if not m:
        return None
    name = re.split(r"\s[-–—]\s", m.group(1))[0].strip(" .-")
    return name if 2 <= len(name) <= 60 else None


def _from_url(curl: str | None, website: str | None) -> str | None:
    slug = re.search(r"linkedin\.com/company/([^/?#]+)", curl or "", re.I)
    if slug:
        words = [w for w in re.split(r"[-_]+", slug.group(1)) if w and not w.isdigit()]
        if words:
            return " ".join(w.capitalize() for w in words)
    host = re.sub(r"^(https?://)?(www\.)?", "", (website or "").strip().lower()).split("/")[0]
    root = host.split(".")[0] if host else ""
    return root.capitalize() if len(root) >= 2 else None


def company_name(headline, curl, website) -> str | None:
    """CommentMiner gives a company page/website but no company name. Take the name from the headline when it
    agrees with the website/page (it reads better), else from the page slug or the website."""
    h, fallback = _from_headline(headline), _from_url(curl, website)
    if h and (curl or website):
        hk, fk = _SQUASH(_SUFFIX.sub("", h)), _SQUASH(_SUFFIX.sub("", fallback or ""))
        if hk and fk and (hk[:5] in fk or fk[:5] in hk):
            return h
        return fallback
    return h or fallback


def map_payload(payload: object) -> MapResult:
    res = MapResult()
    try:
        rows = rows_from(payload, ("comments", "data", "items", "results", "rows"))
    except ValueError as e:
        res.errors.append(str(e))
        return res
    # One row per comment; a person can comment many times. Group by person first: every comment used to
    # overwrite the previous one (same source key), so only one comment per person was ever kept.
    groups: dict[str, list] = {}
    for i, raw in enumerate(rows):
        try:
            if not isinstance(raw, dict):
                raise ValueError("row is not an object")
            r = norm_row(raw)
            name = pick(r, "author_name", "name")
            url = pick(r, "author_profile_url", "profile_url", "linkedin_url")
            if not (name or url):
                raise ValueError("no name or profile url")
            groups.setdefault(url or f"{name}|{pick(r, 'post_id')}", []).append((raw, r))
        except Exception as e:
            res.errors.append(f"row {i}: {e}")
    for key, items in groups.items():
        try:
            items.sort(key=lambda it: str(pick(it[1], "commented_at", "collected_at", "created_at", "comment_date") or ""))
            raw, r = items[-1]                                   # newest comment carries the profile fields
            fv = lambda *ks: next((pick(x, *ks) for _, x in reversed(items) if pick(x, *ks)), None)
            headline, website, curl = fv("author_headline", "headline"), fv("company_website"), fv("company_linkedin_url")
            texts = [pick(x, "comment_text") or "" for _, x in items]
            # every email the tool gave for this person (any comment, newest first; a field may hold several)
            given = ", ".join(pick(x, "email") or "" for _, x in reversed(items))
            email, others, mine = split_emails(given, texts, website)
            comments = [{"post_title": pick(x, "post_title"), "post_url": pick(x, "post_url"), "comment_text": pick(x, "comment_text"),
                         "commented_at": pick(x, "commented_at")} for _, x in items]
            fields = {
                "full_name": fv("author_name", "name"), "headline": headline,
                "linkedin_url": fv("author_profile_url", "profile_url", "linkedin_url"), "email": email, "phone": fv("phone"),
                "company_website": website, "company_url": curl, "company_name": company_name(headline, curl, website),
                "lead_type": lead_type_from_headline(headline),
            }
            out = {**raw, "_comments": comments, "_comment_emails": others, "_emails": mine}
            res.records.append(NormalizedRecord(TOOL, "person", f"{TOOL}:{key}", out, fields,
                                                parse_dt(pick(r, "commented_at", "collected_at", "created_at", "comment_date"))))
        except Exception as e:
            res.errors.append(f"{key}: {e}")
    return res


_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


def _host(website) -> str:
    h = re.sub(r"^(https?://)?(www\.)?", "", (website or "").strip().lower()).split("/")[0]
    return h


def pick_emails(given, comment_texts, website) -> tuple:
    """(main email, other emails written in their comments). The tool's email stays the main one, except when it
    is a personal mailbox and a comment contains an address on the lead's own company website: that work address
    is clearly theirs and better for outreach. Other addresses may belong to a colleague, so they are only listed."""
    main, others, _ = split_emails(given, comment_texts, website)
    return main, others


def _emails_in(text) -> list:
    out = []
    for e in _EMAIL.findall(text or ""):
        e = e.lower().rstrip(".")
        if e not in out:
            out.append(e)
    return out


def split_emails(given, comment_texts, website) -> tuple:
    """(main email, other emails from comments, the lead's emails). CommentMiner's email field can hold several
    addresses ("a@x.com, b@gmail.com"); all are the lead's. The main one (used for enrichment / CRM) is the first,
    except that a work address on the lead's own company website beats a personal mailbox. Addresses found only in
    comments, not in the email field, are listed separately: they may be a colleague's."""
    from app.enrich import PERSONAL_EMAIL_DOMAINS
    given_list = _emails_in(given)
    found = []
    for t in comment_texts:
        for e in _emails_in(t):
            if e not in found:
                found.append(e)
    host = _host(website)
    dom = lambda e: e.rsplit("@", 1)[-1]
    own = lambda e: bool(host) and (dom(e) == host or dom(e).endswith("." + host) or host.endswith("." + dom(e)))
    main = given_list[0] if given_list else None
    if main and dom(main) in PERSONAL_EMAIL_DOMAINS:
        main = next((e for e in given_list if own(e)), None) or next((e for e in found if own(e)), None) or main
    elif not main:
        main = next((e for e in found if own(e)), None) or (found[0] if found else None)
    mine = ([main] if main else []) + [e for e in given_list if e != main]
    return main, [e for e in found if e not in mine], mine


_EMPLOYER = re.compile(
    r"recruit|recuit|recuriter|requiter|recrut|\btalent\b|\bstaffing\b|\bhr\b|human resources|\bhiring\b|\bsourcer\b|headhunt|"
    r"bench ?sales?|bench (delivery|marketing)|resource (coordinator|executive|manag\w*|specialist)|client acquisition|"
    r"\bta (lead|partner|specialist|manager)\b|account manager|business development|\bbdm\b|delivery (lead|manager|head)|"
    r"sell candidates|placed \d+", re.I)


def lead_type_from_headline(headline) -> str:
    """Recruiters / staffing / HR people are employer-side contacts. Anything else stays unknown (never guessed as
    a candidate). A guess from the headline only."""
    return "employer_contact" if _EMPLOYER.search(headline or "") else "unknown"
