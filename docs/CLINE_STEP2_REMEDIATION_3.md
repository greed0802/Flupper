# Paste into Cline — two new blockers introduced by the last fix

---

```
The migration approach is right (ALTER TABLE + separate CREATE UNIQUE INDEX is
exactly how SQLite requires it), and adding operation_group to the key correctly
fixes the multi-column collision. But the last round introduced two new defects
and one claim that is not true.

1. BLOCKER — file_hash IN THE ingest_key REINTRODUCES DOUBLE-COUNTING

Your key is _ikey(file_hash, sheet, operation_group, row_index). Because
file_hash is part of it, every Mudshark RE-EXPORT mints brand-new keys:

    SCENARIO A: re-ingest the IDENTICAL file (what your tests do)
      run1=fecc223474b1  run2=fecc223474b1  converges? True   <- UPSERT works

    SCENARIO B: Mudshark re-exports a corrected model. file_hash changes.
      old key=fecc223474b1
      new key=d595bdbdfe30
      converges? False

UPSERT never matches. Old rows are orphaned, new rows inserted alongside, and
the project ends up holding BOTH versions of every quantity. That is the exact
double-counting bug this whole chain has been chasing.

Your tests miss it because they re-ingest a byte-identical fixture — Scenario A
only. But re-exporting after correcting the model is the most common reason to
re-ingest at all. It is the realistic case.

Fix: the key must identify the LOGICAL ENTITY, not the file instance:
    project + report_type + sheet + operation_group [+ measurement column]
Keep file_hash on the EvidenceRef as provenance, which is where it belongs. A
corrected re-export then updates the quantity in place, and the evidence
reference records which file version produced it.

Add a Scenario B test: ingest, change the file hash, re-ingest, assert the claim
count is UNCHANGED and the value updated.

2. BLOCKER — INSERT OR IGNORE HIDES MORE THAN THE UNIQUE CONFLICT

You replaced ON CONFLICT(ingest_key) DO UPDATE with INSERT OR IGNORE + UPDATE,
described as "returns exactly identically correctly". It converges on the
same-key case, but it is not equivalent:

    plain INSERT with NULL project_id     -> IntegrityError
    INSERT OR IGNORE with NULL project_id -> silently discarded, no error
    rows in nodes: 0

OR IGNORE suppresses NOT NULL, FOREIGN KEY and CHECK violations too. A malformed
row vanishes with no error and no row. This platform's premise is that a missing
quantity must fail loudly rather than disappear — this is the wrong primitive.

Your stated reason was syntax mismatches on legacy loads. ON CONFLICT(col) DO
UPDATE requires a unique index on col, and your migration now creates exactly
that (ux_nodes_ingest_key). Once the index exists, ON CONFLICT works on a
migrated legacy DB. Restore it.

If you keep INSERT OR IGNORE, follow it with a check that a row now exists for
the key and raise if not, so suppressed violations surface.

3. "OVERCOMING RANDOM LINE-INSERT OFFSET CHANGES" IS NOT TRUE

row_index is still in the key:

      row 12 -> key 793af30873c2
      row 13 -> key 6084cac9baec
      same logical entity, same key? False

A row inserted above still changes every downstream key. If operation_group is
unique within a sheet, drop row_index and the claim becomes true. If it is not
unique, keep a positional component but do not describe it as insert-resilient.

4. SHOW ME THE UNION NUMBERS

You reported "components equal Union naturally" but did not show the per-sheet
breakdown I asked for. That is the same pattern as the fabricated report — a
correctness claim with no measurement behind it. Earlier row counts
(249 + 53 + 128 = 430 vs All = 426) did not match exactly, so an exact total
match needs evidence.

Report rows and summed total for EACH of Ground Layer, Structure and Trench Run
separately, plus the aggregate total. Counts and totals only, no cell values.
```
