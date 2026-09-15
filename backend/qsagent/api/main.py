"""Local HTTP gateway for the Flupper agent runtime (Phase 4, task A).

Transport only
--------------
This package contains no quantity logic, no rate logic and no CheckMate rules.
It validates shapes, enforces the approval handshake, drives ``AgentSession``,
and renders results. Every decision about what a number *means* still happens
in ``qsagent.runtime``, ``qsagent.checkmate`` and ``qsagent.tools``.

The real sandbox runs here
--------------------------
Tier 3 execution goes through ``sandbox.execute`` - real child processes, real
timeouts, real output ceilings - because the whole point of the gateway is to be
the surface a workstation actually talks to. The sandbox's per-run workspace is
created inside ``sandbox_root``, which the gateway owns.

Local-first
-----------
The default posture is: bound to loopback by ``run_local``, single worker
(the approval registry is in-process), no wildcard CORS, no API docs routes,
a hard request-body ceiling, and no credential ever carried in a request or
response body.

Authenticated
-------------
Every route under ``API_PREFIX`` requires a bearer token, ``/health`` included,
and the token is passed into ``create_app`` by the caller rather than read from
the environment here. There is no unauthenticated mode, no fallback that turns
one on, and no place a rejected caller can read the expected value back out of a
response. The comparison is constant-time.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..contracts.evidence import ApprovalLevel
from ..runtime import (
    AgentSession,
    DEVELOPMENT_PROFILE,
    ModelRouter,
    ModelTier,
    NetworkPolicy,
    SAFE_PROFILE,
    SandboxRequest,
)
from ..storage import QSStore
from .approvals import (
    DEFAULT_TTL_SECONDS,
    MODEL_KIND,
    TIER3_KIND,
    ApprovalRegistry,
    canonical_request_hash,
    check_binding,
    prompt_digest,
)
from .contracts import (
    MAX_OUTPUT_CHARS,
    ApprovalGrantedResponse,
    ApprovalNonceResponse,
    ApproveRequest,
    CreateProjectRequest,
    DeliverRequest,
    HealthResponse,
    ModelApprovalRequest,
    ModelResultResponse,
    ModelTaskRequest,
    PlanRequest,
    ProjectResponse,
    ReasoningRequest,
    RestartRequest,
    SessionStateResponse,
    Tier3ApprovalRequest,
    Tier3ExecuteRequest,
    Tier3ResultResponse,
    TransitionResponse,
)
from .errors import register_error_handlers
from .limits import DEFAULT_MAX_BODY_BYTES, BodySizeLimitMiddleware
from .revisions import register_revision_routes

log = logging.getLogger(__name__)

API_PREFIX = "/api/v1"

# Only the default workspace parent used when a caller does not supply one.
DEFAULT_SANDBOX_ROOT = Path("sandbox_runs")

# No wildcard, ever. A wildcard origin would let any page in the operator's
# browser drive the agent's sandbox, which is the exact confused-deputy problem
# the approval handshake exists to prevent.
DEFAULT_ALLOWED_ORIGINS: tuple[str, ...] = (
    "http://localhost:5000",
    "http://127.0.0.1:5000",
)

PROFILES = {"safe": SAFE_PROFILE, "development": DEVELOPMENT_PROFILE}
NETWORK_POLICIES = {policy.value: policy for policy in NetworkPolicy}


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------
# Name of the environment variable ``run_local`` reads the bearer token from.
# The gateway factory itself never touches the environment - the caller passes
# the token in. That is what lets the test suite drive the real app object with
# a known token instead of stubbing the check out.
API_TOKEN_ENV_VAR = "FLUPPER_API_TOKEN"

# One response for every authentication failure. A missing header, a wrong
# scheme, empty credentials and a mismatched token are deliberately
# indistinguishable: a caller who can tell them apart has an oracle for
# probing the token and learns whether a guess was well-formed.
_AUTH_FAILED_MESSAGE = "authentication required"

_bearer_scheme = HTTPBearer(auto_error=False)


def _auth_failure() -> HTTPException:
    """Build the single, non-reflective 401 that every failure path returns.

    The message is a fixed sentence that cannot contain the presented or the
    expected token, and ``errors`` renders it through the standard envelope, so
    a rejected caller learns nothing beyond "not authenticated".
    """
    return HTTPException(
        status_code=401,
        detail=_AUTH_FAILED_MESSAGE,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_bearer_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    """Authenticate one request against the token the app was built with.

    ``auto_error=False`` makes the scheme dependency return ``None`` for a
    missing, empty or non-bearer header instead of raising, so every failure
    mode leaves through the one exit below.

    The comparison is :func:`secrets.compare_digest` over UTF-8 *bytes*. Bytes
    rather than ``str`` because ``compare_digest`` rejects non-ASCII ``str``
    input with ``TypeError`` - a caller sending a unicode token would otherwise
    turn an authentication failure into an unhandled 500.
    """
    expected = getattr(request.app.state, "api_token", None)
    if not expected:
        # Unreachable via ``create_app``, which refuses to build without a
        # token. Kept as a fail-closed backstop: an app that somehow lost its
        # token must refuse every request rather than serve them all.
        log.error("gateway has no configured token; refusing request")
        raise _auth_failure()

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _auth_failure()

    if not secrets.compare_digest(
        credentials.credentials.encode("utf-8"), expected.encode("utf-8")
    ):
        raise _auth_failure()


# --------------------------------------------------------------------------
# Session ownership
# --------------------------------------------------------------------------
class SessionRegistry:
    """Owns the live ``AgentSession`` objects behind one app-wide lock.

    Sessions are never created implicitly from an identifier. ``plan`` is the
    only route that instantiates one; every other session route fails closed
    with a 404 when nothing has been planned.

    One app-wide lock, not one lock per project
    -------------------------------------------
    A per-project lock reads better, but it would be wrong here. The store is a
    single SQLite connection shared by every project, so two projects proceeding
    concurrently could interleave a ``commit`` from one with a half-written
    transaction from the other. ``check_same_thread=False`` makes individual
    statements safe; it says nothing about transaction boundaries.

    The lock is also what stops two Tier 3 runs from racing the same sandbox
    root. Serialising them is the intended behaviour for a single-operator
    workstation, not an accident.

    Every route that reads-then-writes a session runs inside ``locked``, so the
    check-then-act sequence inside one request (does the session exist, is it in
    the right state, does this nonce apply to it) cannot interleave with another
    request at all.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[int, AgentSession] = {}

    @contextmanager
    def locked(self) -> Iterator[None]:
        with self._lock:
            yield

    def get(self, project_id: int) -> AgentSession | None:
        """Call only while holding the lock - see the class docstring."""
        return self._sessions.get(int(project_id))

    def create(self, project_id: int, *, store: QSStore) -> AgentSession:
        """Call only while holding the lock - see the class docstring."""
        session = AgentSession(int(project_id), store=store)
        self._sessions[int(project_id)] = session
        return session

    def __len__(self) -> int:
        return len(self._sessions)


def require_project(store: QSStore, project_id: int) -> dict:
    """404 for an unknown project - never create one from a path parameter.

    Call this **inside** ``sessions.locked()``. The store is a single
    ``sqlite3.Connection`` shared by every request, and its own module docstring
    requires callers to serialise transactions; a read issued outside the lock
    races a write in another thread and was observed returning ``None`` for a
    committed row, plus raising ``InterfaceError`` and ``OperationalError``.
    Route bodies therefore open the lock first and look the project up second.
    """
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="unknown project")
    return project


def require_session(sessions: SessionRegistry, project_id: int) -> AgentSession:
    session = sessions.get(project_id)
    if session is None:
        raise HTTPException(status_code=404, detail="no session planned for this project")
    return session


# --------------------------------------------------------------------------
# Pure helpers - no app state, directly unit-testable
# --------------------------------------------------------------------------
def ensure_sandbox_root(root: Path) -> Path:
    """Create the workspace parent the gateway hands to the sandbox.

    The account running the gateway is the only account that can execute Tier 3
    commands (an LD_PRELOAD can do anything that account can do), so the root is
    private to that account rather than a world-writable temporary directory.
    POSIX honours mode 0700; Windows has no equivalent and is documented rather
    than pretended otherwise.
    """
    root.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(root, 0o700)
    return root


def tier3_action_id(request_hash: str) -> str:
    """Action id an approval must name to authorise one exact Tier 3 request."""
    return f"tool_{request_hash}"


def tier3_request_hash(project_id: int, segment_id: str, body: Tier3ExecuteRequest) -> str:
    """Canonical hash of one exact Tier 3 action.

    Built identically by the mint route and the execute route. If the two ever
    disagreed, the execute route would simply find no approval - fail closed -
    rather than run something that was never approved.
    """
    return canonical_request_hash(
        {
            "kind": TIER3_KIND,
            "project_id": project_id,
            "segment_id": segment_id,
            "argv": list(body.argv),
            "profile": body.profile,
            "network_policy": body.network_policy,
        }
    )


def build_tier3_request(
    body: Tier3ExecuteRequest,
    *,
    approval_id: str,
    sandbox_root: Path,
) -> SandboxRequest:
    """Materialise the immutable sandbox request the session will execute.

    ``approval_id`` is the session's approval key *and* the sandbox's callback
    key, so granting the approval and running the request cannot drift apart.
    """
    return SandboxRequest(
        argv=tuple(body.argv),
        profile=PROFILES[body.profile],
        workspace_parent=sandbox_root,
        approval_id=approval_id,
        network_policy=NETWORK_POLICIES[body.network_policy],
    )


def model_action_id(task_id: str) -> str:
    """Action id ``AgentSession`` requires CONFIRM for, for one model task.

    The session's own contract: ``execute_model_task`` checks ``model_<task_id>``.
    It carries no payload, which is why the registry also records a payload
    binding for model approvals - see ``ApprovalRegistry.bind``.
    """
    return f"model_{task_id}"


def model_request_hash(
    project_id: int, segment_id: str, task_id: str, prompt: str
) -> str:
    """Canonical hash of one exact model task call.

    The prompt is bound by digest, so the approval is tied to the exact text
    that will be sent without the registry ever holding a copy of it.
    """
    return canonical_request_hash(
        {
            "kind": MODEL_KIND,
            "project_id": project_id,
            "segment_id": segment_id,
            "task_id": task_id,
            "prompt_digest": prompt_digest(prompt),
        }
    )


def session_state_response(project_id: int, session: AgentSession) -> SessionStateResponse:
    return SessionStateResponse(
        project_id=project_id,
        state=session.state.value,
        segment_id=session.segment_id,
        history_length=len(session.history),
        tool_runs=len(session.tool_runs),
        failed_tools=session.has_failed_tools,
        model_runs=len(session.model_runs),
        failed_models=session.has_failed_models,
    )


def transition_response(
    project_id: int, session: AgentSession, record
) -> TransitionResponse:
    return TransitionResponse(
        project_id=project_id,
        from_state=record.from_state.value,
        to_state=record.to_state.value,
        action=record.action,
        journal_seq=record.journal_seq,
        segment_id=session.segment_id,
    )


def tier3_result_response(result) -> Tier3ResultResponse:
    """Render sandbox output under a hard character ceiling.

    Output is capped again here, not only inside the sandbox: the sandbox caps
    *bytes* to protect the pipe, this caps *characters* to protect the JSON
    response and anything that later renders it.
    """
    stdout = result.stdout.decode("utf-8", "replace")
    stderr = result.stderr.decode("utf-8", "replace")
    return Tier3ResultResponse(
        ok=result.ok,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        output_limited=result.output_limited,
        resource_limited=result.resource_limited,
        duration_ms=result.duration_ms,
        isolation_backend=result.isolation_backend,
        isolation_strength=result.isolation_strength,
        error_code=result.error_code,
        stdout=stdout[:MAX_OUTPUT_CHARS],
        stdout_truncated=len(stdout) > MAX_OUTPUT_CHARS,
        stderr=stderr[:MAX_OUTPUT_CHARS],
        stderr_truncated=len(stderr) > MAX_OUTPUT_CHARS,
    )


def nonce_response(record) -> ApprovalNonceResponse:
    return ApprovalNonceResponse(
        nonce=record.nonce,
        kind=record.kind,
        action_id=record.action_id,
        request_hash=record.request_hash,
        project_id=record.project_id,
        segment_id=record.segment_id,
        expires_in_seconds=record.remaining_seconds(time.time()),
    )


# --------------------------------------------------------------------------
# Application factory
# --------------------------------------------------------------------------
def create_app(
    *,
    store: QSStore,
    router: ModelRouter,
    api_token: str,
    sandbox_root: Path | str = DEFAULT_SANDBOX_ROOT,
    allowed_origins: Sequence[str] = DEFAULT_ALLOWED_ORIGINS,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    approval_ttl_seconds: float = DEFAULT_TTL_SECONDS,
) -> FastAPI:
    """Build the gateway around already-constructed collaborators.

    Nothing is read from the environment here. The caller passes the store, the
    router and the bearer token in, which is what lets a test drive the exact
    same app object against an in-memory store, an empty router and a known
    token while production swaps in a real database, a real BYOK router and a
    token from the process environment - the code path is the same one.

    ``api_token`` is required and is validated, not defaulted. A gateway that
    cannot authenticate is a gateway that must not start: there is deliberately
    no unauthenticated mode and no fallback that turns one on.
    """
    if not api_token or not api_token.strip():
        raise ValueError("api_token is required and must not be blank")

    root = ensure_sandbox_root(Path(sandbox_root))
    if not allowed_origins or any(origin == "*" for origin in allowed_origins):
        raise ValueError("allowed_origins must be explicit and must not contain '*'")

    sessions = SessionRegistry()
    approvals = ApprovalRegistry(ttl_seconds=approval_ttl_seconds)

    app = FastAPI(
        title="Flupper Local Gateway",
        version="0.1.0",
        # Docs and the OpenAPI schema are disabled on purpose. This surface
        # executes processes, so every route should be reached through the
        # workstation that implements the approval handshake rather than through
        # a browser page that enumerates the routes.
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.state.store = store
    app.state.router = router
    app.state.sandbox_root = root
    app.state.sessions = sessions
    app.state.approvals = approvals
    # Held on the app rather than in a module global so two apps built in one
    # process - which is exactly what the test suite does - cannot authenticate
    # each other's callers.
    app.state.api_token = api_token

    # Middleware order is resolution order. ``add_middleware`` prepends, so the
    # last call ends up outermost: CORS wraps the body limit, which means an
    # oversized request still gets CORS headers and the browser can read the
    # 413 instead of reporting an opaque network failure.
    app.add_middleware(BodySizeLimitMiddleware, max_body_bytes=max_body_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        # ``Authorization`` must be listed or a browser client is blocked at the
        # preflight and can never authenticate at all. Still no wildcard, and
        # still no cookies: the token travels in a header, never in an ambient
        # credential the browser attaches on its own.
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )

    register_error_handlers(app)

    # The token requirement is attached to the router, not to each handler, so a
    # route added later cannot be left unauthenticated by omission. It covers
    # ``/health`` too - see the note on that route.
    api = APIRouter(prefix=API_PREFIX, dependencies=[Depends(require_bearer_token)])

    # ------------------------------------------------------------------ health
    # Authenticated, deliberately. ``sandbox_root_ready`` reports whether a real
    # process-execution workspace exists right now, which is operator state: an
    # anonymous reader learns whether this workstation is live and able to run
    # code. Loopback binding hides that today, but this route is also the one a
    # tunnel or a load balancer is most likely to be pointed at, so the safe
    # default is chosen now rather than remembered later. Liveness probing that
    # must bypass authentication belongs on a process supervisor's own check,
    # not on a route that describes the sandbox.
    @api.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Liveness only. No session, no project, no credentials, no internals."""
        return HealthResponse(status="ok", sandbox_root_ready=root.is_dir())

    # ---------------------------------------------------------------- projects
    @api.post("/projects", response_model=ProjectResponse, status_code=201)
    def create_project(body: CreateProjectRequest) -> ProjectResponse:
        # The store lock also covers project creation: an INSERT plus its
        # commit must not interleave with a Tier 3 run journaling into the same
        # SQLite connection.
        with sessions.locked():
            project_id = store.create_project(body.name, body.client, body.tender_no)
        return ProjectResponse(project_id=project_id, name=body.name)

    @api.get("/projects/{project_id}", response_model=ProjectResponse)
    def read_project(project_id: int) -> ProjectResponse:
        with sessions.locked():
            project = require_project(store, project_id)
        return ProjectResponse(project_id=project["id"], name=project["name"])

    # ----------------------------------------------------------------- session
    @api.get(
        "/projects/{project_id}/session",
        response_model=SessionStateResponse,
    )
    def read_session(project_id: int) -> SessionStateResponse:
        with sessions.locked():
            require_project(store, project_id)
            session = require_session(sessions, project_id)
            return session_state_response(project_id, session)

    @api.post(
        "/projects/{project_id}/session/plan",
        response_model=TransitionResponse,
    )
    def plan(project_id: int, body: PlanRequest) -> TransitionResponse:
        """The only route that instantiates a session.

        A client that has merely guessed a project id gets a 404 here rather
        than a session in RECEIVED, so "does this project have an agent" is
        never answered by an accident of routing.
        """
        with sessions.locked():
            require_project(store, project_id)
            session = sessions.get(project_id)
            if session is None:
                session = sessions.create(project_id, store=store)
            record = session.plan({"steps": body.steps, "notes": body.notes})
            return transition_response(project_id, session, record)

    @api.post(
        "/projects/{project_id}/session/start_executing",
        response_model=TransitionResponse,
    )
    def start_executing(project_id: int) -> TransitionResponse:
        with sessions.locked():
            require_project(store, project_id)
            session = require_session(sessions, project_id)
            record = session.start_executing()
            # The segment rotated, so every nonce minted against the previous
            # one is dead. Purge them rather than leaving them to be rejected
            # one at a time, and rather than letting them accumulate.
            approvals.purge_project(project_id)
            return transition_response(project_id, session, record)

    @api.post(
        "/projects/{project_id}/session/restart",
        response_model=TransitionResponse,
    )
    def restart(project_id: int, body: RestartRequest) -> TransitionResponse:
        with sessions.locked():
            require_project(store, project_id)
            session = require_session(sessions, project_id)
            record = session.restart(body.reason)
            approvals.purge_project(project_id)
            return transition_response(project_id, session, record)

    @api.post(
        "/projects/{project_id}/session/validate",
        response_model=TransitionResponse,
    )
    def start_validating(project_id: int) -> TransitionResponse:
        with sessions.locked():
            require_project(store, project_id)
            session = require_session(sessions, project_id)
            record = session.start_validating()
            return transition_response(project_id, session, record)

    @api.post(
        "/projects/{project_id}/session/reason",
        response_model=TransitionResponse,
    )
    def start_reasoning(project_id: int, body: ReasoningRequest) -> TransitionResponse:
        with sessions.locked():
            require_project(store, project_id)
            session = require_session(sessions, project_id)
            record = session.start_reasoning(list(body.claims))
            return transition_response(project_id, session, record)

    @api.post(
        "/projects/{project_id}/session/deliver",
        response_model=TransitionResponse,
    )
    def deliver(project_id: int, body: DeliverRequest) -> TransitionResponse:
        with sessions.locked():
            require_project(store, project_id)
            session = require_session(sessions, project_id)
            record = session.deliver(body.response_text, list(body.claims))
            return transition_response(project_id, session, record)

    # ------------------------------------------------------------------ tier 3
    @api.post(
        "/projects/{project_id}/session/request_tier3_approval",
        response_model=ApprovalNonceResponse,
    )
    def request_tier3_approval(
        project_id: int, body: Tier3ApprovalRequest
    ) -> ApprovalNonceResponse:
        """Mint a one-shot capability for one exact argv vector.

        The request hash covers argv, profile, network policy, project and the
        live segment, so the nonce authorises exactly what the caller will run
        and nothing else. Minting only registers intent - approval is a separate
        call, so "the agent proposed this" and "the operator allowed this" stay
        distinguishable in the audit trail.
        """
        with sessions.locked():
            require_project(store, project_id)
            session = require_session(sessions, project_id)
            request_hash = tier3_request_hash(project_id, session.segment_id, body)
            record = approvals.issue(
                kind=TIER3_KIND,
                action_id=tier3_action_id(request_hash),
                request_hash=request_hash,
                project_id=project_id,
                segment_id=session.segment_id,
            )
            return nonce_response(record)

    @api.post(
        "/projects/{project_id}/session/approve",
        response_model=ApprovalGrantedResponse,
    )
    def approve(project_id: int, body: ApproveRequest) -> ApprovalGrantedResponse:
        """Spend one nonce and grant exactly the action it names.

        The order is deliberate: look up without consuming, validate every
        binding against *this* route and session, grant in the session, and only
        then remove the nonce. If the grant raises, the nonce survives and the
        caller can retry; removal first would burn the capability silently.
        """
        with sessions.locked():
            require_project(store, project_id)
            session = require_session(sessions, project_id)
            pending = approvals.peek(body.nonce)
            check_binding(
                pending,
                project_id=project_id,
                segment_id=session.segment_id,
            )
            session.approve(pending.action_id, ApprovalLevel.CONFIRM)
            if pending.kind == MODEL_KIND:
                # The session grant is keyed by action id alone and a model
                # action id is ``model_<task_id>``, which says nothing about the
                # prompt. Record the payload the operator actually approved so
                # the execution route can refuse every other prompt.
                approvals.bind(
                    project_id=project_id,
                    action_id=pending.action_id,
                    request_hash=pending.request_hash,
                )
            approvals.consume(body.nonce)
            return ApprovalGrantedResponse(
                action_id=pending.action_id,
                kind=pending.kind,
                state=session.state.value,
                segment_id=session.segment_id,
            )

    @api.post(
        "/projects/{project_id}/session/execute_tier3",
        response_model=Tier3ResultResponse,
    )
    def execute_tier3(project_id: int, body: Tier3ExecuteRequest) -> Tier3ResultResponse:
        """Run one Tier 3 request through the real sandbox.

        The approval id is rebuilt from the same canonical hash the mint route
        used and handed to ``SandboxRequest``. No path in this module can call
        ``execute_tier3`` with an approval id it did not derive from the body it
        is about to execute.
        """
        with sessions.locked():
            require_project(store, project_id)
            session = require_session(sessions, project_id)
            request_hash = tier3_request_hash(project_id, session.segment_id, body)
            request = build_tier3_request(
                body,
                approval_id=tier3_action_id(request_hash),
                sandbox_root=root,
            )
            result = session.execute_tier3(request)
            return tier3_result_response(result)

    # ------------------------------------------------------------------ models
    @api.post(
        "/projects/{project_id}/session/request_model_approval",
        response_model=ApprovalNonceResponse,
    )
    def request_model_approval(
        project_id: int, body: ModelApprovalRequest
    ) -> ApprovalNonceResponse:
        """Mint a nonce for one BYOK model call, bound to the exact prompt.

        A local task needs no capability - it never leaves the workstation - so
        asking for one is a 400 rather than a nonce that cannot be spent.
        """
        with sessions.locked():
            require_project(store, project_id)
            session = require_session(sessions, project_id)
            if router.get_tier(body.task_id) is not ModelTier.BYOK_MODEL:
                raise HTTPException(
                    status_code=400,
                    detail="task does not require a BYOK approval",
                )
            request_hash = model_request_hash(
                project_id, session.segment_id, body.task_id, body.prompt
            )
            record = approvals.issue(
                kind=MODEL_KIND,
                action_id=model_action_id(body.task_id),
                request_hash=request_hash,
                project_id=project_id,
                segment_id=session.segment_id,
            )
            return nonce_response(record)

    @api.post(
        "/projects/{project_id}/session/execute_model_task",
        response_model=ModelResultResponse,
    )
    def execute_model_task(project_id: int, body: ModelTaskRequest) -> ModelResultResponse:
        with sessions.locked():
            require_project(store, project_id)
            session = require_session(sessions, project_id)
            # The router's policy table decides, not the caller: an unknown task
            # is a 404 before any other consideration, and the tier the router
            # reports is the tier that governs whether a capability is needed.
            if router.get_tier(body.task_id) is ModelTier.BYOK_MODEL:
                # Consumed before the call, so a failure downstream cannot leave
                # a payload binding lying around for a later request to spend.
                # Failing closed means a caller that was in the wrong lifecycle
                # state must mint again - which is the correct cost.
                approvals.require_binding(
                    project_id=project_id,
                    action_id=model_action_id(body.task_id),
                    request_hash=model_request_hash(
                        project_id, session.segment_id, body.task_id, body.prompt
                    ),
                )
            response = session.execute_model_task(body.task_id, body.prompt, router)
            text = response.text
            return ModelResultResponse(
                text=text[:MAX_OUTPUT_CHARS],
                truncated=len(text) > MAX_OUTPUT_CHARS,
                provider=response.provider_name,
                model=response.model_name,
                usage_tokens=response.usage_tokens,
            )

    # ------------------------------------------------- revision intelligence
    # Phase 5B. Read-only, so it is registered on the same authenticated router
    # as everything else and inherits the bearer-token dependency: there is no
    # unauthenticated revision surface to forget to protect.
    register_revision_routes(api, store=store, sessions=sessions)

    app.include_router(api)
    return app
