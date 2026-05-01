# 03 Table-by-Table Truth Audit

## `observations`

- **Rows:** 42,749
- **Purpose:** Legacy daily station observation evidence; not safe canonical training without provenance retrofit.
- **Canonical/derived/evidence:** evidence/derived
- **External source feed:** Mostly WU daily history plus HKO/Ogimet daily backfills.
- **Writer code path:** daily_obs_append.py, backfill_wu_daily_all.py, backfill_hko_daily.py, backfill_ogimet_metar.py
- **Reader code path:** legacy calibration rebuilds, audits, settlement comparisons
- **Fields that must never be null or ambiguous:** city,target_date,source,station_id,high_temp,low_temp,unit,timezone,collection_window_*,authority,data_source_version,provenance_metadata
- **Current contract check:** Fails provenance contract for 39,431 rows; lacks temp_metric/physical_quantity/observation_field/training_allowed/causality_status.
- **Unsafe row classes:** missing provenance; null market/metric/source identity; fallback rows lacking role; local-hour-only rows; rows with null settlement values; empty causal rows by absence.
- **Use ruling:** live = NO; replay = NO unless market/forecast lineage exists; training = NO unless provenance/source-role/causality gates pass; evidence = YES with caveats.

## `settlements`

- **Rows:** 1,561
- **Purpose:** Legacy settlement evidence; high-only; not exact market replay truth.
- **Canonical/derived/evidence:** evidence/derived
- **External source feed:** WU/HKO settlement reconstruction evidence.
- **Writer code path:** settlement reconstruction/import paths; exact writer not fully proven from loaded rows
- **Reader code path:** settlement checks, legacy labels
- **Fields that must never be null or ambiguous:** market_slug,winning_bin,settlement_value,settlement_source,unit,temperature_metric,bin bounds,provenance_json
- **Current contract check:** All market_slug null; 49 null values; 92 null bins; unique city/date is unsafe.
- **Unsafe row classes:** missing provenance; null market/metric/source identity; fallback rows lacking role; local-hour-only rows; rows with null settlement values; empty causal rows by absence.
- **Use ruling:** live = NO; replay = NO unless market/forecast lineage exists; training = NO unless provenance/source-role/causality gates pass; evidence = YES with caveats.

## `settlements_v2`

- **Rows:** 0
- **Purpose:** Intended canonical dual-metric settlement table.
- **Canonical/derived/evidence:** canonical intended but empty
- **External source feed:** None populated.
- **Writer code path:** db migration/scaffolding only in current DB
- **Reader code path:** future v2 settlement consumers
- **Fields that must never be null or ambiguous:** city,target_date,temperature_metric,market identity,settlement value/source,authority,provenance
- **Current contract check:** Empty, therefore not usable.
- **Unsafe row classes:** missing provenance; null market/metric/source identity; fallback rows lacking role; local-hour-only rows; rows with null settlement values; empty causal rows by absence.
- **Use ruling:** live = NO; replay = NO unless market/forecast lineage exists; training = NO unless provenance/source-role/causality gates pass; evidence = NO rows.

## `observation_instants_v2`

- **Rows:** 1,813,662
- **Purpose:** Best populated hourly evidence table with provenance/time geometry.
- **Canonical/derived/evidence:** evidence/derived
- **External source feed:** WU hourly, Ogimet fallback, Meteostat bulk fallback, HKO accumulator lane.
- **Writer code path:** backfill_obs_v2.py, fill_obs_v2_dst_gaps.py, fill_obs_v2_meteostat.py, observation_instants_v2_writer.py
- **Reader code path:** observation_instants_current view, etl_hourly_observations.py, possible live/monitoring paths
- **Fields that must never be null or ambiguous:** city,target_date,source,timezone_name,local_timestamp,utc_timestamp,offset,DST flags,temp fields,station,authority,data_version,provenance
- **Current contract check:** Populated and provenance-present, but lacks metric/training/causality/source_role fields; current view includes 68 sources under v1.wu-native.
- **Unsafe row classes:** missing provenance; null market/metric/source identity; fallback rows lacking role; local-hour-only rows; rows with null settlement values; empty causal rows by absence.
- **Use ruling:** live = guarded evidence only; replay = NO unless market/forecast lineage exists; training = NO unless provenance/source-role/causality gates pass; evidence = YES with caveats.

## `observation_instants`

- **Rows:** 868,305
- **Purpose:** Legacy Open-Meteo-style hourly instant evidence.
- **Canonical/derived/evidence:** evidence/derived
- **External source feed:** Open-Meteo Archive/grid lane.
- **Writer code path:** hourly_instants_append.py, backfill_hourly_openmeteo.py
- **Reader code path:** coverage/audits and legacy ETL
- **Fields that must never be null or ambiguous:** UTC/local/timezone/DST/temp fields/source
- **Current contract check:** No authority/data_version/provenance_json; missing running_min; evidence only.
- **Unsafe row classes:** missing provenance; null market/metric/source identity; fallback rows lacking role; local-hour-only rows; rows with null settlement values; empty causal rows by absence.
- **Use ruling:** live = NO; replay = NO unless market/forecast lineage exists; training = NO unless provenance/source-role/causality gates pass; evidence = YES with caveats.

## `hourly_observations`

- **Rows:** 1,813,568
- **Purpose:** Lossy compatibility table, not canonical.
- **Canonical/derived/evidence:** evidence/derived
- **External source feed:** Derived from hourly instants/v2; source tags retained only.
- **Writer code path:** etl_hourly_observations.py and legacy scripts
- **Reader code path:** legacy consumers
- **Fields that must never be null or ambiguous:** city,obs_date,obs_hour,temp,temp_unit,source
- **Current contract check:** No UTC timestamp/timezone/provenance/authority/station; cannot represent DST duplicate local hours; ETL uses COALESCE temp_current/running_max.
- **Unsafe row classes:** missing provenance; null market/metric/source identity; fallback rows lacking role; local-hour-only rows; rows with null settlement values; empty causal rows by absence.
- **Use ruling:** live = NO; replay = NO unless market/forecast lineage exists; training = NO unless provenance/source-role/causality gates pass; evidence = YES with caveats.

## `forecasts`

- **Rows:** 0
- **Purpose:** Legacy deterministic forecast table.
- **Canonical/derived/evidence:** evidence/derived
- **External source feed:** Open-Meteo previous-runs/forecast append intended.
- **Writer code path:** forecasts_append.py, etl_historical_forecasts.py
- **Reader code path:** historical forecast consumers/calibration if allowed
- **Fields that must never be null or ambiguous:** source,forecast_basis_date,forecast_issue_time,lead,forecast_high/low,retrieved/imported
- **Current contract check:** Empty; coverage ledger says forecasts missing at scale.
- **Unsafe row classes:** missing provenance; null market/metric/source identity; fallback rows lacking role; local-hour-only rows; rows with null settlement values; empty causal rows by absence.
- **Use ruling:** live = NO; replay = NO unless market/forecast lineage exists; training = NO unless provenance/source-role/causality gates pass; evidence = NO rows.

## `historical_forecasts_v2`

- **Rows:** 0
- **Purpose:** Intended v2 historical forecast lane.
- **Canonical/derived/evidence:** canonical intended but empty
- **External source feed:** Historical forecast/previous-runs source.
- **Writer code path:** etl_historical_forecasts.py/migrations
- **Reader code path:** v2 calibration/replay intended
- **Fields that must never be null or ambiguous:** temperature_metric,forecast_value,available_at,authority,data_version,provenance
- **Current contract check:** Empty; no causal training rows.
- **Unsafe row classes:** missing provenance; null market/metric/source identity; fallback rows lacking role; local-hour-only rows; rows with null settlement values; empty causal rows by absence.
- **Use ruling:** live = NO; replay = NO unless market/forecast lineage exists; training = NO unless provenance/source-role/causality gates pass; evidence = NO rows.

## `ensemble_snapshots`

- **Rows:** 0
- **Purpose:** Legacy ensemble table.
- **Canonical/derived/evidence:** evidence/derived
- **External source feed:** TIGGE/Open-Meteo ensemble intended.
- **Writer code path:** ensemble/TIGGE loaders
- **Reader code path:** legacy p_raw scripts
- **Fields that must never be null or ambiguous:** issue/valid/available/fetch, members_json, p_raw_json
- **Current contract check:** Empty.
- **Unsafe row classes:** missing provenance; null market/metric/source identity; fallback rows lacking role; local-hour-only rows; rows with null settlement values; empty causal rows by absence.
- **Use ruling:** live = NO; replay = NO unless market/forecast lineage exists; training = NO unless provenance/source-role/causality gates pass; evidence = NO rows.

## `ensemble_snapshots_v2`

- **Rows:** 0
- **Purpose:** Intended canonical high/low ensemble forecast snapshots.
- **Canonical/derived/evidence:** canonical intended but empty
- **External source feed:** TIGGE mx2t6/mn2t6 local-day extractors.
- **Writer code path:** extract_tigge_mx2t6_localday_max.py, extract_tigge_mn2t6_localday_min.py
- **Reader code path:** rebuild_calibration_pairs_v2.py, p_raw v2 backfill
- **Fields that must never be null or ambiguous:** temperature_metric,physical_quantity,observation_field,issue_time,available_at,fetch_time,members,training_allowed,causality_status,provenance
- **Current contract check:** Schema is strong but empty.
- **Unsafe row classes:** missing provenance; null market/metric/source identity; fallback rows lacking role; local-hour-only rows; rows with null settlement values; empty causal rows by absence.
- **Use ruling:** live = NO; replay = NO unless market/forecast lineage exists; training = NO unless provenance/source-role/causality gates pass; evidence = NO rows.

## `calibration_pairs`

- **Rows:** 0
- **Purpose:** Legacy calibration rows.
- **Canonical/derived/evidence:** evidence/derived
- **External source feed:** Derived from forecasts and observations/settlements.
- **Writer code path:** rebuild_calibration_pairs_canonical.py
- **Reader code path:** calibration/model training
- **Fields that must never be null or ambiguous:** p_raw,outcome,lead,season,source/version
- **Current contract check:** Empty.
- **Unsafe row classes:** missing provenance; null market/metric/source identity; fallback rows lacking role; local-hour-only rows; rows with null settlement values; empty causal rows by absence.
- **Use ruling:** live = NO; replay = NO unless market/forecast lineage exists; training = NO unless provenance/source-role/causality gates pass; evidence = NO rows.

## `calibration_pairs_v2`

- **Rows:** 0
- **Purpose:** Intended v2 metric-specific calibration rows.
- **Canonical/derived/evidence:** canonical intended but empty
- **External source feed:** Derived from ensemble_snapshots_v2 and verified observations.
- **Writer code path:** rebuild_calibration_pairs_v2.py
- **Reader code path:** platt_models_v2/training
- **Fields that must never be null or ambiguous:** snapshot_id,temperature_metric,p_raw,outcome,forecast_available_at,training_allowed,causality_status,data_version
- **Current contract check:** Empty; rebuild code can pick latest verified observation by source order unless source-role guarded.
- **Unsafe row classes:** missing provenance; null market/metric/source identity; fallback rows lacking role; local-hour-only rows; rows with null settlement values; empty causal rows by absence.
- **Use ruling:** live = NO; replay = NO unless market/forecast lineage exists; training = NO unless provenance/source-role/causality gates pass; evidence = NO rows.

## `market_events / market_events_v2`

- **Rows:** 0
- **Purpose:** Market rule/event registry.
- **Canonical/derived/evidence:** canonical intended but empty
- **External source feed:** Polymarket client/exchange sources intended.
- **Writer code path:** polymarket_client.py and market ingestion paths
- **Reader code path:** replay, settlement, live decision joins
- **Fields that must never be null or ambiguous:** condition/token/slug/rule/source/unit/bin/close/finalization
- **Current contract check:** Both empty; settlement rows cannot be tied to contracts.
- **Unsafe row classes:** missing provenance; null market/metric/source identity; fallback rows lacking role; local-hour-only rows; rows with null settlement values; empty causal rows by absence.
- **Use ruling:** live = NO; replay = NO unless market/forecast lineage exists; training = NO unless provenance/source-role/causality gates pass; evidence = NO rows.

## `data_coverage`

- **Rows:** 346,417
- **Purpose:** Coverage/missingness ledger.
- **Canonical/derived/evidence:** audit ledger
- **External source feed:** Hole scanner and append/backfill scripts.
- **Writer code path:** hole_scanner.py and writers
- **Reader code path:** audits/readiness
- **Fields that must never be null or ambiguous:** table,city,source,target_date,status,reason,fetched/expected/retry
- **Current contract check:** Useful; confirms forecast missingness and observation gaps.
- **Unsafe row classes:** missing provenance; null market/metric/source identity; fallback rows lacking role; local-hour-only rows; rows with null settlement values; empty causal rows by absence.
- **Use ruling:** live = NO; replay = NO unless market/forecast lineage exists; training = NO unless provenance/source-role/causality gates pass; evidence = YES with caveats.

## `solar_daily / diurnal_* / temp_persistence`

- **Rows:** 58,898
- **Purpose:** Derived feature/evidence tables.
- **Canonical/derived/evidence:** evidence/derived
- **External source feed:** Weather/sun/observation-derived features.
- **Writer code path:** feature builders not fully audited here
- **Reader code path:** model features/monitoring
- **Fields that must never be null or ambiguous:** source inputs and data_version should be traceable
- **Current contract check:** Usable only if upstream source row lineage is made explicit.
- **Unsafe row classes:** missing provenance; null market/metric/source identity; fallback rows lacking role; local-hour-only rows; rows with null settlement values; empty causal rows by absence.
- **Use ruling:** live = NO; replay = NO unless market/forecast lineage exists; training = NO unless provenance/source-role/causality gates pass; evidence = YES with caveats.