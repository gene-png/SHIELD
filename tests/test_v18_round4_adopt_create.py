"""Round-4 PR 1 — adopt-or-create flow tests.

Covers the round-4 §8 acceptance criteria for the adopt route:
  - GET renders the create-or-pick form.
  - target='new' creates a Project + adopts the artifact in one txn,
    writes two audit rows (project.created + artifact.adopted_into_project),
    and redirects to the new project's workspace.
  - target='existing' still works for the v1 use case.
  - Zero Trust requires a framework.
  - Cross-client targets return / redirect away.
  - The created Project carries platform / framework / stage='intake'.
  - Atomicity: a validation failure on the new path doesn't leave
    a half-adopted artifact or a half-created project.
"""
from __future__ import annotations

import pytest

from shield.extensions import db
from shield.models import (
    Artifact,
    AuditEntry,
    Client,
    Origin,
    PlatformType,
    Project,
)
from shield.spine.repository import write_human_artifact


@pytest.fixture()
def acme_repo_with_file(admin, acme):
    repo = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="Client Repository", stage="client_repository",
        is_client_repository=True, created_by_id=admin.id,
    )
    db.session.add(repo)
    db.session.commit()
    art = write_human_artifact(
        project=repo, stage="client_repository",
        title="inventory.xlsx",
        file_stream=None, filename=None, mime_type=None,
        actor=admin, body_text="[]",
    )
    return repo, art


# --------------------------------------------------------------------
# GET: the picker page renders
# --------------------------------------------------------------------

def test_adopt_get_renders_create_or_pick_form(admin_client, acme, acme_repo_with_file):
    _, art = acme_repo_with_file
    r = admin_client.get(f"/clients/{acme.id}/adopt-artifact/{art.id}")
    assert r.status_code == 200
    # Form fields the partial emits.
    assert b'name="target"' in r.data
    assert b'name="new_project_service"' in r.data
    assert b'name="new_project_name"' in r.data


def test_adopt_get_shows_existing_projects_when_present(
    admin_client, acme, admin, acme_repo_with_file,
):
    _, art = acme_repo_with_file
    existing = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="Pre-existing Acme project", stage="intake",
        created_by_id=admin.id,
    )
    db.session.add(existing)
    db.session.commit()
    r = admin_client.get(f"/clients/{acme.id}/adopt-artifact/{art.id}")
    assert b"Pre-existing Acme project" in r.data


def test_adopt_get_with_no_existing_shows_first_project_hint(
    admin_client, acme, acme_repo_with_file,
):
    _, art = acme_repo_with_file
    r = admin_client.get(f"/clients/{acme.id}/adopt-artifact/{art.id}")
    # The empty-state copy lives inside the partial.
    assert b"no projects yet" in r.data


# --------------------------------------------------------------------
# POST target=new: create-and-adopt happy path
# --------------------------------------------------------------------

def test_adopt_create_new_creates_project_and_adopts(
    admin_client, acme, acme_repo_with_file,
):
    _, art = acme_repo_with_file
    r = admin_client.post(
        f"/clients/{acme.id}/adopt-artifact/{art.id}",
        data={
            "target": "new",
            "new_project_service": "tech_debt",
            "new_project_name": "Acme TD Q3 2026",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    # New project lives.
    project = (
        db.session.query(Project)
        .filter_by(client_id=acme.id, name="Acme TD Q3 2026")
        .first()
    )
    assert project is not None
    assert project.platform == PlatformType.TECH_DEBT
    assert project.stage == "intake"
    assert project.is_client_repository is False
    # Artifact's project_id moved to the new project.
    refreshed = db.session.get(Artifact, art.id)
    assert refreshed.project_id == project.id
    assert refreshed.origin == Origin.HUMAN_INPUT  # origin immutable
    # Redirect target is the new project's workspace.
    assert f"/platform/tech-debt/project/{project.id}" in r.headers["Location"]


def test_adopt_create_new_writes_paired_audit_rows(
    admin_client, acme, acme_repo_with_file,
):
    _, art = acme_repo_with_file
    before_created = db.session.query(AuditEntry).filter_by(
        action="project.created",
    ).count()
    before_adopted = db.session.query(AuditEntry).filter_by(
        action="artifact.adopted_into_project",
    ).count()
    admin_client.post(
        f"/clients/{acme.id}/adopt-artifact/{art.id}",
        data={
            "target": "new",
            "new_project_service": "attack_surface",
            "new_project_name": "Acme ATT&CK 2026",
        },
    )
    # Round-4 §6: pair of audit rows for the create-new path.
    assert db.session.query(AuditEntry).filter_by(
        action="project.created",
    ).count() == before_created + 1
    assert db.session.query(AuditEntry).filter_by(
        action="artifact.adopted_into_project",
    ).count() == before_adopted + 1


def test_adopt_create_new_zero_trust_requires_framework(
    admin_client, acme, acme_repo_with_file,
):
    _, art = acme_repo_with_file
    r = admin_client.post(
        f"/clients/{acme.id}/adopt-artifact/{art.id}",
        data={
            "target": "new",
            "new_project_service": "zero_trust",
            "new_project_name": "Acme ZT",
            # framework deliberately omitted
        },
        follow_redirects=False,
    )
    # Redirected back to the form, not the workspace.
    assert r.status_code == 302
    assert "/adopt-artifact/" in r.headers["Location"]
    # No project created, artifact didn't move.
    assert db.session.query(Project).filter_by(
        client_id=acme.id, name="Acme ZT",
    ).count() == 0
    refreshed = db.session.get(Artifact, art.id)
    assert refreshed.project_id != None  # noqa: E711
    # Still in the synthetic repository project.
    repo_id = refreshed.project_id
    repo = db.session.get(Project, repo_id)
    assert repo.is_client_repository is True


def test_adopt_create_new_zero_trust_with_framework_succeeds(
    admin_client, acme, acme_repo_with_file,
):
    _, art = acme_repo_with_file
    r = admin_client.post(
        f"/clients/{acme.id}/adopt-artifact/{art.id}",
        data={
            "target": "new",
            "new_project_service": "zero_trust",
            "new_project_name": "Acme ZT",
            "new_project_framework": "cisa_ztmm_v2",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    project = (
        db.session.query(Project)
        .filter_by(client_id=acme.id, name="Acme ZT")
        .first()
    )
    assert project is not None
    assert project.platform == PlatformType.ZERO_TRUST
    assert project.framework == "cisa_ztmm_v2"


def test_adopt_create_new_empty_name_fails(
    admin_client, acme, acme_repo_with_file,
):
    _, art = acme_repo_with_file
    r = admin_client.post(
        f"/clients/{acme.id}/adopt-artifact/{art.id}",
        data={
            "target": "new",
            "new_project_service": "tech_debt",
            "new_project_name": "   ",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "/adopt-artifact/" in r.headers["Location"]
    assert db.session.query(Project).filter_by(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        is_client_repository=False,
    ).count() == 0


def test_adopt_create_new_bogus_service_fails(
    admin_client, acme, acme_repo_with_file,
):
    _, art = acme_repo_with_file
    r = admin_client.post(
        f"/clients/{acme.id}/adopt-artifact/{art.id}",
        data={
            "target": "new",
            "new_project_service": "drop_table",
            "new_project_name": "evil project",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert db.session.query(Project).filter_by(
        client_id=acme.id, name="evil project",
    ).count() == 0


# --------------------------------------------------------------------
# POST target=existing: still works
# --------------------------------------------------------------------

def test_adopt_existing_still_works(admin_client, acme, admin, acme_repo_with_file):
    _, art = acme_repo_with_file
    existing = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="Existing TD", stage="intake", created_by_id=admin.id,
    )
    db.session.add(existing)
    db.session.commit()
    r = admin_client.post(
        f"/clients/{acme.id}/adopt-artifact/{art.id}",
        data={"target": "existing", "existing_project_id": existing.id},
        follow_redirects=False,
    )
    assert r.status_code == 302
    refreshed = db.session.get(Artifact, art.id)
    assert refreshed.project_id == existing.id
    # Existing-path: only ONE audit row (no project.created).
    assert db.session.query(AuditEntry).filter_by(
        action="artifact.adopted_into_project",
        target_id=art.id,
    ).count() == 1


# --------------------------------------------------------------------
# Cross-client safety
# --------------------------------------------------------------------

def test_adopt_existing_rejects_cross_client(
    admin_client, acme, admin, acme_repo_with_file,
):
    """Pointing existing_project_id at another client's project must
    flash + redirect, not adopt."""
    _, art = acme_repo_with_file
    beta = Client(name="Beta")
    db.session.add(beta)
    db.session.commit()
    beta_p = Project(
        client_id=beta.id, platform=PlatformType.TECH_DEBT,
        name="Beta", stage="intake", created_by_id=admin.id,
    )
    db.session.add(beta_p)
    db.session.commit()
    admin_client.post(
        f"/clients/{acme.id}/adopt-artifact/{art.id}",
        data={"target": "existing", "existing_project_id": beta_p.id},
    )
    refreshed = db.session.get(Artifact, art.id)
    assert refreshed.project_id != beta_p.id


def test_adopt_artifact_from_other_client_returns_404(
    admin_client, acme, admin,
):
    """The artifact in the URL must belong to the client in the URL."""
    beta = Client(name="Beta")
    db.session.add(beta)
    db.session.commit()
    beta_repo = Project(
        client_id=beta.id, platform=PlatformType.TECH_DEBT,
        name="r", stage="client_repository",
        is_client_repository=True, created_by_id=admin.id,
    )
    db.session.add(beta_repo)
    db.session.commit()
    beta_art = write_human_artifact(
        project=beta_repo, stage="client_repository",
        title="b.txt", file_stream=None, filename=None, mime_type=None,
        actor=admin, body_text="[]",
    )
    r = admin_client.get(f"/clients/{acme.id}/adopt-artifact/{beta_art.id}")
    assert r.status_code == 404


def test_adopt_no_target_redirects_back(admin_client, acme, acme_repo_with_file):
    """Submit without a target value should not silently adopt anywhere."""
    _, art = acme_repo_with_file
    r = admin_client.post(
        f"/clients/{acme.id}/adopt-artifact/{art.id}",
        data={"new_project_name": "loose form"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    # And nothing got created.
    assert db.session.query(Project).filter_by(name="loose form").count() == 0


# --------------------------------------------------------------------
# RBAC
# --------------------------------------------------------------------

def test_adopt_requires_admin(reviewer_client, acme, acme_repo_with_file):
    _, art = acme_repo_with_file
    r = reviewer_client.get(
        f"/clients/{acme.id}/adopt-artifact/{art.id}",
        follow_redirects=False,
    )
    # @admin_only redirects/403s.
    assert r.status_code in (302, 403, 404)


# --------------------------------------------------------------------
# Sub-PR 4C: /clients/<id>/projects/new + "Start a new project" button
# --------------------------------------------------------------------

def test_new_project_for_client_get_renders_form(admin_client, acme):
    r = admin_client.get(f"/clients/{acme.id}/projects/new")
    assert r.status_code == 200
    # Shared partial renders.
    assert b'name="new_project_service"' in r.data
    assert b'name="new_project_name"' in r.data
    # Existing-project radio hidden in this entry point.
    assert b'name="target"' not in r.data or b'value="existing"' not in r.data


def test_new_project_for_client_post_creates_project(admin_client, acme):
    r = admin_client.post(
        f"/clients/{acme.id}/projects/new",
        data={
            "target": "new",
            "new_project_service": "attack_surface",
            "new_project_name": "Acme ATT&CK Q4 2026",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    project = (
        db.session.query(Project)
        .filter_by(client_id=acme.id, name="Acme ATT&CK Q4 2026")
        .first()
    )
    assert project is not None
    assert project.platform == PlatformType.ATTACK_SURFACE
    assert project.stage == "intake"
    # Audit row with the right `created_from`.
    entry = (
        db.session.query(AuditEntry)
        .filter_by(action="project.created", target_id=project.id)
        .first()
    )
    assert entry is not None
    assert entry.details.get("created_from") == "client_detail"


def test_new_project_for_client_zero_trust_requires_framework(admin_client, acme):
    r = admin_client.post(
        f"/clients/{acme.id}/projects/new",
        data={
            "target": "new",
            "new_project_service": "zero_trust",
            "new_project_name": "Acme ZT",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "/projects/new" in r.headers["Location"]
    assert db.session.query(Project).filter_by(
        client_id=acme.id, name="Acme ZT",
    ).count() == 0


def test_new_project_for_client_admin_only(reviewer_client, acme):
    r = reviewer_client.get(
        f"/clients/{acme.id}/projects/new",
        follow_redirects=False,
    )
    assert r.status_code in (302, 403, 404)


def test_client_detail_page_shows_start_new_project_button_for_admin(
    admin_client, acme,
):
    r = admin_client.get(f"/clients/{acme.id}")
    assert r.status_code == 200
    assert b"Start a new project" in r.data


def test_client_detail_page_hides_button_for_reviewer(
    reviewer_client, acme,
):
    r = reviewer_client.get(f"/clients/{acme.id}")
    assert r.status_code == 200
    assert b"Start a new project" not in r.data


def test_adopt_existing_project_path_label_when_used(
    admin_client, acme, admin, acme_repo_with_file,
):
    """The audit row's `via` detail field distinguishes paths."""
    _, art = acme_repo_with_file
    existing = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="Existing", stage="intake", created_by_id=admin.id,
    )
    db.session.add(existing)
    db.session.commit()
    admin_client.post(
        f"/clients/{acme.id}/adopt-artifact/{art.id}",
        data={"target": "existing", "existing_project_id": existing.id},
    )
    entry = (
        db.session.query(AuditEntry)
        .filter_by(action="artifact.adopted_into_project", target_id=art.id)
        .first()
    )
    assert entry is not None
    assert entry.details.get("via") == "adopt_existing"
