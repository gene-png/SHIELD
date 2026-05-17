# Changelog

All notable changes to SHIELD are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Version
numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The `feat/v0.1-spine` branch contains everything below (33 commits ahead
of `main`). PR #1 brings it onto `main`.

## [Unreleased]

Items deferred to v2 (out of v1 scope):

- Cross-platform value loop (spec §9 / Decision #5) — explicitly
  deferred per the spec until v1 is validated in production.
- Per-user Client association on `User` — the intake surface currently
  shows every active project to every CLIENT-role user instead of
  scoping to one client.

## [1.6] — 2026-05-17

### Added
- **P3 coverage-run XLSX export** (`shield/spine/exporters.coverage_run_to_xlsx`)
  — 4-sheet executive deliverable: Summary (headline + counts + top
  blind spots), Coverage (every technique, sorted by tactic), Gaps
  (uncovered + partial subset for funding), Methodology (capability
  list version, source artifact, "how to read this workbook"). New
  route `GET /platform/attack-surface/project/<id>/run/<run_id>/export.xlsx`.

## [1.5] — 2026-05-17

### Added
- **Capability-list XLSX export** (`shield/spine/exporters.capability_list_to_xlsx`)
  — 2-sheet workbook: Overview (provenance for the auditor) + Items
  (capability rows). New route `GET /clients/<client_id>/capability-list/<list_id>/export.xlsx`.
- `CapabilityList.created_by` relationship for the exporter's provenance
  row.

## [1.4] — 2026-05-17

### Added
- **Audit log viewer** at `/audit/` (admin + reviewer only). Filters on
  action (ILIKE), client (dropdown), since (1h / 24h / 7d / 30d / all).
  Paginated 50/page. Closes the read-side gap on the append-only audit
  table.

## [1.3] — 2026-05-17

### Added
- **Defensive RBAC decorators** (`shield/spine/rbac.py`): `@admin_only`
  and `@admin_or_reviewer`. Applied to every mutating route across P1,
  P2, P3, and the spine views. Three-layer enforcement now in place:
  nav hiding + role gate (`_restrict_client_to_intake`) + defensive
  decorator.
- New tests: `test_reviewer_can_browse_platform_indexes`,
  `test_reviewer_blocked_from_mutating_routes`,
  `test_reviewer_can_promote_ai_artifacts`,
  `test_reviewer_can_view_audit_log`,
  `test_client_blocked_from_audit_log`.

### Changed
- Removed inline role checks made redundant by the decorator on
  `p2.submit`, `p2.downgrade_attribution`, `p2.walkability`,
  `projects.relink_capability_list`, `jobs.index`.
- `docs/security/RBAC_MATRIX.md` rewritten — "Known gaps (v0.6) —
  convention only" replaced with "Enforcement layers" listing all three.

## [1.2.5] — 2026-05-17 — CI artifact-dir fix

### Fixed
- `TestConfig.ARTIFACT_STORAGE_DIR` now defaults to
  `{tempfile.gettempdir()}/shield-test-artifacts` so the bare GitHub
  Actions runner (which has no `/app` directory) can run the
  artifact-writing tests.

## [1.2.4] — 2026-05-17 — ruff clean

### Fixed
- All 201 ruff errors. Line-length bumped 100 → 120; per-file E501
  ignores for the two pure data-table files
  (`scripts/seed_catalog.py`, `shield/p3_attack_surface/attack_data.py`).
  `_enum_values` extracted as a module-level helper. Various B904
  (`raise ... from e`), S112 (try/except/continue → log), B017
  (`pytest.raises(Exception)` → `pytest.raises(ValueError)`).

## [1.2.3] — 2026-05-17

### Changed
- README refreshed for the v1.0 → v1.2 feature surface: Async AI
  section, /jobs and /intake tour entries, relink-capability-list
  action, `make vendor-attack`, dev.cmd pointer, RBAC matrix link.

## [1.2.2] — 2026-05-17

### Changed
- Anthropic SDK `max_retries` bumped 2 → 4 (env-tunable). Explicit
  120 s timeout per call (`ANTHROPIC_TIMEOUT_SECONDS`). Engagements
  fire 5-10 AI calls in quick succession; bumping retries reduces
  rate-limit-window friction.

## [1.2.1] — 2026-05-17

### Added
- **HTMX-polled job-wait page** — replaces full-page `meta-refresh`
  with a tiny status fragment swapped via `hx-trigger=every 2s`.
  Finished jobs return `HX-Redirect` so HTMX navigates the browser to
  the configured `next_url`.
- **Admin `/jobs/` observability listing** — queued / started / failed
  / recently-finished, with worker heartbeats + failed-job tracebacks
  inline.

### Fixed
- `scripts/vendor_assets.py` no longer bails on USWDS failure; HTMX
  still vendors. USWDS version bumped 3.8.1 (404) → 3.10.0.

## [1.2.0] — 2026-05-17 — async AI

### Added
- **Redis + RQ + worker container**. All 6 AI-calling routes
  (`p1.extract`, `p1.overlap`, `p1.chat`, `p2.analyze`,
  `p2.generate_roadmap`, `p3.analyze`) now enqueue jobs instead of
  blocking the gunicorn worker. Browser redirects to `/jobs/<id>/wait`,
  which polls and lands on the workspace when the worker finishes.
- `shield/tasks.py` with one top-level function per AI workflow.
- `shield/spine/jobs.py` with status + wait routes.

### Changed
- The Anthropic system prompt is now `cache_control: ephemeral` — first
  call within a 5-minute window pays full input cost; subsequent calls
  hit cache at ~10× cheaper. Cache stats recorded in artifact lineage.

## [1.1.4] — 2026-05-17

### Added
- `dev.cmd` Windows entrypoint mirroring every Makefile target for
  users without POSIX `make`.

## [1.1.3] — 2026-05-17

### Added
- CSS for the 15+ shield-* component classes introduced in v0.3–v1.1.1
  (banners, blindspot cards, walkability, chat, intake, picker variants).

## [1.1.2] — 2026-05-17

### Added
- **Route-level test coverage** for the v0.3–v1.1.1 work. Seven new
  tests exercising the picker's AI-origin gate on relink, evidence
  upload, submit/lock, attribution downgrade rejection, and the role
  gate. 13 → 20 tests.

## [1.1.1] — 2026-05-17

### Added
- **Cross-project relink-capability-list** via the picker. New spine
  blueprint at `/projects/<id>/relink-capability-list`. The same
  AI-origin acknowledgment gate that fires at project creation now
  also fires at post-creation relink.

## [1.1] — 2026-05-17

### Added
- **MITRE STIX vendoring** — `flask vendor-attack` downloads
  Enterprise ATT&CK and upserts top-level techniques into
  `mitre_techniques`. First run: 222 techniques.
- **DB-backed P3 routes** — `_load_techniques()` prefers the DB
  catalog, falls back to the in-memory starter set for tests.

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
- Identity upsert by sub-OR-email + id_token claim parsing (so seeded
  demo users transition cleanly on first real login and admin roles
  reach `_upsert_user_from_claims`).

### Added — operational + docs
- `make vendor-attack` Makefile target.
- `docs/v1.0-fix-list.md` (the canonical triage from inspection to v1).
- `docs/unified-portal-spec.md` + the PDF moved into the repo.
- Anthropic prompt caching on the system prompt (`cache_control:
  ephemeral`) — meaningful cost savings on repeat AI calls within the
  5-minute window.
- README refreshed for the v1.0 feature surface.

## [0.1] — 2026-05-16 — initial scaffold

Initial commit on `feat/v0.1-spine`. Flask factory + SQLAlchemy +
Keycloak OIDC + three platform blueprints + spine modules + bundled
33-technique MITRE starter set + dev-agent container.
