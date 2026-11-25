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
    import json
    r.shape = json.dumps(shape_of(p), sort_keys=True)[:2000]
    return r


def _parse(p: dict) -> ProviderResult:
    emails, phones = [], []
    for e in p.get("emails") or []:
        addr = (e.get("email") if isinstance(e, dict) else e) or ""
        addr = str(addr).strip().lower()
        if "@" not in addr:
            continue
        smtp = str(e.get("smtp_valid") or "").lower() if isinstance(e, dict) else ""
        emails.append(ContactValue(addr, _SMTP.get(smtp, "unverified"), (e.get("grade") if isinstance(e, dict) else None)))
    for ph in p.get("phones") or []:
        num = (ph.get("number") if isinstance(ph, dict) else ph) or ""
        num = str(num).strip()
        if num:
            phones.append(ContactValue(num, "unverified", None))
    if not phones:  # tolerate scalar fields some response variants use (unverified assumption)
        for k in ("current_work_phone", "phone", "mobile_phone"):
            if isinstance(p.get(k), str) and p[k].strip():
                phones.append(ContactValue(p[k].strip(), "unverified", None))
                break
    company = []
    cp = p.get("current_employer_phone")
    if isinstance(cp, dict) and str(cp.get("number") or "").strip():
        company.append(ContactValue(str(cp["number"]).strip(), "valid" if str(cp.get("validity", "")).lower() == "valid" else "unverified",
                                    cp.get("grade")))
    if emails or phones or company:
        return ProviderResult("matched", emails, phones, company, credits_used=1)
    return ProviderResult("not_found")


class RocketReach(Provider):
    name = "rocketreach"

    def __init__(self, api_key: str, http=None, sleep=time.sleep, max_wait: int = 60, poll: float = 3.0):
        self._key, self._http, self._sleep, self.max_wait, self.poll = (api_key or "").strip(), http or _get, sleep, max_wait, poll

    def configured(self):
        return bool(self._key)

    def _call(self, path, params):
        if not self._key:
            raise ProviderError("RocketReach API key is not set (ROCKETREACH_API_KEY).")
        try:
            status, text = self._http(f"{BASE}{path}?{urlencode(params)}", {"Api-Key": self._key})
        except Exception as e:  # never echo the exception text: it may contain the URL/headers
            raise ProviderError("RocketReach request failed (%s)" % type(e).__name__)
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
