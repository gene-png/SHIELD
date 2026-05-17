"""Strip likely PII from text before it leaves SHIELD for Anthropic.

Defense-in-depth. Two stacked layers, applied in order:

1. **Regex layer** — always on. Catches structured PII: emails, US phones,
   IPv4, SSN, credit-card-shaped runs, ZIP+street fragments, URLs.
2. **Presidio NER layer** — gated on `AI_REDACTION_MODE=full` AND the
   library being importable. Catches free-form names, organizations,
   and locations that the regex layer can't reasonably express.

Every match is replaced with a typed placeholder (e.g. `[REDACTED_EMAIL]`)
so the model still sees that a value *was* there. The redaction report
returned alongside the cleaned text is structured for lineage — counts
by category, never raw values — so the audit log records what was
withheld without re-leaking the secret.

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


def _apply_regex(text: str, report: RedactionReport) -> str:
    for label, pat in _PATTERNS:
        def _sub(_m: re.Match[str], _label: str = label) -> str:
            report._bump(_label)
            return f"[REDACTED_{_label}]"
        text = pat.sub(_sub, text)
    return text


def _apply_extra_terms(text: str, terms: Iterable[str], report: RedactionReport) -> str:
    for raw in terms:
        term = raw.strip()
        if not term or len(term) < 3:
            # Refuse to redact 1-2 char terms — would turn most prose into mush.
            continue
        # Case-insensitive whole-word-ish match. We don't use \b on both
        # sides because client names often include "&" / "," etc.
        pat = re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE)
        new_text, n = pat.subn("[REDACTED_TERM]", text)
        if n > 0:
            report.extra_terms_applied.append(term)
            report._bump("TERM", n)
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
    pay the spaCy load cost once.
    """
    global _PRESIDIO_ANALYZER, _PRESIDIO_TRIED
    if _PRESIDIO_TRIED:
        return _PRESIDIO_ANALYZER
    _PRESIDIO_TRIED = True
    try:
        from presidio_analyzer import AnalyzerEngine
    except ImportError:
        return None
    try:
        _PRESIDIO_ANALYZER = AnalyzerEngine()
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


def _apply_presidio(text: str, report: RedactionReport) -> str:
    analyzer = _get_presidio_analyzer()
    if analyzer is None:
        return text
    report.presidio_available = True
    results = analyzer.analyze(
        text=text,
        entities=_PRESIDIO_ENTITIES,
        language="en",
    )
    # Replace right-to-left so earlier spans' character offsets stay
    # valid as we mutate the string.
    for r in sorted(results, key=lambda x: x.start, reverse=True):
        report._bump(r.entity_type)
        text = text[:r.start] + f"[REDACTED_{r.entity_type}]" + text[r.end:]
    return text


# --------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------

def redact(
    text: str,
    *,
    mode: str = "full",
    extra_terms: Iterable[str] = (),
) -> tuple[str, RedactionReport]:
    """Run the configured redaction layers on `text`.

    Returns (cleaned_text, report). `report.to_dict()` is what callers
    should merge into the AI artifact lineage.

    `mode`:
      - "off":   bypass redaction entirely. The report still records the
                 mode so the audit log shows it was skipped on purpose.
      - "regex": regex layer only.
      - "full":  regex layer + Presidio NER if available.

    `extra_terms` is the per-call list of literals to mask (typically
    the client's organization name pulled from the project). Empty by
    default — the AI client wires it in from app config.
    """
    report = RedactionReport(mode=mode)
    if not text:
        return text, report
    if mode == "off":
        return text, report

    out = _apply_extra_terms(text, extra_terms, report)
    out = _apply_regex(out, report)
    if mode == "full":
        out = _apply_presidio(out, report)
    return out, report
