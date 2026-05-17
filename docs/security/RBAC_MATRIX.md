# SHIELD — RBAC Matrix

Authoritative list of which role can do what. Cross-reference with the
inline `@login_required` and role checks in the route source: if this
document and the code disagree, the code is the source of truth and
**this document is wrong** — fix the doc, file an issue.

## Roles

| Role        | Purpose                                                                              |
|-------------|--------------------------------------------------------------------------------------|
| `ADMIN`     | The consulting party. Drives every engagement; promotes AI artifacts; downgrades attribution; submits questionnaires. |
| `REVIEWER`  | The audit-walking party. Read-only across every project, every artifact. Used by the auditor persona for the current-state artifact walk. May promote AI artifacts. |
| `CLIENT`    | The customer-side persona. Submits source documents via the intake surface. Has **no** visibility into the AI lane, the picker, the repository browser, or any platform workflow. |

Anonymous (unauthenticated) users can hit `/auth/login`, `/auth/callback`,
and `/healthz`; everything else redirects them to `/auth/login`.

## Access matrix

`✓` = allowed · `–` = denied (server-side; the nav also hides the link)
· `R/O` = read-only

| Route group                                              | ADMIN | REVIEWER | CLIENT  | Enforcement |
|----------------------------------------------------------|:-----:|:--------:|:-------:|------------|
| `/` (home)                                               |   ✓   |    ✓     | → /intake | Redirect in `home()` |
| `/healthz`                                               |   ✓   |    ✓     |    ✓    | Anonymous |
| `/auth/login`, `/auth/callback`, `/auth/logout`         |   ✓   |    ✓     |    ✓    | Anonymous on login routes |
| `/intake/`, `/intake/project/<id>/upload`               |   ✓   |    ✓     |    ✓    | `login_required` |
| `/clients/`, `/clients/<id>`                            |   ✓   |    ✓     |    –    | Role gate (`_restrict_client_to_intake`) |
| `/repository/`, `/repository/artifact/<id>`             |   ✓   |    ✓     |    –    | Role gate |
| `/repository/artifact/<id>/promote`                     |   ✓   |    ✓     |    –    | `spine.repository.promote_artifact` checks role |
| `/platform/tech-debt/*`                                  |   ✓   |   R/O    |    –    | Role gate; reviewer can browse but mutating routes (upload/extract/etc.) still require admin presence in practice |
| `/platform/zero-trust/*`                                 |   ✓   |   R/O    |    –    | Role gate |
| `/platform/zero-trust/<id>/submit`                       |   ✓   |    –     |    –    | Inline `current_user.role == Role.ADMIN` |
| `/platform/zero-trust/<id>/answer/<cid>/downgrade`       |   ✓   |    –     |    –    | Inline (admin only; downgrade-not-upgrade rank check) |
| `/platform/zero-trust/<id>/walkability`                  |   ✓   |    ✓     |    –    | Inline role check (admin + reviewer) |
| `/platform/zero-trust/<id>/evidence/<cid>`               |   ✓   |    ✓     |    –    | Role gate; locked responses blocked |
| `/platform/attack-surface/*`                             |   ✓   |   R/O    |    –    | Role gate |

## Integrity-model invariants (role-independent)

Some rules are enforced regardless of role. They apply equally to ADMIN.

| Rule                                                                    | Where                                                          |
|-------------------------------------------------------------------------|----------------------------------------------------------------|
| `Artifact.origin` is immutable after insert.                             | `@validates` on the model + SQLAlchemy `before_update` event + Postgres trigger |
| `audit_entries` is append-only.                                          | Postgres trigger `trg_audit_append_only`                       |
| The AI lane is never user-writable.                                      | `repository.write_ai_artifact` takes no `actor` parameter      |
| Picking an `ai_generated` artifact requires `acknowledged_ai_reuse=yes`. | `spine.picker.link_capability_list_to_project` raises if missing |
| Promotion never alters origin.                                           | `spine.repository.promote_artifact` only sets `reuse_status`   |
| Submitted questionnaire responses are locked.                            | `p2.answer` refuses on locked; routes refuse mutations after `project.stage == "submitted"` |

## Known gaps (v0.6)

1. **No per-user Client association.** The intake surface currently
   shows projects from any client to any CLIENT-role user. Once a
   `User.client_id` FK lands (planned v1.0+ once multi-tenant testing
   demands it), the intake surface tightens to the user's own client.

2. **REVIEWER's "R/O across the platforms" is enforced only by convention
   in v0.6.** Reviewer can navigate the platform pages but does not have
   a mutate button anywhere — the templates conditionally hide them by
   role. A defensive layer (decorator) on the platform mutating routes
   should be added in v1.x.

3. **`promote` is open to REVIEWER as well as ADMIN.** This matches
   `promote_artifact`'s role check (`Role.ADMIN, Role.REVIEWER`). If
   strict separation is desired (admin promotes, reviewer audits), the
   spine function is the place to tighten it.
