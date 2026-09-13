# Flupper — Autonomous Civil QS Agent Platform

An auditable, evidence-first Quantity Surveying agent platform for Australian
civil tendering.

## Execution cycle

```
RECEIVED ─► PLANNED ─► EXECUTING ─► VALIDATING ─► REASONING ─► DELIVERED
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│       FUTURE FLUTTER QS WORKSTATION (Web / Desktop / Android)   │
└───────────────────────────────┬─────────────────────────────────┘
                                │ HTTPS / WSS — Phase 4C+
┌───────────────────────────────▼─────────────────────────────────┐
│  CONTROLLED TUNNEL (planned) → 127.0.0.1:8000, bearer auth      │
└───────────────────────────────┬─────────────────────────────────┘
┌───────────────────────────────▼─────────────────────────────────┐
│  LOCAL FASTAPI GATEWAY — loopback, one worker, authenticated    │
└───────────────────────────────┬─────────────────────────────────┘
┌───────────────────────────────▼─────────────────────────────────┐
│  AGENT RUNTIME — state machine + model router (local ↔ BYOK)    │
└──────────┬──────────────────┬───────────────────┬───────────────┘
   TIER 1 QS TOOLS      TIER 2 DATA          TIER 3 CODE
   trench, cut/fill,    PDF, Mudshark,       sandboxed
   bulking, bedding     Excel, revisions     code-gen fallback
┌─────────────────────────────────────────────────────────────────┐
│  QS CHECKMATE — arithmetic · units · safety · grounding trace   │
└───────────────────────────────┬─────────────────────────────────┘
┌───────────────────────────────▼─────────────────────────────────┐
│  ARTIFACT & EVIDENCE STORE — SQLite + optional sqlite-vec       │
└─────────────────────────────────────────────────────────────────┘
```

The real tunnel hostname is owner-controlled and supplied out-of-band through
`FLUPPER_TUNNEL_HOSTNAME`. It is intentionally not stored in tracked source.

## Core engine pillars

| Pillar | Responsibility | Status |
|---|---|---|
| Evidence First | Traceability for every quantity | ✅ Phase 1 |
| 3-Tier Tooling | Deterministic math over probabilistic text | ✅ Phases 1–3 |
| Assumption Engine | Explicit lifecycle of QS assumptions | ✅ Phase 1 |
| QS CheckMate | Pre-response verification gate | ✅ Phases 1–3 |
| Human Approval | Safe execution boundaries | ✅ Phase 3 |
| Knowledge Graph | Relational tender model | ✅ Phase 2 |
| Local API Gateway | Bounded, authenticated lifecycle API | ✅ Phases 4A–4B |
| Controlled Tunnel | Explicit loopback-only public transport | 🟡 Phase 4C planning |
| Flutter Workstation | Project, agent, and evidence client | ⬜ planned |

## Phases

### Phases 1–2 — Storage, evidence, ingestion, and knowledge graph ✅
- SQLite evidence store, append-only audit journal, evidence contracts, Tier 1
  QS math, CheckMate, PDF/Mudshark ingestion, WBS mapping, and graph storage.

### Phase 3A–3D — Runtime orchestration ✅
- AgentSession lifecycle and transition gates.
- Tier 3 sandbox with platform-specific safety behavior.
- ModelRouter policy and provider boundaries.
- Human approval and delivery gates.

### Phase 4A — Local FastAPI gateway ✅
- Injectable app factory around the existing store, router, session, and sandbox.
- Bounded DTOs, project/session lifecycle routes, request limits, sanitized errors.
- Server-owned sandbox root and server-generated approval/action bindings.
- Exact `127.0.0.1` binding and one Uvicorn worker.

### Phase 4B — Authentication boundary ✅
- Mandatory `FLUPPER_API_TOKEN`.
- Bearer authentication on every `/api/v1/*` route, including health.
- Constant-time credential comparison and non-reflective 401 responses.
- No generated, printed, or tracked credentials.

### Phase 4C — Controlled Cloudflare Tunnel 🟡 planning
- Explicit opt-in connector only; never started by the API process.
- Strict synthetic/test manifest validation before any real tunnel is considered.
- Only the loopback API origin may be routed.
- Exact hostname, credential, catch-all, subprocess, cleanup, and redaction rules
  remain to be reviewed and implemented.
- No real tunnel, real client data, or production egress during verification.

### Phase 5 — Flutter workstation, revision, cost, and artifacts ⬜ planned
- 3-pane Project Explorer/Graph, Agent Canvas, and Evidence Inspector.
- Revision comparison traced to quantity and cost variance.
- Composite rate normalisation and confidence tracking.
- Formula-linked Excel BOQ export with audit trail reports.

### Phase 6 — Tender portal automation and risk engine ⬜ planned
- EstimateOne addenda and revision notices through a separately approved crawler.
- Risk register for drawing/spec conflicts, soil classification, compaction, and
  unquoted specialty trades.

## Engineering rules

1. An LLM never does arithmetic. It selects tools; Tier 1 Python computes.
2. Every number is replayable. Inputs are stored; CheckMate re-executes and compares.
3. Every number is cited. No evidence, no claim — enforced by the type system.
4. The journal is append-only. Tampering is detectable via the hash chain.
5. Safety is not advisory. Unsupported excavation over 1.5 m is a hard failure.
6. The API remains loopback-bound and single-worker while sessions and locks are
   in memory.
7. Real project data, prompts, credentials, tunnel manifests, and client names
   never enter tracked source.
