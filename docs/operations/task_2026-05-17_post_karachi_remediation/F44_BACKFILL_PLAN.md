# F44 Backfill Plan — observation_instants_v2 gap 2026-05-11 to 2026-05-17

**Scope**: 7 missing days × ~49 non-HKO cities × ~20 hours/day ≈ 6,860 rows.
**Source**: WU_ICAO cities via `wu_hourly_client`; OGIMET_METAR cities via `ogimet_hourly_client`.
**DO NOT EXECUTE** until F44 live-tick fix is deployed and daemon is running.

## Prerequisite checks

```bash
# 1. Confirm live-tick is running (check ingest daemon heartbeat)
cat state/daemon-heartbeat-ingest.json | python3 -m json.tool | grep last_tick

# 2. Confirm obs_v2 tick has fired at least once
grep "K2 obs_v2_tick" logs/zeus-ingest.log | tail -5

# 3. Verify current MAX(target_date) is fresh before backfilling
sqlite3 state/zeus-world.db "SELECT MAX(target_date), COUNT(*) FROM observation_instants_v2"
# Expected: MAX = today or yesterday; count > 1,835,645
```

## Backfill command (run ONLY after prerequisites pass)

```bash
cd /Users/leofitz/.openclaw/workspace-venus/zeus
source .venv/bin/activate

# Step 1: WU_ICAO cities — 7-day rolling backfill via obs_v2_live_tick
python scripts/obs_v2_live_tick.py --days-back 8 --verbose
# Expect: ~48 cities × 7 days × ~20 obs ≈ 6,720 WU rows. Runtime ~10 min.

# Step 2: HKO (Hong Kong) — use existing hko_ingest_tick project-only
python scripts/hko_ingest_tick.py --project-only
# Expect: HKO accumulator rows projected into v2. Runtime < 1 min.

# Step 3: Verify backfill result
sqlite3 state/zeus-world.db "SELECT MAX(target_date), COUNT(*) FROM observation_instants_v2"
# Expected: MAX = today, count ~1,842,000+

sqlite3 state/zeus-world.db \
  "SELECT target_date, COUNT(DISTINCT city) FROM observation_instants_v2 \
   WHERE target_date >= '2026-05-11' GROUP BY target_date ORDER BY target_date"
# Expected: 7 rows (2026-05-11 through 2026-05-17), each with ~40+ cities
```

## Success criterion

`SELECT MAX(target_date) FROM observation_instants_v2` returns date >= 2026-05-17.

## Notes

- `obs_v2_live_tick.py --days-back 8` covers 2026-05-10 through 2026-05-17 inclusive.
  The 2026-05-10 rows already exist; idempotent inserts are no-ops on those.
- WU API rate limit: 0.5s sleep per city; expect ~25s for 48 WU cities per day-chunk.
- Ogimet rate limit: 21s inter-request; expect ~17 min for all Ogimet cities per run.
- If WU 429s: re-run with `--cities <remaining>` after a 60s backoff.
- No daemon restart required. The live-tick fires at :15 each hour independently.
