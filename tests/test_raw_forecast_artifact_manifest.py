# Lifecycle: created=2026-06-18; last_reviewed=2026-07-28; last_reused=2026-07-28
# Purpose: Reject raw manifest schema aliases so retired authority fields cannot execute.
# Reuse: pytest tests/test_raw_forecast_artifact_manifest.py
# Authority basis: replacement live/experiment separation incident 2026-06-18.

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

import src.data.raw_forecast_artifact_manifest as manifest_module

from src.data.openmeteo_ecmwf_ifs9_anchor import HIGH_DATA_VERSION, LOW_DATA_VERSION, PRODUCT_ID, SOURCE_ID
from src.data.raw_forecast_artifact_manifest import (
    RawForecastArtifactManifest,
    UnsupportedRawForecastArtifactManifestFieldsError,
    read_manifest,
    write_manifest,
    write_manifest_to_db,
)
from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema


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
