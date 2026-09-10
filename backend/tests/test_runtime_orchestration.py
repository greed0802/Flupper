"""Tests for Phase 3D: model orchestration inside AgentSession.

All providers are fake in-process objects. No network calls, no SDK imports,
no credentials in source. Fake providers count their own invocations so every
test asserts the provider was (or was not) actually reached - no hollow 0 == 0.
"""

from __future__ import annotations

import json

import pytest

from qsagent.contracts.evidence import (
    ApprovalLevel,
    EvidenceRef,
    Quantity,
    QuantityClaim,
    ToolRun,
    Unit,
)
from qsagent.runtime import (
    AgentSession,
    ApprovalRequiredError,
    InvalidTransitionError,
    ModelRouter,
    ModelRun,
    ModelTier,
    ProviderResponse,
    SessionState,
    ToolFailureError,
)
from qsagent.runtime.router import MalformedResponseError, MissingCredentialError
from qsagent.storage import QSStore

LOCAL_TASK = "metadata"           # policy: LOCAL_MODEL
BYOK_TASK = "document_synthesis"  # policy: BYOK_MODEL
PROMPT_SENTINEL = "PROMPT-SENTINEL-4f2b"
SECRET_SENTINEL = "sk-SECRET-SENTINEL-1234"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeSecretProvider:
    """Explicit key mapping. Missing key -> None. Counts lookups."""

    def __init__(self, keys: dict[str, str | None] | None = None) -> None:
        self._keys = keys or {}
        self.call_count: dict[str, int] = {}

    def get_key(self, provider: str) -> str | None:
        self.call_count[provider] = self.call_count.get(provider, 0) + 1
        return self._keys.get(provider)


class FakeProvider:
    """Configurable provider double. Records calls and the key it was given."""

    def __init__(self, name, tier, *, response=None, raises=None) -> None:
        self.name = name
        self.supported_tier = tier
        self._response = response
        self._raises = raises
        self.call_count = 0
        self.received_keys: list[str | None] = []

    def complete(self, prompt, *, api_key):
        self.call_count += 1
        self.received_keys.append(api_key)
        if self._raises is not None:
            raise self._raises
        return self._response


def ok_response(provider_name: str = "fake_local", *, text: str = "ok", tokens: int = 7) -> ProviderResponse:
    return ProviderResponse(
        text=text,
        provider_name=provider_name,
        model_name=f"{provider_name}-1",
        usage_tokens=tokens,
    )


def make_router(*, local=None, byok=None, keys=None) -> ModelRouter:
    router = ModelRouter(FakeSecretProvider(keys))
    if local is not None:
        router.register(local)
    if byok is not None:
        router.register(byok)
    return router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_claim(state: str = "m3_insitu", conv: bool = False) -> QuantityClaim:
    return QuantityClaim(
        project_id=1,
        description="Test",
        quantity=Quantity(value=72.0, unit=Unit.M3),
        method="trench.volume",
        evidence=[EvidenceRef(file_hash="a" * 64, file_name="f", sheet="1", raw_text="72")],
        workings=["working"],
        measurement_state=state,
        conversion_applied=conv,
    )


def make_tool(tier: int = 1, ok: bool = True, err=None) -> ToolRun:
    return ToolRun(
        project_id=1,
        tool_id="trench.volume",
        tier=tier,
        inputs={"depth_m": 1.0},
        outputs={"volume_m3": 72.0},
        ok=ok,
        error=err,
    )


def session_in_reasoning(store=None, project_id: int = 1) -> AgentSession:
    """Drive a session to REASONING with one successful tool run recorded."""
    session = AgentSession(project_id, store=store)
    session.plan({"steps": 1})
    session.start_executing()
    session.record_tool_run(make_tool())
    session.start_validating()
    session.start_reasoning([make_claim()])
    assert session.state is SessionState.REASONING
    return session


# ---------------------------------------------------------------------------
# 1. LOCAL_MODEL needs no approval and needs no credential
# ---------------------------------------------------------------------------

def test_local_model_succeeds():
    local = FakeProvider("fake_local", ModelTier.LOCAL_MODEL, response=ok_response())
    secrets = FakeSecretProvider({})
    router = ModelRouter(secrets)
    router.register(local)

    session = session_in_reasoning()
    result = session.execute_model_task(LOCAL_TASK, PROMPT_SENTINEL, router)

    assert isinstance(result, ProviderResponse)
    assert result.text == "ok"
    assert local.call_count == 1          # the provider really was invoked
    assert local.received_keys == [None]  # local tier carries no credential
    assert secrets.call_count == {}       # and no key lookup is attempted

    runs = session.model_runs
    assert len(runs) == 1
    run = runs[0]
    assert isinstance(run, ModelRun)
    assert run.ok is True
    assert run.task_id == LOCAL_TASK
    assert run.tier == "LOCAL_MODEL"
    assert run.provider_name == "fake_local"
    assert run.model_name == "fake_local-1"
    assert run.usage_tokens == 7
    assert run.duration_ms >= 0
    assert run.error_code is None
    assert session.has_failed_models is False

    session.deliver("done", [make_claim()])
    assert session.state is SessionState.DELIVERED


# ---------------------------------------------------------------------------
# 2. BYOK_MODEL is gated behind CONFIRM approval
# ---------------------------------------------------------------------------

def test_byok_requires_approval():
    byok = FakeProvider("fake_byok", ModelTier.BYOK_MODEL, response=ok_response("fake_byok"))
    router = make_router(byok=byok, keys={"fake_byok": SECRET_SENTINEL})
    session = session_in_reasoning()

    with pytest.raises(ApprovalRequiredError):
        session.execute_model_task(BYOK_TASK, PROMPT_SENTINEL, router)

    assert byok.call_count == 0        # refused before any provider traffic
    assert session.model_runs == []    # nothing executed, so nothing recorded
    assert session.has_failed_models is False
    assert session.state is SessionState.REASONING


# ---------------------------------------------------------------------------
# 3. A failed BYOK call is evidence and blocks delivery
# ---------------------------------------------------------------------------

def test_byok_blocks_delivery_on_failure():
    byok = FakeProvider("fake_byok", ModelTier.BYOK_MODEL, response=ok_response("fake_byok"))
    router = make_router(byok=byok, keys={})   # no key -> MissingCredentialError
    session = session_in_reasoning()
    session.approve(f"model_{BYOK_TASK}", ApprovalLevel.CONFIRM)

    with pytest.raises(MissingCredentialError):
        session.execute_model_task(BYOK_TASK, PROMPT_SENTINEL, router)

    assert byok.call_count == 0        # key resolution failed before the call
    assert session.has_failed_models is True
    runs = session.model_runs
    assert len(runs) == 1
    assert runs[0].ok is False
    assert runs[0].error_code == "MissingCredentialError"
    assert runs[0].provider_name is None   # never guessed on a redacted failure
    assert runs[0].usage_tokens is None

    with pytest.raises(InvalidTransitionError):
        session.deliver("done", [make_claim()])
    assert session.state is SessionState.REASONING

    # The token was consumed even though the attempt failed: not reusable.
    with pytest.raises(ApprovalRequiredError):
        session.execute_model_task(BYOK_TASK, PROMPT_SENTINEL, router)


# ---------------------------------------------------------------------------
# 4. A structurally invalid response is a failure, not a success
# ---------------------------------------------------------------------------

def test_malformed_response_prevents_delivery():
    byok = FakeProvider("fake_byok", ModelTier.BYOK_MODEL, response={"text": "not a response"})
    router = make_router(byok=byok, keys={"fake_byok": SECRET_SENTINEL})
    session = session_in_reasoning()
    session.approve(f"model_{BYOK_TASK}", ApprovalLevel.CONFIRM)

    with pytest.raises(MalformedResponseError):
        session.execute_model_task(BYOK_TASK, PROMPT_SENTINEL, router)

    assert byok.call_count == 1
    run = session.model_runs[0]
    assert run.ok is False
    assert run.error_code == "MalformedResponseError"
    assert run.usage_tokens is None
    assert session.has_failed_models is True

    with pytest.raises(InvalidTransitionError):
        session.deliver("done", [make_claim()])


# ---------------------------------------------------------------------------
# 5. Model tasks are legal only in REASONING
# ---------------------------------------------------------------------------

def test_model_call_strict_lifecycle():
    local = FakeProvider("fake_local", ModelTier.LOCAL_MODEL, response=ok_response())
    router = make_router(local=local)

    received = AgentSession(1)
    with pytest.raises(InvalidTransitionError):
        received.execute_model_task(LOCAL_TASK, PROMPT_SENTINEL, router)

    executing = AgentSession(1)
    executing.plan({})
    executing.start_executing()
    with pytest.raises(InvalidTransitionError):
        executing.execute_model_task(LOCAL_TASK, PROMPT_SENTINEL, router)

    assert received.state is SessionState.RECEIVED
    assert executing.state is SessionState.EXECUTING
    assert local.call_count == 0
    assert received.model_runs == [] and executing.model_runs == []


# ---------------------------------------------------------------------------
# 6. Journal metadata is provider-identity only: no prompt, no secret
# ---------------------------------------------------------------------------

def test_journal_provider_metadata_safe():
    store = QSStore(":memory:")
    pid = store.get_or_create_project("T")
    local = FakeProvider("fake_local", ModelTier.LOCAL_MODEL, response=ok_response())
    router = ModelRouter(FakeSecretProvider({}))
    router.register(local)

    session = session_in_reasoning(store, project_id=pid)
    session.execute_model_task(LOCAL_TASK, PROMPT_SENTINEL, router)
    assert local.call_count == 1   # non-hollow: the audited call really happened

    rows = [r for r in store.journal_entries(pid) if r["action"] == "execute_model_task"]
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])

    bounded = {
        "task_id", "tier", "ok", "duration_ms",
        "provider", "model", "usage_tokens", "error_code",
    }
    assert set(payload) <= bounded
    assert payload["task_id"] == LOCAL_TASK
    assert payload["tier"] == "LOCAL_MODEL"
    assert payload["ok"] is True
    assert payload["provider"] == "fake_local"
    assert payload["model"] == "fake_local-1"
    assert payload["usage_tokens"] == 7
    assert isinstance(payload["duration_ms"], int)
    assert payload["duration_ms"] >= 0

    blob = json.dumps([dict(r) for r in store.journal_entries(pid)])
    assert PROMPT_SENTINEL not in blob     # prompt never persisted
    assert SECRET_SENTINEL not in blob     # credential never persisted
    assert "ok_response" not in blob       # model output never persisted


# ---------------------------------------------------------------------------
# 7. One-shot approval, and restart clears failed model evidence
# ---------------------------------------------------------------------------

def test_approval_consumed_and_restart():
    byok = FakeProvider("fake_byok", ModelTier.BYOK_MODEL, response=ok_response("fake_byok"))
    router = make_router(byok=byok, keys={"fake_byok": SECRET_SENTINEL})
    session = session_in_reasoning()

    session.approve(f"model_{BYOK_TASK}", ApprovalLevel.CONFIRM)
    first = session.execute_model_task(BYOK_TASK, PROMPT_SENTINEL, router)
    assert first.text == "ok"
    assert byok.received_keys == [SECRET_SENTINEL]
    assert session.has_failed_models is False

    # One-shot: the consumed token cannot authorise a second call.
    with pytest.raises(ApprovalRequiredError):
        session.execute_model_task(BYOK_TASK, PROMPT_SENTINEL, router)
    assert byok.call_count == 1

    # Fail a run so the segment carries a failed model record.
    no_key_router = make_router(byok=byok, keys={})
    session.approve(f"model_{BYOK_TASK}", ApprovalLevel.CONFIRM)
    with pytest.raises(MissingCredentialError):
        session.execute_model_task(BYOK_TASK, PROMPT_SENTINEL, no_key_router)
    assert session.has_failed_models is True

    # Restart is the boundary: the failure evidence belongs to the old segment.
    session.restart("retry after credential fix")
    assert session.state is SessionState.VALIDATING
    assert not session.has_failed_models
    assert session.model_runs == []

    session.start_reasoning([make_claim()])
    session.approve(f"model_{BYOK_TASK}", ApprovalLevel.CONFIRM)
    session.execute_model_task(BYOK_TASK, PROMPT_SENTINEL, router)
    assert session.has_failed_models is False
    assert byok.call_count == 2

    session.deliver("done", [make_claim()])
    assert session.state is SessionState.DELIVERED


# ---------------------------------------------------------------------------
# 8. A clean model layer does not bypass CheckMate
# ---------------------------------------------------------------------------

def test_checkmate_overrides_success():
    store = QSStore(":memory:")
    pid = store.get_or_create_project("T")
    local = FakeProvider("fake_local", ModelTier.LOCAL_MODEL, response=ok_response())
    router = make_router(local=local)
    session = session_in_reasoning(store, project_id=pid)

    session.execute_model_task(LOCAL_TASK, PROMPT_SENTINEL, router)
    assert local.call_count == 1
    assert session.has_failed_models is False   # model layer is clean here

    bad = make_claim(state="UNRESOLVED", conv=True)
    with pytest.raises(InvalidTransitionError):
        session.deliver("done", [bad])

    assert session.state is SessionState.REASONING
    actions = [r["action"] for r in store.journal_entries(pid)]
    assert actions.count("checkmate_failed_delivery") == 1


# ---------------------------------------------------------------------------
# 9. Tier 3 sandbox behaviour is untouched by the model registry
# ---------------------------------------------------------------------------

def test_no_silent_fallback_tier3():
    session = AgentSession(1)
    session.plan({})
    session.start_executing()

    # CONFIRM is required before a tier-3 run is recorded at all.
    with pytest.raises(ApprovalRequiredError):
        session.record_tool_run(make_tool(tier=3, ok=False, err="boom"))
    assert session.has_failed_tools is False

    session.approve("tool_trench.volume", ApprovalLevel.CONFIRM)
    with pytest.raises(ToolFailureError):
        session.record_tool_run(make_tool(tier=3, ok=False, err="boom"))

    assert session.has_failed_tools is True
    assert session.has_failed_models is False   # tool failures never touch models
    assert session.model_runs == []

    with pytest.raises(InvalidTransitionError):
        session.start_validating()

    session.restart("tool failed")
    assert session.state is SessionState.RECEIVED
    assert session.has_failed_tools is False
    assert session.has_failed_models is False
    assert session.model_runs == []
