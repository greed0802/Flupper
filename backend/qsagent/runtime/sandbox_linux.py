"""POSIX isolation backends.

Two backends are offered, chosen honestly:

``RLimitBackend`` (strength **weak**)
    POSIX resource limits (``RLIMIT_AS``/``RLIMIT_CPU``/``RLIMIT_NPROC``) plus a
    dedicated process group so a timeout can kill the whole tree. This is
    *resource* isolation only: there is no mount, PID or network namespace, so
    ``supports_network_isolation()`` returns ``False``.

``NsjailBackend`` (strength **strong**)
    Wraps the child in ``nsjail`` with ``--clone_newnet``, which provides a
    genuine network namespace and therefore real fail-closed network isolation.
    It is only selected after a functional smoke test proves nsjail can actually
    run the interpreter on this host — the mere presence of the binary is never
    treated as evidence.

Neither backend attempts to emulate a container: a filesystem still visible to
the child is reported as such by the ``strength`` value rather than hidden.
"""

from __future__ import annotations

import logging
import os
import select
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import replace
from typing import Optional

try:  # POSIX only; keeps this module importable (for introspection) elsewhere.
    import resource
except ImportError:  # pragma: no cover - non-POSIX
    resource = None  # type: ignore[assignment]

from .sandbox import NetworkPolicy, ResourceProfile, SandboxResult

log = logging.getLogger(__name__)

_READ_CHUNK = 65536
_TERMINATION_GRACE_S = 5.0


class RLimitBackend:
    """Resource-limited POSIX subprocess in its own process group."""

    name = "posix_rlimit"
    strength = "weak"

    def supports_network_isolation(self) -> bool:
        return False

    def run(
        self,
        *,
        argv,
        cwd,
        env,
        stdin: Optional[bytes],
        profile: ResourceProfile,
        network_policy: NetworkPolicy = NetworkPolicy.BEST_EFFORT,
    ) -> SandboxResult:
        if resource is None:  # pragma: no cover - import guard
            raise OSError("POSIX rlimit backend requires the resource module")

        start_time = time.monotonic()
        mem_bytes = profile.max_memory_mb * 1024 * 1024
        cpu_seconds = int(profile.max_cpu_seconds)
        processes = int(profile.max_processes)

        def _preexec() -> None:  # pragma: no cover - runs in the child
            os.setpgrp()
            resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            try:
                resource.setrlimit(resource.RLIMIT_NPROC, (processes, processes))
            except (ValueError, OSError):
                # Not permitted in some containers; not fatal.
                pass

        proc = subprocess.Popen(  # noqa: S603 - argv list, no shell
            list(argv),
            cwd=str(cwd),
            env=env or None,
            stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=_preexec,
        )

        try:
            if stdin:
                # Small, bounded input only; the caller validated the size.
                try:
                    proc.stdin.write(stdin)
                    proc.stdin.flush()
                finally:
                    proc.stdin.close()

            stdout_data, stderr_data, timed_out, output_limited = self._capture(
                proc, profile.max_output_bytes, profile.timeout_seconds
            )

            if timed_out or output_limited:
                self._kill_group(proc)

            exit_code = proc.wait()
            duration_ms = int((time.monotonic() - start_time) * 1000)
            error_code = (
                "TIMEOUT" if timed_out else ("OUTPUT_LIMIT" if output_limited else None)
            )

            return SandboxResult(
                ok=(exit_code == 0 and not timed_out and not output_limited),
                exit_code=exit_code,
                stdout=stdout_data,
                stderr=stderr_data,
                timed_out=timed_out,
                output_limited=output_limited,
                resource_limited=False,
                duration_ms=duration_ms,
                isolation_backend=self.name,
                isolation_strength=self.strength,
                error_code=error_code,
            )
        except Exception:
            self._kill_group(proc)
            raise

    @staticmethod
    def _kill_group(proc: subprocess.Popen) -> None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        try:
            proc.wait(timeout=_TERMINATION_GRACE_S)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            pass

    @staticmethod
    def _capture(proc: subprocess.Popen, max_bytes: int, timeout: float):
        """Incrementally drain both pipes with a byte budget and a deadline."""
        streams = [proc.stdout, proc.stderr]
        buffers: dict[int, bytearray] = {s.fileno(): bytearray() for s in streams}
        for stream in streams:
            os.set_blocking(stream.fileno(), False)

        start = time.monotonic()
        total = 0
        timed_out = False
        output_limited = False

        while True:
            if time.monotonic() - start > timeout:
                timed_out = True
                break
            if total >= max_bytes:
                output_limited = True
                break

            ready, _, _ = select.select(streams, [], [], 0.1)
            for stream in ready:
                try:
                    chunk = stream.read(_READ_CHUNK)
                except (BlockingIOError, OSError):
                    continue
                if chunk:
                    buffers[stream.fileno()].extend(chunk)
                    total += len(chunk)
                    if total >= max_bytes:
                        output_limited = True
                        break
            if output_limited:
                break

            if proc.poll() is not None and not ready:
                # Process exited and nothing more to read.
                break

        if not timed_out and not output_limited:
            for stream in streams:
                while True:
                    try:
                        chunk = stream.read(_READ_CHUNK)
                    except (BlockingIOError, OSError):
                        break
                    if not chunk:
                        break
                    buffers[stream.fileno()].extend(chunk)
                    total += len(chunk)

        stdout_data = bytes(buffers[proc.stdout.fileno()])[:max_bytes]
        stderr_data = bytes(buffers[proc.stderr.fileno()])[:max_bytes]
        return stdout_data, stderr_data, timed_out, output_limited


class NsjailBackend:
    """``nsjail``-wrapped execution with a genuine network namespace.

    Selection is deliberately conservative: the binary must exist, the operator
    must have opted in via ``FLUPPER_SANDBOX_NSJAIL=1``, and a functional smoke
    test must succeed. Anything less falls back to ``RLimitBackend`` and a
    ``BLOCK_STRICT`` request then fails closed.
    """

    name = "nsjail"
    strength = "strong"

    def __init__(self, nsjail_path: str) -> None:
        self._nsjail = nsjail_path

    def supports_network_isolation(self) -> bool:
        # ``--clone_newnet`` creates a real, empty network namespace.
        return True

    @staticmethod
    def detect() -> Optional["NsjailBackend"]:
        if sys.platform == "win32":
            return None
        path = shutil.which("nsjail")
        if not path:
            return None
        if os.environ.get("FLUPPER_SANDBOX_NSJAIL") != "1":
            # nsjail behaviour depends on host kernel/permissions, so it is
            # never enabled implicitly.
            return None
        if not NsjailBackend._smoke_test(path):
            log.info("nsjail present but failed smoke test; using rlimit backend")
            return None
        return NsjailBackend(path)

    @staticmethod
    def _smoke_test(path: str) -> bool:
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [
                    path,
                    "--mode",
                    "o",
                    "--chroot",
                    "/",
                    "--clone_newnet",
                    "--",
                    sys.executable,
                    "-c",
                    "print('nsjail-ok')",
                ],
                capture_output=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return proc.returncode == 0 and b"nsjail-ok" in proc.stdout

    def run(
        self,
        *,
        argv,
        cwd,
        env,
        stdin: Optional[bytes],
        profile: ResourceProfile,
        network_policy: NetworkPolicy = NetworkPolicy.BEST_EFFORT,
    ) -> SandboxResult:
        jail_argv = [
            self._nsjail,
            "--mode",
            "o",
            "--chroot",
            "/",
            "--cwd",
            str(cwd),
            "--time_limit",
            str(int(profile.timeout_seconds)),
            "--rlimit_as",
            str(profile.max_memory_mb),
            "--rlimit_cpu",
            str(int(profile.max_cpu_seconds)),
            "--rlimit_nproc",
            str(profile.max_processes),
        ]
        if network_policy in (NetworkPolicy.BLOCK_STRICT, NetworkPolicy.BEST_EFFORT):
            jail_argv.append("--clone_newnet")
        jail_argv += ["--", *argv]

        # The outer process is still resource-capped and group-killed by the
        # rlimit path; nsjail adds the namespace layer inside.
        result = RLimitBackend().run(
            argv=tuple(jail_argv),
            cwd=cwd,
            env=env,
            stdin=stdin,
            profile=profile,
            network_policy=network_policy,
        )
        return replace(
            result, isolation_backend=self.name, isolation_strength=self.strength
        )


