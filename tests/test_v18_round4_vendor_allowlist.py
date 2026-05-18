"""Tests for the vendor-allowlist NER false-positive fix.

The user reported on the admin-final v2 capability list that vendor
names were being redacted as PERSON/LOCATION/NRP — "Commvault" →
"[REDACTED_PERSON]", "Cisco" → "[REDACTED_LOCATION]", "Atlassian" →
"[REDACTED_NRP]". The vendor allowlist pre-masks those tokens before
Presidio runs so its NER never sees them.

These tests cover:
  1. Each vendor name from the reported failure stays intact after
     a full-mode redact() call.
  2. Real PII next to a vendor name still gets redacted (allowlist
     scope is surgical, not "skip everything in this row").
  3. The regex layer still catches emails / phones inside vendor
     strings.
"""
from __future__ import annotations

import pytest

from shield.ai.redact import redact
from shield.ai.vendor_allowlist import (
    compiled_pattern,
    mask_allowlisted,
    unmask_allowlisted,
)

# --------------------------------------------------------------------
# Helpers used inline by other redact tests
# --------------------------------------------------------------------

def test_mask_and_unmask_roundtrips_text():
    text = "We use Microsoft Defender for Endpoint and Cisco Umbrella."
    masked, mapping = mask_allowlisted(text)
    # The vendor names shouldn't appear literally in the masked text.
    assert "Microsoft Defender" not in masked
    assert "Cisco" not in masked
    # Mapping carries the originals so we can restore.
    restored = unmask_allowlisted(masked, mapping)
    assert restored == text


def test_compiled_pattern_is_case_insensitive():
    pat = compiled_pattern()
    assert pat.search("cisco") is not None
    assert pat.search("CISCO") is not None
    assert pat.search("Cisco") is not None


def test_compiled_pattern_uses_word_boundaries():
    pat = compiled_pattern()
    # "Cisco" inside a larger token should NOT match (no partial replace).
    assert pat.search("PiscoCisco") is None
    # But adjacent punctuation IS a boundary.
    assert pat.search("(Cisco)") is not None
    assert pat.search("Cisco.") is not None


# --------------------------------------------------------------------
# The reported failure modes from the user's table — each must stay intact
# --------------------------------------------------------------------

@pytest.mark.parametrize("vendor", [
    "Commvault",
    "Cisco",
    "Cisco Secure Firewall",
    "Atlassian",
    "Jamf",
    "Zscaler",
    "Tenable",
    "Intune",
    "Entra",
    "Rapid7",
    "Defender for Cloud Apps",
])
def test_known_vendor_names_survive_full_redaction(vendor):
    """Each reported false-positive case must pass through full mode.

    Note: Presidio may or may not be available in the test env. The
    pre-mask step still applies; if Presidio is present, the sentinel
    keeps the vendor name hidden from it; if Presidio is absent, the
    regex-only fallback never touches the vendor name anyway.
    """
    text = f"We use {vendor} as our primary tool."
    out, _ = redact(text, mode="full")
    assert vendor in out, (
        f"Vendor name {vendor!r} got redacted: {out!r}"
    )
    # And specifically: no [REDACTED_*] token covering the vendor.
    assert "[REDACTED_" not in out or vendor.split()[0] in out


# --------------------------------------------------------------------
# Defense-in-depth: actual PII still gets redacted
# --------------------------------------------------------------------

def test_email_still_redacted_inside_vendor_string():
    """A real email next to a vendor name is still PII."""
    text = "Contact alice@example.com about our Cisco renewal."
    out, report = redact(text, mode="full")
    assert "alice@example.com" not in out
    assert "[REDACTED_EMAIL]" in out
    assert "Cisco" in out
    assert report.counts.get("EMAIL") == 1


def test_phone_still_redacted_inside_vendor_string():
    text = "Call 555-123-4567 for the Tenable account team."
    out, _ = redact(text, mode="full")
    assert "555-123-4567" not in out
    assert "Tenable" in out


def test_per_project_term_still_masks_when_adjacent_to_vendor():
    """The per-project literal terms (client org name) take precedence
    over the vendor allowlist — those are explicit don't-leak rules.

    `redact` runs extra_terms FIRST, then regex, then NER (with the
    vendor pre-mask). So a client-name token gets masked even though
    Cisco does not.
    """
    text = "Acme Corp uses Cisco Secure for all of finance."
    out, _ = redact(
        text, mode="full", extra_terms=["Acme Corp"],
    )
    assert "Acme Corp" not in out
    assert "[REDACTED_TERM]" in out
    assert "Cisco" in out


def test_real_person_still_redacted_with_vendor_nearby():
    """Make sure the allowlist doesn't accidentally cloak a real PII
    PERSON match that happens to sit next to a vendor name.

    Skips itself if Presidio isn't available (regex-only mode has
    no PERSON detector).
    """
    text = "Bob Henderson manages our CrowdStrike deployment."
    out, report = redact(text, mode="full")
    if not report.presidio_available:
        pytest.skip("Presidio NER not installed; PERSON detection N/A")
    assert "CrowdStrike" in out
    assert "Bob Henderson" not in out
    assert "[REDACTED_PERSON]" in out
