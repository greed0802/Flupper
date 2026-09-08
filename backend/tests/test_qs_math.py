import math

import pytest

from qsagent.tools import REGISTRY
from qsagent.tools.qs_math import (
    SAFEWORK_DEPTH_LIMIT_M,
    batter_volume,
    bulking_shrinkage,
    pad_footing,
    trench_volume,
)


class TestTrenchVolume:
    def test_shallow_vertical_trench(self):
        r = trench_volume(length_m=100, width_mm=600, depth_m=1.2, pipe_od_mm=300)
        # 0.6 x 1.2 x 100
        assert r.outputs["excavation_m3"] == pytest.approx(72.0)
        assert r.outputs["batter_hv"] == 0.0
        assert r.outputs["safework_compliant"] is True
        assert not r.warnings

    def test_bedding_and_overlay_volumes(self):
        r = trench_volume(length_m=50, width_mm=800, depth_m=1.0, pipe_od_mm=375,
                          bedding_mm=100, overlay_mm=150)
        assert r.outputs["bedding_m3"] == pytest.approx(0.8 * 0.1 * 50)
        assert r.outputs["overlay_m3"] == pytest.approx(0.8 * 0.15 * 50)
        assert r.outputs["pipe_displacement_m3"] == pytest.approx(
            math.pi / 4 * 0.375**2 * 50)

    def test_volumes_balance(self):
        r = trench_volume(length_m=37.5, width_mm=750, depth_m=1.45, pipe_od_mm=450)
        o = r.outputs
        total = (o["bedding_m3"] + o["haunch_m3"] + o["overlay_m3"]
                 + o["pipe_displacement_m3"] + o["backfill_m3"])
        assert total == pytest.approx(o["excavation_m3"])

    def test_deep_trench_auto_batters(self):
        r = trench_volume(length_m=20, width_mm=900, depth_m=2.4, pipe_od_mm=450,
                          soil_class="clay")
        assert r.outputs["batter_hv"] == 0.75
        # 2.4 * (0.9 + 0.75*2.4) * 20
        assert r.outputs["excavation_m3"] == pytest.approx(2.4 * (0.9 + 1.8) * 20)
        assert any("SafeWork" in w for w in r.warnings)

    def test_deep_trench_shored_stays_vertical(self):
        r = trench_volume(length_m=20, width_mm=900, depth_m=2.4, pipe_od_mm=450,
                          shored=True)
        assert r.outputs["batter_hv"] == 0.0
        assert r.outputs["excavation_m3"] == pytest.approx(0.9 * 2.4 * 20)
        assert any("shored" in w for w in r.warnings)

    def test_pipe_wider_than_trench_rejected(self):
        with pytest.raises(ValueError, match="does not fit"):
            trench_volume(length_m=10, width_mm=300, depth_m=1.2, pipe_od_mm=375)

    def test_zones_exceeding_depth_rejected(self):
        with pytest.raises(ValueError, match="exceeds trench depth"):
            trench_volume(length_m=10, width_mm=900, depth_m=0.5, pipe_od_mm=450,
                          bedding_mm=100, overlay_mm=150)

    def test_negative_length_rejected(self):
        with pytest.raises(ValueError, match="greater than zero"):
            trench_volume(length_m=-5, width_mm=600, depth_m=1.0)


class TestBulkingShrinkage:
    def test_clay_factors(self):
        r = bulking_shrinkage(insitu_m3=100, material="clay")
        assert r.outputs["loose_m3"] == pytest.approx(130.0)
        assert r.outputs["compacted_m3"] == pytest.approx(88.0)

    def test_rock_swells_and_does_not_shrink(self):
        r = bulking_shrinkage(insitu_m3=50, material="rock")
        assert r.outputs["loose_m3"] > 50
        assert r.outputs["compacted_m3"] > 50  # rock fill occupies more when placed

    def test_truck_loads_round_up(self):
        r = bulking_shrinkage(insitu_m3=100, material="sand", truck_capacity_m3=10)
        assert r.outputs["truck_loads"] == math.ceil(112.0 / 10)

    def test_unknown_material_warns_and_defaults(self):
        r = bulking_shrinkage(insitu_m3=10, material="unobtanium")
        assert any("No bulking factor" in w for w in r.warnings)
        assert r.outputs["bulking_factor"] == 1.20

    def test_explicit_override_wins(self):
        r = bulking_shrinkage(insitu_m3=10, material="clay", bulking_factor=1.45)
        assert r.outputs["loose_m3"] == pytest.approx(14.5)

    def test_zero_volume_is_legal(self):
        r = bulking_shrinkage(insitu_m3=0, material="clay")
        assert r.outputs["loose_m3"] == 0
        assert r.outputs["truck_loads"] == 0


class TestBatterVolume:
    def test_shallow_vertical_allowed(self):
        r = batter_volume(length_m=10, base_width_m=2, depth_m=1.0, batter_hv=0.0)
        assert r.outputs["volume_m3"] == pytest.approx(20.0)
        assert r.outputs["safework_compliant"] is True

    def test_deep_vertical_unsupported_is_rejected(self):
        with pytest.raises(ValueError, match="SafeWork Australia breach"):
            batter_volume(length_m=10, base_width_m=2, depth_m=2.0, batter_hv=0.0)

    def test_deep_defaults_to_soil_batter(self):
        r = batter_volume(length_m=10, base_width_m=2, depth_m=2.0, soil_class="sand")
        assert r.outputs["batter_hv"] == 1.0
        assert r.outputs["top_width_m"] == pytest.approx(2 + 2 * 1.0 * 2.0)
        assert r.outputs["volume_m3"] == pytest.approx(2.0 * (2 + 2.0) * 10)

    def test_extra_over_is_the_batter_wedge(self):
        r = batter_volume(length_m=10, base_width_m=2, depth_m=2.0, batter_hv=0.5)
        assert r.outputs["extra_over_m3"] == pytest.approx(
            r.outputs["volume_m3"] - r.outputs["vertical_prism_m3"])

    def test_shoring_permits_vertical_faces(self):
        r = batter_volume(length_m=10, base_width_m=2, depth_m=3.0, shored=True)
        assert r.outputs["batter_hv"] == 0.0
        assert r.outputs["safework_compliant"] is True
        assert any("shoring" in w for w in r.warnings)

    def test_threshold_is_exclusive(self):
        r = batter_volume(length_m=10, base_width_m=2,
                          depth_m=SAFEWORK_DEPTH_LIMIT_M, batter_hv=0.0)
        assert r.outputs["safework_compliant"] is True


class TestPadFooting:
    def test_basic_quantities(self):
        r = pad_footing(length_m=2.4, width_m=2.4, thickness_m=0.6, count=4)
        assert r.outputs["concrete_m3"] == pytest.approx(2.4 * 2.4 * 0.6 * 4)
        assert r.outputs["formwork_m2"] == pytest.approx(2 * (2.4 + 2.4) * 0.6 * 4)
        assert r.outputs["count"] == 4

    def test_blinding_projects_beyond_footing(self):
        r = pad_footing(length_m=2.0, width_m=2.0, thickness_m=0.5,
                        blinding_mm=50, blinding_projection_mm=100)
        assert r.outputs["blinding_area_m2"] == pytest.approx(2.2 * 2.2)
        assert r.outputs["blinding_m3"] == pytest.approx(2.2 * 2.2 * 0.05)

    def test_reo_mass_follows_rate(self):
        r = pad_footing(length_m=2, width_m=2, thickness_m=0.5, reo_rate_kg_per_m3=120)
        assert r.outputs["reo_kg"] == pytest.approx(2 * 2 * 0.5 * 120)

    def test_deep_excavation_warns(self):
        r = pad_footing(length_m=2, width_m=2, thickness_m=0.5, excavation_depth_m=2.2)
        assert any("SafeWork" in w for w in r.warnings)

    def test_invalid_count_rejected(self):
        with pytest.raises(ValueError, match="count must be at least 1"):
            pad_footing(length_m=2, width_m=2, thickness_m=0.5, count=0)


class TestRegistry:
    def test_all_tier1_tools_registered(self):
        ids = {t["id"] for t in REGISTRY.list()}
        assert {"trench.volume", "earthwork.bulking_shrinkage",
                "civil.batter_volume", "structural.pad_footing"} <= ids

    def test_run_captures_payload_for_replay(self):
        run = REGISTRY.run("trench.volume", 1, length_m=10, width_mm=600, depth_m=1.0)
        assert run.ok and run.tier == 1
        assert run.inputs["length_m"] == 10
        assert run.outputs["excavation_m3"] == pytest.approx(6.0)
        assert run.workings

    def test_failure_is_captured_not_raised(self):
        run = REGISTRY.run("trench.volume", 1, length_m=-1, width_mm=600, depth_m=1.0)
        assert run.ok is False
        assert "ValueError" in run.error

    def test_runs_are_deterministic(self):
        a = REGISTRY.run("civil.batter_volume", 1, length_m=12, base_width_m=3, depth_m=2.5)
        b = REGISTRY.run("civil.batter_volume", 1, length_m=12, base_width_m=3, depth_m=2.5)
        assert a.outputs == b.outputs
