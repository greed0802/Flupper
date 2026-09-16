"""Artifact export package (Phase 5D).

Public surface
--------------
Everything a route, a registry or a test needs is re-exported here so imports
read ``from qsagent.artifacts import ...`` rather than reaching into sub-modules.
The split exists so the route module never touches openpyxl directly and the
workbook builder never knows about HTTP.

What this package does
----------------------
* :mod:`.bounds` — every ceiling in one place; nothing inside a function.
* :mod:`.registry` — thread-safe, in-memory artifact store with TTL eviction.
* :mod:`.workbook` — openpyxl builder that assembles a BoQ workbook from store
  rows and writes it to *bytes*; no file handle, no path, no side-effects.
* :mod:`.export_dto` — Pydantic request / response models for the export route.

What this package does NOT do
------------------------------
No route binding, no authentication, no store writes, no journal entries. The
route that calls :func:`build_boq_workbook` lives in :mod:`qsagent.api`.
"""

from __future__ import annotations

from .bounds import (
    ARTIFACT_CLEANUP_INTERVAL_SECONDS,
    ARTIFACT_ID_CHARS,
    ASSUMPTION_COLUMNS,
    BANNER_ROW,
    CHECKMATE_COLUMNS,
    CLAIM_COLUMNS,
    CLAIM_STATUSES,
    DEFAULT_ARTIFACT_TTL_SECONDS,
    EXPORT_MEDIA_TYPE,
    EXPORT_TYPE_BOQ_XLSX,
    FIRST_DATA_ROW,
    GATE_NOT_RUN,
    GATE_PASSED,
    GATE_REJECTED,
    HEADER_ROW,
    MAX_ARTIFACT_AGE_SECONDS,
    MAX_EXPORT_ASSUMPTIONS,
    MAX_EXPORT_BYTES,
    MAX_EXPORT_CELL_CHARS,
    MAX_EXPORT_CHECKMATE_ROWS,
    MAX_EXPORT_CLAIMS,
    MAX_EXPORT_COLUMNS,
    MAX_EXPORT_EVIDENCE_PER_CLAIM,
    MAX_EXPORT_FORMULA_CELLS,
    MAX_EXPORT_FORMULA_CHARS,
    MAX_EXPORT_RATE_BINDINGS,
    MAX_EXPORT_SHEETS,
    MAX_EXPORT_TEXT_CHARS,
    MAX_EXPORT_WARNINGS,
    SHEET_ASSUMPTIONS,
    SHEET_CHECKMATE,
    SHEET_CLAIMS,
    SHEET_NAMES,
    STATUS_ACCEPTED,
    STATUS_REVIEW,
    STATUS_UNRESOLVED,
    VALUE_UNAVAILABLE,
    VALUE_UNRESOLVED,
)
from .export_dto import (
    ArtifactRef,
    ExportRequest,
    ExportResponse,
    RateBinding,
)
from .registry import ArtifactEntry, ArtifactRegistry
from .workbook import WorkbookWarning, build_boq_workbook

__all__ = [
    # bounds
    "ARTIFACT_CLEANUP_INTERVAL_SECONDS",
    "ARTIFACT_ID_CHARS",
    "ASSUMPTION_COLUMNS",
    "BANNER_ROW",
    "CHECKMATE_COLUMNS",
    "CLAIM_COLUMNS",
    "CLAIM_STATUSES",
    "DEFAULT_ARTIFACT_TTL_SECONDS",
    "EXPORT_MEDIA_TYPE",
    "EXPORT_TYPE_BOQ_XLSX",
    "FIRST_DATA_ROW",
    "GATE_NOT_RUN",
    "GATE_PASSED",
    "GATE_REJECTED",
    "HEADER_ROW",
    "MAX_ARTIFACT_AGE_SECONDS",
    "MAX_EXPORT_ASSUMPTIONS",
    "MAX_EXPORT_BYTES",
    "MAX_EXPORT_CELL_CHARS",
    "MAX_EXPORT_CHECKMATE_ROWS",
    "MAX_EXPORT_CLAIMS",
    "MAX_EXPORT_COLUMNS",
    "MAX_EXPORT_EVIDENCE_PER_CLAIM",
    "MAX_EXPORT_FORMULA_CELLS",
    "MAX_EXPORT_FORMULA_CHARS",
    "MAX_EXPORT_RATE_BINDINGS",
    "MAX_EXPORT_SHEETS",
    "MAX_EXPORT_TEXT_CHARS",
    "MAX_EXPORT_WARNINGS",
    "SHEET_ASSUMPTIONS",
    "SHEET_CHECKMATE",
    "SHEET_CLAIMS",
    "SHEET_NAMES",
    "STATUS_ACCEPTED",
    "STATUS_REVIEW",
    "STATUS_UNRESOLVED",
    "VALUE_UNAVAILABLE",
    "VALUE_UNRESOLVED",
    # export_dto
    "ArtifactRef",
    "ExportRequest",
    "ExportResponse",
    "RateBinding",
    # registry
    "ArtifactEntry",
    "ArtifactRegistry",
    # workbook
    "WorkbookWarning",
    "build_boq_workbook",
]
