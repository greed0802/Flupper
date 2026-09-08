# Paste into Cline — Phase 2 remediation

---

```
Good work on the client-name sanitisation and on diagnosing the duplicate
document node. Both were correct and both mattered.

But PHASE 2 = NOT COMPLETE. Three claims do not hold. Do not add more tests to
compensate — fix these four things specifically.

1. DELETE phase2_verification_report.json AND DO NOT REGENERATE IT LIKE THAT

You did not measure anything. You hard-coded the values:

    report = {'tests': {'passed': 78, 'failed': 0},
              'union_reconciliation': {'mismatch_fixture': 'PASS', ...},
              'bulked_to_insitu': {'calculation': 'PASS', ...},
              'real_project_validation': {'status': 'PASS'}, ...}

Nothing in that script runs a test, queries the DB, or reads the real project.
Three of those PASS values are for work that was never executed at all: the
union reconciliation fixtures, the bulked->in-situ test, and the real Aldi
ingest. A report that restates its own inputs launders unverified claims into
something that looks like evidence. That is worse than no report.

Regenerate ONLY from real output. Any field without a measurement behind it must
say UNRESOLVED. Also: it is currently NOT gitignored — I verified it is
TRACKABLE, as is backend/tests/fixtures/master/Test_Results_31082026.xls. Decide
deliberately whether that fixture is synthetic enough to commit, and add the
report to .gitignore.

2. YOUR IDEMPOTENCE TEST PROVES NOTHING — LOOK AT ITS OUTPUT

    FIRST_RUN:  {'nodes': 1, 'edges': 0, 'claims': 0, 'claims_sum': 0.0}
    SECOND_RUN: {'nodes': 1, 'edges': 0, 'claims': 0, 'claims_sum': 0.0}

claims: 0 and claims_sum: 0.0. The fixture ingested ZERO quantities. You
compared nothing to nothing and got a delta of zero. This test passes even if
the parser is deleted entirely.

Your own logs also show the fixture is missing two of the three volume sheets,
and the one sheet present still produced no claims — that is an unexplained
parser result which should have stopped you.

Re-run idempotence on a fixture that produces a NON-ZERO claim count and a
NON-ZERO claims_sum, covering all three component sheets. Assert the sum is
non-zero before asserting the delta is zero.

3. REPLACE THE DELETE-BASED CLEANUP WITH UPSERT ON A DETERMINISTIC KEY

    DELETE FROM evidence_nodes  WHERE project_id=?;
    DELETE FROM quantity_claims WHERE project_id=?;

I simulated this against a project holding Mudshark data, a manually-entered QS
allowance, and drawing-derived quantities:

    nodes  before=5  after re-ingesting ONLY mudshark=2
    claims before=(3, 1612.0)  after=(1, 1200.0)
    DESTROYED: the manual QS rock allowance (340 m3), the drawing-derived
               trench quantity (72 m3), and every edge linking them

Re-ingesting one source deletes quantities belonging to other sources and any
human QS work. It also contradicts the append-only audit design from Phase 1:
journal entries end up referring to evidence nodes that no longer exist.

Do this instead: give every logical entity a deterministic ingest_key, e.g.
sha256(file_hash | sheet | row_index | column). Make it UNIQUE and use
INSERT ... ON CONFLICT(ingest_key) DO UPDATE. Re-ingest then converges naturally,
touches only rows from that source, and leaves everything else intact. Scope any
deletion to the specific source file being re-ingested — never to project_id.

4. RUN THE FOUR TESTS THAT WERE NEVER RUN

  a) Union reconciliation, BOTH directions:
       components 100 + 200 vs All = 350  -> assert a WARN is produced
       components 100 + 200 vs All = 300  -> assert NO warning
     Assert programmatically on the finding, not on the code existing.

  b) Bulked -> in-situ: assert the result equals Q / BF and NOT Q * BF, and
     assert an Assumption row is persisted carrying the factor, direction and
     ASSUMED status.

  c) Real project ingest: run the CLI against Aldi. Report node counts by type,
     assumptions raised, journal entry count, and the CheckMate badge summary.
     Counts and structure only — no cell values, no client names in output.

  d) All Strata Operations comparison: compare the ingested earthworks total
     against the aggregate sheet total. State whether component and aggregate
     rows are being summed as if independent. If the parser cannot determine the
     relationship, report AGGREGATION_STATUS = UNRESOLVED rather than choosing.

Report each as PASS / FAIL / UNRESOLVED with the actual numbers behind it.
UNRESOLVED is an acceptable answer. A PASS without a measurement is not.

On git history: your CURRENT_TREE_CLEAN / HISTORY_CONTAINS_SENSITIVE_ARTIFACT
reporting was correct, and you were right not to rewrite history unasked. Leave
it as is for now.
```
