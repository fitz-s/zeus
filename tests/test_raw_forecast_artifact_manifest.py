# Lifecycle: created=2026-06-18; last_reviewed=2026-07-28; last_reused=2026-07-28
# Purpose: Reject raw manifest schema aliases so retired authority fields cannot execute.
# Reuse: pytest tests/test_raw_forecast_artifact_manifest.py
# Authority basis: replacement live/experiment separation incident 2026-06-18.

from __future__ import annotations

import json
from dataclasses import replace

import pytest

import src.data.raw_forecast_artifact_manifest as manifest_module

from src.data.openmeteo_ecmwf_ifs9_anchor import HIGH_DATA_VERSION, PRODUCT_ID, SOURCE_ID
from src.data.raw_forecast_artifact_manifest import (
    RawForecastArtifactManifest,
    UnsupportedRawForecastArtifactManifestFieldsError,
    read_manifest,
    write_manifest,
)


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
