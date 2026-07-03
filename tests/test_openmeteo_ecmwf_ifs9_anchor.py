# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect run-pinned Open-Meteo ECMWF IFS 9km deterministic anchor requests.
# Reuse: Run before changing Open-Meteo ECMWF IFS 9km anchor capture or manifest wiring.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t blocked-candidate integration.
"""Open-Meteo ECMWF IFS 9km anchor request tests."""

from __future__ import annotations

from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from src.data.openmeteo_ecmwf_ifs9_anchor import (
    HIGH_DATA_VERSION,
    LOW_DATA_VERSION,
    MODEL,
    PRODUCT_ID,
    SINGLE_RUNS_FORECAST_URL,
    SOURCE_ID,
    build_anchor_request,
    build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest,
    extract_openmeteo_ecmwf_ifs9_localday_anchor,
    fetch_openmeteo_ecmwf_ifs9_anchor_payload,
)


def test_anchor_request_uses_single_runs_api_and_explicit_run() -> None:
    request = build_anchor_request(
        latitude=31.2304,
        longitude=121.4737,
        run=datetime(2026, 6, 6, 0, tzinfo=timezone.utc),
        timezone_name="Asia/Shanghai",
    )

    assert request.url().startswith(SINGLE_RUNS_FORECAST_URL)
    params = parse_qs(urlparse(request.url()).query)
    assert params["run"] == ["2026-06-06T00:00"]
    assert params["models"] == [MODEL]
    assert params["hourly"] == ["temperature_2m"]
    assert params["forecast_hours"] == ["120"]
    assert params["timezone"] == ["Asia/Shanghai"]

    metadata = request.manifest_metadata()
    assert metadata["source_id"] == SOURCE_ID
    assert metadata["product_id"] == PRODUCT_ID
    assert metadata["role"] == "soft_spatial_anchor"
    assert metadata["trade_authority_status"] == "BLOCKED"
    assert metadata["training_allowed"] is False


def test_anchor_request_rejects_non_cycle_or_missing_run() -> None:
    with pytest.raises(ValueError, match="00/06/12/18"):
        build_anchor_request(
            latitude=31.2304,
            longitude=121.4737,
            run="2026-06-06T03:00:00+00:00",
            timezone_name="Asia/Shanghai",
        )

    with pytest.raises(ValueError, match="exactly on a UTC cycle hour"):
        build_anchor_request(
            latitude=31.2304,
            longitude=121.4737,
            run="2026-06-06T00:30:00+00:00",
            timezone_name="Asia/Shanghai",
        )

    with pytest.raises(ValueError, match="timezone-aware"):
        build_anchor_request(
            latitude=31.2304,
            longitude=121.4737,
            run="2026-06-06T00:00:00",
            timezone_name="Asia/Shanghai",
        )


def test_anchor_request_rejects_bad_domain_and_preserves_data_versions() -> None:
    with pytest.raises(ValueError, match="latitude"):
        build_anchor_request(
            latitude=91,
            longitude=121.4737,
            run="2026-06-06T00:00:00+00:00",
            timezone_name="Asia/Shanghai",
        )

    assert HIGH_DATA_VERSION == "openmeteo_ecmwf_ifs9_anchor_localday_high"
    assert LOW_DATA_VERSION == "openmeteo_ecmwf_ifs9_anchor_localday_low"
    for identifier in (SOURCE_ID, PRODUCT_ID, HIGH_DATA_VERSION, LOW_DATA_VERSION):
        assert ("h" + "3") not in identifier.lower()


def test_anchor_fetch_uses_shared_openmeteo_client(monkeypatch) -> None:
    request = build_anchor_request(
        latitude=31.2304,
        longitude=121.4737,
        run="2026-06-06T00:00:00+00:00",
        timezone_name="Asia/Shanghai",
    )
    calls = []

    def fake_fetch(url, params, *, timeout, max_retries, endpoint_label, fast_fail_429=False):
        calls.append((url, params, timeout, max_retries, endpoint_label, fast_fail_429))
        return {"hourly": {"time": [], "temperature_2m": []}}

    monkeypatch.setattr("src.data.openmeteo_client.fetch", fake_fetch)

    payload = fetch_openmeteo_ecmwf_ifs9_anchor_payload(request, timeout=7.0, max_retries=2)

    assert payload == {"hourly": {"time": [], "temperature_2m": []}}
    assert calls[0][0] == SINGLE_RUNS_FORECAST_URL
    assert calls[0][1]["run"] == "2026-06-06T00:00"
    assert calls[0][1]["models"] == MODEL
    assert calls[0][2] == 7.0
    assert calls[0][3] == 2
    assert calls[0][4] == "openmeteo_ecmwf_ifs9_single_runs_anchor"
    assert calls[0][5] is False


def test_anchor_artifact_manifest_preserves_run_pinned_request_and_metric_identity(tmp_path) -> None:
    artifact = tmp_path / "openmeteo-ifs9.json"
    artifact.write_text('{"hourly":{"time":["2026-06-06T00:00"],"temperature_2m":[20.0]}}\n', encoding="utf-8")
    request = build_anchor_request(
        latitude=31.1979,
        longitude=121.3363,
        run="2026-06-06T00:00:00+00:00",
        timezone_name="Asia/Shanghai",
    )

    high_manifest = build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest(
        artifact,
        request=request,
        metric="high",
        source_available_at=datetime(2026, 6, 6, 7, tzinfo=timezone.utc),
        captured_at=datetime(2026, 6, 6, 8, tzinfo=timezone.utc),
        product_metadata={"generationtime_ms": 1.23},
    )
    low_manifest = build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest(
        artifact,
        request=request,
        metric="low",
        source_available_at=datetime(2026, 6, 6, 7, tzinfo=timezone.utc),
        captured_at=datetime(2026, 6, 6, 8, tzinfo=timezone.utc),
    )

    assert high_manifest.source_id == SOURCE_ID
    assert high_manifest.product_id == PRODUCT_ID
    assert high_manifest.data_version == HIGH_DATA_VERSION
    assert low_manifest.data_version == LOW_DATA_VERSION
    assert high_manifest.request_url == SINGLE_RUNS_FORECAST_URL
    assert high_manifest.request_params["run"] == "2026-06-06T00:00"
    assert high_manifest.request_params["models"] == MODEL
    assert high_manifest.product_metadata["metric"] == "high"
    assert high_manifest.product_metadata["generationtime_ms"] == 1.23
    assert "single-runs-api.open-meteo.com" in str(high_manifest.product_metadata["openmeteo_single_runs_url"])
    high_manifest.verify_artifact()


def test_anchor_artifact_manifest_rejects_bad_metric_or_pre_available_capture(tmp_path) -> None:
    artifact = tmp_path / "openmeteo-ifs9.json"
    artifact.write_text('{"hourly":{"time":[],"temperature_2m":[]}}\n', encoding="utf-8")
    request = build_anchor_request(
        latitude=31.1979,
        longitude=121.3363,
        run="2026-06-06T00:00:00+00:00",
        timezone_name="Asia/Shanghai",
    )

    with pytest.raises(ValueError, match="metric"):
        build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest(
            artifact,
            request=request,
            metric="mean",
            source_available_at=datetime(2026, 6, 6, 7, tzinfo=timezone.utc),
            captured_at=datetime(2026, 6, 6, 8, tzinfo=timezone.utc),
        )

    with pytest.raises(ValueError, match="captured_at cannot precede source_available_at"):
        build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest(
            artifact,
            request=request,
            metric="high",
            source_available_at=datetime(2026, 6, 6, 8, tzinfo=timezone.utc),
            captured_at=datetime(2026, 6, 6, 7, tzinfo=timezone.utc),
        )


def test_anchor_response_extracts_localday_high_low_from_hourly_json() -> None:
    payload = {
        "hourly_units": {"temperature_2m": "°C"},
        "hourly": {
            "time": [
                "2026-06-05T23:00",
                "2026-06-06T00:00",
                "2026-06-06T12:00",
                "2026-06-06T23:00",
                "2026-06-07T00:00",
            ],
            "temperature_2m": [18.0, 20.0, 31.5, 24.0, 19.0],
        },
    }

    anchor = extract_openmeteo_ecmwf_ifs9_localday_anchor(
        payload,
        city_timezone="Asia/Shanghai",
        target_local_date=date(2026, 6, 6),
        source_cycle_time=datetime(2026, 6, 5, 0, tzinfo=timezone.utc),
        min_hourly_samples=3,
    )

    assert anchor.source_id == SOURCE_ID
    assert anchor.product_id == PRODUCT_ID
    assert anchor.high_data_version == HIGH_DATA_VERSION
    assert anchor.low_data_version == LOW_DATA_VERSION
    assert anchor.high_c == pytest.approx(31.5)
    assert anchor.low_c == pytest.approx(20.0)
    assert anchor.sample_count == 3
    assert [item.hour for item in anchor.contributing_local_times] == [0, 12, 23]
    assert anchor.contributing_valid_times_utc[0].hour == 16
    assert anchor.trade_authority_status == "BLOCKED"
    assert anchor.training_allowed is False


def test_anchor_response_accepts_aware_times_and_converts_fahrenheit() -> None:
    payload = {
        "hourly_units": {"temperature_2m": "°F"},
        "hourly": {
            "time": ["2026-06-05T16:00:00+00:00", "2026-06-06T06:00:00+00:00"],
            "temperature_2m": [68.0, 95.0],
        },
    }

    anchor = extract_openmeteo_ecmwf_ifs9_localday_anchor(
        payload,
        city_timezone="Asia/Shanghai",
        target_local_date=date(2026, 6, 6),
        min_hourly_samples=2,
    )

    assert anchor.low_c == pytest.approx(20.0)
    assert anchor.high_c == pytest.approx(35.0)
    assert [item.hour for item in anchor.contributing_local_times] == [0, 14]


def test_anchor_response_fails_closed_for_malformed_payload_or_coverage() -> None:
    with pytest.raises(ValueError, match="lengths must match"):
        extract_openmeteo_ecmwf_ifs9_localday_anchor(
            {"hourly": {"time": ["2026-06-06T00:00"], "temperature_2m": [20.0, 21.0]}},
            city_timezone="UTC",
            target_local_date=date(2026, 6, 6),
        )

    with pytest.raises(ValueError, match="insufficient"):
        extract_openmeteo_ecmwf_ifs9_localday_anchor(
            {"hourly": {"time": ["2026-06-05T00:00"], "temperature_2m": [20.0]}},
            city_timezone="UTC",
            target_local_date=date(2026, 6, 6),
        )

    with pytest.raises(ValueError, match="unit"):
        extract_openmeteo_ecmwf_ifs9_localday_anchor(
            {"hourly_units": {"temperature_2m": "rankine"}, "hourly": {"time": ["2026-06-06T00:00"], "temperature_2m": [20.0]}},
            city_timezone="UTC",
            target_local_date=date(2026, 6, 6),
        )
