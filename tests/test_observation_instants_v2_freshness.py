# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: docs/operations/task_2026-05-17_post_karachi_remediation/F44_INVESTIGATION.md
#   Antibody for F44: observation_instants_v2 writer dead since 2026-05-10.
#   These tests catch the "dead-writer" category permanently by asserting
#   MAX(target_date) is within a defined SLA window.
#   CI-runnable, no live DB dependency — parametrized over a fixture DB.
"""Antibody tests for observation_instants_v2 freshness SLA.

F44 root cause: no live-tick writer existed. The table was populated only by
one-time backfill scripts. These tests catch the dead-writer category by:

1. Asserting MAX(target_date) within 48h SLA on a fixture DB.
2. Asserting the new obs_v2_live_tick module imports cleanly.
3. Asserting that ingest_main.py registers an 'ingest_k2_obs_v2' scheduler job.
4. Asserting the live-tick script does NOT write openmeteo_archive_hourly rows
   (source-tier violation; would be rejected by A2 but we catch it at design time).
"""
from __future__ import annotations

import sqlite3
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fresh_v2_db(tmp_path: Path) -> Path:
    """Fixture DB with observation_instants_v2 containing fresh rows (today)."""
    db_path = tmp_path / "test_world.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE observation_instants_v2 (
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            utc_timestamp TEXT NOT NULL,
            authority TEXT NOT NULL,
            data_version TEXT NOT NULL,
            imported_at TEXT NOT NULL
        )
    """)
    today = date.today().isoformat()
    conn.execute(
        "INSERT INTO observation_instants_v2 (city, target_date, source, utc_timestamp, authority, data_version, imported_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("Karachi", today, "wu_icao_history", f"{today}T12:00:00+00:00", "VERIFIED", "v1.wu-native", f"{today}T12:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def stale_v2_db(tmp_path: Path) -> Path:
    """Fixture DB simulating F44: MAX(target_date) = 7 days ago (stale beyond SLA)."""
    db_path = tmp_path / "stale_world.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE observation_instants_v2 (
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            utc_timestamp TEXT NOT NULL,
            authority TEXT NOT NULL,
            data_version TEXT NOT NULL,
            imported_at TEXT NOT NULL
        )
    """)
    stale_date = (date.today() - timedelta(days=7)).isoformat()
    conn.execute(
        "INSERT INTO observation_instants_v2 (city, target_date, source, utc_timestamp, authority, data_version, imported_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("Karachi", stale_date, "wu_icao_history", f"{stale_date}T12:00:00+00:00", "VERIFIED", "v1.wu-native", f"{stale_date}T12:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def empty_v2_db(tmp_path: Path) -> Path:
    """Fixture DB simulating F44 at forecasts.db: zero rows."""
    db_path = tmp_path / "empty_world.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE observation_instants_v2 (
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            utc_timestamp TEXT NOT NULL,
            authority TEXT NOT NULL,
            data_version TEXT NOT NULL,
            imported_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Freshness SLA helpers (parametrizable for future use)
# ---------------------------------------------------------------------------

SLA_HOURS = 48  # maximum acceptable staleness


def _max_target_date(db_path: Path) -> date | None:
    """Return MAX(target_date) from observation_instants_v2, or None if empty."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT MAX(target_date) FROM observation_instants_v2").fetchone()
        if row and row[0]:
            return date.fromisoformat(row[0])
        return None
    finally:
        conn.close()


def _check_freshness(db_path: Path, *, sla_hours: int = SLA_HOURS) -> tuple[bool, str]:
    """Return (is_fresh, message) for the given DB."""
    max_date = _max_target_date(db_path)
    if max_date is None:
        return False, "observation_instants_v2 is empty (zero rows)"
    today = date.today()
    staleness_days = (today - max_date).days
    staleness_hours = staleness_days * 24
    if staleness_hours > sla_hours:
        return False, (
            f"MAX(target_date)={max_date} is {staleness_days}d ({staleness_hours}h) old, "
            f"exceeds {sla_hours}h SLA. "
            f"Root cause: observation_instants_v2 writer not running (F44 category)."
        )
    return True, f"MAX(target_date)={max_date} is {staleness_days}d old, within {sla_hours}h SLA"


# ---------------------------------------------------------------------------
# SLA tests
# ---------------------------------------------------------------------------

def test_freshness_check_passes_for_recent_data(fresh_v2_db: Path) -> None:
    """Freshness helper reports OK when MAX(target_date) = today."""
    is_fresh, msg = _check_freshness(fresh_v2_db)
    assert is_fresh, f"Expected fresh DB to pass SLA check: {msg}"


def test_freshness_check_fails_for_stale_data(stale_v2_db: Path) -> None:
    """Freshness helper catches F44-category staleness (7-day gap > 48h SLA)."""
    is_fresh, msg = _check_freshness(stale_v2_db)
    assert not is_fresh, "Expected stale DB (7-day gap) to fail SLA check"
    assert "F44" in msg or "SLA" in msg, f"Error message should mention SLA/F44: {msg}"


def test_freshness_check_fails_for_empty_table(empty_v2_db: Path) -> None:
    """Freshness helper catches empty table (F44 worst case: no rows at all)."""
    is_fresh, msg = _check_freshness(empty_v2_db)
    assert not is_fresh, "Expected empty DB to fail SLA check"
    assert "empty" in msg.lower() or "zero" in msg.lower(), f"Message should say empty: {msg}"


def test_exactly_48h_boundary_is_fresh(tmp_path: Path) -> None:
    """Exactly at SLA boundary (2 days ago) should pass."""
    db_path = tmp_path / "boundary.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE observation_instants_v2 (city TEXT, target_date TEXT, source TEXT, utc_timestamp TEXT, authority TEXT, data_version TEXT, imported_at TEXT)")
    boundary_date = (date.today() - timedelta(days=2)).isoformat()
    conn.execute("INSERT INTO observation_instants_v2 VALUES (?, ?, ?, ?, ?, ?, ?)",
                 ("London", boundary_date, "wu_icao_history", f"{boundary_date}T00:00:00+00:00", "VERIFIED", "v1.wu-native", f"{boundary_date}T00:00:00+00:00"))
    conn.commit()
    conn.close()
    is_fresh, msg = _check_freshness(db_path, sla_hours=48)
    assert is_fresh, f"2 days ago (48h) should be within SLA: {msg}"


def test_beyond_48h_boundary_is_stale(tmp_path: Path) -> None:
    """Three days ago (>48h) should fail."""
    db_path = tmp_path / "beyond.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE observation_instants_v2 (city TEXT, target_date TEXT, source TEXT, utc_timestamp TEXT, authority TEXT, data_version TEXT, imported_at TEXT)")
    old_date = (date.today() - timedelta(days=3)).isoformat()
    conn.execute("INSERT INTO observation_instants_v2 VALUES (?, ?, ?, ?, ?, ?, ?)",
                 ("London", old_date, "wu_icao_history", f"{old_date}T00:00:00+00:00", "VERIFIED", "v1.wu-native", f"{old_date}T00:00:00+00:00"))
    conn.commit()
    conn.close()
    is_fresh, msg = _check_freshness(db_path, sla_hours=48)
    assert not is_fresh, f"3 days ago (>48h) should fail SLA: {msg}"


# ---------------------------------------------------------------------------
# Structural antibody: live-tick module importability
# ---------------------------------------------------------------------------

def test_obs_v2_live_tick_imports_cleanly() -> None:
    """obs_v2_live_tick.py must import without errors.

    Catches regressions where the module's imports are broken (e.g. a
    refactor renames a function the tick depends on).
    """
    from scripts.obs_v2_live_tick import run_live_tick, TickResult, DATA_VERSION
    assert callable(run_live_tick), "run_live_tick must be callable"
    assert DATA_VERSION.startswith("v1."), f"DATA_VERSION must match v1.* pattern, got {DATA_VERSION!r}"


def test_obs_v2_live_tick_does_not_use_openmeteo_source() -> None:
    """The live-tick script must not use openmeteo_archive_hourly as a source.

    openmeteo_archive_hourly is NOT in any city's allowed_sources set (A2 rule).
    Using it would cause all writes to be rejected by the v2 writer.
    This is the design constraint that motivated F44's fix shape (not a simple
    dual-write from hourly_instants_append.py).
    """
    import ast
    tick_path = Path(__file__).resolve().parent.parent / "scripts" / "obs_v2_live_tick.py"
    source_text = tick_path.read_text()
    assert "openmeteo_archive_hourly" not in source_text, (
        "obs_v2_live_tick.py must not reference 'openmeteo_archive_hourly'. "
        "This source is rejected by v2 writer A2 validation for all cities. "
        "Use wu_icao_history (WU_ICAO tier) or ogimet_metar_* (OGIMET_METAR tier)."
    )


# ---------------------------------------------------------------------------
# Structural antibody: ingest_main.py registers v2 tick job
# ---------------------------------------------------------------------------

def test_ingest_main_registers_obs_v2_job() -> None:
    """ingest_main.py must register 'ingest_k2_obs_v2' as a scheduler job.

    Catches regressions where the scheduler wiring is accidentally removed.
    This is the F44 fix — if this assertion fails, the writer is dead again.
    """
    ingest_main_path = Path(__file__).resolve().parent.parent / "src" / "ingest_main.py"
    source_text = ingest_main_path.read_text()
    assert "ingest_k2_obs_v2" in source_text, (
        "ingest_main.py must register 'ingest_k2_obs_v2' scheduler job. "
        "This is the F44 fix. If this job is missing, observation_instants_v2 "
        "will go stale (same root cause: no live-tick writer)."
    )
    assert "_k2_obs_v2_tick" in source_text, (
        "ingest_main.py must define _k2_obs_v2_tick function. "
        "This is the F44 fix entry point."
    )
