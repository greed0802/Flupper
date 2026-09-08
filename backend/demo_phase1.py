"""End-to-end Phase 1 walkthrough: evidence -> Tier 1 math -> CheckMate -> journal.

Run:  python demo_phase1.py
"""

from __future__ import annotations

from qsagent.checkmate import CheckMate, Severity
from qsagent.contracts import (
    Assumption,
    Discipline,
    EvidenceNode,
    EvidenceRef,
    Quantity,
    QuantityClaim,
    Unit,
)
from qsagent.storage import QSStore
from qsagent.tools import REGISTRY

DRAWING = b"%PDF-1.7 fake C-204 Rev 3 stormwater layout"


def main() -> None:
    store = QSStore(":memory:")
    pid = store.create_project("Bunbury ORR - Package C", client="Main Roads WA",
                               tender_no="MRWA-2026-114")

    file_hash = store.hash_bytes(DRAWING)
    store.add_document(pid, "C-204_Rev3_Stormwater.pdf", file_hash,
                       discipline="CIVIL", drawing_no="C-204", revision="3",
                       title="Stormwater Layout Sheet 04")

    ev = EvidenceRef(
        file_hash=file_hash, file_name="C-204_Rev3_Stormwater.pdf",
        drawing_no="C-204", revision="3", sheet="04", page=4, zone="C4",
        bbox=(212.0, 604.5, 388.0, 622.0),
        raw_text="SW-02  DN375 RCP  L=100m  INV 12.35-11.52  TRENCH 600 WIDE",
    )

    # --- assumption -------------------------------------------------------
    a01 = Assumption(
        id=store.next_assumption_id(pid), project_id=pid,
        statement="100 mm Type A granular bedding and 150 mm overlay adopted; "
                  "bedding class not nominated on C-204 Rev 3.",
        rationale="Standard MRWA detail for DN375 RCP in trafficable areas.",
        impact_delta_aud=3850.0, evidence=[ev])
    store.upsert_assumption(a01)

    # --- Tier 1 deterministic math ----------------------------------------
    run = REGISTRY.run("trench.volume", pid, length_m=100.0, width_mm=600,
                       depth_m=1.85, pipe_od_mm=375, bedding_mm=100,
                       overlay_mm=150, soil_class="clay")
    run_id = store.save_run(run)

    print(f"\n=== Tier 1  {run.tool_id}  (run #{run_id}) ===")
    for line in run.workings:
        print("   ", line)
    for w in run.warnings:
        print("  ! ", w)

    # --- claim, grounded in evidence --------------------------------------
    claim = QuantityClaim(
        project_id=pid, description="SW-02 stormwater trench excavation",
        quantity=Quantity(value=run.outputs["excavation_m3"], unit=Unit.M3),
        method="trench.volume", evidence=[ev], assumption_ids=[a01.id],
        workings=run.workings)
    claim_id = store.save_claim(claim)

    # --- knowledge graph: Document -> ... -> BOQ line ----------------------
    prev = None
    for node_type, label in [
        ("document", "C-204_Rev3_Stormwater.pdf"),
        ("drawing", "C-204 Rev 3"),
        ("element", "Stormwater Line SW-02"),
        ("quantity", f"Trench excavation {claim.quantity}"),
        ("boq_line", "BOQ 3.12 - Stormwater trench excavation"),
    ]:
        nid = store.add_node(EvidenceNode(project_id=pid, node_type=node_type,
                                          label=label, discipline=Discipline.CIVIL,
                                          ref=ev))
        if prev:
            store.link(pid, prev, nid, "derives")
        prev = nid

    # --- CheckMate gate ---------------------------------------------------
    report = CheckMate().verify_run(run, claims=[claim])
    store.save_checkmate(pid, claim_id, report.passed,
                         [f.as_dict() for f in report.findings])

    print(f"\n=== QS CheckMate: {report.badge()} ===")
    for f in report.findings:
        mark = {"INFO": "ok  ", "WARN": "warn", "FAIL": "FAIL"}[f.severity.value]
        print(f"  [{mark}] {f.rule}: {f.message}")

    print(f"\n=== Claim ===\n  {claim.description}: {claim.quantity}")
    print(f"  Evidence: {'; '.join(claim.citations())}")
    print(f"  Assumptions: {', '.join(claim.assumption_ids)}")

    print("\n=== Audit journal (hash-chained) ===")
    for row in store.journal_entries(pid):
        print(f"  #{row['seq']:>2} {row['actor']:<7} {row['action']:<18} "
              f"{row['approval']:<7} {row['entry_hash'][:12]}")
    print(f"  chain intact: {store.verify_journal()}")

    assert report.passed, "CheckMate rejected the run"
    store.close()


if __name__ == "__main__":
    main()
