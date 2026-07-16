"""Queue runner for replacement forecast live materialization requests."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

from src.config import PROJECT_ROOT
from src.contracts.replacement_pipeline_files import (
    ContractViolation,
    validate_materialization_request,
    validate_materialization_seed,
)
from src.data.replacement_forecast_cycle_policy import tradeable_grade_coverage_sql
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
DEFAULT_MATERIALIZATION_SUBPROCESS_TIMEOUT_SECONDS = 240.0


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


def _parse_batch_results(
    stdout: str | bytes | None,
) -> tuple[dict[Path, subprocess.CompletedProcess[str]], list[str]]:
    if isinstance(stdout, bytes):
        stdout = stdout.decode(errors="replace")
    parsed: dict[Path, subprocess.CompletedProcess[str]] = {}
    protocol_errors: list[str] = []
    for line in (stdout or "").splitlines():
        try:
            payload = json.loads(line)
            input_json = Path(str(payload["input_json"]))
            parsed[input_json] = subprocess.CompletedProcess(
                args=list(_materialization_command(input_json)),
                returncode=int(payload["returncode"]),
                stdout=str(payload.get("stdout") or ""),
                stderr=str(payload.get("stderr") or ""),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            protocol_errors.append(f"{exc.__class__.__name__}: {exc}")
    return parsed, protocol_errors


def _run_materialization_batch(
    pending: Sequence[_PendingMaterialization],
) -> dict[Path, subprocess.CompletedProcess[str]]:
    if not pending:
        return {}
    command = (
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "materialize_replacement_forecast_live.py"),
        "--batch-input-json",
        *(str(item.input_json) for item in pending),
        "--commit",
    )
    try:
        batch = _run_command(command)
    except subprocess.TimeoutExpired as exc:
        parsed, _ = _parse_batch_results(exc.stdout)
        for item in pending:
            parsed.setdefault(
                item.input_json,
                _timeout_result(item.command, exc),
            )
        return parsed
    parsed, protocol_errors = _parse_batch_results(batch.stdout)
    for item in pending:
        if item.input_json not in parsed:
            error_type = (
                "MaterializationBatchProcessError"
                if batch.returncode != 0
                else "MaterializationBatchProtocolError"
            )
            parsed[item.input_json] = subprocess.CompletedProcess(
                args=list(item.command),
                returncode=int(batch.returncode) if batch.returncode != 0 else 2,
                stdout="",
                stderr=json.dumps(
                    {
                        "status": "ERROR",
                        "error_type": error_type,
                        "error": "batch result missing for request",
                        "details": protocol_errors,
                        "batch_stderr": batch.stderr,
                    },
                    sort_keys=True,
                )
                + "\n",
            )
    return parsed


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


def _receipt_name(path: Path) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{path.stem}.{stamp}{path.suffix}"


def _move_request(path: Path, destination_dir: Path) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / _receipt_name(path)
    while target.exists():
        target = destination_dir / _receipt_name(path)
    os.replace(path, target)
    return target


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


def _seed_already_covered(*, forecast_db: Path | str | None, seed: dict[str, object]) -> bool:
    if forecast_db is None:
        return False
    from src.state.db import _connect

    db_path = Path(forecast_db)
    if not db_path.exists():
        return False
    conn = _connect(db_path, write_class="live")
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
        tradeable_grade_clause = tradeable_grade_coverage_sql(posterior_columns=posterior_columns)
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
            ORDER BY datetime(computed_at) DESC, posterior_id DESC
            LIMIT 1
            """,
            (SOURCE_ID, city, target_date, metric, baseline_source_run_id, openmeteo_source_run_id),
        ).fetchone()
        if posterior is None:
            return False
        decision_time = _parse_utc_iso(seed.get("computed_at")) or datetime.now(timezone.utc)
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
        seed_observation_time = _parse_utc_iso(
            seed.get("day0_observed_extreme_observation_time")
        )
        if seed_observation_time is not None:
            try:
                posterior_provenance = json.loads(
                    str(posterior["provenance_json"] or "{}")
                )
            except (TypeError, ValueError):
                posterior_provenance = {}
            conditioning = (
                posterior_provenance.get("day0_conditioning")
                if isinstance(posterior_provenance, dict)
                else None
            )
            posterior_observation_time = _parse_utc_iso(
                conditioning.get("observation_time")
                if isinstance(conditioning, dict)
                else None
            )
            if (
                posterior_observation_time is None
                or posterior_observation_time < seed_observation_time
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


def _seed_source_cycle_regresses_current_posterior(
    *,
    forecast_db: Path | str | None,
    seed: dict[str, object],
) -> bool:
    """True when this seed is older than the family's latest materialized posterior.

    The materializer's monotone consumed-cycle guard remains the final authority.
    This queue-side check only prevents a seed that is already known to be
    unconstructable from spending a subprocess slot every cycle.
    """

    if forecast_db is None:
        return False
    request_cycle = _parse_utc_iso(seed.get("source_cycle_time"))
    if request_cycle is None:
        return False
    db_path = Path(forecast_db)
    if not db_path.exists():
        return False
    from src.state.db import _connect

    try:
        conn = _connect(db_path, write_class="live")
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
        finally:
            conn.close()
    except Exception:
        return False
    if row is None:
        return False
    current_raw = row["source_cycle_time"] if hasattr(row, "keys") else row[0]
    current_cycle = _parse_utc_iso(current_raw)
    return current_cycle is not None and request_cycle < current_cycle


def _write_request(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")


def _cycle_advance_seed_priority_map(
    forecast_db: Path | str | None,
) -> dict[str, tuple[int, str]]:
    """Return filename -> priority for cycle-advance seeds/requests.

    The producer records whether a seed repairs a held-position family in
    ``cycle_advance_enqueues``. The consumer must preserve that priority after a
    seed becomes either a seed file or a request file; plain filename ordering
    can otherwise spend live cycles on non-held cities while a held position has
    stale belief.
    """
    if forecast_db is None:
        return {}
    db_path = Path(forecast_db)
    if not db_path.exists():
        return {}
    try:
        from src.state.db import _connect  # noqa: PLC0415

        conn = _connect(db_path, write_class=None)
        try:
            conn.execute("PRAGMA query_only=ON")
            table = conn.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type='table' AND name='cycle_advance_enqueues'
                LIMIT 1
                """
            ).fetchone()
            if table is None:
                return {}
            rows = conn.execute(
                """
                SELECT seed_file, held_position, enqueued_at
                FROM cycle_advance_enqueues
                WHERE seed_file IS NOT NULL AND seed_file != ''
                """
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - priority is best-effort; queue must still drain
        return {}

    priority: dict[str, tuple[int, str]] = {}
    for seed_file, held_position, enqueued_at in rows:
        name = Path(str(seed_file)).name
        if not name:
            continue
        value = (0 if int(held_position or 0) == 1 else 1, str(enqueued_at or ""))
        current = priority.get(name)
        if current is None or value < current:
            priority[name] = value
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
_UNCHANGED_BLOCKED_REASON = "REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET"
_UNCHANGED_BLOCKED_SKIP_REASON = (
    "REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_UNCHANGED_BLOCKED_INPUT"
)
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

        scheme = scheme_for_city(city)
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
    return tuple(source for source in scheme.final_sources if source not in served)


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
    try:
        from src.state.db import _connect  # noqa: PLC0415

        conn = _connect(db_path, write_class=None)
        try:
            conn.execute("PRAGMA query_only=ON")
            missing_sources = _source_clock_missing_configured_sources(conn, payload)
            row = conn.execute(
                """
                SELECT COUNT(*),
                       COALESCE(MAX(raw_model_forecast_id), 0),
                       COALESCE(MAX(captured_at), ''),
                       COALESCE(MAX(source_available_at), '')
                FROM raw_model_forecasts
                WHERE city = ?
                  AND target_date = ?
                  AND metric = ?
                """,
                scope,
            ).fetchone()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - unknown watermark must retry, never suppress work
        return None
    if row is None:
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
            "raw": (
                {"missing_configured_sources": missing_sources}
                if missing_sources
                else tuple(row)
            ),
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
    for line in reversed((completed.stdout or "").splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, Mapping):
            continue
        reasons = payload.get("reason_codes")
        if not isinstance(reasons, list):
            return ()
        return tuple(str(reason) for reason in reasons)
    return ()


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
    newest_by_key: dict[tuple[str, ...], tuple[tuple[datetime, int, str], Path]] = {}
    for path in requests:
        payload = _load_request_payload_for_coalescing(path)
        if payload is None:
            continue
        key = _request_semantic_key(payload)
        if key is None:
            continue
        keys[path] = key
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
        moved = _move_request(path, processed_path)
        _write_sidecar(
            moved,
            {
                "status": "SKIPPED_SUPERSEDED_REQUEST",
                "reason_codes": [
                    "REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_SUPERSEDED_BY_NEWER_DUPLICATE"
                ],
                "request_written": False,
                "request_validated": False,
                "subprocess_spawned": False,
                "superseded_by": newest_path.name,
                "semantic_key": {
                    field: key[idx] for idx, field in enumerate(_REQUEST_DEDUP_KEY_FIELDS)
                },
            },
        )
        superseded.append(str(moved))
    return tuple(remaining), tuple(superseded)


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
    priority = _cycle_advance_seed_priority_map(forecast_db)
    seeds = tuple(
        sorted(
            (path for path in seed_path.glob("*.json") if path.is_file()),
            key=lambda path: _cycle_advance_file_sort_key(path, priority),
        )
    )
    if not seeds:
        return [], [], ["REPLACEMENT_LIVE_MATERIALIZATION_SEED_QUEUE_EMPTY"]
    if seed_processed_dir is None or seed_failed_dir is None:
        raise ValueError("seed_processed_dir and seed_failed_dir are required when seed_dir is set")
    processed_path = Path(seed_processed_dir)
    failed_path = Path(seed_failed_dir)
    processed: list[str] = []
    failed: list[str] = []
    reasons: list[str] = []
    for seed_json in seeds[:limit]:
        try:
            seed = _load_seed_json(seed_json)
            if not _looks_like_seed(seed):
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
                continue
            # UPGRADE RE-SEED BYPASS (Task #32, 2026-06-11): a seed written by the fusion-upgrade
            # trigger (upgrade_trigger="instrument_set_expansion") INTENTIONALLY re-materializes a
            # covered scope — "a tradeable posterior exists" is precisely the state it supersedes
            # (that posterior was fused from a strictly smaller instrument set). Coverage-skipping
            # it would make every upgrade seed die as SKIPPED_ALREADY_COVERED and the PARTIAL
            # fusion could never heal. The upgrade seed's idempotency authority is the
            # fusion_upgrade_enqueues marker (at most one enqueue per (scope, cycle,
            # capturable-family-superset) transition), NOT coverage — so this bypass cannot loop.
            if not seed.get("upgrade_trigger") and _seed_already_covered(
                forecast_db=forecast_db, seed=seed
            ):
                moved = _move_request(seed_json, processed_path)
                _write_sidecar(
                    moved,
                    {
                        "status": "SKIPPED_ALREADY_COVERED",
                        "reason_codes": ["REPLACEMENT_MATERIALIZATION_SEED_ALREADY_COVERED"],
                        "request_written": False,
                    },
                )
                processed.append(str(moved))
                continue
            if _seed_source_cycle_regresses_current_posterior(
                forecast_db=forecast_db, seed=seed
            ):
                moved = _move_request(seed_json, processed_path)
                _write_sidecar(
                    moved,
                    {
                        "status": "SKIPPED_SOURCE_CYCLE_REGRESSION",
                        "reason_codes": ["REPLACEMENT_MATERIALIZATION_SOURCE_CYCLE_REGRESSION"],
                        "request_written": False,
                    },
                )
                processed.append(str(moved))
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
                continue
            request_path = request_dir / seed_json.name
            _write_request(request_path, dict(result.request))
            moved = _move_request(seed_json, processed_path)
            _write_sidecar(
                moved,
                {
                    "status": result.status,
                    "reason_codes": list(result.reason_codes),
                    "request_written": str(request_path),
                },
            )
            processed.append(str(moved))
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
    if processed:
        reasons.append("REPLACEMENT_LIVE_MATERIALIZATION_SEED_QUEUE_PROCESSED")
    if failed:
        reasons.append("REPLACEMENT_LIVE_MATERIALIZATION_SEED_FAILED")
    if max(len(seeds) - limit, 0):
        reasons.append("REPLACEMENT_LIVE_MATERIALIZATION_SEED_QUEUE_LIMIT_REACHED")
    return processed, failed, reasons


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
    one path.
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
        return _process_replacement_forecast_live_materialization_queue_locked(
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
            runner=runner,
            discover=discover,
        )


def _process_replacement_forecast_live_materialization_queue_locked(
    *,
    request_path: Path,
    processed_path: Path,
    failed_path: Path,
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
    discovery_report: ReplacementForecastSeedDiscoveryReport | None = None
    if discover and raw_manifest_dir is not None:
        if seed_dir is None:
            raise ValueError("seed_dir is required when forecast_db/raw_manifest_dir discovery is configured")
        if forecast_db is None:
            raise ValueError("forecast_db and raw_manifest_dir must be configured together")
        discovery_report = discover_replacement_forecast_materialization_seeds(
            forecast_db=forecast_db,
            raw_manifest_dir=raw_manifest_dir,
            seed_dir=seed_dir,
            limit=int(seed_discovery_limit or seed_limit or limit),
        )
    seed_processed, seed_failed, seed_reasons = _prepare_seed_requests(
        seed_dir=seed_dir,
        seed_processed_dir=seed_processed_dir,
        seed_failed_dir=seed_failed_dir,
        request_dir=request_path,
        forecast_db=forecast_db,
        limit=int(seed_limit or limit),
    )
    if not request_path.exists():
        return ReplacementForecastLiveMaterializationQueueReport(
            status="NO_REQUESTS",
            request_dir=str(request_path),
            processed_dir=str(processed_path),
            failed_dir=str(failed_path),
            processed_count=0,
            failed_count=0,
            skipped_count=0,
            seed_processed_count=len(seed_processed),
            seed_failed_count=len(seed_failed),
            seed_discovery_report=discovery_report,
            processed_files=(),
            failed_files=(),
            seed_processed_files=tuple(seed_processed),
            seed_failed_files=tuple(seed_failed),
            reason_codes=tuple(seed_reasons + ["REPLACEMENT_LIVE_MATERIALIZATION_QUEUE_ABSENT"]),
        )
    priority = _cycle_advance_seed_priority_map(forecast_db)
    requests = tuple(
        sorted(
            (path for path in request_path.glob("*.json") if path.is_file()),
            key=lambda path: _cycle_advance_file_sort_key(path, priority),
        )
    )
    if not requests:
        return ReplacementForecastLiveMaterializationQueueReport(
            status="NO_REQUESTS",
            request_dir=str(request_path),
            processed_dir=str(processed_path),
            failed_dir=str(failed_path),
            processed_count=0,
            failed_count=0,
            skipped_count=0,
            seed_processed_count=len(seed_processed),
            seed_failed_count=len(seed_failed),
            seed_discovery_report=discovery_report,
            processed_files=(),
            failed_files=(),
            seed_processed_files=tuple(seed_processed),
            seed_failed_files=tuple(seed_failed),
            reason_codes=tuple(seed_reasons + ["REPLACEMENT_LIVE_MATERIALIZATION_QUEUE_EMPTY"]),
        )

    requests, superseded = _coalesce_superseded_materialization_requests(
        requests,
        processed_path=processed_path,
    )

    processed: list[str] = list(superseded)
    failed: list[str] = []
    unchanged_blocked: list[str] = []
    pending: list[_PendingMaterialization] = []
    marker_dir = request_path.parent / "blocked_attempts"
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
            moved = _move_request(input_json, processed_path)
            _write_sidecar(
                moved,
                {
                    "status": "SKIPPED_UNCHANGED_BLOCKED_INPUT",
                    "reason_codes": [_UNCHANGED_BLOCKED_SKIP_REASON],
                    "request_validated": True,
                    "subprocess_spawned": False,
                    "attempt_fingerprint": attempt_fingerprint,
                },
            )
            processed.append(str(moved))
            unchanged_blocked.append(str(moved))
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

    for item in pending:
        input_json = item.input_json
        completed = completed_by_path[input_json]
        timed_out = completed.returncode == 124
        _surface_subprocess_warnings(input_json.name, completed)
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
        if completed.returncode == 0:
            if item.marker_path is not None:
                try:
                    item.marker_path.unlink()
                except FileNotFoundError:
                    pass
            moved = _move_request(input_json, processed_path)
            _write_sidecar(moved, payload)
            processed.append(str(moved))
        else:
            if (
                item.request_payload is not None
                and _UNCHANGED_BLOCKED_REASON
                in _subprocess_result_reason_codes(completed)
            ):
                try:
                    _write_blocked_attempt_marker(
                        marker_path=item.marker_path,
                        payload=item.request_payload,
                        fingerprint=item.attempt_fingerprint,
                    )
                except OSError:
                    pass
            moved = _move_request(input_json, failed_path)
            _write_sidecar(moved, payload)
            failed.append(str(moved))

    status = "FAILED" if failed else "PROCESSED"
    reasons = [*seed_reasons, "REPLACEMENT_LIVE_MATERIALIZATION_QUEUE_PROCESSED"]
    if superseded:
        reasons.append("REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_SUPERSEDED_BY_NEWER_DUPLICATE")
    if unchanged_blocked:
        reasons.append(_UNCHANGED_BLOCKED_SKIP_REASON)
    if failed:
        reasons.append("REPLACEMENT_LIVE_MATERIALIZATION_REQUEST_FAILED")
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
        seed_processed_count=len(seed_processed),
        seed_failed_count=len(seed_failed),
        seed_discovery_report=discovery_report,
        processed_files=tuple(processed),
        failed_files=tuple(failed),
        seed_processed_files=tuple(seed_processed),
        seed_failed_files=tuple(seed_failed),
        reason_codes=tuple(reasons),
    )
