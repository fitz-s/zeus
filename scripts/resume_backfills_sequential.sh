#!/bin/bash
# Sequential resume of the 4 broken concurrent backfill jobs.
#
# Why this script exists:
# The original parallel approach (5 backfills × 1 shared SQLite DB) died from
# write-lock contention even with WAL. WU crashed hard at Atlanta's first
# INSERT; hourly/solar/previous_runs each completed only a fraction of their
# 46-city sweep. This script runs the 4 remaining backfills ONE AT A TIME so
# each gets 100% of the DB write lock, eliminating lock contention entirely.
#
# Cities with stale/partial data are deleted first so the skip-if-present
# default logic in each script re-fetches them fully. Cities already complete
# (NYC in observations, ~14 cities in observation_instants, etc.) are skipped
# automatically and zero API quota is wasted on them.
#
# Design contract:
# - Runs detached via nohup so Claude Code lifecycle (compact, exit, crash) is
#   irrelevant to the job. Log file is the single source of truth for progress.
# - Fail-fast on any step (set -e). A DB error or network failure halts the
#   whole pipeline rather than silently producing half-baked derived tables.
# - Final row-count sanity check at the end so a quick tail of the log tells
#   operator whether the rebuild succeeded.
#
# Usage (from zeus/):
#   nohup bash scripts/resume_backfills_sequential.sh \
#        > state/rebuild-nohup-$(date +%Y%m%d-%H%M%S).out 2>&1 &
#   disown
#   # Progress:  tail -f state/rebuild-sequential-*.log
set -euo pipefail

cd "$(dirname "$0")/.."

TS=$(date +%Y%m%d-%H%M%S)
LOG="state/rebuild-sequential-${TS}.log"
DB="state/zeus-world.db"

# WU calls require an operator-provided key. Do not embed transition keys in
# active scripts; shell parameter expansion fails closed before any fetch.
: "${WU_API_KEY:?WU_API_KEY must be set in the operator environment before running WU backfill}"
export WU_API_KEY

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"
}

log "=== Sequential backfill resume started ==="
log "Log file: $LOG"
log "PID: $$"

# ---------------------------------------------------------------------------
# Step 0: Clear partial cities so skip-if-present fully re-fetches them.
# ---------------------------------------------------------------------------
log "--- Step 0: Clear partial-data cities ---"
sqlite3 "$DB" <<SQL 2>&1 | tee -a "$LOG"
PRAGMA journal_mode=WAL;
DELETE FROM observations         WHERE city='Chicago'     AND source='wu_icao_history';
DELETE FROM solar_daily          WHERE city='Dallas';
DELETE FROM observation_instants WHERE city='Paris';
DELETE FROM forecasts            WHERE city IN ('Denver','Houston','Los Angeles');
SELECT 'observations_after',         COUNT(*), COUNT(DISTINCT city) FROM observations;
SELECT 'observation_instants_after', COUNT(*), COUNT(DISTINCT city) FROM observation_instants;
SELECT 'solar_daily_after',          COUNT(*), COUNT(DISTINCT city) FROM solar_daily;
SELECT 'forecasts_after',            COUNT(*), COUNT(DISTINCT city) FROM forecasts;
SQL
log "Partial-city cleanup complete."

# ---------------------------------------------------------------------------
# Step 1: WU ICAO daily (45 cities, HK excluded)
#         Skips NYC (already complete), re-fetches Chicago + Atlanta...Panama.
# ---------------------------------------------------------------------------
log "--- Step 1: WU ICAO daily backfill (45 cities, --all --missing-only) ---"
# --all --missing-only (2026-04-14 restart): the previous 2026-04-13 run used
# default skip-if-present which, combined with a stale-in-memory Layer-3 guard,
# wrote partial rows for hot cities (Jeddah 614/834, Sao Paulo 715/834, etc.).
# Default skip-if-present would now *skip* those cities entirely. --all walks
# every city in CITY_STATIONS and --missing-only fills only the dates not yet
# in observations/wu_icao_history, so existing good days are reused and only
# the Layer-3-holes get re-fetched.
# --days 834 covers 2024-01-01 → today (2026-04-13).
python scripts/backfill_wu_daily_all.py --all --missing-only --days 834 2>&1 | tee -a "$LOG"
log "Step 1 done."

# ---------------------------------------------------------------------------
# Step 2: Open-Meteo hourly → observation_instants (46 cities)
#         Default skips already-present cities; re-runs Paris (deleted above)
#         + remaining ~31 uncovered cities.
# ---------------------------------------------------------------------------
log "--- Step 2: Open-Meteo hourly backfill (46 cities) ---"
python scripts/backfill_hourly_openmeteo.py --days 832 2>&1 | tee -a "$LOG"
log "Step 2 done."

# ---------------------------------------------------------------------------
# Step 3: Open-Meteo solar → solar_daily (46 cities)
#         Default = only cities not in solar_daily. Dallas (deleted above)
#         + remaining ~38 uncovered cities.
# ---------------------------------------------------------------------------
log "--- Step 3: Open-Meteo solar backfill (46 cities) ---"
python scripts/backfill_solar_openmeteo.py --days 832 2>&1 | tee -a "$LOG"
log "Step 3 done."

# ---------------------------------------------------------------------------
# Step 4: Open-Meteo Previous Runs → forecasts (46 cities × 5 NWP models)
#         No skip-if-present filter on forecasts table; INSERT OR IGNORE
#         prevents duplicates. Wasted API calls on the 10 already-complete
#         cities, accepted as cost of simplicity.
# ---------------------------------------------------------------------------
log "--- Step 4: Open-Meteo Previous Runs backfill (46 cities × 5 models) ---"
python scripts/backfill_openmeteo_previous_runs.py --days 832 2>&1 | tee -a "$LOG"
log "Step 4 done."

# ---------------------------------------------------------------------------
# Final: row counts sanity check
# ---------------------------------------------------------------------------
log "--- Final row counts ---"
python - <<'PY' 2>&1 | tee -a "$LOG"
from src.state.db import get_world_connection
c = get_world_connection()
for t in ['observations', 'observation_instants', 'solar_daily', 'forecasts']:
    r = c.execute(f'SELECT COUNT(*), COUNT(DISTINCT city) FROM {t}').fetchone()
    print(f'  {t:24s} rows={r[0]:>10,} cities={r[1]}')
# Per-source for observations
print('\n  observations by source:')
for src, n, nc in c.execute("SELECT source, COUNT(*), COUNT(DISTINCT city) FROM observations GROUP BY source").fetchall():
    print(f'    {src:25s} rows={n:>8,} cities={nc}')
c.close()
PY

log "=== Sequential backfill resume completed successfully ==="
