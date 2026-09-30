# Lifecycle: created=2026-06-18; last_reviewed=2026-09-30; last_reused=2026-09-30
# Created: 2026-06-18
# Last reused/audited: 2026-09-30
# Purpose: Immutable body manifests and scoped, causal append-only local proof dependencies.
# Reuse: pytest tests/test_raw_forecast_artifact_manifest.py
# Authority basis: replacement live/experiment separation + approved finite_evidence_probability_symmetry local-proof slice.

from __future__ import annotations

import json
import errno
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.data.raw_forecast_artifact_manifest as manifest_module

from src.data.openmeteo_ecmwf_ifs9_anchor import HIGH_DATA_VERSION, LOW_DATA_VERSION, PRODUCT_ID, SINGLE_RUNS_FORECAST_URL, SOURCE_ID
from src.data.raw_forecast_artifact_manifest import (
    RawForecastArtifactManifest,
    UnsupportedRawForecastArtifactManifestFieldsError,
    read_manifest,
    write_manifest,
    write_manifest_to_db,
)
from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema


@pytest.fixture(autouse=True)
def _no_external_side_effects(monkeypatch):
    import requests
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError("local proof must not perform HTTP, venue, or subprocess I/O")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def _manifest(tmp_path):
    artifact = tmp_path / "payload.json"
    artifact.write_text(json.dumps({"ok": True}), encoding="utf-8")
    return RawForecastArtifactManifest.from_file(
        artifact,
        source_id=SOURCE_ID,
        product_id=PRODUCT_ID,
        data_version=HIGH_DATA_VERSION,
        source_cycle_time="2026-06-18T06:00:00+00:00",
        source_available_at="2026-06-18T08:00:00+00:00",
        captured_at="2026-06-18T08:05:00+00:00",
        request_url="https://example.invalid/openmeteo",
        request_params={"city": "Karachi"},
        product_metadata={"city": "Karachi", "target_date": "2026-06-19"},
    )


def test_read_manifest_rejects_retired_trade_authority_status(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    write_manifest(_manifest(tmp_path), path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["trade_authority_status"] = "BLOCKED"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        UnsupportedRawForecastArtifactManifestFieldsError,
        match="unsupported fields",
    ) as exc_info:
        read_manifest(path)
    assert exc_info.value.fields == {"trade_authority_status"}


def test_read_manifest_rejects_unknown_top_level_fields(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    write_manifest(_manifest(tmp_path), path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["unknown_authority_alias"] = "LIVE_AUTHORITY"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported fields"):
        read_manifest(path)


def test_write_manifest_never_exposes_a_truncated_target_on_replace_failure(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "manifest.json"
    original = _manifest(tmp_path)
    write_manifest(original, path)
    original_bytes = path.read_bytes()

    def fail_replace(source, target) -> None:
        assert target == path
        assert path.read_bytes() == original_bytes
        assert read_manifest(source).request_url == "https://example.invalid/replacement"
        raise OSError("simulated replace failure")

    monkeypatch.setattr(manifest_module.os, "replace", fail_replace)
    replacement = replace(
        original,
        request_url="https://example.invalid/replacement",
    )

    with pytest.raises(OSError, match="simulated replace failure"):
        write_manifest(replacement, path)

    assert path.read_bytes() == original_bytes
    assert tuple(tmp_path.glob("*.tmp")) == ()


@pytest.mark.parametrize("data_version", (HIGH_DATA_VERSION, LOW_DATA_VERSION))
def test_normal_manifest_preparation_preserves_same_body_first_possession(tmp_path, data_version):
    from scripts.materialize_replacement_forecast_live import _prepare_live_schema_and_manifest
    conn = sqlite3.connect(":memory:")
    ensure_replacement_forecast_live_schema(conn)
    conn.commit()
    original = replace(_manifest(tmp_path), data_version=data_version)
    first = _prepare_live_schema_and_manifest(conn, init_schema=False, schema_ready=True,
        openmeteo_manifest=original, anchor_artifact_id=None)
    original_row = conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (first.anchor_artifact_id,)).fetchone()
    later_path = tmp_path / "same-body-different-path.json"
    later_path.write_bytes(Path(original.artifact_path).read_bytes())
    later = replace(original, artifact_path=str(later_path),
        source_available_at="2026-06-18T10:00:00+00:00", captured_at="2026-06-18T10:05:00+00:00",
        product_metadata={"city": "Karachi", "target_date": "2026-06-19", "precision_metadata_json": "later-proof.json"})
    second = _prepare_live_schema_and_manifest(conn, init_schema=False, schema_ready=True,
        openmeteo_manifest=later, anchor_artifact_id=None)
    assert second.anchor_artifact_id == first.anchor_artifact_id
    assert conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (first.anchor_artifact_id,)).fetchone() == original_row
    assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts WHERE source_available_at<=?", ("2026-06-18T09:00:00+00:00",)).fetchone()[0] == 1
    conn.close()


def test_new_raw_bytes_append_without_changing_old_capture(tmp_path):
    conn = sqlite3.connect(":memory:")
    ensure_replacement_forecast_live_schema(conn)
    original = _manifest(tmp_path)
    first_id = write_manifest_to_db(conn, original)
    first_row = conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (first_id,)).fetchone()
    fresh_path = tmp_path / "actual-new-body.json"
    fresh_path.write_text('{"ok":false}\n', encoding="utf-8")
    fresh = RawForecastArtifactManifest.from_file(fresh_path, source_id=original.source_id, product_id=original.product_id,
        data_version=original.data_version, source_cycle_time=original.source_cycle_time,
        source_available_at="2026-06-18T10:00:00+00:00", captured_at="2026-06-18T10:05:00+00:00",
        request_url=original.request_url, request_params=original.request_params, product_metadata=original.product_metadata)
    fresh_id = write_manifest_to_db(conn, fresh)
    assert fresh_id != first_id
    assert conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (first_id,)).fetchone() == first_row
    assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 2
    conn.close()


def _local_proof_case(tmp_path, data_version, *, response_scope_override=None):
    """Real owned bytes/request; this fixture does not claim physical q authority."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    run = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    target = (run + timedelta(days=1)).date().isoformat()
    metric = "high" if data_version == HIGH_DATA_VERSION else "low"
    body = tmp_path / "original-body.json"
    body.write_text(json.dumps({
        "latitude": 24.9, "longitude": 67.1, "elevation": 12.0,
        "timezone": "Asia/Karachi", "utc_offset_seconds": 18000,
        "_zeus_current_target_scope": {"city": "Karachi", "target_date": target, "metric": metric,
                                       **(response_scope_override or {})},
        "hourly_units": {"temperature_2m": "°C"},
        "hourly": {"time": [f"{target}T{hour:02d}:00" for hour in range(24)],
                   "temperature_2m": [20.0 + hour / 10 for hour in range(24)]},
    }), encoding="utf-8")
    original = RawForecastArtifactManifest.from_file(
        body, source_id=SOURCE_ID, product_id=PRODUCT_ID, data_version=data_version,
        source_cycle_time=run, source_available_at=run + timedelta(minutes=5),
        captured_at=run + timedelta(minutes=5),
        request_url=SINGLE_RUNS_FORECAST_URL,
        request_params={"latitude": 24.9, "longitude": 67.1, "timezone": "Asia/Karachi",
                        "models": "ecmwf_ifs", "run": run.strftime("%Y-%m-%dT%H:%M"),
                        "hourly": "temperature_2m", "temperature_unit": "celsius", "cell_selection": "land"},
        product_metadata={"city": "Karachi", "target_date": target, "metric": metric},
    )
    original_id = write_manifest_to_db(conn, original)
    conn.commit()
    current = tmp_path / "current-owned-body.json"
    current.write_bytes(body.read_bytes())
    native = tmp_path / "owned-static.bytes"
    native.write_bytes(b"controlled owned static bytes, not HTTP or native authorization")
    ground = tmp_path / "owned-ground.bytes"
    ground.write_bytes(b"controlled owned ground bytes, not settlement authority")
    precision = {
        "city": "Karachi", "target_local_date": target, "timezone_name": "Asia/Karachi",
        "requested_lat": 24.9, "requested_lon": 67.1,
        "nearest_grid_lat": 24.9, "nearest_grid_lon": 67.1,
        "source_geometry_proof": {
            "raw_payload_sha256": original.sha256,
            "static_asset_audit": {"path": str(native), "sha256": manifest_module.sha256_file(native)},
            "station_ground_proof": {"path": str(ground), "sha256": manifest_module.sha256_file(ground)},
        },
    }
    return conn, original_id, original, replace(original, artifact_path=str(current)), precision


def _read_local(conn, original_id, manifest, decision_at):
    metadata = manifest.product_metadata
    return manifest_module.read_anchor_local_proof(
        conn, original_id, city=metadata["city"], target_date=metadata["target_date"],
        metric=metadata["metric"], decision_at=decision_at,
    )


@pytest.mark.parametrize("data_version", (HIGH_DATA_VERSION, LOW_DATA_VERSION))
def test_explicit_local_proof_new_path_and_precision_only_at_new_cut(tmp_path, data_version):
    conn, original_id, original, candidate, precision = _local_proof_case(tmp_path, data_version)
    before = dict(conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (original_id,)).fetchone())
    # Restoring the old path/default manifest reuse cannot add immutable precision.
    assert write_manifest_to_db(conn, candidate) == original_id
    assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 1
    conn.commit()
    assert _read_local(conn, original_id, original, datetime.now(timezone.utc)) is None
    conn.execute("BEGIN IMMEDIATE")
    proof_id = manifest_module.write_anchor_local_proof(
        conn, original_id, candidate, precision_metadata=precision,
    )
    conn.commit()
    Path(original.artifact_path).unlink()
    resolved = _read_local(conn, original_id, original, datetime.now(timezone.utc))
    assert resolved.proof_artifact_id == proof_id
    assert resolved.owned_body["path"] == candidate.artifact_path
    assert resolved.precision_metadata == precision
    assert resolved.original_body_artifact == before
    assert dict(conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (original_id,)).fetchone()) == before
    old_cut = resolved.local_possessed_at - timedelta(milliseconds=1)
    assert _read_local(conn, original_id, original, old_cut) is None
    row = conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (proof_id,)).fetchone()
    sealed = json.loads(Path(row["artifact_path"]).read_text())
    assert "recorded_at" not in sealed
    assert sealed["clock_resolution"] == "milliseconds"
    assert resolved.local_possessed_at <= resolved.recorded_at
    assert resolved.recorded_at == datetime.fromisoformat(row["recorded_at"])
    assert resolved.local_possessed_at.microsecond % 1000 == 0
    conn.execute("BEGIN IMMEDIATE")
    assert manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision) == proof_id
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 2
    assert _read_local(conn, original_id, original, datetime.now(timezone.utc)).local_possessed_at == resolved.local_possessed_at
    conn.close()


@pytest.mark.parametrize("data_version", (HIGH_DATA_VERSION, LOW_DATA_VERSION))
def test_bad_latest_local_proof_real_repossession_resets_unknown_and_aba(tmp_path, data_version):
    conn, original_id, original, candidate, precision = _local_proof_case(tmp_path, data_version)
    conn.execute("BEGIN IMMEDIATE")
    first = manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    conn.commit()
    assert _read_local(conn, original_id, original, datetime.now(timezone.utc)).proof_artifact_id == first
    conn.execute("UPDATE raw_forecast_artifacts SET recorded_at='unknown' WHERE artifact_id=?", (first,))
    conn.commit()
    with pytest.raises(ValueError, match="anchor_local_proof"):
        _read_local(conn, original_id, original, datetime.now(timezone.utc))
    conn.execute("BEGIN IMMEDIATE")
    second = manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    conn.commit()
    assert second != first
    assert _read_local(conn, original_id, original, datetime.now(timezone.utc)).proof_artifact_id == second
    # Same apparent value A does not erase a later independently observed unknown row.
    conn.execute("""INSERT INTO raw_forecast_artifacts
        (source_id,product_id,data_version,source_cycle_time,source_available_at,captured_at,
         artifact_path,sha256,byte_size,request_url,request_params_json,artifact_metadata_json,recorded_at)
        SELECT source_id,product_id,data_version,source_cycle_time,source_available_at,captured_at,
               artifact_path,?,byte_size,request_url,request_params_json,artifact_metadata_json,'unknown'
        FROM raw_forecast_artifacts WHERE artifact_id=?""", ("f" * 64, second))
    conn.commit()
    with pytest.raises(ValueError, match="anchor_local_proof"):
        _read_local(conn, original_id, original, datetime.now(timezone.utc))
    conn.execute("BEGIN IMMEDIATE")
    third = manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    conn.commit()
    assert third not in {first, second}
    assert _read_local(conn, original_id, original, datetime.now(timezone.utc)).proof_artifact_id == third
    conn.close()


@pytest.mark.parametrize("data_version", (HIGH_DATA_VERSION, LOW_DATA_VERSION))
@pytest.mark.parametrize("fault", (
    "city", "target_date", "metric", "run", "request_url", "request_latitude",
    "request_longitude", "request_timezone", "request_model", "request_cell", "sha", "size", "data_version",
))
def test_local_proof_exact_permission_rejects_foreign_scope_before_file_io(tmp_path, monkeypatch, data_version, fault):
    conn, original_id, original, candidate, precision = _local_proof_case(tmp_path, data_version)
    changed = candidate
    if fault in {"city", "target_date", "metric"}:
        meta = dict(candidate.product_metadata)
        meta[fault] = {"city": "Hong Kong", "target_date": "2099-01-01", "metric": "low" if meta["metric"] == "high" else "high"}[fault]
        changed = replace(candidate, product_metadata=meta)
    elif fault.startswith("request_") and fault != "request_url":
        key = fault.removeprefix("request_")
        key = {"model": "models", "cell": "cell_selection"}.get(key, key)
        params = dict(candidate.request_params)
        params[key] = {"latitude": 24.91, "longitude": 67.11, "timezone": "UTC", "models": "ecmwf_ifs025", "cell_selection": "nearest"}[key]
        changed = replace(candidate, request_params=params)
    elif fault == "run":
        changed = replace(candidate, source_cycle_time=candidate.source_cycle_time - timedelta(hours=6))
    elif fault == "request_url":
        changed = replace(candidate, request_url="https://example.invalid/foreign")
    elif fault == "sha":
        changed = replace(candidate, sha256="0" * 64)
    elif fault == "size":
        changed = replace(candidate, byte_size=candidate.byte_size + 1)
    elif fault == "data_version":
        changed = replace(candidate, data_version=LOW_DATA_VERSION if data_version == HIGH_DATA_VERSION else HIGH_DATA_VERSION)
    conn.execute("BEGIN IMMEDIATE")

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid exact permission reached owned file I/O")

    monkeypatch.setattr(manifest_module, "_proof_path", forbidden)
    with pytest.raises(ValueError, match="original_request_or_scope"):
        manifest_module.write_anchor_local_proof(conn, original_id, changed, precision_metadata=precision)
    assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 1
    conn.rollback()
    conn.close()


@pytest.mark.parametrize("data_version", (HIGH_DATA_VERSION, LOW_DATA_VERSION))
@pytest.mark.parametrize("fault", ("city", "date", "timezone", "requested", "selected", "body_sha", "static_missing", "ground_missing", "nan", "empty"))
def test_local_precision_is_bound_to_original_request_and_owned_response(tmp_path, data_version, fault):
    conn, original_id, original, candidate, precision = _local_proof_case(tmp_path, data_version)
    conn.execute("BEGIN IMMEDIATE")
    good = manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    conn.commit()
    wrong = json.loads(json.dumps(precision))
    if fault == "city": wrong["city"] = "Hong Kong"
    elif fault == "date": wrong["target_local_date"] = "2099-01-01"
    elif fault == "timezone": wrong["timezone_name"] = "UTC"
    elif fault == "requested": wrong["requested_lat"] = 24.91
    elif fault == "selected": wrong["nearest_grid_lon"] = 67.11
    elif fault == "body_sha": wrong["source_geometry_proof"]["raw_payload_sha256"] = "0" * 64
    elif fault == "static_missing": del wrong["source_geometry_proof"]["static_asset_audit"]
    elif fault == "ground_missing": del wrong["source_geometry_proof"]["station_ground_proof"]
    elif fault == "nan": wrong["requested_lat"] = float("nan")
    elif fault == "empty": wrong = {}
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(ValueError, match="anchor_local_proof"):
        manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=wrong)
    conn.rollback()
    assert _read_local(conn, original_id, original, datetime.now(timezone.utc)).proof_artifact_id == good
    assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 2
    conn.close()


@pytest.mark.parametrize("data_version", (HIGH_DATA_VERSION, LOW_DATA_VERSION))
@pytest.mark.parametrize("fault", ("file_missing", "file_changed", "metadata_malformed", "captured_bad", "recorded_bad", "recorded_future", "original_changed", "body_changed"))
def test_latest_local_proof_damage_rejects_instead_of_falling_back(tmp_path, data_version, fault):
    conn, original_id, original, candidate, precision = _local_proof_case(tmp_path, data_version)
    conn.execute("BEGIN IMMEDIATE")
    aid = manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    conn.commit()
    assert _read_local(conn, original_id, original, datetime.now(timezone.utc)).proof_artifact_id == aid
    row = conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (aid,)).fetchone()
    if fault == "file_missing": Path(row["artifact_path"]).unlink()
    elif fault == "file_changed": Path(row["artifact_path"]).write_bytes(b"corrupt")
    elif fault == "metadata_malformed": conn.execute("UPDATE raw_forecast_artifacts SET artifact_metadata_json='broken' WHERE artifact_id=?", (aid,))
    elif fault == "captured_bad": conn.execute("UPDATE raw_forecast_artifacts SET captured_at='unknown' WHERE artifact_id=?", (aid,))
    elif fault == "recorded_bad": conn.execute("UPDATE raw_forecast_artifacts SET recorded_at='unknown' WHERE artifact_id=?", (aid,))
    elif fault == "recorded_future": conn.execute("UPDATE raw_forecast_artifacts SET recorded_at='2099-01-01T00:00:00+00:00' WHERE artifact_id=?", (aid,))
    elif fault == "original_changed": conn.execute("UPDATE raw_forecast_artifacts SET request_url='https://foreign.invalid' WHERE artifact_id=?", (original_id,))
    elif fault == "body_changed": Path(candidate.artifact_path).write_bytes(b"same path different bytes")
    conn.commit()
    with pytest.raises(ValueError, match="anchor_local_proof"):
        _read_local(conn, original_id, original, datetime.now(timezone.utc))
    conn.close()


@pytest.mark.parametrize("data_version", (HIGH_DATA_VERSION, LOW_DATA_VERSION))
def test_local_proof_rolls_back_and_disk_failure_never_reports_progress(tmp_path, monkeypatch, data_version):
    conn, original_id, original, candidate, precision = _local_proof_case(tmp_path, data_version)
    conn.execute("BEGIN IMMEDIATE")

    def enospc(fd):
        raise OSError(errno.ENOSPC, "simulated full private fixture disk")

    with monkeypatch.context() as patch:
        patch.setattr(manifest_module.os, "fsync", enospc)
        with pytest.raises(OSError, match="full private"):
            manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    conn.rollback()
    assert _read_local(conn, original_id, original, datetime.now(timezone.utc)) is None
    assert list(tmp_path.glob("*.tmp")) == []
    conn.execute("BEGIN IMMEDIATE")
    manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    conn.rollback()
    assert _read_local(conn, original_id, original, datetime.now(timezone.utc)) is None
    assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 1
    # A completed but uncommitted local file is never canonical proof.
    assert list(tmp_path.glob("openmeteo_anchor_local_proof_*.json"))
    conn.close()


@pytest.mark.parametrize("data_version", (HIGH_DATA_VERSION, LOW_DATA_VERSION))
def test_local_frontier_changed_during_publish_refuses_insert(tmp_path, monkeypatch, data_version):
    conn, original_id, original, candidate, precision = _local_proof_case(tmp_path, data_version)
    conn.execute("BEGIN IMMEDIATE")
    first = manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    conn.commit()
    conn.execute("UPDATE raw_forecast_artifacts SET recorded_at='unknown' WHERE artifact_id=?", (first,))
    conn.commit()
    publish = manifest_module._write_local_proof_file

    def alter_frontier(path, encoded):
        publish(path, encoded)
        conn.execute("UPDATE raw_forecast_artifacts SET captured_at='changed-after-observation' WHERE artifact_id=?", (first,))

    monkeypatch.setattr(manifest_module, "_write_local_proof_file", alter_frontier)
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(ValueError, match="frontier_changed_before_insert"):
        manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 2
    conn.rollback()
    conn.close()


def test_local_proof_external_transport_fence_is_real():
    import requests
    with pytest.raises(AssertionError, match="must not perform HTTP"):
        requests.get("https://unexpected.invalid/local-proof-test", timeout=0.01)


@pytest.mark.parametrize("data_version", (HIGH_DATA_VERSION, LOW_DATA_VERSION))
@pytest.mark.parametrize("fault", ("source", "product", "run", "run_type", "models", "bool_coordinate", "clock", "clock_order", "future_record"))
def test_invalid_original_artifact_cannot_acquire_local_proof(tmp_path, data_version, fault):
    conn, original_id, original, candidate, precision = _local_proof_case(tmp_path, data_version)
    if fault in {"source", "product"}:
        column = "source_id" if fault == "source" else "product_id"
        conn.execute(f"UPDATE raw_forecast_artifacts SET {column}='foreign' WHERE artifact_id=?", (original_id,))
    elif fault in {"run", "run_type", "models", "bool_coordinate"}:
        params = dict(candidate.request_params)
        params[{"run": "run", "run_type": "run", "models": "models", "bool_coordinate": "latitude"}[fault]] = {"run": "2099-01-01T00:00", "run_type": True, "models": "ecmwf_ifs025", "bool_coordinate": True}[fault]
        conn.execute("UPDATE raw_forecast_artifacts SET request_params_json=? WHERE artifact_id=?", (json.dumps(params), original_id))
    elif fault == "clock":
        conn.execute("UPDATE raw_forecast_artifacts SET captured_at='unknown' WHERE artifact_id=?", (original_id,))
    elif fault == "clock_order":
        conn.execute("UPDATE raw_forecast_artifacts SET captured_at='2099-01-01T00:00:00+00:00' WHERE artifact_id=?", (original_id,))
    elif fault == "future_record":
        conn.execute("UPDATE raw_forecast_artifacts SET recorded_at='2099-01-01T00:00:00+00:00' WHERE artifact_id=?", (original_id,))
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(ValueError, match="anchor_local_proof"):
        manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 1
    conn.rollback()
    conn.close()


@pytest.mark.parametrize("data_version", (HIGH_DATA_VERSION, LOW_DATA_VERSION))
def test_restored_old_path_still_needs_new_precision_dependency(tmp_path, data_version):
    conn, original_id, original, candidate, precision = _local_proof_case(tmp_path, data_version)
    original_bytes = Path(original.artifact_path).read_bytes()
    Path(original.artifact_path).unlink()
    assert _read_local(conn, original_id, original, datetime.now(timezone.utc)) is None
    Path(original.artifact_path).write_bytes(original_bytes)
    assert write_manifest_to_db(conn, original) == original_id
    conn.commit()
    assert _read_local(conn, original_id, original, datetime.now(timezone.utc)) is None
    assert "precision_metadata_json" not in json.loads(conn.execute("SELECT artifact_metadata_json FROM raw_forecast_artifacts WHERE artifact_id=?", (original_id,)).fetchone()[0])
    conn.execute("BEGIN IMMEDIATE")
    proof_id = manifest_module.write_anchor_local_proof(conn, original_id, original, precision_metadata=precision)
    conn.commit()
    result = _read_local(conn, original_id, original, datetime.now(timezone.utc))
    assert result.proof_artifact_id == proof_id
    assert result.owned_body["path"] == original.artifact_path
    conn.close()


@pytest.mark.parametrize("data_version", (HIGH_DATA_VERSION, LOW_DATA_VERSION))
def test_neighbor_id_twelve_unknown_proof_does_not_poison_id_one(tmp_path, data_version):
    conn, original_id, original, candidate, precision = _local_proof_case(tmp_path, data_version)
    assert original_id == 1
    conn.execute("BEGIN IMMEDIATE")
    proof_id = manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    conn.commit()
    row = conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (proof_id,)).fetchone()
    metadata = json.loads(row["artifact_metadata_json"])
    metadata.update(original_artifact_id=12, city="Hong Kong")
    conn.execute("""INSERT INTO raw_forecast_artifacts
        (source_id,product_id,data_version,source_cycle_time,source_available_at,captured_at,
         artifact_path,sha256,byte_size,request_url,request_params_json,artifact_metadata_json,recorded_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (row["source_id"], row["product_id"], row["data_version"], row["source_cycle_time"],
         "unknown", "unknown", str(tmp_path / "openmeteo_anchor_local_proof_12_foreign.json"),
         "e" * 64, 1, row["request_url"], row["request_params_json"], json.dumps(metadata), "unknown"))
    conn.commit()
    assert _read_local(conn, original_id, original, datetime.now(timezone.utc)).proof_artifact_id == proof_id
    with pytest.raises(ValueError, match="scope_mismatch"):
        manifest_module.read_anchor_local_proof(conn, original_id, city="Hong Kong", target_date=original.product_metadata["target_date"], metric=original.product_metadata["metric"], decision_at=datetime.now(timezone.utc))
    conn.close()


@pytest.mark.parametrize("data_version", (HIGH_DATA_VERSION, LOW_DATA_VERSION))
def test_local_proof_deadline_and_transaction_guards_have_zero_append(tmp_path, data_version):
    import time
    conn, original_id, original, candidate, precision = _local_proof_case(tmp_path, data_version)
    with pytest.raises(ValueError, match="caller_write_transaction_required"):
        manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(TimeoutError, match="deadline_expired"):
        manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision, deadline_monotonic=time.monotonic() - 1)
    with pytest.raises(TimeoutError, match="deadline_expired"):
        manifest_module.read_anchor_local_proof(conn, original_id, **_proof_scope_for_test(original), decision_at=datetime.now(timezone.utc), deadline_monotonic=time.monotonic() - 1)
    assert list(tmp_path.glob("openmeteo_anchor_local_proof_*.json")) == []
    conn.rollback()
    conn.close()


def _proof_scope_for_test(manifest):
    return {key: manifest.product_metadata[key] for key in ("city", "target_date", "metric")}


@pytest.mark.parametrize("data_version", (HIGH_DATA_VERSION, LOW_DATA_VERSION))
def test_later_good_local_possession_never_rebinds_old_asof(tmp_path, data_version):
    import time
    conn, original_id, original, candidate, precision = _local_proof_case(tmp_path, data_version)
    conn.execute("BEGIN IMMEDIATE")
    first = manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    conn.commit()
    before = _read_local(conn, original_id, original, datetime.now(timezone.utc))
    time.sleep(0.003)  # Distinguish actual millisecond recording cuts, not mocked source clocks.
    changed = dict(precision, audit_note="new independently possessed precision JSON")
    conn.execute("BEGIN IMMEDIATE")
    second = manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=changed)
    conn.commit()
    assert second != first
    assert _read_local(conn, original_id, original, before.recorded_at).proof_artifact_id == first
    current = _read_local(conn, original_id, original, datetime.now(timezone.utc))
    assert current.proof_artifact_id == second
    assert current.precision_metadata == changed
    assert current.original_body_artifact == before.original_body_artifact
    conn.close()


@pytest.mark.parametrize("data_version", (HIGH_DATA_VERSION, LOW_DATA_VERSION))
def test_original_tuple_mutation_during_publish_refuses_insert(tmp_path, monkeypatch, data_version):
    conn, original_id, original, candidate, precision = _local_proof_case(tmp_path, data_version)
    publish = manifest_module._write_local_proof_file

    def alter_original(path, encoded):
        publish(path, encoded)
        conn.execute("UPDATE raw_forecast_artifacts SET artifact_path='changed-canonical-path' WHERE artifact_id=?", (original_id,))

    monkeypatch.setattr(manifest_module, "_write_local_proof_file", alter_original)
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(ValueError, match="frontier_changed_before_insert"):
        manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 1
    conn.rollback()
    conn.close()


@pytest.mark.parametrize("data_version", (HIGH_DATA_VERSION, LOW_DATA_VERSION))
def test_local_proof_relative_path_requires_explicit_root_and_preserves_tuple(tmp_path, data_version):
    conn, original_id, original, candidate, precision = _local_proof_case(tmp_path, data_version)
    relative = replace(candidate, artifact_path="current-owned-body.json")
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(ValueError, match="relative_path_without_root"):
        manifest_module.write_anchor_local_proof(conn, original_id, relative, precision_metadata=precision)
    aid = manifest_module.write_anchor_local_proof(conn, original_id, relative, precision_metadata=precision, root=tmp_path)
    conn.commit()
    assert _read_local(conn, original_id, original, datetime.now(timezone.utc)).proof_artifact_id == aid
    conn.close()


@pytest.mark.parametrize("data_version", (HIGH_DATA_VERSION, LOW_DATA_VERSION))
def test_local_proof_proof_byte_budget_and_plain_row_factory(tmp_path, monkeypatch, data_version):
    conn, original_id, original, candidate, precision = _local_proof_case(tmp_path, data_version)
    conn.row_factory = None
    conn.execute("BEGIN IMMEDIATE")
    with monkeypatch.context() as patch:
        patch.setattr(manifest_module, "_LOCAL_PROOF_MAX_BYTES", 512)
        with pytest.raises(ValueError, match="proof_byte_budget"):
            manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    assert list(tmp_path.glob("openmeteo_anchor_local_proof_*.json")) == []
    aid = manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    conn.commit()
    assert _read_local(conn, original_id, original, datetime.now(timezone.utc)).proof_artifact_id == aid
    conn.close()


@pytest.mark.parametrize("data_version", (HIGH_DATA_VERSION, LOW_DATA_VERSION))
@pytest.mark.parametrize("field", ("city", "target_date", "metric"))
def test_actual_owned_body_declared_scope_cannot_be_rebound(tmp_path, data_version, field):
    # The provider/producer body declaration is independent of a precision self-assertion.
    value = {"city": "Hong Kong", "target_date": "2099-01-01", "metric": "low" if data_version == HIGH_DATA_VERSION else "high"}[field]
    conn, original_id, original, candidate, precision = _local_proof_case(tmp_path, data_version, response_scope_override={field: value})
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(ValueError, match="precision_identity_mismatch"):
        manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 1
    assert list(tmp_path.glob("openmeteo_anchor_local_proof_*.json")) == []
    conn.rollback()
    conn.close()


def _bad_local_proof_prefix(conn, first, count):
    conn.execute("UPDATE raw_forecast_artifacts SET recorded_at='unknown' WHERE artifact_id=?", (first,))
    for index in range(count - 1):
        # Leave real ID gaps to distinguish a prefix commitment from count/maxID.
        conn.execute("""INSERT INTO raw_forecast_artifacts
            (artifact_id,source_id,product_id,data_version,source_cycle_time,source_available_at,captured_at,
             artifact_path,sha256,byte_size,request_url,request_params_json,artifact_metadata_json,recorded_at)
            SELECT ?,source_id,product_id,data_version,source_cycle_time,source_available_at,captured_at,
                   artifact_path,?,byte_size,request_url,request_params_json,artifact_metadata_json,'unknown'
            FROM raw_forecast_artifacts WHERE artifact_id=?""", (first + 2 * (index + 1), f"{index:064x}", first))
    conn.commit()


def _unknown_local_proof_clone(conn, original_proof_id, *, artifact_id=None):
    conn.execute("""INSERT INTO raw_forecast_artifacts
        (artifact_id,source_id,product_id,data_version,source_cycle_time,source_available_at,captured_at,
         artifact_path,sha256,byte_size,request_url,request_params_json,artifact_metadata_json,recorded_at)
        SELECT ?,source_id,product_id,data_version,source_cycle_time,source_available_at,captured_at,
               artifact_path,?,byte_size,request_url,request_params_json,artifact_metadata_json,'unknown'
        FROM raw_forecast_artifacts WHERE artifact_id=?""", (artifact_id, "f" * 64, original_proof_id))
    conn.commit()


@pytest.mark.parametrize("data_version", (HIGH_DATA_VERSION, LOW_DATA_VERSION))
@pytest.mark.parametrize("fault", ("modified_descriptor", "deleted_row", "inserted_gap", "id_aba", "new_unknown"))
def test_more_than_one_frontier_batch_of_bad_proofs_has_normal_reset(tmp_path, data_version, fault):
    import time
    conn, original_id, original, candidate, precision = _local_proof_case(tmp_path, data_version)
    conn.execute("BEGIN IMMEDIATE")
    first = manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    conn.commit()
    assert _read_local(conn, original_id, original, datetime.now(timezone.utc)).proof_artifact_id == first
    _bad_local_proof_prefix(conn, first, 129)
    old_cut = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="anchor_local_proof"):
        _read_local(conn, original_id, original, old_cut)
    time.sleep(0.003)  # Separate actual SQL millisecond cuts, never alter source clocks.
    conn.execute("BEGIN IMMEDIATE")
    repaired = manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    conn.commit()
    result = _read_local(conn, original_id, original, datetime.now(timezone.utc))
    assert result.proof_artifact_id == repaired
    row = conn.execute("SELECT artifact_path FROM raw_forecast_artifacts WHERE artifact_id=?", (repaired,)).fetchone()
    sealed = json.loads(Path(row["artifact_path"]).read_text())
    assert sealed["observed_frontier"]["row_count"] == 129
    assert Path(row["artifact_path"]).stat().st_size < 10_000
    with pytest.raises(ValueError, match="anchor_local_proof"):
        _read_local(conn, original_id, original, old_cut)
    conn.execute("BEGIN IMMEDIATE")
    assert manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision) == repaired
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 131
    assert _read_local(conn, original_id, original, datetime.now(timezone.utc)).recorded_at == result.recorded_at
    if fault == "modified_descriptor":
        conn.execute("UPDATE raw_forecast_artifacts SET captured_at='ABA-descriptor-change' WHERE artifact_id=?", (first,))
    elif fault == "deleted_row":
        conn.execute("DELETE FROM raw_forecast_artifacts WHERE artifact_id=?", (first,))
    elif fault == "inserted_gap":
        _unknown_local_proof_clone(conn, first, artifact_id=first + 1)
    elif fault == "id_aba":
        # Count and maxID stay identical, but the observed ordered IDs do not.
        conn.execute("DELETE FROM raw_forecast_artifacts WHERE artifact_id=?", (first + 2,))
        _unknown_local_proof_clone(conn, first, artifact_id=first + 1)
    else:
        _unknown_local_proof_clone(conn, repaired)
    conn.commit()
    with pytest.raises(ValueError, match="anchor_local_proof"):
        _read_local(conn, original_id, original, datetime.now(timezone.utc))
    conn.close()


@pytest.mark.parametrize("data_version", (HIGH_DATA_VERSION, LOW_DATA_VERSION))
def test_thousand_row_frontier_streams_all_pages_and_reuses_first_clock(tmp_path, monkeypatch, data_version):
    conn, original_id, original, candidate, precision = _local_proof_case(tmp_path, data_version)
    conn.execute("BEGIN IMMEDIATE")
    first = manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    conn.commit()
    original_row = dict(conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (original_id,)).fetchone())
    assert _read_local(conn, original_id, original, datetime.now(timezone.utc)).proof_artifact_id == first
    _bad_local_proof_prefix(conn, first, 1000)
    page_sizes = []
    decode = manifest_module._proof_rows_as_dicts

    def record_page(cursor):
        rows = decode(cursor)
        page_sizes.append(len(rows))
        return rows

    monkeypatch.setattr(manifest_module, "_proof_rows_as_dicts", record_page)
    conn.execute("BEGIN IMMEDIATE")
    repaired = manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    conn.commit()
    result = _read_local(conn, original_id, original, datetime.now(timezone.utc))
    assert result.proof_artifact_id == repaired
    sealed = json.loads(Path(conn.execute("SELECT artifact_path FROM raw_forecast_artifacts WHERE artifact_id=?", (repaired,)).fetchone()[0]).read_text())
    assert sealed["observed_frontier"]["row_count"] == 1000
    assert page_sizes.count(128) >= 7
    assert max(page_sizes) == 128
    assert sealed["observed_frontier"]["max_artifact_id"] == first + 2 * 999
    assert len(json.dumps(sealed).encode()) < 10_000
    conn.execute("BEGIN IMMEDIATE")
    assert manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision) == repaired
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 1002
    assert _read_local(conn, original_id, original, datetime.now(timezone.utc)).recorded_at == result.recorded_at
    assert dict(conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (original_id,)).fetchone()) == original_row
    conn.close()


@pytest.mark.parametrize("data_version", (HIGH_DATA_VERSION, LOW_DATA_VERSION))
@pytest.mark.parametrize("stage", ("observe", "before_insert", "read"))
def test_frontier_deadline_in_second_page_never_returns_partial_commitment(tmp_path, monkeypatch, data_version, stage):
    conn, original_id, original, candidate, precision = _local_proof_case(tmp_path, data_version)
    conn.execute("BEGIN IMMEDIATE")
    first = manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    conn.commit()
    assert _read_local(conn, original_id, original, datetime.now(timezone.utc)).proof_artifact_id == first
    _bad_local_proof_prefix(conn, first, 1000)
    if stage == "read":
        conn.execute("BEGIN IMMEDIATE")
        manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
        conn.commit()
        assert _read_local(conn, original_id, original, datetime.now(timezone.utc)) is not None
    prior_rows = conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0]
    prior_files = set(tmp_path.glob("openmeteo_anchor_local_proof_*.json"))
    state = {"clock": 0.0, "frontier_calls": 0, "pages": 0, "armed": False}
    frontier = manifest_module._proof_frontier

    def observe_frontier(*args, **kwargs):
        state["frontier_calls"] += 1
        state["armed"] = state["frontier_calls"] == (2 if stage == "before_insert" else 1)
        return frontier(*args, **kwargs)

    def expire_during_second_query(sql):
        if state["armed"] and "ORDER BY artifact_id DESC LIMIT" in sql:
            state["pages"] += 1
            if state["pages"] == 2:
                state["clock"] = 10.0

    monkeypatch.setattr(manifest_module, "_proof_frontier", observe_frontier)
    monkeypatch.setattr(manifest_module.time, "monotonic", lambda: state["clock"])
    conn.set_trace_callback(expire_during_second_query)
    try:
        if stage == "read":
            with pytest.raises(TimeoutError, match="deadline_expired"):
                manifest_module.read_anchor_local_proof(conn, original_id, **_proof_scope_for_test(original), decision_at=datetime.now(timezone.utc), deadline_monotonic=5.0)
        else:
            conn.execute("BEGIN IMMEDIATE")
            with pytest.raises(TimeoutError, match="deadline_expired"):
                manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision, deadline_monotonic=5.0)
        assert state["pages"] == 2  # One complete batch was observed, never treated as a full prefix.
        assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == prior_rows
        files = set(tmp_path.glob("openmeteo_anchor_local_proof_*.json"))
        if stage == "before_insert":
            assert len(files - prior_files) == 1  # Uncommitted complete file is not canonical progress.
        else:
            assert files == prior_files
    finally:
        conn.set_trace_callback(None)
        conn.rollback()
        conn.close()


@pytest.mark.parametrize("data_version", (HIGH_DATA_VERSION, LOW_DATA_VERSION))
@pytest.mark.parametrize("fault", ("foreign_url", "foreign_unit", "foreign_cell"))
def test_self_consistent_foreign_original_request_cannot_mint_local_proof(tmp_path, data_version, fault):
    conn, original_id, original, candidate, precision = _local_proof_case(tmp_path, data_version)
    conn.execute("BEGIN IMMEDIATE")
    healthy_id = manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    conn.commit()
    assert _read_local(conn, original_id, original, datetime.now(timezone.utc)).proof_artifact_id == healthy_id
    if fault == "foreign_url":
        candidate = replace(candidate, request_url="https://foreign.invalid/v1/forecast")
        conn.execute("UPDATE raw_forecast_artifacts SET request_url=? WHERE artifact_id=?", (candidate.request_url, original_id))
    else:
        params = dict(candidate.request_params)
        params["temperature_unit" if fault == "foreign_unit" else "cell_selection"] = "fahrenheit" if fault == "foreign_unit" else "nearest"
        candidate = replace(candidate, request_params=params)
        conn.execute("UPDATE raw_forecast_artifacts SET request_params_json=? WHERE artifact_id=?", (json.dumps(params, sort_keys=True), original_id))
    conn.commit()
    files = set(tmp_path.glob("openmeteo_anchor_local_proof_*.json"))
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(ValueError, match="anchor_local_proof"):
        manifest_module.write_anchor_local_proof(conn, original_id, candidate, precision_metadata=precision)
    assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 2
    assert set(tmp_path.glob("openmeteo_anchor_local_proof_*.json")) == files
    conn.rollback()
    with pytest.raises(ValueError, match="anchor_local_proof"):
        _read_local(conn, original_id, original, datetime.now(timezone.utc))
    conn.close()
