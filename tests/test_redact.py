"""Unit tests for shield.ai.redact — the AI egress PII filter.

These exercise the regex layer only. The Presidio layer is gated on
spaCy being importable; CI installs that in the runtime image but we
don't pin a model in tests because the regex layer is what we depend
on for correctness. The mode wiring is checked separately.

Note: `redact()` returns a 3-tuple `(text, report, mapping)` since
the round-4 follow-up that added roundtripping. Tests that don't
exercise the mapping unpack with `_`.

Placeholder format: each match gets a uniquely-numbered placeholder
like `[REDACTED_EMAIL_0001]` — assertions use substring matches
(`b"[REDACTED_EMAIL_" in out`) rather than the old exact-form check.
"""
from __future__ import annotations

from shield.ai.redact import redact, unredact


def test_off_mode_passes_text_through_untouched():
    text = "Reach me at alice@example.com or 555-123-4567."
    out, report, mapping = redact(text, mode="off")
    assert out == text
    assert report.mode == "off"
    assert report.counts == {}
    assert mapping == {}


def test_regex_layer_masks_email():
    out, report, _ = redact("Contact alice@example.com today.", mode="regex")
    assert "alice@example.com" not in out
    assert "[REDACTED_EMAIL_" in out
    assert report.counts.get("EMAIL") == 1


def test_regex_layer_masks_phone_in_multiple_formats():
    cases = [
        "Call (555) 123-4567 anytime.",
        "Phone: 555.123.4567 ext 1",
        "+1-555-123-4567",
    ]
    for raw in cases:
        out, _, _ = redact(raw, mode="regex")
        assert "555" not in out, f"phone leaked through in: {raw!r} -> {out!r}"
        assert "[REDACTED_PHONE_" in out


def test_regex_layer_masks_ssn():
    out, report, _ = redact("SSN on file: 123-45-6789.", mode="regex")
    assert "123-45-6789" not in out
    assert report.counts.get("SSN") == 1


def test_regex_layer_masks_credit_card():
    # 16-digit Visa-shaped number.
    out, report, _ = redact("Card 4111 1111 1111 1111 charged.", mode="regex")
    assert "4111" not in out
    assert report.counts.get("CREDIT_CARD") == 1


def test_regex_layer_masks_url_and_strips_query_token():
    raw = "See https://example.com/callback?token=abc123 for details."
    out, report, _ = redact(raw, mode="regex")
    assert "example.com" not in out
    assert "token=abc123" not in out
    assert "[REDACTED_URL_" in out
    assert report.counts.get("URL") == 1


def test_regex_layer_masks_us_street_address():
    out, report, _ = redact("Office is at 123 Maple Street, Anytown.", mode="regex")
    assert "Maple Street" not in out
    assert report.counts.get("STREET") == 1


def test_regex_layer_masks_ipv4():
    out, report, _ = redact("Server at 10.0.0.5 went down.", mode="regex")
    assert "10.0.0.5" not in out
    assert report.counts.get("IP_ADDRESS") == 1


def test_extra_terms_mask_client_name_case_insensitive():
    out, report, _ = redact(
        "ACME Corp lost data last week. acme corp filed a report.",
        mode="regex",
        extra_terms=["ACME Corp"],
    )
    assert "ACME Corp" not in out
    assert "acme corp" not in out.lower() or "[REDACTED_TERM_" in out
    assert report.counts.get("TERM") == 2
    assert "ACME Corp" in report.extra_terms_applied


def test_extra_terms_shorter_than_three_chars_are_dropped():
    # Refuse 1-2 char terms — those would obliterate prose.
    out, _, _ = redact("IT is the team.", mode="regex", extra_terms=["IT"])
    assert "IT is the team." == out


def test_empty_text_is_passed_through():
    out, report, mapping = redact("", mode="full")
    assert out == ""
    assert report.counts == {}
    assert mapping == {}


def test_report_to_dict_is_lineage_safe():
    _, report, _ = redact("alice@example.com", mode="regex")
    payload = report.to_dict()
    # Lineage payload must be JSON-serializable primitives only.
    import json
    json.dumps(payload)
    assert payload["redaction_mode"] == "regex"
    assert payload["redaction_total"] >= 1
    assert payload["redaction_counts"]["EMAIL"] == 1


def test_report_records_no_raw_values_only_counts():
    """The whole point of the redactor is to not re-leak the secret.

    The structured report must not contain any of the values that
    were masked — only their categories and counts. The mapping
    DOES carry originals (intentionally — for roundtripping) but
    it's returned separately and never lands in the report.
    """
    raw = "alice@example.com / 555-123-4567 / 123-45-6789"
    _, report, _ = redact(raw, mode="regex")
    serialized = repr(report.to_dict())
    assert "alice@example.com" not in serialized
    assert "555" not in serialized
    assert "123-45-6789" not in serialized


# --------------------------------------------------------------------
# Round-trip: redact + unredact restores the originals
# --------------------------------------------------------------------

def test_unredact_restores_originals_from_mapping():
    raw = "Contact alice@example.com or call 555-123-4567."
    out, _, mapping = redact(raw, mode="regex")
    assert "alice@example.com" not in out
    assert "555-123-4567" not in out
    # Now roundtrip — Claude's response would carry these placeholders
    # verbatim through the mask + back.
    restored = unredact(out, mapping)
    assert restored == raw


def test_unredact_handles_partial_placeholder_subset():
    """If only some placeholders appear in the response (e.g. Claude
    used them in a summary but not in a separate sentence), only those
    get restored — the rest of the response is untouched.
    """
    raw = "alice@example.com / 555-123-4567"
    _, _, mapping = redact(raw, mode="regex")
    # Pretend Claude returned a response mentioning only the email
    # placeholder.
    placeholders = list(mapping.keys())
    email_placeholder = next(p for p in placeholders if "EMAIL" in p)
    response = f"The contact email is {email_placeholder}."
    restored = unredact(response, mapping)
    assert restored == "The contact email is alice@example.com."


def test_unredact_passes_through_text_with_no_placeholders():
    raw = "Hello, world!"
    _, _, mapping = redact("alice@example.com", mode="regex")
    # Claude's response contains no placeholders — should be untouched.
    assert unredact(raw, mapping) == raw


def test_each_match_gets_a_unique_placeholder():
    """Two emails in one string become two different placeholders so
    the mapping can roundtrip correctly."""
    raw = "Email alice@example.com or bob@example.com."
    _, _, mapping = redact(raw, mode="regex")
    email_placeholders = [p for p in mapping if "EMAIL" in p]
    assert len(email_placeholders) == 2
    assert mapping[email_placeholders[0]] != mapping[email_placeholders[1]]
