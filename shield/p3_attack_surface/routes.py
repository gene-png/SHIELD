"""Platform 3 routes (Attack Surface / ATT&CK coverage)."""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import login_required

from ..extensions import db
from ..models import (
    Artifact,
    Client,
    CoverageFinding,
    CoverageRun,
    MitreTechnique,
    PlatformType,
    Project,
)
from ..spine.audit import log_audit
from ..spine.exporters import coverage_run_to_xlsx
from ..spine.picker import (
    link_capability_list_to_project,
    list_capability_lists_for_client,
)
from ..spine.rbac import admin_only
from . import bp
from .attack_data import TECHNIQUES as STARTER_TECHNIQUES


def _load_techniques() -> list[dict]:
    """Return MITRE techniques as list-of-dicts.

    Prefers the DB catalog (populated by `flask vendor-attack` or by the
    seed script). Falls back to the in-memory starter set if the DB is
    empty (e.g. test config with SQLite in-memory and no seed run).
    Same dict shape in both cases.
    """
    rows = (
        db.session.query(MitreTechnique)
        .order_by(MitreTechnique.technique_id)
        .all()
    )
    if rows:
        return [
            {
                "technique_id": r.technique_id,
                "name": r.name,
                "tactic": r.tactic,
                "description": r.description or "",
            }
            for r in rows
        ]
    return STARTER_TECHNIQUES


def _get_project_or_404(project_id: str) -> Project:
    p = db.session.get(Project, project_id)
    if p is None or p.platform != PlatformType.ATTACK_SURFACE:
        abort(404)
    from ..spine.access import require_client_access
    require_client_access(p.client_id)
    return p


@bp.route("/")
@login_required
def index():
    from sqlalchemy import select

    from ..spine.access import scope_query
    stmt = (
        select(Project)
        .where(Project.platform == PlatformType.ATTACK_SURFACE,
               Project.archived.is_(False),
               Project.is_client_repository.is_(False))
        .order_by(Project.created_at.desc())
    )
    stmt = scope_query(stmt, Project)
    projects = list(db.session.scalars(stmt))
    return render_template("p3/index.html", projects=projects)


# ----- project creation -----

@bp.route("/new", methods=["GET", "POST"])
@login_required
@admin_only
def new_project():
    """Create a new Attack Surface project."""
    from flask_login import current_user
    clients = db.session.query(Client).order_by(Client.name).all()
    lists_by_client = {c.id: list_capability_lists_for_client(c.id) for c in clients}

    if request.method == "POST":
        client_id = (request.form.get("client_id") or "").strip()
        name = (request.form.get("name") or "").strip()
        cl_version_id = (request.form.get("capability_list_id") or "").strip() or None
        ack = request.form.get("acknowledged_ai_reuse") == "yes"
        form = {
            "client_id": client_id, "name": name,
            "capability_list_id": cl_version_id or "", "ack": ack,
        }

        if not client_id or not name:
            flash("Client and project name are required.", "error")
            return render_template(
                "p3/new_project.html",
                clients=clients, lists_by_client=lists_by_client, form=form,
            )
        client = db.session.get(Client, client_id)
        if client is None:
            flash("Invalid client.", "error")
            return redirect(url_for("p3.new_project"))

        project = Project(
            client_id=client.id,
            platform=PlatformType.ATTACK_SURFACE,
            name=name,
            stage="intake",
            created_by_id=current_user.id,
        )
        db.session.add(project)
        db.session.commit()
        log_audit(
            "project.create", actor=current_user,
            target_type="project", target_id=project.id,
            project_id=project.id, client_id=client.id,
            details={"platform": "attack_surface", "name": name},
        )

        if cl_version_id:
            try:
                link_capability_list_to_project(
                    project=project,
                    capability_list_version_id=cl_version_id,
                    actor=current_user,
                    acknowledged_ai_reuse=ack,
                )
            except PermissionError as e:
                if "AI_REUSE_ACK_REQUIRED" in str(e):
                    flash(
                        "That capability list is AI-generated. "
                        "Tick the acknowledgment to reuse it.",
                        "error",
                    )
                    return redirect(url_for("p3.workspace", project_id=project.id))
                raise
            except ValueError as e:
                flash(f"Could not link capability list: {e}", "error")
                return redirect(url_for("p3.workspace", project_id=project.id))

        flash(f"Created project '{name}'.", "info")
        return redirect(url_for("p3.workspace", project_id=project.id))

    return render_template(
        "p3/new_project.html",
        clients=clients, lists_by_client=lists_by_client, form={},
    )


@bp.route("/project/<project_id>")
@login_required
def workspace(project_id: str):
    project = _get_project_or_404(project_id)
    artifacts = (
        db.session.query(Artifact).filter_by(project_id=project.id)
        .order_by(Artifact.created_at.desc()).all()
    )
    runs = (
        db.session.query(CoverageRun).filter_by(project_id=project.id)
        .order_by(CoverageRun.created_at.desc()).all()
    )
    return render_template(
        "p3/workspace.html",
        project=project, artifacts=artifacts, runs=runs,
        technique_count=len(_load_techniques()),
    )


@bp.route("/project/<project_id>/analyze", methods=["POST"])
@login_required
@admin_only
def analyze(project_id: str):
    project = _get_project_or_404(project_id)
    cl = project.capability_snapshot
    if cl is None:
        flash("Link a capability list to this project first.", "error")
        return redirect(url_for("p3.workspace", project_id=project.id))

    from ..tasks import enqueue_ai, p3_coverage_job
    job = enqueue_ai(p3_coverage_job, project.id)
    flash("ATT&CK coverage analysis queued. Refreshing as it runs …", "info")
    return redirect(url_for(
        "jobs.wait", job_id=job.id,
        next=url_for("p3.workspace", project_id=project.id),
    ))


@bp.route("/project/<project_id>/run/<run_id>")
@login_required
def run_detail(project_id: str, run_id: str):
    project = _get_project_or_404(project_id)
    run = db.session.get(CoverageRun, run_id)
    if run is None or run.project_id != project.id:
        abort(404)
    findings = (
        db.session.query(CoverageFinding)
        .filter_by(coverage_run_id=run.id).all()
    )
    techniques_by_id = {t["technique_id"]: t for t in _load_techniques()}
    return render_template(
        "p3/run_detail.html",
        project=project, run=run, findings=findings,
        techniques_by_id=techniques_by_id,
    )


@bp.route("/project/<project_id>/run/<run_id>/export.xlsx")
@login_required
def run_export(project_id: str, run_id: str):
    """4-sheet XLSX of a coverage run (Summary + Coverage + Gaps + Methodology).

    No role decorator — any authenticated platform user (admin/reviewer)
    can download. CLIENT is gated upstream by _restrict_client_to_intake.
    """
    project = _get_project_or_404(project_id)
    run = db.session.get(CoverageRun, run_id)
    if run is None or run.project_id != project.id:
        abort(404)
    findings = (
        db.session.query(CoverageFinding)
        .filter_by(coverage_run_id=run.id).all()
    )
    techniques_by_id = {t["technique_id"]: t for t in _load_techniques()}
    blob = coverage_run_to_xlsx(run, project, findings, techniques_by_id)

    # ASCII-only filename for Content-Disposition. The em-dash and
    # other non-ASCII chars commonly land in client_display_name or
    # project name and silently break Chrome downloads via
    # `filename="..."` — switching to send_file lets Flask emit the
    # RFC 5987 `filename*` form for full Unicode in the save dialog.
    import io
    import unicodedata

    from flask import send_file
    raw = f"attack_coverage_{project.name}_{run.created_at.strftime('%Y%m%d_%H%M')}.xlsx"
    nfkd = unicodedata.normalize("NFKD", raw)
    ascii_safe = nfkd.encode("ascii", "ignore").decode("ascii")
    ascii_safe = ascii_safe.replace(" ", "_").replace("/", "-").strip("_")
    if not ascii_safe.lower().endswith(".xlsx"):
        ascii_safe = f"{ascii_safe[:80]}.xlsx"
    return send_file(
        io.BytesIO(blob),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=ascii_safe,
    )


def _load_prompt(filename: str) -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / "ai" / "prompts" / filename).read_text(encoding="utf-8")
