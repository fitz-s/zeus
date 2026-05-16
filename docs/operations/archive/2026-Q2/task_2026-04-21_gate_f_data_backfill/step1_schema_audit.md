# Gate F Data Backfill — Step 1: Schema Audit

Created: 2026-04-21
Authority basis: Phase 10 DT-close post-archive; user directive "最利好系统 + 最适合长远发展"; window 2024-01-01 → today; all data must carry verify/source.

## Scope

Audit the dual-track v2 schemas for metric-parametrized ETL readiness (HIGH + LOW), verify/source column completeness, and daemon ingestion activity. No code changes in this step; output is a gap list that Step 2 (metric-parametrized ETL design) must close.

## DB inventory

| DB file | Size | Role |
|---|---:|---|
| `state/zeus-world.db` | 434 MB | Authoritative data DB (observations, forecasts, calibration, snapshots, settlements) |
| `state/zeus_trades.db` | 608 KB | Trades-focused; v2 tables present but empty |
| `state/zeus.db` | (small) | Legacy; no v2 content |
| `state/risk_state.db` / `risk_state-live.db` | — | RiskGuard state, not data pipeline |

All audit numbers below are from `state/zeus-world.db`.

## v2 Schema Readiness Matrix

| Table | metric 1st-class | authority | data_version | source/provenance | indexes | rows |
|---|---|---|---|---|---|---:|
| `observation_instants_v2` | ✅ (has `running_max` + `running_min` both) | **❌ MISSING** | **❌ MISSING** | ✅ `source` | ✅ | 0 |
| `historical_forecasts_v2` | ✅ `temperature_metric` NOT NULL CHECK('high','low') | **❌ MISSING** | **❌ MISSING** | ✅ `source` | ✅ | 0 |
| `calibration_pairs_v2` | ✅ | ✅ | ✅ | ✅ (via `snapshot_id` FK lineage) | ✅ | 0 |
| `platt_models_v2` | ✅ | ✅ | ✅ | N/A (aggregate) | ✅ | 0 |
| `ensemble_snapshots_v2` | ✅ + rich (`causality_status`, `manifest_hash`, `provenance_json`, `boundary_ambiguous`) | ✅ | ✅ | N/A | ✅ | 0 |
| `settlements_v2` | ✅ | ✅ | — (not critical) | ✅ `settlement_source` + `provenance_json` | ✅ | 0 |

**v2 Golden Window posture confirmed**: every v2 table is 0 rows. Structural writers exist for all six tables; no backfill/live-ingest has crossed the v2 boundary.

## Legacy table inventory (as bootstrap sources for Step 3 backfill)

| Table | rows | cities | date range | source distribution | authority col? |
|---|---:|---:|---|---|---|
| `observations` (daily) | 42,504 | 51 | 2023-12-27 → 2026-04-16 | wu_icao_history 39186 + ogimet 5×3-800 + hko_daily_api 821 | ✅ VERIFIED (42498) / QUARANTINED (6) |
| `observation_instants` | 859,668 | **46** | 2024-01-01 → 2026-04-12 | openmeteo_archive_hourly (single source) | **❌** no authority col; only `running_max`, no `running_min` |
| `historical_forecasts` (legacy) | 0 | — | — | — | — |
| `calibration_pairs` (legacy) | 0 | — | — | — | ✅ authority col exists |
| `platt_models` (legacy) | 0 | — | — | — | ✅ authority col exists |
| `forecasts` | 0 | — | — | — | — |

**Legacy-only data present**: `observations` (daily, 51 cities) + `observation_instants` (hourly, 46 cities). Everything else is 0 rows.

### 5 cities missing from `observation_instants`

`Amsterdam, Guangzhou, Helsinki, Karachi, Manila` — present in daily `observations` (51 cities) but absent from hourly `observation_instants` (46 cities). Open-Meteo archive endpoint coverage gap; needs confirmation during backfill. These are real backfill targets (Step 3), not a structural blocker for Step 2 ETL design.

### `observations.value_type` schema oddity

All 42,504 rows have `value_type='high'`, yet both `high_temp` AND `low_temp` columns are non-null for all rows. The `value_type` column is semantically wrong in its current shape (it should partition rows by metric, not annotate a high/low pair). This oddity is cosmetic; Step 2 ETL can read `observations.low_temp` directly for LOW-metric backfill and does not depend on `value_type`.

## Daemon ingestion activity

| Signal | Value |
|---|---|
| `state/scheduler_jobs_health.json` (B047 new artifact) | 1 entry — `run_mode` at 2026-04-21T16:14:06Z (my antibody test probe, not live daemon) |
| Most recent daily obs (observations.fetched_at) | 2026-04-16T16:25:57Z |
| Most recent instant obs (observation_instants.imported_at) | 2026-04-14T18:52:13Z |
| Today | 2026-04-21 |
| Ingestion lag (daily) | ~5 days |
| Ingestion lag (instant) | ~7 days |

**Verdict**: the K2 appenders (`_k2_daily_obs_tick`, `_k2_hourly_instants_tick`, `_k2_forecasts_daily_tick`, etc.) are not producing new rows. Either the daemon is stopped, the jobs are erroring silently (but B047 decorator now writes to `scheduler_jobs_health.json` — absence of entries confirms the jobs have not run since the decorator landed), or the services are paused by operator.

**Step 3 prerequisite**: before historical backfill, restart the daemon (or launchd service) so the freshest boundary gets covered by live ingestion instead of historical backfill. Backfill should stop at the daemon's last-covered day minus a safety margin.

## Dead-source watchlist

Two legacy daily sources are stale > 1 year and should be classified dead (exclude from `authority=VERIFIED` acceptance in Step 2):

| Source | Most recent target_date | Most recent fetched_at | Row count |
|---|---|---|---:|
| `ogimet_metar_fact` | 2025-02-19 | 2026-04-16 (last attempt) | 2 |
| `ogimet_metar_vilk` | 2025-02-15 | 2026-04-16 (last attempt) | 59 |

`fetched_at` shows the scheduler still tries but target_date stops advancing — upstream feed broken. Either decommission the scheduler registration for these two stations or fall back to alternative sources. Non-blocking for Step 2; flag for operator review.

## Schema gaps that Step 2 ETL must close

Two structural gaps prevent Step 2 ETL from meeting the "all data has verify/source" directive:

### Gap A — `observation_instants_v2` missing `authority` + `data_version`

Current shape:
```sql
CREATE TABLE observation_instants_v2 (
    id, city, target_date, source, timezone_name, local_hour, local_timestamp,
    utc_timestamp, utc_offset_minutes, dst_active, is_ambiguous_local_hour,
    is_missing_local_hour, time_basis,
    temp_current, running_max, running_min,
    delta_rate_per_h, temp_unit, station_id, observation_count,
    raw_response, source_file, imported_at
);
```

Needed (DDL to land in Step 2 migration):
```sql
ALTER TABLE observation_instants_v2 ADD COLUMN authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
    CHECK (authority IN ('VERIFIED','UNVERIFIED','QUARANTINED'));
ALTER TABLE observation_instants_v2 ADD COLUMN data_version TEXT NOT NULL DEFAULT 'v1';
```

Rationale: match the other five v2 tables; `authority=VERIFIED` gates ETL write (only after source-level validation passes); `data_version` enables training-gate invalidation without row rewrites.

### Gap B — `historical_forecasts_v2` missing `authority` + `data_version`

Current shape:
```sql
CREATE TABLE historical_forecasts_v2 (
    id, city, target_date, source, temperature_metric, forecast_value,
    temp_unit, lead_days, available_at, recorded_at
);
```

Needed:
```sql
ALTER TABLE historical_forecasts_v2 ADD COLUMN authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
    CHECK (authority IN ('VERIFIED','UNVERIFIED','QUARANTINED'));
ALTER TABLE historical_forecasts_v2 ADD COLUMN data_version TEXT NOT NULL DEFAULT 'v1';
ALTER TABLE historical_forecasts_v2 ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}';
```

`provenance_json` recommended (not just authority) because forecast rows carry multi-field lineage (issue_time, fetch_time, lead_days, upstream API version) that is better serialized than column-split.

## Structural architecture note (K << N principle)

The v2 schemas already express metric-parametrization via `temperature_metric` NOT NULL CHECK('high','low'). Step 2 ETL should honor this structurally:

```python
def etl_instant_observations_v2(
    city: City,
    metric: Literal["high", "low"],  # 1st-class parameter, not branched code
    window: DateRange,
    source: Source,   # WU / Open-Meteo / ogimet / ...
    authority: Authority,
) -> IngestReport:
    ...
```

Single code path; two runs per job (metric=high, metric=low); AST antibody (analogous to B047) asserts that no metric-aware ETL writes without `temperature_metric` stamp. This is the "make category impossible" enforcement per Fitz methodology.

## Step 2 entry conditions

Before Step 2 (metric-parametrized ETL development) opens:

1. ✅ Schema readiness matrix documented (this file)
2. ✅ 2 schema gaps identified (Gap A + Gap B above)
3. ✅ Legacy bootstrap sources characterized (51-city daily, 46-city hourly, 5-city hourly gap)
4. ✅ Dead-source watchlist flagged (ogimet_metar_fact / _vilk)
5. ✅ Daemon staleness documented (5-7 day lag as of 2026-04-21)

Step 2 scope = DDL migration (Gap A + B) + metric-parametrized `etl_instant_observations_v2.py` + relationship antibody tests + Step 3 window plan.

## Step 3 preview (not this audit)

| Data stream | Source | Time window | HIGH data | LOW data |
|---|---|---|---|---|
| Daily observations | `observations` (legacy) | 2024-01-01 → latest | ✅ `high_temp` | ✅ `low_temp` |
| Hourly observations | Open-Meteo archive | 2024-01-01 → latest | ✅ running_max | ✅ running_min (v2 only) |
| Historical forecasts | TIGGE GRIB + per-city 24h-lead JSON | 2024-01-01 → latest | GRIB mx2t6 + per-city JSON | GRIB mn2t6 + per-city JSON (currently unextracted) |
| Calibration pairs | join(observations × historical_forecasts_v2) | after forecasts exist | derived | derived |
| Platt models | fit(calibration_pairs_v2) | after calibration pairs exist | derived | derived |

**Important** (user ruling 2026-04-21): do not touch TIGGE GRIB archive files. Per-city 24h-lead JSON extraction is currently only `seoul/20251231 + 20260101` — effectively blank. Step 3 must plan whether to extract from `tigge_ecmwf_ens_regions_{mx2t6,mn2t6}/` or use a different forecast ingest path for the 2024-01-01 → today window.

## Files / artifacts cited

- `src/state/schema/v2_schema.py` — v2 CREATE TABLE definitions (source of truth for target DDL)
- `src/state/db.py` — legacy `observations`, `observation_instants` schemas
- `src/main.py` — K2 scheduler job registrations (now B047-observable)
- `state/scheduler_jobs_health.json` — daemon pulse per B047 decorator

## Next action

Step 2 planning lock: write the DDL migration + draft `etl_instant_observations_v2.py` as a metric-parametrized ETL with verify/source stamping. Dispatch Step 2 in a separate commit/packet.
