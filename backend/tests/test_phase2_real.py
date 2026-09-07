import pytest
from pathlib import Path
from qsagent.storage.db import QSStore
from qsagent.ingest.cli import _ingest_masterfile
from qsagent.ingest.mudshark import MudsharkSource

REAL_ALDI = Path(__file__).parent.parent.parent / "masterfile" / "2026" / "August" / "Aldi Dandenong"

@pytest.mark.skipif(not REAL_ALDI.exists(), reason="Real client data not available")
def test_real_project_ingest_and_aggregation(tmp_path):
    # This covers 4c (Counts/structure) and 4d (Aggregation resolution)
    store = QSStore(tmp_path / "real.db")
    project_id = store.get_or_create_project("Aldi Dandenong")
    
    _ingest_masterfile(store, project_id, REAL_ALDI)
    
    metrics = {
        "nodes": store.conn.execute("SELECT node_type, COUNT(*) as c FROM evidence_nodes GROUP BY node_type").fetchall(),
        "claims": store.conn.execute("SELECT COUNT(*) FROM quantity_claims").fetchone()[0],
        "assumptions": store.conn.execute("SELECT COUNT(*) FROM assumptions").fetchone()[0],
        "journal_entries": store.conn.execute("SELECT COUNT(*) FROM audit_journal").fetchone()[0],
    }
    
    # 4c: structural assertions (non-zero quantities on realistic data)
    assert metrics["claims"] > 0
    assert metrics["assumptions"] > 0
    assert metrics["journal_entries"] > 0
    node_dict = {r["node_type"]: r["c"] for r in metrics["nodes"]}
    assert node_dict.get("quantity", 0) > 0
    
    # 4d: Check the comparison of component vs All Strata union
    with MudsharkSource(REAL_ALDI) as src:
        workbooks = src.parse_all()
    results = workbooks["Results"]
    
    xc = results.cross_checks.get("All_Strata_vs_components")
    assert xc is not None
    assert xc.component_total > 0
    assert xc.union_total > 0
    # After OL-filtering fix: All Strata OL=0 totals now match component OL=0 totals exactly.
    # Ratio must NOT be an exact integer (which would mean the bug is back).
    ratio = xc.union_total / xc.component_total
    assert abs(ratio - round(ratio)) > 0 or xc.passed, (
        f"Cross-check ratio={ratio:.6f}; if integer and failed, triple-counting bug is back"
    )
    # On this project the cross-check passes after the fix
    assert xc.passed, f"All_Strata_vs_components should PASS, got: {xc.message}"
