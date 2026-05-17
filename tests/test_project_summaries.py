"""Smoke tests for the P1 / P2 project_summary routes.

These don't try to assert on the rendered HTML in detail — the layout
will change. They check the two non-trivial behaviors:
  1. The empty state renders without exploding when no analysis exists.
  2. With a fixture analysis artifact in the DB, the page surfaces the
     expected headline so we know the JSON parse + template wiring is
     intact end-to-end.

A 404 for an admin on a project that exists would mean the route blew
up; a 200 with the empty-state copy is the correct behavior.
"""
from __future__ import annotations

import json

from shield.extensions import db
from shield.models import PlatformType, Project
from shield.spine.repository import write_ai_artifact

# --------------------------------------------------------------------
# P1 — Tech Debt summary
# --------------------------------------------------------------------

def _p1_project(acme, admin):
    p = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="TD test", stage="intake", created_by_id=admin.id,
    )
    db.session.add(p)
    db.session.commit()
    return p


def test_p1_summary_empty_state_renders(admin_client, acme, admin):
    project = _p1_project(acme, admin)
    r = admin_client.get(f"/platform/tech-debt/project/{project.id}/summary")
    assert r.status_code == 200
    assert b"Nothing to summarize yet" in r.data


def test_p1_summary_renders_headline_when_overlap_exists(admin_client, acme, admin):
    project = _p1_project(acme, admin)
    body = {
        "overlaps": [{
            "category": "SIEM",
            "candidates": ["Splunk", "Sentinel"],
            "estimated_overlap_cost_usd": 320000,
            "recommendation": "Consolidate onto one SIEM.",
            "rationale": "Two SIEMs duplicate the same use cases.",
        }],
        "shadow_it": [],
        "summary": {
            "total_annual_spend_usd": 5_000_000,
            "consolidation_savings_estimate_usd": 380_000,
            "headline": "There is ~$380K of likely-recoverable spend.",
        },
    }
    write_ai_artifact(
        project=project, stage="overlap_analysis",
        title="overlap fixture", body_text=json.dumps(body),
        input_artifact_ids=[],
        prompt_version="p1_overlap.v1", model="fixture",
    )

    r = admin_client.get(f"/platform/tech-debt/project/{project.id}/summary")
    assert r.status_code == 200
    assert b"$380K of likely-recoverable spend" in r.data
    assert b"SIEM" in r.data


# --------------------------------------------------------------------
# P2 — Zero Trust summary
# --------------------------------------------------------------------

def test_p2_summary_empty_state_renders(admin_client, p2_project):
    r = admin_client.get(f"/platform/zero-trust/project/{p2_project.id}/summary")
    assert r.status_code == 200
    assert b"Nothing to summarize yet" in r.data


def test_p2_summary_renders_headline_and_gaps(admin_client, p2_project):
    body = {
        "controls": [
            {
                "control_id": "ZTMM.IDENT.AUTH",
                "client_claim": "implemented",
                "ai_assessment": "partial",
                "rationale": "MFA covers most users; service accounts excluded.",
                "gap": "Service accounts bypass MFA.",
                "next_step": "Roll service-account MFA via Conditional Access.",
            },
            {
                "control_id": "ZTMM.NET.SEG",
                "client_claim": "not_implemented",
                "ai_assessment": "not_implemented",
                "rationale": "Flat L2; no micro-segmentation evidence.",
                "gap": "No segmentation between user and server VLANs.",
                "next_step": "Pilot east-west segmentation in datacenter.",
            },
        ],
        "summary": {
            "controls_total": 35,
            "implemented": 12,
            "partial": 18,
            "not_implemented": 5,
            "headline": "Twelve of 35 controls fully implemented.",
        },
    }
    write_ai_artifact(
        project=p2_project, stage="current_state_assessment",
        title="posture fixture", body_text=json.dumps(body),
        input_artifact_ids=[],
        prompt_version="p2_posture.v1", model="fixture",
    )

    r = admin_client.get(f"/platform/zero-trust/project/{p2_project.id}/summary")
    assert r.status_code == 200
    assert b"Twelve of 35 controls fully implemented." in r.data
    # The top-three gap card should surface a specific gap.
    assert b"Service accounts bypass MFA." in r.data
