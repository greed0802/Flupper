"""Evidence-backed rate normalization (Phase 5C): deterministic and read-only.

What this module is
-------------------
One stored ``rate`` evidence row becomes one rate proposal. The row's payload
supplies the amount, unit, currency, category and effective date; the store
supplies the source document, and every value in the result is either read from
one of those two places or derived from them by arithmetic this module states
outright. Nothing here writes, journals, calls a model or reaches the network.

The rule this module exists to keep
-----------------------------------
*No rate is fabricated.* Four things follow from that, and each is enforced
rather than promised:

* an amount is only ever the number the row already carried. There is no market
  lookup, no index, no interpolation and no escalation factor, because none of
  those has a verified source in this store. A row missing anything it needs
  comes back ``unresolved`` with a reason instead of a number;
* the source document is resolved from the store, never from the payload. The
  hash and file name in a proposal were read back out of ``documents``, so a
  caller cannot nominate proof by writing a hash into a payload;
* units, currencies and categories are closed sets. An unrecognised spelling is
  reported, not mapped onto something plausible;
* everything is decided in the fixed order listed below, so the same row always
  yields the same reason and the same digits.

Determinism
-----------
Money is :class:`decimal.Decimal` from parse to serialization and is quantized
once, to two places, with ``ROUND_HALF_UP``. No ``float`` touches an amount on
the way in or the way out, and the result is rendered as a decimal string rather
than a JSON number so a reader cannot re-round it differently.

Only one input is not in the database: the current date, used to report how old
a rate is. It is passed in as ``today`` and echoed by the response, so an age is
recomputable from the response alone.

Evaluation order
----------------
``payload shape -> source document -> provenance kind -> amount -> unit and
category -> unit/category compatibility -> currency -> effective date``.

The first step that fails decides the reason and the rest are not evaluated. A
row with several problems therefore reports one reason, deterministically,
instead of an arbitrary one.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Optional

from ..contracts.evidence import SHA256_RE, Unit
from ..revisions.canonical import normalize_unit

# --------------------------------------------------------------------------
# Outcome vocabulary
# --------------------------------------------------------------------------
STATUS_NORMALIZED = "normalized"
STATUS_UNRESOLVED = "unresolved"

#: ``exact`` needs a machine export *and* a locator. ``inferred`` is still a
#: number read from the row - it is the *proof* that is weaker, not the amount,
#: which is why it is a confidence and not a refusal. ``unresolved`` means there
#: is no number to be confident about.
CONFIDENCE_EXACT = "exact"
CONFIDENCE_INFERRED = "inferred"
CONFIDENCE_UNRESOLVED = "unresolved"

#: The only currency this platform stores a verified source for. A payload
#: spelling this as ``"$"`` or ``"A$"`` is not accepted as AUD: inferring a
#: currency from a symbol is exactly the kind of guess that fabricates a rate.
CURRENCY_AUD = "AUD"

#: Rate categories, and the canonical units each may be quoted in. A category
#: outside this set is reported, never coerced onto a near neighbour.
RATE_CATEGORIES: tuple[str, ...] = (
    "plant_time",
    "labour_time",
    "material_unit",
    "volume_cart_away",
    "subcontract_lumpsum",
)

CATEGORY_UNITS: dict[str, frozenset[str]] = {
    "plant_time": frozenset({Unit.HOUR.value}),
    "labour_time": frozenset({Unit.HOUR.value}),
    "material_unit": frozenset(
        {
            Unit.M.value,
            Unit.M2.value,
            Unit.M3.value,
            Unit.MM.value,
            Unit.KG.value,
            Unit.TONNE.value,
            Unit.EACH.value,
            Unit.ITEM.value,
            Unit.LITRE.value,
        }
    ),
    "volume_cart_away": frozenset({Unit.M3.value}),
    "subcontract_lumpsum": frozenset({Unit.EACH.value, Unit.ITEM.value}),
}

#: Every canonical unit a rate may be quoted in, derived from the categories
#: above so the two cannot disagree.
CANONICAL_RATE_UNITS: frozenset[str] = frozenset(
    unit for units in CATEGORY_UNITS.values() for unit in units
)

# --------------------------------------------------------------------------
# Payload schema - the complete set of keys a rate node may carry
# --------------------------------------------------------------------------
#: A rate payload is read against this closed set. An unexpected key is a
#: refusal rather than an invitation to look around: a field this module does
#: not know about could be the one that changes the number, and treating it as
#: noise would normalise a rate on an incomplete reading of the row.
#:
#: ``sheet``, ``page``, ``raw_text`` and ``bbox`` are the locator, read only
#: when the corresponding column is null (see :func:`_locator`). ``source_file``
#: is accepted and deliberately ignored: it is a path from someone else's
#: machine, it is never evidence of a rate, and it never leaves this process.
ALLOWED_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {
        "amount",
        "unit",
        "currency",
        "rate_category",
        "effective_date",
        "doc_id",
        "file_hash",
        "provenance",
        "sheet",
        "page",
        "raw_text",
        "bbox",
        "source_file",
    }
)

#: How a row declares where it came from. Required: without it there is no way
#: to tell a machine export from something a person typed, and a rate that
#: cannot say which it is cannot claim to be verified.
PROVENANCE_MACHINE_EXPORT = "machine_export"
PROVENANCE_HAND_PREPARED = "hand_prepared"
PROVENANCE_KINDS: tuple[str, ...] = (
    PROVENANCE_MACHINE_EXPORT,
    PROVENANCE_HAND_PREPARED,
)

# --------------------------------------------------------------------------
# Money and dates
# --------------------------------------------------------------------------
#: Two places, half-up, decided once. ``1.005`` is ``0.01`` here because it is
#: ``Decimal("1.005")`` and not the binary float that sits a hair below it.
AMOUNT_EXPONENT = Decimal("0.01")
AMOUNT_QUANTUM_PLACES = 2

#: Bounds on an accepted amount. A rate outside them is not a rate this
#: platform is willing to name, and rejecting it keeps the rendered string
#: short enough that no response bound can ever be the thing that cuts a number
#: a reader would otherwise have taken at face value.
MIN_RATE_AMOUNT = Decimal("0.01")
MAX_RATE_AMOUNT = Decimal("1000000000")
MAX_AMOUNT_CHARS = 64
MAX_DATE_CHARS = 10
MAX_HASH_CHARS = 64

#: A rate older than this is still a rate - it is reported as stale rather than
#: escalated, because escalation needs a source this store does not have.
MAX_RATE_AGE_DAYS = 365

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# --------------------------------------------------------------------------
# Reasons - why a row has no number
# --------------------------------------------------------------------------
REASON_MALFORMED_PAYLOAD = "malformed_payload"
REASON_UNKNOWN_PAYLOAD_KEYS = "unknown_payload_keys"
#: The row names no source at all: no document id and no file hash.
REASON_MISSING_SOURCE = "missing_source"
REASON_INVALID_DOCUMENT_ID = "invalid_document_id"
REASON_INVALID_SOURCE_HASH = "invalid_source_hash"
REASON_UNVERIFIED_SOURCE = "unverified_source"
REASON_EVIDENCE_UNAVAILABLE = "evidence_unavailable"
REASON_SOURCE_MISMATCH = "source_mismatch"
#: The row does not say whether a machine or a person produced it.
REASON_MISSING_PROVENANCE = "missing_provenance"
REASON_UNSUPPORTED_PROVENANCE = "unsupported_provenance"
REASON_MISSING_AMOUNT = "missing_amount"
REASON_INVALID_AMOUNT = "invalid_amount"
REASON_MISSING_UNIT = "missing_unit"
REASON_UNSUPPORTED_UNIT = "unsupported_unit"
REASON_MISSING_CATEGORY = "missing_category"
REASON_UNSUPPORTED_CATEGORY = "unsupported_category"
REASON_UNIT_CATEGORY_MISMATCH = "unit_category_mismatch"
REASON_MISSING_CURRENCY = "missing_currency"
REASON_UNSUPPORTED_CURRENCY = "unsupported_currency"
REASON_MISSING_EFFECTIVE_DATE = "missing_effective_date"
REASON_INVALID_EFFECTIVE_DATE = "invalid_effective_date"

UNRESOLVED_REASONS: tuple[str, ...] = (
    REASON_MALFORMED_PAYLOAD,
    REASON_UNKNOWN_PAYLOAD_KEYS,
    REASON_MISSING_SOURCE,
    REASON_INVALID_DOCUMENT_ID,
    REASON_INVALID_SOURCE_HASH,
    REASON_UNVERIFIED_SOURCE,
    REASON_EVIDENCE_UNAVAILABLE,
    REASON_SOURCE_MISMATCH,
    REASON_MISSING_PROVENANCE,
    REASON_UNSUPPORTED_PROVENANCE,
    REASON_MISSING_AMOUNT,
    REASON_INVALID_AMOUNT,
    REASON_MISSING_UNIT,
    REASON_UNSUPPORTED_UNIT,
    REASON_MISSING_CATEGORY,
    REASON_UNSUPPORTED_CATEGORY,
    REASON_UNIT_CATEGORY_MISMATCH,
    REASON_MISSING_CURRENCY,
    REASON_UNSUPPORTED_CURRENCY,
    REASON_MISSING_EFFECTIVE_DATE,
    REASON_INVALID_EFFECTIVE_DATE,
)

# --------------------------------------------------------------------------
# Warnings - what a number does not tell you
# --------------------------------------------------------------------------
#: The source is verified but the row carries no locator, so the number can be
#: reproduced from the store without being tied to a spot on a page.
WARNING_LOCATOR_UNAVAILABLE = "locator_unavailable"
#: Same idea for the sheet or page reference.
WARNING_SHEET_OR_PAGE_UNAVAILABLE = "sheet_or_page_unavailable"
#: A person prepared this row, so it is not a machine export.
WARNING_MANUAL_PROVENANCE = "manual_provenance"
#: Older than :data:`MAX_RATE_AGE_DAYS`. No escalation is applied.
WARNING_STALE_SOURCE = "stale_source"
#: Another node in the same request quotes the same source and category.
WARNING_DUPLICATE_QUOTE = "duplicate_source_quote"
#: Emitted by the route, not here: the document lookup hit its ceiling, so a
#: source that could not be resolved might simply be past the bound.
WARNING_DOCUMENTS_TRUNCATED = "source_documents_truncated"

WARNINGS: tuple[str, ...] = (
    WARNING_LOCATOR_UNAVAILABLE,
    WARNING_SHEET_OR_PAGE_UNAVAILABLE,
    WARNING_MANUAL_PROVENANCE,
    WARNING_STALE_SOURCE,
    WARNING_DUPLICATE_QUOTE,
    WARNING_DOCUMENTS_TRUNCATED,
)


# --------------------------------------------------------------------------
# Inputs and result
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class SourceDocument:
    """One ``documents`` row, as far as a rate proposal needs to know it."""

    document_id: int
    file_name: str
    file_hash: str


@dataclass(frozen=True)
class RateNodeInput:
    """One stored ``rate`` row, already read out of the store.

    Columns are passed as they were stored. This module never queries, and an
    absent value is ``None`` rather than an empty string, so "the column is
    null" and "the column says nothing" are the same state, as they are in the
    schema.
    """

    node_id: int
    label: str
    payload: str
    file_hash: Optional[str] = None
    sheet: Optional[str] = None
    page: Optional[int] = None
    bbox: Optional[str] = None
    raw_text: Optional[str] = None


@dataclass(frozen=True)
class NormalizedRate:
    """One proposal. Every field is either read or refused; none is guessed."""

    node_id: int
    label: str
    status: str
    confidence: str
    reason: Optional[str] = None
    source_document_id: Optional[int] = None
    source_file_name: Optional[str] = None
    source_file_hash: Optional[str] = None
    original_amount: Optional[str] = None
    original_unit: Optional[str] = None
    normalized_amount: Optional[str] = None
    normalized_unit: Optional[str] = None
    currency: Optional[str] = None
    rate_category: Optional[str] = None
    effective_date: Optional[str] = None
    source_age_days: Optional[int] = None
    locator_present: bool = False
    duplicate_quote_count: int = 0
    warnings: tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.status == STATUS_NORMALIZED


# --------------------------------------------------------------------------
# Small readers - each one answers a single question, or refuses to
# --------------------------------------------------------------------------
def _text(value: Any) -> Optional[str]:
    """A non-empty stripped string. A number is not a word."""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _payload_object(raw: str) -> tuple[Optional[dict], Optional[str]]:
    """Decode a stored payload against the closed key set.

    Both a payload that will not decode and one carrying a key this module does
    not know about are refusals. The second case matters more than it looks: a
    row that has grown a field is a row whose meaning has changed, and reading
    only the fields we recognise would normalise a rate from a partly
    understood row.
    """
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None, REASON_MALFORMED_PAYLOAD
    if not isinstance(value, dict):
        return None, REASON_MALFORMED_PAYLOAD
    if set(value) - ALLOWED_PAYLOAD_KEYS:
        return None, REASON_UNKNOWN_PAYLOAD_KEYS
    return value, None


def _amount(value: Any) -> tuple[Optional[Decimal], Optional[str], Optional[str]]:
    """Parse an amount to a quantized :class:`Decimal`.

    Returns ``(quantized, original_text, reason)``. The parse goes through
    ``str`` and ``Decimal`` and never through ``float``: ``Decimal("1.005")`` is
    exactly one and a half thousandths, whereas the float literal is slightly
    less and would round the other way.
    """
    if value is None:
        return None, None, REASON_MISSING_AMOUNT
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None, None, REASON_INVALID_AMOUNT
    text = str(value).strip()
    if not text:
        return None, None, REASON_MISSING_AMOUNT
    if len(text) > MAX_AMOUNT_CHARS:
        return None, None, REASON_INVALID_AMOUNT
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError):
        return None, None, REASON_INVALID_AMOUNT
    # ``is_finite`` first: comparing a NaN Decimal raises rather than returning
    # a boolean, so the bounds check below would otherwise be the failure point.
    if not parsed.is_finite():
        return None, None, REASON_INVALID_AMOUNT
    if parsed < MIN_RATE_AMOUNT or parsed > MAX_RATE_AMOUNT:
        return None, None, REASON_INVALID_AMOUNT
    quantized = parsed.quantize(AMOUNT_EXPONENT, rounding=ROUND_HALF_UP)
    if quantized < MIN_RATE_AMOUNT:
        # 0.004 rounds to 0.00. A rate that rounds to nothing is not a rate, and
        # reporting it as one would be a fabricated zero.
        return None, None, REASON_INVALID_AMOUNT
    return quantized, text, None


def _effective_date(value: Any, today: date) -> tuple[Optional[str], Optional[str]]:
    """A ``YYYY-MM-DD`` date that is not in the future."""
    text = _text(value)
    if text is None:
        return None, REASON_MISSING_EFFECTIVE_DATE
    if len(text) > MAX_DATE_CHARS or not _DATE_RE.match(text):
        return None, REASON_INVALID_EFFECTIVE_DATE
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return None, REASON_INVALID_EFFECTIVE_DATE
    if parsed > today:
        # A rate cannot be effective in the future and still be evidence of
        # what something costs now.
        return None, REASON_INVALID_EFFECTIVE_DATE
    return parsed.isoformat(), None


def _document_id(value: Any) -> tuple[Optional[int], Optional[str]]:
    """A positive integer document id, or a reason it is not one."""
    if value is None:
        return None, None
    if isinstance(value, bool) or not isinstance(value, int):
        return None, REASON_INVALID_DOCUMENT_ID
    if value < 1:
        return None, REASON_INVALID_DOCUMENT_ID
    return int(value), None


def _source_hash(*values: Any) -> tuple[Optional[str], Optional[str]]:
    """The first usable SHA-256 among *values*, or a reason it is not usable.

    A value that is present but not a hash is reported rather than skipped: a
    malformed hash in the payload is a statement about the row that is wrong,
    and falling through to the next source would hide it.
    """
    for value in values:
        if value is None:
            continue
        text = _text(value)
        if text is None:
            continue
        lowered = text.lower()
        if not SHA256_RE.match(lowered):
            return None, REASON_INVALID_SOURCE_HASH
        return lowered, None
    return None, None


def _locator(
    node: RateNodeInput, payload: Mapping[str, Any]
) -> tuple[bool, bool, tuple[str, ...]]:
    """``(locator, sheet_or_page, warnings)`` for one row.

    Column first, payload second - the same precedence the revision diff reads
    provenance with. The column is what a caller that populated it meant to
    store; the payload copy is what the ingest paths that never pass a ``ref``
    actually leave behind.
    """
    warnings: list[str] = []
    raw_text = node.raw_text or _text(payload.get("raw_text"))
    bbox = node.bbox or _text(payload.get("bbox"))
    locator = bool(raw_text or bbox)
    if not locator:
        warnings.append(WARNING_LOCATOR_UNAVAILABLE)

    page = node.page if node.page is not None else payload.get("page")
    has_page = (
        isinstance(page, int) and not isinstance(page, bool) and page >= 1
    )
    sheet = node.sheet or _text(payload.get("sheet"))
    referenced = bool(sheet) or has_page
    if not referenced:
        warnings.append(WARNING_SHEET_OR_PAGE_UNAVAILABLE)
    return locator, referenced, tuple(warnings)


def index_documents(
    rows: Iterable[SourceDocument],
) -> tuple[dict[int, SourceDocument], dict[str, SourceDocument]]:
    """Index source documents by id and by lower-cased hash.

    Two indexes because a row may name its source either way, and both must
    resolve to the same document when it names both.
    """
    by_id: dict[int, SourceDocument] = {}
    by_hash: dict[str, SourceDocument] = {}
    for row in rows:
        by_id[row.document_id] = row
        by_hash[row.file_hash.lower()] = row
    return by_id, by_hash


def normalize_rate_node(
    node: RateNodeInput,
    *,
    documents_by_id: Mapping[int, SourceDocument],
    documents_by_hash: Mapping[str, SourceDocument],
    today: date,
) -> NormalizedRate:
    """Turn one stored ``rate`` row into one proposal.

    Pure: no I/O, no clock, no randomness. ``today`` is an argument so the age a
    proposal reports is recomputable from the payload and the response rather
    than from whenever the reader happens to run it.

    The steps run in the order the module docstring fixes. The first refusal
    returns immediately; nothing after it is evaluated, so a row with several
    problems reports the same one reason every time.

    An unresolved row carries no number, no unit, no currency and no category -
    including the ones it did parse. Nothing about a refused row is offered as
    a partial result, because a partially populated proposal is exactly the
    thing a reader would use as though it were complete.
    """

    def refused(reason: str) -> NormalizedRate:
        return NormalizedRate(
            node_id=node.node_id,
            label=node.label,
            status=STATUS_UNRESOLVED,
            confidence=CONFIDENCE_UNRESOLVED,
            reason=reason,
        )

    payload, reason = _payload_object(node.payload)
    if payload is None:
        return refused(reason or REASON_MALFORMED_PAYLOAD)

    locator, sheet_or_page, locator_warnings = _locator(node, payload)

    # Source document. The payload's claims are read first and verified against
    # the store; the columns are the fallback for a row that only populated
    # them. Whatever resolves here is what the response names - the payload's
    # own hash and id are never echoed back as though they were proof.
    document_id, reason = _document_id(payload.get("doc_id"))
    if reason is not None:
        return refused(reason)

    source_hash, reason = _source_hash(payload.get("file_hash"), node.file_hash)
    if reason is not None:
        return refused(reason)

    if document_id is None and source_hash is None:
        return refused(REASON_MISSING_SOURCE)

    if document_id is not None:
        source = documents_by_id.get(document_id)
        if source is None:
            # The row names a document this project no longer holds. Its source
            # was replaced or the row predates a re-ingest, and there is no rate
            # to quote from a document nobody can open.
            return refused(REASON_EVIDENCE_UNAVAILABLE)
        if source_hash is not None and source.file_hash.lower() != source_hash:
            return refused(REASON_SOURCE_MISMATCH)
    else:
        source = documents_by_hash.get(source_hash)
        if source is None:
            return refused(REASON_UNVERIFIED_SOURCE)

    # The resolved document's own hash is what the proposal will cite, so it has
    # to be a hash. ``documents.file_hash`` is unconstrained TEXT, and a row
    # holding something else cannot be named as the source of a rate: emitting
    # it would put a non-hash in the field a reader treats as proof.
    if not SHA256_RE.match(source.file_hash.lower()):
        return refused(REASON_INVALID_SOURCE_HASH)

    # Provenance kind. Required: a row that will not say whether a machine or a
    # person produced it cannot be reported as verified either way.
    declared = _text(payload.get("provenance"))
    if declared is None:
        return refused(REASON_MISSING_PROVENANCE)
    provenance = declared.casefold()
    if provenance not in PROVENANCE_KINDS:
        return refused(REASON_UNSUPPORTED_PROVENANCE)

    # Amount.
    amount, original_amount, reason = _amount(payload.get("amount"))
    if reason is not None:
        return refused(reason)

    # Unit and category, each against its own closed set, then against each
    # other. A unit that is fine in general but wrong for the stated category is
    # refused rather than converted: "R/hr" for a cart-away is not a rate anyone
    # can use, and turning it into one would be arithmetic on a guess.
    declared_unit = _text(payload.get("unit"))
    if declared_unit is None:
        return refused(REASON_MISSING_UNIT)
    unit = normalize_unit(declared_unit)
    if unit not in CANONICAL_RATE_UNITS:
        return refused(REASON_UNSUPPORTED_UNIT)

    declared_category = _text(payload.get("rate_category"))
    if declared_category is None:
        return refused(REASON_MISSING_CATEGORY)
    category = declared_category.casefold()
    if category not in RATE_CATEGORIES:
        return refused(REASON_UNSUPPORTED_CATEGORY)
    if unit not in CATEGORY_UNITS[category]:
        return refused(REASON_UNIT_CATEGORY_MISMATCH)

    # Currency. AUD or nothing: a symbol is not a currency declaration.
    declared_currency = _text(payload.get("currency"))
    if declared_currency is None:
        return refused(REASON_MISSING_CURRENCY)
    if declared_currency.casefold() != CURRENCY_AUD.casefold():
        return refused(REASON_UNSUPPORTED_CURRENCY)

    effective, reason = _effective_date(payload.get("effective_date"), today)
    if reason is not None:
        return refused(reason)
    assert effective is not None  # _effective_date returns both or neither
    age = (today - date.fromisoformat(effective)).days

    warnings = list(locator_warnings)
    if provenance == PROVENANCE_HAND_PREPARED:
        warnings.append(WARNING_MANUAL_PROVENANCE)
    if age > MAX_RATE_AGE_DAYS:
        # Reported, never applied. Escalating needs a verified index and an
        # explicit assumption, and this store has neither.
        warnings.append(WARNING_STALE_SOURCE)

    # ``exact`` is a statement about the proof, not about the arithmetic: it
    # needs a machine export and a locator that ties the number to a spot in the
    # source. Without both, the number is still the row's own - so it is
    # reported, at lower confidence, with the missing piece named in warnings.
    exact = (
        provenance == PROVENANCE_MACHINE_EXPORT and locator and sheet_or_page
    )

    return NormalizedRate(
        node_id=node.node_id,
        label=node.label,
        status=STATUS_NORMALIZED,
        confidence=CONFIDENCE_EXACT if exact else CONFIDENCE_INFERRED,
        reason=None,
        source_document_id=source.document_id,
        source_file_name=source.file_name,
        source_file_hash=source.file_hash,
        original_amount=original_amount,
        original_unit=unit,
        normalized_amount=str(amount),
        normalized_unit=unit,
        currency=CURRENCY_AUD,
        rate_category=category,
        effective_date=effective,
        source_age_days=age,
        locator_present=locator,
        warnings=tuple(warnings),
    )


def apply_duplicate_quotes(
    proposals: Iterable[NormalizedRate],
) -> list[NormalizedRate]:
    """Flag proposals that quote the same source, category and unit as another.

    Two rows quoting one plant rate for one category are two readings of one
    quote, and a reader has to be told so rather than shown whichever row the
    query happened to return first. Nothing is preferred and nothing is dropped:
    both stay, both are flagged, and ``duplicate_quote_count`` says how many
    others share the key. Unresolved rows take no part - a row with no number
    cannot be a duplicate of one.
    """
    items = list(proposals)

    def key(proposal: NormalizedRate) -> tuple:
        return (
            proposal.source_file_hash,
            proposal.rate_category,
            proposal.normalized_unit,
            proposal.currency,
        )

    counts: dict[tuple, int] = {}
    for proposal in items:
        if proposal.resolved:
            counts[key(proposal)] = counts.get(key(proposal), 0) + 1

    flagged: list[NormalizedRate] = []
    for proposal in items:
        others = counts.get(key(proposal), 0) - 1 if proposal.resolved else 0
        if others <= 0:
            flagged.append(proposal)
            continue
        warnings = proposal.warnings
        if WARNING_DUPLICATE_QUOTE not in warnings:
            warnings = warnings + (WARNING_DUPLICATE_QUOTE,)
        flagged.append(
            replace(
                proposal,
                duplicate_quote_count=others,
                warnings=warnings,
            )
        )
    return flagged


def normalize_rate_nodes(
    nodes: Iterable[RateNodeInput],
    *,
    documents: Iterable[SourceDocument],
    today: date,
) -> list[NormalizedRate]:
    """Normalize a batch, in the order it was given, with duplicates flagged.

    The order is the caller's order, so the response lines up with the request
    for a reader who is looking at both. Everything else is order-independent:
    each proposal is decided from its own row alone, and the duplicate pass is
    a set operation over the results.
    """
    by_id, by_hash = index_documents(documents)
    proposals = [
        normalize_rate_node(
            node,
            documents_by_id=by_id,
            documents_by_hash=by_hash,
            today=today,
        )
        for node in nodes
    ]
    return apply_duplicate_quotes(proposals)
