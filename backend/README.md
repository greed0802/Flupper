# Flupper backend — Phase 1

Deterministic foundation for the Autonomous Civil QS Agent Platform.

```
qsagent/
  contracts/   Pydantic evidence contracts (no quantity without evidence)
  storage/     SQLite single store + hash-chained append-only audit journal
  tools/       Tier 1 deterministic QS math + tool registry
  checkmate/   Pre-response verification gate
tests/         72 tests
demo_phase1.py End-to-end walkthrough
```

## Setup

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest      # 72 passed
.venv/bin/python demo_phase1.py
```
