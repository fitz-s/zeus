# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-02_live_entry_data_contract/PLAN_v4.md LiveEntryForecastTargetContract.v1 Phase 1.
"""V4 Open Data future target-local-date relationship contracts.

These tests deliberately encode the category that caused live to stay at zero
orders despite green source health: source-run freshness is not future market
coverage. Phase 1 writes the contract before production implementation.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from importlib import import_module

UTC = timezone.utc


def _contract_module():
    return import_module("src.data.forecast_target_contract")


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def test_source_cycle_date_is_not_target_local_date() -> None:
    contract = _contract_module()

    scope = contract.build_forecast_target_scope(
        city_id="new_york",
        city_name="New York",
        city_timezone="America/New_York",
        target_local_date=date(2026, 5, 8),
        temperature_metric="high",
        source_cycle_time=_utc(2026, 5, 3),
        data_version="ecmwf_opendata_mx2t6_local_calendar_day_max_v1",
        market_refs=("condition-nyc-2026-05-08-high",),
    )

    assert scope.source_cycle_time.date() == date(2026, 5, 3)
    assert scope.target_local_date == date(2026, 5, 8)
    assert scope.target_window_start_utc == _utc(2026, 5, 8, 4)
    assert scope.target_window_end_utc == _utc(2026, 5, 9, 4)


def test_today_target_row_cannot_authorize_future_market() -> None:
    contract = _contract_module()

    decision = contract.evaluate_producer_coverage(
        city_id="new_york",
        city_timezone="America/New_York",
        target_local_date=date(2026, 5, 8),
        temperature_metric="high",
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_v2_db_reader",
        source_run_status="SUCCESS",
        source_run_completeness="COMPLETE",
        snapshot_target_date=date(2026, 5, 3),
        snapshot_metric="high",
        expected_steps=(126, 132, 138, 144),
        observed_steps=(126, 132, 138, 144),
        expected_members=51,
        observed_members=51,
        has_source_linkage=True,
    )

    assert decision.status == "BLOCKED"
    assert "SNAPSHOT_TARGET_DATE_MISMATCH" in decision.reason_codes


def test_city_local_day_window_computes_required_steps() -> None:
    contract = _contract_module()

    window = contract.compute_target_local_day_window_utc(
        city_timezone="America/New_York",
        target_local_date=date(2026, 5, 8),
    )
    steps = contract.required_period_end_steps(
        source_cycle_time=_utc(2026, 5, 3),
        target_window_start_utc=window.start_utc,
        target_window_end_utc=window.end_utc,
        period_hours=6,
    )

    assert window.start_utc == _utc(2026, 5, 8, 4)
    assert window.end_utc == _utc(2026, 5, 9, 4)
    assert steps == (126, 132, 138, 144, 150)


def test_dst_target_day_required_steps_are_not_24h_assumption() -> None:
    contract = _contract_module()

    window = contract.compute_target_local_day_window_utc(
        city_timezone="Europe/London",
        target_local_date=date(2026, 3, 29),
    )
    duration_hours = int((window.end_utc - window.start_utc).total_seconds() // 3600)

    assert duration_hours == 23


def test_dplus10_dst_transition_target_day_does_not_exceed_profile_silently() -> None:
    contract = _contract_module()

    window = contract.compute_target_local_day_window_utc(
        city_timezone="Europe/London",
        target_local_date=date(2026, 3, 29),
    )
    steps = contract.required_period_end_steps(
        source_cycle_time=_utc(2026, 3, 19),
        target_window_start_utc=window.start_utc,
        target_window_end_utc=window.end_utc,
        period_hours=6,
    )
    decision = contract.evaluate_horizon_coverage(
        required_steps=steps,
        live_max_step_hours=240,
    )

    duration_hours = int((window.end_utc - window.start_utc).total_seconds() // 3600)
    assert duration_hours == 23
    assert max(steps) > 240
    assert decision.status == "BLOCKED"
    assert "SOURCE_RUN_HORIZON_OUT_OF_RANGE" in decision.reason_codes


def test_dplus10_required_steps_utc_negative_city_do_not_exceed_profile_silently() -> None:
    contract = _contract_module()

    window = contract.compute_target_local_day_window_utc(
        city_timezone="America/New_York",
        target_local_date=date(2026, 5, 13),
    )
    steps = contract.required_period_end_steps(
        source_cycle_time=_utc(2026, 5, 3),
        target_window_start_utc=window.start_utc,
        target_window_end_utc=window.end_utc,
        period_hours=6,
    )
    decision = contract.evaluate_horizon_coverage(
        required_steps=steps,
        live_max_step_hours=240,
    )

    assert max(steps) > 240
    assert decision.status == "BLOCKED"
    assert "SOURCE_RUN_HORIZON_OUT_OF_RANGE" in decision.reason_codes


def test_dplus10_required_steps_utc_positive_city_do_not_exceed_profile_silently() -> None:
    contract = _contract_module()

    window = contract.compute_target_local_day_window_utc(
        city_timezone="Asia/Tokyo",
        target_local_date=date(2026, 5, 13),
    )
    steps = contract.required_period_end_steps(
        source_cycle_time=_utc(2026, 5, 3),
        target_window_start_utc=window.start_utc,
        target_window_end_utc=window.end_utc,
        period_hours=6,
    )
    decision = contract.evaluate_horizon_coverage(
        required_steps=steps,
        live_max_step_hours=240,
    )

    assert max(steps) > 240
    assert decision.status == "BLOCKED"
    assert "SOURCE_RUN_HORIZON_OUT_OF_RANGE" in decision.reason_codes


def test_06z_short_horizon_blocks_dplus10() -> None:
    contract = _contract_module()

    window = contract.compute_target_local_day_window_utc(
        city_timezone="America/New_York",
        target_local_date=date(2026, 5, 13),
    )
    steps = contract.required_period_end_steps(
        source_cycle_time=_utc(2026, 5, 3, 6),
        target_window_start_utc=window.start_utc,
        target_window_end_utc=window.end_utc,
        period_hours=6,
    )
    decision = contract.evaluate_horizon_coverage(
        required_steps=steps,
        live_max_step_hours=144,
    )

    assert decision.status == "BLOCKED"
    assert "SOURCE_RUN_HORIZON_OUT_OF_RANGE" in decision.reason_codes


def test_missing_future_target_scope_blocks_entry_readiness() -> None:
    contract = _contract_module()

    decision = contract.evaluate_producer_coverage(
        city_id="new_york",
        city_timezone="America/New_York",
        target_local_date=date(2026, 5, 8),
        temperature_metric="high",
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_v2_db_reader",
        source_run_status="SUCCESS",
        source_run_completeness="COMPLETE",
        snapshot_target_date=None,
        snapshot_metric=None,
        expected_steps=(126, 132, 138, 144),
        observed_steps=(),
        expected_members=51,
        observed_members=0,
        has_source_linkage=False,
    )

    assert decision.status == "BLOCKED"
    assert "FUTURE_TARGET_DATE_NOT_COVERED" in decision.reason_codes
    assert "MISSING_REQUIRED_STEPS" in decision.reason_codes
