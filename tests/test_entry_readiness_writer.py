# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-02_full_launch_audit/REMEDIATION_PLAN_2026-05-03.md Phase B4 entry-readiness writer relationship contract.
"""Relationship tests for the entry-readiness writer.

These tests verify the cross-module write contract: the writer
refuses to land ``LIVE_ELIGIBLE`` unless rollout, calibration, and
promotion-evidence gates are all aligned.  The writer never silently
upgrades; it always writes a deterministic row whose status matches
the joined verdict.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.config import (
    EntryForecastCalibrationPolicyId,
    EntryForecastConfig,
    EntryForecastRolloutMode,
    EntryForecastSourceTransport,
    entry_forecast_config,
)
from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
)
from src.control.entry_forecast_rollout import (
    EntryForecastPromotionEvidence,
    EntryForecastRolloutDecision,
)
from src.data.calibration_transfer_policy import CalibrationTransferDecision
from src.data.entry_readiness_writer import (
    ENTRY_FORECAST_STRATEGY_KEY,
    write_entry_readiness,
)
from src.data.forecast_target_contract import ForecastTargetScope
from src.data.live_entry_status import LiveEntryForecastStatus
from src.state.db import init_schema, init_schema_forecasts
from src.state.readiness_repo import write_readiness_state
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


def _scope() -> ForecastTargetScope:
    return ForecastTargetScope(
        city_id="LONDON",
        city_name="London",
        city_timezone="Europe/London",
        target_local_date=date(2026, 5, 8),
        temperature_metric="high",
        source_cycle_time=_utc(2026, 5, 3, 0),
        data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
        target_window_start_utc=_utc(2026, 5, 7, 23),
        target_window_end_utc=_utc(2026, 5, 8, 23),
        required_step_hours=(120, 126, 132),
        market_refs=("condition-123",),
    )


def _ready_status() -> LiveEntryForecastStatus:
    return LiveEntryForecastStatus(
        status="LIVE_ELIGIBLE",
        blockers=(),
        executable_row_count=4,
        producer_readiness_count=4,
        producer_live_eligible_count=4,
    )


def _evidence(**overrides) -> EntryForecastPromotionEvidence:
    base: dict = {
        "operator_approval_id": "operator-1",
        "g1_evidence_id": "g1-2026-05-03",
        "status_snapshot": _ready_status(),
        "calibration_promotion_approved": True,
        "canary_success_evidence_id": "canary-1",
    }
    base.update(overrides)
    return EntryForecastPromotionEvidence(**base)


def _live_rollout_decision() -> EntryForecastRolloutDecision:
    return EntryForecastRolloutDecision(
        status="LIVE_ELIGIBLE",
        reason_codes=("ENTRY_FORECAST_LIVE_APPROVED",),
    )


def _blocked_rollout_decision() -> EntryForecastRolloutDecision:
    return EntryForecastRolloutDecision(
        status="BLOCKED",
        reason_codes=("ENTRY_FORECAST_ROLLOUT_BLOCKED",),
    )


def _live_calibration_decision() -> CalibrationTransferDecision:
    return CalibrationTransferDecision(
        status="LIVE_ELIGIBLE",
        reason_codes=("CALIBRATION_TRANSFER_APPROVED",),
        policy_id=EntryForecastCalibrationPolicyId.ECMWF_OPEN_DATA_USES_TIGGE_LOCALDAY_CAL_V1.value,
        forecast_data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
        calibration_data_version="tigge_mx2t6_local_calendar_day_max_v1",
        live_promotion_approved=True,
    )


def _shadow_calibration_decision() -> CalibrationTransferDecision:
    return CalibrationTransferDecision(
        status="SHADOW_ONLY",
        reason_codes=("CALIBRATION_TRANSFER_SHADOW_ONLY",),
        policy_id=EntryForecastCalibrationPolicyId.ECMWF_OPEN_DATA_USES_TIGGE_LOCALDAY_CAL_V1.value,
        forecast_data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
        calibration_data_version="tigge_mx2t6_local_calendar_day_max_v1",
        live_promotion_approved=False,
    )


def _live_cfg() -> EntryForecastConfig:
    return replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.LIVE)


def _seed_producer_readiness(conn: sqlite3.Connection, *, scope: ForecastTargetScope | None = None) -> str:
    scope = scope or _scope()
    readiness_id = (
        f"producer-{scope.city_id}-{scope.target_local_date.isoformat()}-{scope.temperature_metric}"
    )
    if scope.temperature_metric == "high":
        physical_quantity = "mx2t3_local_calendar_day_max"
        observation_field = "high_temp"
        track = "mx2t6_high_full_horizon"
    else:
        physical_quantity = "mn2t3_local_calendar_day_min"
        observation_field = "low_temp"
        track = "mn2t6_low_full_horizon"
    write_readiness_state(
        conn,
        readiness_id=readiness_id,
        scope_type="city_metric",
        status="LIVE_ELIGIBLE",
        computed_at=_utc(2026, 5, 3, 10),
        expires_at=_utc(2026, 5, 3, 18),
        city_id=scope.city_id,
        city=scope.city_name,
        city_timezone=scope.city_timezone,
        target_local_date=scope.target_local_date,
        temperature_metric=scope.temperature_metric,
        physical_quantity=physical_quantity,
        observation_field=observation_field,
        data_version=scope.data_version,
        source_id="ecmwf_open_data",
        track=track,
        source_run_id="source-run-1",
        strategy_key="producer_readiness",
        reason_codes_json=["PRODUCER_COVERAGE_READY"],
        dependency_json={"coverage_id": "coverage-1"},
    )
    return readiness_id


def _all_gates_aligned_args(conn: sqlite3.Connection) -> dict:
    scope = _scope()
    return dict(
        scope=scope,
        rollout_decision=_live_rollout_decision(),
        calibration_decision=_live_calibration_decision(),
        promotion_evidence=_evidence(),
        config=_live_cfg(),
        market_family="POLY_TEMP_LONDON",
        condition_id="condition-123",
        producer_readiness_id=_seed_producer_readiness(conn, scope=scope),
        computed_at=_utc(2026, 5, 3, 12),
    )


def test_all_gates_aligned_writes_live_eligible_with_expiry() -> None:
    conn = _conn()
    args = _all_gates_aligned_args(conn)

    result = write_entry_readiness(conn, **args)

    assert result.status == "LIVE_ELIGIBLE"
    assert result.reason_codes == (
        "ENTRY_FORECAST_LIVE_APPROVED",
        "CALIBRATION_TRANSFER_APPROVED",
    )
    assert result.expires_at == _utc(2026, 5, 3, 15)

    row = conn.execute(
        "SELECT * FROM readiness_state WHERE strategy_key = ?",
        (ENTRY_FORECAST_STRATEGY_KEY,),
    ).fetchone()
    assert row is not None
    assert row["status"] == "LIVE_ELIGIBLE"
    assert row["expires_at"] == _utc(2026, 5, 3, 15).isoformat()
    assert row["target_local_date"] == "2026-05-08"
    assert row["track"] == "mx2t6_high_full_horizon"
    assert row["physical_quantity"] == "mx2t3_local_calendar_day_max"
    assert row["source_run_id"] == "source-run-1"

    provenance = json.loads(row["provenance_json"])
    assert provenance["rollout_mode"] == "live"
    assert provenance["calibration_live_promotion_approved"] is True
    assert provenance["promotion_evidence"]["operator_approval_id"] == "operator-1"


def test_blocked_rollout_writes_blocked_row_and_no_expiry() -> None:
    conn = _conn()
    args = _all_gates_aligned_args(conn)
    args["rollout_decision"] = _blocked_rollout_decision()

    result = write_entry_readiness(conn, **args)

    assert result.status == "BLOCKED"
    assert "ENTRY_FORECAST_ROLLOUT_BLOCKED" in result.reason_codes
    assert result.expires_at is None

    row = conn.execute(
        "SELECT * FROM readiness_state WHERE strategy_key = ?",
        (ENTRY_FORECAST_STRATEGY_KEY,),
    ).fetchone()
    assert row["status"] == "BLOCKED"
    assert row["expires_at"] is None


def test_shadow_calibration_blocks_live_even_with_live_rollout() -> None:
    conn = _conn()
    args = _all_gates_aligned_args(conn)
    args["calibration_decision"] = _shadow_calibration_decision()

    result = write_entry_readiness(conn, **args)

    assert result.status == "BLOCKED"
    assert "ENTRY_READINESS_LIVE_REQUIRES_CALIBRATION_APPROVAL" in result.reason_codes
    assert "CALIBRATION_TRANSFER_SHADOW_ONLY" in result.reason_codes


def test_promotion_evidence_missing_blocks_live() -> None:
    conn = _conn()
    args = _all_gates_aligned_args(conn)
    args["promotion_evidence"] = None

    result = write_entry_readiness(conn, **args)

    assert result.status == "BLOCKED"
    assert "ENTRY_READINESS_LIVE_REQUIRES_PROMOTION_EVIDENCE" in result.reason_codes


def test_promotion_evidence_calibration_unapproved_blocks_live() -> None:
    conn = _conn()
    args = _all_gates_aligned_args(conn)
    args["promotion_evidence"] = _evidence(calibration_promotion_approved=False)

    result = write_entry_readiness(conn, **args)

    assert result.status == "BLOCKED"
    assert "ENTRY_READINESS_LIVE_REQUIRES_PROMOTION_EVIDENCE" in result.reason_codes


def test_canary_rollout_with_live_calibration_writes_shadow_only() -> None:
    conn = _conn()
    args = _all_gates_aligned_args(conn)
    args["rollout_decision"] = EntryForecastRolloutDecision(
        status="CANARY_ELIGIBLE",
        reason_codes=("ENTRY_FORECAST_CANARY_APPROVED",),
    )

    result = write_entry_readiness(conn, **args)

    assert result.status == "SHADOW_ONLY"
    assert result.expires_at is None
    assert "ENTRY_FORECAST_CANARY_APPROVED" in result.reason_codes


def test_naive_computed_at_rejected() -> None:
    conn = _conn()
    args = _all_gates_aligned_args(conn)
    args["computed_at"] = datetime(2026, 5, 3, 12)

    with pytest.raises(ValueError, match="timezone-aware"):
        write_entry_readiness(conn, **args)


def test_low_track_uses_low_metric_identity() -> None:
    conn = _conn()
    args = _all_gates_aligned_args(conn)
    low_scope = replace(
        _scope(),
        temperature_metric="low",
        data_version=ECMWF_OPENDATA_LOW_DATA_VERSION,
    )
    args["scope"] = low_scope
    args["producer_readiness_id"] = _seed_producer_readiness(conn, scope=low_scope)

    write_entry_readiness(conn, **args)

    row = conn.execute(
        "SELECT track, physical_quantity, observation_field FROM readiness_state WHERE strategy_key = ?",
        (ENTRY_FORECAST_STRATEGY_KEY,),
    ).fetchone()
    assert row["track"] == "mn2t6_low_full_horizon"
    assert row["physical_quantity"] == "mn2t3_local_calendar_day_min"
    assert row["observation_field"] == "low_temp"


def test_live_eligible_requires_matching_producer_readiness() -> None:
    conn = _conn()
    args = _all_gates_aligned_args(conn)
    args["producer_readiness_id"] = "missing-producer"
    conn.execute("DELETE FROM readiness_state WHERE strategy_key = 'producer_readiness'")

    result = write_entry_readiness(conn, **args)

    assert result.status == "BLOCKED"
    assert "ENTRY_READINESS_PRODUCER_MISSING" in result.reason_codes


def test_writer_resolves_producer_from_attached_forecasts_db(tmp_path: Path) -> None:
    forecasts_db = tmp_path / "forecasts.db"
    forecasts_conn = sqlite3.connect(str(forecasts_db))
    forecasts_conn.row_factory = sqlite3.Row
    init_schema_forecasts(forecasts_conn)
    producer_id = _seed_producer_readiness(forecasts_conn)
    forecasts_conn.commit()
    forecasts_conn.close()

    conn = _conn()
    conn.execute("ATTACH DATABASE ? AS forecasts", (str(forecasts_db),))
    args = _all_gates_aligned_args(conn)
    conn.execute("DELETE FROM readiness_state WHERE strategy_key = 'producer_readiness'")
    args["producer_readiness_id"] = producer_id

    result = write_entry_readiness(conn, **args)

    assert result.status == "LIVE_ELIGIBLE"
    row = conn.execute(
        "SELECT physical_quantity, source_run_id, dependency_json FROM readiness_state WHERE strategy_key = ?",
        (ENTRY_FORECAST_STRATEGY_KEY,),
    ).fetchone()
    assert row["physical_quantity"] == "mx2t3_local_calendar_day_max"
    assert row["source_run_id"] == "source-run-1"
    assert json.loads(row["dependency_json"]) == {"producer_readiness_id": producer_id}


def test_writer_does_not_fallback_to_main_producer_when_forecasts_attached(tmp_path: Path) -> None:
    forecasts_db = tmp_path / "forecasts_without_readiness.db"
    forecasts_db.touch()
    conn = _conn()
    args = _all_gates_aligned_args(conn)
    conn.execute("ATTACH DATABASE ? AS forecasts", (str(forecasts_db),))

    result = write_entry_readiness(conn, **args)

    assert result.status == "BLOCKED"
    assert "ENTRY_READINESS_PRODUCER_MISSING" in result.reason_codes


def test_dependency_links_back_to_producer_readiness() -> None:
    conn = _conn()
    args = _all_gates_aligned_args(conn)

    write_entry_readiness(conn, **args)

    row = conn.execute(
        "SELECT dependency_json FROM readiness_state WHERE strategy_key = ?",
        (ENTRY_FORECAST_STRATEGY_KEY,),
    ).fetchone()
    dep = json.loads(row["dependency_json"])
    assert dep == {"producer_readiness_id": args["producer_readiness_id"]}
