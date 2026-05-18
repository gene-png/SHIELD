"""Allowlist of known security / IT vendor & product names.

Presidio's spaCy NER doesn't recognize most product brand names, so it
pattern-matches them as PERSON / LOCATION / ORG / NRP — the
"Commvault → [REDACTED_PERSON]" failure mode the user reported on the
admin-final capability list. This list pre-masks those tokens so
Presidio never sees them.

How it's used:
    `shield.ai.redact._apply_presidio` calls `mask_allowlisted` to
    swap each occurrence with a sentinel before NER. After NER, it
    calls `unmask_allowlisted` to put the originals back. The regex
    layer (emails / phones / SSN / addresses / URLs) runs separately
    and is unaffected — vendor names don't pattern-match those.

What belongs here:
  - Commercial security / IT vendor names (Cisco, Microsoft, ...)
  - Distinctive product / suite names (Sentinel, Defender, FortiGate, ...)
  - Vendor-name parents that NER misclassifies (Salesforce, GitHub, ...)

What does NOT belong here:
  - Generic words (User, Network, Identity) — adding these would let
    actual PII slip through under those tokens.
  - Person-name-like terms even if they're brand-name-adjacent
    (e.g. "Bob's Burgers" → keep Bob redactable).

Maintenance: one entry per line. The list is matched case-insensitively
with word boundaries so trailing punctuation / sentence position
don't matter. Adding a vendor is a one-line edit; removing one is too.

This is a defense-in-depth measure for false-positive reduction —
it does NOT defend against intentional PII smuggling (the regex
layer covers that for emails / phones / SSN / CC / IP / URL /
addresses, and the per-project literal terms catch client-specific
strings).
"""
from __future__ import annotations

# Ordered loosely by category for human review. The runtime cost of
# the list size is one regex compile per process; matching is O(n)
# in input length, independent of list size.
VENDOR_ALLOWLIST: list[str] = [
    # --- Hyperscalers + cloud ---
    "AWS", "Amazon Web Services", "Azure", "GCP", "Google Cloud",
    "Cloudflare", "Akamai", "Fastly",

    # --- Major vendors / parent orgs ---
    "Microsoft", "Cisco", "Google", "Apple", "Oracle", "IBM",
    "VMware", "Salesforce", "Atlassian", "GitHub", "GitLab",
    "ServiceNow", "Dell", "HPE", "Hewlett Packard Enterprise",
    "HCL", "Broadcom", "Symantec", "RSA", "Forcepoint",

    # --- Endpoint / EDR / AV ---
    "CrowdStrike", "Falcon",
    "Defender", "Defender for Endpoint", "Defender for Cloud",
    "Defender for Office 365", "Defender for Cloud Apps",
    "SentinelOne", "Singularity",
    "Tanium", "Sophos", "Trellix", "McAfee", "Carbon Black",
    "Malwarebytes", "Cybereason",

    # --- SIEM / SOAR / XDR ---
    "Splunk", "Sentinel", "Microsoft Sentinel",
    "Sumo Logic", "LogRhythm", "QRadar", "Exabeam", "Securonix",
    "Elastic", "Elastic Security", "Chronicle",
    "Cortex", "Cortex XSOAR", "Cortex XDR", "Demisto",

    # --- IAM / SSO / MFA / PAM ---
    "Okta", "Auth0", "Ping", "PingFederate", "PingOne",
    "Duo", "Duo Security", "Yubico", "YubiKey",
    "SailPoint", "IdentityIQ", "Saviynt",
    "Entra", "Entra ID", "Azure AD", "Active Directory",
    "CyberArk", "BeyondTrust", "Delinea", "Thycotic",
    "HashiCorp", "Vault",

    # --- Network / firewall / NDR / SASE / ZTNA ---
    "Palo Alto", "Palo Alto Networks", "Prisma", "Prisma Access",
    "Fortinet", "FortiGate", "FortiManager", "FortiAnalyzer",
    "Check Point", "Juniper", "F5", "BIG-IP",
    "Zscaler", "Netskope", "iboss",
    "Gigamon", "Vectra", "Darktrace", "ExtraHop",
    "Cloudflare One", "Cisco Umbrella", "Umbrella",

    # --- CSPM / CNAPP / cloud security ---
    "Wiz", "Lacework", "Orca", "Prisma Cloud", "CrowdStrike Falcon Cloud",
    "Aqua", "Sysdig", "Snyk",

    # --- Vulnerability / asset / posture ---
    "Tenable", "Nessus", "Tenable.io", "Tenable.sc",
    "Rapid7", "InsightVM", "Nexpose",
    "Qualys", "Axonius",
    "Kenna", "BigFix",

    # --- Email / DLP ---
    "Mimecast", "Proofpoint", "Abnormal", "Avanan",
    "Forcepoint DLP", "Purview", "Microsoft Purview",

    # --- DevOps / source / CI ---
    "Jira", "Jira Service Management", "Confluence",
    "Slack", "Teams", "Microsoft Teams",
    "Zoom", "Webex",
    "Jenkins", "CircleCI", "GitHub Actions",
    "PagerDuty", "Opsgenie", "VictorOps",

    # --- Backup / IRM / DR ---
    "Veeam", "Commvault", "Cohesity", "Rubrik", "Veritas",
    "Druva", "Acronis",

    # --- ITSM / management ---
    "Intune", "Microsoft Intune",
    "Jamf", "Kandji", "Mosyle", "Addigy",
    "SolarWinds", "Nagios", "Datadog", "New Relic",

    # --- Training / phishing ---
    "KnowBe4", "Proofpoint Security Awareness", "Hoxhunt",

    # --- Threat intel ---
    "Recorded Future", "Mandiant", "FireEye", "Anomali", "ThreatConnect",

    # --- Other commonly-misclassified strings ---
    "Linux", "Windows", "macOS", "iOS", "Android",
    "VPN", "SSO", "MFA", "SIEM", "SOAR", "EDR", "XDR",
    "DLP", "CASB", "IDS", "IPS", "WAF", "NDR",
    "CSPM", "CNAPP", "CWPP",
    "Entra ID P1", "Entra ID P2", "Entra ID Premium",
    "Office 365", "Microsoft 365", "M365", "E5",
    "Cisco ISE", "Cisco Secure", "Cisco Umbrella",
    "Microsoft Authenticator", "Google Authenticator",
]


def compiled_pattern():
    """Compile and return a single regex matching any allowlist entry.

    Match is case-insensitive with word boundaries on both sides so
    'Cisco' matches 'Cisco' and 'Cisco.' but not 'PiscoCisco'.
    Longest-first sort so 'Microsoft Defender' wins over 'Microsoft'
    when both could match.
    """
    import re
    items = sorted(set(VENDOR_ALLOWLIST), key=len, reverse=True)
    alt = "|".join(re.escape(s) for s in items)
    # Lookarounds approximate \b but allow trailing/leading punctuation
    # like quotes / parens which \b treats as word breaks anyway.
    return re.compile(r"(?<!\w)(?:" + alt + r")(?!\w)", re.IGNORECASE)


# Sentinel design: Presidio's spaCy NER is alarmingly eager — even
# a pure-lowercase identifier in subject position gets scored as
# PERSON. The angle-bracket form below is one of the few we tested
# that spaCy reliably classifies as O (out-of-entity). The double
# brackets also keep the sentinel visually distinct from the regex
# layer's `[REDACTED_*]` tokens so a leaked sentinel is obvious.
_SENTINEL_PREFIX = "<<k"
_SENTINEL_SUFFIX = ">>"


def mask_allowlisted(text: str) -> tuple[str, dict[str, str]]:
    """Replace allowlist matches with NER-invisible sentinels.

    Returns (masked_text, sentinel_map) where sentinel_map[token] is
    the original matched string. The lookup map is per-call so
    concurrent requests stay isolated.
    """
    pat = compiled_pattern()
    mapping: dict[str, str] = {}

    def _swap(m):
        original = m.group(0)
        token = f"{_SENTINEL_PREFIX}{len(mapping):04d}{_SENTINEL_SUFFIX}"
        mapping[token] = original
        return token

    return pat.sub(_swap, text), mapping


def unmask_allowlisted(text: str, mapping: dict[str, str]) -> str:
    """Restore the originals in `mapping` to the text. No-op if empty."""
    if not mapping:
        return text
    for token, original in mapping.items():
        text = text.replace(token, original)
    return text
