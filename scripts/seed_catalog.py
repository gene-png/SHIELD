"""Seed catalog: 75 capability-list items for Acme Co.

Curated for breadth AND deliberate overlap so Platform 1's overlap analysis
has real findings to surface on first run. Cost figures are illustrative.

DELIBERATE OVERLAPS (these will trip overlap analysis):
  - Two SIEMs:  Splunk + Microsoft Sentinel
  - Two EDRs:   CrowdStrike Falcon + Microsoft Defender for Endpoint
  - Two AV:     Symantec Endpoint Protection (legacy) + MS Defender
  - Two collab: Slack + Microsoft Teams
  - Two MFA:    Duo + Azure MFA (built into Entra)
  - Two ticketing/ITSM: ServiceNow + Jira Service Management
  - Two backup: Veeam + Commvault
"""

CATALOG: list[dict] = [
    # ---------------- Endpoint (AV / EDR / XDR) — 7 ----------------
    {"name": "CrowdStrike Falcon",                 "vendor": "CrowdStrike", "category": "EDR",   "function": "endpoint detection and response",                 "annual_cost_usd": 220_000, "license_count": 1500},
    {"name": "Microsoft Defender for Endpoint",    "vendor": "Microsoft",   "category": "EDR",   "function": "endpoint detection and response (M365 E5)",       "annual_cost_usd": 0,       "license_count": 1500, "notes": "Bundled in M365 E5"},
    {"name": "Symantec Endpoint Protection",       "vendor": "Broadcom",    "category": "AV",    "function": "legacy antivirus",                                "annual_cost_usd": 36_000,  "license_count": 1200, "notes": "Legacy; should be retired"},
    {"name": "SentinelOne Singularity",            "vendor": "SentinelOne", "category": "EDR",   "function": "endpoint detection and response",                 "annual_cost_usd": 0,       "license_count": 200,  "notes": "Pilot on dev fleet"},
    {"name": "Malwarebytes Endpoint Protection",   "vendor": "Malwarebytes","category": "AV",    "function": "anti-malware",                                    "annual_cost_usd": 12_000,  "license_count": 400,  "notes": "Used by marketing only"},
    {"name": "Carbon Black App Control",           "vendor": "VMware",      "category": "EDR",   "function": "application allow-listing",                       "annual_cost_usd": 28_000,  "license_count": 80},
    {"name": "Tanium Endpoint",                    "vendor": "Tanium",      "category": "EDR",   "function": "real-time endpoint visibility",                   "annual_cost_usd": 90_000,  "license_count": 1500},

    # ---------------- SIEM / SOAR — 6 ----------------
    {"name": "Splunk Enterprise",                  "vendor": "Splunk/Cisco","category": "SIEM",  "function": "log aggregation + correlation",                   "annual_cost_usd": 480_000, "license_count": None},
    {"name": "Microsoft Sentinel",                 "vendor": "Microsoft",   "category": "SIEM",  "function": "cloud-native SIEM/SOAR",                          "annual_cost_usd": 320_000, "license_count": None, "notes": "Recently added"},
    {"name": "Splunk SOAR",                        "vendor": "Splunk/Cisco","category": "SIEM",  "function": "security orchestration + automation",             "annual_cost_usd": 95_000,  "license_count": None},
    {"name": "Elastic Security",                   "vendor": "Elastic",     "category": "SIEM",  "function": "log search + detections (dev/staging)",           "annual_cost_usd": 40_000,  "license_count": None, "notes": "Used by SRE for ops logs"},
    {"name": "Cortex XSOAR",                       "vendor": "Palo Alto",   "category": "SIEM",  "function": "SOAR playbook automation",                        "annual_cost_usd": 0,       "license_count": None, "notes": "Trial seat under sales eval"},
    {"name": "LogRhythm SIEM",                     "vendor": "Exabeam",     "category": "SIEM",  "function": "SIEM (legacy regulated workload)",                "annual_cost_usd": 65_000,  "license_count": None},

    # ---------------- Network security (NGFW / NDR / IPS) — 7 ----------------
    {"name": "Palo Alto Networks PA-5260",         "vendor": "Palo Alto",   "category": "NGFW",  "function": "perimeter NGFW",                                  "annual_cost_usd": 140_000, "license_count": 4},
    {"name": "Fortinet FortiGate 600F",            "vendor": "Fortinet",    "category": "NGFW",  "function": "branch firewalls",                                "annual_cost_usd": 85_000,  "license_count": 12},
    {"name": "Cisco Firepower 2130",               "vendor": "Cisco",       "category": "NGFW",  "function": "datacenter firewall",                             "annual_cost_usd": 70_000,  "license_count": 6},
    {"name": "ExtraHop Reveal(x)",                 "vendor": "ExtraHop",    "category": "NDR",   "function": "network detection and response",                  "annual_cost_usd": 180_000, "license_count": None},
    {"name": "Darktrace Detect",                   "vendor": "Darktrace",   "category": "NDR",   "function": "AI-driven NDR",                                   "annual_cost_usd": 210_000, "license_count": None, "notes": "Renewal under review"},
    {"name": "Snort Open Source",                  "vendor": "Cisco",       "category": "IPS",   "function": "open-source IPS",                                 "annual_cost_usd": 0,       "license_count": None},
    {"name": "Cloudflare WAF",                     "vendor": "Cloudflare",  "category": "NGFW",  "function": "web application firewall + DDoS",                 "annual_cost_usd": 48_000,  "license_count": None},

    # ---------------- Email security — 5 ----------------
    {"name": "Proofpoint Email Protection",        "vendor": "Proofpoint",  "category": "Email Security", "function": "email gateway",                          "annual_cost_usd": 70_000, "license_count": 1800},
    {"name": "Microsoft Defender for Office 365",  "vendor": "Microsoft",   "category": "Email Security", "function": "M365 email security (E5)",               "annual_cost_usd": 0,      "license_count": 1500, "notes": "Bundled"},
    {"name": "Abnormal Security",                  "vendor": "Abnormal",    "category": "Email Security", "function": "BEC/phishing AI",                        "annual_cost_usd": 55_000, "license_count": 1500},
    {"name": "Mimecast Awareness Training",        "vendor": "Mimecast",    "category": "Email Security", "function": "phishing training",                      "annual_cost_usd": 18_000, "license_count": 1500},
    {"name": "KnowBe4 Security Awareness",         "vendor": "KnowBe4",     "category": "Email Security", "function": "security awareness training",            "annual_cost_usd": 22_000, "license_count": 1500, "notes": "Overlap with Mimecast training"},

    # ---------------- Identity / SSO / MFA / PAM — 7 ----------------
    {"name": "Okta Workforce Identity",            "vendor": "Okta",        "category": "SSO",   "function": "workforce SSO + lifecycle",                       "annual_cost_usd": 165_000, "license_count": 1500},
    {"name": "Microsoft Entra ID P2",              "vendor": "Microsoft",   "category": "SSO",   "function": "Azure AD / conditional access",                   "annual_cost_usd": 0,       "license_count": 1500, "notes": "Bundled in M365 E5"},
    {"name": "Duo Security",                       "vendor": "Cisco",       "category": "MFA",   "function": "MFA + device trust",                              "annual_cost_usd": 36_000,  "license_count": 1500},
    {"name": "Microsoft Entra MFA",                "vendor": "Microsoft",   "category": "MFA",   "function": "MFA (Entra)",                                     "annual_cost_usd": 0,       "license_count": 1500, "notes": "Same MFA factor coverage as Duo"},
    {"name": "CyberArk Privilege Cloud",           "vendor": "CyberArk",    "category": "PAM",   "function": "privileged access management",                    "annual_cost_usd": 95_000,  "license_count": 200},
    {"name": "BeyondTrust Remote Support",         "vendor": "BeyondTrust", "category": "PAM",   "function": "privileged remote sessions",                      "annual_cost_usd": 28_000,  "license_count": 60},
    {"name": "JumpCloud Directory",                "vendor": "JumpCloud",   "category": "IAM",   "function": "directory + device management (engineering)",     "annual_cost_usd": 14_000,  "license_count": 150,  "notes": "Engineering-only directory"},

    # ---------------- Vulnerability mgmt — 5 ----------------
    {"name": "Tenable Nessus Pro",                 "vendor": "Tenable",     "category": "VM",    "function": "vulnerability scanning",                          "annual_cost_usd": 24_000,  "license_count": 4},
    {"name": "Tenable.io",                         "vendor": "Tenable",     "category": "VM",    "function": "cloud vulnerability management",                  "annual_cost_usd": 38_000,  "license_count": None},
    {"name": "Rapid7 InsightVM",                   "vendor": "Rapid7",      "category": "VM",    "function": "vulnerability management",                        "annual_cost_usd": 52_000,  "license_count": None, "notes": "Overlap with Tenable.io"},
    {"name": "Qualys VMDR",                        "vendor": "Qualys",      "category": "VM",    "function": "vulnerability detection and response",            "annual_cost_usd": 40_000,  "license_count": None, "notes": "Legacy compliance workload"},
    {"name": "Snyk Open Source",                   "vendor": "Snyk",        "category": "VM",    "function": "OSS dependency scanning",                         "annual_cost_usd": 28_000,  "license_count": 120},

    # ---------------- Cloud security (CSPM / CWPP / CIEM) — 5 ----------------
    {"name": "Wiz Cloud Security Platform",        "vendor": "Wiz",         "category": "CSPM",  "function": "CSPM + CIEM + workload posture",                  "annual_cost_usd": 240_000, "license_count": None},
    {"name": "Prisma Cloud",                       "vendor": "Palo Alto",   "category": "CSPM",  "function": "CSPM + CWPP (legacy contract)",                   "annual_cost_usd": 110_000, "license_count": None, "notes": "Sunsetting; overlaps Wiz"},
    {"name": "Microsoft Defender for Cloud",       "vendor": "Microsoft",   "category": "CSPM",  "function": "Azure-native CSPM",                               "annual_cost_usd": 0,       "license_count": None, "notes": "Bundled in Azure"},
    {"name": "AWS Security Hub",                   "vendor": "AWS",         "category": "CSPM",  "function": "AWS-native posture",                              "annual_cost_usd": 0,       "license_count": None},
    {"name": "Lacework Polygraph",                 "vendor": "Fortinet",    "category": "CWPP",  "function": "workload security",                               "annual_cost_usd": 75_000,  "license_count": None, "notes": "Considered redundant after Wiz"},

    # ---------------- DLP — 4 ----------------
    {"name": "Symantec DLP",                       "vendor": "Broadcom",    "category": "DLP",   "function": "data loss prevention",                            "annual_cost_usd": 95_000, "license_count": 1500},
    {"name": "Microsoft Purview DLP",              "vendor": "Microsoft",   "category": "DLP",   "function": "DLP in M365",                                     "annual_cost_usd": 0,      "license_count": 1500, "notes": "Bundled in M365 E5; overlaps Symantec"},
    {"name": "Forcepoint DLP",                     "vendor": "Forcepoint",  "category": "DLP",   "function": "network DLP",                                     "annual_cost_usd": 60_000, "license_count": 1500},
    {"name": "Code42 Incydr",                      "vendor": "Code42",      "category": "DLP",   "function": "insider risk",                                    "annual_cost_usd": 42_000, "license_count": 1500},

    # ---------------- Backup / DR — 5 ----------------
    {"name": "Veeam Backup & Replication",         "vendor": "Veeam",       "category": "Backup","function": "VM and file backup",                              "annual_cost_usd": 56_000, "license_count": None},
    {"name": "Commvault Complete Data Protection", "vendor": "Commvault",   "category": "Backup","function": "enterprise backup",                               "annual_cost_usd": 80_000, "license_count": None, "notes": "Overlap with Veeam"},
    {"name": "Druva inSync",                       "vendor": "Druva",       "category": "Backup","function": "endpoint backup (SaaS)",                          "annual_cost_usd": 28_000, "license_count": 1500},
    {"name": "Rubrik Security Cloud",              "vendor": "Rubrik",      "category": "Backup","function": "immutable backup + ransomware detection",         "annual_cost_usd": 95_000, "license_count": None},
    {"name": "AWS Backup",                         "vendor": "AWS",         "category": "Backup","function": "AWS-native backup",                               "annual_cost_usd": 22_000, "license_count": None},

    # ---------------- Patch / RMM — 4 ----------------
    {"name": "Microsoft Intune",                   "vendor": "Microsoft",   "category": "Patch", "function": "endpoint config and patching",                    "annual_cost_usd": 0,       "license_count": 1500, "notes": "Bundled in M365 E5"},
    {"name": "Automox",                            "vendor": "Automox",     "category": "Patch", "function": "cloud-native patching",                           "annual_cost_usd": 22_000,  "license_count": 1500, "notes": "Overlaps Intune for Windows"},
    {"name": "NinjaOne RMM",                       "vendor": "NinjaOne",    "category": "RMM",   "function": "RMM (engineering labs)",                          "annual_cost_usd": 14_000,  "license_count": 200},
    {"name": "Jamf Pro",                           "vendor": "Jamf",        "category": "Patch", "function": "Mac/iOS management",                              "annual_cost_usd": 18_000,  "license_count": 300},

    # ---------------- CASB / shadow-IT — 3 ----------------
    {"name": "Netskope SSE",                       "vendor": "Netskope",    "category": "CASB",  "function": "CASB + SWG",                                      "annual_cost_usd": 120_000, "license_count": 1500},
    {"name": "Microsoft Defender for Cloud Apps",  "vendor": "Microsoft",   "category": "CASB",  "function": "M365 CASB",                                       "annual_cost_usd": 0,       "license_count": 1500, "notes": "Bundled; overlaps Netskope"},
    {"name": "Zscaler Internet Access",            "vendor": "Zscaler",     "category": "CASB",  "function": "SWG / SSE",                                       "annual_cost_usd": 145_000, "license_count": 1500},

    # ---------------- ITSM — 3 ----------------
    {"name": "ServiceNow ITSM",                    "vendor": "ServiceNow",  "category": "ITSM",  "function": "ITSM + incident management",                      "annual_cost_usd": 285_000, "license_count": 200},
    {"name": "Jira Service Management",            "vendor": "Atlassian",   "category": "ITSM",  "function": "ITSM for engineering",                             "annual_cost_usd": 36_000,  "license_count": 250,  "notes": "Overlap with ServiceNow for tickets"},
    {"name": "PagerDuty",                          "vendor": "PagerDuty",   "category": "ITSM",  "function": "on-call + paging",                                "annual_cost_usd": 28_000,  "license_count": 80},

    # ---------------- Asset / inventory — 4 ----------------
    {"name": "ServiceNow CMDB",                    "vendor": "ServiceNow",  "category": "Inventory", "function": "configuration management database",           "annual_cost_usd": 0,       "license_count": None, "notes": "Bundled with ServiceNow ITSM"},
    {"name": "Axonius Cybersecurity Asset Mgmt",   "vendor": "Axonius",     "category": "Inventory", "function": "cyber asset attack surface management",       "annual_cost_usd": 95_000,  "license_count": None},
    {"name": "Lansweeper",                         "vendor": "Lansweeper",  "category": "Inventory", "function": "IT asset discovery",                          "annual_cost_usd": 14_000,  "license_count": None, "notes": "Overlap with Axonius"},
    {"name": "Microsoft Endpoint Manager",         "vendor": "Microsoft",   "category": "Inventory", "function": "device inventory (Intune)",                   "annual_cost_usd": 0,       "license_count": 1500, "notes": "Bundled"},

    # ---------------- DevOps / CI / SCM — 4 ----------------
    {"name": "GitHub Enterprise",                  "vendor": "GitHub",      "category": "DevOps","function": "source control + CI",                             "annual_cost_usd": 78_000,  "license_count": 250},
    {"name": "GitLab Self-Managed",                "vendor": "GitLab",      "category": "DevOps","function": "self-hosted SCM/CI (legacy)",                     "annual_cost_usd": 45_000,  "license_count": 120,  "notes": "Overlap with GitHub Enterprise"},
    {"name": "HashiCorp Vault",                    "vendor": "HashiCorp",   "category": "DevOps","function": "secrets management",                              "annual_cost_usd": 60_000,  "license_count": None},
    {"name": "1Password Business",                 "vendor": "1Password",   "category": "DevOps","function": "shared secrets / human passwords",                "annual_cost_usd": 24_000,  "license_count": 1500},

    # ---------------- Productivity / collaboration (overlap) — 6 ----------------
    {"name": "Microsoft Teams",                    "vendor": "Microsoft",   "category": "Productivity", "function": "chat + meetings (M365)",                  "annual_cost_usd": 0,       "license_count": 1500, "notes": "Bundled"},
    {"name": "Slack Enterprise Grid",              "vendor": "Salesforce",  "category": "Productivity", "function": "chat",                                    "annual_cost_usd": 145_000, "license_count": 1500, "notes": "Overlap with Teams"},
    {"name": "Zoom Workplace",                     "vendor": "Zoom",        "category": "Productivity", "function": "video conferencing",                      "annual_cost_usd": 95_000,  "license_count": 1500, "notes": "Overlap with Teams meetings"},
    {"name": "Google Workspace",                   "vendor": "Google",      "category": "Productivity", "function": "email + docs (marketing only)",           "annual_cost_usd": 22_000,  "license_count": 80,   "notes": "Overlap with M365 for marketing org"},
    {"name": "Confluence",                         "vendor": "Atlassian",   "category": "Productivity", "function": "wiki",                                    "annual_cost_usd": 18_000,  "license_count": 1500},
    {"name": "Notion Enterprise",                  "vendor": "Notion",      "category": "Productivity", "function": "wiki/docs (engineering)",                 "annual_cost_usd": 11_000,  "license_count": 200,  "notes": "Overlap with Confluence"},

    # ---------------- Other / specialty — 4 ----------------
    {"name": "Recorded Future",                    "vendor": "Recorded Future", "category": "Other", "function": "threat intelligence",                        "annual_cost_usd": 90_000, "license_count": None},
    {"name": "Cofense PhishMe",                    "vendor": "Cofense",         "category": "Other", "function": "phishing simulation",                        "annual_cost_usd": 24_000, "license_count": 1500, "notes": "Overlap with KnowBe4 / Mimecast"},
    {"name": "Trellix XDR",                        "vendor": "Trellix",         "category": "Other", "function": "XDR (legacy McAfee/FireEye)",                "annual_cost_usd": 38_000, "license_count": None, "notes": "Legacy; review for retirement"},
    {"name": "Sumo Logic",                         "vendor": "Sumo Logic",      "category": "Other", "function": "log analytics (marketing analytics)",        "annual_cost_usd": 26_000, "license_count": None, "notes": "Overlap with Splunk for app logs"},
]


def total_cost_usd() -> int:
    return sum((c.get("annual_cost_usd") or 0) for c in CATALOG)


if __name__ == "__main__":
    print(f"Items: {len(CATALOG)}")
    print(f"Total annual cost: ${total_cost_usd():,}")
