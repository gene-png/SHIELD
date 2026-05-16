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
from sqlalchemy import select

from . import bp
from ..ai.client import AIClient, AIError
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
from ..spine.repository import (
    write_ai_artifact,
    write_human_ai_informed_artifact,
    write_human_artifact,
)


# ----- module landing -----

@bp.route("/")
@login_required
def index():
    projects = (
        db.session.query(Project)
        .filter_by(platform=PlatformType.TECH_DEBT, archived=False)
        .order_by(Project.created_at.desc())
        .all()
    )
    return render_template("p1/index.html", projects=projects)


# ----- project workspace -----

def _get_project_or_404(project_id: str) -> Project:
    p = db.session.get(Project, project_id)
    if p is None or p.platform != PlatformType.TECH_DEBT:
        abort(404)
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
    return render_template(
        "p1/workspace.html", project=project,
        human=human, ai=ai, informed=informed,
    )


@bp.route("/project/<project_id>/upload", methods=["POST"])
@login_required
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
def extract(project_id: str):
    project = _get_project_or_404(project_id)
    src_id = request.form.get("artifact_id")
    src = db.session.get(Artifact, src_id) if src_id else None
    if src is None or src.project_id != project.id or src.origin != Origin.HUMAN_INPUT:
        flash("Pick a human-source artifact to extract from.", "error")
        return redirect(url_for("p1.workspace", project_id=project.id))

    # Read source content. For binary, body_text may be empty — we degrade
    # gracefully by saying "no readable text" so this stays functional in v0.1.
    source_text = src.body_text or "(binary content; extraction stub returned)"

    try:
        ai = AIClient()
        text, lineage = ai.complete(
            system=_load_prompt("p1_extraction.md"),
            user=source_text[:200_000],
            prompt_version="p1_extraction.v1",
            json_response=True,
        )
    except AIError as e:
        flash(f"AI extraction failed: {e}", "error")
        return redirect(url_for("p1.workspace", project_id=project.id))

    art = write_ai_artifact(
        project=project, stage="ai_extraction",
        title=f"AI extraction of {src.title}",
        body_text=text,
        input_artifact_ids=[src.id],
        prompt_version=lineage["prompt_version"],
        model=lineage["model"],
        additional_lineage=lineage,
    )
    flash("AI extraction landed in the AI lane.", "info")
    return redirect(url_for("p1.workspace", project_id=project.id) + f"#a-{art.id}")


# ----- Extraction review (the named human-authored-AI-informed artifact) -----

@bp.route("/project/<project_id>/review/<ai_artifact_id>", methods=["GET", "POST"])
@login_required
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
    return render_template("p1/review_extraction.html", project=project, src=src)


# ----- AI overlap analysis -----

@bp.route("/project/<project_id>/overlap", methods=["POST"])
@login_required
def overlap(project_id: str):
    project = _get_project_or_404(project_id)
    confirmed_id = request.form.get("artifact_id")
    confirmed = db.session.get(Artifact, confirmed_id) if confirmed_id else None
    if confirmed is None or confirmed.origin != Origin.HUMAN_AI_INFORMED:
        flash("Run overlap on the admin-confirmed extraction, not the raw AI output.", "error")
        return redirect(url_for("p1.workspace", project_id=project.id))

    try:
        ai = AIClient()
        text, lineage = ai.complete(
            system=_load_prompt("p1_overlap.md"),
            user=confirmed.body_text or "[]",
            prompt_version="p1_overlap.v1",
            json_response=True,
        )
    except AIError as e:
        flash(f"AI overlap analysis failed: {e}", "error")
        return redirect(url_for("p1.workspace", project_id=project.id))

    write_ai_artifact(
        project=project, stage="overlap_analysis",
        title="AI overlap analysis",
        body_text=text,
        input_artifact_ids=[confirmed.id],
        prompt_version=lineage["prompt_version"],
        model=lineage["model"],
        additional_lineage=lineage,
    )
    flash("Overlap analysis landed in the AI lane.", "info")
    return redirect(url_for("p1.workspace", project_id=project.id))


# ----- helpers -----

def _load_prompt(filename: str) -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / "ai" / "prompts" / filename).read_text(encoding="utf-8")
