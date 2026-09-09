import io
import json
import math
import shutil
import zipfile
from pathlib import Path
import pytest

from qsagent.storage import QSStore
from qsagent.ingest.bbx import _extract_factors
from qsagent.ingest.cli import _ingest_masterfile
from qsagent.checkmate.engine import CheckMate, Severity
from qsagent.contracts.evidence import QuantityClaim

FIXTURES = Path(__file__).parent / "fixtures"

def create_bbx(materials_xml: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("project.xml", f"<Project><Site><Materials>{materials_xml}</Materials></Site></Project>")
    return buf.getvalue()

def test_extract_factors_unity():
    bbx = create_bbx("<Material><BulkingFactor>1</BulkingFactor><CompressionFactor>1.0</CompressionFactor></Material>")
    bf, sf = _extract_factors(bbx)
    assert bf == 1.0
    assert sf == 1.0

def test_extract_factors_conflicting():
    bbx = create_bbx("<Material><BulkingFactor>1.25</BulkingFactor></Material><Material><BulkingFactor>1.30</BulkingFactor></Material>")
    bf, sf = _extract_factors(bbx)
    assert bf is None
    assert sf is None

def test_extract_factors_malformed():
    bbx = create_bbx("<Material><BulkingFactor>TBA</BulkingFactor></Material>")
    bf, sf = _extract_factors(bbx)
    assert bf is None

def test_extract_factors_zero_is_invalid():
    bbx = create_bbx("<Material><BulkingFactor>0</BulkingFactor></Material>")
    bf, sf = _extract_factors(bbx)
    assert bf is None

def test_extract_factors_missing_tag():
    bbx = create_bbx("<Material><Name>A</Name></Material>")
    bf, sf = _extract_factors(bbx)
    assert bf is None
    assert sf is None

def test_extract_factors_multiple_consistent_resolves():
    bbx = create_bbx("<Material><BulkingFactor>1.3</BulkingFactor></Material><Material><BulkingFactor>1.3000</BulkingFactor></Material>")
    bf, sf = _extract_factors(bbx)
    assert math.isclose(bf, 1.3)

def create_fake_masterfile(tmp_path: Path, bbx_content: bytes) -> Path:
    mdir = tmp_path / "masterfile"
    shutil.copytree(FIXTURES / "master", mdir)
    (mdir / "settings.bbx").write_bytes(bbx_content)
    return mdir

def _row_to_claim(row) -> QuantityClaim:
    return QuantityClaim.model_validate({
        "claim_id": row["claim_id"],
        "project_id": row["project_id"],
        "description": row["description"],
        "quantity": {"value": row["value"], "unit": row["unit"]},
        "measurement_state": row["measurement_state"],
        "conversion_applied": bool(row["conversion_applied"]),
        "method": row["method"],
        "evidence": json.loads(row["evidence"]),
        "assumption_ids": json.loads(row["assumptions"]),
        "workings": json.loads(row["workings"]),
    })

def test_ingest_unity_factors(tmp_path):
    bbx = create_bbx("<Material><BulkingFactor>1</BulkingFactor><CompressionFactor>1.0</CompressionFactor></Material>")
    mdir = create_fake_masterfile(tmp_path, bbx)
    store = QSStore(tmp_path / "test.db")
    pid = store.get_or_create_project("Test")
    _ingest_masterfile(store, pid, mdir)
    claims = list(store.conn.execute("SELECT * FROM quantity_claims WHERE method = 'mudshark.ingest.cut_raw'"))
    assert len(claims) > 0, "Expected at least one non-zero claim"
    c_row = claims[0]
    assert c_row["measurement_state"] == "bulked"
    assert c_row["conversion_applied"] == 0
    asms = list(store.conn.execute("SELECT * FROM assumptions"))
    assert not asms, "No assumption should be raised for unity"
    claim = _row_to_claim(c_row)
    report = CheckMate().verify_claim(claim)
    assert report.passed
    assert not any(f.severity is Severity.WARN for f in report.findings if f.rule == "quantity.measurement_state")
    assert not any(f.severity is Severity.FAIL for f in report.findings)
    nodes = list(store.conn.execute("SELECT payload FROM evidence_nodes WHERE node_type = 'document'"))
    bbx_node_payloads = [json.loads(n["payload"]) for n in nodes if json.loads(n["payload"]).get("kind") == "bbx"]
    assert len(bbx_node_payloads) == 1
    assert math.isclose(bbx_node_payloads[0].get("project_sf"), 1.0)

def test_ingest_non_unity_factors(tmp_path):
    bbx = create_bbx("<Material><BulkingFactor>1.25</BulkingFactor><CompressionFactor>0.9</CompressionFactor></Material>")
    mdir = create_fake_masterfile(tmp_path, bbx)
    store = QSStore(tmp_path / "test.db")
    pid = store.get_or_create_project("Test")
    _ingest_masterfile(store, pid, mdir)
    claims = list(store.conn.execute("SELECT * FROM quantity_claims WHERE method = 'mudshark.ingest.cut_bulked_to_insitu'"))
    assert len(claims) > 0
    c_row = claims[0]
    assert c_row["measurement_state"] == "m3_insitu"
    assert c_row["conversion_applied"] == 1
    ev_list = json.loads(c_row["evidence"])
    raw_input = float(ev_list[0]["raw_text"].split("=")[1])
    stored_value = float(c_row["value"])
    assert raw_input > 0
    assert stored_value > 0
    assert math.isclose(stored_value, raw_input / 1.25, rel_tol=1e-6, abs_tol=1e-6)
    asms = list(store.conn.execute("SELECT * FROM assumptions"))
    assert len(asms) > 0
    a_row = asms[0]
    assert a_row["status"] == "ASSUMED"
    assert "1.25" in a_row["statement"]
    assert "bulked m3 to" in a_row["statement"]
    assert "insitu m3" in a_row["statement"]
    claim = _row_to_claim(c_row)
    report = CheckMate().verify_claim(claim)
    assert report.passed
    assert not any(f.severity is Severity.WARN for f in report.findings if f.rule == "quantity.measurement_state")
    assert not any(f.severity is Severity.FAIL for f in report.findings)
    nodes = list(store.conn.execute("SELECT payload FROM evidence_nodes WHERE node_type = 'document'"))
    bbx_node_payloads = [json.loads(n["payload"]) for n in nodes if json.loads(n["payload"]).get("kind") == "bbx"]
    assert len(bbx_node_payloads) == 1
    assert math.isclose(bbx_node_payloads[0].get("project_sf"), 0.9)

def test_ingest_conflicting_factors(tmp_path):
    bbx = create_bbx("<Material><BulkingFactor>1.25</BulkingFactor></Material><Material><BulkingFactor>1.30</BulkingFactor></Material>")
    mdir = create_fake_masterfile(tmp_path, bbx)
    store = QSStore(tmp_path / "test.db")
    pid = store.get_or_create_project("Test")
    _ingest_masterfile(store, pid, mdir)
    claims = list(store.conn.execute("SELECT * FROM quantity_claims WHERE method = 'mudshark.ingest.cut_raw_unresolved'"))
    assert len(claims) > 0
    c_row = claims[0]
    assert c_row["measurement_state"] == "UNRESOLVED"
    assert c_row["conversion_applied"] == 0
    ev_list = json.loads(c_row["evidence"])
    raw_input = float(ev_list[0]["raw_text"].split("=")[1])
    assert raw_input > 0
    assert math.isclose(float(c_row["value"]), raw_input, rel_tol=1e-6, abs_tol=1e-6)
    claim = _row_to_claim(c_row)
    report = CheckMate().verify_claim(claim)
    assert report.passed
    warns = [f for f in report.findings if f.severity is Severity.WARN and f.rule == 'quantity.measurement_state']
    assert len(warns) == 1
    assert not any(f.severity is Severity.FAIL for f in report.findings)

def test_ingest_missing_factors(tmp_path):
    bbx = create_bbx("<Material><Name>A</Name></Material>")
    mdir = create_fake_masterfile(tmp_path, bbx)
    store = QSStore(tmp_path / "test.db")
    pid = store.get_or_create_project("Test")
    _ingest_masterfile(store, pid, mdir)
    claims = list(store.conn.execute("SELECT * FROM quantity_claims WHERE method = 'mudshark.ingest.cut_raw_unresolved'"))
    assert len(claims) > 0
    c_row = claims[0]
    assert c_row["measurement_state"] == "UNRESOLVED"
    assert c_row["conversion_applied"] == 0
    ev_list = json.loads(c_row["evidence"])
    raw_input = float(ev_list[0]["raw_text"].split("=")[1])
    assert raw_input > 0
    assert math.isclose(float(c_row["value"]), raw_input, rel_tol=1e-6, abs_tol=1e-6)
    claim = _row_to_claim(c_row)
    report = CheckMate().verify_claim(claim)
    assert report.passed
    warns = [f for f in report.findings if f.severity is Severity.WARN and f.rule == 'quantity.measurement_state']
    assert len(warns) == 1
    assert not any(f.severity is Severity.FAIL for f in report.findings)

def test_ingest_multiple_bbx_one_malformed(tmp_path):
    bbx1 = create_bbx("<Material><BulkingFactor>1.25</BulkingFactor></Material>")
    bbx2 = create_bbx("<Material><BulkingFactor>TJ4</BulkingFactor></Material>")
    mdir = tmp_path / "masterfile"
    shutil.copytree(FIXTURES / "master", mdir)
    (mdir / "valid.bbx").write_bytes(bbx1)
    (mdir / "malformed.bbx").write_bytes(bbx2)
    store = QSStore(tmp_path / "test.db")
    pid = store.get_or_create_project("Test")
    _ingest_masterfile(store, pid, mdir)
    claims = list(store.conn.execute("SELECT * FROM quantity_claims WHERE method = 'mudshark.ingest.cut_raw_unresolved'"))
    assert len(claims) > 0
    c_row = claims[0]
    assert c_row["measurement_state"] == "UNRESOLVED"
    assert c_row["conversion_applied"] == 0
    ev_list = json.loads(c_row["evidence"])
    raw_input = float(ev_list[0]["raw_text"].split("=")[1])
    assert raw_input > 0
    assert math.isclose(float(c_row["value"]), raw_input, rel_tol=1e-6, abs_tol=1e-6)
    claim = _row_to_claim(c_row)
    report = CheckMate().verify_claim(claim)
    assert report.passed
    warns = [f for f in report.findings if f.severity is Severity.WARN and f.rule == 'quantity.measurement_state']
    assert len(warns) == 1
    assert not any(f.severity is Severity.FAIL for f in report.findings)