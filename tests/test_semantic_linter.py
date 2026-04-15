"""Tests for scripts/semantic_linter.py K2_struct rule.

Verifies that the linter detects bare FROM calibration_pairs queries
outside the allowlist (src/calibration/store.py, migrations/).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.semantic_linter import _check_calibration_pairs_select, run_linter


# ---------------------------------------------------------------------------
# K2_struct lint rule: no bare FROM calibration_pairs outside allowlist
# ---------------------------------------------------------------------------

def test_no_bare_calibration_pairs_select():
    """K2_struct: linter finds zero violations in src/ for calibration_pairs SELECTs."""
    src_path = PROJECT_ROOT / "src"
    # Collect only calibration_pairs violations from src/
    violations = []
    for py_file in src_path.rglob("*.py"):
        if py_file.name.startswith("test_"):
            continue
        content = py_file.read_text()
        violations.extend(_check_calibration_pairs_select(py_file, content))

    assert violations == [], (
        f"K2_struct: {len(violations)} bare FROM calibration_pairs found outside allowlist:\n"
        + "\n".join(violations)
    )


def test_linter_detects_violation_in_non_allowlisted_file(tmp_path):
    """_check_calibration_pairs_select fires on a file outside the allowlist."""
    bad_file = tmp_path / "bad_query.py"
    bad_file.write_text(
        'rows = conn.execute("SELECT p_raw FROM calibration_pairs WHERE city = ?")\n'
    )
    violations = _check_calibration_pairs_select(bad_file, bad_file.read_text())
    assert len(violations) == 1
    assert "K2_struct" in violations[0]
    assert "calibration_pairs" in violations[0]


def test_linter_allows_store_py(tmp_path):
    """_check_calibration_pairs_select does not fire for store.py (allowlist)."""
    store_file = tmp_path / "store.py"
    store_file.write_text(
        'rows = conn.execute("SELECT p_raw FROM calibration_pairs WHERE authority = ?")\n'
    )
    violations = _check_calibration_pairs_select(store_file, store_file.read_text())
    assert violations == []


def test_linter_allows_migrations_dir(tmp_path):
    """_check_calibration_pairs_select does not fire for files inside migrations/."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    migration_file = migrations_dir / "001_add_authority.py"
    migration_file.write_text(
        'conn.execute("SELECT COUNT(*) FROM calibration_pairs")\n'
    )
    violations = _check_calibration_pairs_select(migration_file, migration_file.read_text())
    assert violations == []


def test_linter_ignores_commented_lines(tmp_path):
    """_check_calibration_pairs_select ignores FROM calibration_pairs inside comments."""
    commented_file = tmp_path / "some_module.py"
    commented_file.write_text(
        '# SELECT * FROM calibration_pairs -- this is a comment\n'
        'x = 1\n'
    )
    violations = _check_calibration_pairs_select(commented_file, commented_file.read_text())
    assert violations == []
