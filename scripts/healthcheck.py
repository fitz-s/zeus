# Created: 2026-03-26
# Last reused/audited: 2026-05-03
# Authority basis: live healthcheck contract + PLAN_v4 Phase 10 live-entry blocker status surface.
"""Zeus health check for Venus/OpenClaw monitoring.

Reads mode-qualified state written by the running daemon.
Exit code 0 = healthy, 1 = degraded, 2 = dead.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_mode, state_path
from src.state.db import get_connection
from src.state.decision_chain import query_no_trade_cases

STATUS_STALE_SECONDS = 2 * 3600
RISKGUARD_STALE_SECONDS = 5 * 60
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


def _world_db_path() -> Path:
    return state_path("zeus-world.db")


def _riskguard_label() -> str:
    configured = os.environ.get("ZEUS_RISKGUARD_LABEL")
    if configured:
        return configured
    mode = _mode()
    if mode == "live":
        return "com.zeus.riskguard-live"
    return "com.zeus.riskguard"


def _status_age_seconds(timestamp: str) -> float | None:
    if not timestamp:
        return None
    try:
        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())


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

    world_db_path = _world_db_path()
    result["world_db_path"] = str(world_db_path)
    if world_db_path.exists():
        try:
            import sqlite3

            from src.config import entry_forecast_config
            from src.data.live_entry_status import build_live_entry_forecast_status

            conn = sqlite3.connect(str(world_db_path))
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
