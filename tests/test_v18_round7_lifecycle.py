"""Round-7 §17: archive / unarchive / purge for Projects and Artifacts.

The two-tier soft-delete pattern. Archive is the default verb — easy
to do, easy to undo, hidden from default views. Purge is high-friction:
requires the row's name typed verbatim, removes the file on disk, and
leaves a tombstone row for the audit trail to point at.

These tests pin the route contracts, the audit rows, and the lifecycle
state machine.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from shield.extensions import db
from shield.models import (
    Artifact,
    AuditEntry,
    PlatformType,
    Project,
)
from shield.spine.repository import write_human_artifact


@pytest.fixture()
def p(app, acme, admin):
    p = Project(client_id=acme.id, platform=PlatformType.TECH_DEBT,
                name="Lifecycle test project", stage="intake",
                created_by_id=admin.id)
    db.session.add(p)
    db.session.commit()
    return p


@pytest.fixture()
def real_file_artifact(p, admin, tmp_path):
    """Create an artifact backed by a real file on disk so purge has
    something to remove."""
    f = tmp_path / "real.txt"
    f.write_text("hello")
    art = write_human_artifact(
        project=p, stage="raw_intake",
        title="real-file.txt",
        file_stream=None, filename="real.txt", mime_type="text/plain",
        actor=admin, body_text="hello",
    )
    # Point storage_key at the actual file so purge will try to remove it.
    art.storage_key = str(f)
    db.session.commit()
    return art


# --------------------------------------------------------------------
# Project archive / unarchive / purge
# --------------------------------------------------------------------

def test_archive_project_stamps_metadata_and_writes_audit(admin_client, p, admin):
    r = admin_client.post(
        f"/admin/lifecycle/projects/{p.id}/archive",
        data={"reason": "wrong client picked"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    db.session.refresh(p)
    assert p.archived is True
    assert p.archived_at is not None
    assert p.archived_by_id == admin.id
    assert p.archived_reason == "wrong client picked"
    assert p.lifecycle_state == "archived"
    # One audit entry written.
    entry = (db.session.query(AuditEntry)
             .filter_by(action="project.archived", target_id=p.id).one())
    assert entry.details.get("reason") == "wrong client picked"


def test_unarchive_project_clears_metadata(admin_client, p, admin):
    admin_client.post(
        f"/admin/lifecycle/projects/{p.id}/archive",
        data={"reason": "oops"},
    )
    r = admin_client.post(
        f"/admin/lifecycle/projects/{p.id}/unarchive",
        follow_redirects=False,
    )
    assert r.status_code == 302
    db.session.refresh(p)
    assert p.archived is False
    assert p.archived_at is None
    assert p.archived_reason is None
    assert p.lifecycle_state == "active"


def test_purge_project_requires_exact_name_confirmation(admin_client, p):
    """Without the correct confirmation phrase, the purge is a no-op."""
    r = admin_client.post(
        f"/admin/lifecycle/projects/{p.id}/purge",
        data={"confirmation_phrase": "wrong name", "reason": "demo"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    db.session.refresh(p)
    assert p.purged_at is None
    assert p.lifecycle_state == "active"


def test_purge_project_with_correct_phrase_tombstones_and_removes_files(
    admin_client, p, real_file_artifact,
):
    file_path = Path(real_file_artifact.storage_key)
    assert file_path.exists(), "fixture didn't create the file"
    r = admin_client.post(
        f"/admin/lifecycle/projects/{p.id}/purge",
        data={"confirmation_phrase": p.name,
              "reason": "GDPR erasure"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    db.session.refresh(p)
    assert p.purged_at is not None
    assert p.purged_reason == "GDPR erasure"
    assert p.lifecycle_state == "purged"
    # Cascade: the artifact's file was removed and the row tombstoned.
    db.session.refresh(real_file_artifact)
    assert real_file_artifact.purged_at is not None
    assert real_file_artifact.storage_key is None
    assert not file_path.exists()


def test_archive_project_blocks_client_role(client_role_client, p):
    r = client_role_client.post(
        f"/admin/lifecycle/projects/{p.id}/archive",
        data={"reason": "x"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 403)


# --------------------------------------------------------------------
# Artifact archive / unarchive / purge
# --------------------------------------------------------------------

def test_archive_artifact_hides_from_default_browse(admin_client, p, admin):
    art = write_human_artifact(
        project=p, stage="raw_intake",
        title="will-be-archived.txt",
        file_stream=None, filename=None, mime_type=None,
        actor=admin, body_text="x",
    )
    # Visible by default — the artifact's detail link appears.
    detail_link = f"/repository/artifact/{art.id}".encode()
    r = admin_client.get("/repository/")
    assert detail_link in r.data
    # Archive it.
    r = admin_client.post(
        f"/admin/lifecycle/artifacts/{art.id}/archive",
        data={"reason": "test"},
        follow_redirects=False,
    )
    assert r.status_code == 302, r.data[:400]
    db.session.refresh(art)
    assert art.archived is True
    # Now hidden from default browse — fresh GET, no flash carryover.
    r = admin_client.get("/repository/")
    assert detail_link not in r.data
    # But visible with the show_archived toggle.
    r = admin_client.get("/repository/?show_archived=1")
    assert detail_link in r.data


def test_purge_artifact_requires_exact_title(admin_client, real_file_artifact):
    r = admin_client.post(
        f"/admin/lifecycle/artifacts/{real_file_artifact.id}/purge",
        data={"confirmation_phrase": "wrong"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    db.session.refresh(real_file_artifact)
    assert real_file_artifact.purged_at is None


def test_purge_artifact_removes_file_and_clears_storage_key(
    admin_client, real_file_artifact,
):
    path = Path(real_file_artifact.storage_key)
    assert path.exists()
    r = admin_client.post(
        f"/admin/lifecycle/artifacts/{real_file_artifact.id}/purge",
        data={"confirmation_phrase": real_file_artifact.title,
              "reason": "accidentally uploaded"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    db.session.refresh(real_file_artifact)
    assert real_file_artifact.purged_at is not None
    assert real_file_artifact.storage_key is None
    assert not path.exists()
    # Audit row exists with the reason.
    entry = (db.session.query(AuditEntry)
             .filter_by(action="artifact.purged",
                        target_id=real_file_artifact.id).one())
    assert entry.details.get("reason") == "accidentally uploaded"


# --------------------------------------------------------------------
# Audit details renderer integration
# --------------------------------------------------------------------

def test_audit_render_uses_friendly_text_for_archive_actions():
    from shield.spine.audit_render import render_audit_details
    out = render_audit_details(
        "project.archived",
        {"reason": "test run done"},
    )
    assert "Archived" in out
    assert "test run done" in out
    out2 = render_audit_details("project.unarchived", {})
    assert "Restored" in out2
    out3 = render_audit_details(
        "project.purged",
        {"reason": "GDPR", "files_deleted": 3},
    )
    assert "Purged" in out3
    assert "3 files" in out3
