# Created: 2026-03-26
# Last reused or audited: 2026-05-16
# Authority basis: docs/operations/task_2026-05-14_k1_followups/PLAN.md §4.5 (K1 broken-script remediation); docs/operations/task_2026-05-16_live_continuous_run_package/LIVE_CONTINUOUS_RUN_PACKAGE_PLAN.md Phase C
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
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_mode, state_path
from src.state.db import get_connection, get_forecasts_connection, ZEUS_FORECASTS_DB_PATH
from src.state.decision_chain import query_no_trade_cases

STATUS_STALE_SECONDS = 2 * 3600
RISKGUARD_STALE_SECONDS = 5 * 60
SOURCE_HEALTH_WRITER_STALE_SECONDS = 10 * 60
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
        env = payload.get("EnvironmentVariables") or {}
        program_args = payload.get("ProgramArguments") or []
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
    result["source_health"] = _source_health_status()
    result["source_health_ok"] = bool(result["source_health"].get("ok"))
    if not result["source_health_ok"]:
        result["source_health_issue"] = result["source_health"].get("issue") or "LIVE_SOURCE_HEALTH_STALE"

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
        and bool(result.get("riskguard_fresh"))
        and bool(result.get("riskguard_contract_valid"))
        and bool(result.get("assumptions_valid"))
        and bool(result.get("code_plane_ok"))
        and bool(result.get("launchd_contract_ok"))
        and bool(result.get("source_health_ok"))
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
