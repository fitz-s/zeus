#!/usr/bin/env python3
"""Force one full discovery cycle with all gates patched to HEALTHY.

Side-by-side test: did SF7 unblock real production candidates, or is there
a deeper filter inside discovery/evaluator that returns 0 even when gates pass?

Patches:
- src.control.heartbeat_supervisor.summary  → returns {health=HEALTHY, allow_submit=True}
- src.control.ws_gap_guard.summary           → returns SUBSCRIBED + allow_submit=True

Then invokes src.main.main() with sys.argv mocked to ["src.main", "--once"].

NOT for production. Test-only. Run after restarting RiskGuard with SF7 fix.
"""
from __future__ import annotations

import os
import sys

# Must precede any `src.*` import (and unittest.mock.patch on `src.*`)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Destructive opt-in guard — refuse to run without explicit acknowledgement.
# This script mutates state/zeus-world.db and deletes pause/tombstone files.
# ---------------------------------------------------------------------------
_FLAG = "--i-understand-this-is-destructive"
_ENV_VAR = "ZEUS_ALLOW_FORCE_CYCLE"

if _FLAG not in sys.argv and os.environ.get(_ENV_VAR) != "1":
    print(
        f"ERROR: this script mutates the live DB and removes pause/tombstone files.\n"
        f"To run, pass '{_FLAG}' or set {_ENV_VAR}=1 in the environment.",
        file=sys.stderr,
    )
    sys.exit(1)

# Strip the flag from argv so argparse / downstream code don't see it.
if _FLAG in sys.argv:
    sys.argv.remove(_FLAG)

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

# Ensure DB is unpaused before invocation
import sqlite3
from src.state.db_writer_lock import WriteClass, db_writer_lock  # noqa: E402

DB_PATH = Path("state/zeus-world.db")
NOW_ISO = datetime.now(timezone.utc).isoformat()


def _ensure_unpaused():
    with db_writer_lock(DB_PATH, WriteClass.BULK):
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            """
            INSERT INTO control_overrides_history (
              override_id, target_type, target_key, action_type, value,
              issued_by, issued_at, effective_until, reason, precedence,
              operation, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "control_plane:global:entries_paused", "global", "entries", "gate", "false",
                "control_plane", NOW_ISO, None, "force_cycle_test_pre", 100, "upsert", NOW_ISO,
            ),
        )
        conn.commit()
        conn.close()
    for f in ("state/auto_pause_failclosed.tombstone", "state/auto_pause_streak.json"):
        try:
            os.remove(f)
        except FileNotFoundError:
            pass


def _fake_heartbeat_summary():
    return {
        "health": "HEALTHY",
        "last_success_at": NOW_ISO,
        "consecutive_failures": 0,
        "heartbeat_id": "force_cycle_test",
        "cadence_seconds": 5,
        "last_error": None,
        "entry": {"allow_submit": True, "required_order_types": ["GTC", "GTD"]},
    }


def _fake_ws_summary():
    return {
        "connected": True,
        "last_message_at": NOW_ISO,
        "consecutive_gaps": 0,
        "subscription_state": "SUBSCRIBED",
        "gap_reason": "message_received",
        "m5_reconcile_required": False,
        "affected_markets": [],
        "updated_at": NOW_ISO,
        "stale_after_seconds": 30,
        "stale": False,
        "entry": {"allow_submit": True},
    }


def main():
    _ensure_unpaused()

    sys.argv = ["src.main", "--once"]

    with patch("src.control.heartbeat_supervisor.summary", _fake_heartbeat_summary), \
         patch("src.control.ws_gap_guard.summary", _fake_ws_summary):
        from src.main import main as zeus_main
        zeus_main()


if __name__ == "__main__":
    main()
