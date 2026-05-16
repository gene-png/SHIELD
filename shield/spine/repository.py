"""The repository writer — the chokepoint for ALL artifact writes.

There are exactly two write methods:
  - write_human_artifact(...)
  - write_ai_artifact(...)

The AI lane is therefore physically separate at the call site: a request
handler that has a `User` cannot reach `write_ai_artifact` without
explicitly going through an AI processing step.

Origin is set here, on creation. The DB trigger then ensures origin
cannot be mutated by any later UPDATE.
"""
from __future__ import annotations

import hashlib
import io
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

from flask import current_app

from ..extensions import db
from ..models import (
    Artifact,
    Origin,
    Project,
    ReuseStatus,
    Role,
    TrustTier,
    User,
)
from .audit import log_audit


# --------------------------------------------------------------------
# Storage backend
# --------------------------------------------------------------------

def _storage_root() -> Path:
    root = Path(current_app.config["ARTIFACT_STORAGE_DIR"])
    root.mkdir(parents=True, exist_ok=True)
    return root


def _save_blob(stream: BinaryIO, project_id: str, lane: str, filename: str) -> tuple[str, int, str]:
    """Persist a binary stream. Returns (storage_key, size_bytes, sha256_hex)."""
    base = _storage_root() / project_id / lane
    base.mkdir(parents=True, exist_ok=True)
    safe_name = secrets.token_hex(8) + "_" + os.path.basename(filename)
    target = base / safe_name
    h = hashlib.sha256()
    size = 0
    with open(target, "wb") as f:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            f.write(chunk)
            h.update(chunk)
            size += len(chunk)
    return str(target.relative_to(_storage_root())), size, h.hexdigest()


# --------------------------------------------------------------------
# Public writers
# --------------------------------------------------------------------

def write_human_artifact(
    *,
    project: Project,
    stage: str,
    title: str,
    file_stream: BinaryIO | None,
    filename: str | None,
    mime_type: str | None,
    actor: User,
    trust_tier: TrustTier = TrustTier.NOT_APPLICABLE,
    body_text: str | None = None,
    capability_list_version_id: str | None = None,
) -> Artifact:
    """Write a human-input artifact.

    Hard rule (spec §7.1): this method, and only this method, can land
    files in the human lane. It physically cannot write origin=AI.
    """
    storage_key: str | None = None
    size_bytes: int | None = None
    sha256_hex: str | None = None
    if file_stream is not None and filename is not None:
        storage_key, size_bytes, sha256_hex = _save_blob(file_stream, project.id, "human", filename)

    art = Artifact(
        project_id=project.id,
        stage=stage,
        origin=Origin.HUMAN_INPUT,
        trust_tier=trust_tier,
        reuse_status=ReuseStatus.DRAFT,
        title=title,
        filename=filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
        storage_key=storage_key,
        body_text=body_text,
        lineage={"sha256": sha256_hex} if sha256_hex else {},
        actor_id=actor.id,
        actor_role=actor.role,
        capability_list_version_id=capability_list_version_id,
    )
    db.session.add(art)
    db.session.commit()

    log_audit(
        "artifact.write_human",
        actor=actor,
        target_type="artifact",
        target_id=art.id,
        project_id=project.id,
        client_id=project.client_id,
        details={"stage": stage, "trust_tier": trust_tier.value, "sha256": sha256_hex},
    )
    return art


def write_ai_artifact(
    *,
    project: Project,
    stage: str,
    title: str,
    body_text: str,
    input_artifact_ids: list[str],
    prompt_version: str,
    model: str,
    capability_list_version_id: str | None = None,
    additional_lineage: dict[str, Any] | None = None,
) -> Artifact:
    """Write an AI-generated artifact.

    No `actor: User` parameter — by design. An AI artifact has no human
    author. It is automatically labeled origin=AI_GENERATED, lands in
    the AI lane, and records lineage to its inputs.
    """
    lineage: dict[str, Any] = {
        "input_artifacts": input_artifact_ids,
        "prompt_version": prompt_version,
        "model": model,
        "produced_at": datetime.utcnow().isoformat() + "Z",
    }
    if additional_lineage:
        lineage.update(additional_lineage)

    art = Artifact(
        project_id=project.id,
        stage=stage,
        origin=Origin.AI_GENERATED,
        trust_tier=TrustTier.NOT_APPLICABLE,
        reuse_status=ReuseStatus.DRAFT,
        title=title,
        body_text=body_text,
        lineage=lineage,
        capability_list_version_id=capability_list_version_id,
    )
    db.session.add(art)
    db.session.commit()

    log_audit(
        "artifact.write_ai",
        target_type="artifact",
        target_id=art.id,
        project_id=project.id,
        client_id=project.client_id,
        details={"stage": stage, "model": model, "inputs": input_artifact_ids},
    )
    return art


def write_human_ai_informed_artifact(
    *,
    project: Project,
    stage: str,
    title: str,
    body_text: str,
    cites_artifact_ids: list[str],
    actor: User,
    capability_list_version_id: str | None = None,
) -> Artifact:
    """Write a human-authored, AI-informed synthesis (third origin).

    Used by Platform 1's admin-final list and Platform 2's roadmap.
    """
    art = Artifact(
        project_id=project.id,
        stage=stage,
        origin=Origin.HUMAN_AI_INFORMED,
        trust_tier=TrustTier.NOT_APPLICABLE,
        reuse_status=ReuseStatus.DRAFT,
        title=title,
        body_text=body_text,
        lineage={"cites": cites_artifact_ids},
        actor_id=actor.id,
        actor_role=actor.role,
        capability_list_version_id=capability_list_version_id,
    )
    db.session.add(art)
    db.session.commit()

    log_audit(
        "artifact.write_human_ai_informed",
        actor=actor,
        target_type="artifact",
        target_id=art.id,
        project_id=project.id,
        client_id=project.client_id,
        details={"stage": stage, "cites": cites_artifact_ids},
    )
    return art


def promote_artifact(*, artifact: Artifact, actor: User, reason: str) -> Artifact:
    """Promote an AI artifact to APPROVED.

    Only AI-origin artifacts can be promoted. Origin is NOT changed.
    """
    if artifact.origin != Origin.AI_GENERATED:
        raise ValueError("Only AI_GENERATED artifacts can be promoted.")
    if actor.role not in (Role.ADMIN, Role.REVIEWER):
        raise PermissionError("Only admins or reviewers can promote artifacts.")
    if not reason or not reason.strip():
        raise ValueError("A promotion reason is required.")

    artifact.reuse_status = ReuseStatus.APPROVED
    artifact.promoted_at = datetime.utcnow()
    artifact.promoted_by_id = actor.id
    artifact.promotion_reason = reason.strip()
    db.session.commit()

    log_audit(
        "artifact.promote",
        actor=actor,
        target_type="artifact",
        target_id=artifact.id,
        project_id=artifact.project_id,
        details={"reason": reason.strip()},
    )
    return artifact
