"""Bundled MITRE ATT&CK Enterprise technique slice.

NOTE: This is a v0.1 starter set of ~30 high-value techniques. The full
STIX bundle is downloaded into shield/p3_attack_surface/data/ via the
`make vendor-attack` target in a follow-up. We bundle this slice so the
platform works fully offline on first start, and so the demo coverage
artifact has real techniques to display.

Source: MITRE ATT&CK Enterprise v14.x. ATT&CK is © MITRE; used under their
Terms of Use. See docs/ATTRIBUTIONS.md.
"""
from __future__ import annotations

TECHNIQUES: list[dict] = [
    # --- Initial Access ---
    {"technique_id": "T1566",     "name": "Phishing",                              "tactic": "Initial Access",      "description": "Send malicious content to gain initial access."},
    {"technique_id": "T1190",     "name": "Exploit Public-Facing Application",    "tactic": "Initial Access",      "description": "Exploit weaknesses in internet-facing apps."},
    {"technique_id": "T1133",     "name": "External Remote Services",             "tactic": "Initial Access",      "description": "Leverage external remote services for initial access."},
    {"technique_id": "T1195",     "name": "Supply Chain Compromise",              "tactic": "Initial Access",      "description": "Manipulate products or delivery mechanisms."},
    {"technique_id": "T1078",     "name": "Valid Accounts",                       "tactic": "Initial Access",      "description": "Use compromised credentials."},

    # --- Execution ---
    {"technique_id": "T1059",     "name": "Command and Scripting Interpreter",    "tactic": "Execution",           "description": "Abuse command/scripting interpreters."},
    {"technique_id": "T1204",     "name": "User Execution",                       "tactic": "Execution",           "description": "Rely on user actions to execute malicious code."},
    {"technique_id": "T1569",     "name": "System Services",                      "tactic": "Execution",           "description": "Abuse system services to execute commands."},

    # --- Persistence ---
    {"technique_id": "T1098",     "name": "Account Manipulation",                 "tactic": "Persistence",         "description": "Modify accounts to maintain access."},
    {"technique_id": "T1547",     "name": "Boot or Logon Autostart Execution",    "tactic": "Persistence",         "description": "Configure autostart mechanisms."},
    {"technique_id": "T1136",     "name": "Create Account",                       "tactic": "Persistence",         "description": "Create accounts to maintain access."},

    # --- Privilege Escalation ---
    {"technique_id": "T1068",     "name": "Exploitation for Privilege Escalation","tactic": "Privilege Escalation","description": "Exploit vulns for elevated access."},
    {"technique_id": "T1055",     "name": "Process Injection",                    "tactic": "Privilege Escalation","description": "Inject into processes to evade defenses."},

    # --- Defense Evasion ---
    {"technique_id": "T1562",     "name": "Impair Defenses",                      "tactic": "Defense Evasion",     "description": "Disable or interfere with security tools."},
    {"technique_id": "T1070",     "name": "Indicator Removal",                    "tactic": "Defense Evasion",     "description": "Remove evidence of activity."},
    {"technique_id": "T1027",     "name": "Obfuscated Files or Information",      "tactic": "Defense Evasion",     "description": "Hide intent of files/info."},

    # --- Credential Access ---
    {"technique_id": "T1110",     "name": "Brute Force",                          "tactic": "Credential Access",   "description": "Attempt credential guessing."},
    {"technique_id": "T1555",     "name": "Credentials from Password Stores",     "tactic": "Credential Access",   "description": "Steal from credential stores."},
    {"technique_id": "T1003",     "name": "OS Credential Dumping",                "tactic": "Credential Access",   "description": "Dump OS credential material."},

    # --- Discovery ---
    {"technique_id": "T1083",     "name": "File and Directory Discovery",         "tactic": "Discovery",           "description": "Enumerate file system."},
    {"technique_id": "T1018",     "name": "Remote System Discovery",              "tactic": "Discovery",           "description": "Find remote systems."},
    {"technique_id": "T1087",     "name": "Account Discovery",                    "tactic": "Discovery",           "description": "Enumerate accounts."},

    # --- Lateral Movement ---
    {"technique_id": "T1021",     "name": "Remote Services",                      "tactic": "Lateral Movement",    "description": "Use remote services for lateral movement."},
    {"technique_id": "T1570",     "name": "Lateral Tool Transfer",                "tactic": "Lateral Movement",    "description": "Move tools across hosts."},

    # --- Collection ---
    {"technique_id": "T1119",     "name": "Automated Collection",                 "tactic": "Collection",          "description": "Use automation to collect data."},
    {"technique_id": "T1213",     "name": "Data from Information Repositories",   "tactic": "Collection",          "description": "Collect from wikis, SharePoint, etc."},

    # --- Command and Control ---
    {"technique_id": "T1071",     "name": "Application Layer Protocol",           "tactic": "Command and Control", "description": "C2 over standard application protocols."},
    {"technique_id": "T1090",     "name": "Proxy",                                "tactic": "Command and Control", "description": "Use proxies for C2."},

    # --- Exfiltration ---
    {"technique_id": "T1041",     "name": "Exfiltration Over C2 Channel",         "tactic": "Exfiltration",        "description": "Exfiltrate over C2."},
    {"technique_id": "T1567",     "name": "Exfiltration Over Web Service",        "tactic": "Exfiltration",        "description": "Exfiltrate via cloud/web services."},

    # --- Impact ---
    {"technique_id": "T1486",     "name": "Data Encrypted for Impact",            "tactic": "Impact",              "description": "Ransomware-style encryption."},
    {"technique_id": "T1490",     "name": "Inhibit System Recovery",              "tactic": "Impact",              "description": "Delete shadow copies / backups."},
    {"technique_id": "T1485",     "name": "Data Destruction",                     "tactic": "Impact",              "description": "Destroy data for impact."},
]
