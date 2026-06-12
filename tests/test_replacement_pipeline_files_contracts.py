# Created: 2026-06-10
# Last reused/audited: 2026-06-10
# Authority basis: pipeline-contract project, operator directive 2026-06-10
"""Relationship tests for the replacement-pipeline file contracts.

These are NOT plain function tests. They pin the producer⇄consumer compatibility
relationship that the 2026-06-10 queue-starvation incident broke:

* ROUND-TRIP: each producer's READY output passes the matching consumer's
  validator. If a producer's dict-assembly site drifts away from what the
  consumer expects, one of these fails immediately — the divergence can no
  longer be silent-until-a-downstream-KeyError.
* VIOLATION: the exact new-listing-scout intent stub
  ``{source, condition_id, enqueued_at, reason}`` is REJECTED by the
  MATERIALIZATION_REQUEST validator with a message naming every missing field.
  That is the precise shape that crashed the materializer subprocess on every
  cycle and starved 772 cells; the contract makes it unconstructable as a
  request.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.contracts.replacement_pipeline_files import (
    ContractViolation,
    MATERIALIZATION_REQUEST_SCHEMA_VERSION,
    MATERIALIZATION_SEED_SCHEMA_VERSION,
    SCOUT_INTENT_SCHEMA_VERSION,
    validate_materialization_request,
    validate_materialization_seed,
    validate_scout_intent,
)
from src.data.replacement_forecast_materialization_request_builder import (
    build_replacement_forecast_materialization_request,
)


# ---------------------------------------------------------------------------
# Fixtures: a real seed payload + its on-disk artifacts (mirrors the queue test
# _write_seed_inputs so the REQUEST round-trip exercises the genuine builder).
# ---------------------------------------------------------------------------
def _write_seed_artifacts(base: Path) -> dict[str, object]:
    (base / "aifs_samples.json").write_text(
        json.dumps(
            {
                "samples": [
                    {"member_id": "pf-001", "valid_time_utc": "2026-06-06T18:00:00+00:00", "temperature": 18.0, "temperature_unit": "C"},
                    {"member_id": "pf-002", "valid_time_utc": "2026-06-06T18:00:00+00:00", "temperature": 25.0, "temperature_unit": "C"},
                    {"member_id": "pf-003", "valid_time_utc": "2026-06-06T18:00:00+00:00", "temperature": 32.0, "temperature_unit": "C"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (base / "openmeteo_payload.json").write_text(
        json.dumps({"hourly_units": {"temperature_2m": "C"}, "hourly": {"time": ["2026-06-07T00:00"], "temperature_2m": [20.0]}}),
        encoding="utf-8",
    )
    (base / "precision_metadata.json").write_text(
        json.dumps(
            {
                "city": "Shanghai",
                "station_id": "ZSSS",
                "city_lat": 31.2304,
                "city_lon": 121.4737,
                "station_lat": 31.1979,
                "station_lon": 121.3363,
                "requested_lat": 31.1979,
                "requested_lon": 121.3363,
                "requested_coordinate_precision_decimals": 4,
                "nearest_grid_lat": 31.2,
                "nearest_grid_lon": 121.3,
                "nearest_grid_distance_km": 3.5,
                "native_grid": "openmeteo_ecmwf_ifs_9km",
                "delivery_grid_resolution": "0p1",
                "interpolation_method": "nearest_gridpoint",
                "endpoint_mode": "hourly_zeus_aggregated",
                "local_day_start_utc": "2026-06-06T16:00:00+00:00",
                "local_day_end_utc": "2026-06-07T16:00:00+00:00",
                "timezone_name": "Asia/Shanghai",
                "target_local_date": "2026-06-07",
                "temperature_unit": "C",
                "anchor_sigma_c": 3.0,
                "grid_elevation_m": 4.0,
                "station_elevation_m": 3.0,
                "land_sea_mask": "land",
                "city_class": "flat_inland",
                "station_mapping_policy": "settlement_station",
            }
        ),
        encoding="utf-8",
    )
    return {
        "city": "Shanghai",
        "target_date": "2026-06-07",
        "temperature_metric": "high",
        "source_cycle_time": "2026-06-06T00:00:00+00:00",
        "computed_at": "2026-06-06T04:00:00+00:00",
        "expires_at": "2026-06-06T06:00:00+00:00",
        "baseline_source_run_id": "b0-run",
        "baseline_data_version": "ecmwf_opendata_mx2t3_local_calendar_day_max",
        "baseline_source_available_at": "2026-06-06T02:00:00+00:00",
        "aifs_source_run_id": "aifs-run",
        "aifs_source_available_at": "2026-06-06T02:30:00+00:00",
        "openmeteo_source_run_id": "openmeteo-run",
        "openmeteo_source_available_at": "2026-06-06T03:00:00+00:00",
        "aifs_samples_json": "aifs_samples.json",
        "openmeteo_payload_json": "openmeteo_payload.json",
        "precision_metadata_json": "precision_metadata.json",
        "bins": [
            {"bin_id": "cool", "lower_c": None, "upper_c": 20.0, "center_c": 19.0},
            {"bin_id": "warm", "lower_c": 21.0, "upper_c": 30.0, "center_c": 25.0},
            {"bin_id": "hot", "lower_c": 31.0, "upper_c": None, "center_c": 32.0},
        ],
    }


def _valid_seed_payload(base: Path) -> dict[str, object]:
    """A seed dict that satisfies the SEED contract (mirrors seed builder output)."""
    payload = _write_seed_artifacts(base)
    payload.update(
        {
            "city_timezone": "Asia/Shanghai",
            "anchor_weight": 0.80,
            "anchor_sigma_c": 3.00,
            "settlement_step_c": 1.0,
        }
    )
    return payload


def _scout_stub() -> dict[str, object]:
    return {
        "source": "new_listing_scout",
        "condition_id": "0xabcdef",
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
        "reason": "NEW_LISTING_FAST_LANE",
    }


# ===========================================================================
# ROUND-TRIP: producer output passes consumer validation
# ===========================================================================
def test_request_builder_output_passes_consumer_validation(tmp_path) -> None:
    """REQUEST round-trip: the genuine request builder's READY output validates."""
    seed = _write_seed_artifacts(tmp_path)
    result = build_replacement_forecast_materialization_request(seed, base_dir=tmp_path)
    assert result.ok, result.reason_codes
    assert result.request is not None
    request = validate_materialization_request(dict(result.request))
    assert request.city == "Shanghai"
    assert request.temperature_metric == "high"
    assert request.aifs_input_key == "aifs_samples_json"
    assert request.schema_version == MATERIALIZATION_REQUEST_SCHEMA_VERSION


def test_seed_payload_round_trips_through_seed_validator(tmp_path) -> None:
    """SEED round-trip: a builder-shaped seed payload validates."""
    seed = _valid_seed_payload(tmp_path)
    validated = validate_materialization_seed(seed)
    assert validated.city == "Shanghai"
    assert validated.aifs_input_key == "aifs_samples_json"
    assert validated.settlement_step_c == 1.0
    assert validated.schema_version == MATERIALIZATION_SEED_SCHEMA_VERSION


def test_scout_intent_round_trips_through_intent_validator() -> None:
    """INTENT round-trip: the scout's stub validates as a SCOUT_INTENT."""
    intent = validate_scout_intent(_scout_stub())
    assert intent.source == "new_listing_scout"
    assert intent.reason == "NEW_LISTING_FAST_LANE"
    assert intent.schema_version == SCOUT_INTENT_SCHEMA_VERSION
    # to_dict round-trips back through the validator unchanged.
    assert validate_scout_intent(intent.to_dict()).condition_id == intent.condition_id


# ===========================================================================
# VIOLATION: the scout stub is rejected as a REQUEST with named missing fields
# ===========================================================================
def test_scout_stub_rejected_as_request_naming_missing_fields() -> None:
    """The exact starvation-causing shape is rejected by the REQUEST validator."""
    with pytest.raises(ContractViolation) as excinfo:
        validate_materialization_request(_scout_stub())
    message = str(excinfo.value)
    assert "MATERIALIZATION_REQUEST" in message
    # The message must NAME the missing HARD-REQUIRED (minimal-runnable) fields so
    # an operator reading the failed/ receipt sees exactly what was wrong. The
    # consumer gate's required set is the materializer's immediate-access set
    # (temperature_metric, target_date, source_cycle_time) — the keys whose
    # absence KeyError-crashes the subprocess. The scout stub carries none of them.
    for required_field in (
        "temperature_metric",
        "target_date",
        "source_cycle_time",
    ):
        assert required_field in message, f"{required_field} not named in: {message}"


def test_scout_stub_also_rejected_as_seed() -> None:
    with pytest.raises(ContractViolation):
        validate_materialization_seed(_scout_stub())


def test_request_missing_aifs_input_is_rejected(tmp_path) -> None:
    seed = _write_seed_artifacts(tmp_path)
    result = build_replacement_forecast_materialization_request(seed, base_dir=tmp_path)
    assert result.ok
    payload = dict(result.request)
    payload.pop("aifs_samples_json", None)
    payload.pop("aifs_grib_path", None)
    with pytest.raises(ContractViolation) as excinfo:
        validate_materialization_request(payload)
    assert "AIFS input selector" in str(excinfo.value)


def test_request_wrong_typed_number_is_rejected(tmp_path) -> None:
    seed = _write_seed_artifacts(tmp_path)
    result = build_replacement_forecast_materialization_request(seed, base_dir=tmp_path)
    payload = dict(result.request)
    payload["anchor_weight"] = "not-a-number"
    with pytest.raises(ContractViolation) as excinfo:
        validate_materialization_request(payload)
    assert "anchor_weight" in str(excinfo.value)


def test_non_object_payload_rejected() -> None:
    with pytest.raises(ContractViolation):
        validate_materialization_request([1, 2, 3])  # type: ignore[arg-type]
    with pytest.raises(ContractViolation):
        validate_materialization_seed("not-a-dict")  # type: ignore[arg-type]
    with pytest.raises(ContractViolation):
        validate_scout_intent(42)  # type: ignore[arg-type]


def test_empty_required_field_treated_as_missing() -> None:
    intent = _scout_stub()
    intent["condition_id"] = "   "
    with pytest.raises(ContractViolation) as excinfo:
        validate_scout_intent(intent)
    assert "condition_id" in str(excinfo.value)
