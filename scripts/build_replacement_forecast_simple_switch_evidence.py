#!/usr/bin/env python3
"""Build replacement forecast simple-switch evidence JSON from local artifacts."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.replacement_forecast_simple_switch_evidence import (  # noqa: E402
    EVENT_REACTOR_NO_BYPASS_REPORT_PATH,
    FULL_REPLACEMENT_SUITE_REPORT_PATH,
    SHADOW_SCHEMA_DRY_RUN_REPORT_PATH,
    OpenMeteoIfs9EndpointProbeConfig,
    build_replacement_forecast_simple_switch_evidence_report,
    default_openmeteo_probe_run,
)
from scripts.init_replacement_forecast_shadow_schema import initialize_replacement_forecast_shadow_schema  # noqa: E402


def _overrides(path: Path | None) -> dict[str, object] | None:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("override JSON must decode to an object")
    return payload


def _parse_utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _openmeteo_probe_config(args: argparse.Namespace) -> OpenMeteoIfs9EndpointProbeConfig | None:
    if not args.probe_openmeteo:
        return None
    run = _parse_utc_datetime(args.openmeteo_run) if args.openmeteo_run else default_openmeteo_probe_run()
    target_date = (
        date.fromisoformat(args.openmeteo_target_date)
        if args.openmeteo_target_date
        else (run + timedelta(days=1)).date()
    )
    return OpenMeteoIfs9EndpointProbeConfig(
        latitude=args.openmeteo_latitude,
        longitude=args.openmeteo_longitude,
        timezone_name=args.openmeteo_timezone,
        run=run,
        target_local_date=target_date,
        forecast_hours=args.openmeteo_forecast_hours,
        min_hourly_samples=args.openmeteo_min_hourly_samples,
        timeout=args.openmeteo_timeout,
        max_retries=args.openmeteo_max_retries,
    )


def _run_replacement_suite(worktree: Path) -> None:
    requested_patterns = [
        "tests/test_replacement_forecast_*.py",
        "tests/test_openmeteo_ecmwf_ifs9_*.py",
        "tests/test_ecmwf_aifs_*.py",
    ]
    test_files: list[str] = []
    for pattern in requested_patterns:
        test_files.extend(str(path) for path in sorted(worktree.glob(pattern)))
    if not test_files:
        raise RuntimeError("replacement suite test file expansion produced no files")
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        *test_files,
    ]
    result = subprocess.run(
        command,
        cwd=str(worktree),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    report_path = worktree / FULL_REPLACEMENT_SUITE_REPORT_PATH
    report_path.parent.mkdir(parents=True, exist_ok=True)
    summary = ""
    for line in reversed(result.stdout.splitlines()):
        text = line.strip()
        if text:
            summary = text
            break
    report_path.write_text(
        json.dumps(
            {
                "command": command[3:],
                "requested_patterns": requested_patterns,
                "expanded_test_count": len(test_files),
                "returncode": result.returncode,
                "summary": summary,
                "stdout_tail": result.stdout[-4000:],
                "stderr_tail": result.stderr[-4000:],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _run_event_reactor_no_bypass_suite(worktree: Path) -> None:
    test_target = "tests/engine/test_event_reactor_no_bypass.py"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        test_target,
    ]
    result = subprocess.run(
        command,
        cwd=str(worktree),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    report_path = worktree / EVENT_REACTOR_NO_BYPASS_REPORT_PATH
    report_path.parent.mkdir(parents=True, exist_ok=True)
    summary = ""
    for line in reversed(result.stdout.splitlines()):
        text = line.strip()
        if text:
            summary = text
            break
    report_path.write_text(
        json.dumps(
            {
                "command": command[3:],
                "returncode": result.returncode,
                "summary": summary,
                "stdout_tail": result.stdout[-4000:],
                "stderr_tail": result.stderr[-4000:],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _run_shadow_schema_dry_run(*, forecast_db: Path, worktree: Path) -> None:
    report = initialize_replacement_forecast_shadow_schema(forecast_db, commit=False)
    report_path = worktree / SHADOW_SCHEMA_DRY_RUN_REPORT_PATH
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build replacement simple-switch evidence")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--worktree", type=Path, default=ROOT)
    parser.add_argument("--override-json", type=Path, default=None)
    parser.add_argument("--probe-openmeteo", action="store_true")
    parser.add_argument("--openmeteo-latitude", type=float, default=31.2304)
    parser.add_argument("--openmeteo-longitude", type=float, default=121.4737)
    parser.add_argument("--openmeteo-timezone", default="Asia/Shanghai")
    parser.add_argument("--openmeteo-run", default=None)
    parser.add_argument("--openmeteo-target-date", default=None)
    parser.add_argument("--openmeteo-forecast-hours", type=int, default=72)
    parser.add_argument("--openmeteo-min-hourly-samples", type=int, default=20)
    parser.add_argument("--openmeteo-timeout", type=float, default=20.0)
    parser.add_argument("--openmeteo-max-retries", type=int, default=1)
    parser.add_argument("--run-replacement-suite", action="store_true")
    parser.add_argument("--run-event-reactor-no-bypass-suite", action="store_true")
    parser.add_argument("--run-shadow-schema-dry-run", action="store_true")
    parser.add_argument("--forecast-db", type=Path, default=None)
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.run_shadow_schema_dry_run:
            _run_shadow_schema_dry_run(
                forecast_db=args.forecast_db or (args.root / "state" / "zeus-forecasts.db"),
                worktree=args.worktree,
            )
        if args.run_replacement_suite:
            _run_replacement_suite(args.worktree)
        if args.run_event_reactor_no_bypass_suite:
            _run_event_reactor_no_bypass_suite(args.worktree)
        report = build_replacement_forecast_simple_switch_evidence_report(
            root=args.root,
            worktree=args.worktree,
            overrides=_overrides(args.override_json),
            openmeteo_probe=_openmeteo_probe_config(args),
        )
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error_type": exc.__class__.__name__, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    payload = report.as_dict()
    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    if args.stdout:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"{report.status}: {','.join(report.reason_codes)}")
    return 0 if report.complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
