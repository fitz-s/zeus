#!/usr/bin/env bash
# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: operator directive 2026-05-01
#                  "把所有flag都切换成live模式，但是我们不启动live deamon就好"
#                  + WS auto-derive directive 2026-05-01
#                  ("任何硬编码bankroll都是一次严重的结构性失误" — same shape
#                  applies to hardcoded condition_id lists; auto-derivation
#                  from src.data.market_scanner is the structural fix).
#
# Arm Zeus for live trading. Idempotent. Does NOT load daemons —
# operator runs `launchctl load …` separately when ready to fire.
#
# What this script does:
#   1. Inject `ZEUS_HARVESTER_LIVE_ENABLED=1` into both daemon plists.
#      This is the DR-33-A safety flag; without it,
#      harvester_truth_writer is a no-op and every settlement is
#      `quarantine_reason=no_observation_for_target_date`.
#   1b. Inject `ZEUS_USER_CHANNEL_WS_ENABLED=1` and
#      `ZEUS_USER_CHANNEL_WS_AUTO_DERIVE=1` into both daemon plists.
#      Master toggle + auto-derive switch for the M3 Polymarket user-channel
#      WebSocket. With both set, the daemon derives the subscription set
#      from the live market scanner instead of a hardcoded plist value.
#      Operator can still pin a static list via
#      `POLYMARKET_USER_WS_CONDITION_IDS=…`; the env var, when present, wins.
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

# ---- 1) Inject env-var flags into both plists ----
# Parametrized so we can flip more than one toggle without copy/paste.
inject_flag() {
  local plist="$1"
  local var_name="$2"
  local var_value="${3:-1}"
  local key=":EnvironmentVariables:${var_name}"
  if [[ ! -f "$plist" ]]; then
    echo "WARN: $plist missing — skipping"
    return 0
  fi
  if /usr/libexec/PlistBuddy -c "Print $key" "$plist" >/dev/null 2>&1; then
    /usr/libexec/PlistBuddy -c "Set $key $var_value" "$plist"
    echo "  set    $plist :: ${var_name}=${var_value}"
  else
    /usr/libexec/PlistBuddy -c "Add $key string $var_value" "$plist"
    echo "  added  $plist :: ${var_name}=${var_value}"
  fi
}

echo "[arm_live_mode] step 1/3 — plist env vars"
for plist in \
    "$LAUNCHAGENTS/com.zeus.live-trading.plist" \
    "$LAUNCHAGENTS/com.zeus.data-ingest.plist"; do
  inject_flag "$plist" "ZEUS_HARVESTER_LIVE_ENABLED" "1"
  # User-channel WS auto-derive (2026-05-01): master toggle + auto-derive
  # switch. Without these, the daemon never starts the user-channel WS and
  # ws_user_channel.gap_reason='not_configured' blocks live submits.
  inject_flag "$plist" "ZEUS_USER_CHANNEL_WS_ENABLED" "1"
  inject_flag "$plist" "ZEUS_USER_CHANNEL_WS_AUTO_DERIVE" "1"
done

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

# ---- 3) Expire entries_paused override + clear auto-pause tombstone ----
echo "[arm_live_mode] step 3/3 — expire entries_paused + clear tombstone"
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

# control_overrides is a VIEW over control_overrides_history (B070), so
# direct UPDATE is illegal — must INSERT an 'expire' row via the
# canonical helper. The tombstone is a separate signal: when a cycle
# auto-pauses on an exception it writes
# state/auto_pause_failclosed.tombstone. is_entries_paused() OR's the
# override and the tombstone, so both must be cleared.
TOMBSTONE="state/auto_pause_failclosed.tombstone"
if [[ -f "$TOMBSTONE" ]]; then
  printf "  removing tombstone "
  cat "$TOMBSTONE"
  rm -f "$TOMBSTONE"
else
  echo "  no tombstone present"
fi

echo ""
echo "[arm_live_mode] DONE — daemons NOT loaded."
echo "  next step (operator-authorized):"
echo "    launchctl load $LAUNCHAGENTS/com.zeus.data-ingest.plist"
echo "    launchctl load $LAUNCHAGENTS/com.zeus.riskguard-live.plist"
echo "    launchctl load $LAUNCHAGENTS/com.zeus.live-trading.plist"
