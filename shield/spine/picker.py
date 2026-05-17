"""The client-scoped artifact picker — the INTEGRITY CHOKEPOINT.

From the unified-portal spec §7.2:
    "Selecting a cross-project artifact takes a frozen, version-stamped
     snapshot at the moment of linking, never a live reference."
    "AI-origin items in the picker are visually unmistakable and gated
     by the reuse-moment acknowledgment."

This module produces the candidate set; the template renders the gate.
"""
from __future__ import annotations

from sqlalchemy import select

from ..extensions import db
from ..models import (
    Artifact,
    CapabilityList,
    Origin,
    Project,
    ReuseStatus,
)
from .audit import log_audit


def list_capability_lists_for_client(client_id: str) -> list[CapabilityList]:
    """Capability-list versions belonging to this client, newest first."""
    return list(
        db.session.scalars(
            select(CapabilityList)
            .where(CapabilityList.client_id == client_id)
            .order_by(CapabilityList.version.desc())
        )
    )


def list_reusable_artifacts_for_client(
    client_id: str,
    *,
    include_ai: bool = True,
) -> list[Artifact]:
    """Artifacts in this client's projects that are eligible for reuse.

    Eligible means: human-input source, OR an AI artifact that has been
    promoted (reuse_status == APPROVED), OR a human-authored-AI-informed
    synthesis. Drafts of AI output are NOT reusable.
    """
    stmt = (
        select(Artifact)
        .join(Project, Project.id == Artifact.project_id)
        .where(Project.client_id == client_id, Project.archived.is_(False))
    )
    arts = list(db.session.scalars(stmt))
    out: list[Artifact] = []
    for a in arts:
        if a.origin == Origin.HUMAN_INPUT:
            out.append(a)
        elif a.origin == Origin.HUMAN_AI_INFORMED:
            out.append(a)
        elif include_ai and a.origin == Origin.AI_GENERATED and a.reuse_status == ReuseStatus.APPROVED:
            out.append(a)
    return out


def link_capability_list_to_project(
    *,
    project: Project,
    capability_list_version_id: str,
    actor,
    acknowledged_ai_reuse: bool = False,
) -> None:
    """Snapshot-link a capability list version to a project.

    Per Decision #2: this is a frozen snapshot at the moment of linking.
    If the underlying list later changes, the project still references
    the version stamped here.
    """
    cl: CapabilityList | None = db.session.get(CapabilityList, capability_list_version_id)
    if cl is None or cl.client_id != project.client_id:
        raise ValueError("That capability list does not belong to this client.")

    if cl.origin == Origin.AI_GENERATED and not acknowledged_ai_reuse:
        raise PermissionError("AI_REUSE_ACK_REQUIRED")

    project.capability_list_version_id = cl.id
    db.session.commit()

    log_audit(
        "project.link_capability_list",
        actor=actor,
        target_type="project",
        target_id=project.id,
        project_id=project.id,
        client_id=project.client_id,
        details={
            "capability_list_id": cl.id,
            "version": cl.version,
            "origin": cl.origin.value,
            "acknowledged_ai_reuse": bool(acknowledged_ai_reuse and cl.origin == Origin.AI_GENERATED),
        },
    )
