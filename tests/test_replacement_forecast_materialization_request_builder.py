# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06
# Purpose: Protect validated request generation for replacement shadow materialization.
# Reuse: Run before changing queue input contract or live simple-switch request production.
# Authority basis: Simple switch must not depend on hand-built unvalidated materialization JSON.
"""Replacement forecast materialization request builder tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from src.data.replacement_forecast_materialization_request_builder import (
    build_replacement_forecast_materialization_request,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


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
        json.dumps(
            {
                "hourly_units": {"temperature_2m": "C"},
                "hourly": {
                    "time": ["2026-06-07T00:00", "2026-06-07T06:00", "2026-06-07T12:00"],
                    "temperature_2m": [19.0, 27.0, 24.0],
                },
            }
        ),
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

    result = build_replacement_forecast_materialization_request(seed, base_dir=tmp_path)

    assert result.ok is True
    request = result.request
    assert request is not None
    assert request["city_timezone"] == "Asia/Shanghai"
    assert request["aifs_samples_json"] == str(tmp_path / "aifs_samples.json")
    assert request["openmeteo_payload_json"] == str(tmp_path / "openmeteo_payload.json")
    assert request["precision_metadata_json"] == str(tmp_path / "precision_metadata.json")
    assert request["anchor_weight"] == 0.80
    assert request["anchor_sigma_c"] == 3.00


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
    future["aifs_source_available_at"] = "2026-06-06T05:00:00+00:00"

    future_result = build_replacement_forecast_materialization_request(future, base_dir=tmp_path)

    assert future_result.ok is False
    assert future_result.reason_codes == ("REPLACEMENT_MATERIALIZATION_REQUEST_HAS_FUTURE_DEPENDENCY",)

    precision = json.loads((tmp_path / "precision_metadata.json").read_text(encoding="utf-8"))
    precision["endpoint_mode"] = "daily_vendor_aggregated"
    (tmp_path / "precision_metadata.json").write_text(json.dumps(precision), encoding="utf-8")
    precision_result = build_replacement_forecast_materialization_request(seed, base_dir=tmp_path)

    assert precision_result.ok is False
    assert "OM9_PRECISION_GUARD_BLOCKED_REQUEST_BUILD" in precision_result.reason_codes


def test_request_builder_rejects_incomplete_market_bin_family(tmp_path) -> None:
    seed = _write_inputs(tmp_path)
    seed["bins"] = [
        {"bin_id": "cool", "lower_c": None, "upper_c": 20.0, "center_c": 19.0},
        {"bin_id": "hot", "lower_c": 25.0, "upper_c": None, "center_c": 32.0},
    ]

    try:
        build_replacement_forecast_materialization_request(seed, base_dir=tmp_path)
    except ValueError as exc:
        assert "gap" in str(exc)
    else:
        raise AssertionError("incomplete bin family must raise")


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
