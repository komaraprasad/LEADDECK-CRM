# LeadDeck Phase 3 (CRM) Implementation Plan

> For agentic workers: use superpowers:executing-plans (native). Steps use checkboxes.

**Goal:** In-house CRM on top of Phase 1/2 data: stages, owners, notes, tasks, timeline, pipeline board, readiness rules, event intake for Phase 4.
**Architecture:** `app/crm.py` (rules, no HTTP), `app/crm_routes.py` (Flask routes), migration 5, new UI pages (Pipeline, Tasks) plus a lead drawer in `app.js`. All state changes write `lead_events`.
**Tech Stack:** existing Flask/sqlite3/vanilla JS; no new dependencies.
**Spec:** `docs/superpowers/specs/2026-11-17-phase3-crm-design.md`

## Global Constraints
- Originals untouched; no commit/push; no new dependencies; no live external call in tests.
- Stages: new, enriched, ready, contacted, replied, interested, meeting, won, lost, not_a_fit (labels in UI). Do-not-contact is the existing `people.do_not_contact` flag and overrides all.
- Automation moves forward only, never past `ready`. Only exception: DNC demotes `ready` to `enriched` (safety).
- Contacted-or-later (`contacted, replied, interested, meeting, won`) is refused while do-not-contact.
- `not_a_fit` and `lost` require a reason.
- Every change writes a `lead_events` row (actor = user email or `system`).
- Event intake `POST /api/crm/events` returns 404 when `CRM_EVENT_TOKEN` is empty, 401 on a wrong token (constant-time compare), is CSRF-exempt.
- Erase removes crm_leads, notes, tasks, lead_events for the person.
- UI: text via textContent only; no inline HTML from data.

## Review Focus
- Person erased with notes/tasks/events: everything gone.
- DNC set on a Ready lead: demoted, never moved to Contacted.
- Re-running backfill/refresh: no duplicate rows or events.
- Bounce event for an email we do not know: accepted, logged, no crash.
- Task due today/overdue ordering and timezone (UTC stored, shown as-is).
- Board with 0 leads and with more than the per-column limit.

### Task 1: Schema + rules (`app/schema.py`, `app/crm.py`, `tests/test_crm.py`)
Migration 5 tables: `crm_leads(person_id unique, stage, owner_email, next_action_at, stage_changed_at, lost_reason)`, `lead_events`, `notes`, `tasks`.
Produces: `STAGES`, `ensure_lead`, `backfill`, `set_stage`, `refresh_readiness`, `add_note`, `create_task`, `update_task`, `list_tasks`, `timeline`, `board`, `ingest_event`, `assign_owner`.

### Task 2: Wire into existing flows
`leads.add_to_campaign` -> ensure+refresh; `enrich.run_enrichment` -> refresh per person; `enrich.set_do_not_contact` -> refresh; `enrich.erase_person` -> delete CRM rows; startup backfill in `create_app`.

### Task 3: Routes (`app/crm_routes.py`, `app/web.py`, `app/config.py`)
`GET /api/crm/board`, `GET /api/crm/leads/<pid>` (lead + crm + contacts + notes + tasks + timeline), `POST /api/crm/leads/<pid>/stage`, `.../owner`, `.../notes`, `POST /api/crm/tasks`, `PATCH /api/crm/tasks/<id>`, `GET /api/crm/tasks`, `GET /api/crm/users`, `POST /api/crm/events`.

### Task 4: UI (`app.js`, `app.css`, `pipeline.html`, `tasks.html`)
Pipeline board with filters + lead drawer; Tasks page; sidebar links; toasts; empty states.

### Task 5: Docs + verify
README Phase 3 section; full suite; deliver; report what was and was not tested.
