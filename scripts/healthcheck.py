# Lifecycle: created=2026-03-26; last_reviewed=2026-05-16; last_reused=2026-05-16
# Purpose: Operator healthcheck for live daemon, launchd, source truth, entry capability, and settlement freshness.
# Reuse: Run when live health predicates, launchd contracts, or readiness/status summary health fields change.
# Created: 2026-03-26
# Last reused or audited: 2026-05-17
# Authority basis: docs/operations/task_2026-05-14_k1_followups/PLAN.md §4.5 (K1 broken-script remediation); docs/operations/task_2026-05-16_live_continuous_run_package/LIVE_CONTINUOUS_RUN_PACKAGE_PLAN.md Phase C; 2026-05-17 riskguard live DB-holder health contract.
"""Zeus health check for Venus/OpenClaw monitoring.

Reads mode-qualified state written by the running daemon.
Exit code 0 = healthy, 1 = degraded, 2 = dead.
"""

import json
import os
import plistlib
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_mode, state_path
from src.state.db import get_connection, get_forecasts_connection, ZEUS_FORECASTS_DB_PATH
from src.state.decision_chain import query_no_trade_cases

STATUS_STALE_SECONDS = 2 * 3600
RISKGUARD_STALE_SECONDS = 5 * 60
SOURCE_HEALTH_WRITER_STALE_SECONDS = 15 * 60
LIVE_DB_UNKNOWN_HOLDER_SECONDS = 10 * 60
SETTLEMENT_TRUTH_STALE_SECONDS = int(os.environ.get("ZEUS_SETTLEMENT_TRUTH_STALE_SECONDS", str(48 * 3600)))
PROCESS_CODE_STALE_TOLERANCE_SECONDS = 2
PROCESS_CODE_SURFACES = {
    "live_trading": (
        "src/main.py",
        "src/engine/cycle_runner.py",
        "src/control/ws_gap_guard.py",
        "src/execution/command_recovery.py",
        "src/execution/exchange_reconcile.py",
        "src/execution/executor.py",
        "src/execution/harvester_pnl_resolver.py",
    ),
    "data_ingest": (
        "src/ingest_main.py",
        "src/ingest/harvester_truth_writer.py",
        "src/data/source_health_probe.py",
    ),
    "riskguard": ("src/riskguard/riskguard.py",),
    "forecast_live": (
        "src/ingest/forecast_live_daemon.py",
        "src/data/source_health_probe.py",
        "src/data/ecmwf_open_data.py",
    ),
}
STATUS_REQUIRED_KEYS = ("control", "runtime", "execution", "learning", "truth")
STATUS_CONTROL_REQUIRED_KEYS = (
    "recommended_auto_commands",
    "review_required_commands",
    "recommended_commands",
)
RISK_DETAILS_REQUIRED_KEYS = (
    "execution_quality_level",
    "strategy_signal_level",
    "recommended_controls",
    "recommended_strategy_gates",
)


def _mode() -> str:
    return get_mode()


def _launchd_label() -> str:
    return os.environ.get("ZEUS_LAUNCHD_LABEL", f"com.zeus.{_mode()}-trading")


def _status_path() -> Path:
    return state_path("status_summary.json")


def _risk_state_path() -> Path:
    return state_path("risk_state.db")


def _zeus_db_path() -> Path:
    return state_path("zeus.db").parent / "zeus.db"


def _trade_db_path() -> Path:
    return state_path("zeus_trades.db")


def _source_health_path() -> Path:
    return state_path("source_health.json")


def _world_db_path() -> Path:
    # K1 split 2026-05-11: ensemble_snapshots_v2 / readiness_state moved to
    # forecasts.db.  Return ZEUS_FORECASTS_DB_PATH so callers (and monkeypatched
    # tests that stub this function) target the correct physical file.
    return ZEUS_FORECASTS_DB_PATH


def _riskguard_label() -> str:
    configured = os.environ.get("ZEUS_RISKGUARD_LABEL")
    if configured:
        return configured
    mode = _mode()
    if mode == "live":
        return "com.zeus.riskguard-live"
    return "com.zeus.riskguard"


def _forecast_live_label() -> str:
    return os.environ.get("ZEUS_FORECAST_LIVE_LABEL", "com.zeus.forecast-live")


def _data_ingest_label() -> str:
    return os.environ.get("ZEUS_DATA_INGEST_LABEL", "com.zeus.data-ingest")


def _launchagents_dir() -> Path:
    return Path(os.environ.get("ZEUS_LAUNCHAGENTS_DIR", str(Path.home() / "Library" / "LaunchAgents")))


def _code_plane_identity() -> dict:
    try:
        from scripts.live_health_probe import _git_runtime_identity

        return _git_runtime_identity(str(PROJECT_ROOT))
    except Exception as exc:
        return {
            "status": "git_unavailable",
            "repo": str(PROJECT_ROOT),
            "error": str(exc),
            "dirty": None,
            "matches_expected": False,
        }


def _code_plane_is_ready(identity: dict) -> bool:
    return (
        identity.get("status") == "ok"
        and identity.get("dirty") is False
        and identity.get("matches_expected") is True
    )


def _module_from_program_arguments(program_args: list) -> str:
    for idx, token in enumerate(program_args[:-1]):
        if token == "-m":
            return str(program_args[idx + 1])
    return ""


def _has_module_launch(command: str, module: str) -> bool:
    tokens = str(command or "").split()
    for idx, token in enumerate(tokens[:-1]):
        if token == "-m" and tokens[idx + 1] == module:
            return True
    return False


def _is_known_live_db_holder(command: str) -> bool:
    command = str(command or "")
    known_modules = (
        "src.main",
        "src.riskguard.riskguard",
    )
    if any(_has_module_launch(command, module) for module in known_modules):
        return True
    return False


def _redacted_process_command(command: str) -> str:
    tokens = str(command or "").split()
    for idx, token in enumerate(tokens[:-1]):
        if token == "-m":
            return f"-m {tokens[idx + 1]}"
    for token in tokens:
        if token.endswith(".py"):
            return Path(token).name
    return tokens[0] if tokens else ""


def _etime_to_seconds(raw: str) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    days = 0
    if "-" in text:
        day_text, text = text.split("-", 1)
        try:
            days = int(day_text)
        except ValueError:
            return None
    parts = text.split(":")
    try:
        values = [int(part) for part in parts]
    except ValueError:
        return None
    if len(values) == 2:
        hours = 0
        minutes, seconds = values
    elif len(values) == 3:
        hours, minutes, seconds = values
    else:
        return None
    return (((days * 24) + hours) * 60 + minutes) * 60 + seconds


def _process_info(pid: int) -> dict:
    try:
        proc = subprocess.run(
            ["ps", "-p", str(pid), "-o", "etime=,command="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        return {"pid": pid, "error": str(exc)}
    if proc.returncode != 0:
        return {"pid": pid, "error": (proc.stderr or "").strip() or "ps failed"}
    text = proc.stdout.strip()
    if not text:
        return {"pid": pid, "error": "ps empty"}
    parts = text.split(None, 1)
    etime = parts[0]
    command = parts[1] if len(parts) > 1 else ""
    return {
        "pid": pid,
        "etime": etime,
        "etime_seconds": _etime_to_seconds(etime),
        "command": _redacted_process_command(command),
        "_command_for_classification": command,
    }


def _process_start_epoch(pid: int) -> float | None:
    try:
        proc = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    text = proc.stdout.strip()
    if not text:
        return None
    try:
        return time.mktime(time.strptime(text, "%a %b %d %H:%M:%S %Y"))
    except ValueError:
        return None


def _max_source_mtime(root: Path, rel_paths: tuple[str, ...]) -> float | None:
    mtimes: list[float] = []
    for rel_path in rel_paths:
        try:
            mtimes.append((root / rel_path).stat().st_mtime)
        except OSError:
            pass
    return max(mtimes) if mtimes else None


def _process_loaded_code_status(launchd_contracts: dict, *, root: Path | None = None) -> dict:
    root_path = Path(root or PROJECT_ROOT)
    stale: list[dict] = []
    unattested: list[dict] = []
    items: list[dict] = []
    for item in launchd_contracts.get("items", []):
        name = item.get("name")
        rel_paths = PROCESS_CODE_SURFACES.get(str(name))
        if not rel_paths:
            continue
        loaded = item.get("loaded") if isinstance(item.get("loaded"), dict) else {}
        pid = int(loaded.get("pid") or 0)
        source_mtime = _max_source_mtime(root_path, rel_paths)
        attestation = {
            "name": name,
            "label": item.get("label"),
            "pid": pid,
            "source_mtime": source_mtime,
            "paths": list(rel_paths),
        }
        if pid <= 0 or source_mtime is None:
            attestation["issue"] = "process_loaded_code_unattested"
            unattested.append(attestation)
            items.append(attestation)
            continue
        started_at = _process_start_epoch(pid)
        attestation["started_at"] = started_at
        if started_at is None:
            attestation["issue"] = "process_loaded_code_unattested"
            unattested.append(attestation)
        elif started_at + PROCESS_CODE_STALE_TOLERANCE_SECONDS < source_mtime:
            attestation["issue"] = "process_started_before_source_mtime"
            stale.append(attestation)
        items.append(attestation)
    issue = None
    if stale:
        issue = "PROCESS_LOADED_CODE_STALE"
    elif unattested:
        issue = "PROCESS_LOADED_CODE_UNATTESTED"
    return {
        "ok": not stale and not unattested,
        "issue": issue,
        "stale": stale,
        "unattested": unattested,
        "items": items,
    }


def _live_db_holder_status() -> dict:
    db_path = _trade_db_path()
    db_paths = [db_path, db_path.with_name(f"{db_path.name}-wal"), db_path.with_name(f"{db_path.name}-shm")]
    existing = [str(path) for path in db_paths if path.exists()]
    if not existing:
        return {
            "ok": True,
            "path": str(db_path),
            "holders": [],
            "unknown_long_lived_holders": [],
            "diagnostic": "db_files_missing",
        }
    try:
        proc = subprocess.run(
            ["lsof", "-F", "pn", *existing],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "path": str(db_path),
            "holders": [],
            "unknown_long_lived_holders": [],
            "unattested_holders": [],
            "diagnostic_unavailable": True,
            "issue": "LIVE_DB_HOLDER_ATTESTATION_UNAVAILABLE",
        }
    except Exception as exc:
        return {
            "ok": False,
            "path": str(db_path),
            "holders": [],
            "unknown_long_lived_holders": [],
            "unattested_holders": [],
            "diagnostic_unavailable": True,
            "issue": f"LIVE_DB_HOLDER_ATTESTATION_FAILED:{type(exc).__name__}",
        }
    stderr = (proc.stderr or "").strip()
    if proc.returncode != 0 and stderr:
        return {
            "ok": False,
            "path": str(db_path),
            "holders": [],
            "unknown_long_lived_holders": [],
            "unattested_holders": [],
            "diagnostic_unavailable": True,
            "issue": "LIVE_DB_HOLDER_ATTESTATION_FAILED",
            "error": stderr,
        }
    if proc.returncode != 0 and not (proc.stdout or "").strip():
        return {
            "ok": True,
            "path": str(db_path),
            "holders": [],
            "unknown_long_lived_holders": [],
            "unattested_holders": [],
            "diagnostic": "no_holders",
        }

    holders_by_pid: dict[int, dict] = {}
    current_pid: int | None = None
    for line in (proc.stdout or "").splitlines():
        if line.startswith("p"):
            try:
                current_pid = int(line[1:])
            except ValueError:
                current_pid = None
            if current_pid is not None:
                holders_by_pid.setdefault(current_pid, {"pid": current_pid, "open_dbs": []})
        elif line.startswith("n") and current_pid is not None:
            holders_by_pid.setdefault(current_pid, {"pid": current_pid, "open_dbs": []})["open_dbs"].append(line[1:])

    holders: list[dict] = []
    unknown_long_lived: list[dict] = []
    unattested: list[dict] = []
    for pid, holder in sorted(holders_by_pid.items()):
        info = _process_info(pid)
        command = str(info.pop("_command_for_classification", "") or info.get("command") or "")
        etime_seconds = info.get("etime_seconds")
        holder.update(info)
        holder["known_live_owner"] = _is_known_live_db_holder(command)
        holder["open_dbs"] = sorted(set(holder.get("open_dbs", [])))
        holders.append(holder)
        if not holder["known_live_owner"] and not isinstance(etime_seconds, int):
            unattested.append(holder)
        if (
            not holder["known_live_owner"]
            and isinstance(etime_seconds, int)
            and etime_seconds >= LIVE_DB_UNKNOWN_HOLDER_SECONDS
        ):
            unknown_long_lived.append(holder)

    issue = None
    if unknown_long_lived:
        issue = "LIVE_DB_UNKNOWN_LONG_LIVED_HOLDER"
    elif unattested:
        issue = "LIVE_DB_HOLDER_ATTESTATION_INCOMPLETE"
    return {
        "ok": not unknown_long_lived and not unattested,
        "path": str(db_path),
        "holder_age_budget_seconds": LIVE_DB_UNKNOWN_HOLDER_SECONDS,
        "holders": holders,
        "unknown_long_lived_holders": unknown_long_lived,
        "unattested_holders": unattested,
        "issue": issue,
    }


def _first_launchctl_field(output: str, field: str) -> str:
    match = re.search(rf"^\s*{re.escape(field)}\s*=\s*(.+)$", output or "", re.MULTILINE)
    if not match:
        return ""
    return match.group(1).strip()


def _launchctl_block_items(output: str, block_name: str) -> list[str]:
    match = re.search(
        rf"^\s*{re.escape(block_name)}\s*=\s*\{{\n(?P<body>.*?)^\s*\}}",
        output or "",
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        return []
    return [line.strip() for line in match.group("body").splitlines() if line.strip()]


def _launchctl_environment(output: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in _launchctl_block_items(output, "environment"):
        if "=>" not in line:
            continue
        key, value = line.split("=>", 1)
        env[key.strip()] = value.strip()
    return env


def _launchctl_loaded_contract(
    label: str,
    *,
    root_path: Path,
    plist_path: Path,
    expected_module: str,
) -> dict:
    item: dict = {
        "ok": False,
        "issues": [],
    }
    try:
        ps = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        item["issues"].append(f"loaded_contract_unreadable:{type(exc).__name__}")
        item["error"] = str(exc)
        return item
    output = (ps.stdout or ps.stderr or "")
    if ps.returncode != 0:
        item["issues"].append("loaded_job_missing")
        item["error"] = output.strip()
        return item

    arguments = _launchctl_block_items(output, "arguments")
    environment = _launchctl_environment(output)
    properties = {
        part.strip()
        for part in _first_launchctl_field(output, "properties").split("|")
        if part.strip()
    }
    minimum_runtime_raw = _first_launchctl_field(output, "minimum runtime")
    try:
        minimum_runtime = int(minimum_runtime_raw)
    except ValueError:
        minimum_runtime = None
    module = _module_from_program_arguments(arguments)
    path = _first_launchctl_field(output, "path")
    state = _first_launchctl_field(output, "state")
    pid = _parse_launchctl_pid(output)
    working_directory = _first_launchctl_field(output, "working directory")

    item.update(
        {
            "path": path,
            "state": state,
            "pid": pid,
            "keep_alive": "keepalive" in properties,
            "run_at_load": "runatload" in properties,
            "minimum_runtime": minimum_runtime,
            "working_directory": working_directory,
            "pythonpath_matches": environment.get("PYTHONPATH") == str(root_path),
            "module": module,
        }
    )
    if path != str(plist_path):
        item["issues"].append("loaded_plist_path_mismatch")
    if state != "running" or pid <= 0:
        item["issues"].append("loaded_job_not_running")
    if "keepalive" not in properties:
        item["issues"].append("loaded_keepalive_not_true")
    if "runatload" not in properties:
        item["issues"].append("loaded_runatload_not_true")
    if minimum_runtime is None or not (10 <= minimum_runtime <= 300):
        item["issues"].append("loaded_minimum_runtime_missing_or_out_of_range")
    if working_directory != str(root_path):
        item["issues"].append("loaded_working_directory_mismatch")
    if environment.get("PYTHONPATH") != str(root_path):
        item["issues"].append("loaded_pythonpath_mismatch")
    if module != expected_module:
        item["issues"].append("loaded_program_module_mismatch")
    item["ok"] = not item["issues"]
    return item


def _launchd_contracts(
    launchagents_dir: Path | None = None,
    *,
    root: Path | None = None,
) -> dict:
    root_path = Path(root or PROJECT_ROOT)
    launchagents = Path(launchagents_dir or _launchagents_dir())
    specs = (
        ("live_trading", _launchd_label(), "src.main"),
        ("data_ingest", _data_ingest_label(), "src.ingest_main"),
        ("riskguard", _riskguard_label(), "src.riskguard.riskguard"),
        ("forecast_live", _forecast_live_label(), "src.ingest.forecast_live_daemon"),
    )
    items: list[dict] = []
    for name, label, expected_module in specs:
        path = launchagents / f"{label}.plist"
        item = {
            "name": name,
            "label": label,
            "path": str(path),
            "ok": False,
            "issues": [],
        }
        if not path.exists():
            item["issues"].append("plist_missing")
            items.append(item)
            continue
        try:
            with open(path, "rb") as handle:
                payload = plistlib.load(handle)
        except Exception as exc:
            item["issues"].append(f"plist_unreadable:{type(exc).__name__}")
            item["error"] = str(exc)
            items.append(item)
            continue
        if not isinstance(payload, dict):
            item["issues"].append("plist_not_dict")
            item["ok"] = False
            items.append(item)
            continue
        env = payload.get("EnvironmentVariables") or {}
        if not isinstance(env, dict):
            item["issues"].append("environment_variables_not_dict")
            env = {}
        program_args = payload.get("ProgramArguments") or []
        if not isinstance(program_args, list):
            item["issues"].append("program_arguments_not_list")
            program_args = []
        module = _module_from_program_arguments(program_args)
        throttle = payload.get("ThrottleInterval")
        item.update(
            {
                "keep_alive": bool(payload.get("KeepAlive")),
                "run_at_load": bool(payload.get("RunAtLoad")),
                "throttle_interval": throttle,
                "working_directory": payload.get("WorkingDirectory"),
                "pythonpath_matches": env.get("PYTHONPATH") == str(root_path),
                "module": module,
            }
        )
        if payload.get("Label") != label:
            item["issues"].append("label_mismatch")
        if payload.get("KeepAlive") is not True:
            item["issues"].append("keepalive_not_true")
        if payload.get("RunAtLoad") is not True:
            item["issues"].append("runatload_not_true")
        if not isinstance(throttle, int) or not (10 <= throttle <= 300):
            item["issues"].append("throttle_interval_missing_or_out_of_range")
        if payload.get("WorkingDirectory") != str(root_path):
            item["issues"].append("working_directory_mismatch")
        if env.get("PYTHONPATH") != str(root_path):
            item["issues"].append("pythonpath_mismatch")
        if module != expected_module:
            item["issues"].append("program_module_mismatch")
        loaded = _launchctl_loaded_contract(
            label,
            root_path=root_path,
            plist_path=path,
            expected_module=expected_module,
        )
        item["loaded"] = loaded
        for issue in loaded.get("issues", []):
            item["issues"].append(issue)
        item["ok"] = not item["issues"]
        items.append(item)
    return {
        "ok": all(item["ok"] for item in items),
        "launchagents_dir": str(launchagents),
        "items": items,
    }


def _status_age_seconds(timestamp: str) -> float | None:
    if not timestamp:
        return None
    try:
        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())


def _settlement_truth_status(db_path: Path | None = None) -> dict:
    path = Path(db_path or _world_db_path())
    if not path.exists():
        return {
            "ok": False,
            "path": str(path),
            "issue": "SETTLEMENT_TRUTH_DB_MISSING",
        }
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT COUNT(*) AS count, MAX(settled_at) AS max_settled_at, "
            "MAX(recorded_at) AS max_recorded_at FROM settlements_v2"
        ).fetchone()
        conn.close()
    except Exception as exc:
        return {
            "ok": False,
            "path": str(path),
            "issue": "SETTLEMENT_TRUTH_UNAVAILABLE",
            "error": str(exc),
        }
    count = int(row[0] or 0) if row else 0
    max_settled_at = row[1] if row else None
    age_seconds = _status_age_seconds(max_settled_at or "")
    ok = (
        count > 0
        and age_seconds is not None
        and age_seconds <= SETTLEMENT_TRUTH_STALE_SECONDS
    )
    issue = None
    if count <= 0:
        issue = "SETTLEMENT_TRUTH_EMPTY"
    elif age_seconds is None:
        issue = "SETTLEMENT_TRUTH_MAX_SETTLED_AT_UNPARSEABLE"
    elif not ok:
        issue = "SETTLEMENT_TRUTH_STALE"
    return {
        "ok": ok,
        "path": str(path),
        "count": count,
        "max_settled_at": max_settled_at,
        "max_recorded_at": row[2] if row else None,
        "age_seconds": None if age_seconds is None else round(age_seconds, 1),
        "stale_budget_seconds": SETTLEMENT_TRUTH_STALE_SECONDS,
        "issue": issue,
    }


def _source_health_status() -> dict:
    path = _source_health_path()
    try:
        from src.control.freshness_gate import evaluate_freshness

        verdict = evaluate_freshness(state_dir=path.parent)
    except Exception as exc:
        return {
            "ok": False,
            "path": str(path),
            "error": str(exc),
            "issue": "SOURCE_HEALTH_UNAVAILABLE",
        }

    written_at_age = _status_age_seconds(verdict.written_at or "")
    writer_fresh = (
        written_at_age is not None
        and written_at_age <= SOURCE_HEALTH_WRITER_STALE_SECONDS
    )
    source_statuses = [
        {
            "source": status.source,
            "fresh": status.fresh,
            "stale": status.stale,
            "last_success_at": status.last_success_at,
            "age_seconds": None if status.age_seconds is None else round(status.age_seconds, 1),
            "budget_seconds": status.budget_seconds,
            "degradation_flags": list(status.degradation_flags),
        }
        for status in verdict.source_statuses
    ]
    all_sources_fresh = bool(source_statuses) and all(status["fresh"] for status in source_statuses)
    ok = (
        verdict.branch == "FRESH"
        and writer_fresh
        and all_sources_fresh
        and not verdict.operator_overrides
    )
    issue = None
    if verdict.branch == "ABSENT":
        issue = "SOURCE_HEALTH_ABSENT"
    elif verdict.branch == "STALE":
        issue = "SOURCE_HEALTH_SOURCE_STALE"
    elif not writer_fresh:
        issue = "SOURCE_HEALTH_WRITER_STALE"
    elif verdict.operator_overrides:
        issue = "SOURCE_HEALTH_OPERATOR_OVERRIDE"
    elif not all_sources_fresh:
        issue = "SOURCE_HEALTH_SOURCE_NOT_FRESH"
    return {
        "ok": ok,
        "path": str(path),
        "branch": verdict.branch,
        "issue": issue,
        "written_at": verdict.written_at,
        "written_at_age_seconds": None if written_at_age is None else round(written_at_age, 1),
        "writer_fresh": writer_fresh,
        "writer_budget_seconds": SOURCE_HEALTH_WRITER_STALE_SECONDS,
        "all_sources_fresh": all_sources_fresh,
        "stale_sources": list(verdict.stale_sources),
        "day0_capture_disabled": bool(verdict.day0_capture_disabled),
        "ensemble_disabled": bool(verdict.ensemble_disabled),
        "degraded_data": bool(verdict.degraded_data),
        "operator_overrides": list(verdict.operator_overrides),
        "source_statuses": source_statuses,
    }


def _execution_capability_db_lock_status(status: dict | None) -> dict:
    execution_capability = (status or {}).get("execution_capability")
    if not isinstance(execution_capability, dict):
        return {"ok": True, "locks": [], "issue": None}
    locks: list[dict] = []
    for action_name, action in execution_capability.items():
        if not isinstance(action, dict):
            continue
        components = action.get("components")
        if not isinstance(components, list):
            continue
        for component in components:
            if not isinstance(component, dict):
                continue
            details = component.get("details") if isinstance(component.get("details"), dict) else {}
            error = str(details.get("error") or "")
            if "database is locked" not in error.lower():
                continue
            locks.append(
                {
                    "action": action_name,
                    "component": component.get("component"),
                    "allowed": component.get("allowed"),
                    "reason": component.get("reason"),
                    "loader_failed": bool(details.get("loader_failed")),
                    "error_type": details.get("error_type"),
                    "error": error,
                }
            )
    return {
        "ok": not locks,
        "locks": locks,
        "issue": "LIVE_DB_LOCK_EXECUTION_CAPABILITY" if locks else None,
    }


def _parse_launchctl_pid(output: str) -> int:
    """Parse PID from launchctl output across macOS formats."""
    text = (output or "").strip()
    if not text:
        return 0

    # Older tabular format from `launchctl list`
    if "\t" in text and "\n" not in text and not text.startswith("{"):
        parts = text.split("\t")
        if parts and parts[0] != "-":
            try:
                return int(parts[0])
            except ValueError:
                return 0

    # Current key/value block format from `launchctl list <label>`
    match = re.search(r'"PID"\s*=\s*(\d+);', text)
    if match:
        return int(match.group(1))
    match = re.search(r'\bpid\s*=\s*(\d+)', text)
    if match:
        return int(match.group(1))

    return 0


def _launchctl_pid_for(label: str) -> int:
    commands = [
        ["launchctl", "list", label],
        ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
    ]
    for cmd in commands:
        try:
            ps = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        except Exception:
            continue
        if ps.returncode != 0:
            continue
        pid = _parse_launchctl_pid(ps.stdout or ps.stderr)
        if pid > 0:
            return pid
    return 0


def _missing_required_keys(payload: dict | None, required: tuple[str, ...], *, prefix: str = "") -> list[str]:
    if not isinstance(payload, dict):
        return [prefix.rstrip(".")] if prefix else list(required)
    missing: list[str] = []
    for key in required:
        if key not in payload:
            missing.append(f"{prefix}{key}" if prefix else key)
    return missing


def check() -> dict:
    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "healthy": False,
        "mode": _mode(),
        "launchd_label": _launchd_label(),
        "riskguard_label": _riskguard_label(),
    }
    result["code_plane"] = _code_plane_identity()
    result["code_plane_ok"] = _code_plane_is_ready(result["code_plane"])
    if not result["code_plane_ok"]:
        result["code_plane_issue"] = "LIVE_CODE_PLANE_DRIFT"
    result["launchd_contracts"] = _launchd_contracts()
    result["launchd_contract_ok"] = bool(result["launchd_contracts"].get("ok"))
    if not result["launchd_contract_ok"]:
        result["launchd_contract_issue"] = "LIVE_LAUNCHD_CONTRACT_DRIFT"
    result["process_code"] = _process_loaded_code_status(result["launchd_contracts"])
    result["process_code_ok"] = bool(result["process_code"].get("ok"))
    if not result["process_code_ok"]:
        result["process_code_issue"] = result["process_code"].get("issue") or "PROCESS_LOADED_CODE_UNATTESTED"
    result["source_health"] = _source_health_status()
    result["source_health_ok"] = bool(result["source_health"].get("ok"))
    if not result["source_health_ok"]:
        result["source_health_issue"] = result["source_health"].get("issue") or "LIVE_SOURCE_HEALTH_STALE"
    result["live_db_holders"] = _live_db_holder_status()
    result["live_db_holders_ok"] = bool(result["live_db_holders"].get("ok", True))
    if not result["live_db_holders_ok"]:
        result["live_db_holders_issue"] = result["live_db_holders"].get("issue") or "LIVE_DB_HOLDER_POLICY"

    # Check daemon PID
    try:
        pid = _launchctl_pid_for(result["launchd_label"])
        result["pid"] = pid
        result["daemon_alive"] = pid > 0
    except Exception:
        result["daemon_alive"] = False

    # Check RiskGuard PID
    try:
        pid = _launchctl_pid_for(result["riskguard_label"])
        result["riskguard_pid"] = pid
        result["riskguard_alive"] = pid > 0
    except Exception:
        result["riskguard_alive"] = False

    try:
        pid = _launchctl_pid_for(_data_ingest_label())
        result["data_ingest_label"] = _data_ingest_label()
        result["data_ingest_pid"] = pid
        result["data_ingest_alive"] = pid > 0
    except Exception:
        result["data_ingest_label"] = _data_ingest_label()
        result["data_ingest_alive"] = False

    # Check mode-qualified status summary
    status_path = _status_path()
    result["status_path"] = str(status_path)
    if status_path.exists():
        try:
            with open(status_path) as f:
                status = json.load(f)
            status_contract_missing = _missing_required_keys(status, STATUS_REQUIRED_KEYS)
            control = status.get("control", {}) if isinstance(status, dict) else {}
            status_contract_missing.extend(
                _missing_required_keys(control, STATUS_CONTROL_REQUIRED_KEYS, prefix="control.")
            )
            result["status_contract_missing_keys"] = status_contract_missing
            result["status_contract_valid"] = not status_contract_missing
            if result["status_contract_valid"]:
                result["recommended_auto_commands"] = list(control.get("recommended_auto_commands", []))
                result["review_required_commands"] = list(control.get("review_required_commands", []))
                result["recommended_commands"] = list(control.get("recommended_commands", []))
                result["action_required"] = bool(result["recommended_commands"])
                result["auto_action_available"] = bool(result["recommended_auto_commands"])
            else:
                result["recommended_auto_commands"] = []
                result["review_required_commands"] = []
                result["recommended_commands"] = []
                result["action_required"] = False
                result["auto_action_available"] = False
            result["last_cycle"] = status.get("timestamp", "unknown")
            risk = status.get("risk", {}) if isinstance(status.get("risk", {}), dict) else {}
            result["risk_level"] = risk.get("level", "UNKNOWN")
            result["infrastructure_level"] = risk.get("infrastructure_level")
            infrastructure_issues = risk.get("infrastructure_issues", [])
            result["infrastructure_issues"] = (
                list(infrastructure_issues)
                if isinstance(infrastructure_issues, list)
                else []
            )
            risk_details = risk.get("details", {}) or {}
            if isinstance(risk_details, dict):
                result["risk_details"] = risk_details
            result["positions"] = status.get("portfolio", {}).get("open_positions", 0)
            result["exposure"] = status.get("portfolio", {}).get("total_exposure_usd", 0)
            result["entries_pause_source"] = control.get("entries_pause_source")
            result["entries_pause_reason"] = control.get("entries_pause_reason")
            cycle = status.get("cycle", {}) or {}
            result["entries_blocked_reason"] = cycle.get("entries_blocked_reason")
            result["force_exit_review_scope"] = cycle.get("force_exit_review_scope")
            result["quarantine_expired"] = cycle.get("quarantine_expired")
            result["cycle_failed"] = bool(cycle.get("failed", False))
            result["failure_reason"] = cycle.get("failure_reason")
            execution = status.get("execution", {}) or {}
            if isinstance(execution, dict):
                result["execution_summary"] = execution.get("overall", execution)
            execution_capability = status.get("execution_capability", {}) or {}
            if isinstance(execution_capability, dict):
                entry_capability = execution_capability.get("entry", {}) or {}
                if isinstance(entry_capability, dict):
                    result["entry_execution_capability"] = entry_capability
                    entry_status = entry_capability.get("status")
                    result["entry_execution_capability_status"] = entry_status
                    result["entry_execution_capability_ok"] = entry_status not in {
                        "blocked",
                        "UNKNOWN_BLOCKED",
                        "unknown_blocked",
                    }
                    if not result["entry_execution_capability_ok"]:
                        result["entry_execution_capability_issue"] = "LIVE_ENTRY_EXECUTION_BLOCKED"
            strategy = status.get("strategy", {}) or {}
            if isinstance(strategy, dict):
                result["strategy_summary"] = strategy
            learning = status.get("learning", {}) or {}
            if isinstance(learning, dict):
                result["learning_summary"] = learning
                if "no_trade_stage_counts" in learning:
                    result["recent_no_trade_stage_counts"] = learning["no_trade_stage_counts"]
            control = status.get("control", {}) or {}
            if isinstance(control, dict):
                result["control_state"] = control
            runtime = status.get("runtime", {}) or {}
            if isinstance(runtime, dict):
                result["runtime_summary"] = runtime
            result["db_lock_status"] = _execution_capability_db_lock_status(status)
            result["db_lock_ok"] = bool(result["db_lock_status"].get("ok", True))
            if not result["db_lock_ok"]:
                result["db_lock_issue"] = result["db_lock_status"].get("issue") or "LIVE_DB_LOCK"
            age_seconds = _status_age_seconds(result["last_cycle"])
            if age_seconds is not None:
                result["status_age_seconds"] = round(age_seconds, 1)
                result["status_fresh"] = age_seconds <= STATUS_STALE_SECONDS
            else:
                result["status_fresh"] = False
        except Exception:
            result["status_summary"] = "corrupt"
    else:
        result["status_summary"] = "missing"
        result["action_required"] = False
        result["db_lock_status"] = {"ok": True, "locks": [], "issue": None}
        result["db_lock_ok"] = True
        result["entry_execution_capability_ok"] = False

    risk_state_path = _risk_state_path()
    result["risk_state_path"] = str(risk_state_path)
    if risk_state_path.exists():
        try:
            import sqlite3

            conn = sqlite3.connect(str(risk_state_path))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT level, checked_at, details_json FROM risk_state ORDER BY checked_at DESC LIMIT 1"
            ).fetchone()
            conn.close()

            if row is not None:
                result["riskguard_level"] = row["level"]
                result["riskguard_checked_at"] = row["checked_at"]
                details_json = row["details_json"] if "details_json" in row.keys() else None
                details = json.loads(details_json) if details_json else {}
                result["riskguard_contract_missing_keys"] = _missing_required_keys(details, RISK_DETAILS_REQUIRED_KEYS)
                result["riskguard_contract_valid"] = not result["riskguard_contract_missing_keys"]
                age_seconds = _status_age_seconds(row["checked_at"])
                if age_seconds is not None:
                    result["riskguard_age_seconds"] = round(age_seconds, 1)
                    result["riskguard_fresh"] = age_seconds <= RISKGUARD_STALE_SECONDS
                else:
                    result["riskguard_fresh"] = False
            else:
                result["riskguard_state"] = "empty"
                result["riskguard_fresh"] = False
                result["riskguard_contract_valid"] = False
        except Exception:
            result["riskguard_state"] = "corrupt"
            result["riskguard_fresh"] = False
            result["riskguard_contract_valid"] = False
    else:
        result["riskguard_state"] = "missing"
        result["riskguard_fresh"] = False
        result["riskguard_contract_valid"] = False

    if "recent_no_trade_stage_counts" not in result:
        try:
            conn = get_connection(_zeus_db_path())
            no_trade_cases = query_no_trade_cases(conn, hours=24)
            conn.close()
            stage_counts: dict[str, int] = {}
            for case in no_trade_cases:
                stage = str(case.get("rejection_stage") or "UNKNOWN")
                stage_counts[stage] = stage_counts.get(stage, 0) + 1
            result["recent_no_trade_stage_counts"] = stage_counts
        except Exception:
            result["recent_no_trade_stage_counts"] = {}

    # K1 split 2026-05-11: ensemble_snapshots_v2 and readiness_state moved to
    # forecasts.db.  _world_db_path() now returns ZEUS_FORECASTS_DB_PATH so
    # that monkeypatched tests (which stub _world_db_path) continue to exercise
    # the absent-DB branch correctly.
    forecasts_db_path = _world_db_path()
    result["forecasts_db_path"] = str(forecasts_db_path)
    if forecasts_db_path.exists():
        try:
            from src.config import entry_forecast_config
            from src.data.live_entry_status import build_live_entry_forecast_status

            conn = get_forecasts_connection()
            conn.row_factory = sqlite3.Row
            entry_forecast_status = build_live_entry_forecast_status(
                conn,
                config=entry_forecast_config(),
            )
            conn.close()
            result["entry_forecast_status"] = entry_forecast_status.to_dict()
            result["entry_forecast_blockers"] = list(entry_forecast_status.blockers)
        except Exception as exc:
            result["entry_forecast_status"] = {"status": "UNKNOWN_BLOCKED", "error": str(exc)}
            result["entry_forecast_blockers"] = ["ENTRY_FORECAST_STATUS_UNAVAILABLE"]
    else:
        result["entry_forecast_status"] = {"status": "UNKNOWN_BLOCKED", "error": "world_db_missing"}
        result["entry_forecast_blockers"] = ["ENTRY_FORECAST_WORLD_DB_MISSING"]

    result["settlement_truth"] = _settlement_truth_status()
    result["settlement_truth_ok"] = bool(result["settlement_truth"].get("ok"))
    if not result["settlement_truth_ok"]:
        result["settlement_truth_issue"] = result["settlement_truth"].get("issue") or "SETTLEMENT_TRUTH_UNHEALTHY"

    try:
        from scripts.validate_assumptions import run_validation

        validation = run_validation()
        result["assumptions_valid"] = bool(validation["valid"])
        if not validation["valid"]:
            result["assumption_mismatches"] = validation["mismatches"]
    except Exception as exc:
        result["assumptions_valid"] = False
        result["assumption_mismatches"] = [f"validation_error: {exc}"]

    healthy = (
        bool(result.get("daemon_alive"))
        and bool(result.get("status_fresh"))
        and bool(result.get("status_contract_valid"))
        and bool(result.get("riskguard_alive"))
        and bool(result.get("data_ingest_alive"))
        and bool(result.get("riskguard_fresh"))
        and bool(result.get("riskguard_contract_valid"))
        and bool(result.get("assumptions_valid"))
        and bool(result.get("code_plane_ok"))
        and bool(result.get("process_code_ok"))
        and bool(result.get("launchd_contract_ok"))
        and bool(result.get("source_health_ok"))
        and bool(result.get("entry_execution_capability_ok", True))
        and bool(result.get("settlement_truth_ok"))
        and bool(result.get("db_lock_ok", True))
        and bool(result.get("live_db_holders_ok", True))
        and not bool(result.get("cycle_failed"))
        and result.get("infrastructure_level") != "RED"
    )

    # Phase C-4 activation: when ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS=1,
    # entry_forecast_blockers participate in the healthy predicate. Default
    # OFF preserves the legacy "GREEN even if entry-forecast is BLOCKED"
    # behavior. Closes the fail-OPEN seam critic-opus ATTACK 4 surfaced.
    if os.environ.get("ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS") == "1":
        healthy = healthy and not bool(result.get("entry_forecast_blockers"))

    result["healthy"] = healthy
    return result


def exit_code_for(result: dict) -> int:
    if result.get("healthy"):
        return 0
    if result.get("daemon_alive") or result.get("status_summary") not in {"missing", "corrupt"}:
        return 1
    return 2


if __name__ == "__main__":
    result = check()
    print(json.dumps(result, indent=2))
    sys.exit(exit_code_for(result))
