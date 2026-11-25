# LeadDeck Phase 1: Foundation and Campaign Layer (Design)

Date: 2026-11-17
Status: DRAFT, awaiting approval. Nothing has been built.
Size: a project (new backend, database, login, UI pages).

## 1. Intent and why

Your manager asked for one end-to-end lead-generation product:
Campaign -> Data (enriched) -> Outreach -> Analytics -> CRM.

Today there are four separate LinkedIn tools, each with its own database and no shared view of leads. Every later phase (enrichment, CRM, outreach, analytics) needs one place where all leads live, de-duplicated, with a record of where each came from. Phase 1 builds that place and the "Campaign" step on top of it.

Phase 1 answers: "Can I see every lead from all four tools in one list, without duplicates, group them into a campaign, and export it?"

## 2. Goals

1. Pull leads from all four tools into one Postgres database on a "Sync now" action.
2. Store people and companies as separate records, linked to each other.
3. Remove duplicates across tools (same person found by two tools = one lead, two sources).
4. Keep provenance: for every lead, which tool(s) found it, when, and the raw record.
5. Tag each lead as candidate, employer contact, or unknown.
6. Campaigns: create a named campaign, add leads by filter or by hand, export it as CSV/Excel.
7. Login so the data is not public.
8. Keep the current LeadDeck tool-health dashboard working.

## 3. Non-goals (Phase 1)

- No enrichment of phone/email (Phase 2).
- No CRM pipeline, outreach sending, or analytics (Phases 3 to 5).
- No changes to the four live tools.
- No login added to the four tools themselves (flagged as a separate risk).
- No automated LinkedIn access from LeadDeck. It only reads what the tools already collected.

## 4. Anti-goals (a failure even if it "works")

- Duplicate people or companies in the list.
- A sync that silently drops or overwrites good data with blanks.
- Invented or guessed contact data. Phase 1 stores only what a tool reported.
- Leads visible without login.

## 5. Constraints

- Budget: $0 for Phase 1 (Render free + Neon free).
- Stack: Python. Follows the tools' existing stack (FastAPI, Postgres).
- Windows 11 dev machine, Google Antigravity IDE, local run via run.bat must keep working.
- Existing LeadDeck is stdlib `http.server` + one `index.html`, deployed on Render via render.yaml.
- Render free tier sleeps after idle, so in-process schedulers are unreliable.
- Do not make live requests to the tools, push, or deploy without your explicit go-ahead.

## 6. Approaches considered

| # | Approach | Trade-off |
|---|----------|-----------|
| 1 | Pull from each tool's existing read endpoints (chosen) | Tools unchanged, low risk; needs one mapper per tool and tools must be reachable |
| 2 | Import Excel/CSV exports | Safest, works offline, but manual and loses fields (kept as fallback) |
| 3 | Read the tools' databases directly | Fastest, but breaks whenever a tool's internal tables change |
| 4 | Change each tool to push leads | Real-time, but means editing four live tools |

Chosen: 1, with 2 as fallback. You said "do which is best" and delegated the choice. This is my call, not something you agreed to in detail.

## 7. Design

### 7.1 Architecture (my call)

- Add a FastAPI app to the LeadDeck repo. It serves the existing dashboard page, the health check, and the new leads API and pages.
- Postgres on Neon (free tier). SQLAlchemy plus Alembic for schema and migrations.
- Render start command changes from `python server.py` to uvicorn, and the build step installs requirements. `run.bat` is updated to match. The old health logic is moved over, not rewritten.
- Local development uses the same Postgres connection string from an environment variable. No secrets are committed.

### 7.2 Data model

- `companies`: id, name, linkedin_url, domain, industry, size, website, first_seen, last_seen.
- `people`: id, company_id, full_name, headline, title, location, linkedin_url (unique, normalized), email, phone, lead_type (candidate / employer_contact / unknown), first_seen, last_seen.
- `lead_sources`: id, person_id or company_id, tool (commentminer / talentpulse / hiringbadge / jobharvest), source_key, raw_payload (JSON), collected_at, synced_at. This is the provenance record.
- `job_postings`: id, company_id, title, job_url, location, posted, tool job id. A hiring signal from JobHarvest.
- `campaigns`: id, name, description, created_by, created_at.
- `campaign_leads`: campaign_id, person_id, added_at, status (placeholder for later phases).
- `sync_runs`: id, tool, started, finished, fetched, created, updated, errors.
- `users`: id, email, password hash, role.

### 7.3 Where each tool's data goes (from the code I read)

| Tool | Read from | Becomes |
|------|-----------|---------|
| TalentPulse | GET `/api/leads` | people, type candidate |
| Hiring Badge Finder | GET `/api/leads` | people, type employer_contact, plus company fields |
| CommentMiner | GET `/api/posts/combined` | people, type unknown (tag "commenter"), plus company website/LinkedIn if present |
| JobHarvest | GET `/results` / saved jobs | companies and job_postings; people only when a contact profile link exists |

Field names were read from the tools' source code. They have not been checked against the live responses.

### 7.4 De-duplication and merging

- Person key: normalized LinkedIn profile URL (lowercase, strip query string and trailing slash, keep `/in/<slug>`). If missing, fall back to name plus company, flagged "low confidence" and never auto-merged across tools.
- Company key: LinkedIn company URL slug, else website domain, else normalized name.
- Merge rule: a non-empty value is never replaced by an empty one. If two tools disagree, the most recently collected non-empty value wins, and every source row is kept.
- Re-running a sync is safe (idempotent): same input gives the same rows.

### 7.5 Sync

- "Sync now" button per tool and for all tools. Each run writes a `sync_runs` row.
- First run for each tool is a dry run showing counts (would create / would update / would skip) before anything is written.
- One failed tool never blocks the others. Errors show in the UI.
- Scheduling (my call): because Render free sleeps, do not rely on an in-process scheduler. Provide a token-protected `/api/sync` endpoint that a free external cron (for example GitHub Actions or cron-job.org) can call. This is optional and off by default.
- CSV/Excel import for each tool's export format, using the same mappers, as the fallback.

### 7.6 UI

- Keep the existing four-tool health dashboard as the "Tools" tab.
- New "Leads" tab: table with search, filters (tool, lead type, company, location, has email, has phone), and CSV/Excel export.
- New "Campaigns" tab: create a campaign, add leads from the current filter or by selection, view and export.
- New "Sync" tab: per-tool last sync, counts, errors, buttons.
- Login page. One admin created from environment variables; additional users added by the admin.

### 7.7 Auth (my call)

Email and password, hashed, session cookie. HTTPS comes from Render. No self-signup. This protects LeadDeck only; the four tools remain open and should be fixed separately.

## 8. Acceptance criteria

1. With fixture data for all four tools, a sync creates the expected people and companies, and a second sync creates no duplicates.
2. A person present in two tools appears once, with two source records.
3. A blank value in a later sync never erases an existing value.
4. A tool that is down produces a visible error and does not stop the other three.
5. Leads, campaigns and export are not reachable without logging in.
6. A campaign created from a filter exports the same rows the filter shows.
7. The Tools health tab still works as before.
8. Automated tests pass locally with no network access.

## 9. Verification plan

- Unit tests for URL normalization, mappers, merge rules, and de-duplication using fixtures modeled on each tool's real schema.
- Integration tests against a local Postgres.
- First live sync only after you approve, starting with a dry run. Results will be reported as tested or not tested.

## 10. Risks

- Live responses may differ from the code I read, so a mapper may need adjusting on the first dry run.
- The four tools have no login, so anyone with a URL can read leads. LeadDeck will be protected; the tools are not.
- Two tools use plain `http://` on AWS.
- LinkedIn terms restrict automation, and storing personal data brings GDPR and India DPDP duties. Needs a retention and deletion path before outreach (Phase 4).
- Render and Neon free tiers have cold starts and storage limits.

## 11. Left to the builder

Exact column types and indexes, pagination size, UI styling inside the existing look, and the module layout, as long as they follow this document and existing conventions.

## 12. Calls I made that you can change

- FastAPI + Neon Postgres + SQLAlchemy/Alembic.
- Email/password login with a single admin seeded from environment variables.
- External cron instead of an in-app scheduler.
- CommentMiner commenters typed "unknown" until a rule says otherwise.
- Low-confidence name-plus-company matches are never auto-merged.
