# Created: 2026-10-06
# Last reused/audited: 2026-10-06
# Lifecycle: created=2026-10-06; last_reviewed=2026-10-06; last_reused=2026-10-06
# Purpose: A station without provable point ground (MPMG, ZSQD) passes the OM9 elevation
#   predicates only when they hold over the whole hull of its published heights; point ground
#   stays UNPROVEN and VERIFIED stations are unchanged.
# Reuse: pytest tests/test_om9_bounded_station_ground.py
# Authority basis: operator mandate 2026-10-05 (every listed city continuously current; never
#   loosen freshness/authority; gate on the domain predicate, not a proxy; no hand-entered
#   values); AGENTS §2 INV-47; config/AGENTS.md station_height_bound.
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import src.config as config
from src.data import station_ground_evidence as ground
from src.data import openmeteo_ecmwf_ifs9_precision_guard as guard
from src.data.openmeteo_ecmwf_ifs9_precision_guard import (
    OpenMeteoIfs9PrecisionMetadata, evaluate_openmeteo_ecmwf_ifs9_precision_guard,
)
from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema

UTC = timezone.utc
BOUND = {"Qingdao": "ZSQD", "Panama City": "MPMG"}
# Old coverage shape; a VERIFIED station must keep exactly these keys.
COVERAGE_KEYS = {"revision", "status", "reason", "target_start_utc", "target_end_utc",
                 "station_id", "facts_identity", "applicability_identity"}


def _rows():
    return json.loads((config.PROJECT_ROOT / "config/station_precise_coords.json").read_text())


def _bodies(station):
    kind = config.STATION_HEIGHT_BOUND_KIND
    primary = config.PROJECT_ROOT / config.station_ground_source_artifact_ref(source_kind=kind, station_id=station)
    bridge = config.PROJECT_ROOT / config.station_ground_identity_bridge_artifact_ref(source_kind=kind, station_id=station)
    return primary.read_bytes(), bridge.read_bytes()


def _facts(station, primary, bridge):
    return config.station_ground_facts_from_bytes(source_kind=config.STATION_HEIGHT_BOUND_KIND,
        station_id=station, raw_body=primary, identity_bridge_bytes=bridge)


@pytest.mark.parametrize("city,station", sorted(BOUND.items()))
def test_real_bound_bodies_replay_to_a_bounded_hull_never_ground(city, station):
    primary, bridge = _bodies(station)
    claim = _rows()[city]["station_height_bound"]
    assert hashlib.sha256(primary).hexdigest() == claim["body_sha256"]
    assert hashlib.sha256(bridge).hexdigest() == claim["bridge"]["body_sha256"]
    facts = _facts(station, primary, bridge)
    assert all(claim[key] == value for key, value in facts.items())
    # The hull is the two records' displayed-precision intervals, nothing typed by hand.
    feet = Decimal(json.loads(primary)["STATION"][0]["ELEVATION"])
    metres = next(row["elev"] for row in json.loads(bridge) if row["icaoId"] == station)
    assert facts["elevation_min_m"] == float(min((feet - Decimal("0.5")) * Decimal("0.3048"), Decimal(metres) - Decimal("0.5")))
    assert facts["elevation_max_m"] == float(max((feet + Decimal("0.5")) * Decimal("0.3048"), Decimal(metres) + Decimal("0.5")))
    assert "elevation_m" not in facts and facts["revision"] == config.STATION_HEIGHT_BOUND_REVISION
    geometry = config.runtime_station_geometry_for_city(config.cities_by_name[city])
    assert geometry["ground_status"] == "BOUNDED"
    assert geometry["ground_reason"] == "STATION_GROUND_POINT_UNPROVEN"
    assert geometry["ground_elevation_m"] is None
    assert config.station_ground_status(geometry["ground_facts"]) == "BOUNDED"
    assert b"token" not in primary.lower()


def _private_config(tmp_path, monkeypatch, cities, mutate=None):
    rows = _rows()
    for city in cities:
        for key in ("station_ground_proof", "station_height_bound"):
            claim = rows[city].get(key)
            if isinstance(claim, dict):
                for ref in (claim["artifact_ref"], claim.get("bridge", {}).get("artifact_ref")):
                    if ref:
                        (tmp_path / Path(ref).name).write_bytes((config.PROJECT_ROOT / ref).read_bytes())
    if mutate is not None:
        mutate(tmp_path, rows)
    (tmp_path / "station_precise_coords.json").write_text(json.dumps(rows))
    (tmp_path / "cities.json").write_bytes((config.PROJECT_ROOT / "config/cities.json").read_bytes())
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    return rows


def _status(city):
    return config.runtime_station_geometry_for_city(config.cities_by_name[city])["ground_status"]


@pytest.mark.parametrize("mutation", ("as_ground_proof", "hand_edited_hull", "body_hash", "bridge_hash",
    "verified_kind_as_bound", "awc_height_not_whole_metres", "awc_station_missing", "possession_not_max"))
def test_a_bound_claim_cannot_pose_as_ground_or_drift_from_its_bodies(tmp_path, monkeypatch, mutation):
    def mutate(root, rows):
        claim = rows["Qingdao"]["station_height_bound"]
        if mutation == "as_ground_proof":
            rows["Qingdao"]["station_ground_proof"] = rows["Qingdao"].pop("station_height_bound")
        elif mutation == "hand_edited_hull":
            claim["elevation_max_m"] = 9.0
        elif mutation == "body_hash":
            claim["body_sha256"] = "0" * 64
        elif mutation == "bridge_hash":
            claim["bridge"]["body_sha256"] = "0" * 64
        elif mutation == "verified_kind_as_bound":
            other = dict(rows["Tel Aviv"]["station_ground_proof"], station_id="ZSQD")
            rows["Qingdao"]["station_height_bound"] = other
        elif mutation in ("awc_height_not_whole_metres", "awc_station_missing"):
            path = root / Path(claim["bridge"]["artifact_ref"]).name
            body = json.loads(path.read_bytes())
            if mutation == "awc_station_missing":
                body = [row for row in body if row["icaoId"] != "ZSQD"]
            else:
                next(row for row in body if row["icaoId"] == "ZSQD")["elev"] = 2.4
            path.write_bytes(json.dumps(body).encode())
            claim["bridge"]["body_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif mutation == "possession_not_max":
            claim["checked_at"] = claim["bridge"]["checked_at"]
    _private_config(tmp_path, monkeypatch, ("Qingdao", "Tel Aviv"))
    assert _status("Qingdao") == "BOUNDED"
    _private_config(tmp_path, monkeypatch, ("Qingdao", "Tel Aviv"), mutate)
    assert _status("Qingdao") == "UNPROVEN"


def test_a_ground_proof_always_outranks_a_height_bound(tmp_path, monkeypatch):
    def mutate(root, rows):
        rows["Tel Aviv"]["station_height_bound"] = rows["Qingdao"]["station_height_bound"]
    _private_config(tmp_path, monkeypatch, ("Qingdao", "Tel Aviv"), mutate)
    assert _status("Tel Aviv") == "VERIFIED"


def test_a_changed_published_height_is_a_new_fact_identity():
    primary, bridge = _bodies("ZSQD")
    payload = json.loads(primary)
    payload["STATION"][0]["ELEVATION"] = "34.0"
    moved = _facts("ZSQD", json.dumps(payload).encode(), bridge)
    original = _facts("ZSQD", primary, bridge)
    assert moved["elevation_max_m"] != original["elevation_max_m"]
    assert ground.ground_facts_identity(moved) != ground.ground_facts_identity(original)


# Guard predicates over the hull, isolated from source authenticity (own tests below).
def _metadata(*, city_class, grid, hull=None, point=None):
    proof = None
    if hull is not None:
        proof = {"station_ground_proof": {"revision": "station_ground_roles_v1", "status": "BOUNDED",
            "facts": {"revision": config.STATION_HEIGHT_BOUND_REVISION,
                      "elevation_min_m": hull[0], "elevation_max_m": hull[1]}}}
    return OpenMeteoIfs9PrecisionMetadata(city="Synthetic", station_id="ZZZZ", city_lat=40.0, city_lon=10.0,
        station_lat=40.0, station_lon=10.0, requested_lat=40.0, requested_lon=10.0,
        requested_coordinate_precision_decimals=4, nearest_grid_lat=40.01, nearest_grid_lon=10.01,
        nearest_grid_distance_km=1.4, native_grid="openmeteo_ecmwf_ifs_9km", delivery_grid_resolution="9km",
        interpolation_method="openmeteo_api_point_interpolation", endpoint_mode="hourly_zeus_aggregated",
        local_day_start_utc=datetime(2026, 10, 6, tzinfo=UTC), local_day_end_utc=datetime(2026, 10, 7, tzinfo=UTC),
        timezone_name="UTC", target_local_date=date(2026, 10, 6), temperature_unit="celsius", anchor_sigma_c=3.0,
        grid_elevation_m=grid, station_elevation_m=point, land_sea_mask="land", city_class=city_class,
        station_mapping_policy="settlement_station_reference", source_geometry_proof=proof)


@pytest.fixture
def _no_authenticity(monkeypatch):
    monkeypatch.setattr(guard, "geometry_proof_authenticity_reason", lambda *_a, **_k: None)


@pytest.mark.parametrize("city_class", ("mountain", "valley"))
def test_terrain_review_holds_over_the_whole_hull(_no_authenticity, city_class):
    # |grid - h| spans [90, 110] m: a point at either end differs, the hull must not pass.
    straddle = evaluate_openmeteo_ecmwf_ifs9_precision_guard(_metadata(city_class=city_class, grid=150.0, hull=(40.0, 60.0)),
        decision_at=datetime.now(UTC))
    assert not straddle.passable_for_live_materialization
    assert "OM9_TERRAIN_ELEVATION_REVIEW_REQUIRED" in straddle.reason_codes
    assert evaluate_openmeteo_ecmwf_ifs9_precision_guard(_metadata(city_class=city_class, grid=150.0, point=60.0),
        decision_at=datetime.now(UTC)).passable_for_live_materialization
    inside = evaluate_openmeteo_ecmwf_ifs9_precision_guard(_metadata(city_class=city_class, grid=150.0, hull=(60.0, 80.0)),
        decision_at=datetime.now(UTC))
    assert inside.status == "PASS" and inside.elevation_delta_m is None  # no point delta exists


def test_elevation_delta_high_holds_over_the_whole_hull(_no_authenticity):
    # |grid - h| spans [240, 260] m.
    straddle = evaluate_openmeteo_ecmwf_ifs9_precision_guard(_metadata(city_class="coastal", grid=300.0, hull=(40.0, 60.0)),
        decision_at=datetime.now(UTC))
    assert not straddle.passable_for_live_materialization and "OM9_ELEVATION_DELTA_HIGH" in straddle.reason_codes
    assert evaluate_openmeteo_ecmwf_ifs9_precision_guard(_metadata(city_class="coastal", grid=300.0, point=60.0),
        decision_at=datetime.now(UTC)).status == "PASS"


@pytest.mark.parametrize("hull", (None, (60.0, 40.0), (float("nan"), 10.0)))
def test_absent_or_malformed_hull_requires_elevation_metadata(_no_authenticity, hull):
    result = evaluate_openmeteo_ecmwf_ifs9_precision_guard(_metadata(city_class="coastal", grid=30.0, hull=hull),
        decision_at=datetime.now(UTC))
    assert result.status == "BLOCK" and "OM9_ELEVATION_METADATA_REQUIRED" in result.reason_codes


# End to end: real registry rows/bodies, normal archive, producer metadata, real guard.
def _seed_setup(tmp_path, monkeypatch, city_name):
    from tests.test_openmeteo_ecmwf_ifs9_bucket_transport import _actual_o1280_static_fixture
    _private_config(tmp_path, monkeypatch, (city_name,))
    monkeypatch.setattr(ground, "_store_root", lambda: tmp_path / "state" / "station_ground")
    clock = [datetime(2026, 10, 6, 6, 30, tzinfo=UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz or UTC)

    monkeypatch.setattr(ground, "datetime", Clock)
    db = tmp_path / "zeus-forecasts.db"
    with sqlite3.connect(db) as conn:
        ensure_replacement_forecast_live_schema(conn)
    report = ground.archive_station_ground_evidence(db, [city_name])
    assert report["status"] == "GROUND_SOURCE_ARCHIVED", report
    transport, path, _, _, surface_clock, _ = _actual_o1280_static_fixture(tmp_path, monkeypatch)
    surface_clock[0] = clock[0]
    monkeypatch.setattr(transport, "HSURF_LOCAL_CACHE", str(path))
    monkeypatch.setattr(config, "state_path", lambda filename: tmp_path / "state" / filename)
    city = config.runtime_cities_by_name()[city_name]
    points, _, center = transport.om_get_surrounding_gridpoints(city.lat, city.lon)
    selected = transport.om_get_coordinates(points[center])
    zone = ZoneInfo(city.timezone)
    target = (clock[0].astimezone(zone) + timedelta(days=1)).date()
    longitude = (selected.grid_longitude_east + 180.0) % 360.0 - 180.0  # provider reports [-180, 180]
    payload = {"latitude": selected.grid_latitude, "longitude": longitude, "elevation": 19.0,
        "timezone": city.timezone, "utc_offset_seconds": int(clock[0].astimezone(zone).utcoffset().total_seconds()),
        "hourly_units": {"temperature_2m": "°C"},
        "hourly": {"time": [f"{target.isoformat()}T{hour:02d}:00" for hour in range(24)], "temperature_2m": [24.] * 24}}
    raw = (json.dumps(payload, sort_keys=True) + "\n").encode()
    return db, clock, report["archived"][city_name], raw, target


def _seed_guard(city_name, raw, target, cut, evidence):
    from scripts.download_replacement_forecast_current_targets import _precision_metadata
    metadata = OpenMeteoIfs9PrecisionMetadata(**_precision_metadata(city_name, target.isoformat(),
        anchor_sigma_c=3., raw_payload_bytes=raw, analysis_at=cut))
    return metadata, evaluate_openmeteo_ecmwf_ifs9_precision_guard(metadata, raw_payload_bytes=raw,
        decision_at=cut, station_ground_evidence=evidence)


@pytest.mark.parametrize("city_name", sorted(BOUND))
def test_bound_station_seed_passes_the_real_guard_end_to_end(tmp_path, monkeypatch, city_name):
    db, clock, evidence, raw, target = _seed_setup(tmp_path, monkeypatch, city_name)
    cut = clock[0] + timedelta(minutes=1)
    metadata, result = _seed_guard(city_name, raw, target, cut, evidence)
    assert result.status == "PASS", result.reason_codes
    assert result.reason_codes == ("OM9_PRECISION_METADATA_PASS",) and result.elevation_delta_m is None
    proof = metadata.source_geometry_proof["station_ground_proof"]
    assert metadata.station_elevation_m is None
    assert proof["status"] == "BOUNDED" and proof["reason"] == "STATION_GROUND_POINT_UNPROVEN"
    assert proof["facts"] == evidence["facts"] and proof["audit"]["body_sha256"] == evidence["body_sha256"]
    assert evidence["revision"] == ground.MANIFEST_KIND
    assert set(evidence["input_bodies"]) == {"ground", "identity_bridge"}
    coverage = ground.station_ground_target_coverage(evidence, decision_at=cut,
        target_start_utc=metadata.local_day_start_utc, target_end_utc=metadata.local_day_end_utc)
    assert coverage["ground_status"] == "BOUNDED" and coverage["status"] == "VERIFIED"
    # A forged point height on a BOUNDED proof is an identity mismatch, not a pass.
    from dataclasses import replace
    forged = evaluate_openmeteo_ecmwf_ifs9_precision_guard(replace(metadata, station_elevation_m=5.0),
        raw_payload_bytes=raw, decision_at=cut, station_ground_evidence=evidence)
    assert forged.status == "BLOCK" and "OM9_STATION_SOURCE_IDENTITY_MISMATCH" in forged.reason_codes
    # Not possessed at the cut: blocked exactly as today.
    early = clock[0] - timedelta(minutes=1)
    assert evaluate_openmeteo_ecmwf_ifs9_precision_guard(metadata, raw_payload_bytes=raw,
        decision_at=early, station_ground_evidence=evidence).status == "BLOCK"


@pytest.mark.parametrize("tamper", ("wrh_body", "awc_body"))
def test_unreadable_archived_bound_body_blocks_unproven(tmp_path, monkeypatch, tamper):
    db, clock, evidence, raw, target = _seed_setup(tmp_path, monkeypatch, "Qingdao")
    role = "ground" if tamper == "wrh_body" else "identity_bridge"
    path = Path(evidence["input_bodies"][role]["artifact_path"])
    path.write_bytes(path.read_bytes().replace(b"ZSQD", b"ZSQE", 1))
    cut = clock[0] + timedelta(minutes=1)
    assert ground.read_current_station_ground_evidence(db, city="Qingdao", decision_at=cut) is None
    _, result = _seed_guard("Qingdao", raw, target, cut, evidence)
    assert result.status == "BLOCK" and "OM9_STATION_GROUND_PROOF_UNPROVEN" in result.reason_codes


def test_mismatched_config_body_leaves_the_station_unproven_before_any_request(tmp_path, monkeypatch):
    from scripts.download_replacement_forecast_current_targets import _station_ground_prerequisite_reason

    def mutate(root, rows):
        (root / "synoptic_wrh_zsqd_station.json").write_bytes(b'{"SUMMARY":{}}')
    _private_config(tmp_path, monkeypatch, ("Qingdao",), mutate)
    reason = _station_ground_prerequisite_reason(config.cities_by_name["Qingdao"])
    assert reason == "OM9_STATION_GROUND_PROOF_UNPROVEN:STATION_GROUND_PROOF_INVALID"


@pytest.mark.parametrize("city_name", ("Singapore", "Chicago"))
def test_verified_station_guard_and_dependency_shape_are_unchanged(tmp_path, monkeypatch, city_name):
    db, clock, evidence, raw, target = _seed_setup(tmp_path, monkeypatch, city_name)
    cut = clock[0] + timedelta(minutes=1)
    metadata, result = _seed_guard(city_name, raw, target, cut, evidence)
    height = evidence["facts"]["elevation_m"]
    assert result.status == "PASS" and result.reason_codes == ("OM9_PRECISION_METADATA_PASS",)
    assert metadata.station_elevation_m == height
    assert result.elevation_delta_m == metadata.grid_elevation_m - height
    proof = metadata.source_geometry_proof["station_ground_proof"]
    assert proof["status"] == "VERIFIED" and proof["reason"] is None
    assert config.station_ground_status(evidence["facts"]) == "VERIFIED"
    coverage = ground.station_ground_target_coverage(evidence, decision_at=cut,
        target_start_utc=metadata.local_day_start_utc, target_end_utc=metadata.local_day_end_utc)
    assert set(coverage) == COVERAGE_KEYS and coverage["status"] == "VERIFIED"
    assert coverage["applicability_identity"] == hashlib.sha256(ground._encoded(
        {key: value for key, value in coverage.items() if key != "applicability_identity"})).hexdigest()
