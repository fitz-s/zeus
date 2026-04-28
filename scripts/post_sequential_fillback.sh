#!/bin/bash
# Post-sequential-backfill fill-back pass.
#
# Waits for the running sequential backfill wrapper (PID passed as $1) to exit,
# then runs a `--missing-only` pass over WU to pick up any days that were
# rejected by the now-deleted IngestionGuard Layer 3 before the deletion took
# effect. The WU process running during Step 1 imported Layer 3 into memory
# before the code edit and will continue to reject legitimate hot/warm days
# (Austin 97°F March, Sao Paulo 92°F May, etc.) until it exits. This script
# fills those rejected days afterwards using the new Layer-3-free guard.
#
# It also runs a first scan pass of the K2 hole_scanner against the live
# zeus-world.db so operators have an immediate `data_coverage` ledger after
# the backfill completes.
#
# Usage (from zeus/):
#   nohup bash scripts/post_sequential_fillback.sh <sequential_pid> \
#        > state/fillback-nohup-$(date +%Y%m%d-%H%M%S).out 2>&1 &
#   disown
set -euo pipefail

cd "$(dirname "$0")/.."

if [ $# -lt 1 ]; then
    echo "usage: $0 <sequential_wrapper_pid>" >&2
    exit 64
fi
WAIT_PID="$1"
TS=$(date +%Y%m%d-%H%M%S)
LOG="state/fillback-sequential-${TS}.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"
}

log "=== Post-sequential fill-back started ==="
log "Waiting for PID $WAIT_PID to exit (sequential backfill)..."

# Poll every 60s until the sequential wrapper exits.
while kill -0 "$WAIT_PID" 2>/dev/null; do
    sleep 60
done
log "Sequential backfill PID $WAIT_PID has exited. Beginning fill-back."

# ---------------------------------------------------------------------------
# Step A: WU --all --missing-only — fill back rejected-by-old-Layer-3 days
# ---------------------------------------------------------------------------
# --all: iterate every city in CITY_STATIONS regardless of skip-if-present
# --missing-only: _dates_needing_fetch() returns dates where (target_date) is
#   NOT in observations WHERE source='wu_icao_history'. Rejected days are
#   not in observations (guard raised before the INSERT), so they qualify.
# --days 834: same horizon as the main backfill.
#
# Expected duration: 30-60 min (only fetches missing dates, ~20-50 per city).
#
# WU calls require an operator-provided key. Do not embed transition keys in
# active scripts; shell parameter expansion fails closed before any fetch.
: "${WU_API_KEY:?WU_API_KEY must be set in the operator environment before running WU fillback}"
export WU_API_KEY

log "--- Step A: WU --all --missing-only ---"
python scripts/backfill_wu_daily_all.py --all --missing-only --days 834 2>&1 | tee -a "$LOG"
log "Step A done."

# ---------------------------------------------------------------------------
# Step B: HKO missing-month refresh (HK-only, current+prior month)
# ---------------------------------------------------------------------------
# HKO backfill is month-oriented, so re-running it catches any days that
# flipped from "#" to "C" since the last run, plus any Layer-3 rejections
# (HK was not hitting Layer 3 in the log but we do this defensively).
log "--- Step B: HKO current+prior month refresh ---"
PRIOR=$(date -v-1m +%Y-%m 2>/dev/null || date -d '1 month ago' +%Y-%m)
CURR=$(date +%Y-%m)
python scripts/backfill_hko_daily.py --start "$PRIOR" --end "$CURR" 2>&1 | tee -a "$LOG"
log "Step B done."

# ---------------------------------------------------------------------------
# Step C: K2 hole_scanner — populate data_coverage ledger for first time
# ---------------------------------------------------------------------------
log "--- Step C: hole_scanner --scan all ---"
python -m src.data.hole_scanner --scan all 2>&1 | tee -a "$LOG"
log "Step C done."

# ---------------------------------------------------------------------------
# Final row counts + coverage report
# ---------------------------------------------------------------------------
log "--- Final row counts ---"
python - <<'PY' 2>&1 | tee -a "$LOG"
from src.state.db import get_world_connection
c = get_world_connection()
for t in ['observations', 'observation_instants', 'solar_daily', 'forecasts']:
    r = c.execute(f'SELECT COUNT(*), COUNT(DISTINCT city) FROM {t}').fetchone()
    print(f'  {t:24s} rows={r[0]:>10,} cities={r[1]}')
print('\n  observations by source:')
for src, n, nc in c.execute("SELECT source, COUNT(*), COUNT(DISTINCT city) FROM observations GROUP BY source").fetchall():
    print(f'    {src:25s} rows={n:>8,} cities={nc}')
c.close()
PY

log "--- K2 coverage report ---"
python -m src.data.hole_scanner --report 2>&1 | tee -a "$LOG"

log "=== Post-sequential fill-back completed successfully ==="
