# Created: 2026-06-06
# Last reused/audited: 2026-10-02
# Lifecycle: created=2026-06-06; last_reviewed=2026-10-02; last_reused=2026-10-02
# Purpose: Protect replacement source_run/source_run_coverage identity from cross-product lineage drift.
# Reuse: Run before writing or reading replacement source_run dependencies, readiness rows, or replay provenance.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + Bayes fusion live integration.
"""Replacement source-run identity validator tests."""

from __future__ import annotations

from hashlib import sha256
import copy
import json

import pytest

from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION_UNCERTIFIED,
    coordinate_bound_data_version,
)
from src.data.replacement_forecast_source_run_identity import (
    expected_replacement_dependency_identity_by_role,
    validate_replacement_source_run_identity,
)


_CURRENT_MANIFEST_JSON = '{"coordinate_profile":"test-current"}'


@pytest.fixture(autouse=True)
def _current_coordinate_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.config as config
    from src.data import ecmwf_open_data as native

    # Import the collector before the protocol-only profile override. Its
    # bound alias must not retain this test's temporary profile afterward.
    assert native.runtime_coordinate_manifest_json is config.runtime_coordinate_manifest_json

    monkeypatch.setattr(
        config,
        "runtime_coordinate_manifest_json",
        lambda: _CURRENT_MANIFEST_JSON,
        raising=False,
    )


def _source_run(role: str, metric: str = "high", **overrides):
    expected = expected_replacement_dependency_identity_by_role(metric)[role]
    row = {
        "source_run_id": f"run-{role}",
        "source_id": expected.source_id,
        "dataset_id": expected.data_version,
        "temperature_metric": metric,
        "physical_quantity": expected.physical_quantity,
        "observation_field": expected.observation_field,
        "expected_members": expected.expected_members,
        "observed_members": expected.expected_members,
    }
    row.update(overrides)
    return row


def _coverage(role: str, metric: str = "high", **overrides):
    expected = expected_replacement_dependency_identity_by_role(metric)[role]
    row = {
        "source_run_id": f"run-{role}",
        "source_id": expected.source_id,
        "data_version": expected.data_version,
        "temperature_metric": metric,
        "physical_quantity": expected.physical_quantity,
        "observation_field": expected.observation_field,
    }
    row.update(overrides)
    return row


def test_expected_dependency_identity_map_separates_raw_anchor_and_derived_products() -> None:
    high = expected_replacement_dependency_identity_by_role("high")
    low = expected_replacement_dependency_identity_by_role("low")

    assert set(high) == {"baseline_b0", "openmeteo_ifs9_anchor", "soft_anchor_posterior"}
    assert high["openmeteo_ifs9_anchor"].raw_ensemble_eligible is False
    assert high["soft_anchor_posterior"].raw_ensemble_eligible is False
    assert high["soft_anchor_posterior"].source_id == "openmeteo_ecmwf_ifs9_bayes_fusion"
    assert high["soft_anchor_posterior"].data_version.endswith("_high_v1")
    assert low["soft_anchor_posterior"].data_version.endswith("_low_v1")


@pytest.mark.parametrize("metric", ("high", "low"))
def test_native_certificate_same_snapshot_requires_full_target_extrema(metric):
    import sqlite3
    from src.data.replacement_forecast_source_run_identity import native_coordinate_certificate_reason
    from src.data.forecast_extrema_authority import REMAINING_WINDOW_ATTRIBUTION_STATUS

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE source_run (source_run_id TEXT, manifest_hash TEXT)")
    conn.execute("INSERT INTO source_run VALUES ('run', 'current')")
    conn.execute("""CREATE TABLE ensemble_snapshots (snapshot_id INTEGER PRIMARY KEY,
        city TEXT,target_date TEXT,temperature_metric TEXT,dataset_id TEXT,provenance_json TEXT,
        source_cycle_time TEXT,source_available_at TEXT,source_run_id TEXT,
        causality_status TEXT,boundary_ambiguous INTEGER,forecast_window_attribution_status TEXT,
        contributes_to_target_extrema INTEGER)""")
    version = expected_replacement_dependency_identity_by_role(metric)["baseline_b0"].data_version
    conn.execute("INSERT INTO ensemble_snapshots VALUES (17,'Hong Kong','2026-10-02',? ,?,'{}',"
        "'2026-10-02T06:00:00Z','2026-10-02T14:00:00Z','run','OK',0,?,0)",
        (metric, version, REMAINING_WINDOW_ATTRIBUTION_STATUS))
    kwargs = dict(shape={"snapshot_id": 17}, city="Hong Kong", target_date="2026-10-02", metric=metric)
    assert native_coordinate_certificate_reason(conn, **kwargs) == "REPLACEMENT_CURRENT_EVIDENCE_EXTREMA_WINDOW_INVALID"
    for status, contributes in (("FULLY_INSIDE_TARGET_LOCAL_DAY", 1), ("INTERVAL_CENSORED_TARGET_LOCAL_DAY", 0)):
        conn.execute("UPDATE ensemble_snapshots SET forecast_window_attribution_status=?,contributes_to_target_extrema=?",
            (status, contributes))
        assert native_coordinate_certificate_reason(conn, **kwargs) is None
    conn.execute("UPDATE ensemble_snapshots SET causality_status='UNKNOWN'")
    assert native_coordinate_certificate_reason(conn, **kwargs) == "REPLACEMENT_CURRENT_EVIDENCE_EXTREMA_WINDOW_INVALID"
    assert native_coordinate_certificate_reason(conn, **{**kwargs, "metric": "low" if metric == "high" else "high"}) == "REPLACEMENT_CURRENT_COORDINATE_IDENTITY_MISMATCH"
    conn.close()


@pytest.mark.parametrize(
    ("metric", "base"),
    (("high", ECMWF_OPENDATA_HIGH_DATA_VERSION), ("low", ECMWF_OPENDATA_LOW_DATA_VERSION)),
)
def test_baseline_identity_is_bound_to_the_current_coordinate_manifest(
    metric: str,
    base: str,
) -> None:
    expected = expected_replacement_dependency_identity_by_role(metric)["baseline_b0"]

    assert expected.data_version == coordinate_bound_data_version(
        base, sha256(_CURRENT_MANIFEST_JSON.encode("utf-8")).hexdigest()
    )


def test_baseline_identity_rejects_a_prior_coordinate_manifest() -> None:
    old_data_version = coordinate_bound_data_version(
        ECMWF_OPENDATA_HIGH_DATA_VERSION, "a" * 64
    )
    decision = validate_replacement_source_run_identity(
        role="baseline_b0",
        temperature_metric="high",
        source_run=_source_run("baseline_b0", dataset_id=old_data_version),
    )

    assert decision.valid is False
    assert "REPLACEMENT_SOURCE_RUN_DATA_VERSION_MISMATCH" in decision.reason_codes


@pytest.mark.parametrize("metric", ("high", "low"))
def test_owned_legacy_manifest_same_city_inputs_preserve_actual_source_identity(tmp_path, monkeypatch, metric):
    """Protocol proof only; actual native/public authority is tested separately."""
    import src.config as config
    from src.data.ecmwf_open_data import _write_runtime_coordinate_manifest
    from src.data.replacement_forecast_source_run_identity import native_coordinate_manifest_compatibility

    current = {"coordinate_basis": "runtime_settlement_station", "cities": [{
        "city": "Hong Kong", "lat": 22.3022, "lon": 114.1742, "timezone": "Asia/Hong_Kong", "unit": "C",
        "station_geometry": {"station_id": "HKO_HQ", "lat": 22.3022, "lon": 114.1742, "validity_reason": None}}]}
    original = copy.deepcopy(current)
    original["cities"][0]["station_geometry"].update(elevation_m=32.0, station_surface="land")
    encoded = json.dumps(original, sort_keys=True, separators=(",", ":"))
    owned = _write_runtime_coordinate_manifest(tmp_path, manifest_json=encoded)
    monkeypatch.setenv("ZEUS_51_SOURCE_ROOT", str(tmp_path))
    monkeypatch.setattr(config, "runtime_coordinate_manifest_json", lambda: json.dumps(current, sort_keys=True, separators=(",", ":")))
    base = ECMWF_OPENDATA_HIGH_DATA_VERSION if metric == "high" else ECMWF_OPENDATA_LOW_DATA_VERSION
    version = coordinate_bound_data_version(base, sha256(encoded.encode()).hexdigest())
    source = _source_run("baseline_b0", metric, dataset_id=version, manifest_hash=sha256(encoded.encode()).hexdigest())
    coverage = _coverage("baseline_b0", metric, city="Hong Kong", data_version=version)
    before = copy.deepcopy((source, coverage, owned.read_bytes()))
    proof = native_coordinate_manifest_compatibility("Hong Kong", metric, version)
    assert proof is not None and proof["source_manifest_sha256"] == sha256(encoded.encode()).hexdigest()
    decision = validate_replacement_source_run_identity(role="baseline_b0", temperature_metric=metric,
        source_run=source, coverage=coverage)
    assert decision.valid, decision.reason_codes
    assert (source, coverage, owned.read_bytes()) == before


@pytest.mark.parametrize("fault", ("missing", "tampered", "symlink"))
def test_owned_coordinate_manifest_cannot_be_replaced_by_a_cached_path(tmp_path, monkeypatch, fault):
    import src.config as config
    from src.data.ecmwf_open_data import _write_runtime_coordinate_manifest
    from src.data.replacement_forecast_source_run_identity import native_coordinate_manifest_compatibility
    current = {"coordinate_basis": "runtime_settlement_station", "cities": [{
        "city": "Hong Kong", "lat": 22.3022, "lon": 114.1742, "timezone": "Asia/Hong_Kong", "unit": "C",
        "station_geometry": {"station_id": "HKO_HQ", "lat": 22.3022, "lon": 114.1742, "validity_reason": None}}]}
    original = copy.deepcopy(current)
    original["cities"][0]["station_geometry"].update(elevation_m=32.0, station_surface="land")
    raw = json.dumps(original, sort_keys=True, separators=(",", ":"))
    owned = _write_runtime_coordinate_manifest(tmp_path, manifest_json=raw)
    version = coordinate_bound_data_version(ECMWF_OPENDATA_HIGH_DATA_VERSION, sha256(raw.encode()).hexdigest())
    monkeypatch.setenv("ZEUS_51_SOURCE_ROOT", str(tmp_path))
    monkeypatch.setattr(config, "runtime_coordinate_manifest_json", lambda: json.dumps(current))
    cache = {}
    assert native_coordinate_manifest_compatibility("Hong Kong", "high", version, manifest_cache=cache) is not None
    if fault == "tampered":
        owned.write_text(raw + " ")
    else:
        owned.unlink()
        if fault == "symlink":
            other = tmp_path / "foreign-manifest.json"
            other.write_text(raw)
            owned.symlink_to(other)
    assert native_coordinate_manifest_compatibility("Hong Kong", "high", version, manifest_cache=cache) is None


def test_coordinate_compatibility_is_local_to_the_unchanged_city(tmp_path, monkeypatch):
    import src.config as config
    from src.data.ecmwf_open_data import _write_runtime_coordinate_manifest
    from src.data.replacement_forecast_source_run_identity import native_coordinate_manifest_compatibility
    hong_kong = {"city": "Hong Kong", "lat": 22.3022, "lon": 114.1742, "timezone": "Asia/Hong_Kong", "unit": "C",
        "station_geometry": {"station_id": "HKO_HQ", "lat": 22.3022, "lon": 114.1742, "validity_reason": None}}
    paris = {"city": "Paris", "lat": 48.9694, "lon": 2.4414, "timezone": "Europe/Paris", "unit": "C",
        "station_geometry": {"station_id": "LFPB", "lat": 48.9694, "lon": 2.4414, "validity_reason": None}}
    current = {"coordinate_basis": "runtime_settlement_station", "cities": [hong_kong, paris]}
    original = copy.deepcopy(current)
    for row in original["cities"]:
        row["station_geometry"].update(elevation_m=None, station_surface="land")
    raw = json.dumps(original, sort_keys=True, separators=(",", ":"))
    _write_runtime_coordinate_manifest(tmp_path, manifest_json=raw)
    version = coordinate_bound_data_version(ECMWF_OPENDATA_HIGH_DATA_VERSION, sha256(raw.encode()).hexdigest())
    monkeypatch.setenv("ZEUS_51_SOURCE_ROOT", str(tmp_path))
    monkeypatch.setattr(config, "runtime_coordinate_manifest_json", lambda: json.dumps(current))
    before = native_coordinate_manifest_compatibility("Hong Kong", "high", version)
    current["cities"][1]["lat"] += .001
    after = native_coordinate_manifest_compatibility("Hong Kong", "high", version)
    assert before is not None and after is not None
    assert before["city_inputs_sha256"] == after["city_inputs_sha256"]
    assert before["current_manifest_sha256"] != after["current_manifest_sha256"]
    assert native_coordinate_manifest_compatibility("Paris", "high", version) is None


@pytest.mark.parametrize("axis", ("lat", "lon", "timezone", "unit", "station_id", "station_lat", "extra", "surface", "height"))
def test_owned_coordinate_compatibility_rejects_changed_scope_and_unknown_fields(tmp_path, monkeypatch, axis):
    import src.config as config
    from src.data.ecmwf_open_data import _write_runtime_coordinate_manifest
    from src.data.replacement_forecast_source_run_identity import native_coordinate_manifest_compatibility
    current = {"coordinate_basis": "runtime_settlement_station", "cities": [{
        "city": "Hong Kong", "lat": 22.3022, "lon": 114.1742, "timezone": "Asia/Hong_Kong", "unit": "C",
        "station_geometry": {"station_id": "HKO_HQ", "lat": 22.3022, "lon": 114.1742, "validity_reason": None}}]}
    old = copy.deepcopy(current)
    old["cities"][0]["station_geometry"].update(elevation_m=32.0, station_surface="land")
    if axis == "extra": old["cities"][0]["station_geometry"]["unknown_retired_field"] = 0
    elif axis == "surface": old["cities"][0]["station_geometry"]["station_surface"] = "sea"
    elif axis == "height": old["cities"][0]["station_geometry"]["elevation_m"] = True
    elif axis == "station_id": current["cities"][0]["station_geometry"]["station_id"] = "OTHER"
    elif axis == "station_lat": current["cities"][0]["station_geometry"]["lat"] += .001
    elif axis in ("lat", "lon"): current["cities"][0][axis] += .001
    else: current["cities"][0][axis] = "F" if axis == "unit" else "UTC"
    encoded = json.dumps(old, sort_keys=True, separators=(",", ":"))
    _write_runtime_coordinate_manifest(tmp_path, manifest_json=encoded)
    monkeypatch.setenv("ZEUS_51_SOURCE_ROOT", str(tmp_path))
    monkeypatch.setattr(config, "runtime_coordinate_manifest_json", lambda: json.dumps(current))
    version = coordinate_bound_data_version(ECMWF_OPENDATA_HIGH_DATA_VERSION, sha256(encoded.encode()).hexdigest())
    assert native_coordinate_manifest_compatibility("Hong Kong", "high", version) is None


def test_native_old_manifest_normal_certificate_rebuilds_without_relabeling_sources(tmp_path, monkeypatch):
    """Normal native/HTTP writers; controlled input, not a live GRIB capture."""
    import sqlite3
    import inspect
    import subprocess
    from pathlib import Path
    from dataclasses import asdict, replace
    from datetime import date, datetime, timedelta
    import src.config as config
    from src.contracts import ensemble_snapshot_provenance as provenance_contract
    from src.data import replacement_forecast_materializer as materializer
    from src.data import replacement_forecast_live_materialization_queue as queue
    from src.data.replacement_forecast_materialization_seed_builder import latest_baseline_coverage_for_replacement_seed
    from src.data.replacement_forecast_current_target_plan import build_replacement_forecast_current_target_plan
    from src.data.replacement_forecast_seed_discovery import discover_replacement_forecast_materialization_seeds
    from src.data.raw_forecast_artifact_manifest import RawForecastArtifactManifest, write_manifest
    from src.data.replacement_forecast_materialization_request_builder import build_materialize_request_dataclass
    from src.state.db_writer_lock import db_writer_lock, WriteClass
    from scripts import materialize_replacement_forecast_live as cli
    from tests.integration import test_w3_solve_seam_g3 as normal
    from tests import test_replacement_forecast_materializer as raw_inputs

    static = normal._noaa_native_sources.__wrapped__(tmp_path, monkeypatch)
    next(static)
    fixture = None
    try:
        city = config.runtime_cities_by_name()["Chicago"]
        station = config.runtime_station_geometry_for_city(city)
        current = {"coordinate_basis": "runtime_settlement_station", "cities": [{
            "city": city.name, "lat": city.lat, "lon": city.lon, "timezone": city.timezone, "unit": city.settlement_unit,
            "station_geometry": {key: station[key] for key in ("station_id", "lat", "lon", "validity_reason")}}]}
        original = copy.deepcopy(current)
        old_geometry = original["cities"][0]["station_geometry"]
        old_geometry.update(elevation_m=station["ground_elevation_m"], station_surface="land")
        original_text = json.dumps(original, sort_keys=True, separators=(",", ":"))
        current_text = json.dumps(current, sort_keys=True, separators=(",", ":"))
        profile = [original_text]
        monkeypatch.setattr(config, "runtime_coordinate_manifest_json", lambda: profile[0])
        from src.data import ecmwf_open_data as native
        monkeypatch.setattr(native, "runtime_coordinate_manifest_json", lambda: profile[0])
        # skip_extract supplies a controlled extraction, so persist its real
        # original manifest with the ordinary owner API before ingest.
        native._write_runtime_coordinate_manifest(tmp_path / "native-ens", manifest_json=original_text)
        monkeypatch.setenv("ZEUS_51_SOURCE_ROOT", str(tmp_path / "native-ens"))
        ordinary_input = raw_inputs._fixture_ens_surface_provenance
        original_hash = provenance_contract.grid_surface_evidence_identity_hash

        def extracted_input(**kwargs):
            body = json.loads(ordinary_input(**kwargs))
            # Original extractor copied precisely its manifest's station fields.
            body["grid_surface_evidence"]["station_geometry"] = dict(old_geometry)
            return json.dumps(body)

        with monkeypatch.context() as old_producer:
            old_producer.setattr(raw_inputs, "_fixture_ens_surface_provenance", extracted_input)
            old_producer.setattr(provenance_contract, "grid_surface_evidence_identity_hash",
                lambda proof, **kwargs: original_hash(proof, legacy_station_schema=True))
            # The small public fixture omits transport lineage. Supply the
            # normal producer's actual request run ID before its original
            # manifest INSERT, never by changing a sealed artifact afterward.
            anchor_inputs = dict(vars(raw_inputs))
            anchor_source = inspect.getsource(raw_inputs._hko_request_with_owned_anchor).replace(
                'product_metadata={"city":city.name,"target_date":request.target_date.isoformat()}',
                'product_metadata={"city":city.name,"target_date":request.target_date.isoformat(),'
                '"source_run_id":request.openmeteo_source_run_id}')
            exec(compile(anchor_source, raw_inputs.__file__, "exec"), anchor_inputs)
            old_producer.setattr(raw_inputs, "_hko_request_with_owned_anchor", anchor_inputs["_hko_request_with_owned_anchor"])
            fixture = normal._kord_normal_prior_fixture(
                tmp_path, monkeypatch, target_date=date(2026, 10, 2),
            )
            assert fixture.request.target_date == date(2026, 10, 2)
            assert fixture.request.source_cycle_time == datetime(2026, 10, 1, tzinfo=fixture.cut.tzinfo)
            normal._kord_public_bundles(fixture, monkeypatch, at=fixture.cut)
        immutable = {table: tuple(tuple(row) for row in fixture.conn.execute(f"SELECT * FROM {table} ORDER BY rowid"))
            for table in ("source_run", "source_run_coverage", "ensemble_snapshots", "raw_model_forecasts", "raw_forecast_artifacts")}
        owned_hashes = {row["artifact_path"]:sha256(Path(row["artifact_path"]).read_bytes()).hexdigest()
            for row in fixture.conn.execute("SELECT artifact_path FROM raw_forecast_artifacts")}
        old_row = dict(fixture.conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",
            (fixture.result.posterior_id,)).fetchone())
        old_shape = json.loads(old_row["provenance_json"])["bayes_precision_fusion"]["current_evidence_shape"]
        original_version = fixture.request.baseline_data_version
        # Export only mutable transport from the actually owned canonical
        # artifact. Its request/body/source clocks and DB descriptor stay put.
        transport = tmp_path / "normal-transport"
        transport.mkdir()
        precision_path = transport / "precision.json"
        precision_path.write_text(json.dumps(asdict(fixture.request.openmeteo_precision_guard.metadata), default=str))
        original_anchor = dict(fixture.conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?",
            (fixture.request.anchor_artifact_id,)).fetchone())
        manifest_path = transport / "anchor.manifest.json"
        manifest = RawForecastArtifactManifest(**{key: original_anchor[key] for key in (
            "source_id", "product_id", "data_version", "artifact_path", "sha256", "byte_size", "source_cycle_time",
            "source_available_at", "captured_at", "request_url")},
            request_params=json.loads(original_anchor["request_params_json"]), product_metadata={
                **json.loads(original_anchor["artifact_metadata_json"]), "artifact_id":fixture.request.anchor_artifact_id,
                "source_run_id":fixture.request.openmeteo_source_run_id,
                "openmeteo_payload_json":original_anchor["artifact_path"],
                "precision_metadata_json":str(precision_path),"manifest_json":str(manifest_path)})
        manifest.verify_artifact()
        write_manifest(manifest, manifest_path)
        profile[0] = current_text
        from src.data.replacement_forecast_source_run_identity import native_coordinate_certificate_reason
        assert native_coordinate_certificate_reason(fixture.conn,shape=old_shape,city=city.name,
            target_date=fixture.request.target_date,metric="low") == "REPLACEMENT_CURRENT_COORDINATE_COMPATIBILITY_MISSING_OR_INVALID"
        seed = {"city":city.name,"target_date":str(fixture.request.target_date),"temperature_metric":"low",
            "baseline_source_run_id":fixture.request.baseline_source_run_id,
            "openmeteo_source_run_id":fixture.request.openmeteo_source_run_id,"computed_at":fixture.cut.isoformat()}
        assert not queue._seed_already_covered(forecast_db=fixture.db, forecast_conn=fixture.conn, seed=seed)
        coverage = latest_baseline_coverage_for_replacement_seed(fixture.conn, city=city.name,
            target_date=seed["target_date"], temperature_metric="low", not_after_source_cycle_time=fixture.request.source_cycle_time,
            as_of_time=fixture.cut)
        assert coverage is not None and coverage["data_version"] == original_version
        # The queue's read API deliberately makes its connection query-only.
        new_cut = fixture.cut + timedelta(minutes=1)
        fixture.sql_clock[0] = new_cut
        plan = build_replacement_forecast_current_target_plan(fixture.db, now_utc=new_cut)
        assert plan.target_count == plan.can_seed_count == 1, plan
        seed_dir, request_dir = transport / "seeds", transport / "requests"
        discovery = discover_replacement_forecast_materialization_seeds(forecast_db=fixture.db,
            raw_manifest_dir=transport, seed_dir=seed_dir, request_dir=request_dir, computed_at=new_cut, limit=1)
        assert discovery.discovered_count == 1, discovery
        processed, failed, reasons = queue._prepare_seed_requests_with_connection(seed_dir=seed_dir,
            seed_processed_dir=transport/"seeds-processed",seed_failed_dir=transport/"seeds-failed",
            request_dir=request_dir,forecast_db=fixture.db,forecast_conn=None,limit=1)
        assert len(processed) == 1 and not failed, (processed,failed,reasons)
        queued = tuple(request_dir.glob("*.json"))
        assert len(queued) == 1
        queued_payload = json.loads(queued[0].read_text())
        assert Path(queued_payload["openmeteo_manifest_json"]).is_absolute()
        request = build_materialize_request_dataclass(queued_payload, base_dir=request_dir)
        assert request.baseline_data_version == original_version
        fingerprint_inputs = []
        real_sha256 = queue.hashlib.sha256
        def traced_hash(raw=b"", *args, **kwargs):
            try:
                identity = json.loads(raw)
                if isinstance(identity, dict) and "native_coordinate_inputs" in identity.get("raw", {}):
                    fingerprint_inputs.append(identity)
            except (ValueError, TypeError, UnicodeError):
                pass
            return real_sha256(raw, *args, **kwargs)
        with monkeypatch.context() as trace:
            trace.setattr(queue.hashlib, "sha256", traced_hash)
            fingerprint = queue._blocked_attempt_fingerprint(input_json=queued[0],
                forecast_db=fixture.db, payload=queued_payload)
        assert fingerprint is not None and len(fingerprint_inputs) == 1
        dependency = fingerprint_inputs[0]["raw"]["native_coordinate_inputs"]
        assert dependency["data_version"] == original_version and dependency["city_inputs_sha256"]
        legacy_namespace = copy.deepcopy(fingerprint_inputs[0])
        del legacy_namespace["raw"]["native_coordinate_inputs"]
        old_fingerprint = real_sha256(json.dumps(legacy_namespace, sort_keys=True,
            separators=(",", ":"), default=str).encode()).hexdigest()
        marker_dir = transport / "blocked_attempts"
        marker_path = queue._blocked_attempt_marker_path(marker_dir, queued_payload)
        queue._write_blocked_attempt_marker(marker_path=marker_path, payload=queued_payload,
            fingerprint=old_fingerprint)
        assert not queue._blocked_attempt_state(marker_dir=marker_dir,input_json=queued[0],
            payload=queued_payload,forecast_db=fixture.db)[2]
        queue._write_blocked_attempt_marker(marker_path=marker_path,payload=queued_payload,
            fingerprint=fingerprint)
        assert queue._blocked_attempt_state(marker_dir=marker_dir,input_json=queued[0],
            payload=queued_payload,forecast_db=fixture.db)[2]
        # Only the active city's inputs belong in this request's proof/FP.
        unrelated = copy.deepcopy(current)
        unrelated["cities"].append({"city":"Paris","lat":48.9694,"lon":2.4414,
            "timezone":"Europe/Paris","unit":"C","station_geometry":{
                "station_id":"LFPB","lat":48.9694,"lon":2.4414,"validity_reason":None}})
        profile[0] = json.dumps(unrelated,sort_keys=True,separators=(",", ":"))
        assert queue._blocked_attempt_fingerprint(input_json=queued[0],forecast_db=fixture.db,
            payload=queued_payload) == fingerprint
        profile[0] = current_text
        # Preserve the legacy marker so the real claim/actor exercises DRAIN.
        queue._write_blocked_attempt_marker(marker_path=marker_path,payload=queued_payload,
            fingerprint=old_fingerprint)
        calls = []
        def actor(command):
            path = Path(command[command.index("--input-json")+1])
            calls.append(path)
            fixture.conn.execute("PRAGMA query_only=OFF")
            code, stdout, stderr = cli._run_one(path, commit=True, init_schema=False,
                conn=fixture.conn, publish_wake=False, schema_ready=True,
                writer_lock=lambda: db_writer_lock(fixture.db, WriteClass.LIVE, blocking=False))
            return subprocess.CompletedProcess(command, code, stdout, stderr)
        executed = queue.process_replacement_forecast_live_materialization_queue(request_dir=request_dir,
            processed_dir=transport/"processed",failed_dir=transport/"failed",forecast_db=fixture.db,
            runner=actor,discover=False,limit=1)
        assert len(calls) == 1 and executed.failed_count == 0, executed
        latest = dict(fixture.conn.execute("SELECT * FROM forecast_posteriors ORDER BY posterior_id DESC LIMIT 1").fetchone())
        assert latest["posterior_id"] != fixture.result.posterior_id
        from types import SimpleNamespace
        new = SimpleNamespace(posterior_id=latest["posterior_id"])
        fresh = json.loads(fixture.conn.execute("SELECT provenance_json FROM forecast_posteriors WHERE posterior_id=?",
            (new.posterior_id,)).fetchone()[0])["bayes_precision_fusion"]["current_evidence_shape"]
        assert fresh["native_coordinate_compatibility"]["original_grid_surface_evidence_identity_hash"] == old_shape["grid_surface_evidence_identity_hash"]
        assert fresh["grid_surface_evidence_identity_hash"] != old_shape["grid_surface_evidence_identity_hash"]
        assert json.loads(old_row["q_json"]) == json.loads(fixture.conn.execute(
            "SELECT q_json FROM forecast_posteriors WHERE posterior_id=?", (new.posterior_id,)).fetchone()[0])
        fixture.request, fixture.result = request, new
        assert len(normal._kord_public_bundles(fixture, monkeypatch, at=new_cut)) == 2
        seed["computed_at"] = new_cut.isoformat()
        assert queue._seed_already_covered(forecast_db=fixture.db, forecast_conn=fixture.conn, seed=seed)
        repeat_plan = build_replacement_forecast_current_target_plan(fixture.db, now_utc=new_cut)
        assert repeat_plan.covered_count == 1, tuple(row.as_dict() for row in repeat_plan.rows)
        repeat = discover_replacement_forecast_materialization_seeds(forecast_db=fixture.db,
            raw_manifest_dir=transport,seed_dir=transport/"repeat-seeds",computed_at=new_cut,limit=1)
        assert repeat.discovered_count == 0 and repeat.failed_count == 0, repeat
        profile[0] = json.dumps(unrelated,sort_keys=True,separators=(",", ":"))
        assert len(normal._kord_public_bundles(fixture,monkeypatch,at=new_cut)) == 2
        assert queue._seed_already_covered(forecast_db=fixture.db,forecast_conn=fixture.conn,seed=seed)
        assert build_replacement_forecast_current_target_plan(fixture.db,now_utc=new_cut).covered_count == 1
        profile[0] = current_text
        changed = copy.deepcopy(current)
        changed["cities"][0]["lat"] += .001
        profile[0] = json.dumps(changed,sort_keys=True,separators=(",", ":"))
        assert not queue._seed_already_covered(forecast_db=fixture.db,forecast_conn=fixture.conn,seed=seed)
        assert build_replacement_forecast_current_target_plan(fixture.db,now_utc=new_cut).target_count == 0
        assert queue._blocked_attempt_fingerprint(input_json=queued[0],forecast_db=fixture.db,
            payload=queued_payload) != fingerprint
        assert native_coordinate_certificate_reason(fixture.conn,shape=fresh,city=city.name,
            target_date=request.target_date,metric="low") == "REPLACEMENT_CURRENT_COORDINATE_COMPATIBILITY_MISSING_OR_INVALID"
        with pytest.raises(AssertionError,match="REPLACEMENT_POSTERIOR_READINESS_NOT_LIVE_GRADE"):
            normal._kord_public_bundles(fixture,monkeypatch,at=new_cut)
        profile[0] = current_text
        # A normal new certificate must not turn a partial/bad native binding
        # into coverage. Each private single fault is restored, with actual
        # canonical source rows intact in the final immutability comparison.
        pristine = json.loads(latest["provenance_json"])
        original_snapshot_provenance = fixture.conn.execute("SELECT provenance_json FROM ensemble_snapshots WHERE snapshot_id=?",
            (fresh["snapshot_id"],)).fetchone()[0]
        original_manifest_hash = fixture.conn.execute("SELECT manifest_hash FROM source_run WHERE source_run_id=?",
            (request.baseline_source_run_id,)).fetchone()[0]
        for fault in ("missing_proof","original_hash","semantic_hash","missing_snapshot","sea_cell","source_manifest"):
            fixture.conn.execute("PRAGMA query_only=OFF")
            try:
                damaged = copy.deepcopy(pristine)
                damaged_shape = damaged["bayes_precision_fusion"]["current_evidence_shape"]
                if fault == "missing_proof":
                    del damaged_shape["native_coordinate_compatibility"]
                elif fault in ("original_hash","semantic_hash"):
                    field = "original_grid_surface_evidence_identity_hash" if fault=="original_hash" else "city_inputs_sha256"
                    damaged_shape["native_coordinate_compatibility"][field] = "0"*64
                elif fault == "missing_snapshot":
                    damaged_shape["snapshot_id"] = 999999
                elif fault == "sea_cell":
                    snapshot = dict(fixture.conn.execute("SELECT * FROM ensemble_snapshots WHERE snapshot_id=?",
                        (fresh["snapshot_id"],)).fetchone())
                    native_body = json.loads(snapshot["provenance_json"])
                    native_body["grid_surface_evidence"]["selected_land_fraction"] = 0.0
                    fixture.conn.execute("UPDATE ensemble_snapshots SET provenance_json=? WHERE snapshot_id=?",
                        (json.dumps(native_body),fresh["snapshot_id"]))
                else:
                    fixture.conn.execute("UPDATE source_run SET manifest_hash=? WHERE source_run_id=?",
                        ("0"*64,request.baseline_source_run_id))
                fixture.conn.execute("UPDATE forecast_posteriors SET provenance_json=? WHERE posterior_id=?",
                    (json.dumps(damaged),new.posterior_id))
                fixture.conn.commit()  # Independent readonly replay sees the private single fault.
                assert native_coordinate_certificate_reason(fixture.conn,shape=damaged_shape,city=city.name,
                    target_date=request.target_date,metric="low") is not None, fault
                assert not queue._seed_already_covered(forecast_db=fixture.db,forecast_conn=fixture.conn,seed=seed), fault
                with pytest.raises(AssertionError,match="BLOCKED"):
                    normal._kord_public_bundles(fixture,monkeypatch,at=new_cut)
            finally:
                fixture.conn.execute("PRAGMA query_only=OFF")
                fixture.conn.execute("UPDATE forecast_posteriors SET provenance_json=? WHERE posterior_id=?",
                    (latest["provenance_json"],new.posterior_id))
                fixture.conn.execute("UPDATE ensemble_snapshots SET provenance_json=? WHERE snapshot_id=?",
                    (original_snapshot_provenance,fresh["snapshot_id"]))
                fixture.conn.execute("UPDATE source_run SET manifest_hash=? WHERE source_run_id=?",
                    (original_manifest_hash,request.baseline_source_run_id))
                fixture.conn.commit()
        assert len(normal._kord_public_bundles(fixture,monkeypatch,at=new_cut)) == 2
        anchor_body_path = Path(original_anchor["artifact_path"])
        original_body_bytes = anchor_body_path.read_bytes()
        try:
            anchor_body_path.write_bytes(original_body_bytes+b" ")
            assert not queue._seed_already_covered(forecast_db=fixture.db,forecast_conn=fixture.conn,seed=seed)
            with pytest.raises(AssertionError,match="BLOCKED"):
                normal._kord_public_bundles(fixture,monkeypatch,at=new_cut)
        finally:
            anchor_body_path.write_bytes(original_body_bytes)
        assert len(normal._kord_public_bundles(fixture,monkeypatch,at=new_cut)) == 2
        expired_seed = {**seed,"computed_at":(request.source_cycle_time+timedelta(hours=31)).isoformat()}
        assert not queue._seed_already_covered(forecast_db=fixture.db,forecast_conn=fixture.conn,seed=expired_seed)
        assert dict(fixture.conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?", (old_row["posterior_id"],)).fetchone()) == old_row
        assert {table: tuple(tuple(row) for row in fixture.conn.execute(f"SELECT * FROM {table} ORDER BY rowid"))
            for table in immutable} == immutable
        assert {path:sha256(Path(path).read_bytes()).hexdigest() for path in owned_hashes} == owned_hashes
    finally:
        if fixture is not None:
            fixture.conn.close()
            fixture.builtin.close()
        next(static, None)


def test_low_baseline_rejects_uncertified_same_cycle_dataset() -> None:
    old_data_version = coordinate_bound_data_version(
        ECMWF_OPENDATA_LOW_DATA_VERSION_UNCERTIFIED, "a" * 64
    )
    decision = validate_replacement_source_run_identity(
        role="baseline_b0",
        temperature_metric="low",
        source_run=_source_run("baseline_b0", metric="low", dataset_id=old_data_version),
    )

    assert decision.valid is False
    assert "REPLACEMENT_SOURCE_RUN_DATA_VERSION_MISMATCH" in decision.reason_codes


def test_baseline_identity_reports_missing_current_coordinate_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.config as config

    monkeypatch.delattr(config, "runtime_coordinate_manifest_json", raising=False)
    expected = expected_replacement_dependency_identity_by_role("high")["baseline_b0"]
    decision = validate_replacement_source_run_identity(
        role="baseline_b0", temperature_metric="high", source_run={}
    )

    assert expected.data_version is None
    assert decision.valid is False
    assert decision.reason_codes == (
        "REPLACEMENT_SOURCE_RUN_ID_MISSING",
        "REPLACEMENT_SOURCE_RUN_SOURCE_ID_MISMATCH",
        "REPLACEMENT_SOURCE_RUN_CURRENT_COORDINATE_PROFILE_MISSING",
    )


def test_source_run_identity_validates_source_run_and_coverage_pair() -> None:
    for role in ("baseline_b0", "openmeteo_ifs9_anchor", "soft_anchor_posterior"):
        decision = validate_replacement_source_run_identity(
            role=role,
            temperature_metric="high",
            source_run=_source_run(role),
            coverage=_coverage(role),
        )
        assert decision.valid is True
        assert decision.reason_codes == ("REPLACEMENT_SOURCE_RUN_IDENTITY_VALID",)
        assert decision.source_run_id == f"run-{role}"


def test_source_run_identity_blocks_wrong_data_version_and_metric() -> None:
    decision = validate_replacement_source_run_identity(
        role="baseline_b0",
        temperature_metric="high",
        source_run=_source_run(
            "baseline_b0",
            dataset_id="ecmwf_opendata_mn2t3_local_calendar_day_min",
            temperature_metric="low",
        ),
    )

    assert decision.valid is False
    assert "REPLACEMENT_SOURCE_RUN_DATA_VERSION_MISMATCH" in decision.reason_codes
    assert "REPLACEMENT_SOURCE_RUN_METRIC_MISMATCH" in decision.reason_codes


def test_source_run_identity_blocks_non_ensemble_members_on_anchor_and_posterior() -> None:
    for role in ("openmeteo_ifs9_anchor", "soft_anchor_posterior"):
        decision = validate_replacement_source_run_identity(
            role=role,
            temperature_metric="high",
            source_run=_source_run(role, expected_members=51, observed_members=51),
        )
        assert decision.valid is False
        assert "REPLACEMENT_SOURCE_RUN_NON_ENSEMBLE_HAS_MEMBERS" in decision.reason_codes


def test_source_run_identity_requires_exact_expected_members_for_ensemble_products() -> None:
    decision = validate_replacement_source_run_identity(
        role="baseline_b0",
        temperature_metric="high",
        source_run=_source_run("baseline_b0", expected_members=50, observed_members=50),
    )

    assert decision.valid is False
    assert "REPLACEMENT_SOURCE_RUN_EXPECTED_MEMBERS_MISMATCH" in decision.reason_codes


def test_source_run_identity_blocks_coverage_mismatch() -> None:
    decision = validate_replacement_source_run_identity(
        role="openmeteo_ifs9_anchor",
        temperature_metric="high",
        source_run=_source_run("openmeteo_ifs9_anchor"),
        coverage=_coverage(
            "openmeteo_ifs9_anchor",
            source_run_id="different-run",
            data_version="openmeteo_ecmwf_ifs9_anchor_localday_low",
            physical_quantity="wrong_quantity",
        ),
    )

    assert decision.valid is False
    assert "REPLACEMENT_SOURCE_RUN_COVERAGE_ID_MISMATCH" in decision.reason_codes
    assert "REPLACEMENT_SOURCE_RUN_COVERAGE_DATA_VERSION_MISMATCH" in decision.reason_codes
    assert "REPLACEMENT_SOURCE_RUN_COVERAGE_PHYSICAL_QUANTITY_MISMATCH" in decision.reason_codes


def test_source_run_identity_rejects_unknown_metric_or_role() -> None:
    with pytest.raises(ValueError, match="temperature_metric"):
        expected_replacement_dependency_identity_by_role("mean")

    with pytest.raises(ValueError, match="unsupported replacement dependency role"):
        validate_replacement_source_run_identity(
            role="unknown",
            temperature_metric="high",
            source_run={},
        )


def test_retired_low_window_v2_yield_requires_closed_same_coordinate_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a proven uncertified LOW incumbent yields to the exact v2 run."""
    import sqlite3
    from datetime import datetime, timezone
    import src.data.replacement_input_hwm as hwm

    expected = expected_replacement_dependency_identity_by_role("low")["baseline_b0"]
    old = coordinate_bound_data_version(
        ECMWF_OPENDATA_LOW_DATA_VERSION_UNCERTIFIED,
        expected.data_version.rsplit("__coordsha_", 1)[1],
    )
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE forecast_posteriors (posterior_id INTEGER PRIMARY KEY, source_id TEXT,
        runtime_layer TEXT, city TEXT, target_date TEXT, temperature_metric TEXT,
        computed_at TEXT, dependency_source_run_ids_json TEXT, provenance_json TEXT);
      CREATE TABLE source_run (source_run_id TEXT PRIMARY KEY, source_id TEXT, dataset_id TEXT,
        temperature_metric TEXT, physical_quantity TEXT, observation_field TEXT,
        expected_members INTEGER, observed_members INTEGER, source_available_at TEXT);
      CREATE TABLE source_run_coverage (source_run_id TEXT, city TEXT, target_local_date TEXT,
        temperature_metric TEXT, computed_at TEXT, recorded_at TEXT, data_version TEXT, source_id TEXT,
        physical_quantity TEXT, observation_field TEXT, snapshot_ids_json TEXT);
      CREATE TABLE ensemble_snapshots (snapshot_id INTEGER PRIMARY KEY, source_run_id TEXT,
        city TEXT, target_date TEXT, temperature_metric TEXT, dataset_id TEXT,
        source_id TEXT, model_version TEXT, source_available_at TEXT, recorded_at TEXT);
    """)
    conn.execute("INSERT INTO forecast_posteriors VALUES (1, ?, 'live', 'Seoul', '2026-09-23', 'low', '2026-09-23T05:05:10+00:00', '{\"baseline_b0\":\"old18\",\"current_ensemble_snapshot\":18}', '{\"bayes_precision_fusion\":{\"current_evidence_shape\":{\"snapshot_id\":18}}}')", (expected_replacement_dependency_identity_by_role("low")["soft_anchor_posterior"].source_id,))
    for run_id, version in (("old18", old), ("new12", expected.data_version)):
        conn.execute("INSERT INTO source_run VALUES (?, ?, ?, 'low', ?, 'low_temp', 51, 51, '2026-09-23T04:00:00+00:00')", (run_id, expected.source_id, version, expected.physical_quantity))
    conn.execute("INSERT INTO source_run_coverage VALUES ('old18','Seoul','2026-09-23','low','2026-09-23T05:00:00+00:00','2026-09-23T05:00:00+00:00', ?, ?, ?, 'low_temp','[18]')", (old, expected.source_id, expected.physical_quantity))
    conn.execute("INSERT INTO ensemble_snapshots VALUES (18,'old18','Seoul','2026-09-23','low', ?, ?, 'ecmwf_ens','2026-09-23T04:00:00+00:00','2026-09-23T04:01:00+00:00')", (old, expected.source_id))
    conn.execute("INSERT INTO ensemble_snapshots VALUES (12,'new12','Seoul','2026-09-23','low', ?, ?, 'ecmwf_ens','2026-09-23T04:00:00+00:00','2026-09-23T04:01:00+00:00')", (expected.data_version, expected.source_id))
    monkeypatch.setattr(hwm, "_latest_eligible_ensemble_input_mark", lambda *_a, **_k: (12, datetime(2026, 9, 22, 12, tzinfo=timezone.utc)))
    assert hwm.retired_low_uncertified_incumbent_yields_to_current_ensemble(conn, city='Seoul', target_date='2026-09-23', metric='low', incoming_baseline_source_run_id='new12', decision_time=datetime(2026, 9, 23, 5, 5, 10, tzinfo=timezone.utc))
    assert not hwm.retired_low_uncertified_incumbent_yields_to_current_ensemble(conn, city='Seoul', target_date='2026-09-23', metric='low', incoming_baseline_source_run_id='old18', decision_time=datetime(2026, 9, 23, 5, 5, 10, tzinfo=timezone.utc))
    conn.execute("UPDATE source_run SET dataset_id=? WHERE source_run_id='old18'", (expected.data_version,))
    assert not hwm.retired_low_uncertified_incumbent_yields_to_current_ensemble(conn, city='Seoul', target_date='2026-09-23', metric='low', incoming_baseline_source_run_id='new12', decision_time=datetime(2026, 9, 23, 5, 5, 10, tzinfo=timezone.utc))
    conn.execute("UPDATE source_run SET dataset_id=? WHERE source_run_id='old18'", (old,))
    conn.execute("UPDATE forecast_posteriors SET provenance_json='not-json' WHERE posterior_id=1")
    assert not hwm.retired_low_uncertified_incumbent_yields_to_current_ensemble(conn, city='Seoul', target_date='2026-09-23', metric='low', incoming_baseline_source_run_id='new12', decision_time=datetime(2026, 9, 23, 5, 5, 10, tzinfo=timezone.utc))
    conn.execute("UPDATE forecast_posteriors SET provenance_json='{\"bayes_precision_fusion\":{\"current_evidence_shape\":{\"snapshot_id\":19}}}' WHERE posterior_id=1")
    assert not hwm.retired_low_uncertified_incumbent_yields_to_current_ensemble(conn, city='Seoul', target_date='2026-09-23', metric='low', incoming_baseline_source_run_id='new12', decision_time=datetime(2026, 9, 23, 5, 5, 10, tzinfo=timezone.utc))


def test_retired_low_yield_is_point_in_time_and_scope_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Future or foreign incumbent evidence cannot authorize a historical rollback."""
    import sqlite3
    from datetime import datetime, timezone
    import src.data.replacement_input_hwm as hwm

    expected = expected_replacement_dependency_identity_by_role("low")["baseline_b0"]
    old = coordinate_bound_data_version(ECMWF_OPENDATA_LOW_DATA_VERSION_UNCERTIFIED, expected.data_version.rsplit("__coordsha_", 1)[1])
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE forecast_posteriors (posterior_id INTEGER PRIMARY KEY, source_id TEXT, runtime_layer TEXT, city TEXT, target_date TEXT, temperature_metric TEXT, computed_at TEXT, dependency_source_run_ids_json TEXT, provenance_json TEXT);
      CREATE TABLE source_run (source_run_id TEXT PRIMARY KEY, source_id TEXT, dataset_id TEXT, temperature_metric TEXT, physical_quantity TEXT, observation_field TEXT, expected_members INTEGER, observed_members INTEGER, source_available_at TEXT);
      CREATE TABLE source_run_coverage (source_run_id TEXT, city TEXT, target_local_date TEXT, temperature_metric TEXT, computed_at TEXT, recorded_at TEXT, data_version TEXT, source_id TEXT, physical_quantity TEXT, observation_field TEXT, snapshot_ids_json TEXT);
      CREATE TABLE ensemble_snapshots (snapshot_id INTEGER PRIMARY KEY, source_run_id TEXT, city TEXT, target_date TEXT, temperature_metric TEXT, dataset_id TEXT, source_id TEXT, model_version TEXT, source_available_at TEXT, recorded_at TEXT);
    """)
    conn.execute("INSERT INTO source_run VALUES ('old', ?, ?, 'low', ?, 'low_temp',51,51,'2026-09-23T04:00:00+00:00')", (expected.source_id,old,expected.physical_quantity))
    conn.execute("INSERT INTO source_run VALUES ('new', ?, ?, 'low', ?, 'low_temp',51,51,'2026-09-23T04:00:00+00:00')", (expected.source_id,expected.data_version,expected.physical_quantity))
    conn.execute("INSERT INTO source_run_coverage VALUES ('old','Seoul','2026-09-23','low','2026-09-23T04:00:00+00:00','2026-09-23T06:00:00+00:00', ?, ?, ?, 'low_temp','[18]')", (old,expected.source_id,expected.physical_quantity))
    conn.execute("INSERT INTO ensemble_snapshots VALUES (18,'old','Seoul','2026-09-23','low', ?, ?, 'ecmwf_ens','2026-09-23T04:00:00+00:00','2026-09-23T04:01:00+00:00')", (old,expected.source_id))
    conn.execute("INSERT INTO ensemble_snapshots VALUES (12,'new','Seoul','2026-09-23','low', ?, ?, 'ecmwf_ens','2026-09-23T04:00:00+00:00','2026-09-23T04:01:00+00:00')", (expected.data_version,expected.source_id))
    dep='{\"baseline_b0\":\"old\",\"current_ensemble_snapshot\":18}'
    prov='{\"bayes_precision_fusion\":{\"current_evidence_shape\":{\"snapshot_id\":18}}}'
    conn.execute("INSERT INTO forecast_posteriors VALUES (1,?,'live','Seoul','2026-09-23','low','2026-09-23T06:00:00+00:00',?,?)", (expected_replacement_dependency_identity_by_role('low')['soft_anchor_posterior'].source_id,dep,prov))
    conn.execute("INSERT INTO forecast_posteriors VALUES (2,?,'live','Other','2026-09-23','low','2026-09-23T04:00:00+00:00',?,?)", (expected_replacement_dependency_identity_by_role('low')['soft_anchor_posterior'].source_id,dep,prov))
    monkeypatch.setattr(hwm,'_latest_eligible_ensemble_input_mark',lambda *_a,**_k:(12,datetime(2026,9,23,4,tzinfo=timezone.utc)))
    now=datetime(2026,9,23,5,tzinfo=timezone.utc)
    assert not hwm.retired_low_uncertified_incumbent_yields_to_current_ensemble(conn,city='Seoul',target_date='2026-09-23',metric='low',incoming_baseline_source_run_id='new',decision_time=now,incumbent_posterior_id=1)
    assert not hwm.retired_low_uncertified_incumbent_yields_to_current_ensemble(conn,city='Seoul',target_date='2026-09-23',metric='low',incoming_baseline_source_run_id='new',decision_time=now,incumbent_posterior_id=2)
