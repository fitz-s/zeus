# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: operator follow-up 2026-06-11 ~16:40Z (twin-authority #8, serve-
#   freshest-eligible reconciliation). The FSR producer's per-family election
#   (ranked_coverage, fairness path) must mint the family's event from the freshest
#   LIVE_ELIGIBLE coverage row when one exists — a BLOCKED newer run (the transient
#   state of every cycle-build window, 4x/day) must never displace an eligible older
#   run (没有新的就用老的). When NO eligible row exists, the family still mints its
#   freshest row BRANDED with its honest statuses (serve-what-exists) — the reactor's
#   intake gate now passes branded events through to the serving authority.
"""RELATIONSHIP tests across the boundary

    source_run_coverage rows (forecasts DB) -> ForecastSnapshotReadyTrigger
    .scan_committed_snapshots election -> minted FSR payload coverage statuses

Pins the producer half of the serve-freshest-eligible law: the event's payload
statuses come from the row the election serves, and the election prefers
LIVE_ELIGIBLE over any fresher non-eligible row.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

import pytest

from src.events.event_writer import EventWriter
from src.events.triggers.forecast_snapshot_ready import ForecastSnapshotReadyTrigger
from src.state.db import init_schema, init_schema_forecasts

UTC = timezone.utc
_MEMBERS_JSON = "[" + ",".join(str(i) for i in range(1, 52)) + "]"
_DECISION_TIME = datetime(2026, 6, 3, 5, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _hermetic_flags(monkeypatch: Any) -> None:
    """Pin the two live-config-dependent toggles: fairness election ON (the live
    posture under test) and the replacement posterior filter OFF (fixtures carry
    no forecast_posteriors; the filter is orthogonal to the election law)."""
    # Wave-1 2026-06-12: coverage fairness is unconditional (flag deleted) — no patch needed.
    monkeypatch.setattr(
        "src.events.triggers.forecast_snapshot_ready._replacement_trade_authority_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        "src.events.triggers.forecast_snapshot_ready.market_phase_admits",
        lambda **_kwargs: True,
    )


def _insert_run(
    conn: sqlite3.Connection,
    *,
    city: str,
    run_id: str,
    snap_id: int,
    issue_time: str,
    computed_at: str,
    completeness: str,
    readiness: str,
) -> None:
    """One source_run + coverage + snapshot triple with configurable statuses."""
    city_id = city.lower().replace(" ", "_")
    conn.execute(
        """
        INSERT OR IGNORE INTO source_run (
            source_run_id, source_id, track, release_calendar_key, ingest_mode, origin_mode,
            source_cycle_time, source_issue_time, source_available_at, captured_at,
            target_local_date, city_id, city_timezone, temperature_metric, dataset_id,
            expected_members, observed_members, expected_steps_json, observed_steps_json,
            completeness_status, status
        ) VALUES (
            ?, 'ecmwf-open-data', 'ens', ?, 'SCHEDULED_LIVE', 'SCHEDULED_LIVE',
            ?, ?, ?, ?, '2026-06-04',
            ?, 'America/Chicago', 'high', 'v1',
            51, 51, '[0,3,6]', '[0,3,6]', 'COMPLETE', 'SUCCESS'
        )
        """,
        (run_id, issue_time, issue_time, issue_time, issue_time, computed_at, city_id),
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
            ?, ?, 'ecmwf-open-data', 'ensemble_snapshots_db_reader', ?, 'ens',
            ?, ?, 'America/Chicago', '2026-06-04', 'high', 'temperature',
            'high_temp', 'v1', 51, 51, '[0,3,6]', '[0,3,6]', ?,
            '2026-06-04T05:00:00+00:00', '2026-06-05T05:00:00+00:00',
            ?, ?, ?, '2026-06-05T04:00:00+00:00'
        )
        """,
        (
            f"cov-{run_id}",
            run_id,
            issue_time,
            city_id,
            city,
            f"[{snap_id}]",
            completeness,
            readiness,
            computed_at,
        ),
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
            ?, '2026-06-04T06:00:00+00:00',
            ?, ?, 6, ?,
            'ecmwf', 'v1', 'ecmwf-open-data', 'ensemble_snapshots_db_reader', ?,
            ?, ?, ?, ?,
            'VERIFIED', 'OK', 0, 1,
            'FULLY_INSIDE_TARGET_LOCAL_DAY', '2026-06-04T05:00:00+00:00', 6, 'C', 0
        )
        """,
        (
            snap_id,
            "Chicago",
            issue_time,
            issue_time,
            computed_at,
            _MEMBERS_JSON,
            run_id,
            issue_time,
            issue_time,
            issue_time,
            issue_time,
        ),
    )


def _conns() -> tuple[sqlite3.Connection, sqlite3.Connection]:
    forecasts = sqlite3.connect(":memory:")
    forecasts.row_factory = sqlite3.Row
    init_schema_forecasts(forecasts)
    world = sqlite3.connect(":memory:")
    init_schema(world)
    return forecasts, world


def _scan(forecasts: sqlite3.Connection, world: sqlite3.Connection) -> list[dict]:
    import json

    trigger = ForecastSnapshotReadyTrigger(
        EventWriter(world),
        live_eligibility_reader=lambda _sr, _cov, _snap, _now: True,
    )
    trigger.scan_committed_snapshots(
        forecasts_conn=forecasts,
        decision_time=_DECISION_TIME,
        received_at=_DECISION_TIME.isoformat(),
        limit=10,
    )
    rows = world.execute("SELECT payload_json FROM opportunity_events").fetchall()
    return [json.loads(r[0]) for r in rows]


def test_blocked_newest_run_does_not_displace_eligible_older_run():
    """ANTIBODY (producer half of serve-freshest-eligible): a family holding a
    BLOCKED NEWER run (cycle-build window) and a LIVE_ELIGIBLE OLDER run mints
    its event from the ELIGIBLE older run — statuses AND source_run_id."""
    forecasts, world = _conns()
    _insert_run(
        forecasts,
        city="Chicago",
        run_id="run-old-eligible",
        snap_id=1,
        issue_time="2026-06-02T00:00:00+00:00",
        computed_at="2026-06-02T04:00:00+00:00",
        completeness="COMPLETE",
        readiness="LIVE_ELIGIBLE",
    )
    _insert_run(
        forecasts,
        city="Chicago",
        run_id="run-new-blocked",
        snap_id=2,
        issue_time="2026-06-03T00:00:00+00:00",  # NEWER
        computed_at="2026-06-03T04:30:00+00:00",  # NEWER computed
        completeness="PARTIAL",
        readiness="BLOCKED",
    )
    forecasts.commit()

    payloads = _scan(forecasts, world)
    assert len(payloads) == 1, f"per-family election must mint exactly one event, got {len(payloads)}"
    p = payloads[0]
    assert p["coverage_readiness_status"] == "LIVE_ELIGIBLE", (
        f"the BLOCKED newer run displaced the eligible older run: {p['source_run_id']} "
        f"readiness={p['coverage_readiness_status']}"
    )
    assert p["coverage_completeness_status"] == "COMPLETE"
    assert p["source_run_id"] == "run-old-eligible"


def test_family_with_only_blocked_rows_still_mints_branded():
    """Serve-what-exists: a family with NO eligible row anywhere still mints its
    freshest row BRANDED with honest statuses — it must not go dark (the reactor
    gate passes branded events to the serving authority, which decides)."""
    forecasts, world = _conns()
    _insert_run(
        forecasts,
        city="Chicago",
        run_id="run-only-blocked",
        snap_id=3,
        issue_time="2026-06-03T00:00:00+00:00",
        computed_at="2026-06-03T04:30:00+00:00",
        completeness="PARTIAL",
        readiness="BLOCKED",
    )
    forecasts.commit()

    payloads = _scan(forecasts, world)
    assert len(payloads) == 1, "a family with only branded-blocked coverage must still mint"
    p = payloads[0]
    assert p["coverage_readiness_status"] == "BLOCKED"
    assert p["coverage_completeness_status"] == "PARTIAL"
    assert p["source_run_id"] == "run-only-blocked"
