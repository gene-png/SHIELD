"""Schema-invariant tests for the v1.8 client-portal model additions.

These don't exercise the routes (those land in later PRs). They lock
in the contract for the new tables so future migrations can't quietly
drift:

  - Artifact.client_id is required and auto-set by the writers.
  - ClientMembership rejects duplicate (client_id, user_id).
  - ReviewerAssignment rejects duplicate (reviewer_id, client_id).
  - Notification + Deliverable + Message instantiate cleanly.

The integrity model (Origin immutability, audit append-only) is
already covered by tests/test_integrity_model.py — this file is
specifically about the v1.8 additions.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from shield.extensions import db
from shield.models import (
    Client,
    ClientInvitation,
    ClientMembership,
    Deliverable,
    Message,
    Notification,
    PlatformType,
    Project,
    ReviewerAssignment,
    Role,
    User,
)
from shield.spine.repository import write_human_artifact

# --------------------------------------------------------------------
# Artifact.client_id wiring
# --------------------------------------------------------------------

def test_write_human_artifact_sets_client_id_from_project(admin, acme):
    project = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="td", stage="intake", created_by_id=admin.id,
    )
    db.session.add(project)
    db.session.commit()

    art = write_human_artifact(
        project=project, stage="raw_intake",
        title="t", file_stream=None, filename=None, mime_type=None,
        actor=admin, body_text="hello",
    )
    assert art.client_id == acme.id, (
        "write_human_artifact must denormalize project.client_id onto the "
        "artifact so per-client scoping queries don't need a Project join"
    )


# --------------------------------------------------------------------
# ClientMembership
# --------------------------------------------------------------------

def test_client_membership_rejects_duplicates(app, acme, admin):
    # A separate CLIENT-role user we can membership.
    u = User(sub="t-c", email="t-c@example.com",
             display_name="Test Client", role=Role.CLIENT)
    db.session.add(u)
    db.session.commit()

    db.session.add(ClientMembership(
        client_id=acme.id, user_id=u.id,
        membership_role="member", invited_at=datetime.utcnow(),
    ))
    db.session.commit()

    db.session.add(ClientMembership(
        client_id=acme.id, user_id=u.id,
        membership_role="primary_poc", invited_at=datetime.utcnow(),
    ))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_client_invitation_token_hash_is_unique(app, acme, admin):
    inv1 = ClientInvitation(
        client_id=acme.id, invited_by=admin.id,
        email="x@example.com", token_hash="dup-hash",
        expires_at=datetime.utcnow() + timedelta(days=7),
    )
    inv2 = ClientInvitation(
        client_id=acme.id, invited_by=admin.id,
        email="y@example.com", token_hash="dup-hash",
        expires_at=datetime.utcnow() + timedelta(days=7),
    )
    db.session.add(inv1)
    db.session.commit()
    db.session.add(inv2)
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


# --------------------------------------------------------------------
# ReviewerAssignment
# --------------------------------------------------------------------

def test_reviewer_assignment_rejects_duplicate_pair(app, acme, admin):
    r = User(sub="t-r", email="t-r@example.com",
             display_name="Test Reviewer", role=Role.REVIEWER)
    db.session.add(r)
    db.session.commit()

    db.session.add(ReviewerAssignment(
        reviewer_id=r.id, client_id=acme.id,
        assigned_by_id=admin.id, assigned_at=datetime.utcnow(),
    ))
    db.session.commit()

    db.session.add(ReviewerAssignment(
        reviewer_id=r.id, client_id=acme.id,
        assigned_by_id=admin.id, assigned_at=datetime.utcnow(),
    ))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


# --------------------------------------------------------------------
# Other tables instantiate cleanly
# --------------------------------------------------------------------

def test_message_thread_minimal_row(app, acme, admin):
    db.session.add(Message(
        client_id=acme.id, project_id=None,
        author_id=admin.id, body="hi",
    ))
    db.session.commit()
    row = db.session.query(Message).filter_by(client_id=acme.id).first()
    assert row is not None
    assert row.read_at_map == {}


def test_notification_minimal_row(app, acme, admin):
    db.session.add(Notification(
        user_id=admin.id, client_id=acme.id,
        event_type="intake_completed",
        title="Intake completed", body=None,
        link=f"/clients/{acme.id}/intake",
    ))
    db.session.commit()


def test_deliverable_links_to_artifact(admin, acme):
    project = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="td", stage="intake", created_by_id=admin.id,
    )
    db.session.add(project)
    db.session.commit()

    art = write_human_artifact(
        project=project, stage="admin_final",
        title="The capability list",
        file_stream=None, filename=None, mime_type=None,
        actor=admin, body_text="[]",
    )

    d = Deliverable(
        client_id=acme.id, project_id=project.id, artifact_id=art.id,
        title="Capability list v1", summary="Initial baseline",
        finalized_by=admin.id,
    )
    db.session.add(d)
    db.session.commit()
    assert d.artifact_id == art.id
    assert d.superseded_at is None


# --------------------------------------------------------------------
# Client intake-metadata columns
# --------------------------------------------------------------------

def test_client_intake_metadata_defaults(app):
    c = Client(name="New Lead Corp")
    db.session.add(c)
    db.session.commit()
    assert c.service_interests == []
    assert c.compliance_frameworks == []
    assert c.consult_requested is False
    assert c.intake_completed_at is None
