"""Evidence-backed rate normalization (Phase 5C).

Read-only. The core is :mod:`qsagent.rates.normalize`; the authenticated route
that exposes it lives in :mod:`qsagent.api.rates`. Nothing in this package
writes, journals, escalates a rate or touches a quantity claim.
"""

from .normalize import (
    ALLOWED_PAYLOAD_KEYS,
    CANONICAL_RATE_UNITS,
    CATEGORY_UNITS,
    CONFIDENCE_EXACT,
    CONFIDENCE_INFERRED,
    CONFIDENCE_UNRESOLVED,
    CURRENCY_AUD,
    MAX_RATE_AGE_DAYS,
    RATE_CATEGORIES,
    STATUS_NORMALIZED,
    STATUS_UNRESOLVED,
    UNRESOLVED_REASONS,
    WARNINGS,
    NormalizedRate,
    RateNodeInput,
    SourceDocument,
    apply_duplicate_quotes,
    index_documents,
    normalize_rate_node,
    normalize_rate_nodes,
)

__all__ = [
    "ALLOWED_PAYLOAD_KEYS",
    "CANONICAL_RATE_UNITS",
    "CATEGORY_UNITS",
    "CONFIDENCE_EXACT",
    "CONFIDENCE_INFERRED",
    "CONFIDENCE_UNRESOLVED",
    "CURRENCY_AUD",
    "MAX_RATE_AGE_DAYS",
    "RATE_CATEGORIES",
    "STATUS_NORMALIZED",
    "STATUS_UNRESOLVED",
    "UNRESOLVED_REASONS",
    "WARNINGS",
    "NormalizedRate",
    "RateNodeInput",
    "SourceDocument",
    "apply_duplicate_quotes",
    "index_documents",
    "normalize_rate_node",
    "normalize_rate_nodes",
]
