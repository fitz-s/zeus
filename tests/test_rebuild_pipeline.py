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

    for i in range(3):
        target = f"2025-07-{i+1:02d}"
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

        conn.execute(
            "INSERT INTO observations "
            "(city, target_date, source, high_temp, low_temp, unit, authority) "
            "VALUES ('NYC', ?, 'wu_icao_history', 85.0, 70.0, 'F', 'VERIFIED')",
            (target,),
        )

    conn.commit()
    conn.close()

    from scripts.rebuild_calibration_pairs_canonical import rebuild
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    summary = rebuild(
        conn2,
        dry_run=False,
        force=True,
        city_filter="NYC",
        n_mc=25,
        allow_unaudited_ensemble=True,
    )
    conn2.commit()

    pairs = conn2.execute(
        "SELECT authority FROM calibration_pairs WHERE city='NYC'"
    ).fetchall()
    conn2.close()

    assert len(pairs) == 276, f"Expected exactly 276 calibration pairs, got {len(pairs)}"
    assert all(r["authority"] == "VERIFIED" for r in pairs), (
        "All rebuilt calibration_pairs must have authority='VERIFIED'"
    )
    assert summary.snapshots_processed == 3
    assert summary.pairs_written == 276


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

    for target in ["2025-07-01", "2025-07-02"]:
        conn.execute(
            "INSERT INTO observations "
            "(city, target_date, source, high_temp, low_temp, unit, authority) "
            "VALUES ('NYC', ?, 'wu_icao_history', 85.0, 70.0, 'F', 'VERIFIED')",
            (target,),
        )
    conn.commit()
    conn.close()

    from scripts.rebuild_calibration_pairs_canonical import rebuild
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    summary = rebuild(
        conn2,
        dry_run=False,
        force=True,
        city_filter="NYC",
        n_mc=10,
        allow_unaudited_ensemble=True,
    )

    # Only 1 snapshot processed (VERIFIED one); UNVERIFIED skipped by WHERE clause
    assert summary.snapshots_scanned == 1
    assert summary.snapshots_processed == 1
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


# ---------------------------------------------------------------------------
# test_refit_writes_authority_verified (C1 test)
# ---------------------------------------------------------------------------

def test_refit_writes_authority_verified(tmp_path):
    """C1 fix: refit_platt writes authority='VERIFIED' explicitly in INSERT column list.

    Runs refit_platt against a fixture, SELECTs from platt_models,
    asserts all rows have authority='VERIFIED'.
    """
    conn, db_path = _make_tmp_db(tmp_path)

    # Add authority column to platt_models if not already present
    info = conn.execute("PRAGMA table_info(platt_models)").fetchall()
    cols = {row[1] for row in info}
    if "authority" not in cols:
        conn.execute(
            "ALTER TABLE platt_models ADD COLUMN "
            "authority TEXT NOT NULL DEFAULT 'UNVERIFIED'"
        )

    # Seed 15 complete canonical decision groups for one bucket.
    for group_idx in range(15):
        target_date = f"2025-07-{group_idx+1:02d}"
        group_id = f"canonical-group-{group_idx}"
        for bin_idx in range(92):
            conn.execute(
                "INSERT INTO calibration_pairs "
                "(city, target_date, range_label, p_raw, outcome, lead_days, "
                " season, cluster, forecast_available_at, settlement_value, "
                " decision_group_id, bias_corrected, authority, bin_source) "
                "VALUES ('NYC', ?, ?, ?, ?, 2.0, 'JJA', 'NYC', "
                " '2025-06-01T06:00:00Z', 85.0, ?, 0, 'VERIFIED', 'canonical_v1')",
                (
                    target_date,
                    f"{bin_idx * 2}-{bin_idx * 2 + 1}°F",
                    0.4 if bin_idx == group_idx % 92 else 0.01,
                    1 if bin_idx == group_idx % 92 else 0,
                    group_id,
                ),
            )
    conn.commit()
    conn.close()

    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from scripts.refit_platt import refit_all
    import unittest.mock as mock

    # Patch get_connection to return our test db
    def _get_test_conn():
        import sqlite3 as _sqlite3
        c = _sqlite3.connect(str(db_path))
        c.row_factory = _sqlite3.Row
        return c

    with mock.patch("scripts.refit_platt.get_connection", side_effect=_get_test_conn):
        with mock.patch("scripts.refit_platt.init_schema"):
            refit_all()

    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    rows = conn2.execute(
        "SELECT authority FROM platt_models WHERE is_active = 1"
    ).fetchall()
    conn2.close()

    assert len(rows) == 1, "Expected exactly 1 active platt_model row after refit  # Deterministic fixture count; see K4.5.1 commit 3"
    assert all(r["authority"] == "VERIFIED" for r in rows), (
        f"All platt_models must have authority='VERIFIED' after refit. "
        f"Got: {[r['authority'] for r in rows]}"
    )


# ---------------------------------------------------------------------------
# test_rows_written_reflects_actual_inserts (H4 test)
# ---------------------------------------------------------------------------

def test_rows_written_reflects_actual_inserts(tmp_path):
    """Canonical rebuild reports actual writes and does not accumulate duplicates."""
    conn, db_path = _make_tmp_db(tmp_path)

    for i in range(3):
        target = f"2025-07-{i+1:02d}"
        members = [85.0 + j * 0.1 for j in range(51)]
        conn.execute(
            "INSERT OR IGNORE INTO ensemble_snapshots "
            "(city, target_date, issue_time, valid_time, available_at, "
            " fetch_time, lead_hours, members_json, model_version, data_version, authority) "
            "VALUES ('NYC', ?, ?, ?, ?, ?, 48.0, ?, 'ecmwf_tigge', 'v1', 'VERIFIED')",
            (
                target, f"{target}T00:00:00Z", f"{target}T12:00:00Z",
                f"{target}T06:00:00Z", f"{target}T07:00:00Z",
                json.dumps(members),
            ),
        )
        conn.execute(
            "INSERT INTO observations "
            "(city, target_date, source, high_temp, low_temp, unit, authority) "
            "VALUES ('NYC', ?, 'wu_icao_history', 85.0, 70.0, 'F', 'VERIFIED')",
            (target,),
        )
    conn.commit()
    conn.close()

    from scripts.rebuild_calibration_pairs_canonical import rebuild

    # First run
    conn1 = sqlite3.connect(str(db_path))
    conn1.row_factory = sqlite3.Row
    summary1 = rebuild(
        conn1,
        dry_run=False,
        force=True,
        city_filter="NYC",
        n_mc=25,
        allow_unaudited_ensemble=True,
    )
    conn1.commit()
    row_count1 = conn1.execute("SELECT COUNT(*) FROM calibration_pairs").fetchone()[0]
    conn1.close()

    assert summary1.pairs_written == 276
    assert row_count1 == 276

    # Second run deletes and rebuilds the canonical slice, but does not accumulate duplicates.
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    summary2 = rebuild(
        conn2,
        dry_run=False,
        force=True,
        city_filter="NYC",
        n_mc=25,
        allow_unaudited_ensemble=True,
    )
    conn2.commit()
    row_count2 = conn2.execute("SELECT COUNT(*) FROM calibration_pairs").fetchone()[0]
    conn2.close()

    assert summary2.pairs_written == 276
    assert row_count2 == 276


# ---------------------------------------------------------------------------
# test_rebuild_calibration_db_error_is_not_swallowed (M6 test)
# ---------------------------------------------------------------------------

def test_rebuild_calibration_db_error_is_not_swallowed(tmp_path):
    import sqlite3 as _sqlite3
    from unittest.mock import patch
    import scripts.rebuild_calibration_pairs_canonical as canonical

    conn, db_path = _make_tmp_db(tmp_path)

    target = "2025-07-01"
    members = [85.0 + j * 0.1 for j in range(51)]
    conn.execute(
        "INSERT OR IGNORE INTO ensemble_snapshots "
        "(city, target_date, issue_time, valid_time, available_at, "
        " fetch_time, lead_hours, members_json, model_version, data_version, authority) "
        "VALUES ('NYC', ?, ?, ?, ?, ?, 48.0, ?, 'ecmwf_tigge', 'v1', 'VERIFIED')",
        (
            target, f"{target}T00:00:00Z", f"{target}T12:00:00Z",
            f"{target}T06:00:00Z", f"{target}T07:00:00Z",
            json.dumps(members),
        ),
    )
    conn.execute(
        "INSERT INTO observations "
        "(city, target_date, source, high_temp, low_temp, unit, authority) "
        "VALUES ('NYC', ?, 'wu_icao_history', 85.0, 70.0, 'F', 'VERIFIED')",
        (target,),
    )
    conn.commit()
    conn.close()

    real_conn = _sqlite3.connect(str(db_path))
    real_conn.row_factory = _sqlite3.Row
    with patch.object(canonical, "add_calibration_pair", side_effect=_sqlite3.IntegrityError("injected")):
        with pytest.raises(_sqlite3.IntegrityError):
            canonical.rebuild(
                real_conn,
                dry_run=False,
                force=True,
                city_filter="NYC",
                n_mc=10,
                allow_unaudited_ensemble=True,
            )
    remaining = real_conn.execute("SELECT COUNT(*) FROM calibration_pairs").fetchone()[0]
    real_conn.close()
    assert remaining == 0


# ---------------------------------------------------------------------------
# K3_struct E2E: synthetic-bin path uses real degree symbol (C2 regression guard)
# ---------------------------------------------------------------------------

def test_rebuild_calibration_synthetic_bins_use_real_degree_symbol(tmp_path):
    """Canonical bins use real degree symbols, not the escaped string u00b0."""
    conn, db_path = _make_tmp_db(tmp_path)

    target = "2025-07-01"
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
    conn.execute(
        "INSERT INTO observations "
        "(city, target_date, source, high_temp, low_temp, unit, authority) "
        "VALUES ('NYC', ?, 'wu_icao_history', 85.0, 70.0, 'F', 'VERIFIED')",
        (target,),
    )
    conn.commit()
    conn.close()

    from scripts.rebuild_calibration_pairs_canonical import rebuild
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    rebuild(
        conn2,
        dry_run=False,
        force=True,
        city_filter="NYC",
        n_mc=10,
        allow_unaudited_ensemble=True,
    )
    conn2.commit()

    labels = [
        row[0]
        for row in conn2.execute(
            "SELECT range_label FROM calibration_pairs WHERE city='NYC'"
        ).fetchall()
    ]
    conn2.close()

    assert len(labels) > 0, "Expected at least some calibration_pairs from synthetic bins"
    # At least one synthetic bin label should have a real degree symbol
    labels_with_degree = [l for l in labels if "\u00b0" in l]
    labels_with_escaped = [l for l in labels if "u00b0" in l]
    assert len(labels_with_degree) > 0, (
        f"C2 fix: expected real \u00b0 in range_labels. Got labels: {labels}"
    )
    assert len(labels_with_escaped) == 0, (
        f"C2 regression: found literal 'u00b0' in range_labels: {labels_with_escaped}"
    )


# ---------------------------------------------------------------------------
# K3_struct E2E: unit-mismatch observation is rejected, no VERIFIED row written
# ---------------------------------------------------------------------------

def test_rebuild_settlements_rejects_unknown_unit(tmp_path):
    """K3_struct: obs with unknown unit='X' for F-settlement city is rejected by validator.

    Seeds NYC (settlement_unit=F) with one VERIFIED obs that has unit='X' (unknown).
    Asserts: zero settlements written, rows_skipped==1.

    Note: C-unit obs for F-city is CONVERTED (not rejected) per K1_struct spec
    ('convert, don't reject' for F<->C mismatches). Unknown units (not F/C/K)
    are always rejected.
    """
    conn, db_path = _make_tmp_db(tmp_path)

    # NYC expects F; inject unknown-unit obs to trigger UnknownUnitError
    conn.execute(
        "INSERT INTO observations "
        "(city, target_date, source, high_temp, low_temp, unit, authority) "
        "VALUES ('NYC', '2025-07-01', 'wu_icao', 85.0, 70.0, 'X', 'VERIFIED')"
    )
    conn.commit()
    conn.close()

    from scripts.rebuild_settlements import rebuild_settlements
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    summary = rebuild_settlements(conn2, dry_run=False, city_filter="NYC")
    conn2.commit()

    settlements = conn2.execute(
        "SELECT COUNT(*) FROM settlements WHERE city='NYC'"
    ).fetchone()[0]
    conn2.close()

    assert settlements == 0, (
        f"Unknown-unit obs must not produce a VERIFIED settlement. Got {settlements} rows."
    )
    assert summary["rows_skipped"] == 1, (
        f"Expected rows_skipped=1 for unknown-unit obs, got {summary['rows_skipped']}"
    )


# ---------------------------------------------------------------------------
# K3_struct E2E: multi-city fixture with one wrong-unit city
# ---------------------------------------------------------------------------

def test_rebuild_multi_city_one_unknown_unit_one_valid(tmp_path):
    """K3_struct: multi-city run where one obs has unknown unit, rest are valid.

    NYC (F): 2 VERIFIED F-unit obs -> 2 settlements written.
    NYC (F): 1 VERIFIED X-unit obs -> rejected (unknown unit), rows_skipped += 1.
    Total: 2 settlements, 1 skipped.

    Note: C-unit obs for F-city is CONVERTED (not rejected) per K1_struct spec.
    """
    conn, db_path = _make_tmp_db(tmp_path)

    # 2 good F-unit observations
    for i in range(2):
        conn.execute(
            "INSERT INTO observations "
            "(city, target_date, source, high_temp, low_temp, unit, authority) "
            "VALUES ('NYC', ?, 'wu_icao', 85.0, 70.0, 'F', 'VERIFIED')",
            (f"2025-07-{i+1:02d}",),
        )
    # 1 unknown-unit (X) observation
    conn.execute(
        "INSERT INTO observations "
        "(city, target_date, source, high_temp, low_temp, unit, authority) "
        "VALUES ('NYC', '2025-07-10', 'wu_icao', 85.0, 70.0, 'X', 'VERIFIED')"
    )
    conn.commit()
    conn.close()

    from scripts.rebuild_settlements import rebuild_settlements
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    summary = rebuild_settlements(conn2, dry_run=False, city_filter="NYC")
    conn2.commit()

    settlements = conn2.execute(
        "SELECT COUNT(*) FROM settlements WHERE city='NYC'"
    ).fetchone()[0]
    conn2.close()

    assert settlements == 2, f"Expected 2 valid settlements, got {settlements}"
    assert summary["rows_skipped"] == 1, (
        f"Expected 1 skipped (unknown unit), got {summary['rows_skipped']}"
    )
