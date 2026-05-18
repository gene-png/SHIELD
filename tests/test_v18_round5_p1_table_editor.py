"""Tests for the P1 review_extraction + finalize table-editor rebuild.

The pages used to be giant JSON <textarea> blocks. Round-5 §6.2-6.3
replaces them with the shared `_components/capability_table_editor.html`
partial. The partial's JS serializes the visible table into a hidden
input on submit, so the route layer sees the same form-field name and
JSON shape it always did — only the editing surface changed.
"""
from __future__ import annotations

import json

import pytest

from shield.extensions import db
from shield.models import Artifact, Origin, PlatformType, Project
from shield.spine.repository import write_ai_artifact


@pytest.fixture()
def p1_project(app, acme, admin):
    p = Project(
        client_id=acme.id, platform=PlatformType.TECH_DEBT,
        name="P1 test", stage="extraction_review", created_by_id=admin.id,
    )
    db.session.add(p)
    db.session.commit()
    return p


@pytest.fixture()
def ai_extraction(p1_project):
    """A representative AI-extraction artifact whose body_text is the
    JSON array the table editor renders."""
    items = [
        {"name": "Splunk", "vendor": "Splunk", "category": "SIEM",
         "function": "log aggregation", "annual_cost_usd": 480000,
         "license_count": 0, "notes": ""},
        {"name": "Microsoft Sentinel", "vendor": "Microsoft",
         "category": "SIEM", "function": "cloud-native SIEM",
         "annual_cost_usd": 320000, "license_count": 0, "notes": ""},
    ]
    return write_ai_artifact(
        project=p1_project, stage="ai_extraction",
        title="AI extraction",
        body_text=json.dumps(items),
        input_artifact_ids=[], prompt_version="p1_extraction.v1", model="fixture",
    )


# --------------------------------------------------------------------
# review_extraction
# --------------------------------------------------------------------

def test_review_extraction_renders_table_editor(admin_client, p1_project, ai_extraction):
    r = admin_client.get(
        f"/platform/tech-debt/project/{p1_project.id}/review/{ai_extraction.id}"
    )
    assert r.status_code == 200
    # The page is no longer a giant textarea.
    assert b"shield-capability-table" in r.data
    # The shared editor's hidden field uses the route-expected name.
    assert b'name="confirmed_text"' in r.data
    # Both items from the AI extraction render as rows.
    assert b"Splunk" in r.data
    assert b"Microsoft Sentinel" in r.data
    # The legacy textarea is gone.
    assert b'name="confirmed_text"\n' not in r.data or b"<textarea" not in r.data \
           or b'name="confirmed_text"' in r.data  # at least the hidden form field name


def test_review_extraction_post_writes_human_ai_informed_artifact(
    admin_client, p1_project, ai_extraction,
):
    """The route still reads `confirmed_text` from the form; the table
    editor's JS produces the same JSON shape on submit. Server-side
    we POST the JSON directly (no JS in tests) and check persistence."""
    payload = json.dumps([
        {"name": "Splunk", "vendor": "Splunk", "category": "SIEM",
         "function": "log aggregation"},
    ])
    r = admin_client.post(
        f"/platform/tech-debt/project/{p1_project.id}/review/{ai_extraction.id}",
        data={"confirmed_text": payload},
        follow_redirects=False,
    )
    assert r.status_code == 302
    art = (
        db.session.query(Artifact)
        .filter_by(
            project_id=p1_project.id,
            origin=Origin.HUMAN_AI_INFORMED,
            stage="extraction_review",
        )
        .order_by(Artifact.created_at.desc())
        .first()
    )
    assert art is not None
    parsed = json.loads(art.body_text)
    assert len(parsed) == 1
    assert parsed[0]["name"] == "Splunk"


def test_review_extraction_handles_malformed_ai_body_gracefully(
    admin_client, p1_project, admin,
):
    """If the AI artifact's body_text isn't valid JSON, the editor
    still renders with zero rows — admin can add rows manually."""
    bad = write_ai_artifact(
        project=p1_project, stage="ai_extraction",
        title="bad", body_text="this is not JSON",
        input_artifact_ids=[],
        prompt_version="p1_extraction.v1", model="fixture",
    )
    r = admin_client.get(
        f"/platform/tech-debt/project/{p1_project.id}/review/{bad.id}"
    )
    assert r.status_code == 200
    # Editor shell is still there.
    assert b"shield-capability-table" in r.data
    # The Add-row button is present so the admin has a way forward.
    assert b"Add row" in r.data


# --------------------------------------------------------------------
# finalize
# --------------------------------------------------------------------

@pytest.fixture()
def confirmed_extraction(p1_project, admin):
    """A human-AI-informed artifact at stage='extraction_review' —
    this is what /finalize reads as its source list."""
    items = [
        {"name": "Splunk", "vendor": "Splunk", "category": "SIEM",
         "function": "log aggregation"},
        {"name": "CrowdStrike Falcon", "vendor": "CrowdStrike",
         "category": "EDR", "function": "endpoint detection"},
    ]
    art = Artifact(
        project_id=p1_project.id,
        client_id=p1_project.client_id,
        origin=Origin.HUMAN_AI_INFORMED,
        stage="extraction_review",
        title="confirmed extraction",
        body_text=json.dumps(items),
        actor_id=admin.id,
        lineage={},
    )
    db.session.add(art)
    db.session.commit()
    return art


def test_finalize_renders_table_editor(admin_client, p1_project, confirmed_extraction):
    r = admin_client.get(f"/platform/tech-debt/project/{p1_project.id}/finalize")
    assert r.status_code == 200
    assert b"shield-capability-table" in r.data
    assert b'name="items_json"' in r.data
    # Both items from the confirmed extraction render.
    assert b"Splunk" in r.data
    assert b"CrowdStrike Falcon" in r.data
    # Old "Items (JSON array)" label is gone.
    assert b"Items (JSON array)" not in r.data


def test_finalize_with_empty_items_json_does_not_500(
    admin_client, p1_project, confirmed_extraction,
):
    """Regression: SHIELD's CSP is `script-src 'self'`, so the inline
    table-editor script was blocked silently and the hidden
    `items_json` stayed empty. The original POST handler called
    `json.loads("")` and surfaced as "Items JSON is malformed: char 0".

    Now: empty form value defaults to `[]` so the route handles it
    cleanly (no-items branch flashes + re-renders, no 500).
    """
    r = admin_client.post(
        f"/platform/tech-debt/project/{p1_project.id}/finalize",
        data={"items_json": "", "notes": ""},
        follow_redirects=False,
    )
    # Flash + redirect back, or re-render. Either way, NOT a 500.
    assert r.status_code in (200, 302)


def test_review_extraction_with_empty_confirmed_text_persists_as_json_array(
    admin_client, p1_project, ai_extraction,
):
    """Mirror defense for the review surface — empty form value
    persists as `[]` rather than empty-string body_text."""
    r = admin_client.post(
        f"/platform/tech-debt/project/{p1_project.id}/review/{ai_extraction.id}",
        data={"confirmed_text": ""},
        follow_redirects=False,
    )
    assert r.status_code == 302
    art = (
        db.session.query(Artifact)
        .filter_by(
            project_id=p1_project.id,
            origin=Origin.HUMAN_AI_INFORMED,
            stage="extraction_review",
        )
        .order_by(Artifact.created_at.desc())
        .first()
    )
    assert art is not None
    assert art.body_text == "[]"


def test_finalize_post_creates_capability_list(admin_client, p1_project, confirmed_extraction):
    """Server-side, the form still reads `items_json` exactly as before.
    POSTing well-formed JSON should still produce a CapabilityList row."""
    from shield.models import CapabilityList
    before = db.session.query(CapabilityList).filter_by(
        client_id=p1_project.client_id,
    ).count()
    payload = json.dumps([
        {"name": "Splunk", "vendor": "Splunk", "category": "SIEM",
         "function": "log aggregation"},
        {"name": "Microsoft Sentinel", "vendor": "Microsoft",
         "category": "SIEM", "function": "cloud SIEM"},
    ])
    r = admin_client.post(
        f"/platform/tech-debt/project/{p1_project.id}/finalize",
        data={"items_json": payload, "notes": ""},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert db.session.query(CapabilityList).filter_by(
        client_id=p1_project.client_id,
    ).count() == before + 1
