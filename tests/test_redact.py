"""Unit tests for shield.ai.redact — the AI egress PII filter.

These exercise the regex layer only. The Presidio layer is gated on
spaCy being importable; CI installs that in the runtime image but we
don't pin a model in tests because the regex layer is what we depend
on for correctness. The mode wiring is checked separately.
"""
from __future__ import annotations

from shield.ai.redact import redact


def test_off_mode_passes_text_through_untouched():
    text = "Reach me at alice@example.com or 555-123-4567."
    out, report = redact(text, mode="off")
    assert out == text
    assert report.mode == "off"
    assert report.counts == {}


def test_regex_layer_masks_email():
    out, report = redact("Contact alice@example.com today.", mode="regex")
    assert "alice@example.com" not in out
    assert "[REDACTED_EMAIL]" in out
    assert report.counts.get("EMAIL") == 1


def test_regex_layer_masks_phone_in_multiple_formats():
    cases = [
        "Call (555) 123-4567 anytime.",
        "Phone: 555.123.4567 ext 1",
        "+1-555-123-4567",
    ]
    for raw in cases:
        out, _ = redact(raw, mode="regex")
        assert "555" not in out, f"phone leaked through in: {raw!r} -> {out!r}"
        assert "[REDACTED_PHONE]" in out


def test_regex_layer_masks_ssn():
    out, report = redact("SSN on file: 123-45-6789.", mode="regex")
    assert "123-45-6789" not in out
    assert report.counts.get("SSN") == 1


def test_regex_layer_masks_credit_card():
    # 16-digit Visa-shaped number.
    out, report = redact("Card 4111 1111 1111 1111 charged.", mode="regex")
    assert "4111" not in out
    assert report.counts.get("CREDIT_CARD") == 1


def test_regex_layer_masks_url_and_strips_query_token():
    raw = "See https://example.com/callback?token=abc123 for details."
    out, report = redact(raw, mode="regex")
    assert "example.com" not in out
    assert "token=abc123" not in out
    assert "[REDACTED_URL]" in out
    assert report.counts.get("URL") == 1


def test_regex_layer_masks_us_street_address():
    out, report = redact("Office is at 123 Maple Street, Anytown.", mode="regex")
    assert "Maple Street" not in out
    assert report.counts.get("STREET") == 1


def test_regex_layer_masks_ipv4():
    out, report = redact("Server at 10.0.0.5 went down.", mode="regex")
    assert "10.0.0.5" not in out
    assert report.counts.get("IP_ADDRESS") == 1


def test_extra_terms_mask_client_name_case_insensitive():
    out, report = redact(
        "ACME Corp lost data last week. acme corp filed a report.",
        mode="regex",
        extra_terms=["ACME Corp"],
    )
    assert "ACME Corp" not in out
    assert "acme corp" not in out.lower() or "[REDACTED_TERM]" in out
    assert report.counts.get("TERM") == 2
    assert "ACME Corp" in report.extra_terms_applied


def test_extra_terms_shorter_than_three_chars_are_dropped():
    # Refuse 1-2 char terms — those would obliterate prose.
    out, _ = redact("IT is the team.", mode="regex", extra_terms=["IT"])
    assert "IT is the team." == out


def test_empty_text_is_passed_through():
    out, report = redact("", mode="full")
    assert out == ""
    assert report.counts == {}


def test_report_to_dict_is_lineage_safe():
    _, report = redact("alice@example.com", mode="regex")
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
    were masked — only their categories and counts.
    """
    raw = "alice@example.com / 555-123-4567 / 123-45-6789"
    _, report = redact(raw, mode="regex")
    serialized = repr(report.to_dict())
    assert "alice@example.com" not in serialized
    assert "555" not in serialized
    assert "123-45-6789" not in serialized
