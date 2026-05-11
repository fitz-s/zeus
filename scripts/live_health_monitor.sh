#!/bin/bash
# Created: 2026-05-11
# Last reused/audited: 2026-05-11
# Authority basis: Live monitoring during first-order qualification (operator directive 2026-05-11)
#
# Polls scripts/live_health_probe.py every 60s, but emits one line only when:
#   - state transitions (e.g., OK→ALERT or ALERT→OK or any flag set changes)
#   - 30-minute heartbeat tick (one OK summary per half hour to confirm probe alive)
# Stdout lines are events; the Monitor tool turns each into a notification.

cd /Users/leofitz/.openclaw/workspace-venus/zeus

LAST_STATE_FILE=/tmp/zeus_monitor_last_state.txt
LAST_TICK_FILE=/tmp/zeus_monitor_last_tick.txt
touch "$LAST_STATE_FILE" "$LAST_TICK_FILE"

while true; do
  output=$(.venv/bin/python scripts/live_health_probe.py 2>&1)
  # Extract the flags= component as the state signature
  state_sig=$(echo "$output" | grep -oE 'flags=[^ ]+' | head -1)
  last_state=$(cat "$LAST_STATE_FILE" 2>/dev/null)
  now_epoch=$(date +%s)
  last_tick=$(cat "$LAST_TICK_FILE" 2>/dev/null || echo 0)
  elapsed=$((now_epoch - last_tick))

  if [ "$state_sig" != "$last_state" ]; then
    echo "$output"
    echo "$state_sig" > "$LAST_STATE_FILE"
    echo "$now_epoch" > "$LAST_TICK_FILE"
  elif [ $elapsed -ge 1800 ]; then
    # 30-min keepalive
    echo "$output"
    echo "$now_epoch" > "$LAST_TICK_FILE"
  fi

  sleep 60
done
