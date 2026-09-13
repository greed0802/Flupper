"""Fail CI if tracked source contains obvious credentials or real tunnel hosts."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACKED = subprocess.check_output(
    ["git", "ls-files", "-z"], cwd=ROOT, text=False
).decode().split("\0")

# Deliberately high-confidence patterns; documentation may mention variable names,
# but must not contain credential-shaped values.
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:ghp|github_pat|sk|xox[baprs])_[A-Za-z0-9_\-]{20,}\b"),
)

# Phase 4C tracked docs/config may use only the reserved synthetic hostname.
REAL_API_HOST = re.compile(
    r"\bapi\.(?!example\.invalid\b)[a-z0-9-]+(?:\.[a-z0-9-]+)+\b",
    re.IGNORECASE,
)
HOST_SCAN_PREFIXES = (".clinerules/", "docs/", "backend/qsagent/api/")

failures: list[str] = []
for name in TRACKED:
    if not name or not name.startswith(HOST_SCAN_PREFIXES):
        continue
    path = ROOT / name
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue

    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            failures.append(f"credential-shaped value in tracked file: {name}")
            break
    if REAL_API_HOST.search(text):
        failures.append(f"non-synthetic API hostname in tracked file: {name}")

if failures:
    raise SystemExit("\n".join(sorted(set(failures))))

print(f"tracked hygiene OK ({len([x for x in TRACKED if x])} tracked files scanned)")
