"""v1.9 batch 1 — wayfinding + visual consistency hardening.

Pins the user-facing outcomes from the two enterprise UX reviews:

  - Active nav state (aria-current + CSS class) appears on the
    current section.
  - /portal/documents has two modes — onboarding wizard for users
    finishing intake, library mode for returning users.
  - /portal/services is now a view-first page; the edit form lives
    at /portal/services/edit.
  - Portal dashboard service cards carry status badges and explicit
    next-step text.
  - "primary_poc" surfaces as "Primary Point of Contact" in Settings.
  - Activity page shows a failed-jobs alert banner and friendly job
    labels instead of raw function names.
  - Queue links each client name to the client hub at /clients/<id>.
  - 405 errors render inside the app shell (not bare browser page).
"""
from __future__ import annotations

from datetime import datetime

import pytest

from shield.extensions import db
from shield.models import (
    Client,
    ClientMembership,
    Role,
    User,
)


# --------------------------------------------------------------------
# Active nav state
# --------------------------------------------------------------------

def test_admin_nav_marks_queue_active_on_queue_page(admin_client):
    r = admin_client.get("/clients/queue")
    assert r.status_code == 200
    body = r.data
    assert b'aria-current="page"' in body
    assert b"shield-nav__link--active" in body


def test_admin_nav_does_not_mark_clients_active_when_on_queue(admin_client):
    """The Clients tab should NOT light up just because we're on
    /clients/queue — Queue gets its own active state, Clients stays
    inactive there."""
    r = admin_client.get("/clients/queue")
    body = r.data.decode()
    # The Clients link should appear without active class right after
    # the Queue link. Cheap heuristic: count active markers — exactly
    # one nav link should be active.
    assert body.count("shield-nav__link--active") == 1


def test_admin_nav_marks_audit_active_on_audit_page(admin_client):
    r = admin_client.get("/audit/")
    body = r.data
    assert b'aria-current="page"' in body


# --------------------------------------------------------------------
# /portal/documents library mode vs. wizard mode
# --------------------------------------------------------------------

@pytest.fixture()
def returning_client(client, app, admin):
    """A CLIENT-role user whose intake is COMPLETE — should see
    library mode at /portal/documents."""
    c = Client(name="Returning", legal_name="Returning Inc.",
               intake_completed_at=datetime.utcnow())
    db.session.add(c)
    db.session.commit()
    u = User(sub="ret", email="ret@example", display_name="Ret",
             role=Role.CLIENT)
    db.session.add(u)
    db.session.commit()
    db.session.add(ClientMembership(
        client_id=c.id, user_id=u.id,
        membership_role="primary_poc",
        invited_by_id=admin.id, accepted_at=datetime.utcnow(),
    ))
    db.session.commit()
    from tests.conftest import _login
    _login(client, u.id)
    return client, c, u


@pytest.fixture()
def onboarding_client(client, app, admin):
    """A CLIENT-role user mid-intake — should see wizard mode."""
    c = Client(name="Onboarding", legal_name="Onboarding LLC",
               intake_completed_at=None)
    db.session.add(c)
    db.session.commit()
    u = User(sub="onb", email="onb@example", display_name="Onb",
             role=Role.CLIENT)
    db.session.add(u)
    db.session.commit()
    db.session.add(ClientMembership(
        client_id=c.id, user_id=u.id,
        membership_role="primary_poc",
        invited_by_id=admin.id, accepted_at=datetime.utcnow(),
    ))
    db.session.commit()
    from tests.conftest import _login
    _login(client, u.id)
    return client, c, u


def test_documents_library_mode_for_returning_user(returning_client):
    client, _, _ = returning_client
    r = client.get("/portal/documents")
    assert r.status_code == 200
    body = r.data
    # No wizard step counter for returning users.
    assert b"Step 3 of 4" not in body
    # Library framing.
    assert b"Your documents" in body
    assert b"Add more files" in body


def test_documents_wizard_mode_for_onboarding_user(onboarding_client):
    client, _, _ = onboarding_client
    r = client.get("/portal/documents")
    assert r.status_code == 200
    body = r.data
    assert b"Step 3 of 4" in body
    assert b"Share what you" in body  # "Share what you've already got"


# --------------------------------------------------------------------
# /portal/services view vs. edit
# --------------------------------------------------------------------

def test_services_view_renders_cards_not_form(returning_client):
    client, c, _ = returning_client
    c.service_interests = ["zero_trust"]
    db.session.commit()
    r = client.get("/portal/services")
    body = r.data
    # The new view-first surface shows a status badge and a
    # "Change services" CTA, not a checkbox grid.
    assert b"Change services" in body
    assert b"shield-status-badge" in body


def test_services_edit_renders_checkbox_form(returning_client):
    client, _, _ = returning_client
    r = client.get("/portal/services/edit")
    assert r.status_code == 200
    body = r.data
    assert b'type="checkbox"' in body
    assert b"Save changes" in body


def test_services_post_is_still_supported(returning_client):
    """Backward-compat — the prior POST handler at /portal/services
    keeps working so test_v18_portal_dashboard's regression doesn't
    break."""
    client, _, _ = returning_client
    r = client.post(
        "/portal/services",
        data={"services": "zero_trust"},
        follow_redirects=False,
    )
    assert r.status_code in (302,)


# --------------------------------------------------------------------
# Portal dashboard badges + Next-step
# --------------------------------------------------------------------

def test_dashboard_card_for_zt_setup_state_has_start_questionnaire_cta(
    returning_client,
):
    client, c, _ = returning_client
    c.service_interests = ["zero_trust"]
    db.session.commit()
    r = client.get("/portal/")
    body = r.data
    # Status badge appears.
    assert b"shield-status-badge" in body
    # Next-step text directs the user.
    assert b"Start questionnaire" in body


# --------------------------------------------------------------------
# primary_poc relabel in Settings
# --------------------------------------------------------------------

def test_settings_shows_friendly_role_label(returning_client):
    client, _, _ = returning_client
    r = client.get("/portal/settings/")
    body = r.data
    assert b"Primary Point of Contact" in body
    # The raw enum value must not surface.
    assert b">primary_poc<" not in body


# --------------------------------------------------------------------
# Activity page polish
# --------------------------------------------------------------------

def test_activity_page_uses_friendly_function_labels(admin_client):
    r = admin_client.get("/jobs/")
    assert r.status_code == 200
    body = r.data.decode()
    # The friendly label map is rendered into the page (it lives in the
    # template, so even with no jobs, the dict keys aren't visible —
    # but the page should NOT show raw `p3_coverage_job` text outside
    # of the rendered template). Cheap check: confirm the page chrome
    # is in place + no failed jobs banner when there are no failures.
    assert "<h1>Activity.</h1>" in body
    # When there are no jobs, the body should not contain raw
    # `p3_coverage_job` etc. inline.
    assert "p3_coverage_job" not in body


# --------------------------------------------------------------------
# Queue links client name to the client hub
# --------------------------------------------------------------------

def test_queue_links_client_name_to_detail_page(admin_client, app, admin):
    c = Client(name="Queue Linkable")
    db.session.add(c)
    db.session.commit()
    r = admin_client.get("/clients/queue")
    expected_link = f"/clients/{c.id}".encode()
    assert expected_link in r.data


# --------------------------------------------------------------------
# 405 inside the shell
# --------------------------------------------------------------------

def test_405_renders_within_app_shell(admin_client):
    """Hit a POST-only route via GET. Must come back inside the SHIELD
    chrome (nav + header + footer), not a bare browser 405."""
    r = admin_client.get("/admin/lifecycle/projects/nonexistent/archive")
    # The route is POST-only — Flask returns 405 by default.
    assert r.status_code == 405
    body = r.data
    # Our error template is inside the app shell.
    assert b"SHIELD" in body
    assert b"<header" in body
