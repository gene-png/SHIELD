"""Role-based access control decorators.

Closes the gap flagged in docs/security/THREAT_MODEL.md and the v0.6
caveat in docs/security/RBAC_MATRIX.md ("REVIEWER's R/O is enforced by
convention only"). Apply these decorators on top of `@login_required`
to mutating routes so reviewer / client roles cannot bypass the nav
hiding by URL-typing or by replaying a captured form.

Use:
    @bp.route("/something", methods=["POST"])
    @login_required
    @admin_only
    def do_thing(): ...

Or:
    @admin_or_reviewer

Rules:
- ADMIN can do anything.
- REVIEWER can read everything but cannot mutate the spine, the
  capability list, the questionnaire, or any AI artifact. The two
  exceptions where REVIEWER can act are PROMOTE (the spec's audit
  persona may approve an AI artifact for downstream reuse) and the
  WALKABILITY view (a read-only audit walk by design).
- CLIENT never reaches these routes — the app-level role gate
  redirects them to /intake before the decorator runs. The decorator
  still rejects CLIENT defensively in case the gate is ever loosened.
"""
from __future__ import annotations

from functools import wraps

from flask import flash, redirect, url_for
from flask_login import current_user

from ..models import Role


def _redirect_unauthenticated():
    return redirect(url_for("identity.login"))


def admin_only(view):
    """Allow only Role.ADMIN. Reject REVIEWER / CLIENT with a flash."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return _redirect_unauthenticated()
        if current_user.role != Role.ADMIN:
            flash("This action requires the admin role.", "error")
            return redirect(url_for("home"))
        return view(*args, **kwargs)
    return wrapped


def admin_or_reviewer(view):
    """Allow ADMIN or REVIEWER. Reject CLIENT with a flash."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return _redirect_unauthenticated()
        if current_user.role not in (Role.ADMIN, Role.REVIEWER):
            flash("This action requires the admin or reviewer role.", "error")
            return redirect(url_for("home"))
        return view(*args, **kwargs)
    return wrapped
