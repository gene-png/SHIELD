"""Structural tests for the vendored Zero Trust framework catalogs.

These guard against three regressions:
  1. A catalog JSON file going missing (load would raise at import).
  2. A catalog ending up empty (a malformed vendor-script run).
  3. The control schema drifting (every row must have id/pillar/title).

The actual catalog content is data, not code — these tests check the
*shape* and rely on the vendor script to source authoritative content.
"""
from __future__ import annotations

import pytest

from shield.p2_zerotrust.frameworks import FRAMEWORKS, Framework


@pytest.mark.parametrize("fw_id", ["cisa_ztmm_v2", "dod_zt", "nist_csf_v2"])
def test_framework_loaded(fw_id: str):
    fw = FRAMEWORKS[fw_id]
    assert isinstance(fw, Framework)
    assert fw.name
    assert fw.controls, f"{fw_id} catalog must have controls"
    assert fw.pillars, f"{fw_id} catalog must declare its pillars"


def test_nist_csf_has_full_subcategory_count():
    """NIST CSF 2.0 publishes 106+ subcategories across 6 functions.

    Our OSCAL parse should yield far more than the v0.1 starter's 6
    placeholder rows. Asserting >100 catches a vendor-script regression
    that silently drops subcategories.
    """
    fw = FRAMEWORKS["nist_csf_v2"]
    assert len(fw.controls) > 100
    pillars_seen = {c.pillar for c in fw.controls}
    assert pillars_seen == {"Govern", "Identify", "Protect", "Detect", "Respond", "Recover"}


def test_every_control_has_id_pillar_title():
    for fw in FRAMEWORKS.values():
        for c in fw.controls:
            assert c.id, f"{fw.id} has a control with no id"
            assert c.pillar, f"{fw.id} control {c.id} has no pillar"
            assert c.title, f"{fw.id} control {c.id} has no title"


def test_every_control_pillar_is_declared_on_the_framework():
    """Catch typos: if a control references a pillar that isn't in
    the framework's pillars list, the UI's pillar grouping breaks."""
    for fw in FRAMEWORKS.values():
        declared = set(fw.pillars)
        for c in fw.controls:
            assert c.pillar in declared, (
                f"{fw.id} control {c.id} uses pillar {c.pillar!r} "
                f"which is not in {sorted(declared)}"
            )


def test_control_ids_are_unique_within_each_framework():
    for fw in FRAMEWORKS.values():
        ids = [c.id for c in fw.controls]
        assert len(ids) == len(set(ids)), f"{fw.id} has duplicate control ids"
