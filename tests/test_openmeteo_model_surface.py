# Created: 2026-09-29
# Last reused/audited: 2026-09-29
# Authority basis: finite_evidence_probability_symmetry Sep29 native surface slice; INV-14/47.
"""Real OM entity decoding and immutable static-evidence clock relationships."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest
from omfiles import OmFileWriter

from src.data import openmeteo_model_surface as surface


@pytest.fixture
def ordinary_static_http(tmp_path, monkeypatch):
    data = np.full((1441, 2879), 6.0, dtype=np.float32)
    data[720, 1440] = -999
    data[720, 1441] = -20
    data[720, 1442] = np.nan
    data[720, 1443] = 9999
    path = tmp_path / "official-shaped.om"
    writer = OmFileWriter(str(path))
    root = writer.write_array(data, chunks=(20, 20), name="HSURF")
    writer.close(root)
    entity = {"body": path.read_bytes(), "etag": '"first"',
              "last_modified": "Mon, 28 Sep 2026 00:00:00 GMT", "calls": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            entity["calls"].append(dict(self.headers))
            if not entity.get("force_200") and self.headers.get("If-None-Match") == entity["etag"]:
                self.send_response(304)
                if not entity.get("omit_304_headers"):
                    self.send_header("ETag", entity["etag"])
                    self.send_header("Last-Modified", entity["last_modified"])
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(entity.get("declared_size", len(entity["body"]))))
            self.send_header("ETag", entity["etag"])
            self.send_header("Last-Modified", entity["last_modified"])
            self.end_headers()
            if not entity.get("declared_size"):
                self.wfile.write(entity["body"])

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(surface, "_asset_url", lambda domain: f"http://127.0.0.1:{server.server_port}/HSURF.om")
    monkeypatch.setattr(surface, "_cache_root", lambda: tmp_path / "static")
    clock = [datetime(2026, 9, 29, 12, tzinfo=UTC)]
    monkeypatch.setattr(surface, "_now", lambda: clock[0])
    yield entity, clock, tmp_path
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def _proof(capture, *, lon=0.125, body_at=None):
    return surface.model_surface_witness("icon_global", selected_latitude=0,
        selected_longitude=lon, body_captured_at=body_at or "2026-09-29T11:00:00+00:00",
        asset_capture=capture)


def _validate(proof, *, lon=0.125, decision="2026-09-29T12:01:00+00:00", body="2026-09-29T11:00:00+00:00"):
    return surface.validate_model_surface_witness(proof, model="icon_global",
        selected_latitude=0, selected_longitude=lon,
        body_captured_at=body, decision_at=decision)


def test_actual_om_shape_index_negative_land_and_later_possession_reset(ordinary_static_http):
    entity, clock, _ = ordinary_static_http
    capture = surface.ensure_model_surface("icon_global")
    assert capture.status == "READY"
    proof = _proof(capture)
    assert proof["status"] == "VERIFIED"
    assert proof["geometry"]["grid_profile"]["nx"] == 2879
    assert proof["geometry"]["x"] == 1441
    assert proof["geometry"]["y"] == 720
    assert proof["geometry"]["native_grid_elevation_m"] == -20
    assert proof["geometry"]["native_surface"] == "LAND"
    assert proof["asset_audit"]["whole_sha256"] == hashlib.sha256(entity["body"]).hexdigest()
    # Earlier body + later possession of already-existing static is legal NOW,
    # but cannot be used to re-authorize the earlier decision.
    assert _validate(proof) is None
    assert _validate(proof, decision="2026-09-29T11:01:00+00:00") == "MODEL_SURFACE_NOT_CAUSAL"
    assert surface.read_model_surface_capture("icon_global", decision_at="2026-09-29T11:01:00+00:00").reason == "MODEL_SURFACE_NOT_CAUSAL"
    assert surface.read_model_surface_capture("icon_global", decision_at="2026-09-29T12:01:00+00:00").as_payload() == capture.as_payload()
    assert len(entity["calls"]) == 1  # Reader validation never performs network I/O.


@pytest.mark.parametrize(("lon", "reason"), [(0, "MODEL_SURFACE_SEA"),
    (.25, "MODEL_SURFACE_NO_DATA"), (.375, "MODEL_SURFACE_NO_HEIGHT")])
def test_actual_om_sentinels_are_not_height_based_land(ordinary_static_http, lon, reason):
    capture = surface.ensure_model_surface("icon_global")
    proof = _proof(capture, lon=lon)
    assert proof["status"] == "UNPROVEN"
    assert proof["reason"] == reason
    assert _validate(proof, lon=lon) == reason


def test_same_object_304_preserves_capture_and_republished_same_sha_is_new_version(ordinary_static_http):
    entity, clock, _ = ordinary_static_http
    first = surface.ensure_model_surface("icon_global")
    clock[0] += timedelta(minutes=2)
    cached = surface.ensure_model_surface("icon_global")
    assert cached.as_payload() == first.as_payload()
    entity["force_200"] = True
    same_200 = surface.ensure_model_surface("icon_global")
    assert same_200.as_payload() == first.as_payload()
    assert len(list(Path(first.asset["manifest_path"]).parent.glob("*.manifest.json"))) == 1
    entity["force_200"] = False
    entity["etag"] = '"republished"'
    entity["last_modified"] = "Tue, 29 Sep 2026 12:01:00 GMT"
    later = surface.ensure_model_surface("icon_global")
    assert later.status == "READY"
    assert later.asset["whole_sha256"] == first.asset["whole_sha256"]
    assert later.asset["manifest_path"] != first.asset["manifest_path"]
    assert json.loads(Path(first.asset["manifest_path"]).read_text())["captured_at"] == first.asset["captured_at"]
    assert _proof(later)["reason"] == "MODEL_SURFACE_EPOCH_MISMATCH"
    # A normal same-issued forecast recapture after republish restores proof.
    recaptured_at = "2026-09-29T12:03:00+00:00"
    proof = _proof(later, body_at=recaptured_at)
    assert _validate(proof, body=recaptured_at, decision="2026-09-29T12:04:00+00:00") is None
    assert surface.model_surface_stable_projection(proof) == surface.model_surface_stable_projection(_proof(first))


def test_selected_cell_roundtrip_and_model_identity_fail_closed(ordinary_static_http):
    capture = surface.ensure_model_surface("icon_global")
    assert _proof(capture, lon=.126)["reason"] == "MODEL_SURFACE_SELECTED_CELL_MISMATCH"
    proof = _proof(capture)
    assert surface.validate_model_surface_witness(proof, model="icon_eu", selected_latitude=0,
        selected_longitude=.125, body_captured_at="2026-09-29T11:00:00+00:00",
        decision_at="2026-09-29T12:01:00+00:00") == "MODEL_SURFACE_MODEL_MISMATCH"
    for model in ("icon_seamless", "ukmo_seamless", "kma_gdps", "unknown"):
        assert surface.ensure_model_surface(model).reason == "MODEL_SURFACE_UNSUPPORTED"


@pytest.mark.parametrize("tamper", ["bytes", "manifest", "geometry", "path", "symlink"])
def test_local_reader_rejects_tampered_dependencies_without_network(ordinary_static_http, tamper):
    entity, _, tmp_path = ordinary_static_http
    capture = surface.ensure_model_surface("icon_global")
    proof = _proof(capture)
    if tamper == "bytes":
        Path(capture.asset["asset_path"]).write_bytes(b"not the captured OM entity")
    elif tamper == "manifest":
        path = Path(capture.asset["manifest_path"])
        content = json.loads(path.read_text())
        content["captured_at"] = "2026-09-28T00:00:00+00:00"
        path.write_text(json.dumps(content))
    elif tamper == "geometry":
        proof["geometry"]["native_grid_elevation_m"] = -19
    elif tamper == "path":
        proof["asset_audit"]["asset_path"] = str(tmp_path / ".." / "outside.om")
    else:
        path = Path(capture.asset["asset_path"])
        original = tmp_path / "symlink-target.om"
        path.rename(original)
        path.symlink_to(original)
    assert _validate(proof) is not None
    assert len(entity["calls"]) == 1


@pytest.mark.parametrize(("model", "shape", "coords", "xy", "domain"), [
    ("icon_global", (1441, 2879), (31.125, 121.5), (2412, 969), "dwd_icon"),
    ("icon_eu", (657, 1377), (35.75, 7.75), (500, 100), "dwd_icon_eu"),
    ("icon_d2", (746, 1215), (45.18, 6.06), (500, 100), "dwd_icon_d2"),
    ("ukmo_global_deterministic_10km", (1920, 2560), (31.125, 121.5), (2144, 1292), "ukmo_global_deterministic_10km"),
])
def test_each_explicit_domain_decodes_its_own_real_om_cell(ordinary_static_http, model, shape, coords, xy, domain):
    entity, _, tmp_path = ordinary_static_http
    data = np.full(shape, -999, dtype=np.float32)
    data[xy[1], xy[0]] = 17
    path = tmp_path / "model-shaped.om"
    writer = OmFileWriter(str(path))
    root = writer.write_array(data, chunks=(20, 20), name="HSURF")
    writer.close(root)
    entity["body"] = path.read_bytes()
    capture = surface.ensure_model_surface(model)
    assert capture.status == "READY"
    proof = surface.model_surface_witness(model, selected_latitude=coords[0], selected_longitude=coords[1],
        body_captured_at="2026-09-29T11:00:00+00:00", asset_capture=capture)
    assert proof["status"] == "VERIFIED"
    assert proof["geometry"]["domain"] == domain
    assert (proof["geometry"]["x"], proof["geometry"]["y"]) == xy
    assert proof["geometry"]["native_grid_elevation_m"] == 17
    assert surface.validate_model_surface_witness(proof, model=model, selected_latitude=coords[0], selected_longitude=coords[1],
        body_captured_at="2026-09-29T11:00:00+00:00", decision_at="2026-09-29T12:01:00+00:00") is None


@pytest.mark.parametrize("damage", ["manifest", "missing_manifest", "bytes", "missing_bytes"])
def test_normal_full_get_restores_dependencies_without_reauthorizing_old_cut(ordinary_static_http, damage):
    entity, clock, tmp_path = ordinary_static_http
    first = surface.ensure_model_surface("icon_global")
    old_proof = _proof(first)
    manifest_path = Path(first.asset["manifest_path"])
    asset_path = Path(first.asset["asset_path"])
    original_manifest = manifest_path.read_bytes()
    if damage == "manifest":
        changed = json.loads(original_manifest)
        changed["captured_at"] = "2026-09-28T01:00:00+00:00"
        # Still causal: only independent filename wholeSHA proves corruption.
        manifest_path.write_bytes(surface._json(changed))
    elif damage == "missing_manifest":
        manifest_path.unlink()
    elif damage == "bytes":
        asset_path.write_bytes(b"damaged original entity")
    else:
        asset_path.unlink()
    assert _validate(old_proof) is not None
    clock[0] += timedelta(minutes=2)
    restored = surface.ensure_model_surface("icon_global")
    assert restored.status == "READY"
    assert "If-None-Match" not in entity["calls"][-1]  # invalid evidence requires real full200
    new_proof = _proof(restored)
    assert _validate(new_proof, decision="2026-09-29T12:03:00+00:00") is None
    if damage in ("manifest", "missing_manifest"):
        assert restored.asset["manifest_path"] != first.asset["manifest_path"]
        assert restored.asset["captured_at"] == clock[0].isoformat()
        assert _validate(old_proof) is not None
        assert _validate(new_proof) == "MODEL_SURFACE_NOT_CAUSAL"
        assert restored.asset["reacquisition"]["kind"] == "EVIDENCE_REACQUIRED"
        if damage == "manifest":
            bad = restored.asset["reacquisition"]["recovery_of_expected_manifest_identity"]["invalid_references"][0]
            assert bad["expected_manifest_sha256"] == first.asset["manifest_sha256"]
            assert bad["invalid_manifest_bytes_sha256"] != bad["expected_manifest_sha256"]
            assert not manifest_path.exists()
    else:
        assert manifest_path.read_bytes() == original_manifest
        assert restored.as_payload() == first.as_payload()
        assert _validate(old_proof) is None  # exact bytes restored, clocks unchanged
        if damage == "bytes":
            assert list((tmp_path / "static").glob("*.om.corrupt.*"))
    assert surface.ensure_model_surface("icon_global").as_payload() == restored.as_payload()


def test_actual_object_a_b_a_transition_has_new_capture_not_old_a_clock(ordinary_static_http):
    entity, clock, tmp_path = ordinary_static_http
    first = surface.ensure_model_surface("icon_global")
    a_body = entity["body"]
    data = np.full((1441, 2879), 6, dtype=np.float32)
    data[720, 1441] = -21
    path = tmp_path / "second-object.om"
    writer = OmFileWriter(str(path))
    root = writer.write_array(data, chunks=(20, 20), name="HSURF")
    writer.close(root)
    clock[0] += timedelta(minutes=2)
    entity.update(body=path.read_bytes(), etag='"second"', last_modified="Tue, 29 Sep 2026 12:01:00 GMT")
    second = surface.ensure_model_surface("icon_global")
    assert second.status == "READY"
    clock[0] += timedelta(minutes=2)
    entity.update(body=a_body, etag='"first"', last_modified="Mon, 28 Sep 2026 00:00:00 GMT")
    third = surface.ensure_model_surface("icon_global")
    assert third.status == "READY"
    assert third.asset["whole_sha256"] == first.asset["whole_sha256"]
    assert third.asset["manifest_path"] != first.asset["manifest_path"]
    assert third.asset["captured_at"] == clock[0].isoformat()
    assert third.asset["reacquisition"]["kind"] == "OBJECT_TRANSITION"
    assert _validate(_proof(third), decision="2026-09-29T12:03:00+00:00") == "MODEL_SURFACE_NOT_CAUSAL"
    assert surface.ensure_model_surface("icon_global").as_payload() == third.as_payload()
    assert surface.read_model_surface_capture("icon_global", decision_at="2026-09-29T12:03:00+00:00").as_payload() == second.as_payload()
    assert surface.read_model_surface_capture("icon_global", decision_at="2026-09-29T12:05:00+00:00").as_payload() == third.as_payload()


def test_wrong_actual_shape_and_deadline_are_not_cached_as_authority(ordinary_static_http):
    import time
    entity, _, tmp_path = ordinary_static_http
    data = np.zeros((2, 3), dtype=np.float32)
    path = tmp_path / "wrong-shape.om"
    writer = OmFileWriter(str(path))
    root = writer.write_array(data, chunks=(2, 3), name="HSURF")
    writer.close(root)
    entity["body"] = path.read_bytes()
    assert surface.ensure_model_surface("icon_global").reason == "MODEL_SURFACE_GRID_SHAPE_MISMATCH"
    assert not list((tmp_path / "static").glob("*.manifest.json"))
    before = len(entity["calls"])
    assert surface.ensure_model_surface("icon_global", deadline=time.monotonic()-1).reason == "MODEL_SURFACE_DEADLINE"
    assert len(entity["calls"]) == before


def test_known_bad_latest_manifest_does_not_fall_back_to_older_capture(ordinary_static_http):
    entity, clock, _ = ordinary_static_http
    old = surface.ensure_model_surface("icon_global")
    clock[0] += timedelta(minutes=2)
    entity.update(etag='"new-version"', last_modified="Tue, 29 Sep 2026 12:01:00 GMT")
    newer = surface.ensure_model_surface("icon_global")
    path = Path(newer.asset["manifest_path"])
    damaged = json.loads(path.read_bytes())
    damaged["captured_at"] = "2026-09-29T12:01:30+00:00"
    path.write_bytes(surface._json(damaged))
    reader = surface.read_model_surface_capture("icon_global", decision_at="2026-09-29T12:03:00+00:00")
    assert reader.reason == "MODEL_SURFACE_MANIFEST_INVALID"
    assert reader.asset is None
    clock[0] += timedelta(minutes=2)
    restored = surface.ensure_model_surface("icon_global")
    assert restored.status == "READY"
    assert restored.asset["manifest_path"] not in (old.asset["manifest_path"], newer.asset["manifest_path"])
    assert "If-None-Match" not in entity["calls"][-1]
    assert surface.read_model_surface_capture("icon_global", decision_at="2026-09-29T12:05:00+00:00").as_payload() == restored.as_payload()
    assert not path.exists()  # Quarantined damaged evidence does not freeze RESET.


@pytest.mark.parametrize(("field", "value", "reason"), [
    ("etag", "", "MODEL_SURFACE_MANIFEST_INVALID"),
    ("last_modified", "", "MODEL_SURFACE_INVALID_CLOCK"),
    ("last_modified", "Wed, 30 Sep 2026 00:00:00 GMT", "MODEL_SURFACE_NOT_CAUSAL"),
    ("declared_size", 8*1024*1024+1, "MODEL_SURFACE_INVALID_SIZE"),
])
def test_entity_headers_epoch_and_eight_mib_bound_are_fail_closed(ordinary_static_http, field, value, reason):
    entity, _, tmp_path = ordinary_static_http
    entity[field] = value
    assert surface.ensure_model_surface("icon_global").reason == reason
    assert not list((tmp_path / "static").glob("*.manifest.json"))


def test_read_only_missing_asset_does_not_fetch_or_create_cache(ordinary_static_http):
    entity, _, tmp_path = ordinary_static_http
    result = surface.read_model_surface_capture("icon_global", decision_at="2026-09-29T12:01:00+00:00")
    assert result.reason == "MODEL_SURFACE_ASSET_MISSING"
    assert not entity["calls"]
    assert not (tmp_path / "static").exists()


@pytest.mark.parametrize("changed_epoch", [False, True])
def test_304_without_exact_epoch_identity_requires_full_entity_get(ordinary_static_http, changed_epoch):
    entity, clock, _ = ordinary_static_http
    first = surface.ensure_model_surface("icon_global")
    clock[0] += timedelta(minutes=2)
    if changed_epoch:
        # Normal S3 samebytes republish: ETag stays unchanged, LM changes.
        entity["last_modified"] = "Tue, 29 Sep 2026 12:01:00 GMT"
    else:
        entity["omit_304_headers"] = True
    next_capture = surface.ensure_model_surface("icon_global")
    assert next_capture.status == "READY"
    assert len(entity["calls"]) == 3  # first200, conditional304, unconditional200
    assert "If-None-Match" not in entity["calls"][-1]
    assert next_capture.asset["whole_sha256"] == first.asset["whole_sha256"]
    if changed_epoch:
        assert next_capture.asset["manifest_path"] != first.asset["manifest_path"]
        assert next_capture.asset["captured_at"] == clock[0].isoformat()
        assert _proof(next_capture)["reason"] == "MODEL_SURFACE_EPOCH_MISMATCH"
    else:
        assert next_capture.as_payload() == first.as_payload()
