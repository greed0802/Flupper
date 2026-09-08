# Step 2 review — one leak, one unverifiable claim

## 1. Data leak — client project names committed to GitHub

`probe_masterfile.json` (8,453 lines) was committed and pushed in
`a741947 "Data Onboarding: Added new patch files and a probe masterfile"`.

Contents check:

```
size: 111,061 chars
  "Aldi"       31x     "Dandenong"  31x
  "Francis"    26x     "Padre"      10x
  "Waverley"   10x     "Brighton"    8x
  "Kilda"       8x
contains cell VALUES?   False
contains sheet names?   True
contains file paths?    True
```

**Assessment.** No quantities or rates leaked — the probe did its job and
emitted structure only. What leaked is the **client project list**: which jobs
were tendered, their names, and the internal file layout. For a QS consultancy
that is commercially sensitive — it reveals the client book.

**Severity: contained.** The repository is `private` with `0` forks, so exposure
is limited to those already granted access. This is not a public disclosure.

**Root cause is mine.** `docs/CLINE_PHASE2_PROMPT.md` instructed Cline to write
`probe_masterfile.json`, but `.gitignore` only listed `probe_report*.json` and
`probe_drawings*.json`. I named an output file that my own ignore rules did not
cover. Cline followed instructions; the guardrail had a hole.

**Fixed:** the file is removed from tracking and the pattern broadened:

```
probe*.json
*probe*.json
/patch*.py
```

Verified: `probe_masterfile.json`, `probe_drawings.json`, `probe_report.json`,
`probe_anything_else.json`, `patch1.py`, `patch2.py` all IGNORED;
`backend/tests/fixtures/**` still trackable.

**Remaining action for the user.** Removing the file from tracking does **not**
erase it from history — commit `a741947` still contains it. Options:

- *Accept it.* The repo is private, only names leaked, no numbers. Lowest effort
  and defensible.
- *Purge history.* `git filter-repo --path probe_masterfile.json --invert-paths`
  then force-push. Rewrites SHAs; only sensible while the branch has few
  collaborators. **Do not do this without deciding deliberately** — it breaks
  every existing clone.

Recommendation: accept, given private + names-only. Revisit before the repo is
ever made public.

`patch1.py` / `patch2.py` were also committed — ad-hoc scratch scripts that
should not be part of the permanent record. Now ignored.

## 2. The Step 2 work is not verifiable from here

The summary reports `paths.py`, `mudshark.py`, `bbx.py`, `wbs.py`,
`drawings.py`, `cli.py` and 78 green tests. None of it is on the remote:

```
backend/qsagent/  -> __init__.py  checkmate  contracts  storage  tools
backend/tests/    -> test_checkmate.py  test_qs_math.py  test_storage.py
```

The two commits cited (`bbebb75`, `bf8a18c`) are local to the workstation. This
is *correct behaviour* — the prompt said "do not push". But it means the 78-test
claim is unverified here; what I can confirm is that the **72 Phase 1 tests
still pass** against the pushed tree.

The 12% image-only PDF finding is a genuinely valuable result and vindicates
Correction 3 — the 2.2% probe sample had suggested zero. Roughly 67 of 555
drawings need the no-title-block path, so that code will be exercised in
production, not a rare edge case.

## 3. Verify before trusting "all green"

Three claims deserve direct evidence rather than a passing suite, because each
concerns a bug class that *passes* tests by construction:

**a) Idempotence is asserted, not just described.** The exit criterion was that
ingesting Aldi twice yields identical graph output. Confirm the test compares
node/edge counts *and* summed quantity values across two runs — not merely that
the second run does not crash. Note the summary mentions "DB cleanup ensures it
passes idempotence checks": if the test *deletes* rows before re-ingesting, it
proves nothing. Idempotence must hold without a reset.

**b) The union reconciliation must be shown to fire.** A CheckMate rule that
never triggers is indistinguishable from one that works. Confirm there is a test
feeding deliberately mismatched component vs `All ...` totals and asserting a
WARN is produced.

**c) Bulked → in-situ conversion.** Assert that a known bulked input yields
`value / BF` and that an `Assumption` row is written with the factor used. This
was the original 30% error; it deserves an explicit test.

## 4. Ask for the reconciliation output, not a summary

Request the actual CLI output against Aldi Dandenong: node counts by type,
assumptions raised, journal entries, and the CheckMate badge summary — with
structure and counts only, no cell values. Compare the ingested earthworks total
against the `All Strata Operations` total. If those reconcile, the
double-counting class of bug is genuinely closed. That is the evidence; the test
count is not.
