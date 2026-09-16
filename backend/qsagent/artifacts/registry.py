"""In-memory artifact registry with TTL eviction (Phase 5D).

Design constraints
------------------
* Thread-safe for concurrent route access via a single ``threading.Lock``.
* No disk I/O inside the registry — the bytes live in memory; the caller
  decides whether to also write them to a temp file.
* Eviction is passive (on read / list) plus active (a background daemon thread
  that wakes every ``ARTIFACT_CLEANUP_INTERVAL_SECONDS``).  Either path calls
  the same ``_evict()`` helper, so the logic lives once.
* The registry holds no reference to the FastAPI app or the store. It is
  created by `create_app` and hung on ``app.state.artifacts``; the route
  pulls it from there.

Artifact ids
------------
UUID4 canonical strings — 36 chars, checked at registration and at access.
The registry refuses an id that is not exactly 36 characters so no caller can
probe ``/artifacts/../../etc`` through a crafted parameter.
"""

from __future__ import annotations

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
    """One artifact stored in the registry.

    Attributes
    ----------
    artifact_id:
        UUID4 canonical string (36 chars).
    media_type:
        MIME type for the ``Content-Type`` header on download.
    data:
        Raw bytes of the artifact; bounded by :data:`~.bounds.MAX_EXPORT_BYTES`.
    expires_at:
        ``time.monotonic()`` timestamp after which this entry is stale.
    created_at:
        ``time.monotonic()`` timestamp at registration (for age calculation).
    """

    artifact_id: str
    media_type: str
    data: bytes
    expires_at: float
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
    """Thread-safe, TTL-bounded in-memory artifact store.

    Usage
    -----
    ::

        registry = ArtifactRegistry()
        registry.start_cleanup()          # starts background eviction thread

        artifact_id = registry.register(data, media_type="application/...")
        entry = registry.get(artifact_id) # None if expired or unknown
        registry.delete(artifact_id)

    The cleanup thread is a daemon, so it does not prevent process exit.  Call
    :meth:`stop_cleanup` in tests that need a clean teardown.
    """

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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_cleanup(self) -> None:
        """Start the background eviction daemon. Idempotent."""
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
        """Signal the background thread to stop and join it (for tests)."""
        self._stop_event.set()
        with self._lock:
            t = self._cleanup_thread
        if t is not None:
            t.join(timeout=self._cleanup_interval + 1.0)

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def register(
        self,
        data: bytes,
        *,
        media_type: str,
        ttl_seconds: float | None = None,
    ) -> str:
        """Store *data* and return a fresh UUID4 artifact id.

        Raises
        ------
        ValueError
            If ``len(data) > max_bytes``.
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
        )
        with self._lock:
            self._evict()
            self._store[artifact_id] = entry
        return artifact_id

    def get(self, artifact_id: str) -> ArtifactEntry | None:
        """Return the entry or ``None`` if absent, unknown or expired."""
        if len(artifact_id) != ARTIFACT_ID_CHARS:
            return None
        with self._lock:
            self._evict()
            entry = self._store.get(artifact_id)
        if entry is None or entry.expired:
            return None
        return entry

    def delete(self, artifact_id: str) -> bool:
        """Remove one entry. Returns ``True`` if it existed."""
        with self._lock:
            return self._store.pop(artifact_id, None) is not None

    def __len__(self) -> int:
        with self._lock:
            self._evict()
            return len(self._store)

    def __iter__(self) -> Iterator[ArtifactEntry]:
        """Iterate live (non-expired) entries under the lock."""
        with self._lock:
            self._evict()
            yield from list(self._store.values())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _evict(self) -> None:
        """Remove expired entries. MUST be called under ``self._lock``."""
        now = time.monotonic()
        dead = [k for k, v in self._store.items() if now >= v.expires_at]
        for k in dead:
            del self._store[k]

    def _cleanup_loop(self) -> None:
        """Background daemon: sleep, evict, repeat."""
        while not self._stop_event.wait(timeout=self._cleanup_interval):
            with self._lock:
                self._evict()

