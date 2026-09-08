# Workstation acceptance gate — first run

## What was observed

```
PS D:\Flicker\Flupper\Flupper> $env:FLUPPER_REAL_PROJECT
                                          <- printed nothing
PS D:\Flicker\Flupper\Flupper\backend> pytest tests/test_phase2_real.py -v
tests\test_phase2_real.py .                                          [100%]
================================ 1 passed in 2.03s ================================
```

Also note the first invocation from the repository root failed with
`file or directory not found: tests/test_phase2_real.py`. That is expected —
`pytest.ini` lives in `backend/`, so a relative test path only resolves from
there. Running `pytest backend/tests/test_phase2_real.py` from the root works
too.

## The result is PASS, and it is real

`.` means passed. A skip prints `s` and reports `1 skipped`. So the `skipif`
did not fire, meaning `REAL_PROJECT.exists()` returned `True`.

The runtime confirms it: **2.03 s**, against roughly 0.01 s for a skipped test.
Something was parsed.

Most conclusive are the assertions the test actually makes. It is not a
smoke test — it asserts:

```python
assert claims > 0
assert assumptions > 0
assert journal > 0
assert node_dict.get("quantity", 0) > 0
assert cut_xc.passed          # Cut (Bulked) reconciliation
# plus Fill and Imported reconciliation where present
```

None of those can pass against a nonexistent directory: `_ingest_masterfile`
would find no `Results.xls`, and `claims` would be 0. This is the opposite of
the hollow `0 == 0` idempotence test rejected earlier in the review — the
assertions have real content and real data behind them.

**So the workstation gate passed, on real Mudshark data, including the
seven-column union reconciliation that the 3.0× double-count bug used to
break.**

## Why the env var appeared empty

`$env:FLUPPER_REAL_PROJECT` printed nothing, yet the path resolved. Two
possible explanations:

**(a) The working tree predates the merge.** `git pull` may not have run since
`8b08214`, so the file on disk is the earlier version with the project path
hardcoded. In that case the test found the data directly and the env var was
never consulted.

**(b) The variable was set in a different shell.** PowerShell `$env:` values do
not persist across sessions.

Either way the ingest was real. Only the mechanism differs.

Worth knowing that on Windows the fallback `Path("/nonexistent")` resolves to
`D:\nonexistent` — drive-relative, not absolute. It does not exist, so the skip
logic still behaves correctly; but the sentinel is not as inert as it looks and
would be better as a value that can never be a real path.

## Confirming which case applies

```powershell
git -C D:\Flicker\Flupper\Flupper log --oneline -1
```

If that is not `8b08214` or later, the checkout predates the merge — case (a).

```powershell
git -C D:\Flicker\Flupper\Flupper pull
Get-Content backend\tests\test_phase2_real.py -TotalCount 12
```

After pulling, the file must read
`REAL_PROJECT = Path(_env) if _env else Path("/nonexistent")` and contain no
project name.

## Re-running the gate cleanly

```powershell
cd D:\Flicker\Flupper\Flupper
$env:FLUPPER_REAL_PROJECT = "D:\Flicker\Flupper\Flupper\masterfile\2026\August\<project dir>"
cd backend
pytest tests/test_phase2_real.py -v
```

Expect `1 passed`. To prove the guard works, unset it and confirm a skip:

```powershell
Remove-Item Env:\FLUPPER_REAL_PROJECT
pytest tests/test_phase2_real.py -v      # expect: 1 skipped
```

That negative control matters. A test that passes when data is present *and*
skips when it is absent is verified in both directions; one that only ever
passes could still be passing for the wrong reason.

## Status

```
REPOSITORY / CI GATE   = PASS (96 collected, 95 passed, 0 failed, 1 skipped)
WORKSTATION GATE       = PASS (real ingest, 2.03 s, reconciliation asserts held)
MECHANISM              = UNCONFIRMED (env var vs pre-merge hardcoded path)
```

Phase 2 is validated against real project data. The only open item is
confirming the workstation checkout is current, which is a two-command check.