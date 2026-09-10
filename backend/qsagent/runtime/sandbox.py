"""Tier 3 execution boundary — controlled subprocess sandbox.

Design principles
-----------------
* A plain subprocess is *process isolation*, not a complete security sandbox.
  Every result therefore reports the backend that ran it and that backend's
  honest isolation strength ("weak" / "medium" / "strong").
* The sandbox fails **closed**: when a caller demands a guarantee (e.g. strict
  network isolation) that the selected backend cannot actually enforce, the
  request is rejected rather than silently downgraded.
* Callers pass argv arrays, never shell strings. ``shell=True`` is never used
  and untrusted text is never interpolated into a command line.
* The child never inherits the parent environment wholesale. A minimal safe set
  is built and secret-looking variables are stripped.

This module knows nothing about ``AgentSession``. Approval checks, single-use
consumption and audit recording are injected as narrow callbacks, which keeps
the low-level execution path free of state-machine coupling.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from ..contracts.evidence import ApprovalLevel

log = logging.getLogger(__name__)

__all__ = [
    "NetworkPolicy",
    "ResourceProfile",
    "SandboxRequest",
    "SandboxResult",
    "SandboxError",
    "SandboxPolicyViolationError",
    "AuditFailureError",
    "SAFE_PROFILE",
    "DEVELOPMENT_PROFILE",
    "MAXIMUM_LOCAL_PROFILE",
    "execute",
]


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------
class SandboxError(RuntimeError):
    """Base class for sandbox runtime failures."""


class SandboxPolicyViolationError(SandboxError):
    """A requested security policy cannot be enforced by any available backend.

    Raised *before* any process is spawned, so the caller can distinguish a
    fail-closed policy rejection from an execution failure.

    Deliberately named distinctly from the router's ``PolicyViolationError`` so
    that ``from qsagent.runtime import ...`` never returns two different classes
    under one name.
    """


class AuditFailureError(SandboxError):
    """The action ran but its bounded audit record could not be written.

    Audit-first platform: an unrecorded action is treated as a failure even when
    the underlying subprocess succeeded.
    """


# --------------------------------------------------------------------------
# Network policy
# --------------------------------------------------------------------------
class NetworkPolicy(str, Enum):
    """Network containment requested for a run.

    ALLOW          no restriction (backend may still isolate for other reasons)
    BEST_EFFORT    isolate when the backend can, otherwise run unrestricted
    BLOCK_STRICT   isolation is mandatory; fail closed if unavailable
    """

    ALLOW = "ALLOW"
    BEST_EFFORT = "BEST_EFFORT"
    BLOCK_STRICT = "BLOCK_STRICT"


# --------------------------------------------------------------------------
# Resource profiles
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ResourceProfile:
    """Authoritative resource ceiling for a run.

    A profile is the single source of truth: there are no per-request resource
    overrides in this task, so the effective limit *is* the profile limit.
    Every field is validated at construction time.
    """

    name: str
    max_memory_mb: int
    max_cpu_seconds: float
    max_processes: int
    max_output_bytes: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("profile name cannot be empty")
        if self.max_memory_mb <= 0:
            raise ValueError(f"max_memory_mb must be positive, got {self.max_memory_mb}")
        if self.max_cpu_seconds <= 0:
            raise ValueError(f"max_cpu_seconds must be positive, got {self.max_cpu_seconds}")
        if self.max_processes <= 0:
            raise ValueError(f"max_processes must be positive, got {self.max_processes}")
        if self.max_output_bytes <= 0:
            raise ValueError(
                f"max_output_bytes must be positive, got {self.max_output_bytes}"
            )
        if self.timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds must be positive, got {self.timeout_seconds}")


SAFE_PROFILE = ResourceProfile(
    name="safe",
    max_memory_mb=2048,
    max_cpu_seconds=2.0,
    max_processes=16,
    max_output_bytes=1 * 1024 * 1024,
    timeout_seconds=30.0,
)

DEVELOPMENT_PROFILE = ResourceProfile(
    name="development",
    max_memory_mb=4096,
    max_cpu_seconds=4.0,
    max_processes=32,
    max_output_bytes=4 * 1024 * 1024,
    timeout_seconds=120.0,
)

# maximum-local must be spelled out explicitly by the caller so the host always
# keeps headroom; the values below are a conservative starting point, never an
# automatic "consume everything" policy.
MAXIMUM_LOCAL_PROFILE = ResourceProfile(
    name="maximum-local",
    max_memory_mb=8192,
    max_cpu_seconds=8.0,
    max_processes=64,
    max_output_bytes=8 * 1024 * 1024,
    timeout_seconds=300.0,
)


# --------------------------------------------------------------------------
# Request / result contracts (immutable)
# --------------------------------------------------------------------------
_MAX_STDIN_BYTES = 10 * 1024 * 1024  # 10 MB hard ceiling


@dataclass(frozen=True)
class SandboxRequest:
    """An immutable request to run one argv vector inside the sandbox."""

    argv: tuple[str, ...]
    profile: ResourceProfile
    workspace_parent: Path
    approval_id: str
    stdin: Optional[bytes] = None
    environment: Optional[dict[str, str]] = None
    network_policy: NetworkPolicy = NetworkPolicy.BEST_EFFORT

    def __post_init__(self) -> None:
        if not self.argv:
            raise ValueError("argv cannot be empty")
        if not all(isinstance(a, str) for a in self.argv):
            raise ValueError("argv entries must all be strings")
        if not self.approval_id:
            raise ValueError("approval_id cannot be empty")
        if self.stdin is not None and len(self.stdin) > _MAX_STDIN_BYTES:
            raise ValueError(
                f"stdin exceeds {_MAX_STDIN_BYTES} byte hard limit "
                f"(got {len(self.stdin)})"
            )
        object.__setattr__(self, "argv", tuple(self.argv))
        object.__setattr__(self, "workspace_parent", Path(self.workspace_parent))
        if isinstance(self.network_policy, str):
            object.__setattr__(self, "network_policy", NetworkPolicy(self.network_policy))


@dataclass(frozen=True)
class SandboxResult:
    """Immutable outcome of one sandboxed run."""

    ok: bool
    exit_code: Optional[int]
    stdout: bytes
    stderr: bytes
    timed_out: bool
    output_limited: bool
    resource_limited: bool
    duration_ms: int
    isolation_backend: str
    isolation_strength: str  # "weak" | "medium" | "strong"
    error_code: Optional[str] = None


# --------------------------------------------------------------------------
# Environment sanitisation
# --------------------------------------------------------------------------
_SECRET_MARKERS = (
    "_KEY",
    "_SECRET",
    "_TOKEN",
    "_PASSWORD",
    "_PASSWD",
    "_CREDENTIALS",
    "_CREDENTIAL",
    "KAGGLE",
    "CLOUDFLARE",
    "AWS_",
    "AZURE_",
    "GCP_",
    "OPENAI_",
    "ANTHROPIC_",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "DATABASE_URL",
    "PRIVATE_KEY",
)

# Explicitly dropped even though they do not contain a marker above, because
# they are common credential carriers.
_SECRET_EXACT = frozenset(
    {"TOKEN", "SECRET", "PASSWORD", "APIKEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"}
)


def _is_secret_name(name: str) -> bool:
    upper = name.upper()
    if upper in _SECRET_EXACT:
        return True
    return any(marker in upper for marker in _SECRET_MARKERS)


def _sanitize_environment(custom_env: Optional[dict[str, str]]) -> dict[str, str]:
    """Return a child environment with secret-looking variables removed.

    When ``custom_env`` is provided it is used as an explicit allowlist (minus
    anything that still looks like a secret). When it is ``None`` a minimal safe
    set is constructed so the child never inherits the parent environment
    wholesale.
    """
    if custom_env is not None:
        clean: dict[str, str] = {}
        for key, value in custom_env.items():
            if _is_secret_name(key):
                continue
            clean[key] = value
        return clean

    if sys.platform == "win32":
        names = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP", "PATHEXT", "COMSPEC")
    else:
        names = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")

    minimal: dict[str, str] = {}
    for name in names:
        value = os.environ.get(name)
        if value is not None and not _is_secret_name(name):
            minimal[name] = value
    return minimal


# --------------------------------------------------------------------------
# Workspace lifecycle
# --------------------------------------------------------------------------
def _create_workspace(parent: Path) -> Path:
    parent = Path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    return Path(
        tempfile.mkdtemp(prefix=f"flupper_sandbox_{uuid.uuid4().hex[:8]}_", dir=str(parent))
    )


def _cleanup_workspace(workspace: Path) -> None:
    """Remove the per-run workspace, retrying briefly for lingering handles."""

    def _on_error(_func, path, _exc_info):  # pragma: no cover - platform timing
        try:
            os.chmod(path, 0o700)
        except Exception:
            pass

    last_error: Optional[Exception] = None
    for _ in range(5):
        try:
            shutil.rmtree(str(workspace), onerror=_on_error)
            if not Path(workspace).exists():
                return
        except Exception as exc:  # pragma: no cover - platform timing
            last_error = exc
        time.sleep(0.05)
    if Path(workspace).exists() and last_error is not None:  # pragma: no cover
        log.warning("Workspace %s not fully removed: %s", workspace, last_error)


# --------------------------------------------------------------------------
# Backend selection
# --------------------------------------------------------------------------
def _select_backend():
    """Return the strongest backend available on this host.

    Backends are imported lazily and platform-guarded so that importing this
    module never pulls POSIX-only facilities (``resource``) on Windows, or
    Windows-only ctypes structures elsewhere.
    """
    if sys.platform == "win32":
        from .sandbox_windows import WindowsJobObjectBackend

        return WindowsJobObjectBackend()

    from .sandbox_linux import NsjailBackend, RLimitBackend

    nsjail = NsjailBackend.detect()
    if nsjail is not None:
        return nsjail
    return RLimitBackend()


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def _validate_request(request: SandboxRequest) -> None:
    """Final request-shape validation before any resource is allocated."""
    if not request.argv:
        raise ValueError("argv cannot be empty")
    if request.profile is None:
        raise ValueError("profile is required")
    if request.stdin is not None and len(request.stdin) > _MAX_STDIN_BYTES:
        raise ValueError("stdin exceeds hard limit")


def execute(
    request: SandboxRequest,
    check_approval: Callable[[str, ApprovalLevel], None],
    consume_approval: Callable[[str, ApprovalLevel], None],
    audit: Callable[[str, dict], None],
) -> SandboxResult:
    """Run ``request`` under the strongest available isolation backend.

    Parameters
    ----------
    check_approval(action_id, level)
        Must raise if the approval is missing or stale. Called *before* any
        process is spawned.
    consume_approval(action_id, level)
        Marks the approval single-use. Attempted exactly once after the run,
        regardless of whether the run succeeded.
    audit(action, payload)
        Records bounded metadata. Never receives source, stdin, environment or
        unbounded output.

    Guarantees
    ----------
    * Both ``audit`` and ``consume_approval`` are always attempted.
    * Error precedence when both fail: audit error > consumption error.
    * No workspace or child process is left behind on any path.
    """
    _validate_request(request)
    check_approval(request.approval_id, ApprovalLevel.CONFIRM)

    backend = _select_backend()

    if (
        request.network_policy is NetworkPolicy.BLOCK_STRICT
        and not backend.supports_network_isolation()
    ):
        raise SandboxPolicyViolationError(
            f"BLOCK_STRICT network policy is unavailable on backend "
            f"'{backend.name}' (strength={backend.strength}); refusing to run "
            f"with network access."
        )

    workspace: Optional[Path] = None
    result: Optional[SandboxResult] = None

    try:
        workspace = _create_workspace(request.workspace_parent)
        clean_env = _sanitize_environment(request.environment)
        result = backend.run(
            argv=tuple(request.argv),
            cwd=workspace,
            env=clean_env,
            stdin=request.stdin,
            profile=request.profile,
            network_policy=request.network_policy,
        )
    except Exception as exc:  # noqa: BLE001 - converted to a safe code
        log.warning("Sandbox setup failed (%s)", type(exc).__name__)
        result = SandboxResult(
            ok=False,
            exit_code=None,
            stdout=b"",
            stderr=b"",
            timed_out=False,
            output_limited=False,
            resource_limited=False,
            duration_ms=0,
            isolation_backend=getattr(backend, "name", "unknown"),
            isolation_strength=getattr(backend, "strength", "weak"),
            error_code="SPAWN_FAILED",
        )
    finally:
        if workspace is not None:
            try:
                _cleanup_workspace(workspace)
            except Exception as cleanup_exc:  # pragma: no cover - defensive
                log.error("Workspace cleanup failed: %s", cleanup_exc)

        audit_error: Optional[AuditFailureError] = None
        consume_error: Optional[RuntimeError] = None

        if result is not None:
            try:
                audit(
                    "tier3_exec",
                    {
                        "argv0": request.argv[0],
                        "exit_code": result.exit_code,
                        "duration_ms": result.duration_ms,
                        "ok": result.ok,
                        "error_code": result.error_code,
                        "isolation_backend": result.isolation_backend,
                        "isolation_strength": result.isolation_strength,
                        "profile": request.profile.name,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                log.error("Audit recording failed: %s", type(exc).__name__)
                audit_error = AuditFailureError(
                    f"Sandbox executed but audit recording failed: {type(exc).__name__}"
                )

        try:
            consume_approval(request.approval_id, ApprovalLevel.CONFIRM)
        except Exception as exc:  # noqa: BLE001
            log.error("Approval consumption failed: %s", type(exc).__name__)
            consume_error = RuntimeError(
                f"Sandbox executed but approval consumption failed: {type(exc).__name__}"
            )

        if audit_error is not None:
            raise audit_error
        if consume_error is not None:
            raise consume_error

    assert result is not None  # every path assigns result before the finally runs
    return result


