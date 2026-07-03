import json

from tests.test_replacement_forecast_materializer import _precision_guard

from src.data.replacement_forecast_materialization_request_builder import build_replacement_forecast_materialization_request


def test_obsolete_aifs_seed_fields_do_not_enter_materialization_request(tmp_path) -> None:
    (tmp_path / "samples.json").write_text(json.dumps({"samples": [{"member_id": "m00", "valid_time_utc": "2026-06-06T18:00:00+00:00", "temperature": 20.0}]}))
    hours = list(range(24))
    (tmp_path / "om9.json").write_text(
        json.dumps(
            {
                "hourly_units": {"temperature_2m": "C"},
                "hourly": {
                    "time": [f"2026-06-07T{hour:02d}:00" for hour in hours],
                    "temperature_2m": [19.0 + (hour % 9) for hour in hours],
                },
            }
        )
    )
    (tmp_path / "precision.json").write_text(json.dumps(_precision_guard().metadata.__dict__, default=str))
    payload = {
        "city": "Shanghai",
        "city_timezone": "Asia/Shanghai",
        "target_date": "2026-06-07",
        "temperature_metric": "high",
        "source_cycle_time": "2026-06-06T00:00:00+00:00",
        "computed_at": "2026-06-06T04:00:00+00:00",
        "baseline_source_run_id": "b0-run",
        "baseline_data_version": "ecmwf_opendata_mx2t3_local_calendar_day_max",
        "baseline_source_available_at": "2026-06-06T02:00:00+00:00",
        "aifs_source_run_id": "aifs-run",
        "aifs_source_available_at": "2026-06-06T02:30:00+00:00",
        "openmeteo_source_run_id": "om9-run",
        "openmeteo_source_available_at": "2026-06-06T03:00:00+00:00",
        "bins": [
            {"bin_id": "cold", "upper_c": 20.0, "center_c": 19.0},
            {"bin_id": "warm", "lower_c": 21.0, "upper_c": None, "center_c": 22.0},
        ],
        "aifs_samples_json": "samples.json",
        "openmeteo_payload_json": "om9.json",
        "precision_metadata_json": "precision.json",
    }

    result = build_replacement_forecast_materialization_request(payload, base_dir=tmp_path)

    assert result.ok is True
    assert result.request is not None
    assert "aifs_samples_json" not in result.request
    assert "aifs_source_run_id" not in result.request
    assert "aifs_source_available_at" not in result.request
