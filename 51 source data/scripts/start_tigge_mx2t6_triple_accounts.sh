#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/leofitz/.openclaw/workspace-venus/51 source data"
PYTHON_BIN="/Users/leofitz/miniconda3/bin/python"

ACCOUNT1_RC="${ACCOUNT1_RC:-/Users/leofitz/.ecmwfapirc}"
ACCOUNT2_RC="${ACCOUNT2_RC:-/Users/leofitz/.openclaw/secrets/ecmwf/account2.ecmwfapirc}"
ACCOUNT3_RC="${ACCOUNT3_RC:-/Users/leofitz/.openclaw/secrets/ecmwf/account3.ecmwfapirc}"
ACCOUNT4_RC="${ACCOUNT4_RC:-/Users/leofitz/.openclaw/secrets/ecmwf/account4.ecmwfapirc}"
ACCOUNT5_RC="${ACCOUNT5_RC:-/Users/leofitz/.openclaw/secrets/ecmwf/account5.ecmwfapirc}"
ENABLE_ACCOUNT4="${ENABLE_ACCOUNT4:-1}"
ENABLE_ACCOUNT5="${ENABLE_ACCOUNT5:-1}"

SESSION1="${SESSION1:-tigge-mx2t6-a1}"
SESSION2="${SESSION2:-tigge-mx2t6-a2}"
SESSION3="${SESSION3:-tigge-mx2t6-a3}"
SESSION4="${SESSION4:-tigge-mx2t6-a4}"
SESSION5="${SESSION5:-tigge-mx2t6-a5}"

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
RETRY_STALL_LIMIT="${RETRY_STALL_LIMIT:-8}"

account_rcs=("$ACCOUNT1_RC" "$ACCOUNT2_RC" "$ACCOUNT3_RC")
sessions=("$SESSION1" "$SESSION2" "$SESSION3")
status_paths=(
  "$ROOT/tmp/tigge_mx2t6_download_status_a1.json"
  "$ROOT/tmp/tigge_mx2t6_download_status_a2.json"
  "$ROOT/tmp/tigge_mx2t6_download_status_a3.json"
)

if [[ "$ENABLE_ACCOUNT4" == "1" ]]; then
  if [[ -f "$ACCOUNT4_RC" ]]; then
    account_rcs+=("$ACCOUNT4_RC")
    sessions+=("$SESSION4")
    status_paths+=("$ROOT/tmp/tigge_mx2t6_download_status_a4.json")
  else
    echo "account4 not enabled (missing rc): $ACCOUNT4_RC" >&2
  fi
fi

if [[ "$ENABLE_ACCOUNT5" == "1" ]]; then
  if [[ -f "$ACCOUNT5_RC" ]]; then
    account_rcs+=("$ACCOUNT5_RC")
    sessions+=("$SESSION5")
    status_paths+=("$ROOT/tmp/tigge_mx2t6_download_status_a5.json")
  else
    echo "account5 not enabled (missing rc): $ACCOUNT5_RC" >&2
  fi
fi

for path in "${account_rcs[@]}"; do
  if [[ ! -f "$path" ]]; then
    echo "missing account rc: $path"
    exit 2
  fi
done

for session in "${sessions[@]}"; do
  if tmux has-session -t "$session" 2>/dev/null; then
    echo "tmux session exists: $session"
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

echo "shard plan (${lane_count} lanes):"
for idx in "${!sessions[@]}"; do
  shard_num=$((idx + 1))
  shard_from_var="SHARD${shard_num}_FROM"
  shard_to_var="SHARD${shard_num}_TO"
  echo "  shard${shard_num}: ${!shard_from_var} -> ${!shard_to_var} (session=${sessions[$idx]})"
done

cd "$ROOT"

for idx in "${!sessions[@]}"; do
  shard_num=$((idx + 1))
  shard_from_var="SHARD${shard_num}_FROM"
  shard_to_var="SHARD${shard_num}_TO"
  ECMWF_API_RC_FILE="${account_rcs[$idx]}" \
  STATUS_PATH="${status_paths[$idx]}" \
  DATE_FROM="${!shard_from_var}" \
  DATE_TO="${!shard_to_var}" \
  MAX_BATCH_DAYS="$MAX_BATCH_DAYS" \
  MAX_WORKERS="$MAX_WORKERS" \
  SLEEP_SECONDS="$SLEEP_SECONDS" \
  MAX_PASSES="$MAX_PASSES" \
  RETRY_STALL_LIMIT="$RETRY_STALL_LIMIT" \
  scripts/start_tigge_mx2t6_tmux.sh "${sessions[$idx]}"
done

echo "started ${lane_count} sessions:"
for session in "${sessions[@]}"; do
  echo "  tmux attach -t $session"
done
echo "status files:"
for status_path in "${status_paths[@]}"; do
  echo "  $status_path"
done
