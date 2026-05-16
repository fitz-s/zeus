# Data Readiness Remediation — Master Plan v2

**Status**: DRAFT v2 — supersedes plan.md (v1). Awaiting architect + critic review then operator approval.
**Created**: 2026-04-23
**Last audited**: 2026-04-23
**Stakes declaration**: This system will place real-money trades on Polymarket using data from `state/zeus-world.db`. If the data layer has silent corruption, **no amount of code review will find the bug** because the bug is not in code — it is in data semantics. Operator directive 2026-04-23: "必须做到一切数据就绪". Every claim in this document has a traceable provenance anchor.

## Provenance discipline for this plan

Every factual claim in this plan is labeled with one of:
- `[VERIFIED sql]` — verified by direct `sqlite3 state/zeus-world.db` query during 2026-04-23 scientist audit
- `[VERIFIED code]` — verified by file:line reading during 2026-04-23 scout trace
- `[VERIFIED fs]` — verified by filesystem inspection during drafting
- `[EVIDENCE ⟶ source]` — derived from cited source subagent or review
- `[UNVERIFIED]` — known-unknown; the remediation slice for it starts with investigation

No claim in this plan is unanchored. If a claim lacks provenance, it is a defect in this plan and must be resolved before execution.

## v1 retrospective — what we corrected

Plan v1 shipped with the following confirmed errors (scientist + critic + my own SQL verification):

| v1 claim | v1 value | Reality `[VERIFIED sql]` | Consequence if v1 executed |
|---|---|---|---|
| Warsaw poisoned row utc_timestamp | `2024-12-16T18:00Z` | `2024-12-16T17:00:00+00:00` | DELETE matches 0 rows; row remains |
| Houston poisoned row utc_timestamp | `2024-05-17T00:00Z` | `2024-05-17T05:00:00+00:00` | DELETE matches 0 rows |
| Lagos poisoned row utc_timestamp | `2025-11-25T19:00Z` | `2025-11-25T19:00:00+00:00` | Format mismatch (Z vs +00:00), matches 0 |
| Target table for settlement fixes | `settlements_v2` | `settlements_v2`=0 rows; real data in `settlements`=1,562 | ACs pass vacuously; no fix |
| F-city NULL settlement_value | 458 | 458 F + **171 C** = 629 total | R3-A under-scoped by 37% |
| pm_bin_lo populated = bins ready | 1,503 / 1,562 (96%) | 360 F-city have genuine `lo<hi`; **933 C-city are `lo=hi=value`** (placeholders) | Plan assumed 96% DB-reconstructible; reality 23% |
| UnitConsistencyViolation events | 525 over 04-16→04-21 | **1,401** (2.67x) | Root-cause cities wrong (Houston/NYC/Lagos dominate, not BA/HK/Toronto) |
| Meteostat sources dropped | 46 | **40 of 46**; 6 still active through 2026-03-15 | R5-A scope under-counted |
| Lagos density drop | "March 2026 71% drop" | Drop started Aug 2025 (44→22); Mar 2026 was second-stage collapse (22→7.1) | R5-B investigation targets wrong date |
| `is_missing_local_hour=0` "bug" | "flag never set despite gaps" | Flag correctly zero — gap-hour rows never inserted (upstream correct) | R0-C would corrupt 1.8M rows by flipping flags on existing (correct) rows |
| R2-B antibody "no src.signal in src.data" | clean slate | **3 existing imports** already violate (`daily_obs_append:72`, `hourly_instants_append:50`, `ingestion_guard:475`) | Antibody fails day-1 |
| File:line for `rebuild_run_id` in db.py | `src/state/db.py:209` | L209 is in `observations` table block; forecasts CREATE is at `src/state/db.py:653-668` | Misleading evidence citation |

**Pattern of v1 errors**: (a) transcription of value without re-verification; (b) wrong table due to v1/v2 confusion; (c) mistaking structural presence for semantic readiness (pm_bin_lo populated ≠ pm_bin_lo useful); (d) framing staggered/gradual changes as point events; (e) interpreting "flag always 0" as bug without checking whether the associated rows exist at all; (f) citing architect-side line numbers without re-opening the file.

Plan v2 rebuilds every claim from primary evidence.

---

## Section 1 — Authoritative environment snapshot (2026-04-23T06:36Z)

### 1.1 DB inventory `[VERIFIED fs + sql]`

| File | Size | Tables | Role |
|---|---:|---:|---|
| `state/zeus-world.db` | 1,720.60 MB | 52 | **Authoritative** data DB; all analysis targets this |
| `state/zeus_trades.db` | 0.78 MB | shadow | decision_log=122 rows only; rest 0 |
| `state/zeus.db` | 4 KB | 0 | Empty stub; not the live DB |
| `state/observations.db` | 0 bytes | — | Orphan; **no code opens it** `[VERIFIED code: scout grep 66 files, 0 hits]` — safe to rm |

### 1.2 launchd services `[VERIFIED fs]`

```
~/Library/LaunchAgents/com.zeus.live-trading.plist       (KeepAlive=true)
~/Library/LaunchAgents/com.zeus.riskguard-live.plist
~/Library/LaunchAgents/com.zeus.heartbeat-sensor.plist
```

`com.zeus.live-trading.plist` EnvironmentVariables: `ZEUS_MODE`, `PYTHONPATH`, `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`. **`WU_API_KEY` absent** → confirms k2_daily_obs intermittent failures.

Zero data-ingest-specific plists exist. `scripts/hko_ingest_tick.py` (shipped in step 8) has NO launchd/cron home → it has never run scheduled.

### 1.3 Cron job status `[EVIDENCE ⟶ coupling subagent]`

`~/.openclaw/cron/jobs.json` for venus agent: only `city-settlement-audit` (weekly) enabled. `zeus-heartbeat`, `zeus-daily-audit`, `zeus-antibody-scan`, `zeus-daily-review`, `zeus-weekly-review` all DISABLED. Zero weather/market ingest cron entries.

### 1.4 Running processes `[VERIFIED ⟶ tracer]`

`com.zeus.live-trading` pid 17530 is currently RUNNING (not stopped — prior `-15` in launchctl output was the SIGTERM *signal* from a previous instance; KeepAlive restarted it). Entries are `paused` via `control_overrides` since 2026-04-18T13:19 UTC; `risk_level=DATA_DEGRADED` continuous.

### 1.5 zeus_meta / VIEW state `[VERIFIED sql]`

```sql
SELECT * FROM zeus_meta;
-- observation_data_version | v1.wu-native  (flipped 2026-04-23 in step 5)
SELECT COUNT(*) FROM observation_instants_current;  -- 1,813,662 ✓
```

The Phase 2 atomic flip (step 5) succeeded and remains in effect. observations_v2_current VIEW exposes the v1.wu-native corpus.

### 1.6 observation_instants_v2 corpus `[VERIFIED sql]`

- 1,813,662 rows
- 51 distinct cities (HK=4 from hko_hourly_accumulator, others 20k–40k)
- UTC range 2023-12-31T11:00 → 2026-04-23T04:00
- Sources: 68 distinct tags (1 wu_icao_history=932,777 rows; 46 meteostat_bulk_*; 20 ogimet_metar_*; 1 hko_hourly_accumulator=4)
- temp_unit: C=1,386,942 / F=426,720
- authority: VERIFIED=1,813,658; ICAO_STATION_NATIVE=4 (HK only)
- data_version: all v1.wu-native
- running_max: physically impossible >56.7°C: **3 rows** (identified below)
- running_min: range [-26.1, 106.0°F] — plausible
- `delta_rate_per_h`: **100% NULL** across all 1.8M rows
- `is_missing_local_hour`: **100% = 0** — and this is correct (gap-hour rows never inserted)
- `is_ambiguous_local_hour`: 91 rows, all on correct DST fall-back dates
- provenance_json: 0 NULL, 0 missing `tier` key

### 1.7 Settlements truth `[VERIFIED sql]`

```
settlements       : 1,562 rows, winning_bin NULL for ALL 1,562, settlement_value NULL for 629
settlements_v2    : 0 rows (schema exists, never populated)
observations      : 42,749 rows, 51 cities
```

Settlement NULL breakdown:
- `settlement_value NULL` = 629 (F=458, C=171)
- `winning_bin NULL` = 1,562 (100%)
- `pm_bin_lo NOT NULL` = 1,503 (but see N1 below)
- `pm_bin_hi NOT NULL` = 1,352
- `pm_bin_lo < pm_bin_hi` (genuine Polymarket bins) = **360 rows (23%)**
- `pm_bin_lo == pm_bin_hi` (C-city self-referential placeholders) = **933 rows (60%)**
- No usable bin pair (NULL or inverted) = 269 rows (17%)
- `settlement_source_type` distribution: WU=1,459, NOAA=67, HKO=29, CWA=7

Reconstruction feasibility:
- **DB-alone reconstructible winning_bin**: 360 F-city rows (all already have matching observations.high_temp)
- **Need Gamma API re-fetch**: 1,104 C-city + 98 F-city = 1,202 rows
- NULL settlement_value rows: 100% (629/629) have corresponding `observations.high_temp` — all recoverable from existing observations

### 1.8 Forecast / ensemble pipeline `[VERIFIED sql]`

```
forecasts                  : 0 rows, schema has 13 columns
                             (missing: rebuild_run_id, data_source_version,
                              and ~18 more K1 columns per src/state/db.py:182-214)
historical_forecasts       : 0 rows
historical_forecasts_v2    : 0 rows
ensemble_snapshots         : 0 rows
ensemble_snapshots_v2      : 0 rows
calibration_pairs          : 0 rows
calibration_pairs_v2       : 0 rows
platt_models               : 0 rows
platt_models_v2            : 0 rows
```

Nothing downstream of observation_instants_v2 has ever been populated.

Data coverage ledger:
```
WRITTEN         : 118,073
MISSING         : 212,603 (200,226 from 5 forecast sources never run; 914 WU; 20 HKO; 11,443 other)
LEGITIMATE_GAP  : 15,361
```

### 1.9 Derived tables `[VERIFIED sql]`

```
diurnal_curves      : 4,800 (50 × 4 × 24 — HK absent)
diurnal_peak_prob   : 14,400 (50 × 12 × 24)
hourly_observations : 1,813,568
temp_persistence    : 1,419 (some buckets n<5)
solar_daily         : 38,279 (last 2026-04-12, missing Amsterdam/Guangzhou/Helsinki/Karachi/Manila)
```

### 1.10 Scheduler health `[VERIFIED sql ⟶ state/scheduler_jobs_health.json]`

| Job | Status | Last success | Last failure reason |
|---|---|---|---|
| harvester | OK | 2026-04-23T06:00 | Gamma pagination timeout offset=208,800 (recoverable — next cycle restarts from 0) |
| k2_daily_obs | INTERMITTENT | 2026-04-22T20:00 | WU_API_KEY env var missing |
| k2_hourly_instants | OK | 2026-04-23T06:08 | — |
| k2_hole_scanner | OK | 2026-04-22T10:00 | — |
| k2_forecasts_daily | FAILED (never OK) | — | `table forecasts has no column named rebuild_run_id` |
| ecmwf_open_data | FAILED since 2026-04-07 | — | subprocess exit=1, stderr swallowed |
| k2_startup_catch_up | FAILED (latest) | — | `No module named 'pytz'` |
| k2_solar_daily | UNKNOWN | last 2026-04-12 per solar_daily max_ts | — |

### 1.11 availability_fact runtime violations `[VERIFIED sql]`

```
UnitConsistencyViolation  : 1,401  (Houston/2025-06-01=177; NYC/2026-01-15=175;
                                   Lagos/2025-11-25=174; Houston/2026-04-19=106;
                                   then Houston/04-18=78, Houston/04-20=62,
                                   Toronto/04-19=53, HK/04-19=53)
UnknownCityViolation      : 350   ("Not A City/2026-01-15" — contract-test probe)
PhysicalBoundsViolation   : 250   (Wellington/2026-07-18=175 — FUTURE DATE;
                                   NYC/04-16..18=75)
DstBoundaryViolation      : 178   (London/2025-03-30=175; others=1)
CollectionTimingViolation : 175   (LA/2026-06-15 — FUTURE DATE)
```

Top UCV culprits are F-cities (Houston/NYC/Lagos historical bulk + Apr 16-21 daily), **not** BA/HK/Toronto as v1 claimed. Future-date PhysicalBoundsViolation + CollectionTimingViolation entries suggest validators fire on forecasted/synthetic probes (not ingested observations).

### 1.12 Runtime control state `[VERIFIED sql + fs]`

- `control_overrides.entries_paused = True` since 2026-04-18T13:19:12
- `control_overrides.reason = auto_pause:ValueError`
- `state/auto_pause_failclosed.tombstone = auto_pause:RuntimeError` ← **different exception type**
- `status_summary.risk_level = DATA_DEGRADED` (chain: empty strategy_health_snapshot → DATA_DEGRADED)
- `status_summary.discrepancy_flags: ["v2_empty_despite_closure_claim"]`
- No operator resume has been issued; log window covering 2026-04-18 rotated

---

## Section 2 — Issue inventory (exhaustive, evidence-anchored)

Every issue below follows the user-mandated 9-field structure. No patching; these are the structural deficits the system's **data plane** presents.

---

### Issue #01 — `forecasts` table broad schema drift

- **ID**: DR-01
- **Severity**: 🔴 P0 — blocks every forecast ingest run
- **Discovery source**: tracer subagent 2026-04-23; confirmed by scientist audit
- **Detection method**: `state/scheduler_jobs_health.json.k2_forecasts_daily.status=FAILED`, `last_failure_reason='table forecasts has no column named rebuild_run_id'`; cross-check `PRAGMA table_info(forecasts)` (13 columns) vs declared schema `src/state/db.py:653-668` + K1 additions at L182-214
- **Key defect**: The `init_schema` pathway applies CREATE TABLE IF NOT EXISTS for NEW DBs. For pre-existing tables, there is no ALTER TABLE migration — any column added to `src/state/db.py` after the table was first created never lands on the live DB. `forecasts_append.py:256-262` INSERT references ~20 K1 columns; live `forecasts` has 13 of them. INSERT fails on the FIRST missing column (currently `rebuild_run_id`), then raises and rolls back. Every k2_forecasts_daily run has failed since this K1 set was added to the writer.
- **Macro-context preserved**: the `forecasts` table is a read surface for strategy (ensemble signal, harvester). Its schema semantics (forecast_high / forecast_low / lead_days / source) must remain. Only ADDITIVE column migration is proposed.
- **New solution**: (a) audit diff between declared `src/state/db.py` forecasts schema and live `PRAGMA table_info`; (b) emit `ALTER TABLE forecasts ADD COLUMN <col> <type>` for each missing column, preserving NULL defaults; (c) run `k2_forecasts_daily` manually and confirm row count increments.
- **Fix procedure**:
  1. `python -c "from src.state.db import get_world_connection, init_schema; ..."` — inspect which declarations exist; produce diff
  2. Write `scripts/migrations/2026_04_23_align_forecasts_schema.py` (atomic, idempotent — each ADD COLUMN guarded by a catch-existing-column exception)
  3. Run migration; re-check PRAGMA
  4. Manual invocation: `python -c "from src.data.forecasts_append import daily_tick; ..."` — expect `forecasts` row count > 0 after run
  5. Let the scheduler fire next cycle (07:30 UTC) and confirm `scheduler_jobs_health.json` updates to OK
- **Rollback**: reverse ADD COLUMN is SQLite 3.35+ only. Safer rollback: git revert migration script; if columns confuse downstream readers, write `UPDATE forecasts SET <col>=NULL` rather than DROP.
- **Verification / acceptance**:
  - `[AC]` `PRAGMA table_info(forecasts)` returns superset of `src/state/db.py` declared columns — asserted by new test `tests/test_forecasts_schema_alignment.py`
  - `[AC]` After 1 scheduler cycle post-migration: `SELECT COUNT(*) FROM forecasts > 0`
  - `[AC]` `scheduler_jobs_health.json.k2_forecasts_daily.status == 'OK'` with `last_success_at` ≥ migration time
- **Possible omissions**: `historical_forecasts` table may have same drift; audit separately. K1 spec may have CHECK constraints not captured by `PRAGMA table_info` — need CHECK-clause parser for complete comparison.
- **Post-completion review points**:
  - Who validates that new columns are populated by the writer for every future INSERT? (→ test_forecasts_schema_alignment becomes reusable antibody)
  - Is there any downstream code that silently reads missing columns via `SELECT col FROM ...` and fails? Grep for `SELECT .* FROM forecasts` after migration.
  - Should the init_schema flow add a migration framework (Alembic-lite) so future schema changes don't leave this kind of trap?

---

### Issue #02 — `forecasts` table has zero rows (runtime consequence of #01)

- **ID**: DR-02
- **Severity**: 🔴 P0 — every downstream calibration/training path starves
- **Discovery source**: completeness subagent 2026-04-23; verified by scientist
- **Detection method**: `[VERIFIED sql]` `SELECT COUNT(*) FROM forecasts = 0`; `data_coverage` shows 200,226 MISSING forecast rows across 5 sources (openmeteo_previous_runs, icon_previous_runs, gfs_previous_runs, ecmwf_previous_runs, ukmo_previous_runs, 42,075 / 42,075 / 42,075 / 42,075 / 31,926 respectively)
- **Key defect**: consequence of DR-01 — once DR-01 is fixed, backfill must run for the N-day-back gap window
- **Macro-context preserved**: forecasts table is the source of truth for the 5-model ensemble feature input to calibration. Historical backfill for 2024-01-01 → today is required for training.
- **New solution**: after DR-01 migration, run `scripts/backfill_openmeteo_previous_runs.py` for full historical window
- **Fix procedure**:
  1. DR-01 migration must complete first
  2. `python scripts/backfill_openmeteo_previous_runs.py --start 2024-01-01 --end 2026-04-23` (estimate: Open-Meteo API, ~5 models × 51 cities × 850 days × 7 leads = ~1.5M requests; with batching, hours)
  3. Verify per-city per-lead coverage via `data_coverage` ledger
- **Rollback**: `DELETE FROM forecasts WHERE imported_at >= <backfill start>`; legacy emptiness was pre-existing state
- **Verification / acceptance**:
  - `[AC]` `SELECT COUNT(*) FROM forecasts > 1,500,000` (estimate; 5 models × 51 × 850 × 7)
  - `[AC]` `SELECT MIN(target_date), MAX(target_date) FROM forecasts` covers 2024-01-01 → today
  - `[AC]` `data_coverage MISSING` count for these 5 sources drops to near 0 (legitimate gaps allowlisted)
- **Possible omissions**: Open-Meteo API rate limits; backfill may need multi-day execution window. Open-Meteo historical data quality varies per model.
- **Post-completion review points**: validate forecast distribution vs observations (bias? spread?); check any model's previous_runs that's missing from Open-Meteo archive.

---

### Issue #03 — ECMWF Open Data ensemble download stopped 2026-04-07 (16-day gap)

- **ID**: DR-03
- **Severity**: 🔴 P0 — live TIGGE ensemble feed dead
- **Discovery source**: coupling + tracer subagents
- **Detection method**: `ls raw/ecmwf_open_ens/ecmwf/` last dir `20260407`; `scheduler_jobs_health.json.ecmwf_open_data.status=FAILED`, `last_failure_reason=subprocess exit=1`, stderr swallowed by wrapper
- **Key defect**: subprocess failure with discarded stderr. Root cause unknown until manual run captures output. Likely candidates: (a) `ecmwf.opendata` pip package version bump changed API signature; (b) network/proxy interaction; (c) ECMWF data availability for specific run hours not yet published when job fires. The wrapper at `src/main.py` subprocess invocation does not pass `stderr=subprocess.PIPE`, so failure reason is unrecoverable from health json.
- **Macro-context preserved**: ECMWF Open Data is a **live daily feed**, separate from TIGGE cloud-VM historical bulk (Target 4). Both feed `ensemble_snapshots_v2` via ingest. Live feed is the "today's ensemble" source; bulk is historical training data.
- **New solution**: (a) instrument the subprocess wrapper to capture stderr into log file; (b) run manually, read actual error; (c) fix root cause; (d) one-shot backfill 2026-04-08 → today
- **Fix procedure**:
  1. Patch `src/main.py` subprocess invocation (wherever `collect_open_ens_cycle` shells out) to stream stderr into `logs/ecmwf_open_data.err`
  2. Manual invocation: `cd "/Users/leofitz/.openclaw/workspace-venus/51 source data" && python scripts/download_ecmwf_open_ens.py --date 2026-04-23 --run-hour 0 ...` (exact args TBD by reading script)
  3. Diagnose error from captured stderr
  4. Apply fix (pip upgrade / arg update / proxy / whatever)
  5. Backfill 2026-04-08 → 2026-04-23 inclusive via loop
  6. Confirm `ensemble_snapshots` row count grows (this table is the old schema; the new one is `ensemble_snapshots_v2`)
- **Rollback**: delete rows back to pre-fix state; leave live feed broken again. Not preferred — once unblocked, keep it running.
- **Verification / acceptance**:
  - `[AC]` `scheduler_jobs_health.json.ecmwf_open_data.status == 'OK'` post-fix
  - `[AC]` New run directories appear daily under `raw/ecmwf_open_ens/ecmwf/2026042[4-9]/`
  - `[AC]` `ensemble_snapshots` row count > 0 (NOT `_v2` — this is the legacy live-feed target)
- **Possible omissions**: this path writes to the OLD `ensemble_snapshots` schema; v2 is populated by the cloud-VM TIGGE pipeline (#04) via `ingest_grib_to_snapshots.py`. Schemas may drift. Audit before claiming #04 and #03 are independent.
- **Post-completion review points**: merge or disambiguate `ensemble_snapshots` (live) vs `ensemble_snapshots_v2` (bulk). Are both read by downstream? Or is live feed obsolete now that bulk exists?

---

### Issue #04 — TIGGE extractor never run; ensemble_snapshots_v2 = 0; calibration starved

- **ID**: DR-04
- **Severity**: 🔴 P0 — operator's stated end goal ("只等 tigge 数据进行坐标extractor随后训练") cannot proceed without this
- **Discovery source**: coupling subagent; confirmed by scientist
- **Detection method**: `[VERIFIED fs]` `find "/Users/leofitz/.openclaw/workspace-venus/51 source data/raw" -name "*localday*"` returns empty; `[VERIFIED sql]` `SELECT COUNT(*) FROM ensemble_snapshots_v2 = 0`; cloud-VM GRIB raw download complete (703 GB, mx2t6 2240/2240, mn2t6 2240/2240 per `docs/artifacts/tigge_cloud_wiring_snapshot_2026-04-19.md`)
- **Key defect**: between "raw GRIB downloaded to cloud VM" and "ensemble_snapshots_v2 populated locally", a multi-step pipeline has never been executed. Scripts exist (`scripts/extract_tigge_mx2t6_localday_max.py`, `scripts/extract_tigge_mn2t6_localday_min.py`, `scripts/ingest_grib_to_snapshots.py`, `scripts/rebuild_calibration_pairs_v2.py`, `scripts/refit_platt_v2.py`) but have never been invoked against the current raw bulk.
- **Macro-context preserved**: cloud GRIB remains untouched; coordinate manifest `/Users/leofitz/.openclaw/workspace-venus/51 source data/docs/tigge_city_coordinate_manifest_full_latest.json` remains authoritative; extractor algorithm (eccodes nearest-grid + 3×3 0.5° box per 51 members) remains unchanged.
- **New solution**: operator-executed shell invocation on the cloud VM, then rsync back, then local ingest + rebuild + refit.
- **Fix procedure** (operator action → data scientist action):
  1. **[OPERATOR]** SSH to TIGGE cloud VM in europe-west4-a
  2. **[OPERATOR]** `cd /data/tigge/workspace-venus/51\ source\ data/ && python3 scripts/extract_tigge_mx2t6_localday_max.py --track mx2t6_high [...]`
  3. **[OPERATOR]** same for `extract_tigge_mn2t6_localday_min.py --track mn2t6_low`
  4. **[OPERATOR]** verify per-city counts in output directories; skim a sample JSON for sanity (`training_allowed` counts healthy)
  5. **[OPERATOR]** rsync JSONs back: `rsync -avz <VM>:/data/.../localday_max ./raw/tigge_ecmwf_ens_mx2t6_localday_max/`
  6. `python scripts/ingest_grib_to_snapshots.py --track mx2t6_high` (writes `ensemble_snapshots_v2`)
  7. same for mn2t6_low
  8. `python scripts/rebuild_calibration_pairs_v2.py --no-dry-run --force` (writes `calibration_pairs_v2`)
  9. `python scripts/refit_platt_v2.py --no-dry-run --force` (writes `platt_models_v2`)
- **Rollback**: per-table DELETE with `ingested_at > <pipeline start>`. Better: include `extractor_run_id` (timestamp + extractor git SHA) column in every inserted row so rollback is `WHERE extractor_run_id = <specific run>` — precise even under concurrent daemon activity.
- **Verification / acceptance**:
  - `[AC]` `SELECT COUNT(DISTINCT city) FROM ensemble_snapshots_v2 >= 45` (allow a few cities not in TIGGE coverage)
  - `[AC]` `SELECT COUNT(DISTINCT target_date) FROM ensemble_snapshots_v2 >= 60` (at least 2 months of training data)
  - `[AC]` `SELECT COUNT(*) FROM ensemble_snapshots_v2 >= 45 * 60 * 2 = 5,400` (per-city per-date per-member, with 51 members ⟶ much more)
  - `[AC]` `SELECT COUNT(*) FROM calibration_pairs_v2 > 0`
  - `[AC]` `SELECT COUNT(*) FROM platt_models_v2 > 0`
- **Possible omissions**: (i) schema for `ensemble_snapshots_v2` may need `extractor_run_id` column ADD before ingest (blocker if so; must resolve at step 5 gate); (ii) local disk space for raw GRIB or JSON if rsync goes cloud→local.
- **Post-completion review points**: (a) does evaluator actually READ from `platt_models_v2` in runtime? Per `docs/artifacts/tigge_cloud_wiring_snapshot_2026-04-19.md:424-444`, live evaluator still calls legacy `get_calibrator → platt_models`. This is a **separate** seam-upgrade packet, not in data-readiness scope. (b) Brier regression AC9 requires live trading to measure; deferred.

---

### Issue #05 — Three physically-impossible temperature rows in v2 (Warsaw 88°C, Houston 160°F, Lagos 89°C)

- **ID**: DR-05
- **Severity**: 🔴 P0 — direct training corruption anchor
- **Discovery source**: completeness subagent; verified by scientist with EXACT utc_timestamps
- **Detection method**: `[VERIFIED sql]` `SELECT * FROM observation_instants_v2 WHERE (running_max > 56.7 AND temp_unit='C') OR (running_max > 135 AND temp_unit='F')` returns exactly 3 rows:
  - Warsaw `utc_timestamp='2024-12-16T17:00:00+00:00'` running_max=88.0°C source=wu_icao_history
  - Houston `utc_timestamp='2024-05-17T05:00:00+00:00'` running_max=160.0°F source=wu_icao_history
  - Lagos `utc_timestamp='2025-11-25T19:00:00+00:00'` running_max=89.0°C source=wu_icao_history
- **Key defect**: upstream WU raw feed emitted a corrupt reading; extremum-preserving aggregator (Phase 0 design) faithfully took the bucket max; validator `availability_fact` already flagged Houston (`PhysicalBoundsViolation`) but did NOT auto-quarantine the stored row. Warsaw wasn't flagged — validator coverage gap. Adjacent meteostat rows for same local hour have plausible values (Warsaw 7.6°C, etc.), proving the WU row is the corrupt one.
- **Macro-context preserved**: extremum-preservation is correct design for capturing SPECI peaks. What's missing is a **physical-bounds CHECK** at row construction.
- **New solution**: (a) cache + quarantine + DELETE the 3 rows; (b) add `ObsV2Row` `__post_init__` CHECK that rejects `running_max > 60°C / > 140°F` or `running_min < -80°C / < -110°F` at construction; (c) rejected rows go to `state/quarantine/obs_v2_rejected.jsonl` via a catch-block in the writer's `insert_rows` (so the gap is visible, not silent).
- **Fix procedure**:
  1. Backup rows before delete: `sqlite3 ... "SELECT ... WHERE <the-3-ids>"` piped to `state/quarantine/obs_v2_poisoned_backup_20260423.jsonl`
  2. `DELETE FROM observation_instants_v2 WHERE source='wu_icao_history' AND running_max > 56.7 AND temp_unit='C'` (matches Warsaw + Lagos)
  3. `DELETE FROM observation_instants_v2 WHERE source='wu_icao_history' AND running_max > 135.0 AND temp_unit='F'` (matches Houston)
  4. `SELECT changes()` after each statement — assert 2 and 1 respectively
  5. Patch `ObsV2Row.__post_init__` to add physical-bounds branch with explicit thresholds. Reject → `InvalidObsV2RowError`.
  6. Patch writer `insert_rows`: catch `InvalidObsV2RowError` per row; append to `state/quarantine/obs_v2_rejected.jsonl` with `(city, utc_timestamp, source, reason)`; continue batch (don't abort entire insert).
  7. Ship antibody test `tests/test_obs_v2_physical_bounds.py`.
- **Rollback**: (a) git revert writer patch; (b) the 3 rows are in quarantine backup — re-insert via ObsV2Row bypass if operator insists (requires writer patch disable).
- **Verification / acceptance**:
  - `[AC]` `SELECT COUNT(*) FROM observation_instants_v2 WHERE (running_max > 56.7 AND temp_unit='C') OR (running_max > 135 AND temp_unit='F') == 0`
  - `[AC]` `tests/test_obs_v2_physical_bounds.py` green (4 test cases: accept 56°C, reject 60°C, accept 134°F, reject 140°F)
  - `[AC]` `state/quarantine/obs_v2_poisoned_backup_20260423.jsonl` contains 3 rows
- **Possible omissions**: validator `availability_fact` still has coverage gap for Warsaw — the plan's `ObsV2Row` CHECK is a harder gate, but the separate validator should also be extended. Earth-record-low threshold −80°C safe for Zeus's city universe (lowest real plausible is Anchorage ≈ −40°C), but explicit rationale should be in code comment for future arctic expansion.
- **Post-completion review points**:
  - Run `SELECT MAX(running_max), MIN(running_min) per city` monthly as Immune-System check
  - Audit `availability_fact.failure_type='PhysicalBoundsViolation'` — any events post-patch signal upstream thinning (row rejected instead of ingested)
  - If rejection rate exceeds N rows/day for any source, alert operator (dashboard TBD)

---

### Issue #06 — `is_missing_local_hour` interpretation error (not a bug, but consumers may expect it)

- **ID**: DR-06
- **Severity**: 🟡 P2 — no data corruption; documentation + downstream-reader audit
- **Discovery source**: completeness subagent misflagged as bug; critic reframed correctly; scientist verified
- **Detection method**: `[VERIFIED sql]` `SELECT SUM(is_missing_local_hour) FROM observation_instants_v2 = 0`; spot-check London 2025-03-30 shows rows at h=0, h=2, h=3, h=4 but **no row at h=1**; spot-check Atlanta 2025-03-09 shows the same pattern
- **Key defect**: the flag is correctly 0 on every existing row — those rows are not themselves in DST gaps. The DST gap is represented by **row absence**, not by a flagged row. Downstream consumers that check `is_missing_local_hour=1` as a "warning signal" will never see the warning, but they also won't see a wrongly-placed row — the system is internally consistent. The v1 plan's R0-C (SQL UPDATE to set flag on existing rows) would CORRUPT the DB by marking valid rows as missing-hour.
- **Macro-context preserved**: the upstream client's row-construction logic that excludes DST-gap-hour rows is correct. Nothing to fix in data. The concern is **consumer-side assumption**.
- **New solution**: (a) DO NOT update existing rows; (b) add antibody test that asserts the system never stores a row in a DST gap hour; (c) document the "gap = row absence" contract in `src/data/observation_instants_v2_writer.py` docstring.
- **Fix procedure**:
  1. Write `tests/test_obs_v2_dst_gap_hour_absent.py`: for each DST-observing city, for each known spring-forward date, assert `SELECT COUNT(*) FROM observation_instants_v2 WHERE city=? AND date(local_timestamp)=? AND CAST(substr(local_timestamp,12,2) AS INT) = <gap_hour> = 0`
  2. Add docstring note to writer module explaining the contract
  3. Audit known downstream readers (`src/signal/diurnal.py`, `src/engine/monitor_refresh.py` via diurnal_curves) for any code that checks `is_missing_local_hour` — confirm none relies on the flag being set for a specific purpose
- **Rollback**: none — this is additive test + doc
- **Verification / acceptance**:
  - `[AC]` `tests/test_obs_v2_dst_gap_hour_absent.py` green for at least 5 cities × 2 years = 10 DST transitions
  - `[AC]` No existing code uses `WHERE is_missing_local_hour = 1` — confirmed by `grep -rn "is_missing_local_hour\s*=\s*1" src/ scripts/ tests/` returns only test assertions against 0
- **Possible omissions**: fall-back flag `is_ambiguous_local_hour=91` is correct (scientist verified correct cities and dates) — no action needed there.
- **Post-completion review points**: if a future feature wants to know "this date had a gap", it should query `WHERE city=? AND date=? AND hour_sequence_is_incomplete` — not rely on a flag. Document this in signal-layer contract.

---

### Issue #07 — Settlement `winning_bin` NULL for ALL 1,562 rows

- **ID**: DR-07
- **Severity**: 🔴 P0 — root cause of `calibration_pairs_v2=0`; blocks all training
- **Discovery source**: completeness; real root cause traced by scout
- **Detection method**: `[VERIFIED sql]` `SELECT COUNT(*) FROM settlements WHERE winning_bin IS NULL = 1,562`; `[VERIFIED code]` scout traced `src/main.py:105 → harvester.run_harvester → harvester.py:317 _write_settlement_truth`
- **Key defect** (scout verified): `_write_settlement_truth` at `src/execution/harvester.py:528-572` runs. The UPDATE clause at L547-556 likely matches 0 rows on most closed markets (no pre-existing settlement row). Fallback INSERT at L558-564 writes `winning_bin = <_find_winning_bin(event)>`. But `_find_winning_bin(event)` at L486-503 only uses `event['winningOutcome']`; if Gamma returns the event without that field (e.g., market just closed, outcome not yet declared), the function returns None, which becomes NULL in DB. The function has NO validation that it produced a non-None result before passing to `_write_settlement_truth`.

The commit at L565 succeeds regardless of whether the UPDATE or INSERT actually wrote a non-NULL winning_bin — there is no `SELECT changes()` check. A silent-success commit across 1,562 iterations.

- **Macro-context preserved**: harvester's overall settlement-harvesting design (polling closed events, writing settlement truth) is correct. The winning_bin computation is the leak.
- **New solution**: (a) fix `_find_winning_bin` + `_write_settlement_truth` to explicitly skip events with no outcome (don't silently write NULL); (b) add row-count validation (assert UPDATE or INSERT affected ≥1 row AND winning_bin is not NULL); (c) for the 1,562 existing NULL rows, re-compute winning_bin using existing `pm_bin_lo/pm_bin_hi` + settlement_value where possible (360 F-city rows) OR Gamma re-fetch (1,202 rows).
- **Fix procedure**:
  1. **Audit**: read `harvester.py:486-503` (`_find_winning_bin`) and `:528-572` (`_write_settlement_truth`) — confirm the None-handling defect.
  2. **Patch harvester**: refactor `_write_settlement_truth` to:
     - raise `ValueError("winning_bin cannot be NULL for settlement")` if `winning_bin is None`
     - assert `SELECT changes() > 0` after UPDATE; if 0, do INSERT; after INSERT, assert `changes() > 0`
     - add log entry with event_slug + winning_bin for successful writes
  3. Deploy patched harvester; next harvester cycle should stop adding NULL winning_bin rows (will raise and log instead).
  4. **Backfill 360 DB-reconstructible rows**: new script `scripts/backfill_winning_bin_from_db.py`:
     - SELECT rows WHERE winning_bin IS NULL AND settlement_value IS NOT NULL AND pm_bin_lo < pm_bin_hi
     - For each: apply `SettlementSemantics.for_city(city).assert_settlement_value(settlement_value)` to get canonical rounded value
     - Determine winning_bin by containment: if `pm_bin_lo ≤ rounded_val < pm_bin_hi` → main bin; else shoulder
     - UPDATE settlements SET winning_bin=?, settlement_value=<canonical> WHERE id=?
  5. **Backfill 629 NULL settlement_value rows**: new script `scripts/backfill_settlement_value_from_observations.py`:
     - For each NULL settlement_value row: `SELECT high_temp FROM observations WHERE city=? AND target_date=?`
     - Apply `SettlementSemantics.for_city(city).assert_settlement_value(high_temp)` (handles unit + rounding per city)
     - UPDATE. Validate `changes() = 1`.
     - Back up the 629 pre-update rows to `state/quarantine/settlement_value_pre_backfill_20260423.jsonl`
  6. **Gamma re-fetch for 1,202 rows**: new script `scripts/refetch_closed_market_bins.py`:
     - For each (city, target_date) needing bins, call Gamma events API by market-slug
     - Extract bin thresholds; update `pm_bin_lo`, `pm_bin_hi`
     - If Gamma no longer serves the event (replay rot), mark row as `winning_bin='_UNRECOVERABLE_'` with explicit reason in `provenance_metadata`
  7. Post-backfill: re-run `backfill_winning_bin_from_db` — now covers all 1,562 minus unrecoverable
- **Rollback**: restore from `state/quarantine/*_pre_backfill_*.jsonl` backups; `UPDATE settlements SET winning_bin=NULL, settlement_value=NULL WHERE id IN (<recovered_id_list>)`
- **Verification / acceptance**:
  - `[AC]` `SELECT COUNT(*) FROM settlements WHERE winning_bin IS NULL` drops from 1,562 to ≤ (number of unrecoverable rows) — specifically, 360 DB-path + up to 1,202 Gamma-path → target ≤ 50 unrecoverable
  - `[AC]` `SELECT COUNT(*) FROM settlements WHERE settlement_value IS NULL` drops from 629 to ≤ 15 (the 15 orphan HK settlements recovered via DR-12, + 7 pre-April HK NULLs via DR-16)
  - `[AC]` New `tests/test_harvester_winning_bin_required.py`: asserts `_write_settlement_truth` raises on None winning_bin
  - `[AC]` `tests/test_settlement_bin_resolution_complete.py`: post-run, assert all non-`_UNRECOVERABLE_` rows have non-NULL winning_bin
- **Possible omissions**:
  - Gamma API closed-market data rot window unknown — some events may be unrecoverable. Plan accepts partial success with documented reason.
  - `_find_winning_bin` uses `event['winningOutcome']` format; the computation from `pm_bin_lo/hi/value` is a different algorithm. Reconciliation needed — are they consistent for any overlap set? Run comparison on 360 DB-reconstructible rows: if `_find_winning_bin` output disagrees with containment-based output, that's a separate bug.
- **Post-completion review points**:
  - Harvester pagination timeout (Gamma offset=208,800) — recoverable per scout, but monitor. If pagination fails consistently near same offset, Gamma API rate-limit or data-size issue.
  - Why do C-city settlements have `pm_bin_lo = pm_bin_hi = settlement_value` placeholder pattern? Was there an earlier run that wrote this? Trace `pm_bin_lo`'s writer.
  - Consider adding `winning_bin TEXT NOT NULL CHECK(winning_bin != '')` constraint post-backfill to prevent future regression.

---

### Issue #08 — 629 settlements with NULL `settlement_value`; no writer path exists

- **ID**: DR-08
- **Severity**: 🔴 P0 — 100% of F-city settlements (458) and 171 C-city settlements have no value
- **Discovery source**: completeness (F=458); scientist added C=171
- **Detection method**: `[VERIFIED sql]`; `[VERIFIED code]` scout grepped 66 files for `settlement_value` — only schema/docs/tests reference; NO writer, NO UPDATE, NO ETL populates this column
- **Key defect**: the column was added to the schema but no code path ever writes it. The harvester INSERT at `harvester.py:558-564` **omits** the column entirely. Fixed as part of DR-07 step 5.
- **Macro-context preserved**: `observations.high_temp` is the authoritative daily high (from WU/HKO/Ogimet) and is populated for all 629 (scientist verified). Recovery is a local UPDATE.
- **New solution**: see DR-07 step 5
- **Fix procedure**: (merged with DR-07)
- **Rollback**: (merged with DR-07)
- **Verification / acceptance**: (merged with DR-07)
- **Possible omissions**: for 15 April HK orphans (DR-12) and 7 pre-April HK NULLs (DR-16), `observations` also lacks the row — depends on those sub-issues resolving first.
- **Post-completion review points**: patch harvester INSERT at L558-564 to explicitly include `settlement_value` in the column list (even if NULL from the event) — makes the omission visible at code level. Consider adding `settlement_value IS NOT NULL` CHECK as eventual type-level antibody.

---

### Issue #09 — 5 HKO-class coupling bugs (data ingest tied to trading daemon)

- **ID**: DR-09
- **Severity**: 🔴 P0 — structural; operator's explicit directive
- **Discovery source**: inventory + coupling subagents; user directive
- **Detection method**: `[VERIFIED code]` grep of `from src.data.X import` in `src/main.py` returns the 5 data-collection imports; the 8 K2 scheduler jobs register in APScheduler; no standalone CLI for these lanes except the already-shipped `scripts/hko_ingest_tick.py` (step 8).
- **Key defect**: every data ingest lane runs inside `com.zeus.live-trading` daemon's in-process APScheduler. When daemon stops (even briefly — crash, deploy, manual stop), ingest stops. When daemon is running but a lane upstream fails (e.g., WU_API_KEY), the lane fails silently inside the scheduler wrapper. This creates a "vault-security-guard" anti-pattern — the thing that would detect data-layer degradation is itself dependent on the trading runtime.
- **Macro-context preserved**: data-collection business logic in `src/data/{daily_obs_append, hourly_instants_append, solar_append, forecasts_append, ecmwf_open_data, hole_scanner}` is correct. What must change is the **scheduling layer** (in-process APScheduler → launchd plists) and the **import boundary** (data-lane scripts must not transitively pull trading-side modules).
- **New solution**: create `scripts/ingest/` package of thin tick scripts, each a standalone CLI; install per-lane launchd plists; remove the K2 scheduler bindings from `src/main.py`; ship import-boundary antibody.
- **Fix procedure** (large — executed in R2-A..E):
  - **R2-A** Create `scripts/ingest/` package:
    ```
    scripts/ingest/__init__.py
    scripts/ingest/_shared.py      # LaneContext dataclass, structured log, exit-code enum
    scripts/ingest/wu_icao_tick.py
    scripts/ingest/ogimet_tick.py
    scripts/ingest/openmeteo_hourly_tick.py   # replaces hourly_instants_append tick
    scripts/ingest/openmeteo_solar_tick.py
    scripts/ingest/forecasts_tick.py          # depends on DR-01 migration
    scripts/ingest/ecmwf_open_ens_tick.py     # depends on DR-03 fix
    scripts/ingest/hko_tick.py                # relocate from scripts/hko_ingest_tick.py
    scripts/ingest/hole_scan_tick.py
    ```
    Each accepts `--dry-run`, `--verbose`, `--catch-up-days N` (consolidates R2-D's TBD catch-up decision)
  - **R2-B** Ship antibody `tests/test_ingest_isolation.py`:
    - AST-walks each `scripts/ingest/*.py` for direct `import` and `from ... import` statements
    - Transitively follows into imported `src.data.*` modules; asserts no transitive edge reaches `src.engine, src.execution, src.strategy, src.signal, src.supervisor_api, src.control, src.observability, src.main, src.data.polymarket_client`
    - String-scan for `importlib.import_module(`, `__import__(`, and `exec(` — flag if found
    - Note: must first resolve DR-10 (`_is_missing_local_hour` relocation) else antibody fails on existing `src/data/daily_obs_append.py:72`
  - **R2-C** Write 8 launchd plists (`com.zeus.ingest.<lane>.plist`) with:
    - `StartCalendarInterval` arrays (exact schedules TBD per lane; see below)
    - `EnvironmentVariables` including `WU_API_KEY` (fixes DR-15) and proxy settings
    - `StandardErrorPath` → `logs/ingest/<lane>.err` (fixes DR-03's stderr-swallowed class)
    - `StandardOutPath` → `logs/ingest/<lane>.log`
  - **R2-D** (serialized: only after R2-A/B/C proven stable 7 days): remove K2 scheduler bindings from `src/main.py` (functions at L122,140,157,175,194,216-219,238; `_k2_startup_catch_up` at L207-233). **Also update the 2 tests** that will break (DR-11).
  - **R2-E** Update `architecture/script_manifest.yaml` registrations + file-header provenance blocks per CLAUDE.md rule.

  Lane schedules (UTC):
  | Lane | Schedule | Rationale |
  |---|---|---|
  | wu_icao | daily 08:00 | WU API key recovers daily history |
  | ogimet | daily 08:30 | Stagger after WU to avoid DB lock |
  | openmeteo_hourly | hourly :07 | Open-Meteo archive updates hourly |
  | solar | daily 00:30 | Astronomical, stable |
  | forecasts | daily 07:30 | NWP model cycles settled |
  | ecmwf_open_ens | 4x daily at ECMWF run+8h (08, 14, 20, 02) | ECMWF cycles |
  | hko | hourly :05 | HKO rhrread accumulator |
  | hole_scan | daily 04:00 | Overnight coverage audit |

  DB lock contention: 8 lanes may occasionally overlap. Per-lane writes use `PRAGMA busy_timeout=30000` + `BEGIN IMMEDIATE` with retry; acceptable for the write rates (<100 rows/sec per lane).

- **Rollback**: R2-D rollback is git-revert + `launchctl unload` all 8 plists. R2-A/B/C are independent and additive — can exist alongside K2 scheduler without R2-D for extended dwell. N=7 days of 5+ successful runs per lane in `state/ingest_log.jsonl` with zero FAILED status before R2-D.
- **Verification / acceptance**:
  - `[AC]` `pytest -q tests/test_ingest_isolation.py` green (0 boundary violations)
  - `[AC]` Each `python scripts/ingest/<lane>.py --dry-run` exits 0 (structural validity)
  - `[AC]` After 24h of live plist operation: `state/ingest_log.jsonl` has entries from all 8 lanes with per-lane freshness thresholds (see `test_data_ingest_log_freshness.py`)
  - `[AC]` Post-R2-D: `grep -c "scheduler.add_job.*k2_\|_k2_.*_tick\|_ecmwf_open_data_cycle" src/main.py == 0` AND `grep -c "from src.data" src/main.py == 0` (no data imports at all)
  - `[AC]` Post-R2-D: trading daemon boots cleanly with only trading-specific scheduler jobs (`_run_mode`, `_harvester_cycle`, `_write_heartbeat`)
- **Possible omissions**:
  - Polymarket market_scanner data coupling is out of scope for this packet (scout confirmed it's trading-intrinsic, not data-ingest)
  - `_startup_data_health_check` at `src/main.py:397-427` may need rewrite to read `ingest_log.jsonl` freshness rather than APScheduler metrics (architect P1-A above)
  - Dry-run invocation from launchd may need a PYTHONPATH/venv activation wrapper
- **Post-completion review points**:
  - After 30d operation, audit `scheduler_jobs_health.json` drift (is it still updated correctly post-R2-D? Probably deprecated)
  - Replace heartbeat-sensor with ingest-log-freshness as primary liveness signal?

---

### Issue #10 — `_is_missing_local_hour` placement violates target import boundary

- **ID**: DR-10
- **Severity**: 🟠 P1 — dependency of DR-09's R2-B antibody
- **Discovery source**: critic; verified by scout
- **Detection method**: `[VERIFIED code]` `_is_missing_local_hour` defined at `src/signal/diurnal.py:19-36`; used by:
  - `src/data/daily_obs_append.py:72` (direct import)
  - `src/data/hourly_instants_append.py:50` (direct import)
  - `src/data/ingestion_guard.py:475` (lazy inside method, "avoid circular at module load")
- **Key defect**: the function is a pure timezone utility — no signal-domain semantics — but lives in `src/signal/diurnal.py`. This creates data→signal import edges that violate the data-lane isolation we'd encode as antibody.
- **Macro-context preserved**: function signature and behavior unchanged; only location moves
- **New solution**: relocate to `src/data/_time_utils.py` (private module); update 3 + 3 callers (3 in data-lane, 3 inside diurnal.py itself per scout)
- **Fix procedure**:
  1. Create `src/data/_time_utils.py` with `_is_missing_local_hour(local_dt, tz) -> bool` (verbatim from diurnal.py L19-36)
  2. Update `src/data/daily_obs_append.py:72` and `src/data/hourly_instants_append.py:50` imports
  3. Update `src/data/ingestion_guard.py:475` lazy import
  4. Update `src/signal/diurnal.py` to `from src.data._time_utils import _is_missing_local_hour` (reverse flow OK; signal reading data-layer utility is semantically correct)
  5. Run full test suite; must green
- **Rollback**: git revert
- **Verification / acceptance**:
  - `[AC]` Full test suite green
  - `[AC]` `grep -rn "from src.signal.diurnal import _is_missing_local_hour" src/` returns only `src/signal/diurnal.py` (moved from external callers)
  - `[AC]` DR-09's R2-B antibody now passes trivially on this front
- **Possible omissions**: other signal-layer helpers may be similarly misplaced — audit `src/signal/*` for pure utilities
- **Post-completion review points**: establish a doc/principle: "time semantics and primitive utilities live in `src/contracts/` or `src/data/_*_utils.py`; `src/signal/*` reserved for signal-domain logic"

---

### Issue #11 — Tests `test_R11` and `test_R12` will break on R2-D scheduler removal

- **ID**: DR-11
- **Severity**: 🟠 P1 — gated dependency for R2-D
- **Discovery source**: scout
- **Detection method**: `[VERIFIED code]` `tests/test_k2_live_ingestion_relationships.py`:
  - `test_R11_main_py_defines_all_k2_functions`: asserts function names exist
  - `test_R12_main_py_references_k2_job_ids`: scans source for `id="k2_*"` literals
- **Key defect**: these tests hard-code the current coupling as an invariant. If R2-D removes those functions, tests fail.
- **Macro-context preserved**: tests protect against regression of intended coupling — but the intended coupling itself is what we're changing. The tests must move to protect the NEW invariant ("data-lane scripts run via launchd, not APScheduler").
- **New solution**: rewrite both tests to reference the new shape
- **Fix procedure**:
  1. Rewrite `test_R11` to assert `scripts/ingest/` has the 8 expected tick scripts
  2. Rewrite `test_R12` to assert per-lane launchd plist files exist in `~/Library/LaunchAgents/com.zeus.ingest.*` — but this couples test to OS/user, which is bad. Better: assert a manifest file `architecture/ingest_schedule.yaml` exists, containing expected lanes + schedules; then a runtime-verification script (not a pytest) asserts live launchd state matches
  3. Move the reshaped tests to `tests/test_ingest_lanes_defined.py` and retire `test_k2_live_ingestion_relationships.py` (or leave as historical stub)
- **Rollback**: git revert
- **Verification / acceptance**:
  - `[AC]` New test suite green; retired tests archived
  - `[AC]` `architecture/ingest_schedule.yaml` exists + listed in `architecture/test_topology.yaml`
- **Possible omissions**: `tests/test_bug100_k1_k2_structural.py:290,313` uses `_scheduler_job` decorator introspection — scout says this test would pass vacuously post-R2-D (no K2 functions to check). But the test's intent (ensure all scheduler-registered functions have observability decorator) must be preserved. Review this test for analogous rewrite.
- **Post-completion review points**: establish a "test-migration checklist" step in every scheduler-structure change PR going forward.

---

### Issue #12 — HK observations frozen 2026-03-31; 15 April settlements orphaned

- **ID**: DR-12
- **Severity**: 🟠 P1 — ingested daily HK data loss since Apr 1; breaks settlement→observation link for April
- **Discovery source**: completeness; scientist confirmed
- **Detection method**: `[VERIFIED sql]` `observations` for HK last target_date=2026-03-31 (821 rows); `settlements` has 15 HK rows with target_dates 2026-04-01..2026-04-15 (all with non-NULL settlement_value from HKO climate page source); no matching `observations` row for those 15 dates
- **Key defect**: HK daily ingest path runs inside `daily_obs_append.daily_tick` which is a per-call orchestrator that fetches WU before HKO. When WU_API_KEY env is missing (DR-15), daily_tick raises early, and HKO path at `daily_obs_append.py:1366 _accumulate_hko_reading(conn)` plus the HKO archive refresh at L1372+ never run. So HK daily observations haven't been written since the WU failure began (pattern consistent with k2_daily_obs intermittent failures starting mid-March).
- **Macro-context preserved**: HKO climate-page settlement source has already populated settlement_value for these 15 rows — the gap is only in the `observations` daily table.
- **New solution**: (a) fix WU_API_KEY (DR-15) — blocks future daily_tick failures; (b) backfill HK observations 2026-04-01 through yesterday via direct HKO CLMMAXT/CLMMINT API call (same logic as daily_obs_append, but isolated)
- **Fix procedure**:
  1. DR-15 must land first (else retry will keep failing)
  2. New script `scripts/backfill_hk_observations_april.py` — imports `_fetch_hko_month_with_retry` from `src/data/daily_obs_append.py` (not trading-side, safe import); for months 2026-04 through today, fetch CLMMAXT/CLMMINT; UPDATE `observations` with daily high/low
  3. Verify: `SELECT MIN(target_date), MAX(target_date) FROM observations WHERE city='Hong Kong'` covers 2026-03-31 → yesterday
  4. Re-verify: `SELECT COUNT(*) FROM settlements s WHERE s.city='Hong Kong' AND NOT EXISTS (SELECT 1 FROM observations o WHERE o.city=s.city AND o.target_date=s.target_date)` == 0 (zero orphans)
- **Rollback**: `DELETE FROM observations WHERE city='Hong Kong' AND imported_at > <backfill start>`; plan accepts original frozen state
- **Verification / acceptance**:
  - `[AC]` Zero HK orphan settlements (per-query above)
  - `[AC]` HK observations row count increases by ≥ 15 (Apr 1–15) + newer dates
- **Possible omissions**: the 7 pre-April HK NULLs (DR-16) are in a different bucket — they're in `settlements` without `settlement_value`, not observations
- **Post-completion review points**: verify DR-09's hko_tick.py extracts HKO path from daily_tick so it runs independently; the "HKO blocked by WU failure" pattern must not recur

---

### Issue #13 — 40 of 46 meteostat_bulk sources dropped (Aug 2025 → Mar 2026, staggered)

- **ID**: DR-13
- **Severity**: 🟡 P2 — 46% density reduction for last 5+ weeks; doesn't corrupt data but thins training corpus
- **Discovery source**: completeness (v1 claimed 46); scientist corrected to 40 of 46
- **Detection method**: `[VERIFIED sql]` `SELECT source, MAX(utc_timestamp) FROM observation_instants_v2 WHERE source LIKE 'meteostat_bulk_%' GROUP BY source`:
  - 40 sources last timestamp pre-2026-03-01
  - 6 still active through 2026-03-15: kmia, katl, cyyz, fact, kdal, efhk
  - First dropout: dnmm (Lagos) at 2025-07-27
- **Key defect**: meteostat bulk CDN archives update at variable cadence per station; some stations appear to have lost upstream contribution. Not a Zeus bug — upstream dataset limitation.
- **Macro-context preserved**: WU remains primary for 47 cities; Ogimet for 3. Meteostat was supplemental (extremum preservation). Loss reduces signal quality but doesn't break pipeline.
- **New solution**: (a) probe Meteostat endpoint for still-available data — is it recoverable? (b) if recoverable, re-run `scripts/fill_obs_v2_meteostat.py` for the 40 stations; (c) if endpoint is dead for those stations, document as permanent baseline shift and allowlist.
- **Fix procedure**:
  1. Probe: for 5 sample stations (mix of dropped + active), `curl -sI https://bulk.meteostat.net/v2/hourly/{wmo}.csv.gz` — live endpoint check
  2. If 200: attempt full-range fetch via `fill_obs_v2_meteostat.py` for the dropped 40
  3. If 404/410: update per-city allowlist in `confirmed_upstream_gaps.yaml` noting the end date
- **Rollback**: n/a — additive
- **Verification / acceptance**:
  - `[AC]` Either ≥30 of 40 stations recovered OR documented as permanent gap with rationale
  - `[AC]` Post-recovery per-city density audit shows ≤ 10% cities below 30 obs/day
- **Possible omissions**: Istanbul/Moscow/Tel Aviv/Shanghai never had meteostat — out of scope; they rely on Ogimet/WU
- **Post-completion review points**: decision on whether meteostat is a maintained upstream or should be removed from tier resolver's allowed-source set

---

### Issue #14 — Auto-pause since 2026-04-18T13:19 UTC; RCA log rotated

- **ID**: DR-14
- **Severity**: 🟠 P1 — trading frozen 5+ days; operator decision required for resume
- **Discovery source**: tracer; scientist noted exception-type discrepancy
- **Detection method**: `[VERIFIED sql]` `control_overrides` row `issued_by=system_auto_pause, reason=auto_pause:ValueError, issued_at=2026-04-18T13:19:12`; `status_summary.entries_paused=true`; `state/auto_pause_failclosed.tombstone = auto_pause:RuntimeError` (**different exception**)
- **Key defect**: an unhandled exception in `cycle_runner._execute_discovery_phase` triggered `pause_entries`. Persisted via DB. Daemon restarted (KeepAlive) but pause survives. The exception TYPE mismatch between control_overrides (ValueError) and tombstone (RuntimeError) suggests TWO distinct auto-pause events — one wrote to DB cleanly, the other fell to the tombstone-fallback path (DB write failed).
- **Macro-context preserved**: auto-pause is fail-safe and correct behavior. The bug is the lack of RCA material + operator notification path (heartbeat-sensor didn't alert loudly enough).
- **New solution**: (a) grep all available log files for `ValueError` and `RuntimeError` on 2026-04-18 and subsequent; (b) if log evidence recovered, root-cause + patch; (c) if not recoverable, operator decides resume with explicit risk acknowledgment — but not this packet's scope to resume trading.
- **Fix procedure**:
  1. Scan `logs/zeus-live.err`, `logs/zeus-live.log`, `logs/zeus-live.err.*` (rotated copies), `state/cycle_trace/` for 2026-04-18T13:18 → 13:20 window
  2. Query `SELECT * FROM decision_log WHERE created_at > '2026-04-18T13:18' AND created_at < '2026-04-18T13:20'` for cycle-state evidence
  3. If root cause identified: patch `cycle_runner._execute_discovery_phase` with appropriate guard; write regression test; document in known_gaps
  4. If root cause NOT identified: DO NOT resume. Document as known-unknown. Operator's explicit resume required.
- **Rollback**: none — read-only investigation + optional patch
- **Verification / acceptance**:
  - `[AC]` Either: RCA doc exists with reproducer test, AND operator approves resume → `UPDATE control_overrides SET entries_paused=false` executed by operator directly
  - OR: plan closes with "auto-pause RCA unrecoverable; resume deferred to separate packet"
- **Possible omissions**: this is the one slice where plan v2 honestly cannot guarantee resolution; evidence may have already rotated away
- **Post-completion review points**: add log-retention policy audit (why did logs rotate before operator could RCA?); improve heartbeat-sensor to alert loudly on auto-pause

---

### Issue #15 — `WU_API_KEY` absent from launchd EnvironmentVariables

- **ID**: DR-15
- **Severity**: 🟠 P1 — upstream of DR-12 (HK daily freeze), DR-07 partial (daily_tick path), intermittent k2_daily_obs
- **Discovery source**: tracer; confirmed by fs read of plist
- **Detection method**: `[VERIFIED fs]` `cat ~/Library/LaunchAgents/com.zeus.live-trading.plist` — EnvironmentVariables dict has ZEUS_MODE, PYTHONPATH, HTTP_PROXY, HTTPS_PROXY, NO_PROXY but NO WU_API_KEY
- **Key defect**: operator's shell has WU_API_KEY in `~/.zshrc` or similar, so manual CLI invocation works. Launchd environment does not inherit shell env; WU_API_KEY is unset; `daily_obs_append.py:99-100` fallback `_WU_PUBLIC_WEB_KEY` should apply but some code path throws `RuntimeError("WU_API_KEY environment variable is required but not set")` — possibly stale `.pyc` with older check, or a different code path with a stricter check.
- **Macro-context preserved**: public key fallback is a documented contingency; the issue is env-inheritance.
- **New solution**: (a) add `WU_API_KEY` to plist EnvironmentVariables (operator action or scripted via `defaults write` / plist edit); (b) also search source for the strict "required but not set" string to identify which path raised — fix if it's a stale/wrong check
- **Fix procedure**:
  1. Grep `grep -rn "WU_API_KEY environment variable is required" src/` — find the raising code
  2. If the raise is warranted (e.g., strict-mode init), add `WU_API_KEY` to plist; operator performs `launchctl unload && launchctl load com.zeus.live-trading.plist` after editing
  3. If the raise is legacy that should fall back to public key: patch the code; add a test that env-absent branch uses public key
  4. Post-fix: `scheduler_jobs_health.json.k2_daily_obs.status` returns to OK within 1 cycle
- **Rollback**: remove env var from plist; plist edit is reversible
- **Verification / acceptance**:
  - `[AC]` `plutil -p ~/Library/LaunchAgents/com.zeus.live-trading.plist` output includes `WU_API_KEY`
  - `[AC]` Post-DR-09 R2-C: same env var added to `com.zeus.ingest.wu_icao.plist`
  - `[AC]` `scheduler_jobs_health.json.k2_daily_obs.status == 'OK'` (pre-R2-D; post-R2-D this job doesn't exist)
- **Possible omissions**: plist-edit may be blocked under SIP/operator authentication; fallback is editing in place + unload/load cycle
- **Post-completion review points**: same analysis for OTHER env vars the code expects (e.g., if there's a Gamma API key, NOAA key, etc.) — audit `os.environ.get` calls across src/data

---

### Issue #16 — 7 pre-April HK settlements with NULL settlement_value

- **ID**: DR-16
- **Severity**: 🟡 P2 — additional to 15 April orphans (DR-12)
- **Discovery source**: scientist (not in completeness or plan v1)
- **Detection method**: `[VERIFIED sql]` HK settlements with settlement_value IS NULL: 7 rows at target_dates 2026-03-13, 03-14, 03-16, 03-19, 03-25, 03-26 (and one more — scientist report lists 6 dates but "7 rows", need exact list)
- **Key defect**: HKO climate page scrape at those dates likely returned empty or 404, but the settlement row was still created (by harvester writing partial settlement with bins but no value)
- **Macro-context preserved**: HKO daily climate archive is the authoritative source; re-scrape is the recovery path
- **New solution**: re-scrape HKO climate page for those 7 dates; UPDATE settlement_value
- **Fix procedure**:
  1. Extract exact list of 7 (city='Hong Kong', target_date) rows with NULL settlement_value
  2. For each, invoke the HKO climate scrape function from `src/data/daily_obs_append.py` (whichever function fetches CLMMAXT for a specific date)
  3. UPDATE `settlements SET settlement_value=? WHERE city='Hong Kong' AND target_date=?`
  4. If HKO still returns empty: allowlist in `confirmed_upstream_gaps.yaml` with explicit reason
- **Rollback**: UPDATE back to NULL (not useful)
- **Verification / acceptance**: `[AC]` `SELECT COUNT(*) FROM settlements WHERE city='Hong Kong' AND settlement_value IS NULL == 0` (or documented unrecoverable)
- **Possible omissions**: n/a
- **Post-completion review points**: add regression test asserting every HK settlement has either settlement_value or explicit unrecoverable marker

---

### Issue #17 — 68 duplicate (city, target_date) pairs in `observations`

- **ID**: DR-17
- **Severity**: 🟡 P2 — structural schema gap (missing UNIQUE constraint); values currently consistent but fragile
- **Discovery source**: scientist — **not in any prior report**
- **Detection method**: `[VERIFIED sql]` 68 pairs with 2 rows each; Lucknow 60 (ogimet_metar_vilk + WU, Dec 2024 – Feb 2025), Tel Aviv 7 (ogimet_metar_llbg + WU, Apr 2026), Cape Town 2 (ogimet + WU); all pairs have matching high_temp values
- **Key defect**: `observations` schema lacks UNIQUE(city, target_date) — two ingest paths for the same city can write duplicate rows. Currently no value drift, but a future WU-vs-Ogimet disagreement would land both values and downstream JOINs would return duplicate rows.
- **Macro-context preserved**: existing data is consistent; fix is structural (schema) + source-prioritization
- **New solution**: (a) deduplicate existing rows keeping WU (higher priority per current convention); (b) add UNIQUE(city, target_date) constraint; (c) update appenders to UPSERT rather than INSERT
- **Fix procedure**:
  1. Backup all 68 pairs to `state/quarantine/observations_dupes_20260423.jsonl`
  2. `DELETE FROM observations WHERE rowid IN (SELECT MIN(rowid) FROM observations GROUP BY city, target_date HAVING COUNT(*) > 1)` — delete the earlier-inserted duplicate (source != 'wu_icao_history' if equal-count; choice documented)
  3. Actually preferred: keep wu_icao_history source over ogimet: `DELETE FROM observations WHERE source LIKE 'ogimet%' AND (city, target_date) IN (SELECT city, target_date FROM observations GROUP BY city, target_date HAVING COUNT(*) > 1)`
  4. Verify `SELECT city, target_date, COUNT(*) FROM observations GROUP BY 1,2 HAVING COUNT(*)>1` returns 0 rows
  5. Add UNIQUE constraint via schema migration: `CREATE UNIQUE INDEX idx_observations_city_date ON observations(city, target_date)`
  6. Update `daily_obs_append` writers to `INSERT OR REPLACE` or equivalent UPSERT
  7. Add test `tests/test_observations_unique_city_date.py`
- **Rollback**: restore from backup; drop unique index; revert appender
- **Verification / acceptance**:
  - `[AC]` Zero duplicate pairs
  - `[AC]` Unique index present
  - `[AC]` New test green
  - `[AC]` Next daily_tick run inserts successfully (no UNIQUE violations for legitimate re-writes)
- **Possible omissions**: if WU and Ogimet values DISAGREE for a given (city, date), keeping WU silently discards Ogimet evidence — may be wrong in cases where WU is corrupt (see DR-05). Resolution: log the discarded value before delete, so we have audit trail of disagreements.
- **Post-completion review points**: audit OTHER tables for similar missing UNIQUE constraints (settlements, data_coverage, forecasts post-DR-01 migration)

---

### Issue #18 — Toronto WU vs Meteostat same-timestamp disagreements up to 9.3°C

- **ID**: DR-18
- **Severity**: 🟡 P2 — unreported silent source drift; unknown systematic impact
- **Discovery source**: scientist — **not in any prior report**
- **Detection method**: `[VERIFIED sql]` Toronto sampled period: WU vs Meteostat same utc_timestamp delta > 3°C in 10+ cases; max observed 9.3°C (e.g., 2026-03-10 h12: WU=18.0°C, Meteostat=8.7°C)
- **Key defect**: unknown root cause. Hypotheses: (a) Meteostat returns prior-day or prior-hour data (stale cache); (b) WU CYYZ API returns different station than Meteostat's WMO 71624 maps to; (c) one source interprets timezone differently.
- **Macro-context preserved**: nothing to change until root cause identified
- **New solution**: investigate, then decide: (a) correct one source; (b) document systematic bias; (c) exclude one source
- **Fix procedure**:
  1. Extract the 10 Toronto disagreements (more if full sample run)
  2. Manually verify: for each delta>3 case, query WU API directly AND Meteostat endpoint directly, compare to DB values
  3. Expand audit to other cities — is this Toronto-specific or pan-fleet?
  4. Document findings; if systematic, allowlist or source-exclude
- **Rollback**: n/a (investigation only)
- **Verification / acceptance**: `[AC]` Investigation doc + decision (correct/allowlist/exclude)
- **Possible omissions**: this may be a much larger drift pattern than 10 samples suggest — full-fleet cross-source comparison is expensive
- **Post-completion review points**: add a monthly cron that runs cross-source agreement check and alerts on drift

---

### Issue #19 — `delta_rate_per_h` NULL for all 1.8M v2 rows

- **ID**: DR-19
- **Severity**: 🟡 P2 — feature never populated; downstream may or may not need it
- **Discovery source**: completeness + scientist
- **Detection method**: `[VERIFIED sql]` 100% NULL
- **Key defect**: column declared in schema, never written. Any downstream feature expecting this gets all-NULL.
- **Macro-context preserved**: depends on whether training feature set includes delta_rate_per_h
- **New solution**: decide populate-vs-reserved based on downstream requirement
- **Fix procedure**:
  1. Grep for `delta_rate_per_h` in `src/signal/`, `src/calibration/`, `src/engine/` — find all readers
  2. If readers exist: populate via one-shot backfill script computing `delta = (running_max(h+1) - running_max(h)) * 1.0` or similar semantics; update writer to fill going forward
  3. If no readers: mark column as reserved with comment; add test asserting "delta_rate_per_h is NULL-only in current runtime — re-audit if any consumer accesses"
- **Rollback**: UPDATE back to NULL if populated
- **Verification / acceptance**: `[AC]` Decision documented; either column populated OR explicit reserve-with-audit test
- **Possible omissions**: the computation algorithm is ambiguous (central diff? backward diff? peak-to-peak?). Resolution requires spec from training team.
- **Post-completion review points**: none unique to this issue

---

### Issue #20 — Availability_fact has future-date violations (validator on synthetic data)

- **ID**: DR-20
- **Severity**: 🟡 P2 — indicates validators fire on forecasted/synthetic data; not a data corruption but design smell
- **Discovery source**: scientist — **not in prior reports**
- **Detection method**: `[VERIFIED sql]` `availability_fact.scope_key='Wellington/2026-07-18'` (PhysicalBoundsViolation, 175 events); `'Los Angeles/2026-06-15'` (CollectionTimingViolation, 175 events)
- **Key defect**: the PhysicalBoundsViolation validator is checking forecast or hypothetical temperatures, not ingested observations. This is intended (forecasts can have implausible values) but the violation count pollutes the same table used for observation sanity.
- **Macro-context preserved**: validators are correct — this is a labeling clarity issue
- **New solution**: add `data_origin` column to `availability_fact` (`observed`, `forecast`, `synthetic`, `probe`) — so downstream can filter
- **Fix procedure**:
  1. Schema migration: `ALTER TABLE availability_fact ADD COLUMN data_origin TEXT DEFAULT 'observed'`
  2. Update validator call sites to pass explicit origin
  3. Audit existing rows: classify by scope_type + date (future date ⟹ forecast/synthetic)
- **Rollback**: DROP COLUMN (if SQLite supports) or ignore
- **Verification / acceptance**: `[AC]` Schema updated; new rows have non-default origin; existing rows back-classified
- **Possible omissions**: probe data ("Not A City/2026-01-15" UnknownCityViolation, 350 events) is a separate origin — contract-test probe
- **Post-completion review points**: in new Zeus monitoring, operator sees "real data violations: N" separately from "validator-fire events on synthetic: M"

---

### Issue #21 — Legacy `observation_instants` (867k openmeteo rows) with `running_max` all NULL

- **ID**: DR-21
- **Severity**: 🟢 P3 — dead weight; defer to Phase 4 post-dwell
- **Discovery source**: all 4 prior reports
- **Detection method**: `[VERIFIED sql]` 867,585 rows with `temp_current` but `running_max` all NULL
- **Key defect**: pre-v2 legacy; kept for compat dwell per Phase 2 step 5 decision (+30d)
- **Macro-context preserved**: intentional read-only compat during dwell
- **New solution**: Phase 4 DROP after 30d zero-read window (the Gate F packet's plan v3 Phase 4). Out of scope for data-readiness packet.
- **Fix procedure**: defer to Gate F Phase 4 (~2026-05-23)
- **Rollback**: n/a
- **Verification / acceptance**: none in this packet
- **Possible omissions**: `data_coverage.py` and `hole_scanner.py` reference the legacy table name as an enum, but scout confirmed they use the ledger, not the data table. Before DROP, re-verify.
- **Post-completion review points**: Phase 4 work item outside this packet

---

### Issue #22 — `state/observations.db` is a 0-byte orphan file

- **ID**: DR-22
- **Severity**: 🟢 P3 — zero impact; cleanliness
- **Discovery source**: all 4 reports
- **Detection method**: `[VERIFIED fs]` 0 bytes; `[VERIFIED code]` scout grep 0 hits for `observations.db`
- **Key defect**: relic
- **Macro-context preserved**: n/a
- **New solution**: `rm state/observations.db`
- **Fix procedure**: `rm state/observations.db` as last slice after other work confirmed stable
- **Rollback**: `touch state/observations.db` (re-creates empty; no code opens it anyway)
- **Verification / acceptance**: `[AC]` file absent
- **Possible omissions**: check git-ignore to ensure it doesn't reappear at next daemon start — scout confirmed no code creates it
- **Post-completion review points**: none

---

### Issue #23 — Lagos density staggered collapse (Aug 2025 → Mar 2026)

- **ID**: DR-23
- **Severity**: 🟡 P2 — correctly framed version of a misframed v1 issue
- **Discovery source**: completeness (v1 framed as single event); scientist corrected
- **Detection method**: `[VERIFIED sql]` monthly rows for Lagos: 44-47/day Jan 2024 → Jul 2025; 22/day Aug 2025 (meteostat_bulk_dnmm dropout); 7.1/day Mar 2026 (WU DNMM further thinning); partial April still thin
- **Key defect**: two independent upstream degradations, neither Zeus's fault
- **Macro-context preserved**: WU DNMM remains the primary source for Lagos (sparse but authoritative)
- **New solution**: (a) for Aug 2025 → Feb 2026: meteostat bulk via DR-13 recovery if possible; (b) for Mar 2026+: WU raw scrape or Ogimet DNMM (check availability)
- **Fix procedure**:
  1. (subsumed by DR-13 step 1-3)
  2. Probe Ogimet DNMM for Mar 2026+; if available, enroll Lagos as Tier 2 Ogimet-supplemental (currently only Istanbul/Moscow/TelAviv are Tier 2 primary)
  3. If not: confirmed_upstream_gaps_accepted entries for the months
- **Rollback**: n/a
- **Verification / acceptance**: `[AC]` Lagos density audit post-recovery
- **Possible omissions**: Lagos is a sparsely-reported station structurally; even full recovery may cap at 20/day
- **Post-completion review points**: per-city density SLO (e.g., `alerts if <15 obs/day for 7d`)

---

### Issue #24 — Solar daily stale 11 days + 5 cities missing

- **ID**: DR-24
- **Severity**: 🟡 P2 — feature-degraded for 5 cities
- **Discovery source**: completeness
- **Detection method**: `[VERIFIED sql]` max target_date 2026-04-12; missing cities Amsterdam, Guangzhou, Helsinki, Karachi, Manila
- **Key defect**: solar_append.daily_tick runs inside daemon (DR-09); stopped when daily_tick broke. Missing cities suggests historical ingest gap not related to the current freeze.
- **Macro-context preserved**: astronomical data is deterministic; can be computed locally or fetched once
- **New solution**: (a) DR-09's R2-A openmeteo_solar_tick.py decoupling; (b) one-shot backfill for 5 missing cities + refresh
- **Fix procedure**:
  1. (subsumed by DR-09 R2-A)
  2. `python scripts/ingest/openmeteo_solar_tick.py --cities Amsterdam Guangzhou Helsinki Karachi Manila --start 2024-01-01 --end 2026-04-30`
  3. Verify 51 distinct cities in solar_daily post-run
- **Rollback**: DELETE FROM solar_daily WHERE imported_at > <backfill start>
- **Verification / acceptance**: `[AC]` `SELECT COUNT(DISTINCT city) FROM solar_daily = 51`; `SELECT MAX(target_date) FROM solar_daily >= date('now')-1`
- **Possible omissions**: n/a
- **Post-completion review points**: future `delta_rate_per_h`-style gaps — audit column by column for all tables

---

### Issue #25 — Fossil source `ogimet_metar_fact` remains primary for Cape Town in v2

- **ID**: DR-25
- **Severity**: 🟢 P3 — semantic conflict; 1,636 rows
- **Discovery source**: inventory; confirmed by scientist
- **Detection method**: `[VERIFIED sql]` 1,636 rows in v2 sourced `ogimet_metar_fact` covering Cape Town 2024-02-23 → 2026-04-18; designated fossil per `docs/operations/current_source_validity.md`
- **Key defect**: source tag classified as "fossil lineage, not active source routing" — yet it IS the primary Cape Town source in v2. Contradiction between doc intent and data reality.
- **Macro-context preserved**: Cape Town needs a source; Ogimet FACT is the only one working
- **New solution**: re-classify (a) keep the 1,636 rows as valid but re-tag source lineage as `ogimet_metar_fact_cape_town_primary` (clarifying comment in docs); OR (b) migrate to a different source if one exists
- **Fix procedure**:
  1. Audit: does WU FACT API work for Cape Town? Test.
  2. If yes: backfill via WU; leave Ogimet rows as historical evidence; update tier_resolver primary source
  3. If no: update `current_source_validity.md` to clarify fossil vs primary distinction
- **Rollback**: n/a
- **Verification / acceptance**: `[AC]` Cape Town source in v2 matches documented primary in `tier_resolver.py` OR docs update
- **Possible omissions**: Cape Town's `fact` station designation may be legitimate Johannesburg/FACT ICAO; check
- **Post-completion review points**: audit all "fossil" source designations in current_source_validity against actual v2 source presence

---

### Issue #26 — No cron/launchd scheduling for data-ingest lanes (zero redundancy)

- **ID**: DR-26
- **Severity**: 🟡 P2 — subsumed by DR-09 but called out separately for completeness
- **Discovery source**: coupling + inventory; scout's target 11 completed by me
- **Detection method**: `[VERIFIED fs]` 3 zeus plists exist (live-trading, riskguard-live, heartbeat-sensor), zero data-ingest plists; `[VERIFIED fs]` `~/.openclaw/cron/jobs.json` zero enabled venus data-ingest entries
- **Key defect**: all ingest scheduling is in-process APScheduler (subsumed by DR-09 diagnosis)
- **Macro-context preserved**: resolved by DR-09's R2-C
- **New solution**: (DR-09 R2-C)
- **Fix procedure**: (DR-09 R2-C)
- **Rollback**: (DR-09)
- **Verification / acceptance**: (DR-09)
- **Possible omissions**: cron vs launchd — macOS best-practice is launchd; plan uses launchd
- **Post-completion review points**: ensure operator documents the 8 new plists in `/Users/leofitz/.openclaw/CLAUDE.md` or equivalent

---

### Issue #27 — No caching of Polymarket market bin thresholds → replay-rot risk

- **ID**: DR-27
- **Severity**: 🟡 P2 — affects DR-07 backfill feasibility
- **Discovery source**: scout target 7
- **Detection method**: `[VERIFIED code]` `src/data/market_scanner.py` + `src/data/polymarket_client.py` have no DB cache for market bins; harvester reads live Gamma API each cycle
- **Key defect**: if Gamma archives/deletes closed events, bin thresholds become permanently unrecoverable — DR-07 backfill for 1,202 rows blocked partially
- **Macro-context preserved**: live trading paths don't need cache (markets are live)
- **New solution**: (a) cache market bin thresholds to new `market_bin_cache` table on every market_scanner poll; (b) DR-07's Gamma re-fetch populates cache for closed markets
- **Fix procedure**:
  1. Schema: `CREATE TABLE market_bin_cache (market_slug TEXT PRIMARY KEY, city TEXT, target_date TEXT, bins_json TEXT, cached_at TEXT)`
  2. Patch `market_scanner._fetch_events_by_tags` to UPSERT into cache on every poll
  3. Patch DR-07 backfill to also populate cache
- **Rollback**: DROP TABLE market_bin_cache
- **Verification / acceptance**: `[AC]` cache non-empty after 24h live operation
- **Possible omissions**: pre-existing closed markets (before cache exists) still subject to Gamma availability
- **Post-completion review points**: can this cache replace scheduler_jobs_health.json harvester state for diagnostic purposes?

---

### Issue #28 — `k2_startup_catch_up` failed with "No module named 'pytz'"

- **ID**: DR-28
- **Severity**: 🟢 P3 — venv dependency missing; simple fix
- **Discovery source**: scientist
- **Detection method**: `[VERIFIED sql]` `scheduler_jobs_health.json.k2_startup_catch_up.last_failure_reason = "No module named 'pytz'"`
- **Key defect**: `pytz` is imported somewhere in catch_up_missing path; venv at `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv` doesn't have it (probably superseded by `zoneinfo` but left as residual import)
- **Macro-context preserved**: code intent is timezone handling; fix should use zoneinfo (stdlib) not pytz
- **New solution**: grep for `import pytz` across src/; replace with `from zoneinfo import ZoneInfo` or equivalent; delete pytz references
- **Fix procedure**:
  1. `grep -rn "import pytz\|pytz\." src/` — enumerate
  2. Rewrite each to stdlib zoneinfo
  3. Remove pytz from any requirements file
  4. Run full test suite
- **Rollback**: `pip install pytz`
- **Verification / acceptance**: `[AC]` No pytz imports remain; `k2_startup_catch_up` succeeds post-patch (or is retired by DR-09)
- **Possible omissions**: other legacy imports similarly broken
- **Post-completion review points**: consider linter rule banning pytz in favor of zoneinfo

---

### Issue #29 — Boot catch-up TBD (plan v1 unresolved)

- **ID**: DR-29
- **Severity**: 🟠 P1 — resolves DR-09's R2-D gating decision
- **Discovery source**: architect + critic
- **Detection method**: plan v1 explicitly says "TBD in review"
- **Key defect**: decision not made
- **Macro-context preserved**: catch-up semantics must not be lost when R2-D removes `_k2_startup_catch_up`
- **New solution**: each tick script in `scripts/ingest/` supports `--catch-up-days N` flag. `_k2_startup_catch_up` deleted entirely. Trading daemon's `_startup_data_health_check` reads `state/ingest_log.jsonl` freshness instead of APScheduler metrics.
- **Fix procedure**: built into DR-09 R2-A spec for `_shared.py`
- **Rollback**: (DR-09)
- **Verification / acceptance**: each tick script has `--catch-up-days`, tested via dry-run
- **Possible omissions**: trading daemon's boot-time data-freshness check (`_startup_data_health_check`) must be rewritten — otherwise daemon incorrectly thinks data is fresh
- **Post-completion review points**: ingest_log.jsonl schema + freshness rules documented

---

### Issue #30 — Forecasts subprocess stderr swallowed class bug

- **ID**: DR-30
- **Severity**: 🟡 P2 — pattern bug; applies beyond ECMWF
- **Discovery source**: critic
- **Detection method**: `scheduler_jobs_health.json.ecmwf_open_data.last_failure_reason` only stores shell invocation string, not stderr
- **Key defect**: `_scheduler_job` decorator catches exceptions but the subprocess-invoking functions (`ecmwf_open_data.collect_open_ens_cycle` via `main.py:238`) do not pipe stderr to logs
- **Macro-context preserved**: error propagation semantics
- **New solution**: audit every subprocess.run in src/; ensure `stderr=subprocess.PIPE` + log capture
- **Fix procedure**:
  1. `grep -rn "subprocess.run\|subprocess.Popen\|os.system" src/` — enumerate
  2. Patch each to capture + log stderr
  3. Ship antibody test asserting no bare subprocess without stderr handling
- **Rollback**: git revert per patch
- **Verification / acceptance**: `[AC]` All subprocess calls have explicit stderr handling; antibody test green
- **Possible omissions**: `scripts/` may have similar pattern — audit
- **Post-completion review points**: consider a `run_subprocess_with_logging(cmd, log_path)` shared utility

---

## Section 3 — Execution phases

Dependency DAG (revised per architect lane-parallelization guidance):

```
Task Zero (planning-lock + harvester RCA) ─┐
                                            ├→ Phase R0 (emergency bleed-stops)
Phase R0 ─┬→ DR-01 (forecasts schema)      │
          ├→ DR-05 (poisoned rows)         │
          ├→ DR-10 (relocate helper)       │
          ├→ DR-06 (DST flag antibody)     │
          ├→ DR-22 (rm observations.db)    │
          └→ DR-28 (pytz→zoneinfo)         │
             │
             └→ Phase R1 (unblock) — PARALLEL lane
                ├→ DR-14 (auto-pause RCA)
                ├→ DR-15 (WU_API_KEY)
                ├→ DR-30 (subprocess stderr)
                └→ DR-28 (pytz if not in R0)

             │
             └→ Phase R2 (consolidation, DR-09 + DR-11 + DR-26 + DR-29) — PARALLEL lane
                R2-A: ingest package
                R2-B: antibody (after DR-10 lands)
                R2-C: launchd plists
                R2-D: remove K2 scheduler (GATED on R2-A/B/C 7d dwell + DR-11 tests migrated)
                R2-E: manifests

             │
             └→ Phase R3 (settlement pipeline) — PARALLEL lane
                ├→ DR-07 (winning_bin backfill + harvester patch)
                ├→ DR-08 (settlement_value backfill)
                ├→ DR-12 (HK April observations)
                ├→ DR-16 (HK pre-April NULLs)
                ├→ DR-17 (observations dedupe + UNIQUE)
                └→ DR-27 (market bin cache)

             │
             └→ Phase R4 (TIGGE) — PARALLEL lane
                ├→ DR-02 (forecasts historical backfill, after DR-01)
                ├→ DR-03 (ECMWF subprocess repair)
                ├→ DR-04 (TIGGE extractor — OPERATOR action on cloud VM)
                └→ downstream: ensemble_snapshots_v2, calibration_pairs_v2, platt_models_v2

             │
             └→ Phase R5 (density recovery) — PARALLEL lane
                ├→ DR-13 (meteostat probe + recovery)
                ├→ DR-18 (Toronto WU-meteostat disagreement investigation)
                ├→ DR-23 (Lagos staggered collapse)
                └→ DR-24 (solar 5 cities + freshness)

             │
             └→ Phase R6 (cosmetic) — LAST
                ├→ DR-19 (delta_rate_per_h decision)
                ├→ DR-20 (availability_fact data_origin column)
                ├→ DR-21 (legacy observation_instants DROP — Gate F Phase 4, deferred)
                └→ DR-25 (fossil source reclassification)
```

### Task Zero (mandatory before any Phase R*)

1. Run `python scripts/topology_doctor.py --planning-lock --changed-files <all files listed in section 5> --plan-evidence docs/operations/task_2026-04-23_data_readiness_remediation/plan_v2.md` — expect `topology check ok` before proceeding
2. Harvester RCA: read `src/execution/harvester.py:486-572` completely; confirm scout's hypothesis about `_find_winning_bin` returning None for events without `winningOutcome` field; decide patch shape
3. `logs/zeus-live.err*` tail for 2026-04-18 window — is the ValueError/RuntimeError root cause recoverable?

### Phase ordering rationale

- R0 (bleed-stops) is prerequisite to everything because DR-01 migration unblocks forecasts, DR-05 physical-bounds CHECK prevents future poisoned rows during ingestion phases, DR-10 unblocks R2-B antibody.
- R1/R2/R3/R4/R5 can run as parallel lanes after R0 — scientist/scout evidence shows no cross-lane blocking dependency.
- R6 is last because it's cosmetic and some items depend on all prior phases being stable.

### Estimated wall-clock (assuming operator+agent parallelism)

- Task Zero: 1-2h (RCA is bounded by log availability)
- R0: 4-6h (mostly scripts + tests; DR-05 is 5min SQL + code patch + test)
- R1/R2/R3/R4/R5 parallel: longest pole is R4 (operator TIGGE run on cloud VM + ingest) ≈ 1-2 days
- R2-D (scheduler removal) gated on 7-day dwell — out of session
- R6: 2-4h after R2-D dwell completes

Total for data-ready state (excluding R2-D dwell + R4 operator action): **~2 days active work**.

---

## Section 4 — Antibody suite (shipped in this packet)

| Test | Purpose | Phase |
|---|---|---|
| `tests/test_forecasts_schema_alignment.py` | `PRAGMA table_info(forecasts)` ⊇ declared columns in `src/state/db.py` | R0 (DR-01) |
| `tests/test_obs_v2_physical_bounds.py` | `ObsV2Row.__post_init__` rejects >60°C / >140°F | R0 (DR-05) |
| `tests/test_obs_v2_dst_gap_hour_absent.py` | DST-gap-hour rows never stored | R0 (DR-06) |
| `tests/test_ingest_isolation.py` | `scripts/ingest/*.py` transitively do not import trading-side modules | R2 (DR-09 R2-B) |
| `tests/test_data_ingest_log_freshness.py` | Each lane has recent entry in `state/ingest_log.jsonl` per per-lane threshold | R2 (DR-09) |
| `tests/test_ingest_lanes_defined.py` | 8 expected `scripts/ingest/*.py` + manifest sync | R2 (DR-11 replacement) |
| `tests/test_harvester_winning_bin_required.py` | `_write_settlement_truth` raises on None winning_bin | R3 (DR-07) |
| `tests/test_settlement_bin_resolution_complete.py` | Every non-`_UNRECOVERABLE_` settlement has winning_bin | R3 (DR-07) |
| `tests/test_observations_unique_city_date.py` | UNIQUE(city, target_date) enforced | R3 (DR-17) |
| `tests/test_no_subprocess_without_stderr.py` | All subprocess.run calls have stderr handling | R1 (DR-30) |

---

## Section 5 — File inventory (exhaustive)

### Created
- `scripts/ingest/__init__.py`, `_shared.py`, `wu_icao_tick.py`, `ogimet_tick.py`, `openmeteo_hourly_tick.py`, `openmeteo_solar_tick.py`, `forecasts_tick.py`, `ecmwf_open_ens_tick.py`, `hko_tick.py`, `hole_scan_tick.py`
- `scripts/migrations/2026_04_23_align_forecasts_schema.py`
- `scripts/backfill_winning_bin_from_db.py`
- `scripts/backfill_settlement_value_from_observations.py`
- `scripts/refetch_closed_market_bins.py`
- `scripts/backfill_hk_observations_april.py`
- `src/data/_time_utils.py` (relocated `_is_missing_local_hour`)
- 10 antibody tests (see Section 4)
- 8 launchd plists in `~/Library/LaunchAgents/com.zeus.ingest.*.plist`
- `architecture/ingest_schedule.yaml`
- `state/quarantine/obs_v2_poisoned_backup_20260423.jsonl` (pre-DELETE)
- `state/quarantine/observations_dupes_20260423.jsonl` (pre-dedup)
- `state/quarantine/settlement_value_pre_backfill_20260423.jsonl` (pre-backfill)

### Modified
- `src/state/db.py` — align forecasts schema; verify all tables
- `src/data/observation_instants_v2_writer.py` — physical-bounds CHECK + quarantine channel
- `src/data/daily_obs_append.py`, `src/data/hourly_instants_append.py`, `src/data/ingestion_guard.py`, `src/signal/diurnal.py` — relocated `_is_missing_local_hour` import
- `src/execution/harvester.py` — winning_bin None-rejection, row-count validation
- `src/main.py` — R2-D scheduler removal + `_startup_data_health_check` rewrite (last slice)
- `architecture/script_manifest.yaml`, `architecture/test_topology.yaml`
- `docs/operations/current_data_state.md` (via packet evidence, post-execution)
- `docs/operations/task_2026-04-21_gate_f_data_backfill/confirmed_upstream_gaps.yaml` (Cape Town + meteostat + Lagos entries)

### Deleted
- `state/observations.db`
- Obsolete K2 scheduler bindings in `src/main.py` (post R2-D dwell)

### Receipt
- `docs/operations/task_2026-04-23_data_readiness_remediation/work_log.md`
- `docs/operations/task_2026-04-23_data_readiness_remediation/receipt.json`

---

## Section 6 — Risks & pre-mortem (rewritten)

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| S1 | Gamma API closed-market replay-rot — 1,202 DR-07 rows unrecoverable | MED | Partial settlement data | Document `_UNRECOVERABLE_` marker; accept partial coverage |
| S2 | DR-01 migration ADDs column that clashes with existing constraint | LOW | Migration fails | Idempotent migration script; per-column try/except |
| S3 | R2-D removes scheduler but plist scheduling fails silently | LOW | Data ingestion stops undetected | `test_data_ingest_log_freshness.py` alerts within 24h; 7d dwell before R2-D |
| S4 | Auto-pause RCA unrecoverable (logs rotated) | HIGH | Can't resume trading safely | Documented known-unknown; operator decides separate packet |
| S5 | TIGGE cloud VM extractor operator action delayed | MED | Training blocked on R4-B | Explicit operator-action marker in plan; other phases complete independently |
| S6 | Physical-bounds CHECK in writer rejects legitimate tropical extremes | LOW | Data gap instead of poisoned row | Earth-record high 56.7°C; 60°C CHECK leaves 3°C buffer; quarantine channel makes gaps visible |
| S7 | R2-B antibody too strict, blocks a legitimate future cross-module helper | LOW | PR friction | Allowlist mechanism in test for operator-approved exceptions |
| S8 | Concurrent daemon write during R0-B DELETE | LOW | Race on row identity | BEGIN IMMEDIATE + assert `changes()=3` |
| S9 | HKO re-scrape (DR-16) still returns empty | MED | 7 HK settlements permanent unrecoverable | Allowlist with reason |
| S10 | Meteostat probe (DR-13) shows endpoint dead for 40 stations | HIGH | Document baseline shift; no recovery | Explicit allowlist; accept lower density SLO |

---

## Section 7 — Open questions (flag for reviewers)

Q1. **DR-07 Gamma re-fetch**: 1,202 rows × 1 request each = ~20 min serial; what's Gamma's rate-limit? Parallelize? Accept slow?

Q2. **DR-19 delta_rate_per_h**: populate or reserve? Needs training-side spec confirmation.

Q3. **DR-20 availability_fact data_origin**: backfill existing 2,354 rows or only going-forward?

Q4. **R2-C launchd schedules**: the proposed per-lane times are best-guess. Operator preference?

Q5. **R2-D dwell duration**: proposed 7 days; operator may want longer.

Q6. **DR-14 auto-pause**: attempt resume within this packet or fully defer?

Q7. **DR-18 Toronto disagreement**: investigation scope — Toronto only or fleet-wide cross-source audit?

Q8. **DR-27 market bin cache**: should existing harvester ALSO write to cache on every current cycle, or only new ingest paths?

Q9. **DR-21 legacy DROP timing**: per Gate F Phase 4, +30d from 2026-04-23 flip = 2026-05-23; stick with that or fold into this packet?

Q10. **Planning-lock interpretation**: this packet creates ~22 new files + modifies `src/state/**` + `src/main.py`. Planning-lock machine check must pass; architect flagged this as implicit but not explicit in v1.

---

## Section 8 — Post-completion review plan

After all phases close, run these as independent review:

1. **Immune-system audit**: run `scripts/compare_diurnal_v1_v2.py` + new `scripts/audit_data_health.py` — does the v2 corpus integrity persist post-remediation?
2. **Per-city freshness SLO**: automated cron checks per-city per-source max timestamp; alert if staleness exceeds threshold
3. **Cross-source agreement audit**: monthly WU vs Ogimet vs Meteostat disagreement report — expand DR-18 to full fleet
4. **Harvester correctness review**: audit 30 days of harvester runs for silent-success commits (winning_bin still NULL on any row written post-patch)
5. **Training pipeline end-to-end**: once R4 completes, operator runs evaluator against v2 calibration; compare signal decisions 30d vs legacy baseline
6. **Planning-lock adherence audit**: every future packet touching `src/state/**` runs `topology_doctor.py --planning-lock` before commit
7. **Antibody coverage audit**: re-evaluate every "new finding" in prior subagent reports against shipped antibody suite — did we miss any immune-system holes?

---

## Section 9 — What this plan explicitly does NOT solve (honest scope boundaries)

- **Live trading resume** — requires DR-14 resolution + operator decision; not in packet
- **Evaluator seam upgrade to platt_models_v2** — separate packet per Gate F architecture doc
- **Polymarket market_scanner decoupling** — trading-intrinsic code, separate architecture packet
- **AC9 Platt Brier regression test** — requires live trading to measure
- **30-day signal backtest decision parity** — same blocker
- **Phase 4 legacy DROP** — 30d dwell required (→ ~2026-05-23)
- **TIGGE extractor execution on cloud VM** — operator action, not agent automation

These are listed so the next operator/agent knows scope boundaries.

---

## Section 10 — Receipt binding

Packet closure requires:

- `docs/operations/task_2026-04-23_data_readiness_remediation/work_log.md` — per-phase execution record
- `docs/operations/task_2026-04-23_data_readiness_remediation/receipt.json` — all AC commands + raw outputs
- Updated `docs/operations/current_data_state.md` via proper packet evidence
- All 10 antibody tests green on main branch
- Planning-lock machine check passing

Without all four, the packet does not close.
