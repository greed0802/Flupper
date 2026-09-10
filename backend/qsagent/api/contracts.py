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

ArgvItem = Annotated[str, StringConstraints(max_length=MAX_ARGV_ITEM_CHARS)]

SandboxProfileName = Literal["safe", "development"]
SandboxNetworkPolicyName = Literal["ALLOW", "BEST_EFFORT", "BLOCK_STRICT"]
ApprovalKind = Literal["tier3", "model"]


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
