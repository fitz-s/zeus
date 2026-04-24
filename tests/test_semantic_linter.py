"""Tests for scripts/semantic_linter.py K2_struct rule.

Verifies that the linter detects bare FROM calibration_pairs queries
outside the allowlist (src/calibration/store.py, migrations/).
"""
# Lifecycle: created=2026-04-13; last_reviewed=2026-04-24; last_reused=2026-04-24
# Purpose: Protect static semantic-linter antibodies for unsafe table/query usage.
# Reuse: Inspect architecture/test_topology.yaml and scripts/semantic_linter.py allowlists before extending.
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.semantic_linter import (
    _check_calibration_pairs_select,
    _check_legacy_hourly_observations_select,
    _check_settlements_metric_filter,
    run_linter,
)


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


# ---------------------------------------------------------------------------
# P0 unsafe-table lint rule: no bare canonical hourly_observations reads
# ---------------------------------------------------------------------------


def test_linter_detects_hourly_observations_query_in_non_allowlisted_file(tmp_path):
    """P0_unsafe_table fires on bare reads from legacy hourly_observations."""
    bad_file = tmp_path / "bad_hourly_query.py"
    bad_file.write_text(
        'rows = conn.execute("SELECT * FROM hourly_observations WHERE city = ?")\n'
    )
    violations = _check_legacy_hourly_observations_select(
        bad_file, bad_file.read_text()
    )
    assert len(violations) == 1
    assert "P0_unsafe_table" in violations[0]
    assert "hourly_observations" in violations[0]


def test_linter_detects_hourly_observations_join(tmp_path):
    """P0_unsafe_table catches JOIN hourly_observations as well as FROM."""
    bad_file = tmp_path / "bad_hourly_join.py"
    bad_file.write_text(
        'rows = conn.execute("SELECT * FROM foo JOIN hourly_observations h ON h.city = foo.city")\n'
    )
    violations = _check_legacy_hourly_observations_select(
        bad_file, bad_file.read_text()
    )
    assert len(violations) == 1
    assert "P0_unsafe_table" in violations[0]


def test_linter_detects_multiline_hourly_observations_query(tmp_path):
    """P0_unsafe_table catches whitespace-formatted multiline SQL."""
    bad_file = tmp_path / "bad_hourly_multiline.py"
    bad_file.write_text(
        'rows = conn.execute("""\n'
        'SELECT *\n'
        'FROM\n'
        '  hourly_observations\n'
        '""")\n'
    )
    violations = _check_legacy_hourly_observations_select(
        bad_file, bad_file.read_text()
    )
    assert len(violations) == 1
    assert "P0_unsafe_table" in violations[0]


def test_linter_detects_quoted_hourly_observations_identifier(tmp_path):
    """P0_unsafe_table catches quoted legacy table identifiers."""
    bad_file = tmp_path / "bad_hourly_quoted.py"
    bad_file.write_text(
        'rows = conn.execute(\'SELECT * FROM "hourly_observations"\')\n'
    )
    violations = _check_legacy_hourly_observations_select(
        bad_file, bad_file.read_text()
    )
    assert len(violations) == 1
    assert "P0_unsafe_table" in violations[0]


def test_linter_detects_block_comment_before_hourly_observations(tmp_path):
    """P0_unsafe_table catches SQL comments inserted before the table name."""
    bad_file = tmp_path / "bad_hourly_block_comment.py"
    bad_file.write_text(
        'rows = conn.execute("SELECT * FROM /* legacy */ hourly_observations")\n'
    )
    violations = _check_legacy_hourly_observations_select(
        bad_file, bad_file.read_text()
    )
    assert len(violations) == 1
    assert "P0_unsafe_table" in violations[0]


def test_linter_detects_schema_qualified_hourly_observations(tmp_path):
    """P0_unsafe_table catches schema-qualified legacy table references."""
    bad_file = tmp_path / "bad_hourly_schema_qualified.py"
    bad_file.write_text(
        'rows = conn.execute("SELECT * FROM main.hourly_observations")\n'
    )
    violations = _check_legacy_hourly_observations_select(
        bad_file, bad_file.read_text()
    )
    assert len(violations) == 1
    assert "P0_unsafe_table" in violations[0]


def test_linter_detects_adjacent_literal_hourly_observations_query(tmp_path):
    """P0_unsafe_table catches Python-adjacent SQL string literals."""
    bad_file = tmp_path / "bad_hourly_adjacent_literal.py"
    bad_file.write_text(
        'rows = conn.execute("SELECT * FROM " "hourly_observations")\n'
    )
    violations = _check_legacy_hourly_observations_select(
        bad_file, bad_file.read_text()
    )
    assert len(violations) == 1
    assert "P0_unsafe_table" in violations[0]


def test_linter_detects_concatenated_literal_hourly_observations_query(tmp_path):
    """P0_unsafe_table catches literal SQL assembled with string addition."""
    bad_file = tmp_path / "bad_hourly_concat_literal.py"
    bad_file.write_text(
        'rows = conn.execute("SELECT * FROM " + "hourly_observations")\n'
    )
    violations = _check_legacy_hourly_observations_select(
        bad_file, bad_file.read_text()
    )
    assert len(violations) == 1
    assert "P0_unsafe_table" in violations[0]


def test_linter_detects_keyword_literal_hourly_observations_query(tmp_path):
    """P0_unsafe_table catches literal SQL assembled in keyword arguments."""
    bad_file = tmp_path / "bad_hourly_keyword_literal.py"
    bad_file.write_text(
        'rows = pd.read_sql(sql="SELECT * FROM " + "hourly_observations", con=conn)\n'
    )
    violations = _check_legacy_hourly_observations_select(
        bad_file, bad_file.read_text()
    )
    assert len(violations) == 1
    assert "P0_unsafe_table" in violations[0]


def test_linter_detects_format_literal_hourly_observations_query(tmp_path):
    """P0_unsafe_table catches SQL literal assembly through str.format."""
    bad_file = tmp_path / "bad_hourly_format_literal.py"
    bad_file.write_text(
        'rows = conn.execute("SELECT * FROM {}".format("hourly_observations"))\n'
    )
    violations = _check_legacy_hourly_observations_select(
        bad_file, bad_file.read_text()
    )
    assert len(violations) == 1
    assert "P0_unsafe_table" in violations[0]


def test_linter_detects_percent_literal_hourly_observations_query(tmp_path):
    """P0_unsafe_table catches SQL literal assembly through percent formatting."""
    bad_file = tmp_path / "bad_hourly_percent_literal.py"
    bad_file.write_text(
        'rows = conn.execute("SELECT * FROM %s" % "hourly_observations")\n'
    )
    violations = _check_legacy_hourly_observations_select(
        bad_file, bad_file.read_text()
    )
    assert len(violations) == 1
    assert "P0_unsafe_table" in violations[0]


def test_linter_detects_percent_mapping_hourly_observations_query(tmp_path):
    """P0_unsafe_table catches percent formatting with literal mappings."""
    bad_file = tmp_path / "bad_hourly_percent_mapping.py"
    bad_file.write_text(
        'rows = conn.execute("SELECT * FROM %(table)s" % {"table": "hourly_observations"})\n'
    )
    violations = _check_legacy_hourly_observations_select(
        bad_file, bad_file.read_text()
    )
    assert len(violations) == 1
    assert "P0_unsafe_table" in violations[0]


def test_linter_detects_variable_percent_mapping_hourly_observations_query(tmp_path):
    """P0_unsafe_table catches percent formatting with constant mapping variables."""
    bad_file = tmp_path / "bad_hourly_percent_mapping_variable.py"
    bad_file.write_text(
        'mapping = {"table": "hourly_observations"}\n'
        'rows = conn.execute("SELECT * FROM %(table)s" % mapping)\n'
    )
    violations = _check_legacy_hourly_observations_select(
        bad_file, bad_file.read_text()
    )
    assert len(violations) == 1
    assert "P0_unsafe_table" in violations[0]


def test_linter_detects_variable_backed_fstring_hourly_observations_query(tmp_path):
    """P0_unsafe_table catches f-strings backed by constant table names."""
    bad_file = tmp_path / "bad_hourly_fstring_literal.py"
    bad_file.write_text(
        'table = "hourly_observations"\n'
        'rows = conn.execute(f"SELECT * FROM {table}")\n'
    )
    violations = _check_legacy_hourly_observations_select(
        bad_file, bad_file.read_text()
    )
    assert len(violations) == 1
    assert "P0_unsafe_table" in violations[0]


def test_linter_detects_format_unpack_mapping_hourly_observations_query(tmp_path):
    """P0_unsafe_table catches str.format with unpacked literal mappings."""
    bad_file = tmp_path / "bad_hourly_format_unpack_mapping.py"
    bad_file.write_text(
        'rows = conn.execute("SELECT * FROM {table}".format(**{"table": "hourly_observations"}))\n'
    )
    violations = _check_legacy_hourly_observations_select(
        bad_file, bad_file.read_text()
    )
    assert len(violations) == 1
    assert "P0_unsafe_table" in violations[0]


def test_linter_detects_format_map_hourly_observations_query(tmp_path):
    """P0_unsafe_table catches format_map with constant mapping variables."""
    bad_file = tmp_path / "bad_hourly_format_map.py"
    bad_file.write_text(
        'mapping = {"table": "hourly_observations"}\n'
        'rows = conn.execute("SELECT * FROM {table}".format_map(mapping))\n'
    )
    violations = _check_legacy_hourly_observations_select(
        bad_file, bad_file.read_text()
    )
    assert len(violations) == 1
    assert "P0_unsafe_table" in violations[0]


def test_linter_allows_evidence_hourly_view_reference(tmp_path):
    """Evidence adapters may reference an explicit evidence view name."""
    adapter_file = tmp_path / "adapter.py"
    adapter_file.write_text(
        'rows = conn.execute("SELECT * FROM v_evidence_hourly_observations")\n'
    )
    violations = _check_legacy_hourly_observations_select(
        adapter_file, adapter_file.read_text()
    )
    assert violations == []


def test_linter_allows_hourly_observations_compatibility_writer(tmp_path):
    """The legacy compatibility writer remains allowlisted by exact filename."""
    writer_file = PROJECT_ROOT / "scripts" / "etl_hourly_observations.py"
    content = 'rows = conn.execute("SELECT * FROM hourly_observations")\n'
    violations = _check_legacy_hourly_observations_select(writer_file, content)
    assert violations == []


def test_linter_does_not_allow_hourly_observations_by_basename_only(tmp_path):
    """A non-canonical path cannot bypass P0_unsafe_table by filename."""
    other_dir = tmp_path / "src"
    other_dir.mkdir()
    writer_file = other_dir / "etl_hourly_observations.py"
    writer_file.write_text(
        'rows = conn.execute("SELECT * FROM hourly_observations")\n'
    )
    violations = _check_legacy_hourly_observations_select(
        writer_file, writer_file.read_text()
    )
    assert len(violations) == 1
    assert "P0_unsafe_table" in violations[0]


def test_linter_allows_hourly_observations_in_tests_dir(tmp_path):
    """Tests may include legacy hourly table fixture/probe SQL."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    test_file = tests_dir / "test_hourly_fixture.py"
    test_file.write_text(
        'rows = conn.execute("SELECT * FROM hourly_observations")\n'
    )
    violations = _check_legacy_hourly_observations_select(
        test_file, test_file.read_text()
    )
    assert violations == []


def test_linter_allows_hourly_observations_in_migrations_dir(tmp_path):
    """Migrations may inspect legacy hourly tables during schema transitions."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    migration_file = migrations_dir / "001_hourly.py"
    migration_file.write_text(
        'rows = conn.execute("SELECT * FROM hourly_observations")\n'
    )
    violations = _check_legacy_hourly_observations_select(
        migration_file, migration_file.read_text()
    )
    assert violations == []


def test_linter_ignores_hourly_observations_in_comments(tmp_path):
    """Comment-only references do not trip the P0 unsafe-table rule."""
    commented_file = tmp_path / "commented_hourly.py"
    commented_file.write_text(
        '# SELECT * FROM hourly_observations -- legacy note only\n'
        'x = 1\n'
    )
    violations = _check_legacy_hourly_observations_select(
        commented_file, commented_file.read_text()
    )
    assert violations == []


# ---------------------------------------------------------------------------
# H3 lint rule: settlements reads must pin temperature_metric
# ---------------------------------------------------------------------------

def test_h3_flags_bare_join_settlements(tmp_path):
    """H3 fires when `JOIN settlements` has no temperature_metric filter."""
    bad_file = tmp_path / "bad_join.py"
    bad_file.write_text(
        'rows = conn.execute("""\n'
        '    SELECT f.city, s.settlement_value\n'
        '    FROM historical_forecasts f\n'
        '    JOIN settlements s ON f.city = s.city AND f.target_date = s.target_date\n'
        '""").fetchall()\n'
    )
    violations = _check_settlements_metric_filter(
        bad_file, bad_file.read_text()
    )
    assert len(violations) == 1, violations
    assert "temperature_metric" in violations[0]


def test_h3_accepts_metric_filtered_join(tmp_path):
    """H3 passes when the SQL literal includes temperature_metric."""
    good_file = tmp_path / "good_join.py"
    good_file.write_text(
        'rows = conn.execute("""\n'
        '    SELECT f.city, s.settlement_value\n'
        '    FROM historical_forecasts f\n'
        '    JOIN settlements s\n'
        '      ON f.city = s.city\n'
        '     AND f.target_date = s.target_date\n'
        '     AND s.temperature_metric = \'high\'\n'
        '""").fetchall()\n'
    )
    violations = _check_settlements_metric_filter(
        good_file, good_file.read_text()
    )
    assert violations == []


def test_h3_flags_bare_from_settlements(tmp_path):
    """H3 fires on `FROM settlements` (no JOIN) without metric filter."""
    bad_file = tmp_path / "bad_from.py"
    bad_file.write_text(
        'row = conn.execute(\n'
        '    "SELECT settlement_value FROM settlements WHERE city = ? AND target_date = ?",\n'
        '    ("NYC", "2026-04-24"),\n'
        ').fetchone()\n'
    )
    violations = _check_settlements_metric_filter(
        bad_file, bad_file.read_text()
    )
    assert len(violations) == 1, violations


def test_h3_accepts_from_settlements_with_metric(tmp_path):
    """H3 passes on FROM settlements when metric filter is present."""
    good_file = tmp_path / "good_from.py"
    good_file.write_text(
        'row = conn.execute(\n'
        '    "SELECT settlement_value FROM settlements "\n'
        '    "WHERE city = ? AND target_date = ? AND temperature_metric = \'high\'",\n'
        '    ("NYC", "2026-04-24"),\n'
        ').fetchone()\n'
    )
    violations = _check_settlements_metric_filter(
        good_file, good_file.read_text()
    )
    assert violations == []


def test_h3_ignores_non_settlements_tables(tmp_path):
    """H3 does not fire on tables whose name merely starts with `settlements`."""
    ok_file = tmp_path / "non_settlements.py"
    ok_file.write_text(
        'row = conn.execute(\n'
        '    "SELECT * FROM settlements_authority_monotonic WHERE id = ?",\n'
        '    (1,),\n'
        ').fetchone()\n'
    )
    violations = _check_settlements_metric_filter(
        ok_file, ok_file.read_text()
    )
    assert violations == []


def test_h3_ignores_docstring_mentions(tmp_path):
    """H3 must not fire on JOIN settlements mentioned in prose/docstrings."""
    ok_file = tmp_path / "docstring_only.py"
    ok_file.write_text(
        '"""This function documents that it computes MAE from\n'
        'historical_forecasts JOIN settlements — but does not execute SQL."""\n'
        'x = 1\n'
    )
    violations = _check_settlements_metric_filter(
        ok_file, ok_file.read_text()
    )
    assert violations == []


def test_h3_skips_allowlisted_writer(tmp_path, monkeypatch):
    """Allowlisted files (writers, audit tools) are exempt from H3."""
    from scripts import semantic_linter as linter_mod
    # Simulate harvester.py (writer path).
    writer_file = (PROJECT_ROOT / "src/execution/harvester.py")
    assert writer_file.exists()
    violations = _check_settlements_metric_filter(
        writer_file, writer_file.read_text()
    )
    assert violations == [], "harvester.py (allowlisted writer) should not trip H3"


def test_h3_skips_tests_directory(tmp_path):
    """Test files are exempt from H3."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    test_file = tests_dir / "test_sample.py"
    test_file.write_text(
        'rows = conn.execute(\n'
        '    "SELECT * FROM settlements WHERE city = ?", ("NYC",)\n'
        ').fetchall()\n'
    )
    violations = _check_settlements_metric_filter(
        test_file, test_file.read_text()
    )
    assert violations == []


def test_h3_skips_migrations_directory(tmp_path):
    """Migration files are exempt — schema changes cross all metrics."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    migration_file = migrations_dir / "001_rebuild_settlements.py"
    migration_file.write_text(
        'rows = conn.execute(\n'
        '    "SELECT COUNT(*) FROM settlements WHERE authority IS NOT NULL",\n'
        ').fetchone()\n'
    )
    violations = _check_settlements_metric_filter(
        migration_file, migration_file.read_text()
    )
    assert violations == [], "migrations/ is dir-level exempt from H3"


def test_h3_skips_scripts_directory(tmp_path):
    """scripts/ is dir-level carve-out per T2-S3 scope decision."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    audit_file = scripts_dir / "investigate_settlements.py"
    audit_file.write_text(
        'rows = conn.execute(\n'
        '    "SELECT city, settlement_value FROM settlements WHERE target_date = ?",\n'
        '    ("2026-04-24",),\n'
        ').fetchall()\n'
    )
    violations = _check_settlements_metric_filter(
        audit_file, audit_file.read_text()
    )
    assert violations == [], (
        "scripts/ is carved out at directory level; canonical-path "
        "training scripts will be promoted via T2-S3-followup-SCRIPTS"
    )
