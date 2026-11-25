# LeadDeck Phase 2 (Enrichment) Implementation Plan

> For agentic workers: use superpowers:executing-plans (native). Steps use checkboxes.

**Goal:** Enrich campaign leads with verified emails/phones from RocketReach, with caps, ledger, provenance and compliance flags.
**Architecture:** Provider interface (`app/providers`) + orchestration (`app/enrich.py`) + new tables via migration 2 + Flask routes + Campaigns UI. All provider HTTP is injected so tests run offline.
**Tech Stack:** existing Flask/sqlite3/requests stack; no new dependencies.
**Spec:** `docs/superpowers/specs/2026-11-17-phase2-enrichment-design.md`

## Global Constraints
- Originals untouched; no commit/push; no new dependencies; no live provider call in tests (network blocked).
- API key only from env `ROCKETREACH_API_KEY`; never stored, logged, returned by any endpoint or exported.
- Existing non-empty `people` values are never overwritten by enrichment; blanks may be filled only with a verified-valid email or any returned phone.
- Credits: estimate before every run; run stops before a lookup that would exceed the cap; first run default lead cap 10, credit cap 25 (`ENRICH_DEFAULT_CREDIT_CAP`, `ENRICH_DEFAULT_LEAD_CAP`).
- RocketReach response shape is an assumption (not verified against a live account); adapter must tolerate missing fields.

## Review Focus
- Person with no LinkedIn URL and no company: skipped with reason, no credit.
- Provider returns an error/timeout mid-run: item marked error, run continues, no double-charge.
- Cap smaller than eligible leads: stops exactly at cap, status `capped`.
- Re-running the same campaign: already-enriched people skipped, zero credits.
- Do-not-contact person: never looked up.
- Erased person re-appearing in a later sync: not recreated with data.
- API key missing: estimate works, run refused with a clear message.

### Task 1: Schema + config
Files: modify `app/schema.py` (migration 2), `app/config.py`; test `tests/test_enrich_schema.py`.
Produces: tables `contact_points`, `enrichment_runs`, `enrichment_items`, `credit_ledger`, `erasures`; columns `people.do_not_contact`, `people.dnc_reason`; Settings `rocketreach_api_key`, `enrich_credit_cap`, `enrich_lead_cap`.
- [ ] Test: fresh DB has version 2 and the tables; v1 DB upgrades without losing rows.
- [ ] Implement, run tests.

### Task 2: Providers
Files: create `app/providers/{__init__,base,fake,rocketreach}.py`; test `tests/test_providers.py`.
Produces: `ProviderResult(status, emails, phones, credits_used, raw_error)`, `Provider.estimate(n)->int`, `Provider.lookup(person)->ProviderResult`; `RocketReach(api_key, http, sleep, max_wait)`; `FakeProvider(script)`.
Tests: parses fixture with valid/invalid/inconclusive emails; polling until complete; timeout -> status `error`; no key -> `ProviderError`; key never in error text.

### Task 3: Orchestration
Files: create `app/enrich.py`; test `tests/test_enrich.py`.
Produces: `estimate(db, cid, provider)`, `run_enrichment(db, provider, cid, credit_cap, lead_cap, user) -> run dict`, `set_do_not_contact(db, pid, flag, reason)`, `erase_person(db, pid)`; merge skips erased URLs.
Tests: all Review Focus items above.

### Task 4: API routes
Files: modify `app/web.py`, `app/leads.py` (contact points in `get_lead`, export columns Verified Email / Email Status); test `tests/test_enrich_web.py`.
Routes: `GET /api/enrich/config`, `POST /api/campaigns/<id>/enrich/estimate`, `POST /api/campaigns/<id>/enrich` (background thread, 202), `GET /api/enrich/runs[/<id>]`, `GET /api/enrich/ledger`, `POST /api/leads/<id>/do-not-contact`, `DELETE /api/leads/<id>` (admin).

### Task 5: UI + docs
Files: modify `app/static/app.js`, `README.md`, `.env.example`. Enrich panel on a campaign (estimate, caps, confirm, progress, results), contact points on rows, DNC/erase buttons, ledger table.

### Task 6: Verify
Run full suite; confirm originals unchanged; report what was and was not tested (no live RocketReach call).
