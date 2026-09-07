"""Tests for Phase 2 data ingestion pipelines."""

import io
import json
import sqlite3
import pytest
from pathlib import Path
from datetime import date

# Avoid missing import runtime
import openpyxl
from qsagent.ingest.paths import parse_file_name, parse_folder_path, pick_latest
from qsagent.ingest.mudshark import MudsharkSource, MudsharkWorkbook, QuantityRow, LinearRow, MaterialRow, ParseError
from qsagent.ingest.wbs import map_wbs, WBSItem
from qsagent.ingest.drawings import _discipline_from_drawing_no, _classify_kind

from qsagent.storage import QSStore
from qsagent.contracts import Discipline, Unit


# 1. Paths verification
def test_parse_file_name():
    p1 = parse_file_name("Retail_Store_Alpha_Results_31082026.xls")
    assert p1.report_type == "Results"
    assert p1.export_date == date(2026, 8, 31)
    assert p1.copy_index is None
    assert not p1.is_stale

    p2 = parse_file_name("Retail_Store_Alpha_Trench_Summary_28082026(2).xlsx")
    assert p2.report_type == "Trench_Summary"
    assert p2.export_date == date(2026, 8, 28)
    assert p2.copy_index == 2
    assert p2.is_stale

    p3 = parse_file_name("Primary School West - Stage 2_Masterfile_04092026.zip")
    assert p3.report_type == "Masterfile"
    assert p3.export_date == date(2026, 9, 4)

def test_parse_folder_path():
    path = Path("D:/exports/masterfile/2026/August/Retail Store Alpha")
    parsed = parse_folder_path(path)
    assert parsed.year == 2026
    assert parsed.month == "August"
    assert parsed.project_name == "Retail Store Alpha"

    path2 = Path("/Volumes/Mac/masterfile/2026/September/02. Primary School West - Stage 2/")
    parsed2 = parse_folder_path(path2)
    assert parsed2.year == 2026
    assert parsed2.month == "September"
    assert parsed2.project_name == "Primary School West"

def test_pick_latest():
    f1 = parse_file_name("Proj_Results_10012026.xls")
    f2 = parse_file_name("Proj_Results_12012026.xls")
    f3 = parse_file_name("Proj_Results_15012026(1).xls")
    picked = pick_latest([f1, f2, f3])
    assert "Results" in picked
    assert picked["Results"].export_date == date(2026, 1, 12)  # ignores the (1) duplicate entirely


# 2. WBS mapping verification
def test_wbs_mapping():
    assert map_wbs("Stripping (0.1m)", {}) == WBSItem.TOPSOIL_STRIP
    assert map_wbs("BUILDING PAD Cut", {}) == WBSItem.BULK_CUT
    assert map_wbs("Fill: Sandy Silt", {}) == WBSItem.BULK_FILL
    assert map_wbs("Rock Excavation", {}) == WBSItem.ROCK_EXCAVATION
    assert map_wbs("Trench Run 1", {}) == WBSItem.TRENCH
    # fallback to columns
    assert map_wbs("Generic Operations Group", {"Cut (Bulked m³)": 10.0}) == WBSItem.BULK_CUT
    assert map_wbs("Generic Operations Group", {"Fill (Compressed m³)": 5.0}) == WBSItem.BULK_FILL


# 3. Drawing names
def test_drawing_classification():
    assert _classify_kind(".pdf") == "pdf"
    assert _classify_kind(".jpg") == "site_photo"
    assert _classify_kind(".xlsx") == "schedule"
    assert _classify_kind(".dwg") == "cad_drawing"

def test_discipline():
    assert _discipline_from_drawing_no("CV-101") == "CIVIL"
    assert _discipline_from_drawing_no("ARCH-01") == "ARCHITECTURAL"
    assert _discipline_from_drawing_no("S-01") == "STRUCTURAL"
    assert _discipline_from_drawing_no("M_102") == "MECHANICAL"
    assert _discipline_from_drawing_no("E-LIGHT-01") == "ELECTRICAL"
    assert _discipline_from_drawing_no("SK-4") == "SPECIFICATION"
