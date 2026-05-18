"""Round-7 §19: workflow auto-progression hooks.

Less admin clicking on the happy path. When the user saves an upstream
artifact (a source upload, a reviewed extraction, a locked ZT
questionnaire), the system auto-queues the next analytic step instead
of waiting for the admin to press another button.

Editorial checkpoints stay manual:
  - Admin's confirmation of an AI extraction (the "Save approved
    extraction" press)
  - Admin's approval of the admin-final capability list
  - Client's "Submit final answers" on the ZT questionnaire
  - Promoting an AI artifact for reuse
  - Marking an artifact as a Deliverable

This module is the dispatch surface. Each platform's routes call into
`maybe_auto_progress_p1_after_*` after the editorial save. The hook
checks the actor's `auto_progress_workflows` flag and the necessary
preconditions, then enqueues the job with details.triggered_by =
'auto_progress' so the audit trail is honest about who fired it.
"""
from __future__ import annotations

from typing import Any

from flask_login import current_user

from ..extensions import db
from ..models import Artifact, Origin, Project, User
from .audit import log_audit


def should_auto_progress(actor: User | None = None) -> bool:
    """Per-user opt-out. NULL/missing/True all mean 'auto-progress on';
    only an explicit False disables the hooks."""
    actor = actor or current_user
    if not getattr(actor, "is_authenticated", False):
        return False
    flag = getattr(actor, "auto_progress_workflows", True)
    return bool(flag if flag is not None else True)


def _audit_auto_progress(
    action: str, project: Project, *, actor: User | None,
    source_artifact_id: str | None = None,
) -> None:
    """Drop an audit row marking that auto-progression fired. The
    eventual job will write its own audit row when it completes; this
    one is the marker that a human action triggered the auto-step (so
    the trail is honest about what was 'manual' vs. 'auto')."""
    log_audit(
        action,
        actor=actor or (current_user if getattr(current_user, "is_authenticated", False) else None),
        target_type="project", target_id=project.id,
        project_id=project.id, client_id=project.client_id,
        details={
            "triggered_by": "auto_progress",
            "source_artifact_id": source_artifact_id,
        },
    )


def maybe_auto_progress_p1_after_upload(
    project: Project, source: Artifact, *, actor: User | None = None,
) -> Any | None:
    """After an admin uploads a source file to a Tech Debt project,
    auto-queue p1.extract_job against it.

    Returns the queued job (so a route can chain a redirect to
    jobs.wait if it wants), or None if the hook was a no-op.
    """
    if not should_auto_progress(actor):
        return None
    if source.origin != Origin.HUMAN_INPUT:
        return None
    from ..tasks import enqueue_ai, p1_extract_job
    job = enqueue_ai(p1_extract_job, project.id, source.id)
    _audit_auto_progress(
        "auto_progress.p1_extract_queued",
        project, actor=actor, source_artifact_id=source.id,
    )
    return job


def maybe_auto_progress_p1_after_review(
    project: Project, review: Artifact, *, actor: User | None = None,
) -> Any | None:
    """After an admin saves a reviewed extraction, auto-queue overlap
    analysis against it."""
    if not should_auto_progress(actor):
        return None
    if review.origin != Origin.HUMAN_AI_INFORMED or review.stage != "extraction_review":
        return None
    from ..tasks import enqueue_ai, p1_overlap_job
    job = enqueue_ai(p1_overlap_job, project.id, review.id)
    _audit_auto_progress(
        "auto_progress.p1_overlap_queued",
        project, actor=actor, source_artifact_id=review.id,
    )
    return job


def maybe_auto_progress_p2_after_submit(
    project: Project, *, actor: User | None = None,
) -> Any | None:
    """After the client (or admin-on-behalf) submits + locks the Zero
    Trust questionnaire, auto-queue the current-state assessment."""
    if not should_auto_progress(actor):
        return None
    if project.stage != "submitted":
        return None
    from ..tasks import enqueue_ai, p2_analyze_job
    job = enqueue_ai(p2_analyze_job, project.id)
    _audit_auto_progress(
        "auto_progress.p2_assessment_queued",
        project, actor=actor,
    )
    return job


def maybe_auto_progress_p2_after_target(
    project: Project, *, actor: User | None = None,
) -> Any | None:
    """When both a current-state assessment AND a desired-future-state
    target exist, auto-queue the transition roadmap."""
    if not should_auto_progress(actor):
        return None
    current = (
        db.session.query(Artifact)
        .filter_by(project_id=project.id,
                   origin=Origin.AI_GENERATED,
                   stage="current_state_assessment")
        .order_by(Artifact.created_at.desc())
        .first()
    )
    target = (
        db.session.query(Artifact)
        .filter_by(project_id=project.id,
                   origin=Origin.HUMAN_INPUT,
                   stage="desired_future_state")
        .order_by(Artifact.created_at.desc())
        .first()
    )
    if not (current and target):
        return None
    from ..tasks import enqueue_ai, p2_roadmap_job
    job = enqueue_ai(p2_roadmap_job, project.id)
    _audit_auto_progress(
        "auto_progress.p2_roadmap_queued",
        project, actor=actor,
    )
    return job
