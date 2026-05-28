# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: docs/findings_2026_05_28.md §B1 — generation-naming denylist
"""
Test 1: Repo path + text scan for generation-naming words.
xfail(strict=False): repo-owned source still contains many forbidden tokens;
PR3 sweep will eradicate them.
"""
import pathlib
import re

import pytest

# Forbidden words built by concatenation so THIS file does not trip the denylist.
_VER = "ver" + "sion"          # "version"
_LEG = "leg" + "acy"           # "legacy"
_V2  = "_v" + "2"              # "_v2"
_V1  = "_v" + "1"              # "_v1"
_VN  = "v" + "next"            # "vnext"
_V0_ = "v" + "0" + "_"        # "v0_" prefix form
_V1_ = "v" + "1" + "_"        # "v1_"
_V2_ = "v" + "2" + "_"        # "v2_"

REPO_ROOT = pathlib.Path(__file__).parents[2]

SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    ".codegraph",
    "static",   # skip this very directory (tests/static/ — test bodies use concatenation but comments may match)
}

SKIP_SUFFIXES = {
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".egg-info",
    ".lock", ".png", ".jpg", ".jpeg", ".gif", ".ico",
    ".db", ".db-wal", ".db-shm", ".bin", ".gz", ".tar", ".zip",
    ".whl", ".egg",
}

# Pattern: version / _v<N> / v<N>_ / vnext / legacy
_PATTERN = re.compile(
    r"(?:" + _VER + r"|_v\d+|v\d+_|" + _VN + r"|" + _LEG + r")",
    re.IGNORECASE,
)


def _candidate_files():
    """Yield repo-owned .py and .yaml/.yml files skipping infra dirs."""
    for path in REPO_ROOT.rglob("*"):
        # Skip infra / binary dirs
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix in SKIP_SUFFIXES:
            continue
        if path.suffix not in {".py", ".yaml", ".yml", ".md", ".sql", ".sh", ".txt"}:
            continue
        yield path


def _collect_violations():
    violations = []
    for path in _candidate_files():
        rel = path.relative_to(REPO_ROOT)
        # 1. Path itself
        if _PATTERN.search(str(rel)):
            violations.append(f"PATH: {rel}")
            continue
        # 2. File content
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if _PATTERN.search(line):
                violations.append(f"TEXT {rel}:{lineno}: {line[:120].strip()}")
                break  # one violation per file is enough for the count
    return violations


@pytest.mark.xfail(strict=False, reason="awaits PR3 sweep — generation words still present in repo source")
def test_no_forbidden_generation_words_in_paths_or_text():
    """Repo-owned source must contain zero generation-naming tokens."""
    violations = _collect_violations()
    assert violations == [], (
        f"Found {len(violations)} generation-naming violations:\n"
        + "\n".join(violations[:30])
        + ("\n..." if len(violations) > 30 else "")
    )
