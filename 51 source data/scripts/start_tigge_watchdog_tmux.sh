#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/leofitz/.openclaw/workspace-venus/51 source data"
PYTHON_BIN="/Users/leofitz/miniconda3/bin/python"
SESSION_NAME="${1:-tigge-watchdog}"

LANES="${LANES:-2}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-300}"
STALL_MINUTES="${STALL_MINUTES:-180}"
STALE_MINUTES="${STALE_MINUTES:-20}"

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  echo "tmux session already exists: $SESSION_NAME"
  echo "attach: tmux attach -t $SESSION_NAME"
  exit 0
fi

tmux new-session -d -s "$SESSION_NAME" \
  "cd '$ROOT' && '$PYTHON_BIN' scripts/tigge_watchdog.py --lanes '$LANES' --interval-seconds '$INTERVAL_SECONDS' --stall-minutes '$STALL_MINUTES' --stale-minutes '$STALE_MINUTES'"

echo "started tmux session: $SESSION_NAME"
echo "attach: tmux attach -t $SESSION_NAME"
