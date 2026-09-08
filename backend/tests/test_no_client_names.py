"""Guard: client and project names must never appear in tracked source.

This regressed once already — QS diagnostic reasoning was recorded in code
comments and named the real project. A grep caught it, so make the grep a test.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# Real client/project identifiers that must not be committed.
FORBIDDEN = re.compile(
    r"aldi|dandenong|padre\s*pio|brighton\s*rd|mt\s*waverley|nerang|"
    r"st\.?\s*francis\s*of\s*assisi",
    re.IGNORECASE,
)

SCAN_DIRS = ("backend", "tools")
SKIP_SUFFIXES = {".pyc"}


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", *SCAN_DIRS], cwd=REPO, capture_output=True, text=True
    )
    if out.returncode != 0:
        pytest.skip("git not available")
    # Exclude this guard itself: it necessarily contains the forbidden pattern.
    self_rel = Path(__file__).resolve().relative_to(REPO).as_posix()
    return [
        REPO / line
        for line in out.stdout.split()
        if line and line != self_rel
    ]


def test_no_client_names_in_tracked_source():
    offenders: list[str] = []
    for path in _tracked_files():
        if path.suffix in SKIP_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if FORBIDDEN.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{i}")
    assert not offenders, (
        "Client/project names found in tracked source. Use a neutral label such as "
        "'the reference project', or read the path from the FLUPPER_REAL_PROJECT "
        f"environment variable.\nOffending lines: {offenders}"
    )
