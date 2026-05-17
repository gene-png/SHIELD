"""Repository browser — the one global, read-only, cross-module view.

Also hosts the promotion action: promotion is a status change on an
existing artifact, so it belongs to the repository view rather than to a
particular platform's workflow routes.
"""
from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import select

from ..extensions import db
from ..models import Artifact, Origin, Project
from .access import require_client_access, scope_query
from .rbac import admin_or_reviewer
from .repository import promote_artifact

bp = Blueprint("repository", __name__, template_folder="../templates/repository")


@bp.route("/")
@login_required
def browse():
    origin_filter = request.args.get("origin", "all")
    stmt = select(Artifact).order_by(Artifact.created_at.desc()).limit(200)
    if origin_filter != "all":
        try:
            stmt = stmt.where(Artifact.origin == Origin(origin_filter))
        except ValueError:
            pass
    # Per-client scoping (v1.8): admins see everything; reviewers see
    # everything if un-assigned, otherwise their assigned clients;
    # clients see only their own. Synthetic "Client Repository" projects
    # are filtered out of the global browse — they belong on the
    # client's intake_view and the portal /portal/documents page.
    stmt = scope_query(stmt, Artifact)
    stmt = stmt.join(Project, Artifact.project_id == Project.id).where(
        Project.is_client_repository.is_(False)
    )
    artifacts = list(db.session.scalars(stmt))
    return render_template(
        "repository/browse.html",
        artifacts=artifacts,
        origin_filter=origin_filter,
    )


@bp.route("/artifact/<artifact_id>")
@login_required
def artifact_detail(artifact_id: str):
    art = db.session.get(Artifact, artifact_id)
    if art is not None:
        # Cross-client read protection. 404 (not 403) before any
        # other handling so existence of another client's artifact
        # never leaks through the error code.
        require_client_access(art.client_id)
    if art is None:
        return ("Not found", 404)
    project = db.session.get(Project, art.project_id)
    promoter = None
    if art.promoted_by_id:
        from ..models import User
        promoter = db.session.get(User, art.promoted_by_id)
    return render_template(
        "repository/artifact_detail.html",
        art=art, project=project, promoter=promoter,
    )


@bp.route("/artifact/<artifact_id>/promote", methods=["POST"])
@login_required
@admin_or_reviewer
def promote(artifact_id: str):
    """Promote an AI artifact to APPROVED.

    The spine's `promote_artifact` enforces the rules: only AI-origin can
    be promoted, only by admin or reviewer, only with a non-empty reason.
    Origin is never altered (immutable). This route is the UI surface.
    """
    art = db.session.get(Artifact, artifact_id)
    if art is None:
        abort(404)
    reason = request.form.get("reason", "").strip()
    try:
        promote_artifact(artifact=art, actor=current_user, reason=reason)
    except PermissionError as e:
        flash(f"Promotion denied: {e}", "error")
    except ValueError as e:
        flash(f"Promotion rejected: {e}", "error")
    else:
        flash("Draft approved for reuse — still labeled as an automated-analysis draft.", "info")
    return redirect(url_for("repository.artifact_detail", artifact_id=art.id))
