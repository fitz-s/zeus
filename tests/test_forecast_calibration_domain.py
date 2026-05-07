# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: may4math ForecastCalibrationDomain Phase 2.5 base contract
# Lifecycle: created=2026-05-04; last_reviewed=2026-05-04; last_reused=2026-05-04
# Purpose: Protect forecast calibration domain identity before live calibration wiring consumes it.
# Reuse: Confirm source/cycle/metric/domain fields still match may4math Phase 2.5 before extending integration.
"""Forecast calibration domain identity tests."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.calibration.forecast_calibration_domain import (
    CalibrationAuthorityResult,
    ContractOutcomeDomain,
    ContractOutcomeDomainMismatch,
    ForecastCalibrationDomain,
    ForecastCalibrationDomainMismatch,
    ForecastToBinEvidence,
)


def _domain(**overrides: object) -> ForecastCalibrationDomain:
    values = {
        "source_id": "ecmwf_open_data",
        "data_version": "ecmwf_opendata_mx2t6_local_calendar_day_max_v1",
        "source_cycle_hour_utc": 0,
        "horizon_profile": "full",
        "metric": "high",
        "cluster": "Tokyo",
        "season": "MAM",
        "input_space": "width_normalized_density",
        "city_local_cycle_hour": 9,
    }
    values.update(overrides)
    return ForecastCalibrationDomain(**values)  # type: ignore[arg-type]


def test_domain_key_includes_source_cycle_and_local_hour() -> None:
    domain = _domain(source_cycle_hour_utc=12, city_local_cycle_hour=21)

    assert "cycle12z" in domain.key
    assert "local21" in domain.key
    assert "ecmwf_open_data" in domain.key


def test_00z_and_12z_domains_do_not_match() -> None:
    forecast = _domain(source_cycle_hour_utc=12)
    calibrator = _domain(source_cycle_hour_utc=0)

    assert forecast.mismatch_fields(calibrator) == ("source_cycle_hour_utc",)
    with pytest.raises(ForecastCalibrationDomainMismatch, match="CALIBRATION_DOMAIN_MISMATCH"):
        forecast.assert_matches(calibrator)


def test_metric_mismatch_is_explicit() -> None:
    high = _domain(metric="high")
    low = _domain(metric="low")

    assert high.mismatch_fields(low) == ("metric",)


def test_invalid_cycle_hour_rejected() -> None:
    with pytest.raises(ValueError, match="source_cycle_hour_utc"):
        _domain(source_cycle_hour_utc=24)


def _contract_domain(**overrides: object) -> ContractOutcomeDomain:
    values = {
        "city": "Kuala Lumpur",
        "target_local_date": date(2026, 6, 10),
        "city_timezone": "Asia/Kuala_Lumpur",
        "temperature_metric": "low",
        "observation_field": "low_temp",
        "settlement_source_type": "WU",
        "settlement_station_id": "WMKK",
        "settlement_unit": "C",
        "settlement_rounding_policy": "wmo_half_up",
        "bin_grid_id": "kuala_lumpur_celsius_low_v1",
        "bin_schema_version": "v1",
    }
    values.update(overrides)
    return ContractOutcomeDomain(**values)  # type: ignore[arg-type]


def test_contract_outcome_domain_rejects_high_low_observation_flip() -> None:
    with pytest.raises(ContractOutcomeDomainMismatch, match="high must bind high_temp"):
        _contract_domain(temperature_metric="high", observation_field="low_temp")


def test_forecast_to_bin_evidence_blocks_ambiguous_low_window_from_training() -> None:
    domain = _contract_domain()
    t0 = datetime(2026, 6, 9, 14, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 6, 9, 20, 0, tzinfo=timezone.utc)

    with pytest.raises(ContractOutcomeDomainMismatch, match="AMBIGUOUS_CROSSES"):
        ForecastToBinEvidence(
            contract_domain=domain,
            forecast_source_id="tigge_mars",
            data_version="tigge_mn2t6_local_calendar_day_min_v1",
            issue_time_utc=datetime(2026, 6, 9, 0, 0, tzinfo=timezone.utc),
            cycle_hour_utc=0,
            horizon_profile="full",
            physical_quantity="mn2t6",
            aggregation_window_hours=6,
            window_start_utc=t0,
            window_end_utc=t1,
            window_start_local=t0,
            window_end_local=t1,
            local_day_overlap_hours=4.0,
            attribution_status="AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY",
            contributes_to_target_extrema=False,
            training_allowed=True,
            live_allowed=False,
            block_reasons=("ambiguous_crosses_local_day_boundary",),
        )


def test_fully_attributable_low_window_can_be_marked_training_allowed() -> None:
    domain = _contract_domain()
    t0 = datetime(2026, 6, 9, 18, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc)

    evidence = ForecastToBinEvidence(
        contract_domain=domain,
        forecast_source_id="tigge_mars",
        data_version="tigge_mn2t6_local_calendar_day_min_v1",
        issue_time_utc=datetime(2026, 6, 9, 0, 0, tzinfo=timezone.utc),
        cycle_hour_utc=0,
        horizon_profile="full",
        physical_quantity="mn2t6",
        aggregation_window_hours=6,
        window_start_utc=t0,
        window_end_utc=t1,
        window_start_local=t0,
        window_end_local=t1,
        local_day_overlap_hours=6.0,
        attribution_status="FULLY_INSIDE_TARGET_LOCAL_DAY",
        contributes_to_target_extrema=True,
        training_allowed=True,
        live_allowed=False,
        block_reasons=("shadow_only_until_trade_drift_gate_passes",),
    )

    assert evidence.contributes_to_target_extrema is True
    assert evidence.training_allowed is True
    assert evidence.live_allowed is False


def test_payload_factory_missing_explicit_window_is_shadow_blocked() -> None:
    evidence = ForecastToBinEvidence.from_snapshot_payload(
        _contract_domain(),
        {
            "forecast_source_id": "tigge_mars",
            "data_version": "tigge_mn2t6_local_calendar_day_min_v1",
            "issue_time_utc": "2026-06-09T00:00:00+00:00",
            "horizon_profile": "full",
            "physical_quantity": "mn2t6",
            "boundary_policy": {"boundary_ambiguous": False},
            "causality": {"status": "OK"},
            # local_day_start/end are intentionally insufficient: they are
            # target-day envelope fields, not the 6h forecast product window.
            "local_day_start_utc": "2026-06-09T16:00:00+00:00",
            "local_day_end_utc": "2026-06-10T16:00:00+00:00",
        },
    )

    assert evidence.attribution_status == "UNKNOWN"
    assert evidence.training_allowed is False
    assert "missing_explicit_forecast_window_evidence" in evidence.block_reasons


def test_payload_factory_accepts_fully_inside_explicit_window_for_training_shadow() -> None:
    evidence = ForecastToBinEvidence.from_snapshot_payload(
        _contract_domain(),
        {
            "forecast_source_id": "tigge_mars",
            "data_version": "tigge_mn2t6_local_calendar_day_min_v1",
            "issue_time_utc": "2026-06-09T00:00:00+00:00",
            "cycle_hour_utc": 0,
            "horizon_profile": "full",
            "physical_quantity": "mn2t6",
            "aggregation_window_hours": 6,
            "forecast_window_start_utc": "2026-06-09T18:00:00+00:00",
            "forecast_window_end_utc": "2026-06-10T00:00:00+00:00",
            "forecast_window_start_local": "2026-06-10T02:00:00+08:00",
            "forecast_window_end_local": "2026-06-10T08:00:00+08:00",
            "boundary_policy": {"boundary_ambiguous": False},
            "causality": {"status": "OK"},
        },
    )

    assert evidence.attribution_status == "FULLY_INSIDE_TARGET_LOCAL_DAY"
    assert evidence.contributes_to_target_extrema is True
    assert evidence.training_allowed is True
    assert evidence.live_allowed is False


def test_payload_factory_nondict_boundary_policy_does_not_raise() -> None:
    evidence = ForecastToBinEvidence.from_snapshot_payload(
        _contract_domain(),
        {
            "forecast_source_id": "tigge_mars",
            "data_version": "tigge_mn2t6_local_calendar_day_min_v1",
            "issue_time_utc": "2026-06-09T00:00:00+00:00",
            "cycle_hour_utc": 0,
            "horizon_profile": "full",
            "physical_quantity": "mn2t6",
            "aggregation_window_hours": 6,
            "forecast_window_start_utc": "2026-06-09T18:00:00+00:00",
            "forecast_window_end_utc": "2026-06-10T00:00:00+00:00",
            "forecast_window_start_local": "2026-06-10T02:00:00+08:00",
            "forecast_window_end_local": "2026-06-10T08:00:00+08:00",
            "boundary_policy": "malformed",
            "causality": {"status": "OK"},
        },
    )

    assert evidence.attribution_status == "FULLY_INSIDE_TARGET_LOCAL_DAY"
    assert evidence.training_allowed is True
    assert evidence.block_reasons == ()


def test_payload_factory_blocks_issue_after_relevant_window() -> None:
    evidence = ForecastToBinEvidence.from_snapshot_payload(
        _contract_domain(),
        {
            "forecast_source_id": "tigge_mars",
            "data_version": "tigge_mn2t6_local_calendar_day_min_v1",
            "issue_time_utc": "2026-06-09T19:00:00+00:00",
            "cycle_hour_utc": 18,
            "horizon_profile": "full",
            "physical_quantity": "mn2t6",
            "forecast_window_start_utc": "2026-06-09T18:00:00+00:00",
            "forecast_window_end_utc": "2026-06-10T00:00:00+00:00",
            "forecast_window_start_local": "2026-06-10T02:00:00+08:00",
            "forecast_window_end_local": "2026-06-10T08:00:00+08:00",
            "boundary_policy": {"boundary_ambiguous": False},
            "causality": {"status": "OK"},
        },
    )

    assert evidence.attribution_status == "ISSUED_AFTER_RELEVANT_WINDOW"
    assert evidence.training_allowed is False
    assert "issued_after_relevant_window" in evidence.block_reasons


def test_payload_factory_counts_dst_overlap_in_actual_hours() -> None:
    evidence = ForecastToBinEvidence.from_snapshot_payload(
        _contract_domain(
            city="NYC",
            target_local_date=date(2026, 3, 8),
            city_timezone="America/New_York",
            bin_grid_id="nyc_low_fahrenheit_v1",
            settlement_station_id="KNYC",
            settlement_unit="F",
        ),
        {
            "forecast_source_id": "tigge_mars",
            "data_version": "tigge_mn2t6_local_calendar_day_min_v1",
            "issue_time_utc": "2026-03-08T00:00:00+00:00",
            "cycle_hour_utc": 0,
            "horizon_profile": "full",
            "physical_quantity": "mn2t6",
            "forecast_window_start_utc": "2026-03-08T06:00:00+00:00",
            "forecast_window_end_utc": "2026-03-08T08:00:00+00:00",
            "forecast_window_start_local": "2026-03-08T01:00:00-05:00",
            "forecast_window_end_local": "2026-03-08T04:00:00-04:00",
            "boundary_policy": {"boundary_ambiguous": False},
            "causality": {"status": "OK"},
        },
    )

    assert evidence.attribution_status == "FULLY_INSIDE_TARGET_LOCAL_DAY"
    assert evidence.local_day_overlap_hours == 2.0
    assert evidence.training_allowed is True


def test_payload_factory_boundary_ambiguous_overrides_window_geometry() -> None:
    evidence = ForecastToBinEvidence.from_snapshot_payload(
        _contract_domain(),
        {
            "forecast_source_id": "tigge_mars",
            "data_version": "tigge_mn2t6_local_calendar_day_min_v1",
            "issue_time_utc": "2026-06-09T00:00:00+00:00",
            "horizon_profile": "full",
            "physical_quantity": "mn2t6",
            "forecast_window_start_utc": "2026-06-09T18:00:00+00:00",
            "forecast_window_end_utc": "2026-06-10T00:00:00+00:00",
            "forecast_window_start_local": "2026-06-10T02:00:00+08:00",
            "forecast_window_end_local": "2026-06-10T08:00:00+08:00",
            "boundary_policy": {"boundary_ambiguous": True},
            "causality": {"status": "OK"},
        },
    )

    assert evidence.attribution_status == "AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY"
    assert evidence.training_allowed is False
    assert "boundary_ambiguous" in evidence.block_reasons


def test_payload_factory_marks_previous_day_window_as_reassignment_candidate() -> None:
    evidence = ForecastToBinEvidence.from_snapshot_payload(
        _contract_domain(),
        {
            "forecast_source_id": "tigge_mars",
            "data_version": "tigge_mn2t6_local_calendar_day_min_v1",
            "issue_time_utc": "2026-06-09T00:00:00+00:00",
            "horizon_profile": "full",
            "physical_quantity": "mn2t6",
            "forecast_window_start_utc": "2026-06-09T10:00:00+00:00",
            "forecast_window_end_utc": "2026-06-09T16:00:00+00:00",
            "forecast_window_start_local": "2026-06-09T18:00:00+08:00",
            "forecast_window_end_local": "2026-06-10T00:00:00+08:00",
            "boundary_policy": {"boundary_ambiguous": False},
            "causality": {"status": "OK"},
        },
    )

    assert evidence.attribution_status == "DETERMINISTICALLY_PREVIOUS_LOCAL_DAY"
    assert evidence.training_allowed is False
    assert evidence.is_deterministic_reassignment_candidate is True
    assert evidence.reassignment_candidate_local_date == date(2026, 6, 9)
    assert "deterministic_reassignment_requires_revision" in evidence.block_reasons


def test_payload_factory_cross_midnight_window_is_not_reassignment_candidate() -> None:
    evidence = ForecastToBinEvidence.from_snapshot_payload(
        _contract_domain(),
        {
            "forecast_source_id": "tigge_mars",
            "data_version": "tigge_mn2t6_local_calendar_day_min_v1",
            "issue_time_utc": "2026-06-09T00:00:00+00:00",
            "horizon_profile": "full",
            "physical_quantity": "mn2t6",
            "forecast_window_start_utc": "2026-06-09T14:00:00+00:00",
            "forecast_window_end_utc": "2026-06-09T20:00:00+00:00",
            "forecast_window_start_local": "2026-06-09T22:00:00+08:00",
            "forecast_window_end_local": "2026-06-10T04:00:00+08:00",
            "boundary_policy": {"boundary_ambiguous": False},
            "causality": {"status": "OK"},
        },
    )

    assert evidence.attribution_status == "AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY"
    assert evidence.training_allowed is False
    assert evidence.deterministic_local_date is None
    assert evidence.is_deterministic_reassignment_candidate is False
    assert "ambiguous_crosses_local_day_boundary" in evidence.block_reasons


def test_calibration_authority_result_blocks_incompatible_fallback_live_use() -> None:
    requested = _domain(metric="low", cluster="Kuala Lumpur")
    served = _domain(metric="low", cluster="Jakarta")

    with pytest.raises(ContractOutcomeDomainMismatch, match="compatibility gates"):
        CalibrationAuthorityResult(
            contract_domain=_contract_domain(),
            requested_calibration_domain=requested,
            served_calibration_domain=served,
            route="COMPATIBLE_FALLBACK",
            calibrator_model_key="low:Jakarta:JJA:v1:00:tigge_mars:full:width_normalized_density",
            n_eff=80,
            n_samples=80,
            bin_schema_compatible=True,
            settlement_semantics_compatible=True,
            source_cycle_horizon_compatible=True,
            local_day_construction_compatible=True,
            climate_compatible=False,
            live_eligible=True,
            block_reasons=(),
        )
