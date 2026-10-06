# Created: 2026-10-01
# Last audited: 2026-10-06
# Lifecycle: created=2026-10-01; last_reviewed=2026-10-06; last_reused=2026-10-06
# Purpose: Synoptic WRH station ground is VERIFIED only with an agreeing independent NOAA site.
# Reuse: pytest tests/test_synoptic_wrh_station_ground.py
# Authority basis: operator mandate 2026-10-01 (no station-ground data gaps, guard unchanged);
#   AGENTS §2 INV-47; config/AGENTS.md station_ground_proof.
from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.config as config
from src.data import station_ground_evidence as ground
from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema

UTC = timezone.utc
KIND = config.SYNOPTIC_WRH_SOURCE_KIND
CITIES = {"Tel Aviv": "LLBG", "Beijing": "ZBAA", "Wuhan": "ZHHH",
          "Taipei": "RCSS", "Moscow": "UUWW", "Denver": "KBKF"}


def _claim(city):
    rows = json.loads((config.PROJECT_ROOT / "config/station_precise_coords.json").read_text())
    return rows, rows[city]["station_ground_proof"]


def _bodies(station):
    primary = (config.PROJECT_ROOT / config.station_ground_source_artifact_ref(source_kind=KIND, station_id=station)).read_bytes()
    bridge = (config.PROJECT_ROOT / config.station_ground_identity_bridge_artifact_ref(source_kind=KIND, station_id=station)).read_bytes()
    return primary, bridge


def _facts(station, primary, bridge):
    return config.station_ground_facts_from_bytes(source_kind=KIND, station_id=station,
        raw_body=primary, identity_bridge_bytes=bridge)


@pytest.mark.parametrize("city,station", sorted(CITIES.items()))
def test_live_captured_documents_parse_and_bind_verified(city, station):
    """The six retained original bodies replay to the registry's own facts."""
    primary, bridge = _bodies(station)
    rows, claim = _claim(city)
    assert hashlib.sha256(primary).hexdigest() == claim["body_sha256"]
    assert hashlib.sha256(bridge).hexdigest() == claim["bridge"]["body_sha256"]
    facts = _facts(station, primary, bridge)
    assert facts is not None and facts["source_kind"] == KIND and facts["station_id"] == station
    assert all(claim[key] == value for key, value in facts.items())
    # Whole feet is the published quantity; metres are its exact conversion.
    from decimal import Decimal
    assert facts["elevation_m"] == float(Decimal(json.loads(primary)["STATION"][0]["ELEVATION"]) * Decimal("0.3048"))
    geometry = config.runtime_station_geometry_for_city(config.cities_by_name[city])
    assert geometry["ground_status"] == "VERIFIED", geometry["ground_reason"]
    assert geometry["ground_elevation_m"] == facts["elevation_m"]
    assert geometry["ground_audit"]["bridge"]["artifact_ref"] == claim["bridge"]["artifact_ref"]
    # The rotating page token is never retained in any recorded identity.
    assert "token" not in json.dumps(claim).lower() and b"token" not in primary.lower()


@pytest.mark.parametrize("city", ("Qingdao", "Panama City"))
def test_stale_or_unconfirmed_sites_never_gain_point_ground(city):
    # Their WRH record is only one end of a BOUNDED height hull
    # (tests/test_om9_bounded_station_ground.py), never VERIFIED ground.
    station = config.cities_by_name[city].wu_station
    assert config.station_ground_source_artifact_ref(source_kind=KIND, station_id=station) is None
    geometry = config.runtime_station_geometry_for_city(config.cities_by_name[city])
    assert geometry["ground_status"] != "VERIFIED" and geometry["ground_elevation_m"] is None


def _mutate_primary(primary, mutation):
    payload = json.loads(primary)
    station = payload["STATION"][0]
    if mutation == "two_stations":
        payload["STATION"].append(dict(station))
    elif mutation == "foreign_stid":
        station["STID"] = "KORD"
    elif mutation == "inactive":
        station["STATUS"] = "INACTIVE"
    elif mutation == "metres_unit":
        station["UNITS"]["elevation"] = "m"
    elif mutation == "fractional_feet":
        station["ELEVATION"] = str(float(station["ELEVATION"]) + 0.4)
    elif mutation == "nonfinite_feet":
        station["ELEVATION"] = "NaN"
    elif mutation == "numeric_coordinate":
        station["LATITUDE"] = float(station["LATITUDE"])
    elif mutation == "refused":
        payload["SUMMARY"]["RESPONSE_CODE"] = 2
    elif mutation == "not_json":
        return b"<html>"
    return json.dumps(payload).encode()


@pytest.mark.parametrize("mutation", ("two_stations", "foreign_stid", "inactive", "metres_unit",
    "fractional_feet", "nonfinite_feet", "numeric_coordinate", "refused", "not_json"))
@pytest.mark.parametrize("station", ("LLBG", "KBKF"))
def test_primary_rejections(station, mutation):
    primary, bridge = _bodies(station)
    assert _facts(station, primary, bridge) is not None
    assert _facts(station, _mutate_primary(primary, mutation), bridge) is None


def _isd_rows(bridge):
    return list(csv.reader(io.StringIO(bridge.decode())))


def _isd_body(rows):
    out = io.StringIO()
    csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator="\n").writerows(rows)
    return out.getvalue().encode()


@pytest.mark.parametrize("mutation", ("foreign_station", "moved_row", "late_row", "header", "empty",
    "height_disagrees", "lat_disagrees", "lon_disagrees", "malformed"))
def test_isd_bridge_rejections_and_disagreement(mutation):
    primary, bridge = _bodies("LLBG")
    rows = _isd_rows(bridge)
    if mutation == "foreign_station":
        rows[1][0] = "54511099999"
    elif mutation == "moved_row":
        rows[-1][2] = "32.111389"
    elif mutation == "late_row":
        rows[1][5] = "2025-08-24T02:00:00"
    elif mutation == "header":
        rows[0][4] = "ELEV"
    elif mutation == "empty":
        rows = rows[:1]
    elif mutation == "height_disagrees":
        for row in rows[1:]:
            row[4] = "42.14"
    elif mutation == "lat_disagrees":
        for row in rows[1:]:
            row[2] = "32.021389"
    elif mutation == "lon_disagrees":
        for row in rows[1:]:
            row[3] = "34.896667"
    body = b'"STATION"\n"x,"y' if mutation == "malformed" else _isd_body(rows)
    assert _facts("LLBG", primary, bridge) is not None
    assert _facts("LLBG", primary, body) is None


@pytest.mark.parametrize("mutation", ("foreign_id", "height_disagrees", "feet_unit", "lat_disagrees", "polygon"))
def test_nws_bridge_rejections_and_disagreement(mutation):
    primary, bridge = _bodies("KBKF")
    feature = json.loads(bridge)
    if mutation == "foreign_id":
        feature["properties"]["stationIdentifier"] = "KDEN"
    elif mutation == "height_disagrees":
        feature["properties"]["elevation"]["value"] = 1726.1
    elif mutation == "feet_unit":
        feature["properties"]["elevation"]["unitCode"] = "wmoUnit:ft"
    elif mutation == "lat_disagrees":
        feature["geometry"]["coordinates"][1] = 39.7233
    elif mutation == "polygon":
        feature["geometry"]["type"] = "Polygon"
    assert _facts("KBKF", primary, bridge) is not None
    assert _facts("KBKF", primary, json.dumps(feature).encode()) is None


def test_bridge_is_required_and_must_be_the_approved_entity():
    primary, bridge = _bodies("LLBG")
    assert config.station_ground_facts_from_bytes(source_kind=KIND, station_id="LLBG", raw_body=primary) is None
    _, other = _bodies("ZBAA")
    assert _facts("LLBG", primary, other) is None  # another station's NOAA site never bridges


def _private_registry(tmp_path, monkeypatch, city, mutate=None):
    rows, claim = _claim(city)
    for ref in (claim["artifact_ref"], claim["bridge"]["artifact_ref"]):
        (tmp_path / Path(ref).name).write_bytes((config.PROJECT_ROOT / ref).read_bytes())
    if mutate is not None:
        mutate(tmp_path, claim)
    (tmp_path / "station_precise_coords.json").write_text(json.dumps(rows))
    (tmp_path / "cities.json").write_bytes((config.PROJECT_ROOT / "config/cities.json").read_bytes())
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    return rows, claim


@pytest.mark.parametrize("mutation", ("bridge_disagrees", "bridge_kind", "bridge_url", "bridge_hash",
    "missing_bridge", "possession_not_max", "source_hash"))
def test_registry_claim_without_its_agreeing_bridge_is_unproven(tmp_path, monkeypatch, mutation):
    def mutate(root, claim):
        bridge_path = root / Path(claim["bridge"]["artifact_ref"]).name
        if mutation == "bridge_disagrees":
            rows = _isd_rows(bridge_path.read_bytes())
            for row in rows[1:]:
                row[4] = "52.14"
            bridge_path.write_bytes(_isd_body(rows))
            claim["bridge"]["body_sha256"] = hashlib.sha256(bridge_path.read_bytes()).hexdigest()
        elif mutation == "bridge_kind":
            claim["bridge"]["source_kind"] = "awc_stationinfo_v1"
        elif mutation == "bridge_url":
            claim["bridge"]["source_url"] = config.AWC_STATION_IDENTITY_SOURCE_URL
        elif mutation == "bridge_hash":
            claim["bridge"]["body_sha256"] = "0" * 64
        elif mutation == "missing_bridge":
            del claim["bridge"]
        elif mutation == "possession_not_max":
            claim["checked_at"] = claim["bridge"]["checked_at"]
            claim["source_checked_at"] = (datetime.fromisoformat(claim["checked_at"]) + timedelta(seconds=1)).isoformat()
        elif mutation == "source_hash":
            claim["body_sha256"] = "0" * 64
    _private_registry(tmp_path, monkeypatch, "Tel Aviv")
    assert config.runtime_station_geometry_for_city(config.cities_by_name["Tel Aviv"])["ground_status"] == "VERIFIED"
    _private_registry(tmp_path, monkeypatch, "Tel Aviv", mutate)
    assert config.runtime_station_geometry_for_city(config.cities_by_name["Tel Aviv"])["ground_status"] == "UNPROVEN"


def _archive_setup(tmp_path, monkeypatch, city):
    _private_registry(tmp_path, monkeypatch, city)
    monkeypatch.setattr(ground, "_store_root", lambda: tmp_path / "state" / "station_ground")
    clock = [datetime(2026, 10, 2, 0, 30, tzinfo=UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz or UTC)

    monkeypatch.setattr(ground, "datetime", Clock)
    db = tmp_path / "zeus-forecasts.db"
    with sqlite3.connect(db) as conn:
        ensure_replacement_forecast_live_schema(conn)
    return db, clock


@pytest.mark.parametrize("city", ("Tel Aviv", "Denver"))
def test_normal_archive_binds_both_whole_bodies_at_actual_possession(tmp_path, monkeypatch, city):
    db, clock = _archive_setup(tmp_path, monkeypatch, city)
    _, claim = _claim(city)
    report = ground.archive_station_ground_evidence(db, [city])
    assert report["status"] == "GROUND_SOURCE_ARCHIVED", report
    evidence = report["archived"][city]
    assert evidence["revision"] == ground.MANIFEST_KIND and evidence["source_kind"] == KIND
    assert set(evidence["input_bodies"]) == {"ground", "identity_bridge"}
    assert evidence["input_bodies"]["identity_bridge"]["request_url"] == claim["bridge"]["source_url"]
    primary, bridge = _bodies(CITIES[city])
    assert Path(evidence["input_bodies"]["ground"]["artifact_path"]).read_bytes() == primary
    assert Path(evidence["input_bodies"]["identity_bridge"]["artifact_path"]).read_bytes() == bridge
    assert ground.read_current_station_ground_evidence(db, city=city, decision_at=clock[0] - timedelta(microseconds=1)) is None
    assert ground.read_current_station_ground_evidence(db, city=city, decision_at=clock[0]) == evidence
    clock[0] += timedelta(hours=1)
    assert ground.archive_station_ground_evidence(db, [city])["archived"][city] == evidence


def test_archived_bridge_body_tamper_is_not_evidence(tmp_path, monkeypatch):
    db, clock = _archive_setup(tmp_path, monkeypatch, "Tel Aviv")
    evidence = ground.archive_station_ground_evidence(db, ["Tel Aviv"])["archived"]["Tel Aviv"]
    path = Path(evidence["input_bodies"]["identity_bridge"]["artifact_path"])
    path.write_bytes(path.read_bytes().replace(b"41.14", b"51.14"))
    assert ground.read_current_station_ground_evidence(db, city="Tel Aviv", decision_at=clock[0]) is None


def test_om9_precision_guard_passes_a_synoptic_ground_seed_end_to_end(tmp_path, monkeypatch):
    """Real archive, producer precision metadata and guard; controlled O1280 surface only."""
    from tests.test_openmeteo_ecmwf_ifs9_bucket_transport import _actual_o1280_static_fixture
    from src.data.openmeteo_ecmwf_ifs9_precision_guard import (
        OpenMeteoIfs9PrecisionMetadata, evaluate_openmeteo_ecmwf_ifs9_precision_guard)
    from scripts.download_replacement_forecast_current_targets import _precision_metadata
    from zoneinfo import ZoneInfo

    city_name = "Tel Aviv"
    db, clock = _archive_setup(tmp_path, monkeypatch, city_name)
    evidence = ground.archive_station_ground_evidence(db, [city_name])["archived"][city_name]
    transport, path, _, _, surface_clock, _ = _actual_o1280_static_fixture(tmp_path, monkeypatch)
    surface_clock[0] = clock[0]
    monkeypatch.setattr(transport, "HSURF_LOCAL_CACHE", str(path))
    monkeypatch.setattr(config, "state_path", lambda filename: tmp_path / "state" / filename)
    city = config.runtime_cities_by_name()[city_name]
    points, _, center = transport.om_get_surrounding_gridpoints(city.lat, city.lon)
    selected = transport.om_get_coordinates(points[center])
    height = float(evidence["facts"]["elevation_m"])
    zone = ZoneInfo(city.timezone)
    target = (clock[0].astimezone(zone) + timedelta(days=1)).date()
    payload = {"latitude": selected.grid_latitude, "longitude": selected.grid_longitude_east, "elevation": height,
        "timezone": city.timezone, "utc_offset_seconds": int(clock[0].astimezone(zone).utcoffset().total_seconds()),
        "hourly_units": {"temperature_2m": "°C"},
        "hourly": {"time": [f"{target.isoformat()}T{hour:02d}:00" for hour in range(24)], "temperature_2m": [24.] * 24}}
    raw = (json.dumps(payload, sort_keys=True) + "\n").encode()
    cut = clock[0] + timedelta(minutes=1)
    metadata = OpenMeteoIfs9PrecisionMetadata(**_precision_metadata(city_name, target.isoformat(),
        anchor_sigma_c=3., raw_payload_bytes=raw, analysis_at=cut))
    assert metadata.station_elevation_m == height
    guard = evaluate_openmeteo_ecmwf_ifs9_precision_guard(metadata, raw_payload_bytes=raw,
        decision_at=cut, station_ground_evidence=evidence)
    assert guard.status == "PASS", guard.reason_codes
    # The same seed without possessed ground evidence at the cut stays blocked.
    early = clock[0] - timedelta(minutes=1)
    blocked = evaluate_openmeteo_ecmwf_ifs9_precision_guard(metadata, raw_payload_bytes=raw,
        decision_at=early, station_ground_evidence=evidence)
    assert blocked.status != "PASS"
