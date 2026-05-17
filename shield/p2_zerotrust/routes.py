"""Platform 2 routes (Zero Trust)."""
from __future__ import annotations

import json
from datetime import datetime

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from . import bp
from .frameworks import FRAMEWORKS
from ..ai.client import AIClient, AIError
from ..extensions import db
from ..models import (
    Artifact,
    CapabilityList,
    Client,
    Origin,
    PlatformType,
    Project,
    QuestionnaireResponse,
    Role,
    TrustTier,
)
from ..spine.audit import log_audit
from ..spine.picker import (
    list_capability_lists_for_client,
    link_capability_list_to_project,
)
from ..spine.repository import (
    write_ai_artifact,
    write_human_ai_informed_artifact,
    write_human_artifact,
)


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


# ----- project creation -----

@bp.route("/new", methods=["GET", "POST"])
@login_required
def new_project():
    """Create a new Zero Trust project.

    Framework selection is the defining first act per spec §8.2:
    "The choice drives which question set the client even sees. It is not
    buried configuration; it is the engagement's defining act."
    """
    clients = db.session.query(Client).order_by(Client.name).all()
    lists_by_client = {c.id: list_capability_lists_for_client(c.id) for c in clients}

    if request.method == "POST":
        client_id = (request.form.get("client_id") or "").strip()
        name = (request.form.get("name") or "").strip()
        framework_id = (request.form.get("framework") or "").strip()
        cl_version_id = (request.form.get("capability_list_id") or "").strip() or None
        ack = request.form.get("acknowledged_ai_reuse") == "yes"

        form = {
            "client_id": client_id, "name": name, "framework": framework_id,
            "capability_list_id": cl_version_id or "", "ack": ack,
        }

        if not client_id or not name or not framework_id:
            flash("Client, project name, and framework are required.", "error")
            return render_template(
                "p2/new_project.html",
                clients=clients, lists_by_client=lists_by_client,
                frameworks=FRAMEWORKS, form=form,
            )
        if framework_id not in FRAMEWORKS:
            flash(f"Unknown framework: {framework_id}", "error")
            return render_template(
                "p2/new_project.html",
                clients=clients, lists_by_client=lists_by_client,
                frameworks=FRAMEWORKS, form=form,
            )
        client = db.session.get(Client, client_id)
        if client is None:
            flash("Invalid client.", "error")
            return redirect(url_for("p2.new_project"))

        project = Project(
            client_id=client.id,
            platform=PlatformType.ZERO_TRUST,
            name=name,
            stage="intake",
            framework=framework_id,
            created_by_id=current_user.id,
        )
        db.session.add(project)
        db.session.commit()
        log_audit(
            "project.create", actor=current_user,
            target_type="project", target_id=project.id,
            project_id=project.id, client_id=client.id,
            details={"platform": "zero_trust", "framework": framework_id, "name": name},
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
                    return redirect(url_for("p2.workspace", project_id=project.id))
                raise
            except ValueError as e:
                flash(f"Could not link capability list: {e}", "error")
                return redirect(url_for("p2.workspace", project_id=project.id))

        flash(f"Created project '{name}' against {FRAMEWORKS[framework_id].name}.", "info")
        return redirect(url_for("p2.workspace", project_id=project.id))

    return render_template(
        "p2/new_project.html",
        clients=clients, lists_by_client=lists_by_client,
        frameworks=FRAMEWORKS, form={},
    )


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
    if project.stage == "submitted":
        flash(
            "This project has been submitted. Per spec decision #3, "
            "attribution is immutable once submitted — reopen via a "
            "fresh engagement.",
            "error",
        )
        return redirect(url_for("p2.workspace", project_id=project.id))

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
        project=project, stage="current_state_assessment",
        title="AI posture analysis — current state",
        body_text=text,
        input_artifact_ids=[],
        prompt_version=lineage["prompt_version"],
        model=lineage["model"],
        capability_list_version_id=cl.id if cl else None,
        additional_lineage=lineage,
    )
    flash("Current-state assessment landed in the AI lane.", "info")
    return redirect(url_for("p2.workspace", project_id=project.id))


# ----- Desired future state (spec §8.2 artifact #2) -----
#
# Aspirational stated intent. NOT evidence-backed. The UI must never let
# this read like the current-state assessment. We mark it origin=HUMAN_INPUT
# with stage=desired_future_state so the lane and origin badge separate
# it from the AI current-state artifact.

@bp.route("/project/<project_id>/set-target", methods=["GET", "POST"])
@login_required
def set_target(project_id: str):
    project = _get_project_or_404(project_id)
    framework = FRAMEWORKS.get(project.framework or "")
    if framework is None:
        flash("This project has no framework set.", "error")
        return redirect(url_for("p2.workspace", project_id=project.id))

    # Latest desired-state artifact (if any) so the form can pre-fill.
    existing = (
        db.session.query(Artifact)
        .filter_by(project_id=project.id, origin=Origin.HUMAN_INPUT,
                   stage="desired_future_state")
        .order_by(Artifact.created_at.desc())
        .first()
    )
    prefilled: dict = {}
    if existing and existing.body_text:
        try:
            prefilled = json.loads(existing.body_text).get("targets", {})
        except json.JSONDecodeError:
            prefilled = {}

    if request.method == "POST":
        notes = (request.form.get("notes") or "").strip()
        targets: dict[str, str] = {}
        for c in framework.controls:
            val = (request.form.get(f"target_{c.id}") or "").strip()
            if val in ("implemented", "partial", "not_implemented", "na"):
                targets[c.id] = val

        if not targets:
            flash("Pick at least one target answer.", "error")
            return render_template(
                "p2/set_target.html", project=project,
                framework=framework, targets=prefilled, notes=notes,
            )

        payload = {"notes": notes, "targets": targets}
        write_human_artifact(
            project=project,
            stage="desired_future_state",
            title=f"Desired future-state target ({framework.name})",
            file_stream=None, filename=None, mime_type=None,
            actor=current_user,
            body_text=json.dumps(payload, indent=2),
        )
        flash("Desired future-state target recorded (human-input).", "info")
        return redirect(url_for("p2.workspace", project_id=project.id))

    return render_template(
        "p2/set_target.html", project=project,
        framework=framework, targets=prefilled, notes="",
    )


# ----- Transition roadmap (spec §8.2 artifact #3) -----

@bp.route("/project/<project_id>/roadmap", methods=["POST"])
@login_required
def generate_roadmap(project_id: str):
    """Generate the transition roadmap AI artifact.

    Cites the current-state assessment AND the desired-future-state target
    in its lineage. Per the spec it is a 'living plan' — admins are
    expected to review and validate it; full validation flow is a v1.0
    refinement.
    """
    project = _get_project_or_404(project_id)
    framework = FRAMEWORKS.get(project.framework or "")
    if framework is None:
        flash("This project has no framework set.", "error")
        return redirect(url_for("p2.workspace", project_id=project.id))

    current = (
        db.session.query(Artifact)
        .filter_by(project_id=project.id, origin=Origin.AI_GENERATED,
                   stage="current_state_assessment")
        .order_by(Artifact.created_at.desc())
        .first()
    )
    desired = (
        db.session.query(Artifact)
        .filter_by(project_id=project.id, origin=Origin.HUMAN_INPUT,
                   stage="desired_future_state")
        .order_by(Artifact.created_at.desc())
        .first()
    )
    if current is None or desired is None:
        flash(
            "Roadmap requires BOTH the current-state assessment AND a "
            "desired future-state target. Produce them first.",
            "error",
        )
        return redirect(url_for("p2.workspace", project_id=project.id))

    capabilities = []
    cl = project.capability_snapshot
    if cl is not None:
        capabilities = [
            {"name": i.name, "vendor": i.vendor, "category": i.category, "function": i.function}
            for i in cl.items
        ]

    try:
        current_payload = json.loads(current.body_text or "{}")
    except json.JSONDecodeError:
        current_payload = current.body_text
    try:
        desired_payload = json.loads(desired.body_text or "{}")
    except json.JSONDecodeError:
        desired_payload = desired.body_text

    payload = {
        "framework": framework.id,
        "controls": [{"id": c.id, "title": c.title, "pillar": c.pillar} for c in framework.controls],
        "current_state": current_payload,
        "desired_future_state": desired_payload,
        "capabilities": capabilities,
    }

    try:
        ai = AIClient()
        text, lineage = ai.complete(
            system=_load_prompt("p2_roadmap.md"),
            user=json.dumps(payload, indent=2),
            prompt_version="p2_roadmap.v1",
            json_response=True,
        )
    except AIError as e:
        flash(f"Roadmap generation failed: {e}", "error")
        return redirect(url_for("p2.workspace", project_id=project.id))

    write_ai_artifact(
        project=project,
        stage="transition_roadmap",
        title="AI transition roadmap (draft — for admin validation)",
        body_text=text,
        input_artifact_ids=[current.id, desired.id],
        prompt_version=lineage["prompt_version"],
        model=lineage["model"],
        capability_list_version_id=cl.id if cl else None,
        additional_lineage=lineage,
    )
    flash(
        "Transition roadmap drafted in the AI lane. Per the spec it is "
        "a living plan — review and validate before sharing externally.",
        "info",
    )
    return redirect(url_for("p2.workspace", project_id=project.id))


# ----- Submit / lock (spec §10 decision #3 — immutable once submitted) -----

@bp.route("/project/<project_id>/submit", methods=["POST"])
@login_required
def submit(project_id: str):
    """Lock every QuestionnaireResponse so attribution is immutable.

    Per spec decision #3, "immutable once submitted" is a hard rule. After
    this route runs, the `answer` route refuses edits to any locked
    response. The project's `stage` moves to "submitted".
    """
    project = _get_project_or_404(project_id)
    if current_user.role != Role.ADMIN:
        flash("Only admins can submit.", "error")
        return redirect(url_for("p2.workspace", project_id=project.id))

    now = datetime.utcnow()
    locked_count = 0
    for r in db.session.query(QuestionnaireResponse).filter_by(project_id=project.id):
        if not r.locked:
            r.locked = True
            r.submitted_at = now
            locked_count += 1
    project.stage = "submitted"
    db.session.commit()
    log_audit(
        "p2.submit",
        actor=current_user,
        target_type="project", target_id=project.id,
        project_id=project.id, client_id=project.client_id,
        details={"locked_responses": locked_count},
    )
    flash(
        f"Submitted: locked {locked_count} response(s). Per the spec, "
        f"attribution is now immutable.",
        "info",
    )
    return redirect(url_for("p2.workspace", project_id=project.id))


# ----- Attribution downgrade (spec §10 decision #3) -----
#
# Admin may downgrade attribution but never upgrade. The hierarchy of
# client-trust (high → low):
#     CLIENT_ASSERTED  >  ADMIN_ASSISTED  >  ADMIN_ENTERED_ON_BEHALF
# CLIENT_PROVIDED_EVIDENCE is separate (file evidence; not a downgrade
# target). NOT_APPLICABLE is reserved for non-questionnaire artifacts.

_TIER_RANK = {
    TrustTier.CLIENT_ASSERTED: 3,
    TrustTier.ADMIN_ASSISTED: 2,
    TrustTier.ADMIN_ENTERED_ON_BEHALF: 1,
}


@bp.route("/project/<project_id>/answer/<control_id>/downgrade", methods=["POST"])
@login_required
def downgrade_attribution(project_id: str, control_id: str):
    project = _get_project_or_404(project_id)
    if current_user.role != Role.ADMIN:
        flash("Only admins can change attribution.", "error")
        return redirect(url_for("p2.workspace", project_id=project.id))

    new_tier_str = (request.form.get("trust_tier") or "").strip()
    try:
        new_tier = TrustTier(new_tier_str)
    except ValueError:
        flash(f"Invalid trust tier: {new_tier_str!r}", "error")
        return redirect(url_for("p2.workspace", project_id=project.id))

    response = (
        db.session.query(QuestionnaireResponse)
        .filter_by(project_id=project.id, control_id=control_id)
        .one_or_none()
    )
    if response is None:
        abort(404)
    if response.locked:
        flash("Response is locked (submitted); attribution is immutable.", "error")
        return redirect(url_for("p2.workspace", project_id=project.id))

    old_rank = _TIER_RANK.get(response.trust_tier, 0)
    new_rank = _TIER_RANK.get(new_tier, 0)
    if new_rank == 0:
        flash(
            f"{new_tier.value!r} is not a valid downgrade target.",
            "error",
        )
        return redirect(url_for("p2.workspace", project_id=project.id))
    if new_rank >= old_rank:
        flash(
            "Attribution can only be downgraded, never upgraded "
            f"({response.trust_tier.value} ⇒ {new_tier.value} rejected).",
            "error",
        )
        return redirect(url_for("p2.workspace", project_id=project.id))

    old_tier = response.trust_tier
    response.trust_tier = new_tier
    db.session.commit()
    log_audit(
        "p2.attribution_downgrade",
        actor=current_user,
        target_type="questionnaire_response", target_id=response.id,
        project_id=project.id, client_id=project.client_id,
        details={
            "control_id": control_id,
            "from": old_tier.value,
            "to": new_tier.value,
        },
    )
    flash(
        f"Attribution downgraded: {old_tier.value} ⇒ {new_tier.value}.",
        "info",
    )
    return redirect(url_for("p2.workspace", project_id=project.id) + f"#c-{control_id}")


def _load_prompt(filename: str) -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / "ai" / "prompts" / filename).read_text(encoding="utf-8")
