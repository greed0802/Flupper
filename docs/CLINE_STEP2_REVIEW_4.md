# Review 4 — the 3.0x fix is plausible, but two new defects were introduced

The union accumulator fix and the migration work both sound right in outline.
But the remediation introduced a **new double-counting vector** and a **silent
failure mode**, and one claim is simply not true as stated.

---

## 1. The union fix is unverified — ask for the numbers

Reported: the OL=0 accumulator bug is fixed and "components equal Union
naturally". Plausible — tying the union total to OL=0 rows rather than summing
every sub-hierarchy row is exactly the right correction for an exact-3.0x
artefact.

But it is asserted, not shown. This is the same pattern as the fabricated
report: a claim of correctness with no measurement behind it. The earlier
per-sheet row counts (249 + 53 + 128 = 430 vs All = 426) did **not** match
exactly, so an exact total match deserves evidence rather than trust.

Required: the per-sheet breakdown that was explicitly requested and not
supplied — rows and summed total contributed by each of Ground Layer, Structure
and Trench Run separately, plus the aggregate. Counts and totals only.

## 2. CRITICAL — `file_hash` in the ingest_key reintroduces double-counting

The key is `_ikey(file_hash, sheet, operation_group, row_index)`.

Because `file_hash` is a component, **every Mudshark re-export mints entirely
new keys**:

```
SCENARIO A: re-ingest the IDENTICAL file (what the tests do)
  key run1=fecc223474b1  key run2=fecc223474b1  converges? True   <- UPSERT works

SCENARIO B: Mudshark RE-EXPORTS (corrected model). file_hash changes.
  old file key=fecc223474b1
  new file key=d595bdbdfe30
  converges? False
```

UPSERT never matches. The old rows are orphaned and the new rows inserted
alongside them, so the project accumulates **both versions of every quantity** —
the exact double-counting failure this entire review chain has been chasing.

The tests do not catch it because they re-ingest a byte-identical fixture, which
only exercises Scenario A. In practice, re-exporting after a model correction is
the single most common reason to re-ingest — this is the realistic case, not the
edge case.

Fix: the key must identify the **logical entity**, not the file instance. Use
project + report type + sheet + operation group (+ measurement column). Keep
`file_hash` on the `EvidenceRef` where it belongs — as provenance — not in the
identity key. Then a corrected re-export updates the quantity in place, and the
evidence reference records which file version it came from.

Add a test for Scenario B: ingest, change the file hash, re-ingest, assert claim
count is unchanged and the value updated.

## 3. `INSERT OR IGNORE` suppresses more than the unique conflict

`ON CONFLICT(ingest_key) DO UPDATE` was replaced with `INSERT OR IGNORE` +
`UPDATE ... WHERE ingest_key=?`, described as returning "exactly identically
correctly". It converges on the same-key case, but it is not equivalent:

```
plain INSERT with NULL project_id  -> IntegrityError
INSERT OR IGNORE with NULL project_id -> silently discarded, no error
rows in nodes: 0
```

`OR IGNORE` suppresses **every** constraint class — `NOT NULL`, foreign key,
`CHECK` — not just the unique violation. A malformed row disappears with no
error and no row. On a platform whose premise is that a missing quantity must
fail loudly rather than vanish, this is the wrong primitive.

The stated reason for the change was "syntax mismatches against separate partial
constraint mapping in legacy loads". `ON CONFLICT(col) DO UPDATE` requires a
unique index on `col` — which the migration now creates
(`ux_nodes_ingest_key`). Once that index exists, `ON CONFLICT` works fine on a
migrated legacy DB. Restore it.

If `INSERT OR IGNORE` must stay, follow it with a check that a row actually
exists for the key and raise if not — so suppressed violations surface.

## 4. "Overcoming random line-insert offset changes" is false

`row_index` is still in the key:

```
  row 12 -> key 793af30873c2
  row 13 -> key 6084cac9baec
  SAME logical entity, same key? False
```

Adding `operation_group` improved specificity — genuinely useful, and it fixes
the multi-column collision correctly. But a row inserted above still changes
every downstream key. The claim overstates what the change achieved.

If `operation_group` is unique within a sheet, drop `row_index` entirely and the
claim becomes true. If it is not unique, keep a positional component but stop
describing it as insert-resilient.

---

## Status

```
[x] Migration decoupled: ALTER TABLE + separate UNIQUE INDEX  (correct approach)
[x] operation_group added to key - fixes multi-column collision
[~] Union 3.0x fix - plausible, NOT demonstrated
[ ] file_hash in ingest_key -> re-export double-counts     BLOCKER
[ ] INSERT OR IGNORE hides NOT NULL / FK / CHECK failures  BLOCKER
[ ] "insert-resilient key" claim is false as stated

PHASE 2 = NOT COMPLETE (2 blockers)
```

The migration approach is right and the collision fix is right. The two blockers
are both small changes — restore `ON CONFLICT`, and take `file_hash` out of the
identity key.
