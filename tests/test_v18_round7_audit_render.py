"""Round-7 §6.6 / §3.3: the audit log details column renders as plain
English, with raw JSON behind a "Show raw JSON" toggle.

Pins the formatter dispatch (per-action one-liners + generic fallback)
and verifies the audit_index template renders the summary instead of
the old `<details><pre>JSON</pre></details>` headline.
"""
from __future__ import annotations

import pytest

from shield.extensions import db
from shield.models import AuditEntry
from shield.spine.audit_render import (
    FORMATTERS,
    render_audit_details,
)


# --------------------------------------------------------------------
# Per-action formatter outputs
# --------------------------------------------------------------------

def test_artifact_write_human_renders_filename_and_stage():
    out = render_audit_details(
        "artifact.write_human",
        {"stage": "raw_intake", "filename": "inventory.csv", "size_bytes": 1234},
    )
    assert "inventory.csv" in out
    assert "raw_intake" in out


def test_artifact_promote_includes_reason_when_present():
    out = render_audit_details("artifact.promote", {"reason": "validated against three controls"})
    assert "validated against three controls" in out
    assert "reuse" in out.lower()


def test_project_create_mentions_name_and_platform():
    out = render_audit_details(
        "project.create",
        {"name": "Acme TD Q2", "platform": "tech_debt", "created_from": "adopt_flow"},
    )
    assert "Acme TD Q2" in out
    assert "tech debt" in out
    assert "adopt_flow" in out


def test_intake_upload_includes_size_bytes_formatted():
    out = render_audit_details(
        "intake.upload",
        {"filename": "inv.xlsx", "size_bytes": 245678, "actor_role": "client"},
    )
    assert "inv.xlsx" in out
    # The byte count is comma-formatted for readability.
    assert "245,678" in out


def test_access_denied_mentions_path_and_method():
    out = render_audit_details("access_denied", {"path": "/clients/abc/detail", "method": "GET"})
    assert "/clients/abc/detail" in out
    assert "GET" in out


def test_message_posted_uses_thread_distinction():
    project_thread = render_audit_details("message.posted", {"project_id": "p1", "length": 42})
    client_thread = render_audit_details("message.posted", {"length": 42})
    assert "project thread" in project_thread
    assert "client-level thread" in client_thread


def test_seed_actions_match_prefix():
    out = render_audit_details(
        "seed.capability_list",
        {"version": 1, "items": 75},
    )
    assert "Seeded" in out
    assert "items=75" in out


# --------------------------------------------------------------------
# Fallback path — unknown action with arbitrary details
# --------------------------------------------------------------------

def test_unknown_action_falls_back_to_key_value_pairs():
    out = render_audit_details("widget.created", {"color": "blue", "size": 12})
    # Both pieces of state should appear, key=value style.
    assert "color=blue" in out
    assert "size=12" in out


def test_unknown_action_with_empty_details_renders_dash():
    assert render_audit_details("widget.created", None) == "—"
    assert render_audit_details("widget.created", {}) == "—"


def test_formatter_exception_falls_back_silently():
    """A buggy formatter must NOT break the audit page — it should
    fall through to the generic key=value renderer instead of raising."""
    bad_action = "test.exploding"
    FORMATTERS[bad_action] = lambda d: 1 / 0  # forces ZeroDivisionError
    try:
        out = render_audit_details(bad_action, {"safe": True})
        assert "safe=True" in out
    finally:
        del FORMATTERS[bad_action]


# --------------------------------------------------------------------
# Template integration — audit_index renders the summary, hides JSON
# --------------------------------------------------------------------

def test_audit_index_renders_plain_english_and_hides_json(admin_client, app, admin):
    # Seed an audit row with a known action whose formatter produces
    # a distinctive phrase.
    db.session.add(AuditEntry(
        action="artifact.write_human",
        actor_email=admin.email,
        actor_id=admin.id,
        target_type="artifact",
        details={"stage": "raw_intake", "filename": "smoke.csv"},
    ))
    db.session.commit()

    r = admin_client.get("/audit/")
    assert r.status_code == 200
    body = r.data
    # Plain-English summary appears.
    assert b"smoke.csv" in body
    assert b"raw_intake" in body
    # JSON toggle is named "Show raw JSON" (not the old summary preview).
    assert b"Show raw JSON" in body
    # The raw JSON body still exists inside the <details> so compliance
    # auditors can expand it.
    assert b"&#34;filename&#34;" in body or b"\"filename\"" in body
