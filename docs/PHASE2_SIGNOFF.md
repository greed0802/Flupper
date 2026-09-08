# Phase 2 — verified complete

Independently verified against the pushed commit, not taken from the summary.

---

## Test suite

```
from backend/:   94 passed, 1 skipped in 0.59s
from repo root:  94 passed, 1 skipped in 0.49s
```

95 collected, identical from both working directories. The single skip is
`test_phase2_real.py` ("Real client data not available"), correct on a machine
without the dataset.

## Behaviour matrix — re-derived independently

Pattern list is now:

```
('bulked_to_insitu', 'cut_bulked_to_insitu', '_to_insitu',
 'volume_conversion', 'state_convert')
```

`cut_raw_unresolved` removed. All six cases behave as agreed:

```
OK  ingest.cut_raw_unresolved   UNRESOLVED  -> VERIFIED (with notes)
OK  ingest.cut_raw_unresolved   None        -> VERIFIED (with notes)
OK  ingest.cut_raw_unresolved   m3_insitu   -> VERIFIED (with notes)
OK  ingest.trench_length        UNRESOLVED  -> VERIFIED (with notes)
OK  convert.bulked_to_insitu    UNRESOLVED  -> REJECTED
OK  convert.bulked_to_insitu    m3_insitu   -> VERIFIED (with notes)

matrix correct: True
```

Safe raw storage warns and passes; a genuine conversion on an unresolved state
is rejected. That is exactly the agreed design.

## The E2E test is real, not hollow

`test_no_ingested_claim_is_rejected_by_checkmate` runs `_ingest_masterfile`
against the fixture and asserts `len(rows) > 0` before checking badges — so it
cannot pass on an empty ingest, which was the failure mode of the earlier
idempotence test.

Confirmed collected and passing:

```
collected 29 items / 28 deselected / 1 selected
1 passed, 28 deselected
```

It verifies 4 real claims totalling 2150.0 m3:

```
mudshark.ingest.cut_raw_unresolved  UNRESOLVED  count=4  sum=2150.0
```

This is the structural fix that matters most. Every blocker across the eight
review rounds — the 3.0x union double-count, `file_hash` in the ingest key, the
relative fixture path, the inverted pattern list — was invisible to a green
suite because the tests exercised constructed inputs while the production path
diverged. This test binds the gate to real ingest output, so the next change
that breaks that path fails immediately.

## Regression sweep

```
idempotence: run1 nodes/claims/sum=(5, 4, 2150.0)  run2=(5, 4, 2150.0)
  non-zero claims? True   delta zero? True   -> PASS
journal chain intact: True
```

Idempotence holds on a fixture that produces non-zero quantities, without a
database reset. The hash-chained audit journal still verifies.

---

## Phase 2 acceptance gate — final

```
[x] Source files preserved; SHA-256 integrity verified
[x] Duplicate detection (bbx by content hash)
[x] True idempotence WITHOUT DB reset, on non-zero quantities
[x] UPSERT non-destructive; other sources and manual QS work survive
[x] ingest_key excludes file_hash; occurrence retained as disambiguator
[x] Collision guard raises on conflicting duplicate keys
[x] Constraint violations propagate (FK / NOT NULL loud)
[x] Schema migration for pre-existing databases
[x] Union reconciliation fires on mismatch, silent on match
[x] 3.0x aggregate double-count fixed; all 7 columns at 0.0000%
[x] OL=1 uniqueness measured (64/64, 0 duplicates)
[x] Measurement-state inconsistency diagnosed; unsafe conversion removed
[x] CheckMate gates conversions without blocking safe raw storage
[x] E2E test binds the gate to real ingest output
[x] Image-only drawings detected (12%); no silent title-block skips
[x] Suite passes from both working directories
[x] Client names removed from tracked source; probe outputs gitignored

PHASE 2 = COMPLETE
```

## Carried into Phase 3

Not defects — scope that Phase 2 deliberately deferred.

1. **BBX settings parser is load-bearing.** BF = 1.0 is confirmed on Aldi
   Dandenong only, and Mudshark factors are per-project settings. A job with
   BF = 1.30 would store raw values marked `UNRESOLVED` — safe, but ~30% high
   until resolved. This parser is what converts `UNRESOLVED` into a usable
   state. Schedule it first.

2. **Replace substring matching with an explicit flag.** Inferring "was a
   conversion applied" from a method name is fragile and has already misfired
   once. A boolean `conversion_applied` on `QuantityClaim` makes the rule exact
   and removes the failure class.

3. **`test_phase2_real.py` contributes nothing on CI.** It skips wherever the
   dataset is absent, so real-data coverage exists only on the workstation.
   Consider a sanitised committed fixture derived from real structure.

4. **History still contains `probe_masterfile.json`** in commit `a741947`
   (client project names, no quantities). Repo is private with 0 forks —
   accepted. Revisit before the repository is ever made public.
