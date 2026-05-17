"""Build downloadable artifacts (XLSX) from spine entities.

Used by the routes that hand a polished file back to the user — the
canonical case is an admin sharing a client's capability-list version
with stakeholders. Output is openpyxl-rendered; no public CDNs touched.

Hard rule: exporters READ from the DB and WRITE to a BytesIO. They
never mutate spine state, and they never call the AI client.
"""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from ..models import CapabilityList, CoverageFinding, CoverageRun, Project


def _header_font() -> Font:
    return Font(bold=True, color="FFFFFF")


def _header_fill() -> PatternFill:
    return PatternFill(start_color="005EA2", end_color="005EA2", fill_type="solid")


def _ws_set_widths(ws, widths: dict[str, int]) -> None:
    for letter, w in widths.items():
        ws.column_dimensions[letter].width = w


def capability_list_to_xlsx(cl: CapabilityList) -> bytes:
    """Render a CapabilityList version as a two-sheet XLSX.

    Sheet 1 "Overview"   provenance metadata for the auditor.
    Sheet 2 "Items"      the capability rows themselves.
    """
    wb = Workbook()

    # ---- Sheet 1: Overview ----
    ws = wb.active
    ws.title = "Overview"
    ws.append(["SHIELD capability list"])
    ws["A1"].font = Font(bold=True, size=14)

    meta_rows = [
        ("Client",        cl.client.name if cl.client else "—"),
        ("Version",       f"v{cl.version}"),
        ("Label",         cl.label),
        ("Origin",        cl.origin.value),
        ("Created at",    cl.created_at.strftime("%Y-%m-%d %H:%M:%S UTC") if cl.created_at else "—"),
        ("Created by",    cl.created_by.email if cl.created_by else "—"),
        ("Item count",    len(cl.items)),
        ("Notes",         cl.notes or "—"),
    ]
    for i, (k, v) in enumerate(meta_rows, start=3):
        ws.cell(row=i, column=1, value=k).font = Font(bold=True)
        ws.cell(row=i, column=2, value=str(v))
    _ws_set_widths(ws, {"A": 18, "B": 60})
    ws["A12"] = "Origin legend"
    ws["A12"].font = Font(bold=True)
    ws["A13"] = "human_input"
    ws["B13"] = "Source data uploaded or entered by a human."
    ws["A14"] = "ai_generated"
    ws["B14"] = "Produced by an AI processing step. Not source data."
    ws["A15"] = "human_ai_informed"
    ws["B15"] = "Human-authored synthesis that cites AI findings (e.g. admin-final list)."

    # ---- Sheet 2: Items ----
    items_ws = wb.create_sheet("Items")
    headers = [
        "Name", "Vendor", "Category", "Function",
        "Annual cost (USD)", "License count", "Notes",
    ]
    items_ws.append(headers)
    for col in range(1, len(headers) + 1):
        c = items_ws.cell(row=1, column=col)
        c.font = _header_font()
        c.fill = _header_fill()
        c.alignment = Alignment(horizontal="center")

    for item in cl.items:
        items_ws.append([
            item.name,
            item.vendor or "",
            item.category or "",
            item.function or "",
            item.annual_cost_usd or 0,
            item.license_count or 0,
            item.notes or "",
        ])

    items_ws.freeze_panes = "A2"
    _ws_set_widths(items_ws, {
        "A": 32, "B": 22, "C": 18, "D": 36,
        "E": 18, "F": 14, "G": 40,
    })

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------
# Platform 3 — ATT&CK coverage run
# --------------------------------------------------------------------

def coverage_run_to_xlsx(
    run: CoverageRun,
    project: Project,
    findings: list[CoverageFinding],
    techniques_by_id: dict[str, dict],
) -> bytes:
    """Render a CoverageRun as a 4-sheet executive deliverable.

    Sheets:
      1. Summary       — exec-readable: headline + counts + top blind spots.
      2. Coverage      — every technique with its coverage classification.
      3. Gaps          — uncovered + partial only (the call-to-action subset).
      4. Methodology   — capability list version, AI prompt, model, when.

    techniques_by_id is built by the caller (route reads it via the same
    _load_techniques() helper P3 routes use, so we don't import that here).
    """
    wb = Workbook()

    summary = run.summary or {}
    findings = list(findings)

    # ------------- Sheet 1: Summary -------------
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "SHIELD ATT&CK coverage analysis"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = summary.get("headline", "")
    ws["A2"].font = Font(italic=True, size=12)
    ws["A2"].alignment = Alignment(wrap_text=True)

    ws["A4"] = "Client"
    ws["B4"] = project.client.name if project.client else "—"
    ws["A5"] = "Project"
    ws["B5"] = project.name
    ws["A6"] = "Coverage run at"
    ws["B6"] = run.created_at.strftime("%Y-%m-%d %H:%M:%S UTC") if run.created_at else "—"
    for r in range(4, 7):
        ws.cell(row=r, column=1).font = Font(bold=True)

    ws["A8"] = "Coverage class"
    ws["B8"] = "Count"
    for c in ("A8", "B8"):
        ws[c].font = _header_font()
        ws[c].fill = _header_fill()
    ws["A9"] = "covered"
    ws["B9"] = int(summary.get("covered", 0) or 0)
    ws["A10"] = "partial"
    ws["B10"] = int(summary.get("partial", 0) or 0)
    ws["A11"] = "uncovered"
    ws["B11"] = int(summary.get("uncovered", 0) or 0)
    ws["A12"] = "total"
    ws["B12"] = int(summary.get("total_techniques", len(findings)) or len(findings))
    ws["A12"].font = Font(bold=True)
    ws["B12"].font = Font(bold=True)

    ws["A14"] = "Top blind spots"
    ws["A14"].font = Font(bold=True)
    top_three = summary.get("top_three_blind_spots", []) or []
    for i, tid in enumerate(top_three, start=15):
        ws.cell(row=i, column=1, value=tid)
        t = techniques_by_id.get(tid, {})
        ws.cell(row=i, column=2, value=t.get("name", ""))
        ws.cell(row=i, column=3, value=t.get("tactic", ""))
    _ws_set_widths(ws, {"A": 18, "B": 38, "C": 22})

    # ------------- Sheet 2: Coverage matrix -------------
    cov = wb.create_sheet("Coverage")
    cov.append([
        "Technique ID", "Technique name", "Tactic", "Coverage",
        "Detection tools", "Prevention tools", "Response tools", "Rationale",
    ])
    for col in range(1, 9):
        c = cov.cell(row=1, column=col)
        c.font = _header_font()
        c.fill = _header_fill()
        c.alignment = Alignment(horizontal="center")
    cov.freeze_panes = "A2"

    # Order rows by tactic, then technique_id, so the matrix scans naturally.
    sorted_findings = sorted(
        findings,
        key=lambda f: (
            (techniques_by_id.get(f.technique_id, {}) or {}).get("tactic", "z"),
            f.technique_id,
        ),
    )
    for f in sorted_findings:
        t = techniques_by_id.get(f.technique_id, {}) or {}
        cov.append([
            f.technique_id,
            t.get("name", ""),
            t.get("tactic", ""),
            f.coverage,
            ", ".join(f.detection_tools or []),
            ", ".join(f.prevention_tools or []),
            ", ".join(f.response_tools or []),
            f.rationale or "",
        ])
    _ws_set_widths(cov, {
        "A": 14, "B": 36, "C": 22, "D": 12,
        "E": 30, "F": 30, "G": 30, "H": 60,
    })

    # ------------- Sheet 3: Gaps -------------
    gaps = wb.create_sheet("Gaps")
    gaps.append([
        "Technique ID", "Technique name", "Tactic", "Coverage", "Rationale",
    ])
    for col in range(1, 6):
        c = gaps.cell(row=1, column=col)
        c.font = _header_font()
        c.fill = _header_fill()
        c.alignment = Alignment(horizontal="center")
    gaps.freeze_panes = "A2"
    for f in sorted_findings:
        if f.coverage in ("uncovered", "partial"):
            t = techniques_by_id.get(f.technique_id, {}) or {}
            gaps.append([
                f.technique_id, t.get("name", ""), t.get("tactic", ""),
                f.coverage, f.rationale or "",
            ])
    _ws_set_widths(gaps, {"A": 14, "B": 36, "C": 22, "D": 12, "E": 60})

    # ------------- Sheet 4: Methodology -------------
    meth = wb.create_sheet("Methodology")
    meth["A1"] = "Methodology"
    meth["A1"].font = Font(bold=True, size=14)
    cl = run.capability_list_version_id
    rows: list[tuple[str, str]] = [
        ("Capability list version id", cl or "—"),
        ("Number of techniques evaluated", str(len(findings))),
        ("Source artifact",
         f"AI artifact {run.artifact_id[:8]}" if run.artifact_id else "—"),
        ("Coverage run id", run.id),
        ("Project id", project.id),
        ("Client id", project.client_id),
        ("Generated", run.created_at.strftime("%Y-%m-%d %H:%M:%S UTC") if run.created_at else "—"),
        ("",                             ""),
        ("How to read this workbook",    ""),
        ("",
         "Summary is the executive-reading-path artifact: the CISO/CFO/CEO "
         "should be able to answer 'what are we blind to?' without opening "
         "the matrix. Per the spec (§8.3) the matrix is the substrate, not "
         "the surface."),
        ("",
         "Coverage lists every technique evaluated, sorted by tactic. Use "
         "this to verify the AI's reasoning for any specific finding."),
        ("",
         "Gaps is the subset of Coverage where the result is uncovered or "
         "partial — the funded items for the remediation plan."),
    ]
    for i, (k, v) in enumerate(rows, start=3):
        meth.cell(row=i, column=1, value=k).font = Font(bold=True)
        meth.cell(row=i, column=2, value=v).alignment = Alignment(wrap_text=True)
    _ws_set_widths(meth, {"A": 32, "B": 70})

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
