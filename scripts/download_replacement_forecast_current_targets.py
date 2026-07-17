#!/usr/bin/env python3
# Created: 2026-06-07
# Last reused/audited: 2026-07-16
# Lifecycle: created=2026-06-07; last_reviewed=2026-07-16
# Purpose: Download current-target Open-Meteo ECMWF IFS 9km raw inputs for replacement forecast materialization.
# Reuse: Run before live replacement materialization when dry-run reports current-target coverage gaps.
# Authority basis: Raw artifacts are live inputs only after the replacement materializer emits
#   forecast_posteriors rows with runtime_layer='live'.
"""Download replacement forecast raw inputs for current market targets."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import cities_by_name  # noqa: E402
from src.data.openmeteo_ecmwf_ifs9_anchor import (  # noqa: E402
    HIGH_DATA_VERSION as OPENMETEO_HIGH_DATA_VERSION,
    LOW_DATA_VERSION as OPENMETEO_LOW_DATA_VERSION,
    build_anchor_request,
    build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest,
    fetch_openmeteo_ecmwf_ifs9_anchor_payload,
    fetch_openmeteo_ecmwf_ifs9_anchor_payload_standard_unstamped,
    fetch_openmeteo_ifs9_model_meta,
    validate_openmeteo_ecmwf_ifs9_meta_window,
)
from src.data.raw_forecast_artifact_manifest import (  # noqa: E402
    RawForecastArtifactManifest,
    manifest_matches_artifact,
    repin_manifest_from_file,
    write_manifest,
    write_manifest_to_db,
)
from src.data.replacement_forecast_current_target_plan import (  # noqa: E402
    ReplacementForecastCurrentTargetPlan,
    build_replacement_forecast_current_target_plan,
)
from src.state.db import _connect  # noqa: E402
from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema  # noqa: E402


METRIC_TO_OPENMETEO_VERSION = {"high": OPENMETEO_HIGH_DATA_VERSION, "low": OPENMETEO_LOW_DATA_VERSION}


def _safe_name(value: str) -> str:
    return value.replace("/", "_").replace(" ", "_")


def _parse_cycle(value: str | None, *, now: datetime, release_lag_hours: float) -> datetime:
    """Parse an EXPLICIT cycle string. The ``value=None`` guess path is DEAD (2026-06-11).

    The old fallback floored ``now − release_lag`` to a cycle hour — a guessed clock that
    requested unpublished 12Z/18Z runs every night; the rung-2 meta guard refused them and
    the refusal aborted the whole download→materialize cycle. Run selection without an
    explicit operator cycle goes through the probe-resolved single authority
    (``src.data.replacement_forecast_production._probe_resolved_available_cycle``); this
    function refuses to guess so the dead path is unconstructable."""
    if not value:
        raise ValueError(
            "cycle must be explicit or probe-resolved; the now-minus-release-lag guess "
            "is dead (2026-06-11 run-selection single authority)"
        )
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--cycle must be timezone-aware")
    cycle = parsed.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    if cycle.hour not in {0, 6, 12, 18}:
        raise ValueError("--cycle hour must be 00, 06, 12, or 18 UTC")
    return cycle


def _source_available_at(cycle: datetime, *, release_lag_hours: float) -> datetime:
    return cycle.astimezone(UTC) + timedelta(hours=release_lag_hours)


def _single_runs_public_for_request(request) -> bool:
    """Best-effort source-clock precheck before rung-1 single-runs.

    The availability resolver may admit a cycle because the S3 bucket declares it
    before Open-Meteo's single-runs API serves it. In that state, trying rung 1
    for every city only produces repeated 400s. The source-clock probe refreshes
    cached Open-Meteo model metadata before this downloader runs; when that cache
    says ECMWF single-runs has not publicly exposed ``request.run`` yet, skip rung
    1 and proceed to the existing meta/bucket ladder.
    """
    try:
        from src.data.source_clock_update_probe import DEFAULT_MODEL_UPDATES_JSONL  # noqa: PLC0415
        from src.data.openmeteo_model_updates import read_model_updates_jsonl  # noqa: PLC0415
        from src.strategy.live_inference.source_clock_vnext import (  # noqa: PLC0415
            source_publicly_usable_at,
        )

        updates = read_model_updates_jsonl(DEFAULT_MODEL_UPDATES_JSONL)
    except Exception:
        return True
    for update in updates:
        if str(update.model) != "ecmwf_ifs":
            continue
        try:
            run_clock = update.to_source_run_clock()
            return (
                update.last_run_initialisation_time.astimezone(UTC) == request.run.astimezone(UTC)
                and datetime.now(tz=UTC) >= source_publicly_usable_at(run_clock)
            )
        except Exception:
            return True
    return True


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
    body = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
    # Validate the exact bytes we are about to publish. A malformed raw payload
    # is worse than a missing payload because manifest discovery will keep
    # reusing it for every held-position reseed.
    json.loads(body)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        tmp = ""
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _json_file_valid(path: Path) -> bool:
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return True
    except Exception:
        return False


def _write_manifest_file(output_dir: Path, manifest: RawForecastArtifactManifest) -> Path:
    target = output_dir / (
        f"{manifest.source_id}.{manifest.data_version}."
        f"{manifest.source_cycle_time.strftime('%Y%m%dT%H%M%SZ')}."
        f"{manifest.sha256[:12]}.{_safe_name(str(manifest.product_metadata.get('city') or 'multi'))}.manifest.json"
    )
    write_manifest(manifest, target)
    return target


def _deadline_timeout(
    deadline_monotonic: float | None,
    *,
    default: float,
) -> float:
    if deadline_monotonic is None:
        return default
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("current-target download deadline expired")
    return max(0.001, min(default, remaining))


def _try_bucket_rung_three(
    *,
    request,
    city: str,
    target_date: str,
    timezone_name: str,
    meta_refusal: Exception,
    single_runs_exc: Exception,
    deadline_monotonic: float | None = None,
    bucket_manifest_provider: Callable[[], dict] | None = None,
) -> tuple[dict, dict]:
    """Rung-3 admission gate: serve from the S3 data_spatial bucket, or re-raise rung-2.

    Strict preconditions (ALL must hold, else the rung-2 ValueError is re-raised UNCHANGED
    so its refusal semantics are never masked):
      1. the bucket's in-progress/latest.json declares EXACTLY the wanted run;
      2. every needed local-day hourly timestep is present in that manifest's valid_times
         (partial-run admission — no extrapolation / gap-fill);
      3. the city is on the cross-check-VERIFIED whitelist (coastal/terrain cities differ
         from the API's downscaled point series; only ≤0.05C-verified cities are served).
    Returns ``(payload, provenance)`` on admission."""
    from datetime import date as _date

    from src.data.openmeteo_ecmwf_ifs9_bucket_transport import (
        BucketTransportNotAdmissible,
        capture_city_target_elevation,
        fetch_bucket_anchor_payload,
        fetch_bucket_anchor_payload_downscaled,
        fetch_bucket_run_manifest,
        local_day_hourly_valid_times,
        resolve_bucket_serve_method,
        select_declaring_manifest,
    )

    manifests = (
        bucket_manifest_provider()
        if bucket_manifest_provider is not None
        else fetch_bucket_run_manifest(
            timeout=_deadline_timeout(deadline_monotonic, default=20.0),
            deadline_monotonic=deadline_monotonic,
        )
    )
    manifest = select_declaring_manifest(manifests, wanted_run=request.run)
    if manifest is None:
        # condition 1 fails: bucket does not declare the wanted run. No transport can serve
        # this city this cycle — signal a skippable non-admission (carries the rung-2 reason).
        raise BucketTransportNotAdmissible(
            f"bucket does not declare wanted run {request.run.isoformat()} "
            f"(rung-2 refusal: {meta_refusal})"
        )
    # condition 3: HOW may the bucket serve this city — "raw" (nearest-gridpoint read verified)
    # OR "downscaled" (terrain land-cell + lapse-rate read verified) OR None (non-admitted).
    # A city verified by NEITHER class stays non-admitted and falls to rungs 1-2 (honest; the
    # 0.1C cross-check tolerance is never weakened — coastal/terrain cities the downscaling
    # cannot reproduce do not get served).
    serve_method = resolve_bucket_serve_method(city)
    if serve_method is None:
        raise BucketTransportNotAdmissible(
            f"city {city} not on bucket cross-check whitelist (raw or downscaled) "
            f"(rung-2 refusal: {meta_refusal})"
        )
    needed = local_day_hourly_valid_times(
        run=request.run,
        city_timezone=timezone_name,
        target_local_date=_date.fromisoformat(target_date),
        forecast_hours=request.forecast_hours,
    )
    try:
        if serve_method == "downscaled":
            # target elevation = the API-reported 90m-DEM elevation (captured once per city,
            # cached with provenance). This is the SAME authority that VERIFIED the city.
            target_elev = capture_city_target_elevation(
                city,
                request.latitude,
                request.longitude,
                timeout=_deadline_timeout(deadline_monotonic, default=20.0),
            )
            result = fetch_bucket_anchor_payload_downscaled(  # re-checks admission internally
                latitude=request.latitude,
                longitude=request.longitude,
                target_elevation_m=target_elev,
                run=request.run,
                timezone_name=timezone_name,
                needed_valid_times=needed,
                manifest=manifest,
                deadline_monotonic=deadline_monotonic,
            )
        else:  # "raw"
            result = fetch_bucket_anchor_payload(  # re-checks admission (condition 2) internally
                latitude=request.latitude,
                longitude=request.longitude,
                run=request.run,
                timezone_name=timezone_name,
                needed_valid_times=needed,
                manifest=manifest,
                deadline_monotonic=deadline_monotonic,
            )
    except ValueError as admission_exc:
        # condition 2 fails: a needed local-day timestep is not yet written. Skip this city
        # this cycle (no extrapolation) — it falls to a higher rung next tick.
        raise BucketTransportNotAdmissible(
            f"city {city} partial-run admission failed: {admission_exc}"
        ) from admission_exc
    provenance = dict(result.provenance)
    provenance["single_runs_fallback_reason"] = (
        f"HTTP 400 run not yet served: {str(single_runs_exc)[:120]}"
    )
    provenance["meta_stamped_fallback_reason"] = (
        f"rung-2 could not serve: {str(meta_refusal)[:120]}"
    )
    return result.payload, provenance


def _resolve_anchor_payload(
    *,
    request,
    city: str,
    target_date: str,
    timezone_name: str,
    deadline_monotonic: float | None = None,
    bucket_manifest_provider: Callable[[], dict] | None = None,
    client: httpx.Client | None = None,
    meta_wave_failure: Exception | None = None,
) -> tuple[dict, dict]:
    """Resolve one city's anchor payload through the full transport ladder.

    Rung 1 (run-pinned single-runs) → rung 2 (meta-stamped standard) → rung 3 (S3 bucket
    partial-run). Returns ``(payload, transport_provenance)``. Raises
    ``BucketTransportNotAdmissible`` only when NO rung can serve this city THIS cycle (so the
    caller skips the city, never the batch). Genuine defects still raise loudly:
      * rung 1: HTTP 400 (run-not-yet-served), 429, and 5xx degrade to rung 2; auth/client
        defects re-raise.
      * rung 2: meta REFUSAL (ValueError: provider declares an older run; never weakened),
        transport errors, provider rate limits, quota cooldown, retry exhaustion, and 5xx
        degrade to rung 3. Other 4xx responses on meta are client-side defects and re-raise.
      * rung 3: serves only cross-check-whitelisted cities for the bucket-declared wanted run
        with every needed timestep present; otherwise BucketTransportNotAdmissible.
    """
    from src.data.openmeteo_ecmwf_ifs9_anchor import (
        fetch_openmeteo_ecmwf_ifs9_anchor_payload_meta_stamped,
    )

    def _is_transient_provider_failure(exc: Exception) -> bool:
        text = str(exc).lower()
        return (
            "429" in text
            or "too many requests" in text
            or "quota exhausted" in text
            or "temporarily blocked" in text
            or "cooldown" in text
            or "exhausted retries" in text
            or "rate limit" in text
        )

    def _exception_summary(exc: Exception) -> str:
        return f"{type(exc).__name__}: {str(exc)[:160]}"

    # A failed wave has already bracketed the provider's standard endpoint. Retrying both
    # HTTP rungs per city would recreate the waterfall; preserve the refusal and continue at
    # the independent bucket transport.
    single_runs_exc: Exception
    if meta_wave_failure is not None:
        single_runs_exc = RuntimeError(
            "single-runs rung skipped: source-clock metadata says requested run is not public yet"
        )
        rung2_reason: Exception = meta_wave_failure
    elif _single_runs_public_for_request(request):
        try:
            kwargs: dict[str, object] = {"fast_fail_429": True}
            if client is not None:
                kwargs["client"] = client
            if deadline_monotonic is not None:
                kwargs.update(
                    timeout=_deadline_timeout(deadline_monotonic, default=30.0),
                    max_retries=1,
                )
            payload = fetch_openmeteo_ecmwf_ifs9_anchor_payload(request, **kwargs)
            return payload, {
                "openmeteo_endpoint": "single_runs_api",
                "run_authority": "run_pinned_single_runs",
            }
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code != 400 and status_code != 429 and status_code < 500:
                raise
            # `except ... as` unbinds the name at block exit; persist it for rungs 2/3.
            single_runs_exc = exc
        except RuntimeError as exc:
            if not _is_transient_provider_failure(exc):
                raise
            single_runs_exc = exc
    else:
        single_runs_exc = RuntimeError(
            "single-runs rung skipped: source-clock metadata says requested run is not public yet"
        )

    # Rung 2: meta-stamped standard API (provider-declared run + atomicity).
    if meta_wave_failure is None:
        try:
            kwargs = {"fast_fail_429": True}
            if client is not None:
                kwargs["client"] = client
            if deadline_monotonic is not None:
                kwargs.update(
                    timeout=_deadline_timeout(deadline_monotonic, default=30.0),
                    max_retries=1,
                )
            payload, meta_provenance = fetch_openmeteo_ecmwf_ifs9_anchor_payload_meta_stamped(
                request, **kwargs
            )
            provenance = dict(meta_provenance)
            provenance["single_runs_fallback_reason"] = _exception_summary(single_runs_exc)
            return payload, provenance
        except httpx.HTTPStatusError as meta_status_exc:
            # 429/5xx = provider-side unavailability (degrade to rung 3); other 4xx = our defect.
            status_code = meta_status_exc.response.status_code
            if status_code != 429 and status_code < 500:
                raise
            rung2_reason = meta_status_exc
        except RuntimeError as meta_runtime_exc:
            if not _is_transient_provider_failure(meta_runtime_exc):
                raise
            rung2_reason = meta_runtime_exc
        except (ValueError, httpx.TransportError) as meta_exc:
            # ValueError = meta REFUSAL (older run; never weakened); TransportError = provider
            # unreachable. Both degrade to rung 3 (the bucket is independent infrastructure).
            rung2_reason = meta_exc

    # Rung 3: S3 bucket partial-run (whitelisted cities only).
    rung_three_kwargs: dict[str, object] = {
        "request": request,
        "city": city,
        "target_date": target_date,
        "timezone_name": timezone_name,
        "meta_refusal": rung2_reason,
        "single_runs_exc": single_runs_exc,
    }
    if deadline_monotonic is not None:
        rung_three_kwargs["deadline_monotonic"] = deadline_monotonic
    if bucket_manifest_provider is not None:
        rung_three_kwargs["bucket_manifest_provider"] = bucket_manifest_provider
    return _try_bucket_rung_three(
        **rung_three_kwargs,
    )


def _fetch_meta_stamped_anchor_wave(
    requests: dict[tuple[str, str], object],
    *,
    max_workers: int,
    deadline_monotonic: float | None,
    client: httpx.Client,
) -> tuple[
    dict[tuple[str, str], tuple[dict, dict[str, object], datetime]],
    dict[tuple[str, str], Exception],
]:
    """Fetch a CURRENT-run city wave under one provider metadata bracket."""
    if not requests:
        return {}, {}
    request0 = next(iter(requests.values()))
    timeout = _deadline_timeout(deadline_monotonic, default=30.0)
    meta_before = fetch_openmeteo_ifs9_model_meta(
        timeout=timeout,
        max_retries=1,
        fast_fail_429=True,
        client=client,
    )
    # Refuse before issuing city payload requests when the provider does not declare this run.
    validate_openmeteo_ecmwf_ifs9_meta_window(request0, meta_before, meta_before)

    payloads: dict[tuple[str, str], tuple[dict, datetime]] = {}
    failures: dict[tuple[str, str], Exception] = {}
    workers = min(max(1, int(max_workers)), 8, len(requests))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="openmeteo-anchor") as executor:
        future_keys = {
            executor.submit(
                fetch_openmeteo_ecmwf_ifs9_anchor_payload_standard_unstamped,
                request,
                timeout=_deadline_timeout(deadline_monotonic, default=30.0),
                max_retries=1,
                fast_fail_429=True,
                client=client,
            ): key
            for key, request in requests.items()
        }
        for future in as_completed(future_keys):
            key = future_keys[future]
            try:
                payloads[key] = (dict(future.result()), datetime.now(tz=UTC))
            except Exception as exc:  # each city retains its independent bucket fallback
                failures[key] = exc

    meta_after = fetch_openmeteo_ifs9_model_meta(
        timeout=_deadline_timeout(deadline_monotonic, default=20.0),
        max_retries=1,
        fast_fail_429=True,
        client=client,
    )
    try:
        provenance = dict(
            validate_openmeteo_ecmwf_ifs9_meta_window(request0, meta_before, meta_after)
        )
    except Exception as exc:
        failures.update({key: exc for key in payloads})
        return {}, failures
    provenance["meta_stamp_scope"] = "download_wave"
    provenance["meta_stamp_wave_payload_count"] = len(payloads)
    resolved = {
        key: (payload, dict(provenance), captured_at)
        for key, (payload, captured_at) in payloads.items()
    }
    return resolved, failures


def download_current_target_raw_inputs(
    *,
    forecast_db: Path,
    output_dir: Path,
    cycle: datetime,
    limit: int | None,
    write_db: bool,
    release_lag_hours: float,
    anchor_sigma_c: float,
    include_covered: bool = False,
    missing_manifests_only: bool = False,
    precomputed_plan: ReplacementForecastCurrentTargetPlan | None = None,
    max_wall_clock_seconds: float | None = None,
    fetch_workers: int = 4,
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
    plan = precomputed_plan or build_replacement_forecast_current_target_plan(
        forecast_db,
        required_openmeteo_source_cycle_time=cycle,
    )
    if include_covered:
        _rows = list(plan.rows)
    elif missing_manifests_only:
        _rows = [row for row in plan.rows if row.missing_openmeteo_manifest]
    else:
        _rows = [row for row in plan.rows if not row.covered]
    _rows.sort(
        key=lambda row: (
            0 if getattr(row, "missing_openmeteo_manifest", False) else 1,
            0 if not getattr(row, "covered", False) else 1,
            str(getattr(row, "target_date", "")),
            str(getattr(row, "city", "")),
            str(getattr(row, "temperature_metric", "")),
        )
    )
    targets = _rows[:limit] if limit else _rows
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / cycle.strftime("%Y%m%dT%H%M%SZ")
    raw_dir.mkdir(parents=True, exist_ok=True)
    nominal_source_available = _source_available_at(
        cycle, release_lag_hours=release_lag_hours
    )
    manifests: list[RawForecastArtifactManifest] = []
    skipped_cities: list[dict[str, object]] = []
    downloaded: dict[str, object] = {
        "openmeteo_payload_count": 0,
        "precision_metadata_count": 0,
        "openmeteo_transport_fetch_count": 0,
        "openmeteo_model_meta_fetch_count": 0,
        "openmeteo_wave_payload_count": 0,
    }
    deadline_monotonic = (
        time.monotonic() + max(0.0, float(max_wall_clock_seconds))
        if max_wall_clock_seconds is not None
        else None
    )
    resolved_payloads: dict[
        tuple[str, str], tuple[dict, dict[str, object], datetime]
    ] = {}
    meta_wave_failures: dict[tuple[str, str], Exception] = {}
    unavailable_targets: set[tuple[str, str]] = set()
    processed_target_count = 0
    timeboxed_incomplete = False
    bucket_manifests: dict | None = None

    from src.data.openmeteo_ecmwf_ifs9_bucket_transport import (
        BucketTransportNotAdmissible,
        fetch_bucket_run_manifest,
    )

    def current_bucket_manifests() -> dict:
        nonlocal bucket_manifests
        if bucket_manifests is None:
            bucket_manifests = fetch_bucket_run_manifest(
                timeout=_deadline_timeout(deadline_monotonic, default=20.0),
                deadline_monotonic=deadline_monotonic,
            )
        return bucket_manifests

    pending_requests: dict[tuple[str, str], object] = {}
    for target in targets:
        city_config = cities_by_name.get(target.city)
        if city_config is None:
            continue
        target_key = (target.city, target.target_date)
        payload_path = raw_dir / f"openmeteo_{_safe_name(target.city)}_{target.target_date}_{target.temperature_metric}_{cycle.strftime('%Y%m%dT%H%M%SZ')}.json"
        if payload_path.exists() and _json_file_valid(payload_path):
            continue
        pending_requests.setdefault(
            target_key,
            build_anchor_request(
                latitude=float(city_config.lat),
                longitude=float(city_config.lon),
                run=cycle,
                timezone_name=city_config.timezone,
                forecast_hours=120,
            ),
        )

    openmeteo_client = httpx.Client()
    if pending_requests and not _single_runs_public_for_request(next(iter(pending_requests.values()))):
        try:
            wave_resolved, meta_wave_failures = _fetch_meta_stamped_anchor_wave(
                pending_requests,
                max_workers=fetch_workers,
                deadline_monotonic=deadline_monotonic,
                client=openmeteo_client,
            )
            downloaded["openmeteo_model_meta_fetch_count"] = 2
        except Exception as exc:
            meta_wave_failures = {key: exc for key in pending_requests}
            wave_resolved = {}
            downloaded["openmeteo_model_meta_fetch_count"] = 1
        resolved_payloads.update(wave_resolved)
        downloaded["openmeteo_transport_fetch_count"] = len(wave_resolved)
        downloaded["openmeteo_wave_payload_count"] = len(wave_resolved)

    try:
        for target in targets:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                timeboxed_incomplete = True
                break
            target_key = (target.city, target.target_date)
            city_config = cities_by_name.get(target.city)
            if city_config is None:
                processed_target_count += 1
                continue
            payload_path = raw_dir / f"openmeteo_{_safe_name(target.city)}_{target.target_date}_{target.temperature_metric}_{cycle.strftime('%Y%m%dT%H%M%SZ')}.json"
            precision_path = raw_dir / f"openmeteo_precision_{_safe_name(target.city)}_{target.target_date}_{target.temperature_metric}.json"
            request = pending_requests.get(target_key) or build_anchor_request(
                latitude=float(city_config.lat),
                longitude=float(city_config.lon),
                run=cycle,
                timezone_name=city_config.timezone,
                forecast_hours=120,
            )
            payload_captured_at = datetime.now(tz=UTC)
            anchor_transport_provenance: dict[str, object] = {
                "openmeteo_endpoint": "single_runs_api",
                "run_authority": "run_pinned_single_runs",
            }

            if (not payload_path.exists()) or (not _json_file_valid(payload_path)):
                try:
                    if target_key in unavailable_targets:
                        raise BucketTransportNotAdmissible(
                            "same city/date transport was already non-admissible this pass"
                        )
                    cached = resolved_payloads.get(target_key)
                    if cached is None:
                        payload, anchor_transport_provenance = _resolve_anchor_payload(
                            request=request,
                            city=target.city,
                            target_date=target.target_date,
                            timezone_name=city_config.timezone,
                            deadline_monotonic=deadline_monotonic,
                            bucket_manifest_provider=current_bucket_manifests,
                            client=openmeteo_client,
                            meta_wave_failure=meta_wave_failures.get(target_key),
                        )
                        payload_captured_at = datetime.now(tz=UTC)
                        resolved_payloads[target_key] = (
                            payload,
                            anchor_transport_provenance,
                            payload_captured_at,
                        )
                        downloaded["openmeteo_transport_fetch_count"] = (
                            int(downloaded["openmeteo_transport_fetch_count"]) + 1
                        )
                    else:
                        payload, anchor_transport_provenance, payload_captured_at = cached
                except BucketTransportNotAdmissible as not_admissible:
                    unavailable_targets.add(target_key)
                    skipped_cities.append(
                        {
                            "city": target.city,
                            "target_date": target.target_date,
                            "metric": target.temperature_metric,
                            "reason": str(not_admissible)[:200],
                        }
                    )
                    processed_target_count += 1
                    continue
                except TimeoutError:
                    timeboxed_incomplete = True
                    break
                _write_json(payload_path, payload)
            _write_json(
                precision_path,
                _precision_metadata(
                    target.city,
                    target.target_date,
                    anchor_sigma_c=anchor_sigma_c,
                ),
            )
            downloaded["openmeteo_payload_count"] = (
                int(downloaded["openmeteo_payload_count"]) + 1
            )
            downloaded["precision_metadata_count"] = (
                int(downloaded["precision_metadata_count"]) + 1
            )

            is_bucket = str(
                anchor_transport_provenance.get("run_authority", "")
            ).startswith("bucket_partial_run")
            effective_source_available = (
                payload_captured_at
                if is_bucket
                else min(payload_captured_at, nominal_source_available)
            )
            manifests.append(
                build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest(
                    payload_path,
                    request=request,
                    metric=target.temperature_metric,
                    source_available_at=effective_source_available.isoformat(),
                    captured_at=payload_captured_at.isoformat(),
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
            processed_target_count += 1
    finally:
        openmeteo_client.close()

    written_manifests: list[str] = []
    db_artifact_ids: list[int] = []
    conn = None
    if write_db:
        conn = _connect(forecast_db, write_class="live")
        ensure_replacement_forecast_live_schema(conn)
        # BEGIN IMMEDIATE: take the write lock up front so busy_timeout WAITS for it,
        # instead of a deferred BEGIN failing on the SELECT->INSERT upgrade under
        # rollback-journal (delete) mode contention (the forecast-DB lock storm).
        conn.execute("BEGIN IMMEDIATE")
    try:
        for manifest in manifests:
            # Manifest-drift guard (2026-07-08 posterior blackout): the manifest was built
            # from the on-disk artifact above, but on the reuse path (payload_path.exists())
            # the file can have been rewritten with a benign serialization change AFTER an
            # earlier pin - the trailing "\n" _write_json appends (e2cd7a9bc, 2026-06-24) -
            # or by a concurrent cycle. If the bytes on disk no longer match this manifest's
            # byte_size/sha, re-pin from the CURRENT file so BOTH the raw_manifests/*.json
            # file and the DB row describe the exact artifact verify_artifact will stat,
            # instead of persisting a stale size that aborts materialization. A MISSING
            # artifact is left to write_manifest_to_db's verify to raise (not re-pinned).
            if not manifest_matches_artifact(manifest) and Path(manifest.artifact_path).exists():
                manifest = repin_manifest_from_file(manifest)
            manifest_path = _write_manifest_file(output_dir, manifest)
            written_manifests.append(str(manifest_path))
            if conn is not None:
                db_artifact_ids.append(
                    write_manifest_to_db(conn, manifest, verify_artifact=True, repin_on_drift=True)
                )
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
        "status": (
            "CURRENT_TARGET_RAW_INPUTS_TIMEBOXED_INCOMPLETE"
            if timeboxed_incomplete
            else "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED"
        ),
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
        "skipped_city_count": len(skipped_cities),
        "skipped_cities": skipped_cities,
        "timeboxed_incomplete": timeboxed_incomplete,
        "unattempted_target_count": len(targets) - processed_target_count,
        "max_wall_clock_seconds": max_wall_clock_seconds,
        "fetch_workers": min(max(1, int(fetch_workers)), 8),
        "coverage_before": plan.as_dict(),
    }


def download_current_target_openmeteo_inputs(
    *,
    forecast_db: Path,
    output_dir: Path,
    cycle: datetime,
    limit: int | None,
    write_db: bool,
    release_lag_hours: float,
    anchor_sigma_c: float,
    include_covered: bool = False,
    missing_manifests_only: bool = False,
    precomputed_plan: ReplacementForecastCurrentTargetPlan | None = None,
    max_wall_clock_seconds: float | None = None,
    fetch_workers: int = 4,
) -> dict[str, object]:
    """Live replacement-chain downloader for Open-Meteo current-target inputs."""

    return download_current_target_raw_inputs(
        forecast_db=forecast_db,
        output_dir=output_dir,
        cycle=cycle,
        limit=limit,
        write_db=write_db,
        release_lag_hours=release_lag_hours,
        anchor_sigma_c=anchor_sigma_c,
        include_covered=include_covered,
        missing_manifests_only=missing_manifests_only,
        precomputed_plan=precomputed_plan,
        max_wall_clock_seconds=max_wall_clock_seconds,
        fetch_workers=fetch_workers,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download current replacement forecast raw inputs")
    parser.add_argument("--forecast-db", type=Path, default=ROOT / "state" / "zeus-forecasts.db")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "state" / "replacement_forecast_live" / "raw_manifests")
    parser.add_argument("--cycle", help="UTC cycle datetime; default = probe-resolved newest published anchor-complete cycle")
    parser.add_argument("--release-lag-hours", type=float, default=14.0)
    parser.add_argument("--anchor-sigma-c", type=float, default=3.0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--write-db", action="store_true")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)
    if args.cycle:
        cycle = _parse_cycle(args.cycle, now=datetime.now(tz=UTC), release_lag_hours=args.release_lag_hours)
    else:
        # Run-selection single authority (2026-06-11): no explicit cycle → the probe-resolved
        # newest anchor-complete published cycle, same as the production jobs. Never a guess.
        from src.data.replacement_forecast_production import _probe_resolved_available_cycle

        maybe_cycle = _probe_resolved_available_cycle()
        if maybe_cycle is None:
            print(
                json.dumps({"status": "CYCLE_PROBE_UNRESOLVED", "detail": "no anchor-complete cycle provable by provider probes; pass --cycle to override"}),
                file=sys.stderr,
            )
            return 2
        cycle = maybe_cycle
    try:
        result = download_current_target_raw_inputs(
            forecast_db=args.forecast_db,
            output_dir=args.output_dir,
            cycle=cycle,
            limit=args.limit,
            write_db=args.write_db,
            release_lag_hours=args.release_lag_hours,
            anchor_sigma_c=args.anchor_sigma_c,
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
