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

from src.config import settings, state_path
from src.state.db import get_connection
from src.state.decision_chain import query_no_trade_cases

STATUS_STALE_SECONDS = 2 * 3600
RISKGUARD_STALE_SECONDS = 5 * 60


def _mode() -> str:
    return os.environ.get("ZEUS_MODE", settings.mode)


def _launchd_label() -> str:
    return os.environ.get("ZEUS_LAUNCHD_LABEL", f"com.zeus.{_mode()}-trading")


def _status_path() -> Path:
    return state_path("status_summary.json")


def _risk_state_path() -> Path:
    return state_path("risk_state.db")


def _zeus_db_path() -> Path:
    return state_path("zeus.db").parent / "zeus.db"


def _riskguard_label() -> str:
    return os.environ.get("ZEUS_RISKGUARD_LABEL", "com.zeus.riskguard")


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
    if "\t" in text and not text.startswith("{"):
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

    return 0


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
        ps = subprocess.run(
            ["launchctl", "list", result["launchd_label"]],
            capture_output=True, text=True, timeout=5,
        )
        if ps.returncode == 0:
            pid = _parse_launchctl_pid(ps.stdout)
            result["pid"] = pid
            result["daemon_alive"] = pid > 0
        else:
            result["daemon_alive"] = False
    except Exception:
        result["daemon_alive"] = False

    # Check RiskGuard PID
    try:
        ps = subprocess.run(
            ["launchctl", "list", result["riskguard_label"]],
            capture_output=True, text=True, timeout=5,
        )
        if ps.returncode == 0:
            pid = _parse_launchctl_pid(ps.stdout)
            result["riskguard_pid"] = pid
            result["riskguard_alive"] = pid > 0
        else:
            result["riskguard_alive"] = False
    except Exception:
        result["riskguard_alive"] = False

    # Check mode-qualified status summary
    status_path = _status_path()
    result["status_path"] = str(status_path)
    if status_path.exists():
        try:
            with open(status_path) as f:
                status = json.load(f)
            result["last_cycle"] = status.get("timestamp", "unknown")
            result["risk_level"] = status.get("risk", {}).get("level", "UNKNOWN")
            result["positions"] = status.get("portfolio", {}).get("open_positions", 0)
            result["exposure"] = status.get("portfolio", {}).get("total_exposure_usd", 0)
            cycle = status.get("cycle", {}) or {}
            result["entries_blocked_reason"] = cycle.get("entries_blocked_reason")
            result["cycle_failed"] = bool(cycle.get("failed", False))
            result["failure_reason"] = cycle.get("failure_reason")
            execution = status.get("execution", {}) or {}
            if isinstance(execution, dict):
                result["execution_summary"] = execution.get("overall", execution)
            strategy = status.get("strategy", {}) or {}
            if isinstance(strategy, dict):
                result["strategy_summary"] = strategy
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

    risk_state_path = _risk_state_path()
    result["risk_state_path"] = str(risk_state_path)
    if risk_state_path.exists():
        try:
            import sqlite3

            conn = sqlite3.connect(str(risk_state_path))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT level, checked_at FROM risk_state ORDER BY checked_at DESC LIMIT 1"
            ).fetchone()
            conn.close()

            if row is not None:
                result["riskguard_level"] = row["level"]
                result["riskguard_checked_at"] = row["checked_at"]
                age_seconds = _status_age_seconds(row["checked_at"])
                if age_seconds is not None:
                    result["riskguard_age_seconds"] = round(age_seconds, 1)
                    result["riskguard_fresh"] = age_seconds <= RISKGUARD_STALE_SECONDS
                else:
                    result["riskguard_fresh"] = False
            else:
                result["riskguard_state"] = "empty"
                result["riskguard_fresh"] = False
        except Exception:
            result["riskguard_state"] = "corrupt"
            result["riskguard_fresh"] = False
    else:
        result["riskguard_state"] = "missing"
        result["riskguard_fresh"] = False

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

    try:
        from scripts.validate_assumptions import run_validation

        validation = run_validation()
        result["assumptions_valid"] = bool(validation["valid"])
        if not validation["valid"]:
            result["assumption_mismatches"] = validation["mismatches"]
    except Exception as exc:
        result["assumptions_valid"] = False
        result["assumption_mismatches"] = [f"validation_error: {exc}"]

    result["healthy"] = (
        bool(result.get("daemon_alive"))
        and bool(result.get("status_fresh"))
        and bool(result.get("riskguard_alive"))
        and bool(result.get("riskguard_fresh"))
        and bool(result.get("assumptions_valid"))
        and not bool(result.get("cycle_failed"))
    )
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
