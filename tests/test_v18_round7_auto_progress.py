"""Round-7 §19: workflow auto-progression hooks.

After an admin saves an upstream artifact (a source upload, a reviewed
extraction, a locked ZT submission), the system auto-queues the next
analytic step. The admin can opt out via `User.auto_progress_workflows`.
"""
from __future__ import annotations

import json
from io import BytesIO

import pytest

from shield.extensions import db
from shield.models import (
    Artifact,
    AuditEntry,
    Origin,
    PlatformType,
    Project,
)
from shield.spine.auto_progress import (
    maybe_auto_progress_p1_after_review,
    maybe_auto_progress_p1_after_upload,
    should_auto_progress,
)
from shield.spine.repository import (
    write_human_ai_informed_artifact,
    write_human_artifact,
)


@pytest.fixture()
def p1_project(app, acme, admin):
    p = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="auto-progress test", stage="intake",
        created_by_id=admin.id,
    )
    db.session.add(p)
    db.session.commit()
    return p


# --------------------------------------------------------------------
# Helper-level checks — no HTTP layer
# --------------------------------------------------------------------

def test_should_auto_progress_defaults_true(admin):
    # New user fixtures don't set the column; the default is True.
    assert should_auto_progress(admin) is True


def test_should_auto_progress_respects_opt_out(admin):
    admin.auto_progress_workflows = False
    db.session.commit()
    assert should_auto_progress(admin) is False


def test_auto_progress_p1_after_upload_writes_audit_row(
    app, p1_project, admin,
):
    src = write_human_artifact(
        project=p1_project, stage="raw_intake",
        title="inv.csv",
        file_stream=None, filename="inv.csv", mime_type="text/csv",
        actor=admin, body_text="dummy",
    )
    job = maybe_auto_progress_p1_after_upload(p1_project, src, actor=admin)
    # In test mode, the queue runs synchronously and the job returns
    # whatever the worker writes. Either way: an auto_progress audit
    # row should exist.
    entry = (db.session.query(AuditEntry)
             .filter_by(action="auto_progress.p1_extract_queued").first())
    assert entry is not None
    assert entry.details.get("triggered_by") == "auto_progress"


def test_auto_progress_p1_after_review_writes_audit_row(
    app, p1_project, admin,
):
    review = write_human_ai_informed_artifact(
        project=p1_project, stage="extraction_review",
        title="reviewed", body_text="[]",
        cites_artifact_ids=[], actor=admin,
    )
    maybe_auto_progress_p1_after_review(p1_project, review, actor=admin)
    entry = (db.session.query(AuditEntry)
             .filter_by(action="auto_progress.p1_overlap_queued").first())
    assert entry is not None
    assert entry.details.get("triggered_by") == "auto_progress"


def test_opt_out_skips_the_hook(app, p1_project, admin):
    admin.auto_progress_workflows = False
    db.session.commit()
    src = write_human_artifact(
        project=p1_project, stage="raw_intake",
        title="x.csv",
        file_stream=None, filename=None, mime_type=None,
        actor=admin, body_text="x",
    )
    job = maybe_auto_progress_p1_after_upload(p1_project, src, actor=admin)
    assert job is None
    # No audit row created.
    entries = (db.session.query(AuditEntry)
               .filter_by(action="auto_progress.p1_extract_queued").all())
    assert entries == []


# --------------------------------------------------------------------
# Route-level: upload triggers auto-progress redirect
# --------------------------------------------------------------------

def test_upload_route_auto_redirects_to_jobs_wait(admin_client, p1_project):
    r = admin_client.post(
        f"/platform/tech-debt/project/{p1_project.id}/upload",
        data={
            "title": "auto.csv",
            "file": (BytesIO(b"a,b,c"), "auto.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert r.status_code == 302
    # The upload route now sends the user to jobs.wait when
    # auto-progression is on, so the user lands on the wait screen
    # rather than back on the workspace.
    loc = r.headers["Location"]
    assert "/jobs/" in loc and "/wait" in loc


# --------------------------------------------------------------------
# Audit renderer covers auto_progress.* actions
# --------------------------------------------------------------------

def test_audit_render_recognizes_auto_progress_actions():
    from shield.spine.audit_render import render_audit_details
    out = render_audit_details(
        "auto_progress.p1_extract_queued",
        {"triggered_by": "auto_progress", "source_artifact_id": "abc"},
    )
    assert "Auto-queued" in out
