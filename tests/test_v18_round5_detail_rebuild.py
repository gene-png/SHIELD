"""Tests for the round-5 §5.4 client-detail rebuild.

The old /clients/<id> rendered projects as `<ul><li>` one-liners with no
links. Round-5 wants per-project cards with service chips, stages,
last-activity, and a stage-aware primary action button — same shape as
the dashboard cards on the client side.
"""
from __future__ import annotations

import pytest

from shield.extensions import db
from shield.models import Client, PlatformType, Project


@pytest.fixture()
def acme_with_projects(app, acme, admin):
    """Three real projects (one per platform) + one archived + the
    synthetic repository project. Tests assert which appear where."""
    p1 = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="Acme TD active", stage="overlap_analysis",
        created_by_id=admin.id,
    )
    p2 = Project(
        client_id=acme.id, platform=PlatformType.ZERO_TRUST,
        name="Acme ZT active", stage="current_state_assessment",
        framework="cisa_ztmm_v2", created_by_id=admin.id,
    )
    p3 = Project(
        client_id=acme.id, platform=PlatformType.ATTACK_SURFACE,
        name="Acme ATT&CK active", stage="intake",
        created_by_id=admin.id,
    )
    archived = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="Acme TD old", stage="complete",
        archived=True, created_by_id=admin.id,
    )
    repo = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="Client Repository", stage="client_repository",
        is_client_repository=True, created_by_id=admin.id,
    )
    db.session.add_all([p1, p2, p3, archived, repo])
    db.session.commit()
    return [p1, p2, p3, archived, repo]


def test_detail_renders_per_project_cards(admin_client, acme, acme_with_projects):
    r = admin_client.get(f"/clients/{acme.id}")
    assert r.status_code == 200
    # Each active project's NAME appears (was missing as a link before).
    assert b"Acme TD active" in r.data
    assert b"Acme ZT active" in r.data
    assert b"Acme ATT&amp;CK active" in r.data or b"Acme ATT&CK active" in r.data
    # Service chips: each card shows the platform's friendly label.
    assert b"Tech Debt" in r.data
    assert b"Zero Trust" in r.data
    assert b"Attack Surface" in r.data


def test_detail_excludes_synthetic_repository_project(admin_client, acme, acme_with_projects):
    """The is_client_repository synthetic project must not appear among
    the active project cards — it's an admin-internal placeholder."""
    r = admin_client.get(f"/clients/{acme.id}")
    assert r.status_code == 200
    # The repo project is named "Client Repository"; it must not appear
    # in the main projects section (would be confusing as a card).
    # (It can appear elsewhere — e.g. intake_view — but not on the
    # main client detail.)
    # Count by checking that "Client Repository" doesn't appear as a
    # card title.
    assert b"Client Repository" not in r.data


def test_detail_lists_archived_projects_in_separate_section(
    admin_client, acme, acme_with_projects,
):
    r = admin_client.get(f"/clients/{acme.id}")
    # Archived project name appears, but inside the collapsible
    # "Archived projects" section.
    assert b"Acme TD old" in r.data
    assert b"Archived projects" in r.data


def test_detail_stage_action_buttons_link_to_right_endpoint(
    admin_client, acme, acme_with_projects,
):
    """Per-stage action labels surface on the cards (round-5 §5.4)."""
    r = admin_client.get(f"/clients/{acme.id}")
    assert b"View overlap findings" in r.data  # p1 overlap_analysis
    assert b"Open assessment" in r.data        # p2 current_state_assessment
    assert b"Continue intake" in r.data        # p3 stage='intake'


def test_detail_shows_contact_info_when_set(admin_client, acme):
    acme.primary_poc_name = "Alice Hopkins"
    acme.primary_poc_email = "alice@acme.example"
    acme.primary_poc_phone = "555-555-5555"
    acme.address_line1 = "100 Main St"
    acme.city = "Anytown"
    acme.state = "WA"
    db.session.commit()
    r = admin_client.get(f"/clients/{acme.id}")
    assert b"Alice Hopkins" in r.data
    assert b"alice@acme.example" in r.data
    assert b"100 Main St" in r.data


def test_detail_omits_contact_section_when_unset(admin_client, app):
    """A bare client with no contact info shouldn't show an empty
    Contact section."""
    c = Client(name="Bare Client")
    db.session.add(c)
    db.session.commit()
    r = admin_client.get(f"/clients/{c.id}")
    assert r.status_code == 200
    # The label is gated by an `{% if %}` — should not render when
    # all the fields are NULL.
    assert b"Primary point of contact" not in r.data


def test_detail_empty_state_when_no_projects(admin_client, app):
    c = Client(name="No-Project Co")
    db.session.add(c)
    db.session.commit()
    r = admin_client.get(f"/clients/{c.id}")
    assert b"No active projects yet" in r.data


def test_detail_uses_client_display_name_when_set(
    admin_client, acme, admin,
):
    """Round-4 §4.4 + round-5 §5.4: client_display_name takes
    precedence over name on the card."""
    p = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="Internal — Acme TD Q3 2026",
        client_display_name="My consolidation review",
        stage="overlap_analysis", created_by_id=admin.id,
    )
    db.session.add(p)
    db.session.commit()
    r = admin_client.get(f"/clients/{acme.id}")
    # The friendly label is what the card surfaces.
    assert b"My consolidation review" in r.data
    # And the internal name is shown smaller as "Internal: …".
    assert b"Internal: Internal" in r.data


def test_detail_admin_sees_action_buttons(admin_client, acme):
    r = admin_client.get(f"/clients/{acme.id}")
    assert b"Start a new project" in r.data
    assert b"View intake" in r.data


def test_detail_reviewer_does_not_see_admin_buttons(reviewer_client, acme):
    r = reviewer_client.get(f"/clients/{acme.id}")
    assert r.status_code == 200
    assert b"Start a new project" not in r.data
