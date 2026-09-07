"""Parse project identity from the Mudshark folder/file naming convention.

Supported patterns
------------------
Layout A – loose files in a year/month/project directory:
    masterfile/2026/August/Aldi Dandenong/
        Aldi_Dandenong_Results_31082026.xls
        Aldi_Dandenong_Trench_Summary_31082026.xlsx

Layout B – single zip per project in a year/month directory.

Naming rules observed in the field
-----------------------------------
- Date suffix is DDMMYYYY.
- Windows copy-suffix (1), (2), (3) marks stale snapshots – rejected.
- Folder names sometimes carry a "02. " ordinal prefix and/or " - Stage 2" suffix.
- Report type is one of: Results, Measurements, Depth_Categories, Trench_Summary,
  Masterfile, Project.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, Optional

# ─── constants ───────────────────────────────────────────────────────────────

ReportType = Literal[
    "Results", "Measurements", "Depth_Categories", "Trench_Summary",
    "Masterfile", "Project", "Unknown",
]

REPORT_KEYWORDS: dict[str, ReportType] = {
    "results": "Results",
    "measurements": "Measurements",
    "depth_categories": "Depth_Categories",
    "trench_summary": "Trench_Summary",
    "masterfile": "Masterfile",
    "project": "Project",
}

_DATE_RE = re.compile(r"(\d{2})(\d{2})(\d{4})(?:\s*\(\d+\))?$")
_COPY_RE = re.compile(r"\s*\((\d+)\)\s*$")
_ORDINAL_RE = re.compile(r"^\d+\.\s*")
_STAGE_RE = re.compile(r"\s*-?\s*stage\s*\d+\s*$", re.I)

_MONTH_MAP = {
    m: i + 1 for i, m in enumerate(
        ["january","february","march","april","may","june",
         "july","august","september","october","november","december"]
    )
}


# ─── data classes ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FileInfo:
    original_name: str
    stem: str
    extension: str
    report_type: ReportType
    export_date: Optional[date]
    copy_index: Optional[int]
    is_stale: bool


@dataclass(frozen=True)
class FolderInfo:
    year: Optional[int]
    month: Optional[str]
    project_name: str
    raw_folder_name: str


# ─── helpers ─────────────────────────────────────────────────────────────────

def _clean_project_name(raw: str) -> str:
    name = _ORDINAL_RE.sub("", raw).strip()
    return _STAGE_RE.sub("", name).strip()


def _detect_report_type(stem: str) -> ReportType:
    lower = stem.lower().replace(" ", "_")
    for keyword, rtype in sorted(REPORT_KEYWORDS.items(), key=lambda x: -len(x[0])):
        if keyword in lower:
            return rtype
    return "Unknown"


def _detect_date(stem: str) -> Optional[date]:
    stem_clean = _COPY_RE.sub("", stem)
    m = _DATE_RE.search(stem_clean)
    if not m:
        return None
    dd, mm, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(yyyy, mm, dd)
    except ValueError:
        return None


def _detect_copy_index(stem: str) -> Optional[int]:
    m = _COPY_RE.search(stem)
    return int(m.group(1)) if m else None


# ─── public API ──────────────────────────────────────────────────────────────

def parse_file_name(name: str) -> FileInfo:
    """Parse a file name into a FileInfo.  Never raises."""
    p = Path(name)
    stem = p.stem
    ext = p.suffix.lower()
    report_type = _detect_report_type(stem)
    export_date = _detect_date(stem)
    copy_index = _detect_copy_index(stem)
    return FileInfo(
        original_name=name,
        stem=stem,
        extension=ext,
        report_type=report_type,
        export_date=export_date,
        copy_index=copy_index,
        is_stale=(copy_index is not None),
    )


def parse_folder_path(path: Path) -> FolderInfo:
    """Infer year, month, project name from a masterfile directory path."""
    parts = path.resolve().parts
    year: Optional[int] = None
    month: Optional[str] = None
    project_name = _clean_project_name(path.name)

    for part in reversed(parts[:-1]):
        if year is None and re.fullmatch(r"\d{4}", part):
            year = int(part)
        elif month is None and part.lower() in _MONTH_MAP:
            month = part.capitalize()
        if year and month:
            break

    return FolderInfo(
        year=year,
        month=month,
        project_name=project_name,
        raw_folder_name=path.name,
    )


def pick_latest(files: list[FileInfo]) -> dict[ReportType, FileInfo]:
    """For each report type return the file with the latest date.

    Windows copy-indexed files are always rejected.
    """
    best: dict[ReportType, FileInfo] = {}
    for fi in files:
        if fi.copy_index is not None:
            continue
        rtype = fi.report_type
        if rtype not in best:
            best[rtype] = fi
        else:
            incumbent = best[rtype]
            if fi.export_date and incumbent.export_date:
                if fi.export_date > incumbent.export_date:
                    best[rtype] = fi
            elif fi.export_date and not incumbent.export_date:
                best[rtype] = fi
    return best
