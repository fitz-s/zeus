"""Zeus health check for Venus/OpenClaw monitoring.

Returns JSON: last_cycle, risk_level, positions, PID, daemon_alive.
Exit code 0 = healthy, 1 = degraded, 2 = dead.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = Path(__file__).parent.parent / "state"
STATUS_PATH = STATE_DIR / "status_summary.json"


def check() -> dict:
    result = {"timestamp": datetime.now(timezone.utc).isoformat(), "healthy": False}

    # Check daemon PID
    try:
        ps = subprocess.run(
            ["launchctl", "list", "com.zeus.paper-trading"],
            capture_output=True, text=True, timeout=5,
        )
        if ps.returncode == 0:
            parts = ps.stdout.strip().split("\t")
            pid = int(parts[0]) if parts[0] != "-" else 0
            result["pid"] = pid
            result["daemon_alive"] = pid > 0
        else:
            result["daemon_alive"] = False
    except Exception:
        result["daemon_alive"] = False

    # Check status summary
    if STATUS_PATH.exists():
        try:
            with open(STATUS_PATH) as f:
                status = json.load(f)
            result["last_cycle"] = status.get("timestamp", "unknown")
            result["risk_level"] = status.get("risk", {}).get("level", "UNKNOWN")
            result["positions"] = status.get("portfolio", {}).get("open_positions", 0)
            result["exposure"] = status.get("portfolio", {}).get("total_exposure_usd", 0)
            result["healthy"] = True
        except Exception:
            result["status_summary"] = "corrupt"
    else:
        result["status_summary"] = "missing"

    return result


if __name__ == "__main__":
    result = check()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["healthy"] else 2)
