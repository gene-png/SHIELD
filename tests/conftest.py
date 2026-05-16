"""Pytest fixtures for SHIELD."""
from __future__ import annotations

import pytest

from shield import create_app
from shield.config import TestConfig
from shield.extensions import db


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
