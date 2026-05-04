# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-02_live_entry_data_contract/PLAN_v4.md Phase 11 shadow evaluator boundary.
"""Entry forecast shadow boundary tests."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timezone

from src.config import EntryForecastRolloutMode, entry_forecast_config
from src.contracts.ensemble_snapshot_provenance import ECMWF_OPENDATA_HIGH_DATA_VERSION
from src.data.entry_forecast_shadow import evaluate_entry_forecast_shadow
from src.data.forecast_target_contract import build_forecast_target_scope
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


def _scope():
    return build_forecast_target_scope(
        city_id="LONDON",
        city_name="London",
        city_timezone="Europe/London",
        target_local_date=date(2026, 5, 8),
        temperature_metric="high",
        source_cycle_time=_utc(2026, 5, 3),
        data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
        market_refs=("condition-123",),
    )


def _insert_snapshot(conn: sqlite3.Connection, *, linked: bool = True) -> None:
    scope = _scope()
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
            :city, :target_date, 'high', 'mx2t6_local_calendar_day_max',
            'high_temp', '2026-05-03T00:00:00+00:00', :target_date,
            '2026-05-03T08:10:00+00:00', '2026-05-03T08:15:00+00:00',
            120.0, :members_json, 'ecmwf_ens', :data_version,
            :source_id, :source_transport, :source_run_id, :release_calendar_key,
            :source_cycle_time, :source_release_time, :source_available_at,
            1, 'OK', 0, 0, :manifest_hash, '{}', 'VERIFIED', 'degC',
            :local_day_start_utc, 144.0
        )
        """,
        {
            "city": scope.city_name,
            "target_date": scope.target_local_date.isoformat(),
            "members_json": json.dumps([18.0] * 51),
            "data_version": scope.data_version,
            "source_id": "ecmwf_open_data" if linked else None,
            "source_transport": "ensemble_snapshots_v2_db_reader" if linked else None,
            "source_run_id": "source-run-1" if linked else None,
            "release_calendar_key": "ecmwf_open_data:mx2t6_high:full" if linked else None,
            "source_cycle_time": "2026-05-03T00:00:00+00:00" if linked else None,
            "source_release_time": "2026-05-03T08:05:00+00:00" if linked else None,
            "source_available_at": "2026-05-03T08:10:00+00:00" if linked else None,
            "manifest_hash": "4" * 64,
            "local_day_start_utc": scope.target_window_start_utc.isoformat(),
        },
    )


def _insert_producer_readiness(
    conn: sqlite3.Connection,
    *,
    status: str = "LIVE_ELIGIBLE",
    reasons: list[str] | None = None,
    source_run_id: str = "source-run-1",
) -> None:
    scope = _scope()
    reasons = reasons or ["PRODUCER_COVERAGE_READY"]
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
            'producer-readiness-1', 'producer|london|high', 'city_metric', :city_id, :city, :city_timezone,
            :target_local_date, NULL, 'high', 'mx2t6_local_calendar_day_max',
            'high_temp', :data_version, 'ecmwf_open_data', 'mx2t6_high_full_horizon', :source_run_id,
            NULL, NULL, NULL, '[]',
            :strategy_key, :status, :reason_codes_json, :computed_at, :expires_at,
            '{}', '{}'
        )
        """,
        {
            "city_id": scope.city_id,
            "city": scope.city_name,
            "city_timezone": scope.city_timezone,
            "target_local_date": scope.target_local_date.isoformat(),
            "data_version": scope.data_version,
            "strategy_key": PRODUCER_READINESS_STRATEGY_KEY,
            "source_run_id": source_run_id,
            "status": status,
            "reason_codes_json": json.dumps(reasons),
            "computed_at": _utc(2026, 5, 3, 9).isoformat(),
            "expires_at": _utc(2026, 5, 3, 12).isoformat() if status == "LIVE_ELIGIBLE" else None,
        },
    )


def test_executable_reader_failure_blocks_shadow_boundary() -> None:
    conn = _conn()
    _insert_snapshot(conn, linked=False)
    _insert_producer_readiness(conn)

    decision = evaluate_entry_forecast_shadow(
        conn,
        scope=_scope(),
        config=entry_forecast_config(),
        now_utc=_utc(2026, 5, 3, 9),
    )

    assert decision.status == "BLOCKED"
    assert decision.reason_codes == ("NO_EXECUTABLE_FORECAST_ROWS_FOR_TARGET",)
    assert decision.snapshot_id is None


def test_missing_producer_readiness_blocks_shadow_boundary() -> None:
    conn = _conn()
    _insert_snapshot(conn, linked=True)

    decision = evaluate_entry_forecast_shadow(
        conn,
        scope=_scope(),
        config=entry_forecast_config(),
        now_utc=_utc(2026, 5, 3, 9),
    )

    assert decision.status == "BLOCKED"
    assert decision.reason_codes == ("PRODUCER_READINESS_MISSING",)
    assert decision.snapshot_id is not None


def test_source_run_mismatch_between_snapshot_and_producer_readiness_blocks_shadow_boundary() -> None:
    conn = _conn()
    _insert_snapshot(conn, linked=True)
    _insert_producer_readiness(conn, source_run_id="older-source-run")

    decision = evaluate_entry_forecast_shadow(
        conn,
        scope=_scope(),
        config=entry_forecast_config(),
        now_utc=_utc(2026, 5, 3, 9),
    )

    assert decision.status == "BLOCKED"
    assert decision.reason_codes == ("PRODUCER_SOURCE_RUN_MISMATCH",)
    assert decision.source_run_id == "source-run-1"


def test_calibration_transfer_defaults_entry_forecast_to_shadow_only() -> None:
    conn = _conn()
    _insert_snapshot(conn, linked=True)
    _insert_producer_readiness(conn)

    decision = evaluate_entry_forecast_shadow(
        conn,
        scope=_scope(),
        config=entry_forecast_config(),
        now_utc=_utc(2026, 5, 3, 9),
    )

    assert decision.status == "SHADOW_ONLY"
    assert decision.reason_codes == ("CALIBRATION_TRANSFER_SHADOW_ONLY",)
    assert decision.producer_readiness_id == "producer-readiness-1"
    assert decision.calibration_data_version == "tigge_mx2t6_local_calendar_day_max_v1"


def test_rollout_blocked_keeps_promoted_calibration_shadow_only() -> None:
    conn = _conn()
    _insert_snapshot(conn, linked=True)
    _insert_producer_readiness(conn)
    blocked_cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.BLOCKED)

    decision = evaluate_entry_forecast_shadow(
        conn,
        scope=_scope(),
        config=blocked_cfg,
        now_utc=_utc(2026, 5, 3, 9),
        live_calibration_promotion_approved=True,
    )

    assert decision.status == "SHADOW_ONLY"
    assert decision.reason_codes == ("ENTRY_FORECAST_ROLLOUT_BLOCKED",)
    assert decision.live_eligible is False


def test_expired_producer_readiness_blocks_shadow_boundary() -> None:
    conn = _conn()
    _insert_snapshot(conn, linked=True)
    _insert_producer_readiness(conn)

    decision = evaluate_entry_forecast_shadow(
        conn,
        scope=_scope(),
        config=entry_forecast_config(),
        now_utc=_utc(2026, 5, 3, 13),
        live_calibration_promotion_approved=True,
    )

    assert decision.status == "BLOCKED"
    assert decision.reason_codes == ("PRODUCER_READINESS_EXPIRED",)


def test_live_rollout_and_promoted_calibration_still_requires_rollout_gate() -> None:
    conn = _conn()
    _insert_snapshot(conn, linked=True)
    _insert_producer_readiness(conn)
    cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.LIVE)

    decision = evaluate_entry_forecast_shadow(
        conn,
        scope=_scope(),
        config=cfg,
        now_utc=_utc(2026, 5, 3, 9),
        live_calibration_promotion_approved=True,
    )

    assert decision.status == "SHADOW_ONLY"
    assert decision.reason_codes == ("ENTRY_FORECAST_ROLLOUT_GATE_REQUIRED",)


def test_live_rollout_with_passing_rollout_decision_returns_live_eligible() -> None:
    """Phase B6: when caller provides a rollout decision that permits
    live submission, the shadow function returns LIVE_ELIGIBLE so the
    ``live_eligible`` property is reachable. Without this branch the
    property is unreachable and the dataclass field is dead code.
    """

    from src.control.entry_forecast_rollout import EntryForecastRolloutDecision

    conn = _conn()
    _insert_snapshot(conn, linked=True)
    _insert_producer_readiness(conn)
    cfg = replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.LIVE)

    rollout_decision = EntryForecastRolloutDecision(
        status="LIVE_ELIGIBLE",
        reason_codes=("ENTRY_FORECAST_LIVE_APPROVED",),
    )

    decision = evaluate_entry_forecast_shadow(
        conn,
        scope=_scope(),
        config=cfg,
        now_utc=_utc(2026, 5, 3, 9),
        live_calibration_promotion_approved=True,
        rollout_decision=rollout_decision,
    )

    assert decision.status == "LIVE_ELIGIBLE"
    assert decision.live_eligible is True
    assert decision.reason_codes == ("ENTRY_FORECAST_LIVE_APPROVED",)
