"""Platform 3 routes (Attack Surface / ATT&CK coverage)."""
from __future__ import annotations

import json

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import login_required

from . import bp
from .attack_data import TECHNIQUES
from ..ai.client import AIClient, AIError
from ..extensions import db
from ..models import (
    Artifact,
    CoverageFinding,
    CoverageRun,
    MitreTechnique,
    Origin,
    PlatformType,
    Project,
)
from ..spine.repository import write_ai_artifact


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
        technique_count=len(TECHNIQUES),
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
        "techniques": TECHNIQUES,
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
    techniques_by_id = {t["technique_id"]: t for t in TECHNIQUES}
    return render_template(
        "p3/run_detail.html",
        project=project, run=run, findings=findings,
        techniques_by_id=techniques_by_id,
    )


def _load_prompt(filename: str) -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / "ai" / "prompts" / filename).read_text(encoding="utf-8")
