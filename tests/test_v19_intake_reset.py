"""v1.9 bug fix: admin can reset a client's corrupted intake fields.

The pre-v1.9 /portal/about HTMX form had an include-scope bug that
saved one field's value into every column on each blur. The form is
fixed, but rows that were corrupted before the fix stay corrupted
until an admin clears them. This admin verb does that.
"""
from __future__ import annotations

import pytest

from shield.extensions import db
from shield.models import (
    AuditEntry,
    Client,
)


@pytest.fixture()
def corrupted_client(app):
    """A client with every contact + address field set to the same
    bogus value — the exact bug shape from the production /portal/about
    flow before the v1.9 fix."""
    c = Client(
        name="PGCTC",
        legal_name="PGCTC", dba_name="PGCTC", website="PGCTC",
        size_band="PGCTC",
        primary_poc_name="PGCTC", primary_poc_title="PGCTC",
        primary_poc_email="PGCTC", primary_poc_phone="PGCTC",
        address_line1="PGCTC", city="PGCTC", state="PGCTC",
        postal_code="PGCTC", country="PGCTC",
        prompting_context="PGCTC",
        service_interests=["tech_debt", "zero_trust"],
    )
    db.session.add(c)
    db.session.commit()
    return c


def test_reset_requires_typed_confirmation(admin_client, corrupted_client):
    """Wrong (or missing) confirmation phrase = no-op."""
    r = admin_client.post(
        f"/clients/{corrupted_client.id}/intake/reset",
        data={"confirmation_phrase": "wrong"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    db.session.refresh(corrupted_client)
    # All corrupted fields are still in place.
    assert corrupted_client.primary_poc_email == "PGCTC"
    assert corrupted_client.legal_name == "PGCTC"


def test_reset_with_correct_phrase_clears_intake_fields(
    admin_client, corrupted_client,
):
    r = admin_client.post(
        f"/clients/{corrupted_client.id}/intake/reset",
        data={"confirmation_phrase": "PGCTC"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    db.session.refresh(corrupted_client)
    # Every contact + org field is now NULL.
    for col in (
        "legal_name", "dba_name", "website", "size_band",
        "primary_poc_name", "primary_poc_title", "primary_poc_email",
        "primary_poc_phone",
        "address_line1", "address_line2", "city", "state",
        "postal_code", "country", "prompting_context",
    ):
        assert getattr(corrupted_client, col) is None, (
            f"{col!r} should have been cleared"
        )


def test_reset_preserves_services_and_uploads_metadata(
    admin_client, corrupted_client,
):
    """service_interests, intake_completed_at, and the client's name +
    industry must NOT be touched — they were correct even when the
    contact fields were corrupted."""
    corrupted_client.industry = "Financial services"
    corrupted_client.service_interests = ["zero_trust"]
    from datetime import datetime
    corrupted_client.intake_completed_at = datetime(2026, 5, 1)
    db.session.commit()

    admin_client.post(
        f"/clients/{corrupted_client.id}/intake/reset",
        data={"confirmation_phrase": "PGCTC"},
    )
    db.session.refresh(corrupted_client)
    assert corrupted_client.name == "PGCTC"
    assert corrupted_client.industry == "Financial services"
    assert corrupted_client.service_interests == ["zero_trust"]
    assert corrupted_client.intake_completed_at is not None


def test_reset_writes_audit_row(admin_client, corrupted_client):
    admin_client.post(
        f"/clients/{corrupted_client.id}/intake/reset",
        data={"confirmation_phrase": "PGCTC"},
    )
    entry = (db.session.query(AuditEntry)
             .filter_by(action="client.intake_fields_reset",
                        target_id=corrupted_client.id)
             .one())
    assert entry.details.get("cleared_fields")
    assert "primary_poc_email" in entry.details["cleared_fields"]


def test_reset_blocked_for_client_role(client_role_client, corrupted_client):
    r = client_role_client.post(
        f"/clients/{corrupted_client.id}/intake/reset",
        data={"confirmation_phrase": "PGCTC"},
        follow_redirects=False,
    )
    # Role gate redirects CLIENT to /portal/ before admin_only fires.
    assert r.status_code in (302, 403, 404)
    db.session.refresh(corrupted_client)
    assert corrupted_client.primary_poc_email == "PGCTC"


def test_intake_view_renders_reset_button_for_admin(admin_client, corrupted_client):
    r = admin_client.get(f"/clients/{corrupted_client.id}/intake")
    assert r.status_code == 200
    assert b"Reset intake fields" in r.data
    assert b"reset" in r.data  # the form action URL
