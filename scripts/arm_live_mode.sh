#!/usr/bin/env bash
# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: operator directive 2026-05-01
#                  "把所有flag都切换成live模式，但是我们不启动live deamon就好"
#
# Arm Zeus for live trading. Idempotent. Does NOT load daemons —
# operator runs `launchctl load …` separately when ready to fire.
#
# What this script does:
#   1. Inject `ZEUS_HARVESTER_LIVE_ENABLED=1` into both daemon plists.
#      This is the DR-33-A safety flag; without it,
#      harvester_truth_writer is a no-op and every settlement is
#      `quarantine_reason=no_observation_for_target_date`.
#   2. Write `state/cutover_guard.json` with state=LIVE_ENABLED.
#      `gate_for_intent(IntentKind.ENTRY)` returns allow_submit=True
#      only in this state. Default (file absent) = NORMAL = blocked.
#   3. Expire the auto-pause override that has been active in
#      `control_overrides` since 2026-04-28T22:03Z.
#      `is_entries_paused()` returns False once `effective_until` is
#      in the past.
#
# What this script does NOT do:
#   - Load daemons. Operator owns that timing.
#   - HK quarantine release (deferred until HKO 2026-04 archive
#     publishes ~2026-06-01).
#   - Paris LFPG legacy QUARANTINE downgrade (batched with HK release).
#
# Verification after running:
#   python -c "import json; print(json.load(open('state/cutover_guard.json'))['state'])"
#   sqlite3 state/zeus-world.db \
#     "SELECT override_id, value, effective_until FROM control_overrides \
#      WHERE override_id='control_plane:global:entries_paused';"
#   /usr/libexec/PlistBuddy -c \
#     "Print :EnvironmentVariables:ZEUS_HARVESTER_LIVE_ENABLED" \
#     ~/Library/LaunchAgents/com.zeus.live-trading.plist

set -euo pipefail

ZEUS_DIR="${ZEUS_DIR:-/Users/leofitz/.openclaw/workspace-venus/zeus}"
LAUNCHAGENTS="${LAUNCHAGENTS:-${HOME}/Library/LaunchAgents}"
NOW_UTC="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"

cd "$ZEUS_DIR"

# ---- 1) Inject ZEUS_HARVESTER_LIVE_ENABLED=1 into both plists ----
inject_flag() {
  local plist="$1"
  local key=":EnvironmentVariables:ZEUS_HARVESTER_LIVE_ENABLED"
  if [[ ! -f "$plist" ]]; then
    echo "WARN: $plist missing — skipping"
    return 0
  fi
  if /usr/libexec/PlistBuddy -c "Print $key" "$plist" >/dev/null 2>&1; then
    /usr/libexec/PlistBuddy -c "Set $key 1" "$plist"
    echo "  set    $plist :: ZEUS_HARVESTER_LIVE_ENABLED=1"
  else
    /usr/libexec/PlistBuddy -c "Add $key string 1" "$plist"
    echo "  added  $plist :: ZEUS_HARVESTER_LIVE_ENABLED=1"
  fi
}

echo "[arm_live_mode] step 1/3 — plist env vars"
inject_flag "$LAUNCHAGENTS/com.zeus.live-trading.plist"
inject_flag "$LAUNCHAGENTS/com.zeus.data-ingest.plist"

# ---- 2) Write state/cutover_guard.json = LIVE_ENABLED ----
echo "[arm_live_mode] step 2/3 — cutover_guard.json"
python3 - <<PY
import json, os
from pathlib import Path
state_path = Path("state/cutover_guard.json")
payload = {
    "state": "LIVE_ENABLED",
    "transitions": [
        {
            "from": "NORMAL",
            "to": "LIVE_ENABLED",
            "by": "operator",
            "at": "$NOW_UTC",
            "reason": "live launch authorization 2026-05-01 — operator directive '把所有flag都切换成live模式'",
        }
    ],
}
state_path.parent.mkdir(parents=True, exist_ok=True)
tmp = state_path.with_suffix(".tmp")
tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
os.replace(tmp, state_path)
print(f"  wrote {state_path} :: state=LIVE_ENABLED")
PY

# ---- 3) Expire entries_paused override ----
echo "[arm_live_mode] step 3/3 — expire entries_paused override"
python3 - <<PY
import sqlite3
conn = sqlite3.connect("state/zeus-world.db")
cur = conn.cursor()
row = cur.execute(
    "SELECT effective_until FROM control_overrides "
    "WHERE override_id='control_plane:global:entries_paused'"
).fetchone()
if row is None:
    print("  no entries_paused override active — nothing to expire")
else:
    cur.execute(
        "UPDATE control_overrides SET effective_until=? "
        "WHERE override_id='control_plane:global:entries_paused'",
        ("$NOW_UTC",),
    )
    conn.commit()
    print(f"  expired entries_paused (was effective_until={row[0]!r}) → now={'$NOW_UTC'}")
conn.close()
PY

echo ""
echo "[arm_live_mode] DONE — daemons NOT loaded."
echo "  next step (operator-authorized):"
echo "    launchctl load $LAUNCHAGENTS/com.zeus.data-ingest.plist"
echo "    launchctl load $LAUNCHAGENTS/com.zeus.riskguard-live.plist"
echo "    launchctl load $LAUNCHAGENTS/com.zeus.live-trading.plist"
