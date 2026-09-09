import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from ..checkmate.engine import CheckMate
from ..contracts.evidence import ApprovalLevel, QuantityClaim, ToolRun
from ..storage.db import QSStore

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

class AgentSession:
    def __init__(self, project_id: int, *, store: QSStore | None = None, actor: str = "agent") -> None:
        self.project_id = project_id
        self._store = store
        self.default_actor = actor
        self._state = SessionState.RECEIVED
        self._history: list[TransitionRecord] = []
        self._tool_runs: list[ToolRun] = []
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