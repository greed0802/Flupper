"""Walk a drawing zip in-memory; extract title-block data with PyMuPDF.

All PDFs scanned for a text layer. Image-only PDFs recorded with
title_block_extracted=False – never silently skipped.

Non-PDF kinds:
  .jpg/.jpeg/.png  → site_photo
  .xlsx/.xls       → schedule
  .docx/.doc       → specification
  .dwg/.dxf/.dgn   → cad_drawing
  others           → unknown (warn)

Discipline inferred from drawing-number prefix:
  C/CV/CIVIL → CIVIL   A/ARCH → ARCHITECTURAL   S/STR → STRUCTURAL
  M/ME/MECH  → MECHANICAL   E/EL/ELEC → ELECTRICAL
  H/HY/HYD  → HYDRAULIC    L/LA/LAND → LANDSCAPE
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_TB_X_FRAC = 0.62
_TB_Y_FRAC = 0.72
_DRAWING_NO_RE = re.compile(r"\b([A-Z]{1,4}[-_ ]?\d{2,4}(?:[-_ ]?[A-Z0-9]{1,3})?)\b")
_REVISION_RE   = re.compile(r"\bRev(?:ision)?\.?\s*([A-Z0-9]{1,3})\b", re.I)

_DISC_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(C|CV|CIVIL)\b",   re.I), "CIVIL"),
    (re.compile(r"^(A|ARCH)\b",       re.I), "ARCHITECTURAL"),
    (re.compile(r"^(S|STR|STRUCT)\b", re.I), "STRUCTURAL"),
    (re.compile(r"^(M|ME|MECH)\b",    re.I), "MECHANICAL"),
    (re.compile(r"^(E|EL|ELEC)\b",    re.I), "ELECTRICAL"),
    (re.compile(r"^(H|HY|HYD)\b",     re.I), "HYDRAULIC"),
    (re.compile(r"^(L|LA|LAND)\b",    re.I), "LANDSCAPE"),
    (re.compile(r"^SK\b",             re.I), "SPECIFICATION"),
]


@dataclass
class DrawingRecord:
    file_name: str
    file_hash: str
    kind: str
    page_count: Optional[int] = None
    has_text_layer: Optional[bool] = None
    title_block_extracted: bool = False
    drawing_no: Optional[str] = None
    revision: Optional[str] = None
    sheet_title: Optional[str] = None
    discipline: str = "UNKNOWN"
    title_block_line_count: Optional[int] = None
    error: Optional[str] = None
    warnings: list[str] = field(default_factory=list)


def _classify_kind(ext: str) -> str:
    if ext == ".pdf":
        return "pdf"
    if ext in {".jpg", ".jpeg", ".png"}:
        return "site_photo"
    if ext in {".xlsx", ".xls"}:
        return "schedule"
    if ext in {".docx", ".doc"}:
        return "specification"
    if ext in {".dwg", ".dxf", ".dgn"}:
        return "cad_drawing"
    return "unknown"


def _discipline_from_drawing_no(drawing_no: str) -> str:
    prefix = re.split(r"[-_ ]", drawing_no)[0].strip()
    for pattern, disc in _DISC_MAP:
        if pattern.match(prefix):
            return disc
    return "UNKNOWN"


def _probe_text_layer(data: bytes) -> bool:
    return b"/Font" in data



def _open_fitz():
    try:
        import pymupdf as fitz
        return fitz
    except ImportError:
        pass
    try:
        import fitz
        return fitz
    except ImportError:
        return None


def _extract_title_block(data: bytes) -> tuple[Optional[str], Optional[str], Optional[str], int, Optional[str]]:
    """Return (drawing_no, revision, title, line_count, error)."""
    fitz = _open_fitz()
    if fitz is None:
        return None, None, None, 0, "pymupdf not installed"
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        if doc.page_count == 0:
            doc.close()
            return None, None, None, 0, "empty PDF"
        page = doc[0]
        tb = fitz.Rect(
            page.rect.width * _TB_X_FRAC, page.rect.height * _TB_Y_FRAC,
            page.rect.width, page.rect.height,
        )
        text = page.get_text("text", clip=tb) or ""
        doc.close()
        lines = [ln for ln in text.splitlines() if ln.strip()]
        drawing_nos = _DRAWING_NO_RE.findall(text)
        revs = _REVISION_RE.findall(text)
        return (
            drawing_nos[0] if drawing_nos else None,
            revs[0] if revs else None,
            lines[0] if lines else None,
            len(lines),
            None,
        )
    except Exception as exc:
        return None, None, None, 0, f"{type(exc).__name__}: {exc}"


def _page_count(data: bytes) -> Optional[int]:
    fitz = _open_fitz()
    if fitz is None:
        return None
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        n = doc.page_count
        doc.close()
        return n
    except Exception:
        return None


def probe_drawing_zip(zip_path: Path) -> list[DrawingRecord]:
    """Walk zip in-memory; return one DrawingRecord per file. Never extracts to disk."""
    records: list[DrawingRecord] = []
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        log.error("Bad zip %s: %s", zip_path, exc)
        return records

    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            ext = Path(info.filename).suffix.lower()
            name = Path(info.filename).name
            data = zf.read(info.filename)
            fhash = hashlib.sha256(data).hexdigest()
            kind = _classify_kind(ext)
            rec = DrawingRecord(file_name=name, file_hash=fhash, kind=kind)

            if kind == "pdf":
                rec.has_text_layer = _probe_text_layer(data)
                if not rec.has_text_layer:
                    rec.warnings.append(
                        f"{name}: no text layer (likely scanned); title-block extraction skipped"
                    )
                else:
                    dn, rev, title, lc, err = _extract_title_block(data)
                    if err:
                        rec.error = err
                        rec.warnings.append(f"{name}: title-block error: {err}")
                    elif dn or title:
                        rec.title_block_extracted = True
                        rec.drawing_no = dn
                        rec.revision = rev
                        rec.sheet_title = title
                        rec.title_block_line_count = lc
                        if dn:
                            rec.discipline = _discipline_from_drawing_no(dn)
                    else:
                        rec.warnings.append(
                            f"{name}: text layer present but no title-block data; "
                            "title_block_extracted=False"
                        )
                rec.page_count = _page_count(data)

            elif kind == "unknown":
                rec.warnings.append(
                    f"{name}: unrecognised extension '{ext}' – recorded but not parsed"
                )

            records.append(rec)

    return records

