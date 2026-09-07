"""Read Mudshark Excel exports into typed rows.

Supports Layout A (loose directory) and Layout B (zip archive).

Sheet ingest decisions:
  INGEST:   Ground Layer Operations, Structure Strata Operations,
            Trench Run Strata Operations, Trenches, Trench Depth Categories,
            Areas and Perimeters, Structure Thickness Breakdown
  SKIP+XC:  All Strata Operations, All Materials (union sheets; cross-check only)
  PAYLOAD:  Ground Layer Materials, Structure Materials, Trench Run Materials

Outline levels on volume sheets: {0, 1, 2}
  OL=0 – category header / grand total  -> SKIP, derive by summing OL-1
  OL=1 – operation group leaf quantity  -> INGEST as QuantityRow
  OL=2 – material breakdown             -> MATERIAL PAYLOAD on parent OL-1

xlrd cannot detect formulas; totals identified structurally only.
"""
from __future__ import annotations

import hashlib
import io
import logging
import warnings
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

log = logging.getLogger(__name__)

MeasurementState = Literal["bulked", "compressed", "banked", "linear_m", "m2", "m3_insitu"]

# Column semantic map: (header fragment, measurement_state)
_COL_SEMANTICS: list[tuple[str, MeasurementState]] = [
    ("Exported (Bulked",      "bulked"),
    ("Reused (Bulked",        "bulked"),
    ("Cut (Bulked",           "bulked"),
    ("From Site (Compressed", "compressed"),
    ("Imported (Banked",      "banked"),
    ("Fill (Compressed",      "compressed"),
    ("Site Balance (Bulked",  "bulked"),
]

_SKIP_SHEETS = frozenset({"All Strata Operations", "All Materials"})
_VOLUME_SHEETS = frozenset({
    "Ground Layer Operations",
    "Structure Strata Operations",
    "Trench Run Strata Operations",
})
_LINEAR_SHEETS  = frozenset({"Trenches", "Trench Depth Categories"})
_AREA_SHEETS    = frozenset({"Areas and Perimeters"})
_MATERIAL_SHEETS = frozenset({
    "Ground Layer Materials",
    "Structure Materials",
    "Trench Run Materials",
})

_XCHECK_TOL = 0.005   # 0.5%


# ─── data classes ─────────────────────────────────────────────────────────────

@dataclass
class MaterialRow:
    """OL-2 material breakdown – payload on a QuantityRow, never a sibling."""
    name: str
    values: dict[str, float]   # header -> value in original measurement state


@dataclass
class QuantityRow:
    """OL-1 operation-group leaf from a volume sheet."""
    sheet: str
    row_index: int
    operation_group: str
    outline_level: int
    values: dict[str, float]   # header -> value
    materials: list[MaterialRow] = field(default_factory=list)
    wbs_item: Optional[str] = None


@dataclass
class LinearRow:
    """Leaf row from Trenches or Trench Depth Categories."""
    sheet: str
    row_index: int
    label: str
    outline_level: int
    quantity_m: float
    avg_depth_m: Optional[float]
    depth_category_code: Optional[str] = None
    parent_network: Optional[str] = None
    parent_run: Optional[str] = None


@dataclass
class AreaRow:
    row_index: int
    name: str
    true_area_m2: Optional[float]
    top_down_area_m2: Optional[float]
    outside_perimeter_m: Optional[float]
    all_edges_m: Optional[float]
    total_thickness_m: Optional[float]


@dataclass
class CrossCheckResult:
    component_total: float
    union_total: float
    abs_diff: float
    rel_diff: float
    passed: bool
    message: str


class ParseError(Exception):
    def __init__(self, file_name: str, sheet: str, row: int, reason: str) -> None:
        self.file_name = file_name
        self.sheet = sheet
        self.row = row
        self.reason = reason
        super().__init__(f"ParseError in {file_name!r} sheet={sheet!r} row={row}: {reason}")


@dataclass
class MudsharkWorkbook:
    file_name: str
    file_hash: str
    quantity_rows: list[QuantityRow] = field(default_factory=list)
    linear_rows: list[LinearRow] = field(default_factory=list)
    area_rows: list[AreaRow] = field(default_factory=list)
    cross_checks: dict[str, CrossCheckResult] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ─── internal helpers ─────────────────────────────────────────────────────────

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _map_headers(header_values: list) -> dict[int, tuple[str, Optional[MeasurementState]]]:
    """Map column index -> (header_text, measurement_state | None)."""
    result: dict[int, tuple[str, Optional[MeasurementState]]] = {}
    for i, h in enumerate(header_values):
        h = str(h).strip() if h else ""
        state: Optional[MeasurementState] = None
        for fragment, ms in _COL_SEMANTICS:
            if fragment.lower() in h.lower():
                state = ms
                break
        result[i] = (h, state)
    return result


def _ol_from_rowinfo(ws: Any, row_idx: int, has_format: bool) -> int:
    if not has_format:
        return 0
    ri = ws.rowinfo_map.get(row_idx)
    return ri.outline_level if ri else 0


def _row_has_any_number(ws: Any, row_idx: int, col_indices: list[int]) -> bool:
    return any(ws.cell_type(row_idx, c) == 2 for c in col_indices if c < ws.ncols)


def _read_float(ws: Any, row_idx: int, col_idx: int, fname: str, sheet: str) -> Optional[float]:
    if col_idx >= ws.ncols:
        return None
    ct = ws.cell_type(row_idx, col_idx)
    if ct == 2:
        return float(ws.cell_value(row_idx, col_idx))
    if ct in (0, 6):
        return None
    if ct == 1:
        val = ws.cell_value(row_idx, col_idx)
        raise ParseError(fname, sheet, row_idx,
                         f"col {col_idx}: expected NUMBER, got TEXT={val!r}")
    return None


def _xcheck(name: str, comp: float, union: float) -> CrossCheckResult:
    diff = abs(comp - union)
    rel = diff / max(abs(union), 1e-9)
    passed = rel <= _XCHECK_TOL
    msg = (f"{'PASS' if passed else 'WARN'}: {name} "
           f"components={comp:.3f} union={union:.3f} diff={rel:.4%}")
    return CrossCheckResult(comp, union, diff, rel, passed, msg)




# ─── sheet parsers ────────────────────────────────────────────────────────────

def _parse_volume_sheet(ws, sheet_name, file_name, has_format):
    if ws.nrows < 2:
        return [], 0.0
    headers = _map_headers(ws.row_values(0))
    numeric_cols = [c for c, (_, s) in headers.items() if s is not None]
    if not numeric_cols:
        raise ParseError(file_name, sheet_name, 0, "No recognised measurement columns")
    rows, cut_total, current_ol1 = [], 0.0, None
    for r in range(1, ws.nrows):
        ol = _ol_from_rowinfo(ws, r, has_format)
        label = str(ws.cell_value(r, 0)).strip() if ws.cell_type(r, 0) == 1 else ""
        if not _row_has_any_number(ws, r, numeric_cols):
            if ol == 1:
                current_ol1 = None
            continue
        if ol == 0:
            for c, (h, s) in headers.items():
                if s == "bulked" and "Cut" in h and ws.cell_type(r, c) == 2:
                    cut_total += float(ws.cell_value(r, c))
            continue
        if ol == 1:
            vals: dict[str, float] = {}
            for c, (h, state) in headers.items():
                if not h or state is None:
                    continue
                v = _read_float(ws, r, c, file_name, sheet_name)
                if v is not None:
                    vals[h] = v
            qr = QuantityRow(sheet=sheet_name, row_index=r,
                             operation_group=label, outline_level=ol, values=vals)
            rows.append(qr)
            current_ol1 = qr
            continue
        if ol == 2 and current_ol1 is not None:
            if label in ("Material", "Strata", ""):
                continue
            vals = {}
            for c, (h, state) in headers.items():
                if not h or state is None:
                    continue
                v = _read_float(ws, r, c, file_name, sheet_name)
                if v is not None:
                    vals[h] = v
            if vals:
                current_ol1.materials.append(MaterialRow(name=label, values=vals))
    return rows, cut_total


def _parse_trenches_sheet(ws, file_name, has_format):
    rows, current_network, current_run, in_seg = [], None, None, False
    for r in range(ws.nrows):
        ol = _ol_from_rowinfo(ws, r, has_format)
        label = ws.cell_value(r, 0) if ws.cell_type(r, 0) == 1 else ""
        if ol == 1 and isinstance(label, str) and "TrenchNetwork" in label:
            current_network = label
        elif ol == 2 and isinstance(label, str) and "TrenchRun" in label:
            current_run = label
        elif ol == 3:
            if label == "TrenchSegment":
                in_seg = True
            elif in_seg:
                in_seg = False
            elif ws.cell_type(r, 1) == 2:
                qty = float(ws.cell_value(r, 1))
                avg_d = float(ws.cell_value(r, 3)) if ws.cell_type(r, 3) == 2 else None
                rows.append(LinearRow(sheet=ws.name, row_index=r, label=str(label),
                    outline_level=ol, quantity_m=qty, avg_depth_m=avg_d,
                    parent_network=current_network, parent_run=current_run))
        else:
            in_seg = False
    return rows


def _parse_depth_categories_sheet(ws, file_name, has_format):
    rows, cat, net, run_, in_seg = [], None, None, None, False
    for r in range(ws.nrows):
        ol = _ol_from_rowinfo(ws, r, has_format)
        label = ws.cell_value(r, 0) if ws.cell_type(r, 0) == 1 else ""
        if ol == 1 and isinstance(label, str) and "DepthCategoryCode" in label:
            cat = label.split(":")[-1].strip().split(" ")[0] if ":" in label else label
        elif ol == 2 and isinstance(label, str) and "TrenchNetwork" in label:
            net = label
        elif ol == 3 and isinstance(label, str) and "TrenchRun" in label:
            run_ = label
        elif ol == 4:
            if label == "TrenchSegment":
                in_seg = True
            elif in_seg:
                in_seg = False
            elif ws.cell_type(r, 1) == 2:
                qty = float(ws.cell_value(r, 1))
                avg_d = float(ws.cell_value(r, 3)) if ws.cell_type(r, 3) == 2 else None
                rows.append(LinearRow(sheet=ws.name, row_index=r, label=str(label),
                    outline_level=ol, quantity_m=qty, avg_depth_m=avg_d,
                    depth_category_code=cat, parent_network=net, parent_run=run_))
        else:
            in_seg = False
    return rows


def _parse_areas_sheet(ws, file_name):
    rows = []
    for r in range(1, ws.nrows):
        nc = ws.cell_value(r, 0)
        if not nc or str(nc).strip() in ("", "Name"):
            continue
        def _fv(c, _r=r):
            return float(ws.cell_value(_r, c)) if c < ws.ncols and ws.cell_type(_r, c) == 2 else None
        rows.append(AreaRow(row_index=r, name=str(nc).strip(), true_area_m2=_fv(1),
            top_down_area_m2=_fv(2), outside_perimeter_m=_fv(3),
            all_edges_m=_fv(4), total_thickness_m=_fv(5)))
    return rows


def _parse_union_cut_total(ws, has_format):
    """Sum the Cut (Bulked) column in the All Strata Operations union sheet.

    Only OL=0 rows are counted — these are the group-level totals written by
    Mudshark.  OL=1 (leaf) and OL=2 (material detail) rows carry the same
    values again and must not be included or the total is multiplied by 3.

    Matching _parse_volume_sheet, which also accumulates cut_total only from
    OL=0 rows on the component sheets (Ground Layer, Structure, Trench Run).
    """
    if ws.nrows < 2:
        return 0.0
    headers = _map_headers(ws.row_values(0))
    cut_col = next((c for c, (h, _) in headers.items() if "Cut (Bulked" in h), None)
    if cut_col is None:
        return 0.0
    total = 0.0
    for r in range(1, ws.nrows):
        if ws.cell_type(r, cut_col) != 2:
            continue
        ri = ws.rowinfo_map.get(r) if has_format else None
        ol = ri.outline_level if ri else 0
        if ol == 0:   # group-level total rows only
            total += float(ws.cell_value(r, cut_col))
    return total


# ─── main parse entry points ──────────────────────────────────────────────────

def _open_xls(data: bytes, file_name: str) -> tuple:
    import xlrd  # noqa: PLC0415
    try:
        wb = xlrd.open_workbook(file_contents=data, formatting_info=True)
        return wb, True
    except NotImplementedError:
        wb = xlrd.open_workbook(file_contents=data)
        return wb, False


def _parse_results_xls(data: bytes, file_name: str) -> "MudsharkWorkbook":
    try:
        import xlrd  # noqa
    except ImportError as e:
        raise ImportError("xlrd>=2.0.1 required: pip install xlrd>=2.0.1") from e
    fhash = _sha256(data)
    wb_out = MudsharkWorkbook(file_name=file_name, file_hash=fhash)
    wb, has_format = _open_xls(data, file_name)
    if not has_format:
        wb_out.warnings.append("formatting_info=True failed; outline levels unavailable")
    sheet_names = [s.name for s in wb.sheets()]
    comp_cut = 0.0
    for sname in _VOLUME_SHEETS:
        if sname not in sheet_names:
            wb_out.warnings.append(f"Expected sheet '{sname}' not found in {file_name}")
            continue
        ws = wb.sheet_by_name(sname)
        sheet_rows, sheet_cut = _parse_volume_sheet(ws, sname, file_name, has_format)
        wb_out.quantity_rows.extend(sheet_rows)
        comp_cut += sheet_cut
    if "All Strata Operations" in sheet_names:
        union_ws = wb.sheet_by_name("All Strata Operations")
        union_cut = _parse_union_cut_total(union_ws, has_format)
        wb_out.cross_checks["All_Strata_vs_components"] = _xcheck(
            "All Strata Operations", comp_cut, union_cut)
    trench_rows: list = []
    if "Trenches" in sheet_names:
        ws = wb.sheet_by_name("Trenches")
        trench_rows = _parse_trenches_sheet(ws, file_name, has_format)
        wb_out.linear_rows.extend(trench_rows)
    depth_rows: list = []
    if "Trench Depth Categories" in sheet_names:
        ws = wb.sheet_by_name("Trench Depth Categories")
        depth_rows = _parse_depth_categories_sheet(ws, file_name, has_format)
        wb_out.linear_rows.extend(depth_rows)
        t_total = sum(r.quantity_m for r in trench_rows)
        d_total = sum(r.quantity_m for r in depth_rows)
        wb_out.cross_checks["Trenches_vs_DepthCategories"] = _xcheck(
            "Trenches vs Trench Depth Categories", t_total, d_total)
    if "Areas and Perimeters" in sheet_names:
        ws = wb.sheet_by_name("Areas and Perimeters")
        wb_out.area_rows.extend(_parse_areas_sheet(ws, file_name))
    return wb_out


def _parse_trench_summary_xlsx(data: bytes, file_name: str) -> "MudsharkWorkbook":
    try:
        import openpyxl  # noqa
    except ImportError as e:
        raise ImportError("openpyxl required for .xlsx") from e
    fhash = _sha256(data)
    wb_out = MudsharkWorkbook(file_name=file_name, file_hash=fhash)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb_full = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    ws_full = wb_full.active
    headers = [str(h.value).strip() if h.value else ""
               for h in next(ws_full.iter_rows(max_row=1))]
    summed: dict = {h: 0.0 for h in headers if h}
    leaf_rows: list = []
    for r_idx, row in enumerate(ws_full.iter_rows(min_row=2), start=2):
        rd = ws_full.row_dimensions.get(r_idx)
        ol = rd.outline_level if rd else 0
        if ol < 2:
            continue
        lc = row[0].value
        if not lc or str(lc).strip() in ("", "Strata", "Material"):
            continue
        label = str(lc).strip()
        vals: dict = {}
        for i, cell in enumerate(row):
            if i >= len(headers) or not headers[i]:
                continue
            if isinstance(cell.value, (int, float)) and cell.value is not None:
                vals[headers[i]] = float(cell.value)
                summed[headers[i]] = summed.get(headers[i], 0.0) + float(cell.value)
        if vals:
            leaf_rows.append(QuantityRow(sheet="Trench_Summary", row_index=r_idx,
                operation_group=label, outline_level=ol, values=vals))
    wb_out.quantity_rows = leaf_rows
    for r_idx, row in enumerate(ws_full.iter_rows(min_row=2), start=2):
        rd = ws_full.row_dimensions.get(r_idx)
        ol = rd.outline_level if rd else 0
        if ol > 0:
            continue
        for i, cell in enumerate(row):
            if i >= len(headers) or not headers[i]:
                continue
            if isinstance(cell.value, (int, float)) and cell.value is not None:
                cached = float(cell.value)
                computed = summed.get(headers[i], 0.0)
                if abs(cached) > 1e-6:
                    diff = abs(computed - cached) / max(abs(cached), 1e-9)
                    if diff > _XCHECK_TOL:
                        wb_out.warnings.append(
                            f"Trench_Summary cached total col='{headers[i]}' "
                            f"cached={cached:.3f} computed={computed:.3f} ({diff:.2%})")
                break
    return wb_out


def _parse_measurements_xls(data: bytes, file_name: str) -> "MudsharkWorkbook":
    try:
        import xlrd  # noqa
    except ImportError as e:
        raise ImportError("xlrd>=2.0.1 required") from e
    fhash = _sha256(data)
    wb_out = MudsharkWorkbook(file_name=file_name, file_hash=fhash)
    wb, has_format = _open_xls(data, file_name)
    for ws in wb.sheets():
        sname = ws.name
        header_row_idx = None
        for r in range(min(25, ws.nrows)):
            vals = [ws.cell_value(r, c) for c in range(ws.ncols)]
            strs = [v for v in vals if isinstance(v, str) and v.strip()]
            if len(strs) >= 3:
                header_row_idx = r
                break
        if header_row_idx is None:
            wb_out.warnings.append(f"{file_name} sheet '{sname}': no header found, skipped")
            continue
        for r in range(header_row_idx + 1, ws.nrows):
            ol = _ol_from_rowinfo(ws, r, has_format)
            if ws.cell_type(r, 2) != 2:
                continue
            label = str(ws.cell_value(r, 0)) if ws.cell_type(r, 0) == 1 else ""
            qty = float(ws.cell_value(r, 2))
            units = str(ws.cell_value(r, 3)).strip() if ws.cell_type(r, 3) == 1 else ""
            if units.lower() in ("m", ""):
                wb_out.linear_rows.append(LinearRow(
                    sheet=sname, row_index=r, label=label, outline_level=ol,
                    quantity_m=qty, avg_depth_m=None))
    return wb_out


# ─── public source interface ──────────────────────────────────────────────────

class MudsharkSource:
    """Unified interface over a loose project directory (Layout A) or a zip (Layout B)."""

    def __init__(self, path: "Any") -> None:
        self.path = Path(path)
        self._zip: Optional[zipfile.ZipFile] = None
        if self.path.suffix.lower() == ".zip":
            self._zip = zipfile.ZipFile(self.path)

    def close(self) -> None:
        if self._zip:
            self._zip.close()

    def __enter__(self) -> "MudsharkSource":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _files(self) -> list[tuple[str, bytes]]:
        results: list[tuple[str, bytes]] = []
        if self._zip:
            for info in self._zip.infolist():
                if not info.is_dir():
                    ext = Path(info.filename).suffix.lower()
                    if ext in {".xls", ".xlsx"}:
                        results.append((Path(info.filename).name,
                                        self._zip.read(info.filename)))
        else:
            for p in self.path.rglob("*"):
                if p.suffix.lower() in {".xls", ".xlsx"} and p.is_file():
                    results.append((p.name, p.read_bytes()))
        return results

    def parse_all(self) -> "dict[str, MudsharkWorkbook]":
        """Parse all spreadsheet files; return dict keyed by report type."""
        from .paths import parse_file_name, pick_latest  # noqa: PLC0415
        files_data = self._files()
        file_infos = [parse_file_name(n) for n, _ in files_data]
        latest = pick_latest(file_infos)
        out: dict[str, MudsharkWorkbook] = {}
        for rtype, fi in latest.items():
            data = next((d for n, d in files_data if n == fi.original_name), None)
            if data is None:
                log.warning("Could not find bytes for %s", fi.original_name)
                continue
            ext = fi.extension
            if rtype == "Results" and ext == ".xls":
                out["Results"] = _parse_results_xls(data, fi.original_name)
            elif rtype == "Trench_Summary" and ext == ".xlsx":
                out["Trench_Summary"] = _parse_trench_summary_xlsx(data, fi.original_name)
            elif rtype == "Measurements" and ext == ".xls":
                out["Measurements"] = _parse_measurements_xls(data, fi.original_name)
            elif rtype == "Depth_Categories":
                fhash = _sha256(data)
                wb_out = MudsharkWorkbook(file_name=fi.original_name, file_hash=fhash)
                wb_out.warnings.append(
                    f"{fi.original_name}: standalone Depth_Categories export is empty "
                    "(data lives in Results.xls 'Trench Depth Categories' sheet) - skipped")
                out["Depth_Categories"] = wb_out
            else:
                log.debug("Skipping %s (type=%s ext=%s)", fi.original_name, rtype, ext)
        return out
