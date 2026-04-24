"""Daemon heartbeat staleness check.

Checks state/daemon-heartbeat.json and alerts if the daemon has
not written a heartbeat in more than STALE_THRESHOLD_SECONDS.

Exit codes:
  0 = heartbeat fresh (daemon alive)
  1 = heartbeat stale or file missing (daemon may be dead)

Usage:
  python scripts/check_daemon_heartbeat.py
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

STALE_THRESHOLD_SECONDS = 300  # 5 minutes


def _state_dir() -> Path:
    from src.config import STATE_DIR
    return Path(STATE_DIR)


def check_heartbeat() -> tuple[bool, str]:
    """Check daemon heartbeat freshness.

    Returns (is_fresh, message).
    """
    path = _state_dir() / "daemon-heartbeat.json"

    if not path.exists():
        return False, f"MISSING: {path} not found (daemon never started or crashed)"

    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        return False, f"UNREADABLE: {path}: {exc}"

    raw_ts = data.get("timestamp")
    if not raw_ts:
        return False, f"MALFORMED: no 'timestamp' field in {path}"

    try:
        ts = datetime.fromisoformat(raw_ts)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except Exception as exc:
        return False, f"BAD_TIMESTAMP: cannot parse {raw_ts!r}: {exc}"

    now = datetime.now(timezone.utc)
    age_seconds = (now - ts).total_seconds()

    if age_seconds > STALE_THRESHOLD_SECONDS:
        stale_minutes = age_seconds / 60
        return (
            False,
            f"STALE: last heartbeat {stale_minutes:.1f}m ago"
            f" (threshold {STALE_THRESHOLD_SECONDS // 60}m), ts={raw_ts}",
        )

    return True, f"OK: last heartbeat {age_seconds:.0f}s ago (ts={raw_ts})"


def main() -> int:
    fresh, msg = check_heartbeat()
    print(msg)
    return 0 if fresh else 1


if __name__ == "__main__":
    sys.exit(main())
