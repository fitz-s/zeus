# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T3
# Lifecycle: created=2026-05-21; last_reviewed=2026-05-21; last_reused=never
# Purpose: Unit tests for shoulder_exposure_ledger writer/reader — schema, round-trip, aggregation.
# Reuse: Run when shoulder_exposure_ledger schema or read/write functions change.

"""Tests for shoulder_exposure_ledger.py — writer/reader for shoulder_exposure_ledger table."""

from __future__ import annotations

import sqlite3
from datetime import timezone, datetime

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_world_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    from src.state.schema.shoulder_exposure_ledger_schema import ensure_table
    ensure_table(conn)
    return conn


# ---------------------------------------------------------------------------
# SCHEMA tests
# ---------------------------------------------------------------------------

class TestShoulderExposureLedgerSchema:
    """Verify schema created correctly."""

    def test_table_created(self) -> None:
        conn = _make_world_conn()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='shoulder_exposure_ledger'"
        ).fetchone()
        assert row is not None, "shoulder_exposure_ledger table must exist"

    def test_schema_version_check_constraint(self) -> None:
        """schema_version must only accept 22 or 23 (current T3 range)."""
        conn = _make_world_conn()
        # Valid: 22
        conn.execute(
            "INSERT INTO shoulder_exposure_ledger "
            "(shoulder_side, weather_system_cluster, city, target_date, source, regime, "
            " notional_usd, decision_event_id, observed_at, schema_version) "
            "VALUES ('sell','cluster1','Atlanta','2026-07-15','ecmwf','heat_dome',100.0,'deid1','2026-07-10T12:00:00Z',22)"
        )
        # Valid: 23
        conn.execute(
            "INSERT INTO shoulder_exposure_ledger "
            "(shoulder_side, weather_system_cluster, city, target_date, source, regime, "
            " notional_usd, decision_event_id, observed_at, schema_version) "
            "VALUES ('sell','cluster1','Chicago','2026-07-15','ecmwf','heat_dome',100.0,'deid2','2026-07-10T12:00:00Z',23)"
        )
        # Invalid: 21
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO shoulder_exposure_ledger "
                "(shoulder_side, weather_system_cluster, city, target_date, source, regime, "
                " notional_usd, decision_event_id, observed_at, schema_version) "
                "VALUES ('sell','cluster1','Dallas','2026-07-15','ecmwf','heat_dome',100.0,'deid3','2026-07-10T12:00:00Z',21)"
            )


# ---------------------------------------------------------------------------
# WRITER tests
# ---------------------------------------------------------------------------

class TestShoulderExposureLedgerWriter:
    """write_shoulder_exposure_entry and read_cluster_exposure."""

    def test_write_and_read_round_trip(self) -> None:
        conn = _make_world_conn()
        from src.state.shoulder_exposure_ledger import (
            write_shoulder_exposure_entry,
            read_cluster_exposure,
        )
        write_shoulder_exposure_entry(
            shoulder_side="sell",
            weather_system_cluster="heat_dome_east_2026_07_15",
            city="Atlanta",
            target_date="2026-07-15",
            source="ecmwf",
            regime="heat_dome",
            notional_usd=250.0,
            decision_event_id="deid_v1_test_001",
            observed_at="2026-07-10T12:00:00Z",
            conn=conn,
        )
        total = read_cluster_exposure(
            cluster="heat_dome_east_2026_07_15",
            side="sell",
            conn=conn,
        )
        assert total == pytest.approx(250.0)

    def test_read_returns_zero_for_empty_cluster(self) -> None:
        conn = _make_world_conn()
        from src.state.shoulder_exposure_ledger import read_cluster_exposure
        total = read_cluster_exposure(
            cluster="nonexistent_cluster",
            side="sell",
            conn=conn,
        )
        assert total == pytest.approx(0.0)

    def test_aggregates_multiple_entries_same_cluster(self) -> None:
        conn = _make_world_conn()
        from src.state.shoulder_exposure_ledger import (
            write_shoulder_exposure_entry,
            read_cluster_exposure,
        )
        for city, notional in [("Atlanta", 100.0), ("Chicago", 150.0)]:
            write_shoulder_exposure_entry(
                shoulder_side="sell",
                weather_system_cluster="heat_dome_east_2026_07_15",
                city=city,
                target_date="2026-07-15",
                source="ecmwf",
                regime="heat_dome",
                notional_usd=notional,
                decision_event_id=f"deid_v1_{city}",
                observed_at="2026-07-10T12:00:00Z",
                conn=conn,
            )
        total = read_cluster_exposure(
            cluster="heat_dome_east_2026_07_15",
            side="sell",
            conn=conn,
        )
        assert total == pytest.approx(250.0)

    def test_side_isolation(self) -> None:
        """Sell and buy exposures tracked separately per side."""
        conn = _make_world_conn()
        from src.state.shoulder_exposure_ledger import (
            write_shoulder_exposure_entry,
            read_cluster_exposure,
        )
        write_shoulder_exposure_entry(
            shoulder_side="sell",
            weather_system_cluster="cluster_x",
            city="Atlanta",
            target_date="2026-07-15",
            source="ecmwf",
            regime="heat_dome",
            notional_usd=300.0,
            decision_event_id="deid_sell",
            observed_at="2026-07-10T12:00:00Z",
            conn=conn,
        )
        write_shoulder_exposure_entry(
            shoulder_side="buy",
            weather_system_cluster="cluster_x",
            city="Chicago",
            target_date="2026-07-15",
            source="ecmwf",
            regime="heat_dome",
            notional_usd=200.0,
            decision_event_id="deid_buy",
            observed_at="2026-07-10T12:00:00Z",
            conn=conn,
        )
        sell_total = read_cluster_exposure("cluster_x", "sell", conn=conn)
        buy_total = read_cluster_exposure("cluster_x", "buy", conn=conn)
        assert sell_total == pytest.approx(300.0)
        assert buy_total == pytest.approx(200.0)

    def test_cluster_isolation(self) -> None:
        """Different clusters don't bleed into each other."""
        conn = _make_world_conn()
        from src.state.shoulder_exposure_ledger import (
            write_shoulder_exposure_entry,
            read_cluster_exposure,
        )
        write_shoulder_exposure_entry(
            shoulder_side="sell",
            weather_system_cluster="cluster_a",
            city="Atlanta",
            target_date="2026-07-15",
            source="ecmwf",
            regime="heat_dome",
            notional_usd=500.0,
            decision_event_id="deid_a",
            observed_at="2026-07-10T12:00:00Z",
            conn=conn,
        )
        total_b = read_cluster_exposure("cluster_b", "sell", conn=conn)
        assert total_b == pytest.approx(0.0)

    def test_read_distinct_cities(self) -> None:
        """read_distinct_cities_in_cluster returns the set of cities with sell entries."""
        conn = _make_world_conn()
        from src.state.shoulder_exposure_ledger import (
            write_shoulder_exposure_entry,
            read_distinct_cities_in_cluster,
        )
        for city in ["Atlanta", "Chicago", "Dallas"]:
            write_shoulder_exposure_entry(
                shoulder_side="sell",
                weather_system_cluster="heat_dome_east_2026_07_15",
                city=city,
                target_date="2026-07-15",
                source="ecmwf",
                regime="heat_dome",
                notional_usd=100.0,
                decision_event_id=f"deid_{city}",
                observed_at="2026-07-10T12:00:00Z",
                conn=conn,
            )
        cities = read_distinct_cities_in_cluster(
            cluster="heat_dome_east_2026_07_15",
            side="sell",
            conn=conn,
        )
        assert set(cities) == {"Atlanta", "Chicago", "Dallas"}
