"""ASGI request-body ceiling.

A JSON gateway that accepts ``list[str]`` argv vectors has no bound of its own
until one is imposed, and by the time FastAPI has parsed a body it is already in
memory. This middleware imposes the ceiling before that happens.

Two layers, because either alone is bypassable:

* a declared ``Content-Length`` over the ceiling is refused before the router
  ever sees the request, so the common oversized case costs nothing;
* the ``receive`` channel is wrapped so a chunked or undeclared body is counted
  as it streams in. Nothing is buffered, so the ceiling costs no memory, and
  chunks that arrived before the limit was crossed were already forwarded to the
  application exactly as received.

The 413 is emitted from inside the ``receive`` wrapper, before unwinding. That
ordering matters: once an ASGI response has begun it cannot be replaced, so the
rejection must reach the wire before anything else does.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, MutableMapping

DEFAULT_MAX_BODY_BYTES = 64 * 1024

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


class _BodyTooLarge(Exception):
    """Internal unwinding signal - never escapes this module."""


async def _send_413(send: Send, limit: int) -> None:
    payload = json.dumps(
        {
            "error": "RequestTooLarge",
            "message": f"request body exceeds the {limit} byte limit",
        }
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode("ascii")),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


class BodySizeLimitMiddleware:
    """Refuse request bodies larger than ``max_body_bytes``.

    Pure ASGI (no ``BaseHTTPMiddleware``) so the body stream is intercepted
    rather than re-buffered - the wrapper changes nothing for a request that
    stays under the ceiling.
    """

    def __init__(self, app: Any, *, max_body_bytes: int = DEFAULT_MAX_BODY_BYTES) -> None:
        if max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive")
        self.app = app
        self.max_body_bytes = int(max_body_bytes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = _declared_length(scope)
        if declared is not None and declared > self.max_body_bytes:
            await _send_413(send, self.max_body_bytes)
            return

        state = {"received": 0, "rejected": False}

        async def receive_limited() -> Message:
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"") or b""
                state["received"] += len(body)
                if state["received"] > self.max_body_bytes:
                    state["rejected"] = True
                    # Reject first, unwind second: the status line has to be on
                    # the wire before anything else can claim the response.
                    await _send_413(send, self.max_body_bytes)
                    raise _BodyTooLarge()
            return message

        async def send_limited(message: Message) -> None:
            # After a rejection the response is already committed; drop whatever
            # the application tries to emit next instead of corrupting the wire.
            if state["rejected"]:
                return
            await send(message)

        try:
            await self.app(scope, receive_limited, send_limited)
        except _BodyTooLarge:
            return


def _declared_length(scope: Scope) -> int | None:
    """Parsed ``Content-Length``, or ``None`` when absent or unparseable."""
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None
