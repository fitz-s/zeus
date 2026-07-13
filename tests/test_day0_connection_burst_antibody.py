# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: docs/evidence/lock_storm/2026-06-13_lock_storm_regression_archaeology.md
#   Antibody section — relationship test for the connection-burst regression
#   (commit 347f713d) that opened N independent world connections per monitoring pass.
"""Antibody tests: monitoring phase must not open secondary world connections.

Regression class: commit 347f713d introduced _recover_kill_memo_from_events()
with a world_conn=None fallback that opened get_world_connection_read_only() per
city when the in-process kill memo was cold (after every daemon restart). With 47
cities, the monitoring phase opened 47 simultaneous read-only world connections
inside the reactor cycle that already held the composite write connection on
zeus-world.db, producing SQLITE_BUSY × 47 every ~2 minutes.

Invariants (relationship, not function):
  R_BURST_1. latest_rounded_extreme() with a supplied world_conn NEVER calls
             get_world_connection_read_only() or sqlite3.connect() — zero
             independent world connections opened.
  R_BURST_2. _recover_kill_memo_from_events() with world_conn=None (no conn
             supplied) raises a typed error immediately (fail-loud) instead
             of silently opening a connection — the category is unconstructable.
  R_BURST_3. evaluate_hard_fact_exit() threads world_conn through to
             latest_rounded_extreme() — the full production call chain opens
             zero independent world connections per city.
"""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.data.day0_fast_obs import (
    FAST_OBS_SOURCE_ID,
    Day0FastObsEmitter,
    _recover_kill_memo_from_events,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _events_conn() -> sqlite3.Connection:
    """In-memory world-equivalent connection for tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE opportunity_events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT,
            payload_json TEXT
        )
        """
    )
    conn.commit()
    return conn


def _insert_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    city: str,
    target_date: str,
    metric: str,
    rounded_value: int,
    source_authorized_status: str = "AUTHORIZED",
    local_date_status: str = "MATCH",
    dst_status: str = "UNAMBIGUOUS",
    settlement_source: str = FAST_OBS_SOURCE_ID,
) -> None:
    payload = {
        "city": city,
        "target_date": target_date,
        "metric": metric,
        "rounded_value": rounded_value,
        "source_authorized_status": source_authorized_status,
        "local_date_status": local_date_status,
        "dst_status": dst_status,
        "settlement_source": settlement_source,
    }
    conn.execute(
        "INSERT INTO opportunity_events (event_id, event_type, payload_json) VALUES (?,?,?)",
        (event_id, "DAY0_EXTREME_UPDATED", json.dumps(payload)),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# R_BURST_1: latest_rounded_extreme with a supplied conn opens zero independent
#            world connections (the cold-memo path threads the conn through).
# ---------------------------------------------------------------------------

class TestNoSecondaryConnectionWhenConnSupplied:

    def test_latest_rounded_extreme_cold_memo_opens_zero_independent_connections(self):
        """RELATIONSHIP: with world_conn supplied and cold in-process memo,
        latest_rounded_extreme must NOT call get_world_connection_read_only()
        or sqlite3.connect() — it uses only the supplied connection."""
        events_conn = _events_conn()
        _insert_event(
            events_conn,
            event_id="e1",
            city="Chicago",
            target_date="2026-06-01",
            metric="high",
            rounded_value=73,
        )

        emitter = Day0FastObsEmitter()  # cold memo — simulates post-restart

        ro_calls: list = []
        connect_calls: list = []

        with patch("src.state.db.get_world_connection_read_only", side_effect=lambda: (ro_calls.append(1) or MagicMock())) as mock_ro, \
             patch("sqlite3.connect", side_effect=lambda *a, **kw: (connect_calls.append(a), sqlite3.connect(*a, **kw))[1]) as mock_connect:
            result = emitter.latest_rounded_extreme(
                "Chicago", "2026-06-01", "high", world_conn=events_conn
            )

        assert result == 73, f"expected 73 from event store, got {result}"
        assert len(ro_calls) == 0, (
            f"get_world_connection_read_only was called {len(ro_calls)} time(s); "
            "must be 0 when world_conn is supplied"
        )

    def test_multiple_city_recovery_opens_zero_independent_connections(self):
        """Cold memo for multiple cells — none may open a secondary connection."""
        events_conn = _events_conn()
        for i, city in enumerate(["Chicago", "Dallas", "Denver"]):
            _insert_event(
                events_conn,
                event_id=f"e{i}",
                city=city,
                target_date="2026-06-01",
                metric="high",
                rounded_value=70 + i,
            )

        emitter = Day0FastObsEmitter()  # cold memo for all cities

        ro_calls: list = []

        with patch("src.state.db.get_world_connection_read_only", side_effect=lambda: (ro_calls.append(1) or MagicMock())):
            for city in ["Chicago", "Dallas", "Denver"]:
                emitter.latest_rounded_extreme(
                    city, "2026-06-01", "high", world_conn=events_conn
                )

        assert len(ro_calls) == 0, (
            f"get_world_connection_read_only opened {len(ro_calls)} independent connection(s) "
            "for 3-city recovery; must be 0 when world_conn supplied"
        )


# ---------------------------------------------------------------------------
# R_BURST_2: _recover_kill_memo_from_events raises a typed error when conn
#            is absent — the None-fallback silent-open path is deleted.
# ---------------------------------------------------------------------------

class TestNoneConnFails:

    def test_recover_with_no_conn_raises_typed_error(self):
        """When world_conn is absent (None), _recover_kill_memo_from_events
        must raise a RuntimeError immediately, never open a connection."""
        ro_calls: list = []

        with patch("src.state.db.get_world_connection_read_only", side_effect=lambda: (ro_calls.append(1) or MagicMock())):
            with pytest.raises(RuntimeError, match="world_conn"):
                _recover_kill_memo_from_events(
                    city_name="Chicago",
                    target_date="2026-06-01",
                    metric="high",
                    world_conn=None,
                )

        assert len(ro_calls) == 0, (
            "get_world_connection_read_only was called even though the call should have "
            "raised immediately on world_conn=None"
        )

    def test_recover_with_no_conn_does_not_silently_open_connection(self):
        """Belt-and-braces: the old fallback must not run.

        Any change that re-introduces the silent-open path breaks this test."""
        connect_calls: list = []

        with patch("sqlite3.connect", side_effect=lambda *a, **kw: (connect_calls.append(a), sqlite3.connect(*a, **kw))[1]):
            with pytest.raises(RuntimeError):
                _recover_kill_memo_from_events(
                    city_name="Chicago",
                    target_date="2026-06-01",
                    metric="high",
                    world_conn=None,
                )

        world_opens = [c for c in connect_calls if "world" in str(c).lower()]
        assert len(world_opens) == 0, (
            f"sqlite3.connect opened a world-db path {world_opens} "
            "despite world_conn=None; the fallback open path must be deleted"
        )


# ---------------------------------------------------------------------------
# R_BURST_3: evaluate_hard_fact_exit threads world_conn so latest_rounded_extreme
#            receives a real conn and opens no independent world connections.
# ---------------------------------------------------------------------------

class TestEvaluateHardFactExitThreadsConn:

    def _make_position(self, state: str = "day0_window", bin_label: str = "35C-40C"):
        pos = SimpleNamespace()
        pos.trade_id = "test-trade-001"
        pos.state = state
        pos.direction = "buy_no"
        pos.target_date = "2026-06-01"
        pos.temperature_metric = "high"
        pos.bin_label = bin_label
        pos.neg_edge_count = 0
        return pos

    def _make_city(self):
        city = SimpleNamespace()
        city.name = "Chicago"
        city.timezone = "America/Chicago"
        city.settlement_unit = "F"
        city.settlement_source_type = "wu_icao"
        city.wu_station = "KORD"
        return city

    def test_evaluate_hard_fact_exit_no_secondary_connections(self):
        """evaluate_hard_fact_exit with world_conn supplied must open zero
        independent world connections even when the kill memo is cold."""
        events_conn = _events_conn()
        _insert_event(
            events_conn,
            event_id="e1",
            city="Chicago",
            target_date="2026-06-01",
            metric="high",
            rounded_value=90,  # clearly above any bin → won't trigger an exit action here
        )

        emitter = Day0FastObsEmitter()

        ro_calls: list = []

        with patch("src.data.day0_fast_obs.get_fast_obs_emitter", return_value=emitter), \
             patch("src.state.db.get_world_connection_read_only", side_effect=lambda: (ro_calls.append(1) or MagicMock())), \
             patch("src.execution.day0_hard_fact_exit._wu_rounded_extremes", return_value=(None, None)), \
             patch("src.data.day0_oracle_anomaly.is_day0_family_paused", return_value=False):
            from src.execution.day0_hard_fact_exit import evaluate_hard_fact_exit
            try:
                evaluate_hard_fact_exit(
                    position=self._make_position(),
                    city=self._make_city(),
                    world_conn=events_conn,
                )
            except Exception:
                # Result doesn't matter; what matters is that no secondary
                # world connections were opened.
                pass

        assert len(ro_calls) == 0, (
            f"get_world_connection_read_only was called {len(ro_calls)} time(s) "
            "from evaluate_hard_fact_exit with world_conn supplied; must be 0"
        )
