# Gate F Data Backfill — Step 8: Data-Collection / Trading-Daemon Decoupling

Created: 2026-04-23
Last reused/audited: 2026-04-23
Authority basis: operator directive 2026-04-23 "daemon-live和polymarket数据/
天气数据采集本不应该混为一谈"; `.omc/plans/observation-instants-migration-iter3.md`
Phase 1 L95 (HK accumulator-forward specification).

## Correction

Step 5 / 6 / 7 closeout docs incorrectly carried these items under a single
"daemon not live" banner:

- "2026-04-23 当日完整 — daemon 恢复"
- "HK `hko_hourly_accumulator` — daemon 启用"
- "AC9 Brier 回归测试 — daemon live"
- "30-day signal backtest — daemon live"

This conflates two independent subsystems:

1. **Trading daemon** (`src/main.py` + riskguard + cycle_runner + executor):
   places orders on Polymarket. This is the "daemon-live" track.
2. **Data-collection pipelines** (WU/Ogimet/Meteostat fetches, HKO
   accumulator, Polymarket market-data polling): weather and market data
   ingestion. These should run on their own schedule and never depend on
   whether trading is placing orders.

## Structural issue discovered

`_accumulate_hko_reading()` lives in `src/data/daily_obs_append.py` and is
only called from `src/main.py` (lines 122 and 216) via `daily_tick` /
`catch_up_missing`. So today, **HKO accumulation actually stops when the
trading daemon is stopped** — even though plan v3 L95 intended the
accumulator to run forward independently.

Evidence of this coupling bite:

```
state/zeus-world.db: hko_hourly_accumulator had 3 rows across 2 days
                      pre-fix (sporadic, from brief daemon uptime)
                      instead of ~24 rows/day continuous.
```

Even worse: the **projection step** from `hko_hourly_accumulator` to
`observation_instants_v2` (plan v3 L95: `source='hko_hourly_accumulator'`
+ `authority='ICAO_STATION_NATIVE'` + `provenance.note='hourly_history_
gap_pre_deploy'`) **was never implemented** in any shipped script. HK
appeared in v2 with 0 rows even though accumulator had data.

## Fix landed

New standalone script: **`scripts/hko_ingest_tick.py`**

- Two phases (can run either or both):
  1. `--tick` (default): single `HKO rhrread` fetch → write to
     `hko_hourly_accumulator` (idempotent ON CONFLICT).
  2. `--project`: batch `hko_hourly_accumulator` →
     `observation_instants_v2` with A6-enforced provenance (idempotent
     UNIQUE(city, source, utc_timestamp)).
- **Does not import** `src.main`, `src.engine`, `src.execution` — the
  trading-daemon import graph. Runs independently.
- Registered in `architecture/script_manifest.yaml` as `etl_writer`.
- Intended for hourly cron: `0 * * * * python scripts/hko_ingest_tick.py`
  (optionally `--tick-only` to skip projection, but combined mode is
  cheap and keeps v2 up-to-date every hour).

First run (2026-04-23):

```
tick: tick_ok=True       ← 1 new accumulator row (HKT current temp)
project: candidates=4 written=4 build_errors=0
                          ← 3 pre-existing accumulator rows + 1 new
```

Result: `observation_instants_v2` HK row count went **0 → 4**, all with
`source='hko_hourly_accumulator'`, `authority='ICAO_STATION_NATIVE'`,
`data_version='v1.wu-native'`. HK is now present in the corpus per plan
v3 L95 contract.

## AC11 invariant still holds

`diurnal_curves` only stores (city, season, hour) cells with ≥5 samples
(ETL threshold). HK has 4 total rows across 2 dates, so it does not
appear in `diurnal_curves` yet. Signal layer's fail-closed fallback
continues to fire for HK. `tests/test_diurnal_curves_empty_hk_handled.py`
4/4 green after this change — the in-memory monkeypatched fixture does
not care that real DB now has 4 HK rows, because the test pins the
empty-curves contract (not the empty-DB contract).

## Reframed "deferred" list

Pre-correction classification (step5/6/7):

```
ALL-BLOCKED-BY-DAEMON:
  - 2026-04-23 当日完整
  - HK accumulator activation
  - AC9 Brier regression
  - 30-day signal backtest
```

Post-correction classification:

### Data-collection track (independent of trading)

| Item | Status | Action |
|---|---|---|
| HKO accumulator tick | ✅ Unblocked | `scripts/hko_ingest_tick.py`; schedule via cron, independent of trading daemon |
| HKO accumulator → v2 projection | ✅ Shipped | Same script, default mode; idempotent |
| 2026-04-23 current-day fill | ⏸ Intentional skip | Today is in progress; running `backfill_obs_v2.py --start 2026-04-23 --end 2026-04-23` now produces partial days (the 44 same-day `dates_under_threshold` pattern we saw for 2026-04-23). Cleaner path: run backfill after UTC midnight completes everywhere (~end of 2026-04-24 UTC), OR schedule daily via cron at UTC 01:00 to lock in the previous full day. |
| WU/Ogimet hourly ingest | ⚠ Coupled to daemon | `daily_obs_append.daily_tick` only runs from `src/main.py`. Same coupling as HKO. **Separate packet needed to extract this into standalone `scripts/wu_ingest_tick.py` + `scripts/ogimet_ingest_tick.py` (out of scope for step 8).** |
| Polymarket market-data polling | ⚠ Unknown coupling | Needs audit; likely also coupled to `src/main.py`. Separate packet. |
| Tail-day backfill (previous day) | ✅ Unblocked | `backfill_obs_v2.py --start <yesterday> --end <yesterday>` |

### Trading-behavior track (genuinely requires live trading)

| Item | Why it needs live trading |
|---|---|
| AC9 Platt Brier regression | Requires `calibration_pairs_v2` populated from settled markets via harvester; although harvester can run standalone, calibration_pairs generation needs snapshots from live evaluator cycles |
| 30-day signal backtest decision parity | Needs full evaluator → executor cycle to produce trade decisions comparable against v1 baseline |
| Phase 4 legacy `observation_instants` DROP | Requires 30-day dwell window with zero reads from legacy table; reads only happen during live runtime |

### Completely different subsystem

| Item | Reason |
|---|---|
| Ogimet boundary-widening for 8 tail allowlist entries | Low-impact data-collection optimization; separate from both tracks |

## What step 8 fixed (narrow)

1. HKO accumulator now runnable independently of trading daemon.
2. HKO accumulator → v2 projection gap closed.
3. HK v2 row count: 0 → 4, growing by 1/hour when cron runs
   `scripts/hko_ingest_tick.py`.
4. Correction of the prior docs' "all-blocked-by-daemon" framing.

## What step 8 did NOT fix (explicitly out of scope)

1. WU / Ogimet / Meteostat ingestion still depend on
   `src/main.py` → `daily_obs_append.daily_tick`. Same coupling as HKO
   had. Splitting them is a separate packet with ~6 touch points.
2. Polymarket market-data polling coupling — needs audit first.
3. Scheduling / cron installation — left to operator.
4. Harvester extraction — harvester is inside `src/execution/` and calls
   evaluator/monitor seams; true extraction is larger architecture work.

## Cron recommendation (operator action, not shipped)

```cron
# HKO hourly — runs independently of trading daemon
0 * * * *  cd /path/to/zeus && source .venv/bin/activate && python scripts/hko_ingest_tick.py

# Previous-day tail backfill — runs daily at 01:00 UTC
0 1 * * *  cd /path/to/zeus && source .venv/bin/activate && \
           python scripts/backfill_obs_v2.py --data-version v1.wu-native \
             --start $(date -u -v-1d +%Y-%m-%d) --end $(date -u -v-1d +%Y-%m-%d) \
             --cities <50 non-HK cities>
```

(Installation into `~/.openclaw/cron/jobs.json` or launchd is a separate
operator-gated step; this doc records the recommendation only.)

## References

- `src/data/daily_obs_append.py` — where `_accumulate_hko_reading` lives
- `src/main.py` L122, L216 — current coupling to trading daemon
- `scripts/hko_ingest_tick.py` — the fix
- `.omc/plans/observation-instants-migration-iter3.md` L95 — HK
  accumulator-forward contract
- `docs/operations/task_2026-04-21_gate_f_data_backfill/step5..step7` —
  the closeout docs whose deferred framing this step corrects
