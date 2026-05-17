"""Tests for the v1.8 admin surfaces (PR 5).

Covers:
  - / (home) redirects ADMIN to /clients/queue
  - /clients/queue bucketing (new leads / waiting / active)
  - /clients/<id>/intake renders the client's submitted metadata
  - /clients/<id>/adopt-artifact/<artifact_id> moves the file
  - /projects/<id>/finalize-artifact/<artifact_id> creates a Deliverable
  - /admin/messages/ inbox + /admin/messages/<cid>/<thread> thread
"""
from __future__ import annotations

from datetime import datetime

import pytest

from shield.extensions import db
from shield.models import (
    AuditEntry,
    Client,
    ClientMembership,
    Deliverable,
    Message,
    Origin,
    PlatformType,
    Project,
    Role,
    User,
)
from shield.spine.repository import write_human_artifact

# --------------------------------------------------------------------
# Home redirect
# --------------------------------------------------------------------

def test_admin_home_redirects_to_queue(admin_client):
    r = admin_client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/clients/queue")


# --------------------------------------------------------------------
# /clients/queue bucketing
# --------------------------------------------------------------------

def test_queue_buckets_clients_correctly(admin_client, acme, admin, app):
    # Beta: new lead (intake_completed_at NULL by default)
    beta = Client(name="Beta")
    db.session.add(beta)
    db.session.commit()

    # Gamma: intake complete, has service interests, no project yet
    gamma = Client(name="Gamma")
    gamma.service_interests = ["tech_debt"]
    gamma.intake_completed_at = datetime.utcnow()
    db.session.add(gamma)
    db.session.commit()

    # Acme: intake complete, has project, so falls in "active"
    acme.intake_completed_at = datetime.utcnow()
    acme.service_interests = ["tech_debt"]
    p = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="Acme TD", stage="intake", created_by_id=admin.id,
    )
    db.session.add(p)
    db.session.commit()

    r = admin_client.get("/clients/queue")
    assert r.status_code == 200
    assert b"Beta" in r.data
    assert b"Gamma" in r.data
    assert b"Acme" in r.data


def test_queue_requires_admin(reviewer_client):
    r = reviewer_client.get("/clients/queue", follow_redirects=False)
    # admin_only sends non-admins to 403/redirect — either way, not 200.
    assert r.status_code in (302, 403, 404)


# --------------------------------------------------------------------
# /clients/<id>/intake
# --------------------------------------------------------------------

def test_intake_view_renders_client_metadata(admin_client, acme):
    acme.legal_name = "Acme Industries Inc."
    acme.primary_poc_name = "Alice"
    acme.prompting_context = "Audit due Q3."
    db.session.commit()
    r = admin_client.get(f"/clients/{acme.id}/intake")
    assert r.status_code == 200
    assert b"Acme Industries Inc." in r.data
    assert b"Alice" in r.data
    assert b"Audit due Q3." in r.data


# --------------------------------------------------------------------
# Adopt-artifact
# --------------------------------------------------------------------

@pytest.fixture()
def acme_repo_with_file(admin, acme):
    """Synthetic repository project + one uploaded artifact in it."""
    repo = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="Client Repository", stage="client_repository",
        is_client_repository=True, created_by_id=admin.id,
    )
    db.session.add(repo)
    db.session.commit()
    art = write_human_artifact(
        project=repo, stage="client_repository",
        title="inventory.xlsx",
        file_stream=None, filename=None, mime_type=None,
        actor=admin, body_text="[]",
    )
    return repo, art


def test_adopt_artifact_moves_file_to_real_project(
    admin_client, acme, admin, acme_repo_with_file,
):
    repo, art = acme_repo_with_file
    target = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="Acme TD", stage="intake", created_by_id=admin.id,
    )
    db.session.add(target)
    db.session.commit()

    r = admin_client.post(
        f"/clients/{acme.id}/adopt-artifact/{art.id}",
        data={"project_id": target.id},
        follow_redirects=False,
    )
    assert r.status_code == 302

    refreshed = db.session.get(type(art), art.id)
    assert refreshed.project_id == target.id
    assert refreshed.client_id == acme.id   # client_id never moves
    assert refreshed.origin == Origin.HUMAN_INPUT  # origin immutable


def test_adopt_artifact_writes_audit(admin_client, acme, admin, acme_repo_with_file):
    _, art = acme_repo_with_file
    target = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="Acme TD", stage="intake", created_by_id=admin.id,
    )
    db.session.add(target)
    db.session.commit()

    before = db.session.query(AuditEntry).filter_by(
        action="artifact.adopted_into_project",
    ).count()
    admin_client.post(
        f"/clients/{acme.id}/adopt-artifact/{art.id}",
        data={"project_id": target.id},
    )
    assert db.session.query(AuditEntry).filter_by(
        action="artifact.adopted_into_project",
    ).count() == before + 1


def test_adopt_artifact_rejects_cross_client_target(
    admin_client, acme, admin, acme_repo_with_file,
):
    """The form's project_id must belong to the same client. Refusing
    cross-client adoption keeps client_id consistent with project."""
    _, art = acme_repo_with_file
    beta = Client(name="Beta")
    db.session.add(beta)
    db.session.commit()
    beta_p = Project(
        client_id=beta.id, platform=PlatformType.TECH_DEBT,
        name="Beta TD", stage="intake", created_by_id=admin.id,
    )
    db.session.add(beta_p)
    db.session.commit()

    r = admin_client.post(
        f"/clients/{acme.id}/adopt-artifact/{art.id}",
        data={"project_id": beta_p.id},
        follow_redirects=False,
    )
    assert r.status_code == 302   # flash + redirect, not a hard error
    refreshed = db.session.get(type(art), art.id)
    # Did NOT move — still in the synthetic repo.
    assert refreshed.project_id != beta_p.id


# --------------------------------------------------------------------
# Finalize artifact as Deliverable
# --------------------------------------------------------------------

def test_finalize_artifact_creates_deliverable(admin_client, acme, admin):
    project = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="Acme TD", stage="overlap_analysis", created_by_id=admin.id,
    )
    db.session.add(project)
    db.session.commit()
    art = write_human_artifact(
        project=project, stage="admin_final",
        title="Capability list v1",
        file_stream=None, filename=None, mime_type=None,
        actor=admin, body_text="[]",
    )

    r = admin_client.post(
        f"/projects/{project.id}/finalize-artifact/{art.id}",
        data={"title": "Final Tech Debt report", "summary": "TLDR"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    d = (
        db.session.query(Deliverable)
        .filter_by(client_id=acme.id, artifact_id=art.id)
        .first()
    )
    assert d is not None
    assert d.title == "Final Tech Debt report"
    assert d.superseded_at is None


def test_finalize_supersedes_previous(admin_client, acme, admin):
    project = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="Acme TD", stage="overlap_analysis", created_by_id=admin.id,
    )
    db.session.add(project)
    db.session.commit()
    art = write_human_artifact(
        project=project, stage="admin_final",
        title="L", file_stream=None, filename=None, mime_type=None,
        actor=admin, body_text="[]",
    )

    admin_client.post(
        f"/projects/{project.id}/finalize-artifact/{art.id}",
        data={"title": "First", "summary": "a"},
    )
    admin_client.post(
        f"/projects/{project.id}/finalize-artifact/{art.id}",
        data={"title": "Second", "summary": "b"},
    )

    rows = (
        db.session.query(Deliverable)
        .filter_by(artifact_id=art.id)
        .order_by(Deliverable.finalized_at.asc())
        .all()
    )
    assert len(rows) == 2
    first, second = rows[0], rows[1]
    assert first.superseded_at is not None
    assert first.superseded_by == second.id
    assert second.superseded_at is None


# --------------------------------------------------------------------
# /admin/messages/
# --------------------------------------------------------------------

def test_admin_messages_inbox_lists_threads(admin_client, acme, admin):
    """Inbox shows one row per (client, project_id) thread."""
    # Add a client-side message to the general thread.
    cu = User(sub="ccc", email="ccc@example.com",
              display_name="Client User", role=Role.CLIENT)
    db.session.add(cu)
    db.session.commit()
    db.session.add(ClientMembership(
        client_id=acme.id, user_id=cu.id,
        membership_role="member",
        invited_at=datetime.utcnow(), accepted_at=datetime.utcnow(),
    ))
    db.session.add(Message(
        client_id=acme.id, project_id=None,
        author_id=cu.id, body="Hi there",
    ))
    db.session.commit()

    r = admin_client.get("/admin/messages/")
    assert r.status_code == 200
    assert b"Hi there" in r.data
    assert b"Acme" in r.data


def test_admin_messages_thread_post_writes_reply(admin_client, acme, admin):
    r = admin_client.post(
        f"/admin/messages/{acme.id}/general",
        data={"body": "Got it — kickoff Thursday?"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    latest = (
        db.session.query(Message)
        .filter_by(client_id=acme.id, project_id=None)
        .order_by(Message.created_at.desc())
        .first()
    )
    assert latest is not None
    assert latest.author_id == admin.id
    assert latest.body == "Got it — kickoff Thursday?"
