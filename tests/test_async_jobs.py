"""End-to-end tests for the async AI path.

With the `_sync_rq` autouse fixture in conftest, `enqueue_ai(...)` runs
the job synchronously inside the same Python process (no Redis server,
no worker container needed). That means a route's POST that enqueues an
AI job will complete the job before the response is returned — so we
can assert on the final DB state.

`AI_MODE=fixture` (set by TestConfig) means the Anthropic client returns
canned JSON from `shield/ai/fixtures/*.json` rather than hitting the
network, so these tests are deterministic and cost nothing to run.
"""
from __future__ import annotations

import io

from shield.extensions import db
from shield.models import (
    Artifact,
    CoverageFinding,
    CoverageRun,
    Origin,
    PlatformType,
    Project,
    QuestionnaireResponse,
    TrustTier,
)
from shield.spine.repository import write_human_artifact


def _make_p1_project(acme, admin) -> Project:
    p = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="TD test", stage="intake",
        capability_list_version_id=acme.capability_lists[0].id,
        created_by_id=admin.id,
    )
    db.session.add(p)
    db.session.commit()
    return p


def _make_p3_project(acme, admin) -> Project:
    p = Project(
        client_id=acme.id, platform=PlatformType.ATTACK_SURFACE,
        name="ATT&CK test", stage="intake",
        capability_list_version_id=acme.capability_lists[0].id,
        created_by_id=admin.id,
    )
    db.session.add(p)
    db.session.commit()
    return p


# --------------------------------------------------------------------
# P1: extract enqueues + completes inline + writes AI artifact
# --------------------------------------------------------------------

def test_p1_extract_completes_inline_and_writes_ai_artifact(
    admin_client, acme, admin,
):
    project = _make_p1_project(acme, admin)
    src = write_human_artifact(
        project=project, stage="raw_intake",
        title="Inventory CSV",
        file_stream=io.BytesIO(b"name,vendor\nSplunk,Splunk Inc\n"),
        filename="inv.csv", mime_type="text/csv",
        actor=admin,
    )

    r = admin_client.post(
        f"/platform/tech-debt/project/{project.id}/extract",
        data={"artifact_id": src.id},
        follow_redirects=False,
    )
    # The route redirects to /jobs/<id>/wait, but the sync-mode RQ
    # already ran the job before returning, so the AI artifact exists.
    assert r.status_code == 302
    assert r.headers["Location"].startswith("/jobs/")

    ai_arts = (
        db.session.query(Artifact)
        .filter_by(project_id=project.id, origin=Origin.AI_GENERATED, stage="ai_extraction")
        .all()
    )
    assert len(ai_arts) == 1, "extract job should have written exactly one AI artifact"
    art = ai_arts[0]
    assert src.id in (art.lineage or {}).get("input_artifacts", []), (
        "AI artifact must record the source artifact in its lineage"
    )
    assert (art.lineage or {}).get("prompt_version") == "p1_extraction.v1"


# --------------------------------------------------------------------
# P3: analyze enqueues + completes inline + materializes the run rows
# --------------------------------------------------------------------

def test_p3_analyze_materializes_coverage_run_inline(
    admin_client, acme, admin,
):
    project = _make_p3_project(acme, admin)

    r = admin_client.post(
        f"/platform/attack-surface/project/{project.id}/analyze",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["Location"].startswith("/jobs/")

    # The job writes both the AI artifact AND the materialized
    # CoverageRun + CoverageFinding rows. Verify all three.
    ai_arts = (
        db.session.query(Artifact)
        .filter_by(project_id=project.id, origin=Origin.AI_GENERATED, stage="attack_coverage")
        .all()
    )
    assert len(ai_arts) == 1

    runs = db.session.query(CoverageRun).filter_by(project_id=project.id).all()
    assert len(runs) == 1
    findings = (
        db.session.query(CoverageFinding)
        .filter_by(coverage_run_id=runs[0].id).all()
    )
    # The p3_attack_coverage.v1 fixture ships with 9 findings.
    assert len(findings) >= 1, (
        "p3 coverage job must materialize at least one CoverageFinding"
    )


# --------------------------------------------------------------------
# P2: analyze pre-flight check is intact (no enqueue without framework)
# --------------------------------------------------------------------

def test_p2_analyze_route_runs_inline(admin_client, acme, admin):
    project = Project(
        client_id=acme.id, platform=PlatformType.ZERO_TRUST,
        name="ZT test", stage="intake", framework="cisa_ztmm_v2",
        capability_list_version_id=acme.capability_lists[0].id,
        created_by_id=admin.id,
    )
    db.session.add(project)
    db.session.commit()
    # Need at least one response so the AI payload isn't empty
    db.session.add(QuestionnaireResponse(
        project_id=project.id, framework="cisa_ztmm_v2",
        control_id="ZTMM.IDENT.1", answer="implemented", rationale="MFA on all",
        trust_tier=TrustTier.ADMIN_ASSISTED, attributed_user_id=admin.id,
    ))
    db.session.commit()

    r = admin_client.post(
        f"/platform/zero-trust/project/{project.id}/analyze",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["Location"].startswith("/jobs/")

    current_state = (
        db.session.query(Artifact)
        .filter_by(
            project_id=project.id, origin=Origin.AI_GENERATED,
            stage="current_state_assessment",
        )
        .one_or_none()
    )
    assert current_state is not None, (
        "p2 analyze must produce a current_state_assessment artifact"
    )
