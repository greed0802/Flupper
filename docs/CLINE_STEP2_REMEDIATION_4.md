# Paste into Cline — one blocker left

---

```
Two fixes verified correct, and the big one is genuinely resolved.

The 3.0x union bug is fixed. Your numbers match the predicted diagnosis exactly:

    union BEFORE fix: 9713.666
    union AFTER  fix: 3237.889
    ratio = 3.000000, components unchanged at 3237.889

The aggregate collapsed by exactly 3.0x while components held steady — precisely
what "same total counted once per component sheet" looks like when fixed. Good
diagnosis, good fix.

Fix 2 also verified. I confirmed only the unique case is swallowed:

    duplicate key -> 'UNIQUE constraint failed: c.k'      contains 'UNIQUE'? True
    bad FK        -> 'FOREIGN KEY constraint failed'      contains 'UNIQUE'? False
    NULL NOT NULL -> 'NOT NULL constraint failed: c.pid'  contains 'UNIQUE'? False

FK and NOT NULL propagate loudly. Correct. (String-matching driver messages is a
little brittle, but the behaviour and the test are right.)

Fix 1 is correct and Scenario B is the right test.

ONE BLOCKER REMAINS.

1. DROPPING row_index CAN SILENTLY LOSE QUANTITIES

You justified it with "operation_group is unique per sheet on this project
(confirmed above: 4 rows)". But your own item-4 table from the same run reports
OL=0 counts of 7, 3 and 3, totalling 13. There is no 4 anywhere. The cited
evidence does not exist.

More importantly, you checked uniqueness at the WRONG LEVEL. Per our agreed
decision table, OL=1 rows are the ingested quantity rows; OL=0 rows are category
totals that get skipped. You verified uniqueness on rows that are not ingested.

If two OL=1 rows in a sheet share an operation-group label — two stormwater runs
both labelled 'Stormwater Drainage', completely ordinary in a trench schedule —
they now collide. I demonstrated it:

    ingested 2 distinct runs (120 + 80 = 200)
    stored: count=1  sum=80.0
    LOST 120 m3 silently -- no error, no warning

This is worse than the double-counting it replaced. Over-measurement shows up in
a total; this vanishes without trace.

Do all three:
  a) Verify uniqueness on OL=1 rows, across all three component sheets, on more
     than one project. Report duplicate label counts per sheet.
  b) If any duplicates exist, restore a disambiguator — but use a stable
     occurrence ordinal within the group (1, 2, 3 in sheet order) rather than
     row_index. That survives unrelated row insertions while still separating
     siblings.
  c) Regardless of the outcome, add a COLLISION GUARD: before upsert, if the key
     already exists within this ingest run and the value differs, raise. Silent
     overwrite must be impossible by construction, not merely unlikely.

2. YOUR RECONCILIATION ONLY COVERS ONE SHEET

Your breakdown shows Structure = 0.000 and Trench Run = 0.000, so the check
reduces to "Ground Layer == aggregate". It would not detect a double-count in
the other two sheets.

The zeros are plausible — those sheets carry no Cut (Bulked m3); trench sheets
populate cols 5/6/7 (Imported/Fill), per your own Step 1 findings. But assert it
rather than assume it. Extend the reconciliation to the columns those sheets
actually populate: Imported (Banked m3) and Fill (Compressed m3). Then all three
sheets are genuinely covered.

3. MINOR — YOUR PROSE CONTRADICTS YOUR TABLE

You wrote "the extra 2 OL=0 rows in All Strata (11 vs 13 component rows)". 11 is
FEWER than 13, not extra. And sheet-level summary rows at the top of the
aggregate would increase its count, so that explanation cannot produce a
decrease. The likelier cause is per-sheet header rows collapsing on merge —
consistent with your earlier counts (249+53+128 = 430 vs All = 426, also a
decrease). Totals reconcile exactly so correctness is unaffected, but do not
record the wrong reasoning as understood.
```

---

## Additional — added after checking the pushed commit

```
4. "85/85 PASSED" DOES NOT REPRODUCE ON THE PUSHED COMMIT

From backend/ (the documented working directory):

    82 passed, 1 skipped in 0.33s
    SKIPPED [1] tests/test_phase2_real.py:9: Real client data not available

That is 83 collected, not 85. And the skip is test_phase2_real.py — the real
Aldi ingest, the single test that touches actual project data. It silently skips
anywhere the dataset is absent, so it adds nothing on CI or a reviewer's
machine.

From the repository root, three tests FAIL:

    FAILED test_migration.py::test_migration_adds_ingest_key_to_existing_db
    FAILED test_phase2_fixes.py::test_idempotence
    FAILED test_phase2_fixes.py::test_bulked_to_insitu - assert 0 > 0

Cause is a relative fixture path:

    masterfile_dir = Path("tests/fixtures/master")
    ERROR qsagent.ingest.cli: No Results.xls found in tests/fixtures/master

It resolves against the current working directory. Anchor it to the test file:

    FIXTURES = Path(__file__).parent / "fixtures"
    masterfile_dir = FIXTURES / "master"

Important consequence: test_bulked_to_insitu fails with "assert 0 > 0" because
NO assumptions were created — the ingest found no input file. That test was
never verifying the Q / BF conversion; it only passed when the path happened to
resolve. So bulked->in-situ is still UNPROVEN. After fixing the path, confirm it
asserts the conversion against real parsed data, not an empty ingest.

Always run pytest from the repository root as well as from backend/ before
reporting a count, and quote the actual pytest summary line rather than a
number.
```
