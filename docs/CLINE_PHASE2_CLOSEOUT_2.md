# Paste into Cline — one line to remove

---

```
The CheckMate rule works and the tests are consistent from both directories:

    from backend/:   91 passed, 1 skipped
    from repo root:  91 passed, 1 skipped

But the pattern list rejects the one path it should permit.

THE DEFECT

_CONVERSION_METHOD_PATTERNS includes "cut_raw_unresolved". That is not a
conversion — it is the method you use for the value you deliberately did NOT
convert, which was the whole point of the previous fix. Listing it as a
conversion inverts the intent.

Your ingest writes exactly two method strings, and I tested both against the
gate:

    mudshark.ingest.cut_raw_unresolved  -> conversion? True   <-- wrong
    mudshark.ingest.trench_length       -> conversion? False

End to end, a real bulk cut claim now returns:

    badge  : REJECTED
    [WARN] quantity.measurement_state: ... measurement_state=UNRESOLVED
    [FAIL] quantity.measurement_state_conversion: ... produced by conversion
           method 'mudshark.ingest.cut_raw_unresolved'

Full matrix:

    cut_raw_unresolved  state=UNRESOLVED  -> REJECTED
    cut_raw_unresolved  state=m3_insitu   -> VERIFIED (with notes)
    cut_raw_unresolved  state=None        -> REJECTED
    trench_length       state=UNRESOLVED  -> VERIFIED (with notes)

So EVERY bulk cut on EVERY project is rejected until the BBX parser exists. That
is not what we agreed. The agreement was:

    UNRESOLVED                        -> WARN  (usable, flagged)
    UNRESOLVED used IN a conversion   -> FAIL

Storing a raw unconverted value is the safe path we deliberately chose. It
should warn, not fail.

WHY YOUR TESTS MISSED IT

Your seven tests use synthetic method names, so they prove the mechanism works.
None asserts the behaviour of the method strings your ingest actually emits. The
gate is correct in the abstract and wrong against its only real caller. This is
the same pattern as earlier rounds: sound logic verified against constructed
inputs rather than real output.

FIX

1. Remove "cut_raw_unresolved" from _CONVERSION_METHOD_PATTERNS.

2. Add a test asserting method="mudshark.ingest.cut_raw_unresolved" with
   measurement_state="UNRESOLVED" produces a WARN and PASSES.

3. Add a test that runs the ingest CLI and asserts NO claim it produces is
   REJECTED by CheckMate. That directly closes the abstract-vs-real gap: any
   future change to the pattern list that breaks the real path fails at once.

4. Consider replacing substring matching with an explicit flag. A claim knows
   whether a conversion was applied; inferring it from a method name is fragile
   and will misfire again as names grow. A boolean conversion_applied on
   QuantityClaim makes the rule exact and removes this whole failure class.

Note also: trench_length with state=None passes with only a WARN. For linear
metres that is arguably right, but it means an untracked state on a VOLUME claim
would also slip through if its method name happened not to match a pattern.
Point 4 fixes that too.

One line to remove, two tests to add. The gate itself is correct.
```
