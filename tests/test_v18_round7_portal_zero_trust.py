"""Round-7 follow-up: the client-facing Zero Trust questionnaire and
the routing changes that take a client straight into it after intake
or service request.

Three pinned outcomes:
  1. After finishing /portal/confirm with zero_trust selected, the
     client is redirected to /portal/zero-trust (not /portal/).
  2. Submitting a Zero Trust service request also routes the client
     into the questionnaire rather than dead-ending on the dashboard.
  3. /portal/zero-trust auto-creates a ZT Project (with the right
     framework guess) and renders the questionnaire; answering one
     row writes a QuestionnaireResponse with CLIENT_ASSERTED tier.
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


@pytest.fixture()
def client_with_member(app, admin):
    """A Client + a CLIENT-role user with an accepted membership. Used
    as the actor for portal-side tests."""
    c = Client(
        name="ZT Test Org", legal_name="ZT Test Org",
        service_interests=["zero_trust"],
        intake_completed_at=None,
    )
    db.session.add(c)
    db.session.commit()
    u = User(sub="zt-user", email="zt@example", display_name="ZT User",
             role=Role.CLIENT)
    db.session.add(u)
    db.session.commit()
    db.session.add(ClientMembership(
        client_id=c.id, user_id=u.id,
        membership_role="primary_poc",
        invited_by_id=admin.id, accepted_at=datetime.utcnow(),
    ))
    db.session.commit()
    return c, u


@pytest.fixture()
def zt_portal(client, client_with_member):
    """Test client logged in as the CLIENT-role user."""
    _, u = client_with_member
    from tests.conftest import _login
    _login(client, u.id)
    return client


# --------------------------------------------------------------------
# /portal/confirm POST → /portal/zero-trust when ZT is selected
# --------------------------------------------------------------------

def test_confirm_redirects_to_zt_when_service_includes_it(
    zt_portal, client_with_member,
):
    r = zt_portal.post("/portal/confirm", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/portal/zero-trust")
    # The intake stamp also fired.
    c, _ = client_with_member
    db.session.refresh(c)
    assert c.intake_completed_at is not None


def test_confirm_skips_zt_redirect_when_not_selected(zt_portal, client_with_member):
    """A client whose service_interests doesn't include zero_trust
    still lands on /portal/ on confirm."""
    c, _ = client_with_member
    c.service_interests = ["tech_debt"]
    db.session.commit()
    r = zt_portal.post("/portal/confirm", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/portal/")


# --------------------------------------------------------------------
# /portal/services/request POST → /portal/zero-trust when service=zero_trust
# --------------------------------------------------------------------

def test_service_request_for_zero_trust_routes_client_to_questionnaire(
    zt_portal,
):
    r = zt_portal.post(
        "/portal/services/request",
        data={"service": "zero_trust", "notes": "test", "deadline": ""},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/portal/zero-trust")


def test_service_request_for_tech_debt_still_lands_on_dashboard(zt_portal):
    """Non-ZT requests keep the old behavior: notify admin, send the
    client back to the dashboard."""
    r = zt_portal.post(
        "/portal/services/request",
        data={"service": "tech_debt", "notes": "x", "deadline": ""},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/portal/")


# --------------------------------------------------------------------
# /portal/zero-trust: GET + answer + submit
# --------------------------------------------------------------------

def test_portal_zt_get_creates_project_and_renders_framework(
    zt_portal, client_with_member,
):
    c, _ = client_with_member
    r = zt_portal.get("/portal/zero-trust")
    assert r.status_code == 200
    assert b"Zero Trust assessment" in r.data
    # A ZT project was created on demand.
    proj = (db.session.query(Project)
            .filter_by(client_id=c.id, platform=PlatformType.ZERO_TRUST)
            .first())
    assert proj is not None
    # Default framework is CISA ZTMM (the safe default).
    assert proj.framework == "cisa_ztmm_v2"


def test_portal_zt_get_routes_back_when_zt_not_in_interests(
    zt_portal, client_with_member,
):
    c, _ = client_with_member
    c.service_interests = ["tech_debt"]
    db.session.commit()
    r = zt_portal.get("/portal/zero-trust", follow_redirects=False)
    assert r.status_code == 302
    # Lands on /portal/services so the client can add the interest.
    assert "/portal/services" in r.headers["Location"]


def test_portal_zt_answer_saves_response_as_client_asserted(
    zt_portal, client_with_member,
):
    c, u = client_with_member
    # GET creates the project; then save one answer.
    zt_portal.get("/portal/zero-trust")
    proj = (db.session.query(Project)
            .filter_by(client_id=c.id, platform=PlatformType.ZERO_TRUST)
            .first())
    # Pick any control id the loaded framework exposes.
    from shield.p2_zerotrust.frameworks import FRAMEWORKS
    fw = FRAMEWORKS.get(proj.framework)
    assert fw and fw.controls, "framework didn't load with controls"
    cid = fw.controls[0].id
    r = zt_portal.post(
        "/portal/zero-trust/answer",
        data={"control_id": cid, "answer": "partial",
              "rationale": "rolling out"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    response = (db.session.query(QuestionnaireResponse)
                .filter_by(project_id=proj.id, control_id=cid).one())
    assert response.answer == "partial"
    assert response.rationale == "rolling out"
    assert response.trust_tier == TrustTier.CLIENT_ASSERTED
    assert response.attributed_user_id == u.id


def test_portal_zt_submit_locks_answers_and_moves_stage(
    zt_portal, client_with_member,
):
    c, _ = client_with_member
    zt_portal.get("/portal/zero-trust")
    proj = (db.session.query(Project)
            .filter_by(client_id=c.id, platform=PlatformType.ZERO_TRUST)
            .first())
    # Save one answer so there's a response to lock.
    from shield.p2_zerotrust.frameworks import FRAMEWORKS
    fw = FRAMEWORKS.get(proj.framework)
    cid = fw.controls[0].id
    zt_portal.post(
        "/portal/zero-trust/answer",
        data={"control_id": cid, "answer": "partial", "rationale": ""},
    )
    # Submit.
    r = zt_portal.post("/portal/zero-trust/submit", follow_redirects=False)
    assert r.status_code == 302
    db.session.refresh(proj)
    assert proj.stage == "submitted"
    resp = (db.session.query(QuestionnaireResponse)
            .filter_by(project_id=proj.id, control_id=cid).one())
    assert resp.locked is True
    assert resp.submitted_at is not None


def test_portal_zt_submit_rejects_edits_to_locked_responses(
    zt_portal, client_with_member,
):
    """Once submitted, the answer route refuses further edits."""
    c, _ = client_with_member
    zt_portal.get("/portal/zero-trust")
    proj = (db.session.query(Project)
            .filter_by(client_id=c.id, platform=PlatformType.ZERO_TRUST)
            .first())
    from shield.p2_zerotrust.frameworks import FRAMEWORKS
    cid = FRAMEWORKS.get(proj.framework).controls[0].id
    zt_portal.post(
        "/portal/zero-trust/answer",
        data={"control_id": cid, "answer": "partial", "rationale": ""},
    )
    zt_portal.post("/portal/zero-trust/submit")
    # Try to edit the locked answer.
    zt_portal.post(
        "/portal/zero-trust/answer",
        data={"control_id": cid, "answer": "implemented", "rationale": "x"},
    )
    resp = (db.session.query(QuestionnaireResponse)
            .filter_by(project_id=proj.id, control_id=cid).one())
    # Answer is the original locked value, not the edit attempt.
    assert resp.answer == "partial"
