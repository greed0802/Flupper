# Flupper

**Autonomous Civil QS Agent Platform** — an auditable, evidence-first Quantity
Surveying agent for Australian civil tendering.

Deterministic Python does the maths; the model only decides which tool to call.
Every quantity carries a drawing number, revision, sheet, zone and file hash, and
every response passes the **QS CheckMate** gate before it reaches a human.

- Architecture and roadmap → [`docs/ROADMAP.md`](docs/ROADMAP.md)
- Compressed agent context → [`docs/CONTEXT.md`](docs/CONTEXT.md)
- Local API runbook → [`docs/LOCAL_API_RUNBOOK.md`](docs/LOCAL_API_RUNBOOK.md)
- Backend → [`backend/`](backend/)

## Current status

| Phase | Scope | Status |
|---|---|---|
| 1 | Storage, evidence schemas, Tier 1 QS math, CheckMate | ✅ complete |
| 2 | PDF/Mudshark ingestion and knowledge graph | ✅ complete |
| 3A–3D | Agent runtime, sandbox, state machine, BYOK router | ✅ complete |
| 4A | Local FastAPI gateway | ✅ complete |
| 4B | Bearer authentication and secure route boundary | ✅ complete |
| 4C | Controlled Cloudflare Tunnel exposure | ✅ implementation complete — synthetic/local verification only |
| 5A | Read-only Flutter workstation shell | ✅ complete; Chrome verified in CI |
| 5B | Read-only server-side revision diff | ✅ complete |
| 5C | Evidence-backed rate normalization | 🟡 planning |
| 5D | Artifact/export engine | ⬜ planned |
| 6 | Tender portal automation and risk engine | ⬜ planned |

Phase 4A/4B remain local and single-worker. The API binds to `127.0.0.1`,
requires `FLUPPER_API_TOKEN`, and must not be exposed through a tunnel until
Phase 4C is reviewed and explicitly approved. The owner-controlled tunnel
hostname is supplied out-of-band; no real hostname or tunnel credential belongs
in tracked source.

## Development and verification

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
.venv/bin/pytest                 # run from repository root
(cd backend && ../.venv/bin/pytest)
```

Current verified Python baseline for the Phase 5B commit:

```text
Repo root: 487 collected, 485 passed, 2 skipped
backend/:  487 collected, 485 passed, 2 skipped
```

The skips are environment-bound: the tunnel connector test requires `cloudflared`,
and `test_phase2_real.py` requires the workstation-only `FLUPPER_REAL_PROJECT`
dataset. Skips are not counted as passes.

## Local API launch

The launcher fails closed unless a bearer token is supplied out of band:

```bash
export FLUPPER_API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
PYTHONPATH=backend .venv/bin/python -m qsagent.api.run_local
```

The server remains bound to exact `127.0.0.1` with one Uvicorn worker. Phase 4C
remains opt-in and has not established a production tunnel. Do not
place real client data, prompts, credentials, tunnel manifests, or tunnel
credentials in Git.

Shutdown, recovery, and the full authenticated health check are documented in
the [local API runbook](docs/LOCAL_API_RUNBOOK.md).
