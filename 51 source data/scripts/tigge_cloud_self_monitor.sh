#!/usr/bin/env bash
set -euo pipefail

CLOUD_ROOT="${CLOUD_ROOT:-/data/tigge}"
CLOUD_DATA_ROOT="${CLOUD_DATA_ROOT:-$CLOUD_ROOT/zeus/51 source data}"
CLOUD_BUNDLE_ROOT="${CLOUD_BUNDLE_ROOT:-$HOME/tigge_bundle}"
ACCOUNT_LIMIT="${ACCOUNT_LIMIT:-5}"
MAX_WORKERS="${MAX_WORKERS:-2}"
EXPECTED_TOTAL="${EXPECTED_TOTAL:-4464}"
MIN_FREE_GB="${MIN_FREE_GB:-150}"
DATE_FROM="${DATE_FROM:-2024-01-01}"
DATE_TO="${DATE_TO:-}"

REPORT_DIR="${REPORT_DIR:-$CLOUD_ROOT/logs/self_monitor}"
mkdir -p "$REPORT_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
report="$REPORT_DIR/tigge_self_monitor_${stamp}.log"
latest="$REPORT_DIR/tigge_self_monitor_latest.log"

exec > >(tee "$report") 2>&1

echo "timestamp_utc=$(date -u +%FT%TZ)"
echo "cloud_root=$CLOUD_ROOT"
echo "data_root=$CLOUD_DATA_ROOT"
echo "account_limit=$ACCOUNT_LIMIT"
echo "max_workers=$MAX_WORKERS"
echo "date_from=$DATE_FROM"
echo "date_to=${DATE_TO:-dynamic}"
export CLOUD_DATA_ROOT

if ! findmnt -T "$CLOUD_ROOT" >/dev/null 2>&1; then
  echo "status=CRITICAL reason=data_disk_not_mounted"
  ln -sf "$report" "$latest"
  exit 2
fi

available_gb="$(df -BG "$CLOUD_ROOT" | awk 'NR==2 {gsub(/G/, "", $4); print $4}')"
echo "available_gb=$available_gb"
if [[ "${available_gb:-0}" -lt "$MIN_FREE_GB" ]]; then
  echo "status=CRITICAL reason=low_disk_space min_free_gb=$MIN_FREE_GB"
  ln -sf "$report" "$latest"
  exit 2
fi

if [[ ! -x "$CLOUD_BUNDLE_ROOT/tigge_gce_trial.sh" ]]; then
  echo "status=CRITICAL reason=missing_gce_helper path=$CLOUD_BUNDLE_ROOT/tigge_gce_trial.sh"
  ln -sf "$report" "$latest"
  exit 2
fi

if [[ ! -d "$CLOUD_DATA_ROOT" ]]; then
  echo "status=CRITICAL reason=missing_data_root"
  ln -sf "$report" "$latest"
  exit 2
fi

missing_sessions=()
for track in mx2t6 mn2t6; do
  for lane in $(seq 1 "$ACCOUNT_LIMIT"); do
    session="tigge-${track}-a${lane}"
    if ! tmux has-session -t "$session" 2>/dev/null; then
      missing_sessions+=("$session")
    fi
  done
done
if ! tmux has-session -t tigge-progress 2>/dev/null; then
  missing_sessions+=("tigge-progress")
fi
if ! tmux has-session -t tigge-watchdog 2>/dev/null; then
  missing_sessions+=("tigge-watchdog")
fi

if (( ${#missing_sessions[@]} > 0 )); then
  echo "missing_sessions=${missing_sessions[*]}"
  echo "action=remote_start_for_missing_sessions"
  (
    cd "$CLOUD_BUNDLE_ROOT"
    if [[ -n "$DATE_TO" ]]; then
      DATE_FROM="$DATE_FROM" DATE_TO="$DATE_TO" ACCOUNT_LIMIT="$ACCOUNT_LIMIT" MAX_WORKERS="$MAX_WORKERS" ./tigge_gce_trial.sh remote-start
    else
      DATE_FROM="$DATE_FROM" ACCOUNT_LIMIT="$ACCOUNT_LIMIT" MAX_WORKERS="$MAX_WORKERS" ./tigge_gce_trial.sh remote-start
    fi
  )
else
  echo "missing_sessions=none"
fi

echo "--- remote_health ---"
(
  cd "$CLOUD_BUNDLE_ROOT"
  ./tigge_gce_trial.sh remote-health
)

echo "--- aggregate_counts ---"
python_bin="$CLOUD_ROOT/venv/bin/python"
"$python_bin" - <<'PY'
from pathlib import Path
from datetime import datetime
import os
import re

root = Path(os.environ["CLOUD_DATA_ROOT"])
logs = root / "logs"
raw = root / "raw"
expected_total = 4464

counts = {}
for track in ("mx2t6", "mn2t6"):
    track_root = raw / f"tigge_ecmwf_ens_regions_{track}"
    counts[track] = len(list(track_root.rglob("*.grib.ok"))) if track_root.exists() else 0
total = sum(counts.values())
print(f"ok_counts mx2t6={counts['mx2t6']} mn2t6={counts['mn2t6']} total={total}/{expected_total} pct={total/expected_total*100:.4f}")

ts_re = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')
log_re = re.compile(r'tigge_(mx2t6|mn2t6)_download_(tigge-[^-]+-a\d)_')
now = datetime.now().astimezone()
done = []
retry = []
for path in logs.glob("tigge_*_download_tigge-*-a*.log"):
    m = log_re.search(path.name)
    if not m:
        continue
    current = None
    for line in path.read_text(errors="ignore").splitlines():
        tm = ts_re.match(line)
        if tm:
            try:
                current = datetime.strptime(tm.group(1), "%Y-%m-%d %H:%M:%S").astimezone()
            except Exception:
                current = None
        if current is None:
            continue
        if '"event": "request_done"' in line:
            done.append(current)
        if "ConnectionReset" in line or "Transfer interrupted" in line or '"event": "file_retry"' in line:
            retry.append(current)
for minutes in (30, 60, 120, 240):
    d = [x for x in done if (now - x).total_seconds() <= minutes * 60]
    r = [x for x in retry if (now - x).total_seconds() <= minutes * 60]
    print(f"rate_window minutes={minutes} done={len(d)} rate_per_hour={len(d)/(minutes/60):.2f} retry={len(r)}")
PY

echo "status=OK"
ln -sf "$report" "$latest"
