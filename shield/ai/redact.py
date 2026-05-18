"""Strip likely PII from text before it leaves SHIELD for Anthropic.

Defense-in-depth. Two stacked layers, applied in order:

1. **Regex layer** — always on. Catches structured PII: emails, US phones,
   IPv4, SSN, credit-card-shaped runs, ZIP+street fragments, URLs.
2. **Presidio NER layer** — gated on `AI_REDACTION_MODE=full` AND the
   library being importable. Catches free-form names, organizations,
   and locations that the regex layer can't reasonably express.

**Round-tripping** (the round-4 follow-up). Every match is replaced
with a uniquely-numbered placeholder (e.g. `[REDACTED_EMAIL_0001]`)
and the original value is captured in a per-call mapping dict. The
caller (typically `AIClient.complete`) sends the redacted text to
Anthropic, gets a response back, and runs `unredact()` to restore
the originals. The mapping lives in memory for the duration of the
AI call only; it is never persisted to the lineage or audit log.

Net effect: real PII (and organization names) never reaches the
Anthropic API on the wire, but the response stored in our DB shows
the original values — which is what an admin actually wants to read.

The redaction report (counts by category) IS still persisted on the
artifact's lineage so the audit log shows what was withheld during
transit, without re-leaking the secret.

Configurable via app config:
    AI_REDACTION_MODE             = off | regex | full    (default: full)
    AI_REDACTION_EXTRA_TERMS      = comma-separated case-insensitive
                                    literals (client names, project codes).

The chokepoint is `shield.ai.client.AIClient.complete()`. No other module
should call Anthropic directly, so this is the only place redaction has
to live.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

# --------------------------------------------------------------------
# Regex layer
# --------------------------------------------------------------------
#
# Order matters: more specific patterns first so a credit-card-shaped
# string doesn't get partially eaten by a generic phone match.
#
# We deliberately do NOT try to match every conceivable format. The job
# is to catch the common shapes — anything exotic falls to the NER layer.

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # SSN: 3-2-4 with optional separators. Filters out 000/666/9xx leading
    # groups since those are never assigned, but otherwise lenient.
    ("SSN", re.compile(
        r"\b(?!000|666|9\d{2})\d{3}[ -]?(?!00)\d{2}[ -]?(?!0000)\d{4}\b"
    )),
    # Credit-card-shaped: 13-19 digits with optional space/hyphen
    # separators. Luhn-validating would be more precise but adds a
    # dependency on `python-stdnum`; for redaction the false-positive
    # cost is acceptable.
    ("CREDIT_CARD", re.compile(
        r"\b(?:\d[ -]?){13,19}\b"
    )),
    # Email — the practical RFC subset.
    ("EMAIL", re.compile(
        r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
    )),
    # US phone, generously: optional +1, optional parens, hyphens or
    # dots. 10 digits worth.
    ("PHONE", re.compile(
        r"(?<!\d)(?:\+?1[ \-.]?)?\(?\d{3}\)?[ \-.]?\d{3}[ \-.]?\d{4}(?!\d)"
    )),
    # IPv4. We don't redact RFC1918 internals because those are the kind
    # of artifact a coverage analysis is genuinely about — but the regex
    # doesn't try to distinguish, and the lineage report says how many
    # were caught so a reviewer can spot over-redaction.
    ("IP_ADDRESS", re.compile(
        r"\b(?:\d{1,3}\.){3}\d{1,3}\b"
    )),
    # URLs — strip query strings since those frequently embed tokens.
    ("URL", re.compile(
        r"\bhttps?://[^\s<>\"']+",
        re.IGNORECASE,
    )),
    # US street address: number + street name + suffix.
    # Conservative — only matches Street/St/Ave/Blvd/Road/Rd/Drive/Dr/Lane/Ln/Way.
    ("STREET", re.compile(
        r"\b\d{1,6}\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?\s+"
        r"(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr|Lane|Ln|Way|Court|Ct)\b\.?",
        re.IGNORECASE,
    )),
]


@dataclass
class RedactionReport:
    """Structured summary of what was masked. Goes into AI artifact lineage.

    Counts only — raw values are never recorded, since the whole point of
    redaction is to keep them out of downstream storage.
    """
    mode: str
    counts: dict[str, int] = field(default_factory=dict)
    extra_terms_applied: list[str] = field(default_factory=list)
    presidio_available: bool = False

    def to_dict(self) -> dict:
        return {
            "redaction_mode": self.mode,
            "redaction_counts": dict(self.counts),
            "redaction_extra_terms": list(self.extra_terms_applied),
            "redaction_presidio_available": self.presidio_available,
            "redaction_total": sum(self.counts.values()),
        }

    def _bump(self, label: str, n: int = 1) -> None:
        if n <= 0:
            return
        self.counts[label] = self.counts.get(label, 0) + n


def _next_placeholder(label: str, mapping: dict[str, str]) -> str:
    """Return a uniquely-numbered placeholder for the given category.

    The number is `len(mapping)` so every placeholder across every
    category is globally unique within one call — `[REDACTED_EMAIL_0017]`
    is different from `[REDACTED_PHONE_0017]` even though they share
    the index. That guarantee matters for round-tripping: each
    placeholder maps back to exactly one original.
    """
    return f"[REDACTED_{label}_{len(mapping):04d}]"


def _apply_regex(text: str, report: RedactionReport,
                 mapping: dict[str, str]) -> str:
    for label, pat in _PATTERNS:
        def _sub(m: re.Match[str], _label: str = label) -> str:
            original = m.group(0)
            report._bump(_label)
            placeholder = _next_placeholder(_label, mapping)
            mapping[placeholder] = original
            return placeholder
        text = pat.sub(_sub, text)
    return text


def _apply_extra_terms(text: str, terms: Iterable[str],
                       report: RedactionReport,
                       mapping: dict[str, str]) -> str:
    for raw in terms:
        term = raw.strip()
        if not term or len(term) < 3:
            # Refuse to redact 1-2 char terms — would turn most prose into mush.
            continue
        # Case-insensitive whole-word-ish match. We don't use \b on both
        # sides because client names often include "&" / "," etc.
        pat = re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE)

        n_found = 0
        def _sub(m: re.Match[str]) -> str:
            nonlocal n_found
            original = m.group(0)
            n_found += 1
            placeholder = _next_placeholder("TERM", mapping)
            mapping[placeholder] = original
            return placeholder
        new_text = pat.sub(_sub, text)
        if n_found > 0:
            report.extra_terms_applied.append(term)
            report._bump("TERM", n_found)
            text = new_text
    return text


# --------------------------------------------------------------------
# Presidio layer (optional)
# --------------------------------------------------------------------
#
# Presidio + spaCy is heavyweight. We import lazily and degrade to
# regex-only if either is missing. The analyzer is cached at module
# scope because constructing it loads the spaCy model and that's slow
# enough (~1s) to be worth amortizing across calls in the same worker.

_PRESIDIO_ANALYZER = None
_PRESIDIO_TRIED = False


def _get_presidio_analyzer():
    """Return a presidio AnalyzerEngine, or None if unavailable.

    Cached after the first call (success or failure). Worker processes
    pay the spaCy load cost once. We explicitly point Presidio at the
    `en_core_web_sm` model that the Dockerfile installs — the library
    defaults to `_lg` (~750 MB), which is unnecessary image bloat for
    PERSON/LOCATION/ORG NER on the kind of text SHIELD sends.
    """
    global _PRESIDIO_ANALYZER, _PRESIDIO_TRIED
    if _PRESIDIO_TRIED:
        return _PRESIDIO_ANALYZER
    _PRESIDIO_TRIED = True
    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider
    except ImportError:
        return None
    try:
        provider = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        })
        _PRESIDIO_ANALYZER = AnalyzerEngine(nlp_engine=provider.create_engine())
    except Exception:
        # spaCy model missing, OS-level issue, etc. We refuse to crash
        # the AI pipeline over a missing optional redactor — the regex
        # layer still ran.
        _PRESIDIO_ANALYZER = None
    return _PRESIDIO_ANALYZER


# Entity types we let Presidio handle. The regex layer already covers
# EMAIL_ADDRESS / PHONE_NUMBER / US_SSN / CREDIT_CARD / IP_ADDRESS / URL,
# so for those we'd just be double-counting. We let Presidio do the
# free-form things only.
_PRESIDIO_ENTITIES = ["PERSON", "LOCATION", "ORGANIZATION", "NRP"]


def _apply_presidio(text: str, report: RedactionReport,
                    mapping: dict[str, str]) -> str:
    analyzer = _get_presidio_analyzer()
    if analyzer is None:
        return text
    report.presidio_available = True

    # Vendor-name pre-mask: replace any known commercial vendor /
    # product name with a sentinel BEFORE Presidio sees the text.
    # Without this, spaCy's PERSON / LOCATION / NRP / ORG detectors
    # routinely flag brand names (Commvault, Cisco, Atlassian, Jamf,
    # Zscaler, Tenable, etc.) as named entities and the redactor
    # destroys legitimate commercial-software metadata.
    from .vendor_allowlist import mask_allowlisted, unmask_allowlisted
    masked_text, sentinel_map = mask_allowlisted(text)
    if sentinel_map:
        report._bump("ALLOWLISTED_VENDOR_TERMS", len(sentinel_map))

    results = analyzer.analyze(
        text=masked_text,
        entities=_PRESIDIO_ENTITIES,
        language="en",
    )
    # Replace right-to-left so earlier spans' character offsets stay
    # valid as we mutate the string. Each NER hit gets its own
    # uniquely-numbered placeholder + mapping entry, mirroring the
    # regex layer's roundtrip-friendly behavior.
    out = masked_text
    for r in sorted(results, key=lambda x: x.start, reverse=True):
        report._bump(r.entity_type)
        original = out[r.start:r.end]
        placeholder = _next_placeholder(r.entity_type, mapping)
        mapping[placeholder] = original
        out = out[:r.start] + placeholder + out[r.end:]

    # Restore vendor names. Sentinels won't survive being inside a
    # [REDACTED_*] span because we never put a sentinel inside one
    # (mask runs before NER), but the unmasker is defensive.
    return unmask_allowlisted(out, sentinel_map)


# --------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------

def redact(
    text: str,
    *,
    mode: str = "full",
    extra_terms: Iterable[str] = (),
) -> tuple[str, RedactionReport, dict[str, str]]:
    """Run the configured redaction layers on `text`.

    Returns `(redacted_text, report, mapping)`:
      - `redacted_text` has every match swapped for a uniquely-numbered
        placeholder (`[REDACTED_EMAIL_0001]`, `[REDACTED_PERSON_0002]`,
        ...). This is the safe-to-send-online form.
      - `report.to_dict()` is what callers merge into the AI artifact
        lineage. Counts only — no raw values, no mapping leak.
      - `mapping` is `{placeholder: original}`. The caller (the AI
        client) holds it in memory for the duration of the round-trip
        to Anthropic so it can `unredact()` the response. Never
        persisted to lineage or audit.

    `mode`:
      - "off":   bypass redaction entirely. Returns text unchanged and
                 an empty mapping. The report still records the mode
                 so the audit log shows it was skipped on purpose.
      - "regex": regex layer only.
      - "full":  regex layer + Presidio NER if available.

    `extra_terms` is the per-call list of literals to mask (typically
    the client's organization name pulled from the project). Empty by
    default — the AI client wires it in from app config.
    """
    report = RedactionReport(mode=mode)
    mapping: dict[str, str] = {}
    if not text:
        return text, report, mapping
    if mode == "off":
        return text, report, mapping

    out = _apply_extra_terms(text, extra_terms, report, mapping)
    out = _apply_regex(out, report, mapping)
    if mode == "full":
        out = _apply_presidio(out, report, mapping)
    return out, report, mapping


def unredact(text: str, mapping: dict[str, str]) -> str:
    """Restore originals from a placeholder map.

    The mapping is what `redact()` returned. Walking placeholders
    longest-first protects against any chance of partial overlap
    (e.g. a placeholder that's a prefix of another, which shouldn't
    happen given our naming scheme but defensive doesn't cost much).
    """
    if not text or not mapping:
        return text
    for placeholder in sorted(mapping.keys(), key=len, reverse=True):
        if placeholder in text:
            text = text.replace(placeholder, mapping[placeholder])
    return text
