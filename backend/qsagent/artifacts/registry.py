"""In-memory artifact registry with TTL eviction (Phase 5D).

Design constraints
------------------
* Thread-safe for concurrent route access via a single ``threading.Lock``.
* No disk I/O inside the registry — bytes live in memory only.
* Passive eviction on every read/list call plus active eviction via a
  FastAPI lifespan-owned asyncio task from :meth:`async_cleanup_task`.
* The registry holds no reference to the FastAPI app or the store.

Lifecycle (asyncio path — approved architecture)
------------------------------------------------
The cleanup task is owned by the FastAPI lifespan::

    task = asyncio.create_task(registry.async_cleanup_task())
    # … yield …
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

Test-only synchronous path
--------------------------
``start_cleanup()`` / ``stop_cleanup()`` use a daemon thread.
Do **not** call them in production code.

Artifact ids
------------
UUID4 canonical strings — 36 chars, enforced at registration and access.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Iterator

from .bounds import (
    ARTIFACT_CLEANUP_INTERVAL_SECONDS,
    ARTIFACT_ID_CHARS,
    DEFAULT_ARTIFACT_TTL_SECONDS,
    MAX_EXPORT_BYTES,
)


@dataclass
class ArtifactEntry:
    """One artifact stored in the registry."""

    artifact_id: str
    media_type: str
    data: bytes
    expires_at: float
    project_id: int
    created_at: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self.expires_at

    @property
    def age_seconds(self) -> float:
        return time.monotonic() - self.created_at

    @property
    def bytes_len(self) -> int:
        return len(self.data)



class ArtifactRegistry:
    """Thread-safe, TTL-bounded in-memory artifact store."""

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_ARTIFACT_TTL_SECONDS,
        cleanup_interval: float = ARTIFACT_CLEANUP_INTERVAL_SECONDS,
        max_bytes: int = MAX_EXPORT_BYTES,
    ) -> None:
        self._ttl = float(ttl_seconds)
        self._cleanup_interval = float(cleanup_interval)
        self._max_bytes = int(max_bytes)
        self._lock = threading.Lock()
        self._store: dict[str, ArtifactEntry] = {}
        self._stop_event = threading.Event()
        self._cleanup_thread: threading.Thread | None = None

    async def async_cleanup_task(self) -> None:
        """Lifespan-owned coroutine. Cancelled by the lifespan on shutdown."""
        try:
            while True:
                await asyncio.sleep(self._cleanup_interval)
                with self._lock:
                    self._evict()
        except asyncio.CancelledError:
            raise

    def start_cleanup(self) -> None:
        """Start daemon eviction thread. **Test use only.**"""
        with self._lock:
            if self._cleanup_thread is not None and self._cleanup_thread.is_alive():
                return
            self._stop_event.clear()
            t = threading.Thread(
                target=self._cleanup_loop,
                name="artifact-cleanup",
                daemon=True,
            )
            t.start()
            self._cleanup_thread = t

    def stop_cleanup(self) -> None:
        """Stop daemon eviction thread. **Test use only.**"""
        self._stop_event.set()
        with self._lock:
            t = self._cleanup_thread
        if t is not None:
            t.join(timeout=self._cleanup_interval + 1.0)

    def register(
        self,
        data: bytes,
        *,
        media_type: str,
        project_id: int,
        ttl_seconds: float | None = None,
    ) -> str:
        """Store bytes and return a UUID4 artifact id.

        Raises ValueError if len(data) > max_bytes.
        """
        if len(data) > self._max_bytes:
            raise ValueError(
                f"artifact is {len(data)} bytes, ceiling is {self._max_bytes}"
            )
        ttl = float(ttl_seconds) if ttl_seconds is not None else self._ttl
        artifact_id = str(uuid.uuid4())
        entry = ArtifactEntry(
            artifact_id=artifact_id,
            media_type=media_type,
            data=data,
            expires_at=time.monotonic() + ttl,
            project_id=int(project_id),
        )
        with self._lock:
            self._evict()
            self._store[artifact_id] = entry
        return artifact_id

    def get(
        self, artifact_id: str, *, project_id: int | None = None
    ) -> ArtifactEntry | None:
        """Return entry or None if absent, expired, or wrong project."""
        if len(artifact_id) != ARTIFACT_ID_CHARS:
            return None
        with self._lock:
            self._evict()
            entry = self._store.get(artifact_id)
        if entry is None or entry.expired:
            return None
        if project_id is not None and entry.project_id != int(project_id):
            return None
        return entry

    def delete(self, artifact_id: str) -> bool:
        with self._lock:
            return self._store.pop(artifact_id, None) is not None

    def __len__(self) -> int:
        with self._lock:
            self._evict()
            return len(self._store)

    def __iter__(self) -> Iterator[ArtifactEntry]:
        with self._lock:
            self._evict()
            yield from list(self._store.values())

    def _evict(self) -> None:
        dead = [k for k, v in self._store.items() if v.expired]
        for k in dead:
            del self._store[k]

    def _cleanup_loop(self) -> None:
        while not self._stop_event.wait(timeout=self._cleanup_interval):
            with self._lock:
                self._evict()

    @property
    def age_seconds(self) -> float:
        return time.monotonic() - self.created_at

    @property
    def bytes_len(self) -> int:
        return len(self.data)
