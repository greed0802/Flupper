import pytest
import sqlite3
import subprocess
from pathlib import Path
from qsagent.storage.db import QSStore
from qsagent.ingest.cli import _ingest_masterfile
from qsagent.ingest.mudshark import _xcheck, CrossCheckResult
from qsagent.ingest.wbs import bulked_to_insitu

# Item 2: Idempotence test on functional fixture
def test_idempotence(tmp_path):
    store = QSStore(tmp_path / "test.db")
    project_id = store.get_or_create_project("Test Project")
    masterfile_dir = Path("tests/fixtures/master")
    
    # Run first time
    _ingest_masterfile(store, project_id, masterfile_dir)
    
    # Measure
    n1 = store.conn.execute("SELECT COUNT(*) FROM evidence_nodes").fetchone()[0]
    c1 = store.conn.execute("SELECT COUNT(*) FROM quantity_claims").fetchone()[0]
    sum1 = store.conn.execute("SELECT SUM(value) FROM quantity_claims").fetchone()[0] or 0.0
    
    assert c1 > 0, "Fixture must produce non-zero claim count"
    assert sum1 > 0, "Fixture must produce non-zero claims sum"
    
    # Run second time
    _ingest_masterfile(store, project_id, masterfile_dir)
    
    # Measure again
    n2 = store.conn.execute("SELECT COUNT(*) FROM evidence_nodes").fetchone()[0]
    c2 = store.conn.execute("SELECT COUNT(*) FROM quantity_claims").fetchone()[0]
    sum2 = store.conn.execute("SELECT SUM(value) FROM quantity_claims").fetchone()[0] or 0.0
    
    assert n1 == n2, f"Nodes changed: {n1} -> {n2}"
    assert c1 == c2, f"Claims changed: {c1} -> {c2}"
    assert sum1 == sum2, f"Sums changed: {sum1} -> {sum2}"

# Item 4a: Union reconciliation BOTH directions
def test_union_reconciliation_logic():
    # components 100+200 vs All=350 -> WARN
    xc_warn = _xcheck("Test", 300.0, 350.0)
    assert xc_warn.passed is False
    assert "WARN" in xc_warn.message
    
    # components 100+200 vs All=300 -> PASS
    xc_pass = _xcheck("Test", 300.0, 300.0)
    assert xc_pass.passed is True
    assert "PASS" in xc_pass.message

# Item 4b: Bulked -> in-situ logic and persistence
def test_bulked_to_insitu(tmp_path):
    q = 100.0
    bf = 1.25
    insitu = bulked_to_insitu(q, bf)
    assert insitu == q / bf  # Not q * bf
    
    store = QSStore(tmp_path / "test.db")
    project_id = store.get_or_create_project("Test Project")
    masterfile_dir = Path("tests/fixtures/master")
    _ingest_masterfile(store, project_id, masterfile_dir)
    
    # Assert assumption row persisted properly
    asms = store.conn.execute("SELECT * FROM assumptions").fetchall()
    assert len(asms) > 0
    a = asms[0]
    assert "Bulking factor" in a["statement"]
    assert "-> in-situ" in a["statement"]
    assert a["status"] == "ASSUMED"
