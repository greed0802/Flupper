# Phase 3 kickoff — handover for a new coding session

Paste the "Kickoff prompt" section below into a new Arena coding session on
`greed0802/Flupper`. Everything an agent needs to resume is in this file.

---

## Where things stand

- **PR #1 merged.** `main` is at `8b08214` (Phase 1 + Phase 2).
- **Repository/CI gate: PASS** — 96 collected, 95 passed, 0 failed, 1 skipped.
  Identical from the repository root and from `backend/`.
- **Workstation gate: PASS** — the real-project test ran against live Mudshark
  data in 2.03 s and its reconciliation assertions held.
- **Phase 2 is frozen.** Do not reopen it for theoretical improvements.

### One local commit was never pushed

This session closed when PR #1 merged, so remote access was revoked before the
last doc could be pushed:

```
028bc80  Record workstation acceptance gate result: real ingest passed
         docs/WORKSTATION_GATE_RESULT.md
```

The file exists in the sandbox working tree but **not on `main`**. It is a
record, not code — nothing depends on it. Either re-create it in the new session
or let it go. Everything else is merged.

---

## Bug classes closed in Phase 2 — do not regress these

Each was found by measurement after being reported as fixed. The regression
tests now guarding them are the most valuable artifacts in the repo.

| Bug | Guard |
|---|---|
| Aggregate double-count (union summed every sub-hierarchy row → exactly 3.0×) | 7-column cross-check |
| Destructive re-ingest (project-wide `DELETE` wiped manual QS + other sources) | cross-source preservation test |
| Re-export duplication (`file_hash` in `ingest_key` minted new keys per export) | different-SHA idempotence test |
| Unsafe state conversion (dividing by BF when source emits one nominal state → 23% under-measure) | CheckMate measurement-state gate |
| Client names in tracked source | `backend/tests/test_no_client_names.py` |

---

## Phase 3, task 1 — BBX settings/state extraction

This is load-bearing, not polish.

**Why it blocks everything else.** `measurement_state="UNRESOLVED"` is a
deliberate safety stop. Ingest currently stores the raw Mudshark value and
refuses to convert it. CheckMate emits a WARN for that state and a FAIL if
anything tries to run a conversion on it. So no earthworks quantity can be
priced with confidence until the real per-project factors are known.

**What is actually known.** BF = 1.0 and SF = 1.0 are confirmed on **one**
project. The proof is that the same physical material appears in a Bulked column
and a Compressed column at an identical value:

```
Reused    (Bulked m3)     = 336.266    <- cut on site, reused
From Site (Compressed m3) = 336.266    <- fill sourced from site

BF=1.30 SF=0.88 -> 227.626
BF=1.25 SF=0.90 -> 242.112
BF=1.20 SF=0.90 -> 252.200
only BF=SF=1.0 yields 336.266
```

Mudshark factors are **per-project settings**. A job configured with BF = 1.30
emits genuinely state-differentiated figures. The parser would store raw values
marked `UNRESOLVED` — safe, but roughly 30% high until resolved.

**Deliverable.** Read the bulking and shrinkage factors from the `.bbx` project
file (or wherever Mudshark persists them), populate `measurement_state` with a
real value, and only then allow conversion. Two tests are mandatory:

1. A project with BF = 1.0 resolves to a like-for-like state, and its quantities
   pass CheckMate without the UNRESOLVED warning.
2. A project with BF != 1.0 converts correctly and records an Assumption row
   carrying the factor, its direction, and `ASSUMED` status.

## Phase 3, task 2 — replace substring matching with an explicit flag

`CheckMate._CONVERSION_METHOD_PATTERNS` infers "was a conversion applied" by
substring-matching the `method` string. It has already misfired once:
`cut_raw_unresolved` was listed as a conversion, which caused **every** bulk cut
claim to be REJECTED — the exact inverse of the intent.

A claim knows whether it was converted. Add `conversion_applied: bool` to
`QuantityClaim`, gate on that, and delete the pattern list. This removes the
failure class rather than patching an instance of it.

## Phase 3, task 3 — then the roadmap items

Agent runtime state machine (RECEIVED → PLANNED → EXECUTING → VALIDATING →
REASONING → DELIVERED), BYOK model router, Tier 3 sandbox, SAFE/REVIEW/CONFIRM
approval gates. See `docs/ROADMAP.md`.

---

## Standing constraints — carry these into every session

- **Real Mudshark and drawing data must never be committed.** Data stays local;
  only structure crosses into the repo. This was violated once (see below).
- **Ignore probe *outputs*, not just data inputs.** Naming an output file in a
  prompt without a matching ignore rule is how client names first reached the
  remote.
- `Trench_Summary.xlsx` is **hand-made by the user** from `Results.xls`,
  including its `+/-` collapsible grouping. `Results.xls` is the
  Mudshark-generated file.
- Keep Cline in **Plan mode** until a plan is explicitly reviewed and approved.
- **A hand-written report is not verification.** Require command output. A
  `PASS` typed into JSON is worth nothing.
- **Beware the hollow test.** `0 == 0` passes trivially. Assert non-zero inputs
  before asserting a zero delta.
- **Test the real path, not a constructed one.** Every blocker in Phase 2 was
  invisible to a green suite because the tests exercised synthetic inputs while
  production diverged.
- **Run pytest from both the repository root and `backend/`.** A relative
  fixture path hid three failures for two rounds.
- **Report `N collected, M passed, K skipped`** — never collapse a skip into a
  pass.

## Security gate — before any public release

`probe_masterfile.json` remains in private Git history at **`a741947`**. It
contains client project names and `D:` paths; **no quantities or rates**. The
repository is private with 0 forks, so exposure is contained and history was
deliberately not rewritten.

**This is a required pre-publication gate.** Before the repo is ever made
public:

```bash
git filter-repo --path probe_masterfile.json --invert-paths
```

That rewrites every commit SHA, so coordinate it with anyone holding a clone.

---

## Environment notes

- Recreate the venv each session; `.venv/` is gitignored:
  ```bash
  python3 -m venv .venv && .venv/bin/pip install pydantic pytest openpyxl xlrd
  ```
- System-wide `pip3 install` fails (PEP 668). Always use `.venv/bin/pip`.
- `sqlite_vec` is not installed; `QSStore` degrades to `vec_enabled=False`.
  Tests must not assume vector search.
- `xlrd` >= 2.0 reads **only** legacy `.xls`. Never point it at
  `Trench_Summary.xlsx`.
- **xlrd cannot detect formulas** — a `SUM` cell reads as `TEXT ''` or its
  cached number. Identify totals structurally.
- Do **not** use `xlwt` for new fixtures.
- On Windows, run pytest from `backend/` — `pytest.ini` lives there.

---

## Kickoff prompt

```
Repo: greed0802/Flupper, main at 8b08214. Phase 1 and Phase 2 are merged and
frozen. Read docs/PHASE3_KICKOFF.md first — it has the full handover, the
standing constraints, and the bug classes I do not want regressed.

Start Phase 3 task 1: BBX settings/state extraction.

Context: ingest currently stores raw Mudshark values with
measurement_state="UNRESOLVED" because per-project bulking and shrinkage factors
are unknown. CheckMate WARNs on that state and FAILs any conversion attempt, so
no earthworks quantity can be priced with confidence. BF=1.0 is confirmed on one
project only, by the Reused(Bulked) == From Site(Compressed) == 336.266
identity. Factors are per-project settings, so this does not generalise.

Deliver: read BF and SF from the .bbx project file, populate measurement_state
with a real value, and allow conversion only once it is resolved. Two tests are
mandatory — a BF=1.0 project passing CheckMate without the UNRESOLVED warning,
and a BF!=1.0 project converting correctly while recording an Assumption row
with the factor, direction and ASSUMED status.

Ground rules: plan first and stop for my approval before writing code. Never
commit real Mudshark or drawing data. Run pytest from both the repo root and
backend/, and report "N collected, M passed, K skipped" verbatim — do not
collapse a skip into a pass. Every PASS must come from command output.
```