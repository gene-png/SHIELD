"""Append-only audit log.

Every state change, promotion, attribution, and reuse must produce one
audit entry. The DB enforces append-only via a trigger; this helper is
the one Pythonic write path.
"""
from __future__ import annotations

from typing import Any

from ..extensions import db
from ..models import AuditEntry, User


def log_audit(
    action: str,
    *,
    actor: User | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    project_id: str | None = None,
    client_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEntry:
    entry = AuditEntry(
        action=action,
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
        target_type=target_type,
        target_id=target_id,
        project_id=project_id,
        client_id=client_id,
        details=details or {},
    )
    db.session.add(entry)
    db.session.commit()
    return entry
