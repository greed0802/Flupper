"""Structure probe — describes your Mudshark/drawing archives WITHOUT leaking data.

Run this locally against your zips. It emits a JSON report describing the
*shape* of your files (extensions, sheet names, column headers, title-block
text patterns) so parsers can be written against reality instead of guesswork.

PRIVACY: by default this reports structure only. Cell VALUES, quantities and
rates are never emitted. Column headers and sheet names are emitted because
parsers cannot be written without them; review the output before sharing.
Pass --redact-headers if even headers are sensitive (they rarely are).

Usage:
    python probe_export.py ~/exports/*.zip --out probe_report.json
    python probe_export.py ~/exports/ --out probe_report.json

Dependencies: none required. openpyxl improves .xlsx inspection if present.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

# Patterns that look like a drawing number, e.g. C-204, A203, SK-12, MRWA-C-101
DRAWING_NO_RE = re.compile(r"\b([A-Z]{1,4}[-_ ]?\d{2,4}(?:[-_ ]?[A-Z0-9]{1,3})?)\b")
REVISION_RE = re.compile(r"\b(?:REV(?:ISION)?\.?\s*([A-Z0-9]{1,3})|\bRev\s*([A-Z0-9]{1,3}))\b", re.I)

SPREADSHEET_EXT = {".xlsx", ".xlsm", ".xls", ".csv", ".txt"}
DRAWING_EXT = {".pdf", ".dwg", ".dxf", ".dgn"}
MUDSHARK_EXT = {".bbx", ".msk", ".mud", ".rib"}


def _anon(name: str) -> str:
    """Stable pseudonym for a filename, so we can talk about it without naming it."""
    return "f_" + hashlib.sha256(name.encode()).hexdigest()[:8]


def probe_csv(data: bytes, max_rows: int = 40) -> dict[str, Any]:
    """Report delimiter, header row and column names — never cell values."""
    try:
        text = data.decode("utf-8-sig", errors="replace")
    except Exception:
        return {"error": "undecodable"}
    lines = text.splitlines()[:max_rows]
    if not lines:
        return {"error": "empty"}

    # Score each candidate by its most common per-line occurrence count. A real
    # delimiter appears a consistent, non-zero number of times on most lines.
    def _score(d: str) -> int:
        per_line = Counter(ln.count(d) for ln in lines if ln.strip())
        per_line.pop(0, None)  # lines without the delimiter tell us nothing
        if not per_line:
            return 0
        modal_count, lines_agreeing = per_line.most_common(1)[0]
        return modal_count * lines_agreeing

    delim = max([",", ";", "\t", "|"], key=_score)
    if _score(delim) == 0:
        return {"kind": "delimited", "note": "no delimiter detected — single column?",
                "column_count": 1, "columns": [lines[0][:80]]}

    # Mudshark exports often carry preamble rows before the real header.
    best_idx, best_n = 0, 0
    for i, ln in enumerate(lines):
        n = len([c for c in ln.split(delim) if c.strip()])
        if n > best_n:
            best_idx, best_n = i, n

    header = [c.strip().strip('"') for c in lines[best_idx].split(delim)]
    return {
        "kind": "delimited",
        "delimiter": {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}[delim],
        "preamble_rows": best_idx,
        "column_count": len(header),
        "columns": header,
        "sample_line_count": len(lines),
    }


def probe_xlsx(data: bytes) -> dict[str, Any]:
    """Sheet names, dimensions and header rows via openpyxl if available."""
    try:
        import openpyxl  # type: ignore
    except ImportError:
        return {"kind": "xlsx", "note": "install openpyxl for sheet detail"}
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        return {"kind": "xlsx", "error": f"{type(exc).__name__}: {exc}"}

    sheets = []
    for ws in wb.worksheets:
        header, header_row = [], None
        for r, row in enumerate(ws.iter_rows(max_row=25, values_only=True), start=1):
            cells = [c for c in row if c not in (None, "")]
            if len(cells) >= 2 and all(isinstance(c, str) for c in cells):
                header = [str(c).strip() for c in row if c not in (None, "")]
                header_row = r
                break
        sheets.append({
            "name": ws.title,
            "max_row": ws.max_row,
            "max_column": ws.max_column,
            "header_row": header_row,
            "columns": header,
        })
    wb.close()
    return {"kind": "xlsx", "sheet_count": len(sheets), "sheets": sheets}



def probe_xls(data: bytes) -> dict[str, Any]:
    # Sheet names, headers, typemaps and outline status via xlrd.
    try:
        import xlrd
    except ImportError:
        return {'kind': 'xls', 'note': 'install xlrd>=2.0.1 for legacy .xls'}
    
    try:
        try:
            wb = xlrd.open_workbook(file_contents=data, formatting_info=True)
            has_format = True
        except NotImplementedError:
            wb = xlrd.open_workbook(file_contents=data)
            has_format = False
    except Exception as exc:
        return {'kind': 'xls', 'error': f'{type(exc).__name__}: {exc}'}

    sheets = []
    for ws in wb.sheets():
        header, header_row = [], None
        for r in range(min(ws.nrows, 25)):
            cells = ws.row_values(r)
            non_empty = [c for c in cells if c not in (None, '')]
            if len(non_empty) >= 2 and all(isinstance(c, str) for c in non_empty):
                header = [str(c).strip() for c in cells]
                header_row = r + 1
                break
        
        outline_levels = []
        if has_format and ws.rowinfo_map:
            levels = {info.outline_level for info in ws.rowinfo_map.values()}
            outline_levels = sorted(levels)

        col_types = {}
        type_names = {0:'EMPTY', 1:'TEXT', 2:'NUMBER', 3:'DATE', 4:'BOOL', 5:'ERR', 6:'BLANK'}
        if header_row and ws.nrows > header_row:
            from collections import Counter
            for cx in range(ws.ncols):
                types_found = Counter(ws.cell_type(rx, cx) for rx in range(header_row, min(ws.nrows, header_row+50)))
                val = {type_names.get(k, str(k)): v for k, v in types_found.items()}
                col_types[str(cx)] = val

        sheets.append({
            'name': ws.name,
            'max_row': ws.nrows,
            'max_column': ws.ncols,
            'header_row': header_row,
            'columns': header,
            'has_formatting_info': has_format,
            'rowinfo_map_size': len(ws.rowinfo_map) if has_format else 0,
            'outline_levels_found': outline_levels,
            'col_types_sample': col_types,
        })
    return {'kind': 'xls', 'sheet_count': len(sheets), 'sheets': sheets}


def probe_pdf(data: bytes) -> dict[str, Any]:
    """Page count plus drawing-number/revision patterns. No full text emitted."""
    info: dict[str, Any] = {"kind": "pdf", "bytes": len(data)}
    info["page_count"] = len(re.findall(rb"/Type\s*/Page\b", data)) or None
    info["has_text_layer"] = b"/Font" in data
    info["likely_scanned"] = (b"/Font" not in data) and (b"/Image" in data)
    try:
        import fitz  # type: ignore
    except ImportError:
        info["note"] = "install pymupdf for title-block extraction"
        return info
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        info["page_count"] = doc.page_count
        page = doc[0]
        info["page_size_pt"] = [round(page.rect.width, 1), round(page.rect.height, 1)]
        # Title blocks live in the bottom-right corner of an AU civil sheet.
        tb = fitz.Rect(page.rect.width * 0.62, page.rect.height * 0.72,
                       page.rect.width, page.rect.height)
        text = page.get_text("text", clip=tb) or ""
        info["title_block_line_count"] = len([l for l in text.splitlines() if l.strip()])
        info["drawing_no_candidates"] = sorted(set(DRAWING_NO_RE.findall(text)))[:5]
        revs = [a or b for a, b in REVISION_RE.findall(text)]
        info["revision_candidates"] = sorted(set(revs))[:5]
        doc.close()
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


def probe_binary(name: str, data: bytes) -> dict[str, Any]:
    """Unknown/proprietary format (.bbx etc): report magic bytes and container type."""
    head = data[:16]
    out: dict[str, Any] = {
        "kind": "binary",
        "bytes": len(data),
        "magic_hex": head.hex(),
        "magic_ascii": "".join(chr(b) if 32 <= b < 127 else "." for b in head),
    }
    if head[:2] == b"PK":
        out["container"] = "zip-based"
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                out["inner_entries"] = z.namelist()[:40]
        except Exception:
            pass
    elif head[:5] == b"SQLite" [:5] or data[:16].startswith(b"SQLite format 3"):
        out["container"] = "sqlite"
    elif head[:1] in (b"{", b"["):
        out["container"] = "json-like"
    elif b"<?xml" in data[:200]:
        out["container"] = "xml"
    return out


def probe_archive(path: Path, redact_headers: bool = False) -> dict[str, Any]:
    report: dict[str, Any] = {
        "archive": path.name,
        "archive_alias": _anon(path.name),
        "size_mb": round(path.stat().st_size / 1e6, 2),
        "entries": [],
    }
    ext_counter: Counter[str] = Counter()
    dir_depths: Counter[int] = Counter()
    by_ext: dict[str, list[str]] = defaultdict(list)

    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        report["error"] = "not a zip archive"
        return report

    with zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        report["entry_count"] = len(members)
        for m in members:
            ext = Path(m.filename).suffix.lower()
            ext_counter[ext] += 1
            dir_depths[m.filename.count("/")] += 1
            by_ext[ext].append(m.filename)

        report["extension_histogram"] = dict(ext_counter.most_common())
        report["folder_depth_histogram"] = dict(sorted(dir_depths.items()))
        report["sample_paths"] = [m.filename for m in members[:25]]

        # Deep-probe a couple of representatives per interesting extension.
        interesting = SPREADSHEET_EXT | DRAWING_EXT | MUDSHARK_EXT
        for ext in [e for e in ext_counter if e in interesting]:
            for fname in by_ext[ext][:2]:
                try:
                    data = zf.read(fname)
                except Exception as exc:
                    report["entries"].append({"name": fname, "error": str(exc)})
                    continue
                if ext in {".csv", ".txt"}:
                    detail = probe_csv(data)
                elif ext in {".xlsx", ".xlsm"}:
                    detail = probe_xlsx(data)
                elif ext == ".xls":
                    detail = probe_xls(data)
                elif ext == ".pdf":
                    detail = probe_pdf(data)
                else:
                    detail = probe_binary(fname, data)

                if redact_headers:
                    detail.pop("columns", None)
                    for s in detail.get("sheets", []):
                        s.pop("columns", None)

                report["entries"].append({
                    "name": Path(fname).name,
                    "path_depth": fname.count("/"),
                    "ext": ext,
                    **detail,
                })
    return report



def probe_loose_dir(path: Path, redact_headers: bool = False) -> dict[str, Any]:
    report: dict[str, Any] = {
        'archive': f'Loose files in {path.name}',
        'archive_alias': _anon(path.name),
        'size_mb': 0.0,
        'entries': [],
    }
    ext_counter: Counter[str] = Counter()
    by_ext: dict[str, list[Path]] = defaultdict(list)
    
    files = [p for p in path.rglob('*') if p.is_file() and p.suffix.lower() not in {'.DS_Store'}]
    report['entry_count'] = len(files)
    total_size = 0
    for p in files:
        sz = p.stat().st_size
        total_size += sz
        ext = p.suffix.lower()
        ext_counter[ext] += 1
        by_ext[ext].append(p)
        
    report['size_mb'] = round(total_size / 1e6, 2)
    report['extension_histogram'] = dict(ext_counter.most_common())
    report['sample_paths'] = [p.name for p in files[:25]]
    
    interesting = SPREADSHEET_EXT | DRAWING_EXT | MUDSHARK_EXT
    for ext in [e for e in ext_counter if e in interesting]:
        for filepath in by_ext[ext]:
            try:
                data = filepath.read_bytes()
            except Exception as exc:
                report['entries'].append({'name': filepath.name, 'error': str(exc)})
                continue
            
            if ext in {'.csv', '.txt'}:
                detail = probe_csv(data)
            elif ext in {'.xlsx', '.xlsm'}:
                detail = probe_xlsx(data)
            elif ext == '.xls':
                detail = probe_xls(data)
            elif ext == '.pdf':
                detail = probe_pdf(data)
            else:
                detail = probe_binary(filepath.name, data)
                
            if redact_headers:
                detail.pop('columns', None)
                for s in detail.get('sheets', []):
                    s.pop('columns', None)

            report['entries'].append({
                'name': filepath.name,
                'ext': ext,
                **detail,
            })
    return report

def main() -> int:

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="zip files, or directories containing them")
    ap.add_argument("--out", default="probe_report.json")
    ap.add_argument("--redact-headers", action="store_true",
                    help="omit column headers and sheet columns from the report")
    args = ap.parse_args()

    targets: list[Path] = []
    dir_targets: list[Path] = []
    for raw in args.paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            targets += sorted(p.rglob("*.zip"))
            dir_targets.append(p)
        elif p.exists():
            targets.append(p)
        else:
            print(f"  ! not found: {p}", file=sys.stderr)

    if not targets and not dir_targets:
        print("No paths found.", file=sys.stderr)
        return 1

    reports = []
    for t in targets:
        print(f"probing {t.name} (zip) ...", file=sys.stderr)
        reports.append(probe_archive(t, redact_headers=args.redact_headers))
    for d in dir_targets:
        print(f"probing {d.name} (loose dir) ...", file=sys.stderr)
        reports.append(probe_loose_dir(d, redact_headers=args.redact_headers))

    out = Path(args.out)
    out.write_text(json.dumps(
        {"probe_version": 1, "archive_count": len(reports), "archives": reports},
        indent=2, default=str))

    total = sum(r.get("entry_count", 0) for r in reports)
    print(f"\nWrote {out}  ({len(reports)} archives, {total} files described)",
          file=sys.stderr)
    print("Review it before sharing — it contains filenames, sheet names and "
          "column headers, but no cell values.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
