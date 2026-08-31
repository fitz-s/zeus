# Created: 2026-06-08
# Last reused/audited: 2026-08-31
# Authority basis: operator Point-1 directive 2026-06-08 — move BAYES_PRECISION_FUSION/replacement_0_1
#   forecast PRODUCTION (raw-input download + live materialization) OFF the
#   live-trading daemon (src/main.py) INTO the forecast-live (data) daemon. The
#   large forecast downloads monopolized disk I/O on the trading
#   process, starving the reactor + market_scanner and locking riskguard dependency
#   reads -> DATA_DEGRADED flap that blocked all trades. The weeks-stable baseline
#   ran forecast production in a SEPARATE daemon; this module restores that split.
"""Shared replacement-forecast PRODUCTION functions (raw-input download +
live materialization).

These functions were moved out of ``src/main.py`` so heavy forecast downloads
no longer run inside the live-trading process. They are now
imported by BOTH ``src/main.py`` (for back-compat name resolution + the in-cycle
runtime-flags read) AND ``src/ingest/forecast_live_daemon.py`` (which actually
SCHEDULES the download + materialize jobs on the data daemon's lane).

Behavior, logging, gating, and fail-soft semantics are preserved exactly. The
download is a SEPARATE function/job from the materialize cycle; the materialize
cycle is LIGHT (seed_discovery -> seed -> materialize on already-downloaded
manifests only — it never downloads).
"""

from __future__ import annotations

import atexit
import contextlib
import fcntl
import functools
import json
import logging
import math
import os
import re
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Event, Lock
from typing import Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from src.config import settings

logger = logging.getLogger("zeus.replacement_forecast_production")

_SOURCE_CLOCK_DOWNLOAD_INFLIGHT_LOCK = Lock()
_SOURCE_CLOCK_DOWNLOAD_INFLIGHT: dict[tuple[object, ...], object] = {}
_CURRENT_TARGET_DOWNLOAD_LOCK = Lock()
_CURRENT_TARGET_BUCKET_POOL_LOCK = Lock()
_CURRENT_TARGET_BUCKET_POOL: object | None = None
_CURRENT_TARGET_BUCKET_POOL_CYCLE: datetime | None = None
_BPF_EXTRA_ROTATION_LOCK = Lock()
_BPF_EXTRA_ROTATION_OWNER_LOCK = Lock()
_BPF_EXTRA_ROTATION_SCHEMA_VERSION = 1
_BPF_EXTRA_ROTATION_FILENAME = ".bpf_extra_rotation_cursor.json"


def _bpf_extra_group_key(target: object) -> tuple[str, str]:
    return (
        str(getattr(target, "city")),
        str(getattr(target, "target_date")),
    )


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0))
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write_bpf_extra_rotation(
    path: Path,
    *,
    cycle_key: str,
    last_attempted_group: tuple[str, str],
) -> None:
    parent = path.parent
    stable_parent = parent.parent
    if not stable_parent.is_dir():
        raise FileNotFoundError(
            f"BPF extra rotation stable parent missing: {stable_parent}"
        )
    parent.mkdir(exist_ok=True)
    _fsync_directory(stable_parent)
    temporary = parent / (
        f".{path.name}.pid{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    payload = {
        "schema_version": _BPF_EXTRA_ROTATION_SCHEMA_VERSION,
        "cycle": cycle_key,
        "last_attempted_group": {
            "city": last_attempted_group[0],
            "target_date": last_attempted_group[1],
        },
    }
    try:
        temporary.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        _fsync_file(temporary)
        os.replace(temporary, path)
        _fsync_directory(parent)
    finally:
        temporary.unlink(missing_ok=True)


def _try_acquire_bpf_extra_rotation_owner(
    state_path: Path | None,
) -> tuple[str, int | None, str | None]:
    if state_path is None:
        return "UNCONFIGURED", None, None
    if not _BPF_EXTRA_ROTATION_OWNER_LOCK.acquire(blocking=False):
        return "BUSY", None, None

    lock_fd: int | None = None
    try:
        parent = state_path.parent
        stable_parent = parent.parent
        if not stable_parent.is_dir():
            raise FileNotFoundError(
                f"BPF extra rotation stable parent missing: {stable_parent}"
            )
        parent.mkdir(exist_ok=True)
        _fsync_directory(stable_parent)
        lock_path = state_path.with_name(f".{state_path.name}.lock")
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(lock_fd)
            _BPF_EXTRA_ROTATION_OWNER_LOCK.release()
            return "BUSY", None, None
        _fsync_directory(parent)
        return "ACQUIRED", lock_fd, None
    except Exception as exc:  # noqa: BLE001 - ownership failure is fail-soft
        if lock_fd is not None:
            os.close(lock_fd)
        _BPF_EXTRA_ROTATION_OWNER_LOCK.release()
        return "LOCK_FAILED", None, f"{type(exc).__name__}: {str(exc)[:220]}"


def _release_bpf_extra_rotation_owner(lock_fd: int) -> None:
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)
        _BPF_EXTRA_ROTATION_OWNER_LOCK.release()


def _bpf_extra_rotation_start(
    *,
    state_path: Path | None,
    cycle_key: str,
    keys: tuple[tuple[str, str], ...],
) -> tuple[int, str]:
    if state_path is None:
        return 0, "UNCONFIGURED"
    if not state_path.is_file():
        return 0, "MISSING"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except OSError as exc:
        logger.warning("BPF extra rotation cursor read failed: %s", exc)
        return 0, "READ_FAILED"
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("BPF extra rotation cursor corrupt: %s", exc)
        return 0, "CORRUPT"
    try:
        if (
            not isinstance(payload, dict)
            or int(payload.get("schema_version")) != _BPF_EXTRA_ROTATION_SCHEMA_VERSION
        ):
            raise ValueError("unsupported schema")
        stored_cycle = str(payload["cycle"])
        raw_group = payload["last_attempted_group"]
        if not isinstance(raw_group, dict):
            raise ValueError("last_attempted_group must be an object")
        last_key = (str(raw_group["city"]), str(raw_group["target_date"]))
        if not all(last_key):
            raise ValueError("last_attempted_group fields must be non-empty")
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("BPF extra rotation cursor corrupt: %s", exc)
        return 0, "CORRUPT"
    if stored_cycle != cycle_key:
        return 0, "CYCLE_RESET"
    if last_key in keys:
        return (keys.index(last_key) + 1) % len(keys), "RESUMED"
    ordered = sorted(keys)
    successor = next((key for key in ordered if key > last_key), ordered[0])
    return keys.index(successor), "MEMBERSHIP_RECOVERED"


def _bpf_extra_rotation_state_path(
    cfg: Mapping[str, object],
) -> Path | None:
    explicit = cfg.get("bpf_extra_rotation_state_path")
    if explicit not in (None, ""):
        return Path(str(explicit))
    seed_dir = cfg.get("seed_dir")
    if seed_dir in (None, ""):
        return None
    return Path(str(seed_dir)).parent / _BPF_EXTRA_ROTATION_FILENAME


def _rotate_bpf_extra_targets(
    targets: Sequence[object],
    *,
    cycle: datetime,
    state_path: Path | None,
    priority_group_keys: set[tuple[str, str]] | None = None,
) -> tuple[tuple[object, ...], int, int, str]:
    """Rotate urgent and ordinary groups independently, with urgent groups first."""

    grouped: dict[tuple[str, str], list[object]] = {}
    for target in targets:
        key = _bpf_extra_group_key(target)
        grouped.setdefault(key, []).append(target)
    keys = tuple(grouped)
    if not keys:
        return (), 0, 0, "NO_TARGETS"

    cycle_key = cycle.astimezone(timezone.utc).isoformat()
    priority = set(priority_group_keys or ())
    priority_keys = tuple(key for key in keys if key in priority)
    ordinary_keys = tuple(key for key in keys if key not in priority)
    with _BPF_EXTRA_ROTATION_LOCK:
        if not priority_keys:
            start, cursor_status = _bpf_extra_rotation_start(
                state_path=state_path,
                cycle_key=cycle_key,
                keys=keys,
            )
            rotated_keys = keys[start:] + keys[:start]
        else:
            priority_start, priority_status = _bpf_extra_rotation_start(
                state_path=state_path,
                cycle_key=cycle_key,
                keys=priority_keys,
            )
            rotated_priority = (
                priority_keys[priority_start:] + priority_keys[:priority_start]
            )
            if ordinary_keys:
                ordinary_start, ordinary_status = _bpf_extra_rotation_start(
                    state_path=state_path,
                    cycle_key=cycle_key,
                    keys=ordinary_keys,
                )
                rotated_ordinary = (
                    ordinary_keys[ordinary_start:] + ordinary_keys[:ordinary_start]
                )
            else:
                ordinary_status = "NO_TARGETS"
                rotated_ordinary = ()
            rotated_keys = rotated_priority + rotated_ordinary
            start = keys.index(rotated_keys[0])
            cursor_status = (
                f"PRIORITY_{priority_status};ORDINARY_{ordinary_status}"
            )
    return (
        tuple(target for key in rotated_keys for target in grouped[key]),
        start,
        len(keys),
        cursor_status,
    )


def _advance_bpf_extra_rotation(
    *,
    cycle: datetime,
    rotated_targets: Sequence[object],
    attempted_group_count: int,
    state_path: Path | None,
) -> dict[str, object]:
    keys = tuple(dict.fromkeys(_bpf_extra_group_key(target) for target in rotated_targets))
    if not keys:
        return {"status": "NO_TARGETS"}
    attempted = min(len(keys), max(0, int(attempted_group_count)))
    if attempted == 0:
        return {"status": "NO_PROGRESS"}
    last_key = keys[attempted - 1]
    if state_path is None:
        return {
            "status": "UNCONFIGURED",
            "last_attempted_group": last_key,
        }
    cycle_key = cycle.astimezone(timezone.utc).isoformat()
    with _BPF_EXTRA_ROTATION_LOCK:
        try:
            _atomic_write_bpf_extra_rotation(
                state_path,
                cycle_key=cycle_key,
                last_attempted_group=last_key,
            )
        except Exception as exc:  # noqa: BLE001 - download result remains authoritative
            logger.warning("BPF extra rotation cursor write failed: %s", exc)
            return {
                "status": "WRITE_FAILED",
                "error": f"{type(exc).__name__}: {str(exc)[:220]}",
                "last_attempted_group": last_key,
            }
    return {
        "status": "PERSISTED",
        "last_attempted_group": last_key,
    }


def _close_current_target_bucket_pool(cycle: datetime | None = None) -> None:
    global _CURRENT_TARGET_BUCKET_POOL, _CURRENT_TARGET_BUCKET_POOL_CYCLE
    with _CURRENT_TARGET_BUCKET_POOL_LOCK:
        if (
            cycle is not None
            and _CURRENT_TARGET_BUCKET_POOL_CYCLE is not None
            and _CURRENT_TARGET_BUCKET_POOL_CYCLE != cycle
        ):
            return
        pool = _CURRENT_TARGET_BUCKET_POOL
        _CURRENT_TARGET_BUCKET_POOL = None
        _CURRENT_TARGET_BUCKET_POOL_CYCLE = None
    if pool is not None:
        pool.close()


def _close_stale_current_target_bucket_pool(cycle: datetime) -> None:
    global _CURRENT_TARGET_BUCKET_POOL, _CURRENT_TARGET_BUCKET_POOL_CYCLE
    with _CURRENT_TARGET_BUCKET_POOL_LOCK:
        if (
            _CURRENT_TARGET_BUCKET_POOL is None
            or _CURRENT_TARGET_BUCKET_POOL_CYCLE == cycle
        ):
            return
        pool = _CURRENT_TARGET_BUCKET_POOL
        _CURRENT_TARGET_BUCKET_POOL = None
        _CURRENT_TARGET_BUCKET_POOL_CYCLE = None
    pool.close()


def _current_target_bucket_pool(cycle: datetime):
    global _CURRENT_TARGET_BUCKET_POOL, _CURRENT_TARGET_BUCKET_POOL_CYCLE
    _close_stale_current_target_bucket_pool(cycle)
    with _CURRENT_TARGET_BUCKET_POOL_LOCK:
        if _CURRENT_TARGET_BUCKET_POOL is None:
            from src.data.openmeteo_ecmwf_ifs9_bucket_transport import (
                BucketPointReaderPool,
            )

            _CURRENT_TARGET_BUCKET_POOL = BucketPointReaderPool()
            _CURRENT_TARGET_BUCKET_POOL_CYCLE = cycle
        return _CURRENT_TARGET_BUCKET_POOL


def _single_current_target_download(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        if not _CURRENT_TARGET_DOWNLOAD_LOCK.acquire(blocking=False):
            return {"status": "CURRENT_TARGET_DOWNLOAD_INFLIGHT_SKIP"}
        try:
            return fn(*args, **kwargs)
        except Exception:
            _close_current_target_bucket_pool()
            raise
        finally:
            _CURRENT_TARGET_DOWNLOAD_LOCK.release()

    return wrapped


atexit.register(_close_current_target_bucket_pool)

# The source-clock downloader can parse up to this many Open-Meteo locations
# from one response. Keep the urgent first request equally dense: after a run
# appears or a quota window reopens, one round trip should advance many market
# families instead of an arbitrary alphabetical city.
_SOURCE_CLOCK_LOCATION_BATCH_SIZE = 25
_NONRETRYABLE_SOURCE_HTTP_STATUS_CODES = frozenset(
    {400, 401, 403, 404, 405, 410, 422}
)
_SOURCE_HTTP_STATUS_PATTERNS = (
    re.compile(r"status_code\s*[=:]\s*(\d{3})", re.IGNORECASE),
    re.compile(r"(?:client|server) error\s+['\"](\d{3})\b", re.IGNORECASE),
    re.compile(r"\bHTTP/\d(?:\.\d)?\s+(\d{3})\b", re.IGNORECASE),
    re.compile(r"\bHTTP\s+(\d{3})\b", re.IGNORECASE),
)
_SOURCE_RETRYABLE_TRANSPORT_MARKERS = (
    "connection",
    "connect error",
    "reset",
    "timed out",
    "timeout",
    "dns",
    "tls",
    "ssl",
    "certificate",
    "eof",
    "network",
    "socket",
    "proxy",
    "rate limit",
    "quota",
    "too many requests",
    "try again",
)
_SOURCE_PERMANENT_400_MARKERS = (
    "invalid parameter",
    "unsupported",
    "must be",
    "out of bounds",
    "unknown model",
    "no data is available for this location",
)


def _source_transport_error_is_nonretryable(
    error: object,
) -> bool:
    """Whether one source-run delivery error is terminal for its exact request.

    Typed Open-Meteo outcomes are authoritative. The text fallback exists only for
    legacy reports; a raw 400 is terminal rather than a scheduler-wide transport retry.
    """

    outcome = getattr(error, "outcome", None)
    if outcome is not None:
        retry_class = getattr(outcome, "retry_class", None)
        return getattr(retry_class, "value", retry_class) == "terminal"
    if isinstance(error, Mapping) and "retry_class" in error:
        return str(error.get("retry_class")) == "terminal"

    text = str(error or "")
    lowered = text.lower()
    if any(marker in lowered for marker in _SOURCE_RETRYABLE_TRANSPORT_MARKERS):
        return False
    codes = {
        int(match.group(1))
        for pattern in _SOURCE_HTTP_STATUS_PATTERNS
        for match in pattern.finditer(text)
    }
    if not codes or not codes.issubset(_NONRETRYABLE_SOURCE_HTTP_STATUS_CODES):
        return False
    if codes == {400}:
        return True
    return True


def _source_cycle_can_cover_local_decision_window(
    *,
    cycle: datetime,
    target_date: str,
    timezone_name: str,
    decision_time: datetime | None = None,
) -> bool:
    """Whether a run can contain the full day or the unresolved Day0 suffix.

    Future targets still require a run initialized before their local day. A
    current local-day run may start after 03:xx because the downstream parser
    separately requires an elapsed-prefix-only gap plus complete coverage from
    decision time through the unresolved evening. Past partial days remain
    inadmissible; malformed geometry stays fail-open only to that stricter
    downstream payload validator.
    """

    try:
        target = date.fromisoformat(str(target_date))
        zone = ZoneInfo(str(timezone_name))
        decision = decision_time or datetime.now(timezone.utc)
        if cycle.utcoffset() is None or decision.utcoffset() is None:
            return True
        local_cycle = cycle.astimezone(zone)
        local_decision = decision.astimezone(zone)
    except (TypeError, ValueError, KeyError):
        return True
    if local_cycle.date() != target:
        return local_cycle.date() < target
    if local_cycle.hour <= 3:
        return True
    return local_decision.date() == target and cycle <= decision


def _settings_section(name: str, default=None):
    source = settings._data if hasattr(settings, "_data") else settings
    if isinstance(source, dict):
        return source.get(name, default)
    try:
        return source[name]
    except KeyError:
        return default


def _scheduler_job(job_name: str):
    """Decorator: mirror of src/main.py's scheduler-health wrapper (B047).

    Wraps fn so success -> ``scheduler_jobs_health.json[job_name].status = OK``
    and exception -> logged with traceback + ``status = FAILED``. Never re-raises
    (fail-open per K2 design). Preserved here verbatim so the moved
    ``_replacement_forecast_download_cycle`` keeps its identical wrapping (and its
    ``.__wrapped__`` accessor) after the relocation.
    """

    def _decorator(fn):
        @functools.wraps(fn)
        def _wrapper(*args, **kwargs):
            from src.observability.scheduler_health import _write_scheduler_health

            try:
                result = fn(*args, **kwargs)
                _write_scheduler_health(job_name, failed=False)
                return result
            except Exception as exc:
                logger.error("%s failed: %s", job_name, exc, exc_info=True)
                _write_scheduler_health(job_name, failed=True, reason=str(exc))

        return _wrapper

    return _decorator


def _replacement_forecast_live_materialization_queue_config() -> dict[str, object]:
    from src.config import PROJECT_ROOT, RUNTIME_ROOT, STATE_DIR

    cfg = _settings_section("replacement_forecast_live", {}) or {}
    base_dir = STATE_DIR / "replacement_forecast_live"
    raw_manifest_dir = cfg.get("raw_manifest_dir")
    forecast_db = cfg.get("forecast_db")
    materialization_limit = int(cfg.get("materialization_limit_per_cycle") or 80)
    poll_batch_limit = max(
        1,
        min(
            materialization_limit,
            int(cfg.get("materialization_poll_batch_limit") or 8),
        ),
    )

    def _rooted_path(value, fallback: Path | None = None) -> Path | None:
        raw = value if value not in (None, "") else fallback
        if raw in (None, ""):
            return None
        path = Path(str(raw))
        if path.is_absolute():
            return path
        if path.parts and path.parts[0] == "state":
            return RUNTIME_ROOT / path
        return PROJECT_ROOT / path

    request_dir = _rooted_path(cfg.get("request_dir"), base_dir / "requests")
    return {
        "seed_dir": _rooted_path(cfg.get("seed_dir"), base_dir / "seeds"),
        "seed_processed_dir": _rooted_path(cfg.get("seed_processed_dir"), base_dir / "seed_processed"),
        "seed_failed_dir": _rooted_path(cfg.get("seed_failed_dir"), base_dir / "seed_failed"),
        "forecast_db": _rooted_path(
            forecast_db,
            STATE_DIR / "zeus-forecasts.db",
        ),
        "raw_manifest_dir": _rooted_path(
            raw_manifest_dir,
            base_dir / "raw_manifests",
        ),
        "seed_discovery_limit": int(cfg.get("seed_discovery_limit_per_cycle") or cfg.get("seed_limit_per_cycle") or cfg.get("materialization_limit_per_cycle") or 80),
        "request_dir": request_dir,
        "inflight_dir": (
            request_dir.parent / "inflight"
            if request_dir is not None
            else base_dir / "inflight"
        ),
        "processed_dir": _rooted_path(cfg.get("processed_dir"), base_dir / "processed"),
        "failed_dir": _rooted_path(cfg.get("failed_dir"), base_dir / "failed"),
        "seed_limit": int(cfg.get("seed_limit_per_cycle") or cfg.get("materialization_limit_per_cycle") or 80),
        "limit": materialization_limit,
        "poll_batch_limit": poll_batch_limit,
        "download_output_dir": _rooted_path(cfg.get("download_output_dir"), _rooted_path(raw_manifest_dir, base_dir / "raw_manifests")),
        "download_limit": int(cfg.get("download_limit_per_cycle") or cfg.get("seed_discovery_limit_per_cycle") or cfg.get("materialization_limit_per_cycle") or 10),
        "download_release_lag_hours": float(cfg.get("download_release_lag_hours") or 14.0),
        "download_anchor_sigma_c": float(cfg.get("download_anchor_sigma_c") or 3.0),
        "source_clock_fanout_workers": int(cfg.get("source_clock_fanout_workers") or 4),
    }


# The two raw-artifact sources this downloader owns. The cycle high-water mark is the MIN over
# BOTH of MAX(source_cycle_time): a half-downloaded cycle (one source lagging) is NOT current.
_CURRENT_TARGET_ARTIFACT_SOURCE_IDS = ("openmeteo_ecmwf_ifs_9km",)


def _max_downloaded_current_target_cycle(forecast_db: Path) -> datetime | None:
    """High-water mark of downloaded current-target raw-input cycles, or None when unknown.

    None (no rows for either source, or any read error) means "cannot prove currency" ->
    the caller treats the cycle as stale and fires the idempotent download. The currency
    check must FAIL OPEN toward downloading; it must never freeze freshness.
    """
    from src.state.db import _connect  # noqa: PLC0415

    try:
        conn = _connect(Path(forecast_db))
        try:
            maxes: list[datetime] = []
            for sid in _CURRENT_TARGET_ARTIFACT_SOURCE_IDS:
                row = conn.execute(
                    "SELECT MAX(source_cycle_time) FROM raw_forecast_artifacts"
                    " WHERE source_id = ?",
                    (sid,),
                ).fetchone()
                if row is None or row[0] is None:
                    return None
                maxes.append(
                    datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
                )
            return min(maxes)
        finally:
            conn.close()
    except Exception:
        return None


def _probe_resolved_available_cycle() -> datetime | None:
    """SINGLE run-selection authority for every production download lane (K4.0b(a)).

    The fetchable cycle is whatever the anchor provider probes CONFIRM is published — never
    a wall-clock − release-lag guess. The guessed clock asked for unpublished 12Z/18Z
    runs every night; the rung-2 meta guard refused them (correctly) and the refusal
    aborted the whole download→materialize cycle (2026-06-11 incident,
    logs/zeus-forecast-live.err: "provider declares run 06:00 but caller wants 18:00").
    None = no anchor cycle provable right now → callers SKIP the tick with a
    receipt and retry next tick; they must never fall back to a guessed run.
    """
    from src.data.replacement_cycle_availability import (  # noqa: PLC0415
        newest_complete_cycle,
        resolve_provider_anchor_cycle_availability,
    )

    availability = resolve_provider_anchor_cycle_availability(
        datetime.now(timezone.utc),
    )
    return newest_complete_cycle(availability)


def _probe_resolved_bayes_precision_fusion_extras_cycle() -> datetime | None:
    """Newest cycle fetchable by the BPF extras transport itself.

    The anchor lane can use a ladder (single-runs, model meta, bucket). BPF
    extras are persisted from the Open-Meteo single-runs API, so an anchor-only
    bucket/meta cycle is not enough proof that extras can fetch the same run.
    """
    from src.data.replacement_cycle_availability import (  # noqa: PLC0415
        newest_complete_cycle,
        probe_openmeteo_single_run_available,
        resolve_anchor_cycle_availability,
    )

    availability = resolve_anchor_cycle_availability(
        datetime.now(timezone.utc),
        probe_anchor=probe_openmeteo_single_run_available,
    )
    return newest_complete_cycle(availability)


def _critical_scopes_missing_current_anchor(
    forecast_db: Path,
    scopes: Sequence[tuple[str, str, str]],
    cycle: datetime,
) -> tuple[tuple[str, str, str], ...] | None:
    """Return exact critical scopes without materializable canonical raw at ``cycle``."""

    from src.data.replacement_forecast_source_run_identity import (  # noqa: PLC0415
        expected_replacement_dependency_identity_by_role,
    )
    from scripts.download_replacement_forecast_current_targets import (  # noqa: PLC0415
        _current_target_payload_file_materializable,
    )
    from src.config import cities_by_name  # noqa: PLC0415
    from src.state.db import _connect  # noqa: PLC0415

    try:
        conn = _connect(forecast_db, write_class=None)
        conn.execute("PRAGMA query_only=ON")
        try:
            missing: list[tuple[str, str, str]] = []
            cycle_iso = cycle.astimezone(timezone.utc).isoformat()
            for city, target_date, metric in scopes:
                identity = expected_replacement_dependency_identity_by_role(metric)[
                    "openmeteo_ifs9_anchor"
                ]
                row = conn.execute(
                    """
                    SELECT artifact_path, sha256, byte_size
                    FROM raw_forecast_artifacts
                    WHERE source_id = ?
                      AND product_id = ?
                      AND data_version = ?
                      AND source_cycle_time = ?
                      AND json_extract(artifact_metadata_json, '$.city') = ?
                      AND json_extract(artifact_metadata_json, '$.target_date') = ?
                      AND json_extract(artifact_metadata_json, '$.metric') = ?
                    LIMIT 1
                    """,
                    (
                        identity.source_id,
                        identity.product_id,
                        identity.data_version,
                        cycle_iso,
                        city,
                        target_date,
                        metric,
                    ),
                ).fetchone()
                if row is None:
                    missing.append((city, target_date, metric))
                    continue
                city_config = cities_by_name.get(city)
                if city_config is None or not _current_target_payload_file_materializable(
                    Path(str(row[0])),
                    city_timezone=city_config.timezone,
                    target_date=target_date,
                    cycle=cycle,
                    expected_sha256=str(row[1]),
                    expected_byte_size=int(row[2]),
                ):
                    missing.append((city, target_date, metric))
            return tuple(missing)
        finally:
            conn.close()
    except Exception:
        return None


@_single_current_target_download
def _download_replacement_forecast_current_targets_if_needed(
    cfg: dict[str, object],
    *,
    max_wall_clock_seconds: float | None = None,
    required_scopes: Sequence[tuple[str, str, str]] | None = None,
    quota_critical: bool = False,
    quota_priority: bool = False,
) -> dict[str, object] | None:
    forecast_db = cfg.get("forecast_db")
    output_dir = cfg.get("download_output_dir") or cfg.get("raw_manifest_dir")
    if forecast_db is None or output_dir is None:
        raise ValueError("replacement current-target download requires forecast_db and raw_manifest_dir/download_output_dir")
    if quota_critical and quota_priority:
        raise ValueError("current-target quota lane must be critical or priority, not both")
    from scripts.download_replacement_forecast_current_targets import (
        download_current_target_openmeteo_inputs,
    )
    from src.data.replacement_forecast_current_target_plan import (
        build_replacement_forecast_current_target_plan,
    )

    # CYCLE-CURRENCY ANTIBODY (2026-06-09): coverage ("a posterior exists for every target")
    # NEVER implies currency ("the currently-available IFS cycle's raw inputs exist"). The old
    # gates short-circuited on plan.ready alone, so once ANY cycle fully materialized the cron
    # could never advance the anchor again — deterministic_forecast_anchors froze at 06-08T18
    # for ~24h while Open-Meteo was serving 06-09T00 (it answered 200 OK to the BAYES_PRECISION_FUSION leg of the
    # SAME job run). Both early returns now additionally require the downloaded high-water mark
    # to have reached the currently-available cycle.
    #
    # RUN-SELECTION AUTHORITY (2026-06-11, twin-authority kill): the available cycle is
    # probe-resolved, NEVER now − release_lag (that guess requested unpublished runs and the
    # rung-2 refusal aborted the whole cycle). release_lag_hours survives ONLY as the
    # source_available_at metadata model passed to the downloader — it takes no part in
    # deciding WHICH run to fetch.
    release_lag_hours = float(cfg.get("download_release_lag_hours") or 14.0)
    available_cycle = _probe_resolved_available_cycle()
    if available_cycle is None:
        return {
            "status": "CYCLE_PROBE_UNRESOLVED_SKIP",
        "detail": "no anchor cycle provable by provider probes this tick; "
            "retrying next tick — a guessed run is never requested",
        }
    _close_stale_current_target_bucket_pool(available_cycle)
    downloaded_cycle = _max_downloaded_current_target_cycle(Path(str(forecast_db)))
    cycle_advanced = downloaded_cycle is None or downloaded_cycle < available_cycle

    plan = None
    if required_scopes is None:
        plan = build_replacement_forecast_current_target_plan(
            Path(str(forecast_db)),
            required_openmeteo_source_cycle_time=available_cycle,
        )
    else:
        required_scopes = tuple(dict.fromkeys(required_scopes))
        if not required_scopes:
            return {
                "status": "CURRENT_TARGET_SCOPED_DOWNLOAD_NO_TARGETS",
                "available_cycle": available_cycle.isoformat(),
            }
        if quota_critical:
            from src.data.replacement_forecast_seed_discovery import (  # noqa: PLC0415
                held_position_family_priorities,
            )

            critical_families = held_position_family_priorities()
            unauthorized = tuple(
                scope for scope in required_scopes if critical_families.get(scope) != 0
            )
            if unauthorized:
                raise ValueError(
                    "critical current-target quota requires exact canonical "
                    "day0_window/pending_exit scopes: "
                    + ",".join("/".join(scope) for scope in unauthorized)
                )
            missing_critical_scopes = _critical_scopes_missing_current_anchor(
                Path(str(forecast_db)),
                required_scopes,
                available_cycle,
            )
            if missing_critical_scopes is None:
                raise RuntimeError("critical current-target anchor coverage unreadable")
            if not missing_critical_scopes:
                return {
                    "status": "CURRENT_TARGET_CRITICAL_SCOPES_ALREADY_COVERED",
                    "available_cycle": available_cycle.isoformat(),
                    "downloaded_cycle": (
                        None
                        if downloaded_cycle is None
                        else downloaded_cycle.isoformat()
                    ),
                    "target_count": len(required_scopes),
                    "written_manifest_count": 0,
                }
            required_scopes = missing_critical_scopes
    if quota_critical and required_scopes is None:
        raise ValueError("critical current-target quota requires explicit scopes")
    cycle_targets_have_current_manifests = (
        plan is not None and plan.missing_openmeteo_manifest_count <= 0
    )
    cycle_targets_are_materialized = plan is not None and plan.ready
    if cycle_targets_are_materialized:
        _close_current_target_bucket_pool()
        return {
            "status": "CURRENT_TARGETS_ALREADY_COVERED",
            "coverage": plan.as_dict(),
            "available_cycle": available_cycle.isoformat(),
            "downloaded_cycle": None if downloaded_cycle is None else downloaded_cycle.isoformat(),
        }
    if cycle_targets_have_current_manifests:
        _close_current_target_bucket_pool()
        return {
            "status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS",
            "coverage": plan.as_dict(),
            "available_cycle": available_cycle.isoformat(),
            "downloaded_cycle": None if downloaded_cycle is None else downloaded_cycle.isoformat(),
        }
    deadline = (
        time.monotonic() + max(0.0, float(max_wall_clock_seconds))
        if max_wall_clock_seconds is not None
        else None
    )
    remaining = (
        max(0.0, deadline - time.monotonic())
        if deadline is not None
        else None
    )
    if remaining is not None and remaining <= 0:
        return {
            "status": "CURRENT_TARGET_RAW_INPUTS_TIMEBOXED_INCOMPLETE",
            "available_cycle": available_cycle.isoformat(),
            "downloaded_cycle": None if downloaded_cycle is None else downloaded_cycle.isoformat(),
            "timeboxed_incomplete": True,
            "unattempted_target_count": (
                len(required_scopes)
                if plan is None and required_scopes is not None
                else plan.target_count
            ),
            "max_wall_clock_seconds": max_wall_clock_seconds,
            "coverage": None if plan is None else plan.as_dict(),
        }
    cycle = available_cycle
    download_kwargs: dict[str, object] = {}
    if required_scopes is not None:
        download_kwargs["required_scopes"] = required_scopes
    bucket_pool = _current_target_bucket_pool(cycle)
    try:
        quota_context = contextlib.nullcontext()
        if quota_critical:
            from src.data.openmeteo_quota import quota_tracker  # noqa: PLC0415

            # SCOPE: only the explicit canonical Day0/pending-exit scopes validated
            # above. DRAIN: this bounded raw-anchor call and its existing manifest
            # commit. RESET: context exit; every later call re-proves current phase.
            quota_context = quota_tracker.critical_lane()
        elif quota_priority:
            from src.data.openmeteo_quota import quota_tracker  # noqa: PLC0415

            # SCOPE: probe-resolved source-clock anchor capture only. DRAIN: the
            # bounded exact-cycle target wave commits manifests, then the next poll
            # re-counts residual scopes. RESET: context exit or zero residual gaps.
            quota_context = quota_tracker.priority_lane()
        with quota_context:
            result = download_current_target_openmeteo_inputs(
                forecast_db=Path(str(forecast_db)),
                output_dir=Path(str(output_dir)),
                cycle=cycle,
                # ``required_scopes`` is already the bounded, freshly committed source
                # batch. Applying the generic maintenance limit here silently drops the
                # tail before the deadline can decide how much work fits, leaving raw
                # model rows without the anchor required to materialize q.
                limit=(
                    None
                    if required_scopes is not None
                    else int(cfg.get("download_limit") or 10)
                ),
                write_db=True,
                release_lag_hours=release_lag_hours,
                anchor_sigma_c=float(cfg.get("download_anchor_sigma_c") or 3.0),
                # CYCLE-CURRENCY (K-root instance #3): when this call fires because the available
                # cycle is AHEAD of the downloaded high-water mark, the NEW cycle's raw inputs are
                # needed for ALL current targets — coverage ("a posterior exists") must not filter
                # the target list. Once that cycle is already represented, a residual manifest gap
                # must repair only uncovered rows; replaying every covered target each poll rewrites
                # the same manifests and repeatedly drives global seed discovery.
                include_covered=cycle_advanced,
                missing_manifests_only=not cycle_advanced,
                precomputed_plan=plan,
                max_wall_clock_seconds=remaining,
                fetch_workers=int(cfg.get("source_clock_fanout_workers") or 4),
                bucket_reader_pool=bucket_pool,
                quota_critical=quota_critical,
                quota_priority=quota_priority,
                **download_kwargs,
            )
    except Exception:
        _close_current_target_bucket_pool(cycle)
        raise
    # A scoped held-capital slice shares the cycle pool with the broad downloader.
    # Completing that local slice does not prove the broad cycle is complete; closing
    # here discards the broad slice's partially decoded hourly points and makes every
    # later timebox restart from zero. Only the unscoped broad owner may close on local
    # completion. Cycle rollover, broad coverage, exceptions, and process exit retain
    # their existing cleanup paths.
    if required_scopes is None and not bool(result.get("timeboxed_incomplete")):
        _close_current_target_bucket_pool(cycle)
    result.setdefault("available_cycle", available_cycle.isoformat())
    result.setdefault(
        "downloaded_cycle",
        None if downloaded_cycle is None else downloaded_cycle.isoformat(),
    )
    return result


def _download_bayes_precision_fusion_extra_raw_inputs_if_needed(
    cfg: dict[str, object],
    *,
    max_wall_clock_seconds: float | None = 45.0,
) -> dict[str, object] | None:
    """Download missing multi-model inputs within one bounded live-runtime slice."""
    forecast_db = cfg.get("forecast_db")
    if forecast_db is None:
        return None
    try:
        from datetime import date  # noqa: PLC0415

        from src.config import cities_by_name  # noqa: PLC0415
        from src.data.replacement_forecast_current_target_plan import (  # noqa: PLC0415
            ReplacementForecastTargetKey,
            build_replacement_forecast_current_target_plan,
        )
        from src.data.bayes_precision_fusion_download import (  # noqa: PLC0415
            BayesPrecisionFusionDownloadTarget,
            bayes_precision_fusion_quota_cooldown_seconds,
            download_bayes_precision_fusion_extra_raw_inputs,
        )

        release_lag_hours = float(cfg.get("download_release_lag_hours") or 14.0)
        cooldown_seconds = bayes_precision_fusion_quota_cooldown_seconds()
        if cooldown_seconds > 0:
            return {
                "status": "BAYES_PRECISION_FUSION_EXTRA_QUOTA_COOLDOWN_SKIPPED",
                "cooldown_seconds": cooldown_seconds,
            }
        # RUN-SELECTION AUTHORITY (2026-06-19): the capture cycle is the newest cycle
        # provably fetchable by the BPF extras transport itself. The anchor lane can
        # advance through meta/bucket before the single-runs API serves the same run;
        # extras must not follow that anchor-only cycle and then fail every target.
        cycle = _probe_resolved_bayes_precision_fusion_extras_cycle()
        if cycle is None:
            # The single-runs probe can be unavailable while the anchor lane has
            # already durably captured a current-target cycle through another
            # Open-Meteo rung. That DB row is live evidence, not a wall-clock
            # guess. Use it so the BPF lane attempts to heal the exact cycle the
            # materializer is reading; transport/quota failures are then surfaced
            # by the downloader as retryable health instead of hiding behind a
            # probe skip.
            cycle = _max_downloaded_current_target_cycle(Path(str(forecast_db)))
        if cycle is None:
            return {"status": "BAYES_PRECISION_FUSION_EXTRA_CYCLE_PROBE_UNRESOLVED_SKIP"}

        # CYCLE-CURRENCY (2026-06-09, K-root instance #5): plan ``covered`` is not
        # cycle-aware, so it cannot decide capture admission. Exact-cycle provider-family
        # coverage below can: it keeps a currently covered market in the fanout when the new
        # cycle is absent, and removes it once the live q-path's two-family minimum has landed.
        # Future targets still need full local-day coverage. Active Day0 targets may use
        # an elapsed-prefix-only vector, but the downstream parser must prove that it spans
        # decision time through the unresolved evening before any row becomes authority.
        plan = build_replacement_forecast_current_target_plan(Path(str(forecast_db)))
        decision_time = datetime.now(timezone.utc)
        coverage = _extras_coverage_missing(
            cfg,
            cycle,
            decision_time=decision_time,
        )
        missing_scopes = None if coverage is None else coverage[0]
        try:
            from src.data.replacement_forecast_seed_discovery import (  # noqa: PLC0415
                held_position_family_priorities,
            )

            held_priority = held_position_family_priorities()
        except Exception:
            held_priority = {}
        capture_rows: list[object] = list(plan.rows)
        planned_scopes = {
            (row.city, row.target_date, row.temperature_metric)
            for row in capture_rows
        }
        for city, target_date, metric in held_priority:
            if (city, target_date, metric) in planned_scopes:
                continue
            capture_rows.append(
                ReplacementForecastTargetKey(
                    city=city,
                    target_date=target_date,
                    temperature_metric=metric,
                )
            )
        admitted_rows = [
            row
            for row in capture_rows
            if (
                missing_scopes is None
                or (row.city, row.temperature_metric, row.target_date) in missing_scopes
            )
            and (
                (city_cfg := cities_by_name.get(row.city)) is None
                or _source_cycle_can_cover_local_decision_window(
                    cycle=cycle,
                    target_date=row.target_date,
                    timezone_name=str(city_cfg.timezone),
                    decision_time=decision_time,
                )
            )
        ]
        admitted_rows.sort(
            key=lambda row: (
                held_priority.get(
                    (row.city, row.target_date, row.temperature_metric),
                    2,
                ),
                bool(getattr(row, "day0_observed_extreme_required", False)),
                int(getattr(row, "posterior_count", 0)) > 0,
                not bool(getattr(row, "can_seed", False)),
                -int(getattr(row, "fusion_current_value_count", 0)),
                row.target_date,
                row.city,
                row.temperature_metric,
            )
        )
        held_group_keys = {
            (row.city, row.target_date)
            for row in admitted_rows
            if held_priority.get(
                (row.city, row.target_date, row.temperature_metric),
                2,
            ) < 2
        }
        starved_rows = [
            row
            for row in admitted_rows
            if (
                int(getattr(row, "posterior_count", 0)) <= 0
                and bool(getattr(row, "can_seed", False))
                and not bool(
                    getattr(row, "day0_observed_extreme_required", False)
                )
            )
        ]
        starvation_frontier = min(
            (row.target_date for row in starved_rows),
            default=None,
        )
        priority_group_keys = held_group_keys | {
            (row.city, row.target_date)
            for row in starved_rows
            if row.target_date == starvation_frontier
        }
        targets: list[BayesPrecisionFusionDownloadTarget] = []
        for row in admitted_rows:
            city_cfg = cities_by_name.get(row.city)
            if city_cfg is None:
                continue
            try:
                lead_days = max(0, (date.fromisoformat(row.target_date) - cycle.date()).days)
            except Exception:
                lead_days = 0
            targets.append(BayesPrecisionFusionDownloadTarget(
                city=row.city, metric=row.temperature_metric, target_date=row.target_date,
                lead_days=lead_days, latitude=float(city_cfg.lat), longitude=float(city_cfg.lon),
                timezone_name=str(city_cfg.timezone),
            ))
        if not targets:
            return {"status": "BAYES_PRECISION_FUSION_EXTRA_NO_TARGETS"}
        rotation_state_path = _bpf_extra_rotation_state_path(cfg)
        owner_status, owner_fd, owner_error = (
            _try_acquire_bpf_extra_rotation_owner(rotation_state_path)
        )
        if owner_status != "ACQUIRED" or owner_fd is None:
            result = {
                "status": (
                    "BAYES_PRECISION_FUSION_EXTRA_ROTATION_BUSY_FAILSOFT_SKIPPED"
                    if owner_status == "BUSY"
                    else "BAYES_PRECISION_FUSION_EXTRA_ROTATION_UNAVAILABLE_FAILSOFT_SKIPPED"
                ),
                "target_rotation_owner_status": owner_status,
                "retryable": True,
            }
            if owner_error is not None:
                result["error"] = owner_error
            return result

        try:
            (
                rotated_targets,
                rotation_start,
                rotation_group_count,
                rotation_read_status,
            ) = _rotate_bpf_extra_targets(
                targets,
                cycle=cycle,
                state_path=rotation_state_path,
                priority_group_keys=priority_group_keys,
            )
            download_error: Exception | None = None
            try:
                result = download_bayes_precision_fusion_extra_raw_inputs(
                    forecast_db=Path(str(forecast_db)),
                    cycle=cycle,
                    targets=rotated_targets,
                    release_lag_hours=release_lag_hours,
                    max_wall_clock_seconds=max_wall_clock_seconds,
                )
            except Exception as exc:
                download_error = exc
                result = {
                    "status": "BAYES_PRECISION_FUSION_EXTRA_CAPTURE_FAILSOFT_SKIPPED",
                    "error": str(exc),
                }
                attempted = 0
                receipt_status = "EXCEPTION_NO_RECEIPT"
            else:
                raw_attempted = result.get("attempted_target_group_count")
                if isinstance(raw_attempted, bool) or not isinstance(
                    raw_attempted, int
                ):
                    attempted = 0
                    receipt_status = "MISSING_OR_INVALID"
                else:
                    attempted = min(
                        rotation_group_count,
                        max(0, raw_attempted),
                    )
                    receipt_status = (
                        "EXACT"
                        if attempted == raw_attempted
                        else "OUT_OF_RANGE_CLAMPED"
                    )
            rotation_write = _advance_bpf_extra_rotation(
                cycle=cycle,
                rotated_targets=rotated_targets,
                attempted_group_count=attempted,
                state_path=rotation_state_path,
            )
            result["target_rotation_owner_status"] = owner_status
            result["target_rotation_start"] = rotation_start
            result["target_rotation_group_count"] = rotation_group_count
            result["target_rotation_attempted_group_count"] = attempted
            result["target_rotation_progress_receipt_status"] = receipt_status
            result["target_rotation_cursor_read_status"] = rotation_read_status
            result["target_rotation_cursor_write_status"] = rotation_write["status"]
            result["target_rotation_last_attempted_group"] = rotation_write.get(
                "last_attempted_group"
            )
            if rotation_write.get("error"):
                result["target_rotation_cursor_error"] = rotation_write["error"]
            if download_error is not None:
                logger.warning(
                    "BAYES_PRECISION_FUSION extra-model capture skipped (fail-soft): %s",
                    download_error,
                )
            return result
        finally:
            _release_bpf_extra_rotation_owner(owner_fd)
    except Exception as exc:  # noqa: BLE001 - fail-soft: extras accrual never breaks the cycle
        logger.warning("BAYES_PRECISION_FUSION extra-model capture skipped (fail-soft): %s", exc)
        return {"status": "BAYES_PRECISION_FUSION_EXTRA_CAPTURE_FAILSOFT_SKIPPED", "error": str(exc)}


def _download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
    cfg: dict[str, object],
    *,
    source_clock_report: object,
    max_wall_clock_seconds: float | None = None,
    on_source_commit: Callable[[str, Mapping[str, object]], None] | None = None,
    decision_time: datetime | None = None,
) -> dict[str, object] | None:
    """Fast source-clock current capture for only updated sources and affected cities.

    This is the latency path.  It writes the live current ``single_runs`` rows
    needed by the source-clock q kernel, but leaves the slower full-history
    healing pass to the normal BPF downloader.
    """
    forecast_db = cfg.get("forecast_db")
    if forecast_db is None:
        return None
    try:
        from datetime import date  # noqa: PLC0415

        from src.config import cities_by_name  # noqa: PLC0415
        from src.data.bayes_precision_fusion_download import (  # noqa: PLC0415
            BayesPrecisionFusionDownloadTarget,
            bayes_precision_fusion_quota_cooldown_seconds,
            bayes_precision_fusion_held_quota_cooldown_seconds,
            bayes_precision_fusion_held_quota_priority,
            bayes_precision_fusion_source_clock_quota_priority,
            download_bayes_precision_fusion_extra_raw_inputs,
        )
        from src.data.openmeteo_model_updates import read_model_updates_jsonl  # noqa: PLC0415
        from src.data.replacement_forecast_current_target_plan import (  # noqa: PLC0415
            ReplacementForecastTargetKey,
            replacement_forecast_current_target_keys,
        )
        from src.data.replacement_forecast_seed_discovery import (  # noqa: PLC0415
            held_position_family_priorities,
        )
        from src.data.source_clock_update_probe import DEFAULT_MODEL_UPDATES_JSONL  # noqa: PLC0415
        from src.strategy.live_inference.source_clock_city_weights import (  # noqa: PLC0415
            affected_cities_for_source_updates,
        )
        from src.strategy.live_inference.source_clock_vnext import source_publicly_usable_at  # noqa: PLC0415

        payload = source_clock_report.as_dict()
        updated_sources = tuple(
            str(source).strip()
            for source in (payload.get("updated_sources") or getattr(source_clock_report, "updated_sources", ()) or ())
            if str(source).strip()
        )
        affected_cities = tuple(
            str(city).strip()
            for city in (payload.get("affected_cities") or getattr(source_clock_report, "affected_cities", ()) or ())
            if str(city).strip()
        )
        if not updated_sources:
            return {"status": "SOURCE_CLOCK_BPF_SCOPED_NO_UPDATED_SOURCES"}
        if not affected_cities:
            return {
                "status": "SOURCE_CLOCK_BPF_SCOPED_NO_AFFECTED_CITIES",
                "updated_sources": updated_sources,
            }

        held_priority = held_position_family_priorities()
        cooldown_seconds = bayes_precision_fusion_quota_cooldown_seconds()
        if cooldown_seconds > 0:
            affected_city_set = set(affected_cities)
            has_held_target = any(
                priority < 2 and city in affected_city_set
                for (city, _target_date, _metric), priority in held_priority.items()
            )
            held_cooldown_seconds = (
                bayes_precision_fusion_held_quota_cooldown_seconds()
                if has_held_target
                else cooldown_seconds
            )
            if held_cooldown_seconds > 0:
                return {
                    "status": "SOURCE_CLOCK_BPF_SCOPED_QUOTA_COOLDOWN_SKIPPED",
                    "updated_sources": updated_sources,
                    "affected_cities": affected_cities,
                    "cooldown_seconds": held_cooldown_seconds,
                }

        now = decision_time or datetime.now(timezone.utc)
        source_cycles: dict[str, datetime] = {}
        source_availabilities: dict[str, datetime] = {}
        frozen_runs = payload.get("source_runs")
        if isinstance(frozen_runs, Mapping):
            for source in updated_sources:
                frozen = frozen_runs.get(source)
                if not isinstance(frozen, Mapping):
                    continue
                try:
                    initialisation_raw = datetime.fromisoformat(
                        str(frozen["initialisation_time"])
                    )
                    availability_raw = datetime.fromisoformat(
                        str(frozen["availability_time"])
                    )
                    interval = int(frozen.get("update_interval_seconds") or 0)
                    if (
                        initialisation_raw.utcoffset() is None
                        or availability_raw.utcoffset() is None
                        or interval < 0
                    ):
                        raise ValueError("source-run identity must be aware and nonnegative")
                    initialisation = initialisation_raw.astimezone(timezone.utc)
                    availability = availability_raw.astimezone(timezone.utc)
                except (KeyError, TypeError, ValueError):
                    continue
                source_cycles[source] = initialisation
                source_availabilities[source] = availability
        else:
            # Legacy telemetry callers do not carry a cursor token and therefore
            # cannot advance one from this mutable metadata fallback. Live probes
            # always freeze source_runs alongside their exact cursor values.
            try:
                updates_path = Path(str(payload.get("model_updates_path") or DEFAULT_MODEL_UPDATES_JSONL))
                for update in read_model_updates_jsonl(updates_path):
                    source = str(update.model)
                    if source not in updated_sources:
                        continue
                    run_clock = update.to_source_run_clock()
                    if now >= source_publicly_usable_at(run_clock):
                        source_cycles[source] = (
                            update.last_run_initialisation_time.astimezone(timezone.utc)
                        )
                        source_availabilities[source] = (
                            update.last_run_availability_time.astimezone(timezone.utc)
                        )
            except Exception:
                source_cycles = {}
        unresolved_sources = tuple(
            source for source in updated_sources if source not in source_cycles
        )
        resolved_sources = tuple(
            source for source in updated_sources if source in source_cycles
        )
        if not resolved_sources:
            return {
                "status": "SOURCE_CLOCK_BPF_SCOPED_CYCLE_UNRESOLVED_SKIP",
                "updated_sources": updated_sources,
                "affected_cities": affected_cities,
                "unresolved_sources": unresolved_sources,
            }

        all_target_keys = tuple(
            replacement_forecast_current_target_keys(Path(str(forecast_db)))
        )
        planned_scopes = {
            (row.city, row.target_date, row.temperature_metric)
            for row in all_target_keys
        }
        held_target_keys = tuple(
            ReplacementForecastTargetKey(
                city=city,
                target_date=target_date,
                temperature_metric=metric,
            )
            for city, target_date, metric in held_priority
            if (city, target_date, metric) not in planned_scopes
        )
        all_target_keys = (*all_target_keys, *held_target_keys)
        reported_affected = set(affected_cities)
        target_keys_by_source: dict[str, list[object]] = {}
        for source in resolved_sources:
            source_affected = (
                set(affected_cities_for_source_updates((source,)))
                & reported_affected
            )
            target_keys_by_source[source] = sorted(
                (row for row in all_target_keys if row.city in source_affected),
                key=lambda row: (
                    held_priority.get(
                        (row.city, row.target_date, row.temperature_metric),
                        2,
                    ),
                    row.target_date,
                    row.city,
                    row.temperature_metric,
                ),
            )

        planned_target_count = sum(len(rows) for rows in target_keys_by_source.values())
        covered_target_count = 0
        coverage_probe_status = "SOURCE_CLOCK_TARGET_COVERAGE_READ_FAILED"
        try:
            from src.state.db import _connect_read_only  # noqa: PLC0415

            coverage_conn = _connect_read_only(Path(str(forecast_db)))
            filtered_target_keys: dict[str, list[object]] = {}
            scoped_covered_target_count = 0
            try:
                for source, rows in target_keys_by_source.items():
                    cycle_iso = source_cycles[source].isoformat()
                    covered = {
                        (str(city), str(target_date), str(metric))
                        for city, target_date, metric in coverage_conn.execute(
                            """
                            SELECT city, target_date, metric
                              FROM raw_model_forecasts
                             WHERE model = ?
                               AND source_cycle_time = ?
                               AND endpoint = 'single_runs'
                            """,
                            (source, cycle_iso),
                        )
                    }
                    missing = [
                        row
                        for row in rows
                        if (row.city, row.target_date, row.temperature_metric)
                        not in covered
                    ]
                    scoped_covered_target_count += len(rows) - len(missing)
                    filtered_target_keys[source] = missing
            finally:
                coverage_conn.close()
            target_keys_by_source = filtered_target_keys
            covered_target_count = scoped_covered_target_count
            coverage_probe_status = "SOURCE_CLOCK_TARGET_COVERAGE_SCOPED"
        except Exception:
            # Coverage is an optimization only. The downloader's own natural-key
            # dedup remains the correctness backstop when this read is unavailable.
            pass
        missing_target_count = sum(len(rows) for rows in target_keys_by_source.values())

        structurally_unservable_by_source: dict[str, int] = {}
        coverable_target_keys: dict[str, list[object]] = {}
        for source, rows in target_keys_by_source.items():
            cycle = source_cycles[source]
            coverable: list[object] = []
            unservable = 0
            for row in rows:
                city_cfg = cities_by_name.get(row.city)
                if city_cfg is None or _source_cycle_can_cover_local_decision_window(
                    cycle=cycle,
                    target_date=row.target_date,
                    timezone_name=str(city_cfg.timezone),
                    decision_time=now,
                ):
                    coverable.append(row)
                else:
                    unservable += 1
            coverable_target_keys[source] = coverable
            structurally_unservable_by_source[source] = unservable
        target_keys_by_source = coverable_target_keys
        structurally_unservable_target_count = sum(
            structurally_unservable_by_source.values()
        )
        actionable_missing_target_count = sum(
            len(rows) for rows in target_keys_by_source.values()
        )

        targets_by_source: dict[str, list[BayesPrecisionFusionDownloadTarget]] = {}
        for source, target_keys in target_keys_by_source.items():
            cycle = source_cycles[source]
            targets: list[BayesPrecisionFusionDownloadTarget] = []
            for row in target_keys:
                city_cfg = cities_by_name.get(row.city)
                if city_cfg is None:
                    continue
                try:
                    lead_days = max(
                        0,
                        (date.fromisoformat(row.target_date) - cycle.date()).days,
                    )
                except Exception:
                    lead_days = 0
                targets.append(
                    BayesPrecisionFusionDownloadTarget(
                        city=row.city,
                        metric=row.temperature_metric,
                        target_date=row.target_date,
                        lead_days=lead_days,
                        latitude=float(city_cfg.lat),
                        longitude=float(city_cfg.lon),
                        timezone_name=str(city_cfg.timezone),
                    )
                )
            targets_by_source[source] = targets

        if not any(targets_by_source.values()):
            return {
                "status": "SOURCE_CLOCK_BPF_SCOPED_NO_TARGETS",
                "source_cycles": {
                    source: cycle.isoformat()
                    for source, cycle in source_cycles.items()
                },
                "updated_sources": updated_sources,
                "affected_cities": affected_cities,
                "planned_target_count": planned_target_count,
                "covered_target_count": covered_target_count,
                "missing_target_count": missing_target_count,
                "actionable_missing_target_count": actionable_missing_target_count,
                "structurally_unservable_target_count": (
                    structurally_unservable_target_count
                ),
                "structurally_unservable_by_source": structurally_unservable_by_source,
                "coverage_probe_status": coverage_probe_status,
            }

        max_workers = min(
            max(1, int(cfg.get("source_clock_fanout_workers") or 4)),
            8,
        )
        task_type = tuple[
            str,
            datetime,
            list[BayesPrecisionFusionDownloadTarget],
        ]
        tasks_by_source: dict[str, list[task_type]] = {}
        priority_tasks: list[task_type] = []
        source_order = tuple(
            sorted(
                resolved_sources,
                key=lambda source: (
                    min(
                        (
                            held_priority.get(
                                (
                                    row.city,
                                    row.target_date,
                                    row.temperature_metric,
                                ),
                                2,
                            )
                            for row in target_keys_by_source[source]
                        ),
                        default=3,
                    ),
                    resolved_sources.index(source),
                ),
            )
        )
        for source in source_order:
            targets = targets_by_source[source]
            if not targets:
                continue
            grouped_targets: list[list[BayesPrecisionFusionDownloadTarget]] = []
            group_index: dict[str, int] = {}
            for target in targets:
                key = target.city
                index = group_index.get(key)
                if index is None:
                    group_index[key] = len(grouped_targets)
                    grouped_targets.append([target])
                else:
                    grouped_targets[index].append(target)
            held_groups: list[list[BayesPrecisionFusionDownloadTarget]] = []
            while grouped_targets and min(
                held_priority.get(
                    (target.city, target.target_date, target.metric),
                    2,
                )
                for target in grouped_targets[0]
            ) < 2:
                held_groups.append(grouped_targets.pop(0))
            priority_tasks.extend(
                (
                    source,
                    source_cycles[source],
                    [
                        target
                        for group in held_groups[
                            offset : offset + _SOURCE_CLOCK_LOCATION_BATCH_SIZE
                        ]
                        for target in group
                    ],
                )
                for offset in range(
                    0,
                    len(held_groups),
                    _SOURCE_CLOCK_LOCATION_BATCH_SIZE,
                )
            )
            if not grouped_targets:
                tasks_by_source[source] = []
                continue
            tasks_by_source[source] = [
                (
                    source,
                    source_cycles[source],
                    [
                        target
                        for group in grouped_targets[
                            offset : offset + _SOURCE_CLOCK_LOCATION_BATCH_SIZE
                        ]
                        for target in group
                    ],
                )
                for offset in range(
                    0,
                    len(grouped_targets),
                    _SOURCE_CLOCK_LOCATION_BATCH_SIZE,
                )
            ]

        held_priority_barrier = bool(priority_tasks)
        if not priority_tasks:
            for source in source_order:
                source_tasks = tasks_by_source.get(source, [])
                if source_tasks:
                    priority_tasks.append(source_tasks.pop(0))
                    break

        tasks: list[
            tuple[
                str,
                datetime,
                list[BayesPrecisionFusionDownloadTarget],
            ]
        ] = []
        for offset in range(
            max((len(source_tasks) for source_tasks in tasks_by_source.values()), default=0)
        ):
            for source in source_order:
                source_tasks = tasks_by_source.get(source, ())
                if offset < len(source_tasks):
                    tasks.append(source_tasks[offset])

        worker_count = min(max_workers, len(tasks))
        deadline = (
            time.monotonic() + max(0.0, float(max_wall_clock_seconds))
            if max_wall_clock_seconds is not None
            else None
        )
        quota_abort = Event()

        def _download_task(
            source: str,
            cycle: datetime,
            chunk: list[BayesPrecisionFusionDownloadTarget],
        ) -> tuple[str, dict[str, object]]:
            held_chunk = any(
                held_priority.get(
                    (target.city, target.target_date, target.metric),
                    2,
                )
                < 2
                for target in chunk
            )
            cooldown_seconds = (
                bayes_precision_fusion_held_quota_cooldown_seconds()
                if held_chunk
                else bayes_precision_fusion_quota_cooldown_seconds()
            )
            if quota_abort.is_set() or cooldown_seconds > 0:
                quota_abort.set()
                return source, {
                    "status": "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE",
                    "target_count": len(chunk),
                    "written_row_count": 0,
                    "transport_errors": (
                        f"source_clock_quota_abort:cooldown_seconds={cooldown_seconds}",
                    ),
                    "transport_aborted_remaining_targets": True,
                    "global_models_expected": 1,
                    "global_models_unavailable": (source,),
                    "single_runs_request_cycles": {
                        source: cycle.isoformat(),
                    },
                }
            remaining = (
                max(0.0, deadline - time.monotonic())
                if deadline is not None
                else None
            )
            quota_context = (
                bayes_precision_fusion_held_quota_priority()
                if held_chunk
                else bayes_precision_fusion_source_clock_quota_priority()
            )
            with quota_context:
                report = download_bayes_precision_fusion_extra_raw_inputs(
                    forecast_db=Path(str(forecast_db)),
                    cycle=cycle,
                    targets=chunk,
                    models=(source,),
                    include_previous_runs=False,
                    prune_after=False,
                    allow_single_runs_fallback=False,
                    release_lag_hours=float(
                        cfg.get("download_release_lag_hours") or 14.0
                    ),
                    max_wall_clock_seconds=remaining,
                    frozen_source_runs={
                        source: (cycle, source_availabilities[source])
                    },
                )
            if bool(report.get("transport_aborted_remaining_targets")):
                quota_abort.set()
            return source, report

        reports_by_source: dict[str, list[dict[str, object]]] = {
            source: [] for source in updated_sources
        }
        fanout_errors: list[str] = []
        source_commit_notifications = 0
        source_commit_notification_errors: list[str] = []

        assert priority_tasks
        priority_reports: list[dict[str, object]] = []
        scheduled_tasks = (*priority_tasks, *tasks)
        executor_worker_count = min(max_workers, len(scheduled_tasks))
        callback_futures = {}
        source_executor = ThreadPoolExecutor(
            max_workers=executor_worker_count,
            thread_name_prefix="source-clock-bpf",
        )
        callback_executor = ThreadPoolExecutor(
            max_workers=executor_worker_count,
            thread_name_prefix="source-clock-commit",
        )
        try:
            futures = {}

            def _submit_task(
                task: task_type,
                *,
                is_priority: bool,
                runner: Callable[
                    [str, datetime, list[BayesPrecisionFusionDownloadTarget]],
                    tuple[str, dict[str, object]],
                ],
            ) -> object:
                source, cycle, chunk = task
                task_key = (
                    source,
                    cycle.isoformat(),
                    tuple(
                        (
                            target.city,
                            target.target_date,
                            target.metric,
                        )
                        for target in chunk
                    ),
                )
                created = False
                with _SOURCE_CLOCK_DOWNLOAD_INFLIGHT_LOCK:
                    future = _SOURCE_CLOCK_DOWNLOAD_INFLIGHT.get(task_key)
                    if future is None:
                        future = source_executor.submit(runner, *task)
                        _SOURCE_CLOCK_DOWNLOAD_INFLIGHT[task_key] = future
                        created = True

                if created:
                    def _release_source_future(_future, *, key=task_key) -> None:
                        with _SOURCE_CLOCK_DOWNLOAD_INFLIGHT_LOCK:
                            if _SOURCE_CLOCK_DOWNLOAD_INFLIGHT.get(key) is _future:
                                _SOURCE_CLOCK_DOWNLOAD_INFLIGHT.pop(key, None)

                    future.add_done_callback(_release_source_future)
                futures[future] = (source, is_priority)

                return future

            priority_futures = {
                _submit_task(task, is_priority=True, runner=_download_task)
                for task in priority_tasks
            }
            priority_barrier = Event()
            if not held_priority_barrier:
                priority_barrier.set()
            else:
                priority_remaining = [len(priority_futures)]
                priority_lock = Lock()

                def _release_priority_barrier(_future: object) -> None:
                    with priority_lock:
                        priority_remaining[0] -= 1
                        if priority_remaining[0] == 0:
                            priority_barrier.set()

                for future in priority_futures:
                    future.add_done_callback(_release_priority_barrier)

            def _download_after_priority(
                source: str,
                cycle: datetime,
                chunk: list[BayesPrecisionFusionDownloadTarget],
            ) -> tuple[str, dict[str, object]]:
                remaining = (
                    max(0.0, deadline - time.monotonic())
                    if deadline is not None
                    else None
                )
                if not priority_barrier.wait(remaining):
                    return source, {
                        "status": "BAYES_PRECISION_FUSION_EXTRA_TIMEBOXED_INCOMPLETE",
                        "target_count": len(chunk),
                        "written_row_count": 0,
                        "timeboxed_incomplete": True,
                        "timebox_unattempted_target_groups": len(
                            {target.city for target in chunk}
                        ),
                        "global_models_expected": 1,
                        "global_models_unavailable": (source,),
                        "single_runs_request_cycles": {source: cycle.isoformat()},
                    }
                return _download_task(source, cycle, chunk)

            for task in tasks:
                _submit_task(
                    task,
                    is_priority=False,
                    runner=_download_after_priority,
                )

            source_timeout = (
                None
                if deadline is None
                else max(0.0, deadline - time.monotonic())
            )
            try:
                for future in as_completed(futures, timeout=source_timeout):
                    source, is_priority = futures[future]
                    try:
                        result_source, task_report = future.result()
                    except Exception as exc:  # noqa: BLE001 - preserve successful sources
                        fanout_errors.append(
                            f"{source}:{type(exc).__name__}: {str(exc)[:220]}"
                        )
                        continue
                    reports_by_source[result_source].append(task_report)
                    if is_priority:
                        priority_reports.append(task_report)
                    if (
                        on_source_commit is not None
                        and int(task_report.get("written_row_count") or 0) > 0
                    ):
                        callback_futures[
                            callback_executor.submit(
                                on_source_commit,
                                result_source,
                                task_report,
                            )
                        ] = result_source
            except TimeoutError:
                pass

            fanout_deadline_pending_sources = tuple(
                sorted(
                    {
                        source
                        for future, (source, _is_priority) in futures.items()
                        if not future.done()
                    }
                )
            )

            callback_timeout = (
                None
                if deadline is None
                else max(0.0, deadline - time.monotonic())
            )
            completed_callbacks = set()
            try:
                for future in as_completed(
                    callback_futures,
                    timeout=callback_timeout,
                ):
                    completed_callbacks.add(future)
                    source = callback_futures[future]
                    try:
                        future.result()
                    except Exception as exc:  # noqa: BLE001 - final catch-up remains authoritative
                        source_commit_notification_errors.append(
                            f"{source}:{type(exc).__name__}: {str(exc)[:220]}"
                        )
                    else:
                        source_commit_notifications += 1
            except TimeoutError:
                pass

            def _log_late_callback_result(future) -> None:
                source = callback_futures[future]
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001 - periodic catch-up remains authoritative
                    logger.warning(
                        "source-clock deferred commit callback failed for %s: %s",
                        source,
                        exc,
                    )

            for future in set(callback_futures) - completed_callbacks:
                future.add_done_callback(_log_late_callback_result)
        finally:
            source_executor.shutdown(wait=False, cancel_futures=False)
            callback_executor.shutdown(wait=False, cancel_futures=False)
        source_commit_notifications_pending = (
            len(callback_futures) - source_commit_notifications
            - len(source_commit_notification_errors)
        )

        source_results: dict[str, dict[str, object]] = {}
        for source in updated_sources:
            if source in unresolved_sources:
                source_results[source] = {
                    "status": "SOURCE_CLOCK_SOURCE_CYCLE_UNRESOLVED",
                    "target_count": 0,
                    "written_row_count": 0,
                    "transport_errors": (),
                    "fanout_errors": (),
                }
                continue
            source_reports = reports_by_source[source]
            source_errors = tuple(
                error for error in fanout_errors if error.startswith(f"{source}:")
            )
            statuses = {
                str(item.get("status") or "") for item in source_reports
            }
            source_transport_errors = tuple(
                str(value)
                for item in source_reports
                for value in (item.get("transport_errors") or ())
            )
            source_transport_outcomes = tuple(
                value
                for item in source_reports
                for value in (item.get("transport_outcomes") or ())
                if isinstance(value, Mapping)
            )
            source_permanent_errors = tuple(
                error
                for error in source_transport_errors
                if _source_transport_error_is_nonretryable(error)
            )
            source_permanent_outcomes = tuple(
                outcome
                for outcome in source_transport_outcomes
                if _source_transport_error_is_nonretryable(outcome)
            )
            source_incomplete = any(
                item.get("global_models_dropped_scoped")
                or item.get("global_models_unavailable")
                for item in source_reports
            )
            expected_cycle = source_cycles[source].isoformat()
            actual_cycles = {
                str(cycle)
                for item in source_reports
                for cycle in (
                    (item.get("single_runs_request_cycles") or {}).get(source),
                )
                if cycle
            }
            identity_mismatch = (
                bool(targets_by_source[source])
                and isinstance(frozen_runs, Mapping)
                and actual_cycles != {expected_cycle}
            )
            if not targets_by_source[source]:
                status = "SOURCE_CLOCK_SOURCE_NO_TARGETS"
            elif identity_mismatch:
                status = "SOURCE_CLOCK_SOURCE_RUN_IDENTITY_MISMATCH"
            elif source in fanout_deadline_pending_sources:
                status = "SOURCE_CLOCK_SOURCE_TIMEBOXED_INCOMPLETE"
            elif source_errors:
                status = "SOURCE_CLOCK_SOURCE_CAPTURE_FAILSOFT_SKIPPED"
            elif "BAYES_PRECISION_FUSION_EXTRA_TIMEBOXED_INCOMPLETE" in statuses:
                status = "SOURCE_CLOCK_SOURCE_TIMEBOXED_INCOMPLETE"
            elif (
                "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE" in statuses
                and source_transport_errors
                and (
                    len(source_permanent_outcomes) == len(source_transport_outcomes)
                    if source_transport_outcomes
                    else len(source_permanent_errors) == len(source_transport_errors)
                )
            ):
                status = "SOURCE_CLOCK_SOURCE_PERMANENT_FAILURE"
            elif (
                "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE" in statuses
                or source_incomplete
            ):
                status = "SOURCE_CLOCK_SOURCE_TRANSPORT_RETRYABLE"
            elif statuses == {
                "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
            }:
                status = "SOURCE_CLOCK_SOURCE_RAW_INPUTS_DOWNLOADED"
            else:
                status = "SOURCE_CLOCK_SOURCE_CAPTURE_FAILSOFT_SKIPPED"
            source_results[source] = {
                "status": status,
                "cycle": (
                    next(iter(actual_cycles))
                    if len(actual_cycles) == 1
                    else expected_cycle
                ),
                "expected_cycle": expected_cycle,
                "actual_cycles": tuple(sorted(actual_cycles)),
                "target_count": sum(
                    int(item.get("target_count") or 0)
                    for item in source_reports
                ),
                "written_row_count": sum(
                    int(item.get("written_row_count") or 0)
                    for item in source_reports
                ),
                "transport_errors": source_transport_errors,
                "transport_outcomes": source_transport_outcomes,
                "permanent_errors": source_permanent_errors,
                "permanent_outcomes": source_permanent_outcomes,
                "fanout_errors": source_errors,
            }

        source_statuses = {
            str(item.get("status") or "") for item in source_results.values()
        }
        if "SOURCE_CLOCK_SOURCE_CYCLE_UNRESOLVED" in source_statuses:
            status = "SOURCE_CLOCK_BPF_SCOPED_CYCLE_UNRESOLVED_PARTIAL"
        elif "SOURCE_CLOCK_SOURCE_RUN_IDENTITY_MISMATCH" in source_statuses:
            status = "SOURCE_CLOCK_BPF_SCOPED_RUN_IDENTITY_MISMATCH"
        elif "SOURCE_CLOCK_SOURCE_CAPTURE_FAILSOFT_SKIPPED" in source_statuses:
            status = "SOURCE_CLOCK_BPF_SCOPED_CAPTURE_FAILSOFT_SKIPPED"
        elif "SOURCE_CLOCK_SOURCE_TIMEBOXED_INCOMPLETE" in source_statuses:
            status = "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_TIMEBOXED_INCOMPLETE"
        elif "SOURCE_CLOCK_SOURCE_TRANSPORT_RETRYABLE" in source_statuses:
            status = "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE"
        elif "SOURCE_CLOCK_SOURCE_PERMANENT_FAILURE" in source_statuses:
            status = "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_PERMANENT_FAILURE"
        else:
            status = "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"

        reports = [
            item
            for source_reports in reports_by_source.values()
            for item in source_reports
        ]
        committed_families = tuple(
            sorted(
                {
                    tuple(str(part) for part in scope)
                    for item in reports
                    for scope in (item.get("committed_families") or ())
                    if isinstance(scope, (tuple, list)) and len(scope) == 3
                }
            )
        )
        report = {
            "status": status,
            "cycle": max(source_cycles.values()).isoformat(),
            "source_cycles": {
                source: source_cycles[source].isoformat()
                for source in resolved_sources
            },
            "source_results": source_results,
            "forecast_db": str(forecast_db),
            "target_count": sum(
                int(item.get("target_count") or 0) for item in reports
            ),
            "planned_target_count": planned_target_count,
            "covered_target_count": covered_target_count,
            "missing_target_count": missing_target_count,
            "actionable_missing_target_count": actionable_missing_target_count,
            "structurally_unservable_target_count": structurally_unservable_target_count,
            "structurally_unservable_by_source": structurally_unservable_by_source,
            "coverage_probe_status": coverage_probe_status,
            "candidate_row_count": sum(
                int(item.get("candidate_row_count") or 0) for item in reports
            ),
            "written_row_count": sum(
                int(item.get("written_row_count") or 0) for item in reports
            ),
            "committed_families": committed_families,
            "pruned_row_count": sum(
                int(item.get("pruned_row_count") or 0) for item in reports
            ),
            "dropped": tuple(
                value
                for item in reports
                for value in (item.get("dropped") or ())
            ),
            "domain_excluded": tuple(
                sorted(
                    {
                        value
                        for item in reports
                        for value in (item.get("domain_excluded") or ())
                    }
                )
            ),
            "transport_errors": tuple(
                value
                for item in reports
                for value in (item.get("transport_errors") or ())
            ),
            "transport_aborted_remaining_targets": any(
                bool(item.get("transport_aborted_remaining_targets"))
                for item in reports
            ),
            "timeboxed_incomplete": any(
                bool(item.get("timeboxed_incomplete"))
                for item in reports
            ),
            "timebox_unattempted_target_groups": sum(
                int(item.get("timebox_unattempted_target_groups") or 0)
                for item in reports
            ),
            "timebox_unpersisted_row_count": sum(
                int(item.get("timebox_unpersisted_row_count") or 0)
                for item in reports
            ),
            "prune_skipped_timebox": any(
                bool(item.get("prune_skipped_timebox")) for item in reports
            ),
            "max_wall_clock_seconds": max_wall_clock_seconds,
            "global_models_expected": sum(
                max(
                    (
                        int(item.get("global_models_expected") or 0)
                        for item in source_reports
                    ),
                    default=0,
                )
                for source_reports in reports_by_source.values()
            ),
            "global_models_dropped_scoped": sorted(
                {
                    value
                    for item in reports
                    for value in (item.get("global_models_dropped_scoped") or ())
                }
            ),
            "global_models_unavailable": sorted(
                {
                    value
                    for item in reports
                    for value in (item.get("global_models_unavailable") or ())
                }
            ),
            "fanout_workers": worker_count,
            "fanout_errors": tuple(fanout_errors),
            "fanout_deadline_pending_sources": fanout_deadline_pending_sources,
            "source_commit_notifications": source_commit_notifications,
            "source_commit_notifications_pending": source_commit_notifications_pending,
            "source_commit_notification_errors": tuple(
                source_commit_notification_errors
            ),
            "priority_probe_source": priority_tasks[0][0],
            "priority_probe_sources": tuple(
                dict.fromkeys(task[0] for task in priority_tasks)
            ),
            "priority_probe_families": tuple(
                dict.fromkeys(
                    (target.city, target.target_date)
                    for task in priority_tasks
                    for target in task[2]
                )
            ),
            "priority_probe_transport_aborted": any(
                bool(report.get("transport_aborted_remaining_targets"))
                for report in priority_reports
            ),
            "updated_sources": updated_sources,
            "affected_cities": affected_cities,
        }
        return report
    except Exception as exc:  # noqa: BLE001 - source-clock fast capture must fail soft
        logger.warning("source-clock scoped BPF capture skipped (fail-soft): %s", exc)
        return {
            "status": "SOURCE_CLOCK_BPF_SCOPED_CAPTURE_FAILSOFT_SKIPPED",
            "error": str(exc),
        }


_EXTRAS_FIXPOINT_HEALTH_JOB = "bayes_precision_fusion_capture"


def _extras_coverage_missing(
    cfg: dict[str, object],
    cycle: datetime,
    *,
    decision_time: datetime | None = None,
) -> tuple[set[tuple[str, str, str]], int] | None:
    """Per-(city, metric, target_date) coverage gap for ``cycle``'s BPF single_runs capture.

    Returns ``(missing_scopes, planned_count)`` where ``missing_scopes`` is the set of planned
    scopes without two provider families at this cycle's exact natural key, and
    ``planned_count`` is the size of the plan. Returns ``None`` on any probe error
    (caller fails-open = re-run).

    THE DENOMINATOR is the current-market plan plus canonical held-position
    families, the same union both fan-outs build their download targets from. A scope is "covered"
    iff it has >=2 distinct provider families in ``single_runs`` rows at the exact
    (city, metric, target_date, source_cycle_time) key the materializer's q-path reads
    (replacement_current_value_serving.read_current_instrument_values) — so completeness here
    is byte-aligned with the live shape's minimum provider-family requirement. One
    provider row is partial capture, not completeness. A ``previous_runs`` substitute
    is a q FALLBACK, not cycle completeness, so it is deliberately NOT counted: the
    cycle's own two provider families must land or capture keeps retrying for THIS cycle.
    """
    forecast_db = cfg.get("forecast_db")
    if forecast_db is None:
        return None
    try:
        from datetime import timezone as _tz  # noqa: PLC0415

        from src.data.replacement_forecast_current_target_plan import (  # noqa: PLC0415
            build_replacement_forecast_current_target_plan,
        )
        from src.data.replacement_forecast_seed_discovery import (  # noqa: PLC0415
            held_position_family_priorities,
        )
        from src.state.db import _connect  # noqa: PLC0415

        plan = build_replacement_forecast_current_target_plan(Path(str(forecast_db)))
        from src.config import cities_by_name  # noqa: PLC0415

        capture_scopes = {
            (row.city, row.temperature_metric, row.target_date)
            for row in plan.rows
        }
        capture_scopes.update(
            (city, metric, target_date)
            for city, target_date, metric in held_position_family_priorities()
        )
        need = {
            (city, metric, target_date)
            for city, metric, target_date in capture_scopes
            if (
                (city_cfg := cities_by_name.get(city)) is None
                or _source_cycle_can_cover_local_decision_window(
                    cycle=cycle,
                    target_date=target_date,
                    timezone_name=str(city_cfg.timezone),
                    decision_time=decision_time,
                )
            )
        }
        if not need:
            return (set(), 0)  # no planned scopes (e.g. no open markets) => nothing to capture
        conn = _connect(Path(str(forecast_db)))
        try:
            cycle_iso = cycle.astimezone(_tz.utc).isoformat()
            rows = conn.execute(
                "SELECT DISTINCT city, metric, target_date, model "
                "FROM raw_model_forecasts "
                "WHERE source_cycle_time = ? AND endpoint = 'single_runs'",
                (cycle_iso,),
            ).fetchall()
        finally:
            conn.close()
        from src.strategy.live_inference.source_clock_vnext import (  # noqa: PLC0415
            provider_family_for_source,
        )

        families_by_scope: dict[tuple[str, str, str], set[str]] = {}
        for city, metric, target_date, model in rows:
            scope = (str(city), str(metric), str(target_date))
            families_by_scope.setdefault(scope, set()).add(
                provider_family_for_source(str(model))
            )
        have = {
            scope for scope, families in families_by_scope.items() if len(families) >= 2
        }
        return (need - have, len(need))
    except Exception:
        return None


def _extras_fixpoint_latched(cycle: datetime) -> bool:
    """True iff the prior full extras pass for THIS cycle landed ZERO new rows while coverage
    was still incomplete — i.e. the residual gap is provably unservable for this cycle right now
    (a fixpoint), so re-running the fan-out cannot make progress. The latch is keyed on the
    cycle ISO, so the instant ``_probe_resolved_available_cycle`` advances to a newer cycle the
    latch is stale (cycle mismatch) and the new cycle gets the full self-healing treatment from
    scratch — no count is stored, no prune is needed (architect cross-check 2026-06-16)."""
    try:
        from datetime import timezone as _tz  # noqa: PLC0415
        import json as _json  # noqa: PLC0415

        from src.config import state_path  # noqa: PLC0415

        path = state_path("scheduler_jobs_health.json")
        if not path.exists():
            return False
        with open(path) as f:
            data = _json.load(f)
        live = (data.get(_EXTRAS_FIXPOINT_HEALTH_JOB) or {}).get("business_liveness") or {}
        return bool(live.get("extras_fixpoint_latched")) and str(
            live.get("extras_fixpoint_cycle")
        ) == cycle.astimezone(_tz.utc).isoformat()
    except Exception:
        return False  # unreadable latch -> not latched -> re-probe (fail toward self-healing)


def _held_position_extras_missing_scopes(
    cfg: dict[str, object],
    missing_scopes: set[tuple[str, str, str]],
) -> set[tuple[str, str, str]]:
    """Held-position scopes whose BPF current capture is still missing.

    A per-cycle extras fixpoint is a resource-control latch for ordinary current
    targets. It must not become a live-money dead end: if a held family still
    lacks the current raw inputs required for a fresh posterior, the capture lane
    keeps retrying until the cycle rolls or the scope is covered.
    """
    if not missing_scopes:
        return set()
    try:
        from src.data.replacement_cycle_advance_trigger import (  # noqa: PLC0415
            _held_position_families,
        )
        from src.state.db import _connect, _zeus_trade_db_path  # noqa: PLC0415

        trade_db = Path(str(cfg.get("trades_db") or _zeus_trade_db_path()))
        if not trade_db.exists():
            return set()
        conn = _connect(trade_db, write_class=None)
        try:
            conn.execute("PRAGMA query_only=ON")
            held = _held_position_families(conn)
        finally:
            conn.close()
        held_as_extras_scopes = {
            (city, metric, target_date)
            for city, target_date, metric in held
        }
        return set(missing_scopes) & held_as_extras_scopes
    except Exception:
        return set()


def _record_extras_fixpoint(cfg: dict[str, object], cycle: datetime, *, written: int) -> None:
    """Update the per-cycle fixpoint latch from the fan-out's own progress signal.

    LATCH iff this pass landed ZERO new rows (``written == 0``) AND coverage is STILL incomplete
    for ``cycle`` -> the residual is unservable now, stop looping (complete-with-gap, logged).
    UN-LATCH on any progress (``written > 0``) or full coverage -> self-healing resumes. The
    downloader is per-row idempotent (bayes_precision_fusion_download.py:918-957), so on a
    steady-state re-run where nothing new is servable ``written`` is exactly 0 — that zero IS
    the fixpoint signal; no cross-tick count needs persisting. Best-effort (never raises)."""
    try:
        from datetime import timezone as _tz  # noqa: PLC0415

        from src.observability.scheduler_health import (  # noqa: PLC0415
            _write_scheduler_health,
        )

        cov = _extras_coverage_missing(cfg, cycle)
        # cov None (probe error) or non-empty missing-set => still-incomplete.
        still_incomplete = cov is None or bool(cov[0])
        latched = bool(written == 0 and still_incomplete)
        cycle_iso = cycle.astimezone(_tz.utc).isoformat()
        if latched and cov is not None:
            logger.info(
                "BAYES_PRECISION_FUSION extras FIXPOINT for cycle %s: pass landed 0 new rows with "
                "%d/%d planned scopes still missing single_runs -> complete-with-gap (unservable "
                "this cycle; will re-heal when the cycle advances): %s",
                cycle_iso,
                len(cov[0]),
                cov[1],
                ", ".join(sorted(f"{c}/{m}/{d}" for c, m, d in cov[0])[:20]),
            )
        # `extra` only sets business_liveness when truthy; the FAILED/global-models health
        # write at :730-741 passes NO extra, so it never clobbers this latch (and vice versa).
        _write_scheduler_health(
            _EXTRAS_FIXPOINT_HEALTH_JOB,
            failed=False,
            extra={
                "extras_fixpoint_cycle": cycle_iso,
                "extras_fixpoint_latched": latched,
            },
        )
    except Exception:
        logger.debug("BAYES_PRECISION_FUSION extras fixpoint record failed (non-fatal)", exc_info=True)


def _record_bayes_precision_fusion_capture_health(
    cfg: dict[str, object],
    report: dict[str, object],
) -> None:
    """Write component health for the BPF capture sub-lane.

    The parent replacement download job can succeed while the BPF capture lane
    did not obtain extra current-cycle raw-model rows. Durable production health
    distinguishes hard capture failures from quota/transport degradation:
    ``FAILED`` is restart-blocking, while transport-degraded ``SKIPPED`` is
    explicit degraded evidence as long as canonical live posterior freshness and
    the materializer remain healthy.
    """

    from src.observability.scheduler_health import _write_scheduler_health  # noqa: PLC0415

    status = str(report.get("status") or "")
    if status == "BAYES_PRECISION_FUSION_EXTRA_NO_TARGETS":
        return
    raw_transport_errors = report.get("transport_errors") or ()
    if isinstance(raw_transport_errors, str):
        transport_errors = (raw_transport_errors,)
    else:
        transport_errors = tuple(str(err) for err in raw_transport_errors)
    quota_degraded = (
        status == "BAYES_PRECISION_FUSION_EXTRA_QUOTA_COOLDOWN_SKIPPED"
        or (
            status == "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE"
            and any(
                "open-meteo quota exhausted" in err.lower()
                or "too many requests" in err.lower()
                or "429" in err.lower()
                or "rate limit" in err.lower()
                for err in transport_errors
            )
        )
    )
    if quota_degraded:
        _write_scheduler_health(
            _EXTRAS_FIXPOINT_HEALTH_JOB,
            failed=False,
            skipped=True,
            skip_reason=status,
            extra={
                "transport_degraded": True,
                "transport_degradation_reason": status,
                "quota_cooldown_seconds": int(report.get("cooldown_seconds") or 0),
            },
        )
        return
    if status == "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED":
        cycle_raw = report.get("cycle")
        try:
            cycle = datetime.fromisoformat(str(cycle_raw).replace("Z", "+00:00"))
        except Exception:
            cycle = None
        if cycle is not None:
            _record_extras_fixpoint(
                cfg,
                cycle,
                written=int(report.get("written_row_count", 0) or 0),
            )
        unavailable = report.get("global_models_unavailable")
        if unavailable:
            _write_scheduler_health(
                _EXTRAS_FIXPOINT_HEALTH_JOB,
                failed=True,
                reason=str(unavailable),
            )
            return
        _write_scheduler_health(_EXTRAS_FIXPOINT_HEALTH_JOB, failed=False)
        return
    if status:
        reason = str(report.get("error") or report.get("detail") or status)
        _write_scheduler_health(
            _EXTRAS_FIXPOINT_HEALTH_JOB,
            failed=True,
            reason=reason,
        )


def _extras_cycle_incomplete(cfg: dict[str, object], cycle: datetime | None = None) -> bool:
    """Coverage-aware probe: does ``cycle`` (default: probe-resolved) still need its BPF extras?

    Returns True (run the extras fan-out) when ANY planned (city, metric, target_date) scope
    lacks its persisted current ``single_runs`` capture at this cycle's source_cycle_time AND
    the per-cycle fixpoint latch is NOT set; False (skip) when every planned scope is covered OR
    the residual gap is a proven unservable-this-cycle fixpoint. Returns True on any probe error
    so the caller fails-open (safe default = run the extras).

    WHY THE FLAT ROW-COUNT GATE WAS WRONG (fix 2026-06-16, root cause
    docs/evidence/timing_audit/capture_reactor_stall_rootcause_2026-06-16.md):
    the prior gate compared ``COUNT(*) WHERE source_cycle_time=?`` against a flat floor of
    200 rows — BLIND to per-(city, target_date) coverage. The near-day (lead=0) leg alone is
    ~382 rows for one cycle, so the gate declared the WHOLE cycle "complete" and skipped the
    fan-out while lead+1/lead+2 city scopes were still un-captured. Those scopes were then
    permanently stranded: the q-path (replacement_forecast_materializer.py:966-975 ->
    read_current_instrument_values) found no current single_runs row, returned None, and
    q_shape fell back to the old non-fused posterior shape
    (EXTRAS_CURRENT_CYCLE_COMPLETE_SKIPPED fired 318×; lead+1 was 93% STALE). The new gate is
    coverage-aware (``_extras_coverage_missing``): incomplete iff a PLANNED scope's own
    single_runs is absent, so it keeps re-running until every planned lead's scopes land.

    TERMINATION (the loop provably halts — no infinite re-run). Two independent bounds:
      A. PER-CYCLE FIXPOINT (the explicit unservable-case handler). Each fan-out pass is
         per-row idempotent (bayes_precision_fusion_download.py:918-957) so the covered set for
         a fixed cycle C is monotone non-decreasing. ``_record_extras_fixpoint`` watches the
         pass's own ``written_row_count``: a pass that lands ZERO new rows while still
         incomplete means the residual scopes are unservable for C right now (Open-Meteo beyond
         its publish horizon, a city/model it will not serve this cycle, or a statically-
         excluded model the downloader never even requests) -> it LATCHES, and this gate then
         returns False (complete-with-gap, logged). Any later progress un-latches. So for a
         FIXED C the fan-out runs at most until the covered count stops increasing — a strictly
         monotone bounded sequence -> finite re-runs. This distinguishes "not yet captured but
         servable -> re-run" (written>0 keeps healing) from "unservable -> complete-with-gap".
      B. CROSS-CYCLE ROLLOVER (makes complete-with-gap safe). The probe is keyed to
         ``_probe_resolved_bayes_precision_fusion_extras_cycle()`` — the newest cycle the
         BPF extras single-runs transport itself can serve on the fixed 00/06/12/18Z grid
         (replacement_cycle_availability.py:47), monotone in publish order. Within ~6h the
         next single-runs cycle publishes, the probe advances to C', the latch (keyed on C's
         ISO) goes stale, and C' is healed from scratch. A permanently-unservable scope thus
         halts looping for C but never poisons C+1.
         => INVARIANT: for any cycle C the fan-out runs on finitely many ticks — bounded by
            min(ticks-until-covered-count-stops-rising, C's ~6h active-probe window) — and the
            unservable residual is surfaced (logged), never silently looped on.
    """
    try:
        if cycle is None:
            cycle = _probe_resolved_bayes_precision_fusion_extras_cycle()
        if cycle is None:
            return True  # no cycle known; fail-open
        cov = _extras_coverage_missing(cfg, cycle)
        if cov is None:
            return True  # probe error -> fail-open (run the extras)
        missing, planned = cov
        if not missing:
            return False  # every planned scope captured for this cycle => complete (terminates)
        if _extras_fixpoint_latched(cycle):
            held_missing = _held_position_extras_missing_scopes(cfg, missing)
            if held_missing:
                logger.warning(
                    "BAYES_PRECISION_FUSION extras FIXPOINT pierced for held positions at cycle %s: "
                    "%d held scope(s) still missing current single_runs; re-running fan-out for "
                    "live redecision: %s",
                    cycle.isoformat(),
                    len(held_missing),
                    ", ".join(sorted(f"{c}/{m}/{d}" for c, m, d in held_missing)[:20]),
                )
                return True
            # Residual is a proven unservable-this-cycle fixpoint -> stop re-running (the latch
            # auto-clears when the cycle advances; bound B). Surface that we are skipping ON a gap.
            logger.info(
                "BAYES_PRECISION_FUSION extras coverage-incomplete for cycle %s but FIXPOINT-latched "
                "(%d/%d planned scopes unservable this cycle) -> skip re-run (complete-with-gap)",
                cycle.isoformat(),
                len(missing),
                planned,
            )
            return False
        logger.info(
            "BAYES_PRECISION_FUSION extras coverage-incomplete for cycle %s: %d/%d planned "
            "scopes still missing single_runs (re-running fan-out): %s",
            cycle.isoformat(),
            len(missing),
            planned,
            ", ".join(sorted(f"{c}/{m}/{d}" for c, m, d in missing)[:20]),
        )
        return True
    except Exception:
        return True  # fail-open: if we can't probe, run the extras


def _per_leg_downloaded_cycle(forecast_db: Path, source_id: str) -> datetime | None:
    """Per-source high-water mark of downloaded raw-input cycles (None = unknown → fetch).

    Same fail-open contract as _max_downloaded_current_target_cycle, but scoped to the
    live OpenMeteo anchor source."""
    from src.state.db import _connect  # noqa: PLC0415

    try:
        conn = _connect(Path(forecast_db))
        try:
            row = conn.execute(
                "SELECT MAX(source_cycle_time) FROM raw_forecast_artifacts"
                " WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            if row is None or row[0] is None:
                return None
            return datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
        finally:
            conn.close()
    except Exception:
        return None


def _current_target_anchor_gap_count(
    forecast_db: Path,
    cycle: datetime,
) -> int | None:
    """Return exact-cycle current-target anchor gaps (None = unreadable -> retry)."""

    if not forecast_db.exists():
        return 0
    try:
        from src.data.replacement_forecast_current_target_plan import (  # noqa: PLC0415
            build_replacement_forecast_current_target_plan,
        )

        plan = build_replacement_forecast_current_target_plan(
            forecast_db,
            required_openmeteo_source_cycle_time=cycle,
        )
        return int(plan.missing_openmeteo_manifest_count)
    except Exception:
        return None


def _held_common_cycle_recovery_targets(
    forecast_db: Path,
    *,
    decision_time: datetime,
) -> tuple[tuple[datetime, tuple[tuple[str, str, str], ...]], ...] | None:
    """Return held scopes whose posterior trails their newest common input cycle.

    The provider anchor can advance beyond ENS after a held scope missed one
    bounded download slice. Fetching only the newest anchor then cannot heal the
    posterior: same-cycle probability law rejects ``new anchor + older ENS``.
    Recovery includes both missing anchors and already-committed anchors whose
    reseed was lost across a crash/restart boundary. ``None`` means the evidence
    was unreadable and must be retried.
    """

    from src.data.replacement_forecast_seed_discovery import (  # noqa: PLC0415
        held_position_family_priorities,
    )
    from src.data.replacement_forecast_current_target_plan import (  # noqa: PLC0415
        SOURCE_ID,
    )
    from src.data.replacement_forecast_materialization_seed_builder import (  # noqa: PLC0415
        latest_baseline_coverage_for_replacement_seed,
    )
    from src.data.replacement_input_hwm import (  # noqa: PLC0415
        latest_eligible_ensemble_input_cycle,
    )
    from src.state.db import _connect_read_only  # noqa: PLC0415

    held_scopes = tuple(sorted(held_position_family_priorities()))
    if not held_scopes:
        return ()
    conn = None
    try:
        conn = _connect_read_only(forecast_db)
        conn.execute("PRAGMA query_only=ON")
        scopes_by_cycle: dict[datetime, list[tuple[str, str, str]]] = {}
        for city, target_date, metric in held_scopes:
            ensemble_hwm = latest_eligible_ensemble_input_cycle(
                conn,
                city=city,
                target_date=target_date,
                metric=metric,
                decision_time=decision_time,
            )
            if ensemble_hwm is None:
                continue
            ensemble_hwm = ensemble_hwm.astimezone(timezone.utc)
            baseline = latest_baseline_coverage_for_replacement_seed(
                conn,
                city=city,
                target_date=target_date,
                temperature_metric=metric,
                not_after_source_cycle_time=ensemble_hwm,
                as_of_time=decision_time,
            )
            if baseline is None:
                continue
            baseline_cycle = datetime.fromisoformat(
                str(baseline["source_cycle_time"]).replace("Z", "+00:00")
            ).astimezone(timezone.utc)
            # Both high-water marks are authority-filtered as of the same
            # decision clock. Their minimum is the newest cycle neither leg
            # outruns; the materializer still performs exact same-cycle
            # identity validation before publishing q.
            common_cycle = min(baseline_cycle, ensemble_hwm)
            row = conn.execute(
                """
                SELECT source_cycle_time
                FROM forecast_posteriors
                WHERE source_id = ?
                  AND city = ?
                  AND target_date = ?
                  AND temperature_metric = ?
                  AND runtime_layer = 'live'
                ORDER BY computed_at DESC, posterior_id DESC
                LIMIT 1
                """,
                (SOURCE_ID, city, target_date, metric),
            ).fetchone()
            posterior_cycle = None
            if row is not None and row[0]:
                posterior_cycle = datetime.fromisoformat(
                    str(row[0]).replace("Z", "+00:00")
                ).astimezone(timezone.utc)
            if posterior_cycle is not None and posterior_cycle >= common_cycle:
                continue
            scopes_by_cycle.setdefault(common_cycle, []).append(
                (city, target_date, metric)
            )
    except Exception:
        return None
    finally:
        if conn is not None:
            conn.close()

    return tuple(
        (cycle, tuple(scopes))
        for cycle, scopes in sorted(scopes_by_cycle.items())
    )


def _held_common_cycle_anchor_gaps(
    forecast_db: Path,
    *,
    decision_time: datetime,
) -> tuple[tuple[datetime, tuple[tuple[str, str, str], ...]], ...] | None:
    """Return only missing-anchor subsets of held common-cycle q debt."""

    targets = _held_common_cycle_recovery_targets(
        forecast_db,
        decision_time=decision_time,
    )
    if targets is None:
        return None
    batches: list[tuple[datetime, tuple[tuple[str, str, str], ...]]] = []
    for cycle, scopes in targets:
        missing = _critical_scopes_missing_current_anchor(
            forecast_db,
            scopes,
            cycle,
        )
        if missing is None:
            return None
        if missing:
            batches.append((cycle, missing))
    return tuple(batches)


def _recover_held_common_cycle_anchors_if_needed(
    cfg: Mapping[str, object],
    *,
    decision_time: datetime | None = None,
) -> dict[str, object] | None:
    """Capture exact missing anchor legs for held scopes' common input cycle."""

    forecast_db = cfg.get("forecast_db")
    output_dir = cfg.get("download_output_dir") or cfg.get("raw_manifest_dir")
    if forecast_db is None or output_dir is None:
        return None
    from scripts.download_replacement_forecast_current_targets import (  # noqa: PLC0415
        download_current_target_openmeteo_inputs,
    )

    now = (decision_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
    forecast_db_path = Path(str(forecast_db))
    batches = _held_common_cycle_recovery_targets(
        forecast_db_path,
        decision_time=now,
    )
    if batches is None:
        return {
            "status": "HELD_COMMON_CYCLE_EVIDENCE_UNREADABLE_RETRY",
            "decision_time": now.isoformat(),
            "recoveries": [],
            "committed_families": (),
        }
    report: dict[str, object] = {
        "status": (
            "HELD_COMMON_CYCLE_GAPS_FOUND"
            if batches
            else "HELD_COMMON_CYCLE_CURRENT"
        ),
        "decision_time": now.isoformat(),
        "recoveries": [],
        "committed_families": (),
    }
    anchor_hwm = _per_leg_downloaded_cycle(
        forecast_db_path,
        "openmeteo_ecmwf_ifs_9km",
    )
    rolled_past = 0
    committed_families: list[tuple[str, str, str]] = []
    for cycle, scopes in batches:
        missing_before = _critical_scopes_missing_current_anchor(
            forecast_db_path,
            scopes,
            cycle,
        )
        if missing_before is None:
            report["status"] = "HELD_COMMON_CYCLE_RECOVERY_PARTIAL"
            report["recoveries"].append(  # type: ignore[union-attr]
                {
                    "cycle": cycle.isoformat(),
                    "scopes": [list(scope) for scope in scopes],
                    "status": "ANCHOR_EVIDENCE_UNREADABLE_RETRY",
                    "committed_families": [],
                }
            )
            continue
        missing_set = set(missing_before)
        ready_before = tuple(scope for scope in scopes if scope not in missing_set)
        committed_families.extend(ready_before)
        if missing_before and anchor_hwm is not None and cycle < anchor_hwm:
            rolled_past += 1
            report["recoveries"].append(  # type: ignore[union-attr]
                {
                    "cycle": cycle.isoformat(),
                    "scopes": [list(scope) for scope in scopes],
                    "status": "PROVIDER_CYCLE_ROLLED_PAST",
                    "anchor_hwm": anchor_hwm.isoformat(),
                    "committed_families": [
                        list(scope) for scope in ready_before
                    ],
                }
            )
            continue
        try:
            recovered: tuple[tuple[str, str, str], ...] = ready_before
            if missing_before:
                result = download_current_target_openmeteo_inputs(
                    forecast_db=forecast_db_path,
                    output_dir=Path(str(output_dir)),
                    cycle=cycle,
                    limit=None,
                    write_db=True,
                    release_lag_hours=float(
                        cfg.get("download_release_lag_hours") or 14.0
                    ),
                    anchor_sigma_c=float(
                        cfg.get("download_anchor_sigma_c") or 3.0
                    ),
                    required_scopes=missing_before,
                    fetch_workers=int(cfg.get("source_clock_fanout_workers") or 4),
                    quota_priority=True,
                )
                # SCOPE: only exact held families requested in this recovery batch.
                # DRAIN: re-read canonical exact-cycle coverage after the downloader
                # commits; a count or sibling manifest is never family evidence.
                # RESET: only families absent from the post-commit missing set may
                # publish a reseed; unreadable evidence remains retryable.
                missing_after = _critical_scopes_missing_current_anchor(
                    forecast_db_path,
                    missing_before,
                    cycle,
                )
                if missing_after is None:
                    report["status"] = "HELD_COMMON_CYCLE_RECOVERY_PARTIAL"
                else:
                    missing_after_set = set(missing_after)
                    newly_recovered = tuple(
                        scope
                        for scope in missing_before
                        if scope not in missing_after_set
                    )
                    committed_families.extend(newly_recovered)
                    recovered = (*ready_before, *newly_recovered)
            else:
                result = {
                    "status": "ANCHOR_ALREADY_CURRENT_RESEED_REQUIRED",
                    "written_manifest_count": 0,
                    "written_manifests": [],
                }
            recovery: dict[str, object] = {
                "cycle": cycle.isoformat(),
                "scopes": [list(scope) for scope in scopes],
                "status": result.get("status"),
                "written_manifest_count": result.get("written_manifest_count"),
                "written_manifests": list(result.get("written_manifests") or ()),
                "committed_families": [list(scope) for scope in recovered],
            }
            if recovered:
                manifest_paths = tuple(
                    str(path)
                    for path in (result.get("written_manifests") or ())
                    if str(path).strip()
                )
                reseed_kwargs: dict[str, object] = {"scopes": recovered}
                if manifest_paths:
                    reseed_kwargs["manifest_snapshot"] = {
                        "manifest_paths": manifest_paths
                    }
                reseed = _enqueue_cycle_advance_reseeds_if_needed(
                    dict(cfg),
                    **reseed_kwargs,
                )
                if reseed is not None:
                    recovery["reseed_status"] = reseed.get("status")
                    recovery["seeds_enqueued"] = reseed.get("seeds_enqueued")
                    reseed_status = str(reseed.get("status") or "")
                    reseed_error = (
                        None
                        if reseed_status == "CYCLE_ADVANCE_TRIGGER"
                        else f"cycle_advance:{reseed_status or 'RESEED_STATUS_MISSING'}"
                    )
                    if reseed_error is not None:
                        report["status"] = "HELD_COMMON_CYCLE_RECOVERY_PARTIAL"
                        recovery["reseed_error"] = reseed_error
            report["recoveries"].append(recovery)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 - next poll retries exact gaps.
            report["status"] = "HELD_COMMON_CYCLE_RECOVERY_PARTIAL"
            report["recoveries"].append(  # type: ignore[union-attr]
                {
                    "cycle": cycle.isoformat(),
                    "scopes": [list(scope) for scope in scopes],
                    "status": "FETCH_FAILED_RETRY",
                    "error": str(exc)[:200],
                    "committed_families": [
                        list(scope) for scope in ready_before
                    ],
                }
            )
    if rolled_past == len(batches) and batches:
        report["status"] = "HELD_COMMON_CYCLE_GAPS_ROLLED_PAST"
    report["committed_families"] = tuple(dict.fromkeys(committed_families))
    return report


def _replacement_cycle_availability_poll_if_needed(
    cfg: dict[str, object],
    *,
    source_clock_report: object | None = None,
) -> dict[str, object] | None:
    """PROBE-RESOLVED raw-input fetch (operator directive 2026-06-11: automatic, ahead of
    need, no guessed numbers — K4.0b(a) availability-poll organ).

    Every poll tick:
      1. Resolve anchor published state of the recent cycles by PROBING the provider
         (src/data/replacement_cycle_availability.py). The release-lag constant takes NO
         part in this decision; it remains only the legacy cron's backstop schedule.
      2. Fetch the published anchor cycle when the journal does not yet hold it.
    Idempotent: source high-water marks short-circuit; the underlying downloader also
    skips already-present manifests. Fail-soft: a failed anchor fetch is retried on the
    next tick. Returns a compact report dict."""
    forecast_db = cfg.get("forecast_db")
    output_dir = cfg.get("download_output_dir") or cfg.get("raw_manifest_dir")
    if forecast_db is None or output_dir is None:
        return None
    from scripts.download_replacement_forecast_current_targets import (  # noqa: PLC0415
        download_current_target_openmeteo_inputs,
    )
    from src.data.replacement_cycle_availability import (  # noqa: PLC0415
        newest_complete_cycle,
        resolve_provider_anchor_cycle_availability,
    )

    now = datetime.now(timezone.utc)
    availability = resolve_provider_anchor_cycle_availability(now)
    forecast_db_path = Path(str(forecast_db))
    anchor_have = _per_leg_downloaded_cycle(forecast_db_path, "openmeteo_ecmwf_ifs_9km")
    newest_anchor_published = next((a.cycle for a in availability if a.anchor_available), None)
    anchor_missing_scope_count = (
        _current_target_anchor_gap_count(forecast_db_path, newest_anchor_published)
        if newest_anchor_published is not None
        else 0
    )
    anchor_cycle_advanced = (
        newest_anchor_published is not None
        and (anchor_have is None or newest_anchor_published > anchor_have)
    )

    fetch_anchor_cycle = (
        newest_anchor_published
        if newest_anchor_published is not None
        and (
            anchor_cycle_advanced
            or anchor_missing_scope_count is None
            or anchor_missing_scope_count > 0
        )
        else None
    )
    report: dict[str, object] = {
        "status": "AVAILABILITY_POLL",
        "now": now.isoformat(),
        "newest_anchor_published": newest_anchor_published.isoformat() if newest_anchor_published else None,
        "newest_complete_published": (
            newest_complete_cycle(availability).isoformat()
            if newest_complete_cycle(availability)
            else None
        ),
        "anchor_downloaded_cycle": anchor_have.isoformat() if anchor_have else None,
        "anchor_missing_scope_count": anchor_missing_scope_count,
        "legs_fetched": [],
    }
    try:
        if source_clock_report is None:
            from src.data.source_clock_update_probe import (  # noqa: PLC0415
                probe_openmeteo_source_clock_updates,
            )

            source_clock_report = probe_openmeteo_source_clock_updates(advance_cursor=False)
        source_clock_payload = source_clock_report.as_dict()
        report["source_clock_status"] = source_clock_payload.get("status")
        report["source_clock_updated_sources"] = source_clock_payload.get("updated_sources", [])
        report["source_clock_affected_cities"] = source_clock_payload.get("affected_cities", [])
        report["source_clock_error"] = source_clock_payload.get("error")
    except Exception as exc:  # noqa: BLE001 - source-clock probe must not break anchor polling
        report["source_clock_status"] = "SOURCE_CLOCK_PROBE_FAILSOFT_SKIPPED"
        report["source_clock_error"] = str(exc)[:200]
    recovery_report = _recover_held_common_cycle_anchors_if_needed(
        cfg,
        decision_time=now,
    )
    if recovery_report is not None:
        report["held_common_cycle_recovery_status"] = recovery_report.get(
            "status"
        )
        report["held_common_cycle_recovery"] = recovery_report.get("recoveries")
    recovery_active = bool(
        recovery_report
        and recovery_report.get("status") != "HELD_COMMON_CYCLE_CURRENT"
    )
    if fetch_anchor_cycle is None and not recovery_active:
        # Legs current — but do NOT return yet: the extras lane below must still run.
        # Leg currency does not imply the same-cycle multimodel extras exist (2026-06-11:
        # legs poll-fetched at 00Z while every extras row sat unfetched → q_lcb NULL).
        report["status"] = "AVAILABILITY_POLL_CURRENT"
    for leg, cycle in (
        ("anchor", fetch_anchor_cycle),
    ):
        if cycle is None:
            continue
        try:
            download_current_target_openmeteo_inputs(
                forecast_db=Path(str(forecast_db)),
                output_dir=Path(str(output_dir)),
                cycle=cycle,
                limit=int(cfg.get("download_limit") or 10),
                write_db=True,
                release_lag_hours=float(cfg.get("download_release_lag_hours") or 14.0),
                anchor_sigma_c=float(cfg.get("download_anchor_sigma_c") or 3.0),
                include_covered=anchor_cycle_advanced,
                missing_manifests_only=not anchor_cycle_advanced,
                fetch_workers=int(cfg.get("source_clock_fanout_workers") or 4),
                quota_priority=True,
            )
            report["legs_fetched"].append({"leg": leg, "cycle": cycle.isoformat()})  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 — anchor fail-soft; next tick retries
            logger.warning(
                "availability-poll %s leg fetch failed for cycle %s (retry next tick): %s",
                leg,
                cycle.isoformat(),
                exc,
            )
            report.setdefault("legs_failed", []).append(  # type: ignore[union-attr]
                {"leg": leg, "cycle": cycle.isoformat(), "error": str(exc)[:200]}
            )
    # The bayes_precision_fusion extras ride the SAME probe-driven tick (run-selection
    # single authority): fusion needs same-cycle multimodel rows to produce q_lcb, and the
    # lag-modeled cron (next fire hours away) left q_lcb NULL long after the probe poll had
    # already fetched the anchor leg (2026-06-11: 00Z posteriors materialized with
    # q_lcb NULL = honest no-edge = no orders, while every extras row sat unfetched).
    # Idempotent per persisted (model, city, target, metric, cycle, endpoint) row;
    # flag-gated + fail-soft inside — it never breaks the poll.
    #
    # R4b (2026-06-13): gate the extras fan-out so the 5-min poll does NOT re-drive the full
    # download on every tick. The extras are only needed when (a) a new anchor cycle was actually
    # fetched this tick, OR (b) the
    # current-cycle's extras are COVERAGE-incomplete (per-(city,metric,target_date) probe, fix
    # 2026-06-16 — was a coverage-blind flat row-count that stranded lead+1/+2 scopes).
    # When every planned scope is captured (or the residual is a proven unservable-this-cycle
    # fixpoint), skip. The next genuine publish re-triggers. Fail-open: any probe error -> run.
    #
    # CYCLE CAPTURED ONCE (architect cross-check 2026-06-16): resolve the probe cycle a single
    # time and reuse it for both the gate and the post-pass fixpoint record so the latch can
    # never key to a cycle the gate didn't evaluate (the sub-second re-resolve race). The
    # fan-out re-resolves internally for its OWN target build; momentary disagreement costs at
    # most one benign extra pass and self-corrects next tick.
    _extras_cycle = _probe_resolved_bayes_precision_fusion_extras_cycle()
    _should_run_extras = _extras_cycle_incomplete(cfg, _extras_cycle)
    if _should_run_extras:
        bayes_precision_fusion_report = _download_bayes_precision_fusion_extra_raw_inputs_if_needed(cfg)
        if bayes_precision_fusion_report is not None:
            _bpf_status = bayes_precision_fusion_report.get("status")
            report["bayes_precision_fusion_extras_status"] = _bpf_status
            # Fixpoint record (termination bound A): latch complete-with-gap when THIS pass
            # landed 0 new rows while still incomplete; un-latch on progress. Uses the pass's
            # own written_row_count — the per-row-idempotent downloader makes 0 the honest
            # "nothing new servable" signal. Keyed on _extras_cycle; auto-clears on rollover.
            # ONLY record on a status that actually RAN the download to completion: a fail-soft
            # skip (FAILSOFT_SKIPPED / NO_TARGETS / UNRESOLVED_SKIP) carries no written_row_count
            # and is a TRANSIENT error, NOT proof the residual is unservable — latching on it
            # would wrongly suppress the self-healing re-run. (Distinguishes "unservable ->
            # complete-with-gap" from "transient fan-out error -> keep re-running".)
            if _extras_cycle is not None and _bpf_status == "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED":
                _record_extras_fixpoint(
                    cfg,
                    _extras_cycle,
                    written=int(bayes_precision_fusion_report.get("written_row_count", 0) or 0),
                )
    else:
        report["bayes_precision_fusion_extras_status"] = "EXTRAS_CURRENT_CYCLE_COMPLETE_SKIPPED"
    # Task #32 — PARTIAL-fusion UPGRADE TRIGGER. The extras fetch above may have just landed a
    # decorrelated provider's current value (single_runs row) for a scope whose latest posterior
    # was fused from a strictly smaller instrument set. This availability-poll lane already KNOWS
    # the moment new rows land, so the upgrade re-seed rides the SAME tick (operator law
    # 下载有自己的daemon — no new daemon, no parallel materialization path). It writes a seed into
    # the SAME seed_dir the materialize cycle drains; idempotent per (scope, cycle,
    # capturable-family-superset) via the fusion_upgrade_enqueues marker. Fail-soft: a trigger
    # error is logged and never breaks the poll.
    upgrade_report = _enqueue_fusion_upgrade_reseeds_if_needed(cfg)
    if upgrade_report is not None:
        report["fusion_upgrade_status"] = upgrade_report.get("status")
        report["fusion_upgrade_seeds_enqueued"] = upgrade_report.get("seeds_enqueued")
        if upgrade_report.get("upgrades_detected"):
            report["fusion_upgrade_detail"] = {
                k: upgrade_report.get(k)
                for k in ("upgrades_detected", "seeds_enqueued", "already_enqueued", "enqueued")
            }
    # U5 step 2a — NEWER-CYCLE re-materialization TRIGGER (sister of the fusion-upgrade trigger).
    # This availability-poll lane already KNOWS the moment a fresher cycle's raw legs land (the
    # anchor fetch above), so the cycle-advance re-seed rides the SAME tick (operator law:
    # 下载有自己的daemon — no new daemon, no parallel materialization path). It enqueues ONE seed per
    # active-window family whose latest posterior consumed a STRICTLY older cycle than the freshest
    # materializable one, HELD positions first, idempotent per (scope, target-cycle). Fail-soft.
    cycle_advance_report = _enqueue_cycle_advance_reseeds_if_needed(cfg)
    if cycle_advance_report is not None:
        report["cycle_advance_status"] = cycle_advance_report.get("status")
        report["cycle_advance_seeds_enqueued"] = cycle_advance_report.get("seeds_enqueued")
        if cycle_advance_report.get("advances_detected"):
            report["cycle_advance_detail"] = {
                k: cycle_advance_report.get(k)
                for k in (
                    "freshest_materializable_cycle",
                    "advances_detected",
                    "held_advances_detected",
                    "seeds_enqueued",
                    "held_seeds_enqueued",
                    "already_enqueued",
                    "manifest_missing",
                    "enqueued",
                )
            }
    return report


def _prepared_reseed_manifests(
    raw_manifest_dir: object,
    manifest_snapshot: dict[str, object] | None,
) -> tuple[datetime | None, object | None]:
    if manifest_snapshot is None:
        return None, None
    from src.data.replacement_forecast_seed_discovery import (  # noqa: PLC0415
        _load_manifests,
    )

    computed_at = manifest_snapshot.get("computed_at")
    if not isinstance(computed_at, datetime):
        computed_at = datetime.now(timezone.utc)
        manifest_snapshot["computed_at"] = computed_at
    if "manifests" not in manifest_snapshot:
        manifest_paths = manifest_snapshot.get("manifest_paths")
        if isinstance(manifest_paths, (tuple, list)) and manifest_paths:
            from src.data.replacement_forecast_seed_discovery import (  # noqa: PLC0415
                _load_manifest_files,
            )

            manifest_snapshot["manifests"] = _load_manifest_files(
                manifest_paths,
                computed_at=computed_at,
            )
        else:
            manifest_snapshot["manifests"] = _load_manifests(
                Path(str(raw_manifest_dir)),
                computed_at=computed_at,
            )
    return computed_at, manifest_snapshot["manifests"]


def _enqueue_fusion_upgrade_reseeds_if_needed(
    cfg: dict[str, object],
    *,
    scopes: Sequence[tuple[str, str, str]] | None = None,
    changed_sources: Sequence[str] | None = None,
    manifest_snapshot: dict[str, object] | None = None,
    limit: int | None = None,
) -> dict[str, object] | None:
    """Enqueue scopes whose provider set or consumed raw input revision changed.

    Returns None when required paths are not configured. Errors remain fail-soft so source ingest
    can continue and the periodic catch-up lane can retry.
    """
    forecast_db = cfg.get("forecast_db")
    seed_dir = cfg.get("seed_dir")
    raw_manifest_dir = cfg.get("raw_manifest_dir")
    if forecast_db is None or seed_dir is None or raw_manifest_dir is None:
        return None
    try:
        computed_at, manifests = _prepared_reseed_manifests(
            raw_manifest_dir,
            manifest_snapshot,
        )
        from src.data.replacement_fusion_upgrade_trigger import (  # noqa: PLC0415
            enqueue_fusion_upgrade_reseeds,
        )

        return enqueue_fusion_upgrade_reseeds(
            forecast_db=Path(str(forecast_db)),
            seed_dir=Path(str(seed_dir)),
            raw_manifest_dir=Path(str(raw_manifest_dir)),
            limit=int(
                limit
                if limit is not None
                else cfg.get("seed_limit") or cfg.get("limit") or 10
            ),
            scopes=scopes,
            changed_sources=changed_sources,
            computed_at=computed_at,
            manifests=manifests,
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft: the trigger never breaks the poll
        logger.warning("fusion-upgrade trigger skipped (fail-soft): %s", exc)
        return {"status": "FUSION_UPGRADE_TRIGGER_FAILSOFT_SKIPPED", "error": str(exc)}


def _enqueue_cycle_advance_reseeds_if_needed(
    cfg: dict[str, object],
    *,
    scopes: Sequence[tuple[str, str, str]] | None = None,
    manifest_snapshot: dict[str, object] | None = None,
    limit: int | None = None,
    causal_baseline_source_run_id: str | None = None,
) -> dict[str, object] | None:
    """U5 step 2a — enqueue re-materialization seeds for active-window families whose latest
    posterior consumed a STRICTLY OLDER cycle than the freshest materializable in-universe cycle.
    Delegates the comparison + enqueue to the single-authority module so the rule lives at one site.
    HELD positions (read-only from zeus_trades.db) are prioritized. Returns the trigger report (None
    when seed_dir / forecast_db / raw_manifest_dir are not configured). Fail-soft: any error returns
    a status dict, never raises into the poll."""
    forecast_db = cfg.get("forecast_db")
    seed_dir = cfg.get("seed_dir")
    raw_manifest_dir = cfg.get("raw_manifest_dir")
    if forecast_db is None or seed_dir is None or raw_manifest_dir is None:
        return None
    try:
        computed_at, manifests = _prepared_reseed_manifests(
            raw_manifest_dir,
            manifest_snapshot,
        )
        from src.data.replacement_cycle_advance_trigger import (  # noqa: PLC0415
            enqueue_cycle_advance_reseeds,
        )
        from src.state.db import _zeus_trade_db_path  # noqa: PLC0415

        return enqueue_cycle_advance_reseeds(
            forecast_db=Path(str(forecast_db)),
            seed_dir=Path(str(seed_dir)),
            raw_manifest_dir=Path(str(raw_manifest_dir)),
            trades_db=_zeus_trade_db_path(),
            limit=int(
                limit
                if limit is not None
                else cfg.get("seed_limit") or cfg.get("limit") or 10
            ),
            scopes=scopes,
            computed_at=computed_at,
            manifests=manifests,
            include_missing_posterior=scopes is not None,
            causal_baseline_source_run_id=causal_baseline_source_run_id,
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft: the trigger never breaks the poll
        logger.warning("cycle-advance trigger skipped (fail-soft): %s", exc)
        return {"status": "CYCLE_ADVANCE_TRIGGER_FAILSOFT_SKIPPED", "error": str(exc)}


@_scheduler_job("anchor_meta_stamp_cross_check")
def _anchor_meta_stamp_cross_check() -> None:
    """Hourly: re-verify meta-stamped anchor artifacts against single-runs once the same
    run is served there (K4.0b(f) belt-and-suspenders; MISMATCH ⇒ ERROR + receipt)."""
    cfg = _replacement_forecast_live_materialization_queue_config()
    forecast_db = cfg.get("forecast_db")
    if forecast_db is None:
        return
    from src.data.anchor_cross_check import (  # noqa: PLC0415
        run_anchor_cross_check_cycle,
        run_bucket_anchor_cross_check_cycle,
    )

    report = run_anchor_cross_check_cycle(Path(str(forecast_db)))
    if report.get("checked") or report.get("errors"):
        logger.info("anchor meta-stamp cross-check report: %s", report)

    # Rung-3 bucket transport antibody: re-verify bucket artifacts against single-runs once
    # the run is served there. VERIFIED receipts grow the city whitelist that gates future
    # bucket serves; MISMATCH ⇒ ERROR + receipt (coastal/terrain city stays off the whitelist).
    bucket_report = run_bucket_anchor_cross_check_cycle(Path(str(forecast_db)))
    if bucket_report.get("checked") or bucket_report.get("errors"):
        logger.info("anchor bucket-transport cross-check report: %s", bucket_report)


@_scheduler_job("replacement_cycle_availability_poll")
def _replacement_cycle_availability_poll() -> None:
    """Interval job: probe provider publication state and fetch fresh raw-input legs the
    moment they exist — BEFORE the engine needs them (operator directive 2026-06-11).
    Runs on the download lane; never blocks the 5-min materialize cycle."""
    cfg = _replacement_forecast_live_materialization_queue_config()
    report = _replacement_cycle_availability_poll_if_needed(cfg)
    if report is None:
        return
    if report.get("status") == "AVAILABILITY_POLL_CURRENT":
        logger.debug("cycle availability poll current: %s", report)
    else:
        logger.info("cycle availability poll report: %s", report)


def _ingest_station_forecasts_live(cfg: dict[str, object]) -> dict[str, int] | None:
    """Config-driven station-forecast (CWA/HKO) live ingest into raw_model_forecasts.

    Runs on the download lane (publish-time cron + boot catch-up), so station data refreshes at
    the same ~2x/day cadence as the gridded raw inputs the lane already fetches. Uses an AUTOCOMMIT
    connection: each tiny per-row write self-commits, so no write lock is held across a provider
    network fetch (avoids the forecast-DB "database is locked" contention the heavy capture guards
    against with BEGIN IMMEDIATE). Fail-soft end to end: returns None on any setup error, and the
    dispatcher swallows per-source provider errors, so station ingest can never kill the cycle.
    Returns ``{source_id: rows_written}`` or None.
    """
    forecast_db = cfg.get("forecast_db")
    if forecast_db is None:
        return None
    try:
        from src.data.station_forecast_adapter import (  # noqa: PLC0415
            ingest_enabled_station_sources_live,
        )
        from src.state.db import _connect  # noqa: PLC0415

        conn = _connect(Path(str(forecast_db)), write_class="live")
        # Autocommit: tiny per-row INSERT self-commits; the network fetch inside each ingest
        # function holds no write transaction. _persist_rows is autocommit-safe by contract.
        conn.isolation_level = None
        try:
            return ingest_enabled_station_sources_live(conn)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - station ingest must never break the download cycle
        logger.warning("station-forecast live ingest skipped (fail-soft): %s", exc)
        return None


# Re-home guard (2026-07-20): the 2026-06-11 download-lane migration ("下载有自己的daemon") moved the
# gridded download off forecast-live into ingest_main's availability poll, but LEFT BEHIND the station
# ingest call above (it lived only in the now-descheduled _replacement_forecast_download_cycle). cwa_
# township/hko_fnd went dark 2026-07-17 as a result. This due-gated wrapper re-homes the call onto the
# (fast) availability poll: a monotonic-clock gate fetches at most ~1×/interval so the poll never hammers
# the CWA/HKO provider APIs, matching the original ~4×/day download-cron cadence. Fail-soft throughout.
_STATION_INGEST_MIN_INTERVAL_S = 3 * 3600
_last_station_ingest_monotonic: float | None = None


def _ingest_station_forecasts_if_due(cfg: dict[str, object]) -> dict[str, int] | None:
    """Staleness-gated station-forecast ingest for the availability-poll lane. Returns the ingest
    report when a fetch was due (and ran), else None. Never raises: the monotonic gate is advanced
    before the fetch so a transient provider failure waits one interval rather than hammering."""
    global _last_station_ingest_monotonic
    import time  # noqa: PLC0415

    now = time.monotonic()
    if (
        _last_station_ingest_monotonic is not None
        and (now - _last_station_ingest_monotonic) < _STATION_INGEST_MIN_INTERVAL_S
    ):
        return None
    _last_station_ingest_monotonic = now
    return _ingest_station_forecasts_live(cfg)


@_scheduler_job("replacement_forecast_download")
def _replacement_forecast_download_cycle() -> None:
    """Proactive raw-input PRE-FETCH for the BAYES_PRECISION_FUSION/replacement soft-anchor forecast.

    Operator directive 2026-06-08 (WIRING FIX): forecast raw-input downloads
    MUST NOT run inside the 5-min seed->materialize cycle. When large downloads ran
    inline the materialize job overran its 5-min interval and apscheduler SKIPPED
    every subsequent cycle ("maximum number of running instances reached") — seeds
    never got produced and readiness went permanently stale. Raw inputs are DATA
    and must be fetched ahead of need on a slower, independent lane; the trade-
    producing materialize cycle then only consumes already-downloaded manifests.

    Runs on the default executor (20-worker pool) on its own long interval, so it
    overlaps the fast materialize cycle on a separate thread without blocking it.
    Fail-soft and idempotent (skips already-downloaded manifests)."""
    cfg = _replacement_forecast_live_materialization_queue_config()
    try:
        from src.data.source_clock_update_probe import (  # noqa: PLC0415
            probe_openmeteo_source_clock_updates,
        )

        source_clock_report = probe_openmeteo_source_clock_updates(advance_cursor=False)
        logger.info("source-clock model update probe report: %s", source_clock_report.as_dict())
    except Exception as exc:  # noqa: BLE001 - source-clock metadata cannot kill raw downloads
        logger.warning("source-clock model update probe skipped (fail-soft): %s", exc)
    download_report = _download_replacement_forecast_current_targets_if_needed(cfg)
    if download_report is not None:
        _dl_status = download_report.get("status")
        if _dl_status in {
            "CURRENT_TARGETS_ALREADY_COVERED",
            "CURRENT_TARGETS_HAVE_RAW_MANIFESTS",
        }:
            # ANTI-SILENT-SKIP (2026-06-09): the suppressed skip is what made the frozen-anchor
            # failure invisible for 24h. A skip must self-declare its cycle facts (compact, the
            # download job runs ~2x/day so this is cheap).
            logger.info(
                "replacement current-target download skipped (%s): available_cycle=%s "
                "downloaded_cycle=%s",
                _dl_status,
                download_report.get("available_cycle"),
                download_report.get("downloaded_cycle"),
            )
        else:
            logger.info(
                "replacement forecast current-target download report: %s", download_report
            )
    # THE_PATH BAYES_PRECISION_FUSION-Bayes multi-model capture/accrual (forward + fixed-lead), gated by the
    # Writes only
    # raw_model_forecasts here; downstream live materialization consumes those rows to build
    # replacement posteriors. Fail-soft.
    bayes_precision_fusion_capture_report = _download_bayes_precision_fusion_extra_raw_inputs_if_needed(cfg)
    if bayes_precision_fusion_capture_report is not None and bayes_precision_fusion_capture_report.get("status") not in {
        "BAYES_PRECISION_FUSION_EXTRA_NO_TARGETS",
    }:
        logger.info("BAYES_PRECISION_FUSION extra-model raw-input capture report: %s", bayes_precision_fusion_capture_report)
    # SILENT-DEATH SURFACING (2026-06-09): if the extras sub-step fails, probe-skips,
    # or downloads with missing global instruments, the parent download job still
    # shows OK. Write a distinct component entry so preflight can fail on the
    # actual fusion-capture state.
    if bayes_precision_fusion_capture_report is not None:
        _record_bayes_precision_fusion_capture_health(cfg, bayes_precision_fusion_capture_report)
    # STATION-CALIBRATED forecast ingest (CWA township / HKO fnd) runs on ingest_main's availability
    # poll via _ingest_station_forecasts_if_due — NOT here. This cycle is unscheduled (telemetry
    # only), so it must not duplicate that ingest: doing so would double-fetch the provider APIs.
    # Release the queue lock after one micro-batch so a newly arrived source can
    # preempt old catch-up debt on the 1s poll lane. Discovery consumes existing
    # explicit requests and newly discovered seeds in the same priority sort.
    catchup_report = _run_replacement_forecast_live_materialization_queue_once(
        cfg,
        discover=True,
        limit=int(cfg["poll_batch_limit"]),
    )
    if (
        catchup_report.processed_count
        or catchup_report.seed_processed_count
        or catchup_report.failed_count
        or catchup_report.seed_failed_count
    ):
        logger.info(
            "replacement forecast live materialization download-catchup: %s",
            catchup_report.as_dict(),
        )


@_scheduler_job("replacement_forecast_live_materialize")
def _replacement_forecast_live_materialize_cycle(
    *,
    discover: bool = True,
    limit: int | None = None,
    seed_limit: int | None = None,
) -> None:
    cfg = _replacement_forecast_live_materialization_queue_config()
    report = _run_replacement_forecast_live_materialization_queue_once(
        cfg,
        discover=discover,
        limit=limit,
        seed_limit=seed_limit,
    )
    _log_replacement_forecast_materialization_report(report)


def _run_replacement_forecast_live_materialization_queue_once(
    cfg: dict[str, object],
    *,
    discover: bool = True,
    limit: int | None = None,
    seed_limit: int | None = None,
):
    from src.data.replacement_forecast_live_materialization_queue import (
        process_replacement_forecast_live_materialization_queue,
    )

    revision_before = _forecast_posterior_revision(cfg)
    batch_limit = int(cfg["limit"] if limit is None else limit)
    seed_batch_limit = min(
        int(cfg["seed_limit"]),
        batch_limit if seed_limit is None else max(0, int(seed_limit)),
    )
    report = process_replacement_forecast_live_materialization_queue(
        request_dir=cfg["request_dir"],
        processed_dir=cfg["processed_dir"],
        failed_dir=cfg["failed_dir"],
        seed_dir=cfg["seed_dir"],
        seed_processed_dir=cfg["seed_processed_dir"],
        seed_failed_dir=cfg["seed_failed_dir"],
        forecast_db=cfg["forecast_db"],
        raw_manifest_dir=cfg["raw_manifest_dir"],
        seed_discovery_limit=min(int(cfg["seed_discovery_limit"]), batch_limit),
        seed_limit=seed_batch_limit,
        limit=batch_limit,
        discover=discover,
    )
    revision_after = _forecast_posterior_revision(cfg)
    if (
        revision_before is not None
        and revision_after is not None
        and revision_after > revision_before
    ):
        committed_posterior_count = int(
            getattr(report, "committed_posterior_count", 0) or 0
        )
        reactor_wake_published_count = int(
            getattr(report, "reactor_wake_published_count", 0) or 0
        )
        if (
            committed_posterior_count > 0
            and reactor_wake_published_count == committed_posterior_count
        ):
            logger.info(
                "forecast posterior advanced rowid=%d->%d; %d committed posteriors "
                "already covered by commit wakes",
                revision_before,
                revision_after,
                reactor_wake_published_count,
            )
            return report
        from src.runtime.reactor_wake import publish_reactor_wake

        forecast_families = _forecast_posterior_families_between(
            cfg,
            revision_before=revision_before,
            revision_after=revision_after,
        )
        try:
            wake = publish_reactor_wake(
                source="replacement_forecast_production",
                reason="forecast_posterior_advanced",
                forecast_families=forecast_families,
            )
        except Exception:
            logger.warning(
                "forecast posterior advanced rowid=%d->%d but reactor wake publish "
                "failed; periodic reactor scan remains authoritative",
                revision_before,
                revision_after,
                exc_info=True,
            )
        else:
            logger.info(
                "forecast posterior advanced rowid=%d->%d families=%d; "
                "reactor wake published id=%s",
                revision_before,
                revision_after,
                len(forecast_families),
                wake.wake_id,
            )
    return report


_CURRENT_POSTERIOR_FAMILY_SCAN_SQL = """
    SELECT city,
           target_date,
           temperature_metric,
           MAX(computed_at) AS latest_computed_at,
           MAX(posterior_id) AS latest_posterior_id
      FROM forecast_posteriors
           INDEXED BY idx_forecast_posteriors_runtime_layer_target
     WHERE runtime_layer = 'live'
     GROUP BY city, target_date, temperature_metric
     ORDER BY latest_computed_at DESC, latest_posterior_id DESC
     LIMIT ?
"""


def _current_forecast_posterior_families(
    cfg: dict[str, object],
    *,
    limit: int = 100,
) -> tuple[tuple[str, str, str], ...]:
    """Return current live families from the covering index, newest first.

    This is boot catch-up scope only; it never grants probability authority.
    Live materialization writes ``training_allowed=0`` by contract, while every
    woken family is re-read and re-authorized downstream.  Keeping the query on
    the runtime-layer covering index avoids decoding the table's large q/provenance
    payloads after a cold daemon restart.
    """

    raw_path = cfg.get("forecast_db")
    if not raw_path:
        return ()
    path = Path(str(raw_path)).expanduser().resolve()
    if not path.exists():
        return ()
    conn = None
    try:
        from src.state.db import _connect_read_only

        conn = _connect_read_only(path)
        family_limit = max(1, min(int(limit), 100))
        rows = conn.execute(
            _CURRENT_POSTERIOR_FAMILY_SCAN_SQL,
            (family_limit,),
        ).fetchall()
        families: list[tuple[str, str, str]] = []
        for row in rows:
            family = (
                str(row[0] or "").strip(),
                str(row[1] or "").strip(),
                str(row[2] or "").strip(),
            )
            if all(family):
                families.append(family)
        return tuple(families)
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return ()
    finally:
        if conn is not None:
            conn.close()


def _publish_current_forecast_posterior_wake(
    cfg: dict[str, object],
):
    """Queue one boot catch-up for current posteriors committed before restart."""

    families = _current_forecast_posterior_families(cfg)
    if not families:
        return None
    from src.runtime.reactor_wake import publish_reactor_wake

    wake = publish_reactor_wake(
        source="forecast_live_boot_current_posterior",
        reason="forecast_posterior_advanced",
        forecast_families=families,
    )
    logger.info(
        "forecast-live boot current-posterior wake published families=%d id=%s",
        len(families),
        wake.wake_id,
    )
    return wake


def _forecast_posterior_families_between(
    cfg: dict[str, object],
    *,
    revision_before: int,
    revision_after: int,
) -> tuple[tuple[str, str, str], ...]:
    """Return changed live families, largest settlement-bin reversal first."""

    raw_path = cfg.get("forecast_db")
    if not raw_path or revision_after <= revision_before:
        return ()
    path = Path(str(raw_path)).expanduser().resolve()
    if not path.exists():
        return ()
    conn = None
    try:
        from src.state.db import _connect_read_only

        conn = _connect_read_only(path)
        rows = conn.execute(
            """
            SELECT changed.city,
                   changed.target_date,
                   changed.temperature_metric,
                   changed.latest_rowid,
                   latest.q_json,
                   (
                       SELECT previous.q_json
                         FROM forecast_posteriors AS previous
                        WHERE previous.city = changed.city
                          AND previous.target_date = changed.target_date
                          AND previous.temperature_metric = changed.temperature_metric
                          AND previous.runtime_layer = 'live'
                          AND previous.training_allowed = 0
                          AND previous.rowid <= ?
                        ORDER BY previous.rowid DESC
                        LIMIT 1
                   ) AS previous_q_json
              FROM (
                    SELECT city,
                           target_date,
                           temperature_metric,
                           MAX(rowid) AS latest_rowid
                      FROM forecast_posteriors
                     WHERE rowid > ?
                       AND rowid <= ?
                       AND runtime_layer = 'live'
                       AND training_allowed = 0
                     GROUP BY city, target_date, temperature_metric
                   ) AS changed
              JOIN forecast_posteriors AS latest
                ON latest.rowid = changed.latest_rowid
             LIMIT 101
            """,
            (revision_before, revision_before, revision_after),
        ).fetchall()
        ranked: list[tuple[float, int, tuple[str, str, str]]] = []
        for row in rows:
            family = (
                str(row[0] or "").strip(),
                str(row[1] or "").strip(),
                str(row[2] or "").strip(),
            )
            if all(family):
                ranked.append(
                    (
                        _posterior_max_bin_delta(row[4], row[5]),
                        int(row[3] or 0),
                        family,
                    )
                )
        if len(ranked) > 100:
            return ()
        ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
        return tuple(item[2] for item in ranked)
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return ()
    finally:
        if conn is not None:
            conn.close()


def _posterior_max_bin_delta(new_q_json: object, old_q_json: object) -> float:
    """Score one family by its largest changed settlement-bin probability."""

    if old_q_json is None:
        return 1.0
    try:
        new_q = json.loads(str(new_q_json))
        old_q = json.loads(str(old_q_json))
        if not isinstance(new_q, dict) or not isinstance(old_q, dict):
            return 1.0
        max_delta = 0.0
        for label in set(new_q) | set(old_q):
            new_value = float(new_q.get(label, 0.0))
            old_value = float(old_q.get(label, 0.0))
            if not math.isfinite(new_value) or not math.isfinite(old_value):
                return 1.0
            max_delta = max(max_delta, abs(new_value - old_value))
        return max_delta
    except (TypeError, ValueError, json.JSONDecodeError):
        return 1.0


def _forecast_posterior_revision(cfg: dict[str, object]) -> int | None:
    """Return the append-only posterior rowid high-water mark in constant time."""

    raw_path = cfg.get("forecast_db")
    if not raw_path:
        return None
    path = Path(str(raw_path)).expanduser().resolve()
    if not path.exists():
        return None
    conn = None
    try:
        from src.state.db import _connect_read_only

        conn = _connect_read_only(path)
        row = conn.execute(
            "SELECT COALESCE(MAX(rowid), 0) FROM forecast_posteriors"
        ).fetchone()
        return int(row[0] or 0) if row is not None else 0
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return None
    finally:
        if conn is not None:
            conn.close()


def _log_replacement_forecast_materialization_report(report) -> None:
    report_payload = report.as_dict()
    seed_discovery = report_payload.get("seed_discovery_report")
    seed_discovery_active = (
        isinstance(seed_discovery, dict)
        and (
            int(seed_discovery.get("discovered_count") or 0) > 0
            or int(seed_discovery.get("failed_count") or 0) > 0
        )
    )
    if report.failed_count or report.seed_failed_count or (
        isinstance(seed_discovery, dict) and int(seed_discovery.get("failed_count") or 0) > 0
    ):
        logger.warning("replacement forecast live materialization queue failures: %s", report_payload)
    elif report.processed_count or report.seed_processed_count or seed_discovery_active:
        logger.info("replacement forecast live materialization queue processed: %s", report_payload)
