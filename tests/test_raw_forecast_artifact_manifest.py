# Lifecycle: created=2026-06-18; last_reviewed=2026-06-18; last_reused=2026-06-18
# Purpose: Protect raw manifest schema compatibility without letting retired authority fields execute.
# Reuse: pytest tests/test_raw_forecast_artifact_manifest.py
# Authority basis: replacement live/experiment separation incident 2026-06-18.

from __future__ import annotations

import json

import pytest

from src.data.openmeteo_ecmwf_ifs9_anchor import HIGH_DATA_VERSION, PRODUCT_ID, SOURCE_ID
from src.data.raw_forecast_artifact_manifest import (
    RawForecastArtifactManifest,
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


def test_read_manifest_drops_retired_trade_authority_status(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    write_manifest(_manifest(tmp_path), path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["trade_authority_status"] = "SHADOW_ONLY"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = read_manifest(path)

    assert loaded.source_id == SOURCE_ID
    assert "trade_authority_status" not in loaded.to_dict()


def test_read_manifest_rejects_unknown_top_level_fields(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    write_manifest(_manifest(tmp_path), path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["unknown_authority_alias"] = "LIVE_AUTHORITY"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported fields"):
        read_manifest(path)
