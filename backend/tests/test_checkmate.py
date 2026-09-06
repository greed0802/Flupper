import pytest

from qsagent.checkmate import CheckMate, Severity, check_unit_product
from qsagent.contracts import EvidenceRef, Quantity, QuantityClaim, Unit
from qsagent.tools import REGISTRY

HASH = "a" * 64


def ref(**kw):
    base = dict(file_hash=HASH, file_name="C-204.pdf", drawing_no="C-204",
                revision="3", sheet="04", raw_text="DN375 RCP @ 1:120")
    base.update(kw)
    return EvidenceRef(**base)


def claim(**kw):
    base = dict(project_id=1, description="Stormwater SW-02 trench excavation",
                quantity=Quantity(value=72.0, unit=Unit.M3), method="trench.volume",
                evidence=[ref()], workings=["0.6 x 1.2 x 100 = 72.000 m3"])
    base.update(kw)
    return QuantityClaim(**base)


class TestUnitAlgebra:
    def test_m2_times_m_is_m3(self):
        assert check_unit_product(["m2", "m"], "m3") is None

    def test_m2_times_mm_declared_m3_warns_on_scale(self):
        f = check_unit_product(["m2", "mm"], "m3")
        assert f is not None and f.severity is Severity.WARN
        assert "Mixed length scales" in f.message

    def test_dimension_mismatch_fails(self):
        f = check_unit_product(["m", "m"], "m3")
        assert f.severity is Severity.FAIL

    def test_unknown_unit_fails(self):
        f = check_unit_product(["furlong"], "m")
        assert f.severity is Severity.FAIL


class TestIndependentArithmetic:
    def test_replay_of_honest_run_passes(self):
        run = REGISTRY.run("trench.volume", 1, length_m=100, width_mm=600, depth_m=1.2)
        report = CheckMate().verify_run(run)
        assert report.passed
        assert report.badge() == "VERIFIED"

    def test_tampered_output_is_caught(self):
        run = REGISTRY.run("trench.volume", 1, length_m=100, width_mm=600, depth_m=1.2)
        run.outputs["excavation_m3"] = 99.0  # an LLM "corrected" the number
        report = CheckMate().verify_run(run)
        assert not report.passed
        assert any(f.rule == "arith.replay" for f in report.failures)

    def test_independent_multiplication_check(self):
        cm = CheckMate()
        assert cm.check_arithmetic(72.0, [0.6, 1.2, 100])[0].severity is Severity.INFO
        bad = cm.check_arithmetic(75.0, [0.6, 1.2, 100])
        assert bad[0].severity is Severity.FAIL

    def test_failed_run_is_rejected(self):
        run = REGISTRY.run("trench.volume", 1, length_m=-1, width_mm=600, depth_m=1)
        report = CheckMate().verify_run(run)
        assert not report.passed
        assert report.badge() == "REJECTED"


class TestSafetyGate:
    def test_deep_unsupported_excavation_never_reaches_the_user(self):
        run = REGISTRY.run("civil.batter_volume", 1, length_m=10, base_width_m=2,
                           depth_m=2.5, batter_hv=0.0)
        report = CheckMate().verify_run(run)
        assert not report.passed  # the tool itself refused

    def test_battered_deep_excavation_passes_with_a_note(self):
        run = REGISTRY.run("civil.batter_volume", 1, length_m=10, base_width_m=2,
                           depth_m=2.5, soil_class="clay")
        report = CheckMate().verify_run(run)
        assert report.passed
        assert any(f.rule == "safety.safework_1500" and f.severity is Severity.WARN
                   for f in report.findings)
        assert report.badge() == "VERIFIED (with notes)"

    def test_shallow_excavation_is_informational(self):
        run = REGISTRY.run("trench.volume", 1, length_m=10, width_mm=600, depth_m=1.0)
        report = CheckMate().verify_run(run)
        assert any(f.rule == "safety.safework_1500" and f.severity is Severity.INFO
                   for f in report.findings)


class TestGrounding:
    def test_claim_without_evidence_cannot_be_constructed(self):
        with pytest.raises(Exception):
            claim(evidence=[])

    def test_fully_cited_claim_passes(self):
        report = CheckMate().verify_claim(claim())
        assert report.passed
        assert "C-204" in report.findings[-1].message

    def test_missing_revision_warns(self):
        report = CheckMate().verify_claim(claim(evidence=[ref(revision=None)]))
        assert report.passed
        assert any(f.rule == "grounding.revision" for f in report.warnings)

    def test_missing_workings_warns(self):
        report = CheckMate().verify_claim(claim(workings=[]))
        assert any(f.rule == "grounding.workings" for f in report.warnings)

    def test_hallucinated_number_in_prose_is_rejected(self):
        cm = CheckMate()
        findings = cm.check_text_numbers(
            "Excavate 72.0 m3 of trench and cart away 415 m3 of spoil.", allowed=[72.0])
        assert any(f.severity is Severity.FAIL and "415" in str(f.detail["value"])
                   for f in findings)

    def test_grounded_prose_passes(self):
        findings = CheckMate().check_text_numbers(
            "Excavate 72.0 m3 of trench.", allowed=[72.0, 12.5])
        assert all(f.severity is Severity.INFO for f in findings)


class TestSuspectScales:
    def test_metres_into_a_mm_parameter_warns(self):
        run = REGISTRY.run("trench.volume", 1, length_m=10, width_mm=0.6, depth_m=1.0)
        report = CheckMate().verify_run(run)
        assert any(f.rule == "unit.suspect_scale" for f in report.findings)

    def test_mm_into_a_metre_parameter_warns(self):
        run = REGISTRY.run("trench.volume", 1, length_m=10000, width_mm=600, depth_m=1.0)
        report = CheckMate().verify_run(run)
        assert any(f.rule == "unit.suspect_scale" for f in report.findings)
