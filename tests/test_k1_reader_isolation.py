# Created: 2026-05-17
# Last reused/audited: 2026-05-17
# Authority basis: F40/F41 K1-reader regressions — prevent world-DB direct access
# for forecast_class tables in scripts that cross DB boundaries post-K1-split.
# See docs/operations/task_2026-05-17_post_karachi_remediation/FIX_K1_READERS.md §C
"""Antibody: scripts that were K1-misrouted must not regress to world-DB direct access.

Scope: F40 (bridge_oracle_to_calibration) + F41 (evaluate_calibration_transfer_oos).
These two scripts were confirmed to read forecast_class tables via get_world_connection
before the K1 fix. This test prevents regression.

Broader codebase sweep (37+ scripts) is deferred — those are pre-K1 backfill scripts
whose world-DB access was correct under the legacy single-DB schema and requires
separate per-script migration work.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

# Tables that are exclusively forecast_class post-K1-split (not ghost-copied to world)
# Per architecture/db_table_ownership.yaml: forecast_class entries with no world ghost.
FORECAST_ONLY_TABLES = {
    "calibration_pairs_v2",   # 91M rows in forecasts.db; ghost on world is legacy_archived
    "source_run",
    "source_run_coverage",
    "readiness_state",
    "market_events_v2",
    "ensemble_snapshots_v2",
    "job_run",
}

# Patterns that indicate direct world-DB access (not cross-DB helper)
BAD_PATTERNS = [
    r'sqlite3\.connect\([^)]*zeus-world\.db',  # direct sqlite3.connect to world
    r'get_world_connection\b',                  # bare world connection helper
]

# Scripts confirmed fixed by F40+F41 — must NOT have BAD_PATTERNS + forecast table
K1_FIXED_SCRIPTS = [
    "bridge_oracle_to_calibration.py",
    "evaluate_calibration_transfer_oos.py",
]


@pytest.mark.parametrize("script_name", K1_FIXED_SCRIPTS)
def test_k1_fixed_script_does_not_use_world_for_forecast_tables(script_name):
    """Regression: F40/F41 scripts must not re-acquire direct world-DB access
    for forecast_class tables after K1 fix."""
    script = SCRIPTS / script_name
    assert script.exists(), f"Script not found: {script}"

    src = script.read_text()

    uses_bad = any(re.search(p, src) for p in BAD_PATTERNS)
    uses_forecast_table = any(t in src for t in FORECAST_ONLY_TABLES)

    assert not (uses_bad and uses_forecast_table), (
        f"{script_name}: regressed — uses world-DB direct access AND references "
        f"a forecast_class table. Use get_forecasts_connection_with_world() instead.\n"
        f"Bad patterns found: {[p for p in BAD_PATTERNS if re.search(p, src)]}\n"
        f"Forecast tables referenced: {[t for t in FORECAST_ONLY_TABLES if t in src]}"
    )


def test_bridge_uses_forecasts_connection_with_world():
    """F40 structural check: bridge must use the cross-DB context manager."""
    src = (SCRIPTS / "bridge_oracle_to_calibration.py").read_text()
    assert "get_forecasts_connection_with_world" in src, (
        "bridge_oracle_to_calibration.py must use get_forecasts_connection_with_world()"
    )
    assert "DB_PATH" not in src or "DB_PATH removed" in src, (
        "DB_PATH constant must be removed from bridge_oracle_to_calibration.py (K1 F40)"
    )


def test_cal_transfer_eval_uses_forecasts_connection_with_world():
    """F41 structural check: evaluate_calibration_transfer_oos must use cross-DB helper."""
    src = (SCRIPTS / "evaluate_calibration_transfer_oos.py").read_text()
    assert "get_forecasts_connection_with_world" in src, (
        "evaluate_calibration_transfer_oos.py must use get_forecasts_connection_with_world()"
    )
    assert "get_world_connection" not in src or src.count("get_world_connection") == 0, (
        "evaluate_calibration_transfer_oos.py must not use get_world_connection() (K1 F41)"
    )


def test_cal_transfer_eval_qualifies_world_table_inserts():
    """F41 correctness: INSERT into world-class table must be qualified as world.*
    so it resolves to the ATTACHed world.db under get_forecasts_connection_with_world."""
    src = (SCRIPTS / "evaluate_calibration_transfer_oos.py").read_text()
    # Must NOT have bare INSERT INTO validated_calibration_transfers
    bare_insert = re.search(
        r'INSERT\s+INTO\s+validated_calibration_transfers\b(?!\s*\(|[^.]*world\.)',
        src,
    )
    assert bare_insert is None, (
        "evaluate_calibration_transfer_oos.py: bare INSERT INTO validated_calibration_transfers "
        "found — must be qualified as world.validated_calibration_transfers since "
        "get_forecasts_connection_with_world uses forecasts.db as MAIN."
    )
    # Must have the qualified form
    assert "world.validated_calibration_transfers" in src, (
        "evaluate_calibration_transfer_oos.py: INSERT must use world.validated_calibration_transfers"
    )
