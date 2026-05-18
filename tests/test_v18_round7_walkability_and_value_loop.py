"""Round-7 §8 — audit-walkability for P1 and P3, plus the cross-service
value-loop page on /clients/<id>/value-loop.

Existing P2 walkability already covers Zero Trust; this batch adds the
P1 (Tech Debt) and P3 (Attack Surface) reviewer surfaces and a synthesis
page that pulls the three together for a single client.
"""
from __future__ import annotations

import json

import pytest

from shield.extensions import db
from shield.models import (
    Artifact,
    CapabilityList,
    CapabilityListItem,
    CoverageFinding,
    CoverageRun,
    Origin,
    PlatformType,
    Project,
)
from shield.spine.repository import (
    write_ai_artifact,
    write_human_ai_informed_artifact,
)


# --------------------------------------------------------------------
# Fixtures shared across walkability + value-loop tests
# --------------------------------------------------------------------

@pytest.fixture()
def p1_project(app, acme, admin):
    p = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="P1 walk test", stage="overlap_analysis",
        created_by_id=admin.id,
    )
    db.session.add(p)
    db.session.commit()
    return p


@pytest.fixture()
def p3_project(app, acme, admin):
    p = Project(
        client_id=acme.id, platform=PlatformType.ATTACK_SURFACE,
        name="P3 walk test", stage="intake",
        created_by_id=admin.id,
    )
    db.session.add(p)
    db.session.commit()
    return p


@pytest.fixture()
def acme_overlap(p1_project, admin):
    """Seed a reviewed extraction + an overlap-analysis AI artifact
    on the P1 project so the walkability page has data to show."""
    write_human_ai_informed_artifact(
        project=p1_project, stage="extraction_review",
        title="reviewed", body_text=json.dumps([{"name": "Splunk"}]),
        cites_artifact_ids=[], actor=admin,
    )
    return write_ai_artifact(
        project=p1_project, stage="overlap_analysis",
        title="overlap",
        body_text=json.dumps({
            "summary": {
                "headline": "Two SIEM tools doing one job.",
                "consolidation_savings_estimate_usd": 250000,
                "total_annual_spend_usd": 1_200_000,
            },
            "overlaps": [{
                "category": "SIEM",
                "candidates": ["Splunk", "Microsoft Sentinel"],
                "estimated_overlap_cost_usd": 250000,
                "rationale": "Both ingest the same log sources.",
                "recommendation": "Keep Splunk; retire Sentinel.",
            }],
            "shadow_it": [{
                "name": "Slack DLP plugin",
                "reason": "No owner of record.",
                "recommendation": "Confirm owner or retire.",
            }],
        }),
        input_artifact_ids=[],
        prompt_version="p1_overlap.v1", model="fixture",
    )


@pytest.fixture()
def acme_coverage_run(p3_project, acme, admin):
    cl = CapabilityList(client_id=acme.id, version=99, label="walk test",
                        origin=Origin.HUMAN_INPUT, created_by_id=admin.id)
    db.session.add(cl)
    db.session.commit()
    db.session.add(CapabilityListItem(
        capability_list_id=cl.id, name="Splunk", vendor="Splunk Inc",
        category="SIEM", function="log aggregation"))
    db.session.commit()
    run = CoverageRun(
        project_id=p3_project.id,
        capability_list_version_id=cl.id,
        summary={"headline": "Heavy reliance on detection only.",
                 "covered": 5, "partial": 3, "uncovered": 2,
                 "top_three_blind_spots": ["T1059"]},
    )
    db.session.add(run)
    db.session.commit()
    db.session.add(CoverageFinding(
        coverage_run_id=run.id, technique_id="T1059",
        coverage="uncovered",
        detection_tools=[], prevention_tools=[], response_tools=[],
        rationale="No EDR-class capability mapped.",
    ))
    db.session.add(CoverageFinding(
        coverage_run_id=run.id, technique_id="T1078",
        coverage="partial",
        detection_tools=["Splunk"], prevention_tools=[], response_tools=[],
        rationale="Detection-only; no prevention or response.",
    ))
    db.session.commit()
    return run


# --------------------------------------------------------------------
# P1 walkability
# --------------------------------------------------------------------

def test_p1_walkability_renders_overlap_groups(admin_client, p1_project, acme_overlap):
    r = admin_client.get(
        f"/platform/tech-debt/project/{p1_project.id}/walkability"
    )
    assert r.status_code == 200
    body = r.data
    # The walkability is labeled as the audit walk surface.
    assert b"Audit walk" in body
    # Overlap group renders: candidates, rationale, recommendation.
    assert b"Splunk" in body
    assert b"Microsoft Sentinel" in body
    assert b"Keep Splunk" in body
    # Cost estimate is rendered with thousands separators.
    assert b"250,000" in body
    # Shadow IT section appears with the seeded entry.
    assert b"Shadow IT" in body
    assert b"Slack DLP plugin" in body


def test_p1_walkability_empty_state_when_no_overlap(admin_client, p1_project):
    """No overlap artifact = friendly empty state, not a 500."""
    r = admin_client.get(
        f"/platform/tech-debt/project/{p1_project.id}/walkability"
    )
    assert r.status_code == 200
    assert b"No overlap analysis to walk yet" in r.data


def test_p1_walkability_blocks_client_role(client_role_client, p1_project, acme_overlap):
    """admin_or_reviewer gate keeps CLIENT users out."""
    r = client_role_client.get(
        f"/platform/tech-debt/project/{p1_project.id}/walkability",
        follow_redirects=False,
    )
    # 302 to /portal/ via the role gate, or 403 if directly denied.
    # Either is "not allowed"; the contract is "CLIENT cannot read it."
    assert r.status_code in (302, 403)


# --------------------------------------------------------------------
# P3 walkability
# --------------------------------------------------------------------

def test_p3_walkability_groups_by_coverage_class(
    admin_client, p3_project, acme_coverage_run,
):
    r = admin_client.get(
        f"/platform/attack-surface/project/{p3_project.id}"
        f"/run/{acme_coverage_run.id}/walkability"
    )
    assert r.status_code == 200
    body = r.data
    assert b"Audit walk" in body
    # Uncovered + partial groups both render with their headings.
    assert b"Uncovered techniques" in body
    assert b"Partially covered" in body
    # Per-technique rationale appears.
    assert b"No EDR-class capability mapped" in body
    assert b"Detection-only" in body
    # Recommendations are surfaced.
    assert b"Add a capability" in body or b"Extend existing" in body


def test_p3_walkability_cross_project_run_returns_404(
    admin_client, p3_project, acme, admin,
):
    """A coverage run from a different project must 404 when accessed
    through this project's walkability URL."""
    other = Project(client_id=acme.id, platform=PlatformType.ATTACK_SURFACE,
                    name="other", stage="intake", created_by_id=admin.id)
    db.session.add(other)
    db.session.commit()
    other_cl = CapabilityList(client_id=acme.id, version=100, label="x",
                              origin=Origin.HUMAN_INPUT, created_by_id=admin.id)
    db.session.add(other_cl)
    db.session.commit()
    other_run = CoverageRun(project_id=other.id,
                            capability_list_version_id=other_cl.id, summary={})
    db.session.add(other_run)
    db.session.commit()
    r = admin_client.get(
        f"/platform/attack-surface/project/{p3_project.id}"
        f"/run/{other_run.id}/walkability"
    )
    assert r.status_code == 404


# --------------------------------------------------------------------
# Cross-service value-loop synthesis
# --------------------------------------------------------------------

def test_value_loop_renders_all_three_services(
    admin_client, acme, acme_overlap, acme_coverage_run,
):
    """With Tech Debt overlap + Attack Surface run both present, the
    value-loop page surfaces the savings number AND the uncovered
    technique count side by side."""
    r = admin_client.get(f"/clients/{acme.id}/value-loop")
    assert r.status_code == 200
    body = r.data
    # Tech Debt savings card.
    assert b"250,000" in body
    assert b"Open Tech Debt summary" in body
    # Attack Surface gaps card.
    assert b"uncovered ATT&amp;CK techniques" in body
    assert b"Open coverage run" in body
    # Zero Trust card renders even when there's no roadmap (empty state).
    assert b"No Zero Trust roadmap available yet" in body


def test_value_loop_empty_state_when_nothing_run(admin_client, acme):
    """A fresh client with no analyses yet still renders the page
    cleanly — each card just shows its empty state."""
    r = admin_client.get(f"/clients/{acme.id}/value-loop")
    assert r.status_code == 200
    body = r.data
    assert b"No Tech Debt overlap analysis available yet" in body
    assert b"No Attack Surface coverage run available yet" in body
    assert b"No Zero Trust roadmap available yet" in body


def test_value_loop_blocks_cross_client_access(
    client_role_client, acme,
):
    """A CLIENT-role user from a different org must not read this page.
    The role gate redirects CLIENT roles to /portal/; the contract is
    'no read,' so 302 or 404 both satisfy."""
    r = client_role_client.get(
        f"/clients/{acme.id}/value-loop", follow_redirects=False,
    )
    assert r.status_code in (302, 404)
