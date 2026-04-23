# Data Readiness Remediation — Master Plan

Created: 2026-04-23
Status: **DRAFT — pending architect + critic review, then operator approval**
Authority basis:
  - Operator directive 2026-04-23 "多个gap和耦合证实了系统中存在大量迁移遗留问题…这次必须做到一切数据就绪，只是等待目前tigge数据进行坐标extractor随后训练"
  - Four parallel subagent investigations 2026-04-23:
    - inventory (data-collection code paths) — `ad02107d7d91e8877`
    - coupling + TIGGE (architect) — `a47acd839aea97575`
    - runtime/ops (tracer) — `a3cbb212766cac0b7`
    - completeness + correctness (scientist) — `aaf280e159438d48c`
  - Builds on: `.omc/plans/observation-instants-migration-iter3.md` + `task_2026-04-21_gate_f_data_backfill/step5..step8`

## Goal

State when this packet closes:
> All data pipelines **decoupled from trading daemon**, **running independently** via cron/launchd per lane, **writing clean data** (zero poisoned rows, zero silent schema failures, settlement pipeline complete, ensemble_snapshots_v2 populated), with **antibody tests** that make the discovered classes of error unwritable in future code. At that point the operator's stated training plan ("TIGGE extractor → training") is truly unblocked at the data layer.

Non-goals (explicitly out of scope):
- Actual ML training runs
- Live trading resume / auto-pause policy changes (R1-A is an investigation-only slice)
- Deep Polymarket market-data backfill infrastructure (too large; separate packet)
- Phase 4 legacy `observation_instants` DROP (needs +30d dwell; Gate F packet)

## Evidence base (synthesized from 4 subagent reports)

### Silent-failure inventory (confirmed, all currently live)

| # | Bug | Evidence | Impact |
|---|---|---|---|
| 1 | `forecasts` table schema drift — `rebuild_run_id` column declared in `src/state/db.py:209` but absent from live table | `PRAGMA table_info(forecasts)` + `scheduler_jobs_health.json` FAILED since daemon Apr-21 start | 0 forecast rows written ever; blocks entire calibration chain |
| 2 | `ecmwf_open_data` subprocess exit=1 since 2026-04-07 | `ls raw/ecmwf_open_ens/ecmwf/` last dir `20260407`; scheduler_jobs_health FAILED; stderr swallowed | 16 days ensemble data gap; `ensemble_snapshots_v2`=0 |
| 3 | `k2_daily_obs` FAILED 25 times since Apr-21T15:00 — `WU_API_KEY not set` despite code L99-100 having `_WU_PUBLIC_WEB_KEY` fallback | error log | HKO accumulator at L1366 of daily_tick never reached → HK frozen |
| 4 | Entries auto-paused since 2026-04-18T13:19 | `control_overrides` row with `issued_by=system_auto_pause`, `reason=auto_pause:ValueError`; status_summary confirms | 5 days no trading; root cause log rotated |
| 5 | `is_missing_local_hour` = 0 across all 1.8M v2 rows | London 2025-03-30 h=0→h=2 no flag; Atlanta 2025-03-09 same | DST spring-forward silently untagged |
| 6 | 3 physically-impossible rows in v2 (Warsaw 88°C, Houston 160°F, Lagos 89°C) | direct SELECT; meteostat same-hour proves corruption | Training anchor contamination |
| 7 | 458/458 F-city settlements have `settlement_value=NULL` + `winning_bin=NULL` | `SELECT COUNT(*)` | 100% of US calibration data missing |
| 8 | All 1,562 settlements have `winning_bin=NULL` (even C-cities with valid settlement_value) | direct SELECT | Polymarket bin resolution step never ran |
| 9 | 46 meteostat_bulk sources dropped out 2025-07-27 → 2026-03-15 | `SELECT MAX(utc_timestamp) GROUP BY source` | 46% density reduction for 5+ weeks |
| 10 | 525 UnitConsistencyViolation events 2026-04-16→04-21 | `availability_fact` table | Buenos Aires/HK/Toronto declared F (should be C) in runtime |
| 11 | Legacy `observation_instants` 867k rows `running_max=NULL` | `PRAGMA + SELECT` | Dead weight if no reader; silent NULL if reader |
| 12 | `state/observations.db` = 0 bytes | `ls -la` | Orphan DB |
| 13 | HK `observations` frozen 2026-03-31; 15 orphan HK settlements Apr 1–15 | direct SELECT | Daily HK ingest stopped at coupling bite |
| 14 | Lagos March 2026 WU density 71% drop | row count per month | 1-month single-city anomaly |
| 15 | `solar_daily` stale 11 days + missing Amsterdam/Guangzhou/Helsinki/Karachi/Manila | direct SELECT | Daylight features degraded for 5 cities |
| 16 | Fossil `ogimet_metar_fact` still primary for Cape Town in v2 (1,636 rows) | direct SELECT | Conflict: designated fossil vs sole source |
| 17 | `delta_rate_per_h` NULL for all 1.8M v2 rows | direct SELECT | Feature never populated |
| 18 | `ogimet_metar_taipei_46692` — legacy SYNOP path historical rows still in DB? | needs probe | Low priority if not written in last 30d |

### Coupling inventory (HKO-class)

| Module | Only trading-side caller | Fix path |
|---|---|---|
| `forecasts_append.daily_tick` | `src/main.py:175,219` | Extract to `scripts/ingest/forecasts_tick.py` |
| `hourly_instants_append.hourly_tick` | `src/main.py:140,217` | Extract to `scripts/ingest/openmeteo_hourly_tick.py` |
| `ecmwf_open_data.collect_open_ens_cycle` | `src/main.py:238` | Extract to `scripts/ingest/ecmwf_open_ens_tick.py` |
| `solar_append.daily_tick` | `src/main.py:157,218` | Extract to `scripts/ingest/solar_tick.py` |
| `daily_obs_append.daily_tick` (WU+Ogimet slice) | `src/main.py:122,216` | Extract to `scripts/ingest/wu_icao_tick.py` + `scripts/ingest/ogimet_tick.py` |
| `hole_scanner.scan_all` | `src/main.py:194` | Extract to `scripts/ingest/hole_scan_tick.py` |
| `market_scanner._fetch_events_by_tags` | trading-only (evaluator) | Decouple as separate packet (not in this plan) |

### TIGGE critical path (confirmed)

Raw GRIB 703GB complete on cloud VM. Extractor never run. Local-calendar-day JSON absent. `ensemble_snapshots_v2`/`calibration_pairs_v2`/`platt_models_v2` all empty. No new code needed — only operational execution of existing scripts (`extract_tigge_*`, `ingest_grib_to_snapshots.py`, `rebuild_calibration_pairs_v2.py`, `refit_platt_v2.py`).

## Phased Remediation Plan

### Phase R0 — Emergency bleed-stops (3 parallel tracks)

Purpose: stop currently-happening silent failures before any new work.

| Slice | Action | Files touched | Rollback |
|---|---|---|---|
| **R0-A** forecasts schema | `ALTER TABLE forecasts ADD COLUMN rebuild_run_id TEXT; ADD COLUMN data_source_version TEXT;` + run `k2_forecasts_daily` manually to verify | DB only | `ALTER TABLE … DROP COLUMN …` |
| **R0-B** Data poisoning cleanup | `DELETE FROM observation_instants_v2 WHERE (city='Warsaw' AND utc_timestamp='2024-12-16T18:00Z') OR (city='Houston' AND utc_timestamp='2024-05-17T00:00Z') OR (city='Lagos' AND utc_timestamp='2025-11-25T19:00Z')` + strengthen `ObsV2Row.__post_init__` with physical-bounds CHECK (`running_max ≤ 60°C / 140°F`, `running_min ≥ -80°C / -110°F`) + `tests/test_obs_v2_physical_bounds.py` antibody | `src/data/observation_instants_v2_writer.py` + new test | Git revert writer + reinsert rows |
| **R0-C** `is_missing_local_hour` fix | Fix detection logic in `observation_instants_v2_writer.py` per round-trip check already in `src/signal/diurnal.py:19-36` (`_is_missing_local_hour`). Backfill flag on existing 1.8M rows via one-shot SQL UPDATE keyed on local_timestamp not existing in timezone (or: re-derive via Python pass) | `src/data/observation_instants_v2_writer.py` + one-shot update script + test | Revert writer change; flag retains 0 (no worse than pre-fix) |

Gate R0→R1: antibody suite green; `k2_forecasts_daily` writes rows; 0 rows above physical bounds; spring-forward flag populates correctly for London 2025-03-30 / Atlanta 2025-03-09.

### Phase R1 — Runtime unblock (investigation-heavy)

| Slice | Action |
|---|---|
| **R1-A** auto-pause RCA | `grep -l "ValueError" logs/*.log logs/*.err 2>/dev/null` + query `decision_log` for `2026-04-18T13:18` window + `cycle_runner._execute_discovery_phase` code review. **Only if root cause identified**: clear `control_overrides` pause + resume. Else: document as known-unknown, do NOT resume |
| **R1-B** UnitConsistencyViolation RCA | `availability_fact` 525 events Apr 16-21 for Buenos Aires/HK/Toronto. Identify calling code path (likely evaluator or cycle_runner passing wrong city.unit). Fix or file as Tier-3 ticket |
| **R1-C** `WU_API_KEY` env | Add to `~/Library/LaunchAgents/com.zeus.live-trading.plist` `EnvironmentVariables` dict OR remove strict check in whichever code path throws. Inspect stale `.pyc` possibility |

Gate R1→R2: no open P0/P1 runtime failures in `scheduler_jobs_health.json`; either trading resumed or explicit decision to defer resume.

### Phase R2 — Data collection consolidation (CORE of operator directive)

Purpose: move all ingestion out of trading daemon into independent per-lane scripts.

**R2-A** Create `scripts/ingest/` package with 8 tick scripts:

```
scripts/ingest/__init__.py                  # package marker + shared constants
scripts/ingest/_shared.py                   # shared helpers (logging, DB conn, log path)
scripts/ingest/wu_icao_tick.py              # daily WU high/low for 47 WU cities
scripts/ingest/ogimet_tick.py               # daily Ogimet METAR for Istanbul/Moscow/Tel Aviv
scripts/ingest/openmeteo_hourly_tick.py     # hourly observation_instants append
scripts/ingest/openmeteo_solar_tick.py      # daily sunrise/sunset for 51 cities
scripts/ingest/forecasts_tick.py            # daily Open-Meteo Previous Runs (GFS/ECMWF/Icon/UKMO)
scripts/ingest/ecmwf_open_ens_tick.py       # ECMWF Open Data live ensemble
scripts/ingest/hko_tick.py                  # MOVE of existing scripts/hko_ingest_tick.py
scripts/ingest/hole_scan_tick.py            # data_coverage self-heal patrol
```

Each script:
- Calls an `ingest_*()` function from the existing `src/data/*` module (refactor minimal — keep business logic)
- Imports ONLY from `src.data.*`, `src.state.*`, `src.config`, `src.contracts.*`. NEVER from `src.engine/*, src.execution/*, src.strategy/*, src.signal/*, src.supervisor_api/*, src.control/*, src.observability/*`.
- Has `--dry-run` and `--verbose` flags.
- Writes a JSONL log row per run to `state/ingest_log.jsonl` (unified log).
- Returns exit 0 on success, 1 on recoverable failure (HTTP retry), 2 on unrecoverable (schema/config).

**R2-B** Antibody: `tests/test_ingest_isolation.py`

```python
# AST walk each scripts/ingest/*.py — assert no ImportFrom / Import node references
# src.engine, src.execution, src.strategy, src.signal, src.supervisor_api,
# src.control, src.observability, src.main
```

This makes the HKO-class coupling **syntactically unwritable** (Fitz Constraint #2 — "make the category impossible").

**R2-C** launchd plists OR cron wiring per lane:

```
~/Library/LaunchAgents/com.zeus.ingest.wu.plist        (daily UTC 08:00)
~/Library/LaunchAgents/com.zeus.ingest.ogimet.plist    (daily UTC 08:30)
~/Library/LaunchAgents/com.zeus.ingest.openmeteo_hourly.plist  (hourly :07)
~/Library/LaunchAgents/com.zeus.ingest.solar.plist     (daily UTC 00:30)
~/Library/LaunchAgents/com.zeus.ingest.forecasts.plist (daily UTC 07:30)
~/Library/LaunchAgents/com.zeus.ingest.ecmwf_ens.plist (cron at ECMWF run hours)
~/Library/LaunchAgents/com.zeus.ingest.hko.plist       (hourly :05)
~/Library/LaunchAgents/com.zeus.ingest.hole_scan.plist (daily UTC 04:00)
```

Each plist has `StandardErrorPath` → `logs/ingest/<lane>.err` so subprocess stderr is captured.

**R2-D** Remove `_k2_*_tick` + `_ecmwf_open_data_cycle` from `src/main.py` scheduler. The trading daemon stops scheduling data collection entirely. Update `_k2_startup_catch_up` to skip ingest modules (or invoke the new ingest scripts as subprocess — TBD in review).

Gate R2→R3: antibody test green; each of 8 new scripts runs stand-alone successfully; at least 1 full run of each scheduled tick captured in `state/ingest_log.jsonl`.

### Phase R3 — Settlement pipeline completion

Purpose: resolve the discovered root cause of `calibration_pairs_v2=0` (not TIGGE; winning_bin never computed).

**R3-A** Backfill `settlement_value` for 458 F-city settlements:

- For each (city, target_date) where settlement.settlement_value is NULL:
  - Lookup `observations.high_temp` for (city, target_date) — daily max from authoritative source
  - Apply `SettlementSemantics.for_city(city).round_values()` per unit
  - UPDATE settlement row

Evidence gate: count F-city settlements with non-null settlement_value == 458 (was 0).

**R3-B** Compute `winning_bin` for all 1,562 settlements:

- Requires Polymarket market bin thresholds per (city, target_date). Potential sources:
  - `market_scanner` cache in DB
  - Historical snapshot in `ensemble_snapshots` (empty)
  - Re-fetch via Gamma API (market is closed → description should be persistent)
- Algorithm: floor-containment per current law (plan v3 & current known_gaps HK section). For each settlement, find the bin where `bin_lo ≤ settlement_value < bin_hi` (or shoulder bins if out of range).
- Write winning_bin to settlement row + emit calibration_pairs_v2 record.

Evidence gate: count settlements with non-null winning_bin == 1,562.

**R3-C** HK observations recovery 2026-04-01 → 2026-04-15:

- 15 orphan HK settlements have values from HKO climate page
- Back-populate `observations` table with these 15 daily highs (source='hko_climate_archive')
- Verify no double-count with any CLMMAXT batch already processed

### Phase R4 — Forecast / ensemble pipeline restore

**R4-A** ECMWF Open Data subprocess repair:

- Run `python3 "/51 source data/scripts/download_ecmwf_open_ens.py" --date TODAY --run-hour 0 ...` manually
- Capture stderr → diagnose (likely: API auth rotation, missing `ecmwf.opendata` pip dep, proxy)
- Fix and one-shot backfill 2026-04-08 → today

**R4-B** TIGGE coordinate extractor (CLOUD VM — operator action):

- SSH to TIGGE cloud VM
- Run `scripts/extract_tigge_mx2t6_localday_max.py --track mx2t6_high` and the mn2t6_low equivalent
- Output: `/data/tigge/workspace-venus/51 source data/raw/tigge_ecmwf_ens_{mx2t6,mn2t6}_localday_*/` per-city per-date JSON
- Rsync JSONs back locally (small, ~MB each)

**R4-C** ingest + rebuild + refit:

- `python scripts/ingest_grib_to_snapshots.py --track mx2t6_high`
- same for low track
- `python scripts/rebuild_calibration_pairs_v2.py --no-dry-run --force`
- `python scripts/refit_platt_v2.py --no-dry-run --force`

Evidence gate: `ensemble_snapshots_v2 > 0`, `calibration_pairs_v2 > 0`, `platt_models_v2 > 0`.

### Phase R5 — Density recovery

**R5-A** Meteostat 46-source dropout investigation:

- Probe current bulk endpoint: `curl -sI https://bulk.meteostat.net/v2/hourly/72219.csv.gz` → 200 or error?
- If endpoint alive: re-run `scripts/fill_obs_v2_meteostat.py` for date range 2025-07-27 → today to restore
- If endpoint dead: document and accept 24 obs/day density as new baseline

**R5-B** Lagos March 2026 gap:

- Ogimet probe for DNMM March 2026 → if Ogimet has coverage, run supplemental fill
- Else allowlist the shortfall per existing pattern

### Phase R6 — Hygiene + Antibodies

| R6 slice | Action |
|---|---|
| R6-A | Remove `state/observations.db` (0-byte orphan) |
| R6-B | `delta_rate_per_h` decision — populate (computed from consecutive running_max deltas) if any training code needs it; else mark as "reserved" and test its absence |
| R6-C | solar_daily fill for 5 missing cities + refresh |
| R6-D | 3 suspicious 0.0°C settlements manual spot-check |
| R6-E | Fossil `ogimet_metar_fact` decision — is Cape Town OK with it as a documented non-fossil exception, or deprecate? |
| R6-F | `availability_fact` Warsaw validator coverage gap (88°C escaped) — extend validator to all cities not subset |
| R6-G | Legacy `observation_instants` DROP — after confirming zero runtime readers post-R2 |

Antibody suite to ship alongside fixes:

| Test | Protects |
|---|---|
| `tests/test_obs_v2_physical_bounds.py` | Poisoned temperature values (R0-B) |
| `tests/test_obs_v2_dst_missing_hour_flag.py` | Spring-forward flag populated (R0-C) |
| `tests/test_ingest_isolation.py` | HKO-class coupling syntactically unwritable (R2-B) |
| `tests/test_forecasts_schema_alignment.py` | `PRAGMA table_info` vs module INSERT column set (prevents R0-A class silent-drift) |
| `tests/test_settlement_bin_resolution_complete.py` | every settled row has winning_bin (R3-B) |
| `tests/test_data_ingest_log_freshness.py` | `state/ingest_log.jsonl` has entries in last 25h per lane (R2 runtime evidence) |

## Cross-phase dependencies

```
R0-A (schema) ─────┐
R0-B (poison)  ────┼──> R1 (unblock) ──┐
R0-C (DST flag) ───┘                    │
                                        ├──> R2 (consolidate) ──┐
                                        │                        │
R1-C (plist env) ───────────────────────┘                        │
                                                                 │
R3-A/B/C (settlement) ───> [calibration_pairs_v2 unblocked] ─────┼──> R4 (TIGGE)
R4-A/B/C (TIGGE) ─────────> [platt_models_v2 populated] ─────────┘
                                                                 │
R5 (density) — parallel with R2/R3                               │
R6 (hygiene) — parallel with R2-R5                               │
                                                                 ▼
                                                        DATA-READY state
```

R2 is not a strict blocker for R3-R5 — can run in parallel if bandwidth permits. But R2 completion before R4 is recommended (so R4-A/B/C runs through the new decoupled paths).

## Acceptance Criteria (measurable)

| AC | Command | Pass condition |
|---|---|---|
| AC-R0-1 | `PRAGMA table_info(forecasts)` | columns include `rebuild_run_id`, `data_source_version` |
| AC-R0-2 | `SELECT COUNT(*) FROM observation_instants_v2 WHERE running_max > 55 AND temp_unit='C'` | `= 0` |
| AC-R0-3 | `SELECT COUNT(*) FROM observation_instants_v2 WHERE running_max > 135 AND temp_unit='F'` | `= 0` |
| AC-R0-4 | `SELECT SUM(is_missing_local_hour) FROM observation_instants_v2 WHERE city IN (DST cities)` | > 0 (spring-forward dates marked) |
| AC-R2-1 | `pytest -q tests/test_ingest_isolation.py` | green |
| AC-R2-2 | Each `scripts/ingest/*.py --dry-run` | exit 0 |
| AC-R2-3 | `grep -c "_k2_.*_tick\|_ecmwf_open_data_cycle" src/main.py` | `= 0` (scheduler bindings removed) |
| AC-R2-4 | `ls ~/Library/LaunchAgents/com.zeus.ingest.*.plist` | 8 files exist |
| AC-R3-1 | `SELECT COUNT(*) FROM settlements_v2 WHERE settlement_value IS NULL` | `= 0` (or documented per-row exception) |
| AC-R3-2 | `SELECT COUNT(*) FROM settlements_v2 WHERE winning_bin IS NULL` | `= 0` |
| AC-R4-1 | `SELECT COUNT(*) FROM ensemble_snapshots_v2` | `> 0` |
| AC-R4-2 | `SELECT COUNT(*) FROM calibration_pairs_v2` | `> 0` |
| AC-R4-3 | `SELECT COUNT(*) FROM platt_models_v2` | `> 0` |
| AC-R6-G | `grep -rn "observation_instants[^_]" src/ scripts/` | `= 0` (no reader references legacy table post-R2) |

## Rollback per phase

| Phase | Rollback cost |
|---|---|
| R0-A | `ALTER TABLE … DROP COLUMN …` (SQLite 3.35+); 1 min |
| R0-B | Git revert writer; DB poisoned rows already gone (accept) |
| R0-C | Git revert writer + one-shot SQL UPDATE zeroing flags |
| R1-A | No change if investigation only; if resume issued → `pause_entries('manual_rollback')` |
| R1-B | Config rollback via git |
| R1-C | `launchctl unload` + plist revert |
| R2 | Per-script: `launchctl unload <plist>` + keep trading daemon's old scheduler bindings (do NOT remove R2-D until R2-C proven stable for N days) |
| R3 | `UPDATE settlements_v2 SET settlement_value=NULL, winning_bin=NULL WHERE [recovered_set]` |
| R4 | `DELETE FROM ensemble_snapshots_v2 WHERE ingested_at > <R4 start>` → same for pairs + models |
| R5 | per-source DELETE on `observation_instants_v2 WHERE source='meteostat_bulk_*' AND imported_at > <R5 start>` |
| R6 | Per-slice |

## Pre-mortem (top 5 risks)

**S1** — R2-D removing scheduler bindings breaks catch-up. Mitigation: ship R2-A/B/C first, validate 48h via plists, THEN R2-D. Do not squash.

**S2** — R3-B market bin thresholds unrecoverable from history. Mitigation: first pass uses Gamma API re-fetch; if markets are archived/hidden, document per-city and skip rather than synthesize.

**S3** — R4-B TIGGE extractor run requires operator manual action on cloud VM. Mitigation: script runbook, but execution is out of our control. AC-R4-* will not pass until operator performs this step.

**S4** — R0-B physical-bounds CHECK rejects rows from upstream corruption in future → missing data instead of poisoned data. Mitigation: log to `availability_fact.validator_block_events` so the gap is visible.

**S5** — R1-A resumes entries without understanding the original ValueError → re-triggers immediately. Mitigation: R1-A is investigation-only; resume gated on explicit operator confirmation after RCA.

## Open questions (flag for architect + critic)

Q1. R2-D: should trading daemon fully stop scheduling ingest, or keep a "safety-net" secondary scheduler that only fires if plist scheduler hasn't written a heartbeat in N hours? Trade-off: belt-and-suspenders redundancy vs. single-source-of-truth.

Q2. R3-B: if Gamma API re-fetch fails, what's the acceptable fallback? Options: skip those settlements, best-effort with documented uncertainty, block on operator.

Q3. R4-A: who owns the ECMWF subprocess fix — does it belong in `scripts/ingest/ecmwf_open_ens_tick.py` (R2 scope) or a separate ticket?

Q4. R6-B `delta_rate_per_h` — without visibility into which training code will use it, do we populate defensively or leave reserved?

Q5. Phase ordering — is there a tighter chain where R3 can start sooner (settlement completion does not strictly require R2 decoupling)?

## File inventory (proposed)

### Create
- `scripts/ingest/__init__.py`
- `scripts/ingest/_shared.py`
- `scripts/ingest/wu_icao_tick.py`
- `scripts/ingest/ogimet_tick.py`
- `scripts/ingest/openmeteo_hourly_tick.py`
- `scripts/ingest/openmeteo_solar_tick.py`
- `scripts/ingest/forecasts_tick.py`
- `scripts/ingest/ecmwf_open_ens_tick.py`
- `scripts/ingest/hko_tick.py` (move `scripts/hko_ingest_tick.py`)
- `scripts/ingest/hole_scan_tick.py`
- `scripts/backfill_settlement_values.py` (R3-A)
- `scripts/compute_settlement_winning_bins.py` (R3-B)
- `scripts/backfill_hk_observations_april.py` (R3-C)
- `scripts/backfill_delta_rate_per_h.py` (R6-B, conditional)
- `tests/test_ingest_isolation.py` (R2-B)
- `tests/test_obs_v2_physical_bounds.py` (R0-B)
- `tests/test_obs_v2_dst_missing_hour_flag.py` (R0-C)
- `tests/test_forecasts_schema_alignment.py` (R0-A)
- `tests/test_settlement_bin_resolution_complete.py` (R3-B)
- `tests/test_data_ingest_log_freshness.py` (R2 runtime evidence)
- `~/Library/LaunchAgents/com.zeus.ingest.*.plist` × 8 (R2-C)

### Modify
- `src/data/observation_instants_v2_writer.py` (R0-B physical bounds, R0-C DST flag fix)
- `src/main.py` (R2-D scheduler removal — last slice)
- `architecture/script_manifest.yaml` (register all new scripts)
- `architecture/test_topology.yaml` (register all new tests)
- Docs: current_data_state.md update path via proper packet evidence

### Delete
- `state/observations.db` (R6-A)
- `scripts/hko_ingest_tick.py` (moved to scripts/ingest/hko_tick.py — R2 leaves redirect stub for 30 days)
- Potentially `scripts/backfill_hko_daily.py` per inventory agent (audit first)

## Receipt binding

Closure evidence will be:
- `docs/operations/task_2026-04-23_data_readiness_remediation/work_log.md` (per-phase execution notes)
- `docs/operations/task_2026-04-23_data_readiness_remediation/receipt.json` (all AC commands + outputs)
- Final refresh of `docs/operations/current_data_state.md` with post-remediation posture (via same packet evidence)
