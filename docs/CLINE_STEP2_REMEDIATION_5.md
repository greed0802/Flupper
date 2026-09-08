# Paste into Cline — the passing cross-checks exposed a real problem

---

```
The uniqueness work is right this time — OL=1 rows, actual parsed QuantityRow
objects, duplicate count reported as zero. Retaining `occurrence` as a latent
disambiguator instead of deleting it was the correct call. Extending the
cross-check to all seven columns is exactly what I asked for.

But your seven passing cross-checks contain a result that cannot be physically
true. Stop and diagnose before closing Phase 2.

1. BLOCKER — THE MEASUREMENT STATES ARE MUTUALLY INCONSISTENT

Two exact identities hold in your reconciled data:

    Cut(Bulked)      = Exported(Bulked) + Reused(Bulked)
    3237.889         = 2901.623 + 336.266                    exact

    Fill(Compressed) = Imported(Banked) + FromSite(Compressed)
    3518.693         = 3182.427 + 336.266                    exact

Identity 1 is fine — all terms Bulked.

Identity 2 adds a BANKED quantity to a COMPRESSED quantity and gets a COMPRESSED
total, with no conversion. Banked is in-situ density; Compressed is placed and
compacted. They differ by the shrinkage factor and cannot sum exactly.

And this:

    Reused    (Bulked m3)     = 336.266
    From Site (Compressed m3) = 336.266      IDENTICAL to 3dp

The same material in a Bulked column and a Compressed column at the same number.
For clay at BF 1.30 / SF 0.88, 336.266 bulked is 258.666 in-situ, placing at
~227.6 compressed — not 336.266.

Site Balance agrees with the same reading: Fill - Cut = 3518.693 - 3237.889 =
280.804, exact.

So the sheet is internally coherent as LIKE-FOR-LIKE volumes. That coherence is
the problem — it only works if Bulked, Banked and Compressed are the same state
in these numbers.

Either:
  (a) the headers are NOMINAL — Mudshark labels columns by role but emits all
      figures in one state (probably in-situ). If so your Cut / BF -> insitu
      conversion is WRONG and is dividing an already-in-situ figure by 1.30 —
      a 23% UNDER-measure, the mirror of the 30% error we started with; or
  (b) the parser is reading a state-normalised totals block rather than the
      per-state columns.

DO NOT change the conversion yet. Diagnose first:

  1. Take ONE OL=1 row. Report its raw values across all 7 columns plus the
     exact column header text from the file. Numbers and structure only.
  2. State whether Cut = Exported + Reused holds at INDIVIDUAL ROW level or only
     on totals.
  3. Check Mudshark's project settings for the configured bulking and shrinkage
     factors. If BF = 1.0 and SF = 1.0, the columns are trivially equal and the
     labels are nominal — that confirms (a) cheaply.
  4. Cross-reference the Trench_Summary.xlsx you built by hand. You transcribed
     those figures knowing what they meant. If you treated them as like-for-like,
     that settles it.

Until resolved, mark converted quantities measurement_state = UNRESOLVED rather
than asserting insitu_m3.

2. THE RELATIVE PATH BUG IS STILL NOT FIXED

Against your pushed commit:

    from backend/:   84 passed, 1 skipped
    from repo root:  3 failed, 81 passed, 1 skipped
                     FAILED test_bulked_to_insitu - assert 0 > 0

test_bulked_to_insitu still fails with "assert 0 > 0" — no assumptions created,
ingest still finds no input file. The bulked->in-situ conversion is STILL
unverified, and that is exactly the test that would have caught item 1.

    FIXTURES = Path(__file__).parent / "fixtures"

3. UNIQUENESS ACCEPTED

64 OL=1 rows, 64 unique (sheet, label) combos, 0 duplicates — measured at the
right level. Accepted. One project only, but `occurrence` covers the duplicate
case. Confirm the collision guard exists: if a key repeats within one ingest run
and the value differs, it must RAISE, not overwrite.
```
