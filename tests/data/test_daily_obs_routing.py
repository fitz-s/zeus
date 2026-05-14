# Created: 2026-05-14
# Last reused or audited: 2026-05-14
# Authority basis: docs/operations/task_2026-05-14_k1_followups/PLAN.md §2 P0
"""K1 P0 routing tests: daily_tick and catch_up_obs are called with a
forecasts.db connection, not world.db.

PLAN §2 P0 spec:
    "pre-P1 the test asserts on sqlite3.connect-path string match
    endswith('zeus-forecasts.db') since typed-conn does not exist yet"

Strategy: patch `src.ingest_main.get_forecasts_connection` and
`src.ingest_main.get_world_connection` to return an in-memory SQLite connection
(with the necessary schema), then call the private ingest functions and verify:

1. `_k2_daily_obs_tick` opens a forecasts connection (not world) for daily_tick.
2. `_k2_startup_catch_up` opens a forecasts connection for catch_up_obs, while
   the world connection is still used for data_coverage + forecasts queries.

These are routing-contract tests. They do NOT assert on observation data values;
they assert on which DB family each writer is called with.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_forecasts_mem_conn() -> sqlite3.Connection:
    """Return an in-memory connection that looks like zeus-forecasts.db to
    daily_obs_append.daily_tick.

    We do NOT call init_schema_forecasts() here because that function has a
    pre-existing test-env failure (no world.db → static helpers → settlements_v2
    index error — tracked as pre-existing failure in test_forecast_db_split_invariant).

    Instead we create the minimal schema that daily_obs_append.daily_tick needs:
    - observations (forecasts-class, the table being rerouted)
    - data_coverage (world-class in prod, but in :memory: tests it must be
      co-located with the connection daily_tick receives)

    This test is a routing test, not a schema test. It verifies WHICH connection
    object is passed to daily_tick / catch_up_missing; it does not verify schema
    correctness (that is covered by test_forecast_db_split_invariant).
    """
    conn = sqlite3.connect(":memory:")
    # Minimal observations table matching the production schema.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            high_temp REAL,
            low_temp REAL,
            unit TEXT NOT NULL DEFAULT 'F',
            station_id TEXT,
            fetched_at TEXT,
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED',
            UNIQUE(city, target_date, source)
        )
    """)
    # data_coverage is needed by daily_obs_append internals.
    # In prod it lives on world.db; for this routing test we stub it here.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS data_coverage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            data_table TEXT NOT NULL,
            data_source TEXT NOT NULL,
            status TEXT NOT NULL,
            retry_after TEXT,
            fetched_at TEXT,
            UNIQUE(city, target_date, data_table, data_source)
        )
    """)
    # daily_observation_revisions is also written by daily_obs_append internals.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_observation_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            revision_num INTEGER NOT NULL DEFAULT 1,
            recorded_at TEXT
        )
    """)
    conn.commit()
    return conn


def _make_world_mem_conn() -> sqlite3.Connection:
    """Return an in-memory connection that looks like zeus-world.db for
    _k2_startup_catch_up Phase 2 staleness probes.

    Minimal schema: only the tables that _k2_startup_catch_up queries on the
    world connection (forecasts + data_coverage).  We do not call init_schema()
    to avoid the same pre-existing test-env schema issue.
    """
    conn = sqlite3.connect(":memory:")
    # forecasts table (world-class ENS data) — queried in Phase 2 staleness probe.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS forecasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            captured_at TEXT
        )
    """)
    # data_coverage — queried in Phase 2 solar_daily staleness probe.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS data_coverage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            data_table TEXT NOT NULL,
            data_source TEXT NOT NULL,
            status TEXT NOT NULL,
            retry_after TEXT,
            fetched_at TEXT,
            UNIQUE(city, target_date, data_table, data_source)
        )
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# ROT-1: _k2_daily_obs_tick calls daily_tick with a forecasts connection
# ---------------------------------------------------------------------------

class TestDailyObsTickRouting:
    """ROT-1 — _k2_daily_obs_tick must use get_forecasts_connection."""

    def test_daily_obs_tick_uses_forecasts_connection(self):
        """daily_tick must be invoked with the connection returned by
        get_forecasts_connection, NOT get_world_connection.

        Pre-P1 verification: the mock captures which connection object is
        passed; we assert it came from the forecasts factory, not world.

        Patching at source (src.state.db) because ingest_main imports
        get_forecasts_connection locally inside the function body on each call.
        """
        import src.ingest_main as ingest_main
        forecasts_conn = _make_forecasts_mem_conn()

        # acquire_lock context manager that always returns True (acquired).
        lock_ctx = MagicMock()
        lock_ctx.__enter__ = MagicMock(return_value=True)
        lock_ctx.__exit__ = MagicMock(return_value=False)

        with (
            patch("src.state.db.get_forecasts_connection", return_value=forecasts_conn) as mock_fc,
            patch("src.data.daily_obs_append.daily_tick") as mock_daily_tick,
            patch("src.data.dual_run_lock.acquire_lock", return_value=lock_ctx),
        ):
            mock_daily_tick.return_value = {"rows_written": 0}
            ingest_main._k2_daily_obs_tick()

        # get_forecasts_connection was called (not world).
        mock_fc.assert_called_once()
        # daily_tick received the forecasts connection.
        assert mock_daily_tick.called, "daily_tick must be called"
        actual_conn = mock_daily_tick.call_args[0][0]
        assert actual_conn is forecasts_conn, (
            f"daily_tick must receive the forecasts connection; got {actual_conn!r}"
        )


# ---------------------------------------------------------------------------
# ROT-2: _k2_startup_catch_up calls catch_up_obs with a forecasts connection
#         while keeping the world connection for everything else
# ---------------------------------------------------------------------------

class TestStartupCatchUpRouting:
    """ROT-2 — _k2_startup_catch_up must use get_forecasts_connection for
    catch_up_obs and get_world_connection for everything else.
    """

    def test_catch_up_obs_uses_forecasts_connection(self):
        """catch_up_missing (aliased as catch_up_obs) must be invoked with
        the connection from get_forecasts_connection.

        World connection must still be opened for Phase 2 staleness probes.

        Patching at source (src.state.db) because ingest_main imports both
        get_world_connection and get_forecasts_connection locally inside the
        function body on each call.
        """
        import src.ingest_main as ingest_main
        forecasts_conn = _make_forecasts_mem_conn()
        world_conn = _make_world_mem_conn()

        with (
            patch("src.state.db.get_forecasts_connection", return_value=forecasts_conn) as mock_fc,
            patch("src.state.db.get_world_connection", return_value=world_conn) as mock_wc,
            patch("src.data.daily_obs_append.catch_up_missing") as mock_catch_up_obs,
            patch("src.data.hourly_instants_append.catch_up_missing") as mock_catch_up_hourly,
            patch("src.data.solar_append.catch_up_missing") as mock_catch_up_solar,
            patch("src.data.forecasts_append.catch_up_missing") as mock_catch_up_forecasts,
            patch("src.data.forecasts_append.daily_tick") as mock_forecasts_dt,
            patch("src.data.solar_append.daily_tick") as mock_solar_dt,
        ):
            mock_catch_up_obs.return_value = {"filled": 0}
            mock_catch_up_hourly.return_value = {"filled": 0}
            mock_catch_up_solar.return_value = {"filled": 0}
            mock_catch_up_forecasts.return_value = {"filled": 0}

            ingest_main._k2_startup_catch_up()

        # get_forecasts_connection opened for obs_conn.
        mock_fc.assert_called_once_with(write_class="bulk")
        # get_world_connection opened for the main conn.
        mock_wc.assert_called_once_with(write_class="bulk")

        # catch_up_obs (daily_obs_append.catch_up_missing) called with forecasts conn.
        assert mock_catch_up_obs.called, "catch_up_obs must be called"
        obs_conn_arg = mock_catch_up_obs.call_args[0][0]
        assert obs_conn_arg is forecasts_conn, (
            f"catch_up_obs must receive the forecasts connection; got {obs_conn_arg!r}"
        )

        # Other K2 catch-ups still use the world connection.
        for mock_fn, name in [
            (mock_catch_up_hourly, "catch_up_hourly"),
            (mock_catch_up_solar, "catch_up_solar"),
            (mock_catch_up_forecasts, "catch_up_forecasts"),
        ]:
            if mock_fn.called:
                conn_arg = mock_fn.call_args[0][0]
                assert conn_arg is world_conn, (
                    f"{name} must use world connection; got {conn_arg!r}"
                )


# ---------------------------------------------------------------------------
# ROT-3: DB path sanity check (belt-and-suspenders, no mocking)
# ---------------------------------------------------------------------------

class TestDBPathSanity:
    """ROT-3 — Verify the DB path constants are correctly split post-K1."""

    def test_forecasts_db_path_distinct_from_world_db_path(self):
        from src.state.db import ZEUS_WORLD_DB_PATH, ZEUS_FORECASTS_DB_PATH
        assert ZEUS_FORECASTS_DB_PATH != ZEUS_WORLD_DB_PATH, (
            "forecasts.db and world.db must be different paths"
        )
        assert str(ZEUS_FORECASTS_DB_PATH).endswith("zeus-forecasts.db"), (
            f"Unexpected forecasts DB path: {ZEUS_FORECASTS_DB_PATH}"
        )
        assert str(ZEUS_WORLD_DB_PATH).endswith("zeus-world.db"), (
            f"Unexpected world DB path: {ZEUS_WORLD_DB_PATH}"
        )

    def test_get_forecasts_connection_returns_forecasts_path(self, tmp_path):
        """get_forecasts_connection opens a file that ends in zeus-forecasts.db.

        We temporarily point the path constant to a tmp file to avoid touching
        production state/ during tests.
        """
        from src.state import db as db_module
        fake_forecasts = tmp_path / "zeus-forecasts.db"
        fake_world = tmp_path / "zeus-world.db"
        orig_f = db_module.ZEUS_FORECASTS_DB_PATH
        orig_w = db_module.ZEUS_WORLD_DB_PATH
        try:
            db_module.ZEUS_FORECASTS_DB_PATH = fake_forecasts
            db_module.ZEUS_WORLD_DB_PATH = fake_world
            # Create minimal schema so connection succeeds.
            sqlite3.connect(str(fake_forecasts)).close()
            sqlite3.connect(str(fake_world)).close()

            fc = db_module.get_forecasts_connection()
            wc = db_module.get_world_connection()
            fc_path = fc.execute("PRAGMA database_list").fetchone()[2]
            wc_path = wc.execute("PRAGMA database_list").fetchone()[2]
            fc.close()
            wc.close()

            assert fc_path.endswith("zeus-forecasts.db"), (
                f"get_forecasts_connection opened {fc_path!r}"
            )
            assert wc_path.endswith("zeus-world.db"), (
                f"get_world_connection opened {wc_path!r}"
            )
        finally:
            db_module.ZEUS_FORECASTS_DB_PATH = orig_f
            db_module.ZEUS_WORLD_DB_PATH = orig_w
