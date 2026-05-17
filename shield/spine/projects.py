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
from ..models import PlatformType, Project
from .picker import link_capability_list_to_project, list_capability_lists_for_client
from .rbac import admin_or_reviewer

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
