import pytest
import sqlite3
from pathlib import Path
from qsagent.storage.db import QSStore
from qsagent.ingest.cli import _ingest_masterfile
from qsagent.ingest.mudshark import _xcheck, CrossCheckResult
from qsagent.ingest.wbs import bulked_to_insitu
from qsagent.contracts import EvidenceNode, Discipline


# ── Scenario A: byte-identical re-ingest ─────────────────────────────────────
def test_idempotence(tmp_path):
    """Same file, same content — counts and sums must not change."""
    store = QSStore(tmp_path / "test.db")
    project_id = store.get_or_create_project("Test Project")
    masterfile_dir = Path("tests/fixtures/master")

    _ingest_masterfile(store, project_id, masterfile_dir)
    n1 = store.conn.execute("SELECT COUNT(*) FROM evidence_nodes").fetchone()[0]
    c1 = store.conn.execute("SELECT COUNT(*) FROM quantity_claims").fetchone()[0]
    sum1 = store.conn.execute("SELECT SUM(value) FROM quantity_claims").fetchone()[0] or 0.0

    assert c1 > 0, "Fixture must produce non-zero claim count"
    assert sum1 > 0, "Fixture must produce non-zero claims sum"

    _ingest_masterfile(store, project_id, masterfile_dir)
    n2 = store.conn.execute("SELECT COUNT(*) FROM evidence_nodes").fetchone()[0]
    c2 = store.conn.execute("SELECT COUNT(*) FROM quantity_claims").fetchone()[0]
    sum2 = store.conn.execute("SELECT SUM(value) FROM quantity_claims").fetchone()[0] or 0.0

    assert n1 == n2, f"Nodes changed: {n1} -> {n2}"
    assert c1 == c2, f"Claims changed: {c1} -> {c2}"
    assert sum1 == sum2, f"Sums changed: {sum1} -> {sum2}"




# ── Scenario B: re-export with corrected model (file_hash changes) ─────────
def test_idempotence_scenario_b_file_hash_change(tmp_path):
    """Re-ingest after file_hash changes must update value, not duplicate rows."""
    from qsagent.contracts import QuantityClaim, Quantity, Unit, EvidenceRef
    from qsagent.ingest.cli import _ikey

    store = QSStore(tmp_path / "test.db")
    project_id = store.get_or_create_project("ScenB")
    key = _ikey(str(project_id), "mudshark.results",
                "Ground Layer Operations", "Bulk Cut", "Cut (Bulked m3)")

    def _claim(value, file_hash):
        return QuantityClaim(
            project_id=project_id,
            description="Cut (in-situ)",
            quantity=Quantity(value=value, unit=Unit.M3),
            method="mudshark.ingest.cut_bulked_to_insitu",
            evidence=[EvidenceRef(
                file_hash=file_hash, file_name=f"Results_{file_hash[:8]}.xls",
                sheet="Ground Layer Operations", raw_text="Cut",
            )],
        )

    # 64-char SHA-256 hex stubs
    hash_v1 = "a" * 64
    hash_v2 = "b" * 64

    store.save_claim(_claim(100.0, hash_v1), ingest_key=key)
    c1 = store.conn.execute("SELECT COUNT(*) FROM quantity_claims").fetchone()[0]
    v1 = store.conn.execute(
        "SELECT value FROM quantity_claims WHERE ingest_key=?", (key,)).fetchone()[0]
    assert c1 == 1 and v1 == 100.0

    store.save_claim(_claim(120.0, hash_v2), ingest_key=key)  # new hash, corrected value
    c2 = store.conn.execute("SELECT COUNT(*) FROM quantity_claims").fetchone()[0]
    v2 = store.conn.execute(
        "SELECT value FROM quantity_claims WHERE ingest_key=?", (key,)).fetchone()[0]
    assert c2 == 1, f"Count grew on re-export: {c1} -> {c2} (double-counting bug)"
    assert v2 == 120.0, f"Value not updated: still {v2}"


# ── Integrity errors must propagate, not vanish ───────────────────────────────
def test_bad_project_id_raises_not_silently_discards(tmp_path):
    """FK violation must raise, not vanish silently."""
    store = QSStore(tmp_path / "strict.db")
    store.get_or_create_project("real project")
    bad_node = EvidenceNode(
        project_id=9999, node_type="quantity", label="orphan",
        discipline=Discipline.CIVIL, payload={},
    )
    with pytest.raises(Exception):
        store.add_node(bad_node, ingest_key="bad-key-orphan")


# ── Item 4a: Union reconciliation BOTH directions ─────────────────────────────
def test_union_reconciliation_logic():
    xc_warn = _xcheck("Test", 300.0, 350.0)
    assert xc_warn.passed is False
    assert "WARN" in xc_warn.message

    xc_pass = _xcheck("Test", 300.0, 300.0)
    assert xc_pass.passed is True
    assert "PASS" in xc_pass.message


# ── Item 4b: Bulked -> in-situ: division not multiplication ──────────────────
def test_bulked_to_insitu(tmp_path):
    q, bf = 100.0, 1.25
    insitu = bulked_to_insitu(q, bf)
    assert insitu == q / bf, f"Expected division: {q}/{bf}={q/bf}, got {insitu}"

    store = QSStore(tmp_path / "test.db")
    project_id = store.get_or_create_project("Test Project")
    _ingest_masterfile(store, project_id, Path("tests/fixtures/master"))

    asms = store.conn.execute("SELECT * FROM assumptions").fetchall()
    assert len(asms) > 0
    a = asms[0]
    assert "Bulking factor" in a["statement"]
    assert "-> in-situ" in a["statement"]
    assert a["status"] == "ASSUMED"
