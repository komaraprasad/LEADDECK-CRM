# LeadDeck Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a separate copy of LeadDeck that pulls leads from the four LinkedIn tools into one de-duplicated Postgres database, with login, a leads list, campaigns and export.

**Architecture:** A FastAPI app (SQLAlchemy 2.0 models, Alembic migrations) next to the existing health dashboard. One mapper per tool turns that tool's read-endpoint JSON into normalized person/company/source records. A single upsert service does de-duplication and merging. Tests use SQLite in memory and fixtures; production uses Neon Postgres via `DATABASE_URL`.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, SQLAlchemy 2.0, Alembic, httpx, openpyxl, bcrypt, itsdangerous (session cookie), pytest.

**Spec:** `2026-11-17-phase1-foundation-design.md` (design doc approved by the user on 2026-11-17).

## Global Constraints

- NEVER modify the four tool folders or the original `ai-lead-toolkit` folder under `C:\Users\Inspironlabs\Desktop\finnal versions\`. All work happens in the new folder `C:\Users\Inspironlabs\Desktop\finnal versions\leaddeck-phase1`.
- NEVER run `git push`, add a git remote, or commit. No git commands are in this plan. The new folder has no `.git` directory.
- No live requests to the four tools, no deploy, until the user explicitly says so. Tests run offline against fixtures.
- Never print, log or commit secrets. Config comes from environment variables: `DATABASE_URL`, `SECRET_KEY`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `SYNC_TOKEN`, and the four tool base URLs (defaults = the live URLs already in `server.py`).
- Phase 1 stores only values a tool reported. No guessing or generating contact data.
- Stack follows the tools: Python, FastAPI, Postgres. Budget: $0 (Render free + Neon free).
- The existing health check behavior (`/api/health`, 15 s timeout, four tools pinged in parallel) and `run.bat` on port 3000 keep working.
- Windows 11 compatible commands (PowerShell or cmd).
- Database: Neon Postgres (free). Not Render Postgres (free DB expires) and not Supabase (free projects pause after 7 days idle). Engine created with `pool_pre_ping=True` and a connect retry, because Neon computes scale to zero after about 5 minutes idle.
- Plan for the smaller storage figure: sources disagree on Neon free storage (1 GB per Neon's own page, 0.5 GB on several blogs). Design and test as if 0.5 GB, and show database size on the Sync page.
- Render free filesystem is ephemeral: no SQLite or uploaded files in production; all state in Postgres. Import uploads are processed in memory.
- Render free web service sleeps after 15 minutes idle (about 1 minute to wake); the app exposes a lightweight `GET /healthz` (no DB, no login) for the external cron to wake it.

## Review Focus

1. LinkedIn URL variants for one person (`HTTPS://www.linkedin.com/in/Jane-Doe/?utm=x`, `in.linkedin.com/in/jane-doe`, trailing slash, percent-encoded slug) must normalize to one key. Test in Task 3.
2. A tool that is asleep or returns HTML, an empty body, a 5xx, or a timeout must give a visible error for that tool only; the other three still sync. Test in Task 6.
3. The same person appearing twice inside one payload, or null/empty fields in a later row, must not create duplicates or erase existing values. Test in Task 4.
4. Export cells starting with `=`, `+`, `-` or `@` (e.g. a headline of `=HYPERLINK(...)`) must be neutralized so Excel does not run them. Test in Task 9.
5. Two clicks on "Sync now" (concurrent syncs of one tool) must not double-insert; the second is rejected while the first runs. Test in Task 6.

---

## File Structure

New folder `leaddeck-phase1/`:

- `app/main.py` creates the FastAPI app, wires routers, serves static UI.
- `app/config.py` reads environment settings.
- `app/db.py` provides the engine, `SessionLocal`, `Base`, `get_db`.
- `app/models.py` defines all tables.
- `app/normalize.py` provides pure key-normalization functions.
- `app/merge.py` provides the upsert/de-dup service.
- `app/mappers/{talentpulse,hiringbadge,commentminer,jobharvest}.py` map one tool's JSON to `NormalizedRecord`.
- `app/sync.py` provides fetchers, dry run, sync runs, locking.
- `app/importer.py` provides CSV/Excel import using the same mappers.
- `app/auth.py` provides password hashing, login, session guard, admin seed.
- `app/routers/{health,leads,campaigns,sync,auth}.py` provide the HTTP routes.
- `app/exporter.py` provides CSV/xlsx export with formula neutralization.
- `app/static/` holds `index.html` (existing dashboard kept as the Tools tab), `leads.html`, `campaigns.html`, `sync.html`, `login.html`, `app.js`, `app.css`.
- `alembic/` and `alembic.ini` for migrations.
- `tests/` with `conftest.py`, `fixtures/*.json`, and one test file per module.
- `reference/` holds read-only copies of the four tool folders (for deriving fixtures only; never imported).
- `requirements.txt`, `render.yaml`, `run.bat`, `README.md`, `.env.example`, `.gitignore`.

---

### Task 1: Workspace copy, skeleton and health parity

**Files:**
- Create: `leaddeck-phase1/` (copied from `ai-lead-toolkit`, excluding `.git`), `leaddeck-phase1/reference/<four tool folders>` (copied), `app/__init__.py`, `app/config.py`, `app/main.py`, `app/routers/health.py`, `requirements.txt`, `.env.example`, `.gitignore`, `tests/conftest.py`, `tests/test_health.py`
- Source (read only): `ai-lead-toolkit/server.py` (the `TOOLS` dict and `check_tool_health()` logic)

**Interfaces:**
- Produces: `app.config.Settings` (fields: `database_url: str`, `secret_key: str`, `admin_email: str`, `admin_password: str`, `sync_token: str`, `tool_urls: dict[str, str]` with keys `commentminer`, `talentpulse`, `hiringbadge`, `jobharvest`); `app.config.get_settings() -> Settings`; `app.main.app: FastAPI`; `GET /api/health` returning the same JSON shape as the old `server.py`.

- [ ] **Step 1: Copy, do not move.** Copy `ai-lead-toolkit` to `leaddeck-phase1` without `.git` (`robocopy ... /E /XD .git`). Copy each of the four tool folders into `leaddeck-phase1\reference\`. Verify originals are untouched by comparing file counts before and after.
- [ ] **Step 2: Read old `server.py` and record the exact JSON shape of `/api/health` in the test.** Write `test_health_matches_legacy_shape` asserting keys per tool and that one tool failing does not fail the others (httpx mocked, no network).
- [ ] **Step 3: Run it.** Run `pytest tests/test_health.py -v`. Expected: FAIL (module missing).
- [ ] **Step 4: Implement** `get_settings()` and `GET /api/health` in `app/routers/health.py`, porting the logic from `server.py` with `httpx` and a 15 s timeout, pinging the four tools concurrently. Serve the existing `index.html` at `/`.
- [ ] **Step 5: Run** `pytest tests/test_health.py -v`. Expected: PASS.
- [ ] **Step 6: Start locally** with `uvicorn app.main:app --port 3000` and confirm `/` loads and `/api/health` responds (health pings to live tools are allowed only if the user has authorized live requests; otherwise skip and report as untested).

### Task 2: Database models and migrations

**Files:**
- Create: `app/db.py`, `app/models.py`, `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_initial.py`, `tests/test_models.py`

**Interfaces:**
- Produces: `Base`, `SessionLocal`, `get_db()`; models `Company`, `Person`, `LeadSource`, `JobPosting`, `Campaign`, `CampaignLead`, `SyncRun`, `User` with the columns in spec section 7.2. `Person.lead_type` is a string constrained to `candidate | employer_contact | unknown`. `Person.linkedin_url` and `Company.linkedin_url` are nullable and unique when present. `LeadSource` stores `tool`, `source_key`, `raw_payload` (JSON), `collected_at`, `synced_at`, and a nullable `person_id` / `company_id`. `Person` also has `match_confidence` (`high | low`).

- [ ] **Step 1: Write** `test_create_all_tables` (SQLite in memory creates every table) and `test_person_linkedin_url_unique` (inserting two people with the same non-null URL raises `IntegrityError`; two with null URL is allowed).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** models with SQLAlchemy 2.0 typed mappings; use `JSON` type that works on SQLite and Postgres (`JSONB` variant on Postgres). Generate the Alembic initial migration from the models.
- [ ] **Step 4: Run, expect PASS.** Also run `alembic upgrade head` against a temporary SQLite file and confirm it succeeds.

### Task 3: Key normalization

**Files:**
- Create: `app/normalize.py`, `tests/test_normalize.py`

**Interfaces:**
- Produces: `normalize_person_url(url: str | None) -> str | None` (returns `https://www.linkedin.com/in/<slug>` lowercase slug, or `None`); `normalize_company_url(url: str | None) -> str | None` (`https://www.linkedin.com/company/<slug>`); `normalize_domain(website: str | None) -> str | None` (lowercase host, no `www.`, no path); `normalize_name(name: str | None) -> str` (casefold, collapsed whitespace, accents stripped).

- [ ] **Step 1: Write tests** for: `HTTPS://www.linkedin.com/in/Jane-Doe/?utm=x`, `in.linkedin.com/in/jane-doe`, `linkedin.com/in/jane-doe/`, percent-encoded slug, `/in/jane-doe/details/experience/` all returning `https://www.linkedin.com/in/jane-doe`; a Sales Navigator URL (`/sales/lead/...`) returns `None` (not a profile key); empty/`None` returns `None`; `normalize_domain("https://WWW.Acme.com/careers")` returns `acme.com`; `normalize_name("  José  Ñúñez ")` returns `jose nunez`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** with `urllib.parse` and `unicodedata`; no regex-only parsing of the whole URL.
- [ ] **Step 4: Run, expect PASS.**

### Task 4: Merge and de-duplication service

**Files:**
- Create: `app/merge.py`, `tests/test_merge.py`

**Interfaces:**
- Consumes: models (Task 2), normalizers (Task 3).
- Produces: dataclass `NormalizedRecord(tool: str, kind: Literal["person","company","job"], source_key: str, collected_at: datetime | None, raw: dict, fields: dict)`; `upsert_records(db: Session, records: list[NormalizedRecord], dry_run: bool = False) -> UpsertResult` where `UpsertResult(created: int, updated: int, skipped: int, errors: list[str])`. Person `fields` keys: `full_name, headline, title, location, linkedin_url, email, phone, company_name, company_url, company_website, lead_type`. Company `fields` keys: `name, linkedin_url, website, industry, size`. Job `fields` keys: `title, job_url, location, posted, company_name, company_url`.

- [ ] **Step 1: Write tests:** `test_same_person_two_tools_one_row_two_sources`; `test_rerun_is_idempotent` (second run gives `created == 0`); `test_blank_never_overwrites_value`; `test_newer_nonempty_wins`; `test_duplicate_within_payload_collapses`; `test_no_url_falls_back_to_name_company_low_confidence_and_not_merged_across_tools`; `test_dry_run_writes_nothing_but_reports_counts`; `test_company_matched_by_url_then_domain_then_name`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** `upsert_records`. Match order for persons: normalized profile URL; otherwise name+company within the same tool only, `match_confidence="low"`. Run each call in one transaction; on `dry_run` roll back and return the counts. `lead_type` stays at its first non-`unknown` value unless a later record has a different non-`unknown` value (then keep the existing and add the source; do not flip silently).
- [ ] **Step 4: Run, expect PASS.**

### Task 5: Tool mappers and fixtures

**Files:**
- Create: `app/mappers/__init__.py`, `app/mappers/{talentpulse,hiringbadge,commentminer,jobharvest}.py`, `tests/fixtures/{talentpulse,hiringbadge,commentminer,jobharvest}.json`, `tests/test_mappers.py`

**Interfaces:**
- Consumes: `NormalizedRecord` (Task 4).
- Produces: in each mapper module, `map_payload(payload: object) -> list[NormalizedRecord]` that never raises on a bad row (a bad row is skipped and counted via a returned `errors` attribute: return type is `MapResult(records: list[NormalizedRecord], errors: list[str])`). `app.mappers.MAPPERS: dict[str, Callable]` keyed by the four tool names.
- Field sources (from `reference/`): TalentPulse `leads` (`linkedin_url, full_name, current_title, company_name, location, email, phone, headline, first_found_at`) -> person, `candidate`; Hiring Badge Finder (`profile_url, name, headline, title, company, location, status, open_roles, first_found, last_seen`) -> person `employer_contact`; CommentMiner combined comments (`author_name, author_profile_url, author_headline, email, phone, company_website, company_linkedin_url`) -> person `unknown`; JobHarvest results (`job_id, title, company_name, company_url, location, posted, contacts, job_industry, job_size`) -> company plus job, plus a person only when `contacts` has a profile link.

- [ ] **Step 1: Build fixtures** (small, 3 to 5 rows each, including one row with missing fields and one with Unicode) by reading the real column names in `reference/`. Do not copy real personal data; use made-up values.
- [ ] **Step 2: Write tests** asserting each fixture maps to the exact expected record counts, lead types, normalized fields, and that a malformed row (wrong type, missing key) is skipped with an error message while good rows still map.
- [ ] **Step 3: Run, expect FAIL.**
- [ ] **Step 4: Implement** the four mappers; where a field name in the live JSON might differ, accept the known alternatives found in the reference code and note them in a comment.
- [ ] **Step 5: Run, expect PASS.**

### Task 6: Sync service, dry run, locking and cron endpoint

**Files:**
- Create: `app/sync.py`, `app/routers/sync.py`, `tests/test_sync.py`

**Interfaces:**
- Consumes: mappers (Task 5), `upsert_records` (Task 4), settings (Task 1).
- Produces: `fetch_tool_payload(tool: str, client: httpx.Client) -> object` (GET endpoints: talentpulse `/api/leads`, hiringbadge `/api/leads`, commentminer `/api/posts/combined`, jobharvest `/results`); `run_sync(db: Session, tool: str, dry_run: bool, client: httpx.Client) -> SyncRun`; `POST /api/sync/{tool}?dry_run=true|false`, `POST /api/sync/all`, `GET /api/sync/runs`, `POST /api/sync/cron` (requires header `X-Sync-Token` equal to `SYNC_TOKEN`; disabled with 404 when `SYNC_TOKEN` is empty).

- [ ] **Step 1: Write tests** with `httpx.MockTransport`: `test_sync_creates_leads_and_records_run`; `test_non_json_html_response_marks_run_failed_others_continue`; `test_timeout_and_500_mark_only_that_tool_failed`; `test_second_sync_of_same_tool_rejected_while_running` (409); `test_dry_run_default_on_first_sync_of_a_tool` (the first sync for a tool with no prior successful run behaves as dry run unless `dry_run=false` is explicit); `test_cron_requires_token` (401 wrong, 404 when unset).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** Use a per-tool lock row or an in-process lock plus a `SyncRun` row with status `running`; on startup mark stale `running` rows as `failed`. Timeout 90 s per request (tools on free hosting take about a minute to wake), one retry on timeout or 502/503. Never send credentials.
- [ ] **Step 4: Run, expect PASS.**

### Task 7: CSV/Excel import fallback

**Files:**
- Create: `app/importer.py`, `tests/test_importer.py`, `tests/fixtures/export_*.csv`

**Interfaces:**
- Produces: `import_export_file(db: Session, tool: str, filename: str, content: bytes, dry_run: bool) -> UpsertResult`; `POST /api/sync/{tool}/import` (multipart upload). Reads the export column headers listed in the reference code (e.g. Hiring Badge Finder: `Person Name, Person LinkedIn URL, Company Name, ...`; JobHarvest: `Job Title, Company Name, Company URL, ...`) and converts rows to the same dict shape the mapper expects, then calls the tool's mapper.

- [ ] **Step 1: Write tests** importing a CSV and an xlsx fixture per tool, expecting the same people/companies as the JSON fixture path, and a file with unknown headers returning a clear 400 error naming the missing columns.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** with `csv` and `openpyxl`; limit upload size to 10 MB.
- [ ] **Step 4: Run, expect PASS.**

### Task 8: Authentication

**Files:**
- Create: `app/auth.py`, `app/routers/auth.py`, `tests/test_auth.py`

**Interfaces:**
- Produces: `hash_password(pw: str) -> str`, `verify_password(pw: str, hashed: str) -> bool` (bcrypt); `seed_admin(db: Session, settings: Settings) -> None` (creates the admin from env only if no users exist); `require_user` FastAPI dependency (401 JSON for `/api/*`, redirect to `/login` for pages); `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`; admin-only `POST /api/users`. Session via Starlette `SessionMiddleware` using `SECRET_KEY`, cookie `httponly`, `samesite=lax`. `/api/health` and `/login` stay public; everything else needs login. Login failures are rate limited (5 per minute per IP, in memory).

- [ ] **Step 1: Write tests:** wrong password 401; correct 200 and cookie; `/api/leads` without login 401; seeded admin created once; non-admin cannot create users (403); 6th failed login in a minute gets 429.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** Refuse to start (clear error) if `SECRET_KEY` is unset or the default placeholder.
- [ ] **Step 4: Run, expect PASS.**

### Task 9: Leads API and export

**Files:**
- Create: `app/routers/leads.py`, `app/exporter.py`, `tests/test_leads.py`, `tests/test_exporter.py`

**Interfaces:**
- Produces: `GET /api/leads?q=&tool=&lead_type=&company=&location=&has_email=&has_phone=&page=&page_size=` returning `{items, total, page, page_size}` (page_size max 200, default 50); `GET /api/leads/{id}` including its sources; `GET /api/leads/export.csv` and `/export.xlsx` using the same filters; `build_csv(rows) -> bytes`, `build_xlsx(rows) -> bytes` in `exporter.py`, both neutralizing cells that start with `=`, `+`, `-`, `@` (prefix with `'`).

- [ ] **Step 1: Write tests:** each filter narrows results; combined filters; pagination totals; unauthenticated 401; export rows equal the filtered rows; a headline `=HYPERLINK("http://x","y")` exports as `'=HYPERLINK(...)`; names with commas, quotes and Unicode round-trip in CSV.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** with SQLAlchemy queries (case-insensitive `ilike` search on name, headline, company).
- [ ] **Step 4: Run, expect PASS.**

### Task 10: Campaigns API

**Files:**
- Create: `app/routers/campaigns.py`, `tests/test_campaigns.py`

**Interfaces:**
- Produces: `POST /api/campaigns` (`name`, optional `description`); `GET /api/campaigns`; `GET /api/campaigns/{id}`; `POST /api/campaigns/{id}/leads` with either `{"person_ids": [...]}` or `{"filter": {...same filter keys as /api/leads...}}` (adds all matching, ignoring ones already in); `DELETE /api/campaigns/{id}/leads/{person_id}`; `GET /api/campaigns/{id}/export.csv|xlsx`.

- [ ] **Step 1: Write tests:** create/list; add by ids; add by filter equals the filter's result set; adding twice does not duplicate; export of a campaign equals its members; deleting a lead removes only the membership, not the person.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** (reuse the filter function from Task 9; do not re-implement it).
- [ ] **Step 4: Run, expect PASS.**

### Task 11: UI pages

**Files:**
- Create: `app/static/{login.html,leads.html,campaigns.html,sync.html,app.js,app.css}`; Modify: `app/static/index.html` (the copied dashboard gets a top nav; its health display is unchanged); Test: `tests/test_ui_routes.py`

**Interfaces:**
- Consumes: all `/api/*` routes above.
- Produces: pages at `/` (Tools), `/leads`, `/campaigns`, `/sync`, `/login`. Leads page: search box, filters (tool, lead type, has email, has phone), paginated table, "Add to campaign" and export buttons. Sync page: per-tool last run, counts, errors, "Dry run", "Sync now" and file-import controls. Keep the existing dashboard's look (read the current `index.html` styles first and reuse them).

- [ ] **Step 1: Write route tests:** each page returns 200 when logged in and redirects to `/login` when not; `/` still contains the four tool names.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** plain HTML and vanilla JS (no build step, matching the existing project). Escape all text inserted into the DOM (use `textContent`, not `innerHTML`, for lead data).
- [ ] **Step 4: Run, expect PASS.** Then open each page locally in a browser with seeded fixture data and check the filters, campaign add and export by eye; report what was actually looked at.

### Task 12: Deploy config, docs and full verification

**Files:**
- Modify (in the copy only): `render.yaml`, `run.bat`; Create: `README.md`, `tests/test_end_to_end.py`

**Interfaces:**
- Produces: `render.yaml` with `buildCommand: pip install -r requirements.txt && alembic upgrade head`, `startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT`, env var names only (no values); `run.bat` that creates a venv, installs requirements, runs migrations and starts on port 3000; README with setup, env vars, how to run tests, how sync and import work, known risks.

- [ ] **Step 1: Write** `test_end_to_end`: log in, run dry-run then real sync for all four fixture payloads via mocked transport, confirm one person present in two tools is a single lead with two sources, create a campaign from a filter, export it, confirm row count.
- [ ] **Step 2: Run the whole suite** with `pytest -q`. Expected: all pass, no network used (run once with the network disabled to prove it).
- [ ] **Step 3: Confirm the originals are unchanged:** compare file lists and modified times of `ai-lead-toolkit` and the four tool folders to the snapshot taken in Task 1 Step 1.
- [ ] **Step 4: Write the delivery notes** (short summary, setup/usage, demo flow, cost = $0, what was tested and what was not, including that the live first sync and Postgres/Neon behavior are untested until authorized).

---

## Self-Review

- Spec coverage: pull sync (6), CSV fallback (7), people/company/job/source models (2), de-dup and merge rules (3, 4), lead types (5), campaigns (10), export (9), login (8), Tools tab preserved (1, 11), dry run (4, 6), external cron (6), acceptance criteria 1 to 8 (4, 6, 8, 9, 10, 12), no change to source folders and no push (Global Constraints, 1, 12).
- Not covered by design: live-response field verification (needs authorization; first dry run).
- Types: `NormalizedRecord`, `UpsertResult`, `MapResult`, `SyncRun` names are consistent across Tasks 4 to 7.
