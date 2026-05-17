"""Client intake surface — the 6th page archetype (spec §6.6).

A stripped, guided render scoped to the projects a client may submit to.
States plainly that files become client-provided source. No repository
browsing, no picker, no AI-lane visibility. File upload only, written
to the human lane with actor=current_user, stage=intake.

Per spec: "writes only; reads back only confirmation of the client's own
submission". Client users only see their own uploads.
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
from ..models import Artifact, Origin, Project
from .audit import log_audit
from .repository import write_human_artifact

bp = Blueprint("intake", __name__, template_folder="../templates/intake")


def _client_visible_projects() -> list[Project]:
    """Projects a logged-in client may submit to.

    v0.6 scope: we do not yet have a per-user Client association in the
    model, so a CLIENT-role user sees every non-archived project that is
    still in an intake-friendly stage. v1.0 will tighten this to a
    Client FK on User. ADMIN/REVIEWER see all (intake surface is rarely
    relevant to them, but useful for testing).
    """
    q = (
        db.session.query(Project)
        .filter_by(archived=False)
        .order_by(Project.created_at.desc())
    )
    # Limit to projects in early stages — the intake surface is for
    # ingestion, not for browsing finalized engagements.
    open_stages = ("intake", "extraction", "extraction_review")
    return [p for p in q if p.stage in open_stages or p.stage is None]


@bp.route("/")
@login_required
def index():
    projects = _client_visible_projects()
    # Show the client only their own uploaded artifacts (writes-only feel,
    # confirmation surface only).
    my_uploads = (
        db.session.query(Artifact)
        .filter_by(actor_id=current_user.id, origin=Origin.HUMAN_INPUT)
        .order_by(Artifact.created_at.desc())
        .limit(10)
        .all()
    )
    return render_template(
        "intake/index.html",
        projects=projects,
        my_uploads=my_uploads,
    )


@bp.route("/project/<project_id>/upload", methods=["POST"])
@login_required
def upload(project_id: str):
    project = db.session.get(Project, project_id)
    if project is None or project.archived:
        abort(404)

    f = request.files.get("file")
    title = (request.form.get("title") or (f.filename if f else "")).strip() or "Untitled"
    if not f or not f.filename:
        flash("Pick a file to upload.", "error")
        return redirect(url_for("intake.index"))

    art = write_human_artifact(
        project=project,
        stage="intake",
        title=title,
        file_stream=f.stream,
        filename=f.filename,
        mime_type=f.mimetype,
        actor=current_user,
    )
    log_audit(
        "intake.upload",
        actor=current_user,
        target_type="artifact", target_id=art.id,
        project_id=project.id, client_id=project.client_id,
        details={
            "stage": "intake",
            "filename": f.filename,
            "size_bytes": art.size_bytes,
            "actor_role": current_user.role.value,
        },
    )
    # Don't bake the project's seed-client name into the flash —
    # speaks past the user about their own org. Per round-3 §2.3 Change 2.
    flash(f"Uploaded {f.filename}.", "info")
    return redirect(url_for("intake.index"))
