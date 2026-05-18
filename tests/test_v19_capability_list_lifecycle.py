"""v1.9 item 4: CapabilityList gets the same archive/purge pattern as
Project and Artifact.

Pins:
  - Archive stamps metadata + writes audit; unarchive reverses.
  - Purge requires typed `v<version>` confirmation; nothing is changed
    when the phrase is wrong.
  - latest_capability_list() skips archived and purged lists so the
    admin's "current" view doesn't surface tombstones.
  - Default capability-list table on clients/detail.html shows only
    active rows; archived live behind the toggle.
"""
from __future__ import annotations

import pytest

from shield.extensions import db
from shield.models import AuditEntry, CapabilityList, Origin


@pytest.fixture()
def cl_v2(app, acme, admin):
    """A second capability list on the seeded acme client, so we can
    test archive/restore without nuking the v1 fixture."""
    cl = CapabilityList(
        client_id=acme.id, version=2, label="v2 test list",
        origin=Origin.HUMAN_INPUT, created_by_id=admin.id,
    )
    db.session.add(cl)
    db.session.commit()
    return cl


def test_archive_stamps_metadata_and_audits(admin_client, cl_v2, admin):
    r = admin_client.post(
        f"/admin/lifecycle/capability-lists/{cl_v2.id}/archive",
        data={"reason": "rolled forward to v3"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    db.session.refresh(cl_v2)
    assert cl_v2.archived is True
    assert cl_v2.archived_at is not None
    assert cl_v2.archived_by_id == admin.id
    assert cl_v2.archived_reason == "rolled forward to v3"
    assert cl_v2.lifecycle_state == "archived"
    entry = (db.session.query(AuditEntry)
             .filter_by(action="capability_list.archived",
                        target_id=cl_v2.id).one())
    assert entry.details.get("reason") == "rolled forward to v3"
    assert entry.details.get("version") == 2


def test_unarchive_clears_metadata(admin_client, cl_v2):
    admin_client.post(
        f"/admin/lifecycle/capability-lists/{cl_v2.id}/archive",
        data={"reason": "x"},
    )
    r = admin_client.post(
        f"/admin/lifecycle/capability-lists/{cl_v2.id}/unarchive",
        follow_redirects=False,
    )
    assert r.status_code == 302
    db.session.refresh(cl_v2)
    assert cl_v2.archived is False
    assert cl_v2.archived_at is None
    assert cl_v2.lifecycle_state == "active"


def test_purge_requires_correct_version_phrase(admin_client, cl_v2):
    r = admin_client.post(
        f"/admin/lifecycle/capability-lists/{cl_v2.id}/purge",
        data={"confirmation_phrase": "v999", "reason": "x"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    db.session.refresh(cl_v2)
    assert cl_v2.purged_at is None


def test_purge_with_correct_phrase_tombstones(admin_client, cl_v2):
    r = admin_client.post(
        f"/admin/lifecycle/capability-lists/{cl_v2.id}/purge",
        data={"confirmation_phrase": "v2",
              "reason": "GDPR erasure"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    db.session.refresh(cl_v2)
    assert cl_v2.purged_at is not None
    assert cl_v2.archived is True
    assert cl_v2.lifecycle_state == "purged"
    entry = (db.session.query(AuditEntry)
             .filter_by(action="capability_list.purged",
                        target_id=cl_v2.id).one())
    assert entry.details.get("reason") == "GDPR erasure"
    assert entry.details.get("version") == 2


def test_latest_capability_list_skips_archived(app, acme, admin, cl_v2):
    """latest_capability_list() should return the newest ACTIVE list."""
    from shield.extensions import db as _db
    # Active v3.
    cl_v3 = CapabilityList(
        client_id=acme.id, version=3, label="v3",
        origin=Origin.HUMAN_INPUT, created_by_id=admin.id,
    )
    _db.session.add(cl_v3)
    _db.session.commit()
    # Archive v3 — latest should fall back to v2 (or whatever's active).
    cl_v3.archived = True
    _db.session.commit()
    latest = acme.latest_capability_list()
    assert latest is not None
    assert latest.id == cl_v2.id


def test_clients_detail_shows_archive_button_for_admin(admin_client, acme, cl_v2):
    r = admin_client.get(f"/clients/{acme.id}")
    assert r.status_code == 200
    body = r.data
    # Archive button form action references the lifecycle route.
    expected = f"/admin/lifecycle/capability-lists/{cl_v2.id}/archive".encode()
    assert expected in body


def test_archived_lists_appear_behind_toggle_with_restore(
    admin_client, acme, cl_v2,
):
    admin_client.post(
        f"/admin/lifecycle/capability-lists/{cl_v2.id}/archive",
        data={"reason": "test"},
    )
    r = admin_client.get(f"/clients/{acme.id}")
    body = r.data
    # The archived list section renders with restore + purge controls.
    assert b"Archived capability lists" in body
    expected_restore = f"/admin/lifecycle/capability-lists/{cl_v2.id}/unarchive".encode()
    assert expected_restore in body
