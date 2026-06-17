"""Queue runner for replacement forecast shadow materialization requests."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from src.config import PROJECT_ROOT
from src.contracts.replacement_pipeline_files import (
    ContractViolation,
    validate_materialization_request,
    validate_materialization_seed,
)
from src.data.replacement_forecast_cycle_policy import tradeable_grade_coverage_sql
from src.data.replacement_forecast_materialization_request_builder import (
    build_replacement_forecast_materialization_request,
)
from src.data.replacement_forecast_seed_discovery import (
    ReplacementForecastSeedDiscoveryReport,
    discover_replacement_forecast_materialization_seeds,
)


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class ReplacementForecastShadowMaterializationQueueReport:
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


def _run_command(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


_LOG = logging.getLogger("zeus.replacement_shadow_materialization_queue")


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


def _quarantine_stale_lock(lock_path: Path, *, holder_pid: int | None) -> Path | None:
    """Move an orphaned lock into ``quarantined_stale_locks/`` (audit trail; never silent-delete)."""
    qdir = lock_path.parent / "quarantined_stale_locks"
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
    unparseable) holder means the lock is orphaned, so we quarantine it for audit and steal the
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
            _quarantine_stale_lock(lock_path, holder_pid=holder_pid)
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
    required = {
        "city",
        "target_date",
        "temperature_metric",
        "computed_at",
        "baseline_source_run_id",
        "aifs_source_run_id",
        "openmeteo_source_run_id",
        "openmeteo_payload_json",
        "precision_metadata_json",
        "bins",
    }
    return required.issubset(payload) and ("aifs_samples_json" in payload or "aifs_grib_path" in payload)


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
        posterior_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(forecast_posteriors)").fetchall()
        }
        # TRADEABLE-GRADE COVERAGE (operator directive 2026-06-10; basis-predicate fix 2026-06-12).
        # A covering posterior must be CERTIFIED-bootstrap tradeable-grade. The original proxy
        # `q_lcb_json IS NOT NULL` broke once the soft-anchor path began carrying a PROMOTED
        # Wilson-over-AIFS-votes q_lcb (basis="wilson_aifs_member_votes") instead of NULL: a
        # CAPTURE_MISSING / FUSED_Q_BUILD_FAILED row is STILL not live-eligible, yet now has a
        # non-NULL q_lcb and would WRONGLY satisfy the old proxy — re-introducing the exact
        # mask-and-starve disease (an untradeable posterior counting as "done forever" and blocking
        # its own fusion repair). The predicate now keys on the CERTIFIED bootstrap basis (the SAME
        # string the live calibration-credential reader pins), so only a fusion-grade row counts as
        # coverage and a soft-anchor/CAPTURE_MISSING row re-seeds on the next cycle. Single authority:
        # cycle_policy.tradeable_grade_coverage_sql. Schema-conditional as before.
        tradeable_grade_clause = tradeable_grade_coverage_sql(posterior_columns=posterior_columns)
        posterior = conn.execute(
            f"""
            SELECT 1
            FROM forecast_posteriors
            WHERE source_id = 'openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor'
              AND city = ?
              AND target_date = ?
              AND temperature_metric = ?
              {tradeable_grade_clause}
              AND json_extract(dependency_source_run_ids_json, '$.baseline_b0') = ?
            LIMIT 1
            """,
            (city, target_date, metric, baseline_source_run_id),
        ).fetchone()
        if posterior is None:
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
            SELECT 1
            FROM readiness_state
            WHERE strategy_key = 'openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor'
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
            LIMIT 1
            """,
            (city, target_date, metric, baseline_source_run_id),
        ).fetchone()
        return readiness is not None
    finally:
        conn.close()


def _write_request(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")


# POISON-PILL IMMUNITY (2026-06-10): the materializer subprocess accesses these keys
# unconditionally and immediately (scripts/materialize_replacement_forecast_shadow.py:163-165,
# then 173/178/197 for the aifs input). A request file missing any of them — e.g. a
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


def _validate_request_payload(path: Path) -> tuple[bool, str, str]:
    """Return (ok, reason_code, detail) for a queued request file WITHOUT spawning a subprocess.

    A valid materialization request always carries the minimal keys the materializer accesses
    before any work (temperature_metric, target_date, source_cycle_time) plus one AIFS input
    selector (aifs_samples_json or aifs_grib_path). Anything else (a scout intent stub,
    unparseable JSON, a non-object) is poison: fail it fast so it leaves the queue at most once.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, "REPLACEMENT_SHADOW_MATERIALIZATION_REQUEST_UNREADABLE", repr(exc)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return False, "REPLACEMENT_SHADOW_MATERIALIZATION_REQUEST_MALFORMED_JSON", str(exc)
    if not isinstance(payload, dict):
        return False, "REPLACEMENT_SHADOW_MATERIALIZATION_REQUEST_NOT_OBJECT", f"top-level {type(payload).__name__}"
    # BOUNDARY CONTRACT (2026-06-10): the consumer half of the producer⇄consumer
    # contract. This replaces the ad-hoc required-key / AIFS-input checks with the
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
            reason_code = "REPLACEMENT_SHADOW_MATERIALIZATION_REQUEST_MISSING_REQUIRED_KEYS"
        elif "AIFS input selector" in exc.detail:
            reason_code = "REPLACEMENT_SHADOW_MATERIALIZATION_REQUEST_MISSING_AIFS_INPUT"
        else:
            reason_code = "REPLACEMENT_SHADOW_MATERIALIZATION_REQUEST_CONTRACT_VIOLATION"
        return (
            False,
            reason_code,
            exc.detail,
        )
    return True, "", ""


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
        return [], [], ["REPLACEMENT_SHADOW_MATERIALIZATION_SEED_QUEUE_ABSENT"]
    seeds = tuple(sorted(path for path in seed_path.glob("*.json") if path.is_file()))
    if not seeds:
        return [], [], ["REPLACEMENT_SHADOW_MATERIALIZATION_SEED_QUEUE_EMPTY"]
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
                        "reason_codes": ["REPLACEMENT_SHADOW_MATERIALIZATION_SEED_CONTRACT_VIOLATION"],
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
        reasons.append("REPLACEMENT_SHADOW_MATERIALIZATION_SEED_QUEUE_PROCESSED")
    if failed:
        reasons.append("REPLACEMENT_SHADOW_MATERIALIZATION_SEED_FAILED")
    if max(len(seeds) - limit, 0):
        reasons.append("REPLACEMENT_SHADOW_MATERIALIZATION_SEED_QUEUE_LIMIT_REACHED")
    return processed, failed, reasons


def process_replacement_forecast_shadow_materialization_queue(
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
    runner: Runner = _run_command,
) -> ReplacementForecastShadowMaterializationQueueReport:
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
            return ReplacementForecastShadowMaterializationQueueReport(
                status="LOCKED",
                request_dir=str(request_path),
                processed_dir=str(processed_path),
                failed_dir=str(failed_path),
                processed_count=0,
                failed_count=0,
                skipped_count=0,
                reason_codes=("REPLACEMENT_SHADOW_MATERIALIZATION_QUEUE_LOCKED",),
            )
        return _process_replacement_forecast_shadow_materialization_queue_locked(
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
        )


def _process_replacement_forecast_shadow_materialization_queue_locked(
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
    runner: Runner = _run_command,
) -> ReplacementForecastShadowMaterializationQueueReport:
    discovery_report: ReplacementForecastSeedDiscoveryReport | None = None
    if raw_manifest_dir is not None:
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
        return ReplacementForecastShadowMaterializationQueueReport(
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
            reason_codes=tuple(seed_reasons + ["REPLACEMENT_SHADOW_MATERIALIZATION_QUEUE_ABSENT"]),
        )
    requests = tuple(sorted(path for path in request_path.glob("*.json") if path.is_file()))
    if not requests:
        return ReplacementForecastShadowMaterializationQueueReport(
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
            reason_codes=tuple(seed_reasons + ["REPLACEMENT_SHADOW_MATERIALIZATION_QUEUE_EMPTY"]),
        )

    processed: list[str] = []
    failed: list[str] = []
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
        command = (
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "materialize_replacement_forecast_shadow.py"),
            "--input-json",
            str(input_json),
            "--init-schema",
            "--commit",
        )
        completed = runner(command)
        _surface_subprocess_warnings(input_json.name, completed)
        payload = {
            "command": list(command),
            "returncode": int(completed.returncode),
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        if completed.returncode == 0:
            moved = _move_request(input_json, processed_path)
            _write_sidecar(moved, payload)
            processed.append(str(moved))
        else:
            moved = _move_request(input_json, failed_path)
            _write_sidecar(moved, payload)
            failed.append(str(moved))

    status = "FAILED" if failed else "PROCESSED"
    reasons = [*seed_reasons, "REPLACEMENT_SHADOW_MATERIALIZATION_QUEUE_PROCESSED"]
    if failed:
        reasons.append("REPLACEMENT_SHADOW_MATERIALIZATION_REQUEST_FAILED")
    skipped = max(len(requests) - limit, 0)
    if skipped:
        reasons.append("REPLACEMENT_SHADOW_MATERIALIZATION_QUEUE_LIMIT_REACHED")
    return ReplacementForecastShadowMaterializationQueueReport(
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
