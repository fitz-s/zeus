# Created: 2026-09-30
# Last reused/audited: 2026-09-30
# Lifecycle: created=2026-09-30; last_reviewed=2026-09-30; last_reused=2026-09-30
# Purpose: Antibody for the 2026-09-27 ifs9 anchor blackout of 7 cities.
# Reuse: Run for any change to Open-Meteo anchor cell/station certification.
# Authority basis: c1d7ebd53 certified the provider cell with a 1e-5 deg float64
#   equality although the API serialises Float32 coordinates (up to 1.8e-5 off),
#   and blocked every provider sea cell; NYC, Miami, Tokyo, Beijing, Panama City,
#   Sao Paulo (float drift) and Seoul (all-sea 3x3 box) lost every anchor from
#   cycle 2026-09-27T12Z while the skip never reached a health surface.
"""A legitimate provider cell certifies for every city; a failure is named in health."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.config import cities_by_name
import src.data.openmeteo_ecmwf_ifs9_bucket_transport as transport

# Live-shaped provider answers for the ECMWF IFS run of 2026-09-30T00Z
# (state/openmeteo_response_store.db), with the O1280 HSURF heights of the
# selected cell's 3x3 box (state/static/ecmwf_ifs_o1280_hsurf.om).
LIVE_CELLS = {
    # city: (response lat, lon, DEM elevation; {flat index: HSURF metres})
    "NYC": (40.808434, -73.89206, 2.0, {
        989206: 27.0, 989207: 19.0, 989208: -999.0, 992021: 22.0, 992022: 12.0,
        992023: 21.0, 994840: 16.0, 994841: 9.0, 994842: 19.0,
    }),
    "Seoul": (37.50439, 126.43143, 5.0, {
        1127514: -999.0, 1127515: -999.0, 1127516: -999.0, 1130520: -999.0,
        1130521: -999.0, 1130522: -999.0, 1133529: -999.0, 1133530: -999.0,
        1133531: -999.0,
    }),
}


@pytest.fixture
def live_surface(monkeypatch, tmp_path):
    """Replace only the HSURF file read; grid math, selection and guard stay real."""
    heights = {index: h for *_rest, box in LIVE_CELLS.values() for index, h in box.items()}
    static = tmp_path / "hsurf.om"
    static.write_bytes(b"live-shaped-surface")
    monkeypatch.setattr(transport, "HSURF_LOCAL_CACHE", str(static))
    monkeypatch.setattr(transport, "read_model_elevation", lambda index, **_kw: heights[index])
    real_proof = transport.source_cell_geometry_proof
    monkeypatch.setattr(
        transport, "source_cell_geometry_proof",
        lambda **kw: real_proof(**{**kw, "local_cache": str(static)}),
    )


def _response(city: str, target_date: str) -> bytes:
    import scripts.download_replacement_forecast_current_targets as dl

    lat, lon, dem, _box = LIVE_CELLS[city]
    payload = {
        "latitude": lat, "longitude": lon, "elevation": dem,
        "timezone": cities_by_name[city].timezone,
        "hourly_units": {"time": "iso8601", "temperature_2m": "°C"},
        "hourly": {
            "time": [f"{target_date}T{hour:02d}:00" for hour in range(24)],
            "temperature_2m": [20.0 + hour / 10 for hour in range(24)],
        },
    }
    scoped = dl._current_target_scoped_payload(
        payload, city=city, target_date=target_date, metric="high",
    )
    return (json.dumps(scoped, indent=2, sort_keys=True, default=str) + "\n").encode()


@pytest.mark.parametrize("city", sorted(LIVE_CELLS))
def test_dropped_city_live_provider_cell_certifies(live_surface, city) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    raw = _response(city, "2026-10-01")
    precision, reason, _retry = dl._current_target_source_geometry_check(
        city, "2026-10-01", raw, anchor_sigma_c=3.0,
    )
    assert reason is None and precision is not None
    lat, lon, _dem, _box = LIVE_CELLS[city]
    assert (precision["nearest_grid_lat"], precision["nearest_grid_lon"]) == (lat, lon)


def test_provider_sea_cell_is_served_at_sea_level(live_surface) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    precision, _reason, _retry = dl._current_target_source_geometry_check(
        "Seoul", "2026-10-01", _response("Seoul", "2026-10-01"), anchor_sigma_c=3.0,
    )
    assert precision["land_sea_mask"] == "sea"
    assert precision["grid_elevation_m"] == 0.0


def test_same_grid_cell_separates_float32_rounding_from_a_neighbour() -> None:
    # NYC: live response vs float64 recomputation of cell 992022 differ 1.5e-5.
    cell = transport.om_get_coordinates(992022)
    assert transport.same_grid_cell(40.808434, -73.89206, cell.grid_latitude, cell.grid_longitude_east)
    neighbour = transport.om_get_coordinates(992023)
    assert not transport.same_grid_cell(
        40.808434, -73.89206, neighbour.grid_latitude, neighbour.grid_longitude_east,
    )
    assert not transport.same_grid_cell(40.808434 + 0.2, -73.89206, cell.grid_latitude, cell.grid_longitude_east)
    assert not transport.same_grid_cell(float("nan"), -73.89206, cell.grid_latitude, cell.grid_longitude_east)


def test_certification_failure_is_a_named_latched_health_fault(tmp_path, monkeypatch) -> None:
    import scripts.download_replacement_forecast_current_targets as dl
    import src.observability.scheduler_health as health
    from src.control.live_health import _forecast_pipeline_surface

    path = tmp_path / "scheduler_jobs_health.json"
    monkeypatch.setattr(health, "_SCHEDULER_HEALTH_PATH", path)

    def surface():
        return _forecast_pipeline_surface(json.loads(path.read_text()))

    reason = "OM9_SOURCE_GEOMETRY_PROOF_UNAVAILABLE:OM9 raw response grid differs"
    dl._publish_source_geometry_faults(
        [{"city": "NYC", "target_date": "2026-10-01", "metric": "high", "reason": reason},
         {"city": "NYC", "target_date": "2026-10-01", "metric": "low", "reason": reason},
         {"city": "Tokyo", "target_date": "2026-10-01", "metric": "high",
          "reason": "anchor payload has no finite target-day sample"}],
        certified_cities={"London"},
    )
    entry = json.loads(path.read_text())[dl.SOURCE_GEOMETRY_HEALTH_JOB]
    assert entry["status"] == "FAILED"
    assert set(entry["open_faults"]) == {"NYC"}
    assert "NYC=" + reason in entry["last_failure_reason"]
    assert surface()["ok"] is False
    assert "openmeteo_ifs9_source_geometry" in surface()["issue"]

    # A pass that never reached NYC cannot clear it.
    dl._publish_source_geometry_faults([], certified_cities={"London"})
    assert surface()["ok"] is False
    since = json.loads(path.read_text())[dl.SOURCE_GEOMETRY_HEALTH_JOB]["open_faults"]["NYC"]["since"]

    # A family failing while a sibling certifies keeps the city open, same onset.
    dl._publish_source_geometry_faults(
        [{"city": "NYC", "target_date": "2026-10-02", "metric": "high", "reason": reason}],
        certified_cities={"NYC"},
    )
    faults = json.loads(path.read_text())[dl.SOURCE_GEOMETRY_HEALTH_JOB]["open_faults"]
    assert faults["NYC"]["since"] == since

    dl._publish_source_geometry_faults([], certified_cities={"NYC"})
    entry = json.loads(path.read_text())[dl.SOURCE_GEOMETRY_HEALTH_JOB]
    assert entry["status"] == "OK" and entry["open_faults"] == {}
    assert surface()["ok"] is True


def test_downloader_publishes_geometry_fault_through_its_own_pass(tmp_path, monkeypatch) -> None:
    import scripts.download_replacement_forecast_current_targets as dl
    import src.observability.scheduler_health as health

    path = tmp_path / "scheduler_jobs_health.json"
    monkeypatch.setattr(health, "_SCHEDULER_HEALTH_PATH", path)
    monkeypatch.setattr(transport, "source_geometry_static_prerequisite_reason", lambda: None)
    monkeypatch.setattr(
        dl, "_current_target_source_geometry_check",
        lambda *_a, **_k: (None, "OM9 raw response grid differs from same-source static surface", True),
    )
    lat, lon, dem, _box = LIVE_CELLS["NYC"]
    payload = json.loads(_response("NYC", "2026-10-01"))
    monkeypatch.setattr(dl, "_single_runs_public_for_request", lambda *_args: False)
    monkeypatch.setattr(dl, "_fetch_meta_stamped_anchor_wave", lambda requests, **_kw: ({
        next(iter(requests)): (payload, {"openmeteo_endpoint": "standard_api_meta_stamped",
                                         "run_authority": "provider_meta_declared"},
                               datetime.now(timezone.utc)),
    }, {}))
    db = tmp_path / "forecast.db"
    result = dl.download_current_target_raw_inputs(
        forecast_db=db, output_dir=tmp_path / "raw",
        cycle=datetime(2026, 9, 30, 0, tzinfo=timezone.utc), limit=None, write_db=True,
        release_lag_hours=14.0, anchor_sigma_c=3.0,
        required_scopes=(("NYC", "2026-10-01", "high"),),
    )
    assert result["written_manifest_count"] == 0
    entry = json.loads(path.read_text())[dl.SOURCE_GEOMETRY_HEALTH_JOB]
    assert entry["status"] == "FAILED" and set(entry["open_faults"]) == {"NYC"}
