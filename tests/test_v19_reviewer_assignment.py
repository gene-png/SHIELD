"""v1.9 item 3: admin UI to grant + revoke reviewer-per-client access.

Pins:
  - /admin/reviewers/ renders the page with each reviewer and their
    active assignments.
  - POST /admin/reviewers/grant creates a new ReviewerAssignment.
  - POST /admin/reviewers/<id>/revoke soft-revokes (revoked_at).
  - Granting an already-revoked assignment RESTORES it (clears
    revoked_at) instead of erroring on the unique constraint.
  - Non-admin roles can't reach any of these.
"""
from __future__ import annotations

import pytest

from shield.extensions import db
from shield.models import (
    AuditEntry,
    Client,
    ReviewerAssignment,
    Role,
    User,
)


@pytest.fixture()
def reviewer_user(app):
    u = User(sub="rev", email="rev@example", display_name="Rev",
             role=Role.REVIEWER)
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture()
def two_clients(app):
    a = Client(name="Org Alpha")
    b = Client(name="Org Bravo")
    db.session.add_all([a, b])
    db.session.commit()
    return a, b


# --------------------------------------------------------------------
# Index page
# --------------------------------------------------------------------

def test_reviewers_index_renders_for_admin(admin_client, reviewer_user):
    r = admin_client.get("/admin/reviewers/")
    assert r.status_code == 200
    body = r.data
    assert b"Reviewer access" in body
    assert b"rev@example" in body
    # Grant form is present.
    assert b'name="reviewer_id"' in body
    assert b'name="client_id"' in body


def test_reviewers_index_blocked_for_client_role(client_role_client):
    r = client_role_client.get("/admin/reviewers/", follow_redirects=False)
    # role gate redirects CLIENT to /portal/ before admin_only fires
    assert r.status_code in (302, 403)


# --------------------------------------------------------------------
# Grant
# --------------------------------------------------------------------

def test_grant_creates_assignment_and_audit(
    admin_client, reviewer_user, two_clients, admin,
):
    alpha, _ = two_clients
    r = admin_client.post(
        "/admin/reviewers/grant",
        data={"reviewer_id": reviewer_user.id, "client_id": alpha.id},
        follow_redirects=False,
    )
    assert r.status_code == 302
    ra = (db.session.query(ReviewerAssignment)
          .filter_by(reviewer_id=reviewer_user.id, client_id=alpha.id)
          .one())
    assert ra.revoked_at is None
    assert ra.assigned_by_id == admin.id
    entry = (db.session.query(AuditEntry)
             .filter_by(action="reviewer.assigned").first())
    assert entry is not None
    assert entry.details["reviewer_email"] == "rev@example"


def test_grant_idempotent_when_active(
    admin_client, reviewer_user, two_clients,
):
    alpha, _ = two_clients
    admin_client.post("/admin/reviewers/grant",
                      data={"reviewer_id": reviewer_user.id,
                            "client_id": alpha.id})
    admin_client.post("/admin/reviewers/grant",
                      data={"reviewer_id": reviewer_user.id,
                            "client_id": alpha.id})
    count = (db.session.query(ReviewerAssignment)
             .filter_by(reviewer_id=reviewer_user.id, client_id=alpha.id)
             .count())
    assert count == 1


def test_grant_restores_a_revoked_assignment(
    admin_client, reviewer_user, two_clients,
):
    """Re-granting a revoked row clears the revoked_at flag and reuses
    the existing row rather than failing on the unique constraint."""
    from datetime import datetime
    alpha, _ = two_clients
    ra = ReviewerAssignment(
        reviewer_id=reviewer_user.id, client_id=alpha.id,
        assigned_by_id=reviewer_user.id, revoked_at=datetime.utcnow(),
    )
    db.session.add(ra)
    db.session.commit()
    admin_client.post("/admin/reviewers/grant",
                      data={"reviewer_id": reviewer_user.id,
                            "client_id": alpha.id})
    db.session.refresh(ra)
    assert ra.revoked_at is None


def test_grant_rejects_non_reviewer_user(
    admin_client, two_clients, admin,
):
    """Granting access to a non-reviewer-role user is rejected with
    a flash, not a constraint error."""
    alpha, _ = two_clients
    r = admin_client.post(
        "/admin/reviewers/grant",
        data={"reviewer_id": admin.id, "client_id": alpha.id},
        follow_redirects=False,
    )
    assert r.status_code == 302
    count = (db.session.query(ReviewerAssignment)
             .filter_by(reviewer_id=admin.id).count())
    assert count == 0


# --------------------------------------------------------------------
# Revoke
# --------------------------------------------------------------------

def test_revoke_sets_revoked_at_and_audits(
    admin_client, reviewer_user, two_clients,
):
    alpha, _ = two_clients
    admin_client.post("/admin/reviewers/grant",
                      data={"reviewer_id": reviewer_user.id,
                            "client_id": alpha.id})
    ra = (db.session.query(ReviewerAssignment)
          .filter_by(reviewer_id=reviewer_user.id, client_id=alpha.id)
          .one())
    r = admin_client.post(
        f"/admin/reviewers/{ra.id}/revoke",
        follow_redirects=False,
    )
    assert r.status_code == 302
    db.session.refresh(ra)
    assert ra.revoked_at is not None
    entry = (db.session.query(AuditEntry)
             .filter_by(action="reviewer.revoked", target_id=ra.id).one())
    assert entry is not None


# --------------------------------------------------------------------
# Access layer respects the assignments
# --------------------------------------------------------------------

def test_reviewer_with_assignment_is_scoped_to_that_client(
    app, reviewer_user, two_clients,
):
    """End-to-end: granting an assignment scopes the reviewer's
    client_ids_for_user output to just that client."""
    from shield.spine.access import client_ids_for_user
    alpha, bravo = two_clients
    db.session.add(ReviewerAssignment(
        reviewer_id=reviewer_user.id, client_id=alpha.id,
        assigned_by_id=reviewer_user.id,
    ))
    db.session.commit()
    allowed = client_ids_for_user(reviewer_user)
    assert allowed == [alpha.id]
