# Step 2 remediation review — architecture fixed, one number is a bug

The three substantive complaints were addressed properly this time.

**UPSERT is genuinely correct.** Verified the pattern preserves other sources:

```
before re-ingest: count=3 sum=1612.0   (mudshark + manual QS + drawings)
after  re-ingest: count=3 sum=1612.0
manual QS + drawings survived? True

after corrected re-export: count=3 sum=1762.0  (updated in place, no duplicate)
```

That is real idempotence — convergent, non-destructive, and it correctly updates
a changed value rather than appending a second row. Using `RETURNING id` to keep
`link()` foreign keys intact was the right call.

The fabricated report was deleted and replaced with a generator, the idempotence
fixture now asserts non-zero claims before asserting a zero delta, and `4c` was
written as an auto-skipping real-data test so it does not fail on machines
without the dataset. All correct.

Two problems remain — one is a genuine bug being mistaken for a finding.

---

## 1. CRITICAL — `3237.889` vs `9713.666` is not a reconciliation finding

It is reported as CheckMate "catching" a cross-check failure. Check the ratio:

```
components/all: 3237.889 vs 9713.666
ratio b/a = 3.000000
a*3       = 9713.667   (vs 9713.666)  diff=0.001
```

**Exactly 3.000000×.** A real discrepancy between a hand-rolled component sum
and a Mudshark aggregate would be an arbitrary ratio — 1.02, 0.87, something
irregular. An exact integer multiple of 3, matching the number of component
sheets (Ground Layer, Structure, Trench Run), is a bug signature.

The overwhelmingly likely cause: the **same total is being counted once per
component sheet**. Either the aggregate figure is read three times and summed,
or each component sheet is being resolved to the same `All Strata Operations`
rows.

This matters because it is precisely the double-counting failure mode the whole
review chain has been chasing — and it has now surfaced *in the reconciliation
check itself*, which was supposed to be the thing that detects it. Shipping this
as "CheckMate working correctly" would bury the bug behind a WARN that looks
like a legitimate data-quality note.

Do not proceed until the ratio is explained. Expected outcome once fixed: the
components either reconcile within tolerance, or disagree by an irregular
amount. An exact 3.0 must not survive.

## 2. "Migrations native on DB restart" is false

Adding `ingest_key TEXT UNIQUE` to `schema.sql` does **not** alter an existing
database. `CREATE TABLE IF NOT EXISTS` is a no-op when the table already exists:

```
Existing DB after re-running updated schema.sql:
  columns: ['id', 'label']
  ingest_key present? False
  UPSERT would fail: OperationalError: table evidence_nodes has no column named ingest_key
```

Any database created before this change — including the one used for earlier
ingest runs — will not gain the column, and every UPSERT against it fails at
runtime. Tests pass only because they build a fresh in-memory database each time.

Required: a real migration step. Simplest workable form:

```python
cols = {r[1] for r in conn.execute("PRAGMA table_info(evidence_nodes)")}
if "ingest_key" not in cols:
    conn.execute("ALTER TABLE evidence_nodes ADD COLUMN ingest_key TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_nodes_ingest_key "
                 "ON evidence_nodes(ingest_key)")
```

Note SQLite cannot add a `UNIQUE` column via `ALTER TABLE`; the constraint has
to come from a separate unique index. Apply the same to `quantity_claims`. Add a
test that opens a DB built from the *old* schema, runs the migration, and
performs a successful UPSERT.

## 3. Minor — verify the ingest_key covers enough

`_ikey(file_hash, sheet, str(row_index))` omits the column. If one row yields
several quantities from different measurement columns — `Cut (Bulked m³)` and
`Fill (Compressed m³)` on the same row — they collide on the same key and the
second silently overwrites the first.

Confirm whether the parser emits one claim per row or one per measurement
column. If the latter, add the column to the key.

Also: row index is positional. If Mudshark re-exports with a row inserted above,
every downstream key changes and the old rows are orphaned rather than updated.
A content-based component (operation-group label) would be more stable. Not
blocking, but worth deciding deliberately.

---

## Status

```
[x] UPSERT replaces destructive DELETE - verified non-destructive
[x] Fabricated report deleted, generator written
[x] Idempotence fixture asserts non-zero claims first
[x] Union reconciliation tests written (both directions)
[x] Bulked -> in-situ test with ASSUMED assumption
[x] Real-project test written with safe auto-skip
[ ] Schema migration for existing databases      -> FAILS on any pre-existing DB
[ ] All Strata reconciliation explained          -> exact 3.0x ratio = bug
[ ] ingest_key collision risk on multi-column rows -> unconfirmed

PHASE 2 = NOT COMPLETE (2 blockers, 1 unconfirmed)
```

The architecture is now right. What remains is one real bug that the new
check surfaced, and one migration gap. Both are small.
