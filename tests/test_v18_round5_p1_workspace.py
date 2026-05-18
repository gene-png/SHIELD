"""Tests for the P1 workspace step-flow rebuild (round-5 §6.1).

The 3-column lane view ("Client source documentation" / "Automated drafts"
/ "Your reviewed versions") is gone. The new layout is a vertical
4-step flow where each step shows its status (done / active / waiting),
a short recap, and a primary action button when relevant.
"""
from __future__ import annotations

import json

import pytest

from shield.extensions import db
from shield.models import PlatformType, Project
from shield.spine.repository import (
    write_ai_artifact,
    write_human_ai_informed_artifact,
    write_human_artifact,
)


@pytest.fixture()
def p1_project(app, acme, admin):
    p = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="P1 step-flow test", stage="intake", created_by_id=admin.id,
    )
    db.session.add(p)
    db.session.commit()
    return p


# --------------------------------------------------------------------
# State machine — one fixture per stage state
# --------------------------------------------------------------------

def test_workspace_renders_four_steps(admin_client, p1_project):
    """Empty project: 4 step cards render, each with 'Step N' label."""
    r = admin_client.get(f"/platform/tech-debt/project/{p1_project.id}")
    assert r.status_code == 200
    for n in (b"Step 1", b"Step 2", b"Step 3", b"Step 4"):
        assert n in r.data


def test_workspace_step1_waiting_when_no_source(admin_client, p1_project):
    r = admin_client.get(f"/platform/tech-debt/project/{p1_project.id}")
    assert r.status_code == 200
    assert b"No client source documentation yet" in r.data


def test_workspace_step1_active_when_source_uploaded(
    admin_client, p1_project, admin,
):
    write_human_artifact(
        project=p1_project, stage="raw_intake",
        title="inventory.csv",
        file_stream=None, filename=None, mime_type=None,
        actor=admin, body_text="…",
    )
    r = admin_client.get(f"/platform/tech-debt/project/{p1_project.id}")
    # Step 1 done OR active; "Run automated reading" CTA appears.
    assert b"Run automated reading" in r.data
    assert b"inventory.csv" in r.data


def test_workspace_step2_active_when_extraction_exists(
    admin_client, p1_project, admin,
):
    write_human_artifact(
        project=p1_project, stage="raw_intake",
        title="inv.csv", file_stream=None, filename=None, mime_type=None,
        actor=admin, body_text="x",
    )
    write_ai_artifact(
        project=p1_project, stage="ai_extraction",
        title="AI extraction",
        body_text=json.dumps([{"name": "Splunk"}]),
        input_artifact_ids=[],
        prompt_version="p1_extraction.v1", model="fixture",
    )
    r = admin_client.get(f"/platform/tech-debt/project/{p1_project.id}")
    assert b"Review and confirm" in r.data
    # Action URL points at review_extraction.
    assert b"/review/" in r.data


def test_workspace_step3_active_when_review_done(
    admin_client, p1_project, admin,
):
    write_human_ai_informed_artifact(
        project=p1_project, stage="extraction_review",
        title="reviewed",
        body_text=json.dumps([{"name": "Splunk"}]),
        cites_artifact_ids=[], actor=admin,
    )
    r = admin_client.get(f"/platform/tech-debt/project/{p1_project.id}")
    # Step 3 active → "Run overlap analysis" CTA.
    assert b"Run overlap analysis" in r.data


def test_workspace_step3_done_links_to_summary(
    admin_client, p1_project, admin,
):
    write_human_ai_informed_artifact(
        project=p1_project, stage="extraction_review",
        title="r", body_text="[]", cites_artifact_ids=[], actor=admin,
    )
    write_ai_artifact(
        project=p1_project, stage="overlap_analysis",
        title="overlap",
        body_text=json.dumps({"overlaps": [], "summary": {}}),
        input_artifact_ids=[],
        prompt_version="p1_overlap.v1", model="fixture",
    )
    r = admin_client.get(f"/platform/tech-debt/project/{p1_project.id}")
    assert b"Open overlap dashboard" in r.data
    assert b"/summary" in r.data


def test_workspace_step4_active_when_reviews_exist_no_final(
    admin_client, p1_project, admin,
):
    write_human_ai_informed_artifact(
        project=p1_project, stage="extraction_review",
        title="r", body_text="[]", cites_artifact_ids=[], actor=admin,
    )
    r = admin_client.get(f"/platform/tech-debt/project/{p1_project.id}")
    assert b"Finalize the list" in r.data
    assert b"/finalize" in r.data


def test_workspace_step4_done_when_final_exists(
    admin_client, p1_project, admin,
):
    write_human_ai_informed_artifact(
        project=p1_project, stage="extraction_review",
        title="r", body_text="[]", cites_artifact_ids=[], actor=admin,
    )
    write_human_ai_informed_artifact(
        project=p1_project, stage="admin_final",
        title="Final list", body_text="[]",
        cites_artifact_ids=[], actor=admin,
    )
    r = admin_client.get(f"/platform/tech-debt/project/{p1_project.id}")
    assert b"View final list" in r.data


# --------------------------------------------------------------------
# Chat panel only appears once the reviewed version exists
# --------------------------------------------------------------------

def test_chat_panel_hidden_before_review(admin_client, p1_project):
    r = admin_client.get(f"/platform/tech-debt/project/{p1_project.id}")
    assert b"Questions for the overlap findings" not in r.data


def test_chat_panel_visible_after_review(admin_client, p1_project, admin):
    write_human_ai_informed_artifact(
        project=p1_project, stage="extraction_review",
        title="r", body_text="[]", cites_artifact_ids=[], actor=admin,
    )
    r = admin_client.get(f"/platform/tech-debt/project/{p1_project.id}")
    assert b"Questions for the overlap findings" in r.data


# --------------------------------------------------------------------
# Old 3-column layout is gone
# --------------------------------------------------------------------

def test_workspace_does_not_render_old_3_column_labels(admin_client, p1_project):
    r = admin_client.get(f"/platform/tech-debt/project/{p1_project.id}")
    # The old column headings shouldn't be on the new step-flow layout.
    assert b"Automated drafts" not in r.data
    assert b"Your reviewed versions" not in r.data
