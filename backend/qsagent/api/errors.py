"""Exception to HTTP mapping for the gateway.

Every failure leaves this layer as the same shape::

    {"error": "<ExceptionClassName>", "message": "<bounded, safe text>"}

Two invariants:

* **5xx bodies are non-reflective.** They carry a fixed sentence chosen by
  exception class, never ``str(exc)``, a traceback, a SQL fragment or a
  provider message. The originating detail goes to the log, not the wire.
* **422 bodies carry field locations, not values.** Pydantic echoes the
  offending input in ``errors()``, and for this API that input can be a prompt
  or an argv vector, so only the ``loc`` path is forwarded.

Status codes deliberately distinguish caller mistakes (400), missing
capabilities (403), wrong lifecycle position (409), malformed upstream answers
(502), and our own failures (500).
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from ..runtime import (
    ApprovalConsumptionError,
    ApprovalRequiredError,
    AuditFailureError,
    InvalidTransitionError,
    MalformedResponseError,
    MissingCredentialError,
    PolicyViolationError,
    ProviderFailureError,
    RouterError,
    SandboxError,
    SandboxPolicyViolationError,
    ToolFailureError,
    UnknownTaskError,
)
from .approvals import ApprovalError

log = logging.getLogger(__name__)

MAX_ECHOED_MESSAGE_CHARS = 200
MAX_REPORTED_FIELDS = 32

# Starlette resolves the most derived class first, so a subclass listed here
# always wins over its parent (that is what keeps SandboxPolicyViolationError a
# 400 while its parent SandboxError stays a 500).
STATUS_BY_EXCEPTION: tuple[tuple[type[BaseException], int], ...] = (
    (UnknownTaskError, 404),
    (PolicyViolationError, 400),
    (SandboxPolicyViolationError, 400),
    (InvalidTransitionError, 409),
    (ApprovalRequiredError, 403),
    (ApprovalError, 403),
    (MissingCredentialError, 403),
    (ToolFailureError, 422),
    (MalformedResponseError, 502),
    (ProviderFailureError, 502),
    (RouterError, 502),
    (AuditFailureError, 500),
    (ApprovalConsumptionError, 500),
    (SandboxError, 500),
)

# Fixed 5xx text per class. Nothing here can be influenced by request content.
FIXED_SERVER_MESSAGES: dict[type[BaseException], str] = {
    AuditFailureError: (
        "the action ran but could not be audited; the result is not trusted"
    ),
    ApprovalConsumptionError: (
        "the action ran but its one-shot approval could not be consumed"
    ),
    SandboxError: "sandbox execution failed",
    RouterError: "model routing failed",
}

GENERIC_SERVER_MESSAGE = "internal error"


def error_body(status: int, exc: BaseException) -> dict[str, object]:
    """Build the sanitised body for one exception."""
    name = type(exc).__name__
    if status >= 500:
        message = FIXED_SERVER_MESSAGES.get(type(exc), GENERIC_SERVER_MESSAGE)
    else:
        message = str(exc)[:MAX_ECHOED_MESSAGE_CHARS].strip() or name
    return {"error": name, "message": message}


def _json_for(status: int, exc: BaseException) -> JSONResponse:
    name = type(exc).__name__
    if status >= 500:
        # class name only: an exception message can carry a path, a key or a
        # fragment of user data.
        log.error("api: %s -> %d", name, status)
    else:
        log.warning("api: %s -> %d", name, status)
    return JSONResponse(status_code=status, content=error_body(status, exc))


def _status_handler(status: int):
    async def handler(_request: Request, exc: Exception) -> JSONResponse:
        return _json_for(status, exc)

    return handler


def register_error_handlers(app) -> None:
    for exc_type, status in STATUS_BY_EXCEPTION:
        app.add_exception_handler(exc_type, _status_handler(status))

    async def validation_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields: list[str] = []
        for err in exc.errors():
            loc = err.get("loc", ()) or ()
            fields.append(".".join(str(part) for part in loc))
        return JSONResponse(
            status_code=422,
            content={
                "error": "RequestValidationError",
                "message": "request body failed validation",
                "fields": sorted(set(fields))[:MAX_REPORTED_FIELDS],
            },
        )

    async def http_exception_handler(
        _request: Request, exc: HTTPException
    ) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "request rejected"
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": "HTTPException",
                "message": detail[:MAX_ECHOED_MESSAGE_CHARS],
            },
            headers=getattr(exc, "headers", None),
        )

    async def unhandled(_request: Request, exc: Exception) -> JSONResponse:
        return _json_for(500, exc)

    app.add_exception_handler(RequestValidationError, validation_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    # ``Exception`` is hoisted by Starlette into the outermost ServerErrorMiddleware
    # handler, which is what makes the catch-all genuinely catch-all.
    app.add_exception_handler(Exception, unhandled)
