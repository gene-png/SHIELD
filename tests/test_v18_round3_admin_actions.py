"""Round-3 PR 3C — admin fulfill/decline tests.

Covers:
  - /clients/queue surfaces open ServiceRequests.
  - /clients/<id>/requests/<id>/fulfill creates a Project, links the
    request, audits, notifies the client.
  - /clients/<id>/requests/<id>/decline records reason, audits,
    notifies the client.
  - 'unsure' requests can't be fulfilled directly.
  - Already-resolved requests are no-ops with a flash.
  - Reason must be at least 10 chars to decline.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from shield.extensions import db
from shield.models import (
    AuditEntry,
    ClientMembership,
    Notification,
    PlatformType,
    Project,
    Role,
    ServiceRequest,
    User,
)


@pytest.fixture()
def acme_member(app, acme, admin):
    """A CLIENT-role member of Acme so notifications have someone to
    address. Returns the User."""
    u = User(sub="cu", email="cu@example.com",
             display_name="Acme Member", role=Role.CLIENT)
    db.session.add(u)
    db.session.commit()
    db.session.add(ClientMembership(
        client_id=acme.id, user_id=u.id,
        membership_role="primary_poc",
        invited_at=datetime.utcnow(),
        accepted_at=datetime.utcnow(),
    ))
    db.session.commit()
    return u


@pytest.fixture()
def open_request(app, acme, acme_member):
    sr = ServiceRequest(
        client_id=acme.id, requested_by=acme_member.id,
        service="tech_debt", notes="suspect overlap on SIEM + EDR",
        deadline=(datetime.utcnow().date() + timedelta(days=30)),
    )
    db.session.add(sr)
    db.session.commit()
    return sr


@pytest.fixture()
def unsure_request(app, acme, acme_member):
    sr = ServiceRequest(
        client_id=acme.id, requested_by=acme_member.id,
        service="unsure", notes="not sure where to start",
    )
    db.session.add(sr)
    db.session.commit()
    return sr


# --------------------------------------------------------------------
# Queue surfaces open requests
# --------------------------------------------------------------------

def test_queue_surfaces_open_request(admin_client, acme, open_request):
    """The Waiting bucket includes clients with open ServiceRequests
    even if there's no service_interests gap."""
    acme.intake_completed_at = datetime.utcnow()
    db.session.commit()

    r = admin_client.get("/clients/queue")
    assert r.status_code == 200
    assert b"Open requests" in r.data
    assert b"Fulfill" in r.data
    assert b"Decline" in r.data
    assert b"suspect overlap on SIEM + EDR" in r.data


# --------------------------------------------------------------------
# Fulfill
# --------------------------------------------------------------------

def test_fulfill_creates_project_and_links_request(
    admin_client, acme, admin, open_request,
):
    r = admin_client.post(
        f"/clients/{acme.id}/requests/{open_request.id}/fulfill",
        data={"name": "Acme TD Q2", "client_display_name": "My TD review"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    # Project exists, linked to the request, with the client-facing label.
    project = (
        db.session.query(Project)
        .filter_by(client_id=acme.id, platform=PlatformType.TECH_DEBT, name="Acme TD Q2")
        .first()
    )
    assert project is not None
    assert project.client_display_name == "My TD review"
    refreshed = db.session.get(ServiceRequest, open_request.id)
    assert refreshed.fulfilled_project_id == project.id
    assert refreshed.is_fulfilled


def test_fulfill_writes_audit_and_notification(
    admin_client, acme, admin, open_request, acme_member,
):
    before_audit = db.session.query(AuditEntry).filter_by(
        action="client.service_request_fulfilled",
    ).count()
    before_notif = db.session.query(Notification).filter_by(
        user_id=acme_member.id,
        event_type="client.service_request_fulfilled",
    ).count()
    admin_client.post(
        f"/clients/{acme.id}/requests/{open_request.id}/fulfill",
        data={"name": "Acme TD Q2"},
    )
    assert db.session.query(AuditEntry).filter_by(
        action="client.service_request_fulfilled",
    ).count() == before_audit + 1
    # Client member receives a notification.
    assert db.session.query(Notification).filter_by(
        user_id=acme_member.id,
        event_type="client.service_request_fulfilled",
    ).count() == before_notif + 1


def test_fulfill_requires_name(admin_client, acme, open_request):
    r = admin_client.post(
        f"/clients/{acme.id}/requests/{open_request.id}/fulfill",
        data={"name": ""},
        follow_redirects=False,
    )
    assert r.status_code == 302
    # Redirected back to the form, request not fulfilled.
    refreshed = db.session.get(ServiceRequest, open_request.id)
    assert refreshed.is_open


def test_fulfill_rejects_unsure_request(admin_client, acme, unsure_request):
    """'unsure' is a request flavor, not a real platform — admin
    should reply in messages or decline-with-reason."""
    r = admin_client.post(
        f"/clients/{acme.id}/requests/{unsure_request.id}/fulfill",
        data={"name": "anything"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    refreshed = db.session.get(ServiceRequest, unsure_request.id)
    assert refreshed.is_open
    # And no project was created.
    assert db.session.query(Project).filter_by(
        client_id=acme.id, name="anything",
    ).count() == 0


def test_fulfill_idempotent_on_already_fulfilled(
    admin_client, acme, admin, open_request,
):
    # First fulfill — succeeds.
    admin_client.post(
        f"/clients/{acme.id}/requests/{open_request.id}/fulfill",
        data={"name": "Acme TD Q2"},
    )
    # Second attempt — flashes + redirects; no duplicate project.
    r = admin_client.post(
        f"/clients/{acme.id}/requests/{open_request.id}/fulfill",
        data={"name": "Different Name"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert db.session.query(Project).filter_by(
        client_id=acme.id, name="Different Name",
    ).count() == 0


# --------------------------------------------------------------------
# Decline
# --------------------------------------------------------------------

def test_decline_records_reason_and_state(admin_client, acme, open_request):
    r = admin_client.post(
        f"/clients/{acme.id}/requests/{open_request.id}/decline",
        data={"reason": "Out of scope for this quarter; revisit Q3."},
        follow_redirects=False,
    )
    assert r.status_code == 302
    refreshed = db.session.get(ServiceRequest, open_request.id)
    assert refreshed.is_declined
    assert refreshed.declined_reason.startswith("Out of scope")
    assert refreshed.declined_at is not None


def test_decline_writes_audit_and_notification(
    admin_client, acme, open_request, acme_member,
):
    before_audit = db.session.query(AuditEntry).filter_by(
        action="client.service_request_declined",
    ).count()
    before_notif = db.session.query(Notification).filter_by(
        user_id=acme_member.id,
        event_type="client.service_request_declined",
    ).count()
    admin_client.post(
        f"/clients/{acme.id}/requests/{open_request.id}/decline",
        data={"reason": "Doesn't fit current contract terms."},
    )
    assert db.session.query(AuditEntry).filter_by(
        action="client.service_request_declined",
    ).count() == before_audit + 1
    assert db.session.query(Notification).filter_by(
        user_id=acme_member.id,
        event_type="client.service_request_declined",
    ).count() == before_notif + 1


def test_decline_requires_reason_at_least_10_chars(admin_client, acme, open_request):
    r = admin_client.post(
        f"/clients/{acme.id}/requests/{open_request.id}/decline",
        data={"reason": "nope"},
        follow_redirects=False,
    )
    # Redirects back to the form, not the success path.
    assert r.status_code == 302
    refreshed = db.session.get(ServiceRequest, open_request.id)
    assert refreshed.is_open
    assert refreshed.declined_at is None


# --------------------------------------------------------------------
# Cross-client + RBAC
# --------------------------------------------------------------------

def test_fulfill_requires_admin_role(reviewer_client, acme, open_request):
    r = reviewer_client.post(
        f"/clients/{acme.id}/requests/{open_request.id}/fulfill",
        data={"name": "anything"},
        follow_redirects=False,
    )
    # @admin_only redirects/302s; either way not 200 + not created.
    assert r.status_code in (302, 403)
    refreshed = db.session.get(ServiceRequest, open_request.id)
    assert refreshed.is_open
