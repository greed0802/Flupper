# Flupper

**Autonomous Civil QS Agent Platform** — an auditable, evidence-first Quantity
Surveying agent for Australian civil tendering.

Deterministic Python does the maths; the model only decides which tool to call.
Every quantity carries a drawing number, revision, sheet, zone and file hash, and
every response passes the **QS CheckMate** gate before it reaches a human.

- 📐 Roadmap & architecture → [`docs/ROADMAP.md`](docs/ROADMAP.md)
- 🐍 Backend (Phase 1 complete) → [`backend/`](backend/)

```bash
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest       # 94 passed, 1 skipped
.venv/bin/python demo_phase1.py  # end-to-end walkthrough
```

## Status

| Phase | Scope | Status |
|---|---|---|
| 1 | Storage layer, evidence schemas, Tier 1 QS math, CheckMate | ✅ complete |
| 2 | PDF/Mudshark ingestion, knowledge graph | ✅ complete |
| 3 | Agent runtime, BYOK router, approval gates | ⬜ next |
| 4 | Cloudflare Tunnel, Flutter 3-pane workstation | ⬜ |
| 5 | Revision & cost intelligence, artifact engine | ⬜ |
| 6 | Tender portal automation, risk engine | ⬜ |
