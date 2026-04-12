"""Daemon heartbeat staleness check.

Checks state/daemon-heartbeat-{mode}.json and alerts if the daemon has
not written a heartbeat in more than STALE_THRESHOLD_SECONDS.

Exit codes:
  0 = heartbeat fresh (daemon alive)
  1 = heartbeat stale or file missing (daemon may be dead)

Usage:
  python scripts/check_daemon_heartbeat.py [--mode live|paper]
  python scripts/check_daemon_heartbeat.py --all
"""

import argparse
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


def check_mode(mode: str) -> tuple[bool, str]:
    """Check heartbeat for a single mode.

    Returns (is_fresh, message).
    """
    path = _state_dir() / f"daemon-heartbeat-{mode}.json"

    if not path.exists():
        return False, f"[{mode}] MISSING: {path} not found (daemon never started or crashed)"

    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        return False, f"[{mode}] UNREADABLE: {path}: {exc}"

    raw_ts = data.get("timestamp")
    if not raw_ts:
        return False, f"[{mode}] MALFORMED: no 'timestamp' field in {path}"

    try:
        ts = datetime.fromisoformat(raw_ts)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except Exception as exc:
        return False, f"[{mode}] BAD_TIMESTAMP: cannot parse {raw_ts!r}: {exc}"

    now = datetime.now(timezone.utc)
    age_seconds = (now - ts).total_seconds()

    if age_seconds > STALE_THRESHOLD_SECONDS:
        stale_minutes = age_seconds / 60
        return (
            False,
            f"[{mode}] STALE: last heartbeat {stale_minutes:.1f}m ago"
            f" (threshold {STALE_THRESHOLD_SECONDS // 60}m), ts={raw_ts}",
        )

    return True, f"[{mode}] OK: last heartbeat {age_seconds:.0f}s ago (ts={raw_ts})"


def main() -> int:
    parser = argparse.ArgumentParser(description="Check daemon heartbeat staleness")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--mode", choices=["live", "paper"], help="Check a specific mode")
    group.add_argument("--all", action="store_true", help="Check both live and paper")
    args = parser.parse_args()

    if args.all:
        modes = ["live", "paper"]
    elif args.mode:
        modes = [args.mode]
    else:
        # Default: use current ZEUS_MODE env
        from src.config import get_mode
        modes = [get_mode()]

    any_stale = False
    for mode in modes:
        fresh, msg = check_mode(mode)
        print(msg)
        if not fresh:
            any_stale = True

    return 1 if any_stale else 0


if __name__ == "__main__":
    sys.exit(main())
