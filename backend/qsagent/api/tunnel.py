"""Controlled Cloudflare Tunnel launcher (Phase 4, task C).

Transport from the Cloudflare edge to the local gateway, and nothing else.

Security posture
----------------
* **The gateway stays on loopback.** The origin is a compiled-in constant,
  ``http://127.0.0.1:8000``. It is not read from the manifest, the environment or
  an argument, so no configuration mistake and no caller can point this at
  ``0.0.0.0``, at another host, or at a different local port - which is what
  keeps it from becoming a general-purpose forwarder for whatever else happens
  to be listening on the workstation.
* **Opt-in, never automatic.** Nothing in :mod:`qsagent.api.main` or
  :mod:`qsagent.api.run_local` imports this module. The tunnel starts only when
  this module is executed by hand, with a manifest path given as an argument.
* **Named tunnel only.** ``cloudflared tunnel --config <file> run``. Never
  ``--url``, so there is no anonymous ``trycloudflare.com`` address and no
  unauthenticated public entry point.
* **Bearer authentication is the authorisation boundary, not the tunnel.** The
  tunnel is transport. It neither supplies nor inspects ``FLUPPER_API_TOKEN``;
  requests still have to satisfy ``require_bearer_token`` at the gateway. A
  tunnel that is up but unauthenticated exposes nothing.
* **No secret in this module's output.** The credential file is checked for
  existence and never opened, parsed or logged. Error messages name the rule
  that failed - and, for unknown keys, the keys *we* allow - never a value from
  the manifest, the environment or the filesystem.

Stopping and revoking
---------------------
* **Kill switch.** ``Ctrl+C`` (or ``SIGTERM``) on this wrapper stops the
  ``cloudflared`` process group and removes the temporary config. There is no
  supervisor and no daemon mode, so nothing restarts it afterwards.
* **Revocation.** Deleting the tunnel, or the DNS record for its hostname, in
  the Cloudflare dashboard - or ``cloudflared tunnel delete`` - invalidates the
  credential independently of this process.

Usage::

    FLUPPER_API_TOKEN=<token> FLUPPER_TUNNEL_HOSTNAME=api.example.invalid \
        python -m qsagent.api.run_tunnel tunnel-manifest.json

    # validate only; does not contact Cloudflare and does not need cloudflared
    python -m qsagent.api.run_tunnel --check tunnel-manifest.json
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .main import API_TOKEN_ENV_VAR

log = logging.getLogger(__name__)

CLOUDFLARED_BINARY = "cloudflared"

# The one origin the tunnel may reach. Constant on purpose: see the module
# docstring. ``tests/test_api_tunnel.py`` asserts it still matches the address
# ``run_local`` binds, so the two cannot drift apart silently.
TUNNEL_ORIGIN_SERVICE = "http://127.0.0.1:8000"
CATCH_ALL_SERVICE = "http_status:404"

TUNNEL_HOSTNAME_ENV_VAR = "FLUPPER_TUNNEL_HOSTNAME"

# Exact manifest schema. Anything else is rejected rather than ignored.
MANIFEST_KEYS = frozenset({"tunnel", "credentials-file", "ingress"})
API_INGRESS_KEYS = frozenset({"hostname", "service"})
CATCH_ALL_INGRESS_KEYS = frozenset({"service"})

MAX_HOSTNAME_CHARS = 253
MAX_TUNNEL_NAME_CHARS = 63

# One DNS label: alphanumeric ends, hyphens inside, at most 63 characters. The
# character class alone is what rejects a wildcard (``*``), a scheme (``:``), a
# port (``:``), a path (``/``), whitespace and every control character.
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
# At least two labels, so a bare ``localhost`` is not a tunnel hostname.
_HOSTNAME_RE = re.compile(rf"^(?:{_LABEL}\.)+{_LABEL}$")
_TUNNEL_NAME_RE = re.compile(rf"^[A-Za-z0-9][A-Za-z0-9._-]{{0,{MAX_TUNNEL_NAME_CHARS - 1}}}$")


class TunnelConfigError(ValueError):
    """A manifest, or the environment around it, failed a rule.

    Subclasses :class:`ValueError` so a caller that already catches that keeps
    working. Carries a fixed sentence describing the *rule*; never the value
    that broke it.
    """


def _valid_hostname(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > MAX_HOSTNAME_CHARS:
        return False
    return bool(_HOSTNAME_RE.match(value))


def _valid_tunnel_identifier(value: object) -> bool:
    """A canonical UUID, or a simple tunnel name.

    A name is accepted because Cloudflare lets an operator address a tunnel by
    name, but it is restricted to characters that cannot smuggle a path, a flag
    or a second argument into the config document.
    """
    if not isinstance(value, str) or not value:
        return False
    try:
        # Canonical hyphenated form only. A hex string without the hyphens is a
        # valid UUID to ``uuid`` but is not what Cloudflare stores, so it falls
        # through to the name check rather than being accepted as either.
        if str(uuid.UUID(value)) == value:
            return True
    except ValueError:
        pass
    if value.startswith(".") or ".." in value:
        return False
    return bool(_TUNNEL_NAME_RE.match(value))


def read_configured_hostname(environ: Mapping[str, str] | None = None) -> str:
    """The hostname this deployment may serve, lower-cased.

    The manifest has to agree with this value, which is what stops an edited - or
    borrowed - manifest from quietly publishing a name the operator never
    configured. It is read from the environment and never written anywhere
    except the short-lived config handed to ``cloudflared``.
    """
    source = os.environ if environ is None else environ
    value = (source.get(TUNNEL_HOSTNAME_ENV_VAR) or "").strip()
    if not value:
        raise TunnelConfigError(f"{TUNNEL_HOSTNAME_ENV_VAR} is not set")
    if not _valid_hostname(value):
        raise TunnelConfigError(f"{TUNNEL_HOSTNAME_ENV_VAR} is not a valid hostname")
    return value.lower()


def require_api_token_configured(environ: Mapping[str, str] | None = None) -> None:
    """Fail closed unless an API token is exported for the gateway.

    A presence check and nothing more. It shows an operator intended an
    authenticated gateway to be running; it cannot prove the running gateway
    holds the same value, and it authorises nothing. The token is never read,
    copied, compared, forwarded or logged here - the tunnel is transport, and
    ``require_bearer_token`` at the gateway remains the boundary.
    """
    source = os.environ if environ is None else environ
    if not (source.get(API_TOKEN_ENV_VAR) or "").strip():
        raise TunnelConfigError(
            f"{API_TOKEN_ENV_VAR} must be set before the gateway is exposed"
        )


def _resolve_credentials_file(value: object) -> Path:
    """Validate the credential *reference*. The file is never opened."""
    if not isinstance(value, str) or not value:
        raise TunnelConfigError("credentials-file must be a non-empty string")
    path = Path(value)
    if not path.is_absolute():
        raise TunnelConfigError("credentials-file must be an absolute path")
    try:
        if not path.exists():
            raise TunnelConfigError("credentials-file does not exist")
        if not path.is_file():
            raise TunnelConfigError("credentials-file must be a regular file")
    except OSError:
        raise TunnelConfigError("credentials-file could not be inspected") from None
    return path


def _resolve_ingress(rules: object, expected_hostname: str) -> str:
    """Return the validated API hostname, or refuse the whole manifest.

    The list is checked as an exact shape - one hostname rule, then one
    catch-all - rather than sanitised. An operator who adds a rule has made a
    decision this validator is not entitled to guess at, and a catch-all placed
    ahead of the hostname rule would silently swallow it.
    """
    if not isinstance(rules, list) or len(rules) != 2:
        raise TunnelConfigError(
            "ingress must hold exactly two rules: the API hostname, then a catch-all"
        )

    api_rule, catch_all_rule = rules

    if not isinstance(api_rule, dict) or set(api_rule) != API_INGRESS_KEYS:
        raise TunnelConfigError(
            "ingress rule 1 must have exactly the keys: hostname, service"
        )
    if not isinstance(catch_all_rule, dict) or set(catch_all_rule) != CATCH_ALL_INGRESS_KEYS:
        raise TunnelConfigError("ingress rule 2 must have exactly the key: service")

    hostname = api_rule.get("hostname")
    if not _valid_hostname(hostname):
        raise TunnelConfigError("ingress rule 1 hostname is not a valid hostname")
    if hostname.lower() != expected_hostname:
        raise TunnelConfigError(
            "manifest hostname does not match the configured tunnel hostname"
        )
    if api_rule.get("service") != TUNNEL_ORIGIN_SERVICE:
        raise TunnelConfigError(
            f"ingress rule 1 service must be exactly {TUNNEL_ORIGIN_SERVICE}"
        )
    if catch_all_rule.get("service") != CATCH_ALL_SERVICE:
        raise TunnelConfigError(
            f"ingress rule 2 service must be exactly {CATCH_ALL_SERVICE}"
        )

    return hostname.lower()


@dataclass(frozen=True)
class TunnelManifest:
    """A manifest that passed every rule, reduced to what cloudflared needs."""

    tunnel: str
    credentials_file: Path
    hostname: str

    def to_cloudflared_config(self) -> dict[str, object]:
        """The exact document written for ``cloudflared``.

        Rebuilt from the validated fields rather than copied from the manifest:
        the origin and the catch-all come from this module's constants, so what
        runs cannot disagree with what was checked.
        """
        return {
            "tunnel": self.tunnel,
            "credentials-file": str(self.credentials_file),
            "ingress": [
                {"hostname": self.hostname, "service": TUNNEL_ORIGIN_SERVICE},
                {"service": CATCH_ALL_SERVICE},
            ],
        }


def validate_manifest(raw: object, *, expected_hostname: str) -> TunnelManifest:
    """Check a parsed manifest against the schema and the configured hostname."""
    if not isinstance(raw, dict):
        raise TunnelConfigError("manifest must be a JSON object")

    if set(raw) - MANIFEST_KEYS:
        # Names the keys *we* accept, never the keys that were found: a manifest
        # is a file an operator can be handed, and echoing its contents back into
        # a log is how a path or a token escapes one.
        allowed = ", ".join(sorted(MANIFEST_KEYS))
        raise TunnelConfigError(f"manifest has unexpected keys; allowed: {allowed}")

    if not _valid_hostname(expected_hostname):
        raise TunnelConfigError(f"{TUNNEL_HOSTNAME_ENV_VAR} is not a valid hostname")

    tunnel_id = raw.get("tunnel")
    if not _valid_tunnel_identifier(tunnel_id):
        raise TunnelConfigError("tunnel must be a UUID or a simple tunnel name")

    hostname = _resolve_ingress(raw.get("ingress"), expected_hostname.lower())
    credentials_file = _resolve_credentials_file(raw.get("credentials-file"))

    return TunnelManifest(
        tunnel=tunnel_id, credentials_file=credentials_file, hostname=hostname
    )


def load_manifest(
    path: Path | str, *, environ: Mapping[str, str] | None = None
) -> TunnelManifest:
    """Read, parse and validate a manifest. Nothing here contacts Cloudflare."""
    require_api_token_configured(environ)

    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        raise TunnelConfigError("manifest could not be read") from None

    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        raise TunnelConfigError("manifest is not valid JSON") from None

    return validate_manifest(raw, expected_hostname=read_configured_hostname(environ))


class _TempConfigFile:
    """The validated config on disk while ``cloudflared`` runs.

    ``mkstemp`` creates the file owner-only on POSIX, so it is not world-readable
    while it exists. Removal is best-effort and no more: the file is unlinked in
    ``cleanup`` (called from ``finally``) and again at interpreter exit.

    That is deliberately not described as a secure erase. On an SSD, a
    copy-on-write filesystem, or a Windows volume with shadow copies, the blocks
    may well outlive the unlink, and claiming otherwise would be a worse answer
    than saying so. The file holds a hostname, a tunnel identifier and two paths;
    the credential itself is referenced by path and read only by ``cloudflared``.
    """

    def __init__(self, payload: Mapping[str, object]) -> None:
        descriptor, name = tempfile.mkstemp(prefix="flupper-tunnel-", suffix=".json")
        self.path = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
        atexit.register(self.cleanup)

    def cleanup(self) -> None:
        """Idempotent: an already-removed file is not an error."""
        try:
            self.path.unlink()
        except OSError:
            pass


def _spawn_cloudflared(config_path: Path) -> subprocess.Popen:
    """Start the connector in its own process group.

    ``start_new_session`` (POSIX) and ``CREATE_NEW_PROCESS_GROUP`` (Windows) both
    matter for one reason: without them the child shares this process's group, so
    the group-directed signal ``_terminate`` uses would also hit the wrapper - or
    miss the connector entirely.

    stdout and stderr are deliberately *inherited* rather than piped. A pipe
    nobody drains deadlocks the moment ``cloudflared`` fills its buffer, and there
    is nothing here to redact: the connector logs its own transport events, and no
    credential is passed on its command line. The only path in argv is the
    temporary config, which holds no secret.
    """
    if shutil.which(CLOUDFLARED_BINARY) is None:
        raise FileNotFoundError(CLOUDFLARED_BINARY)

    extra: dict[str, object] = {}
    if os.name == "nt":
        # Windows has no process group in the POSIX sense - CTRL_BREAK_EVENT needs
        # an attached console - so shutdown terminates the connector directly.
        extra["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        extra["start_new_session"] = True

    return subprocess.Popen(
        [CLOUDFLARED_BINARY, "tunnel", "--config", str(config_path), "run"],
        **extra,
    )


def _terminate(process: subprocess.Popen, *, timeout: float = 5.0) -> int | None:
    """Stop the connector, escalating if it will not go.

    Idempotent by construction: an already-exited process short-circuits on
    ``poll``, so this is safe to call from a signal handler, a failure path and
    the normal exit path, in any order and any number of times.
    """
    if process.poll() is not None:
        return process.returncode

    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass

    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        pass

    try:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass

    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def _install_stop_handlers(process: subprocess.Popen) -> None:
    """Turn Ctrl+C into an orderly stop of the connector, not of this process."""

    def _stop(_signum: int, _frame: object) -> None:
        _terminate(process)

    for name in ("SIGINT", "SIGTERM"):
        number = getattr(signal, name, None)
        if number is None:
            continue
        try:
            signal.signal(number, _stop)
        except (ValueError, OSError):
            # Not the main thread, or the platform refused the handler. The
            # ``finally`` in ``main`` still stops the connector on the way out.
            continue


def main(argv: Sequence[str] | None = None) -> int:
    """Validate a manifest, then supervise ``cloudflared`` until it stops."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    args = list(sys.argv[1:] if argv is None else argv)
    check_only = bool(args) and args[0] == "--check"
    if check_only:
        args = args[1:]
    if len(args) != 1:
        raise SystemExit(
            "usage: python -m qsagent.api.run_tunnel [--check] <manifest.json>"
        )

    try:
        manifest = load_manifest(args[0])
    except TunnelConfigError as exc:
        # ``exc`` names the rule that failed and no value from the manifest, the
        # environment or the filesystem, so it is safe to surface.
        raise SystemExit(f"tunnel configuration rejected: {exc}") from None

    if check_only:
        # Validation is complete and nothing has been contacted. The hostname is
        # not logged: it is the one operator value this process holds.
        log.info("tunnel manifest accepted")
        return 0

    if shutil.which(CLOUDFLARED_BINARY) is None:
        raise SystemExit(f"{CLOUDFLARED_BINARY} is not on PATH; install it and retry")

    temporary = _TempConfigFile(manifest.to_cloudflared_config())
    try:
        process = _spawn_cloudflared(temporary.path)
    except OSError:
        temporary.cleanup()
        raise SystemExit(f"could not start {CLOUDFLARED_BINARY}") from None

    _install_stop_handlers(process)
    log.info("tunnel connector started; Ctrl+C to stop")
    try:
        return process.wait()
    except KeyboardInterrupt:  # reachable only if the handler was refused
        _terminate(process)
        return process.wait()
    finally:
        _terminate(process)
        temporary.cleanup()


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
