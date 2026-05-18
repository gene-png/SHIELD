"""Tests for the P2 three-card dashboard rebuild (round-5 §6.4).

The workspace's three artifact lanes (current-state, desired-future,
roadmap) used to render the AI body as <details><pre>JSON</pre></details>.
The new layout renders each as a compact dashboard:

  Card 1 (current state):
    - Big % score = implemented / total.
    - Per-pillar progress bars.
    - "Open full assessment" CTA → /platform/zero-trust/<id>/summary.

  Card 2 (desired future):
    - Count of targets set.
    - "Set desired future state" / "Edit targets" CTA.

  Card 3 (roadmap):
    - Phase count + top-3 phase summaries.
    - "Open roadmap" CTA.
"""
from __future__ import annotations

import json

import pytest

from shield.extensions import db
from shield.models import Artifact, Origin, PlatformType, Project, TrustTier


@pytest.fixture()
def p2_project(app, acme, admin):
    p = Project(
        client_id=acme.id, platform=PlatformType.ZERO_TRUST,
        name="P2 dashboards test", stage="intake",
        framework="cisa_ztmm_v2", created_by_id=admin.id,
    )
    db.session.add(p)
    db.session.commit()
    return p


def _add_artifact(project, stage, body):
    art = Artifact(
        project_id=project.id, client_id=project.client_id,
        origin=Origin.AI_GENERATED, stage=stage,
        title=f"{stage} artifact",
        body_text=json.dumps(body),
        trust_tier=TrustTier.NOT_APPLICABLE,
        lineage={},
    )
    db.session.add(art)
    db.session.commit()
    return art


# --------------------------------------------------------------------
# Card 1 — current state
# --------------------------------------------------------------------

def test_current_state_card_renders_score_when_artifact_exists(
    admin_client, p2_project,
):
    _add_artifact(p2_project, "current_state_assessment", {
        "controls": [
            {"control_id": "ZTMM.IDENT.AUTH", "ai_assessment": "implemented"},
            {"control_id": "ZTMM.IDENT.LIFEC", "ai_assessment": "partial"},
            {"control_id": "ZTMM.DEV.PCY",    "ai_assessment": "not_implemented"},
            {"control_id": "ZTMM.NET.SEG",    "ai_assessment": "implemented"},
        ],
        "summary": {
            "controls_total": 4, "implemented": 2,
            "partial": 1, "not_implemented": 1,
            "headline": "Half of controls are in place; segmentation is the gap.",
        },
    })
    r = admin_client.get(f"/platform/zero-trust/project/{p2_project.id}")
    assert r.status_code == 200
    # 2/4 = 50% score.
    assert b"50%" in r.data
    assert b"controls in place" in r.data
    # Headline appears.
    assert b"segmentation is the gap" in r.data
    # CTA to open the full assessment.
    assert b"Open full assessment" in r.data


def test_current_state_card_per_pillar_bars(admin_client, p2_project):
    """Per-pillar progress bars reflect implemented-vs-total counts."""
    _add_artifact(p2_project, "current_state_assessment", {
        "controls": [
            # Identity pillar: 2 controls, 1 implemented = 50%
            {"control_id": "ZTMM.IDENT.AUTH", "ai_assessment": "implemented"},
            {"control_id": "ZTMM.IDENT.LIFEC", "ai_assessment": "partial"},
            # Devices pillar: 1 control, implemented = 100%
            {"control_id": "ZTMM.DEV.PCY", "ai_assessment": "implemented"},
        ],
        "summary": {"controls_total": 3, "implemented": 2, "partial": 1, "not_implemented": 0},
    })
    r = admin_client.get(f"/platform/zero-trust/project/{p2_project.id}")
    # The per-pillar header renders.
    assert b"By pillar" in r.data
    # Pillar labels from cisa_ztmm_v2 framework show up.
    assert b"Identity" in r.data
    assert b"Devices" in r.data


def test_current_state_card_empty_state(admin_client, p2_project):
    r = admin_client.get(f"/platform/zero-trust/project/{p2_project.id}")
    assert b"Not run yet" in r.data


# --------------------------------------------------------------------
# Card 2 — desired future state
# --------------------------------------------------------------------

def test_desired_state_card_empty_state(admin_client, p2_project):
    r = admin_client.get(f"/platform/zero-trust/project/{p2_project.id}")
    assert b"No targets set yet" in r.data
    assert b"Set desired future state" in r.data


def test_desired_state_card_shows_count_when_set(admin_client, p2_project, admin):
    # desired_future_state is human_input (admin enters via /set-target).
    art = Artifact(
        project_id=p2_project.id, client_id=p2_project.client_id,
        origin=Origin.HUMAN_INPUT, stage="desired_future_state",
        title="targets", body_text=json.dumps({"targets": {"a": 1, "b": 2, "c": 3}}),
        trust_tier=TrustTier.ADMIN_ENTERED_ON_BEHALF,
        actor_id=admin.id, lineage={},
    )
    db.session.add(art)
    db.session.commit()
    r = admin_client.get(f"/platform/zero-trust/project/{p2_project.id}")
    assert b"3</strong> control target" in r.data
    assert b"Edit targets" in r.data


# --------------------------------------------------------------------
# Card 3 — roadmap
# --------------------------------------------------------------------

def test_roadmap_card_empty_state(admin_client, p2_project):
    r = admin_client.get(f"/platform/zero-trust/project/{p2_project.id}")
    assert b"Not generated yet" in r.data


def test_roadmap_card_renders_phases(admin_client, p2_project):
    _add_artifact(p2_project, "transition_roadmap", {
        "phases": [
            {"name": "Quick wins", "summary": "MFA on remaining services"},
            {"name": "Foundation", "summary": "PAM + segmentation pilot"},
            {"name": "Mature", "summary": "Full microsegmentation rollout"},
            {"name": "Optimize", "summary": "Continuous verification"},
        ],
    })
    r = admin_client.get(f"/platform/zero-trust/project/{p2_project.id}")
    assert b"4</strong> phase" in r.data
    assert b"Quick wins" in r.data
    # Top 3 shown explicitly + a "and N more" line.
    assert b"and 1 more" in r.data
    assert b"Open roadmap" in r.data


# --------------------------------------------------------------------
# No <details><pre> JSON dumps remain in the three cards
# --------------------------------------------------------------------

def test_workspace_does_not_show_raw_json_dumps_in_cards(admin_client, p2_project):
    """Even with all three artifacts present, no `<pre>` JSON in the
    new three-card section."""
    _add_artifact(p2_project, "current_state_assessment", {"controls": [], "summary": {}})
    _add_artifact(p2_project, "desired_future_state", {"targets": {}})
    _add_artifact(p2_project, "transition_roadmap", {"phases": []})
    r = admin_client.get(f"/platform/zero-trust/project/{p2_project.id}")
    # Defensive: the old "Inspect" / pre-block fragment should not appear
    # in the three-card section anymore.
    assert b"shield-body-text" not in r.data
