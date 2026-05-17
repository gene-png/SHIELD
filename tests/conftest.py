"""Pytest fixtures for SHIELD."""
from __future__ import annotations

import pytest

from shield import create_app
from shield.config import TestConfig
from shield.extensions import db
from shield.models import (
    CapabilityList,
    Origin,
    PlatformType,
    Project,
    Role,
    User,
)
from shield.models import (
    Client as ClientModel,
)


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


# --------------------------------------------------------------------
# Auth helpers — log a user in without going through Keycloak.
# --------------------------------------------------------------------

def _login(client, user_id: str) -> None:
    """Set the Flask-Login session cookies on the test client."""
    with client.session_transaction() as sess:
        sess["_user_id"] = user_id
        sess["_fresh"] = True


@pytest.fixture()
def admin(app):
    """Create + return a logged-in-able admin User."""
    u = User(sub="t-admin", email="t-admin@example.com",
             display_name="Test Admin", role=Role.ADMIN)
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture()
def client_user(app):
    """Create + return a logged-in-able CLIENT-role User."""
    u = User(sub="t-client", email="t-client@example.com",
             display_name="Test Client", role=Role.CLIENT)
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture()
def admin_client(client, admin):
    _login(client, admin.id)
    return client


@pytest.fixture()
def client_role_client(client, client_user):
    _login(client, client_user.id)
    return client


@pytest.fixture()
def acme(app, admin):
    """Test client (organization) + a v1 human-input capability list."""
    c = ClientModel(name="Acme Test")
    db.session.add(c)
    db.session.commit()
    cl = CapabilityList(client_id=c.id, version=1, label="v1 baseline",
                        origin=Origin.HUMAN_INPUT, created_by_id=admin.id)
    db.session.add(cl)
    db.session.commit()
    return c


@pytest.fixture()
def ai_capability_list(app, acme, admin):
    """An AI-origin capability list version for the acme client."""
    cl = CapabilityList(client_id=acme.id, version=2, label="AI-extracted",
                        origin=Origin.AI_GENERATED, created_by_id=admin.id)
    db.session.add(cl)
    db.session.commit()
    return cl


@pytest.fixture()
def p2_project(app, acme, admin):
    p = Project(client_id=acme.id, platform=PlatformType.ZERO_TRUST,
                name="ZT test", stage="intake", framework="cisa_ztmm_v2",
                created_by_id=admin.id)
    db.session.add(p)
    db.session.commit()
    return p
