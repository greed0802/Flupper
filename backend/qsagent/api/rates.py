"""Rate proposal route (Phase 5C): read-only, authenticated, bounded.

Transport only
--------------
This module resolves the rate rows a caller named, reads their payloads and the
project's documents under one snapshot, and renders what
:mod:`qsagent.rates.normalize` decided. It writes nothing, journals nothing,
escalates nothing, converts no currency and touches no quantity claim. A
proposal is an answer to a question about stored evidence, not a change to it.

Nothing the caller sends is trusted as a rate
---------------------------------------------
The request carries node ids and nothing else. There is no field for an amount,
a unit, a currency, a category, a hash or a path, so a caller cannot assert what
a rate is or nominate the proof for one. Every number, unit and file name in the
response was read out of the store and resolved against ``documents``.

Why a proposal and not a write
------------------------------
A normalized rate that persisted here would be a number a quantity claim could
later be built on, and this phase has no approval path, no CheckMate rule and no
audit entry for that. So the arithmetic is exposed and the result is returned,
and storing a rate stays a decision for a phase that can carry its approval and
its rollback.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import date, datetime, timezone
from typing import Protocol, Sequence

from fastapi import APIRouter, HTTPException

from ..rates import (
    NormalizedRate,
    RateNodeInput,
    SourceDocument,
    normalize_rate_nodes,
)
from ..rates.normalize import WARNING_DOCUMENTS_TRUNCATED
from ..storage import QSStore, read_snapshot
from .contracts import (
    MAX_RATE_NODE_IDS,
    MAX_RATE_SCANNED_DOCUMENTS,
    MAX_RATE_TEXT_CHARS,
    MAX_RATE_WARNINGS,
    RateProposalDTO,
    RateProposalRequest,
    RateProposalResponse,
)
# One text for one condition. The project lookup is the same lookup the revision
# diff makes, so it fails with the same sentence rather than a second wording of
# it that a client would have to learn separately.
from .revisions import MESSAGE_UNKNOWN_PROJECT

#: Every reason a named id resolves to nothing shares this answer - an id that
#: never existed, an id belonging to another project, and an id whose node is not
#: a rate. A caller who could tell those apart would have an oracle for the
#: existence of other projects' rows.
MESSAGE_UNKNOWN_RATE_NODE = "unknown rate node for this project"


class SessionLock(Protocol):
    """The one thing this module needs from the gateway's session registry.

    Declared structurally so the router can be registered without importing
    ``qsagent.api.main``, which imports this module. ``locked()`` takes no
    argument: the gateway holds one app-wide lock because the store is one
    SQLite connection shared by every project, and a per-project lock would let
    two projects interleave commits on it.
    """

    def locked(self) -> AbstractContextManager[None]: ...


def _utc_today() -> date:
    """The day ages are measured against.

    UTC rather than local time: a proposal's ``source_age_days`` should not
    change because the operator's machine crossed midnight.
    """
    return datetime.now(timezone.utc).date()


def _node_input(row) -> RateNodeInput:
    """One stored row as the normalizer's input.

    Columns are passed through as stored, including the null ones: the
    normalizer distinguishes an absent locator from an empty one and needs to
    see which it has.
    """
    return RateNodeInput(
        node_id=int(row["id"]),
        label=str(row["label"]),
        payload=str(row["payload"]),
        file_hash=row["file_hash"],
        sheet=row["sheet"],
        page=row["page"],
        bbox=row["bbox"],
        raw_text=row["raw_text"],
    )


def _source_document(row) -> SourceDocument:
    return SourceDocument(
        document_id=int(row["id"]),
        file_name=str(row["file_name"]),
        file_hash=str(row["file_hash"]),
    )


def _proposal_dto(proposal: NormalizedRate) -> RateProposalDTO:
    """Render one proposal, keeping every string inside its bound.

    The two display strings are cut rather than refused, the same way the
    revision diff treats a label: ``label`` and ``documents.file_name`` are
    unbounded ``TEXT`` columns, and a store that holds a long one should still
    produce a response rather than a validation failure. Nothing that carries
    meaning is cut - amounts, units, dates and hashes are bounded by the
    normalizer before they get here, so a bound can never be the thing that
    changes a rate.
    """
    return RateProposalDTO(
        node_id=proposal.node_id,
        label=proposal.label[:MAX_RATE_TEXT_CHARS],
        status=proposal.status,
        confidence=proposal.confidence,
        reason=proposal.reason,
        source_document_id=proposal.source_document_id,
        source_file_name=(
            proposal.source_file_name[:MAX_RATE_TEXT_CHARS]
            if proposal.source_file_name is not None
            else None
        ),
        source_file_hash=proposal.source_file_hash,
        original_amount=proposal.original_amount,
        original_unit=proposal.original_unit,
        normalized_amount=proposal.normalized_amount,
        normalized_unit=proposal.normalized_unit,
        currency=proposal.currency,
        rate_category=proposal.rate_category,
        effective_date=proposal.effective_date,
        source_age_days=proposal.source_age_days,
        locator_present=proposal.locator_present,
        duplicate_quote_count=proposal.duplicate_quote_count,
        warnings=list(proposal.warnings[:MAX_RATE_WARNINGS]),
    )


def build_rate_proposals(
    store: QSStore,
    project_id: int,
    node_ids: Sequence[int],
    *,
    today: date,
) -> RateProposalResponse:
    """Answer one request. Call inside ``sessions.locked()``.

    ``node_ids`` is already deduplicated and bounded by the request model, so
    the set the store is asked for is the set the response answers: one proposal
    per id, in the order asked. An id that does not resolve under this project
    fails the whole request rather than being skipped, because a shorter answer
    than the question is the kind of partial result a reader would not notice.
    """
    with read_snapshot(store):
        if store.get_project(project_id) is None:
            raise HTTPException(status_code=404, detail=MESSAGE_UNKNOWN_PROJECT)

        rows = store.rate_nodes(project_id, node_ids)
        if {int(row["id"]) for row in rows} != set(node_ids):
            raise HTTPException(status_code=404, detail=MESSAGE_UNKNOWN_RATE_NODE)

        document_rows, documents_truncated = store.documents_for_project(
            project_id, MAX_RATE_SCANNED_DOCUMENTS
        )

    by_id = {int(row["id"]): row for row in rows}
    proposals = normalize_rate_nodes(
        [_node_input(by_id[int(node_id)]) for node_id in node_ids],
        documents=[_source_document(row) for row in document_rows],
        today=today,
    )

    warnings: list[str] = []
    if documents_truncated:
        # A source that could not be resolved might be past the ceiling rather
        # than absent. Saying so is what keeps a bound from being read as a
        # finding about the project.
        warnings.append(WARNING_DOCUMENTS_TRUNCATED)

    items = [_proposal_dto(proposal) for proposal in proposals]
    return RateProposalResponse(
        project_id=int(project_id),
        reference_date=today.isoformat(),
        proposals=items[:MAX_RATE_NODE_IDS],
        normalized=sum(1 for item in items if item.status == "normalized"),
        unresolved=sum(1 for item in items if item.status != "normalized"),
        warnings=warnings[:MAX_RATE_WARNINGS],
    )


def register_rate_routes(
    api: APIRouter, *, store: QSStore, sessions: SessionLock
) -> None:
    """Attach the read-only rate proposal route to an authenticated router.

    ``api`` is the router the gateway already mounts with a bearer-token
    dependency, so this route inherits authentication by construction rather
    than restating it.
    """

    @api.post(
        "/projects/{project_id}/rates/proposals/normalize",
        response_model=RateProposalResponse,
    )
    def normalize_rates(
        project_id: int, request: RateProposalRequest
    ) -> RateProposalResponse:
        # The lock is held for the whole read, not just the lookup: the store is
        # one connection shared by every request, and its own contract requires
        # callers to serialise their transactions.
        with sessions.locked():
            return build_rate_proposals(
                store, project_id, request.node_ids, today=_utc_today()
            )
