"""Platform 1 routes.

Stages (per unified-portal spec §8.1):
  1. Raw intake          (human upload)
  2. AI extraction       (AI lane)
  3. Extraction review   (human-authored-AI-informed)
  4. AI overlap analysis (AI lane)
  5. Conversational interrogation (AI lane scratch, opt-in commit)
  6. Admin-final list    (human-authored-AI-informed)
"""
from __future__ import annotations

import json

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import (
    Artifact,
    CapabilityList,
    CapabilityListItem,
    Client,
    Origin,
    PlatformType,
    Project,
)
from ..spine.audit import log_audit
from ..spine.picker import (
    link_capability_list_to_project,
    list_capability_lists_for_client,
)
from ..spine.rbac import admin_only
from ..spine.repository import (
    write_human_ai_informed_artifact,
    write_human_artifact,
)
from . import bp

# ----- module landing -----

@bp.route("/")
@login_required
def index():
    from sqlalchemy import select

    from ..spine.access import scope_query
    stmt = (
        select(Project)
        .where(Project.platform == PlatformType.TECH_DEBT,
               Project.archived.is_(False),
               Project.is_client_repository.is_(False))
        .order_by(Project.created_at.desc())
    )
    stmt = scope_query(stmt, Project)
    projects = list(db.session.scalars(stmt))
    return render_template("p1/index.html", projects=projects)


# ----- project creation -----

@bp.route("/new", methods=["GET", "POST"])
@login_required
@admin_only
def new_project():
    """Create a new Tech Debt project.

    Capability-list linking is handled here via the spine's picker so the
    integrity rules (snapshot-by-version-id; AI-origin acknowledgment
    gate) are enforced from a single chokepoint, not re-implemented per
    platform.
    """
    clients = db.session.query(Client).order_by(Client.name).all()

    # For each client, expose their capability list versions so the form
    # can show a dropdown without an extra round-trip. Map: client_id -> [cl, ...].
    lists_by_client: dict[str, list] = {
        c.id: list_capability_lists_for_client(c.id) for c in clients
    }

    if request.method == "POST":
        client_id = request.form.get("client_id", "").strip()
        name = request.form.get("name", "").strip()
        cl_version_id = request.form.get("capability_list_id", "").strip() or None
        ack = request.form.get("acknowledged_ai_reuse") == "yes"

        if not client_id or not name:
            flash("Client and project name are required.", "error")
            return render_template(
                "p1/new_project.html",
                clients=clients, lists_by_client=lists_by_client,
                form={"client_id": client_id, "name": name,
                      "capability_list_id": cl_version_id or "", "ack": ack},
            )

        client = db.session.get(Client, client_id)
        if client is None:
            flash("Invalid client.", "error")
            return redirect(url_for("p1.new_project"))

        project = Project(
            client_id=client.id,
            platform=PlatformType.TECH_DEBT,
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
            details={"platform": "tech_debt", "name": name},
        )

        # Optionally link a capability list version via the picker (the
        # integrity chokepoint: freezes a snapshot; raises if AI-origin
        # without explicit acknowledgment).
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
                    # Project was created; user can finish linking from workspace.
                    return redirect(url_for("p1.workspace", project_id=project.id))
                raise
            except ValueError as e:
                flash(f"Could not link capability list: {e}", "error")
                return redirect(url_for("p1.workspace", project_id=project.id))

        flash(f"Created project '{name}'.", "info")
        return redirect(url_for("p1.workspace", project_id=project.id))

    return render_template(
        "p1/new_project.html",
        clients=clients, lists_by_client=lists_by_client, form={},
    )


# ----- project workspace -----

def _get_project_or_404(project_id: str) -> Project:
    p = db.session.get(Project, project_id)
    if p is None or p.platform != PlatformType.TECH_DEBT:
        abort(404)
    # v1.8: cross-client read protection. 404 (not 403) for callers
    # without access — see shield.spine.access.require_client_access.
    from ..spine.access import require_client_access
    require_client_access(p.client_id)
    return p


@bp.route("/project/<project_id>")
@login_required
def workspace(project_id: str):
    project = _get_project_or_404(project_id)
    artifacts = (
        db.session.query(Artifact)
        .filter_by(project_id=project.id)
        .order_by(Artifact.created_at.desc())
        .all()
    )
    human    = [a for a in artifacts if a.origin == Origin.HUMAN_INPUT]
    ai       = [a for a in artifacts if a.origin == Origin.AI_GENERATED]
    informed = [a for a in artifacts if a.origin == Origin.HUMAN_AI_INFORMED]
    # Whether the Project summary button should be enabled — only if
    # there's at least one overlap_analysis artifact to summarize.
    has_overlap = any(a.stage == "overlap_analysis" for a in ai)
    return render_template(
        "p1/workspace.html", project=project,
        human=human, ai=ai, informed=informed,
        has_overlap=has_overlap,
    )


@bp.route("/project/<project_id>/summary")
@login_required
def project_summary(project_id: str):
    """Executive-first summary of the project (mirror of P3's run_detail).

    Pulls the most recent overlap_analysis artifact, parses its JSON,
    surfaces the headline + counts + top three overlaps, then keeps
    the full overlap content (every group + shadow IT row) collapsible
    below. Falls through to a friendly empty state when no overlap
    has run yet.
    """
    project = _get_project_or_404(project_id)
    overlap_art = (
        db.session.query(Artifact)
        .filter_by(
            project_id=project.id,
            origin=Origin.AI_GENERATED,
            stage="overlap_analysis",
        )
        .order_by(Artifact.created_at.desc())
        .first()
    )
    overlap = None
    if overlap_art is not None and overlap_art.body_text:
        try:
            overlap = json.loads(overlap_art.body_text)
        except (ValueError, TypeError):
            overlap = None

    cap_list = project.capability_snapshot
    tools_count = len(cap_list.items) if cap_list is not None else 0

    return render_template(
        "p1/project_summary.html",
        project=project,
        overlap=overlap,
        overlap_art=overlap_art,
        tools_count=tools_count,
    )


@bp.route("/project/<project_id>/upload", methods=["POST"])
@login_required
@admin_only
def upload(project_id: str):
    project = _get_project_or_404(project_id)
    f = request.files.get("file")
    title = request.form.get("title") or (f.filename if f else "Untitled")
    if not f:
        flash("No file selected.", "error")
        return redirect(url_for("p1.workspace", project_id=project.id))
    write_human_artifact(
        project=project, stage="raw_intake", title=title,
        file_stream=f.stream, filename=f.filename, mime_type=f.mimetype,
        actor=current_user,
    )
    flash(f"Uploaded {title} to the human lane.", "info")
    return redirect(url_for("p1.workspace", project_id=project.id))


# ----- AI extraction -----

@bp.route("/project/<project_id>/extract", methods=["POST"])
@login_required
@admin_only
def extract(project_id: str):
    project = _get_project_or_404(project_id)
    src_id = request.form.get("artifact_id")
    src = db.session.get(Artifact, src_id) if src_id else None
    if src is None or src.project_id != project.id or src.origin != Origin.HUMAN_INPUT:
        flash("Pick a human-source artifact to extract from.", "error")
        return redirect(url_for("p1.workspace", project_id=project.id))

    from ..tasks import enqueue_ai, p1_extract_job
    job = enqueue_ai(p1_extract_job, project.id, src.id)
    flash("Automated extraction queued. We'll refresh as it runs …", "info")
    return redirect(url_for(
        "jobs.wait", job_id=job.id,
        next=url_for("p1.workspace", project_id=project.id),
    ))


# ----- Extraction review (the named human-authored-AI-informed artifact) -----

@bp.route("/project/<project_id>/review/<ai_artifact_id>", methods=["GET", "POST"])
@login_required
@admin_only
def review_extraction(project_id: str, ai_artifact_id: str):
    project = _get_project_or_404(project_id)
    src = db.session.get(Artifact, ai_artifact_id)
    if src is None or src.origin != Origin.AI_GENERATED:
        abort(404)
    if request.method == "POST":
        confirmed = request.form.get("confirmed_text", "")
        write_human_ai_informed_artifact(
            project=project, stage="extraction_review",
            title=f"Admin-confirmed extraction (from {src.title})",
            body_text=confirmed,
            cites_artifact_ids=[src.id],
            actor=current_user,
        )
        flash("Admin-confirmed extraction recorded.", "info")
        return redirect(url_for("p1.workspace", project_id=project.id))

    # Round-5 §6.2: the page used to be a giant JSON textarea. Now it's
    # a real table editor. Parse the AI body_text into a list of dicts
    # the partial can render; fall back to an empty list on malformed
    # input so the admin can still add rows manually.
    try:
        items = json.loads(src.body_text or "[]")
        if not isinstance(items, list):
            items = []
    except (ValueError, TypeError):
        items = []
    return render_template(
        "p1/review_extraction.html",
        project=project, src=src, items=items,
    )


# ----- AI overlap analysis -----

@bp.route("/project/<project_id>/overlap", methods=["POST"])
@login_required
@admin_only
def overlap(project_id: str):
    project = _get_project_or_404(project_id)
    confirmed_id = request.form.get("artifact_id")
    confirmed = db.session.get(Artifact, confirmed_id) if confirmed_id else None
    if confirmed is None or confirmed.origin != Origin.HUMAN_AI_INFORMED:
        flash("Run overlap on the admin-confirmed extraction, not the raw automated output.", "error")
        return redirect(url_for("p1.workspace", project_id=project.id))

    from ..tasks import enqueue_ai, p1_overlap_job
    job = enqueue_ai(p1_overlap_job, project.id, confirmed.id)
    flash("Overlap analysis queued. Refreshing as it runs …", "info")
    return redirect(url_for(
        "jobs.wait", job_id=job.id,
        next=url_for("p1.workspace", project_id=project.id),
    ))


# ----- Conversational interrogation (spec §8.1 stage 5) -----
#
# The findings from overlap analysis are a *workspace*, not a report.
# The admin queries them. Every exchange lands in the AI lane as scratch.
# Committing anything to the authoritative list is a separate, explicit
# act (see `commit_chat` below). The conversation must never silently
# leak into the final artifact.

def _find_latest(project: Project, origin: Origin, stage: str) -> Artifact | None:
    return (
        db.session.query(Artifact)
        .filter_by(project_id=project.id, origin=origin, stage=stage)
        .order_by(Artifact.created_at.desc())
        .first()
    )


@bp.route("/project/<project_id>/chat", methods=["POST"])
@login_required
@admin_only
def chat(project_id: str):
    project = _get_project_or_404(project_id)
    question = (request.form.get("question") or "").strip()
    if not question:
        flash("Type a question first.", "error")
        return redirect(url_for("p1.workspace", project_id=project.id))

    confirmed = _find_latest(project, Origin.HUMAN_AI_INFORMED, "extraction_review")
    overlap_art = _find_latest(project, Origin.AI_GENERATED, "overlap_analysis")
    if confirmed is None or overlap_art is None:
        flash(
            "The conversation needs an admin-confirmed extraction AND an "
            "AI overlap analysis to ground on. Run those stages first.",
            "error",
        )
        return redirect(url_for("p1.workspace", project_id=project.id))

    from ..tasks import enqueue_ai, p1_chat_job
    job = enqueue_ai(p1_chat_job, project.id, question)
    flash("Question queued. We'll refresh as the analysis runs …", "info")
    return redirect(url_for(
        "jobs.wait", job_id=job.id,
        next=url_for("p1.workspace", project_id=project.id) + "#chat",
    ))


@bp.route("/project/<project_id>/chat/<chat_artifact_id>/commit", methods=["POST"])
@login_required
@admin_only
def commit_chat(project_id: str, chat_artifact_id: str):
    """Explicitly commit a chat exchange to the admin-final list.

    Hard rule (spec §8.1): committing is a deliberate human act. The
    conversation MUST NOT silently leak into the authoritative artifact.
    This route is the only path for that promotion to happen.
    """
    project = _get_project_or_404(project_id)
    src = db.session.get(Artifact, chat_artifact_id)
    if (
        src is None
        or src.project_id != project.id
        or src.origin != Origin.AI_GENERATED
        or src.stage != "conversational_interrogation"
    ):
        abort(404)

    note = (request.form.get("note") or "").strip()
    if not note:
        flash("Add a short note describing why this is being committed.", "error")
        return redirect(url_for("p1.workspace", project_id=project.id) + "#chat")

    # The committed artifact is human-authored-AI-informed: it cites the
    # specific chat exchange and the admin's note about why it matters.
    body = (
        f"Committed from chat.\n\n"
        f"Admin note: {note}\n\n"
        f"--- Source AI exchange ---\n{src.body_text}"
    )
    write_human_ai_informed_artifact(
        project=project,
        stage="chat_commit",
        title=f"Commit: {src.title}",
        body_text=body,
        cites_artifact_ids=[src.id],
        actor=current_user,
    )
    flash("Committed to your reviewed version.", "info")
    return redirect(url_for("p1.workspace", project_id=project.id) + "#chat")


# ----- Admin-final reconciled list (spec §8.1 stage 6) -----
#
# The authoritative capability list. Human-authored, AI-informed. This is
# the engagement's headline output. Spec is firm: "All states are retained;
# they must never become visually interchangeable just because they contain
# similar rows, especially once cost figures are attached." This route
# therefore writes a NEW CapabilityList version (origin=HUMAN_AI_INFORMED)
# rather than mutating any earlier state.

@bp.route("/project/<project_id>/finalize", methods=["GET", "POST"])
@login_required
@admin_only
def finalize(project_id: str):
    project = _get_project_or_404(project_id)
    confirmed = _find_latest(project, Origin.HUMAN_AI_INFORMED, "extraction_review")
    if confirmed is None:
        flash(
            "Finalize requires an admin-confirmed extraction. Run the "
            "review stage first.",
            "error",
        )
        return redirect(url_for("p1.workspace", project_id=project.id))

    # Also surface chat-commit artifacts so the admin can fold them in.
    chat_commits = (
        db.session.query(Artifact)
        .filter_by(project_id=project.id, origin=Origin.HUMAN_AI_INFORMED, stage="chat_commit")
        .order_by(Artifact.created_at.desc())
        .all()
    )

    if request.method == "POST":
        items_json = (request.form.get("items_json") or "").strip()
        notes = (request.form.get("notes") or "").strip()

        try:
            items = json.loads(items_json)
        except json.JSONDecodeError as e:
            flash(f"Items JSON is malformed: {e}", "error")
            return render_template(
                "p1/finalize.html", project=project,
                confirmed=confirmed, chat_commits=chat_commits,
                items_json=items_json, notes=notes,
            )
        if not isinstance(items, list):
            flash("Items must be a JSON array.", "error")
            return render_template(
                "p1/finalize.html", project=project,
                confirmed=confirmed, chat_commits=chat_commits,
                items_json=items_json, notes=notes,
            )

        client = project.client
        existing_max = max(
            (cl.version for cl in client.capability_lists), default=0,
        )
        new_version = existing_max + 1

        new_cl = CapabilityList(
            client_id=client.id,
            version=new_version,
            label=f"Admin-final from {project.name}",
            origin=Origin.HUMAN_AI_INFORMED,
            notes=notes or None,
            created_by_id=current_user.id,
        )
        db.session.add(new_cl)
        db.session.flush()  # need new_cl.id for items

        for raw in items:
            if not isinstance(raw, dict) or not raw.get("name"):
                continue
            db.session.add(CapabilityListItem(
                capability_list_id=new_cl.id,
                name=str(raw.get("name", "")).strip(),
                vendor=str(raw.get("vendor", "") or "").strip() or None,
                category=str(raw.get("category", "") or "Uncategorized").strip(),
                function=str(raw.get("function", "") or "").strip() or None,
                annual_cost_usd=int(raw.get("annual_cost_usd") or 0),
                license_count=int(raw.get("license_count") or 0),
                notes=str(raw.get("notes", "") or "").strip() or None,
            ))
        db.session.commit()

        cited = [confirmed.id] + [c.id for c in chat_commits]
        write_human_ai_informed_artifact(
            project=project,
            stage="admin_final",
            title=f"Admin-final capability list v{new_version}",
            body_text=items_json,
            cites_artifact_ids=cited,
            actor=current_user,
            capability_list_version_id=new_cl.id,
        )

        # Project moves to "complete" once it has produced the headline output.
        project.stage = "complete"
        db.session.commit()

        log_audit(
            "p1.finalize",
            actor=current_user,
            target_type="capability_list",
            target_id=new_cl.id,
            project_id=project.id,
            client_id=client.id,
            details={
                "version": new_version,
                "items": db.session.query(CapabilityListItem)
                                   .filter_by(capability_list_id=new_cl.id).count(),
                "origin": "human_ai_informed",
            },
        )
        flash(
            f"Created admin-final capability list v{new_version}. "
            f"The earlier raw/AI/confirmed versions remain intact.",
            "info",
        )
        return redirect(url_for(
            "clients.capability_list_detail",
            client_id=client.id, list_id=new_cl.id,
        ))

    # Round-5 §6.3: render the confirmed extraction as a table the
    # admin can edit inline, not a raw JSON textarea. Server still
    # reads `items_json` from the form; the partial's JS serializes
    # the table back into that hidden field on submit.
    try:
        items = json.loads(confirmed.body_text or "[]")
        if not isinstance(items, list):
            items = []
    except (ValueError, TypeError):
        items = []
    return render_template(
        "p1/finalize.html", project=project,
        confirmed=confirmed, chat_commits=chat_commits,
        items=items, notes="",
    )


# ----- helpers -----

def _load_prompt(filename: str) -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / "ai" / "prompts" / filename).read_text(encoding="utf-8")
