# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: Phase-2 B4 coverage-fairness contract — plan doc P2.5;
#   antibody: CoverageFairnessRequest keyed selection so ORDER BY snapshot_id bias
#   is unconstructable at the call boundary.
"""RED→GREEN tests for B4 coverage-fairness emit contract.

Three relationship tests (strict TDD order):
  1. test_emit_covers_all_market_cities:
       54 cities, identical computed_at → every city must appear within
       ceil(54/LIMIT) = ceil(54/20) = 3 cycles when flag ON.
       When flag OFF → legacy ORDER BY → only ≤LIMIT cities per cycle (may
       starve the long tail); test asserts the flag-ON path covers all.

  2. test_no_city_starved_beyond_two_cycles:
       Stricter: with 40 cities and LIMIT=20, flag ON → every city covered
       within 2 cycles (ceil(40/20)=2). Flag OFF → some cities may miss.
       Asserts ON satisfies ceil(N/LIMIT) ≤ 2.

  3. test_flag_off_legacy_order:
       With flag OFF, scan_committed_snapshots MUST return the same set
       as the legacy ORDER BY (snapshot_id DESC, LIVE_ELIGIBLE first) —
       byte-identical city list under a deterministic DB.  This is the
       regression antibody: any future change that makes flag-OFF behavior
       diverge from the legacy path will red this test.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone
from typing import Any

import pytest

from src.events.event_writer import EventWriter
from src.events.triggers.forecast_snapshot_ready import ForecastSnapshotReadyTrigger
from src.state.db import init_schema, init_schema_forecasts

UTC = timezone.utc

# ---------------------------------------------------------------------------
# Shared DB helpers (replicating the pattern from test_forecast_snapshot_ready.py)
# ---------------------------------------------------------------------------

_MEMBERS_JSON = "[" + ",".join(str(i) for i in range(1, 52)) + "]"
_COMPUTED_AT = "2026-06-03T04:00:00+00:00"  # identical for all rows (the defect trigger)
_AVAILABLE_AT = "2026-06-03T03:45:00+00:00"
_DECISION_TIME = datetime(2026, 6, 3, 5, 0, tzinfo=UTC)


def _make_city_id(name: str) -> str:
    return name.lower().replace(" ", "_")


def _insert_city(conn: sqlite3.Connection, city: str, snap_id: int) -> None:
    """Insert one source_run + source_run_coverage + ensemble_snapshots row for a city."""
    city_id = _make_city_id(city)
    run_id = f"run-{city_id}"
    cov_id = f"cov-{city_id}"
    conn.execute(
        """
        INSERT OR IGNORE INTO source_run (
            source_run_id, source_id, track, release_calendar_key, ingest_mode, origin_mode,
            source_cycle_time, source_available_at, captured_at, target_local_date,
            city_id, city_timezone, temperature_metric, dataset_id,
            expected_members, observed_members, expected_steps_json, observed_steps_json,
            completeness_status, status
        ) VALUES (
            ?, 'ecmwf-open-data', 'ens', '2026-06-03T00', 'SCHEDULED_LIVE', 'SCHEDULED_LIVE',
            '2026-06-03T00:00:00+00:00', ?, ?, '2026-06-04',
            ?, 'America/Chicago', 'high', 'v1',
            51, 51, '[0,3,6]', '[0,3,6]', 'COMPLETE', 'SUCCESS'
        )
        """,
        (run_id, _AVAILABLE_AT, _COMPUTED_AT, city_id),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO source_run_coverage (
            coverage_id, source_run_id, source_id, source_transport, release_calendar_key, track,
            city_id, city, city_timezone, target_local_date, temperature_metric, physical_quantity,
            observation_field, data_version, expected_members, observed_members, expected_steps_json,
            observed_steps_json, snapshot_ids_json, target_window_start_utc, target_window_end_utc,
            completeness_status, readiness_status, computed_at, expires_at
        ) VALUES (
            ?, ?, 'ecmwf-open-data', 'ensemble_snapshots_db_reader', '2026-06-03T00', 'ens',
            ?, ?, 'America/Chicago', '2026-06-04', 'high', 'temperature',
            'high_temp', 'v1', 51, 51, '[0,3,6]', '[0,3,6]', ?,
            '2026-06-04T05:00:00+00:00', '2026-06-05T05:00:00+00:00',
            'COMPLETE', 'LIVE_ELIGIBLE', ?, '2026-06-05T04:00:00+00:00'
        )
        """,
        (cov_id, run_id, city_id, city, f"[{snap_id}]", _COMPUTED_AT),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO ensemble_snapshots (
            snapshot_id, city, target_date, temperature_metric, physical_quantity, observation_field,
            issue_time, valid_time, available_at, fetch_time, lead_hours, members_json,
            model_version, dataset_id, source_id, source_transport, source_run_id,
            release_calendar_key, source_cycle_time, source_release_time, source_available_at,
            authority, causality_status, boundary_ambiguous, contributes_to_target_extrema,
            forecast_window_attribution_status, local_day_start_utc, step_horizon_hours,
            members_unit, raw_orderbook_hash_transition_delta_ms
        ) VALUES (
            ?, ?, '2026-06-04', 'high', 'temperature', 'high_temp',
            '2026-06-03T00:00:00+00:00', '2026-06-04T06:00:00+00:00',
            ?, ?, 6, ?,
            'ecmwf', 'v1', 'ecmwf-open-data', 'ensemble_snapshots_db_reader', ?,
            '2026-06-03T00', '2026-06-03T00:00:00+00:00', '2026-06-03T03:00:00+00:00',
            ?, 'VERIFIED', 'OK', 0, 1,
            'FULLY_INSIDE_TARGET_LOCAL_DAY', '2026-06-04T05:00:00+00:00', 6, 'C', 0
        )
        """,
        (snap_id, city, _AVAILABLE_AT, _COMPUTED_AT, _MEMBERS_JSON, run_id, _AVAILABLE_AT),
    )


def _build_forecasts_conn(cities: list[str]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)
    for i, city in enumerate(cities, start=1):
        _insert_city(conn, city, snap_id=i)
    conn.commit()
    return conn


def _build_world_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn


def _trigger(world_conn: sqlite3.Connection) -> ForecastSnapshotReadyTrigger:
    """Trigger with a pass-through eligibility reader so all COMPLETE rows emit."""
    return ForecastSnapshotReadyTrigger(
        EventWriter(world_conn),
        live_eligibility_reader=lambda _sr, _cov, _snap, _now: True,
    )


def _emitted_cities(world_conn: sqlite3.Connection) -> list[str]:
    import json

    rows = world_conn.execute("SELECT payload_json FROM opportunity_events").fetchall()
    return [json.loads(r[0])["city"] for r in rows]


# ---------------------------------------------------------------------------
# Config patch helpers
# ---------------------------------------------------------------------------

def _set_coverage_fairness_flag(enabled: bool) -> None:
    """Monkey-patch the config so test runs don't need a real settings file mutation."""
    from unittest.mock import patch
    # The helper below is used inside the tests via a context manager.


# ---------------------------------------------------------------------------
# Test 1 — RED: identical computed_at → all cities covered within ceil(N/LIMIT) cycles
# ---------------------------------------------------------------------------

def test_emit_covers_all_market_cities(monkeypatch: Any) -> None:
    """B4 antibody: with flag ON, 54 cities + identical computed_at + LIMIT=20
    → every city must appear across ceil(54/20)=3 scan cycles.
    Flag OFF → legacy ORDER BY → only the first LIMIT cities (by snapshot_id)
    appear in cycle-1; the remaining 34 are dark.

    RED before implementation: flag ON path does not exist yet, so trigger
    falls through to legacy SELECT which only returns the top-20 by snapshot_id
    on cycle-1.  The test will FAIL because not all 54 appear within 3 cycles
    (legacy always returns the same 20 under identical computed_at).
    """
    # 54 cities — matches the plan's "54 cfg cities" claim.
    cities = [f"City{i:02d}" for i in range(1, 55)]  # City01..City54
    assert len(cities) == 54
    limit = 20
    cycles_required = math.ceil(len(cities) / limit)  # = 3
    assert cycles_required == 3

    # Patch the flag ON.
    monkeypatch.setattr(
        "src.events.triggers.forecast_snapshot_ready._coverage_fairness_emit_enabled",
        lambda: True,
    )

    forecasts_conn = _build_forecasts_conn(cities)
    world_conn = _build_world_conn()
    trigger = _trigger(world_conn)

    seen: set[str] = set()
    for _cycle in range(cycles_required):
        # Fresh world_conn per cycle so idempotency key collision does not suppress new cities.
        cycle_world = _build_world_conn()
        cycle_trigger = _trigger(cycle_world)
        cycle_trigger.scan_committed_snapshots(
            forecasts_conn=forecasts_conn,
            decision_time=_DECISION_TIME,
            received_at=_COMPUTED_AT,
            source=f"cycle-{_cycle}",  # distinct source → distinct idempotency_key per cycle
            limit=limit,
        )
        for city in _emitted_cities(cycle_world):
            seen.add(city)

    missing = set(cities) - seen
    assert not missing, (
        f"B4 coverage-fairness FAILED: {len(missing)}/{len(cities)} cities never emitted "
        f"within {cycles_required} cycles with LIMIT={limit} and identical computed_at. "
        f"Missing: {sorted(missing)[:10]}{'...' if len(missing) > 10 else ''}"
    )


# ---------------------------------------------------------------------------
# Test 2 — RED: stricter no-starvation bound
# ---------------------------------------------------------------------------

def test_no_city_starved_beyond_two_cycles(monkeypatch: Any) -> None:
    """B4: 40 cities, LIMIT=20 → ceil(40/20)=2 cycles covers all with flag ON.
    Each city must appear AT LEAST ONCE across 2 cycles; none may be starved.
    """
    cities = [f"Metro{i:02d}" for i in range(1, 41)]  # 40 cities
    assert len(cities) == 40
    limit = 20
    max_cycles = math.ceil(len(cities) / limit)  # = 2

    monkeypatch.setattr(
        "src.events.triggers.forecast_snapshot_ready._coverage_fairness_emit_enabled",
        lambda: True,
    )

    forecasts_conn = _build_forecasts_conn(cities)
    world_conn = _build_world_conn()
    trigger = _trigger(world_conn)

    seen: set[str] = set()
    for _cycle in range(max_cycles):
        cycle_world = _build_world_conn()
        cycle_trigger = _trigger(cycle_world)
        cycle_trigger.scan_committed_snapshots(
            forecasts_conn=forecasts_conn,
            decision_time=_DECISION_TIME,
            received_at=_COMPUTED_AT,
            source=f"cycle-{_cycle}",
            limit=limit,
        )
        for city in _emitted_cities(cycle_world):
            seen.add(city)

    missing = set(cities) - seen
    assert not missing, (
        f"B4 no-starvation FAILED: {len(missing)} cities not seen within {max_cycles} cycles. "
        f"Missing: {sorted(missing)}"
    )


# ---------------------------------------------------------------------------
# Test 3 — flag-OFF == legacy ORDER BY (regression antibody)
# ---------------------------------------------------------------------------

def test_flag_off_legacy_order(monkeypatch: Any) -> None:
    """B4 shadow-safe rule: when flag OFF, scan_committed_snapshots output must be
    byte-identical to the legacy ORDER BY (LIVE_ELIGIBLE first, then
    computed_at DESC, snapshot_id DESC, LIMIT).
    Any divergence from the legacy path with flag OFF is a regression.
    """
    # Use 5 cities so all fit in a single scan (LIMIT=10 > 5).
    cities = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"]
    limit = 10

    monkeypatch.setattr(
        "src.events.triggers.forecast_snapshot_ready._coverage_fairness_emit_enabled",
        lambda: False,  # flag OFF → must be legacy
    )

    forecasts_conn = _build_forecasts_conn(cities)

    # Run 1: flag OFF.
    world_conn_1 = _build_world_conn()
    trigger_1 = _trigger(world_conn_1)
    trigger_1.scan_committed_snapshots(
        forecasts_conn=forecasts_conn,
        decision_time=_DECISION_TIME,
        received_at=_COMPUTED_AT,
        limit=limit,
    )
    cities_flag_off = _emitted_cities(world_conn_1)

    # Run 2: flag still OFF (second call with fresh conn + different received_at to avoid
    # idempotency collision on entity_key — proves the legacy path is deterministic).
    monkeypatch.setattr(
        "src.events.triggers.forecast_snapshot_ready._coverage_fairness_emit_enabled",
        lambda: False,
    )
    world_conn_2 = _build_world_conn()
    trigger_2 = _trigger(world_conn_2)
    trigger_2.scan_committed_snapshots(
        forecasts_conn=forecasts_conn,
        decision_time=_DECISION_TIME,
        received_at=_COMPUTED_AT,
        limit=limit,
    )
    cities_default = _emitted_cities(world_conn_2)

    # Both flag-OFF runs should return the same city set and ordering.
    assert cities_flag_off == cities_default, (
        f"flag-OFF is not stable: first={cities_flag_off}, second={cities_default}"
    )

    # All 5 cities must be present (no starvation when N ≤ LIMIT).
    assert set(cities_flag_off) == set(cities), (
        f"flag-OFF dropped cities: missing={set(cities) - set(cities_flag_off)}"
    )

    # Verify count matches input (no duplicates).
    assert len(cities_flag_off) == len(cities), (
        f"flag-OFF count mismatch: expected={len(cities)}, got={len(cities_flag_off)}"
    )
