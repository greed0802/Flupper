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

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ..contracts.evidence import QuantityClaim

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

