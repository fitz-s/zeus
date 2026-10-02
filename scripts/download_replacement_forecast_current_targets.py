#!/usr/bin/env python3
# Created: 2026-06-07
# Last reused/audited: 2026-09-30
# Lifecycle: created=2026-06-07; last_reviewed=2026-09-30; last_reused=2026-09-30
# Purpose: Download current-target Open-Meteo ECMWF IFS 9km raw inputs for replacement forecast materialization.
# Reuse: Run before live replacement materialization when dry-run reports current-target coverage gaps.
# Authority basis: Raw artifacts are live inputs only after the replacement materializer emits
#   forecast_posteriors rows with runtime_layer='live'.
"""Download replacement forecast raw inputs for current market targets."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import logging
import math
import os
import sqlite3
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta
from dataclasses import replace
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import cities_by_name  # noqa: E402
from src.data.openmeteo_ecmwf_ifs9_anchor import (  # noqa: E402
    CURRENT_RUN_CONTEXT_HOURS,
    HIGH_DATA_VERSION as OPENMETEO_HIGH_DATA_VERSION,
    LOW_DATA_VERSION as OPENMETEO_LOW_DATA_VERSION,
    PRODUCT_ID as OPENMETEO_PRODUCT_ID,
    SOURCE_ID as OPENMETEO_SOURCE_ID,
    build_anchor_request,
    build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest,
    extract_openmeteo_ecmwf_ifs9_localday_anchor,
    fetch_openmeteo_ecmwf_ifs9_anchor_payload,
    fetch_openmeteo_ecmwf_ifs9_anchor_payloads,
    fetch_openmeteo_ecmwf_ifs9_anchor_payload_standard_unstamped,
    fetch_openmeteo_ifs9_model_meta,
    validate_openmeteo_ecmwf_ifs9_meta_window,
)
from src.data.openmeteo_quota import quota_tracker  # noqa: E402
from src.data.openmeteo_ecmwf_ifs9_precision_guard import (  # noqa: E402
    OpenMeteoIfs9PrecisionMetadata,
    evaluate_openmeteo_ecmwf_ifs9_precision_guard,
)
from src.data.raw_forecast_artifact_manifest import (  # noqa: E402
    RawForecastArtifactManifest,
    manifest_matches_artifact,
    read_manifest,
    repin_manifest_from_file,
    write_manifest,
    write_manifest_to_db,
    write_anchor_local_proof,
    anchor_precision_transport_path,
    publish_anchor_precision_transport,
    anchor_transport_manifest_path,
    publish_anchor_transport_manifest,
)
from src.data.replacement_forecast_current_target_plan import (  # noqa: E402
    ReplacementForecastCurrentTargetPlan,
    ReplacementForecastTargetKey,
    build_replacement_forecast_current_target_plan,
)
from src.state.db import _connect  # noqa: E402
from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema  # noqa: E402


_CURRENT_TARGET_ROTATION_LOCK = Lock()
_CURRENT_TARGET_ROTATION_OFFSETS: dict[str, int] = {}
_CURRENT_TARGET_ROTATION_STATE_VERSION = 1
_RotationStateToken = tuple[str | None, int | None]
_MAX_BUCKET_FALLBACK_RESERVE_SECONDS = 10.0
_ANCHOR_WAVE_MAX_INFLIGHT = 8
_ANCHOR_WAVE_LOCK = Lock()
_ANCHOR_WAVE_EXECUTOR: ThreadPoolExecutor | None = None
_ANCHOR_WAVE_INFLIGHT: dict[tuple[str, str, str], Future] = {}


def _rotation_state_lock_path(state_path: Path) -> Path:
    return state_path.with_name(f"{state_path.name}.lock")


def _read_rotation_state(
    state_path: Path,
    *,
    cycle_key: str,
) -> tuple[int, int, _RotationStateToken]:
    if not state_path.exists():
        return 0, 0, (None, None)
    try:
        state = json.loads(state_path.read_text())
        if not isinstance(state, dict):
            raise ValueError("rotation state must be an object")
        state_fields = set(state)
        legacy_state = state_fields == {"cycle", "next_start"}
        required_fields = {
            "version",
            "cycle",
            "next_start",
            "generation",
        }
        if not legacy_state and state_fields not in (
            required_fields,
            required_fields | {"last_attempted_family"},
        ):
            raise ValueError("rotation state fields mismatch")
        if (
            not legacy_state
            and state.get("version") != _CURRENT_TARGET_ROTATION_STATE_VERSION
        ):
            raise ValueError("rotation state version mismatch")
        state_cycle = state.get("cycle")
        next_start = state.get("next_start")
        generation = 0 if legacy_state else state.get("generation")
        if (
            not isinstance(state_cycle, str)
            or isinstance(next_start, bool)
            or not isinstance(next_start, int)
            or next_start < 0
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 0
        ):
            raise ValueError("rotation state fields invalid")
        last_family = state.get("last_attempted_family")
        if "last_attempted_family" in state and (
            not isinstance(last_family, list)
            or len(last_family) != 3
            or any(not isinstance(value, str) or not value for value in last_family)
        ):
            raise ValueError("rotation last family invalid")
        parsed_cycle = datetime.fromisoformat(state_cycle.replace("Z", "+00:00"))
        if (
            parsed_cycle.tzinfo is None
            or parsed_cycle.utcoffset() is None
            or parsed_cycle.astimezone(UTC).isoformat() != state_cycle
        ):
            raise ValueError("rotation state cycle is not canonical UTC ISO")
        if state_cycle != cycle_key:
            return 0, 0, (state_cycle, generation)
        return next_start, generation, (state_cycle, generation)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"CURRENT_TARGET_ROTATION_STATE_INVALID:{state_path}"
        ) from exc


def _rotation_last_attempted_family(
    state_path: Path, *, cycle_key: str,
) -> tuple[str, str, str] | None:
    if not state_path.exists():
        return None
    state = json.loads(state_path.read_text())
    if state["cycle"] != cycle_key or "last_attempted_family" not in state:
        return None
    family = state["last_attempted_family"]
    return family[0], family[1], family[2]


def _write_rotation_state(
    state_path: Path,
    *,
    cycle_key: str,
    next_start: int,
    generation: int,
    last_attempted_family: tuple[str, str, str] | None = None,
) -> None:
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=state_path.parent,
            prefix=f".{state_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            state: dict[str, object] = {
                "version": _CURRENT_TARGET_ROTATION_STATE_VERSION,
                "cycle": cycle_key,
                "next_start": next_start,
                "generation": generation,
            }
            if last_attempted_family is not None:
                state["last_attempted_family"] = last_attempted_family
            json.dump(state, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, state_path)
        temp_path = None
        directory_fd = os.open(state_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def _current_target_family_key(row: object) -> tuple[str, str, str]:
    return (
        str(getattr(row, "city")),
        str(getattr(row, "target_date")),
        str(getattr(row, "temperature_metric")),
    )


def _current_target_rotation_state_path(
    output_dir: Path,
    rows: Sequence[object],
    *,
    scoped: bool,
) -> Path:
    if not scoped:
        return output_dir / ".current_target_rotation.json"
    scope_identity = json.dumps(
        sorted(_current_target_family_key(row) for row in rows),
        separators=(",", ":"),
    )
    scope_hash = hashlib.sha256(scope_identity.encode("utf-8")).hexdigest()[:16]
    return output_dir / f".current_target_rotation.scoped-{scope_hash}.json"


def _ordered_current_target_rows(
    rows: Sequence[object],
    held_family_priority: dict[tuple[str, str, str], int],
) -> list[object]:
    """Put existing exposure ahead of discovery without dropping either lane."""

    def sort_key(row: object) -> tuple[object, ...]:
        city, target_date, metric = _current_target_family_key(row)
        return (
            held_family_priority.get((city, target_date, metric), 2),
            0 if getattr(row, "missing_openmeteo_manifest", False) else 1,
            0 if not getattr(row, "covered", False) else 1,
            target_date,
            city,
            metric,
        )

    return sorted(rows, key=sort_key)


def _rotate_current_target_rows(
    rows: Sequence[object],
    *,
    cycle: datetime,
    state_path: Path | None = None,
    pinned_prefix_count: int = 0,
    stable_rotation_key: bool = False,
) -> tuple[list[object], int, int, int, _RotationStateToken]:
    ordered = list(rows)
    pinned_count = min(max(0, int(pinned_prefix_count)), len(ordered))
    pinned = ordered[:pinned_count]
    rotating = ordered[pinned_count:]
    cycle_key = cycle.astimezone(UTC).isoformat()
    if not rotating:
        if state_path is None:
            return pinned, 0, 0, 0, (None, None)
        _, generation, state_token = _read_rotation_state(
            state_path,
            cycle_key=cycle_key,
        )
        return pinned, 0, 0, generation, state_token
    with _CURRENT_TARGET_ROTATION_LOCK:
        persisted_start = 0
        generation = 0
        state_token: _RotationStateToken = (None, None)
        if state_path is not None:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            with _rotation_state_lock_path(state_path).open("a+") as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                persisted_start, generation, state_token = _read_rotation_state(
                    state_path,
                    cycle_key=cycle_key,
                )
        start = (
            persisted_start
            if state_path is not None
            else _CURRENT_TARGET_ROTATION_OFFSETS.get(cycle_key, 0)
        ) % len(rotating)
        if stable_rotation_key and state_path is not None:
            last_family = _rotation_last_attempted_family(
                state_path, cycle_key=cycle_key
            )
            if last_family is not None:
                last_key = (last_family[1], last_family[0], last_family[2])
                keys = [
                    (row.target_date, row.city, row.temperature_metric)
                    for row in rotating
                ]
                if last_key in keys:
                    start = (keys.index(last_key) + 1) % len(keys)
                else:
                    start = next(
                        (index for index, key in enumerate(keys) if key > last_key),
                        0,
                    )
        _CURRENT_TARGET_ROTATION_OFFSETS.clear()
        _CURRENT_TARGET_ROTATION_OFFSETS[cycle_key] = start
    return (
        pinned + rotating[start:] + rotating[:start],
        start,
        len(rotating),
        generation,
        state_token,
    )


def _advance_current_target_rotation(
    *,
    cycle: datetime,
    row_count: int,
    attempted_count: int,
    incomplete: bool,
    state_path: Path | None = None,
    expected_generation: int = 0,
    expected_state_token: _RotationStateToken = (None, None),
    stable_rotation_key: bool = False,
    last_attempted_family: tuple[str, str, str] | None = None,
) -> tuple[int, bool]:
    cycle_key = cycle.astimezone(UTC).isoformat()
    if stable_rotation_key and attempted_count > 0 and last_attempted_family is None:
        raise ValueError("stable rotation requires the actual last processed family")

    if row_count <= 0:
        if state_path is not None:
            _read_rotation_state(state_path, cycle_key=cycle_key)
        return 0, False

    def advance_locked() -> tuple[int, bool]:
        if state_path is not None:
            persisted_start, persisted_generation, persisted_state_token = _read_rotation_state(
                state_path,
                cycle_key=cycle_key,
            )
            persisted_cycle = persisted_state_token[0]
            if persisted_cycle is not None and cycle_key != persisted_cycle:
                if cycle_key <= persisted_cycle:
                    return persisted_start, False
                persisted_start = 0
                persisted_generation = 0
            elif persisted_state_token != expected_state_token:
                return persisted_start, False
            if persisted_generation != expected_generation:
                return persisted_start, False
        if not incomplete:
            next_start = 0
        else:
            current_start = (
                persisted_start
                if state_path is not None
                else _CURRENT_TARGET_ROTATION_OFFSETS.get(cycle_key, 0)
            )
            # A timeboxed bucket payload may have decoded and cached only a valid-time
            # prefix while completing zero targets. Keep that target at the head so the
            # next slice can reuse the per-cycle point cache; rotate only past targets
            # whose full payload was processed.
            next_start = (current_start + max(0, int(attempted_count))) % row_count
        _CURRENT_TARGET_ROTATION_OFFSETS.clear()
        _CURRENT_TARGET_ROTATION_OFFSETS[cycle_key] = next_start
        if state_path is not None:
            stable_last = None
            if stable_rotation_key:
                stable_last = (
                    last_attempted_family
                    if attempted_count > 0 else _rotation_last_attempted_family(
                        state_path, cycle_key=cycle_key
                    )
                )
            _write_rotation_state(
                state_path,
                cycle_key=cycle_key,
                next_start=next_start,
                generation=expected_generation + 1,
                last_attempted_family=stable_last,
            )
        return next_start, True

    with _CURRENT_TARGET_ROTATION_LOCK:
        if state_path is not None:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            with _rotation_state_lock_path(state_path).open("a+") as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                return advance_locked()
        return advance_locked()


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


def _precision_metadata(
    city: str, target_date: str, *, anchor_sigma_c: float,
    raw_payload_bytes: bytes,
    analysis_at: datetime | None = None,
) -> dict[str, object]:
    from src.data.openmeteo_ecmwf_ifs9_bucket_transport import (
        same_grid_cell, capture_source_cell_geometry_proof,
    )
    from src.data.openmeteo_ecmwf_ifs9_precision_guard import (
        _haversine_km, grid_surface_elevation_m,
    )
    from src.config import runtime_station_geometry_for_city

    payload = json.loads(raw_payload_bytes)
    if not isinstance(payload, dict):
        raise ValueError("OM9 raw response is not an object")
    city_config = cities_by_name[city]
    if payload.get("timezone") != city_config.timezone:
        raise ValueError("OM9 raw response timezone differs from city contract")
    scope = payload.get("_zeus_current_target_scope")
    if isinstance(scope, dict) and (
        scope.get("city") != city or scope.get("target_date") != target_date
    ):
        raise ValueError("OM9 raw response target scope differs from precision metadata")
    start, end = _local_day_window(city_config.timezone, target_date)
    analysis_at = datetime.now(tz=UTC) if analysis_at is None else analysis_at
    if analysis_at.tzinfo is None or analysis_at.utcoffset() is None:
        raise ValueError("OM9 analysis cut must be timezone-aware")
    station = runtime_station_geometry_for_city(city_config,effective_at=analysis_at)
    if station["validity_reason"] is not None:
        raise ValueError(f"OM9 precise station source: {station['validity_reason']}")
    station_id = str(station["station_id"])
    station_lat = float(station["lat"])
    station_lon = float(station["lon"])
    station_height = station["ground_elevation_m"]
    if not all(math.isfinite(value) for value in (station_lat, station_lon)):
        raise ValueError("OM9 precise station has non-finite geometry")
    if _haversine_km(
        float(city_config.lat), float(city_config.lon), station_lat, station_lon,
    ) > 5.0:
        raise ValueError("OM9 precise station coordinate differs from city contract")
    try:
        response_lat = float(payload["latitude"])
        response_lon = float(payload["longitude"])
        target_dem = float(payload["elevation"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("OM9 raw response geometry invalid") from exc
    source_proof = capture_source_cell_geometry_proof(
        latitude=response_lat, longitude=response_lon,
        target_elevation_m=target_dem,
        requested_latitude=float(city_config.lat), requested_longitude=float(city_config.lon),
    )
    if not math.isfinite(target_dem) or not same_grid_cell(
        response_lat, response_lon,
        float(source_proof["selected_grid_lat"]), float(source_proof["selected_grid_lon"]),
    ):
        raise ValueError("OM9 raw response grid differs from same-source static surface")
    source_proof["raw_payload_sha256"] = hashlib.sha256(raw_payload_bytes).hexdigest()
    source_proof["station_registry_sha256"] = station["registry_sha256"]
    source_proof["station_ground_proof"] = {
        "revision": "station_ground_roles_v1", "status": station["ground_status"],
        "reason": station["ground_reason"], "facts": station["ground_facts"],
        "audit": station["ground_audit"],
    }
    return {
        "city": city,
        "station_id": station_id,
        "city_lat": float(city_config.lat),
        "city_lon": float(city_config.lon),
        "station_lat": station_lat,
        "station_lon": station_lon,
        "requested_lat": float(city_config.lat),
        "requested_lon": float(city_config.lon),
        "requested_coordinate_precision_decimals": 4,
        "nearest_grid_lat": response_lat,
        "nearest_grid_lon": response_lon,
        "nearest_grid_distance_km": _haversine_km(
            float(city_config.lat), float(city_config.lon), response_lat, response_lon,
        ),
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
        "grid_elevation_m": grid_surface_elevation_m(source_proof),
        "station_elevation_m": station_height,
        "land_sea_mask": "sea" if source_proof["cell_is_sea"] else "land",
        "city_class": "coastal" if source_proof["nearby_sea"] else "standard",
        "station_mapping_policy": "settlement_station_reference",
        "source_geometry_proof": source_proof,
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


_LOG = logging.getLogger("zeus.replacement_forecast_current_targets")
SOURCE_GEOMETRY_HEALTH_JOB = "openmeteo_ifs9_source_geometry"
_SOURCE_GEOMETRY_SKIP_PREFIXES = (
    "OM9_SOURCE_GEOMETRY_PROOF_UNAVAILABLE:", "OM9_SOURCE_GEOMETRY_LOCAL_BLOCK:",
)


def _publish_source_geometry_faults(
    skipped: Sequence[Mapping[str, object]], *, certified_cities: set[str],
) -> None:
    """A city whose anchor cell fails certification is a named, latched fault.

    It opens on any failed family and closes only when that city certifies a
    family with none failing, so the city cannot drop out of the anchor table
    without the forecast-pipeline health surface naming it.
    """
    from src.observability.scheduler_health import write_open_faults

    failed: dict[str, str] = {}
    for row in skipped:
        reason = str(row.get("reason") or "")
        if reason.startswith(_SOURCE_GEOMETRY_SKIP_PREFIXES):
            failed.setdefault(str(row.get("city")), reason)
    for city, reason in sorted(failed.items()):
        _LOG.error("OM9 SOURCE GEOMETRY CERTIFICATION FAILED city=%s reason=%s", city, reason)
    write_open_faults(
        SOURCE_GEOMETRY_HEALTH_JOB,
        opened=failed,
        closed=certified_cities - failed.keys(),
    )


def _current_target_source_geometry_check(
    city: str, target_date: str, raw: bytes, *, anchor_sigma_c: float,
) -> tuple[dict[str, object] | None, str | None, bool]:
    """Return a witnessed precision, or a local block / provider-refresh debt.

    Only a malformed/mislocated provider answer can be repaired by fetching the
    same run again. Missing static surface, station truth or a high-risk cell
    blocks this one source target without spending repeated network quota.
    """
    try:
        precision = _precision_metadata(
            city, target_date, anchor_sigma_c=anchor_sigma_c,
            raw_payload_bytes=raw,
        )
        result = evaluate_openmeteo_ecmwf_ifs9_precision_guard(
            OpenMeteoIfs9PrecisionMetadata(**precision), raw_payload_bytes=raw,
            decision_at=datetime.now(tz=UTC),
        )
    except (KeyError, TypeError, ValueError, ImportError, OSError) as exc:
        detail = str(exc)
        return None, detail, isinstance(exc, KeyError) or (
            isinstance(exc, ValueError) and detail.startswith("OM9 raw response")
        )
    if result.passable_for_live_materialization:
        return precision, None, False
    reason = ";".join(result.reason_codes)
    return None, reason, any(
        code.startswith("OM9_SOURCE_RESPONSE_") for code in result.reason_codes
    )


def _current_target_witnessed_cached_path(
    base_path: Path, *, city: str, target_date: str, metric: str,
    city_timezone: str, cycle: datetime, anchor_sigma_c: float,
) -> tuple[Path | None, str | None]:
    """Prefer an exact same-cycle artifact with a current cell/station witness.

    Alternate files preserve old DB SHA paths. A failed repair is not stored;
    the next scheduled pass can try the same provider again, at most once per
    target per pass, without any in-loop retry or wholesale cache deletion.
    """
    local_block: str | None = None
    candidates = (base_path, *sorted(base_path.parent.glob(f"{base_path.stem}.geometry-*.json")))
    for path in candidates:
        if not path.exists():
            continue
        if not _current_target_payload_file_materializable(
            path, city_timezone=city_timezone, target_date=target_date, cycle=cycle,
        ):
            continue
        raw = path.read_bytes()
        scope = json.loads(raw).get("_zeus_current_target_scope")
        if scope != {"city": city, "target_date": target_date, "metric": metric}:
            continue
        _precision, reason, retry_provider = _current_target_source_geometry_check(
            city, target_date, raw, anchor_sigma_c=anchor_sigma_c,
        )
        if _precision is not None:
            return path, None
        if not retry_provider:
            local_block = reason or "OM9_SOURCE_GEOMETRY_PROOF_UNAVAILABLE"
    return None, local_block


def _current_target_artifact_source_proof(
    city: str, target_date: str, metric: str,
    payload_path: Path, precision_path: Path,
    *, expected_sha256: str, expected_byte_size: int,
    decision_time: datetime | None = None,
) -> bool:
    """Validate one DB artifact and its precision witness before coverage counts.

    The registry SHA remains historical audit; a different city's row changing
    must not freeze this station. Its current identity is independently re-read
    by the guard, while every other source-cell field must still reproduce.
    """
    try:
        if decision_time is None or decision_time.tzinfo is None or decision_time.utcoffset() is None:
            return False
        raw = payload_path.read_bytes()
        if len(raw) != expected_byte_size or hashlib.sha256(raw).hexdigest() != expected_sha256:
            return False
        scope = json.loads(raw).get("_zeus_current_target_scope")
        if not isinstance(scope, dict) or scope != {
            "city": city, "target_date": target_date, "metric": metric,
        }:
            return False
        stored = json.loads(precision_path.read_bytes())
        if not isinstance(stored, dict):
            return False
        city_config = cities_by_name[city]
        start, end = _local_day_window(city_config.timezone, target_date)
        if (
            stored.get("city") != city
            or stored.get("target_local_date") != target_date
            or stored.get("timezone_name") != city_config.timezone
            or stored.get("local_day_start_utc") != start.isoformat()
            or stored.get("local_day_end_utc") != end.isoformat()
            or abs(float(stored["city_lat"]) - float(city_config.lat)) > 1e-6
            or abs(float(stored["city_lon"]) - float(city_config.lon)) > 1e-6
            or abs(float(stored["requested_lat"]) - float(city_config.lat)) > 1e-6
            or abs(float(stored["requested_lon"]) - float(city_config.lon)) > 1e-6
        ):
            return False
        return evaluate_openmeteo_ecmwf_ifs9_precision_guard(
            OpenMeteoIfs9PrecisionMetadata(**stored), raw_payload_bytes=raw,
            decision_at=decision_time,
        ).passable_for_live_materialization
    except (OSError, TypeError, ValueError, KeyError, ImportError, AttributeError):
        return False


def _json_file_valid(path: Path) -> bool:
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return True
    except Exception:
        return False


def _current_target_scoped_payload(
    payload: object,
    *,
    city: str,
    target_date: str,
    metric: str,
) -> dict:
    """Bind canonical payload bytes to the target certificate identity.

    One provider city/run payload spans several dates, so its unscoped bytes
    share one SHA. ``raw_forecast_artifacts`` is content-addressed by that SHA,
    while the anchor manifest and precision metadata are target-specific. The
    namespaced scope keeps provider samples unchanged but prevents two valid
    target certificates from collapsing onto one DB row.
    """

    if not isinstance(payload, dict):
        raise TypeError("current-target Open-Meteo payload must be an object")
    scoped = dict(payload)
    scoped["_zeus_current_target_scope"] = {
        "city": str(city),
        "target_date": str(target_date),
        "metric": str(metric),
    }
    return scoped


def _current_target_payload_materializable(
    payload: object,
    *,
    city_timezone: str,
    target_date: str,
    cycle: datetime,
) -> bool:
    """Whether the raw payload can produce the canonical target-day anchor."""

    try:
        extract_openmeteo_ecmwf_ifs9_localday_anchor(
            payload,
            city_timezone=city_timezone,
            target_local_date=date.fromisoformat(target_date),
            source_cycle_time=cycle,
        )
    except (TypeError, ValueError):
        return False
    return True


def anchor_local_proof_covers_owned_window(
    raw: bytes, precision: Mapping[str, object], *, cycle: datetime, decision_at: datetime,
) -> bool:
    """The one coverage law a local proof is written under.

    A day not yet started at ``decision_at`` needs the full local day; a day in
    progress needs the exact remaining-hours suffix (Day0 law).
    """

    try:
        extract_openmeteo_ecmwf_ifs9_localday_anchor(
            json.loads(raw), city_timezone=str(precision["timezone_name"]),
            target_local_date=date.fromisoformat(str(precision["target_local_date"])),
            source_cycle_time=cycle, require_full_localday=True, remaining_from_utc=decision_at,
        )
    except (TypeError, ValueError, KeyError):
        return False
    return True


def _current_target_payload_file_materializable(
    path: Path,
    *,
    city_timezone: str,
    target_date: str,
    cycle: datetime,
    expected_sha256: str | None = None,
    expected_byte_size: int | None = None,
) -> bool:
    try:
        raw = path.read_bytes()
        if expected_byte_size is not None and len(raw) != expected_byte_size:
            return False
        if (
            expected_sha256 is not None
            and hashlib.sha256(raw).hexdigest() != expected_sha256
        ):
            return False
        payload = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return _current_target_payload_materializable(
        payload,
        city_timezone=city_timezone,
        target_date=target_date,
        cycle=cycle,
    )


def _anchor_local_proof_transport(
    local: object,
    *, raw_dir: Path, city: str, target_date: str, metric: str,
    cycle: datetime, decision_time: datetime, restore_missing: bool = False,
) -> bool:
    """Verify, or restore only missing producer-owned transport from sealed proof.

    SCOPE: this output/cycle and exact family. DRAIN: normal producer republishes
    missing transport, not evidence. RESET: complete transport is reused without
    a new proof, capture or source clock; existing bad files remain explicit debt.
    """
    from src.data.replacement_forecast_cycle_policy import anchor_precision_metadata_identity

    if raw_dir.is_symlink() or raw_dir.parent.is_symlink() or dict(local.scope) != {
        "city": city, "target_date": target_date, "metric": metric,
    }:
        raise ValueError("anchor transport is not this producer's output/family")
    body = local.original_body_artifact
    if (datetime.fromisoformat(body["source_cycle_time"]) != cycle
        or any(datetime.fromisoformat(body[key]) > decision_time for key in
               ("source_available_at", "captured_at", "recorded_at"))
        or local.recorded_at > decision_time or local.local_possessed_at > decision_time):
        raise ValueError("anchor transport possession is after decision")
    expected_body = raw_dir / (
        f"openmeteo_{_safe_name(city)}_{target_date}_{metric}_{cycle.strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    owned = Path(local.owned_body["path"])
    suffix = owned.name.removeprefix(f"{expected_body.stem}.geometry-").removesuffix(".json")
    if (owned.is_symlink() or owned.parent.resolve() != raw_dir.resolve()
        or owned.name != expected_body.name and not (
            owned.name == f"{expected_body.stem}.geometry-{suffix}.json"
            and len(suffix) == 12 and all(c in "0123456789abcdef" for c in suffix))):
        raise ValueError("anchor transport body is outside owned cycle path")
    precision_path = anchor_precision_transport_path(owned, local.precision_metadata)
    metadata = {**json.loads(body["artifact_metadata_json"]),
        "openmeteo_payload_json": str(owned), "precision_metadata_json": str(precision_path)}
    metadata["source_run_id"] = metadata.get("source_run_id") or (
        f"raw:{body['source_id']}:{body['data_version']}:{cycle.astimezone(UTC).isoformat()}")
    manifest = RawForecastArtifactManifest(
        source_id=body["source_id"], product_id=body["product_id"], data_version=body["data_version"],
        artifact_path=str(owned), sha256=body["sha256"], byte_size=body["byte_size"],
        source_cycle_time=body["source_cycle_time"], source_available_at=body["source_available_at"],
        captured_at=body["captured_at"], request_url=body["request_url"],
        request_params=json.loads(body["request_params_json"]), product_metadata=metadata,
    )
    manifest.verify_artifact()
    manifest_path = anchor_transport_manifest_path(manifest, raw_dir.parent)
    # Validate every existing file before publishing anything. An existing
    # corrupt/new truth is never overwritten using an older frozen proof.
    missing: list[tuple[Path, object]] = []
    for path, payload in ((precision_path, dict(local.precision_metadata)), (manifest_path, manifest.to_dict())):
        if path.is_symlink():
            raise ValueError("anchor transport path is a symlink")
        if not path.exists():
            missing.append((path, payload))
            continue
        if not path.is_file():
            raise ValueError("anchor transport path is not a regular file")
        actual = json.loads(path.read_bytes())
        if path == precision_path:
            if path.read_bytes() != json.dumps(dict(local.precision_metadata), sort_keys=True,
                    separators=(",", ":"), allow_nan=False).encode():
                raise ValueError("anchor transport precision bytes differ from frozen proof")
            if anchor_precision_metadata_identity(OpenMeteoIfs9PrecisionMetadata(**actual)) != anchor_precision_metadata_identity(
                OpenMeteoIfs9PrecisionMetadata(**local.precision_metadata)):
                raise ValueError("anchor transport precision differs from frozen proof")
        else:
            existing = read_manifest(path)
            existing.verify_artifact()
            existing_meta = dict(existing.product_metadata)
            if (existing.source_id != manifest.source_id or existing.product_id != manifest.product_id
                or existing.data_version != manifest.data_version or existing.source_cycle_time != cycle
                or existing.sha256 != manifest.sha256 or existing.byte_size != manifest.byte_size
                or existing.request_url != manifest.request_url or dict(existing.request_params) != dict(manifest.request_params)
                or Path(existing.artifact_path).resolve() != owned.resolve()
                or existing.source_available_at > decision_time or existing.captured_at > decision_time
                or any(existing_meta.get(key) != value for key, value in local.scope.items())
                or existing_meta.get("openmeteo_payload_json") != str(owned)
                or existing_meta.get("precision_metadata_json") != str(precision_path)):
                raise ValueError("anchor transport manifest differs from canonical proof")
    if missing and not restore_missing:
        return False
    for path, payload in missing:
        # Publish an fsynced complete file with no replacement. A concurrent
        # writer, symlink or ENOSPC cannot be reported as successful restoration.
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                # Re-prove a concurrent complete publication. Existence alone
                # is not progress; partial, corrupt or foreign output is debt.
                if _anchor_local_proof_transport(local,raw_dir=raw_dir,city=city,target_date=target_date,
                    metric=metric,cycle=cycle,decision_time=decision_time):
                    return True
                raise
            directory = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            os.unlink(temporary)
    return True


def _canonical_current_target_reuse(
    forecast_db: Path,
    *,
    cycle: datetime,
    targets: Sequence[object],
    raw_dir: Path,
    anchor_sigma_c: float,
    decision_time: datetime,
    deadline_monotonic: float | None = None,
) -> dict[tuple[str, str, str], int]:
    """Return exact canonical artifacts whose immutable bytes already exist.

    SCOPE: one current-target family at one source cycle. DRAIN: a missing or
    byte-drifted artifact stays outside this map and follows the normal fetch /
    repin path in the same tick. RESET: a new source cycle changes both the DB
    predicate and payload path, so it is fetched normally.

    Reusing a payload must not manufacture a later possession time. The DB row
    already records the first canonical capture; rebuilding its manifest with
    ``datetime.now()`` would move ``source_available_at`` forward on every poll
    and make causal materialization chase a fact that never becomes old enough.
    """
    if not forecast_db.exists() or not targets:
        return {}
    wanted = {_current_target_family_key(row) for row in targets}
    cycle_iso = cycle.astimezone(UTC).isoformat()
    try:
        conn = _connect(forecast_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT artifact_id, data_version, artifact_path, sha256, byte_size,
                   artifact_metadata_json, source_cycle_time
            FROM raw_forecast_artifacts
            WHERE source_id = ?
              AND product_id = ?
              AND source_cycle_time = ?
              AND data_version IN (?, ?)
            ORDER BY artifact_id DESC
            """,
            (
                OPENMETEO_SOURCE_ID,
                OPENMETEO_PRODUCT_ID,
                cycle_iso,
                OPENMETEO_HIGH_DATA_VERSION,
                OPENMETEO_LOW_DATA_VERSION,
            ),
        ).fetchall()
        local_proofs = {}
        from src.data.raw_forecast_artifact_manifest import read_anchor_local_proof
        from src.data.station_ground_evidence import read_current_station_ground_evidence
        from src.data.replacement_current_value_serving import station_ground_target_coverage_for_city
        from src.data.replacement_forecast_cycle_policy import replacement_readiness_expires_at
        for row in rows:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                raise TimeoutError("canonical anchor reuse deadline exceeded")
            try:
                metadata = json.loads(str(row["artifact_metadata_json"] or "{}"))
                key = (str(metadata.get("city") or ""), str(metadata.get("target_date") or ""), str(metadata.get("metric") or ""))
                if key not in wanted:
                    continue
                local = read_anchor_local_proof(conn, int(row["artifact_id"]), city=key[0], target_date=key[1],
                    metric=key[2], decision_at=decision_time, deadline_monotonic=deadline_monotonic)
                if local is None:
                    continue
                local_proofs[int(row["artifact_id"])] = False
                if decision_time >= replacement_readiness_expires_at(cycle):
                    continue
                ground = read_current_station_ground_evidence(forecast_db, city=key[0], decision_at=decision_time)
                if ground is None or station_ground_target_coverage_for_city(ground, city=key[0],
                    target_date=key[1], decision_at=decision_time)["status"] != "VERIFIED":
                    continue
                raw = Path(local.owned_body["path"]).read_bytes()
                if (not evaluate_openmeteo_ecmwf_ifs9_precision_guard(
                    OpenMeteoIfs9PrecisionMetadata(**local.precision_metadata), raw_payload_bytes=raw,
                    decision_at=decision_time, station_ground_evidence=ground,
                ).passable_for_live_materialization or not _current_target_payload_materializable(
                    json.loads(raw), city_timezone=cities_by_name[key[0]].timezone, target_date=key[1], cycle=cycle)):
                    continue
                local_proofs[int(row["artifact_id"])] = local
            except (OSError, TypeError, ValueError, KeyError, UnicodeError):
                # A damaged latest local proof is not replaced by an older
                # sidecar. It remains normal producer repair debt.
                local_proofs[int(row["artifact_id"])] = False
    except (OSError, sqlite3.Error):
        return {}
    finally:
        if "conn" in locals():
            conn.close()

    reused: dict[tuple[str, str, str], int] = {}
    for row in rows:
        try:
            metadata = json.loads(str(row["artifact_metadata_json"] or "{}"))
            key = (
                str(metadata.get("city") or ""),
                str(metadata.get("target_date") or ""),
                str(metadata.get("metric") or ""),
            )
            if key not in wanted or key in reused:
                continue
            city, target_date, metric = key
            local = local_proofs.get(int(row["artifact_id"]))
            if local is False:
                continue
            if local is not None:
                try:
                    _anchor_local_proof_transport(local, raw_dir=raw_dir, city=city, target_date=target_date,
                        metric=metric, cycle=cycle, decision_time=decision_time, restore_missing=True)
                except (OSError, TypeError, ValueError, KeyError) as exc:
                    # Do not turn corrupt transport or a failed atomic publish
                    # into fresh HTTP / an overwrite by the ordinary path.
                    raise RuntimeError(f"anchor transport restoration failed:{key}") from exc
                reused[key] = int(row["artifact_id"])
                continue
            expected_path = raw_dir / (
                f"openmeteo_{_safe_name(city)}_{target_date}_{metric}_"
                f"{cycle.strftime('%Y%m%dT%H%M%SZ')}.json"
            )
            artifact_path = Path(str(row["artifact_path"]))
            if (
                artifact_path.parent.resolve() != raw_dir.resolve()
                or artifact_path.name != expected_path.name
                and not (
                    artifact_path.name.startswith(f"{expected_path.stem}.geometry-")
                    and len(artifact_path.name) == len(expected_path.stem) + len(".geometry-") + 12 + len(".json")
                    and artifact_path.suffix == ".json"
                )
            ):
                continue
            payload_text = str(metadata.get("openmeteo_payload_json") or "").strip()
            precision_text = str(metadata.get("precision_metadata_json") or "").strip()
            if not payload_text or not precision_text:
                continue
            payload_path = Path(payload_text)
            precision_path = Path(precision_text)
            if not payload_path.is_absolute():
                payload_path = raw_dir.parent / payload_path
            if not precision_path.is_absolute():
                precision_path = raw_dir.parent / precision_path
            if payload_path.resolve() != artifact_path.resolve():
                continue
            if not _json_file_valid(payload_path) or not _json_file_valid(precision_path):
                continue
            if not _current_target_artifact_source_proof(
                city, target_date, metric, payload_path, precision_path,
                expected_sha256=str(row["sha256"]),
                expected_byte_size=int(row["byte_size"]),
                decision_time=decision_time,
            ):
                continue
            raw = payload_path.read_bytes()
            payload = json.loads(raw)
            city_config = cities_by_name.get(city)
            if city_config is None or not _current_target_payload_materializable(
                payload,
                city_timezone=city_config.timezone,
                target_date=target_date,
                cycle=cycle,
            ):
                continue
            manifest_path = raw_dir.parent / (
                f"{OPENMETEO_SOURCE_ID}.{row['data_version']}."
                f"{cycle.strftime('%Y%m%dT%H%M%SZ')}."
                f"{str(row['sha256'])[:12]}.{_safe_name(city)}.manifest.json"
            )
            manifest = read_manifest(manifest_path)
            manifest.verify_artifact()
            if (
                manifest.source_id != OPENMETEO_SOURCE_ID
                or manifest.product_id != OPENMETEO_PRODUCT_ID
                or manifest.data_version != str(row["data_version"])
                or manifest.source_cycle_time != cycle.astimezone(UTC)
                or Path(manifest.artifact_path).resolve() != artifact_path.resolve()
                or manifest.sha256 != str(row["sha256"])
                or manifest.byte_size != int(row["byte_size"])
                or dict(manifest.product_metadata) != metadata
            ):
                continue
            reused[key] = int(row["artifact_id"])
        except (OSError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
            continue
    return reused


def _canonical_sibling_payload_reuse(
    forecast_db: Path,
    *,
    cycle: datetime,
    targets: Sequence[object],
    anchor_sigma_c: float = 3.0,
) -> dict[tuple[str, str], tuple[dict, dict[str, object], datetime]]:
    """Reuse one verified hourly payload for a missing target date or metric.

    Open-Meteo serves one run-pinned hourly ``temperature_2m`` payload per
    city/run. HIGH and LOW are distinct downstream data versions, but the same
    payload can cover several target dates and both metrics; a provider quota
    must not strand another materializable date/metric after capture. SCOPE is
    one city/cycle payload plus an explicitly verified target date. DRAIN is
    the normal manifest loop below. RESET is an exact date/metric artifact,
    which then moves the family into ``_canonical_current_target_reuse``.
    """

    if not forecast_db.exists() or not targets:
        return {}
    wanted_by_city: dict[str, list[tuple[str, str]]] = {}
    for target in targets:
        wanted_by_city.setdefault(str(target.city), []).append(
            (str(target.target_date), str(target.temperature_metric))
        )
    cycle_iso = cycle.astimezone(UTC).isoformat()
    try:
        conn = _connect(forecast_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT artifact_id, data_version, artifact_path, sha256, byte_size,
                   captured_at, artifact_metadata_json
            FROM raw_forecast_artifacts
            WHERE source_id = ?
              AND product_id = ?
              AND source_cycle_time = ?
              AND data_version IN (?, ?)
            ORDER BY artifact_id DESC
            """,
            (
                OPENMETEO_SOURCE_ID,
                OPENMETEO_PRODUCT_ID,
                cycle_iso,
                OPENMETEO_HIGH_DATA_VERSION,
                OPENMETEO_LOW_DATA_VERSION,
            ),
        ).fetchall()
    except (OSError, sqlite3.Error):
        return {}
    finally:
        if "conn" in locals():
            conn.close()

    reused: dict[tuple[str, str], tuple[dict, dict[str, object], datetime]] = {}
    excluded_metadata = {
        "artifact_class",
        "cities",
        "city",
        "target_date",
        "target_dates",
        "metric",
        "source_run_id",
        "openmeteo_payload_json",
        "precision_metadata_json",
    }
    for row in rows:
        try:
            metadata = json.loads(str(row["artifact_metadata_json"] or "{}"))
            city = str(metadata.get("city") or "")
            sibling_metric = str(metadata.get("metric") or "")
            row_metric = (
                "high"
                if str(row["data_version"]) == OPENMETEO_HIGH_DATA_VERSION
                else "low"
                if str(row["data_version"]) == OPENMETEO_LOW_DATA_VERSION
                else ""
            )
            city_targets = wanted_by_city.get(city, ())
            if not city_targets or sibling_metric != row_metric:
                continue
            artifact_path = Path(str(row["artifact_path"]))
            raw = artifact_path.read_bytes()
            if (
                len(raw) != int(row["byte_size"])
                or hashlib.sha256(raw).hexdigest() != str(row["sha256"])
            ):
                continue
            payload = json.loads(raw)
            city_config = cities_by_name.get(city)
            if city_config is None:
                continue
            captured_at = datetime.fromisoformat(
                str(row["captured_at"]).replace("Z", "+00:00")
            ).astimezone(UTC)
            provenance = {
                name: value
                for name, value in metadata.items()
                if name not in excluded_metadata
            }
            provenance["raw_metric_sibling_reuse"] = sibling_metric
            provenance["raw_target_date_sibling_reuse"] = str(
                metadata.get("target_date") or ""
            )
            for target_date, wanted_metric in city_targets:
                key = (city, target_date)
                if (
                    key in reused
                    or wanted_metric not in {"high", "low"}
                    or not _current_target_payload_materializable(
                        payload,
                        city_timezone=city_config.timezone,
                        target_date=target_date,
                        cycle=cycle,
                    )
                ):
                    continue
                rescoped = _current_target_scoped_payload(
                    payload, city=city, target_date=target_date, metric=wanted_metric,
                )
                raw_scoped = (json.dumps(rescoped, indent=2, sort_keys=True, default=str) + "\n").encode()
                witnessed, _reason, _retry = _current_target_source_geometry_check(
                    city, target_date, raw_scoped, anchor_sigma_c=anchor_sigma_c,
                )
                if witnessed is None:
                    continue
                reused[key] = (payload, dict(provenance), captured_at)
        except (OSError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
            continue
    return reused


def _write_manifest_file(output_dir: Path, manifest: RawForecastArtifactManifest) -> Path:
    if (manifest.source_id == OPENMETEO_SOURCE_ID
            and ".precision-" in str(manifest.product_metadata.get("precision_metadata_json", ""))):
        return publish_anchor_transport_manifest(manifest, output_dir)
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


def _deadline_timeout_preserving_bucket_fallback(
    deadline_monotonic: float | None,
    *,
    default: float,
    bucket_fallback_reserve_seconds: float = _MAX_BUCKET_FALLBACK_RESERVE_SECONDS,
) -> float:
    """Bound one provider rung without consuming the bucket fallback window.

    The run-pinned and metadata-stamped APIs must not starve the independently
    admitted exact-run bucket transport. A deadline-expired provider rung is a
    retryable transport failure; callers continue at the bucket rung.
    """
    if deadline_monotonic is None:
        return default
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= bucket_fallback_reserve_seconds:
        raise TimeoutError("current-target deadline reserved for bucket fallback")
    return max(0.001, min(default, remaining - bucket_fallback_reserve_seconds))


def _bucket_fallback_reserve_seconds(
    max_wall_clock_seconds: float | None,
) -> float:
    """Reserve a bounded final-third for the bucket rung of one download call."""
    if max_wall_clock_seconds is None:
        return _MAX_BUCKET_FALLBACK_RESERVE_SECONDS
    return min(
        _MAX_BUCKET_FALLBACK_RESERVE_SECONDS,
        max(0.0, float(max_wall_clock_seconds)) / 3.0,
    )


def _anchor_wave_key(
    key: tuple[str, str],
    request: object,
) -> tuple[str, str, str]:
    return (key[0], key[1], request.run.isoformat())


def _submit_anchor_wave_requests(
    requests: dict[tuple[str, str], object],
    *,
    fetch_payload: Callable[[object], dict],
) -> tuple[dict[Future, tuple[str, str]], dict[tuple[str, str], Exception]]:
    """Submit only unowned anchor requests to the process-bounded worker pool.

    Completed results are consumed only in the invocation that submitted them.
    A future still running after that invocation's deadline remains registered;
    later ticks defer its exact city/date/cycle instead of creating another
    request. Its late result is discarded before any payload/DB write.
    """
    global _ANCHOR_WAVE_EXECUTOR

    submitted: dict[Future, tuple[str, str]] = {}
    deferred: dict[tuple[str, str], Exception] = {}
    with _ANCHOR_WAVE_LOCK:
        for key, future in tuple(_ANCHOR_WAVE_INFLIGHT.items()):
            if future.done():
                _ANCHOR_WAVE_INFLIGHT.pop(key, None)
        available_slots = _ANCHOR_WAVE_MAX_INFLIGHT - len(_ANCHOR_WAVE_INFLIGHT)
        for key, request in requests.items():
            wave_key = _anchor_wave_key(key, request)
            if wave_key in _ANCHOR_WAVE_INFLIGHT:
                deferred[key] = RuntimeError(
                    "meta-stamped anchor request still in flight for this city/date/cycle"
                )
                continue
            if available_slots <= 0:
                deferred[key] = RuntimeError(
                    "meta-stamped anchor worker capacity is occupied by earlier requests"
                )
                continue
            if _ANCHOR_WAVE_EXECUTOR is None:
                _ANCHOR_WAVE_EXECUTOR = ThreadPoolExecutor(
                    max_workers=_ANCHOR_WAVE_MAX_INFLIGHT,
                    thread_name_prefix="openmeteo-anchor",
                )
            future = _ANCHOR_WAVE_EXECUTOR.submit(fetch_payload, request)
            _ANCHOR_WAVE_INFLIGHT[wave_key] = future
            submitted[future] = key
            available_slots -= 1
    return submitted, deferred


def _discard_finished_anchor_wave_requests(
    futures: dict[Future, tuple[str, str]],
    requests: dict[tuple[str, str], object],
) -> None:
    """Drop only completed futures; running workers retain their no-duplicate lease."""
    with _ANCHOR_WAVE_LOCK:
        for future, key in futures.items():
            wave_key = _anchor_wave_key(key, requests[key])
            if future.done() and _ANCHOR_WAVE_INFLIGHT.get(wave_key) is future:
                _ANCHOR_WAVE_INFLIGHT.pop(wave_key, None)


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
    bucket_read_point: Callable[[str, int], float] | None = None,
    bucket_read_workers: int = 1,
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
    # Both rungs report the API's response geometry, whose ``elevation`` is the
    # requested point's 90m DEM (captured once per request point, cached with
    # provenance) — the SAME authority that VERIFIED the city and that the precision
    # guard binds to the frozen O1280 cell proof. An uncapturable DEM (provider quota
    # block, transport failure) leaves this city unserved this cycle, never guessed.
    try:
        target_elev = capture_city_target_elevation(
            city,
            request.latitude,
            request.longitude,
            timeout=_deadline_timeout(deadline_monotonic, default=20.0),
        )
    except (ValueError, httpx.HTTPError) as dem_exc:
        raise BucketTransportNotAdmissible(
            f"city {city} target DEM unavailable: {str(dem_exc)[:160]}"
        ) from dem_exc
    try:
        if serve_method == "downscaled":
            result = fetch_bucket_anchor_payload_downscaled(  # re-checks admission internally
                latitude=request.latitude,
                longitude=request.longitude,
                target_elevation_m=target_elev,
                run=request.run,
                timezone_name=timezone_name,
                needed_valid_times=needed,
                manifest=manifest,
                read_point=bucket_read_point,
                read_workers=bucket_read_workers,
                deadline_monotonic=deadline_monotonic,
            )
        else:  # "raw"
            result = fetch_bucket_anchor_payload(  # re-checks admission (condition 2) internally
                latitude=request.latitude,
                longitude=request.longitude,
                target_elevation_m=target_elev,
                run=request.run,
                timezone_name=timezone_name,
                needed_valid_times=needed,
                manifest=manifest,
                read_point=bucket_read_point,
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


def _resolve_anchor_payload(
    *,
    request,
    city: str,
    target_date: str,
    timezone_name: str,
    deadline_monotonic: float | None = None,
    bucket_fallback_reserve_seconds: float = _MAX_BUCKET_FALLBACK_RESERVE_SECONDS,
    bucket_manifest_provider: Callable[[], dict] | None = None,
    bucket_read_point: Callable[[str, int], float] | None = None,
    bucket_read_workers: int = 1,
    client: httpx.Client | None = None,
    meta_wave_failure: Exception | None = None,
    single_runs_run_refusals: set | None = None,
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
    elif (
        single_runs_run_refusals is not None
        and request.run.isoformat() in single_runs_run_refusals
    ):
        # A sibling city already got HTTP 400 for this exact run this pass:
        # the API refusal is run-scoped, not city-scoped, so re-asking per
        # city only converts one refusal into a metered 400 per city.
        single_runs_exc = RuntimeError(
            "single-runs rung skipped: run refused 400 for a sibling city this pass"
        )
    elif _single_runs_public_for_request(request):
        try:
            kwargs: dict[str, object] = {"fast_fail_429": True}
            if client is not None:
                kwargs["client"] = client
            if deadline_monotonic is not None:
                kwargs.update(
                    timeout=_deadline_timeout_preserving_bucket_fallback(
                        deadline_monotonic,
                        default=30.0,
                        bucket_fallback_reserve_seconds=bucket_fallback_reserve_seconds,
                    ),
                    max_retries=1,
                )
            payload = fetch_openmeteo_ecmwf_ifs9_anchor_payload(request, **kwargs)
            if _current_target_payload_materializable(
                payload,
                city_timezone=timezone_name,
                target_date=target_date,
                cycle=request.run,
            ):
                return payload, {
                    "openmeteo_endpoint": "single_runs_api",
                    "run_authority": "run_pinned_single_runs",
                }
            single_runs_exc = ValueError(
                "single-runs payload has no finite target-day sample"
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code != 400 and status_code != 429 and status_code < 500:
                raise
            if status_code == 400 and single_runs_run_refusals is not None:
                single_runs_run_refusals.add(request.run.isoformat())
            # `except ... as` unbinds the name at block exit; persist it for rungs 2/3.
            single_runs_exc = exc
        except (RuntimeError, TimeoutError, httpx.TransportError) as exc:
            if (
                not isinstance(exc, (TimeoutError, httpx.TransportError))
                and not _is_transient_provider_failure(exc)
            ):
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
                    timeout=_deadline_timeout_preserving_bucket_fallback(
                        deadline_monotonic,
                        default=30.0,
                        bucket_fallback_reserve_seconds=bucket_fallback_reserve_seconds,
                    ),
                    max_retries=1,
                )
            payload, meta_provenance = fetch_openmeteo_ecmwf_ifs9_anchor_payload_meta_stamped(
                request, **kwargs
            )
            if _current_target_payload_materializable(
                payload,
                city_timezone=timezone_name,
                target_date=target_date,
                cycle=request.run,
            ):
                provenance = dict(meta_provenance)
                provenance["single_runs_fallback_reason"] = _exception_summary(single_runs_exc)
                return payload, provenance
            rung2_reason = ValueError(
                "meta-stamped payload has no finite target-day sample"
            )
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
        except (TimeoutError, ValueError, httpx.TransportError) as meta_exc:
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
    if bucket_read_point is not None:
        rung_three_kwargs["bucket_read_point"] = bucket_read_point
    if bucket_read_workers != 1:
        rung_three_kwargs["bucket_read_workers"] = bucket_read_workers
    return _try_bucket_rung_three(
        **rung_three_kwargs,
    )


def _fetch_meta_stamped_anchor_wave(
    requests: dict[tuple[str, str], object],
    *,
    max_workers: int,
    deadline_monotonic: float | None,
    bucket_fallback_reserve_seconds: float = _MAX_BUCKET_FALLBACK_RESERVE_SECONDS,
    client: httpx.Client,
    quota_critical: bool = False,
    quota_priority: bool = False,
) -> tuple[
    dict[tuple[str, str], tuple[dict, dict[str, object], datetime]],
    dict[tuple[str, str], Exception],
]:
    """Fetch a CURRENT-run city wave under one provider metadata bracket."""
    if not requests:
        return {}, {}
    request0 = next(iter(requests.values()))
    timeout = _deadline_timeout_preserving_bucket_fallback(
        deadline_monotonic,
        default=30.0,
        bucket_fallback_reserve_seconds=bucket_fallback_reserve_seconds,
    )
    quota_context = (
        quota_tracker.critical_lane()
        if quota_critical
        else quota_tracker.priority_lane()
        if quota_priority
        else contextlib.nullcontext()
    )
    with quota_context:
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
    wave_requests = requests
    if deadline_monotonic is not None:
        # Submit one worker-width wave so this bounded live slice cannot multiply
        # its wall-clock budget by the number of queued city/date targets.
        # Unattempted targets remain absent and are reconsidered next slice.
        wave_requests = dict(list(requests.items())[:workers])
    def _fetch_payload(request):
        quota_context = (
            quota_tracker.critical_lane()
            if quota_critical
            else quota_tracker.priority_lane()
            if quota_priority
            else contextlib.nullcontext()
        )
        with quota_context:
            return fetch_openmeteo_ecmwf_ifs9_anchor_payload_standard_unstamped(
                request,
                timeout=_deadline_timeout_preserving_bucket_fallback(
                    deadline_monotonic,
                    default=30.0,
                    bucket_fallback_reserve_seconds=bucket_fallback_reserve_seconds,
                ),
                max_retries=1,
                fast_fail_429=True,
                client=client,
            )

    future_keys, deferred = _submit_anchor_wave_requests(
        wave_requests,
        fetch_payload=_fetch_payload,
    )
    failures.update(deferred)
    try:
        for future in as_completed(
            future_keys,
            timeout=(
                None
                if deadline_monotonic is None
                else _deadline_timeout_preserving_bucket_fallback(
                    deadline_monotonic,
                    default=30.0,
                    bucket_fallback_reserve_seconds=bucket_fallback_reserve_seconds,
                )
            ),
        ):
            key = future_keys[future]
            try:
                payload = dict(future.result())
                request = wave_requests[key]
                if not _current_target_payload_materializable(
                    payload,
                    city_timezone=request.timezone_name,
                    target_date=key[1],
                    cycle=request.run,
                ):
                    raise ValueError(
                        "meta-stamped payload has no finite target-day sample"
                    )
                payloads[key] = (payload, datetime.now(tz=UTC))
            except Exception as exc:  # each city retains its independent bucket fallback
                failures[key] = exc
    except TimeoutError as exc:
        for future, key in future_keys.items():
            if not future.done():
                failures[key] = exc
    finally:
        # Running workers stay process-bounded and leased to their exact
        # city/date/cycle. They cannot write late results, and future ticks
        # defer instead of creating duplicate HTTP requests.
        _discard_finished_anchor_wave_requests(future_keys, wave_requests)

    if not payloads:
        return {}, failures

    quota_context = (
        quota_tracker.critical_lane()
        if quota_critical
        else quota_tracker.priority_lane()
        if quota_priority
        else contextlib.nullcontext()
    )
    with quota_context:
        meta_after = fetch_openmeteo_ifs9_model_meta(
            timeout=_deadline_timeout_preserving_bucket_fallback(
                deadline_monotonic,
                default=20.0,
                bucket_fallback_reserve_seconds=bucket_fallback_reserve_seconds,
            ),
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


def _fetch_run_pinned_anchor_wave(
    requests: dict[tuple[str, str], object],
    *,
    deadline_monotonic: float | None,
    bucket_fallback_reserve_seconds: float = _MAX_BUCKET_FALLBACK_RESERVE_SECONDS,
    client: httpx.Client,
    quota_critical: bool = False,
    quota_priority: bool = False,
) -> dict[tuple[str, str], tuple[dict, dict[str, object], datetime]]:
    """Fetch every city/date anchor in one run-pinned multi-location call."""

    items = tuple(requests.items())
    if not items:
        return {}
    quota_context = (
        quota_tracker.critical_lane()
        if quota_critical
        else quota_tracker.priority_lane()
        if quota_priority
        else contextlib.nullcontext()
    )
    with quota_context:
        payloads = fetch_openmeteo_ecmwf_ifs9_anchor_payloads(
            tuple(request for _, request in items),
            timeout=_deadline_timeout_preserving_bucket_fallback(
                deadline_monotonic,
                default=30.0,
                bucket_fallback_reserve_seconds=bucket_fallback_reserve_seconds,
            ),
            max_retries=1,
            fast_fail_429=True,
            client=client,
        )
    captured_at = datetime.now(tz=UTC)
    resolved: dict[tuple[str, str], tuple[dict, dict[str, object], datetime]] = {}
    for (key, request), payload in zip(items, payloads, strict=True):
        raw_payload = dict(payload)
        if not _current_target_payload_materializable(
            raw_payload,
            city_timezone=request.timezone_name,
            target_date=key[1],
            cycle=request.run,
        ):
            continue
        resolved[key] = (
            raw_payload,
            {
                "openmeteo_endpoint": "single_runs_api",
                "run_authority": "run_pinned_single_runs",
                "location_batch_size": len(items),
            },
            captured_at,
        )
    return resolved


def _dedupe_pending_anchor_requests(
    requests: dict[tuple[str, str], object],
) -> tuple[
    dict[tuple[str, str], object],
    dict[tuple[str, str], tuple[tuple[str, str], ...]],
]:
    """Fetch each identical city/run request once, then fan it out by target date."""

    representative_by_request: dict[object, tuple[str, str]] = {}
    representatives: dict[tuple[str, str], object] = {}
    fanout: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for target_key, request in requests.items():
        representative = representative_by_request.get(request)
        if representative is None:
            representative = target_key
            representative_by_request[request] = representative
            representatives[representative] = request
            fanout[representative] = []
        fanout[representative].append(target_key)
    return representatives, {
        representative: tuple(target_keys)
        for representative, target_keys in fanout.items()
    }


def _fan_out_anchor_payloads(
    resolved: dict[tuple[str, str], tuple[dict, dict[str, object], datetime]],
    *,
    requests: dict[tuple[str, str], object],
    fanout: dict[tuple[str, str], tuple[tuple[str, str], ...]],
) -> dict[tuple[str, str], tuple[dict, dict[str, object], datetime]]:
    expanded: dict[tuple[str, str], tuple[dict, dict[str, object], datetime]] = {}
    for representative, target_keys in fanout.items():
        fetched = resolved.get(representative)
        if fetched is None:
            continue
        payload, provenance, captured_at = fetched
        request = requests[representative]
        for target_key in target_keys:
            if not _current_target_payload_materializable(
                payload,
                city_timezone=request.timezone_name,
                target_date=target_key[1],
                cycle=request.run,
            ):
                continue
            expanded[target_key] = (
                payload,
                {
                    **provenance,
                    "request_payload_fanout_count": len(target_keys),
                },
                captured_at,
            )
    return expanded


def _expand_required_metric_siblings(
    forecast_db: Path,
    scopes: Sequence[tuple[str, str, str]],
) -> tuple[tuple[str, str, str], ...]:
    """Close active HIGH/LOW twins before spending a city/run request.

    One run-pinned hourly payload is the raw input for both extrema.  A scoped
    recovery that requests only one active metric must persist its active twin
    in the same pass; otherwise the local target rotation can strand usable
    bytes until a later cycle.  Missing ``market_events`` keeps the caller's
    exact scope unchanged for isolated fixtures and non-market repair use.
    """

    normalized = tuple(
        dict.fromkeys(
            (
                str(city).strip(),
                str(target_date).strip(),
                str(metric).strip(),
            )
            for city, target_date, metric in scopes
            if str(city).strip()
            and str(target_date).strip()
            and str(metric).strip() in {"high", "low"}
        )
    )
    if not normalized or not forecast_db.exists():
        return normalized
    try:
        conn = _connect(forecast_db)
        active_scopes = {
            (str(city), str(target_date), str(metric))
            for city, target_date, metric in conn.execute(
                """
                SELECT DISTINCT city, target_date, temperature_metric
                  FROM market_events
                 WHERE temperature_metric IN ('high', 'low')
                """
            ).fetchall()
        }
    except (OSError, sqlite3.Error):
        return normalized
    finally:
        if "conn" in locals():
            conn.close()

    expanded = list(normalized)
    seen = set(normalized)
    for city, target_date, metric in normalized:
        sibling = (city, target_date, "low" if metric == "high" else "high")
        if sibling in active_scopes and sibling not in seen:
            expanded.append(sibling)
            seen.add(sibling)
    return tuple(expanded)


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
    required_scopes: Sequence[tuple[str, str, str]] | None = None,
    expand_metric_siblings: bool = True,
    scoped_rotation: bool = True,
    stable_rotation_key: bool = False,
    max_wall_clock_seconds: float | None = None,
    fetch_workers: int = 4,
    bucket_reader_pool=None,
    quota_critical: bool = False,
    quota_priority: bool = False,
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
    if limit is not None and (isinstance(limit, bool) or limit <= 0):
        raise ValueError("limit must be a positive integer or None")
    plan: ReplacementForecastCurrentTargetPlan | None = None
    if required_scopes is not None:
        if expand_metric_siblings:
            required_scopes = _expand_required_metric_siblings(
                forecast_db,
                required_scopes,
            )
        _rows = [
            ReplacementForecastTargetKey(city, target_date, metric)
            for city, target_date, metric in dict.fromkeys(
                (
                    str(city).strip(),
                    str(target_date).strip(),
                    str(metric).strip(),
                )
                for city, target_date, metric in required_scopes
                if str(city).strip()
                and str(target_date).strip()
                and str(metric).strip() in {"high", "low"}
            )
        ]
        _rows.sort(key=lambda row: (row.target_date, row.city, row.temperature_metric))
    else:
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
    from src.data.replacement_forecast_seed_discovery import (
        held_position_family_priorities,
    )

    held_family_priority = held_position_family_priorities()
    _rows = _ordered_current_target_rows(
        _rows,
        held_family_priority,
    )
    held_priority_row_count = sum(
        _current_target_family_key(row) in held_family_priority
        for row in _rows
    )
    # Pin existing exposure only when the configured slice can still carry at
    # least one ordinary family.  A smaller slice keeps the old full-universe
    # rotation so priority cannot turn into permanent background starvation.
    priority_row_count = (
        held_priority_row_count
        if limit is None or held_priority_row_count < int(limit)
        else 0
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    rotation_state_path = _current_target_rotation_state_path(
        output_dir,
        _rows,
        scoped=required_scopes is not None and scoped_rotation,
    )
    (
        rotated_rows,
        rotation_start,
        rotation_row_count,
        rotation_generation,
        rotation_state_token,
    ) = _rotate_current_target_rows(
        _rows,
        cycle=cycle,
        state_path=rotation_state_path,
        pinned_prefix_count=priority_row_count,
        stable_rotation_key=stable_rotation_key,
    )
    targets = rotated_rows[:limit] if limit is not None else rotated_rows
    raw_dir = output_dir / cycle.strftime("%Y%m%dT%H%M%SZ")
    raw_dir.mkdir(parents=True, exist_ok=True)
    deadline_monotonic = (
        time.monotonic() + max(0.0, float(max_wall_clock_seconds))
        if max_wall_clock_seconds is not None
        else None
    )
    reuse_decision_time = datetime.now(tz=UTC)
    canonical_reuse = (
        _canonical_current_target_reuse(
            forecast_db,
            cycle=cycle,
            targets=targets,
            raw_dir=raw_dir,
            anchor_sigma_c=anchor_sigma_c,
            decision_time=reuse_decision_time,
            deadline_monotonic=deadline_monotonic,
        )
        if write_db
        else {}
    )
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
        "openmeteo_intra_wave_fanout_count": 0,
        "openmeteo_single_runs_location_batch_count": 0,
    }
    bucket_fallback_reserve_seconds = _bucket_fallback_reserve_seconds(
        max_wall_clock_seconds
    )
    resolved_payloads: dict[
        tuple[str, str], tuple[dict, dict[str, object], datetime]
    ] = _canonical_sibling_payload_reuse(
        forecast_db,
        cycle=cycle,
        targets=tuple(
            target
            for target in targets
            if _current_target_family_key(target) not in canonical_reuse
        ),
        anchor_sigma_c=anchor_sigma_c,
    )
    sibling_payload_reuse_count = len(resolved_payloads)
    meta_wave_failures: dict[tuple[str, str], Exception] = {}
    unavailable_targets: set[tuple[str, str]] = set()
    processed_target_count = 0
    rotating_processed_count = 0
    rotating_family_keys = {
        _current_target_family_key(row) for row in rotated_rows[priority_row_count:]
    } if stable_rotation_key else set()
    last_processed_rotating_family: tuple[str, str, str] | None = None

    def mark_processed(target: object) -> None:
        nonlocal processed_target_count, rotating_processed_count
        nonlocal last_processed_rotating_family
        processed_target_count += 1
        family = _current_target_family_key(target)
        if family in rotating_family_keys:
            rotating_processed_count += 1
            last_processed_rotating_family = family

    timeboxed_incomplete = False
    bucket_manifests: dict | None = None

    from src.data.openmeteo_ecmwf_ifs9_bucket_transport import (
        BucketTransportNotAdmissible,
        fetch_bucket_run_manifest,
        source_geometry_static_prerequisite_reason,
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
    witnessed_cached_paths: dict[tuple[str, str, str], Path] = {}
    source_local_blocks: dict[tuple[str, str, str], str] = {}
    static_block = source_geometry_static_prerequisite_reason()
    for target in targets:
        city_config = cities_by_name.get(target.city)
        if city_config is None:
            continue
        if _current_target_family_key(target) in canonical_reuse:
            continue
        if static_block is not None:
            source_local_blocks[_current_target_family_key(target)] = static_block
            continue
        target_key = (target.city, target.target_date)
        if target_key in resolved_payloads:
            continue
        payload_path = raw_dir / f"openmeteo_{_safe_name(target.city)}_{target.target_date}_{target.temperature_metric}_{cycle.strftime('%Y%m%dT%H%M%SZ')}.json"
        cached_path, local_block = _current_target_witnessed_cached_path(
            payload_path, city=target.city, target_date=target.target_date,
            metric=target.temperature_metric,
            city_timezone=city_config.timezone, cycle=cycle,
            anchor_sigma_c=anchor_sigma_c,
        )
        if cached_path is not None:
            witnessed_cached_paths[_current_target_family_key(target)] = cached_path
            continue
        if local_block is not None:
            source_local_blocks[_current_target_family_key(target)] = local_block
            continue
        pending_requests.setdefault(
            target_key,
            build_anchor_request(
                latitude=float(city_config.lat),
                longitude=float(city_config.lon),
                run=cycle,
                timezone_name=city_config.timezone,
                forecast_hours=120,
                past_hours=CURRENT_RUN_CONTEXT_HOURS,
            ),
        )
    representative_requests, request_fanout = _dedupe_pending_anchor_requests(
        pending_requests
    )

    openmeteo_client = httpx.Client()
    first_request = next(iter(pending_requests.values()), None)
    single_runs_public = (
        first_request is not None
        and _single_runs_public_for_request(first_request)
    )
    single_runs_wave_failure: Exception | None = None
    metered_quota_context = (
        quota_tracker.critical_lane()
        if quota_critical
        else quota_tracker.priority_lane()
        if quota_priority
        else contextlib.nullcontext()
    )
    with metered_quota_context:
        metered_anchor_quota_available = (
            not pending_requests or quota_tracker.can_call()
        )
    downloaded["openmeteo_metered_quota_available"] = (
        metered_anchor_quota_available
    )
    if pending_requests and not metered_anchor_quota_available:
        quota_skip = RuntimeError(
            "metered Open-Meteo anchor quota unavailable; using independent bucket rung"
        )
        single_runs_wave_failure = quota_skip
        meta_wave_failures = {key: quota_skip for key in pending_requests}

    if (
        metered_anchor_quota_available
        and single_runs_public
        and len(pending_requests) > 1
    ):
        try:
            fetched_wave = _fetch_run_pinned_anchor_wave(
                representative_requests,
                deadline_monotonic=deadline_monotonic,
                bucket_fallback_reserve_seconds=bucket_fallback_reserve_seconds,
                client=openmeteo_client,
                quota_critical=quota_critical,
                quota_priority=quota_priority,
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code != 400 and status_code != 429 and status_code < 500:
                raise
            single_runs_wave_failure = exc
        except (RuntimeError, TimeoutError, httpx.TransportError) as exc:
            if not isinstance(exc, (TimeoutError, httpx.TransportError)) and not _is_transient_provider_failure(exc):
                raise
            single_runs_wave_failure = exc
        else:
            wave_resolved = _fan_out_anchor_payloads(
                fetched_wave,
                requests=pending_requests,
                fanout=request_fanout,
            )
            resolved_payloads.update(wave_resolved)
            downloaded["openmeteo_transport_fetch_count"] = len(fetched_wave)
            downloaded["openmeteo_wave_payload_count"] = len(fetched_wave)
            downloaded["openmeteo_intra_wave_fanout_count"] = max(
                0, len(wave_resolved) - len(fetched_wave)
            )
        downloaded["openmeteo_single_runs_location_batch_count"] = 1

    if (
        metered_anchor_quota_available
        and pending_requests
        and (not single_runs_public or single_runs_wave_failure is not None)
    ):
        try:
            fetched_wave, representative_failures = _fetch_meta_stamped_anchor_wave(
                representative_requests,
                max_workers=fetch_workers,
                deadline_monotonic=deadline_monotonic,
                bucket_fallback_reserve_seconds=bucket_fallback_reserve_seconds,
                client=openmeteo_client,
                quota_critical=quota_critical,
                quota_priority=quota_priority,
            )
            downloaded["openmeteo_model_meta_fetch_count"] = 2
        except Exception as exc:
            meta_wave_failures = {key: exc for key in pending_requests}
            wave_resolved = {}
            fetched_wave = {}
            downloaded["openmeteo_model_meta_fetch_count"] = 1
        else:
            wave_resolved = _fan_out_anchor_payloads(
                fetched_wave,
                requests=pending_requests,
                fanout=request_fanout,
            )
            meta_wave_failures = {
                target_key: error
                for representative, error in representative_failures.items()
                for target_key in request_fanout.get(representative, (representative,))
            }
        if single_runs_wave_failure is not None:
            reason = (
                f"{type(single_runs_wave_failure).__name__}: "
                f"{str(single_runs_wave_failure)[:160]}"
            )
            wave_resolved = {
                key: (
                    payload,
                    {**provenance, "single_runs_fallback_reason": reason},
                    captured_at,
                )
                for key, (payload, provenance, captured_at) in wave_resolved.items()
            }
        resolved_payloads.update(wave_resolved)
        downloaded["openmeteo_transport_fetch_count"] = len(fetched_wave)
        downloaded["openmeteo_wave_payload_count"] = len(fetched_wave)
        downloaded["openmeteo_intra_wave_fanout_count"] = max(
            0, len(wave_resolved) - len(fetched_wave)
        )

    from src.data.openmeteo_ecmwf_ifs9_bucket_transport import (
        BucketPointReaderPool,
        record_city_target_elevation,
    )

    owns_bucket_pool = bucket_reader_pool is None
    bucket_pool = (
        bucket_reader_pool if bucket_reader_pool is not None else BucketPointReaderPool()
    )
    # Runs the single-runs API refused with 400 during THIS pass. The refusal
    # is run-scoped: once one city sees it, every sibling city skips rung 1
    # instead of paying one metered 400 each (~146x per cycle transition).
    single_runs_run_refusals: set = set()
    try:
        for target in targets:
            target_key = (target.city, target.target_date)
            city_config = cities_by_name.get(target.city)
            if city_config is None:
                mark_processed(target)
                continue
            family_key = _current_target_family_key(target)
            if family_key in canonical_reuse:
                mark_processed(target)
                continue
            base_payload_path = raw_dir / f"openmeteo_{_safe_name(target.city)}_{target.target_date}_{target.temperature_metric}_{cycle.strftime('%Y%m%dT%H%M%SZ')}.json"
            payload_path = witnessed_cached_paths.get(family_key, base_payload_path)
            precision_path = raw_dir / f"openmeteo_precision_{_safe_name(target.city)}_{target.target_date}_{target.temperature_metric}.json"
            if family_key in source_local_blocks:
                skipped_cities.append({
                    "city": target.city, "target_date": target.target_date,
                    "metric": target.temperature_metric,
                    "reason": f"OM9_SOURCE_GEOMETRY_LOCAL_BLOCK:{source_local_blocks[family_key]}",
                })
                mark_processed(target)
                continue
            request = pending_requests.get(target_key) or build_anchor_request(
                latitude=float(city_config.lat),
                longitude=float(city_config.lon),
                run=cycle,
                timezone_name=city_config.timezone,
                forecast_hours=120,
                past_hours=CURRENT_RUN_CONTEXT_HOURS,
            )
            payload_captured_at = datetime.now(tz=UTC)
            anchor_transport_provenance: dict[str, object] = {
                "openmeteo_endpoint": "single_runs_api",
                "run_authority": "run_pinned_single_runs",
            }

            payload_is_materializable = family_key in witnessed_cached_paths
            if payload_is_materializable:
                payload = json.loads(payload_path.read_text(encoding="utf-8"))
            if not payload_is_materializable:
                try:
                    if target_key in unavailable_targets:
                        raise BucketTransportNotAdmissible(
                            "same city/date transport was already non-admissible this pass"
                        )
                    cached = resolved_payloads.get(target_key)
                    if cached is None:
                        if (
                            deadline_monotonic is not None
                            and time.monotonic() >= deadline_monotonic
                        ):
                            timeboxed_incomplete = True
                            continue
                        payload, anchor_transport_provenance = _resolve_anchor_payload(
                            request=request,
                            city=target.city,
                            target_date=target.target_date,
                            timezone_name=city_config.timezone,
                            deadline_monotonic=deadline_monotonic,
                            bucket_fallback_reserve_seconds=bucket_fallback_reserve_seconds,
                            bucket_manifest_provider=current_bucket_manifests,
                            bucket_read_point=bucket_pool.read,
                            bucket_read_workers=min(max(1, int(fetch_workers)), 8),
                            client=openmeteo_client,
                            meta_wave_failure=meta_wave_failures.get(target_key),
                            single_runs_run_refusals=single_runs_run_refusals,
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
                    mark_processed(target)
                    continue
                except TimeoutError:
                    timeboxed_incomplete = True
                    continue
                if not str(anchor_transport_provenance.get("run_authority", "")).startswith(
                    "bucket_partial_run"
                ):
                    # An API answer for these exact coordinates carries the provider's
                    # own target DEM: keep the bucket rung's DEM current for free.
                    try:
                        record_city_target_elevation(
                            target.city, request.latitude, request.longitude, payload,
                        )
                    except (OSError, ValueError, TypeError) as dem_exc:
                        _LOG.warning(
                            "OM9 target DEM not recorded city=%s: %s", target.city, dem_exc,
                        )
                if not _current_target_payload_materializable(
                    payload,
                    city_timezone=city_config.timezone,
                    target_date=target.target_date,
                    cycle=cycle,
                ):
                    skipped_cities.append(
                        {
                            "city": target.city,
                            "target_date": target.target_date,
                            "metric": target.temperature_metric,
                            "reason": "anchor payload has no finite target-day sample",
                        }
                    )
                    mark_processed(target)
                    continue
            scoped_payload = _current_target_scoped_payload(
                payload, city=target.city, target_date=target.target_date,
                metric=target.temperature_metric,
            )
            scoped_bytes = (json.dumps(scoped_payload, indent=2, sort_keys=True, default=str) + "\n").encode()
            precision, geometry_reason, _retry = _current_target_source_geometry_check(
                target.city, target.target_date, scoped_bytes,
                anchor_sigma_c=anchor_sigma_c,
            )
            if precision is None:
                skipped_cities.append({
                    "city": target.city,
                    "target_date": target.target_date,
                    "metric": target.temperature_metric,
                    "reason": f"OM9_SOURCE_GEOMETRY_PROOF_UNAVAILABLE:{geometry_reason}",
                })
                mark_processed(target)
                continue
            if base_payload_path.exists() and (
                not payload_is_materializable or base_payload_path.read_bytes() != scoped_bytes
            ):
                payload_path = base_payload_path.with_name(
                    f"{base_payload_path.stem}.geometry-{hashlib.sha256(scoped_bytes).hexdigest()[:12]}.json"
                )
                if payload_path.exists() and payload_path.read_bytes() != scoped_bytes:
                    raise ValueError("OM9 geometry repair path hash collision")
            if payload_path.exists():
                if payload_path.is_symlink() or payload_path.read_bytes() != scoped_bytes:
                    raise ValueError("OM9 existing body differs from normal scoped bytes")
            else:
                _write_json(payload_path, scoped_payload)
            precision_path = publish_anchor_precision_transport(payload_path, precision)
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
            mark_processed(target)
    finally:
        if owns_bucket_pool:
            bucket_pool.close()
        openmeteo_client.close()

    written_manifests: list[str] = []
    db_artifact_ids: list[int] = []
    local_proof_artifact_ids: list[int] = []
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
            if conn is not None:
                artifact_id = write_manifest_to_db(
                    conn, manifest, verify_artifact=True, repin_on_drift=True,
                )
                db_artifact_ids.append(artifact_id)
                # Same-body reuse retains the canonical run, even when this
                # local transport adds newly possessed precision. A transport
                # label cannot create a new provider issue or source run.
                if manifest.source_id == OPENMETEO_SOURCE_ID:
                    cursor = conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?",(artifact_id,))
                    canonical = dict(zip((column[0] for column in cursor.description),cursor.fetchone()))
                    metadata = json.loads(canonical["artifact_metadata_json"])
                    original_run = metadata.get("source_run_id") or (
                        f"raw:{manifest.source_id}:{manifest.data_version}:"
                        f"{datetime.fromisoformat(canonical['source_cycle_time'].replace('Z','+00:00')).astimezone(UTC).isoformat()}")
                    manifest = replace(manifest,
                        source_cycle_time=datetime.fromisoformat(canonical["source_cycle_time"].replace("Z","+00:00")),
                        source_available_at=datetime.fromisoformat(canonical["source_available_at"].replace("Z","+00:00")),
                        captured_at=datetime.fromisoformat(canonical["captured_at"].replace("Z","+00:00")),
                        request_url=canonical["request_url"],request_params=json.loads(canonical["request_params_json"]),
                        product_metadata={**metadata,"source_run_id":original_run,
                            "openmeteo_payload_json":manifest.artifact_path,
                            "precision_metadata_json":manifest.product_metadata["precision_metadata_json"]})
                # A same-byte row retains its original path, metadata and first
                # clocks. Freeze this actual producer's independently possessed
                # body/precision as a separate local dependency, never as HTTP
                # freshness or an UPDATE of the original artifact.
                precision = json.loads(Path(str(manifest.product_metadata["precision_metadata_json"])).read_bytes())
                raw = Path(manifest.artifact_path).read_bytes()
                cut = datetime.now(tz=UTC)
                guard = evaluate_openmeteo_ecmwf_ifs9_precision_guard(
                    OpenMeteoIfs9PrecisionMetadata(**precision), raw_payload_bytes=raw, decision_at=cut,
                )
                if not guard.passable_for_live_materialization:
                    raise ValueError("anchor local proof precision invalid: " + ";".join(guard.reason_codes))
                if not anchor_local_proof_covers_owned_window(raw, precision, cycle=manifest.source_cycle_time, decision_at=cut):
                    raise ValueError("partial local-day coverage: missing, duplicate or unordered hourly slots")
                local_proof_artifact_ids.append(write_anchor_local_proof(
                    conn, artifact_id, manifest, precision_metadata=precision,
                    deadline_monotonic=deadline_monotonic,
                ))
            manifest_path = _write_manifest_file(output_dir, manifest)
            written_manifests.append(str(manifest_path))
        if conn is not None:
            conn.commit()
    except Exception:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        if conn is not None:
            conn.close()
    if write_db:
        _publish_source_geometry_faults(
            skipped_cities,
            certified_cities={
                *(str(m.product_metadata.get("city")) for m in manifests),
                *(city for city, _date, _metric in canonical_reuse),
            },
        )

    total_row_count = priority_row_count + rotation_row_count
    unscheduled_target_count = max(0, total_row_count - len(targets))
    incomplete_target_set = (
        timeboxed_incomplete
        or len(manifests) + len(canonical_reuse) < len(targets)
        or unscheduled_target_count > 0
    )
    rotating_attempted_count = (
        rotating_processed_count if stable_rotation_key
        else max(0, processed_target_count - min(priority_row_count, len(targets)))
    )
    rotation_next_start, rotation_cas_applied = _advance_current_target_rotation(
        cycle=cycle,
        row_count=rotation_row_count,
        attempted_count=rotating_attempted_count,
        incomplete=incomplete_target_set,
        state_path=rotation_state_path,
        expected_generation=rotation_generation,
        expected_state_token=rotation_state_token,
        stable_rotation_key=stable_rotation_key,
        last_attempted_family=last_processed_rotating_family,
    )

    return {
        "status": (
            "CURRENT_TARGET_RAW_INPUTS_TIMEBOXED_INCOMPLETE"
            if timeboxed_incomplete
            else "CURRENT_TARGET_RAW_INPUTS_TRANSPORT_RETRYABLE"
            if targets and not manifests and not canonical_reuse
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
        "local_proof_artifact_ids": local_proof_artifact_ids,
        "reused_canonical_artifact_count": len(canonical_reuse),
        "reused_canonical_artifact_ids": list(canonical_reuse.values()),
        "sibling_payload_reuse_count": sibling_payload_reuse_count,
        "downloaded": downloaded,
        "skipped_city_count": len(skipped_cities),
        "skipped_cities": skipped_cities,
        "timeboxed_incomplete": timeboxed_incomplete,
        "unattempted_target_count": len(targets) - processed_target_count,
        "unscheduled_target_count": unscheduled_target_count,
        "target_rotation_start": rotation_start,
        "target_rotation_next_start": rotation_next_start,
        "target_rotation_advanced": incomplete_target_set,
        "target_rotation_cas_applied": rotation_cas_applied,
        "max_wall_clock_seconds": max_wall_clock_seconds,
        "fetch_workers": min(max(1, int(fetch_workers)), 8),
        "coverage_before": None if plan is None else plan.as_dict(),
        "required_scope_count": None if required_scopes is None else len(_rows),
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
    required_scopes: Sequence[tuple[str, str, str]] | None = None,
    expand_metric_siblings: bool = True,
    scoped_rotation: bool = True,
    stable_rotation_key: bool = False,
    max_wall_clock_seconds: float | None = None,
    fetch_workers: int = 4,
    bucket_reader_pool=None,
    quota_critical: bool = False,
    quota_priority: bool = False,
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
        required_scopes=required_scopes,
        expand_metric_siblings=expand_metric_siblings,
        scoped_rotation=scoped_rotation,
        stable_rotation_key=stable_rotation_key,
        max_wall_clock_seconds=max_wall_clock_seconds,
        fetch_workers=fetch_workers,
        bucket_reader_pool=bucket_reader_pool,
        quota_critical=quota_critical,
        quota_priority=quota_priority,
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
