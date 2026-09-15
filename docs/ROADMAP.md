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

### Phase 5C — Evidence-backed rate normalization ✅ read-only

```
POST /api/v1/projects/{project_id}/rates/proposals/normalize
```

Turns stored `rate` evidence rows into normalized rate proposals under bearer
auth, the gateway session lock, and one explicit SQLite read snapshot. The
request carries node ids and nothing else: there is no field for an amount, a
unit, a currency, a category, a hash or a path, so a caller cannot assert what a
rate is or nominate the proof for one.

Delivered:

- a closed payload schema — `amount`, `unit`, `currency`, `rate_category`,
  `effective_date`, `provenance`, the `doc_id`/`file_hash` source link and the
  locator keys; an unexpected key is a refusal, not a field to look around;
- closed unit, currency and category sets, plus explicit category-to-unit
  compatibility: `crates`, `USD`, `$` and `R/hr` for a cart-away are all
  `unresolved` rather than mapped onto something plausible;
- money as `Decimal` from parse to serialization, quantized once to two places
  with `ROUND_HALF_UP` and rendered as a decimal string, so no `float` and no
  JSON number can change a rate in transit;
- source resolution against `documents` inside the project — the hash and file
  name in a proposal were read out of the store, never echoed from a payload;
- `exact` requires a machine export and a locator; anything else that still has
  a verifiable source is `inferred` with the missing piece named in `warnings`;
- duplicate quotes of one source and category are flagged rather than picked
  between, and a request that names an id outside the project fails whole rather
  than answering in part;
- bounded request (positive strict integer ids, at most 100, deduplicated) and
  bounded response; no writes, no journal entry, no CheckMate row, no approval
  consumption, no quantity-claim change.

No escalation, inflation or FX is applied. A rate older than a year is reported
as `stale_source` with the unchanged number; a currency that is not `AUD` is
unresolved. Neither has a verified source in this store, so neither is guessed.

Known data limitation: no ingest path writes `node_type='rate'` today, so this
surface currently answers from rows the platform does not yet produce. A row
with a verified source but no locator is `inferred`; a row whose document id no
longer resolves is `evidence_unavailable`. Persisting a normalized rate, and
binding one to a quantity claim, is deliberately deferred — it needs its own
approval, audit and rollback path.

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
