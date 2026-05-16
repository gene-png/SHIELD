"""Repository browser — the one global, read-only, cross-module view."""
from __future__ import annotations

from flask import Blueprint, render_template, request
from flask_login import login_required
from sqlalchemy import select

from ..extensions import db
from ..models import Artifact, Origin, Project

bp = Blueprint("repository", __name__, template_folder="../templates/repository")


@bp.route("/")
@login_required
def browse():
    origin_filter = request.args.get("origin", "all")
    stmt = select(Artifact).order_by(Artifact.created_at.desc()).limit(200)
    if origin_filter != "all":
        try:
            stmt = stmt.where(Artifact.origin == Origin(origin_filter))
        except ValueError:
            pass
    artifacts = list(db.session.scalars(stmt))
    return render_template(
        "repository/browse.html",
        artifacts=artifacts,
        origin_filter=origin_filter,
    )


@bp.route("/artifact/<artifact_id>")
@login_required
def artifact_detail(artifact_id: str):
    art = db.session.get(Artifact, artifact_id)
    if art is None:
        return ("Not found", 404)
    project = db.session.get(Project, art.project_id)
    return render_template("repository/artifact_detail.html", art=art, project=project)
