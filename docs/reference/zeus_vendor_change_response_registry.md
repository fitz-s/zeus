# Zeus Vendor Change Response Registry

Created: 2026-05-03
Last reused/audited: 2026-05-03
Authority basis: operator directive 2026-05-03 — "我们交易的是概率和polymarket的选择，不是完美的物理"
Status: REFERENCE / canonical surface map

## §1 The Principle

Polymarket (PM) is the **only** authority for settlement. Every weather vendor
in our pipeline (Wunderground, Ogimet, Meteostat, HKO, NOAA, TIGGE/ECMWF) is a
**dependency we chose to predict or reconstruct PM's settlement** — not a
source of truth in itself. This document maps every place in the codebase where
that dependency is encoded, so that when reality changes (PM switches sources,
WU silently mutates, or a new Lagos-class city appears) we can respond
completely instead of patching whack-a-mole.

The implicit assumptions our system makes about each vendor:

- **Wunderground (WU)** — primary settlement source for all 46 cities except
  Hong Kong. We assume: (a) the per-city `settlement_source` URL stays valid;
  (b) the underlying ICAO station mapping does not change; (c) page units (F vs
  C) match `cities.json` `unit`; (d) HTML parser keeps working; (e) hourly
  archive completeness ≥0.80 for healthy stations; (f) PM reads the same page.
  **Already-violated examples**: Lagos KIA01/DNMM hourly archive thinned mid-2025;
  unit conventions differ across UK/US.
- **Ogimet METAR (`/cgi-bin/getmetar`)** — fallback for DST-day spring-forward
  gaps + primary source for Istanbul/Moscow/Tel Aviv. We assume: throttle-free
  public CGI access, continuous historical archive, METAR temp/dewpoint regex
  pattern stable, our User-Agent not blocked.
- **Meteostat bulk** — used for sparse-day backfill on a per-station basis.
  We assume `bulk.meteostat.net/v2/hourly/{wmo}.csv.gz` continues to append.
  **Already-violated**: Lagos (WMO 65201) frozen at 2025-07-27.
- **HKO daily extract** — Hong Kong native authority. We assume HKO's daily
  observation API semantics are unchanged.
- **NOAA NWS / weather.gov** — proposed alternate source for some PM markets;
  not currently a primary source in our DB but probed for health. The PM
  settlement-source string for some markets says NOAA — we read via Ogimet
  METAR proxy because no programmatic NOAA API was wired.
- **TIGGE/ECMWF MARS** — forecast grid (52-member ensemble). We assume the
  grid cell at `(city.lat, city.lon)` is physically representative of the
  station microclimate. **Critical implicit coupling**: if PM moves the
  settlement station, our forecasts may now be aimed at the wrong geographic
  point even though our `(lat, lon)` for the city was unchanged.

## §2 The Five Trigger Scenarios

| Trigger | Description | Likelihood | Canonical example |
|---|---|---|---|
| **T1** | PM switches settlement source for one or more markets | LOW-MED | Hypothetical: PM moves NYC settlement from `KLGA` to `KJFK`; or globally from WU pages to NOAA NWS API |
| **T2** | WU silently changes data | MED | Page parser breaks (HTML restructure); units flip on a city; ICAO reassigned (DNMM ↔ DNAA); WU acquired & policies change |
| **T3** | New "Lagos-class" city discovered | MED | Existing city's primary source structurally fails. We trained on bad data unknowingly. Lagos itself (Phase 0). Future candidates: any city with `coverage_mean_90d < 0.85`. |
| **T4** | New "Shenzhen-class" city onboarded | MED | Adding a city to portfolio with no/limited vendor coverage. Multiple choices: which ICAO; which fallbacks; whether to wait for sample-size convergence. |
| **T5** | Vendor outage of indeterminate duration | HIGH | Meteostat bulk archive freeze (already real for Lagos). Ogimet stops (real for Lagos 2026-03-18). WU returns 200 OK but stale snapshot. TIGGE MARS downtime. |

Notes on likelihood: T1 has not happened in Zeus history but PM has changed
mappings in past markets per investigation logs. T5 has happened multiple
times this year. T2 is the most insidious because detection requires
canary-style probing.

## §3 The Dependency Surface (exhaustive)

### Layer 1 — Config

| File | Coupling | Breaks if |
|---|---|---|
| `config/cities.json` `cities[].wu_station` | ICAO that defines the settlement station and the WU URL | T1, T2 station reassignment |
| `config/cities.json` `cities[].settlement_source` | Full URL pointing at WU per-city page | T1, T2 |
| `config/cities.json` `cities[].meteostat_station` | WMO ID for fallback | T5 vendor freeze |
| `config/cities.json` `cities[].wu_pws` | Personal weather station ID (currently mostly unused) | – |
| `config/cities.json` `cities[].lat` / `.lon` | TIGGE grid cell selection | T1 if station physically moves |
| `config/cities.json` `cities[].historical_peak_hour` | Computed from THIS station's history | T1 station change → re-derive |
| `config/cities.json` `cities[].diurnal_amplitude_c/f` | Same | Same |
| `config/cities.json` `cities[].timezone` | Local-hour computation | Rare (city tz changes are external) |
| `config/cities.json` `cities[].unit` (`C` vs `F`) | Authority for unit cross-check; PM displays in this unit | T1 if PM relabels market |
| `config/city_monthly_bounds.json` (46 cities × 12 months) | Per-station physical bounds derived from training source | T1, T3 — must regenerate |

### Layer 2 — Schema

| Surface | Coupling |
|---|---|
| `observation_instants_v2.source` (TEXT NOT NULL) | Encodes vendor identity literally; ~80 distinct values currently (1 wu, 30+ meteostat, 30+ ogimet, 1 hko) |
| `observation_instants_v2.data_version` (default `'v1'`; in practice `'v1.wu-native'`) | Versions the namespace; cutover requires bump |
| `observation_instants_v2.authority` CHECK IN ('VERIFIED','UNVERIFIED','QUARANTINED') | Authority flag — invalidating a source means flipping rows |
| `observation_instants_v2.UNIQUE(city, source, utc_timestamp)` | Cutover must NOT collide |
| `settlements_v2.settlement_source` | URL stored per row | Audit trail of T1 history |
| `calibration_pairs_v2.bin_source` (default `'legacy'`) | Bin namespace; Platt training is sensitive |
| `calibration_pairs_v2.training_allowed` | The flip switch when source becomes untrusted |
| `calibration_pairs_v2.causality_status` | OK / DST_AMBIGUOUS — boundary marker |
| `platt_models_v2.*` | Trained on bin pairs from a specific source — invalidated on T1 |
| `ensemble_snapshots_v2` | TIGGE forecast tied to lat/lon |

### Layer 3 — Ingestion (per-vendor clients)

| File | Vendor | Source-tag emitted |
|---|---|---|
| `src/data/wu_hourly_client.py` | WU | `wu_icao_history` |
| `src/data/ogimet_hourly_client.py` | Ogimet METAR | `ogimet_metar_<icao>` |
| `src/data/daily_obs_append.py:1019` `OGIMET_CITIES` | Ogimet primary list (Istanbul, Moscow, Tel Aviv) | hardcoded ICAO + source_tag |
| `src/data/meteostat_bulk_client.py` | Meteostat | `meteostat_bulk_<icao>` |
| `src/data/observation_client.py` | shared HTTP client | – |
| `scripts/etl_tigge_*.py` | TIGGE/ECMWF | ensemble snapshots |
| `scripts/backfill_hko_daily.py` | HKO | `hko_hourly_accumulator` (daily decomposed) |
| `src/data/source_health_probe.py` (lines 70-419) | ALL vendors — health probes | open-meteo, wu_pws, hko, ogimet, ecmwf, noaa, tigge_mars |

### Layer 4 — Storage

| File | Coupling |
|---|---|
| `src/data/observation_instants_v2_writer.py` | Writes rows with hardcoded `source` string; gates on `tier_resolver.allowed_sources_for_city` |
| `src/ingest/harvester_truth_writer.py` | Settlement writer; gated by `ZEUS_HARVESTER_LIVE_ENABLED` env flag (line 443) |
| `src/data/daily_obs_append.py` | Per-source append paths; `OGIMET_CITIES` whitelist (line 1019) |

### Layer 5 — Tier Resolution / Fallback Chain

| Surface | Defines |
|---|---|
| `src/data/tier_resolver.py:55` `class Tier` | 3 tiers: WU_ICAO, OGIMET_METAR, HKO_NATIVE |
| `src/data/tier_resolver.py:152` `TIER_SCHEDULE` | Built from `cities.json` settlement-source classification |
| `src/data/tier_resolver.py:155-170` `TIER_ALLOWED_SOURCES` | Per-tier whitelist |
| `src/data/tier_resolver.py:182` `_build_expected_sources()` | Per-city primary source string |
| `src/data/tier_resolver.py:216` `_build_allowed_sources_by_city()` | Per-city primary + fallback set |
| `src/data/tier_resolver.py:88` `SOURCE_ROLE_FALLBACK_EVIDENCE` | Tags fallback sources as not-training-allowed by default |

**Critical**: tier 4 Ogimet (added 2026-04-15 per docstring line 42) is "deliberately limited" — no automatic Lagos-class onboarding without explicit code change.

### Layer 6 — Calibration

| Surface | Coupling |
|---|---|
| `calibration_pairs_v2` rows | Implicitly tagged by source via `data_version`; ~600k pairs/(city,track) for Tier 1, much less for newcomers |
| `platt_models_v2` | Per (city, metric, cluster, season) Platt fits — full retrain on T1 |
| `scripts/rebuild_calibration_pairs_canonical.py` | Cutover script |
| `scripts/etl_tigge_calibration.py` | TIGGE → calibration pairs |
| `docs/reference/zeus_calibration_weighting_authority.md` | Governs LOW-track training eligibility — per-city opt-out logic |

**Sample-size implication** (per `zeus_oracle_density_discount_reference.md` §5.3): Lagos LOW has ~120 verified pairs; Houston HIGH has ~600,000. Same source change has different impact: Lagos LOW retrain converges faster but starts from a position of insufficient regime absorption; Houston HIGH is statistically robust but a full retrain costs more compute.

### Layer 7 — Bridge

| File:Line | Coupling |
|---|---|
| `scripts/bridge_oracle_to_calibration.py:45-46` | imports `expected_source_for_city`, `allowed_sources_for_city` |
| `scripts/bridge_oracle_to_calibration.py:152-154` | computes `primary_source`, derives `fallback_sources` |
| `scripts/bridge_oracle_to_calibration.py:161` | gates mismatch counting on `(city, target_date, primary_source)` |

Bridge writes sole-tenant to `data/oracle_error_rates.json`. Any DDD integration must respect this contract.

### Layer 8 — Penalty

| File | Coupling |
|---|---|
| `src/strategy/oracle_penalty.py` | `_classify_rate` thresholds OK/INCIDENTAL/CAUTION/BLACKLIST; `_load` reads `data/oracle_error_rates.json` |
| (future) `src/strategy/data_density_discount.py` | DDD per `zeus_oracle_density_discount_reference.md` §6 |

### Layer 9 — Settlement

| File | Coupling |
|---|---|
| `src/ingest/harvester_truth_writer.py` | Writes `settlements_v2`; reads `settlement_source` URL |
| `src/execution/harvester.py` | Live trade outcome reconciliation |
| `scripts/rebuild_settlements.py` | Cutover script for settlement table |

### Layer 10 — Snapshot / Forecast

| File | Coupling |
|---|---|
| `scripts/_tigge_common.py` | Shared TIGGE accessor |
| `scripts/etl_tigge_*.py` (8 files) | TIGGE → ensemble snapshots |
| `scripts/extract_tigge_mn2t6_localday_min.py` / `extract_tigge_mx2t6_localday_max.py` | Localday min/max from TIGGE 6h |
| `scripts/backfill_tigge_snapshot_p_raw_v2.py` / `backfill_tigge_snapshot_p_raw.py` | Backfill scripts |
| `src/contracts/ensemble_snapshot_provenance.py` | Provenance tracking |

**Implicit coupling**: TIGGE selects grid cell from `cities.json` `(lat, lon)`. If T1 fires (PM moves station to a different physical location), our forecast is now aimed at the wrong place. Detection currently weak.

### Layer 11 — Test

Test files with hardcoded vendor identity (incomplete; ~25 files):

- `tests/test_tier_resolver.py` — antibody A3 invariants
- `tests/test_bridge_oracle_to_calibration.py`
- `tests/test_calibration_bins_canonical.py`
- `tests/test_canonical_data_versions_namespace.py`
- `tests/test_data_freshness_gate.py`
- `tests/test_harvester_dr33_live_enablement.py`
- `tests/test_harvester_metric_identity.py`
- `tests/test_hk_rejects_vhhh_source.py` (hardcodes VHHH ICAO rejection)
- `tests/test_ingestion_guard.py` (hardcodes Lagos/Wellington/Buenos Aires city-specific assertions)
- `tests/test_obs_v2_dst_missing_hour_flag.py`
- `tests/test_obs_v2_physical_bounds.py`
- `tests/test_data_rebuild_relationships.py`
- `tests/test_k2_live_ingestion_relationships.py`
- `tests/test_market_scanner_provenance.py`
- `tests/test_backfill_completeness_guardrails.py`
- `tests/test_calibration_manager.py`
- `tests/test_evaluator_strategy_key_failclosed.py`
- `tests/test_lifecycle.py`
- `tests/test_hourly_clients_parse.py`
- `tests/test_ensemble_client.py`, `test_ensemble_signal.py`
- `tests/test_instrument_invariants.py`
- `tests/test_config.py`
- `tests/test_db.py`
- `tests/runtime/test_legacy_snapshot_projection_upsert.py`
- `tests/test_center_buy_repair.py`

Pre-commit invariant baseline at `.claude/hooks/pre-commit-invariant-test.sh` — TEST_FILES list line 231; baseline 658/46. T1 likely requires baseline bump after rerouted writes.

### Layer 12 — Validation / AST guards

| File | Coupling |
|---|---|
| `src/data/ingestion_guard.py` | Physical bounds per (city, month) — derived from station's climate |
| `src/types/observation_atom.py:92` `authority Literal` | Three-state enum |
| `src/types/observation_atom.py:107-115` | Authority/validation_pass cross-checks |
| `tests/test_hourly_local_time_contract_ast.py` | AST guard listing source-aware scripts (TARGETS list) — NEW source needs entry |
| `scripts/check_dynamic_sql.py` | Per-file dynamic-SQL gate (see baseline test file) |
| `tests/test_dynamic_sql_baseline.py` | Locks per-file allowed dynamic-SQL count |
| `tests/test_contract_source_fields_baseline.py` | Locks per-file source-field literal count |
| `scripts/antibody_scan.py` | Cross-cutting antibody scanner |

### Layer 13 — Documentation (other reference docs that go stale)

| File | Goes stale on which trigger |
|---|---|
| `docs/reference/zeus_oracle_density_discount_reference.md` | T1, T3 (Lagos-class additions need section update) |
| `docs/reference/zeus_calibration_weighting_authority.md` | T1 (training eligibility lists) |
| `docs/reference/zeus_data_and_replay_reference.md` | T1, T2 |
| `docs/reference/zeus_market_settlement_reference.md` | T1 (PM source URLs) |
| `docs/reference/zeus_failure_modes_reference.md` | T2, T5 |
| `docs/operations/task_2026-05-02_full_launch_audit/LAGOS_GAP_FOLLOWUP.md` | T3 case study — keep updated |
| `architecture/digest_profiles.py` | Profiles reference source classifications |

### Layer 14 — Operations

| Surface | Coupling |
|---|---|
| `src/ingest/harvester_truth_writer.py` cron job (`ingest_main.py` minute 45 hourly) | Reads PM settlement; gated by `ZEUS_HARVESTER_LIVE_ENABLED` env |
| Per-vendor backfill scripts (`scripts/backfill_*.py`, ~10 files) | Each scoped to one vendor |
| `scripts/fill_obs_v2_dst_gaps.py` | Lagos-class fallback decision logic |
| `scripts/fill_obs_v2_meteostat.py` | Bulk fallback runner |
| `scripts/audit_observation_instants_v2.py` | Daily audit |
| `scripts/source_contract_auto_convert.py` | Source-tag namespace converter |
| `scripts/onboard_cities.py` | T4 entry point |
| `data/oracle_error_rates.json` | Sole writer: bridge — co-tenancy contract |
| `data/source_health.json` (output of source_health_probe) | Health surface — monitor for T2/T5 signals |
| `state/risk_state-live.db` | Live risk state — must reconcile on cutover |
| Operator runbooks (under `docs/runbooks/`) | Vendor-specific recovery procedures |

## §4 Per-Trigger Response Playbook

### T1 — PM switches settlement source

1. **Detection**:
   - `src/data/station_migration_probe.py:80` `compare_cities_against_gamma()` — already implemented, compares our `settlement_source` vs PM's gamma API
   - Daily probe job; alert when `compare_cities_against_gamma` returns mismatches
   - SQL: `SELECT settlement_source, COUNT(*) FROM settlements_v2 WHERE recorded_at > date('now', '-7 days') GROUP BY settlement_source` — alert if a new URL appears
2. **Containment**:
   - Set `ZEUS_HARVESTER_LIVE_ENABLED=0` in daemon env; harvester stops writing PM truth
   - Set per-city blacklist in `oracle_penalty.py` (`oracle_error_rate := 0.99` for impacted cities until cutover complete)
   - Halt entries via existing entry guard
3. **Investigation**:
   - Diff old vs new `settlement_source` URLs — identify station change vs URL-format change
   - Run `station_migration_probe.run_probe()` for full report
   - Check if new station already has `wu_station` row in `cities.json` (some PM-side moves are within-vendor)
   - Quantify Platt sample loss: `SELECT COUNT(*) FROM calibration_pairs_v2 WHERE city=X AND authority='VERIFIED'`
4. **Repair (in order)**:
   1. Layer 1: update `cities.json` (`wu_station`, `settlement_source`, possibly `lat/lon`, `historical_peak_hour`, `diurnal_amplitude_*`)
   2. Layer 1: regenerate `city_monthly_bounds.json` for impacted cities (`scripts/generate_monthly_bounds.py`)
   3. Layer 5: update `tier_resolver._build_expected_sources` if new source-tag emerges
   4. Layer 2: bump `data_version` (e.g., `v2.<date>-cutover`)
   5. Layer 3: backfill new source for at least 90 days of history (volume permitting)
   6. Layer 6: mark old-version Platt pairs `training_allowed=0`; rebuild Platt models on new pairs
   7. Layer 11: update affected tests (search for old ICAO/source string)
   8. Layer 13: bump pre-commit baseline if test count changes
5. **Verification**:
   - Re-run `station_migration_probe.compare_cities_against_gamma` → 0 mismatches
   - Run bridge for last 30 days; mismatch_rate within historical band
   - Confirm DDD does not auto-trip from city_floor drop
6. **Backfill**:
   - **Do NOT delete old-version `observation_instants_v2` rows** — they're needed for historical audit and replay fidelity
   - Set `training_allowed=0` on all old-version `calibration_pairs_v2` rows for impacted cities
   - Document in `docs/operations/task_<date>_t1_cutover_<city>/` per existing convention

### T2 — WU silently changes data

1. **Detection**:
   - `src/data/source_health_probe.py:113` `_probe_wu_pws()` — synthetic canary check
   - Per-city per-day mismatch jump (bridge result drift > 2σ vs 30-day baseline)
   - Unit-flipping is the most insidious: spec says F but page returns C → contamination case in `tests/test_ingestion_guard.py` already protects against single-row, but a global flip needs a separate unit-consistency monitor
2. **Containment**:
   - Per-city blacklist via `oracle_penalty` (set rate to 0.99 for impacted cities)
   - Halt new `observation_instants_v2` writes by raising `IngestionRejected` from guard
3. **Investigation**:
   - WebFetch the suspect URL directly (cf. Lagos investigation 2026-05-02)
   - Compare HTML structure to last known-good captured page
   - Inspect last N parsed rows for unit, value range, station_id consistency
4. **Repair**:
   - Layer 3: patch parser
   - Layer 1: update unit if PM relabeled
   - Layer 12: tighten ingestion_guard.physical_bounds if values now in different range
   - Layer 6: mark recent rows in calibration_pairs_v2 with `causality_status` flag if contamination detected
5. **Verification**:
   - Replay the parser against a captured-good page; assert byte-equal extraction
   - Re-enable city; monitor mismatch_rate for 7 days
6. **Backfill**:
   - Quarantine contaminated rows: `UPDATE observation_instants_v2 SET authority='QUARANTINED' WHERE ...`
   - Re-fetch via fallback (Ogimet/Meteostat) for affected window if vendor doesn't restore

### T3 — New "Lagos-class" city discovered

(Mirrors what was done for Lagos in this session; see `docs/operations/task_2026-05-02_full_launch_audit/LAGOS_GAP_FOLLOWUP.md`)

1. **Detection**:
   - DDD city_floor drops below `HARD_FLOOR_FOR_SETTLEMENT` and stays
   - Per-city Platt sample count grows abnormally slowly
   - Coverage variance (90d std) exceeds threshold
2. **Containment**:
   - Add city to oracle_penalty CAUTION list with kelly_mult ≤ 0.95
   - Do NOT blacklist (per operator directive 2026-05-02 — convert thinness to discount)
3. **Investigation**:
   - Per-source coverage: `SELECT source, COUNT(DISTINCT utc_timestamp) FROM observation_instants_v2 WHERE city=X AND target_date >= '...' GROUP BY source`
   - WebFetch primary settlement URL — is upstream alive?
   - Per-fallback coverage check (Meteostat archive freshness, Ogimet stoppage date)
   - Calibration sample count by lead/season — small-sample multiplier zone?
4. **Repair**:
   1. Layer 1: per-city `hard_floor_for_settlement` override in `cities.json` if vendor structurally limited
   2. Layer 5: extend `OGIMET_CITIES` whitelist (line 1019) IF Ogimet is a viable fallback for THAT city's PM-settled station
   3. Layer 5: add fallback source tag to `_build_allowed_sources_by_city()` allowed set
   4. Layer 8: future DDD will activate per `zeus_oracle_density_discount_reference.md` §6 — small-sample multiplier compensates for low Platt samples
   5. Layer 14: trigger backfill via `fill_obs_v2_dst_gaps.py` or new fallback runner
5. **Verification**:
   - Bridge runs full 90-day window; mismatch + DDD both classified
   - DDD doesn't fire on routine days, fires on outage days
6. **Backfill**:
   - Mark thin-coverage days as `training_allowed=0` (don't pollute Platt with bad-data days)
   - Restart Platt training pipeline for the city

### T4 — New "Shenzhen-class" city onboarded

1. **Detection**:
   - Operator-driven (PM lists new market)
2. **Containment**:
   - City entered `oracle_penalty` BLACKLIST until validation complete (no live trades)
3. **Investigation**:
   - Determine PM settlement source URL → primary station
   - Check WU page existence + parser compatibility
   - Probe Ogimet & Meteostat for the same station
   - Establish historical archive depth (need ≥1 year for seasonal Platt)
4. **Repair**:
   1. Layer 1: add to `cities.json` (all required fields)
   2. Layer 5: assign tier in `tier_resolver` (default: WU_ICAO if WU primary; OGIMET_METAR if PM uses NOAA-style settlement)
   3. Layer 1: generate `city_monthly_bounds.json` row (may be NULL if <30 samples; lat-band fallback in guard)
   4. Layer 3: ensure clients accept the new ICAO
   5. Layer 12: extend AST guard TARGETS list if new script written
   6. Layer 6: backfill ≥365 days of `observation_instants_v2` + run Platt training
   7. Layer 8: keep DDD multiplier-adjusted small-sample floor active until N ≥ 1000 pairs
5. **Verification**:
   - Test entry: `tests/test_config.py` city-mapping invariants
   - Shadow trade for 14+ days; mismatch < 5%
6. **Backfill**:
   - N/A — onboarding starts clean

### T5 — Vendor outage of indeterminate duration

1. **Detection**:
   - `source_health_probe` daily output flips to "unhealthy"
   - Per-source observation count for last 24h drops below threshold
   - Bridge mismatch_rate spikes
2. **Containment**:
   - DDD shortfall fires (per `zeus_oracle_density_discount_reference.md` §6)
   - Day-0 circuit breaker (§7) rejects entries with `entries_blocked_reason="day0_observation_gap"`
3. **Investigation**:
   - Direct WebFetch of vendor URL — alive but stale, dead, or geofenced?
   - Compare vendor health probe history — duration since last successful probe
4. **Repair**:
   - If short outage (<7 days): wait + monitor; DDD penalizes appropriately
   - If long outage: trigger fallback chain via `tier_resolver.allowed_sources_for_city`
   - Already-violated example (Meteostat Lagos): no recovery from our side — vendor-frozen
5. **Verification**:
   - Daily coverage returns to within 1σ of `city_floor`
   - DDD shortfall returns to 0
6. **Backfill**:
   - Run fallback ingest for the gap window (Ogimet, Meteostat)
   - Mark fallback rows `training_allowed=0` if fallback is not the PM-settling source
   - Bridge re-evaluates mismatch on backfilled rows

## §5 The "Things Normal People Miss" Appendix

These are the load-bearing couplings that look innocuous in code review but
break catastrophically on T1/T2/T3:

1. **`historical_peak_hour` and `diurnal_amplitude` in `cities.json` are
   physically tied to the specific microclimate of the station, not the city
   centroid.** Buckley Space Force Base (KBKF) is east of Denver; downtown
   Denver peaks ~30 min later. If PM moves NYC settlement from KLGA (LaGuardia,
   marine influenced) to KJFK (more inland), peak hour shifts ~30 min. Our DDD
   directional-coverage windows would aim at the wrong target hours and
   overstate / understate coverage.

2. **TIGGE grid cell is selected from `(city.lat, city.lon)`, NOT from the
   station coordinate.** If PM moves to a station 50 km away, our forecasts
   are now aimed at the wrong point. Detection: check if the new station's
   coordinates differ materially from the city's stored lat/lon. Fix: bump
   data_version + re-extract TIGGE for new lat/lon + re-run Platt.

3. **`OGIMET_CITIES` whitelist (`src/data/daily_obs_append.py:1019`) hardcodes
   3 cities (Istanbul, Moscow, Tel Aviv).** Adding a new "Lagos-class" city
   that wants Ogimet as a primary fallback requires editing this dict. Easy to
   miss because the writer's tier-level whitelist allows the source-tag
   without requiring the dict entry.

4. **Per-source `meteostat_bulk_<icao>` distinct values in
   `observation_instants_v2.source` (~30 currently) — adding a city requires
   a new source-tag enumeration.** A naive add of a city without bumping
   `tier_resolver.TIER_ALLOWED_SOURCES` will silently get rejected by writer
   antibody A2.

5. **`calibration_pairs_v2.training_allowed` is the only flip switch from
   "trusted training data" to "quarantined".** Setting `authority='QUARANTINED'`
   is informational; `training_allowed=0` is the actual gate. T1 cutover must
   set `training_allowed=0` for old rows; missing this means Platt continues
   training on stale-vendor pairs.

6. **`calibration_pairs_v2.bin_source` (default `'legacy'`) is a separate
   namespace from `data_version`.** Bin definitions can change independently
   of source. A T1 cutover that adds a new bin schema must increment
   `bin_source` to avoid mixing pre/post bins in the same Platt fit.

7. **Pre-commit invariant baseline locks 658/46.** Cutovers that re-route
   tests will change pass/skip counts; baseline must be bumped or commits
   will be blocked. The `[skip-invariant]` marker is a documented escape but
   must be paired with a same-day baseline-update commit.

8. **`cities.json` `_settlement_source_discipline` and `_changelog` fields**
   are top-level audit metadata. T1 cutover must update `_changelog` per
   convention.

9. **AST guard at `tests/test_hourly_local_time_contract_ast.py`** has a
   TARGETS list (~21 paths). Adding new ingestion script must extend this
   list or AST coverage gap opens.

10. **`config/city_monthly_bounds.json` provenance fields** (`generated_at`,
    `source`, `script`, `tigge_date_range`) — T1 regeneration must update all.
    Stale `generated_at` is the only easy detection signal that this file is
    out of sync with cities.

11. **Authority enum is mirrored across multiple tables** —
    `observation_instants_v2.authority`, `calibration_pairs_v2.authority`,
    `settlements_v2.authority`. T1 cutover quarantine must propagate
    consistently across all three to maintain join-time integrity.

12. **`station_migration_probe` writes to `data/source_health.json`**, but
    consumers may be few. Operator-side alerting on this file's content is
    the actual T1 detection — if alerts aren't wired, probe runs silently.

13. **Settlement URL string contains country/state/city slug** that may not
    match `cities.json` `slug_names` field. Slug drift is a common silent
    breakage — PM may rename their URL slug without changing the underlying
    station; our hardcoded URLs go 404.

14. **Lagos's source-distribution contains `meteostat_bulk_dnmm` rows from
    pre-2025-07-27 that are still present and marked authoritative.** If
    they're used in DDD city_floor calculation, they bias the floor upward
    relative to today's reality. Floor calculation must explicitly query for
    "primary source only" rows, not "all sources".

15. **`OGIMET_CITIES` is documented as "PM uses NOAA but no programmatic
    NOAA API" — meaning the source_tag `ogimet_metar_<icao>` IS the PM
    settlement source for those 3 cities.** If NOAA ever opens an API, the
    tier should be reclassified — code change scope is wider than it looks.

16. **`data_version` is the cutover boundary marker, but multiple consumers
    read it differently.** Bridge filters strictly on `v1.wu-native`;
    Platt training reads broader; some scripts ignore it entirely. T1 must
    audit every reader before declaring cutover.

## §6 What to Monitor (proactive surveillance)

Add these to the existing `source_health_probe` output and monitoring stack:

| Cadence | Check | Implementation hint |
|---|---|---|
| Daily | settlement_source string consistency | `SELECT DISTINCT settlement_source FROM settlements_v2 WHERE recorded_at > date('now', '-7 days')` — alert on any new URL |
| Daily | Per-city distinct sources count | `SELECT city, COUNT(DISTINCT source) FROM observation_instants_v2 WHERE target_date > date('now','-7 days') GROUP BY city HAVING COUNT(DISTINCT source) != prior_week_count` |
| Daily | `compare_cities_against_gamma` mismatch list | already implemented in `src/data/station_migration_probe.py:80`; ensure it RUNS and alerts |
| Daily | Per-source freshness | `MAX(target_date) by source` — alert if any source's max lags current_date - 2 days |
| Daily | `source_health.json` parse — flag unhealthy probes | output of `source_health_probe` |
| Weekly | Per-(city, track) Platt sample count | `SELECT city, temperature_metric, COUNT(*) FROM calibration_pairs_v2 WHERE authority='VERIFIED' GROUP BY 1,2` — alert on >10% drop WoW |
| Weekly | Per-city 90d coverage trend | `SELECT city, AVG(distinct_hours/24.0) FROM ...` — alert when 7d MA drops > 5pp below 30d MA |
| Weekly | `city_monthly_bounds.json` freshness | check `generated_at` is within last 90 days |
| Per-event | PM page hash canary | curl + sha256 of last known good page; alert on mismatch |
| Per-event | Unit-flip canary | per-city ranking of recent observation values vs historical bounds; alert if recent week's mean is > 3σ outside |

## §7 Cross-references

- `docs/reference/zeus_oracle_density_discount_reference.md` — DDD canonical spec; depends on this registry for cutover-time invalidation rules
- `docs/reference/zeus_calibration_weighting_authority.md` — Platt training eligibility (T1 invalidation rules)
- `docs/reference/zeus_market_settlement_reference.md` — PM settlement concepts (T1 entry point)
- `docs/reference/zeus_data_and_replay_reference.md` — replay fidelity across data_version cutovers
- `docs/reference/zeus_failure_modes_reference.md` — generic failure taxonomy
- `docs/operations/task_2026-05-02_full_launch_audit/LAGOS_GAP_FOLLOWUP.md` — concrete T3 case study
- `docs/operations/task_2026-05-02_settlement_pipeline_audit/PLATT_HOUR_RESIDUAL_AUDIT.md` — empirical evidence for Platt regime absorption
- `src/data/tier_resolver.py` — Phase 0 tier authority code
- `src/data/station_migration_probe.py` — T1 detection code (already exists, ensure operational)
- `src/data/source_health_probe.py` — T2/T5 detection code
- `architecture/digest_profiles.py` — profile classifications referencing source tiers

## §8 Open Questions

These could not be definitively resolved from the codebase alone and need
operator input:

1. **Unit-flip handling mid-month**: if a vendor flips the unit on day 15 of a
   month, `observation_instants_v2` UNIQUE constraint on `(city, source,
   utc_timestamp)` doesn't catch it. Should the writer require unit
   consistency within a `(city, source)` partition?

2. **`city_monthly_bounds.json` regeneration on T1**: regenerate immediately
   with potentially-thin new-station data, or fall back to lat-band guards
   for transition window? Current code already handles missing entries via
   lat-band, so the latter is the safer default — but this should be
   explicit operator policy.

3. **Promotion criterion for `fallback_evidence` → primary**: when does a
   long-running fallback (e.g., we end up reading Ogimet for 6 months
   because WU never recovers) get promoted to primary in `tier_resolver`?
   No code-side threshold exists; currently operator manual override only.

4. **Data_version ratchet on T2 silent change**: should every detected T2
   automatically force a data_version bump, or is that operator-decided?
   Auto-bump is safer but expensive (Platt retrain cost).

5. **TIGGE re-extraction trigger**: if PM moves a station's coordinate by
   <10km, is full TIGGE re-extraction warranted? TIGGE grid cells are
   ~25-50km — submit-cell moves may be noise.
