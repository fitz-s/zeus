# Lifecycle: created=2026-06-08; last_reviewed=2026-09-30; last_reused=2026-09-30
# Purpose: BLOCKER 4 — cell_selection and elevation/downscaling are first-class product identity and must be persisted alongside every raw_model_forecasts row.
# Reuse: Run with pytest; update if product-identity columns or cell_selection/elevation handling in the BAYES_PRECISION_FUSION downloader changes.
# Created: 2026-06-08
# Last reused or audited: 2026-09-30
# Authority basis: BAYES_PRECISION_FUSION_SPEC.md §6 F1 + Fitz Constraint #4. Open-Meteo's cell_selection
#   (nearest vs land vs sea) and elevation/downscaling materially change the returned 2m
#   temperature: the SAME lat/lon with cell_selection=land vs nearest can pick a DIFFERENT
#   grid cell -> a different physical product. These are first-class product identity, not
#   cosmetic params, and MUST be persisted so a stored value proves which cell produced it.
"""BLOCKER 4 — cell_selection + elevation/downscaling are persisted product identity.

The download writer must record cell_selection, elevation_param, downscaling_policy, and
model_domain_hash on every raw_model_forecasts row. Two captures of the same model/city/date at
DIFFERENT cell_selection are DIFFERENT products and must be distinguishable (different
model_domain_hash) so a residual history never mixes two physical cells.
"""
from __future__ import annotations

import sqlite3
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager
from functools import lru_cache

import pytest

from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema


@lru_cache(maxsize=4)
def _controlled_native_static_bytes(model):
    """Real OM encoding on the official served grid; controlled fixture, not live geography."""
    import tempfile
    import numpy as np
    from omfiles import OmFileWriter
    from src.data.openmeteo_model_surface import _profile
    profile = _profile(model)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "HSURF.om"
        writer = OmFileWriter(str(path))
        root = writer.write_array(np.full((profile["ny"], profile["nx"]), 6.0, dtype=np.float32), chunks=(20,20), name="HSURF")
        writer.close(root)
        return path.read_bytes()


def _selected_test_cell(model, latitude, longitude):
    from src.data.openmeteo_model_surface import _profile, _float32
    profile = _profile(model)
    def cell(value, origin, step):
        index = int((_float32(value) - _float32(origin)) / _float32(step) + .5)
        return _float32(_float32(origin) + _float32(_float32(index) * _float32(step)))
    return cell(latitude, profile["lat_min"], profile["dy"]), cell(longitude, profile["lon_min"], profile["dx"])


@pytest.fixture(autouse=True)
def _controlled_model_static_transport(tmp_path, monkeypatch):
    from src.data import openmeteo_model_surface as surface
    from src.data import bayes_precision_fusion_download as dl
    monkeypatch.setattr(surface, "_cache_root", lambda: tmp_path / "static")
    monkeypatch.setattr(surface, "_now", lambda: dl.datetime.now(UTC))
    @contextmanager
    def stream(method, url, **kwargs):
        domain = url.split("/data/")[1].split("/")[0]
        model = next(model for model, definition in surface._PROFILES.items() if definition[0] == domain)
        body = _controlled_native_static_bytes(model)
        headers = {"etag": '"controlled-fixture"', "last-modified": "Mon, 01 Jan 2024 00:00:00 GMT", "content-length":str(len(body))}
        yield SimpleNamespace(status_code=200, headers=headers, iter_raw=lambda **_kwargs:iter((body,)))
    monkeypatch.setattr(surface.httpx, "stream", stream)


def _forecast_db(tmp_path: Path) -> Path:
    db = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(str(db))
    ensure_replacement_forecast_live_schema(conn)
    conn.commit()
    conn.close()
    return db


def _target():
    from src.data.bayes_precision_fusion_download import BayesPrecisionFusionDownloadTarget
    return BayesPrecisionFusionDownloadTarget(city="Paris", metric="high", target_date="2026-06-09",
                             lead_days=1, latitude=48.967, longitude=2.428,
                             timezone_name="Europe/Paris")


def test_cell_selection_and_elevation_persisted(tmp_path) -> None:
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)
    download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=datetime(2026, 6, 8, tzinfo=UTC), targets=[_target()],
        single_runs_fetch=lambda **k: 20.0, previous_runs_fetch=lambda **k: 19.5,
    )
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT cell_selection, elevation_param, downscaling_policy, model_domain_hash,
                  coverage_status
           FROM raw_model_forecasts"""
    ).fetchall()
    conn.close()
    assert rows
    for r in rows:
        assert r["cell_selection"], "cell_selection must be recorded"
        assert r["elevation_param"], "elevation_param must be recorded"
        assert r["downscaling_policy"], "downscaling_policy must be recorded"
        assert r["model_domain_hash"], "model_domain_hash must be recorded"
        assert r["coverage_status"], "coverage_status must be recorded"


def test_different_cell_selection_yields_different_model_domain_hash(tmp_path) -> None:
    """The model_domain_hash binds (provider, model_name, cell_selection, elevation_param,
    downscaling_policy, endpoint_mode). Changing cell_selection changes the hash -> two
    physical cells are never conflated under one identity."""
    from src.data.bayes_precision_fusion_download import _model_domain_hash

    base = dict(
        provider="open-meteo", model_name="gfs_global", cell_selection="nearest",
        elevation_param="requested", downscaling_policy="none",
        endpoint_mode="previous_runs",
    )
    h_nearest = _model_domain_hash(**base)
    h_land = _model_domain_hash(**{**base, "cell_selection": "land"})
    h_elev = _model_domain_hash(**{**base, "elevation_param": "30"})
    assert h_nearest != h_land, "cell_selection must change the domain hash"
    assert h_nearest != h_elev, "elevation must change the domain hash"
    # Deterministic + stable for the same inputs.
    assert h_nearest == _model_domain_hash(**base)


def _current_rows(tmp_path, monkeypatch, *, metric="high"):
    from src.data import bayes_precision_fusion_download as dl

    target = replace(_target(), metric=metric)
    monkeypatch.setattr("src.config.state_path", lambda filename: tmp_path / "state" / filename)
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {
        target.city: SimpleNamespace(
            name=target.city, lat=target.latitude, lon=target.longitude,
            timezone=target.timezone_name,
        ),
    })
    db = _forecast_db(tmp_path)
    cycle = datetime(2026, 6, 8, tzinfo=UTC)
    _download_time(monkeypatch, dl, cycle.replace(hour=4))
    _mock_single_model_http(monkeypatch, dl, value=20.0)
    dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=cycle, targets=[target],
        models=("icon_global", "ukmo_global_deterministic_10km"),
        include_previous_runs=False, prune_after=False,
    )
    conn = sqlite3.connect(db)
    return conn, target, cycle


def _mock_single_model_http(monkeypatch, dl, *, value, network=False, hour_count=24):
    dl._SINGLE_RUNS_PAYLOAD_CACHE.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEX.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEXED_KEYS.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_RECORDED_AT.clear()

    def fetch(url, params, **kwargs):
        assert "," not in params["models"]
        day = datetime(2026, 6, 9 if hour_count == 24 else 8)
        latitude, longitude = _selected_test_cell(params["models"], float(params["latitude"]), float(params["longitude"]))
        payload = {"latitude": latitude, "longitude": longitude, "elevation": 123,
            "timezone": "Europe/Paris", "utc_offset_seconds":7200, "hourly_units": {"temperature_2m": "°C"},
            "hourly": {"time": [(day + timedelta(hours=i)).isoformat(timespec="minutes") for i in range(hour_count)],
                       "temperature_2m": [value] * hour_count}}
        body = (json.dumps(payload, indent=2) + "\n").encode()
        fetched_at = dl.datetime.now(UTC).timestamp()
        kwargs["capture_entity_body"](body, fetched_at)
        if network:
            kwargs["capture_network_response"](body, fetched_at, {"content-type": "application/json"})
        return json.loads(body)

    monkeypatch.setattr("src.data.openmeteo_client.fetch", fetch)


def _download_time(monkeypatch, module, when):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return when.astimezone(tz) if tz else when.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", Clock)


def _persist_exact_provider_body(conn, tmp_path, *, city, metric, target_date, model, cycle, captured, value, expected_written=1, network=False,
        payload_dates=None):
    """Real entity parser/artifact/raw writer fixture, with causal original clocks."""
    from src.config import runtime_cities_by_name
    from src.data import bayes_precision_fusion_download as dl
    from src.data.openmeteo_ecmwf_ifs9_anchor import SINGLE_RUNS_FORECAST_URL
    cfg = runtime_cities_by_name()[city]
    run = datetime.fromisoformat(cycle)
    stamp = datetime.fromisoformat(captured)
    target = dl.BayesPrecisionFusionDownloadTarget(city=city, metric=metric, target_date=target_date,
        lead_days=max(0, (datetime.fromisoformat(target_date).date() - run.date()).days),
        latitude=float(cfg.lat), longitude=float(cfg.lon), timezone_name=str(cfg.timezone))
    params = {"latitude": target.latitude, "longitude": target.longitude,
        "timezone": target.timezone_name, "models": dl.OPENMETEO_MODEL_IDS.get(model, model),
        "hourly":"temperature_2m", "temperature_unit":"celsius", "cell_selection":"land",
        "run":run.replace(tzinfo=None).isoformat()}
    day = datetime.fromisoformat(target_date)
    from zoneinfo import ZoneInfo
    offset = int(day.replace(tzinfo=ZoneInfo(target.timezone_name)).utcoffset().total_seconds())
    payload = {"latitude":target.latitude, "longitude":target.longitude, "elevation":45,
        "timezone":target.timezone_name, "utc_offset_seconds":offset, "hourly_units":{"temperature_2m":"°C"},
        "hourly":{"time":[(datetime.fromisoformat(body_day)+timedelta(hours=i)).isoformat(timespec="minutes")
            for body_day in (payload_dates or (target_date,)) for i in range(24)],
                  "temperature_2m":[value]*(24*len(payload_dates or (target_date,)))}}
    body = (json.dumps(payload, indent=2)+"\n").encode()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("src.config.state_path", lambda filename: Path(tmp_path)/"state"/filename)
        _download_time(patch, dl, stamp)
        from src.data.openmeteo_model_surface import ensure_model_surface
        ensure_model_surface(model)
        selected_lat, selected_lon = _selected_test_cell(model, target.latitude, target.longitude)
        payload.update(latitude=selected_lat, longitude=selected_lon)
        body = (json.dumps(payload, indent=2)+"\n").encode()
        bound = dl._bind_physical_response(json.loads(body), model=model, url=SINGLE_RUNS_FORECAST_URL,
            params=params, run=run, captures=[(body, stamp.timestamp())],
            network_captures=[(body, stamp.timestamp(), {"content-type": "application/json"})] if network else ())
        row = dict(model=model,city=city,metric=metric,target_date=target_date,source_cycle_time=cycle,
            source_available_at=captured,captured_at=captured,lead_days=target.lead_days,
            forecast_value_c=value,endpoint="single_runs",_physical_response=bound[dl._BATCH_PHYSICAL_RESPONSE_KEY],
            **dl._bayes_precision_fusion_product_identity(model,"single_runs",target))
        assert dl._persist_rows(conn, [row]) == expected_written
    return int(row["artifact_id"])


def _normal_ifs9_owned_product(tmp_path, monkeypatch, metric, *, legacy=False):
    """Ordinary body/raw writer with actual full O1280 decoding, not a proof stub."""
    from tests.test_openmeteo_ecmwf_ifs9_bucket_transport import _actual_o1280_static_fixture
    from src.config import runtime_cities_by_name
    from src.data import bayes_precision_fusion_download as dl
    from src.data.openmeteo_ecmwf_ifs9_anchor import SINGLE_RUNS_FORECAST_URL
    from src.data.replacement_current_value_serving import read_current_instrument_values

    transport, path, data, write, clock, _ = _actual_o1280_static_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(transport, "HSURF_LOCAL_CACHE", str(path))
    monkeypatch.setattr("src.config.state_path", lambda filename: tmp_path / "state" / filename)
    city = runtime_cities_by_name()["Hong Kong"]
    points, _, center = transport.om_get_surrounding_gridpoints(float(city.lat), float(city.lon))
    selected = transport.om_get_coordinates(points[center])
    run = datetime(2026, 9, 30, tzinfo=UTC)
    target = dl.BayesPrecisionFusionDownloadTarget(city="Hong Kong", metric=metric,
        target_date="2026-10-01", lead_days=1, latitude=float(city.lat), longitude=float(city.lon),
        timezone_name=str(city.timezone))
    params = {"latitude": target.latitude, "longitude": target.longitude, "timezone": target.timezone_name,
        "models": "ecmwf_ifs", "hourly": "temperature_2m", "temperature_unit": "celsius",
        "cell_selection": "land", "run": run.replace(tzinfo=None).isoformat()}
    day = datetime(2026, 10, 1)
    payload = {"latitude": selected.grid_latitude, "longitude": selected.grid_longitude_east, "elevation": 32.,
        "timezone": target.timezone_name, "utc_offset_seconds": 28800,
        "hourly_units": {"temperature_2m": "°C"},
        "hourly": {"time": [(day + timedelta(hours=i)).isoformat(timespec="minutes") for i in range(24)],
                   "temperature_2m": [20.] * 24}}
    db = _forecast_db(tmp_path)
    conn = sqlite3.connect(db)
    def persist(expected_written):
        _download_time(monkeypatch, dl, clock[0])
        body = (json.dumps(payload, indent=2) + "\n").encode()
        bound = dl._bind_physical_response(json.loads(body), model="ecmwf_ifs", url=SINGLE_RUNS_FORECAST_URL,
            params=params, run=run, captures=[(body, clock[0].timestamp())],
            network_captures=[(body, clock[0].timestamp(), {"content-type": "application/json"})])
        row = dict(model="ecmwf_ifs", city=target.city, metric=metric, target_date=target.target_date,
            source_cycle_time=run.isoformat(), source_available_at=clock[0].isoformat(), captured_at=clock[0].isoformat(),
            lead_days=1, forecast_value_c=20., endpoint="single_runs",
            _physical_response=bound[dl._BATCH_PHYSICAL_RESPONSE_KEY],
            **dl._bayes_precision_fusion_product_identity("ecmwf_ifs", "single_runs", target))
        assert dl._persist_rows(conn, [row]) == expected_written
        conn.commit()
        return read_current_instrument_values(conn, city=target.city, metric=metric, target_date=target.target_date,
            source_cycle_time_iso=run.isoformat(), decision_time_iso=(clock[0] + timedelta(minutes=1)).isoformat())["ecmwf_ifs"]
    persist.payload = payload
    if legacy:
        _download_time(monkeypatch, dl, clock[0] - timedelta(minutes=5))
        identity = dl._bayes_precision_fusion_product_identity("ecmwf_ifs", "single_runs", target)
        identity.update(elevation_param="requested", downscaling_policy="none",
            model_domain_hash=dl._model_domain_hash(provider=dl.OPENMETEO_PROVIDER, model_name="ecmwf_ifs",
                cell_selection="land", elevation_param="requested", downscaling_policy="none", endpoint_mode="single_runs"))
        old = (clock[0] - timedelta(minutes=5)).isoformat()
        assert dl._persist_rows(conn, [dict(model="ecmwf_ifs", city=target.city, metric=metric,
            target_date=target.target_date, source_cycle_time=run.isoformat(), source_available_at=old,
            captured_at=old, lead_days=1, forecast_value_c=20., endpoint="single_runs", **identity)]) == 1
        conn.commit()
    served = persist(0 if legacy else 1)
    return conn, served, persist, data, write, clock


@pytest.mark.parametrize("metric", ("high", "low"))
def test_normal_float32_ifs9_cell_producer_guard_and_frozen_reader_keep_native_identity(tmp_path, monkeypatch, metric):
    """Whole controlled native OM/ordinary HTTP producer, not field weather evidence."""
    from copy import deepcopy
    from src.config import runtime_cities_by_name
    from tests.test_config import _official_us_ground_registry
    from tests.test_openmeteo_ecmwf_ifs9_bucket_transport import _actual_o1280_static_fixture
    from src.data import bayes_precision_fusion_download as dl, station_ground_evidence as ground
    from src.data.replacement_current_value_serving import read_current_instrument_values, frozen_ifs9_response_has_authority, provider_geometry_projection
    from src.data.openmeteo_ecmwf_ifs9_precision_guard import OpenMeteoIfs9PrecisionMetadata, evaluate_openmeteo_ecmwf_ifs9_precision_guard
    from src.data.openmeteo_ecmwf_ifs9_anchor import SINGLE_RUNS_FORECAST_URL
    from scripts.download_replacement_forecast_current_targets import _precision_metadata

    _official_us_ground_registry(tmp_path, monkeypatch, "NYC")
    transport, path, _, _, clock, _ = _actual_o1280_static_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(transport, "HSURF_LOCAL_CACHE", str(path))
    monkeypatch.setattr(ground, "_store_root", lambda:tmp_path/"ground")
    monkeypatch.setattr("src.config.state_path", lambda filename:tmp_path/"state"/filename)
    _download_time(monkeypatch, ground, clock[0])
    _download_time(monkeypatch, dl, clock[0])
    city = runtime_cities_by_name()["NYC"]
    point = transport.select_terrain_optimised_point(city.lat, city.lon, 2.,
        read_elevation=lambda index:32.)
    assert point.flat_index == 992022
    canonical = transport.capture_source_cell_geometry_proof(latitude=point.grid_latitude,
        longitude=point.grid_longitude_east,target_elevation_m=2.,
        requested_latitude=city.lat,requested_longitude=city.lon)
    run = clock[0].replace(hour=0)
    cut = clock[0]+timedelta(minutes=1)
    payload = {"latitude":40.808434,"longitude":-73.89206,"elevation":2.,
        "timezone":city.timezone,"utc_offset_seconds":-14400,
        "hourly_units":{"temperature_2m":"°C"},
        "hourly":{"time":[f"2026-10-01T{hour:02d}:00" for hour in range(24)],
                  "temperature_2m":[20.]*24}}
    body = (json.dumps(payload,sort_keys=True)+"\n").encode()
    fetches=[]
    def fetch(url, params, **kwargs):
        fetches.append(dict(params))
        assert params["models"] == "ecmwf_ifs"
        assert float(params["latitude"]) == city.lat and float(params["longitude"]) == city.lon
        kwargs["capture_entity_body"](body,clock[0].timestamp())
        kwargs["capture_network_response"](body,clock[0].timestamp(),{"content-type":"application/json"})
        return json.loads(body)
    monkeypatch.setattr("src.data.openmeteo_client.fetch", fetch)
    db = _forecast_db(tmp_path)
    evidence = ground.archive_station_ground_evidence(db,["NYC"])["archived"]["NYC"]
    target=dl.BayesPrecisionFusionDownloadTarget(city="NYC",metric=metric,target_date="2026-10-01",
        lead_days=1,latitude=city.lat,longitude=city.lon,timezone_name=city.timezone)
    report=dl.download_bayes_precision_fusion_extra_raw_inputs(forecast_db=db,cycle=run,targets=[target],
        models=("ecmwf_ifs",),include_previous_runs=False,prune_after=False)
    assert len(fetches)==1,report
    conn=sqlite3.connect(db)
    served=read_current_instrument_values(conn,city="NYC",metric=metric,target_date=target.target_date,
        source_cycle_time_iso=run.isoformat(),decision_time_iso=cut.isoformat())["ecmwf_ifs"]
    proof=served.physical_response["source_cell_geometry_proof"]
    assert proof == canonical
    assert frozen_ifs9_response_has_authority(served.physical_response,
        provider_geometry_projection(served.physical_response),decision_at=cut)
    metadata=OpenMeteoIfs9PrecisionMetadata(**_precision_metadata("NYC",target.target_date,
        anchor_sigma_c=3.,raw_payload_bytes=body,analysis_at=cut))
    guard=evaluate_openmeteo_ecmwf_ifs9_precision_guard(metadata,raw_payload_bytes=body,
        decision_at=cut,station_ground_evidence=evidence)
    assert guard.status == "PASS",guard.reason_codes
    assert metadata.source_geometry_proof["selected_flat_index"] == 992022
    assert metadata.source_geometry_proof["selected_grid_lon"] == point.grid_longitude_east
    assert (metadata.nearest_grid_lat,metadata.nearest_grid_lon)==(payload["latitude"],payload["longitude"])
    before=conn.execute("SELECT * FROM raw_model_forecasts").fetchall()
    neighbor=transport.om_get_coordinates(992023)
    wrong={**payload,"latitude":neighbor.grid_latitude,"longitude":neighbor.grid_longitude_east}
    wrong_body=(json.dumps(wrong,sort_keys=True)+"\n").encode()
    with pytest.raises(ValueError,match="requested terrain selection"):
        _precision_metadata("NYC",target.target_date,anchor_sigma_c=3.,raw_payload_bytes=wrong_body,analysis_at=cut)
    with pytest.raises(ValueError,match="requested terrain selection"):
        dl._bind_physical_response(wrong,model="ecmwf_ifs",url=SINGLE_RUNS_FORECAST_URL,params=fetches[0],run=run,
            captures=[(wrong_body,clock[0].timestamp())],
            network_captures=[(wrong_body,clock[0].timestamp(),{"content-type":"application/json"})])
    bad=deepcopy(served.physical_response)
    bad["source_cell_geometry_proof"]["selected_flat_index"]=992023
    assert not frozen_ifs9_response_has_authority(bad,provider_geometry_projection(bad),decision_at=cut)
    assert conn.execute("SELECT * FROM raw_model_forecasts").fetchall()==before
    assert transport.capture_source_cell_geometry_proof(latitude=payload["latitude"],longitude=payload["longitude"],
        target_elevation_m=2.,requested_latitude=city.lat,requested_longitude=city.lon)==canonical
    conn.close()


def _normal_anchor_only_ifs9(tmp_path, monkeypatch, metric, *, city_name="Hong Kong", daemon_lane=None):
    """The current-target producer owns this anchor's actual body and frozen O1280."""
    from dataclasses import dataclass
    from tests.test_station_ground_evidence import _setup, _wmd_setup, _archive
    from tests.test_openmeteo_ecmwf_ifs9_bucket_transport import _actual_o1280_static_fixture
    import src.config as config
    import scripts.download_replacement_forecast_current_targets as producer
    from src.data.openmeteo_ecmwf_ifs9_anchor import OpenMeteoEcmwfIfs9AnchorRequest, build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest
    from src.data.raw_forecast_artifact_manifest import write_manifest_to_db
    from src.data.openmeteo_ecmwf_ifs9_precision_guard import OpenMeteoIfs9PrecisionMetadata
    from src.data.replacement_current_value_serving import _ARTIFACT_IDENTITY_JSON_SQL
    from src.data.replacement_forecast_materializer import _bind_provider_geometry_identity,_insert_anchor,ReplacementForecastMaterializeRequest
    from src.data.openmeteo_ecmwf_ifs9_anchor import extract_openmeteo_ecmwf_ifs9_localday_anchor
    from src.data.openmeteo_ecmwf_ifs9_precision_guard import evaluate_openmeteo_ecmwf_ifs9_precision_guard

    db, *_ = (_wmd_setup if city_name == "Paris" else _setup)(tmp_path, monkeypatch)
    if daemon_lane is not None:
        from src.ingest import forecast_live_daemon as daemon
        from src.data import station_ground_evidence as ground
        from src.state.schema.v2_schema import apply_canonical_schema
        with sqlite3.connect(db) as conn:
            apply_canonical_schema(conn, forecast_tables=True)
            if daemon_lane=="background":
                from src.data.replacement_forecast_readiness import SOURCE_ID as replacement_source
                # This explicitly unlicensed old row only selects the ordinary
                # background queue. It is never read as probability authority.
                conn.execute("""INSERT INTO forecast_posteriors(source_id,product_id,data_version,
                    city,target_date,temperature_metric,source_cycle_time,source_available_at,
                    computed_at,q_json,posterior_method) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (replacement_source,"test_only_old","old_unlicensed",city_name,"2026-10-01",metric,
                     "2026-09-29T12:00:00+00:00","2026-09-29T12:05:00+00:00",
                     "2026-09-29T13:00:00+00:00","{}","TEST_ONLY_UNLICENSED_QUEUE_ROLE"))
        seed_dir, requests = tmp_path/"bootstrap-seeds",tmp_path/"bootstrap-requests"
        seed_dir.mkdir()
        requests.mkdir()
        cut = datetime(2026,9,29,21,59,59,tzinfo=UTC)
        seed = {"city":city_name,"target_date":"2026-10-01","temperature_metric":metric,
            "computed_at":cut.isoformat(),"source_cycle_time":"2026-09-29T12:00:00+00:00",
            "baseline_source_run_id":"bootstrap-baseline","openmeteo_source_run_id":"bootstrap-anchor",
            "openmeteo_payload_json":str(tmp_path/"not-yet-acquired-anchor.json"),
            "precision_metadata_json":str(tmp_path/"not-yet-derived-precision.json"),
            "bins":[{"bin_id":"25C","lower_c":24.5,"upper_c":25.5}]}
        cfg={"forecast_db":db,"seed_dir":seed_dir,"request_dir":requests,
            "seed_processed_dir":tmp_path/"bootstrap-processed","seed_failed_dir":tmp_path/"bootstrap-failed",
            "processed_dir":tmp_path/"requests-processed","failed_dir":tmp_path/"requests-failed",
            "raw_manifest_dir":tmp_path/"manifests"}
        def tick():
            return (daemon._replacement_forecast_station_revision_fast_lane(cfg) if daemon_lane=="station"
                else daemon._replacement_forecast_materialize_lane(cfg,lane="background",seed_limit=1))
        name=(f"Hong_Kong.station-input-revision.{metric}.json" if daemon_lane=="station"
            else f"Hong_Kong.ordinary.{metric}.json")
        # A foreign declared namespace cannot bootstrap the actual queue DB.
        (seed_dir/name).write_text(json.dumps({**seed,"forecast_db":str(tmp_path/"foreign.db")}))
        tick()
        assert ground.read_current_station_ground_evidence(db,city=city_name,decision_at="2026-09-29T22:00:00Z") is None
        assert not list(requests.glob("*.json"))
        (seed_dir/name).write_text(json.dumps(seed))
        bootstrap_report=tick()
        entity=ground.read_current_station_ground_evidence(db,city=city_name,decision_at="2026-09-29T22:00:00Z")
        assert entity is not None,json.dumps(bootstrap_report)
        assert ground.read_current_station_ground_evidence(db,city=city_name,decision_at=cut) is None
        assert not list(requests.glob("*.json"))
        with sqlite3.connect(db) as conn:
            oldtuple=tuple(conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?",(entity["artifact_id"],)).fetchone())
        (seed_dir/name).write_text(json.dumps(seed))
        tick()
        with sqlite3.connect(db) as conn:
            assert tuple(conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?",(entity["artifact_id"],)).fetchone())==oldtuple
            if daemon_lane=="background":
                conn.execute("DELETE FROM forecast_posteriors WHERE posterior_method='TEST_ONLY_UNLICENSED_QUEUE_ROLE'")
    entity = _archive(db, city_name)
    transport, path, _, _, clock, _ = _actual_o1280_static_fixture(tmp_path, monkeypatch)
    # Fixture producer clock precedes actual SQLite INSERT; no old cut is
    # restamped, and the canonical tuple's independent recorded clock is read.
    clock[0] = datetime.now(UTC) - timedelta(minutes=1)
    monkeypatch.setattr(transport, "HSURF_LOCAL_CACHE", str(path))
    monkeypatch.setattr(config, "state_path", lambda filename: tmp_path / "state" / filename)
    city = config.runtime_cities_by_name()[city_name]
    points, _, center = transport.om_get_surrounding_gridpoints(city.lat, city.lon)
    selected = transport.om_get_coordinates(points[center])
    run = clock[0].replace(hour=clock[0].hour//6*6, minute=0, second=0, microsecond=0)
    target = clock[0].astimezone(__import__("zoneinfo").ZoneInfo(city.timezone)).date() + timedelta(days=1)
    payload = {"latitude":selected.grid_latitude,"longitude":selected.grid_longitude_east,"elevation":float(entity["facts"]["elevation_m"]),
        "timezone":city.timezone,"utc_offset_seconds":int(clock[0].astimezone(__import__("zoneinfo").ZoneInfo(city.timezone)).utcoffset().total_seconds()),
        "hourly_units":{"temperature_2m":"°C"},
        "hourly":{"time":[datetime.combine(target,datetime.min.time()).replace(hour=i).isoformat(timespec="minutes") for i in range(24)],
            "temperature_2m":[20.]*24}}
    raw = (json.dumps(payload,indent=2)+"\n").encode()
    raw_path=tmp_path/"anchor.json"
    raw_path.write_bytes(raw)
    metadata = OpenMeteoIfs9PrecisionMetadata(**producer._precision_metadata(
        city.name, target.isoformat(), anchor_sigma_c=3., raw_payload_bytes=raw))
    manifest = build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest(raw_path,
        request=OpenMeteoEcmwfIfs9AnchorRequest(city.lat,city.lon,run,city.timezone),metric=metric,
        source_available_at=clock[0],captured_at=clock[0],
        product_metadata={"city":city.name,"target_date":target.isoformat()})
    conn=sqlite3.connect(db)
    aid=write_manifest_to_db(conn,manifest)
    conn.commit()
    artifact={**json.loads(conn.execute(f"SELECT {_ARTIFACT_IDENTITY_JSON_SQL} FROM raw_forecast_artifacts a WHERE artifact_id=?",(aid,)).fetchone()[0]),"forecast_db":str(db)}
    cut=max(datetime.now(UTC),clock[0])+timedelta(minutes=1)
    @dataclass(frozen=True)
    class Shape:
        shape_hash: str="current-shape"
        provider_geometry_evidence: object=None
        provider_geometry_identity_hash: object=None
        provider_geometry_audit: object=None
    from src.data.replacement_current_value_serving import station_ground_target_coverage_for_city
    coverage = station_ground_target_coverage_for_city(entity,city=city.name,target_date=target,decision_at=cut) if city_name=="Paris" else None
    bound=_bind_provider_geometry_identity(Shape(),{},anchor_metadata=metadata,decision_at=cut,
        station_ground_evidence=entity,anchor_raw_artifact=artifact,station_ground_target_coverage=coverage)
    anchor_id=_insert_anchor(conn,ReplacementForecastMaterializeRequest(city=city.name,city_id=city.name,
        city_timezone=city.timezone,target_date=target,temperature_metric=metric,
        baseline_source_run_id="unit-anchor-only-not-q-authority",baseline_data_version="unit-only",
        baseline_source_available_at=clock[0],openmeteo_anchor=extract_openmeteo_ecmwf_ifs9_localday_anchor(
            payload,city_timezone=city.timezone,target_local_date=target,source_cycle_time=run,require_full_localday=True),
        openmeteo_source_run_id=None,openmeteo_source_available_at=clock[0],bins=[],source_cycle_time=run,computed_at=cut,
        anchor_artifact_id=aid,openmeteo_precision_guard=evaluate_openmeteo_ecmwf_ifs9_precision_guard(
            metadata,raw_payload_bytes=raw,decision_at=cut),openmeteo_raw_payload_bytes=raw),metric=metric)
    conn.commit()
    conn.close()
    scope={"city":city.name,"target_date":target.isoformat(),"metric":metric,
        "expected_anchor_artifact_id":aid,"anchor_id":anchor_id,"forecast_db":db}
    return bound,raw_path,cut,scope


def _normal_localproof_recovery(tmp_path, monkeypatch, metric, *, daemon_lane=None):
    import scripts.download_replacement_forecast_current_targets as producer
    from src.config import runtime_cities_by_name
    from src.data.raw_forecast_artifact_manifest import write_manifest_to_db, read_anchor_local_proof
    from src.data.openmeteo_ecmwf_ifs9_anchor import build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest

    bound, source_path, _, scope = _normal_anchor_only_ifs9(tmp_path, monkeypatch, metric, daemon_lane=daemon_lane)
    from src.data import openmeteo_ecmwf_ifs9_bucket_transport as transport
    prerequisite = transport.source_geometry_static_prerequisite_reason
    # The prerequisite's definition-time default names the live-relative file.
    # Keep its real decoder, supplying this test's actual whole O1280 entity.
    monkeypatch.setattr(transport, "source_geometry_static_prerequisite_reason",
        lambda: prerequisite(local_cache=transport.HSURF_LOCAL_CACHE))
    original_anchor = bound.provider_geometry_audit["anchor_raw_artifact"]
    cycle = datetime.fromisoformat(original_anchor["source_cycle_time"])
    city = runtime_cities_by_name()[scope["city"]]
    output = tmp_path / "normal-anchor-inputs"
    raw_dir = output / cycle.strftime("%Y%m%dT%H%M%SZ")
    raw_dir.mkdir(parents=True)
    owned_path = raw_dir / f"openmeteo_{producer._safe_name(city.name)}_{scope['target_date']}_{metric}_{cycle.strftime('%Y%m%dT%H%M%SZ')}.json"
    producer._write_json(owned_path, producer._current_target_scoped_payload(
        json.loads(source_path.read_bytes()), city=city.name, target_date=scope["target_date"], metric=metric,
    ))
    old_path = tmp_path / "first-owned-body.json"
    old_path.write_bytes(owned_path.read_bytes())
    request = producer.build_anchor_request(latitude=city.lat, longitude=city.lon, run=cycle,
        timezone_name=city.timezone, forecast_hours=120, past_hours=producer.CURRENT_RUN_CONTEXT_HOURS)
    original = build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest(old_path, request=request, metric=metric,
        source_available_at=original_anchor["source_available_at"], captured_at=original_anchor["captured_at"],
        product_metadata={"city":city.name,"target_date":scope["target_date"],
            "openmeteo_payload_json":str(old_path),"precision_metadata_json":str(tmp_path/"missing-precision.json")})
    with sqlite3.connect(scope["forecast_db"]) as conn:
        aid = write_manifest_to_db(conn, original)
        conn.commit()
        original_row = tuple(conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (aid,)).fetchone())
    old_path.unlink()  # A real same-SHA owned copy remains; do not repair this historical path/row.
    before = datetime.now(UTC)
    from src.data.replacement_forecast_production import _critical_scopes_missing_current_anchor
    actual_family = (city.name, scope["target_date"], metric)
    assert _critical_scopes_missing_current_anchor(scope["forecast_db"], [actual_family], cycle, decision_time=before) == (actual_family,)
    from src.data.replacement_forecast_current_target_plan import _load_openmeteo_manifest_index, _openmeteo_manifest_coverage
    def actual_coverage(cut):
        with sqlite3.connect(scope["forecast_db"]) as conn:
            conn.row_factory = sqlite3.Row
            columns = {row[1] for row in conn.execute("PRAGMA table_info(raw_forecast_artifacts)")}
            identity = (original.source_id, original.product_id, original.data_version)
            index = _load_openmeteo_manifest_index(conn, raw_artifact_columns=columns,
                metadata_column="artifact_metadata_json", identities={identity}, cities={city.name}, decision_time=cut)
            return _openmeteo_manifest_coverage(index.get((identity[0], identity[2], city.name), ()),
                target_date=scope["target_date"], city_timezone=city.timezone,
                required_source_cycle_time=cycle.isoformat(), decision_time=cut)
    assert actual_coverage(before)[0] == 0
    monkeypatch.setattr("src.data.replacement_forecast_seed_discovery.held_position_family_priorities", lambda: {})
    def no_network(*args, **kwargs):
        raise AssertionError("normal same-byte local proof recovery must not fetch HTTP")
    monkeypatch.setattr(producer, "_resolve_anchor_payload", no_network)
    monkeypatch.setattr(producer, "_fetch_run_pinned_anchor_wave", no_network)
    monkeypatch.setattr(producer, "_fetch_meta_stamped_anchor_wave", no_network)
    args = dict(forecast_db=scope["forecast_db"], output_dir=output, cycle=cycle, limit=None,
        write_db=True,release_lag_hours=0.,anchor_sigma_c=3.,
        required_scopes=[(city.name,scope["target_date"],metric)],expand_metric_siblings=False)
    preserved = {}
    if daemon_lane is not None:
        legacy_precision = raw_dir/f"openmeteo_precision_{producer._safe_name(city.name)}_{scope['target_date']}_{metric}.json"
        legacy_manifest = output/f"{original.source_id}.{original.data_version}.{cycle.strftime('%Y%m%dT%H%M%SZ')}.{original.sha256[:12]}.{producer._safe_name(city.name)}.manifest.json"
        legacy_precision.write_bytes(b'{"old_transport_without_ground":true}\n')
        producer.write_manifest(original,legacy_manifest)
        preserved={path:path.read_bytes() for path in (legacy_precision,legacy_manifest,owned_path)}
    report = producer.download_current_target_raw_inputs(**args)
    assert all(path.read_bytes()==body for path,body in preserved.items())
    assert report["db_artifact_ids"] == [aid], json.dumps(report, default=str)
    assert report["downloaded"]["openmeteo_transport_fetch_count"] == 0
    assert len(report["local_proof_artifact_ids"]) == 1
    with sqlite3.connect(scope["forecast_db"]) as conn:
        assert tuple(conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (aid,)).fetchone()) == original_row
        assert read_anchor_local_proof(conn,aid,city=city.name,target_date=scope["target_date"],metric=metric,
            decision_at=before) is None
        evidence = read_anchor_local_proof(conn,aid,city=city.name,target_date=scope["target_date"],metric=metric,
            decision_at=datetime.now(UTC))
        assert evidence is not None and evidence.proof_artifact_id == report["local_proof_artifact_ids"][0]
        assert evidence.original_body_artifact["artifact_path"] == str(old_path)
        resolved_path = Path(evidence.owned_body["path"])
        assert resolved_path.parent == raw_dir and resolved_path.read_bytes() == owned_path.read_bytes()
        assert evidence.precision_metadata["source_geometry_proof"]["raw_payload_sha256"] == original.sha256
        assert not old_path.exists()
        from src.data.replacement_current_value_serving import _ARTIFACT_IDENTITY_JSON_SQL
        from src.data.replacement_forecast_cycle_policy import _anchor_ifs9_response_has_authority, anchor_local_proof_dependency
        from src.data.replacement_forecast_materializer import _bind_provider_geometry_identity
        from src.data.openmeteo_ecmwf_ifs9_precision_guard import OpenMeteoIfs9PrecisionMetadata
        from src.data.station_ground_evidence import forecast_db_from_connection
        namespace = forecast_db_from_connection(conn)
        artifact = {**json.loads(conn.execute(f"SELECT {_ARTIFACT_IDENTITY_JSON_SQL} FROM raw_forecast_artifacts a WHERE artifact_id=?",
            (aid,)).fetchone()[0]),"forecast_db":str(namespace)}
        new_cut = datetime.now(UTC)
        resolved = _bind_provider_geometry_identity(replace(bound,shape_hash="same-physical-input"),{},
            anchor_metadata=OpenMeteoIfs9PrecisionMetadata(**evidence.precision_metadata),decision_at=new_cut,
            station_ground_evidence=bound.provider_geometry_audit["anchor_station_ground"],
            anchor_raw_artifact=artifact,anchor_local_proof=anchor_local_proof_dependency(evidence,forecast_db=namespace))
        assert resolved.provider_geometry_identity_hash == bound.provider_geometry_identity_hash
        actual_scope = dict(city=city.name,target_date=scope["target_date"],metric=metric,
            expected_anchor_artifact_id=aid,request_anchor_artifact_id=aid,forecast_db=namespace)
        assert _anchor_ifs9_response_has_authority(resolved.provider_geometry_evidence,resolved.provider_geometry_audit,
            materialized_at=new_cut,**actual_scope)
        assert not _anchor_ifs9_response_has_authority(resolved.provider_geometry_evidence,resolved.provider_geometry_audit,
            materialized_at=before,**actual_scope)
        no_dependency = dict(resolved.provider_geometry_audit)
        no_dependency.pop("anchor_local_proof")
        assert not _anchor_ifs9_response_has_authority(resolved.provider_geometry_evidence,no_dependency,
            materialized_at=new_cut,**actual_scope)
        from src.data.replacement_input_hwm import _exact_consumed_anchor_artifact_cycle
        provenance = {"openmeteo_anchor_artifact_id": aid, "bayes_precision_fusion": {
            "current_evidence_shape": {"provider_geometry_audit": resolved.provider_geometry_audit}}}
        conn.row_factory = sqlite3.Row
        lag, consumed_cycle = _exact_consumed_anchor_artifact_cycle(conn, city=city.name,
            target_date=scope["target_date"], metric=metric, decision_time=new_cut,
            posterior_computed_at=new_cut, provenance=provenance)
        assert lag is None and consumed_cycle == cycle
        old_lag, _ = _exact_consumed_anchor_artifact_cycle(conn, city=city.name,
            target_date=scope["target_date"], metric=metric, decision_time=new_cut,
            posterior_computed_at=before, provenance=provenance)
        assert old_lag == "basis=anchor_local_proof_identity_unverifiable"
    assert actual_coverage(before)[0] == 0  # Late possession cannot revive the old seed.
    assert actual_coverage(datetime.now(UTC))[0] == 1
    assert _critical_scopes_missing_current_anchor(scope["forecast_db"], [actual_family], cycle, decision_time=before) == (actual_family,)
    assert _critical_scopes_missing_current_anchor(scope["forecast_db"], [actual_family], cycle, decision_time=datetime.now(UTC)) == ()
    assert _critical_scopes_missing_current_anchor(scope["forecast_db"], [actual_family], cycle, decision_time=None) is None
    again = producer.download_current_target_raw_inputs(**args)
    assert again["local_proof_artifact_ids"] == []
    assert again["reused_canonical_artifact_ids"] == [aid]
    assert again["downloaded"]["openmeteo_transport_fetch_count"] == 0
    assert _critical_scopes_missing_current_anchor(scope["forecast_db"], [actual_family], cycle, decision_time=datetime.now(UTC)) == ()
    return SimpleNamespace(city=city, scope=scope, cycle=cycle, output=output, original=original,
        aid=aid, original_row=original_row, before=before, report=report, bound=resolved, args=args)


@pytest.mark.parametrize("metric", ("high", "low"))
def test_normal_anchor_producer_appends_local_proof_for_same_bytes_without_rewriting_original(tmp_path, monkeypatch, metric):
    _normal_localproof_recovery(tmp_path, monkeypatch, metric)


@pytest.mark.parametrize("metric", ("high", "low"))
def test_anchor_preflight_keeps_ordinary_owned_body_when_no_local_proof_is_needed(tmp_path, monkeypatch, metric):
    """Normal manifest writer, real own precision; no local/HTTP receipt invented."""
    from src.data.replacement_forecast_production import _critical_scopes_missing_current_anchor
    from src.data.raw_forecast_artifact_manifest import write_manifest_to_db, read_anchor_local_proof
    from src.data.openmeteo_ecmwf_ifs9_anchor import build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest
    import scripts.download_replacement_forecast_current_targets as producer
    from src.config import runtime_cities_by_name
    bound, path, _, scope = _normal_anchor_only_ifs9(tmp_path,monkeypatch,metric)
    city = runtime_cities_by_name()[scope["city"]]
    original = bound.provider_geometry_audit["anchor_raw_artifact"]
    cycle = datetime.fromisoformat(original["source_cycle_time"])
    payload = producer._current_target_scoped_payload(json.loads(path.read_bytes()),city=city.name,
        target_date=scope["target_date"],metric=metric)
    raw = (json.dumps(payload,indent=2)+"\n").encode()
    owned = tmp_path/"ordinary-owned-body.json"
    owned.write_bytes(raw)
    cut = datetime.now(UTC)
    precision = producer._precision_metadata(city.name,scope["target_date"],anchor_sigma_c=3.,raw_payload_bytes=raw,analysis_at=cut)
    precision_path = tmp_path/"ordinary-precision.json"
    precision_path.write_text(json.dumps(precision,default=str))
    request = producer.build_anchor_request(latitude=city.lat,longitude=city.lon,run=cycle,
        timezone_name=city.timezone,forecast_hours=120,past_hours=producer.CURRENT_RUN_CONTEXT_HOURS)
    manifest = build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest(owned,request=request,metric=metric,
        source_available_at=cut,captured_at=cut,product_metadata={"city":city.name,"target_date":scope["target_date"],
            "openmeteo_payload_json":str(owned),"precision_metadata_json":str(precision_path)})
    with sqlite3.connect(scope["forecast_db"]) as conn:
        aid = write_manifest_to_db(conn,manifest)
        conn.commit()
        assert read_anchor_local_proof(conn,aid,city=city.name,target_date=scope["target_date"],metric=metric,
            decision_at=datetime.now(UTC)) is None
    family = (city.name,scope["target_date"],metric)
    assert _critical_scopes_missing_current_anchor(scope["forecast_db"],[family],cycle,decision_time=datetime.now(UTC)) == ()
    assert _critical_scopes_missing_current_anchor(scope["forecast_db"],[family],cycle,
        decision_time=manifest.captured_at-timedelta(microseconds=1)) == (family,)
    with sqlite3.connect(scope["forecast_db"]) as conn:
        recorded = datetime.fromisoformat(conn.execute(
            "SELECT recorded_at FROM raw_forecast_artifacts WHERE artifact_id=?", (aid,)).fetchone()[0])
    assert _critical_scopes_missing_current_anchor(scope["forecast_db"],[family],cycle,
        decision_time=recorded-timedelta(microseconds=1)) == (family,)
    from src.data.replacement_forecast_cycle_policy import replacement_readiness_expires_at
    assert _critical_scopes_missing_current_anchor(scope["forecast_db"],[family],cycle,
        decision_time=replacement_readiness_expires_at(cycle)) == (family,)


@pytest.mark.parametrize("variant", ("date_objects", "utc_z", "same_offset", "different_instant", "foreign_day", "naive", "unknown_type"))
def test_frozen_precision_identity_normalizes_only_explicit_same_date_and_instant(tmp_path, monkeypatch, variant):
    from src.data.replacement_forecast_cycle_policy import anchor_precision_metadata_identity
    from src.data.openmeteo_ecmwf_ifs9_precision_guard import OpenMeteoIfs9PrecisionMetadata
    from datetime import date
    bound,_,_,_ = _normal_anchor_only_ifs9(tmp_path,monkeypatch,"low")
    metadata = OpenMeteoIfs9PrecisionMetadata(**bound.provider_geometry_audit["anchor_precision_metadata"])
    start = datetime.fromisoformat(metadata.local_day_start_utc)
    changed = metadata
    if variant == "date_objects":
        changed = replace(metadata,target_local_date=date.fromisoformat(metadata.target_local_date),
            local_day_start_utc=start,local_day_end_utc=datetime.fromisoformat(metadata.local_day_end_utc))
    elif variant == "utc_z":
        changed = replace(metadata,local_day_start_utc=start.isoformat().replace("+00:00","Z"))
    elif variant == "same_offset":
        changed = replace(metadata,local_day_start_utc=start.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Hong_Kong")).isoformat())
    elif variant == "different_instant":
        changed = replace(metadata,local_day_start_utc=(start+timedelta(microseconds=1)).isoformat(),
            local_day_end_utc=(datetime.fromisoformat(metadata.local_day_end_utc)+timedelta(microseconds=1)).isoformat())
    elif variant == "foreign_day":
        changed = replace(metadata,target_local_date=(date.fromisoformat(metadata.target_local_date)+timedelta(days=1)).isoformat())
    elif variant == "naive":
        with pytest.raises(ValueError,match="timezone-aware"):
            replace(metadata,local_day_start_utc=start.replace(tzinfo=None))
        return
    else:
        changed = replace(metadata,target_local_date=SimpleNamespace())
    if variant in ("naive","unknown_type"):
        with pytest.raises(ValueError):
            anchor_precision_metadata_identity(changed)
    else:
        assert (anchor_precision_metadata_identity(metadata)==anchor_precision_metadata_identity(changed)) == (
            variant in ("date_objects","utc_z","same_offset"))


def _normal_owned_anchor_seed_public(tmp_path, monkeypatch, metric, missing_transport, capture_case=None, *, daemon_lane=None):
    """Real local acquisition/seed/public chain; controlled 51 ENS, not GRIB."""
    from src.data import bayes_precision_fusion_download as dl
    from tests.test_replacement_forecast_materializer import _low_revision_authority_conn, _bins, _BaselineBundle, _Evidence
    from src.data.replacement_forecast_source_run_identity import expected_replacement_dependency_identity_by_role
    from src.data.replacement_forecast_seed_discovery import discover_replacement_forecast_materialization_seeds
    from src.data.replacement_forecast_live_materialization_queue import _prepare_seed_requests_with_connection
    from src.data.replacement_forecast_materialization_request_builder import build_materialize_request_dataclass
    from src.data.replacement_forecast_materializer import materialize_replacement_forecast_live
    from src.data.replacement_forecast_bundle_reader import read_replacement_forecast_bundle, ReplacementForecastAuthorityPurpose
    from src.data.replacement_forecast_readiness import ReplacementForecastReadinessDecision
    from src.data.replacement_forecast_cycle_policy import replacement_readiness_expires_at
    from src.data.replacement_forecast_seed_discovery import held_position_family_priorities as real_held_priorities

    context = _normal_localproof_recovery(tmp_path, monkeypatch, metric, daemon_lane=daemon_lane)
    city, cycle, db = context.city, context.cycle, context.scope["forecast_db"]
    target = datetime.fromisoformat(context.scope["target_date"]).date()
    conn = _low_revision_authority_conn(db, include_legacy_provider_fixtures=False,
        include_retired_incumbent=False, city_name=city.name, target_date=target, source_cycle=cycle)
    conn.execute("UPDATE source_run_coverage SET city_id=?", (city.name.upper().replace(" ", "_"),))
    # Test-only controlled member input is built before any licensed posterior.
    # Both metrics use their real source roles, not a LOW shape relabeled at read.
    if metric == "high":
        expected = expected_replacement_dependency_identity_by_role(metric)["baseline_b0"]
        for table in ("source_run", "source_run_coverage", "ensemble_snapshots"):
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            replacements = {"temperature_metric":metric, "physical_quantity":expected.physical_quantity,
                "observation_field":expected.observation_field, "dataset_id":expected.data_version,
                "data_version":expected.data_version, "track":"mx2t6_high_short_horizon",
                "release_calendar_key":"ecmwf_open_data:mx2t6_high_short_horizon"}
            fields = {key:value for key,value in replacements.items() if key in columns}
            conn.execute(f"UPDATE {table} SET " + ",".join(f"{key}=?" for key in fields), tuple(fields.values()))
    for item in _bins():
        conn.execute("""INSERT INTO market_events(market_slug,city,target_date,temperature_metric,
            condition_id,token_id,range_label,range_low,range_high) VALUES(?,?,?,?,?,?,?,?,?)""",
            (f"controlled-localproof-{metric}-{item.bin_id}", city.name, target.isoformat(), metric,
                f"controlled-{item.bin_id}", f"controlled-{item.bin_id}", item.bin_id, item.lower_c, item.upper_c))
    conn.commit()
    http_calls=[]
    def fetch(url, params, **kwargs):
        if url.endswith("/meta.json"):
            return {"last_run_initialisation_time":cycle.timestamp(),
                "last_run_modification_time":(cycle+timedelta(minutes=1)).timestamp(),
                "last_run_availability_time":(cycle+timedelta(minutes=1)).timestamp()}
        http_calls.append(dict(params))
        selected = _selected_test_cell(params["models"], city.lat, city.lon)
        body = (json.dumps({"latitude":selected[0],"longitude":selected[1],"elevation":32.,
            "timezone":city.timezone,"utc_offset_seconds":28800,"hourly_units":{"temperature_2m":"°C"},
            "hourly":{"time":[f"{target.isoformat()}T{hour:02d}:00" for hour in range(24)],
                "temperature_2m":[22. if params["models"]=="icon_global" else 24.]*24}})+"\n").encode()
        capture = datetime.now(UTC).timestamp()
        kwargs["capture_entity_body"](body, capture)
        kwargs["capture_network_response"](body, capture, {"content-type":"application/json"})
        return json.loads(body)
    monkeypatch.setattr("src.data.openmeteo_client.fetch", fetch)
    downloaded = dl.download_bayes_precision_fusion_extra_raw_inputs(forecast_db=db,cycle=cycle,
        targets=[dl.BayesPrecisionFusionDownloadTarget(city=city.name,metric=metric,target_date=target.isoformat(),
            lead_days=1,latitude=city.lat,longitude=city.lon,timezone_name=city.timezone)],
        models=("icon_global","ukmo_global_deterministic_10km"),include_previous_runs=False,prune_after=False)
    if missing_transport is not None:
        import scripts.download_replacement_forecast_current_targets as producer
        from src.data.replacement_forecast_production import _critical_scopes_missing_current_anchor
        manifest_path = Path(context.report["written_manifests"][0])
        transport = json.loads(manifest_path.read_bytes())
        precision_path = Path(transport["product_metadata"]["precision_metadata_json"])
        lost = precision_path if missing_transport == "precision" else manifest_path
        lost.unlink()
        family = (city.name,target.isoformat(),metric)
        assert _critical_scopes_missing_current_anchor(db,[family],cycle,decision_time=datetime.now(UTC),
            raw_manifest_dir=context.output) == (family,)
        import src.data.replacement_forecast_production as production
        # Only the provider cycle probe is controlled; real market-root,
        # preflight, downloader and canonical reuse must reach restoration.
        monkeypatch.setattr(production,"_probe_resolved_available_cycle",lambda **kwargs: cycle)
        wrapped = production._download_replacement_forecast_current_targets_if_needed({
            "forecast_db":db,"raw_manifest_dir":context.output,"download_release_lag_hours":.001})
        assert wrapped["status"] != "CURRENT_TARGETS_ALREADY_COVERED", wrapped
        assert wrapped["reused_canonical_artifact_ids"] == [context.aid]
        assert wrapped["local_proof_artifact_ids"] == []
        assert wrapped["downloaded"]["openmeteo_transport_fetch_count"] == 0
        assert lost.is_file()
        assert _critical_scopes_missing_current_anchor(db,[family],cycle,decision_time=datetime.now(UTC),
            raw_manifest_dir=context.output) == ()
        repeated = producer.download_current_target_raw_inputs(**context.args)
        assert repeated["reused_canonical_artifact_ids"] == [context.aid]
        assert repeated["local_proof_artifact_ids"] == []
        assert repeated["downloaded"]["openmeteo_transport_fetch_count"] == 0
        already = production._download_replacement_forecast_current_targets_if_needed({
            "forecast_db":db,"raw_manifest_dir":context.output,"download_release_lag_hours":.001})
        assert already["status"] == "CURRENT_TARGETS_ALREADY_COVERED", already
    cut = datetime.now(UTC)
    from src.data.replacement_current_value_serving import read_current_instrument_values
    assert {"icon_global", "ukmo_global_deterministic_10km"} <= set(read_current_instrument_values(
        conn,city=city.name,metric=metric,target_date=target.isoformat(),source_cycle_time_iso=cycle.isoformat(),
        include_station_sources=True,decision_time_iso=cut.isoformat())), downloaded
    def normal_materialize(at, label):
        seed_dir, request_dir = tmp_path/f"seeds-{label}", tmp_path/f"requests-{label}"
        report = discover_replacement_forecast_materialization_seeds(forecast_db=db,raw_manifest_dir=context.output,
            seed_dir=seed_dir,request_dir=request_dir,computed_at=at,limit=1)
        assert report.discovered_count == 1, report
        seed = json.loads(next(seed_dir.glob("*.json")).read_text())
        assert seed["openmeteo_anchor_artifact_id"] == context.aid
        assert seed["openmeteo_source_available_at"] == context.original.source_available_at.isoformat()
        assert datetime.fromisoformat(seed["expires_at"]) <= replacement_readiness_expires_at(cycle)
        from src.data.replacement_forecast_live_materialization_queue import _queue_read_only_connection
        if daemon_lane == "station":
            from src.ingest import forecast_live_daemon as daemon
            filename=next(seed_dir.glob("*.json"))
            station_filename=filename.with_name(f"{filename.stem}.station-input-revision.fresh.json")
            filename.rename(station_filename)
            original_seed_bytes=station_filename.read_bytes()
            report=daemon._replacement_forecast_station_revision_fast_lane({"forecast_db":db,
                "seed_dir":seed_dir,"request_dir":request_dir,"seed_processed_dir":tmp_path/f"processed-{label}",
                "seed_failed_dir":tmp_path/f"failed-{label}"})
            processed,failed,reasons=report["seed_processed_files"],report["seed_failed_files"],report["reason_codes"]
            assert len(processed)==1 and not failed,json.dumps(report)
            assert Path(processed[0]).read_bytes()==original_seed_bytes,report
        else:
            with _queue_read_only_connection(db) as readonly:
                processed,failed,reasons = _prepare_seed_requests_with_connection(seed_dir=seed_dir,
                    seed_processed_dir=tmp_path/f"processed-{label}",seed_failed_dir=tmp_path/f"failed-{label}",request_dir=request_dir,
                    forecast_db=db,forecast_conn=readonly if daemon_lane else None,limit=1)
        assert len(processed)==1 and not failed, (processed,failed,reasons)
        request = build_materialize_request_dataclass(json.loads(next(request_dir.glob("*.json")).read_text()),base_dir=request_dir)
        assert request.computed_at == at
        result = materialize_replacement_forecast_live(conn,request)
        conn.commit()
        assert result.ok, result.reason_codes
        posterior = conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",(result.posterior_id,)).fetchone()
        cert = conn.execute("SELECT * FROM readiness_state WHERE readiness_id=?",(result.readiness_id,)).fetchone()
        readiness = ReplacementForecastReadinessDecision(readiness_id=cert["readiness_id"],status=cert["status"],
            reason_codes=tuple(json.loads(cert["reason_codes_json"])),dependency_json=json.loads(cert["dependency_json"]),
            provenance_json=json.loads(cert["provenance_json"]),expires_at=datetime.fromisoformat(cert["expires_at"]))
        return request,result,posterior,readiness
    request,result,posterior,readiness = normal_materialize(cut,"first")
    provenance = json.loads(posterior["provenance_json"])
    audit = provenance["bayes_precision_fusion"]["current_evidence_shape"]["provider_geometry_audit"]
    assert audit["anchor_local_proof"]["artifact_id"] == context.report["local_proof_artifact_ids"][0]
    assert tuple(conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?",(context.aid,)).fetchone()) == context.original_row
    def public(row, cert, at):
        return [read_replacement_forecast_bundle(conn,baseline_bundle=_BaselineBundle(_Evidence("new12")),
            readiness=cert,city=city.name,target_date=target,temperature_metric=metric,
            decision_time=at.isoformat(),current_bin_topology_hash=row["bin_topology_hash"],
            enforce_raw_input_hwm=True,authority_purpose=purpose) for purpose in ReplacementForecastAuthorityPurpose]
    for served in public(posterior,readiness,cut):
        assert served.ok, served.reason_code
        assert served.bundle.posterior_id == result.posterior_id

    if capture_case is not None:
        from src.data import replacement_forecast_production as production, openmeteo_model_updates as updates
        from src.data import source_clock_update_probe as probe, replacement_forecast_seed_discovery as discovery
        from src.data.replacement_current_value_serving import physical_capture_debt_reason, physical_source_proof_dependency
        from src.state import db as state_db
        damage,held=capture_case
        state=tmp_path/"capture-consumer-state"
        state.mkdir()
        # The per-test canonical DB factory owns this namespace. Refresh the
        # imported path adapter, not the held-role classifier or its result.
        monkeypatch.setattr(discovery,"_zeus_trade_db_path",state_db._zeus_trade_db_path)
        trades_path=discovery._zeus_trade_db_path()
        assert trades_path.resolve().is_relative_to(tmp_path.resolve())
        with sqlite3.connect(trades_path) as trades:
            trades.execute("""CREATE TABLE position_current(position_id TEXT PRIMARY KEY,
                city TEXT,target_date TEXT,temperature_metric TEXT,
                phase TEXT CHECK(phase IN ('pending_entry','active','day0_window','pending_exit')),
                shares REAL CHECK(shares>0),direction TEXT CHECK(direction IN ('buy_yes','buy_no')),token_id TEXT)""")
            if held:
                trades.execute("INSERT INTO position_current VALUES(?,?,?,?,'active',20.,'buy_yes',?)",
                    (f"private-held-{metric}",city.name,target.isoformat(),metric,f"controlled-{_bins()[0].bin_id}"))
                assert trades.execute("SELECT shares FROM position_current WHERE position_id=?",(f"private-held-{metric}",)).fetchone()[0]==20.
        monkeypatch.setattr(discovery,"held_position_family_priorities",real_held_priorities)
        assert real_held_priorities()==({(city.name,target.isoformat(),metric):1} if held else {})
        monkeypatch.setattr(updates,"_fetch_openmeteo",fetch)
        metadata_path=state/"updates.jsonl"
        updates.write_model_updates_jsonl(metadata_path,updates.fetch_model_updates(
            ("icon_global","ukmo_global_deterministic_10km"),max_workers=1))
        monkeypatch.setattr(probe,"DEFAULT_MODEL_UPDATES_JSONL",metadata_path)
        monkeypatch.setattr(dl,"BAYES_PRECISION_FUSION_EXTRA_MODELS",("icon_global","ukmo_global_deterministic_10km"))
        monkeypatch.setattr(dl,"BAYES_PRECISION_FUSION_CANDIDATE_ACCRUAL_MODELS",())
        serving=provenance["bayes_precision_fusion"]["current_value_serving"]
        old_proof=physical_source_proof_dependency(serving["ukmo_global_deterministic_10km"]["physical_response"])
        raw_id,body_id=conn.execute("SELECT raw_model_forecast_id,artifact_id FROM raw_model_forecasts WHERE model=? AND city=? AND target_date=? AND metric=?",
            ("ukmo_global_deterministic_10km",city.name,target.isoformat(),metric)).fetchone()
        raw_before=[tuple(row) for row in conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id")]
        receipt_id,receipt_path=conn.execute("SELECT artifact_id,artifact_path FROM raw_forecast_artifacts WHERE data_version='openmeteo_single_model_http_capture_receipt_v1'"
            " AND json_extract(artifact_metadata_json,'$.physical_http_capture_receipt.body_artifact_id')=? ORDER BY artifact_id DESC LIMIT 1",(body_id,)).fetchone()
        Path(receipt_path).unlink()
        if damage=="extreme":
            conn.execute("UPDATE raw_forecast_artifacts SET captured_at='unknown',source_available_at='unknown',recorded_at='unknown' WHERE artifact_id=?",(receipt_id,))
        conn.commit()
        assert not any(item.ok for item in public(posterior,readiness,datetime.now(UTC)))
        assert physical_capture_debt_reason(conn,raw_model_forecast_id=raw_id,decision_time_iso=datetime.now(UTC).isoformat())=="HTTP_CAPTURE_RECEIPT_MISSING"
        calls=len(http_calls)
        cfg={"forecast_db":db,"seed_dir":tmp_path/"normal-capture-seeds","raw_manifest_dir":context.output,
            "bpf_extra_rotation_state_path":tmp_path/"normal-capture-rotation.json"}
        captured=production._download_bayes_precision_fusion_extra_raw_inputs_if_needed(cfg,
            planning_cycle=cycle,max_wall_clock_seconds=10,include_previous_runs=False,prune_after=False)
        assert captured["physical_capture_recovered_raw_ids"]==(raw_id,),captured
        assert captured["written_row_count"]==0 and len(http_calls)==calls+1
        assert captured["committed_families"]==((city.name,target.isoformat(),metric),)
        assert [tuple(row) for row in conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id")]==raw_before
        reset_cut=datetime.now(UTC)
        reset_request,reset_result,reset_posterior,reset_readiness=normal_materialize(reset_cut,"http-reset")
        assert reset_result.posterior_id!=result.posterior_id
        reset_prov=json.loads(reset_posterior["provenance_json"])
        reset_shape=reset_prov["bayes_precision_fusion"]["current_evidence_shape"]
        new_proof=physical_source_proof_dependency(reset_prov["bayes_precision_fusion"]["current_value_serving"]["ukmo_global_deterministic_10km"]["physical_response"])
        assert new_proof!=old_proof and new_proof["capture_receipt_artifact_id"]!=receipt_id
        assert reset_shape["provider_geometry_identity_hash"]==provenance["bayes_precision_fusion"]["current_evidence_shape"]["provider_geometry_identity_hash"]
        assert reset_posterior["q_json"]==posterior["q_json"]
        assert reset_shape["provider_geometry_audit"]["anchor_local_proof"]==audit["anchor_local_proof"]
        assert not any(item.ok for item in public(posterior,readiness,cut))
        for served in public(reset_posterior,reset_readiness,reset_cut):
            assert served.ok,served.reason_code
            assert served.bundle.posterior_id==reset_result.posterior_id
        repeated=production._download_bayes_precision_fusion_extra_raw_inputs_if_needed(cfg,
            planning_cycle=cycle,max_wall_clock_seconds=10,include_previous_runs=False,prune_after=False)
        assert len(http_calls)==calls+1 and not repeated.get("physical_capture_recovered_raw_ids"),repeated
        assert [tuple(row) for row in conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id")]==raw_before
        assert tuple(conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?",(context.aid,)).fetchone())==context.original_row
        expiry=replacement_readiness_expires_at(cycle)
        assert reset_readiness.expires_at<=expiry
        count=conn.execute("SELECT count(*) FROM forecast_posteriors").fetchone()[0]
        stale=materialize_replacement_forecast_live(conn,replace(reset_request,
            computed_at=expiry+timedelta(microseconds=1),expires_at=expiry+timedelta(minutes=1)))
        assert not stale.ok and "REPLACEMENT_MATERIALIZATION_OM9_SOURCE_CYCLE_TOO_STALE" in stale.reason_codes
        assert conn.execute("SELECT count(*) FROM forecast_posteriors").fetchone()[0]==count
        assert not any(item.ok for item in public(reset_posterior,reset_readiness,expiry+timedelta(microseconds=1)))
        conn.close()
        return

    # A corrupt latest dependency cannot fall back to the original missing
    # path, but a new actual local verification drains it through normal APIs.
    proof_path = Path(conn.execute("SELECT artifact_path FROM raw_forecast_artifacts WHERE artifact_id=?",
        (context.report["local_proof_artifact_ids"][0],)).fetchone()[0])
    proof_path.write_bytes(proof_path.read_bytes()+b" ")
    from src.data.replacement_forecast_production import _critical_scopes_missing_current_anchor
    family = (city.name,target.isoformat(),metric)
    assert _critical_scopes_missing_current_anchor(db,[family],cycle,decision_time=datetime.now(UTC)) == (family,)
    assert not any(item.ok for item in public(posterior,readiness,datetime.now(UTC)))
    import scripts.download_replacement_forecast_current_targets as producer
    recovered = producer.download_current_target_raw_inputs(forecast_db=db,output_dir=context.output,cycle=cycle,
        limit=None,write_db=True,release_lag_hours=0.,anchor_sigma_c=3.,
        required_scopes=[(city.name,target.isoformat(),metric)],expand_metric_siblings=False)
    assert recovered["db_artifact_ids"] == [context.aid]
    assert recovered["local_proof_artifact_ids"] and recovered["local_proof_artifact_ids"] != context.report["local_proof_artifact_ids"]
    reset_cut = datetime.now(UTC)
    assert _critical_scopes_missing_current_anchor(db,[family],cycle,decision_time=reset_cut) == ()
    reset_request,reset_result,reset_posterior,reset_readiness = normal_materialize(reset_cut,"reset")
    assert reset_result.posterior_id != result.posterior_id
    assert reset_posterior["q_json"] == posterior["q_json"]
    reset_shape = json.loads(reset_posterior["provenance_json"])["bayes_precision_fusion"]["current_evidence_shape"]
    assert reset_shape["provider_geometry_identity_hash"] == provenance["bayes_precision_fusion"]["current_evidence_shape"]["provider_geometry_identity_hash"]
    for served in public(reset_posterior,reset_readiness,reset_cut):
        assert served.ok, served.reason_code
        assert served.bundle.posterior_id == reset_result.posterior_id
    expiry = replacement_readiness_expires_at(cycle)
    assert _critical_scopes_missing_current_anchor(db,[family],cycle,decision_time=expiry+timedelta(microseconds=1)) == (family,)
    foreign = (city.name,(target+timedelta(days=1)).isoformat(),metric)
    assert _critical_scopes_missing_current_anchor(db,[foreign],cycle,decision_time=reset_cut) == (foreign,)
    count = conn.execute("SELECT count(*) FROM forecast_posteriors").fetchone()[0]
    stale = materialize_replacement_forecast_live(conn,replace(reset_request,computed_at=expiry+timedelta(microseconds=1),
        expires_at=expiry+timedelta(minutes=1)))
    assert not stale.ok and "REPLACEMENT_MATERIALIZATION_OM9_SOURCE_CYCLE_TOO_STALE" in stale.reason_codes
    assert conn.execute("SELECT count(*) FROM forecast_posteriors").fetchone()[0] == count
    assert not any(item.ok for item in public(reset_posterior,reset_readiness,expiry+timedelta(microseconds=1)))
    assert tuple(conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?",(context.aid,)).fetchone()) == context.original_row
    conn.close()


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("missing_transport", (None, "precision", "manifest"))
def test_same_byte_local_proof_normal_seed_materializer_and_public_reset(tmp_path, monkeypatch, metric, missing_transport):
    _normal_owned_anchor_seed_public(tmp_path,monkeypatch,metric,missing_transport)


@pytest.mark.parametrize("metric", ("high","low"))
@pytest.mark.parametrize("lane", ("station","background"))
def test_daemon_selected_seed_bootstraps_ground_then_fresh_immutable_precision_and_public(tmp_path,monkeypatch,metric,lane):
    """Actual daemon bootstrap without a request; normal fresh proof/seed/q, no oldcut renewal."""
    _normal_owned_anchor_seed_public(tmp_path,monkeypatch,metric,None,daemon_lane=lane)


@pytest.mark.parametrize("metric",("low","high"))
@pytest.mark.parametrize("damage",("ordinary","extreme"))
@pytest.mark.parametrize("held",(False,True))
def test_normal_same_issued_http_proof_progress_reseeds_new_public_certificate(tmp_path,monkeypatch,metric,damage,held):
    _normal_owned_anchor_seed_public(tmp_path,monkeypatch,metric,None,capture_case=(damage,held))


@pytest.mark.parametrize("metric",("high","low"))
@pytest.mark.parametrize("fault",("precision_json","manifest_json","symlink","foreign_output","old_cut","enospc","concurrent_foreign","concurrent_same"))
def test_normal_localproof_transport_restores_only_owned_missing_complete_files(tmp_path,monkeypatch,metric,fault):
    import errno
    import scripts.download_replacement_forecast_current_targets as producer
    from src.data.raw_forecast_artifact_manifest import read_anchor_local_proof
    context = _normal_localproof_recovery(tmp_path,monkeypatch,metric)
    manifest = Path(context.report["written_manifests"][0])
    precision = Path(json.loads(manifest.read_bytes())["product_metadata"]["precision_metadata_json"])
    original_manifest = manifest.read_bytes()
    original_precision = precision.read_bytes()
    with sqlite3.connect(context.scope["forecast_db"]) as conn:
        original_count=conn.execute("SELECT count(*) FROM raw_forecast_artifacts").fetchone()[0]
    if fault == "precision_json":
        precision.write_bytes(b"{")
    elif fault == "manifest_json":
        manifest.write_bytes(b"{")
    elif fault == "symlink":
        precision.unlink()
        precision.symlink_to(tmp_path/"foreign-precision.json")
    elif fault in ("enospc","concurrent_foreign","concurrent_same","old_cut","foreign_output"):
        precision.unlink()
    if fault == "enospc":
        monkeypatch.setattr(producer.os,"link",lambda *a: (_ for _ in ()).throw(OSError(errno.ENOSPC,"no space")))
    elif fault in ("concurrent_foreign","concurrent_same"):
        link = producer.os.link
        def competing_publish(source,target):
            Path(target).write_bytes(b"{}" if fault=="concurrent_foreign" else original_precision)
            return link(source,target)
        monkeypatch.setattr(producer.os,"link",competing_publish)
    if fault in ("old_cut","foreign_output"):
        with sqlite3.connect(context.scope["forecast_db"]) as conn:
            evidence = read_anchor_local_proof(conn,context.aid,city=context.city.name,
                target_date=context.scope["target_date"],metric=metric,decision_at=datetime.now(UTC))
        raw_dir = context.output/context.cycle.strftime("%Y%m%dT%H%M%SZ")
        if fault=="foreign_output":
            raw_dir=tmp_path/"foreign-output"/raw_dir.name
            raw_dir.mkdir(parents=True)
        with pytest.raises(ValueError):
            producer._anchor_local_proof_transport(evidence,raw_dir=raw_dir,city=context.city.name,
                target_date=context.scope["target_date"],metric=metric,cycle=context.cycle,
                decision_time=context.before if fault=="old_cut" else datetime.now(UTC),restore_missing=True)
        assert not precision.exists()
    elif fault=="concurrent_same":
        report=producer.download_current_target_raw_inputs(**context.args)
        assert report["reused_canonical_artifact_ids"]==[context.aid]
        assert report["local_proof_artifact_ids"]==[]
        assert precision.read_bytes()==original_precision
    else:
        with pytest.raises(RuntimeError,match="anchor transport restoration failed"):
            producer.download_current_target_raw_inputs(**context.args)
        if fault=="enospc":
            assert not precision.exists()
        elif fault=="symlink":
            assert precision.is_symlink() and not precision.exists()
        else:
            assert (manifest if fault=="manifest_json" else precision).read_bytes() == (
                b"{}" if fault=="concurrent_foreign" else b"{")
    if fault!="manifest_json":
        assert manifest.read_bytes()==original_manifest
    with sqlite3.connect(context.scope["forecast_db"]) as conn:
        assert tuple(conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?",(context.aid,)).fetchone())==context.original_row
        assert conn.execute("SELECT count(*) FROM raw_forecast_artifacts").fetchone()[0] == original_count


@pytest.mark.parametrize("metric",("high","low"))
@pytest.mark.parametrize("damage",("missing","cell","request","static","body","scope","model","old_cut","none_cut","borrowed_cell"))
def test_anchor_only_ifs9_replays_its_own_normal_body_request_cell_and_cut(tmp_path,monkeypatch,metric,damage):
    from copy import deepcopy
    from src.data.replacement_forecast_cycle_policy import _anchor_ifs9_response_has_authority
    bound,path,cut,scope=_normal_anchor_only_ifs9(tmp_path,monkeypatch,metric)
    geometry=deepcopy(bound.provider_geometry_evidence)
    audit=deepcopy(bound.provider_geometry_audit)
    assert _anchor_ifs9_response_has_authority(geometry,audit,materialized_at=cut.isoformat(),**scope)
    proof=audit["anchor_precision_metadata"]["source_geometry_proof"]
    if damage=="missing":
        del audit["anchor_raw_artifact"]
    elif damage=="cell":
        proof["selected_flat_index"]+=1
    elif damage=="request":
        audit["anchor_precision_metadata"]["requested_lat"]+=.01
    elif damage=="static":
        Path(proof["static_asset_audit"]["asset_path"]).unlink()
    elif damage=="body":
        path.write_bytes(path.read_bytes()+b" ")
    elif damage=="scope":
        audit["anchor_precision_metadata"]["target_local_date"]="2030-01-01"
    elif damage=="model":
        audit["anchor_raw_artifact"]["product_id"]="ecmwf_ifs"
    elif damage=="borrowed_cell":
        from src.data.openmeteo_ecmwf_ifs9_bucket_transport import select_terrain_optimised_point,capture_source_cell_geometry_proof,HSURF_LOCAL_CACHE
        other=select_terrain_optimised_point(22.8,114.8,32.,local_cache=HSURF_LOCAL_CACHE)
        borrowed=capture_source_cell_geometry_proof(latitude=other.grid_latitude,
            longitude=(other.grid_longitude_east+180)%360-180,target_elevation_m=32.,
            requested_latitude=22.8,requested_longitude=114.8)
        proof.update(borrowed)
        geometry["providers"]["__anchor_ifs9__"]["source_geometry_proof"].update({key:value for key,value in borrowed.items()
            if key not in ("static_asset_audit","static_hsurf_sha256")})
    elif damage=="none_cut":
        assert not _anchor_ifs9_response_has_authority(geometry,audit,materialized_at=None,**scope)
        return
    else:
        cut=datetime.fromisoformat(proof["static_asset_audit"]["possessed_at"])-timedelta(microseconds=1)
    assert not _anchor_ifs9_response_has_authority(geometry,audit,materialized_at=cut.isoformat(),**scope)


@pytest.mark.parametrize("metric",("high","low"))
@pytest.mark.parametrize("foreign",("target","metric"))
def test_anchor_legal_foreign_pair_cannot_relabel_independent_certificate_scope(tmp_path,monkeypatch,metric,foreign):
    """A second fully legal own body/metadata pair, not a hash-tampered strawman."""
    from copy import deepcopy
    from datetime import date
    import scripts.download_replacement_forecast_current_targets as producer
    from src.data.openmeteo_ecmwf_ifs9_anchor import OpenMeteoEcmwfIfs9AnchorRequest,build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest
    from src.data.raw_forecast_artifact_manifest import write_manifest_to_db
    from src.data.replacement_current_value_serving import _ARTIFACT_IDENTITY_JSON_SQL
    from src.data.replacement_forecast_cycle_policy import _anchor_ifs9_response_has_authority
    bound,path,cut,original=_normal_anchor_only_ifs9(tmp_path,monkeypatch,metric)
    geometry=bound.provider_geometry_evidence
    audit=deepcopy(bound.provider_geometry_audit)
    old=audit["anchor_raw_artifact"]
    params=json.loads(old["request_params_json"])
    payload=json.loads(path.read_bytes())
    day=date.fromisoformat(original["target_date"])+(timedelta(days=1) if foreign=="target" else timedelta())
    payload["hourly"]["time"]=[datetime.combine(day,datetime.min.time()).replace(hour=i).isoformat(timespec="minutes") for i in range(24)]
    newpath=tmp_path/"legal-foreign-anchor.json"
    newpath.write_bytes((json.dumps(payload,indent=2)+"\n").encode())
    other_metric=("low" if metric=="high" else "high") if foreign=="metric" else metric
    precision=producer._precision_metadata(original["city"],day.isoformat(),anchor_sigma_c=3.,raw_payload_bytes=newpath.read_bytes())
    manifest=build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest(newpath,
        request=OpenMeteoEcmwfIfs9AnchorRequest(float(params["latitude"]),float(params["longitude"]),
            datetime.fromisoformat(old["source_cycle_time"]),params["timezone"]),metric=other_metric,
        source_available_at=old["source_available_at"],captured_at=old["captured_at"],
        product_metadata={"city":original["city"],"target_date":day.isoformat()})
    with sqlite3.connect(old["forecast_db"]) as conn:
        aid=write_manifest_to_db(conn,manifest)
        conn.commit()
        other={**json.loads(conn.execute(f"SELECT {_ARTIFACT_IDENTITY_JSON_SQL} FROM raw_forecast_artifacts a WHERE artifact_id=?",(aid,)).fetchone()[0]),"forecast_db":old["forecast_db"]}
    audit.update(anchor_raw_artifact=other,anchor_precision_metadata=precision)
    legal={"city":original["city"],"target_date":day.isoformat(),"metric":other_metric,
        "expected_anchor_artifact_id":aid,"request_anchor_artifact_id":aid,"forecast_db":original["forecast_db"]}
    assert _anchor_ifs9_response_has_authority(geometry,audit,materialized_at=cut.isoformat(),**legal)
    # Even re-signing the body's metadata/main claimed ID cannot change the
    # actual posterior's independently selected canonical anchor FK or family.
    assert not _anchor_ifs9_response_has_authority(geometry,audit,materialized_at=cut.isoformat(),**original)
    claimed={**original,"expected_anchor_artifact_id":aid}
    assert not _anchor_ifs9_response_has_authority(geometry,audit,materialized_at=cut.isoformat(),**claimed)
    wrong_scope={**legal,"target_date":original["target_date"],"metric":metric}
    assert not _anchor_ifs9_response_has_authority(geometry,audit,materialized_at=cut.isoformat(),**wrong_scope)
    assert not _anchor_ifs9_response_has_authority(geometry,audit,materialized_at=cut.isoformat())


@pytest.mark.parametrize("metric", ("high", "low"))
def test_anchor_frozen_ground_replays_own_cut_after_actual_later_page_and_station_changes(tmp_path, monkeypatch, metric):
    from copy import deepcopy
    import src.config as config
    import scripts.download_replacement_forecast_current_targets as producer
    from src.data import station_ground_evidence as ground
    from src.data.openmeteo_ecmwf_ifs9_precision_guard import OpenMeteoIfs9PrecisionMetadata
    from src.data.replacement_forecast_cycle_policy import _anchor_ifs9_response_has_authority
    from src.data.replacement_forecast_materializer import _bind_provider_geometry_identity
    from tests.test_station_ground_evidence import _archive, _update_official

    a, body_path, cut_a, scope = _normal_anchor_only_ifs9(tmp_path, monkeypatch, metric)
    geometry, audit = a.provider_geometry_evidence, a.provider_geometry_audit
    db = audit["anchor_raw_artifact"]["forecast_db"]
    registry = config.CONFIG_DIR / "station_precise_coords.json"
    official_body = config.CONFIG_DIR / "hko_station_metadata.html"
    claims = json.loads(registry.read_text())
    body_a = official_body.read_bytes()
    clock = [cut_a + timedelta(minutes=2)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz or UTC)

    monkeypatch.setattr(ground, "datetime", Clock)
    assert _anchor_ifs9_response_has_authority(geometry, audit, materialized_at=cut_a.isoformat(), **scope)

    body_b = body_a + b"<!-- unrelated official page formatting -->"
    _update_official(registry, official_body, claims, body_b, (cut_a + timedelta(minutes=1)).isoformat())
    b = _archive(db)
    assert b["facts_identity"] == audit["anchor_station_ground"]["facts_identity"]
    assert _anchor_ifs9_response_has_authority(geometry, audit, materialized_at=cut_a.isoformat(), **scope)
    assert _anchor_ifs9_response_has_authority(geometry, audit, materialized_at=clock[0].isoformat(), **scope)

    body_c = body_b.replace(b'<td class="td1_normal_class">32</td>', b'<td class="td1_normal_class">33</td>', 1)
    clock[0] = cut_a + timedelta(minutes=4)
    _update_official(registry, official_body, claims, body_c, (cut_a + timedelta(minutes=3)).isoformat())
    c = _archive(db)
    assert c["facts"]["elevation_m"] == 33
    assert ground.read_current_station_ground_evidence(db, city=scope["city"], decision_at=cut_a)["facts"]["elevation_m"] == 32
    assert _anchor_ifs9_response_has_authority(geometry, audit, materialized_at=cut_a.isoformat(), **scope)
    assert not _anchor_ifs9_response_has_authority(geometry, audit, materialized_at=clock[0].isoformat(), **scope)

    metadata_c = OpenMeteoIfs9PrecisionMetadata(**producer._precision_metadata(
        scope["city"], scope["target_date"], anchor_sigma_c=3., raw_payload_bytes=body_path.read_bytes(),analysis_at=clock[0],
    ))
    bound_c = _bind_provider_geometry_identity(a, {}, anchor_metadata=metadata_c, decision_at=clock[0],
        station_ground_evidence=c, anchor_raw_artifact=audit["anchor_raw_artifact"])
    assert _anchor_ifs9_response_has_authority(bound_c.provider_geometry_evidence,
        bound_c.provider_geometry_audit, materialized_at=clock[0].isoformat(), **scope)
    forged = deepcopy(bound_c.provider_geometry_audit)
    forged["anchor_station_ground"]["facts"]["elevation_m"] = 32
    assert not _anchor_ifs9_response_has_authority(bound_c.provider_geometry_evidence,
        forged, materialized_at=clock[0].isoformat(), **scope)
    del forged["anchor_station_ground"]
    assert not _anchor_ifs9_response_has_authority(bound_c.provider_geometry_evidence,
        forged, materialized_at=clock[0].isoformat(), **scope)


@pytest.mark.parametrize("metric", ("high", "low"))
def test_anchor_natural_ids_are_bound_to_the_actual_reader_database_namespace(tmp_path, monkeypatch, metric):
    from copy import deepcopy
    from datetime import date
    import scripts.download_replacement_forecast_current_targets as producer
    from src.data.openmeteo_ecmwf_ifs9_anchor import (
        OpenMeteoEcmwfIfs9AnchorRequest, build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest,
        extract_openmeteo_ecmwf_ifs9_localday_anchor,
    )
    from src.data.openmeteo_ecmwf_ifs9_precision_guard import (
        OpenMeteoIfs9PrecisionMetadata, evaluate_openmeteo_ecmwf_ifs9_precision_guard,
    )
    from src.data.raw_forecast_artifact_manifest import write_manifest_to_db
    from src.data.replacement_current_value_serving import _ARTIFACT_IDENTITY_JSON_SQL
    from src.data.replacement_forecast_cycle_policy import _anchor_ifs9_response_has_authority
    from src.data.replacement_forecast_materializer import (
        ReplacementForecastMaterializeRequest, _bind_provider_geometry_identity, _insert_anchor,
    )
    from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema
    from tests.test_station_ground_evidence import _archive

    a, path_a, cut, scope_a = _normal_anchor_only_ifs9(tmp_path, monkeypatch, metric)
    assert _anchor_ifs9_response_has_authority(a.provider_geometry_evidence,
        a.provider_geometry_audit, materialized_at=cut.isoformat(), **scope_a)
    db_b = tmp_path / "foreign-namespace" / Path(scope_a["forecast_db"]).name
    db_b.parent.mkdir()
    with sqlite3.connect(db_b) as conn:
        ensure_replacement_forecast_live_schema(conn)
    entity_b = _archive(db_b)
    payload = json.loads(path_a.read_bytes())
    payload["hourly"]["temperature_2m"] = [21.] * 24
    path_b = tmp_path / "foreign-normal-anchor.json"
    path_b.write_bytes((json.dumps(payload, indent=2) + "\n").encode())
    metadata_b = OpenMeteoIfs9PrecisionMetadata(**producer._precision_metadata(
        scope_a["city"], scope_a["target_date"], anchor_sigma_c=3., raw_payload_bytes=path_b.read_bytes(),
    ))
    artifact_a = a.provider_geometry_audit["anchor_raw_artifact"]
    params = json.loads(artifact_a["request_params_json"])
    run = datetime.fromisoformat(artifact_a["source_cycle_time"])
    captured = datetime.fromisoformat(artifact_a["captured_at"])
    target = date.fromisoformat(scope_a["target_date"])
    manifest_b = build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest(path_b,
        request=OpenMeteoEcmwfIfs9AnchorRequest(float(params["latitude"]), float(params["longitude"]), run, params["timezone"]),
        metric=metric, source_available_at=captured, captured_at=captured,
        product_metadata={"city":scope_a["city"], "target_date":scope_a["target_date"]})
    with sqlite3.connect(db_b) as conn:
        aid_b = write_manifest_to_db(conn, manifest_b)
        artifact_b = {**json.loads(conn.execute(f"SELECT {_ARTIFACT_IDENTITY_JSON_SQL} FROM raw_forecast_artifacts a WHERE artifact_id=?",
            (aid_b,)).fetchone()[0]), "forecast_db":str(db_b)}
        anchor_b = _insert_anchor(conn, ReplacementForecastMaterializeRequest(
            city=scope_a["city"],city_id=scope_a["city"],city_timezone=params["timezone"],target_date=target,temperature_metric=metric,
            baseline_source_run_id="unit-anchor-not-q-authority",baseline_data_version="unit-only",baseline_source_available_at=captured,
            openmeteo_anchor=extract_openmeteo_ecmwf_ifs9_localday_anchor(payload, city_timezone=params["timezone"],
                target_local_date=target,source_cycle_time=run,require_full_localday=True),openmeteo_source_run_id=None,
            openmeteo_source_available_at=captured,bins=[],source_cycle_time=run,computed_at=cut,anchor_artifact_id=aid_b,
            openmeteo_raw_payload_bytes=path_b.read_bytes(),openmeteo_precision_guard=evaluate_openmeteo_ecmwf_ifs9_precision_guard(
                metadata_b,raw_payload_bytes=path_b.read_bytes(),decision_at=cut)),metric=metric)
    assert aid_b == scope_a["expected_anchor_artifact_id"]
    assert anchor_b == scope_a["anchor_id"]
    b = _bind_provider_geometry_identity(a, {}, anchor_metadata=metadata_b, decision_at=cut,
        station_ground_evidence=entity_b,anchor_raw_artifact=artifact_b)
    assert b.provider_geometry_evidence == a.provider_geometry_evidence
    scope_b = {**scope_a, "forecast_db":db_b}
    assert _anchor_ifs9_response_has_authority(b.provider_geometry_evidence,
        b.provider_geometry_audit,materialized_at=cut.isoformat(),**scope_b)
    assert not _anchor_ifs9_response_has_authority(a.provider_geometry_evidence,
        b.provider_geometry_audit,materialized_at=cut.isoformat(),**scope_a)
    foreign_ground = deepcopy(a.provider_geometry_audit)
    foreign_ground["anchor_station_ground"] = entity_b
    assert not _anchor_ifs9_response_has_authority(a.provider_geometry_evidence,
        foreign_ground,materialized_at=cut.isoformat(),**scope_a)
    missing_namespace = {**scope_a, "forecast_db":None}
    assert not _anchor_ifs9_response_has_authority(a.provider_geometry_evidence,
        a.provider_geometry_audit,materialized_at=cut.isoformat(),**missing_namespace)
    alias = tmp_path / "reader-db-alias.sqlite"
    alias.symlink_to(scope_a["forecast_db"])
    assert _anchor_ifs9_response_has_authority(a.provider_geometry_evidence,
        a.provider_geometry_audit,materialized_at=cut.isoformat(),**{**scope_a,"forecast_db":alias})


@pytest.mark.parametrize("metric", ("high", "low"))
def test_normal_wmd_future_disclosure_changes_only_target_applicability_hwm_and_network_debt(tmp_path, monkeypatch, metric):
    """Actual ground/raw producers; HWM component evidence is not a q certificate."""
    from src.data import station_ground_evidence as ground
    from src.data.replacement_current_value_serving import (
        physical_capture_debt_reason, read_current_instrument_values, station_ground_target_coverage_for_city,
    )
    from src.data.replacement_forecast_live_materialization_queue import _blocked_attempt_fingerprint
    from src.data.replacement_input_hwm import _exact_current_value_serving_lag
    from tests.test_station_ground_evidence import _wmd_setup, _archive, _wmd_known_periods

    db, registry, primary, _, claims, clock = _wmd_setup(tmp_path, monkeypatch)
    a = _archive(db, "Paris")
    cut_a = datetime(2026,9,30,1,19,tzinfo=UTC)
    run = "2026-09-29T18:00:00+00:00"
    days = ("2026-09-30", "2026-10-01")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    models = ("icon_global", "ukmo_global_deterministic_10km")
    for day in days:
        for model in models:
            _persist_exact_provider_body(conn,tmp_path,city="Paris",metric=metric,target_date=day,model=model,
                cycle=run,captured="2026-09-30T01:16:00+00:00",value=20.,network=True,payload_dates=days)
    conn.commit()
    provenances, fingerprints = {}, {}

    def fingerprint(day, cut):
        return _blocked_attempt_fingerprint(input_json=tmp_path/"seed.json",forecast_db=db,
            payload={"forecast_db":str(db),"city":"Paris","target_date":day,"temperature_metric":metric,
                "source_cycle_time":run,"computed_at":cut.isoformat()})

    def lag(day, decision):
        return _exact_current_value_serving_lag(conn,city="Paris",target_date=day,metric=metric,
            decision_time=decision,posterior_computed_at=cut_a,provenance=provenances[day])

    for day in days:
        served = read_current_instrument_values(conn,city="Paris",metric=metric,target_date=day,
            source_cycle_time_iso=run,decision_time_iso=cut_a.isoformat())
        assert set(served) == set(models)
        provenances[day] = {"bayes_precision_fusion":{"used_models":list(models),
            "current_value_serving":{model:value.as_provenance() for model,value in served.items()},
            "current_evidence_shape":{"provider_geometry_evidence":{"component_only":True},
                "provider_geometry_audit":{"anchor_station_ground":a}}}}
        assert lag(day,cut_a)[1] is None
        fingerprints[day] = fingerprint(day,cut_a)
        assert fingerprints[day] is not None
    originals = conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id").fetchall()
    _wmd_known_periods(primary,registry,claims,[("2026-10-01",100)],captured="2026-09-30T01:20:00Z")
    clock[0] = datetime(2026,9,30,1,30,tzinfo=UTC)
    b = _archive(db,"Paris")
    assert b["facts_identity"] == a["facts_identity"]
    cut_b = datetime(2026,9,30,1,31,tzinfo=UTC)
    assert station_ground_target_coverage_for_city(b,city="Paris",target_date=days[1],decision_at=cut_b)["status"] == "DATA_DEGRADED"
    assert lag(days[1],cut_b)[1] == "basis=station_ground_target_applicability_changed"
    assert lag(days[0],cut_b)[1] is None
    assert lag(days[1],cut_a)[1] is None
    assert fingerprint(days[1],cut_b) != fingerprints[days[1]]
    assert fingerprint(days[0],cut_b) == fingerprints[days[0]]
    assert fingerprint(days[1],cut_a) == fingerprints[days[1]]
    # Only missing HTTP proof can be repaired by HTTP. A known target conflict
    # must not use that same reason to keep forcing requests for this day.
    conn.execute("DELETE FROM raw_forecast_artifacts WHERE data_version='openmeteo_single_model_http_capture_receipt_v1'")
    conn.commit()
    raw_ids = {row["target_date"]:row["raw_model_forecast_id"] for row in originals if row["model"]=="icon_global"}
    assert physical_capture_debt_reason(conn,raw_model_forecast_id=raw_ids[days[0]],decision_time_iso=cut_b.isoformat()) == "HTTP_CAPTURE_RECEIPT_MISSING"
    assert physical_capture_debt_reason(conn,raw_model_forecast_id=raw_ids[days[1]],decision_time_iso=cut_b.isoformat()) is None
    assert physical_capture_debt_reason(conn,raw_model_forecast_id=raw_ids[days[1]],decision_time_iso=cut_a.isoformat()) == "HTTP_CAPTURE_RECEIPT_MISSING"
    assert conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id").fetchall() == originals
    conn.close()


@pytest.mark.parametrize("metric", ("high", "low"))
def test_wmd_public_ground_window_uses_independent_family_and_decision_visible_knowledge(tmp_path, monkeypatch, metric):
    from copy import deepcopy
    import src.config as config
    from src.data import station_ground_evidence as ground
    from src.data.replacement_forecast_cycle_policy import _anchor_station_ground_has_authority
    from tests.test_station_ground_evidence import _archive, _wmd_known_periods
    a, _, cut_a, scope = _normal_anchor_only_ifs9(tmp_path,monkeypatch,metric,city_name="Paris")
    audit, geometry = a.provider_geometry_audit, a.provider_geometry_evidence
    metadata = audit["anchor_precision_metadata"]
    independent = {"certificate_city":scope["city"], "certificate_target_date":scope["target_date"]}
    assert _anchor_station_ground_has_authority(geometry,audit,cut_a.isoformat(),target_scope=metadata,**independent)
    for field, wrong in (("target_local_date","2030-01-01"), ("city","Hong Kong"),
        ("timezone_name","UTC"), ("local_day_start_utc",(cut_a-timedelta(days=1)).isoformat()),
        ("local_day_end_utc",(cut_a+timedelta(days=1)).isoformat())):
        assert not _anchor_station_ground_has_authority(geometry,audit,cut_a.isoformat(),
            target_scope={**metadata,field:wrong},**independent)
    assert not _anchor_station_ground_has_authority(geometry,audit,cut_a.isoformat(),target_scope=metadata)
    registry = config.CONFIG_DIR/"station_precise_coords.json"
    primary = config.CONFIG_DIR/"wmo_wmd_lfpb_station.xml"
    claims = json.loads(registry.read_text())
    _wmd_known_periods(primary,registry,claims,[(scope["target_date"],100)],captured=(cut_a+timedelta(minutes=1)).isoformat())
    cut_b = cut_a+timedelta(minutes=3)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return (cut_a+timedelta(minutes=2)).astimezone(tz or UTC)
    monkeypatch.setattr(ground,"datetime",Clock)
    b = _archive(scope["forecast_db"],"Paris")
    assert b["facts_identity"] == audit["anchor_station_ground"]["facts_identity"]
    assert _anchor_station_ground_has_authority(geometry,audit,cut_a.isoformat(),target_scope=metadata,**independent)
    # At a genuinely later materialization cut, an older same-facts XML cannot
    # hide newly possessed target restrictions disclosed by the newer entity.
    later = deepcopy(audit)
    later["decision_at"] = cut_b.isoformat()
    assert not _anchor_station_ground_has_authority(geometry,later,cut_b.isoformat(),target_scope=metadata,**independent)


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("legacy", (False, True))
def test_frozen_ifs9_keeps_original_raw_identity_when_equal_value_body_is_recaptured(tmp_path, monkeypatch, metric, legacy):
    from src.data.replacement_current_value_serving import frozen_ifs9_response_has_authority, provider_geometry_projection
    conn, a, persist, _, _, clock = _normal_ifs9_owned_product(tmp_path, monkeypatch, metric, legacy=legacy)
    raw = conn.execute("SELECT * FROM raw_model_forecasts").fetchall()
    assert frozen_ifs9_response_has_authority(a.physical_response, provider_geometry_projection(a.physical_response),
        decision_at=(clock[0]+timedelta(minutes=1)).isoformat())
    original_identity = a.physical_response["frozen_product_identity"]
    assert original_identity["artifact_id"] == (None if legacy else a.physical_response["artifact_id"])
    # A real new hourly body can have the same daily scalar. It must retain the
    # immutable old raw/body identity as well as its new entity and HTTP receipt.
    persist.payload["hourly"]["temperature_2m"][2] = 19. if metric=="high" else 21.
    clock[0] += timedelta(minutes=2)
    b = persist(0)
    assert b.physical_response["entity_body_sha256"] != a.physical_response["entity_body_sha256"]
    assert b.physical_response["frozen_product_identity"] == original_identity
    assert frozen_ifs9_response_has_authority(b.physical_response, provider_geometry_projection(b.physical_response),
        decision_at=(clock[0]+timedelta(minutes=1)).isoformat())
    assert frozen_ifs9_response_has_authority(a.physical_response, provider_geometry_projection(a.physical_response),
        decision_at="2026-09-30T12:01:00Z")
    assert conn.execute("SELECT * FROM raw_model_forecasts").fetchall() == raw
    conn.close()


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("damage", ("two_fields", "missing_index", "index", "native_height", "effective_height",
    "sea", "request", "selected", "model", "body_hash", "static_bytes", "old_cut",
    "surface_projection", "surface_witness", "geometry_request", "unit"))
def test_used_ifs9_frozen_response_replays_its_own_complete_request_body_and_cell(tmp_path, monkeypatch, metric, damage):
    from copy import deepcopy
    from dataclasses import dataclass
    from src.data.replacement_forecast_materializer import _bind_provider_geometry_identity
    from src.data.replacement_current_value_serving import frozen_ifs9_response_has_authority, provider_geometry_projection
    @dataclass(frozen=True)
    class Shape:
        shape_hash: str = "current-shape"
        provider_geometry_evidence: object = None
        provider_geometry_identity_hash: object = None
        provider_geometry_audit: object = None

    conn, served, _, _, _, clock = _normal_ifs9_owned_product(tmp_path, monkeypatch, metric)
    cut = (clock[0] + timedelta(minutes=1)).isoformat()
    physical = deepcopy(served.physical_response)
    bound = _bind_provider_geometry_identity(Shape(), {"ecmwf_ifs": served}, decision_at=cut)
    geometry = deepcopy(bound.provider_geometry_evidence["providers"]["ecmwf_ifs"])
    assert frozen_ifs9_response_has_authority(physical, geometry, decision_at=cut)
    proof = physical["source_cell_geometry_proof"]
    if damage == "two_fields":
        physical["source_cell_geometry_proof"] = {"revision": proof["revision"], "cell_is_sea": False}
        geometry["source_cell_geometry_proof"] = deepcopy(physical["source_cell_geometry_proof"])
    elif damage == "missing_index":
        del proof["selected_flat_index"]
    elif damage == "index":
        proof["selected_flat_index"] += 1
    elif damage == "native_height":
        proof["raw_grid_elevation_m"] += 1
    elif damage == "effective_height":
        proof["effective_grid_elevation_m"] += 1
    elif damage == "sea":
        proof["cell_is_sea"] = True
    elif damage == "request":
        physical["frozen_product_identity"]["latitude_requested"] = 0.
    elif damage == "selected":
        geometry["selected_latitude"] += .01
    elif damage == "model":
        physical["frozen_product_identity"]["model_name"] = "ecmwf_ifs025"
    elif damage == "body_hash":
        physical["frozen_entity_body"]["body_artifact"]["sha256"] = "0" * 64
    elif damage == "static_bytes":
        Path(proof["static_asset_audit"]["asset_path"]).unlink()
    elif damage == "surface_projection":
        geometry["model_surface_geometry"]["native_grid_elevation_m"] += 1
    elif damage == "surface_witness":
        physical["model_surface_witness"]["geometry"]["native_grid_elevation_m"] += 1
        geometry = provider_geometry_projection(physical)
    elif damage == "geometry_request":
        geometry["requested_latitude"] = 0.
    elif damage == "unit":
        physical["temperature_unit"] = "fahrenheit"
        geometry = provider_geometry_projection(physical)
    else:
        cut = (clock[0] - timedelta(microseconds=1)).isoformat()
    assert not frozen_ifs9_response_has_authority(physical, geometry, decision_at=cut)
    conn.close()


@pytest.mark.parametrize("metric", ("high", "low"))
def test_ifs9_static_whole_body_is_audit_dependency_not_local_geometry(tmp_path, monkeypatch, metric):
    from dataclasses import dataclass
    from src.data.replacement_forecast_materializer import _bind_provider_geometry_identity
    from src.data.replacement_current_value_serving import physical_source_proof_dependency, frozen_ifs9_response_has_authority
    @dataclass(frozen=True)
    class Shape:
        shape_hash: str = "current-shape"
        provider_geometry_evidence: object = None
        provider_geometry_identity_hash: object = None
        provider_geometry_audit: object = None
    conn, a, persist, data, write, clock = _normal_ifs9_owned_product(tmp_path, monkeypatch, metric)
    raw = conn.execute("SELECT * FROM raw_model_forecasts").fetchall()
    bound_a = _bind_provider_geometry_identity(Shape(), {"ecmwf_ifs": a}, decision_at=clock[0].isoformat())
    data[0, 0] += 1  # Another cell, never this request's physical product.
    write()
    clock[0] += timedelta(minutes=2)
    b = persist(0)
    bound_b = _bind_provider_geometry_identity(Shape(), {"ecmwf_ifs": b}, decision_at=clock[0].isoformat())
    assert bound_a.provider_geometry_identity_hash == bound_b.provider_geometry_identity_hash
    assert bound_a.shape_hash == bound_b.shape_hash
    assert physical_source_proof_dependency(a.physical_response) != physical_source_proof_dependency(b.physical_response)
    assert frozen_ifs9_response_has_authority(a.physical_response,
        bound_a.provider_geometry_evidence["providers"]["ecmwf_ifs"], decision_at="2026-09-30T12:01:00Z")
    own_index = a.physical_response["source_cell_geometry_proof"]["selected_flat_index"]
    data[0, own_index] += 1
    write()
    clock[0] += timedelta(minutes=2)
    c = persist(0)
    bound_c = _bind_provider_geometry_identity(Shape(), {"ecmwf_ifs": c}, decision_at=clock[0].isoformat())
    assert bound_c.provider_geometry_identity_hash != bound_b.provider_geometry_identity_hash
    assert bound_c.shape_hash != bound_b.shape_hash
    assert conn.execute("SELECT * FROM raw_model_forecasts").fetchall() == raw
    conn.close()


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("debt", (None, "ENTITY_BODY_MISSING", "HTTP_CAPTURE_RECEIPT_MISSING", "MODEL_SURFACE_EPOCH_AFTER_BODY"))
def test_physical_capture_debt_uses_exact_causal_raw_and_actual_land_product(tmp_path, monkeypatch, metric, debt):
    from tests.test_station_ground_evidence import _setup, _archive
    from src.data import bayes_precision_fusion_download as dl, openmeteo_model_surface as surface
    from src.data.replacement_current_value_serving import physical_capture_debt_reason
    db, _, _, _, _ = _setup(tmp_path, monkeypatch)
    _archive(db)
    conn = sqlite3.connect(db)
    cycle = "2026-09-29T12:00:00+00:00"
    artifact_id = _persist_exact_provider_body(conn, tmp_path, city="Hong Kong", metric=metric,
        target_date="2026-09-30", model="icon_global", cycle=cycle,
        captured="2026-09-29T22:05:00+00:00", value=20,
        network=debt != "HTTP_CAPTURE_RECEIPT_MISSING")
    conn.commit()
    raw_id = conn.execute("SELECT raw_model_forecast_id FROM raw_model_forecasts").fetchone()[0]
    before = conn.execute("SELECT * FROM raw_model_forecasts").fetchall()
    decision = "2026-09-29T23:30:00+00:00"
    if debt == "ENTITY_BODY_MISSING":
        path = conn.execute("SELECT artifact_path FROM raw_forecast_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()[0]
        Path(path).unlink()
    elif debt == "MODEL_SURFACE_EPOCH_AFTER_BODY":
        body = _controlled_native_static_bytes("icon_global")
        @contextmanager
        def stream(method, url, **kwargs):
            yield SimpleNamespace(status_code=200, headers={"etag":'"new-epoch"',
                "last-modified":"Tue, 29 Sep 2026 23:00:00 GMT", "content-length":str(len(body))},
                iter_raw=lambda **_kwargs:iter((body,)))
        monkeypatch.setattr(surface.httpx, "stream", stream)
        _download_time(monkeypatch, dl, datetime(2026,9,29,23,15,tzinfo=UTC))
        assert surface.ensure_model_surface("icon_global").status == "READY"
    assert physical_capture_debt_reason(conn, raw_model_forecast_id=raw_id, decision_time_iso=decision) == debt
    assert physical_capture_debt_reason(conn, raw_model_forecast_id=raw_id, decision_time_iso="2026-09-29T21:59:59Z") is None
    assert physical_capture_debt_reason(conn, raw_model_forecast_id=raw_id, decision_time_iso="2026-10-02T00:00:00Z") is None
    assert physical_capture_debt_reason(conn, raw_model_forecast_id=True, decision_time_iso=decision) is None
    assert physical_capture_debt_reason(conn, raw_model_forecast_id=raw_id+1000, decision_time_iso=decision) is None
    assert conn.execute("SELECT * FROM raw_model_forecasts").fetchall() == before
    conn.execute("UPDATE raw_model_forecasts SET product_id='foreign-product' WHERE raw_model_forecast_id=?", (raw_id,))
    assert physical_capture_debt_reason(conn, raw_model_forecast_id=raw_id, decision_time_iso=decision) is None
    conn.close()


@pytest.mark.parametrize("missing", ("ground", "surface"))
def test_physical_capture_debt_does_not_force_missing_non_network_evidence(tmp_path, monkeypatch, missing):
    from tests.test_station_ground_evidence import _setup, _archive
    from src.data.replacement_current_value_serving import physical_capture_debt_reason
    from src.data import openmeteo_model_surface as surface
    db, _, _, _, _ = _setup(tmp_path, monkeypatch)
    if missing != "ground":
        _archive(db)
    conn = sqlite3.connect(db)
    _persist_exact_provider_body(conn, tmp_path, city="Hong Kong", metric="high", target_date="2026-09-30",
        model="icon_global", cycle="2026-09-29T12:00:00+00:00", captured="2026-09-29T22:05:00+00:00", value=20)
    conn.commit()
    if missing == "surface":
        for path in surface._cache_root().glob("*.manifest.json"):
            path.unlink()
    raw_id = conn.execute("SELECT raw_model_forecast_id FROM raw_model_forecasts").fetchone()[0]
    assert physical_capture_debt_reason(conn, raw_model_forecast_id=raw_id, decision_time_iso="2026-09-29T23:30:00Z") is None
    conn.close()


@pytest.mark.parametrize("metric", ("high","low"))
@pytest.mark.parametrize("damage", ("captured_at","source_available_at","recorded_at",
    "future_captured_at","future_source_available_at","future_recorded_at","all_clocks","metadata","receipt_file"))
def test_matching_broken_latest_http_receipt_can_drain_without_serving_old_body(tmp_path, monkeypatch, metric, damage):
    from tests.test_station_ground_evidence import _setup, _archive
    from src.data.replacement_current_value_serving import physical_capture_debt_reason, read_current_instrument_values
    db, _, _, _, _ = _setup(tmp_path,monkeypatch)
    _archive(db)
    conn = sqlite3.connect(db)
    scope = dict(city="Hong Kong", metric=metric, target_date="2026-09-30", model="icon_global",
                 cycle="2026-09-29T12:00:00+00:00",value=20,network=True)
    _persist_exact_provider_body(conn,tmp_path,**scope,captured="2026-09-29T22:05:00+00:00")
    conn.commit()
    raw_id = conn.execute("SELECT raw_model_forecast_id FROM raw_model_forecasts").fetchone()[0]
    raw_before = conn.execute("SELECT * FROM raw_model_forecasts").fetchall()
    def current(cut):
        return read_current_instrument_values(conn,city=scope["city"],metric=metric,target_date=scope["target_date"],
            source_cycle_time_iso=scope["cycle"],decision_time_iso=cut)
    assert current("2026-09-29T22:10:00Z")["icon_global"].value_c==20
    _persist_exact_provider_body(conn,tmp_path,**scope,captured="2026-09-29T22:30:00+00:00",expected_written=0)
    conn.commit()
    latest_id, latest_path = conn.execute("SELECT artifact_id,artifact_path FROM raw_forecast_artifacts"
        " WHERE data_version='openmeteo_single_model_http_capture_receipt_v1' ORDER BY artifact_id DESC LIMIT 1").fetchone()
    if damage in ("captured_at","source_available_at","recorded_at"):
        conn.execute(f"UPDATE raw_forecast_artifacts SET {damage}='broken-clock' WHERE artifact_id=?",(latest_id,))
    elif damage.startswith("future_"):
        conn.execute(f"UPDATE raw_forecast_artifacts SET {damage.removeprefix('future_')}='2026-09-30T10:00:00Z' WHERE artifact_id=?",(latest_id,))
    elif damage=="all_clocks":
        conn.execute("UPDATE raw_forecast_artifacts SET captured_at='broken-clock',source_available_at='broken-clock',recorded_at='broken-clock' WHERE artifact_id=?",(latest_id,))
    elif damage=="metadata":
        conn.execute("UPDATE raw_forecast_artifacts SET artifact_metadata_json='{}' WHERE artifact_id=?",(latest_id,))
    else:
        Path(latest_path).unlink()
    conn.commit()
    old_cut="2026-09-29T22:10:00Z"
    rejected_cut="2026-09-29T22:45:00Z"
    assert "icon_global" not in current(rejected_cut)
    assert physical_capture_debt_reason(conn,raw_model_forecast_id=raw_id,decision_time_iso=rejected_cut)=="HTTP_CAPTURE_RECEIPT_MISSING"
    assert current(old_cut)["icon_global"].value_c==20
    _persist_exact_provider_body(conn,tmp_path,**scope,captured="2026-09-29T23:00:00+00:00",expected_written=0)
    conn.commit()
    assert current("2026-09-29T23:05:00Z")["icon_global"].value_c==20
    assert physical_capture_debt_reason(conn,raw_model_forecast_id=raw_id,decision_time_iso="2026-09-29T23:05:00Z") is None
    assert "icon_global" not in current(rejected_cut)
    assert current(old_cut)["icon_global"].value_c==20
    assert conn.execute("SELECT * FROM raw_model_forecasts").fetchall()==raw_before
    conn.close()


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("column,value", (
    ("latitude_requested", 0.0), ("longitude_requested", 0.0),
    ("timezone_requested", "UTC"), ("model_name", "ecmwf_ifs025"),
    ("provider", "other"), ("source_family", "other"),
    ("product_id", "other"), ("cell_selection", "nearest"),
    ("elevation_param", "nan"), ("downscaling_policy", "none"),
    ("model_domain_hash", "bad-domain"), ("request_url_hash", "bad-request"),
    ("request_params_json", '{"elevation":"nan"}'),
))
def test_wrong_provider_product_cannot_serve_current_cohort_or_frontier(
    tmp_path, monkeypatch, metric, column, value,
):
    from src.data.replacement_current_value_serving import (
        current_value_serving_schema, read_current_instrument_values,
        read_current_instrument_frontier_identity, read_freshest_coherent_instrument_values,
    )

    conn, target, cycle = _current_rows(tmp_path, monkeypatch, metric=metric)
    conn.execute(f"UPDATE raw_model_forecasts SET {column}=? WHERE model='icon_global'", (value,))
    decision = cycle.replace(hour=5).isoformat()
    scope = dict(city=target.city, metric=metric, target_date=target.target_date)
    served = read_current_instrument_values(
        conn, **scope, source_cycle_time_iso=cycle.isoformat(), decision_time_iso=decision,
    )
    assert set(served) == {"ukmo_global_deterministic_10km"}
    models = ("icon_global", "ukmo_global_deterministic_10km")
    assert read_freshest_coherent_instrument_values(
        conn, **scope, decision_time_iso=decision, models=models, cohort_window_hours=6,
    ) == {}
    frontier = dict(read_current_instrument_frontier_identity(
        conn, **scope, decision_time_iso=decision, models=models,
        schema=current_value_serving_schema(conn),
    ))
    assert frontier["icon_global"] is None
    assert frontier["ukmo_global_deterministic_10km"] == served["ukmo_global_deterministic_10km"].raw_model_forecast_id
    conn.close()


@pytest.mark.parametrize("metric", ("high", "low"))
def test_ordinary_new_cycle_drains_superseded_default_dem_label(tmp_path, monkeypatch, metric):
    from src.data import bayes_precision_fusion_download as dl
    from src.data.replacement_current_value_serving import read_current_instrument_values

    conn, target, cycle = _current_rows(tmp_path, monkeypatch, metric=metric)
    conn.execute("UPDATE raw_model_forecasts SET elevation_param='requested', downscaling_policy='none'")
    conn.commit()
    scope = dict(city=target.city, metric=metric, target_date=target.target_date)
    assert read_current_instrument_values(
        conn, **scope, source_cycle_time_iso=cycle.isoformat(),
        decision_time_iso=cycle.replace(hour=5).isoformat(),
    ) == {}
    for run in (cycle, cycle.replace(hour=6)):
        _download_time(monkeypatch, dl, run.replace(hour=run.hour + 4))
        _mock_single_model_http(monkeypatch, dl, value=21.0)
        report = dl.download_bayes_precision_fusion_extra_raw_inputs(
            forecast_db=Path(conn.execute("PRAGMA database_list").fetchone()[2]),
            cycle=run, targets=[target],
            models=("icon_global", "ukmo_global_deterministic_10km"),
            include_previous_runs=False, prune_after=False,
        )
        assert report["written_row_count"] == (0 if run == cycle else 2)
    served = read_current_instrument_values(
        conn, **scope, source_cycle_time_iso=cycle.replace(hour=6).isoformat(),
        decision_time_iso=cycle.replace(hour=11).isoformat(),
    )
    assert set(served) == {"icon_global", "ukmo_global_deterministic_10km"}
    assert {row.served_cycle for row in served.values()} == {cycle.replace(hour=6).isoformat()}
    old = conn.execute("SELECT downscaling_policy FROM raw_model_forecasts WHERE source_cycle_time=?", (cycle.isoformat(),)).fetchall()
    assert old == [("none",), ("none",)]
    conn.close()


def test_registered_station_labels_do_not_substitute_for_response_proof(monkeypatch):
    from src.data.replacement_current_value_serving import _source_clock_product_has_authority

    for model, provider in (
        ("hko_fnd", "hong_kong_observatory"),
        ("cwa_township_hourly_high", "cwa_taiwan"),
        ("cwa_township_hourly_low", "cwa_taiwan"),
    ):
        row = dict(
            model=model, provider=provider, source_family="station_official_forecast",
            source_id=f"{model}_single_runs", model_name=model,
            product_id=f"{model}::single_runs", endpoint="single_runs", endpoint_mode="single_runs",
            downscaling_policy="agency_mos", elevation_param="station",
            metric="low" if model.endswith("_low") else "high",
        )
        assert not _source_clock_product_has_authority(json.dumps(row), lead_days=1)
        assert not _source_clock_product_has_authority(json.dumps({**row, "product_id": "other"}), lead_days=1)
        assert not _source_clock_product_has_authority(json.dumps({**row, "model": "hko_unregistered"}), lead_days=1)
        if model.startswith("cwa_township_hourly_"):
            assert not _source_clock_product_has_authority(json.dumps({**row, "metric": "high" if row["metric"] == "low" else "low"}), lead_days=1)


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("damage", ("missing", "bytes", "wrong_city", "units", "value"))
def test_actual_response_proof_rejects_missing_tampered_and_wrong_city(tmp_path, monkeypatch, metric, damage):
    import hashlib
    from src.data.replacement_current_value_serving import read_current_instrument_values
    conn, target, cycle = _current_rows(tmp_path, monkeypatch, metric=metric)
    artifact_id = conn.execute("SELECT artifact_id FROM raw_model_forecasts WHERE model='icon_global'").fetchone()[0]
    path, metadata_json = conn.execute("SELECT artifact_path,artifact_metadata_json FROM raw_forecast_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
    if damage == "missing":
        conn.execute("UPDATE raw_model_forecasts SET artifact_id=NULL WHERE model='icon_global'")
    elif damage == "bytes":
        Path(path).write_bytes(Path(path).read_bytes() + b" ")
    elif damage == "value":
        conn.execute("UPDATE raw_model_forecasts SET forecast_value_c=99 WHERE model='icon_global'")
    else:
        payload = json.loads(Path(path).read_bytes())
        metadata = json.loads(metadata_json)
        if damage == "wrong_city":
            payload["latitude"], payload["longitude"] = 0, 0
            metadata["physical_response"]["locations"][0]["selected_latitude"] = 0
            metadata["physical_response"]["locations"][0]["selected_longitude"] = 0
        else:
            payload["hourly_units"]["temperature_2m"] = "°F"
        body = json.dumps(payload).encode()
        Path(path).write_bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        conn.execute("UPDATE raw_forecast_artifacts SET sha256=?,byte_size=?,artifact_metadata_json=? WHERE artifact_id=?", (digest,len(body),json.dumps(metadata),artifact_id))
        conn.execute("UPDATE raw_model_forecasts SET raw_sha256=? WHERE artifact_id=?", (digest,artifact_id))
    scope = dict(city=target.city, metric=metric, target_date=target.target_date, source_cycle_time_iso=cycle.isoformat())
    # Carrier-bound provided/drain checks and source-clock consumers share the gate.
    for decision in (None, cycle.replace(hour=5).isoformat()):
        values = read_current_instrument_values(conn, **scope, decision_time_iso=decision)
        assert "icon_global" not in values
    conn.close()


def test_single_model_location_batch_keeps_each_models_actual_geometry(tmp_path, monkeypatch):
    from datetime import date
    from src.data import bayes_precision_fusion_download as dl
    monkeypatch.setattr("src.config.state_path", lambda filename: tmp_path / filename)
    dl._SINGLE_RUNS_PAYLOAD_CACHE.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEX.clear()
    run = datetime(2026, 6, 8, tzinfo=UTC)
    requested = []

    def fetch(url, params, **kwargs):
        requested.append(dict(params))
        assert params["models"] in ("icon_global", "ukmo_global_deterministic_10km")
        elevation = 70 if params["models"] == "icon_global" else 120
        payload = []
        for index, (lat, lon) in enumerate(zip(params["latitude"].split(","), params["longitude"].split(","))):
            payload.append({"latitude": float(lat) + .01, "longitude": float(lon) - .01,
                "elevation": elevation + index, "timezone": "Europe/Paris",
                "hourly_units": {"temperature_2m": "°C"},
                "hourly": {"time": [f"2026-06-09T{hour:02d}:00" for hour in range(24)],
                           "temperature_2m": [20.0 + index] * 24}})
        body = json.dumps(payload, indent=2).encode()
        kwargs["capture_entity_body"](body, run.replace(hour=4).timestamp())
        return json.loads(body)

    monkeypatch.setattr("src.data.openmeteo_client.fetch", fetch)
    locations = [(48.967,2.428,"Europe/Paris",(date(2026,6,9),)),
                 (48.0,2.0,"Europe/Paris",(date(2026,6,9),))]
    result = dl._default_live_fetch_locations_batched(models=["icon_global","ukmo_global_deterministic_10km"], locations=locations, run=run, forecast_hours=120)
    assert len(requested) == 2 and all("," not in params["models"] for params in requested)
    for index, per_day in enumerate(result):
        proof = per_day[date(2026,6,9)][dl._BATCH_PHYSICAL_RESPONSE_KEY]
        assert proof["icon_global"]["target_dem_elevation_m"] == 70 + index
        assert proof["ukmo_global_deterministic_10km"]["target_dem_elevation_m"] == 120 + index
        assert proof["icon_global"]["native_grid_elevation_m"] is None
        assert proof["icon_global"]["native_surface"] == "UNKNOWN"
        assert proof["icon_global"]["aggregation"] == "max_min_of_local_day_hourly_samples"


def test_physical_manifest_redacts_request_credentials(tmp_path, monkeypatch):
    from src.data import bayes_precision_fusion_download as dl
    monkeypatch.setattr("src.config.state_path", lambda filename: tmp_path / filename)
    payload = {"latitude": 1, "longitude": 2, "elevation": 3}
    body = json.dumps(payload).encode()
    params = {"models":"icon_global","hourly":"temperature_2m", "latitude":1,"longitude":2,
              "timezone":"UTC",
              "api_key":"secret-key","token":"secret-token","authorization":"secret-header"}
    bound = dl._bind_physical_response(json.loads(body), model="icon_global",
        url="https://username:password@example.com/v1/forecast?api_key=secret-key",
        params=params, run=datetime(2026,6,8,tzinfo=UTC), captures=[(body,datetime(2026,6,8,4,tzinfo=UTC).timestamp())])
    evidence = json.dumps(bound[dl._BATCH_PHYSICAL_RESPONSE_KEY])
    for secret in ("secret-key","secret-token","secret-header","username","password"):
        assert secret not in evidence


@pytest.mark.parametrize("city", ("Hong Kong", "Chicago"))
def test_station_ground_facts_identity_excludes_audit_but_cutoff_requires_possession(tmp_path, monkeypatch, city):
    from dataclasses import dataclass
    import src.config as config
    from tests.test_station_ground_evidence import _setup, _archive
    from src.data.replacement_forecast_materializer import _bind_provider_geometry_identity
    from src.data.replacement_forecast_cycle_policy import _anchor_station_ground_has_authority

    db, _, _, _, _ = _setup(tmp_path, monkeypatch, city)
    entity = _archive(db, city)
    station = config.runtime_station_geometry_for_city(config.runtime_cities_by_name()[city])
    assert station["ground_status"] == "VERIFIED"
    @dataclass(frozen=True)
    class Shape:
        shape_hash: str = "current-shape"
        provider_geometry_evidence: object = None
        provider_geometry_identity_hash: object = None
        provider_geometry_audit: object = None
    @dataclass(frozen=True)
    class Metadata:
        city: str
        station_id: str
        station_lat: float
        station_lon: float
        station_elevation_m: float
        source_geometry_proof: object
    proof = {"revision": "openmeteo_ifs9_o1280_source_cell_v1", "station_registry_sha256": "a" * 64,
        "station_ground_proof": {"revision": "station_ground_roles_v1", "status": "VERIFIED", "reason": None,
            "facts": station["ground_facts"], "audit": station["ground_audit"]}}
    metadata = Metadata(city, str(station["station_id"]), float(station["lat"]), float(station["lon"]),
        float(station["ground_elevation_m"]), proof)
    bound = _bind_provider_geometry_identity(Shape(), {}, anchor_metadata=metadata, decision_at="2026-09-29T22:00:00+00:00", station_ground_evidence=entity)
    assert _anchor_station_ground_has_authority(bound.provider_geometry_evidence, bound.provider_geometry_audit, "2026-09-29T22:00:00Z")
    assert not _anchor_station_ground_has_authority(bound.provider_geometry_evidence, bound.provider_geometry_audit)
    assert not _anchor_station_ground_has_authority(bound.provider_geometry_evidence, bound.provider_geometry_audit, "2026-09-29T04:00:00Z")
    old = _bind_provider_geometry_identity(Shape(), {}, anchor_metadata=metadata, decision_at="2026-09-29T04:00:00Z", station_ground_evidence=entity)
    assert not _anchor_station_ground_has_authority(old.provider_geometry_evidence, old.provider_geometry_audit, "2026-09-29T04:00:00Z")
    changed_audit = json.loads(json.dumps(proof))
    changed_audit["station_registry_sha256"] = "b" * 64
    changed_audit["station_ground_proof"]["audit"]["body_sha256"] = "c" * 64
    changed_audit["station_ground_proof"]["audit"]["checked_at"] = "2026-09-29T21:30:00Z"
    other = _bind_provider_geometry_identity(Shape(), {}, anchor_metadata=replace(metadata, source_geometry_proof=changed_audit),
        decision_at="2026-09-29T22:00:00Z", station_ground_evidence=entity)
    assert other.provider_geometry_identity_hash == bound.provider_geometry_identity_hash
    # Audit labels are not authority. The normal canonical entity above owns
    # real original bytes, source capture and independent DB possession.
    assert _anchor_station_ground_has_authority(other.provider_geometry_evidence, other.provider_geometry_audit, "2026-09-29T22:00:00Z")
    assert other.shape_hash == bound.shape_hash
    missing = _bind_provider_geometry_identity(Shape(), {}, anchor_metadata=metadata, decision_at="2026-09-29T22:00:00Z")
    assert not _anchor_station_ground_has_authority(missing.provider_geometry_evidence, missing.provider_geometry_audit, "2026-09-29T22:00:00Z")
    changed_audit["station_ground_proof"]["facts"]["elevation_m"] = 33.0
    changed = _bind_provider_geometry_identity(Shape(), {}, anchor_metadata=replace(metadata, source_geometry_proof=changed_audit),
        decision_at="2026-09-29T22:00:00Z", station_ground_evidence=entity)
    assert changed.shape_hash != bound.shape_hash
    assert not _anchor_station_ground_has_authority(changed.provider_geometry_evidence, changed.provider_geometry_audit, "2026-09-29T22:00:00Z")


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("latest_location_damage", (None, "missing", "foreign_claim", "duplicate"))
def test_same_issued_archive_appends_proof_without_rewriting_legacy_raw(tmp_path, monkeypatch, metric, latest_location_damage):
    from src.data import bayes_precision_fusion_download as dl
    from src.data.replacement_current_value_serving import read_current_instrument_values
    conn, target, cycle = _current_rows(tmp_path, monkeypatch, metric=metric)
    for model, in conn.execute("SELECT model FROM raw_model_forecasts").fetchall():
        old_hash = dl._model_domain_hash(provider=dl.OPENMETEO_PROVIDER,
            model_name=dl.OPENMETEO_MODEL_IDS.get(model, model), cell_selection="land",
            elevation_param="requested", downscaling_policy="none", endpoint_mode="single_runs")
        conn.execute("UPDATE raw_model_forecasts SET artifact_id=NULL,raw_sha256=NULL,elevation_param='requested',"
            "downscaling_policy='none',model_domain_hash=?,recorded_at=? WHERE model=?",
            (old_hash, cycle.replace(hour=4).isoformat(), model))
    conn.execute("DELETE FROM raw_forecast_artifacts")
    conn.commit()
    original = conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id").fetchall()
    scope = dict(city=target.city, metric=metric, target_date=target.target_date)
    assert read_current_instrument_values(conn, **scope, source_cycle_time_iso=cycle.isoformat(),
        decision_time_iso=cycle.replace(hour=5).isoformat()) == {}
    _download_time(monkeypatch, dl, cycle.replace(hour=8))
    _mock_single_model_http(monkeypatch, dl, value=20.0)
    report = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=Path(conn.execute("PRAGMA database_list").fetchone()[2]), cycle=cycle,
        targets=[target], models=("icon_global", "ukmo_global_deterministic_10km"),
        frozen_source_runs={model: dl._DerivedOffGridSingleRunsRun(run=cycle)
            for model in ("icon_global", "ukmo_global_deterministic_10km")},
        include_previous_runs=False, prune_after=False, revalidate_legacy_capture=True,
    )
    assert report["written_row_count"] == 0
    assert conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id").fetchall() == original
    assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 2
    assert read_current_instrument_values(conn, **scope, source_cycle_time_iso=cycle.isoformat(),
        decision_time_iso=cycle.replace(hour=5).isoformat()) == {}
    served = read_current_instrument_values(conn, **scope, source_cycle_time_iso=cycle.isoformat(),
        decision_time_iso=cycle.replace(hour=9).isoformat())
    assert set(served) == {"icon_global", "ukmo_global_deterministic_10km"}
    for value in served.values():
        assert value.value_c == 20.0
        assert value.served_cycle == cycle.isoformat()
        assert value.captured_at == cycle.replace(hour=4).isoformat()
        assert value.physical_response["revalidated_legacy_product"] is True
        assert value.physical_response["raw_model_forecast_id"] == value.raw_model_forecast_id
        assert value.physical_response["proof_captured_at"] == cycle.replace(hour=8).isoformat()
    assert read_current_instrument_values(conn, **scope, source_cycle_time_iso=cycle.isoformat()) == {}
    assert read_current_instrument_values(conn, **scope, source_cycle_time_iso=cycle.isoformat(),
        decision_time_iso=(cycle + timedelta(hours=31)).isoformat()) == {}
    _persist_exact_provider_body(conn,tmp_path,city=target.city,metric=metric,target_date=target.target_date,
        model="icon_global",cycle=cycle.isoformat(),captured=cycle.replace(hour=10).isoformat(),
        value=21.0,expected_written=0)
    if latest_location_damage is not None:
        artifact_id, metadata_json = conn.execute("SELECT artifact_id,artifact_metadata_json FROM raw_forecast_artifacts "
            "WHERE product_id LIKE '%icon_global%' ORDER BY artifact_id DESC LIMIT 1").fetchone()
        metadata = json.loads(metadata_json)
        locations = metadata["physical_response"]["locations"]
        if latest_location_damage == "missing":
            metadata["physical_response"]["locations"] = []
        elif latest_location_damage == "foreign_claim":
            locations[0]["requested_latitude"] = 0.0
            locations[0]["requested_longitude"] = 0.0
            locations[0]["timezone"] = "UTC"
        else:
            metadata["physical_response"]["locations"] = locations * 2
        conn.execute("UPDATE raw_forecast_artifacts SET artifact_metadata_json=? WHERE artifact_id=?",
            (json.dumps(metadata), artifact_id))
    conn.commit()
    # Latest same-family bytes differ from immutable value: do not hide this
    # mismatch behind the older valid derived witness.
    changed=read_current_instrument_values(conn,**scope,source_cycle_time_iso=cycle.isoformat(),
        decision_time_iso=cycle.replace(hour=11).isoformat())
    assert set(changed)=={"ukmo_global_deterministic_10km"}
    assert conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id").fetchall()==original
    conn.close()


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("latest_damage", (None, "missing_body", "foreign_claim", "tampered_receipt"))
def test_real_http_a_b_a_receipts_reset_without_renewing_immutable_raw_or_body(tmp_path, monkeypatch, metric, latest_damage):
    from src.data import bayes_precision_fusion_download as dl
    from src.data.replacement_current_value_serving import read_current_instrument_values
    from src.data.replacement_input_hwm import _exact_current_value_serving_lag
    conn, target, cycle = _current_rows(tmp_path, monkeypatch, metric=metric)
    old_hash = dl._model_domain_hash(provider=dl.OPENMETEO_PROVIDER, model_name="icon_global",
        cell_selection="land", elevation_param="requested", downscaling_policy="none", endpoint_mode="single_runs")
    conn.execute("UPDATE raw_model_forecasts SET artifact_id=NULL,raw_sha256=NULL,elevation_param='requested',"
        "downscaling_policy='none',model_domain_hash=? WHERE model='icon_global'", (old_hash,))
    conn.execute("DELETE FROM raw_forecast_artifacts WHERE product_id LIKE '%icon_global%'")
    conn.commit()
    raw_before = conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id").fetchall()
    def recapture(hour, value, *, network=True):
        _download_time(monkeypatch, dl, cycle.replace(hour=hour))
        _mock_single_model_http(monkeypatch, dl, value=value, network=network)
        result = dl.download_bayes_precision_fusion_extra_raw_inputs(
            forecast_db=Path(conn.execute("PRAGMA database_list").fetchone()[2]), cycle=cycle,
            targets=[target], models=("icon_global",), include_previous_runs=False, prune_after=False,
            frozen_source_runs={"icon_global": dl._DerivedOffGridSingleRunsRun(run=cycle)}, revalidate_legacy_capture=True)
        assert result["written_row_count"] == 0
        if network:
            assert (target.city, target.target_date, metric) in result["committed_families"]
        assert conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id").fetchall() == raw_before
    def current(hour):
        return read_current_instrument_values(conn, city=target.city, metric=metric, target_date=target.target_date,
            source_cycle_time_iso=cycle.isoformat(), decision_time_iso=cycle.replace(hour=hour).isoformat())
    recapture(8, 20)
    original = current(9)["icon_global"]
    body_id = original.physical_response["artifact_id"]
    body_before = conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (body_id,)).fetchone()
    # Another ordinary capture writer learns a changed provider answer. The
    # recovery producer must not hide this behind its older equal-value body.
    _persist_exact_provider_body(conn, tmp_path, city=target.city, metric=metric, target_date=target.target_date,
        model="icon_global", cycle=cycle.isoformat(), captured=cycle.replace(hour=10).isoformat(),
        value=21, expected_written=0, network=True)
    conn.commit()
    assert "icon_global" not in current(11)
    recapture(12, 20)
    restored = current(13)["icon_global"]
    assert restored.raw_model_forecast_id == original.raw_model_forecast_id
    assert restored.captured_at == original.captured_at == cycle.replace(hour=4).isoformat()
    assert restored.physical_response["proof_captured_at"] == cycle.replace(hour=12).isoformat()
    assert restored.physical_response["capture_receipt_artifact_id"] != original.physical_response["capture_receipt_artifact_id"]
    assert conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (body_id,)).fetchone() == body_before
    assert "icon_global" not in current(11)  # New possession cannot rewrite the historical cutoff.
    assert current(9)["icon_global"].physical_response == original.physical_response
    provenance = {"bayes_precision_fusion": {"used_models": ["icon_global"],
        "current_value_serving": {"icon_global": original.as_provenance()}}}
    lag = _exact_current_value_serving_lag(conn, city=target.city, target_date=target.target_date,
        metric=metric, decision_time=cycle.replace(hour=13), posterior_computed_at=cycle.replace(hour=9), provenance=provenance)
    assert lag[0] and "physical_proof_dependency_changed" in lag[1]
    provenance["bayes_precision_fusion"]["current_value_serving"]["icon_global"] = restored.as_provenance()
    reset_lag = _exact_current_value_serving_lag(conn, city=target.city, target_date=target.target_date,
        metric=metric, decision_time=cycle.replace(hour=13), posterior_computed_at=cycle.replace(hour=13), provenance=provenance)
    assert reset_lag[0] and reset_lag[1] is None, reset_lag
    receipt_count = conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts WHERE data_version='openmeteo_single_model_http_capture_receipt_v1'").fetchone()[0]
    assert receipt_count == 3
    recapture(14, 20, network=False)
    assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts WHERE data_version='openmeteo_single_model_http_capture_receipt_v1'").fetchone()[0] == receipt_count
    assert current(15)["icon_global"].physical_response["capture_receipt_artifact_id"] == restored.physical_response["capture_receipt_artifact_id"]
    if latest_damage is not None:
        artifact_id = restored.physical_response["capture_receipt_artifact_id"]
        path, metadata = conn.execute("SELECT artifact_path,artifact_metadata_json FROM raw_forecast_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
        if latest_damage == "tampered_receipt":
            Path(path).write_bytes(Path(path).read_bytes() + b" ")
        else:
            parsed = json.loads(metadata)
            receipt = parsed["physical_http_capture_receipt"]
            if latest_damage == "missing_body":
                receipt["body_artifact_id"] = 999999
            else:
                receipt["physical_response"]["locations"][0]["requested_latitude"] = 0
            conn.execute("UPDATE raw_forecast_artifacts SET artifact_metadata_json=? WHERE artifact_id=?", (json.dumps(parsed), artifact_id))
            conn.commit()
        assert "icon_global" not in current(15)  # Latest bad event cannot uncover A08.
    conn.close()


@pytest.mark.parametrize("first_metric", ("high", "low"))
@pytest.mark.parametrize("cache_kind", ("exact", "superset", "old_persisted_superset"))
def test_bpf_payload_cache_reuse_cannot_mint_a_network_receipt_for_metric_twin(tmp_path, monkeypatch, first_metric, cache_kind):
    from src.data import bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client
    db = _forecast_db(tmp_path)
    run = datetime(2026, 6, 8, tzinfo=UTC)
    target = replace(_target(), metric=first_metric)
    monkeypatch.setattr("src.config.state_path", lambda filename: tmp_path / "state" / filename)
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {target.city: SimpleNamespace(name=target.city,
        lat=target.latitude,lon=target.longitude,timezone=target.timezone_name)})
    cache_path = tmp_path / "payload_cache.json"
    monkeypatch.setattr(dl, "_single_runs_payload_cache_persistence_enabled", lambda: True)
    monkeypatch.setattr(dl, "_single_runs_payload_cache_path", lambda: cache_path)
    _download_time(monkeypatch, dl, run.replace(hour=8))
    _mock_single_model_http(monkeypatch, dl, value=20, network=True, hour_count=120)
    network_calls = []
    original_fetch = client.fetch
    def counted(*args, **kwargs):
        network_calls.append(1)
        return original_fetch(*args, **kwargs)
    monkeypatch.setattr(client, "fetch", counted)
    dl.download_bayes_precision_fusion_extra_raw_inputs(forecast_db=db, cycle=run, targets=[target],
        models=("icon_global",), forecast_hours=120, include_previous_runs=False, prune_after=False)
    conn = sqlite3.connect(db)
    first_body = conn.execute("SELECT * FROM raw_forecast_artifacts WHERE data_version='openmeteo_single_model_entity_body_v1'").fetchone()
    assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts WHERE data_version='openmeteo_single_model_http_capture_receipt_v1'").fetchone()[0] == 1
    if cache_kind == "old_persisted_superset":
        cached = json.loads(cache_path.read_text())
        for entry in cached["entries"].values():
            entry["payload"][dl._BATCH_PHYSICAL_RESPONSE_KEY]["network_capture"] = {
                "captured_at": run.replace(hour=8).isoformat(), "response_headers": {"content-type": "application/json"}}
        cache_path.write_text(json.dumps(cached))
        dl._SINGLE_RUNS_PAYLOAD_CACHE.clear()
        dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEX.clear()
        dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEXED_KEYS.clear()
        dl._SINGLE_RUNS_PAYLOAD_CACHE_RECORDED_AT.clear()
        dl._load_persisted_single_runs_payload_cache(force=True)
    _download_time(monkeypatch, dl, run.replace(hour=14))
    twin = replace(target, metric="low" if first_metric == "high" else "high")
    result = dl.download_bayes_precision_fusion_extra_raw_inputs(forecast_db=db, cycle=run, targets=[twin],
        models=("icon_global",), forecast_hours=120 if cache_kind == "exact" else 72,
        include_previous_runs=False, prune_after=False)
    assert result["written_row_count"] == 1 and len(network_calls) == 1
    assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts WHERE data_version='openmeteo_single_model_http_capture_receipt_v1'").fetchone()[0] == 1
    assert conn.execute("SELECT * FROM raw_forecast_artifacts WHERE data_version='openmeteo_single_model_entity_body_v1'").fetchone() == first_body
    assert {row[0] for row in conn.execute("SELECT captured_at FROM raw_model_forecasts")} == {run.replace(hour=8).isoformat()}
    assert {row[0] for row in conn.execute("SELECT source_available_at FROM raw_model_forecasts")} == {run.replace(hour=8).isoformat()}
    conn.close()


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("clock", ("captured_at", "source_available_at", "recorded_at"))
def test_latest_same_family_malformed_receipt_clock_is_not_hidden_by_old_body(tmp_path, monkeypatch, metric, clock):
    from src.data.replacement_current_value_serving import read_current_instrument_values
    conn, target, cycle = _current_rows(tmp_path, monkeypatch, metric=metric)
    original = conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id").fetchall()
    def current(hour):
        return read_current_instrument_values(conn, city=target.city, metric=metric, target_date=target.target_date,
            source_cycle_time_iso=cycle.isoformat(), decision_time_iso=cycle.replace(hour=hour).isoformat())
    assert "icon_global" in current(5)
    _persist_exact_provider_body(conn, tmp_path, city=target.city, metric=metric, target_date=target.target_date,
        model="icon_global", cycle=cycle.isoformat(), captured=cycle.replace(hour=10).isoformat(),
        value=21, expected_written=0, network=True)
    conn.commit()
    assert "icon_global" not in current(11)
    receipt_id = conn.execute("SELECT MAX(artifact_id) FROM raw_forecast_artifacts WHERE data_version='openmeteo_single_model_http_capture_receipt_v1'").fetchone()[0]
    conn.execute(f"UPDATE raw_forecast_artifacts SET {clock}='broken-clock' WHERE artifact_id=?", (receipt_id,))
    conn.commit()
    assert "icon_global" not in current(11)
    assert "icon_global" in current(5)
    _persist_exact_provider_body(conn, tmp_path, city=target.city, metric=metric, target_date=target.target_date,
        model="icon_global", cycle=cycle.isoformat(), captured=cycle.replace(hour=12).isoformat(),
        value=20, expected_written=0, network=True)
    conn.commit()
    assert current(13)["icon_global"].value_c == 20
    assert conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id").fetchall() == original
    conn.close()


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("future_microsecond", (900000, 101))
def test_future_receipt_preserves_prior_value_at_subsecond_cutoff(tmp_path, monkeypatch, metric, future_microsecond):
    from src.data.replacement_current_value_serving import read_current_instrument_values
    conn, target, cycle = _current_rows(tmp_path, monkeypatch, metric=metric)
    cutoff = cycle.replace(hour=10, microsecond=100)
    future = cutoff.replace(microsecond=future_microsecond)
    _persist_exact_provider_body(conn, tmp_path, city=target.city, metric=metric, target_date=target.target_date,
        model="icon_global", cycle=cycle.isoformat(), captured=future.isoformat(),
        value=21, expected_written=0, network=True)
    conn.commit()
    def current(decision):
        return read_current_instrument_values(conn, city=target.city, metric=metric, target_date=target.target_date,
            source_cycle_time_iso=cycle.isoformat(), decision_time_iso=decision.isoformat())
    assert current(cutoff)["icon_global"].value_c == 20
    assert "icon_global" not in current(future)
    conn.close()


@pytest.mark.parametrize("metric", ("high", "low"))
def test_blocked_seed_fingerprint_tracks_same_raw_proof_possession_at_its_cutoff(tmp_path, monkeypatch, metric):
    from src.data import bayes_precision_fusion_download as dl
    from src.data.replacement_forecast_live_materialization_queue import _blocked_attempt_fingerprint
    from src.data.replacement_forecast_seed_discovery import _seed_name
    conn, target, cycle = _current_rows(tmp_path, monkeypatch, metric=metric)
    db = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    original_raw = conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id").fetchall()
    target_scope = {"city": target.city, "target_date": target.target_date, "temperature_metric": metric}
    def fingerprint(hour):
        payload = {**target_scope, "source_cycle_time": cycle.isoformat(), "computed_at": cycle.replace(hour=hour).isoformat()}
        return _blocked_attempt_fingerprint(input_json=tmp_path / _seed_name(target_scope, computed_at=cycle.replace(hour=hour)),
            forecast_db=db, payload=payload)
    old = fingerprint(5)
    assert old is not None and fingerprint(6) == old  # Merely ticking is not new evidence.
    raw_cursor = conn.execute("SELECT * FROM raw_model_forecasts WHERE model='icon_global'")
    raw = dict(zip((field[0] for field in raw_cursor.description), raw_cursor.fetchone(), strict=True))
    body_path, body_params, request_url = conn.execute("SELECT artifact_path,request_params_json,request_url FROM raw_forecast_artifacts WHERE artifact_id=?", (raw["artifact_id"],)).fetchone()
    entity = Path(body_path).read_bytes()
    captured = cycle.replace(hour=8)
    _download_time(monkeypatch, dl, captured)
    bound = dl._bind_physical_response(json.loads(entity), model="icon_global", url=request_url,
        params=json.loads(body_params), run=cycle, captures=[(entity, captured.timestamp())],
        network_captures=[(entity, captured.timestamp(), {"content-type": "application/json"})])
    body_count = conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts WHERE data_version='openmeteo_single_model_entity_body_v1'").fetchone()[0]
    assert dl._persist_rows(conn, [{**raw, "captured_at": captured.isoformat(), "source_available_at": captured.isoformat(),
        "recorded_at": captured.isoformat(), "_physical_response": bound[dl._BATCH_PHYSICAL_RESPONSE_KEY]}]) == 0
    conn.commit()
    refreshed = fingerprint(9)
    assert refreshed is not None and refreshed != old
    assert fingerprint(5) == old  # New possession never changes the frozen old decision.
    assert fingerprint(10) == refreshed
    assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts WHERE data_version='openmeteo_single_model_entity_body_v1'").fetchone()[0] == body_count
    assert conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id").fetchall() == original_raw
    assert _seed_name(target_scope, computed_at=cycle.replace(hour=9)) != _seed_name(target_scope, computed_at=cycle.replace(hour=5))
    conn.close()


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("count,future", ((65, True), (1000, False), (7200, False)))
def test_physical_receipt_scan_is_complete_streamed_and_future_group_does_not_revoke_prior_cut(tmp_path, monkeypatch, metric, count, future):
    import time
    import tracemalloc
    from src.data import replacement_current_value_serving as serving
    conn, target, cycle = _current_rows(tmp_path, monkeypatch, metric=metric)
    original_body = conn.execute("SELECT artifact_id FROM raw_model_forecasts WHERE model='icon_global'").fetchone()[0]
    cursor = conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (original_body,))
    original = dict(zip((field[0] for field in cursor.description), cursor.fetchone(), strict=True))
    # Controlled candidate catalog: only the original has valid body proof.
    # Later malformed receipts must be selected and rejected, never hidden.
    columns = [key for key in original if key != "artifact_id"]
    for index in range(count):
        captured = cycle.replace(hour=10 if future else 8, microsecond=index+1)
        candidate = {**original, "data_version": "openmeteo_single_model_http_capture_receipt_v1",
            "sha256": f"{index+1:064x}", "captured_at": captured.isoformat(),
            "source_available_at": captured.isoformat(), "recorded_at": captured.isoformat()}
        if not future and index == count-1:
            candidate["captured_at"] = "broken-latest-clock"
        conn.execute(f"INSERT INTO raw_forecast_artifacts ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            tuple(candidate[key] for key in columns))
    foreign = {**candidate, "sha256": "f"*64, "request_params_json": json.dumps({"latitude":0,"longitude":0,"timezone":"UTC"})}
    conn.execute(f"INSERT INTO raw_forecast_artifacts ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        tuple(foreign[key] for key in columns))
    conn.commit()
    schema = serving.current_value_serving_schema(conn)
    decision = cycle.replace(hour=9 if not future else 10)
    identity = conn.execute(f"SELECT {serving._product_identity_select(schema, decision_iso=decision.isoformat())} FROM raw_model_forecasts WHERE model='icon_global'").fetchone()[0]
    tracemalloc.start()
    start = time.perf_counter()
    selected = json.loads(serving._read_product_identity_at_cutoff(conn, identity))
    elapsed = time.perf_counter()-start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(json.dumps({"same_family_candidates":count,"future":future,"elapsed_seconds":elapsed,"peak_bytes":peak}))
    if future:
        assert selected["physical_artifact"]["artifact_id"] == original_body
        assert serving.read_current_instrument_values(conn, city=target.city, metric=metric, target_date=target.target_date,
            source_cycle_time_iso=cycle.isoformat(), decision_time_iso=decision.isoformat())["icon_global"].value_c == 20
    else:
        assert selected["physical_artifact"]["captured_at"] == "broken-latest-clock"
        assert not serving._source_clock_product_has_authority(json.dumps(selected), lead_days=1)
    assert peak < 2_000_000  # Retain a batch and best, not the whole catalog.
    conn.close()


def test_interrupted_physical_receipt_scan_never_returns_an_older_best(tmp_path, monkeypatch):
    from src.data import replacement_current_value_serving as serving
    conn, target, cycle = _current_rows(tmp_path, monkeypatch)
    schema = serving.current_value_serving_schema(conn)
    identity = conn.execute(f"SELECT {serving._product_identity_select(schema, decision_iso=cycle.replace(hour=5).isoformat())} FROM raw_model_forecasts WHERE model='icon_global'").fetchone()[0]
    ticks = iter((0.0,0.0,3.0))
    monkeypatch.setattr(serving.time, "monotonic", lambda:next(ticks))
    with pytest.raises(serving.CurrentValueServingReadUnavailable, match="scan_budget_exceeded"):
        serving._read_product_identity_at_cutoff(conn, identity)
    conn.close()


@pytest.mark.parametrize("metric", ("high", "low"))
def test_current_reader_binds_actual_model_surface_and_recovers_bad_asset_causally(tmp_path, monkeypatch, metric):
    from src.data import bayes_precision_fusion_download as dl
    from src.data import openmeteo_model_surface as surface
    from src.data.replacement_current_value_serving import read_current_instrument_values, physical_source_proof_dependency
    conn, target, cycle = _current_rows(tmp_path, monkeypatch, metric=metric)
    def current(hour):
        return read_current_instrument_values(conn, city=target.city, metric=metric, target_date=target.target_date,
            source_cycle_time_iso=cycle.isoformat(), decision_time_iso=cycle.replace(hour=hour).isoformat())
    original = current(5)["icon_global"]
    proof = original.physical_response
    witness = proof["model_surface_witness"]
    assert witness["status"] == "VERIFIED" and witness["geometry"]["native_surface"] == "LAND"
    assert witness["geometry"]["native_grid_elevation_m"] == 6
    assert proof["target_dem_elevation_m"] == 123  # Distinct physical roles, no DEM-as-native.
    assert physical_source_proof_dependency(proof)["model_surface_asset"]["whole_sha256"] == witness["asset_audit"]["whole_sha256"]
    # A damaged proof manifest requires new possession. Repairing only the
    # known whole-byte cache would correctly retain its original clock.
    Path(witness["asset_audit"]["manifest_path"]).write_bytes(b"broken-owned-static-fixture")
    assert "icon_global" not in current(5)
    assert "ukmo_global_deterministic_10km" in current(5)  # Exact model, not global degradation.
    _download_time(monkeypatch, dl, cycle.replace(hour=6))
    restored_asset = surface.ensure_model_surface("icon_global")
    assert restored_asset.status == "READY"
    assert "icon_global" not in current(5)  # Later possession cannot repair the old decision.
    restored = current(7)["icon_global"]
    assert restored.raw_model_forecast_id == original.raw_model_forecast_id
    assert restored.physical_response["entity_body_sha256"] == proof["entity_body_sha256"]
    assert physical_source_proof_dependency(restored.physical_response) != physical_source_proof_dependency(proof)
    conn.close()
@pytest.mark.parametrize("damage", (None,"wrong_first_site"))
def test_single_model_location_batch_persists_and_serves_second_city_both_metrics(tmp_path,monkeypatch,damage):
    from src.data import bayes_precision_fusion_download as dl
    from src.data.replacement_current_value_serving import read_current_instrument_values
    from datetime import date
    run=datetime(2026,6,8,tzinfo=UTC)
    captured=run.replace(hour=4)
    db=_forecast_db(tmp_path)
    monkeypatch.setattr("src.config.state_path",lambda filename:tmp_path/"state"/filename)
    points={"Paris":SimpleNamespace(lat=48.967,lon=2.428,timezone="Europe/Paris"),
        "London":SimpleNamespace(lat=51.51,lon=-0.01,timezone="Europe/London")}
    monkeypatch.setattr("src.config.runtime_cities_by_name",lambda:points)
    _download_time(monkeypatch,dl,captured)
    dl._SINGLE_RUNS_PAYLOAD_CACHE.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEX.clear()
    def fetch(_url,params,**kwargs):
        assert params["models"]=="icon_global"
        payload=[]
        for lat,lon,tz in zip(str(params["latitude"]).split(","),str(params["longitude"]).split(","),str(params["timezone"]).split(","),strict=True):
            day=datetime(2026,6,9)
            latitude,longitude=float(lat),float(lon)
            latitude,longitude=_selected_test_cell("icon_global",latitude,longitude)
            if damage=="wrong_first_site" and tz=="Europe/Paris":
                latitude,longitude=0,0
            payload.append({"latitude":latitude,"longitude":longitude,"elevation":100,
                "timezone":tz,"utc_offset_seconds":7200 if tz=="Europe/Paris" else 3600,
                "hourly_units":{"temperature_2m":"°C"},
                "hourly":{"time":[(day+timedelta(hours=i)).isoformat(timespec="minutes") for i in range(24)],
                          "temperature_2m":[15+i%7 for i in range(24)]}})
        body=(json.dumps(payload,indent=2)+"\n").encode()
        kwargs["capture_entity_body"](body,captured.timestamp())
        return json.loads(body)
    monkeypatch.setattr("src.data.openmeteo_client.fetch",fetch)
    targets=[dl.BayesPrecisionFusionDownloadTarget(city=city,metric=metric,target_date="2026-06-09",lead_days=1,
        latitude=point.lat,longitude=point.lon,timezone_name=point.timezone)
        for city,point in points.items() for metric in ("high","low")]
    report=dl.download_bayes_precision_fusion_extra_raw_inputs(forecast_db=db,cycle=run,targets=targets,
        models=("icon_global",),frozen_source_runs={"icon_global":dl._DerivedOffGridSingleRunsRun(run=run)},
        include_previous_runs=False,prune_after=False,allow_single_runs_fallback=False)
    assert report["written_row_count"]==4
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(DISTINCT artifact_id) FROM raw_model_forecasts").fetchone()[0]==1
        for city in points:
            for metric,expected in (("high",21),("low",15)):
                served=read_current_instrument_values(conn,city=city,metric=metric,target_date="2026-06-09",
                    source_cycle_time_iso=run.isoformat(),decision_time_iso=run.replace(hour=5).isoformat())
                if city=="Paris" and damage:
                    assert served=={}
                else:
                    assert served["icon_global"].value_c==expected
                    assert served["icon_global"].physical_response["requested_latitude"]==points[city].lat
        metadata,params=conn.execute("SELECT artifact_metadata_json,request_params_json FROM raw_forecast_artifacts").fetchone()
        metadata,params=json.loads(metadata),json.loads(params)
        for key in ("latitude","longitude","timezone"):
            params[key]=",".join([str(params[key]).split(",")[0]]*2)
        metadata["physical_response"]["request_params"]=params
        conn.execute("UPDATE raw_forecast_artifacts SET artifact_metadata_json=?,request_params_json=?",
            (json.dumps(metadata),json.dumps(params)))
        # Ambiguous duplicate requested locations cannot bind either row to
        # a unique response location, even with an intact entity-body hash.
        for city in points:
            assert read_current_instrument_values(conn,city=city,metric="high",target_date="2026-06-09",
                source_cycle_time_iso=run.isoformat(),decision_time_iso=run.replace(hour=5).isoformat())=={}


@pytest.mark.parametrize("provider",("hko","cwa"))
@pytest.mark.parametrize("layer",("raw","artifact","proof"))
def test_station_availability_clock_cannot_be_replaced_with_earlier_publish_clock(tmp_path,monkeypatch,provider,layer):
    from tests.test_station_forecast_live_ingest_wiring import _station_body_writer
    from src.data.replacement_current_value_serving import read_current_instrument_values
    monkeypatch.setattr("src.config.state_path",lambda filename:tmp_path/"state"/filename)
    conn,_body,city,models=_station_body_writer(monkeypatch,provider)
    scope=dict(city=city,metric="high",target_date="2026-07-24",
        source_cycle_time_iso="2026-07-23T06:00:00+00:00",decision_time_iso="2026-07-23T10:16:00+00:00",
        include_station_sources=True)
    assert models["high"] in read_current_instrument_values(conn,**scope)
    if layer=="raw":
        conn.execute("UPDATE raw_model_forecasts SET source_available_at=source_cycle_time")
    elif layer=="artifact":
        conn.execute("UPDATE raw_forecast_artifacts SET source_available_at=source_cycle_time")
    else:
        for artifact_id,raw in conn.execute("SELECT artifact_id,artifact_metadata_json FROM raw_forecast_artifacts").fetchall():
            evidence=json.loads(raw)
            for proof in evidence["station_response"]["items"]:
                proof["source_available_at"]=proof["source_cycle_time"]
            conn.execute("UPDATE raw_forecast_artifacts SET artifact_metadata_json=? WHERE artifact_id=?",(json.dumps(evidence),artifact_id))
    assert read_current_instrument_values(conn,**scope)=={}
    conn.close()


@pytest.mark.parametrize("metric",("high","low"))
def test_registered_cwa_quantity_cannot_be_served_as_its_opposite_metric(tmp_path,monkeypatch,metric):
    from tests.test_station_forecast_live_ingest_wiring import _station_body_writer
    from src.data.replacement_current_value_serving import read_current_instrument_values
    monkeypatch.setattr("src.config.state_path",lambda filename:tmp_path/"state"/filename)
    conn,_body,city,models=_station_body_writer(monkeypatch,"cwa")
    wrong="low" if metric=="high" else "high"
    conn.execute("UPDATE raw_model_forecasts SET metric=? WHERE model=?",(wrong,models[metric]))
    served=read_current_instrument_values(conn,city=city,metric=wrong,target_date="2026-07-24",
        source_cycle_time_iso="2026-07-23T06:00:00+00:00",decision_time_iso="2026-07-23T10:16:00+00:00",
        include_station_sources=True)
    assert set(served)=={models[wrong]}
    conn.close()


def test_normal_held_revision_missing_producer_recaptures_original_archive_cycle(tmp_path, monkeypatch):
    from src.data import bayes_precision_fusion_download as dl
    from src.data.replacement_current_value_serving import read_current_instrument_values
    from tests.test_bayes_precision_fusion_download import _real_capture_world
    world = _real_capture_world(tmp_path, monkeypatch, "single")
    target = world.targets[0]
    with sqlite3.connect(world.db) as conn:
        raw_id, body_id = conn.execute("SELECT raw_model_forecast_id,artifact_id FROM raw_model_forecasts WHERE city=?", (target.city,)).fetchone()
        domain = dl._model_domain_hash(provider=dl.OPENMETEO_PROVIDER, model_name="icon_global",
            cell_selection="land", elevation_param="requested", downscaling_policy="none", endpoint_mode="single_runs")
        conn.execute("UPDATE raw_model_forecasts SET artifact_id=NULL,raw_sha256=NULL,elevation_param='requested',"
            "downscaling_policy='none',model_domain_hash=? WHERE raw_model_forecast_id=?", (domain,raw_id))
        conn.execute("DELETE FROM raw_forecast_artifacts WHERE artifact_id=? OR (data_version='openmeteo_single_model_http_capture_receipt_v1'"
            " AND json_extract(artifact_metadata_json,'$.physical_http_capture_receipt.body_artifact_id')=?)", (body_id,body_id))
        original = conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id").fetchall()
        original_cert = conn.execute("SELECT * FROM forecast_posteriors").fetchall()
    before_calls = len(world.calls)
    world.clock[0] = datetime(2026,9,29,23,30,tzinfo=UTC)
    original_get = world.provider.get
    def later_run_is_a_suffix(url, *, params=None, timeout=None):
        response = original_get(url, params=params, timeout=timeout)
        if params and params.get("run") != world.run.replace(tzinfo=None).isoformat():
            import httpx
            payload = response.json()
            issued = datetime.fromisoformat(params["run"]).replace(tzinfo=UTC)
            indices = [i for i, raw in enumerate(payload["hourly"]["time"])
                if (datetime.fromisoformat(raw)-timedelta(seconds=payload["utc_offset_seconds"])).replace(tzinfo=UTC)>=issued]
            payload["hourly"]={key:[values[i] for i in indices] for key,values in payload["hourly"].items()}
            return httpx.Response(200,json=payload,
                headers={key:value for key,value in response.headers.items() if key!="content-length"},request=response.request)
        return response
    monkeypatch.setattr(world.provider,"get",later_run_is_a_suffix)
    report = _normal_capture_producer(tmp_path, monkeypatch, world, planning_cycle=world.run+timedelta(hours=6))
    assert "coherent_archive_capture" in report, report
    assert report["coherent_archive_capture"]["attempted_target_group_count"] == 1
    assert report["written_row_count"] == 0
    assert report["committed_families"] == ((target.city,target.target_date,target.metric),)
    assert report["physical_capture_recovered_raw_ids"] == (raw_id,)
    assert len(world.calls) == before_calls + 2  # Exact old repair plus ordinary latest suffix.
    assert datetime.fromisoformat(world.calls[-2]["run"]).replace(tzinfo=UTC) == world.run
    with sqlite3.connect(world.db) as conn:
        assert conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id").fetchall()==original
        assert conn.execute("SELECT * FROM forecast_posteriors").fetchall()==original_cert
        assert conn.execute("SELECT DISTINCT source_cycle_time FROM raw_forecast_artifacts WHERE product_id LIKE '%icon_global%'").fetchall()==[(world.run.isoformat(),)]
        served=read_current_instrument_values(conn,city=target.city,metric=target.metric,target_date=target.target_date,
            source_cycle_time_iso=world.run.isoformat(),decision_time_iso=world.clock[0].isoformat())
        assert set(served)=={"icon_global"}
        assert served["icon_global"].physical_response["revalidated_legacy_product"]


def _normal_capture_producer(tmp_path, monkeypatch, world, *, planning_cycle=None):
    """Held-scope input and one configured provider; capture/authority remain real."""
    from src.data import bayes_precision_fusion_download as dl, replacement_forecast_production as production
    target = world.targets[0]
    _download_time(monkeypatch, production, world.clock[0])
    monkeypatch.setattr(dl, "BAYES_PRECISION_FUSION_EXTRA_MODELS", ("icon_global",))
    monkeypatch.setattr(dl, "BAYES_PRECISION_FUSION_CANDIDATE_ACCRUAL_MODELS", ())
    monkeypatch.setattr("src.data.replacement_forecast_seed_discovery.held_position_family_priorities",
        lambda **_: {(target.city, target.target_date, target.metric): 0})
    monkeypatch.setattr("src.data.replacement_forecast_current_target_plan.replacement_forecast_current_target_keys", lambda *_a, **_: ())
    return production._download_bayes_precision_fusion_extra_raw_inputs_if_needed(
        {"forecast_db":world.db,"raw_manifest_dir":tmp_path/"raw_manifests","seed_dir":tmp_path/"seeds"},
        planning_cycle=planning_cycle or world.run,max_wall_clock_seconds=5,include_previous_runs=False,prune_after=False)


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("damage", ("captured_at", "receipt_file"))
def test_normal_modern_held_proof_debt_insert_zero_progress_changes_only_new_cut(tmp_path, monkeypatch, metric, damage):
    from tests.test_bayes_precision_fusion_download import _real_capture_world
    from src.data import bayes_precision_fusion_download as dl
    from src.data.replacement_current_value_serving import read_current_instrument_values
    from src.data.replacement_input_hwm import _exact_current_value_serving_lag
    from src.data.replacement_forecast_live_materialization_queue import _blocked_attempt_fingerprint
    world = _real_capture_world(tmp_path, monkeypatch, "single", metric)
    target = world.targets[0]
    scope = dict(city=target.city,target_date=target.target_date,metric=metric,
        source_cycle_time_iso=world.run.isoformat())
    old_cut = datetime(2026,9,29,22,10,tzinfo=UTC)
    def fingerprint(cut):
        return _blocked_attempt_fingerprint(input_json=tmp_path/"input.json",forecast_db=world.db,
            payload={"city":target.city,"target_date":target.target_date,"temperature_metric":metric,
                "source_cycle_time":world.run.isoformat(),"computed_at":cut.isoformat()})
    old_fp = fingerprint(old_cut)
    with sqlite3.connect(world.db) as conn:
        original = read_current_instrument_values(conn,**scope,decision_time_iso=old_cut.isoformat())["icon_global"]
        cursor=conn.execute("SELECT * FROM raw_model_forecasts WHERE raw_model_forecast_id=?",(original.raw_model_forecast_id,))
        raw=dict(zip((column[0] for column in cursor.description),cursor.fetchone(),strict=True))
        body_path, request_url, params = conn.execute("SELECT artifact_path,request_url,request_params_json FROM raw_forecast_artifacts WHERE artifact_id=?",(raw["artifact_id"],)).fetchone()
        body=Path(body_path).read_bytes()
        world.clock[0]=datetime(2026,9,29,22,30,tzinfo=UTC)
        bound=dl._bind_physical_response(json.loads(body),model="icon_global",url=request_url,
            params=json.loads(params),run=world.run,captures=[(body,world.clock[0].timestamp())],
            network_captures=[(body,world.clock[0].timestamp(),{"content-type":"application/json"})])
        assert dl._persist_rows(conn,[{**raw,"source_available_at":world.clock[0].isoformat(),
            "captured_at":world.clock[0].isoformat(),"_physical_response":bound[dl._BATCH_PHYSICAL_RESPONSE_KEY]}])==0
        latest_id, latest_path = conn.execute("SELECT artifact_id,artifact_path FROM raw_forecast_artifacts"
            " WHERE data_version='openmeteo_single_model_http_capture_receipt_v1' ORDER BY artifact_id DESC LIMIT 1").fetchone()
        if damage=="captured_at":
            conn.execute("UPDATE raw_forecast_artifacts SET captured_at='broken-clock' WHERE artifact_id=?",(latest_id,))
        else:
            Path(latest_path).unlink()
        conn.commit()
        before=conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id").fetchall()
        assert "icon_global" not in read_current_instrument_values(conn,**scope,decision_time_iso="2026-09-29T22:45:00Z")
    world.clock[0]=datetime(2026,9,29,23,30,tzinfo=UTC)
    before_calls, before_cost = len(world.calls),world.tracker.calls_today()
    rejected_fp=fingerprint(world.clock[0])
    report=_normal_capture_producer(tmp_path,monkeypatch,world)
    assert report["written_row_count"]==0
    assert report["physical_capture_recovered_raw_ids"]==(original.raw_model_forecast_id,)
    assert report["committed_families"]==((target.city,target.target_date,metric),)
    assert len(world.calls)==before_calls+1 and world.tracker.calls_today()==before_cost+1
    with sqlite3.connect(world.db) as conn:
        assert conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id").fetchall()==before
        current=read_current_instrument_values(conn,**scope,decision_time_iso=world.clock[0].isoformat())["icon_global"]
        assert current.raw_model_forecast_id==original.raw_model_forecast_id and current.captured_at==original.captured_at
        assert current.physical_response["capture_receipt_artifact_id"]!=original.physical_response["capture_receipt_artifact_id"]
        assert "icon_global" not in read_current_instrument_values(conn,**scope,decision_time_iso="2026-09-29T22:45:00Z")
        assert read_current_instrument_values(conn,**scope,decision_time_iso=old_cut.isoformat())["icon_global"].physical_response==original.physical_response
        lag=_exact_current_value_serving_lag(conn,city=target.city,target_date=target.target_date,metric=metric,
            decision_time=world.clock[0],posterior_computed_at=old_cut,
            provenance={"bayes_precision_fusion":{"used_models":["icon_global"],"current_value_serving":{"icon_global":original.as_provenance()}}})
        assert lag[0] and "physical_proof_dependency_changed" in lag[1]
    assert fingerprint(old_cut)==old_fp
    healed_fp=fingerprint(world.clock[0])
    assert healed_fp is not None and healed_fp!=rejected_fp
    _normal_capture_producer(tmp_path,monkeypatch,world)
    assert len(world.calls)==before_calls+1 and world.tracker.calls_today()==before_cost+1
    assert fingerprint(world.clock[0])==healed_fp
