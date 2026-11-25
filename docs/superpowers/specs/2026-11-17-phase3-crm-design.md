# LeadDeck Phase 3: CRM: Design (draft for approval)

Date: 2026-11-17. Status: **DRAFT, not approved. Nothing is built.**

## 1. Problem and success criteria

The manager's diagram ends in a CRM fed by Campaign and Data, and later by Outreach. Phase 3 is our own CRM inside LeadDeck: the one place where each lead's status, owner, notes, tasks and history live.

Success means:
- Every campaign lead has exactly one current **stage**, an optional owner, and a full history of stage changes.
- A lead becomes "Ready to contact" automatically only when its data is good (valid email, right person, not do-not-contact).
- Users can add notes, create tasks with due dates, and see "my tasks today".
- A pipeline board shows leads by stage with filters.
- Phase 4 can push events (sent, reply, bounce, unsubscribe) in without redesign.

## 2. Goals, non-goals, anti-goals

Goals: stages, owner, notes, tasks, timeline, board, readiness rules, event intake for Phase 4.

Non-goals: sending email or LinkedIn messages (Phase 4), charts and reports (Phase 5), deal values/forecasting/invoices, email inbox sync, a mobile app.

Anti-goals (failure even if it works): a lead contacted while marked do-not-contact; stage changes with no history; automation silently moving leads backwards; a second pile of leads separate from the Phase 1 data; more than about 10 stages (research warns of status sprawl).

## 3. Research that shaped this (2026-11-17)

- Sales guidance separates **lead status** (what the rep is doing: New, Working, Engaged, Meeting) from **opportunity stages** (the later deal cycle) to avoid clutter (SalesHive glossary). We only need the lead side now, so one stage field is enough; opportunity stages are out of scope.
- Typical outbound stage lists: new, enriched, contacted, engaged, qualified, meeting booked, disqualified (Reply.io, Prospeo). Kanban boards are the common view (Instantly).
- Smartlead documents webhooks for email sent, open, click, reply, bounce, unsubscribe and lead-category updates, delivered by HTTPS POST to a URL we provide. This confirms Phase 4 will need a public URL (deployment before Phase 4). Not verified on your account.

## 4. Approaches considered

A. **Build the CRM inside LeadDeck, reusing Phase 1/2 data (recommended).** One database, no sync problems, no new cost; matches the manager's "own product" decision. Cost: we build and maintain basic CRM features.
B. **Adopt an open-source CRM and sync to it.** Rejected for now: two systems to keep consistent, extra hosting, and the manager asked for our own product. Not evaluated in depth; can be revisited.
C. **Spreadsheet.** Rejected: no history, no enforcement of do-not-contact, does not scale.

## 5. Design (approach A)

**Stages (my proposal; names are your call):** New → Enriched → Ready to contact → Contacted → Replied → Interested → Meeting booked → Won / Lost, plus **Not a fit** (with a reason). Do-not-contact stays the existing flag and overrides everything.

**Data model (new migration):**
- `crm_leads`: person_id (unique), stage, owner_email, next_action_at, stage_changed_at, lost_reason.
- `lead_events`: person_id, type (stage_change, note, task_created, task_done, enrichment, email_sent, reply, bounce, unsubscribe, …), detail (JSON text), actor, created_at. This is the timeline and the intake point for Phase 4 events.
- `notes`: person_id, body, author, created_at.
- `tasks`: person_id, title, due_at, owner_email, done_at, created_by.

**Rules:**
- Adding a person to a campaign creates their `crm_leads` row (stage New). Existing campaign leads are backfilled once.
- Auto stage moves, forward only and never past Contacted: enrichment found data → Enriched; Enriched + valid email + identity check passed + not do-not-contact → Ready to contact.
- Later stages (Contacted onward) are set by Phase 4 events or manually. Automation never moves a lead backwards. Manual moves are always allowed except to Contacted-or-later while do-not-contact.
- A bounce or unsubscribe event sets the contact's email to invalid or the person to do-not-contact (Phase 4 wiring, designed for here).
- Every change writes a `lead_events` row with who or what did it.

**Screens:** Pipeline board (columns by stage, filters by campaign/owner/company); lead detail drawer (contact points, stage selector, notes, tasks, timeline); "My tasks" list (due today, overdue); bulk move of selected leads.

**API:** `GET /api/crm/board`, `POST /api/crm/leads/<pid>/stage`, `POST /api/crm/leads/<pid>/notes`, `POST/PATCH /api/crm/tasks`, `GET /api/crm/tasks?mine=1`, `GET /api/crm/leads/<pid>/timeline`, `POST /api/crm/events` (token-protected intake, used by Phase 4).

## 6. Cost and risks

Cost: 0 new software. Risks: stage definitions the manager wants different (cheap to change early); several users editing the same lead (last write wins, history keeps both); timeline growth on the free 0.5 GB database (events are small text rows); LinkedIn/privacy duties for stored notes (erase must remove notes, tasks, events: included).

## 7. Accuracy validation

Tests for: backfill idempotent; readiness rule on each failing condition; no backwards auto moves; do-not-contact blocks Contacted; erase removes all CRM rows; event intake rejects a bad token; history written for every change.

## 8. Open items needing your answer

1. Are the stage names right for your manager's process?
2. Owners: should leads be assigned to specific users (needs more than the single admin login we have now)?
3. Any stage or field your manager already expects in "his" CRM?

## 9. Left to the builder

Exact board layout, drawer wording, colour coding, pagination sizes, and task reminder wording.
