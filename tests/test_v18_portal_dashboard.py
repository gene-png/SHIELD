"""Tests for the v1.8 portal returning-client surfaces (PR 4).

Covers:
  - The real dashboard (service cards + recent activity)
  - /portal/services (manage interests after intake)
  - /portal/deliverables/ list + /portal/deliverables/<id> detail
  - /portal/messages/ list + thread + post
  - /portal/settings + invite-a-colleague + accept flow
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from werkzeug.datastructures import MultiDict

from shield.extensions import db
from shield.models import (
    AuditEntry,
    ClientInvitation,
    ClientMembership,
    Deliverable,
    Message,
    PlatformType,
    Project,
    Role,
    User,
)
from shield.spine.portal import _hash_token
from shield.spine.repository import write_human_artifact


@pytest.fixture()
def acme_pm(client, acme, admin):
    """Primary-POC user for Acme. Logged in via the test client."""
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


@pytest.fixture()
def acme_p1_project(app, acme, admin):
    p = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="Acme Tech Debt Q2", stage="overlap_analysis",
        created_by_id=admin.id,
    )
    db.session.add(p)
    db.session.commit()
    return p


# --------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------

def test_dashboard_shows_service_cards_for_chosen_interests(
    client, acme_pm, acme, acme_p1_project,
):
    acme.service_interests = ["tech_debt", "zero_trust"]
    db.session.commit()
    r = client.get("/portal/")
    assert r.status_code == 200
    # The two cards in service_interests show up; attack_surface (not picked)
    # does NOT show.
    assert b"Tech Debt" in r.data
    assert b"Zero Trust" in r.data
    assert b"Attack Surface" not in r.data


def test_dashboard_card_state_active_when_project_exists(
    client, acme_pm, acme, acme_p1_project,
):
    acme.service_interests = ["tech_debt"]
    db.session.commit()
    r = client.get("/portal/")
    assert r.status_code == 200
    assert b"In progress" in r.data


def test_dashboard_card_state_delivered_when_deliverable_exists(
    client, acme_pm, acme, acme_p1_project, admin,
):
    acme.service_interests = ["tech_debt"]
    db.session.commit()
    art = write_human_artifact(
        project=acme_p1_project, stage="admin_final",
        title="Capability list v1",
        file_stream=None, filename=None, mime_type=None,
        actor=admin, body_text="[]",
    )
    db.session.add(Deliverable(
        client_id=acme.id, project_id=acme_p1_project.id, artifact_id=art.id,
        title="The list", finalized_by=admin.id,
    ))
    db.session.commit()
    r = client.get("/portal/")
    assert b"Deliverables ready" in r.data


# --------------------------------------------------------------------
# /portal/services
# --------------------------------------------------------------------

def test_services_post_updates_interests(client, acme_pm, acme):
    r = client.post(
        "/portal/services",
        data=MultiDict([("services", "attack_surface")]),
        follow_redirects=False,
    )
    # /portal/services delegates to welcome which redirects to /about.
    assert r.status_code == 302
    refreshed = db.session.query(type(acme)).filter_by(id=acme.id).first()
    assert refreshed.service_interests == ["attack_surface"]


# --------------------------------------------------------------------
# /portal/deliverables
# --------------------------------------------------------------------

def test_deliverables_list_empty_state(client, acme_pm):
    r = client.get("/portal/deliverables/")
    assert r.status_code == 200
    assert b"Nothing finalized yet" in r.data


def test_deliverables_list_groups_by_project(
    client, acme_pm, acme, acme_p1_project, admin,
):
    art = write_human_artifact(
        project=acme_p1_project, stage="admin_final",
        title="L1", file_stream=None, filename=None, mime_type=None,
        actor=admin, body_text="[]",
    )
    db.session.add(Deliverable(
        client_id=acme.id, project_id=acme_p1_project.id, artifact_id=art.id,
        title="Final Tech Debt", summary="The TLDR.",
        finalized_by=admin.id,
    ))
    db.session.commit()
    r = client.get("/portal/deliverables/")
    assert r.status_code == 200
    assert b"Final Tech Debt" in r.data
    assert b"Acme Tech Debt Q2" in r.data  # project name as the group header


def test_deliverable_detail_404_for_other_clients_deliverable(client, acme_pm):
    """Per access spec: existence of another client's deliverable
    must not leak — 404, not 403."""
    # Build a beta client + project + deliverable.
    from shield.models import Client
    beta = Client(name="Beta DT")
    db.session.add(beta)
    db.session.commit()
    p = Project(
        client_id=beta.id, platform=PlatformType.TECH_DEBT,
        name="b", stage="intake",
    )
    db.session.add(p)
    db.session.commit()
    art = write_human_artifact(
        project=p, stage="admin_final",
        title="beta", file_stream=None, filename=None, mime_type=None,
        actor=acme_pm, body_text="[]",
    )
    d = Deliverable(
        client_id=beta.id, project_id=p.id, artifact_id=art.id,
        title="Beta's report", finalized_by=acme_pm.id,
    )
    db.session.add(d)
    db.session.commit()

    r = client.get(f"/portal/deliverables/{d.id}")
    assert r.status_code == 404


# --------------------------------------------------------------------
# /portal/messages
# --------------------------------------------------------------------

def test_messages_list_includes_general_and_each_project(
    client, acme_pm, acme_p1_project,
):
    r = client.get("/portal/messages/")
    assert r.status_code == 200
    assert b"General" in r.data
    assert b"Acme Tech Debt Q2" in r.data


def test_messages_post_writes_to_thread(client, acme_pm, acme):
    before = db.session.query(Message).filter_by(client_id=acme.id).count()
    r = client.post(
        "/portal/messages/general",
        data={"body": "Quick question for the team."},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert db.session.query(Message).filter_by(client_id=acme.id).count() == before + 1
    m = (
        db.session.query(Message)
        .filter_by(client_id=acme.id, project_id=None)
        .order_by(Message.created_at.desc())
        .first()
    )
    assert m.body == "Quick question for the team."


def test_messages_thread_marks_unread_as_read(
    client, acme_pm, acme, admin,
):
    """Visiting a thread sets the viewer's read_at_map entry."""
    m = Message(
        client_id=acme.id, project_id=None,
        author_id=admin.id, body="hello",
    )
    db.session.add(m)
    db.session.commit()
    assert (m.read_at_map or {}).get(acme_pm.id) is None
    client.get("/portal/messages/general")
    refreshed = db.session.get(Message, m.id)
    assert refreshed.read_at_map.get(acme_pm.id) is not None


# --------------------------------------------------------------------
# /portal/settings + invite + accept
# --------------------------------------------------------------------

def test_settings_post_updates_profile(client, acme_pm):
    r = client.post(
        "/portal/settings/",
        data={"display_name": "Alice PM", "title": "Director, IT", "phone": "555-1212"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    refreshed = db.session.get(User, acme_pm.id)
    assert refreshed.display_name == "Alice PM"
    assert refreshed.title == "Director, IT"
    assert refreshed.phone == "555-1212"


def test_invite_creates_row_and_returns_url(client, acme_pm, acme):
    before = db.session.query(ClientInvitation).filter_by(client_id=acme.id).count()
    r = client.post(
        "/portal/settings/invite",
        data={"email": "bob@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    # The redirect carries the invite_url as a query string param.
    assert "invite_url=" in r.headers["Location"]
    assert db.session.query(ClientInvitation).filter_by(client_id=acme.id).count() == before + 1
    inv = (
        db.session.query(ClientInvitation)
        .filter_by(client_id=acme.id, email="bob@example.com")
        .first()
    )
    assert inv is not None
    assert inv.token_hash and inv.token_hash != ""
    # The plaintext token is NOT stored — only the hash.
    assert inv.token_hash != "bob@example.com"


def test_invite_rejects_non_primary_poc(client, acme, admin):
    """A 'member' (not primary_poc) cannot invite further colleagues."""
    member = User(sub="m1", email="m1@example.com",
                  display_name="Member", role=Role.CLIENT)
    db.session.add(member)
    db.session.commit()
    db.session.add(ClientMembership(
        client_id=acme.id, user_id=member.id,
        membership_role="member",
        invited_at=datetime.utcnow(),
        accepted_at=datetime.utcnow(),
    ))
    db.session.commit()
    with client.session_transaction() as sess:
        sess["_user_id"] = member.id
        sess["_fresh"] = True

    r = client.post(
        "/portal/settings/invite",
        data={"email": "carol@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 403


def test_invite_accept_links_membership(client, acme_pm, acme):
    """The invitee uses the token URL to join an existing client."""
    # Create the invitee user first (Keycloak would normally do this).
    bob = User(sub="bob", email="bob@example.com",
               display_name="Bob", role=Role.CLIENT)
    db.session.add(bob)
    db.session.commit()

    # Create the invite as the PM.
    plaintext = "rand-token-xyz"
    inv = ClientInvitation(
        client_id=acme.id, invited_by=acme_pm.id,
        email="bob@example.com",
        token_hash=_hash_token(plaintext),
        expires_at=datetime.utcnow() + timedelta(days=7),
    )
    db.session.add(inv)
    db.session.commit()

    # Switch the test client to bob and POST the accept route.
    with client.session_transaction() as sess:
        sess["_user_id"] = bob.id
        sess["_fresh"] = True

    r = client.post(f"/portal/invitations/accept/{plaintext}",
                    follow_redirects=False)
    assert r.status_code == 302
    # Bob is now a member of Acme.
    m = (
        db.session.query(ClientMembership)
        .filter_by(client_id=acme.id, user_id=bob.id)
        .first()
    )
    assert m is not None
    assert m.membership_role == "member"
    assert m.accepted_at is not None
    # Audit row recorded.
    assert (
        db.session.query(AuditEntry)
        .filter_by(action="client.user_joined", target_id=acme.id)
        .count()
        >= 1
    )


def test_invite_accept_rejects_wrong_email(client, acme_pm, acme):
    """Forwarded link used by the wrong account fails closed.

    Per round-2 §8: invitations are tied to an email. If the
    logged-in user's email doesn't match, refuse with 403.
    """
    plaintext = "wrong-acct-token"
    inv = ClientInvitation(
        client_id=acme.id, invited_by=acme_pm.id,
        email="alice@example.com",  # invited alice
        token_hash=_hash_token(plaintext),
        expires_at=datetime.utcnow() + timedelta(days=7),
    )
    db.session.add(inv)
    db.session.commit()
    # acme_pm's email is pp@example.com — mismatch.
    r = client.post(f"/portal/invitations/accept/{plaintext}",
                    follow_redirects=False)
    assert r.status_code == 403


def test_invite_accept_404_when_expired(client, acme_pm, acme):
    plaintext = "expired-token"
    inv = ClientInvitation(
        client_id=acme.id, invited_by=acme_pm.id,
        email="pp@example.com",
        token_hash=_hash_token(plaintext),
        expires_at=datetime.utcnow() - timedelta(seconds=1),
    )
    db.session.add(inv)
    db.session.commit()
    r = client.get(f"/portal/invitations/accept/{plaintext}",
                   follow_redirects=False)
    assert r.status_code == 404


def test_invite_accept_404_when_revoked(client, acme_pm, acme):
    plaintext = "revoked-token"
    inv = ClientInvitation(
        client_id=acme.id, invited_by=acme_pm.id,
        email="pp@example.com",
        token_hash=_hash_token(plaintext),
        expires_at=datetime.utcnow() + timedelta(days=7),
        revoked_at=datetime.utcnow(),
    )
    db.session.add(inv)
    db.session.commit()
    r = client.get(f"/portal/invitations/accept/{plaintext}",
                   follow_redirects=False)
    assert r.status_code == 404
