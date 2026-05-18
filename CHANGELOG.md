# Changelog

All notable changes to SHIELD are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Version
numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The `feat/v0.1-spine` branch holds everything below. PR #1 brings it onto `main`.

## [Unreleased]

Items deferred to v2 (out of v1 scope):

- Cross-platform value loop (spec §9 / Decision #5) — explicitly
  deferred per the spec until v1 is validated in production.
- Workspace rebuild on P1/P2 in the style of P3's executive run-detail
  (the analog is a per-project "review output" page, not a workspace
  redesign).
- Email-delivered invites — v1.8 ships in-app only; the inviter sees
  a copy-pasteable invitation link.
- Deliverable revision UI — `superseded_at` / `superseded_by` ship in
  the schema in v1.8 but the listing UI for superseded versions is
  a v2 follow-up.

## [1.8.2] — 2026-05-17 — round-4 sub-PR C: "Start a new project" entry point

Final of three round-4 sub-PRs. Adds the third use of the shared
`project_create_form` partial and finishes the round-4 doc's §3.

### Added — client-detail entry point

- `/clients/<id>/projects/new` — GET renders the shared partial
  with `show_existing_radio=False`; POST creates the Project and
  redirects to its workspace. Audited as `project.created` with
  `created_from='client_detail'`. Admin-only.
- `/clients/<id>` (client detail) page now shows a primary
  **"+ Start a new project for this client"** button for admins,
  alongside a secondary **View intake** link. Reviewers don't see
  the button.

### Round-4 complete

Three sub-PRs stacked on `feat/v1.8-portal`:
  `a146a66` (4A — adopt becomes create-or-pick)
  `05f4f8e` (4B — fulfill uses the shared partial + ZT framework)
  this commit (4C — client-detail "Start a new project")

The 11-step adopt flow the doc identified is now a 1-POST flow. The
same shared partial backs all three project-creation entry points,
so changing copy or adding a field happens in one file. Zero Trust
projects pick a framework at creation; Tech Debt and Attack Surface
go straight to a named stage='intake' project. The integrity model
(origin immutable, capability list linked later via the existing
relink flow, no AI-reuse ack until the picker selects an AI-origin
list) is unchanged.

### Tests

- 184 → 190 passing. 6 new in `tests/test_v18_round4_adopt_create.py`
  for the client-detail entry point: GET renders form, POST creates,
  Zero Trust without framework rejects atomically, admin-only RBAC,
  client-detail button visible to admin / hidden from reviewer.

## [1.8.2-rc2] — 2026-05-17 — round-4 sub-PR B: fulfill uses the shared partial

Second of three round-4 sub-PRs. Refactors `/clients/<id>/requests/
<id>/fulfill` to render the shared `project_create_form` partial
with `lock_service=True` (service is fixed to the request's service)
and `show_existing_radio=False` (this entry point creates a NEW
project to satisfy the request).

### Added — Zero Trust framework support on fulfill

- Fulfilling a `zero_trust` ServiceRequest now requires picking a
  framework (CISA ZTMM 2.0 / DoD ZT / NIST CSF 2.0). The framework
  dropdown is conditionally shown by the partial's inline JS;
  server-side validation rejects atomically when missing.

### Changed — fulfill route shares the project-creation logic

- POST now goes through `_create_project_from_form` (added in PR 4A),
  which centralizes the service/name/framework validation. The
  fulfill route adds the request-specific behavior on top:
  `sr.fulfilled_project_id` linkage + notification fire.
- Emits TWO audit rows now (matches the adopt-create-new path):
  `project.created` (with `created_from='fulfill_flow'` +
  `source_request_id`) and `client.service_request_fulfilled`.
- Dropped the inline `client_display_name` field from the form;
  admins set the optional client-facing label later from the
  project workspace.

### Tests

- 182 → 184 passing. 2 new tests in
  `tests/test_v18_round3_admin_actions.py` for the Zero Trust
  framework requirement (without-framework rejects atomically,
  with-framework succeeds). 5 existing fulfill tests updated for
  the new form field name (`name` → `new_project_name`) + the
  paired audit row.

## [1.8.2-rc1] — 2026-05-17 — round-4 sub-PR A: adopt becomes create-or-pick

First of three round-4 sub-PRs. Replaces the existing-project-only
adopt picker with a create-or-pick form so admins can create the
receiving project inline from a brand-new client's repository — the
common case the round-2/3 work didn't address.

### Added — shared `_components/project_create_form.html`

One Jinja partial parameterized by the caller. Used in this PR for
adopt; in 4B for service-request fulfillment; in 4C for the
client-detail "Start a new project" button. The partial:

- Shows the "An existing project" radio when
  `show_existing_radio=True` and the client has any non-archived,
  non-repository projects; defaults to the most recently created.
- Shows "A new project" radio always; defaults to it when no
  existing projects.
- Service select, locked (hidden) when `lock_service=True`.
- Project-name input pre-populated with
  `{client.legal_name or name} — {service display} {year}`.
- Framework dropdown shown/hidden via inline JS when service ==
  zero_trust.
- Per round-4 chat answer: no notes field, no description column,
  no Capability-List or AI-reuse acknowledgment at create time
  (those defer to the relink flow).

### Changed — `/clients/<id>/adopt-artifact/<artifact_id>`

- Now accepts `GET` (renders the picker) + `POST` (handles both
  `target=existing` and `target=new`).
- `target=new` path creates the Project, links the artifact, and
  emits **two** audit rows in the same transaction
  (`project.created` with `created_from='adopt_flow'` +
  `source_artifact_id`, and `artifact.adopted_into_project` with
  `prior_stage` + `via='adopt_create'`). Round-4 §6.
- `target=existing` path still works for v1 callers; emits one
  audit row with `via='adopt_existing'`.
- Zero Trust new-project path requires a framework; rejects with
  flash + redirect-to-form otherwise. No half-state on validation
  failure.
- Per round-4 chat answer: stage stays `client_repository` on
  adopt; client is **not** notified (admin housekeeping).
- Post-submit redirect lands on the new project's workspace.

### Tests

- 167 → 182 passing. 15 new in
  `tests/test_v18_round4_adopt_create.py`:
  GET renders form, existing projects show when present, empty-state
  hint when none, target=new creates project + adopts + audits,
  paired audit rows, ZT-without-framework fails atomically (no
  half-state), ZT-with-framework succeeds, empty/bogus name+service
  reject, target=existing still works with one audit row,
  cross-client existing target redirects, cross-client artifact
  returns 404, missing-target redirects back, admin-only RBAC,
  `via` audit detail distinguishes paths.
- 3 existing PR 5 tests updated for the new form-field shape
  (`project_id` → `target` + `existing_project_id`).

## [1.8.1] — 2026-05-17 — round-3 PR 3C: admin fulfill / decline + notifications

Final of three round-3 sub-PRs. Closes the loop on the
"client requests, admin acts" flow.

### Added — admin actions on a ServiceRequest

- `/clients/queue` "Waiting on us" bucket now surfaces open
  ServiceRequests inline: service tag, request date, optional
  deadline, notes excerpt, and per-row **Fulfill** / **Decline**
  buttons.
- `GET /clients/<id>/requests/<id>/fulfill` renders a small form
  pre-populated with a suggested project name (`{label} —
  {service} ({yyyy-mm})`) and an optional client-facing label.
  POST creates a Project with `stage='intake'`, links
  `ServiceRequest.fulfilled_project_id`, audits as
  `client.service_request_fulfilled`, writes a `Notification` for
  every accepted client member, redirects to the new project's
  workspace. Rejects `unsure` requests (admin should reply in
  messages first).
- `GET /clients/<id>/requests/<id>/decline` renders a reason form;
  POST sets `declined_at`/`declined_reason`, audits as
  `client.service_request_declined`, notifies client members.
  Reason must be ≥10 chars.

### Tests

- 157 → 167 passing. 10 new in
  `tests/test_v18_round3_admin_actions.py` covering: queue surfaces
  open requests; fulfill creates the project, links the request,
  writes audit + notification, requires a name, rejects `unsure`,
  idempotent on already-fulfilled; decline records reason + state,
  writes audit + notification, requires a 10-char reason; reviewers
  blocked from fulfill (admin-only).

### Round-3 complete

Three sub-PRs stacked on `feat/v1.8-portal`:
  `eed3d97` (PR 3A — schema + leak fix)
  `9db62a6` (PR 3B — state machine + request form)
  this commit (PR 3C — admin fulfill / decline + notifications)

The bug the user reported on the v1.8 walkthrough is closed:
the dashboard now renders cards as legitimate state transitions
(seed Acme projects show as `in_review` / `ready_to_view` because
the migration backfilled fulfilled ServiceRequests for them), and
every portal screen renders the client's typed legal_name rather
than the seed name.

## [1.8.1-rc2] — 2026-05-17 — round-3 PR 3B: dashboard state machine + request-a-service

Second of three round-3 sub-PRs. Replaces the round-2 dashboard's
three-state cards (`awaiting`/`active`/`delivered`) with the round-3
§5 seven-state machine, and adds the `/portal/services/request` form
that creates a `ServiceRequest` row admins can act on.

### Changed — dashboard card resolver

Round-3 §5 state machine, in precedence order:

  `ready_to_view` — at least one non-superseded Deliverable
  `complete`      — Project.stage in (archived, complete)
  `awaiting_docs` — Project.stage in (intake, raw_intake)
  `in_review`     — any other not-yet-delivered Project.stage
  `requested`     — open ServiceRequest, no Project
  `declined`      — declined ServiceRequest, no Project
  `setup`         — service in client.service_interests, no Project, no request
  (no card)       — none of the above

The round-3 rule "project state takes precedence over request state"
is enforced: if a fulfilled Project exists, the open request is shown
only as informational context, not as the card's state.

Cards use `Project.client_display_name` when set, otherwise the
service label (`Tech Debt` / `Zero Trust` / `Attack Surface`). The
admin's internal Project.name never leaks to the client surface.

### Added — `/portal/services/request`

- GET renders the round-3 §4.2 form (one required service radio,
  optional notes textarea, optional deadline date).
- POST writes a `ServiceRequest` row, appends the service to
  `client.service_interests` (skipping `unsure`), writes a
  `client.service_requested` audit row, and creates a
  `Notification` for every active ADMIN.
- The dashboard's empty-state CTA and the new "+ Add another service"
  button both point here.
- Re-request button on a `declined` card POSTs to this endpoint with
  the service prefilled.

### Tests

- 139 → 157 passing. 18 new tests in
  `tests/test_v18_round3_state_machine.py` covering every card state
  (no-card / requested / declined / setup / awaiting_docs / in_review
  / ready_to_view / complete), project-precedence-over-request, the
  `client_display_name` substitution, and the request POST flow:
  audit-row, service-interests append, `unsure` doesn't append,
  admin notification, bogus-service rejection, bad-deadline graceful
  handling, deadline capture.
- 2 existing PR 4 tests updated for the new state copy
  ("In progress"→"In review", "Deliverables ready"→"Ready to view").

## [1.8.1-rc1] — 2026-05-17 — round-3 PR 3A: ServiceRequest schema + leak fix

Round-3 of the UX work, split into three sub-PRs stacked on
`feat/v1.8-portal`. This is sub-PR 3A — schema and leak fix only;
the dashboard state machine + request-a-service flow land in 3B,
admin fulfill/decline in 3C.

### Added — `ServiceRequest` model + `Project.client_display_name`

- `service_requests` table. The "client REQUESTS a service" pattern
  the doc replaces a "Start a new Project" button with. Three
  derivable states: `is_open`, `is_fulfilled`, `is_declined`.
- `projects.client_display_name` — optional friendly label. When set,
  the portal renders this; admin screens always render `name`.
- Migration `0003_v18r3_service_requests` round-trips cleanly. The
  backfill creates one fulfilled ServiceRequest per existing
  non-repository project on every Client — Acme's three demo projects
  become "legitimate" cards in the round-3 state machine (PR 3B)
  instead of mystery cards.

### Fixed — "Acme Co" leak across portal templates

- New `client_label` Jinja filter: returns
  `client.legal_name or client.name`, falling back through
  whitespace-only values.
- Every `{{ client.name }}` in `shield/templates/portal/*.html`
  switched to `{{ client | client_label }}` so a client who typed
  "My Real Org" during /portal/about sees their typed name, not the
  seed-assigned "Acme Co". Legacy `intake/index.html` template also
  patched.
- Flash on the legacy intake upload no longer names the project's
  client.

### Changed — role gate retires /intake/ for CLIENT

- The v1.8 PR 3 role gate kept `/intake/` accessible to CLIENT users
  for backward compatibility. Round-3 §2.3 calls this out as a leak
  path; the gate now redirects CLIENT off `/intake/` to `/portal/`
  regardless of URL. The legacy blueprint stays mounted for
  ADMIN/REVIEWER who may still use it for testing.

### Tests

- 120 → 139 passing. 19 new tests in
  `tests/test_v18_round3_schema_and_leak.py`:
  ServiceRequest open/fulfilled/declined branches, optional metadata,
  Project.client_display_name nullability, `client_label` filter
  precedence (legal_name → name → empty), parametrized portal-page
  legal-name renders (no "Acme Co" leak across 9 portal URLs), legacy
  `/intake/` redirect.
- Updated 2 existing tests that asserted `/intake/` was reachable for
  CLIENT.

## [1.8.0] — 2026-05-17 — client-portal redesign complete (PR 6 of 6)

PR 6 is the wrap-up: documentation, threat-model rows, and a small
audit-viewer convenience. No new behavior on existing routes.

### Added — threat model rows (`docs/security/THREAT_MODEL.md`)

- **Cross-client read by a multi-tenant user.** The `access_denied`
  audit pattern, 404-not-403 failure mode, and the membership /
  assignment scoping that backs it.
- **Phishing / social engineering inside message threads.** Append-only,
  audited, no peer-to-peer DM, `/admin/messages/` makes every
  conversation part of the audit surface.
- **Invitation-token replay or theft.** SHA-256-hashed storage,
  email-match enforcement, 7-day expiry, revocation, audit trail.

### Added — audit viewer quick filters

- `/audit/` page gains a row of one-click chips for the v1.8 event
  families: client intake, artifact adoption, deliverables,
  messages, cross-client denied, client uploads. Each chip
  prefills the existing free-text action filter — no new query
  parameters, no schema change.

### Status

- 120 tests passing across 1.0 → 1.8 (was 58/58 at v1.7 ship).
- Six PR commits on `feat/v1.8-portal`:
  `6889d7f` (PR 1 — schema + migration)
  `3e2aa31` (PR 2 — access control overhaul)
  `99abc9f` (PR 3 — welcome + intake wizard)
  `8520bb8` (PR 4 — dashboard + deliverables + messages + invites)
  `99cb03a` (PR 5 — admin queue + intake-view + adopt + finalize + inbox)
  this commit (PR 6 — docs + threat model + audit quick filters)
- Existing flows unchanged: integrity model + origin immutability
  + audit append-only + AI-egress redaction all carry through. The
  rework is purely additive around the v1.0 → v1.7 spine.

## [1.8.0-rc5] — 2026-05-17 — admin surfaces: queue, intake-view, adopt, finalize, inbox (PR 5 of 6)

### Added — admin landing + workflow

- `/` (home) now redirects ADMIN users to `/clients/queue`.
  CLIENT and REVIEWER redirects unchanged.
- `/clients/queue` — three-bucket action queue:
    - **New leads** (intake_completed_at NULL)
    - **Waiting on us** (intake done, service interests with no
      matching Project, or consult requested)
    - **Active** (at least one non-repository Project)
  Plus a per-client "unread from them" count so conversational work
  surfaces alongside intake work.

- `/clients/<id>/intake` — admin's read-only view of every Client
  metadata field the client submitted via `/portal/welcome` →
  `/portal/about`, plus the documents in the synthetic Client
  Repository project.
- `POST /clients/<id>/adopt-artifact/<artifact_id>` — link a
  repository artifact to a real Project. The artifact stays
  origin=human_input (origin is immutable); only `project_id`
  moves. Cross-client targets are refused. Audited as
  `artifact.adopted_into_project`.

### Added — finalize artifacts as Deliverables

- `POST /projects/<id>/finalize-artifact/<artifact_id>` creates a
  `Deliverable` snapshot the client sees in `/portal/deliverables/`.
  Re-running for the same `(project, artifact)` marks the previous
  Deliverable superseded (`superseded_at` + `superseded_by`).
- Audited as `deliverable.finalized` (+ `deliverable.superseded`
  when applicable).

### Added — admin cross-client messages

- New `shield.spine.admin_views` blueprint mounted at `/admin/*`.
- `/admin/messages/` — inbox of every accessible thread, sorted by
  unread-first then newest-first. Reviewers see only their assigned
  clients' threads (scope_query); admins see everything.
- `/admin/messages/<client_id>/<thread_key>` — admin view of one
  thread + reply form. GET marks read; POST appends with the same
  `message.posted` audit row the portal side writes.

### Changed — clients list + nav

- `/clients/` adds **Services** + **Intake** columns showing each
  client's service_interests + intake state.
- Admin nav adds **Queue** (first) and **Inbox**.

### Tests

- 109 → 120 passing. 11 new tests in `tests/test_v18_admin.py`:
  admin home → queue redirect, queue bucketing, intake-view renders
  client metadata, adopt-artifact moves the project_id + audits +
  rejects cross-client targets, finalize creates a Deliverable +
  supersedes the previous, admin messages inbox lists threads, admin
  thread POST writes the reply.

## [1.8.0-rc4] — 2026-05-17 — client portal: dashboard, messages, deliverables, invites (PR 4 of 6)

### Added — returning-client surfaces

- `/portal/` is now the **real dashboard**, not a placeholder.
  Renders one service card per `client.service_interests` entry,
  each in one of three states: `awaiting` (no Project yet),
  `active` (Project exists, no Deliverable), `delivered`
  (at least one Deliverable). Right-rail shows recent deliverables +
  recent messages with unread counts.
- `/portal/services` — manage service interests after initial intake.
- `/portal/deliverables/` — finalized reports grouped by project.
- `/portal/deliverables/<id>` — single deliverable with summary +
  readable body (uses the v1.7 `readable_body` partial so JSON
  bodies render as structured content, not raw `<pre>` dumps).
- `/portal/messages/` — thread list. One general thread (project_id
  NULL) plus one per non-archived project. Each row shows latest
  message preview + unread count for the viewer.
- `/portal/messages/<thread_key>` — single thread chronologically.
  GET marks every message the viewer hadn't read; POST appends a
  new message and writes a `message.posted` audit row.
- `/portal/settings/` — profile (display name / title / phone) +
  team listing + invite-a-colleague (primary-POC only).
- `/portal/settings/invite` — creates a `ClientInvitation` row with
  a SHA-256-hashed token; the plaintext token only ever exists
  during the response and is shown to the inviter as a
  copy-pasteable URL (per round-2 §10 answer — no SMTP).
- `/portal/settings/invite/<id>/revoke` — primary-POC can revoke
  a pending invite.
- `/portal/invitations/accept/<token>` — invitee accepts. Validates
  expiry + revocation + email-match (the logged-in user's email
  must match the invited email; mismatch returns 403 — fail closed).

### Added — audit events

- `message.posted`
- `client.invited_user`
- `client.user_joined`
- `client.invitation_revoked`

### Tests

- 92 → 109 passing. 17 new tests in
  `tests/test_v18_portal_dashboard.py` covering: dashboard service
  cards per interest, state transitions (awaiting/active/delivered),
  services-page interest change, deliverables list grouping +
  empty state + cross-client 404, messages list/thread/post + read-
  tracking, settings profile update, invite-create writes hashed
  token + returns the URL, member (non-PM) can't invite, accept
  links membership and writes audit, mismatched email is 403,
  expired/revoked tokens are 404.

## [1.8.0-rc3] — 2026-05-17 — client portal: welcome + intake wizard (PR 3 of 6)

### Added — `shield.spine.portal` blueprint

- `/portal/welcome` — service-selection. Three big cards (Tech Debt /
  Zero Trust / Attack Surface) plus an "I'm not sure" option that
  co-exists (per round-2 §8.1 answer) with the service checkboxes
  rather than being mutually exclusive.
- `/portal/about` — org/POC/address/compliance/prompt form with
  per-field HTMX auto-save. Each input wires `hx-post=/portal/about/field`
  on blur; the endpoint accepts only a whitelist of column names so
  it can't be used as a write-anything Client.update.
- `/portal/documents` — drag-and-drop upload to the synthetic
  per-client repository project (Option B for storage paths). Files
  land with `origin=human_input`, `stage='client_repository'`, and
  the existing redaction-on-AI-egress chain stays intact.
- `/portal/confirm` — final step; sets `intake_completed_at` and
  writes a `client.intake_completed` audit row.
- `/portal/` — placeholder dashboard (PR 4 replaces this with the
  real per-service card view + message threads + activity feed).

### Changed — entry points

- `/` (home) redirects CLIENT users to `/portal/welcome` if their
  client's `intake_completed_at` is NULL, otherwise to `/portal/`.
- The role gate `_restrict_client_to_portal` (renamed from
  `_restrict_client_to_intake`) now allows `/portal/*` for CLIENT
  users. `/intake/*` stays accessible for backward compatibility
  but the redirect target for everything else is `/portal/`.
- CLIENT nav rebuilt: Home / My documents / My services / Settings,
  all pointing into `/portal/*`.

### Added — audit event types (writes only; PR 6 wires the viewer filter)

- `client.service_interest_changed` — welcome form save with diff.
- `client.about_saved` — `/portal/about/submit` checkpoint.
- `client.intake_completed` — `/portal/confirm` finalize.
- `file_uploaded_to_repository` — every client-tier upload.
- `project.create_client_repository` — on-demand backfill of the
  synthetic project (rare; the migration creates it for existing
  clients, but a future client created outside the seed path hits
  this code path on first portal visit).

### Tests

- 78 → 92 passing. 14 new tests in `tests/test_v18_portal_wizard.py`:
  home redirects (welcome vs dashboard), welcome form save +
  service-interest audit, service-key whitelist filter,
  about-field per-column save, about-field rejects unknown columns,
  about-submit requires POC email, documents upload lands in the
  synthetic project + writes audit, confirm sets `intake_completed_at`,
  CLIENT users still 302 from non-portal URLs.

## [1.8.0-rc2] — 2026-05-17 — client portal: access control (PR 2 of 6)

### Added — `shield.spine.access`

- `client_ids_for_user(user)` resolves which client_ids a given user
  may read. Sentinel `None` for unrestricted (admin and un-assigned
  reviewer per round-2 §10 answer); list for finite scope (CLIENT's
  accepted memberships, REVIEWER's non-revoked assignments).
- `require_client_access(client_id)` aborts 404 (not 403) and writes
  an `access_denied` audit row on failure — existence of another
  client's resource never leaks through the error code.
- `@require_client_for_param("client_id")` decorator for routes
  whose URL parameter is the client id.
- `scope_query(stmt, model)` adds the client-scope WHERE to a Select.
- `user_clients()` returns the resolved Client rows for nav/dashboard.

### Changed — every cross-client-readable route is now scoped

- `/repository/` and `/repository/artifact/<id>` filter by access;
  synthetic client_repository projects are excluded from the global
  browse (they'll surface on the portal in PR 3).
- `/clients/`, `/clients/<id>`, `/clients/<id>/capability-list/*`
  apply `@require_client_for_param`.
- `/platform/{tech-debt,zero-trust,attack-surface}/` index pages
  scope-filter the project list.
- The shared `_get_project_or_404` in each platform now calls
  `require_client_access(project.client_id)` so every project-scoped
  route inherits the check (workspaces, all POST actions,
  project_summary, finalize, walkability, run_detail).
- `/projects/<id>/relink-capability-list` adds the same check.
- `/audit/` scopes its query and the client filter to the user's
  resolved clients.

### Tests

- 66 → 78 passing. 12 new tests in `tests/test_v18_access.py`
  covering: admin unrestricted, unauthenticated empty, CLIENT
  resolves to accepted memberships only (pending invitations don't
  grant access), REVIEWER with zero assignments preserves
  see-everything, revoked assignments don't count, cross-client
  reads return 404, access_denied audit row is written.

## [1.8.0-rc1] — 2026-05-17 — client portal: schema only (PR 1 of 6)

This release is the first of six PRs implementing the v1.8 client
portal redesign (see `docs/v1.8-portal-spec.md`, to be added). It is
schema-only: no new routes, no template changes, no behavior change
for existing flows.

### Added — data model

- `Client` extended with intake metadata: legal/dba name, website,
  size band, primary POC name/title/email/phone, full address,
  compliance frameworks (JSON list), compliance deadline, prompting
  context, `service_interests` (JSON list of `tech_debt` /
  `zero_trust` / `attack_surface`), `consult_requested` flag,
  `intake_completed_at` timestamp.
- `User` extended with optional `title` and `phone` (Keycloak still
  owns email + sub).
- `Project` gains `is_client_repository` flag. Exactly one synthetic
  "Client Repository" project per client; client-tier uploads land
  there with `stage='client_repository'`.
- `Artifact` gains a denormalized `client_id` column (NOT NULL).
  Writers auto-fill it from `project.client_id`; every per-client
  scoping query reads this directly.
- Six new tables: `client_memberships`, `client_invitations`,
  `messages`, `notifications`, `deliverables`, `reviewer_assignments`.

### Added — migration

- `0002_v18_client_portal` with backfill: existing artifacts get
  `client_id` set from their project; the demo Acme client gets all
  three service interests with `intake_completed_at = NULL` so
  `client@demo` walks the new welcome flow on next login; a synthetic
  "Client Repository" project is created per client; `client@demo`
  becomes a `primary_poc` of Acme. Round-trips cleanly via
  `flask db downgrade 0001_initial && flask db upgrade`.

### Tests

- 58 → 66 passing. 8 new schema-invariant tests in
  `tests/test_v18_models.py` covering Artifact.client_id wiring,
  ClientMembership uniqueness, ReviewerAssignment uniqueness,
  ClientInvitation token-hash uniqueness, and basic instantiation of
  Message / Notification / Deliverable.

## [1.7] — 2026-05-17 — UX pass against the field-review doc

### Added — PII redaction on the AI egress path

- `shield/ai/redact.py`: regex layer (emails, US phones, SSN,
  credit-card runs, IPv4, URLs, US street addresses) + optional
  Presidio NER layer (PERSON / LOCATION / ORG / NRP). Runs at the
  only chokepoint, `AIClient.complete()`. Per-project literals
  (client org name, project name) wired in via
  `tasks._redaction_terms`.
- Redaction counts land in the AI artifact lineage — never raw
  values — so the audit log shows the layer ran.
- Footer disclosure on every authenticated non-client page.
- Configurable via `AI_REDACTION_MODE = off | regex | full` and
  `AI_REDACTION_EXTRA_TERMS`. TestConfig defaults to `regex` so the
  suite never tries to load spaCy.
- Closes the "PII leakage to third-party LLM" gap in
  `docs/security/THREAT_MODEL.md`.

### Added — real Zero Trust catalogs

- `scripts/vendor_zt_catalogs.py` fetches and parses NIST CSF 2.0
  from NIST's official OSCAL JSON release (185 subcategories across
  6 functions) and mirrors CISA ZTMM 2.0 (35 functions × 8 pillars)
  + DoD Zero Trust (44 capabilities × 7 pillars). Output JSON is
  committed for offline-reproducible builds; the framework loader
  reads from those files at import.
- Replaces the previous 6–7-control starter sets.

### Added — readable artifact bodies + Activity page

- New `_components/readable_body.html` partial pretty-prints JSON
  artifact bodies and surfaces recognized shapes (P3 ATT&CK summary,
  P1 chat answer + citations, P1 overlap redundancies/gaps,
  extraction item lists). Replaces opaque `<pre>` dumps on the
  artifact detail page and P1/P2 workspaces.
- `from_json` Jinja filter (returns None on parse failure).

### Added — documentation

- `docs/GLOSSARY.md` with plain-English definitions of every term an
  admin or reviewer meets in the UI: roles, the three platforms,
  source / draft / reviewed-version, picker + approval, the Activity
  page, redaction + lineage + audit log, and the framework catalogs.

### Changed — language pass on user-facing copy

- Uniform relabel: "AI" → "automated analysis" / "automated draft"
  across templates, picker copy, workspaces, wait screen, flash
  messages, and the repository.
- Origin badges: `human_input` → "From your team", `ai_generated` →
  "Automated draft", `human_ai_informed` → "Your reviewed version".
- "Promotion" → "Approval" (with copy clarifying that approval never
  converts a draft into source data).
- "Jobs" → "Activity"; the page is now admin-only — reviewers walk
  finished artifacts, not the worker queue.
- Wait screen rewritten: friendlier headline, 3-step checklist
  (redact → analyze → save for review), friendly status labels,
  admin-only collapsible for the technical detail.
- DB enums, route field names, and the integrity model (spec §7.3)
  are unchanged — only the labels.

### Changed — P3 run detail copy

- "Uncovered" → "not-covered" in the recommendation paragraph
  (parallels XLSX export already shipped in v1.6).
- Removed the cross-platform Tech Debt funding aside — that loop
  belongs in the portal-level narrative, not in a single coverage
  run.

### Fixed — Anthropic streaming for long P3 generations

- Switched from `client.messages.create()` to `messages.stream()`
  for the long-running P3 ATT&CK coverage call (~16K output tokens).
  Non-streaming hung past ~5 minutes; streaming keeps the connection
  actively used. Verified live: P3 finishes in ~2:45 vs >10 min hang.
- `_parse_ai_coverage_response()` walks brace depth to recover the
  last complete finding on truncation; synthesizes executive_summary
  from recovered findings so the UI shows real numbers instead of
  silent zeros.

### Tests

- 50 → 54 passing. 13 new redaction tests, 7 structural tests for ZT
  catalogs, 4 for the `from_json` filter.

## [1.6] — 2026-05-17 — XLSX exports + audit viewer + RBAC

### Added

- **P3 coverage-run XLSX export** — 4-sheet executive deliverable:
  Summary (headline + counts + top blind spots), Coverage (every
  technique, sorted by tactic), Gaps (uncovered + partial), Methodology.
  Route: `GET /platform/attack-surface/project/<id>/run/<run_id>/export.xlsx`.
- **Capability-list XLSX export** — 2-sheet workbook: Overview
  (provenance) + Items. Route:
  `GET /clients/<client_id>/capability-list/<list_id>/export.xlsx`.
- `CapabilityList.created_by` relationship for export provenance.
- **Audit log viewer** at `/audit/` (admin + reviewer only). Filters on
  action (ILIKE), client (dropdown), since (1h / 24h / 7d / 30d / all).
  Paginated 50/page.
- **Defensive RBAC decorators** (`shield/spine/rbac.py`): `@admin_only`
  and `@admin_or_reviewer`. Applied to every mutating route. Three
  enforcement layers in place: nav hiding + role gate + decorator.
- `docs/security/RBAC_MATRIX.md` rewritten — "convention only" replaced
  with the three-layer enforcement list.

## [1.2] — 2026-05-17 — async AI

### Added

- **Redis + RQ + worker container**. All six AI-calling routes
  (`p1.extract`, `p1.overlap`, `p1.chat`, `p2.analyze`,
  `p2.generate_roadmap`, `p3.analyze`) enqueue jobs instead of
  blocking the gunicorn worker. Browser redirects to
  `/jobs/<id>/wait`, which polls and lands on the workspace when the
  worker finishes.
- **HTMX-polled job-wait page** with `hx-trigger=every 2s` and an
  `HX-Redirect` from the status fragment when the job lands.
- **Admin `/jobs/` observability listing** — queued / started /
  failed / recently-finished, with worker heartbeats + failed-job
  tracebacks inline.
- **Cross-project relink-capability-list** via the picker. The
  AI-origin acknowledgment gate that fires at project creation now
  also fires at post-creation relink.
- **MITRE STIX vendoring** — `flask vendor-attack` downloads
  Enterprise ATT&CK and upserts top-level techniques into
  `mitre_techniques`. First run: 222 techniques. P3 routes prefer
  the DB catalog, fall back to the in-memory starter set for tests.
- Anthropic system prompts marked `cache_control: ephemeral` (first
  call pays full cost; subsequent within 5 minutes hit cache at ~10×
  cheaper). Cache stats land in artifact lineage.
- Anthropic SDK `max_retries` bumped 2 → 4 (env-tunable). Per-call
  timeout set explicitly via `ANTHROPIC_TIMEOUT_SECONDS`.
- `dev.cmd` Windows entrypoint mirroring every Makefile target.

### Tests

- 7 new route-level tests for the picker's AI-origin gate on relink,
  evidence upload, submit/lock, attribution downgrade rejection, and
  the role gate. 13 → 20 tests.

### Fixed

- `TestConfig.ARTIFACT_STORAGE_DIR` defaults to
  `{tempfile.gettempdir()}/shield-test-artifacts` so the bare GitHub
  Actions runner can run artifact-writing tests.
- All 201 ruff errors. Line-length bumped 100 → 120 with per-file
  E501 ignores for the two pure data-table files. `_enum_values`
  extracted as a helper. Various B904 / S112 / B017 cleanups.
- `scripts/vendor_assets.py` no longer bails on USWDS failure; HTMX
  still vendors. USWDS bumped 3.8.1 (404) → 3.10.0.
- CSS for the 15+ `shield-*` component classes introduced in
  v0.3–v1.1 (banners, blindspot cards, walkability, chat, intake,
  picker variants).
- README refreshed for the post-v1.0 feature surface.

## [1.0.0-rc1] — 2026-05-17 — spec-complete on the main path

### Added — spine + integrity model

- Origin immutability enforced **three ways**: Python `@validates`,
  SQLAlchemy `before_update` listener, Postgres `BEFORE UPDATE` trigger.
- Audit log append-only by Postgres trigger.
- Picker as integrity chokepoint: `link_capability_list_to_project`
  raises `PermissionError("AI_REUSE_ACK_REQUIRED")` when an AI-origin
  list is selected without the explicit ack.

### Added — Platform 1 (Tech Debt)

- All six spec stages (`raw_intake → ai_extraction →
  extraction_review → overlap_analysis → conversational_interrogation →
  admin_final`) wired end-to-end on the spine.
- Binary file extraction (`shield/services/text_extraction.py`):
  PDF / DOCX / XLSX / text → `body_text` at upload time.
- Promotion UI on artifact detail page (admin/reviewer, reason
  required, never alters origin).

### Added — Platform 2 (Zero Trust)

- Framework picker at project creation (CISA ZTMM / DoD ZT / NIST CSF).
- Three distinct artifacts per spec §8.2 D#4: current-state /
  desired-future-state / transition-roadmap.
- Submit/lock flow per §10 D#3 — immutable once submitted.
- Per-answer attribution downgrade (downgrade-only, rank-enforced).
- Evidence upload inline at each control.
- Reviewer audit-walkability template — framework control → claim →
  evidence → AI assessment → gap → remediation.

### Added — Platform 3 (Attack Surface)

- Project creation + executive-first run_detail page (matrix as
  substrate, not surface).
- Materialized `CoverageRun` + `CoverageFinding` rows per spec §8.3.

### Added — shared spine

- All six page archetypes from spec §6, including client intake
  surface (§6.6).
- Client/admin/reviewer role gate (`_restrict_client_to_intake`) +
  role-aware nav.
- `docs/security/RBAC_MATRIX.md`.

### Fixed — v0.2 P0 stack-boot bugs

- Keycloak healthcheck port 8080 → 9000 (KC25 moved health to the
  management interface).
- Migration enum types: switched `sa.Enum` → `postgresql.ENUM` with
  `create_type=False`.
- Model enums: `values_callable=_enum_values` so lowercase values
  persist correctly to Postgres.
- Makefile uses `--env-file .env` (compose's default discovery looks
  beside the compose file, not at the project root).
- Artifacts named volume permission: Dockerfile pre-creates
  `/app/artifacts` owned by the `shield` user.
- `requirements-dev.txt` installed in the runtime image so `pytest`,
  `ruff`, `detect-secrets`, `pre-commit` are available in the
  container.

### Fixed — auth path during browser walk

- PKCE enabled on the Authlib OIDC client (Keycloak 25 requires it).
- `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true` — discovery returns
  back-channel endpoints based on Host header, so the app talks to
  `keycloak:8080` while the browser uses `localhost:8080`.
- Identity upsert by sub-OR-email + id_token claim parsing.

### Added — operational + docs

- `make vendor-attack` Makefile target.
- `docs/v1.0-fix-list.md` (the canonical triage from inspection to v1).
- `docs/unified-portal-spec.md` + the PDF moved into the repo.

## [0.1] — 2026-05-16 — initial scaffold

Flask factory + SQLAlchemy + Keycloak OIDC + three platform blueprints
+ spine modules + bundled 33-technique MITRE starter set + dev-agent
container.
