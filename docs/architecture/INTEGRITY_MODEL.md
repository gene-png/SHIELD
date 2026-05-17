# SHIELD — Integrity Model

This document is the developer's contract for the integrity model from
the unified-portal spec §4. Every line is enforced somewhere in code; the
"enforced by" column tells you where.

## Origins (immutable)

| Origin                  | Meaning                                                          | Enforced by |
|-------------------------|------------------------------------------------------------------|-------------|
| `human_input`           | Uploaded or entered by a human (client or admin)                 | `RepositoryWriter.write_human_artifact` |
| `ai_generated`          | Produced by an AI processing step                                | `RepositoryWriter.write_ai_artifact` (no `actor` param) |
| `human_ai_informed`     | Synthesis a human authored that cites AI findings                | `RepositoryWriter.write_human_ai_informed_artifact` |

**Promotion** is a STATUS on `ai_generated`, not a separate origin:
`reuse_status: draft → approved`. Origin is never rewritten.

## DB-level enforcement

- `artifacts.origin` is `NOT NULL`. The trigger `trg_artifacts_origin_immutable`
  (in migration `0001_initial`) raises an exception if any `UPDATE`
  attempts to change the column.
- `audit_entries` has trigger `trg_audit_append_only` rejecting `UPDATE`
  and `DELETE`.

## Reuse-moment gate (the integrity chokepoint)

When a user selects an `ai_generated` artifact in the picker
(`shield/spine/picker.py`), the picker requires an explicit
acknowledgment checkbox (`acknowledged_ai_reuse=yes`). The server raises
`PermissionError("AI_REUSE_ACK_REQUIRED")` if it is missing.

## Versioning and snapshot semantics

Capability lists are versioned per client. Cross-project linking
(`spine.picker.link_capability_list_to_project`) sets
`Project.capability_list_version_id` to the picked version's id. This is
a frozen reference: the list can change later (new version), but the
project keeps pointing at the version it was based on (Decision #2).

## Audit invariants

Every write goes through `spine.audit.log_audit`. The append-only DB
trigger means an attacker who manages SQL injection cannot tamper with
prior audit history — only append more rows, which leave their own trace.
