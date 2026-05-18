"""Cross-client read protection — the v1.8 client-portal access gate.

Locks in the round-2 acceptance criteria:

  - CLIENT users see only their own client's resources (404 on others).
  - REVIEWER users with assignments see only assigned clients.
  - REVIEWER users with zero assignments preserve the pre-v1.8
    see-everything default (per UX doc round 2 answer).
  - ADMIN users are unrestricted.
  - Cross-client read attempts return 404 (NOT 403) and write an
    `access_denied` audit row.

These tests don't try to assert HTML — they hit the routes that
exist today and check status codes.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from shield.extensions import db
from shield.models import (
    AuditEntry,
    Client,
    ClientMembership,
    PlatformType,
    Project,
    ReviewerAssignment,
    Role,
    User,
)
from shield.spine.access import client_ids_for_user
from shield.spine.repository import write_human_artifact

# --------------------------------------------------------------------
# Fixtures: a second client (Beta), a project + artifact per client.
# --------------------------------------------------------------------

@pytest.fixture()
def beta(app, admin):
    """A second Client so cross-client tests have a 'them' to test against."""
    c = Client(name="Beta Industries")
    db.session.add(c)
    db.session.commit()
    return c


@pytest.fixture()
def acme_project(app, acme, admin):
    p = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="Acme TD", stage="intake", created_by_id=admin.id,
    )
    db.session.add(p)
    db.session.commit()
    return p


@pytest.fixture()
def beta_project(app, beta, admin):
    p = Project(
        client_id=beta.id, platform=PlatformType.TECH_DEBT,
        name="Beta TD", stage="intake", created_by_id=admin.id,
    )
    db.session.add(p)
    db.session.commit()
    return p


@pytest.fixture()
def beta_artifact(app, beta_project, admin):
    return write_human_artifact(
        project=beta_project, stage="raw_intake",
        title="beta secret",
        file_stream=None, filename=None, mime_type=None,
        actor=admin, body_text="nothing to see here",
    )


# --------------------------------------------------------------------
# client_ids_for_user — the resolver everything else builds on
# --------------------------------------------------------------------

def test_admin_is_unrestricted(app, admin):
    assert client_ids_for_user(admin) is None


def test_unauthenticated_sees_nothing(app):
    class _Anon:
        is_authenticated = False
        role = None
    assert client_ids_for_user(_Anon()) == []


def test_client_user_resolves_to_accepted_memberships(app, acme, admin):
    u = User(sub="c1", email="c1@example.com",
             display_name="Client One", role=Role.CLIENT)
    db.session.add(u)
    db.session.commit()
    # Pending invitation — accepted_at is NULL — must NOT grant access.
    db.session.add(ClientMembership(
        client_id=acme.id, user_id=u.id,
        membership_role="member", invited_at=datetime.utcnow(),
        accepted_at=None,
    ))
    db.session.commit()
    assert client_ids_for_user(u) == []

    # After acceptance, the client_id appears.
    m = db.session.query(ClientMembership).filter_by(user_id=u.id).first()
    m.accepted_at = datetime.utcnow()
    db.session.commit()
    assert client_ids_for_user(u) == [acme.id]


def test_reviewer_with_zero_assignments_is_unrestricted(app):
    """Round-2 §10 answer: un-assigned reviewers preserve see-everything.

    Flipping that to whitelist requires a follow-up migration to ensure
    every current reviewer has at least one assignment.
    """
    r = User(sub="r0", email="r0@example.com",
             display_name="Unassigned Rev", role=Role.REVIEWER)
    db.session.add(r)
    db.session.commit()
    assert client_ids_for_user(r) is None


def test_reviewer_with_assignment_resolves_to_that_client(app, acme, admin):
    r = User(sub="r1", email="r1@example.com",
             display_name="Assigned Rev", role=Role.REVIEWER)
    db.session.add(r)
    db.session.commit()
    db.session.add(ReviewerAssignment(
        reviewer_id=r.id, client_id=acme.id,
        assigned_by_id=admin.id, assigned_at=datetime.utcnow(),
    ))
    db.session.commit()
    assert client_ids_for_user(r) == [acme.id]


def test_reviewer_revoked_assignment_does_not_count(app, acme, admin):
    r = User(sub="r2", email="r2@example.com",
             display_name="Revoked Rev", role=Role.REVIEWER)
    db.session.add(r)
    db.session.commit()
    db.session.add(ReviewerAssignment(
        reviewer_id=r.id, client_id=acme.id,
        assigned_by_id=admin.id, assigned_at=datetime.utcnow(),
        revoked_at=datetime.utcnow(),
    ))
    db.session.commit()
    # All assignments revoked → falls back to the unassigned default.
    assert client_ids_for_user(r) is None


# --------------------------------------------------------------------
# HTTP-level: cross-client read returns 404 (not 403) and audits
# --------------------------------------------------------------------

def test_admin_can_read_any_client_artifact(admin_client, beta_artifact):
    r = admin_client.get(f"/repository/artifact/{beta_artifact.id}")
    assert r.status_code == 200


def test_assigned_reviewer_blocked_from_other_clients_artifact(
    client, reviewer, admin, acme, beta_artifact,
):
    """A reviewer assigned to Acme should 404 on Beta's artifact."""
    db.session.add(ReviewerAssignment(
        reviewer_id=reviewer.id, client_id=acme.id,
        assigned_by_id=admin.id, assigned_at=datetime.utcnow(),
    ))
    db.session.commit()
    with client.session_transaction() as sess:
        sess["_user_id"] = reviewer.id
        sess["_fresh"] = True

    r = client.get(f"/repository/artifact/{beta_artifact.id}")
    # 404, not 403 — existence of the cross-client artifact must not leak.
    assert r.status_code == 404


def test_unassigned_reviewer_still_sees_other_clients(
    reviewer_client, beta_artifact,
):
    """Round-2 §10 answer: un-assigned reviewer == see everything."""
    r = reviewer_client.get(f"/repository/artifact/{beta_artifact.id}")
    assert r.status_code == 200


def test_assigned_reviewer_blocked_from_other_clients_detail(
    client, reviewer, admin, acme, beta,
):
    db.session.add(ReviewerAssignment(
        reviewer_id=reviewer.id, client_id=acme.id,
        assigned_by_id=admin.id, assigned_at=datetime.utcnow(),
    ))
    db.session.commit()
    with client.session_transaction() as sess:
        sess["_user_id"] = reviewer.id
        sess["_fresh"] = True

    r = client.get(f"/clients/{beta.id}")
    assert r.status_code == 404


def test_cross_client_read_writes_access_denied_audit(
    client, reviewer, admin, acme, beta,
):
    db.session.add(ReviewerAssignment(
        reviewer_id=reviewer.id, client_id=acme.id,
        assigned_by_id=admin.id, assigned_at=datetime.utcnow(),
    ))
    db.session.commit()
    with client.session_transaction() as sess:
        sess["_user_id"] = reviewer.id
        sess["_fresh"] = True

    before = db.session.query(AuditEntry).filter_by(action="access_denied").count()
    r = client.get(f"/clients/{beta.id}")
    assert r.status_code == 404
    after = db.session.query(AuditEntry).filter_by(action="access_denied").count()
    assert after == before + 1
    # The audit row references the rejected client and the attempting user.
    entry = (
        db.session.query(AuditEntry)
        .filter_by(action="access_denied")
        .order_by(AuditEntry.at.desc())
        .first()
    )
    assert entry.client_id == beta.id
    assert entry.actor_id == reviewer.id


def test_client_user_blocked_from_other_clients_artifact(
    client, client_user, admin, acme, beta_artifact,
):
    """The CLIENT-role role gate redirects them off platform URLs,
    but this test pins the underlying access layer: even if the gate
    didn't fire (e.g. a future /portal/* leak), a cross-client read
    would still 404.
    """
    db.session.add(ClientMembership(
        client_id=acme.id, user_id=client_user.id,
        membership_role="member",
        invited_at=datetime.utcnow(),
        accepted_at=datetime.utcnow(),
    ))
    db.session.commit()
    # Bypass the role gate by calling client_ids_for_user directly,
    # because /repository/* is currently behind the gate for CLIENTs.
    assert client_ids_for_user(client_user) == [acme.id]
    # If they could reach the route, the per-route check would 404.
    # `abort(404)` raises werkzeug.exceptions.NotFound — pin the exact
    # exception class so we know it's the access-gate path, not some
    # other Exception leaking through.
    from werkzeug.exceptions import NotFound

    from shield.spine.access import require_client_access
    with pytest.raises(NotFound):
        with client.application.test_request_context(f"/repository/artifact/{beta_artifact.id}"):
            require_client_access(beta_artifact.client_id, user=client_user)
