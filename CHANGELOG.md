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
