"""Per-client read scoping for the v1.8 client portal.

Three concerns this module handles:

1. **What client_ids may a given user read?**
   See `client_ids_for_user`. The return shape is one of:
     - `None`            (ADMIN: unrestricted)
     - `list[str]`       (CLIENT: their accepted memberships;
                          REVIEWER: their non-revoked assignments)
     - `list[str]`       (REVIEWER with zero assignments: SENTINEL_UNRESTRICTED
                          since the round-2 UX answer was "un-assigned
                          reviewers preserve the current see-everything
                          behavior until at least one assignment exists").

2. **Cross-client read attempts return 404, not 403.**
   Per the round-2 spec acceptance criteria: leaking *existence* is
   the loud failure mode. `require_client_access` aborts with 404
   and writes an `access_denied` audit row so it's still visible to
   ops without giving the caller information.

3. **SQL-level scoping helpers** for the dozen-ish list routes that
   filter by client. `scope_query(query, model, user)` applies the
   right `.where()` for the user's scope and is a no-op for admins.

This module is deliberately thin. Per-route enforcement lives in the
route handlers — that way it's grep-able which routes are scoped and
which aren't. There's also `@require_client_for_param` for the common
case of "the URL has a <client_id> path parameter, this user must
have access."
"""
from __future__ import annotations

from collections.abc import Iterable
from functools import wraps

from flask import abort, request
from flask_login import current_user
from sqlalchemy import Select

from ..extensions import db
from ..models import (
    Client,
    ClientMembership,
    ReviewerAssignment,
    Role,
)
from .audit import log_audit

# Sentinel: `None` means "unrestricted" (admin). Distinct from `[]`
# meaning "explicitly no clients" (a non-admin with no memberships).
SENTINEL_UNRESTRICTED: None = None


def client_ids_for_user(user) -> list[str] | None:
    """Return the list of client_ids the user may read, or None if unrestricted.

    Branches:
      - ADMIN                                   → None (unrestricted)
      - CLIENT                                  → accepted memberships
      - REVIEWER with at least one assignment   → those clients
      - REVIEWER with zero assignments          → None (preserves current
                                                  see-everything default
                                                  per round-2 §10 answer)
      - Unauthenticated                         → [] (sees nothing)

    Returns a list of string ids (no duplicates, order not guaranteed).
    """
    if not user or not getattr(user, "is_authenticated", False):
        return []
    if user.role == Role.ADMIN:
        return SENTINEL_UNRESTRICTED

    if user.role == Role.CLIENT:
        rows = (
            db.session.query(ClientMembership.client_id)
            .filter(
                ClientMembership.user_id == user.id,
                ClientMembership.accepted_at.is_not(None),
            )
            .all()
        )
        return list({r[0] for r in rows})

    if user.role == Role.REVIEWER:
        rows = (
            db.session.query(ReviewerAssignment.client_id)
            .filter(
                ReviewerAssignment.reviewer_id == user.id,
                ReviewerAssignment.revoked_at.is_(None),
            )
            .all()
        )
        if not rows:
            # Round-2 §10 answer: un-assigned reviewers preserve the
            # pre-v1.8 see-everything behavior. Flipping this to a
            # whitelist requires a follow-up migration to ensure every
            # current reviewer has at least one assignment first.
            return SENTINEL_UNRESTRICTED
        return list({r[0] for r in rows})

    return []


def require_client_access(client_id: str, *, user=None) -> None:
    """Abort with 404 if `user` may not read `client_id`.

    404 not 403 — the round-2 spec is explicit that existence of a
    resource owned by a different client must not leak through the
    error code. Writes an `access_denied` audit row before aborting
    so ops can still see the access attempt.

    `user` defaults to `current_user`; pass it explicitly only in
    tests or batch jobs that lack a request context.
    """
    if user is None:
        user = current_user
    allowed = client_ids_for_user(user)
    if allowed is SENTINEL_UNRESTRICTED:
        return
    if client_id in allowed:
        return
    log_audit(
        "access_denied",
        actor=user if getattr(user, "is_authenticated", False) else None,
        target_type="client",
        target_id=client_id,
        client_id=client_id,
        details={
            "path": getattr(request, "path", None),
            "method": getattr(request, "method", None),
        },
    )
    abort(404)


def scope_query(stmt: Select, model, user=None, *, attr: str = "client_id") -> Select:
    """Add a client-scope WHERE clause to `stmt`.

    No-op when the user is unrestricted (admin or un-assigned reviewer).
    Adds `WHERE <model>.<attr> IN (...)` when the user has a finite
    set, including the empty-set case (returns no rows).

    `attr` lets you scope a model whose client foreign key isn't named
    `client_id` — e.g. AuditEntry stores it on `client_id` too, but a
    future model might use `tenant_id` and this lets us reuse the helper.
    """
    if user is None:
        user = current_user
    allowed = client_ids_for_user(user)
    if allowed is SENTINEL_UNRESTRICTED:
        return stmt
    column = getattr(model, attr)
    if not allowed:
        # Empty allow-list: return a contradiction so the query yields
        # no rows but stays a valid Select (the caller's `.first()` /
        # `.all()` continue to work).
        return stmt.where(column.is_(None)).where(column.is_not(None))
    return stmt.where(column.in_(allowed))


def require_client_for_param(param_name: str = "client_id"):
    """Decorator: read `<param_name>` from view-args and gate access.

    Usage:
        @bp.route("/<client_id>")
        @login_required
        @require_client_for_param()
        def detail(client_id):
            ...

    Reduces boilerplate on the dozen routes whose only access concern
    is "does this user own this client?". For routes scoped indirectly
    (via project_id or artifact_id), use `require_client_access`
    inline after the lookup.
    """
    def deco(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            cid = kwargs.get(param_name)
            if cid is None:
                abort(404)
            require_client_access(cid)
            return view(*args, **kwargs)
        return wrapper
    return deco


def user_clients(user=None) -> Iterable[Client]:
    """Resolve the user's allowed Client rows for nav / dashboard rendering.

    For ADMIN this returns every Client in name order. For CLIENT and
    REVIEWER it returns only the in-scope rows. Always returns an
    iterable; callers should not assume list semantics.
    """
    if user is None:
        user = current_user
    allowed = client_ids_for_user(user)
    q = db.session.query(Client).order_by(Client.name)
    if allowed is SENTINEL_UNRESTRICTED:
        return q.all()
    if not allowed:
        return []
    return q.filter(Client.id.in_(allowed)).all()
