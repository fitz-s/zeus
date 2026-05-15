#!/usr/bin/env python3
# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_data_pipeline_live_rootfix/DATA_PIPELINE_ROOTFIX_PLAN.md
"""Live data-pipeline end-to-end diagnostic.

This checker is intentionally live-only: it reads the active process table and
the runtime DB files used by Zeus. It never writes DB truth and never performs
venue actions.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import runtime_cities_by_name
from src.data.executable_forecast_reader import read_executable_forecast

TRADE_DB_NAME = "zeus_trades.db"
WORLD_DB_NAME = "zeus-world.db"
FORECASTS_DB_NAME = "zeus-forecasts.db"
FORECAST_LIVE_OWNER_ENV = "ZEUS_FORECAST_LIVE_OWNER"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _connect_live_readonly(*, trade_db: Path, world_db: Path, forecasts_db: Path) -> sqlite3.Connection:
    trade_uri = f"file:{trade_db.resolve()}?mode=ro"
    conn = sqlite3.connect(trade_uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.execute(f"ATTACH DATABASE 'file:{world_db.resolve()}?mode=ro' AS world")
    conn.execute(f"ATTACH DATABASE 'file:{forecasts_db.resolve()}?mode=ro' AS forecasts")
    return conn


def _fetch_all_dicts(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _fetch_one_dict(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def _table_exists(conn: sqlite3.Connection, schema: str, table: str) -> bool:
    row = conn.execute(
        f"SELECT 1 FROM {schema}.sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, schema: str, table: str) -> set[str]:
    if not _table_exists(conn, schema, table):
        return set()
    return {str(row["name"]) for row in conn.execute(f"PRAGMA {schema}.table_info({table})").fetchall()}


def _table_stats(conn: sqlite3.Connection, latest_source_run_id: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for schema in ("main", "world", "forecasts"):
        for table in ("ensemble_snapshots_v2", "source_run", "source_run_coverage", "readiness_state"):
            item: dict[str, Any] = {"schema": schema, "table": table, "exists": _table_exists(conn, schema, table)}
            if not item["exists"]:
                out.append(item)
                continue
            item["total_rows"] = conn.execute(f"SELECT COUNT(*) AS n FROM {schema}.{table}").fetchone()["n"]
            columns = _table_columns(conn, schema, table)
            if latest_source_run_id and "source_run_id" in columns:
                item["latest_source_run_rows"] = conn.execute(
                    f"SELECT COUNT(*) AS n FROM {schema}.{table} WHERE source_run_id = ?",
                    (latest_source_run_id,),
                ).fetchone()["n"]
            if table == "readiness_state" and "computed_at" in columns:
                item["max_computed_at"] = conn.execute(
                    f"SELECT MAX(computed_at) AS v FROM {schema}.{table}"
                ).fetchone()["v"]
            out.append(item)
    return out


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def _argv(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _has_module_launch(command: str, module: str) -> bool:
    argv = _argv(command)
    return any(token == "-m" and idx + 1 < len(argv) and argv[idx + 1] == module for idx, token in enumerate(argv))


def _is_test_or_checker_command(command: str) -> bool:
    argv = _argv(command)
    return any(
        token.endswith("pytest")
        or token == "pytest"
        or token.endswith("tests/test_forecast_live_daemon.py")
        or token.endswith("tests/test_check_data_pipeline_live_e2e.py")
        or token.endswith("scripts/check_data_pipeline_live_e2e.py")
        for token in argv
    )


def _is_forecast_live_owner_command(command: str) -> bool:
    if _is_test_or_checker_command(command):
        return False
    argv = _argv(command)
    return _has_module_launch(command, "src.ingest.forecast_live_daemon") or any(
        token.endswith("src/ingest/forecast_live_daemon.py") for token in argv
    )


def _is_legacy_ingest_owner_command(command: str) -> bool:
    return _has_module_launch(command, "src.ingest_main")


def _selected_process_env(pid: int) -> dict[str, str]:
    proc = _run(["ps", "eww", "-p", str(pid), "-o", "command="])
    if proc.returncode != 0:
        return {}
    try:
        tokens = shlex.split(proc.stdout.strip())
    except ValueError:
        tokens = proc.stdout.strip().split()
    selected: dict[str, str] = {}
    for token in tokens:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if key == FORECAST_LIVE_OWNER_ENV:
            selected[key] = value
    return selected


def _legacy_ingest_process_owns_opendata(proc: dict[str, Any]) -> bool:
    command = str(proc.get("command"))
    if not _is_legacy_ingest_owner_command(command):
        return False
    env = proc.get("env") if isinstance(proc.get("env"), dict) else {}
    owner = str(env.get(FORECAST_LIVE_OWNER_ENV, "ingest_main")).strip().lower() or "ingest_main"
    return owner != "forecast_live"


def _is_live_main_command(command: str) -> bool:
    return _has_module_launch(command, "src.main")


def _is_tracked_live_process_command(command: str) -> bool:
    return (
        _is_live_main_command(command)
        or _is_legacy_ingest_owner_command(command)
        or _is_forecast_live_owner_command(command)
    )


def _process_rows() -> list[dict[str, Any]]:
    proc = _run(["ps", "-axo", "pid=,ppid=,etime=,command="])
    rows: list[dict[str, Any]] = []
    if proc.returncode != 0:
        return [{"error": proc.stderr.strip() or "ps failed"}]
    for line in proc.stdout.splitlines():
        raw = line.strip()
        if not raw:
            continue
        parts = raw.split(None, 3)
        if len(parts) < 4:
            continue
        pid, ppid, etime, command = parts
        if not _is_tracked_live_process_command(command):
            continue
        rows.append(
            {
                "pid": int(pid),
                "ppid": int(ppid),
                "etime": etime,
                "command": command,
                "env": _selected_process_env(int(pid)),
                "cwd": _process_cwd(int(pid)),
                "open_dbs": _process_open_dbs(int(pid)),
            }
        )
    return rows


def _live_root(processes: list[dict[str, Any]], override: str | None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    predicates = (
        _is_live_main_command,
        _is_forecast_live_owner_command,
        _is_legacy_ingest_owner_command,
    )
    for predicate in predicates:
        for proc in processes:
            if predicate(str(proc.get("command"))) and proc.get("cwd"):
                return Path(str(proc["cwd"])).resolve()
    return ROOT


def _process_cwd(pid: int) -> str | None:
    proc = _run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        if line.startswith("n"):
            return line[1:]
    return None


def _process_open_dbs(pid: int) -> list[str]:
    proc = _run(["lsof", "-p", str(pid)])
    if proc.returncode != 0:
        return []
    hits: list[str] = []
    for line in proc.stdout.splitlines():
        if any(name in line for name in ("zeus_trades.db", "zeus-world.db", "zeus-forecasts.db")):
            hits.append(line.split()[-1])
    return sorted(set(hits))


def _latest_source_run(conn: sqlite3.Connection) -> dict[str, Any] | None:
    return _fetch_one_dict(
        conn,
        """
        SELECT *
        FROM forecasts.source_run
        WHERE source_id = 'ecmwf_open_data'
        ORDER BY captured_at DESC, source_cycle_time DESC
        LIMIT 1
        """,
    )


def _source_run_by_id(conn: sqlite3.Connection, source_run_id: str) -> dict[str, Any] | None:
    return _fetch_one_dict(
        conn,
        """
        SELECT *
        FROM forecasts.source_run
        WHERE source_run_id = ?
        """,
        (source_run_id,),
    )


def _target_range(conn: sqlite3.Connection, source_run_id: str) -> dict[str, Any]:
    return _fetch_one_dict(
        conn,
        """
        SELECT
            MIN(target_date) AS min_target_date,
            MAX(target_date) AS max_target_date,
            COUNT(DISTINCT target_date) AS target_dates,
            COUNT(*) AS rows
        FROM forecasts.ensemble_snapshots_v2
        WHERE source_run_id = ?
        """,
        (source_run_id,),
    ) or {}


def _candidate_snapshot(conn: sqlite3.Connection, source_run_id: str, now_date: date) -> dict[str, Any] | None:
    live_eligible_for_source_run = _fetch_one_dict(
        conn,
        """
        SELECT es.*
        FROM forecasts.source_run_coverage AS cov
        JOIN forecasts.ensemble_snapshots_v2 AS es
          ON es.source_run_id = cov.source_run_id
         AND es.source_id = cov.source_id
         AND es.source_transport = cov.source_transport
         AND es.city = cov.city
         AND es.target_date = cov.target_local_date
         AND es.temperature_metric = cov.temperature_metric
         AND es.data_version = cov.data_version
        WHERE cov.source_run_id = ?
          AND cov.readiness_status = 'LIVE_ELIGIBLE'
          AND cov.target_local_date >= ?
        ORDER BY
          CASE WHEN cov.city = 'London' THEN 0 ELSE 1 END,
          cov.target_local_date,
          cov.city,
          es.snapshot_id
        LIMIT 1
        """,
        (source_run_id, now_date.isoformat()),
    )
    if live_eligible_for_source_run is not None:
        return live_eligible_for_source_run

    return _fetch_one_dict(
        conn,
        """
        SELECT es.*
        FROM forecasts.source_run_coverage AS cov
        JOIN forecasts.source_run AS sr
          ON sr.source_run_id = cov.source_run_id
        JOIN forecasts.ensemble_snapshots_v2 AS es
          ON es.source_run_id = cov.source_run_id
         AND es.source_id = cov.source_id
         AND es.source_transport = cov.source_transport
         AND es.city = cov.city
         AND es.target_date = cov.target_local_date
         AND es.temperature_metric = cov.temperature_metric
         AND es.data_version = cov.data_version
        WHERE cov.readiness_status = 'LIVE_ELIGIBLE'
          AND cov.target_local_date >= ?
        ORDER BY
          sr.captured_at DESC,
          sr.source_cycle_time DESC,
          CASE WHEN cov.city = 'London' THEN 0 ELSE 1 END,
          cov.target_local_date,
          cov.city,
          es.snapshot_id
        LIMIT 1
        """,
        (now_date.isoformat(),),
    )


def _reader_probe(conn: sqlite3.Connection, source_run: dict[str, Any], snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if snapshot is None:
        return {"status": "BLOCKED", "reason_code": "NO_CANDIDATE_SNAPSHOT", "ok": False, "elapsed_ms": 0.0}
    cities = runtime_cities_by_name()
    city_name = str(snapshot["city"])
    city = cities.get(city_name)
    if city is None:
        return {"status": "BLOCKED", "reason_code": "CITY_NOT_IN_RUNTIME_CONFIG", "ok": False, "city": city_name, "elapsed_ms": 0.0}
    started = time.perf_counter()
    result = read_executable_forecast(
        conn,
        city_id=city.name.upper().replace(" ", "_"),
        city_name=city.name,
        city_timezone=city.timezone,
        target_local_date=date.fromisoformat(str(snapshot["target_date"])),
        temperature_metric=str(snapshot["temperature_metric"]),
        source_id=str(snapshot["source_id"]),
        source_transport=str(snapshot["source_transport"]),
        data_version=str(snapshot["data_version"]),
        track=str(source_run["track"]),
        strategy_key="entry_forecast",
        market_family="live_e2e_probe",
        condition_id="live_e2e_probe",
        decision_time=_now_utc(),
        require_entry_readiness=False,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "status": result.status,
        "reason_code": result.reason_code,
        "ok": result.ok,
        "elapsed_ms": round(elapsed_ms, 3),
        "candidate": {
            "city": city_name,
            "target_date": snapshot["target_date"],
            "temperature_metric": snapshot["temperature_metric"],
            "data_version": snapshot["data_version"],
            "snapshot_id": snapshot["snapshot_id"],
        },
    }


def _evaluator_cutover_static_guard(live_root: Path) -> dict[str, Any]:
    """Inspect the live checkout's evaluator cutover guard without importing it."""
    evaluator_path = live_root / "src" / "engine" / "evaluator.py"
    try:
        text = evaluator_path.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "reason_code": "EVALUATOR_SOURCE_UNREADABLE",
            "detail": str(exc),
            "path": str(evaluator_path),
        }
    required_markers = {
        "read_executable_forecast(": "reader_call",
        "use_executable_forecast_cutover": "cutover_branch",
        "legacy_entry_primary_fetch_blocked": "legacy_fetch_block_marker",
    }
    missing = [name for marker, name in required_markers.items() if marker not in text]
    return {
        "ok": not missing,
        "reason_code": "OK" if not missing else "EVALUATOR_CUTOVER_MARKERS_MISSING",
        "missing_markers": missing,
        "path": str(evaluator_path),
    }


def _source_cycle_date(source_run: dict[str, Any]) -> date | None:
    raw = source_run.get("source_cycle_time")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)).date()
    except ValueError:
        return None


def _build_checks(
    *,
    processes: list[dict[str, Any]],
    latest_source_run: dict[str, Any] | None,
    target_range: dict[str, Any],
    reader_probe: dict[str, Any],
    evaluator_guard: dict[str, Any],
) -> list[Check]:
    checks: list[Check] = []
    owner_processes = [
        proc for proc in processes
        if _legacy_ingest_process_owns_opendata(proc)
        or _is_forecast_live_owner_command(str(proc.get("command")))
    ]
    dedicated_owners = [proc for proc in owner_processes if _is_forecast_live_owner_command(str(proc.get("command")))]
    legacy_opendata_owners = [proc for proc in owner_processes if _legacy_ingest_process_owns_opendata(proc)]
    live_daemons = [proc for proc in processes if _is_live_main_command(str(proc.get("command")))]
    checks.append(
        Check(
            "live_main_process_present",
            "PASS" if live_daemons else "FAIL",
            f"count={len(live_daemons)}",
        )
    )
    checks.append(
        Check(
            "single_forecast_owner",
            "PASS" if len(owner_processes) == 1 else "FAIL",
            f"count={len(owner_processes)}",
        )
    )
    checks.append(
        Check(
            "dedicated_forecast_owner",
            "PASS" if len(dedicated_owners) == 1 else "FAIL",
            "expected forecast_live_daemon owner, not legacy src.ingest_main",
        )
    )
    checks.append(
        Check(
            "legacy_ingest_opendata_demoted",
            "PASS" if not legacy_opendata_owners else "FAIL",
            f"legacy_opendata_owner_count={len(legacy_opendata_owners)}",
        )
    )
    checks.append(
        Check(
            "latest_source_run_present",
            "PASS" if latest_source_run is not None else "FAIL",
            str(latest_source_run.get("source_run_id")) if latest_source_run else "missing",
        )
    )
    if latest_source_run is not None:
        cycle_date = _source_cycle_date(latest_source_run)
        min_target = target_range.get("min_target_date")
        contaminated = bool(cycle_date and min_target and str(min_target) < cycle_date.isoformat())
        checks.append(
            Check(
                "source_run_target_dates_not_before_cycle",
                "FAIL" if contaminated else "PASS",
                f"source_cycle_date={cycle_date}, min_target_date={min_target}, max_target_date={target_range.get('max_target_date')}",
            )
        )
    checks.append(
        Check(
            "reader_live_ready",
            "PASS" if reader_probe.get("ok") else "FAIL",
            f"{reader_probe.get('status')} / {reader_probe.get('reason_code')} elapsed_ms={reader_probe.get('elapsed_ms')}",
        )
    )
    checks.append(
        Check(
            "evaluator_cutover_static_guard",
            "PASS" if evaluator_guard.get("ok") else "FAIL",
            f"{evaluator_guard.get('reason_code')} path={evaluator_guard.get('path')}",
        )
    )
    return checks


def run_live_check(*, live_root_override: str | None = None) -> tuple[int, dict[str, Any]]:
    started = time.perf_counter()
    processes = _process_rows()
    live_root = _live_root(processes, live_root_override)
    trade_db = live_root / "state" / TRADE_DB_NAME
    world_db = live_root / "state" / WORLD_DB_NAME
    forecasts_db = live_root / "state" / FORECASTS_DB_NAME
    with _connect_live_readonly(trade_db=trade_db, world_db=world_db, forecasts_db=forecasts_db) as conn:
        database_list = [dict(row) for row in conn.execute("PRAGMA database_list").fetchall()]
        latest = _latest_source_run(conn)
        target_range = _target_range(conn, str(latest["source_run_id"])) if latest else {}
        snapshot = _candidate_snapshot(conn, str(latest["source_run_id"]), _now_utc().date()) if latest else None
        reader_source_run = (
            _source_run_by_id(conn, str(snapshot["source_run_id"]))
            if snapshot is not None
            else latest
        )
        reader = (
            _reader_probe(conn, reader_source_run, snapshot)
            if reader_source_run
            else {"status": "BLOCKED", "reason_code": "SOURCE_RUN_MISSING", "ok": False, "elapsed_ms": 0.0}
        )
        stats = _table_stats(conn, str(latest["source_run_id"]) if latest else None)

    evaluator_guard = _evaluator_cutover_static_guard(live_root)
    checks = _build_checks(
        processes=processes,
        latest_source_run=latest,
        target_range=target_range,
        reader_probe=reader,
        evaluator_guard=evaluator_guard,
    )
    ok = all(check.status == "PASS" for check in checks)
    payload = {
        "status": "PASS" if ok else "FAIL",
        "generated_at": _now_utc().isoformat(),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "live_root": str(live_root),
        "live_paths": {
            "trade_db": str(trade_db),
            "world_db": str(world_db),
            "forecasts_db": str(forecasts_db),
        },
        "processes": processes,
        "database_list": database_list,
        "latest_source_run": latest,
        "reader_source_run": reader_source_run,
        "latest_source_run_target_range": target_range,
        "table_stats": stats,
        "reader_probe": reader,
        "evaluator_cutover_static_guard": evaluator_guard,
        "checks": [asdict(check) for check in checks],
    }
    return (0 if ok else 1), payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Required acknowledgement: inspect real live process and runtime DB paths.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    parser.add_argument("--live-root", help="Override live repo root; by default inferred from active live process cwd.")
    args = parser.parse_args(argv)
    if not args.live:
        msg = {
            "status": "ERROR",
            "reason": "LIVE_ACK_REQUIRED",
            "detail": "Pass --live to run against real live process and runtime DB paths.",
        }
        print(json.dumps(msg, indent=2, sort_keys=True))
        return 2
    code, payload = run_live_check(live_root_override=args.live_root)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(f"status={payload['status']} elapsed_ms={payload['elapsed_ms']}")
        for check in payload["checks"]:
            print(f"{check['status']} {check['name']}: {check['detail']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
