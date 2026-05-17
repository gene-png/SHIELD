"""Round-3 PR 3A — schema + leak-fix tests.

Covers:
  - ServiceRequest model contract (open / fulfilled / declined branches)
  - Project.client_display_name nullable
  - The `client_label` Jinja filter falls back through legal_name
  - Portal templates render legal_name instead of seed name
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from shield import create_app
from shield.config import TestConfig
from shield.extensions import db
from shield.models import (
    Client,
    ClientMembership,
    PlatformType,
    Project,
    Role,
    ServiceRequest,
    User,
)

# --------------------------------------------------------------------
# ServiceRequest schema invariants
# --------------------------------------------------------------------

def test_service_request_open_state(app, acme, admin):
    sr = ServiceRequest(
        client_id=acme.id, requested_by=admin.id,
        service="tech_debt", notes="cost-cutting target",
    )
    db.session.add(sr)
    db.session.commit()
    assert sr.is_open
    assert not sr.is_fulfilled
    assert not sr.is_declined


def test_service_request_fulfilled_state(app, acme, admin):
    project = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="Acme TD", stage="intake", created_by_id=admin.id,
    )
    db.session.add(project)
    db.session.commit()
    sr = ServiceRequest(
        client_id=acme.id, requested_by=admin.id,
        service="tech_debt",
        fulfilled_project_id=project.id,
    )
    db.session.add(sr)
    db.session.commit()
    assert sr.is_fulfilled
    assert not sr.is_open
    assert not sr.is_declined


def test_service_request_declined_state(app, acme, admin):
    sr = ServiceRequest(
        client_id=acme.id, requested_by=admin.id,
        service="zero_trust",
        declined_at=datetime.utcnow(),
        declined_reason="Scope is out of fit for v1.",
    )
    db.session.add(sr)
    db.session.commit()
    assert sr.is_declined
    assert not sr.is_open
    assert not sr.is_fulfilled
    assert sr.declined_reason


def test_service_request_carries_optional_metadata(app, acme, admin):
    deadline = date.today() + timedelta(days=30)
    sr = ServiceRequest(
        client_id=acme.id, requested_by=admin.id,
        service="attack_surface", notes="board ask",
        deadline=deadline,
    )
    db.session.add(sr)
    db.session.commit()
    refreshed = db.session.get(ServiceRequest, sr.id)
    assert refreshed.notes == "board ask"
    assert refreshed.deadline == deadline


def test_project_client_display_name_nullable(app, acme, admin):
    p = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="Internal — Acme TD Q3", stage="intake", created_by_id=admin.id,
    )
    db.session.add(p)
    db.session.commit()
    assert p.client_display_name is None
    p.client_display_name = "My Tech Debt review"
    db.session.commit()
    refreshed = db.session.get(Project, p.id)
    assert refreshed.client_display_name == "My Tech Debt review"


# --------------------------------------------------------------------
# client_label Jinja filter
# --------------------------------------------------------------------

@pytest.fixture()
def app_for_filter():
    return create_app(TestConfig)


def test_client_label_prefers_legal_name(app_for_filter):
    f = app_for_filter.jinja_env.filters["client_label"]
    c = Client(name="Seed Name", legal_name="What The Client Typed Inc.")
    assert f(c) == "What The Client Typed Inc."


def test_client_label_falls_back_to_name(app_for_filter):
    f = app_for_filter.jinja_env.filters["client_label"]
    c = Client(name="Seed Name", legal_name=None)
    assert f(c) == "Seed Name"


def test_client_label_handles_none(app_for_filter):
    f = app_for_filter.jinja_env.filters["client_label"]
    assert f(None) == ""


def test_client_label_handles_empty_legal_name(app_for_filter):
    """Empty / whitespace-only legal_name should fall through to name."""
    f = app_for_filter.jinja_env.filters["client_label"]
    c = Client(name="Seed Name", legal_name="   ")
    assert f(c) == "Seed Name"


# --------------------------------------------------------------------
# Portal templates render the typed legal_name, not the seed name
# --------------------------------------------------------------------

@pytest.fixture()
def acme_pm_with_typed_legal_name(client, acme, admin):
    """Acme has a legal_name the client typed during intake — fresh.

    Bug A as described in round-3: every portal screen must render
    the typed legal_name, never the seed-assigned name.
    """
    acme.legal_name = "Real Org I Just Typed In, LLC"
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


@pytest.mark.parametrize("url", [
    "/portal/",
    "/portal/welcome",
    "/portal/about",
    "/portal/services",
    "/portal/documents",
    "/portal/confirm",
    "/portal/settings/",
    "/portal/messages/",
    "/portal/deliverables/",
])
def test_portal_pages_render_legal_name_not_seed_name(
    client, acme_pm_with_typed_legal_name, url,
):
    r = client.get(url)
    assert r.status_code == 200, f"{url} returned {r.status_code}"
    assert b"Real Org I Just Typed In, LLC" in r.data, (
        f"{url} did not render the typed legal_name"
    )
    assert b"Acme Co" not in r.data, (
        f"{url} leaked the seed name 'Acme Co' to a CLIENT user "
        "who typed a different organization"
    )


def test_legacy_intake_redirects_client_to_portal(client, acme_pm_with_typed_legal_name):
    r = client.get("/intake/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/portal/")
