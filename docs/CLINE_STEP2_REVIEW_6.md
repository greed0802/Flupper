# Review 6 — the reconciliation passed, and that is what exposes the real problem

The uniqueness measurement was done properly this time: OL=1 rows, actual parsed
`QuantityRow` objects, with the count of duplicate groups reported as zero.
Retaining the `occurrence` field as a latent disambiguator rather than deleting
it was the right call. Extending the cross-check to all seven columns was
exactly what was asked for.

But the seven passing cross-checks contain a result that cannot be physically
true, and it undermines the measurement-state model the whole ingest rests on.

---

## 1. BLOCKER — the measurement states cannot all be correct

Two exact identities hold in the reconciled data:

```
Identity 1:  Cut(Bulked)      = Exported(Bulked) + Reused(Bulked)
             3237.889         = 2901.623 + 336.266          exact

Identity 2:  Fill(Compressed) = Imported(Banked) + FromSite(Compressed)
             3518.693         = 3182.427 + 336.266          exact
```

Identity 1 is dimensionally sound — every term is Bulked.

**Identity 2 sums a Banked quantity and a Compressed quantity into a Compressed
total with no conversion.** Banked is in-situ density; Compressed is placed and
compacted. Those differ by the shrinkage factor. They cannot be added directly
and produce an exact total.

Worse:

```
Reused    (Bulked m3)      = 336.266
From Site (Compressed m3)  = 336.266     identical to 3 dp
```

The same physical material appears in a Bulked column and a Compressed column at
**exactly the same number**. If those labels mean what we assumed, this is
impossible. For clay at BF 1.30 / SF 0.88, 336.266 bulked is 258.666 in-situ,
which places as ~227.6 compressed — not 336.266.

Site Balance is consistent with the same reading:

```
Site Balance = Fill - Cut = 3518.693 - 3237.889 = 280.804    exact
```

So the sheet is internally coherent as a set of **like-for-like volumes**. That
coherence is the problem: it only works if Bulked, Banked and Compressed are all
the same state in these numbers.

Two possible explanations, and they demand different responses:

- **(a) The column headers are nominal, not operative.** Mudshark may label
  columns by their conceptual role while emitting all figures in one state
  (likely in-situ/banked), applying factors only in its own reports. If so, the
  `Cut (Bulked m3) / BF -> insitu_m3` conversion is **wrong** and is currently
  dividing an already-in-situ figure by 1.30 — a 23% under-measure, the mirror
  of the original 30% error.
- **(b) The parser is reading a state-normalised region of the sheet** — a
  totals block rendered in one state — rather than the per-state columns.

This is not a rounding question. It decides whether every earthworks quantity in
the system is correct or out by the bulking factor.

**Required before Phase 2 closes.** Do not change the conversion yet — diagnose
first:

1. Take one OL=1 row. Report its raw values across all seven columns, plus the
   column header text exactly as it appears in the file. Structure and numbers
   only, no client identifiers.
2. State whether `Cut = Exported + Reused` holds at the individual row level or
   only on totals.
3. Check Mudshark's own settings/report for the bulking and shrinkage factors
   configured on this project. If BF = 1.0 and SF = 1.0, the columns are
   trivially equal and the labels are nominal — explanation (a), confirmed
   cheaply.
4. Cross-reference the `Trench_Summary.xlsx` you built by hand. You transcribed
   those figures knowing what they meant. If your summary treats them as
   like-for-like, that settles it.

Until this is resolved, mark every converted quantity `AGGREGATION_STATUS` /
`measurement_state` as **UNRESOLVED** rather than asserting `insitu_m3`.

## 2. The relative-path bug is still unfixed

Verified against the pushed commit:

```
from backend/:   84 passed, 1 skipped
from repo root:  3 failed, 81 passed, 1 skipped
                 FAILED test_phase2_fixes.py::test_bulked_to_insitu - assert 0 > 0
```

`test_bulked_to_insitu` still fails with `assert 0 > 0` — no assumptions created,
because the ingest still finds no input file. **The bulked→in-situ conversion
remains unverified**, which is precisely the check that would have caught
item 1.

Anchor the fixture path to the test file:

```python
FIXTURES = Path(__file__).parent / "fixtures"
```

## 3. Uniqueness — accepted, with one scope note

`64 OL=1 rows, 64 unique (sheet, label) combos, 0 duplicates` is a real
measurement at the correct level. Accepted.

Scope caveat: one project. The `occurrence` field covers a project that does
have duplicates, which is the right design. Confirm the collision guard is
present — if a key repeats within one ingest run and the value differs, it must
raise rather than overwrite.

---

## Status

```
[x] OL=1 uniqueness measured correctly (64/64, 0 duplicates)
[x] occurrence field retained as latent disambiguator
[x] Cross-check extended to all 7 columns
[x] 3.0x union bug fixed and holding
[x] Prose corrected on the 11 vs 13 row question
[ ] Measurement states are mutually inconsistent      BLOCKER
[ ] Relative fixture path still failing from repo root
[ ] bulked -> in-situ conversion still unverified

PHASE 2 = NOT COMPLETE (1 blocker)
```

The irony is worth stating plainly: the reconciliation working correctly is what
exposed this. Seven columns agreeing to 0.0000% is not the finish line — it is
the evidence that the columns are not in the states we labelled them.
