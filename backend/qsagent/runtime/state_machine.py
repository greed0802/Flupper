import logging
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from ..checkmate.engine import CheckMate
from ..contracts.evidence import ApprovalLevel, QuantityClaim, ToolRun
from ..storage.db import QSStore
from .sandbox import (
    AuditFailureError,
    NetworkPolicy,
    ResourceProfile,
    SAFE_PROFILE,
    SandboxRequest,
    SandboxResult,
    execute as execute_in_sandbox,
)
from .router import ModelRouter, ModelTier, ProviderResponse

log = logging.getLogger(__name__)

class SessionState(str, Enum):
    RECEIVED = "RECEIVED"
    PLANNED = "PLANNED"
    EXECUTING = "EXECUTING"
    VALIDATING = "VALIDATING"
    REASONING = "REASONING"
    DELIVERED = "DELIVERED"

class InvalidTransitionError(RuntimeError):
    pass

class ApprovalRequiredError(RuntimeError):
    pass

class ToolFailureError(RuntimeError):
    def __init__(self, run: ToolRun) -> None:
        super().__init__(f"Tool run {run.tool_id} failed: {run.error}")
        self.run = run

class ApprovalConsumptionError(RuntimeError):
    """A model call was attempted but its one-shot approval was not consumed.

    Raised after the attempt, never before it: the external call already
    happened, and a reusable approval token is a security defect even when the
    provider call itself succeeded.
    """

@dataclass
class TransitionRecord:
    from_state: SessionState
    to_state: SessionState
    actor: str
    action: str
    payload: dict
    approval: ApprovalLevel
    timestamp: datetime
    journal_seq: int

@dataclass
class ModelRun:
    """Bounded record of one model execution inside a session.

    Invariant: never contains prompt text, model output, a credential, or a raw
    provider message. error_code is an exception class name only.
    """

    task_id: str
    tier: str
    provider_name: Optional[str]
    model_name: Optional[str]
    usage_tokens: Optional[int]
    duration_ms: int
    ok: bool
    error_code: Optional[str]

class AgentSession:
    def __init__(self, project_id: int, *, store: QSStore | None = None, actor: str = "agent") -> None:
        self.project_id = project_id
        self._store = store
        self.default_actor = actor
        self._state = SessionState.RECEIVED
        self._history: list[TransitionRecord] = []
        self._tool_runs: list[ToolRun] = []
        self._model_runs: list[ModelRun] = []
        self._approvals: dict[str, ApprovalLevel] = {}

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def history(self) -> list[TransitionRecord]:
        return self._history

    def _utcnow(self) -> datetime:
        return datetime.now(timezone.utc)

    def _satisfies(self, stored: ApprovalLevel, required: ApprovalLevel) -> bool:
        if required == ApprovalLevel.SAFE:
            return True
        if required == ApprovalLevel.REVIEW:
            return stored in (ApprovalLevel.REVIEW, ApprovalLevel.CONFIRM)
        if required == ApprovalLevel.CONFIRM:
            return stored == ApprovalLevel.CONFIRM
        return False

    def _check_approval(self, action_id: str, level: ApprovalLevel) -> None:
        if level in (ApprovalLevel.REVIEW, ApprovalLevel.CONFIRM):
            stored = self._approvals.get(action_id)
            if stored is None or not self._satisfies(stored, level):
                raise ApprovalRequiredError(f"Action '{action_id}' requires {level.value} approval.")

    def _consume_approval(self, action_id: str, level: ApprovalLevel) -> None:
        if level in (ApprovalLevel.REVIEW, ApprovalLevel.CONFIRM):
            self._approvals.pop(action_id, None)
    def _write_journal(self, actor: str, action: str, subject: Optional[str] = None, payload: Optional[dict] = None, approval: ApprovalLevel = ApprovalLevel.SAFE) -> int:
        if self._store:
            entry = self._store.journal(self.project_id, actor=actor, action=action, subject=subject, payload=payload, approval=approval)
            return entry.seq
        return 0

    def _transition(self, allowed_from: set[SessionState], to: SessionState, *, actor: str, action: str, payload: dict, approval: ApprovalLevel = ApprovalLevel.SAFE) -> TransitionRecord:
        if self._state not in allowed_from:
            raise InvalidTransitionError(f"Cannot transition from {self._state.value} to {to.value} via '{action}'")
        
        self._check_approval(action, approval)
        seq = self._write_journal(actor, action, payload=payload, approval=approval)
        self._consume_approval(action, approval)
        
        if action == "restart":
            self._approvals.clear()
            self._tool_runs.clear()
            self._model_runs.clear()
            
        record = TransitionRecord(
            from_state=self._state,
            to_state=to,
            actor=actor,
            action=action,
            payload=payload,
            approval=approval,
            timestamp=self._utcnow(),
            journal_seq=seq
        )
        self._state = to
        self._history.append(record)
        return record

    def plan(self, plan_payload: dict, *, actor: str | None = None) -> TransitionRecord:
        a = actor or self.default_actor
        return self._transition(
            {SessionState.RECEIVED, SessionState.PLANNED},
            SessionState.PLANNED,
            actor=a, action="plan", payload=plan_payload
        )

    def start_executing(self, *, actor: str | None = None) -> TransitionRecord:
        a = actor or self.default_actor
        self._approvals.clear()
        self._tool_runs.clear()
        self._model_runs.clear()
        return self._transition(
            {SessionState.PLANNED},
            SessionState.EXECUTING,
            actor=a, action="start_executing", payload={}
        )

    def record_tool_run(self, run: ToolRun, *, actor: str | None = None) -> None:
        if self._state is not SessionState.EXECUTING:
            raise InvalidTransitionError(f"Cannot record tool run in state {self._state.value}. Must be EXECUTING.")
        a = actor or self.default_actor
        if run.tier == 3:
            action_id = f"tool_{run.tool_id}"
            self._check_approval(action_id, ApprovalLevel.CONFIRM)
            self._consume_approval(action_id, ApprovalLevel.CONFIRM)
            
        payload = {"tool_id": run.tool_id, "ok": run.ok}
        if not run.ok and run.error:
            payload["error"] = run.error
            
        self._write_journal(a, "record_tool_run", subject=run.tool_id, payload=payload)
        self._tool_runs.append(run)
        
        if not run.ok:
            raise ToolFailureError(run)

    @property
    def has_failed_tools(self) -> bool:
        """True when any recorded tool run (any tier) failed."""
        return any(not run.ok for run in self._tool_runs)

    def _audit_tier3(self, action: str, payload: dict) -> None:
        """Bounded Tier 3 audit sink used by the sandbox orchestrator."""
        self._write_journal(self.default_actor, action, subject="tier3", payload=payload)

    def execute_tier3(
        self,
        tool_id: str,
        argv: list[str] | tuple[str, ...],
        *,
        profile: ResourceProfile = SAFE_PROFILE,
        workspace_parent: Path | str | None = None,
        stdin: bytes | None = None,
        environment: dict[str, str] | None = None,
        network_policy: NetworkPolicy = NetworkPolicy.BEST_EFFORT,
        actor: str | None = None,
    ) -> SandboxResult:
        """Execute an argv vector inside the Tier 3 sandbox.

        Approval
            Requires ``CONFIRM`` for action id ``tool_<tool_id>``; the approval is
            checked *before* any process starts and consumed exactly once after
            the attempt, even when the attempt fails.

        Recording
            A ``ToolRun(tier=3)`` is always appended so downstream validation can
            see the failure (``has_failed_tools`` / ``start_validating``) instead
            of the failure being swallowed. Unlike ``record_tool_run`` this does
            not raise ``ToolFailureError``: the ``SandboxResult`` is returned so
            the caller can inspect exit code, timeouts and truncation directly.

        Only bounded metadata (argv[0], argument count, profile name, output
        sizes, exit code) is journalled — never stdin, environment or raw output.
        """
        if self._state is not SessionState.EXECUTING:
            raise InvalidTransitionError(
                f"Cannot execute tier-3 tool in state {self._state.value}. "
                f"Must be EXECUTING."
            )

        approval_id = f"tool_{tool_id}"
        if workspace_parent is None:
            workspace_parent = Path(tempfile.gettempdir())

        request = SandboxRequest(
            argv=tuple(argv),
            profile=profile,
            workspace_parent=Path(workspace_parent),
            approval_id=approval_id,
            stdin=stdin,
            environment=environment,
            network_policy=network_policy,
        )

        result = execute_in_sandbox(
            request,
            check_approval=self._check_approval,
            consume_approval=self._consume_approval,
            audit=self._audit_tier3,
        )

        self._tool_runs.append(
            ToolRun(
                project_id=self.project_id,
                tool_id=tool_id,
                tier=3,
                inputs={
                    "argv0": request.argv[0],
                    "argc": len(request.argv),
                    "profile": profile.name,
                    "network_policy": request.network_policy.value,
                },
                outputs={
                    "exit_code": result.exit_code,
                    "duration_ms": result.duration_ms,
                    "stdout_bytes": len(result.stdout),
                    "stderr_bytes": len(result.stderr),
                    "timed_out": result.timed_out,
                    "output_limited": result.output_limited,
                    "isolation_backend": result.isolation_backend,
                    "isolation_strength": result.isolation_strength,
                },
                ok=result.ok,
                error=result.error_code,
                duration_ms=result.duration_ms,
            )
        )
        return result


    @property
    def has_failed_models(self) -> bool:
        """True when any model run recorded in this segment failed."""
        return any(not run.ok for run in self._model_runs)

    @property
    def model_runs(self) -> list[ModelRun]:
        """Copy of the bounded model-run records for the current segment."""
        return list(self._model_runs)

    def execute_model_task(
        self,
        task_id: str,
        prompt: str,
        router: ModelRouter,
        *,
        actor: str | None = None,
    ) -> ProviderResponse:
        """Run one routed model task and record a bounded ModelRun.

        Lifecycle
            Legal only in REASONING. This call never moves the session between
            states; it records evidence that ``deliver`` then enforces.

        Approval
            BYOK_MODEL tasks require CONFIRM for action id ``model_<task_id>``.
            The approval is checked before the provider is reached, and consumed
            exactly once after the attempt - including when the attempt failed
            or when audit recording itself failed, so a token can never be
            replayed into a second external call.

        Recording
            The ModelRun and the journal payload carry bounded metadata only:
            task_id, tier, provider/model name, usage tokens, duration, a boolean
            and a safe error code (an exception class name). Prompts, model
            output, credentials and raw provider messages are never stored.

        Raises
            InvalidTransitionError    - not in REASONING
            UnknownTaskError          - task_id not in the router policy
            ApprovalRequiredError     - BYOK task without prior CONFIRM approval
            AuditFailureError         - the attempt completed but audit failed
            ApprovalConsumptionError  - the attempt completed but the one-shot
                                        approval could not be consumed
            RouterError subclass      - re-raised unchanged from the router

            When several of these apply the precedence is:
            audit failure > approval-consumption failure > router failure.
        """
        if self._state is not SessionState.REASONING:
            raise InvalidTransitionError(
                f"Cannot execute model task in state {self._state.value}. "
                f"Must be REASONING."
            )

        a = actor or self.default_actor
        tier = router.get_tier(task_id)  # canonical policy; UnknownTaskError if unknown
        approval_id = f"model_{task_id}"

        if tier is ModelTier.BYOK_MODEL:
            self._check_approval(approval_id, ApprovalLevel.CONFIRM)

        start = time.monotonic()
        response: ProviderResponse | None = None
        router_err: Exception | None = None
        audit_err: AuditFailureError | None = None
        consume_err: ApprovalConsumptionError | None = None

        provider_name: str | None = None
        model_name: str | None = None
        usage_tokens: int | None = None
        error_code: str | None = None

        try:
            response = router.complete(task_id, prompt)
            provider_name = response.provider_name
            model_name = response.model_name
            usage_tokens = response.usage_tokens
        except Exception as exc:  # noqa: BLE001 - classified, never re-worded
            router_err = exc
            error_code = type(exc).__name__
        finally:
            # Duration is measured here; the provider contract has no duration.
            duration_ms = int((time.monotonic() - start) * 1000)
            ok = response is not None

            self._model_runs.append(
                ModelRun(
                    task_id=task_id,
                    tier=tier.value,
                    provider_name=provider_name,
                    model_name=model_name,
                    usage_tokens=usage_tokens,
                    duration_ms=duration_ms,
                    ok=ok,
                    error_code=error_code,
                )
            )

            payload: dict = {
                "task_id": task_id,
                "tier": tier.value,
                "ok": ok,
                "duration_ms": duration_ms,
            }
            if ok:
                payload["provider"] = provider_name
                payload["model"] = model_name
                payload["usage_tokens"] = usage_tokens
            else:
                payload["error_code"] = error_code

            # Audit first, but never at the cost of leaving a live token: the
            # consumption attempt below runs even when this write raises.
            try:
                self._write_journal(
                    a, "execute_model_task", subject=task_id, payload=payload
                )
            except Exception as exc:  # noqa: BLE001
                audit_err = AuditFailureError(
                    f"model task {task_id!r} attempted but audit recording "
                    f"failed: {type(exc).__name__}"
                )

            if tier is ModelTier.BYOK_MODEL:
                try:
                    self._consume_approval(approval_id, ApprovalLevel.CONFIRM)
                except Exception as exc:  # noqa: BLE001
                    consume_err = ApprovalConsumptionError(
                        f"model task {task_id!r} attempted but approval "
                        f"consumption failed: {type(exc).__name__}"
                    )

        if audit_err is not None:
            raise audit_err
        if consume_err is not None:
            raise consume_err
        if router_err is not None:
            raise router_err
        assert response is not None  # unreachable: ok implies response was set
        return response


    def start_validating(self, *, actor: str | None = None) -> TransitionRecord:
        a = actor or self.default_actor
        if not self._tool_runs:
            raise InvalidTransitionError("Cannot start validating without at least one tool run.")
        if any(not r.ok for r in self._tool_runs):
            raise InvalidTransitionError("Cannot start validating with failed tool runs.")
            
        return self._transition(
            {SessionState.EXECUTING},
            SessionState.VALIDATING,
            actor=a, action="start_validating", payload={"run_count": len(self._tool_runs)}
        )

    def start_reasoning(self, claims: list[QuantityClaim], *, actor: str | None = None) -> TransitionRecord:
        a = actor or self.default_actor
        if any(not r.ok for r in self._tool_runs):
            raise InvalidTransitionError("Cannot start reasoning with failed tool runs.")
            
        engine = CheckMate()
        for idx, claim in enumerate(claims):
            report = engine.verify_claim(claim)
            if not report.passed:
                self._write_journal(a, "checkmate_failed_validation", subject=claim.description, payload=report.as_dict())
                raise InvalidTransitionError(f"Claim {idx} failed validation: {report.badge()}")

        return self._transition(
            {SessionState.VALIDATING},
            SessionState.REASONING,
            actor=a, action="start_reasoning", payload={"claim_count": len(claims)}
        )

    def deliver(self, response_text: str, claims: list[QuantityClaim], *, actor: str | None = None) -> TransitionRecord:
        a = actor or self.default_actor

        # A failed model execution is an evidence failure: it can never be
        # reasoned away. CheckMate below remains the final gate for claims.
        if self.has_failed_models:
            raise InvalidTransitionError(
                "Cannot deliver with failed model runs."
            )
        
        engine = CheckMate()
        for idx, claim in enumerate(claims):
            report = engine.verify_claim(claim)
            if not report.passed:
                self._write_journal(a, "checkmate_failed_delivery", subject=claim.description, payload=report.as_dict())
                raise InvalidTransitionError(f"Claim {idx} failed delivery verification: {report.badge()}")

        return self._transition(
            {SessionState.REASONING},
            SessionState.DELIVERED,
            actor=a, action="deliver", payload={"response_length": len(response_text), "claim_count": len(claims)}
        )

    def approve(self, action_id: str, level: ApprovalLevel = ApprovalLevel.CONFIRM, *, actor: str | None = None) -> None:
        if self._state == SessionState.DELIVERED:
            raise InvalidTransitionError("Cannot approve actions after delivery.")
        a = actor or self.default_actor
        self._approvals[action_id] = level
        self._write_journal(a, "approve", subject=action_id, payload={"level": level.value})

    def restart(self, reason: str, *, actor: str | None = None) -> TransitionRecord:
        a = actor or self.default_actor
        if self._state == SessionState.EXECUTING:
            to_state = SessionState.RECEIVED
        elif self._state == SessionState.VALIDATING:
            to_state = SessionState.EXECUTING
        elif self._state == SessionState.REASONING:
            to_state = SessionState.VALIDATING
        else:
            raise InvalidTransitionError(f"Cannot restart from {self._state.value}")
            
        return self._transition(
            {SessionState.EXECUTING, SessionState.VALIDATING, SessionState.REASONING},
            to_state,
            actor=a, action="restart", payload={"reason": reason}
        )