# Flupper — Autonomous Civil QS Agent Platform

An auditable, evidence-first Quantity Surveying agent platform for Australian civil tendering.

## Execution cycle

```
RECEIVED ─► PLANNED ─► EXECUTING ─► VALIDATING ─► REASONING ─► DELIVERED
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│         FLUTTER QS WORKSTATION (Web / Desktop / Android)        │
│  Project Tree/Graph │ Agent Canvas & Artifacts │ Evidence & PDF │
└───────────────────────────────┬─────────────────────────────────┘
                                │ HTTPS / WSS
┌───────────────────────────────▼─────────────────────────────────┐
│   API GATEWAY & TUNNEL (FastAPI) — api.dhanrickeviota.com       │
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
│  ARTIFACT & EVIDENCE STORE — SQLite + sqlite-vec                │
└─────────────────────────────────────────────────────────────────┘
```

## Core engine pillars

| Pillar | Responsibility | Standard | Status |
|---|---|---|---|
| Evidence First | Traceability for every quantity | Drawing No, Revision, Sheet, Zone, Raw Dimension, Hash | ✅ Phase 1 |
| 3-Tier Tooling | Deterministic math over probabilistic text | T1 fixed Python; T2 parsers; T3 sandbox | ✅ Tier 1 |
| Assumption Engine | Explicit lifecycle of QS assumptions | ID (A-01), Status, Impact delta | ✅ Phase 1 |
| QS CheckMate | Pre-response verification gate | Arithmetic, SafeWork >1.5 m batter, units | ✅ Phase 1 |
| Human Approval | Safe execution boundaries | SAFE / REVIEW / CONFIRM | 🟡 recorded, gate in Phase 3 |
| Knowledge Graph | Relational tender model | Document → Drawing → Element → Quantity → Rate → BOQ Line | ✅ storage; ingestion Phase 2 |

## Phases

### Phase 1 — Storage, evidence schemas, Tier 1 QS math ✅ **complete**
- SQLite single store: `projects`, `documents`, `evidence_nodes`, `evidence_edges`,
  `assumptions`, `quantity_claims`, `tool_runs`, `audit_journal`, `checkmate_results`.
- Append-only, SHA-256 hash-chained audit journal (enforced by SQLite triggers);
  full tool payloads stored for deterministic replay.
- Tier 1 tools: `trench.volume`, `earthwork.bulking_shrinkage`,
  `civil.batter_volume`, `structural.pad_footing`.
- Pydantic evidence contracts: a `QuantityClaim` cannot be constructed without at
  least one `EvidenceRef` carrying a valid SHA-256, a sheet/page and a locator.
- CheckMate: independent replay of arithmetic, unit dimensional algebra,
  SafeWork Australia 1.5 m rule, grounding trace, hallucinated-number detection.
- 72 tests passing.

### Phase 2 — Ingestion pipeline & knowledge graph
`Upload → SHA-256 → Title Block OCR → Discipline Classifier → Entity Linker → Graph Index`
- PyMuPDF title-block extraction; Mudshark CSV layer parsing (Cut/Fill, Topsoil
  Strip, Rock Excavation, Backfill) mapped to Australian WBS.
- Graph linker: `C-204 Rev 3 → SW-02 → A-03 (100 mm Type A bedding) → BOQ 3.12`.
- sqlite-vec embeddings for semantic document retrieval (`doc_chunks` table already
  provisioned when the extension is present).

### Phase 3 — Agent runtime, CheckMate gates, approvals
- State machine orchestrator; multi-model router (local Qwen/Ollama for metadata
  and summaries, cloud BYOK for document synthesis).
- Tier 3 subprocess sandbox for malformed spreadsheets when pandas/openpyxl fail.
- Human approval gate with diff previews before writing to master BOQ files.

### Phase 4 — Cloudflare Tunnel & Flutter workstation
- `cloudflared` routing `api.dhanrickeviota.com → localhost:8000`, bearer auth, CORS.
- 3-pane Flutter client: Project Explorer/Graph · Agent Canvas · Evidence Inspector.

### Phase 5 — Revision, cost intelligence, artifact engine
- Rev A vs Rev B comparison traced to downstream quantity and cost variance.
- Composite rate normalisation (plant/hr, labour/hr, material/unit, cart away/m³)
  with confidence from quote count and tender age.
- Formula-linked Excel BOQ export (AS 2124 / MasterFormat) + audit trail reports.

### Phase 6 — Tender portal automation & risk engine
- EstimateOne Playwright crawler for addenda and revision notices.
- Risk register: drawing/spec conflicts, missing soil classification, non-standard
  compaction, unquoted specialty trades.

## Engineering rules

1. **An LLM never does arithmetic.** It selects tools; Tier 1 Python computes.
2. **Every number is replayable.** Inputs are stored; CheckMate re-executes and compares.
3. **Every number is cited.** No evidence, no claim — enforced by the type system.
4. **The journal is append-only.** Tampering is detectable via the hash chain.
5. **Safety is not advisory.** >1.5 m unsupported excavation is a hard failure.
