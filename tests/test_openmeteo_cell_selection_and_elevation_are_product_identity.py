# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: BLOCKER 4 — cell_selection and elevation/downscaling are first-class product identity and must be persisted alongside every raw_model_forecasts row.
# Reuse: Run with pytest; update if product-identity columns or cell_selection/elevation handling in the BAYES_PRECISION_FUSION downloader changes.
# Created: 2026-06-08
# Last reused or audited: 2026-06-08
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

import pytest

from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema


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


def _mock_single_model_http(monkeypatch, dl, *, value):
    dl._SINGLE_RUNS_PAYLOAD_CACHE.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEX.clear()

    def fetch(url, params, **kwargs):
        assert "," not in params["models"]
        day = datetime(2026, 6, 9)
        payload = {"latitude": 48.95, "longitude": 2.45, "elevation": 123,
            "timezone": "Europe/Paris", "hourly_units": {"temperature_2m": "°C"},
            "hourly": {"time": [(day + timedelta(hours=i)).isoformat(timespec="minutes") for i in range(24)],
                       "temperature_2m": [value] * 24}}
        body = (json.dumps(payload, indent=2) + "\n").encode()
        kwargs["capture_entity_body"](body, dl.datetime.now(UTC).timestamp())
        return json.loads(body)

    monkeypatch.setattr("src.data.openmeteo_client.fetch", fetch)


def _download_time(monkeypatch, module, when):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return when.astimezone(tz) if tz else when.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", Clock)


def _persist_exact_provider_body(conn, tmp_path, *, city, metric, target_date, model, cycle, captured, value, expected_written=1):
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
    payload = {"latitude":target.latitude, "longitude":target.longitude, "elevation":45,
        "timezone":target.timezone_name, "hourly_units":{"temperature_2m":"°C"},
        "hourly":{"time":[(day+timedelta(hours=i)).isoformat(timespec="minutes") for i in range(24)],
                  "temperature_2m":[value]*24}}
    body = (json.dumps(payload, indent=2)+"\n").encode()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("src.config.state_path", lambda filename: Path(tmp_path)/"state"/filename)
        _download_time(patch, dl, stamp)
        bound = dl._bind_physical_response(json.loads(body), model=model, url=SINGLE_RUNS_FORECAST_URL,
            params=params, run=run, captures=[(body, stamp.timestamp())])
        row = dict(model=model,city=city,metric=metric,target_date=target_date,source_cycle_time=cycle,
            source_available_at=captured,captured_at=captured,lead_days=target.lead_days,
            forecast_value_c=value,endpoint="single_runs",_physical_response=bound[dl._BATCH_PHYSICAL_RESPONSE_KEY],
            **dl._bayes_precision_fusion_product_identity(model,"single_runs",target))
        assert dl._persist_rows(conn, [row]) == expected_written
    return int(row["artifact_id"])


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


def test_station_ground_facts_identity_excludes_audit_but_cutoff_requires_possession(tmp_path, monkeypatch):
    from dataclasses import dataclass
    import src.config as config
    from tests.test_config import _official_hko_registry
    from src.data.replacement_forecast_materializer import _bind_provider_geometry_identity
    from src.data.replacement_forecast_cycle_policy import _anchor_station_ground_has_authority

    _official_hko_registry(tmp_path, monkeypatch)
    station = config.runtime_station_geometry_for_city(config.runtime_cities_by_name()["Hong Kong"])
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
    metadata = Metadata("Hong Kong", str(station["station_id"]), float(station["lat"]), float(station["lon"]),
        float(station["ground_elevation_m"]), proof)
    bound = _bind_provider_geometry_identity(Shape(), {}, anchor_metadata=metadata, decision_at="2026-09-29T22:00:00+00:00")
    assert _anchor_station_ground_has_authority(bound.provider_geometry_evidence, bound.provider_geometry_audit, "2026-09-29T22:00:00Z")
    assert not _anchor_station_ground_has_authority(bound.provider_geometry_evidence, bound.provider_geometry_audit)
    assert not _anchor_station_ground_has_authority(bound.provider_geometry_evidence, bound.provider_geometry_audit, "2026-09-29T04:00:00Z")
    old = _bind_provider_geometry_identity(Shape(), {}, anchor_metadata=metadata, decision_at="2026-09-29T04:00:00Z")
    assert not _anchor_station_ground_has_authority(old.provider_geometry_evidence, old.provider_geometry_audit, "2026-09-29T04:00:00Z")
    changed_audit = json.loads(json.dumps(proof))
    changed_audit["station_registry_sha256"] = "b" * 64
    changed_audit["station_ground_proof"]["audit"]["body_sha256"] = "c" * 64
    changed_audit["station_ground_proof"]["audit"]["checked_at"] = "2026-09-29T21:30:00Z"
    other = _bind_provider_geometry_identity(Shape(), {}, anchor_metadata=replace(metadata, source_geometry_proof=changed_audit),
        decision_at="2026-09-29T22:00:00Z")
    assert other.provider_geometry_identity_hash == bound.provider_geometry_identity_hash
    assert other.shape_hash == bound.shape_hash
    changed_audit["station_ground_proof"]["facts"]["elevation_m"] = 33.0
    changed = _bind_provider_geometry_identity(Shape(), {}, anchor_metadata=replace(metadata, source_geometry_proof=changed_audit),
        decision_at="2026-09-29T22:00:00Z")
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
            if damage=="wrong_first_site" and tz=="Europe/Paris":
                latitude,longitude=0,0
            payload.append({"latitude":latitude,"longitude":longitude,"elevation":100,
                "timezone":tz,"hourly_units":{"temperature_2m":"°C"},
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
    from src.data import bayes_precision_fusion_download as dl, replacement_forecast_production as production
    from src.data.replacement_current_value_serving import read_current_instrument_values
    conn,target,_cycle = _current_rows(tmp_path,monkeypatch)
    cycle = datetime(2026,6,9,tzinfo=UTC)
    now = datetime(2026,6,10,2,tzinfo=UTC)
    for model, in conn.execute("SELECT model FROM raw_model_forecasts").fetchall():
        domain = dl._model_domain_hash(provider=dl.OPENMETEO_PROVIDER,model_name=dl.OPENMETEO_MODEL_IDS.get(model,model),
            cell_selection="land",elevation_param="requested",downscaling_policy="none",endpoint_mode="single_runs")
        conn.execute("UPDATE raw_model_forecasts SET artifact_id=NULL,raw_sha256=NULL,elevation_param='requested',"
            "downscaling_policy='none',model_domain_hash=?,source_cycle_time=?,source_available_at=?,captured_at=?,recorded_at=? WHERE model=?",
            (domain,cycle.isoformat(),*(cycle.replace(hour=4).isoformat(),)*3,model))
    conn.execute("DELETE FROM raw_forecast_artifacts")
    old_provenance={"bayes_precision_fusion":{"current_evidence_shape":{"semantics_revision":"ensemble_center_scenarios_v5"}}}
    conn.execute("INSERT INTO forecast_posteriors (source_id,product_id,data_version,city,target_date,temperature_metric,"
        "source_cycle_time,source_available_at,computed_at,q_json,posterior_method,provenance_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("openmeteo_ecmwf_ifs9_bayes_fusion","test","test",target.city,target.target_date,target.metric,
         cycle.isoformat(),cycle.replace(hour=4).isoformat(),cycle.replace(hour=5).isoformat(),'{}',"test",json.dumps(old_provenance)))
    conn.commit()
    original=conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id").fetchall()
    original_cert=conn.execute("SELECT * FROM forecast_posteriors").fetchall()
    db=Path(conn.execute("PRAGMA database_list").fetchone()[2])
    _download_time(monkeypatch,dl,now)
    _download_time(monkeypatch,production,now)
    _mock_single_model_http(monkeypatch,dl,value=20)
    monkeypatch.setattr(dl,"_read_source_clock_single_runs_requests",lambda **_: {})
    monkeypatch.setattr("src.data.replacement_forecast_seed_discovery.held_position_family_priorities",
        lambda **_: {(target.city,target.target_date,target.metric):0})
    monkeypatch.setattr("src.data.replacement_forecast_current_target_plan.replacement_forecast_current_target_keys",lambda *_a,**_: ())
    monkeypatch.setattr("src.config.cities_by_name",{target.city:SimpleNamespace(lat=target.latitude,
        lon=target.longitude,timezone=target.timezone_name)})
    cfg={"forecast_db":db,"raw_manifest_dir":tmp_path/"raw_manifests","seed_dir":tmp_path/"seeds"}
    report=production._download_bayes_precision_fusion_extra_raw_inputs_if_needed(cfg,
        planning_cycle=now.replace(hour=0),max_wall_clock_seconds=5,include_previous_runs=False,prune_after=False)
    assert "coherent_archive_capture" in report, report
    assert report["coherent_archive_capture"]["attempted_target_group_count"] == 1
    assert report["written_row_count"] == 0
    assert report["committed_families"] == ((target.city,target.target_date,target.metric),)
    assert conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id").fetchall()==original
    assert conn.execute("SELECT * FROM forecast_posteriors").fetchall()==original_cert
    assert conn.execute("SELECT DISTINCT source_cycle_time FROM raw_forecast_artifacts").fetchall()==[(cycle.isoformat(),)]
    served=read_current_instrument_values(conn,city=target.city,metric=target.metric,target_date=target.target_date,
        source_cycle_time_iso=cycle.isoformat(),decision_time_iso=now.isoformat())
    assert len(served)==1 and all(value.physical_response["revalidated_legacy_product"] for value in served.values())
    conn.close()
