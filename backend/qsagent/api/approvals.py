"""Single-use approval nonces for the HTTP gateway.

Why a registry exists at all
----------------------------
``AgentSession`` already stores approvals keyed by a deterministic action id.
On its own that is a *policy* gate, not a capability: anything that can name the
action id can grant it. The registry adds the missing capability layer.

* A nonce is ``uuid4().hex`` - unguessable, returned to the caller exactly once.
* The nonce is bound to a canonical hash of the exact action being approved
  (argv + profile + network policy, or task id + prompt hash), to the project,
  and to the *session segment* that was live when it was minted.
* It is single-use, enforced by popping it under the per-project lock.
* It expires.

The hash is rebuilt on both sides - the mint endpoint and the execution
endpoint - so approving ``["echo", "hello"]`` can never authorise
``["rm", "-rf", "/"]``. ``execute_tier3`` then reads ``approval_id`` straight
off the rebuilt ``SandboxRequest``, which makes the tie between "approve" and
"execute" structural rather than conventional.

The registry is deliberately in-process. The gateway is a single-worker local
daemon; a multi-worker deployment would need a shared store, and silently
losing nonce state between workers would be a security bug. That limitation is
enforced in ``run_local.py`` rather than documented and forgotten.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Mapping

DEFAULT_TTL_SECONDS = 300.0
MAX_PENDING_APPROVALS = 256

CANONICAL_ENCODING = "utf-8"

TIER3_KIND = "tier3"
MODEL_KIND = "model"
APPROVAL_KINDS = frozenset({TIER3_KIND, MODEL_KIND})


class ApprovalError(RuntimeError):
    """A nonce was unknown, expired, already consumed, or bound elsewhere."""


def canonical_request_hash(metadata: Mapping[str, object]) -> str:
    """SHA-256 over a canonical JSON encoding of *metadata*.

    Canonical means: keys sorted, no insignificant whitespace, ASCII-escaped.
    Two callers that build the same semantic payload from the same values
    always get the same digest, on any platform and in any dict order.
    """
    canonical = json.dumps(
        metadata,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode(CANONICAL_ENCODING)).hexdigest()


def prompt_digest(prompt: str) -> str:
    """Bind a task approval to the exact prompt text without storing it."""
    return hashlib.sha256(prompt.encode(CANONICAL_ENCODING)).hexdigest()


@dataclass(frozen=True)
class PendingApproval:
    nonce: str
    kind: str
    action_id: str
    request_hash: str
    project_id: int
    segment_id: str
    created_at: float
    expires_at: float

    def expired(self, now: float) -> bool:
        return now >= self.expires_at

    def remaining_seconds(self, now: float) -> int:
        return max(0, int(self.expires_at - now))


@dataclass(frozen=True)
class Binding:
    """The exact payload a session-level approval was granted for.

    ``AgentSession`` grants are keyed by action id and carry no payload, so for
    any action id that does not already encode its own payload the gateway has
    to remember what was approved.
    """

    request_hash: str
    expires_at: float


class ApprovalRegistry:
    """In-process, single-worker registry of one-shot approval nonces."""

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_pending: int = MAX_PENDING_APPROVALS,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_pending <= 0:
            raise ValueError("max_pending must be positive")
        self.ttl_seconds = float(ttl_seconds)
        self.max_pending = int(max_pending)
        self._pending: dict[str, PendingApproval] = {}
        self._bindings: dict[tuple[int, str], Binding] = {}

    def __len__(self) -> int:
        self._prune()
        return len(self._pending)

    def issue(
        self,
        *,
        kind: str,
        action_id: str,
        request_hash: str,
        project_id: int,
        segment_id: str,
    ) -> PendingApproval:
        if kind not in APPROVAL_KINDS:
            raise ValueError(f"unknown approval kind {kind!r}")
        if not action_id or not request_hash or not segment_id:
            raise ValueError("action_id, request_hash and segment_id are required")

        self._prune()
        now = time.time()
        record = PendingApproval(
            nonce=uuid.uuid4().hex,
            kind=kind,
            action_id=action_id,
            request_hash=request_hash,
            project_id=int(project_id),
            segment_id=segment_id,
            created_at=now,
            expires_at=now + self.ttl_seconds,
        )
        self._pending[record.nonce] = record
        self._enforce_capacity()
        return record

    def peek(self, nonce: str) -> PendingApproval:
        """Look a nonce up without consuming it, so its binding can be checked
        before any session state changes."""
        self._prune()
        record = self._pending.get(nonce)
        if record is None:
            raise ApprovalError("unknown, expired or already-consumed approval nonce")
        return record

    def consume(self, nonce: str) -> PendingApproval:
        """Pop a nonce exactly once. Raises when it was already consumed."""
        self._prune()
        record = self._pending.pop(nonce, None)
        if record is None:
            raise ApprovalError("unknown, expired or already-consumed approval nonce")
        return record

    def bind(
        self,
        *,
        project_id: int,
        action_id: str,
        request_hash: str,
    ) -> None:
        """Record the exact payload a *session-level* approval was granted for.

        ``AgentSession`` keys approvals by action id alone. For Tier 3 that is
        already a payload binding, because the gateway derives the action id
        from the request hash - approve ``["echo","hello"]`` and the session
        holds a grant for ``tool_<hash(["echo","hello"])>``, which no other argv
        vector can name.

        Model tasks are not like that. The session requires CONFIRM for
        ``model_<task_id>``, which cannot express *which prompt* was approved, so
        approving prompt A would otherwise leave prompt B runnable. This records
        the payload the operator actually approved so ``require_binding`` can
        refuse everything else at execution time.

        Minting is not successful until this is recorded: the caller grants the
        session approval and registers the payload in the same lock.
        """
        self._prune()
        now = time.time()
        self._bindings[(int(project_id), action_id)] = Binding(
            request_hash=request_hash,
            expires_at=now + self.ttl_seconds,
        )

    def require_binding(
        self,
        *,
        project_id: int,
        action_id: str,
        request_hash: str,
    ) -> None:
        """Consume a recorded payload binding, refusing anything that differs.

        Single-use and fail-closed: an absent binding and a mismatched binding
        raise the identical generic error, so a caller cannot probe for how far
        it got.
        """
        self._prune()
        record = self._bindings.pop((int(project_id), action_id), None)
        if record is None or record.request_hash != request_hash:
            raise ApprovalError("approval nonce does not apply to this request")

    def purge_project(self, project_id: int) -> int:
        """Drop every pending nonce for *project_id*; returns the count.

        Called when a session segment rotates (restart / start_executing) so a
        nonce minted against a segment that no longer exists cannot linger.

        Payload bindings are cleared with the nonces they belong to: a binding
        without a session segment to execute in is exactly as stale.
        """
        stale = [
            nonce for nonce, rec in self._pending.items()
            if rec.project_id == int(project_id)
        ]
        for nonce in stale:
            del self._pending[nonce]
        bound = [key for key in self._bindings if key[0] == int(project_id)]
        for key in bound:
            del self._bindings[key]
        return len(stale)

    # ---------------------------------------------------------------- internals
    def _prune(self) -> None:
        now = time.time()
        expired = [nonce for nonce, rec in self._pending.items() if rec.expired(now)]
        for nonce in expired:
            del self._pending[nonce]
        stale = [key for key, rec in self._bindings.items() if now >= rec.expires_at]
        for key in stale:
            del self._bindings[key]

    def _enforce_capacity(self) -> None:
        while len(self._pending) > self.max_pending:
            oldest = min(self._pending.values(), key=lambda rec: rec.created_at)
            del self._pending[oldest.nonce]


def check_binding(
    record: PendingApproval,
    *,
    project_id: int,
    segment_id: str,
    request_hash: str | None = None,
) -> None:
    """Validate a looked-up nonce against the route it is being spent on.

    Deliberately raises the same generic message for every mismatch: a caller
    learns that its nonce did not apply, not which part of the binding failed.
    """
    if record.project_id != int(project_id):
        raise ApprovalError("approval nonce does not apply to this request")
    if record.segment_id != segment_id:
        raise ApprovalError("approval nonce does not apply to this request")
    if request_hash is not None and record.request_hash != request_hash:
        raise ApprovalError("approval nonce does not apply to this request")
