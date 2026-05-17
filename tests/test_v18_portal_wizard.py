"""End-to-end-ish tests for the v1.8 portal wizard (PR 3).

Covers the four steps:
  1. /portal/welcome — service-selection
  2. /portal/about   — per-field HTMX auto-save
  3. /portal/documents — drag-drop upload to the synthetic project
  4. /portal/confirm — sets intake_completed_at

Plus the home redirect: a CLIENT user with intake_completed_at = NULL
lands on /portal/welcome; with it set, lands on /portal/.

These tests use the existing `acme` fixture + a fresh CLIENT user
with a `ClientMembership` to Acme.
"""
from __future__ import annotations

import io
from datetime import datetime

import pytest

from shield.extensions import db
from shield.models import (
    Artifact,
    AuditEntry,
    Client,
    ClientMembership,
    Origin,
    Project,
    Role,
    User,
)


@pytest.fixture()
def acme_member(client, acme, admin):
    """A CLIENT-role user who is an accepted member of Acme."""
    u = User(sub="pm1", email="pm1@example.com",
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
    # Ensure the synthetic client-repository project exists. The
    # TestConfig schema is freshly created from the model metadata
    # (no migration), so the backfill that creates it in production
    # doesn't run — we create it here for tests.
    repo = (
        db.session.query(Project)
        .filter_by(client_id=acme.id, is_client_repository=True)
        .first()
    )
    if repo is None:
        from shield.models import PlatformType
        repo = Project(
            client_id=acme.id, platform=PlatformType.TECH_DEBT,
            name="Client Repository", stage="client_repository",
            is_client_repository=True, created_by_id=admin.id,
        )
        db.session.add(repo)
        db.session.commit()
    with client.session_transaction() as sess:
        sess["_user_id"] = u.id
        sess["_fresh"] = True
    return u


# --------------------------------------------------------------------
# Home redirect
# --------------------------------------------------------------------

def test_home_redirects_client_with_null_intake_to_welcome(client, acme_member):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/portal/welcome")


def test_home_redirects_client_with_completed_intake_to_dashboard(
    client, acme_member, acme,
):
    acme.intake_completed_at = datetime.utcnow()
    db.session.commit()
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/portal/")


# --------------------------------------------------------------------
# Step 1: welcome
# --------------------------------------------------------------------

def test_welcome_renders_three_service_cards(client, acme_member):
    r = client.get("/portal/welcome")
    assert r.status_code == 200
    assert b"Tech Debt" in r.data
    assert b"Zero Trust" in r.data
    assert b"Attack Surface" in r.data
    assert b"I'm not sure" in r.data


def test_welcome_save_writes_service_interests_and_audits(
    client, acme_member, acme,
):
    before = db.session.query(AuditEntry).filter_by(
        action="client.service_interest_changed",
    ).count()
    from werkzeug.datastructures import MultiDict
    r = client.post(
        "/portal/welcome",
        data=MultiDict([
            ("services", "tech_debt"),
            ("services", "attack_surface"),
            ("consult_requested", "yes"),
        ]),
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/portal/about")

    refreshed = db.session.get(Client, acme.id)
    assert set(refreshed.service_interests) == {"tech_debt", "attack_surface"}
    assert refreshed.consult_requested is True

    after = db.session.query(AuditEntry).filter_by(
        action="client.service_interest_changed",
    ).count()
    assert after == before + 1


def test_welcome_strips_unknown_service_keys(client, acme_member, acme):
    """A hand-crafted POST with bogus service strings must be filtered."""
    from werkzeug.datastructures import MultiDict
    client.post(
        "/portal/welcome",
        data=MultiDict([("services", "tech_debt"), ("services", "drop_table")]),
        follow_redirects=False,
    )
    refreshed = db.session.get(Client, acme.id)
    assert refreshed.service_interests == ["tech_debt"]


# --------------------------------------------------------------------
# Step 2: about (HTMX per-field auto-save)
# --------------------------------------------------------------------

def test_about_field_post_writes_single_column(client, acme_member, acme):
    r = client.post(
        "/portal/about/field",
        data={"name": "legal_name", "value": "Acme Industries Inc."},
    )
    assert r.status_code == 200
    assert b"saved" in r.data
    refreshed = db.session.get(Client, acme.id)
    assert refreshed.legal_name == "Acme Industries Inc."


def test_about_field_rejects_unknown_column(client, acme_member, acme):
    """The endpoint is a deliberately narrow write surface.

    The fixture leaves service_interests at the model default ([]).
    The rejection check is the 400 — we further verify legal_name
    didn't get written by accident.
    """
    r = client.post(
        "/portal/about/field",
        data={"name": "service_interests", "value": '["malicious"]'},
    )
    assert r.status_code == 400
    refreshed = db.session.get(Client, acme.id)
    # The bogus column name must not have side-channelled into the
    # service_interests JSON column — it stays at its starting value.
    assert refreshed.service_interests == []
    # And legal_name (a legitimate field, but not the one in the POST)
    # must also stay None — proves the endpoint doesn't ignore `name`.
    assert refreshed.legal_name is None


def test_about_submit_requires_primary_poc_email(client, acme_member, acme):
    # Reset email to None to trigger the validation.
    acme.primary_poc_email = None
    db.session.commit()
    r = client.post("/portal/about/submit", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/portal/about")


def test_about_submit_with_email_advances_to_documents(client, acme_member, acme):
    acme.primary_poc_email = "alice@acme.example"
    db.session.commit()
    r = client.post("/portal/about/submit", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/portal/documents")


# --------------------------------------------------------------------
# Step 3: documents
# --------------------------------------------------------------------

def test_documents_upload_lands_in_synthetic_repository_project(
    client, acme_member, acme,
):
    data = {
        "files": (io.BytesIO(b"hello"), "policy.txt"),
    }
    r = client.post(
        "/portal/documents/upload",
        data=data, content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert r.status_code == 302

    repo = db.session.query(Project).filter_by(
        client_id=acme.id, is_client_repository=True,
    ).first()
    assert repo is not None
    arts = (
        db.session.query(Artifact)
        .filter_by(project_id=repo.id, origin=Origin.HUMAN_INPUT)
        .all()
    )
    assert len(arts) == 1
    assert arts[0].client_id == acme.id
    assert arts[0].stage == "client_repository"
    assert arts[0].title == "policy.txt"


def test_documents_upload_writes_audit(client, acme_member):
    before = db.session.query(AuditEntry).filter_by(
        action="file_uploaded_to_repository",
    ).count()
    client.post(
        "/portal/documents/upload",
        data={"files": (io.BytesIO(b"hi"), "f.txt")},
        content_type="multipart/form-data",
    )
    after = db.session.query(AuditEntry).filter_by(
        action="file_uploaded_to_repository",
    ).count()
    assert after == before + 1


# --------------------------------------------------------------------
# Step 4: confirm
# --------------------------------------------------------------------

def test_confirm_post_sets_intake_completed_at(client, acme_member, acme):
    assert acme.intake_completed_at is None
    r = client.post("/portal/confirm", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/portal/")
    refreshed = db.session.get(Client, acme.id)
    assert refreshed.intake_completed_at is not None


# --------------------------------------------------------------------
# Cross-cutting: CLIENT users can't reach non-portal URLs
# --------------------------------------------------------------------

def test_client_cannot_reach_audit_after_v18(client, acme_member):
    r = client.get("/audit/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/portal/")


def test_client_is_redirected_off_legacy_intake(client, acme_member):
    """Round-3 closes the /intake/ loophole.

    The v1.8 PR 3 role gate kept /intake/ accessible to CLIENT users
    for backward compatibility. Round-3 §2.3 calls this out as a
    leak path (the legacy template renders `project.client.name` =
    "Acme Co") and tightens the gate so CLIENT users are redirected
    to /portal/ regardless of URL.
    """
    r = client.get("/intake/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/portal/")
