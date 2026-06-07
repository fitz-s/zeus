#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="/Users/leofitz/miniconda3/bin/python"

ACCOUNT1_RC="${ACCOUNT1_RC:-/Users/leofitz/.ecmwfapirc}"
ACCOUNT2_RC="${ACCOUNT2_RC:-/Users/leofitz/.openclaw/secrets/ecmwf/account2.ecmwfapirc}"
SESSION1="${SESSION1:-tigge-mx2t6-a1}"
SESSION2="${SESSION2:-tigge-mx2t6-a2}"

DATE_FROM="${DATE_FROM:-2024-01-01}"
DATE_TO="${DATE_TO:-$("$PYTHON_BIN" - <<'PY'
from datetime import date, timedelta
print((date.today() - timedelta(days=2)).isoformat())
PY
)}"
SPLIT_DATE="${SPLIT_DATE:-$("$PYTHON_BIN" - <<'PY'
from datetime import date, timedelta
start=date.fromisoformat("2024-01-01")
end=date.today()-timedelta(days=2)
mid=start + timedelta(days=((end-start).days)//2)
print(mid.isoformat())
PY
)}"

MAX_BATCH_DAYS="${MAX_BATCH_DAYS:-3}"
MAX_WORKERS="${MAX_WORKERS:-2}"
SLEEP_SECONDS="${SLEEP_SECONDS:-180}"
MAX_PASSES="${MAX_PASSES:-2000}"
RETRY_STALL_LIMIT="${RETRY_STALL_LIMIT:-8}"

if [[ ! -f "$ACCOUNT1_RC" ]]; then
  echo "missing account1 rc: $ACCOUNT1_RC"
  exit 2
fi
if [[ ! -f "$ACCOUNT2_RC" ]]; then
  echo "missing account2 rc: $ACCOUNT2_RC"
  exit 2
fi

if tmux has-session -t "$SESSION1" 2>/dev/null; then
  echo "tmux session exists: $SESSION1"
  exit 2
fi
if tmux has-session -t "$SESSION2" 2>/dev/null; then
  echo "tmux session exists: $SESSION2"
  exit 2
fi

DATE2_FROM="$("$PYTHON_BIN" - <<PY
from datetime import date, timedelta
split=date.fromisoformat("$SPLIT_DATE")
print((split+timedelta(days=1)).isoformat())
PY
)"

echo "dual-shard plan:"
echo "  shard1: $DATE_FROM -> $SPLIT_DATE (session=$SESSION1)"
echo "  shard2: $DATE2_FROM -> $DATE_TO (session=$SESSION2)"

cd "$ROOT"

ECMWF_API_RC_FILE="$ACCOUNT1_RC" \
STATUS_PATH="$ROOT/tmp/tigge_mx2t6_download_status_a1.json" \
DATE_FROM="$DATE_FROM" \
DATE_TO="$SPLIT_DATE" \
MAX_BATCH_DAYS="$MAX_BATCH_DAYS" \
MAX_WORKERS="$MAX_WORKERS" \
SLEEP_SECONDS="$SLEEP_SECONDS" \
MAX_PASSES="$MAX_PASSES" \
RETRY_STALL_LIMIT="$RETRY_STALL_LIMIT" \
scripts/start_tigge_mx2t6_tmux.sh "$SESSION1"

ECMWF_API_RC_FILE="$ACCOUNT2_RC" \
STATUS_PATH="$ROOT/tmp/tigge_mx2t6_download_status_a2.json" \
DATE_FROM="$DATE2_FROM" \
DATE_TO="$DATE_TO" \
MAX_BATCH_DAYS="$MAX_BATCH_DAYS" \
MAX_WORKERS="$MAX_WORKERS" \
SLEEP_SECONDS="$SLEEP_SECONDS" \
MAX_PASSES="$MAX_PASSES" \
RETRY_STALL_LIMIT="$RETRY_STALL_LIMIT" \
scripts/start_tigge_mx2t6_tmux.sh "$SESSION2"

echo "started both sessions:"
echo "  tmux attach -t $SESSION1"
echo "  tmux attach -t $SESSION2"
echo "status files:"
echo "  $ROOT/tmp/tigge_mx2t6_download_status_a1.json"
echo "  $ROOT/tmp/tigge_mx2t6_download_status_a2.json"
