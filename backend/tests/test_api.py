"""HTTP gateway tests (Phase 4, task A).

These drive the real ``create_app`` object - the same routes, the same
middleware stack, the same error mapping that ``run_local`` serves. Only the
collaborators change: an in-memory store and a router holding recording test
doubles, so the suite needs no network, no credentials and no fixtures on disk.

The security assertions are the point of this file, so each one is written to be
falsifiable rather than reassuring:

* a nonce is bound to an exact argv vector - the test mints for one payload and
  executes another, and requires that to fail;
* a nonce is single-use - the test approves twice and then proves no second
  execution became possible;
* a nonce dies with its session segment - the test restarts the session between
  mint and approve;
* an unapproved Tier 3 request is refused *before* a process starts - the test
  runs exactly the argv the nonce was minted for, so the only thing missing is
  the approval;
* a rejected request must not become an oracle - the tests assert the offending
  input is absent from the echoed message;
* concurrency is tested against a real uvicorn server on a real socket, not
  against ``TestClient``, because the bug being guarded against (two threads
  both seeing "no session yet") does not exist in a single-threaded client.

Run from ``backend/`` or the repo root.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest
import uvicorn
from fastapi.testclient import TestClient

from qsagent.api import ApprovalError, ApprovalRegistry, create_app
from qsagent.api.approvals import (
    MAX_PENDING_APPROVALS,
    canonical_request_hash,
    check_binding,
)
from qsagent.api.contracts import (
    MAX_ARGV_ITEM_CHARS,
    MAX_ARGV_ITEMS,
    MAX_OUTPUT_CHARS,
    MAX_PROMPT_CHARS,
)
from qsagent.api.limits import BodySizeLimitMiddleware
from qsagent.runtime import ModelRouter, ModelTier, ProviderResponse
from qsagent.storage import QSStore

API = "/api/v1"

# Task ids taken from the router's canonical policy table, not invented here.
BYOK_TASK = "document_synthesis"
LOCAL_TASK = "summary"


# --------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------
class RecordingSecretProvider:
    """Structural ``SecretProvider``; records what was asked for."""

    def __init__(self, keys: dict[str, str] | None = None) -> None:
        self._keys = dict(keys or {})
        self.asked: list[str] = []

    def get_key(self, provider: str) -> str | None:
        self.asked.append(provider)
        return self._keys.get(provider)


class RecordingProvider:
    """Structural ``ModelProvider`` that records the prompts it received."""

    def __init__(self, name: str, tier: ModelTier, text: str = "model-answer") -> None:
        self._name = name
        self._tier = tier
        self._text = text
        self.calls: list[dict] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def supported_tier(self) -> ModelTier:
        return self._tier

    def complete(self, prompt: str, *, api_key: str | None) -> ProviderResponse:
        self.calls.append({"prompt": prompt, "api_key": api_key})
        return ProviderResponse(
            text=self._text,
            provider_name=self._name,
            model_name=f"{self._name}-v1",
            usage_tokens=11,
        )


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture()
def store():
    st = QSStore(":memory:")
    yield st
    st.close()


@pytest.fixture()
def secrets():
    return RecordingSecretProvider({"fakecloud": "test-key-not-real"})


@pytest.fixture()
def provider():
    return RecordingProvider("fakecloud", ModelTier.BYOK_MODEL)


@pytest.fixture()
def router(secrets, provider):
    r = ModelRouter(secrets)
    r.register(provider)
    return r


@pytest.fixture()
def app(tmp_path, store, router):
    return create_app(
        store=store,
        router=router,
        sandbox_root=tmp_path / "sandboxes",
    )


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def new_project(client: TestClient, name: str = "gateway-test") -> int:
    resp = client.post(f"{API}/projects", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["project_id"]


def plan(client: TestClient, project_id: int) -> int:
    resp = client.post(f"{API}/projects/{project_id}/session/plan", json={"steps": 1})
    assert resp.status_code == 200, resp.text
    return project_id


def start_executing(client: TestClient, project_id: int) -> int:
    resp = client.post(f"{API}/projects/{project_id}/session/start_executing")
    assert resp.status_code == 200, resp.text
    return project_id


def executing_project(client: TestClient, name: str = "exec-project") -> int:
    return start_executing(client, plan(client, new_project(client, name)))


@pytest.fixture()
def project(client):
    """A project whose session is already EXECUTING - the Tier 3 precondition."""
    return executing_project(client)


def tier3_body(
    argv,
    *,
    profile: str = "safe",
    network_policy: str = "BEST_EFFORT",
) -> dict:
    return {"argv": list(argv), "profile": profile, "network_policy": network_policy}


def echo_body(marker: str) -> dict:
    """A Tier 3 payload whose stdout is exactly *marker*.

    ``sys.executable`` with ``-c`` is the only way to get a deterministic,
    dependency-free child process on both Windows and POSIX.
    """
    return tier3_body([sys.executable, "-c", "import sys; print(sys.argv[1])", marker])


def mint(client: TestClient, project_id: int, body: dict):
    return client.post(
        f"{API}/projects/{project_id}/session/request_tier3_approval", json=body
    )


def approve(client: TestClient, project_id: int, nonce: str):
    return client.post(
        f"{API}/projects/{project_id}/session/approve", json={"nonce": nonce}
    )


def execute(client: TestClient, project_id: int, body: dict):
    return client.post(f"{API}/projects/{project_id}/session/execute_tier3", json=body)


def authorize(client: TestClient, project_id: int, body: dict) -> str:
    """Run the full mint -> approve handshake for one exact body."""
    minted = mint(client, project_id, body)
    assert minted.status_code == 200, minted.text
    granted = approve(client, project_id, minted.json()["nonce"])
    assert granted.status_code == 200, granted.text
    return minted.json()["action_id"]


# --------------------------------------------------------------------------
# Body-limit harness
#
# The middleware is pure ASGI, so it can be driven directly: no server, no
# client library, and full control over how the body is chunked and whether a
# Content-Length is declared. That control is the whole point - a chunked body
# is the case the declared-length shortcut does not cover.
# --------------------------------------------------------------------------
class _BodyReader:
    """Minimal ASGI app that drains the body and reports what it received."""

    def __init__(self) -> None:
        self.received = 0
        self.error: BaseException | None = None

    async def __call__(self, scope, receive, send) -> None:
        try:
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    break
                self.received += len(message.get("body", b"") or b"")
                if not message.get("more_body"):
                    break
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
        except BaseException as exc:  # noqa: BLE001 - observing it is the test
            self.error = exc
            raise


def _drive(middleware, scope: dict, chunks: list[dict]) -> list[dict]:
    """Run *middleware* once against a scripted receive channel."""
    sent: list[dict] = []
    pending = list(chunks)

    async def receive():
        if pending:
            return pending.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(middleware(scope, receive, send))
    return sent


def _http_scope(headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/projects",
        "raw_path": b"/api/v1/projects",
        "query_string": b"",
        "root_path": "",
        "headers": list(headers or []),
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
    }


def _request_chunks(*parts: bytes, last_has_more: bool = False) -> list[dict]:
    out = []
    for index, part in enumerate(parts):
        more = index < len(parts) - 1 or last_has_more
        out.append({"type": "http.request", "body": part, "more_body": more})
    return out


class TestBodyLimitMiddleware:
    """Drives ``BodySizeLimitMiddleware`` directly."""

    def test_declared_length_over_limit_never_reaches_the_app(self):
        reader = _BodyReader()
        middleware = BodySizeLimitMiddleware(reader, max_body_bytes=16)
        scope = _http_scope([(b"content-length", b"9999")])

        sent = _drive(middleware, scope, [])

        assert sent[0]["type"] == "http.response.start"
        assert sent[0]["status"] == 413
        assert reader.received == 0, "the app must not be entered at all"
        assert reader.error is None

    def test_declared_length_under_limit_is_forwarded_untouched(self):
        reader = _BodyReader()
        middleware = BodySizeLimitMiddleware(reader, max_body_bytes=16)
        scope = _http_scope([(b"content-length", b"5")])

        sent = _drive(middleware, scope, _request_chunks(b"hello"))

        assert reader.received == 5
        assert reader.error is None
        assert sent[0]["status"] == 200

    def test_chunked_body_over_limit_is_rejected(self):
        """No Content-Length: the ceiling must be enforced while streaming."""
        reader = _BodyReader()
        middleware = BodySizeLimitMiddleware(reader, max_body_bytes=8)

        sent = _drive(middleware, _http_scope(), _request_chunks(b"12345", b"6789AB"))

        # The first chunk is under the ceiling, so it was forwarded exactly as
        # received before the second chunk tripped the limit.
        assert reader.received == 5
        assert sent[0]["type"] == "http.response.start"
        assert sent[0]["status"] == 413
        body = b"".join(
            message.get("body", b"") for message in sent if "body" in message
        )
        assert json.loads(body)["error"] == "RequestTooLarge"

    def test_chunked_body_under_limit_passes_through(self):
        reader = _BodyReader()
        middleware = BodySizeLimitMiddleware(reader, max_body_bytes=64)

        sent = _drive(middleware, _http_scope(), _request_chunks(b"12345", b"678"))

        assert reader.received == 8
        assert reader.error is None
        assert sent[0]["status"] == 200

    def test_non_positive_limit_is_refused_at_construction(self):
        with pytest.raises(ValueError):
            BodySizeLimitMiddleware(_BodyReader(), max_body_bytes=0)


# --------------------------------------------------------------------------
# Approval registry (unit)
# --------------------------------------------------------------------------
class TestApprovalRegistry:
    """The capability layer, tested without HTTP so the failures are precise."""

    def _issue(self, registry: ApprovalRegistry, **overrides):
        kwargs = {
            "kind": "tier3",
            "action_id": "tool_abc",
            "request_hash": "hash-1",
            "project_id": 1,
            "segment_id": "segment-a",
        }
        kwargs.update(overrides)
        return registry.issue(**kwargs)

    def test_nonce_is_single_use(self):
        registry = ApprovalRegistry()
        record = self._issue(registry)
        assert len(registry) == 1, "the nonce must actually have been stored"

        first = registry.consume(record.nonce)
        assert first.nonce == record.nonce
        with pytest.raises(ApprovalError):
            registry.consume(record.nonce)
        assert len(registry) == 0

    def test_peek_does_not_consume(self):
        registry = ApprovalRegistry()
        record = self._issue(registry)

        peeked = registry.peek(record.nonce)

        assert peeked == record
        assert len(registry) == 1, "peek must leave the nonce spendable"

    def test_nonce_expires(self):
        registry = ApprovalRegistry(ttl_seconds=0.02)
        record = self._issue(registry)
        assert not record.expired(time.time())

        time.sleep(0.05)

        assert record.expired(time.time())
        with pytest.raises(ApprovalError):
            registry.peek(record.nonce)
        assert len(registry) == 0, "an expired nonce must not linger in memory"

    def test_unknown_nonce_is_refused(self):
        registry = ApprovalRegistry()
        with pytest.raises(ApprovalError):
            registry.peek("0" * 32)

    def test_prefix_of_a_real_nonce_is_refused(self):
        registry = ApprovalRegistry()
        record = self._issue(registry)
        with pytest.raises(ApprovalError):
            registry.peek(record.nonce[:-1])

    def test_capacity_is_enforced(self):
        registry = ApprovalRegistry(max_pending=3)
        for _ in range(6):
            self._issue(registry)
        assert len(registry) == 3

    def test_unknown_kind_is_refused(self):
        registry = ApprovalRegistry()
        with pytest.raises(ValueError):
            self._issue(registry, kind="not-a-kind")

    def test_blank_fields_are_refused(self):
        registry = ApprovalRegistry()
        with pytest.raises(ValueError):
            self._issue(registry, action_id="")
        with pytest.raises(ValueError):
            self._issue(registry, request_hash="")
        with pytest.raises(ValueError):
            self._issue(registry, segment_id="")

    def test_purge_project_is_scoped(self):
        registry = ApprovalRegistry()
        self._issue(registry, project_id=1)
        self._issue(registry, project_id=1)
        other = self._issue(registry, project_id=2)

        assert registry.purge_project(1) == 2
        assert len(registry) == 1
        assert registry.peek(other.nonce) == other

    def test_binding_survives_until_it_is_spent(self):
        registry = ApprovalRegistry()
        registry.bind(project_id=1, action_id="model_x", request_hash="hash-1")

        # Recorded at approve time, spent at execution time - exactly once, so
        # a second execution cannot reuse the operator's decision.
        registry.require_binding(
            project_id=1, action_id="model_x", request_hash="hash-1"
        )
        with pytest.raises(ApprovalError):
            registry.require_binding(
                project_id=1, action_id="model_x", request_hash="hash-1"
            )

    def test_binding_that_was_never_recorded_is_refused(self):
        registry = ApprovalRegistry()
        with pytest.raises(ApprovalError):
            registry.require_binding(
                project_id=1, action_id="model_x", request_hash="hash-1"
            )

    def test_mismatched_binding_is_refused_and_consumed(self):
        registry = ApprovalRegistry()
        registry.bind(project_id=1, action_id="model_x", request_hash="hash-1")

        with pytest.raises(ApprovalError):
            registry.require_binding(
                project_id=1, action_id="model_x", request_hash="hash-2"
            )
        # A mismatch spends the binding rather than leaving it to be retried.
        with pytest.raises(ApprovalError):
            registry.require_binding(
                project_id=1, action_id="model_x", request_hash="hash-1"
            )

    def test_purge_project_clears_bindings_too(self):
        registry = ApprovalRegistry()
        registry.bind(project_id=1, action_id="model_x", request_hash="hash-1")
        registry.bind(project_id=2, action_id="model_x", request_hash="hash-2")

        registry.purge_project(1)

        with pytest.raises(ApprovalError):
            registry.require_binding(
                project_id=1, action_id="model_x", request_hash="hash-1"
            )
        registry.require_binding(
            project_id=2, action_id="model_x", request_hash="hash-2"
        )

    @pytest.mark.parametrize(
        "override",
        [
            {"project_id": 99},
            {"segment_id": "segment-b"},
            {"request_hash": "hash-2"},
        ],
        ids=["wrong-project", "wrong-segment", "wrong-payload"],
    )
    def test_check_binding_rejects_every_mismatch(self, override):
        registry = ApprovalRegistry()
        record = self._issue(registry)
        expected = {
            "project_id": 1,
            "segment_id": "segment-a",
            "request_hash": "hash-1",
        }
        expected.update(override)

        with pytest.raises(ApprovalError) as excinfo:
            check_binding(record, **expected)

        # One message for every mismatch: a caller must learn that its nonce did
        # not apply, never which part of the binding failed.
        assert str(excinfo.value) == "approval nonce does not apply to this request"

    def test_check_binding_accepts_an_exact_match(self):
        registry = ApprovalRegistry()
        record = self._issue(registry)
        check_binding(
            record, project_id=1, segment_id="segment-a", request_hash="hash-1"
        )

    def test_canonical_hash_ignores_mapping_order(self):
        a = canonical_request_hash({"argv": ["a", "b"], "profile": "safe"})
        b = canonical_request_hash({"profile": "safe", "argv": ["a", "b"]})
        assert a == b

    def test_canonical_hash_is_sensitive_to_argv_order(self):
        first = canonical_request_hash({"argv": ["a", "b"]})
        second = canonical_request_hash({"argv": ["b", "a"]})
        assert first != second


# --------------------------------------------------------------------------
# Contract surface
# --------------------------------------------------------------------------
class TestContractSurface:
    def test_health_is_open_and_reports_sandbox_readiness(self, client):
        resp = client.get(f"{API}/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "sandbox_root_ready": True}

    def test_docs_and_schema_are_not_published(self, client):
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404, path

    def test_unknown_field_is_rejected(self, client):
        resp = client.post(f"{API}/projects", json={"name": "x", "extra": True})
        assert resp.status_code == 422
        # Field *locations* are reported, never the values that failed.
        assert resp.json()["fields"] == ["body.extra"]

    def test_project_id_must_be_an_integer(self, client):
        assert client.get(f"{API}/projects/not-a-number").status_code == 422

    def test_unknown_project_is_404(self, client):
        resp = client.get(f"{API}/projects/999999")
        assert resp.status_code == 404
        # Starlette's HTTPException name, echoed through the same shape as
        # every other failure - callers never have to guess the schema.
        assert set(resp.json()) == {"error", "message"}
        assert resp.json()["error"] == "HTTPException"


# --------------------------------------------------------------------------
# Tier 3: mint -> approve -> execute
# --------------------------------------------------------------------------
class TestTier3ApprovalFlow:
    def test_execute_before_any_approval_is_refused(self, client, project):
        """Same argv throughout, so the *only* missing thing is the approval."""
        body = echo_body("unapproved")

        resp = execute(client, project, body)

        assert resp.status_code == 403
        assert resp.json()["error"] == "ApprovalRequiredError"
        state = client.get(f"{API}/projects/{project}/session").json()
        assert state["tool_runs"] == 0, "no process may start without approval"
        assert state["failed_tools"] is False

    def test_minted_but_unapproved_nonce_still_refuses(self, client, project):
        body = echo_body("minted-only")
        assert mint(client, project, body).status_code == 200

        resp = execute(client, project, body)

        assert resp.status_code == 403
        assert client.get(f"{API}/projects/{project}/session").json()["tool_runs"] == 0

    def test_approved_request_runs_exactly_that_argv(self, client, project):
        marker = "approved-payload-4471"
        body = echo_body(marker)

        authorize(client, project, body)
        resp = execute(client, project, body)

        assert resp.status_code == 200, resp.text
        result = resp.json()
        assert result["ok"] is True
        assert result["stdout"].strip() == marker
        assert result["timed_out"] is False
        assert result["isolation_backend"]
        state = client.get(f"{API}/projects/{project}/session").json()
        assert state["tool_runs"] == 1
        assert state["failed_tools"] is False

    def test_nonce_for_one_argv_does_not_authorise_another(self, client, project):
        authorize(client, project, echo_body("harmless"))

        resp = execute(client, project, echo_body("something-else"))

        assert resp.status_code == 403
        assert resp.json()["error"] == "ApprovalRequiredError"
        assert client.get(f"{API}/projects/{project}/session").json()["tool_runs"] == 0

    def test_nonce_does_not_authorise_a_different_profile(self, client, project):
        authorized = echo_body("profile-bound")
        authorize(client, project, authorized)
        mutated = dict(authorized, profile="development")

        assert execute(client, project, mutated).status_code == 403

    def test_approving_twice_grants_nothing_extra(self, client, project):
        body = echo_body("once-only")
        nonce = mint(client, project, body).json()["nonce"]

        assert approve(client, project, nonce).status_code == 200
        assert approve(client, project, nonce).status_code == 403

        # One grant was issued, so exactly one execution can happen - the
        # duplicate approve must not have topped the approval back up.
        assert execute(client, project, body).status_code == 200
        assert execute(client, project, body).status_code == 403

    def test_nonce_from_another_project_is_refused(self, client, project):
        other = executing_project(client, "other-project")
        nonce = mint(client, project, echo_body("cross-project")).json()["nonce"]

        resp = approve(client, other, nonce)

        assert resp.status_code == 403
        assert resp.json()["error"] == "ApprovalError"

    def test_restart_kills_outstanding_nonces(self, client, project):
        body = echo_body("stale-segment")
        nonce = mint(client, project, body).json()["nonce"]
        assert (
            client.post(
                f"{API}/projects/{project}/session/restart",
                json={"reason": "test rotation"},
            ).status_code
            == 200
        )

        resp = approve(client, project, nonce)

        assert resp.status_code == 403
        assert resp.json()["error"] == "ApprovalError"

    def test_expired_nonce_is_refused(self, tmp_path, store, router):
        app = create_app(
            store=store,
            router=router,
            sandbox_root=tmp_path / "sandboxes",
            approval_ttl_seconds=0.05,
        )
        with TestClient(app) as c:
            project_id = executing_project(c)
            body = echo_body("slow-approver")
            nonce = mint(c, project_id, body).json()["nonce"]
            time.sleep(0.15)

            resp = approve(c, project_id, nonce)

            assert resp.status_code == 403
            assert resp.json()["error"] == "ApprovalError"

    def test_unknown_nonce_is_refused(self, client, project):
        resp = approve(client, project, "f" * 32)
        assert resp.status_code == 403
        assert resp.json()["error"] == "ApprovalError"

    def test_tier3_needs_an_executing_session(self, client):
        project_id = plan(client, new_project(client, "not-executing"))
        body = echo_body("wrong-state")
        authorize(client, project_id, body)

        resp = execute(client, project_id, body)

        assert resp.status_code == 409
        assert resp.json()["error"] == "InvalidTransitionError"

    def test_tier3_for_an_unknown_project_is_404(self, client):
        resp = execute(client, 999999, echo_body("no-such-project"))
        assert resp.status_code == 404

    def test_session_of_a_project_without_one_is_404(self, client):
        project_id = new_project(client, "no-session-yet")
        resp = client.get(f"{API}/projects/{project_id}/session")
        assert resp.status_code == 404


# --------------------------------------------------------------------------
# Tier 3: request validation and bounds
# --------------------------------------------------------------------------
class TestTier3RequestValidation:
    def test_empty_argv_is_rejected(self, client, project):
        resp = execute(client, project, {"argv": [], "profile": "safe"})
        assert resp.status_code == 422
        assert resp.json()["fields"] == ["body.argv"]

    def test_argv_item_count_is_bounded(self, client, project):
        body = tier3_body([sys.executable] * (MAX_ARGV_ITEMS + 1))

        resp = execute(client, project, body)

        assert resp.status_code == 422
        assert resp.json()["fields"] == ["body.argv"]

    def test_argv_item_length_is_bounded(self, client, project):
        body = tier3_body([sys.executable, "A" * (MAX_ARGV_ITEM_CHARS + 1)])

        assert execute(client, project, body).status_code == 422

    def test_unknown_profile_is_rejected(self, client, project):
        body = tier3_body([sys.executable], profile="no-such-profile")
        assert execute(client, project, body).status_code == 422

    def test_unknown_network_policy_is_rejected(self, client, project):
        body = tier3_body([sys.executable], network_policy="WIDE_OPEN")
        assert execute(client, project, body).status_code == 422

    def test_validation_error_does_not_echo_the_offending_value(self, client, project):
        """Pydantic puts the bad input in ``errors()``; the gateway must not."""
        oversized = "S" * (MAX_ARGV_ITEM_CHARS + 64)
        body = tier3_body([sys.executable, oversized])

        resp = execute(client, project, body)

        assert resp.status_code == 422
        assert oversized not in resp.text
        assert resp.json()["fields"] == ["body.argv.1"]

    def test_oversized_body_is_refused_before_parsing(self, tmp_path, store, router):
        app = create_app(
            store=store,
            router=router,
            sandbox_root=tmp_path / "sandboxes",
            max_body_bytes=512,
        )
        with TestClient(app) as c:
            project_id = executing_project(c)
            resp = c.post(
                f"{API}/projects/{project_id}/session/execute_tier3",
                json=tier3_body([sys.executable, "x" * 4096]),
            )

        assert resp.status_code == 413
        assert resp.json()["error"] == "RequestTooLarge"

    def test_block_strict_network_policy_either_isolates_or_fails_closed(
        self, client, project
    ):
        """A claim of network isolation must be real, never merely assumed."""
        body = tier3_body(
            [sys.executable, "-c", "print('net')"], network_policy="BLOCK_STRICT"
        )
        authorize(client, project, body)

        resp = execute(client, project, body)

        if resp.status_code == 200:
            assert resp.json()["isolation_strength"] != "none", (
                "BLOCK_STRICT was honoured without any isolation backend"
            )
        else:
            assert resp.status_code == 400
            assert resp.json()["error"] == "SandboxPolicyViolationError"


# --------------------------------------------------------------------------
# Model tasks
#
# The router allows ``execute_model_task`` only in REASONING, so these tests
# walk the real lifecycle - run a tool, validate, reason - rather than reaching
# into the session to force a state the API itself cannot produce.
# --------------------------------------------------------------------------
def reasoning_project(client: TestClient, name: str = "reasoning-project") -> int:
    project_id = executing_project(client, name)
    body = echo_body("journey")
    authorize(client, project_id, body)
    assert execute(client, project_id, body).status_code == 200, "tool run required"
    assert client.post(f"{API}/projects/{project_id}/session/validate").status_code == 200
    resp = client.post(f"{API}/projects/{project_id}/session/reason", json={"claims": []})
    assert resp.status_code == 200, resp.text
    return project_id


@pytest.fixture()
def reasoning(client):
    return reasoning_project(client)


def model_nonce(client: TestClient, project_id: int, task_id: str, prompt: str) -> str:
    resp = client.post(
        f"{API}/projects/{project_id}/session/request_model_approval",
        json={"task_id": task_id, "prompt": prompt},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["nonce"]


def run_model(client: TestClient, project_id: int, task_id: str, prompt: str):
    return client.post(
        f"{API}/projects/{project_id}/session/execute_model_task",
        json={"task_id": task_id, "prompt": prompt},
    )


class TestModelApprovalFlow:
    def test_local_task_needs_no_capability(self, client, project):
        resp = client.post(
            f"{API}/projects/{project}/session/request_model_approval",
            json={"task_id": LOCAL_TASK, "prompt": "hi"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "HTTPException"

    def test_unknown_task_is_not_invented(self, client, project):
        resp = client.post(
            f"{API}/projects/{project}/session/request_model_approval",
            json={"task_id": "not-a-real-task", "prompt": "hi"},
        )
        assert resp.status_code == 404
        assert resp.json()["error"] == "UnknownTaskError"

    def test_prompt_is_bounded(self, client, project):
        resp = client.post(
            f"{API}/projects/{project}/session/request_model_approval",
            json={"task_id": BYOK_TASK, "prompt": "p" * (MAX_PROMPT_CHARS + 1)},
        )
        assert resp.status_code == 422
        assert resp.json()["fields"] == ["body.prompt"]

    def test_spent_nonce_cannot_be_replayed(self, client, reasoning):
        prompt = "summarise the schedule"
        nonce = model_nonce(client, reasoning, BYOK_TASK, prompt)
        assert approve(client, reasoning, nonce).status_code == 200

        assert approve(client, reasoning, nonce).status_code == 403

    def test_byok_task_is_refused_without_a_capability(
        self, client, reasoning, provider
    ):
        resp = run_model(client, reasoning, BYOK_TASK, "summarise")

        assert resp.status_code == 403
        # The capability check runs before the session's policy check, so a
        # caller without a nonce is refused without learning anything about the
        # session's own approval state.
        assert resp.json()["error"] == "ApprovalError"
        assert provider.calls == [], "the provider must not be reached"

    def test_approved_byok_task_reaches_the_provider(
        self, client, reasoning, provider
    ):
        prompt = "summarise the trench schedule"
        nonce = model_nonce(client, reasoning, BYOK_TASK, prompt)
        assert approve(client, reasoning, nonce).status_code == 200

        resp = run_model(client, reasoning, BYOK_TASK, prompt)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["provider"] == "fakecloud"
        assert body["text"] == "model-answer"
        assert body["truncated"] is False
        assert body["usage_tokens"] == 11
        assert provider.calls == [{"prompt": prompt, "api_key": "test-key-not-real"}]

    def test_nonce_for_one_prompt_does_not_authorise_another(
        self, client, reasoning, provider
    ):
        """The session keys grants by ``model_<task_id>``; the gateway must not."""
        nonce = model_nonce(client, reasoning, BYOK_TASK, "the approved prompt")
        assert approve(client, reasoning, nonce).status_code == 200

        resp = run_model(client, reasoning, BYOK_TASK, "an entirely different prompt")

        assert resp.status_code == 403
        assert resp.json()["error"] == "ApprovalError"
        assert provider.calls == [], "the provider must not be reached"

    def test_payload_binding_is_single_use(self, client, reasoning, provider):
        prompt = "one shot only"
        nonce = model_nonce(client, reasoning, BYOK_TASK, prompt)
        assert approve(client, reasoning, nonce).status_code == 200

        assert run_model(client, reasoning, BYOK_TASK, prompt).status_code == 200
        # The session grant and the payload binding are both spent by the first
        # call, so a replay is refused by either mechanism, not just one.
        assert run_model(client, reasoning, BYOK_TASK, prompt).status_code == 403
        assert len(provider.calls) == 1

    def test_restart_clears_an_unspent_payload_binding(
        self, client, reasoning, provider
    ):
        prompt = "approved then invalidated"
        nonce = model_nonce(client, reasoning, BYOK_TASK, prompt)
        assert approve(client, reasoning, nonce).status_code == 200
        restarted = client.post(
            f"{API}/projects/{reasoning}/session/restart",
            json={"reason": "payload binding must not survive a rotation"},
        )
        assert restarted.status_code == 200, restarted.text

        resp = run_model(client, reasoning, BYOK_TASK, prompt)

        assert resp.status_code == 403
        assert provider.calls == []

    def test_local_task_runs_without_a_nonce(self, client, reasoning, router):
        local = RecordingProvider("localllm", ModelTier.LOCAL_MODEL, text="local-answer")
        router.register(local)

        resp = run_model(client, reasoning, LOCAL_TASK, "a local question")

        assert resp.status_code == 200, resp.text
        assert resp.json()["provider"] == "localllm"
        assert resp.json()["text"] == "local-answer"
        assert local.calls[0]["api_key"] is None, "local providers get no key"

    def test_model_task_needs_a_reasoning_session(self, client, project):
        prompt = "wrong lifecycle"
        nonce = model_nonce(client, project, BYOK_TASK, prompt)
        assert approve(client, project, nonce).status_code == 200

        resp = run_model(client, project, BYOK_TASK, prompt)

        assert resp.status_code == 409
        assert resp.json()["error"] == "InvalidTransitionError"

    def test_output_over_the_ceiling_is_truncated_not_refused(
        self, tmp_path, store, secrets
    ):
        long_provider = RecordingProvider(
            "fakecloud", ModelTier.BYOK_MODEL, text="Z" * (MAX_OUTPUT_CHARS + 500)
        )
        router = ModelRouter(secrets)
        router.register(long_provider)
        app = create_app(store=store, router=router, sandbox_root=tmp_path / "sandboxes")
        with TestClient(app) as c:
            project_id = reasoning_project(c, "long-output")
            prompt = "produce far too much text"
            nonce = model_nonce(c, project_id, BYOK_TASK, prompt)
            assert approve(c, project_id, nonce).status_code == 200

            resp = run_model(c, project_id, BYOK_TASK, prompt)

        assert resp.status_code == 200, resp.text
        assert resp.json()["truncated"] is True
        assert len(resp.json()["text"]) == MAX_OUTPUT_CHARS


# --------------------------------------------------------------------------
# Error surface
# --------------------------------------------------------------------------
class TestErrorSurface:
    def test_every_failure_uses_the_same_envelope(self, client, project):
        """One shape for every failure, so a client never has to guess."""
        failures = [
            client.get(f"{API}/projects/424242"),                      # 404
            execute(client, 424242, echo_body("missing")),             # 404
            execute(client, project, echo_body("unapproved")),         # 403
            execute(client, project, {"argv": [], "profile": "safe"}), # 422
            client.get(f"{API}/projects/not-an-int"),                  # 422
        ]
        statuses = {resp.status_code for resp in failures}
        assert statuses == {403, 404, 422}, "the cases must actually differ"

        for resp in failures:
            body = resp.json()
            assert set(body) <= {"error", "message", "fields"}, body
            assert body["error"] and isinstance(body["error"], str)
            assert body["message"] and isinstance(body["message"], str)
            assert "detail" not in body, "FastAPI's default envelope leaked"

    def test_unhandled_exception_is_500_and_non_reflective(
        self, tmp_path, store, router, monkeypatch
    ):
        secret = "internal-detail-8c1f-do-not-leak"

        def boom(*args, **kwargs):
            raise RuntimeError(secret)

        app = create_app(store=store, router=router, sandbox_root=tmp_path / "sandboxes")
        # Starlette re-raises after handing the exception to the registered
        # handler; a real server logs it and closes. That is the behaviour under
        # test, so the client must not re-raise on our behalf.
        with TestClient(app, raise_server_exceptions=False) as c:
            monkeypatch.setattr(store, "get_project", boom)
            resp = c.get(f"{API}/projects/1")

        assert resp.status_code == 500
        assert resp.json() == {"error": "RuntimeError", "message": "internal error"}
        assert secret not in resp.text
        assert "Traceback" not in resp.text

    def test_a_500_never_carries_the_exception_text(self):
        """``error_body`` is the only path to a 5xx, so assert on it directly."""
        from qsagent.api.errors import GENERIC_SERVER_MESSAGE, error_body

        body = error_body(500, RuntimeError("SELECT * FROM clients WHERE name='Acme'"))

        assert body["message"] == GENERIC_SERVER_MESSAGE
        assert "Acme" not in json.dumps(body)


# --------------------------------------------------------------------------
# Concurrency
#
# ``TestClient`` funnels every request through one portal, so a check-then-act
# race between two requests cannot be reproduced with it at all. These tests
# start a real uvicorn server on an ephemeral port and hit it from real threads,
# which is the only way to exercise the lock the gateway relies on.
# --------------------------------------------------------------------------
@pytest.fixture()
def live_server(app):
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_level="warning",
            access_log=False,
            # The gateway serves no WebSocket endpoint; skipping the protocol
            # keeps websockets' own deprecation warnings out of the run.
            ws="none",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("uvicorn did not start within 20s")
    port = server.servers[0].sockets[0].getsockname()[1]

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=20)


def call(base: str, method: str, path: str, payload=None):
    """One JSON round-trip. Returns ``(status, body)``; errors are values."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def in_parallel(jobs: list) -> list:
    """Run every zero-argument callable in its own thread, in argument order."""
    results: list = [None] * len(jobs)

    def runner(index: int, job) -> None:
        try:
            results[index] = job()
        except BaseException as exc:  # noqa: BLE001 - the assertions report it
            results[index] = exc

    threads = [
        threading.Thread(target=runner, args=(index, job))
        for index, job in enumerate(jobs)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)
    return results


class TestConcurrency:
    def test_racing_session_creation_produces_one_plan(self, live_server, store):
        status, project = call(live_server, "POST", f"{API}/projects", {"name": "race"})
        assert status == 201, project
        project_id = project["project_id"]

        outcomes = in_parallel(
            [
                lambda: call(
                    live_server,
                    "POST",
                    f"{API}/projects/{project_id}/session/plan",
                    {"steps": 1},
                )
                for _ in range(8)
            ]
        )

        # Re-planning an already-planned session is legal by design, so a 200
        # proves nothing on its own. What must hold is that only one request
        # *created* the session: the check-then-create is inside the lock, so
        # every other thread must have found the session already there.
        assert all(outcome[0] == 200 for outcome in outcomes), outcomes
        bodies = [outcome[1] for outcome in outcomes]

        created = [body for body in bodies if body["from_state"] == "RECEIVED"]
        assert len(created) == 1, "two threads both saw 'no session yet'"

        # One session object served all eight requests: a segment id is minted
        # once, at construction, so a second session would show up here.
        assert len({body["segment_id"] for body in bodies}) == 1, bodies

        plans = [
            row
            for row in store.journal_entries(project_id)
            if row["action"] == "plan"
        ]
        assert len(plans) == len(outcomes)

    def test_concurrent_journeys_each_authorise_only_their_own_argv(self, live_server):
        """Six threads mint/approve/execute simultaneously, one marker each.

        If approvals could be paired with the wrong request, a thread would see
        another thread's marker - or fail outright. Each result must carry the
        marker that thread asked for, and nothing else.
        """
        status, project = call(live_server, "POST", f"{API}/projects", {"name": "race-t3"})
        assert status == 201, project
        project_id = project["project_id"]
        prefix = f"{API}/projects/{project_id}/session"
        assert call(live_server, "POST", f"{prefix}/plan", {"steps": 1})[0] == 200
        assert call(live_server, "POST", f"{prefix}/start_executing")[0] == 200

        markers = [f"marker-{index:02d}" for index in range(6)]

        def journey(marker: str):
            body = echo_body(marker)
            minted_status, minted = call(
                live_server, "POST", f"{prefix}/request_tier3_approval", body
            )
            if minted_status != 200:
                return ("mint", minted_status, minted)
            approved_status, approved = call(
                live_server, "POST", f"{prefix}/approve", {"nonce": minted["nonce"]}
            )
            if approved_status != 200:
                return ("approve", approved_status, approved)
            executed_status, executed = call(
                live_server, "POST", f"{prefix}/execute_tier3", body
            )
            return ("execute", executed_status, executed)

        outcomes = in_parallel(
            [(lambda marker=marker: journey(marker)) for marker in markers]
        )

        for marker, outcome in zip(markers, outcomes):
            stage, status_code, payload = outcome
            assert stage == "execute", (marker, outcome)
            assert status_code == 200, (marker, payload)
            assert payload["stdout"].strip() == marker, (marker, payload)

    def test_one_approval_admits_exactly_one_concurrent_execution(self, live_server):
        """Six threads race to spend a single approval; one may win."""
        status, project = call(live_server, "POST", f"{API}/projects", {"name": "contest"})
        assert status == 201, project
        project_id = project["project_id"]
        prefix = f"{API}/projects/{project_id}/session"
        assert call(live_server, "POST", f"{prefix}/plan", {"steps": 1})[0] == 200
        assert call(live_server, "POST", f"{prefix}/start_executing")[0] == 200

        body = echo_body("contended")
        status, minted = call(
            live_server, "POST", f"{prefix}/request_tier3_approval", body
        )
        assert status == 200, minted
        status, granted = call(
            live_server, "POST", f"{prefix}/approve", {"nonce": minted["nonce"]}
        )
        assert status == 200, granted

        outcomes = in_parallel(
            [lambda: call(live_server, "POST", f"{prefix}/execute_tier3", body) for _ in range(6)]
        )

        accepted = [outcome for outcome in outcomes if outcome[0] == 200]
        assert len(accepted) == 1, outcomes
        assert all(outcome[0] in (200, 403) for outcome in outcomes), outcomes
        assert accepted[0][1]["stdout"].strip() == "contended"
