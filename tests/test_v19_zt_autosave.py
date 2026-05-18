"""v1.9 item 1: Zero Trust questionnaire auto-saves per row.

Both surfaces (admin /platform/zero-trust/.../answer and client
/portal/zero-trust/answer) now branch on the HX-Request header:
  - With the header: return a small "saved ✓" fragment, 200.
  - Without it: keep the prior flash+redirect behavior.

Plus the rendered templates carry the `hx-include="closest tr"`
attribute so each row sends only its own control_id + answer +
rationale, avoiding the pre-v1.9 form-wide-include bug.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from shield.extensions import db
from shield.models import (
    Client,
    ClientMembership,
    PlatformType,
    Project,
    QuestionnaireResponse,
    Role,
    TrustTier,
    User,
)


# --------------------------------------------------------------------
# Admin side — p2.answer
# --------------------------------------------------------------------

@pytest.fixture()
def admin_p2(app, acme, admin):
    p = Project(
        client_id=acme.id, platform=PlatformType.ZERO_TRUST,
        name="autosave P2", stage="intake",
        framework="cisa_ztmm_v2", created_by_id=admin.id,
    )
    db.session.add(p)
    db.session.commit()
    return p


def test_p2_answer_returns_fragment_for_htmx(admin_client, admin_p2, admin):
    from shield.p2_zerotrust.frameworks import FRAMEWORKS
    cid = FRAMEWORKS.get(admin_p2.framework).controls[0].id
    r = admin_client.post(
        f"/platform/zero-trust/project/{admin_p2.id}/answer",
        headers={"HX-Request": "true"},
        data={"control_id": cid, "answer": "partial", "rationale": ""},
    )
    assert r.status_code == 200
    assert b"saved" in r.data
    # The DB still got the write.
    saved = (db.session.query(QuestionnaireResponse)
             .filter_by(project_id=admin_p2.id, control_id=cid).one())
    assert saved.answer == "partial"
    assert saved.trust_tier == TrustTier.ADMIN_ASSISTED


def test_p2_answer_non_htmx_still_redirects(admin_client, admin_p2):
    from shield.p2_zerotrust.frameworks import FRAMEWORKS
    cid = FRAMEWORKS.get(admin_p2.framework).controls[0].id
    r = admin_client.post(
        f"/platform/zero-trust/project/{admin_p2.id}/answer",
        data={"control_id": cid, "answer": "partial", "rationale": ""},
        follow_redirects=False,
    )
    assert r.status_code == 302


def test_p2_workspace_renders_hx_include_closest_tr(admin_client, admin_p2):
    r = admin_client.get(
        f"/platform/zero-trust/project/{admin_p2.id}"
    )
    assert r.status_code == 200
    # The auto-save attributes must be in place.
    assert b'hx-include="closest tr"' in r.data
    assert b'hx-trigger="change"' in r.data
    assert b'hx-trigger="blur changed"' in r.data


# --------------------------------------------------------------------
# Client side — portal.zero_trust_answer
# --------------------------------------------------------------------

@pytest.fixture()
def zt_client_session(client, app, admin):
    c = Client(name="ZT Auto", legal_name="ZT Auto",
               service_interests=["zero_trust"],
               intake_completed_at=datetime.utcnow())
    db.session.add(c)
    db.session.commit()
    u = User(sub="zt-auto", email="auto@x", display_name="A",
             role=Role.CLIENT)
    db.session.add(u)
    db.session.commit()
    db.session.add(ClientMembership(
        client_id=c.id, user_id=u.id,
        membership_role="primary_poc",
        invited_by_id=admin.id, accepted_at=datetime.utcnow(),
    ))
    db.session.commit()
    from tests.conftest import _login
    _login(client, u.id)
    return client, c, u


def test_portal_answer_returns_fragment_for_htmx(zt_client_session):
    client, c, _ = zt_client_session
    # First GET creates the project + framework.
    client.get("/portal/zero-trust")
    project = (db.session.query(Project)
               .filter_by(client_id=c.id, platform=PlatformType.ZERO_TRUST)
               .first())
    from shield.p2_zerotrust.frameworks import FRAMEWORKS
    cid = FRAMEWORKS.get(project.framework).controls[0].id
    r = client.post(
        "/portal/zero-trust/answer",
        headers={"HX-Request": "true"},
        data={"control_id": cid, "answer": "implemented", "rationale": "ok"},
    )
    assert r.status_code == 200
    assert b"saved" in r.data
    saved = (db.session.query(QuestionnaireResponse)
             .filter_by(project_id=project.id, control_id=cid).one())
    assert saved.answer == "implemented"
    assert saved.trust_tier == TrustTier.CLIENT_ASSERTED


def test_portal_answer_htmx_error_fragment_on_locked(zt_client_session):
    """A locked response posts back an error pip, not a redirect."""
    client, c, _ = zt_client_session
    client.get("/portal/zero-trust")
    project = (db.session.query(Project)
               .filter_by(client_id=c.id, platform=PlatformType.ZERO_TRUST)
               .first())
    from shield.p2_zerotrust.frameworks import FRAMEWORKS
    cid = FRAMEWORKS.get(project.framework).controls[0].id
    # Save then submit.
    client.post("/portal/zero-trust/answer",
                data={"control_id": cid, "answer": "partial", "rationale": ""})
    client.post("/portal/zero-trust/submit")
    # Now try editing via HTMX.
    r = client.post(
        "/portal/zero-trust/answer",
        headers={"HX-Request": "true"},
        data={"control_id": cid, "answer": "implemented", "rationale": "x"},
    )
    assert r.status_code == 200
    assert b"locked" in r.data
    # And the answer didn't change.
    db.session.expire_all()
    saved = (db.session.query(QuestionnaireResponse)
             .filter_by(project_id=project.id, control_id=cid).one())
    assert saved.answer == "partial"


def test_portal_zt_template_has_hx_attributes(zt_client_session):
    client, _, _ = zt_client_session
    r = client.get("/portal/zero-trust")
    assert r.status_code == 200
    assert b'hx-include="closest tr"' in r.data
    assert b'hx-trigger="change"' in r.data
    assert b'hx-trigger="blur changed"' in r.data
    # The "auto-save" prose appears in the intro.
    assert b"auto-save" in r.data
