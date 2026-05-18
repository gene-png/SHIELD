"""Archive / unarchive / purge — the admin's two-tier delete pattern.

Round-7 §17. The integrity model says the audit log is append-only and
artifact origin is immutable, so we can't actually DELETE rows. But
admins need an operational verb to make typo'd projects, accidentally
uploaded files, and abandoned engagements stop cluttering their view.

The two tiers:

  ARCHIVE  (soft-delete, default action)
    - Sets `archived=True` + stamps `archived_at`, `archived_by_id`,
      `archived_reason`.
    - Hidden from default views; reachable via "Show archived" toggle.
    - Reversible via /unarchive.
    - Use for: typo'd names, wrong client picked, test runs, abandoned
      engagements.

  PURGE    (hard-delete, high-friction)
    - Requires a typed confirmation phrase matching the row's name.
    - Removes the file from disk; sets `purged_at` + `purged_by_id` +
      `purged_reason`. Row stays so the audit log can still resolve it.
    - Use for: GDPR right-to-erasure, accidentally uploaded protected
      material, demo data cleanup.

All routes are admin-only. CLIENT and REVIEWER never reach them.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

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
from ..models import Artifact, Project
from .audit import log_audit
from .rbac import admin_only

bp = Blueprint("lifecycle", __name__)


# --------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------

@bp.route("/projects/<project_id>/archive", methods=["POST"])
@login_required
@admin_only
def project_archive(project_id: str):
    p = db.session.get(Project, project_id)
    if p is None or p.purged_at is not None:
        abort(404)
    if p.archived:
        flash("Project is already archived.", "info")
        return redirect(_post_archive_redirect(p))
    reason = (request.form.get("reason") or "").strip()
    p.archived = True
    p.archived_at = datetime.utcnow()
    p.archived_by_id = current_user.id
    p.archived_reason = reason or None
    db.session.commit()
    log_audit(
        "project.archived",
        actor=current_user,
        target_type="project", target_id=p.id,
        project_id=p.id, client_id=p.client_id,
        details={"reason": reason, "prior_state": "active"},
    )
    flash(f"Archived “{p.name}.”", "info")
    return redirect(_post_archive_redirect(p))


@bp.route("/projects/<project_id>/unarchive", methods=["POST"])
@login_required
@admin_only
def project_unarchive(project_id: str):
    p = db.session.get(Project, project_id)
    if p is None or p.purged_at is not None:
        abort(404)
    if not p.archived:
        flash("Project isn't archived.", "info")
        return redirect(url_for("clients.detail", client_id=p.client_id))
    p.archived = False
    p.archived_at = None
    p.archived_by_id = None
    p.archived_reason = None
    db.session.commit()
    log_audit(
        "project.unarchived",
        actor=current_user,
        target_type="project", target_id=p.id,
        project_id=p.id, client_id=p.client_id,
        details={"prior_state": "archived"},
    )
    flash(f"Restored “{p.name}.”", "info")
    return redirect(url_for("clients.detail", client_id=p.client_id))


@bp.route("/projects/<project_id>/purge", methods=["POST"])
@login_required
@admin_only
def project_purge(project_id: str):
    p = db.session.get(Project, project_id)
    if p is None:
        abort(404)
    if p.purged_at is not None:
        flash("Project is already purged.", "info")
        return redirect(url_for("clients.detail", client_id=p.client_id))
    typed = (request.form.get("confirmation_phrase") or "").strip()
    if typed != p.name:
        flash(
            "Type the project's exact name to confirm purge. Nothing was changed.",
            "error",
        )
        return redirect(url_for("clients.detail", client_id=p.client_id))
    reason = (request.form.get("reason") or "").strip()

    # Cascade-style behavior: every artifact under the project also
    # becomes a tombstone with its file removed.
    files_deleted = 0
    for art in list(p.artifacts):
        if art.purged_at is None:
            _purge_artifact_inplace(
                art, actor_id=current_user.id, reason=f"cascade from project purge: {reason}",
            )
            files_deleted += 1

    p.purged_at = datetime.utcnow()
    p.purged_by_id = current_user.id
    p.purged_reason = reason or None
    p.archived = True  # purged implies archived for view-filter purposes
    db.session.commit()
    log_audit(
        "project.purged",
        actor=current_user,
        target_type="project", target_id=p.id,
        project_id=p.id, client_id=p.client_id,
        details={
            "reason": reason,
            "files_deleted": files_deleted,
            "name_at_purge": p.name,
        },
    )
    flash(
        f"Purged “{p.name}” ({files_deleted} files removed from disk).",
        "info",
    )
    return redirect(url_for("clients.detail", client_id=p.client_id))


def _post_archive_redirect(p: Project) -> str:
    return url_for("clients.detail", client_id=p.client_id)


# --------------------------------------------------------------------
# Artifacts
# --------------------------------------------------------------------

@bp.route("/artifacts/<artifact_id>/archive", methods=["POST"])
@login_required
@admin_only
def artifact_archive(artifact_id: str):
    a = db.session.get(Artifact, artifact_id)
    if a is None or a.purged_at is not None:
        abort(404)
    if a.archived:
        flash("Already archived.", "info")
        return redirect(url_for("repository.artifact_detail", artifact_id=a.id))
    reason = (request.form.get("reason") or "").strip()
    a.archived = True
    a.archived_at = datetime.utcnow()
    a.archived_by_id = current_user.id
    a.archived_reason = reason or None
    db.session.commit()
    log_audit(
        "artifact.archived",
        actor=current_user,
        target_type="artifact", target_id=a.id,
        project_id=a.project_id, client_id=a.client_id,
        details={"reason": reason, "prior_state": "active"},
    )
    flash(f"Archived “{a.title}.”", "info")
    return redirect(url_for("repository.artifact_detail", artifact_id=a.id))


@bp.route("/artifacts/<artifact_id>/unarchive", methods=["POST"])
@login_required
@admin_only
def artifact_unarchive(artifact_id: str):
    a = db.session.get(Artifact, artifact_id)
    if a is None or a.purged_at is not None:
        abort(404)
    if not a.archived:
        flash("Not archived.", "info")
        return redirect(url_for("repository.artifact_detail", artifact_id=a.id))
    a.archived = False
    a.archived_at = None
    a.archived_by_id = None
    a.archived_reason = None
    db.session.commit()
    log_audit(
        "artifact.unarchived",
        actor=current_user,
        target_type="artifact", target_id=a.id,
        project_id=a.project_id, client_id=a.client_id,
        details={"prior_state": "archived"},
    )
    flash(f"Restored “{a.title}.”", "info")
    return redirect(url_for("repository.artifact_detail", artifact_id=a.id))


@bp.route("/artifacts/<artifact_id>/purge", methods=["POST"])
@login_required
@admin_only
def artifact_purge(artifact_id: str):
    a = db.session.get(Artifact, artifact_id)
    if a is None:
        abort(404)
    if a.purged_at is not None:
        flash("Already purged.", "info")
        return redirect(url_for("repository.artifact_detail", artifact_id=a.id))
    typed = (request.form.get("confirmation_phrase") or "").strip()
    if typed != a.title:
        flash(
            "Type the artifact's exact title to confirm purge. Nothing was changed.",
            "error",
        )
        return redirect(url_for("repository.artifact_detail", artifact_id=a.id))
    reason = (request.form.get("reason") or "").strip()
    _purge_artifact_inplace(a, actor_id=current_user.id, reason=reason)
    db.session.commit()
    log_audit(
        "artifact.purged",
        actor=current_user,
        target_type="artifact", target_id=a.id,
        project_id=a.project_id, client_id=a.client_id,
        details={"reason": reason, "title_at_purge": a.title,
                 "file_was_deleted": a.storage_key is None},
    )
    flash(f"Purged “{a.title}.”", "info")
    return redirect(url_for("repository.artifact_detail", artifact_id=a.id))


def _purge_artifact_inplace(a: Artifact, *, actor_id: str, reason: str) -> None:
    """Stamp tombstone columns and remove the file on disk.

    Callable from artifact_purge directly and from project_purge for
    cascading. Does NOT commit — the caller decides the transaction
    boundary. body_text is preserved so the audit trail can still cite
    what was being purged; storage_key is cleared once the file is
    gone so a future re-read can't pretend the file's still there.
    """
    a.archived = True  # purged implies archived for view filters
    a.archived_at = a.archived_at or datetime.utcnow()
    a.archived_by_id = a.archived_by_id or actor_id
    a.purged_at = datetime.utcnow()
    a.purged_by_id = actor_id
    a.purged_reason = reason or None

    if a.storage_key:
        try:
            p = Path(a.storage_key)
            if p.exists() and p.is_file():
                p.unlink()
        except OSError:
            # File system removal failed (already gone, permission, etc.).
            # We still tombstone the row — the audit row will reflect
            # that the file_was_deleted flag is False.
            pass
        else:
            a.storage_key = None
