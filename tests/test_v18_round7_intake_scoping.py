"""Round-7 §4 acceptance tests — intake surface is scoped to the
caller's own client memberships.

Locks in the Phase 1 outcome from the round-7 spec:

  Bug A:  A CLIENT user's intake page must not display another client's
          name. The seed (`scripts/seed.py`) used to bake "Acme Co" in
          even when the viewer didn't belong to Acme.

  Bug B:  A CLIENT user must not see projects belonging to a client
          they have no membership with.

In the v1.8+ architecture CLIENT users don't reach /intake/ directly
(they're redirected to /portal/ by the role gate in
`shield._restrict_client_to_portal`), so the test against /intake/
itself is a smoke test for the redirect. The substantive test is
calling `_client_visible_projects` with a CLIENT user authenticated
and asserting the helper itself filters by ClientMembership — that's
the defense-in-depth pin that catches future regressions if the role
gate is ever loosened.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from shield.extensions import db
from shield.models import (
    Client,
    ClientMembership,
    PlatformType,
    Project,
    Role,
    User,
)


# --------------------------------------------------------------------
# Fixtures — two clients, a member of each, and a project per client
# --------------------------------------------------------------------

@pytest.fixture()
def org_a(app):
    c = Client(name="Org A", legal_name="Organization A LLC",
               intake_completed_at=datetime.utcnow())
    db.session.add(c)
    db.session.commit()
    return c


@pytest.fixture()
def org_b(app):
    c = Client(name="Org B", legal_name="Organization B LLC",
               intake_completed_at=datetime.utcnow())
    db.session.add(c)
    db.session.commit()
    return c


@pytest.fixture()
def user_a(app, admin, org_a):
    u = User(sub="user-a", email="user-a@a.example",
             display_name="A User", role=Role.CLIENT)
    db.session.add(u)
    db.session.commit()
    db.session.add(ClientMembership(
        client_id=org_a.id, user_id=u.id,
        membership_role="primary_poc",
        invited_by_id=admin.id, accepted_at=datetime.utcnow(),
    ))
    db.session.commit()
    return u


@pytest.fixture()
def project_a(app, org_a, admin):
    p = Project(client_id=org_a.id, platform=PlatformType.TECH_DEBT,
                name="A tech-debt project", stage="intake",
                created_by_id=admin.id)
    db.session.add(p)
    db.session.commit()
    return p


@pytest.fixture()
def project_b(app, org_b, admin):
    p = Project(client_id=org_b.id, platform=PlatformType.TECH_DEBT,
                name="B tech-debt project", stage="intake",
                created_by_id=admin.id)
    db.session.add(p)
    db.session.commit()
    return p


# --------------------------------------------------------------------
# Defense-in-depth — _client_visible_projects itself filters by
# ClientMembership, independent of the role gate
# --------------------------------------------------------------------

def test_helper_returns_only_member_clients_projects(
    client, user_a, project_a, project_b,
):
    """Call `_client_visible_projects` with the CLIENT user pushed onto
    the Flask login session — the helper must filter to A's project
    only, even though /intake/ itself is gated to admins."""
    from tests.conftest import _login
    _login(client, user_a.id)
    # Use the test request context so current_user resolves.
    with client.application.test_request_context("/intake/"):
        from flask_login import login_user
        login_user(user_a)
        from shield.spine.intake import _client_visible_projects
        visible = _client_visible_projects()
    names = {p.name for p in visible}
    assert "A tech-debt project" in names
    assert "B tech-debt project" not in names


def test_helper_returns_empty_for_unbound_client_user(
    client, app, project_a, project_b,
):
    """A CLIENT-role user with zero ClientMembership rows must see an
    empty list — never a fallback that leaks every project."""
    unbound = User(sub="user-x", email="user-x@x.example",
                   display_name="Unbound", role=Role.CLIENT)
    db.session.add(unbound)
    db.session.commit()
    from tests.conftest import _login
    _login(client, unbound.id)
    with client.application.test_request_context("/intake/"):
        from flask_login import login_user
        login_user(unbound)
        from shield.spine.intake import _client_visible_projects
        assert _client_visible_projects() == []


def test_helper_excludes_client_repository_projects(
    client, user_a, org_a, admin,
):
    """The synthetic client-repository project (one per client, holds
    portal uploads pre-adoption) must NOT appear on the intake page.
    It's an internal bucket; surfacing it confuses the upload UX."""
    repo = Project(
        client_id=org_a.id, platform=PlatformType.TECH_DEBT,
        name="Client repository", stage="intake",
        is_client_repository=True, created_by_id=admin.id,
    )
    db.session.add(repo)
    db.session.commit()
    from tests.conftest import _login
    _login(client, user_a.id)
    with client.application.test_request_context("/intake/"):
        from flask_login import login_user
        login_user(user_a)
        from shield.spine.intake import _client_visible_projects
        visible = _client_visible_projects()
    assert all(not p.is_client_repository for p in visible)


# --------------------------------------------------------------------
# Role-gate smoke tests — CLIENT users never see /intake/ at all
# --------------------------------------------------------------------

def test_client_user_is_redirected_away_from_intake(
    client, user_a, project_a, project_b,
):
    """The v1.8 role gate is the primary defense against Bug B —
    CLIENT users hit /portal/, never /intake/."""
    from tests.conftest import _login
    _login(client, user_a.id)
    r = client.get("/intake/", follow_redirects=False)
    assert r.status_code == 302
    assert "/portal/" in r.headers["Location"]


def test_admin_user_still_sees_projects_across_clients(
    admin_client, project_a, project_b,
):
    """The scope tightening doesn't change ADMIN behavior — staff
    still see every open-stage project on the intake surface."""
    r = admin_client.get("/intake/")
    assert r.status_code == 200
    assert b"A tech-debt project" in r.data
    assert b"B tech-debt project" in r.data


def test_intake_template_renders_friendly_platform_label(
    admin_client, project_a,
):
    """Round-7 §4.6: the platform label is rendered in plain language,
    not inside a <code> block with the raw enum value."""
    r = admin_client.get("/intake/")
    assert r.status_code == 200
    # Friendly label appears.
    assert b"Tech Debt review" in r.data
    # Raw <code>tech debt</code> is gone.
    assert b"<code>tech debt</code>" not in r.data
