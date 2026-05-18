"""Cross-platform project actions on the spine.

Currently: relinking a project to a different CapabilityListVersion via
the picker. This is the "after creation" version of the same flow the
platform `/new` routes use at creation time, so the AI-origin
acknowledgment gate is enforced in exactly one place.

Per spec §7.2 + decision #2: linking takes a *frozen snapshot* by
version-id, never a live reference. The earlier link, the artifacts
that referenced the earlier version, and the audit entries all survive
unchanged — only `Project.capability_list_version_id` is updated.
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

from ..extensions import db
from ..models import Artifact, Deliverable, PlatformType, Project
from .audit import log_audit
from .picker import link_capability_list_to_project, list_capability_lists_for_client
from .rbac import admin_only, admin_or_reviewer

bp = Blueprint("projects", __name__, template_folder="../templates/spine")


_PLATFORM_WORKSPACE_ENDPOINT = {
    PlatformType.TECH_DEBT:      "p1.workspace",
    PlatformType.ZERO_TRUST:     "p2.workspace",
    PlatformType.ATTACK_SURFACE: "p3.workspace",
}


def _workspace_url(project: Project) -> str:
    endpoint = _PLATFORM_WORKSPACE_ENDPOINT.get(project.platform)
    if endpoint is None:
        return url_for("home")
    return url_for(endpoint, project_id=project.id)


@bp.route("/<project_id>/relink-capability-list", methods=["GET", "POST"])
@login_required
@admin_or_reviewer
def relink_capability_list(project_id: str):
    project = db.session.get(Project, project_id)
    if project is None or project.archived:
        abort(404)
    # v1.8: 404 (not 403) when an admin-or-reviewer reaches a project
    # outside their assigned-client scope. The decorator above already
    # enforces the role; this enforces the client.
    from .access import require_client_access
    require_client_access(project.client_id)

    available = list_capability_lists_for_client(project.client_id)

    if request.method == "POST":
        cl_id = (request.form.get("capability_list_id") or "").strip()
        ack = request.form.get("acknowledged_ai_reuse") == "yes"
        if not cl_id:
            flash("Pick a capability list version.", "error")
            return render_template(
                "spine/relink_capability_list.html",
                project=project, available=available, form={"ack": ack},
            )
        try:
            link_capability_list_to_project(
                project=project,
                capability_list_version_id=cl_id,
                actor=current_user,
                acknowledged_ai_reuse=ack,
            )
        except PermissionError as e:
            if "AI_REUSE_ACK_REQUIRED" in str(e):
                flash(
                    "That capability list is AI-generated. Tick the "
                    "acknowledgment to reuse it.",
                    "error",
                )
                return render_template(
                    "spine/relink_capability_list.html",
                    project=project, available=available,
                    form={"capability_list_id": cl_id, "ack": ack},
                )
            raise
        except ValueError as e:
            flash(f"Could not link capability list: {e}", "error")
            return redirect(_workspace_url(project))
        flash("Capability list relinked. The earlier link is preserved in audit.", "info")
        return redirect(_workspace_url(project))

    return render_template(
        "spine/relink_capability_list.html",
        project=project, available=available, form={},
    )


# ====================================================================
# Finalize an artifact as a client-facing Deliverable (v1.8)
# ====================================================================
# Why this lives on the project, not the artifact: a Deliverable is
# anchored to the *project's* output (Capability List v1.2, P3 run
# 2026-04-15, etc.), and a single artifact's id stays internal to the
# working repository. Finalizing produces a row in `deliverables` that
# clients see via /portal/deliverables/. The underlying Artifact is
# unchanged — origin stays whatever it was; finalization is purely a
# snapshot/index entry, not a state change.

@bp.route("/<project_id>/finalize-artifact/<artifact_id>", methods=["POST"])
@login_required
@admin_only
def finalize_artifact(project_id: str, artifact_id: str):
    project = db.session.get(Project, project_id)
    if project is None:
        abort(404)
    from .access import require_client_access
    require_client_access(project.client_id)

    art = db.session.get(Artifact, artifact_id)
    if art is None or art.project_id != project.id:
        abort(404)

    title = (request.form.get("title") or art.title).strip() or art.title
    summary = (request.form.get("summary") or "").strip() or None

    # If an existing un-superseded deliverable already covers this
    # artifact, mark it superseded and create a new one. That's the
    # "Tech Debt report — Q2 vs Q3" use case the schema supports.
    existing = (
        db.session.query(Deliverable)
        .filter_by(project_id=project.id, artifact_id=art.id, superseded_at=None)
        .first()
    )

    new_deliverable = Deliverable(
        client_id=project.client_id, project_id=project.id, artifact_id=art.id,
        title=title, summary=summary, finalized_by=current_user.id,
    )
    db.session.add(new_deliverable)
    db.session.flush()  # need new_deliverable.id for the superseded_by link

    from datetime import datetime
    if existing is not None:
        existing.superseded_at = datetime.utcnow()
        existing.superseded_by = new_deliverable.id

    db.session.commit()

    log_audit(
        "deliverable.finalized",
        actor=current_user,
        target_type="deliverable", target_id=new_deliverable.id,
        project_id=project.id, client_id=project.client_id,
        details={
            "artifact_id": art.id,
            "title": title,
            "supersedes": existing.id if existing else None,
        },
    )
    if existing is not None:
        log_audit(
            "deliverable.superseded",
            actor=current_user,
            target_type="deliverable", target_id=existing.id,
            project_id=project.id, client_id=project.client_id,
            details={"superseded_by": new_deliverable.id},
        )
    flash("Finalized. The client can see it in their Deliverables.", "info")
    return redirect(_workspace_url(project))
