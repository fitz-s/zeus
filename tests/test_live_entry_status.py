# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-02_live_entry_data_contract/PLAN_v4.md Phase 10 live-entry blocker status.
"""Live-entry forecast blocker status tests."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timezone

from src.config import EntryForecastRolloutMode, entry_forecast_config
from src.contracts.ensemble_snapshot_provenance import ECMWF_OPENDATA_HIGH_DATA_VERSION
from src.data.live_entry_status import build_live_entry_forecast_status
from src.data.producer_readiness import PRODUCER_READINESS_STRATEGY_KEY
from src.state.db import init_schema
from src.state.schema.v2_schema import apply_v2_schema

UTC = timezone.utc


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_v2_schema(conn)
    return conn


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _insert_snapshot(conn: sqlite3.Connection, *, linked: bool) -> None:
    conn.execute(
        """
        INSERT INTO ensemble_snapshots_v2 (
            city, target_date, temperature_metric, physical_quantity,
            observation_field, issue_time, valid_time, available_at, fetch_time,
            lead_hours, members_json, model_version, data_version,
            source_id, source_transport, source_run_id, release_calendar_key,
            source_cycle_time, source_release_time, source_available_at,
            training_allowed, causality_status, boundary_ambiguous,
            ambiguous_member_count, manifest_hash, provenance_json, authority,
            members_unit, local_day_start_utc, step_horizon_hours
        ) VALUES (
            'London', '2026-05-08', 'high', 'mx2t6_local_calendar_day_max',
            'high_temp', '2026-05-03T00:00:00+00:00', '2026-05-08',
            '2026-05-03T08:10:00+00:00', '2026-05-03T08:15:00+00:00',
            120.0, :members_json, 'ecmwf_ens', :data_version,
            :source_id, :source_transport, :source_run_id, :release_calendar_key,
            :source_cycle_time, :source_release_time, :source_available_at,
            1, 'OK', 0, 0, :manifest_hash, '{}', 'VERIFIED', 'degC',
            '2026-05-08T00:00:00+00:00', 144.0
        )
        """,
        {
            "members_json": json.dumps([18.0] * 51),
            "data_version": ECMWF_OPENDATA_HIGH_DATA_VERSION,
            "source_id": "ecmwf_open_data" if linked else None,
            "source_transport": "ensemble_snapshots_v2_db_reader" if linked else None,
            "source_run_id": "source-run-1" if linked else None,
            "release_calendar_key": "ecmwf_open_data:mx2t6_high:full" if linked else None,
            "source_cycle_time": "2026-05-03T00:00:00+00:00" if linked else None,
            "source_release_time": "2026-05-03T08:05:00+00:00" if linked else None,
            "source_available_at": "2026-05-03T08:10:00+00:00" if linked else None,
            "manifest_hash": "3" * 64,
        },
    )


def _insert_producer_readiness(conn: sqlite3.Connection, *, status: str, reasons: list[str]) -> None:
    conn.execute(
        """
        INSERT INTO readiness_state (
            readiness_id, scope_key, scope_type, city_id, city, city_timezone,
            target_local_date, metric, temperature_metric, physical_quantity,
            observation_field, data_version, source_id, track, source_run_id,
            market_family, event_id, condition_id, token_ids_json,
            strategy_key, status, reason_codes_json, computed_at, expires_at,
            dependency_json, provenance_json
        ) VALUES (
            :readiness_id, :scope_key, 'city_metric', 'LONDON', 'London', 'Europe/London',
            '2026-05-08', NULL, 'high', 'mx2t6_local_calendar_day_max',
            'high_temp', :data_version, 'ecmwf_open_data', 'mx2t6_high_full_horizon', 'source-run-1',
            NULL, NULL, NULL, '[]',
            :strategy_key, :status, :reason_codes_json, :computed_at, :expires_at,
            '{}', '{}'
        )
        """,
        {
            "readiness_id": f"producer-readiness-{status}",
            "scope_key": f"producer|{status}",
            "data_version": ECMWF_OPENDATA_HIGH_DATA_VERSION,
            "strategy_key": PRODUCER_READINESS_STRATEGY_KEY,
            "status": status,
            "reason_codes_json": json.dumps(reasons),
            "computed_at": _utc(2026, 5, 3, 9).isoformat(),
            "expires_at": _utc(2026, 5, 3, 12).isoformat() if status == "LIVE_ELIGIBLE" else None,
        },
    )


def test_empty_world_db_surfaces_zero_rows_and_no_future_coverage() -> None:
    blocked_cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.BLOCKED)
    status = build_live_entry_forecast_status(_conn(), config=blocked_cfg)

    assert status.status == "BLOCKED"
    assert "ZERO_EXECUTABLE_OPENDATA_ROWS" in status.blockers
    assert "NO_FUTURE_TARGET_DATE_COVERAGE" in status.blockers
    assert "ENTRY_FORECAST_ROLLOUT_BLOCKED" in status.blockers


def test_legacy_v2_rows_do_not_count_as_executable() -> None:
    conn = _conn()
    _insert_snapshot(conn, linked=False)

    status = build_live_entry_forecast_status(conn, config=entry_forecast_config())

    assert status.executable_row_count == 0
    assert "ZERO_EXECUTABLE_OPENDATA_ROWS" in status.blockers


def test_producer_readiness_reasons_are_exposed_as_blockers() -> None:
    conn = _conn()
    _insert_snapshot(conn, linked=True)
    _insert_producer_readiness(conn, status="BLOCKED", reasons=["NO_FUTURE_TARGET_DATE_COVERAGE"])

    status = build_live_entry_forecast_status(conn, config=entry_forecast_config())

    assert status.executable_row_count == 1
    assert status.producer_readiness_count == 1
    assert "ZERO_EXECUTABLE_OPENDATA_ROWS" not in status.blockers
    assert "NO_FUTURE_TARGET_DATE_COVERAGE" in status.blockers


def test_live_eligible_data_still_blocks_when_rollout_mode_is_blocked() -> None:
    conn = _conn()
    _insert_snapshot(conn, linked=True)
    _insert_producer_readiness(conn, status="LIVE_ELIGIBLE", reasons=["PRODUCER_COVERAGE_READY"])
    blocked_cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.BLOCKED)

    status = build_live_entry_forecast_status(
        conn,
        config=blocked_cfg,
        now_utc=_utc(2026, 5, 3, 10),
    )

    assert status.producer_live_eligible_count == 1
    assert status.blockers == ("ENTRY_FORECAST_ROLLOUT_BLOCKED",)


def test_expired_live_eligible_producer_readiness_blocks_status() -> None:
    conn = _conn()
    _insert_snapshot(conn, linked=True)
    _insert_producer_readiness(conn, status="LIVE_ELIGIBLE", reasons=["PRODUCER_COVERAGE_READY"])

    status = build_live_entry_forecast_status(
        conn,
        config=entry_forecast_config(),
        now_utc=_utc(2026, 5, 3, 13),
    )

    assert status.producer_live_eligible_count == 0
    assert "PRODUCER_READINESS_EXPIRED" in status.blockers


def test_live_mode_without_blockers_reports_live_eligible_status() -> None:
    conn = _conn()
    _insert_snapshot(conn, linked=True)
    _insert_producer_readiness(conn, status="LIVE_ELIGIBLE", reasons=["PRODUCER_COVERAGE_READY"])
    cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.LIVE)

    status = build_live_entry_forecast_status(
        conn,
        config=cfg,
        now_utc=_utc(2026, 5, 3, 10),
    )

    assert status.status == "LIVE_ELIGIBLE"
    assert status.blockers == ()
