# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect replacement forecast products from baseline calibration authority reuse.
# Reuse: Run before wiring replacement posterior evidence into calibration, q builders, or training rows.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t integration.
"""Replacement forecast calibration quarantine tests."""

from __future__ import annotations

import pytest

from src.data.replacement_forecast_calibration_quarantine import (
    AIFS_PRODUCT_ID,
    AIFS_SOURCE_ID,
    REPLACEMENT_PRODUCT_ID,
    REPLACEMENT_SOURCE_ID,
    ReplacementForecastCalibrationRequest,
    evaluate_replacement_forecast_calibration_quarantine,
)


def _request(**overrides) -> ReplacementForecastCalibrationRequest:
    params = {
        "target_source_id": REPLACEMENT_SOURCE_ID,
        "target_product_id": REPLACEMENT_PRODUCT_ID,
        "target_data_version": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1",
        "calibration_source_id": REPLACEMENT_SOURCE_ID,
        "calibration_product_id": REPLACEMENT_PRODUCT_ID,
        "calibration_data_version": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1",
        "calibration_method": "soft_anchor_product_specific",
        "training_allowed": False,
    }
    params.update(overrides)
    return ReplacementForecastCalibrationRequest(**params)


def test_product_specific_calibration_is_allowed_only_when_isolated_from_baseline() -> None:
    decision = evaluate_replacement_forecast_calibration_quarantine(_request())

    assert decision.allowed is True
    assert decision.status == "ALLOWED"
    assert decision.reason_codes == ("REPLACEMENT_CALIBRATION_PRODUCT_SPECIFIC",)
    assert decision.as_dict()["training_allowed"] is False


def test_baseline_platt_emos_raw_and_sigma_floor_are_blocked() -> None:
    cases = [
        ("extended_platt", "ecmwf_open_data", "ecmwf_opendata_ifs_ens_0p25", "ecmwf_opendata_mx2t3_local_calendar_day_max"),
        ("emos", "ecmwf_open_data", "ecmwf_opendata_ifs_ens_0p25", "ecmwf_opendata_mn2t3_local_calendar_day_min"),
        ("raw_honest", "tigge", "tigge_ifs_ens", "tigge_mx2t6_local_calendar_day_max"),
        ("sigma_floor", "ecmwf_open_data", "ecmwf_opendata_ifs_ens_0p25", "ecmwf_opendata_mx2t3_local_calendar_day_max"),
    ]
    for method, source_id, product_id, data_version in cases:
        decision = evaluate_replacement_forecast_calibration_quarantine(
            _request(
                calibration_source_id=source_id,
                calibration_product_id=product_id,
                calibration_data_version=data_version,
                calibration_method=method,
            )
        )
        assert decision.allowed is False
        assert "REPLACEMENT_CALIBRATION_METHOD_REUSES_BASELINE_AUTHORITY" in decision.reason_codes
        assert "REPLACEMENT_CALIBRATION_BASELINE_LINEAGE_FORBIDDEN" in decision.reason_codes
        assert "REPLACEMENT_CALIBRATION_PRODUCT_IDENTITY_MISMATCH" in decision.reason_codes


def test_aifs_sampled_2t_cannot_use_period_extrema_calibration() -> None:
    decision = evaluate_replacement_forecast_calibration_quarantine(
        _request(
            target_source_id=AIFS_SOURCE_ID,
            target_product_id=AIFS_PRODUCT_ID,
            target_data_version="ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
            calibration_source_id=AIFS_SOURCE_ID,
            calibration_product_id=AIFS_PRODUCT_ID,
            calibration_data_version="ecmwf_aifs_ens_mx2t3_period_extrema_local_calendar_day_max",
            calibration_method="aifs_product_specific",
        )
    )

    assert decision.allowed is False
    assert "REPLACEMENT_CALIBRATION_SAMPLED_2T_CANNOT_USE_PERIOD_EXTREMA" in decision.reason_codes


def test_training_authority_is_blocked() -> None:
    decision = evaluate_replacement_forecast_calibration_quarantine(
        _request(training_allowed=True)
    )

    assert decision.allowed is False
    assert "REPLACEMENT_CALIBRATION_TRAINING_AUTHORITY_FORBIDDEN" in decision.reason_codes


def test_non_replacement_targets_and_short_alias_are_rejected() -> None:
    decision = evaluate_replacement_forecast_calibration_quarantine(
        _request(
            target_source_id="ecmwf_open_data",
            target_product_id="ecmwf_opendata_ifs_ens_0p25",
            target_data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
        )
    )
    assert decision.allowed is False
    assert "REPLACEMENT_CALIBRATION_TARGET_NOT_REPLACEMENT_PRODUCT" in decision.reason_codes

    with pytest.raises(ValueError, match="full replacement identity"):
        _request(target_source_id="short_" + "h" + "3_alias")
