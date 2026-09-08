# Paste into Cline — Phase 2 close-out

---

```
Verified against your pushed commit. Both outstanding items are genuinely fixed:

    from backend/:   84 passed, 1 skipped in 0.44s
    from repo root:  84 passed, 1 skipped in 0.42s

Identical from both directories — the path fix is real. Collision guard
confirmed present.

Your BF=1.0 conclusion is CORRECT, but the two rows you cited do not prove it.
Worth knowing, because the reasoning will be reviewed by a QS later.

BUILDING PAD - Strata (Imported = Fill = Site Balance = 311.981421) is a PURE
IMPORT row: no cut, nothing exported. For such a row, Imported == Fill is a
tautology, and Site Balance = Fill - Cut = Fill - 0 is another. Those identities
hold regardless of the factors. Similarly Cut = Exported + Reused never crosses a
state boundary — all three terms are Bulked.

The discriminating evidence was already in your totals table:

    Reused    (Bulked m3)     = 336.266   <- cut on site, reused
    From Site (Compressed m3) = 336.266   <- fill sourced from site

Same material from the cut side and the fill side, both non-zero, different
labelled states, identical value. Testing plausible factors:

    BF=1.30 SF=0.88 -> 227.626
    BF=1.25 SF=0.90 -> 242.112
    BF=1.20 SF=0.90 -> 252.200
    only BF=SF=1.0 yields 336.266

Please put THAT reasoning in the code comment instead.

Your fix is also the right trade. Storing raw + UNRESOLVED (3237.889) beats
dividing by BF (2490.684, a silent 23% UNDER-measure). Under-measuring produces
an under-priced tender with nothing to flag it. Correct call.

ONE THING BEFORE PHASE 3 — CHECKMATE MUST ACT ON UNRESOLVED

measurement_state is persisted in schema.sql and on QuantityClaim, but:

    grep "UNRESOLVED|measurement_state" backend/qsagent/checkmate/engine.py
    (no matches)

So a quantity with an unknown measurement state passes the gate and can reach a
BOQ carrying a VERIFIED badge — asserting confidence the data does not support.
That is exactly what CheckMate exists to prevent.

Add a rule:
  - measurement_state UNRESOLVED or null  -> at least WARN
  - UNRESOLVED used in any volume conversion or cost calculation -> FAIL
  - add tests for both directions (fires when unresolved, silent when resolved)

Recording uncertainty is only half a control; the gate has to act on it.

SCOPE NOTE FOR PHASE 3

BF=1.0 is confirmed on Aldi Dandenong ONLY. Mudshark factors are per-project
settings. A job configured with BF=1.30 emits genuinely state-differentiated
figures, and the parser would store raw values marked UNRESOLVED — safe, but 30%
high until resolved.

That makes the BBX settings parser load-bearing, not optional polish: it is what
turns UNRESOLVED into a usable state. Schedule it early in Phase 3.

With the CheckMate rule added, Phase 2 is complete. The double-counting bug
class is closed and demonstrated across all 7 columns.
```
