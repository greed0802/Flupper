"""Windows isolation backend using Win32 Job Objects.

Isolation strength: **medium**.

A Job Object provides genuine, kernel-enforced containment for the whole process
tree: process-count, per-process memory and job-wide user-time limits, plus
reliable tree-wide termination. It does **not** provide a network namespace, a
filesystem sandbox or a security-token boundary, so this backend honestly
reports ``supports_network_isolation() == False`` and a ``BLOCK_STRICT`` network
policy is refused by the orchestration layer rather than silently ignored.

Process creation is race-safe: the child is created suspended, assigned to the
job, and only then resumed, so it cannot spawn descendants outside the job.
"""

from __future__ import annotations

import ctypes
import logging
import subprocess
import sys
import time
from ctypes import wintypes
from typing import Optional

from .sandbox import NetworkPolicy, ResourceProfile, SandboxResult

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Win32 constants
# --------------------------------------------------------------------------
CREATE_SUSPENDED = 0x00000004
CREATE_UNICODE_ENVIRONMENT = 0x00000400

HANDLE_FLAG_INHERIT = 0x00000001
STARTF_USESTDHANDLES = 0x00000100

INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
STILL_ACTIVE = 259

JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_JOB_TIME = 0x00000004
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

JobObjectExtendedLimitInformation = 9


# --------------------------------------------------------------------------
# ctypes structures
# --------------------------------------------------------------------------
class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", wintypes.LPVOID),
        ("bInheritHandle", wintypes.BOOL),
    ]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


# --------------------------------------------------------------------------
# kernel32 prototypes
#
# Declaring argtypes/restype is mandatory on 64-bit Windows: without it ctypes
# treats pointer-sized HANDLE return values as 32-bit ints and silently
# truncates them.
# --------------------------------------------------------------------------
def _configure_prototypes(k) -> None:
    k.CreatePipe.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(SECURITY_ATTRIBUTES),
        wintypes.DWORD,
    ]
    k.CreatePipe.restype = wintypes.BOOL

    k.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    k.SetHandleInformation.restype = wintypes.BOOL

    k.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOW),
        ctypes.POINTER(PROCESS_INFORMATION),
    ]
    k.CreateProcessW.restype = wintypes.BOOL

    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k.CreateJobObjectW.restype = wintypes.HANDLE

    k.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    k.SetInformationJobObject.restype = wintypes.BOOL

    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.AssignProcessToJobObject.restype = wintypes.BOOL

    k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.TerminateJobObject.restype = wintypes.BOOL

    k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.TerminateProcess.restype = wintypes.BOOL

    k.ResumeThread.argtypes = [wintypes.HANDLE]
    k.ResumeThread.restype = wintypes.DWORD

    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD

    k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k.GetExitCodeProcess.restype = wintypes.BOOL

    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL

    k.PeekNamedPipe.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    k.PeekNamedPipe.restype = wintypes.BOOL

    k.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    k.ReadFile.restype = wintypes.BOOL

    k.WriteFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    k.WriteFile.restype = wintypes.BOOL

    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE


_kernel32 = ctypes.windll.kernel32 if sys.platform == "win32" else None
if _kernel32 is not None:  # pragma: no cover - Windows only
    _configure_prototypes(_kernel32)

_READ_CHUNK = 4096
_STDIN_CHUNK = 65536
_TERMINATION_GRACE_MS = 5000


class WindowsJobObjectBackend:
    """Job-Object-based containment for a subprocess tree."""

    name = "win32_job"
    strength = "medium"

    def supports_network_isolation(self) -> bool:
        """Job Objects provide no network namespace — never claim otherwise."""
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
        if _kernel32 is None:  # pragma: no cover - import guard
            raise OSError("Windows Job Object backend requires win32")

        k = _kernel32
        start_time = time.monotonic()

        stdin_read, stdin_write = self._create_pipe(
            read_inheritable=True, write_inheritable=False
        )
        stdout_read, stdout_write = self._create_pipe(
            read_inheritable=False, write_inheritable=True
        )
        stderr_read, stderr_write = self._create_pipe(
            read_inheritable=False, write_inheritable=True
        )

        # Live-handle registry: a handle is removed the moment it is closed so a
        # recycled numeric handle value can never be closed twice.
        live: dict[str, object] = {
            "stdin_read": stdin_read,
            "stdin_write": stdin_write,
            "stdout_read": stdout_read,
            "stdout_write": stdout_write,
            "stderr_read": stderr_read,
            "stderr_write": stderr_write,
        }

        def close(name: str) -> None:
            handle = live.pop(name, None)
            if handle:
                k.CloseHandle(handle)

        process_handle = None
        job_handle = None
        process_created = False

        try:
            si = STARTUPINFOW()
            si.cb = ctypes.sizeof(STARTUPINFOW)
            si.dwFlags = STARTF_USESTDHANDLES
            si.hStdInput = stdin_read
            si.hStdOutput = stdout_write
            si.hStdError = stderr_write

            cmd_buf = ctypes.create_unicode_buffer(subprocess.list2cmdline(list(argv)))
            app_buf = ctypes.create_unicode_buffer(argv[0])
            env_block = self._build_environment_block(env)
            env_buf = (
                ctypes.create_unicode_buffer(env_block) if env_block is not None else None
            )

            pi = PROCESS_INFORMATION()
            created = k.CreateProcessW(
                app_buf,
                cmd_buf,
                None,
                None,
                True,
                CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT,
                ctypes.cast(env_buf, ctypes.c_void_p) if env_buf is not None else None,
                str(cwd),
                ctypes.byref(si),
                ctypes.byref(pi),
            )
            if not created:
                raise OSError(f"CreateProcessW failed with error {ctypes.get_last_error()}")

            process_created = True
            process_handle = pi.hProcess
            live["process"] = pi.hProcess
            live["thread"] = pi.hThread

            # The parent no longer needs the child-side pipe ends.
            close("stdin_read")
            close("stdout_write")
            close("stderr_write")

            try:
                job_handle = k.CreateJobObjectW(None, None)
                if not job_handle:
                    raise OSError(
                        f"CreateJobObjectW failed with error {ctypes.get_last_error()}"
                    )

                info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
                info.BasicLimitInformation.LimitFlags = (
                    JOB_OBJECT_LIMIT_PROCESS_MEMORY
                    | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                    | JOB_OBJECT_LIMIT_JOB_TIME
                    | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                )
                info.ProcessMemoryLimit = profile.max_memory_mb * 1024 * 1024
                info.BasicLimitInformation.ActiveProcessLimit = profile.max_processes
                info.BasicLimitInformation.PerJobUserTimeLimit = int(
                    profile.max_cpu_seconds * 10_000_000
                )

                if not k.SetInformationJobObject(
                    job_handle,
                    JobObjectExtendedLimitInformation,
                    ctypes.byref(info),
                    ctypes.sizeof(info),
                ):
                    raise OSError(
                        "SetInformationJobObject failed with error "
                        f"{ctypes.get_last_error()}"
                    )

                if not k.AssignProcessToJobObject(job_handle, process_handle):
                    raise OSError(
                        "AssignProcessToJobObject failed with error "
                        f"{ctypes.get_last_error()}"
                    )

                if k.ResumeThread(pi.hThread) == 0xFFFFFFFF:
                    raise OSError(
                        f"ResumeThread failed with error {ctypes.get_last_error()}"
                    )

                if stdin:
                    self._write_stdin(live["stdin_write"], stdin)
                close("stdin_write")

                stdout_data, stderr_data, timed_out, output_limited = self._capture_with_limit(
                    process_handle=process_handle,
                    job_handle=job_handle,
                    stdout_handle=live["stdout_read"],
                    stderr_handle=live["stderr_read"],
                    max_bytes=profile.max_output_bytes,
                    timeout=profile.timeout_seconds,
                )

                k.WaitForSingleObject(process_handle, INFINITE)

                exit_code = wintypes.DWORD(0)
                k.GetExitCodeProcess(process_handle, ctypes.byref(exit_code))

                duration_ms = int((time.monotonic() - start_time) * 1000)
                error_code = (
                    "TIMEOUT"
                    if timed_out
                    else ("OUTPUT_LIMIT" if output_limited else None)
                )

                return SandboxResult(
                    ok=(exit_code.value == 0 and not timed_out and not output_limited),
                    exit_code=int(exit_code.value),
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
                # Setup failed after the child was created: it is suspended and
                # must never be left orphaned outside the intended job.
                if process_created:
                    self._terminate_tree(job_handle, process_handle)
                raise
            finally:
                if job_handle:
                    k.CloseHandle(job_handle)
                    job_handle = None
        finally:
            for name in list(live.keys()):
                close(name)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _terminate_tree(job_handle, process_handle) -> None:
        """Terminate the whole tree, then wait briefly for the child to die."""
        k = _kernel32
        try:
            if job_handle:
                k.TerminateJobObject(job_handle, 2)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("TerminateJobObject failed: %s", exc)
        try:
            if process_handle:
                k.TerminateProcess(process_handle, 2)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("TerminateProcess failed: %s", exc)
        try:
            if process_handle:
                k.WaitForSingleObject(process_handle, _TERMINATION_GRACE_MS)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("WaitForSingleObject after termination failed: %s", exc)

    @staticmethod
    def _create_pipe(*, read_inheritable: bool, write_inheritable: bool):
        """Create a pipe, then set per-end inheritance explicitly."""
        k = _kernel32
        sa = SECURITY_ATTRIBUTES()
        sa.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
        sa.lpSecurityDescriptor = None
        sa.bInheritHandle = True

        read_h = wintypes.HANDLE()
        write_h = wintypes.HANDLE()
        if not k.CreatePipe(
            ctypes.byref(read_h), ctypes.byref(write_h), ctypes.byref(sa), 0
        ):
            raise OSError(f"CreatePipe failed with error {ctypes.get_last_error()}")

        WindowsJobObjectBackend._set_inherit(read_h, read_inheritable)
        WindowsJobObjectBackend._set_inherit(write_h, write_inheritable)
        return read_h, write_h

    @staticmethod
    def _set_inherit(handle, inherit: bool) -> None:
        flags = HANDLE_FLAG_INHERIT if inherit else 0
        if not _kernel32.SetHandleInformation(handle, HANDLE_FLAG_INHERIT, flags):
            log.warning(
                "SetHandleInformation failed: %s", ctypes.get_last_error()
            )

    @staticmethod
    def _build_environment_block(env) -> Optional[str]:
        """Build a Unicode environment block: ``K=V\\0K=V\\0\\0``."""
        if not env:
            return None
        return "\0".join(f"{key}={value}" for key, value in sorted(env.items())) + "\0\0"

    @staticmethod
    def _write_stdin(handle, data: bytes) -> None:
        if not handle or not data:
            return
        written = wintypes.DWORD(0)
        ok = _kernel32.WriteFile(
            handle, data, len(data), ctypes.byref(written), None
        )
        if not ok:
            log.warning("WriteFile (stdin) failed: %s", ctypes.get_last_error())

    def _capture_with_limit(
        self,
        *,
        process_handle,
        job_handle,
        stdout_handle,
        stderr_handle,
        max_bytes: int,
        timeout: float,
    ):
        """Read output without deadlocking, enforcing timeout and byte budget.

        Ordering guarantee: on timeout or output-limit the job is terminated
        *before* any further read, and the process is awaited, so the bounded
        drain that follows is non-blocking.
        """
        k = _kernel32
        start = time.monotonic()
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        total = 0
        timed_out = False
        output_limited = False

        while True:
            if time.monotonic() - start > timeout:
                timed_out = True
                self._terminate_tree(job_handle, process_handle)
                break

            if total >= max_bytes:
                output_limited = True
                self._terminate_tree(job_handle, process_handle)
                break

            if k.WaitForSingleObject(process_handle, 100) == WAIT_OBJECT_0:
                break

            total = self._drain_available(
                stdout_handle, stdout_chunks, stderr_handle, stderr_chunks, total, max_bytes
            )

        # Bounded, non-blocking drain of whatever is already buffered.
        total = self._drain_available(
            stdout_handle, stdout_chunks, stderr_handle, stderr_chunks, total, max_bytes
        )

        return (
            b"".join(stdout_chunks),
            b"".join(stderr_chunks),
            timed_out,
            output_limited,
        )

    @staticmethod
    def _drain_available(
        stdout_handle, stdout_chunks, stderr_handle, stderr_chunks, total, max_bytes
    ) -> int:
        """Read only bytes already buffered (PeekNamedPipe), never blocking."""
        k = _kernel32
        for handle, chunks in ((stdout_handle, stdout_chunks), (stderr_handle, stderr_chunks)):
            while total < max_bytes:
                available = wintypes.DWORD(0)
                if not k.PeekNamedPipe(
                    handle, None, 0, None, ctypes.byref(available), None
                ):
                    break
                if available.value == 0:
                    break
                to_read = min(available.value, _READ_CHUNK, max_bytes - total)
                if to_read <= 0:
                    break
                buf = ctypes.create_string_buffer(to_read)
                read = wintypes.DWORD(0)
                if not k.ReadFile(handle, buf, to_read, ctypes.byref(read), None):
                    break
                if read.value == 0:
                    break
                chunks.append(buf.raw[: read.value])
                total += read.value
        return total




