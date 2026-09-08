# Paste into Cline — two blockers remain

---

```
The UPSERT work is right. I verified the pattern preserves other sources:

    before re-ingest: count=3 sum=1612.0   (mudshark + manual QS + drawings)
    after  re-ingest: count=3 sum=1612.0   -> manual + drawings survived
    after corrected re-export: count=3 sum=1762.0  (updated in place)

That is real idempotence. Using RETURNING id to keep link() foreign keys intact
was the right call. Deleting the fabricated report and making the idempotence
fixture assert non-zero claims first were both correct.

Two blockers before Phase 2 can be called complete.

1. YOUR 3237.889 vs 9713.666 IS A BUG, NOT A FINDING

You reported this as CheckMate correctly catching a cross-check failure. Check
the ratio:

    ratio = 9713.666 / 3237.889 = 3.000000
    3237.889 * 3 = 9713.667   (diff 0.001, pure float rounding)

EXACTLY 3.0x. A real discrepancy between a component sum and a Mudshark
aggregate would be irregular — 1.02, 0.87, something arbitrary. An exact integer
multiple matching the number of component sheets (Ground Layer, Structure,
Trench Run) is a bug signature.

Most likely: the same total is being counted once per component sheet — either
the aggregate is read three times and summed, or all three component sheets are
resolving to the same All Strata Operations rows.

This is exactly the double-counting failure we have been chasing all along, and
it has now appeared INSIDE the check that was supposed to detect it. Do not ship
it as a WARN that looks like a data-quality note.

Debug it: print the row count and summed total contributed by EACH component
sheet separately, plus the aggregate total. Structure and numbers only, no cell
values. If one sheet contributes the entire 3237.889 and the other two
contribute the same figure again, that confirms it. Expected after the fix:
either reconciliation within tolerance, or an irregular difference. An exact 3.0
must not survive.

2. "MIGRATIONS NATIVE ON DB RESTART" IS FALSE

Adding ingest_key to schema.sql does NOT alter an existing database.
CREATE TABLE IF NOT EXISTS is a no-op when the table exists. Verified:

    Existing DB after re-running updated schema.sql:
      columns: ['id', 'label']
      ingest_key present? False
      UPSERT would fail: OperationalError: table evidence_nodes has no column
                         named ingest_key

Every DB created before this change — including the ones from your earlier
ingest runs — will fail at runtime on the first UPSERT. Your tests pass only
because they build a fresh in-memory DB each time.

Add a real migration:

    cols = {r[1] for r in conn.execute("PRAGMA table_info(evidence_nodes)")}
    if "ingest_key" not in cols:
        conn.execute("ALTER TABLE evidence_nodes ADD COLUMN ingest_key TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_nodes_ingest_key "
                     "ON evidence_nodes(ingest_key)")

SQLite cannot add a UNIQUE column via ALTER TABLE, so the constraint must come
from a separate unique index. Same for quantity_claims. Add a test that opens a
DB built from the OLD schema, migrates it, and performs a successful UPSERT.

3. CONFIRM THE ingest_key IS SPECIFIC ENOUGH

_ikey(file_hash, sheet, str(row_index)) omits the column. If one row produces
several quantities from different measurement columns — Cut (Bulked m3) and
Fill (Compressed m3) on the same row — they collide and the second silently
overwrites the first. Tell me whether the parser emits one claim per row or one
per measurement column. If per column, add the column to the key.

Also note row_index is positional: if Mudshark re-exports with a row inserted
above, every downstream key changes and old rows are orphaned instead of
updated. Consider including the operation-group label for stability. Not
blocking — but decide deliberately rather than by default.

Report the per-sheet breakdown for item 1 before changing any parsing logic. I
want to see which sheet contributes what.
```
