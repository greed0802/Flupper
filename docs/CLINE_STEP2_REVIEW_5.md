# Review 5 — 3.0x bug genuinely fixed; one new silent-data-loss risk

Two fixes are verified correct. One introduces a silent quantity-loss path, and
the evidence cited for it does not hold.

---

## Confirmed fixed

**The 3.0x union bug is genuinely resolved.** The numbers match the predicted
diagnosis precisely:

```
union BEFORE fix: 9713.666
union AFTER  fix: 3237.889
ratio before/after = 3.000000
components unchanged at 3237.889
```

The aggregate dropped by exactly 3.0x while components stayed put — which is
what a "same total counted once per component sheet" bug looks like when fixed.
Diff is now 0.000000000. This was the right diagnosis and the right fix.

**Fix 2 (`INSERT` + `IntegrityError` filtering) is correct.** Verified that
non-unique violations propagate:

```
duplicate key    -> 'UNIQUE constraint failed: c.k'        contains 'UNIQUE'? True
bad FK           -> 'FOREIGN KEY constraint failed'        contains 'UNIQUE'? False
NULL NOT NULL    -> 'NOT NULL constraint failed: c.pid'    contains 'UNIQUE'? False
```

Only the unique case is swallowed; FK and NOT NULL raise. String-matching on
driver messages is slightly brittle — `sqlite3.IntegrityError` subclasses would
be cleaner if available — but the behaviour is right and the test covers it.

**Fix 1 (`file_hash` removed from the key) is correct** and
`test_idempotence_scenario_b_file_hash_change` tests the right scenario.

---

## 1. BLOCKER — dropping `row_index` creates silent quantity loss

The key is now `project_id | report_type | sheet | operation_group [| col]`,
justified by "operation_group is unique per sheet on this project (confirmed
above: 4 rows)".

**The cited evidence does not exist.** The item-4 table from the same run
reports OL=0 row counts of 7, 3 and 3 — component total 13. Neither 7, 3, 3 nor
13 is 4. The uniqueness claim references a measurement that appears nowhere in
the output.

**More seriously, uniqueness was checked at the wrong level.** Per the agreed
decision table, **OL=1 rows are the ingested quantity rows**; OL=0 rows are
category totals that get skipped. Uniqueness was verified on OL=0 rows — the
ones that are *not* ingested.

If two OL=1 rows in a sheet share an operation-group label — two stormwater runs
both labelled `Stormwater Drainage`, which is entirely ordinary in a trench
schedule — they now collide:

```
ingested 2 distinct runs (120 + 80 = 200)
stored: count=1  sum=80.0
LOST 120 m3 silently -- no error, no warning
```

A quantity disappears with no exception and no warning. That is strictly worse
than the double-counting it replaced: over-measurement is visible in a total,
whereas this is invisible.

Required before this is safe:

- Verify uniqueness on **OL=1 rows** (the ingested level), across **all three
  component sheets** and **more than one project**.
- If any duplicate label exists, restore a disambiguator. A stable occurrence
  ordinal within the group (`1`, `2`, `3` in sheet order) is better than
  `row_index`: it survives unrelated row insertions elsewhere in the sheet while
  still separating siblings.
- Regardless of the outcome, add a **collision guard**: before upsert, if the
  key already exists in this ingest run and the value differs, raise rather than
  overwrite. Silent overwrite must be impossible by construction.

## 2. The reconciliation is weaker than it appears

The breakdown shows two of three sheets contributing nothing:

```
Ground Layer  : 3237.889
Structure     : 0.000   <- contributes nothing
Trench Run    : 0.000   <- contributes nothing
component sum : 3237.889
```

So the union check currently reduces to `Ground Layer == aggregate`. It is a
single-sheet comparison wearing the clothes of a three-way reconciliation, and
it would not detect a double-count in Structure or Trench Run.

The zeros are plausible — Structure and Trench Run legitimately carry no
`Cut (Bulked m³)`, per the earlier finding that trench sheets populate
cols 5/6/7 (Imported/Fill). But that should be asserted, not assumed. Extend the
reconciliation to the columns those sheets actually populate
(`Imported (Banked m³)`, `Fill (Compressed m³)`), so all three sheets are
genuinely covered.

## 3. Minor — the prose contradicts its own table

> "The extra 2 OL=0 rows in All Strata (11 vs 13 component rows)"

11 is **fewer** than 13, not extra. The explanation that follows — sheet-level
summary rows Mudshark writes at the top of the aggregate — would *increase* the
aggregate count, so it cannot explain a decrease. The likelier explanation is
that per-sheet header rows collapse on merge, consistent with the earlier row
counts (249 + 53 + 128 = 430 vs All = 426, also a decrease).

The totals reconcile exactly, so this does not affect correctness. But the
stated reasoning is wrong and should not be recorded as understood.

---

## Status

```
[x] 3.0x union bug fixed - verified by exact ratio collapse
[x] file_hash removed from key - Scenario B tested
[x] INSERT + IntegrityError filtering - FK/NOT NULL verified loud
[x] Union numbers finally shown as measurements
[ ] row_index dropped on unverified uniqueness -> SILENT QUANTITY LOSS
[ ] Uniqueness must be checked at OL=1, all sheets, >1 project
[ ] Reconciliation covers only 1 of 3 sheets in practice
[ ] "extra 2 rows" reasoning is wrong (11 < 13)

PHASE 2 = NOT COMPLETE (1 blocker)
```

Down to one blocker, and it is narrow: confirm uniqueness at the level that is
actually ingested, or add a disambiguator. Add the collision guard either way.

---

## 4. LATE FINDING — "85/85 passed" does not reproduce

The pushed commit does not give 85 passing tests in a clean checkout.

**From `backend/` (the documented working directory):**

```
82 passed, 1 skipped in 0.33s
SKIPPED [1] tests/test_phase2_real.py:9: Real client data not available
```

83 collected, not 85. And the single skip is `test_phase2_real.py` — the real
Aldi ingest, the one test that exercises actual project data. On any machine
without the dataset it silently skips, so it contributes nothing to the count on
CI or on a reviewer's checkout.

**From the repository root, three tests fail:**

```
FAILED backend/tests/test_migration.py::test_migration_adds_ingest_key_to_existing_db
FAILED backend/tests/test_phase2_fixes.py::test_idempotence
FAILED backend/tests/test_phase2_fixes.py::test_bulked_to_insitu - assert 0 > 0
```

Root cause is a relative path:

```python
masterfile_dir = Path("tests/fixtures/master")
...
ERROR qsagent.ingest.cli:cli.py:53 No Results.xls found in tests/fixtures/master
```

It resolves against the current working directory, so the suite passes from
`backend/` and fails from the repo root. Fixture paths must be anchored to the
test file:

```python
FIXTURES = Path(__file__).parent / "fixtures"
masterfile_dir = FIXTURES / "master"
```

Note the consequence for `test_bulked_to_insitu`: it fails with `assert 0 > 0`
because no assumptions were created — the ingest found no input file at all. The
test was not verifying the conversion; it was passing only because the fixture
happened to resolve. Once the path is fixed, confirm it actually asserts
`Q / BF` on real parsed data rather than on an empty ingest.

This also means the bulked→in-situ verification is still **unproven** — the test
that was supposed to demonstrate it never had data to work with in this
environment.
