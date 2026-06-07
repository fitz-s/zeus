#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="/Users/leofitz/miniconda3/bin/python"

ACCOUNT1_RC="${ACCOUNT1_RC:-/Users/leofitz/.ecmwfapirc}"
ACCOUNT2_RC="${ACCOUNT2_RC:-/Users/leofitz/.openclaw/secrets/ecmwf/account2.ecmwfapirc}"
ACCOUNT3_RC="${ACCOUNT3_RC:-/Users/leofitz/.openclaw/secrets/ecmwf/account3.ecmwfapirc}"
ACCOUNT4_RC="${ACCOUNT4_RC:-/Users/leofitz/.openclaw/secrets/ecmwf/account4.ecmwfapirc}"
ACCOUNT5_RC="${ACCOUNT5_RC:-/Users/leofitz/.openclaw/secrets/ecmwf/account5.ecmwfapirc}"
ENABLE_ACCOUNT4="${ENABLE_ACCOUNT4:-1}"
ENABLE_ACCOUNT5="${ENABLE_ACCOUNT5:-1}"
TIGGE_ACCOUNT_LIMIT="${TIGGE_ACCOUNT_LIMIT:-2}"

SESSION1="${SESSION1:-tigge-mn2t6-a1}"
SESSION2="${SESSION2:-tigge-mn2t6-a2}"
SESSION3="${SESSION3:-tigge-mn2t6-a3}"
SESSION4="${SESSION4:-tigge-mn2t6-a4}"
SESSION5="${SESSION5:-tigge-mn2t6-a5}"
PROGRESS_SESSION="${PROGRESS_SESSION:-tigge-progress}"

DATE_FROM="${DATE_FROM:-2024-01-01}"
DATE_TO="${DATE_TO:-$("$PYTHON_BIN" - <<'PY'
from datetime import date, timedelta
print((date.today() - timedelta(days=2)).isoformat())
PY
)}"

MAX_BATCH_DAYS="${MAX_BATCH_DAYS:-3}"
MAX_WORKERS="${MAX_WORKERS:-1}"
SLEEP_SECONDS="${SLEEP_SECONDS:-180}"
MAX_PASSES="${MAX_PASSES:-2000}"
RETRY_STALL_LIMIT="${RETRY_STALL_LIMIT:-60}"
REQUIRE_PILOT_GATES="${REQUIRE_PILOT_GATES:-0}"

EXPECTED_FILES="${EXPECTED_FILES:-2232}"
PROGRESS_INTERVAL_SECONDS="${PROGRESS_INTERVAL_SECONDS:-2}"
PROGRESS_DECIMALS="${PROGRESS_DECIMALS:-8}"

STATUS1="$ROOT/tmp/tigge_mn2t6_download_status_a1.json"
STATUS2="$ROOT/tmp/tigge_mn2t6_download_status_a2.json"
STATUS3="$ROOT/tmp/tigge_mn2t6_download_status_a3.json"
STATUS4="$ROOT/tmp/tigge_mn2t6_download_status_a4.json"
STATUS5="$ROOT/tmp/tigge_mn2t6_download_status_a5.json"

account_rcs=("$ACCOUNT1_RC" "$ACCOUNT2_RC" "$ACCOUNT3_RC")
sessions=("$SESSION1" "$SESSION2" "$SESSION3")
status_paths=("$STATUS1" "$STATUS2" "$STATUS3")

if [[ "$ENABLE_ACCOUNT4" == "1" ]]; then
  if [[ -f "$ACCOUNT4_RC" ]]; then
    account_rcs+=("$ACCOUNT4_RC")
    sessions+=("$SESSION4")
    status_paths+=("$STATUS4")
  else
    echo "account4 not enabled (missing rc): $ACCOUNT4_RC" >&2
  fi
fi

if [[ "$ENABLE_ACCOUNT5" == "1" ]]; then
  if [[ -f "$ACCOUNT5_RC" ]]; then
    account_rcs+=("$ACCOUNT5_RC")
    sessions+=("$SESSION5")
    status_paths+=("$STATUS5")
  else
    echo "account5 not enabled (missing rc): $ACCOUNT5_RC" >&2
  fi
fi

if (( TIGGE_ACCOUNT_LIMIT < 1 )); then
  echo "TIGGE_ACCOUNT_LIMIT must be >= 1"
  exit 2
fi
if (( TIGGE_ACCOUNT_LIMIT < ${#account_rcs[@]} )); then
  account_rcs=("${account_rcs[@]:0:$TIGGE_ACCOUNT_LIMIT}")
  sessions=("${sessions[@]:0:$TIGGE_ACCOUNT_LIMIT}")
  status_paths=("${status_paths[@]:0:$TIGGE_ACCOUNT_LIMIT}")
fi

for path in "${account_rcs[@]}"; do
  if [[ ! -f "$path" ]]; then
    echo "missing account rc: $path"
    exit 2
  fi
done

lane_count="${#account_rcs[@]}"

eval "$("$PYTHON_BIN" - <<PY
from datetime import date, timedelta
start=date.fromisoformat("$DATE_FROM")
end=date.fromisoformat("$DATE_TO")
if end < start:
    raise SystemExit("DATE_TO must be >= DATE_FROM")
days=(end-start).days+1
lane_count=int("$lane_count")
base=days//lane_count
rem=days%lane_count
cursor=start
for idx in range(lane_count):
    span=base+(1 if idx < rem else 0)
    shard_from=cursor
    shard_to=cursor+timedelta(days=span-1)
    print(f'SHARD{idx+1}_FROM={shard_from.isoformat()}')
    print(f'SHARD{idx+1}_TO={shard_to.isoformat()}')
    cursor=shard_to+timedelta(days=1)
PY
)"

should_start_session() {
  local status_path="$1"
  local expected_from="$2"
  local expected_to="$3"
  if [[ ! -f "$status_path" ]]; then return 0; fi
  local status
  status=$("$PYTHON_BIN" - <<PY
import json
from pathlib import Path
p=Path(r"$status_path")
try: d=json.loads(p.read_text(encoding="utf-8"))
except Exception: print("unknown"); raise SystemExit(0)
if d.get("date_from") != "$expected_from" or d.get("date_to") != "$expected_to":
    print("incomplete")
    raise SystemExit(0)
s=d.get("status"); missing=d.get("missing_tasks")
print("complete" if s == "complete" or missing == 0 else "incomplete")
PY
)
  [[ "$status" != "complete" ]]
}

ensure_session() {
  local session="$1" rc="$2" status_path="$3" shard_from="$4" shard_to="$5"
  if tmux has-session -t "$session" 2>/dev/null; then return 0; fi
  if ! should_start_session "$status_path" "$shard_from" "$shard_to"; then return 0; fi
  ECMWF_API_RC_FILE="$rc" STATUS_PATH="$status_path" DATE_FROM="$shard_from" DATE_TO="$shard_to" MAX_BATCH_DAYS="$MAX_BATCH_DAYS" MAX_WORKERS="$MAX_WORKERS" SLEEP_SECONDS="$SLEEP_SECONDS" MAX_PASSES="$MAX_PASSES" RETRY_STALL_LIMIT="$RETRY_STALL_LIMIT" REQUIRE_PILOT_GATES="$REQUIRE_PILOT_GATES" "$ROOT/scripts/start_tigge_mn2t6_tmux.sh" "$session"
}

ensure_progress() {
  if tmux has-session -t "$PROGRESS_SESSION" 2>/dev/null; then return 0; fi
  tmux new-session -d -s "$PROGRESS_SESSION" "cd '$ROOT' && $PYTHON_BIN scripts/tigge_region_progress_bars.py --one-line --single-line --expected '$EXPECTED_FILES' --lanes '$lane_count' --bar-width 10 --interval-seconds '$PROGRESS_INTERVAL_SECONDS' --decimals '$PROGRESS_DECIMALS'"
}

for idx in "${!sessions[@]}"; do
  shard_num=$((idx + 1))
  shard_from_var="SHARD${shard_num}_FROM"
  shard_to_var="SHARD${shard_num}_TO"
  ensure_session \
    "${sessions[$idx]}" \
    "${account_rcs[$idx]}" \
    "${status_paths[$idx]}" \
    "${!shard_from_var}" \
    "${!shard_to_var}"
done
ensure_progress
