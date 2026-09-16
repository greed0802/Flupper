"""Pydantic request / response models for the artifact export route (Phase 5D).

These models live outside :mod:`qsagent.api.contracts` because they carry
export-specific bounds from :mod:`qsagent.artifacts.bounds` rather than the
API-wide ceilings in that module. Keeping them here means a test can import
the whole artifact package without pulling in the FastAPI application.

Rules (same as the rest of contracts.py):
* ``extra="forbid"`` on every model — no key smuggling.
* Every string and list is explicitly bounded.
* No credential field anywhere.

Rate binding design
-------------------
A caller supplies a *reference* to a stored rate node (a ``rate_node_id``),
not a numeric rate.  The route reads the ``normalized_amount`` for that node
out of the store, scoped to the caller's project, and uses that stored number
to populate the workbook.  A client that cannot supply a numeric rate and have
it accepted cannot nominate its own arithmetic as an authoritative evidenced
rate; the only source of truth for any amount is the store's own normalizer
output.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .bounds import (
    EXPORT_TYPE_BOQ_XLSX,
    MAX_EXPORT_CELL_CHARS,
    MAX_EXPORT_CLAIMS,
    MAX_EXPORT_RATE_BINDINGS,
    MAX_EXPORT_TEXT_CHARS,
)

# --------------------------------------------------------------------------
# Sub-models
# --------------------------------------------------------------------------

#: A positive integer — the ``id`` column of an ``evidence_nodes`` row that
#: carries a normalised rate payload.  Must be >= 1; 0 is never a valid SQLite
#: rowid, and negative values are nonsensical.  The route validates that the
#: id belongs to the request's project before any read; an id from another
#: project is treated as missing.
PositiveRateNodeId = Annotated[int, Field(ge=1)]


class RateBinding(BaseModel):
    """Pairing of a claim id to a *server-issued* rate node id.

    The caller names two server-held identifiers and nothing else — there is
    no field for a numeric amount, a unit, a currency or a category.  The
    route reads the ``normalized_amount`` for ``rate_node_id`` out of the
    store (scoped to the caller's project) and places that stored number into
    the workbook.

    A client cannot submit an authoritative rate by naming a node id that
    carries a different amount than the one it knows about: the store is the
    only source of truth.
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: Annotated[str, Field(min_length=1, max_length=MAX_EXPORT_TEXT_CHARS)]
    rate_node_id: PositiveRateNodeId


class ArtifactRef(BaseModel):
    """Opaque reference returned after a successful artifact creation.

    The ``artifact_id`` is a UUID4 canonical string (36 chars). The route
    that creates the artifact also returns a ``download_url`` the client can
    follow immediately; the id is the durable part should the client need to
    reconstruct the URL.
    """

    model_config = ConfigDict(extra="forbid")

    artifact_id: Annotated[str, Field(min_length=36, max_length=36)]
    download_url: Annotated[str, Field(min_length=1, max_length=MAX_EXPORT_CELL_CHARS)]


# --------------------------------------------------------------------------
# Request
# --------------------------------------------------------------------------

class ExportRequest(BaseModel):
    """Body of ``POST /projects/{project_id}/artifacts/export``.

    ``export_type`` is the only allowlisted value for now. Any value besides
    ``"boq_xlsx"`` is rejected at validation time, before any store access.

    ``rate_bindings`` is optional — an export with no bindings produces a
    workbook with an Unresolved entry in the Rate column for every claim row.
    """

    model_config = ConfigDict(extra="forbid")

    export_type: Literal["boq_xlsx"] = EXPORT_TYPE_BOQ_XLSX  # type: ignore[assignment]
    rate_bindings: Annotated[
        list[RateBinding],
        Field(default_factory=list, max_length=MAX_EXPORT_RATE_BINDINGS),
    ] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Response
# --------------------------------------------------------------------------

class ExportResponse(BaseModel):
    """Body of a successful ``POST /projects/{project_id}/artifacts/export``.

    ``warnings`` carries non-fatal findings (truncated claim list, unresolved
    rate, unknown claim id in a binding) up to ``MAX_EXPORT_WARNINGS``.  A
    non-empty list does not mean the workbook is wrong — the caller decides
    whether any finding is blocking.

    ``gate`` reflects the latest stored CheckMate result for this project:
    ``"PASSED"``, ``"REJECTED"`` or ``"NOT_RUN"``.  It is informational; the
    route never gates delivery on it (that is Phase 5D approval territory).
    """

    model_config = ConfigDict(extra="forbid")

    artifact: ArtifactRef
    project_id: int
    claim_count: Annotated[int, Field(ge=0, le=MAX_EXPORT_CLAIMS)]
    truncated: bool
    gate: Literal["PASSED", "REJECTED", "NOT_RUN"]
    warnings: Annotated[list[str], Field(default_factory=list, max_length=20)]
