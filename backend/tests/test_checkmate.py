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
        trace = next((f for f in report.findings if f.rule == "grounding.trace"), None)
        assert trace is not None and "C-204" in trace.message

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


class TestMeasurementStateGate:
    """Rule: measurement_state UNRESOLVED/None → WARN; conversion method + UNRESOLVED → FAIL.

    Discriminating evidence that BF = SF = 1.0 on the Aldi Dandenong project
    (and therefore that applying any bulking factor would silently produce a
    wrong figure):

        Reused   (Bulked m³)      = 336.266   ← material cut on site, reused
        From Site (Compressed m³) = 336.266   ← same material used as fill

    The same physical material appears in a Bulked column on the cut side and a
    Compressed column on the fill side, yet the values are identical to 3 d.p.
    Testing plausible conversion factors:
        BF=1.30, SF=0.88 → From Site = 336.266 × 0.88/1.30 = 227.626  ≠ 336.266
        BF=1.25, SF=0.90 → From Site = 336.266 × 0.90/1.25 = 242.112  ≠ 336.266
        BF=1.20, SF=0.90 → From Site = 336.266 × 0.90/1.20 = 252.200  ≠ 336.266
        BF=SF=1.0        → From Site = 336.266 × 1.0/1.0   = 336.266  ✓

    Only BF = SF = 1.0 is consistent. The column headers are role labels; all
    figures are emitted in one unified state. Applying a non-unity BF produces
    a silent 23% under-measure and an under-priced tender.
    """

    def test_unresolved_state_produces_warn_not_fail(self):
        """UNRESOLVED alone → WARN, claim still passes gate."""
        c = claim(measurement_state="UNRESOLVED",
                  method="mudshark.ingest.cut_raw_unresolved")
        report = CheckMate().verify_claim(c)
        ms_findings = [f for f in report.findings
                       if f.rule == "quantity.measurement_state"]
        assert any(f.severity is Severity.WARN for f in ms_findings), (
            "Expected WARN for UNRESOLVED state")

    def test_null_state_produces_warn_not_fail(self):
        """measurement_state=None → WARN; not every existing claim has state tagged."""
        c = claim(measurement_state=None)
        report = CheckMate().verify_claim(c)
        ms_findings = [f for f in report.findings
                       if f.rule == "quantity.measurement_state"]
        assert any(f.severity is Severity.WARN for f in ms_findings), (
            "Expected WARN for None state")
        # Must not FAIL — too disruptive for legacy claims without state tag
        assert not any(f.severity is Severity.FAIL for f in ms_findings)

    def test_conversion_method_plus_unresolved_fails(self):
        """Conversion method + UNRESOLVED → FAIL; cannot reach BOQ with VERIFIED badge."""
        # Simulate: someone calls a conversion tool without first resolving state
        c = claim(
            measurement_state="UNRESOLVED",
            method="mudshark.ingest.cut_bulked_to_insitu",
            description="Cut (in-situ) – BUILDING PAD via bulked_to_insitu",
        )
        report = CheckMate().verify_claim(c)
        fail_findings = [f for f in report.findings
                         if f.rule == "quantity.measurement_state_conversion"
                         and f.severity is Severity.FAIL]
        assert fail_findings, (
            "Conversion method with UNRESOLVED state must produce a FAIL finding")
        assert not report.passed, "Gate must reject UNRESOLVED + conversion method"
        assert report.badge() == "REJECTED"

    def test_conversion_method_plus_null_state_fails(self):
        """Conversion method + None state also → FAIL."""
        c = claim(
            measurement_state=None,
            method="mudshark.ingest.cut_bulked_to_insitu",
            description="Cut (in-situ) – BUILDING PAD, state untracked",
        )
        report = CheckMate().verify_claim(c)
        fail_findings = [f for f in report.findings
                         if f.rule == "quantity.measurement_state_conversion"
                         and f.severity is Severity.FAIL]
        assert fail_findings, "Conversion method with None state must FAIL"

    def test_resolved_state_silent_on_non_conversion_method(self):
        """Resolved state on an ordinary claim → no measurement_state findings."""
        c = claim(measurement_state="m3_insitu")
        report = CheckMate().verify_claim(c)
        ms_findings = [f for f in report.findings
                       if "measurement_state" in f.rule]
        assert not ms_findings, (
            f"Resolved state should produce no measurement_state findings; got {ms_findings}")

    def test_resolved_state_on_conversion_method_passes(self):
        """Confirmed state + conversion method → gate passes cleanly."""
        c = claim(
            measurement_state="m3_insitu",
            method="mudshark.ingest.cut_bulked_to_insitu",
            description="Cut (in-situ) – confirmed BF=1.0 from BBX settings",
        )
        report = CheckMate().verify_claim(c)
        fail_findings = [f for f in report.findings if f.severity is Severity.FAIL]
        assert not fail_findings, (
            f"Resolved state + conversion method must pass; failures: {fail_findings}")

    def test_unresolved_claim_in_verify_run_is_flagged(self):
        """Unresolved claims passed to verify_run surface the state warning."""
        run = REGISTRY.run("trench.volume", 1, length_m=10, width_mm=600, depth_m=1.0)
        c = claim(measurement_state="UNRESOLVED",
                  method="mudshark.ingest.cut_raw_unresolved")
        report = CheckMate().verify_run(run, claims=[c])
        assert any(f.rule == "quantity.measurement_state" for f in report.findings), (
            "verify_run must surface measurement_state warnings from claims")

    def test_real_ingest_method_cut_raw_unresolved_warns_not_fails(self):
        """Real ingest method 'mudshark.ingest.cut_raw_unresolved' + UNRESOLVED → WARN + PASSES.

        This is the safe path: storing raw unconverted values marked UNRESOLVED.
        The gate must warn (flagging uncertainty) but pass (allowing the claim).
        Rejecting this path would block every bulk cut until BBX parser exists.
        """
        c = claim(
            measurement_state="UNRESOLVED",
            method="mudshark.ingest.cut_raw_unresolved",
            description="Cut (Bulked) – BUILDING PAD",
        )
        report = CheckMate().verify_claim(c)
        # Must have measurement_state warning
        assert any(
            f.rule == "quantity.measurement_state" and f.severity is Severity.WARN
            for f in report.findings
        ), "Expected WARN for UNRESOLVED state"
        # Must NOT fail
        assert not any(f.severity is Severity.FAIL for f in report.findings), (
            f"cut_raw_unresolved is NOT a conversion; must not FAIL. Got: {report.findings}"
        )
        assert report.passed, "Gate must pass for unconverted raw storage"
        assert report.badge() == "VERIFIED (with notes)"

    def test_real_ingest_method_trench_length_unresolved_passes(self):
        """Linear measurement method + UNRESOLVED → WARN + PASSES."""
        c = claim(
            measurement_state="UNRESOLVED",
            method="mudshark.ingest.trench_length",
            description="Trench T-01 length",
            quantity=Quantity(value=45.2, unit=Unit.M),
        )
        report = CheckMate().verify_claim(c)
        assert report.passed
        # May have measurement_state warning, but must not FAIL
        assert not any(f.severity is Severity.FAIL for f in report.findings)


class TestIngestE2E:
    """End-to-end integration: real ingest output must pass CheckMate gate.

    This closes the abstract-vs-real gap. If a future change to
    _CONVERSION_METHOD_PATTERNS causes the ingest output to be rejected,
    this test fails immediately.
    """

    def test_no_ingested_claim_is_rejected_by_checkmate(self, tmp_path):
        """Run ingest on real Mudshark export; assert all claims pass CheckMate.
        
        This closes the abstract-vs-real gap. If a future change to
        _CONVERSION_METHOD_PATTERNS breaks the ingest output, this fails immediately.
        """
        from pathlib import Path
        from qsagent.storage.db import QSStore
        from qsagent.ingest.cli import _ingest_masterfile

        fixture_dir = Path(__file__).parent / "fixtures" / "master"
        if not fixture_dir.exists():
            pytest.skip(f"Fixture {fixture_dir} not found")

        store = QSStore(tmp_path / "test_e2e.db")
        project_id = store.get_or_create_project("E2E CheckMate Test")

        # Ingest
        _ingest_masterfile(store, project_id, fixture_dir)

        # Retrieve all claims
        rows = store.conn.execute(
            """
            SELECT claim_id, description, value, unit,
                   measurement_state, method
            FROM quantity_claims WHERE project_id = ?
            """,
            (project_id,),
        ).fetchall()

        assert len(rows) > 0, "No claims found in DB after ingest"

        # Build minimal QuantityClaim objects and verify each
        from qsagent.contracts.evidence import QuantityClaim, Quantity, Unit, EvidenceRef

        ref = EvidenceRef(
            file_name="test_export.csv",
            file_hash="0" * 64,
            sheet="BULK CUT & FILL",
            raw_text="(synthetic test evidence)",
        )

        rejected = []
        for row in rows:
            c = QuantityClaim(
                claim_id=row[0],
                project_id=project_id,
                description=row[1],
                quantity=Quantity(value=row[2], unit=Unit(row[3])),
                measurement_state=row[4],
                method=row[5],
                evidence=[ref],
            )
            report = CheckMate().verify_claim(c)
            if not report.passed:
                rejected.append((c.description, c.method, report.badge(), report.findings))

        if rejected:
            msg = "CheckMate rejected claims from real ingest:\n"
            for desc, method, badge, findings in rejected:
                msg += f"\n  {desc} [{method}] → {badge}\n"
                for f in findings:
                    if f.severity is Severity.FAIL:
                        msg += f"    [FAIL] {f.rule}: {f.message}\n"
            pytest.fail(msg)

