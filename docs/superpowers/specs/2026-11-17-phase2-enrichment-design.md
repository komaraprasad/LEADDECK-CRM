# LeadDeck Phase 2: Enrichment: Design (draft for approval)

Date: 2026-11-17. Status: **DRAFT, not approved. Nothing is built.**

## 1. Problem and success criteria

Phase 1 gives us leads in one database (Campaign step). Phase 2 is the **Data** box in the manager's diagram: for the leads in a campaign, fill in verified email, phone and other details, with the source of every value recorded.

Success means:
- A user selects a campaign, sees a **credit estimate**, confirms, and gets enriched contacts back.
- Every email/phone shows its provider, date and verification status.
- No run can spend more than the cap the user set.
- Existing values from TalentPulse etc. are never overwritten or lost.
- We can state, from a measured pilot, the match rate and verified-valid rate per provider.

## 2. Goals, non-goals, anti-goals

Goals: RocketReach enrichment now; Apollo and an email verifier later through the same interface; per-run caps; credits ledger; compliance flags.

Non-goals (this phase): sending emails (Phase 4), CRM (Phase 3), analytics (Phase 5), scraping LinkedIn ourselves, buying more providers.

Anti-goals (a failure even if it "works"): silently burning credits; showing unverified emails as if they were good; keys visible in the UI, database, logs or exports; touching the four original tool folders; any push or commit.

## 3. What we know and don't (research, 2026-11-17)

Verified in sources (vendor/third-party pages, not on your account):
- RocketReach plans are per user: Essentials about $329-399/yr (email only), Pro about $829/yr (adds phones), Ultimate about $1,699-2,099/yr. Phone lookups are Pro and above.
- RocketReach documents more than one credit system; older lookup pages say lookups consume **export credits** (LeadHaste, Nov 2026). Annual plans advertise unlimited lookups but cap exports.
- Failed or low-confidence lookups can still consume credits if RocketReach returns a result.

**Conflicting, must be checked on your account:** whether API access is on every paid plan or Ultimate only (Salesmotion and Emelia say every paid plan; hackceleration, enrich.so, Cleanlist say Ultimate or Team). **Your plan decides if Phase 2 can run at all through the API.**

Assumptions from my own knowledge, to be confirmed in the pilot: lookup is asynchronous with polling (so localhost works, no webhook needed); email results carry a quality grade and an SMTP-valid flag; credit use is returned in responses.

## 4. Approaches considered

A. **RocketReach only, hard-wired.** Fastest. Rejected: adding Apollo later means a rewrite, and we cannot compare providers.
B. **Provider interface, RocketReach first (recommended).** One small adapter per provider, fake provider for offline tests, Apollo/verifier added later without touching the rest. Slightly more upfront design, no extra cost.
C. **Buy a waterfall tool (Clay, FullEnrich).** Rejected for now: recurring cost, and the manager asked for a low budget and our own product.

## 5. Design (approach B)

Data model (new migration, existing tables untouched):
- `contact_points`: person_id, kind (email/phone), value, provider, found_at, verification (unverified/valid/invalid/risky/unknown), verified_at, provider_grade, is_primary.
- `enrichment_runs`: campaign_id, provider, status, estimated_credits, credit_cap, lead_cap, started/finished, created_by, dry_run.
- `enrichment_items`: run_id, person_id, status (matched/not_found/error/skipped), credits_used, error.
- `credit_ledger`: provider, run_id, credits, balance_after (when the API reports it), note.
- `people` gets `do_not_contact` (bool, reason, date). Existing people.email/phone stay as-is; best contact point is shown alongside.

Code: `app/providers/base.py` (interface: `lookup(person) -> ProviderResult`, `estimate(people) -> credits`), `rocketreach.py`, `fake.py`, `app/enrich.py` (run orchestration, caps, skip rules), routes under `/api/enrich/*`, UI additions on the Campaigns page.

Rules:
- Cheapest first: use existing data, then the provider. Skip anyone already enriched or marked do-not-contact.
- Estimate shown before every run; the run stops the moment the cap is reached; the first run per campaign defaults to a small lead cap (10).
- Match on the normalized LinkedIn URL first, name+company second (marked lower confidence).
- Phones only on explicit request, with their own estimate.
- Emails count as "usable" only when verified; unverified ones are shown but flagged.
- API key only in an environment variable (`ROCKETREACH_API_KEY`); never stored, logged or exported. Responses saved without the key.
- Providers are called only by explicit user action; no live call in tests (network blocked as in Phase 1).
- Delete-person action (removes person, contact points and sources) for privacy requests.

## 6. Cost and budget

Software cost: 0 on top of what you already pay. Spending is credits only, capped per run. Email verifier is optional and later (about $8 per 1,000 for Bouncer/NeverBounce; MillionVerifier $89 per 50,000), chosen after the pilot shows how bad the raw bounce rate is.

## 7. Accuracy validation

Pilot of 20-30 real leads (cap set by you): record match rate, verified-valid rate, credits per useful contact, and wrong-person cases by manual spot check. Report results with what was and was not measured before scaling.

## 8. Risks

- API not included in your RocketReach plan (blocking; check first).
- Vendor accuracy claims are not independent; bounce rates reported at 10-30%.
- Thin coverage for India-based contacts is likely (inference); the pilot will show it.
- LinkedIn/privacy law (GDPR, India DPDP): contact data should be used only with a lawful basis; do-not-contact and delete flows are the minimum.
- Credit rules may differ from docs (several credit systems); ledger reads actual numbers from responses.

## 9. Open items needing your answer

1. Your RocketReach plan name and whether you can create an API key.
2. Per-run credit cap and pilot size you are comfortable with.
3. Email-only first, phones later (my call; phones are costlier and Pro+).

## 10. Left to the builder

File layout details, UI wording, and retry/backoff values are the builder's choice within the rules above.
