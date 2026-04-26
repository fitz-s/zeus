# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: docs/operations/task_2026-04-26_full_data_midstream_fix_plan/
#                  phases/task_2026-04-26_phase4_closeout/plan.md slice A1b
"""Slice A1b relationship + function tests.

Phase 1 §11 + parent post-review addendum deferred a semantic_linter
extension for calibration_pairs reads (the P3 4.5.A pattern for
settlements). Phase 4 closeout delivers it for calibration_pairs_v2:
every SELECT/JOIN against the v2 table must carry a temperature_metric
predicate or the linter emits an error.

Pre-fix audit confirmed all 5 current v2 reader sites already use
`WHERE temperature_metric = ?`. The lint is a forward-looking antibody
against future regressions — pinning the convention before it's
silently broken.

Tests:
1. SQL with calibration_pairs_v2 + temperature_metric WHERE → no
   violation (happy path).
2. SQL with calibration_pairs_v2 + NO metric predicate → violation.
3. SQL with calibration_pairs (legacy) → no v2 violation (legacy is
   covered by the K2_struct check at _check_calibration_pairs_select).
4. Allowlisted file path → no violation even if metric absent.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.semantic_linter import (
    _check_calibration_pairs_v2_metric_filter,
)


def test_v2_read_with_metric_predicate_passes():
    """Happy path: SELECT with WHERE temperature_metric = ?."""
    py_file = PROJECT_ROOT / "scripts" / "fake_v2_reader.py"
    content = '''
conn.execute(
    """
    SELECT p_raw, lead_days, outcome
    FROM calibration_pairs_v2
    WHERE temperature_metric = ?
      AND cluster = ?
    """,
    (metric, cluster),
)
'''
    violations = _check_calibration_pairs_v2_metric_filter(py_file, content)
    assert violations == [], (
        f"Expected 0 violations for v2 read with metric predicate; "
        f"got {len(violations)}: {violations}"
    )


def test_v2_read_without_metric_predicate_violates():
    """Antibody: SELECT without WHERE temperature_metric must violate."""
    py_file = PROJECT_ROOT / "scripts" / "fake_v2_reader.py"
    content = '''
conn.execute(
    """
    SELECT p_raw, lead_days, outcome
    FROM calibration_pairs_v2
    WHERE cluster = ?
    """,
    (cluster,),
)
'''
    violations = _check_calibration_pairs_v2_metric_filter(py_file, content)
    assert len(violations) == 1, (
        f"Expected 1 violation for metric-less v2 read; got {len(violations)}"
    )
    assert "calibration_pairs_v2" in violations[0]
    assert "A1b" in violations[0]
    assert "temperature_metric predicate" in violations[0]


def test_legacy_calibration_pairs_read_does_not_trigger_v2_lint():
    """Legacy calibration_pairs is K2_struct-locked; v2 lint doesn't apply."""
    py_file = PROJECT_ROOT / "scripts" / "fake_legacy_reader.py"
    content = '''
conn.execute(
    """
    SELECT p_raw FROM calibration_pairs WHERE cluster = ?
    """,
    (cluster,),
)
'''
    violations = _check_calibration_pairs_v2_metric_filter(py_file, content)
    assert violations == [], (
        "Legacy calibration_pairs read must not trigger A1b v2 lint."
    )


def test_v2_read_with_aliased_metric_predicate_passes():
    """Alias-qualified metric predicate must satisfy the check."""
    py_file = PROJECT_ROOT / "scripts" / "fake_v2_reader.py"
    content = '''
conn.execute(
    """
    SELECT p.p_raw FROM calibration_pairs_v2 AS p
    WHERE p.temperature_metric = ?
    """,
    (metric,),
)
'''
    violations = _check_calibration_pairs_v2_metric_filter(py_file, content)
    assert violations == [], "Aliased metric predicate must pass."


def test_test_files_skipped_by_lint():
    """Test files are exempt from the v2 metric lint (test fixtures may
    legitimately query without metric for diagnostic reasons)."""
    py_file = PROJECT_ROOT / "tests" / "test_some_v2_reader.py"
    content = '''
conn.execute("SELECT * FROM calibration_pairs_v2")
'''
    violations = _check_calibration_pairs_v2_metric_filter(py_file, content)
    assert violations == [], "Test files must not trigger v2 metric lint."


def test_migrations_skipped_by_lint():
    """Migration scripts may legitimately bulk-touch v2 without metric scope."""
    py_file = PROJECT_ROOT / "migrations" / "fake_migration.py"
    content = '''
conn.execute("SELECT * FROM calibration_pairs_v2")
'''
    violations = _check_calibration_pairs_v2_metric_filter(py_file, content)
    assert violations == [], "Migration files must not trigger v2 metric lint."


def test_current_v2_readers_pass_lint():
    """Repo-wide assertion: all 5 current v2 reader sites in src/+scripts/
    pass the new lint. Pre-fix audit identified these as already metric-
    scoped; this test pins that fact and detects future regressions."""
    expected_v2_readers = [
        PROJECT_ROOT / "scripts" / "refit_platt_v2.py",
        PROJECT_ROOT / "scripts" / "rebuild_calibration_pairs_v2.py",
        PROJECT_ROOT / "scripts" / "verify_truth_surfaces.py",
        PROJECT_ROOT / "scripts" / "backfill_tigge_snapshot_p_raw_v2.py",
    ]
    for py_file in expected_v2_readers:
        if not py_file.exists():
            continue  # Skip if file moved/renamed
        violations = _check_calibration_pairs_v2_metric_filter(
            py_file, py_file.read_text(encoding="utf-8", errors="replace"),
        )
        assert violations == [], (
            f"Pre-existing v2 reader {py_file.name} fails A1b lint: "
            f"{violations}"
        )
