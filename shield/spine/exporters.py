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

from ..models import CapabilityList


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
