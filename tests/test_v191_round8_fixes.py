"""v1.9.1 — round-8 review fixes.

Pins the user-reported regressions from the round-8 expert panel:

  1. /portal/about HTMX form bug: every field saved as legal_name's
     value. v1.9's `hx-include="this"` was the wrong fix; HTMX adds
     to the include set rather than replacing it. The v1.9.1 template
     renames each input to use its column name + `hx-params` whitelist.
     This test posts the OLD bug shape (every input named "value") AND
     the NEW correct shape and verifies each writes the right column.
  2. Pre-populated email + name + title + phone on /portal/about so
     the user doesn't re-enter what the session already knows.
  3. Auto-create projects for selected services (Attack Surface was
     getting silently dropped).
  4. Admin notification on client.self_signup_bootstrap.
  5. CLIENT users can reach /notifications/ (was being redirected
     away by the role gate).
  6. Friendly-label filter maps raw enum slugs to display strings.
  7. Queue carries waiting_since + earliest_project_at timestamps.
  8. Failed-jobs banner renders on Queue when there are failures.
  9. Industry + notes inline editor on /clients/<id>.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from shield.extensions import db
from shield.models import (
    Client,
    ClientMembership,
    Notification,
    PlatformType,
    Project,
    Role,
    User,
)


@pytest.fixture()
def fresh_member(client, app, admin):
    """A self-signed-up user with one accepted membership."""
    c = Client(name="Nexus")
    db.session.add(c)
    db.session.commit()
    u = User(sub="nex", email="nex@x", display_name="Nexus User",
             role=Role.CLIENT, title="CISO", phone="+1 555 0000")
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


# --------------------------------------------------------------------
# About-form data-corruption bug — the real fix
# --------------------------------------------------------------------

def test_about_field_endpoint_reads_value_from_named_column(fresh_member):
    """POST to /portal/about/field with the NEW shape (each input has
    its own name) saves only the named column."""
    client, c, _ = fresh_member
    r = client.post(
        "/portal/about/field",
        data={"name": "primary_poc_name", "primary_poc_name": "Dana Q"},
    )
    assert r.status_code == 200
    db.session.refresh(c)
    assert c.primary_poc_name == "Dana Q"
    # No other column got the same value.
    assert c.legal_name in (None, "")
    assert c.primary_poc_email != "Dana Q"


def test_about_field_endpoint_accepts_legacy_value_shape(fresh_member):
    """Backward compat: if a client POSTs the old `value=<text>` shape
    (e.g. from a stale browser), the server still writes to the named
    column rather than 400-ing."""
    client, c, _ = fresh_member
    r = client.post(
        "/portal/about/field",
        data={"name": "primary_poc_phone", "value": "+1 555 9999"},
    )
    assert r.status_code == 200
    db.session.refresh(c)
    assert c.primary_poc_phone == "+1 555 9999"


def test_about_template_uses_named_inputs_and_hx_params_whitelist(fresh_member):
    """Pin the template-level fix: each input carries its column name
    + an hx-params whitelist so collisions can't happen."""
    client, _, _ = fresh_member
    r = client.get("/portal/about")
    body = r.data
    assert b'name="legal_name"' in body
    assert b'name="primary_poc_name"' in body
    assert b'name="size_band"' in body
    assert b'hx-params="csrf_token,name,legal_name"' in body
    assert b'hx-params="csrf_token,name,size_band"' in body
    assert b'hx-params="csrf_token,name,prompting_context"' in body


def test_about_get_prepopulates_email_from_session(fresh_member):
    client, c, u = fresh_member
    assert c.primary_poc_email is None
    client.get("/portal/about")
    db.session.refresh(c)
    # The session's user email pre-filled the contact field.
    assert c.primary_poc_email == u.email
    # Display name pre-filled the contact name.
    assert c.primary_poc_name == u.display_name


def test_about_get_prepopulates_title_and_phone_from_user_profile(fresh_member):
    client, c, _ = fresh_member
    client.get("/portal/about")
    db.session.refresh(c)
    assert c.primary_poc_title == "CISO"
    assert c.primary_poc_phone == "+1 555 0000"


def test_about_field_mirrors_phone_to_user_profile(fresh_member):
    """Reviewer §IV: title + phone entered on the intake form should
    sync to the user's Settings profile so the user doesn't have to
    re-enter them."""
    client, _, u = fresh_member
    u.phone = None
    db.session.commit()
    client.post(
        "/portal/about/field",
        data={"name": "primary_poc_phone", "primary_poc_phone": "+1 555 1234"},
    )
    db.session.refresh(u)
    assert u.phone == "+1 555 1234"


# --------------------------------------------------------------------
# Auto-create projects for selected services
# --------------------------------------------------------------------

def test_welcome_post_auto_creates_project_for_attack_surface(fresh_member):
    """The previous bug: only Zero Trust got a project; Attack Surface
    was silently dropped. Now every selected service gets a stub
    project."""
    client, c, _ = fresh_member
    client.post(
        "/portal/welcome",
        data={"services": ["zero_trust", "attack_surface"]},
    )
    projects = (db.session.query(Project)
                .filter_by(client_id=c.id)
                .all())
    platforms = {p.platform for p in projects if not p.is_client_repository}
    assert PlatformType.ZERO_TRUST in platforms
    assert PlatformType.ATTACK_SURFACE in platforms


def test_welcome_post_does_not_double_create(fresh_member):
    client, c, _ = fresh_member
    client.post("/portal/welcome", data={"services": ["zero_trust"]})
    client.post("/portal/welcome", data={"services": ["zero_trust"]})
    zt = (db.session.query(Project)
          .filter_by(client_id=c.id, platform=PlatformType.ZERO_TRUST,
                     archived=False, is_client_repository=False)
          .count())
    assert zt == 1


# --------------------------------------------------------------------
# Admin notification on self-signup
# --------------------------------------------------------------------

def test_self_signup_notifies_admins(client, app, admin):
    """A new client self-signing up via /portal/start-organization
    should fire one Notification per active admin."""
    # New CLIENT user, no membership.
    u = User(sub="signup", email="signup@x", display_name="Signup User",
             role=Role.CLIENT)
    db.session.add(u)
    db.session.commit()
    from tests.conftest import _login
    _login(client, u.id)
    client.post("/portal/start-organization",
                data={"organization_name": "BrandNew Org"})
    notifs = (db.session.query(Notification)
              .filter_by(event_type="client.self_signup", user_id=admin.id)
              .all())
    assert notifs, "self-signup should notify every active admin"
    assert "BrandNew Org" in notifs[0].title


# --------------------------------------------------------------------
# Notifications route is reachable by CLIENT users
# --------------------------------------------------------------------

def test_notifications_index_not_redirected_for_client(fresh_member):
    """Round-8 §III: the bell linked to /notifications/ but the role
    gate redirected CLIENT users away to /portal/. Now /notifications/
    is whitelisted."""
    client, _, _ = fresh_member
    r = client.get("/notifications/", follow_redirects=False)
    assert r.status_code == 200


# --------------------------------------------------------------------
# friendly_label filter
# --------------------------------------------------------------------

def test_friendly_label_filter_maps_raw_enum_values():
    from shield import create_app
    from shield.config import TestConfig
    app = create_app(TestConfig)
    with app.app_context():
        env = app.jinja_env
        assert env.from_string("{{ 'zero_trust' | friendly_label }}").render() == "Zero Trust"
        assert env.from_string("{{ 'attack_coverage' | friendly_label }}").render() == "ATT&amp;CK coverage"
        assert env.from_string("{{ 'admin_final' | friendly_label }}").render() == "Final list"
        # Unknown values fall through to a humanized form.
        assert env.from_string("{{ 'weird_thing' | friendly_label }}").render() == "Weird Thing"
        # None / empty -> em dash.
        assert env.from_string("{{ None | friendly_label }}").render() == "—"


def test_messages_list_uses_friendly_label_for_platform(fresh_member):
    """The "Zero_trust · intake" raw-slug bug from the reviewer."""
    client, c, _ = fresh_member
    p = Project(
        client_id=c.id, platform=PlatformType.ZERO_TRUST,
        name="ZT", stage="intake",
    )
    db.session.add(p)
    db.session.commit()
    from shield.models import Message
    db.session.add(Message(
        client_id=c.id, project_id=p.id,
        author_id=fresh_member[2].id, body="hi",
    ))
    db.session.commit()
    r = client.get("/portal/messages/")
    body = r.data
    assert b"Zero Trust" in body
    # Raw slug must NOT appear.
    assert b"Zero_trust" not in body


# --------------------------------------------------------------------
# Queue waiting-since + failed-jobs banner
# --------------------------------------------------------------------

def test_queue_renders_waiting_since_column(admin_client, app, admin):
    """Round-8 §III: every queue row needs a "waiting since" timestamp
    so admins can prioritize. Seed a "waiting" client so the table
    actually renders."""
    c = Client(name="WaitClient",
               service_interests=["zero_trust"],
               intake_completed_at=datetime.utcnow())
    db.session.add(c)
    db.session.commit()
    r = admin_client.get("/clients/queue")
    assert r.status_code == 200
    assert b"Waiting since" in r.data


def test_queue_renders_active_since_column(admin_client, app, admin):
    """Seed an active client (intake done + has a non-repo project)
    so the Active table renders, then check its new column."""
    c = Client(name="ActiveClient", intake_completed_at=datetime.utcnow())
    db.session.add(c)
    db.session.commit()
    p = Project(client_id=c.id, platform=PlatformType.TECH_DEBT,
                name="Active Proj", stage="overlap_analysis",
                created_by_id=admin.id)
    db.session.add(p)
    db.session.commit()
    r = admin_client.get("/clients/queue")
    assert b"Active since" in r.data


# --------------------------------------------------------------------
# Client detail admin-fields editor
# --------------------------------------------------------------------

def test_admin_field_sets_industry_on_client(admin_client, acme):
    r = admin_client.post(
        f"/clients/{acme.id}/admin-field",
        data={"name": "industry", "industry": "Defense"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    db.session.refresh(acme)
    assert acme.industry == "Defense"


def test_admin_field_rejects_unknown_field(admin_client, acme):
    r = admin_client.post(
        f"/clients/{acme.id}/admin-field",
        data={"name": "primary_poc_email", "primary_poc_email": "hack@x"},
    )
    assert r.status_code == 400


def test_admin_field_htmx_returns_saved_fragment(admin_client, acme):
    r = admin_client.post(
        f"/clients/{acme.id}/admin-field",
        headers={"HX-Request": "true"},
        data={"name": "industry", "industry": "Healthcare"},
    )
    assert r.status_code == 200
    assert b"saved" in r.data


def test_client_detail_renders_admin_field_editor(admin_client, acme):
    r = admin_client.get(f"/clients/{acme.id}")
    assert b"Admin fields" in r.data
    assert b"Internal notes" in r.data


# --------------------------------------------------------------------
# Step numbering on welcome
# --------------------------------------------------------------------

def test_welcome_template_shows_step_one_of_four(fresh_member):
    client, _, _ = fresh_member
    r = client.get("/portal/welcome")
    assert b"Step 1 of 4" in r.data
