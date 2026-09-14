# Local API Runbook — Phase 4A / 4B

Operational runbook for the authenticated local gateway delivered by Phase 4A
(FastAPI app factory) and Phase 4B (bearer boundary).

Everything below describes behaviour that exists in tracked source, in
`backend/qsagent/api/run_local.py` and `backend/qsagent/api/main.py`. Nothing
here is aspirational.

## 1. Scope

Covered:

- Launching the local gateway on this workstation.
- The bearer token requirement and where the token comes from.
- Verifying liveness with an authenticated request.
- Stopping the process cleanly and recovering afterwards.

Not covered:

- **Phase 4C (Cloudflare Tunnel) is out of scope.** Exposure of this gateway
  beyond loopback is not documented here, is not implied by any command below,
  and must be separately reviewed and explicitly approved before it happens.
  The owner-controlled tunnel hostname and any tunnel credential are supplied
  out of band and never belong in tracked source.

The gateway is transport only. No quantity, rate, or CheckMate rule lives in
`qsagent/api/`; a runbook problem is a transport problem, not a measurement
problem.

## 2. Verified launch posture

These are module-level constants in `run_local.py`, not environment-tunable
settings. Changing one is a code change, reviewed as such.

| Property | Verified value | Why it is fixed |
|---|---|---|
| Bind address | exact `127.0.0.1` (`BIND_HOST`) | Loopback only. A constant means no configuration mistake can publish a process-executing API on a LAN interface. |
| Port | `8000` (`BIND_PORT`) | Constant; see §6 if the port is already taken. |
| Uvicorn workers | `1` (`WORKERS`) | Approval registry and live sessions are in-process. A second worker would not share them, so an approval minted by one worker would be invisible to another. |
| Auto-reload | disabled (`RELOAD = False`) | The reload supervisor runs a second process with its own registry, reintroducing the split-state problem above. |
| API routes | all under `/api/v1` (`API_PREFIX`) | The token dependency is attached to the router, not per handler, so a route added later cannot be left unauthenticated by omission. |
| `/api/v1/health` | authenticated | Deliberately. A supervisor liveness probe that must bypass authentication belongs on the supervisor, not on a route that reports sandbox state. |
| Docs routes | `/docs`, `/redoc`, `/openapi.json` are not published | No schema is served to a caller. |
| Request body ceiling | 64 KiB (`DEFAULT_MAX_BODY_BYTES`) | Oversized bodies are rejected through the standard error envelope. |
| CORS origins | `http://localhost:5000`, `http://127.0.0.1:5000` | No wildcard, and no cookies: the token travels in the `Authorization` header only. |

## 3. Environment variables

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `FLUPPER_API_TOKEN` | **yes** | none | Bearer token every `/api/v1/*` request must present. Missing, empty, or whitespace-only means the process refuses to start. |
| `FLUPPER_DB` | no | `flupper.db` | Path to the SQLite evidence store, resolved relative to the current working directory. |
| `FLUPPER_SANDBOX_ROOT` | no | `sandbox_runs` | Parent directory for Tier 3 run workspaces, resolved relative to the current working directory. |
| `FLUPPER_KEY_<PROVIDER>` | no | none | BYOK provider key read per call from the process environment. A provider with no key simply cannot be routed to. |

The token is resolved **before** the store is opened, so a misconfigured launch
fails without creating a database file on its way to failing.

## 4. Token handling

Rules, all enforced by construction in `run_local.py`:

- The process **never mints** a token. It reads `FLUPPER_API_TOKEN` or exits.
- The process **never prints** the token, and no failure message contains it —
  the startup error names the missing variable and how to produce a value.
- Provider keys are read from the environment at call time and never cached on
  an object, logged, or returned to a caller.

Generate the token **outside the repository**, so no editor buffer, tracked
file, or repository-adjacent scratch file ever holds it.

POSIX:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

PowerShell:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Keep the value in your shell session or an out-of-repo secret store. Do **not**
write it to a file inside the working tree, and do not commit it — a token that
reaches stdout has already been copied into a terminal scrollback, an IDE panel,
a service manager's journal, or a CI log.

## 5. Launch

Dependencies come from `backend/requirements.txt`; the commands below run the
package from `backend/` via `PYTHONPATH`. Python 3.12 is the verified
interpreter (same version pinned in `.github/workflows/verify.yml`).

POSIX:

```bash
export FLUPPER_API_TOKEN="<token generated in §4>"
export FLUPPER_DB="$PWD/flupper.db"
export FLUPPER_SANDBOX_ROOT="$PWD/sandbox_runs"
PYTHONPATH=backend .venv/bin/python -m qsagent.api.run_local
```

PowerShell:

```powershell
$env:FLUPPER_API_TOKEN = "<token generated in §4>"
$env:FLUPPER_DB = "$PWD\flupper.db"
$env:FLUPPER_SANDBOX_ROOT = "$PWD\sandbox_runs"
$env:PYTHONPATH = "backend"
.venv\Scripts\python.exe -m qsagent.api.run_local
```

Confirm the bind line in the startup log before sending anything. The server is
listening on `127.0.0.1:8000` and nowhere else.

## 6. Verify with an authenticated health request

`/api/v1/health` is the smallest authenticated round trip. It returns two fixed
fields and nothing else — no session, no project, no credentials, no internals:

```json
{"status": "ok", "sandbox_root_ready": true}
```

`status` is liveness. `sandbox_root_ready` reports whether the server-owned
sandbox parent is a directory, so a runbook check can distinguish "the gateway
is up" from "the gateway is up and Tier 3 can actually run".

POSIX:

```bash
curl -sS -H "Authorization: Bearer $FLUPPER_API_TOKEN" \
  http://127.0.0.1:8000/api/v1/health
```

PowerShell:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/health" `
  -Headers @{ Authorization = "Bearer $env:FLUPPER_API_TOKEN" }
```

Authentication failures are deliberately indistinguishable. A missing header,
a wrong scheme, empty credentials, and a mismatched token all return the same
401 with a fixed non-reflective message, compared in constant time — a rejected
caller cannot tell whether a guess was well-formed, and cannot read the
expected value back out of any response.

If port 8000 is already in use, stop the process holding it. Do not rebind to
another interface or expose the port to work around it; both are Phase 4C
review decisions, not runbook steps.

## 7. Shutdown

Stop the foreground process with `Ctrl-C` (SIGINT). Uvicorn stops accepting new
connections, lets in-flight requests finish, and exits. That is the only
supported stop.

Do not reach for a forced kill as a first response. A forced kill can interrupt
an in-progress Tier 3 run and its journal write, which is exactly the state you
do not want to debug from.

## 8. Recovery

State is split between durable and in-process, and recovery depends on knowing
which is which:

| State | Lives in | Survives restart |
|---|---|---|
| Projects, evidence, audit journal, Tier 3 run history | SQLite store at `FLUPPER_DB` | **yes** |
| Live sessions, Tier 3 approval registry, action bindings | process memory | no |

Procedure:

1. Restart the gateway with the **same** `FLUPPER_DB` value and a
   `FLUPPER_API_TOKEN` value. A new token is fine; a new database path is not a
   restart, it is a different install.
2. Re-open an existing project with `GET /api/v1/projects/{project_id}`.
3. Instantiate a fresh session for that project
   (`POST /api/v1/projects/{project_id}/session/plan`). That route is the only
   one that creates a session, and it returns 404 for a project id that does not
   exist rather than inventing one.
4. Re-mint any approval you still need. Approvals do not survive a restart —
   they were bound to a process, and replaying an old approval id is not a
   supported recovery path.

### Preserve the SQLite database

- **Never delete `flupper.db` to "clean up" a restart.** It holds manual QS
  entries, ingested evidence, and the audit journal. Deleting it is data loss,
  not a fix.
- If the write-ahead journal is present alongside it, leave `flupper.db-wal` and
  `flupper.db-shm` in place. Removing them discards commits that have not been
  checkpointed yet.
- Copy, do not move, the database if you need a backup before an upgrade.
- `sandbox_runs` is disposable run scratch, not evidence. Prune it only when no
  Tier 3 run is in flight.

## 9. Server-owned sandbox

The sandbox root is chosen by the server, not by a request:

- The launcher resolves `FLUPPER_SANDBOX_ROOT` (default `sandbox_runs`) and
  passes it to the app; a caller cannot supply a path per request.
- The app creates the directory (`parents=True`, `exist_ok=True`) and, on POSIX,
  sets mode `0700`. The account running the gateway is the only account that can
  execute Tier 3 commands, so the root is private to that account rather than a
  world-writable temporary directory. Windows has no mode equivalent, which is
  documented rather than pretended otherwise.
- Per-run workspaces are created inside that root by the server. Approval action
  ids are derived server-side from the canonical request hash, so the id a
  client must present to execute is not client-choosable.
- `/api/v1/health` reports only whether the root is ready. It never reports the
  path or its contents.

## 10. Git hygiene

Tracked source may contain variable names and synthetic examples. It must not
contain:

- real client or project data of any kind;
- real prompts;
- credentials, tokens, or provider keys of any shape;
- tunnel manifests, tunnel credentials, or real tunnel hostnames;
- real project or workstation filesystem paths.

`tools/verify_tracked_hygiene.py` runs in CI over `.clinerules/`, `docs/`, and
`backend/qsagent/api/`, failing on credential-shaped values and on any
non-synthetic API hostname. Use placeholder paths (`<repo-root>`, `<token
generated in §4>`) and a reserved synthetic hostname when an example needs a
host at all.

## 11. Related documents

- Architecture and phase status → [`ROADMAP.md`](ROADMAP.md)
- Compressed agent context → [`CONTEXT.md`](CONTEXT.md)
- Repository overview and verified test baseline → [`../README.md`](../README.md)
