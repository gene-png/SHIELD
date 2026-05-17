"""Platform 2 routes (Zero Trust)."""
from __future__ import annotations

import json
from datetime import datetime

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import (
    Artifact,
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
    link_capability_list_to_project,
    list_capability_lists_for_client,
)
from ..spine.rbac import admin_only, admin_or_reviewer
from ..spine.repository import (
    write_human_artifact,
)
from . import bp
from .frameworks import FRAMEWORKS


def _get_project_or_404(project_id: str) -> Project:
    p = db.session.get(Project, project_id)
    if p is None or p.platform != PlatformType.ZERO_TRUST:
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
        .where(Project.platform == PlatformType.ZERO_TRUST,
               Project.archived.is_(False),
               Project.is_client_repository.is_(False))
        .order_by(Project.created_at.desc())
    )
    stmt = scope_query(stmt, Project)
    projects = list(db.session.scalars(stmt))
    return render_template("p2/index.html", projects=projects, frameworks=FRAMEWORKS)


# ----- project creation -----

@bp.route("/new", methods=["GET", "POST"])
@login_required
@admin_only
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
    has_assessment = any(a.stage == "current_state_assessment" for a in artifacts)
    return render_template(
        "p2/workspace.html", project=project, framework=framework,
        artifacts=artifacts, answered=answered,
        has_assessment=has_assessment,
    )


@bp.route("/project/<project_id>/summary")
@login_required
def project_summary(project_id: str):
    """Executive-first summary of the project (mirror of P3's run_detail).

    Pulls the latest current_state_assessment, parses its JSON
    `{controls: [...], summary: {implemented, partial, not_implemented}}`
    shape, surfaces the headline + counts + top three gaps, and keeps
    the per-pillar matrix collapsible below.
    """
    project = _get_project_or_404(project_id)
    framework = FRAMEWORKS.get(project.framework or "cisa_ztmm_v2")
    assessment_art = (
        db.session.query(Artifact)
        .filter_by(
            project_id=project.id,
            origin=Origin.AI_GENERATED,
            stage="current_state_assessment",
        )
        .order_by(Artifact.created_at.desc())
        .first()
    )
    assessment = None
    if assessment_art is not None and assessment_art.body_text:
        try:
            assessment = json.loads(assessment_art.body_text)
        except (ValueError, TypeError):
            assessment = None

    # Build a lookup from control_id to its catalog row so the template
    # can render names and pillars without rescanning the framework on
    # every iteration.
    control_lookup: dict = {}
    if framework is not None:
        control_lookup = {c.id: c for c in framework.controls}

    return render_template(
        "p2/project_summary.html",
        project=project,
        framework=framework,
        assessment=assessment,
        assessment_art=assessment_art,
        control_lookup=control_lookup,
    )


@bp.route("/project/<project_id>/answer", methods=["POST"])
@login_required
@admin_only
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
@admin_only
def analyze(project_id: str):
    project = _get_project_or_404(project_id)
    framework = FRAMEWORKS.get(project.framework or "cisa_ztmm_v2")
    if framework is None:
        flash("Unknown framework.", "error")
        return redirect(url_for("p2.workspace", project_id=project.id))

    from ..tasks import enqueue_ai, p2_analyze_job
    job = enqueue_ai(p2_analyze_job, project.id)
    flash("Current-state assessment queued. Refreshing as it runs …", "info")
    return redirect(url_for(
        "jobs.wait", job_id=job.id,
        next=url_for("p2.workspace", project_id=project.id),
    ))


# ----- Desired future state (spec §8.2 artifact #2) -----
#
# Aspirational stated intent. NOT evidence-backed. The UI must never let
# this read like the current-state assessment. We mark it origin=HUMAN_INPUT
# with stage=desired_future_state so the lane and origin badge separate
# it from the AI current-state artifact.

@bp.route("/project/<project_id>/set-target", methods=["GET", "POST"])
@login_required
@admin_only
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
@admin_only
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

    from ..tasks import enqueue_ai, p2_roadmap_job
    job = enqueue_ai(p2_roadmap_job, project.id)
    flash("Transition roadmap queued. Refreshing as it runs …", "info")
    return redirect(url_for(
        "jobs.wait", job_id=job.id,
        next=url_for("p2.workspace", project_id=project.id),
    ))


# ----- Evidence upload tied to a control (spec §8.2) -----
#
# Spec rule: "Evidence attaches inline at the question it supports, never
# to a separate pile." The file is written to the human lane with
# trust_tier=CLIENT_PROVIDED_EVIDENCE and the QuestionnaireResponse's
# evidence_artifact_id is updated to point at the artifact.

@bp.route("/project/<project_id>/evidence/<control_id>", methods=["POST"])
@login_required
@admin_only
def upload_evidence(project_id: str, control_id: str):
    project = _get_project_or_404(project_id)
    if project.stage == "submitted":
        flash("Project is submitted; evidence is locked.", "error")
        return redirect(url_for("p2.workspace", project_id=project.id))

    response = (
        db.session.query(QuestionnaireResponse)
        .filter_by(project_id=project.id, control_id=control_id)
        .one_or_none()
    )
    if response is None:
        flash(
            f"Answer the control {control_id} first; evidence attaches "
            f"to a saved answer.",
            "error",
        )
        return redirect(url_for("p2.workspace", project_id=project.id))
    if response.locked:
        flash("Response is locked; evidence cannot be changed.", "error")
        return redirect(url_for("p2.workspace", project_id=project.id))

    f = request.files.get("evidence_file")
    if not f or not f.filename:
        flash("Pick a file to upload as evidence.", "error")
        return redirect(url_for("p2.workspace", project_id=project.id) + f"#c-{control_id}")

    art = write_human_artifact(
        project=project,
        stage="evidence",
        title=f"Evidence for {control_id}: {f.filename}",
        file_stream=f.stream,
        filename=f.filename,
        mime_type=f.mimetype,
        actor=current_user,
        trust_tier=TrustTier.CLIENT_PROVIDED_EVIDENCE,
    )
    response.evidence_artifact_id = art.id
    db.session.commit()
    log_audit(
        "p2.evidence.upload",
        actor=current_user,
        target_type="questionnaire_response", target_id=response.id,
        project_id=project.id, client_id=project.client_id,
        details={"control_id": control_id, "artifact_id": art.id, "filename": f.filename},
    )
    flash(
        f"Evidence attached to {control_id}: {f.filename}.",
        "info",
    )
    return redirect(url_for("p2.workspace", project_id=project.id) + f"#c-{control_id}")


# ----- Submit / lock (spec §10 decision #3 — immutable once submitted) -----

@bp.route("/project/<project_id>/submit", methods=["POST"])
@login_required
@admin_only
def submit(project_id: str):
    """Lock every QuestionnaireResponse so attribution is immutable.

    Per spec decision #3, "immutable once submitted" is a hard rule. After
    this route runs, the `answer` route refuses edits to any locked
    response. The project's `stage` moves to "submitted".
    """
    project = _get_project_or_404(project_id)

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
@admin_only
def downgrade_attribution(project_id: str, control_id: str):
    project = _get_project_or_404(project_id)

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


# ----- Reviewer audit-walkability (spec §8.2) -----
#
# The auditor / reviewer is a different reader than the admin or client.
# They need traceability over polish: for every control they want to walk
# the line `framework control → client's claim → evidence → AI's
# assessment → gap → proposed remediation`, with each part visibly
# separable, "without trusting the tool".

@bp.route("/project/<project_id>/walkability")
@login_required
@admin_or_reviewer
def walkability(project_id: str):
    project = _get_project_or_404(project_id)

    framework = FRAMEWORKS.get(project.framework or "")
    if framework is None:
        flash("No framework set on this project.", "error")
        return redirect(url_for("p2.workspace", project_id=project.id))

    responses = (
        db.session.query(QuestionnaireResponse)
        .filter_by(project_id=project.id)
        .all()
    )
    responses_by_control = {r.control_id: r for r in responses}

    current_state = (
        db.session.query(Artifact)
        .filter_by(
            project_id=project.id, origin=Origin.AI_GENERATED,
            stage="current_state_assessment",
        )
        .order_by(Artifact.created_at.desc())
        .first()
    )
    ai_by_control: dict[str, dict] = {}
    if current_state and current_state.body_text:
        try:
            data = json.loads(current_state.body_text)
            for finding in data.get("controls", []):
                cid = finding.get("control_id")
                if cid:
                    ai_by_control[cid] = finding
        except json.JSONDecodeError:
            pass

    roadmap = (
        db.session.query(Artifact)
        .filter_by(
            project_id=project.id, origin=Origin.AI_GENERATED,
            stage="transition_roadmap",
        )
        .order_by(Artifact.created_at.desc())
        .first()
    )
    remediation_by_control: dict[str, dict] = {}
    if roadmap and roadmap.body_text:
        try:
            data = json.loads(roadmap.body_text)
            for item in data.get("items", []):
                cid = item.get("control_id")
                if cid:
                    remediation_by_control[cid] = item
        except json.JSONDecodeError:
            pass

    return render_template(
        "p2/walkability.html",
        project=project, framework=framework,
        responses_by_control=responses_by_control,
        ai_by_control=ai_by_control,
        remediation_by_control=remediation_by_control,
        current_state=current_state, roadmap=roadmap,
    )


def _load_prompt(filename: str) -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / "ai" / "prompts" / filename).read_text(encoding="utf-8")
