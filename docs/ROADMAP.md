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

### Phase 5 — Flutter workstation, revision, cost, and artifacts ⬜ planned
- 3-pane Project Explorer/Graph, Agent Canvas, and Evidence Inspector.
- Revision comparison traced to quantity and cost variance.
- Composite rate normalisation and confidence tracking.
- Formula-linked Excel BOQ export with audit trail reports.

### Phase 5B — server-side revision diff ✅ read-only

```
GET /api/v1/projects/{project_id}/revisions/diff/{base_document_id}/{target_document_id}
```

Compares the stored evidence of two documents inside one project, under the
bearer token and the gateway's session lock, inside one explicit read snapshot.

Delivered:

- a canonical identity for an evidence row (`qsagent.revisions.canonical`) and
  a deterministic comparison (`qsagent.revisions.diff`);
- six outcomes: `added`, `removed`, `changed`, `unchanged`, `ambiguous`,
  `unresolved`;
- affected-claim association by exact evidence-reference match, plus a count of
  the claims that could **not** be associated;
- every list, string and count bounded, with truncation reported rather than
  inferred;
- the shared connection survives a diff, and a diff writes nothing: no journal
  entry, no claim mutation, no CheckMate row, no approval consumed.

Verified inventory — what the store does not carry yet:

- no ingest path passes a `ref` to `add_node`, so every provenance column on
  `evidence_nodes` is null in production data. The diff therefore reads
  `file_hash`, `doc_id`, `sheet` and the drawing identity out of `payload`, and
  partitions a document's rows by `payload.doc_id` / `payload.file_hash`;
- consequently no evidence row carries a locator, so affected claims are
  reported as unassociated rather than matched on the source hash alone.
  Closing that needs ingestion to record the locator on the node, or an
  explicit claim↔node edge — Phase 5C/5D work;
- rows written with an `ingest_key` are updated in place on re-ingest, so a
  Mudshark re-export leaves the earlier revision with no rows of its own. That
  is reported as `evidence_unavailable`, never as a full set of removals.

Deferred: revision → cost variance, and any comparison across projects.

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
