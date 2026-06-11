#!/usr/bin/env python3
# Created: 2026-06-07
# Last reused/audited: 2026-06-07
# Lifecycle: created=2026-06-07; last_reviewed=2026-06-07
# Purpose: Download current-target Open-Meteo ECMWF IFS 9km and AIFS ENS raw inputs for replacement forecast materialization.
# Reuse: Run before live replacement materialization when dry-run reports current-target coverage gaps.
# Authority basis: Raw artifacts remain SHADOW_ONLY; live authority still comes from posterior/readiness gates.
"""Download replacement forecast raw inputs for current market targets."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import cities_by_name  # noqa: E402
from src.data.ecmwf_aifs_ens_request import build_aifs_ens_open_data_request, retrieve_aifs_ens_open_data_request  # noqa: E402
from src.data.ecmwf_aifs_sampled_2t_localday import HIGH_DATA_VERSION as AIFS_HIGH_DATA_VERSION  # noqa: E402
from src.data.ecmwf_aifs_sampled_2t_localday import LOW_DATA_VERSION as AIFS_LOW_DATA_VERSION  # noqa: E402
from src.data.openmeteo_ecmwf_ifs9_anchor import (  # noqa: E402
    HIGH_DATA_VERSION as OPENMETEO_HIGH_DATA_VERSION,
    LOW_DATA_VERSION as OPENMETEO_LOW_DATA_VERSION,
    build_anchor_request,
    build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest,
    fetch_openmeteo_ecmwf_ifs9_anchor_payload,
)
from src.data.raw_forecast_artifact_manifest import RawForecastArtifactManifest, write_manifest, write_manifest_to_db  # noqa: E402
from src.data.replacement_forecast_current_target_plan import (  # noqa: E402
    build_replacement_forecast_current_target_plan,
)
from src.state.db import _connect  # noqa: E402
from src.state.schema.v2_schema import ensure_replacement_forecast_shadow_schema  # noqa: E402


METRIC_TO_AIFS_VERSION = {"high": AIFS_HIGH_DATA_VERSION, "low": AIFS_LOW_DATA_VERSION}
METRIC_TO_OPENMETEO_VERSION = {"high": OPENMETEO_HIGH_DATA_VERSION, "low": OPENMETEO_LOW_DATA_VERSION}
MIN_COMPLETE_AIFS_BYTES = 100_000_000


def _safe_name(value: str) -> str:
    return value.replace("/", "_").replace(" ", "_")


def _parse_cycle(value: str | None, *, now: datetime, release_lag_hours: float) -> datetime:
    if value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("--cycle must be timezone-aware")
        cycle = parsed.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        if cycle.hour not in {0, 6, 12, 18}:
            raise ValueError("--cycle hour must be 00, 06, 12, or 18 UTC")
        return cycle
    cutoff = now.astimezone(UTC) - timedelta(hours=release_lag_hours)
    candidate = cutoff.replace(minute=0, second=0, microsecond=0)
    while candidate.hour not in {0, 6, 12, 18}:
        candidate -= timedelta(hours=1)
    return candidate


def _source_available_at(cycle: datetime, *, release_lag_hours: float) -> datetime:
    return cycle.astimezone(UTC) + timedelta(hours=release_lag_hours)


def _aifs_steps_for_targets(targets: list[object], *, cycle: datetime) -> tuple[int, ...]:
    steps: set[int] = set()
    for target in targets:
        city_config = cities_by_name.get(target.city)
        if city_config is None:
            continue
        start_utc, end_utc = _local_day_window(city_config.timezone, target.target_date)
        for step in range(0, 121, 6):
            valid = cycle.astimezone(UTC) + timedelta(hours=step)
            if start_utc <= valid < end_utc:
                steps.add(step)
    if not steps:
        raise ValueError("no AIFS 6-hour steps cover current replacement targets")
    return tuple(sorted(steps))


def _local_day_window(city_timezone: str, target_date: str) -> tuple[datetime, datetime]:
    local_date = date.fromisoformat(target_date)
    zone = ZoneInfo(city_timezone)
    start = datetime(local_date.year, local_date.month, local_date.day, tzinfo=zone)
    end = start + timedelta(days=1)
    return start.astimezone(UTC), end.astimezone(UTC)


def _precision_metadata(city: str, target_date: str, *, anchor_sigma_c: float) -> dict[str, object]:
    city_config = cities_by_name[city]
    start, end = _local_day_window(city_config.timezone, target_date)
    station_id = city_config.wu_station or city
    return {
        "city": city,
        "station_id": station_id,
        "city_lat": float(city_config.lat),
        "city_lon": float(city_config.lon),
        "station_lat": float(city_config.lat),
        "station_lon": float(city_config.lon),
        "requested_lat": float(city_config.lat),
        "requested_lon": float(city_config.lon),
        "requested_coordinate_precision_decimals": 4,
        "nearest_grid_lat": float(city_config.lat),
        "nearest_grid_lon": float(city_config.lon),
        "nearest_grid_distance_km": 0.0,
        "native_grid": "openmeteo_ecmwf_ifs_9km",
        "delivery_grid_resolution": "9km",
        "interpolation_method": "openmeteo_api_point_interpolation",
        "endpoint_mode": "hourly_zeus_aggregated",
        "local_day_start_utc": start.isoformat(),
        "local_day_end_utc": end.isoformat(),
        "timezone_name": city_config.timezone,
        "target_local_date": target_date,
        "temperature_unit": "celsius",
        "anchor_sigma_c": float(anchor_sigma_c),
        "grid_elevation_m": 0.0,
        "station_elevation_m": 0.0,
        "land_sea_mask": "land",
        "city_class": "standard",
        "station_mapping_policy": "operator_verified_station",
    }


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _write_manifest_file(output_dir: Path, manifest: RawForecastArtifactManifest) -> Path:
    target = output_dir / (
        f"{manifest.source_id}.{manifest.data_version}."
        f"{manifest.source_cycle_time.strftime('%Y%m%dT%H%M%SZ')}."
        f"{manifest.sha256[:12]}.{_safe_name(str(manifest.product_metadata.get('city') or 'multi'))}.manifest.json"
    )
    write_manifest(manifest, target)
    return target


def download_current_target_raw_inputs(
    *,
    forecast_db: Path,
    output_dir: Path,
    cycle: datetime,
    limit: int | None,
    write_db: bool,
    skip_aifs: bool,
    skip_openmeteo: bool,
    release_lag_hours: float,
    anchor_sigma_c: float,
    aifs_retries: int,
    include_covered: bool = False,
) -> dict[str, object]:
    # Fetch the FULL plan (no limit) so uncovered cities beyond the first `limit`
    # alphabetical slots are visible.  The per-cycle cap is applied AFTER filtering
    # to uncovered rows only — otherwise a limit of 10 on an alphabetically-ordered
    # result that happens to start with 10 covered cities returns an empty target
    # list and the downloader silently produces zero manifests every cycle.
    #
    # CYCLE-CURRENCY (2026-06-09, K-root instance #3): "covered" means a posterior EXISTS —
    # it says NOTHING about which cycle that posterior was built on. Filtering the download
    # targets to uncovered rows therefore self-perpetuates staleness: a fully-covered window
    # never receives the NEW cycle's raw inputs, so re-materialization at the fresh cycle can
    # only ever bind old manifests (observed live: 06-11 targets re-pinned to the 06-08T18
    # manifests because the 06-09T00 download had skipped every covered target).
    # ``include_covered=True`` (passed by the production wrapper when the available cycle is
    # ahead of the downloaded high-water mark, and by the CLI when --cycle is explicit)
    # downloads raw inputs for ALL current targets at the requested cycle.
    plan = build_replacement_forecast_current_target_plan(forecast_db)
    _rows = list(plan.rows) if include_covered else [row for row in plan.rows if not row.covered]
    targets = _rows[:limit] if limit else _rows
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / cycle.strftime("%Y%m%dT%H%M%SZ")
    raw_dir.mkdir(parents=True, exist_ok=True)
    captured_at = datetime.now(tz=UTC)
    source_available = _source_available_at(cycle, release_lag_hours=release_lag_hours)
    manifests: list[RawForecastArtifactManifest] = []
    downloaded: dict[str, object] = {
        "aifs_grib": None,
        "openmeteo_payload_count": 0,
        "precision_metadata_count": 0,
    }

    targets_by_metric: dict[str, list[object]] = defaultdict(list)
    for target in targets:
        targets_by_metric[target.temperature_metric].append(target)

    if targets and not skip_aifs:
        aifs_steps = _aifs_steps_for_targets(targets, cycle=cycle)
        step_slug = f"{aifs_steps[0]}_{aifs_steps[-1]}_{len(aifs_steps)}steps"
        aifs_path = raw_dir / f"aifs_ens_{cycle.strftime('%Y%m%d_%Hz')}_2t_steps_{step_slug}.grib2"
        request = build_aifs_ens_open_data_request(
            forecast_date=cycle.date(),
            cycle_hour=cycle.hour,
            target_path=aifs_path,
            steps=aifs_steps,
        )
        if aifs_path.exists() and aifs_path.stat().st_size < MIN_COMPLETE_AIFS_BYTES:
            aifs_path.unlink()
        if not aifs_path.exists():
            last_error: Exception | None = None
            for attempt in range(max(1, int(aifs_retries))):
                try:
                    retrieve_aifs_ens_open_data_request(request)
                    last_error = None
                    break
                except Exception as exc:  # noqa: BLE001 - transport retry surface
                    last_error = exc
                    if aifs_path.exists() and aifs_path.stat().st_size < MIN_COMPLETE_AIFS_BYTES:
                        aifs_path.unlink()
                    if attempt + 1 >= max(1, int(aifs_retries)):
                        break
                    time.sleep(min(90.0, 10.0 * (2 ** attempt)))
            if last_error is not None:
                raise last_error
        downloaded["aifs_grib"] = str(aifs_path)
        for metric, metric_targets in sorted(targets_by_metric.items()):
            scope_cities = sorted({target.city for target in metric_targets})
            scope_dates = sorted({target.target_date for target in metric_targets})
            manifests.append(
                RawForecastArtifactManifest.from_file(
                    aifs_path,
                    source_id="ecmwf_aifs_ens",
                    product_id="ecmwf_aifs_ens_sampled_2t_6h_v1",
                    data_version=METRIC_TO_AIFS_VERSION[metric],
                    source_cycle_time=cycle.isoformat(),
                    source_available_at=source_available.isoformat(),
                    captured_at=max(captured_at, source_available).isoformat(),
                    request_url="ecmwf-opendata://aifs-ens/2t/current-targets",
                    request_params=request.retrieve_kwargs(),
                    product_metadata={
                        "artifact_class": "aifs_sampled_2t_grib_current_targets",
                        "cities": scope_cities,
                        "target_dates": scope_dates,
                        "metric": metric,
                        "source_run_id": f"aifs-current-targets-{metric}-{cycle.strftime('%Y%m%dT%H%M%SZ')}",
                    },
                )
            )

    if not skip_openmeteo:
        for target in targets:
            city_config = cities_by_name.get(target.city)
            if city_config is None:
                continue
            payload_path = raw_dir / f"openmeteo_{_safe_name(target.city)}_{target.target_date}_{target.temperature_metric}_{cycle.strftime('%Y%m%dT%H%M%SZ')}.json"
            precision_path = raw_dir / f"openmeteo_precision_{_safe_name(target.city)}_{target.target_date}_{target.temperature_metric}.json"
            request = build_anchor_request(
                latitude=float(city_config.lat),
                longitude=float(city_config.lon),
                run=cycle,
                timezone_name=city_config.timezone,
                forecast_hours=120,
            )
            anchor_transport_provenance: dict[str, object] = {
                "openmeteo_endpoint": "single_runs_api",
                "run_authority": "run_pinned_single_runs",
            }
            if not payload_path.exists():
                # Transport ladder (operator directive 2026-06-11, K4.0b(f)): run-pinned
                # single-runs FIRST (strongest provenance); when it does not yet serve the
                # wanted run, fall back to the meta-stamped standard API (provider-declared
                # run identity + pre/post atomicity check). Both serve the same model feed;
                # only the transport + run-authority differ, and the manifest records which.
                try:
                    payload = fetch_openmeteo_ecmwf_ifs9_anchor_payload(request)
                except httpx.HTTPStatusError as single_runs_exc:
                    # ONLY the run-not-yet-served class (HTTP 400 from single-runs) may
                    # degrade to the meta-stamped transport. Every other failure (auth,
                    # 5xx, schema, transport) must raise loudly — degrading on those
                    # would mask real defects behind a transport switch.
                    if single_runs_exc.response.status_code != 400:
                        raise
                    from src.data.openmeteo_ecmwf_ifs9_anchor import (
                        fetch_openmeteo_ecmwf_ifs9_anchor_payload_meta_stamped,
                    )

                    payload, meta_provenance = (
                        fetch_openmeteo_ecmwf_ifs9_anchor_payload_meta_stamped(request)
                    )
                    anchor_transport_provenance = dict(meta_provenance)
                    anchor_transport_provenance["single_runs_fallback_reason"] = (
                        f"HTTP 400 run not yet served: {str(single_runs_exc)[:160]}"
                    )
                _write_json(payload_path, payload)
            _write_json(precision_path, _precision_metadata(target.city, target.target_date, anchor_sigma_c=anchor_sigma_c))
            downloaded["openmeteo_payload_count"] = int(downloaded["openmeteo_payload_count"]) + 1
            downloaded["precision_metadata_count"] = int(downloaded["precision_metadata_count"]) + 1
            manifests.append(
                build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest(
                    payload_path,
                    request=request,
                    metric=target.temperature_metric,
                    source_available_at=source_available.isoformat(),
                    captured_at=max(captured_at, source_available).isoformat(),
                    product_metadata={
                        "artifact_class": "openmeteo_ecmwf_ifs9_anchor_current_targets",
                        "city": target.city,
                        "cities": [target.city],
                        "target_date": target.target_date,
                        "target_dates": [target.target_date],
                        "metric": target.temperature_metric,
                        "source_run_id": (
                            f"openmeteo-current-targets-{_safe_name(target.city)}-"
                            f"{target.temperature_metric}-{cycle.strftime('%Y%m%dT%H%M%SZ')}"
                        ),
                        "openmeteo_payload_json": str(payload_path),
                        "precision_metadata_json": str(precision_path),
                        **anchor_transport_provenance,
                    },
                )
            )

    written_manifests: list[str] = []
    db_artifact_ids: list[int] = []
    conn = None
    if write_db:
        conn = _connect(forecast_db, write_class="live")
        ensure_replacement_forecast_shadow_schema(conn)
        conn.execute("BEGIN")
    try:
        for manifest in manifests:
            manifest_path = _write_manifest_file(output_dir, manifest)
            written_manifests.append(str(manifest_path))
            if conn is not None:
                db_artifact_ids.append(write_manifest_to_db(conn, manifest, verify_artifact=True))
        if conn is not None:
            conn.commit()
    except Exception:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        if conn is not None:
            conn.close()

    return {
        "status": "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED",
        "cycle": cycle.isoformat(),
        "forecast_db": str(forecast_db),
        "output_dir": str(output_dir),
        "target_count": len(targets),
        "manifest_count": len(manifests),
        "written_manifest_count": len(written_manifests),
        "written_manifests": written_manifests,
        "write_db": write_db,
        "db_artifact_ids": db_artifact_ids,
        "downloaded": downloaded,
        "coverage_before": plan.as_dict(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download current replacement forecast raw inputs")
    parser.add_argument("--forecast-db", type=Path, default=ROOT / "state" / "zeus-forecasts.db")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "state" / "replacement_forecast_shadow" / "raw_manifests")
    parser.add_argument("--cycle", help="UTC cycle datetime; default latest 00/06/12/18 cycle older than release lag")
    parser.add_argument("--release-lag-hours", type=float, default=14.0)
    parser.add_argument("--anchor-sigma-c", type=float, default=3.0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--write-db", action="store_true")
    parser.add_argument("--skip-aifs", action="store_true")
    parser.add_argument("--skip-openmeteo", action="store_true")
    parser.add_argument("--aifs-retries", type=int, default=4)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)
    cycle = _parse_cycle(args.cycle, now=datetime.now(tz=UTC), release_lag_hours=args.release_lag_hours)
    try:
        result = download_current_target_raw_inputs(
            forecast_db=args.forecast_db,
            output_dir=args.output_dir,
            cycle=cycle,
            limit=args.limit,
            write_db=args.write_db,
            skip_aifs=args.skip_aifs,
            skip_openmeteo=args.skip_openmeteo,
            release_lag_hours=args.release_lag_hours,
            anchor_sigma_c=args.anchor_sigma_c,
            aifs_retries=args.aifs_retries,
            # An EXPLICIT --cycle is an operator instruction to (re)download THAT cycle's
            # raw inputs for the whole current window — coverage must not filter it.
            include_covered=bool(args.cycle),
        )
        if args.output_json is not None:
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            args.output_json.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error_type": exc.__class__.__name__, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    if args.stdout:
        print(json.dumps(result, sort_keys=True, default=str))
    else:
        print(result["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
