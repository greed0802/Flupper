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
│  CONTROLLED TUNNEL (opt-in) → 127.0.0.1:8000, bearer auth       │
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
| Controlled Tunnel | Explicit loopback-only public transport | ✅ Phase 4C |
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

### Phase 4C — Controlled Cloudflare Tunnel ✅ implementation complete — synthetic/local verification only
- Explicit opt-in connector only; never started by the API process. Nothing in
  `main`, `run_local`, or `qsagent.api` imports the launcher, so the serving path
  cannot reach it.
- Strict manifest validation: exactly one hostname rule followed by exactly one
  `http_status:404` catch-all. Extra, duplicate, missing, or reordered rules are
  rejected rather than sanitised.
- Only the loopback API origin may be routed. The origin is a constant
  (`http://127.0.0.1:8000`), never read from the manifest, the environment, or an
  argument, and a test pins it to the address `run_local` binds.
- The manifest hostname must equal `FLUPPER_TUNNEL_HOSTNAME`.
- The credential file is path-checked only (absolute, exists, regular file) and
  is never opened, parsed, or logged.
- `cloudflared` runs in its own process group with streams inherited, so a signal
  cannot reach the wrapper and no undrained pipe can deadlock. The temporary
  config is owner-only and unlinked on every exit path.
- No quick tunnel, and no automatic production startup integration.

**Scope of this status.** Implementation complete, verified synthetically and
locally only. No real tunnel connection has been established, no hostname has
been published, and no production tunnel has been deployed — production tunnel
deployment remains separately approved.

- The tracked hostname remains synthetic (`api.example.invalid`); the owner's real
  hostname is supplied out-of-band and is never committed.
- The API continues to require bearer authentication (`FLUPPER_API_TOKEN`) on
  every route, including health. The tunnel is transport and authorises nothing.
- No real client data, credentials, or production egress during verification.

### Phase 5A — Read-only Flutter workstation ✅
- Flutter Web/Desktop shell with in-memory authentication only.
- Authenticated health view and explicit single-project read view.
- Bounded Dart DTOs matching the existing API contracts.
- Python contract mirror tests, Flutter VM tests, Web build, and sequential
  per-file Chrome CI verification.
- No project creation, local data cache, or client-controlled filesystem access.

### Phase 5B — Server-side revision diff ✅ read-only

```
GET /api/v1/projects/{project_id}/revisions/diff/{base_document_id}/{target_document_id}
```

Compares stored evidence for two documents in one project under bearer auth,
the gateway session lock, and one explicit SQLite read snapshot.

Delivered:

- deterministic evidence identity and canonical comparison;
- `added`, `removed`, `changed`, `unchanged`, `ambiguous`, and `unresolved` results;
- bounded responses and exact evidence-reference claim association;
- shared-connection survival and read-only guarantees;
- no CheckMate, approval, journal, or delivery mutation.

Known data limitation: existing ingest paths do not consistently persist locator
lineage on evidence nodes. The diff reports unassociated claims rather than
matching by source hash alone. In-place re-ingest is reported as
`evidence_unavailable`, not fabricated removals.

### Phase 5C — Evidence-backed rate normalization 🟡 planning
- Define rate contracts only after reviewing stored evidence and provenance.
- Keep assumptions, currency, unit, source, age, quote count, and confidence
  explicit.
- Do not fabricate rates or add external pricing egress.

### Phase 5D — Artifact/export engine ⬜ planned
- Formula-linked Excel BOQ export with bounded, server-owned artifact handling.
- Preserve evidence references and audit trails.
- No arbitrary client filesystem paths or unbounded generated output.

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
