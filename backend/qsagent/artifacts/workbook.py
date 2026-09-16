"""openpyxl BoQ workbook builder (Phase 5D).

Evidence-first export
---------------------
This module assembles a Bill of Quantities workbook from rows that were read
out of the store by the calling route. It writes nothing to the store, issues
no journal entries, makes no HTTP calls, and has no opinion on authentication
or approval. Its only job is: given rows, produce bytes.

Layout contract (fixed; tests assert this verbatim)
----------------------------------------------------
Row 1 of every sheet: server banner (NON-DELIVERABLE DRAFT).
Row 2: column headers from bounds.CLAIM_COLUMNS etc.
Row 3+: data rows.

The Claims sheet uses an openpyxl formula in column F (Extended Amount):
``=C{row}*E{row}``.  Column C is Quantity; column E is Rate (AUD).  The row
index is server-computed from bounds.FIRST_DATA_ROW.  A total row at the
bottom uses ``=SUM(F3:F{last})``.  Both formula shapes are bounded by
MAX_EXPORT_FORMULA_CHARS and counted against MAX_EXPORT_FORMULA_CELLS.

Warnings
--------
Non-fatal anomalies are collected into a WorkbookWarning list and returned
alongside the bytes. The route decides what to surface.  The builder never
raises for a recoverable anomaly — only for a hard ceiling violation.
"""

from __future__ import annotations

import io
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Sequence

import openpyxl
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from .bounds import (
    ASSUMPTION_COLUMNS,
    BANNER_ROW,
    CHECKMATE_COLUMNS,
    CLAIM_COLUMNS,
    FIRST_DATA_ROW,
    GATE_NOT_RUN,
    GATE_PASSED,
    GATE_REJECTED,
    HEADER_ROW,
    MAX_EXPORT_ASSUMPTIONS,
    MAX_EXPORT_BYTES,
    MAX_EXPORT_CHECKMATE_ROWS,
    MAX_EXPORT_CLAIMS,
    MAX_EXPORT_EVIDENCE_PER_CLAIM,
    MAX_EXPORT_FORMULA_CELLS,
    MAX_EXPORT_FORMULA_CHARS,
    MAX_EXPORT_TEXT_CHARS,
    MAX_EXPORT_WARNINGS,
    SHEET_ASSUMPTIONS,
    SHEET_CHECKMATE,
    SHEET_CLAIMS,
    VALUE_UNRESOLVED,
)

# --------------------------------------------------------------------------
# Styling (cosmetic only — not in bounds)
# --------------------------------------------------------------------------
_BANNER_FILL = PatternFill("solid", fgColor="FFC7CE")
_HEADER_FILL = PatternFill("solid", fgColor="BDD7EE")
_TOTAL_FILL  = PatternFill("solid", fgColor="E2EFDA")
_BOLD        = Font(bold=True)
_BANNER_FONT = Font(bold=True, color="9C0006")


@dataclass
class WorkbookWarning:
    """One non-fatal anomaly found during workbook construction."""

    code: str
    detail: str


# --------------------------------------------------------------------------
# Public entry-point
# --------------------------------------------------------------------------

def build_boq_workbook(
    *,
    project_name: str,
    claim_rows: Sequence[sqlite3.Row],
    assumption_rows: Sequence[sqlite3.Row],
    checkmate_rows: Sequence[sqlite3.Row],
    rate_bindings: dict[str, float],
    claims_truncated: bool = False,
    assumptions_truncated: bool = False,
    checkmate_truncated: bool = False,
) -> tuple[bytes, list[WorkbookWarning], str]:
    """Build a BoQ workbook and return (bytes, warnings, gate).

    Parameters
    ----------
    project_name:
        Written into the banner of every sheet.
    claim_rows:
        sqlite3.Row objects from quantity_claims.
    assumption_rows:
        sqlite3.Row objects from assumptions.
    checkmate_rows:
        sqlite3.Row objects from checkmate_results.
    rate_bindings:
        {claim_id: rate_aud} mapping.
    claims_truncated / assumptions_truncated / checkmate_truncated:
        Pass-through flags from the store read.

    Returns
    -------
    tuple[bytes, list[WorkbookWarning], str]
        (xlsx_bytes, warnings, gate).

    Raises
    ------
    ValueError
        If the assembled workbook exceeds MAX_EXPORT_BYTES.
    """
    warnings: list[WorkbookWarning] = []

    claims = list(claim_rows)[:MAX_EXPORT_CLAIMS]
    if claims_truncated or len(list(claim_rows)) > MAX_EXPORT_CLAIMS:
        warnings.append(WorkbookWarning(
            code="claims_truncated",
            detail=(
                f"Claim list was truncated to {MAX_EXPORT_CLAIMS}. "
                "Remaining claims are not included in this workbook."
            ),
        ))

    assumptions = list(assumption_rows)[:MAX_EXPORT_ASSUMPTIONS]
    if assumptions_truncated or len(list(assumption_rows)) > MAX_EXPORT_ASSUMPTIONS:
        warnings.append(WorkbookWarning(
            code="assumptions_truncated",
            detail=f"Assumption list was truncated to {MAX_EXPORT_ASSUMPTIONS}.",
        ))

    checkmate = list(checkmate_rows)[:MAX_EXPORT_CHECKMATE_ROWS]
    if checkmate_truncated or len(list(checkmate_rows)) > MAX_EXPORT_CHECKMATE_ROWS:
        warnings.append(WorkbookWarning(
            code="checkmate_truncated",
            detail=f"CheckMate result list was truncated to {MAX_EXPORT_CHECKMATE_ROWS}.",
        ))

    gate = _derive_gate(checkmate)

    claim_ids = {row["claim_id"] for row in claims}
    for cid in rate_bindings:
        if cid not in claim_ids:
            warnings.append(WorkbookWarning(
                code="binding_unknown_claim",
                detail=f"Rate binding for claim_id {cid!r} does not match any exported claim.",
            ))

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    _build_claims_sheet(
        wb.create_sheet(SHEET_CLAIMS),
        project_name=project_name,
        rows=claims,
        rate_bindings=rate_bindings,
        warnings=warnings,
    )
    _build_assumptions_sheet(
        wb.create_sheet(SHEET_ASSUMPTIONS),
        project_name=project_name,
        rows=assumptions,
    )
    _build_checkmate_sheet(
        wb.create_sheet(SHEET_CHECKMATE),
        project_name=project_name,
        rows=checkmate,
        gate=gate,
    )

    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()

    if len(data) > MAX_EXPORT_BYTES:
        raise ValueError(
            f"workbook is {len(data)} bytes, ceiling is {MAX_EXPORT_BYTES}"
        )

    if len(warnings) > MAX_EXPORT_WARNINGS:
        warnings = warnings[:MAX_EXPORT_WARNINGS]

    return data, warnings, gate


# --------------------------------------------------------------------------
# Private helpers
# --------------------------------------------------------------------------

def _derive_gate(checkmate_rows: list[Any]) -> str:
    """GATE_REJECTED if any row failed, GATE_PASSED if all passed, GATE_NOT_RUN if empty."""
    if not checkmate_rows:
        return GATE_NOT_RUN
    for row in checkmate_rows:
        if not row["passed"]:
            return GATE_REJECTED
    return GATE_PASSED


def _clip(value: Any, n: int = MAX_EXPORT_TEXT_CHARS) -> str:
    s = str(value) if value is not None else ""
    return s[:n]


def _write_banner(ws: Worksheet, project_name: str, ncols: int) -> None:
    banner = _clip(
        f"NON-DELIVERABLE DRAFT  |  {project_name}  |  Generated by Flupper",
        MAX_EXPORT_TEXT_CHARS,
    )
    ws.cell(row=BANNER_ROW, column=1, value=banner)
    cell = ws.cell(row=BANNER_ROW, column=1)
    cell.font = _BANNER_FONT
    cell.fill = _BANNER_FILL
    if ncols > 1:
        ws.merge_cells(
            start_row=BANNER_ROW, start_column=1,
            end_row=BANNER_ROW, end_column=ncols,
        )


def _write_headers(ws: Worksheet, columns: tuple[str, ...]) -> None:
    for col_idx, header in enumerate(columns, start=1):
        cell = ws.cell(row=HEADER_ROW, column=col_idx, value=header)
        cell.font = _BOLD
        cell.fill = _HEADER_FILL


def _evidence_text(evidence_json: str) -> str:
    try:
        refs = json.loads(evidence_json or "[]")
    except (ValueError, TypeError):
        return ""
    parts: list[str] = []
    for ref in refs[:MAX_EXPORT_EVIDENCE_PER_CLAIM]:
        fn  = _clip(ref.get("file_name", ""), 60)
        loc = _clip(ref.get("source_locator", ref.get("drawing_no", "")), 60)
        parts.append(f"{fn}:{loc}" if loc else fn)
    return "; ".join(parts)[:MAX_EXPORT_TEXT_CHARS]


# --------------------------------------------------------------------------
# Sheet builders
# --------------------------------------------------------------------------

def _build_claims_sheet(
    ws: Worksheet,
    *,
    project_name: str,
    rows: list[Any],
    rate_bindings: dict[str, float],
    warnings: list[WorkbookWarning],
) -> None:
    """Populate the Claims sheet.

    Columns: A Claim ID, B Description, C Quantity, D Unit, E Rate (AUD),
             F Extended Amount [=C{row}*E{row}], G Method, H Evidence,
             I Measurement State, J Checkmate Status
    """
    ncols = len(CLAIM_COLUMNS)
    _write_banner(ws, project_name, ncols)
    _write_headers(ws, CLAIM_COLUMNS)

    formula_count = 0
    last_data_row = FIRST_DATA_ROW - 1

    for row_idx, row in enumerate(rows, start=FIRST_DATA_ROW):
        claim_id    = _clip(row["claim_id"])
        description = _clip(row["description"])
        quantity    = row["value"]
        unit        = _clip(row["unit"])
        method      = _clip(row["method"])
        evidence    = _evidence_text(row["evidence"] or "[]")
        mstate      = _clip(row["measurement_state"] or "")

        if claim_id in rate_bindings:
            rate: Any = rate_bindings[claim_id]
        else:
            rate = VALUE_UNRESOLVED
            warnings.append(WorkbookWarning(
                code="rate_unresolved",
                detail=f"No rate binding for claim {claim_id!r}; rate set to Unresolved.",
            ))

        formula: Any
        if isinstance(rate, (int, float)) and formula_count < MAX_EXPORT_FORMULA_CELLS:
            f = f"=C{row_idx}*E{row_idx}"
            assert len(f) <= MAX_EXPORT_FORMULA_CHARS, f
            formula = f
            formula_count += 1
        else:
            formula = ""

        ws.cell(row=row_idx, column=1, value=claim_id)
        ws.cell(row=row_idx, column=2, value=description)
        ws.cell(row=row_idx, column=3, value=quantity)
        ws.cell(row=row_idx, column=4, value=unit)
        ws.cell(row=row_idx, column=5, value=rate)
        ws.cell(row=row_idx, column=6, value=formula)
        ws.cell(row=row_idx, column=7, value=mstate)          # Measurement State
        ws.cell(row=row_idx, column=8, value=method)          # Status slot — method fits here
        ws.cell(row=row_idx, column=9, value=evidence)        # Evidence / Provenance
        last_data_row = row_idx

    if rows:
        total_row = last_data_row + 1
        ws.cell(row=total_row, column=5, value="TOTAL").font = _BOLD
        f_total = f"=SUM(F{FIRST_DATA_ROW}:F{last_data_row})"
        assert len(f_total) <= MAX_EXPORT_FORMULA_CHARS
        ws.cell(row=total_row, column=6, value=f_total).font = _BOLD
        for col in range(1, ncols + 1):
            ws.cell(row=total_row, column=col).fill = _TOTAL_FILL


def _build_assumptions_sheet(
    ws: Worksheet,
    *,
    project_name: str,
    rows: list[Any],
) -> None:
    ncols = len(ASSUMPTION_COLUMNS)
    _write_banner(ws, project_name, ncols)
    _write_headers(ws, ASSUMPTION_COLUMNS)
    for row_idx, row in enumerate(rows, start=FIRST_DATA_ROW):
        ws.cell(row=row_idx, column=1, value=_clip(row["id"]))
        ws.cell(row=row_idx, column=2, value=_clip(row["statement"]))
        ws.cell(row=row_idx, column=3, value=_clip(row["status"]))
        ws.cell(row=row_idx, column=4, value=_clip(row["rationale"] or ""))
        ws.cell(row=row_idx, column=5, value=row["impact_delta_aud"])
        ws.cell(row=row_idx, column=6, value=row["impact_value"])
        ws.cell(row=row_idx, column=7, value=_clip(row["impact_unit"] or ""))
        ws.cell(row=row_idx, column=8, value=_evidence_text(row["evidence"] or "[]"))
        ws.cell(row=row_idx, column=9, value=_clip(row["raised_at"] or ""))
        ws.cell(row=row_idx, column=10, value=_clip(row["resolved_at"] or ""))


def _build_checkmate_sheet(
    ws: Worksheet,
    *,
    project_name: str,
    rows: list[Any],
    gate: str,
) -> None:
    ncols = len(CHECKMATE_COLUMNS)
    _write_banner(ws, project_name, ncols)
    _write_headers(ws, CHECKMATE_COLUMNS)
    for row_idx, row in enumerate(rows, start=FIRST_DATA_ROW):
        raw = row["findings"] or "[]"
        try:
            findings_str = _clip(json.dumps(json.loads(raw)))
        except (ValueError, TypeError):
            findings_str = _clip(str(raw))
        ws.cell(row=row_idx, column=1, value=_clip(row["subject"]))
        ws.cell(row=row_idx, column=2, value=bool(row["passed"]))
        ws.cell(row=row_idx, column=3, value=findings_str)
        ws.cell(row=row_idx, column=4, value=_clip(row["created_at"] or ""))

