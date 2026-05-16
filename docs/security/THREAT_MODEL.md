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
- Flask app ↔ Anthropic API (egress only).
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

## Known gaps (tracked, not silently accepted)

- No SAST (Bandit/Semgrep) wired in. Decided ZAP-only for v0.1. Add Bandit on the first follow-up PR if you want SAST.
- No DAST coverage of authenticated paths. ZAP baseline is unauthenticated.
- No SCA tool (pip-audit/Trivy) on the dev-agent image yet.
- No formal `RBAC` matrix doc; per-route role checks are inline. To be extracted in a follow-up.
