"""K4 authority gate relationship tests.

Pins the authority gate invariants so they cannot silently regress.

All tests use :memory: SQLite or tmp_path fixtures.
NO writes to production DB.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(init_world: bool = True) -> sqlite3.Connection:
    """Create an in-memory DB with Zeus schema, authority columns applied."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    if init_world:
        init_schema(conn)
        # K4: add authority columns to tables that init_schema doesn't include yet
        _add_authority_columns(conn)
    return conn


def _add_authority_columns(conn: sqlite3.Connection) -> None:
    """Add authority columns to tables that init_schema lacks them (worktree shim)."""
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


def _seed_calibration_pairs(
    conn: sqlite3.Connection,
    city: str = "NYC",
    authority: str = "UNVERIFIED",
    n: int = 20,
) -> None:
    """Seed calibration_pairs rows with given authority."""
    season = "JJA"
    cluster = city  # K3: cluster == city
    for i in range(n):
        conn.execute(
            """
            INSERT INTO calibration_pairs
            (city, target_date, range_label, p_raw, outcome, lead_days,
             season, cluster, forecast_available_at, bias_corrected, authority)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                city,
                f"2025-07-{i+1:02d}",
                "85-86F",
                0.3,
                i % 2,
                float(i % 5 + 1),
                season,
                cluster,
                f"2025-07-{i+1:02d}T06:00:00Z",
                authority,
            ),
        )
    conn.commit()


def _seed_observations(
    conn: sqlite3.Connection,
    city: str = "NYC",
    authority: str = "VERIFIED",
    n: int = 5,
) -> None:
    """Seed observations rows."""
    for i in range(n):
        conn.execute(
            """
            INSERT INTO observations
            (city, target_date, source, high_temp, low_temp, unit, authority)
            VALUES (?, ?, 'wu_icao', ?, 70.0, 'F', ?)
            """,
            (city, f"2025-07-{i+1:02d}", 85.0 + i, authority),
        )
    conn.commit()


def _seed_settlements(
    conn: sqlite3.Connection,
    city: str = "NYC",
    authority: str = "VERIFIED",
    n: int = 5,
) -> None:
    """Seed settlements rows."""
    for i in range(n):
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO settlements
                (city, target_date, settlement_value, settlement_source,
                 settled_at, authority)
                VALUES (?, ?, ?, 'wu_icao_rebuild', '2025-07-01T12:00:00Z', ?)
                """,
                (city, f"2025-07-{i+1:02d}", 85.0 + i, authority),
            )
        except Exception:
            pass
    conn.commit()


def _seed_ensemble_snapshots(
    conn: sqlite3.Connection,
    city: str = "NYC",
    authority: str = "VERIFIED",
    n: int = 3,
) -> None:
    """Seed ensemble_snapshots rows."""
    members = json.dumps([85.0 + j * 0.5 for j in range(51)])
    for i in range(n):
        conn.execute(
            """
            INSERT OR IGNORE INTO ensemble_snapshots
            (city, target_date, issue_time, valid_time, available_at,
             fetch_time, lead_hours, members_json, model_version,
             data_version, authority)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ecmwf_tigge', 'v1', ?)
            """,
            (
                city,
                f"2025-07-{i+1:02d}",
                f"2025-07-{i+1:02d}T00:00:00Z",
                f"2025-07-{i+1:02d}T12:00:00Z",
                f"2025-07-{i+1:02d}T06:00:00Z",
                f"2025-07-{i+1:02d}T07:00:00Z",
                48.0,
                members,
                authority,
            ),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Test 1: get_pairs_for_bucket defaults to VERIFIED-only
# ---------------------------------------------------------------------------

def test_get_pairs_for_bucket_defaults_to_verified_only():
    """Property: get_pairs_for_bucket with only UNVERIFIED rows returns 0 rows by default."""
    from src.calibration.store import get_pairs_for_bucket

    conn = _make_db()
    _seed_calibration_pairs(conn, authority="UNVERIFIED", n=10)

    # Default call: should filter to VERIFIED, find nothing
    result = get_pairs_for_bucket(conn, "NYC", "JJA")
    assert len(result) == 0, (
        f"Expected 0 VERIFIED rows but got {len(result)}. "
        "get_pairs_for_bucket must default to authority='VERIFIED'."
    )

    # Explicit 'any': should return all rows
    result_any = get_pairs_for_bucket(conn, "NYC", "JJA", authority_filter="any")
    assert len(result_any) == 10, (
        f"Expected 10 rows with authority_filter='any', got {len(result_any)}"
    )


def test_get_pairs_for_bucket_returns_verified_rows():
    """Property: VERIFIED rows are returned by default filter."""
    from src.calibration.store import get_pairs_for_bucket

    conn = _make_db()
    _seed_calibration_pairs(conn, authority="VERIFIED", n=8)
    _seed_calibration_pairs(conn, authority="UNVERIFIED", n=5)

    result = get_pairs_for_bucket(conn, "NYC", "JJA")
    assert len(result) == 8, (
        f"Expected 8 VERIFIED rows, got {len(result)}"
    )


# ---------------------------------------------------------------------------
# Test 2: market_fusion AuthorityViolation on UNVERIFIED data
# ---------------------------------------------------------------------------

def test_market_fusion_raises_authority_violation_on_unverified():
    """Property: compute_alpha raises AuthorityViolation when authority_verified=False."""
    from src.strategy.market_fusion import compute_alpha, AuthorityViolation
    from src.types.temperature import TemperatureDelta

    with pytest.raises(AuthorityViolation):
        compute_alpha(
            calibration_level=2,
            ensemble_spread=TemperatureDelta(5.0, "F"),
            model_agreement="AGREE",
            lead_days=3.0,
            hours_since_open=24.0,
            city_name="NYC",
            season="JJA",
            authority_verified=False,
        )


def test_market_fusion_succeeds_with_verified_data():
    """Property: compute_alpha succeeds when authority_verified=True (default)."""
    from src.strategy.market_fusion import compute_alpha
    from src.types.temperature import TemperatureDelta

    result = compute_alpha(
        calibration_level=2,
        ensemble_spread=TemperatureDelta(5.0, "F"),
        model_agreement="AGREE",
        lead_days=3.0,
        hours_since_open=24.0,
        city_name="NYC",
        season="JJA",
        authority_verified=True,
    )
    assert 0.20 <= result.value <= 0.85


# ---------------------------------------------------------------------------
# Test 3: rebuild_settlements only writes VERIFIED rows
# ---------------------------------------------------------------------------

def test_rebuild_settlements_only_writes_verified_rows(tmp_path):
    """Property: rebuild_settlements writes rows only for VERIFIED observations."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))

    db_path = tmp_path / "test_world.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    from src.state.db import init_schema
    init_schema(conn)
    _add_authority_columns(conn)

    # Seed 3 VERIFIED + 2 UNVERIFIED observations for NYC
    for i in range(3):
        conn.execute(
            "INSERT INTO observations "
            "(city, target_date, source, high_temp, low_temp, unit, authority) "
            "VALUES (?, ?, 'wu_icao', 85.0, 70.0, 'F', 'VERIFIED')",
            ("NYC", f"2025-07-{i+1:02d}"),
        )
    for i in range(2):
        conn.execute(
            "INSERT INTO observations "
            "(city, target_date, source, high_temp, low_temp, unit, authority) "
            "VALUES (?, ?, 'wu_icao', 75.0, 60.0, 'F', 'UNVERIFIED')",
            ("NYC", f"2025-08-{i+1:02d}"),
        )
    conn.commit()
    conn.close()

    from scripts.rebuild_settlements import rebuild_settlements
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    summary = rebuild_settlements(conn2, dry_run=False, city_filter="NYC")
    conn2.commit()

    # Check output
    rows = conn2.execute(
        "SELECT authority FROM settlements WHERE city='NYC'"
    ).fetchall()
    conn2.close()

    assert len(rows) == 3, f"Expected 3 settlement rows, got {len(rows)}"
    assert all(r["authority"] == "VERIFIED" for r in rows), (
        "All rebuilt settlements must have authority='VERIFIED'"
    )
    assert summary["rows_skipped"] == 0


# ---------------------------------------------------------------------------
# Test 4: rebuild_calibration only produces pairs from VERIFIED observations
# ---------------------------------------------------------------------------

def test_rebuild_calibration_requires_verified_observations(tmp_path):
    """Property: rebuild_calibration skips snapshots with no VERIFIED settlement."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))

    db_path = tmp_path / "test_world_cal.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    from src.state.db import init_schema
    init_schema(conn)
    _add_authority_columns(conn)

    # Seed VERIFIED snapshots for NYC
    _seed_ensemble_snapshots(conn, city="NYC", authority="VERIFIED", n=3)

    # Seed only 2 VERIFIED settlements (1 snapshot has no settlement)
    _seed_settlements(conn, city="NYC", authority="VERIFIED", n=2)
    conn.commit()
    conn.close()

    from scripts.rebuild_calibration import rebuild_calibration
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    summary = rebuild_calibration(conn2, dry_run=False, city_filter="NYC")
    conn2.commit()

    # 3 snapshots processed; 1 skipped (no settlement for day 3)
    assert summary["rows_processed"] == 3
    assert summary["rows_skipped"] == 1

    # All written pairs have authority='VERIFIED'
    pairs = conn2.execute(
        "SELECT authority FROM calibration_pairs WHERE city='NYC'"
    ).fetchall()
    conn2.close()
    assert all(r["authority"] == "VERIFIED" for r in pairs), (
        "All rebuilt calibration_pairs must have authority='VERIFIED'"
    )


# ---------------------------------------------------------------------------
# Test 5: refit_platt queries only VERIFIED pairs
# ---------------------------------------------------------------------------

def test_refit_platt_queries_only_verified_pairs():
    """Property: refit_platt.py SQL includes AND authority='VERIFIED'."""
    script = PROJECT_ROOT / "scripts" / "refit_platt.py"
    content = script.read_text()
    assert "authority = 'VERIFIED'" in content, (
        "refit_platt.py must include AND authority = 'VERIFIED' filter"
    )


# ---------------------------------------------------------------------------
# Test 6: migrate dry-run is safe (no deletes)
# ---------------------------------------------------------------------------

def test_migrate_add_authority_column_dry_run_is_safe(tmp_path):
    """Property: migration dry-run makes no schema or data changes."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))

    db_path = tmp_path / "test_migrate.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    from src.state.db import init_schema
    init_schema(conn)

    # Seed some observations
    conn.execute(
        "INSERT INTO observations "
        "(city, target_date, source, high_temp, low_temp, unit) "
        "VALUES ('NYC', '2025-07-01', 'wu_icao', 85.0, 70.0, 'F')"
    )
    conn.commit()
    obs_count_before = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    conn.close()

    from scripts.migrate_add_authority_column import run_migration
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    # dry_run=True, destructive_confirmed=False
    summary = run_migration(conn2, dry_run=True, destructive_confirmed=False)
    obs_count_after = conn2.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    conn2.close()

    assert obs_count_before == obs_count_after, (
        f"Dry-run must not delete rows: {obs_count_before} -> {obs_count_after}"
    )
    # No DELETE steps should have run
    assert not any("DEL  " in s for s in summary["steps"]), (
        "Dry-run must not log any DEL steps"
    )


# ---------------------------------------------------------------------------
# Test 7: migrate refuses destructive deletes without ZEUS_DESTRUCTIVE_CONFIRMED
# ---------------------------------------------------------------------------

def test_migrate_refuses_destructive_without_env_var(tmp_path, monkeypatch):
    """Property: migration with --no-dry-run but no env var skips deletes."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))

    # Ensure env var is NOT set
    monkeypatch.delenv("ZEUS_DESTRUCTIVE_CONFIRMED", raising=False)

    db_path = tmp_path / "test_migrate2.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    from src.state.db import init_schema
    init_schema(conn)

    conn.execute(
        "INSERT INTO observations "
        "(city, target_date, source, high_temp, low_temp, unit) "
        "VALUES ('NYC', '2025-07-01', 'wu_icao', 85.0, 70.0, 'F')"
    )
    conn.commit()
    obs_before = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    conn.close()

    from scripts.migrate_add_authority_column import run_migration
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    # dry_run=False but destructive_confirmed=False (env var absent)
    summary = run_migration(conn2, dry_run=False, destructive_confirmed=False)
    obs_after = conn2.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    conn2.close()

    assert obs_before == obs_after, (
        f"Without ZEUS_DESTRUCTIVE_CONFIRMED, observations must not be deleted: "
        f"{obs_before} -> {obs_after}"
    )
    # Should have BLOCKED message
    assert any("BLOCKED" in s or "not set" in s.lower() for s in summary["steps"]), (
        "Migration should log a BLOCKED/warning message when env var is absent"
    )


def test_migrate_runs_destructive_with_env_var(tmp_path, monkeypatch):
    """Property: migration with ZEUS_DESTRUCTIVE_CONFIRMED=1 runs delete steps."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))

    monkeypatch.setenv("ZEUS_DESTRUCTIVE_CONFIRMED", "1")

    db_path = tmp_path / "test_migrate3.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    from src.state.db import init_schema
    init_schema(conn)
    # Add authority column so DELETE can check it
    _add_authority_columns(conn)

    # Seed one UNVERIFIED observation
    conn.execute(
        "INSERT INTO observations "
        "(city, target_date, source, high_temp, low_temp, unit, authority) "
        "VALUES ('NYC', '2025-07-01', 'wu_icao', 85.0, 70.0, 'F', 'UNVERIFIED')"
    )
    conn.commit()
    conn.close()

    from scripts.migrate_add_authority_column import run_migration
    conn2 = sqlite3.connect(str(db_path))
    conn2.row_factory = sqlite3.Row
    summary = run_migration(conn2, dry_run=False, destructive_confirmed=True)
    obs_after = conn2.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    conn2.close()

    # The DELETE ran (the code path was exercised)
    assert any("DEL  observations" in s or "DESTRUCTIVE" in s for s in summary["steps"]), (
        "With ZEUS_DESTRUCTIVE_CONFIRMED=1, DELETE steps must run"
    )
    # Our seeded UNVERIFIED row should be gone (not tigge_derived source)
    assert obs_after == 0, (
        f"UNVERIFIED non-tigge_derived observation should have been deleted, got {obs_after} rows"
    )


# ---------------------------------------------------------------------------
# Test 8: AuthorityViolation is a ValueError subclass
# ---------------------------------------------------------------------------

def test_authority_violation_is_value_error():
    """AuthorityViolation must be a ValueError subclass for catch-all error handling."""
    from src.strategy.market_fusion import AuthorityViolation
    assert issubclass(AuthorityViolation, ValueError)


# ---------------------------------------------------------------------------
# Test 9: evaluator authority gate returns rejection on UNVERIFIED pairs
# ---------------------------------------------------------------------------

def test_evaluator_gate_label_present_in_evaluator_code():
    """Property: evaluator.py contains the AUTHORITY_GATE rejection stage."""
    evaluator = PROJECT_ROOT / "src" / "engine" / "evaluator.py"
    content = evaluator.read_text()
    assert "AUTHORITY_GATE" in content, (
        "evaluator.py must have AUTHORITY_GATE rejection stage for K4 guard"
    )
    assert "insufficient_verified_calibration" in content, (
        "evaluator.py must reference insufficient_verified_calibration reason"
    )
