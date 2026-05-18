"""Plain-English rendering for the audit log details column.

Round-7 §6.6 / §3.3: the audit table used to render the `details`
column as `<details><summary>{...}</summary><pre>{indent JSON}</pre>`.
For compliance auditors who want the raw structured data, that's still
useful — it stays available as a "Show raw JSON" toggle. But the
default view of each row is a sentence a non-developer can read.

This module is the dictionary of action -> formatter functions. Each
formatter takes the audit row's details dict and returns a short
plain-English string. Unknown actions fall back to a key=value
one-liner so they're not invisible — they just look less polished
until somebody writes a proper formatter.

Formatters intentionally tolerate missing keys (the audit log is
append-only; old rows may pre-date a schema change). When a key is
absent, the formatter renders an em-dash or skips the clause rather
than raising.
"""
from __future__ import annotations

from typing import Any, Callable

_Formatter = Callable[[dict[str, Any]], str]


def _pluralize(n: int, singular: str, plural: str | None = None) -> str:
    """'1 item' vs '3 items' — used in summaries throughout."""
    if n == 1:
        return f"1 {singular}"
    return f"{n} {plural or singular + 's'}"


# ---------------------------------------------------------------------
# Per-action formatters. Keep one-liners. Avoid mentioning UUIDs in the
# narrative — the audit table already shows truncated ids in the
# Target column. If you need to mention a thing, mention what it is
# (a file's title, a list label) not its id.
# ---------------------------------------------------------------------


def _fmt_artifact_write_human(d: dict[str, Any]) -> str:
    stage = d.get("stage") or "—"
    fn = d.get("filename") or d.get("title") or "a document"
    return f"Saved uploaded {fn} at stage {stage}."


def _fmt_artifact_write_ai(d: dict[str, Any]) -> str:
    stage = d.get("stage") or "—"
    pv = d.get("prompt_version") or "?"
    return f"Wrote an automated draft at stage {stage} (prompt {pv})."


def _fmt_artifact_write_human_ai_informed(d: dict[str, Any]) -> str:
    stage = d.get("stage") or "—"
    return f"Saved a reviewed version at stage {stage}."


def _fmt_artifact_promote(d: dict[str, Any]) -> str:
    reason = d.get("reason") or ""
    if reason:
        return f"Approved for reuse — “{reason}”."
    return "Approved for reuse."


def _fmt_artifact_adopted(d: dict[str, Any]) -> str:
    prior = d.get("prior_stage") or "client_repository"
    return f"Adopted a document into the project (was at stage {prior})."


def _fmt_project_create(d: dict[str, Any]) -> str:
    name = d.get("name") or "(unnamed)"
    plat = (d.get("platform") or "").replace("_", " ") or "—"
    src = d.get("created_from")
    if src:
        return f"Created project “{name}” ({plat}) via {src}."
    return f"Created project “{name}” ({plat})."


def _fmt_project_link_capability_list(d: dict[str, Any]) -> str:
    v = d.get("version")
    label = d.get("label") or "(no label)"
    if v is not None:
        return f"Linked capability list v{v} “{label}” to the project."
    return f"Linked capability list “{label}” to the project."


def _fmt_intake_upload(d: dict[str, Any]) -> str:
    fn = d.get("filename") or "a document"
    n = d.get("size_bytes")
    role = d.get("actor_role") or ""
    size = f" ({n:,} bytes)" if isinstance(n, int) else ""
    suffix = f" by {role}" if role else ""
    return f"Uploaded {fn}{size}{suffix}."


def _fmt_file_uploaded_to_repository(d: dict[str, Any]) -> str:
    fn = d.get("filename") or d.get("title") or "a document"
    return f"Client uploaded {fn} to their repository."


def _fmt_client_intake_completed(d: dict[str, Any]) -> str:
    return "Client finished the intake wizard."


def _fmt_client_service_interest_changed(d: dict[str, Any]) -> str:
    before = d.get("before") or []
    after = d.get("after") or []
    added = [s for s in after if s not in before]
    removed = [s for s in before if s not in after]
    parts = []
    if added:
        parts.append("added " + ", ".join(added))
    if removed:
        parts.append("removed " + ", ".join(removed))
    if not parts:
        return "Service interests confirmed (no change)."
    return "Service interests updated: " + "; ".join(parts) + "."


def _fmt_client_service_requested(d: dict[str, Any]) -> str:
    svc = (d.get("service") or "").replace("_", " ") or "a service"
    return f"Client requested {svc}."


def _fmt_client_service_request_fulfilled(d: dict[str, Any]) -> str:
    svc = (d.get("service") or "").replace("_", " ") or "the service"
    return f"Fulfilled a {svc} request by creating / adopting a project."


def _fmt_client_service_request_declined(d: dict[str, Any]) -> str:
    reason = d.get("reason") or ""
    if reason:
        return f"Declined a service request — “{reason}”."
    return "Declined a service request."


def _fmt_client_invited_user(d: dict[str, Any]) -> str:
    email = d.get("email") or "a colleague"
    return f"Invited {email} to join the client."


def _fmt_client_invitation_revoked(d: dict[str, Any]) -> str:
    email = d.get("email") or "the invitee"
    return f"Revoked {email}'s pending invitation."


def _fmt_client_user_joined(d: dict[str, Any]) -> str:
    return "A colleague accepted their invitation and joined."


def _fmt_client_about_saved(d: dict[str, Any]) -> str:
    fields = d.get("fields_changed") or d.get("changed") or []
    if fields:
        return f"Saved client info ({_pluralize(len(fields), 'field')} updated)."
    return "Saved client info."


def _fmt_client_self_signup_bootstrap(d: dict[str, Any]) -> str:
    return "Created a new client via self-signup."


def _fmt_message_posted(d: dict[str, Any]) -> str:
    n = d.get("length")
    where = "project thread" if d.get("project_id") else "client-level thread"
    if isinstance(n, int):
        return f"Posted a message ({n} chars) on the {where}."
    return f"Posted a message on the {where}."


def _fmt_deliverable_finalized(d: dict[str, Any]) -> str:
    title = d.get("title") or "(untitled)"
    return f"Finalized deliverable “{title}”."


def _fmt_deliverable_superseded(d: dict[str, Any]) -> str:
    title = d.get("title") or "(untitled)"
    return f"Superseded a prior deliverable “{title}”."


def _fmt_access_denied(d: dict[str, Any]) -> str:
    path = d.get("path") or "(unknown path)"
    method = d.get("method") or "GET"
    return f"Cross-client access attempt — {method} {path}."


def _fmt_p1_finalize(d: dict[str, Any]) -> str:
    items = d.get("item_count")
    if isinstance(items, int):
        return f"Approved the final capability list ({_pluralize(items, 'item')})."
    return "Approved the final capability list."


def _fmt_p2_evidence_upload(d: dict[str, Any]) -> str:
    ctrl = d.get("control_id") or "an unknown control"
    return f"Attached evidence to {ctrl}."


def _fmt_p2_submit(d: dict[str, Any]) -> str:
    answered = d.get("answered")
    if isinstance(answered, int):
        return f"Submitted final answers — {_pluralize(answered, 'control')}."
    return "Submitted final answers."


def _fmt_p2_attribution_downgrade(d: dict[str, Any]) -> str:
    ctrl = d.get("control_id") or "(control)"
    return f"Downgraded attribution on {ctrl}."


def _fmt_auth_login(d: dict[str, Any]) -> str:
    role = d.get("role") or "user"
    return f"Logged in ({role})."


def _fmt_auth_logout(d: dict[str, Any]) -> str:
    return "Logged out."


def _fmt_seed(d: dict[str, Any]) -> str:
    # seed.* actions — keep these literal-ish, ops will see them only
    # in the dev/test trail.
    bits = []
    for k in ("items", "projects", "mitre_techniques", "count"):
        if k in d:
            bits.append(f"{k}={d[k]}")
    return "Seeded demo data" + (f" ({', '.join(bits)})." if bits else ".")


# ---------------------------------------------------------------------
# Registry. Order doesn't matter; lookup is by exact action string,
# falling back to prefix match for action families (e.g. seed.*).
# ---------------------------------------------------------------------

def _fmt_archived(d: dict[str, Any]) -> str:
    reason = d.get("reason") or ""
    return f"Archived. {reason}".rstrip(". ") + "."


def _fmt_unarchived(d: dict[str, Any]) -> str:
    return "Restored from archive."


def _fmt_purged(d: dict[str, Any]) -> str:
    reason = d.get("reason") or ""
    files = d.get("files_deleted")
    prefix = "Purged"
    if isinstance(files, int) and files > 0:
        prefix += f" ({files} files removed)"
    return prefix + (f" — {reason}" if reason else ".") + ("." if not reason else "")


FORMATTERS: dict[str, _Formatter] = {
    "artifact.write_human":            _fmt_artifact_write_human,
    "artifact.write_ai":               _fmt_artifact_write_ai,
    "artifact.write_human_ai_informed": _fmt_artifact_write_human_ai_informed,
    "artifact.promote":                _fmt_artifact_promote,
    "artifact.adopted_into_project":   _fmt_artifact_adopted,
    "project.create":                  _fmt_project_create,
    "project.created":                 _fmt_project_create,
    "project.create_client_repository": lambda d: "Created the client's upload repository.",
    "project.link_capability_list":    _fmt_project_link_capability_list,
    "intake.upload":                   _fmt_intake_upload,
    "file_uploaded_to_repository":     _fmt_file_uploaded_to_repository,
    "client.intake_completed":         _fmt_client_intake_completed,
    "client.service_interest_changed": _fmt_client_service_interest_changed,
    "client.service_requested":        _fmt_client_service_requested,
    "client.service_request_fulfilled": _fmt_client_service_request_fulfilled,
    "client.service_request_declined": _fmt_client_service_request_declined,
    "client.invited_user":             _fmt_client_invited_user,
    "client.invitation_revoked":       _fmt_client_invitation_revoked,
    "client.user_joined":              _fmt_client_user_joined,
    "client.about_saved":              _fmt_client_about_saved,
    "client.self_signup_bootstrap":    _fmt_client_self_signup_bootstrap,
    "message.posted":                  _fmt_message_posted,
    "deliverable.finalized":           _fmt_deliverable_finalized,
    "deliverable.superseded":          _fmt_deliverable_superseded,
    "access_denied":                   _fmt_access_denied,
    "p1.finalize":                     _fmt_p1_finalize,
    "p2.evidence.upload":              _fmt_p2_evidence_upload,
    "p2.submit":                       _fmt_p2_submit,
    "p2.attribution_downgrade":        _fmt_p2_attribution_downgrade,
    "auth.login":                      _fmt_auth_login,
    "auth.logout":                     _fmt_auth_logout,
    "project.archived":                _fmt_archived,
    "project.unarchived":              _fmt_unarchived,
    "project.purged":                  _fmt_purged,
    "artifact.archived":               _fmt_archived,
    "artifact.unarchived":             _fmt_unarchived,
    "artifact.purged":                 _fmt_purged,
}

def _fmt_auto_progress(d: dict[str, Any]) -> str:
    # Action name is "auto_progress.<what>_queued"; surface that as a
    # sentence so the audit reader knows the system queued the step.
    return "Auto-queued the next step on the happy path."


_PREFIX_FORMATTERS: tuple[tuple[str, _Formatter], ...] = (
    ("seed.",          _fmt_seed),
    ("auto_progress.", _fmt_auto_progress),
)


def _fallback(details: dict[str, Any]) -> str:
    """Generic key=value rendering for actions without a dedicated formatter.

    Skips internal-only keys (anything starting with `_`) and stringifies
    values to keep the column compact.
    """
    if not details:
        return "—"
    parts = []
    for k, v in details.items():
        if k.startswith("_"):
            continue
        if isinstance(v, (dict, list)):
            v = f"{type(v).__name__}({len(v)})"
        parts.append(f"{k}={v}")
        if len(parts) >= 4:
            parts.append("…")
            break
    return "; ".join(parts) or "—"


def render_audit_details(action: str, details: dict[str, Any] | None) -> str:
    """Plain-English one-liner for an audit row's details column.

    Lookup order:
      1. exact match on `action` in FORMATTERS
      2. prefix match (seed.*, etc.)
      3. generic key=value fallback

    The raw JSON is still available via the "Show raw JSON" toggle in
    the template — this function only produces the headline text.
    """
    d = details or {}
    fn = FORMATTERS.get(action)
    if fn is not None:
        try:
            return fn(d)
        except Exception:
            # Formatter bug must NOT break the audit page. Drop to the
            # fallback so the row still renders something readable.
            return _fallback(d)
    for prefix, formatter in _PREFIX_FORMATTERS:
        if action.startswith(prefix):
            try:
                return formatter(d)
            except Exception:
                return _fallback(d)
    return _fallback(d)
