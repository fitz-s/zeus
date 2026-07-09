# Lifecycle: created=2026-07-08; last_reviewed=2026-07-08; last_reused=2026-07-08
# Purpose: A raw artifact rewritten with a benign trailing-newline (the "\n" _write_json
#   appends, added 2026-06-24 by e2cd7a9bc) AFTER its manifest was pinned makes
#   verify_artifact hard-fail on byte_size/sha drift, which aborted ALL current-target
#   materialization (posterior blackout 2026-07-08). repin_manifest_from_file rebuilds
#   byte_size+sha from the current bytes so a present, valid artifact self-heals.
# Reuse: pytest tests/test_forecast_manifest_repin.py
# Authority basis: forecast posterior blackout 2026-07-08 (manifest byte_size desync).

from __future__ import annotations

import json

import pytest

from src.data.openmeteo_ecmwf_ifs9_anchor import HIGH_DATA_VERSION, PRODUCT_ID, SOURCE_ID
from src.data.raw_forecast_artifact_manifest import (
    RawForecastArtifactManifest,
    manifest_matches_artifact,
    repin_manifest_from_file,
)


def _pin(artifact) -> RawForecastArtifactManifest:
    return RawForecastArtifactManifest.from_file(
        artifact,
        source_id=SOURCE_ID,
        product_id=PRODUCT_ID,
        data_version=HIGH_DATA_VERSION,
        source_cycle_time="2026-07-07T18:00:00+00:00",
        source_available_at="2026-07-08T08:00:00+00:00",
        captured_at="2026-07-08T08:05:00+00:00",
        request_url="https://example.invalid/openmeteo",
        request_params={"city": "Manila"},
        product_metadata={"city": "Manila", "target_date": "2026-07-10", "metric": "high"},
    )


def test_trailing_newline_drift_hard_fails_then_repin_heals(tmp_path) -> None:
    artifact = tmp_path / "openmeteo_Manila_2026-07-10_high.json"
    # Body as the anchor payload is composed, WITHOUT the trailing newline (this is the
    # exact preimage of a manifest pinned before the "\n" was appended to the file).
    body = json.dumps({"hourly": {"time": ["2026-07-10T00:00"], "t": [30.0]}}, indent=2, sort_keys=True)
    artifact.write_text(body, encoding="utf-8")

    manifest = _pin(artifact)
    pinned_size = manifest.byte_size
    manifest.verify_artifact()  # matches - no raise
    assert manifest_matches_artifact(manifest) is True

    # _write_json rewrites the SAME payload with a trailing "\n" (1 byte larger, new sha).
    artifact.write_text(body + "\n", encoding="utf-8")
    assert artifact.stat().st_size == pinned_size + 1

    # Today: the stale manifest hard-fails verification -> materialization aborts.
    assert manifest_matches_artifact(manifest) is False
    with pytest.raises(ValueError, match="byte_size mismatch"):
        manifest.verify_artifact()

    # The guard: re-pin from the CURRENT bytes -> heals, preserving every other field.
    repinned = repin_manifest_from_file(manifest)
    assert repinned.byte_size == pinned_size + 1
    repinned.verify_artifact()  # no raise
    assert manifest_matches_artifact(repinned) is True
    assert repinned.source_id == manifest.source_id
    assert repinned.product_id == manifest.product_id
    assert repinned.data_version == manifest.data_version
    assert repinned.artifact_path == manifest.artifact_path
    assert repinned.captured_at == manifest.captured_at
    assert dict(repinned.product_metadata) == dict(manifest.product_metadata)
    assert repinned.sha256 != manifest.sha256  # sha re-pinned too, not just size


def test_repin_missing_artifact_raises(tmp_path) -> None:
    artifact = tmp_path / "gone.json"
    artifact.write_text(json.dumps({"ok": True}), encoding="utf-8")
    manifest = _pin(artifact)
    artifact.unlink()
    with pytest.raises(FileNotFoundError):
        repin_manifest_from_file(manifest)


def test_matches_artifact_false_when_missing(tmp_path) -> None:
    artifact = tmp_path / "gone.json"
    artifact.write_text(json.dumps({"ok": True}), encoding="utf-8")
    manifest = _pin(artifact)
    artifact.unlink()
    assert manifest_matches_artifact(manifest) is False
