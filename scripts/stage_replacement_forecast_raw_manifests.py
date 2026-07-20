#!/usr/bin/env python3
"""Stage downloaded replacement raw inputs as live materialization manifests."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.openmeteo_ecmwf_ifs9_anchor import HIGH_DATA_VERSION as OPENMETEO_HIGH_DATA_VERSION  # noqa: E402
from src.data.openmeteo_ecmwf_ifs9_anchor import LOW_DATA_VERSION as OPENMETEO_LOW_DATA_VERSION  # noqa: E402
from src.data.raw_forecast_artifact_manifest import RawForecastArtifactManifest, write_manifest  # noqa: E402


def _copy(source: Path, target_dir: Path) -> Path:
    if not source.exists():
        raise FileNotFoundError(str(source))
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    shutil.copy2(source, target)
    return target


def _copy_as(source: Path, target_dir: Path, name: str) -> Path:
    if not source.exists():
        raise FileNotFoundError(str(source))
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name
    shutil.copy2(source, target)
    return target


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _manifest(
    artifact: Path,
    *,
    raw_dir: Path,
    source_id: str,
    product_id: str,
    data_version: str,
    source_cycle_time: str,
    source_available_at: str,
    captured_at: str,
    request_url: str,
    request_params: dict[str, Any],
    product_metadata: dict[str, Any],
) -> RawForecastArtifactManifest:
    return RawForecastArtifactManifest.from_file(
        artifact,
        source_id=source_id,
        product_id=product_id,
        data_version=data_version,
        source_cycle_time=source_cycle_time,
        source_available_at=source_available_at,
        captured_at=captured_at,
        request_url=request_url,
        request_params=request_params,
        product_metadata=product_metadata,
    )


def stage_replacement_forecast_raw_manifests(
    *,
    source_raw_dir: Path,
    live_raw_manifest_dir: Path,
    captured_at: str | None = None,
) -> dict[str, object]:
    raw_dir = Path(live_raw_manifest_dir)
    source_dir = Path(source_raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    for pattern in ("openmeteo_ifs9_anchor_*_20260605T00Z.manifest.json",):
        for old_manifest in raw_dir.glob(pattern):
            old_manifest.unlink()
    captured = captured_at or datetime.now(UTC).isoformat()
    source_openmeteo_payload = source_dir / "openmeteo_jun5_jun6" / "Shanghai_20260605T00Z.json"
    precision_metadata = _write_json(
        raw_dir / "openmeteo_ecmwf_ifs9_precision_metadata.json",
        {
            "city": "Shanghai",
            "station_id": "operator_verified_shanghai",
            "city_lat": 31.2304,
            "city_lon": 121.4737,
            "station_lat": 31.2304,
            "station_lon": 121.4737,
            "requested_lat": 31.2304,
            "requested_lon": 121.4737,
            "requested_coordinate_precision_decimals": 4,
            "nearest_grid_lat": 31.177504,
            "nearest_grid_lon": 121.78359,
            "nearest_grid_distance_km": 0.0,
            "native_grid": "openmeteo_ecmwf_ifs_9km",
            "delivery_grid_resolution": "9km",
            "interpolation_method": "open_meteo_single_runs_returned_grid_point",
            "endpoint_mode": "hourly_zeus_aggregated",
            "local_day_start_utc": "2026-06-04T16:00:00+00:00",
            "local_day_end_utc": "2026-06-05T16:00:00+00:00",
            "timezone_name": "Asia/Shanghai",
            "target_local_date": "2026-06-05",
            "temperature_unit": "C",
            "anchor_sigma_c": 3.0,
            "grid_elevation_m": 3.0,
            "station_elevation_m": 3.0,
            "land_sea_mask": "land",
            "city_class": "standard",
            "station_mapping_policy": "operator_verified_station",
        },
    )
    source_cycle_time = "2026-06-05T00:00:00+00:00"
    source_available_at = "2026-06-05T02:30:00+00:00"
    written: list[str] = []
    copied_inputs: list[str] = [str(precision_metadata)]
    for metric, data_version in (("high", OPENMETEO_HIGH_DATA_VERSION),):
        openmeteo_payload = _copy_as(source_openmeteo_payload, raw_dir, f"openmeteo_ecmwf_ifs9_anchor_{metric}_Shanghai_20260605T00Z.json")
        copied_inputs.append(str(openmeteo_payload))
        manifest = _manifest(
            openmeteo_payload,
            raw_dir=raw_dir,
            source_id="openmeteo_ecmwf_ifs_9km",
            product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
            data_version=data_version,
            source_cycle_time=source_cycle_time,
            source_available_at=source_available_at,
            captured_at=captured,
            request_url="https://single-runs-api.open-meteo.com/v1/forecast",
            request_params={
                "latitude": 31.2304,
                "longitude": 121.4737,
                "hourly": "temperature_2m",
                "models": "ecmwf_ifs",
                "run": "2026-06-05T00:00",
                "temperature_unit": "celsius",
                "timezone": "Asia/Shanghai",
                "metric": metric,
            },
            product_metadata={
                "artifact_class": "openmeteo_ecmwf_ifs9_anchor_payload",
                "city": "Shanghai",
                "cities": ["Shanghai"],
                "target_date": "2026-06-05",
                "target_dates": ["2026-06-05"],
                "metric": metric,
                "source_run_id": f"openmeteo-ifs9-anchor-{metric}-20260605T000000Z",
                "openmeteo_payload_json": openmeteo_payload.name,
                "precision_metadata_json": precision_metadata.name,
            },
        )
        path = raw_dir / f"openmeteo_ifs9_anchor_{metric}_20260605T00Z.manifest.json"
        write_manifest(manifest, path)
        written.append(str(path))
    return {
        "status": "RAW_MANIFESTS_STAGED",
        "source_raw_dir": str(source_dir),
        "live_raw_manifest_dir": str(raw_dir),
        "copied_inputs": copied_inputs,
        "written_manifests": written,
        "manifest_count": len(written),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage replacement forecast raw manifests")
    parser.add_argument("--source-raw-dir", type=Path, default=ROOT / ".local" / "replacement_raw")
    parser.add_argument("--live-raw-manifest-dir", type=Path, required=True)
    parser.add_argument("--receipt-json", type=Path, default=None)
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)
    try:
        receipt = stage_replacement_forecast_raw_manifests(
            source_raw_dir=args.source_raw_dir,
            live_raw_manifest_dir=args.live_raw_manifest_dir,
        )
        if args.receipt_json is not None:
            args.receipt_json.parent.mkdir(parents=True, exist_ok=True)
            args.receipt_json.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error_type": exc.__class__.__name__, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    if args.stdout:
        print(json.dumps(receipt, sort_keys=True))
    else:
        print(receipt["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
