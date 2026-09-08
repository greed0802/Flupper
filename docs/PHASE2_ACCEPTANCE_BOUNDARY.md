# Verification boundary — repository/CI vs workstation

Recorded so the skipped real-project test is never later mistaken for forgotten
coverage. It is a deliberate architectural boundary, not a gap.

## Repository / CI gate

Runs anywhere, reproducible by any reviewer:

- Synthetic fixtures only (`backend/tests/fixtures/`)
- Deterministic behavioural tests — idempotence, upsert convergence,
  cross-source preservation
- Schema and migration tests, including from a genuine legacy database
- Parser contracts and CheckMate gate behaviour
- End-to-end test binding CheckMate to real ingest output
- Client-name regression guard (`test_no_client_names.py`)

Current state: **96 collected — 95 passed, 0 failed, 1 skipped**, identical from
the repository root and from `backend/`.

## Workstation acceptance gate

Runs only on the user's machine, against data that must never enter the
repository:

- Actual client datasets and Mudshark exports
- Actual drawing sets
- Real project reconciliation (the seven-column union check against live data)

`backend/tests/test_phase2_real.py` is the harness. It reads its path from the
`FLUPPER_REAL_PROJECT` environment variable and skips when unset:

```
REAL_PROJECT_TEST = SKIPPED
SKIP_REASON = "Real data unavailable; set FLUPPER_REAL_PROJECT"
```

To execute the workstation gate:

```powershell
$env:FLUPPER_REAL_PROJECT = "masterfile\2026\August\<project dir>"
cd backend; python -m pytest tests/test_phase2_real.py -v
```

## Why the separation is necessary

A dataset deliberately excluded from the repository cannot also be required to
produce a CI result reproducible by every reviewer — the two requirements are
mutually exclusive. Collapsing them would force real client data into the repo,
which is the one constraint that must never be relaxed.

## Accurate phrasing

Correct:

> Phase 2 implementation and repository verification complete; workstation-only
> real-project acceptance executed separately.
>
> 96 tests collected — 95 passed, 0 failed, 1 skipped.

Incorrect:

> 96/96 passed.
> Phase 2 fully verified against real project evidence.

The skipped test is specifically the real-project validation. Never report a
skip as a pass.
