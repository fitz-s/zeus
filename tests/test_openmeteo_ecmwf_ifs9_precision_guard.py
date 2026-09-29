# Created: 2026-06-06
# Last reused/audited: 2026-09-29
# Lifecycle: created=2026-06-06; last_reviewed=2026-09-29; last_reused=2026-09-29
# Purpose: Protect Open-Meteo ECMWF IFS 9km deterministic anchor precision metadata gates.
# Reuse: Run before allowing OM9 anchor rows into replacement posterior readiness.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t shadow/veto integration.
"""Open-Meteo ECMWF IFS 9km precision guard tests."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib

import pytest

from src.data.openmeteo_ecmwf_ifs9_precision_guard import (
    OpenMeteoIfs9PrecisionMetadata,
    evaluate_openmeteo_ecmwf_ifs9_precision_guard,
)


UTC = timezone.utc


@pytest.fixture(autouse=True)
def _legacy_precision_fixtures_are_not_source_certificates(monkeypatch, request) -> None:
    # Existing examples isolate the original precision policy; source authenticity
    # has its own tests below, with raw bytes and independent station/cell witnesses.
    if not request.node.name.startswith("test_source_geometry_"):
        import src.data.openmeteo_ecmwf_ifs9_precision_guard as guard
        monkeypatch.setattr(guard, "geometry_proof_authenticity_reason", lambda *_args, **_kwargs: None)


def _metadata(**overrides: object) -> OpenMeteoIfs9PrecisionMetadata:
    values = {
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
        "local_day_start_utc": datetime(2026, 6, 5, 16, tzinfo=UTC),
        "local_day_end_utc": datetime(2026, 6, 6, 16, tzinfo=UTC),
        "timezone_name": "Asia/Shanghai",
        "target_local_date": date(2026, 6, 6),
        "temperature_unit": "C",
        "anchor_sigma_c": 3.0,
        "grid_elevation_m": 4.0,
        "station_elevation_m": 3.0,
        "land_sea_mask": "land",
        "city_class": "flat_inland",
        "station_mapping_policy": "settlement_station",
    }
    values.update(overrides)
    return OpenMeteoIfs9PrecisionMetadata(**values)  # type: ignore[arg-type]


def test_openmeteo_ifs9_precision_guard_passes_complete_hourly_station_metadata() -> None:
    result = evaluate_openmeteo_ecmwf_ifs9_precision_guard(_metadata())

    assert result.status == "PASS"
    assert result.reason_codes == ("OM9_PRECISION_METADATA_PASS",)
    assert result.elevation_delta_m == pytest.approx(1.0)
    assert result.high_risk_bucket == "standard"
    assert result.passable_for_live_materialization is True


def test_openmeteo_ifs9_precision_guard_blocks_vendor_daily_or_unknown_interpolation() -> None:
    daily = evaluate_openmeteo_ecmwf_ifs9_precision_guard(_metadata(endpoint_mode="daily_vendor_aggregated"))
    assert daily.status == "BLOCK"
    assert "OM9_ENDPOINT_MUST_BE_HOURLY_ZEUS_AGGREGATED" in daily.reason_codes

    unknown = evaluate_openmeteo_ecmwf_ifs9_precision_guard(_metadata(interpolation_method="unknown"))
    assert unknown.status == "BLOCK"
    assert "OM9_INTERPOLATION_METHOD_REQUIRED" in unknown.reason_codes


def test_openmeteo_ifs9_precision_guard_blocks_missing_grid_identity_or_units() -> None:
    bad_grid = evaluate_openmeteo_ecmwf_ifs9_precision_guard(_metadata(native_grid="vendor_latest", delivery_grid_resolution="unknown"))
    assert bad_grid.status == "BLOCK"
    assert "OM9_NATIVE_GRID_UNVERIFIED" in bad_grid.reason_codes
    assert "OM9_DELIVERY_GRID_RESOLUTION_UNVERIFIED" in bad_grid.reason_codes

    fahrenheit = evaluate_openmeteo_ecmwf_ifs9_precision_guard(_metadata(temperature_unit="F"))
    assert fahrenheit.status == "BLOCK"
    assert "OM9_ANCHOR_UNIT_MUST_BE_CELSIUS" in fahrenheit.reason_codes


def test_openmeteo_ifs9_precision_guard_blocks_city_center_or_low_precision_requested_coordinates() -> None:
    city_center = evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        _metadata(requested_lat=31.2304, requested_lon=121.4737)
    )
    assert city_center.status == "BLOCK"
    assert "OM9_REQUESTED_COORDINATE_NOT_SETTLEMENT_STATION" in city_center.reason_codes

    low_precision = evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        _metadata(
            requested_lat=31.2,
            requested_lon=121.3,
            requested_coordinate_precision_decimals=1,
        )
    )
    assert low_precision.status == "BLOCK"
    assert "OM9_REQUESTED_COORDINATE_PRECISION_TOO_LOW" in low_precision.reason_codes


def test_openmeteo_ifs9_precision_guard_blocks_missing_elevation_landsea_or_far_gridpoint() -> None:
    missing = evaluate_openmeteo_ecmwf_ifs9_precision_guard(_metadata(grid_elevation_m=None, land_sea_mask=None))
    assert missing.status == "BLOCK"
    assert "OM9_ELEVATION_METADATA_REQUIRED" in missing.reason_codes
    assert "OM9_LAND_SEA_MASK_REQUIRED" in missing.reason_codes

    far = evaluate_openmeteo_ecmwf_ifs9_precision_guard(_metadata(nearest_grid_distance_km=25.0))
    assert far.status == "BLOCK"
    assert "OM9_NEAREST_GRID_DISTANCE_HIGH" in far.reason_codes


def test_openmeteo_ifs9_precision_guard_serves_provider_sea_cell_and_reviews_terrain() -> None:
    # The provider serves its sea cell for every coastal request it cannot move
    # to land; the cell's identity is certified, so it is the anchor, not a ban.
    coastal = evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        _metadata(city_class="coastal", land_sea_mask="sea", grid_elevation_m=0.0)
    )
    assert coastal.status == "PASS"
    assert coastal.high_risk_bucket == "coastal"
    assert coastal.passable_for_live_materialization is True

    mountain = evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        _metadata(city_class="mountain", grid_elevation_m=300.0, station_elevation_m=120.0)
    )
    assert mountain.status == "REVIEW_REQUIRED"
    assert mountain.high_risk_bucket == "mountain"
    assert "OM9_TERRAIN_ELEVATION_REVIEW_REQUIRED" in mountain.reason_codes


def test_openmeteo_ifs9_precision_metadata_rejects_bad_local_day_window() -> None:
    with pytest.raises(ValueError, match="23, 24, or 25 hours"):
        _metadata(
            local_day_start_utc="2026-06-06T00:00:00+00:00",
            local_day_end_utc="2026-06-06T22:00:00+00:00",
        )

    dst_23h = evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        _metadata(
            local_day_start_utc="2026-03-08T05:00:00+00:00",
            local_day_end_utc="2026-03-09T04:00:00+00:00",
            timezone_name="America/New_York",
            target_local_date="2026-03-08",
        )
    )
    assert dst_23h.status == "PASS"


def test_source_geometry_rejects_legacy_fake_zero_and_missing_raw_bytes() -> None:
    from src.data.openmeteo_ecmwf_ifs9_precision_guard import geometry_proof_authenticity_reason

    legacy = _metadata(grid_elevation_m=0.0, station_elevation_m=0.0,
                       nearest_grid_lat=31.1979, nearest_grid_lon=121.3363,
                       nearest_grid_distance_km=0.0, land_sea_mask="land")
    assert geometry_proof_authenticity_reason(legacy, raw_payload_bytes=b"{}") == "OM9_SOURCE_GEOMETRY_PROOF_MISSING"
    fake = _metadata(source_geometry_proof={
        "revision": "openmeteo_ifs9_o1280_source_cell_v1",
        "raw_payload_sha256": hashlib.sha256(b"{}").hexdigest(),
    })
    assert geometry_proof_authenticity_reason(fake) == "OM9_SOURCE_RESPONSE_BYTES_MISSING"


def _official_hko_precision(tmp_path, monkeypatch):
    import json
    import src.config as config
    import scripts.download_replacement_forecast_current_targets as dl
    import src.data.openmeteo_ecmwf_ifs9_bucket_transport as transport
    from tests.test_config import _official_hko_registry
    registry, artifact, rows = _official_hko_registry(tmp_path, monkeypatch)
    raw = json.dumps({"latitude": 22.3, "longitude": 114.17, "elevation": 28.0,
                      "timezone": "Asia/Hong_Kong"}).encode()
    cell = {
        "revision": "openmeteo_ifs9_o1280_source_cell_v1",
        "static_hsurf_sha256": "static-v1", "selected_flat_index": 12,
        "selected_grid_lat": 22.3, "selected_grid_lon": 114.17,
        "raw_grid_elevation_m": 30.0, "effective_grid_elevation_m": 30.0,
        "target_dem_elevation_m": 28.0, "cell_is_sea": False,
        "cell_is_center": False, "nearby_sea": False,
    }
    monkeypatch.setattr(transport, "source_cell_geometry_proof", lambda **_kwargs: dict(cell))
    metadata = OpenMeteoIfs9PrecisionMetadata(**dl._precision_metadata(
        "Hong Kong", "2026-09-29", anchor_sigma_c=3.0, raw_payload_bytes=raw,
    ))
    return metadata, raw, cell, registry, artifact, rows


def _official_kord_precision(tmp_path, monkeypatch):
    import json
    import src.config as config
    import scripts.download_replacement_forecast_current_targets as dl
    import src.data.openmeteo_ecmwf_ifs9_bucket_transport as transport
    from tests.test_config import _official_kord_registry
    registry, artifact, rows = _official_kord_registry(tmp_path, monkeypatch)
    city = config.cities_by_name["Chicago"]
    raw = json.dumps({"latitude": city.lat, "longitude": city.lon, "elevation": 204.8,
                      "timezone": city.timezone}).encode()
    cell = {
        "revision": "openmeteo_ifs9_o1280_source_cell_v1",
        "static_hsurf_sha256": "controlled-static-v1", "selected_flat_index": 12,
        "selected_grid_lat": city.lat, "selected_grid_lon": city.lon,
        "raw_grid_elevation_m": 205.0, "effective_grid_elevation_m": 205.0,
        "target_dem_elevation_m": 204.8, "cell_is_sea": False,
        "cell_is_center": False, "nearby_sea": False,
    }
    # Controlled native HSURF fixture, not a ground/precision authority stub.
    monkeypatch.setattr(transport, "source_cell_geometry_proof", lambda **_kwargs: dict(cell))
    metadata = OpenMeteoIfs9PrecisionMetadata(**dl._precision_metadata(
        "Chicago", "2026-09-30", anchor_sigma_c=3.0, raw_payload_bytes=raw,
    ))
    return metadata, raw, cell, registry, artifact, rows


def test_source_geometry_kord_producer_uses_verified_ground_not_reference_height(tmp_path, monkeypatch):
    import json
    metadata, raw, _, registry, _, rows = _official_kord_precision(tmp_path, monkeypatch)
    assert metadata.station_elevation_m == 204.8
    assert metadata.requested_lat == 41.96017
    assert metadata.requested_lon == -87.93161
    assert metadata.station_lat == 41.9786  # preserve separate reference identity
    assert metadata.station_lon == -87.9048
    ground = metadata.source_geometry_proof["station_ground_proof"]
    assert ground["facts"]["source_kind"] == "noaa_homr_primary_dcp_snapshot_v1"
    assert evaluate_openmeteo_ecmwf_ifs9_precision_guard(metadata, raw_payload_bytes=raw).status == "PASS"
    rows["Chicago"]["station_ground_proof"]["height_role"] = "airport_msl"
    registry.write_text(json.dumps(rows))
    assert "OM9_STATION_GROUND_PROOF_UNPROVEN" in evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        metadata, raw_payload_bytes=raw,
    ).reason_codes


def test_source_geometry_binds_response_station_and_static_surface(monkeypatch, tmp_path) -> None:
    import json
    metadata, raw, cell, registry, artifact, rows = _official_hko_precision(tmp_path, monkeypatch)
    proof = metadata.source_geometry_proof
    assert evaluate_openmeteo_ecmwf_ifs9_precision_guard(metadata, raw_payload_bytes=raw).status == "PASS"
    assert "OM9_SOURCE_RESPONSE_IDENTITY_MISMATCH" in evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        metadata, raw_payload_bytes=raw + b" "
    ).reason_codes
    for altered in (
        b'{"latitude":22.4,"longitude":114.17,"elevation":28.0,"timezone":"Asia/Hong_Kong"}',
        b'{"latitude":22.3,"longitude":114.17,"elevation":80.0,"timezone":"Asia/Hong_Kong"}',
    ):
        rebound = _metadata(**{**metadata.__dict__, "source_geometry_proof": {
            **proof, "raw_payload_sha256": hashlib.sha256(altered).hexdigest(),
        }})
        assert "OM9_SOURCE_RESPONSE_GEOMETRY_MISMATCH" in evaluate_openmeteo_ecmwf_ifs9_precision_guard(
            rebound, raw_payload_bytes=altered,
        ).reason_codes
    assert "OM9_SOURCE_GEOMETRY_PROOF_MISMATCH" in evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        _metadata(**{**metadata.__dict__, "source_geometry_proof": {**proof, "static_hsurf_sha256": "wrong"}}),
        raw_payload_bytes=raw,
    ).reason_codes
    assert "OM9_STATION_SOURCE_IDENTITY_MISMATCH" in evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        _metadata(**{**metadata.__dict__, "station_elevation_m": 0.0}),
        raw_payload_bytes=raw,
    ).reason_codes
    # Same station, changed unrelated registry row: provenance remains historical,
    # current station identity is still validated independently.
    rows["Manila"]["source"] = "unrelated audit edit"
    registry.write_text(json.dumps(rows))
    assert evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        metadata, raw_payload_bytes=raw,
    ).status == "PASS"


def test_source_geometry_producer_uses_actual_response_and_precise_station(monkeypatch) -> None:
    import json
    import scripts.download_replacement_forecast_current_targets as dl
    import src.data.openmeteo_ecmwf_ifs9_bucket_transport as transport
    from src.config import cities_by_name

    requested = cities_by_name["Manila"]
    response = {"latitude": 14.516696, "longitude": 121.05752,
                "elevation": 13.0, "timezone": "Asia/Manila"}
    raw = json.dumps(response, separators=(",", ":")).encode()
    cell = {
        "revision": "openmeteo_ifs9_o1280_source_cell_v1",
        "static_hsurf_sha256": "fixture-static-hash", "selected_flat_index": 491,
        "selected_grid_lat": response["latitude"], "selected_grid_lon": response["longitude"],
        "raw_grid_elevation_m": -7.0, "effective_grid_elevation_m": 13.0,
        "target_dem_elevation_m": 13.0, "cell_is_sea": False,
        "cell_is_center": True, "nearby_sea": False,
    }
    monkeypatch.setattr(transport, "source_cell_geometry_proof", lambda **_kwargs: dict(cell))
    precision = dl._precision_metadata("Manila", "2026-09-27", anchor_sigma_c=3.0,
                                       raw_payload_bytes=raw)
    assert precision["station_id"] == "RPLL"
    assert precision["station_elevation_m"] is None  # 22.9 is airport reference, not measurement ground
    assert precision["source_geometry_proof"]["station_ground_proof"]["status"] == "UNPROVEN"
    rejected = evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        OpenMeteoIfs9PrecisionMetadata(**precision), raw_payload_bytes=raw,
    )
    assert rejected.status == "BLOCK"
    assert "OM9_STATION_GROUND_PROOF_UNPROVEN" in rejected.reason_codes
    assert precision["grid_elevation_m"] == -7.0
    assert precision["nearest_grid_lat"] == response["latitude"]
    assert precision["nearest_grid_lon"] == response["longitude"]
    assert precision["nearest_grid_distance_km"] > 0.0
    assert precision["source_geometry_proof"]["target_dem_elevation_m"] == 13.0
    assert precision["source_geometry_proof"]["raw_payload_sha256"] == hashlib.sha256(raw).hexdigest()

    altered = {**response, "latitude": float(requested.lat) + 0.2}
    with pytest.raises(ValueError, match="raw response grid differs"):
        dl._precision_metadata("Manila", "2026-09-27", anchor_sigma_c=3.0,
                               raw_payload_bytes=json.dumps(altered).encode())


def test_source_geometry_static_rewrite_cannot_reuse_cached_proof(monkeypatch, tmp_path) -> None:
    from types import SimpleNamespace
    import src.data.openmeteo_ecmwf_ifs9_bucket_transport as transport

    static = tmp_path / "hsurf.om"
    static.write_bytes(b"first-field")
    monkeypatch.setattr(transport, "select_terrain_optimised_point", lambda *_args, **_kwargs: SimpleNamespace(
        flat_index=10, grid_latitude=14.5, grid_longitude_east=121.0,
        model_elevation_m=12.0, is_sea=False, is_center=True,
    ))
    monkeypatch.setattr(transport, "read_model_elevation", lambda *_args, **_kwargs: 12.0)
    monkeypatch.setattr(transport, "om_get_surrounding_gridpoints", lambda *_args: ((10,), (), ()))
    kwargs = dict(latitude=14.5, longitude=121.0, target_elevation_m=13.0,
                  local_cache=str(static))
    first = transport.source_cell_geometry_proof(**kwargs)
    static.write_bytes(b"later-field")
    later = transport.source_cell_geometry_proof(**kwargs)
    assert first["static_hsurf_sha256"] != later["static_hsurf_sha256"]
    assert first["selected_flat_index"] == later["selected_flat_index"]


def test_source_geometry_corrupt_static_direct_guard_returns_typed_block(
    monkeypatch, tmp_path,
) -> None:
    import src.data.openmeteo_ecmwf_ifs9_bucket_transport as transport
    real_source_cell = transport.source_cell_geometry_proof
    metadata, raw, *_ = _official_hko_precision(tmp_path, monkeypatch)
    malformed = tmp_path / "hsurf.om"
    malformed.write_bytes(b"not-an-om-file")
    monkeypatch.setattr(transport, "source_cell_geometry_proof", lambda **kwargs: real_source_cell(
        **kwargs, local_cache=str(malformed),
    ))
    result = evaluate_openmeteo_ecmwf_ifs9_precision_guard(metadata, raw_payload_bytes=raw)
    assert result.status == "BLOCK"
    assert "OM9_SOURCE_GEOMETRY_PROOF_UNAVAILABLE" in result.reason_codes


def test_source_geometry_ground_missing_then_normal_registry_reload_recovers(tmp_path, monkeypatch):
    import json
    import scripts.download_replacement_forecast_current_targets as dl
    metadata, raw, cell, registry, artifact, rows = _official_hko_precision(tmp_path, monkeypatch)
    legacy = _metadata(**{**metadata.__dict__, "source_geometry_proof": {
        key: value for key, value in metadata.source_geometry_proof.items() if key != "station_ground_proof"
    }})
    assert "OM9_STATION_GROUND_PROOF_UNPROVEN" in evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        legacy, raw_payload_bytes=raw,
    ).reason_codes
    missing = json.loads(json.dumps(rows))
    del missing["Hong Kong"]["station_ground_proof"]
    registry.write_text(json.dumps(missing))
    unavailable = OpenMeteoIfs9PrecisionMetadata(**dl._precision_metadata(
        "Hong Kong", "2026-09-29", anchor_sigma_c=3.0, raw_payload_bytes=raw,
    ))
    assert unavailable.station_elevation_m is None
    assert evaluate_openmeteo_ecmwf_ifs9_precision_guard(unavailable, raw_payload_bytes=raw).status == "BLOCK"
    registry.write_text(json.dumps(rows))
    recomputed = OpenMeteoIfs9PrecisionMetadata(**dl._precision_metadata(
        "Hong Kong", "2026-09-29", anchor_sigma_c=3.0, raw_payload_bytes=raw,
    ))
    assert evaluate_openmeteo_ecmwf_ifs9_precision_guard(recomputed, raw_payload_bytes=raw).status == "PASS"
    assert recomputed.source_geometry_proof["raw_payload_sha256"] == unavailable.source_geometry_proof["raw_payload_sha256"]


def test_source_geometry_frozen_ground_audit_is_not_newest_page_hash_gate(tmp_path, monkeypatch):
    import json
    metadata, raw, cell, registry, artifact, rows = _official_hko_precision(tmp_path, monkeypatch)
    artifact.write_bytes(artifact.read_bytes() + b"<!-- unrelated station metadata update -->")
    claim = rows["Hong Kong"]["station_ground_proof"]
    claim["body_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    registry.write_text(json.dumps(rows))
    assert evaluate_openmeteo_ecmwf_ifs9_precision_guard(metadata, raw_payload_bytes=raw).status == "PASS"
    artifact.write_bytes(b"not the recorded original entity")
    assert "OM9_STATION_GROUND_PROOF_UNPROVEN" in evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        metadata, raw_payload_bytes=raw,
    ).reason_codes
