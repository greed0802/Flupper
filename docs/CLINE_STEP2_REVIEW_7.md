# Review 7 — blocker resolved correctly; Phase 2 acceptable with one gap to close

Verified against the pushed commit. The two outstanding items are genuinely
fixed, and the measurement-state conclusion holds.

---

## Verified

**Relative-path bug fixed.** Identical results from both working directories:

```
from backend/:   84 passed, 1 skipped in 0.44s
from repo root:  84 passed, 1 skipped in 0.42s
```

85 collected. The skip remains `test_phase2_real.py` ("Real client data not
available"), which is correct behaviour on a machine without the dataset.

**The BF = 1.0 conclusion is correct** — though the two rows cited do not
actually prove it.

`BUILDING PAD - Strata` shows `Imported = Fill = Site Balance = 311.981421`. For
a **pure-import row** (no cut, nothing exported), `Imported == Fill` is a
tautology, and `Site Balance = Fill - Cut = Fill - 0` is another. Those
identities collapse regardless of what the factors are. Likewise
`Cut = Exported + Reused` never crosses a state boundary — all three terms are
Bulked.

The discriminating evidence was already in the totals table from the previous
round:

```
Reused    (Bulked m3)     = 336.266    <- material cut on site, reused
From Site (Compressed m3) = 336.266    <- fill sourced from site
```

Same physical material seen from the cut side and the fill side, both non-zero,
labelled different states, identical value. Testing plausible factor pairs:

```
BF=1.30 SF=0.88 -> 227.626
BF=1.25 SF=0.90 -> 242.112
BF=1.20 SF=0.90 -> 252.200
...only BF=SF=1.0 yields 336.266
```

So the conclusion is well-founded. Worth recording the *correct* reasoning in
the code comment, because the stated justification would not survive review by
another QS.

**The fix is the right trade.** Storing the raw value with
`measurement_state="UNRESOLVED"` rather than dividing by BF:

```
Option A (chosen):  3237.889  correct given BF=1.0; if a future project has
                              BF=1.3, the value is high but FLAGGED
Option B (previous): 2490.684  a silent 23% UNDER-measure on this project
```

Under-measuring is the worse commercial failure — it produces an under-priced
tender with nothing to signal it. Making the uncertainty visible is right.

**Collision guard confirmed** present in `cli.py`, raising on a repeated key with
a differing value.

---

## Remaining gap — `UNRESOLVED` is recorded but not enforced

`measurement_state` is persisted in `schema.sql` and on the `QuantityClaim`
contract. But CheckMate has no rule referencing it:

```
grep "UNRESOLVED\|measurement_state" backend/qsagent/checkmate/engine.py
(no matches)
```

So a quantity whose state is unknown can flow through the gate and reach a BOQ
with a VERIFIED badge. That is precisely the class of failure CheckMate exists to
prevent — the badge would assert confidence the data does not support.

Add a rule: a claim with `measurement_state` of `UNRESOLVED` (or null) must
produce at least a `WARN`, and must **FAIL** if it is used in any downstream
volume conversion or cost calculation. Recording the uncertainty is only half
the control; the gate has to act on it.

## Scope note — BF = 1.0 is a per-project finding

Mudshark bulking and shrinkage factors are per-project settings. The finding is
confirmed on Aldi Dandenong only. A job configured with BF = 1.30 would emit
genuinely state-differentiated figures, and the parser would still store raw
values marked `UNRESOLVED` — safe, but 30% high until resolved.

This makes the deferred BBX settings parser load-bearing rather than optional
polish: it is what converts `UNRESOLVED` into a usable state. Worth scheduling
early in Phase 3 rather than leaving open-ended.

---

## Phase 2 status

```
[x] Idempotence via UPSERT, non-destructive, Scenario B tested
[x] ingest_key excludes file_hash; occurrence retained as disambiguator
[x] Collision guard raises on conflicting duplicate keys
[x] Constraint violations propagate (FK / NOT NULL loud, UNIQUE handled)
[x] Schema migration for pre-existing databases
[x] 3.0x union double-count fixed and holding
[x] Cross-check covers all 7 measurement columns at 0.0000%
[x] OL=1 uniqueness measured (64/64, 0 duplicates)
[x] Measurement-state inconsistency diagnosed; unsafe conversion removed
[x] Suite passes from both working directories (84 passed, 1 skipped)
[x] Client names removed from tracked source; probe outputs gitignored
[ ] CheckMate does not act on measurement_state=UNRESOLVED
[ ] Real-project test skips without the dataset (by design, but 0 CI coverage)
[ ] BF=1.0 confirmed on one project only; BBX settings parser outstanding

PHASE 2 = ACCEPTABLE, with the CheckMate rule as a precondition for Phase 3
```

The double-counting class of bug is closed and demonstrated. The remaining item
is small and well-defined.
