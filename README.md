# LeadDeck - Phase 1: Foundation and Campaign layer

One place for every lead from the four LinkedIn tools (CommentMiner, TalentPulse, Hiring Badge Finder, JobHarvest):
de-duplicated, with a record of where each lead came from, a searchable Leads list, Campaigns, Excel/CSV export and login.

This folder is a **separate copy**. The four tool folders and the original `ai-lead-toolkit` were not modified.
Nothing in here has been committed, pushed or deployed. Design: `docs/superpowers/specs/`. Plan: `docs/superpowers/plans/`.

## Run it locally (Windows)

1. Double-click `run.bat`. It creates a virtual environment, installs `requirements.txt`, creates a `.env` with a random
   secret and **prints the admin login once** (write it down), then opens http://localhost:3000.
2. Sign in. Open **Sync**, click **Dry run** on a tool to preview, then **Sync now**.
3. **Leads**: search and filter, tick rows or "Add all matching" into a **Campaign**, export CSV or Excel.

Data is kept in `leaddeck.db` (SQLite) locally. The **Tools** tab is the original dashboard (live health of the four tools).

## Deploy (when you approve it)

Render (free) + Neon Postgres (free). Set these in the Render dashboard, never in files:

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | Neon connection string (`postgresql://...?sslmode=require`) |
| `SECRET_KEY` | long random string (Render can generate it). The app refuses to start without a real one |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | first admin, created only when there are no users |
| `SYNC_TOKEN` | optional; enables `POST /api/sync/cron` (header `X-Sync-Token`) for a free external scheduler such as cron-job.org |
| `TOOL_URL_<TOOL>` | optional overrides of the four tool URLs |

Free Render sleeps after 15 minutes idle (about 1 minute to wake). `GET /healthz` is a lightweight wake-up endpoint.

## How sync works

* LeadDeck only **reads** each tool's existing endpoints (TalentPulse and Hiring Badge Finder `/api/leads`, CommentMiner `/api/posts/combined`, JobHarvest `/results`). It never writes to them.
* A tool's **first** sync is a dry run (counts only) until you click **Sync now**. The cron endpoint follows the same rule.
* One tool failing (asleep, HTML error page, timeout, 5xx) shows an error for that tool; the others continue. One bad row is skipped, never fatal.
* **Fallback:** on the Sync page, **Import file** accepts a tool's own CSV/Excel export.
* Duplicates are matched on the normalized LinkedIn URL. A value is never replaced by a blank; the newer non-empty value wins; every source row is kept.
* Matches made only on name + company (no profile URL) stay within one tool, are marked "low match", and are never merged across tools.

## Tests

`python -m pytest -q` (158 tests, offline, fixture data modeled on each tool's schema; network access is blocked during tests).

## Deviations from the approved plan (package installs were blocked while building)

| Planned | Used instead | Why / effect |
|---|---|---|
| FastAPI | Flask | FastAPI could not be installed; Flask was already available and includes a test client |
| SQLAlchemy + Alembic | Small SQL layer + versioned migrations (`app/db.py`, `app/schema.py`) | not installable. Same tables as the design |
| bcrypt | `hashlib.scrypt` (standard library) | not installable; scrypt is a strong, standard password hash |
| httpx | requests | not installable |

## Known limits - please read

* **Postgres/Neon is untested.** All tests ran on SQLite. The Postgres path (`psycopg`) is written but has never been run. Run a first test against a Neon database before relying on it.
* **Live tool responses are unverified.** Mappers use field names read from each tool's source code. Run each tool's **Dry run** first and check the counts.
* **`lead_type` for a person found by two tools depends on which tool synced first** (the first specific type, candidate or employer contact, sticks; the other tool is still listed under "Found by"). Decision for your manager: keep this, or add a "both" type.
* The four tools still have **no login**; anyone with their URL can read them. LeadDeck itself is protected. Two tools run on plain `http://`.
* LinkedIn terms restrict automation; storing personal data brings GDPR / India DPDP duties. Needs a retention and deletion process before outreach (Phase 4).
* No email/phone enrichment, outreach, CRM or analytics yet (Phases 2 to 5).
* Single worker process (sync locking is in-process); fine for a small team.
* Neon free storage is stated as 1 GB by Neon and 0.5 GB by some other sources; the Sync page shows the database size.

## Cost

$0 for Phase 1 (Render free + Neon free). First upgrade if needed: always-on Render web service from about $7/month.

## Phase 2: Enrichment (RocketReach)

Added on top of Phase 1; nothing in the four original tools is touched.

**Setup:** put `ROCKETREACH_API_KEY=...` in `.env` (never paste it in chat, never commit it) and restart. Optional: `ENRICH_DEFAULT_CREDIT_CAP` (default 25) and `ENRICH_DEFAULT_LEAD_CAP` (default 10).

**Use:** Campaigns -> Open -> *Enrich contacts*. Click **Estimate cost** (shows how many leads would be looked up, how many are skipped and why, and the estimated credits), then **Run enrichment**. The run stops before it would exceed the credit cap. Re-running a campaign skips people already looked up.

**Rules:** existing emails/phones are never overwritten; every found value is stored in `contact_points` with provider, grade, verification and date; an email is copied onto the lead only if it is verified valid and the lead had none; do-not-contact people are never looked up; **Erase** deletes a person and remembers their profile URL so a later sync cannot bring them back (people with no profile URL cannot be blocked from returning).

**API:** `GET /api/enrich/config`, `POST /api/campaigns/<id>/enrich/estimate`, `POST /api/campaigns/<id>/enrich` (needs `"confirm": true`), `GET /api/enrich/runs`, `GET /api/enrich/ledger`, `POST /api/leads/<id>/do-not-contact`, `DELETE /api/leads/<id>` (admin).

**Not verified (read this):** the RocketReach adapter was written from documentation and tested only against made-up responses. No real RocketReach call has been made. Whether your plan includes API access, the exact response fields, and how credits are counted must be confirmed with a small pilot (cap 3-5 leads first). LeadDeck counts 1 credit per lookup that returns data; compare with your RocketReach dashboard. The credit ledger is exposed through the API only; there is no ledger page yet. Phones come back from the same lookup when your plan includes them; there is no separate phone step.

**Pilot stats (Phase 2):** opening an enriched campaign shows measured results: looked up, found anything, valid email, direct phone, company phone only, no phone, credits used and credits per valid email (`GET /api/campaigns/<id>/enrich/stats`). "Valid" is the provider's own check. Employer/switchboard numbers are saved as `company_phone` and are never copied into a lead's phone field.

**Wrong-person guard:** after each lookup LeadDeck compares the provider's returned name with the lead's name. No shared word = a different person: nothing is saved, the credit is counted, the lead shows as "wrong person" in the stats and is not retried automatically. A different employer only adds a "needs review" note (people change jobs). If the provider returns no name, nothing can be checked and the result is kept.

## Phase 3: CRM

**Pages:** *Pipeline* (board of every campaign lead by stage; drag a card to move it; click it for the detail panel) and *Tasks* (Overdue / Today / Upcoming / All open / Done, "only my tasks"). The detail panel holds contact details, stage, owner, tasks, notes and a full timeline.

**Stages:** New, Enriched, Ready to contact, Contacted, Replied, Interested, Meeting booked, Won, Lost, Not a fit. Lost and Not a fit need a reason. Adding a lead to a campaign creates its CRM row; existing campaign leads are backfilled at startup.

**Automatic rules:** enrichment moves New -> Enriched; Enriched + a valid email + passed wrong-person check + not do-not-contact -> Ready to contact. Automation only moves forward and never past Ready. Safety demotion: Ready -> Enriched if the lead becomes do-not-contact, loses its last valid email, or turns out to be the wrong person. Contacted-or-later is refused for do-not-contact leads. Every change is written to the lead's timeline with who/what did it. **Erase** removes the CRM rows, notes, tasks and timeline too.

**Phase 4 intake (disabled until configured):** set `CRM_EVENT_TOKEN` in `.env`; then `POST /api/crm/events` with header `X-Event-Token` and JSON `{"type": "email_sent|reply|bounce|unsubscribe", "email": "...", "detail": {...}}` updates the matching lead (sent -> Contacted, reply -> Replied, bounce -> email marked invalid, unsubscribe -> do-not-contact). Unknown emails are logged, not fatal. Needs a public HTTPS address to receive webhooks from an outreach tool, so use it after deployment. Not tested against a real outreach tool.

**Known limits:** one stage per lead (not per campaign); no deal values or forecasting; "today" for tasks uses the browser's date, timestamps are shown in UTC; the board shows the 50 newest leads per stage (search/filters reach the rest); drag-and-drop to a column that is off-screen needs the board scrolled so the column is visible (the lead panel's Stage menu always works).

## Leads workspace upgrade (Phase 3.1)

Ideas adopted from HubSpot (quick filters, saved views, record preview), Zoho CRM (custom list views) and Pipedrive ("rotting" deals):

- **Suggestions on every filter**: company, location, job title and the main search box show the most common matching values (with counts) as you type or when you click an empty box. Arrow keys + Enter work. Results update as you type (no Enter needed).
- **Click a lead to open its full record** (contacts, stage, owner, notes, tasks, timeline) without leaving the Leads page. Previewing never adds a lead to the pipeline.
- **Sortable columns**, filter chips with one-click removal, "Clear all", select-all-on-page, and filters kept in the page address (reload / share a link).
- **New filters**: job title, CRM stage, contact quality, hide do-not-contact.
- **Contact quality score (0-100)**: verified email 50, other usable email 20, direct phone 35, company phone only 10, found by 2+ tools 15. High = 50+, Medium = 20-49, Low = under 20. It measures how reachable a lead is, not how good a fit it is.
- **Saved views**: save the current filters + sort as a named view, private or shared with the team (creator or admin can delete).
- **Bulk assign owner** for the selected rows.
- **Stale flag on the CRM board**: a lead with no activity (stage change, note, task, email event) for 7 days in Ready / Contacted / Interested / Meeting, or 2 days in Replied, shows a red "Nd idle" badge; "stale only" filter. Thresholds: `STALE_DAYS` in `app/crm.py`.
- Search on the CRM board also suggests as you type.

New endpoints: `GET /api/leads/suggest`, `GET/POST/DELETE /api/views`, `POST /api/crm/bulk/owner`; `/api/leads` accepts `sort`, `dir`, `title`, `stage`, `quality`, `owner`, `dnc`. Schema migration 6 adds `saved_views`.

## Design refresh

New look built on a "deck of cards" idea (no new dependencies; fonts Bricolage Grotesque + Figtree load from Google Fonts and fall back to system fonts offline):
- Colour carries meaning: every CRM stage has its own colour (pills, board column tops, the pipeline summary strip); contact quality is a ring (green 50+, amber 20-49, grey below).
- Motion answers an action or marks one arrival: cards deal onto the CRM board when it opens, a moved card flashes where it lands, toasts slide in and count down, the lead panel slides in, suggestion lists pop open, rings fill. `prefers-reduced-motion` turns all of it off.
- Sidebar icons, avatars with initials, skeleton loading rows, friendly empty states, animated counters on Sync, split-screen sign-in with a show-password toggle. On phones the sidebar becomes a top icon bar.

## Analytics (Phase 5)
Open **Analytics** in the sidebar (`/analytics`, API `GET /api/analytics?days=7|30|90`). Read-only: it computes everything from data LeadDeck already stores and writes nothing.
- **Tiles:** leads, valid-email and direct-phone rates, credits used, credits per valid email, open and overdue tasks.
- **Pipeline funnel:** leads that have *ever reached* each stage (from the CRM history) and the share that moved on from the previous stage; ended leads (won / lost / not a fit); count of stale leads.
- **Contact quality:** high / medium / low bands of the 0-100 reachability score (it does not measure fit).
- **Per-day charts:** credits used and CRM activity. **Sources:** leads, valid emails, phones and average score per tool. **Team:** leads and open tasks per owner.
- **Outreach:** sent, reply rate, bounce rate and unsubscribes come from `lead_events` (`email_sent`, `reply`, `bounce`, `unsubscribe`). Until any exist the panel says so; nothing is estimated.
- Credits are LeadDeck's own count (1 per lookup that returned data); compare with your RocketReach dashboard.
