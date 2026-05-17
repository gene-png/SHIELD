"""The non-negotiable invariants from the unified-portal spec.

These tests are the contract the rework is built against. If any of them
break, the integrity model is broken — fix the code, not the test.
"""
from __future__ import annotations

import io

import pytest

from shield.extensions import db
from shield.models import (
    Artifact,
    CapabilityList,
    Client,
    Origin,
    PlatformType,
    Project,
    ReuseStatus,
    Role,
    User,
)
from shield.spine.repository import (
    promote_artifact,
    write_ai_artifact,
    write_human_ai_informed_artifact,
    write_human_artifact,
)


def _make_world():
    admin = User(sub="t-admin", email="t-admin", display_name="t-admin", role=Role.ADMIN)
    client = Client(name="TestCo")
    db.session.add_all([admin, client])
    db.session.commit()
    cl = CapabilityList(client_id=client.id, version=1, label="v1", origin=Origin.HUMAN_INPUT,
                        created_by_id=admin.id)
    db.session.add(cl)
    db.session.commit()
    project = Project(client_id=client.id, platform=PlatformType.TECH_DEBT,
                      name="P", stage="intake", capability_list_version_id=cl.id)
    db.session.add(project)
    db.session.commit()
    return admin, client, project, cl


def test_human_writer_produces_human_origin(app):
    admin, _client, project, _cl = _make_world()
    art = write_human_artifact(
        project=project, stage="raw_intake", title="test",
        file_stream=io.BytesIO(b"hello"), filename="t.txt", mime_type="text/plain",
        actor=admin,
    )
    assert art.origin == Origin.HUMAN_INPUT
    assert art.lineage.get("sha256")


def test_ai_writer_has_no_actor_param_by_design(app):
    """write_ai_artifact() takes no `actor` — an AI artifact has no human author."""
    admin, _client, project, _cl = _make_world()
    art = write_ai_artifact(
        project=project, stage="ai_extraction",
        title="ai",
        body_text="{}",
        input_artifact_ids=[],
        prompt_version="p1_extraction.v1",
        model="claude-opus-4-7",
    )
    assert art.origin == Origin.AI_GENERATED
    assert art.actor_id is None  # by design


def test_origin_cannot_be_mutated(app):
    """SQLAlchemy assignment of a different origin is rejected before commit."""
    admin, _client, project, _cl = _make_world()
    art = write_human_artifact(
        project=project, stage="raw_intake", title="t", file_stream=None, filename=None,
        mime_type=None, actor=admin, body_text="x",
    )
    # The @validates raises on attribute set; the SQLAlchemy listener
    # raises on flush. Wrap both lines so whichever fires first is caught.
    with pytest.raises(ValueError):
        art.origin = Origin.AI_GENERATED
        db.session.commit()
    db.session.rollback()


def test_promote_only_ai_origin(app):
    admin, _client, project, _cl = _make_world()
    h = write_human_artifact(
        project=project, stage="raw_intake", title="t", file_stream=None, filename=None,
        mime_type=None, actor=admin, body_text="x",
    )
    with pytest.raises(ValueError):
        promote_artifact(artifact=h, actor=admin, reason="trying")


def test_promote_keeps_origin(app):
    admin, _client, project, _cl = _make_world()
    a = write_ai_artifact(
        project=project, stage="ai_extraction", title="ai", body_text="{}",
        input_artifact_ids=[], prompt_version="p1_extraction.v1", model="x",
    )
    promote_artifact(artifact=a, actor=admin, reason="reviewed")
    refreshed = db.session.get(Artifact, a.id)
    assert refreshed.origin == Origin.AI_GENERATED   # origin unchanged
    assert refreshed.reuse_status == ReuseStatus.APPROVED


def test_promote_requires_reason(app):
    admin, _client, project, _cl = _make_world()
    a = write_ai_artifact(
        project=project, stage="ai_extraction", title="ai", body_text="{}",
        input_artifact_ids=[], prompt_version="p1_extraction.v1", model="x",
    )
    with pytest.raises(ValueError):
        promote_artifact(artifact=a, actor=admin, reason="")


def test_human_ai_informed_is_distinct_origin(app):
    admin, _client, project, _cl = _make_world()
    a = write_ai_artifact(
        project=project, stage="ai_extraction", title="ai", body_text="[]",
        input_artifact_ids=[], prompt_version="p1_extraction.v1", model="x",
    )
    h = write_human_ai_informed_artifact(
        project=project, stage="extraction_review", title="confirmed",
        body_text="[]", cites_artifact_ids=[a.id], actor=admin,
    )
    assert h.origin == Origin.HUMAN_AI_INFORMED
    assert h.origin not in (Origin.HUMAN_INPUT, Origin.AI_GENERATED)
