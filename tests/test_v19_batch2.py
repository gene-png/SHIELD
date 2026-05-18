"""v1.9 batch 2 — list filters, notifications bell, P2 auto-progression.

Pins:
  - List pages emit the filter controls + `data-shield-filter` table
    attribute the JS hooks onto.
  - Notifications bell renders in the secondary nav, shows the
    unread-count badge when items exist, and the index page marks
    items read on visit.
  - P2 submit and P2 set_target call into the auto-progression hooks.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from shield.extensions import db
from shield.models import (
    AuditEntry,
    Notification,
    PlatformType,
    Project,
    QuestionnaireResponse,
    TrustTier,
)
from shield.spine.repository import write_human_artifact


# --------------------------------------------------------------------
# List filters
# --------------------------------------------------------------------

@pytest.mark.parametrize("path,key", [
    ("/clients/",                "clients"),
    ("/platform/tech-debt/",     "p1"),
    ("/platform/zero-trust/",    "p2"),
    ("/platform/attack-surface/", "p3"),
])
def test_list_pages_emit_shield_filter_table(admin_client, path, key):
    r = admin_client.get(path)
    assert r.status_code == 200, f"{path} returned {r.status_code}"
    expected = f'data-shield-filter="{key}"'.encode()
    assert expected in r.data, (
        f"List page {path} should have a `data-shield-filter=\"{key}\"` table"
    )


def test_list_pages_include_search_input(admin_client):
    r = admin_client.get("/clients/")
    body = r.data
    assert b'data-shield-filter-search="clients"' in body
    assert b'type="search"' in body


def test_p1_index_includes_stage_filter(admin_client):
    r = admin_client.get("/platform/tech-debt/")
    assert b'data-shield-filter-stage="p1"' in r.data


# --------------------------------------------------------------------
# Notifications bell
# --------------------------------------------------------------------

def test_bell_renders_with_no_badge_when_no_unread(admin_client):
    r = admin_client.get("/clients/queue")
    body = r.data
    # Bell link exists.
    assert b'class="shield-bell"' in body
    # No unread → no badge element.
    assert b"shield-bell__badge" not in body


def test_bell_shows_badge_with_unread_count(admin_client, app, admin):
    db.session.add(Notification(
        user_id=admin.id,
        event_type="client.service_requested",
        title="Test notification",
    ))
    db.session.commit()
    r = admin_client.get("/clients/queue")
    body = r.data
    assert b"shield-bell__badge" in body
    assert b">1<" in body  # unread count = 1


def test_notifications_index_marks_items_read(admin_client, app, admin):
    n = Notification(user_id=admin.id,
                     event_type="x", title="t")
    db.session.add(n)
    db.session.commit()
    r = admin_client.get("/notifications/")
    assert r.status_code == 200
    db.session.refresh(n)
    assert n.read_at is not None


def test_mark_read_endpoint_clears_unread(admin_client, app, admin):
    db.session.add(Notification(user_id=admin.id, event_type="x", title="t"))
    db.session.add(Notification(user_id=admin.id, event_type="x", title="t2"))
    db.session.commit()
    r = admin_client.post("/notifications/mark-read", follow_redirects=False)
    assert r.status_code == 302
    unread = (db.session.query(Notification)
              .filter_by(user_id=admin.id, read_at=None).count())
    assert unread == 0


# --------------------------------------------------------------------
# P2 auto-progression wiring
# --------------------------------------------------------------------

@pytest.fixture()
def p2_admin_project(app, acme, admin):
    p = Project(
        client_id=acme.id, platform=PlatformType.ZERO_TRUST,
        name="auto-progress P2", stage="intake",
        framework="cisa_ztmm_v2",
        created_by_id=admin.id,
    )
    db.session.add(p)
    db.session.commit()
    return p


def test_p2_submit_writes_auto_progress_audit(
    admin_client, p2_admin_project, admin,
):
    db.session.add(QuestionnaireResponse(
        project_id=p2_admin_project.id, framework="cisa_ztmm_v2",
        control_id="USER.1.1", answer="partial",
        trust_tier=TrustTier.ADMIN_ASSISTED, attributed_user_id=admin.id,
    ))
    db.session.commit()
    r = admin_client.post(
        f"/platform/zero-trust/project/{p2_admin_project.id}/submit",
        follow_redirects=False,
    )
    assert r.status_code == 302
    auto = (db.session.query(AuditEntry)
            .filter_by(action="auto_progress.p2_assessment_queued").first())
    assert auto is not None


def test_p2_set_target_with_current_state_present_queues_roadmap(
    admin_client, p2_admin_project, admin,
):
    """When a current-state assessment artifact already exists, saving
    a desired_future_state should fire the roadmap auto-progress hook."""
    from shield.spine.repository import write_ai_artifact
    write_ai_artifact(
        project=p2_admin_project, stage="current_state_assessment",
        title="cs", body_text="{}",
        input_artifact_ids=[],
        prompt_version="p2_assessment.v1", model="fixture",
    )
    # Use a real control id from the loaded CISA ZTMM catalog.
    from shield.p2_zerotrust.frameworks import FRAMEWORKS
    fw = FRAMEWORKS.get(p2_admin_project.framework)
    cid = fw.controls[0].id
    r = admin_client.post(
        f"/platform/zero-trust/project/{p2_admin_project.id}/set-target",
        data={"notes": "test target",
              f"target_{cid}": "implemented"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    auto = (db.session.query(AuditEntry)
            .filter_by(action="auto_progress.p2_roadmap_queued").first())
    assert auto is not None
