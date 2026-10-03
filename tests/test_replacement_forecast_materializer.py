# Created: 2026-06-06
# Last reused/audited: 2026-10-02
# Lifecycle: created=2026-06-06; last_reviewed=2026-10-02; last_reused=2026-10-02
# Purpose: Protect DB materialization for Open-Meteo ECMWF IFS 9km + Bayes-fusion replacement live layer.
# Reuse: Run before changing replacement forecast live/experiment write path.
# Authority basis: Operator-directed replacement forecast simple-switch readiness.
"""Replacement forecast materializer tests."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest



from src.data.openmeteo_ecmwf_ifs9_anchor import OpenMeteoIfs9LocalDayAnchor
from src.data.openmeteo_ecmwf_ifs9_bucket_transport import (
    source_cell_geometry_proof as _REAL_SOURCE_CELL_GEOMETRY_PROOF,
)
from src.data.openmeteo_ecmwf_ifs9_precision_guard import (
    OpenMeteoIfs9PrecisionMetadata,
    evaluate_openmeteo_ecmwf_ifs9_precision_guard,
)
from src.data.replacement_forecast_materializer import (
    _BayesPrecisionFusionFusionOverride,
    Day0EnqueueOwnershipWitness,
    REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL,
    REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET,
    ReplacementForecastMaterializeRequest,
    STALE_DAY0_ENQUEUE_OWNER,
    _QLCB_BASIS,
    _ensure_forecast_posteriors_runtime_layer,
    _ensure_replacement_frontier_indexes,
    _ensure_replacement_identity_columns,
    _replacement_is_live_layer,
    materialize_replacement_forecast_live,
)
import src.data.replacement_forecast_materializer as materializer_mod
from src.data import replacement_cycle_advance_trigger as cycle_advance
from src.data.replacement_forecast_source_run_identity import (
    expected_replacement_dependency_identity_by_role,
)
from src.data.replacement_forecast_readiness import LIVE_RUNTIME_LAYER, STRATEGY_KEY
from src.state.db import _create_readiness_state
from src.state.db import _create_source_run, _create_source_run_coverage
from src.state.schema.v2_schema import (
    _ensure_forecast_posteriors_runtime_layer_compatibility,
    apply_canonical_schema,
)
from src.state.source_run_repo import write_source_run
from src.state.readiness_repo import write_readiness_state

UTC = timezone.utc
_DEFAULT_PRECISION_GUARD = object()
REPO_ROOT = Path(__file__).resolve().parents[1]
_HKO_FORECAST_DB_DIR: Path | None = None


@dataclass(frozen=True)
class _Evidence:
    source_run_id: str


@dataclass(frozen=True)
class _BaselineBundle:
    evidence: _Evidence


@dataclass(frozen=True)
class _TemperatureBin:
    bin_id: str
    lower_c: float | None = None
    upper_c: float | None = None
    center_c: float | None = None
    display_unit: str = "C"
    settlement_unit: str = "C"
    rounding_rule: str = "wmo_half_up"


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 6, hour, minute, tzinfo=UTC)


def _hko_dt(hour: int, minute: int = 0) -> datetime:
    # The official ground entity was possessed on Sep29; never backdate it to
    # the June fixtures. Sep30 -> Oct1 preserves the original +8h lead geometry.
    return datetime(2026, 9, 30, hour, minute, tzinfo=UTC)


@pytest.fixture
def _hko_source_surface(tmp_path, monkeypatch, _hko_native_surfaces):
    """Actual O1280 OM decoding of controlled static input, not guard authority."""
    from functools import partial
    import numpy as np
    from omfiles import OmFileWriter
    import src.data.openmeteo_ecmwf_ifs9_bucket_transport as transport
    from src.data import station_ground_evidence as ground

    # Prior approved 13bc prerequisite: only this explicit physical fixture
    # enables file-backed canonical possession, never the generic source cases.
    monkeypatch.setattr(sys.modules[__name__], "_HKO_FORECAST_DB_DIR", tmp_path / "forecast-fixtures")
    monkeypatch.setattr(ground, "_store_root", lambda: tmp_path / "station-ground")
    class GroundClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return (_hko_dt(0)-timedelta(minutes=30)).astimezone(tz or UTC)
    monkeypatch.setattr(ground, "datetime", GroundClock)

    path = tmp_path / "controlled-o1280-hsurf.om"
    writer = OmFileWriter(str(path))
    root = writer.write_array(np.full((1, transport.O1280_TOTAL_POINTS), 32.0,
                                      dtype=np.float32), chunks=(1, 4096), name="HSURF")
    writer.close(root)
    # Other tests retain their legacy controlled seam. This fixture explicitly
    # routes the real constructor to one temporary, whole-byte OM artifact.
    monkeypatch.setattr(transport, "source_cell_geometry_proof",
                        partial(_REAL_SOURCE_CELL_GEOMETRY_PROOF, local_cache=str(path)))
    monkeypatch.setattr(transport, "HSURF_LOCAL_CACHE", str(path))
    monkeypatch.setattr(transport, "_o1280_snapshot_root", lambda: tmp_path / "owned-o1280")
    monkeypatch.setattr(transport, "_o1280_snapshot_now", lambda: _hko_dt(0)-timedelta(hours=1))
    yield path
    transport._hsurf_reader.cache_clear()


@pytest.fixture
def _hko_native_surfaces(tmp_path, monkeypatch, request=None, *, static_captured_at=None):
    """Ordinary loopback whole-OM captures for explicit global and US domains."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import numpy as np
    from omfiles import OmFileWriter
    from src.data import openmeteo_model_surface as surface

    bodies = {}
    frontier = request is not None and "_target_frontier_native_surfaces" in request.fixturenames
    models = (("icon_global", "ukmo_global_deterministic_10km") if frontier else
              ("icon_global", "ukmo_global_deterministic_10km", "gfs_hrrr", "ncep_nbm_conus",
               "icon_d2", "meteofrance_arome_france_hd", "ukmo_uk_deterministic_2km"))
    for model in models:
        profile = surface._profile(model)
        domain, shape = profile["domain"], (profile["ny"], profile["nx"])
        path = tmp_path / f"{domain}.om"
        writer = OmFileWriter(str(path))
        root = writer.write_array(np.full(shape, 32.0, dtype=np.float32), chunks=(20,20), name="HSURF")
        writer.close(root)
        bodies[domain] = path.read_bytes()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = bodies.get(self.path.lstrip("/"))
            if body is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("ETag", '"'+hashlib.sha256(body).hexdigest()+'"')
            modified = (static_captured_at-timedelta(hours=1)).strftime("%a, %d %b %Y %H:%M:%S GMT") if static_captured_at else (
                "Fri, 05 Jun 2026 22:00:00 GMT" if frontier else "Tue, 29 Sep 2026 22:00:00 GMT")
            self.send_header("Last-Modified", modified)
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1",0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(surface, "_asset_url", lambda domain: f"http://127.0.0.1:{server.server_port}/{domain}")
    monkeypatch.setattr(surface, "_cache_root", lambda: tmp_path / "native-static")
    monkeypatch.setattr(surface, "_now", lambda: static_captured_at or (
        _dt(2, 50) if frontier else _hko_dt(0)-timedelta(hours=1)))
    try:
        for model in models:
            capture = surface.ensure_model_surface(model)
            assert capture.status == "READY", capture.reason
        yield
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def _target_frontier_native_surfaces(tmp_path, monkeypatch, request):
    """TEST_ONLY_SYNTHETIC_EXTERNAL_CONDITION: legal source/cut, not Shanghai q."""
    native_surfaces = _hko_native_surfaces.__wrapped__(tmp_path, monkeypatch, request)
    next(native_surfaces)
    try:
        yield
    finally:
        native_surfaces.close()


@pytest.fixture
def _historical_shanghai_component_surface(tmp_path, monkeypatch):
    """TEST_ONLY_SYNTHETIC_EXTERNAL_CONDITION, not retained June source evidence."""
    import src.data.openmeteo_ecmwf_ifs9_bucket_transport as transport
    static_cut = _dt(0)-timedelta(hours=1)
    native = _hko_native_surfaces.__wrapped__(tmp_path,monkeypatch,static_captured_at=static_cut)
    next(native)
    physical = _hko_source_surface.__wrapped__(tmp_path,monkeypatch,None)
    next(physical)
    monkeypatch.setattr(transport,"_o1280_snapshot_now",lambda:static_cut)
    try:
        yield
    finally:
        physical.close()
        native.close()


def _synthetic_historical_shanghai_ground_registry(tmp_path, monkeypatch, name):
    """New hypothetical external body, never backdate the retained Sep30 bytes."""
    import src.config as config
    assert name == "Shanghai"
    lat,lon = "31.143378","121.805214"
    identity = "30137822"
    payload = {"TEST_ONLY_SYNTHETIC_EXTERNAL_CONDITION": "hypothetical June station response",
        "stationCollection":{"definitions":[{"defType":"elevations","abbr":"GROUND",
            "description":"ELEVATION OF THE GROUND"}],"stations":[{
            "ncdcStnId":identity,"header":{"preferredName":"TEST ONLY HYPOTHETICAL PUDONG",
                "latitude_dec":lat,"longitude_dec":lon,"por":{"endDate":"Present"}},
            "identifiers":[{"idType":"ICAO","id":"ZSPD"},{"idType":"NCDCSTNID","id":identity}],
            "location":{"ncdcstnId":identity,"latitudes":[{"latitude_dec":lat}],
                "longitudes":[{"longitude_dec":lon}],"latLonPairs":[{"latitude_dec":lat,"longitude_dec":lon}],
                "elevations":[{"elevationType":"GROUND","elevationFeet":"15","elevationMeters":"4.5"}],
                "geoInfo":{"ncdcstnId":identity},"nwsInfo":{"ncdcstnId":identity}}}]}}
    raw = json.dumps(payload,sort_keys=True).encode()
    assert raw != (config.PROJECT_ROOT/"config/noaa_homr_zspd_station.json").read_bytes()
    facts = config.station_ground_facts_from_bytes(source_kind=config.HOMR_INTERNATIONAL_GROUND_SOURCE_KIND,
        station_id="ZSPD",raw_body=raw)
    assert facts is not None and facts["elevation_m"] == 4.5
    # Registered transport filename inside the private config only; bytes and
    # their explicit hypothetical marker are independent of the real asset.
    artifact = tmp_path/"noaa_homr_zspd_station.json"
    artifact.write_bytes(raw)
    rows = json.loads((config.PROJECT_ROOT/"config/station_precise_coords.json").read_text())
    captured = _dt(0)-timedelta(hours=1)
    rows[name]["station_ground_proof"] = {**facts,"artifact_ref":f"config/{artifact.name}",
        "body_sha256":hashlib.sha256(raw).hexdigest(),"checked_at":captured.isoformat(),
        "query_date":"2026-06-05","query_url":f"{config.HOMR_GROUND_SOURCE_URL}?qid=ICAO%3AZSPD&qidMod=is&current=true&date=2026-06-05&phrData=false"}
    registry = tmp_path/"station_precise_coords.json"
    registry.write_text(json.dumps(rows))
    (tmp_path/"cities.json").write_bytes((config.PROJECT_ROOT/"config/cities.json").read_bytes())
    monkeypatch.setattr(config,"CONFIG_DIR",tmp_path)
    return registry,artifact,rows


def _historical_shanghai_component_request(tmp_path, monkeypatch, *, metric="high", computed_at=None):
    """Normal physical proof under explicit hypothetical history; controlled ENS."""
    conn,request = _shanghai_current_owner_request(tmp_path,monkeypatch,metric=metric,
        target_date=date(2026,6,7),source_cycle_time=_dt(0),computed_at=computed_at or _dt(18),
        ground_recorded_at=_dt(0)-timedelta(minutes=30),
        ground_captured_at=_dt(0)-timedelta(hours=1),
        ground_registry_factory=_synthetic_historical_shanghai_ground_registry,record_observed_prints=False)
    request = replace(request,day0_observed_extreme_c=None,day0_observed_extreme_source=None,
        day0_observed_extreme_observation_time=None,day0_observed_extreme_sample_count=None,
        day0_observed_extreme_unit="C")
    assert materializer_mod._precision_guard_block_reason(request,conn) == ()
    return conn,request


def _hko_raw_openmeteo_bytes() -> bytes:
    from src.config import runtime_cities_by_name
    from src.data.openmeteo_ecmwf_ifs9_bucket_transport import source_cell_geometry_proof
    city = runtime_cities_by_name()["Hong Kong"]
    cell = source_cell_geometry_proof(latitude=city.lat, longitude=city.lon,
                                     target_elevation_m=32.0)
    return json.dumps({
        "latitude": cell["selected_grid_lat"], "longitude": cell["selected_grid_lon"],
        "elevation": 32.0, "timezone": "Asia/Hong_Kong", "utc_offset_seconds": 28800,
        "hourly": {"time": [f"2026-10-01T{hour:02d}:00" for hour in range(24)],
                   "temperature_2m": [27.0 if hour == 12 else 18.5 for hour in range(24)]},
        "hourly_units": {"temperature_2m": "°C"},
        "_zeus_current_target_scope": {"city": "Hong Kong", "target_date": "2026-10-01", "metric": "high"},
    }, sort_keys=True).encode()


def _hko_precision_guard(*, decision_at=None, raw_payload_bytes=None):
    from scripts.download_replacement_forecast_current_targets import _precision_metadata
    raw = raw_payload_bytes or _hko_raw_openmeteo_bytes()
    metadata = OpenMeteoIfs9PrecisionMetadata(**_precision_metadata(
        "Hong Kong", "2026-10-01", anchor_sigma_c=3.0, raw_payload_bytes=raw,
        analysis_at=decision_at or _hko_dt(4)))
    guard = evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        metadata, raw_payload_bytes=raw, decision_at=decision_at or _hko_dt(4))
    assert guard.passable_for_live_materialization, guard.reason_codes
    return guard


def _hko_request(**kwargs):
    from src.data.openmeteo_ecmwf_ifs9_anchor import extract_openmeteo_ecmwf_ifs9_localday_anchor
    raw = _hko_raw_openmeteo_bytes()
    cycle = kwargs.pop("source_cycle_time", _hko_dt(0))
    # 00Z permits partial polling at 06:40Z; this controlled input proves
    # the complete target window, not the complete global run at that time.
    # Defaults describe a lawful later decision, never an invented 04Z ENS.
    cut = kwargs.pop("computed_at", _hko_dt(8))
    expiry = kwargs.pop("expires_at", _hko_dt(10))
    baseline_available = kwargs.pop("baseline_source_available_at", cycle+timedelta(hours=2))
    anchor_available = kwargs.pop("openmeteo_source_available_at", cycle+timedelta(hours=3))
    guard = kwargs.pop("openmeteo_precision_guard", _DEFAULT_PRECISION_GUARD)
    if guard is _DEFAULT_PRECISION_GUARD:
        guard = _hko_precision_guard(decision_at=cut)
    anchor = extract_openmeteo_ecmwf_ifs9_localday_anchor(json.loads(raw), city_timezone="Asia/Hong_Kong",
        target_local_date=date(2026, 10, 1), source_cycle_time=cycle)
    return replace(_request(**kwargs, openmeteo_precision_guard=guard),
        city="Hong Kong", city_id="Hong Kong", city_timezone="Asia/Hong_Kong", target_date=date(2026, 10, 1),
        source_cycle_time=cycle, computed_at=cut, expires_at=expiry,
        baseline_source_available_at=baseline_available, openmeteo_source_available_at=anchor_available,
        openmeteo_anchor=anchor, openmeteo_raw_payload_bytes=raw)


def _current_baseline_data_version(metric: str = "high") -> str:
    value = expected_replacement_dependency_identity_by_role(metric)["baseline_b0"].data_version
    assert value is not None
    return value


def _conn(*, archive_ground: bool = True) -> sqlite3.Connection:
    db = None
    if _HKO_FORECAST_DB_DIR is not None:
        _HKO_FORECAST_DB_DIR.mkdir(exist_ok=True)
        fd,filename = tempfile.mkstemp(prefix="forecast-",suffix=".db",dir=_HKO_FORECAST_DB_DIR)
        os.close(fd)
        db = Path(filename)
    conn = sqlite3.connect(db if db is not None else ":memory:")
    conn.row_factory = sqlite3.Row
    apply_canonical_schema(conn, forecast_tables=True)
    _create_readiness_state(conn)
    if db is not None and archive_ground:
        from src.data.station_ground_evidence import archive_station_ground_evidence
        conn.commit()
        archived = archive_station_ground_evidence(db,["Hong Kong"])
        assert archived["status"] == "GROUND_SOURCE_ARCHIVED",archived
    return conn


def _ensure_source_run_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS source_run (
            source_run_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            track TEXT NOT NULL,
            release_calendar_key TEXT NOT NULL,
            ingest_mode TEXT NOT NULL,
            origin_mode TEXT NOT NULL,
            source_cycle_time TEXT NOT NULL,
            source_issue_time TEXT,
            source_release_time TEXT,
            source_available_at TEXT,
            fetch_started_at TEXT,
            fetch_finished_at TEXT,
            captured_at TEXT,
            imported_at TEXT,
            valid_time_start TEXT,
            valid_time_end TEXT,
            target_local_date TEXT,
            city_id TEXT,
            city_timezone TEXT,
            temperature_metric TEXT,
            physical_quantity TEXT,
            observation_field TEXT,
            dataset_id TEXT,
            expected_members INTEGER,
            observed_members INTEGER,
            expected_steps_json TEXT NOT NULL DEFAULT '[]',
            observed_steps_json TEXT NOT NULL DEFAULT '[]',
            expected_count INTEGER,
            observed_count INTEGER,
            completeness_status TEXT NOT NULL,
            partial_run INTEGER NOT NULL DEFAULT 0,
            raw_payload_hash TEXT,
            manifest_hash TEXT,
            status TEXT NOT NULL,
            reason_code TEXT,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


def _ensure_source_run_coverage_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_run_coverage (
            coverage_id TEXT PRIMARY KEY,
            source_run_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            release_calendar_key TEXT NOT NULL,
            track TEXT NOT NULL,
            city TEXT NOT NULL,
            target_local_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            expected_members INTEGER NOT NULL,
            observed_members INTEGER NOT NULL,
            expected_steps_json TEXT NOT NULL,
            observed_steps_json TEXT NOT NULL,
            snapshot_ids_json TEXT NOT NULL,
            completeness_status TEXT NOT NULL,
            readiness_status TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            expires_at TEXT,
            recorded_at TEXT NOT NULL
        )
        """
    )


def _anchor(*, source_cycle_time: datetime | None = None) -> OpenMeteoIfs9LocalDayAnchor:
    local_tz = timezone(timedelta(hours=8))
    contributing_local_times = tuple(datetime(2026, 6, 7, hour, tzinfo=local_tz) for hour in range(24))
    return OpenMeteoIfs9LocalDayAnchor(
        city_timezone="Asia/Shanghai",
        target_local_date=date(2026, 6, 7),
        high_c=27.0,
        low_c=18.5,
        sample_count=24,
        contributing_local_times=contributing_local_times,
        contributing_valid_times_utc=tuple(item.astimezone(UTC) for item in contributing_local_times),
        source_cycle_time=source_cycle_time or _dt(0),
    )


def _anchor_with_local_hours(*, hours: range | tuple[int, ...]) -> OpenMeteoIfs9LocalDayAnchor:
    local_tz = timezone(timedelta(hours=8))
    contributing_local_times = tuple(datetime(2026, 6, 7, hour, tzinfo=local_tz) for hour in hours)
    return replace(
        _anchor(),
        sample_count=len(contributing_local_times),
        contributing_local_times=contributing_local_times,
        contributing_valid_times_utc=tuple(item.astimezone(UTC) for item in contributing_local_times),
    )


def _fixture_raw_openmeteo_bytes() -> bytes:
    """Controlled local-axis payload; the offset does not renew any source clock."""
    from zoneinfo import ZoneInfo

    response = {
        "latitude": 31.14, "longitude": 121.80, "elevation": 8.0,
        "timezone": "Asia/Shanghai", "utc_offset_seconds": 28800,
        "hourly": {
            "time": [f"2026-06-07T{hour:02d}:00" for hour in range(24)],
            "temperature_2m": [27.0 if hour == 12 else 18.5 for hour in range(24)],
        },
        "hourly_units": {"temperature_2m": "°C"},
        "_zeus_current_target_scope": {"city": "Shanghai", "target_date": "2026-06-07", "metric": "high"},
    }
    local_axis = tuple(datetime.fromisoformat(at).replace(tzinfo=ZoneInfo(response["timezone"]))
                       for at in response["hourly"]["time"])
    assert all(at.utcoffset() == timedelta(seconds=response["utc_offset_seconds"]) for at in local_axis)
    assert local_axis[0].astimezone(UTC) == _dt(16)
    assert (local_axis[0] + timedelta(days=1)).astimezone(UTC) == datetime(2026, 6, 7, 16, tzinfo=UTC)
    assert tuple(at.astimezone(UTC) for at in local_axis) == _anchor().contributing_valid_times_utc
    return (json.dumps(response, indent=2, sort_keys=True) + "\n").encode()


def _fixture_ens_surface_provenance(*, cycle: str = "2026-06-06T00:00:00+00:00", city_name: str = "Shanghai", selected_coords=None, decision_at=None) -> str:
    """Portable, internally consistent land-mask witness, not an ECMWF observation."""
    from src.config import cities_by_name, runtime_station_geometry_for_city
    from src.contracts.ensemble_snapshot_provenance import GRID_SURFACE_EVIDENCE_REVISION

    station = runtime_station_geometry_for_city(cities_by_name[city_name],effective_at=decision_at)
    assert station["validity_reason"] is None
    lat, lon = selected_coords or ((22.25, 114.25) if city_name == "Hong Kong" else (31.14, 121.80))
    neighbors = [
        {"flat_index": idx, "lat": lat, "lon": lon, "land_fraction": fraction}
        for idx, lat, lon, fraction in (
            (100, lat, lon, 0.9),
            (101, lat, lon+.25, 0.2),
            (102, lat+.25, lon, 0.2),
            (103, lat+.25, lon+.25, 0.2),
        )
    ]
    proof = {
        "revision": GRID_SURFACE_EVIDENCE_REVISION,
        "selection_rule": "nearest_land_of_surrounding_four_v1",
        "request_lat": cities_by_name[city_name].lat, "request_lon": cities_by_name[city_name].lon,
        "station_geometry": dict(station),
        "mask_source": "ecmwf_open_data_ifs_oper_fc_step0_lsm",
        "mask_source_url": "https://example.test/oper-mask.grib2",
        "mask_source_index_url": "https://example.test/oper-mask.index",
        "mask_source_cycle_time": cycle,
        # The toy mask receipt must belong to its declared run and already be
        # possessed by the fixture snapshot (new12 is available at 12:05).
        "mask_source_fetched_at": (datetime.fromisoformat(cycle)+timedelta(minutes=4)).isoformat(),
        "mask_source_index_offset": 0,
        "mask_source_index_length": 200,
        "mask_sha256": "a" * 64,
        "mask_grid_identity_hash": "b" * 64,
        "temperature_grid_identity_hash": "b" * 64,
        "selected_flat_index": 100,
        "selected_lat": lat, "selected_lon": lon,
        "selected_land_fraction": 0.9,
        "four_neighbors": neighbors,
    }
    return json.dumps({
        "city": city_name,
        "nearest_grid_lat": lat,
        "nearest_grid_lon": lon,
        "contract_outcome_evidence": {"settlement_station_id": station["station_id"]},
        "grid_surface_evidence": proof,
    })


@pytest.mark.parametrize("role,axis", (("request", "lat"), ("request", "lon"),
                                      ("station", "lat"), ("station", "lon")))
def test_ens_fixture_keeps_forecast_query_and_official_station_roles_distinct(role, axis):
    """Controlled ENS receipt; actual KORD query/official HOMR facts, not q."""
    from src.config import runtime_cities_by_name, runtime_station_geometry_for_city
    from src.data.executable_forecast_reader import grid_surface_evidence_reason

    city = runtime_cities_by_name()["Chicago"]
    cycle = datetime(2026, 10, 1, tzinfo=UTC)
    conn = _low_revision_authority_conn(city_name="Chicago", include_legacy_provider_fixtures=False,
        include_retired_incumbent=False, source_cycle=cycle)
    try:
        row = dict(conn.execute("SELECT * FROM ensemble_snapshots WHERE snapshot_id=12").fetchone())
        proof = json.loads(row["provenance_json"])
        geometry = proof["grid_surface_evidence"]["station_geometry"]
        station = runtime_station_geometry_for_city(city, effective_at=cycle+timedelta(minutes=5))
        assert station["ground_status"] == "VERIFIED"
        assert station["ground_elevation_m"] == 204.8
        assert (geometry["lat"], geometry["lon"]) == (station["lat"], station["lon"])
        assert (city.lat, city.lon) != (station["lat"], station["lon"])
        assert grid_surface_evidence_reason(row) is None
        if role == "request":
            proof["grid_surface_evidence"][f"request_{axis}"] = station[axis]
        else:
            geometry[axis] = getattr(city, axis)
        # Swap only one role field, leaving city/source/cycle/native cell/body
        # evidence intact. This is not an UPDATE to a licensed old snapshot.
        mixed = {**row, "provenance_json": json.dumps(proof)}
        assert grid_surface_evidence_reason(mixed) == "EXECUTABLE_FORECAST_GRID_SURFACE_STATION_UNVERIFIED"
        assert grid_surface_evidence_reason(row) is None
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _materializer_unit_source_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    """Controlled HSURF input; never mock the precision guard or raw witness."""
    import src.data.openmeteo_ecmwf_ifs9_bucket_transport as transport

    monkeypatch.setattr(transport, "source_cell_geometry_proof", lambda **kw: {
        "revision": "openmeteo_ifs9_o1280_source_cell_v1",
        "static_hsurf_sha256": "b" * 64,
        "selected_flat_index": 100,
        "selected_grid_lat": 31.14 if abs(kw["latitude"] - 31.1433) < .01 else kw["latitude"],
        "selected_grid_lon": 121.80 if abs(kw["latitude"] - 31.1433) < .01 else kw["longitude"],
        "raw_grid_elevation_m": 10.0,
        "effective_grid_elevation_m": 8.0,
        "target_dem_elevation_m": 8.0,
        "cell_is_sea": False, "cell_is_center": False, "nearby_sea": False,
    })


def _qualify_raw_fixture_rows(conn, *, rebuild=False):
    """Upgrade legacy fixture rows from actual writer output, retaining their IDs.

    Only fixture setup calls this; production read/authority gates are unmodified.
    Entity bytes and agency records pass the same capture/persistence constructors.
    """
    import src.data.bayes_precision_fusion_download as dl
    import src.data.station_forecast_adapter as station_adapter
    from src.config import runtime_cities_by_name
    from unittest.mock import patch

    columns = [row[1] for row in conn.execute("PRAGMA table_info(raw_model_forecasts)")]
    originals = [dict(zip(columns, tuple(row))) for row in conn.execute("SELECT * FROM raw_model_forecasts")]
    for old in originals:
        if not rebuild and (old.get("artifact_id") or old.get("source_id")):
            continue
        cities = runtime_cities_by_name()
        city = next(value for name, value in cities.items() if name.casefold() == old["city"].casefold())
        # This raw-writer staging DB is not a second ground/authority owner.
        # Its first artifact must remain the actual forecast body being copied.
        staging = _conn(archive_ground=False)
        model = old["model"]
        captured = old.get("captured_at") or old["source_available_at"]
        if model == "cwa_township":  # retained retired 063, never promote it to 061
            staging.close()
            continue
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                when = datetime.fromisoformat(captured)
                return when.astimezone(tz) if tz else when.replace(tzinfo=None)
        if model.startswith(("cwa_", "hko_")):
            if model.startswith("cwa_"):
                from tests.test_station_forecast_live_ingest_wiring import _hourly_low_xml
                body = _hourly_low_xml(issue_time=old["source_cycle_time"],
                    update_time=old["source_cycle_time"], sent_time=old["source_cycle_time"],
                    points=[(f"{old['target_date']}T{hour:02d}:00:00+08:00", str(old["forecast_value_c"]))
                            for hour in range(24)])
            else:
                body = json.dumps({"updateTime": old["source_cycle_time"], "weatherForecast": [{
                    "forecastDate": old["target_date"].replace("-", ""),
                    "forecastMaxtemp": {"value": old["forecast_value_c"], "unit": "C"},
                    "forecastMintemp": {"value": old["forecast_value_c"], "unit": "C"}}]}).encode()
            class Response:
                def __enter__(self): return self
                def __exit__(self, *_args): pass
                def read(self): return body
            with patch("urllib.request.urlopen", lambda *_args, **_kwargs: Response()), \
                 patch.object(station_adapter, "datetime", Clock), patch.object(dl, "datetime", Clock):
                if model.startswith("cwa_"):
                    station_adapter.ingest_cwa_township_hourly_extrema_live(
                        staging, metrics=(old["metric"],), api_key="fixture-only")
                else:
                    station_adapter.ingest_hko_fnd_live(staging, metrics=(old["metric"],))
        else:
            target = dl.BayesPrecisionFusionDownloadTarget(
                city=old["city"], metric=old["metric"], target_date=old["target_date"],
                lead_days=old["lead_days"], latitude=city.lat, longitude=city.lon, timezone_name=city.timezone,
            )
            identity = dl._bayes_precision_fusion_product_identity(model, old["endpoint"], target)
            params = json.loads(identity["request_params_json"])
            run = datetime.fromisoformat(old["source_cycle_time"])
            previous = old["endpoint"] == "previous_runs"
            if not previous:
                params["run"] = run.strftime("%Y-%m-%dT%H:%M")
            variable = params["hourly"]
            grid_lat, grid_lon = (31.14, 121.80) if model == "ecmwf_ifs" and old["city"].casefold() == "shanghai" else (city.lat, city.lon)
            if old["city"] in {"Los Angeles", "Milan", "London"}:
                from src.config import runtime_station_geometry_for_city
                station = runtime_station_geometry_for_city(city, effective_at=datetime.fromisoformat(captured))
                assert station["ground_status"] == "VERIFIED"
                target_elevation = station["ground_elevation_m"]
                # Retained actual served-header goldens for the projected grids;
                # regular-grid coordinates are exact official cell centers.
                selected = {"icon_global": (34., -118.375),
                            "ukmo_global_deterministic_10km": (33.9375, -118.40625),
                            "gfs_hrrr": (33.94541, -118.40222),
                            "ncep_nbm_conus": (33.94122, -118.38857)}
                grid_lat, grid_lon = selected.get(model, (grid_lat, grid_lon))
                if old["city"] in {"Milan", "London"} and model != "ecmwf_ifs":
                    if model == "ukmo_uk_deterministic_2km":
                        from src.data.openmeteo_model_surface import _profile, _project, _float32
                        profile = _profile(model)
                        px,py = _project(profile,latitude=city.lat,longitude=city.lon)
                        axes = [_float32(_float32(value-profile[f"origin_{axis}"])/profile["dx"])
                                for value,axis in ((px,"x"),(py,"y"))]
                        indices = [math.floor(q+.5) if q >= 0 else math.ceil(q-.5) for q in axes]
                        grid_lat,grid_lon = _project(profile,
                            x=_float32(_float32(_float32(indices[0])*profile["dx"])+profile["origin_x"]),
                            y=_float32(_float32(_float32(indices[1])*profile["dy"])+profile["origin_y"]))
                        grid_lon = _float32(math.fmod(_float32(grid_lon+180),360)-180)
                    else:
                        from tests.test_openmeteo_cell_selection_and_elevation_are_product_identity import _selected_test_cell
                        grid_lat, grid_lon = _selected_test_cell(model, city.lat, city.lon)
                if model == "ecmwf_ifs":
                    from src.data.openmeteo_ecmwf_ifs9_bucket_transport import source_cell_geometry_proof
                    cell = source_cell_geometry_proof(latitude=city.lat, longitude=city.lon,
                                                      target_elevation_m=target_elevation)
                    grid_lat, grid_lon = cell["selected_grid_lat"], cell["selected_grid_lon"]
                    if grid_lon > 180:
                        grid_lon -= 360.
            frontier = model in {"icon_global", "ukmo_global_deterministic_10km"} and old["city"] == "Shanghai" and conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='unrelated_writer'").fetchone() is not None
            if frontier:
                grid_lat, grid_lon = ((31.125, 121.75) if model == "icon_global" else (31.21875, 121.78125))
            payload = {"latitude": grid_lat, "longitude": grid_lon, "elevation": 8.0,
                "timezone": city.timezone, "hourly_units": {variable: "°C"},
                "hourly": {"time": [f"{old['target_date']}T{hour:02d}:00" for hour in range(24)],
                           variable: [old["forecast_value_c"]] * 24}}
            if old["city"] in {"Los Angeles", "Milan", "London"}:
                from zoneinfo import ZoneInfo
                local_start = datetime.combine(date.fromisoformat(old["target_date"]),
                                               datetime.min.time(), tzinfo=ZoneInfo(city.timezone))
                payload.update(elevation=target_elevation, utc_offset_seconds=int(local_start.utcoffset().total_seconds()))
                if model == "gfs_hrrr":
                    # Pinned upstream HRRR has 48h only at 00/06/12/18Z.
                    # A controlled body cannot invent slots outside that run.
                    assert run.hour % 6 == 0
                    hours = [(datetime.fromisoformat(at).replace(tzinfo=ZoneInfo(city.timezone)).astimezone(UTC)-run).total_seconds()/3600
                             for at in payload["hourly"]["time"]]
                    assert min(hours) >= 0 and max(hours) <= 48
            if frontier:
                payload["utc_offset_seconds"] = 28800
            body = (json.dumps(payload, indent=2) + "\n").encode()
            from src.data.openmeteo_client import PREVIOUS_RUNS_URL
            url = PREVIOUS_RUNS_URL if previous else "https://single-runs-api.open-meteo.com/v1/forecast"
            bound = dl._bind_physical_response(payload, model=model, url=url, params=params, run=run,
                captures=[(body, datetime.fromisoformat(captured).timestamp())],
                network_captures=[(body, datetime.fromisoformat(captured).timestamp(),
                                   {"content-type": "application/json"})]
                    if old["city"] in {"Los Angeles", "Milan", "London"} or frontier else ())
            raw = {key: old[key] for key in ("model", "city", "target_date", "metric", "source_cycle_time",
                "source_available_at", "lead_days", "forecast_value_c", "endpoint")}
            raw.update(captured_at=captured, **identity, _physical_response=bound[dl._BATCH_PHYSICAL_RESPONSE_KEY])
            if old["city"] in {"Los Angeles", "Milan"} or frontier:
                # Actual body/receipt artifacts remain in this same database.
                # A SQL-frontier marker is a fresh ordinary INSERT allocation,
                # not enrichment of an already licensed or foreign row.
                allocation = None
                if frontier:
                    assert not any(old.get(field) for field in
                                   ("request_url_hash", "raw_sha256", "artifact_id", "source_id"))
                    marker = old["raw_model_forecast_id"]
                    conn.execute("DELETE FROM raw_model_forecasts WHERE raw_model_forecast_id=?", (marker,))
                    maximum = conn.execute("SELECT COALESCE(MAX(raw_model_forecast_id),0) FROM raw_model_forecasts").fetchone()[0]
                    assert maximum < marker
                    if maximum < marker-1:
                        allocation = marker-1
                        conn.execute("INSERT INTO raw_model_forecasts(raw_model_forecast_id,model) VALUES (?,'TEST_ONLY_ALLOCATION')",
                                     (allocation,))
                with patch.object(dl, "datetime", Clock):
                    dl._persist_rows(conn, [raw])
                if frontier:
                    actual = conn.execute("""SELECT raw_model_forecast_id,raw_sha256 FROM raw_model_forecasts
                        WHERE product_id=? AND request_url_hash=? AND artifact_id=?""",
                        (raw["product_id"], raw["request_url_hash"], raw["artifact_id"])).fetchone()
                    assert tuple(actual) == (marker, raw["raw_sha256"])
                    if allocation is not None:
                        conn.execute("DELETE FROM raw_model_forecasts WHERE raw_model_forecast_id=?", (allocation,))
                        assert conn.execute("SELECT 1 FROM raw_model_forecasts WHERE raw_model_forecast_id=?",
                                            (allocation,)).fetchone() is None
                staging.close()
                continue
            with patch.object(dl, "datetime", Clock):
                dl._persist_rows(staging, [raw])
        produced = dict(staging.execute("SELECT * FROM raw_model_forecasts").fetchone())
        if "recorded_at" in produced:
            produced["recorded_at"] = old.get("recorded_at") or captured
        for name in produced:
            if name not in columns:
                conn.execute(f"ALTER TABLE raw_model_forecasts ADD COLUMN {name}")
                columns.append(name)
        if produced["artifact_id"] is not None:
            artifact = dict(staging.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?",
                                           (produced["artifact_id"],)).fetchone())
            artifact_columns = [r[1] for r in conn.execute("PRAGMA table_info(raw_forecast_artifacts)")]
            for name in artifact:
                if name not in artifact_columns:
                    conn.execute(f"ALTER TABLE raw_forecast_artifacts ADD COLUMN {name}")
                    artifact_columns.append(name)
            existing = conn.execute("""SELECT artifact_id,captured_at,source_available_at
                FROM raw_forecast_artifacts WHERE source_id=? AND product_id=? AND data_version=?
                AND source_cycle_time=? AND sha256=?""", tuple(artifact[key] for key in
                    ("source_id", "product_id", "data_version", "source_cycle_time", "sha256"))).fetchone()
            if existing is not None:
                # Mirror the ordinary writer: same immutable entity keeps its
                # first possession clock, even when a later fixture row cites it.
                produced["artifact_id"] = existing[0]
                produced["captured_at"] = existing[1]
                if model.startswith(("cwa_", "hko_")):
                    produced["source_available_at"] = existing[2]
            else:
                new_id = conn.execute("SELECT COALESCE(MAX(artifact_id),0)+1 FROM raw_forecast_artifacts").fetchone()[0]
                artifact["artifact_id"] = produced["artifact_id"] = new_id
                conn.execute("INSERT INTO raw_forecast_artifacts (" + ",".join(artifact) + ") VALUES (" +
                             ",".join("?" for _ in artifact) + ")", tuple(artifact.values()))
        fields = [name for name in produced if name != "raw_model_forecast_id"]
        conn.execute("UPDATE raw_model_forecasts SET " + ",".join(name + "=?" for name in fields) +
                     " WHERE raw_model_forecast_id=?", (*[produced[name] for name in fields], old["raw_model_forecast_id"]))
        staging.close()


@pytest.mark.parametrize("endpoint", ("single_runs", "previous_runs"))
def test_writer_fixture_binds_endpoint_series_without_renewing_same_body_capture(endpoint):
    from src.data.openmeteo_client import PREVIOUS_RUNS_URL
    conn = _conn()
    conn.execute("""INSERT INTO raw_model_forecasts (
        model,city,target_date,metric,source_cycle_time,source_available_at,captured_at,
        lead_days,forecast_value_c,endpoint
    ) VALUES ('gfs_global','Shanghai','2026-06-07','high',?,?,?,1,23,?)""",
        (_dt(0).isoformat(), _dt(3).isoformat(), _dt(3).isoformat(), endpoint))
    _qualify_raw_fixture_rows(conn)
    before = dict(conn.execute("SELECT * FROM raw_forecast_artifacts").fetchone())
    body = json.loads(Path(before["artifact_path"]).read_bytes())
    variable = "temperature_2m_previous_day1" if endpoint == "previous_runs" else "temperature_2m"
    assert body["hourly"][variable] == [23.0]*24
    assert body["hourly_units"] == {variable: "°C"}
    if endpoint == "previous_runs":
        assert before["request_url"] == PREVIOUS_RUNS_URL
    # The same immutable entity cited after a later fixture ingestion keeps
    # its actual first receipt, exactly like the normal raw/artifact writer.
    conn.execute("UPDATE raw_model_forecasts SET captured_at=?", (_dt(4).isoformat(),))
    _qualify_raw_fixture_rows(conn, rebuild=True)
    assert conn.execute("SELECT count(*) FROM raw_forecast_artifacts").fetchone()[0] == 1
    assert dict(conn.execute("SELECT * FROM raw_forecast_artifacts").fetchone()) == before
    raw = conn.execute("SELECT * FROM raw_model_forecasts").fetchone()
    assert raw["artifact_id"] == before["artifact_id"]
    assert raw["raw_sha256"] == before["sha256"]
    assert raw["source_cycle_time"] == _dt(0).isoformat()
    assert raw["captured_at"] == _dt(3).isoformat()
    conn.close()


def _fixture_current_shape(_conn, request, **kwargs):
    """Controlled ENS members; real decomposition and later geometry binding."""
    center = float(kwargs["center_c"])
    # Compute is read-only. A setup that ran the ordinary native writer must
    # consume that exact canonical identity/member input, not mint 9001 or
    # recenter the issued ENS when the provider center changes. Thin source-
    # selection components below retain their explicitly unlicensed seam.
    actual = None
    if "source_run" in {row[0] for row in _conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        actual = materializer_mod.read_current_evidence_snapshot_identity(
            _conn,request,metric=request.temperature_metric)
    if actual is not None:
        members = tuple(json.loads(actual.members_json))
        if actual.members_unit in ("F", "degF", "°F"):
            members = tuple((value-32)/1.8 for value in members)
        return materializer_mod._current_evidence_shape_from_values(
            snapshot_id=actual.snapshot_id,source_cycle_time=actual.source_cycle_time,
            source_available_at=actual.source_available_at,members_c=members,
            grid_surface_evidence_revision=actual.grid_surface_evidence_revision,
            grid_surface_evidence_identity_hash=actual.grid_surface_evidence_identity_hash,
            native_coordinate_compatibility=actual.native_coordinate_compatibility,
            **{key:kwargs[key] for key in ("provider_values_c","provider_weights","center_c","provider_cycles")})
    return materializer_mod._current_evidence_shape_from_values(
        snapshot_id=9001, source_cycle_time=str(request.source_cycle_time.isoformat()),
        source_available_at=str(request.computed_at.isoformat()),
        members_c=tuple(center + (index - 25) * .02 for index in range(51)),
        grid_surface_evidence_revision="ecmwf_ens_land_cell_selection_v1",
        grid_surface_evidence_identity_hash="a" * 64,
        **{key: kwargs[key] for key in ("provider_values_c", "provider_weights", "center_c", "provider_cycles")},
    )


def _record_fixture_current_temperature(conn, *, at, value_c=30.0):
    """Possessed typed METAR input consumed by the real Day0 state reader."""
    from src.state.schema.observation_prints_schema import append_print, ensure_table
    ensure_table(conn)  # the production ledger shape, never a hand-copied legacy DDL
    append_print(conn, city="Shanghai", station_id="ZSPD", source_channel="aviationweather_metar",
        publish_ts_utc=at.isoformat(), value_native=value_c, unit="C", fetched_at_utc=at.isoformat(),
        raw_report=f"METAR ZSPD {at.strftime('%d%H%M')}Z {round(value_c):02d}/20 T{round(value_c*10):04d}0200")
    from src.config import runtime_cities_by_name
    from src.data.bayes_precision_fusion_capture import OPENMETEO_MODEL_IDS
    from src.data.day0_hourly_vectors import (
        Day0HourlyVector, day0_hourly_models_for_city,
        day0_source_clock_ensemble_member_models, persist_day0_hourly_vectors,
    )
    city = runtime_cities_by_name()["Shanghai"]
    captured = at - timedelta(minutes=1)
    cycle = _dt(6)
    vectors = []
    provider_models = day0_hourly_models_for_city(city)
    ensemble_models = day0_source_clock_ensemble_member_models()
    for index, model in enumerate((*provider_models, *ensemble_models)):
        ensemble = model in ensemble_models
        api_model = "ecmwf_ifs025_ensemble" if ensemble else OPENMETEO_MODEL_IDS.get(model, model)
        request_hash = "sha256:fixture-ens" if ensemble else f"sha256:fixture-{model}"
        metadata = {
            "provider": "openmeteo", "model": model, "model_api_id": api_model,
            "endpoint": "https://single-runs-api.open-meteo.com/v1/forecast",
            "endpoint_mode": "single_runs", "source_run_authority": "run_pinned_single_runs",
            "provider_run_id": f"openmeteo:{api_model}:{cycle.isoformat()}",
            "provider_source_cycle_time_utc": cycle.isoformat(),
            "provider_source_available_at_utc": _dt(7).isoformat(),
            "provider_source_modified_at_utc": _dt(7).isoformat(),
            "fetch_started_at": captured.isoformat(), "fetch_finished_at": captured.isoformat(),
            "request_hash": request_hash, "source_run_id": f"day0_hourly:{request_hash}",
            "request_params_json": json.dumps({"metadata_model": api_model}),
        }
        offset = (ensemble_models.index(model)-25)*.02 if ensemble else index*.1
        vectors.append(Day0HourlyVector(
            model=model, city="Shanghai", target_date="2026-06-07", timezone_name=city.timezone,
            captured_at=captured.isoformat(),
            times=tuple(f"2026-06-07T{hour:02d}:00" for hour in range(24)),
            temps_c=tuple(value_c + offset for _ in range(24)),
            source_run_meta_json=json.dumps(metadata),
        ))
    for vector in vectors:
        meta = json.loads(vector.source_run_meta_json)
        persist_day0_hourly_vectors([vector], target_date="2026-06-07", conn=conn,
            request_hash=meta["request_hash"], endpoint=meta["endpoint"], now=at)
    conn.commit()


def _fixture_fast_residual_likelihood(*, extreme_c, at):
    from src.config import runtime_cities_by_name
    from src.data.day0_fast_obs import FAST_RESIDUAL_LIKELIHOOD_REVISION, FastStationResidualLikelihood
    from src.config import settlement_source_type_for_city
    city = runtime_cities_by_name()["Shanghai"]
    source_type = settlement_source_type_for_city(city, "2026-06-07")
    settlement_channel = "wu_icao_history" if source_type == "wu_icao" else "noaa_wrh_zspd"
    payload = {
        "semantics_revision": FAST_RESIDUAL_LIKELIHOOD_REVISION,
        "station_id": "ZSPD", "settlement_channel": settlement_channel,
        "fast_channel": "aviationweather_metar", "unit": "C", "as_of": at.isoformat(),
        "window_start": (at-timedelta(days=7)).isoformat(), "matched_pairs": 30,
        "residual_weights_c": ((0.0, 1.0),), "unknown_weight": 0.0,
        "settlement_extreme_c": extreme_c,
    }
    return FastStationResidualLikelihood(**payload, identity_hash=hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest())


def _precision_guard(**overrides: object):
    from src.config import cities_by_name, runtime_station_geometry_for_city
    from src.data.openmeteo_ecmwf_ifs9_precision_guard import _haversine_km

    city = cities_by_name["Shanghai"]
    station = runtime_station_geometry_for_city(city)
    assert station["validity_reason"] is None
    raw_bytes = _fixture_raw_openmeteo_bytes()
    proof = {
        "revision": "openmeteo_ifs9_o1280_source_cell_v1",
        "static_hsurf_sha256": "b" * 64,
        "selected_flat_index": 100,
        "selected_grid_lat": 31.14, "selected_grid_lon": 121.80,
        "raw_grid_elevation_m": 10.0,
        "effective_grid_elevation_m": 8.0,
        "target_dem_elevation_m": 8.0,
        "cell_is_sea": False, "cell_is_center": False, "nearby_sea": False,
        "raw_payload_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "station_registry_sha256": station["registry_sha256"],
    }
    values = {
        "city": "Shanghai",
        "station_id": station["station_id"],
        "city_lat": float(city.lat),
        "city_lon": float(city.lon),
        "station_lat": station["lat"],
        "station_lon": station["lon"],
        "requested_lat": station["lat"],
        "requested_lon": station["lon"],
        "requested_coordinate_precision_decimals": 4,
        "nearest_grid_lat": 31.14,
        "nearest_grid_lon": 121.80,
        "nearest_grid_distance_km": _haversine_km(station["lat"], station["lon"], 31.14, 121.80),
        "native_grid": "openmeteo_ecmwf_ifs_9km",
        "delivery_grid_resolution": "0p1",
        "interpolation_method": "nearest_gridpoint",
        "endpoint_mode": "hourly_zeus_aggregated",
        "local_day_start_utc": _dt(16),
        "local_day_end_utc": datetime(2026, 6, 7, 16, tzinfo=UTC),
        "timezone_name": "Asia/Shanghai",
        "target_local_date": date(2026, 6, 7),
        "temperature_unit": "C",
        "anchor_sigma_c": 3.0,
        "grid_elevation_m": 10.0,
        "station_elevation_m": station["elevation_m"],
        "land_sea_mask": "land",
        "city_class": "standard",
        "station_mapping_policy": "settlement_station",
        "source_geometry_proof": proof,
    }
    values.update(overrides)
    return evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        OpenMeteoIfs9PrecisionMetadata(**values),  # type: ignore[arg-type]
        raw_payload_bytes=raw_bytes,
    )


@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_reauthenticates_same_artifact_and_anchor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reauthenticate owned canonical bytes at an independent request cut."""
    conn = _conn()
    request = _hko_request_with_owned_anchor(conn, _hko_request())
    raw_bytes = request.openmeteo_raw_payload_bytes
    anchor = request.openmeteo_anchor
    guard = request.openmeteo_precision_guard
    assert guard.passable_for_live_materialization
    missing_cut = evaluate_openmeteo_ecmwf_ifs9_precision_guard(guard.metadata, raw_payload_bytes=raw_bytes)
    assert not missing_cut.passable_for_live_materialization
    assert missing_cut.reason_codes == ("OM9_SOURCE_GEOMETRY_DECISION_CUT_REQUIRED",)
    assert materializer_mod._precision_guard_block_reason(request, conn) == ()
    assert materializer_mod._precision_guard_block_reason(replace(request, openmeteo_raw_payload_bytes=None), conn) == (
        "OM9_SOURCE_RESPONSE_BYTES_MISSING",
    )
    assert materializer_mod._precision_guard_block_reason(replace(request, openmeteo_anchor=replace(anchor, high_c=28.0)), conn) == (
        "OM9_SOURCE_RESPONSE_ANCHOR_MISMATCH",
    )
    bad_guard = replace(guard, status="PASS", reason_codes=("fabricated",))
    assert materializer_mod._precision_guard_block_reason(replace(request, openmeteo_precision_guard=bad_guard), conn) == (
        "OM9_PRECISION_GUARD_RESULT_MISMATCH",
    )
    changed_raw = raw_bytes.replace(b"27.0", b"29.0")
    assert materializer_mod._precision_guard_block_reason(replace(request, openmeteo_raw_payload_bytes=changed_raw), conn) == (
        "OM9_SOURCE_RESPONSE_ANCHOR_MISMATCH",
    )
    conn.close()


def _bins() -> tuple[_TemperatureBin, ...]:
    return (
        _TemperatureBin("cool", upper_c=20.0, center_c=19.0),
        _TemperatureBin("warm", lower_c=21.0, upper_c=30.0),
        _TemperatureBin("hot", lower_c=31.0, center_c=32.0),
    )


def _install_live_fusion(
    monkeypatch: pytest.MonkeyPatch,
    *,
    complete: bool = True,
    shape_lag_hours: float = 0.0,
    snapshot_id: int = 9001,
    shape_cycle_time: datetime | None = None,
    current_serving: dict[str, dict[str, object]] | None = None,
    predictive_sigma_c: float = 2.0,
    request: ReplacementForecastMaterializeRequest | None = None,
) -> None:
    members = tuple(25.0 + (index - 25) * 0.02 for index in range(51))
    # This seam fixture isolates downstream writes. Build and bind its shape
    # through the real proof constructors; acceptance tests below use raw writers.
    within_var = sum((value - 25.0) ** 2 for value in members) / len(members)
    provider_delta = math.sqrt(predictive_sigma_c ** 2 - within_var)
    carrier_cycle = shape_cycle_time or _dt(0)
    shape_cycle = carrier_cycle - timedelta(hours=shape_lag_hours)
    shape = materializer_mod._current_evidence_shape_from_values(
        snapshot_id=snapshot_id, source_cycle_time=shape_cycle.isoformat(),
        source_available_at=(carrier_cycle + timedelta(hours=1)).isoformat(), members_c=members,
        provider_values_c={"ecmwf_ifs": 25.0 - provider_delta, "icon_global": 25.0 + provider_delta},
        provider_weights={"ecmwf_ifs": .5, "icon_global": .5}, center_c=25.0,
        provider_cycles=dict.fromkeys(("ecmwf_ifs", "icon_global"), carrier_cycle.isoformat()),
        carrier_cycle_time=carrier_cycle.isoformat(),
        grid_surface_evidence_revision="ecmwf_ens_land_cell_selection_v1",
        grid_surface_evidence_identity_hash="a" * 64,
    )
    shape = materializer_mod._bind_provider_geometry_identity(
        shape, {}, anchor_metadata=(request.openmeteo_precision_guard.metadata if request else _precision_guard().metadata),
        decision_at=request.computed_at if request else None,
    )
    override = _BayesPrecisionFusionFusionOverride(
        anchor_value_c=25.0,
        anchor_sigma_c=0.35,
        method="test_bayes_precision_fusion",
        used_models=("ecmwf_ifs9", "gfs", "icon", "gem", "jma"),
        model_set_hash="test-model-set",
        resolution_mix_hash="test-resolution-mix",
        lead_bucket="d1",
        dropped_models=(),
        excluded_regionals=(),
        dropped_aliases=(),
        raw_model_forecast_ids=(101, 102, 103),
        anchor_bridge={"test": True},
        predictive_sigma_c=predictive_sigma_c,
        decorrelated_providers_complete=complete,
        decorrelated_providers_served=5 if complete else 4,
        decorrelated_providers_expected=5,
        current_value_serving=(
            current_serving
            if current_serving is not None
            else {"ecmwf_ifs9": {"served_via": "single_runs"}}
        ),
        current_evidence_shape=shape.as_payload(),
        current_evidence_members_c=members,
    )
    monkeypatch.setattr(materializer_mod, "_replacement_bayes_precision_fusion_override", lambda *args, **kwargs: override)


def _fixture_native_shape_identity(conn, request, monkeypatch, *, members_c):
    """TEST_ONLY controlled extracted windows, ordinary native authority writer.

    No GRIB/network acquisition is claimed. The release calendar, parser,
    canonical snapshot/run/coverage and returned identity are not mocked.
    """
    from zoneinfo import ZoneInfo
    from src.config import runtime_cities_by_name, runtime_coordinate_manifest_json
    from src.data import ecmwf_open_data as native
    from src.state.db import init_schema_forecasts
    from tests.test_opendata_writes_v2_table import _make_opendata_high_payload
    from tests.test_ingest_grib_source_run_context import _complete_low_window_payload
    from src.contracts.ensemble_snapshot_provenance import ECMWF_OPENDATA_LOW_DATA_VERSION
    city = runtime_cities_by_name()[request.city]
    cycle = request.source_cycle_time
    metric = request.temperature_metric
    captured = cycle + (timedelta(hours=6, minutes=41) if cycle.hour in (0, 12)
                        else timedelta(hours=4, minutes=46))
    assert captured <= request.computed_at, (cycle, captured, request.computed_at)
    init_schema_forecasts(conn)
    start = datetime.combine(request.target_date, datetime.min.time(), tzinfo=ZoneInfo(city.timezone))
    end = (start + timedelta(days=1)).astimezone(UTC)
    start = start.astimezone(UTC)
    selected = (round(city.lat*4)/4, round(city.lon*4)/4)
    if metric == "high":
        body = _make_opendata_high_payload(str(request.target_date), cycle.isoformat(),
            local_day_start_iso=start.isoformat(), local_day_end_iso=end.isoformat(),
            nearest_grid_lat=selected[0], nearest_grid_lon=selected[1])
        subdir, track = "open_ens_mx2t6_localday_max", "mx2t6_high"
    else:
        body = _complete_low_window_payload(city.name, city.timezone, str(request.target_date), cycle.isoformat())
        body["data_version"] = ECMWF_OPENDATA_LOW_DATA_VERSION
        subdir, track = "open_ens_mn2t6_localday_min", "mn2t6_low"
    grid = json.loads(_fixture_ens_surface_provenance(city_name=city.name,
        cycle=cycle.isoformat(), selected_coords=selected, decision_at=captured))["grid_surface_evidence"]
    grid["mask_source_fetched_at"] = captured.isoformat()
    manifest_sha = hashlib.sha256(runtime_coordinate_manifest_json().encode()).hexdigest()
    lead = (request.target_date-cycle.date()).days
    body.update(city=city.name, lat=city.lat, lon=city.lon, timezone=city.timezone,
        unit=city.settlement_unit, members_unit=city.settlement_unit, lead_day=lead,
        nearest_grid_lat=selected[0], nearest_grid_lon=selected[1],
        generated_at=captured.isoformat(), manifest_sha256=manifest_sha, manifest_hash=manifest_sha,
        grid_surface_evidence=grid)
    body["selected_step_ranges"] = body["selected_step_ranges_inner"]
    for member, value_c in zip(body["members"], members_c, strict=True):
        value = value_c if city.settlement_unit == "C" else value_c*1.8+32
        boundary = value-.2 if metric == "high" else value+.2
        member.update(value_native_unit=value,
            **{f"inner_{'max' if metric == 'high' else 'min'}_native_unit": value,
               f"boundary_{'max' if metric == 'high' else 'min'}_native_unit":
                   boundary if member["boundary_step_ranges"] else None})
        if metric == "high":
            for window in member["native_windows"]:
                interval = f"{window['start_step_hours']}-{window['end_step_hours']}"
                window["value_native_unit"] = value if interval in member["inner_step_ranges"] else boundary
    db = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    root = db.parent / "controlled-native-ens"
    directory = (root / "raw" / "coordinate_manifests" / manifest_sha / subdir /
                 city.name.lower().replace(" ", "-") /
                 native._cycle_extract_dir_name(run_date=cycle.date(), run_hour=cycle.hour))
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{subdir}_target_{request.target_date}_lead_{lead}.json"
    serialized = json.dumps(body, sort_keys=True)
    if path.exists():
        assert path.read_text() == serialized, "one issued native input cannot change with provider center"
    else:
        path.write_text(serialized)
    existing = conn.execute("""SELECT es.* FROM ensemble_snapshots es JOIN source_run sr
        ON sr.source_run_id=es.source_run_id WHERE es.city=? AND es.target_date=?
        AND es.temperature_metric=? AND es.source_cycle_time=? AND sr.manifest_hash=?""",
        (city.name,str(request.target_date),metric,cycle.isoformat(),manifest_sha)).fetchone()
    if existing is not None:
        # Rebinding a later decision reads its first canonical possession;
        # it must not re-run the writer and renew recorded/source clocks.
        row = dict(existing)
    else:
        row = None
    class ClockType(type):
        def __instancecheck__(cls, value): return isinstance(value, datetime)
    class NativeClock(datetime, metaclass=ClockType):
        @classmethod
        def now(cls, tz=None): return captured.astimezone(tz or UTC)
    if row is None:
        # TEST_ONLY_CONTROLLED_CANONICAL_INSERT_CLOCK. The native row's
        # schema DEFAULT records this actual private INSERT, not wall-now
        # after the hypothetical decision. Existing rows are never updated.
        builtin = sqlite3.connect(":memory:")
        conn.create_function("strftime",2,lambda fmt,value: captured.isoformat(timespec="milliseconds")
            if (fmt,value)==("%Y-%m-%dT%H:%M:%f+00:00","now")
            else builtin.execute("SELECT strftime(?,?)",(fmt,value)).fetchone()[0])
        try:
            with monkeypatch.context() as ingress:
                ingress.setattr(native, "datetime", NativeClock)
                ingress.setattr(native._ingest_grib_module, "_now_utc_iso", lambda: captured.isoformat())
                decision, release = native._select_cycle_for_track(track=track, now_utc=captured)
                assert decision is native.FetchDecision.FETCH_ALLOWED and release["selected_cycle_time"] == cycle
                collected = native.collect_open_ens_cycle(track=track, skip_download=True, skip_extract=True,
                    grid_surface_source_evidence=grid, conn=conn, now_utc=captured,
                    _paths=native._resolve_opendata_paths(source_root=root, environ={}))
        finally:
            conn.create_function("strftime",2,lambda fmt,value:
                builtin.execute("SELECT strftime(?,?)",(fmt,value)).fetchone()[0])
        assert collected["status"] == "ok", collected
        row = dict(conn.execute("SELECT * FROM ensemble_snapshots WHERE source_run_id=? AND city=? AND target_date=? AND temperature_metric=?",
            (collected["source_run_id"], city.name, str(request.target_date), metric)).fetchone())
    assert row["source_cycle_time"] == cycle.isoformat()
    assert captured <= datetime.fromisoformat(row["recorded_at"]) <= request.computed_at
    actual_members = tuple(json.loads(row["members_json"]))
    if city.settlement_unit == "F":
        actual_members = tuple((value-32)/1.8 for value in actual_members)
    assert actual_members == pytest.approx(tuple(members_c))
    from src.contracts.ensemble_snapshot_provenance import grid_surface_evidence_identity_hash
    surface = json.loads(row["provenance_json"])["grid_surface_evidence"]
    return row, grid_surface_evidence_identity_hash(surface), actual_members


def _install_hko_live_fusion(monkeypatch, **kwargs):
    """Lawful ground/geometry write seam; not a claim of normal provider capture."""
    request = kwargs.pop("request", None) or _hko_request(source_cycle_time=_hko_dt(6), computed_at=_hko_dt(11), expires_at=_hko_dt(12))
    conn = kwargs.pop("conn",None)
    selected_cells = kwargs.pop("selected_cells", None)
    kwargs.setdefault("shape_cycle_time", request.source_cycle_time)
    if conn is not None:
        request = _hko_request_with_owned_anchor(conn,request)
    _install_live_fusion(monkeypatch, request=request, **kwargs)
    if conn is None:
        return request  # Legacy downstream seam, not current proof authority.
    original = materializer_mod._replacement_bayes_precision_fusion_override()
    members = original.current_evidence_members_c
    native_row, native_surface_hash, members = _fixture_native_shape_identity(conn, request, monkeypatch, members_c=members)
    within = sum((value-25.0)**2 for value in members)/len(members)
    delta = math.sqrt(original.predictive_sigma_c**2-within)
    values = {"icon_global":25.0-delta,"ukmo_global_deterministic_10km":25.0+delta}
    served = _hko_current_provider_inputs(request,values,conn=conn,selected_cells=selected_cells)
    shape = materializer_mod._current_evidence_shape_from_values(
        snapshot_id=native_row["snapshot_id"],source_cycle_time=kwargs["shape_cycle_time"].isoformat(),
        source_available_at=native_row["source_available_at"],members_c=members,
        provider_values_c=values,provider_weights=dict.fromkeys(values,.5),center_c=25.0,
        provider_cycles=dict.fromkeys(values,request.source_cycle_time.isoformat()),
        carrier_cycle_time=request.source_cycle_time.isoformat(),
        grid_surface_evidence_revision="ecmwf_ens_land_cell_selection_v1",grid_surface_evidence_identity_hash=native_surface_hash)
    if kwargs.get("shape_lag_hours"):
        shape = replace(shape,shape_lag_hours=kwargs["shape_lag_hours"],
            source_cycle_time=(request.source_cycle_time-timedelta(hours=kwargs["shape_lag_hours"])).isoformat())
    from src.data import station_ground_evidence as ground
    from src.data.replacement_current_value_serving import _ARTIFACT_IDENTITY_JSON_SQL, station_ground_target_coverage_for_city
    db = ground.forecast_db_from_connection(conn)
    evidence = ground.read_current_station_ground_evidence(db,city=request.city,decision_at=request.computed_at)
    artifact = json.loads(conn.execute(f"SELECT {_ARTIFACT_IDENTITY_JSON_SQL} FROM raw_forecast_artifacts a WHERE artifact_id=?",
                                      (request.anchor_artifact_id,)).fetchone()[0])
    shape = materializer_mod._bind_provider_geometry_identity(shape,served,
        anchor_metadata=request.openmeteo_precision_guard.metadata,decision_at=request.computed_at,
        station_ground_evidence=evidence,anchor_raw_artifact={**artifact,"forecast_db":str(db)},
        station_ground_target_coverage=station_ground_target_coverage_for_city(evidence,city=request.city,
            target_date=request.target_date,decision_at=request.computed_at))
    override = replace(original,used_models=tuple(values),current_value_serving={k:v.as_provenance() for k,v in served.items()},
        current_evidence_shape=shape.as_payload(),raw_model_forecast_ids=tuple(v.raw_model_forecast_id for v in served.values()),
        decorrelated_providers_expected=2,decorrelated_providers_served=2 if kwargs.get("complete",True) else 1)
    monkeypatch.setattr(materializer_mod,"_replacement_bayes_precision_fusion_override",lambda *a,**k:override)
    return request


def _hko_request_with_owned_anchor(conn,request):
    """Normal canonical anchor writer; no guessed ID or borrowed database."""
    from src.config import runtime_cities_by_name
    from src.data.station_ground_evidence import forecast_db_from_connection
    from src.data.openmeteo_ecmwf_ifs9_anchor import (
        OpenMeteoEcmwfIfs9AnchorRequest,build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest,
    )
    from src.data.raw_forecast_artifact_manifest import write_manifest_to_db
    city = runtime_cities_by_name()[request.city]
    payload = json.loads(request.openmeteo_raw_payload_bytes)
    payload["_zeus_current_target_scope"]["metric"] = request.temperature_metric
    raw = (json.dumps(payload,sort_keys=True)+"\n").encode()
    db = forecast_db_from_connection(conn)
    assert db is not None
    path = Path(db).parent / f"anchor-{request.temperature_metric}-{hashlib.sha256(raw).hexdigest()}.json"
    path.write_bytes(raw)
    manifest = build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest(path,
        request=OpenMeteoEcmwfIfs9AnchorRequest(city.lat,city.lon,request.source_cycle_time,city.timezone),
        metric=request.temperature_metric,source_available_at=request.openmeteo_source_available_at,
        captured_at=request.openmeteo_source_available_at,
        product_metadata={"city":city.name,"target_date":request.target_date.isoformat()})
    # TEST_ONLY_CONTROLLED_CANONICAL_INSERT_CLOCK: actual private INSERT;
    # restore SQLite's own function afterward, never UPDATE sealed rows.
    builtin = sqlite3.connect(":memory:")
    def insert_clock(fmt,value):
        if (fmt,value)==("%Y-%m-%dT%H:%M:%f+00:00","now"):
            return request.computed_at.isoformat(timespec="milliseconds")
        return builtin.execute("SELECT strftime(?,?)",(fmt,value)).fetchone()[0]
    conn.create_function("strftime",2,insert_clock)
    artifact_id = write_manifest_to_db(conn,manifest)
    conn.create_function("strftime",2,lambda fmt,value:builtin.execute("SELECT strftime(?,?)",(fmt,value)).fetchone()[0])
    conn.commit()  # The independent readonly authority reader sees committed possession.
    # Keep the builtin alive for the private conn's callback lifetime.
    if city.name == "Hong Kong":
        guard = _hko_precision_guard(decision_at=request.computed_at,raw_payload_bytes=raw)
    else:
        from scripts.download_replacement_forecast_current_targets import _precision_metadata
        metadata = OpenMeteoIfs9PrecisionMetadata(**_precision_metadata(
            city.name, request.target_date.isoformat(), anchor_sigma_c=3.0,
            raw_payload_bytes=raw, analysis_at=request.computed_at))
        guard = evaluate_openmeteo_ecmwf_ifs9_precision_guard(
            metadata, raw_payload_bytes=raw, decision_at=request.computed_at)
        assert guard.passable_for_live_materialization, guard.reason_codes
    return replace(request,anchor_artifact_id=artifact_id,openmeteo_raw_payload_bytes=raw,
        openmeteo_precision_guard=guard)


def _hko_current_provider_inputs(request,values,*,conn,selected_cells=None):
    """Prior 2ab writer seam with current actual body/capture/native proof."""
    from unittest.mock import patch
    from src.config import runtime_cities_by_name
    from src.data import bayes_precision_fusion_download as dl
    from src.data.replacement_current_value_serving import read_current_instrument_values
    captured = max(request.source_cycle_time+timedelta(hours=1),request.openmeteo_source_available_at)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return captured.astimezone(tz or UTC)
    city = runtime_cities_by_name()[request.city]
    target = dl.BayesPrecisionFusionDownloadTarget(city=city.name,metric=request.temperature_metric,
        target_date=request.target_date.isoformat(),lead_days=1,latitude=city.lat,longitude=city.lon,timezone_name=city.timezone)
    for model,value in values.items():
        identity = dl._bayes_precision_fusion_product_identity(model,"single_runs",target)
        params = json.loads(identity["request_params_json"])
        params["run"] = request.source_cycle_time.strftime("%Y-%m-%dT%H:%M")
        lat,lon = (22.25,114.125) if model=="icon_global" else (22.3125,114.1875)
        if selected_cells is not None:
            lat,lon = selected_cells[model]
        from zoneinfo import ZoneInfo
        local_start = datetime.combine(request.target_date,datetime.min.time(),tzinfo=ZoneInfo(city.timezone))
        payload = {"latitude":lat,"longitude":lon,"elevation":32.0,"timezone":city.timezone,
            "utc_offset_seconds":int(local_start.utcoffset().total_seconds()),
            "hourly_units":{"temperature_2m":"°C"},"hourly":{"time":[f"{request.target_date}T{hour:02d}:00" for hour in range(24)],
            "temperature_2m":[value]*24}}
        if selected_cells is not None and request.temperature_metric == "low":
            payload["hourly"]["temperature_2m"] = [value if hour==4 else value+1. for hour in range(24)]
            assert min(payload["hourly"]["temperature_2m"]) == value
            assert max(payload["hourly"]["temperature_2m"]) != value
        body = (json.dumps(payload,sort_keys=True)+"\n").encode()
        bound = dl._bind_physical_response(payload,model=model,url="https://single-runs-api.open-meteo.com/v1/forecast",
            params=params,run=request.source_cycle_time,captures=[(body,captured.timestamp())],
            network_captures=[(body,captured.timestamp(),{"content-type":"application/json"})])
        row = {"model":model,"city":city.name,"target_date":str(request.target_date),"metric":request.temperature_metric,
            "source_cycle_time":request.source_cycle_time.isoformat(),"source_available_at":captured.isoformat(),
            "captured_at":captured.isoformat(),"lead_days":1,"forecast_value_c":value,"endpoint":"single_runs",
            **identity,"_physical_response":bound[dl._BATCH_PHYSICAL_RESPONSE_KEY]}
        with patch.object(dl,"datetime",Clock):
            dl._persist_rows(conn,[row])
    served = read_current_instrument_values(conn,city=city.name,metric=request.temperature_metric,
        target_date=str(request.target_date),source_cycle_time_iso=request.source_cycle_time.isoformat(),
        decision_time_iso=request.computed_at.isoformat())
    assert set(values).issubset(served)
    conn.commit()  # End ordinary writer setup before the consumer transaction begins.
    return {model:served[model] for model in values}


def _install_pinned_ready_fusion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Supply source-clock witness fields through the producer, not a reader mock."""

    _install_live_fusion(monkeypatch)
    producer = materializer_mod._replacement_bayes_precision_fusion_override
    override = producer(None)
    monkeypatch.setattr(
        materializer_mod,
        "_replacement_bayes_precision_fusion_override",
        lambda *_args, **_kwargs: replace(
            override,
            current_evidence_shape={
                **override.current_evidence_shape,
                "member_values_hash": hashlib.sha256(json.dumps(
                    override.current_evidence_members_c,
                ).encode()).hexdigest(),
            },
            current_value_serving={
                "ecmwf_ifs9": {
                    "raw_model_forecast_id": 101,
                    "served_via": "single_runs",
                    "served_cycle": _dt(0).isoformat(),
                    "captured_at": _dt(1).isoformat(),
                },
            },
        ),
    )


def _assert_wu_fast_pinned_contract(
    provenance: dict[str, object], *, city: str, target_date: str,
    metric: str, decision_time: datetime,
) -> None:
    """The actual materializer output must pass the held reader, not a mock gate."""

    from copy import deepcopy
    from src.data.replacement_forecast_bundle_reader import (
        _held_pinned_provenance_reason,
    )

    def reason(value: dict[str, object]) -> str | None:
        return _held_pinned_provenance_reason(
            value, city=city, target_date=target_date,
            metric=metric, decision_time=decision_time,
        )

    assert reason(provenance) is None
    missing = deepcopy(provenance)
    missing["day0_provisional_observation"].pop("fast_residual_likelihood")
    assert reason(missing) == "REPLACEMENT_PINNED_DAY0_FAST_RESIDUAL_IDENTITY_MISSING"
    corrupted = deepcopy(provenance)
    corrupted["day0_provisional_observation"]["fast_residual_likelihood"]["unknown_weight"] = 0.01
    assert reason(corrupted) == "REPLACEMENT_PINNED_DAY0_FAST_RESIDUAL_IDENTITY_INVALID"

    def resigned(field: str, value: object, *, base=None) -> dict[str, object]:
        changed = deepcopy(provenance if base is None else base)
        likelihood = changed["day0_provisional_observation"]["fast_residual_likelihood"]
        likelihood[field] = value
        identity = {key: likelihood[key] for key in (
            "semantics_revision", "station_id", "settlement_channel", "fast_channel",
            "unit", "as_of", "window_start", "matched_pairs", "unknown_weight",
            "settlement_extreme_c",
        )}
        identity["residual_weights_c"] = tuple(
            (row["residual_c"], row["weight"])
            for row in likelihood["residual_weights_c"]
        )
        likelihood["identity_hash"] = hashlib.sha256(json.dumps(
            identity, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        return changed

    different_but_valid_hash = resigned(
        "matched_pairs",
        provenance["day0_provisional_observation"]["fast_residual_likelihood"]["matched_pairs"] + 1,
    )
    assert reason(different_but_valid_hash) == (
        "REPLACEMENT_PINNED_DAY0_FAST_RESIDUAL_CARRIER_IDENTITY_MISMATCH"
    )
    wrong_carrier = deepcopy(provenance)
    wrong_carrier["day0_remaining_carrier_content_identity"] = "0" * 64
    assert reason(wrong_carrier) == (
        "REPLACEMENT_PINNED_DAY0_FAST_RESIDUAL_CARRIER_IDENTITY_MISMATCH"
    )
    wrong_q = deepcopy(provenance)
    wrong_q["day0_remaining_carrier_q"][0] += 0.01
    assert reason(wrong_q) == "REPLACEMENT_PINNED_DAY0_FAST_RESIDUAL_CARRIER_VALUE_MISMATCH"
    wrong_sample = deepcopy(provenance)
    wrong_sample["day0_remaining_carrier_probability_samples"][0][0] += 0.01
    assert reason(wrong_sample) == "REPLACEMENT_PINNED_DAY0_FAST_RESIDUAL_CARRIER_VALUE_MISMATCH"
    assert reason(resigned("station_id", "WRONG")) == (
        "REPLACEMENT_PINNED_DAY0_FAST_RESIDUAL_IDENTITY_INVALID"
        if provenance["day0_provisional_observation"]["fast_residual_likelihood"]["settlement_channel"].startswith("noaa_wrh_")
        else "REPLACEMENT_PINNED_DAY0_FAST_RESIDUAL_CARRIER_INVALID"
    )
    # A NOAA channel embeds its station. The one-field inconsistency is
    # rejected structurally; even a self-consistent foreign pair must fail
    # the independent current-family carrier binding (the original intent).
    assert reason(resigned("settlement_channel","noaa_wrh_wrong",
        base=resigned("station_id","WRONG"))) == "REPLACEMENT_PINNED_DAY0_FAST_RESIDUAL_CARRIER_INVALID"
    assert reason(resigned("settlement_channel", "noaa_wrh_wrong")) == (
        "REPLACEMENT_PINNED_DAY0_FAST_RESIDUAL_IDENTITY_INVALID"
    )
    likelihood = provenance["day0_provisional_observation"]["fast_residual_likelihood"]
    other_valid_channel = (
        f"noaa_wrh_{likelihood['station_id'].lower()}"
        if likelihood["settlement_channel"] == "wu_icao_history"
        else "wu_icao_history"
    )
    assert reason(resigned("settlement_channel", other_valid_channel)) == (
        "REPLACEMENT_PINNED_DAY0_FAST_RESIDUAL_CARRIER_INVALID"
    )
    future = resigned("as_of", (decision_time + timedelta(minutes=1)).isoformat())
    future["day0_provisional_observation"]["observation_time"] = (
        decision_time + timedelta(minutes=1)
    ).isoformat()
    assert reason(future) == "REPLACEMENT_PINNED_DAY0_FAST_RESIDUAL_CARRIER_INVALID"
    wrong_metric = deepcopy(provenance)
    wrong_metric["day0_provisional_observation"]["metric"] = (
        "low" if metric == "high" else "high"
    )
    assert reason(wrong_metric) == "REPLACEMENT_PINNED_DAY0_METRIC_MISMATCH"
    wrong_unit = deepcopy(provenance)
    wrong_unit["day0_provisional_observation"]["unit"] = (
        "F" if provenance["day0_provisional_observation"]["unit"] == "C" else "C"
    )
    assert reason(wrong_unit) == "REPLACEMENT_PINNED_DAY0_UNIT_MISMATCH"
    wrong_shape = deepcopy(provenance)
    wrong_shape["q_shape"] = "fused_normal_direct"
    assert reason(wrong_shape) == "REPLACEMENT_PINNED_DAY0_FAST_RESIDUAL_SHAPE_INVALID"
    wrong_top_level = deepcopy(provenance)
    wrong_top_level["day0_preliminary_report_survival_likelihood"] = {
        "identity_hash": "1" * 64,
    }
    assert reason(wrong_top_level) == "REPLACEMENT_PINNED_DAY0_FAST_RESIDUAL_SHAPE_INVALID"


def _request(
    *,
    baseline_data_version: str | None = None,
    baseline_source_run_id: str = "b0-run",
    baseline_source_available_at: datetime | None = None,
    openmeteo_source_run_id: str | None = "om9-run",
    openmeteo_source_available_at: datetime | None = None,
    source_cycle_time: datetime | None = None,
    computed_at: datetime | None = None,
    expires_at: datetime | None = None,
    anchor_artifact_id: int | None = None,
    openmeteo_precision_guard=_DEFAULT_PRECISION_GUARD,
    day0_observed_extreme_c: float | None = None,
    day0_observed_extreme_source: str | None = None,
    day0_observed_extreme_observation_time: str | None = None,
    day0_observed_extreme_sample_count: int | None = None,
    day0_observation_state: str | None = None,
) -> ReplacementForecastMaterializeRequest:
    if baseline_data_version is None:
        baseline_data_version = _current_baseline_data_version("high")
    guard = _precision_guard() if openmeteo_precision_guard is _DEFAULT_PRECISION_GUARD else openmeteo_precision_guard
    return ReplacementForecastMaterializeRequest(
        city="Shanghai",
        city_id="Shanghai",
        city_timezone="Asia/Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        baseline_source_run_id=baseline_source_run_id,
        baseline_data_version=baseline_data_version,
        baseline_source_available_at=baseline_source_available_at or _dt(2),
        openmeteo_anchor=_anchor(source_cycle_time=source_cycle_time),
        openmeteo_source_run_id=openmeteo_source_run_id,
        openmeteo_source_available_at=openmeteo_source_available_at or _dt(3),
        bins=_bins(),
        source_cycle_time=source_cycle_time or _dt(0),
        computed_at=computed_at or _dt(4),
        expires_at=expires_at or _dt(6),
        anchor_artifact_id=anchor_artifact_id,
        openmeteo_precision_guard=guard,
        openmeteo_raw_payload_bytes=_fixture_raw_openmeteo_bytes(),
        day0_observed_extreme_c=day0_observed_extreme_c,
        day0_observed_extreme_source=day0_observed_extreme_source,
        day0_observed_extreme_observation_time=day0_observed_extreme_observation_time,
        day0_observed_extreme_sample_count=day0_observed_extreme_sample_count,
        day0_observed_extreme_unit="C" if day0_observed_extreme_c is not None else None,
        day0_observation_state=day0_observation_state,
    )


def _test_current_residual(request, settlement_extreme_c):
    from src.data.day0_fast_obs import FAST_RESIDUAL_LIKELIHOOD_REVISION, FastStationResidualLikelihood
    identity = {"semantics_revision":FAST_RESIDUAL_LIKELIHOOD_REVISION,
        "station_id":"ZSPD", "settlement_channel":"wu_icao_history",
        "fast_channel":"aviationweather_metar", "unit":"C",
        "as_of":request.computed_at.isoformat(),
        "window_start":(request.computed_at-timedelta(days=7)).isoformat(),
        "matched_pairs":30, "residual_weights_c":((0.0,1.0),),
        "unknown_weight":0.0, "settlement_extreme_c":settlement_extreme_c}
    return FastStationResidualLikelihood(**identity,identity_hash=hashlib.sha256(
        json.dumps(identity,sort_keys=True,separators=(",",":")).encode()).hexdigest())


def _install_day0_current_inputs(conn, monkeypatch, request):
    """Controlled current station/complete hourly inputs, not a carrier/math mock."""
    from src.data.day0_hourly_vectors import Day0HourlyVector, day0_source_clock_ensemble_member_models
    from src.state.schema.observation_prints_schema import ensure_table, append_print
    clock = datetime.fromisoformat(request.day0_observed_extreme_observation_time)
    ensure_table(conn)
    value = float(request.day0_observed_extreme_c)
    append_print(conn, city=request.city, station_id="ZSPD", source_channel="aviationweather_metar",
                 publish_ts_utc=clock.isoformat(), value_native=value, unit="C",
                 fetched_at_utc=clock.isoformat(),
                 raw_report=f"METAR ZSPD {clock:%d%H%M}Z 01008KT 9999 {int(value):02d}/18 Q1014")
    conn.commit()
    models = ("ecmwf_ifs", "icon_global")
    run = _dt(6); available = _dt(7); captured = clock - timedelta(minutes=1)
    def make(model, index=25, ensemble=False):
        meta = {"provider_source_cycle_time_utc":run.isoformat(),
                "provider_source_available_at_utc":available.isoformat(),
                "fetch_finished_at":captured.isoformat(),
                "request_hash":"same-ens" if ensemble else model,
                "provider_run_id":"same-ens" if ensemble else model}
        return Day0HourlyVector(model=model, city=request.city, target_date=str(request.target_date),
            timezone_name=request.city_timezone, captured_at=captured.isoformat(),
            times=tuple(f"{request.target_date}T{hour:02d}:00" for hour in range(24)),
            temps_c=tuple(value + (index-25)*0.02 for _ in range(24)),
            source_run_meta_json=json.dumps(meta))
    vectors=[make(model) for model in models]
    ensemble=[make(model,index,True) for index,model in enumerate(day0_source_clock_ensemble_member_models())]
    monkeypatch.setattr("src.data.day0_hourly_vectors.day0_hourly_models_for_city",lambda _:list(models))
    monkeypatch.setattr("src.data.day0_hourly_vectors.read_freshest_day0_hourly_vectors",
                        lambda **kw:ensemble if len(kw.get("expected_models") or ())==51 else vectors)
    monkeypatch.setattr(materializer_mod,"_day0_remaining_vector_witness",
        lambda *_a,**_kw:_wu_current_carrier_test_witness(city=request.city,target_date=str(request.target_date),
                                                        metric=request.temperature_metric,at=request.computed_at))


def _wu_current_carrier_test_witness(*, city: str, target_date: str, metric: str, at: datetime):
    models = ("ecmwf_ifs", "icon_global")
    clock = at.isoformat()
    return {
        "vector_id": "ecmwf-current-vector", "expected_models": list(models),
        "actual_models": list(models),
        "vector_ids_by_model": dict.fromkeys(models, "ecmwf-current-vector"),
        "capture_times_by_model_utc": dict.fromkeys(models, clock),
        "provider_source_cycle_time_by_model_utc": dict.fromkeys(models, clock),
        "provider_source_available_at_by_model_utc": dict.fromkeys(models, clock),
        "source_run_id_by_model": dict.fromkeys(models, "source-run"),
        "provider_run_id_by_model": dict.fromkeys(models, "provider-run"),
        "request_hash_by_model": dict.fromkeys(models, "request-hash"),
        "city": city, "target_date": target_date, "metric": metric,
    }


@pytest.mark.parametrize("frozen_two_source_scheme", (False, True))
@pytest.mark.parametrize(
    ("metric", "model", "value_c"),
    (("high", "cwa_township_hourly_high", 33.0), ("low", "cwa_township_hourly_low", 24.0)),
)
def test_hourly_cwa_extreme_retains_reader_and_cold_start_center_without_ground_license(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    frozen_two_source_scheme: bool,
    metric: str,
    model: str,
    value_c: float,
) -> None:
    """061 source/mean component; the unsupported RCSS full-q premise is refuted."""
    from src.config import City

    conn = _conn(archive_ground=False)
    target = date(2026, 7, 24)
    # TEST_ONLY_CONTROLLED_CANONICAL_INSERT_CLOCK: these diagnostic markers
    # and the normal station writer are possessed before this component cut.
    # Never rewrite a licensed row's first canonical clock.
    builtin = sqlite3.connect(":memory:")
    conn.create_function("strftime",2,lambda fmt,value:
        "2026-07-23T10:15:00.000+00:00"
        if (fmt,value)==("%Y-%m-%dT%H:%M:%f+00:00","now")
        else builtin.execute("SELECT strftime(?,?)",(fmt,value)).fetchone()[0])
    cwa_id = 701
    conn.execute(
        """
        INSERT INTO raw_model_forecasts (
            raw_model_forecast_id, model, city, target_date, metric,
            source_cycle_time, source_available_at, captured_at, lead_days,
            forecast_value_c, endpoint
        ) VALUES (?, ?, 'Taipei', ?, ?,
                  '2026-07-23T10:14:00+00:00', '2026-07-23T10:15:00+00:00',
                  '2026-07-23T10:15:00+00:00', 1, ?, 'single_runs')
        """,
        (cwa_id, model, target.isoformat(), metric, value_c),
    )
    # The retained F-D0047-063 raw row has the same metric but no entry role.
    conn.execute(
        """
        INSERT INTO raw_model_forecasts (
            raw_model_forecast_id, model, city, target_date, metric,
            source_cycle_time, source_available_at, captured_at, lead_days,
            forecast_value_c, endpoint
        ) VALUES (702, 'cwa_township', 'Taipei', ?, ?,
                  '2026-07-23T10:14:00+00:00', '2026-07-23T10:15:00+00:00',
                  '2026-07-23T10:15:00+00:00', 1, 99.0, 'single_runs')
        """,
        (target.isoformat(), metric),
    )
    taipei = City(
        name="Taipei", lat=25.067244, lon=121.552822, timezone="Asia/Taipei",
        settlement_unit="C", cluster="Taiwan", wu_station="RCSS",
        settlement_source_type="wu_icao",
    )
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {"Taipei": taipei})

    if frozen_two_source_scheme:
        from src.strategy.live_inference.source_clock_city_weights import CityOneScheme

        scheme = CityOneScheme(
            city="Taipei", scheme_status="ACTIVE",
            final_sources=("ecmwf_ifs", "gfs_global"),
            weights={"ecmwf_ifs": 0.6, "gfs_global": 0.4},
            sample_n=30, walkforward_pass=True, one_scheme_status="ACTIVE",
        )
    else:
        scheme = None
    monkeypatch.setattr(
        "src.strategy.live_inference.source_clock_city_weights.scheme_for_city",
        lambda *_args, **_kwargs: scheme,
    )

    request = replace(
        _request(),
        city="Taipei", city_id="Taipei", city_timezone="Asia/Taipei",
        temperature_metric=metric, target_date=target,
        source_cycle_time=datetime(2026, 7, 23, 10, tzinfo=UTC),
        computed_at=datetime(2026, 7, 23, 10, 16, tzinfo=UTC),
    )

    _qualify_raw_fixture_rows(conn)
    with pytest.raises(materializer_mod.BayesPrecisionFusionDeclined) as declined:
        materializer_mod._replacement_bayes_precision_fusion_override(
            request, metric=metric, anchor_value_corrected_c=25.0, conn=conn,
        )

    # Refuted original full-q premise: RCSS has no registered canonical
    # ground route. A scheme cannot legalize it or the mixed gfs_global grid.
    from src.data.replacement_current_value_serving import (
        read_current_instrument_values, station_ground_target_coverage_for_city,
    )
    assert declined.value.reason.startswith("STATION_GROUND_")
    assert any("current provider precision DATA_DEGRADED for Taipei 2026-07-24"
               in entry.message and "TARGET_STATION_GROUND_EVIDENCE_INVALID" in entry.message
               for entry in caplog.records)
    coverage = station_ground_target_coverage_for_city(None,city="Taipei",
        target_date=target,decision_at=request.computed_at)
    assert coverage["status"] == "DATA_DEGRADED"
    assert coverage["reason"] == "TARGET_STATION_GROUND_EVIDENCE_INVALID"
    served = read_current_instrument_values(conn,city="Taipei",metric=metric,
        target_date=str(target),source_cycle_time_iso=request.source_cycle_time.isoformat(),
        decision_time_iso=request.computed_at.isoformat(),include_station_sources=True)
    assert model in served
    assert "cwa_township" not in served
    assert cwa_id == served[model].raw_model_forecast_id
    serving = served[model].as_provenance()
    assert {key: value for key,value in serving.items() if key != "physical_response"} == {
        "served_via": "single_runs",
        "previous_run_substitution": False,
        "raw_model_forecast_id": cwa_id,
        "served_cycle": "2026-07-23T10:14:00+00:00",
        "captured_at": "2026-07-23T10:15:00+00:00",
        "age_hours": 0.017,
        "lead_days": 1,
    }
    evidence = serving["physical_response"]
    assert evidence["model"] == model and evidence["metric"] == metric
    artifact = conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?",
                            (evidence["artifact_id"],)).fetchone()
    body = Path(artifact["artifact_path"]).read_bytes()
    assert hashlib.sha256(body).hexdigest() == evidence["entity_body_sha256"]
    from src.data.station_forecast_adapter import reextract_station_response_value
    assert reextract_station_response_value(body, evidence) == value_c
    # Same mean owner used at materializer.py:4701. The two baseline values
    # are explicit mathematical inputs, not licensed gfs/IFS instruments.
    from src.forecast.center import raw_precision_center
    centers = {"ecmwf_ifs":25.0,"gfs_global":30.0,model:served[model].value_c}
    n_by_model = dict.fromkeys(centers,(None,0))
    weights,center = raw_precision_center(n_by_model,centers,unit="C")
    assert n_by_model[model][1] == 0.0
    assert weights[model] > 0.0
    if metric == "low":
        assert center < 27.0
        assert model in [key for key,(_,n) in n_by_model.items() if n == 0 and weights[key] > 0]


@pytest.mark.usefixtures("_hko_source_surface")
def test_live_override_keeps_every_scheme_weighted_source(monkeypatch) -> None:
    """The live override collapses provider families around the active scheme.

    Pins the wiring: the materializer passes the city's scheme sources into the
    family-freshness collapse, so a configured older sibling (HRRR beside hourly
    NBM) is never renormalized away (Los Angeles, 2026-09-30).
    """
    from src.config import City
    from src.strategy.live_inference.source_clock_city_weights import CityOneScheme

    la = City(
        name="Los Angeles", lat=33.93817, lon=-118.3866,
        timezone="America/Los_Angeles", settlement_unit="F", cluster="US-West",
        wu_station="KLAX", settlement_source_type="wu_icao",
    )
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {la.name: la})
    scheme = CityOneScheme(
        city="Los Angeles", scheme_status="ACTIVE",
        final_sources=("gfs_hrrr", "icon_global", "ukmo_global_deterministic_10km"),
        weights={
            "gfs_hrrr": 0.385, "icon_global": 0.275,
            "ukmo_global_deterministic_10km": 0.340,
        },
        sample_n=259, walkforward_pass=True, one_scheme_status="ACTIVE",
    )
    monkeypatch.setattr(
        "src.strategy.live_inference.source_clock_city_weights.scheme_for_city",
        lambda *_args, **_kwargs: scheme,
    )
    seen: list[tuple[str, ...]] = []

    def spy(served, *, configured=()):
        seen.append(tuple(configured))
        return {}

    monkeypatch.setattr(
        materializer_mod, "_freshest_declared_provider_representatives", spy
    )
    from src.data.station_ground_evidence import archive_station_ground_evidence, forecast_db_from_connection
    conn = _conn(archive_ground=False)
    assert archive_station_ground_evidence(forecast_db_from_connection(conn), ["Los Angeles"])["status"] == "GROUND_SOURCE_ARCHIVED"
    request = _la_current_physical_request(conn,metric="high",
        cycle=datetime(2026,9,30,tzinfo=UTC),decision=datetime(2026,9,30,11,23,tzinfo=UTC))

    # The fixture captures no current rows; the override names that decline after
    # the scheme collapse this test observes has already run.
    with pytest.raises(materializer_mod.BayesPrecisionFusionDeclined,
                       match="PERSISTED_CURRENT_CAPTURE_MISSING"):
        materializer_mod._replacement_bayes_precision_fusion_override(
            request, metric="high", anchor_value_corrected_c=27.0, conn=conn,
        )

    assert seen and set(seen[0]) == set(scheme.weights)


@pytest.mark.parametrize(("city", "metric", "scheme_models", "d2_newer"), (
    ("London", "high", ("ecmwf_ifs", "icon_d2", "icon_global", "ukmo_global_deterministic_10km"), False),
    ("Milan", "high", ("ecmwf_ifs", "icon_d2", "icon_global", "ukmo_global_deterministic_10km"), False),
    ("London", "high", ("icon_d2", "ukmo_uk_deterministic_2km"), False),
    ("Milan", "high", ("icon_d2", "meteofrance_arome_france_hd"), False),
    ("London", "low", ("ecmwf_ifs", "icon_d2"), False),
    ("London", "high", ("icon_d2", "ukmo_uk_deterministic_2km"), True),
))
def test_source_clock_scheme_rejects_possessed_d2_at_d2_lead_but_keeps_global(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest, city: str, metric: str,
    scheme_models: tuple[str, ...], d2_newer: bool,
) -> None:
    """A valid persisted row cannot extend a regional model's physical horizon."""
    from src.config import runtime_cities_by_name
    from src.strategy.live_inference.source_clock_city_weights import CityOneScheme

    request.getfixturevalue("_hko_source_surface")
    if city == "London":
        from tests.test_config import _official_international_homr_registry
        _official_international_homr_registry(request.getfixturevalue("tmp_path"),monkeypatch,city)
    conn = _conn(archive_ground=False)
    city_cfg = runtime_cities_by_name()[city]
    if city in {"Milan", "London"}:
        # Retained Milan WMDR/AWC and London HOMR bodies have distinct real
        # Sep30 capture clocks. Preserve them; move each controlled forecast
        # run/target/cut together after possession, keeping D2/D1 geometry.
        from src.data import station_ground_evidence as ground
        class GroundClock(datetime):
            @classmethod
            def now(cls, tz=None):
                recorded = _hko_dt(0,30) if city == "Milan" else _hko_dt(23)
                return recorded.astimezone(tz or UTC)
        monkeypatch.setattr(ground, "datetime", GroundClock)
        conn.commit()
        db = ground.forecast_db_from_connection(conn)
        assert ground.archive_station_ground_evidence(db, [city])["status"] == "GROUND_SOURCE_ARCHIVED"
        run = _hko_dt(0) if city == "Milan" else datetime(2026,10,1,tzinfo=UTC)
        entity = ground.read_current_station_ground_evidence(db, city=city, decision_at=run+timedelta(hours=1))
        if city == "Milan":
            assert len(entity["input_bodies"]) == 2
        else:
            from src.config import HOMR_INTERNATIONAL_GROUND_SOURCE_KIND
            assert entity["source_kind"] == HOMR_INTERNATIONAL_GROUND_SOURCE_KIND
        assert entity["facts"]["elevation_m"] == (234. if city == "Milan" else 5.8)
        assert datetime.fromisoformat(entity["recorded_at"]) < run+timedelta(minutes=40 if city == "Milan" else 5)
    far_target = date(2026, 10, 2) if city == "Milan" else date(2026, 10, 3)
    near_target = far_target - timedelta(days=1)
    models = tuple(dict.fromkeys((
        "ecmwf_ifs", "icon_d2", "icon_global", "ukmo_global_deterministic_10km",
        *scheme_models,
    )))
    for index, model in enumerate(models):
        model_run = run + timedelta(hours=3) if model == "icon_d2" and d2_newer else run
        conn.execute(
            """INSERT INTO raw_model_forecasts (
                model, city, target_date, metric, source_cycle_time,
                source_available_at, captured_at, recorded_at, lead_days,
                forecast_value_c, endpoint, coverage_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 2, ?, 'single_runs', 'COVERED')""",
            (model, city, far_target.isoformat(), metric, model_run.isoformat(),
             (model_run + timedelta(minutes=35 if city == "Milan" else 5)).isoformat(),
             (model_run + timedelta(minutes=40 if city == "Milan" else 10)).isoformat(),
             (model_run + timedelta(minutes=41 if city == "Milan" else 11)).isoformat(), 20.0 + index),
        )
    scheme = CityOneScheme(
        city=city, scheme_status="ACTIVE", final_sources=scheme_models,
        weights=dict.fromkeys(scheme_models, 1.0 / len(scheme_models)), sample_n=30,
        walkforward_pass=True, one_scheme_status="ACTIVE",
    )
    monkeypatch.setattr(
        "src.strategy.live_inference.source_clock_city_weights.scheme_for_city",
        lambda *_args, **_kwargs: scheme,
    )
    capture = SimpleNamespace(
        has_extras=True, anchor_z=20.0, anchor_tau0=1.0,
        likelihood=tuple(SimpleNamespace(
            model=model, z=22.0, train_residuals=(), n_train=0,
            residuals_by_date={},
        ) for model in ("icon_global", "ukmo_global_deterministic_10km")),
        disagree_var=0.0, anchor_raw_m2_native=None, anchor_raw_n_train=0,
        dropped_models=(),
        selection=SimpleNamespace(excluded_regionals=(), dropped_aliases=()),
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_capture.capture_bayes_precision_instruments",
        lambda **_kwargs: capture,
    )
    monkeypatch.setattr(
        "src.forecast.bayes_precision_fusion.fuse_bayes_precision_posterior",
        lambda **_kwargs: SimpleNamespace(
            sd=0.5, method="TEST_FUSION",
            used_models=("icon_global", "ukmo_global_deterministic_10km"),
            regional_models=(),
        ),
    )

    class _Shape:
        center_sigma_c = 0.5
        predictive_sigma_c = 1.2
        members_c = (20.0, 21.0, 22.0)

        @staticmethod
        def as_payload() -> dict[str, object]:
            return {"source": "test-current-ens-shape"}

    monkeypatch.setattr(materializer_mod, "_read_current_evidence_shape", _fixture_current_shape)
    for target, d2_eligible in ((far_target, False), (near_target, True)):
        # The physical target changes; copy the raw evidence to that exact natural key.
        if d2_eligible:
            if city in {"Milan", "London"}:
                # Different target, different normal entity. Do not relabel
                # already licensed D2 raw rows or their immutable body metadata.
                far_rows = [tuple(row) for row in conn.execute(
                    "SELECT * FROM raw_model_forecasts WHERE city=? AND target_date=? ORDER BY raw_model_forecast_id",
                    (city, far_target.isoformat()))]
                conn.execute("""INSERT INTO raw_model_forecasts (
                    model, city, target_date, metric, source_cycle_time,
                    source_available_at, captured_at, recorded_at, lead_days,
                    forecast_value_c, endpoint, coverage_status)
                    SELECT model, city, ?, metric, source_cycle_time,
                        source_available_at, captured_at, recorded_at, 1,
                        forecast_value_c, endpoint, coverage_status
                    FROM raw_model_forecasts WHERE city=? AND target_date=?""",
                    (target.isoformat(), city, far_target.isoformat()))
            else:
                conn.execute(
                    """UPDATE raw_model_forecasts SET target_date=?, lead_days=1
                       WHERE city=?""",
                    (target.isoformat(), city),
                )
        request = replace(
            _request(), city=city, city_id=city,
            city_timezone=city_cfg.timezone, temperature_metric=metric,
            target_date=target, source_cycle_time=run,
            computed_at=run + timedelta(hours=4 if d2_newer else 1),
        )
        if city in {"Milan", "London"}:
            request = _la_current_physical_request(conn, metric=metric, cycle=run,
                decision=request.computed_at, city_name=city, target=target)
        _qualify_raw_fixture_rows(conn)
        override = materializer_mod._replacement_bayes_precision_fusion_override(
            request, metric=metric, anchor_value_corrected_c=20.0, conn=conn,
        )
        assert override is not None
        if city in {"Milan", "London"} and d2_eligible:
            assert far_rows == [tuple(row) for row in conn.execute(
                "SELECT * FROM raw_model_forecasts WHERE city=? AND target_date=? ORDER BY raw_model_forecast_id",
                (city, far_target.isoformat()))]
        assert ("icon_d2" in override.used_models) is d2_eligible
        if len(scheme_models) == 2 and not d2_eligible:
            assert override.source_clock_one_scheme is not None
            assert override.source_clock_one_scheme["fallback_reason"] == (
                "configured_current_provider_pair_unavailable"
            )
            assert override.source_clock_one_scheme["fallback_to"] == "current_precision_fusion"
            assert set(override.used_models) == {
                "ecmwf_ifs", "icon_global", "ukmo_global_deterministic_10km",
            }
        elif len(scheme_models) == 4:
            assert "icon_global" in override.used_models
            assert "ukmo_global_deterministic_10km" in override.used_models


def _la_current_physical_request(conn, *, metric, cycle, decision, city_name="Los Angeles", target=None, hourly_values=None):
    """TEST_ONLY external inputs with the request city's own normal ground/anchor."""
    from src.config import runtime_cities_by_name, runtime_station_geometry_for_city
    from src.data.openmeteo_ecmwf_ifs9_bucket_transport import source_cell_geometry_proof
    from src.data.openmeteo_ecmwf_ifs9_anchor import extract_openmeteo_ecmwf_ifs9_localday_anchor
    from zoneinfo import ZoneInfo
    city = runtime_cities_by_name()[city_name]
    assert city_name in {"Los Angeles", "Milan", "London", "Chicago"}
    station = runtime_station_geometry_for_city(city, effective_at=decision)
    assert station["ground_status"] == "VERIFIED"
    assert station["ground_elevation_m"] == pytest.approx(
        {"Los Angeles":29.7,"Milan":234.,"London":5.8,"Chicago":204.8}[city_name])
    cell = source_cell_geometry_proof(latitude=city.lat, longitude=city.lon,
                                     target_elevation_m=station["ground_elevation_m"])
    lon = cell["selected_grid_lon"] - 360. if cell["selected_grid_lon"] > 180. else cell["selected_grid_lon"]
    target = target or date(2026, 10, 1 if city_name == "Los Angeles" else 2)
    local_start = datetime.combine(target, datetime.min.time(), tzinfo=ZoneInfo(city.timezone))
    assert cycle <= local_start.astimezone(UTC)  # A real full prior cannot invent an elapsed prefix.
    body = json.dumps({"latitude": cell["selected_grid_lat"], "longitude": lon,
        "elevation": station["ground_elevation_m"], "timezone": city.timezone,
        "utc_offset_seconds": int(local_start.utcoffset().total_seconds()),
        "hourly_units": {"temperature_2m": "°C"},
        "hourly": {"time": [f"{target}T{hour:02d}:00" for hour in range(24)],
                   "temperature_2m": hourly_values if hourly_values is not None else [20.]*24},
        "_zeus_current_target_scope": {"city": city.name, "target_date": str(target), "metric": metric}},
        sort_keys=True).encode()
    anchor = extract_openmeteo_ecmwf_ifs9_localday_anchor(json.loads(body), city_timezone=city.timezone,
        target_local_date=target, source_cycle_time=cycle)
    request = replace(_request(), city=city.name, city_id=city.name, city_timezone=city.timezone,
        target_date=target, temperature_metric=metric, baseline_data_version=_current_baseline_data_version(metric),
        source_cycle_time=cycle, computed_at=decision, expires_at=decision+timedelta(hours=3),
        openmeteo_source_available_at=cycle+timedelta(minutes=5), baseline_source_available_at=cycle+timedelta(minutes=5),
        openmeteo_anchor=anchor, openmeteo_raw_payload_bytes=body)
    return _hko_request_with_owned_anchor(conn, request)


@pytest.mark.usefixtures("_hko_source_surface")
@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize(("missing_hrrr", "shadowed_hrrr", "split_cohort"), (
    (True, False, False), (True, True, False),
    (True, True, True), (False, False, False),
))
def test_source_clock_partial_current_producer_to_jit(
    monkeypatch: pytest.MonkeyPatch, metric: str, missing_hrrr: bool,
    shadowed_hrrr: bool, split_cohort: bool,
) -> None:
    from src.config import runtime_cities_by_name
    from src.engine import event_reactor_adapter as adapter
    from src.strategy.live_inference.source_clock_city_weights import CityOneScheme

    conn = _conn()
    city = "Los Angeles"
    from src.data.station_ground_evidence import archive_station_ground_evidence, forecast_db_from_connection
    assert archive_station_ground_evidence(forecast_db_from_connection(conn), [city])["status"] == "GROUND_SOURCE_ARCHIVED"
    run = datetime(2026, 9, 30, 18, tzinfo=UTC)
    decision = run + timedelta(hours=7 if split_cohort else 4 if shadowed_hrrr else 1)
    configured = ("gfs_hrrr", "icon_global", "ukmo_global_deterministic_10km")
    for index, model in enumerate(("ecmwf_ifs", *configured)):
        if missing_hrrr and not shadowed_hrrr and model == "gfs_hrrr":
            continue
        conn.execute(
            """INSERT INTO raw_model_forecasts (
                model, city, target_date, metric, source_cycle_time,
                source_available_at, captured_at, recorded_at, lead_days,
                forecast_value_c, endpoint, coverage_status
            ) VALUES (?, ?, '2026-10-01', ?, ?, ?, ?, ?, 1, ?, 'single_runs', 'COVERED')""",
            (model, city, metric, run.isoformat(),
             (run + timedelta(minutes=5)).isoformat(),
             (run + timedelta(minutes=10)).isoformat(),
             (run + timedelta(minutes=11)).isoformat(), 20.0 + index),
        )
    if shadowed_hrrr:
        newer = run + timedelta(hours=3)
        conn.execute(
            """INSERT INTO raw_model_forecasts (
                model, city, target_date, metric, source_cycle_time,
                source_available_at, captured_at, recorded_at, lead_days,
                forecast_value_c, endpoint, coverage_status
            ) VALUES ('ncep_nbm_conus', ?, '2026-10-01', ?, ?, ?, ?, ?, 1, 24.0,
                      'single_runs', 'COVERED')""",
            (city, metric, newer.isoformat(),
             (newer + timedelta(minutes=5)).isoformat(),
             (newer + timedelta(minutes=10)).isoformat(),
             (newer + timedelta(minutes=11)).isoformat()),
        )
    if split_cohort:
        icon_cycle = run + timedelta(hours=6)
        conn.execute(
            """INSERT INTO raw_model_forecasts (
                model, city, target_date, metric, source_cycle_time,
                source_available_at, captured_at, recorded_at, lead_days,
                forecast_value_c, endpoint, coverage_status
            ) VALUES ('icon_global', ?, '2026-10-01', ?, ?, ?, ?, ?, 1, 25.0,
                      'single_runs', 'COVERED')""",
            (city, metric, icon_cycle.isoformat(),
             (icon_cycle + timedelta(minutes=5)).isoformat(),
             (icon_cycle + timedelta(minutes=10)).isoformat(),
             (icon_cycle + timedelta(minutes=11)).isoformat()),
        )
    scheme = CityOneScheme(
        city=city, scheme_status="ACTIVE", final_sources=configured,
        weights=dict.fromkeys(configured, 1.0 / len(configured)), sample_n=30,
        walkforward_pass=True, one_scheme_status="GRID_CAP10_LIVE_READY",
    )
    monkeypatch.setattr(
        "src.strategy.live_inference.source_clock_city_weights.scheme_for_city",
        lambda *_args, **_kwargs: scheme,
    )
    capture = SimpleNamespace(
        has_extras=True, anchor_z=20.0, anchor_tau0=1.0,
        likelihood=tuple(SimpleNamespace(
            model=model, z=22.0 + index, train_residuals=(), n_train=0,
            residuals_by_date={},
        ) for index, model in enumerate(
            (*configured, "ncep_nbm_conus") if shadowed_hrrr else configured
        ) if not (missing_hrrr and model == "gfs_hrrr")),
        disagree_var=0.0, anchor_raw_m2_native=None, anchor_raw_n_train=0,
        dropped_models=(),
        selection=SimpleNamespace(excluded_regionals=(), dropped_aliases=()),
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_capture.capture_bayes_precision_instruments",
        lambda **_kwargs: capture,
    )
    monkeypatch.setattr(
        "src.forecast.bayes_precision_fusion.fuse_bayes_precision_posterior",
        lambda **_kwargs: SimpleNamespace(
            sd=0.5, method="TEST_FUSION",
            used_models=tuple(x.model for x in capture.likelihood), regional_models=(),
        ),
    )

    class _Shape:
        center_sigma_c = 0.5
        predictive_sigma_c = 1.2
        members_c = tuple(20.0 + x * 0.1 for x in range(51))

        @staticmethod
        def as_payload() -> dict[str, object]:
            return {"source": "test-current-ens-shape", "provider_count": 3}

    monkeypatch.setattr(materializer_mod, "_read_current_evidence_shape", _fixture_current_shape)
    request = _la_current_physical_request(conn, metric=metric, cycle=run, decision=decision)
    _qualify_raw_fixture_rows(conn)
    override = materializer_mod._replacement_bayes_precision_fusion_override(
        request, metric=metric, anchor_value_corrected_c=20.0, conn=conn,
    )
    assert override is not None
    scheme_proof = override.source_clock_one_scheme
    assert scheme_proof is not None
    # A newer unconfigured NBM no longer shadows the configured HRRR: the
    # scheme keeps every weighted source it possesses (Los Angeles, 2026-09-30).
    hrrr_absent = missing_hrrr and not shadowed_hrrr
    if hrrr_absent:
        assert override.method == "SOURCE_CLOCK_CURRENT_PRECISION_FUSION"
        assert scheme_proof["fallback_reason"] == "configured_current_provider_set_incomplete"
        assert scheme_proof["missing_sources"] == ["gfs_hrrr"]
        assert set(scheme_proof["configured_current_sources"]) == set(configured) - {"gfs_hrrr"}
        cohort = scheme_proof["configured_cohort_value_serving"]
        assert set(cohort) == set(scheme_proof["configured_coherent_sources"])
        assert scheme_proof["configured_cohort_decision_time"] == decision.isoformat()
        if split_cohort:
            assert cohort["icon_global"]["served_cycle"] == run.isoformat()
            assert scheme_proof["between_cohort_value_serving"]["icon_global"]["served_cycle"] == (run + timedelta(hours=6)).isoformat()
            assert override.current_value_serving["icon_global"]["served_cycle"] == (run + timedelta(hours=6)).isoformat()
    else:
        assert override.method == "SOURCE_CLOCK_FIXED_WEIGHT"
        assert "fallback_reason" not in scheme_proof
        assert set(override.used_models) == set(configured)
        if shadowed_hrrr:
            assert "ncep_nbm_conus" not in override.used_models

    provenance = {"bayes_precision_fusion": {
        "used_models": list(override.used_models),
        "current_value_serving": override.current_value_serving,
        "source_clock_one_scheme": scheme_proof,
        "decorrelated_providers_expected": override.decorrelated_providers_expected,
        "decorrelated_providers_served": override.decorrelated_providers_served,
        "decorrelated_providers_complete": override.decorrelated_providers_complete,
    }}
    family = SimpleNamespace(city=city, target_date="2026-10-01", metric=metric)
    posterior_kwargs = {"posterior_computed_at": decision} if hrrr_absent else {}
    present, certificate = adapter._source_clock_model_count_certificate(
        provenance, family=family, decision_time=decision, **posterior_kwargs,
    )
    assert present and certificate is not None, provenance
    assert certificate["posterior_configured_sources"] == tuple(sorted(override.used_models))
    assert certificate["posterior_missing_sources"] == ()
    assert adapter._posterior_bound_spine_inputs(
        conn, family=family, decision_time=decision,
        source_cycle_time=run.isoformat(), provenance=provenance, **posterior_kwargs,
    ) is not None
    if hrrr_absent:
        # A weekly rotation of the ACTIVE scheme between produce and replay must
        # not change the replayed source set: replay uses the pinned scheme.
        rotated = CityOneScheme(
            city=city, scheme_status="ACTIVE",
            final_sources=("ncep_nbm_conus", "icon_global"),
            weights={"ncep_nbm_conus": 0.5, "icon_global": 0.5}, sample_n=30,
            walkforward_pass=True, one_scheme_status="GRID_CAP10_LIVE_READY",
        )
        monkeypatch.setattr(
            "src.strategy.live_inference.source_clock_city_weights.scheme_for_city",
            lambda *_args, **_kwargs: rotated,
        )
        assert adapter._posterior_bound_spine_inputs(
            conn, family=family, decision_time=decision,
            source_cycle_time=run.isoformat(), provenance=provenance, **posterior_kwargs,
        ) is not None
        monkeypatch.setattr(
            "src.strategy.live_inference.source_clock_city_weights.scheme_for_city",
            lambda *_args, **_kwargs: scheme,
        )
        unpinned = json.loads(json.dumps(provenance))
        unpinned["bayes_precision_fusion"]["source_clock_one_scheme"].pop("configured_weights")
        unpinned_reason: dict[str, str] = {}
        assert adapter._posterior_bound_multimodel_members(
            conn, family=family, decision_time=decision,
            source_cycle_time=run.isoformat(), provenance=unpinned,
            reason_out=unpinned_reason, **posterior_kwargs,
        ) is None
        assert unpinned_reason == {"reason": "model_identity_drift:pinned_scheme_missing"}
    if missing_hrrr and not shadowed_hrrr:
        for field, value in (
            ("missing_sources", []),
            ("configured_coherent_sources", ["gfs_hrrr"]),
            ("configured_current_sources", "icon_global"),
            ("configured_current_sources", ["icon_global", "icon_global"]),
        ):
            tampered = json.loads(json.dumps(provenance))
            tampered["bayes_precision_fusion"]["source_clock_one_scheme"][field] = value
            assert adapter._source_clock_model_count_certificate(
                tampered, family=family, decision_time=decision, **posterior_kwargs,
            ) == (True, None)
        tampered = json.loads(json.dumps(provenance))
        tampered["bayes_precision_fusion"]["source_clock_one_scheme"]["configured_cohort_decision_time"] = (
            decision - timedelta(minutes=1)
        ).isoformat()
        assert adapter._source_clock_model_count_certificate(
            tampered, family=family, decision_time=decision, **posterior_kwargs,
        ) == (True, None)
        tampered = json.loads(json.dumps(provenance))
        tampered["bayes_precision_fusion"]["current_value_serving"].pop("icon_global")
        assert adapter._source_clock_model_count_certificate(
            tampered, family=family, decision_time=decision, **posterior_kwargs,
        ) == (True, None)

        arrived = decision + timedelta(minutes=10)
        conn.execute(
            """INSERT INTO raw_model_forecasts (
                model, city, target_date, metric, source_cycle_time,
                source_available_at, captured_at, recorded_at, lead_days,
                forecast_value_c, endpoint, coverage_status
            ) VALUES ('gfs_hrrr', ?, '2026-10-01', ?, ?, ?, ?, ?, 1, 21.0,
                      'single_runs', 'COVERED')""",
            (city, metric, run.isoformat(), arrived.isoformat(),
             arrived.isoformat(), arrived.isoformat()),
        )
        _qualify_raw_fixture_rows(conn)
        # The same fully qualified future row is absent only because its actual
        # availability/capture is later than the original decision.
        assert adapter._posterior_bound_spine_inputs(
            conn, family=family, decision_time=decision,
            source_cycle_time=run.isoformat(), provenance=provenance, **posterior_kwargs,
        ) is not None
        later = arrived + timedelta(minutes=1)
        reason: dict[str, str] = {}
        assert adapter._posterior_bound_multimodel_members(
            conn, family=family, decision_time=later,
            source_cycle_time=run.isoformat(), provenance=provenance,
            reason_out=reason, **posterior_kwargs,
        ) is None
        assert reason == {"reason": "model_identity_drift:configured_current_sources"}

        refreshed = materializer_mod._replacement_bayes_precision_fusion_override(
            replace(request, computed_at=later), metric=metric,
            anchor_value_corrected_c=20.0, conn=conn,
        )
        assert refreshed is not None
        assert refreshed.method == "SOURCE_CLOCK_FIXED_WEIGHT"
        refreshed_provenance = {"bayes_precision_fusion": {
            "used_models": list(refreshed.used_models),
            "current_value_serving": refreshed.current_value_serving,
            "source_clock_one_scheme": refreshed.source_clock_one_scheme,
            "decorrelated_providers_expected": refreshed.decorrelated_providers_expected,
            "decorrelated_providers_served": refreshed.decorrelated_providers_served,
            "decorrelated_providers_complete": refreshed.decorrelated_providers_complete,
        }}
        assert adapter._posterior_bound_spine_inputs(
            conn, family=family, decision_time=later,
            source_cycle_time=run.isoformat(), provenance=refreshed_provenance,
        ) is not None
    if shadowed_hrrr and not split_cohort:
        newer_hrrr_cycle = run + timedelta(hours=6)
        arrived = newer_hrrr_cycle + timedelta(minutes=10)
        conn.execute(
            """INSERT INTO raw_model_forecasts (
                model, city, target_date, metric, source_cycle_time,
                source_available_at, captured_at, recorded_at, lead_days,
                forecast_value_c, endpoint, coverage_status
            ) VALUES ('gfs_hrrr', ?, '2026-10-01', ?, ?, ?, ?, ?, 1, 21.0,
                      'single_runs', 'COVERED')""",
            (city, metric, newer_hrrr_cycle.isoformat(), arrived.isoformat(),
             arrived.isoformat(), arrived.isoformat()),
        )
        _qualify_raw_fixture_rows(conn)
        assert adapter._posterior_bound_spine_inputs(
            conn, family=family, decision_time=decision,
            source_cycle_time=run.isoformat(), provenance=provenance, **posterior_kwargs,
        ) is not None
        reason: dict[str, str] = {}
        _qualify_raw_fixture_rows(conn)
        assert adapter._posterior_bound_multimodel_members(
            conn, family=family, decision_time=arrived + timedelta(minutes=1),
            source_cycle_time=run.isoformat(), provenance=provenance,
            reason_out=reason, **posterior_kwargs,
        ) is None
        # HRRR is consumed directly now, so its newer row names itself.
        assert reason == {"reason": "model_identity_drift:gfs_hrrr"}


@pytest.mark.usefixtures("_hko_source_surface")
@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize(("configured", "newer_sibling"), (
    # Scheme weights the LESS specific family member; the more specific one is newer.
    (("icon_global", "ncep_nbm_conus"), "gfs_hrrr"),
    (("ecmwf_ifs", "ncep_nbm_conus"), "gfs_hrrr"),
    (("ncep_nbm_conus", "ukmo_global_deterministic_10km"), "gfs_hrrr"),
    # Scheme weights the MORE specific member; the less specific one is newer.
    (("gfs_hrrr", "icon_global", "ukmo_global_deterministic_10km"), "ncep_nbm_conus"),
))
def test_source_clock_uses_exactly_the_scheme_sources_whatever_sibling_is_newer(
    monkeypatch: pytest.MonkeyPatch, metric: str,
    configured: tuple[str, ...], newer_sibling: str,
) -> None:
    """One law: a posterior's source set is the city scheme's weighted sources.

    A newer same-family sibling the scheme does not weight must neither replace a
    weighted source nor be added, whichever of the two is the more specific model.
    This US scheme component uses LA's own physical inputs, not Chicago authority.
    """
    from src.data.station_ground_evidence import archive_station_ground_evidence, forecast_db_from_connection
    from src.strategy.live_inference.source_clock_city_weights import CityOneScheme

    conn = _conn()
    city = "Los Angeles"
    assert archive_station_ground_evidence(forecast_db_from_connection(conn), [city])["status"] == "GROUND_SOURCE_ARCHIVED"
    run = datetime(2026, 9, 30, 18, tzinfo=UTC)
    decision = run + timedelta(hours=7)
    rows = [(model, run) for model in dict.fromkeys(("ecmwf_ifs", *configured))]
    # Both HRRR runs are real 48h cycles and cover the whole target local day.
    rows.append((newer_sibling, run + timedelta(hours=6)))
    for index, (model, cycle) in enumerate(rows):
        conn.execute(
            """INSERT INTO raw_model_forecasts (
                model, city, target_date, metric, source_cycle_time,
                source_available_at, captured_at, recorded_at, lead_days,
                forecast_value_c, endpoint, coverage_status
            ) VALUES (?, ?, '2026-10-01', ?, ?, ?, ?, ?, 1, ?, 'single_runs', 'COVERED')""",
            (model, city, metric, cycle.isoformat(),
             (cycle + timedelta(minutes=5)).isoformat(),
             (cycle + timedelta(minutes=10)).isoformat(),
             (cycle + timedelta(minutes=11)).isoformat(), 20.0 + index),
        )
    scheme = CityOneScheme(
        city=city, scheme_status="ACTIVE", final_sources=configured,
        weights=dict.fromkeys(configured, 1.0 / len(configured)), sample_n=30,
        walkforward_pass=True, one_scheme_status="GRID_CAP10_LIVE_READY",
    )
    monkeypatch.setattr(
        "src.strategy.live_inference.source_clock_city_weights.scheme_for_city",
        lambda *_args, **_kwargs: scheme,
    )
    capture = SimpleNamespace(
        has_extras=True, anchor_z=20.0, anchor_tau0=1.0,
        likelihood=tuple(SimpleNamespace(
            model=model, z=22.0, train_residuals=(), n_train=0, residuals_by_date={},
        ) for model, _cycle in rows if model != "ecmwf_ifs"),
        disagree_var=0.0, anchor_raw_m2_native=None, anchor_raw_n_train=0,
        dropped_models=(),
        selection=SimpleNamespace(excluded_regionals=(), dropped_aliases=()),
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_capture.capture_bayes_precision_instruments",
        lambda **_kwargs: capture,
    )
    monkeypatch.setattr(
        "src.forecast.bayes_precision_fusion.fuse_bayes_precision_posterior",
        lambda **_kwargs: SimpleNamespace(
            sd=0.5, method="TEST_FUSION",
            used_models=tuple(x.model for x in capture.likelihood), regional_models=(),
        ),
    )

    monkeypatch.setattr(materializer_mod, "_read_current_evidence_shape", _fixture_current_shape)
    request = _la_current_physical_request(conn, metric=metric, cycle=run, decision=decision)
    _qualify_raw_fixture_rows(conn)

    override = materializer_mod._replacement_bayes_precision_fusion_override(
        request, metric=metric, anchor_value_corrected_c=20.0, conn=conn,
    )

    assert override is not None
    assert override.method == "SOURCE_CLOCK_FIXED_WEIGHT"
    assert set(override.used_models) == set(configured)
    assert newer_sibling not in override.used_models
    conn.close()


@pytest.mark.usefixtures("_hko_source_surface")
def test_partial_current_replay_uses_the_pinned_scheme_not_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay must judge drift with the producer's pinned scheme, not ACTIVE.

    The producer's scheme weights icon/ukmo/gfs_hrrr; HRRR is missing so the
    posterior is partial-current.  When HRRR later arrives (older cycle than a new
    NBM row) the pinned scheme names the drift.  A replay that re-resolved a
    rotated ACTIVE (weighting NBM) would collapse HRRR away and miss it.
    """
    from src.data.station_ground_evidence import archive_station_ground_evidence, forecast_db_from_connection
    from src.engine import event_reactor_adapter as adapter
    from src.strategy.live_inference.source_clock_city_weights import CityOneScheme

    conn = _conn()
    city = "Los Angeles"
    assert archive_station_ground_evidence(forecast_db_from_connection(conn), [city])["status"] == "GROUND_SOURCE_ARCHIVED"
    metric = "high"
    run = datetime(2026, 9, 30, 18, tzinfo=UTC)
    decision = run + timedelta(hours=1)
    configured = ("icon_global", "ukmo_global_deterministic_10km", "gfs_hrrr")
    for index, model in enumerate(("ecmwf_ifs", "icon_global", "ukmo_global_deterministic_10km")):
        conn.execute(
            """INSERT INTO raw_model_forecasts (
                model, city, target_date, metric, source_cycle_time,
                source_available_at, captured_at, recorded_at, lead_days,
                forecast_value_c, endpoint, coverage_status
            ) VALUES (?, ?, '2026-10-01', ?, ?, ?, ?, ?, 1, ?, 'single_runs', 'COVERED')""",
            (model, city, metric, run.isoformat(),
             (run + timedelta(minutes=5)).isoformat(),
             (run + timedelta(minutes=10)).isoformat(),
             (run + timedelta(minutes=11)).isoformat(), 20.0 + index),
        )
    scheme = CityOneScheme(
        city=city, scheme_status="ACTIVE", final_sources=configured,
        weights=dict.fromkeys(configured, 1.0 / len(configured)), sample_n=30,
        walkforward_pass=True, one_scheme_status="GRID_CAP10_LIVE_READY",
    )
    monkeypatch.setattr(
        "src.strategy.live_inference.source_clock_city_weights.scheme_for_city",
        lambda *_args, **_kwargs: scheme,
    )
    capture = SimpleNamespace(
        has_extras=True, anchor_z=20.0, anchor_tau0=1.0,
        likelihood=tuple(SimpleNamespace(
            model=model, z=22.0, train_residuals=(), n_train=0, residuals_by_date={},
        ) for model in ("icon_global", "ukmo_global_deterministic_10km")),
        disagree_var=0.0, anchor_raw_m2_native=None, anchor_raw_n_train=0,
        dropped_models=(),
        selection=SimpleNamespace(excluded_regionals=(), dropped_aliases=()),
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_capture.capture_bayes_precision_instruments",
        lambda **_kwargs: capture,
    )
    monkeypatch.setattr(
        "src.forecast.bayes_precision_fusion.fuse_bayes_precision_posterior",
        lambda **_kwargs: SimpleNamespace(
            sd=0.5, method="TEST_FUSION",
            used_models=("icon_global", "ukmo_global_deterministic_10km"), regional_models=(),
        ),
    )

    monkeypatch.setattr(materializer_mod, "_read_current_evidence_shape", _fixture_current_shape)
    request = _la_current_physical_request(conn, metric=metric, cycle=run, decision=decision)
    _qualify_raw_fixture_rows(conn)
    override = materializer_mod._replacement_bayes_precision_fusion_override(
        request, metric=metric, anchor_value_corrected_c=20.0, conn=conn,
    )
    assert override is not None
    scheme_proof = override.source_clock_one_scheme
    assert scheme_proof["fallback_reason"] == "configured_current_provider_set_incomplete"
    provenance = {"bayes_precision_fusion": {
        "used_models": list(override.used_models),
        "current_value_serving": override.current_value_serving,
        "source_clock_one_scheme": scheme_proof,
        "decorrelated_providers_expected": override.decorrelated_providers_expected,
        "decorrelated_providers_served": override.decorrelated_providers_served,
        "decorrelated_providers_complete": override.decorrelated_providers_complete,
    }}
    family = SimpleNamespace(city=city, target_date="2026-10-01", metric=metric)
    # Replay time: an OLD HRRR row (served but not newest) and a NEWER NBM row
    # exist.  The producer's pinned scheme weights HRRR, so its collapse keeps
    # HRRR and the configured-current set gains it -> genuine drift is named.
    # ACTIVE has rotated to weight NBM instead; a replay that re-resolved ACTIVE
    # would drop HRRR as the stale sibling and silently miss that drift.
    for model, cycle, value in (
        ("gfs_hrrr", run - timedelta(hours=6), 21.0),
        ("ncep_nbm_conus", run, 24.0),
    ):
        conn.execute(
            """INSERT INTO raw_model_forecasts (
                model, city, target_date, metric, source_cycle_time,
                source_available_at, captured_at, recorded_at, lead_days,
                forecast_value_c, endpoint, coverage_status
            ) VALUES (?, ?, '2026-10-01', ?, ?, ?, ?, ?, 1, ?, 'single_runs', 'COVERED')""",
            (model, city, metric, cycle.isoformat(),
             (decision + timedelta(minutes=1)).isoformat(),
             (decision + timedelta(minutes=1)).isoformat(),
             (decision + timedelta(minutes=1)).isoformat(), value),
        )
    _qualify_raw_fixture_rows(conn)
    rotated = CityOneScheme(
        city=city, scheme_status="ACTIVE",
        final_sources=("icon_global", "ukmo_global_deterministic_10km", "ncep_nbm_conus"),
        weights={
            "icon_global": 1 / 3, "ukmo_global_deterministic_10km": 1 / 3,
            "ncep_nbm_conus": 1 / 3,
        },
        sample_n=30, walkforward_pass=True, one_scheme_status="GRID_CAP10_LIVE_READY",
    )
    monkeypatch.setattr(
        "src.strategy.live_inference.source_clock_city_weights.scheme_for_city",
        lambda *_args, **_kwargs: rotated,
    )
    later = decision + timedelta(minutes=2)
    pinned_reason: dict[str, str] = {}
    assert adapter._posterior_bound_multimodel_members(
        conn, family=family, decision_time=later,
        source_cycle_time=run.isoformat(), provenance=provenance,
        reason_out=pinned_reason, posterior_computed_at=decision,
    ) is None
    assert pinned_reason == {"reason": "model_identity_drift:configured_current_sources"}
    conn.close()


@pytest.mark.usefixtures("_hko_source_surface")
def test_source_clock_scheme_pinned_once_per_posterior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selection, collapse and fixed-weight center use ONE scheme resolution.

    A weekly ACTIVE rotation landing mid-computation must not hand the center a
    different basket than the one that selected the sources, and the posterior
    must pin the basket it used.
    LA supplies the US component's real physical premises, not Chicago authority.
    """
    from src.data.station_ground_evidence import archive_station_ground_evidence, forecast_db_from_connection
    from src.strategy.live_inference.source_clock_city_weights import CityOneScheme

    conn = _conn()
    city = "Los Angeles"
    assert archive_station_ground_evidence(forecast_db_from_connection(conn), [city])["status"] == "GROUND_SOURCE_ARCHIVED"
    run = datetime(2026, 9, 30, 18, tzinfo=UTC)
    for index, model in enumerate(("ecmwf_ifs", "icon_global", "ncep_nbm_conus")):
        conn.execute(
            """INSERT INTO raw_model_forecasts (
                model, city, target_date, metric, source_cycle_time,
                source_available_at, captured_at, recorded_at, lead_days,
                forecast_value_c, endpoint, coverage_status
            ) VALUES (?, ?, '2026-10-01', 'low', ?, ?, ?, ?, 1, ?, 'single_runs', 'COVERED')""",
            (model, city, run.isoformat(), (run + timedelta(minutes=5)).isoformat(),
             (run + timedelta(minutes=10)).isoformat(),
             (run + timedelta(minutes=11)).isoformat(), 17.0 + index),
        )
    first = CityOneScheme(
        city=city, scheme_status="ACTIVE", final_sources=("icon_global", "ncep_nbm_conus"),
        weights={"icon_global": 0.5, "ncep_nbm_conus": 0.5}, sample_n=30,
        walkforward_pass=True, one_scheme_status="GRID_CAP10_LIVE_READY",
    )
    rotated = CityOneScheme(
        city=city, scheme_status="ACTIVE", final_sources=("ecmwf_ifs", "icon_global"),
        weights={"ecmwf_ifs": 0.5, "icon_global": 0.5}, sample_n=30,
        walkforward_pass=True, one_scheme_status="GRID_CAP10_LIVE_READY",
    )
    calls: list[int] = []

    def rotating(*_args, **_kwargs):
        calls.append(1)
        return first if len(calls) == 1 else rotated

    monkeypatch.setattr(
        "src.strategy.live_inference.source_clock_city_weights.scheme_for_city", rotating,
    )
    capture = SimpleNamespace(
        has_extras=True, anchor_z=17.0, anchor_tau0=1.0,
        likelihood=tuple(SimpleNamespace(
            model=model, z=17.5, train_residuals=(), n_train=0, residuals_by_date={},
        ) for model in ("icon_global", "ncep_nbm_conus")),
        disagree_var=0.0, anchor_raw_m2_native=None, anchor_raw_n_train=0,
        dropped_models=(),
        selection=SimpleNamespace(excluded_regionals=(), dropped_aliases=()),
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_capture.capture_bayes_precision_instruments",
        lambda **_kwargs: capture,
    )
    monkeypatch.setattr(
        "src.forecast.bayes_precision_fusion.fuse_bayes_precision_posterior",
        lambda **_kwargs: SimpleNamespace(
            sd=0.5, method="TEST_FUSION",
            used_models=("icon_global", "ncep_nbm_conus"), regional_models=(),
        ),
    )

    monkeypatch.setattr(materializer_mod, "_read_current_evidence_shape", _fixture_current_shape)
    request = _la_current_physical_request(conn, metric="low", cycle=run, decision=run+timedelta(hours=1))
    _qualify_raw_fixture_rows(conn)

    override = materializer_mod._replacement_bayes_precision_fusion_override(
        request, metric="low", anchor_value_corrected_c=17.0, conn=conn,
    )

    assert override is not None
    assert set(override.used_models) == {"icon_global", "ncep_nbm_conus"}
    assert override.source_clock_one_scheme["configured_weights"] == dict(first.weights)
    assert len(calls) == 1
    conn.close()


def test_posterior_identity_binds_day0_carrier_operator_and_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """Equal q values must not alias carrier certificates across migrations."""

    conn = _conn()
    request = _request(anchor_artifact_id=17)
    monkeypatch.setattr(materializer_mod, "emit_materialization_latency", lambda **kwargs: None)

    def result(*, identity: str | None = None, operator: str | None = None) -> SimpleNamespace:
        provenance = {}
        if identity is not None:
            provenance["day0_remaining_carrier_content_identity"] = identity
        if operator is not None:
            provenance["day0_remaining_carrier_operator"] = operator
        return SimpleNamespace(
            live_eligible=True,
            q={"cold": 0.2, "warm": 0.8},
            q_lcb_map={"cold": 0.1, "warm": 0.7},
            q_ucb_map={"cold": 0.3, "warm": 0.9},
            data_version="high",
            source_cycle_time="2026-06-06T00:00:00+00:00",
            available_at="2026-06-06T03:00:00+00:00",
            computed_at="2026-06-06T04:00:00+00:00",
            runtime_layer=LIVE_RUNTIME_LAYER,
            dependency_payload={"baseline_b0": "b0-run"},
            dependency_hash="dependency-hash",
            bin_topology_hash="topology-hash",
            posterior_config_hash="config-hash",
            family_id="Shanghai:2026-06-07:high:family",
            provenance_payload=provenance,
        )

    ordinary_id = materializer_mod._write_posterior_row(
        conn, request, metric="high", anchor_id=17, result=result()
    )
    assert materializer_mod._write_posterior_row(
        conn, request, metric="high", anchor_id=17, result=result()
    ) == ordinary_id

    v1_id = materializer_mod._write_posterior_row(
        conn,
        request,
        metric="high",
        anchor_id=17,
        result=result(
            identity="carrier-content-v1",
            operator="extreme_observed_then_noisy_future_v1",
        ),
    )
    v2_id = materializer_mod._write_posterior_row(
        conn,
        request,
        metric="high",
        anchor_id=17,
        result=result(
            identity="carrier-content-v1",
            operator="extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
        ),
    )
    v2_content_id = materializer_mod._write_posterior_row(
        conn,
        request,
        metric="high",
        anchor_id=17,
        result=result(
            identity="carrier-content-v2",
            operator="extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
        ),
    )

    assert len({ordinary_id, v1_id, v2_id, v2_content_id}) == 4
    assert conn.execute("SELECT count(*) FROM forecast_posteriors").fetchone()[0] == 4
    hashes = conn.execute(
        "SELECT posterior_identity_hash FROM forecast_posteriors ORDER BY posterior_id"
    ).fetchall()
    assert len({row[0] for row in hashes}) == 4


def test_prewrite_blocks_precision_metadata_from_another_target_day() -> None:
    stale_precision = _precision_guard(
        target_local_date=date(2026, 6, 6),
        local_day_start_utc=datetime(2026, 6, 5, 16, tzinfo=UTC),
        local_day_end_utc=datetime(2026, 6, 6, 16, tzinfo=UTC),
    )

    reasons = materializer_mod._prewrite_block_reasons(
        _request(openmeteo_precision_guard=stale_precision)
    )

    assert "REPLACEMENT_MATERIALIZATION_OM9_TARGET_SCOPE_MISMATCH" in reasons


def _day0_owner_witness(
    request: ReplacementForecastMaterializeRequest,
    *,
    seed_file: Path,
) -> Day0EnqueueOwnershipWitness:
    identity = cycle_advance._day0_conditioning_identity(
        source=request.day0_observed_extreme_source,
        observation_time=request.day0_observed_extreme_observation_time,
        observed_extreme_c=request.day0_observed_extreme_c,
        unit=request.day0_observed_extreme_unit,
    )
    assert identity is not None
    return Day0EnqueueOwnershipWitness(
        city=request.city,
        target_date=request.target_date.isoformat(),
        metric=request.temperature_metric,
        target_cycle_time=request.source_cycle_time.isoformat(),
        seed_file=str(seed_file),
        conditioning_identity=identity,
    )


def _record_day0_owner(
    conn: sqlite3.Connection,
    request: ReplacementForecastMaterializeRequest,
    witness: Day0EnqueueOwnershipWitness,
) -> None:
    assert cycle_advance._record_enqueue(
        conn,
        city=witness.city,
        target_date=witness.target_date,
        metric=witness.metric,
        consumed_cycle_iso=witness.target_cycle_time,
        target_cycle_iso=witness.target_cycle_time,
        held_position=True,
        seed_file=witness.seed_file,
        day0_observed_extreme_source=request.day0_observed_extreme_source,
        day0_observed_extreme_observation_time=(
            request.day0_observed_extreme_observation_time
        ),
        day0_observed_extreme_c=request.day0_observed_extreme_c,
        day0_observed_extreme_unit=request.day0_observed_extreme_unit,
    ) is True
    conn.commit()


def _prepare_for_final_write(
    conn: sqlite3.Connection,
    request: ReplacementForecastMaterializeRequest,
):
    conn.execute("BEGIN")
    prepared = materializer_mod.prepare_replacement_forecast_live(conn, request)
    conn.rollback()
    assert isinstance(
        prepared, materializer_mod.PreparedReplacementForecastMaterialization
    )
    return prepared


def test_missing_day0_hourly_carrier_is_a_blocked_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unchanged missing vector is terminal until its evidence frontier moves."""

    conn = sqlite3.connect(":memory:")
    request = _request()
    monkeypatch.setattr(
        materializer_mod,
        "_validated_replacement_forecast_request",
        lambda *_args, **_kwargs: (request, "high"),
    )
    monkeypatch.setattr(
        materializer_mod,
        "_day0_ledger_frontier_identity",
        lambda *_args, **_kwargs: None,
    )

    def missing(*_args, **_kwargs):
        raise ValueError("DAY0_NOAA_PRELIMINARY_CARRIER_VECTOR_MISSING")

    monkeypatch.setattr(materializer_mod, "_compute_posterior_payload", missing)

    result = materializer_mod.prepare_replacement_forecast_live(conn, request)

    assert isinstance(result, materializer_mod.ReplacementForecastMaterializeResult)
    assert result.status == "BLOCKED"
    assert result.reason_codes == (
        "DAY0_NOAA_PRELIMINARY_CARRIER_VECTOR_MISSING",
    )


def _shanghai_current_owner_request(tmp_path, monkeypatch, *, metric="high",
    target_date=date(2026,10,2),source_cycle_time=datetime(2026,10,1,tzinfo=UTC),
    computed_at=None,first_compute_at=None,expires_at=None,observed_extreme=None,
    observed_sample_count=12,ground_recorded_at=None,ground_registry_factory=None,
    ground_captured_at=None,record_observed_prints=True):
    """Normal owned ground/anchor/provider proof with controlled ENS/math inputs.

    The actual HOMR body was captured Sep30. Move the entire external forecast
    condition to Oct1 -> Oct2, not that evidence's possession back to June.
    """
    from src.config import runtime_cities_by_name, runtime_station_geometry_for_city
    from src.data import station_ground_evidence as ground
    from src.data.openmeteo_ecmwf_ifs9_anchor import extract_openmeteo_ecmwf_ifs9_localday_anchor
    from src.data.openmeteo_ecmwf_ifs9_bucket_transport import source_cell_geometry_proof
    from tests.test_config import _official_international_homr_registry
    from tests.test_openmeteo_cell_selection_and_elevation_are_product_identity import _selected_test_cell

    registry_factory = ground_registry_factory or _official_international_homr_registry
    registry_factory(tmp_path, monkeypatch, "Shanghai")
    assert metric in {"high","low"}
    cycle = source_cycle_time
    computed = computed_at or cycle+timedelta(hours=18)
    first = first_compute_at or computed
    target = target_date
    assert cycle+timedelta(hours=3) <= first <= computed
    ground_recorded = ground_recorded_at if ground_recorded_at is not None else cycle-timedelta(hours=1)
    assert ground_recorded.tzinfo is not None and ground_recorded <= first
    class GroundClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return ground_recorded.astimezone(tz or UTC)
    monkeypatch.setattr(ground, "datetime", GroundClock)
    conn = _conn(archive_ground=False)
    db = ground.forecast_db_from_connection(conn)
    assert ground.archive_station_ground_evidence(db, ["Shanghai"])["status"] == "GROUND_SOURCE_ARCHIVED"
    evidence = ground.read_current_station_ground_evidence(db, city="Shanghai", decision_at=computed)
    captured = datetime.fromisoformat(evidence["captured_at"].replace("Z", "+00:00"))
    recorded = datetime.fromisoformat(evidence["recorded_at"])
    assert captured == (ground_captured_at or datetime(2026, 9, 30, 12, 52, 1, tzinfo=UTC))
    assert captured <= recorded == ground_recorded <= first <= computed
    city = runtime_cities_by_name()["Shanghai"]
    station = runtime_station_geometry_for_city(city, effective_at=computed)
    cell = source_cell_geometry_proof(latitude=city.lat,longitude=city.lon,target_elevation_m=station["ground_elevation_m"])
    lon = cell["selected_grid_lon"] - 360 if cell["selected_grid_lon"] > 180 else cell["selected_grid_lon"]
    body = json.dumps({"latitude":cell["selected_grid_lat"],"longitude":lon,
        "elevation":station["ground_elevation_m"],"timezone":city.timezone,"utc_offset_seconds":28800,
        "hourly_units":{"temperature_2m":"°C"},
        "hourly":{"time":[f"{target}T{hour:02d}:00" for hour in range(24)],
                  "temperature_2m":[27. if hour==12 else 18.5 for hour in range(24)]},
        "_zeus_current_target_scope":{"city":city.name,"target_date":str(target),"metric":metric}},sort_keys=True).encode()
    anchor = extract_openmeteo_ecmwf_ifs9_localday_anchor(json.loads(body),city_timezone=city.timezone,
        target_local_date=target,source_cycle_time=cycle)
    assert cycle <= anchor.contributing_valid_times_utc[0]
    assert anchor.high_c == 27. and anchor.low_c == 18.5
    template = replace(_request(openmeteo_precision_guard=None,baseline_data_version=_current_baseline_data_version(metric)),
        target_date=target,temperature_metric=metric,
        source_cycle_time=cycle,expires_at=expires_at or computed+timedelta(hours=8),
        openmeteo_source_available_at=cycle+timedelta(hours=3),baseline_source_available_at=cycle+timedelta(hours=2),
        openmeteo_anchor=anchor,openmeteo_raw_payload_bytes=body)
    cells = {model:_selected_test_cell(model,city.lat,city.lon)
             for model in ("icon_global","ukmo_global_deterministic_10km")}
    assert all(abs(lat-city.lat)<.2 and abs(lon-city.lon)<.2 for lat,lon in cells.values())
    def stage(cut):
        active = anchor.contributing_valid_times_utc[0] <= cut
        assert cut < anchor.contributing_valid_times_utc[-1]+timedelta(hours=1)
        value = observed_extreme if observed_extreme is not None else (26. if metric=="high" else 19.)
        request = replace(template,computed_at=cut,
            day0_observed_extreme_c=value if active else None,
            day0_observed_extreme_source="noaa_wrh_zspd" if active else None,
            day0_observed_extreme_observation_time=(cut-timedelta(minutes=5)).isoformat() if active else None,
            day0_observed_extreme_sample_count=observed_sample_count if active else None,
            day0_observed_extreme_unit="C" if active else None)
        return _install_hko_live_fusion(monkeypatch,conn=conn,request=request,selected_cells=cells,
                                        snapshot_id=9001 if metric=="high" else 9002)
    request = stage(first)
    first_entities = tuple(tuple(row) for row in conn.execute("SELECT * FROM raw_forecast_artifacts ORDER BY artifact_id"))
    first_raw = tuple(tuple(row) for row in conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id"))
    if first != computed:
        request = stage(computed)
        assert tuple(tuple(row) for row in conn.execute("SELECT * FROM raw_forecast_artifacts ORDER BY artifact_id")) == first_entities
        assert tuple(tuple(row) for row in conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id")) == first_raw
    assert request.city == "Shanghai" and request.openmeteo_precision_guard.passable_for_live_materialization
    if record_observed_prints and request.day0_observed_extreme_c is not None:
        _append_shanghai_owner_prints(conn,request)
    return conn,request


@pytest.mark.usefixtures("_hko_source_surface")
@pytest.mark.parametrize("metric", ("high", "low"))
def test_shanghai_owner_fixture_keeps_ground_possession_independent_of_run(tmp_path, monkeypatch, metric):
    """Controlled forecast inputs; real retained HOMR capture, not native GRIB."""
    from src.data import station_ground_evidence as ground

    cycle = datetime(2026,9,30,12,tzinfo=UTC)
    ground_recorded = cycle+timedelta(hours=1)
    first = cycle+timedelta(hours=8,minutes=5)
    cut = datetime(2026,10,1,8,15,tzinfo=UTC)
    conn,request = _shanghai_current_owner_request(tmp_path,monkeypatch,metric=metric,
        target_date=date(2026,10,1),source_cycle_time=cycle,computed_at=cut,first_compute_at=first,
        ground_recorded_at=ground_recorded)
    db = ground.forecast_db_from_connection(conn)
    evidence = ground.read_current_station_ground_evidence(db,city=request.city,decision_at=cut)
    assert datetime.fromisoformat(evidence["recorded_at"]) == ground_recorded > cycle
    assert datetime.fromisoformat(evidence["captured_at"].replace("Z","+00:00")) < ground_recorded < first <= cut
    assert ground.read_current_station_ground_evidence(db,city=request.city,
        decision_at=ground_recorded-timedelta(microseconds=1)) is None
    rows = tuple(tuple(row) for row in conn.execute("SELECT * FROM raw_forecast_artifacts ORDER BY artifact_id"))
    assert ground.archive_station_ground_evidence(db,[request.city])["status"] == "GROUND_SOURCE_ARCHIVED"
    assert tuple(tuple(row) for row in conn.execute("SELECT * FROM raw_forecast_artifacts ORDER BY artifact_id")) == rows
    assert ground.read_current_station_ground_evidence(db,city=request.city,decision_at=cut) == evidence
    assert materializer_mod._precision_guard_block_reason(request,conn) == ()
    conn.close()


def _refresh_shanghai_owner_request(conn, monkeypatch, request, *, record_observed_prints=True):
    """Rebind the new analysis cut without renewing ordinary source entities."""
    from src.config import runtime_cities_by_name
    from tests.test_openmeteo_cell_selection_and_elevation_are_product_identity import _selected_test_cell

    city = runtime_cities_by_name()[request.city]
    cells = {model:_selected_test_cell(model,city.lat,city.lon)
             for model in ("icon_global","ukmo_global_deterministic_10km")}
    entities = tuple(tuple(row) for row in conn.execute("SELECT * FROM raw_forecast_artifacts ORDER BY artifact_id"))
    raw = tuple(tuple(row) for row in conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id"))
    refreshed = _install_hko_live_fusion(monkeypatch,conn=conn,request=request,selected_cells=cells,
        snapshot_id=9001 if request.temperature_metric=="high" else 9002)
    assert tuple(tuple(row) for row in conn.execute("SELECT * FROM raw_forecast_artifacts ORDER BY artifact_id")) == entities
    assert tuple(tuple(row) for row in conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id")) == raw
    if record_observed_prints and refreshed.day0_observed_extreme_c is not None and refreshed.day0_observed_extreme_source=="noaa_wrh_zspd":
        _append_shanghai_owner_prints(conn,refreshed)
    return refreshed


def _append_shanghai_owner_prints(conn,request):
    """Controlled WRH body through its ordinary native-product print writer."""
    from src.data.noaa_wrh_timeseries import rows_from_payload
    from src.data.daily_obs_append import _append_noaa_wrh_prints
    from src.state.schema.observation_prints_schema import ensure_table
    ensure_table(conn)
    observed = datetime.fromisoformat(request.day0_observed_extreme_observation_time)
    count = request.day0_observed_extreme_sample_count
    assert isinstance(count,int) and count > 0
    instants = [observed-timedelta(minutes=count-1-index) for index in range(count)]
    values = [25.+index*.05 for index in range(count-1)]+[request.day0_observed_extreme_c]
    body = {"STATION":[{"STID":"ZSPD","OBSERVATIONS":{
        "date_time":[at.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S%z") for at in instants],
        "air_temp_set_1":values,"sea_level_pressure_set_1":[1010]*count,
        "metar_set_1":[f"ZSPD {at:%d%H%M}Z 26/20 T02600200" for at in instants]}}]}
    rows = rows_from_payload(body,station="ZSPD")
    assert len(rows)==request.day0_observed_extreme_sample_count
    _append_noaa_wrh_prints(conn,city_name=request.city,station="ZSPD",unit="C",rows=rows,
        target_date_local=request.target_date,view="all",fetch_utc=request.computed_at-timedelta(minutes=1))
    conn.commit()


def _shanghai_noaa_future_request(tmp_path, monkeypatch, *, metric="high", without_current_state=False,
                                   absorbing_extreme=None,current_temp_c=30.):
    """Controlled WRH/AWC bodies and complete remaining vectors, not gate mocks."""
    from src.config import runtime_cities_by_name
    from src.data import day0_fast_obs as fast, day0_hourly_vectors as hourly
    from src.data.noaa_wrh_timeseries import rows_from_payload
    from src.data.daily_obs_append import _append_noaa_wrh_prints
    from src.data.bayes_precision_fusion_capture import OPENMETEO_MODEL_IDS
    from zoneinfo import ZoneInfo

    extreme = absorbing_extreme if absorbing_extreme is not None else (31. if metric=="high" else 19.)
    from src.state.schema.observation_prints_schema import ensure_table
    # The owner fixture's own current WRH print would itself be today's current state.
    conn, prior = _shanghai_current_owner_request(tmp_path,monkeypatch,metric=metric,observed_extreme=extreme,
                                                  record_observed_prints=False)
    ensure_table(conn)
    city = runtime_cities_by_name()[prior.city]
    cut = prior.computed_at+timedelta(minutes=10)
    observed = cut-timedelta(minutes=5)
    source = fast.fast_obs_source_for_city(city,prior.target_date)
    assert source is not None and source.station_id=="ZSPD"
    # Prior local-day pairs train the likelihood without themselves supplying
    # today's instantaneous state in the single-fault missing-state tests.
    history = [observed-timedelta(hours=index+4) for index in range(fast.FAST_RESIDUAL_MIN_PAIRS)]
    timestamps = [at.astimezone(ZoneInfo(city.timezone)).strftime("%Y-%m-%dT%H:%M:%S%z") for at in history]
    raw = [f"ZSPD {at:%d%H%M}Z {round(extreme):02d}/15 T{round(extreme*10):04d}0150" for at in history]
    product = {"STATION":[{"STID":"ZSPD","OBSERVATIONS":{
        "date_time":timestamps,"air_temp_set_1":[extreme]*len(history),
        "sea_level_pressure_set_1":[1010]*len(history),"metar_set_1":raw}}]}
    rows = rows_from_payload(product,station="ZSPD")
    historical_capture = prior.computed_at-timedelta(minutes=10)
    for target in {date.fromisoformat(row.local_date) for row in rows}:
        _append_noaa_wrh_prints(conn,city_name=city.name,station="ZSPD",unit="C",rows=rows,
            target_date_local=target,view="all",fetch_utc=historical_capture)
    reports = fast.parse_metar_api_payload([{"icaoId":"ZSPD","obsTime":at.timestamp(),
        "receiptTime":(at+timedelta(seconds=30)).isoformat(),"temp":extreme,
        "metarType":"METAR","rawOb":text} for at,text in zip(history,raw,strict=True)])
    writer_time = [historical_capture]
    class ClockType(type):
        def __instancecheck__(cls,value):
            return isinstance(value,datetime)
    class WriterClock(datetime,metaclass=ClockType):
        @classmethod
        def now(cls,tz=None):
            return writer_time[0].astimezone(tz) if tz else writer_time[0].replace(tzinfo=None)
    monkeypatch.setattr(fast,"datetime",WriterClock)
    assert fast._append_metar_prints_to_ledger(conn,((city,source,prior.target_date.isoformat()),),reports)
    writer_time[0] = observed+timedelta(minutes=1)
    current = fast.parse_metar_api_payload([{"icaoId":"ZSPD","obsTime":observed.timestamp(),
        "receiptTime":writer_time[0].isoformat(),"temp":current_temp_c,"metarType":"METAR",
        "rawOb":f"ZSPD {observed:%d%H%M}Z {round(current_temp_c):02d}/20 T{round(current_temp_c*10):04d}0200"}])
    captured = cut-timedelta(minutes=2)
    models = hourly.day0_hourly_models_for_city(city)
    ensemble_models = hourly.day0_source_clock_ensemble_member_models()
    times = [f"{prior.target_date}T{hour:02d}:00" for hour in range(24)]
    for model in (*models,"__ensemble"):
        ensemble = model=="__ensemble"
        api = "ecmwf_ifs025_ensemble" if ensemble else OPENMETEO_MODEL_IDS.get(model,model)
        payload = {"latitude":city.lat,"longitude":city.lon,"timezone":city.timezone,"utc_offset_seconds":28800,
            "hourly_units":{"temperature_2m":"°C"},"hourly":{"time":times,"temperature_2m":[30.]*24}}
        if ensemble:
            for index in range(51):
                key = "temperature_2m" if index==0 else f"temperature_2m_member{index:02d}"
                payload["hourly"][key] = [30.+(index-25)*.02]*24
        endpoint = hourly.OPENMETEO_ENSEMBLE_URL if ensemble else "https://single-runs-api.open-meteo.com/v1/forecast"
        params = {"latitude":city.lat,"longitude":city.lon,"timezone":city.timezone,"hourly":"temperature_2m",
            "models":hourly.DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL if ensemble else api,"run":prior.source_cycle_time.isoformat()}
        if ensemble:
            params["metadata_model"] = hourly.DAY0_SOURCE_CLOCK_ENSEMBLE_METADATA_MODEL
        request_hash = hourly.build_request_hash(endpoint=endpoint,params=params,
            models=list(ensemble_models) if ensemble else [model],captured_at=captured.isoformat(),payload=payload)
        def metadata_for(member):
            return hourly._day0_provider_run_meta(model=member,
                model_api_id=hourly.DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL if ensemble else api,
                run=prior.source_cycle_time,available_at=prior.source_cycle_time+timedelta(hours=8),
                modified_at=prior.source_cycle_time+timedelta(hours=8),
                authority="provider_meta_declared" if ensemble else "run_pinned_single_runs",
                endpoint_mode="ensemble_meta_stamped" if ensemble else "single_runs",
                request_params={**params,"endpoint":endpoint},request_hash=request_hash,
                fetch_started_at=captured,fetch_finished_at=captured)
        metadata = metadata_for(model)
        vectors = hourly.parse_openmeteo_ensemble_hourly_payload(payload,city=city,captured_at=captured.isoformat(),
            source_meta_by_member={member:metadata_for(member) for member in ensemble_models}) if ensemble else hourly.parse_openmeteo_hourly_payload(
                payload,city=city,models=[model],captured_at=captured.isoformat(),source_run_meta_json=json.dumps(metadata))
        assert len(vectors)==(51 if ensemble else 1)
        assert hourly.persist_day0_hourly_vectors(vectors,target_date=prior.target_date.isoformat(),conn=conn,
            request_hash=request_hash,endpoint=endpoint,now=cut)==len(vectors)
    conn.commit()
    request = _refresh_shanghai_owner_request(conn,monkeypatch,replace(prior,computed_at=cut,
        day0_observed_extreme_c=(max if metric=="high" else min)(extreme,current_temp_c),
        day0_observed_extreme_source="aviationweather_metar",day0_observed_extreme_observation_time=observed.isoformat()))
    tables = ("raw_forecast_artifacts","raw_model_forecasts","observation_prints",
              "deterministic_forecast_anchors","forecast_posteriors","readiness_state")
    before_control = {table:tuple(tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid"))
                      for table in tables}
    conn.execute("SAVEPOINT current_print_control")
    # The provisional running extreme and the latest instantaneous state are
    # different inputs. Retain a real earlier AWC peak, not just a WRH value
    # relabeled as an AWC boundary; the newest print remains current_temp_c.
    peak_at = datetime.fromisoformat(prior.day0_observed_extreme_observation_time)
    writer_time[0] = prior.computed_at-timedelta(minutes=1)
    peak = fast.parse_metar_api_payload([{"icaoId":"ZSPD","obsTime":peak_at.timestamp(),
        "receiptTime":(peak_at+timedelta(minutes=1)).isoformat(),"temp":extreme,"metarType":"METAR",
        "rawOb":f"ZSPD {peak_at:%d%H%M}Z {round(extreme):02d}/15 T{round(extreme*10):04d}0150"}])
    assert fast._append_metar_prints_to_ledger(conn,((city,source,str(prior.target_date)),),peak)
    writer_time[0] = observed+timedelta(minutes=1)
    assert fast._append_metar_prints_to_ledger(conn,((city,source,prior.target_date.isoformat()),),current)
    likelihood = fast.build_fast_station_residual_likelihood(conn,city=city.name,target_date=str(prior.target_date),
        metric=metric,observed_source="aviationweather_metar",observation_time=observed,decision_time=cut)
    assert likelihood is not None and likelihood.matched_pairs>=fast.FAST_RESIDUAL_MIN_PAIRS
    state = hourly.read_day0_current_temperature_state(conn=conn,city=city,target_date=str(prior.target_date),decision_time=cut)
    assert state is not None and state.observed_at==observed
    conn.execute("SAVEPOINT qualified_noaa_control")
    control = materialize_replacement_forecast_live(conn,request)
    assert control.ok is True, control.reason_codes
    conn.execute("ROLLBACK TO qualified_noaa_control")
    conn.execute("RELEASE qualified_noaa_control")
    if without_current_state:
        # Private counterfactual only: roll back an uncommitted control input,
        # never DELETE/UPDATE a committed append-only publication.
        conn.execute("ROLLBACK TO current_print_control")
        assert all(tuple(tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid"))==before_control[table]
                   for table in tables)
        assert hourly.read_day0_current_temperature_state(conn=conn,city=city,
            target_date=str(prior.target_date),decision_time=cut) is None
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0
    conn.execute("RELEASE current_print_control")
    conn.commit()
    return conn,request


@pytest.mark.usefixtures("_hko_source_surface")
def test_day0_owner_witness_allows_current_owner_posterior_write(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unchanged owner writes once; current NOAA fixture is not June WU licensing."""
    conn, request = _shanghai_current_owner_request(tmp_path,monkeypatch)
    # Positive-first native possession: this component's controlled 51-member
    # input now passes the ordinary collector, not an invented snapshot 9001.
    from src.data.replacement_forecast_source_run_identity import native_coordinate_certificate_reason
    fusion = materializer_mod._replacement_bayes_precision_fusion_override()
    shape = fusion.current_evidence_shape
    assert native_coordinate_certificate_reason(conn,shape=shape,city=request.city,
        target_date=request.target_date,metric=request.temperature_metric) is None
    assert materializer_mod._fusion_current_evidence_shape_has_live_authority(fusion,request=request,conn=conn)
    missing = {**shape, "snapshot_id": 9001}
    assert conn.execute("SELECT 1 FROM ensemble_snapshots WHERE snapshot_id=9001").fetchone() is None
    assert native_coordinate_certificate_reason(conn,shape=missing,city=request.city,
        target_date=request.target_date,metric=request.temperature_metric) == "REPLACEMENT_CURRENT_COORDINATE_SNAPSHOT_MISSING"
    assert not materializer_mod._fusion_current_evidence_shape_has_live_authority(
        replace(fusion,current_evidence_shape=missing),request=request,conn=conn)
    native_before = {table:tuple(tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid"))
                     for table in ("ensemble_snapshots","source_run","source_run_coverage")}
    rebound, _, members = _fixture_native_shape_identity(conn,replace(request,computed_at=request.computed_at+timedelta(minutes=1)),
        monkeypatch,members_c=fusion.current_evidence_members_c)
    assert rebound["snapshot_id"] == shape["snapshot_id"]
    assert members == pytest.approx(fusion.current_evidence_members_c)
    assert all(tuple(tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")) == before
               for table,before in native_before.items())
    witness = _day0_owner_witness(request, seed_file=tmp_path / "owner-a.json")
    _record_day0_owner(conn, request, witness)
    prepared = _prepare_for_final_write(
        conn, replace(request, day0_enqueue_owner_witness=witness)
    )

    conn.execute("BEGIN IMMEDIATE")
    result = materializer_mod.write_prepared_replacement_forecast_live(conn, prepared)
    conn.commit()

    assert result.ok is True
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("metric", "baseline_data_version", "absorbing_extreme", "fast_extreme"),
    [
        ("high", _current_baseline_data_version("high"), 30.0, 31.0),
        ("low", _current_baseline_data_version("low"), 21.0, 20.0),
    ],
)
@pytest.mark.usefixtures("_historical_shanghai_component_surface")
def test_day0_owner_witness_keeps_newer_fast_residual_over_absorbing_frontier(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    metric: str,
    baseline_data_version: str,
    absorbing_extreme: float,
    fast_extreme: float,
) -> None:
    """A newer fast extreme keeps its residual likelihood and exact enqueue owner."""
    conn,basis = _historical_shanghai_component_request(tmp_path,monkeypatch,metric=metric)
    absorbing = replace(
        replace(basis,
            computed_at=_dt(18),
            expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
            day0_observed_extreme_c=absorbing_extreme,
            day0_observed_extreme_source="wu_icao_history",
            day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
            day0_observed_extreme_sample_count=12,
        ),
        temperature_metric=metric,
        baseline_data_version=baseline_data_version,
    )
    assert materialize_replacement_forecast_live(conn, absorbing).ok is True

    current = replace(
        absorbing,
        computed_at=_dt(18, 10),
        day0_observed_extreme_c=fast_extreme,
        day0_observed_extreme_source="wu_api+same_station_fast_tail",
        day0_observed_extreme_observation_time=_dt(18, 5).isoformat(),
        day0_observed_extreme_sample_count=13,
    )
    likelihood = _fixture_fast_residual_likelihood(extreme_c=absorbing_extreme, at=_dt(18, 10))
    monkeypatch.setattr(
        "src.data.day0_fast_obs.build_fast_station_residual_likelihood",
        lambda *args, **kwargs: likelihood,
    )
    _record_fixture_current_temperature(conn, at=_dt(18, 5), value_c=fast_extreme)
    current = _refresh_shanghai_owner_request(conn,monkeypatch,current,record_observed_prints=False)
    witness = _day0_owner_witness(current, seed_file=tmp_path / "fast-owner.json")
    _record_day0_owner(conn, current, witness)
    prepared = _prepare_for_final_write(
        conn, replace(current, day0_enqueue_owner_witness=witness)
    )

    conn.execute("BEGIN IMMEDIATE")
    result = materializer_mod.write_prepared_replacement_forecast_live(conn, prepared)
    conn.commit()

    assert result.ok is True
    provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (result.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    assert provenance["q_shape"] == "fused_day0_fast_residual_likelihood"
    assert (
        provenance["day0_provisional_observation"]["observed_extreme_c"]
        == fast_extreme
    )
    assert (
        provenance["day0_provisional_observation"]["source"]
        == "wu_api+same_station_fast_tail"
    )


@pytest.mark.parametrize(("missing_metric", "healthy_metric"), (("high", "low"), ("low", "high")))
@pytest.mark.parametrize("state", ("absent", "future_unpossessed"))
@pytest.mark.usefixtures("_hko_source_surface")
def test_noaa_missing_current_state_blocks_only_one_family_and_drains_on_next_cut(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    missing_metric: str,
    healthy_metric: str,
    state: str,
) -> None:
    """A missing/currently unpossessed print must not abort the next family."""
    from src.config import runtime_cities_by_name
    from src.data import day0_fast_obs as fast
    from tests.test_openmeteo_cell_selection_and_elevation_are_product_identity import _selected_test_cell
    conn, missing = _shanghai_noaa_future_request(tmp_path,monkeypatch,metric=missing_metric,without_current_state=True)
    city = runtime_cities_by_name()[missing.city]
    # Only the uncommitted current-print control was rolled back; prior-day
    # training pairs and all ordinary owned forecast entities remain intact.
    def append_current(observed):
        fetched = observed+timedelta(minutes=1)
        class CaptureClock(fast.datetime):
            @classmethod
            def now(cls,tz=None):
                return fetched.astimezone(tz) if tz else fetched.replace(tzinfo=None)
        monkeypatch.setattr(fast,"datetime",CaptureClock)
        reports = fast.parse_metar_api_payload([{"icaoId":"ZSPD","obsTime":observed.timestamp(),
            "receiptTime":fetched.isoformat(),"temp":30.,"metarType":"METAR",
            "rawOb":f"ZSPD {observed:%d%H%M}Z 30/20 T03000200"}])
        source = fast.fast_obs_source_for_city(city,missing.target_date)
        assert fast._append_metar_prints_to_ledger(conn,((city,source,str(missing.target_date)),),reports)
        conn.commit()
    if state=="future_unpossessed":
        append_current(missing.computed_at+timedelta(minutes=5))
    committed_prints = {row["id"]:tuple(row) for row in conn.execute("SELECT * FROM observation_prints")}
    blocked = materialize_replacement_forecast_live(conn, missing)
    assert blocked.status == "BLOCKED"
    assert blocked.reason_codes == (
        "DAY0_NOAA_PRELIMINARY_CARRIER_CURRENT_TEMPERATURE_STATE_MISSING",
    )
    prepared = materializer_mod.prepare_replacement_forecast_live(conn, missing)
    assert isinstance(prepared, materializer_mod.ReplacementForecastMaterializeResult)
    assert prepared.status == "BLOCKED"
    assert prepared.reason_codes == blocked.reason_codes
    assert materializer_mod.compute_replacement_posterior_readonly(conn, missing) is None

    # The sibling has an independently issued forecast. Do not fabricate two
    # different same-run hourly bodies (flat HIGH versus non-flat LOW) under
    # one physical product/capture identity merely to keep both centers 25C.
    healthy_cycle = missing.source_cycle_time+timedelta(hours=6)
    healthy = _install_hko_live_fusion(monkeypatch,conn=conn,request=replace(
        missing,
        temperature_metric=healthy_metric,
        baseline_data_version=_current_baseline_data_version(healthy_metric),
        source_cycle_time=healthy_cycle,
        openmeteo_anchor=replace(missing.openmeteo_anchor,source_cycle_time=healthy_cycle),
        openmeteo_source_available_at=healthy_cycle+timedelta(hours=3),
        baseline_source_available_at=healthy_cycle+timedelta(hours=2),
        day0_observed_extreme_c=31. if healthy_metric=="high" else 19.,
        day0_observed_extreme_source="noaa_wrh_zspd",
    ),snapshot_id=9001 if healthy_metric=="high" else 9002,
        selected_cells={model:_selected_test_cell(model,city.lat,city.lon)
            for model in ("icon_global","ukmo_global_deterministic_10km")})
    _append_shanghai_owner_prints(conn,healthy)
    good = materialize_replacement_forecast_live(conn, healthy)
    assert good.ok is True
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 1

    # Recover by a new actual parser/writer event and a later cut. Do not
    # UPDATE a future report's publication/possession back into the old cut.
    observed = missing.computed_at+timedelta(minutes=1)
    append_current(observed)
    current = _refresh_shanghai_owner_request(conn,monkeypatch,replace(missing,
        computed_at=observed+timedelta(minutes=2),day0_observed_extreme_observation_time=observed.isoformat()))
    recovered = materialize_replacement_forecast_live(conn, current)
    assert recovered.ok is True
    assert recovered.posterior_id is not None
    assert all(tuple(row)==committed_prints[row["id"]] for row in conn.execute("SELECT * FROM observation_prints")
               if row["id"] in committed_prints)


@pytest.mark.usefixtures("_hko_source_surface")
def test_noaa_missing_state_boundary_does_not_swallow_unexpected_calculation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, request = _shanghai_noaa_future_request(tmp_path,monkeypatch)

    def invalid_calculation(*_args, **_kwargs):
        raise ValueError("UNEXPECTED_CALCULATION_ERROR")

    monkeypatch.setattr(materializer_mod, "_compute_posterior_payload", invalid_calculation)
    with pytest.raises(ValueError, match="UNEXPECTED_CALCULATION_ERROR"):
        materializer_mod.prepare_replacement_forecast_live(conn, request)
    with pytest.raises(ValueError, match="UNEXPECTED_CALCULATION_ERROR"):
        materializer_mod.compute_replacement_posterior_readonly(conn, request)
    with pytest.raises(ValueError, match="UNEXPECTED_CALCULATION_ERROR"):
        materialize_replacement_forecast_live(conn, request)


@pytest.mark.parametrize("reason", (
    "DAY0_CONDITIONAL_HIGH_ENSEMBLE_UNAVAILABLE",
    "DAY0_CONDITIONAL_HIGH_ENSEMBLE_SUPERSEDED",
    "DAY0_CONDITIONAL_HIGH_OBSERVATION_ANCHOR_UNAVAILABLE",
    "DAY0_CONDITIONAL_HIGH_OBSERVATION_MISSING",
))
@pytest.mark.usefixtures("_hko_source_surface")
def test_conditional_high_missing_evidence_is_family_blocked_not_calculation_error(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch, reason: str,
) -> None:
    conn, high = _shanghai_noaa_future_request(tmp_path,monkeypatch)

    def missing_high(*_args, metric: str, **_kwargs):
        if metric == "high":
            raise ValueError(reason)
        raise AssertionError("LOW must not use the conditional HIGH path")

    monkeypatch.setattr(materializer_mod, "_day0_noaa_carrier_future_members", missing_high)
    prepared = materializer_mod.prepare_replacement_forecast_live(conn, high)
    assert isinstance(prepared, materializer_mod.ReplacementForecastMaterializeResult)
    assert prepared.status == "BLOCKED"
    assert prepared.reason_codes == (reason,)
    assert materializer_mod.compute_replacement_posterior_readonly(conn, high) is None
    written = materialize_replacement_forecast_live(conn, high)
    assert written.status == "BLOCKED"
    assert written.reason_codes == (reason,)
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0

    from src.config import runtime_cities_by_name
    from tests.test_openmeteo_cell_selection_and_elevation_are_product_identity import _selected_test_cell
    city = runtime_cities_by_name()[high.city]
    old_entities = {row["artifact_id"]:tuple(row) for row in conn.execute("SELECT * FROM raw_forecast_artifacts")}
    low = _install_hko_live_fusion(monkeypatch,conn=conn,request=replace(
        high,
        temperature_metric="low",
        baseline_data_version=_current_baseline_data_version("low"),
        day0_observed_extreme_c=19.,day0_observed_extreme_source="noaa_wrh_zspd",
    ),snapshot_id=9002,selected_cells={model:_selected_test_cell(model,city.lat,city.lon)
        for model in ("icon_global","ukmo_global_deterministic_10km")})
    assert all(tuple(row)==old_entities[row["artifact_id"]] for row in conn.execute("SELECT * FROM raw_forecast_artifacts")
               if row["artifact_id"] in old_entities)
    _append_shanghai_owner_prints(conn,low)
    assert materialize_replacement_forecast_live(conn, low).ok is True


@pytest.mark.parametrize("reason", (
    "DAY0_CONDITIONAL_HIGH_ENSEMBLE_CYCLE_MISMATCH",
    "DAY0_CONDITIONAL_HIGH_PROVIDER_REBUILD_MISMATCH",
    "DAY0_CONDITIONAL_HIGH_RUN_PROOF_INVALID",
    "UNEXPECTED_CALCULATION_ERROR",
))
@pytest.mark.usefixtures("_hko_source_surface")
def test_conditional_high_missing_evidence_boundary_does_not_swallow_mismatch(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch, reason: str,
) -> None:
    conn, request = _shanghai_noaa_future_request(tmp_path,monkeypatch)

    def invalid(*_args, **_kwargs):
        raise ValueError(reason)

    monkeypatch.setattr(materializer_mod, "_compute_posterior_payload", invalid)
    with pytest.raises(ValueError, match=reason):
        materializer_mod.prepare_replacement_forecast_live(conn, request)
    with pytest.raises(ValueError, match=reason):
        materializer_mod.compute_replacement_posterior_readonly(conn, request)
    with pytest.raises(ValueError, match=reason):
        materialize_replacement_forecast_live(conn, request)


@pytest.mark.parametrize(
    ("metric", "baseline_data_version", "absorbing_extreme", "fast_extreme", "bound"),
    [
        ("high", _current_baseline_data_version("high"), 30.0, 31.0, None),
        ("high", _current_baseline_data_version("high"), 30.0, 31.0, float("nan")),
        ("high", _current_baseline_data_version("high"), 30.0, 31.0, 29.0),
        ("high", _current_baseline_data_version("high"), 30.0, 29.0, 30.0),
        ("low", _current_baseline_data_version("low"), 21.0, 20.0, None),
        ("low", _current_baseline_data_version("low"), 21.0, 20.0, float("nan")),
        ("low", _current_baseline_data_version("low"), 21.0, 20.0, 22.0),
        ("low", _current_baseline_data_version("low"), 21.0, 22.0, 21.0),
    ],
)
@pytest.mark.usefixtures("_hko_source_surface")
def test_fast_residual_frontier_fails_closed_when_bound_cannot_cover_history(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    metric: str,
    baseline_data_version: str,
    absorbing_extreme: float,
    fast_extreme: float,
    bound: float | None,
) -> None:
    from src.data import day0_fast_obs as fast
    conn, positive = _shanghai_noaa_future_request(tmp_path,monkeypatch,metric=metric,
        absorbing_extreme=absorbing_extreme,current_temp_c=31. if metric=="high" else 20.)
    absorbing = _refresh_shanghai_owner_request(conn,monkeypatch,replace(positive,
        computed_at=positive.computed_at-timedelta(minutes=10),day0_observed_extreme_c=absorbing_extreme,
        day0_observed_extreme_source="noaa_wrh_zspd",
        day0_observed_extreme_observation_time=(positive.computed_at-timedelta(minutes=15)).isoformat()))
    assert absorbing.baseline_data_version == baseline_data_version
    assert materialize_replacement_forecast_live(conn, absorbing).ok is True
    provisional = _refresh_shanghai_owner_request(conn,monkeypatch,replace(
        positive,
        day0_observed_extreme_c=fast_extreme,
    ))
    valid = fast.build_fast_station_residual_likelihood(conn,city=provisional.city,
        target_date=str(provisional.target_date),metric=metric,observed_source="aviationweather_metar",
        observation_time=provisional.day0_observed_extreme_observation_time,decision_time=provisional.computed_at)
    assert valid is not None and valid.settlement_extreme_c==absorbing_extreme
    # Keep the normal likelihood and source evidence; inject only the original
    # malformed bound (or the original weakened candidate boundary).
    likelihood = None if bound is None else replace(valid,settlement_extreme_c=bound)
    monkeypatch.setattr(
        "src.data.day0_fast_obs.build_fast_station_residual_likelihood",
        lambda *args, **kwargs: likelihood,
    )

    reduced = materializer_mod._request_with_day0_physical_frontier(
        conn,
        provisional,
        metric=metric,
    )

    assert isinstance(reduced, ReplacementForecastMaterializeRequest)
    assert reduced.day0_observed_extreme_c == absorbing_extreme
    assert reduced.day0_observed_extreme_source == "noaa_wrh_zspd"
    assert reduced.day0_observed_extreme_observation_time == absorbing.day0_observed_extreme_observation_time


@pytest.mark.usefixtures("_hko_source_surface")
def test_stronger_absorbing_frontier_after_prepare_invalidates_fast_owner(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Final writer revalidation rejects a fast request superseded after prepare."""
    conn, current = _shanghai_noaa_future_request(tmp_path,monkeypatch,metric="low",
        absorbing_extreme=21.,current_temp_c=20.)
    prior = _refresh_shanghai_owner_request(conn,monkeypatch,replace(current,
        computed_at=current.computed_at-timedelta(minutes=10),day0_observed_extreme_c=21.,
        day0_observed_extreme_source="noaa_wrh_zspd",
        day0_observed_extreme_observation_time=(current.computed_at-timedelta(minutes=15)).isoformat()))
    assert materialize_replacement_forecast_live(conn, prior).ok is True
    current = _refresh_shanghai_owner_request(conn,monkeypatch,current)
    witness = _day0_owner_witness(current, seed_file=tmp_path / "fast-owner.json")
    _record_day0_owner(conn, current, witness)
    prepared = _prepare_for_final_write(
        conn, replace(current, day0_enqueue_owner_witness=witness)
    )

    stronger = _refresh_shanghai_owner_request(conn,monkeypatch,replace(
        prior,
        computed_at=current.computed_at-timedelta(minutes=2),
        day0_observed_extreme_c=19.0,
        day0_observed_extreme_observation_time=(current.computed_at-timedelta(minutes=3)).isoformat(),
    ))
    assert materialize_replacement_forecast_live(conn, stronger).ok is True
    conn.commit()

    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(
        materializer_mod.PreparedReplacementForecastSnapshotStale
    ):
        materializer_mod.write_prepared_replacement_forecast_live(conn, prepared)
    conn.rollback()

    refreshed = _prepare_for_final_write(conn, _refresh_shanghai_owner_request(conn,monkeypatch,prepared.request))
    conn.execute("BEGIN IMMEDIATE")
    result = materializer_mod.write_prepared_replacement_forecast_live(
        conn, refreshed
    )
    conn.commit()

    assert result.status == "BLOCKED"
    assert result.reason_codes == (STALE_DAY0_ENQUEUE_OWNER,)
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 2


@pytest.mark.usefixtures("_hko_source_surface")
def test_day0_owner_witness_blocks_swapped_owner_before_posterior_insert(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A swap after read preparation blocks A, while the current B witness can write."""
    conn, owner_a = _shanghai_current_owner_request(tmp_path,monkeypatch)
    witness_a = _day0_owner_witness(owner_a, seed_file=tmp_path / "owner-a.json")
    _record_day0_owner(conn, owner_a, witness_a)
    prepared_a = _prepare_for_final_write(
        conn, replace(owner_a, day0_enqueue_owner_witness=witness_a)
    )

    owner_b = replace(
        owner_a,
        computed_at=owner_a.computed_at+timedelta(minutes=1),
        day0_observed_extreme_c=26.25,
        day0_observed_extreme_source="noaa_wrh_zspd",
    )
    from src.config import runtime_cities_by_name
    from tests.test_openmeteo_cell_selection_and_elevation_are_product_identity import _selected_test_cell
    city = runtime_cities_by_name()[owner_b.city]
    old_entities = tuple(tuple(row) for row in conn.execute("SELECT * FROM raw_forecast_artifacts ORDER BY artifact_id"))
    old_raw = tuple(tuple(row) for row in conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id"))
    owner_b = _install_hko_live_fusion(monkeypatch,conn=conn,request=owner_b,
        selected_cells={model:_selected_test_cell(model,city.lat,city.lon)
                        for model in ("icon_global","ukmo_global_deterministic_10km")})
    assert tuple(tuple(row) for row in conn.execute("SELECT * FROM raw_forecast_artifacts ORDER BY artifact_id")) == old_entities
    assert tuple(tuple(row) for row in conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id")) == old_raw
    _append_shanghai_owner_prints(conn,owner_b)
    witness_b = _day0_owner_witness(owner_b, seed_file=tmp_path / "owner-b.json")
    assert cycle_advance._record_enqueue(
        conn,
        city=witness_b.city,
        target_date=witness_b.target_date,
        metric=witness_b.metric,
        consumed_cycle_iso=witness_b.target_cycle_time,
        target_cycle_iso=witness_b.target_cycle_time,
        held_position=True,
        seed_file=witness_b.seed_file,
        reason="DAY0_OBSERVATION_ADVANCED",
        replace_existing_seed_file=True,
        day0_observed_extreme_source=owner_b.day0_observed_extreme_source,
        day0_observed_extreme_observation_time=(
            owner_b.day0_observed_extreme_observation_time
        ),
        day0_observed_extreme_c=owner_b.day0_observed_extreme_c,
        day0_observed_extreme_unit=owner_b.day0_observed_extreme_unit,
    ) is True
    conn.commit()

    conn.execute("BEGIN IMMEDIATE")
    stale = materializer_mod.write_prepared_replacement_forecast_live(conn, prepared_a)
    conn.commit()
    assert stale.status == "BLOCKED"
    assert stale.reason_codes == (STALE_DAY0_ENQUEUE_OWNER,)
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0

    prepared_b = _prepare_for_final_write(
        conn, replace(owner_b, day0_enqueue_owner_witness=witness_b)
    )
    conn.execute("BEGIN IMMEDIATE")
    current = materializer_mod.write_prepared_replacement_forecast_live(conn, prepared_b)
    conn.commit()
    assert current.ok is True
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 1


@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_blocks_non_live_posterior_before_execution_authority_table(monkeypatch) -> None:
    conn = _conn()
    request = _install_hko_live_fusion(monkeypatch, conn=conn, request=_hko_request())
    producer_readiness = tuple(tuple(row) for row in conn.execute("SELECT * FROM readiness_state ORDER BY rowid"))
    assert producer_readiness and all(row["strategy_key"] == "producer_readiness" for row in conn.execute("SELECT * FROM readiness_state"))
    assert isinstance(materializer_mod.prepare_replacement_forecast_live(conn, request),
                      materializer_mod.PreparedReplacementForecastMaterialization)
    monkeypatch.setattr(materializer_mod, "_replacement_bayes_precision_fusion_override", lambda *a, **k: None)
    result = materialize_replacement_forecast_live(conn, request)

    assert result.ok is False
    # Catch-all reason stays first (byte-identical prefix for existing consumers);
    # a typed sub-reason is appended so the operator sees WHICH requirement failed
    # (2026-07-13/14 incident: the catch-all alone told 277 receipts nothing).
    assert result.reason_codes[0] == REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET
    assert len(result.reason_codes) > 1
    assert any(code.startswith("Q_MODE:") for code in result.reason_codes)
    assert result.posterior_id is None
    assert result.anchor_id is not None
    assert result.readiness_id is None
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0
    assert tuple(tuple(row) for row in conn.execute("SELECT * FROM readiness_state ORDER BY rowid")) == producer_readiness
    assert conn.execute("SELECT COUNT(*) FROM readiness_state WHERE strategy_key!='producer_readiness'").fetchone()[0] == 0


@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_writes_authorized_06z_cycle_as_live_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    request = _install_hko_live_fusion(monkeypatch, conn=conn,
        request=_hko_request(source_cycle_time=_hko_dt(6), computed_at=_hko_dt(11), expires_at=_hko_dt(12)))
    # The old 10Z component cut precedes the real 10:45 safe-fetch release.
    # Keep its refusal, then prove the same issued run's lawful later RESET.
    assert materializer_mod.read_current_evidence_snapshot_identity(conn,
        replace(request,computed_at=_hko_dt(10)),metric="high") is None
    actual = materializer_mod.read_current_evidence_snapshot_identity(conn,request,metric="high")
    assert actual is not None
    assert actual.snapshot_id == materializer_mod._replacement_bayes_precision_fusion_override().current_evidence_shape["snapshot_id"]
    result = materialize_replacement_forecast_live(conn, request)

    assert result.ok is True
    assert result.anchor_id is not None
    assert result.posterior_id is not None
    row = conn.execute("SELECT runtime_layer, provenance_json FROM forecast_posteriors").fetchone()
    provenance = json.loads(row["provenance_json"])
    assert row["runtime_layer"] == LIVE_RUNTIME_LAYER
    assert provenance["cycle_phase"] == "synoptic"


@pytest.mark.usefixtures("_hko_source_surface")
def test_non_day0_partial_cohort_proof_changes_equal_q_posterior_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    request = _hko_request(source_cycle_time=_hko_dt(6), computed_at=_hko_dt(11), expires_at=_hko_dt(12))
    request = _install_hko_live_fusion(monkeypatch, conn=conn, request=request)
    fusion = materializer_mod._replacement_bayes_precision_fusion_override()
    anchor_id = materializer_mod._insert_anchor(conn, request, metric="high")
    assert conn.execute("SELECT artifact_id FROM deterministic_forecast_anchors WHERE anchor_id=?",
                        (anchor_id,)).fetchone()[0] == request.anchor_artifact_id
    common = {
        "fallback_reason": "configured_current_provider_set_incomplete",
        "configured_coherent_sources": ["icon_global", "ukmo_global_deterministic_10km"],
    }

    def materialized(scheme: dict[str, object]) -> tuple[int, str, dict[str, float]]:
        monkeypatch.setattr(
            materializer_mod, "_replacement_bayes_precision_fusion_override",
            lambda *_args, **_kwargs: replace(fusion, source_clock_one_scheme=scheme),
        )
        computed = materializer_mod._compute_posterior_payload(
            conn, request, metric="high", anchor_id=anchor_id,
        )
        assert computed.live_eligible
        posterior_id = materializer_mod._write_posterior_row(
            conn, request, metric="high", anchor_id=anchor_id, result=computed,
        )
        assert posterior_id is not None
        return posterior_id, computed.posterior_config_hash, computed.q

    old = materialized(dict(common))
    first = materialized({**common,
        "configured_cohort_decision_time": _hko_dt(10).isoformat(),
        "configured_cohort_value_serving": {"icon_global": {"raw_model_forecast_id": 10}},
    })
    revised_row = materialized({**common,
        "configured_cohort_decision_time": _hko_dt(10).isoformat(),
        "configured_cohort_value_serving": {"icon_global": {"raw_model_forecast_id": 11}},
    })
    revised_cutoff = materialized({**common,
        "configured_cohort_decision_time": _hko_dt(10, 1).isoformat(),
        "configured_cohort_value_serving": {"icon_global": {"raw_model_forecast_id": 10}},
    })
    assert old[2] == first[2] == revised_row[2] == revised_cutoff[2]
    assert len({old[0], first[0], revised_row[0], revised_cutoff[0]}) == 4
    assert len({old[1], first[1], revised_row[1], revised_cutoff[1]}) == 4


@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_surfaces_bounds_missing_sub_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-07-13/14 incident fix: a fused-q bounds-build failure must surface the
    Q_MODE:FUSED_NORMAL_BOUNDS_MISSING sub-reason, not just the catch-all code — so
    a BLOCKED receipt tells the operator WHICH requirement failed without opening a
    subprocess log."""
    conn = _conn()
    request = _install_hko_live_fusion(monkeypatch, conn=conn,
        request=_hko_request(source_cycle_time=_hko_dt(6), computed_at=_hko_dt(11), expires_at=_hko_dt(12)))
    assert isinstance(materializer_mod.prepare_replacement_forecast_live(conn, request),
                      materializer_mod.PreparedReplacementForecastMaterialization)
    monkeypatch.setattr(
        materializer_mod,
        "_build_fused_q_bounds",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bootstrap exploded")),
    )

    result = materialize_replacement_forecast_live(conn, request)

    assert result.ok is False
    assert result.reason_codes[0] == REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET
    assert "Q_MODE:FUSED_NORMAL_BOUNDS_MISSING" in result.reason_codes
    assert result.posterior_id is None
    # Shadow accrual still happens: a row is NOT written for the live-blocked mode,
    # but the anchor row is (unchanged prior contract).
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0


def test_runtime_layer_requires_bootstrap_bounds() -> None:
    live_layer = _replacement_is_live_layer(
        replacement_q_mode=REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL,
        q_lcb_map={"cool": 0.1, "warm": 0.6, "hot": 0.05},
        q_ucb_map={"cool": 0.3, "warm": 0.9, "hot": 0.2},
        q_lcb_basis=_QLCB_BASIS,
    )

    assert live_layer is True


def test_runtime_layer_rejects_wilson_or_missing_bounds() -> None:
    assert _replacement_is_live_layer(
        replacement_q_mode=REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL,
        q_lcb_map={"cool": 0.1},
        q_ucb_map={"cool": 0.3},
        q_lcb_basis="legacy_wilson_member_votes",
    ) is False
    assert _replacement_is_live_layer(
        replacement_q_mode=REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL,
        q_lcb_map=None,
        q_ucb_map={"cool": 0.3},
        q_lcb_basis=_QLCB_BASIS,
    ) is False
    assert _replacement_is_live_layer(
        replacement_q_mode=REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL,
        q_lcb_map={"cool": 0.1},
        q_ucb_map=None,
        q_lcb_basis=_QLCB_BASIS,
    ) is False


def test_forecast_posteriors_runtime_layer_migration_refuses_legacy_labels_and_preserves_explicit_live_rows() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_authority_status TEXT NOT NULL DEFAULT 'DIAGNOSTIC_ONLY'
                CHECK (trade_authority_status IN ('DIAGNOSTIC_ONLY', 'LIVE_AUTHORITY')),
            runtime_layer TEXT,
            q_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO forecast_posteriors (trade_authority_status, q_json) VALUES (?, ?)",
        ("DIAGNOSTIC_ONLY", '{"bad":1}'),
    )
    conn.execute(
        "INSERT INTO forecast_posteriors (trade_authority_status, q_json) VALUES (?, ?)",
        ("LIVE_AUTHORITY", '{"good":1}'),
    )
    conn.execute(
        "INSERT INTO forecast_posteriors (runtime_layer, q_json) VALUES (?, ?)",
        (LIVE_RUNTIME_LAYER, '{"good":1}'),
    )

    _ensure_forecast_posteriors_runtime_layer(conn)

    rows = conn.execute("SELECT posterior_id, runtime_layer, q_json FROM forecast_posteriors").fetchall()
    assert [dict(row) for row in rows] == [
        {"posterior_id": 3, "runtime_layer": LIVE_RUNTIME_LAYER, "q_json": '{"good":1}'}
    ]
    assert "trade_authority_status" in {
        row["name"] for row in conn.execute("PRAGMA table_info(forecast_posteriors)")
    }
    conn.execute(
        "INSERT INTO forecast_posteriors (runtime_layer, q_json) VALUES (?, ?)",
        (LIVE_RUNTIME_LAYER, "{}"),
    )
    statuses = [
        row["runtime_layer"]
        for row in conn.execute("SELECT runtime_layer FROM forecast_posteriors ORDER BY posterior_id")
    ]
    assert statuses == [LIVE_RUNTIME_LAYER, LIVE_RUNTIME_LAYER]
    _ensure_forecast_posteriors_runtime_layer(conn)
    assert [
        row["runtime_layer"]
        for row in conn.execute("SELECT runtime_layer FROM forecast_posteriors ORDER BY posterior_id")
    ] == [LIVE_RUNTIME_LAYER, LIVE_RUNTIME_LAYER]
    assert _replacement_is_live_layer(
        replacement_q_mode=REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL,
        q_lcb_map={"cool": 0.1},
        q_ucb_map=None,
        q_lcb_basis=_QLCB_BASIS,
    ) is False


def test_forecast_posteriors_runtime_layer_migration_does_not_write_when_already_live() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY AUTOINCREMENT,
            runtime_layer TEXT NOT NULL DEFAULT 'live'
                CHECK (runtime_layer IN ('live')),
            q_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO forecast_posteriors (runtime_layer, q_json) VALUES (?, ?)",
        (LIVE_RUNTIME_LAYER, "{}"),
    )
    conn.execute(
        """
        CREATE INDEX idx_forecast_posteriors_runtime_layer_target
            ON forecast_posteriors(runtime_layer, posterior_id)
        """
    )

    traced: list[str] = []
    conn.set_trace_callback(lambda sql: traced.append(sql))
    _ensure_forecast_posteriors_runtime_layer(conn)
    _ensure_forecast_posteriors_runtime_layer_compatibility(conn)
    conn.set_trace_callback(None)

    forecast_posterior_mutations = [
        sql.strip().upper()
        for sql in traced
        if "FORECAST_POSTERIORS" in sql.upper()
        and (
            sql.lstrip().upper().startswith("DELETE")
            or sql.lstrip().upper().startswith("UPDATE")
        )
    ]
    assert forecast_posterior_mutations == []
    compatibility_reads = [
        sql
        for sql in traced
        if sql.lstrip().upper().startswith("SELECT 1")
        and "FROM FORECAST_POSTERIORS" in sql.upper()
    ]
    assert compatibility_reads
    assert all(
        "INDEXED BY idx_forecast_posteriors_runtime_layer_target" in sql
        for sql in compatibility_reads
    )
    assert all("!=" not in sql for sql in compatibility_reads)


def test_forecast_posteriors_runtime_layer_migration_repairs_invalid_observation_view() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE observation_instants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            running_max REAL
        );
        CREATE VIEW observation_hourly_extrema AS
            SELECT o.*, o.running_max AS hour_bucket_max, o.running_min AS hour_bucket_min
            FROM observation_instants o;
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_authority_status TEXT NOT NULL DEFAULT 'DIAGNOSTIC_ONLY'
                CHECK (trade_authority_status IN ('DIAGNOSTIC_ONLY', 'LIVE_AUTHORITY')),
            runtime_layer TEXT,
            q_json TEXT NOT NULL
        );
        INSERT INTO forecast_posteriors (trade_authority_status, runtime_layer, q_json)
        VALUES ('LIVE_AUTHORITY', 'live', '{}');
        """
    )

    _ensure_forecast_posteriors_runtime_layer(conn)

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(observation_instants)")}
    posterior_cols = {row["name"] for row in conn.execute("PRAGMA table_info(forecast_posteriors)")}
    assert "running_min" in cols
    assert "trade_authority_status" in posterior_cols
    conn.execute("SELECT * FROM observation_hourly_extrema").fetchall()
    conn.execute(
        "INSERT INTO forecast_posteriors (trade_authority_status, runtime_layer, q_json) VALUES (?, ?, ?)",
        ("LIVE_AUTHORITY", LIVE_RUNTIME_LAYER, "{}"),
    )


def test_legacy_anchor_schema_migration_does_not_rewrite_legacy_status_columns() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE raw_forecast_artifacts (
            artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_authority_status TEXT NOT NULL DEFAULT 'BLOCKED'
                CHECK (trade_authority_status IN ('BLOCKED'))
        );
        INSERT INTO raw_forecast_artifacts (artifact_id, trade_authority_status)
        VALUES (1, 'BLOCKED');

        CREATE TABLE deterministic_forecast_anchors (
            anchor_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            product_id TEXT NOT NULL,
            data_version TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low')),
            anchor_value_c REAL NOT NULL,
            source_cycle_time TEXT NOT NULL,
            source_available_at TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            artifact_id INTEGER REFERENCES raw_forecast_artifacts(artifact_id),
            model TEXT NOT NULL,
            native_grid TEXT,
            delivery_grid_resolution TEXT,
            interpolation_method TEXT,
            contributing_times_json TEXT NOT NULL DEFAULT '[]',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            trade_authority_status TEXT NOT NULL DEFAULT 'BLOCKED'
                CHECK (trade_authority_status IN ('BLOCKED')),
            training_allowed INTEGER NOT NULL DEFAULT 0
                CHECK (training_allowed = 0),
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            anchor_identity_hash TEXT,
            UNIQUE(source_id, product_id, data_version, city, target_date, temperature_metric, source_cycle_time)
        );
        INSERT INTO deterministic_forecast_anchors (
            source_id, product_id, data_version, city, target_date, temperature_metric,
            anchor_value_c, source_cycle_time, source_available_at, captured_at,
            artifact_id, model, trade_authority_status, anchor_identity_hash
        ) VALUES (
            'openmeteo_ecmwf_ifs_9km',
            'openmeteo_ecmwf_ifs9_deterministic_anchor_v1',
            'openmeteo_ecmwf_ifs9_anchor_localday_high',
            'Chengdu',
            '2026-06-17',
            'high',
            25.65,
            '2026-06-17T00:00:00+00:00',
            '2026-06-17T11:21:16+00:00',
            '2026-06-17T12:08:19+00:00',
            1,
            'ecmwf_ifs9',
            'BLOCKED',
            'anchor-hash'
        );

        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY AUTOINCREMENT,
            openmeteo_anchor_id INTEGER REFERENCES deterministic_forecast_anchors(anchor_id),
            trade_authority_status TEXT NOT NULL DEFAULT 'DIAGNOSTIC_ONLY'
                CHECK (trade_authority_status IN ('DIAGNOSTIC_ONLY', 'LIVE_AUTHORITY'))
        );
        """
    )

    _ensure_replacement_identity_columns(conn)

    raw_status = conn.execute(
        "SELECT trade_authority_status FROM raw_forecast_artifacts WHERE artifact_id = 1"
    ).fetchone()["trade_authority_status"]
    anchor_status = conn.execute(
        "SELECT trade_authority_status FROM deterministic_forecast_anchors WHERE anchor_id = 1"
    ).fetchone()["trade_authority_status"]
    assert "trade_authority_status" in {
        row["name"] for row in conn.execute("PRAGMA table_info(forecast_posteriors)")
    }
    conn.execute(
        "INSERT INTO forecast_posteriors (openmeteo_anchor_id, runtime_layer) VALUES (?, ?)",
        (1, LIVE_RUNTIME_LAYER),
    )

    assert raw_status == "BLOCKED"
    assert anchor_status == "BLOCKED"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_keeps_readiness_separate_by_baseline_source_run(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    first_request = _hko_request(baseline_source_run_id="ecmwf_open_data:mx2t6_high:2026-09-30T00Z",
        baseline_source_available_at=_hko_dt(2), computed_at=_hko_dt(8), expires_at=_hko_dt(10))
    second_request = _hko_request(baseline_source_run_id="ecmwf_open_data:mx2t6_high:2026-09-30T00Z:revised",
        baseline_source_available_at=_hko_dt(2,15), computed_at=_hko_dt(8,15), expires_at=_hko_dt(10,15))
    first_request = _install_hko_live_fusion(monkeypatch, conn=conn, request=first_request)
    first = materialize_replacement_forecast_live(conn, first_request)
    second_request = _install_hko_live_fusion(monkeypatch, conn=conn, request=second_request)
    second = materialize_replacement_forecast_live(conn, second_request)

    assert first.ok is True
    assert second.ok is True
    rows = conn.execute(
        """
        SELECT track, dependency_json
        FROM readiness_state
        WHERE city = 'Hong Kong'
          AND target_local_date = '2026-10-01'
          AND temperature_metric = 'high'
          AND strategy_key = ?
        ORDER BY track
        """,
        (STRATEGY_KEY,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["track"] == "soft_anchor_posterior"
    assert "2026-09-30T00Z:revised" in rows[0]["dependency_json"]


@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_writes_certified_bootstrap_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    request = _hko_request()
    request = _install_hko_live_fusion(monkeypatch, conn=conn, request=request)

    result = materialize_replacement_forecast_live(conn, request)

    assert result.ok is True
    posterior_row = conn.execute("SELECT q_json, q_lcb_json, q_ucb_json, provenance_json, runtime_layer FROM forecast_posteriors WHERE posterior_id = ?", (result.posterior_id,)).fetchone()
    q = json.loads(posterior_row["q_json"])
    q_lcb = json.loads(posterior_row["q_lcb_json"])
    q_ucb = json.loads(posterior_row["q_ucb_json"])
    provenance = json.loads(posterior_row["provenance_json"])
    assert posterior_row["runtime_layer"] == LIVE_RUNTIME_LAYER
    assert set(q_lcb) == set(q) == set(q_ucb)
    for key, point in q.items():
        assert q_lcb[key] <= point <= q_ucb[key]
    assert not any(str(key).startswith(("buy_no:", "no:")) for key in q_lcb)
    assert provenance["q_lcb_json_role"] == "fused_center_bootstrap_lcb"


@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_does_not_publish_stale_ensemble_as_live_probability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    request = _hko_request()
    request = _install_hko_live_fusion(monkeypatch, conn=conn, request=request)
    producer_readiness = tuple(tuple(row) for row in conn.execute("SELECT * FROM readiness_state ORDER BY rowid"))
    assert producer_readiness and all(row["strategy_key"] == "producer_readiness"
        for row in conn.execute("SELECT * FROM readiness_state"))
    assert isinstance(materializer_mod.prepare_replacement_forecast_live(conn, request),
                      materializer_mod.PreparedReplacementForecastMaterialization)
    request = _install_hko_live_fusion(monkeypatch, conn=conn, request=request, shape_lag_hours=6.0)

    result = materialize_replacement_forecast_live(conn, request)

    assert result.ok is False
    assert "CAPTURE:CURRENT_EVIDENCE_NOT_LIVE" in result.reason_codes
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0
    assert tuple(tuple(row) for row in conn.execute("SELECT * FROM readiness_state ORDER BY rowid")) == producer_readiness
    assert conn.execute("SELECT COUNT(*) FROM readiness_state WHERE strategy_key!='producer_readiness'").fetchone()[0] == 0


@pytest.mark.usefixtures("_hko_source_surface")
def test_prepared_materialization_keeps_compute_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    request = _hko_request()
    request = _install_hko_live_fusion(monkeypatch, conn=conn, request=request)

    conn.execute("BEGIN")
    prepared = materializer_mod.prepare_replacement_forecast_live(conn, request)
    assert isinstance(
        prepared,
        materializer_mod.PreparedReplacementForecastMaterialization,
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM deterministic_forecast_anchors"
    ).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0
    conn.rollback()

    conn.execute("BEGIN IMMEDIATE")
    result = materializer_mod.write_prepared_replacement_forecast_live(conn, prepared)
    conn.commit()

    assert result.ok is True
    assert conn.execute(
        "SELECT COUNT(*) FROM deterministic_forecast_anchors"
    ).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 1


@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_lifts_computed_at_to_source_run_possession(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    _ensure_source_run_table(conn)
    request = _hko_request(computed_at=_hko_dt(8), expires_at=_hko_dt(10))
    late_possession = _hko_dt(8, 5)
    # The producer sees the effective possession cut after the request is
    # normalized. Its frozen geometry audit must carry that same cut.
    owned = _install_hko_live_fusion(
        monkeypatch, conn=conn, request=replace(request, computed_at=late_possession))
    request = replace(owned, computed_at=request.computed_at)
    for source_run_id, source_id, track in (
        ("b0-run", "ecmwf_open_data", "mx2t3_high"),
        ("om9-run", "openmeteo_ecmwf_ifs9", "localday_high"),
    ):
        write_source_run(
            conn,
            source_run_id=source_run_id,
            source_id=source_id,
            track=track,
            release_calendar_key=f"{source_id}:{track}",
            source_cycle_time=_hko_dt(0),
            source_available_at=_hko_dt(2),
            fetch_finished_at=late_possession,
            captured_at=late_possession,
            imported_at=late_possession,
            status="SUCCESS",
            completeness_status="COMPLETE",
            city_id="Hong Kong",
            city_timezone="Asia/Hong_Kong",
            target_local_date=date(2026, 10, 1),
            temperature_metric="high",
            data_version="forecast_v2",
        )

    result = materialize_replacement_forecast_live(
        conn,
        request,
    )

    assert result.ok is True
    row = conn.execute(
        "SELECT source_available_at, computed_at FROM forecast_posteriors WHERE posterior_id = ?",
        (result.posterior_id,),
    ).fetchone()
    assert row["source_available_at"] == late_possession.isoformat()
    assert row["computed_at"] == late_possession.isoformat()


def test_materializer_blocks_day0_without_observed_extreme() -> None:
    conn = _conn()

    result = materialize_replacement_forecast_live(
        conn,
        _request(
            computed_at=_dt(18),
            expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        ),
    )

    assert result.ok is False
    assert result.reason_codes == ("REPLACEMENT_MATERIALIZATION_DAY0_OBSERVED_EXTREME_REQUIRED",)
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0


@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_allows_typed_day0_zero_observation_full_day_posterior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.contracts.replacement_pipeline_files import (
        DAY0_OBSERVATION_STATE_ZERO_TARGET_DATE_OBSERVATIONS,
    )

    conn = _conn()
    request = _hko_request(
        source_cycle_time=_hko_dt(6), computed_at=_hko_dt(18),
        expires_at=_hko_dt(2)+timedelta(days=1),
        day0_observation_state=DAY0_OBSERVATION_STATE_ZERO_TARGET_DATE_OBSERVATIONS,
    )
    request = _install_hko_live_fusion(monkeypatch, conn=conn, request=request)
    result = materialize_replacement_forecast_live(conn, request)

    assert result.ok is True
    row = conn.execute(
        """
        SELECT posterior_config_hash, provenance_json
        FROM forecast_posteriors
        WHERE posterior_id = ?
        """,
        (result.posterior_id,),
    ).fetchone()
    provenance = json.loads(row["provenance_json"])
    assert provenance["day0_observation_state"] == (
        DAY0_OBSERVATION_STATE_ZERO_TARGET_DATE_OBSERVATIONS
    )
    assert "day0_conditioning" not in provenance
    assert "day0_provisional_observation" not in provenance
    assert row["posterior_config_hash"]

    # Restore the approved same-fixture single-fault twin: real current
    # evidence passes first; only the conflicting declaration is changed.
    conflict = materialize_replacement_forecast_live(
        conn, replace(request, day0_observed_extreme_c=26.0),
    )
    assert conflict.reason_codes == ("REPLACEMENT_MATERIALIZATION_DAY0_ZERO_OBSERVATION_STATE_CONFLICT",)
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 1


def test_materializer_rejects_conflicting_day0_zero_and_observed_extreme() -> None:
    from src.contracts.replacement_pipeline_files import (
        DAY0_OBSERVATION_STATE_ZERO_TARGET_DATE_OBSERVATIONS,
    )

    conn = _conn()
    result = materialize_replacement_forecast_live(
        conn,
        _request(
            computed_at=_dt(18),
            expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
            day0_observed_extreme_c=26.0,
            day0_observation_state=(
                DAY0_OBSERVATION_STATE_ZERO_TARGET_DATE_OBSERVATIONS
            ),
        ),
    )

    assert result.ok is False
    assert (
        "REPLACEMENT_MATERIALIZATION_DAY0_ZERO_OBSERVATION_STATE_CONFLICT"
        in result.reason_codes
    )


@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_day0_observed_extreme_conditions_q_and_bounds(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn, request = _shanghai_current_owner_request(tmp_path,monkeypatch,observed_extreme=26.)

    result = materialize_replacement_forecast_live(
        conn,
        request,
    )

    assert result.ok is True
    row = conn.execute(
        "SELECT q_json, q_lcb_json, provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
        (result.posterior_id,),
    ).fetchone()
    q = json.loads(row["q_json"])
    q_lcb = json.loads(row["q_lcb_json"])
    provenance = json.loads(row["provenance_json"])
    assert q["cool"] == pytest.approx(0.0)
    assert q_lcb["cool"] == pytest.approx(0.0)
    assert q["warm"] > q["hot"]
    assert provenance["q_shape"] == "fused_day0_conditioned_normal"
    assert provenance["day0_conditioning"]["observed_extreme_c"] == 26.0


@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_day0_historical_diurnal_mixture_cannot_enter_q_draws_or_bounds(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retired fitted-mixture contract: preserve the live point, draws and band.

    Normal source proof uses actual possessed Shanghai ground with controlled
    forecast/ENS inputs. The historical fit is hostile offline evidence, never
    an alternative live probability regime or a claim of native GRIB capture.
    """

    from src.calibration import day0_diurnal_residual as diurnal

    from src.events.day0_authority import DAY0_LEGACY_DIURNAL_FIELDS, DAY0_PROBABILITY_MIXTURE_POLICY

    conn, request = _shanghai_current_owner_request(tmp_path,monkeypatch,observed_extreme=26.)
    control = materialize_replacement_forecast_live(conn,request)
    assert control.ok is True
    columns = "q_json, q_lcb_json, q_ucb_json, provenance_json"
    control_row = conn.execute(f"SELECT {columns} FROM forecast_posteriors WHERE posterior_id=?",
                               (control.posterior_id,)).fetchone()
    calls = []
    mixture = diurnal.Day0DiurnalMixture(
        weight=0.5, pi=(0.0, 0.25, 0.75), dead=(True, False, False), k=1,
        anchor=26.0, fit_date="2026-10-01", artifact="TEST_ONLY_HISTORICAL_FITTED_CITY_MIXTURE",
    )
    def serve(**kwargs):
        calls.append(kwargs)
        return mixture, mixture.provenance()
    monkeypatch.setattr(diurnal,"day0_diurnal_mixture",serve)
    result = materialize_replacement_forecast_live(conn,request)
    assert result.ok is True
    row = conn.execute(f"SELECT {columns} FROM forecast_posteriors WHERE posterior_id=?",
                       (result.posterior_id,)).fetchone()
    assert tuple(row) == tuple(control_row)
    assert calls == []
    q, q_lcb, q_ucb = (json.loads(row[key]) for key in ("q_json", "q_lcb_json", "q_ucb_json"))
    provenance = json.loads(row["provenance_json"])
    assert q["cool"] == 0.0
    assert q["warm"] > q["hot"]
    assert q["hot"] != pytest.approx(0.5*q["hot"]+0.5*0.75)
    samples = provenance["q_bootstrap_samples_by_bin"]
    for index in range(len(samples["hot"])):
        row_sum = sum(samples[bin_id][index] for bin_id in ("cool", "warm", "hot"))
        assert row_sum == pytest.approx(1.0, abs=1e-9)
    for bin_id in q:
        assert q_lcb[bin_id] <= q[bin_id] + 1e-12 <= q_ucb[bin_id] + 2e-12
    assert provenance["day0_probability_mixture_policy"] == DAY0_PROBABILITY_MIXTURE_POLICY
    assert not set(DAY0_LEGACY_DIURNAL_FIELDS).intersection(provenance)
    assert provenance["day0_conditioning"]["observed_extreme_c"] == 26.0
    assert provenance["day0_conditioning"]["source"] == "noaa_wrh_zspd"


@pytest.mark.usefixtures("_historical_shanghai_component_surface")
def test_materializer_write_replaces_retracted_same_source_high(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A newer snapshot may retract its own HIGH without erasing independent evidence."""
    conn,basis = _historical_shanghai_component_request(tmp_path,monkeypatch)
    awc = replace(basis,
        computed_at=_dt(18),
        expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        day0_observed_extreme_c=31.0,
        day0_observed_extreme_source="noaa_wrh_zspd",
        day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
        day0_observed_extreme_sample_count=12,
    )
    old_wu = replace(
        awc,
        computed_at=_dt(18, 10),
        day0_observed_extreme_c=30.0,
        day0_observed_extreme_source="wu_icao_history",
        day0_observed_extreme_observation_time=_dt(17, 45).isoformat(),
        day0_observed_extreme_sample_count=10,
    )
    prepared = materializer_mod.prepare_replacement_forecast_live(conn, old_wu)
    assert isinstance(prepared, materializer_mod.PreparedReplacementForecastMaterialization)

    # The old WU worker computed from an earlier read snapshot. A delayed WRH
    # writer commits the stronger, still-causal HIGH31 before that worker owns
    # the writer lock. The writer rejects the stale payload; recomputation must
    # happen after its caller releases the lock.
    assert materialize_replacement_forecast_live(conn, awc).ok is True
    with pytest.raises(
        materializer_mod.PreparedReplacementForecastSnapshotStale
    ):
        materializer_mod.write_prepared_replacement_forecast_live(conn, prepared)
    refreshed = materializer_mod.prepare_replacement_forecast_live(
        conn, _refresh_shanghai_owner_request(conn,monkeypatch,prepared.request,record_observed_prints=False)
    )
    assert isinstance(
        refreshed, materializer_mod.PreparedReplacementForecastMaterialization
    )
    result = materializer_mod.write_prepared_replacement_forecast_live(
        conn, refreshed
    )

    assert result.ok is True
    provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (result.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    assert provenance["day0_conditioning"]["observed_extreme_c"] == 31.0
    assert provenance["day0_conditioning"]["source"] == "noaa_wrh_zspd"
    assert provenance["day0_conditioning"]["observation_time"] == _dt(17, 55).isoformat()

    plateau = materialize_replacement_forecast_live(
        conn,
        _refresh_shanghai_owner_request(conn,monkeypatch,replace(
            awc,
            computed_at=_dt(18, 15),
            day0_observed_extreme_observation_time=_dt(18, 5).isoformat(),
            day0_observed_extreme_sample_count=13,
        ),record_observed_prints=False),
    )
    assert plateau.ok is True
    plateau_provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (plateau.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    assert plateau_provenance["day0_conditioning"]["observed_extreme_c"] == 31.0
    assert plateau_provenance["day0_conditioning"]["observation_time"] == _dt(18, 5).isoformat()

    same_source_regression = materialize_replacement_forecast_live(
        conn,
        _refresh_shanghai_owner_request(conn,monkeypatch,replace(
            awc,
            computed_at=_dt(18, 20),
            day0_observed_extreme_c=30.0,
            day0_observed_extreme_observation_time=_dt(18, 10).isoformat(),
            day0_observed_extreme_sample_count=14,
        ),record_observed_prints=False),
    )
    assert same_source_regression.ok is True
    regression_provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (same_source_regression.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    assert regression_provenance["day0_conditioning"]["observed_extreme_c"] == 30.0
    assert regression_provenance["day0_conditioning"]["observation_time"] == _dt(18, 10).isoformat()


@pytest.mark.usefixtures("_historical_shanghai_component_surface")
def test_wu_newer_snapshot_retracts_stale_source_frontier(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shenzhen antibody: WU 37 -> 36 must not leave the posterior pinned at 37."""
    conn,basis = _historical_shanghai_component_request(tmp_path,monkeypatch)
    first = replace(basis,
        computed_at=_dt(18),
        expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        day0_observed_extreme_c=37.0,
        day0_observed_extreme_source="wu_icao_history",
        day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
        day0_observed_extreme_sample_count=22,
    )
    assert materialize_replacement_forecast_live(conn, first).ok is True

    revised = materializer_mod._request_with_day0_physical_frontier(
        conn,
        replace(
            first,
            computed_at=_dt(18, 20),
            day0_observed_extreme_c=36.0,
            day0_observed_extreme_observation_time=_dt(18, 10).isoformat(),
            day0_observed_extreme_sample_count=24,
        ),
        metric="high",
    )

    assert isinstance(revised, ReplacementForecastMaterializeRequest)
    assert revised.day0_observed_extreme_c == 36.0
    assert revised.day0_observed_extreme_observation_time == _dt(18, 10).isoformat()


@pytest.mark.usefixtures("_historical_shanghai_component_surface")
def test_materializer_readonly_replaces_retracted_same_source_low(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A newer snapshot may retract its own LOW without reopening other sources."""
    conn,basis = _historical_shanghai_component_request(tmp_path,monkeypatch,metric="low")
    awc = replace(
        replace(basis,
            computed_at=_dt(18),
            expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
            day0_observed_extreme_c=19.0,
            day0_observed_extreme_source="noaa_wrh_zspd",
            day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
            day0_observed_extreme_sample_count=12,
        ),
        temperature_metric="low",
        baseline_data_version=_current_baseline_data_version("low"),
    )
    same_source_regression = replace(
        awc,
        computed_at=_dt(18, 10),
        day0_observed_extreme_c=20.0,
        day0_observed_extreme_observation_time=_dt(18, 5).isoformat(),
        day0_observed_extreme_sample_count=13,
    )
    prepared = materializer_mod.prepare_replacement_forecast_live(
        conn,
        same_source_regression,
    )
    assert isinstance(
        prepared,
        materializer_mod.PreparedReplacementForecastMaterialization,
    )
    assert materialize_replacement_forecast_live(conn, awc).ok is True

    with pytest.raises(
        materializer_mod.PreparedReplacementForecastSnapshotStale
    ):
        materializer_mod.write_prepared_replacement_forecast_live(conn, prepared)
    refreshed = materializer_mod.prepare_replacement_forecast_live(
        conn, _refresh_shanghai_owner_request(conn,monkeypatch,prepared.request,record_observed_prints=False)
    )
    assert isinstance(
        refreshed, materializer_mod.PreparedReplacementForecastMaterialization
    )
    write_result = materializer_mod.write_prepared_replacement_forecast_live(
        conn, refreshed
    )
    assert write_result.ok is True
    write_provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (write_result.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    assert write_provenance["day0_conditioning"]["observed_extreme_c"] == 20.0
    assert write_provenance["day0_conditioning"]["observation_time"] == _dt(
        18, 5
    ).isoformat()

    old_wu = replace(
        awc,
        computed_at=_dt(18, 15),
        day0_observed_extreme_c=20.0,
        day0_observed_extreme_source="wu_icao_history",
        day0_observed_extreme_observation_time=_dt(17, 45).isoformat(),
        day0_observed_extreme_sample_count=10,
    )
    posterior = materializer_mod.compute_replacement_posterior_readonly(conn,
        _refresh_shanghai_owner_request(conn,monkeypatch,old_wu,record_observed_prints=False))

    assert posterior is not None
    assert posterior.provenance_payload is not None
    assert posterior.provenance_payload["day0_conditioning"]["observed_extreme_c"] == 19.0
    assert posterior.provenance_payload["day0_conditioning"]["source"] == "noaa_wrh_zspd"
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 2


@pytest.mark.usefixtures("_historical_shanghai_component_surface")
def test_materializer_equal_frontier_uses_current_request_identity(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retired carrier cannot ratchet its source/clock into every later posterior."""

    conn,basis = _historical_shanghai_component_request(tmp_path,monkeypatch)
    likelihood = _fixture_fast_residual_likelihood(extreme_c=31.0, at=_dt(18))
    monkeypatch.setattr("src.data.day0_fast_obs.build_fast_station_residual_likelihood",
                        lambda *_args, **_kwargs: likelihood)
    _record_fixture_current_temperature(conn, at=_dt(17, 55), value_c=31.0)
    prior = replace(basis,
        computed_at=_dt(18),
        expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        day0_observed_extreme_c=31.0,
        day0_observed_extreme_source="wu_api+same_station_fast_tail",
        day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
        day0_observed_extreme_sample_count=12,
    )
    _install_day0_current_inputs(conn, monkeypatch, prior)
    likelihood = _test_current_residual(prior, 30.0)
    monkeypatch.setattr("src.data.day0_fast_obs.build_fast_station_residual_likelihood",lambda *_a,**_kw:likelihood)
    assert materialize_replacement_forecast_live(conn, prior).ok is True

    current = replace(
        prior,
        computed_at=_dt(18, 10),
        day0_observed_extreme_source="noaa_wrh_zspd",
        day0_observed_extreme_observation_time=_dt(17, 50).isoformat(),
        day0_observed_extreme_sample_count=10,
    )
    current = _refresh_shanghai_owner_request(conn,monkeypatch,current,record_observed_prints=False)
    result = materialize_replacement_forecast_live(conn, current)

    assert result.ok is True
    provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (result.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    assert provenance["day0_conditioning"]["observed_extreme_c"] == 31.0
    assert provenance["day0_conditioning"]["source"] == "noaa_wrh_zspd"
    assert provenance["day0_conditioning"]["observation_time"] == _dt(
        17, 50
    ).isoformat()


@pytest.mark.parametrize(
    ("metric", "baseline_data_version", "extreme"),
    [
        ("high", _current_baseline_data_version("high"), 31.0),
        ("low", _current_baseline_data_version("low"), 19.0),
    ],
)
@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_blocks_future_day0_observation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    metric: str,
    baseline_data_version: str,
    extreme: float,
) -> None:
    # This clock contract is channel-independent; use the currently qualified
    # same-city WRH product, not an unproven fast-likelihood prerequisite.
    conn, positive = _shanghai_current_owner_request(tmp_path,monkeypatch,
        metric=metric,observed_extreme=extreme)
    assert positive.baseline_data_version == baseline_data_version
    conn.execute("SAVEPOINT normal_control")
    control = materialize_replacement_forecast_live(conn,positive)
    assert control.ok is True
    conn.execute("ROLLBACK TO normal_control")
    conn.execute("RELEASE normal_control")
    request = replace(positive,day0_observed_extreme_observation_time=(positive.computed_at+timedelta(minutes=30)).isoformat())

    result = materialize_replacement_forecast_live(conn, request)

    assert result.ok is False
    assert result.reason_codes == (
        "REPLACEMENT_MATERIALIZATION_DAY0_OBSERVATION_AFTER_COMPUTED_AT",
    )
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0


def test_materializer_blocks_when_day0_frontier_ledger_read_fails() -> None:
    class BrokenFrontierLedger:
        def execute(self, sql, params):
            del sql, params
            raise sqlite3.DatabaseError("frontier ledger unavailable")

    request = _request(
        computed_at=_dt(18),
        expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        day0_observed_extreme_c=31.0,
        day0_observed_extreme_source="aviationweather_metar",
        day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
    )

    result = materializer_mod._request_with_day0_physical_frontier(
        BrokenFrontierLedger(),
        request,
        metric="high",
    )

    assert isinstance(result, materializer_mod.ReplacementForecastMaterializeResult)
    assert result.reason_codes == (
        "REPLACEMENT_MATERIALIZATION_DAY0_FRONTIER_LEDGER_READ_FAILED",
    )


@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_blocks_malformed_day0_frontier_ledger(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, first = _shanghai_current_owner_request(tmp_path,monkeypatch,observed_extreme=31.)
    written = materialize_replacement_forecast_live(conn, first)
    assert written.ok is True
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        ("[]", written.posterior_id),  # SQL enforces JSON syntax; reject an invalid JSON shape.
    )

    result = materialize_replacement_forecast_live(
        conn,
        _refresh_shanghai_owner_request(conn,monkeypatch,replace(
            first,
            computed_at=first.computed_at+timedelta(minutes=10),
            day0_observed_extreme_c=30.0,
            day0_observed_extreme_observation_time=(first.computed_at+timedelta(minutes=5)).isoformat(),
        )),
    )

    assert result.ok is False
    assert result.reason_codes == (
        "REPLACEMENT_MATERIALIZATION_DAY0_FRONTIER_LEDGER_INVALID",
    )
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("metric", "baseline_data_version", "legacy_extreme", "current_extreme"),
    [
        ("high", _current_baseline_data_version("high"), 31.0, 32.0),
        ("low", _current_baseline_data_version("low"), 19.0, 18.0),
    ],
)
@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_ignores_typed_legacy_provisional_frontier_ledger(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    metric: str,
    baseline_data_version: str,
    legacy_extreme: float,
    current_extreme: float,
) -> None:
    conn, legacy = _shanghai_current_owner_request(tmp_path,monkeypatch,
        metric=metric,observed_extreme=legacy_extreme)
    assert legacy.baseline_data_version == baseline_data_version
    written = materialize_replacement_forecast_live(conn, legacy)
    assert written.ok is True
    provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (written.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    provenance["day0_conditioning"]["evidence_finality"] = (
        "PROVISIONAL_CURRENT_SNAPSHOT"
    )
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(provenance), written.posterior_id),
    )

    current = _refresh_shanghai_owner_request(conn,monkeypatch,replace(
        legacy,
        computed_at=legacy.computed_at+timedelta(minutes=10),
        day0_observed_extreme_c=current_extreme,
        day0_observed_extreme_source="noaa_wrh_zspd",
        day0_observed_extreme_observation_time=(legacy.computed_at+timedelta(minutes=5)).isoformat(),
    ))
    result = materialize_replacement_forecast_live(conn, current)

    assert result.ok is True
    current_provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (result.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    assert (
        current_provenance["day0_conditioning"]["observed_extreme_c"]
        == current_extreme
    )
    assert current_provenance["day0_conditioning"]["source"] == "noaa_wrh_zspd"


@pytest.mark.parametrize(
    ("metric", "baseline_data_version", "malformation"),
    [
        ("high", _current_baseline_data_version("high"), "missing"),
        ("high", _current_baseline_data_version("high"), "nonfinite"),
        ("high", _current_baseline_data_version("high"), "future"),
        ("low", _current_baseline_data_version("low"), "missing"),
        ("low", _current_baseline_data_version("low"), "nonfinite"),
        ("low", _current_baseline_data_version("low"), "future"),
    ],
)
@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_blocks_malformed_typed_provisional_frontier_ledger(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    metric: str,
    baseline_data_version: str,
    malformation: str,
) -> None:
    conn, first = _shanghai_current_owner_request(tmp_path,monkeypatch,metric=metric,
        observed_extreme=31. if metric=="high" else 19.)
    assert first.baseline_data_version == baseline_data_version
    written = materialize_replacement_forecast_live(conn, first)
    assert written.ok is True
    provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (written.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    conditioning = provenance["day0_conditioning"]
    conditioning["evidence_finality"] = "PROVISIONAL_CURRENT_SNAPSHOT"
    if malformation == "missing":
        del conditioning["observed_extreme_c"]
    elif malformation == "nonfinite":
        conditioning["observed_extreme_c"] = "nan"
    else:
        conditioning["observation_time"] = (first.computed_at+timedelta(minutes=5)).isoformat()
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(provenance), written.posterior_id),
    )

    result = materialize_replacement_forecast_live(
        conn,
        _refresh_shanghai_owner_request(conn,monkeypatch,replace(
            first,
            computed_at=first.computed_at+timedelta(minutes=10),
            day0_observed_extreme_observation_time=(first.computed_at+timedelta(minutes=5)).isoformat(),
        )),
    )

    assert result.ok is False
    assert result.reason_codes == (
        "REPLACEMENT_MATERIALIZATION_DAY0_FRONTIER_LEDGER_INVALID",
    )


@pytest.mark.parametrize(
    ("metric", "baseline_data_version"),
    [
        ("high", _current_baseline_data_version("high")),
        ("low", _current_baseline_data_version("low")),
    ],
)
@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_blocks_unknown_frontier_finality(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    metric: str,
    baseline_data_version: str,
) -> None:
    conn, first = _shanghai_current_owner_request(tmp_path,monkeypatch,metric=metric,
        observed_extreme=31. if metric=="high" else 19.)
    assert first.baseline_data_version == baseline_data_version
    written = materialize_replacement_forecast_live(conn, first)
    assert written.ok is True
    provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (written.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    provenance["day0_conditioning"]["source"] = "unclassified_sensor"
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(provenance), written.posterior_id),
    )

    result = materialize_replacement_forecast_live(
        conn,
        _refresh_shanghai_owner_request(conn,monkeypatch,replace(
            first,
            computed_at=first.computed_at+timedelta(minutes=10),
            day0_observed_extreme_c=32.0,
            day0_observed_extreme_observation_time=(first.computed_at+timedelta(minutes=5)).isoformat(),
        )),
    )

    assert result.ok is False
    assert result.reason_codes == (
        "REPLACEMENT_MATERIALIZATION_DAY0_FRONTIER_LEDGER_INVALID",
    )


@pytest.mark.parametrize(
    ("metric", "baseline_data_version", "declared_finality"),
    [
        (
            "high",
            _current_baseline_data_version("high"),
            "TYPO_OR_UNKNOWN_FINALITY",
        ),
        ("high", _current_baseline_data_version("high"), "UNKNOWN"),
        ("high", _current_baseline_data_version("high"), None),
        ("low", _current_baseline_data_version("low"), ""),
        ("low", _current_baseline_data_version("low"), "UNKNOWN"),
        ("low", _current_baseline_data_version("low"), 1),
    ],
)
@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_blocks_unknown_declared_frontier_finality(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    metric: str,
    baseline_data_version: str,
    declared_finality: object,
) -> None:
    conn, first = _shanghai_current_owner_request(tmp_path,monkeypatch,metric=metric,
        observed_extreme=31. if metric=="high" else 19.)
    assert first.baseline_data_version == baseline_data_version
    written = materialize_replacement_forecast_live(conn, first)
    assert written.ok is True
    provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (written.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    provenance["day0_conditioning"]["evidence_finality"] = declared_finality
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(provenance), written.posterior_id),
    )

    result = materialize_replacement_forecast_live(
        conn,
        _refresh_shanghai_owner_request(conn,monkeypatch,replace(
            first,
            computed_at=first.computed_at+timedelta(minutes=10),
            day0_observed_extreme_observation_time=(first.computed_at+timedelta(minutes=5)).isoformat(),
        )),
    )

    assert result.ok is False
    assert result.reason_codes == (
        "REPLACEMENT_MATERIALIZATION_DAY0_FRONTIER_LEDGER_INVALID",
    )


@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_blocks_ledger_observation_after_its_own_compute_time(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, first = _shanghai_current_owner_request(tmp_path,monkeypatch,observed_extreme=31.)
    written = materialize_replacement_forecast_live(conn, first)
    assert written.ok is True
    provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (written.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    provenance["day0_conditioning"]["observation_time"] = (first.computed_at+timedelta(minutes=5)).isoformat()
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(provenance), written.posterior_id),
    )

    result = materialize_replacement_forecast_live(
        conn,
        _refresh_shanghai_owner_request(conn,monkeypatch,replace(
            first,
            computed_at=first.computed_at+timedelta(minutes=10),
            day0_observed_extreme_c=30.0,
            day0_observed_extreme_observation_time=(first.computed_at+timedelta(minutes=5)).isoformat(),
        )),
    )

    assert result.ok is False
    assert result.reason_codes == (
        "REPLACEMENT_MATERIALIZATION_DAY0_FRONTIER_LEDGER_INVALID",
    )


@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_ignores_malformed_pre_day0_frontier_ledger(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cycle = datetime(2026,10,1,tzinfo=UTC)
    conn, request = _shanghai_current_owner_request(tmp_path,monkeypatch,
        computed_at=cycle+timedelta(hours=8))
    pre_day0 = materialize_replacement_forecast_live(conn, request)
    assert pre_day0.ok is True
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        ("[]", pre_day0.posterior_id),  # Valid JSON with invalid shape; pre-Day0 is out of scope.
    )

    day0 = materialize_replacement_forecast_live(
        conn,
        _refresh_shanghai_owner_request(conn,monkeypatch,replace(request,
            computed_at=cycle+timedelta(hours=18),
            expires_at=cycle+timedelta(hours=26),
            day0_observed_extreme_c=31.0,
            day0_observed_extreme_source="noaa_wrh_zspd",
            day0_observed_extreme_observation_time=(cycle+timedelta(hours=17,minutes=55)).isoformat(),
            day0_observed_extreme_sample_count=12,day0_observed_extreme_unit="C",
        )),
    )

    assert day0.ok is True
    provenance = json.loads(
        conn.execute(
            "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
            (day0.posterior_id,),
        ).fetchone()["provenance_json"]
    )
    assert provenance["day0_conditioning"]["observed_extreme_c"] == 31.0


@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_hko_provisional_observation_does_not_truncate_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.data.day0_observation_reader as day0_reader

    conn = _conn()
    conn.execute("""CREATE TABLE observation_prints (
        id INTEGER PRIMARY KEY, city TEXT, station_id TEXT, source_channel TEXT,
        publish_ts_utc TEXT, value_native REAL, unit TEXT,
        fetched_at_utc TEXT, raw_report TEXT
    )""")
    conn.execute(
        """INSERT INTO observation_prints VALUES
           (1, 'Hong Kong', 'HKO', 'hko_rhrread_spot', ?, 25.7, 'C', ?, ?)""",
        (_hko_dt(17, 55).isoformat(), _hko_dt(17, 55).isoformat(), json.dumps({
            "recordTime": _hko_dt(17,55).isoformat(),
            "data": [{"place": "Hong Kong Observatory", "unit": "C", "value": 25.7}],
        })),
    )
    likelihood_identity = {
        "semantics": "hko_provisional_monotonic_survival_beta_jeffreys_v1",
        "lookback_start": "2026-09-24",
        "lookback_end": "2026-10-01",
        "transition_count": 100,
        "retraction_count": 1,
        "median_update_seconds": 600.0,
        "projected_remaining_updates": 36,
    }
    likelihood = {
        **likelihood_identity,
        "boundary_survival_probability": 0.91,
        "identity_hash": materializer_mod._json_hash(likelihood_identity),
    }
    monkeypatch.setattr(
        day0_reader,
        "hko_provisional_revision_likelihood",
        lambda *_args, **_kwargs: likelihood,
    )
    monkeypatch.setattr(
        materializer_mod,
        "_day0_noaa_carrier_future_members",
        lambda *_args, **_kwargs: (
            (25.2, 25.5, 28.3),
            0.5,
            _hko_dt(18).isoformat(),
            (),
            None,
        ),
    )
    request = replace(
        _hko_request(
            computed_at=_hko_dt(18),
            expires_at=_hko_dt(2)+timedelta(days=1),
            day0_observed_extreme_c=25.7,
            day0_observed_extreme_source="hko_hourly_accumulator",
            day0_observed_extreme_observation_time=_hko_dt(17, 55).isoformat(),
            day0_observed_extreme_sample_count=12,
        ),
        temperature_metric="low",
        baseline_data_version=_current_baseline_data_version("low"),
        bins=(
            _TemperatureBin(
                "below24", upper_c=23.0, center_c=22.0,
                rounding_rule="oracle_truncate",
            ),
            _TemperatureBin(
                "target24", lower_c=24.0, upper_c=24.0, center_c=24.0,
                rounding_rule="oracle_truncate",
            ),
            _TemperatureBin(
                "25plus", lower_c=25.0, center_c=26.0,
                rounding_rule="oracle_truncate",
            ),
        ),
    )
    request = _install_hko_live_fusion(monkeypatch, conn=conn, request=request)
    result = materialize_replacement_forecast_live(
        conn,
        request,
    )

    assert result.ok is True
    row = conn.execute(
        "SELECT q_json, q_lcb_json, provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
        (result.posterior_id,),
    ).fetchone()
    q = json.loads(row["q_json"])
    q_lcb = json.loads(row["q_lcb_json"])
    provenance = json.loads(row["provenance_json"])
    # HKO's oracle-truncate contract gives the 24C bin the physical preimage
    # [24, 25), not WMO's [23.5, 24.5). The noisy 25.2C remaining member must
    # therefore retain material 24C mass without treating the provisional
    # 25.7C boundary as deterministic settlement truth.
    assert 0.1 < q["target24"] < 0.25
    assert q["25plus"] > 0.7
    assert q_lcb["target24"] >= 0.0
    assert provenance["day0_provisional_observation"]["support_truncation"] is False
    assert provenance["q_shape"] == "day0_remaining_shared_carrier_v2"
    assert provenance["day0_remaining_carrier_operator"] == (
        "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2"
    )
    assert provenance["day0_remaining_carrier_content_identity"]
    assert provenance["day0_preliminary_report_survival_likelihood"] == likelihood
    assert provenance["day0_remaining_carrier_sample_count"] == 500
    assert provenance["day0_remaining_carrier_q"] == pytest.approx(
        [q[item.bin_id] for item in request.bins]
    )
    assert "day0_conditioning" not in provenance
    assert provenance["day0_provisional_observation"] == {
        "active": True,
        "metric": "low",
        "observed_extreme_c": 25.7,
        "source": "hko_hourly_accumulator",
        "observation_time": _hko_dt(17, 55).isoformat(),
        "sample_count": 12,
        "unit": "C",
        "support_truncation": False,
    }

    revised_request = replace(request, computed_at=_hko_dt(18,10), day0_observed_extreme_c=25.6,
                              day0_observed_extreme_observation_time=_hko_dt(18,5).isoformat())
    revised_request = _install_hko_live_fusion(monkeypatch, conn=conn, request=revised_request)
    revised = materialize_replacement_forecast_live(conn, revised_request)
    assert revised.ok is True
    assert revised.posterior_id != result.posterior_id
    hashes = conn.execute(
        "SELECT posterior_config_hash FROM forecast_posteriors WHERE posterior_id IN (?, ?)",
        (result.posterior_id, revised.posterior_id),
    ).fetchall()
    assert len({row["posterior_config_hash"] for row in hashes}) == 2


def test_hko_spot_request_rebinds_to_current_official_extrema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.data.replacement_forecast_current_target_plan as target_plan

    conn = _conn()
    request = _request(
        computed_at=_dt(18),
        day0_observed_extreme_c=26.0,
        day0_observed_extreme_source="hko_rhrread_spot",
        day0_observed_extreme_observation_time=_dt(17, 2).isoformat(),
        day0_observed_extreme_sample_count=1,
    )
    monkeypatch.setattr(
        target_plan,
        "_latest_authorized_day0_fact",
        lambda *_args, **_kwargs: {
            "observation_source": "hko_hourly_accumulator",
            "observation_time": _dt(17, 50).isoformat(),
            "observed_extreme_native": 25.7,
            "sample_count": 12,
            "unit": "C",
        },
    )

    rebound = materializer_mod._request_with_day0_physical_frontier(
        conn,
        request,
        metric="low",
    )

    assert isinstance(rebound, ReplacementForecastMaterializeRequest)
    assert rebound.day0_observed_extreme_source == "hko_hourly_accumulator"
    assert rebound.day0_observed_extreme_c == pytest.approx(25.7)
    assert rebound.day0_observed_extreme_observation_time == _dt(17, 50).isoformat()
    assert rebound.day0_observed_extreme_sample_count == 12


def test_wu_composite_missing_fusion_retains_typed_capture_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(
        computed_at=datetime(2026, 6, 7, 18, tzinfo=UTC),
        day0_observed_extreme_c=26.0,
        day0_observed_extreme_source="wu_api+same_station_fast_tail",
        day0_observed_extreme_observation_time=datetime(2026, 6, 7, 17, 55, tzinfo=UTC).isoformat(),
        day0_observed_extreme_sample_count=1,
    )
    monkeypatch.setattr(
        materializer_mod, "_replacement_bayes_precision_fusion_override",
        lambda *_args, **_kwargs: None,
    )
    result = materializer_mod._compute_posterior_payload(
        _conn(), request, metric="high", anchor_id=1,
    )
    assert result.live_eligible is False
    assert result.replacement_q_mode == "BAYES_PRECISION_FUSION_CAPTURE_MISSING"


def test_fusion_decline_names_its_branch_in_the_block_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seoul 2026-10-02: the override returned None quietly; the receipt said only
    CAPTURE:STALE_HISTORY_ONLY. The declining branch now rides the receipt."""

    def _declined(*_args, **_kwargs):
        raise materializer_mod.BayesPrecisionFusionDeclined(
            "CURRENT_SHAPE_PROVIDER_COHORT_BELOW_PAIR"
        )

    monkeypatch.setattr(
        materializer_mod, "_replacement_bayes_precision_fusion_override", _declined,
    )
    result = materializer_mod._compute_posterior_payload(
        _conn(), _request(), metric="high", anchor_id=1,
    )
    assert result.live_eligible is False
    assert result.capture_status == "STALE_HISTORY_ONLY"
    assert result.fusion_decline_reason == "CURRENT_SHAPE_PROVIDER_COHORT_BELOW_PAIR"
    assert "FUSION_DECLINED:CURRENT_SHAPE_PROVIDER_COHORT_BELOW_PAIR" in (
        materializer_mod._posterior_block_sub_reason_codes(result)
    )


@pytest.mark.usefixtures("_hko_source_surface")
def test_unreadable_source_clock_scheme_fails_the_family_by_name(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """A scheme artifact that cannot be read is a named family failure everywhere.

    The materializer must not crash, must not silently serve a scheme-less source
    set, and must name the reason in the posterior receipt; the upgrade trigger
    must see nothing newly capturable for the same family.
    """
    import logging

    from src.data import replacement_fusion_upgrade_trigger as trigger

    # The scheme component keeps controlled ENS/math inputs but uses normal
    # same-city ground, body and HTTP-receipt writers. This is not a native
    # public-snapshot qualification test.
    real_override = materializer_mod._replacement_bayes_precision_fusion_override
    conn, request = _shanghai_current_owner_request(tmp_path, monkeypatch)
    monkeypatch.setattr(materializer_mod, "_replacement_bayes_precision_fusion_override", real_override)
    capture = SimpleNamespace(
        has_extras=True, anchor_z=27.0, anchor_tau0=1.0,
        likelihood=tuple(SimpleNamespace(
            model=model, z=25.0, train_residuals=(), n_train=0, residuals_by_date={},
        ) for model in ("icon_global", "ukmo_global_deterministic_10km")),
        disagree_var=0.0, anchor_raw_m2_native=None, anchor_raw_n_train=0,
        dropped_models=(),
        selection=SimpleNamespace(excluded_regionals=(), dropped_aliases=()),
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_capture.capture_bayes_precision_instruments",
        lambda **_kwargs: capture,
    )
    monkeypatch.setattr(
        "src.forecast.bayes_precision_fusion.fuse_bayes_precision_posterior",
        lambda **_kwargs: SimpleNamespace(sd=0.5, method="TEST_FUSION",
            used_models=tuple(x.model for x in capture.likelihood), regional_models=()),
    )
    monkeypatch.setattr(materializer_mod, "_read_current_evidence_shape", _fixture_current_shape)
    healthy = materializer_mod._compute_posterior_payload(
        conn, request, metric="high", anchor_id=request.anchor_artifact_id,
    )
    assert healthy.live_eligible, healthy.capture_status
    immutable = tuple(tuple(row) for row in conn.execute("SELECT * FROM raw_forecast_artifacts ORDER BY artifact_id"))

    def broken(*_args, **_kwargs):
        raise ValueError("ACTIVE.json sha256 mismatch")

    with monkeypatch.context() as fault:
        fault.setattr("src.strategy.live_inference.source_clock_city_weights.scheme_for_city", broken)
        with caplog.at_level(logging.WARNING, logger="zeus.replacement_bayes_precision_fusion"):
            result = materializer_mod._compute_posterior_payload(
                conn, request, metric="high", anchor_id=request.anchor_artifact_id,
            )
        assert result.live_eligible is False
        assert result.capture_status == "SOURCE_CLOCK_SCHEME_UNAVAILABLE"
        assert "CAPTURE:SOURCE_CLOCK_SCHEME_UNAVAILABLE" in (
            materializer_mod._posterior_block_sub_reason_codes(result)
        )
        assert any(
            "SOURCE_CLOCK_SCHEME_UNAVAILABLE" in record.getMessage()
            for record in caplog.records
        )
        assert trigger._capturable_inputs_for_scope(
            conn, city=request.city, target_date=str(request.target_date),
            metric="high", source_cycle_iso=request.source_cycle_time.isoformat(),
        ) == {}
    recovered = materializer_mod._compute_posterior_payload(
        conn, request, metric="high", anchor_id=request.anchor_artifact_id,
    )
    assert recovered.live_eligible
    assert recovered.q == healthy.q
    assert tuple(tuple(row) for row in conn.execute("SELECT * FROM raw_forecast_artifacts ORDER BY artifact_id")) == immutable
    conn.close()


def test_legacy_wu_fast_posterior_without_current_carrier_cannot_replay() -> None:
    import src.engine.event_reactor_adapter as era

    bundle = SimpleNamespace(city="Shanghai", target_date="2026-06-07", provenance_json={
        "q_shape": "fused_day0_fast_residual_likelihood",
        "day0_provisional_observation": {
            "active": True, "source": "wu_api+same_station_fast_tail",
            "metric": "low", "unit": "C",
        },
    })
    # The residual is settlement-minus-METAR at the observed extreme and stays
    # real after local midnight, so a closed day still requires its carrier.
    for decision_time in (datetime(2026, 6, 7, 6, tzinfo=UTC), datetime(2026, 6, 8, 18, tzinfo=UTC)):
        with pytest.raises(ValueError, match="GLOBAL_DAY0_WU_CURRENT_CARRIER_MISSING"):
            era._day0_replacement_conditioning(
                bundle, provisional=True, metric="low", unit="C",
                decision_time=decision_time, entry_authority=False,
            )
    request = _request(
        computed_at=datetime(2026, 6, 8, 18, tzinfo=UTC),
        day0_observed_extreme_c=26.0,
        day0_observed_extreme_source="wu_api+same_station_fast_tail",
        day0_observed_extreme_observation_time="2026-06-07T17:55:00+00:00",
    )
    assert materializer_mod._target_local_day_is_open(request) is False


def test_day0_current_path_revision_separates_old_q_cohort() -> None:
    """Mechanism stamps preserve old attribution, never relabel it as current."""
    from src.events.day0_authority import (
        DAY0_PROBABILITY_SEMANTICS_REVISION,
        DAY0_PROBABILITY_SEMANTICS_REVISION_RESOLVER,
        DAY0_PROBABILITY_SEMANTICS_REVISION_SURVIVAL,
        bind_day0_probability_semantics,
        day0_probability_semantics_revision,
    )

    stamped = bind_day0_probability_semantics("current-path-cert")
    assert day0_probability_semantics_revision(stamped) == DAY0_PROBABILITY_SEMANTICS_REVISION
    assert stamped == f"day0-semrev:{DAY0_PROBABILITY_SEMANTICS_REVISION}:current-path-cert"
    assert DAY0_PROBABILITY_SEMANTICS_REVISION_SURVIVAL != DAY0_PROBABILITY_SEMANTICS_REVISION_RESOLVER
    assert DAY0_PROBABILITY_SEMANTICS_REVISION in {
        DAY0_PROBABILITY_SEMANTICS_REVISION_SURVIVAL,
        DAY0_PROBABILITY_SEMANTICS_REVISION_RESOLVER,
    }
    assert bind_day0_probability_semantics(stamped) == stamped
    for old_revision in (
        "day0_settlement_channel_revision_model_v29_smooth_center_bias_observation_clock_v1",
        "day0_resolver_terminal_composition_v28_smooth_center_bias_observation_clock_v1",
        "day0_settlement_channel_revision_model_v28_hko_observation_clock_v1",
        "day0_resolver_terminal_composition_v27_hko_observation_clock_v1",
        "day0_settlement_channel_revision_model_v28_smooth_center_bias_v1",
        "day0_resolver_terminal_composition_v27_smooth_center_bias_v1",
        "day0_settlement_channel_revision_model_v26_land_grid_v3",
        "day0_resolver_terminal_composition_v25_land_grid_v3",
        "day0_settlement_channel_revision_model_v27_diurnal_mixture_v1",
        "day0_resolver_terminal_composition_v26_diurnal_mixture_v1",
    ):
        old_stamp = f"day0-semrev:{old_revision}:current-path-cert"
        assert old_stamp != stamped
        assert day0_probability_semantics_revision(old_stamp) == old_revision
        # An already-stamped historical mechanism stays historical. A new
        # certificate requires actual recomputation, not another bind call.
        assert bind_day0_probability_semantics(old_stamp) == old_stamp


@pytest.mark.parametrize("source", ("aviationweather_metar", "wu_api+same_station_fast_tail"))
@pytest.mark.usefixtures("_hko_source_surface")
def test_noaa_preliminary_fahrenheit_carrier_materializes_native_v2_q(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
    source: str,
) -> None:
    """KORD/F math component: normal physical writers, controlled conditional ENS.

    Retained HOMR capture precedes this forecast condition. Whole OM bytes,
    source body/anchor and canonical namespace are real private writer inputs;
    observation/remaining-member readers are controlled, not native GRIB/JIT.
    """

    from zoneinfo import ZoneInfo

    from src.config import runtime_cities_by_name
    from src.contracts.settlement_semantics import SettlementSemantics
    from src.data.day0_hourly_vectors import (
        Day0CurrentTemperatureState,
        Day0HourlyVector,
        day0_conditional_high_shape,
        day0_source_clock_ensemble_member_models,
    )
    from src.signal.ensemble_signal import sigma_instrument_for_city

    conn = _conn(archive_ground=False)
    from src.data.station_ground_evidence import archive_station_ground_evidence, forecast_db_from_connection
    assert archive_station_ground_evidence(forecast_db_from_connection(conn), ["Chicago"])["status"] == "GROUND_SOURCE_ARCHIVED"
    city = runtime_cities_by_name()["Chicago"]
    semantics = SettlementSemantics.for_city(city)
    assert semantics.measurement_unit == "F"
    assert semantics.rounding_rule == "wmo_half_up"

    target = date(2026, 10, 1)
    computed_at = datetime(2026, 10, 1, 18, tzinfo=UTC)
    native_members_f = (84.0, 88.0)

    def celsius(value_f: float) -> float:
        return (value_f - 32.0) * 5.0 / 9.0

    native_boundary_f = 86.0
    current_state = Day0CurrentTemperatureState(
        value_native=native_boundary_f,
        observed_at=computed_at,
        source="aviationweather_metar",
    )
    native_bins = (
        ("under_85", None, 84.0),
        ("85", 85.0, 85.0),
        ("86", 86.0, 86.0),
        ("87", 87.0, 87.0),
        ("88_plus", 88.0, None),
    )
    member_values_c = tuple(celsius(value) for value in native_members_f)
    local_tz = ZoneInfo("America/Chicago")
    local_hours = tuple(
        datetime(2026, 10, 1, hour, tzinfo=local_tz) for hour in range(24)
    )
    physical_request = _la_current_physical_request(conn, metric="high", city_name="Chicago",
        target=target, cycle=datetime(2026,10,1,0,tzinfo=UTC), decision=computed_at,
        hourly_values=[22. if hour == 0 else 31. if hour == 12 else 25. for hour in range(24)])
    anchor = physical_request.openmeteo_anchor
    raw_bytes = physical_request.openmeteo_raw_payload_bytes
    guard = physical_request.openmeteo_precision_guard
    assert guard.passable_for_live_materialization
    assert guard.metadata.station_id == "KORD" and guard.metadata.station_elevation_m == 204.8
    assert anchor.high_c == 31. and anchor.low_c == 22. and anchor.sample_count == 24
    assert anchor.contributing_valid_times_utc == tuple(item.astimezone(UTC) for item in local_hours)
    assert anchor.source_cycle_time <= local_hours[0].astimezone(UTC)
    request = ReplacementForecastMaterializeRequest(
        city="Chicago",
        city_id="Chicago",
        city_timezone="America/Chicago",
        target_date=target,
        temperature_metric="high",
        baseline_source_run_id="b0-chicago",
        baseline_data_version=_current_baseline_data_version("high"),
        baseline_source_available_at=datetime(2026, 10, 1, 12, tzinfo=UTC),
        openmeteo_anchor=anchor,
        openmeteo_source_run_id="om-chicago",
        openmeteo_source_available_at=datetime(2026, 10, 1, 12, 10, tzinfo=UTC),
        bins=tuple(
            _TemperatureBin(
                bin_id,
                lower_c=None if lower_f is None else celsius(lower_f),
                upper_c=None if upper_f is None else celsius(upper_f),
                display_unit="F",
                settlement_unit="F",
            )
            for bin_id, lower_f, upper_f in native_bins
        ),
        source_cycle_time=datetime(2026, 10, 1, 0, tzinfo=UTC),
        computed_at=computed_at,
        expires_at=datetime(2026, 10, 1, 20, tzinfo=UTC),
        openmeteo_precision_guard=guard,
        openmeteo_raw_payload_bytes=raw_bytes,
        day0_observed_extreme_c=celsius(native_boundary_f),
        day0_observed_extreme_source=source,
        day0_observed_extreme_observation_time=computed_at.isoformat(),
        day0_observed_extreme_sample_count=1,
        day0_observed_extreme_unit=(
            "F" if source == "wu_api+same_station_fast_tail" else "C"
        ),
    )
    from tests.test_openmeteo_cell_selection_and_elevation_are_product_identity import _selected_test_cell
    selected_cells = {model:_selected_test_cell(model,city.lat,city.lon)
                      for model in ("icon_global","ukmo_global_deterministic_10km")}
    request = _install_hko_live_fusion(monkeypatch,conn=conn,
        request=replace(request,source_cycle_time=physical_request.source_cycle_time,
            anchor_artifact_id=physical_request.anchor_artifact_id),
        selected_cells=selected_cells)
    remaining_hours = range(1, 24)
    times = tuple(f"{target.isoformat()}T{hour:02d}:00" for hour in remaining_hours)
    run = datetime(2026, 10, 1, 6, tzinfo=UTC)
    available = datetime(2026, 10, 1, 7, tzinfo=UTC)
    captured = datetime(2026, 10, 1, 17, 30, tzinfo=UTC)

    def vector_meta(model: str, *, ensemble: bool = False) -> str:
        from src.data.bayes_precision_fusion_capture import OPENMETEO_MODEL_IDS
        from src.data.day0_hourly_vectors import _day0_provider_run_meta, build_request_hash
        from src.data.openmeteo_ecmwf_ifs9_anchor import SINGLE_RUNS_FORECAST_URL
        api_model = "ecmwf_ifs025_ensemble" if ensemble else OPENMETEO_MODEL_IDS.get(model, model)
        params = {"endpoint": SINGLE_RUNS_FORECAST_URL, "models": api_model,
                  "metadata_model": api_model, "run": run.isoformat(),
                  "timezone": city.timezone, "hourly": "temperature_2m"}
        request_hash = build_request_hash(endpoint=SINGLE_RUNS_FORECAST_URL,
            params=params, models=[api_model if ensemble else model], captured_at=captured.isoformat(),
            payload={"hourly": {"time": times,
                "temperature_2m_members": [
                    [celsius(native_boundary_f) if hour <= 13 else
                     celsius(native_boundary_f + (index - 25) * 0.02)
                     for hour in remaining_hours] for index in range(51)]}}
            if ensemble else {"hourly": {"time": times, "test_member_model": model}})
        return json.dumps(_day0_provider_run_meta(model=model, model_api_id=api_model,
            run=run, available_at=available, modified_at=available,
            authority="run_pinned_single_runs", endpoint_mode="single_runs",
            request_params=params, request_hash=request_hash,
            fetch_started_at=captured + timedelta(seconds=1),
            fetch_finished_at=captured + timedelta(seconds=2)))

    vectors = [
        Day0HourlyVector(
            model=model,
            city="Chicago",
            target_date=target.isoformat(),
            timezone_name="America/Chicago",
            captured_at="2026-10-01T17:30:00+00:00",
            times=times,
            temps_c=tuple(
                celsius(native_boundary_f) if hour <= 13 else value_c
                for hour in remaining_hours
            ),
            source_run_meta_json=vector_meta(model),
        )
        for model, value_c in zip(("ecmwf_ifs", "icon_global"), member_values_c)
    ]
    ensemble = [
        Day0HourlyVector(
            model=model, city="Chicago", target_date=target.isoformat(),
            timezone_name="America/Chicago", captured_at=captured.isoformat(),
            times=times,
            temps_c=tuple(
                celsius(native_boundary_f) if hour <= 13
                else celsius(native_boundary_f + (index - 25) * 0.02)
                for hour in remaining_hours
            ),
            source_run_meta_json=vector_meta(model, ensemble=True),
        )
        for index, model in enumerate(day0_source_clock_ensemble_member_models())
    ]
    likelihood_identity = {
        "semantics": "same_station_preliminary_report_survival_likelihood_jeffreys_prior_only_v2",
        "cutoff": computed_at.isoformat(),
        "successes": [],
        "failures": [],
        "unconfirmed_awc_ids": [],
        "alpha": 0.5,
        "beta": 0.5,
        "station_id": "KORD",
        "source_channel_pair": {
            "awc": "aviationweather_metar",
            "ogimet": "ogimet_metar_kord",
        },
    }
    likelihood = {
        **likelihood_identity,
        "identity_hash": hashlib.sha256(
            json.dumps(
                likelihood_identity, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest(),
        "boundary_survival_probability": 0.5,
    }
    monkeypatch.setattr(
        "src.data.day0_hourly_vectors.day0_hourly_models_for_city",
        lambda _city: ["ecmwf_ifs", "icon_global"],
    )
    monkeypatch.setattr(
        "src.data.day0_hourly_vectors.read_freshest_day0_hourly_vectors",
        lambda **kwargs: (
            ensemble if len(kwargs.get("expected_models") or ()) == 51 else vectors
        ),
    )
    monkeypatch.setattr(
        "src.data.day0_hourly_vectors.read_day0_current_temperature_state",
        lambda **_kwargs: current_state,
    )
    monkeypatch.setattr(
        "src.data.day0_observation_reader.same_station_preliminary_report_survival_likelihood",
        lambda *_args, **_kwargs: likelihood,
    )
    from src.data.day0_fast_obs import (
        FAST_RESIDUAL_LIKELIHOOD_REVISION,
        FastStationResidualLikelihood,
    )

    residual_identity = {
        "semantics_revision": FAST_RESIDUAL_LIKELIHOOD_REVISION,
        "station_id": "KORD", "settlement_channel": "noaa_wrh_kord",
        "fast_channel": "aviationweather_metar", "unit": "F",
        "as_of": computed_at.isoformat(),
        "window_start": (computed_at - timedelta(days=7)).isoformat(),
        "matched_pairs": 30, "residual_weights_c": ((0.0, 0.8),),
        "unknown_weight": 0.2, "settlement_extreme_c": None,
    }
    residual = FastStationResidualLikelihood(
        **residual_identity,
        identity_hash=hashlib.sha256(json.dumps(
            residual_identity, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest(),
    )
    # The live builder returns a residual for a raw NOAA METAR boundary too
    # (f41739f6d). Only the producer's tail gate may keep it out of q: that
    # METAR is already the preliminary-survival boundary.
    monkeypatch.setattr(
        "src.data.day0_fast_obs.build_fast_station_residual_likelihood",
        lambda *_args, **_kwargs: residual,
    )
    if source == "wu_api+same_station_fast_tail":
        monkeypatch.setattr(
            materializer_mod, "_day0_remaining_vector_witness",
            lambda *_args, **_kwargs: _wu_current_carrier_test_witness(
                city="Chicago", target_date=target.isoformat(), metric="high",
                at=computed_at,
            ),
        )

    complete_ensemble = ensemble[:]
    ensemble.clear()
    missing_ens = materialize_replacement_forecast_live(conn, request)
    assert missing_ens.status == "BLOCKED"
    assert missing_ens.reason_codes == ("DAY0_CONDITIONAL_HIGH_ENSEMBLE_UNAVAILABLE",)
    assert materializer_mod.compute_replacement_posterior_readonly(conn, request) is None
    ensemble[:] = complete_ensemble

    import src.data.day0_hourly_vectors as hourly_vectors

    original_extremes = hourly_vectors.remaining_day_extremes_c_with_current_state
    with monkeypatch.context() as missing_anchor:
        def without_ensemble_observation_anchor(vectors, **kwargs):
            if len(vectors) == 51:
                return (), ()
            return original_extremes(vectors, **kwargs)

        missing_anchor.setattr(
            hourly_vectors, "remaining_day_extremes_c_with_current_state",
            without_ensemble_observation_anchor,
        )
        no_anchor = materialize_replacement_forecast_live(conn, request)
        assert no_anchor.status == "BLOCKED"
        assert no_anchor.reason_codes == (
            "DAY0_CONDITIONAL_HIGH_OBSERVATION_ANCHOR_UNAVAILABLE",
        )

    ensemble[0] = replace(
        ensemble[0], source_run_meta_json=vector_meta(ensemble[0].model, ensemble=True).replace(
            run.isoformat(), datetime(2026, 10, 1, 0, tzinfo=UTC).isoformat(),
        ),
    )
    with pytest.raises(ValueError, match="DAY0_CONDITIONAL_HIGH_ENSEMBLE_CYCLE_MISMATCH"):
        materialize_replacement_forecast_live(conn, request)
    ensemble[:] = complete_ensemble

    pinned_run = run + timedelta(hours=2)
    pin_file = tmp_path / "day0_provider_run_hwm_pin.json"
    pin_file.write_text(json.dumps({
        "schema_version": 1,
        "entries": {"ecmwf_ifs025_ensemble": {
            "run_initialisation_time": pinned_run.isoformat(),
            "run_availability_time": (run + timedelta(hours=3)).isoformat(),
            "recorded_at": (computed_at - timedelta(minutes=1)).isoformat(),
        }},
    }))
    with monkeypatch.context() as pinned:
        pinned.setattr(hourly_vectors, "_day0_provider_run_hwm_pin_path", lambda: pin_file)
        stale = materialize_replacement_forecast_live(conn, request)
        assert stale.status == "BLOCKED"
        assert stale.reason_codes == ("DAY0_CONDITIONAL_HIGH_ENSEMBLE_SUPERSEDED",)
        next_ensemble = []
        for vector in complete_ensemble:
            meta = json.loads(vector.source_run_meta_json)
            meta["provider_source_cycle_time_utc"] = pinned_run.isoformat()
            meta["provider_source_available_at_utc"] = (
                run + timedelta(hours=3)
            ).isoformat()
            meta["provider_run_id"] = meta["provider_run_id"].replace(
                run.isoformat(), pinned_run.isoformat(),
            )
            next_ensemble.append(replace(vector, source_run_meta_json=json.dumps(meta)))
        ensemble[:] = next_ensemble
        refreshed = materialize_replacement_forecast_live(conn, request)
        assert refreshed.ok is True
        assert refreshed.posterior_id is not None
    ensemble[:] = complete_ensemble

    result = materialize_replacement_forecast_live(conn, request)

    assert result.ok is True
    row = conn.execute(
        "SELECT q_json, provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
        (result.posterior_id,),
    ).fetchone()
    q = json.loads(row["q_json"])
    provenance = json.loads(row["provenance_json"])
    if source == "wu_api+same_station_fast_tail":
        _assert_wu_fast_pinned_contract(
            provenance, city="Chicago", target_date=target.isoformat(),
            metric="high", decision_time=computed_at,
        )
    assert provenance["day0_remaining_carrier_future_extremes_c"] == pytest.approx(
        member_values_c
    )
    shape = day0_conditional_high_shape(
        conn=conn, city=city, target_date=target.isoformat(),
        decision_time=computed_at, current_state=current_state,
        provider_vectors=vectors,
    )
    assert shape.provider_centers_c == pytest.approx(member_values_c)
    assert len(shape.ensemble_centers_c) == 51
    instrument_sigma = sigma_instrument_for_city(city)
    assert instrument_sigma.unit == "F"
    # Only same-observation conditional ENS residual enters the member noise;
    # the provider spread already appears in the two carrier scenarios.
    native_path_sigma_f = shape.extra_sigma_c * 9.0 / 5.0
    native_sigma_f = math.hypot(native_path_sigma_f, instrument_sigma.value)
    def normal_cdf(value: float, mean: float) -> float:
        return 0.5 * (1.0 + math.erf((value - mean) / (native_sigma_f * math.sqrt(2.0))))

    def maximum_cdf(value: float, mean: float, boundary: float | None) -> float:
        if boundary is not None and value < boundary:
            return 0.0
        return normal_cdf(value, mean)

    expected = {}
    for bin_id, lower_f, upper_f in native_bins:
        lower_edge = -math.inf if lower_f is None else lower_f - 0.5
        upper_edge = math.inf if upper_f is None else upper_f + 0.5
        probability = 0.0
        for mean in native_members_f:
            for boundary, weight in (
                ((native_boundary_f, 0.8), (None, 0.2))
                if source == "wu_api+same_station_fast_tail"
                else ((native_boundary_f, 0.5), (None, 0.5))
            ):
                lower_cdf = 0.0 if math.isinf(lower_edge) else maximum_cdf(lower_edge, mean, boundary)
                upper_cdf = 1.0 if math.isinf(upper_edge) else maximum_cdf(upper_edge, mean, boundary)
                probability += weight * (upper_cdf - lower_cdf) / len(native_members_f)
        expected[bin_id] = probability

    assert set(q) == set(expected)
    assert all(math.isfinite(value) and value >= 0.0 for value in q.values())
    assert sum(q.values()) == pytest.approx(1.0)
    assert q == pytest.approx(expected, abs=1e-12)
    assert provenance["day0_remaining_carrier_path_error_sigma_c"] == pytest.approx(
        native_path_sigma_f * 5.0 / 9.0
    )
    assert provenance["day0_conditional_high_shape_identity"] == shape.identity
    assert provenance["q_shape"] == (
        "fused_day0_fast_residual_likelihood"
        if source == "wu_api+same_station_fast_tail"
        else "day0_remaining_shared_carrier_v2"
    )
    assert provenance["day0_remaining_carrier_operator"] == (
        "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2"
    )
    assert provenance["day0_remaining_carrier_content_identity"]
    assert provenance["day0_remaining_carrier_sample_count"] == 500
    assert provenance["day0_current_temperature_state"] == current_state.identity()
    if source != "wu_api+same_station_fast_tail":
        assert provenance["day0_preliminary_report_survival_likelihood"]
        assert "fast_residual_likelihood" not in provenance["day0_provisional_observation"]
        from src.data.replacement_forecast_cycle_policy import (
            fast_residual_carrier_authority_reason,
        )

        assert fast_residual_carrier_authority_reason(
            provenance, city="Chicago", target_date=target.isoformat(),
            metric="high", materialized_at=computed_at,
        ) is None
    if source == "wu_api+same_station_fast_tail":
        assert provenance["day0_preliminary_report_survival_likelihood"] == {}
        assert provenance["day0_provisional_observation"]["fast_residual_likelihood"]["unknown_weight"] == 0.2
        import src.engine.event_reactor_adapter as era
        import numpy as np

        conditioning = era._day0_replacement_conditioning(
            SimpleNamespace(provenance_json=provenance), provisional=True,
            metric="high", unit="F", decision_time=computed_at,
            entry_authority=False,
        )
        from src.events.opportunity_event import make_opportunity_event

        fact = {
            "observation_time": computed_at.isoformat(),
            "observed_extreme_native": native_boundary_f,
            "sample_count": 1, "unit": "F", "station_id": "KORD",
            "observation_source": "wu_icao_history",
            "observation_available_at": computed_at.isoformat(),
            "raw_payload_sha256": "a" * 64,
        }
        event = make_opportunity_event(
            event_type="DAY0_EXTREME_UPDATED",
            entity_key="Chicago|2026-10-01|high|KORD", source="test-current-carrier",
            observed_at=computed_at.isoformat(), available_at=computed_at.isoformat(),
            received_at=computed_at.isoformat(),
            payload={
                "city": "Chicago", "target_date": target.isoformat(), "metric": "high",
                "station_id": "KORD", "settlement_source": "wu_icao_history",
                "settlement_unit": "F", "observation_time": computed_at.isoformat(),
                "rounded_value": native_boundary_f, "raw_value": native_boundary_f,
                "high_so_far": native_boundary_f,
                "source_match_status": "MATCH", "local_date_status": "MATCH",
                "station_match_status": "MATCH", "dst_status": "UNAMBIGUOUS",
                "metric_match_status": "MATCH", "rounding_status": "MATCH",
                "source_authorized_status": "AUTHORIZED", "live_authority_status": "live",
            }, causal_snapshot_id="wu-current-carrier",
        )
        with monkeypatch.context() as boundary:
            boundary.setattr(
                "src.data.replacement_forecast_current_target_plan._latest_authorized_day0_fact",
                lambda *_args, **_kwargs: fact,
            )
            boundary.setattr(
                "src.data.day0_fast_obs.latest_fast_station_extreme_c",
                lambda *_args, **_kwargs: (celsius(native_boundary_f), computed_at.isoformat(), 1, "F"),
            )
            projected = era._global_day0_execution_payload(
                event,
                family=SimpleNamespace(city="Chicago", target_date=target.isoformat(), metric="high"),
                resolution=SimpleNamespace(measurement_unit="F", station_id="KORD"),
                conditioning=conditioning, observation_conn=conn,
                decision_time=computed_at, posterior_id=result.posterior_id,
            )
        assert projected["_edli_day0_carrier_bin_topology"] == provenance["bin_topology"]
        assert projected["_edli_day0_remaining_content_identity"] == provenance[
            "day0_remaining_carrier_content_identity"
        ]
        state = provenance["day0_current_temperature_state"]
        replay_payload = {
            **projected,
            "metric": "high", "target_date": target.isoformat(),
            "settlement_source": source, "rounded_value": native_boundary_f,
        }
        assert {field: replay_payload.get(field) for field in (
            "_edli_day0_current_temperature_native",
            "_edli_day0_current_temperature_observed_at_utc",
            "_edli_day0_current_temperature_source",
            "_edli_day0_conditional_high_shape_identity",
            "_edli_day0_conditional_high_shape_witness",
            "_edli_day0_remaining_variance_basis",
            "_edli_day0_remaining_center_bias_c",
        )} == {
            "_edli_day0_current_temperature_native": state["value_native"],
            "_edli_day0_current_temperature_observed_at_utc": state["observed_at_utc"],
            "_edli_day0_current_temperature_source": state["source"],
            "_edli_day0_conditional_high_shape_identity": provenance["day0_conditional_high_shape_identity"],
            "_edli_day0_conditional_high_shape_witness": provenance["day0_conditional_high_shape_witness"],
            "_edli_day0_remaining_variance_basis": provenance["day0_remaining_variance_basis"],
            "_edli_day0_remaining_center_bias_c": provenance.get("day0_remaining_center_bias_c"),
        }
        replay = era._day0_remaining_p_raw_vector(
            np.asarray(native_members_f), city=city,
            settlement_semantics=semantics,
            bins=[SimpleNamespace(bin_id=bin_id, low=lower_f, high=upper_f)
                  for bin_id, lower_f, upper_f in native_bins],
            payload=replay_payload, extra_member_sigma=0.0,
            decision_time=computed_at,
        )
        assert replay.tolist() == pytest.approx([q[bin_id] for bin_id, *_ in native_bins])
        composed = replay_payload["_edli_day0_composed_probability_samples"]
        assert len(composed) == 500
        for index, row in enumerate(composed):
            assert row == pytest.approx([
                provenance["q_bootstrap_samples_by_bin"][bin_id][index]
                for bin_id, *_ in native_bins
            ])
        assert replay.tolist() != pytest.approx(provenance["day0_remaining_carrier_q"])
        from copy import deepcopy

        tampered_payload = deepcopy(replay_payload)
        tampered_likelihood = tampered_payload[
            "_edli_global_day0_binding"
        ][
            "statistical_probability_conditioning"
        ]["fast_residual_likelihood"]
        tampered_likelihood["unknown_weight"] = 0.01
        with pytest.raises(ValueError, match="DAY0_FAST_RESIDUAL_POSTERIOR_IDENTITY_INVALID"):
            era._day0_remaining_p_raw_vector(
                np.asarray(native_members_f), city=city,
                settlement_semantics=semantics,
                bins=[SimpleNamespace(bin_id=bin_id, low=lower_f, high=upper_f)
                      for bin_id, lower_f, upper_f in native_bins],
                payload=tampered_payload, extra_member_sigma=0.0,
                decision_time=computed_at,
            )
        closed_payload = deepcopy(replay_payload)
        closed_payload.pop("_edli_day0_remaining_content_identity")
        closed_payload.pop("_edli_day0_current_temperature_native")
        closed_payload.pop("_edli_day0_current_temperature_observed_at_utc")
        closed_payload.pop("_edli_day0_current_temperature_source")
        with pytest.raises(ValueError, match="DAY0_PROVISIONAL_REVISION_LIKELIHOOD_UNAVAILABLE"):
            era._day0_remaining_p_raw_vector(
                np.asarray(native_members_f), city=city,
                settlement_semantics=semantics,
                bins=[SimpleNamespace(bin_id=bin_id, low=lower_f, high=upper_f)
                      for bin_id, lower_f, upper_f in native_bins],
                payload=closed_payload, extra_member_sigma=0.0,
                decision_time=computed_at + timedelta(days=1),
            )
    bootstrap_samples = provenance["q_bootstrap_samples_by_bin"]
    assert set(bootstrap_samples) == set(q)
    assert all(len(samples) == 500 for samples in bootstrap_samples.values())
    assert all(
        sum(bootstrap_samples[bin_id][index] for bin_id in q) == pytest.approx(1.0)
        for index in range(500)
    )

    revised_state = Day0CurrentTemperatureState(
        value_native=85.0,
        observed_at=computed_at + timedelta(minutes=10),
        source="aviationweather_metar",
    )
    monkeypatch.setattr(
        "src.data.day0_hourly_vectors.read_day0_current_temperature_state",
        lambda **_kwargs: revised_state,
    )
    own_raw = tuple(tuple(row) for row in conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id"))
    own_artifacts = tuple(tuple(row) for row in conn.execute("SELECT * FROM raw_forecast_artifacts ORDER BY artifact_id"))
    revised_request = _install_hko_live_fusion(monkeypatch,conn=conn,
        request=replace(request,computed_at=computed_at+timedelta(minutes=11)),
        selected_cells=selected_cells)
    assert tuple(tuple(row) for row in conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id")) == own_raw
    assert tuple(tuple(row) for row in conn.execute("SELECT * FROM raw_forecast_artifacts ORDER BY artifact_id")) == own_artifacts
    revised = materialize_replacement_forecast_live(conn,revised_request)
    assert revised.ok is True
    revised_row = conn.execute(
        "SELECT q_json, provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
        (revised.posterior_id,),
    ).fetchone()
    revised_q = json.loads(revised_row["q_json"])
    revised_provenance = json.loads(revised_row["provenance_json"])
    assert revised_provenance["day0_current_temperature_state"] == revised_state.identity()
    assert revised_provenance["day0_remaining_carrier_content_identity"] != provenance[
        "day0_remaining_carrier_content_identity"
    ]
    assert revised_q != q


@pytest.mark.usefixtures("_historical_shanghai_component_surface")
def test_wu_composite_low_rebuilds_current_path_before_one_residual_update(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shanghai LOW prices a changed level with the same provisional extreme."""
    from src.data.day0_fast_obs import (
        FAST_RESIDUAL_LIKELIHOOD_REVISION, FastStationResidualLikelihood,
    )
    from src.data.day0_hourly_vectors import Day0CurrentTemperatureState

    conn,basis = _historical_shanghai_component_request(tmp_path,monkeypatch,metric="low")
    observed = _dt(17, 55)
    current = {"value": 24.0}
    request = replace(
        replace(basis,
            computed_at=_dt(18), expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
            day0_observed_extreme_c=23.0,
            day0_observed_extreme_source="wu_api+same_station_fast_tail",
            day0_observed_extreme_observation_time=observed.isoformat(),
            day0_observed_extreme_sample_count=10,
        ),
        temperature_metric="low",
        baseline_data_version=_current_baseline_data_version("low"),
        bins=(
            _TemperatureBin("cool", upper_c=22.0, center_c=21.0),
            _TemperatureBin("warm", lower_c=23.0, upper_c=25.0, center_c=24.0),
            _TemperatureBin("hot", lower_c=26.0, center_c=27.0),
        ),
    )
    residual_identity = {
        "semantics_revision": FAST_RESIDUAL_LIKELIHOOD_REVISION,
        "station_id": "ZSPD", "settlement_channel": "wu_icao_history",
        "fast_channel": "aviationweather_metar", "unit": "C",
        "as_of": observed.isoformat(),
        "window_start": (observed - timedelta(days=7)).isoformat(),
        "matched_pairs": 30, "residual_weights_c": ((0.0, 0.8),),
        "unknown_weight": 0.2, "settlement_extreme_c": None,
    }
    residual = FastStationResidualLikelihood(
        **residual_identity, identity_hash=hashlib.sha256(json.dumps(
            residual_identity, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest(),
    )
    monkeypatch.setattr(
        "src.data.day0_fast_obs.build_fast_station_residual_likelihood",
        lambda *_args, **_kwargs: residual,
    )
    monkeypatch.setattr(
        "src.data.day0_hourly_vectors.read_day0_current_temperature_state",
        lambda **_kwargs: Day0CurrentTemperatureState(
            value_native=current["value"], observed_at=observed,
            source="aviationweather_metar",
        ),
    )
    monkeypatch.setattr(
        materializer_mod, "_day0_noaa_carrier_future_members",
        lambda *_args, **_kwargs: (
            (current["value"], current["value"] + 3.0), 0.4,
            _dt(18).isoformat(), (), None,
        ),
    )
    monkeypatch.setattr(
        materializer_mod, "_day0_remaining_vector_witness",
        lambda *_args, **_kwargs: _wu_current_carrier_test_witness(
            city="Shanghai", target_date="2026-06-07", metric="low",
            at=_dt(18),
        ),
    )
    first = materialize_replacement_forecast_live(conn, request)
    assert first.ok is True
    first_row = conn.execute(
        "SELECT q_json, provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
        (first.posterior_id,),
    ).fetchone()
    q_first, provenance_first = json.loads(first_row["q_json"]), json.loads(first_row["provenance_json"])
    _assert_wu_fast_pinned_contract(
        provenance_first, city="Shanghai", target_date="2026-06-07",
        metric="low", decision_time=_dt(18),
    )
    assert provenance_first["q_shape"] == "fused_day0_fast_residual_likelihood"
    assert provenance_first["day0_current_temperature_state"]["value_native"] == 24.0
    assert provenance_first["day0_provisional_observation"]["support_truncation"] is False
    assert provenance_first["day0_provisional_observation"]["fast_residual_likelihood"]["unknown_weight"] == 0.2
    assert q_first != dict(zip(
        ("cool", "warm", "hot"), provenance_first["day0_remaining_carrier_q"],
    ))
    import numpy as np
    import src.engine.event_reactor_adapter as era
    from src.config import runtime_cities_by_name
    from src.contracts.settlement_semantics import SettlementSemantics

    conditioning = era._day0_replacement_conditioning(
        SimpleNamespace(provenance_json=provenance_first), provisional=True,
        metric="low", unit="C", decision_time=_dt(18), entry_authority=False,
    )
    state = provenance_first["day0_current_temperature_state"]
    payload = {
        "metric": "low", "target_date": "2026-06-07",
        "settlement_source": "wu_api+same_station_fast_tail",
        "rounded_value": 23.0,
        "statistical_probability_conditioning": conditioning,
        "_edli_day0_current_temperature_native": state["value_native"],
        "_edli_day0_current_temperature_observed_at_utc": state["observed_at_utc"],
        "_edli_day0_current_temperature_source": state["source"],
        "_edli_day0_remaining_content_identity": provenance_first["day0_remaining_carrier_content_identity"],
        "_edli_day0_probability_operator": provenance_first["day0_remaining_carrier_operator"],
        "_edli_day0_remaining_carrier_q": provenance_first["day0_remaining_carrier_q"],
        "_edli_day0_remaining_probability_samples": provenance_first["day0_remaining_carrier_probability_samples"],
        "_edli_day0_remaining_probability_sample_count": provenance_first["day0_remaining_carrier_sample_count"],
        "_edli_day0_remaining_carrier_future_extremes_c": provenance_first["day0_remaining_carrier_future_extremes_c"],
        "_edli_day0_remaining_carrier_final_extremes_c": provenance_first["day0_remaining_carrier_final_extremes_c"],
        "_edli_day0_remaining_carrier_path_error_sigma_c": provenance_first["day0_remaining_carrier_path_error_sigma_c"],
        "_edli_day0_remaining_center_bias_c": provenance_first["day0_remaining_center_bias_c"],
        "_edli_day0_remaining_center_policy": provenance_first["day0_remaining_center_policy"],
        "_edli_day0_probability_mixture_policy": provenance_first["day0_probability_mixture_policy"],
        "_edli_day0_remaining_carrier_probability_cutoff_utc": provenance_first["day0_remaining_carrier_probability_cutoff_utc"],
        "_edli_day0_carrier_bin_topology": provenance_first["bin_topology"],
        "_edli_day0_remaining_vector_witness": provenance_first["day0_remaining_vector_witness"],
    }
    bins = [SimpleNamespace(bin_id=item.bin_id, low=item.lower_c, high=item.upper_c)
            for item in request.bins]
    replay = era._day0_remaining_p_raw_vector(
        np.asarray(provenance_first["day0_remaining_carrier_future_extremes_c"]),
        city=runtime_cities_by_name()["Shanghai"],
        settlement_semantics=SettlementSemantics.for_city(runtime_cities_by_name()["Shanghai"]),
        bins=bins, payload=payload, extra_member_sigma=0.0,
        decision_time=_dt(18),
    )
    assert replay.tolist() == pytest.approx([q_first[item.bin_id] for item in request.bins])
    composed = payload["_edli_day0_composed_probability_samples"]
    assert len(composed) == 500
    for index, row in enumerate(composed):
        assert row == pytest.approx([
            provenance_first["q_bootstrap_samples_by_bin"][item.bin_id][index]
            for item in request.bins
        ])

    current["value"] = 27.0
    revised = materialize_replacement_forecast_live(
        conn, _refresh_shanghai_owner_request(conn,monkeypatch,
            replace(request, computed_at=_dt(18, 10)),record_observed_prints=False),
    )
    assert revised.ok is True
    revised_row = conn.execute(
        "SELECT q_json, provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
        (revised.posterior_id,),
    ).fetchone()
    q_revised = json.loads(revised_row["q_json"])
    provenance_revised = json.loads(revised_row["provenance_json"])
    assert provenance_revised["day0_current_temperature_state"]["value_native"] == 27.0
    assert provenance_revised["day0_remaining_carrier_content_identity"] != provenance_first[
        "day0_remaining_carrier_content_identity"
    ]
    assert q_revised != q_first


def _wu_fast_post_day_request(tmp_path, monkeypatch, *, future_members):
    """Shanghai HIGH WU fast tail, decided after the target local day ended."""
    from src.data.day0_fast_obs import (
        FAST_RESIDUAL_LIKELIHOOD_REVISION, FastStationResidualLikelihood,
    )
    from src.data.day0_hourly_vectors import Day0CurrentTemperatureState

    # Same owner-fixture geometry as the post-local-day prior test: Shanghai
    # 2026-10-02 closes at 16:00Z.
    conn, basis = _shanghai_current_owner_request(tmp_path, monkeypatch,
        source_cycle_time=datetime(2026, 10, 1, 12, tzinfo=UTC),
        computed_at=datetime(2026, 10, 2, 15, 5, tzinfo=UTC), record_observed_prints=False)
    observed = datetime(2026, 10, 2, 15, 55, tzinfo=UTC)
    request = replace(basis,
        day0_observed_extreme_c=26.0,
        day0_observed_extreme_source="wu_api+same_station_fast_tail",
        day0_observed_extreme_observation_time=observed.isoformat(),
        day0_observed_extreme_sample_count=10,
        bins=(
            _TemperatureBin("cool", upper_c=25.0, center_c=24.0),
            _TemperatureBin("mid", lower_c=26.0, upper_c=27.0, center_c=26.5),
            _TemperatureBin("hot", lower_c=28.0, center_c=29.0),
        ),
    )
    residual_identity = {
        "semantics_revision": FAST_RESIDUAL_LIKELIHOOD_REVISION,
        "station_id": "ZSPD", "settlement_channel": "noaa_wrh_zspd",
        "fast_channel": "aviationweather_metar", "unit": "C",
        "as_of": observed.isoformat(),
        "window_start": (observed - timedelta(days=7)).isoformat(),
        "matched_pairs": 30, "residual_weights_c": ((0.0, 0.8),),
        "unknown_weight": 0.2, "settlement_extreme_c": None,
    }
    residual = FastStationResidualLikelihood(
        **residual_identity, identity_hash=hashlib.sha256(json.dumps(
            residual_identity, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest(),
    )
    monkeypatch.setattr(
        "src.data.day0_fast_obs.build_fast_station_residual_likelihood",
        lambda *_args, **_kwargs: residual,
    )
    monkeypatch.setattr(
        "src.data.day0_hourly_vectors.read_day0_current_temperature_state",
        lambda **_kwargs: Day0CurrentTemperatureState(
            value_native=26.0, observed_at=observed, source="aviationweather_metar",
        ),
    )
    post_day = datetime(2026, 10, 2, 16, 5, tzinfo=UTC)  # 00:05 Oct 3 Asia/Shanghai
    calls = []

    def future(_conn, req, **_kwargs):
        calls.append(req.computed_at)
        if future_members is None:
            raise ValueError("DAY0_NOAA_PRELIMINARY_CARRIER_CURRENT_TEMPERATURE_STATE_MISSING")
        return tuple(future_members), 0.0, req.computed_at.isoformat(), (), None

    monkeypatch.setattr(materializer_mod, "_day0_noaa_carrier_future_members", future)
    monkeypatch.setattr(
        materializer_mod, "_day0_remaining_vector_witness",
        lambda *_args, **_kwargs: _wu_current_carrier_test_witness(
            city="Shanghai", target_date="2026-10-02", metric="high", at=post_day,
        ),
    )
    request = _refresh_shanghai_owner_request(
        conn, monkeypatch, replace(request, computed_at=post_day), record_observed_prints=False,
    )
    assert materializer_mod._target_local_day_is_open(request) is False
    return conn, request, post_day, calls


@pytest.mark.usefixtures("_historical_shanghai_component_surface")
def test_wu_fast_post_day_row_carries_complete_carrier_every_validator_reproduces(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After local midnight the residual composes with the observed-extreme carrier."""
    from src.data.replacement_forecast_bundle_reader import _day0_carrier_identity_reason
    from src.data.replacement_forecast_cycle_policy import fast_residual_carrier_authority_reason
    import src.engine.event_reactor_adapter as era

    conn, request, post_day, calls = _wu_fast_post_day_request(
        tmp_path, monkeypatch, future_members=(26.0, 26.0),
    )
    result = materialize_replacement_forecast_live(conn, request)
    assert result.ok is True, result.reason_codes
    assert calls == [post_day]
    provenance = json.loads(conn.execute(
        "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
        (result.posterior_id,),
    ).fetchone()["provenance_json"])
    assert provenance["q_shape"] == "fused_day0_fast_residual_likelihood"
    assert provenance["day0_remaining_center_policy"] == "unshifted_live_v1"
    assert provenance["day0_remaining_center_bias_c"] == 0.0
    assert provenance["day0_probability_mixture_policy"] == "unmixed_live_v1"
    assert provenance["day0_remaining_carrier_future_extremes_c"] == [26.0, 26.0]
    assert provenance["day0_remaining_carrier_content_identity"]
    assert _day0_carrier_identity_reason(provenance) is None
    assert fast_residual_carrier_authority_reason(
        provenance, city="Shanghai", target_date="2026-10-02", metric="high",
        materialized_at=post_day,
    ) is None
    # Held pinned reader: the exact carrier reproduces at the post-day cut.
    _assert_wu_fast_pinned_contract(
        provenance, city="Shanghai", target_date="2026-10-02",
        metric="high", decision_time=post_day,
    )
    conditioning = era._day0_replacement_conditioning(
        SimpleNamespace(city="Shanghai", target_date="2026-10-02", provenance_json=provenance),
        provisional=True, metric="high", unit="C",
        decision_time=post_day + timedelta(minutes=30), entry_authority=False,
    )
    assert conditioning["source"] == "wu_api+same_station_fast_tail"
    assert conditioning["day0_remaining_carrier_content_identity"] == provenance[
        "day0_remaining_carrier_content_identity"
    ]


@pytest.mark.usefixtures("_historical_shanghai_component_surface")
def test_wu_fast_post_day_without_current_state_writes_no_row(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No causal current state after midnight is a blocked family, not a carrier-less row."""
    conn, request, post_day, calls = _wu_fast_post_day_request(
        tmp_path, monkeypatch, future_members=None,
    )
    before = conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0]
    result = materialize_replacement_forecast_live(conn, request)
    assert result.ok is False
    assert any("CURRENT_TEMPERATURE_STATE_MISSING" in code for code in result.reason_codes), result
    assert calls == [post_day]
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == before


def test_wu_and_raw_noaa_fast_are_provisional_until_wrh_authority() -> None:

    composite = _request(
        computed_at=_dt(18),
        expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        day0_observed_extreme_c=31.0,
        day0_observed_extreme_source="wu_api+same_station_fast_tail",
        day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
    )
    direct = replace(
        composite,
        day0_observed_extreme_source="aviationweather_metar",
    )
    settlement_page = replace(direct, day0_observed_extreme_source="noaa_wrh_zspd")

    assert materializer_mod._day0_absorbing_observed_extreme_c(composite) is None
    assert materializer_mod._day0_absorbing_observed_extreme_c(direct) is None
    assert materializer_mod._day0_absorbing_observed_extreme_c(settlement_page) == 31.0


def _assert_remaining_suffix_cannot_supply_full_day_prior(conn, monkeypatch, request, *, first_hour, captured,
                                                         day_in_progress=False):
    """Actual scalar/parser and vector-store roles, not a complete q license."""
    from src.config import runtime_cities_by_name
    from src.data import day0_hourly_vectors as hourly
    from src.data.openmeteo_ecmwf_ifs9_anchor import extract_openmeteo_ecmwf_ifs9_localday_anchor

    payload = json.loads(request.openmeteo_raw_payload_bytes)
    full = extract_openmeteo_ecmwf_ifs9_localday_anchor(payload,city_timezone=request.city_timezone,
        target_local_date=request.target_date,source_cycle_time=request.source_cycle_time,require_full_localday=True)
    assert full == request.openmeteo_anchor
    suffix = {**payload,"hourly":{
        "time":payload["hourly"]["time"][first_hour:],
        "temperature_2m":payload["hourly"]["temperature_2m"][first_hour:]}}
    # The ordinary producer's final anchor stage passes require_full_localday.
    # Observation maturity cannot turn these missing samples into a full mu.
    with pytest.raises(ValueError,match="partial local-day coverage: missing, duplicate or unordered hourly slots"):
        extract_openmeteo_ecmwf_ifs9_localday_anchor(suffix,city_timezone=request.city_timezone,
            target_local_date=request.target_date,source_cycle_time=request.source_cycle_time,require_full_localday=True)
    city = runtime_cities_by_name()[request.city]
    endpoint = "https://single-runs-api.open-meteo.com/v1/forecast"
    params = {"latitude":city.lat,"longitude":city.lon,"timezone":city.timezone,
              "hourly":"temperature_2m","models":"ecmwf_ifs","run":request.source_cycle_time.isoformat()}
    identity = hourly.build_request_hash(endpoint=endpoint,params=params,models=["ecmwf_ifs"],
        captured_at=captured.isoformat(),payload=suffix)
    metadata = hourly._day0_provider_run_meta(model="ecmwf_ifs",model_api_id="ecmwf_ifs",
        run=request.source_cycle_time,available_at=request.source_cycle_time+timedelta(hours=8),
        modified_at=request.source_cycle_time+timedelta(hours=8),authority="run_pinned_single_runs",
        endpoint_mode="single_runs",request_params={**params,"endpoint":endpoint},request_hash=identity,
        fetch_started_at=captured,fetch_finished_at=captured)
    vectors = hourly.parse_openmeteo_hourly_payload(suffix,city=city,models=["ecmwf_ifs"],
        captured_at=captured.isoformat(),source_run_meta_json=json.dumps(metadata))
    assert len(vectors)==1 and len(vectors[0].times)==24-first_hour
    assert hourly.persist_day0_hourly_vectors(vectors,target_date=str(request.target_date),conn=conn,
        request_hash=identity,endpoint=endpoint,now=request.computed_at)==1
    selected = hourly.read_freshest_day0_hourly_vectors(city=request.city,target_date=str(request.target_date),
        conn=conn,now=request.computed_at,expected_models=["ecmwf_ifs"],require_expected=True)
    assert len(selected)==1 and selected[0].times==vectors[0].times
    assert selected[0].source_run_meta_json==vectors[0].source_run_meta_json
    # Even a self-consistent new owned artifact/metadata must fail the
    # materializer's independent source replay, not only a hand-picked flag.
    from tests.test_openmeteo_cell_selection_and_elevation_are_product_identity import _selected_test_cell
    partial = extract_openmeteo_ecmwf_ifs9_localday_anchor(suffix,city_timezone=request.city_timezone,
        target_local_date=request.target_date,source_cycle_time=request.source_cycle_time)
    before=tuple(tuple(row) for row in conn.execute("SELECT * FROM forecast_posteriors ORDER BY posterior_id"))
    candidate = _install_hko_live_fusion(monkeypatch,conn=conn,request=replace(request,
        openmeteo_anchor=partial,openmeteo_raw_payload_bytes=json.dumps(suffix,sort_keys=True).encode()),
        selected_cells={model:_selected_test_cell(model,city.lat,city.lon)
                        for model in ("icon_global","ukmo_global_deterministic_10km")})
    assert candidate.openmeteo_precision_guard.passable_for_live_materialization
    result=materialize_replacement_forecast_live(conn,candidate)
    if day_in_progress:
        # Day0 law H = max(H_confirmed, H_remaining): the observed extreme owns
        # the elapsed slots, so a run owning the exact remaining window is lawful.
        assert result.ok is True, result
        return suffix
    # An ended day keeps the full-day law: a suffix cannot replace yesterday's prior.
    assert result.ok is False and result.reason_codes==("OM9_SOURCE_RESPONSE_INVALID",)
    assert tuple(tuple(row) for row in conn.execute("SELECT * FROM forecast_posteriors ORDER BY posterior_id"))==before
    return suffix


@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_day0_requires_full_prior_even_with_elapsed_observed_extreme(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replace the retired observation-fills-prefix contract, not its source gate."""
    conn,request = _shanghai_noaa_future_request(tmp_path,monkeypatch,
        absorbing_extreme=26.,current_temp_c=26.)
    assert request.day0_observed_extreme_source=="aviationweather_metar"
    assert materializer_mod._day0_absorbing_observed_extreme_c(request) is None
    assert request.source_cycle_time <= request.openmeteo_anchor.contributing_valid_times_utc[0]
    result = materialize_replacement_forecast_live(conn,request)
    assert result.ok is True
    assert "REPLACEMENT_MATERIALIZATION_OM9_LOCALDAY_HOURLY_COVERAGE_INCOMPLETE" not in result.reason_codes
    rows = tuple(tuple(row) for row in conn.execute("SELECT * FROM forecast_posteriors ORDER BY posterior_id"))
    _assert_remaining_suffix_cannot_supply_full_day_prior(conn,monkeypatch,request,first_hour=2,
        captured=request.computed_at-timedelta(minutes=1),day_in_progress=True)
    assert tuple(tuple(row) for row in conn.execute("SELECT * FROM forecast_posteriors ORDER BY posterior_id"))[:len(rows)]==rows


@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_post_localday_preserves_yesterdays_full_prior_not_suffix(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Yesterday's qualified observation still updates q; suffix is no full prior."""
    cycle=datetime(2026,10,1,12,tzinfo=UTC)
    conn,initial = _shanghai_current_owner_request(tmp_path,monkeypatch,source_cycle_time=cycle,
        computed_at=datetime(2026,10,2,15,5,tzinfo=UTC),observed_extreme=32.,observed_sample_count=24)
    assert materialize_replacement_forecast_live(conn,initial).ok is True
    entities=tuple(tuple(row) for row in conn.execute("SELECT * FROM raw_forecast_artifacts ORDER BY artifact_id"))
    request=_refresh_shanghai_owner_request(conn,monkeypatch,
        replace(initial,computed_at=datetime(2026,10,2,17,tzinfo=UTC)))
    assert materializer_mod._target_local_day_is_open(request) is False
    assert request.target_date==date(2026,10,2)
    assert request.source_cycle_time <= request.openmeteo_anchor.contributing_valid_times_utc[0]
    result = materialize_replacement_forecast_live(conn,request)
    assert result.ok is True
    row = conn.execute(
        "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
        (result.posterior_id,),
    ).fetchone()
    provenance = json.loads(row["provenance_json"])
    assert provenance["day0_conditioning"]["observed_extreme_c"] == 32.0
    assert provenance["day0_conditioning"]["sample_count"] == 24
    assert "REPLACEMENT_MATERIALIZATION_OM9_LOCALDAY_HOURLY_COVERAGE_INCOMPLETE" not in result.reason_codes
    _assert_remaining_suffix_cannot_supply_full_day_prior(conn,monkeypatch,request,first_hour=14,
        captured=initial.computed_at-timedelta(minutes=1))
    assert all(tuple(conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?",(row[0],)).fetchone())==row
               for row in entities)


def test_materializer_day0_blocks_om9_missing_future_hours_after_observed_extreme() -> None:
    request = _request(
        computed_at=_dt(18),
        expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        day0_observed_extreme_c=26.0,
        day0_observed_extreme_source="same_station_fast_tail",
        day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
        day0_observed_extreme_sample_count=2,
    )
    partial_request = replace(request, openmeteo_anchor=_anchor_with_local_hours(hours=range(10, 24)))

    result = materialize_replacement_forecast_live(_conn(), partial_request)

    assert result.ok is False
    assert "REPLACEMENT_MATERIALIZATION_OM9_LOCALDAY_HOURLY_COVERAGE_INCOMPLETE" in result.reason_codes


def test_materializer_blocks_readiness_when_baseline_identity_is_wrong() -> None:
    conn = _conn()

    result = materialize_replacement_forecast_live(
        conn,
        _request(baseline_data_version="wrong_baseline_data_version"),
    )

    assert result.ok is False
    assert result.reason_codes == ("REPLACEMENT_MATERIALIZATION_BASELINE_DATA_VERSION_MISMATCH",)
    assert result.posterior_id is None
    assert result.anchor_id is None
    assert result.readiness_id is None
    assert conn.execute("SELECT COUNT(*) FROM deterministic_forecast_anchors").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM readiness_state").fetchone()[0] == 0


@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_preserves_openmeteo_artifact_lineage_without_aifs(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    # Private setup-only PK allocation: the ordinary anchor writer must INSERT
    # artifact 11. No artifact tuple/body/clock or certificate is rewritten.
    assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts WHERE artifact_id>=11").fetchone()[0] == 0
    original_artifacts = tuple(conn.execute("SELECT * FROM raw_forecast_artifacts ORDER BY artifact_id"))
    conn.execute("UPDATE sqlite_sequence SET seq=10 WHERE name='raw_forecast_artifacts'")
    request = _hko_request(anchor_artifact_id=11)
    request = _install_hko_live_fusion(monkeypatch, conn=conn, request=request)
    assert request.anchor_artifact_id == 11
    assert tuple(conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id<11 ORDER BY artifact_id")) == original_artifacts

    result = materialize_replacement_forecast_live(conn, request)

    assert result.ok is True
    anchor_row = conn.execute("SELECT artifact_id FROM deterministic_forecast_anchors WHERE anchor_id = ?", (result.anchor_id,)).fetchone()
    assert anchor_row["artifact_id"] == 11
    posterior_row = conn.execute("SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?", (result.posterior_id,)).fetchone()
    assert "aifs_artifact_id" not in posterior_row["provenance_json"]
    assert '"openmeteo_anchor_artifact_id":11' in posterior_row["provenance_json"]
    readiness_row = conn.execute("SELECT dependency_json FROM readiness_state WHERE readiness_id = ?", (result.readiness_id,)).fetchone()
    assert '"artifact_id":22' not in readiness_row["dependency_json"]
    assert '"artifact_id":11' in readiness_row["dependency_json"]


@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_records_precision_guard_in_anchor_and_posterior_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    request = _hko_request()
    request = _install_hko_live_fusion(monkeypatch, conn=conn, request=request)

    result = materialize_replacement_forecast_live(
        conn,
        request,
    )

    assert result.ok is True
    anchor_row = conn.execute("SELECT provenance_json FROM deterministic_forecast_anchors WHERE anchor_id = ?", (result.anchor_id,)).fetchone()
    posterior_row = conn.execute("SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?", (result.posterior_id,)).fetchone()
    anchor_provenance = json.loads(anchor_row["provenance_json"])
    posterior_provenance = json.loads(posterior_row["provenance_json"])
    assert anchor_provenance["precision_guard"]["status"] == "PASS"
    assert anchor_provenance["precision_guard"]["high_risk_bucket"] == "standard"
    assert posterior_provenance["openmeteo_precision_guard"]["reason_codes"] == ["OM9_PRECISION_METADATA_PASS"]


@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_blocks_when_precision_guard_missing_or_blocked() -> None:
    conn = _conn()

    missing = materialize_replacement_forecast_live(
        conn,
        _hko_request(openmeteo_precision_guard=None),
    )

    assert missing.ok is False
    assert missing.reason_codes == ("OM9_PRECISION_GUARD_REQUIRED_FOR_MATERIALIZATION",)
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0

    good = _hko_precision_guard()
    blocked_guard = evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        replace(good.metadata, endpoint_mode="daily_vendor_aggregated"), raw_payload_bytes=_hko_raw_openmeteo_bytes())
    blocked = materialize_replacement_forecast_live(
        conn,
        _hko_request(openmeteo_precision_guard=blocked_guard),
    )

    assert blocked.ok is False
    assert "OM9_PRECISION_GUARD_NOT_LIVE_PASS" in blocked.reason_codes
    assert "OM9_ENDPOINT_MUST_BE_HOURLY_ZEUS_AGGREGATED" in blocked.reason_codes
    assert conn.execute("SELECT COUNT(*) FROM deterministic_forecast_anchors").fetchone()[0] == 0


def test_materializer_blocks_future_dependency_before_writing_shadow_rows() -> None:
    conn = _conn()

    result = materialize_replacement_forecast_live(
        conn,
        _request(openmeteo_source_available_at=_dt(5)),
    )

    assert result.ok is False
    assert result.reason_codes == ("REPLACEMENT_MATERIALIZATION_DEPENDENCY_AFTER_COMPUTED_AT",)
    assert result.posterior_id is None
    assert result.anchor_id is None
    assert result.readiness_id is None
    assert conn.execute("SELECT COUNT(*) FROM deterministic_forecast_anchors").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM readiness_state").fetchone()[0] == 0


def test_materializer_requires_dependency_source_run_ids_before_writing_shadow_rows() -> None:
    conn = _conn()

    result = materialize_replacement_forecast_live(
        conn,
        _request(openmeteo_source_run_id=""),
    )

    assert result.ok is False
    assert result.reason_codes == ("REPLACEMENT_MATERIALIZATION_OPENMETEO_SOURCE_RUN_ID_MISSING",)
    assert conn.execute("SELECT COUNT(*) FROM deterministic_forecast_anchors").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM readiness_state").fetchone()[0] == 0


@pytest.mark.usefixtures("_hko_source_surface")
def test_materializer_posterior_available_at_includes_baseline_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    request = _hko_request(baseline_source_available_at=_hko_dt(7,30), openmeteo_source_available_at=_hko_dt(3))
    request = _install_hko_live_fusion(monkeypatch, conn=conn, request=request)

    result = materialize_replacement_forecast_live(
        conn,
        request,
    )

    assert result.ok is True
    posterior_row = conn.execute("SELECT source_available_at FROM forecast_posteriors WHERE posterior_id = ?", (result.posterior_id,)).fetchone()
    assert posterior_row["source_available_at"] == _hko_dt(7,30).isoformat()


def test_materializer_blocks_expired_request_before_writing_shadow_rows() -> None:
    conn = _conn()

    result = materialize_replacement_forecast_live(
        conn,
        _request(expires_at=_dt(4)),
    )

    assert result.ok is False
    assert result.reason_codes == ("REPLACEMENT_MATERIALIZATION_EXPIRY_NOT_AFTER_COMPUTED_AT",)
    assert conn.execute("SELECT COUNT(*) FROM deterministic_forecast_anchors").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0


def test_materialize_script_template_requires_precision_metadata() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/materialize_replacement_forecast_live.py", "--print-template"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    template = json.loads(result.stdout)

    assert template["precision_metadata_json"] == "openmeteo_precision_metadata.json"


def test_materialize_script_attaches_world_observations_read_only(
    tmp_path, monkeypatch
) -> None:
    import scripts.materialize_replacement_forecast_live as cli
    import src.state.db as state_db

    forecasts_path = tmp_path / "forecasts.db"
    world_path = tmp_path / "world.db"
    world = sqlite3.connect(world_path)
    world.execute("CREATE TABLE observation_prints (value_native REAL NOT NULL)")
    world.execute("INSERT INTO observation_prints VALUES (8.0)")
    world.commit()
    world.close()
    conn = sqlite3.connect(forecasts_path)
    monkeypatch.setattr(state_db, "ZEUS_WORLD_DB_PATH", world_path)

    cli._attach_world_read_only(conn)

    assert conn.execute(
        "SELECT value_native FROM world.observation_prints"
    ).fetchone()[0] == 8.0
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("INSERT INTO world.observation_prints VALUES (9.0)")
    conn.close()


def test_materializer_connection_skips_journal_bootstrap_behind_bulk_writer(
    tmp_path, monkeypatch
) -> None:
    import src.state.db as state_db

    forecast_path = tmp_path / "forecasts.db"
    bootstrap = sqlite3.connect(forecast_path)
    assert bootstrap.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    bootstrap.execute("CREATE TABLE source_rows (value INTEGER NOT NULL)")
    bootstrap.commit()
    bootstrap.execute("BEGIN IMMEDIATE")
    bootstrap.execute("INSERT INTO source_rows VALUES (1)")
    monkeypatch.setattr(state_db, "ZEUS_FORECASTS_DB_PATH", forecast_path)

    started = time.monotonic()
    materializer = (
        state_db.connect_existing_forecasts_db_without_journal_bootstrap()
    )
    elapsed = time.monotonic() - started
    try:
        assert elapsed < 0.5
        assert materializer.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        materializer.close()
        bootstrap.rollback()
        bootstrap.close()


def test_materialize_script_batch_reuses_connection_and_wakes_each_commit(
    tmp_path, monkeypatch, capsys
) -> None:
    import scripts.materialize_replacement_forecast_live as cli
    import src.state.db as state_db

    inputs = [tmp_path / "a.json", tmp_path / "b.json"]
    calls = []
    lock_held = False

    class _Connection:
        closed = False

        def close(self):
            self.closed = True

    conn = _Connection()
    monkeypatch.setattr(
        state_db,
        "connect_existing_forecasts_db_without_journal_bootstrap",
        lambda: conn,
    )
    monkeypatch.setattr(cli, "_attach_world_read_only", lambda _conn: None)

    @contextmanager
    def _writer_lock():
        nonlocal lock_held
        assert lock_held is False
        lock_held = True
        try:
            yield
        finally:
            lock_held = False

    monkeypatch.setattr(cli, "_forecast_writer_lock", _writer_lock)

    def _prepare(*_args, **_kwargs):
        assert lock_held is False
        with _kwargs["writer_lock"]():
            assert lock_held is True
        return cli._DurablePreparationReceipt(
            schema_ready=True,
            anchor_artifact_id=None,
            manifest_committed=False,
        )

    monkeypatch.setattr(
        cli,
        "_prepare_live_schema_and_manifest",
        _prepare,
    )

    def _run_one(input_json, **kwargs):
        assert lock_held is False
        with kwargs["writer_lock"]():
            assert lock_held is True
        calls.append((input_json, kwargs))
        return 0, json.dumps(
            {
                "status": "READY",
                "committed": True,
                "posterior_id": len(calls),
                "reactor_wake_published": kwargs["publish_wake"],
            }
        ) + "\n", ""

    monkeypatch.setattr(cli, "_run_one", _run_one)
    rc = cli.main(
        [
            "--batch-input-json",
            *(str(path) for path in inputs),
            "--commit",
        ]
    )

    assert rc == 0
    assert conn.closed is True
    assert [call[0] for call in calls] == inputs
    assert all(call[1]["conn"] is conn for call in calls)
    assert all(call[1]["commit"] is True for call in calls)
    assert all(call[1]["publish_wake"] is True for call in calls)
    assert all(call[1]["schema_ready"] is True for call in calls)
    assert all(call[1]["init_schema"] is False for call in calls)
    envelopes = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
    ]
    assert [Path(envelope["input_json"]) for envelope in envelopes] == inputs
    assert [envelope["returncode"] for envelope in envelopes] == [0, 0]


def test_materialize_script_batch_prepares_schema_before_first_input_error(
    tmp_path, monkeypatch, capsys
) -> None:
    import scripts.materialize_replacement_forecast_live as cli
    import src.state.db as state_db

    inputs = [tmp_path / "malformed.json", tmp_path / "valid.json"]
    calls = []
    preparations = []

    class _Connection:
        def close(self):
            return None

    conn = _Connection()
    monkeypatch.setattr(
        state_db,
        "connect_existing_forecasts_db_without_journal_bootstrap",
        lambda: conn,
    )
    monkeypatch.setattr(cli, "_attach_world_read_only", lambda _conn: None)

    @contextmanager
    def _writer_lock():
        yield

    monkeypatch.setattr(cli, "_forecast_writer_lock", _writer_lock)

    def _prepare(*args, **kwargs):
        preparations.append(kwargs)
        return cli._DurablePreparationReceipt(
            schema_ready=True,
            anchor_artifact_id=None,
            manifest_committed=False,
        )

    def _run_one(input_json, **kwargs):
        calls.append((input_json, kwargs))
        if input_json == inputs[0]:
            return 2, "", '{"status":"ERROR"}\n'
        return 0, '{"status":"READY"}\n', ""

    monkeypatch.setattr(cli, "_prepare_live_schema_and_manifest", _prepare)
    monkeypatch.setattr(cli, "_run_one", _run_one)

    rc = cli.main(
        [
            "--batch-input-json",
            *(str(path) for path in inputs),
            "--commit",
            "--init-schema",
        ]
    )

    assert rc == 0
    assert len(preparations) == 1
    assert preparations[0]["init_schema"] is True
    assert [call[0] for call in calls] == inputs
    assert all(call[1]["schema_ready"] is True for call in calls)
    assert all(call[1]["init_schema"] is False for call in calls)
    envelopes = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [envelope["returncode"] for envelope in envelopes] == [2, 0]


def test_materialize_manifest_persistence_does_not_verify_files_under_lock(
    monkeypatch,
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    calls = []

    def _write_manifest(_conn, manifest, **kwargs):
        calls.append((manifest, kwargs, _conn.in_transaction))
        return 17

    manifest = object()
    monkeypatch.setattr(cli, "write_manifest_to_db", _write_manifest)
    receipt = cli._prepare_live_schema_and_manifest(
        conn,
        init_schema=False,
        schema_ready=True,
        openmeteo_manifest=manifest,
        anchor_artifact_id=None,
    )
    conn.close()

    assert calls == [
        (
            manifest,
            {"root": cli.ROOT, "verify_artifact": False},
            True,
        )
    ]
    assert receipt.anchor_artifact_id == 17
    assert receipt.manifest_committed is True


def test_materialize_script_publishes_family_wake_after_commit(monkeypatch) -> None:
    import scripts.materialize_replacement_forecast_live as cli
    from src.runtime import reactor_wake

    published = []
    monkeypatch.setattr(
        reactor_wake,
        "publish_reactor_wake",
        lambda **kwargs: published.append(kwargs)
        or SimpleNamespace(wake_id="wake-1"),
    )
    request = _request()

    assert cli._publish_materialization_wake(request) is True
    assert published == [
        {
            "source": "replacement_forecast_materializer",
            "reason": "forecast_posterior_advanced",
            "forecast_families": (
                (
                    request.city,
                    request.target_date.isoformat(),
                    request.temperature_metric,
                ),
            ),
        }
    ]


def test_materialize_script_initial_compute_precedes_writer_lock(
    tmp_path, monkeypatch
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    db_path = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE frontier (value INTEGER NOT NULL)")
    conn.commit()
    trace: list[str] = []
    conn.set_trace_callback(trace.append)
    prepared = object()
    prepare_calls = 0
    witness_lock_states = []

    lock_held = False

    @contextmanager
    def writer_lock():
        nonlocal lock_held
        assert lock_held is False
        lock_held = True
        try:
            yield
        finally:
            lock_held = False

    def prepare(_conn, _request):
        nonlocal prepare_calls
        assert lock_held is False
        prepare_calls += 1
        return prepared

    def witness(_conn, value):
        assert value is prepared
        witness_lock_states.append(lock_held)
        return "stable-target"

    monkeypatch.setattr(cli, "prepare_replacement_forecast_live", prepare)
    monkeypatch.setattr(cli, "_target_dependency_witness", witness)
    monkeypatch.setattr(
        cli,
        "_revalidate_target_dependency_witness",
        lambda conn, value, _baseline: witness(conn, value),
    )
    monkeypatch.setattr(
        cli,
        "write_prepared_replacement_forecast_live",
        lambda _conn, value: (
            materializer_mod.ReplacementForecastMaterializeResult(
                status="READY",
                reason_codes=(),
                posterior_id=1,
                anchor_id=1,
                readiness_id="ready-1",
            )
            if lock_held and value is prepared
            else pytest.fail("write occurred without the writer lock")
        ),
    )

    result = cli._commit_from_read_snapshot(
        conn,
        SimpleNamespace(
            city="London",
            target_date=date(2026, 7, 19),
            temperature_metric="high",
        ),
        writer_lock=writer_lock,
    )
    conn.close()

    statements = [statement.upper() for statement in trace]
    assert result.ok is True
    assert prepare_calls == 1
    assert witness_lock_states == [False, True]
    assert statements.index("BEGIN") < statements.index("ROLLBACK")
    assert statements.index("ROLLBACK") < statements.index("BEGIN IMMEDIATE")


def test_materialize_script_releases_live_flock_while_sqlite_writer_is_busy(
    tmp_path, monkeypatch
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    db_path = tmp_path / "forecasts.db"
    reader = sqlite3.connect(db_path)
    holder = sqlite3.connect(db_path)
    reader.execute("PRAGMA journal_mode=WAL")
    reader.execute("CREATE TABLE frontier (value INTEGER NOT NULL)")
    reader.commit()
    reader.execute("PRAGMA busy_timeout = 4321")
    holder.execute("BEGIN IMMEDIATE")
    prepared = object()
    lock_held = False
    lock_durations: list[float] = []

    monkeypatch.setattr(cli, "_IMMEDIATE_BUSY_TIMEOUT_MS", 1)
    monkeypatch.setattr(cli, "_IMMEDIATE_RETRY_LIMIT", 3)
    monkeypatch.setattr(cli, "_IMMEDIATE_RETRY_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(cli, "prepare_replacement_forecast_live", lambda *_args: prepared)
    monkeypatch.setattr(cli, "_target_dependency_witness", lambda *_args: "stable")
    monkeypatch.setattr(
        cli,
        "_revalidate_target_dependency_witness",
        lambda *_args: "stable",
    )
    monkeypatch.setattr(
        cli,
        "write_prepared_replacement_forecast_live",
        lambda *_args: _ready_materialization_result(),
    )

    @contextmanager
    def writer_lock():
        nonlocal lock_held
        assert lock_held is False
        lock_held = True
        started = time.monotonic()
        try:
            yield
        finally:
            lock_durations.append(time.monotonic() - started)
            lock_held = False
            if len(lock_durations) == 1:
                holder.rollback()

    result = cli._commit_from_read_snapshot(
        reader,
        SimpleNamespace(
            city="London",
            target_date=date(2026, 7, 19),
            temperature_metric="high",
        ),
        writer_lock=writer_lock,
    )

    assert result.ok is True
    assert len(lock_durations) == 2
    assert lock_durations[0] < 0.1
    assert reader.execute("PRAGMA busy_timeout").fetchone()[0] == 4321
    reader.close()
    holder.close()


def test_materialize_schema_prelude_releases_live_flock_on_sqlite_contention(
    tmp_path, monkeypatch
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    db_path = tmp_path / "forecasts.db"
    reader = sqlite3.connect(db_path)
    holder = sqlite3.connect(db_path)
    reader.execute("PRAGMA journal_mode=WAL")
    reader.commit()
    reader.execute("PRAGMA busy_timeout = 7654")
    holder.execute("BEGIN IMMEDIATE")
    lock_durations: list[float] = []
    manifest = object()

    monkeypatch.setattr(cli, "_IMMEDIATE_BUSY_TIMEOUT_MS", 1)
    monkeypatch.setattr(cli, "_IMMEDIATE_RETRY_LIMIT", 3)
    monkeypatch.setattr(cli, "_IMMEDIATE_RETRY_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(cli, "write_manifest_to_db", lambda *_args, **_kwargs: 17)

    @contextmanager
    def writer_lock():
        started = time.monotonic()
        try:
            yield
        finally:
            lock_durations.append(time.monotonic() - started)
            if len(lock_durations) == 1:
                holder.rollback()

    receipt = cli._prepare_live_schema_and_manifest(
        reader,
        init_schema=False,
        schema_ready=True,
        openmeteo_manifest=manifest,
        anchor_artifact_id=None,
        writer_lock=writer_lock,
    )

    assert receipt.anchor_artifact_id == 17
    assert len(lock_durations) == 2
    assert lock_durations[0] < 0.1
    assert reader.execute("PRAGMA busy_timeout").fetchone()[0] == 7654
    reader.close()
    holder.close()


def test_materialize_writer_contention_exhaustion_is_retryable(
    tmp_path, monkeypatch
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    db_path = tmp_path / "forecasts.db"
    reader = sqlite3.connect(db_path)
    holder = sqlite3.connect(db_path)
    reader.execute("PRAGMA journal_mode=WAL")
    reader.commit()
    reader.execute("PRAGMA busy_timeout = 9876")
    holder.execute("BEGIN IMMEDIATE")
    lock_calls = 0

    monkeypatch.setattr(cli, "_IMMEDIATE_BUSY_TIMEOUT_MS", 1)
    monkeypatch.setattr(cli, "_IMMEDIATE_RETRY_LIMIT", 2)
    monkeypatch.setattr(cli, "_IMMEDIATE_RETRY_DELAY_SECONDS", 0.0)

    @contextmanager
    def writer_lock():
        nonlocal lock_calls
        lock_calls += 1
        yield

    with pytest.raises(
        cli.ReplacementForecastWriteDeferred,
        match="^REPLACEMENT_FORECAST_WRITE_DEFERRED$",
    ) as raised:
        with cli._immediate_writer_transaction(reader, writer_lock):
            pytest.fail("busy SQLite writer must prevent transaction entry")

    response = cli._error_response(raised.value)
    assert lock_calls == 2
    assert reader.in_transaction is False
    assert reader.execute("PRAGMA busy_timeout").fetchone()[0] == 9876
    assert response["reason_codes"] == ["REPLACEMENT_FORECAST_WRITE_DEFERRED"]
    holder.rollback()
    reader.close()
    holder.close()


def test_forecast_writer_lock_never_blocks_outside_bounded_retry(monkeypatch) -> None:
    import scripts.materialize_replacement_forecast_live as cli
    import src.state.db_writer_lock as lock_mod
    from src.state.db import ZEUS_FORECASTS_DB_PATH

    observed: dict[str, object] = {}

    @contextmanager
    def nonblocking_lock(db_path, write_class, *, blocking=True):
        observed.update(
            db_path=db_path,
            write_class=write_class,
            blocking=blocking,
        )
        yield

    monkeypatch.setattr(lock_mod, "db_writer_lock", nonblocking_lock)

    with cli._forecast_writer_lock():
        pass

    assert observed["db_path"] == ZEUS_FORECASTS_DB_PATH
    assert observed["write_class"] is lock_mod.WriteClass.LIVE
    assert observed["blocking"] is False


def test_materialize_transaction_body_busy_is_retryable() -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA busy_timeout = 2468")

    with pytest.raises(
        cli.ReplacementForecastWriteDeferred,
        match="^REPLACEMENT_FORECAST_WRITE_DEFERRED$",
    ):
        with cli._immediate_writer_transaction(conn, nullcontext):
            raise sqlite3.OperationalError("database is locked during commit")

    assert conn.in_transaction is False
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 2468
    conn.close()


def test_materialize_transaction_body_blocking_error_is_not_lock_contention() -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    lock_calls = 0

    @contextmanager
    def writer_lock():
        nonlocal lock_calls
        lock_calls += 1
        yield

    with pytest.raises(BlockingIOError, match="permanent body error"):
        with cli._immediate_writer_transaction(conn, writer_lock):
            raise BlockingIOError("permanent body error")

    assert lock_calls == 1
    assert conn.in_transaction is False
    conn.close()


def test_materialize_script_recomputes_when_snapshot_changes(
    tmp_path, monkeypatch
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    db_path = tmp_path / "forecasts.db"
    reader = sqlite3.connect(db_path)
    writer = sqlite3.connect(db_path)
    reader.execute("PRAGMA journal_mode=WAL")
    reader.execute("CREATE TABLE frontier (value INTEGER NOT NULL)")
    reader.commit()
    prepare_calls = []
    written_values: list[int] = []
    changed = False

    def _prepare(_conn, _request):
        prepare_calls.append(True)
        return object()

    def _witness(conn, _prepared):
        return int(conn.execute("SELECT COUNT(*) FROM frontier").fetchone()[0])

    @contextmanager
    def _writer_lock():
        nonlocal changed
        if not changed:
            writer.execute("INSERT INTO frontier (value) VALUES (1)")
            writer.commit()
            changed = True
        yield

    def _write(conn, _value):
        written_values.append(
            int(conn.execute("SELECT COUNT(*) FROM frontier").fetchone()[0])
        )
        return materializer_mod.ReplacementForecastMaterializeResult(
            status="READY",
            reason_codes=(),
            posterior_id=1,
            anchor_id=1,
            readiness_id="ready-1",
        )

    monkeypatch.setattr(cli, "prepare_replacement_forecast_live", _prepare)
    monkeypatch.setattr(cli, "_target_dependency_witness", _witness)
    monkeypatch.setattr(
        cli,
        "_revalidate_target_dependency_witness",
        lambda conn, value, _baseline: _witness(conn, value),
    )
    monkeypatch.setattr(cli, "write_prepared_replacement_forecast_live", _write)

    result = cli._commit_from_read_snapshot(reader, SimpleNamespace(
        city="London",
        target_date=date(2026, 7, 19),
        temperature_metric="high",
    ), writer_lock=_writer_lock)
    reader.close()
    writer.close()

    assert result.ok is True
    assert len(prepare_calls) == 2
    assert written_values == [1]


def _prepared_target_frontier(marker: object):
    request = _request(anchor_artifact_id=17)
    return materializer_mod.PreparedReplacementForecastMaterialization(
        request=request,
        metric="high",
        day0_ledger_frontier_identity=None,
        posterior=SimpleNamespace(
            live_eligible=True,
            dependency_payload={
                "baseline_b0": request.baseline_source_run_id,
                "openmeteo_ifs9_anchor": request.openmeteo_source_run_id,
                "current_ensemble_snapshot": marker,
            },
            dependency_hash=f"dependency-{marker}",
            source_cycle_time=request.source_cycle_time.isoformat(),
            available_at=request.openmeteo_source_available_at.isoformat(),
            posterior_config_hash="posterior-config",
            provenance_payload={
                "bayes_precision_fusion": {
                    "raw_model_forecast_ids": [marker],
                    "current_value_serving": {
                        "icon_global": {"raw_model_forecast_id": marker}
                    },
                    "current_evidence_shape": {
                        "snapshot_id": marker,
                        "shape_hash": f"shape-{marker}",
                    },
                }
            },
        ),
    )


@pytest.mark.parametrize(
    "fusion",
    (
        None,
        {},
        {"current_value_serving": {}},
        {"current_value_serving": {"icon_global": {}}},
        {"current_value_serving": {"icon_global": {"raw_model_forecast_id": "bad"}}},
        {"current_value_serving": {"icon_global": {"raw_model_forecast_id": 1.5}}},
        {"current_value_serving": {"icon_global": {"raw_model_forecast_id": True}}},
    ),
)
@pytest.mark.usefixtures("_target_frontier_native_surfaces")
def test_target_witness_allows_live_ineligible_missing_serving_provenance(
    fusion,
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    prepared = _prepared_target_frontier(101)
    prepared.posterior.live_eligible = False
    prepared.posterior.provenance_payload = (
        {} if fusion is None else {"bayes_precision_fusion": fusion}
    )

    witness = cli._target_dependency_witness(conn, prepared)
    conn.close()

    assert witness.prepared_provider_row_ids == ()
    assert witness.prepared_snapshot_id is None


@pytest.mark.parametrize(
    "fusion",
    (
        None,
        {},
        {"current_value_serving": {}},
        {"current_value_serving": {"icon_global": {}}},
        {"current_value_serving": {"icon_global": {"raw_model_forecast_id": "bad"}}},
        {"current_value_serving": {"icon_global": {"raw_model_forecast_id": 1.5}}},
        {"current_value_serving": {"icon_global": {"raw_model_forecast_id": True}}},
    ),
)
@pytest.mark.usefixtures("_target_frontier_native_surfaces")
def test_target_witness_rejects_live_eligible_missing_serving_provenance(
    fusion,
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    prepared = _prepared_target_frontier(101)
    prepared.posterior.provenance_payload = (
        {} if fusion is None else {"bayes_precision_fusion": fusion}
    )

    with pytest.raises(
        cli._TargetDependencyWitnessUnavailable,
        match="current value serving witness unavailable",
    ):
        cli._target_dependency_witness(conn, prepared)
    conn.close()


@pytest.mark.usefixtures("_target_frontier_native_surfaces")
def test_commit_returns_typed_blocked_for_ineligible_missing_serving(
    monkeypatch,
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    prepared = _prepared_target_frontier(101)
    prepared.posterior.live_eligible = False
    prepared.posterior.provenance_payload = {}
    blocked = materializer_mod.ReplacementForecastMaterializeResult(
        status="BLOCKED",
        reason_codes=("PREDICTIVE_SIGMA:MISSING",),
        posterior_id=None,
        anchor_id=17,
        readiness_id=None,
    )
    monkeypatch.setattr(
        cli, "prepare_replacement_forecast_live", lambda *_args: prepared
    )
    monkeypatch.setattr(
        cli, "write_prepared_replacement_forecast_live", lambda *_args: blocked
    )

    result = cli._commit_from_read_snapshot(
        conn, prepared.request, writer_lock=nullcontext
    )
    conn.close()

    assert result == blocked


def _ready_materialization_result():
    return materializer_mod.ReplacementForecastMaterializeResult(
        status="READY",
        reason_codes=(),
        posterior_id=1,
        anchor_id=1,
        readiness_id="ready-1",
    )


def _blocked_materialization_result():
    return materializer_mod.ReplacementForecastMaterializeResult(
        status="BLOCKED",
        reason_codes=("TARGET_DEPENDENCY_UNAVAILABLE",),
        posterior_id=None,
        anchor_id=None,
        readiness_id=None,
    )


def _create_target_frontier_tables(conn: sqlite3.Connection) -> None:
    """SQL/locking fixture, not a licensed Shanghai forecast certificate.

    TEST_ONLY_SYNTHETIC_EXTERNAL_CONDITION supplies complete controlled ICON
    response/static bytes through the ordinary writer at the original June cut.
    """
    conn.executescript(
        f"""
        CREATE TABLE source_run (
            source_run_id TEXT PRIMARY KEY,
            source_id TEXT, track TEXT, release_calendar_key TEXT,
            source_cycle_time TEXT,
            source_available_at TEXT, fetch_finished_at TEXT, captured_at TEXT,
            imported_at TEXT,
            expected_count INTEGER, observed_count INTEGER,
            completeness_status TEXT, partial_run INTEGER, raw_payload_hash TEXT,
            manifest_hash TEXT, status TEXT, reason_code TEXT
        );
        CREATE TABLE source_run_coverage (
            coverage_id TEXT PRIMARY KEY,
            source_run_id TEXT, source_id TEXT, release_calendar_key TEXT,
            track TEXT, city TEXT, target_local_date TEXT,
            temperature_metric TEXT, expected_members INTEGER,
            observed_members INTEGER, expected_steps_json TEXT,
            observed_steps_json TEXT, snapshot_ids_json TEXT,
            completeness_status TEXT, readiness_status TEXT,
            computed_at TEXT, expires_at TEXT, recorded_at TEXT
        );
        CREATE INDEX idx_source_run_coverage_test_run
            ON source_run_coverage(source_run_id, city, target_local_date,
                                   temperature_metric);
        CREATE TABLE raw_forecast_artifacts (
            artifact_id INTEGER PRIMARY KEY,
            source_id TEXT, product_id TEXT, data_version TEXT,
            source_cycle_time TEXT, source_available_at TEXT, captured_at TEXT,
            sha256 TEXT, byte_size INTEGER
        );
        CREATE TABLE raw_model_forecasts (
            raw_model_forecast_id INTEGER PRIMARY KEY,
            model TEXT, city TEXT, target_date TEXT, metric TEXT,
            source_cycle_time TEXT, source_available_at TEXT, captured_at TEXT,
            lead_days INTEGER, forecast_value_c REAL, endpoint TEXT,
            recorded_at TEXT
        );
        CREATE TABLE ensemble_snapshots (
            snapshot_id INTEGER PRIMARY KEY,
            city TEXT, target_date TEXT, temperature_metric TEXT,
            source_id TEXT, model_version TEXT, authority TEXT,
            source_run_id TEXT,
            causality_status TEXT, boundary_ambiguous INTEGER,
            forecast_window_attribution_status TEXT,
            contributes_to_target_extrema INTEGER,
            source_cycle_time TEXT, issue_time TEXT,
            source_available_at TEXT, available_at TEXT,
            members_json TEXT, members_unit TEXT, dataset_id TEXT,
            provenance_json TEXT
        );
        CREATE TABLE unrelated_writer (value INTEGER);
        INSERT INTO source_run VALUES (
            'b0-run', 'ecmwf_open_data', 'mx2t6_high',
            'ecmwf_open_data:mx2t6_high:short',
            '2026-06-06T00:00:00+00:00', '2026-06-06T02:00:00+00:00',
            '2026-06-06T02:00:00+00:00', '2026-06-06T02:00:00+00:00',
            '2026-06-06T02:00:00+00:00',
            51, 51, 'COMPLETE', 0,
            'b0-raw', 'b0-manifest', 'SUCCESS', NULL
        );
        INSERT INTO source_run VALUES (
            'om9-run', 'openmeteo', 'ifs9_high',
            'openmeteo:ifs9_high',
            '2026-06-06T00:00:00+00:00', '2026-06-06T03:00:00+00:00',
            '2026-06-06T03:00:00+00:00', '2026-06-06T03:00:00+00:00',
            '2026-06-06T03:00:00+00:00',
            1, 1, 'COMPLETE', 0,
            'om9-raw', 'om9-manifest', 'SUCCESS', NULL
        );
        INSERT INTO raw_forecast_artifacts VALUES (
            17, 'openmeteo', 'ifs9', 'anchor-v1',
            '2026-06-06T00:00:00+00:00', '2026-06-06T03:00:00+00:00',
            '2026-06-06T03:00:00+00:00', 'anchor-sha-a', 10
        );
        INSERT INTO raw_model_forecasts (
            raw_model_forecast_id, model, city, target_date, metric,
            source_cycle_time, source_available_at, captured_at,
            lead_days, forecast_value_c, endpoint
        ) VALUES (
            101, 'icon_global', 'Shanghai', '2026-06-07', 'high',
            '2026-06-06T00:00:00+00:00', '2026-06-06T03:00:00+00:00',
            '2026-06-06T03:00:00+00:00', 1, 27.0, 'single_runs'
        );
        INSERT INTO ensemble_snapshots VALUES (
            101, 'Shanghai', '2026-06-07', 'high',
            'ecmwf_open_data', 'ecmwf_ens', 'VERIFIED', 'b0-run', 'OK', 0,
            'FULLY_INSIDE_TARGET_LOCAL_DAY', 1,
            '2026-06-06T00:00:00+00:00', '2026-06-06T00:00:00+00:00',
            '2026-06-06T03:00:00+00:00', '2026-06-06T03:00:00+00:00',
            '[20.0,21.0]', 'degC', '{_current_baseline_data_version("high")}', '{{}}'
        );
        """
    )
    conn.execute(
        "UPDATE ensemble_snapshots SET provenance_json = ? WHERE snapshot_id = 101",
        (_fixture_ens_surface_provenance(),),
    )
    _set_target_frontier_coverage(conn, snapshot_id=101)
    # The old minimal DDL predates physical writer identity. Upgrade the actual
    # tables using the normal schema and leave marker IDs/anchor17 untouched.
    canonical = _conn()
    artifact_columns = {row[1] for row in conn.execute("PRAGMA table_info(raw_forecast_artifacts)")}
    for column in canonical.execute("PRAGMA table_info(raw_forecast_artifacts)"):
        if column[1] not in artifact_columns:
            conn.execute(f"ALTER TABLE raw_forecast_artifacts ADD COLUMN {column[1]} {column[2]}")
    canonical.close()
    conn.execute("""CREATE UNIQUE INDEX fixture_raw_artifact_identity ON raw_forecast_artifacts(
        source_id,product_id,data_version,source_cycle_time,sha256)""")
    from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema
    ensure_replacement_forecast_live_schema(conn)
    _qualify_raw_fixture_rows(conn)
    from src.data.replacement_current_value_serving import read_current_instrument_values
    request = _prepared_target_frontier(101).request
    served = read_current_instrument_values(conn, city=request.city, metric="high",
        target_date=request.target_date.isoformat(), source_cycle_time_iso=request.source_cycle_time.isoformat(),
        decision_time_iso=request.computed_at.isoformat(), include_station_sources=True)
    assert set(served) == {"icon_global"}
    assert served["icon_global"].raw_model_forecast_id == 101
    assert served["icon_global"].value_c == 27.0
    assert served["icon_global"].physical_response["model_surface_witness"]["status"] == "VERIFIED"
    assert materializer_mod.read_current_evidence_snapshot_id(conn, request, metric="high") == 101
    conn.commit()


def _set_target_frontier_coverage(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    city: str = "Shanghai",
    coverage_id: str = "b0-coverage-current",
    source_run_id: str = "b0-run",
    track: str = "mx2t6_high",
    release_key: str = "ecmwf_open_data:mx2t6_high:short",
) -> None:
    """Make this fixture snapshot explicitly live-eligible for its target."""
    conn.execute(
        """
        INSERT OR REPLACE INTO source_run_coverage VALUES (
            ?, ?, 'ecmwf_open_data', ?, ?, ?,
            '2026-06-07', 'high', 51, 51, '[0,3,6]', '[0,3,6]', ?,
            'COMPLETE', 'LIVE_ELIGIBLE',
            '2026-06-06T03:10:00+00:00', '2026-06-06T06:00:00+00:00',
            '2026-06-06T03:10:00+00:00'
        )
        """,
        (coverage_id, source_run_id, release_key, track, city, f"[{snapshot_id}]"),
    )


@pytest.mark.usefixtures("_target_frontier_native_surfaces")
def test_target_dependency_witness_is_bounded_to_exact_target_rows() -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    prepared = _prepared_target_frontier(101)

    baseline = cli._target_dependency_witness(conn, prepared)
    assert baseline.prepared_snapshot_id == 101
    assert baseline.prepared_shape_id == "shape-101"
    conn.execute("INSERT INTO unrelated_writer VALUES (1)")
    assert cli._target_dependency_witness(conn, prepared) == baseline

    conn.execute(
        "UPDATE raw_forecast_artifacts SET sha256 = 'anchor-sha-b' WHERE artifact_id = 17"
    )
    assert cli._target_dependency_witness(conn, prepared) != baseline
    conn.execute(
        "UPDATE raw_forecast_artifacts SET sha256 = 'anchor-sha-a' WHERE artifact_id = 17"
    )
    conn.execute("INSERT INTO unrelated_writer VALUES (2)")
    assert cli._revalidate_target_dependency_witness(
        conn, prepared, baseline
    ) == baseline

    conn.execute(
        """
        INSERT INTO raw_model_forecasts (
            raw_model_forecast_id, model, city, target_date, metric,
            source_cycle_time, source_available_at, captured_at,
            lead_days, forecast_value_c, endpoint
        ) VALUES (
            102, 'icon_global', 'Shanghai', '2026-06-07', 'high',
            '2026-06-06T01:00:00+00:00', '2026-06-06T03:30:00+00:00',
            '2026-06-06T03:30:00+00:00', 1, 26.0, 'single_runs'
        )
        """
    )
    changed_provider = cli._revalidate_target_dependency_witness(
        conn, prepared, baseline
    )
    assert changed_provider != baseline
    assert changed_provider.provider_family_latest_id == 102
    conn.execute("DELETE FROM raw_model_forecasts WHERE raw_model_forecast_id = 102")

    conn.execute(
        f"""
        INSERT INTO ensemble_snapshots VALUES (
            102, 'Shanghai', '2026-06-07', 'high',
            'ecmwf_open_data', 'ecmwf_ens', 'VERIFIED', 'b0-run', 'OK', 0,
            'FULLY_INSIDE_TARGET_LOCAL_DAY', 1,
            '2026-06-06T00:00:00+00:00', '2026-06-06T00:00:00+00:00',
            '2026-06-06T03:30:00+00:00', '2026-06-06T03:30:00+00:00',
            '[19.0,22.0]', 'degC', '{_current_baseline_data_version("high")}', '{{}}'
        )
        """
    )
    conn.execute("UPDATE ensemble_snapshots SET provenance_json=? WHERE snapshot_id=102",
                 (_fixture_ens_surface_provenance(),))
    _set_target_frontier_coverage(conn, snapshot_id=102)
    with pytest.raises(cli._TargetDependencyWitnessUnavailable):
        cli._revalidate_target_dependency_witness(conn, prepared, baseline)
    conn.close()


@pytest.mark.usefixtures("_target_frontier_native_surfaces")
def test_materialize_script_ignores_unrelated_data_version_changes(
    tmp_path, monkeypatch
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    db_path = tmp_path / "forecasts.db"
    reader = sqlite3.connect(db_path)
    writer = sqlite3.connect(db_path)
    reader.execute("PRAGMA journal_mode=WAL")
    _create_target_frontier_tables(reader)
    reader.commit()
    prepared = _prepared_target_frontier(101)
    prepare_calls = []
    written = []
    initial_data_version = int(reader.execute("PRAGMA data_version").fetchone()[0])
    lock_entries = 0

    @contextmanager
    def writer_lock():
        nonlocal lock_entries
        lock_entries += 1
        if lock_entries == 1:
            writer.execute("INSERT INTO unrelated_writer (value) VALUES (1)")
            writer.commit()
        yield

    def _prepare(_conn, _request):
        prepare_calls.append(True)
        return prepared

    monkeypatch.setattr(cli, "prepare_replacement_forecast_live", _prepare)
    monkeypatch.setattr(
        cli,
        "write_prepared_replacement_forecast_live",
        lambda _conn, value: written.append(value) or _ready_materialization_result(),
    )

    result = cli._commit_from_read_snapshot(
        reader, prepared.request, writer_lock=writer_lock
    )
    current_data_version = int(reader.execute("PRAGMA data_version").fetchone()[0])
    reader.close()
    writer.close()

    assert result.ok is True
    assert current_data_version > initial_data_version
    assert len(prepare_calls) == 1
    assert lock_entries == 1
    assert written == [prepared]


@pytest.mark.parametrize(
    "kind",
    ("source_run_available_at", "source_run_disappears"),
)
def test_materialize_script_refuses_changed_or_missing_target_source_run(
    monkeypatch, kind: str
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    prepared = _prepared_target_frontier(101)
    prepare_results = iter((prepared, _blocked_materialization_result()))
    baseline_witness = ("present", "fetch-finished-a")
    changed_witness = (
        "missing" if kind == "source_run_disappears" else "present",
        None
        if kind == "source_run_disappears"
        else "fetch-finished-b",
    )
    prepare_calls = []
    writes = []

    def _prepare(*_args):
        prepare_calls.append(True)
        return next(prepare_results)

    monkeypatch.setattr(cli, "prepare_replacement_forecast_live", _prepare)
    monkeypatch.setattr(
        cli, "_target_dependency_witness", lambda *_args: baseline_witness
    )
    monkeypatch.setattr(
        cli,
        "_revalidate_target_dependency_witness",
        lambda *_args: changed_witness,
    )
    monkeypatch.setattr(
        cli,
        "write_prepared_replacement_forecast_live",
        lambda *_args: writes.append(True) or _ready_materialization_result(),
    )

    result = cli._commit_from_read_snapshot(
        conn, prepared.request, writer_lock=nullcontext
    )
    conn.close()

    assert result.status == "BLOCKED"
    assert len(prepare_calls) == 2
    assert writes == []


def test_source_run_witness_distinguishes_missing_from_present_empty() -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE source_run (source_run_id TEXT PRIMARY KEY, fetch_finished_at TEXT)"
    )
    columns = cli._table_columns(conn, "source_run")
    missing_rows = cli._exact_rows_witness(
        conn,
        table="source_run",
        pk="source_run_id",
        ids=("run-1",),
        columns=columns,
    )
    conn.execute(
        "INSERT INTO source_run (source_run_id, fetch_finished_at) VALUES (?, NULL)",
        ("run-1",),
    )
    present_empty_rows = cli._exact_rows_witness(
        conn,
        table="source_run",
        pk="source_run_id",
        ids=("run-1",),
        columns=columns,
    )
    conn.execute(
        "UPDATE source_run SET fetch_finished_at = ? WHERE source_run_id = ?",
        ("2026-08-03T01:00:00+00:00", "run-1"),
    )
    present_rows = cli._exact_rows_witness(
        conn,
        table="source_run",
        pk="source_run_id",
        ids=("run-1",),
        columns=columns,
    )
    requested = (("run-1", "request-available"),)
    missing = cli._source_run_states(missing_rows, requested=requested)[0]
    present_empty = cli._source_run_states(
        present_empty_rows, requested=requested
    )[0]
    present = cli._source_run_states(present_rows, requested=requested)[0]
    conn.close()

    assert missing.state == "missing"
    assert present_empty.state == "present_empty"
    assert present.state == "present"
    assert present.fetch_finished_at == "2026-08-03T01:00:00+00:00"


@pytest.mark.usefixtures("_target_frontier_native_surfaces")
def test_target_witness_detects_same_fetch_time_source_run_replacement() -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    prepared = _prepared_target_frontier(101)
    baseline = cli._target_dependency_witness(conn, prepared)
    conn.execute(
        """
        UPDATE source_run
           SET observed_count = 50,
               completeness_status = 'PARTIAL',
               raw_payload_hash = 'b0-replaced',
               status = 'PARTIAL',
               reason_code = 'ONE_MEMBER_MISSING'
         WHERE source_run_id = 'b0-run'
        """
    )
    conn.execute(
        """
        UPDATE source_run_coverage
           SET completeness_status = 'PARTIAL', readiness_status = 'BLOCKED'
         WHERE source_run_id = 'b0-run'
        """
    )

    with pytest.raises(cli._TargetDependencyWitnessUnavailable):
        cli._revalidate_target_dependency_witness(conn, prepared, baseline)
    conn.close()


@pytest.mark.parametrize(
    "delete_sql,raises",
    (
        ("DELETE FROM source_run WHERE source_run_id = 'b0-run'", True),
        ("DELETE FROM raw_forecast_artifacts WHERE artifact_id = 17", True),
        ("DELETE FROM raw_model_forecasts WHERE raw_model_forecast_id = 101", True),
        ("DELETE FROM ensemble_snapshots WHERE snapshot_id = 101", True),
    ),
)
@pytest.mark.usefixtures("_target_frontier_native_surfaces")
def test_target_witness_refuses_disappeared_exact_dependency(
    delete_sql: str, raises: bool
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    prepared = _prepared_target_frontier(101)
    baseline = cli._target_dependency_witness(conn, prepared)
    conn.execute(delete_sql)

    if raises:
        with pytest.raises(cli._TargetDependencyWitnessUnavailable):
            cli._revalidate_target_dependency_witness(conn, prepared, baseline)
    else:
        changed = cli._revalidate_target_dependency_witness(
            conn, prepared, baseline
        )
        assert changed != baseline
        assert changed.source_run_states[0].state == "missing"
    conn.close()


@pytest.mark.usefixtures("_target_frontier_native_surfaces")
def test_shared_frontier_helpers_match_materializer_selectors() -> None:
    from src.data.replacement_current_value_serving import (
        current_value_serving_schema,
        read_current_instrument_frontier_identity,
        read_current_instrument_values,
    )
    from src.data.replacement_forecast_materializer import (
        read_current_evidence_snapshot_id,
        read_current_evidence_snapshot_identity,
    )

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    prepared = _prepared_target_frontier(101)
    request = prepared.request
    served = read_current_instrument_values(
        conn,
        city=request.city,
        metric=prepared.metric,
        target_date=request.target_date.isoformat(),
        source_cycle_time_iso=request.source_cycle_time.isoformat(),
        decision_time_iso=request.computed_at.isoformat(),
        include_station_sources=True,
    )
    bounded = read_current_instrument_frontier_identity(
        conn,
        city=request.city,
        metric=prepared.metric,
        target_date=request.target_date.isoformat(),
        decision_time_iso=request.computed_at.isoformat(),
        models=tuple(served),
        schema=current_value_serving_schema(conn),
    )
    snapshot = read_current_evidence_snapshot_identity(
        conn, request, metric=prepared.metric
    )
    snapshot_id = read_current_evidence_snapshot_id(
        conn, request, metric=prepared.metric
    )
    conn.close()

    assert bounded == tuple(
        sorted((model, value.raw_model_forecast_id) for model, value in served.items())
    )
    assert snapshot is not None
    assert snapshot_id == snapshot.snapshot_id == 101


@pytest.mark.usefixtures("_target_frontier_native_surfaces")
def test_selected_ens_proof_is_required_for_identity_and_shape() -> None:
    """An indexed snapshot id cannot bypass the same selected-row land proof."""
    from src.data.replacement_forecast_materializer import (
        read_current_evidence_snapshot_id,
        read_current_evidence_snapshot_identity,
    )

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    request = _prepared_target_frontier(101).request
    selected = read_current_evidence_snapshot_identity(conn, request, metric="high")
    assert selected is not None
    assert selected.grid_surface_evidence_revision == "ecmwf_ens_land_cell_selection_v1"
    assert len(selected.grid_surface_evidence_identity_hash) == 64
    proof = json.loads(_fixture_ens_surface_provenance())
    proof["grid_surface_evidence"]["mask_source_fetched_at"] = "2026-06-06T03:30:00+00:00"
    conn.execute("UPDATE ensemble_snapshots SET provenance_json=? WHERE snapshot_id=101",
                 (json.dumps(proof),))
    assert read_current_evidence_snapshot_identity(conn, request, metric="high") is None
    assert read_current_evidence_snapshot_id(conn, request, metric="high") is None
    proof["grid_surface_evidence"]["mask_source_fetched_at"] = "2026-06-06T03:00:00+00:00"
    conn.execute("UPDATE ensemble_snapshots SET provenance_json=? WHERE snapshot_id=101",
                 (json.dumps(proof),))
    assert read_current_evidence_snapshot_identity(conn, request, metric="high") is not None
    assert read_current_evidence_snapshot_id(conn, request, metric="high") == 101
    proof = json.loads(_fixture_ens_surface_provenance())
    proof["grid_surface_evidence"]["selected_land_fraction"] = 0.2
    conn.execute("UPDATE ensemble_snapshots SET provenance_json=? WHERE snapshot_id=101",
                 (json.dumps(proof),))
    assert read_current_evidence_snapshot_identity(conn, request, metric="high") is None
    assert read_current_evidence_snapshot_id(conn, request, metric="high") is None
    conn.execute("UPDATE ensemble_snapshots SET provenance_json=? WHERE snapshot_id=101",
                 (_fixture_ens_surface_provenance(),))
    proof["grid_surface_evidence"]["selected_land_fraction"] = 0.9
    proof["grid_surface_evidence"]["mask_source_cycle_time"] = "2026-06-06T06:00:00+00:00"
    conn.execute("UPDATE ensemble_snapshots SET provenance_json=? WHERE snapshot_id=101",
                 (json.dumps(proof),))
    assert read_current_evidence_snapshot_id(conn, request, metric="high") is None
    conn.close()


@pytest.mark.usefixtures("_target_frontier_native_surfaces")
def test_materialized_shape_binds_verified_selected_ens_grid_hash() -> None:
    from src.data.replacement_forecast_materializer import _read_current_evidence_shape

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    request = _prepared_target_frontier(101).request
    conn.execute("UPDATE ensemble_snapshots SET members_json=? WHERE snapshot_id=101",
                 (json.dumps([20.0 + index * .05 for index in range(51)]),))
    kwargs = {
        "metric": "high",
        "provider_values_c": {"icon_global": 21.0, "ukmo_global_deterministic_10km": 22.0},
        "provider_weights": {"icon_global": .6, "ukmo_global_deterministic_10km": .4},
        "center_c": 21.5,
        "provider_cycles": {
            "icon_global": "2026-06-06T00:00:00+00:00",
            "ukmo_global_deterministic_10km": "2026-06-06T00:00:00+00:00",
        },
    }
    first = _read_current_evidence_shape(conn, request, **kwargs)
    assert first is not None
    assert first.grid_surface_evidence_revision == "ecmwf_ens_land_cell_selection_v1"
    provenance = json.loads(_fixture_ens_surface_provenance())
    provenance["grid_surface_evidence"]["mask_source_fetched_at"] = "2026-06-06T03:30:00+00:00"
    conn.execute("UPDATE ensemble_snapshots SET provenance_json=? WHERE snapshot_id=101",
                 (json.dumps(provenance),))
    assert _read_current_evidence_shape(conn, request, **kwargs) is None
    provenance = json.loads(_fixture_ens_surface_provenance())
    provenance["grid_surface_evidence"]["mask_sha256"] = "c" * 64
    conn.execute("UPDATE ensemble_snapshots SET provenance_json=? WHERE snapshot_id=101",
                 (json.dumps(provenance),))
    second = _read_current_evidence_shape(conn, request, **kwargs)
    assert second is not None
    assert first.predictive_sigma_c == second.predictive_sigma_c
    assert first.grid_surface_evidence_identity_hash != second.grid_surface_evidence_identity_hash
    assert first.shape_hash != second.shape_hash
    conn.close()


def _normal_hko_cli_inputs(tmp_path, monkeypatch):
    """Generic CLI admission inputs, not a Shanghai or full-posterior license."""
    from tests.test_config import _official_hko_registry
    from src.config import runtime_cities_by_name
    from src.data.station_ground_evidence import archive_station_ground_evidence

    _official_hko_registry(tmp_path, monkeypatch)
    test_state = tmp_path / "isolated-state"
    test_state.mkdir()
    forecast_db = test_state / "zeus-forecasts.db"
    conn = sqlite3.connect(forecast_db)
    conn.row_factory = sqlite3.Row
    apply_canonical_schema(conn, forecast_tables=True)
    _create_readiness_state(conn)
    conn.commit()
    archived = archive_station_ground_evidence(forecast_db, ["Hong Kong"])
    assert archived["status"] == "GROUND_SOURCE_ARCHIVED", archived
    sqlite3.connect(test_state / "zeus-world.db").close()
    raw = json.loads(_hko_raw_openmeteo_bytes())
    raw.pop("_zeus_current_target_scope")
    scoped = {**raw, "_zeus_current_target_scope": {
        "city": "Hong Kong", "target_date": "2026-10-01", "metric": "high",
    }}
    sealed = (json.dumps(scoped, indent=2, sort_keys=True, default=str)+"\n").encode()
    guard = _hko_precision_guard(decision_at=_hko_dt(4), raw_payload_bytes=sealed)
    metadata_path = tmp_path / "precision.json"
    metadata_path.write_text(json.dumps(asdict(guard.metadata), default=str), encoding="utf-8")
    city = runtime_cities_by_name()["Hong Kong"]
    seed = {
        "city": city.name, "city_id": city.name, "city_timezone": city.timezone,
        "target_date": "2026-10-01", "temperature_metric": "high",
        "source_cycle_time": _hko_dt(0).isoformat(), "computed_at": _hko_dt(4).isoformat(),
        "expires_at": _hko_dt(6).isoformat(),
        "baseline_source_run_id": "b0-run", "baseline_data_version": _current_baseline_data_version("high"),
        "baseline_source_available_at": _hko_dt(2).isoformat(),
        "openmeteo_source_run_id": "om9-run", "openmeteo_source_available_at": _hko_dt(3).isoformat(),
        "latitude": city.lat, "longitude": city.lon,
        "precision_metadata_json": str(metadata_path),
        "bins": [{"bin_id": "warm", "lower_c": 20.0, "upper_c": 30.0, "center_c": 25.0}],
    }
    return conn, raw, sealed, seed


@pytest.mark.usefixtures("_hko_source_surface")
def test_direct_cli_seals_one_source_artifact_and_refuses_old_proof_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generic CLI seal/precision admission; no forecast-q or Shanghai license."""
    import scripts.materialize_replacement_forecast_live as cli

    conn, raw, sealed, seed = _normal_hko_cli_inputs(tmp_path, monkeypatch)
    metadata_path = Path(seed["precision_metadata_json"])
    input_path = tmp_path / "seed.json"
    input_path.write_text(json.dumps(seed), encoding="utf-8")
    monkeypatch.setattr(cli, "fetch_openmeteo_ecmwf_ifs9_anchor_payload", lambda _request: dict(raw))
    captured: list[bytes] = []

    def checked_dry_run(_conn, request):
        assert _conn is conn
        captured.append(request.openmeteo_raw_payload_bytes)
        reasons = materializer_mod._precision_guard_block_reason(request, _conn)
        return materializer_mod.ReplacementForecastMaterializeResult(
            status="BLOCKED" if reasons else "READY", reason_codes=reasons,
            posterior_id=None, anchor_id=None, readiness_id=None,
        )

    monkeypatch.setattr(cli, "_dry_run_from_read_snapshot", checked_dry_run)
    code, response = cli._materialize(input_path, commit=False, init_schema=False, conn=conn)
    assert code == 0, response
    assert response["reason_codes"] == []
    assert captured == [sealed]

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["source_geometry_proof"]["raw_payload_sha256"] = "f" * 64
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    code, response = cli._materialize(input_path, commit=False, init_schema=False, conn=conn)
    conn.close()
    assert code == 1
    assert response["reason_codes"] == ["OM9_PRECISION_GUARD_NOT_LIVE_PASS", "OM9_SOURCE_RESPONSE_IDENTITY_MISMATCH"]
    assert captured == [sealed] * 2


@pytest.mark.usefixtures("_hko_source_surface")
def test_cli_anchor_carries_its_own_om9_cycle_not_the_carrier_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The anchor natural key must name the artifact's own run.

    Seed discovery points openmeteo_anchor_artifact_id at a newer OM9 run than the
    ENS carrier cycle.  Stamping the carrier cycle on the anchor made
    _insert_anchor return the older run's anchor row, so the public replay saw an
    anchor_id whose artifact differs from the provenance artifact and refused
    every such posterior.
    """
    import scripts.materialize_replacement_forecast_live as cli

    conn, raw, _sealed, seed = _normal_hko_cli_inputs(tmp_path, monkeypatch)
    om9_cycle = _hko_dt(0) + timedelta(hours=6)
    seed = {**seed, "source_cycle_time": _hko_dt(0).isoformat(),
            "openmeteo_source_cycle_time": om9_cycle.isoformat()}
    input_path = tmp_path / "seed.json"
    input_path.write_text(json.dumps(seed), encoding="utf-8")
    runs: list[object] = []

    def fetch(request):
        runs.append(request.run)
        return dict(raw)

    monkeypatch.setattr(cli, "fetch_openmeteo_ecmwf_ifs9_anchor_payload", fetch)
    seen: list[object] = []

    def capture(_conn, request):
        seen.append((request.openmeteo_anchor.source_cycle_time, request.source_cycle_time))
        return materializer_mod.ReplacementForecastMaterializeResult(
            status="READY", reason_codes=(), posterior_id=None, anchor_id=None, readiness_id=None,
        )

    monkeypatch.setattr(cli, "_dry_run_from_read_snapshot", capture)
    cli._materialize(input_path, commit=False, init_schema=False, conn=conn)
    conn.close()
    assert seen == [(om9_cycle, _hko_dt(0))]
    assert len(runs) == 1 and runs[0].replace(tzinfo=timezone.utc) == om9_cycle


@pytest.mark.parametrize("metric", ("high", "low"))
def test_current_ensemble_requires_exact_complete_target_coverage(
    metric: str,
) -> None:
    from src.data.replacement_forecast_materializer import (
        read_current_evidence_snapshot_identity,
    )
    from src.data.replacement_input_hwm import latest_eligible_ensemble_input_cycle

    conn = _conn()
    _ensure_source_run_table(conn)
    _ensure_source_run_coverage_table(conn)
    track = f"m{'x' if metric == 'high' else 'n'}2t6_{metric}_short_horizon"
    release_key = f"ecmwf_open_data:{track}"
    request = replace(
        _request(),
        temperature_metric=metric,
        baseline_data_version=_current_baseline_data_version(metric),
    )

    def write_run(
        *, status: str, completeness: str, partial: bool, imported_at: datetime
    ) -> None:
        observed_count = 3 if partial else 48
        write_source_run(
            conn,
            source_run_id="ens-run",
            source_id="ecmwf_open_data",
            track=track,
            release_calendar_key=release_key,
            source_cycle_time=_dt(0),
            source_available_at=_dt(2),
            fetch_finished_at=imported_at,
            captured_at=imported_at,
            imported_at=imported_at,
            status=status,
            completeness_status=completeness,
            partial_run=partial,
            expected_steps_json=list(range(3, 145, 3)),
            observed_steps_json=list(range(3, observed_count + 1, 3)),
            expected_count=48,
            observed_count=observed_count,
            data_version=_current_baseline_data_version(metric),
        )

    # A transport-complete run without this exact target proof must not elect
    # the snapshot.  The 18Z D+5 incident had this shape: the run was SUCCESS
    # while the target needed 162/165h and its coverage was blocked.
    write_run(status="SUCCESS", completeness="COMPLETE", partial=False, imported_at=_dt(3))
    conn.execute(
        f"""
        INSERT INTO ensemble_snapshots (
            snapshot_id, city, target_date, temperature_metric,
            physical_quantity, observation_field, issue_time, available_at,
            fetch_time, lead_hours, members_json, model_version, dataset_id,
            source_id, source_run_id, source_cycle_time, source_available_at,
            authority, causality_status, boundary_ambiguous,
            forecast_window_attribution_status, contributes_to_target_extrema,
            members_unit
        ) VALUES (
            101, 'Shanghai', '2026-06-07', '{metric}',
            'temperature_{'max' if metric == 'high' else 'min'}', '{metric}_temp', '2026-06-06T00:00:00+00:00',
            '2026-06-06T03:00:00+00:00', '2026-06-06T03:00:00+00:00', 24,
            '[20.0,21.0]', 'ecmwf_ens',
            '{_current_baseline_data_version(metric)}', 'ecmwf_open_data',
            'ens-run', '2026-06-06T00:00:00+00:00',
            '2026-06-06T03:00:00+00:00', 'VERIFIED', 'OK', 0,
            'FULLY_INSIDE_TARGET_LOCAL_DAY', 1, 'degC'
        )
        """
    )
    conn.execute("UPDATE ensemble_snapshots SET provenance_json=? WHERE snapshot_id=101",
                 (_fixture_ens_surface_provenance(),))

    def selected() -> tuple[object | None, datetime | None]:
        return (
            read_current_evidence_snapshot_identity(conn, request, metric=metric),
            latest_eligible_ensemble_input_cycle(
                conn,
                city=request.city,
                target_date=request.target_date,
                metric=metric,
                decision_time=request.computed_at,
            ),
        )

    assert selected() == (None, None)

    conn.execute(
        f"""
        INSERT INTO source_run_coverage VALUES (
            'coverage-1', 'ens-run', 'ecmwf_open_data',
            '{release_key}',
            '{track}', 'Shanghai', '2026-06-07', '{metric}',
            51, 51, '[0,3,6]', '[0,3,6]', '[101]',
            'COMPLETE', 'LIVE_ELIGIBLE',
            '2026-06-06T03:10:00+00:00',
            '2026-06-06T06:00:00+00:00',
            '2026-06-06T03:10:00+00:00'
        )
        """
    )
    identity, cycle = selected()
    assert identity is not None
    assert identity.snapshot_id == 101
    assert cycle == _dt(0)

    # Explicit target failure wins over whole-run SUCCESS.  The values model
    # the real D+5 geometry: the target requires 162/165h while this run ends
    # at 144h.
    conn.execute(
        """
        UPDATE source_run_coverage
           SET completeness_status = 'HORIZON_OUT_OF_RANGE',
               readiness_status = 'BLOCKED',
               expected_steps_json = '[3,6,144,162,165]',
               observed_steps_json = '[3,6,144]',
               expires_at = NULL
        """
    )
    assert selected() == (None, None)
    conn.execute(
        """
        UPDATE source_run_coverage
           SET completeness_status = 'COMPLETE',
               readiness_status = 'LIVE_ELIGIBLE',
               expected_steps_json = '[0,3,6]',
               observed_steps_json = '[0,3,6]',
               expires_at = '2026-06-06T06:00:00+00:00'
        """
    )

    # Incremental runs retain the same exact-coverage path.
    write_run(status="PARTIAL", completeness="PARTIAL", partial=True, imported_at=_dt(3, 20))
    identity, cycle = selected()
    assert identity is not None
    assert identity.snapshot_id == 101
    assert cycle == _dt(0)

    conn.execute(
        "UPDATE source_run_coverage SET observed_steps_json = '[0,3]'"
    )
    assert selected() == (None, None)
    conn.execute(
        "UPDATE source_run_coverage SET observed_steps_json = 'not-json'"
    )
    assert selected() == (None, None)
    conn.execute(
        "UPDATE source_run_coverage SET observed_steps_json = '[0,3,6]', "
        "expected_steps_json = '[]'"
    )
    assert selected() == (None, None)
    conn.execute(
        "UPDATE source_run_coverage SET expected_steps_json = '[0,3,6]', "
        "snapshot_ids_json = '[999]'"
    )
    assert selected() == (None, None)
    conn.execute(
        "UPDATE source_run_coverage SET snapshot_ids_json = '[101]'"
    )
    conn.execute(
        "UPDATE source_run_coverage SET recorded_at = '2026-06-06T05:00:00+00:00'"
    )
    assert selected() == (None, None)
    conn.execute(
        "UPDATE source_run_coverage SET recorded_at = '2026-06-06T03:10:00+00:00'"
    )

    write_run(
        status="SUCCESS",
        completeness="COMPLETE",
        partial=False,
        imported_at=_dt(3, 30),
    )
    identity, cycle = selected()
    assert identity is not None
    assert identity.snapshot_id == 101
    assert cycle == _dt(0)

    write_run(status="SUCCESS", completeness="COMPLETE", partial=False, imported_at=_dt(5))
    assert selected() == (None, None)
    conn.close()


@pytest.mark.usefixtures("_target_frontier_native_surfaces")
def test_new_target_family_provider_changes_final_witness() -> None:
    """A newly eligible provider must invalidate the prepared family frontier."""
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    prepared = _prepared_target_frontier(101)
    baseline = cli._target_dependency_witness(conn, prepared)
    conn.execute(
        """
        INSERT INTO raw_model_forecasts (
            raw_model_forecast_id, model, city, target_date, metric,
            source_cycle_time, source_available_at, captured_at,
            lead_days, forecast_value_c, endpoint
        ) VALUES (
            104, 'ukmo_global_deterministic_10km', 'Shanghai', '2026-06-07', 'high',
            '2026-06-06T00:00:00+00:00', '2026-06-06T03:30:00+00:00',
            '2026-06-06T03:30:00+00:00', 1, 26.0, 'single_runs'
        )
        """
    )

    _qualify_raw_fixture_rows(conn)
    current = cli._revalidate_target_dependency_witness(conn, prepared, baseline)
    refreshed = cli._target_dependency_witness(conn, prepared)
    conn.close()

    assert current != baseline
    assert current.provider_family_latest_id == 104
    assert ("ukmo_global_deterministic_10km", 104) in refreshed.provider_frontier
    assert "ukmo_global_deterministic_10km" in refreshed.provider_models


@pytest.mark.usefixtures("_hko_source_surface")
def test_final_lock_uses_real_writer_without_revalidation_or_unbounded_reads(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final lock may run bounded witnesses and the real target writer only."""
    import scripts.materialize_replacement_forecast_live as cli

    cycle = datetime(2026,10,1,tzinfo=UTC)
    conn, request = _shanghai_current_owner_request(tmp_path,monkeypatch,
        computed_at=cycle+timedelta(hours=8),first_compute_at=cycle+timedelta(hours=7),
        expires_at=cycle+timedelta(hours=10))
    assert request.day0_observed_extreme_c is None
    prepared = _prepare_for_final_write(conn, request)
    locked_sql: list[str] = []

    @contextmanager
    def writer_lock():
        conn.set_trace_callback(locked_sql.append)
        try:
            yield
        finally:
            conn.set_trace_callback(None)

    witness = object()
    monkeypatch.setattr(cli, "prepare_replacement_forecast_live", lambda *_args: prepared)
    monkeypatch.setattr(cli, "_target_dependency_witness", lambda *_args: witness)
    monkeypatch.setattr(cli, "_revalidate_target_dependency_witness", lambda *_args: witness)
    monkeypatch.setattr(
        materializer_mod,
        "_validated_replacement_forecast_request",
        lambda *_args: pytest.fail("final writer must reuse the prepared validation"),
    )

    result = cli._commit_from_read_snapshot(
        conn, prepared.request, writer_lock=writer_lock
    )
    conn.close()

    assert result.ok is True
    assert not any("PRAGMA" in sql.upper() for sql in locked_sql)
    assert not any(
        "FORECAST_POSTERIORS" in sql.upper()
        and sql.lstrip().upper().startswith("SELECT")
        and "LIMIT" not in sql.upper()
        for sql in locked_sql
    )


@pytest.mark.usefixtures("_target_frontier_native_surfaces")
def test_final_ens_frontier_preserves_production_casefold_fallback() -> None:
    from src.data.replacement_forecast_materializer import (
        read_current_evidence_snapshot_id,
        read_current_evidence_snapshot_identity,
    )

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    prepared = _prepared_target_frontier(101)
    request = replace(prepared.request, city="shanghai")

    production = read_current_evidence_snapshot_identity(
        conn, request, metric=prepared.metric
    )
    assert production is not None
    traced: list[str] = []
    conn.set_trace_callback(traced.append)
    final_id = read_current_evidence_snapshot_id(
        conn,
        request,
        metric=prepared.metric,
    )
    conn.set_trace_callback(None)
    conn.close()

    assert production.snapshot_id == 101
    assert production.city == "Shanghai"
    assert final_id == production.snapshot_id
    final_sql = [
        sql
        for sql in traced
        if sql.lstrip().upper().startswith("SELECT")
        and "FROM ENSEMBLE_SNAPSHOTS" in sql.upper()
    ]
    assert final_sql
    # exact-city exact row, casefold exact row, exact-city interval-censored row
    # (docs/operations/current/plans/ens_boundary_interval_2026-09-25.md D-3).
    assert len(final_sql) == 3
    assert "CITY = 'SHANGHAI'" in final_sql[0].upper()
    assert "LOWER(CITY) = LOWER('SHANGHAI')" in final_sql[1].upper()
    assert "CITY = 'SHANGHAI'" in final_sql[2].upper()
    assert "INTERVAL_CENSORED_TARGET_LOCAL_DAY" in final_sql[2].upper()


@pytest.mark.usefixtures("_target_frontier_native_surfaces")
def test_final_ens_frontier_detects_absent_to_present() -> None:
    """A prepared missing ENS identity must not become a permanent final gate."""
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    conn.execute("DELETE FROM ensemble_snapshots")
    prepared = _prepared_target_frontier(101)
    prepared.posterior.provenance_payload["bayes_precision_fusion"][
        "current_evidence_shape"
    ] = {"snapshot_id": None, "shape_hash": None}
    baseline = cli._target_dependency_witness(conn, prepared)
    assert baseline.ensemble_identity is None
    conn.execute(
        f"""
        INSERT INTO ensemble_snapshots VALUES (
            102, 'Shanghai', '2026-06-07', 'high',
            'ecmwf_open_data', 'ecmwf_ens', 'VERIFIED', 'b0-run', 'OK', 0,
            'FULLY_INSIDE_TARGET_LOCAL_DAY', 1,
            '2026-06-06T00:00:00+00:00', '2026-06-06T00:00:00+00:00',
            '2026-06-06T03:30:00+00:00', '2026-06-06T03:30:00+00:00',
            '[19.0,22.0]', 'degC', '{_current_baseline_data_version("high")}', '{{}}'
        )
        """
    )
    conn.execute("UPDATE ensemble_snapshots SET provenance_json=? WHERE snapshot_id=102",
                 (_fixture_ens_surface_provenance(),))
    _set_target_frontier_coverage(conn, snapshot_id=102)

    current = cli._revalidate_target_dependency_witness(conn, prepared, baseline)
    conn.close()

    assert current != baseline
    assert current.ensemble_frontier_id == 102


@pytest.mark.usefixtures("_target_frontier_native_surfaces")
def test_ens_casefold_fallback_rejects_new_exact_alias_without_current_proof() -> None:
    """An exact-city alias cannot displace a certified canonical-city proof."""
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    conn.execute("UPDATE raw_model_forecasts SET city = 'shanghai'")
    _qualify_raw_fixture_rows(conn, rebuild=True)
    prepared = _prepared_target_frontier(101)
    prepared = replace(prepared, request=replace(prepared.request, city="shanghai"))
    conn.execute(
        f"""
        INSERT INTO ensemble_snapshots VALUES (
            102, 'shanghai', '2026-06-07', 'high',
            'ecmwf_open_data', 'ecmwf_ens', 'UNVERIFIED', 'b0-run', 'OK', 0,
            'FULLY_INSIDE_TARGET_LOCAL_DAY', 1,
            '2026-06-06T00:00:00+00:00', '2026-06-06T00:00:00+00:00',
            '2026-06-06T03:30:00+00:00', '2026-06-06T03:30:00+00:00',
            '[19.0,22.0]', 'degC', '{_current_baseline_data_version("high")}', '{{}}'
        )
        """
    )
    _set_target_frontier_coverage(
        conn,
        snapshot_id=102,
        city="shanghai",
        coverage_id="b0-coverage-102",
    )
    # Current provider authority is exact canonical city identity. An old ENS
    # alias fallback does not grant authority to a malformed provider city key.
    with pytest.raises(cli._TargetDependencyWitnessUnavailable, match="provider frontier unavailable"):
        cli._target_dependency_witness(conn, prepared)
    baseline_identity = materializer_mod.read_current_evidence_snapshot_identity(
        conn, prepared.request, metric=prepared.metric)
    assert baseline_identity is not None
    assert baseline_identity.city == "Shanghai"
    conn.execute(
        "UPDATE ensemble_snapshots SET authority = 'VERIFIED' WHERE snapshot_id = 102"
    )

    with pytest.raises(cli._TargetDependencyWitnessUnavailable):
        cli._target_dependency_witness(conn, prepared)
    current_id = materializer_mod.read_current_evidence_snapshot_id(
        conn, prepared.request, metric=prepared.metric
    )
    conn.close()

    assert current_id is None


@pytest.mark.usefixtures("_target_frontier_native_surfaces")
def test_existing_frontier_indexes_require_no_live_ddl() -> None:
    """A normal posterior write must not wait on an already-complete schema."""

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    _ensure_replacement_frontier_indexes(conn)

    def deny_index_ddl(
        action: int,
        _arg1: str | None,
        _arg2: str | None,
        _database: str | None,
        _trigger: str | None,
    ) -> int:
        if action == sqlite3.SQLITE_CREATE_INDEX:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    conn.set_authorizer(deny_index_ddl)
    try:
        _ensure_replacement_frontier_indexes(conn)
    finally:
        conn.close()


@pytest.mark.usefixtures("_target_frontier_native_surfaces")
def test_final_ens_selector_has_indexed_logarithmic_work() -> None:
    """Canonical exact/folded target selectors must not scan their target range."""
    from src.data.replacement_forecast_materializer import (
        _ensure_replacement_frontier_indexes,
        read_current_evidence_snapshot_id,
    )

    samples: list[tuple[int, int, int]] = []
    captured_plans: list[tuple[str, ...]] = []
    for row_count in (10, 1_000, 10_000):
        conn = sqlite3.connect(":memory:")
        _create_target_frontier_tables(conn)
        _ensure_replacement_frontier_indexes(conn)
        conn.execute("DELETE FROM ensemble_snapshots")
        conn.executemany(
            f"""
            INSERT INTO ensemble_snapshots VALUES (
                ?, 'Shanghai', '2026-06-07', 'high',
                'ecmwf_open_data', 'ecmwf_ens', 'VERIFIED', 'b0-run', 'OK', 0,
                'FULLY_INSIDE_TARGET_LOCAL_DAY', 1,
                '2026-06-06T00:00:00+00:00', '2026-06-06T00:00:00+00:00',
                '2026-06-06T03:00:00+00:00', '2026-06-06T03:00:00+00:00',
                '[20.0,21.0]', 'degC', '{_current_baseline_data_version("high")}', '{{}}'
            )
            """,
            ((snapshot_id,) for snapshot_id in range(1, row_count + 1)),
        )
        conn.execute("UPDATE ensemble_snapshots SET provenance_json=? WHERE snapshot_id=?",
                     (_fixture_ens_surface_provenance(), row_count))
        _set_target_frontier_coverage(conn, snapshot_id=row_count)
        request = _prepared_target_frontier(row_count).request

        def measured(city: str) -> tuple[int | None, int, list[str]]:
            steps = 0
            traced: list[str] = []

            def count_step() -> int:
                nonlocal steps
                steps += 1
                return 0

            conn.set_trace_callback(traced.append)
            conn.set_progress_handler(count_step, 1)
            try:
                selected = read_current_evidence_snapshot_id(
                    conn, replace(request, city=city), metric="high"
                )
            finally:
                conn.set_progress_handler(None, 0)
                conn.set_trace_callback(None)
            return selected, steps, traced

        exact_id, exact_steps, exact_sql = measured("Shanghai")
        folded_id, folded_steps, folded_sql = measured("shanghai")
        assert exact_id == folded_id == row_count
        samples.append((row_count, exact_steps, folded_steps))
        if row_count == 10_000:
            selector_sql = [
                sql
                for sql in (*exact_sql, *folded_sql)
                if sql.lstrip().upper().startswith("SELECT")
                and "FROM ENSEMBLE_SNAPSHOTS" in sql.upper()
            ]
            captured_plans = [
                tuple(
                    str(row[3])
                    for row in conn.execute("EXPLAIN QUERY PLAN " + sql)
                )
                for sql in selector_sql
            ]
        conn.close()

    assert captured_plans
    plan_text = "\n".join(detail for plan in captured_plans for detail in plan).upper()
    assert "IDX_ENSEMBLE_SNAPSHOTS_REPLACEMENT_EXACT_FRONTIER" in plan_text
    assert "IDX_ENSEMBLE_SNAPSHOTS_REPLACEMENT_CASEFOLD_FRONTIER" in plan_text
    assert "USE TEMP B-TREE" not in plan_text
    assert not any(
        detail.upper().startswith("SCAN ")
        for plan in captured_plans
        for detail in plan
    )
    assert samples[-1][1] < samples[0][1] * 2
    assert samples[-1][2] < samples[0][2] * 2


@pytest.mark.usefixtures("_target_frontier_native_surfaces")
def test_provider_frontier_skips_invalid_rows_like_production_selector() -> None:
    from src.data.replacement_current_value_serving import (
        current_value_serving_schema,
        read_current_instrument_frontier_identity,
        read_current_instrument_values,
    )

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    conn.executemany(
        """
        INSERT INTO raw_model_forecasts (
            raw_model_forecast_id, model, city, target_date, metric,
            source_cycle_time, source_available_at, captured_at,
            lead_days, forecast_value_c, endpoint
        ) VALUES (
            ?, 'icon_global', 'Shanghai', '2026-06-07', 'high',
            ?, '2026-06-06T03:30:00+00:00', '2026-06-06T03:30:00+00:00',
            ?, ?, 'single_runs'
        )
        """,
        (
            (102, "2026-06-06T01:00:00+00:00", 1, float("inf"),),
            (103, "2026-06-06T02:00:00+00:00", "bad-lead", 28.0),
        ),
    )
    served = read_current_instrument_values(
        conn,
        city="Shanghai",
        metric="high",
        target_date="2026-06-07",
        source_cycle_time_iso="2026-06-06T00:00:00+00:00",
        decision_time_iso="2026-06-06T04:00:00+00:00",
        include_station_sources=True,
    )
    frontier = read_current_instrument_frontier_identity(
        conn,
        city="Shanghai",
        metric="high",
        target_date="2026-06-07",
        decision_time_iso="2026-06-06T04:00:00+00:00",
        models=("icon_global",),
        schema=current_value_serving_schema(conn),
    )
    conn.close()

    assert served["icon_global"].raw_model_forecast_id == 101
    assert frontier == (("icon_global", 101),)


@pytest.mark.usefixtures("_historical_shanghai_component_surface")
def test_day0_final_writer_uses_frozen_frontier_without_likelihood_recompute(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real final writer compares Day0 identity without rebuilding fast likelihood."""
    conn,basis = _historical_shanghai_component_request(tmp_path,monkeypatch)
    absorbing = replace(basis,
        computed_at=_dt(18),
        expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        day0_observed_extreme_c=30.0,
        day0_observed_extreme_source="wu_icao_history",
        day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
    )
    assert materialize_replacement_forecast_live(conn, absorbing).ok is True
    conn.commit()
    provisional = replace(
        absorbing,
        computed_at=_dt(18, 10),
        day0_observed_extreme_c=31.0,
        day0_observed_extreme_source="wu_api+same_station_fast_tail",
        day0_observed_extreme_observation_time=_dt(18, 5).isoformat(),
    )
    likelihood = _fixture_fast_residual_likelihood(extreme_c=30.0, at=_dt(18, 10))
    monkeypatch.setattr(
        "src.data.day0_fast_obs.build_fast_station_residual_likelihood",
        lambda *_args, **_kwargs: likelihood,
    )
    _record_fixture_current_temperature(conn, at=_dt(18, 5), value_c=31.0)
    provisional = _refresh_shanghai_owner_request(conn,monkeypatch,provisional,record_observed_prints=False)
    prepared = _prepare_for_final_write(conn, provisional)
    monkeypatch.setattr(
        "src.data.day0_fast_obs.build_fast_station_residual_likelihood",
        lambda *_args, **_kwargs: pytest.fail(
            "final writer must not rebuild Day0 likelihood"
        ),
    )
    locked_sql: list[str] = []
    conn.set_trace_callback(locked_sql.append)
    conn.execute("BEGIN IMMEDIATE")
    result = materializer_mod.write_prepared_replacement_forecast_live(conn, prepared)
    conn.commit()
    conn.set_trace_callback(None)
    conn.close()

    assert result.ok is True
    final_selects = [
        sql for sql in locked_sql if sql.lstrip().upper().startswith("SELECT")
    ]
    assert not any("OBSERVATION_PRINTS" in sql.upper() for sql in locked_sql)
    assert not any(
        "SELECT PROVENANCE_JSON, COMPUTED_AT" in sql.upper()
        and "FROM FORECAST_POSTERIORS" in sql.upper()
        for sql in final_selects
    )


@pytest.mark.usefixtures("_hko_source_surface")
def test_day0_ledger_frontier_allows_65_rows_and_retries_on_append(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Append-only Day0 history has no row-count ratchet; a real append stales prepare."""
    cycle = datetime(2026,10,1,tzinfo=UTC)
    conn, request = _shanghai_current_owner_request(tmp_path,monkeypatch,
        computed_at=cycle+timedelta(hours=18,minutes=10),observed_extreme=30.)
    # The synthetic history protects the bounded ledger frontier, not WU's
    # pre-transition channel license. Keep this current same-station product.
    conn.execute("SAVEPOINT normal_control")
    assert materialize_replacement_forecast_live(conn,request).ok is True
    conn.execute("ROLLBACK TO normal_control")
    conn.execute("RELEASE normal_control")
    conditioning = json.dumps(
        {
            "day0_conditioning": {
                "active": True,
                "metric": "high",
                "observed_extreme_c": 30.0,
                "source": "noaa_wrh_zspd",
                "observation_time": (cycle+timedelta(hours=16)).isoformat(),
            }
        }
    )
    rows = [
        (
            materializer_mod.SOURCE_ID,
            materializer_mod.PRODUCT_ID,
            f"history-{index}",
            request.city,
            request.target_date.isoformat(),
            "high",
            request.source_cycle_time.isoformat(),
            request.openmeteo_source_available_at.isoformat(),
            (cycle+timedelta(hours=17,minutes=index % 60)).isoformat(),
            "{}",
            "history",
            conditioning,
        )
        for index in range(65)
    ]
    conn.executemany(
        """
        INSERT INTO forecast_posteriors (
            source_id, product_id, data_version, city, target_date,
            temperature_metric, source_cycle_time, source_available_at,
            computed_at, q_json, posterior_method, provenance_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    prepared = _prepare_for_final_write(conn, request)

    conn.execute("BEGIN IMMEDIATE")
    first = materializer_mod.write_prepared_replacement_forecast_live(conn, prepared)
    conn.commit()
    assert first.ok is True

    current = _refresh_shanghai_owner_request(conn,monkeypatch,
        replace(request,computed_at=cycle+timedelta(hours=18,minutes=20)))
    stale = _prepare_for_final_write(conn, current)
    conn.execute(
        """
        INSERT INTO forecast_posteriors (
            source_id, product_id, data_version, city, target_date,
            temperature_metric, source_cycle_time, source_available_at,
            computed_at, q_json, posterior_method, provenance_json
        ) VALUES (?, ?, 'append', ?, ?, 'high', ?, ?, ?, '{}', 'history', ?)
        """,
        (
            materializer_mod.SOURCE_ID,
            materializer_mod.PRODUCT_ID,
            request.city,
            request.target_date.isoformat(),
            request.source_cycle_time.isoformat(),
            request.openmeteo_source_available_at.isoformat(),
            (cycle+timedelta(hours=18,minutes=15)).isoformat(),
            conditioning,
        ),
    )
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(materializer_mod.PreparedReplacementForecastSnapshotStale):
        materializer_mod.write_prepared_replacement_forecast_live(conn, stale)
    conn.rollback()
    refreshed = _prepare_for_final_write(
        conn, current
    )
    latest_id = conn.execute(
        "SELECT MAX(posterior_id) FROM forecast_posteriors"
    ).fetchone()[0]
    changed_conditioning = json.loads(conditioning)
    changed_conditioning["day0_conditioning"]["observed_extreme_c"] = 31.0
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(changed_conditioning), latest_id),
    )
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(materializer_mod.PreparedReplacementForecastSnapshotStale):
        materializer_mod.write_prepared_replacement_forecast_live(conn, refreshed)
    conn.rollback()
    conn.close()


@pytest.mark.usefixtures("_target_frontier_native_surfaces")
def test_final_frontier_queries_use_exact_target_indexes_without_temp_sort() -> None:
    from src.data.replacement_current_value_serving import (
        current_value_serving_schema,
        read_current_instrument_family_latest_id,
        read_current_instrument_frontier_identity,
    )
    from src.data.replacement_forecast_materializer import (
        read_current_evidence_snapshot_id,
        read_current_evidence_snapshot_identity,
    )

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    _ensure_replacement_identity_columns(conn)
    _ensure_replacement_frontier_indexes(conn)
    request = _prepared_target_frontier(101).request
    traced: list[str] = []
    # Real ordinary body/receipt params are one location. JSON coordinate axes
    # are bounded per artifact; they are not canonical-table query frontiers.
    artifact = conn.execute("""SELECT a.request_params_json FROM raw_forecast_artifacts a
        JOIN raw_model_forecasts r ON r.artifact_id=a.artifact_id WHERE r.raw_model_forecast_id=101""").fetchone()
    params = json.loads(artifact[0])
    assert all(len(str(params[key]).split(",")) == 1 for key in ("latitude", "longitude", "timezone"))
    conn.set_trace_callback(traced.append)
    read_current_instrument_frontier_identity(
        conn,
        city=request.city,
        metric="high",
        target_date=request.target_date.isoformat(),
        decision_time_iso=request.computed_at.isoformat(),
        models=("icon_global",),
        schema=current_value_serving_schema(conn),
    )
    read_current_instrument_family_latest_id(
        conn,
        city=request.city,
        metric="high",
        target_date=request.target_date.isoformat(),
    )
    materializer_mod._day0_ledger_frontier_identity(conn, request, metric="high")
    identity = read_current_evidence_snapshot_identity(conn, request, metric="high")
    assert identity is not None
    read_current_evidence_snapshot_id(conn, request, metric="high")
    conn.set_trace_callback(None)
    frontier_sql = [
        sql
        for sql in traced
        if sql.lstrip().upper().startswith("SELECT")
        and (
            "FROM RAW_MODEL_FORECASTS" in sql.upper()
            or "FROM FORECAST_POSTERIORS" in sql.upper()
            or (
                "FROM ENSEMBLE_SNAPSHOTS" in sql.upper()
                and "ORDER BY COALESCE" not in sql.upper()
            )
        )
    ]
    plans = [
        tuple(str(row[3]) for row in conn.execute("EXPLAIN QUERY PLAN " + sql))
        for sql in frontier_sql
    ]
    ens_plans = [str(row[3]) for sql in traced
                 if sql.lstrip().upper().startswith("SELECT") and "FROM ENSEMBLE_SNAPSHOTS" in sql.upper()
                 for row in conn.execute("EXPLAIN QUERY PLAN " + sql)]
    conn.close()

    assert plans
    assert all(
        not any("USE TEMP B-TREE" in detail.upper() for detail in plan)
        for plan in plans
    )
    assert any(
        any("IDX_RAW_MODEL_FORECASTS_TARGET_MODEL_FRONTIER" in detail.upper() for detail in plan)
        for plan in plans
    )
    assert any(
        any("IDX_FORECAST_POSTERIORS_SOURCE_FAMILY_FRONTIER" in detail.upper() for detail in plan)
        for plan in plans
    )
    known_json_axis_scans = {f"SCAN {axis} VIRTUAL TABLE INDEX 1:" for axis in ("lat", "lon", "tz")}
    scans = {detail for plan in plans for detail in plan if detail.upper().startswith("SCAN ")}
    # These three bounded virtual axes are permitted, not mandatory. Current
    # SQL may use no virtual scan at all; every other SCAN remains forbidden.
    assert scans <= known_json_axis_scans
    # Every persisted entity must positively use a keyed SEARCH. No physical
    # table or unknown virtual alias is exempted by the JSON-axis allowance.
    for entity in ("raw_model_forecasts", "a", "b", "forecast_posteriors"):
        assert any(detail.startswith(f"SEARCH {entity} ") for plan in plans for detail in plan), (entity, plans)
    assert any(detail.startswith("SEARCH ensemble_snapshot USING INDEX idx_ensemble_snapshots_replacement_exact_frontier ")
               for detail in ens_plans), ens_plans
    assert not any(detail.startswith(f"SCAN {entity} ") for detail in ens_plans
                   for entity in ("ensemble_snapshot", "ensemble_snapshots", "source_run", "source_run_coverage")), ens_plans


@pytest.mark.usefixtures("_target_frontier_native_surfaces")
def test_final_provider_witness_query_count_is_fixed_with_many_invalid_rows() -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    conn.executescript(
        """
        CREATE INDEX idx_raw_model_forecasts_target_model_frontier
            ON raw_model_forecasts(
                city, target_date, metric, model,
                datetime(source_cycle_time) DESC,
                CASE endpoint WHEN 'single_runs' THEN 0 ELSE 1 END,
                lead_days, captured_at DESC, raw_model_forecast_id DESC
            );
        CREATE INDEX idx_raw_model_forecasts_target_frontier
            ON raw_model_forecasts(
                city, target_date, metric, raw_model_forecast_id DESC
            );
        """
    )
    conn.executemany(
        """
        INSERT INTO raw_model_forecasts (
            raw_model_forecast_id, model, city, target_date, metric,
            source_cycle_time, source_available_at, captured_at,
            lead_days, forecast_value_c, endpoint
        ) VALUES (
            ?, 'icon_global', 'Shanghai', '2026-06-07', 'high',
            '2026-06-06T02:00:00+00:00', '2026-06-06T03:30:00+00:00',
            '2026-06-06T03:30:00+00:00', 'bad-lead', NULL, 'single_runs'
        )
        """,
        ((1000 + index,) for index in range(1000)),
    )
    prepared = _prepared_target_frontier(101)
    baseline = cli._target_dependency_witness(conn, prepared)
    traced: list[str] = []
    conn.set_trace_callback(traced.append)
    current = cli._revalidate_target_dependency_witness(conn, prepared, baseline)
    conn.set_trace_callback(None)

    provider_selects = [
        sql
        for sql in traced
        if sql.lstrip().upper().startswith("SELECT")
        and "FROM RAW_MODEL_FORECASTS" in sql.upper()
    ]
    assert current == baseline
    assert len(provider_selects) == 2
    assert all("OFFSET" not in sql.upper() for sql in provider_selects)
    assert any("ORDER BY RAW_MODEL_FORECAST_ID DESC" in sql.upper() for sql in provider_selects)
    assert any("RAW_MODEL_FORECAST_ID IN" in sql.upper() for sql in provider_selects)
    conn.execute(
        """
        INSERT INTO raw_model_forecasts (
            raw_model_forecast_id, model, city, target_date, metric,
            source_cycle_time, source_available_at, captured_at,
            lead_days, forecast_value_c, endpoint
        ) VALUES (
            3000, 'new_invalid', 'Shanghai', '2026-06-07', 'high',
            '2026-06-06T02:00:00+00:00', '2026-06-06T03:30:00+00:00',
            '2026-06-06T03:30:00+00:00', 'bad-lead', NULL, 'single_runs'
        )
        """
    )
    assert cli._revalidate_target_dependency_witness(
        conn, prepared, baseline
    ) != baseline
    conn.close()


@pytest.mark.usefixtures("_hko_source_surface")
def test_source_clock_production_selector_has_no_cap_before_legal_winners(tmp_path, monkeypatch) -> None:
    """Diagnostic rows cannot truncate two actual, later ordered winners.

    The retired fixture's 520 made-up provider names had no source authority.
    They remain explicit diagnostics; only normal ICON/UKMO captures may serve.
    """
    from src.data import replacement_current_value_serving as current
    from src.data.station_ground_evidence import archive_station_ground_evidence
    db = tmp_path / "forecast.db"
    conn = _low_revision_authority_conn(db, include_current_fixture=False)
    assert archive_station_ground_evidence(db, ["Hong Kong"])["status"] == "GROUND_SOURCE_ARCHIVED"
    request = _hko_request_with_owned_anchor(conn, _low_revision_request())
    native, _, members = _fixture_native_shape_identity(conn,request,monkeypatch,
        members_c=tuple(19.+index*.01 for index in range(51)))
    assert members == pytest.approx(tuple(19.+index*.01 for index in range(51)))
    request = replace(request,baseline_source_run_id=native["source_run_id"],
        baseline_source_available_at=datetime.fromisoformat(native["source_available_at"]))
    captured = _hko_current_provider_inputs(request, {
        "icon_global": 27., "ukmo_global_deterministic_10km": 29.,
    }, conn=conn)
    expected = {model: value.raw_model_forecast_id for model, value in captured.items()}
    baseline = materialize_replacement_forecast_live(conn, request)
    assert baseline.ok, baseline.reason_codes
    baseline_row = conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?", (baseline.posterior_id,)).fetchone()
    baseline_q = {key: json.loads(baseline_row[key]) for key in ("q_json", "q_lcb_json", "q_ucb_json")}
    baseline_fusion = json.loads(baseline_row["provenance_json"])["bayes_precision_fusion"]
    assert baseline_fusion["current_evidence_shape"]["snapshot_id"] == native["snapshot_id"]
    baseline_override = materializer_mod._replacement_bayes_precision_fusion_override(
        request, metric="low", anchor_value_corrected_c=request.openmeteo_anchor.low_c, conn=conn)
    assert baseline_override is not None
    # Separate causal pair: a genuinely legal new-day full prior, not a late
    # run's fabricated elapsed prefix. Only the independent decision cut moves.
    future_cycle = datetime(2026, 10, 1, tzinfo=UTC)
    future_capture = future_cycle + timedelta(hours=1)
    future_request = replace(request, target_date=date(2026, 10, 2),
        source_cycle_time=future_cycle, computed_at=future_cycle+timedelta(hours=2),
        openmeteo_source_available_at=future_capture)
    future = _hko_current_provider_inputs(future_request, {"icon_global":31.}, conn=conn)
    future_id = future["icon_global"].raw_model_forecast_id
    future_kwargs = dict(city=request.city, metric="low", target_date="2026-10-02",
        source_cycle_time_iso=future_cycle.isoformat(), include_station_sources=True)
    after_capture = current.read_current_instrument_values(conn, **future_kwargs,
        decision_time_iso=future_request.computed_at.isoformat())
    assert {model:value.raw_model_forecast_id for model,value in after_capture.items()} == {"icon_global":future_id}
    assert after_capture["icon_global"].value_c == 31.
    assert not current.read_current_instrument_values(conn, **future_kwargs,
        decision_time_iso=(future_capture-timedelta(microseconds=1)).isoformat())
    conn.executemany(
        """
        INSERT INTO raw_model_forecasts (
            model, city, target_date, metric, source_cycle_time,
            source_available_at, captured_at, lead_days, forecast_value_c, endpoint, recorded_at
        ) VALUES (?, 'Hong Kong', '2026-10-01', 'low',
                  '2026-09-30T12:00:00+00:00', '2026-09-30T13:00:00+00:00',
                  '2026-09-30T13:00:00+00:00', 1, 27.0, 'single_runs',
                  '2026-09-30T13:00:00+00:00')
        """,
        ((f"diagnostic_unproven_{index:03d}",) for index in range(520)),
    )
    kwargs = dict(city="Hong Kong", metric="low", target_date="2026-10-01",
                  decision_time_iso=request.computed_at.isoformat())
    schema = current.current_value_serving_schema(conn)
    query, params = current._source_clock_rows_query(**dict(
        city=kwargs["city"], metric=kwargs["metric"], target_date=kwargs["target_date"],
        decision_iso=kwargs["decision_time_iso"], schema=schema, max_substitution_age_hours=8.,
    ))
    ordered = list(conn.execute(query, params))
    assert len(ordered) == 522  # 520 unsupported diagnostics + two actual qualified winners.
    legacy = ("ecmwf_ifs9", "gfs", "icon", "gem", "jma")
    legacy_ids = {row[0] for row in conn.execute("SELECT raw_model_forecast_id FROM raw_model_forecasts WHERE model IN (?,?,?,?,?)",legacy)}
    assert len(legacy_ids) == 5  # Still stored, not silently deleted to match the count.
    assert legacy_ids.isdisjoint({row[0] for row in ordered})
    assert set(legacy).isdisjoint(current.read_current_instrument_values(conn, **kwargs,
        source_cycle_time_iso=request.source_cycle_time.isoformat()))
    for model, raw_id in expected.items():
        assert next(index for index, row in enumerate(ordered, 1) if row[0] == raw_id) > 512
    assert future_id not in {row[0] for row in ordered}
    def assert_winners():
        served = current.read_current_instrument_values(conn, **kwargs,
            source_cycle_time_iso=request.source_cycle_time.isoformat(), include_station_sources=True)
        assert {model: value.raw_model_forecast_id for model, value in served.items()} == expected
        assert {model: value.value_c for model, value in served.items()} == {"icon_global":27., "ukmo_global_deterministic_10km":29.}
        assert all(value.served_cycle == request.source_cycle_time.isoformat() for value in served.values())
        assert not any(model.startswith("diagnostic_unproven_") for model in served)
        assert dict(current.read_current_instrument_frontier_identity(conn, **kwargs,
            models=tuple(expected), schema=schema)) == expected
    assert_winners()
    rebuilt = materialize_replacement_forecast_live(conn, request)
    assert rebuilt.ok, rebuilt.reason_codes
    rebuilt_row = conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?", (rebuilt.posterior_id,)).fetchone()
    assert {key: json.loads(rebuilt_row[key]) for key in baseline_q} == baseline_q
    fusion = json.loads(rebuilt_row["provenance_json"])["bayes_precision_fusion"]
    assert set(expected).issubset(fusion["used_models"])
    assert fusion["used_models"] == baseline_fusion["used_models"]
    rebuilt_override = materializer_mod._replacement_bayes_precision_fusion_override(
        request, metric="low", anchor_value_corrected_c=request.openmeteo_anchor.low_c, conn=conn)
    assert rebuilt_override is not None
    weights = {model: basis["weight"] for model, basis in rebuilt_override.precision_center_basis.items()}
    assert weights == {model: basis["weight"] for model, basis in baseline_override.precision_center_basis.items()}
    assert all(math.isfinite(weight) and weight > 0 for weight in weights.values())
    assert sum(weights.values()) == pytest.approx(1.)
    assert not any(model.startswith("diagnostic_unproven_") for model in fusion["used_models"])
    assert not any(model.startswith("diagnostic_unproven_") for model in rebuilt_override.precision_center_basis)

    # Inject the actual regression, not a mirrored count assertion. The same
    # winner contract must turn red when the production SQL stream is capped.
    real_query = current._source_clock_rows_query
    with monkeypatch.context() as capped:
        def limited(*args, **kwargs):
            sql, args = real_query(*args, **kwargs)
            return sql + "\n LIMIT 512", args
        capped.setattr(current, "_source_clock_rows_query", limited)
        with pytest.raises(AssertionError):
            assert_winners()
        assert not materialize_replacement_forecast_live(conn, request).ok
    assert_winners()
    conn.close()


@pytest.mark.usefixtures("_target_frontier_native_surfaces")
def test_final_lock_reads_only_exact_ids_and_bounded_frontiers(
    monkeypatch,
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    prepared = _prepared_target_frontier(101)
    lock_held = False
    locked_sql: list[str] = []

    @contextmanager
    def writer_lock():
        nonlocal lock_held
        lock_held = True
        conn.set_trace_callback(locked_sql.append)
        try:
            yield
        finally:
            conn.set_trace_callback(None)
            lock_held = False

    def _prepare(*_args):
        assert lock_held is False
        return prepared

    def _write(*_args):
        assert lock_held is True
        return _ready_materialization_result()

    monkeypatch.setattr(cli, "prepare_replacement_forecast_live", _prepare)
    monkeypatch.setattr(cli, "write_prepared_replacement_forecast_live", _write)
    result = cli._commit_from_read_snapshot(
        conn, prepared.request, writer_lock=writer_lock
    )
    conn.close()

    selects = [sql for sql in locked_sql if sql.lstrip().upper().startswith("SELECT")]
    assert result.ok is True
    assert selects
    assert not any("PRAGMA" in sql.upper() for sql in locked_sql)
    assert any("SOURCE_RUN_ID IN" in sql.upper() for sql in selects)
    assert any("ARTIFACT_ID IN" in sql.upper() for sql in selects)
    assert any("RAW_MODEL_FORECAST_ID IN" in sql.upper() for sql in selects)
    assert any("SNAPSHOT_ID IN" in sql.upper() for sql in selects)
    frontier_selects = [
        sql
        for sql in selects
        if "ORDER BY RAW_MODEL_FORECAST_ID DESC" in sql.upper()
        or "SNAPSHOT_ID >" in sql.upper()
    ]
    assert frontier_selects
    assert all("LIMIT" in sql.upper() for sql in frontier_selects)
    assert not any("OFFSET" in sql.upper() for sql in selects)


@pytest.mark.usefixtures("_target_frontier_native_surfaces")
def test_exact_target_supersession_retries_before_commit(monkeypatch) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    _create_target_frontier_tables(conn)
    prepared = _prepared_target_frontier(101)
    prepare_results = iter((prepared, _blocked_materialization_result()))
    prepare_calls = 0
    writes = []

    def _prepare(*_args):
        nonlocal prepare_calls
        prepare_calls += 1
        return next(prepare_results)

    @contextmanager
    def writer_lock():
        conn.execute(
            """
            INSERT OR IGNORE INTO raw_model_forecasts (
            raw_model_forecast_id, model, city, target_date, metric,
            source_cycle_time, source_available_at, captured_at,
            lead_days, forecast_value_c, endpoint
        ) VALUES (
                102, 'icon_global', 'Shanghai', '2026-06-07', 'high',
                '2026-06-06T01:00:00+00:00', '2026-06-06T03:30:00+00:00',
                '2026-06-06T03:30:00+00:00', 1, 26.0, 'single_runs'
            )
            """
        )
        conn.commit()
        yield

    monkeypatch.setattr(cli, "prepare_replacement_forecast_live", _prepare)
    monkeypatch.setattr(
        cli,
        "write_prepared_replacement_forecast_live",
        lambda *_args: writes.append(True) or _ready_materialization_result(),
    )
    result = cli._commit_from_read_snapshot(
        conn, prepared.request, writer_lock=writer_lock
    )
    conn.close()

    assert result.status == "BLOCKED"
    assert prepare_calls == 2
    assert writes == []


def test_materialize_script_refuses_changed_anchor_or_provider_frontier(
    monkeypatch,
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    original = _prepared_target_frontier(101)
    prepare_results = iter((original, _blocked_materialization_result()))
    baseline_witness = "provider-row-101"
    writes = []
    monkeypatch.setattr(
        cli, "prepare_replacement_forecast_live", lambda *_args: next(prepare_results)
    )
    monkeypatch.setattr(
        cli,
        "_target_dependency_witness",
        lambda *_args: baseline_witness,
    )
    monkeypatch.setattr(
        cli,
        "_revalidate_target_dependency_witness",
        lambda *_args: "provider-row-102",
    )
    monkeypatch.setattr(
        cli,
        "write_prepared_replacement_forecast_live",
        lambda *_args: writes.append(True) or _ready_materialization_result(),
    )

    result = cli._commit_from_read_snapshot(
        conn, original.request, writer_lock=nullcontext
    )
    conn.close()

    assert result.status == "BLOCKED"
    assert writes == []


def test_materialize_script_snapshot_retry_exhaustion_never_computes_under_writer_lock(
    monkeypatch,
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    prepare_calls = []
    witness_calls = []
    write_calls = []

    def _prepare(_conn, _request):
        prepare_calls.append(True)
        return object()

    def _witness(*_args):
        value = len(witness_calls) * 2
        witness_calls.append(value)
        return value

    def _revalidate(*_args):
        value = len(witness_calls) * 2 + 1
        witness_calls.append(value)
        return value

    monkeypatch.setattr(cli, "prepare_replacement_forecast_live", _prepare)
    monkeypatch.setattr(cli, "_target_dependency_witness", _witness)
    monkeypatch.setattr(cli, "_revalidate_target_dependency_witness", _revalidate)
    monkeypatch.setattr(
        cli,
        "write_prepared_replacement_forecast_live",
        lambda *_args: write_calls.append(True),
    )

    with pytest.raises(
        RuntimeError,
        match="^REPLACEMENT_FORECAST_SNAPSHOT_RETRY_EXHAUSTED$",
    ):
        cli._commit_from_read_snapshot(
            conn,
            SimpleNamespace(
                city="London",
                target_date=date(2026, 7, 19),
                temperature_metric="high",
            ),
            writer_lock=nullcontext,
        )
    conn.close()

    assert len(prepare_calls) == cli._SNAPSHOT_RETRY_LIMIT
    assert len(witness_calls) == cli._SNAPSHOT_RETRY_LIMIT * 2
    assert write_calls == []


def test_materialize_script_retries_changed_frontier_before_writer(
    monkeypatch,
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    prepared = [object(), object()]
    witness_values = iter(("before", "stable"))
    revalidated_values = iter(("after", "stable"))
    prepare_calls = []
    witness_lock_states = []
    write_calls = []
    lock_held = False

    @contextmanager
    def writer_lock():
        nonlocal lock_held
        assert lock_held is False
        lock_held = True
        try:
            yield
        finally:
            lock_held = False

    def _prepare(_conn, _request):
        assert lock_held is False
        value = prepared[len(prepare_calls)]
        prepare_calls.append(value)
        return value

    def _witness(_conn, _prepared):
        witness_lock_states.append(lock_held)
        return next(witness_values)

    def _revalidate(_conn, _prepared, _baseline):
        witness_lock_states.append(lock_held)
        return next(revalidated_values)

    def _write(_conn, value):
        assert lock_held is True
        assert value is prepared[1]
        write_calls.append(value)
        return _ready_materialization_result()

    monkeypatch.setattr(cli, "prepare_replacement_forecast_live", _prepare)
    monkeypatch.setattr(cli, "_target_dependency_witness", _witness)
    monkeypatch.setattr(cli, "_revalidate_target_dependency_witness", _revalidate)
    monkeypatch.setattr(cli, "write_prepared_replacement_forecast_live", _write)

    result = cli._commit_from_read_snapshot(
        conn,
        SimpleNamespace(
            city="London",
            target_date=date(2026, 7, 19),
            temperature_metric="high",
        ),
        writer_lock=writer_lock,
    )
    conn.close()

    assert result.ok is True
    assert prepare_calls == prepared
    assert witness_lock_states == [False, True, False, True]
    assert write_calls == [prepared[1]]


def test_prepared_writer_never_revalidates_or_recomputes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    prepared = materializer_mod.PreparedReplacementForecastMaterialization(
        request=_request(),
        metric="high",
        day0_ledger_frontier_identity=None,
        posterior=SimpleNamespace(
            live_eligible=False,
            replacement_q_mode="BLOCKED",
            capture_status="MISSING",
            fusion_decline_reason=None,
            fusion_decline_evidence=None,
            predictive_sigma_c=None,
            q_lcb_map=None,
            q_ucb_map=None,
        ),
    )
    monkeypatch.setattr(
        materializer_mod,
        "_validated_replacement_forecast_request",
        lambda *_args: pytest.fail("prepared writer must reuse prepare validation"),
    )
    monkeypatch.setattr(
        materializer_mod,
        "_insert_anchor",
        lambda *_args, **_kwargs: 1,
    )

    result = materializer_mod.write_prepared_replacement_forecast_live(
        conn, prepared
    )
    conn.close()
    assert result.status == "BLOCKED"


def test_materialize_script_commit_helper_requires_writer_lock() -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    with pytest.raises(
        RuntimeError, match="^REPLACEMENT_FORECAST_WRITER_LOCK_REQUIRED$"
    ):
        cli._commit_from_read_snapshot(
            conn,
            SimpleNamespace(
                city="London",
                target_date=date(2026, 7, 19),
                temperature_metric="high",
            ),
        )
    conn.close()


def test_materialize_script_injected_commit_connection_requires_writer_lock(
    tmp_path,
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = sqlite3.connect(":memory:")
    with pytest.raises(
        RuntimeError, match="^REPLACEMENT_FORECAST_WRITER_LOCK_REQUIRED$"
    ):
        cli._materialize(
            tmp_path / "not-read.json",
            commit=True,
            init_schema=False,
            conn=conn,
        )
    conn.close()


@pytest.mark.usefixtures("_target_frontier_native_surfaces")
def test_materialize_cli_bootstraps_hot_indexes_outside_writer_lock(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reachable commit path must finish index DDL before final flock/txn."""
    import scripts.materialize_replacement_forecast_live as cli

    payload = {
        "city": "Shanghai",
        "city_id": "Shanghai",
        "city_timezone": "Asia/Shanghai",
        "target_date": "2026-06-07",
        "temperature_metric": "high",
        "source_cycle_time": "2026-06-06T00:00:00+00:00",
        "computed_at": "2026-06-06T04:00:00+00:00",
        "expires_at": "2026-06-06T06:00:00+00:00",
        "baseline_source_run_id": "b0-run",
        "baseline_data_version": _current_baseline_data_version("high"),
        "baseline_source_available_at": "2026-06-06T02:00:00+00:00",
        "openmeteo_source_run_id": "om9-run",
        "openmeteo_source_available_at": "2026-06-06T03:00:00+00:00",
        "openmeteo_payload_json": "anchor.json",
        "precision_metadata_json": "precision.json",
        "bins": [{"bin_id": "warm", "lower_c": 20.0, "upper_c": 30.0}],
    }
    input_json = tmp_path / "request.json"
    input_json.write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "anchor.json").write_text("{}", encoding="utf-8")
    (tmp_path / "precision.json").write_text("{}", encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    lock_held = False
    bootstrap_states: list[tuple[bool, bool]] = []
    real_bootstrap_indexes = cli._ensure_replacement_frontier_indexes

    @contextmanager
    def writer_lock():
        nonlocal lock_held
        assert lock_held is False
        lock_held = True
        try:
            yield
        finally:
            lock_held = False

    def bootstrap_indexes(connection: sqlite3.Connection) -> None:
        bootstrap_states.append((lock_held, connection.in_transaction))
        real_bootstrap_indexes(connection)

    def prepare_schema(*_args, **_kwargs):
        _create_target_frontier_tables(conn)
        return cli._DurablePreparationReceipt(
            schema_ready=True,
            anchor_artifact_id=None,
            manifest_committed=False,
        )

    monkeypatch.setattr(
        cli,
        "extract_openmeteo_ecmwf_ifs9_localday_anchor",
        lambda *_args, **_kwargs: _anchor(),
    )
    monkeypatch.setattr(cli, "OpenMeteoIfs9PrecisionMetadata", lambda **_kwargs: object())
    monkeypatch.setattr(
        cli,
        "evaluate_openmeteo_ecmwf_ifs9_precision_guard",
        lambda _metadata, **_kwargs: _precision_guard(),
    )
    monkeypatch.setattr(cli, "_bins", lambda _payload: _bins())
    monkeypatch.setattr(cli, "_ensure_replacement_frontier_indexes", bootstrap_indexes)
    monkeypatch.setattr(
        cli,
        "_prepare_live_schema_and_manifest",
        prepare_schema,
    )
    monkeypatch.setattr(
        cli,
        "_commit_from_read_snapshot",
        lambda *_args, **_kwargs: _ready_materialization_result(),
    )

    returncode, response = cli._materialize(
        input_json,
        commit=True,
        init_schema=True,
        conn=conn,
        writer_lock=writer_lock,
    )
    index_names = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }
    conn.close()

    assert returncode == 0
    assert response["status"] == "READY"
    assert bootstrap_states
    assert all(state == (False, False) for state in bootstrap_states)
    assert "idx_raw_model_forecasts_target_model_frontier" in index_names
    assert "idx_ensemble_snapshots_replacement_exact_frontier" in index_names
    assert "idx_ensemble_snapshots_replacement_casefold_frontier" in index_names


def test_materialize_script_dry_run_compute_does_not_hold_writer_lock(
    tmp_path, monkeypatch
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    db_path = tmp_path / "forecasts.db"
    reader = sqlite3.connect(db_path)
    writer = sqlite3.connect(db_path, timeout=0)
    reader.execute("PRAGMA journal_mode=WAL")
    reader.execute("CREATE TABLE frontier (value INTEGER NOT NULL)")
    reader.commit()
    trace = []
    reader.set_trace_callback(trace.append)

    def _prepare(_conn, _request):
        writer.execute("BEGIN IMMEDIATE")
        writer.rollback()
        return SimpleNamespace(
            posterior=SimpleNamespace(live_eligible=True),
            anchor_id=None,
        )

    monkeypatch.setattr(cli, "prepare_replacement_forecast_live", _prepare)
    monkeypatch.setattr(
        cli,
        "write_prepared_replacement_forecast_live",
        lambda _conn, _prepared: materializer_mod.ReplacementForecastMaterializeResult(
            status="READY",
            reason_codes=("REPLACEMENT_FORECAST_DRY_RUN_READY",),
            posterior_id=1,
            anchor_id=1,
            readiness_id="ready-1",
        ),
    )
    result = cli._dry_run_from_read_snapshot(reader, _request())
    reader.close()
    writer.close()

    assert result.ok is True
    statements = [statement.upper() for statement in trace]
    assert statements.index("BEGIN") < statements.index("ROLLBACK")
    assert statements.index("ROLLBACK") < statements.index("BEGIN IMMEDIATE")


@pytest.mark.usefixtures("_hko_source_surface")
def test_materialize_script_dry_run_matches_readiness_cert_regression(
    monkeypatch,
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    conn = _conn()
    # Possess the identical normal body before both analysis cuts. The only
    # later regression is the readiness certificate, not a future artifact.
    older_request = _hko_request(computed_at=_hko_dt(9), expires_at=_hko_dt(13))
    older_request = _install_hko_live_fusion(monkeypatch, conn=conn, request=older_request)
    first_request = _hko_request(computed_at=_hko_dt(11), expires_at=_hko_dt(13))
    first_request = _install_hko_live_fusion(monkeypatch, conn=conn, request=first_request)
    incumbent = materialize_replacement_forecast_live(
        conn,
        first_request,
    )
    conn.commit()
    before = conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0]
    older_request = _install_hko_live_fusion(monkeypatch, conn=conn, request=older_request)
    result = cli._dry_run_from_read_snapshot(
        conn,
        older_request,
    )
    after = conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0]
    conn.close()

    assert incumbent.ok is True
    assert result.ok is False
    assert result.reason_codes == ("READINESS_CERT_CYCLE_REGRESSION",)
    assert after == before


def test_materialize_script_reports_durable_manifest_when_posterior_fails(
    tmp_path, monkeypatch
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    payload = {
        "city": "Shanghai",
        "city_id": "Shanghai",
        "city_timezone": "Asia/Shanghai",
        "target_date": "2026-06-07",
        "temperature_metric": "high",
        "source_cycle_time": "2026-06-06T00:00:00+00:00",
        "computed_at": "2026-06-06T04:00:00+00:00",
        "expires_at": "2026-06-06T06:00:00+00:00",
        "baseline_source_run_id": "b0-run",
        "baseline_data_version": _current_baseline_data_version("high"),
        "baseline_source_available_at": "2026-06-06T02:00:00+00:00",
        "openmeteo_source_run_id": "om9-run",
        "openmeteo_source_available_at": "2026-06-06T03:00:00+00:00",
        "openmeteo_payload_json": "anchor.json",
        "precision_metadata_json": "precision.json",
        "bins": [{"bin_id": "warm", "lower_c": 20.0, "upper_c": 30.0}],
    }
    input_json = tmp_path / "request.json"
    input_json.write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "anchor.json").write_text("{}", encoding="utf-8")
    (tmp_path / "precision.json").write_text("{}", encoding="utf-8")
    receipt = cli._DurablePreparationReceipt(
        schema_ready=True,
        anchor_artifact_id=17,
        manifest_committed=True,
    )

    monkeypatch.setattr(cli, "extract_openmeteo_ecmwf_ifs9_localday_anchor", lambda *args, **kwargs: _anchor())
    monkeypatch.setattr(cli, "OpenMeteoIfs9PrecisionMetadata", lambda **kwargs: object())
    monkeypatch.setattr(cli, "evaluate_openmeteo_ecmwf_ifs9_precision_guard", lambda _metadata, **_kwargs: _precision_guard())
    monkeypatch.setattr(cli, "_bins", lambda _payload: _bins())
    monkeypatch.setattr(cli, "_prepare_live_schema_and_manifest", lambda *args, **kwargs: receipt)
    monkeypatch.setattr(
        cli,
        "_commit_from_read_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("posterior failed")
        ),
    )
    conn = sqlite3.connect(":memory:")

    returncode, response = cli._materialize(
        input_json,
        commit=True,
        init_schema=False,
        conn=conn,
        writer_lock=nullcontext,
    )
    conn.close()

    assert returncode == 2
    assert response["status"] == "ERROR"
    assert response["error"] == "posterior failed"
    assert response["posterior_committed"] is False
    assert response["retry_safe"] is True
    assert response["durable_preparation"] == {
        "schema_ready": True,
        "openmeteo_anchor_artifact_id": 17,
        "manifest_committed": True,
    }


def test_materialize_script_preserves_deadline_deferred_after_manifest(
    tmp_path, monkeypatch
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    payload = {
        "city": "Shanghai",
        "city_id": "Shanghai",
        "city_timezone": "Asia/Shanghai",
        "target_date": "2026-06-07",
        "temperature_metric": "high",
        "source_cycle_time": "2026-06-06T00:00:00+00:00",
        "computed_at": "2026-06-06T04:00:00+00:00",
        "expires_at": "2026-06-06T06:00:00+00:00",
        "baseline_source_run_id": "b0-run",
        "baseline_data_version": _current_baseline_data_version("high"),
        "baseline_source_available_at": "2026-06-06T02:00:00+00:00",
        "openmeteo_source_run_id": "om9-run",
        "openmeteo_source_available_at": "2026-06-06T03:00:00+00:00",
        "openmeteo_payload_json": "anchor.json",
        "precision_metadata_json": "precision.json",
        "bins": [{"bin_id": "warm", "lower_c": 20.0, "upper_c": 30.0}],
    }
    input_json = tmp_path / "request.json"
    input_json.write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "anchor.json").write_text("{}", encoding="utf-8")
    (tmp_path / "precision.json").write_text("{}", encoding="utf-8")
    receipt = cli._DurablePreparationReceipt(
        schema_ready=True,
        anchor_artifact_id=17,
        manifest_committed=True,
    )
    deadline_at = datetime.now(timezone.utc) + timedelta(seconds=30)

    monkeypatch.setattr(
        cli,
        "extract_openmeteo_ecmwf_ifs9_localday_anchor",
        lambda *args, **kwargs: _anchor(),
    )
    monkeypatch.setattr(cli, "OpenMeteoIfs9PrecisionMetadata", lambda **kwargs: object())
    monkeypatch.setattr(
        cli,
        "evaluate_openmeteo_ecmwf_ifs9_precision_guard",
        lambda _metadata, **_kwargs: _precision_guard(),
    )
    monkeypatch.setattr(cli, "_bins", lambda _payload: _bins())
    monkeypatch.setattr(
        cli, "_prepare_live_schema_and_manifest", lambda *args, **kwargs: receipt
    )
    monkeypatch.setattr(
        cli,
        "_commit_from_read_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            cli.MaterializationDeadlineExceeded("dependency_witness", deadline_at)
        ),
    )
    conn = sqlite3.connect(":memory:")

    with pytest.raises(cli.MaterializationDeadlineExceeded) as raised:
        cli._materialize(
            input_json,
            commit=True,
            init_schema=False,
            conn=conn,
            writer_lock=nullcontext,
        )
    conn.close()

    assert raised.value.stage == "dependency_witness"


def test_materialize_script_threads_day0_zero_observation_state(
    tmp_path,
    monkeypatch,
) -> None:
    import scripts.materialize_replacement_forecast_live as cli

    payload = {
        "city": "Shanghai",
        "city_id": "Shanghai",
        "city_timezone": "Asia/Shanghai",
        "target_date": "2026-06-07",
        "temperature_metric": "high",
        "source_cycle_time": "2026-06-06T00:00:00+00:00",
        "computed_at": "2026-06-06T18:00:00+00:00",
        "expires_at": "2026-06-08T00:00:00+00:00",
        "baseline_source_run_id": "b0-run",
        "baseline_data_version": _current_baseline_data_version("high"),
        "baseline_source_available_at": "2026-06-06T02:00:00+00:00",
        "openmeteo_source_run_id": "om9-run",
        "openmeteo_source_available_at": "2026-06-06T03:00:00+00:00",
        "openmeteo_payload_json": "anchor.json",
        "precision_metadata_json": "precision.json",
        "day0_observation_state": "zero_target_date_observations",
        "bins": [{"bin_id": "warm", "lower_c": 20.0, "upper_c": 30.0}],
    }
    input_json = tmp_path / "request.json"
    input_json.write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "anchor.json").write_text("{}", encoding="utf-8")
    (tmp_path / "precision.json").write_text("{}", encoding="utf-8")
    captured = []

    monkeypatch.setattr(
        cli,
        "extract_openmeteo_ecmwf_ifs9_localday_anchor",
        lambda *args, **kwargs: _anchor(),
    )
    monkeypatch.setattr(
        cli,
        "OpenMeteoIfs9PrecisionMetadata",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        cli,
        "evaluate_openmeteo_ecmwf_ifs9_precision_guard",
        lambda _metadata, **_kwargs: _precision_guard(),
    )
    monkeypatch.setattr(cli, "_bins", lambda _payload: _bins())
    monkeypatch.setattr(
        cli,
        "_dry_run_from_read_snapshot",
        lambda _conn, request: (
            captured.append(request)
            or cli.ReplacementForecastMaterializeResult(
                status="READY",
                reason_codes=(),
                posterior_id=1,
                anchor_id=1,
                readiness_id="ready-1",
            )
        ),
    )
    conn = sqlite3.connect(":memory:")

    returncode, response = cli._materialize(
        input_json,
        commit=False,
        init_schema=False,
        conn=conn,
    )
    conn.close()

    assert returncode == 0
    assert response["status"] == "READY"
    assert (
        captured[0].day0_observation_state
        == "zero_target_date_observations"
    )


@pytest.mark.usefixtures("_hko_source_surface")
def test_materialize_script_fails_closed_without_precision_metadata(tmp_path, monkeypatch) -> None:
    """Generic input-requiredness after real precision admission, not full q."""
    import scripts.materialize_replacement_forecast_live as cli
    conn, _raw, sealed, request = _normal_hko_cli_inputs(tmp_path, monkeypatch)
    (tmp_path / "openmeteo_payload.json").write_bytes(sealed)
    request["openmeteo_payload_json"] = "openmeteo_payload.json"
    input_json = tmp_path / "request.json"
    input_json.write_text(json.dumps(request), encoding="utf-8")
    admitted = []
    def checked_dry_run(actual_conn, actual_request):
        assert actual_conn is conn
        assert actual_request.openmeteo_raw_payload_bytes == sealed
        reasons = materializer_mod._precision_guard_block_reason(actual_request, actual_conn)
        assert reasons == ()
        admitted.append(actual_request)
        return materializer_mod.ReplacementForecastMaterializeResult(
            status="READY", reason_codes=reasons, posterior_id=None, anchor_id=None, readiness_id=None,
        )
    monkeypatch.setattr(cli, "_dry_run_from_read_snapshot", checked_dry_run)
    code, response = cli._materialize(input_json, commit=False, init_schema=False, conn=conn)
    assert code == 0, response
    assert len(admitted) == 1
    original_entities = tuple(tuple(row) for row in conn.execute(
        "SELECT * FROM raw_forecast_artifacts ORDER BY artifact_id"))
    assert original_entities
    conn.close()

    # Only the required key is removed after the same parser/real guard pass.
    del request["precision_metadata_json"]
    input_json.write_text(json.dumps(request), encoding="utf-8")
    test_state = tmp_path / "isolated-state"
    result = subprocess.run(
        [sys.executable, "scripts/materialize_replacement_forecast_live.py", "--input-json", str(input_json)],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "ZEUS_TEST_STATE_ROOT": str(test_state),
            "ZEUS_PRIMARY_ROOT": str(tmp_path),
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    payload = json.loads(result.stderr)
    assert payload["status"] == "ERROR"
    assert payload["error"] == "input JSON requires precision_metadata_json for Open-Meteo ECMWF IFS 9km anchor"
    assert (test_state / "zeus-world.db").is_file()
    assert (test_state / "zeus-forecasts.db").is_file()
    assert (tmp_path / "openmeteo_payload.json").read_bytes() == sealed
    with sqlite3.connect(f"file:{test_state / 'zeus-forecasts.db'}?mode=ro", uri=True) as after:
        after.execute("PRAGMA query_only=ON")
        assert tuple(after.execute("SELECT * FROM raw_forecast_artifacts ORDER BY artifact_id")) == original_entities


def test_boot_current_posterior_family_scan_uses_covering_index(
    tmp_path: Path,
) -> None:
    from src.data import replacement_forecast_production as production

    forecast_db = tmp_path / "forecasts.db"
    conn = sqlite3.connect(forecast_db)
    conn.executescript(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            runtime_layer TEXT NOT NULL,
            training_allowed INTEGER NOT NULL,
            q_json TEXT NOT NULL
        );
        CREATE INDEX idx_forecast_posteriors_runtime_layer_target
            ON forecast_posteriors(
                runtime_layer, city, target_date, temperature_metric, computed_at
            );
        """
    )
    conn.executemany(
        "INSERT INTO forecast_posteriors VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                1,
                "Paris",
                "2026-08-23",
                "high",
                "2026-08-23T09:00:00+00:00",
                "live",
                0,
                "x" * 100_000,
            ),
            (
                2,
                "Munich",
                "2026-08-23",
                "high",
                "2026-08-23T09:01:00+00:00",
                "live",
                0,
                "y" * 100_000,
            ),
            (
                3,
                "Paris",
                "2026-08-23",
                "high",
                "2026-08-23T09:02:00+00:00",
                "live",
                0,
                "z" * 100_000,
            ),
            (
                4,
                "Experiment",
                "2026-08-23",
                "high",
                "2026-08-23T09:03:00+00:00",
                "experiment",
                1,
                "e" * 100_000,
            ),
        ),
    )
    plan = tuple(
        str(row[3])
        for row in conn.execute(
            "EXPLAIN QUERY PLAN "
            + production._CURRENT_POSTERIOR_FAMILY_SCAN_SQL,
            (100,),
        )
    )
    conn.commit()
    conn.close()

    assert any(
        "USING COVERING INDEX idx_forecast_posteriors_runtime_layer_target"
        in detail
        for detail in plan
    )
    assert production._current_forecast_posterior_families(
        {"forecast_db": str(forecast_db)},
        limit=2,
    ) == (
        ("Paris", "2026-08-23", "high"),
        ("Munich", "2026-08-23", "high"),
    )


def test_seed_cycle_boundary_uses_ordered_live_family_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.data import replacement_forecast_live_materialization_queue as queue
    from src.data import replacement_input_hwm

    forecast_db = tmp_path / "forecasts.db"
    conn = sqlite3.connect(forecast_db)
    conn.executescript(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY,
            source_id TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            source_cycle_time TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            runtime_layer TEXT NOT NULL,
            q_json TEXT NOT NULL,
            provenance_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX idx_forecast_posteriors_runtime_layer_target
            ON forecast_posteriors(
                runtime_layer, city, target_date, temperature_metric, computed_at
            );
        """
    )
    conn.executemany(
        """INSERT INTO forecast_posteriors (
            posterior_id, source_id, city, target_date, temperature_metric,
            source_cycle_time, computed_at, runtime_layer, q_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            (
                1,
                queue.SOURCE_ID,
                "Ankara",
                "2026-08-23",
                "high",
                "2026-08-23T06:00:00+00:00",
                "2026-08-23T08:00:00+00:00",
                "live",
                "x" * 100_000,
            ),
            (
                2,
                queue.SOURCE_ID,
                "Ankara",
                "2026-08-23",
                "high",
                "2026-08-23T18:00:00+00:00",
                "2026-08-23T09:00:00+00:00",
                "offline",
                "y" * 100_000,
            ),
        ),
    )
    conn.commit()
    plan = "\n".join(
        str(row[3])
        for row in conn.execute(
            "EXPLAIN QUERY PLAN " + queue._CURRENT_LIVE_POSTERIOR_CYCLE_SQL,
            (queue.SOURCE_ID, "Ankara", "2026-08-23", "high"),
        ).fetchall()
    )
    conn.close()
    monkeypatch.setattr(
        replacement_input_hwm,
        "latest_eligible_ensemble_input_cycle",
        lambda *_args, **_kwargs: datetime(
            2026, 8, 23, 12, tzinfo=timezone.utc
        ),
    )

    boundary = queue._seed_source_cycle_boundary(
        forecast_db=forecast_db,
        seed={
            "city": "Ankara",
            "target_date": "2026-08-23",
            "temperature_metric": "high",
            "source_cycle_time": "2026-08-23T12:00:00+00:00",
        },
    )

    assert boundary is None
    assert "USING INDEX idx_forecast_posteriors_runtime_layer_target" in plan
    assert "USE TEMP B-TREE FOR ORDER BY" not in plan


def _no_center_debias(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent fitted correction for the equivalence baseline."""

    monkeypatch.setattr(
        materializer_mod.center_debias_live_fit.PROVIDER,
        "correction",
        lambda *args, **kwargs: None,
    )


def _fixed_center_debias(
    monkeypatch: pytest.MonkeyPatch, *, shift_c: float, metric: str = "high"
) -> None:
    """Serve ``shift_c`` for ``metric`` and nothing for any other metric."""

    correction = materializer_mod.center_debias_live_fit.CenterDebiasCorrection(
        shift_c=shift_c,
        param_hash="c" * 64,
        training_cutoff="2026-06-07T00:00:00Z",
        n_rows=4204,
    )
    enabled_metric = metric

    def _correction(conn, *, city, metric, now):
        return correction if metric == enabled_metric else None

    monkeypatch.setattr(
        materializer_mod.center_debias_live_fit.PROVIDER, "correction", _correction
    )


def _materialize_q(
    conn: sqlite3.Connection, request
) -> tuple[dict, dict]:
    result = materialize_replacement_forecast_live(conn, request)
    assert result.ok is True
    row = conn.execute(
        "SELECT q_json, provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
        (result.posterior_id,),
    ).fetchone()
    return json.loads(row["q_json"]), json.loads(row["provenance_json"])


@pytest.mark.usefixtures("_hko_source_surface")
def test_current_center_ignores_fitted_debias_and_keeps_raw_precision_center(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fitted HIGH shift must not move a current-evidence precision center."""

    request = _hko_request()
    baseline_conn = _conn()
    request = _install_hko_live_fusion(monkeypatch, conn=baseline_conn, request=request)
    _no_center_debias(monkeypatch)
    baseline_q, baseline_provenance = _materialize_q(baseline_conn, request)

    shifted_conn = _conn()
    shifted_request = _install_hko_live_fusion(monkeypatch, conn=shifted_conn, request=request)
    _fixed_center_debias(monkeypatch, shift_c=1.0)
    shifted_q, shifted_provenance = _materialize_q(shifted_conn, shifted_request)

    # The fused center is 25.0 with bins cool(<20) / warm(21-30) / hot(>31): a +1.0
    # shift moves the center to 26.0, so mass leaves the cool tail for the hot one.
    assert shifted_q == baseline_q

    assert shifted_provenance["center_debias_c"] is None
    assert shifted_provenance["center_debias_param_hash"] is None
    assert shifted_provenance["center_debias_training_cutoff"] is None
    assert shifted_provenance["bayes_precision_fusion"]["center_debias_c"] is None

    # anchor_value_c stays the UNCORRECTED fused center — the fitter's residual
    # basis. Storing the corrected center here would make the next fit measure an
    # already-corrected error and unwind the correction.
    assert shifted_provenance["anchor_value_c"] == pytest.approx(25.0)
    assert baseline_provenance["anchor_value_c"] == pytest.approx(25.0)
    assert shifted_provenance["bayes_precision_fusion"]["anchor_value_c"] == (
        pytest.approx(25.0)
    )


@pytest.mark.usefixtures("_hko_source_surface")
def test_center_debias_inactive_metric_is_byte_identical_to_no_correction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LOW is the negative control: no shift, and provenance says so explicitly."""

    request = replace(_hko_request(baseline_data_version=_current_baseline_data_version("low")), temperature_metric="low")
    baseline_conn = _conn()
    request = _install_hko_live_fusion(monkeypatch, conn=baseline_conn, request=request)
    _no_center_debias(monkeypatch)
    baseline_q, baseline_provenance = _materialize_q(
        baseline_conn, request,
    )
    baseline_hash = baseline_conn.execute(
        "SELECT posterior_config_hash FROM forecast_posteriors"
    ).fetchone()["posterior_config_hash"]

    # HIGH is enabled and would receive +1.0; this request is LOW, so it must not.
    # Only the HIGH-only fitted knob changes, not the certificate's physical
    # namespace, own artifact, or immutable capture/possession dependencies.
    low_conn = baseline_conn
    _install_hko_live_fusion(monkeypatch, conn=low_conn, request=request)
    _fixed_center_debias(monkeypatch, shift_c=1.0, metric="high")
    low_q, low_provenance = _materialize_q(
        low_conn, request,
    )
    low_hash = low_conn.execute(
        "SELECT posterior_config_hash FROM forecast_posteriors"
    ).fetchone()["posterior_config_hash"]

    assert low_q == baseline_q
    assert low_provenance["center_debias_c"] is None
    assert low_provenance["center_debias_param_hash"] is None
    assert low_provenance["center_debias_training_cutoff"] is None
    # A fail-open row must not churn the config identity of every untouched row.
    assert low_hash == baseline_hash


@pytest.mark.usefixtures("_hko_source_surface")
def test_served_settlement_log_probability_mu_matches_served_mu_anchor_with_debias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-2 fit/serve parity: the helper's ``mu`` must equal the served ``_mu_anchor``.

    Current ``_mu_anchor`` is the raw precision center; a fitted ``center_debias_c``
    is deliberately inert under current-evidence semantics,
    applied BEFORE the Day0 delta. ``served_settlement_log_probability`` reconstructs that
    same sum from the two provenance fields it is fed. Reproducing the served "hot" bin
    probability from provenance alone -- with no access to the live ``_mu_anchor`` variable --
    is only possible if the helper's internal mu is that same value; a stale/uncorrected mu
    would change the integrated probability for this bin (the fused center sits ~2.5 sigma
    below the bin edge, where the Normal CDF is steep) and the assertion below would fail.
    """

    request = _hko_request()
    conn = _conn()
    request = _install_hko_live_fusion(monkeypatch, conn=conn, request=request)
    _fixed_center_debias(monkeypatch, shift_c=1.0)
    q, provenance = _materialize_q(conn, request)

    bpf = provenance["bayes_precision_fusion"]
    assert provenance["center_debias_c"] is None
    # Inert calibration-layer knobs in this fixture -- k=1.0, w=0, no floor, no Day0 -- so the
    # helper's excluded terms cannot be masking a mu mismatch.
    assert provenance["sigma_scale_k_applied"] is None
    assert provenance["uniform_mixture_w_applied"] is None
    assert "day0_conditioning" not in provenance

    log_p = materializer_mod.served_settlement_log_probability(
        anchor_value_c=float(bpf["anchor_value_c"]),
        center_debias_c=float(bpf["center_debias_c"] or 0.0),
        predictive_sigma_c=float(bpf["predictive_sigma_c"]),
        k=1.0,
        metric="high",
        bin_low_c=31.0,
        bin_high_c=None,
        half_step=0.5,
        rounding_rule="wmo_half_up",
        day0_observed_extreme_c=None,
        day0_center_delta_c=0.0,
    )

    assert math.exp(log_p) == pytest.approx(q["hot"], rel=1e-9)


def _coordinate_bound_frontier_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE ensemble_snapshots (
            snapshot_id INTEGER PRIMARY KEY,
            city TEXT, target_date TEXT, temperature_metric TEXT,
            dataset_id TEXT, source_id TEXT, model_version TEXT, authority TEXT,
            causality_status TEXT, boundary_ambiguous INTEGER,
            forecast_window_attribution_status TEXT,
            contributes_to_target_extrema INTEGER,
            source_cycle_time TEXT, issue_time TEXT,
            source_available_at TEXT, available_at TEXT,
            members_json TEXT, members_unit TEXT, provenance_json TEXT,
            source_run_id TEXT
        )
        """
    )
    return conn


def _insert_coordinate_bound_frontier_row(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    dataset_id: str,
    metric: str,
    available_at: datetime,
) -> None:
    conn.execute(
        """
        INSERT INTO ensemble_snapshots VALUES (?, 'Shanghai', '2026-06-07', ?,
            ?, 'ecmwf_open_data', 'ecmwf_ens', 'VERIFIED', 'OK', 0,
            'FULLY_INSIDE_TARGET_LOCAL_DAY', 1,
            '2026-06-06T00:00:00+00:00', '2026-06-06T00:00:00+00:00',
            ?, ?, '[20.0,21.0]', 'degC', ?, NULL)
        """,
        (snapshot_id, metric, dataset_id, available_at.isoformat(), available_at.isoformat(),
         _fixture_ens_surface_provenance()),
    )


@pytest.mark.parametrize(
    ("metric", "base"),
    (
        ("high", "ecmwf_opendata_mx2t3_local_calendar_day_max_boundary_land_grid_v3"),
        ("low", "ecmwf_opendata_mn2t3_local_calendar_day_min_window_land_grid_v3"),
    ),
)
def test_current_evidence_uses_only_the_request_coordinate_dataset(
    monkeypatch: pytest.MonkeyPatch,
    metric: str,
    base: str,
) -> None:
    from hashlib import sha256

    from src.contracts.ensemble_snapshot_provenance import coordinate_bound_data_version

    import src.config as config

    current_manifest = '{"coordinate_profile":"current"}'
    current_data_version = coordinate_bound_data_version(
        base, sha256(current_manifest.encode("utf-8")).hexdigest()
    )
    old_data_version = coordinate_bound_data_version(base, "a" * 64)
    monkeypatch.setattr(
        config,
        "runtime_coordinate_manifest_json",
        lambda: current_manifest,
        raising=False,
    )
    monkeypatch.setattr(
        materializer_mod,
        "ensemble_source_authority_sql",
        lambda **_kwargs: ("1 = 1", ()),
    )
    conn = _coordinate_bound_frontier_conn()
    _insert_coordinate_bound_frontier_row(
        conn, snapshot_id=101, dataset_id=current_data_version,
        metric=metric, available_at=_dt(3),
    )
    # Neither a previous profile nor the legacy base may win through a later
    # arrival within the same source cycle.
    _insert_coordinate_bound_frontier_row(
        conn, snapshot_id=102, dataset_id=old_data_version,
        metric=metric, available_at=_dt(3, 59),
    )
    _insert_coordinate_bound_frontier_row(
        conn, snapshot_id=103, dataset_id=base,
        metric=metric, available_at=_dt(3, 58),
    )
    if metric == "high":
        uncertified = coordinate_bound_data_version(
            "ecmwf_opendata_mx2t3_local_calendar_day_max",
            sha256(current_manifest.encode("utf-8")).hexdigest(),
        )
        _insert_coordinate_bound_frontier_row(
            conn, snapshot_id=104, dataset_id=uncertified,
            metric=metric, available_at=_dt(3, 59),
        )
    request = replace(
        _request(), temperature_metric=metric,
        baseline_data_version=current_data_version,
    )

    identity = materializer_mod.read_current_evidence_snapshot_identity(
        conn, request, metric=metric
    )
    assert identity is not None
    assert identity.snapshot_id == 101
    assert materializer_mod.read_current_evidence_snapshot_id(
        conn, request, metric=metric
    ) == 101
    assert materializer_mod.read_current_evidence_snapshot_id(
        conn,
        replace(request, baseline_data_version=old_data_version),
        metric=metric,
    ) is None
    if metric == "high":
        assert materializer_mod.read_current_evidence_snapshot_id(
            conn, replace(request, baseline_data_version=uncertified), metric=metric,
        ) is None


def test_current_evidence_requires_current_profile_and_point_in_time_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hashlib import sha256

    from src.contracts.ensemble_snapshot_provenance import (
        ECMWF_OPENDATA_HIGH_DATA_VERSION,
        coordinate_bound_data_version,
    )

    import src.config as config

    manifest = '{"coordinate_profile":"current"}'
    data_version = coordinate_bound_data_version(
        ECMWF_OPENDATA_HIGH_DATA_VERSION,
        sha256(manifest.encode("utf-8")).hexdigest(),
    )
    monkeypatch.setattr(
        config, "runtime_coordinate_manifest_json", lambda: manifest, raising=False
    )
    monkeypatch.setattr(
        materializer_mod,
        "ensemble_source_authority_sql",
        lambda **_kwargs: ("1 = 1", ()),
    )
    conn = _coordinate_bound_frontier_conn()
    request = replace(_request(), baseline_data_version=data_version)
    _insert_coordinate_bound_frontier_row(
        conn, snapshot_id=101, dataset_id=data_version,
        metric="high", available_at=_dt(5),
    )

    assert materializer_mod.read_current_evidence_snapshot_id(
        conn, request, metric="high"
    ) is None
    monkeypatch.delattr(config, "runtime_coordinate_manifest_json", raising=False)
    assert materializer_mod.read_current_evidence_snapshot_id(
        conn, request, metric="high"
    ) is None


@pytest.mark.parametrize("entrypoint", ["prepare_replacement_forecast_live", "compute_replacement_posterior_readonly"])
@pytest.mark.parametrize("caller_transaction", [False, True])
@pytest.mark.parametrize("computation_fails", [False, True])
def test_readonly_materialization_keeps_source_and_witness_on_one_snapshot(
    tmp_path, monkeypatch, entrypoint, caller_transaction, computation_fails
):
    from src.data import replacement_forecast_materializer as materializer

    path = tmp_path / "source-snapshot.db"
    reader = sqlite3.connect(path)
    reader.execute("PRAGMA journal_mode=WAL")
    reader.execute("CREATE TABLE vector_revisions (identity INTEGER NOT NULL)")
    reader.execute("INSERT INTO vector_revisions VALUES (1)")
    reader.commit()
    reader.execute("PRAGMA query_only=ON")
    writer = sqlite3.connect(path)
    seen = []
    result = object()
    request = object()

    def validate(conn, incoming):
        assert incoming is request
        seen.append(conn.execute("SELECT MAX(identity) FROM vector_revisions").fetchone()[0])
        writer.execute("INSERT INTO vector_revisions VALUES (2)")
        writer.commit()
        return incoming, "high"

    def compute(conn, incoming, **kwargs):
        seen.append(conn.execute("SELECT MAX(identity) FROM vector_revisions").fetchone()[0])
        if computation_fails:
            raise ValueError("test computation interrupted")
        return result

    monkeypatch.setattr(materializer, "_validated_replacement_forecast_request", validate)
    monkeypatch.setattr(materializer, "_day0_ledger_frontier_identity", lambda *args, **kwargs: None)
    monkeypatch.setattr(materializer, "_compute_posterior_payload", compute)
    try:
        if caller_transaction:
            reader.execute("BEGIN")
        call = getattr(materializer, entrypoint)
        if computation_fails:
            with pytest.raises(ValueError, match="test computation interrupted"):
                call(reader, request)
        else:
            actual = call(reader, request)
            assert (actual.posterior if entrypoint.startswith("prepare_") else actual) is result
        assert seen == [1, 1]
        assert reader.in_transaction is caller_transaction
        if caller_transaction:
            assert reader.execute("SELECT MAX(identity) FROM vector_revisions").fetchone()[0] == 1
            reader.rollback()
        assert reader.execute("SELECT MAX(identity) FROM vector_revisions").fetchone()[0] == 2
    finally:
        writer.close()
        reader.close()


@pytest.mark.parametrize("metric", ["high", "low"])
@pytest.mark.parametrize("carrier_hour,anchor_hour", [(6, 12), (12, 6), (6, 6)])
def test_independent_current_anchor_clock_preserves_prewrite_and_anchor_identity(
    metric, carrier_hour, anchor_hour
) -> None:
    request = replace(
        _request(source_cycle_time=_dt(carrier_hour), computed_at=_dt(14),
                 expires_at=_dt(15), baseline_source_available_at=_dt(13),
                 openmeteo_source_available_at=_dt(13)),
        temperature_metric=metric,
        baseline_data_version=_current_baseline_data_version(metric),
        openmeteo_anchor=_anchor(source_cycle_time=_dt(anchor_hour)),
    )
    assert materializer_mod._prewrite_block_reasons(request) == ()
    conn = _conn()
    try:
        anchor_id = materializer_mod._insert_anchor(conn, request, metric=metric)
        row = conn.execute(
            "SELECT source_cycle_time FROM deterministic_forecast_anchors WHERE anchor_id = ?",
            (anchor_id,),
        ).fetchone()
        assert row[0] == _dt(anchor_hour).isoformat()
        # A new ENS carrier cannot change the identity of the same provider fact.
        assert materializer_mod._insert_anchor(
            conn, replace(request, source_cycle_time=_dt(12)), metric=metric
        ) == anchor_id
    finally:
        conn.close()


@pytest.mark.parametrize("anchor_cycle", [_dt(6), _dt(0) - timedelta(days=3)])
def test_independent_anchor_clock_does_not_launder_future_or_stale_input(anchor_cycle) -> None:
    request = replace(_request(), openmeteo_anchor=_anchor(source_cycle_time=anchor_cycle))
    reasons = materializer_mod._prewrite_block_reasons(request)
    expected = (
        "REPLACEMENT_MATERIALIZATION_OM9_SOURCE_CYCLE_TIME_IN_FUTURE"
        if anchor_cycle > request.computed_at
        else "REPLACEMENT_MATERIALIZATION_OM9_SOURCE_CYCLE_TOO_STALE"
    )
    assert expected in reasons


def test_seed_cycle_boundary_allows_only_proven_retired_low_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An old 18Z LOW cannot suppress exact current window-v2 12Z work."""
    from src.data import replacement_forecast_live_materialization_queue as queue
    from src.data import replacement_input_hwm

    db = tmp_path / "forecast.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
      CREATE TABLE forecast_posteriors (posterior_id INTEGER PRIMARY KEY, source_id TEXT, city TEXT,
        target_date TEXT, temperature_metric TEXT, source_cycle_time TEXT, computed_at TEXT,
        runtime_layer TEXT, provenance_json TEXT);
      CREATE INDEX idx_forecast_posteriors_runtime_layer_target ON forecast_posteriors(runtime_layer,city,target_date,temperature_metric,computed_at);
      INSERT INTO forecast_posteriors VALUES (1,'openmeteo_ecmwf_ifs9_bayes_fusion','Seoul','2026-09-23','low','2026-09-22T18:00:00+00:00','2026-09-23T05:05:10+00:00','live','{}');
    """)
    conn.close()
    monkeypatch.setattr(replacement_input_hwm, "latest_eligible_ensemble_input_cycle", lambda *_a, **_k: datetime(2026,9,22,12,tzinfo=timezone.utc))
    monkeypatch.setattr(replacement_input_hwm, "retired_low_uncertified_incumbent_yields_to_current_ensemble", lambda *_a, **_k: True)
    seed = {'city':'Seoul','target_date':'2026-09-23','temperature_metric':'low','source_cycle_time':'2026-09-22T12:00:00+00:00','baseline_source_run_id':'v2-12'}
    assert queue._seed_source_cycle_boundary(forecast_db=db, seed=seed) is None
    monkeypatch.setattr(replacement_input_hwm, "retired_low_uncertified_incumbent_yields_to_current_ensemble", lambda *_a, **_k: False)
    assert queue._seed_source_cycle_boundary(forecast_db=db, seed=seed) == ('current_posterior','2026-09-22T18:00:00+00:00')


def _low_revision_authority_conn(db_path: Path | None = None, *, include_legacy_provider_fixtures: bool = True,
                                 city_name="Hong Kong", include_retired_incumbent=True,
                                 target_date=date(2026,10,1), source_cycle=None, run_prefix="", snapshot_offset=0,
                                 include_current_fixture=True) -> sqlite3.Connection:
    """Canonical run/coverage/ENS schema with controlled members, not GRIB capture."""
    from src.contracts.ensemble_snapshot_provenance import (
        ECMWF_OPENDATA_LOW_DATA_VERSION_UNCERTIFIED,
        coordinate_bound_data_version,
    )
    from src.config import runtime_cities_by_name
    from src.data.forecast_target_contract import compute_target_local_day_window_utc
    city = runtime_cities_by_name()[city_name]
    city_id = city_name if city_name == "Hong Kong" else city_name.upper().replace(" ", "_")
    source_cycle = source_cycle or _hko_dt(12)
    window = compute_target_local_day_window_utc(city_timezone=city.timezone,
                                               target_local_date=target_date)
    selected_coords = None if city_name == "Hong Kong" else (round(city.lat*4)/4, round(city.lon*4)/4)
    assert include_legacy_provider_fixtures is False or city_name == "Hong Kong"
    if include_retired_incumbent:
        assert (target_date,source_cycle,run_prefix,snapshot_offset) == (date(2026,10,1),_hko_dt(12),"",0)

    if db_path is None:
        conn = _conn()
    else:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        apply_canonical_schema(conn, forecast_tables=True)
        _create_readiness_state(conn)
    _create_source_run(conn)
    _create_source_run_coverage(conn)
    current_version = _current_baseline_data_version("low")
    old_version = coordinate_bound_data_version(
        ECMWF_OPENDATA_LOW_DATA_VERSION_UNCERTIFIED,
        current_version.rsplit("__coordsha_", 1)[1],
    )
    expected = expected_replacement_dependency_identity_by_role("low")["baseline_b0"]
    for run_id, snapshot_id, hour, version in (
        ("old18", 18, 18, old_version),
        ("new12", 12, 12, current_version),
    ):
        if run_id == "old18" and not include_retired_incumbent:
            continue
        is_current = run_id == "new12"
        if is_current and not include_current_fixture:
            continue
        run_id = run_prefix+run_id
        snapshot_id += snapshot_offset
        cycle = source_cycle+timedelta(hours=hour-12)
        issued = cycle + timedelta(minutes=5)
        track = "mn2t6_low_short_horizon"
        release_key = "ecmwf_open_data:mn2t6_low_short_horizon"
        write_source_run(
            conn, source_run_id=run_id, source_id="ecmwf_open_data",
            track=track, release_calendar_key=release_key,
            source_cycle_time=cycle, source_available_at=issued,
            fetch_finished_at=issued, captured_at=issued, imported_at=issued,
            target_local_date=target_date.isoformat(), city_id=city_id,
            city_timezone=city.timezone, temperature_metric="low",
            physical_quantity=expected.physical_quantity,
            observation_field=expected.observation_field, data_version=version,
            expected_members=51, observed_members=51,
            expected_steps_json=[0, 3, 6], observed_steps_json=[0, 3, 6],
            expected_count=3, observed_count=3,
            status="SUCCESS", completeness_status="COMPLETE",
        )
        conn.execute(
            """INSERT INTO source_run_coverage (
                coverage_id, source_run_id, source_id, source_transport,
                release_calendar_key, track, city_id, city, city_timezone,
                target_local_date, temperature_metric, physical_quantity,
                observation_field, data_version, expected_members, observed_members,
                expected_steps_json, observed_steps_json, snapshot_ids_json,
                target_window_start_utc, target_window_end_utc,
                completeness_status, readiness_status, computed_at, expires_at,
                recorded_at
            ) VALUES (?, ?, 'ecmwf_open_data', 'native_grib', ?, ?,
                      ?, ?, ?, ?,
                      'low', ?, ?, ?, 51, 51, '[0,3,6]', '[0,3,6]', ?,
                      ?, ?,
                      'COMPLETE', 'LIVE_ELIGIBLE', ?,
                      ?, ?)""",
            (f"coverage-{run_id}", run_id, release_key, track, city_id, city_name, city.timezone,target_date.isoformat(),
             expected.physical_quantity, expected.observation_field, version,
             json.dumps([snapshot_id]), window.start_utc.isoformat(), window.end_utc.isoformat(),
             issued.isoformat(), (source_cycle+timedelta(hours=15)).isoformat(), issued.isoformat()),
        )
        conn.execute(
            """INSERT INTO ensemble_snapshots (
                snapshot_id, city, target_date, temperature_metric,
                physical_quantity, observation_field, issue_time, available_at,
                fetch_time, lead_hours, members_json, model_version, dataset_id,
                source_id, source_run_id, source_cycle_time, source_available_at,
                authority, causality_status, boundary_ambiguous,
                forecast_window_attribution_status, contributes_to_target_extrema,
                members_unit, recorded_at
            ) VALUES (?, ?, ?, 'low', ?, ?, ?, ?, ?, 24,
                      ?, 'ecmwf_ens', ?, 'ecmwf_open_data', ?, ?, ?,
                      'VERIFIED', 'OK', 0, 'FULLY_INSIDE_TARGET_LOCAL_DAY',
                      1, 'degC', ?)""",
            (snapshot_id, city_name,target_date.isoformat(), expected.physical_quantity, expected.observation_field,
             cycle.isoformat(), issued.isoformat(), issued.isoformat(),
             json.dumps([19.0 + index * 0.01 for index in range(51)]), version,
             run_id, cycle.isoformat(), issued.isoformat(), issued.isoformat()),
        )
        if is_current:
            conn.execute(
                "UPDATE ensemble_snapshots SET provenance_json=? WHERE snapshot_id=?",
                (_fixture_ens_surface_provenance(city_name=city_name, cycle=cycle.isoformat(),
                    selected_coords=selected_coords,decision_at=issued), snapshot_id),
            )
    legacy_models = ("ecmwf_ifs9", "gfs", "icon", "gem", "jma") if include_legacy_provider_fixtures else ()
    for raw_id, model in enumerate(legacy_models, 101):
        conn.execute(
            """INSERT INTO raw_model_forecasts (
                raw_model_forecast_id, model, city, target_date, metric,
                source_cycle_time, source_available_at, captured_at, lead_days,
                forecast_value_c, endpoint
            ) VALUES (?, ?, 'Hong Kong', '2026-10-01', 'low', ?, ?, ?, 1,
                      22.0, 'single_runs')""",
            (raw_id, model, _hko_dt(12).isoformat(),
             _hko_dt(12, 5).isoformat(), _hko_dt(12, 5).isoformat()),
        )
    if not include_retired_incumbent:
        assert not include_legacy_provider_fixtures
        conn.commit()
        return conn
    soft = expected_replacement_dependency_identity_by_role("low")["soft_anchor_posterior"]
    conn.execute(
        """INSERT INTO forecast_posteriors (
            source_id, product_id, data_version, city, target_date,
            temperature_metric, source_cycle_time, source_available_at,
            computed_at, q_json, q_lcb_json, posterior_method,
            dependency_source_run_ids_json, provenance_json, runtime_layer
        ) VALUES (?, 'old-soft', ?, ?, '2026-10-01', 'low',
                  '2026-09-30T18:00:00+00:00', '2026-09-30T18:05:00+00:00',
                  '2026-09-30T19:00:00+00:00', '{}', '{}', 'old-uncertified',
                  ?, ?, 'live')""",
        (soft.source_id, soft.data_version, city_name,
         json.dumps({"baseline_b0": "old18", "current_ensemble_snapshot": 18}),
         json.dumps({"bayes_precision_fusion": {"current_evidence_shape": {"snapshot_id": 18}}})),
    )
    incumbent_id = conn.execute(
        "SELECT posterior_id FROM forecast_posteriors WHERE source_cycle_time=?",
        (_hko_dt(18).isoformat(),),
    ).fetchone()[0]
    write_readiness_state(
        conn, readiness_id="old-uncertified-readiness", scope_type="strategy",
        status="READY", computed_at=_hko_dt(19), city_id=city_id,
        city=city_name, city_timezone=city.timezone,
        target_local_date="2026-10-01", metric="low", temperature_metric="low",
        physical_quantity=soft.physical_quantity,
        observation_field=soft.observation_field,
        data_version=materializer_mod.LOW_DATA_VERSION,
        source_id=soft.source_id, track="soft_anchor_posterior",
        source_run_id=f"posterior:{incumbent_id}", strategy_key=STRATEGY_KEY,
        expires_at=_hko_dt(22),
    )
    if include_legacy_provider_fixtures:
        _qualify_raw_fixture_rows(conn)
    conn.commit()
    return conn


def _low_revision_request() -> ReplacementForecastMaterializeRequest:
    from src.contracts.replacement_pipeline_files import (
        DAY0_OBSERVATION_STATE_ZERO_TARGET_DATE_OBSERVATIONS,
    )

    return replace(
        _hko_request(source_cycle_time=_hko_dt(12), computed_at=_hko_dt(20),
                 expires_at=_hko_dt(22), baseline_source_run_id="new12",
                 baseline_source_available_at=_hko_dt(12, 5),
                 day0_observation_state=DAY0_OBSERVATION_STATE_ZERO_TARGET_DATE_OBSERVATIONS),
        temperature_metric="low",
        baseline_data_version=_current_baseline_data_version("low"),
    )


def _normal_low_revision_migration_fixture(monkeypatch):
    """Retain old 18Z; acquire the actual 12Z window only after safe fetch.

    The legacy 12:05 diagnostic setup remains available to its structural
    tests, but is not updated or relabeled into this normal native world.
    """
    conn = _low_revision_authority_conn(include_current_fixture=False)
    from src.data import ecmwf_open_data as native_writer
    _, early_release = native_writer._select_cycle_for_track(track="mn2t6_low",now_utc=_hko_dt(12,5))
    assert early_release["selected_cycle_time"] != _hko_dt(12)
    assert conn.execute("SELECT 1 FROM ensemble_snapshots WHERE snapshot_id=12").fetchone() is None
    old_snapshot = tuple(conn.execute("SELECT * FROM ensemble_snapshots WHERE snapshot_id=18").fetchone())
    old_posterior = tuple(conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=1").fetchone())
    request = _install_hko_live_fusion(monkeypatch,conn=conn,request=_low_revision_request())
    shape = materializer_mod._replacement_bayes_precision_fusion_override().current_evidence_shape
    native = conn.execute("SELECT * FROM ensemble_snapshots WHERE snapshot_id=?",(shape["snapshot_id"],)).fetchone()
    assert native["source_release_time"] == _hko_dt(18,40).isoformat()
    assert _hko_dt(18,40) < datetime.fromisoformat(native["recorded_at"]) <= request.computed_at
    assert native["source_cycle_time"] == _hko_dt(12).isoformat()
    assert tuple(conn.execute("SELECT * FROM ensemble_snapshots WHERE snapshot_id=18").fetchone()) == old_snapshot
    assert tuple(conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=1").fetchone()) == old_posterior
    request = replace(request,baseline_source_run_id=native["source_run_id"],
        baseline_source_available_at=datetime.fromisoformat(native["source_available_at"]))
    return conn,request


@pytest.mark.usefixtures("_hko_source_surface")
def test_normal_wmd_dual_body_writer_materializes_public_target_scoped_probability(tmp_path, monkeypatch):
    """Actual retained LFPB bodies; controlled forecast/51 ENS, not live GRIB.

    TEST_ONLY_SYNTHETIC_EXTERNAL_CONDITION future WMDR deployment is a newly
    acquired input. It never licenses a future site as today's q geometry.
    """
    from src.config import runtime_cities_by_name
    from src.data import bayes_precision_fusion_download as dl, station_ground_evidence as ground
    from src.data.openmeteo_ecmwf_ifs9_anchor import (
        OpenMeteoEcmwfIfs9AnchorRequest, build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest,
    )
    from src.data.openmeteo_ecmwf_ifs9_bucket_transport import source_cell_geometry_proof
    from src.data.raw_forecast_artifact_manifest import write_manifest_to_db, write_manifest
    from src.data.replacement_forecast_materialization_request_builder import (
        build_replacement_forecast_materialization_request, build_materialize_request_dataclass,
    )
    from src.data.replacement_forecast_bundle_reader import (
        ReplacementForecastAuthorityPurpose, read_replacement_forecast_bundle,
    )
    from src.data.replacement_forecast_readiness import ReplacementForecastReadinessDecision
    from src.data import replacement_forecast_bundle_reader as bundle_reader
    from scripts.download_replacement_forecast_current_targets import _precision_metadata
    from tests.test_config import _official_wmd_registry
    from tests.test_openmeteo_cell_selection_and_elevation_are_product_identity import _selected_test_cell
    from tests.test_station_ground_evidence import _wmd_known_periods

    registry, primary, bridge, claims = _official_wmd_registry(tmp_path, monkeypatch, "Paris")
    original_primary = primary.read_bytes()
    city = runtime_cities_by_name()["Paris"]
    assert city.wu_station == "LFPB"
    target = date(2026,10,1)
    cycle = _hko_dt(12)
    baseline_run = "new12"
    capture = cycle+timedelta(minutes=10)
    db = tmp_path / "wmd-forecast.db"
    conn = _low_revision_authority_conn(db, include_legacy_provider_fixtures=False,
        city_name=city.name, include_retired_incumbent=False)
    ground_clock = [_hko_dt(19, 59)]
    class GroundClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return ground_clock[0].astimezone(tz or UTC)
    monkeypatch.setattr(ground, "datetime", GroundClock)
    ground_a = ground.archive_station_ground_evidence(db, [city.name])["archived"][city.name]
    assert len(ground_a["input_bodies"]) == 2
    assert ground_a["facts"]["elevation_m"] == 67.0
    # TEST_ONLY_CONTROLLED_CANONICAL_INSERT_CLOCK: actual private INSERT time,
    # not an UPDATE of a licensed row or a clock sealed before insertion.
    sql_clock = [_hko_dt(12, 11)]
    builtins = sqlite3.connect(":memory:")
    conn.create_function("strftime", 2, lambda fmt, value:
        sql_clock[0].isoformat(timespec="milliseconds")
        if (fmt, value) == ("%Y-%m-%dT%H:%M:%f+00:00", "now")
        else builtins.execute("SELECT strftime(?,?)", (fmt,value)).fetchone()[0])
    class DownloadClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return capture.astimezone(tz or UTC)
    monkeypatch.setattr(dl, "datetime", DownloadClock)
    def hourly_payload(lat, lon, value):
        return {"latitude":lat, "longitude":lon, "elevation":32.0,
            "timezone":city.timezone, "utc_offset_seconds":7200,
            "hourly_units":{"temperature_2m":"°C"},
            "hourly":{"time":[f"{target.isoformat()}T{hour:02d}:00" for hour in range(24)],
                      "temperature_2m":[value]*24}}
    def fetch(url, params, **kwargs):
        selected = _selected_test_cell(params["models"], city.lat, city.lon)
        body = (json.dumps(hourly_payload(*selected, 22.0 if params["models"]=="icon_global" else 24.0))+"\n").encode()
        kwargs["capture_entity_body"](body, capture.timestamp())
        kwargs["capture_network_response"](body, capture.timestamp(), {"content-type":"application/json"})
        return json.loads(body)
    monkeypatch.setattr("src.data.openmeteo_client.fetch", fetch)
    dl.download_bayes_precision_fusion_extra_raw_inputs(forecast_db=db, cycle=_hko_dt(12),
        targets=[dl.BayesPrecisionFusionDownloadTarget(city=city.name, metric="low",
            target_date="2026-10-01", lead_days=1, latitude=city.lat, longitude=city.lon,
            timezone_name=city.timezone)], models=("icon_global", "ukmo_global_deterministic_10km"),
        include_previous_runs=False, prune_after=False)
    cell = source_cell_geometry_proof(latitude=city.lat, longitude=city.lon, target_elevation_m=32)
    payload = hourly_payload(cell["selected_grid_lat"], cell["selected_grid_lon"], 18.5)
    payload["_zeus_current_target_scope"] = {"city":city.name,"target_date":"2026-10-01","metric":"low"}
    raw = (json.dumps(payload, sort_keys=True)+"\n").encode()
    anchor_path = tmp_path / "wmd-normal-anchor.json"
    anchor_path.write_bytes(raw)
    metadata_path = tmp_path / "wmd-normal-precision.json"
    manifest_dir = tmp_path / "raw-manifests"
    manifest_dir.mkdir()
    manifest_path = manifest_dir / "wmd-anchor.manifest.json"
    manifest = build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest(anchor_path,
        request=OpenMeteoEcmwfIfs9AnchorRequest(city.lat,city.lon,_hko_dt(12),city.timezone),
        metric="low",source_available_at=_hko_dt(12,5),captured_at=_hko_dt(12,10),
        product_metadata={"city":city.name,"target_date":"2026-10-01",
            "source_run_id":"normal-wmd-anchor","openmeteo_payload_json":str(anchor_path),
            "precision_metadata_json":str(metadata_path),"manifest_json":str(manifest_path)})
    artifact_id = write_manifest_to_db(conn, manifest)
    write_manifest(replace(manifest,product_metadata={**manifest.product_metadata,"artifact_id":artifact_id}),manifest_path)
    for item in _bins():
        conn.execute("""INSERT INTO market_events(market_slug,city,target_date,temperature_metric,
            condition_id,token_id,range_label,range_low,range_high) VALUES(?,?,'2026-10-01','low',?,?,?,?,?)""",
            (f"controlled-wmd-{item.bin_id}",city.name,f"controlled-{item.bin_id}",
             f"controlled-{item.bin_id}",item.bin_id,item.lower_c,item.upper_c))
    conn.commit()
    def request_at(cut, *, discovery=False):
        metadata = _precision_metadata(city.name,target.isoformat(),anchor_sigma_c=3.0,
                                       raw_payload_bytes=raw,analysis_at=cut)
        metadata_path.write_text(json.dumps(metadata,default=str))
        if discovery:
            from src.data.replacement_forecast_seed_discovery import discover_replacement_forecast_materialization_seeds
            from src.data.replacement_forecast_live_materialization_queue import _prepare_seed_requests_with_connection
            seed_dir = tmp_path / f"seeds-{target}-{cut.minute}"
            request_dir = tmp_path / f"requests-{target}-{cut.minute}"
            report = discover_replacement_forecast_materialization_seeds(forecast_db=db,
                raw_manifest_dir=manifest_dir,seed_dir=seed_dir,request_dir=request_dir,
                computed_at=cut,limit=1)
            assert report.discovered_count == 1, report
            processed,failed,reasons = _prepare_seed_requests_with_connection(seed_dir=seed_dir,
                seed_processed_dir=tmp_path / f"processed-{target}-{cut.minute}",seed_failed_dir=tmp_path / f"failed-{target}-{cut.minute}",
                request_dir=request_dir,forecast_db=db,forecast_conn=None,limit=1)
            assert len(processed)==1 and not failed,(processed,failed,reasons)
            requests = list(request_dir.glob("*.json"))
            assert len(requests)==1,(requests,reasons,
                [Path(path+".receipt.json").read_text() for path in processed])
            return build_materialize_request_dataclass(json.loads(requests[0].read_text()),base_dir=request_dir)
        seed = dict(city=city.name,city_id=city.name,city_timezone=city.timezone,
            target_date=target.isoformat(),temperature_metric="low",source_cycle_time=cycle.isoformat(),
            computed_at=cut.isoformat(),expires_at=(cycle+timedelta(hours=10)).isoformat(),baseline_source_run_id=baseline_run,
            baseline_data_version=_current_baseline_data_version("low"),baseline_source_available_at=(cycle+timedelta(minutes=5)).isoformat(),
            openmeteo_source_run_id="normal-wmd-anchor",openmeteo_source_available_at=(cycle+timedelta(minutes=5)).isoformat(),
            openmeteo_anchor_artifact_id=artifact_id,openmeteo_payload_json=str(anchor_path),
            precision_metadata_json=str(metadata_path),
            bins=[asdict(item) for item in _bins()])
        built = build_replacement_forecast_materialization_request(seed,base_dir=tmp_path)
        assert built.ok, built.reason_codes
        return build_materialize_request_dataclass(built.request,base_dir=tmp_path)
    def materialize(cut, *, discovery=False):
        request = request_at(cut,discovery=discovery)
        sql_clock[0] = cut
        result = materialize_replacement_forecast_live(conn,request)
        conn.commit()
        return request,result
    reader_clock = [_hko_dt(20)]
    class ReaderClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return reader_clock[0].astimezone(tz or UTC)
    monkeypatch.setattr(bundle_reader,"datetime",ReaderClock)
    def public(result,cut):
        reader_clock[0] = cut
        posterior = conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",(result.posterior_id,)).fetchone()
        cert = conn.execute("SELECT * FROM readiness_state WHERE readiness_id=?",(result.readiness_id,)).fetchone()
        readiness = ReplacementForecastReadinessDecision(readiness_id=cert["readiness_id"],status=cert["status"],
            reason_codes=tuple(json.loads(cert["reason_codes_json"])),dependency_json=json.loads(cert["dependency_json"]),
            provenance_json=json.loads(cert["provenance_json"]),expires_at=datetime.fromisoformat(cert["expires_at"]))
        return [read_replacement_forecast_bundle(conn,baseline_bundle=_BaselineBundle(_Evidence(baseline_run)),
            readiness=readiness,city=city.name,target_date=target,temperature_metric="low",
            decision_time=cut.isoformat(),current_bin_topology_hash=posterior["bin_topology_hash"],
            enforce_raw_input_hwm=True,authority_purpose=purpose) for purpose in ReplacementForecastAuthorityPurpose]
    request,a = materialize(_hko_dt(20),discovery=True)
    assert a.ok,a.reason_codes
    a_reads = public(a,request.computed_at)
    assert all(item.ok for item in a_reads), [item.reason_code for item in a_reads]
    provenance = json.loads(conn.execute("SELECT provenance_json FROM forecast_posteriors WHERE posterior_id=?",(a.posterior_id,)).fetchone()[0])
    shape = provenance["bayes_precision_fusion"]["current_evidence_shape"]
    assert shape["provider_geometry_audit"]["anchor_station_ground"] == ground_a
    original_certificate = tuple(conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",(a.posterior_id,)).fetchone())
    original_anchor_entity = tuple(conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?",(artifact_id,)).fetchone())
    q = dict(a_reads[0].bundle.q)
    mu = provenance["bayes_precision_fusion"]["anchor_value_c"]
    sigma = shape["predictive_sigma_c"]
    cdf = lambda x: .5*(1+math.erf((x-mu)/(sigma*math.sqrt(2))))
    assert q == pytest.approx({"cool":cdf(20.5),"warm":cdf(30.5)-cdf(20.5),"hot":1-cdf(30.5)},abs=1e-12)
    from src.engine import event_reactor_adapter as era, monitor_refresh
    from src.solve.solver import JointOutcomeProbabilityWitness, OutcomeTokenBinding, joint_probability_witness_identity
    bindings = tuple(OutcomeTokenBinding(bin_id=item.bin_id,condition_id="0x"+hashlib.sha256(item.bin_id.encode()).hexdigest(),
        yes_token_id=str(1000+2*index),no_token_id=str(1001+2*index))
        for index,item in enumerate(reversed(request.bins)))
    candidates = tuple(SimpleNamespace(condition_id=binding.condition_id,
        bin=SimpleNamespace(unit="C",low=item.lower_c,high=item.upper_c))
        for binding,item in zip(bindings,reversed(request.bins),strict=True))
    samples,point,basis = era._replacement_global_probability_components(a_reads[0].bundle,
        candidates=candidates,bindings=bindings)
    identity = dict(family_key=a_reads[0].bundle.family_id,bindings=bindings,
        q_version=a_reads[0].bundle.posterior_identity_hash,
        resolution_identity="Paris:LFPB:noaa:C:low:2026-10-01",
        topology_identity=a_reads[0].bundle.bin_topology_hash,
        posterior_identity_hash=a_reads[0].bundle.posterior_identity_hash,
        source_truth_identity=a_reads[0].bundle.dependency_hash,
        authority_certificate_hash=hashlib.sha256(json.dumps(provenance,sort_keys=True).encode()).hexdigest(),
        band_alpha=.05,band_basis=basis,yes_point_q=point,yes_q_samples=samples,captured_at_utc=request.computed_at)
    witness = JointOutcomeProbabilityWitness(**identity,max_age=timedelta(minutes=5),
        witness_identity=joint_probability_witness_identity(**identity))
    assert point == pytest.approx([q[item.bin_id] for item in reversed(request.bins)])
    for index,binding in enumerate(bindings):
        for direction,side in (("buy_yes","YES"),("buy_no","NO")):
            held = SimpleNamespace(condition_id=binding.condition_id,direction=direction,
                token_id=binding.yes_token_id,no_token_id=binding.no_token_id)
            assert monitor_refresh._current_global_held_point_probability(held,witness) == pytest.approx(
                q[binding.bin_id] if side=="YES" else 1-q[binding.bin_id])
            held_samples = monitor_refresh._current_global_held_samples(held,witness,
                current_token_pair=(binding.yes_token_id,binding.no_token_id))
            assert held_samples == pytest.approx(samples[:,index] if side=="YES" else 1-samples[:,index])
            assert era._global_sell_held_probability(SimpleNamespace(bin_id=binding.bin_id,side=side),witness) == pytest.approx(float(held_samples.mean()))
    # B is acquired later and discloses a target-internal future transition,
    # while current facts/geometry remain 67m. Only this target is affected.
    _wmd_known_periods(primary,registry,claims,[("2026-10-01",100)],captured=_hko_dt(20,2).isoformat())
    future_primary = primary.read_bytes()
    ground_clock[0] = _hko_dt(20,2)+timedelta(seconds=30)
    ground_b = ground.archive_station_ground_evidence(db,[city.name])["archived"][city.name]
    assert ground_b["facts_identity"] == ground_a["facts_identity"]
    from src.data.replacement_current_value_serving import station_ground_target_coverage_for_city
    def coverage(evidence,target,decision=None):
        return station_ground_target_coverage_for_city(evidence,city=city.name,target_date=target,
                                                      decision_at=decision or _hko_dt(20,3))
    coverage_a,coverage_b = (coverage(evidence,date(2026,10,1)) for evidence in (ground_a,ground_b))
    assert coverage_a["status"]=="VERIFIED" and coverage_b["status"]=="DATA_DEGRADED"
    assert coverage_a["applicability_identity"] != coverage_b["applicability_identity"]
    assert coverage(ground_a,date(2026,9,30))["applicability_identity"] == coverage(ground_b,date(2026,9,30))["applicability_identity"]
    assert all(item.ok for item in public(a,request.computed_at))
    rejected = public(a,_hko_dt(20,3))
    assert not any(item.ok for item in rejected)
    assert all("basis=station_ground_target_applicability_changed" in item.reason_code for item in rejected)
    _, blocked = materialize(_hko_dt(20,3),discovery=True)
    assert not blocked.ok
    # Genuine reacquisition of the compatible A body resets the target; merely
    # waiting for the conflicting interval must not authorize its old geometry.
    primary.write_bytes(original_primary)
    claims[city.name]["station_ground_proof"].update(body_sha256=hashlib.sha256(original_primary).hexdigest(),
        source_checked_at=_hko_dt(20,4).isoformat(),checked_at=_hko_dt(20,4).isoformat())
    registry.write_text(json.dumps(claims))
    ground_clock[0] = _hko_dt(20,4)+timedelta(seconds=30)
    reset_ground = ground.archive_station_ground_evidence(db,[city.name])["archived"][city.name]
    assert reset_ground["facts_identity"] == ground_a["facts_identity"]
    assert reset_ground["input_bodies"] == ground_a["input_bodies"]
    assert reset_ground["manifest_role"] == "source_capture_confirmation"
    assert reset_ground["captured_at"] == _hko_dt(20,4).isoformat()
    assert coverage(reset_ground,date(2026,10,1),_hko_dt(20,5))["applicability_identity"] == coverage_a["applicability_identity"]
    from src.data.replacement_forecast_seed_discovery import discover_replacement_forecast_materialization_seeds
    reset_cut = _hko_dt(20,5)
    request_at(reset_cut)  # Normal metadata proves the compatible current site.
    no_debt = discover_replacement_forecast_materialization_seeds(forecast_db=db,
        raw_manifest_dir=manifest_dir,seed_dir=tmp_path/"reset-seeds",computed_at=reset_cut,limit=1)
    assert no_debt.discovered_count == 0 and no_debt.failed_count == 0,no_debt
    # Same facts/applicability reset can reuse the still-fresh A certificate;
    # whole-body transport changes cannot invent posterior debt or renew age.
    reset_reads = public(a,reset_cut)
    assert all(item.ok for item in reset_reads),[item.reason_code for item in reset_reads]
    assert all(item.bundle.posterior_id == a.posterior_id for item in reset_reads)
    assert dict(reset_reads[0].bundle.q) == pytest.approx(q,abs=1e-12)
    assert tuple(conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",(a.posterior_id,)).fetchone()) == original_certificate
    assert tuple(conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?",(artifact_id,)).fetchone()) == original_anchor_entity
    expiry = datetime.fromisoformat(conn.execute("SELECT expires_at FROM readiness_state WHERE readiness_id=?",(a.readiness_id,)).fetchone()[0])
    assert not any(item.ok for item in public(a,expiry+timedelta(seconds=1)))
    # Acquire the known future schedule once, then cross its effective boundary
    # with no new ground HTTP/body acquisition. The old Oct1 window straddles
    # 67→100m and remains unsupported even when today's facts are now 100m.
    primary.write_bytes(future_primary)
    claims[city.name]["station_ground_proof"].update(body_sha256=hashlib.sha256(future_primary).hexdigest(),
        source_checked_at=_hko_dt(20,6).isoformat(),checked_at=_hko_dt(20,6).isoformat())
    registry.write_text(json.dumps(claims))
    ground_clock[0] = _hko_dt(20,6)+timedelta(seconds=30)
    scheduled = ground.archive_station_ground_evidence(db,[city.name])["archived"][city.name]
    assert scheduled["facts"]["elevation_m"] == 67.0
    transition_cut = datetime(2026,10,1,0,1,tzinfo=UTC)
    ground_clock[0] = transition_cut
    transitioned = ground.archive_station_ground_evidence(db,[city.name])["archived"][city.name]
    assert transitioned["manifest_role"] == "known_effective_period_transition"
    assert transitioned["input_bodies"] == scheduled["input_bodies"]
    assert transitioned["captured_at"] == scheduled["captured_at"]
    assert transitioned["recorded_at"] == transition_cut.isoformat()
    assert transitioned["facts"]["elevation_m"] == 100.0
    assert coverage(transitioned,date(2026,10,1),transition_cut)["status"] == "DATA_DEGRADED"
    assert coverage(transitioned,date(2026,10,2),transition_cut)["status"] == "VERIFIED"
    assert all(item.ok for item in public(a,request.computed_at))
    assert not any(item.ok for item in public(a,transition_cut))
    # A genuinely new forecast/run/target in the SAME canonical DB can use
    # the fully covered Oct2 geometry. Neither old q nor old ground clocks are
    # relabeled, and future target suitability does not select a future fact.
    target = date(2026,10,2)
    cycle = datetime(2026,10,1,12,tzinfo=UTC)
    capture = cycle+timedelta(minutes=10)
    baseline_run = "period-new12"
    next_conn = _low_revision_authority_conn(db,include_legacy_provider_fixtures=False,
        city_name=city.name,include_retired_incumbent=False,target_date=target,
        source_cycle=cycle,run_prefix="period-",snapshot_offset=100)
    next_conn.close()
    sql_clock[0] = capture+timedelta(minutes=1)
    dl.download_bayes_precision_fusion_extra_raw_inputs(forecast_db=db,cycle=cycle,
        targets=[dl.BayesPrecisionFusionDownloadTarget(city=city.name,metric="low",target_date=target.isoformat(),
            lead_days=1,latitude=city.lat,longitude=city.lon,timezone_name=city.timezone)],
        models=("icon_global","ukmo_global_deterministic_10km"),include_previous_runs=False,prune_after=False)
    payload = hourly_payload(cell["selected_grid_lat"],cell["selected_grid_lon"],18.5)
    payload["_zeus_current_target_scope"] = {"city":city.name,"target_date":target.isoformat(),"metric":"low"}
    raw = (json.dumps(payload,sort_keys=True)+"\n").encode()
    anchor_path = tmp_path/"wmd-known-period-normal-anchor.json"
    anchor_path.write_bytes(raw)
    metadata_path = tmp_path/"wmd-known-period-normal-precision.json"
    manifest_path = manifest_dir/"wmd-known-period-anchor.manifest.json"
    manifest = build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest(anchor_path,
        request=OpenMeteoEcmwfIfs9AnchorRequest(city.lat,city.lon,cycle,city.timezone),
        metric="low",source_available_at=cycle+timedelta(minutes=5),captured_at=capture,
        product_metadata={"city":city.name,"target_date":target.isoformat(),
            "source_run_id":"normal-wmd-known-period-anchor","openmeteo_payload_json":str(anchor_path),
            "precision_metadata_json":str(metadata_path),"manifest_json":str(manifest_path)})
    artifact_id = write_manifest_to_db(conn,manifest)
    write_manifest(replace(manifest,product_metadata={**manifest.product_metadata,"artifact_id":artifact_id}),manifest_path)
    for item in _bins():
        conn.execute("""INSERT INTO market_events(market_slug,city,target_date,temperature_metric,
            condition_id,token_id,range_label,range_low,range_high) VALUES(?,?,?,'low',?,?,?,?,?)""",
            (f"controlled-wmd-period-{item.bin_id}",city.name,target.isoformat(),f"controlled-period-{item.bin_id}",
             f"controlled-period-{item.bin_id}",item.bin_id,item.lower_c,item.upper_c))
    conn.commit()
    new_cut = cycle+timedelta(hours=8)
    ground_clock[0] = new_cut-timedelta(minutes=1)
    assert ground.archive_station_ground_evidence(db,[city.name])["archived"][city.name] == transitioned
    known_request,known_result = materialize(new_cut,discovery=True)
    assert known_result.ok,known_result.reason_codes
    known_reads = public(known_result,new_cut)
    assert all(item.ok for item in known_reads),[item.reason_code for item in known_reads]
    new_provenance = json.loads(conn.execute("SELECT provenance_json FROM forecast_posteriors WHERE posterior_id=?",(known_result.posterior_id,)).fetchone()[0])
    new_shape = new_provenance["bayes_precision_fusion"]["current_evidence_shape"]
    assert new_shape["provider_geometry_audit"]["anchor_station_ground"] == transitioned
    assert new_shape["provider_geometry_identity_hash"] != shape["provider_geometry_identity_hash"]
    assert new_shape["provider_geometry_audit"]["anchor_station_ground_target_coverage"]["status"] == "VERIFIED"
    new_mu = new_provenance["bayes_precision_fusion"]["anchor_value_c"]
    new_sigma = new_shape["predictive_sigma_c"]
    new_cdf = lambda x: .5*(1+math.erf((x-new_mu)/(new_sigma*math.sqrt(2))))
    assert dict(known_reads[0].bundle.q) == pytest.approx(
        {"cool":new_cdf(20.5),"warm":new_cdf(30.5)-new_cdf(20.5),"hot":1-new_cdf(30.5)},abs=1e-12)
    assert tuple(conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",(a.posterior_id,)).fetchone()) == original_certificate
    conn.close()
    builtins.close()


def _normal_hko_writer_proof_relationship(tmp_path, monkeypatch, *, include_raw_ifs):
    """Real entity bytes/ordinary writer + qualified ENS read, not authority mocks."""
    from src.config import runtime_cities_by_name
    from src.data import bayes_precision_fusion_download as dl
    from src.data import station_ground_evidence as ground
    from tests.test_config import _official_hko_registry
    from src.data.replacement_forecast_cycle_policy import (
        current_evidence_shape_has_entry_authority, current_evidence_shape_has_held_authority,
    )
    registry, official_body, ground_claims = _official_hko_registry(tmp_path, monkeypatch)
    original_ground_body = official_body.read_bytes()
    monkeypatch.setattr(ground, "_store_root", lambda: tmp_path / "station-ground")
    ground_clock = [_hko_dt(19, 59)]
    class GroundClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return ground_clock[0].astimezone(tz or UTC)
    monkeypatch.setattr(ground, "datetime", GroundClock)
    db = tmp_path / "forecast.db"
    # This relationship acquires every provider through the normal writer
    # below; legacy convenience rows remain available to their own tests.
    conn = _low_revision_authority_conn(db, include_legacy_provider_fixtures=False)
    # TEST_ONLY_CONTROLLED_CANONICAL_INSERT_CLOCK: evaluate the normal SQLite
    # DEFAULT at INSERT, never UPDATE a licensed row or pre-seal its own clock.
    sql_clock = [_hko_dt(12, 11)]
    sql_builtins = sqlite3.connect(":memory:")
    def canonical_insert_clock(fmt, value):
        if (fmt, value) == ("%Y-%m-%dT%H:%M:%f+00:00", "now"):
            return sql_clock[0].isoformat(timespec="milliseconds")
        return sql_builtins.execute("SELECT strftime(?,?)", (fmt, value)).fetchone()[0]
    conn.create_function("strftime", 2, canonical_insert_clock)
    city = runtime_cities_by_name()["Hong Kong"]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return _hko_dt(12, 10).astimezone(tz) if tz else _hko_dt(12, 10).replace(tzinfo=None)
    monkeypatch.setattr(dl, "datetime", Clock)
    def fetch(url, params, **kwargs):
        value = 22.0 if params["models"] == "icon_global" else 24.0
        is_ifs = params["models"] == "ecmwf_ifs"
        anchor_body = json.loads(_hko_raw_openmeteo_bytes())
        selected_lat, selected_lon = ((anchor_body["latitude"], anchor_body["longitude"]) if is_ifs
            else (22.25,114.125) if params["models"] == "icon_global" else (22.3125,114.1875))
        payload = {"latitude": selected_lat,
            "longitude": selected_lon, "elevation": 32.0,
            "timezone": city.timezone, "utc_offset_seconds": 28800,
            "hourly_units": {"temperature_2m": "°C"},
            "hourly": {"time": [f"2026-10-01T{hour:02d}:00" for hour in range(24)],
                       "temperature_2m": [value]*24}}
        body = (json.dumps(payload, indent=2)+"\n").encode()
        kwargs["capture_entity_body"](body, _hko_dt(12, 10).timestamp())
        if "capture_network_response" in kwargs:
            kwargs["capture_network_response"](body, _hko_dt(12,10).timestamp(), {"content-type": "application/json"})
        return json.loads(body)
    monkeypatch.setattr("src.data.openmeteo_client.fetch", fetch)
    dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=_hko_dt(12),
        targets=[dl.BayesPrecisionFusionDownloadTarget(
            city="Hong Kong", metric="low", target_date="2026-10-01", lead_days=1,
            latitude=city.lat, longitude=city.lon, timezone_name=city.timezone)],
        models=(("ecmwf_ifs", "icon_global", "ukmo_global_deterministic_10km") if include_raw_ifs
                else ("icon_global", "ukmo_global_deterministic_10km")),
        include_previous_runs=False, prune_after=False,
    )
    request = _low_revision_request()
    from src.data.openmeteo_ecmwf_ifs9_anchor import (
        OpenMeteoEcmwfIfs9AnchorRequest, build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest,
    )
    from src.data.raw_forecast_artifact_manifest import write_manifest_to_db
    anchor_payload = json.loads(request.openmeteo_raw_payload_bytes)
    anchor_payload["_zeus_current_target_scope"]["metric"] = "low"
    anchor_bytes = (json.dumps(anchor_payload, indent=2, sort_keys=True)+"\n").encode()
    anchor_path = tmp_path / "normal-low-anchor.json"
    anchor_path.write_bytes(anchor_bytes)
    anchor_manifest = build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest(anchor_path,
        request=OpenMeteoEcmwfIfs9AnchorRequest(city.lat, city.lon, request.source_cycle_time, city.timezone),
        metric="low", source_available_at=_hko_dt(12, 5), captured_at=_hko_dt(12, 10),
        product_metadata={"city":city.name, "target_date":request.target_date.isoformat()})
    anchor_id = write_manifest_to_db(conn, anchor_manifest)
    anchor_row = dict(conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (anchor_id,)).fetchone())
    assert datetime.fromisoformat(anchor_row["captured_at"]) <= datetime.fromisoformat(anchor_row["recorded_at"]) <= request.computed_at
    assert anchor_row["recorded_at"] == sql_clock[0].isoformat(timespec="milliseconds")
    assert write_manifest_to_db(conn, anchor_manifest) == anchor_id
    assert dict(conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (anchor_id,)).fetchone()) == anchor_row
    request = replace(request, anchor_artifact_id=anchor_id, openmeteo_raw_payload_bytes=anchor_bytes,
        openmeteo_precision_guard=_hko_precision_guard(decision_at=request.computed_at, raw_payload_bytes=anchor_bytes))
    sql_clock[0] = request.computed_at
    conn.commit()  # Publish the normal anchor entity before archive's transaction.
    archived = ground.archive_station_ground_evidence(db, ["Hong Kong"])
    ground_a = archived["archived"]["Hong Kong"]
    assert ground_a["captured_at"] == "2026-09-29T21:23:56+00:00"
    assert ground_a["recorded_at"] == _hko_dt(19, 59).isoformat()
    old_cut = replace(request, computed_at=_hko_dt(19, 58))
    assert ground.read_current_station_ground_evidence(
        db, city="Hong Kong", decision_at=old_cut.computed_at) is None
    refused_before_possession = materialize_replacement_forecast_live(conn, old_cut)
    assert refused_before_possession.reason_codes == ("OM9_STATION_GROUND_ENTITY_NOT_POSSESSED",)
    assert conn.execute("SELECT count(*) FROM forecast_posteriors").fetchone()[0] == 1
    real_shape_reader = materializer_mod._read_current_evidence_shape
    with monkeypatch.context() as obsolete:
        def old_shape(*args, **kwargs):
            shape = real_shape_reader(*args, **kwargs)
            assert shape is not None
            return replace(shape, semantics_revision="ensemble_center_scenarios_v5")
        obsolete.setattr(materializer_mod, "_read_current_evidence_shape", old_shape)
        refused = materialize_replacement_forecast_live(conn, request)
        assert not refused.ok
        assert conn.execute("SELECT count(*) FROM forecast_posteriors").fetchone()[0] == 1
    rebuilt = materialize_replacement_forecast_live(conn, request)
    assert rebuilt.ok, rebuilt.reason_codes
    conn.commit()  # Public authority opens the committed canonical anchor FK.
    row = conn.execute("SELECT q_json, provenance_json FROM forecast_posteriors WHERE posterior_id=?",
                       (rebuilt.posterior_id,)).fetchone()
    provenance = json.loads(row["provenance_json"])
    shape = provenance["bayes_precision_fusion"]["current_evidence_shape"]
    assert shape["semantics_revision"] == "ensemble_center_scenarios_v6"
    assert shape["member_count"] == 51
    frozen_ground = shape["provider_geometry_audit"]["anchor_station_ground"]
    assert frozen_ground == ground_a
    authority_scope = dict(materialized_at=request.computed_at, city=request.city,
        target_date=request.target_date.isoformat(), metric=request.temperature_metric,
        anchor_id=rebuilt.anchor_id, forecast_db=ground.forecast_db_from_connection(conn))
    assert current_evidence_shape_has_entry_authority(provenance, **authority_scope)
    assert current_evidence_shape_has_held_authority(provenance, **authority_scope)
    # USED IFS9 must replay its own request/body/static bytes, not just a
    # self-consistent hash of an unproven two-field geometry claim.
    used_models = provenance["bayes_precision_fusion"]["used_models"]
    assert "ecmwf_ifs" in used_models  # Anchor center is also a used instrument.
    anchor_audit = shape["provider_geometry_audit"]
    assert anchor_audit["anchor_ifs9_role"] == ("raw_ifs9_and_anchor" if include_raw_ifs else "anchor_only")
    assert anchor_audit["anchor_raw_artifact"]["artifact_id"] == anchor_id
    assert anchor_audit["anchor_raw_artifact"]["sha256"] == hashlib.sha256(anchor_bytes).hexdigest()
    assert anchor_audit["anchor_precision_metadata"]["source_geometry_proof"]["static_asset_audit"]
    if not include_raw_ifs:
        assert "ecmwf_ifs" not in shape["provider_geometry_evidence"]["providers"]
        assert "ecmwf_ifs" not in provenance["bayes_precision_fusion"]["current_value_serving"]
    ifs_physical = (provenance["bayes_precision_fusion"]["current_value_serving"]["ecmwf_ifs"]["physical_response"]
                    if include_raw_ifs else None)
    ifs_proof = ifs_physical["source_cell_geometry_proof"] if ifs_physical else anchor_audit["anchor_precision_metadata"]["source_geometry_proof"]
    assert ifs_proof["static_asset_audit"]
    if ifs_physical:
        assert ifs_physical["frozen_entity_body"]
        assert ifs_physical["frozen_product_identity"]
    damaged_provenances = []
    for damage in (("two_fields", "missing_used_provider") if include_raw_ifs else ("missing_anchor_artifact", "wrong_anchor_cell")):
        damaged_provenance = json.loads(json.dumps(provenance))
        damaged_fusion = damaged_provenance["bayes_precision_fusion"]
        damaged_shape = damaged_fusion["current_evidence_shape"]
        damaged_geometry = damaged_shape["provider_geometry_evidence"]
        if damage == "missing_anchor_artifact":
            del damaged_shape["provider_geometry_audit"]["anchor_raw_artifact"]
        elif damage == "wrong_anchor_cell":
            damaged_shape["provider_geometry_audit"]["anchor_precision_metadata"]["source_geometry_proof"]["selected_flat_index"] += 1
        elif damage == "two_fields":
            damaged_claim = {"revision": ifs_proof["revision"], "cell_is_sea": False}
            damaged_fusion["current_value_serving"]["ecmwf_ifs"]["physical_response"]["source_cell_geometry_proof"] = damaged_claim
            damaged_geometry["providers"]["ecmwf_ifs"]["source_cell_geometry_proof"] = dict(damaged_claim)
        else:
            # Keep USED and its served instrument unchanged: an anchor proof
            # must not substitute for the missing used-provider instrument.
            del damaged_geometry["providers"]["ecmwf_ifs"]
        damaged_shape["provider_geometry_identity_hash"] = hashlib.sha256(
            json.dumps(damaged_geometry, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        assert not current_evidence_shape_has_entry_authority(damaged_provenance, **authority_scope), damage
        assert not current_evidence_shape_has_held_authority(damaged_provenance, **authority_scope), damage
        damaged_provenances.append(damaged_provenance)
    if not include_raw_ifs:
        from scripts.download_replacement_forecast_current_targets import _precision_metadata
        from src.data.replacement_current_value_serving import _ARTIFACT_IDENTITY_JSON_SQL
        from src.data.replacement_forecast_cycle_policy import _anchor_ifs9_response_has_authority
        foreign_manifests = []
        # Independently normal-produced, internally coherent foreign evidence:
        # neither a false tuple nor a hash-only mutation is the rejection cause.
        for foreign in ("target", "metric", "body"):
            foreign_date = request.target_date + (timedelta(days=1) if foreign == "target" else timedelta())
            foreign_metric = "high" if foreign == "metric" else request.temperature_metric
            foreign_payload = json.loads(anchor_bytes)
            foreign_payload["hourly"]["time"] = [f"{foreign_date.isoformat()}T{hour:02d}:00" for hour in range(24)]
            foreign_payload["_zeus_current_target_scope"].update(target_date=foreign_date.isoformat(), metric=foreign_metric)
            if foreign == "body":
                foreign_payload["hourly"]["temperature_2m"][0] -= 1.0
            foreign_bytes = (json.dumps(foreign_payload, indent=2, sort_keys=True)+"\n").encode()
            foreign_path = tmp_path / f"normal-foreign-{foreign}-anchor.json"
            foreign_path.write_bytes(foreign_bytes)
            foreign_precision = _precision_metadata(city.name, foreign_date.isoformat(),
                anchor_sigma_c=3.0, raw_payload_bytes=foreign_bytes)
            foreign_manifest = build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest(foreign_path,
                request=OpenMeteoEcmwfIfs9AnchorRequest(city.lat, city.lon, request.source_cycle_time, city.timezone),
                metric=foreign_metric, source_available_at=_hko_dt(12, 5), captured_at=_hko_dt(12, 10),
                product_metadata={"city": city.name, "target_date": foreign_date.isoformat()})
            foreign_id = write_manifest_to_db(conn, foreign_manifest)
            if foreign != "body":
                foreign_manifests.append(foreign_manifest)
            conn.commit()
            foreign_artifact = {**json.loads(conn.execute(
                f"SELECT {_ARTIFACT_IDENTITY_JSON_SQL} FROM raw_forecast_artifacts a WHERE artifact_id=?",
                (foreign_id,)).fetchone()[0]), "forecast_db": str(db)}
            assert foreign_id != anchor_id
            damaged_provenance = json.loads(json.dumps(provenance))
            damaged_shape = damaged_provenance["bayes_precision_fusion"]["current_evidence_shape"]
            damaged_audit = damaged_shape["provider_geometry_audit"]
            damaged_audit.update(anchor_precision_metadata=foreign_precision, anchor_raw_artifact=foreign_artifact)
            damaged_provenance["openmeteo_anchor_artifact_id"] = foreign_id
            # The foreign pair has its own legitimate producer scope, but is
            # not the artifact selected by this posterior's canonical anchor FK.
            assert _anchor_ifs9_response_has_authority(damaged_shape["provider_geometry_evidence"],
                damaged_audit, materialized_at=request.computed_at, city=city.name,
                target_date=foreign_date.isoformat(), metric=foreign_metric,
                expected_anchor_artifact_id=foreign_id, request_anchor_artifact_id=foreign_id,
                forecast_db=ground.forecast_db_from_connection(conn)), foreign
            assert not current_evidence_shape_has_entry_authority(damaged_provenance, **authority_scope), foreign
            assert not current_evidence_shape_has_held_authority(damaged_provenance, **authority_scope), foreign
            damaged_provenances.append(damaged_provenance)
        # A second actual tmp DB gets naturally identical local IDs through
        # the same ordinary writer sequence. Its self-consistent own body is
        # not a substitute for the public consumer's A database namespace.
        other_db = tmp_path / "independent-forecast.db"
        other_conn = _low_revision_authority_conn(other_db, include_legacy_provider_fixtures=False)
        other_conn.create_function("strftime", 2, canonical_insert_clock)
        dl.download_bayes_precision_fusion_extra_raw_inputs(
            forecast_db=other_db, cycle=request.source_cycle_time,
            targets=[dl.BayesPrecisionFusionDownloadTarget(city=city.name, metric="low",
                target_date=request.target_date.isoformat(), lead_days=1, latitude=city.lat,
                longitude=city.lon, timezone_name=city.timezone)],
            models=("icon_global", "ukmo_global_deterministic_10km"),
            include_previous_runs=False, prune_after=False)
        # The provider cache replay is not a fresh HTTP receipt. These two
        # genuine other-target/metric products precede B's own LOW entity; no
        # explicit PK assignment, UPDATE or transfer of a licensed ID occurs.
        for other_product in foreign_manifests:
            write_manifest_to_db(other_conn, other_product)
        other_payload = json.loads(anchor_bytes)
        other_payload["hourly"]["temperature_2m"][0] -= 1.0
        other_bytes = (json.dumps(other_payload, indent=2, sort_keys=True)+"\n").encode()
        other_path = tmp_path / "independent-normal-low-anchor.json"
        other_path.write_bytes(other_bytes)
        other_manifest = build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest(other_path,
            request=OpenMeteoEcmwfIfs9AnchorRequest(city.lat, city.lon, request.source_cycle_time, city.timezone),
            metric="low", source_available_at=_hko_dt(12, 5), captured_at=_hko_dt(12, 10),
            product_metadata={"city": city.name, "target_date": request.target_date.isoformat()})
        other_id = write_manifest_to_db(other_conn, other_manifest)
        other_guard = _hko_precision_guard(decision_at=request.computed_at, raw_payload_bytes=other_bytes)
        from src.data.openmeteo_ecmwf_ifs9_anchor import extract_openmeteo_ecmwf_ifs9_localday_anchor
        other_request = replace(request, anchor_artifact_id=other_id, openmeteo_raw_payload_bytes=other_bytes,
            openmeteo_precision_guard=other_guard, openmeteo_anchor=extract_openmeteo_ecmwf_ifs9_localday_anchor(
                other_payload, city_timezone=city.timezone, target_local_date=request.target_date,
                source_cycle_time=request.source_cycle_time, require_full_localday=True))
        other_anchor_id = materializer_mod._insert_anchor(other_conn, other_request, metric="low")
        other_conn.commit()
        other_ground = ground.archive_station_ground_evidence(other_db, [city.name])["archived"][city.name]
        other_artifact = {**json.loads(other_conn.execute(
            f"SELECT {_ARTIFACT_IDENTITY_JSON_SQL} FROM raw_forecast_artifacts a WHERE artifact_id=?",
            (other_id,)).fetchone()[0]), "forecast_db": str(other_db)}
        assert (other_id, other_anchor_id) == (anchor_id, rebuilt.anchor_id)
        assert other_artifact["sha256"] != anchor_audit["anchor_raw_artifact"]["sha256"]
        namespace_provenance = json.loads(json.dumps(provenance))
        namespace_audit = namespace_provenance["bayes_precision_fusion"]["current_evidence_shape"]["provider_geometry_audit"]
        namespace_audit.update(anchor_raw_artifact=other_artifact, anchor_station_ground=other_ground,
            anchor_precision_metadata=_precision_metadata(
            city.name, request.target_date.isoformat(), anchor_sigma_c=3.0, raw_payload_bytes=other_bytes))
        # The foreign artifact is legal in B, before the actual A consumer
        # rejects the pointer despite matching local FK/family/geometry.
        assert _anchor_ifs9_response_has_authority(shape["provider_geometry_evidence"], namespace_audit,
            materialized_at=request.computed_at, city=city.name, target_date=request.target_date.isoformat(),
            metric="low", expected_anchor_artifact_id=other_id, anchor_id=other_anchor_id,
            forecast_db=ground.forecast_db_from_connection(other_conn))
        assert not current_evidence_shape_has_entry_authority(namespace_provenance, **authority_scope)
        assert not current_evidence_shape_has_held_authority(namespace_provenance, **authority_scope)
        damaged_provenances.append(namespace_provenance)
        other_conn.close()
    q = json.loads(row["q_json"])
    mu = provenance["bayes_precision_fusion"]["anchor_value_c"]
    sigma = shape["predictive_sigma_c"]
    cdf = lambda x: .5*(1+math.erf((x-mu)/(sigma*math.sqrt(2))))
    assert q == pytest.approx({"cool": cdf(20.5), "warm": cdf(30.5)-cdf(20.5),
                               "hot": 1-cdf(30.5)}, abs=1e-12)
    assert shape["provider_geometry_evidence"]["providers"]
    serving = provenance["bayes_precision_fusion"]["current_value_serving"]
    assert {"icon_global", "ukmo_global_deterministic_10km"}.issubset(serving)
    for model in ("icon_global", "ukmo_global_deterministic_10km"):
        assert serving[model]["physical_response"] is not None
        physical = serving[model]["physical_response"]
        assert physical["model_surface_witness"]["status"] == "VERIFIED"
        assert physical["native_surface"] == "LAND"
        assert physical["native_grid_elevation_m"] == 32.0
        captured = conn.execute("SELECT * FROM raw_model_forecasts WHERE raw_model_forecast_id=?",
                                (serving[model]["raw_model_forecast_id"],)).fetchone()
        assert captured["model"] == model and captured["raw_sha256"]
    # An unrelated official-page edit is a new possession, not new geometry.
    # Replaying the A certificate must read its sealed A body, never latest B.
    conn.commit()  # Complete the materializer transaction before producer archive.
    body_b = official_body.read_bytes() + b"<!-- unrelated retained page edit -->"
    official_body.write_bytes(body_b)
    ground_claims["Hong Kong"]["station_ground_proof"].update(
        body_sha256=hashlib.sha256(body_b).hexdigest(),
        checked_at=_hko_dt(20).isoformat(),
    )
    registry.write_text(json.dumps(ground_claims))
    ground_clock[0] = _hko_dt(20) + timedelta(seconds=30)
    ground_b = ground.archive_station_ground_evidence(db, ["Hong Kong"])["archived"]["Hong Kong"]
    assert ground_b["body_sha256"] != ground_a["body_sha256"]
    assert ground_b["artifact_id"] != ground_a["artifact_id"]
    assert ground_b["facts_identity"] == ground_a["facts_identity"]
    assert ground.read_frozen_station_ground_evidence(ground_a, decision_at=request.computed_at) == ground_a
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at=request.computed_at) == ground_a
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at=_hko_dt(20, 1)) == ground_b
    from src.data.replacement_forecast_readiness import ReplacementForecastReadinessDecision
    from src.data import replacement_forecast_bundle_reader as bundle_reader
    from src.data.replacement_forecast_bundle_reader import (
        ReplacementForecastAuthorityPurpose, read_replacement_forecast_bundle,
    )
    cert = conn.execute("SELECT * FROM readiness_state WHERE readiness_id=?",
                        (rebuilt.readiness_id,)).fetchone()
    posterior = conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",
                             (rebuilt.posterior_id,)).fetchone()
    class ReaderClock(datetime):
        @classmethod
        def now(cls, tz=None):
            cut = _hko_dt(20, 1)
            return cut.astimezone(tz) if tz else cut.replace(tzinfo=None)
    monkeypatch.setattr(bundle_reader, "datetime", ReaderClock)
    readiness = ReplacementForecastReadinessDecision(
        readiness_id=cert["readiness_id"], status=cert["status"],
        reason_codes=tuple(json.loads(cert["reason_codes_json"])),
        dependency_json=json.loads(cert["dependency_json"]),
        provenance_json=json.loads(cert["provenance_json"]),
        expires_at=datetime.fromisoformat(cert["expires_at"]),
    )
    for purpose in ReplacementForecastAuthorityPurpose:
        served = read_replacement_forecast_bundle(
            conn, baseline_bundle=_BaselineBundle(_Evidence("new12")), readiness=readiness,
            city="Hong Kong", target_date=request.target_date, temperature_metric="low",
            decision_time=_hko_dt(20, 1).isoformat(),
            current_bin_topology_hash=posterior["bin_topology_hash"], enforce_raw_input_hwm=True,
            authority_purpose=purpose,
        )
        assert served.ok, (purpose, served.reason_code)
        assert served.bundle.posterior_id == rebuilt.posterior_id
        assert dict(served.bundle.q) == pytest.approx(q, abs=1e-12)
        if not include_raw_ifs:
            # Real owned evidence is required even with an unchanged/re-signed
            # geometry claim; restoring the same bytes re-enables replay.
            for owned_path in (anchor_path, Path(ifs_proof["static_asset_audit"]["asset_path"])):
                original_bytes = owned_path.read_bytes()
                owned_path.unlink()
                try:
                    assert not current_evidence_shape_has_entry_authority(provenance, **authority_scope)
                    assert not current_evidence_shape_has_held_authority(provenance, **authority_scope)
                    missing_body = read_replacement_forecast_bundle(
                        conn, baseline_bundle=_BaselineBundle(_Evidence("new12")), readiness=readiness,
                        city=request.city, target_date=request.target_date, temperature_metric=request.temperature_metric,
                        decision_time=_hko_dt(20, 1).isoformat(),
                        current_bin_topology_hash=posterior["bin_topology_hash"], enforce_raw_input_hwm=True,
                        authority_purpose=purpose)
                    assert not missing_body.ok
                    assert missing_body.reason_code == "REPLACEMENT_POSTERIOR_READINESS_NOT_LIVE_GRADE"
                finally:
                    owned_path.write_bytes(original_bytes)
                assert current_evidence_shape_has_entry_authority(provenance, **authority_scope)
                assert current_evidence_shape_has_held_authority(provenance, **authority_scope)
        # Positive-first persisted certificate tampering: re-signing the
        # geometry map cannot turn the legacy two-field claim into authority.
        for damaged_provenance in damaged_provenances:
            conn.execute("UPDATE forecast_posteriors SET provenance_json=? WHERE posterior_id=?",
                (json.dumps(damaged_provenance), rebuilt.posterior_id))
            try:
                damaged_read = read_replacement_forecast_bundle(
                    conn, baseline_bundle=_BaselineBundle(_Evidence("new12")), readiness=readiness,
                    city="Hong Kong", target_date=request.target_date, temperature_metric="low",
                    decision_time=_hko_dt(20, 1).isoformat(),
                    current_bin_topology_hash=posterior["bin_topology_hash"], enforce_raw_input_hwm=True,
                    authority_purpose=purpose)
                assert not damaged_read.ok
                assert damaged_read.reason_code == "REPLACEMENT_POSTERIOR_READINESS_NOT_LIVE_GRADE"
            finally:
                conn.execute("UPDATE forecast_posteriors SET provenance_json=? WHERE posterior_id=?",
                    (row["provenance_json"], rebuilt.posterior_id))
        from src.engine import event_reactor_adapter as era, monitor_refresh
        from src.solve.solver import JointOutcomeProbabilityWitness, OutcomeTokenBinding, joint_probability_witness_identity
        # Reorder conditions relative to stored bin order to disprove a positional
        # coincidence. Tokens are a bounded probability fixture, not venue facts.
        candidates = tuple(SimpleNamespace(condition_id="0x"+hashlib.sha256(item.bin_id.encode()).hexdigest(),
            bin=SimpleNamespace(unit="C", low=item.lower_c, high=item.upper_c))
            for item in reversed(request.bins))
        bindings = tuple(OutcomeTokenBinding(bin_id=item.bin_id,
            condition_id=candidate.condition_id, yes_token_id=str(1000+index*2),
            no_token_id=str(1001+index*2))
            for index,(item,candidate) in enumerate(zip(reversed(request.bins), candidates, strict=True)))
        components = era._replacement_global_probability_components(
            served.bundle, candidates=candidates, bindings=bindings)
        assert components is not None
        samples, point, basis = components
        assert point == pytest.approx([q[item.bin_id] for item in reversed(request.bins)])
        identity = dict(family_key=served.bundle.family_id, bindings=bindings,
            q_version=served.bundle.posterior_identity_hash,
            resolution_identity="Hong Kong:HKO_HQ:hko:C:low:2026-10-01",
            topology_identity=served.bundle.bin_topology_hash,
            posterior_identity_hash=served.bundle.posterior_identity_hash,
            source_truth_identity=served.bundle.dependency_hash,
            authority_certificate_hash=hashlib.sha256(cert["provenance_json"].encode()).hexdigest(),
            band_alpha=.05, band_basis=basis, yes_point_q=point, yes_q_samples=samples,
            captured_at_utc=_hko_dt(20, 1))
        witness = JointOutcomeProbabilityWitness(**identity, max_age=timedelta(minutes=5),
            witness_identity=joint_probability_witness_identity(**identity))
        for index,binding in enumerate(bindings):
            points, means = [], []
            for direction,side in (("buy_yes", "YES"), ("buy_no", "NO")):
                held = SimpleNamespace(condition_id=binding.condition_id, direction=direction,
                    token_id=binding.yes_token_id, no_token_id=binding.no_token_id)
                held_point = monitor_refresh._current_global_held_point_probability(held, witness)
                held_samples = monitor_refresh._current_global_held_samples(
                    held, witness, current_token_pair=(binding.yes_token_id, binding.no_token_id))
                expected_point = q[binding.bin_id] if side == "YES" else 1-q[binding.bin_id]
                assert held_point == pytest.approx(expected_point, abs=1e-12)
                assert held_samples == pytest.approx(samples[:, index] if side == "YES" else 1-samples[:, index])
                sell_mean = era._global_sell_held_probability(
                    SimpleNamespace(bin_id=binding.bin_id, side=side), witness)
                assert sell_mean == pytest.approx(float(held_samples.mean()))
                points.append(held_point)
                means.append(sell_mean)
            assert sum(points) == pytest.approx(1.0)
            assert sum(means) == pytest.approx(1.0)
    current_request = replace(request, computed_at=_hko_dt(20, 1))
    sql_clock[0] = current_request.computed_at
    current = materialize_replacement_forecast_live(conn, current_request)
    assert current.ok, current.reason_codes
    conn.commit()  # Publish the new independent canonical anchor before public replay.
    current_row = conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",
                               (current.posterior_id,)).fetchone()
    assert datetime.fromisoformat(current_row["recorded_at"]) == current_request.computed_at
    current_provenance = json.loads(current_row["provenance_json"])
    current_shape = current_provenance["bayes_precision_fusion"]["current_evidence_shape"]
    assert current_shape["provider_geometry_audit"]["anchor_station_ground"] == ground_b
    assert current_shape["provider_geometry_identity_hash"] == shape["provider_geometry_identity_hash"]
    assert json.loads(current_row["q_json"]) == pytest.approx(q, abs=1e-12)
    current_cert = conn.execute("SELECT * FROM readiness_state WHERE readiness_id=?",
                                (current.readiness_id,)).fetchone()
    current_readiness = ReplacementForecastReadinessDecision(
        readiness_id=current_cert["readiness_id"], status=current_cert["status"],
        reason_codes=tuple(json.loads(current_cert["reason_codes_json"])),
        dependency_json=json.loads(current_cert["dependency_json"]),
        provenance_json=json.loads(current_cert["provenance_json"]),
        expires_at=datetime.fromisoformat(current_cert["expires_at"]),
    )
    for purpose in ReplacementForecastAuthorityPurpose:
        current_served = read_replacement_forecast_bundle(
            conn, baseline_bundle=_BaselineBundle(_Evidence("new12")), readiness=current_readiness,
            city="Hong Kong", target_date=request.target_date, temperature_metric="low",
            decision_time=current_request.computed_at.isoformat(),
            current_bin_topology_hash=current_row["bin_topology_hash"], enforce_raw_input_hwm=True,
            authority_purpose=purpose,
        )
        assert current_served.ok, (purpose, current_served.reason_code)
        assert current_served.bundle.posterior_id == current.posterior_id
        assert dict(current_served.bundle.q) == pytest.approx(q, abs=1e-12)
    # TEST_ONLY_SYNTHETIC_EXTERNAL_CONDITION: change the retained official
    # product's ground value, then acquire its original A bytes again. This
    # exercises normal possession/RESET, not an actual station-height change.
    conn.commit()
    original_ground_row = tuple(conn.execute(
        "SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?",
        (ground_a["artifact_id"],)).fetchone())
    changed_ground_body = original_ground_body.replace(
        b'<td class="td1_normal_class">32</td>',
        b'<td class="td1_normal_class">33</td>', 1)
    assert changed_ground_body != original_ground_body
    official_body.write_bytes(changed_ground_body)
    ground_claims["Hong Kong"]["station_ground_proof"].update(
        elevation_m=33.0, body_sha256=hashlib.sha256(changed_ground_body).hexdigest(),
        checked_at=_hko_dt(20, 2).isoformat())
    registry.write_text(json.dumps(ground_claims))
    ground_clock[0] = _hko_dt(20, 2) + timedelta(seconds=30)
    changed_ground = ground.archive_station_ground_evidence(
        db, ["Hong Kong"])["archived"]["Hong Kong"]
    assert changed_ground["facts"]["elevation_m"] == 33.0
    assert changed_ground["facts_identity"] != ground_a["facts_identity"]
    # The real runtime registry/page are now C33, not A32. A frozen
    # certificate still replays its own canonical knowledge at the old cut.
    assert current_evidence_shape_has_entry_authority(provenance, **authority_scope)
    assert current_evidence_shape_has_held_authority(provenance, **authority_scope)
    for purpose in ReplacementForecastAuthorityPurpose:
        historical = read_replacement_forecast_bundle(
            conn, baseline_bundle=_BaselineBundle(_Evidence("new12")), readiness=readiness,
            city=request.city, target_date=request.target_date, temperature_metric=request.temperature_metric,
            decision_time=request.computed_at.isoformat(),
            current_bin_topology_hash=posterior["bin_topology_hash"], enforce_raw_input_hwm=True,
            authority_purpose=purpose)
        assert historical.ok, (purpose, historical.reason_code)
        assert historical.bundle.posterior_id == rebuilt.posterior_id
        assert dict(historical.bundle.q) == pytest.approx(q, abs=1e-12)
    changed_request = replace(request, computed_at=_hko_dt(20, 3))
    sql_clock[0] = changed_request.computed_at
    new_scope = {**authority_scope, "materialized_at": changed_request.computed_at}
    assert not current_evidence_shape_has_entry_authority(provenance, **new_scope)
    assert not current_evidence_shape_has_held_authority(provenance, **new_scope)
    # The unchanged A request is not licensed by B; this is an explicit
    # degradation, not a synthetic B forecast/probability certificate.
    changed = materialize_replacement_forecast_live(conn, changed_request)
    assert not changed.ok
    assert changed.reason_codes == ("OM9_STATION_GROUND_ENTITY_NOT_POSSESSED",)
    for purpose in ReplacementForecastAuthorityPurpose:
        stale_ground = read_replacement_forecast_bundle(
            conn, baseline_bundle=_BaselineBundle(_Evidence("new12")), readiness=current_readiness,
            city=request.city, target_date=request.target_date, temperature_metric=request.temperature_metric,
            decision_time=changed_request.computed_at.isoformat(),
            current_bin_topology_hash=current_row["bin_topology_hash"], enforce_raw_input_hwm=True,
            authority_purpose=purpose)
        assert not stale_ground.ok
        assert "basis=station_ground_current_facts_changed" in stale_ground.reason_code
    # Normal metadata captures C33 from the actual current official body;
    # neither old A geometry nor its probability is pasted onto the new cut.
    rebound_request = replace(changed_request, openmeteo_precision_guard=_hko_precision_guard(
        decision_at=changed_request.computed_at, raw_payload_bytes=anchor_bytes))
    rebound = materialize_replacement_forecast_live(conn, rebound_request)
    assert rebound.ok, rebound.reason_codes
    conn.commit()
    rebound_row = conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?", (rebound.posterior_id,)).fetchone()
    assert datetime.fromisoformat(rebound_row["recorded_at"]) == rebound_request.computed_at
    rebound_provenance = json.loads(rebound_row["provenance_json"])
    rebound_shape = rebound_provenance["bayes_precision_fusion"]["current_evidence_shape"]
    assert rebound_shape["provider_geometry_audit"]["anchor_station_ground"] == changed_ground
    assert rebound_shape["provider_geometry_identity_hash"] != shape["provider_geometry_identity_hash"]
    rebound_mu = rebound_provenance["bayes_precision_fusion"]["anchor_value_c"]
    rebound_sigma = rebound_shape["predictive_sigma_c"]
    rebound_cdf = lambda x: .5*(1+math.erf((x-rebound_mu)/(rebound_sigma*math.sqrt(2))))
    rebound_q = json.loads(rebound_row["q_json"])
    assert rebound_q == pytest.approx({"cool": rebound_cdf(20.5), "warm": rebound_cdf(30.5)-rebound_cdf(20.5),
                                      "hot": 1-rebound_cdf(30.5)}, abs=1e-12)
    rebound_cert = conn.execute("SELECT * FROM readiness_state WHERE readiness_id=?", (rebound.readiness_id,)).fetchone()
    rebound_readiness = ReplacementForecastReadinessDecision(
        readiness_id=rebound_cert["readiness_id"], status=rebound_cert["status"],
        reason_codes=tuple(json.loads(rebound_cert["reason_codes_json"])),
        dependency_json=json.loads(rebound_cert["dependency_json"]),
        provenance_json=json.loads(rebound_cert["provenance_json"]),
        expires_at=datetime.fromisoformat(rebound_cert["expires_at"]))
    for purpose in ReplacementForecastAuthorityPurpose:
        rebound_served = read_replacement_forecast_bundle(
            conn, baseline_bundle=_BaselineBundle(_Evidence("new12")), readiness=rebound_readiness,
            city=request.city, target_date=request.target_date, temperature_metric=request.temperature_metric,
            decision_time=rebound_request.computed_at.isoformat(),
            current_bin_topology_hash=rebound_row["bin_topology_hash"], enforce_raw_input_hwm=True,
            authority_purpose=purpose)
        assert rebound_served.ok, (purpose, rebound_served.reason_code)
        assert dict(rebound_served.bundle.q) == pytest.approx(rebound_q, abs=1e-12)
    conn.commit()
    official_body.write_bytes(original_ground_body)
    ground_claims["Hong Kong"]["station_ground_proof"].update(
        elevation_m=32.0, body_sha256=hashlib.sha256(original_ground_body).hexdigest(),
        checked_at=_hko_dt(20, 4).isoformat())
    registry.write_text(json.dumps(ground_claims))
    ground_clock[0] = _hko_dt(20, 4) + timedelta(seconds=30)
    confirmed_a = ground.archive_station_ground_evidence(
        db, ["Hong Kong"])["archived"]["Hong Kong"]
    assert confirmed_a["manifest_role"] == "source_capture_confirmation"
    assert confirmed_a["input_bodies"]["ground"]["artifact_id"] == ground_a["artifact_id"]
    assert confirmed_a["input_bodies"]["ground"]["captured_at"] == ground_a["captured_at"]
    assert confirmed_a["captured_at"] == _hko_dt(20, 4).isoformat()
    assert confirmed_a["recorded_at"] == ground_clock[0].isoformat()
    assert confirmed_a["facts_identity"] == ground_a["facts_identity"]
    assert tuple(conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?",
        (ground_a["artifact_id"],)).fetchone()) == original_ground_row
    assert ground.read_current_station_ground_evidence(
        db, city="Hong Kong", decision_at=changed_request.computed_at) == changed_ground
    assert ground.read_current_station_ground_evidence(
        db, city="Hong Kong", decision_at=request.computed_at) == ground_a
    reset_request = replace(request, computed_at=_hko_dt(20, 5))
    sql_clock[0] = reset_request.computed_at
    reset = materialize_replacement_forecast_live(conn, reset_request)
    assert reset.ok, reset.reason_codes
    conn.commit()  # RESET certificates are public only after canonical commit.
    reset_row = conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",
                            (reset.posterior_id,)).fetchone()
    assert datetime.fromisoformat(reset_row["recorded_at"]) == reset_request.computed_at
    reset_provenance = json.loads(reset_row["provenance_json"])
    reset_shape = reset_provenance["bayes_precision_fusion"]["current_evidence_shape"]
    assert reset_shape["provider_geometry_audit"]["anchor_station_ground"] == confirmed_a
    assert reset_shape["provider_geometry_identity_hash"] == shape["provider_geometry_identity_hash"]
    assert reset_row["dependency_hash"] != posterior["dependency_hash"]
    assert json.loads(reset_row["q_json"]) == pytest.approx(q, abs=1e-12)
    reset_cert = conn.execute("SELECT * FROM readiness_state WHERE readiness_id=?",
                             (reset.readiness_id,)).fetchone()
    reset_readiness = ReplacementForecastReadinessDecision(
        readiness_id=reset_cert["readiness_id"], status=reset_cert["status"],
        reason_codes=tuple(json.loads(reset_cert["reason_codes_json"])),
        dependency_json=json.loads(reset_cert["dependency_json"]),
        provenance_json=json.loads(reset_cert["provenance_json"]),
        expires_at=datetime.fromisoformat(reset_cert["expires_at"]))
    for purpose in ReplacementForecastAuthorityPurpose:
        reset_served = read_replacement_forecast_bundle(
            conn, baseline_bundle=_BaselineBundle(_Evidence("new12")), readiness=reset_readiness,
            city="Hong Kong", target_date=request.target_date, temperature_metric="low",
            decision_time=reset_request.computed_at.isoformat(),
            current_bin_topology_hash=reset_row["bin_topology_hash"], enforce_raw_input_hwm=True,
            authority_purpose=purpose)
        assert reset_served.ok, (purpose, reset_served.reason_code)
        assert reset_served.bundle.posterior_id == reset.posterior_id
        assert dict(reset_served.bundle.q) == pytest.approx(q, abs=1e-12)
        reset_samples, reset_point, reset_basis = era._replacement_global_probability_components(
            reset_served.bundle, candidates=candidates, bindings=bindings)
        assert reset_point == pytest.approx(point, abs=1e-12)
        reset_identity = {**identity, "q_version": reset_served.bundle.posterior_identity_hash,
            "posterior_identity_hash": reset_served.bundle.posterior_identity_hash,
            "source_truth_identity": reset_served.bundle.dependency_hash,
            "authority_certificate_hash": hashlib.sha256(reset_cert["provenance_json"].encode()).hexdigest(),
            "band_basis": reset_basis, "yes_point_q": reset_point,
            "yes_q_samples": reset_samples, "captured_at_utc": reset_request.computed_at}
        reset_witness = JointOutcomeProbabilityWitness(**reset_identity, max_age=timedelta(minutes=5),
            witness_identity=joint_probability_witness_identity(**reset_identity))
        for index, binding in enumerate(bindings):
            for direction, side in (("buy_yes", "YES"), ("buy_no", "NO")):
                held = SimpleNamespace(condition_id=binding.condition_id, direction=direction,
                    token_id=binding.yes_token_id, no_token_id=binding.no_token_id)
                expected_point = q[binding.bin_id] if side == "YES" else 1-q[binding.bin_id]
                assert monitor_refresh._current_global_held_point_probability(
                    held, reset_witness) == pytest.approx(expected_point, abs=1e-12)
                expected_samples = reset_samples[:, index] if side == "YES" else 1-reset_samples[:, index]
                assert monitor_refresh._current_global_held_samples(held, reset_witness,
                    current_token_pair=(binding.yes_token_id, binding.no_token_id)) == pytest.approx(expected_samples)
                assert era._global_sell_held_probability(SimpleNamespace(bin_id=binding.bin_id, side=side),
                    reset_witness) == pytest.approx(float(expected_samples.mean()))
    conn.commit()
    ground_clock[0] = _hko_dt(20, 6)
    assert ground.archive_station_ground_evidence(db, ["Hong Kong"])["archived"]["Hong Kong"] == confirmed_a
    conn.close()
    sql_builtins.close()


@pytest.mark.usefixtures("_hko_source_surface")
def test_normal_current_writer_rebuilds_v6_posterior_after_literal_v5_refusal(tmp_path, monkeypatch):
    _normal_hko_writer_proof_relationship(tmp_path, monkeypatch, include_raw_ifs=True)


@pytest.mark.usefixtures("_hko_source_surface")
def test_normal_anchor_only_writer_rebuilds_v6_and_public_entry_held_own_proof(tmp_path, monkeypatch):
    _normal_hko_writer_proof_relationship(tmp_path, monkeypatch, include_raw_ifs=False)


def _built_low_revision_request(tmp_path: Path) -> ReplacementForecastMaterializeRequest:
    """Pass a real seed through the production JSON builder and dataclass adapter."""
    from src.data.replacement_forecast_materialization_request_builder import (
        build_materialize_request_dataclass,
        build_replacement_forecast_materialization_request,
    )
    from tests.test_replacement_forecast_materialization_request_builder import _write_inputs

    seed = _write_inputs(tmp_path)
    from dataclasses import asdict

    (tmp_path / str(seed["openmeteo_payload_json"])).write_bytes(_hko_raw_openmeteo_bytes())
    (tmp_path / str(seed["precision_metadata_json"])).write_text(
        json.dumps(asdict(_hko_precision_guard().metadata), default=str), encoding="utf-8",
    )
    seed.update(
        city="Hong Kong", city_id="Hong Kong", city_timezone="Asia/Hong_Kong", target_date="2026-10-01",
        temperature_metric="low",
        source_cycle_time=_hko_dt(12).isoformat(),
        computed_at=_hko_dt(20).isoformat(),
        expires_at=_hko_dt(22).isoformat(),
        baseline_source_run_id="new12",
        baseline_data_version=_current_baseline_data_version("low"),
        baseline_source_available_at=_hko_dt(12, 5).isoformat(),
        day0_observation_state="zero_target_date_observations",
        openmeteo_source_cycle_time=_hko_dt(0).isoformat(),
        openmeteo_source_available_at=_hko_dt(3).isoformat(),
    )
    built = build_replacement_forecast_materialization_request(
        seed, base_dir=tmp_path,
    )
    assert built.ok is True, built.reason_codes
    assert built.request is not None
    return build_materialize_request_dataclass(built.request, base_dir=tmp_path)


@pytest.mark.parametrize("world_shadow", ("empty", "retired_only"))
@pytest.mark.usefixtures("_hko_source_surface")
def test_low_revision_real_request_prefers_forecast_authority_over_attached_world_shadow(
    tmp_path: Path, world_shadow: str,
) -> None:
    """The materializer's attached world ghosts cannot hide current forecast coverage."""
    from src.data.replacement_input_hwm import (
        latest_eligible_ensemble_input_cycle,
    )

    conn = _low_revision_authority_conn()
    request = _built_low_revision_request(tmp_path)
    conn.execute("ATTACH DATABASE ':memory:' AS world")
    conn.execute("CREATE TABLE world.source_run AS SELECT * FROM main.source_run WHERE 0")
    conn.execute(
        "CREATE TABLE world.source_run_coverage AS "
        "SELECT * FROM main.source_run_coverage WHERE 0"
    )
    if world_shadow == "retired_only":
        conn.execute(
            "INSERT INTO world.source_run "
            "SELECT * FROM main.source_run WHERE source_run_id='old18'"
        )
        conn.execute(
            "INSERT INTO world.source_run_coverage "
            "SELECT * FROM main.source_run_coverage WHERE source_run_id='old18'"
        )

    assert latest_eligible_ensemble_input_cycle(
        conn, city=request.city, target_date=request.target_date,
        metric="low", decision_time=request.computed_at,
    ) == _hko_dt(12)
    assert materializer_mod._cycle_monotone_block_reasons(
        conn, request, metric="low"
    ) == ()


@pytest.mark.usefixtures("_hko_source_surface")
def test_low_revision_real_request_prefers_attached_forecasts_over_world_main_shadow(
    tmp_path: Path,
) -> None:
    """The source selector keeps attached forecasts ahead of world-main ghosts."""
    from src.data.replacement_input_hwm import latest_eligible_ensemble_input_cycle
    forecast_db = tmp_path / "forecasts.db"
    forecast_conn = _low_revision_authority_conn(forecast_db)
    forecast_conn.close()
    request = _built_low_revision_request(tmp_path)
    world_conn = sqlite3.connect(":memory:")
    world_conn.row_factory = sqlite3.Row
    world_conn.execute("ATTACH DATABASE ? AS forecasts", (str(forecast_db),))
    world_conn.execute(
        "CREATE TABLE source_run AS SELECT * FROM forecasts.source_run WHERE 0"
    )
    world_conn.execute(
        "CREATE TABLE source_run_coverage AS "
        "SELECT * FROM forecasts.source_run_coverage WHERE 0"
    )

    assert latest_eligible_ensemble_input_cycle(
        world_conn, city=request.city, target_date=request.target_date,
        metric="low", decision_time=request.computed_at,
    ) == _hko_dt(12)


@pytest.mark.parametrize(
    "table_name", ("source_run", "source_run_coverage", "ensemble_snapshots")
)
@pytest.mark.usefixtures("_hko_source_surface")
def test_low_revision_world_only_fallback_preserves_attached_authority(
    tmp_path: Path, table_name: str,
) -> None:
    """A world-only legacy reader keeps its explicitly attached authority."""
    from src.data.replacement_input_hwm import (
        _authority_table_ref,
        latest_eligible_ensemble_input_cycle,
    )

    world_db = tmp_path / "world.db"
    world_writer = _low_revision_authority_conn(world_db)
    world_writer.close()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ? AS world", (str(world_db),))

    assert _authority_table_ref(conn, table_name) == f"world.{table_name}"
    assert latest_eligible_ensemble_input_cycle(
        conn, city="Hong Kong", target_date="2026-10-01",
        metric="low", decision_time=_hko_dt(20),
    ) == _hko_dt(12)


@pytest.mark.usefixtures("_hko_source_surface")
def test_low_revision_migration_materializes_current_12z_and_rebinds_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fully evidenced old 18Z LOW yields to current window-v2 12Z q."""
    from src.data.replacement_input_hwm import latest_eligible_ensemble_input_cycle

    conn, request = _normal_low_revision_migration_fixture(monkeypatch)
    assert latest_eligible_ensemble_input_cycle(
        conn, city="Hong Kong", target_date=request.target_date,
        metric="low", decision_time=request.computed_at,
    ) == _hko_dt(12)
    prior = conn.execute("SELECT * FROM readiness_state").fetchone()
    assert prior["source_run_id"] == "posterior:1"

    result = materialize_replacement_forecast_live(conn, request)

    assert result.ok is True, result.reason_codes
    posterior = conn.execute(
        "SELECT * FROM forecast_posteriors WHERE posterior_id=?", (result.posterior_id,)
    ).fetchone()
    readiness = conn.execute(
        "SELECT * FROM readiness_state WHERE readiness_id=?", (result.readiness_id,)
    ).fetchone()
    assert posterior["source_cycle_time"] == _hko_dt(12).isoformat()
    assert json.loads(posterior["dependency_source_run_ids_json"])["baseline_b0"] == request.baseline_source_run_id
    assert readiness["status"] == "READY"
    assert str(result.posterior_id) in readiness["source_run_id"]
    assert conn.execute("SELECT count(*) FROM readiness_state WHERE strategy_key!='producer_readiness'").fetchone()[0] == 1


@pytest.mark.usefixtures("_hko_source_surface")
def test_low_revision_repeat_materialization_keeps_one_new_certificate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The identical 12Z input reuses its posterior and readiness identity."""
    conn, request = _normal_low_revision_migration_fixture(monkeypatch)

    first = materialize_replacement_forecast_live(conn, request)
    second = materialize_replacement_forecast_live(conn, request)

    assert first.ok is True
    assert second.ok is True
    assert second.posterior_id == first.posterior_id
    assert second.readiness_id == first.readiness_id
    assert conn.execute("SELECT count(*) FROM forecast_posteriors").fetchone()[0] == 2


@pytest.mark.parametrize("purpose", ("ENTRY", "HELD_REDECISION"))
@pytest.mark.usefixtures("_hko_source_surface")
def test_low_revision_bundle_consumer_reads_certified_12z_over_retained_18z(
    purpose: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ENTRY and HELD read the newly certified q despite retained old 18Z."""
    from src.data import replacement_forecast_bundle_reader as bundle_reader
    monkeypatch.setattr(bundle_reader, "datetime", _LowRevisionQueueClock)
    from src.data.replacement_forecast_bundle_reader import (
        ReplacementForecastAuthorityPurpose,
        read_replacement_forecast_bundle,
    )
    from src.data.replacement_forecast_readiness import (
        ReplacementForecastReadinessDecision,
    )

    conn, request = _normal_low_revision_migration_fixture(monkeypatch)
    result = materialize_replacement_forecast_live(conn, request)
    assert result.ok is True, result.reason_codes
    conn.commit()  # Publish the actual row/FK before independent readonly validation.
    cert = conn.execute(
        "SELECT * FROM readiness_state WHERE readiness_id=?", (result.readiness_id,),
    ).fetchone()
    posterior = conn.execute(
        "SELECT * FROM forecast_posteriors WHERE posterior_id=?", (result.posterior_id,),
    ).fetchone()
    readiness = ReplacementForecastReadinessDecision(
        readiness_id=cert["readiness_id"], status=cert["status"],
        reason_codes=tuple(json.loads(cert["reason_codes_json"])),
        dependency_json=json.loads(cert["dependency_json"]),
        provenance_json=json.loads(cert["provenance_json"]),
        expires_at=datetime.fromisoformat(cert["expires_at"]),
    )
    assert conn.execute("SELECT count(*) FROM forecast_posteriors").fetchone()[0] == 2

    served = read_replacement_forecast_bundle(
        conn, baseline_bundle=_BaselineBundle(_Evidence(request.baseline_source_run_id)),
        readiness=readiness, city="Hong Kong", target_date=request.target_date,
        temperature_metric="low", decision_time=_hko_dt(20, 1).isoformat(),
        current_bin_topology_hash=posterior["bin_topology_hash"],
        enforce_raw_input_hwm=True,
        authority_purpose=ReplacementForecastAuthorityPurpose[purpose],
    )

    assert served.ok is True, served.reason_code
    assert served.bundle is not None
    assert served.bundle.posterior_id == result.posterior_id
    assert served.bundle.source_cycle_time == _hko_dt(12).isoformat()
    assert served.bundle.baseline_source_run_id == request.baseline_source_run_id


@pytest.mark.parametrize("invalidity", ("same_revision", "expired_candidate"))
@pytest.mark.usefixtures("_hko_source_surface")
def test_low_revision_materializer_keeps_old_certificate_when_migration_is_unproven(
    invalidity: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither same-version rollback nor expired target proof writes a new q."""
    conn = _low_revision_authority_conn()
    _install_hko_live_fusion(monkeypatch, request=_low_revision_request(), snapshot_id=12, shape_cycle_time=_hko_dt(12))
    if invalidity == "same_revision":
        current = _current_baseline_data_version("low")
        conn.execute("UPDATE source_run SET dataset_id=? WHERE source_run_id='old18'", (current,))
        conn.execute("UPDATE source_run_coverage SET data_version=? WHERE source_run_id='old18'", (current,))
        conn.execute("UPDATE ensemble_snapshots SET dataset_id=? WHERE snapshot_id=18", (current,))
    else:
        conn.execute("UPDATE source_run_coverage SET expires_at=? WHERE source_run_id='new12'", (_hko_dt(19).isoformat(),))

    result = materialize_replacement_forecast_live(conn, _low_revision_request())

    assert result.ok is False
    assert "REPLACEMENT_MATERIALIZATION_SOURCE_CYCLE_REGRESSION" in result.reason_codes
    assert conn.execute("SELECT count(*) FROM forecast_posteriors").fetchone()[0] == 1
    assert conn.execute("SELECT source_run_id FROM readiness_state").fetchone()[0] == "posterior:1"


@pytest.mark.parametrize(
    ("table", "column", "where"),
    (
        ("source_run_coverage", "computed_at", "source_run_id='old18'"),
        ("source_run_coverage", "recorded_at", "source_run_id='old18'"),
        pytest.param("forecast_posteriors", "computed_at", "source_cycle_time='2026-09-30T18:00:00+00:00'",
            # Preserve the historical collected case identity while migrating
            # its actual evidence clocks with the rest of this fixture family.
            id="forecast_posteriors-computed_at-source_cycle_time='2026-06-06T18:00:00+00:00'"),
        ("ensemble_snapshots", "recorded_at", "snapshot_id=18"),
        ("ensemble_snapshots", "source_available_at", "snapshot_id=18"),
    ),
)
@pytest.mark.usefixtures("_hko_source_surface")
def test_low_revision_refuses_individual_future_incumbent_evidence(
    table: str, column: str, where: str,
) -> None:
    """Each incumbent evidence clock must independently precede the decision."""
    from src.data.replacement_input_hwm import (
        retired_low_uncertified_incumbent_yields_to_current_ensemble,
    )

    conn = _low_revision_authority_conn()
    check = lambda: retired_low_uncertified_incumbent_yields_to_current_ensemble(
        conn, city="Hong Kong", target_date="2026-10-01", metric="low",
        incoming_baseline_source_run_id="new12", decision_time=_hko_dt(20),
    )
    assert check() is True
    conn.execute(
        f"UPDATE {table} SET {column}=? WHERE {where}",
        (_hko_dt(21).isoformat(),),
    )
    assert check() is False


@pytest.mark.usefixtures("_hko_source_surface")
def test_low_revision_refuses_expired_current_target_coverage() -> None:
    """A current run loses migration authority when target coverage expires."""
    from src.data.replacement_input_hwm import (
        retired_low_uncertified_incumbent_yields_to_current_ensemble,
    )

    conn = _low_revision_authority_conn()
    conn.execute(
        "UPDATE source_run_coverage SET expires_at=? WHERE source_run_id='new12'",
        (_hko_dt(19).isoformat(),),
    )
    assert retired_low_uncertified_incumbent_yields_to_current_ensemble(
        conn, city="Hong Kong", target_date="2026-10-01", metric="low",
        incoming_baseline_source_run_id="new12", decision_time=_hko_dt(20),
    ) is False


@pytest.mark.usefixtures("_hko_source_surface")
def test_low_revision_refuses_incumbent_run_imported_after_decision() -> None:
    """A future old-run ingest cannot establish historical rollback authority."""
    from src.data.replacement_input_hwm import (
        retired_low_uncertified_incumbent_yields_to_current_ensemble,
    )

    conn = _low_revision_authority_conn()
    conn.execute(
        "UPDATE source_run SET imported_at=? WHERE source_run_id='old18'",
        (_hko_dt(21).isoformat(),),
    )
    assert retired_low_uncertified_incumbent_yields_to_current_ensemble(
        conn, city="Hong Kong", target_date="2026-10-01", metric="low",
        incoming_baseline_source_run_id="new12", decision_time=_hko_dt(20),
    ) is False


class _LowRevisionQueueClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return _hko_dt(20)


@pytest.mark.usefixtures("_hko_source_surface")
def test_low_revision_queue_boundary_and_marker_are_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The normal queue admits one exact migration seed, then its marker dedupes."""
    from src.data import replacement_forecast_live_materialization_queue as queue
    monkeypatch.setattr(queue, "datetime", _LowRevisionQueueClock)

    db_path = tmp_path / "forecast.db"
    conn = _low_revision_authority_conn(db_path)
    seed = {
        "city": "Hong Kong", "target_date": "2026-10-01",
        "temperature_metric": "low", "source_cycle_time": _hko_dt(12).isoformat(),
        "baseline_source_run_id": "new12",
    }
    assert queue._seed_source_cycle_boundary(forecast_db=db_path, seed=seed) is None
    seed_path = tmp_path / "new12.seed.json"
    seed_path.write_text("{}", encoding="utf-8")
    assert cycle_advance._record_enqueue(
        conn, city="Hong Kong", target_date="2026-10-01", metric="low",
        consumed_cycle_iso=_hko_dt(18).isoformat(), target_cycle_iso=_hko_dt(12).isoformat(),
        held_position=True, seed_file=str(seed_path),
    ) is True
    assert cycle_advance._already_enqueued(
        conn, city="Hong Kong", target_date="2026-10-01", metric="low",
        target_cycle_iso=_hko_dt(12).isoformat(),
    ) is True
    assert conn.execute("SELECT count(*) FROM cycle_advance_enqueues").fetchone()[0] == 1


@pytest.mark.usefixtures("_hko_source_surface")
def test_low_revision_queue_refuses_same_version_backward_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 18Z-to-12Z exception closes once the incumbent is current v2."""
    from src.data import replacement_forecast_live_materialization_queue as queue
    monkeypatch.setattr(queue, "datetime", _LowRevisionQueueClock)

    db_path = tmp_path / "forecast.db"
    conn = _low_revision_authority_conn(db_path)
    current = _current_baseline_data_version("low")
    conn.execute("UPDATE source_run SET dataset_id=? WHERE source_run_id='old18'", (current,))
    conn.execute("UPDATE source_run_coverage SET data_version=? WHERE source_run_id='old18'", (current,))
    conn.execute("UPDATE ensemble_snapshots SET dataset_id=? WHERE snapshot_id=18", (current,))
    conn.commit()
    seed = {
        "city": "Hong Kong", "target_date": "2026-10-01",
        "temperature_metric": "low", "source_cycle_time": _hko_dt(12).isoformat(),
        "baseline_source_run_id": "new12",
    }
    assert queue._seed_source_cycle_boundary(forecast_db=db_path, seed=seed) == (
        "current_posterior", _hko_dt(18).isoformat(),
    )


# --- Day0 carrier preflight twin: one admission predicate, the child's own verdict ---------------

_FAST_TAIL = "wu_api+same_station_fast_tail"
_CARRIER_VECTOR_MISSING = "DAY0_NOAA_PRELIMINARY_CARRIER_VECTOR_MISSING"


def _carrier_child_outcome(conn, request) -> str:
    """What the child's prepare ends with for ``request``: the parity ground truth."""
    try:
        prepared = materializer_mod.prepare_replacement_forecast_live(conn, request)
    except ValueError as exc:
        return f"RAISED:{exc}"
    if isinstance(prepared, materializer_mod.ReplacementForecastMaterializeResult):
        return "BLOCKED:" + ",".join(prepared.reason_codes)
    return "PREPARED"


def _carrier_queue_payload(request, directory: Path) -> dict:
    """The queue request JSON that rebuilds ``request`` through the child's own builder."""
    directory.mkdir(parents=True, exist_ok=True)
    raw = directory / "openmeteo.json"
    raw.write_bytes(request.openmeteo_raw_payload_bytes)
    meta = directory / "precision.json"
    meta.write_text(json.dumps(asdict(request.openmeteo_precision_guard.metadata), default=str))

    def iso(value):
        return None if value is None else (value.isoformat() if hasattr(value, "isoformat") else str(value))

    payload = {
        "city": request.city, "city_id": request.city_id, "city_timezone": request.city_timezone,
        "target_date": iso(request.target_date), "temperature_metric": request.temperature_metric,
        "source_cycle_time": iso(request.source_cycle_time), "computed_at": iso(request.computed_at),
        "expires_at": iso(request.expires_at),
        "baseline_source_run_id": request.baseline_source_run_id,
        "baseline_data_version": request.baseline_data_version,
        "baseline_source_available_at": iso(request.baseline_source_available_at),
        "openmeteo_source_run_id": request.openmeteo_source_run_id,
        "openmeteo_source_available_at": iso(request.openmeteo_source_available_at),
        "openmeteo_source_cycle_time": iso(request.openmeteo_anchor.source_cycle_time),
        "openmeteo_anchor_artifact_id": request.anchor_artifact_id,
        "openmeteo_payload_json": str(raw), "precision_metadata_json": str(meta),
        "anchor_weight": request.anchor_weight, "anchor_sigma_c": request.anchor_sigma_c,
        "settlement_step_c": request.settlement_step_c,
        "bins": [{"bin_id": b.bin_id, "lower_c": b.lower_c, "upper_c": b.upper_c, "center_c": b.center_c,
                  "display_unit": b.display_unit, "settlement_unit": b.settlement_unit,
                  "rounding_rule": b.rounding_rule} for b in request.bins],
    }
    for field in ("day0_observed_extreme_c", "day0_observed_extreme_source",
                  "day0_observed_extreme_observation_time", "day0_observed_extreme_sample_count",
                  "day0_observed_extreme_unit", "day0_observation_state"):
        value = getattr(request, field)
        if value is not None:
            payload[field] = iso(value) if field.endswith("observation_time") else value
    return payload


def _carrier_twin(conn, request, directory: Path, monkeypatch):
    """The queue preflight's verdict for ``request`` on the request's own forecast DB."""
    from src.data import replacement_forecast_live_materialization_queue as queue_mod
    from src.data.station_ground_evidence import forecast_db_from_connection

    monkeypatch.setattr(queue_mod, "_attach_world_read_only", lambda _conn: None)
    conn.commit()
    payload = _carrier_queue_payload(request, directory)
    input_json = directory / "request.json"
    input_json.write_text(json.dumps(payload))
    return queue_mod._day0_carrier_vector_preflight_reason(
        forecast_db=forecast_db_from_connection(conn), payload=payload, input_json=input_json,
    )


@pytest.mark.parametrize(("source", "computed_at", "expected"), (
    ("aviationweather_metar", _dt(18), 31.0),
    ("ogimet_metar_zspd", _dt(18), 31.0),
    ("hko_hourly_accumulator", _dt(18), 31.0),
    (_FAST_TAIL, _dt(18), 31.0),
    (_FAST_TAIL, _dt(10), None),  # the target local day has not started
    ("noaa_wrh_zspd", _dt(18), None),  # absorbing settlement channel truncates support instead
    ("wu_icao_history", _dt(18), None),
))
def test_day0_carrier_admission_is_one_predicate(source, computed_at, expected) -> None:
    """The child routes into the carrier region by exactly this predicate, twin included."""
    request = _request(
        computed_at=computed_at, day0_observed_extreme_c=31.0,
        day0_observed_extreme_source=source,
        day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
    )
    assert materializer_mod._day0_carrier_extreme_c(request) == expected


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.usefixtures("_hko_source_surface")
def test_day0_carrier_twin_suppresses_exactly_what_the_child_ends_on_vector_missing(
    tmp_path, monkeypatch: pytest.MonkeyPatch, metric: str,
) -> None:
    """Set equality over the routing branches: {twin suppresses} == {child ends VECTOR_MISSING}."""
    conn, base = _shanghai_noaa_future_request(tmp_path, monkeypatch, metric=metric)
    fast_tail = replace(base, day0_observed_extreme_source=_FAST_TAIL)
    corpus: dict[str, tuple[str, object]] = {}

    def record(name, request):
        corpus[name] = (_carrier_child_outcome(conn, request),
                        _carrier_twin(conn, request, tmp_path / name, monkeypatch))

    record("fast_tail_complete_bundle", fast_tail)
    record("noaa_preliminary_complete_bundle", base)
    assert conn.execute("SELECT 1 FROM day0_hourly_vectors WHERE model = 'icon_global'").fetchone()
    conn.execute("DELETE FROM day0_hourly_vectors WHERE model = 'icon_global'")
    conn.commit()
    record("fast_tail_incomplete_bundle", fast_tail)
    record("noaa_preliminary_incomplete_bundle", base)
    absorbing = replace(base, day0_observed_extreme_source="noaa_wrh_zspd")
    record("absorbing_settlement_channel_incomplete_bundle", absorbing)

    assert corpus["fast_tail_incomplete_bundle"] == (f"BLOCKED:{_CARRIER_VECTOR_MISSING}", _CARRIER_VECTOR_MISSING)
    assert corpus["noaa_preliminary_incomplete_bundle"] == (
        f"BLOCKED:{_CARRIER_VECTOR_MISSING}", _CARRIER_VECTOR_MISSING)
    assert corpus["fast_tail_complete_bundle"][1] is None
    assert corpus["noaa_preliminary_complete_bundle"][1] is None
    assert corpus["absorbing_settlement_channel_incomplete_bundle"][1] is None
    for name, (child, twin) in corpus.items():
        assert (twin is not None) == (child == f"BLOCKED:{_CARRIER_VECTOR_MISSING}"), (name, child, twin)


@pytest.mark.usefixtures("_hko_source_surface")
def test_day0_carrier_twin_falls_through_when_the_residual_likelihood_is_unavailable(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The residual verdict precedes the bundle read: an incomplete bundle must not mask it."""
    conn, base = _shanghai_noaa_future_request(tmp_path, monkeypatch)
    fast_tail = replace(base, day0_observed_extreme_source=_FAST_TAIL)
    conn.execute("DELETE FROM day0_hourly_vectors WHERE model = 'icon_global'")
    conn.commit()
    assert _carrier_twin(conn, fast_tail, tmp_path / "evidence", monkeypatch) == _CARRIER_VECTOR_MISSING
    monkeypatch.setattr("src.data.day0_fast_obs.build_fast_station_residual_likelihood",
                        lambda *_a, **_k: None)
    assert _carrier_child_outcome(conn, fast_tail) == "RAISED:DAY0_WU_CURRENT_CARRIER_RESIDUAL_UNAVAILABLE"
    assert _carrier_twin(conn, fast_tail, tmp_path / "no_evidence", monkeypatch) is None


@pytest.mark.usefixtures("_hko_source_surface")
def test_day0_carrier_twin_falls_through_without_a_current_temperature_state(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, base = _shanghai_noaa_future_request(tmp_path, monkeypatch, without_current_state=True)
    fast_tail = replace(base, day0_observed_extreme_source=_FAST_TAIL)
    assert _carrier_child_outcome(conn, fast_tail) in {
        "BLOCKED:DAY0_NOAA_PRELIMINARY_CARRIER_CURRENT_TEMPERATURE_STATE_MISSING",
        "RAISED:DAY0_WU_CURRENT_CARRIER_RESIDUAL_UNAVAILABLE",
    }
    assert _carrier_twin(conn, fast_tail, tmp_path / "twin", monkeypatch) is None


@pytest.mark.usefixtures("_hko_source_surface")
def test_day0_carrier_twin_falls_through_when_the_store_is_unreadable(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable vector store is an error, never the absent-bundle verdict."""
    import src.data.day0_hourly_vectors as hourly

    conn, base = _shanghai_noaa_future_request(tmp_path, monkeypatch)
    fast_tail = replace(base, day0_observed_extreme_source=_FAST_TAIL)
    conn.execute("DELETE FROM day0_hourly_vectors WHERE model = 'icon_global'")
    conn.commit()
    assert _carrier_twin(conn, fast_tail, tmp_path / "readable", monkeypatch) == _CARRIER_VECTOR_MISSING

    def unreadable(**kwargs):
        assert kwargs["raise_on_db_error"] is True
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(hourly, "read_freshest_day0_hourly_vectors", unreadable)
    assert _carrier_twin(conn, fast_tail, tmp_path / "unreadable", monkeypatch) is None


@pytest.mark.usefixtures("_hko_source_surface")
def test_day0_carrier_preflight_suppresses_once_then_reopens_on_a_new_vector_or_observation(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the real queue: no child for a doomed fast-tail request, and no clock to reopen it."""
    from src.data import replacement_forecast_live_materialization_queue as queue_mod
    from src.data.station_ground_evidence import forecast_db_from_connection

    conn, base = _shanghai_noaa_future_request(tmp_path, monkeypatch)
    fast_tail = replace(base, day0_observed_extreme_source=_FAST_TAIL)
    rows = conn.execute("SELECT * FROM day0_hourly_vectors WHERE model = 'icon_global'").fetchall()
    columns = rows[0].keys()
    conn.execute("DELETE FROM day0_hourly_vectors WHERE model = 'icon_global'")
    conn.commit()
    db = forecast_db_from_connection(conn)
    monkeypatch.setattr(queue_mod, "_attach_world_read_only", lambda _conn: None)
    monkeypatch.setattr(queue_mod, "_seed_source_cycle_boundary", lambda **_k: None)
    monkeypatch.setattr(queue_mod, "_seed_already_covered", lambda **_k: False)
    verdicts: list[bool] = []
    real_verdict = materializer_mod.day0_carrier_vector_missing

    def traced(conn_, request_):
        verdicts.append(real_verdict(conn_, request_))
        return verdicts[-1]

    monkeypatch.setattr(materializer_mod, "day0_carrier_vector_missing", traced)
    spawned: list[list[str]] = []

    def runner(argv):
        spawned.append(list(argv))
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="not a verdict")

    def run_queue(payload):
        request_dir = tmp_path / "requests"
        request_dir.mkdir(exist_ok=True)
        (request_dir / "Shanghai.request.json").write_text(json.dumps(payload))
        return queue_mod.process_replacement_forecast_live_materialization_queue(
            request_dir=request_dir, processed_dir=tmp_path / "processed", failed_dir=tmp_path / "failed",
            forecast_db=db, raw_manifest_dir=None, limit=1, runner=runner, discover=False,
        )

    payload = _carrier_queue_payload(fast_tail, tmp_path / "inputs")
    report = run_queue(payload)
    assert spawned == [] and verdicts == [True]
    assert queue_mod._DAY0_CARRIER_VECTOR_MISSING_REASON in report.reason_codes
    receipt = json.loads(next((tmp_path / "blocked_latest").glob("*.json")).read_text())
    assert receipt["status"] == "BLOCKED_MISSING_PROBABILITY_AUTHORITY"
    assert receipt["result_evidence"]["subprocess_spawned"] is False
    assert next((tmp_path / "blocked_attempts").glob("*.json")), "the verdict is bound to input identity"

    # Identical bytes and identical facts: the unchanged marker answers; nothing is re-derived.
    again = run_queue(payload)
    assert spawned == [] and verdicts == [True]
    assert queue_mod._UNCHANGED_BLOCKED_SKIP_REASON in again.reason_codes

    # A new observation changes the request identity: it is evaluated afresh, on its own cut.
    observed = (datetime.fromisoformat(payload["day0_observed_extreme_observation_time"])
                + timedelta(minutes=1)).isoformat()
    run_queue({**payload, "day0_observed_extreme_observation_time": observed})
    assert verdicts == [True, True] and spawned == []

    # A new hourly vector changes the fact the verdict depends on: the child runs immediately.
    for row in rows:
        conn.execute(
            f"INSERT INTO day0_hourly_vectors ({','.join(columns)}) VALUES ({','.join('?' * len(columns))})",
            tuple(row))
    conn.commit()
    run_queue(payload)
    assert verdicts == [True, True, False]
    assert len(spawned) == 1


@pytest.mark.usefixtures("_hko_source_surface")
def test_day0_carrier_preflight_reopens_when_a_new_current_temperature_print_lands(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A print the request's cut did not yet possess changes the next request, not a clock.

    The doomed request is bound to its facts. The same family's next request carries the
    newly possessed print (the state identity moves the attempt fingerprint) and is judged on
    its own cut: here the longer window now needs a vector the bundle lacks, so it stays doomed
    on its own evidence; once that vector exists the child runs at once.
    """
    from src.data import day0_fast_obs as fast
    from src.data import replacement_forecast_live_materialization_queue as queue_mod
    from src.data.station_ground_evidence import forecast_db_from_connection

    conn, base = _shanghai_noaa_future_request(tmp_path, monkeypatch)
    fast_tail = replace(base, day0_observed_extreme_source=_FAST_TAIL)
    conn.execute("DELETE FROM day0_hourly_vectors WHERE model = 'icon_global'")
    conn.commit()
    db = forecast_db_from_connection(conn)
    monkeypatch.setattr(queue_mod, "_attach_world_read_only", lambda _conn: None)
    payload = _carrier_queue_payload(fast_tail, tmp_path / "inputs")
    seed = tmp_path / "seed.json"

    def fingerprint(body):
        return queue_mod._blocked_attempt_fingerprint(input_json=seed, forecast_db=db, payload=body)

    before = fingerprint(payload)
    assert before is not None
    assert _carrier_twin(conn, fast_tail, tmp_path / "before", monkeypatch) == _CARRIER_VECTOR_MISSING

    city = __import__("src.config", fromlist=["runtime_cities_by_name"]).runtime_cities_by_name()[fast_tail.city]
    observed = fast_tail.computed_at + timedelta(minutes=1)
    fetched = observed + timedelta(minutes=1)

    class CaptureClock(fast.datetime):
        @classmethod
        def now(cls, tz=None):
            return fetched.astimezone(tz) if tz else fetched.replace(tzinfo=None)

    monkeypatch.setattr(fast, "datetime", CaptureClock)
    reports = fast.parse_metar_api_payload([{"icaoId": "ZSPD", "obsTime": observed.timestamp(),
        "receiptTime": fetched.isoformat(), "temp": 30., "metarType": "METAR",
        "rawOb": f"ZSPD {observed:%d%H%M}Z 30/20 T03000200"}])
    source = fast.fast_obs_source_for_city(city, fast_tail.target_date)
    assert fast._append_metar_prints_to_ledger(conn, ((city, source, str(fast_tail.target_date)),), reports)
    conn.commit()

    # The old cut did not possess the print: its verdict and its fingerprint are unchanged.
    assert fingerprint(payload) == before
    assert _carrier_twin(conn, fast_tail, tmp_path / "same_cut", monkeypatch) == _CARRIER_VECTOR_MISSING
    # The next request names the newly possessed print: a new attempt on a new cut.
    later = {**payload, "computed_at": (fetched + timedelta(minutes=1)).isoformat(),
             "day0_observed_extreme_observation_time": observed.isoformat(),
             "day0_current_temperature_state": {"value_native": 30.0, "source": "aviationweather_metar",
                                                "observed_at_utc": observed.astimezone(UTC).isoformat()}}
    assert fingerprint(later) != before
