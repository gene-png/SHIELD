"""Tests for the self-signup bootstrap flow.

Keycloak's realm now has `registrationAllowed: true`. After OIDC
returns a brand-new CLIENT user with no ClientMembership, the home
redirect routes them to `/portal/start-organization` where they
name their org. POSTing the form atomically creates the Client +
ClientMembership and walks them into /portal/welcome.
"""
from __future__ import annotations

import pytest

from shield.extensions import db
from shield.models import AuditEntry, Client, ClientMembership, Role, User


@pytest.fixture()
def fresh_client_user(client):
    """A CLIENT-role user with NO ClientMembership — the self-signup
    state right after OIDC callback runs but before they pick an org."""
    u = User(sub="new-signup", email="newsignup@example.com",
             display_name="New Signup", role=Role.CLIENT)
    db.session.add(u)
    db.session.commit()
    with client.session_transaction() as sess:
        sess["_user_id"] = u.id
        sess["_fresh"] = True
    return u


# --------------------------------------------------------------------
# Home redirect — no membership → start-organization
# --------------------------------------------------------------------

def test_home_routes_membershipless_client_to_start_organization(client, fresh_client_user):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/portal/start-organization")


# --------------------------------------------------------------------
# /portal/start-organization
# --------------------------------------------------------------------

def test_start_organization_get_renders_form(client, fresh_client_user):
    r = client.get("/portal/start-organization")
    assert r.status_code == 200
    assert b'name="organization_name"' in r.data
    assert b"organization called" in r.data


def test_start_organization_post_creates_client_and_membership(
    client, fresh_client_user,
):
    r = client.post(
        "/portal/start-organization",
        data={"organization_name": "Newco Holdings LLC"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/portal/welcome")

    new_client = db.session.query(Client).filter_by(legal_name="Newco Holdings LLC").first()
    assert new_client is not None
    assert new_client.name == "Newco Holdings LLC"
    # Membership row exists with the user as primary_poc and accepted.
    m = (
        db.session.query(ClientMembership)
        .filter_by(client_id=new_client.id, user_id=fresh_client_user.id)
        .first()
    )
    assert m is not None
    assert m.membership_role == "primary_poc"
    assert m.accepted_at is not None


def test_start_organization_writes_audit_row(client, fresh_client_user):
    before = db.session.query(AuditEntry).filter_by(
        action="client.self_signup_bootstrap",
    ).count()
    client.post(
        "/portal/start-organization",
        data={"organization_name": "Bootstrap Co"},
    )
    assert db.session.query(AuditEntry).filter_by(
        action="client.self_signup_bootstrap",
    ).count() == before + 1


def test_start_organization_rejects_empty_name(client, fresh_client_user):
    r = client.post(
        "/portal/start-organization",
        data={"organization_name": "   "},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "/start-organization" in r.headers["Location"]
    assert db.session.query(ClientMembership).filter_by(
        user_id=fresh_client_user.id,
    ).count() == 0


def test_start_organization_handles_name_collision(client, fresh_client_user, acme):
    """If the typed name collides with an existing Client.name, the
    route appends a short suffix and creates anyway — the user can
    rename later from /portal/about. Better than a 500."""
    r = client.post(
        "/portal/start-organization",
        data={"organization_name": acme.name},  # collide on purpose
        follow_redirects=False,
    )
    assert r.status_code == 302
    # The new client should exist with the same legal_name as typed.
    new_client = (
        db.session.query(Client)
        .filter_by(legal_name=acme.name)
        .first()
    )
    assert new_client is not None
    # But Client.name has a suffix to keep the UNIQUE constraint happy.
    assert new_client.name != acme.name
    assert new_client.name.startswith(acme.name)


def test_start_organization_redirects_existing_member_away(client, acme, admin):
    """A user who already has a membership should be sent to their
    normal landing — start-organization is for brand-new users only."""
    u = User(sub="already-onboarded", email="already@example.com",
             display_name="Already In", role=Role.CLIENT)
    db.session.add(u)
    db.session.commit()
    from datetime import datetime
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

    r = client.get("/portal/start-organization", follow_redirects=False)
    assert r.status_code == 302
    # Lands on /portal/welcome or /portal/ depending on intake state.
    assert r.headers["Location"].endswith(("/portal/welcome", "/portal/"))


# --------------------------------------------------------------------
# Cross-cutting: starts work after bootstrap
# --------------------------------------------------------------------

def test_after_bootstrap_user_can_reach_portal_routes(client, fresh_client_user):
    """After the bootstrap POST, the user is a proper CLIENT with a
    membership and can hit /portal/welcome (and beyond)."""
    client.post(
        "/portal/start-organization",
        data={"organization_name": "Smoketest LLC"},
    )
    r = client.get("/portal/welcome")
    assert r.status_code == 200


def test_keycloak_realm_has_registration_enabled():
    """Sanity-check the dev realm import file. If this regresses we'd
    silently break self-signup."""
    import json
    from pathlib import Path
    realm_path = Path(__file__).resolve().parents[1] / "keycloak" / "import" / "shield-dev-realm.json"
    realm = json.loads(realm_path.read_text(encoding="utf-8"))
    assert realm.get("registrationAllowed") is True
    assert realm.get("registrationEmailAsUsername") is True
