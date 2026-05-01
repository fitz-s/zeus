#!/usr/bin/env bash
# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: live-blockers session 2026-05-01 — operator helper to clear
#                  an active auto_pause without re-arming the rest of the
#                  system. Sister script of arm_live_mode.sh; runs only step 3.
#
# What this script does:
#   1. Append an 'expire' row to control_overrides_history that sets
#      effective_until on the latest active row of
#      override_id='control_plane:global:entries_paused'.
#   2. Remove state/auto_pause_failclosed.tombstone if present.
#   3. Remove state/auto_pause_streak.json so the next failure starts a
#      fresh streak instead of immediately re-tripping the threshold.
#
# What this script does NOT do:
#   - Touch cutover_guard.json or any plist env var.
#   - Restart daemons.
#
# Verification after running:
#   sqlite3 state/zeus-world.db \
#     "SELECT override_id, value, effective_until FROM control_overrides \
#      WHERE override_id='control_plane:global:entries_paused';"
#   ls -la state/auto_pause_failclosed.tombstone state/auto_pause_streak.json
#     # (both expected absent)

set -euo pipefail

ZEUS_DIR="${ZEUS_DIR:-/Users/leofitz/.openclaw/workspace-venus/zeus}"
NOW_UTC="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"

cd "$ZEUS_DIR"

echo "[expire_auto_pause] step 1/3 — expire entries_paused override"
.venv/bin/python - <<PY
import sys
sys.path.insert(0, ".")
from src.state.db import expire_control_override
import sqlite3
conn = sqlite3.connect("state/zeus-world.db")
result = expire_control_override(
    conn,
    override_id="control_plane:global:entries_paused",
    expired_at="$NOW_UTC",
)
conn.commit()
conn.close()
print(f"  override-expire: {result}")
PY

echo "[expire_auto_pause] step 2/3 — clear tombstone"
TOMBSTONE="state/auto_pause_failclosed.tombstone"
if [[ -f "$TOMBSTONE" ]]; then
  printf "  removing tombstone "
  cat "$TOMBSTONE" || true
  echo ""
  rm -f "$TOMBSTONE"
else
  echo "  no tombstone present"
fi

echo "[expire_auto_pause] step 3/3 — clear auto_pause_streak.json"
STREAK="state/auto_pause_streak.json"
if [[ -f "$STREAK" ]]; then
  echo "  removing $STREAK"
  rm -f "$STREAK"
else
  echo "  no streak file present"
fi

echo ""
echo "[expire_auto_pause] DONE — entries should resume on next cycle if no fresh failure occurs."
