"""Tier 3 sandbox tests.

These tests exercise the real execution path: actual child processes, actual
pipes, actual Win32/POSIX teardown. No mocks are used for the sandbox itself.

Assertions are written to be non-hollow — every "did not happen" check is paired
with a positive check that the setup genuinely occurred (e.g. the process-tree
test first asserts the grandchild pid was written before asserting it is gone).
"""

import ctypes
import os
import sys
import time
from pathlib import Path

import pytest

from qsagent.contracts.evidence import ApprovalLevel
from qsagent.runtime import (
    AgentSession,
    ApprovalRequiredError,
    InvalidTransitionError,
    NetworkPolicy,
    ResourceProfile,
    SAFE_PROFILE,
    SandboxPolicyViolationError,
    SandboxRequest,
    SessionState,
)
from qsagent.storage import QSStore


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _approved_session(tool_id: str, *, store: QSStore | None = None) -> AgentSession:
    """Session in EXECUTING with CONFIRM granted for ``tool_<tool_id>``."""
    session = AgentSession(1, store=store)
    session.plan({"steps": 1})
    session.start_executing()
    session.approve(f"tool_{tool_id}", ApprovalLevel.CONFIRM)
    return session


def _fast_profile(**overrides) -> ResourceProfile:
    base = dict(
        name="test",
        max_memory_mb=1024,
        max_cpu_seconds=10.0,
        max_processes=8,
        max_output_bytes=65536,
        timeout_seconds=10.0,
    )
    base.update(overrides)
    return ResourceProfile(**base)


def _tier3(
    session: AgentSession,
    tool_id: str,
    argv,
    *,
    workspace_parent,
    profile: ResourceProfile = SAFE_PROFILE,
    network_policy: NetworkPolicy = NetworkPolicy.BEST_EFFORT,
    environment: dict[str, str] | None = None,
    stdin: bytes | None = None,
):
    """Run one Tier 3 tool through the real request-based session contract.

    Mirrors how the API gateway builds a request: the tool id and the approval
    key are derived from the same string, so approving ``tool_<tool_id>`` and
    executing this request are guaranteed to refer to the same action id.
    """
    return session.execute_tier3(
        SandboxRequest(
            argv=tuple(argv),
            profile=profile,
            workspace_parent=Path(workspace_parent),
            approval_id=f"tool_{tool_id}",
            stdin=stdin,
            environment=environment,
            network_policy=network_policy,
        )
    )


def _pid_running(pid: int) -> bool:
    """True only when the process exists *and* is still running."""
    if sys.platform == "win32":  # pragma: no cover - platform specific
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int

        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong(0)
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)

    # POSIX: a zombie still has a /proc entry but an empty cmdline.
    proc_dir = Path(f"/proc/{pid}")
    if not proc_dir.exists():
        return False
    try:
        return bool((proc_dir / "cmdline").read_bytes())
    except OSError:
        return False


# --------------------------------------------------------------------------
# 1. happy path
# --------------------------------------------------------------------------
def test_successful_execution(tmp_path):
    session = _approved_session("echo")

    result = _tier3(
        session,
        "echo",
        (sys.executable, "-c", "print('hello-tier3')"),
        workspace_parent=tmp_path,
    )

    assert result.ok is True
    assert result.exit_code == 0
    assert b"hello-tier3" in result.stdout
    assert result.timed_out is False
    assert result.output_limited is False
    assert result.resource_limited is False
    assert result.error_code is None
    assert result.isolation_strength in {"weak", "medium", "strong"}
    assert result.isolation_backend
    assert session.has_failed_tools is False


# --------------------------------------------------------------------------
# 2. no shell interpretation of hostile arguments
# --------------------------------------------------------------------------
def test_no_shell_injection(tmp_path):
    session = _approved_session("inject")
    hostile = "& del /f /q C:\\*" if os.name == "nt" else "; rm -rf / --no-preserve-root"

    result = _tier3(
        session,
        "inject",
        (sys.executable, "-c", "import sys; print(sys.argv[1])", hostile),
        workspace_parent=tmp_path,
    )

    assert result.ok is True
    # The hostile string arrives as a single, literal argv element.
    assert hostile.encode() in result.stdout


# --------------------------------------------------------------------------
# 3. non-zero exit is reported, not raised, and marked as a failure
# --------------------------------------------------------------------------
def test_nonzero_exit(tmp_path):
    session = _approved_session("boom")

    result = _tier3(
        session,
        "boom",
        (sys.executable, "-c", "raise SystemExit(42)"),
        workspace_parent=tmp_path,
    )

    assert result.ok is False
    assert result.exit_code == 42
    assert result.timed_out is False
    assert session.has_failed_tools is True


# --------------------------------------------------------------------------
# 4. timeout kills the whole process tree
# --------------------------------------------------------------------------
def test_timeout_terminates_process_tree(tmp_path):
    session = _approved_session("timeout")
    marker = tmp_path / "grandchild_pid.txt"

    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen("
        "[sys.executable, '-c', 'import time; time.sleep(999)'])\n"
        f"open({str(marker)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(999)\n"
    )

    result = _tier3(
        session,
        "timeout",
        (sys.executable, "-c", script),
        profile=_fast_profile(timeout_seconds=2.0),
        workspace_parent=tmp_path,
    )

    assert result.timed_out is True
    assert result.error_code == "TIMEOUT"
    assert result.ok is False

    # Positive control: the grandchild really existed before we assert it died.
    assert marker.exists(), "grandchild never reported its pid"
    grandchild_pid = int(marker.read_text().strip())

    time.sleep(0.5)
    assert not _pid_running(grandchild_pid), (
        f"grandchild pid {grandchild_pid} survived the timeout termination"
    )


# --------------------------------------------------------------------------
# 5. output budget is enforced and the child is stopped promptly
# --------------------------------------------------------------------------
def test_output_limit(tmp_path):
    session = _approved_session("flood")

    script = (
        "import sys, time\n"
        "while True:\n"
        "    sys.stdout.write('A' * 1000)\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(0.001)\n"
    )
    profile = _fast_profile(max_output_bytes=8192, timeout_seconds=30.0)

    start = time.monotonic()
    result = _tier3(
        session,
        "flood",
        (sys.executable, "-c", script),
        profile=profile,
        workspace_parent=tmp_path,
    )
    elapsed = time.monotonic() - start

    assert result.output_limited is True
    assert result.error_code == "OUTPUT_LIMIT"
    assert result.ok is False
    assert len(result.stdout) <= profile.max_output_bytes + 8192
    # Regression guard: the child must be terminated on limit, not waited out.
    assert elapsed < 5.0, f"output limit took {elapsed:.1f}s to enforce"


# --------------------------------------------------------------------------
# 6. secret-looking environment variables never reach the child
# --------------------------------------------------------------------------
def test_environment_strips_secrets(tmp_path):
    session = _approved_session("env")

    script = (
        "import os\n"
        "print('API_KEY=' + os.environ.get('API_KEY', 'MISSING'))\n"
        "print('SAFE_VAR=' + os.environ.get('SAFE_VAR', 'MISSING'))\n"
    )

    result = _tier3(
        session,
        "env",
        (sys.executable, "-c", script),
        environment={"API_KEY": "hunter2", "SAFE_VAR": "visible"},
        workspace_parent=tmp_path,
    )

    assert result.ok is True
    text = result.stdout.decode("utf-8", "replace")
    assert "API_KEY=MISSING" in text
    assert "SAFE_VAR=visible" in text
    assert "hunter2" not in text


# --------------------------------------------------------------------------
# 7/8. workspace lifecycle
# --------------------------------------------------------------------------
def test_workspace_cleanup_success(tmp_path):
    session = _approved_session("clean")

    result = _tier3(
        session,
        "clean",
        (sys.executable, "-c", "print('ok')"),
        workspace_parent=tmp_path,
    )

    assert result.ok is True
    assert list(tmp_path.glob("flupper_sandbox_*")) == []


def test_workspace_cleanup_on_timeout(tmp_path):
    session = _approved_session("clean_timeout")

    result = _tier3(
        session,
        "clean_timeout",
        (sys.executable, "-c", "import time; time.sleep(999)"),
        profile=_fast_profile(timeout_seconds=1.0),
        workspace_parent=tmp_path,
    )

    assert result.timed_out is True
    assert list(tmp_path.glob("flupper_sandbox_*")) == []


# --------------------------------------------------------------------------
# 9. invalid request shapes (empty argv + missing executable)
# --------------------------------------------------------------------------
def test_invalid_request(tmp_path):
    # (a) Empty argv is rejected at construction, before any spawn.
    with pytest.raises(ValueError, match="argv cannot be empty"):
        SandboxRequest(
            argv=(),
            profile=SAFE_PROFILE,
            workspace_parent=tmp_path,
            approval_id="tool_invalid",
        )

    # (b) Ambiguous / unlimited-style resource limits are rejected outright.
    with pytest.raises(ValueError, match="max_memory_mb must be positive"):
        _fast_profile(max_memory_mb=0)
    with pytest.raises(ValueError, match="max_output_bytes must be positive"):
        _fast_profile(max_output_bytes=-1)
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        _fast_profile(timeout_seconds=0)

    # (c) A missing executable yields a safe SPAWN_FAILED result, not a crash.
    session = _approved_session("missing")
    missing = str(tmp_path / "definitely-not-a-real-binary")

    result = _tier3(session, "missing", (missing,), workspace_parent=tmp_path)

    assert result.ok is False
    assert result.error_code == "SPAWN_FAILED"
    assert result.exit_code is None
    assert result.stdout == b""
    # A failed spawn still leaves the session in EXECUTING for the agent to react.
    assert session.state is SessionState.EXECUTING


# --------------------------------------------------------------------------
# 10. BLOCK_STRICT network policy is never silently downgraded
# --------------------------------------------------------------------------
def test_strict_network_fails_closed(tmp_path):
    from qsagent.runtime.sandbox import _select_backend

    backend = _select_backend()
    session = _approved_session("netstrict")
    argv = (sys.executable, "-c", "print('ok')")

    if backend.supports_network_isolation():
        # A backend that genuinely isolates the network may run the request.
        result = _tier3(
            session,
            "netstrict",
            argv,
            network_policy=NetworkPolicy.BLOCK_STRICT,
            workspace_parent=tmp_path,
        )
        assert result.ok is True
    else:
        # Fail closed: reject before spawning anything.
        with pytest.raises(SandboxPolicyViolationError, match="BLOCK_STRICT"):
            _tier3(
                session,
                "netstrict",
                argv,
                network_policy=NetworkPolicy.BLOCK_STRICT,
                workspace_parent=tmp_path,
            )
        assert session.has_failed_tools is False
        assert session.state is SessionState.EXECUTING


# --------------------------------------------------------------------------
# 11. Tier 3 requires CONFIRM approval before anything runs
# --------------------------------------------------------------------------
def test_tier3_approval_required(tmp_path):
    session = AgentSession(1)
    session.plan({"steps": 1})
    session.start_executing()

    with pytest.raises(ApprovalRequiredError):
        _tier3(
            session,
            "noapproval",
            (sys.executable, "-c", "print('nope')"),
            workspace_parent=tmp_path,
        )

    # Nothing ran, so nothing is recorded and no workspace was left behind.
    assert session.has_failed_tools is False
    assert session.state is SessionState.EXECUTING
    assert list(tmp_path.glob("flupper_sandbox_*")) == []


# --------------------------------------------------------------------------
# 12. a failed sandbox run blocks validation
# --------------------------------------------------------------------------
def test_failed_sandbox_blocks_validation(tmp_path):
    session = _approved_session("fail")

    result = _tier3(
        session,
        "fail",
        (sys.executable, "-c", "raise SystemExit(3)"),
        workspace_parent=tmp_path,
    )

    assert result.ok is False
    assert session.has_failed_tools is True

    with pytest.raises(InvalidTransitionError, match="failed tool runs"):
        session.start_validating()
    assert session.state is SessionState.EXECUTING


# --------------------------------------------------------------------------
# 13. the audit journal records metadata only — never inputs or output
# --------------------------------------------------------------------------
def test_journal_no_secrets(tmp_path):
    store = QSStore(":memory:")
    project_id = store.get_or_create_project("Sandbox Audit Test")
    session = AgentSession(project_id, store=store)
    session.plan({"steps": 1})
    session.start_executing()
    session.approve("tool_secret", ApprovalLevel.CONFIRM)

    result = _tier3(
        session,
        "secret",
        (sys.executable, "-c", "print('PUBLIC_OUTPUT')"),
        environment={"SECRET_TOKEN": "abc123", "SAFE_VAR": "visible"},
        stdin=b"SECRET_INPUT",
        workspace_parent=tmp_path,
    )
    assert result.ok is True

    entries = store.journal_entries(project_id)
    assert entries, "expected journal entries"
    text = " ".join(str(value) for row in entries for value in tuple(row))

    assert "tier3_exec" in text
    for secret in ("SECRET_INPUT", "PUBLIC_OUTPUT", "SECRET_TOKEN", "abc123"):
        assert secret not in text, f"{secret!r} leaked into the audit journal"



