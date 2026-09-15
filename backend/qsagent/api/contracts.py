"""Typed HTTP contracts for the local Flupper gateway.

Three rules are enforced here rather than in the route bodies:

* every model forbids unknown fields (``extra="forbid"``), so a client cannot
  smuggle extra keys into a journal payload or a log line;
* every string, list and integer is explicitly bounded, so no request body can
  turn into an unbounded log entry, journal payload or model prompt;
* no contract carries a credential. Provider keys are resolved server-side and
  never appear in a request or a response model.

The numeric bounds are module constants because both the routes and the tests
assert against them; a bound that is only hard-coded inside a route is not a
bound anyone can verify.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    Strict,
    StringConstraints,
    field_validator,
    model_validator,
)

from ..contracts.evidence import QuantityClaim, Unit

# --------------------------------------------------------------------------
# Hard bounds - single source of truth
# --------------------------------------------------------------------------
MAX_PROJECT_NAME_CHARS = 200
MAX_ARGV_ITEMS = 50
MAX_ARGV_ITEM_CHARS = 4096
MAX_PROMPT_CHARS = 8000
MAX_RESPONSE_CHARS = 10_000
MAX_OUTPUT_CHARS = 10_000
MAX_CLAIMS = 200
MAX_REASON_CHARS = 500
MAX_TASK_ID_CHARS = 64
MAX_PLAN_STEPS = 1000
MAX_NONCE_CHARS = 64

# ── Phase 5B: revision diff ──────────────────────────────────────────────
# A revision diff is read-only: its request is two integers in the path, so
# every bound below is about the response. Each one is the ceiling on a list
# or a string the server will emit, not a limit it hopes the store respects.
MAX_DIFF_ITEMS = 500
MAX_DIFF_FIELDS = 32
MAX_DIFF_AFFECTED_CLAIMS = 50
MAX_DIFF_WARNINGS = 8
MAX_DIFF_TEXT_CHARS = 255
MAX_DIFF_REASON_CHARS = 200
#: A canonical identity is a SHA-256 digest and a claim id is its own label;
#: both travel through this response, so both are bounded here rather than
#: being written as literals at each use.
MAX_DIFF_HASH_CHARS = 64
MAX_DIFF_CLAIM_ID_CHARS = 64

# ── Phase 5B: what one request may read ──────────────────────────────────
# These bound the read where the constants above bound the answer. They are
# here rather than in the route because they surface in the response: a row
# count a caller is told about is a row count the caller can check.
MAX_DIFF_SCANNED_NODES = 2000
MAX_DIFF_SCANNED_CLAIMS = 2000
MAX_DIFF_SCANNED_DOCUMENTS = 2000

# ── Phase 5C: evidence-backed rate normalization ─────────────────────────
# The first phase here whose response carries money. Every bound below is a
# ceiling on a string the server renders, because a rate is quoted as a decimal
# string and a bound that only lived inside the normalizer would not be a bound
# anyone could check.
#: The ids one request may name. It is also the ceiling on the proposal list,
#: one item per id, so the response size is decided by the request shape rather
#: than by how many rows the project happens to hold.
MAX_RATE_NODE_IDS = 100
#: Ids are positive and fit in a SQLite INTEGER. The upper bound is not
#: decoration: an id beyond 64 bits makes the driver raise on binding, which
#: would surface as a 500 for what is plainly a malformed request.
MAX_RATE_NODE_ID = 9223372036854775807
MAX_RATE_TEXT_CHARS = 255
MAX_RATE_HASH_CHARS = 64
MAX_RATE_AMOUNT_CHARS = 64
MAX_RATE_DATE_CHARS = 10
MAX_RATE_WARNINGS = 8
#: Documents one request may read to resolve sources. A source that cannot be
#: resolved because it sits past this bound is reported as truncated rather than
#: as missing, so a ceiling can never be mistaken for a finding.
MAX_RATE_SCANNED_DOCUMENTS = 2000

ArgvItem = Annotated[str, StringConstraints(max_length=MAX_ARGV_ITEM_CHARS)]

SandboxProfileName = Literal["safe", "development"]
SandboxNetworkPolicyName = Literal["ALLOW", "BEST_EFFORT", "BLOCK_STRICT"]
ApprovalKind = Literal["tier3", "model"]
DiffStatus = Literal[
    "added", "removed", "changed", "unchanged", "ambiguous", "unresolved"
]
DiffRelationship = Literal[
    "same_drawing", "unverified_drawing", "evidence_unavailable"
]

# ── Phase 5C: rate proposal vocabulary ───────────────────────────────────
# Closed sets, and closed here rather than in the normalizer, so a value the
# route can emit is a value this file lists. ``test_rate_normalization`` asserts
# the normalizer's own tuples equal these, which is what stops the two drifting.
RateStatus = Literal["normalized", "unresolved"]
RateConfidence = Literal["exact", "inferred", "unresolved"]
RateCategory = Literal[
    "plant_time",
    "labour_time",
    "material_unit",
    "volume_cart_away",
    "subcontract_lumpsum",
]
RateCurrency = Literal["AUD"]
RateUnresolvedReason = Literal[
    "malformed_payload",
    "unknown_payload_keys",
    "missing_source",
    "invalid_document_id",
    "invalid_source_hash",
    "unverified_source",
    "evidence_unavailable",
    "source_mismatch",
    "missing_provenance",
    "unsupported_provenance",
    "missing_amount",
    "invalid_amount",
    "missing_unit",
    "unsupported_unit",
    "missing_category",
    "unsupported_category",
    "unit_category_mismatch",
    "missing_currency",
    "unsupported_currency",
    "missing_effective_date",
    "invalid_effective_date",
]
RateWarning = Literal[
    "locator_unavailable",
    "sheet_or_page_unavailable",
    "manual_provenance",
    "stale_source",
    "duplicate_source_quote",
    "source_documents_truncated",
]

#: The canonical unit a proposal may be quoted in. Reusing the quantity-claim
#: enum rather than restating it means a unit a rate can be stored in is a unit
#: a claim can be measured in, and neither list can silently grow alone.
RateUnit = Unit

#: An id that can name a row: a JSON integer, positive, and inside SQLite's
#: integer range so a value the driver cannot bind is refused as a malformed
#: request rather than reaching the store and surfacing as a 500.
#:
#: Strict, unlike the rest of this file's integers. An id is not a word and not
#: a flag: without this, ``"1"`` and ``true`` both select row 1, and a caller
#: that sent the wrong type would be answered about the wrong row.
PositiveNodeId = Annotated[
    int, Field(ge=1, le=MAX_RATE_NODE_ID), Strict()
]

#: The two list-item kinds in a diff response. Each is bounded at its own
#: field's length, so a list entry cannot be unbounded just because the list
#: itself is capped.
DiffClaimId = Annotated[str, StringConstraints(max_length=MAX_DIFF_CLAIM_ID_CHARS)]
DiffFieldName = Annotated[str, StringConstraints(max_length=MAX_DIFF_TEXT_CHARS)]


class _StrictModel(BaseModel):
    """Base for request bodies: an unknown field is a client bug, not a no-op."""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------------
class CreateProjectRequest(_StrictModel):
    name: str = Field(..., min_length=1, max_length=MAX_PROJECT_NAME_CHARS)
    client: str | None = Field(default=None, max_length=MAX_PROJECT_NAME_CHARS)
    tender_no: str | None = Field(default=None, max_length=MAX_TASK_ID_CHARS)


class ProjectResponse(BaseModel):
    project_id: int
    name: str


# --------------------------------------------------------------------------
# Session lifecycle
# --------------------------------------------------------------------------
class PlanRequest(_StrictModel):
    """The plan is a small typed step list - never a free-form dictionary."""

    steps: int = Field(default=1, ge=0, le=MAX_PLAN_STEPS)
    notes: str | None = Field(default=None, max_length=MAX_PROJECT_NAME_CHARS)


class RestartRequest(_StrictModel):
    reason: str = Field(..., min_length=1, max_length=MAX_REASON_CHARS)


class ReasoningRequest(_StrictModel):
    claims: list[QuantityClaim] = Field(..., max_length=MAX_CLAIMS)


class DeliverRequest(_StrictModel):
    response_text: str = Field(..., min_length=1, max_length=MAX_RESPONSE_CHARS)
    claims: list[QuantityClaim] = Field(..., max_length=MAX_CLAIMS)


class SessionStateResponse(BaseModel):
    project_id: int
    state: str
    segment_id: str
    history_length: int
    tool_runs: int
    failed_tools: bool
    model_runs: int
    failed_models: bool


class TransitionResponse(BaseModel):
    project_id: int
    from_state: str
    to_state: str
    action: str
    journal_seq: int
    segment_id: str


# --------------------------------------------------------------------------
# Tier 3 approval + execution
# --------------------------------------------------------------------------
class Tier3ExecuteRequest(_StrictModel):
    argv: list[ArgvItem] = Field(..., min_length=1, max_length=MAX_ARGV_ITEMS)
    profile: SandboxProfileName = "safe"
    network_policy: SandboxNetworkPolicyName = "BEST_EFFORT"


class Tier3ApprovalRequest(Tier3ExecuteRequest):
    """A Tier 3 approving request is exactly a Tier 3 execution request.

    Same fields, same canonicalisation, therefore the same request hash - which
    is the point. Approving anything other than the exact argv vector that will
    run is not expressible in this contract.
    """


class ApprovalNonceResponse(BaseModel):
    nonce: str
    kind: ApprovalKind
    action_id: str
    request_hash: str
    project_id: int
    segment_id: str
    expires_in_seconds: int


class ApproveRequest(_StrictModel):
    nonce: str = Field(..., min_length=32, max_length=MAX_NONCE_CHARS)


class ApprovalGrantedResponse(BaseModel):
    action_id: str
    kind: ApprovalKind
    state: str
    segment_id: str


class Tier3ResultResponse(BaseModel):
    ok: bool
    exit_code: int | None
    timed_out: bool
    output_limited: bool
    resource_limited: bool
    duration_ms: int
    isolation_backend: str
    isolation_strength: str
    error_code: str | None
    stdout: str
    stdout_truncated: bool
    stderr: str
    stderr_truncated: bool


# --------------------------------------------------------------------------
# Model tasks
# --------------------------------------------------------------------------
class ModelApprovalRequest(_StrictModel):
    task_id: str = Field(..., min_length=1, max_length=MAX_TASK_ID_CHARS)
    prompt: str = Field(..., min_length=1, max_length=MAX_PROMPT_CHARS)


class ModelTaskRequest(ModelApprovalRequest):
    """A model-task approving request is a model-task execution request."""


class ModelResultResponse(BaseModel):
    text: str
    truncated: bool
    provider: str
    model: str
    usage_tokens: int


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str
    sandbox_root_ready: bool


# --------------------------------------------------------------------------
# Phase 5B - revision diff (read-only)
# --------------------------------------------------------------------------
class RevisionDocumentDTO(BaseModel):
    """One side of the comparison, resolved server-side from its id.

    The client sends two document ids inside a project path and nothing else:
    no path, no hash, no filename. Everything here was read from the store, so
    a caller cannot nominate which file is compared or claim its hash is
    trustworthy.
    """

    document_id: int
    file_name: str = Field(..., max_length=MAX_DIFF_TEXT_CHARS)
    file_hash: str = Field(..., max_length=MAX_DIFF_HASH_CHARS)
    drawing_no: str | None = Field(default=None, max_length=MAX_DIFF_TEXT_CHARS)
    revision: str | None = Field(default=None, max_length=MAX_DIFF_TEXT_CHARS)
    evidence_rows: int
    evidence_truncated: bool


class RevisionEvidenceDTO(BaseModel):
    """One stored evidence row, as it takes part in a comparison.

    Only columns that exist on ``evidence_nodes`` are described. A stored
    ``raw_text`` longer than the bound is cut and flagged rather than dropped,
    because a locator the reader cannot see is worse than one marked short.
    """

    node_id: int
    identity_id: str = Field(..., max_length=MAX_DIFF_HASH_CHARS)
    node_type: str = Field(..., max_length=MAX_DIFF_TEXT_CHARS)
    label: str = Field(..., max_length=MAX_DIFF_TEXT_CHARS)
    drawing_no: str | None = Field(default=None, max_length=MAX_DIFF_TEXT_CHARS)
    revision: str | None = Field(default=None, max_length=MAX_DIFF_TEXT_CHARS)
    sheet: str | None = Field(default=None, max_length=MAX_DIFF_TEXT_CHARS)
    page: int | None
    zone: str | None = Field(default=None, max_length=MAX_DIFF_TEXT_CHARS)
    file_hash: str | None = Field(default=None, max_length=MAX_DIFF_HASH_CHARS)
    raw_text: str | None = Field(default=None, max_length=MAX_DIFF_TEXT_CHARS)
    raw_text_truncated: bool


class RevisionChangeDTO(BaseModel):
    """One classified footprint: what it is, and what a reader must check.

    ``affected_claim_ids`` is the whole point of the exercise: it names the
    claims whose stored evidence cites the base-side row, so a quantity surveyor
    can go straight to the numbers a drawing revision put in doubt. It is
    association only. Nothing in this response asserts a claim is still
    correct, and nothing here changes a claim's status.
    """

    identity_id: str = Field(..., max_length=MAX_DIFF_HASH_CHARS)
    status: DiffStatus
    group_size: int
    base: RevisionEvidenceDTO | None = None
    target: RevisionEvidenceDTO | None = None
    changed_fields: list[DiffFieldName] = Field(
        default_factory=list, max_length=MAX_DIFF_FIELDS
    )
    affected_claim_ids: list[DiffClaimId] = Field(
        default_factory=list, max_length=MAX_DIFF_AFFECTED_CLAIMS
    )
    affected_claims_omitted: int = 0
    reason: str | None = Field(default=None, max_length=MAX_DIFF_REASON_CHARS)


class RevisionDiffCountsDTO(BaseModel):
    added: int
    removed: int
    changed: int
    unchanged: int
    ambiguous: int
    unresolved: int


class RevisionDiffResponse(BaseModel):
    """A read-only comparison of two revisions of one source.

    The counts are of the whole comparison; ``items`` may be shorter. When it
    is, ``items_truncated`` is true and ``items_omitted`` says by how much, so
    a short list is never mistaken for a complete one.
    """

    project_id: int
    base: RevisionDocumentDTO
    target: RevisionDocumentDTO
    relationship: DiffRelationship
    counts: RevisionDiffCountsDTO
    items: list[RevisionChangeDTO] = Field(
        default_factory=list, max_length=MAX_DIFF_ITEMS
    )
    items_truncated: bool
    items_omitted: int
    checked_claims: int
    malformed_claims: int
    unverified_claim_references: int
    unassociated_claims: int
    warnings: list[str] = Field(default_factory=list, max_length=MAX_DIFF_WARNINGS)


# --------------------------------------------------------------------------
# Phase 5C - rate proposals (read-only)
# --------------------------------------------------------------------------
class RateProposalRequest(_StrictModel):
    """The ids of the rate rows to propose from, and nothing else.

    There is no field here for a rate, a unit, a currency, a category, a proof
    reference or a path, so a caller cannot assert any of them. The request
    names rows the server already holds and the server reads their content
    itself - which is the whole difference between quoting a stored rate and
    being handed one.
    """

    node_ids: list[PositiveNodeId] = Field(
        ..., min_length=1, max_length=MAX_RATE_NODE_IDS
    )

    @field_validator("node_ids")
    @classmethod
    def _dedupe(cls, value: list[int]) -> list[int]:
        """Keep the first occurrence of each id, in the order sent.

        Naming the same row twice is asking one question twice. It is resolved
        rather than rejected: answering it twice would put two identical
        proposals in the response for a reader to reconcile. The order is the
        caller's, so the response lines up with the request.
        """
        seen: set[int] = set()
        unique: list[int] = []
        for node_id in value:
            if node_id not in seen:
                seen.add(node_id)
                unique.append(node_id)
        return unique


class RateProposalDTO(BaseModel):
    """One rate row: the stored rate, normalized, or a reason there is none.

    Both shapes are final. A normalized proposal is the only one that carries a
    number, and it always carries the source, unit, currency, category and
    effective date it was read with - a rate without its dimensions is not a
    rate. An unresolved proposal carries no number, no unit, no currency and no
    category at all: a half-populated proposal is exactly the thing a reader
    would quote as though it were complete.

    No unit conversion is applied, so ``original_unit`` and ``normalized_unit``
    are the same canonical spelling. The pair is kept because the amount pair is
    not: an amount may round (``"1.005"`` -> ``"1.01"``) and a reader is entitled
    to both the reading and the rendering. ``original_amount`` is the row's own
    text; ``normalized_amount`` is what this platform would quote.
    """

    node_id: int
    label: str = Field(..., max_length=MAX_RATE_TEXT_CHARS)
    status: RateStatus
    confidence: RateConfidence
    reason: RateUnresolvedReason | None = None
    source_document_id: int | None = None
    source_file_name: str | None = Field(default=None, max_length=MAX_RATE_TEXT_CHARS)
    source_file_hash: str | None = Field(default=None, max_length=MAX_RATE_HASH_CHARS)
    original_amount: str | None = Field(
        default=None, max_length=MAX_RATE_AMOUNT_CHARS
    )
    original_unit: str | None = Field(default=None, max_length=MAX_RATE_TEXT_CHARS)
    normalized_amount: str | None = Field(
        default=None, max_length=MAX_RATE_AMOUNT_CHARS
    )
    normalized_unit: RateUnit | None = None
    currency: RateCurrency | None = None
    rate_category: RateCategory | None = None
    effective_date: str | None = Field(default=None, max_length=MAX_RATE_DATE_CHARS)
    source_age_days: int | None = None
    locator_present: bool
    duplicate_quote_count: int = 0
    warnings: list[RateWarning] = Field(
        default_factory=list, max_length=MAX_RATE_WARNINGS
    )

    @model_validator(mode="after")
    def _consistent(self) -> "RateProposalDTO":
        """Refuse to render a proposal that contradicts itself.

        This guards the server's own arithmetic, not the caller's input: no
        field here is settable by a request. A contradiction is a bug in the
        normalizer, and it fails at the boundary rather than reaching a reader
        as an answer that looks complete.
        """
        if self.status == "normalized":
            if self.normalized_amount is None or self.reason is not None:
                raise ValueError("a normalized proposal needs a number and no reason")
            required = (
                self.source_document_id,
                self.source_file_name,
                self.source_file_hash,
                self.original_amount,
                self.original_unit,
                self.normalized_unit,
                self.currency,
                self.rate_category,
                self.effective_date,
                self.source_age_days,
            )
            if any(value is None for value in required):
                raise ValueError(
                    "a normalized proposal must carry its whole provenance"
                )
            if self.confidence == "unresolved":
                raise ValueError("a normalized proposal cannot be unresolved")
        else:
            if self.normalized_amount is not None or self.reason is None:
                raise ValueError("an unresolved proposal needs a reason and no number")
            if self.confidence != "unresolved":
                raise ValueError("an unresolved proposal cannot carry confidence")
            partial = (
                self.source_document_id,
                self.source_file_name,
                self.source_file_hash,
                self.original_amount,
                self.original_unit,
                self.normalized_unit,
                self.currency,
                self.rate_category,
                self.effective_date,
                self.source_age_days,
            )
            if any(value is not None for value in partial):
                raise ValueError("an unresolved proposal must not carry a partial result")
            if self.locator_present:
                raise ValueError("an unresolved proposal ties nothing to a locator")
            if self.duplicate_quote_count:
                raise ValueError("an unresolved proposal cannot duplicate a quote")
        return self


class RateProposalResponse(BaseModel):
    """Read-only proposals for the rate rows a caller named.

    ``reference_date`` is the day the ages were measured against. Age is the one
    input that is not in the store, so it travels with the answer: a reader can
    recompute ``source_age_days`` from ``effective_date`` and this field instead
    of trusting a figure that depended on when the request happened to run.

    ``normalized`` and ``unresolved`` count the whole answer and always add up to
    ``len(proposals)``: there is no truncation here, because the request bounds
    the answer (one proposal per id, at most ``MAX_RATE_NODE_IDS`` ids). The
    ``warnings`` list is the request's, not a proposal's.
    """

    project_id: int
    reference_date: str = Field(..., max_length=MAX_RATE_DATE_CHARS)
    proposals: list[RateProposalDTO] = Field(
        default_factory=list, max_length=MAX_RATE_NODE_IDS
    )
    normalized: int
    unresolved: int
    warnings: list[RateWarning] = Field(
        default_factory=list, max_length=MAX_RATE_WARNINGS
    )

