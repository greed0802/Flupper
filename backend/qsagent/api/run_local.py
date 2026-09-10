"""Manual launcher for the local gateway.

Security posture
----------------
* **Loopback only.** ``BIND_HOST`` is a constant, not an environment variable,
  so no configuration mistake can publish a process-executing API on a LAN
  interface. Phase 4 exposes it deliberately through a Cloudflare Tunnel.
* **Single worker, enforced.** The approval registry and the live sessions are
  in-process. A second worker would not share them: an approval minted by one
  worker would be invisible to another. One worker is not a preference here, it
  is the only configuration in which the handshake works, so it is asserted
  rather than defaulted and hoped for.
* **No auto-reload.** The reload supervisor runs a second process with its own
  registry, which reintroduces exactly the split-state problem above.
* **No credential in this module's output.** Keys are read from the process
  environment at call time and handed straight to the router.
* **No generated or printed token.** The gateway's bearer token comes from
  ``FLUPPER_API_TOKEN`` or the process does not start. This module never mints
  one and never writes one out: a token that reaches stdout has been copied
  into a terminal scrollback, an IDE panel, a service manager's journal or a CI
  log, which makes it a leaked secret rather than a convenience. Generate one
  out of band and export it::

      python -c "import secrets; print(secrets.token_urlsafe(32))"

Usage::

    FLUPPER_API_TOKEN=<token> python -m qsagent.api.run_local
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path

import uvicorn

from ..runtime import ModelRouter
from ..storage import QSStore
from .main import DEFAULT_SANDBOX_ROOT, create_app

BIND_HOST = "127.0.0.1"
BIND_PORT = 8000
WORKERS = 1
RELOAD = False

KEY_ENV_PREFIX = "FLUPPER_KEY_"
DB_ENV_VAR = "FLUPPER_DB"
SANDBOX_ROOT_ENV_VAR = "FLUPPER_SANDBOX_ROOT"
API_TOKEN_ENV_VAR = "FLUPPER_API_TOKEN"
DEFAULT_DB_NAME = "flupper.db"


class EnvironmentSecretProvider:
    """Resolve provider keys from the process environment, and nowhere else.

    Deliberately minimal: no key file, no keyring, no network lookup. The value
    is read per call so a exported key takes effect without rebuilding the
    router, and it is never cached on the instance, logged, or returned to a
    caller - a key that never becomes an attribute cannot leak through a repr.
    """

    def __init__(self, prefix: str = KEY_ENV_PREFIX) -> None:
        self._prefix = prefix

    def get_key(self, provider: str) -> str | None:
        name = f"{self._prefix}{provider.upper().replace('-', '_')}"
        return os.environ.get(name) or None


def read_api_token(environ: Mapping[str, str] | None = None) -> str:
    """Return the configured bearer token, or stop the process.

    Fail-closed by construction: an unset, empty or whitespace-only variable is
    a startup failure, never a licence to serve unauthenticated requests. No
    token is generated here and none is printed - the message names the missing
    variable and how to produce a value, and nothing else.

    ``environ`` is injectable so the behaviour above can be tested without
    mutating the real process environment.
    """
    source = os.environ if environ is None else environ
    token = (source.get(API_TOKEN_ENV_VAR) or "").strip()
    if not token:
        raise SystemExit(
            f"{API_TOKEN_ENV_VAR} is not set; refusing to start the gateway. "
            "Export a strong random value first, for example: "
            'python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    return token


def build_app(
    *,
    db_path: Path | str,
    sandbox_root: Path | str,
    api_token: str,
    secret_provider: EnvironmentSecretProvider | None = None,
):
    """Wire a real store and a real BYOK router into the gateway factory."""
    store = QSStore(str(db_path))
    router = ModelRouter(secret_provider or EnvironmentSecretProvider())
    return create_app(
        store=store,
        router=router,
        api_token=api_token,
        sandbox_root=Path(sandbox_root),
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Resolved first, before the store is opened: a misconfigured launch should
    # not create a database file on its way to failing.
    api_token = read_api_token()
    db_path = Path(os.environ.get(DB_ENV_VAR, DEFAULT_DB_NAME))
    sandbox_root = Path(os.environ.get(SANDBOX_ROOT_ENV_VAR, DEFAULT_SANDBOX_ROOT))
    app = build_app(
        db_path=db_path, sandbox_root=sandbox_root, api_token=api_token
    )
    uvicorn.run(app, host=BIND_HOST, port=BIND_PORT, workers=WORKERS, reload=RELOAD)


if __name__ == "__main__":  # pragma: no cover - process entry point
    main()
