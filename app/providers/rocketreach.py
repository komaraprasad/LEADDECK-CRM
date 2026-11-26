"""RocketReach adapter (API v2).

ASSUMPTION (not verified against a live account): GET /person/lookup starts a lookup and returns a profile with a
status; when status is not 'complete' we poll GET /person/checkStatus?ids=<id>. Emails carry `smtp_valid`
(valid/invalid/inconclusive) and `grade`. Credits: one per lookup that returns data. Parsing tolerates missing fields.
"""
from __future__ import annotations
import time
from urllib.parse import urlencode
from app.providers.base import ContactValue, Provider, ProviderError, ProviderResult

BASE = "https://api.rocketreach.co/api/v2"
_SMTP = {"valid": "valid", "invalid": "invalid", "inconclusive": "unknown", "unknown": "unknown"}


def _get(url, headers, timeout=30):  # pragma: no cover - real network, never used in tests
    import requests
    r = requests.get(url, headers=headers, timeout=timeout)
    return r.status_code, r.text


def shape_of(v, depth=0):
    """Structure of a JSON value with all values removed (safe to store and share)."""
    if isinstance(v, dict):
        return {k: shape_of(x, depth + 1) for k, x in list(v.items())[:40]} if depth < 4 else "dict"
    if isinstance(v, list):
        return [shape_of(v[0], depth + 1)] if v and depth < 4 else ("list" if v else "[]")
    return type(v).__name__ if v is not None else "null"


def parse_profile(p: dict) -> ProviderResult:
    r = _parse(p)
    r.matched_name = (p.get("name") or None) if isinstance(p.get("name"), str) else None
    r.matched_employer = (p.get("current_employer") or None) if isinstance(p.get("current_employer"), str) else None
    r.matched_title = (p.get("current_title") or None) if isinstance(p.get("current_title"), str) else None
    loc = p.get("location")
    if not (isinstance(loc, str) and loc.strip()):
        loc = ", ".join(x for x in (p.get("city"), p.get("region"), p.get("country")) if isinstance(x, str) and x.strip())
    r.matched_location = loc.strip() or None
    import json
    r.shape = json.dumps(shape_of(p), sort_keys=True)[:2000]
    return r


def _phone_verification(d: dict) -> str:
    """RocketReach phone `grade` (A confirmed, A- likely, B possible) replaced the deprecated `validity`."""
    g = str(d.get("grade") or "").strip().upper()
    if g in ("A", "A-"):
        return "valid"
    if g == "B":
        return "unknown"
    v = str(d.get("validity") or "").lower()
    return "valid" if v == "valid" else "invalid" if v == "invalid" else "unverified"


def _line_type(t) -> str | None:
    t = str(t or "").strip().lower()
    return t or None


def _email_type(t) -> str | None:
    """RocketReach email `type`: professional -> work, personal -> personal."""
    t = str(t or "").strip().lower()
    return {"professional": "work", "work": "work", "business": "work", "personal": "personal"}.get(t, t or None)


# Single-value fields RocketReach sends besides the emails/phones lists (seen in real responses). Every one is
# kept: an address here that the list left out would otherwise be lost.
_EMAIL_FIELDS = (("current_work_email", "work"), ("recommended_professional_email", "work"), ("current_personal_email", "personal"),
                 ("recommended_personal_email", "personal"), ("recommended_email", None))
_PHONE_FIELDS = (("mobile_phone", "mobile"), ("current_work_phone", "direct dial"), ("phone", None))
_digits = lambda s: "".join(ch for ch in str(s) if ch.isdigit())


def _parse(p: dict) -> ProviderResult:
    emails, phones = [], []
    for e in p.get("emails") or []:
        addr = (e.get("email") if isinstance(e, dict) else e) or ""
        addr = str(addr).strip().lower()
        if "@" not in addr:
            continue
        d = e if isinstance(e, dict) else {}
        smtp = str(d.get("smtp_valid") or "").lower()
        emails.append(ContactValue(addr, _SMTP.get(smtp, "unverified"), d.get("grade"), _email_type(d.get("type"))))
    seen = {c.value for c in emails}
    for k, kind in _EMAIL_FIELDS:
        v = p.get(k)
        addr = str(v).strip().lower() if isinstance(v, str) else ""
        if "@" in addr:
            if addr not in seen:
                emails.append(ContactValue(addr, "unverified", None, kind)); seen.add(addr)
            else:  # the list had it: fill in the personal/work label if the list left it out
                for c in emails:
                    if c.value == addr and not c.line_type and kind:
                        c.line_type = kind
    for ph in p.get("phones") or []:
        num = (ph.get("number") if isinstance(ph, dict) else ph) or ""
        num = str(num).strip()
        if num:
            d = ph if isinstance(ph, dict) else {}
            phones.append(ContactValue(num, _phone_verification(d), d.get("grade"), _line_type(d.get("type"))))
    seen_ph = {_digits(c.value)[-10:] for c in phones}
    for k, kind in _PHONE_FIELDS:
        v = p.get(k)
        num = (v.get("number") if isinstance(v, dict) else v) if v else ""
        num = str(num or "").strip()
        if num and _digits(num) and _digits(num)[-10:] not in seen_ph:
            d = v if isinstance(v, dict) else {}
            phones.append(ContactValue(num, _phone_verification(d) if d else "unverified", d.get("grade"), _line_type(d.get("type")) or kind))
            seen_ph.add(_digits(num)[-10:])
    company = []
    cp = p.get("current_employer_phone")
    if isinstance(cp, dict) and str(cp.get("number") or "").strip():
        # documented as the employer's office line, never a direct line to the person
        company.append(ContactValue(str(cp["number"]).strip(), "valid" if str(cp.get("validity", "")).lower() == "valid" else "unverified",
                                    cp.get("grade"), "office"))
    if emails or phones or company:
        return ProviderResult("matched", emails, phones, company, credits_used=1)
    return ProviderResult("not_found")


class _LookupTypeRefused(Exception):
    """The plan does not allow the requested lookup_type (HTTP 400/403 while it was set)."""


class RocketReach(Provider):
    name = "rocketreach"

    def __init__(self, api_key: str, http=None, sleep=time.sleep, max_wait: int = 60, poll: float = 3.0, lookup_type: str = "premium"):
        self._key, self._http, self._sleep, self.max_wait, self.poll = (api_key or "").strip(), http or _get, sleep, max_wait, poll
        # The default lookup can leave out phone numbers the RocketReach website shows; "premium" asks for the full
        # profile. Set ROCKETREACH_LOOKUP_TYPE (e.g. "phone", or "" for RocketReach's default) to change it.
        self.lookup_type = (lookup_type or "").strip()
        self.notice = ""  # set when the plan refused the lookup type and we fell back

    def configured(self):
        return bool(self._key)

    def _call(self, path, params):
        if not self._key:
            raise ProviderError("RocketReach API key is not set (ROCKETREACH_API_KEY).")
        try:
            status, text = self._http(f"{BASE}{path}?{urlencode(params)}", {"Api-Key": self._key})
        except Exception as e:  # never echo the exception text: it may contain the URL/headers
            raise ProviderError("RocketReach request failed (%s)" % type(e).__name__)
        if status in (400, 403) and "lookup_type" in params:
            raise _LookupTypeRefused()
        if status in (401, 403):
            raise ProviderError("RocketReach rejected the API key or plan (HTTP %d). Check that your plan includes API access." % status)
        if status == 429:
            raise ProviderError("RocketReach rate limit reached (HTTP 429). Try again later.")
        if status >= 400:
            raise ProviderError("RocketReach error (HTTP %d)" % status)
        import json
        try:
            return json.loads(text)
        except ValueError:
            raise ProviderError("RocketReach returned a non-JSON response")

    def lookup(self, person: dict) -> ProviderResult:
        if person.get("linkedin_url"):
            params = {"linkedin_url": person["linkedin_url"]}
        elif person.get("full_name") and person.get("company_name"):
            params = {"name": person["full_name"], "current_employer": person["company_name"]}
        else:
            return ProviderResult("error", error="no LinkedIn URL or name+company to match on")
        if self.lookup_type:
            try:
                data = self._call("/person/lookup", {**params, "lookup_type": self.lookup_type})
            except _LookupTypeRefused:
                self.notice = (f"RocketReach refused lookup_type '{self.lookup_type}' on this plan; used its default lookup "
                               "(phones may be missing). Check the plan or set ROCKETREACH_LOOKUP_TYPE.")
                self.lookup_type = ""  # remember for the rest of the run: one wasted call, not one per lead
                data = self._call("/person/lookup", params)
        else:
            data = self._call("/person/lookup", params)
        waited = 0.0
        while str(data.get("status", "complete")).lower() in ("searching", "waiting", "progress", "not_queued") and data.get("id"):
            if waited >= self.max_wait:
                return ProviderResult("error", error="lookup timed out")
            self._sleep(self.poll)
            waited += self.poll
            res = self._call("/person/checkStatus", {"ids": data["id"]})
            data = (res[0] if isinstance(res, list) and res else res) or {}
        if str(data.get("status", "")).lower() == "failed":
            return ProviderResult("not_found")
        return parse_profile(data)
