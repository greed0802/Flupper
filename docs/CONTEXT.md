# Flupper — current compressed context

## Project
Autonomous Civil QS platform. Evidence-first, auditable QS quantities.
Deterministic Python performs arithmetic; CheckMate gates claims and delivery.

## Completed phases
- 1: storage, evidence schemas, Tier 1 QS math, CheckMate.
- 2: PDF/Mudshark ingestion and knowledge graph.
- 3A–3D: AgentSession, sandbox, ModelRouter, approvals and delivery gates.
- 4A: local FastAPI gateway, bounded DTOs, locks, approval bindings.
- 4B: mandatory bearer auth on every `/api/v1/*` route.
- 4C: opt-in Cloudflare Tunnel validator/wrapper; synthetic/local verification
  only. No production tunnel or real hostname is tracked or published.
- 5A: read-only Flutter workstation shell; memory-only auth; Chrome verified in CI.
- 5B: read-only server-side revision diff over stored evidence.

## Current next phase
Phase 5C planning: evidence-backed rate normalization. Phase 5D artifact export
and production tunnel deployment remain separate approvals.

## Verified baseline
Latest Phase 5B commit: `50780f1`.
Python: **487 collected, 485 passed, 2 skipped** from root and `backend/`.
Skips: cloudflared unavailable in the verifier and `FLUPPER_REAL_PROJECT` absent.
Flutter 5A: analyze, VM tests, Web build, and sequential Chrome CI passed.

## API and security boundaries
- API binds to exact `127.0.0.1`, one worker, bearer token required.
- `FLUPPER_API_TOKEN` is never committed, logged, or persisted by the client.
- Tunnel hostname is supplied out-of-band through `FLUPPER_TUNNEL_HOSTNAME`;
  tracked docs use only `api.example.invalid`.
- No real project data, prompts, credentials, probe reports, or client names in Git.
- No external production egress, Kaggle, or live tunnel during verification.
- Run pytest from repository root and `backend/`; report collected/passed/skipped.

## Phase 5B data limitation
Revision diff is read-only and bounded. Existing ingest paths do not reliably
store locator lineage on evidence nodes, so unmatched claims are reported as
unassociated rather than guessed. In-place re-ingests may produce
`evidence_unavailable`; the diff never fabricates removals.

## Layout
`backend/qsagent/` contains API, revisions, runtime, storage, ingest, and CheckMate.
`workstation/` contains the Flutter client. `masterfile/` is real data and ignored.
