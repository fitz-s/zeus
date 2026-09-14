# Created: 2026-06-06
# Last reused/audited: 2026-08-30
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06
# Purpose: Protect validated request generation for replacement live materialization.
# Reuse: Run before changing queue input contract or live simple-switch request production.
# Authority basis: Simple switch must not depend on hand-built unvalidated materialization JSON.
"""Replacement forecast materialization request builder tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from src.data.replacement_forecast_materialization_request_builder import (
    build_materialize_request_dataclass,
    build_replacement_forecast_materialization_request,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _openmeteo_payload(*, hours: range = range(24)) -> dict[str, object]:
    hour_values = list(hours)
    return {
        "hourly_units": {"temperature_2m": "C"},
        "hourly": {
            "time": [f"2026-06-07T{hour:02d}:00" for hour in hour_values],
            "temperature_2m": [19.0 + (hour % 9) for hour in hour_values],
        },
    }


def _write_inputs(tmp_path: Path) -> dict[str, object]:
    (tmp_path / "aifs_samples.json").write_text(
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
    (tmp_path / "openmeteo_payload.json").write_text(
        json.dumps(_openmeteo_payload()),
        encoding="utf-8",
    )
    (tmp_path / "precision_metadata.json").write_text(
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


def test_request_builder_outputs_materializer_ready_json(tmp_path) -> None:
    seed = _write_inputs(tmp_path)
    seed["input_revision_sources"] = ["hko_fnd"]

    result = build_replacement_forecast_materialization_request(seed, base_dir=tmp_path)

    assert result.ok is True
    request = result.request
    assert request is not None
    assert request["city_timezone"] == "Asia/Shanghai"
    assert "aifs_samples_json" not in request
    assert request["openmeteo_payload_json"] == str(tmp_path / "openmeteo_payload.json")
    assert request["precision_metadata_json"] == str(tmp_path / "precision_metadata.json")
    assert request["anchor_weight"] == 0.80
    assert request["anchor_sigma_c"] == 3.00
    assert request["input_revision_sources"] == ["hko_fnd"]


def test_shared_precision_metadata_rebinds_to_each_materialization_target(
    tmp_path,
) -> None:
    seed = _write_inputs(tmp_path)
    precision_path = tmp_path / "precision_metadata.json"
    precision = json.loads(precision_path.read_text(encoding="utf-8"))
    precision.update(
        target_local_date="2026-06-06",
        local_day_start_utc="2026-06-05T16:00:00+00:00",
        local_day_end_utc="2026-06-06T16:00:00+00:00",
    )
    precision_path.write_text(json.dumps(precision), encoding="utf-8")

    built = build_replacement_forecast_materialization_request(
        seed,
        base_dir=tmp_path,
    )
    assert built.ok is True
    request = build_materialize_request_dataclass(
        built.request,
        base_dir=tmp_path,
    )

    metadata = request.openmeteo_precision_guard.metadata
    assert metadata.target_local_date.isoformat() == "2026-06-07"
    assert metadata.local_day_start_utc.isoformat() == (
        "2026-06-06T16:00:00+00:00"
    )
    assert metadata.local_day_end_utc.isoformat() == (
        "2026-06-07T16:00:00+00:00"
    )


def test_request_builder_blocks_incomplete_om9_localday_coverage(tmp_path) -> None:
    seed = _write_inputs(tmp_path)
    (tmp_path / "openmeteo_payload.json").write_text(
        json.dumps(_openmeteo_payload(hours=range(23))),
        encoding="utf-8",
    )

    result = build_replacement_forecast_materialization_request(seed, base_dir=tmp_path)

    assert result.ok is False
    assert result.reason_codes == ("REPLACEMENT_MATERIALIZATION_OM9_LOCALDAY_HOURLY_COVERAGE_INCOMPLETE",)
    assert result.request is None


def test_request_builder_allows_post_localday_day0_observation_to_cover_elapsed_hours(tmp_path) -> None:
    seed = _write_inputs(tmp_path)
    seed.update(
        {
            "computed_at": "2026-06-07T17:00:00+00:00",
            "expires_at": "2026-06-08T00:00:00+00:00",
            "day0_observed_extreme_c": 32.0,
            "day0_observed_extreme_source": "durable_observation_instants",
            "day0_observed_extreme_observation_time": "2026-06-07T15:00:00+00:00",
            "day0_observed_extreme_sample_count": 24,
            "day0_observed_extreme_unit": "C",
        }
    )
    (tmp_path / "openmeteo_payload.json").write_text(
        json.dumps(_openmeteo_payload(hours=range(14, 24))),
        encoding="utf-8",
    )

    result = build_replacement_forecast_materialization_request(seed, base_dir=tmp_path)

    assert result.ok is True
    assert result.request is not None
    assert result.request["day0_observed_extreme_c"] == 32.0


def test_request_builder_threads_typed_day0_zero_observation_state(
    tmp_path,
) -> None:
    seed = _write_inputs(tmp_path)
    seed["day0_observation_state"] = "zero_target_date_observations"

    result = build_replacement_forecast_materialization_request(
        seed,
        base_dir=tmp_path,
    )

    assert result.ok is True
    assert result.request is not None
    assert (
        result.request["day0_observation_state"]
        == "zero_target_date_observations"
    )
    request = build_materialize_request_dataclass(
        result.request,
        base_dir=tmp_path,
    )
    assert request.day0_observation_state == "zero_target_date_observations"


def test_request_builder_preserves_display_settlement_units_and_rounding_rule(tmp_path) -> None:
    seed = _write_inputs(tmp_path)
    seed["settlement_step_c"] = 5.0 / 9.0
    seed["bins"] = [
        {
            "bin_id": "27°C or below",
            "lower_c": None,
            "upper_c": 27.0,
            "center_c": 26.0,
            "display_unit": "C",
            "settlement_unit": "F",
            "rounding_rule": "wmo_half_up",
        },
        {
            "bin_id": "28°C",
            "lower_c": 28.0,
            "upper_c": 28.0,
            "center_c": 28.0,
            "display_unit": "C",
            "settlement_unit": "F",
            "rounding_rule": "wmo_half_up",
        },
        {
            "bin_id": "29°C or above",
            "lower_c": 29.0,
            "upper_c": None,
            "center_c": 30.0,
            "display_unit": "C",
            "settlement_unit": "F",
            "rounding_rule": "wmo_half_up",
        },
    ]

    result = build_replacement_forecast_materialization_request(seed, base_dir=tmp_path)

    assert result.ok is True
    request = result.request
    assert request is not None
    middle = request["bins"][1]
    assert middle["display_unit"] == "C"
    assert middle["settlement_unit"] == "F"
    assert middle["rounding_rule"] == "wmo_half_up"


def test_request_builder_blocks_future_dependency_and_bad_precision(tmp_path) -> None:
    seed = _write_inputs(tmp_path)
    future = dict(seed)
    future["openmeteo_source_available_at"] = "2026-06-06T05:00:00+00:00"

    future_result = build_replacement_forecast_materialization_request(future, base_dir=tmp_path)

    assert future_result.ok is False
    assert future_result.reason_codes == ("REPLACEMENT_MATERIALIZATION_REQUEST_HAS_FUTURE_DEPENDENCY",)

    precision = json.loads((tmp_path / "precision_metadata.json").read_text(encoding="utf-8"))
    precision["endpoint_mode"] = "daily_vendor_aggregated"
    (tmp_path / "precision_metadata.json").write_text(json.dumps(precision), encoding="utf-8")
    precision_result = build_replacement_forecast_materialization_request(seed, base_dir=tmp_path)

    assert precision_result.ok is False
    assert "OM9_PRECISION_GUARD_NOT_LIVE_PASS_REQUEST_BUILD" in precision_result.reason_codes


def test_request_builder_leaves_market_bin_completeness_to_family_semantics(tmp_path) -> None:
    seed = _write_inputs(tmp_path)
    seed["bins"] = [
        {"bin_id": "cool", "lower_c": None, "upper_c": 20.0, "center_c": 19.0},
        {"bin_id": "hot", "lower_c": 25.0, "upper_c": None, "center_c": 32.0},
    ]

    result = build_replacement_forecast_materialization_request(seed, base_dir=tmp_path)

    assert result.ok is True
    assert result.request is not None


def test_request_builder_cli_writes_queue_request(tmp_path) -> None:
    seed = _write_inputs(tmp_path)
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(seed), encoding="utf-8")
    queue_dir = tmp_path / "queue"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_replacement_forecast_materialization_request.py",
            "--input-json",
            str(seed_path),
            "--queue-dir",
            str(queue_dir),
            "--stdout",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    report = json.loads(result.stdout)
    queued = json.loads((queue_dir / "seed.json").read_text(encoding="utf-8"))
    assert report["status"] == "READY"
    assert queued["baseline_source_run_id"] == "b0-run"
    assert queued["precision_metadata_json"] == str(tmp_path / "precision_metadata.json")


def test_request_retains_independent_anchor_and_carrier_clocks(tmp_path) -> None:
    from datetime import datetime

    for carrier_hour, anchor_hour in ((6, 12), (12, 6), (6, 6)):
        seed = _write_inputs(tmp_path)
        seed.update(computed_at="2026-06-06T14:00:00+00:00",
                    expires_at="2026-06-06T15:00:00+00:00",
                    baseline_source_available_at="2026-06-06T13:00:00+00:00",
                    openmeteo_source_available_at="2026-06-06T13:00:00+00:00")
        carrier = f"2026-06-06T{carrier_hour:02d}:00:00+00:00"
        anchor = f"2026-06-06T{anchor_hour:02d}:00:00+00:00"
        seed["source_cycle_time"] = carrier
        seed["openmeteo_source_cycle_time"] = anchor
        result = build_replacement_forecast_materialization_request(seed, base_dir=tmp_path)
        assert result.ok, result.reason_codes
        request = result.request
        assert request is not None
        assert request["source_cycle_time"] == carrier
        assert request["openmeteo_source_cycle_time"] == anchor
        typed = build_materialize_request_dataclass(request, base_dir=tmp_path)
        assert typed.source_cycle_time == datetime.fromisoformat(carrier)
        assert typed.openmeteo_anchor.source_cycle_time == datetime.fromisoformat(anchor)



def test_seed_requires_the_explicit_current_ens_carrier_without_relabeling_baseline(tmp_path) -> None:
    from src.data.raw_forecast_artifact_manifest import read_manifest
    from src.data.replacement_forecast_materialization_seed_builder import build_replacement_forecast_materialization_seed
    from src.data.replacement_forecast_source_run_identity import expected_replacement_dependency_identity_by_role
    from tests.test_replacement_forecast_seed_discovery import _write_raw_inputs

    raw = tmp_path / "raw"
    _write_raw_inputs(raw)
    coverage = dict(source_run_id="ens06", source_id="ecmwf_open_data",
                    data_version=expected_replacement_dependency_identity_by_role("high")["baseline_b0"].data_version,
                    temperature_metric="high", completeness_status="COMPLETE", readiness_status="LIVE_ELIGIBLE",
                    expires_at="2026-06-07T00:00:00+00:00", computed_at="2026-06-06T13:00:00+00:00",
                    source_cycle_time="2026-06-06T06:00:00+00:00", source_available_at="2026-06-06T13:00:00+00:00",
                    city_id="NYC", city_timezone="America/New_York")
    kwargs = dict(city="NYC", target_date="2026-06-08", temperature_metric="high",
                  market_bins=({"range_label": "75°F", "range_low": 75.0, "range_high": 75.0},),
                  baseline_coverage=coverage, openmeteo_manifest=read_manifest(raw / "openmeteo.manifest.json"),
                  openmeteo_payload_json=raw / "openmeteo.json", precision_metadata_json=raw / "precision_metadata.json",
                  computed_at="2026-06-06T14:00:00+00:00", base_dir=tmp_path)
    blocked = build_replacement_forecast_materialization_seed(
        **kwargs, carrier_cycle_time="2026-06-06T12:00:00+00:00"
    )
    assert blocked.seed is None
    assert "REPLACEMENT_MATERIALIZATION_ENS_CARRIER_BASELINE_CYCLE_MISMATCH" in blocked.reason_codes
    ready = build_replacement_forecast_materialization_seed(
        **kwargs, carrier_cycle_time=coverage["source_cycle_time"]
    )
    assert ready.ok, ready.reason_codes
    assert ready.seed["source_cycle_time"] == coverage["source_cycle_time"]
    assert ready.seed["openmeteo_source_cycle_time"] == "2026-06-06T00:00:00+00:00"
