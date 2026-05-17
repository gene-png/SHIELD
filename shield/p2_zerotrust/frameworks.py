"""Framework definitions (lifted in concept from cyberdashboardV2/data/frameworks).

These are intentionally compact starter sets for v0.1. The full control
catalogs (CISA ZTMM 2.0, DoD ZT, NIST CSF 2.0) ship as YAML/JSON files in
later iterations.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Control:
    id: str
    pillar: str
    title: str
    description: str = ""


@dataclass
class Framework:
    id: str
    name: str
    pillars: list[str]
    controls: list[Control] = field(default_factory=list)


# A small representative slice per framework. Real catalogs land in a later PR
# (sourced from cyberdashboardV2/data/frameworks/*).
CISA_ZTMM = Framework(
    id="cisa_ztmm_v2",
    name="CISA Zero Trust Maturity Model 2.0",
    pillars=["Identity", "Devices", "Networks", "Applications & Workloads", "Data"],
    controls=[
        Control("ZTMM.IDENT.1",     "Identity",                  "MFA for all users"),
        Control("ZTMM.IDENT.2",     "Identity",                  "Privileged access management"),
        Control("ZTMM.DEVICE.1",    "Devices",                   "Endpoint inventory"),
        Control("ZTMM.NET.1",       "Networks",                  "Macro-segmentation"),
        Control("ZTMM.APP.1",       "Applications & Workloads",  "Application access governance"),
        Control("ZTMM.DATA.1",      "Data",                      "Data classification"),
        Control("ZTMM.DATA.2",      "Data",                      "DLP for sensitive categories"),
    ],
)

DOD_ZT = Framework(
    id="dod_zt",
    name="DoD Zero Trust Reference Architecture",
    pillars=[
        "User", "Device", "Network/Environment", "Application/Workload",
        "Data", "Visibility & Analytics", "Automation & Orchestration",
    ],
    controls=[
        Control("DODZT.USER.1",     "User",                  "Continuous authentication"),
        Control("DODZT.USER.2",     "User",                  "Risk-adaptive access"),
        Control("DODZT.DEV.1",      "Device",                "Device posture for access decisions"),
        Control("DODZT.NET.1",      "Network/Environment",   "Micro-segmentation"),
        Control("DODZT.DATA.1",     "Data",                  "Data tagging"),
        Control("DODZT.VIS.1",      "Visibility & Analytics","Centralized logging + correlation"),
    ],
)

NIST_CSF = Framework(
    id="nist_csf_v2",
    name="NIST CSF 2.0",
    pillars=["Govern", "Identify", "Protect", "Detect", "Respond", "Recover"],
    controls=[
        Control("CSF.GV.OC-1", "Govern",   "Organizational mission understood"),
        Control("CSF.ID.AM-1", "Identify", "Asset inventory"),
        Control("CSF.PR.AA-1", "Protect",  "Identities and credentials managed"),
        Control("CSF.DE.CM-1", "Detect",   "Networks monitored"),
        Control("CSF.RS.RP-1", "Respond",  "Incident response plan tested"),
        Control("CSF.RC.RP-1", "Recover",  "Recovery plan tested"),
    ],
)

FRAMEWORKS: dict[str, Framework] = {
    f.id: f for f in (CISA_ZTMM, DOD_ZT, NIST_CSF)
}
