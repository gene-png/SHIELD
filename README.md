# SHIELD

**S**ecurity **H**ub for **I**ntegrated **E**valuation, **L**icense oversight, and **D**efensive coverage — one product made of three independent assessment platforms over one shared spine:

- **Platform 1 — Tech Debt.** Find overlapping, redundant, shadow-IT tooling. Redirect waste to priorities.
- **Platform 2 — Zero Trust.** Assess compliance posture against CISA ZTMM 2.0, DoD ZT, or NIST CSF 2.0. Build the roadmap.
- **Platform 3 — Attack Surface.** Map your capabilities to MITRE ATT&CK. Identify the techniques you cannot detect, prevent, or respond to.

Built against the unified-portal spec — designed for GCC High deployability (self-hosted assets, Entra-ID-shaped OIDC), but **fully usable on any commercial Docker host** without GCC High.

License: **MIT**. PRs welcome.

---

## Table of contents

1. [Prerequisites](#prerequisites)
2. [Quick start (3 commands)](#quick-start-3-commands)
3. [Logging in](#logging-in)
4. [Tour of the portal](#tour-of-the-portal)
5. [Using the headless dev agent](#using-the-headless-dev-agent)
6. [Daily workflows](#daily-workflows)
7. [Architecture pointers](#architecture-pointers)
8. [Troubleshooting](#troubleshooting)
9. [Security & compliance posture](#security--compliance-posture)

---

## Prerequisites

| Tool             | Version | Notes                                                                   |
|------------------|---------|-------------------------------------------------------------------------|
| Docker Desktop   | ≥ 24    | Linux / macOS / Windows. WSL2 backend recommended on Windows.           |
| Git              | any     |                                                                         |
| GNU Make         | any     | macOS/Linux bundled; on Windows install via Git Bash / Chocolatey / WSL. |
| VS Code (optional) | ≥ 1.85 | With the Dev Containers extension for the best experience.            |
| Anthropic API key | —      | Get one at [https://console.anthropic.com](https://console.anthropic.com). |

Required free TCP ports on the host: **8000** (app), **8080** (Keycloak), **5432** (Postgres — only published in dev).

---

## Quick start (3 commands)

```bash
git clone https://github.com/gene-png/SHIELD.git
cd SHIELD
cp .env.example .env       # then open .env and paste your ANTHROPIC_API_KEY
make demo                  # builds, migrates, seeds, prints the URL
```

Open `http://localhost:8000`. You'll be redirected to Keycloak to log in.

**Windows note:** use Git Bash, WSL, or PowerShell with `make` available. **No `make`?** Run `dev.cmd <target>` from the repo root — it mirrors every Makefile target (e.g. `dev.cmd demo`, `dev.cmd test`, `dev.cmd vendor-attack`).

**OneDrive note:** clone SHIELD to a path **outside OneDrive** (e.g. `C:\Users\you\source\SHIELD`). OneDrive sync + Docker bind mounts cause intermittent file-lock issues on Windows.

---

## Logging in

The demo realm ships with three users. All have password `demo`.

| Username        | Role     | What they can do                                                                 |
|-----------------|----------|----------------------------------------------------------------------------------|
| `admin@demo`    | admin    | Everything: create clients/projects, upload, run AI steps, promote AI artifacts. |
| `client@demo`   | client   | Their own client's intake surface; submits files and answers.                    |
| `reviewer@demo` | reviewer | Read-only across; the audit-walking persona for the current-state artifact.      |

Keycloak admin console is at `http://localhost:8080`, login `admin / admin`.

---

## Tour of the portal

After login, you land on the **home page** with cards for the three platforms.

- **Clients** (`/clients`) — top-tier listing; every project hangs off a client.
- **Tech Debt** (`/platform/tech-debt`) — Platform 1: raw intake → AI extraction → admin-confirmed extraction → AI overlap → admin-final list. The workspace shows three visibly-separated lanes (human / AI / human-AI-informed). The integrity badges are the visible surface of the [integrity model](docs/architecture/INTEGRITY_MODEL.md).
- **Zero Trust** (`/platform/zero-trust`) — Platform 2: framework-scoped questionnaire + posture analysis. One framework per engagement.
- **Attack Surface** (`/platform/attack-surface`) — Platform 3: ATT&CK coverage analysis over the linked capability list. Executive output first, technical matrix as substrate.
- **Repository** (`/repository`) — read-only browser across everything you're authorized to see. Filter by origin. No uploads happen here.
- **Jobs** (`/jobs`) — admin/reviewer-only async job listing: queued, started, failed (with captured traceback), recently finished. Useful when an AI call appears stuck or a worker is misbehaving.
- **Submit documents** (`/intake`) — the stripped client-intake surface (spec §6.6). CLIENT-role users see ONLY this view — no repository browsing, no picker, no AI-lane visibility.

Workspace pages support a **Relink capability list →** action (admin/reviewer) so a project can swap to a different `CapabilityListVersion` after creation. The picker's AI-origin acknowledgment gate fires there too.

The seed includes a demo client "Acme Co" with a **75-product capability list** specifically curated for breadth and deliberate overlaps (two SIEMs, two EDRs, Slack + Teams, etc.), so Platform 1's overlap analysis produces real findings on first run, not a hand-tuned softball. Run `make vendor-attack` once after first boot to vendor the **full MITRE ATT&CK Enterprise catalog** (~222 top-level techniques) into the `mitre_techniques` table — Platform 3 then operates against the real catalog rather than the 33-technique starter set.

## Async AI

AI calls are **asynchronous**: the route enqueues a job onto a Redis queue (`shield-ai`), redirects the browser to `/jobs/<id>/wait`, and the `worker` container picks the job up and writes the resulting AI artifact when the Anthropic call returns. The wait page polls a small HTMX status fragment every 2 s; when the job's done the server returns an `HX-Redirect` to take the browser back to the workspace. This is why the stack has a `redis` and a `worker` service — `make up` and `make demo` bring them up automatically.

Failures (rate limits, network errors, model errors) surface in the `/jobs` admin listing with the captured traceback. The Anthropic SDK does its own retry/backoff for transient 429/5xx with `ANTHROPIC_MAX_RETRIES=4`.

---

## Using the headless dev agent

**What it is.** A Claude Code CLI running headless inside a sandboxed Docker container. It edits the SHIELD repo in place via a bind-mount and exits when done. It uses `--dangerously-skip-permissions` — which is **safe in this configuration** because the container is the blast radius (read-only root, dropped Linux caps, no host mounts beyond the repo, no published ports).

**One-time setup.**
1. `ANTHROPIC_API_KEY=...` is set in your `.env`.
2. `docker compose -f compose/docker-compose.yml --profile agent build dev-agent` (or `make build`).

**Run a single task.**
```bash
make agent ARGS="add a footer link to docs/security/THREAT_MODEL.md from the base layout"
```
Equivalent:
```bash
docker compose -f compose/docker-compose.yml --profile agent run --rm dev-agent "add a footer link to docs/security/THREAT_MODEL.md from the base layout"
```

The agent will edit files inside the repo (visible to you as normal `git diff`), run any tests/commands it needs in-container, and exit. A transcript of every run is written to `reports/agent-runs/<timestamp>.log` so you can audit what it did.

**Interactive shell inside the sandbox.**
```bash
make agent-shell
```

**Stop / clean up.** Each run uses `--rm` so the container is removed automatically. No state persists between runs except inside the repo mount itself.

**Limits and guardrails.**
- Agent default model: `claude-sonnet-4-6` (override via `ANTHROPIC_MODEL_AGENT` in `.env`).
- Max output tokens per call: `ANTHROPIC_MAX_OUTPUT_TOKENS` (default 4096).
- The container has **no published ports**, so it cannot be reached from outside your host.
- The container has **no Docker socket**, so it cannot spawn sibling containers.

---

## Daily workflows

| Goal                            | Command                                                    |
|---------------------------------|------------------------------------------------------------|
| Start everything                | `make up`                                                  |
| Bring everything up + seed      | `make demo`                                                |
| Stop containers (keep data)     | `make down`                                                |
| **Destroy** all data + volumes  | `make nuke`                                                |
| Tail app logs                   | `make logs`                                                |
| Run DB migrations               | `make migrate`                                             |
| Seed demo data                  | `make seed`                                                |
| Reset DB + reseed               | `make reset`                                               |
| Run tests                       | `make test`                                                |
| Lint                            | `make lint`                                                |
| OWASP ZAP baseline scan         | `make security-scan` → report at `reports/zap/baseline.html` |
| Vendor USWDS + HTMX assets      | `make vendor-assets`                                       |
| Vendor full MITRE catalog       | `make vendor-attack`                                       |
| Bash inside the app container   | `make shell`                                               |
| Run the dev agent               | `make agent ARGS="your prompt"`                            |

Windows users without `make`: run the same targets via `dev.cmd <target>` from the repo root.

---

## Architecture pointers

- **The integrity model** — [docs/architecture/INTEGRITY_MODEL.md](docs/architecture/INTEGRITY_MODEL.md). Read this before changing anything in `shield/spine/`.
- **Threat model** — [docs/security/THREAT_MODEL.md](docs/security/THREAT_MODEL.md). Includes the dev-agent sandbox boundary.
- **RBAC matrix** — [docs/security/RBAC_MATRIX.md](docs/security/RBAC_MATRIX.md). Authoritative who-can-do-what; if it disagrees with the code, the code wins.
- **v1.0 fix list** — [docs/v1.0-fix-list.md](docs/v1.0-fix-list.md). The original triage list; check items show the v1.x milestones.
- **Unified portal spec** — [docs/unified-portal-spec.md](docs/unified-portal-spec.md). The design contract this repo implements.
- **Attributions** — [docs/ATTRIBUTIONS.md](docs/ATTRIBUTIONS.md).

Code pointers:
- `shield/spine/` — the integrity primitives (identity, repository, picker, audit, intake, projects, jobs)
- `shield/tasks.py` — all AI work runs here, in the RQ worker
- `shield/p1_techdebt/`, `shield/p2_zerotrust/`, `shield/p3_attack_surface/` — the three platforms
- `shield/templates/_components/` — the build-once attach / picker / origin-badge components

Layout:

```
shield/
  spine/         identity, audit, repository writer, capability list, picker
  p1_techdebt/   Platform 1 Blueprint + routes
  p2_zerotrust/  Platform 2 Blueprint + routes + frameworks
  p3_attack_surface/   Platform 3 Blueprint + routes + ATT&CK data
  ai/            single Anthropic client + per-platform prompts + fixtures
  templates/     Jinja templates (shared layout + components + per-platform)
  static/        USWDS + HTMX + SHIELD CSS (all self-hosted)
compose/         docker-compose files
docker/          Dockerfiles for app + dev-agent
keycloak/import/ Pre-imported realm with three demo users
migrations/      Alembic
scripts/         seed, vendor_assets, dev_agent_entry
tests/           integrity-model tests + smoke tests
```

---

## Troubleshooting

**Port already in use (8000 / 8080 / 5432).** Stop the conflicting process, or edit the `ports:` block in `compose/docker-compose.yml`.

**`make demo` hangs at "Waiting for db + keycloak healthchecks."** Keycloak can take 30–60s on first start while it imports the realm. Check `docker compose logs keycloak`. If it's looping on errors, `make nuke` and try again.

**Login redirects in a loop.** Your `.env`'s `APP_BASE_URL` doesn't match what your browser is hitting. Set it to `http://localhost:8000`.

**`make agent` fails with `ANTHROPIC_API_KEY is not set`.** The key must be present in `.env` AND the container has to be rebuilt after you add it for the first time (`docker compose --profile agent build dev-agent`).

**Windows: "permission denied" on file mount.** Move the clone OUT of OneDrive. OneDrive sync interferes with Docker bind mounts. Recommended path: `C:\Users\<you>\source\SHIELD`.

**Windows: line ending warnings.** `.gitattributes` normalizes everything to LF. If you see `CRLF will be replaced by LF`, that's git doing its job; the commit will be clean.

**USWDS looks "minimal".** The repo ships a tiny USWDS-compatible stylesheet so the app works fully offline. Run `make vendor-assets` to download the real USWDS bundle into `shield/static/uswds/`.

**Anthropic quota / rate limit.** Set `AI_MODE=fixture` in `.env` to switch all AI calls to canned fixtures from `shield/ai/fixtures/`. The app remains fully usable; only the AI lane returns deterministic content.

---

## Security & compliance posture

- **GCC High-deployable by design.** All UI assets self-hosted; OIDC against any compliant IdP (Keycloak locally; swap to Entra ID for staging/prod by changing `KEYCLOAK_URL`). No public CDNs anywhere in code.
- **No third-party telemetry.** SHIELD does not phone home. Anthropic API calls are the only outbound traffic the app makes; the headless agent calls the same API.
- **Integrity model enforced both at the application layer (`shield/spine/repository.py`) and at the DB layer (origin immutability trigger).** Tests in `tests/test_integrity_model.py` are the contract.
- **OWASP ZAP** baseline scan available locally (`make security-scan`) and runs in CI on pull requests.
- **MIT licensed.** See [LICENSE](LICENSE).

---

If something here doesn't match reality, the docs are wrong — open an issue or PR.
