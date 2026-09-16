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
- 5C: read-only evidence-backed rate proposals with Decimal normalization.
- 5D: bounded temporary BOQ XLSX artifacts with authenticated download.

## Current next phase
Plan the tablet/mobile Flutter trial. Do not implement production tunnel exposure
until separately approved. Consume existing API contracts before adding backend
features.

## Verified baseline
Latest Phase 5D commit: `9df7c13`.
Python: **743 collected, 741 passed, 2 skipped** from root and `backend/`.
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

## Known provenance limitations
Phase 5B reports unassociated claims where ingest does not preserve locator
lineage. Phase 5C reports unresolved rate proposals when source evidence is
insufficient. Phase 5D exports preserve these states and never fabricate evidence.

## Layout
`backend/qsagent/` contains API, revisions, rates, artifacts, runtime, storage,
ingest, and CheckMate. `workstation/` contains the Flutter client.
`masterfile/` is real data and ignored.
