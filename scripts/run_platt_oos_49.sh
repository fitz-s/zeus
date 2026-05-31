#!/bin/bash
# Created: 2026-05-31
# Authority basis: operator "platt只有4个城市/现在就做platt" — all 49 tradeable cities have
#   ~780k HIGH calibration_pairs each; the 8-city OOS scope was arbitrary, not data-bound.
#   Compete Platt OOS across the remaining 41 tradeable cities (the original 8 already scored
#   in /tmp/platt_verdict_full.tsv). Read-heavy OOS scoring; 3-way parallel (WAL-safe).
# Output: per-city raw output in /tmp/platt49/<city>.out ; progress in /tmp/platt49/progress.log.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"
source .venv/bin/activate 2>/dev/null
mkdir -p /tmp/platt49

# 41 remaining tradeable cities (49 tradeable minus the 8 already scored:
# Hong Kong, London, Miami, NYC, Paris, Seoul, Shanghai, Tokyo).
CITIES=(
  "Amsterdam" "Ankara" "Atlanta" "Austin" "Beijing" "Buenos Aires" "Busan"
  "Cape Town" "Chengdu" "Chicago" "Chongqing" "Dallas" "Denver" "Guangzhou"
  "Helsinki" "Houston" "Istanbul" "Jeddah" "Karachi" "Kuala Lumpur"
  "Los Angeles" "Lucknow" "Madrid" "Manila" "Mexico City" "Milan" "Moscow"
  "Munich" "Panama City" "Qingdao" "San Francisco" "Sao Paulo" "Seattle"
  "Shenzhen" "Singapore" "Taipei" "Tel Aviv" "Toronto" "Warsaw" "Wellington"
  "Wuhan"
)
MAXPAR=3
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) START platt49 n=${#CITIES[@]} maxpar=$MAXPAR" >> /tmp/platt49/progress.log

run_city() {
  local c="$1"
  local safe="${c// /_}"
  local t0=$(date -u +%s)
  python scripts/run_platt_oos_scoring.py --city "$c" > "/tmp/platt49/${safe}.out" 2>&1
  local rc=$?
  local t1=$(date -u +%s)
  local verdict=$(grep -oE "PROMOTE|IDENTITY" "/tmp/platt49/${safe}.out" | tail -1)
  echo "$(date -u +%H:%M:%S) DONE c='$c' rc=$rc dur=$((t1-t0))s verdict=${verdict:-NONE}" >> /tmp/platt49/progress.log
}

i=0
for c in "${CITIES[@]}"; do
  run_city "$c" &
  i=$((i+1))
  if (( i % MAXPAR == 0 )); then wait -n 2>/dev/null || wait; fi
done
wait
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ALL_DONE platt49" >> /tmp/platt49/progress.log
