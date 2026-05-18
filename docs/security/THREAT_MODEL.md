# SHIELD — Threat Model (v0.1)

## In scope

- A multi-tenant web portal hosting three platforms for client engagements.
- Authenticated users in three roles: client, admin, reviewer.
- File uploads from clients (potentially untrusted).
- AI calls to Anthropic Claude over HTTPS.
- A sandboxed headless dev agent that runs inside a Docker container.

## Assumed adversaries

1. **Malicious client.** Submits files designed to cause the AI to mis-classify
   or leak data; tries prompt injection in `notes` fields.
2. **Compromised client account.** Tries to read another client's data.
3. **Curious internal user.** Tries to view audit log or repository for
   clients they don't own.
4. **Network attacker.** TLS downgrade, replay, cookie theft.
5. **Supply chain.** Compromised PyPI / npm package.

## Trust boundaries

- Browser ↔ Flask app (TLS in prod; HTTP locally).
- Flask app ↔ Postgres (internal network only; never exposed).
- Flask app ↔ Keycloak (internal network).
- Flask app ↔ Anthropic API (egress only — every user payload is
  redacted by `shield.ai.redact` at this boundary; see below).
- Host filesystem ↔ dev-agent container (NO mount of host paths).

## Mitigations

| Threat                         | Mitigation |
|--------------------------------|------------|
| Prompt injection from clients  | Prompts explicitly tell the model to treat inputs as DATA. JSON-only output. No tool use. Clients cannot reach the AI lane directly. |
| AI output mistaken as source   | `ai_generated` origin + reuse-moment acknowledgment in the picker. DB trigger forbids origin mutation. |
| Cross-tenant data access       | Every query scopes by `client_id`; integration tests cover this. Reviewer role is read-only across, by design. |
| Session theft                  | `SESSION_COOKIE_HTTPONLY` + `SESSION_COOKIE_SECURE` + `SameSite=Lax`. Short session lifetime. |
| CSRF                           | `Flask-WTF` CSRF tokens on every POST form. |
| XSS                            | Jinja autoescape on; strict CSP without unsafe-inline scripts. |
| Clickjacking                   | `X-Frame-Options: DENY`. |
| File upload abuse              | `MAX_CONTENT_LENGTH=64MB`. Files written under a randomized filename. Stored outside the web root. |
| Supply chain                   | Pinned versions in `requirements.txt`. `detect-secrets` pre-commit. ZAP baseline in CI. |
| Headless agent escape          | Container runs read-only root + tmpfs; cap_drop ALL; no host paths; no Docker socket; no published ports. Even with `--dangerously-skip-permissions`, the blast radius is the repo mount. |
| PII leakage to third-party LLM | `shield.ai.redact` runs at the only egress chokepoint (`AIClient.complete`). Regex layer (emails, phones, SSN, CC, IP, URLs, US addresses) is always on; Presidio NER layer (PERSON / LOCATION / ORG / NRP) is on by default and degrades gracefully if spaCy isn't present. Per-project literals (client org name, project name) are masked on every call via `tasks._redaction_terms`. Counts of what was redacted are recorded in the AI artifact lineage so auditors can verify the layer ran — raw values are never persisted in the report. |
| Cross-client read by a multi-tenant user (v1.8) | `shield.spine.access` resolves a per-user `client_ids_for_user` allowlist; every cross-client read route calls `require_client_access` and aborts with 404 (not 403) on mismatch so existence of another client's resource never leaks through the error code. The unauthorized read writes an `access_denied` audit row so ops can spot probing without giving the caller information. Reviewer scope is via the `ReviewerAssignment` table; CLIENT scope is via accepted `ClientMembership` rows (pending invitations don't grant access). |
| Phishing / social engineering inside message threads (v1.8) | Messages are append-only (no edit, no delete) and every post writes a `message.posted` audit row with author role, length, and thread. Threads are confined to client × consulting-firm pairs — no peer-to-peer DM across clients or inside a client's team. Admins see every thread via `/admin/messages/`; the conversation is part of the audit surface, not an out-of-band channel that bypasses it. |
| Invitation-token replay or theft (v1.8) | Plaintext invite tokens are 32-byte URL-safe random; only the SHA-256 hash is stored. `/portal/invitations/accept` validates token presence, non-revocation, non-expiry, AND email-match (the logged-in user's email must equal the invited email, case-insensitively) — a forwarded link used by the wrong account returns 403. Tokens expire 7 days after creation by default. Audited as `client.invited_user` / `client.user_joined` / `client.invitation_revoked`. |

## Known gaps (tracked, not silently accepted)

- ~~No SAST (Bandit/Semgrep) wired in.~~ **Closed in v1.8** — Bandit
  runs in CI (`bandit -r shield scripts -q`). Current state: 0 findings
  in `shield/`. Two `B310 urlopen` warnings in `scripts/` are suppressed
  via `# nosec B310` — those calls are provision-time vendoring of
  pinned URLs (MITRE STIX, USWDS, HTMX), never user input.
- No DAST coverage of authenticated paths. ZAP baseline is unauthenticated.
- ~~No SCA tool (pip-audit/Trivy) on the dev-agent image yet.~~
  **Closed in v1.9** — `pip-audit -r requirements.txt --strict` runs
  in CI. Initial baseline cleared 36 CVEs across Flask, Werkzeug,
  authlib, requests, python-dotenv, and pypdf via version bumps.
  The dev-agent image inherits the same requirements; Trivy on the
  built image is a separate v2 task.
- ~~No formal `RBAC` matrix doc; per-route role checks are inline.~~
  **Closed in v1.3** — see [RBAC_MATRIX.md](RBAC_MATRIX.md). Defensive
  decorators (`@admin_only` / `@admin_or_reviewer`) enforce on every
  mutating route; convention-only enforcement was eliminated.
