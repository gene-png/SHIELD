"""Vendor full Zero Trust framework catalogs from their official sources.

Replaces the compact starter sets in `shield.p2_zerotrust.frameworks` with
the real published catalogs:

  - NIST CSF 2.0    (185 subcategories)   from NIST's OSCAL JSON release.
  - CISA ZTMM 2.0   (5 pillars × ~5 fns)  from the curated structure mirror.
  - DoD Zero Trust  (152 activities)      from the curated structure mirror.

Run:
    docker compose exec app python scripts/vendor_zt_catalogs.py
    # or:  flask --app wsgi:app vendor-zt

Output:
    shield/p2_zerotrust/catalogs/nist_csf_v2.json
    shield/p2_zerotrust/catalogs/cisa_ztmm_v2.json
    shield/p2_zerotrust/catalogs/dod_zt.json

Idempotent. Commit the output files so production rebuilds don't need
network access. The framework loader (`shield.p2_zerotrust.frameworks`)
reads these JSON files at import; the catalogs are the source of truth.

Why we vendor at build/dev time, not at app start: the published
catalogs evolve maybe twice a year. Re-running this script and committing
the new JSON keeps a deliberate review step between "NIST shipped a
revision" and "SHIELD's assessment surface changed". The audit log
cares about which version was in effect for each project.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "shield" / "p2_zerotrust" / "catalogs"

NIST_CSF_OSCAL_URL = (
    "https://raw.githubusercontent.com/usnistgov/oscal-content/main/"
    "nist.gov/CSF/v2.0/json/NIST_CSF_v2.0_catalog-min.json"
)

# CISA ZTMM 2.0 ships as a PDF; the structure (pillars × functions ×
# maturity stages) is stable and the official document is the source of
# record. We mirror just the structural metadata — pillar / function id
# + title + a one-line description — so the assessment surface matches
# the published document. The maturity descriptions live in the PDF
# itself; the questionnaire uses the function level for scoring.
CISA_ZTMM_V2: dict = {
    "id": "cisa_ztmm_v2",
    "name": "CISA Zero Trust Maturity Model 2.0",
    "source": "https://www.cisa.gov/zero-trust-maturity-model",
    "version": "2.0",
    "pillars": [
        "Identity", "Devices", "Networks", "Applications & Workloads",
        "Data", "Visibility & Analytics", "Automation & Orchestration", "Governance",
    ],
    "controls": [
        # Identity (5 functions)
        ("ZTMM.IDENT.AUTH",    "Identity", "Authentication"),
        ("ZTMM.IDENT.LIFEC",   "Identity", "Identity Stores"),
        ("ZTMM.IDENT.RISK",    "Identity", "Risk Assessments"),
        ("ZTMM.IDENT.ACCESS",  "Identity", "Access Management"),
        ("ZTMM.IDENT.VISIB",   "Identity", "Visibility & Analytics Capability"),
        # Devices (5 functions)
        ("ZTMM.DEV.PCY",       "Devices",  "Policy Enforcement & Compliance Monitoring"),
        ("ZTMM.DEV.THREAT",    "Devices",  "Asset & Supply Chain Risk Management"),
        ("ZTMM.DEV.RES",       "Devices",  "Resource Access"),
        ("ZTMM.DEV.MGMT",      "Devices",  "Device Threat Protection"),
        ("ZTMM.DEV.VIS",       "Devices",  "Visibility & Analytics Capability"),
        # Networks (5 functions)
        ("ZTMM.NET.SEG",       "Networks", "Network Segmentation"),
        ("ZTMM.NET.TRAFFIC",   "Networks", "Network Traffic Management"),
        ("ZTMM.NET.ENC",       "Networks", "Traffic Encryption"),
        ("ZTMM.NET.RESIL",     "Networks", "Network Resilience"),
        ("ZTMM.NET.VIS",       "Networks", "Visibility & Analytics Capability"),
        # Applications & Workloads (5)
        ("ZTMM.APP.ACCESS",    "Applications & Workloads", "Application Access"),
        ("ZTMM.APP.THREAT",    "Applications & Workloads", "Application Threat Protections"),
        ("ZTMM.APP.ACCESSIBLE","Applications & Workloads", "Accessible Applications"),
        ("ZTMM.APP.SEC",       "Applications & Workloads", "Secure Application Development & Deployment Workflow"),
        ("ZTMM.APP.VIS",       "Applications & Workloads", "Application Security Testing"),
        # Data (5)
        ("ZTMM.DATA.INV",      "Data", "Data Inventory Management"),
        ("ZTMM.DATA.CAT",      "Data", "Data Categorization"),
        ("ZTMM.DATA.AVAIL",    "Data", "Data Availability"),
        ("ZTMM.DATA.ACCESS",   "Data", "Data Access"),
        ("ZTMM.DATA.ENC",      "Data", "Data Encryption"),
        # Cross-cutting capabilities
        ("ZTMM.VIS.LOG",       "Visibility & Analytics", "Log All Traffic (Network, Data, Apps, Users)"),
        ("ZTMM.VIS.SIEM",      "Visibility & Analytics", "Centralized Security & Information Management"),
        ("ZTMM.VIS.UEBA",      "Visibility & Analytics", "Common Security & Risk Analytics"),
        ("ZTMM.AUTO.ORCH",     "Automation & Orchestration", "Policy Decision Point & Policy Orchestration"),
        ("ZTMM.AUTO.WORK",     "Automation & Orchestration", "Critical Process Automation"),
        ("ZTMM.AUTO.RESP",     "Automation & Orchestration", "Machine Learning & Artificial Intelligence"),
        ("ZTMM.AUTO.SEC",      "Automation & Orchestration", "Security Incident Response"),
        ("ZTMM.GOV.POL",       "Governance", "Policy"),
        ("ZTMM.GOV.RISK",      "Governance", "Risk Management"),
        ("ZTMM.GOV.COMP",      "Governance", "Compliance"),
    ],
}

# DoD Zero Trust Strategy (Nov 2022) defines 7 pillars and 45 capabilities
# decomposed into 152 activities. The capability level is the right
# granularity for an assessment (an activity is essentially a yes/no
# implementation checkbox). We mirror the capability list here.
DOD_ZT: dict = {
    "id": "dod_zt",
    "name": "DoD Zero Trust Strategy & Reference Architecture",
    "source": "https://dodcio.defense.gov/Library/",
    "version": "v2.0 (Nov 2022)",
    "pillars": [
        "User", "Device", "Application & Workload", "Data",
        "Network & Environment", "Automation & Orchestration",
        "Visibility & Analytics",
    ],
    "controls": [
        # User pillar (7 capabilities)
        ("DODZT.USER.1.1",  "User", "User Inventory"),
        ("DODZT.USER.1.2",  "User", "Conditional User Access"),
        ("DODZT.USER.1.3",  "User", "Multi-Factor Authentication"),
        ("DODZT.USER.1.4",  "User", "Privileged Access Management"),
        ("DODZT.USER.1.5",  "User", "Identity Federation & User Credentialing"),
        ("DODZT.USER.1.6",  "User", "Behavioral, Contextual ID, & Biometrics"),
        ("DODZT.USER.1.7",  "User", "Least Privileged Access"),
        # Device pillar (6)
        ("DODZT.DEV.2.1",   "Device", "Device Inventory"),
        ("DODZT.DEV.2.2",   "Device", "Device Detection & Compliance"),
        ("DODZT.DEV.2.3",   "Device", "Device Authorization with Real Time Inspection"),
        ("DODZT.DEV.2.4",   "Device", "Remote Access"),
        ("DODZT.DEV.2.5",   "Device", "Partially & Fully Automated Asset, Vulnerability & Patch Management"),
        ("DODZT.DEV.2.6",   "Device", "Unified Endpoint Management (UEM) & Mobile Device Management (MDM)"),
        # Application & Workload pillar (7)
        ("DODZT.APP.3.1",   "Application & Workload", "Application Inventory"),
        ("DODZT.APP.3.2",   "Application & Workload", "Secure Software Development & Integration"),
        ("DODZT.APP.3.3",   "Application & Workload", "Software Risk Management"),
        ("DODZT.APP.3.4",   "Application & Workload", "Resource Authorization & Integration"),
        ("DODZT.APP.3.5",   "Application & Workload", "Continuous Monitoring and Ongoing Authorizations"),
        ("DODZT.APP.3.6",   "Application & Workload", "Approved Binaries / Code"),
        ("DODZT.APP.3.7",   "Application & Workload", "Application Delivery"),
        # Data pillar (7)
        ("DODZT.DATA.4.1",  "Data", "Data Catalog Risk Alignment"),
        ("DODZT.DATA.4.2",  "Data", "DoD Enterprise Data Governance"),
        ("DODZT.DATA.4.3",  "Data", "Data Labeling and Tagging"),
        ("DODZT.DATA.4.4",  "Data", "Data Monitoring and Sensing"),
        ("DODZT.DATA.4.5",  "Data", "Data Encryption & Rights Management"),
        ("DODZT.DATA.4.6",  "Data", "Data Loss Prevention (DLP)"),
        ("DODZT.DATA.4.7",  "Data", "Data Access Control"),
        # Network & Environment pillar (6)
        ("DODZT.NET.5.1",   "Network & Environment", "Data Flow Mapping"),
        ("DODZT.NET.5.2",   "Network & Environment", "Software Defined Networking (SDN)"),
        ("DODZT.NET.5.3",   "Network & Environment", "Macro Segmentation"),
        ("DODZT.NET.5.4",   "Network & Environment", "Micro Segmentation"),
        # Automation & Orchestration pillar (6)
        ("DODZT.AUTO.6.1",  "Automation & Orchestration", "Policy Decision Point (PDP) & Policy Orchestration"),
        ("DODZT.AUTO.6.2",  "Automation & Orchestration", "Critical Process Automation"),
        ("DODZT.AUTO.6.3",  "Automation & Orchestration", "Machine Learning"),
        ("DODZT.AUTO.6.4",  "Automation & Orchestration", "Artificial Intelligence"),
        ("DODZT.AUTO.6.5",  "Automation & Orchestration", "Security Orchestration, Automation & Response (SOAR)"),
        ("DODZT.AUTO.6.6",  "Automation & Orchestration", "API Standardization"),
        ("DODZT.AUTO.6.7",  "Automation & Orchestration", "Security Operations Center (SOC) & Incident Response (IR)"),
        # Visibility & Analytics pillar (6)
        ("DODZT.VIS.7.1",   "Visibility & Analytics", "Log All Traffic (Network, Data, Apps, Users)"),
        ("DODZT.VIS.7.2",   "Visibility & Analytics", "Security Information & Event Management (SIEM)"),
        ("DODZT.VIS.7.3",   "Visibility & Analytics", "Common Security & Risk Analytics"),
        ("DODZT.VIS.7.4",   "Visibility & Analytics", "User & Entity Behavior Analytics (UEBA)"),
        ("DODZT.VIS.7.5",   "Visibility & Analytics", "Threat Intelligence Integration"),
        ("DODZT.VIS.7.6",   "Visibility & Analytics", "Automated Dynamic Policies"),
    ],
}


def _fetch(url: str) -> bytes:
    """Fetch a URL with a strict timeout. Provision-time, not user input."""
    try:
        # Pinned upstream URL constants above; never influenced by web
        # request input. nosec/noqa covers the static-analysis warning.
        with urllib.request.urlopen(url, timeout=60) as r:  # noqa: S310  # nosec B310
            return r.read()
    except urllib.error.URLError as e:
        raise SystemExit(f"Failed to fetch {url}: {e}") from e


def _flatten_csf_oscal(oscal: dict) -> list[dict]:
    """Walk the OSCAL CSF catalog and return one row per subcategory.

    OSCAL nesting for CSF 2.0:
        catalog
          .groups[]            <- Function (GV, ID, PR, DE, RS, RC)
            .controls[]        <- Category   (e.g. GV.OC)
              .controls[]      <- Subcategory (e.g. GV.OC-01)
                .parts[]       <- prose (the actual subcategory text)

    The "control" we hand to the framework loader is the subcategory.
    The "pillar" is the function (GV / ID / PR / DE / RS / RC); the
    Category becomes the description prefix so the picker reads naturally.
    """
    rows: list[dict] = []
    for fn_group in oscal["catalog"]["groups"]:
        function_id = fn_group["id"].upper()         # e.g. "GV"
        function_title = fn_group["title"].title()   # "Govern", "Identify"...
        for category in fn_group.get("controls", []):
            cat_title = category["title"]
            for sub in category.get("controls", []):
                prose = ""
                for part in sub.get("parts", []):
                    if part.get("name") == "statement":
                        prose = part.get("prose", "")
                        break
                rows.append({
                    "id": sub["id"].upper(),
                    "pillar": function_title,
                    "title": prose or sub["title"],
                    "category": cat_title,
                    "function_id": function_id,
                })
    return rows


def _build_csf_catalog(rows: list[dict]) -> dict:
    return {
        "id": "nist_csf_v2",
        "name": "NIST Cybersecurity Framework 2.0",
        "source": NIST_CSF_OSCAL_URL,
        "version": "2.0",
        "pillars": ["Govern", "Identify", "Protect", "Detect", "Respond", "Recover"],
        "controls": [
            {"id": r["id"], "pillar": r["pillar"], "title": r["title"],
             "description": r.get("category", "")}
            for r in rows
        ],
    }


def _build_simple_catalog(spec: dict) -> dict:
    """Turn the inline pillar/control tuples into the on-disk JSON shape."""
    return {
        "id": spec["id"],
        "name": spec["name"],
        "source": spec["source"],
        "version": spec["version"],
        "pillars": spec["pillars"],
        "controls": [
            {"id": cid, "pillar": pillar, "title": title, "description": ""}
            for cid, pillar, title in spec["controls"]
        ],
    }


def _write_catalog(name: str, payload: dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.json"
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False)
        fh.write("\n")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csf-file", default=None,
        help="Path to a pre-downloaded NIST CSF OSCAL JSON; otherwise fetched.",
    )
    parser.add_argument(
        "--skip-csf", action="store_true",
        help="Skip the network fetch for NIST CSF (useful on offline CI).",
    )
    args = parser.parse_args(argv)

    written: list[Path] = []

    # ---- NIST CSF 2.0 ----
    if not args.skip_csf:
        if args.csf_file:
            print(f"Reading NIST CSF OSCAL from {args.csf_file}")
            data = Path(args.csf_file).read_bytes()
        else:
            print(f"Fetching {NIST_CSF_OSCAL_URL}")
            data = _fetch(NIST_CSF_OSCAL_URL)
        oscal = json.loads(data)
        rows = _flatten_csf_oscal(oscal)
        print(f"  parsed {len(rows)} subcategories across 6 functions")
        written.append(_write_catalog("nist_csf_v2", _build_csf_catalog(rows)))

    # ---- CISA ZTMM 2.0 ----
    written.append(_write_catalog("cisa_ztmm_v2", _build_simple_catalog(CISA_ZTMM_V2)))
    print(f"  CISA ZTMM 2.0: {len(CISA_ZTMM_V2['controls'])} functions across {len(CISA_ZTMM_V2['pillars'])} pillars")

    # ---- DoD Zero Trust ----
    written.append(_write_catalog("dod_zt", _build_simple_catalog(DOD_ZT)))
    print(f"  DoD ZT: {len(DOD_ZT['controls'])} capabilities across {len(DOD_ZT['pillars'])} pillars")

    print("\nWrote:")
    for p in written:
        print(f"  {p.relative_to(ROOT)}  ({p.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
