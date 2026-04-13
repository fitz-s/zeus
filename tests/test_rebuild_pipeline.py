"""K4 rebuild pipeline end-to-end test.

Exercises the full rebuild pipeline on a synthetic fixture:
observations (VERIFIED) -> settlements -> calibration_pairs.

NOTE: Full platt refit is NOT exercised here because it requires
>15 calibration pairs with sklearn. The platt refit is exercised
by test_authority_gate.py unit tests. A full E2E platt test is
TODO for Round 5 (static E2E simulation).

All tests use tmp_path fixtures. NO writes to production DB.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent


def _make_tmp_db(tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    """Create a fresh test DB with Zeus schema + authority columns."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.state.db import init_schema

    db_path = tmp_path / "test_world.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    init_schema(conn)

    # Add authority columns (worktree shim until db.py includes them)
    for table, default in [
        ("calibration_pairs", "UNVERIFIED"),
        ("settlements", "UNVERIFIED"),
        ("platt_models", "UNVERIFIED"),
        ("ensemble_snapshots", "VERIFIED"),
    ]:
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        cols = {row[1] for row in info}
        if "authority" not in cols:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN "
                f"authority TEXT NOT NULL DEFAULT '{default}'"
            )
    conn.commit()
    return conn, db_path


# ---------------------------------------------------------------------------
# Full pipeline: observations -> settlements -> calibration_pairs
# ---------------------------------------------------------------------------

def test_rebuild_settlements_end_to_end(tmp_path):
    """Full settlements rebuild from VERIFIED observations.

    Seeds 3 VERIFIED + 2 UNVERIFIED observations for NYC.
    Asserts settlements has exactly 3 rows, all VERIFIED.
    """
    conn, db_path = _make_tmp_db(tmp_path)

    # Seed 3 VERIFIED observations
    for i in range(3):
        conn.execute(
            "INSERT INTO observations "
            "(city, target_date, source, high_temp, low_temp, unit, authority) "
            "VALUES ('NYC', ?, 'wu_icao', 85.0, 70.0, 'F', 'VERIFIED')",
            (f"2025-07-{i+1:02d}",),
        )
    # Seed 2 UNVERIFIED observations (must NOT become settlements)
    for i in range(2):
        conn.execute(
            "INSERT INTO observations "
            "(city, target_date, source, high_temp, low_temp, unit, authority) "
            "VALUES ('NYC', ?, 'wu_icao', 75.0, 60.0, 'F', 'UNVERIFIED')",
            (f"2025-08-{i+1:02d}",),
        )
    conn.commit()
    conn.close()

    from scripts.rebuild_settlements import rebuild_settlements
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    summary = rebuild_settlements(conn2, dry_run=False, city_filter="NYC")
    conn2.commit()

    rows = conn2.execute(
        "SELECT authority FROM settlements WHERE city='NYC'"
    ).fetchall()
    conn2.close()

    assert len(rows) == 3, f"Expected 3 settlements, got {len(rows)}"
    assert all(r["authority"] == "VERIFIED" for r in rows)
    assert summary["rows_skipped"] == 0


def test_rebuild_calibration_end_to_end(tmp_path):
    """Calibration rebuild: VERIFIED snapshots x VERIFIED settlements -> VERIFIED pairs.

    Seeds 3 VERIFIED snapshots and 3 VERIFIED settlements for NYC.
    Asserts all resulting calibration_pairs have authority='VERIFIED'.
    No UNVERIFIED rows in output.
    """
    conn, db_path = _make_tmp_db(tmp_path)

    # Seed NYC with market_events bins so _get_bins_for_city finds real bins
    for i in range(3):
        target = f"2025-07-{i+1:02d}"
        # Add market_events bins for NYC
        for bin_low, bin_high, label in [
            (None, 82.0, "82F or below"),
            (83.0, 84.0, "83-84F"),
            (85.0, 86.0, "85-86F"),
            (87.0, 88.0, "87-88F"),
            (89.0, None, "89F or above"),
        ]:
            conn.execute(
                "INSERT OR IGNORE INTO market_events "
                "(market_slug, city, target_date, range_label, range_low, range_high, outcome) "
                "VALUES (?, 'NYC', ?, ?, ?, ?, 'YES')",
                (f"nyc-{target}-{label}", target, label, bin_low, bin_high),
            )

        # Add VERIFIED snapshot (51 members)
        members = [85.0 + j * 0.1 for j in range(51)]
        conn.execute(
            "INSERT OR IGNORE INTO ensemble_snapshots "
            "(city, target_date, issue_time, valid_time, available_at, "
            " fetch_time, lead_hours, members_json, model_version, data_version, authority) "
            "VALUES ('NYC', ?, ?, ?, ?, ?, 48.0, ?, 'ecmwf_tigge', 'v1', 'VERIFIED')",
            (
                target,
                f"{target}T00:00:00Z",
                f"{target}T12:00:00Z",
                f"{target}T06:00:00Z",
                f"{target}T07:00:00Z",
                json.dumps(members),
            ),
        )

        # Add VERIFIED settlement
        conn.execute(
            "INSERT OR IGNORE INTO settlements "
            "(city, target_date, settlement_value, settlement_source, settled_at, authority) "
            "VALUES ('NYC', ?, 85.0, 'wu_icao_rebuild', '2025-07-01T12:00:00Z', 'VERIFIED')",
            (target,),
        )

    conn.commit()
    conn.close()

    from scripts.rebuild_calibration import rebuild_calibration
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    summary = rebuild_calibration(conn2, dry_run=False, city_filter="NYC")
    conn2.commit()

    pairs = conn2.execute(
        "SELECT authority FROM calibration_pairs WHERE city='NYC'"
    ).fetchall()
    conn2.close()

    assert len(pairs) > 0, "Expected calibration pairs to be written"
    assert all(r["authority"] == "VERIFIED" for r in pairs), (
        "All rebuilt calibration_pairs must have authority='VERIFIED'"
    )
    assert summary["rows_skipped"] == 0


def test_rebuild_pipeline_skips_unverified_snapshots(tmp_path):
    """Property: snapshots with authority=UNVERIFIED are not processed."""
    conn, db_path = _make_tmp_db(tmp_path)

    members = json.dumps([85.0] * 51)
    # Seed 1 VERIFIED + 1 UNVERIFIED snapshot
    for auth, target in [("VERIFIED", "2025-07-01"), ("UNVERIFIED", "2025-07-02")]:
        conn.execute(
            "INSERT OR IGNORE INTO ensemble_snapshots "
            "(city, target_date, issue_time, valid_time, available_at, "
            " fetch_time, lead_hours, members_json, model_version, data_version, authority) "
            "VALUES ('NYC', ?, ?, ?, ?, ?, 48.0, ?, 'ecmwf_tigge', 'v1', ?)",
            (
                target,
                f"{target}T00:00:00Z",
                f"{target}T12:00:00Z",
                f"{target}T06:00:00Z",
                f"{target}T07:00:00Z",
                members,
                auth,
            ),
        )

    # Settlement for both dates
    for target in ["2025-07-01", "2025-07-02"]:
        conn.execute(
            "INSERT OR IGNORE INTO settlements "
            "(city, target_date, settlement_value, settlement_source, settled_at, authority) "
            "VALUES ('NYC', ?, 85.0, 'wu_icao_rebuild', '2025-07-01T12:00:00Z', 'VERIFIED')",
            (target,),
        )
    conn.commit()
    conn.close()

    from scripts.rebuild_calibration import rebuild_calibration
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    summary = rebuild_calibration(conn2, dry_run=False, city_filter="NYC")

    # Only 1 snapshot processed (VERIFIED one); UNVERIFIED skipped by WHERE clause
    assert summary["rows_processed"] == 1, (
        f"Only VERIFIED snapshots should be processed, got {summary['rows_processed']}"
    )
    conn2.close()


def test_rebuild_settlements_is_idempotent(tmp_path):
    """Property: running rebuild_settlements twice produces the same rows."""
    conn, db_path = _make_tmp_db(tmp_path)

    conn.execute(
        "INSERT INTO observations "
        "(city, target_date, source, high_temp, low_temp, unit, authority) "
        "VALUES ('NYC', '2025-07-01', 'wu_icao', 85.0, 70.0, 'F', 'VERIFIED')"
    )
    conn.commit()
    conn.close()

    from scripts.rebuild_settlements import rebuild_settlements

    # First run
    conn1 = sqlite3.connect(str(db_path))
    conn1.row_factory = sqlite3.Row
    rebuild_settlements(conn1, dry_run=False, city_filter="NYC")
    conn1.commit()
    count1 = conn1.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
    val1 = conn1.execute("SELECT settlement_value FROM settlements LIMIT 1").fetchone()[0]
    conn1.close()

    # Second run
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    rebuild_settlements(conn2, dry_run=False, city_filter="NYC")
    conn2.commit()
    count2 = conn2.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
    val2 = conn2.execute("SELECT settlement_value FROM settlements LIMIT 1").fetchone()[0]
    conn2.close()

    assert count1 == count2, f"Row count changed on second run: {count1} -> {count2}"
    assert val1 == val2, f"Settlement value changed: {val1} -> {val2}"
