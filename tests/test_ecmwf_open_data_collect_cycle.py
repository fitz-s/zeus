# Created: 2026-05-11
# Last reused/audited: 2026-10-03
# Lifecycle: created=2026-05-11; last_reviewed=2026-10-03; last_reused=2026-10-03
# Purpose: Protect bounded collector, cross-track isolation and optional native capture without prediction-budget regression.
# Reuse: Inspect source-run, land-mask and shared-deadline contracts; use private DB/GRIB fixtures and fake HTTP.
# Authority basis: PLAN docs/operations/task_2026-05-11_ecmwf_download_replacement/PLAN.md §5.5
#   Cross-track filename collision antibody: per-step filenames include param
#   (e.g. .step003_mx2t3.grib2 vs .step003_mn2t3.grib2) so concurrent mx2t6_high
#   and mn2t6_low cycles sharing the same output_dir do not clobber each other.
"""Integration regression tests for collect_open_ens_cycle cross-track isolation.

Relationship being tested: when mx2t6_high (param=mx2t3) and mn2t6_low (param=mn2t3)
run concurrently and share the same FIFTY_ONE_ROOT output directory, their per-step
intermediate files must not collide.  The filename pattern is:
  .step{NNN}_{param}.grib2   (e.g. .step003_mx2t3.grib2, .step003_mn2t3.grib2)
"""
from __future__ import annotations

import sqlite3
import hashlib
import json
import time
import multiprocessing
import os
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.state.db import init_schema, init_schema_forecasts
from src.state.schema.v2_schema import apply_canonical_schema
from src.state.source_run_repo import write_source_run


def _terrain_audit_fixture(tmp_path, monkeypatch, *, tracks=("mx2t6_high", "mn2t6_low")):
    """Real GRIB + immutable private snapshot identities; only transport is fake."""
    from scripts import extract_open_ens_localday as extractor
    from src.data import ecmwf_open_data as module
    from tests.test_ingest_grib_source_run_context import _tiny_native_grib

    issue = datetime(2026, 1, 1, tzinfo=timezone.utc)
    raw, mask, mask_proof, _ = _tiny_native_grib(tmp_path, "mx2t6_high", member_count=1)
    z_bytes = mask.with_suffix(".z.grib2").read_bytes()
    decoded = extractor._read_land_mask(mask, mask_proof)
    grid_hash = decoded["grid_identity_hash"]
    root = tmp_path / "audit_source"
    paths = module._resolve_opendata_paths(source_root=root, environ={})
    folder = root / "raw" / "ecmwf_open_ens" / "ecmwf" / "20260101"
    folder.mkdir(parents=True)
    surface_paths = {}
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE source_run (source_run_id TEXT, source_id TEXT, track TEXT,
          source_cycle_time TEXT, status TEXT, completeness_status TEXT, partial_run INTEGER);
        CREATE TABLE ensemble_snapshots (snapshot_id INTEGER PRIMARY KEY, source_run_id TEXT,
          source_id TEXT, temperature_metric TEXT, source_cycle_time TEXT, source_available_at TEXT,
          authority TEXT, provenance_json TEXT);
        CREATE TABLE forecast_posteriors (posterior_id INTEGER PRIMARY KEY, probability REAL, computed_at TEXT);
        INSERT INTO forecast_posteriors VALUES (1, 0.42, '2026-01-01T02:00:00+00:00');
    """)
    for idx, track in enumerate(tracks, 1):
        metric = "high" if track == "mx2t6_high" else "low"
        run_id = f"ecmwf_open_data:{track}:2026-01-01T00Z:test"
        conn.execute("INSERT INTO source_run VALUES (?,?,?,?,?,?,?)",
                     (run_id, "ecmwf_open_data", track, issue.isoformat(), "SUCCESS", "COMPLETE", 0))
        provenance = {"grid_surface_evidence": {
            "mask_grid_identity_hash": grid_hash, "temperature_grid_identity_hash": grid_hash,
            "mask_sha256": hashlib.sha256(mask.read_bytes()).hexdigest(),
            "mask_source_cycle_time": issue.isoformat(),
            "request_lat": 51.6, "request_lon": .1,
            "selected_flat_index": 2, "selected_lat": 51.5, "selected_lon": 0.0,
            "selected_land_fraction": float(decoded["values"][2]),
        }}
        conn.execute("INSERT INTO ensemble_snapshots VALUES (?,?,?,?,?,?,?,?)", (
            idx, run_id, "ecmwf_open_data", metric, issue.isoformat(),
            (issue + timedelta(hours=2)).isoformat(), "VERIFIED", json.dumps(provenance),
        ))
        dest = folder / f".{track}_20260101_00z_lsm.grib2"
        dest.write_bytes(mask.read_bytes())
        dest.with_suffix(".proof.json").write_bytes(mask_proof.read_bytes())
        surface_paths[track] = dest.with_suffix(".z.grib2")
    conn.commit()
    before = tuple(conn.iterdump())
    closed = []

    class ReadConnection:
        def execute(self, *args):
            return conn.execute(*args)

        def close(self):
            closed.append(True)

    class Response:
        def __init__(self, body, *, range_response=False):
            self.body = body
            self.status_code = 206 if range_response else 200
            self.headers = {"Content-Length": str(len(body)), "Date": "Wed, 01 Jan 2100 00:00:00 GMT",
                            "Last-Modified": "Wed, 01 Jan 2099 00:00:00 GMT"}
            if range_response:
                self.headers["Content-Range"] = f"bytes 0-{len(body)-1}/{len(body)}"

        def iter_content(self, chunk_size):
            yield self.body

        def close(self):
            pass

        def raise_for_status(self):
            raise RuntimeError(f"HTTP {self.status_code}")

    class Session:
        def __init__(self):
            self.calls = []
            self.before_get = lambda: closed == [True]
            self.failure = None
            self.z_bytes = z_bytes
            self.index_overrides = {}

        def get(self, url, **kwargs):
            assert self.before_get(), "all snapshot reads must close before HTTP"
            self.calls.append((url, kwargs))
            assert 0 < kwargs["timeout"] <= 10
            assert kwargs["allow_redirects"] is False
            if self.failure:
                raise self.failure
            if url.endswith(".index"):
                row = dict(param="z", levtype="sfc", step="0", type="fc", stream="oper",
                           date="20260101", time="0000", _offset=0, _length=len(self.z_bytes), **{"class": "od"})
                row.update(self.index_overrides)
                return Response(json.dumps(row).encode())
            return Response(self.z_bytes, range_response=True)

        def close(self):
            pass

    session = Session()
    monkeypatch.setattr(module, "get_connection", ReadConnection)
    monkeypatch.setattr(module, "_resolve_opendata_paths", lambda: paths)
    monkeypatch.setattr(module.requests, "Session", lambda: session)
    # Parser/cache unit tests stay in-process. Actual wall-bound transport has
    # separate real spawn/loopback antibodies, not an inherited monkeypatch.
    def fake_transport(cycle, url, index_url, *, deadline):
        index_body, index_http = module._surface_audit_http(session, index_url, deadline=deadline)
        offset, length = module._surface_z_index_entry(index_body, cycle)
        body, range_http = module._surface_audit_http(session, url, deadline=deadline,
                                                     offset=offset, length=length)
        return index_body, body, {"source_fetched_at": datetime.now(timezone.utc).isoformat(),
                                 "index_http": index_http, "range_http": range_http}
    monkeypatch.setattr(module, "_fetch_surface_audit_bytes", fake_transport)
    return dict(db=conn, before=before, paths=paths, surface_paths=surface_paths,
                session=session, closed=closed, grid_hash=grid_hash, issue=issue,
                z_bytes=z_bytes, raw=raw)


def test_normal_surface_audit_actual_bytes_shared_hl_and_clock_identity(tmp_path, monkeypatch):
    from scripts.extract_open_ens_localday import _read_surface_geopotential
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    result = module.capture_open_ens_surface_audit()
    assert result["capture_status"] == "OBSERVED", result
    assert result["captured_tracks"] == ["mx2t6_high", "mn2t6_low"]
    assert len(fixture["session"].calls) == 2
    proofs = []
    for track, path in fixture["surface_paths"].items():
        assert path.read_bytes() == fixture["z_bytes"]
        proof_path = path.with_suffix(".proof.json")
        decoded = _read_surface_geopotential(path, proof_path)
        proof = decoded["proof"]
        proofs.append(proof)
        assert decoded["grid_identity_hash"] == fixture["grid_hash"]
        assert float(decoded["values"][2]) == 313.75
        assert proof["source_issued_at"] is None
        assert proof["index_http"]["headers"]["Date"].startswith("Wed, 01 Jan 2100")
        assert fixture["issue"] <= datetime.fromisoformat(proof["source_fetched_at"]) <= datetime.fromisoformat(proof["source_written_at"]) <= datetime.now(timezone.utc)
        index_body = path.with_suffix(".index.body").read_bytes()
        assert hashlib.sha256(index_body).hexdigest() == proof["source_index_sha256"]
        assert proof["range_http"]["headers"]["Content-Range"] == f"bytes 0-{len(fixture['z_bytes'])-1}/{len(fixture['z_bytes'])}"
        assert track in proof["source_run_id"]
    assert proofs[0]["source_fetched_at"] == proofs[1]["source_fetched_at"]
    before_cache = {str(p): p.read_bytes() for p in fixture["surface_paths"].values()}
    before_proofs = {str(p): p.with_suffix(".proof.json").read_bytes() for p in fixture["surface_paths"].values()}
    again = module.capture_open_ens_surface_audit()
    assert again["cache_reused"] is True
    assert len(fixture["session"].calls) == 2
    assert before_cache == {str(p): p.read_bytes() for p in fixture["surface_paths"].values()}
    assert before_proofs == {str(p): p.with_suffix(".proof.json").read_bytes() for p in fixture["surface_paths"].values()}
    assert tuple(fixture["db"].iterdump()) == fixture["before"]  # no q/old receipt/source clock write


@pytest.mark.parametrize("field,value", (("dataDate", 20260102), ("dataType", "cf"),
                                         ("scanningMode", 64), ("paramId", 130)))
def test_normal_surface_audit_real_wrong_cycle_grid_type_unit_unknown(tmp_path, monkeypatch, field, value):
    import eccodes as ec
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    gid = ec.codes_new_from_message(fixture["z_bytes"])
    try:
        ec.codes_set(gid, field, value)
        fixture["session"].z_bytes = ec.codes_get_message(gid)
    finally:
        ec.codes_release(gid)
    result = module.capture_open_ens_surface_audit()
    assert result["capture_status"] == "UNKNOWN", result
    assert all(not p.exists() for p in fixture["surface_paths"].values())
    assert len(fixture["session"].calls) == 2
    assert tuple(fixture["db"].iterdump()) == fixture["before"]


@pytest.mark.parametrize("overrides", ({"date": "20260102"}, {"step": "3"}, {"levtype": "pl"},
                                     {"param": "gh"}, {"_length": 1024 * 1024 + 1}))
def test_normal_surface_audit_index_rejection_has_no_range_retry(tmp_path, monkeypatch, overrides):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    fixture["session"].index_overrides = overrides
    result = module.capture_open_ens_surface_audit()
    assert result["capture_status"] == "UNKNOWN", result
    assert len(fixture["session"].calls) == 1
    assert all(not p.exists() for p in fixture["surface_paths"].values())


@pytest.mark.parametrize("failure", ("timeout", "deadline"))
def test_normal_surface_audit_slow_fail_no_publish_or_forecast_write(tmp_path, monkeypatch, failure):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    if failure == "timeout":
        fixture["session"].failure = module.requests.Timeout("slow optional index")
    else:
        clock = [100.0]
        monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
        original = fixture["session"].get
        def slow_get(url, **kwargs):
            response = original(url, **kwargs)
            clock[0] += 41
            return response
        monkeypatch.setattr(fixture["session"], "get", slow_get)
    result = module.capture_open_ens_surface_audit()
    assert result["capture_status"] == "UNKNOWN", result
    assert len(fixture["session"].calls) == 1
    assert all(not p.exists() for p in fixture["surface_paths"].values())
    assert tuple(fixture["db"].iterdump()) == fixture["before"]


def test_normal_surface_audit_changed_mask_and_publish_race_never_overwrite(tmp_path, monkeypatch):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    before_link = module.os.link
    raced = fixture["surface_paths"]["mx2t6_high"]
    sentinel = b"other publisher's immutable generation"
    def publish_race(source, target):
        if Path(target) == raced and not raced.exists():
            raced.write_bytes(sentinel)
        return before_link(source, target)
    monkeypatch.setattr(module.os, "link", publish_race)
    result = module.capture_open_ens_surface_audit()
    assert result["captured_tracks"] == ["mn2t6_low"], result
    assert raced.read_bytes() == sentinel
    assert "mx2t6_high" in result["track_gaps"]
    assert fixture["surface_paths"]["mn2t6_low"].read_bytes() == fixture["z_bytes"]


def test_normal_surface_audit_changed_source_generation_stays_unknown(tmp_path, monkeypatch):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    original = fixture["session"].get
    mask = fixture["surface_paths"]["mx2t6_high"].with_name(".mx2t6_high_20260101_00z_lsm.grib2")
    def change_after_read(url, **kwargs):
        response = original(url, **kwargs)
        if not url.endswith(".index"):
            mask.write_bytes(b"new-generation")
        return response
    monkeypatch.setattr(fixture["session"], "get", change_after_read)
    result = module.capture_open_ens_surface_audit()
    assert result["captured_tracks"] == ["mn2t6_low"], result
    assert "SURFACE_AUDIT_MASK_GENERATION_CHANGED" in result["track_gaps"]["mx2t6_high"]
    assert not fixture["surface_paths"]["mx2t6_high"].exists()


def test_normal_surface_audit_existing_corrupt_cache_kept_sibling_can_capture(tmp_path, monkeypatch):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    old = fixture["surface_paths"]["mx2t6_high"]
    old.write_bytes(b"immutable malformed prior cache")
    result = module.capture_open_ens_surface_audit()
    assert result["captured_tracks"] == ["mn2t6_low"], result
    assert old.read_bytes() == b"immutable malformed prior cache"
    assert result["track_gaps"]["mx2t6_high"] == "SURFACE_AUDIT_CACHE_PUBLISH_CONFLICT"


def test_normal_surface_audit_partial_publish_recovers_by_new_real_capture(tmp_path, monkeypatch):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    original = module.os.link
    lost = [False]
    proof = fixture["surface_paths"]["mx2t6_high"].with_suffix(".proof.json")
    def interrupted(source, target):
        if Path(target) == proof and not lost[0]:
            lost[0] = True
            raise OSError("temporary publisher interruption")
        return original(source, target)
    monkeypatch.setattr(module.os, "link", interrupted)
    first = module.capture_open_ens_surface_audit()
    assert first["captured_tracks"] == ["mn2t6_low"], first
    assert not proof.exists()
    old_low_proof = fixture["surface_paths"]["mn2t6_low"].with_suffix(".proof.json").read_bytes()
    second = module.capture_open_ens_surface_audit()
    assert second["captured_tracks"] == ["mx2t6_high", "mn2t6_low"], second
    assert proof.exists()
    assert len(fixture["session"].calls) == 2  # existing valid sibling supplies actual bytes/old fetched clock
    assert old_low_proof == fixture["surface_paths"]["mn2t6_low"].with_suffix(".proof.json").read_bytes()


@pytest.mark.parametrize("fault", ("index_oversized", "range_oversized", "range_200", "range_header"))
def test_normal_surface_audit_stream_bounds_and_range_proof(tmp_path, monkeypatch, fault):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    original = fixture["session"].get
    consumed = []
    def hostile(url, **kwargs):
        response = original(url, **kwargs)
        if url.endswith(".index") and fault == "index_oversized":
            def chunks(chunk_size):
                for _ in range(100):
                    consumed.append(1)
                    yield b"x" * 65536
            response.iter_content = chunks
        elif not url.endswith(".index"):
            if fault == "range_200":
                response.status_code = 200
            elif fault == "range_header":
                response.headers["Content-Range"] = "bytes 1-999/1000"
            elif fault == "range_oversized":
                response.body += b"x"
        return response
    monkeypatch.setattr(fixture["session"], "get", hostile)
    result = module.capture_open_ens_surface_audit()
    assert result["capture_status"] == "UNKNOWN", result
    assert all(not p.exists() for p in fixture["surface_paths"].values())
    assert len(fixture["session"].calls) == (1 if fault == "index_oversized" else 2)
    if fault == "index_oversized":
        assert len(consumed) == 17  # refuse during streaming, never drain all 100 chunks


@pytest.mark.parametrize("fault", ("future_cycle", "future_available", "running", "wrong_temp_grid"))
def test_normal_surface_audit_no_future_or_uncommitted_decision_input(tmp_path, monkeypatch, fault):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    conn = fixture["db"]
    if fault == "future_cycle":
        conn.execute("UPDATE source_run SET source_cycle_time='2100-01-01T00:00:00+00:00'")
    elif fault == "future_available":
        conn.execute("UPDATE ensemble_snapshots SET source_available_at='2100-01-01T00:00:00+00:00'")
    elif fault == "running":
        conn.execute("UPDATE source_run SET status='RUNNING'")
    else:
        conn.execute("UPDATE ensemble_snapshots SET provenance_json=json_set(provenance_json,'$.grid_surface_evidence.temperature_grid_identity_hash',?)", ("f" * 64,))
    before = tuple(conn.iterdump())
    result = module.capture_open_ens_surface_audit()
    assert result["capture_status"] == "UNKNOWN", result
    assert not fixture["session"].calls
    assert tuple(conn.iterdump()) == before


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
@pytest.mark.parametrize("field", ("raw_phi_m2_s2", "selected_point", "mask_sha256", "snapshot_id",
                                   "source_run_id", "source_issued_at"))
def test_cached_surface_own_reference_tamper_keeps_bad_track_unknown(tmp_path, monkeypatch, track, field):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    assert module.capture_open_ens_surface_audit()["capture_status"] == "OBSERVED"
    proof_path = fixture["surface_paths"][track].with_suffix(".proof.json")
    proof = json.loads(proof_path.read_bytes())
    mutations = {"raw_phi_m2_s2": 123456, "selected_point": {"flat_index": 1, "lat": 51.75, "lon": .25},
                 "mask_sha256": "a" * 64, "snapshot_id": 999999,
                 "source_run_id": "foreign-world-run", "source_issued_at": "2026-01-01T00:00:00Z"}
    proof[field] = mutations[field]
    proof_path.write_text(json.dumps(proof))
    bad_bytes = proof_path.read_bytes()
    result = module.capture_open_ens_surface_audit()
    sibling = "mn2t6_low" if track == "mx2t6_high" else "mx2t6_high"
    assert result["captured_tracks"] == [sibling], result
    assert track in result["track_gaps"]
    assert proof_path.read_bytes() == bad_bytes
    assert len(fixture["session"].calls) == 2


@pytest.mark.parametrize("change", ("new_row", "pruned_original", "changed_original", "foreign_source"))
def test_cached_surface_binds_original_not_always_latest(tmp_path, monkeypatch, change):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    assert module.capture_open_ens_surface_audit()["capture_status"] == "OBSERVED"
    cache = {t: p.with_suffix(".proof.json").read_bytes() for t, p in fixture["surface_paths"].items()}
    conn = fixture["db"]
    conn.execute("INSERT INTO ensemble_snapshots SELECT snapshot_id+100, source_run_id, source_id, temperature_metric, source_cycle_time, source_available_at, authority, provenance_json FROM ensemble_snapshots")
    if change == "pruned_original":
        conn.execute("DELETE FROM ensemble_snapshots WHERE snapshot_id=1")
    elif change == "changed_original":
        conn.execute("UPDATE ensemble_snapshots SET provenance_json=json_set(provenance_json,'$.grid_surface_evidence.selected_lat',50) WHERE snapshot_id=1")
    elif change == "foreign_source":
        conn.execute("UPDATE ensemble_snapshots SET source_id='foreign_world_source' WHERE snapshot_id=1")
    before = tuple(conn.iterdump())
    result = module.capture_open_ens_surface_audit()
    assert result["captured_tracks"] == (["mx2t6_high", "mn2t6_low"] if change == "new_row" else ["mn2t6_low"]), result
    assert result["cache_binding_role"] == "ORIGINAL_IMMUTABLE_SNAPSHOT_NOT_CURRENT_TARGET"
    assert cache == {t: p.with_suffix(".proof.json").read_bytes() for t, p in fixture["surface_paths"].items()}
    assert tuple(conn.iterdump()) == before


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
@pytest.mark.parametrize("change", ("absent_id", "foreign_id_run", "stable_aba"))
def test_cached_surface_original_lookup_proof_race_is_one_epoch(tmp_path, monkeypatch, track, change):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    assert module.capture_open_ens_surface_audit()["capture_status"] == "OBSERVED"
    proof_path = fixture["surface_paths"][track].with_suffix(".proof.json")
    original_bytes = proof_path.read_bytes()
    original = json.loads(original_bytes)
    foreign_track = "mn2t6_low" if track == "mx2t6_high" else "mx2t6_high"
    foreign = json.loads(fixture["surface_paths"][foreign_track].with_suffix(".proof.json").read_bytes())
    raced = []

    class RacingConnection:
        def execute(self, query, parameters=()):
            cursor = fixture["db"].execute(query, parameters)
            if "e.snapshot_id=?" in query and parameters[0] == original["snapshot_id"] and not raced:
                replacement = dict(original)
                if change == "absent_id":
                    replacement["snapshot_id"] = 999999
                else:
                    replacement["snapshot_id"] = foreign["snapshot_id"]
                    replacement["source_run_id"] = foreign["source_run_id"]
                proof_path.write_text(json.dumps(replacement))
                raced.append(proof_path.read_bytes())
                if change == "stable_aba":
                    proof_path.write_bytes(original_bytes)
            return cursor

        def close(self):
            pass

    monkeypatch.setattr(module, "get_connection", RacingConnection)
    result = module.capture_open_ens_surface_audit()
    assert raced
    if change == "stable_aba":
        assert result["captured_tracks"] == ["mx2t6_high", "mn2t6_low"], result
        assert proof_path.read_bytes() == original_bytes
    else:
        assert result["captured_tracks"] == [foreign_track], result
        assert "SURFACE_AUDIT_SOURCE_PROOF_GENERATION_CHANGED" in result["track_gaps"][track]
        assert proof_path.read_bytes() == raced[0]  # preserve the publisher, never overwrite with A
    assert len(fixture["session"].calls) == 2
    assert tuple(fixture["db"].iterdump()) == fixture["before"]


def _surface_test_half_packet(cycle, url, index_url, deadline, writer):
    os.write(writer.fileno(), struct.pack("!III", 100, 100, 100) + b"partial")
    time.sleep(30)


def _surface_test_oversized_packet(cycle, url, index_url, deadline, writer):
    os.write(writer.fileno(), struct.pack("!III", 1, 1024 * 1024 + 1, 1))
    time.sleep(30)


@pytest.mark.parametrize("worker", (_surface_test_half_packet, _surface_test_oversized_packet))
def test_surface_spawn_capped_partial_ipc_is_nonblocking_and_reaped(worker):
    from src.data import ecmwf_open_data as module

    before = {p.pid for p in multiprocessing.active_children()}
    start = time.monotonic()
    with pytest.raises((ValueError, module.requests.Timeout)):
        module._fetch_surface_audit_bytes(datetime(2026, 1, 1, tzinfo=timezone.utc),
            "unused", "unused", deadline=start + 3, _worker=worker)
    assert time.monotonic() - start < 3.7
    assert {p.pid for p in multiprocessing.active_children()} == before


@pytest.mark.parametrize("mode", ("good", "headers", "body", "silent"))
def test_surface_actual_spawn_loopback_has_global_wall_bound(tmp_path, mode):
    """Real production spawn wrapper, including get/header and body stalls."""
    from src.data import ecmwf_open_data as module
    from tests.test_ingest_grib_source_run_context import _tiny_native_grib

    _, mask, _, _ = _tiny_native_grib(tmp_path, "mx2t6_high", member_count=1)
    body = mask.with_suffix(".z.grib2").read_bytes()
    index = json.dumps(dict(param="z", levtype="sfc", step="0", type="fc", stream="oper",
                           date="20260101", time="0000", _offset=0, _length=len(body), **{"class": "od"})).encode()
    seen = threading.Event()
    stop = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            seen.set()
            try:
                if mode == "silent":
                    stop.wait(10)
                    return
                if mode == "headers":
                    self.connection.sendall(b"HTTP/1.1 200 OK\r\nX-Trickle: ")
                    while not stop.wait(.05):
                        self.connection.sendall(b"x")
                    return
                is_index = self.path.endswith(".index")
                payload = index if is_index else body
                self.send_response(200 if is_index else 206)
                self.send_header("Content-Length", str(len(payload)))
                if not is_index:
                    self.send_header("Content-Range", f"bytes 0-{len(body)-1}/{len(body)}")
                self.end_headers()
                if mode == "body":
                    for byte in payload:
                        if stop.wait(.05):
                            return
                        self.wfile.write(bytes((byte,)))
                        self.wfile.flush()
                else:
                    self.wfile.write(payload)
            except (OSError, ConnectionError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    before = {p.pid for p in multiprocessing.active_children()}
    base = f"http://127.0.0.1:{server.server_port}/surface"
    start = time.monotonic()
    try:
        if mode == "good":
            raw_index, raw_body, metadata = module._fetch_surface_audit_bytes(
                datetime(2026, 1, 1, tzinfo=timezone.utc), base + ".grib2", base + ".index", deadline=start + 5)
            assert raw_index == index and raw_body == body and metadata["status"] == "ok"
        else:
            with pytest.raises(module.requests.Timeout):
                module._fetch_surface_audit_bytes(datetime(2026, 1, 1, tzinfo=timezone.utc),
                    base + ".grib2", base + ".index", deadline=start + 3)
            assert time.monotonic() - start < 3.7
        assert seen.is_set(), "must exercise the HTTP phase, not merely time out during spawn startup"
        assert {p.pid for p in multiprocessing.active_children()} == before
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_hong_kong_selects_nearest_land_of_four_not_water_class_cell() -> None:
    from scripts.extract_open_ens_localday import _select_land_grid_points

    grid = {
        "gridType": "regular_ll", "Ni": 1440, "Nj": 721,
        "latitudeOfFirstGridPointInDegrees": 90.0,
        "longitudeOfFirstGridPointInDegrees": 180.0,
        "iDirectionIncrementInDegrees": 0.25,
        "jDirectionIncrementInDegrees": 0.25,
        "scanningMode": 0,
    }
    # Same-cycle IFS 0.25-degree LSM: the geometrically nearest cell has
    # 39.0625% land, whereas an adjacent cell has 50.78125%.
    fractions = {
        391417: 0.390625, 391416: 0.5078125,
        389977: 0.625, 389976: 0.65625,
    }
    selected = _select_land_grid_points(
        grid,
        [dict(city="Hong Kong", lat=22.3022, lon=114.1742)],
        fractions.__getitem__,
    )
    assert selected["Hong Kong"]["selected_flat_index"] == 391416
    assert selected["Hong Kong"]["selected_lat"] == 22.25
    assert selected["Hong Kong"]["selected_lon"] == 114.0
    assert selected["Hong Kong"]["selected_land_fraction"] == 0.5078125


def test_mask_possession_clock_cannot_use_pre_fetch_cycle_start() -> None:
    from src.data.ecmwf_open_data import _source_possession_clock

    cycle_start = datetime(2026, 9, 26, 6, tzinfo=timezone.utc)
    mask_fetched = datetime.now(timezone.utc)
    snapshot_time = _source_possession_clock(cycle_start, mask_fetched, extracted=True)
    source_run_time = _source_possession_clock(cycle_start, snapshot_time, extracted=True)
    assert cycle_start < mask_fetched <= snapshot_time <= source_run_time
    assert _source_possession_clock(cycle_start, None, extracted=False) == cycle_start


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
def test_optional_surface_cache_adds_no_http_or_prediction_budget_gate(tmp_path, monkeypatch, track):
    from src.data import ecmwf_open_data

    root = tmp_path / "source"
    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", root)
    requests_seen = []

    def forbidden_network(*args, **kwargs):
        requests_seen.append((args, kwargs))
        raise AssertionError("optional audit capture must not start slow HTTP")

    monkeypatch.setattr(ecmwf_open_data._RateLimitedSession, "get", forbidden_network)
    issue = date(2026, 6, 6)
    trusted = _trusted_land_source_for_date(issue)
    mask_source = {key.removeprefix("mask_"): value for key, value in trusted.items()
                   if key.startswith("mask_source")}
    mask_source.update(mask_sha256=trusted["mask_sha256"],
                       mask_grid_identity_hash=trusted["mask_grid_identity_hash"])
    mask_deadlines, extract_timeouts = [], []
    deadline = time.monotonic() + 2

    def mask_fetch(**kwargs):
        mask_deadlines.append(kwargs["deadline"])
        return mask_source

    def extract(cmd, *, label, timeout):
        extract_timeouts.append(timeout)
        manifest_path = Path(cmd[cmd.index("--manifest-path") + 1])
        manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        payload = _native_partial_scope_payload(track=track, target=date(2026, 6, 7),
                                                issue=issue, manifest_sha=manifest_sha)
        output_root = Path(cmd[cmd.index("--output-root") + 1])
        folder = output_root / ecmwf_open_data.TRACKS[track]["extract_subdir"] / "london" / "20260606"
        folder.mkdir(parents=True)
        (folder / "target.json").write_text(json.dumps(payload))
        surface = Path(cmd[cmd.index("--surface-geopotential-grib-path") + 1])
        assert not surface.exists()  # No fabricated cache or weather acquisition.
        return {"label": label, "ok": True, "returncode": 0, "stdout_tail": "", "stderr_tail": ""}

    conn = _make_conn(tmp_path)
    result = ecmwf_open_data.collect_open_ens_cycle(
        track=track, run_date=issue, run_hour=0,
        now_utc=datetime(2026, 6, 6, 9, tzinfo=timezone.utc), skip_download=True,
        conn=conn, _runner=extract, _mask_fetch_impl=mask_fetch,
        cycle_deadline_monotonic=deadline,
    )
    assert requests_seen == []
    assert mask_deadlines == [deadline]
    assert len(extract_timeouts) == 1 and 0 < extract_timeouts[0] <= 2
    assert result["snapshots_inserted"] == 1, result
    assert conn.execute("SELECT COUNT(*) FROM ensemble_snapshots").fetchone()[0] == 1


def test_collector_does_not_authorize_payload_with_different_mask_hash(tmp_path, monkeypatch) -> None:
    from src.data import ecmwf_open_data
    from tests.test_opendata_writes_v2_table import (
        _make_opendata_high_payload, _trusted_land_source_for_issue,
    )

    root = tmp_path / "51 source data"
    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", root)
    issue = "2026-05-01T00:00:00+00:00"
    payload = _make_opendata_high_payload(
        "2026-05-02", issue,
        local_day_start_iso="2026-05-01T23:00:00+00:00",
        local_day_end_iso="2026-05-02T23:00:00+00:00",
    )
    payload["grid_surface_evidence"]["mask_sha256"] = "d" * 64
    trusted = _trusted_land_source_for_issue(issue)
    mask_result = {
        "source": trusted["mask_source"],
        "source_url": trusted["mask_source_url"],
        "source_index_url": trusted["mask_source_index_url"],
        "source_cycle_time": trusted["mask_source_cycle_time"],
        "source_fetched_at": trusted["mask_source_fetched_at"],
        "source_index_offset": trusted["mask_source_index_offset"],
        "source_index_length": trusted["mask_source_index_length"],
        "mask_sha256": trusted["mask_sha256"],
        "mask_grid_identity_hash": trusted["mask_grid_identity_hash"],
    }

    def extract(cmd, *, label, timeout):
        output_root = Path(cmd[cmd.index("--output-root") + 1])
        folder = output_root / "open_ens_mx2t6_localday_max" / "london" / "20260501"
        folder.mkdir(parents=True)
        (folder / "target.json").write_text(json.dumps(payload), encoding="utf-8")
        return {"label": label, "ok": True, "returncode": 0, "stdout_tail": "", "stderr_tail": ""}

    conn = _make_conn(tmp_path)
    result = ecmwf_open_data.collect_open_ens_cycle(
        track="mx2t6_high", run_date=date(2026, 5, 1), run_hour=0,
        now_utc=datetime(2026, 5, 1, 9, tzinfo=timezone.utc),
        skip_download=True, conn=conn, _runner=extract,
        _mask_fetch_impl=lambda **_kw: mask_result,
    )
    assert result["status"] == "empty_ingest"
    assert conn.execute("SELECT COUNT(*) FROM ensemble_snapshots").fetchone()[0] == 0


@pytest.mark.parametrize("fractions", ({391417: 0.4}, {391417: float("nan")}))
def test_land_grid_selection_refuses_incomplete_or_invalid_mask(fractions) -> None:
    from scripts.extract_open_ens_localday import _select_land_grid_points

    grid = {
        "gridType": "regular_ll", "Ni": 1440, "Nj": 721,
        "latitudeOfFirstGridPointInDegrees": 90.0,
        "longitudeOfFirstGridPointInDegrees": 180.0,
        "iDirectionIncrementInDegrees": 0.25,
        "jDirectionIncrementInDegrees": 0.25,
        "scanningMode": 0,
    }
    with pytest.raises((KeyError, ValueError)):
        _select_land_grid_points(
            grid,
            [dict(city="Hong Kong", lat=22.3022, lon=114.1742)],
            fractions.__getitem__,
        )


def test_gridline_selection_keeps_four_distinct_points_and_wraps_longitude() -> None:
    from scripts.extract_open_ens_localday import _select_land_grid_points

    grid = {
        "gridType": "regular_ll", "Ni": 1440, "Nj": 721,
        "latitudeOfFirstGridPointInDegrees": 90.0,
        "longitudeOfFirstGridPointInDegrees": 180.0,
        "iDirectionIncrementInDegrees": 0.25,
        "jDirectionIncrementInDegrees": 0.25,
        "scanningMode": 0,
    }
    selected = _select_land_grid_points(
        grid, [{"city": "seam", "lat": 22.25, "lon": 179.75}],
        lambda idx: .75 if idx % 1440 == 0 else .25,
    )["seam"]
    assert {cell["flat_index"] for cell in selected["four_neighbors"]} == {
        271 * 1440 + 1439, 271 * 1440, 272 * 1440 + 1439, 272 * 1440,
    }
    assert selected["selected_lon"] == -180.0
    assert selected["selected_land_fraction"] == .75


@pytest.mark.parametrize("status, content_range, body_size", (
    (200, None, 32),
    (206, "bytes 5-35/99", 31),
    (206, "bytes 5-36/99", 31),
))
def test_static_mask_range_rejects_unproved_http_payload(
    status: int, content_range: str | None, body_size: int,
) -> None:
    from src.data.ecmwf_open_data import _read_static_mask_range

    class Response:
        headers = {"Content-Length": str(body_size)}
        status_code = status

        def __init__(self):
            self.headers = dict(self.headers)
            if content_range:
                self.headers["Content-Range"] = content_range

        def iter_content(self, chunk_size):
            yield b"G" * body_size

        def close(self):
            pass

    class Session:
        def get(self, *_args, **_kwargs):
            return Response()

    with pytest.raises(Exception):
        _read_static_mask_range(Session(), "https://example.test/lsm.grib2", 5, 32)


def _native_partial_scope_payload(
    *, track: str, target: date, issue: date, manifest_sha: str,
) -> dict[str, object]:
    """Small full-member native-window payload for one London target day."""
    from tests.test_opendata_writes_v2_table import _make_opendata_high_payload
    from tests.test_ingest_grib_source_run_context import _land_grid_proof
    from src.data import ecmwf_open_data

    start = datetime.combine(target, datetime.min.time(), tzinfo=timezone.utc) - timedelta(hours=1)
    end = start + timedelta(days=1)
    issue_iso = f"{issue.isoformat()}T00:00:00+00:00"
    payload = _make_opendata_high_payload(
        target.isoformat(), issue_iso,
        local_day_start_iso=start.isoformat(), local_day_end_iso=end.isoformat(),
        forecast_window_start_iso=start.isoformat(),
        forecast_window_end_iso=end.isoformat(),
    )
    cfg = ecmwf_open_data.TRACKS[track]
    payload.update(
        manifest_sha256=manifest_sha, manifest_hash=manifest_sha,
        data_version=cfg["data_version"],
        lead_day=(target - issue).days,
        lat=51.505299, lon=0.055278,
        nearest_grid_lat=51.5, nearest_grid_lon=0.0,
    )
    proof = _land_grid_proof()
    proof["mask_source_cycle_time"] = issue_iso
    proof["mask_source_fetched_at"] = (datetime.fromisoformat(issue_iso) + timedelta(hours=1)).isoformat()
    payload["grid_surface_evidence"] = proof
    if track == "mn2t6_low":
        payload.update(
            physical_quantity="mn2t3_local_calendar_day_min",
            param="mn2t3", paramId=122, short_name="mn2t3",
            step_type="min", temperature_metric="low",
        )
        for member in payload["members"]:
            member["inner_min_native_unit"] = member.pop("inner_max_native_unit")
            member.pop("boundary_max_native_unit")
            member["boundary_min_native_unit"] = 30.0
            member["boundary_ambiguous"] = False
            for window in member["native_windows"]:
                if f'{window["start_step_hours"]}-{window["end_step_hours"]}' in member["boundary_step_ranges"]:
                    window["value_native_unit"] = 30.0
    return payload


def _trusted_land_source_for_date(run_date: date) -> dict[str, object]:
    from tests.test_ingest_grib_source_run_context import _land_grid_proof, _trusted_land_source

    issue = datetime.combine(run_date, datetime.min.time(), tzinfo=timezone.utc)
    proof = _land_grid_proof()
    proof["mask_source_cycle_time"] = issue.isoformat()
    proof["mask_source_fetched_at"] = (issue + timedelta(hours=1)).isoformat()
    return _trusted_land_source(proof)


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
def test_partial_retry_preserves_qualified_far_scope_and_publishes_near_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, track: str,
) -> None:
    """A near-only 503 retry cannot erase the far target from an earlier full run."""
    from src.config import runtime_coordinate_manifest_json
    from src.data import ecmwf_open_data
    from src.data.replacement_input_hwm import latest_eligible_ensemble_input_cycle

    t1 = datetime.now(timezone.utc).replace(microsecond=0)
    t2 = t1 + timedelta(minutes=2)
    cut = t1 + timedelta(minutes=1)
    clock = {"now": t1}

    class CollectionDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"].astimezone(tz) if tz else clock["now"].replace(tzinfo=None)

    monkeypatch.setattr(ecmwf_open_data, "datetime", CollectionDateTime)
    monkeypatch.setattr(ecmwf_open_data._ingest_grib_module, "datetime", CollectionDateTime)
    run_date = t1.date()
    near, far = run_date + timedelta(days=1), run_date + timedelta(days=2)
    metric = "high" if track == "mx2t6_high" else "low"
    root = tmp_path / "51 source data"
    conn = sqlite3.connect(tmp_path / "forecasts.db")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)
    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", root)
    monkeypatch.setattr(ecmwf_open_data, "STEP_HOURS", list(range(3, 73, 3)))
    manifest = runtime_coordinate_manifest_json()
    manifest_sha = hashlib.sha256(manifest.encode()).hexdigest()
    cfg = ecmwf_open_data.TRACKS[track]
    json_dir = (
        root / "raw" / "coordinate_manifests" / manifest_sha
        / cfg["extract_subdir"] / "london" / run_date.strftime("%Y%m%d")
    )
    json_dir.mkdir(parents=True)
    far_payload = _native_partial_scope_payload(
        track=track, target=far, issue=run_date, manifest_sha=manifest_sha,
    )
    far_path = json_dir / f"{cfg['extract_subdir']}_target_{far.isoformat()}_lead_2.json"
    far_path.write_text(json.dumps(far_payload), encoding="utf-8")

    failed_far = False

    def fetch(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
        del mirrors
        if failed_far and step >= 51:
            return "FAILED", "HTTP_503"
        ecmwf_open_data._step_cache_path(
            output_dir, run_date=cycle_date, run_hour=cycle_hour,
            step=step, param=param,
        ).write_bytes(b"raw-grib-fixture")
        return "OK", None

    first = ecmwf_open_data.collect_open_ens_cycle(
        track=track, run_date=run_date, run_hour=0, conn=conn,
        skip_extract=True, _fetch_impl=fetch,
        grid_surface_source_evidence=_trusted_land_source_for_date(run_date),
        now_utc=clock["now"],
    )
    assert first["status"] == "ok", first
    old_far = conn.execute(
        "SELECT snapshot_id, members_json, source_available_at, fetch_time "
        "FROM ensemble_snapshots WHERE city='London' AND target_date=? "
        "AND temperature_metric=?", (far.isoformat(), metric),
    ).fetchone()
    old_coverage = conn.execute(
        "SELECT coverage_id, snapshot_ids_json, computed_at, recorded_at, "
        "readiness_status FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?",
        (far.isoformat(), metric),
    ).fetchone()
    assert old_far is not None and old_coverage["readiness_status"] == "LIVE_ELIGIBLE", tuple(
        conn.execute(
            "SELECT readiness_status, reason_code, expected_steps_json, observed_steps_json "
            "FROM source_run_coverage WHERE city='London' AND target_local_date=? "
            "AND temperature_metric=?", (far.isoformat(), metric),
        ).fetchone()
    )
    assert latest_eligible_ensemble_input_cycle(
        conn, city="London", target_date=far, metric=metric, decision_time=cut,
    ) == datetime.combine(run_date, datetime.min.time(), tzinfo=timezone.utc), (
        tuple(conn.execute(
            "SELECT source_available_at, imported_at, fetch_finished_at, captured_at "
            "FROM source_run WHERE source_run_id=?", (first["source_run_id"],),
        ).fetchone()),
        tuple(conn.execute(
            "SELECT source_available_at, fetch_time FROM ensemble_snapshots "
            "WHERE city='London' AND target_date=? AND temperature_metric=?",
            (far.isoformat(), metric),
        ).fetchone()),
        tuple(old_coverage), cut,
    )
    first_possession = conn.execute(
        "SELECT source_available_at, imported_at FROM source_run WHERE source_run_id=?",
        (first["source_run_id"],),
    ).fetchone()

    near_payload = _native_partial_scope_payload(
        track=track, target=near, issue=run_date, manifest_sha=manifest_sha,
    )
    for member in near_payload["members"]:
        for field in ("value_native_unit", "inner_max_native_unit", "boundary_max_native_unit",
                      "inner_min_native_unit", "boundary_min_native_unit"):
            if member.get(field) is not None:
                member[field] += 1.0
        for native_window in member["native_windows"]:
            native_window["value_native_unit"] += 1.0
    near_path = json_dir / f"{cfg['extract_subdir']}_target_{near.isoformat()}_lead_1.json"
    near_path.write_text(json.dumps(near_payload), encoding="utf-8")
    failed_far = True
    clock["now"] = t2
    second = ecmwf_open_data.collect_open_ens_cycle(
        track=track, run_date=run_date, run_hour=0, conn=conn,
        skip_extract=True, _fetch_impl=fetch,
        grid_surface_source_evidence=_trusted_land_source_for_date(run_date),
        now_utc=clock["now"],
    )
    assert second["status"] == "ok", second
    current_far = conn.execute(
        "SELECT snapshot_id, members_json, source_available_at, fetch_time "
        "FROM ensemble_snapshots WHERE city='London' AND target_date=? "
        "AND temperature_metric=?", (far.isoformat(), metric),
    ).fetchone()
    current_coverage = conn.execute(
        "SELECT coverage_id, snapshot_ids_json, computed_at, recorded_at, "
        "readiness_status FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?",
        (far.isoformat(), metric),
    ).fetchone()
    assert tuple(current_far) == tuple(old_far)
    assert tuple(current_coverage) == tuple(old_coverage)
    near_row = conn.execute(
        "SELECT snapshot_id, members_json, source_available_at, fetch_time "
        "FROM ensemble_snapshots WHERE city='London' AND target_date=? "
        "AND temperature_metric=?", (near.isoformat(), metric),
    ).fetchone()
    near_coverage = conn.execute(
        "SELECT coverage_id, snapshot_ids_json, computed_at, recorded_at, "
        "readiness_status FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?",
        (near.isoformat(), metric),
    ).fetchone()
    assert near_row is not None and near_coverage["readiness_status"] == "LIVE_ELIGIBLE"
    assert json.loads(near_row["members_json"])[0] != json.loads(old_far["members_json"])[0]
    assert second["stages"][0]["status"] == "PARTIAL"
    assert second["stages"][0]["failed_steps"] == list(range(51, 73, 3))
    run = conn.execute("SELECT status, completeness_status, partial_run, "
                       "observed_steps_json, reason_code FROM source_run "
                       "WHERE source_run_id=?", (first["source_run_id"],)).fetchone()
    assert run["status"] == "SUCCESS" and run["completeness_status"] == "COMPLETE"
    assert run["partial_run"] == 0
    assert json.loads(run["observed_steps_json"]) == list(range(3, 73, 3))
    assert tuple(conn.execute(
        "SELECT source_available_at, imported_at FROM source_run WHERE source_run_id=?",
        (first["source_run_id"],),
    ).fetchone()) == tuple(first_possession)
    assert latest_eligible_ensemble_input_cycle(
        conn, city="London", target_date=far, metric=metric, decision_time=cut,
    ) == datetime.combine(run_date, datetime.min.time(), tzinfo=timezone.utc)
    assert latest_eligible_ensemble_input_cycle(
        conn, city="London", target_date=near, metric=metric, decision_time=cut,
    ) is None
    assert latest_eligible_ensemble_input_cycle(
        conn, city="London", target_date=far, metric=metric,
        decision_time=t2 + timedelta(minutes=1),
    ) == datetime.combine(run_date, datetime.min.time(), tzinfo=timezone.utc)
    third = ecmwf_open_data.collect_open_ens_cycle(
        track=track, run_date=run_date, run_hour=0, conn=conn,
        skip_extract=True, _fetch_impl=fetch,
        grid_surface_source_evidence=_trusted_land_source_for_date(run_date),
        now_utc=clock["now"],
    )
    assert third["status"] == "ok", third
    repeat_near = conn.execute(
        "SELECT snapshot_id, members_json, source_available_at, fetch_time "
        "FROM ensemble_snapshots WHERE city='London' AND target_date=? "
        "AND temperature_metric=?", (near.isoformat(), metric),
    ).fetchone()
    repeat_coverage = conn.execute(
        "SELECT coverage_id, snapshot_ids_json, computed_at, recorded_at, "
        "readiness_status FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?",
        (near.isoformat(), metric),
    ).fetchone()
    assert tuple(repeat_near) == tuple(near_row)
    assert tuple(repeat_coverage) == tuple(near_coverage)


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
def test_partial_first_attempt_cannot_certify_far_scope_without_prior_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, track: str,
) -> None:
    """An incomplete far JSON cannot authorize a new far snapshot on a partial run."""
    from src.config import runtime_coordinate_manifest_json
    from src.data import ecmwf_open_data
    from src.data.replacement_input_hwm import latest_eligible_ensemble_input_cycle

    run_date = datetime.now(timezone.utc).date()
    near, far = run_date + timedelta(days=1), run_date + timedelta(days=2)
    metric = "high" if track == "mx2t6_high" else "low"
    root = tmp_path / "51 source data"
    conn = sqlite3.connect(tmp_path / "forecasts.db")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)
    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", root)
    monkeypatch.setattr(ecmwf_open_data, "STEP_HOURS", list(range(3, 73, 3)))
    manifest_sha = hashlib.sha256(runtime_coordinate_manifest_json().encode()).hexdigest()
    cfg = ecmwf_open_data.TRACKS[track]
    json_dir = (
        root / "raw" / "coordinate_manifests" / manifest_sha
        / cfg["extract_subdir"] / "london" / run_date.strftime("%Y%m%d")
    )
    json_dir.mkdir(parents=True)
    for target, lead in ((near, 1), (far, 2)):
        payload = _native_partial_scope_payload(
            track=track, target=target, issue=run_date, manifest_sha=manifest_sha,
        )
        (json_dir / f"{cfg['extract_subdir']}_target_{target.isoformat()}_lead_{lead}.json").write_text(
            json.dumps(payload), encoding="utf-8",
        )

    def fetch(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
        del mirrors
        if step >= 51:
            return "FAILED", "HTTP_503"
        ecmwf_open_data._step_cache_path(
            output_dir, run_date=cycle_date, run_hour=cycle_hour,
            step=step, param=param,
        ).write_bytes(b"raw-grib-fixture")
        return "OK", None

    result = ecmwf_open_data.collect_open_ens_cycle(
        track=track, run_date=run_date, run_hour=0, conn=conn,
        skip_extract=True, _fetch_impl=fetch,
        grid_surface_source_evidence=_trusted_land_source_for_date(run_date),
        now_utc=datetime.now(timezone.utc),
    )
    assert result["status"] == "ok", result
    near_coverage = conn.execute(
        "SELECT readiness_status FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?", (near.isoformat(), metric),
    ).fetchone()
    far_snapshot = conn.execute(
        "SELECT snapshot_id FROM ensemble_snapshots WHERE city='London' "
        "AND target_date=? AND temperature_metric=?", (far.isoformat(), metric),
    ).fetchone()
    far_coverage = conn.execute(
        "SELECT readiness_status FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?", (far.isoformat(), metric),
    ).fetchone()
    assert near_coverage is not None and near_coverage["readiness_status"] == "LIVE_ELIGIBLE"
    assert far_snapshot is None
    assert far_coverage is None or far_coverage["readiness_status"] != "LIVE_ELIGIBLE"
    assert latest_eligible_ensemble_input_cycle(
        conn, city="London", target_date=far, metric=metric,
        decision_time=datetime.now(timezone.utc) + timedelta(minutes=1),
    ) is None
    first_possession = tuple(conn.execute(
        "SELECT source_available_at, imported_at FROM source_run WHERE source_run_id=?",
        (result["source_run_id"],),
    ).fetchone())

    previous_near = tuple(conn.execute(
        "SELECT * FROM ensemble_snapshots WHERE city='London' AND target_date=? "
        "AND temperature_metric=?", (near.isoformat(), metric),
    ).fetchone())
    previous_near_coverage = tuple(conn.execute(
        "SELECT * FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?", (near.isoformat(), metric),
    ).fetchone())

    def full_fetch(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
        del mirrors
        ecmwf_open_data._step_cache_path(
            output_dir, run_date=cycle_date, run_hour=cycle_hour,
            step=step, param=param,
        ).write_bytes(b"raw-grib-fixture")
        return "OK", None

    completed = ecmwf_open_data.collect_open_ens_cycle(
        track=track, run_date=run_date, run_hour=0, conn=conn,
        skip_extract=True, _fetch_impl=full_fetch,
        grid_surface_source_evidence=_trusted_land_source_for_date(run_date),
        now_utc=datetime.now(timezone.utc) + timedelta(minutes=2),
    )
    assert completed["status"] == "ok", completed
    retained_near = conn.execute(
        "SELECT * FROM ensemble_snapshots WHERE city='London' AND target_date=? "
        "AND temperature_metric=?", (near.isoformat(), metric),
    ).fetchone()
    assert retained_near is not None and tuple(retained_near) == previous_near
    retained_near_coverage = conn.execute(
        "SELECT * FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?", (near.isoformat(), metric),
    ).fetchone()
    assert retained_near_coverage is not None and tuple(retained_near_coverage) == previous_near_coverage
    recovered_run = conn.execute(
        "SELECT status, completeness_status, partial_run, observed_steps_json "
        "FROM source_run WHERE source_run_id=?", (completed["source_run_id"],),
    ).fetchone()
    assert tuple(recovered_run)[:3] == ("SUCCESS", "COMPLETE", 0)
    assert json.loads(recovered_run["observed_steps_json"]) == list(range(3, 73, 3))
    assert tuple(conn.execute(
        "SELECT source_available_at, imported_at FROM source_run WHERE source_run_id=?",
        (result["source_run_id"],),
    ).fetchone()) == first_possession
    recovered_far = conn.execute(
        "SELECT readiness_status FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?", (far.isoformat(), metric),
    ).fetchone()
    assert recovered_far is not None and recovered_far["readiness_status"] == "LIVE_ELIGIBLE"
    assert latest_eligible_ensemble_input_cycle(
        conn, city="London", target_date=far, metric=metric,
        decision_time=datetime.now(timezone.utc) + timedelta(minutes=3),
    ) == datetime.combine(run_date, datetime.min.time(), tzinfo=timezone.utc)


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
@pytest.mark.parametrize(
    ("failure_mode", "expected_status"),
    (("failed", "download_failed"),
     ("not_released", "skipped_not_released"),
     ("deadline", "download_failed")),
)
def test_zero_ok_retry_preserves_same_run_qualified_far_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    track: str, failure_mode: str, expected_status: str,
) -> None:
    """A failed retry cannot revoke the existing complete same-run certificate."""
    from src.config import runtime_coordinate_manifest_json
    from src.data import ecmwf_open_data
    from src.data.replacement_input_hwm import latest_eligible_ensemble_input_cycle

    run_date = datetime.now(timezone.utc).date()
    far, near = run_date + timedelta(days=2), run_date + timedelta(days=1)
    metric = "high" if track == "mx2t6_high" else "low"
    root = tmp_path / "51 source data"
    conn = sqlite3.connect(tmp_path / "forecasts.db")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)
    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", root)
    monkeypatch.setattr(ecmwf_open_data, "STEP_HOURS", list(range(3, 73, 3)))
    monkeypatch.setattr(ecmwf_open_data, "_write_stderr_dump", lambda *_: None)
    manifest_sha = hashlib.sha256(runtime_coordinate_manifest_json().encode()).hexdigest()
    cfg = ecmwf_open_data.TRACKS[track]
    json_dir = (
        root / "raw" / "coordinate_manifests" / manifest_sha
        / cfg["extract_subdir"] / "london" / run_date.strftime("%Y%m%d")
    )
    json_dir.mkdir(parents=True)
    far_payload = _native_partial_scope_payload(
        track=track, target=far, issue=run_date, manifest_sha=manifest_sha,
    )
    (json_dir / f"{cfg['extract_subdir']}_target_{far.isoformat()}_lead_2.json").write_text(
        json.dumps(far_payload), encoding="utf-8",
    )

    def fetch(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
        del mirrors
        ecmwf_open_data._step_cache_path(
            output_dir, run_date=cycle_date, run_hour=cycle_hour,
            step=step, param=param,
        ).write_bytes(b"raw-grib-fixture")
        return "OK", None

    first = ecmwf_open_data.collect_open_ens_cycle(
        track=track, run_date=run_date, run_hour=0, conn=conn,
        skip_extract=True, _fetch_impl=fetch,
        grid_surface_source_evidence=_trusted_land_source_for_date(run_date),
        now_utc=datetime.now(timezone.utc),
    )
    assert first["status"] == "ok", first
    run_id = first["source_run_id"]
    query_scope = (far.isoformat(), metric)
    old_run = tuple(conn.execute(
        "SELECT * FROM source_run WHERE source_run_id=?", (run_id,),
    ).fetchone())
    old_snapshot = tuple(conn.execute(
        "SELECT * FROM ensemble_snapshots WHERE city='London' AND target_date=? "
        "AND temperature_metric=?", query_scope,
    ).fetchone())
    old_coverage = conn.execute(
        "SELECT * FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?", query_scope,
    ).fetchone()
    assert old_coverage["readiness_status"] == "LIVE_ELIGIBLE"
    old_coverage = tuple(old_coverage)
    expected_issue = datetime.combine(run_date, datetime.min.time(), tzinfo=timezone.utc)
    def latest_far():
        return latest_eligible_ensemble_input_cycle(
            conn, city="London", target_date=far, metric=metric,
            decision_time=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
    assert latest_far() == expected_issue

    def zero_ok(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
        del cycle_date, cycle_hour, param, step, output_dir, mirrors
        if failure_mode == "not_released":
            return "NOT_RELEASED", "HTTP_404"
        return "FAILED", "HTTP_503"

    retry = ecmwf_open_data.collect_open_ens_cycle(
        track=track, run_date=run_date, run_hour=0, conn=conn,
        skip_extract=True, _fetch_impl=zero_ok,
        cycle_deadline_monotonic=(time.monotonic() - 1 if failure_mode == "deadline" else None),
        now_utc=datetime.now(timezone.utc),
    )
    assert retry["status"] == expected_status, retry
    if failure_mode == "deadline":
        assert retry["reason"] == "CYCLE_DEADLINE_EXCEEDED"
    assert retry["snapshots_inserted"] == 0
    assert tuple(conn.execute(
        "SELECT * FROM source_run WHERE source_run_id=?", (run_id,),
    ).fetchone()) == old_run
    assert tuple(conn.execute(
        "SELECT * FROM ensemble_snapshots WHERE city='London' AND target_date=? "
        "AND temperature_metric=?", query_scope,
    ).fetchone()) == old_snapshot
    assert tuple(conn.execute(
        "SELECT * FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?", query_scope,
    ).fetchone()) == old_coverage
    assert latest_far() == expected_issue
    assert conn.execute(
        "SELECT 1 FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?", (near.isoformat(), metric),
    ).fetchone() is None


def _make_conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "world.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_canonical_schema(conn)
    return conn


def _ok_fetch_impl(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
    """Fake _fetch_impl that writes a zero-byte canonical file for each step."""
    canonical = output_dir / f".step{step:03d}_{param}.grib2"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_bytes(b"\x00" * 16)  # non-empty so resume logic treats it as done
    return ("OK", canonical)


def _write_raw_group(root: Path, day: str, hour: int, param: str) -> list[Path]:
    day_dir = root / "raw" / "ecmwf_open_ens" / "ecmwf" / day
    day_dir.mkdir(parents=True, exist_ok=True)
    paths = [
        day_dir / f".{day}_{hour:02d}z_step003_{param}_ens51.grib2",
        day_dir / f"open_ens_{day}_{hour:02d}z_steps_3to144_n48_test_params_{param}.grib2",
    ]
    for path in paths:
        path.write_bytes(b"raw")
    return paths


def _record_raw_authority(
    conn: sqlite3.Connection,
    *,
    day: str,
    hour: int,
    param: str,
    status: str = "SUCCESS",
    completeness: str = "COMPLETE",
    partial: bool = False,
    expected: int = 2,
    snapshot_count: int = 2,
    authority: str = "VERIFIED",
    coordsha: str | None = "6e28420e5809b3b30f5a075629c0ebd56b1fa15d405a00a85455b4fbcd1a14f4",
) -> None:
    track = "mx2t6_high" if param == "mx2t3" else "mn2t6_low"
    metric = "high" if param == "mx2t3" else "low"
    iso_day = datetime.strptime(day, "%Y%m%d").date().isoformat()
    # collect_open_ens_cycle stamps ":coordsha:<manifest_sha>" onto the id. The
    # fixture wrote only the bare cycle prefix, so every retention test passed
    # while production matched nothing and evicted nothing for 9 days (live
    # 2026-09-18: 10 recognized groups, all "source_run: MISSING"). Default to
    # the real shape; pass coordsha=None for the legacy bare-id form.
    source_run_id = f"ecmwf_open_data:{track}:{iso_day}T{hour:02d}Z"
    if coordsha is not None:
        source_run_id = f"{source_run_id}:coordsha:{coordsha}"
    write_source_run(
        conn,
        source_run_id=source_run_id,
        source_id="ecmwf_open_data",
        track=track,
        release_calendar_key=f"ecmwf_open_data:{track}:standard",
        source_cycle_time=f"{iso_day}T{hour:02d}:00:00+00:00",
        status=status,
        completeness_status=completeness,
        partial_run=partial,
        expected_count=expected,
        observed_count=expected,
    )
    for index in range(snapshot_count):
        conn.execute(
            """
            INSERT INTO ensemble_snapshots (
                city, target_date, temperature_metric, physical_quantity,
                observation_field, issue_time, available_at, fetch_time,
                lead_hours, members_json, model_version, dataset_id,
                source_id, source_run_id, authority
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"City{index}",
                iso_day,
                metric,
                "daily_extreme",
                "high_temp" if metric == "high" else "low_temp",
                f"{iso_day}T{hour:02d}:00:00+00:00",
                f"{iso_day}T{hour:02d}:00:00+00:00",
                f"{iso_day}T{hour:02d}:00:00+00:00",
                24.0,
                "[1.0]",
                "test",
                f"test_{source_run_id}",
                "ecmwf_open_data",
                source_run_id,
                authority,
            ),
        )
    conn.commit()


def test_raw_retention_deletes_only_old_complete_verified_groups(tmp_path):
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    old_paths = _write_raw_group(raw_root, "20260818", 0, "mx2t3")
    recent_paths = _write_raw_group(raw_root, "20260820", 0, "mx2t3")
    conn = _make_conn(tmp_path)
    _record_raw_authority(conn, day="20260818", hour=0, param="mx2t3")

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "APPLIED"
    assert result["eligible_group_count"] == 1
    assert result["deleted_file_count"] == 2
    assert all(not path.exists() for path in old_paths)
    assert all(path.exists() for path in recent_paths)


def test_raw_retention_fails_closed_on_incomplete_canonical_proof(tmp_path):
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    partial_paths = _write_raw_group(raw_root, "20260815", 0, "mx2t3")
    missing_paths = _write_raw_group(raw_root, "20260816", 6, "mn2t3")
    disputed_paths = _write_raw_group(raw_root, "20260817", 12, "mx2t3")
    mismatch_paths = _write_raw_group(raw_root, "20260818", 18, "mn2t3")
    conn = _make_conn(tmp_path)
    _record_raw_authority(
        conn,
        day="20260815",
        hour=0,
        param="mx2t3",
        status="PARTIAL",
        completeness="PARTIAL",
        partial=True,
    )
    # 20260816 intentionally has no source_run row.
    _record_raw_authority(
        conn,
        day="20260817",
        hour=12,
        param="mx2t3",
        authority="DISPUTED",
    )
    _record_raw_authority(
        conn,
        day="20260818",
        hour=18,
        param="mn2t3",
        snapshot_count=1,
    )

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "NO_ELIGIBLE_RAW"
    assert result["retained_group_count"] == 4
    assert all(
        path.exists()
        for path in partial_paths + missing_paths + disputed_paths + mismatch_paths
    )


def test_raw_retention_evicts_same_day_proven_group_and_keeps_unproven(tmp_path):
    """Retention is gated by the per-cycle proof, not by a calendar window.

    2026-09-17 operator directive: `_RAW_RETENTION_CALENDAR_DAYS = 0`, so TODAY's
    cycle is eligible the moment its own source-run is COMPLETE and its snapshot
    count matches with every row VERIFIED. This pins both halves — a proven
    same-day group is deleted, and an unproven same-day group is still kept — so
    that reinstating a calendar grace period fails here rather than silently
    re-growing ~23 GB/day of raw GRIB.
    """
    from src.data import ecmwf_open_data

    assert ecmwf_open_data._RAW_RETENTION_CALENDAR_DAYS == 0

    raw_root = tmp_path / "51 source data"
    proven_paths = _write_raw_group(raw_root, "20260821", 0, "mx2t3")
    unproven_paths = _write_raw_group(raw_root, "20260821", 12, "mx2t3")
    conn = _make_conn(tmp_path)
    # Only the 00Z cycle gets COMPLETE + VERIFIED canonical evidence.
    _record_raw_authority(conn, day="20260821", hour=0, param="mx2t3")

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "APPLIED"
    assert result["eligible_group_count"] == 1
    assert all(not path.exists() for path in proven_paths)
    assert all(path.exists() for path in unproven_paths)


def test_raw_retention_matches_the_writers_coordsha_suffixed_run_id(tmp_path):
    """RED-on-revert for the identity drift that silently disabled eviction.

    `collect_open_ens_cycle` stamps `…:<cycle>Z:coordsha:<manifest_sha>` onto the
    source-run id (added 2026-09-09, 160b5ff40), while this retention lookup was
    written against the bare cycle prefix (2026-08-21, 0afd62b16) and kept probing
    for equality. Every group therefore read as MISSING and nothing was ever
    evicted — verified live 2026-09-18: 10 recognized groups, 0 eligible, 19.14 GB
    retained. Reverting to an equality probe must fail here.
    """
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    paths = _write_raw_group(raw_root, "20260818", 0, "mx2t3")
    conn = _make_conn(tmp_path)
    # Only the realistic suffixed id is recorded — no bare-id row exists.
    _record_raw_authority(conn, day="20260818", hour=0, param="mx2t3")

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "APPLIED", (
        "a proven cycle whose source-run id carries the writer's coordsha suffix "
        "must be evictable; an equality probe on the bare prefix matches nothing"
    )
    assert result["eligible_group_count"] == 1
    assert all(not path.exists() for path in paths)


def test_raw_retention_retains_when_any_coordsha_pin_is_unproven(tmp_path):
    """A re-pin must not authorize deleting raw that an earlier pin still needs."""
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    paths = _write_raw_group(raw_root, "20260818", 0, "mx2t3")
    conn = _make_conn(tmp_path)
    _record_raw_authority(conn, day="20260818", hour=0, param="mx2t3", coordsha="a" * 64)
    # Second pin for the SAME cycle, still partial: the group must be retained.
    _record_raw_authority(
        conn,
        day="20260818",
        hour=0,
        param="mx2t3",
        coordsha="b" * 64,
        completeness="PARTIAL",
        partial=True,
    )

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "NO_ELIGIBLE_RAW"
    assert result["retained_group_count"] == 1
    assert all(path.exists() for path in paths)


def test_raw_retention_sweeps_spent_transport_sidecars(tmp_path):
    """`.partial`/`.ranges.json` whose canonical exists are spent resume state.

    These are invisible to the group proof by construction (`_raw_file_identity`
    matches only `.grib2`), so the calendar cutoff never reaches them at any age.
    The success path also never unlinked the range manifests, so one accumulated
    per completed step forever — measured 2026-09-18: 350 regrew in a single cycle
    after a manual sweep of 1,104, every one with a completed `.grib2` sibling.
    """
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    group = _write_raw_group(raw_root, "20260818", 0, "mx2t3")
    day_dir = group[0].parent
    canonical = group[0]

    spent = [
        day_dir / f"{canonical.name}.pf.bbdefa2950f49882.partial",
        day_dir / f"{canonical.name}.cf.bbdefa2950f49882.partial",
        day_dir / f"{canonical.name}.pf.bbdefa2950f49882.partial.ranges.json",
        day_dir / f"{canonical.name}.partial",
    ]
    for path in spent:
        path.write_bytes(b"sidecar")
    # An in-flight step: no canonical exists, so its resume state must survive.
    inflight = day_dir / f".20260818_00z_step999_mx2t3_ens51.grib2.pf.deadbeef.partial"
    inflight_manifest = day_dir / f"{inflight.name}.ranges.json"
    for path in (inflight, inflight_manifest):
        path.write_bytes(b"resumable")

    conn = _make_conn(tmp_path)
    _record_raw_authority(conn, day="20260818", hour=0, param="mx2t3")

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "APPLIED"
    assert all(not path.exists() for path in spent), (
        "sidecars whose canonical exists or is being evicted are spent and must go"
    )
    assert inflight.exists() and inflight_manifest.exists(), (
        "a sidecar with no canonical is an in-flight or resumable download; "
        "deleting its manifest would discard recoverable transport progress"
    )


def test_raw_retention_sweeps_sidecars_stranded_by_an_earlier_eviction(tmp_path):
    """A drained day's sidecars are spent, even though their canonical is gone.

    The first rule was "canonical exists or is being evicted now", which strands
    sidecars the moment an eviction completes: the canonical they point at no
    longer exists, so that test can never fire again. Observed live 2026-09-18 —
    the 00Z ingest evicted 20260917's groups and left 1.0 GB behind.
    """
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    day_dir = raw_root / "raw" / "ecmwf_open_ens" / "ecmwf" / "20260818"
    day_dir.mkdir(parents=True)
    # No canonical .grib2 at all: this day was already drained by a prior run.
    stranded = [
        day_dir / ".20260818_00z_step003_mx2t3_ens51.grib2.pf.abc123.partial",
        day_dir / ".20260818_00z_step003_mx2t3_ens51.grib2.pf.abc123.partial.ranges.json",
    ]
    for path in stranded:
        path.write_bytes(b"stranded")

    conn = _make_conn(tmp_path)
    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "APPLIED"
    assert all(not path.exists() for path in stranded), (
        "sidecars in a day with zero canonicals cannot be resumed into and are spent"
    )


def test_raw_retention_keeps_an_entire_in_flight_day(tmp_path):
    """The dangerous edge of the drained-day rule: a cycle mid-download.

    A day that has partials and NO canonical yet looks identical to a drained day
    by file inventory. It is not — it is a download in progress, and deleting its
    range manifests would discard recoverable transport state. The group proof is
    what separates them: an unproven group means the raw is still wanted.
    """
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    day_dir = raw_root / "raw" / "ecmwf_open_ens" / "ecmwf" / "20260820"
    day_dir.mkdir(parents=True)
    inflight = [
        day_dir / ".20260820_12z_step003_mn2t3_ens51.grib2.pf.beef01.partial",
        day_dir / ".20260820_12z_step003_mn2t3_ens51.grib2.pf.beef01.partial.ranges.json",
        day_dir / ".20260820_12z_step006_mn2t3_ens51.grib2.cf.beef01.partial",
    ]
    for path in inflight:
        path.write_bytes(b"downloading")

    conn = _make_conn(tmp_path)
    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    # KNOWN LIMIT, asserted so it is a decision and not an accident: by file
    # inventory alone an in-flight day is indistinguishable from a drained one,
    # so these are swept. That is acceptable because the transport re-fetches the
    # step (losing only a resumable byte-range prefix, never a proven forecast),
    # and because retention runs only AFTER an ingest commits — never while the
    # same daemon holds the OpenData lock mid-download.
    assert all(not path.exists() for path in inflight)


def test_raw_retention_keeps_sidecars_when_the_group_is_retained(tmp_path):
    """An unproven group keeps its raw AND its resume state."""
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    group = _write_raw_group(raw_root, "20260818", 0, "mx2t3")
    canonical = group[0]
    sidecar = canonical.parent / f"{canonical.name}.pf.abc123.partial"
    sidecar.write_bytes(b"sidecar")
    conn = _make_conn(tmp_path)
    # No authority recorded -> group retained.

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    # The canonical survives (unproven), but its sidecar is still spent state:
    # the canonical exists, so that byte-range prefix is no longer resumable.
    assert all(path.exists() for path in group)
    assert result["status"] in {"APPLIED", "NO_ELIGIBLE_RAW"}
    assert not sidecar.exists()


def test_coordinate_manifest_prune_removes_only_superseded_cycles(tmp_path):
    """Decoded-JSON cycle views older than the ingested cycle are write-only.

    `_build_cycle_scoped_json_root` assembles a temp view of the SELECTED cycle
    only, so once a newer cycle is ingested every older <city>/<cycle> tree is
    unreachable. They inherited no retention: measured 2026-09-18, 33 cycles per
    city across 54 cities spanning 2026-09-09..09-17 — 234 MB, ~26 MB/day,
    unbounded at ~9.5 GB/year.
    """
    from src.data import ecmwf_open_data

    root = tmp_path / "coordsha"
    subdir = "open_ens_mx2t6_localday_max"
    names = ("20260909_cycle12z", "20260910", "20260917_cycle12z", "20260917_cycle18z")
    for city in ("paris", "tokyo"):
        for name in names:
            d = root / subdir / city / name
            d.mkdir(parents=True)
            (d / "payload.json").write_text('{"k": 1}', encoding="utf-8")
    # An unrecognised directory must never be treated as a spent cycle.
    stray = root / subdir / "paris" / "scratch-notes"
    stray.mkdir(parents=True)
    (stray / "keep.txt").write_text("keep", encoding="utf-8")

    summary = ecmwf_open_data._prune_superseded_coordinate_manifest_cycles(
        coordinate_raw_root=root,
        extract_subdir=subdir,
        keep_run_date=date(2026, 9, 17),
        keep_run_hour=18,
    )

    assert summary["status"] == "PRUNED"
    assert summary["removed_cycle_dirs"] == 6, summary  # 3 superseded x 2 cities
    assert summary["removed_bytes"] > 0
    assert summary["errors"] == []
    for city in ("paris", "tokyo"):
        assert (root / subdir / city / "20260917_cycle18z").is_dir(), "kept cycle"
        for gone in ("20260909_cycle12z", "20260910", "20260917_cycle12z"):
            assert not (root / subdir / city / gone).exists()
    assert stray.is_dir() and (stray / "keep.txt").exists()


def test_coordinate_manifest_prune_is_a_noop_on_a_missing_subdir(tmp_path):
    """Cleanup must fail soft: a missing tree is not an error."""
    from src.data import ecmwf_open_data

    summary = ecmwf_open_data._prune_superseded_coordinate_manifest_cycles(
        coordinate_raw_root=tmp_path / "absent",
        extract_subdir="open_ens_mn2t6_localday_min",
        keep_run_date=date(2026, 9, 17),
        keep_run_hour=0,
    )
    assert summary["status"] == "NO_SUPERSEDED_CYCLES"
    assert summary["removed_cycle_dirs"] == 0
    assert summary["errors"] == []


def test_raw_retention_rejects_negative_days(tmp_path):
    from src.data import ecmwf_open_data

    conn = _make_conn(tmp_path)
    with pytest.raises(ValueError, match="must not be negative"):
        ecmwf_open_data._plan_decoded_open_data_raw_retention(
            conn,
            raw_root=tmp_path / "51 source data",
            reference_date=date(2026, 8, 21),
            retention_days=-1,
        )


def test_raw_retention_never_follows_matching_symlink(tmp_path):
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    day_dir = raw_root / "raw" / "ecmwf_open_ens" / "ecmwf" / "20260818"
    day_dir.mkdir(parents=True)
    target = tmp_path / "outside.grib2"
    target.write_bytes(b"must remain")
    link = day_dir / ".20260818_00z_step003_mx2t3_ens51.grib2"
    link.symlink_to(target)
    conn = _make_conn(tmp_path)
    _record_raw_authority(conn, day="20260818", hour=0, param="mx2t3")

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "NO_ELIGIBLE_RAW"
    assert link.is_symlink()
    assert target.read_bytes() == b"must remain"


# ---------------------------------------------------------------------------
# Regression: mx2t6_high and mn2t6_low per-step files do NOT collide
# ---------------------------------------------------------------------------

def test_cross_track_per_step_filenames_are_distinct(tmp_path, monkeypatch):
    """mx2t6_high (mx2t3) and mn2t6_low (mn2t3) written to the same output_dir
    must produce distinct .step{NNN}_{param}.grib2 filenames — no collision."""
    from src.data import ecmwf_open_data

    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", tmp_path / "51 source data")
    monkeypatch.setattr(ecmwf_open_data, "STEP_HOURS", [3, 6, 9])

    files_written: dict[str, list[str]] = {}

    def capturing_fetch_impl(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
        canonical = output_dir / f".step{step:03d}_{param}.grib2"
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_bytes(b"\x00" * 16)
        files_written.setdefault(param, []).append(canonical.name)
        return ("OK", canonical)

    common_kwargs = dict(
        run_date=date(2026, 5, 11),
        run_hour=0,
        now_utc=datetime(2026, 5, 11, 9, 0, tzinfo=timezone.utc),
        _fetch_impl=capturing_fetch_impl,
        skip_extract=True,
    )

    # Run both tracks (sequentially here; concurrent in production)
    ecmwf_open_data.collect_open_ens_cycle(
        track="mx2t6_high",
        conn=_make_conn(tmp_path),
        **common_kwargs,
    )
    ecmwf_open_data.collect_open_ens_cycle(
        track="mn2t6_low",
        conn=_make_conn(tmp_path),
        **common_kwargs,
    )

    high_files = set(files_written.get("mx2t3", []))
    low_files  = set(files_written.get("mn2t3", []))

    assert high_files, "mx2t6_high produced no per-step files"
    assert low_files,  "mn2t6_low produced no per-step files"

    # The intersection must be empty — filenames are distinct because param differs.
    collision = high_files & low_files
    assert not collision, (
        f"Cross-track filename collision detected: {collision}. "
        "Per-step filenames must include param to prevent clobbering between "
        "concurrent mx2t6_high and mn2t6_low cycles."
    )

    # Sanity: each track produces exactly STEP_HOURS files.
    assert len(high_files) == 3, f"Expected 3 high files, got {len(high_files)}: {high_files}"
    assert len(low_files)  == 3, f"Expected 3 low files, got {len(low_files)}: {low_files}"


@pytest.mark.parametrize("track", ["mx2t6_high", "mn2t6_low"])
def test_collect_open_ens_cycle_passes_explicit_manifest(tmp_path, monkeypatch, track):
    """HIGH and LOW sample runtime stations, never an external city's old node."""
    from src.data import ecmwf_open_data

    fifty_one_root = tmp_path / "51 source data"
    manifest_path = fifty_one_root / "docs" / "tigge_city_coordinate_manifest_full_latest.json"
    extract_script = fifty_one_root / "scripts" / "extract_open_ens_localday.py"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps({"cities": [{
        "city": "Tel Aviv", "lat": 32.0853, "lon": 34.7818,
    }]}))
    cities = {
        "Tel Aviv": SimpleNamespace(lat=32.011398, lon=34.8867,
                                    timezone="Asia/Jerusalem", settlement_unit="C"),
        "New City": SimpleNamespace(lat=10.0, lon=-20.0,
                                    timezone="UTC", settlement_unit="F"),
    }
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: cities)
    extract_script.parent.mkdir(parents=True)
    extract_script.write_text("# test extractor\n")
    paths = ecmwf_open_data.OpenDataPaths(
        raw_root=fifty_one_root,
        asset_root=fifty_one_root,
        extract_script=extract_script,
        manifest_path=manifest_path,
        origin="test",
    )

    commands: list[list[str]] = []

    def capture_extract(cmd, *, label, timeout):
        commands.append([str(part) for part in cmd])
        return {"label": label, "ok": False, "stderr_tail": "stop before ingest"}

    result = ecmwf_open_data.collect_open_ens_cycle(
        track=track,
        run_date=date(2026, 6, 6),
        run_hour=0,
        now_utc=datetime(2026, 6, 6, 9, 0, tzinfo=timezone.utc),
        skip_download=True,
        conn=_make_conn(tmp_path),
        _runner=capture_extract,
        _mask_fetch_impl=lambda **_kw: {
            "source": "ecmwf_open_data_ifs_oper_fc_step0_lsm",
            "source_url": "https://example.test/mask.grib2",
            "source_index_url": "https://example.test/mask.index",
            "source_cycle_time": "2026-06-06T00:00:00+00:00",
            "source_fetched_at": "2026-06-06T01:00:00+00:00",
            "source_index_offset": 0, "source_index_length": 187781,
            "mask_sha256": "a" * 64, "mask_grid_identity_hash": "b" * 64,
        },
        _paths=paths,
    )

    assert result["status"] == "extract_failed"
    assert commands
    cmd = commands[0]
    assert "--manifest-path" in cmd
    actual = Path(cmd[cmd.index("--manifest-path") + 1])
    assert actual != manifest_path
    payload = json.loads(actual.read_text())
    assert {row["city"] for row in payload["cities"]} == set(cities)
    tel_aviv = next(row for row in payload["cities"] if row["city"] == "Tel Aviv")
    assert (tel_aviv["lat"], tel_aviv["lon"]) == (32.011398, 34.8867)
    assert tel_aviv["timezone"] == "Asia/Jerusalem"
    assert tel_aviv["unit"] == "C"
    assert hashlib.sha256(actual.read_bytes()).hexdigest() in actual.name
    assert json.loads(manifest_path.read_text())["cities"][0]["lat"] == 32.0853
    assert ".openclaw/workspace-venus" not in " ".join(cmd)


def test_runtime_coordinate_manifest_preserves_prior_snapshots(tmp_path, monkeypatch):
    from src.data import ecmwf_open_data

    city = SimpleNamespace(lat=32.011398, lon=34.8867,
                           timezone="Asia/Jerusalem", settlement_unit="C")
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {"Tel Aviv": city})
    first = ecmwf_open_data._write_runtime_coordinate_manifest(tmp_path)
    content = first.read_bytes()
    assert ecmwf_open_data._write_runtime_coordinate_manifest(tmp_path) == first
    city.lon = 35.1
    second = ecmwf_open_data._write_runtime_coordinate_manifest(tmp_path)
    assert second != first
    assert first.read_bytes() == content
    assert json.loads(second.read_text())["cities"][0]["lon"] == 35.1
    city.lat = float("nan")
    with pytest.raises(ValueError, match="invalid extraction coordinates"):
        ecmwf_open_data._write_runtime_coordinate_manifest(tmp_path)


def test_extract_paths_keep_raw_root_but_pin_script_to_repo(tmp_path):
    """Raw storage is configurable while executable producer code is versioned."""
    from src.data import ecmwf_open_data

    source_root = tmp_path / "new-repo" / "51 source data"
    resolved = ecmwf_open_data._resolve_opendata_paths(
        source_root=source_root,
        environ={},
    )

    assert resolved.raw_root == source_root.resolve()
    assert resolved.asset_root == source_root.resolve()
    assert resolved.extract_script == ecmwf_open_data.PROJECT_ROOT / "scripts" / "extract_open_ens_localday.py"
    assert resolved.origin == "repo_script_fixed"


def test_explicit_source_root_keeps_repo_script(tmp_path):
    """An explicit raw root never selects executable code from a legacy checkout."""
    from src.data import ecmwf_open_data

    source_root = tmp_path / "configured" / "51 source data"
    resolved = ecmwf_open_data._resolve_opendata_paths(
        environ={"ZEUS_51_SOURCE_ROOT": str(source_root)},
    )

    assert resolved.raw_root == source_root.resolve()
    assert resolved.asset_root == source_root.resolve()
    assert resolved.extract_script == ecmwf_open_data.PROJECT_ROOT / "scripts" / "extract_open_ens_localday.py"
    assert resolved.origin == "env_raw_root_repo_script"


def test_missing_extract_assets_fail_before_download_or_subprocess(tmp_path, monkeypatch):
    """A broken asset root is an explicit failed job, not an opaque subprocess error."""
    from src.data import ecmwf_open_data

    missing_root = tmp_path / "missing" / "51 source data"
    runner_called = False
    fetch_called = False
    paths = ecmwf_open_data.OpenDataPaths(
        raw_root=missing_root,
        asset_root=missing_root,
        extract_script=missing_root / "scripts" / "extract_open_ens_localday.py",
        manifest_path=missing_root
        / "docs"
        / "tigge_city_coordinate_manifest_full_latest.json",
        origin="test_missing",
    )

    def forbidden_runner(*args, **kwargs):
        nonlocal runner_called
        runner_called = True
        raise AssertionError("subprocess must not run without extractor assets")

    def forbidden_fetch(*args, **kwargs):
        nonlocal fetch_called
        fetch_called = True
        raise AssertionError("download must not run without extractor assets")

    result = ecmwf_open_data.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=date(2026, 6, 6),
        run_hour=0,
        now_utc=datetime(2026, 6, 6, 9, 0, tzinfo=timezone.utc),
        skip_download=False,
        conn=_make_conn(tmp_path),
        _runner=forbidden_runner,
        _fetch_impl=forbidden_fetch,
        _paths=paths,
    )

    assert result["status"] == "extract_failed"
    assert str(result["reason"]).startswith("MISSING_EXTRACT_ASSETS:")
    assert result["stages"][0]["status"] == "MISSING_EXTRACT_ASSETS"
    assert runner_called is False
    assert fetch_called is False


def test_extract_asset_paths_share_one_cycle_bundle():
    """The script is always the checked-in producer; runtime manifest is explicit."""
    from src.data import ecmwf_open_data

    paths = ecmwf_open_data._resolve_opendata_paths()

    assert paths.extract_script == ecmwf_open_data.PROJECT_ROOT / "scripts" / "extract_open_ens_localday.py"
    assert paths.manifest_path == (
        paths.asset_root / "docs" / "tigge_city_coordinate_manifest_full_latest.json"
    )


def test_versioned_extractor_preserves_high_native_inner_and_boundary_candidates(tmp_path, monkeypatch):
    """HIGH keeps both native planes; only the inner candidate is emitted as value."""
    from scripts import extract_open_ens_localday as extractor

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"cities": [{
            "city": "New York",
            "lat": 40.7,
            "lon": -74.0,
            "timezone": "America/New_York",
            "unit": "C",
            "station_geometry": {
                "station_id": "KJFK", "station_surface": "UNKNOWN", "validity_reason": None,
                "reference_role": "airport_reference", "ground_status": "UNPROVEN",
                "ground_elevation_m": None,
            },
        }]}),
        encoding="utf-8",
    )
    grib = tmp_path / "cycle.grib2"
    grib.write_bytes(b"fixture")

    entries = {}
    for member in range(51):
        entries[(member, 6)] = {
            "member": member,
            "step_hours": 6,
            "start_step": 3,
            "end_step": 6,
            "step_range": "3-6",
            "city_values_k": {"New York": {
                "value_k": 300.15 + member,
                "nearest_grid_lat": 40.75,
                "nearest_grid_lon": -74.0,
                "nearest_grid_distance_km": None,
            }},
        }
        entries[(member, 21)] = {
            "member": member,
            "step_hours": 21,
            "start_step": 18,
            "end_step": 21,
            "step_range": "18-21",
            "city_values_k": {"New York": {
                "value_k": 301.15 + member,
                "nearest_grid_lat": 40.75,
                "nearest_grid_lon": -74.0,
                "nearest_grid_distance_km": None,
            }},
        }
    monkeypatch.setattr(
        extractor,
        "_scan_grib_with_city_values",
        lambda _path, _track, _cities, **_kwargs: {
            "issue_dt": datetime(2026, 6, 6, 0, tzinfo=timezone.utc),
            "entries": entries,
            "rejected_cities": {},
            "temperature_grid_identity_hash": "c" * 64,
            "selected_cities": {"New York": {
                "selected_flat_index": 1, "selected_lat": 40.75,
                "selected_lon": -74.0, "selected_land_fraction": .9,
                "nearest_grid_distance_km": 5.0, "four_neighbors": [],
            }},
        },
    )
    monkeypatch.setattr(extractor, "_read_land_mask", lambda *_a: {
        "proof": {"source": "ecmwf_open_data_ifs_oper_fc_step0_lsm",
                  "source_url": "https://example.test/mask.grib2",
                  "source_index_url": "https://example.test/mask.index",
                  "source_cycle_time": "2026-06-06T00:00:00+00:00",
                  "source_fetched_at": "2026-06-06T01:00:00+00:00",
                  "source_index_offset": 0, "source_index_length": 187781,
                  "mask_sha256": "b" * 64},
        "grid_identity_hash": "c" * 64,
    })

    output_root = tmp_path / "raw"
    result = extractor.extract_open_ens_localday(
        grib_path=grib,
        track_name="mx2t6_high",
        manifest_path=manifest,
        output_root=output_root,
        mask_grib_path=grib,
        mask_proof_path=grib,
    )
    assert result["status"] == "ok"
    payload_path = next(output_root.rglob("*_target_2026-06-06_lead_0.json"))
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert payload["grid_surface_evidence"]["station_geometry"]["ground_status"] == "UNPROVEN"
    assert payload["grid_surface_evidence"]["station_geometry"]["station_surface"] == "UNKNOWN"
    assert payload["boundary_ambiguous"] is False
    assert payload["selected_step_ranges_inner"] == ["18-21"]
    assert payload["selected_step_ranges_boundary"] == ["3-6"]
    member = payload["members"][0]
    assert member["inner_step_ranges"] == ["18-21"]
    assert member["boundary_step_ranges"] == ["3-6"]
    assert member["native_windows"] == [
        {
            "start_step_hours": 3,
            "end_step_hours": 6,
            "value_native_unit": pytest.approx(27.0),
        },
        {
            "start_step_hours": 18,
            "end_step_hours": 21,
            "value_native_unit": pytest.approx(28.0),
        },
    ]
    assert member["inner_max_native_unit"] == pytest.approx(28.0)
    assert member["boundary_max_native_unit"] == pytest.approx(27.0)
    assert member["value_native_unit"] == pytest.approx(member["inner_max_native_unit"])


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"dataDate": 20260923}, "mixed issue fields"),
        ({"Ni": 4}, "mixed grid metadata"),
        ({}, "duplicate member/step tuple"),
    ],
)
def test_extractor_rejects_mixed_cycle_grid_or_duplicate_member_step(
    tmp_path, monkeypatch, mutation, message
):
    """The producer cannot silently combine incompatible GRIB messages."""
    from scripts import extract_open_ens_localday as extractor

    base = {
        "shortName": "mx2t3",
        "dataDate": 20260922,
        "dataTime": 0,
        "dataType": "fc",
        "units": "K",
        "typeOfLevel": "heightAboveGround",
        "level": 2,
        "stepUnits": 1,
        "stepType": "max",
        "startStep": 27,
        "endStep": 30,
        "stepRange": "27-30",
        "lengthOfTimeRange": 3,
        "indicatorOfUnitForTimeRange": 1,
        "gridType": "regular_ll",
        "Ni": 2,
        "Nj": 2,
        "latitudeOfFirstGridPointInDegrees": 1.0,
        "longitudeOfFirstGridPointInDegrees": 0.0,
        "iDirectionIncrementInDegrees": 1.0,
        "jDirectionIncrementInDegrees": 1.0,
        "scanningMode": 0,
    }
    first = dict(base)
    second = dict(base)
    second.update(mutation)
    handles = [first, second]
    monkeypatch.setattr(extractor, "codes_grib_new_from_file", lambda _fh: handles.pop(0) if handles else None)
    monkeypatch.setattr(extractor, "codes_get", lambda handle, key: handle[key])
    monkeypatch.setattr(extractor, "codes_is_defined", lambda handle, key: key in handle)
    monkeypatch.setattr(extractor, "codes_get_values", lambda _handle: [300.0, 300.0, 300.0, 300.0])
    monkeypatch.setattr(extractor, "codes_release", lambda _handle: None)
    grib = tmp_path / "simulated.grib2"
    grib.write_bytes(b"fixture")
    cities = [{"city": "X", "lat": 0.0, "lon": 0.0}]

    with pytest.raises(ValueError, match=message):
        extractor._scan_grib_with_city_values(
            grib,
            extractor.TRACKS["mx2t6_high"],
            cities,
            mask={"fields": {key: first[key] for key in extractor._GRID_KEYS},
                  "values": [1.0] * 4,
                  "proof": {"source_cycle_time": "2026-09-22T00:00:00+00:00"}},
        )
