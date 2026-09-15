"""Revision intelligence (Phase 5B): read-only, deterministic evidence diffs.

Two modules, no third: :mod:`~qsagent.revisions.canonical` defines what makes
two stored evidence rows the same row, and :mod:`~qsagent.revisions.diff`
classifies two revisions against that identity. Nothing here writes, and
nothing here reads a store - the caller supplies rows it has already read under
its own lock.
"""

from .canonical import (
    FLAG_INSUFFICIENT_EVIDENCE,
    FLAG_MALFORMED_EVIDENCE,
    ClaimReference,
    StoredEvidence,
    canonical_hash,
    canonical_json,
    canonical_payload,
    normalize_bbox,
    normalize_identifier,
    normalize_number,
    normalize_text,
    normalize_unit,
)
from .diff import (
    ADDED,
    AFFECTING_STATUSES,
    AMBIGUOUS,
    CHANGED,
    REMOVED,
    STATUS_ORDER,
    STATUS_VALUES,
    UNCHANGED,
    UNRESOLVED,
    ChangeRecord,
    ClaimMapping,
    diff_nodes,
    map_affected_claims,
)

__all__ = [
    "ADDED",
    "AFFECTING_STATUSES",
    "AMBIGUOUS",
    "CHANGED",
    "REMOVED",
    "STATUS_ORDER",
    "STATUS_VALUES",
    "UNCHANGED",
    "UNRESOLVED",
    "FLAG_INSUFFICIENT_EVIDENCE",
    "FLAG_MALFORMED_EVIDENCE",
    "ChangeRecord",
    "ClaimMapping",
    "ClaimReference",
    "StoredEvidence",
    "canonical_hash",
    "canonical_json",
    "canonical_payload",
    "diff_nodes",
    "map_affected_claims",
    "normalize_bbox",
    "normalize_identifier",
    "normalize_number",
    "normalize_text",
    "normalize_unit",
]
