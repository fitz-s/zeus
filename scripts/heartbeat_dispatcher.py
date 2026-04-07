#!/usr/bin/env python3
"""Zero-token heartbeat dispatcher.

Runs healthcheck.py. If healthy → exit silently (zero LLM cost).
If degraded/dead → trigger full Venus agent session for diagnosis + Discord alert.

Replaces the old zeus-heartbeat cron job that woke a full agent session every 30 min
just to check health (wasting ~150k tokens per run × 48 runs/day).
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
HEALTHCHECK = PROJECT_ROOT / "scripts" / "healthcheck.py"
OPENCLAW = "/Users/leofitz/.npm-global/bin/openclaw"
HEARTBEAT_LOG = Path("/Users/leofitz/.openclaw/logs/zeus-heartbeat-dispatch.log")
VENV_PYTHON = str(PROJECT_ROOT / ".venv" / "bin" / "python")

# The original venus heartbeat cron job ID — only triggered on failure
VENUS_HEARTBEAT_JOB_ID = "zeus-heartbeat-001"


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    line = json.dumps({"ts": ts, "msg": msg}) + "\n"
    try:
        with open(HEARTBEAT_LOG, "a") as f:
            f.write(line)
    except Exception:
        pass


def run_healthcheck() -> int:
    """Run healthcheck.py, return exit code."""
    try:
        result = subprocess.run(
            [VENV_PYTHON, str(HEALTHCHECK)],
            capture_output=True, text=True, timeout=60,
            cwd=str(PROJECT_ROOT),
        )
        return result.returncode
    except subprocess.TimeoutExpired:
        log("healthcheck timeout")
        return 2
    except Exception as e:
        log(f"healthcheck error: {e}")
        return 2


def trigger_venus_session(severity: str) -> None:
    """Trigger full Venus agent session for diagnosis."""
    log(f"triggering venus session: severity={severity}")
    try:
        subprocess.run(
            [OPENCLAW, "cron", "run", VENUS_HEARTBEAT_JOB_ID],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as e:
        log(f"trigger failed: {e}")


def main() -> int:
    code = run_healthcheck()

    if code == 0:
        # Healthy — silent exit, zero tokens spent
        log("healthy")
        return 0

    severity = "degraded" if code == 1 else "dead"
    log(f"ALERT: zeus {severity} (exit code {code})")

    # Only now wake the expensive agent session
    trigger_venus_session(severity)
    return code


if __name__ == "__main__":
    sys.exit(main())
