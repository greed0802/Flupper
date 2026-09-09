"""BBX evidence recording.

A .bbx file is a ZIP-based Mudshark project package containing:
  - <uuid>.xml  (project XML with <Project><Name>)
  - various .bbp, .bbs, .pdf, .png tiles

Strategy: record by SHA-256 hash and path only.
No quantity data is extracted from .bbx — the Excel exports are the source.
The XML <Project><Name> is extracted for cross-check against the folder path.

Duplicate detection: files with identical content hashes are reported as
near-duplicates (Mudshark saves multiple snapshots per session).
"""
from __future__ import annotations

import hashlib
import io
import logging
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class BbxRecord:
    """Evidence record for a single .bbx file.  No quantity data contained."""
    file_name: str
    file_hash: str
    file_path: str
    size_bytes: int
    mudshark_project_name: Optional[str] = None   # from <Project><Name> in XML
    inner_file_count: int = 0
    inner_extensions: dict[str, int] = field(default_factory=dict)
    project_bf: Optional[float] = None
    project_sf: Optional[float] = None
    error: Optional[str] = None


def _extract_project_name(zip_bytes: bytes) -> Optional[str]:
    """Read <Project><Name> from the first .xml member of the bbx zip."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            xml_names = [m for m in zf.namelist() if m.endswith(".xml")]
            if not xml_names:
                return None
            xml_data = zf.read(xml_names[0])
            root = ET.fromstring(xml_data)
            name_el = root.find("Name")
            if name_el is not None and name_el.text:
                return name_el.text.strip()
    except Exception as exc:
        log.debug("bbx xml extract failed: %s", exc)
    return None


def _resolve_factor(values: set[str]) -> Optional[float]:
    import math
    if not values:
        return None
    
    parsed = []
    for v in values:
        if not v:
            return None  # missing or empty
        try:
            val = float(v.strip())
            if not math.isfinite(val) or val <= 0:
                return None  # non-finite or non-positive
            parsed.append(val)
        except ValueError:
            return None  # malformed
    
    if not parsed:
        return None
        
    first = parsed[0]
    for val in parsed[1:]:
        if not math.isclose(val, first, rel_tol=1e-5):
            return None  # conflicting
            
    return first


def _extract_factors(zip_bytes: bytes) -> tuple[Optional[float], Optional[float]]:
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            xml_names = [m for m in zf.namelist() if m.endswith(".xml")]
            if not xml_names:
                return None, None
            xml_data = zf.read(xml_names[0])
            root = ET.fromstring(xml_data)
            bfs: set[str] = set()
            sfs: set[str] = set()
            materials = root.findall(".//Materials/Material")
            if not materials:
                return None, None
                
            for mat in materials:
                bf_el = mat.find("BulkingFactor")
                if bf_el is not None and bf_el.text:
                    bfs.add(bf_el.text.strip())
                else:
                    bfs.add("")
                    
                cf_el = mat.find("CompressionFactor")
                if cf_el is not None and cf_el.text:
                    sfs.add(cf_el.text.strip())
                else:
                    sfs.add("")
                    
            return _resolve_factor(bfs), _resolve_factor(sfs)
    except Exception as exc:
        log.debug("bbx factors extract failed: %s", exc)
        return None, None


def _inner_stats(zip_bytes: bytes) -> tuple[int, dict[str, int]]:
    """Count inner files and extensions."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
            exts: dict[str, int] = {}
            for m in members:
                ext = Path(m.filename).suffix.lower() or "(none)"
                exts[ext] = exts.get(ext, 0) + 1
            return len(members), exts
    except Exception:
        return 0, {}


def record_bbx(path: Path) -> BbxRecord:
    """Build a BbxRecord for a single .bbx file without extracting to disk."""
    data = path.read_bytes()
    fhash = hashlib.sha256(data).hexdigest()
    rec = BbxRecord(
        file_name=path.name,
        file_hash=fhash,
        file_path=str(path),
        size_bytes=len(data),
    )
    # Verify it is actually a ZIP
    if data[:4] != b"PK\x03\x04":
        rec.error = "Not a ZIP-based bbx (unexpected magic bytes)"
        return rec

    rec.mudshark_project_name = _extract_project_name(data)
    rec.inner_file_count, rec.inner_extensions = _inner_stats(data)
    rec.project_bf, rec.project_sf = _extract_factors(data)
    return rec


def record_bbx_set(paths: list[Path]) -> tuple[list[BbxRecord], list[tuple[BbxRecord, BbxRecord]]]:
    """Record all .bbx files in a set; return (records, duplicate_pairs).

    Duplicate pairs share the same content hash — Mudshark snapshot copies.
    """
    records: list[BbxRecord] = []
    seen_hashes: dict[str, BbxRecord] = {}
    duplicates: list[tuple[BbxRecord, BbxRecord]] = []

    for p in sorted(paths):
        rec = record_bbx(p)
        records.append(rec)
        if rec.file_hash in seen_hashes:
            duplicates.append((seen_hashes[rec.file_hash], rec))
        else:
            seen_hashes[rec.file_hash] = rec

    return records, duplicates
