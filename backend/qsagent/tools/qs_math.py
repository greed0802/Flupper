"""Tier 1 deterministic civil QS calculations.

All dimensions are in metres unless the argument name ends in `_mm`.
Every function returns its full working so the audit report can reproduce the
number by hand. Rounding is applied only at presentation; stored values keep
full float precision.
"""

from __future__ import annotations

import math
from typing import Optional

from .registry import REGISTRY, ToolResult

# SafeWork Australia / AS 2885 excavation rule: any excavation deeper than
# 1.5 m that a person may enter must be benched, battered or shored.
SAFEWORK_DEPTH_LIMIT_M = 1.5

# Conservative default batter slopes (horizontal : vertical) by soil class.
DEFAULT_BATTER_HV: dict[str, float] = {
    "rock": 0.0,
    "stiff_clay": 0.5,
    "clay": 0.75,
    "firm": 0.75,
    "loose_sand": 1.5,
    "sand": 1.0,
    "fill": 1.5,
    "unknown": 1.0,
}

# Typical Australian geotech bulking (in-situ -> loose) and shrinkage
# (in-situ -> compacted) factors.
BULKING_FACTORS: dict[str, float] = {
    "topsoil": 1.25, "sand": 1.12, "gravel": 1.15, "clay": 1.30,
    "silt": 1.25, "weathered_rock": 1.40, "rock": 1.55, "fill": 1.20,
}
SHRINKAGE_FACTORS: dict[str, float] = {
    "topsoil": 0.90, "sand": 0.95, "gravel": 0.93, "clay": 0.88,
    "silt": 0.90, "weathered_rock": 1.10, "rock": 1.30, "fill": 0.90,
}


def _positive(name: str, value: float) -> float:
    if value is None or value <= 0:
        raise ValueError(f"{name} must be greater than zero (got {value!r})")
    return float(value)


def _non_negative(name: str, value: float) -> float:
    if value is None or value < 0:
        raise ValueError(f"{name} must be zero or greater (got {value!r})")
    return float(value)


def _mm_to_m(value_mm: float) -> float:
    return float(value_mm) / 1000.0


# --------------------------------------------------------------------------
@REGISTRY.register(
    "trench.volume",
    tier=1,
    summary="Pipe trench excavation with bedding, haunch and pipe displacement deductions.",
    unit_map={"excavation_m3": "m3", "bedding_m3": "m3", "haunch_m3": "m3",
              "overlay_m3": "m3", "pipe_displacement_m3": "m3", "backfill_m3": "m3",
              "spoil_m3": "m3"},
)
def trench_volume(
    length_m: float,
    width_mm: float,
    depth_m: float,
    pipe_od_mm: float = 0.0,
    bedding_mm: float = 100.0,
    overlay_mm: float = 150.0,
    soil_class: str = "unknown",
    shored: bool = False,
    batter_hv: Optional[float] = None,
) -> ToolResult:
    """Trench quantities for a single pipe run.

    Zones, from invert up: bedding -> haunch/side fill (pipe zone) ->
    overlay -> general backfill to surface.
    """
    L = _positive("length_m", length_m)
    W = _mm_to_m(_positive("width_mm", width_mm))
    D = _positive("depth_m", depth_m)
    od = _mm_to_m(_non_negative("pipe_od_mm", pipe_od_mm))
    bed = _mm_to_m(_non_negative("bedding_mm", bedding_mm))
    over = _mm_to_m(_non_negative("overlay_mm", overlay_mm))

    workings: list[str] = []
    warnings: list[str] = []

    if od >= W:
        raise ValueError(
            f"pipe OD {pipe_od_mm} mm does not fit in a {width_mm} mm trench")
    if bed + od + over > D + 1e-9:
        raise ValueError(
            "bedding + pipe OD + overlay exceeds trench depth "
            f"({bed + od + over:.3f} m > {D:.3f} m)")

    # --- excavation, battered if required -------------------------------
    hv = 0.0
    if D > SAFEWORK_DEPTH_LIMIT_M and not shored:
        hv = DEFAULT_BATTER_HV.get(soil_class, DEFAULT_BATTER_HV["unknown"]) \
            if batter_hv is None else float(batter_hv)
        warnings.append(
            f"Depth {D:.2f} m exceeds the SafeWork Australia {SAFEWORK_DEPTH_LIMIT_M} m "
            f"limit: battering at {hv}H:1V applied (soil class '{soil_class}'). "
            "Confirm shoring/benching method with the site geotech report.")
    elif D > SAFEWORK_DEPTH_LIMIT_M and shored:
        warnings.append(
            f"Depth {D:.2f} m exceeds {SAFEWORK_DEPTH_LIMIT_M} m: priced as shored "
            "(vertical sides). Shoring/trench shield cost must be included separately.")

    # Trapezoidal cross-section: A = D * (W + hv*D); vertical when hv = 0.
    area = D * (W + hv * D)
    excavation = area * L
    workings.append(
        f"Excavation = D x (W + {hv}H:1V x D) x L = {D:.3f} x ({W:.3f} + {hv} x "
        f"{D:.3f}) x {L:.3f} = {excavation:.3f} m3")

    # --- bedding, haunch, overlay (measured at nominal trench width) -----
    bedding = W * bed * L
    workings.append(f"Bedding = W x t x L = {W:.3f} x {bed:.3f} x {L:.3f} = {bedding:.3f} m3")

    pipe_disp = math.pi / 4.0 * od**2 * L
    haunch = max(W * od * L - pipe_disp, 0.0)
    if od > 0:
        workings.append(
            f"Pipe displacement = pi/4 x OD^2 x L = 0.7854 x {od:.3f}^2 x {L:.3f} "
            f"= {pipe_disp:.3f} m3")
        workings.append(
            f"Haunch/side fill = (W x OD x L) - pipe = ({W:.3f} x {od:.3f} x {L:.3f}) "
            f"- {pipe_disp:.3f} = {haunch:.3f} m3")

    overlay = W * over * L
    if over > 0:
        workings.append(
            f"Overlay = W x t x L = {W:.3f} x {over:.3f} x {L:.3f} = {overlay:.3f} m3")

    # --- general backfill: everything excavated less the bedding zones ---
    backfill = excavation - bedding - haunch - overlay - pipe_disp
    backfill = max(backfill, 0.0)
    workings.append(
        f"General backfill = excavation - bedding - haunch - overlay - pipe = "
        f"{excavation:.3f} - {bedding:.3f} - {haunch:.3f} - {overlay:.3f} - "
        f"{pipe_disp:.3f} = {backfill:.3f} m3")

    # Spoil is everything dug out, less what is put back (in-situ measure).
    spoil = excavation - backfill
    workings.append(
        f"Spoil to cart away (in-situ) = {excavation:.3f} - {backfill:.3f} = {spoil:.3f} m3")

    return ToolResult(
        outputs={
            "excavation_m3": excavation,
            "bedding_m3": bedding,
            "haunch_m3": haunch,
            "overlay_m3": overlay,
            "pipe_displacement_m3": pipe_disp,
            "backfill_m3": backfill,
            "spoil_m3": spoil,
            "batter_hv": hv,
            "trench_top_width_m": W + 2 * hv * D,
            "safework_compliant": bool(shored or hv > 0 or D <= SAFEWORK_DEPTH_LIMIT_M),
        },
        workings=workings,
        warnings=warnings,
    )


# --------------------------------------------------------------------------
@REGISTRY.register(
    "earthwork.bulking_shrinkage",
    tier=1,
    summary="Converts in-situ volume to loose (truck) and compacted (placed) volumes.",
    unit_map={"insitu_m3": "m3", "loose_m3": "m3", "compacted_m3": "m3",
              "mass_t": "t", "truck_loads": "ea"},
)
def bulking_shrinkage(
    insitu_m3: float,
    material: str = "clay",
    bulking_factor: Optional[float] = None,
    shrinkage_factor: Optional[float] = None,
    density_t_per_m3: float = 1.8,
    truck_capacity_m3: float = 10.0,
) -> ToolResult:
    v = _non_negative("insitu_m3", insitu_m3)
    key = material.strip().lower().replace(" ", "_")
    warnings: list[str] = []

    bf = bulking_factor if bulking_factor is not None else BULKING_FACTORS.get(key)
    sf = shrinkage_factor if shrinkage_factor is not None else SHRINKAGE_FACTORS.get(key)
    if bf is None:
        bf = BULKING_FACTORS["fill"]
        warnings.append(
            f"No bulking factor on record for material '{material}'; used default "
            f"{bf} (fill). Confirm against the geotechnical report.")
    if sf is None:
        sf = SHRINKAGE_FACTORS["fill"]
        warnings.append(
            f"No shrinkage factor on record for material '{material}'; used default {sf}.")
    if bf < 1.0:
        warnings.append(f"Bulking factor {bf} is below 1.0 - material would compact on "
                        "excavation, which is unusual. Verify the input.")

    loose = v * float(bf)
    compacted = v * float(sf)
    mass = loose * _positive("density_t_per_m3", density_t_per_m3) / float(bf)
    loads = math.ceil(loose / _positive("truck_capacity_m3", truck_capacity_m3)) if loose else 0

    workings = [
        f"Loose (truck) volume = in-situ x bulking = {v:.3f} x {bf} = {loose:.3f} m3",
        f"Compacted (placed) volume = in-situ x shrinkage = {v:.3f} x {sf} = {compacted:.3f} m3",
        f"Mass = in-situ x density = {v:.3f} x {density_t_per_m3} = {mass:.3f} t",
        f"Truck loads = ceil({loose:.3f} / {truck_capacity_m3}) = {loads}",
    ]
    return ToolResult(
        outputs={
            "insitu_m3": v, "loose_m3": loose, "compacted_m3": compacted,
            "mass_t": mass, "truck_loads": loads,
            "bulking_factor": float(bf), "shrinkage_factor": float(sf),
        },
        workings=workings, warnings=warnings,
    )


# --------------------------------------------------------------------------
@REGISTRY.register(
    "civil.batter_volume",
    tier=1,
    summary="Battered bulk excavation volume with SafeWork Australia >1.5 m compliance check.",
    unit_map={"volume_m3": "m3", "batter_face_area_m2": "m2", "extra_over_m3": "m3"},
)
def batter_volume(
    length_m: float,
    base_width_m: float,
    depth_m: float,
    soil_class: str = "unknown",
    batter_hv: Optional[float] = None,
    shored: bool = False,
    benched: bool = False,
) -> ToolResult:
    L = _positive("length_m", length_m)
    B = _positive("base_width_m", base_width_m)
    D = _positive("depth_m", depth_m)
    warnings: list[str] = []
    workings: list[str] = []

    requires_control = D > SAFEWORK_DEPTH_LIMIT_M
    hv = float(batter_hv) if batter_hv is not None else (
        0.0 if (shored or benched) else DEFAULT_BATTER_HV.get(
            soil_class.strip().lower(), DEFAULT_BATTER_HV["unknown"]))

    if requires_control:
        if shored:
            warnings.append(
                f"Depth {D:.2f} m > {SAFEWORK_DEPTH_LIMIT_M} m: priced with shoring, "
                "vertical faces. Shoring design and installation to be priced separately.")
        elif benched:
            warnings.append(
                f"Depth {D:.2f} m > {SAFEWORK_DEPTH_LIMIT_M} m: priced as benched. "
                "Bench widths must comply with the geotechnical recommendation.")
        elif hv <= 0:
            raise ValueError(
                f"SafeWork Australia breach: excavation depth {D:.2f} m exceeds "
                f"{SAFEWORK_DEPTH_LIMIT_M} m with vertical unsupported faces. "
                "Provide batter_hv, shored=True or benched=True.")
        else:
            warnings.append(
                f"Depth {D:.2f} m > {SAFEWORK_DEPTH_LIMIT_M} m: battered at {hv}H:1V "
                f"for soil class '{soil_class}' per SafeWork Australia excavation code.")

    top_width = B + 2 * hv * D
    volume = D * (B + hv * D) * L
    prism = B * D * L
    workings.append(f"Top width = B + 2 x {hv} x D = {B:.3f} + 2 x {hv} x {D:.3f} = "
                    f"{top_width:.3f} m")
    workings.append(f"Volume = D x (B + {hv} x D) x L = {D:.3f} x ({B:.3f} + {hv} x "
                    f"{D:.3f}) x {L:.3f} = {volume:.3f} m3")
    workings.append(f"Extra-over vertical prism = {volume:.3f} - {prism:.3f} = "
                    f"{volume - prism:.3f} m3")

    slope_len = D * math.sqrt(1 + hv**2)
    face_area = 2 * slope_len * L
    workings.append(f"Batter face area = 2 x D x sqrt(1 + {hv}^2) x L = {face_area:.3f} m2")

    return ToolResult(
        outputs={
            "volume_m3": volume, "vertical_prism_m3": prism,
            "extra_over_m3": volume - prism, "batter_face_area_m2": face_area,
            "top_width_m": top_width, "batter_hv": hv,
            "safework_compliant": bool(not requires_control or shored or benched or hv > 0),
        },
        workings=workings, warnings=warnings,
    )


# --------------------------------------------------------------------------
@REGISTRY.register(
    "structural.pad_footing",
    tier=1,
    summary="Pad footing concrete, formwork, blinding, excavation and reinforcement mass.",
    unit_map={"concrete_m3": "m3", "formwork_m2": "m2", "blinding_m3": "m3",
              "excavation_m3": "m3", "reo_kg": "kg"},
)
def pad_footing(
    length_m: float,
    width_m: float,
    thickness_m: float,
    count: int = 1,
    blinding_mm: float = 50.0,
    blinding_projection_mm: float = 100.0,
    excavation_depth_m: Optional[float] = None,
    working_space_mm: float = 300.0,
    reo_rate_kg_per_m3: float = 110.0,
) -> ToolResult:
    L = _positive("length_m", length_m)
    W = _positive("width_m", width_m)
    T = _positive("thickness_m", thickness_m)
    n = int(count)
    if n < 1:
        raise ValueError("count must be at least 1")
    blind_t = _mm_to_m(_non_negative("blinding_mm", blinding_mm))
    proj = _mm_to_m(_non_negative("blinding_projection_mm", blinding_projection_mm))
    ws = _mm_to_m(_non_negative("working_space_mm", working_space_mm))

    warnings: list[str] = []
    concrete = L * W * T * n
    formwork = 2 * (L + W) * T * n
    blinding_area = (L + 2 * proj) * (W + 2 * proj) * n
    blinding = blinding_area * blind_t
    reo = concrete * _non_negative("reo_rate_kg_per_m3", reo_rate_kg_per_m3)

    exc_depth = float(excavation_depth_m) if excavation_depth_m is not None else T + blind_t
    exc = (L + 2 * ws) * (W + 2 * ws) * exc_depth * n
    if exc_depth > SAFEWORK_DEPTH_LIMIT_M:
        warnings.append(
            f"Footing excavation depth {exc_depth:.2f} m exceeds the SafeWork Australia "
            f"{SAFEWORK_DEPTH_LIMIT_M} m limit - battering or shoring is required; "
            "run civil.batter_volume for the compliant excavation quantity.")

    workings = [
        f"Concrete = L x W x T x n = {L:.3f} x {W:.3f} x {T:.3f} x {n} = {concrete:.3f} m3",
        f"Formwork = 2 x (L + W) x T x n = 2 x ({L:.3f} + {W:.3f}) x {T:.3f} x {n} "
        f"= {formwork:.3f} m2",
        f"Blinding = (L+2p) x (W+2p) x t x n = ({L + 2 * proj:.3f} x {W + 2 * proj:.3f}) "
        f"x {blind_t:.3f} x {n} = {blinding:.3f} m3",
        f"Excavation = (L+2ws) x (W+2ws) x depth x n = {L + 2 * ws:.3f} x "
        f"{W + 2 * ws:.3f} x {exc_depth:.3f} x {n} = {exc:.3f} m3",
        f"Reinforcement = concrete x rate = {concrete:.3f} x {reo_rate_kg_per_m3} "
        f"= {reo:.3f} kg",
    ]
    return ToolResult(
        outputs={
            "concrete_m3": concrete, "formwork_m2": formwork,
            "blinding_m3": blinding, "blinding_area_m2": blinding_area,
            "excavation_m3": exc, "excavation_depth_m": exc_depth, "reo_kg": reo,
            "count": n,
        },
        workings=workings, warnings=warnings,
    )
