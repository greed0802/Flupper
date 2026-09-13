# Flupper — compressed context (paste into Cline / any agent)
## Project
Autonomous Civil QS Agent Platform. Evidence-first, auditable Quantity Surveying.
Every quantity traceable to drawing no, revision, sheet, zone, raw dimension,
file hash. Deterministic Python math over LLM text. Australian standards
(SafeWork Australia, AS 2124).
Graph: Document → Drawing → Element → Quantity → Rate → BOQ Line.
Assumptions have a lifecycle (A-01, ASSUMED/CONFIRMED, impact delta).
CheckMate is a pre-response gate emitting VERIFIED / REVIEW / REJECTED.
## Phases
1. SQLite+sqlite-vec store, evidence schemas, Tier 1 QS math — **DONE**
2. PDF/Mudshark ingestion + knowledge graph — **DONE, merged, frozen**
3. Agent runtime state machine (RECEIVED→PLANNED→EXECUTING→VALIDATING→
   REASONING→DELIVERED), BYOK router, Tier 3 sandbox, SAFE/REVIEW/CONFIRM gates
   — **CURRENT**
4. Cloudflare Tunnel (owner-controlled hostname configured out-of-band via
   FLUPPER_TUNNEL_HOSTNAME → localhost:8000, bearer) + Flutter 3-pane workstation

The deployment hostname is intentionally represented by `api.example.invalid`
in tracked documentation. The owner’s real hostname must be supplied through
local deployment configuration and never committed.
5. Revision intelligence, rate normalisation, Excel BOQ engine
6. EstimateOne Playwright crawler, tender risk register
## State
`main` @ `b891b9b`. Repo private, 0 forks.
Tests: **96 collected, 95 passed, 0 failed, 1 skipped** — same from repo root
and `backend/`. The skip is `test_phase2_real.py` (real data is workstation-only,
reads `FLUPPER_REAL_PROJECT`). It PASSES on the workstation against live data.
## Layout
```
backend/qsagent/{contracts,storage,tools,checkmate,ingest}/
backend/tests/            pytest.ini lives HERE (run pytest from backend/)
tools/probe_export.py     structure-only probe
docs/                     ROADMAP.md, PHASE3_KICKOFF.md, review chain
masterfile/               REAL DATA — gitignored, never commit
```
## Phase 3 task 1 (start here)
BBX settings/state extraction. Load-bearing, not polish.
Ingest stores raw Mudshark values as `measurement_state="UNRESOLVED"` because
per-project bulking/shrinkage factors are unknown. CheckMate WARNs on that state
and FAILs any conversion. So nothing can be priced with confidence yet.
BF=1.0 confirmed on ONE project only, proven by:
```
Reused(Bulked) = From Site(Compressed) = 336.266   same material, both sides
BF=1.30 SF=0.88 → 227.626 | BF=1.25 → 242.112 | only BF=SF=1.0 gives 336.266
```
Factors are per-project settings, so this does NOT generalise.
Deliver: read BF/SF from `.bbx`, populate `measurement_state`, allow conversion
only once resolved. Two mandatory tests — BF=1.0 project passes CheckMate with
no UNRESOLVED warning; BF≠1.0 project converts correctly AND records an
Assumption row with factor, direction, ASSUMED status.
**Do task 2 first if convenient:** replace
`CheckMate._CONVERSION_METHOD_PATTERNS` substring matching with an explicit
`conversion_applied: bool` on `QuantityClaim`. It already misfired once —
`cut_raw_unresolved` was listed as a conversion, which REJECTED every bulk cut
claim. BBX adds more method names to that list.
## Bug classes closed — do not regress
| Bug | Guard |
|---|---|
| Union summed every sub-hierarchy row → exactly 3.0× | 7-column cross-check |
| Project-wide `DELETE` wiped manual QS + other sources | cross-source preservation test |
| `file_hash` in `ingest_key` → new keys every re-export | different-SHA idempotence test |
| Dividing by BF on single-state source → 23% under-measure | measurement-state gate |
| Client names in tracked source | `test_no_client_names.py` |
`ingest_key = project | report_type | sheet | operation_group [| column]`
(+ `occurrence` if labels ever duplicate). Upsert via `ON CONFLICT DO UPDATE`.
Never `DELETE ... WHERE project_id`.
## Rules
- **Never commit real Mudshark/drawing data.** Structure only. Ignore probe
  *outputs* too — an unignored output filename is how client names first leaked.
- **No client names in tracked source** (guard test enforces).
- **A typed PASS is not verification.** Every PASS from command output.
- **Beware hollow tests.** `0 == 0` passes trivially — assert non-zero inputs
  before asserting a zero delta.
- **Test the real path.** Every Phase 2 blocker was invisible to a green suite
  because tests used synthetic inputs while production diverged.
- **Run pytest from repo root AND `backend/`.** A relative fixture path hid
  three failures for two rounds.
- **Report `N collected, M passed, K skipped`.** Never call a skip a pass.
- Cline stays in **Plan mode** until a plan is approved.
- Terse responses.
## Data facts
- `Results.xls` = Mudshark-generated. `Trench_Summary.xlsx` = **hand-made by the
  user**, including its `+/-` grouping.
- OL=1 rows are the ingested quantity rows; OL=0 are category totals (skipped);
  OL=2 are material detail.
- `All … Operations` sheets are UNIONS of the component sheets — cross-check
  only, never ingest, or you double-count.
- Recurring double-count variants: Trench_Summary vs Results; OL-2 vs OL-1;
  All-sheets vs components; Trenches vs Trench Depth Categories.
## Environment
```bash
python3 -m venv .venv && .venv/bin/pip install pydantic pytest openpyxl xlrd
```
- System `pip3` fails (PEP 668) — always use `.venv/bin/pip`.
- `sqlite_vec` absent → `QSStore` degrades to `vec_enabled=False`; tests must not
  assume vector search.
- `xlrd` ≥2.0 reads legacy `.xls` ONLY. Never point it at `.xlsx`.
- **xlrd cannot detect formulas** — a `SUM` reads as `TEXT ''` or a cached
  number. Identify totals structurally.
- `rowinfo_map` is empty without `formatting_info=True`.
- Do **not** use `xlwt`.
- Windows: run pytest from `backend/`.
## Security gate
`probe_masterfile.json` remains in private history at **`a741947`** — client
project names + `D:` paths, no quantities. Accepted while private.
**Required before any public release:**
```bash
git filter-repo --path probe_masterfile.json --invert-paths
```
Rewrites all SHAs — coordinate with clone holders.