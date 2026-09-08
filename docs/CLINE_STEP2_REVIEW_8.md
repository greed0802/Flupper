# Review 8 — the gate works, but it rejects the one thing it should permit

The CheckMate rule is implemented and genuinely fires. Verified from both
working directories:

```
from backend/:   91 passed, 1 skipped in 0.65s
from repo root:  91 passed, 1 skipped in 0.46s
```

92 collected, consistent. The WARN/FAIL split is real, not decorative.

But the pattern list contains a name that is not a conversion, and the
consequence is that **every bulk cut quantity from a real ingest is REJECTED**.

---

## The defect

`_CONVERSION_METHOD_PATTERNS` includes `"cut_raw_unresolved"`:

```python
_CONVERSION_METHOD_PATTERNS = (
    "bulked_to_insitu",
    "cut_bulked_to_insitu",
    "cut_raw_unresolved",      # <-- not a conversion
    "_to_insitu",
    "volume_conversion",
    "state_convert",
)
```

`cut_raw_unresolved` is the method the ingest uses for the value it deliberately
did **not** convert — the fix agreed in the previous round. It is the *absence*
of a conversion. Listing it as one inverts the intent.

The ingest writes exactly two method strings:

```
mudshark.ingest.cut_raw_unresolved  -> matches conversion pattern? True
mudshark.ingest.trench_length       -> matches conversion pattern? False
```

So the only quantity the ingest currently produces with an `UNRESOLVED` state is
auto-failed. End to end:

```
badge  : REJECTED
passed : False
  [WARN] quantity.measurement_state: ... measurement_state=UNRESOLVED
  [FAIL] quantity.measurement_state_conversion: ... was produced by conversion
         method 'mudshark.ingest.cut_raw_unresolved' but measurement state is
         not resolved
```

Full behaviour matrix:

```
cut_raw_unresolved  state=UNRESOLVED  -> REJECTED
cut_raw_unresolved  state=m3_insitu   -> VERIFIED (with notes)
cut_raw_unresolved  state=None        -> REJECTED
trench_length       state=UNRESOLVED  -> VERIFIED (with notes)
trench_length       state=m3_insitu   -> VERIFIED (with notes)
trench_length       state=None        -> VERIFIED (with notes)
```

Every bulk cut on every project is rejected until the BBX settings parser
exists. That is not the agreed design. The agreement was:

- `UNRESOLVED` -> **WARN** (usable, flagged)
- `UNRESOLVED` **used in a conversion or cost calculation** -> **FAIL**

Storing a raw, unconverted value is the safe path we deliberately chose. It
should warn, not fail.

## Why the tests did not catch it

The seven new tests exercise the matrix using synthetic method names, so they
confirm the *mechanism* works. None asserts the behaviour of the method strings
the ingest actually emits. The gate is correct in the abstract and wrong against
its only real caller.

This is the recurring pattern in this build: the logic is sound, but it is
verified against constructed inputs rather than the values the system produces.

## Fix

1. Remove `"cut_raw_unresolved"` from `_CONVERSION_METHOD_PATTERNS`.
2. Add a test asserting that a claim with
   `method="mudshark.ingest.cut_raw_unresolved"` and
   `measurement_state="UNRESOLVED"` produces a **WARN and passes** — the agreed
   behaviour.
3. Add a test that runs the ingest CLI and asserts **no claim it produces is
   REJECTED** by CheckMate. That closes the abstract/real gap directly: any
   future pattern-list change that breaks the real path fails immediately.
4. Consider replacing substring matching with an explicit flag. A claim knows
   whether a conversion was applied; inferring it from a method name is fragile
   and will misfire again as method names grow. A boolean
   `conversion_applied: bool` on `QuantityClaim` would make the rule exact.

## Also worth noting

`trench_length` with `state=None` currently passes with only a WARN. Linear
metres have no bulking state, so that is arguably correct — but it means a
genuinely untracked state on a *volume* claim would also pass if its method name
happened not to match a pattern. Point 4 fixes this class of gap too.

---

## Phase 2 status

```
[x] CheckMate rule implemented, WARN/FAIL split working
[x] 92 tests, consistent from both working directories
[x] QS diagnostic reasoning recorded in code
[ ] Pattern list rejects the deliberate no-conversion path   BLOCKER
[ ] No test asserts CheckMate accepts real ingest output

PHASE 2 = NOT COMPLETE (1 narrow blocker)
```

One line to remove, two tests to add. The gate itself is right.
