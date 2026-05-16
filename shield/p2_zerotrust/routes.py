"""Platform 2 routes (Zero Trust)."""
from __future__ import annotations

import json

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import bp
from .frameworks import FRAMEWORKS
from ..ai.client import AIClient, AIError
from ..extensions import db
from ..models import (
    Artifact,
    Origin,
    PlatformType,
    Project,
    QuestionnaireResponse,
    Role,
    TrustTier,
)
from ..spine.repository import write_ai_artifact, write_human_artifact


def _get_project_or_404(project_id: str) -> Project:
    p = db.session.get(Project, project_id)
    if p is None or p.platform != PlatformType.ZERO_TRUST:
        abort(404)
    return p


@bp.route("/")
@login_required
def index():
    projects = (
        db.session.query(Project)
        .filter_by(platform=PlatformType.ZERO_TRUST, archived=False)
        .order_by(Project.created_at.desc())
        .all()
    )
    return render_template("p2/index.html", projects=projects, frameworks=FRAMEWORKS)


@bp.route("/project/<project_id>")
@login_required
def workspace(project_id: str):
    project = _get_project_or_404(project_id)
    framework = FRAMEWORKS.get(project.framework or "cisa_ztmm_v2")
    artifacts = (
        db.session.query(Artifact)
        .filter_by(project_id=project.id)
        .order_by(Artifact.created_at.desc())
        .all()
    )
    responses = (
        db.session.query(QuestionnaireResponse)
        .filter_by(project_id=project.id)
        .all()
    )
    answered = {r.control_id: r for r in responses}
    return render_template(
        "p2/workspace.html", project=project, framework=framework,
        artifacts=artifacts, answered=answered,
    )


@bp.route("/project/<project_id>/answer", methods=["POST"])
@login_required
def answer(project_id: str):
    project = _get_project_or_404(project_id)
    control_id = request.form.get("control_id", "").strip()
    ans = request.form.get("answer", "").strip()
    rationale = request.form.get("rationale", "").strip()
    if not control_id or ans not in ("implemented", "partial", "not_implemented", "na"):
        flash("Invalid answer.", "error")
        return redirect(url_for("p2.workspace", project_id=project.id))

    # Trust tier rule (spec §4 + decision #3): conservative default; admin
    # presence ⇒ admin-assisted, never client-asserted.
    if current_user.role == Role.ADMIN:
        tier = TrustTier.ADMIN_ASSISTED
    elif current_user.role == Role.CLIENT:
        tier = TrustTier.CLIENT_ASSERTED
    else:
        tier = TrustTier.ADMIN_ASSISTED

    existing = (
        db.session.query(QuestionnaireResponse)
        .filter_by(project_id=project.id, control_id=control_id)
        .one_or_none()
    )
    if existing and existing.locked:
        flash("Answer is locked.", "error")
        return redirect(url_for("p2.workspace", project_id=project.id))

    if existing:
        existing.answer = ans
        existing.rationale = rationale
        existing.trust_tier = tier
        existing.attributed_user_id = current_user.id
    else:
        db.session.add(QuestionnaireResponse(
            project_id=project.id,
            framework=project.framework or "cisa_ztmm_v2",
            control_id=control_id,
            answer=ans,
            rationale=rationale,
            trust_tier=tier,
            attributed_user_id=current_user.id,
        ))
    db.session.commit()
    flash(f"Answer saved for {control_id}.", "info")
    return redirect(url_for("p2.workspace", project_id=project.id) + f"#c-{control_id}")


@bp.route("/project/<project_id>/analyze", methods=["POST"])
@login_required
def analyze(project_id: str):
    project = _get_project_or_404(project_id)
    framework = FRAMEWORKS.get(project.framework or "cisa_ztmm_v2")
    if framework is None:
        flash("Unknown framework.", "error")
        return redirect(url_for("p2.workspace", project_id=project.id))

    capabilities = []
    cl = project.capability_snapshot
    if cl is not None:
        capabilities = [
            {"name": i.name, "vendor": i.vendor, "category": i.category, "function": i.function}
            for i in cl.items
        ]
    responses = (
        db.session.query(QuestionnaireResponse).filter_by(project_id=project.id).all()
    )
    payload = {
        "framework": framework.id,
        "controls": [{"id": c.id, "title": c.title, "pillar": c.pillar} for c in framework.controls],
        "responses": [
            {"control_id": r.control_id, "answer": r.answer, "rationale": r.rationale,
             "trust_tier": r.trust_tier.value}
            for r in responses
        ],
        "capabilities": capabilities,
    }

    try:
        ai = AIClient()
        text, lineage = ai.complete(
            system=_load_prompt("p2_posture.md"),
            user=json.dumps(payload, indent=2),
            prompt_version="p2_posture.v1",
            json_response=True,
        )
    except AIError as e:
        flash(f"Posture analysis failed: {e}", "error")
        return redirect(url_for("p2.workspace", project_id=project.id))

    write_ai_artifact(
        project=project, stage="posture_analysis",
        title="AI posture analysis — current state",
        body_text=text,
        input_artifact_ids=[],
        prompt_version=lineage["prompt_version"],
        model=lineage["model"],
        capability_list_version_id=cl.id if cl else None,
        additional_lineage=lineage,
    )
    flash("Posture analysis landed in the AI lane.", "info")
    return redirect(url_for("p2.workspace", project_id=project.id))


def _load_prompt(filename: str) -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / "ai" / "prompts" / filename).read_text(encoding="utf-8")
