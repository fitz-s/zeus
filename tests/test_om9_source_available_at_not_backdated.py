from datetime import datetime, timezone

from src.data.openmeteo_ecmwf_ifs9_anchor import build_anchor_request, build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest


def test_om9_manifest_source_available_at_defaults_to_captured_at(tmp_path) -> None:
    artifact = tmp_path / "om9.json"
    artifact.write_text("{}", encoding="utf-8")
    request = build_anchor_request(
        latitude=51.5,
        longitude=-0.1,
        run=datetime(2026, 6, 6, 0, tzinfo=timezone.utc),
        timezone_name="Europe/London",
    )

    manifest = build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest(
        artifact,
        request=request,
        metric="high",
        source_available_at=datetime(2026, 6, 6, 8, tzinfo=timezone.utc),
        captured_at=datetime(2026, 6, 6, 7, tzinfo=timezone.utc),
    )

    assert manifest.source_available_at == datetime(2026, 6, 6, 7, tzinfo=timezone.utc)
    assert manifest.product_metadata["requested_source_available_at_role"] == "diagnostic_not_authority"
