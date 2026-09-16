"""Artifact export routes (Phase 5D).

Transport only
--------------
No quantity logic, no rate arithmetic, no CheckMate rules, no journal writes.
The route reads stored data, calls the pure workbook builder, and registers
the bytes in the ArtifactRegistry.

Rate security boundary
----------------------
The caller sends ``rate_node_id`` values — integer rowids of stored
``evidence_nodes`` rows.  The route reads the ``normalized_amount`` from that
row's payload, verifies project scope and normalizer status, and puts *that
stored number* into the workbook.  A caller cannot inject a numeric rate.
"""

from __future__ import annotations

import json
import logging
from contextlib import AbstractContextManager
from typing import Protocol

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..artifacts import EXPORT_MEDIA_TYPE, ArtifactRegistry, ExportRequest, ExportResponse
from ..artifacts.bounds import MAX_EXPORT_ASSUMPTIONS, MAX_EXPORT_CHECKMATE_ROWS, MAX_EXPORT_CLAIMS
from ..artifacts.export_dto import ArtifactRef
from ..storage import QSStore, read_snapshot

log = logging.getLogger(__name__)
_XLSX_SUFFIX = ".xlsx"


class SessionLock(Protocol):
    def locked(self) -> AbstractContextManager[None]: ...


def _resolve_rate(store: QSStore, project_id: int, node_id: int) -> float | None:
    """Return stored normalized_amount for node_id in project_id, or None."""
    rows = store.rate_nodes(project_id, [node_id])
    if not rows:
        return None
    row = rows[0]
    try:
        payload = json.loads(row["payload"]) if row["payload"] else {}
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("status") != "normalized":
        return None
    amount = payload.get("normalized_amount")
    if amount is None:
        return None
    try:
        return float(amount)
    except (TypeError, ValueError):
        return None


def _require_project(store: QSStore, project_id: int) -> None:
    if store.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")


def register_artifact_routes(
    api: APIRouter,
    *,
    store: QSStore,
    sessions: SessionLock,
    artifacts: ArtifactRegistry,
    api_prefix: str = "/api/v1",
) -> None:
    """Attach artifact-export routes to an already-authenticated router."""

    @api.post(
        "/projects/{project_id}/artifacts",
        response_model=ExportResponse,
        status_code=201,
    )
    def create_artifact(request: Request, project_id: int, body: ExportRequest) -> ExportResponse:
        """Generate a BoQ workbook and register it as a downloadable artifact.

        No DB write, no journal entry, no approval mutation.
        If build_boq_workbook raises (e.g. size ceiling) the registry is left
        clean — no partial artifact is registered.
        """
        from ..artifacts import build_boq_workbook  # defer to avoid circular
        _artifacts: ArtifactRegistry = request.app.state.artifacts

        with sessions.locked():
            _require_project(store, project_id)

            with read_snapshot(store):
                claim_rows, claims_truncated = store.claims_for_export(
                    project_id, MAX_EXPORT_CLAIMS
                )
                assumption_rows, assumptions_truncated = store.assumptions_for_export(
                    project_id, MAX_EXPORT_ASSUMPTIONS
                )
                checkmate_rows, checkmate_truncated = store.checkmate_for_export(
                    project_id, MAX_EXPORT_CHECKMATE_ROWS
                )
                project_row = store.get_project(project_id)

            rate_dict: dict[str, float] = {}
            for binding in body.rate_bindings:
                resolved = _resolve_rate(store, project_id, binding.rate_node_id)
                if resolved is not None:
                    rate_dict[binding.claim_id] = resolved

        project_name = str(project_row["name"]) if project_row else "Unknown Project"

        data, wb_warnings, gate = build_boq_workbook(
            project_name=project_name,
            claim_rows=claim_rows,
            assumption_rows=assumption_rows,
            checkmate_rows=checkmate_rows,
            rate_bindings=rate_dict,
            claims_truncated=claims_truncated,
            assumptions_truncated=assumptions_truncated,
            checkmate_truncated=checkmate_truncated,
        )

        artifact_id = _artifacts.register(
            data,
            media_type=EXPORT_MEDIA_TYPE,
            project_id=project_id,
        )

        download_url = f"{api_prefix}/projects/{project_id}/artifacts/{artifact_id}"
        ref = ArtifactRef(artifact_id=artifact_id, download_url=download_url)

        return ExportResponse(
            artifact=ref,
            project_id=project_id,
            claim_count=len(claim_rows),
            truncated=claims_truncated,
            gate=gate,
            warnings=[w.detail for w in wb_warnings],
        )

    @api.get("/projects/{project_id}/artifacts/{artifact_id}")
    def download_artifact(request: Request, project_id: int, artifact_id: str) -> Response:
        """Return raw artifact bytes.

        Project isolation enforced: artifact_id must belong to project_id.
        An id from another project is a 404, identical to a missing id.
        UUID/path-traversal rejection: registry refuses ids whose length != 36
        before any dict lookup.
        """
        _artifacts: ArtifactRegistry = request.app.state.artifacts
        entry = _artifacts.get(artifact_id, project_id=project_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="artifact not found")

        filename = f"boq_{project_id}{_XLSX_SUFFIX}"
        return Response(
            content=entry.data,
            media_type=entry.media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )
