"""Queue runner for replacement forecast live materialization requests."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping, Sequence

from src.config import PROJECT_ROOT
from src.contracts.replacement_pipeline_files import (
    ContractViolation,
    validate_materialization_request,
    validate_materialization_seed,
)
from src.data.replacement_forecast_cycle_policy import tradeable_grade_coverage_sql
from src.data.replacement_current_value_serving import (
    current_value_serving_schema,
    read_current_instrument_frontier_identity,
)
from src.data.replacement_input_hwm import replacement_live_input_lag_reason
from src.data.replacement_forecast_materialization_request_builder import (
    build_replacement_forecast_materialization_request,
)
from src.data.replacement_forecast_readiness import SOURCE_ID, STRATEGY_KEY
from src.data.replacement_forecast_seed_discovery import (
    ReplacementForecastSeedDiscoveryReport,
    discover_replacement_forecast_materialization_seeds,
)


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
DEFAULT_MATERIALIZATION_SUBPROCESS_TIMEOUT_SECONDS = 30.0
DEFAULT_RECENT_SUCCESS_COALESCE_SECONDS = 60.0
# Every subprocess commits to the same SQLite forecast DB. Parallel commit
# processes only multiply cold-page reads and writer contention. Keep one queue
# owner and one DB writer, but bound one pathological family to 30 seconds so it
# cannot stop current-q production for every other city.
DEFAULT_MATERIALIZATION_MAX_WORKERS = 1
MATERIALIZATION_INFLIGHT_DIR_NAME = "inflight"
_CLAIM_METADATA_NAME = "_claim.json"
_STALE_CLAIM_GRACE_SECONDS = 30.0
_TIMEOUT_RETRY_MARKER = ".timeout-retry-"
_TIMEOUT_RETRY_BASE_SECONDS = 60.0
_TIMEOUT_RETRY_MAX_SECONDS = 600.0
_TIMEOUT_RETRY_DEFERRED_REASON = (
    "REPLACEMENT_LIVE_MATERIALIZATION_TIMEOUT_RETRY_DEFERRED"
)
_DAY0_ENQUEUE_OWNERSHIP_CURSOR_NAME = ".replacement-day0-enqueue.cursor"
_DAY0_ENQUEUE_OWNERSHIP_INSPECTION_MULTIPLIER = 4
_DAY0_ENQUEUE_OWNERSHIP_MIN_INSPECTIONS = 8


class _Day0EnqueueOwnership(str, Enum):
    """Authoritative ownership state for a Day0 cycle-advance seed."""

    CURRENT = "CURRENT"
    STALE = "STALE"
    INDETERMINATE = "INDETERMINATE"


@dataclass(frozen=True)
class _Day0EnqueueOwnershipCheck:
    ownership: _Day0EnqueueOwnership
    witness: Mapping[str, str] | None = None


@dataclass(frozen=True)
class ReplacementForecastLiveMaterializationQueueReport:
    status: str
    request_dir: str
    processed_dir: str
    failed_dir: str
    processed_count: int
    failed_count: int
    skipped_count: int
    seed_processed_count: int = 0
    seed_failed_count: int = 0
    committed_posterior_count: int = 0
    reactor_wake_published_count: int = 0
    seed_discovery_report: ReplacementForecastSeedDiscoveryReport | None = None
    processed_files: tuple[str, ...] = ()
    failed_files: tuple[str, ...] = ()
    seed_processed_files: tuple[str, ...] = ()
    seed_failed_files: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status in {"NO_REQUESTS", "PROCESSED", "LOCKED"}

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "request_dir": self.request_dir,
            "processed_dir": self.processed_dir,
            "failed_dir": self.failed_dir,
            "processed_count": self.processed_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "seed_processed_count": self.seed_processed_count,
            "seed_failed_count": self.seed_failed_count,
            "committed_posterior_count": self.committed_posterior_count,
            "reactor_wake_published_count": self.reactor_wake_published_count,
            "seed_discovery_report": None if self.seed_discovery_report is None else self.seed_discovery_report.as_dict(),
            "processed_files": list(self.processed_files),
            "failed_files": list(self.failed_files),
            "seed_processed_files": list(self.seed_processed_files),
            "seed_failed_files": list(self.seed_failed_files),
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class _PendingMaterialization:
    input_json: Path
    command: tuple[str, ...]
    request_payload: Mapping[str, object] | None
    marker_path: Path | None
    attempt_fingerprint: str | None


@dataclass(frozen=True)
class _MaterializationQueueClaim:
    request_path: Path
    batch_path: Path | None
    processed_path: Path
    failed_path: Path
    claimed_count: int
    skipped_count: int
    inflight_deferred_count: int
    timeout_retry_deferred_count: int
    processed_files: tuple[str, ...]
    failed_files: tuple[str, ...]
    seed_processed_files: tuple[str, ...]
    seed_failed_files: tuple[str, ...]
    seed_reasons: tuple[str, ...]
    discovery_report: ReplacementForecastSeedDiscoveryReport | None


def _materialization_subprocess_timeout_seconds() -> float:
    raw = os.environ.get("ZEUS_REPLACEMENT_MATERIALIZATION_TIMEOUT_SECONDS")
    if raw is None or str(raw).strip() == "":
        return DEFAULT_MATERIALIZATION_SUBPROCESS_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "ZEUS_REPLACEMENT_MATERIALIZATION_TIMEOUT_SECONDS must be numeric"
        ) from exc
    if value <= 0:
        raise ValueError(
            "ZEUS_REPLACEMENT_MATERIALIZATION_TIMEOUT_SECONDS must be > 0"
        )
    return value


def _run_command(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=_materialization_subprocess_timeout_seconds(),
    )


def _materialization_command(input_json: Path) -> tuple[str, ...]:
    return (
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "materialize_replacement_forecast_live.py"),
        "--input-json",
        str(input_json),
        "--commit",
    )


def _timeout_result(
    command: Sequence[str],
    exc: subprocess.TimeoutExpired,
) -> subprocess.CompletedProcess[str]:
    try:
        timeout_seconds = float(exc.timeout) if exc.timeout is not None else None
    except (TypeError, ValueError):
        timeout_seconds = None
    effective_timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else DEFAULT_MATERIALIZATION_SUBPROCESS_TIMEOUT_SECONDS
    )
    return subprocess.CompletedProcess(
        args=list(command),
        returncode=124,
        stdout="",
        stderr=json.dumps(
            {
                "status": "ERROR",
                "error_type": "TimeoutExpired",
                "error": (
                    "replacement materialization subprocess exceeded "
                    f"{effective_timeout:.1f}s"
                ),
                "reason_codes": [
                    "REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_TIMEOUT"
                ],
                "timeout_seconds": timeout_seconds,
            }
        )
        + "\n",
    )


def _materialization_error_result(
    command: Sequence[str],
    exc: Exception,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=list(command),
        returncode=2,
        stdout="",
        stderr=json.dumps(
            {
                "status": "ERROR",
                "error_type": exc.__class__.__name__,
                "error": str(exc),
            },
            sort_keys=True,
        )
        + "\n",
    )


def _run_materialization_item(
    item: _PendingMaterialization,
) -> subprocess.CompletedProcess[str]:
    try:
        return _run_command(item.command)
    except subprocess.TimeoutExpired as exc:
        return _timeout_result(item.command, exc)
    except Exception as exc:
        return _materialization_error_result(item.command, exc)


def _run_materialization_batch(
    pending: Sequence[_PendingMaterialization],
) -> dict[Path, subprocess.CompletedProcess[str]]:
    if not pending:
        return {}
    completed: dict[Path, subprocess.CompletedProcess[str]] = {}
    workers = min(DEFAULT_MATERIALIZATION_MAX_WORKERS, len(pending))
    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="replacement-materialize",
    ) as executor:
        futures = {
            executor.submit(_run_materialization_item, item): item
            for item in pending
        }
        for future in as_completed(futures):
            item = futures[future]
            completed[item.input_json] = future.result()
    return completed


_LOG = logging.getLogger("zeus.replacement_live_materialization_queue")


def _surface_subprocess_warnings(input_name: str, completed: "subprocess.CompletedProcess[str]") -> None:
    """ANTI-SILENT-SINK (2026-06-09): each materialization runs as a SUBPROCESS with
    capture_output=True, so every WARNING the materializer emits (e.g. the K3 fusion
    degradation antibodies) lands ONLY in the per-request sidecar JSON — invisible to the
    daemon log, where an operator actually looks. The K3 'decorrelated-provider INCOMPLETE'
    warnings fired 19/40 recent cells and reached no log. Re-emit subprocess WARNING/ERROR
    lines at the queue level so a degradation antibody can never again warn into a void.
    Fail-soft: never raises into the queue loop."""
    try:
        for stream in (completed.stderr or "", completed.stdout or ""):
            for line in stream.splitlines():
                if "WARNING" in line or "ERROR" in line:
                    _LOG.warning("materialize[%s] %s", input_name, line.strip()[:500])
    except Exception:
        pass


def _committed_posterior_wake_status(
    completed: subprocess.CompletedProcess[str],
) -> tuple[bool, bool]:
    """Return (posterior committed, per-family wake published)."""
    if completed.returncode != 0:
        return False, False
    stdout = completed.stdout
    if isinstance(stdout, bytes):
        stdout = stdout.decode(errors="replace")
    for line in reversed((stdout or "").splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, Mapping):
            continue
        committed = bool(payload.get("committed")) and payload.get("posterior_id") is not None
        if committed:
            return True, bool(payload.get("reactor_wake_published"))
    return False, False


def _receipt_name(path: Path) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{path.stem}.{stamp}.pid{os.getpid()}{path.suffix}"


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0))
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _ensure_directory_entry_durable(
    directory: Path,
    *,
    durable_ancestor: Path,
) -> None:
    directory = directory.absolute()
    durable_ancestor = durable_ancestor.absolute()
    try:
        relative = directory.relative_to(durable_ancestor)
    except ValueError as exc:
        raise ValueError(
            f"{directory} is outside durable ancestor {durable_ancestor}"
        ) from exc
    if not durable_ancestor.is_dir():
        raise FileNotFoundError(
            f"durable directory ancestor missing: {durable_ancestor}"
        )
    parent = durable_ancestor
    for part in relative.parts:
        child = parent / part
        child.mkdir(exist_ok=True)
        if not child.is_dir():
            raise NotADirectoryError(child)
        # Repeat this even for an existing child: it repairs a prior attempt
        # whose mkdir succeeded but parent fsync failed.
        _fsync_directory(parent)
        parent = child


def _seed_terminal_receipt_index_path(seed_path: Path) -> Path | None:
    if seed_path.parent.name != "seeds":
        return None
    digest = hashlib.sha256(seed_path.name.encode("utf-8")).hexdigest()
    return seed_path.parent.parent / "seed_receipts" / digest[:2] / f"{digest}.json"


def _write_seed_terminal_receipts(
    seed_path: Path,
    moved_path: Path,
    payload: Mapping[str, object],
) -> None:
    receipt_payload = dict(payload)
    index_payload = {
        **receipt_payload,
        "seed_file": str(seed_path),
        "moved_file": str(moved_path),
    }
    moved_receipt = moved_path.with_suffix(moved_path.suffix + ".receipt.json")
    index_path = _seed_terminal_receipt_index_path(seed_path)
    if index_path is None:
        raise ValueError(f"terminal seed receipt requires a seeds path: {seed_path}")
    _ensure_directory_entry_durable(
        index_path.parent,
        durable_ancestor=seed_path.parent.parent,
    )
    for target, body in (
        (moved_receipt, receipt_payload),
        (index_path, index_payload),
    ):
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(body, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        _fsync_directory(target.parent)


def _move_request(
    path: Path,
    destination_dir: Path,
    *,
    terminal_receipt: Mapping[str, object] | None = None,
) -> Path:
    source_dir = path.parent.absolute()
    destination_dir = destination_dir.absolute()
    common_ancestor = Path(
        os.path.commonpath((source_dir, destination_dir))
    )
    _ensure_directory_entry_durable(
        destination_dir,
        durable_ancestor=common_ancestor.parent,
    )
    while True:
        target = destination_dir / _receipt_name(path)
        try:
            os.link(path, target)
        except FileExistsError:
            continue
        break
    if terminal_receipt is not None:
        try:
            _write_seed_terminal_receipts(path, target, terminal_receipt)
        except Exception:
            target.unlink(missing_ok=True)
            _fsync_directory(destination_dir)
            raise
    # The destination receipt must be durable before the queue name disappears.
    # A PUBLISH_PENDING owner may observe that disappearance and complete its
    # SQLite marker immediately; hardlink-first makes every such observation
    # imply a durable terminal receipt.
    _fsync_directory(destination_dir)
    path.unlink()
    _fsync_directory(path.parent)
    return target


def _publish_latest_seed(seed_path: Path, seed: Mapping[str, object]) -> Path:
    """Atomically retain one zero-copy, source-clock-monotone family seed."""

    city = str(seed["city"]).replace(" ", "_")
    target_date = str(seed["target_date"])
    metric = str(seed["temperature_metric"]).strip().lower()
    latest_dir = seed_path.parent.parent / "seeds_latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    latest_path = latest_dir / f"{city}.{target_date}.{metric}.json"
    candidate_cycle = _parse_utc_iso(seed.get("source_cycle_time"))
    if latest_path.is_file() and candidate_cycle is not None:
        try:
            current_seed = json.loads(latest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current_seed = None
        current_cycle = (
            _parse_utc_iso(current_seed.get("source_cycle_time"))
            if isinstance(current_seed, Mapping)
            else None
        )
        if current_cycle is not None and candidate_cycle < current_cycle:
            return latest_path
    temporary = latest_dir / f".{_receipt_name(latest_path)}.tmp"
    try:
        os.link(seed_path, temporary)
        os.replace(temporary, latest_path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return latest_path


def _write_sidecar(path: Path, payload: dict[str, object]) -> None:
    path.with_suffix(path.suffix + ".receipt.json").write_text(
        json.dumps(payload, sort_keys=True, indent=2),
        encoding="utf-8",
    )


def _read_lock_holder_pid(lock_path: Path) -> int | None:
    """Parse ``pid=<n>`` from a queue lock file; None if missing/unreadable/garbled."""
    try:
        content = lock_path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, OSError):
        return None
    marker = "pid="
    idx = content.find(marker)
    if idx < 0:
        return None
    digits = ""
    for ch in content[idx + len(marker):]:
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else None


def _pid_is_alive(pid: int) -> bool:
    """True iff a process with this PID currently exists (signal-0 liveness probe)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by another user — still a live holder.
        return True
    return True


def _archive_stale_lock(lock_path: Path, *, holder_pid: int | None) -> Path | None:
    """Move an orphaned lock into ``archived_stale_locks/`` (audit trail; never silent-delete)."""
    qdir = lock_path.parent / "archived_stale_locks"
    qdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pid_tag = holder_pid if holder_pid is not None else "unknown"
    dest = qdir / f"{lock_path.name.lstrip('.')}.{stamp}.pid{pid_tag}"
    try:
        os.replace(lock_path, dest)
        return dest
    except FileNotFoundError:
        return None  # another acquirer cleared it first — fine


@contextmanager
def _queue_lock(lock_path: Path):
    """Exclusive single-writer lock for the materialization queue, with STALE-LOCK SELF-HEAL.

    ANTIBODY (rules 5 + 3 — make the orphaned-lock stall UNCONSTRUCTABLE): the lock is released
    only by this contextmanager's ``finally`` unlink. A holder process SIGKILL'd mid-run skips
    ``finally`` entirely, so its lock file would block every future acquirer FOREVER (the ~12h
    live stall: materializer dark -> readiness expired -> reactor READINESS_EXPIRED -> zero
    trades). On ``FileExistsError`` we now probe the recorded holder PID: a DEAD (or
    unparseable) holder means the lock is orphaned, so we archive it for audit and steal the
    lock by retrying the exclusive create once; a genuinely ALIVE holder still blocks (no
    concurrent double-run). ``fd`` stays None on the blocked path, so we never unlink a live
    holder's lock.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd: int | None = None
    try:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder_pid = _read_lock_holder_pid(lock_path)
            if holder_pid is not None and _pid_is_alive(holder_pid):
                yield False
                return
            _archive_stale_lock(lock_path, holder_pid=holder_pid)
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                # Lost a race to another acquirer that grabbed the freed lock first.
                yield False
                return
        os.write(fd, f"pid={os.getpid()} acquired_at={datetime.now(timezone.utc).isoformat()}\n".encode("utf-8"))
        yield True
    finally:
        if fd is not None:
            os.close(fd)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass


def _load_seed_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("seed JSON must decode to an object")
    return payload


def _looks_like_seed(payload: dict[str, object]) -> bool:
    # The live seed discriminator is the baseline + OM9 anchor + precision + bins shape.
    # Retired model keys are not part of the seed signature.
    required = {
        "city",
        "target_date",
        "temperature_metric",
        "computed_at",
        "baseline_source_run_id",
        "openmeteo_source_run_id",
        "openmeteo_payload_json",
        "precision_metadata_json",
        "bins",
    }
    return required.issubset(payload)


def _day0_seed_matches_conditioning(
    seed: Mapping[str, object],
    conditioning: Mapping[str, object],
) -> bool:
    """Return whether a posterior consumed the seed's exact Day0 evidence."""
    seed_metric = str(seed.get("temperature_metric") or "").strip().lower()
    posterior_metric = str(conditioning.get("metric") or "").strip().lower()
    if seed_metric not in {"high", "low"} or posterior_metric != seed_metric:
        return False
    from src.data.replacement_cycle_advance_trigger import (  # noqa: PLC0415
        _day0_conditioning_identity,
    )

    seed_identity = _day0_conditioning_identity(
        source=seed.get("day0_observed_extreme_source"),
        observation_time=seed.get("day0_observed_extreme_observation_time"),
        observed_extreme_c=seed.get("day0_observed_extreme_c"),
        unit=seed.get("day0_observed_extreme_unit"),
    )
    conditioning_identity = _day0_conditioning_identity(
        source=conditioning.get("source"),
        observation_time=conditioning.get("observation_time"),
        observed_extreme_c=conditioning.get("observed_extreme_c"),
        unit=conditioning.get("unit"),
    )
    return (
        seed_identity is not None
        and conditioning_identity is not None
        and seed_identity == conditioning_identity
    )


def _upgrade_day0_seed_has_current_enqueue_ownership(
    *,
    forecast_db: Path | str | None,
    seed_file: Path,
    seed: Mapping[str, object],
) -> _Day0EnqueueOwnershipCheck:
    """Classify Day0 marker ownership without consuming a seed on read uncertainty."""
    # Day0 conditioning is probability truth, not an enqueue-owner type.  Only
    # cycle-advance publishers opt into the cycle_advance_enqueues fence.  A
    # fusion/input-revision seed carries the same canonical Day0 observation
    # but is owned by fusion_upgrade_enqueues; forcing it through the cycle
    # fence consumes a valid seed as STALE before it can repair the posterior.
    if (
        seed.get("cycle_advance_enqueue_owner") is not True
        or seed.get("day0_observed_extreme_observation_time") is None
    ):
        return _Day0EnqueueOwnershipCheck(_Day0EnqueueOwnership.CURRENT)
    from src.data.replacement_cycle_advance_trigger import (  # noqa: PLC0415
        _day0_conditioning_identity,
    )

    identity = _day0_conditioning_identity(
        source=seed.get("day0_observed_extreme_source"),
        observation_time=seed.get("day0_observed_extreme_observation_time"),
        observed_extreme_c=seed.get("day0_observed_extreme_c"),
        unit=seed.get("day0_observed_extreme_unit"),
    )
    if identity is None or forecast_db is None:
        return _Day0EnqueueOwnershipCheck(_Day0EnqueueOwnership.INDETERMINATE)
    db_path = Path(forecast_db)
    if not db_path.exists():
        return _Day0EnqueueOwnershipCheck(_Day0EnqueueOwnership.INDETERMINATE)
    from src.state.db import _connect_read_only  # noqa: PLC0415

    try:
        conn = _connect_read_only(db_path)
        try:
            conn.execute("PRAGMA query_only=ON")
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(cycle_advance_enqueues)").fetchall()
            }
            required = {
                "enqueue_id",
                "city",
                "target_date",
                "metric",
                "target_cycle_time",
                "seed_file",
                "day0_conditioning_identity_json",
            }
            if not required.issubset(columns):
                return _Day0EnqueueOwnershipCheck(_Day0EnqueueOwnership.INDETERMINATE)
            row = conn.execute(
                """
                SELECT seed_file, day0_conditioning_identity_json,
                       target_cycle_time
                FROM cycle_advance_enqueues
                WHERE city = ?
                  AND target_date = ?
                  AND metric = ?
                ORDER BY enqueue_id DESC
                LIMIT 1
                """,
                (
                    str(seed.get("city") or ""),
                    str(seed.get("target_date") or ""),
                    str(seed.get("temperature_metric") or ""),
                ),
            ).fetchone()
            if row is None:
                return _Day0EnqueueOwnershipCheck(_Day0EnqueueOwnership.INDETERMINATE)
            if str(row["seed_file"] or "") != str(seed_file):
                return _Day0EnqueueOwnershipCheck(_Day0EnqueueOwnership.STALE)
            recorded_identity = row["day0_conditioning_identity_json"]
            if not isinstance(recorded_identity, str) or not recorded_identity.strip():
                return _Day0EnqueueOwnershipCheck(_Day0EnqueueOwnership.INDETERMINATE)
            if recorded_identity != identity:
                return _Day0EnqueueOwnershipCheck(_Day0EnqueueOwnership.STALE)
            return _Day0EnqueueOwnershipCheck(
                _Day0EnqueueOwnership.CURRENT,
                witness={
                    "city": str(seed.get("city") or ""),
                    "target_date": str(seed.get("target_date") or ""),
                    "metric": str(seed.get("temperature_metric") or ""),
                    "target_cycle_time": str(row["target_cycle_time"] or ""),
                    "seed_file": str(seed_file),
                    "conditioning_identity": identity,
                },
            )
        finally:
            conn.close()
    except Exception:
        return _Day0EnqueueOwnershipCheck(_Day0EnqueueOwnership.INDETERMINATE)


def _seed_already_covered(*, forecast_db: Path | str | None, seed: dict[str, object]) -> bool:
    if forecast_db is None:
        return False
    from src.state.db import _connect_read_only

    db_path = Path(forecast_db)
    if not db_path.exists():
        return False
    conn = _connect_read_only(db_path)
    try:
        conn.execute("PRAGMA query_only=ON")
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
        }
        if not {"forecast_posteriors", "readiness_state"}.issubset(tables):
            return False
        city = str(seed["city"])
        target_date = str(seed["target_date"])
        metric = str(seed["temperature_metric"])
        baseline_source_run_id = str(seed["baseline_source_run_id"])
        openmeteo_source_run_id = str(seed["openmeteo_source_run_id"])
        posterior_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(forecast_posteriors)").fetchall()
        }
        # TRADEABLE-GRADE COVERAGE (operator directive 2026-06-10; basis-predicate fix 2026-06-12).
        # A covering posterior must be certified-bootstrap tradeable-grade. A non-live or
        # degraded posterior must not count as "done forever" and block its own repair.
        # Single authority: cycle_policy.tradeable_grade_coverage_sql.
        decision_time = _parse_utc_iso(seed.get("computed_at")) or datetime.now(timezone.utc)
        tradeable_grade_clause = tradeable_grade_coverage_sql(
            posterior_columns=posterior_columns,
            decision_time=decision_time,
        )
        runtime_layer_clause = "AND runtime_layer = 'live'" if "runtime_layer" in posterior_columns else ""
        posterior = conn.execute(
            f"""
            SELECT posterior_id, source_cycle_time, computed_at, provenance_json
            FROM forecast_posteriors
            WHERE source_id = ?
              {runtime_layer_clause}
              AND city = ?
              AND target_date = ?
              AND temperature_metric = ?
              {tradeable_grade_clause}
              AND json_extract(dependency_source_run_ids_json, '$.baseline_b0') = ?
              AND json_extract(dependency_source_run_ids_json, '$.openmeteo_ifs9_anchor') = ?
            ORDER BY computed_at DESC, posterior_id DESC
            LIMIT 1
            """,
            (SOURCE_ID, city, target_date, metric, baseline_source_run_id, openmeteo_source_run_id),
        ).fetchone()
        if posterior is None:
            return False
        if replacement_live_input_lag_reason(
            conn,
            city=city,
            target_date=target_date,
            metric=metric,
            decision_time=decision_time,
            posterior_source_cycle_time=posterior["source_cycle_time"],
            posterior_computed_at=posterior["computed_at"],
        ) is not None:
            return False
        if seed.get("day0_observed_extreme_observation_time") is not None:
            try:
                posterior_provenance = json.loads(
                    str(posterior["provenance_json"] or "{}")
                )
            except (TypeError, ValueError):
                posterior_provenance = {}
            conditioning = None
            if isinstance(posterior_provenance, dict):
                from src.data.replacement_cycle_advance_trigger import (  # noqa: PLC0415
                    _active_day0_provisional_or_conditioning,
                )

                conditioning = _active_day0_provisional_or_conditioning(
                    posterior_provenance
                )
            if not isinstance(conditioning, Mapping) or not _day0_seed_matches_conditioning(
                seed,
                conditioning,
            ):
                return False
        readiness_columns = {
            str(row["name"] if isinstance(row, dict) else row[1])
            for row in conn.execute("PRAGMA table_info(readiness_state)").fetchall()
        }
        readiness_status_clause = ""
        if "status" in readiness_columns:
            readiness_status_clause = "AND status = 'READY'"
        # Only a readiness row whose expires_at is still in the future counts as
        # live coverage. An expired row must NOT mark the seed already-covered,
        # otherwise the queue skips it forever and fresh readiness can never be
        # produced (the stale row both blocks the request and never refreshes).
        readiness_freshness_clause = ""
        if "expires_at" in readiness_columns:
            readiness_freshness_clause = (
                "AND (expires_at IS NULL OR expires_at > strftime('%Y-%m-%dT%H:%M:%S', 'now'))"
            )
        readiness = conn.execute(
            f"""
            SELECT dependency_json
            FROM readiness_state
            WHERE strategy_key = ?
              {readiness_status_clause}
              {readiness_freshness_clause}
              AND json_extract(provenance_json, '$.city') = ?
              AND json_extract(provenance_json, '$.target_date') = ?
              AND json_extract(provenance_json, '$.temperature_metric') = ?
              AND EXISTS (
                  SELECT 1
                  FROM json_each(readiness_state.dependency_json, '$.dependencies')
                  WHERE json_extract(value, '$.role') = 'baseline_b0'
                    AND json_extract(value, '$.source_run_id') = ?
              )
              AND EXISTS (
                  SELECT 1
                  FROM json_each(readiness_state.dependency_json, '$.dependencies')
                  WHERE json_extract(value, '$.role') = 'openmeteo_ifs9_anchor'
                    AND json_extract(value, '$.source_run_id') = ?
              )
            LIMIT 1
            """,
            (STRATEGY_KEY, city, target_date, metric, baseline_source_run_id, openmeteo_source_run_id),
        ).fetchone()
        if readiness is None:
            return False
        soft_binding_supported = conn.execute(
            """
            SELECT 1
              FROM readiness_state r,
                   json_each(r.dependency_json, '$.dependencies')
             WHERE json_extract(value, '$.role') = 'soft_anchor_posterior'
             LIMIT 1
            """
        ).fetchone()
        if soft_binding_supported is not None:
            try:
                readiness_payload = json.loads(str(readiness["dependency_json"] or "{}"))
            except (TypeError, ValueError):
                return False
            dependencies = (
                readiness_payload.get("dependencies")
                if isinstance(readiness_payload, dict)
                else None
            )
            matches = [
                item
                for item in (dependencies or [])
                if isinstance(item, dict)
                and item.get("role") == "soft_anchor_posterior"
            ]
            if len(matches) != 1:
                return False
            try:
                bound_posterior_id = int(matches[0].get("posterior_id"))
            except (TypeError, ValueError):
                return False
            if bound_posterior_id != int(posterior["posterior_id"]):
                return False
        return True
    finally:
        conn.close()


def _parse_utc_iso(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _seed_source_cycle_regression(
    *,
    forecast_db: Path | str | None,
    seed: dict[str, object],
) -> tuple[str, str] | None:
    """Why this seed is already below current family cycle truth, if known.

    The materializer's monotone consumed-cycle guard remains the final authority.
    This queue-side check prevents a seed that is already known to be
    unconstructable or immediately HWM-ineligible from spending the single
    subprocess slot every cycle.
    """

    if forecast_db is None:
        return None
    request_cycle = _parse_utc_iso(seed.get("source_cycle_time"))
    if request_cycle is None:
        return None
    db_path = Path(forecast_db)
    if not db_path.exists():
        return None
    from src.state.db import _connect_read_only

    try:
        conn = _connect_read_only(db_path)
        try:
            conn.execute("PRAGMA query_only=ON")
            row = conn.execute(
                """
                SELECT source_cycle_time
                FROM forecast_posteriors
                WHERE source_id = ?
                  AND city = ?
                  AND target_date = ?
                  AND temperature_metric = ?
                ORDER BY computed_at DESC
                LIMIT 1
                """,
                (
                    SOURCE_ID,
                    str(seed.get("city")),
                    str(seed.get("target_date")),
                    str(seed.get("temperature_metric")),
                ),
            ).fetchone()
            from src.data.replacement_input_hwm import (  # noqa: PLC0415
                latest_eligible_ensemble_input_cycle,
            )

            latest_ensemble_cycle = latest_eligible_ensemble_input_cycle(
                conn,
                city=str(seed.get("city")),
                target_date=str(seed.get("target_date")),
                metric=str(seed.get("temperature_metric")),
                decision_time=datetime.now(timezone.utc),
            )
        finally:
            conn.close()
    except Exception:
        return None
    if row is not None:
        current_raw = row["source_cycle_time"] if hasattr(row, "keys") else row[0]
        current_cycle = _parse_utc_iso(current_raw)
        if current_cycle is not None and request_cycle < current_cycle:
            return "current_posterior", current_cycle.isoformat()
    if latest_ensemble_cycle is not None and request_cycle < latest_ensemble_cycle:
        return "current_ensemble_hwm", latest_ensemble_cycle.isoformat()
    return None


def _write_request(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")


def _cycle_advance_never_priced_scopes(
    conn: object,
    fam_scopes: frozenset[tuple[str, str, str]],
) -> frozenset[tuple[str, str, str]]:
    """Return the subset of (city, target_date, metric) scopes with zero prior posterior.

    Best-effort: any failure (missing ``forecast_posteriors`` table on an older
    fixture/test db, locked db, etc.) yields an empty result, which falls back
    to the legacy held/non-held two-tier priority below rather than raising.
    """
    if not fam_scopes:
        return frozenset()
    try:
        priced_scopes: set[tuple[str, str, str]] = set()
        fam_list = tuple(fam_scopes)
        for offset in range(0, len(fam_list), 200):
            chunk = fam_list[offset : offset + 200]
            values = ", ".join("(?, ?, ?)" for _ in chunk)
            priced_rows = conn.execute(
                f"""
                WITH fam(city, target_date, metric) AS (
                    VALUES {values}
                )
                SELECT DISTINCT f.city, f.target_date, f.metric
                FROM fam AS f
                JOIN forecast_posteriors AS p
                  ON p.source_id = ?
                 AND p.city = f.city
                 AND p.target_date = f.target_date
                 AND p.temperature_metric = f.metric
                """,
                tuple(value for scope in chunk for value in scope) + (SOURCE_ID,),
            ).fetchall()
            priced_scopes.update(
                (str(row[0] or ""), str(row[1] or ""), str(row[2] or ""))
                for row in priced_rows
            )
    except Exception:  # noqa: BLE001 - never-priced tier is best-effort; falls back to 0/1 priority
        return frozenset()
    return frozenset(fam_scopes - priced_scopes)


def _current_money_risk_scopes(
    fam_scopes: frozenset[tuple[str, str, str]],
    *,
    trade_db: Path | str | None = None,
) -> frozenset[tuple[str, str, str]]:
    """Return queued families with chain-confirmed capital currently at risk."""

    if not fam_scopes:
        return frozenset()
    try:
        from src.data.replacement_cycle_advance_trigger import (  # noqa: PLC0415
            _held_position_families,
        )
        from src.state.db import _connect_read_only, _zeus_trade_db_path  # noqa: PLC0415

        db_path = Path(trade_db) if trade_db is not None else _zeus_trade_db_path()
        if not db_path.exists():
            return frozenset()
        conn = _connect_read_only(db_path)
        try:
            return frozenset(_held_position_families(conn) & fam_scopes)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - priority loss is loud, queue still drains
        _LOG.error(
            "replacement materialization current-exposure priority read failed; "
            "falling back to persisted enqueue priority: %s",
            exc,
        )
        return frozenset()


def _cycle_advance_seed_priority_map(
    forecast_db: Path | str | None,
    queue_files: Sequence[Path],
    payloads: Mapping[Path, Mapping[str, object] | None] | None = None,
    *,
    trade_db: Path | str | None = None,
) -> dict[str, tuple[int, str]]:
    """Return filename -> priority for queued materialization work.

    Current chain-confirmed exposure is read at claim time and dominates every
    discovery tier.  ``cycle_advance_enqueues.held_position`` is only a producer-time
    fallback: a position may fill after that immutable enqueue row was written, so
    using the marker as current truth can leave the exit organ behind minutes of
    first-price discovery.  Never-priced families lead only when no current capital is
    at risk in the family.
    """
    if not queue_files:
        return {}
    names_by_scope: dict[tuple[str, str, str, str], set[str]] = {}
    request_time_by_name: dict[str, str] = {}
    cycle_by_scope: dict[tuple[str, str, str, str], datetime] = {}
    latest_cycle_by_family: dict[tuple[str, str, str], datetime] = {}
    for path in queue_files:
        payload = (
            payloads[path]
            if payloads is not None and path in payloads
            else _load_request_payload_for_coalescing(path)
        )
        if payload is None:
            continue
        computed_at = _parse_utc_iso(payload.get("computed_at"))
        if computed_at is not None:
            request_time_by_name[path.name] = computed_at.isoformat()
        cycle = _parse_utc_iso(payload.get("source_cycle_time"))
        scope = (
            str(payload.get("city") or "").strip(),
            str(payload.get("target_date") or "").strip(),
            str(payload.get("temperature_metric") or "").strip(),
            "" if cycle is None else cycle.isoformat(),
        )
        if all(scope):
            names_by_scope.setdefault(scope, set()).add(path.name)
            if cycle is not None:
                cycle_by_scope[scope] = cycle
                family = scope[:3]
                latest = latest_cycle_by_family.get(family)
                if latest is None or cycle > latest:
                    latest_cycle_by_family[family] = cycle
    if not names_by_scope:
        return {}
    fam_scopes = frozenset(scope[:3] for scope in names_by_scope)
    current_money_risk = _current_money_risk_scopes(
        fam_scopes,
        trade_db=trade_db,
    )
    rows: list[object] = []
    never_priced_scopes: frozenset[tuple[str, str, str]] = frozenset()
    if forecast_db is not None and Path(forecast_db).exists():
        try:
            from src.state.db import _connect_read_only  # noqa: PLC0415

            conn = _connect_read_only(Path(forecast_db))
            try:
                scopes = tuple(names_by_scope)
                for offset in range(0, len(scopes), 200):
                    chunk = scopes[offset : offset + 200]
                    values = ", ".join("(?, ?, ?, ?)" for _ in chunk)
                    rows.extend(
                        conn.execute(
                            f"""
                            WITH queued(city, target_date, metric, target_cycle_time) AS (
                                VALUES {values}
                            )
                            SELECT e.city,
                                   e.target_date,
                                   e.metric,
                                   e.target_cycle_time,
                                   e.held_position,
                                   e.enqueued_at
                            FROM queued AS q
                            JOIN cycle_advance_enqueues AS e
                              ON e.city = q.city
                             AND e.target_date = q.target_date
                             AND e.metric = q.metric
                             AND e.target_cycle_time = q.target_cycle_time
                            """,
                            tuple(value for scope in chunk for value in scope),
                        ).fetchall()
                    )
                never_priced_scopes = _cycle_advance_never_priced_scopes(
                    conn, fam_scopes
                )
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - priority is best-effort; queue must still drain
            pass

    enqueue_priority: dict[tuple[str, str, str, str], tuple[bool, str]] = {}
    for row in rows:
        scope = tuple(str(value or "") for value in row[:4])
        held_position, enqueued_at = row[4:]
        candidate = (bool(int(held_position or 0)), str(enqueued_at or ""))
        current = enqueue_priority.get(scope)
        if current is None or (candidate[0] and not current[0]) or (
            candidate[0] == current[0] and candidate[1] < current[1]
        ):
            enqueue_priority[scope] = candidate

    priority: dict[str, tuple[int, str]] = {}
    for scope, names in names_by_scope.items():
        fam_scope = scope[:3]
        held_marker, enqueued_at = enqueue_priority.get(scope, (False, ""))
        if fam_scope in current_money_risk:
            base_tier = -2
        elif fam_scope in never_priced_scopes:
            base_tier = -1
        elif held_marker:
            base_tier = 0
        else:
            base_tier = 1
        scope_cycle = cycle_by_scope.get(scope)
        latest_cycle = latest_cycle_by_family.get(fam_scope)
        older_queued_cycle = (
            scope_cycle is not None
            and latest_cycle is not None
            and scope_cycle < latest_cycle
        )
        tier = base_tier * 2 + int(older_queued_cycle)
        for name in names:
            priority[name] = (tier, request_time_by_name.get(name) or enqueued_at)
    return priority


def _cycle_advance_file_sort_key(
    path: Path,
    priority: dict[str, tuple[int, str]],
) -> tuple[int, str, str]:
    return (*priority.get(path.name, (1, "")), path.name)


# POISON-PILL IMMUNITY (2026-06-10): the materializer subprocess accesses these keys
# unconditionally and immediately (scripts/materialize_replacement_forecast_live.py:163-165,
# then the OpenMeteo/precision inputs). A request file missing any of them — e.g. a
# new_listing_scout intent stub {condition_id, enqueued_at, reason, source} — crashes the
# subprocess with KeyError on every cycle and, because it is never removed from requests/,
# permanently consumes a queue slot. 772 such stubs starved ALL legitimate posterior
# production on 2026-06-10. The category antibody: validate the request schema BEFORE
# spawning, and route an invalid file to failed/ so each bad file consumes queue budget AT
# MOST ONCE. A malformed producer must never be able to starve the queue.
# Authority basis: materializer queue starvation incident 2026-06-10, /tmp/materializer_collapse_report.md
_REQUEST_REQUIRED_KEYS: tuple[str, ...] = (
    "temperature_metric",
    "target_date",
    "source_cycle_time",
)
_REQUEST_DEDUP_KEY_FIELDS: tuple[str, ...] = (
    "city",
    "target_date",
    "temperature_metric",
    "source_cycle_time",
    "baseline_source_run_id",
    "openmeteo_source_run_id",
)
_DAY0_CONDITIONING_IDENTITY_KEY = "day0_conditioning_identity"
_UNCHANGED_BLOCKED_REASON = "REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET"
_UNCHANGED_BLOCKED_SKIP_REASON = (
    "REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_UNCHANGED_BLOCKED_INPUT"
)
_BLOCKED_INPUT_RECEIPT_REASON = (
    "REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_BLOCKED_INPUT"
)
_UNCHANGED_BLOCKED_SEED_SKIP_REASON = (
    "REPLACEMENT_LIVE_MATERIALIZATION_SEED_UNCHANGED_BLOCKED_INPUT"
)
_UNCHANGED_SUCCESS_SKIP_REASON = (
    "REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_RECENT_UNCHANGED_SUCCESS"
)
_STALE_DAY0_ENQUEUE_OWNER_REASON = "STALE_DAY0_ENQUEUE_OWNER"
_STALE_DAY0_OWNER_SUPERSEDED_REASON = (
    "REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_SUPERSEDED_BY_DAY0_OWNER"
)
_WRITE_DEFERRED_REASON = "REPLACEMENT_FORECAST_WRITE_DEFERRED"
_ATTEMPT_CLOCK_FIELDS = frozenset({"computed_at", "expires_at"})
_ATTEMPT_INPUT_PATH_FIELDS = (
    "openmeteo_payload_json",
    "precision_metadata_json",
    "aifs_samples_json",
)


def _source_clock_missing_configured_sources(
    conn,
    payload: Mapping[str, object],
) -> tuple[str, ...] | None:
    """Return the exact source-clock dependencies that still block this request."""

    city = str(payload.get("city") or "").strip()
    target_date = str(payload.get("target_date") or "").strip()
    metric = str(payload.get("temperature_metric") or "").strip()
    cycle = _parse_utc_iso(payload.get("source_cycle_time"))
    if not city or not target_date or not metric or cycle is None:
        return None
    try:
        from src.data.replacement_current_value_serving import (  # noqa: PLC0415
            read_current_instrument_values,
        )
        from src.strategy.live_inference.source_clock_city_weights import (  # noqa: PLC0415
            scheme_for_city,
        )
        from src.strategy.live_inference.source_clock_vnext import (  # noqa: PLC0415
            provider_family_for_source,
        )

        scheme = scheme_for_city(city, metric=metric)
        if scheme is None:
            return ()
        served = read_current_instrument_values(
            conn,
            city=city,
            metric=metric,
            target_date=target_date,
            source_cycle_time_iso=cycle.isoformat(),
            include_station_sources=True,
        )
    except Exception:  # noqa: BLE001 - uncertainty must retain the existing retry behavior
        return None
    if any(
        model.startswith(("cwa_", "hko_")) and model not in scheme.weights
        for model in served
    ):
        return ()
    missing = tuple(source for source in scheme.final_sources if source not in served)
    configured_families = {
        provider_family_for_source(source)
        for source in scheme.final_sources
        if source in served
    }
    current_families = {
        provider_family_for_source(source)
        for source in served
    }
    # SCOPE: this request's exact city/date/metric/carrier-cycle raw watermark.
    # DRAIN: arrival of a second independent current provider makes the full raw
    # watermark relevant, allowing one materializer retry through the same queue.
    # RESET: once the retry marker records that watermark, unchanged raw facts are
    # suppressed again. Same-family alias churn never opens the retry path.
    if len(configured_families) < 2 and len(current_families) >= 2:
        return ()
    return missing


def _validate_request_payload(path: Path) -> tuple[bool, str, str]:
    """Return (ok, reason_code, detail) for a queued request file WITHOUT spawning a subprocess.

    A valid materialization request always carries the minimal keys the materializer accesses
    before any work (temperature_metric, target_date, source_cycle_time). Anything else (a scout intent stub,
    unparseable JSON, a non-object) is poison: fail it fast so it leaves the queue at most once.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, "REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_UNREADABLE", repr(exc)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return False, "REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_MALFORMED_JSON", str(exc)
    if not isinstance(payload, dict):
        return False, "REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_NOT_OBJECT", f"top-level {type(payload).__name__}"
    # BOUNDARY CONTRACT (2026-06-10): the consumer half of the producer⇄consumer
    # contract. This replaces the ad-hoc required-key checks with the
    # single shared schema in src.contracts.replacement_pipeline_files. The exact
    # scout-stub shape is rejected here with a ContractViolation whose detail names
    # every missing field — written verbatim into the failed/ receipt below — and
    # the file leaves the queue at most once. Authority basis: pipeline-contract
    # project, operator directive 2026-06-10.
    try:
        validate_materialization_request(payload)
    except ContractViolation as exc:
        # Preserve the pre-existing reason-code vocabulary the receipt consumers /
        # tests rely on, while sourcing the precise detail from the shared contract.
        if exc.detail.startswith("missing_or_empty_required_keys="):
            reason_code = "REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_MISSING_REQUIRED_KEYS"
        elif "OpenMeteo input selector" in exc.detail:
            reason_code = "REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_MISSING_LIVE_INPUT"
        else:
            reason_code = "REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_CONTRACT_VIOLATION"
        return (
            False,
            reason_code,
            exc.detail,
        )
    return True, "", ""


def _load_request_payload_for_coalescing(path: Path) -> Mapping[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _request_semantic_key(payload: Mapping[str, object]) -> tuple[str, ...] | None:
    values: list[str] = []
    for field in _REQUEST_DEDUP_KEY_FIELDS:
        value = str(payload.get(field) or "").strip()
        if not value:
            return None
        if field == "source_cycle_time":
            parsed = _parse_utc_iso(value)
            if parsed is None:
                return None
            value = parsed.isoformat()
        values.append(value)
    day0_fields = (
        "day0_observed_extreme_source",
        "day0_observed_extreme_observation_time",
        "day0_observed_extreme_c",
        "day0_observed_extreme_unit",
    )
    if any(payload.get(field) is not None for field in day0_fields):
        source = str(payload.get("day0_observed_extreme_source") or "").strip()
        observation_time = _parse_utc_iso(
            payload.get("day0_observed_extreme_observation_time")
        )
        unit = str(payload.get("day0_observed_extreme_unit") or "").strip().upper()
        try:
            observed_extreme_c = round(float(payload.get("day0_observed_extreme_c")), 9)
        except (TypeError, ValueError):
            return None
        if not source or observation_time is None or not unit:
            return None
        values.append(
            _DAY0_CONDITIONING_IDENTITY_KEY
            + "="
            + json.dumps(
                {
                    "observation_time": observation_time.isoformat(),
                    "observed_extreme_c": observed_extreme_c,
                    "source": source,
                    "unit": unit,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return tuple(values)


def _request_freshness_key(path: Path, payload: Mapping[str, object]) -> tuple[datetime, int, str]:
    computed_at = _parse_utc_iso(payload.get("computed_at"))
    if computed_at is None:
        computed_at = datetime.min.replace(tzinfo=timezone.utc)
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    return computed_at, mtime_ns, path.name


def _blocked_attempt_fingerprint(
    *,
    input_json: Path,
    forecast_db: Path | str | None,
    payload: Mapping[str, object],
) -> str | None:
    """Hash the request and current raw facts that can heal a blocked attempt."""

    scope = tuple(
        str(payload.get(field) or "").strip()
        for field in ("city", "target_date", "temperature_metric")
    )
    if forecast_db is None:
        return None
    db_path = Path(forecast_db)
    if not all(scope) or not db_path.exists():
        return None
    # A source-clock materialization keeps the ENS carrier cycle fixed while each
    # deterministic provider may advance independently.  Fingerprint the exact
    # production selector frontier at this request's immutable decision instant;
    # carrier-cycle aggregation misses those healing inputs, while target-wide MAX
    # watermarks churn on rows the request could not yet have consumed.
    computed_at = _parse_utc_iso(payload.get("computed_at"))
    if computed_at is None:
        return None
    try:
        from src.state.db import _connect_read_only  # noqa: PLC0415

        conn = _connect_read_only(db_path)
        try:
            conn.execute("PRAGMA query_only=ON")
            missing_sources = _source_clock_missing_configured_sources(conn, payload)
            from src.strategy.live_inference.source_clock_city_weights import (  # noqa: PLC0415
                scheme_for_city,
            )

            scheme = scheme_for_city(scope[0], metric=scope[2])
            configured_models = None if scheme is None else tuple(scheme.final_sources)
            source_clock_frontier = read_current_instrument_frontier_identity(
                conn,
                city=scope[0],
                metric=scope[2],
                target_date=scope[1],
                decision_time_iso=computed_at.isoformat(),
                models=configured_models,
                schema=current_value_serving_schema(conn),
            )
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - unknown watermark must retry, never suppress work
        return None
    file_revisions: dict[str, tuple[int, int] | None] = {}
    if not missing_sources:
        for field in _ATTEMPT_INPUT_PATH_FIELDS:
            raw_path = payload.get(field)
            if raw_path in (None, ""):
                continue
            path = Path(str(raw_path))
            if not path.is_absolute():
                path = input_json.parent / path
            try:
                stat = path.stat()
                file_revisions[field] = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                file_revisions[field] = None
    logic_revisions: dict[str, tuple[int, int] | None] = {}
    for path in (
        PROJECT_ROOT / "src/data/replacement_forecast_materializer.py",
        PROJECT_ROOT / "src/data/replacement_current_value_serving.py",
        PROJECT_ROOT / "src/data/forecast_source_registry.py",
        PROJECT_ROOT / "config/settings.json",
    ):
        try:
            stat = path.stat()
            logic_revisions[path.name] = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            logic_revisions[path.name] = None
    canonical = json.dumps(
        {
            "request": {
                key: value
                for key, value in payload.items()
                if key not in _ATTEMPT_CLOCK_FIELDS
            },
            "files": file_revisions,
            "raw": {
                "missing_configured_sources": missing_sources,
                "source_clock_frontier": source_clock_frontier,
            },
            "logic": logic_revisions,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _blocked_attempt_marker_path(
    marker_dir: Path,
    payload: Mapping[str, object],
) -> Path | None:
    scope = tuple(
        str(payload.get(field) or "").strip()
        for field in ("city", "target_date", "temperature_metric")
    )
    if not all(scope):
        return None
    digest = hashlib.sha256("\0".join(scope).encode("utf-8")).hexdigest()
    return marker_dir / f"{digest}.json"


def _blocked_attempt_state(
    *,
    marker_dir: Path,
    input_json: Path,
    payload: Mapping[str, object],
    forecast_db: Path | str | None,
) -> tuple[Path | None, str | None, bool]:
    marker_path = _blocked_attempt_marker_path(marker_dir, payload)
    fingerprint = _blocked_attempt_fingerprint(
        input_json=input_json,
        payload=payload,
        forecast_db=forecast_db,
    )
    if marker_path is None or fingerprint is None:
        return marker_path, fingerprint, False
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return marker_path, fingerprint, False
    if not isinstance(marker, Mapping):
        return marker_path, fingerprint, False
    return marker_path, fingerprint, marker.get("attempt_fingerprint") == fingerprint


def _recent_success_coalesce_seconds() -> float:
    raw = os.environ.get("ZEUS_REPLACEMENT_RECENT_SUCCESS_COALESCE_SECONDS")
    if raw is None or not str(raw).strip():
        return DEFAULT_RECENT_SUCCESS_COALESCE_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "ZEUS_REPLACEMENT_RECENT_SUCCESS_COALESCE_SECONDS must be numeric"
        ) from exc
    if value < 0:
        raise ValueError(
            "ZEUS_REPLACEMENT_RECENT_SUCCESS_COALESCE_SECONDS must be >= 0"
        )
    return value


def _terminal_receipt_path(
    receipt_dir: Path,
    request_payload: Mapping[str, object],
) -> Path:
    city = str(request_payload.get("city") or "unknown").replace(" ", "_")
    target_date = str(request_payload.get("target_date") or "unknown")
    metric = str(request_payload.get("temperature_metric") or "unknown").lower()
    return receipt_dir / f"{city}.{target_date}.{metric}.json"


def _recent_unchanged_success(
    *,
    processed_path: Path,
    request_payload: Mapping[str, object],
    attempt_fingerprint: str | None,
    now: datetime | None = None,
) -> bool:
    """Whether an exact-input success is still inside the fixed coalescing window."""

    window_seconds = _recent_success_coalesce_seconds()
    if attempt_fingerprint is None or window_seconds <= 0:
        return False
    receipt_path = _terminal_receipt_path(
        processed_path.parent / "succeeded_latest",
        request_payload,
    )
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(receipt, Mapping) or receipt.get("status") != "SUCCEEDED":
        return False
    evidence = receipt.get("result_evidence")
    if not isinstance(evidence, Mapping):
        return False
    if evidence.get("committed_posterior") is not True:
        return False
    if evidence.get("attempt_fingerprint") != attempt_fingerprint:
        return False
    succeeded_at = _parse_utc_iso(receipt.get("recorded_at"))
    if succeeded_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    age_seconds = (current - succeeded_at).total_seconds()
    return 0 <= age_seconds < window_seconds


def _write_blocked_attempt_marker(
    *,
    marker_path: Path | None,
    payload: Mapping[str, object],
    fingerprint: str | None,
) -> None:
    if marker_path is None or fingerprint is None:
        return
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = marker_path.with_suffix(f".tmp.{os.getpid()}")
    temp_path.write_text(
        json.dumps(
            {
                "status": "BLOCKED",
                "reason_codes": [_UNCHANGED_BLOCKED_REASON],
                "attempt_fingerprint": fingerprint,
                "city": payload.get("city"),
                "target_date": payload.get("target_date"),
                "temperature_metric": payload.get("temperature_metric"),
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            },
            sort_keys=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    os.replace(temp_path, marker_path)


def _subprocess_result_reason_codes(completed: subprocess.CompletedProcess[str]) -> tuple[str, ...]:
    for stream in (completed.stdout or "", completed.stderr or ""):
        for line in reversed(stream.splitlines()):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, Mapping):
                continue
            reasons = payload.get("reason_codes")
            if not isinstance(reasons, list):
                continue
            return tuple(str(reason) for reason in reasons)
    return ()


def _record_latest_terminal_request(
    input_json: Path,
    *,
    processed_path: Path,
    request_payload: Mapping[str, object],
    receipt_dir_name: str,
    status: str,
    reason_codes: Sequence[str],
    result_evidence: Mapping[str, object] | None = None,
) -> Path:
    """Replace valueless terminal work with one compact receipt per family.

    Canonical source/owner/posterior rows carry decision truth. Retaining every
    full queue request after it becomes blocked or causally obsolete adds no
    replay value. This receipt keeps the latest disposition while bounding disk
    use by forecast-family cardinality.
    """

    receipt_dir = processed_path.parent / receipt_dir_name
    _ensure_directory_entry_durable(
        receipt_dir,
        durable_ancestor=processed_path.parent,
    )
    target = _terminal_receipt_path(receipt_dir, request_payload)
    temporary = receipt_dir / f".{target.name}.{os.getpid()}.tmp"
    witness = request_payload.get("day0_enqueue_owner_witness")
    receipt = {
        "status": status,
        "reason_codes": list(reason_codes),
        "city": request_payload.get("city"),
        "target_date": request_payload.get("target_date"),
        "temperature_metric": request_payload.get("temperature_metric"),
        "source_cycle_time": request_payload.get("source_cycle_time"),
        "computed_at": request_payload.get("computed_at"),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "conditioning_identity": (
            witness.get("conditioning_identity")
            if isinstance(witness, Mapping)
            else None
        ),
    }
    if result_evidence:
        receipt["result_evidence"] = dict(result_evidence)
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(receipt, handle, sort_keys=True, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    _fsync_directory(receipt_dir)
    input_json.unlink()
    _fsync_directory(input_json.parent)
    return target


def _coalesce_superseded_materialization_requests(
    requests: Sequence[Path],
    *,
    processed_path: Path,
) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    """Keep only the newest request per semantic forecast scope.

    Seed discovery can enqueue the same city/date/metric/source-cycle request on
    every scheduler tick while a previous copy is still waiting. Running every
    duplicate subprocess burns the materializer budget without producing a newer
    posterior, which lets raw live-input cycles outrun live posteriors. Invalid
    or incomplete payloads are deliberately left untouched here so the normal
    pre-spawn validation gate can fail them with its precise reason code.
    """

    keys: dict[Path, tuple[str, ...]] = {}
    payloads: dict[Path, Mapping[str, object]] = {}
    newest_by_key: dict[tuple[str, ...], tuple[tuple[datetime, int, str], Path]] = {}
    for path in requests:
        payload = _load_request_payload_for_coalescing(path)
        if payload is None:
            continue
        key = _request_semantic_key(payload)
        if key is None:
            continue
        keys[path] = key
        payloads[path] = payload
        freshness = _request_freshness_key(path, payload)
        current = newest_by_key.get(key)
        if current is None or freshness > current[0]:
            newest_by_key[key] = (freshness, path)

    keepers = {path for _freshness, path in newest_by_key.values()}
    remaining: list[Path] = []
    superseded: list[str] = []
    for path in requests:
        key = keys.get(path)
        if key is None or path in keepers:
            remaining.append(path)
            continue
        newest_path = newest_by_key[key][1]
        receipt = _record_latest_terminal_request(
            path,
            processed_path=processed_path,
            request_payload=payloads[path],
            receipt_dir_name="superseded_latest",
            status="SKIPPED_SUPERSEDED_REQUEST",
            reason_codes=(
                "REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_SUPERSEDED_BY_NEWER_DUPLICATE",
            ),
            result_evidence={
                "request_validated": False,
                "subprocess_spawned": False,
                "superseded_by": newest_path.name,
            },
        )
        superseded.append(str(receipt))
    return tuple(remaining), tuple(superseded)


def _claim_request_files(batch_path: Path) -> tuple[Path, ...]:
    return tuple(
        path
        for path in batch_path.glob("*.json")
        if path.is_file() and path.name != _CLAIM_METADATA_NAME
    )


def _claim_age_seconds(batch_path: Path) -> float:
    claimed_at: datetime | None = None
    try:
        payload = json.loads(
            (batch_path / _CLAIM_METADATA_NAME).read_text(encoding="utf-8")
        )
        claimed_at = _parse_utc_iso(payload.get("claimed_at"))
    except (AttributeError, OSError, json.JSONDecodeError):
        pass
    if claimed_at is not None:
        return max(
            0.0,
            (datetime.now(timezone.utc) - claimed_at).total_seconds(),
        )
    try:
        return max(0.0, time.time() - batch_path.stat().st_mtime)
    except OSError:
        return 0.0


def _restore_claimed_request(path: Path, request_path: Path, batch_name: str) -> Path:
    request_path.mkdir(parents=True, exist_ok=True)
    attempt = 0
    while True:
        suffix = "" if attempt == 0 else f".recovered-{batch_name}-{attempt}"
        target = request_path / f"{path.stem}{suffix}{path.suffix}"
        try:
            os.link(path, target)
        except FileExistsError:
            attempt += 1
            continue
        _fsync_directory(request_path)
        path.unlink()
        _fsync_directory(path.parent)
        return target


def _timeout_retry_state(path: Path) -> tuple[str, int, float | None]:
    """Return the stable stem, attempt count, and retry wall clock."""

    stem = path.stem
    if _TIMEOUT_RETRY_MARKER not in stem:
        return stem, 0, None
    base, encoded = stem.rsplit(_TIMEOUT_RETRY_MARKER, 1)
    try:
        attempt_raw, retry_ns_raw = encoded.split("-", 1)
        attempt = int(attempt_raw)
        retry_ns = int(retry_ns_raw)
    except (TypeError, ValueError):
        return stem, 0, None
    if not base or attempt <= 0 or retry_ns <= 0:
        return stem, 0, None
    return base, attempt, retry_ns / 1_000_000_000.0


def _restore_claimed_request_after_timeout(
    path: Path,
    request_path: Path,
) -> Path:
    """Requeue one timed-out family without letting it reclaim the next poll."""

    request_path.mkdir(parents=True, exist_ok=True)
    base, prior_attempt, _retry_at = _timeout_retry_state(path)
    attempt = prior_attempt + 1
    exponent = min(max(0, attempt - 1), 10)
    delay_seconds = min(
        _TIMEOUT_RETRY_BASE_SECONDS * (2**exponent),
        _TIMEOUT_RETRY_MAX_SECONDS,
    )
    retry_ns = int((time.time() + delay_seconds) * 1_000_000_000)
    while True:
        target = request_path / (
            f"{base}{_TIMEOUT_RETRY_MARKER}{attempt}-{retry_ns}{path.suffix}"
        )
        try:
            os.link(path, target)
        except FileExistsError:
            retry_ns += 1
            continue
        _fsync_directory(request_path)
        path.unlink()
        _fsync_directory(path.parent)
        return target


def _remove_empty_claim_batch(batch_path: Path) -> None:
    if _claim_request_files(batch_path):
        return
    try:
        (batch_path / _CLAIM_METADATA_NAME).unlink()
    except FileNotFoundError:
        pass
    try:
        batch_path.rmdir()
    except OSError:
        pass


def _recover_stale_claims(
    *,
    request_path: Path,
    inflight_path: Path,
) -> tuple[frozenset[tuple[str, ...]], int]:
    active_keys: set[tuple[str, ...]] = set()
    recovered = 0
    stale_after = (
        _materialization_subprocess_timeout_seconds()
        + _STALE_CLAIM_GRACE_SECONDS
    )
    if not inflight_path.exists():
        return frozenset(), 0
    for batch_path in sorted(path for path in inflight_path.iterdir() if path.is_dir()):
        request_files = _claim_request_files(batch_path)
        if not request_files:
            _remove_empty_claim_batch(batch_path)
            continue
        if _claim_age_seconds(batch_path) >= stale_after:
            for path in request_files:
                _restore_claimed_request(path, request_path, batch_path.name)
                recovered += 1
            _remove_empty_claim_batch(batch_path)
            continue
        for path in request_files:
            payload = _load_request_payload_for_coalescing(path)
            key = _request_semantic_key(payload) if payload is not None else None
            if key is not None:
                active_keys.add(key)
    return frozenset(active_keys), recovered


def _new_claim_batch(inflight_path: Path, request_files: Sequence[Path]) -> Path:
    inflight_path.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    batch_path = inflight_path / f"{stamp}.pid{os.getpid()}"
    suffix = 0
    while batch_path.exists():
        suffix += 1
        batch_path = inflight_path / f"{stamp}.pid{os.getpid()}.{suffix}"
    batch_path.mkdir()
    metadata_path = batch_path / _CLAIM_METADATA_NAME
    metadata_path.write_text(
        json.dumps(
            {
                "claimed_at": datetime.now(timezone.utc).isoformat(),
                "owner_pid": os.getpid(),
                "request_names": [path.name for path in request_files],
            },
            sort_keys=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    moved: list[tuple[Path, Path]] = []
    try:
        for source in request_files:
            claimed = batch_path / source.name
            os.replace(source, claimed)
            moved.append((claimed, source))
    except Exception:
        for claimed, source in reversed(moved):
            if claimed.exists() and not source.exists():
                os.replace(claimed, source)
        _remove_empty_claim_batch(batch_path)
        raise
    return batch_path


def _day0_enqueue_ownership_cursor_path(request_dir: Path) -> Path:
    """Return the hidden, non-seed cursor co-located with queue state."""
    return request_dir.parent / _DAY0_ENQUEUE_OWNERSHIP_CURSOR_NAME


def _read_day0_enqueue_ownership_cursor(cursor_path: Path) -> str | None:
    """Read a prior filename cursor; malformed sidecars safely restart the rotation."""
    try:
        value = cursor_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    return value if value and Path(value).name == value else None


def _write_day0_enqueue_ownership_cursor(cursor_path: Path, filename: str) -> bool:
    """Atomically persist the last inspected seed filename while the queue lock is held."""
    temporary: Path | None = None
    try:
        cursor_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cursor_path.with_name(
            f".{cursor_path.name}.{os.getpid()}.{time.time_ns()}.tmp"
        )
        temporary.write_text(f"{filename}\n", encoding="utf-8")
        os.replace(temporary, cursor_path)
        return True
    except OSError:
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _rotate_seed_snapshot_after_cursor(seeds: Sequence[Path], cursor: str | None) -> tuple[Path, ...]:
    """Visit each sorted snapshot seed at most once, beginning after the durable cursor."""
    snapshot = tuple(seeds)
    if not snapshot or cursor is None:
        return snapshot
    for index, path in enumerate(snapshot):
        if path.name == cursor:
            return snapshot[index + 1 :] + snapshot[: index + 1]
    for index, path in enumerate(snapshot):
        if path.name > cursor:
            return snapshot[index:] + snapshot[:index]
    return snapshot


def _coalesce_superseded_materialization_seeds(
    seeds: Sequence[Path],
    *,
    processed_path: Path,
) -> tuple[
    tuple[Path, ...],
    tuple[str, ...],
    dict[Path, Mapping[str, object] | None],
]:
    """Keep the newest valid seed for each existing request semantic key."""
    keys: dict[Path, tuple[str, ...]] = {}
    payloads: dict[Path, Mapping[str, object]] = {}
    payload_cache: dict[Path, Mapping[str, object] | None] = {}
    newest_by_key: dict[tuple[str, ...], tuple[tuple[datetime, int, str], Path]] = {}
    for path in seeds:
        payload = _load_request_payload_for_coalescing(path)
        payload_cache[path] = payload
        if payload is None:
            continue
        try:
            validate_materialization_seed(payload)
        except ContractViolation:
            continue
        if (
            payload.get("cycle_advance_enqueue_owner") is True
            and payload.get("day0_observed_extreme_observation_time") is not None
        ):
            continue
        request_key = _request_semantic_key(payload)
        if request_key is None:
            continue
        key = request_key + (
            "seed_upgrade_trigger=" + str(payload.get("upgrade_trigger") or ""),
            "cycle_advance_enqueue_owner="
            + str(payload.get("cycle_advance_enqueue_owner") is True),
        )
        keys[path] = key
        payloads[path] = payload
        freshness = _request_freshness_key(path, payload)
        current = newest_by_key.get(key)
        if current is None or freshness > current[0]:
            newest_by_key[key] = (freshness, path)

    keepers = {path for _freshness, path in newest_by_key.values()}
    remaining: list[Path] = []
    superseded: list[str] = []
    for path in seeds:
        key = keys.get(path)
        if key is None or path in keepers:
            remaining.append(path)
            continue
        newest_path = newest_by_key[key][1]
        moved = _move_request(path, processed_path)
        _write_sidecar(
            moved,
            {
                "status": "SKIPPED_SUPERSEDED_SEED",
                "reason_codes": [
                    "REPLACEMENT_LIVE_MATERIALIZATION_SEED_SUPERSEDED_BY_NEWER_DUPLICATE"
                ],
                "request_written": False,
                "superseded_by": newest_path.name,
            },
        )
        superseded.append(str(moved))
    return tuple(remaining), tuple(superseded), payload_cache


def _prepare_seed_requests(
    *,
    seed_dir: Path | str | None,
    seed_processed_dir: Path | str | None,
    seed_failed_dir: Path | str | None,
    request_dir: Path,
    forecast_db: Path | str | None,
    limit: int,
) -> tuple[list[str], list[str], list[str]]:
    if seed_dir is None:
        return [], [], []
    seed_path = Path(seed_dir)
    if not seed_path.exists():
        return [], [], ["REPLACEMENT_LIVE_MATERIALIZATION_SEED_QUEUE_ABSENT"]
    seed_files = tuple(path for path in seed_path.glob("*.json") if path.is_file())
    if not seed_files:
        return [], [], ["REPLACEMENT_LIVE_MATERIALIZATION_SEED_QUEUE_EMPTY"]
    if seed_processed_dir is None or seed_failed_dir is None:
        raise ValueError("seed_processed_dir and seed_failed_dir are required when seed_dir is set")
    processed_path = Path(seed_processed_dir)
    failed_path = Path(seed_failed_dir)
    processed: list[str] = []
    failed: list[str] = []
    reasons: list[str] = []
    cursor_path = _day0_enqueue_ownership_cursor_path(request_dir)
    raw_snapshot = tuple(sorted(seed_files, key=lambda path: path.name))
    rotated_raw_snapshot = _rotate_seed_snapshot_after_cursor(
        raw_snapshot,
        _read_day0_enqueue_ownership_cursor(cursor_path),
    )
    # Cursor rotation and the inspection bound apply before JSON/DB priority work. The window's
    # raw boundary advances even when priority/actionable work stops early, so retained entries
    # make deterministic progress across passes without unbounded queue-lock I/O.
    actionable_limit = max(int(limit), 0)
    inspection_cap = max(
        actionable_limit * _DAY0_ENQUEUE_OWNERSHIP_INSPECTION_MULTIPLIER,
        _DAY0_ENQUEUE_OWNERSHIP_MIN_INSPECTIONS,
    )
    raw_window = rotated_raw_snapshot[:inspection_cap]
    (
        coalesced_window,
        superseded_seeds,
        seed_payloads,
    ) = _coalesce_superseded_materialization_seeds(
        raw_window,
        processed_path=processed_path,
    )
    processed.extend(superseded_seeds)
    if superseded_seeds:
        reasons.append(
            "REPLACEMENT_LIVE_MATERIALIZATION_SEED_SUPERSEDED_BY_NEWER_DUPLICATE"
        )
    priority = _cycle_advance_seed_priority_map(
        forecast_db,
        coalesced_window,
        seed_payloads,
    )
    seeds = tuple(
        sorted(
            coalesced_window,
            key=lambda path: _cycle_advance_file_sort_key(path, priority),
        )
    )
    actionable_count = 0
    inspected_count = 0
    indeterminate_count = 0
    for seed_json in seeds:
        if actionable_count >= actionable_limit or inspected_count >= inspection_cap:
            break
        inspected_count += 1
        try:
            seed = _load_seed_json(seed_json)
            if not _looks_like_seed(seed):
                continue
            ownership_check = _upgrade_day0_seed_has_current_enqueue_ownership(
                forecast_db=forecast_db,
                seed_file=seed_json,
                seed=seed,
            )
            ownership = ownership_check.ownership
            if ownership is _Day0EnqueueOwnership.STALE:
                moved = _move_request(seed_json, processed_path)
                _write_sidecar(
                    moved,
                    {
                        "status": "SKIPPED_STALE_DAY0_ENQUEUE_OWNER",
                        "reason_codes": [
                            "REPLACEMENT_MATERIALIZATION_STALE_DAY0_ENQUEUE_OWNER"
                        ],
                        "request_written": False,
                    },
                )
                processed.append(str(moved))
                actionable_count += 1
                continue
            if ownership is _Day0EnqueueOwnership.INDETERMINATE:
                indeterminate_count += 1
                continue
            # BOUNDARY CONTRACT (2026-06-10): the seed consumer half. _looks_like_seed
            # only discriminates "is this file a seed at all"; the full SEED schema is
            # enforced here so a seed-shaped-but-malformed file (missing a required field,
            # wrong-typed number) is routed to failed/ with the precise ContractViolation
            # detail in the receipt, at most once — never silently passed to the request
            # builder. Authority basis: pipeline-contract project, operator directive
            # 2026-06-10.
            try:
                validate_materialization_seed(seed)
            except ContractViolation as exc:
                moved = _move_request(seed_json, failed_path)
                _write_sidecar(
                    moved,
                    {
                        "status": "ERROR",
                        "reason_codes": ["REPLACEMENT_LIVE_MATERIALIZATION_SEED_CONTRACT_VIOLATION"],
                        "error": exc.detail,
                        "request_written": False,
                    },
                )
                failed.append(str(moved))
                actionable_count += 1
                continue
            # UPGRADE RE-SEED BYPASS (Task #32, 2026-06-11): a seed written by the fusion-upgrade
            # trigger (upgrade_trigger="instrument_set_expansion") INTENTIONALLY re-materializes a
            # covered scope — "a tradeable posterior exists" is precisely the state it supersedes
            # (that posterior was fused from a strictly smaller instrument set). Coverage-skipping
            # it would make every upgrade seed die as SKIPPED_ALREADY_COVERED and the PARTIAL
            # fusion could never heal. The upgrade seed's idempotency authority is the
            # fusion_upgrade_enqueues marker (at most one enqueue per (scope, cycle,
            # capturable-family-superset) transition). A consumed failure can
            # atomically reclaim that marker, while this terminal unchanged-input
            # receipt remains a no-retry witness — so this bypass cannot loop.
            cycle_regression = _seed_source_cycle_regression(
                forecast_db=forecast_db, seed=seed
            )
            if cycle_regression is not None:
                regression_basis, current_cycle = cycle_regression
                reason_code = (
                    "REPLACEMENT_MATERIALIZATION_SOURCE_CYCLE_BELOW_INPUT_HWM"
                    if regression_basis == "current_ensemble_hwm"
                    else "REPLACEMENT_MATERIALIZATION_SOURCE_CYCLE_REGRESSION"
                )
                moved = _move_request(seed_json, processed_path)
                _write_sidecar(
                    moved,
                    {
                        "status": "SKIPPED_SOURCE_CYCLE_REGRESSION",
                        "reason_codes": [reason_code],
                        "request_written": False,
                        "regression_basis": regression_basis,
                        "request_source_cycle_time": seed.get("source_cycle_time"),
                        "current_cycle_time": current_cycle,
                    },
                )
                processed.append(str(moved))
                actionable_count += 1
                continue
            if not seed.get("upgrade_trigger") and _seed_already_covered(
                forecast_db=forecast_db, seed=seed
            ):
                moved = _move_request(seed_json, processed_path)
                _publish_latest_seed(moved, seed)
                _write_sidecar(
                    moved,
                    {
                        "status": "SKIPPED_ALREADY_COVERED",
                        "reason_codes": ["REPLACEMENT_MATERIALIZATION_SEED_ALREADY_COVERED"],
                        "request_written": False,
                    },
                )
                processed.append(str(moved))
                actionable_count += 1
                continue
            result = build_replacement_forecast_materialization_request(seed, base_dir=seed_json.parent)
            if not result.ok or result.request is None:
                moved = _move_request(seed_json, failed_path)
                _write_sidecar(
                    moved,
                    {
                        "status": result.status,
                        "reason_codes": list(result.reason_codes),
                        "request_written": False,
                    },
                )
                failed.append(str(moved))
                actionable_count += 1
                continue
            marker_path, _fingerprint, unchanged = _blocked_attempt_state(
                marker_dir=request_dir.parent / "blocked_attempts",
                input_json=request_dir / seed_json.name,
                payload=result.request,
                forecast_db=forecast_db,
            )
            if unchanged and marker_path is not None:
                # A fusion-upgrade publisher retains private staging until its
                # enqueue marker is complete.  Moving this terminal seed keeps
                # a durable hardlink witness even when latest/ already points
                # at a newer cycle; unlinking the public queue path could leave
                # staging at nlink=1 and make crash recovery republish it.
                terminal_receipt = {
                    "status": "SKIPPED_UNCHANGED_BLOCKED_INPUT",
                    "reason_codes": [_UNCHANGED_BLOCKED_SEED_SKIP_REASON],
                    "request_written": False,
                    "attempt_fingerprint": _fingerprint,
                    "blocked_attempt_marker": str(marker_path),
                }
                moved = _move_request(
                    seed_json,
                    processed_path,
                    terminal_receipt=terminal_receipt,
                )
                _publish_latest_seed(moved, seed)
                processed.append(str(moved))
                reasons.append(_UNCHANGED_BLOCKED_SEED_SKIP_REASON)
                actionable_count += 1
                continue
            ownership_check = _upgrade_day0_seed_has_current_enqueue_ownership(
                forecast_db=forecast_db,
                seed_file=seed_json,
                seed=seed,
            )
            if ownership_check.ownership is _Day0EnqueueOwnership.STALE:
                moved = _move_request(seed_json, processed_path)
                _write_sidecar(
                    moved,
                    {
                        "status": "SKIPPED_STALE_DAY0_ENQUEUE_OWNER",
                        "reason_codes": [
                            "REPLACEMENT_MATERIALIZATION_STALE_DAY0_ENQUEUE_OWNER"
                        ],
                        "request_written": False,
                    },
                )
                processed.append(str(moved))
                actionable_count += 1
                continue
            if ownership_check.ownership is _Day0EnqueueOwnership.INDETERMINATE:
                indeterminate_count += 1
                continue
            request_path = request_dir / seed_json.name
            request_payload = dict(result.request)
            if ownership_check.witness is not None:
                request_payload["day0_enqueue_owner_witness"] = dict(
                    ownership_check.witness
                )
            _write_request(request_path, request_payload)
            moved = _move_request(seed_json, processed_path)
            _publish_latest_seed(moved, seed)
            _write_sidecar(
                moved,
                {
                    "status": result.status,
                    "reason_codes": list(result.reason_codes),
                    "request_written": str(request_path),
                },
            )
            processed.append(str(moved))
            actionable_count += 1
        except Exception as exc:
            moved = _move_request(seed_json, failed_path)
            _write_sidecar(
                moved,
                {
                    "status": "ERROR",
                    "error_type": exc.__class__.__name__,
                    "error": str(exc),
                    "request_written": False,
                },
            )
            failed.append(str(moved))
            actionable_count += 1
    if raw_window and not _write_day0_enqueue_ownership_cursor(
        cursor_path, raw_window[-1].name
    ):
        reasons.append("REPLACEMENT_MATERIALIZATION_DAY0_ENQUEUE_CURSOR_WRITE_FAILED")
    if indeterminate_count:
        reasons.append("REPLACEMENT_MATERIALIZATION_DAY0_ENQUEUE_OWNER_INDETERMINATE")
    if processed:
        reasons.append("REPLACEMENT_LIVE_MATERIALIZATION_SEED_QUEUE_PROCESSED")
    if failed:
        reasons.append("REPLACEMENT_LIVE_MATERIALIZATION_SEED_FAILED")
    if inspected_count < len(raw_window) or len(raw_window) < len(raw_snapshot):
        reasons.append("REPLACEMENT_LIVE_MATERIALIZATION_SEED_QUEUE_LIMIT_REACHED")
    return processed, failed, reasons


def _claim_replacement_forecast_live_materialization_queue_locked(
    *,
    request_path: Path,
    processed_path: Path,
    failed_path: Path,
    seed_dir: Path | str | None,
    seed_processed_dir: Path | str | None,
    seed_failed_dir: Path | str | None,
    forecast_db: Path | str | None,
    raw_manifest_dir: Path | str | None,
    seed_discovery_limit: int | None,
    seed_limit: int | None,
    limit: int,
    discover: bool,
) -> _MaterializationQueueClaim:
    inflight_path = request_path.parent / MATERIALIZATION_INFLIGHT_DIR_NAME
    active_keys, recovered_count = _recover_stale_claims(
        request_path=request_path,
        inflight_path=inflight_path,
    )
    discovery_report: ReplacementForecastSeedDiscoveryReport | None = None
    if discover and raw_manifest_dir is not None:
        if seed_dir is None:
            raise ValueError(
                "seed_dir is required when forecast_db/raw_manifest_dir discovery is configured"
            )
        if forecast_db is None:
            raise ValueError("forecast_db and raw_manifest_dir must be configured together")
        discovery_report = discover_replacement_forecast_materialization_seeds(
            forecast_db=forecast_db,
            raw_manifest_dir=raw_manifest_dir,
            seed_dir=seed_dir,
            request_dir=request_path,
            inflight_dir=inflight_path,
            limit=int(seed_discovery_limit or seed_limit or limit),
        )
    seed_batch_limit = limit if seed_limit is None else int(seed_limit)
    if seed_batch_limit < 0:
        raise ValueError("seed_limit must not be negative")
    if seed_batch_limit:
        seed_processed, seed_failed, seed_reasons = _prepare_seed_requests(
            seed_dir=seed_dir,
            seed_processed_dir=seed_processed_dir,
            seed_failed_dir=seed_failed_dir,
            request_dir=request_path,
            forecast_db=forecast_db,
            limit=seed_batch_limit,
        )
    else:
        seed_processed, seed_failed, seed_reasons = [], [], [
            "REPLACEMENT_LIVE_MATERIALIZATION_SEED_DEFERRED_FOR_REQUESTS"
        ]
    if recovered_count:
        seed_reasons.append("REPLACEMENT_LIVE_MATERIALIZATION_STALE_CLAIM_RECOVERED")

    request_files = (
        tuple(path for path in request_path.glob("*.json") if path.is_file())
        if request_path.exists()
        else ()
    )
    if not request_files:
        return _MaterializationQueueClaim(
            request_path=request_path,
            batch_path=None,
            processed_path=processed_path,
            failed_path=failed_path,
            claimed_count=0,
            skipped_count=0,
            inflight_deferred_count=0,
            timeout_retry_deferred_count=0,
            processed_files=(),
            failed_files=(),
            seed_processed_files=tuple(seed_processed),
            seed_failed_files=tuple(seed_failed),
            seed_reasons=tuple(seed_reasons),
            discovery_report=discovery_report,
        )

    priority = _cycle_advance_seed_priority_map(forecast_db, request_files)
    requests = tuple(
        sorted(
            request_files,
            key=lambda path: _cycle_advance_file_sort_key(path, priority),
        )
    )
    requests, superseded = _coalesce_superseded_materialization_requests(
        requests,
        processed_path=processed_path,
    )
    claimable: list[Path] = []
    inflight_deferred = 0
    timeout_retry_deferred = 0
    now = time.time()
    for path in requests:
        _base, _attempt, retry_at = _timeout_retry_state(path)
        if retry_at is not None and retry_at > now:
            timeout_retry_deferred += 1
            continue
        payload = _load_request_payload_for_coalescing(path)
        key = _request_semantic_key(payload) if payload is not None else None
        if key is not None and key in active_keys:
            inflight_deferred += 1
        else:
            claimable.append(path)
    selected = tuple(claimable[:limit])
    batch_path = (
        _new_claim_batch(inflight_path, selected)
        if selected
        else None
    )
    return _MaterializationQueueClaim(
        request_path=request_path,
        batch_path=batch_path,
        processed_path=processed_path,
        failed_path=failed_path,
        claimed_count=len(selected),
        skipped_count=(
            inflight_deferred
            + timeout_retry_deferred
            + max(len(claimable) - limit, 0)
        ),
        inflight_deferred_count=inflight_deferred,
        timeout_retry_deferred_count=timeout_retry_deferred,
        processed_files=tuple(superseded),
        failed_files=(),
        seed_processed_files=tuple(seed_processed),
        seed_failed_files=tuple(seed_failed),
        seed_reasons=tuple(seed_reasons),
        discovery_report=discovery_report,
    )


def _claim_only_report(
    claim: _MaterializationQueueClaim,
) -> ReplacementForecastLiveMaterializationQueueReport:
    processed = len(claim.processed_files)
    failed = len(claim.failed_files)
    reasons = list(claim.seed_reasons)
    if claim.inflight_deferred_count:
        reasons.append("REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_INFLIGHT")
    if claim.timeout_retry_deferred_count:
        reasons.append(_TIMEOUT_RETRY_DEFERRED_REASON)
    if claim.skipped_count > (
        claim.inflight_deferred_count + claim.timeout_retry_deferred_count
    ):
        reasons.append("REPLACEMENT_LIVE_MATERIALIZATION_QUEUE_LIMIT_REACHED")
    if processed or failed:
        reasons.append("REPLACEMENT_LIVE_MATERIALIZATION_QUEUE_PROCESSED")
        if processed:
            reasons.append(
                "REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_SUPERSEDED_BY_NEWER_DUPLICATE"
            )
    else:
        reasons.append("REPLACEMENT_LIVE_MATERIALIZATION_QUEUE_EMPTY")
    return ReplacementForecastLiveMaterializationQueueReport(
        status="FAILED" if failed else ("PROCESSED" if processed else "NO_REQUESTS"),
        request_dir=str(claim.request_path),
        processed_dir=str(claim.processed_path),
        failed_dir=str(claim.failed_path),
        processed_count=processed,
        failed_count=failed,
        skipped_count=claim.skipped_count,
        seed_processed_count=len(claim.seed_processed_files),
        seed_failed_count=len(claim.seed_failed_files),
        seed_discovery_report=claim.discovery_report,
        processed_files=claim.processed_files,
        failed_files=claim.failed_files,
        seed_processed_files=claim.seed_processed_files,
        seed_failed_files=claim.seed_failed_files,
        reason_codes=tuple(dict.fromkeys(reasons)),
    )


def process_replacement_forecast_live_materialization_queue(
    *,
    request_dir: Path | str,
    processed_dir: Path | str,
    failed_dir: Path | str,
    seed_dir: Path | str | None = None,
    seed_processed_dir: Path | str | None = None,
    seed_failed_dir: Path | str | None = None,
    forecast_db: Path | str | None = None,
    raw_manifest_dir: Path | str | None = None,
    seed_discovery_limit: int | None = None,
    seed_limit: int | None = None,
    limit: int = 10,
    runner: Runner | None = None,
    discover: bool = True,
) -> ReplacementForecastLiveMaterializationQueueReport:
    """Process local materialization request JSON files.

    The queue consumes already-prepared local request files. It does not discover
    markets, submit orders, edit current facts, or write settlement/trade tables.
    Each request is handed to the same CLI used by manual dry runs so the
    precision guard, product identity, and forecast-class schema rules stay in
    one path. The queue lock covers only seed preparation, deduplication, and an
    atomic move into a recoverable inflight batch; family compute runs outside it.
    """

    request_path = Path(request_dir)
    processed_path = Path(processed_dir)
    failed_path = Path(failed_dir)
    if limit <= 0:
        raise ValueError("limit must be positive")
    with _queue_lock(request_path.parent / ".materialization_queue.lock") as lock_acquired:
        if not lock_acquired:
            return ReplacementForecastLiveMaterializationQueueReport(
                status="LOCKED",
                request_dir=str(request_path),
                processed_dir=str(processed_path),
                failed_dir=str(failed_path),
                processed_count=0,
                failed_count=0,
                skipped_count=0,
                reason_codes=("REPLACEMENT_LIVE_MATERIALIZATION_QUEUE_LOCKED",),
            )
        claim = _claim_replacement_forecast_live_materialization_queue_locked(
            request_path=request_path,
            processed_path=processed_path,
            failed_path=failed_path,
            seed_dir=seed_dir,
            seed_processed_dir=seed_processed_dir,
            seed_failed_dir=seed_failed_dir,
            forecast_db=forecast_db,
            raw_manifest_dir=raw_manifest_dir,
            seed_discovery_limit=seed_discovery_limit,
            seed_limit=seed_limit,
            limit=limit,
            discover=discover,
        )
    if claim.batch_path is None:
        return _claim_only_report(claim)
    try:
        batch_report = _process_claimed_materialization_batch(
            request_path=claim.batch_path,
            processed_path=processed_path,
            failed_path=failed_path,
            forecast_db=forecast_db,
            limit=claim.claimed_count,
            runner=runner,
            marker_dir=request_path.parent / "blocked_attempts",
            retry_path=request_path,
        )
    finally:
        _remove_empty_claim_batch(claim.batch_path)

    reasons = [*claim.seed_reasons, *batch_report.reason_codes]
    if claim.inflight_deferred_count:
        reasons.append("REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_INFLIGHT")
    if claim.timeout_retry_deferred_count:
        reasons.append(_TIMEOUT_RETRY_DEFERRED_REASON)
    if claim.skipped_count > (
        claim.inflight_deferred_count + claim.timeout_retry_deferred_count
    ):
        reasons.append("REPLACEMENT_LIVE_MATERIALIZATION_QUEUE_LIMIT_REACHED")
    if claim.processed_files:
        reasons.append(
            "REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_SUPERSEDED_BY_NEWER_DUPLICATE"
        )
    return ReplacementForecastLiveMaterializationQueueReport(
        status=batch_report.status,
        request_dir=str(request_path),
        processed_dir=str(processed_path),
        failed_dir=str(failed_path),
        processed_count=len(claim.processed_files) + batch_report.processed_count,
        failed_count=len(claim.failed_files) + batch_report.failed_count,
        skipped_count=claim.skipped_count + batch_report.skipped_count,
        seed_processed_count=len(claim.seed_processed_files),
        seed_failed_count=len(claim.seed_failed_files),
        committed_posterior_count=batch_report.committed_posterior_count,
        reactor_wake_published_count=batch_report.reactor_wake_published_count,
        seed_discovery_report=claim.discovery_report,
        processed_files=claim.processed_files + batch_report.processed_files,
        failed_files=claim.failed_files + batch_report.failed_files,
        seed_processed_files=claim.seed_processed_files,
        seed_failed_files=claim.seed_failed_files,
        reason_codes=tuple(dict.fromkeys(reasons)),
    )


def _process_claimed_materialization_batch(
    *,
    request_path: Path,
    processed_path: Path,
    failed_path: Path,
    forecast_db: Path | str | None = None,
    limit: int = 10,
    runner: Runner | None = None,
    marker_dir: Path | None = None,
    retry_path: Path | None = None,
) -> ReplacementForecastLiveMaterializationQueueReport:
    if not request_path.exists():
        return ReplacementForecastLiveMaterializationQueueReport(
            status="NO_REQUESTS",
            request_dir=str(request_path),
            processed_dir=str(processed_path),
            failed_dir=str(failed_path),
            processed_count=0,
            failed_count=0,
            skipped_count=0,
            reason_codes=("REPLACEMENT_LIVE_MATERIALIZATION_QUEUE_ABSENT",),
        )
    request_files = _claim_request_files(request_path)
    if not request_files:
        return ReplacementForecastLiveMaterializationQueueReport(
            status="NO_REQUESTS",
            request_dir=str(request_path),
            processed_dir=str(processed_path),
            failed_dir=str(failed_path),
            processed_count=0,
            failed_count=0,
            skipped_count=0,
            reason_codes=("REPLACEMENT_LIVE_MATERIALIZATION_QUEUE_EMPTY",),
        )
    priority = _cycle_advance_seed_priority_map(forecast_db, request_files)
    requests = tuple(
        sorted(
            request_files,
            key=lambda path: _cycle_advance_file_sort_key(path, priority),
        )
    )

    requests, superseded = _coalesce_superseded_materialization_requests(
        requests,
        processed_path=processed_path,
    )

    processed: list[str] = list(superseded)
    failed: list[str] = []
    unchanged_blocked: list[str] = []
    unchanged_success: list[str] = []
    stale_day0_superseded: list[str] = []
    source_cycle_regressions: list[str] = []
    write_deferred: list[str] = []
    timed_out_requests: list[str] = []
    pending: list[_PendingMaterialization] = []
    marker_dir = marker_dir or request_path.parent / "blocked_attempts"
    for input_json in requests[:limit]:
        # POISON-PILL GATE: validate the request schema before spawning the materializer
        # subprocess. An invalid file (scout stub, malformed JSON, missing required keys)
        # is moved to failed/ here, so it consumes this queue slot AT MOST ONCE and can
        # never crash-and-stay to starve legitimate seeds. See _validate_request_payload.
        valid, reason_code, detail = _validate_request_payload(input_json)
        if not valid:
            _LOG.warning(
                "materialize[%s] rejected pre-spawn: %s (%s)",
                input_json.name,
                reason_code,
                detail,
            )
            moved = _move_request(input_json, failed_path)
            _write_sidecar(
                moved,
                {
                    "status": "ERROR",
                    "returncode": None,
                    "reason_codes": [reason_code],
                    "error": detail,
                    "request_validated": False,
                    "subprocess_spawned": False,
                },
            )
            failed.append(str(moved))
            continue
        request_payload = _load_request_payload_for_coalescing(input_json)
        cycle_regression = (
            _seed_source_cycle_regression(
                forecast_db=forecast_db,
                seed=dict(request_payload),
            )
            if request_payload is not None
            else None
        )
        if request_payload is not None and cycle_regression is not None:
            regression_basis, current_cycle = cycle_regression
            reason_code = (
                "REPLACEMENT_MATERIALIZATION_SOURCE_CYCLE_BELOW_INPUT_HWM"
                if regression_basis == "current_ensemble_hwm"
                else "REPLACEMENT_MATERIALIZATION_SOURCE_CYCLE_REGRESSION"
            )
            receipt = _record_latest_terminal_request(
                input_json,
                processed_path=processed_path,
                request_payload=request_payload,
                receipt_dir_name="superseded_latest",
                status="SKIPPED_SOURCE_CYCLE_REGRESSION",
                reason_codes=(reason_code,),
                result_evidence={
                    "request_validated": True,
                    "subprocess_spawned": False,
                    "regression_basis": regression_basis,
                    "request_source_cycle_time": request_payload.get(
                        "source_cycle_time"
                    ),
                    "current_cycle_time": current_cycle,
                },
            )
            processed.append(str(receipt))
            source_cycle_regressions.append(str(receipt))
            continue
        marker_path, attempt_fingerprint, unchanged = (
            _blocked_attempt_state(
                marker_dir=marker_dir,
                input_json=input_json,
                payload=request_payload,
                forecast_db=forecast_db,
            )
            if request_payload is not None
            else (None, None, False)
        )
        if unchanged:
            receipt = _record_latest_terminal_request(
                input_json,
                processed_path=processed_path,
                request_payload=request_payload,
                receipt_dir_name="blocked_latest",
                status="SKIPPED_UNCHANGED_BLOCKED_INPUT",
                reason_codes=(_UNCHANGED_BLOCKED_SKIP_REASON,),
            )
            processed.append(str(receipt))
            unchanged_blocked.append(str(receipt))
            continue
        # SCOPE: one exact city/date/metric request whose successful posterior
        # commit and current input fingerprint are both proven. DRAIN: the fixed
        # window is measured from the original success, so duplicate arrivals do
        # not extend it. RESET: any input/logic revision or window expiry spawns
        # the normal materializer immediately. This coalesces no probability fact.
        if request_payload is not None and _recent_unchanged_success(
            processed_path=processed_path,
            request_payload=request_payload,
            attempt_fingerprint=attempt_fingerprint,
        ):
            receipt = _record_latest_terminal_request(
                input_json,
                processed_path=processed_path,
                request_payload=request_payload,
                receipt_dir_name="success_coalesced_latest",
                status="SKIPPED_RECENT_UNCHANGED_SUCCESS",
                reason_codes=(_UNCHANGED_SUCCESS_SKIP_REASON,),
                result_evidence={
                    "request_validated": True,
                    "subprocess_spawned": False,
                    "attempt_fingerprint": attempt_fingerprint,
                },
            )
            processed.append(str(receipt))
            unchanged_success.append(str(receipt))
            continue
        pending.append(
            _PendingMaterialization(
                input_json=input_json,
                command=_materialization_command(input_json),
                request_payload=request_payload,
                marker_path=marker_path,
                attempt_fingerprint=attempt_fingerprint,
            )
        )
    if runner is None:
        completed_by_path = _run_materialization_batch(pending)
    else:
        completed_by_path = {}
        for item in pending:
            try:
                completed_by_path[item.input_json] = runner(item.command)
            except subprocess.TimeoutExpired as exc:
                completed_by_path[item.input_json] = _timeout_result(item.command, exc)

    committed_posterior_count = 0
    reactor_wake_published_count = 0
    for item in pending:
        input_json = item.input_json
        completed = completed_by_path[input_json]
        timed_out = completed.returncode == 124
        _surface_subprocess_warnings(input_json.name, completed)
        committed, wake_published = _committed_posterior_wake_status(completed)
        committed_posterior_count += int(committed)
        reactor_wake_published_count += int(wake_published)
        payload = {
            "command": list(item.command),
            "returncode": int(completed.returncode),
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        if timed_out:
            try:
                payload["timeout_seconds"] = json.loads(completed.stderr).get(
                    "timeout_seconds"
                )
            except (TypeError, json.JSONDecodeError):
                payload["timeout_seconds"] = None
            payload["reason_codes"] = [
                "REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_TIMEOUT"
            ]
        result_reason_codes = _subprocess_result_reason_codes(completed)
        if completed.returncode == 0:
            if item.marker_path is not None:
                try:
                    item.marker_path.unlink()
                except FileNotFoundError:
                    pass
            if item.request_payload is None:
                moved = _move_request(input_json, processed_path)
                _write_sidecar(moved, payload)
                processed.append(str(moved))
            else:
                result_evidence: dict[str, object] = {
                    "returncode": int(completed.returncode),
                    "committed_posterior": committed,
                    "reactor_wake_published": wake_published,
                }
                if item.attempt_fingerprint is not None:
                    result_evidence["attempt_fingerprint"] = (
                        item.attempt_fingerprint
                    )
                receipt = _record_latest_terminal_request(
                    input_json,
                    processed_path=processed_path,
                    request_payload=item.request_payload,
                    receipt_dir_name="succeeded_latest",
                    status="SUCCEEDED",
                    reason_codes=result_reason_codes,
                    result_evidence=result_evidence,
                )
                processed.append(str(receipt))
        elif timed_out:
            restored = _restore_claimed_request_after_timeout(
                input_json,
                retry_path or request_path,
            )
            timed_out_requests.append(str(restored))
        elif (
            item.request_payload is not None
            and _STALE_DAY0_ENQUEUE_OWNER_REASON in result_reason_codes
        ):
            if item.marker_path is not None:
                try:
                    item.marker_path.unlink()
                except FileNotFoundError:
                    pass
            receipt = _record_latest_terminal_request(
                input_json,
                processed_path=processed_path,
                request_payload=item.request_payload,
                receipt_dir_name="superseded_latest",
                status="SKIPPED_STALE_DAY0_ENQUEUE_OWNER",
                reason_codes=(_STALE_DAY0_OWNER_SUPERSEDED_REASON,),
            )
            processed.append(str(receipt))
            stale_day0_superseded.append(str(receipt))
        elif (
            item.request_payload is not None
            and _UNCHANGED_BLOCKED_REASON in result_reason_codes
        ):
            try:
                _write_blocked_attempt_marker(
                    marker_path=item.marker_path,
                    payload=item.request_payload,
                    fingerprint=item.attempt_fingerprint,
                )
            except OSError:
                pass
            receipt = _record_latest_terminal_request(
                input_json,
                processed_path=processed_path,
                request_payload=item.request_payload,
                receipt_dir_name="blocked_latest",
                status="BLOCKED_MISSING_PROBABILITY_AUTHORITY",
                reason_codes=(_BLOCKED_INPUT_RECEIPT_REASON, *result_reason_codes),
            )
            processed.append(str(receipt))
            unchanged_blocked.append(str(receipt))
        elif _WRITE_DEFERRED_REASON in result_reason_codes:
            if retry_path is None or input_json.parent == retry_path:
                restored = input_json
            else:
                restored = _restore_claimed_request(
                    input_json,
                    retry_path,
                    request_path.name,
                )
            write_deferred.append(str(restored))
        else:
            moved = _move_request(input_json, failed_path)
            _write_sidecar(moved, payload)
            failed.append(str(moved))

    status = "FAILED" if failed else "PROCESSED"
    reasons = ["REPLACEMENT_LIVE_MATERIALIZATION_QUEUE_PROCESSED"]
    if superseded:
        reasons.append("REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_SUPERSEDED_BY_NEWER_DUPLICATE")
    if unchanged_blocked:
        reasons.append(_UNCHANGED_BLOCKED_SKIP_REASON)
    if unchanged_success:
        reasons.append(_UNCHANGED_SUCCESS_SKIP_REASON)
    if stale_day0_superseded:
        reasons.append(_STALE_DAY0_OWNER_SUPERSEDED_REASON)
    if source_cycle_regressions:
        reasons.append("REPLACEMENT_MATERIALIZATION_SOURCE_CYCLE_REGRESSION")
    if write_deferred:
        reasons.append(_WRITE_DEFERRED_REASON)
        _LOG.warning(
            "replacement forecast writes deferred by transient contention: count=%d",
            len(write_deferred),
        )
    if timed_out_requests:
        reasons.extend(
            (
                "REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_TIMEOUT",
                _TIMEOUT_RETRY_DEFERRED_REASON,
            )
        )
        _LOG.warning(
            "replacement forecast materializations timed out and were deferred: count=%d",
            len(timed_out_requests),
        )
    if failed:
        reasons.append("REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_FAILED")
    if committed_posterior_count > reactor_wake_published_count:
        reasons.append("REPLACEMENT_LIVE_MATERIALIZATION_REACTOR_WAKE_FALLBACK_REQUIRED")
    skipped = max(len(requests) - limit, 0)
    if skipped:
        reasons.append("REPLACEMENT_LIVE_MATERIALIZATION_QUEUE_LIMIT_REACHED")
    return ReplacementForecastLiveMaterializationQueueReport(
        status=status,
        request_dir=str(request_path),
        processed_dir=str(processed_path),
        failed_dir=str(failed_path),
        processed_count=len(processed),
        failed_count=len(failed),
        skipped_count=skipped,
        committed_posterior_count=committed_posterior_count,
        reactor_wake_published_count=reactor_wake_published_count,
        processed_files=tuple(processed),
        failed_files=tuple(failed),
        reason_codes=tuple(reasons),
    )
