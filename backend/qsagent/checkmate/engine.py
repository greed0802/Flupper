"""QS CheckMate - the pre-response verification gate.

Nothing reaches the user until it clears this gate. CheckMate re-derives the
arithmetic *independently* of whatever the model said, checks dimensional
integrity, enforces the SafeWork Australia 1.5 m excavation rule, and rejects
any quantity claim that is not anchored to evidence.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Optional, Sequence

from ..contracts.evidence import QuantityClaim, ToolRun, Unit
from ..tools.qs_math import SAFEWORK_DEPTH_LIMIT_M
from ..tools.registry import REGISTRY

# Relative tolerance for independent arithmetic re-derivation.
TOLERANCE = 1e-6


class Severity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class Finding:
    rule: str
    severity: Severity
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "severity": self.severity.value,
                "message": self.message, "detail": self.detail}


@dataclass
class CheckMateReport:
    subject: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.FAIL]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.WARN]

    @property
    def passed(self) -> bool:
        return not self.failures

    def badge(self) -> str:
        if self.failures:
            return "REJECTED"
        return "VERIFIED (with notes)" if self.warnings else "VERIFIED"

    def as_dict(self) -> dict[str, Any]:
        return {"subject": self.subject, "passed": self.passed, "badge": self.badge(),
                "findings": [f.as_dict() for f in self.findings]}


# --------------------------------------------------------------------------
# Unit algebra
# --------------------------------------------------------------------------
_UNIT_DIM: dict[str, tuple[int, int, int]] = {
    # (length, mass, time)
    "m": (1, 0, 0), "mm": (1, 0, 0), "m2": (2, 0, 0), "m3": (3, 0, 0),
    "kg": (0, 1, 0), "t": (0, 1, 0), "hr": (0, 0, 1),
    "ea": (0, 0, 0), "item": (0, 0, 0), "L": (3, 0, 0),
}
_UNIT_SCALE: dict[str, float] = {"m": 1.0, "mm": 0.001, "m2": 1.0, "m3": 1.0,
                                 "kg": 1.0, "t": 1000.0, "L": 0.001}


def dimension_of(unit: str) -> tuple[int, int, int]:
    if unit not in _UNIT_DIM:
        raise ValueError(f"unknown unit: {unit}")
    return _UNIT_DIM[unit]


def check_unit_product(units: Sequence[str], expected: str) -> Optional[Finding]:
    """Catch classic errors like m2 x mm being reported as m3 without conversion."""
    try:
        dims = [dimension_of(u) for u in units]
        exp = dimension_of(expected)
    except ValueError as exc:
        return Finding("unit.unknown", Severity.FAIL, str(exc))
    total = tuple(sum(d[i] for d in dims) for i in range(3))
    if total != exp:
        return Finding(
            "unit.dimension", Severity.FAIL,
            f"Dimensional mismatch: {' x '.join(units)} gives exponents {total}, "
            f"but the result is declared as '{expected}' {exp}.",
            {"operands": list(units), "declared": expected})
    if "mm" in units and expected in {"m", "m2", "m3"}:
        return Finding(
            "unit.mixed_scale", Severity.WARN,
            f"Mixed length scales: {' x '.join(units)} -> '{expected}'. Confirm the "
            "millimetre terms were divided by 1000 before multiplying.",
            {"operands": list(units)})
    return None


# --------------------------------------------------------------------------
class CheckMate:
    """The rules engine. Each `check_*` method contributes findings."""

    def __init__(self, tolerance: float = TOLERANCE) -> None:
        self.tolerance = tolerance

    # ------------------------------------------------ independent arithmetic
    def check_run_replay(self, run: ToolRun) -> list[Finding]:
        """Re-execute a Tier 1 tool from its stored inputs and compare outputs.

        This is the heart of CheckMate: the number shown to the user must be
        reproducible from the recorded inputs alone.
        """
        findings: list[Finding] = []
        if run.tier != 1:
            findings.append(Finding(
                "arith.replay", Severity.INFO,
                f"Tool '{run.tool_id}' is Tier {run.tier}; deterministic replay is only "
                "guaranteed for Tier 1 tools."))
            return findings
        try:
            replay = REGISTRY.run(run.tool_id, run.project_id, **run.inputs)
        except KeyError as exc:
            return [Finding("arith.replay", Severity.FAIL, str(exc))]
        if not replay.ok:
            return [Finding("arith.replay", Severity.FAIL,
                            f"Replay of '{run.tool_id}' failed: {replay.error}")]
        for key, original in run.outputs.items():
            if not isinstance(original, (int, float)) or isinstance(original, bool):
                continue
            recomputed = replay.outputs.get(key)
            if recomputed is None:
                findings.append(Finding(
                    "arith.replay", Severity.FAIL,
                    f"Output '{key}' is not produced by a replay of '{run.tool_id}'.",
                    {"key": key}))
                continue
            if not math.isclose(float(original), float(recomputed),
                                rel_tol=self.tolerance, abs_tol=self.tolerance):
                findings.append(Finding(
                    "arith.replay", Severity.FAIL,
                    f"Arithmetic mismatch on '{key}': reported {original}, "
                    f"independently recomputed {recomputed}.",
                    {"key": key, "reported": original, "recomputed": recomputed}))
        if not findings:
            findings.append(Finding(
                "arith.replay", Severity.INFO,
                f"All {len(run.outputs)} outputs of '{run.tool_id}' reproduced exactly "
                "from stored inputs."))
        return findings

    def check_arithmetic(self, expected: float, operands: Sequence[float],
                         op: str = "mul", label: str = "value") -> list[Finding]:
        """Independent re-derivation of a single stated figure."""
        if op == "mul":
            actual = math.prod(operands)
        elif op == "sum":
            actual = float(sum(operands))
        else:
            return [Finding("arith.op", Severity.FAIL, f"unsupported operation '{op}'")]
        if not math.isclose(expected, actual, rel_tol=self.tolerance, abs_tol=self.tolerance):
            return [Finding(
                "arith.independent", Severity.FAIL,
                f"{label}: stated {expected:g} but {op} of {list(operands)} = {actual:g}.",
                {"stated": expected, "recomputed": actual})]
        return [Finding("arith.independent", Severity.INFO,
                        f"{label}: {expected:g} verified independently.")]

    # ----------------------------------------------------------- unit checks
    def check_units(self, run: ToolRun) -> list[Finding]:
        findings: list[Finding] = []
        try:
            tool = REGISTRY.get(run.tool_id)
        except KeyError:
            return [Finding("unit.tool", Severity.WARN,
                            f"No unit map registered for '{run.tool_id}'.")]
        for key, unit in tool.unit_map.items():
            if key not in run.outputs:
                continue
            try:
                dimension_of(unit)
            except ValueError as exc:
                findings.append(Finding("unit.unknown", Severity.FAIL, str(exc)))
                continue
            value = run.outputs[key]
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0:
                findings.append(Finding(
                    "unit.negative", Severity.FAIL,
                    f"Output '{key}' is negative ({value:g} {unit}); a physical quantity "
                    "cannot be negative.", {"key": key, "value": value}))
        # Any input named *_mm must be plausibly a millimetre figure.
        for key, value in run.inputs.items():
            if key.endswith("_mm") and isinstance(value, (int, float)) and 0 < value < 10:
                findings.append(Finding(
                    "unit.suspect_scale", Severity.WARN,
                    f"Input '{key}' = {value} looks like metres supplied to a millimetre "
                    "parameter.", {"key": key, "value": value}))
            if key.endswith("_m") and isinstance(value, (int, float)) and value > 500:
                findings.append(Finding(
                    "unit.suspect_scale", Severity.WARN,
                    f"Input '{key}' = {value} looks like millimetres supplied to a metre "
                    "parameter.", {"key": key, "value": value}))
        if not findings:
            findings.append(Finding("unit.integrity", Severity.INFO,
                                    "Unit integrity checks passed."))
        return findings

    # -------------------------------------------------------- safety checks
    def check_safety(self, run: ToolRun) -> list[Finding]:
        """SafeWork Australia excavation rule: >1.5 m requires batter/bench/shore."""
        findings: list[Finding] = []
        depth = None
        for key in ("depth_m", "excavation_depth_m"):
            if isinstance(run.inputs.get(key), (int, float)):
                depth = float(run.inputs[key])
                break
        if depth is None and isinstance(run.outputs.get("excavation_depth_m"), (int, float)):
            depth = float(run.outputs["excavation_depth_m"])
        if depth is None:
            return findings
        if depth <= SAFEWORK_DEPTH_LIMIT_M:
            findings.append(Finding(
                "safety.safework_1500", Severity.INFO,
                f"Excavation depth {depth:.2f} m is within the {SAFEWORK_DEPTH_LIMIT_M} m "
                "SafeWork Australia threshold."))
            return findings
        compliant = run.outputs.get("safework_compliant")
        shored = bool(run.inputs.get("shored") or run.inputs.get("benched"))
        hv = float(run.outputs.get("batter_hv") or run.inputs.get("batter_hv") or 0.0)
        if compliant is False or (not shored and hv <= 0):
            findings.append(Finding(
                "safety.safework_1500", Severity.FAIL,
                f"Excavation depth {depth:.2f} m exceeds {SAFEWORK_DEPTH_LIMIT_M} m with "
                "no battering, benching or shoring allowed for.",
                {"depth_m": depth}))
        else:
            method = "shoring/benching" if shored else f"battering at {hv}H:1V"
            findings.append(Finding(
                "safety.safework_1500", Severity.WARN,
                f"Excavation depth {depth:.2f} m exceeds {SAFEWORK_DEPTH_LIMIT_M} m; "
                f"{method} allowed for. Confirm against the geotechnical report.",
                {"depth_m": depth, "batter_hv": hv}))
        return findings

    # ---------------------------------------------- measurement-state checks
    # Conversion-method patterns: if a ToolRun's tool_id matches any of these,
    # it is a volume-state conversion that REQUIRES a resolved measurement_state
    # on every input claim.
    _CONVERSION_METHOD_PATTERNS: tuple[str, ...] = (
        "bulked_to_insitu",
        "cut_bulked_to_insitu",
        "_to_insitu",
        "volume_conversion",
        "state_convert",
    )

    @staticmethod
    def _is_conversion_method(method: str) -> bool:
        return any(p in method for p in CheckMate._CONVERSION_METHOD_PATTERNS)

    def check_measurement_state(self, claim: QuantityClaim) -> list[Finding]:
        """Gate on unknown measurement state.

        Two severity levels:
          WARN — measurement_state is None (not tracked) or is 'UNRESOLVED'.
                 The quantity is usable for counting but must not pass through
                 a volume-state conversion without first being resolved.
          FAIL — the claim's own method is a conversion method AND the state
                 is still UNRESOLVED or None, meaning the conversion was applied
                 (or skipped) without confirming what state the input was in.
                 This is the exact scenario that produces a silent 23% error.

        Discriminating evidence for BF=1.0 / state-unified projects
        ────────────────────────────────────────────────────────────
        The correct test is cross-column identity on material that appears on
        both the cut and fill sides within one ingest run:

            Reused   (Bulked m³)     = X
            From Site (Compressed m³) = X    ← same value, different labelled state

        If X is identical to 4+ significant figures, the only consistent
        interpretation is BF = SF = 1.0 (or all columns emitted in one unified
        state). At any realistic factor (BF=1.25 → 0.80x, BF=1.30 → 0.770x)
        the values would differ by 20–30%. This identity holds for the the reference project project: Reused = From Site = 336.266 m³ exactly.
        """
        state = claim.measurement_state
        findings: list[Finding] = []

        if state is None:
            findings.append(Finding(
                "quantity.measurement_state", Severity.WARN,
                f"Claim '{claim.description}' has no measurement_state. "
                "Cannot confirm whether value has been converted correctly. "
                "Tag with 'bulked', 'banked', 'compressed', 'm3_insitu', or "
                "'UNRESOLVED' so downstream conversion checks can run.",
                {"description": claim.description, "value": claim.quantity.value,
                 "unit": claim.quantity.unit.value},
            ))
        elif state == "UNRESOLVED":
            findings.append(Finding(
                "quantity.measurement_state", Severity.WARN,
                f"Claim '{claim.description}' has measurement_state=UNRESOLVED. "
                "Project BF/SF have not been confirmed from the BBX settings file. "
                "Value is stored raw from the Mudshark column; no factor has been "
                "applied. Do not use in volume-state conversions until resolved.",
                {"description": claim.description, "value": claim.quantity.value,
                 "unit": claim.quantity.unit.value, "measurement_state": state},
            ))

        # FAIL: conversion method used without a resolved state
        if self._is_conversion_method(claim.method) and state in (None, "UNRESOLVED"):
            findings.append(Finding(
                "quantity.measurement_state_conversion", Severity.FAIL,
                f"Claim '{claim.description}' was produced by conversion method "
                f"'{claim.method}' but measurement_state={state!r}. "
                "A volume-state conversion without a confirmed input state "
                "silently propagates the wrong figure. Resolve BF/SF from the "
                "BBX project settings before applying any conversion.",
                {"method": claim.method, "measurement_state": state,
                 "value": claim.quantity.value},
            ))

        return findings

    def check_claims_measurement_state(
        self, claims: Sequence[QuantityClaim]
    ) -> list[Finding]:
        """Run measurement_state check across all claims; aggregate findings."""
        findings: list[Finding] = []
        for claim in claims:
            findings.extend(self.check_measurement_state(claim))
        return findings

    # ------------------------------------------------------ grounding trace
    def check_grounding(self, claim: QuantityClaim) -> list[Finding]:
        findings: list[Finding] = []
        if not claim.evidence:
            return [Finding("grounding.missing", Severity.FAIL,
                            f"Claim '{claim.description}' has no evidence node attached.")]
        for i, ev in enumerate(claim.evidence):
            if ev.drawing_no is None and ev.raw_text is None:
                findings.append(Finding(
                    "grounding.weak", Severity.WARN,
                    f"Evidence #{i + 1} for '{claim.description}' has neither a drawing "
                    "number nor quoted source text.", {"file": ev.file_name}))
            if ev.revision is None and ev.drawing_no:
                findings.append(Finding(
                    "grounding.revision", Severity.WARN,
                    f"Drawing {ev.drawing_no} is cited without a revision - a superseded "
                    "revision may be in use.", {"drawing_no": ev.drawing_no}))
        if not claim.workings:
            findings.append(Finding(
                "grounding.workings", Severity.WARN,
                f"Claim '{claim.description}' carries no calculation workings."))
        if all(f.severity is Severity.INFO for f in findings) or not findings:
            findings.append(Finding(
                "grounding.trace", Severity.INFO,
                f"Claim traced to {len(claim.evidence)} evidence node(s): "
                f"{'; '.join(claim.citations())}"))
        return findings

    def check_text_numbers(self, text: str, allowed: Iterable[float]) -> list[Finding]:
        """Reject model prose that contains numbers no tool produced."""
        allowed_set = [float(a) for a in allowed]
        findings: list[Finding] = []
        for match in re.finditer(r"(?<![\w.])(\d[\d,]*\.?\d*)\s*(m3|m2|m\b|kg|t\b)", text):
            value = float(match.group(1).replace(",", ""))
            if not any(math.isclose(value, a, rel_tol=1e-3, abs_tol=0.01)
                       for a in allowed_set):
                findings.append(Finding(
                    "grounding.hallucinated_number", Severity.FAIL,
                    f"Response states '{match.group(0)}' which no tool run produced.",
                    {"value": value, "unit": match.group(2)}))
        if not findings:
            findings.append(Finding("grounding.hallucinated_number", Severity.INFO,
                                    "All quantities in the response text trace to tool output."))
        return findings

    # ----------------------------------------------------------------- gate
    def verify_run(self, run: ToolRun, claims: Sequence[QuantityClaim] = ()) -> CheckMateReport:
        report = CheckMateReport(subject=run.tool_id)
        if not run.ok:
            report.findings.append(Finding(
                "run.failed", Severity.FAIL, f"Tool run failed: {run.error}"))
            return report
        report.findings += self.check_run_replay(run)
        report.findings += self.check_units(run)
        report.findings += self.check_safety(run)
        for claim in claims:
            report.findings += self.check_grounding(claim)
            report.findings += self.check_measurement_state(claim)
        for warning in run.warnings:
            report.findings.append(Finding("tool.warning", Severity.WARN, warning))
        return report

    def verify_claim(self, claim: QuantityClaim) -> CheckMateReport:
        report = CheckMateReport(subject=claim.description)
        report.findings += self.check_grounding(claim)
        report.findings += self.check_measurement_state(claim)
        if claim.quantity.value < 0:
            report.findings.append(Finding(
                "unit.negative", Severity.FAIL,
                f"Quantity {claim.quantity} is negative."))
        return report


__all__ = ["CheckMate", "CheckMateReport", "Finding", "Severity",
           "check_unit_product", "dimension_of", "Unit"]
