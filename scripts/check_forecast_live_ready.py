#!/usr/bin/env python3
# Created: 2026-05-14
# Last reused/audited: 2026-05-14
# Authority basis: docs/operations/task_2026-05-08_deep_alignment_audit/DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md Phase 8 staged end-to-end verification.
"""Read-only forecast-live authority-chain verifier.

This script never initializes schemas, fetches external sources, starts
daemons, touches launchctl, writes canonical DB/state truth, or authorizes live
trading. It reports the highest evidence-backed completion state plus exact
blockers so operators and future agents cannot confuse code presence with live
data readiness.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.producer_readiness import PRODUCER_READINESS_STRATEGY_KEY
from src.state.db import ZEUS_FORECASTS_DB_PATH

SOURCE_ID = "ecmwf_open_data"
SOURCE_TRANSPORT = "ensemble_snapshots_v2_db_reader"
DEFAULT_HEARTBEAT_PATH = ROOT / "state" / "forecast-live-heartbeat.json"
DEFAULT_SOURCE_HEALTH_PATH = ROOT / "state" / "source_health.json"


class CompletionState(StrEnum):
    CODE_READY_ON_HEAD = "CODE_READY_ON_HEAD"
    OPERATOR_LAUNCH_READY = "OPERATOR_LAUNCH_READY"
    LIVE_RUNNING = "LIVE_RUNNING"
    PRODUCER_READY = "PRODUCER_READY"
    LIVE_CONSUMING = "LIVE_CONSUMING"
    DONE = "DONE"


@dataclass(frozen=True)
class TrackConfig:
    label: str
    source_run_track: str
    job_name: str
    temperature_metric: str


TRACKS: tuple[TrackConfig, ...] = (
    TrackConfig(
        label="HIGH",
        source_run_track="mx2t6_high",
        job_name="forecast_live_opendata_mx2t6_high",
        temperature_metric="high",
    ),
    TrackConfig(
        label="LOW",
        source_run_track="mn2t6_low",
        job_name="forecast_live_opendata_mn2t6_low",
        temperature_metric="low",
    ),
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class TrackReport:
    label: str
    ready: bool
    blockers: list[str]
    job_run: dict[str, Any] | None
    source_run: dict[str, Any] | None
    coverage: dict[str, Any] | None
    readiness: dict[str, Any] | None


@dataclass(frozen=True)
class ForecastLiveReadyReport:
    generated_at: str
    claim_mode: str
    db_path: str
    source_id: str
    highest_completion_state: str
    producer_ready: bool
    runtime_ready: bool
    live_claim_authorized: bool
    blockers: list[str]
    checks: list[CheckResult]
    tracks: list[TrackReport]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_utc(value: str | None, *, field: str) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(str(db_path))
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
    )


def _latest_job_run(conn: sqlite3.Connection, job_name: str) -> dict[str, Any] | None:
    return _row(
        conn.execute(
            """
            SELECT *
            FROM job_run
            WHERE job_name = ?
            ORDER BY scheduled_for DESC, recorded_at DESC
            LIMIT 1
            """,
            (job_name,),
        ).fetchone()
    )


def _latest_source_run(conn: sqlite3.Connection, source_id: str, track: str) -> dict[str, Any] | None:
    return _row(
        conn.execute(
            """
            SELECT *
            FROM source_run
            WHERE source_id = ? AND track = ?
            ORDER BY source_cycle_time DESC, recorded_at DESC
            LIMIT 1
            """,
            (source_id, track),
        ).fetchone()
    )


def _latest_coverage(conn: sqlite3.Connection, source_id: str, temperature_metric: str) -> dict[str, Any] | None:
    return _row(
        conn.execute(
            """
            SELECT *
            FROM source_run_coverage
            WHERE source_id = ?
              AND source_transport = ?
              AND temperature_metric = ?
            ORDER BY computed_at DESC, recorded_at DESC
            LIMIT 1
            """,
            (source_id, SOURCE_TRANSPORT, temperature_metric),
        ).fetchone()
    )


def _latest_readiness(conn: sqlite3.Connection, source_id: str, temperature_metric: str) -> dict[str, Any] | None:
    return _row(
        conn.execute(
            """
            SELECT *
            FROM readiness_state
            WHERE source_id = ?
              AND temperature_metric = ?
              AND strategy_key = ?
            ORDER BY computed_at DESC, recorded_at DESC
            LIMIT 1
            """,
            (source_id, temperature_metric, PRODUCER_READINESS_STRATEGY_KEY),
        ).fetchone()
    )


def _json_obj(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _not_expired(value: str | None, now_utc: datetime) -> bool:
    parsed = _parse_utc(value, field="expires_at")
    return parsed is not None and parsed > now_utc


def _evaluate_track(conn: sqlite3.Connection, config: TrackConfig, now_utc: datetime) -> TrackReport:
    blockers: list[str] = []
    prefix = config.label
    job_run = _latest_job_run(conn, config.job_name)
    source_run = _latest_source_run(conn, SOURCE_ID, config.source_run_track)
    coverage = _latest_coverage(conn, SOURCE_ID, config.temperature_metric)
    readiness = _latest_readiness(conn, SOURCE_ID, config.temperature_metric)

    if job_run is None:
        blockers.append(f"{prefix}_JOB_RUN_MISSING")
    elif job_run.get("status") != "SUCCESS":
        blockers.append(f"{prefix}_JOB_RUN_NOT_SUCCESS:{job_run.get('status')}")

    if source_run is None:
        blockers.append(f"{prefix}_SOURCE_RUN_MISSING")
    else:
        if source_run.get("status") != "SUCCESS":
            blockers.append(f"{prefix}_SOURCE_RUN_NOT_SUCCESS:{source_run.get('status')}")
        if source_run.get("completeness_status") != "COMPLETE":
            blockers.append(f"{prefix}_SOURCE_RUN_NOT_COMPLETE:{source_run.get('completeness_status')}")

    expected_source_run_id = source_run.get("source_run_id") if source_run else None
    if job_run and source_run and job_run.get("source_run_id") != expected_source_run_id:
        blockers.append(f"{prefix}_JOB_RUN_SOURCE_RUN_MISMATCH")

    if coverage is None:
        blockers.append(f"{prefix}_COVERAGE_MISSING")
    else:
        if expected_source_run_id and coverage.get("source_run_id") != expected_source_run_id:
            blockers.append(f"{prefix}_COVERAGE_SOURCE_RUN_MISMATCH")
        if coverage.get("completeness_status") != "COMPLETE":
            blockers.append(f"{prefix}_COVERAGE_NOT_COMPLETE:{coverage.get('completeness_status')}")
        if coverage.get("readiness_status") != "LIVE_ELIGIBLE":
            blockers.append(f"{prefix}_COVERAGE_NOT_LIVE_ELIGIBLE:{coverage.get('readiness_status')}")
        if not _not_expired(coverage.get("expires_at"), now_utc):
            blockers.append(f"{prefix}_COVERAGE_EXPIRED_OR_INVALID")

    if readiness is None:
        blockers.append(f"{prefix}_READINESS_MISSING")
    else:
        if expected_source_run_id and readiness.get("source_run_id") != expected_source_run_id:
            blockers.append(f"{prefix}_READINESS_SOURCE_RUN_MISMATCH")
        dependency = _json_obj(readiness.get("dependency_json"))
        if dependency.get("source_run_id") != expected_source_run_id:
            blockers.append(f"{prefix}_READINESS_DEPENDENCY_MISMATCH")
        if readiness.get("status") != "LIVE_ELIGIBLE":
            blockers.append(f"{prefix}_READINESS_NOT_LIVE_ELIGIBLE:{readiness.get('status')}")
        if not _not_expired(readiness.get("expires_at"), now_utc):
            blockers.append(f"{prefix}_READINESS_EXPIRED_OR_INVALID")

    return TrackReport(
        label=config.label,
        ready=not blockers,
        blockers=blockers,
        job_run=job_run,
        source_run=source_run,
        coverage=coverage,
        readiness=readiness,
    )


def _source_health_check(
    source_health_path: Path,
    *,
    source_id: str,
    now_utc: datetime,
    max_age_seconds: int,
) -> tuple[bool, list[str], CheckResult]:
    if not source_health_path.exists():
        return False, ["SOURCE_HEALTH_MISSING"], CheckResult(
            "source_health",
            "BLOCKED",
            "source_health.json missing",
            {"path": str(source_health_path)},
        )

    try:
        payload = json.loads(source_health_path.read_text())
    except Exception as exc:
        return False, ["SOURCE_HEALTH_UNREADABLE"], CheckResult(
            "source_health",
            "BLOCKED",
            f"source_health.json unreadable: {exc}",
            {"path": str(source_health_path)},
        )

    blockers: list[str] = []
    written_at = _parse_utc(payload.get("written_at"), field="written_at")
    if written_at is None:
        blockers.append("SOURCE_HEALTH_WRITTEN_AT_INVALID")
    else:
        age = (now_utc - written_at).total_seconds()
        if age > max_age_seconds:
            blockers.append("SOURCE_HEALTH_STALE")

    source = (payload.get("sources") or {}).get(source_id)
    if not isinstance(source, dict):
        blockers.append("SOURCE_HEALTH_SOURCE_MISSING")
    else:
        error = source.get("error")
        if isinstance(error, str) and error:
            normalized = error.upper()
            if "429" in normalized or "THROTTLED" in normalized:
                blockers.append("SOURCE_HEALTH_THROTTLED_HTTP_429")
            else:
                blockers.append("SOURCE_HEALTH_ERROR")
        if source.get("last_success_at") is None and source.get("consecutive_failures"):
            blockers.append("SOURCE_HEALTH_NO_SUCCESS_WITH_FAILURES")

    return not blockers, blockers, CheckResult(
        "source_health",
        "PASS" if not blockers else "BLOCKED",
        "source health allows forecast-live readiness" if not blockers else ", ".join(blockers),
        {"path": str(source_health_path), "source_id": source_id},
    )


def _process_check(process_pattern: str) -> tuple[bool, list[str], CheckResult]:
    completed = subprocess.run(
        ["pgrep", "-af", process_pattern],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode == 0 and completed.stdout.strip():
        return True, [], CheckResult(
            "forecast_live_process",
            "PASS",
            "forecast-live process matched",
            {"pattern": process_pattern, "matches": completed.stdout.strip().splitlines()},
        )
    return False, ["FORECAST_LIVE_PROCESS_MISSING"], CheckResult(
        "forecast_live_process",
        "BLOCKED",
        "forecast-live process missing",
        {"pattern": process_pattern, "stderr": completed.stderr.strip()},
    )


def _heartbeat_check(heartbeat_path: Path, now_utc: datetime, max_age_seconds: int) -> tuple[bool, list[str], CheckResult]:
    if not heartbeat_path.exists():
        return False, ["FORECAST_LIVE_HEARTBEAT_MISSING"], CheckResult(
            "forecast_live_heartbeat",
            "BLOCKED",
            "forecast-live heartbeat missing",
            {"path": str(heartbeat_path)},
        )
    try:
        payload = json.loads(heartbeat_path.read_text())
    except Exception as exc:
        return False, ["FORECAST_LIVE_HEARTBEAT_UNREADABLE"], CheckResult(
            "forecast_live_heartbeat",
            "BLOCKED",
            f"forecast-live heartbeat unreadable: {exc}",
            {"path": str(heartbeat_path)},
        )
    ts = _parse_utc(payload.get("timestamp") or payload.get("written_at"), field="timestamp")
    if ts is None:
        return False, ["FORECAST_LIVE_HEARTBEAT_TIMESTAMP_INVALID"], CheckResult(
            "forecast_live_heartbeat",
            "BLOCKED",
            "forecast-live heartbeat timestamp invalid",
            {"path": str(heartbeat_path)},
        )
    age = (now_utc - ts).total_seconds()
    if age > max_age_seconds:
        return False, ["FORECAST_LIVE_HEARTBEAT_STALE"], CheckResult(
            "forecast_live_heartbeat",
            "BLOCKED",
            "forecast-live heartbeat stale",
            {"path": str(heartbeat_path), "age_seconds": age},
        )
    return True, [], CheckResult(
        "forecast_live_heartbeat",
        "PASS",
        "forecast-live heartbeat fresh",
        {"path": str(heartbeat_path), "age_seconds": age},
    )


def _required_tables_present(conn: sqlite3.Connection) -> tuple[bool, list[str], CheckResult]:
    required = ("job_run", "source_run", "source_run_coverage", "readiness_state")
    missing = [table for table in required if not _table_exists(conn, table)]
    if missing:
        return False, [f"TABLE_MISSING:{table}" for table in missing], CheckResult(
            "forecast_schema",
            "BLOCKED",
            "required forecast authority tables missing",
            {"missing": missing},
        )
    return True, [], CheckResult(
        "forecast_schema",
        "PASS",
        "required forecast authority tables exist",
        {"tables": list(required)},
    )


def evaluate_forecast_live_ready(
    *,
    forecasts_db_path: Path,
    source_health_path: Path,
    now_utc: datetime | None = None,
    claim_mode: str = "post-launch",
    require_process: bool = True,
    require_heartbeat: bool = True,
    process_pattern: str = "python -m src.ingest.forecast_live_daemon",
    heartbeat_path: Path = DEFAULT_HEARTBEAT_PATH,
    source_health_max_age_seconds: int = 900,
    heartbeat_max_age_seconds: int = 90,
) -> ForecastLiveReadyReport:
    now_value = (now_utc or _now()).astimezone(timezone.utc)
    blockers: list[str] = []
    checks: list[CheckResult] = []
    tracks: list[TrackReport] = []

    try:
        conn = _connect_read_only(forecasts_db_path)
    except Exception as exc:
        blocker = "FORECASTS_DB_UNREADABLE"
        return ForecastLiveReadyReport(
            generated_at=now_value.isoformat(),
            claim_mode=claim_mode,
            db_path=str(forecasts_db_path),
            source_id=SOURCE_ID,
            highest_completion_state=CompletionState.CODE_READY_ON_HEAD.value,
            producer_ready=False,
            runtime_ready=False,
            live_claim_authorized=False,
            blockers=[blocker],
            checks=[CheckResult("forecasts_db", "BLOCKED", str(exc), {"path": str(forecasts_db_path)})],
            tracks=[],
        )

    try:
        schema_ok, schema_blockers, schema_check = _required_tables_present(conn)
        checks.append(schema_check)
        blockers.extend(schema_blockers)

        if schema_ok:
            for config in TRACKS:
                track_report = _evaluate_track(conn, config, now_value)
                tracks.append(track_report)
                blockers.extend(track_report.blockers)
    finally:
        conn.close()

    source_health_ok, source_health_blockers, source_health = _source_health_check(
        source_health_path,
        source_id=SOURCE_ID,
        now_utc=now_value,
        max_age_seconds=source_health_max_age_seconds,
    )
    checks.append(source_health)
    blockers.extend(source_health_blockers)

    runtime_blockers: list[str] = []
    runtime_checks: list[CheckResult] = []
    process_ok = True
    heartbeat_ok = True
    if require_process:
        process_ok, process_blockers, process_result = _process_check(process_pattern)
        runtime_blockers.extend(process_blockers)
        runtime_checks.append(process_result)
    if require_heartbeat:
        heartbeat_ok, heartbeat_blockers, heartbeat_result = _heartbeat_check(
            heartbeat_path,
            now_value,
            heartbeat_max_age_seconds,
        )
        runtime_blockers.extend(heartbeat_blockers)
        runtime_checks.append(heartbeat_result)
    if not require_process and not require_heartbeat:
        runtime_checks.append(CheckResult(
            "runtime_evidence",
            "SKIPPED",
            "runtime process and heartbeat evidence not required for this staged/read-only probe",
            {},
        ))
    checks.extend(runtime_checks)
    blockers.extend(runtime_blockers)

    data_blockers = [
        blocker
        for blocker in blockers
        if not blocker.startswith("FORECAST_LIVE_PROCESS_")
        and not blocker.startswith("FORECAST_LIVE_HEARTBEAT_")
    ]
    producer_ready = not data_blockers and len(tracks) == len(TRACKS) and all(track.ready for track in tracks)
    runtime_required = require_process or require_heartbeat
    runtime_ready = runtime_required and process_ok and heartbeat_ok

    highest = CompletionState.CODE_READY_ON_HEAD
    if claim_mode == "staged":
        if producer_ready:
            highest = CompletionState.PRODUCER_READY
    else:
        if runtime_ready:
            highest = CompletionState.LIVE_RUNNING
        if runtime_ready and producer_ready:
            highest = CompletionState.PRODUCER_READY

    return ForecastLiveReadyReport(
        generated_at=now_value.isoformat(),
        claim_mode=claim_mode,
        db_path=str(forecasts_db_path),
        source_id=SOURCE_ID,
        highest_completion_state=highest.value,
        producer_ready=producer_ready,
        runtime_ready=runtime_ready,
        live_claim_authorized=False,
        blockers=blockers,
        checks=checks,
        tracks=tracks,
    )


def _summary_lines(report: ForecastLiveReadyReport) -> Iterable[str]:
    yield f"highest_completion_state={report.highest_completion_state}"
    yield f"producer_ready={str(report.producer_ready).lower()}"
    yield f"runtime_ready={str(report.runtime_ready).lower()}"
    if report.blockers:
        yield "blockers:"
        for blocker in report.blockers:
            yield f"- {blocker}"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forecasts-db", type=Path, default=ZEUS_FORECASTS_DB_PATH)
    parser.add_argument("--source-health", type=Path, default=DEFAULT_SOURCE_HEALTH_PATH)
    parser.add_argument("--heartbeat-path", type=Path, default=DEFAULT_HEARTBEAT_PATH)
    parser.add_argument("--claim-mode", choices=("post-launch", "staged"), default="post-launch")
    parser.add_argument("--no-runtime-required", action="store_true")
    parser.add_argument("--process-pattern", default="python -m src.ingest.forecast_live_daemon")
    parser.add_argument("--source-health-max-age-seconds", type=int, default=900)
    parser.add_argument("--heartbeat-max-age-seconds", type=int, default=90)
    parser.add_argument("--now", help="UTC ISO timestamp for deterministic checks")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    if args.no_runtime_required and args.claim_mode != "staged":
        print("--no-runtime-required is only allowed with --claim-mode staged", file=sys.stderr)
        return 2
    now_utc = _parse_utc(args.now, field="now") if args.now else None
    if args.now and now_utc is None:
        print("invalid --now timestamp", file=sys.stderr)
        return 2

    runtime_required = args.claim_mode == "post-launch" and not args.no_runtime_required
    report = evaluate_forecast_live_ready(
        forecasts_db_path=args.forecasts_db,
        source_health_path=args.source_health,
        now_utc=now_utc,
        claim_mode=args.claim_mode,
        require_process=runtime_required,
        require_heartbeat=runtime_required,
        process_pattern=args.process_pattern,
        heartbeat_path=args.heartbeat_path,
        source_health_max_age_seconds=args.source_health_max_age_seconds,
        heartbeat_max_age_seconds=args.heartbeat_max_age_seconds,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True, default=str))
    else:
        print("\n".join(_summary_lines(report)))
    return 0 if not report.blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
