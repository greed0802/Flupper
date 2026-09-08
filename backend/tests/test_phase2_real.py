import pytest
from pathlib import Path
from qsagent.storage.db import QSStore
from qsagent.ingest.cli import _ingest_masterfile
from qsagent.ingest.mudshark import MudsharkSource

REAL_ALDI = (Path(__file__).parent.parent.parent
             / "masterfile" / "2026" / "August" / "Aldi Dandenong")


@pytest.mark.skipif(not REAL_ALDI.exists(), reason="Real client data not available")
def test_real_project_ingest_and_aggregation(tmp_path):
    """4c + 4d: real project structural counts and per-column aggregation checks."""
    store = QSStore(tmp_path / "real.db")
    project_id = store.get_or_create_project("Aldi Dandenong")
    _ingest_masterfile(store, project_id, REAL_ALDI)

    claims     = store.conn.execute("SELECT COUNT(*) FROM quantity_claims").fetchone()[0]
    assumptions = store.conn.execute("SELECT COUNT(*) FROM assumptions").fetchone()[0]
    journal    = store.conn.execute("SELECT COUNT(*) FROM audit_journal").fetchone()[0]
    nodes      = store.conn.execute(
        "SELECT node_type, COUNT(*) as c FROM evidence_nodes GROUP BY node_type"
    ).fetchall()
    node_dict  = {r["node_type"]: r["c"] for r in nodes}

    # 4c: structural assertions
    assert claims > 0
    assert assumptions > 0
    assert journal > 0
    assert node_dict.get("quantity", 0) > 0

    # 4d: per-column cross-checks across all measurement columns
    with MudsharkSource(REAL_ALDI) as src:
        workbooks = src.parse_all()
    results = workbooks["Results"]

    xc_keys = [k for k in results.cross_checks if k.startswith("All_Strata_vs_components")]
    assert len(xc_keys) > 0, "No All_Strata cross-check results produced"

    # Cut (Bulked) — populated by Ground Layer; must reconcile
    cut_keys = [k for k in xc_keys if "Cut" in k]
    assert cut_keys, "Cut (Bulked) column not cross-checked"
    cut_xc = results.cross_checks[cut_keys[0]]
    assert cut_xc.passed, (
        f"Cut reconciliation FAIL: components={cut_xc.component_total:.3f} "
        f"union={cut_xc.union_total:.3f} rel_diff={cut_xc.rel_diff:.4%}"
    )

    # Fill and Imported — populated by Structure/Trench Run sheets
    for col_fragment in ("Fill", "Imported"):
        col_keys = [k for k in xc_keys if col_fragment in k]
        if col_keys:
            xc = results.cross_checks[col_keys[0]]
            # Assert reconciliation holds for these columns too
            assert xc.passed, (
                f"{col_fragment} reconciliation FAIL: "
                f"components={xc.component_total:.3f} union={xc.union_total:.3f} "
                f"rel_diff={xc.rel_diff:.4%}"
            )
