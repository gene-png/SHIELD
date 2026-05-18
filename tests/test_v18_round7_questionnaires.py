"""Round-7 §21 / §24 / §25: section-and-question questionnaire YAML
catalogs and their loader.

The legacy `frameworks.py` (control-based, flat catalog) keeps
serving the existing P2 questionnaire UI. The new
`questionnaires.py` exposes the richer per-question shape from the
spec — stem, cues, mappings, dimensions — that the section-by-section
UI rebuild (§21.7) will consume in a follow-up.
"""
from __future__ import annotations

import pytest

from shield.p2_zerotrust.questionnaires import (
    list_questionnaires,
    load_questionnaire,
)


def test_three_questionnaires_are_registered():
    """The three frameworks the spec calls out — CSF 2.0 HIGH, CISA
    ZTMM 2.0, and DoD ZT — are all loadable through the registry."""
    ids = list_questionnaires()
    assert "csf_2_0_high" in ids
    assert "cisa_ztmm_v2" in ids
    assert "dod_zt" in ids


def test_unknown_framework_raises():
    with pytest.raises(KeyError):
        load_questionnaire("not-a-framework")


# --------------------------------------------------------------------
# CSF 2.0 HIGH structure
# --------------------------------------------------------------------

def test_csf_high_has_twelve_sections():
    q = load_questionnaire("csf_2_0_high")
    assert len(q.sections) == 12
    titles = {s.title for s in q.sections}
    # Spot-check a few of the section names from §21.4.
    assert "Governance, Leadership & Oversight" in titles
    assert "Risk Management" in titles
    assert "Incident Response" in titles
    assert "Continuous Improvement" in titles


def test_csf_high_section1_has_three_questions_with_cf_mappings():
    q = load_questionnaire("csf_2_0_high")
    s1 = q.section_by_id("governance")
    assert s1 is not None
    assert len(s1.questions) == 3
    q11 = s1.questions[0]
    assert q11.id == "csf2.high.s1.q1"
    assert q11.number == "1.1"
    assert "Governance" in q11.stem or "governed" in q11.stem
    # The Governance & Risk subcategory mappings from §21.4 land in
    # csf_subcategories on the question.
    assert "GV.OC-01" in q11.csf_subcategories
    assert "GV.RR-01" in q11.csf_subcategories
    # Implementation groups + dimensions come through.
    assert 1 in q11.implementation_groups
    assert "governance" in q11.dimensions


def test_csf_high_answer_scale_matches_spec():
    q = load_questionnaire("csf_2_0_high")
    ids = {o.id for o in q.answer_scale}
    assert ids == {"not_implemented", "partial",
                   "largely_implemented", "fully_implemented"}


def test_csf_high_document_checklist_has_sixteen_entries():
    """§21.3: 16 documents on the HIGH-impact checklist."""
    q = load_questionnaire("csf_2_0_high")
    assert len(q.documents) == 16
    # Spot-check a couple to make sure it's the right list.
    assert any("System Security Plan" in d for d in q.documents)
    assert any("POA&M" in d for d in q.documents)


def test_csf_high_system_profile_fields_present():
    q = load_questionnaire("csf_2_0_high")
    keys = {f["key"] for f in q.system_profile_fields}
    # Required SystemProfile fields from §21.2.
    for required in ("system_name", "csam_system_id", "fips199_categorization",
                     "system_owner_name", "isso_name", "authorization_status",
                     "ato_expiration_date"):
        assert required in keys


# --------------------------------------------------------------------
# CISA ZTMM 2.0 structure
# --------------------------------------------------------------------

def test_cisa_ztmm_has_eight_sections():
    q = load_questionnaire("cisa_ztmm_v2")
    assert len(q.sections) == 8


def test_cisa_ztmm_pillars_match_spec():
    """The 5 pillars + 3 cross-cutting capabilities from §24."""
    q = load_questionnaire("cisa_ztmm_v2")
    titles = [s.title for s in q.sections]
    assert titles == [
        "Identity",
        "Devices",
        "Networks",
        "Applications and Workloads",
        "Data",
        "Visibility and Analytics",
        "Automation and Orchestration",
        "Governance",
    ]


def test_cisa_ztmm_answer_scale_is_four_levels():
    q = load_questionnaire("cisa_ztmm_v2")
    ids = [o.id for o in q.answer_scale]
    assert ids == ["traditional", "initial", "advanced", "optimal"]


def test_cisa_identity_section_q1_carries_ztmm_function_mappings():
    q = load_questionnaire("cisa_ztmm_v2")
    identity = q.section_by_id("identity")
    assert identity is not None
    q11 = identity.questions[0]
    assert q11.id == "cisa.s1.q1"
    assert "Authentication" in q11.ztmm_functions
    assert "Identity Stores" in q11.ztmm_functions


# --------------------------------------------------------------------
# DoD ZT structure
# --------------------------------------------------------------------

def test_dod_zt_has_seven_sections():
    q = load_questionnaire("dod_zt")
    assert len(q.sections) == 7


def test_dod_zt_pillars_match_spec():
    """The 7 pillars from §25."""
    q = load_questionnaire("dod_zt")
    titles = [s.title for s in q.sections]
    assert titles == [
        "User",
        "Device",
        "Network / Environment",
        "Application / Workload",
        "Data",
        "Visibility and Analytics",
        "Automation and Orchestration",
    ]


def test_dod_zt_uses_two_phase_scale():
    q = load_questionnaire("dod_zt")
    ids = [o.id for o in q.answer_scale]
    # not_yet is the "not yet implemented" floor; Target + Advanced
    # are the phases the DoD ZT strategy specifies.
    assert ids == ["not_yet", "target", "advanced"]


def test_dod_user_section_questions_carry_activities_and_phase():
    q = load_questionnaire("dod_zt")
    user_sec = q.section_by_id("user")
    assert user_sec is not None
    q12 = user_sec.questions[1]
    # PAM-focused question is Advanced-phase per §25 Q1.2.
    assert "advanced" in q12.phases
    assert "Privileged Access Management" in q12.dod_activities


# --------------------------------------------------------------------
# Cross-framework guarantees
# --------------------------------------------------------------------

def test_every_question_has_a_unique_id():
    seen = set()
    for fid in list_questionnaires():
        q = load_questionnaire(fid)
        for section in q.sections:
            for question in section.questions:
                assert question.id not in seen, (
                    f"Duplicate question id {question.id!r} in {fid}"
                )
                seen.add(question.id)


def test_question_count_is_non_trivial():
    """Each framework should have at least a handful of questions —
    catches a truncated catalog file landing by accident."""
    for fid in list_questionnaires():
        q = load_questionnaire(fid)
        assert q.question_count >= 5, (
            f"{fid} only has {q.question_count} questions — looks truncated"
        )


def test_loader_returns_immutable_dataclass():
    """Returning frozen dataclasses prevents callers from mutating the
    cached catalog and surprising the next caller."""
    q = load_questionnaire("cisa_ztmm_v2")
    with pytest.raises((AttributeError, TypeError)):
        q.sections[0].questions[0].stem = "tampered"  # type: ignore[misc]
