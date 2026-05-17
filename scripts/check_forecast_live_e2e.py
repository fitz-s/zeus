#!/usr/bin/env python3
# Lifecycle: created=2026-05-14; last_reviewed=2026-05-17; last_reused=never
# Purpose: Timed E2E smoke against isolated temp DBs; distinguishes code readiness from runtime readiness.
# Reuse: When operators need per-stage timing evidence for forecast-live producer chain before live cutover.
"""Temp-only forecast-live E2E smoke.

This diagnostic does not fetch ECMWF, start a daemon, touch launchctl, write
repo state DBs, place orders, or authorize live trading. It exercises the
forecast-live producer chain against isolated temp DBs and reports exact
per-stage timings so live cutover work can distinguish code readiness from
runtime readiness.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_forecast_live_ready import evaluate_forecast_live_ready
from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
)
from src.data.executable_forecast_reader import read_executable_forecast
from src.data.forecast_target_contract import build_forecast_target_scope
from src.data.producer_readiness import PRODUCER_READINESS_STRATEGY_KEY
from src.ingest import forecast_live_daemon
from src.state.db import (
    ZEUS_FORECASTS_DB_PATH,
    ZEUS_WORLD_DB_PATH,
    get_connection,
    init_schema,
    init_schema_forecasts,
)
from src.state.readiness_repo import write_readiness_state
from src.state.schema.v2_schema import apply_v2_schema

UTC = timezone.utc
NOW = datetime(2026, 5, 14, 9, 30, tzinfo=UTC)
SOURCE_CYCLE = datetime(2026, 5, 14, 0, 0, tzinfo=UTC)
TARGET_LOCAL_DATE = date(2026, 5, 15)
CITY_ID = "LONDON"
CITY_NAME = "London"
CITY_TIMEZONE = "Europe/London"
SOURCE_ID = "ecmwf_open_data"
SOURCE_TRANSPORT = "ensemble_snapshots_v2_db_reader"


@dataclass(frozen=True)
class TrackSmokeConfig:
    label: str
    raw_track: str
    forecast_track: str
    extract_subdir: str
    data_version: str
    temperature_metric: str
    physical_quantity: str
    observation_field: str
    param: str
    param_id: int
    step_type: str
    member_base: float
    condition_id: str
    market_family: str


TRACKS: tuple[TrackSmokeConfig, ...] = (
    TrackSmokeConfig(
        label="HIGH",
        raw_track="mx2t6_high",
        forecast_track="mx2t6_high_full_horizon",
        extract_subdir="open_ens_mx2t6_localday_max",
        data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
        temperature_metric="high",
        physical_quantity="mx2t3_local_calendar_day_max",
        observation_field="high_temp",
        param="mx2t3",
        param_id=121,
        step_type="max",
        member_base=5.0,
        condition_id="condition-smoke-high",
        market_family="forecast-live-smoke-high",
    ),
    TrackSmokeConfig(
        label="LOW",
        raw_track="mn2t6_low",
        forecast_track="mn2t6_low_full_horizon",
        extract_subdir="open_ens_mn2t6_localday_min",
        data_version=ECMWF_OPENDATA_LOW_DATA_VERSION,
        temperature_metric="low",
        physical_quantity="mn2t3_local_calendar_day_min",
        observation_field="low_temp",
        param="mn2t3",
        param_id=122,
        step_type="min",
        member_base=-10.0,
        condition_id="condition-smoke-low",
        market_family="forecast-live-smoke-low",
    ),
)


@dataclass(frozen=True)
class TrackSmokeResult:
    label: str
    reader_ok: bool
    reason_code: str
    n_members: int
    members_match: bool
    members_max_abs_error: float
    source_run_id: str | None
    coverage_id: str | None
    producer_readiness_id: str | None
    entry_readiness_id: str | None


@dataclass(frozen=True)
class SmokeReport:
    status: str
    generated_at: str
    work_dir: str
    artifacts_kept: bool
    external_fetch: bool
    production_db_write: bool
    timings_seconds: dict[str, float]
    daemon_results: dict[str, dict[str, Any]]
    verifier: dict[str, Any]
    tracks: list[TrackSmokeResult]
    blockers: list[str]


def _seconds(start: float, end: float) -> float:
    return round(end - start, 6)


def _reject_production_work_dir(work_dir: Path) -> None:
    resolved = work_dir.resolve()
    state_dir = (ROOT / "state").resolve()
    if resolved == state_dir or state_dir in resolved.parents:
        raise ValueError(f"refusing to write forecast-live smoke artifacts under repo state/: {resolved}")


def _members(config: TrackSmokeConfig) -> list[float]:
    return [config.member_base + (idx * 0.5) for idx in range(51)]


def _scope(config: TrackSmokeConfig):
    return build_forecast_target_scope(
        city_id=CITY_ID,
        city_name=CITY_NAME,
        city_timezone=CITY_TIMEZONE,
        target_local_date=TARGET_LOCAL_DATE,
        temperature_metric=config.temperature_metric,
        source_cycle_time=SOURCE_CYCLE,
        data_version=config.data_version,
        market_refs=(config.condition_id,),
    )


def _payload(config: TrackSmokeConfig) -> dict[str, Any]:
    scope = _scope(config)
    return {
        "generated_at": NOW.isoformat(),
        "data_version": config.data_version,
        "temperature_metric": config.temperature_metric,
        "physical_quantity": config.physical_quantity,
        "param": config.param,
        "paramId": config.param_id,
        "short_name": config.param,
        "step_type": config.step_type,
        "aggregation_window_hours": 3,
        "city": CITY_NAME,
        "lat": 51.4775,
        "lon": -0.4614,
        "unit": "C",
        "manifest_sha256": (config.label[0].lower() * 64),
        "manifest_hash": (config.label[0].lower() * 64),
        "issue_time_utc": SOURCE_CYCLE.isoformat(),
        "target_date_local": TARGET_LOCAL_DATE.isoformat(),
        "lead_day": 1,
        "lead_day_anchor": "issue_utc.date()",
        "timezone": CITY_TIMEZONE,
        "local_day_window": {
            "start": scope.target_window_start_utc.isoformat(),
            "end": scope.target_window_end_utc.isoformat(),
        },
        "local_day_start_utc": scope.target_window_start_utc.isoformat(),
        "local_day_end_utc": scope.target_window_end_utc.isoformat(),
        "step_horizon_hours": 144.0,
        "step_horizon_deficit_hours": 0.0,
        "causality": {"status": "OK"},
        "boundary_ambiguous": False,
        "nearest_grid_lat": 51.5,
        "nearest_grid_lon": -0.5,
        "nearest_grid_distance_km": 5.0,
        "selected_step_ranges": ["24-30", "30-36", "36-42", "42-48"],
        "member_count": 51,
        "missing_members": [],
        "training_allowed": True,
        "members": [
            {"member": idx, "value_native_unit": value}
            for idx, value in enumerate(_members(config))
        ],
    }


def _write_payloads(fifty_one_root: Path) -> None:
    for config in TRACKS:
        json_dir = fifty_one_root / "raw" / config.extract_subdir / "london" / "20260514"
        json_dir.mkdir(parents=True, exist_ok=True)
        json_path = (
            json_dir
            / f"{config.extract_subdir}_target_{TARGET_LOCAL_DATE.isoformat()}_lead_1.json"
        )
        json_path.write_text(json.dumps(_payload(config), sort_keys=True), encoding="utf-8")


def _write_source_health(path: Path) -> None:
    payload = {
        "written_at": NOW.isoformat(),
        "sources": {
            SOURCE_ID: {
                "last_success_at": NOW.isoformat(),
                "last_failure_at": None,
                "consecutive_failures": 0,
                "degraded_since": None,
                "latency_ms": 0,
                "error": None,
            }
        },
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_entry_readiness(trade_conn, forecasts_conn) -> None:
    for config in TRACKS:
        scope = _scope(config)
        producer = forecasts_conn.execute(
            """
            SELECT *
            FROM readiness_state
            WHERE strategy_key = ?
              AND city_id = ?
              AND target_local_date = ?
              AND temperature_metric = ?
              AND source_id = ?
              AND track = ?
            ORDER BY computed_at DESC, recorded_at DESC
            LIMIT 1
            """,
            (
                PRODUCER_READINESS_STRATEGY_KEY,
                scope.city_id,
                scope.target_local_date.isoformat(),
                scope.temperature_metric,
                SOURCE_ID,
                config.forecast_track,
            ),
        ).fetchone()
        if producer is None:
            raise RuntimeError(f"producer readiness missing for {config.label}")
        dependency = json.loads(producer["dependency_json"])
        write_readiness_state(
            trade_conn,
            readiness_id=f"entry-readiness-smoke-{config.label.lower()}",
            scope_type="city_metric",
            status="LIVE_ELIGIBLE",
            computed_at=NOW + timedelta(minutes=1),
            expires_at=NOW + timedelta(hours=2),
            city_id=scope.city_id,
            city=scope.city_name,
            city_timezone=scope.city_timezone,
            target_local_date=scope.target_local_date,
            temperature_metric=scope.temperature_metric,
            physical_quantity=config.physical_quantity,
            observation_field=config.observation_field,
            data_version=config.data_version,
            source_id=SOURCE_ID,
            track=config.forecast_track,
            source_run_id=producer["source_run_id"],
            strategy_key="entry_forecast",
            market_family=config.market_family,
            condition_id=config.condition_id,
            reason_codes_json=["ENTRY_READY"],
            dependency_json={
                "producer_readiness_id": producer["readiness_id"],
                "coverage_id": dependency.get("coverage_id"),
            },
            provenance_json={"smoke": "forecast_live_e2e"},
        )


def _read_live_results(trade_db: Path, forecasts_db: Path) -> list[TrackSmokeResult]:
    trade_conn = get_connection(trade_db, write_class="live")
    try:
        trade_conn.execute("ATTACH DATABASE ? AS forecasts", (str(forecasts_db),))
        results: list[TrackSmokeResult] = []
        for config in TRACKS:
            scope = _scope(config)
            result = read_executable_forecast(
                trade_conn,
                city_id=scope.city_id,
                city_name=scope.city_name,
                city_timezone=scope.city_timezone,
                target_local_date=scope.target_local_date,
                temperature_metric=scope.temperature_metric,
                source_id=SOURCE_ID,
                source_transport=SOURCE_TRANSPORT,
                data_version=config.data_version,
                track=config.forecast_track,
                strategy_key="entry_forecast",
                market_family=config.market_family,
                condition_id=config.condition_id,
                decision_time=NOW + timedelta(minutes=2),
            )
            expected_members = _members(config)
            actual_members: list[float] = []
            evidence = None
            if result.bundle is not None:
                actual_members = list(result.bundle.snapshot.members)
                evidence = result.bundle.evidence
            max_abs_error = (
                max(abs(a - b) for a, b in zip(actual_members, expected_members))
                if len(actual_members) == len(expected_members)
                else float("inf")
            )
            results.append(
                TrackSmokeResult(
                    label=config.label,
                    reader_ok=result.ok,
                    reason_code=result.reason_code,
                    n_members=len(actual_members),
                    members_match=len(actual_members) == 51 and max_abs_error == 0.0,
                    members_max_abs_error=max_abs_error,
                    source_run_id=evidence.source_run_id if evidence is not None else None,
                    coverage_id=evidence.coverage_id if evidence is not None else None,
                    producer_readiness_id=evidence.producer_readiness_id if evidence is not None else None,
                    entry_readiness_id=evidence.entry_readiness_id if evidence is not None else None,
                )
            )
        return results
    finally:
        trade_conn.close()


def _run_in_work_dir(work_dir: Path, *, keep_artifacts: bool) -> SmokeReport:
    _reject_production_work_dir(work_dir)
    if work_dir.exists() and any(work_dir.iterdir()):
        raise ValueError(f"forecast-live smoke work dir must be empty: {work_dir}")
    work_dir.mkdir(parents=True, exist_ok=True)
    forecasts_db = work_dir / "zeus-forecasts-smoke.db"
    trade_db = work_dir / "zeus-trade-smoke.db"
    source_health = work_dir / "source_health.json"
    locks_dir = work_dir / "locks"
    fifty_one_root = work_dir / "51 source data"
    for db_path in (forecasts_db, trade_db):
        if db_path.resolve() in {ZEUS_FORECASTS_DB_PATH.resolve(), ZEUS_WORLD_DB_PATH.resolve()}:
            raise ValueError(f"refusing to write production DB path: {db_path}")

    timings: dict[str, float] = {}
    total_start = time.perf_counter()

    stage_start = time.perf_counter()
    _write_payloads(fifty_one_root)
    _write_source_health(source_health)
    timings["payload_prepare"] = _seconds(stage_start, time.perf_counter())

    stage_start = time.perf_counter()
    forecasts_conn = get_connection(forecasts_db, write_class="bulk")
    trade_conn = get_connection(trade_db, write_class="live")
    try:
        init_schema_forecasts(forecasts_conn)
        init_schema(trade_conn)
        apply_v2_schema(trade_conn)
        timings["schema_init"] = _seconds(stage_start, time.perf_counter())

        original_root = None
        daemon_results: dict[str, dict[str, Any]] = {}
        stage_start = time.perf_counter()
        from src.data import ecmwf_open_data

        original_root = ecmwf_open_data.FIFTY_ONE_ROOT
        ecmwf_open_data.FIFTY_ONE_ROOT = fifty_one_root
        try:
            for config in TRACKS:
                def _collector(*, track: str, _config: TrackSmokeConfig = config) -> dict[str, Any]:
                    return ecmwf_open_data.collect_open_ens_cycle(
                        track=track,
                        run_date=SOURCE_CYCLE.date(),
                        run_hour=SOURCE_CYCLE.hour,
                        skip_download=True,
                        skip_extract=True,
                        conn=forecasts_conn,
                        now_utc=NOW,
                    )

                daemon_results[config.label] = forecast_live_daemon.run_opendata_track(
                    config.raw_track,
                    _locks_dir_override=locks_dir,
                    _collector=_collector,
                    _source_paused=lambda _source_id: False,
                    _job_conn=forecasts_conn,
                    _now_utc=NOW,
                )
        finally:
            if original_root is not None:
                ecmwf_open_data.FIFTY_ONE_ROOT = original_root
        forecasts_conn.commit()
        timings["daemon_collect_and_journal"] = _seconds(stage_start, time.perf_counter())

        stage_start = time.perf_counter()
        _write_entry_readiness(trade_conn, forecasts_conn)
        trade_conn.commit()
        timings["entry_readiness"] = _seconds(stage_start, time.perf_counter())
    finally:
        forecasts_conn.close()
        trade_conn.close()

    stage_start = time.perf_counter()
    verifier_report = evaluate_forecast_live_ready(
        forecasts_db_path=forecasts_db,
        source_health_path=source_health,
        now_utc=NOW,
        claim_mode="staged",
        require_process=False,
        require_heartbeat=False,
    )
    timings["verifier"] = _seconds(stage_start, time.perf_counter())

    stage_start = time.perf_counter()
    track_results = _read_live_results(trade_db, forecasts_db)
    timings["live_reader"] = _seconds(stage_start, time.perf_counter())
    timings["total"] = _seconds(total_start, time.perf_counter())

    blockers = list(verifier_report.blockers)
    for result in track_results:
        if not result.reader_ok:
            blockers.append(f"{result.label}_LIVE_READER_BLOCKED:{result.reason_code}")
        if result.n_members != 51:
            blockers.append(f"{result.label}_MEMBER_COUNT:{result.n_members}")
        if not result.members_match:
            blockers.append(f"{result.label}_MEMBER_PRECISION_LOSS:{result.members_max_abs_error}")

    return SmokeReport(
        status="PASS" if not blockers else "BLOCKED",
        generated_at=datetime.now(UTC).isoformat(),
        work_dir=str(work_dir),
        artifacts_kept=keep_artifacts,
        external_fetch=False,
        production_db_write=False,
        timings_seconds=timings,
        daemon_results=daemon_results,
        verifier={
            "highest_completion_state": verifier_report.highest_completion_state,
            "producer_ready": verifier_report.producer_ready,
            "runtime_ready": verifier_report.runtime_ready,
            "blockers": verifier_report.blockers,
        },
        tracks=track_results,
        blockers=blockers,
    )


def run_smoke(*, work_dir: Path | None = None, keep_artifacts: bool = False) -> SmokeReport:
    if work_dir is not None:
        return _run_in_work_dir(work_dir, keep_artifacts=keep_artifacts)
    with tempfile.TemporaryDirectory(prefix="zeus-forecast-live-smoke-") as tmp:
        return _run_in_work_dir(Path(tmp), keep_artifacts=keep_artifacts)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, help="Optional non-state directory for smoke artifacts")
    parser.add_argument("--keep-artifacts", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        report = run_smoke(work_dir=args.work_dir, keep_artifacts=args.keep_artifacts or bool(args.work_dir))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    payload = asdict(report)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(f"status={report.status}")
        print(f"total_seconds={report.timings_seconds['total']:.6f}")
        for name, seconds in report.timings_seconds.items():
            if name != "total":
                print(f"{name}_seconds={seconds:.6f}")
        if report.blockers:
            print("blockers:")
            for blocker in report.blockers:
                print(f"- {blocker}")
    return 0 if report.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
