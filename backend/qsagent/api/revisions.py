"""Revision diff route (Phase 5B): read-only, authenticated, bounded.

Transport and bounds only
-------------------------
This module resolves the two documents a caller named, reads their evidence
under one snapshot, and renders what
:mod:`qsagent.revisions.diff` classified. It decides nothing about quantities,
runs no CheckMate rule, writes nothing and journals nothing. A diff is a
report, and a report that changed a claim's status would not be one.

Nothing the caller sends is trusted as provenance
-------------------------------------------------
The route takes a project id and two document ids. It does not take a file
hash, a filename or a path, so there is no way to nominate which file the
comparison runs against: the server resolves both sides from the store, inside
the project, or it answers 404. Every hash, name and revision in the response
was read back out of the database.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Iterable, Mapping, Optional, Protocol, Sequence

from fastapi import APIRouter, HTTPException

from ..revisions import (
    ChangeRecord,
    StoredEvidence,
    diff_nodes,
    map_affected_claims,
    normalize_identifier,
)
from ..storage import QSStore, read_snapshot
from .contracts import (
    MAX_DIFF_AFFECTED_CLAIMS,
    MAX_DIFF_CLAIM_ID_CHARS,
    MAX_DIFF_FIELDS,
    MAX_DIFF_ITEMS,
    MAX_DIFF_REASON_CHARS,
    MAX_DIFF_SCANNED_CLAIMS,
    MAX_DIFF_SCANNED_DOCUMENTS,
    MAX_DIFF_SCANNED_NODES,
    MAX_DIFF_TEXT_CHARS,
    MAX_DIFF_WARNINGS,
    RevisionChangeDTO,
    RevisionDiffCountsDTO,
    RevisionDiffResponse,
    RevisionDocumentDTO,
    RevisionEvidenceDTO,
)

#: Ceilings on what is read out of the store for one request live in
#: ``contracts`` beside the response bounds, because a row count reported to
#: the caller is a bound the caller can check.
MAX_SCANNED_NODES_PER_DOCUMENT = MAX_DIFF_SCANNED_NODES
MAX_SCANNED_CLAIMS = MAX_DIFF_SCANNED_CLAIMS
MAX_SCANNED_DOCUMENTS = MAX_DIFF_SCANNED_DOCUMENTS

RELATIONSHIP_SAME_DRAWING = "same_drawing"
RELATIONSHIP_UNVERIFIED = "unverified_drawing"
RELATIONSHIP_NO_EVIDENCE = "evidence_unavailable"

WARNING_BASE_NO_EVIDENCE = "base_document_has_no_evidence_rows"
WARNING_TARGET_NO_EVIDENCE = "target_document_has_no_evidence_rows"
WARNING_DRAWING_UNVERIFIED = "drawing_identity_unverified"
WARNING_NODES_TRUNCATED = "evidence_rows_truncated"
WARNING_CLAIMS_TRUNCATED = "claims_truncated"
WARNING_UNVERIFIED_REFERENCES = "claim_references_without_document_lineage"
WARNING_MALFORMED_CLAIMS = "claims_with_unreadable_evidence"
WARNING_UNASSOCIATED_CLAIMS = "claims_not_associated_with_a_specific_evidence_row"

MESSAGE_UNKNOWN_PROJECT = "unknown project"
MESSAGE_UNKNOWN_BASE = "unknown base document for this project"
MESSAGE_UNKNOWN_TARGET = "unknown target document for this project"
MESSAGE_SAME_DOCUMENT = "base and target document are the same document"
MESSAGE_SAME_HASH = "base and target document do not name two distinct source files"
MESSAGE_DRAWING_MISMATCH = "base and target document are different drawings"


class SessionLock(Protocol):
    """The one thing this module needs from the gateway's session registry.

    Declared structurally so the router can be registered without importing
    ``qsagent.api.main``, which imports this module.
    """

    def locked(self) -> AbstractContextManager[None]: ...


def _clip(value: Any, limit: int = MAX_DIFF_TEXT_CHARS) -> tuple[Optional[str], bool]:
    """Trim a stored string to *limit*, reporting whether it was trimmed."""
    if value is None:
        return None, False
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _clip_list(
    values: Iterable[Any],
    limit: int,
    item_chars: int = MAX_DIFF_TEXT_CHARS,
) -> tuple[list[str], int]:
    """Trim a list of strings, reporting how many entries were dropped."""
    items = [str(value)[:item_chars] for value in values]
    if len(items) <= limit:
        return items, 0
    return items[:limit], len(items) - limit


def _evidence_dto(view: StoredEvidence) -> RevisionEvidenceDTO:
    raw_text, raw_text_truncated = _clip(view.raw_text)
    node_type, _ = _clip(view.node_type)
    label, _ = _clip(view.label)
    drawing_no, _ = _clip(view.drawing_no)
    revision, _ = _clip(view.revision)
    sheet, _ = _clip(view.sheet)
    zone, _ = _clip(view.zone)
    return RevisionEvidenceDTO(
        node_id=view.node_id,
        identity_id=view.identity_id,
        node_type=node_type or "unknown",
        label=label or "",
        drawing_no=drawing_no,
        revision=revision,
        sheet=sheet,
        page=view.page,
        zone=zone,
        file_hash=(view.file_hash or None),
        raw_text=raw_text,
        raw_text_truncated=raw_text_truncated,
    )


def _document_dto(
    row: Mapping[str, Any],
    *,
    evidence_rows: int,
    evidence_truncated: bool,
) -> RevisionDocumentDTO:
    file_name, _ = _clip(row["file_name"])
    drawing_no, _ = _clip(row["drawing_no"])
    revision, _ = _clip(row["revision"])
    return RevisionDocumentDTO(
        document_id=int(row["id"]),
        file_name=file_name or "",
        file_hash=str(row["file_hash"] or "").lower(),
        drawing_no=drawing_no,
        revision=revision,
        evidence_rows=evidence_rows,
        evidence_truncated=evidence_truncated,
    )


def _require_same_drawing(
    base_row: Mapping[str, Any], target_row: Mapping[str, Any]
) -> bool:
    """Reject a comparison across drawings; report whether it was verified.

    Returns ``True`` when both sides name a drawing and the names match, which
    is the only case where a revision relationship is established. A missing
    drawing number on either side is not an error - plenty of sources do not
    carry one - but it is not evidence of a relationship either, so the caller
    reports the comparison as unverified rather than silently treating it as a
    revision of the same drawing.

    Two *different* drawing numbers, on the other hand, are a caller mistake:
    whatever the two documents are, they are not two revisions of one drawing.
    """
    base_drawing = normalize_identifier(base_row["drawing_no"])
    target_drawing = normalize_identifier(target_row["drawing_no"])
    if base_drawing and target_drawing and base_drawing != target_drawing:
        raise HTTPException(status_code=400, detail=MESSAGE_DRAWING_MISMATCH)
    return bool(base_drawing and target_drawing)


def _counts(records: Sequence[ChangeRecord]) -> RevisionDiffCountsDTO:
    tally = {status: 0 for status in RevisionDiffCountsDTO.model_fields}
    for record in records:
        tally[record.status] = tally.get(record.status, 0) + 1
    return RevisionDiffCountsDTO(**tally)


def build_revision_diff(
    store: QSStore,
    project_id: int,
    base_document_id: int,
    target_document_id: int,
) -> RevisionDiffResponse:
    """Compare two stored revisions. Reads only; the caller holds the lock.

    Every read happens inside one :func:`read_snapshot`, so the two document
    rows, both evidence sets and the project's claims all describe the same
    instant even if an ingest commits while the comparison is running. The
    snapshot is released, never committed, and the shared connection stays
    open.
    """
    if base_document_id == target_document_id:
        raise HTTPException(status_code=400, detail=MESSAGE_SAME_DOCUMENT)

    with read_snapshot(store):
        if store.get_project(project_id) is None:
            raise HTTPException(status_code=404, detail=MESSAGE_UNKNOWN_PROJECT)

        base_row = store.get_document(project_id, base_document_id)
        if base_row is None:
            raise HTTPException(status_code=404, detail=MESSAGE_UNKNOWN_BASE)
        target_row = store.get_document(project_id, target_document_id)
        if target_row is None:
            raise HTTPException(status_code=404, detail=MESSAGE_UNKNOWN_TARGET)

        base_hash = str(base_row["file_hash"] or "").lower()
        target_hash = str(target_row["file_hash"] or "").lower()
        if not base_hash or base_hash == target_hash:
            raise HTTPException(status_code=400, detail=MESSAGE_SAME_HASH)

        verified_drawing = _require_same_drawing(base_row, target_row)

        base_rows, base_truncated = store.evidence_nodes_for_document(
            project_id,
            int(base_row["id"]),
            base_hash,
            MAX_SCANNED_NODES_PER_DOCUMENT,
        )
        target_rows, target_truncated = store.evidence_nodes_for_document(
            project_id,
            int(target_row["id"]),
            target_hash,
            MAX_SCANNED_NODES_PER_DOCUMENT,
        )
        claim_rows, claims_truncated = store.claims_for_project(
            project_id, MAX_SCANNED_CLAIMS
        )
        document_names = store.document_names_by_hash(project_id, MAX_SCANNED_DOCUMENTS)

    warnings: list[str] = []
    if base_truncated or target_truncated:
        warnings.append(WARNING_NODES_TRUNCATED)
    if claims_truncated:
        warnings.append(WARNING_CLAIMS_TRUNCATED)
    if not base_rows:
        # An empty base is not "everything was deleted". Nodes written with an
        # ingest key are updated in place on re-ingest, so a base revision that
        # was superseded leaves no rows of its own behind. Reporting that as a
        # full set of removals would invent a finding, so nothing is classified.
        warnings.append(WARNING_BASE_NO_EVIDENCE)
    if not target_rows:
        warnings.append(WARNING_TARGET_NO_EVIDENCE)

    if not base_rows or not target_rows:
        relationship = RELATIONSHIP_NO_EVIDENCE
        records: list[ChangeRecord] = []
        mapping = map_affected_claims(records, (), document_names)
    else:
        relationship = (
            RELATIONSHIP_SAME_DRAWING if verified_drawing else RELATIONSHIP_UNVERIFIED
        )
        if not verified_drawing:
            warnings.append(WARNING_DRAWING_UNVERIFIED)
        records = diff_nodes(base_rows, target_rows)
        mapping = map_affected_claims(
            records,
            ((str(row["claim_id"]), row["evidence"]) for row in claim_rows),
            document_names,
        )

    if mapping.unverified_references:
        warnings.append(WARNING_UNVERIFIED_REFERENCES)
    if mapping.malformed_claims:
        warnings.append(WARNING_MALFORMED_CLAIMS)
    if mapping.unassociated_claims:
        warnings.append(WARNING_UNASSOCIATED_CLAIMS)

    items: list[RevisionChangeDTO] = []
    for record in records[:MAX_DIFF_ITEMS]:
        claim_ids, claims_omitted = _clip_list(
            mapping.matches.get(record.identity_id, ()),
            MAX_DIFF_AFFECTED_CLAIMS,
            MAX_DIFF_CLAIM_ID_CHARS,
        )
        fields, _ = _clip_list(record.changed_fields, MAX_DIFF_FIELDS)
        reason, _ = _clip(record.reason, MAX_DIFF_REASON_CHARS)
        items.append(
            RevisionChangeDTO(
                identity_id=record.identity_id,
                status=record.status,
                group_size=record.group_size,
                base=_evidence_dto(record.base) if record.base else None,
                target=_evidence_dto(record.target) if record.target else None,
                changed_fields=fields,
                affected_claim_ids=claim_ids,
                affected_claims_omitted=claims_omitted,
                reason=reason,
            )
        )

    return RevisionDiffResponse(
        project_id=project_id,
        base=_document_dto(
            base_row, evidence_rows=len(base_rows), evidence_truncated=base_truncated
        ),
        target=_document_dto(
            target_row,
            evidence_rows=len(target_rows),
            evidence_truncated=target_truncated,
        ),
        relationship=relationship,
        counts=_counts(records),
        items=items,
        items_truncated=len(records) > MAX_DIFF_ITEMS,
        items_omitted=max(len(records) - MAX_DIFF_ITEMS, 0),
        checked_claims=mapping.checked_claims,
        malformed_claims=mapping.malformed_claims,
        unverified_claim_references=mapping.unverified_references,
        unassociated_claims=mapping.unassociated_claims,
        warnings=warnings[:MAX_DIFF_WARNINGS],
    )


def register_revision_routes(
    api: APIRouter, *, store: QSStore, sessions: SessionLock
) -> None:
    """Attach the read-only revision diff route to an authenticated router.

    ``api`` is the router the gateway already mounts with a bearer-token
    dependency, so this route inherits authentication by construction rather
    than restating it.
    """

    @api.get(
        "/projects/{project_id}/revisions/diff/{base_document_id}/{target_document_id}",
        response_model=RevisionDiffResponse,
    )
    def revision_diff(
        project_id: int, base_document_id: int, target_document_id: int
    ) -> RevisionDiffResponse:
        # The session lock is held for the whole comparison, not just the
        # lookup: the store is one connection shared by every request, and its
        # own contract requires callers to serialise access to it.
        with sessions.locked():
            return build_revision_diff(
                store, project_id, base_document_id, target_document_id
            )



