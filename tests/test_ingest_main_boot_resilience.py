# Created: 2026-05-02
# Last reused/audited: 2026-05-02
# Authority basis: /tmp/sonnet-data-daemon-resilience.md — boot-time staleness
#                  detection + force-fetch requirement.
"""Regression tests for _k2_startup_catch_up boot-time staleness guard.

Verifies that when the daemon boots with stale forecasts or solar_daily data
(missed overnight cron), it force-calls daily_tick before the next scheduled
cron — and that it skips the force-fetch when data is already fresh.

Calls the underlying function via __wrapped__ (set by functools.wraps in
_scheduler_job decorator) to bypass the scheduler health-writer wrapper.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_connection, init_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn() -> sqlite3.Connection:
    """In-memory DB with full Zeus schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _insert_forecast_row(conn, captured_at: str) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO forecasts
           (city, target_date, source, forecast_basis_date, captured_at)
           VALUES (?, ?, ?, ?, ?)""",
        ("TestCity", "2026-05-01", "ecmwf", "2026-05-01", captured_at),
    )
    conn.commit()


def _insert_solar_coverage_row(conn, fetched_at: str) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO data_coverage
           (data_table, city, data_source, target_date, sub_key, status, fetched_at)
           VALUES ('solar_daily', 'TestCity', 'open_meteo', '2026-05-01', '', 'WRITTEN', ?)""",
        (fetched_at,),
    )
    conn.commit()


def _call_startup_catch_up(conn):
    """Invoke _k2_startup_catch_up's underlying function directly."""
    from src.ingest_main import _k2_startup_catch_up as fn
    fn.__wrapped__()


# ---------------------------------------------------------------------------
# forecasts staleness guard
# ---------------------------------------------------------------------------


class TestForecastsStalenessGuard:
    """forecasts captured_at-based staleness detection."""

    def test_force_refetch_when_forecasts_stale(self):
        """When forecasts.captured_at is > 18h old, daily_tick is called."""
        conn = _make_conn()
        stale_ts = (
            datetime.now(timezone.utc) - timedelta(hours=25)
        ).isoformat()
        _insert_forecast_row(conn, stale_ts)

        fake_result = {"cities_processed": 46, "inserted": 100}

        with (
            patch("src.data.daily_obs_append.catch_up_missing", return_value={}),
            patch("src.data.hourly_instants_append.catch_up_missing", return_value={}),
            patch("src.data.solar_append.catch_up_missing", return_value={}),
            patch("src.data.forecasts_append.catch_up_missing", return_value={}),
            patch("src.data.forecasts_append.daily_tick", return_value=fake_result) as mock_ftick,
            patch("src.data.solar_append.daily_tick", return_value={}) as mock_stick,
            patch("src.state.db.get_world_connection", return_value=conn),
        ):
            _call_startup_catch_up(conn)

        mock_ftick.assert_called_once_with(conn)

    def test_skip_force_refetch_when_forecasts_fresh(self):
        """When forecasts.captured_at is within 18h, daily_tick is NOT called."""
        conn = _make_conn()
        fresh_ts = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).isoformat()
        _insert_forecast_row(conn, fresh_ts)
        # Also insert fresh solar coverage so solar guard also skips
        _insert_solar_coverage_row(conn, fresh_ts)

        with (
            patch("src.data.daily_obs_append.catch_up_missing", return_value={}),
            patch("src.data.hourly_instants_append.catch_up_missing", return_value={}),
            patch("src.data.solar_append.catch_up_missing", return_value={}),
            patch("src.data.forecasts_append.catch_up_missing", return_value={}),
            patch("src.data.forecasts_append.daily_tick", return_value={}) as mock_ftick,
            patch("src.data.solar_append.daily_tick", return_value={}) as mock_stick,
            patch("src.state.db.get_world_connection", return_value=conn),
        ):
            _call_startup_catch_up(conn)

        mock_ftick.assert_not_called()

    def test_force_refetch_when_forecasts_table_empty(self):
        """When forecasts table is empty (MAX returns NULL), daily_tick is called."""
        conn = _make_conn()
        # No forecast rows — NULL MAX triggers infinite staleness
        # Insert fresh solar so only forecasts guard fires
        fresh_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _insert_solar_coverage_row(conn, fresh_ts)

        fake_result = {"cities_processed": 46, "inserted": 6828}

        with (
            patch("src.data.daily_obs_append.catch_up_missing", return_value={}),
            patch("src.data.hourly_instants_append.catch_up_missing", return_value={}),
            patch("src.data.solar_append.catch_up_missing", return_value={}),
            patch("src.data.forecasts_append.catch_up_missing", return_value={}),
            patch("src.data.forecasts_append.daily_tick", return_value=fake_result) as mock_ftick,
            patch("src.data.solar_append.daily_tick", return_value={}) as mock_stick,
            patch("src.state.db.get_world_connection", return_value=conn),
        ):
            _call_startup_catch_up(conn)

        mock_ftick.assert_called_once_with(conn)


# ---------------------------------------------------------------------------
# solar_daily staleness guard
# ---------------------------------------------------------------------------


class TestSolarStalenessGuard:
    """solar_daily data_coverage.fetched_at-based staleness detection."""

    def test_force_refetch_when_solar_stale(self):
        """When data_coverage solar fetched_at > 18h, solar daily_tick is called."""
        conn = _make_conn()
        # Fresh forecast so only solar guard fires
        fresh_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _insert_forecast_row(conn, fresh_ts)

        stale_solar_ts = (
            datetime.now(timezone.utc) - timedelta(hours=30)
        ).isoformat()
        _insert_solar_coverage_row(conn, stale_solar_ts)

        fake_solar = {"cities_processed": 46, "inserted": 650}

        with (
            patch("src.data.daily_obs_append.catch_up_missing", return_value={}),
            patch("src.data.hourly_instants_append.catch_up_missing", return_value={}),
            patch("src.data.solar_append.catch_up_missing", return_value={}),
            patch("src.data.forecasts_append.catch_up_missing", return_value={}),
            patch("src.data.forecasts_append.daily_tick", return_value={}) as mock_ftick,
            patch("src.data.solar_append.daily_tick", return_value=fake_solar) as mock_stick,
            patch("src.state.db.get_world_connection", return_value=conn),
        ):
            _call_startup_catch_up(conn)

        mock_stick.assert_called_once_with(conn)
        mock_ftick.assert_not_called()

    def test_skip_force_refetch_when_solar_fresh(self):
        """When data_coverage solar fetched_at within 18h, solar daily_tick NOT called."""
        conn = _make_conn()
        fresh_ts = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        _insert_forecast_row(conn, fresh_ts)
        _insert_solar_coverage_row(conn, fresh_ts)

        with (
            patch("src.data.daily_obs_append.catch_up_missing", return_value={}),
            patch("src.data.hourly_instants_append.catch_up_missing", return_value={}),
            patch("src.data.solar_append.catch_up_missing", return_value={}),
            patch("src.data.forecasts_append.catch_up_missing", return_value={}),
            patch("src.data.forecasts_append.daily_tick", return_value={}) as mock_ftick,
            patch("src.data.solar_append.daily_tick", return_value={}) as mock_stick,
            patch("src.state.db.get_world_connection", return_value=conn),
        ):
            _call_startup_catch_up(conn)

        mock_stick.assert_not_called()

    def test_force_refetch_when_solar_coverage_empty(self):
        """When data_coverage has no solar rows (NULL MAX), solar daily_tick is called."""
        conn = _make_conn()
        # Fresh forecast so only solar guard fires
        fresh_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _insert_forecast_row(conn, fresh_ts)
        # No solar coverage rows

        fake_solar = {"cities_processed": 46, "inserted": 650}

        with (
            patch("src.data.daily_obs_append.catch_up_missing", return_value={}),
            patch("src.data.hourly_instants_append.catch_up_missing", return_value={}),
            patch("src.data.solar_append.catch_up_missing", return_value={}),
            patch("src.data.forecasts_append.catch_up_missing", return_value={}),
            patch("src.data.forecasts_append.daily_tick", return_value={}) as mock_ftick,
            patch("src.data.solar_append.daily_tick", return_value=fake_solar) as mock_stick,
            patch("src.state.db.get_world_connection", return_value=conn),
        ):
            _call_startup_catch_up(conn)

        mock_stick.assert_called_once_with(conn)

# ---------------------------------------------------------------------------
# Advisory lock guard tests
# ---------------------------------------------------------------------------


from contextlib import contextmanager


def _make_lock_mock(acquired: bool):
    """Return a mock for acquire_lock that yields acquired (True/False)."""
    @contextmanager
    def _mock_acquire_lock(table_name, **kwargs):
        yield acquired
    return _mock_acquire_lock


class TestAdvisoryLockGuard:
    """Boot-forced daily_tick respects advisory lock — skips when lock held."""

    def test_forecasts_boot_tick_skips_when_lock_held(self):
        """When forecasts_daily lock is held, boot-forced daily_tick is NOT called."""
        conn = _make_conn()
        stale_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        _insert_forecast_row(conn, stale_ts)
        fresh_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _insert_solar_coverage_row(conn, fresh_ts)

        lock_not_acquired = _make_lock_mock(acquired=False)

        with (
            patch("src.data.daily_obs_append.catch_up_missing", return_value={}),
            patch("src.data.hourly_instants_append.catch_up_missing", return_value={}),
            patch("src.data.solar_append.catch_up_missing", return_value={}),
            patch("src.data.forecasts_append.catch_up_missing", return_value={}),
            patch("src.data.forecasts_append.daily_tick", return_value={}) as mock_ftick,
            patch("src.data.solar_append.daily_tick", return_value={}) as mock_stick,
            patch("src.state.db.get_world_connection", return_value=conn),
            patch("src.data.dual_run_lock.acquire_lock", new=lock_not_acquired),
        ):
            _call_startup_catch_up(conn)

        mock_ftick.assert_not_called()

    def test_solar_boot_tick_skips_when_lock_held(self):
        """When solar_daily lock is held, boot-forced solar daily_tick is NOT called."""
        conn = _make_conn()
        fresh_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _insert_forecast_row(conn, fresh_ts)
        stale_ts = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        _insert_solar_coverage_row(conn, stale_ts)

        lock_not_acquired = _make_lock_mock(acquired=False)

        with (
            patch("src.data.daily_obs_append.catch_up_missing", return_value={}),
            patch("src.data.hourly_instants_append.catch_up_missing", return_value={}),
            patch("src.data.solar_append.catch_up_missing", return_value={}),
            patch("src.data.forecasts_append.catch_up_missing", return_value={}),
            patch("src.data.forecasts_append.daily_tick", return_value={}) as mock_ftick,
            patch("src.data.solar_append.daily_tick", return_value={}) as mock_stick,
            patch("src.state.db.get_world_connection", return_value=conn),
            patch("src.data.dual_run_lock.acquire_lock", new=lock_not_acquired),
        ):
            _call_startup_catch_up(conn)

        mock_stick.assert_not_called()


# ---------------------------------------------------------------------------
# Solar status filter: FAILED rows must not mask staleness
# ---------------------------------------------------------------------------


class TestSolarStatusFilter:
    """data_coverage FAILED rows must not be counted as fresh solar data."""

    def test_failed_solar_row_treated_as_stale(self):
        """A recent FAILED coverage row must NOT suppress the solar boot-force fetch."""
        conn = _make_conn()
        fresh_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _insert_forecast_row(conn, fresh_ts)

        # Insert a recent FAILED coverage row — should NOT count as fresh
        recent_failed_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        conn.execute(
            """INSERT OR REPLACE INTO data_coverage
               (data_table, city, data_source, target_date, sub_key, status, fetched_at)
               VALUES ('solar_daily', 'TestCity', 'open_meteo', '2026-05-01', '', 'FAILED', ?)""",
            (recent_failed_ts,),
        )
        conn.commit()

        fake_solar = {"cities_processed": 46, "inserted": 650}
        lock_acquired = _make_lock_mock(acquired=True)

        with (
            patch("src.data.daily_obs_append.catch_up_missing", return_value={}),
            patch("src.data.hourly_instants_append.catch_up_missing", return_value={}),
            patch("src.data.solar_append.catch_up_missing", return_value={}),
            patch("src.data.forecasts_append.catch_up_missing", return_value={}),
            patch("src.data.forecasts_append.daily_tick", return_value={}) as mock_ftick,
            patch("src.data.solar_append.daily_tick", return_value=fake_solar) as mock_stick,
            patch("src.state.db.get_world_connection", return_value=conn),
            patch("src.data.dual_run_lock.acquire_lock", new=lock_acquired),
        ):
            _call_startup_catch_up(conn)

        # Only WRITTEN rows count — no WRITTEN rows means infinite staleness → force fetch
        mock_stick.assert_called_once_with(conn)
        mock_ftick.assert_not_called()


# ---------------------------------------------------------------------------
# Pre-Phase-1 timestamp capture: Phase 1 cannot mask overnight gap
# ---------------------------------------------------------------------------


class TestPrePhase1Snapshot:
    """Phase 1 catch-up backfills cannot suppress a boot-force daily_tick."""

    def test_phase1_catch_up_does_not_mask_stale_forecasts(self):
        """Even if Phase 1 writes a fresh forecast row, the pre-boot staleness is used."""
        conn = _make_conn()
        # Pre-boot state: no forecast rows (infinite staleness)
        fresh_solar = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _insert_solar_coverage_row(conn, fresh_solar)

        fresh_row_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

        def _catch_up_forecasts_with_write(conn_arg, *, days_back):
            """Simulates catch_up_missing writing a fresh historical row."""
            _insert_forecast_row(conn_arg, fresh_row_ts)
            return {"inserted": 1}

        fake_result = {"cities_processed": 46, "inserted": 100}
        lock_acquired = _make_lock_mock(acquired=True)

        with (
            patch("src.data.daily_obs_append.catch_up_missing", return_value={}),
            patch("src.data.hourly_instants_append.catch_up_missing", return_value={}),
            patch("src.data.solar_append.catch_up_missing", return_value={}),
            patch(
                "src.data.forecasts_append.catch_up_missing",
                side_effect=_catch_up_forecasts_with_write,
            ),
            patch("src.data.forecasts_append.daily_tick", return_value=fake_result) as mock_ftick,
            patch("src.data.solar_append.daily_tick", return_value={}) as mock_stick,
            patch("src.state.db.get_world_connection", return_value=conn),
            patch("src.data.dual_run_lock.acquire_lock", new=lock_acquired),
        ):
            _call_startup_catch_up(conn)

        # The stale decision was made before Phase 1 wrote the fresh row
        mock_ftick.assert_called_once_with(conn)
