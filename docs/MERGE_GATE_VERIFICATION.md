# Merge gate — independently measured

ChatGPT's review is correct in substance: do not accept a narrative "complete"
declaration. But most of its checklist does not need to go back to Cline — it is
measurable here, from the pushed commit, and I have run it.

One of its gates is also unachievable as written, and one item it did not check
turns out to be a real (minor) finding.

---

## Items measured directly

### 1. Test suite — both working directories

```
ROOT:    collected 95 / passed 94 / failed 0 / skipped 1
BACKEND: collected 95 / passed 94 / failed 0 / skipped 1
```

ChatGPT is right to insist on this phrasing. "95/95 passed" is wrong;
94 passed with 1 skipped is the accurate statement.

### 2. Real-data test

```
REAL_PROJECT_TEST = SKIPPED
SKIP_REASON = "Real client data not available" (tests/test_phase2_real.py:11)
```

Correct behaviour in this sandbox — the dataset lives only on the Windows
workstation. It contributes nothing to CI coverage.

### 5. True re-export idempotence (different file hash)

This is the test that matters most, because it is the scenario the earlier
`file_hash`-in-key defect broke. Byte-modified copy, same logical rows, new
SHA-256:

```
run1_count=4  run1_sum=2150.0
run2_count=4  run2_sum=2150.0
delta_count=0 delta_sum=0.0        -> PASS
```

A corrected re-export converges instead of duplicating. The original defect is
genuinely gone.

### 6. Cross-source preservation

```
before_count=6  before_sum=2562.0
after_count=6   after_sum=2562.0
manual_survived=TRUE
drawing_survived=TRUE               -> PASS
```

Re-ingesting only Mudshark left the manual QS allowance (340.0) and the
drawing-derived quantity (72.0) intact. The destructive-DELETE regression is
gone.

### 7. CheckMate on a real ingested claim

Taken from the database after an actual ingest, not constructed:

```
method=mudshark.ingest.cut_raw_unresolved  state=UNRESOLVED
badge=VERIFIED (with notes)  passed=True
measurement findings: ['WARN']      -> PASS (WARN, not FAIL)
```

And a genuine conversion with an unresolved state is still rejected
(`convert.bulked_to_insitu` + `UNRESOLVED` -> REJECTED), confirmed in the
previous round's matrix.

### 8. Migration from a genuine legacy database

My first attempt at this was wrong — the substitution left `ingest_key` in
place, so the "legacy" DB was not legacy. Redone by stripping both the column
definitions and the unique indexes:

```
legacy_db_before_migration : ingest_key present = False
ingest_key_column_added    = True
unique_index_added         = True  ['ux_nodes_ingest_key']
upsert_success             = True (count=4 sum=2150.0 after TWO ingests)
```

Two consecutive ingests against the migrated legacy DB produce 4 claims, not 8.
The migration works and upsert converges on it.

### 9. Git / security

```
probe_masterfile_tracked        = FALSE
generated_probe_outputs_tracked = FALSE
working_tree_clean              = TRUE
historical_probe_artifact       = TRUE (a741947)
client_names_in_current_tree    = TRUE   <-- see finding below
```

---

## New finding — client names re-entered tracked source

ChatGPT did not check this, and it has regressed since the earlier
sanitisation. `Aldi Dandenong` now appears in tracked **code**, not just docs:

```
backend/qsagent/checkmate/engine.py:296  "...holds for the Aldi
backend/qsagent/checkmate/engine.py:297   Dandenong project: Reused = From Site = 336.266 m3 exactly."
backend/qsagent/ingest/cli.py:29         "Aldi project has 4 OL=1 groups across 3 sheets..."
backend/tests/test_checkmate.py:141      "...BF = SF = 1.0 on the Aldi Dandenong project"
```

Cause is traceable and partly mine: I asked for the QS diagnostic reasoning to
be recorded in code comments, and my own review text named the project. Cline
transcribed it faithfully.

Severity is low — comments, no quantities beyond the 336.266 identity already
discussed, private repo. But it undoes a deliberate earlier fix, and the same
`git grep` that caught it the first time catches it now. It should be
sanitised to "the reference project" before merge, and a grep guard added so it
cannot silently return.

---

## Where I disagree with ChatGPT

### Item 3 (end-to-end real ingest) cannot gate this merge

It asks for `drawings_discovered`, `mudshark_records`, `quantity_claims`,
`quantity_sum` from the real dataset. That data exists only on the user's
Windows workstation and — correctly — must never enter this repository or CI.

So the gate as written can only ever be satisfied by output that cannot be
independently reproduced by a reviewer. That is the same category of evidence
the whole review chain has been rejecting: unverifiable assertion. Requiring it
before merge sets a bar the architecture deliberately makes unmeetable.

The right resolution is the one already in the sign-off: treat real-data
validation as a **workstation acceptance step**, recorded as such, and keep CI
bound to the synthetic fixture plus the E2E test that proves the gate accepts
real ingest output. Merging is not the moment that risk crystallises — the
BBX/BF work in Phase 3 is.

### Item 4 (seven-column reconciliation) is already satisfied

The measured table was supplied last round (all seven columns, 0.0000%), and I
independently confirmed the 3.0x defect is gone by ratio analysis. Re-running it
against the synthetic fixture would prove nothing new, since the fixture has one
sheet.

---

## Verdict

```
PHASE 2 IMPLEMENTATION = COMPLETE (measured)
PHASE 2 CI EVIDENCE    = COMPLETE (measured, synthetic fixture)
REAL-DATA VALIDATION   = SKIPPED  (workstation-only, by design)

MERGE = READY, after sanitising client names from tracked source
```

ChatGPT's conclusion "essentially ready, evidence not yet clean" is right. But
the gap is one grep-fixable comment leak, not a further verification round —
seven of its ten items are measured PASS above, one is SKIPPED by design, one is
unachievable as written, and one is the finding it missed.
