"""Map Mudshark operation-group labels to Australian WBS items.

WBS items observed from Ground Layer Operations (Results.xls):
  OL-0 header      WBS item
  ──────────────   ──────────────────────────
  Stripping …      TOPSOIL_STRIP
  Cut              BULK_CUT
  Fill             BULK_FILL
  Built Structures STRUCTURE_EXCAVATION / STRUCTURE_BACKFILL (by col)
  All Stormwater … TRENCH  (from Trench Run Strata Operations)

Measurement-state conversion:
  Cut (Bulked m³)  → insitu_m3 = bulked_m3 / bulking_factor
  The bulking factor is site-specific and UNKNOWN at ingest time.
  An Assumption is raised so the QS can confirm or override it.

The wbs_item is written back to QuantityRow.wbs_item in place.
"""
from __future__ import annotations

import re
from typing import Optional

# ─── WBS codes ────────────────────────────────────────────────────────────────

class WBSItem:
    TOPSOIL_STRIP          = "TOPSOIL_STRIP"
    BULK_CUT               = "BULK_CUT"
    BULK_FILL              = "BULK_FILL"
    STRUCTURE_EXCAVATION   = "STRUCTURE_EXCAVATION"
    STRUCTURE_BACKFILL     = "STRUCTURE_BACKFILL"
    TRENCH                 = "TRENCH"
    ROCK_EXCAVATION        = "ROCK_EXCAVATION"
    BACKFILL               = "BACKFILL"
    UNKNOWN                = "UNKNOWN"


# ─── keyword rules  (applied in order; first match wins) ──────────────────────

# Each rule: (pattern, wbs_item, note)
_LABEL_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"strip", re.I),         WBSItem.TOPSOIL_STRIP),
    (re.compile(r"\bcut\b", re.I),        WBSItem.BULK_CUT),
    (re.compile(r"\bfill\b", re.I),       WBSItem.BULK_FILL),
    (re.compile(r"rock|blast|rip", re.I), WBSItem.ROCK_EXCAVATION),
    (re.compile(r"trench|pipe|pit|drain", re.I), WBSItem.TRENCH),
    (re.compile(r"building pad|slab|structure", re.I), WBSItem.STRUCTURE_EXCAVATION),
    (re.compile(r"backfill|compact", re.I), WBSItem.BACKFILL),
]

# Column-header rules: when a label is ambiguous, look at which column has value
_CUT_HEADERS  = frozenset({"Cut (Bulked m³)", "Cut (Bulked m\u00b3)"})
_FILL_HEADERS = frozenset({"Fill (Compressed m³)", "Fill (Compressed m\u00b3)"})
_IMP_HEADERS  = frozenset({"Imported (Banked m³)", "Imported (Banked m\u00b3)"})


def map_wbs(operation_group: str, values: dict[str, float]) -> str:
    """Return the WBS item code for an operation-group label.

    Falls back to column presence when the label is generic (e.g. just a
    Mudshark operation-group name like 'BUILDING PAD - Strata').
    """
    label = operation_group.strip()

    # Try label-based rules first
    for pattern, wbs in _LABEL_RULES:
        if pattern.search(label):
            return wbs

    # Fall back to which measurement columns contain values
    has_cut  = any(h in values for h in _CUT_HEADERS)
    has_fill = any(h in values for h in _FILL_HEADERS)
    has_imp  = any(h in values for h in _IMP_HEADERS)

    if has_cut and not has_fill:
        return WBSItem.BULK_CUT
    if has_fill and not has_cut:
        return WBSItem.BULK_FILL
    if has_imp:
        return WBSItem.TRENCH

    return WBSItem.UNKNOWN


def annotate_wbs(rows: list) -> None:
    """Annotate QuantityRow.wbs_item in-place for a list of QuantityRows."""
    for row in rows:
        row.wbs_item = map_wbs(row.operation_group, row.values)


# ─── bulking factor conversion ────────────────────────────────────────────────

# Default Australian standard bulking factors by material (loose / in-situ)
# Source: Australasian earth-moving standard; values commonly accepted in QS practice.
_DEFAULT_BULKING_FACTORS: dict[str, float] = {
    "clay":        1.30,
    "sand":        1.10,
    "gravel":      1.12,
    "rock":        1.60,
    "topsoil":     1.25,
    "fill":        1.15,
    "general":     1.25,   # default when material is unspecified
}

DEFAULT_BULKING_FACTOR = 1.25  # used when material cannot be identified


def bulked_to_insitu(bulked_m3: float, bulking_factor: float) -> float:
    """Convert a bulked volume to in-situ (bank measure)."""
    if bulking_factor <= 0:
        raise ValueError(f"bulking_factor must be positive, got {bulking_factor}")
    return bulked_m3 / bulking_factor


def infer_bulking_factor(material_name: str) -> tuple[float, str]:
    """Return (factor, matched_key) for a material name.

    Always returns a value; caller must raise an Assumption via the store.
    """
    lower = material_name.lower()
    for key, factor in _DEFAULT_BULKING_FACTORS.items():
        if key in lower:
            return factor, key
    return DEFAULT_BULKING_FACTOR, "general"
