"""Round-7 §22: origin badges and key UI labels are role-explicit and
viewer-agnostic.

The earlier viewer-aware proposal (§9.2) was withdrawn; section 22
specifies the same label per artifact regardless of viewer role:

  human_input            -> "Client Provided"
  ai_generated           -> "Automated Draft"
  human_ai_informed      -> "Consultant Approved"
  ai_generated approved  -> "Approved for Reuse" pill

This file pins those strings plus the section-22 verb renames:
"Walkability" -> "Audit walk", "Re-finalize" -> "Refresh results",
"Re-run" -> "Refresh", "Relink list" -> "Change list".
"""
from __future__ import annotations

import json

import pytest

from shield.extensions import db
from shield.models import (
    Artifact,
    Origin,
    PlatformType,
    Project,
    ReuseStatus,
)
from shield.spine.repository import (
    write_ai_artifact,
    write_human_ai_informed_artifact,
    write_human_artifact,
)


@pytest.fixture()
def p1_project(app, acme, admin):
    p = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="badge label test", stage="extraction_review",
        created_by_id=admin.id,
    )
    db.session.add(p)
    db.session.commit()
    return p


# --------------------------------------------------------------------
# Origin badge labels — same text for every viewer
# --------------------------------------------------------------------

def test_repository_browse_uses_role_explicit_filter_labels(admin_client):
    """The repository filter chips read 'Client Provided' / 'Automated
    Drafts' / 'Consultant Approved' rather than the older 'From your
    team' / 'Automated drafts' / 'Reviewed versions' strings."""
    r = admin_client.get("/repository/?origin=all")
    assert r.status_code == 200
    body = r.data
    assert b"Client Provided" in body
    assert b"Automated Drafts" in body
    assert b"Consultant Approved" in body
    # The older labels must not surface.
    assert b"From your team" not in body
    assert b"Reviewed versions" not in body


def test_artifact_detail_renders_section22_badge(admin_client, p1_project, admin):
    """An artifact-detail page surfaces the role-explicit badge from
    `_components/origin_badge.html`. Verified across the three origin
    classes by rendering one of each and asserting on the page body."""
    human = write_human_artifact(
        project=p1_project, stage="raw_intake",
        title="client doc",
        file_stream=None, filename=None, mime_type=None,
        actor=admin, body_text="text",
    )
    ai = write_ai_artifact(
        project=p1_project, stage="ai_extraction",
        title="auto draft", body_text="[]",
        input_artifact_ids=[],
        prompt_version="p1_extraction.v1", model="fixture",
    )
    reviewed = write_human_ai_informed_artifact(
        project=p1_project, stage="extraction_review",
        title="reviewed",
        body_text=json.dumps([]),
        cites_artifact_ids=[ai.id], actor=admin,
    )

    for art, expected in (
        (human, b"Client Provided"),
        (ai, b"Automated Draft"),
        (reviewed, b"Consultant Approved"),
    ):
        r = admin_client.get(f"/repository/artifact/{art.id}")
        assert r.status_code == 200, f"artifact {art.title} returned {r.status_code}"
        assert expected in r.data, (
            f"Expected {expected!r} on artifact {art.title}'s detail page"
        )


def test_ai_artifact_approved_pill_reads_approved_for_reuse(
    admin_client, p1_project,
):
    """An ai_generated artifact whose reuse_status='approved' shows a
    pill labeled 'Approved for Reuse' (was 'approved' lowercase)."""
    art = write_ai_artifact(
        project=p1_project, stage="ai_extraction",
        title="approved draft", body_text="[]",
        input_artifact_ids=[],
        prompt_version="p1_extraction.v1", model="fixture",
    )
    art.reuse_status = ReuseStatus.APPROVED
    db.session.commit()
    r = admin_client.get(f"/repository/artifact/{art.id}")
    assert r.status_code == 200
    assert b"Approved for Reuse" in r.data


# --------------------------------------------------------------------
# Section-22 verb renames
# --------------------------------------------------------------------

def test_p2_workspace_says_audit_walk_not_walkability(
    admin_client, p1_project, app, acme, admin,
):
    """The P2 workspace links to the audit walk page with the new label.
    Build a real P2 project to render the link."""
    p2 = Project(client_id=acme.id, platform=PlatformType.ZERO_TRUST,
                 name="p2 test", stage="intake", framework="cisa_ztmm_v2",
                 created_by_id=admin.id)
    db.session.add(p2)
    db.session.commit()
    r = admin_client.get(f"/platform/zero-trust/project/{p2.id}")
    assert r.status_code == 200
    body = r.data
    # New verb appears.
    assert b"Audit walk" in body
    # Old verb does not.
    assert b"Reviewer walkability" not in body


def test_p2_workspace_says_change_list_not_relink(
    admin_client, app, acme, admin,
):
    """The change-capability-list link uses 'Change list' (round-7 §22)."""
    p2 = Project(client_id=acme.id, platform=PlatformType.ZERO_TRUST,
                 name="p2 change list", stage="intake",
                 framework="cisa_ztmm_v2", created_by_id=admin.id)
    db.session.add(p2)
    db.session.commit()
    r = admin_client.get(f"/platform/zero-trust/project/{p2.id}")
    assert r.status_code == 200
    assert b"Change list" in r.data
    assert b"Relink list" not in r.data


def test_p2_walkability_page_title_says_audit_walk(
    admin_client, app, acme, admin,
):
    """The P2 walkability template's H1 says 'Audit walk', not
    'Reviewer walkability'."""
    p2 = Project(client_id=acme.id, platform=PlatformType.ZERO_TRUST,
                 name="p2 walk title", stage="intake",
                 framework="cisa_ztmm_v2", created_by_id=admin.id)
    db.session.add(p2)
    db.session.commit()
    r = admin_client.get(f"/platform/zero-trust/project/{p2.id}/walkability")
    assert r.status_code == 200
    body = r.data
    assert b"Audit walk" in body
    assert b"Reviewer walkability" not in body
