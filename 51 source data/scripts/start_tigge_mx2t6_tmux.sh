#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="/Users/leofitz/miniconda3/bin/python"
SESSION_NAME="${1:-tigge-mx2t6}"

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
DRY_RUN="${DRY_RUN:-0}"
REQUIRE_PILOT_GATES="${REQUIRE_PILOT_GATES:-1}"

MANIFEST_PATH="${MANIFEST_PATH:-$ROOT/docs/tigge_city_coordinate_manifest_full_latest.json}"
MANIFEST_MD_PATH="${MANIFEST_MD_PATH:-$ROOT/docs/TIGGE_CITY_COORDINATE_MANIFEST_FULL_LATEST.md}"
AUDIT_PATH="${AUDIT_PATH:-$ROOT/tmp/tigge_mx2t6_source_manifest_audit.json}"
STATUS_PATH="${STATUS_PATH:-$ROOT/tmp/tigge_mx2t6_download_status.json}"
PILOT_GRIB_INTEGRITY_PATH="${PILOT_GRIB_INTEGRITY_PATH:-$ROOT/tmp/tigge_mx2t6_grib_integrity_pilot.json}"
PILOT_JSON_INTEGRITY_PATH="${PILOT_JSON_INTEGRITY_PATH:-$ROOT/tmp/tigge_mx2t6_json_integrity_pilot.json}"
PILOT_COVERAGE_PATH="${PILOT_COVERAGE_PATH:-$ROOT/tmp/tigge_mx2t6_coverage_pilot.json}"
EXTRA_DOWNLOAD_ARGS=""
if [[ "$DRY_RUN" == "1" ]]; then
  EXTRA_DOWNLOAD_ARGS="--dry-run"
fi
AUTH_EXPORT_LINE=""
if [[ -n "${ECMWF_API_RC_FILE:-}" ]]; then
  AUTH_EXPORT_LINE="export ECMWF_API_RC_FILE=\"$ECMWF_API_RC_FILE\""
fi

mkdir -p "$ROOT/logs" "$ROOT/tmp"
RUN_TS="$(date -u +"%Y%m%dT%H%M%SZ")"
SESSION_SLUG="$(echo "$SESSION_NAME" | tr ' /' '__')"
LOG_PATH="$ROOT/logs/tigge_mx2t6_download_${SESSION_SLUG}_${RUN_TS}.log"

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  echo "tmux session already exists: $SESSION_NAME"
  echo "attach: tmux attach -t $SESSION_NAME"
  exit 2
fi

if [[ "$REQUIRE_PILOT_GATES" == "1" ]]; then
  "$PYTHON_BIN" - <<PY
import json
import sys
from pathlib import Path

checks = [
    ("manifest_audit", Path(r"$AUDIT_PATH"), "ok"),
    ("pilot_grib_integrity", Path(r"$PILOT_GRIB_INTEGRITY_PATH"), "ok"),
    ("pilot_json_integrity", Path(r"$PILOT_JSON_INTEGRITY_PATH"), "ok"),
    ("pilot_coverage", Path(r"$PILOT_COVERAGE_PATH"), "ok"),
]

missing = []
failing = []
for label, path, key in checks:
    if not path.exists():
        missing.append({"check": label, "path": str(path), "reason": "missing_file"})
        continue
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        failing.append({"check": label, "path": str(path), "reason": f"invalid_json:{exc}"})
        continue
    if payload.get(key) is not True:
        failing.append({"check": label, "path": str(path), "reason": f"{key}_is_not_true"})

if missing or failing:
    print("Pilot gates not satisfied; refusing to start full mx2t6 download.")
    for row in missing:
        print(f"  MISSING {row['check']}: {row['path']}")
    for row in failing:
        print(f"  FAIL {row['check']}: {row['path']} ({row['reason']})")
    print("Set REQUIRE_PILOT_GATES=0 only for emergency/manual override.")
    sys.exit(3)
PY
fi

COMMAND=$(cat <<EOF
cd "$ROOT"
set -euo pipefail
$AUTH_EXPORT_LINE
echo "[\$(date -u +%FT%TZ)] stage=manifest_generate"
"$PYTHON_BIN" scripts/generate_full_tigge_manifest_from_geocoding.py --json-out "$MANIFEST_PATH" --md-out "$MANIFEST_MD_PATH"
echo "[\$(date -u +%FT%TZ)] stage=manifest_audit"
"$PYTHON_BIN" scripts/audit_tigge_manifest_against_cities.py --manifest-path "$MANIFEST_PATH" --output "$AUDIT_PATH"
echo "[\$(date -u +%FT%TZ)] stage=download_start date_from=$DATE_FROM date_to=$DATE_TO"
"$PYTHON_BIN" scripts/tigge_mx2t6_download_resumable.py \\
  --manifest-path "$MANIFEST_PATH" \\
  --status-path "$STATUS_PATH" \\
  --date-from "$DATE_FROM" \\
  --date-to "$DATE_TO" \\
  --max-batch-days "$MAX_BATCH_DAYS" \\
  --max-workers "$MAX_WORKERS" \\
  --sleep-seconds "$SLEEP_SECONDS" \\
  --max-passes "$MAX_PASSES" \\
  --retry-stall-limit "$RETRY_STALL_LIMIT" $EXTRA_DOWNLOAD_ARGS
echo "[\$(date -u +%FT%TZ)] stage=complete"
EOF
)

tmux new-session -d -s "$SESSION_NAME" "bash -lc '$COMMAND' 2>&1 | tee -a \"$LOG_PATH\""

echo "started tmux session: $SESSION_NAME"
echo "attach: tmux attach -t $SESSION_NAME"
echo "log: $LOG_PATH"
echo "status: $STATUS_PATH"
