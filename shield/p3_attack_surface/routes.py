"""Platform 3 routes (Attack Surface / ATT&CK coverage)."""
from __future__ import annotations

import json

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import login_required

from . import bp
from .attack_data import TECHNIQUES as STARTER_TECHNIQUES
from ..ai.client import AIClient, AIError
from ..extensions import db
from ..models import (
    Artifact,
    Client,
    CoverageFinding,
    CoverageRun,
    MitreTechnique,
    Origin,
    PlatformType,
    Project,
)
from ..spine.audit import log_audit
from ..spine.picker import (
    list_capability_lists_for_client,
    link_capability_list_to_project,
)
from ..spine.repository import write_ai_artifact


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
    return p


@bp.route("/")
@login_required
def index():
    projects = (
        db.session.query(Project)
        .filter_by(platform=PlatformType.ATTACK_SURFACE, archived=False)
        .order_by(Project.created_at.desc())
        .all()
    )
    return render_template("p3/index.html", projects=projects)


# ----- project creation -----

@bp.route("/new", methods=["GET", "POST"])
@login_required
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
def analyze(project_id: str):
    project = _get_project_or_404(project_id)
    cl = project.capability_snapshot
    if cl is None:
        flash("Link a capability list to this project first.", "error")
        return redirect(url_for("p3.workspace", project_id=project.id))

    capabilities = [
        {"name": i.name, "vendor": i.vendor, "category": i.category, "function": i.function}
        for i in cl.items
    ]
    payload = {
        "capabilities": capabilities,
        "techniques": _load_techniques(),
    }

    try:
        ai = AIClient()
        text, lineage = ai.complete(
            system=_load_prompt("p3_attack_coverage.md"),
            user=json.dumps(payload),
            prompt_version="p3_attack_coverage.v1",
            json_response=True,
        )
    except AIError as e:
        flash(f"Coverage analysis failed: {e}", "error")
        return redirect(url_for("p3.workspace", project_id=project.id))

    art = write_ai_artifact(
        project=project, stage="attack_coverage",
        title="AI ATT&CK coverage analysis",
        body_text=text,
        input_artifact_ids=[],
        prompt_version=lineage["prompt_version"],
        model=lineage["model"],
        capability_list_version_id=cl.id,
        additional_lineage=lineage,
    )

    # Materialize the run so the executive view has structured data
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = {"findings": [], "executive_summary": {}}
    run = CoverageRun(
        project_id=project.id,
        capability_list_version_id=cl.id,
        artifact_id=art.id,
        summary=parsed.get("executive_summary", {}),
    )
    db.session.add(run)
    db.session.flush()
    for finding in parsed.get("findings", []):
        db.session.add(CoverageFinding(
            coverage_run_id=run.id,
            technique_id=finding.get("technique_id", ""),
            coverage=finding.get("coverage", "uncovered"),
            detection_tools=finding.get("detection_tools", []),
            prevention_tools=finding.get("prevention_tools", []),
            response_tools=finding.get("response_tools", []),
            rationale=finding.get("rationale", ""),
        ))
    db.session.commit()
    flash("Coverage analysis complete.", "info")
    return redirect(url_for("p3.run_detail", project_id=project.id, run_id=run.id))


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


def _load_prompt(filename: str) -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / "ai" / "prompts" / filename).read_text(encoding="utf-8")
