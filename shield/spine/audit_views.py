"""Audit log viewer — the read surface for spine.audit writes.

The spec puts the audit log at the heart of the integrity model (§4,
§5, threat model) but the v1.0 → v1.3 work only wrote to it. This adds
the missing read surface: an admin/reviewer-only listing with filters
on action, client, and time, sorted newest-first.

The underlying `audit_entries` table is append-only (Postgres trigger
+ no UPDATE routes), so this view can never modify state — only display.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from flask import Blueprint, render_template, request
from flask_login import login_required
from sqlalchemy import select

from ..extensions import db
from ..models import AuditEntry
from .rbac import admin_or_reviewer

bp = Blueprint("audit", __name__, template_folder="../templates/spine")


_PAGE_SIZE = 50

# Lookback presets for the date filter. v1.4 keeps the UI simple; a
# free-form date range can come later if it's actually requested.
_SINCE_PRESETS = {
    "1h":   timedelta(hours=1),
    "24h":  timedelta(days=1),
    "7d":   timedelta(days=7),
    "30d":  timedelta(days=30),
    "all":  None,
}


@bp.route("/")
@login_required
@admin_or_reviewer
def index():
    action_q = (request.args.get("action") or "").strip()
    client_id = (request.args.get("client_id") or "").strip()
    since = (request.args.get("since") or "7d").strip()
    page = max(1, int(request.args.get("page") or "1"))

    from .access import scope_query, user_clients
    stmt = select(AuditEntry).order_by(AuditEntry.at.desc())
    # Reviewer scoping (v1.8): reviewers with assignments see only
    # audit rows tagged with one of their clients. Admins are
    # unrestricted; un-assigned reviewers preserve the v1.7
    # see-everything default.
    stmt = scope_query(stmt, AuditEntry)

    if action_q:
        stmt = stmt.where(AuditEntry.action.ilike(f"%{action_q}%"))
    if client_id:
        # Defense-in-depth: an explicit client_id filter still has to
        # pass the per-user scope (the scope_query above already
        # constrains it). require_client_access blocks reviewers from
        # poking other clients' ids into the query string.
        from .access import require_client_access
        require_client_access(client_id)
        stmt = stmt.where(AuditEntry.client_id == client_id)
    if since in _SINCE_PRESETS and _SINCE_PRESETS[since] is not None:
        threshold = datetime.utcnow() - _SINCE_PRESETS[since]
        stmt = stmt.where(AuditEntry.at >= threshold)

    offset = (page - 1) * _PAGE_SIZE
    entries = list(db.session.scalars(stmt.limit(_PAGE_SIZE).offset(offset)))

    # Filter dropdown — scoped to what the user can actually filter on.
    clients = user_clients()

    return render_template(
        "spine/audit_index.html",
        entries=entries, clients=clients,
        action_q=action_q, client_id=client_id, since=since, page=page,
        page_size=_PAGE_SIZE,
        has_next=len(entries) == _PAGE_SIZE,
        since_presets=list(_SINCE_PRESETS.keys()),
    )
