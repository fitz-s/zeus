# Gate F Data Backfill — Step 1b: Per-City Source Validity Audit

Created: 2026-04-21
Authority basis: Per user directive 2026-04-21 "确认一下所有城市现有的 source 还是否 valid 和他们对应的 forecast 然后再回填"; pairs with step1_schema_audit.md.

## Scope

For each of 51 configured cities, verify:
1. The **observation source** currently in use is still valid (advancing, not dead)
2. The **forecast source** is infrastructure-ready to write back for the 2024-01-01 → today window

No TIGGE work this step — user ruling stands. Forecast scope = Open-Meteo Previous Runs only.

## Observation source map (config/cities.json → live DB)

### Aggregate by settlement_source_type

| settlement_source_type | cities | DB source (in `observations`) | Status | Last target_date | Lag |
|---|---:|---|---|---|---|
| `wu_icao` | 47 | `wu_icao_history` | ✅ VALID (advancing) | 2026-04-14 | 7 d |
| `noaa` (→ ogimet proxy) | 3 | `ogimet_metar_{llbg,ltfm,uuww}` | ✅ VALID (advancing) | 2026-04-16 | 5 d |
| `hko` | 1 | `hko_daily_api` | 🟡 **SUSPECT** (stalled at 2026-03-31) | 2026-03-31 | **21 d** |

### Config vs DB cross-check (complete match)

- 47 cities configured `wu_icao` → 46 have `wu_icao_history` as primary source, 1 (Istanbul) has noaa primary (LTFM config matches `ogimet_metar_ltfm`). Match ✅
- 3 cities configured `noaa` → Istanbul (LTFM), Moscow (UUWW), Tel Aviv (LLBG) → map cleanly to `ogimet_metar_{ltfm,uuww,llbg}`. Match ✅
- 1 city configured `hko` → Hong Kong → maps to `hko_daily_api`. Match ✅

**No config/DB drift**: every configured primary source has a live DB row path.

### Dead/zombie sources (NOT routing any city now)

| Source | Rows | Last target_date | Config? | Verdict |
|---|---:|---|---|---|
| `ogimet_metar_fact` | 2 | 2025-02-19 | Cape Town's wu_station="FACT" but settlement_source_type=`wu_icao` (not `noaa`) → this source was historically tried, superseded by WU ICAO | QUARANTINE |
| `ogimet_metar_vilk` | 59 | 2025-02-15 | Lucknow's wu_station="VILK" but settlement_source_type=`wu_icao` → same pattern | QUARANTINE |

These are fossils. Not routing any city today. Step 2 ETL authority validator marks them `QUARANTINED`; Step 3 backfill doesn't touch them.

### Per-city primary-source distribution (51 of 51 cities)

**46 cities on `wu_icao_history`** (lag 7 d): Amsterdam, Ankara, Atlanta, Auckland, Austin, Beijing, Buenos Aires, Busan, Cape Town, Chengdu, Chicago, Chongqing, Dallas, Denver, Guangzhou, Helsinki, Houston, Jakarta, Jeddah, Karachi, Kuala Lumpur, Lagos, London, Los Angeles, Lucknow, Madrid, Manila, Mexico City, Miami, Milan, Munich, NYC, Panama City, Paris, San Francisco, Sao Paulo, Seattle, Seoul, Shanghai, Shenzhen, Singapore, Taipei, Tokyo, Toronto, Warsaw, Wellington, Wuhan

**3 cities on `ogimet_metar_*`** (lag 5 d): Istanbul (ltfm), Moscow (uuww), Tel Aviv (llbg)

**1 city on `hko_daily_api`** (lag 21 d): Hong Kong ⚠

**1 city split**: Cape Town primarily on wu_icao_history (809 rows) with 2 zombie rows from ogimet_metar_fact.
**1 city split**: Lucknow primarily on wu_icao_history (817 rows) with 59 zombie rows from ogimet_metar_vilk.

## Hong Kong HKO gap investigation

**Symptom**: `hko_daily_api` last target_date = 2026-03-31, but last `fetched_at` = 2026-04-16T07:41:26 (before daemon halt).

**Interpretation**: The daemon attempted a fetch on 2026-04-16 but HKO API returned no data beyond 2026-03-31. Three hypotheses, ranked by likelihood:

1. **HKO publishing delay** (most likely): HKO Observatory publishes monthly daily-summary in arrears. April 2026 data may not yet be indexed by their public API.
2. **HKO API endpoint changed**: Authentication or URL rotation broke silently (the fetcher logs warning but returns empty → Zeus sees "no new data").
3. **Zeus HKO fetcher logic bug**: Introduced during some recent refactor.

**Cannot determine without live probe**. Recommendation:
- After daemon restart, run `python -m scripts.force_hko_refresh` (or equivalent manual probe) to see actual API response for April 2026
- If HKO returns empty → is a publishing lag (not a bug)
- If HKO returns data → fetcher had a bug, now fixed by restart
- Either way, Step 3 backfill for Hong Kong is gated on HKO API returning April data

**Do NOT block Hong Kong backfill on this**: 2024-01-01 → 2026-03-31 (the data HKO already has) is 821 rows, well-populated. Backfill that window with current HKO data; re-probe HKO for April separately.

## Forecast source infrastructure

### Pipeline (uniform for all 51 cities)

| Component | Status |
|---|---|
| API provider | Open-Meteo Previous Runs (global lat/lon grid, no per-city authorization) |
| Backfill script | ✅ `scripts/backfill_openmeteo_previous_runs.py` (exists, reads from `src.config.cities`) |
| Daily append | ✅ `src/data/forecasts_append.py` (K2 `_k2_forecasts_daily_tick` wrapper) |
| Supported models | 5: `best_match`, `gfs_global`, `ecmwf_ifs025`, `icon_global`, `ukmo_global_deterministic_10km` |
| Canonical `source` names | `openmeteo_previous_runs`, `gfs_previous_runs`, `ecmwf_previous_runs`, `icon_previous_runs`, `ukmo_previous_runs` |
| Write target | `forecasts` (legacy) — currently 0 rows |

### Gap discovered in Step 1b

**No direct writer to `historical_forecasts_v2`**.

`grep -rn "INSERT INTO historical_forecasts_v2"` returns 0 results in src/ and scripts/. The v2 table is read-ready (correct metric-partitioned schema) but has no writer. Forecast backfill currently writes to `forecasts` (legacy). DT-native consumers (ensemble_signal, evaluator) read from v2 with legacy fallback via `_forecast_rows_for`.

**Step 2 must close this**: either
- (a) refactor `forecasts_append.py` + `backfill_openmeteo_previous_runs.py` to write to both `forecasts` + `historical_forecasts_v2` (double-write during transition), OR
- (b) keep writing to `forecasts`, add a legacy→v2 ETL (matches the `observations` → v2 pattern used elsewhere)

Recommendation: **(b)** — keeps the ingestion path simple (one writer per source type), v2 cutover is an ETL step. Parallels the Phase 7A/7B pattern for observations.

### Per-city forecast validity: uniform YES

Open-Meteo covers all 51 cities by lat/lon (no regional restriction). Per-city forecast validity = config lat/lon validity. `config/cities.json` has non-null lat/lon for all 51 cities (confirmed via `python -m src.config`). No per-city blocker.

## Validity matrix summary (51 cities)

| Validity dimension | Pass | Partial | Block |
|---|---:|---:|---:|
| **Observation source is live** | 50 | 1 (Hong Kong, HKO 21-d stale — likely publishing lag) | 0 |
| **Observation source matches config** | 51 | 0 | 0 |
| **Forecast source is reachable** | 51 | 0 | 0 |
| **Forecast writer is v2-ready** | 0 | 0 | **51** (v2 writer missing — Step 2 owns this) |

## Blocking issues for backfill (ordered by priority)

### P0 — daemon crash loop (from step1_schema_audit.md)

- `com.zeus.live-trading` looping on B070 migration requirement
- Must resolve before backfill (daemon must be collecting at boundary)

### P1 — forecast v2 writer missing

- `historical_forecasts_v2` has no INSERT path
- Step 2 must build this (option b above recommended)

### P2 — HKO April gap

- Needs one live probe after daemon restart to classify publishing lag vs fetcher bug
- Does not block backfill for Hong Kong 2024-01-01 → 2026-03-31 window
- If it's a fetcher bug: Step 2 scope expands to HKO fix

### P3 — zombie ogimet sources

- `ogimet_metar_fact` + `ogimet_metar_vilk` have fossil rows
- Non-blocking; Step 2 QUARANTINE label via authority validator

## Non-blockers (already sound)

- 46/51 cities have **direct parity match** between cities.json settlement_source_type and current DB primary source
- **3 noaa-typed cities** (Istanbul / Moscow / Tel Aviv) correctly route through ogimet metar proxy with same-day lag as WU cities
- 9 cities have **WU PWS backup** configured (Atlanta/Chicago/Dallas/LA/Miami/NYC/Paris/Seattle/SF) — defense in depth if primary WU ICAO breaks
- 8 cities have **Meteostat backup** — additional tier
- **All 51 cities** have valid lat/lon for Open-Meteo forecast coverage

## Next actions (sequenced)

1. **Fix daemon crash** (out-of-scope for this audit; user ruling on B070 migration)
2. **Restart daemon** → K2 appenders fire on next tick → `scheduler_jobs_health.json` grows beyond `run_mode` probe entry
3. **Observe HKO behavior** for 1-2 ticks to classify HKO gap (publishing lag vs bug)
4. **Step 2 ETL design** — close Gap A (observation_instants_v2 authority+data_version) + Gap B (historical_forecasts_v2 authority+data_version+provenance_json) + P1 (v2 forecast writer via ETL from legacy `forecasts`)
5. **Step 3 backfill** — window 2024-01-01 → live-daemon-last-success-minus-safety-margin

## Files cited

- `config/cities.json` — 51 cities with settlement_source_type per city
- `state/zeus-world.db::observations` — 42,504 rows, 51 cities, 2023-12-27 → 2026-04-16
- `state/zeus-world.db::observation_instants` — 859,668 rows, 46 cities (Amsterdam/Guangzhou/Helsinki/Karachi/Manila absent)
- `scripts/backfill_openmeteo_previous_runs.py` — forecast pipeline source of truth
- `src/data/forecasts_append.py` — K2 hourly forecast append wrapper
- `src/data/hole_scanner.py` — tracks 3 forecast sources: openmeteo_previous_runs / gfs_previous_runs / ecmwf_previous_runs

## Verdict

**Sources are sufficient for 2024-01-01 → today backfill** for all 51 cities, **with two qualifiers**:
- Hong Kong coverage ends at 2026-03-31 until HKO publishing gap resolves
- Forecast backfill needs Step 2 v2-writer work before rows land in DT-native v2 tables

Non-TIGGE backfill (per user ruling) is infrastructurally feasible. Proceed to daemon restart + Step 2 after user approval.
