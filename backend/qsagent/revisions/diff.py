"""Revision comparison over stored evidence (Phase 5B).

Pure functions: stored rows in, change records out. No store access, no
writing, no CheckMate, no approval. A diff decides nothing about what a
quantity means - it reports which stored evidence rows are the same, which
moved, and which the store cannot speak to.

Comparison categories
---------------------
``added``      present in the target revision only.
``removed``    present in the base revision only.
``changed``    matched, with at least one comparable field different.
``unchanged``  matched, with every comparable field equal.
``ambiguous``  matched by footprint, but the store cannot say which row
               corresponds to which, so neither pairing nor a change claim is
               made.
``unresolved`` present, but the row itself is insufficient evidence - nothing
               comparable stored on it, or stored JSON that does not decode.

Pairing is never by list position. Two rules are used, in this order: a single
row on each side of a footprint pairs trivially, and otherwise every row on
both sides must carry a stored ``ingest_key`` that is unique within its group.
``ingest_key`` is not derived from the file hash - ``ingest/cli._ikey``
composes it from the project, report type, sheet, operation group and a
within-run occurrence - which is exactly what lets it identify the same logical
row across two exports. When neither rule applies, every row in the group is
reported ambiguous.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from .canonical import ClaimReference, StoredEvidence, normalize_identifier

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

ADDED = "added"
REMOVED = "removed"
CHANGED = "changed"
UNCHANGED = "unchanged"
AMBIGUOUS = "ambiguous"
UNRESOLVED = "unresolved"

#: Presentation order, and the order records are returned in.
STATUS_ORDER = {
    ADDED: 0,
    REMOVED: 1,
    CHANGED: 2,
    UNCHANGED: 3,
    AMBIGUOUS: 4,
    UNRESOLVED: 5,
}

STATUS_VALUES = tuple(STATUS_ORDER)

REASON_AMBIGUOUS_IDENTITY = "ambiguous_identity"

#: Fields compared on a matched pair. ``file_hash`` is deliberately absent: it
#: is provenance, and a re-export changes it on every row. ``payload`` is
#: expanded per key by :func:`_changed_fields`, so it is not compared whole.
CONTENT_FIELDS = ("label", "revision", "raw_text", "bbox", "payload")

#: The statuses that can make a claim's evidence stale. An unchanged row
#: changed nothing; an added row was never cited by the base revision; an
#: ambiguous or unresolved row is not a classification worth propagating.
AFFECTING_STATUSES = frozenset({CHANGED, REMOVED})


@dataclass(frozen=True)
class ChangeRecord:
    """One classified footprint. Internal - the API layer renders it."""

    status: str
    identity_id: str
    base: Optional[StoredEvidence]
    target: Optional[StoredEvidence]
    changed_fields: tuple[str, ...]
    reason: Optional[str]
    group_size: int = 1


@dataclass(frozen=True)
class ClaimMapping:
    """Which claims cite evidence that changed, and what could not be checked."""

    #: ``identity_id`` -> affected claim ids, in the order the claims were read.
    matches: Mapping[str, tuple[str, ...]]
    #: Claims that cite the base document but whose references could not be
    #: tied to a specific evidence row, because no row carries a locator. They
    #: are counted rather than associated: see
    #: :meth:`~qsagent.revisions.canonical.ClaimReference.matches_node`.
    unassociated_claims: int
    #: References whose file hash has no ``documents`` row, so the filename
    #: lineage could not be verified and no match was attempted.
    unverified_references: int
    #: Claims whose stored evidence column is not a usable JSON array.
    malformed_claims: int
    #: Claims whose evidence array parsed and was checked.
    checked_claims: int


# --------------------------------------------------------------------------
# Node comparison
# --------------------------------------------------------------------------


def _group(
    views: Sequence[StoredEvidence],
) -> dict[tuple[Any, ...], list[StoredEvidence]]:
    grouped: dict[tuple[Any, ...], list[StoredEvidence]] = {}
    for view in views:
        grouped.setdefault(view.pairing_key, []).append(view)
    return grouped


def _by_ingest_key(
    views: Sequence[StoredEvidence],
) -> tuple[dict[str, StoredEvidence], bool]:
    """Index by ingest key, reporting whether any key repeated."""
    indexed: dict[str, StoredEvidence] = {}
    duplicated = False
    for view in views:
        key = view.ingest_key or ""
        if key in indexed:
            duplicated = True
            continue
        indexed[key] = view
    return indexed, duplicated


def _pair(
    base: Sequence[StoredEvidence], target: Sequence[StoredEvidence]
) -> tuple[
    list[tuple[StoredEvidence, StoredEvidence]],
    list[StoredEvidence],
    list[StoredEvidence],
]:
    """Pair rows that describe the same thing, or refuse to pair at all."""
    if len(base) == 1 and len(target) == 1:
        return [(base[0], target[0])], [], []

    if all(view.ingest_key for view in base) and all(view.ingest_key for view in target):
        base_index, base_dupes = _by_ingest_key(base)
        target_index, target_dupes = _by_ingest_key(target)
        if not base_dupes and not target_dupes:
            shared = sorted(set(base_index) & set(target_index))
            pairs = [(base_index[key], target_index[key]) for key in shared]
            left_base = [base_index[key] for key in base_index if key not in target_index]
            left_target = [
                target_index[key] for key in target_index if key not in base_index
            ]
            return pairs, left_base, left_target

    return [], list(base), list(target)


def _changed_fields(base: StoredEvidence, target: StoredEvidence) -> tuple[str, ...]:
    """Name every comparable field that differs, or nothing if none does."""
    base_content = base.content
    target_content = target.content

    fields = [
        name
        for name in CONTENT_FIELDS
        if name != "payload" and base_content[name] != target_content[name]
    ]

    base_payload = base_content["payload"] or {}
    target_payload = target_content["payload"] or {}
    for key in sorted(set(base_payload) | set(target_payload)):
        if base_payload.get(key) != target_payload.get(key):
            fields.append(f"payload.{key}")
    return tuple(fields)


def _paired(
    identity_id: str, base: StoredEvidence, target: StoredEvidence
) -> ChangeRecord:
    if not base.reliable or not target.reliable:
        reason = base.reason or target.reason
        return ChangeRecord(UNRESOLVED, identity_id, base, target, (), reason, 2)
    fields = _changed_fields(base, target)
    status = CHANGED if fields else UNCHANGED
    return ChangeRecord(status, identity_id, base, target, fields, None, 2)


def _single(
    status: str,
    identity_id: str,
    base: Optional[StoredEvidence],
    target: Optional[StoredEvidence],
    group_size: int,
) -> ChangeRecord:
    view = base if base is not None else target
    if view is not None and not view.reliable:
        return ChangeRecord(
            UNRESOLVED, identity_id, base, target, (), view.reason, group_size
        )
    return ChangeRecord(status, identity_id, base, target, (), None, group_size)


def _classify_group(
    identity_id: str, base: Sequence[StoredEvidence], target: Sequence[StoredEvidence]
) -> list[ChangeRecord]:
    if not base:
        return [_single(ADDED, identity_id, None, view, len(target)) for view in target]
    if not target:
        return [_single(REMOVED, identity_id, view, None, len(base)) for view in base]

    pairs, left_base, left_target = _pair(base, target)

    if not pairs and left_base and left_target:
        size = len(left_base) + len(left_target)
        return [
            ChangeRecord(
                AMBIGUOUS, identity_id, view, None, (), REASON_AMBIGUOUS_IDENTITY, size
            )
            for view in left_base
        ] + [
            ChangeRecord(
                AMBIGUOUS, identity_id, None, view, (), REASON_AMBIGUOUS_IDENTITY, size
            )
            for view in left_target
        ]

    records = [_paired(identity_id, b, t) for b, t in pairs]
    size = len(base) + len(target)
    records.extend(_single(REMOVED, identity_id, view, None, size) for view in left_base)
    records.extend(_single(ADDED, identity_id, None, view, size) for view in left_target)
    return records


def diff_nodes(
    base_rows: Iterable[Mapping[str, Any]], target_rows: Iterable[Mapping[str, Any]]
) -> list[ChangeRecord]:
    """Classify two sets of evidence rows, deterministically ordered.

    Same rows in, same records out, in the same order - the ordering comes from
    a hash of the pairing key and the stored row id, never from the order
    SQLite happened to return.
    """
    base_groups = _group([StoredEvidence.from_row(row) for row in base_rows])
    target_groups = _group([StoredEvidence.from_row(row) for row in target_rows])

    identity: dict[tuple[Any, ...], str] = {}
    for groups in (base_groups, target_groups):
        for key, group in groups.items():
            identity[key] = group[0].identity_id

    records: list[ChangeRecord] = []
    for key in sorted(identity, key=lambda k: identity[k]):
        base = sorted(base_groups.get(key, ()), key=lambda v: v.sort_key)
        target = sorted(target_groups.get(key, ()), key=lambda v: v.sort_key)
        records.extend(_classify_group(identity[key], base, target))

    return sorted(records, key=lambda r: (STATUS_ORDER[r.status], r.identity_id))


# --------------------------------------------------------------------------
# Affected claims
# --------------------------------------------------------------------------


def _reference_index_key(
    file_hash: Optional[str],
    drawing_no: Any,
    sheet: Any,
    zone: Any,
    page: Optional[int],
) -> tuple[Any, ...]:
    """Cheap lookup key shared by a node and a claim reference.

    ``node_type`` is not part of it: an ``EvidenceRef`` does not carry one, so
    a reference may legitimately match nodes of different types. The full,
    exact comparison still runs afterwards; this key only narrows candidates.
    """
    return (
        file_hash,
        normalize_identifier(drawing_no),
        normalize_identifier(sheet),
        normalize_identifier(zone),
        page,
    )


def _parse_references(raw: Any) -> tuple[list[ClaimReference], bool]:
    """Decode a claim's stored evidence column.

    Returns the usable references and whether the stored value was malformed.
    A valid array whose entries carry no source hash is not malformed - it is
    empty of usable references, which is a different report.
    """
    decoded: Any = raw
    if isinstance(raw, (str, bytes)):
        try:
            decoded = json.loads(raw)
        except ValueError:
            return [], True
    if not isinstance(decoded, list):
        return [], True
    references = [
        reference
        for reference in (ClaimReference.from_json(entry) for entry in decoded)
        if reference is not None
    ]
    return references, False


def map_affected_claims(
    records: Sequence[ChangeRecord],
    claims: Iterable[tuple[str, Any]],
    document_names: Mapping[str, str],
) -> ClaimMapping:
    """Map changed or removed evidence onto the claims that cite it.

    A claim is reported as affected only when one of its stored references
    matches a base-side evidence row exactly, on every field the row carries,
    with the row's file name verified through the document that owns that
    hash. Two things are counted rather than guessed at: a reference whose hash
    has no document row, and a claim that cites the changed document but whose
    references cannot be tied to any specific row because no row carries a
    locator. Both are reported so the absence of an association is visible
    instead of silent.

    Records that are not in :data:`AFFECTING_STATUSES`, and records whose base
    row is not reliable, contribute nothing: a change that was never
    established cannot make a claim stale.
    """
    candidates: dict[tuple[Any, ...], list[StoredEvidence]] = {}
    citing_hashes: set[str] = set()
    for record in records:
        base = record.base
        if base is None or not base.reliable:
            continue
        if record.status not in AFFECTING_STATUSES:
            continue
        if base.file_hash:
            citing_hashes.add(base.file_hash)
        key = _reference_index_key(
            base.file_hash, base.drawing_no, base.sheet, base.zone, base.page
        )
        candidates.setdefault(key, []).append(base)

    matches: dict[str, list[str]] = {}
    unverified = 0
    malformed_claims = 0
    checked_claims = 0
    unassociated = 0

    for claim_id, raw_evidence in claims:
        references, malformed = _parse_references(raw_evidence)
        if malformed:
            malformed_claims += 1
            continue
        if not references:
            # Nothing usable to check and nothing to report: the claim carries
            # no reference that could name a document at all.
            continue
        checked_claims += 1
        cites_base = False
        associated = False
        for reference in references:
            file_name = document_names.get(reference.file_hash or "")
            if file_name is None:
                unverified += 1
                continue
            if reference.file_hash in citing_hashes:
                cites_base = True
            key = _reference_index_key(
                reference.file_hash,
                reference.drawing_no,
                reference.sheet,
                reference.zone,
                reference.page,
            )
            for node in candidates.get(key, ()):
                if not reference.matches_node(node, file_name):
                    continue
                associated = True
                found = matches.setdefault(node.identity_id, [])
                if claim_id not in found:
                    found.append(claim_id)
        if cites_base and not associated:
            unassociated += 1

    return ClaimMapping(
        matches={key: tuple(value) for key, value in matches.items()},
        unassociated_claims=unassociated,
        unverified_references=unverified,
        malformed_claims=malformed_claims,
        checked_claims=checked_claims,
    )


