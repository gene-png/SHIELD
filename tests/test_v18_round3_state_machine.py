"""Round-3 PR 3B — dashboard state machine + request-a-service tests.

Covers:
  - Each card-state branch from round-3 §5:
      not-rendered, requested, declined, setup, awaiting_docs,
      in_review, ready_to_view
  - Project state takes precedence over request state.
  - /portal/services/request POST writes a ServiceRequest, appends to
    service_interests, audits, and notifies admins.
  - 'unsure' creates a request but does NOT append to service_interests.
  - Deadline-as-bad-date is gracefully ignored.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from werkzeug.datastructures import MultiDict

from shield.extensions import db
from shield.models import (
    AuditEntry,
    Client,
    ClientMembership,
    Deliverable,
    Notification,
    PlatformType,
    Project,
    Role,
    ServiceRequest,
    User,
)
from shield.spine.repository import write_human_artifact


@pytest.fixture()
def pm(client, acme, admin):
    """A primary-POC CLIENT user on Acme, logged in. Reset Acme's
    service_interests + intake_completed_at so each test starts fresh."""
    acme.service_interests = []
    acme.intake_completed_at = datetime.utcnow()
    db.session.commit()
    u = User(sub="pp", email="pp@example.com",
             display_name="Acme PM", role=Role.CLIENT)
    db.session.add(u)
    db.session.commit()
    db.session.add(ClientMembership(
        client_id=acme.id, user_id=u.id,
        membership_role="primary_poc",
        invited_at=datetime.utcnow(),
        accepted_at=datetime.utcnow(),
    ))
    db.session.commit()
    with client.session_transaction() as sess:
        sess["_user_id"] = u.id
        sess["_fresh"] = True
    return u


# --------------------------------------------------------------------
# Card states — one branch at a time
# --------------------------------------------------------------------

def test_no_interest_no_request_no_project_renders_no_card(client, pm, acme):
    """Round-3 §5 'Not interested' rule: render nothing."""
    r = client.get("/portal/")
    assert r.status_code == 200
    # The empty-state CTA renders instead of a card.
    assert b"Tell us what you need" in r.data
    # And specifically: no card body strings show up.
    for needle in (b"Requested", b"In review", b"Ready to view"):
        assert needle not in r.data


def test_open_request_renders_requested_state(client, pm, acme, admin):
    db.session.add(ServiceRequest(
        client_id=acme.id, requested_by=pm.id,
        service="tech_debt", notes="want to find overlap",
    ))
    db.session.commit()
    r = client.get("/portal/")
    assert r.status_code == 200
    assert b"Requested." in r.data
    # Newlines in the template wrap the phrase; substring across the
    # break would fail. Match the unique tail instead.
    assert b"one business day" in r.data


def test_declined_request_renders_declined_with_reason_and_rerequest(
    client, pm, acme, admin,
):
    db.session.add(ServiceRequest(
        client_id=acme.id, requested_by=pm.id,
        service="zero_trust",
        declined_at=datetime.utcnow(),
        declined_reason="Out of scope for this contract — happy to revisit Q3.",
    ))
    db.session.commit()
    r = client.get("/portal/")
    assert b"Not a fit right now." in r.data
    assert b"Out of scope for this contract" in r.data
    assert b"Re-request" in r.data


def test_service_in_interests_with_no_project_renders_setup(
    client, pm, acme,
):
    """Round-3 §5: 'Setup' = picked on welcome, no request, no project yet."""
    acme.service_interests = ["attack_surface"]
    db.session.commit()
    r = client.get("/portal/")
    assert b"Getting set up." in r.data


def test_project_in_intake_renders_awaiting_docs(client, pm, acme, admin):
    db.session.add(Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="x", stage="intake", created_by_id=admin.id,
    ))
    db.session.commit()
    r = client.get("/portal/")
    assert b"Waiting on your documents." in r.data
    # Action button to /portal/documents present.
    assert b"Upload documents" in r.data


def test_project_in_overlap_renders_in_review(client, pm, acme, admin):
    db.session.add(Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="x", stage="overlap_analysis", created_by_id=admin.id,
    ))
    db.session.commit()
    r = client.get("/portal/")
    assert b"In review." in r.data


def test_deliverable_makes_card_ready_to_view(client, pm, acme, admin):
    project = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="x", stage="overlap_analysis", created_by_id=admin.id,
    )
    db.session.add(project)
    db.session.commit()
    art = write_human_artifact(
        project=project, stage="admin_final",
        title="cap-list", file_stream=None, filename=None, mime_type=None,
        actor=admin, body_text="[]",
    )
    db.session.add(Deliverable(
        client_id=acme.id, project_id=project.id, artifact_id=art.id,
        title="The big report", finalized_by=admin.id,
    ))
    db.session.commit()
    r = client.get("/portal/")
    assert b"Ready to view." in r.data
    assert b"The big report" in r.data


def test_project_supersedes_open_request_state(client, pm, acme, admin):
    """Round-3 §5: 'A service that has both an open request and an
    active project renders the project state, not the request state.'"""
    db.session.add(ServiceRequest(
        client_id=acme.id, requested_by=pm.id, service="tech_debt",
    ))
    db.session.add(Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="x", stage="overlap_analysis", created_by_id=admin.id,
    ))
    db.session.commit()
    r = client.get("/portal/")
    # Should render "In review", NOT "Requested".
    assert b"In review." in r.data
    assert b"Requested." not in r.data


def test_archived_project_renders_complete(client, pm, acme, admin):
    p = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="x", stage="archived", created_by_id=admin.id,
    )
    db.session.add(p)
    db.session.commit()
    r = client.get("/portal/")
    assert b"Complete." in r.data


def test_client_display_name_replaces_label_on_card(client, pm, acme, admin):
    db.session.add(Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="Acme — Tech Debt Q3 2026", stage="overlap_analysis",
        client_display_name="My consolidation review",
        created_by_id=admin.id,
    ))
    db.session.commit()
    r = client.get("/portal/")
    assert b"My consolidation review" in r.data
    # The internal admin name does NOT leak.
    assert b"Q3 2026" not in r.data


# --------------------------------------------------------------------
# /portal/services/request — the form + POST
# --------------------------------------------------------------------

def test_services_request_get_renders_form(client, pm):
    r = client.get("/portal/services/request")
    assert r.status_code == 200
    # Jinja autoescapes the apostrophe in "I'm not sure" to &#39;, so
    # match the escaped form (or just match "not sure").
    for label in (b"Tech Debt", b"Zero Trust", b"Attack Surface", b"not sure"):
        assert label in r.data


def test_services_request_post_writes_row_and_audits(client, pm, acme):
    before = db.session.query(AuditEntry).filter_by(
        action="client.service_requested",
    ).count()
    r = client.post(
        "/portal/services/request",
        data={"service": "tech_debt", "notes": "Want to find overlap."},
        follow_redirects=False,
    )
    assert r.status_code == 302
    sr = db.session.query(ServiceRequest).filter_by(
        client_id=acme.id, service="tech_debt",
    ).first()
    assert sr is not None
    assert sr.is_open
    assert sr.notes == "Want to find overlap."
    assert db.session.query(AuditEntry).filter_by(
        action="client.service_requested",
    ).count() == before + 1


def test_services_request_appends_to_service_interests(client, pm, acme):
    acme.service_interests = ["zero_trust"]
    db.session.commit()
    client.post(
        "/portal/services/request",
        data={"service": "tech_debt"},
    )
    refreshed = db.session.get(Client, acme.id)
    assert set(refreshed.service_interests) == {"zero_trust", "tech_debt"}


def test_unsure_request_does_not_append_to_service_interests(client, pm, acme):
    """'unsure' is a request flavor, not a platform — don't add it."""
    acme.service_interests = []
    db.session.commit()
    client.post(
        "/portal/services/request",
        data={"service": "unsure", "notes": "Help me figure it out."},
    )
    refreshed = db.session.get(Client, acme.id)
    assert refreshed.service_interests == []
    # But the ServiceRequest row exists.
    sr = db.session.query(ServiceRequest).filter_by(
        client_id=acme.id, service="unsure",
    ).first()
    assert sr is not None


def test_services_request_notifies_admins(client, pm, acme, admin):
    before = db.session.query(Notification).filter_by(
        user_id=admin.id, event_type="client.service_requested",
    ).count()
    client.post(
        "/portal/services/request",
        data={"service": "attack_surface"},
    )
    assert db.session.query(Notification).filter_by(
        user_id=admin.id, event_type="client.service_requested",
    ).count() == before + 1


def test_services_request_rejects_bogus_service(client, pm, acme):
    r = client.post(
        "/portal/services/request",
        data={"service": "drop_table"},
        follow_redirects=False,
    )
    # Flashes + redirects back to the form rather than creating a row.
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/portal/services/request")
    assert db.session.query(ServiceRequest).filter_by(
        client_id=acme.id, service="drop_table",
    ).count() == 0


def test_services_request_handles_bad_deadline_gracefully(client, pm, acme):
    """A garbage deadline should not crash; it's optional."""
    client.post(
        "/portal/services/request",
        data=MultiDict([
            ("service", "tech_debt"),
            ("deadline", "not-a-date"),
        ]),
    )
    sr = db.session.query(ServiceRequest).filter_by(
        client_id=acme.id, service="tech_debt",
    ).first()
    assert sr is not None
    assert sr.deadline is None


def test_services_request_captures_deadline(client, pm, acme):
    when = (datetime.utcnow() + timedelta(days=14)).date()
    client.post(
        "/portal/services/request",
        data={"service": "zero_trust", "deadline": when.isoformat()},
    )
    sr = db.session.query(ServiceRequest).filter_by(
        client_id=acme.id, service="zero_trust",
    ).first()
    assert sr is not None
    assert sr.deadline == when
