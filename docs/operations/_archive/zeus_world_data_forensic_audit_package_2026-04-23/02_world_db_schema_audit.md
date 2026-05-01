# 02 World DB Schema Audit

## Inventory

Total user tables inspected: 52. The table inventory shows a split between populated observation/evidence tables and empty trading/model/replay scaffolding.

| Table | Rows |
|---|---:|
| `availability_fact` | 2,368 |
| `calibration_decision_group` | 0 |
| `calibration_pairs` | 0 |
| `calibration_pairs_v2` | 0 |
| `chronicle` | 0 |
| `control_overrides_history` | 4 |
| `data_coverage` | 346,417 |
| `day0_metric_fact` | 0 |
| `day0_residual_fact` | 0 |
| `decision_log` | 0 |
| `diurnal_curves` | 4,800 |
| `diurnal_peak_prob` | 14,400 |
| `ensemble_snapshots` | 0 |
| `ensemble_snapshots_v2` | 0 |
| `execution_fact` | 0 |
| `forecast_error_profile` | 0 |
| `forecast_skill` | 0 |
| `forecasts` | 0 |
| `historical_forecasts` | 0 |
| `historical_forecasts_v2` | 0 |
| `hko_hourly_accumulator` | 8 |
| `hourly_observations` | 1,813,568 |
| `market_events` | 0 |
| `market_events_v2` | 0 |
| `market_price_history` | 0 |
| `model_bias` | 0 |
| `observation_instants` | 868,305 |
| `observation_instants_v2` | 1,813,662 |
| `observations` | 42,749 |
| `opportunity_fact` | 0 |
| `outcome_fact` | 0 |
| `platt_models` | 0 |
| `platt_models_v2` | 0 |
| `position_current` | 0 |
| `position_events` | 0 |
| `probability_trace_fact` | 0 |
| `replay_results` | 0 |
| `rescue_events_v2` | 0 |
| `risk_actions` | 0 |
| `selection_family_fact` | 0 |
| `selection_hypothesis_fact` | 0 |
| `settlements` | 1,561 |
| `settlements_v2` | 0 |
| `shadow_signals` | 0 |
| `solar_daily` | 38,279 |
| `strategy_health` | 0 |
| `temp_persistence` | 1,419 |
| `token_price_log` | 0 |
| `token_suppression` | 0 |
| `token_suppression_history` | 0 |
| `trade_decisions` | 0 |
| `zeus_meta` | 1 |

## Critical table counts

| Table | Rows |
|---|---:|
| `calibration_pairs` | 0 |
| `calibration_pairs_v2` | 0 |
| `data_coverage` | 346,417 |
| `diurnal_curves` | 4,800 |
| `diurnal_peak_prob` | 14,400 |
| `ensemble_snapshots` | 0 |
| `ensemble_snapshots_v2` | 0 |
| `forecasts` | 0 |
| `historical_forecasts` | 0 |
| `historical_forecasts_v2` | 0 |
| `hko_hourly_accumulator` | 8 |
| `hourly_observations` | 1,813,568 |
| `market_events` | 0 |
| `market_events_v2` | 0 |
| `observation_instants` | 868,305 |
| `observation_instants_v2` | 1,813,662 |
| `observations` | 42,749 |
| `platt_models` | 0 |
| `platt_models_v2` | 0 |
| `replay_results` | 0 |
| `settlements` | 1,561 |
| `settlements_v2` | 0 |
| `solar_daily` | 38,279 |
| `temp_persistence` | 1,419 |

## Critical schema excerpts

### `observations`

```sql
CREATE TABLE observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            high_temp REAL,
            low_temp REAL,
            unit TEXT NOT NULL,
            station_id TEXT,
            fetched_at TEXT,
            -- K1 additions: raw value/unit contract
            raw_value REAL,
            raw_unit TEXT CHECK (raw_unit IN ('F', 'C', 'K')),
            target_unit TEXT CHECK (target_unit IN ('F', 'C')),
            value_type TEXT CHECK (value_type IN ('high', 'low', 'mean')),
            -- K1 additions: temporal provenance
            fetch_utc TEXT,
            local_time TEXT,
            collection_window_start_utc TEXT,
            collection_window_end_utc TEXT,
            -- K1 additions: DST context
            timezone TEXT,
            utc_offset_minutes INTEGER,
            dst_active INTEGER CHECK (dst_active IN (0, 1)),
            is_ambiguous_local_hour INTEGER CHECK (is_ambiguous_local_hour IN (0, 1)),
            is_missing_local_hour INTEGER CHECK (is_missing_local_hour IN (0, 1)),
            -- K1 additions: geographic/seasonal
            hemisphere TEXT CHECK (hemisphere IN ('N', 'S')),
            season TEXT CHECK (season IN ('DJF', 'MAM', 'JJA', 'SON')),
            month INTEGER CHECK (month BETWEEN 1 AND 12),
            -- K1 additions: run provenance
            rebuild_run_id TEXT,
            data_source_version TEXT,
            -- K1 additions: authority + extensibility
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED' CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
            provenance_metadata TEXT,  -- JSON
            UNIQUE(city, target_date, source)
        )
```
### `settlements`

```sql
CREATE TABLE settlements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            market_slug TEXT,
            winning_bin TEXT,
            settlement_value REAL,
            settlement_source TEXT,
            settled_at TEXT,
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED' CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')), pm_bin_lo REAL, pm_bin_hi REAL, unit TEXT, settlement_source_type TEXT, temperature_metric TEXT CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')), physical_quantity TEXT, observation_field TEXT CHECK (observation_field IS NULL OR observation_field IN ('high_temp','low_temp')), data_version TEXT, provenance_json TEXT,
            UNIQUE(city, target_date)
        )
```
### `settlements_v2`

```sql
CREATE TABLE settlements_v2 (
                settlement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL
                    CHECK (temperature_metric IN ('high', 'low')),
                market_slug TEXT,
                winning_bin TEXT,
                settlement_value REAL,
                settlement_source TEXT,
                settled_at TEXT,
                authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
                    CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
                provenance_json TEXT NOT NULL DEFAULT '{}',
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(city, target_date, temperature_metric)
            )
```
### `observation_instants_v2`

```sql
CREATE TABLE observation_instants_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                source TEXT NOT NULL,
                timezone_name TEXT NOT NULL,
                local_hour REAL,
                local_timestamp TEXT NOT NULL,
                utc_timestamp TEXT NOT NULL,
                utc_offset_minutes INTEGER NOT NULL,
                dst_active INTEGER NOT NULL DEFAULT 0,
                is_ambiguous_local_hour INTEGER NOT NULL DEFAULT 0,
                is_missing_local_hour INTEGER NOT NULL DEFAULT 0,
                time_basis TEXT NOT NULL,
                temp_current REAL,
                running_max REAL,
                running_min REAL,
                delta_rate_per_h REAL,
                temp_unit TEXT NOT NULL,
                station_id TEXT,
                observation_count INTEGER,
                raw_response TEXT,
                source_file TEXT,
                imported_at TEXT NOT NULL, authority TEXT NOT NULL DEFAULT 'UNVERIFIED', data_version TEXT NOT NULL DEFAULT 'v1', provenance_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(city, source, utc_timestamp)
            )
```
### `observation_instants`

```sql
CREATE TABLE observation_instants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            timezone_name TEXT NOT NULL,
            local_hour REAL,
            local_timestamp TEXT NOT NULL,
            utc_timestamp TEXT NOT NULL,
            utc_offset_minutes INTEGER NOT NULL,
            dst_active INTEGER NOT NULL DEFAULT 0,
            is_ambiguous_local_hour INTEGER NOT NULL DEFAULT 0,
            is_missing_local_hour INTEGER NOT NULL DEFAULT 0,
            time_basis TEXT NOT NULL,
            temp_current REAL,
            running_max REAL,
            delta_rate_per_h REAL,
            temp_unit TEXT NOT NULL,
            station_id TEXT,
            observation_count INTEGER,
            raw_response TEXT,
            source_file TEXT,
            imported_at TEXT NOT NULL,
            UNIQUE(city, source, utc_timestamp)
        )
```
### `hourly_observations`

```sql
CREATE TABLE hourly_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            obs_date TEXT NOT NULL,
            obs_hour INTEGER NOT NULL,
            temp REAL NOT NULL,
            temp_unit TEXT NOT NULL,
            source TEXT NOT NULL,
            UNIQUE(city, obs_date, obs_hour, source)
        )
```
### `forecasts`

```sql
CREATE TABLE forecasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            forecast_basis_date TEXT,
            forecast_issue_time TEXT,
            lead_days INTEGER,
            lead_time_hours REAL,
            forecast_high REAL,
            forecast_low REAL,
            temp_unit TEXT DEFAULT 'F',
            retrieved_at TEXT,
            imported_at TEXT,
            UNIQUE(city, target_date, source, forecast_basis_date)
        )
```
### `historical_forecasts_v2`

```sql
CREATE TABLE historical_forecasts_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                source TEXT NOT NULL,
                temperature_metric TEXT NOT NULL
                    CHECK (temperature_metric IN ('high', 'low')),
                forecast_value REAL NOT NULL,
                temp_unit TEXT NOT NULL,
                lead_days INTEGER,
                available_at TEXT,
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, authority TEXT NOT NULL DEFAULT 'UNVERIFIED', data_version TEXT NOT NULL DEFAULT 'v1', provenance_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(city, target_date, source, temperature_metric, lead_days)
            )
```
### `ensemble_snapshots_v2`

```sql
CREATE TABLE ensemble_snapshots_v2 (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL
                    CHECK (temperature_metric IN ('high', 'low')),
                physical_quantity TEXT NOT NULL,
                observation_field TEXT NOT NULL
                    CHECK (observation_field IN ('high_temp', 'low_temp')),
                issue_time TEXT,
                valid_time TEXT,
                available_at TEXT NOT NULL,
                fetch_time TEXT NOT NULL,
                lead_hours REAL NOT NULL,
                members_json TEXT NOT NULL,
                p_raw_json TEXT,
                spread REAL,
                is_bimodal INTEGER,
                model_version TEXT NOT NULL,
                data_version TEXT NOT NULL,
                training_allowed INTEGER NOT NULL DEFAULT 1
                    CHECK (training_allowed IN (0, 1)),
                causality_status TEXT NOT NULL DEFAULT 'OK'
                    CHECK (causality_status IN (
                        'OK',
                        'N/A_CAUSAL_DAY_ALREADY_STARTED',
                        'N/A_REQUIRED_STEP_BEYOND_DOWNLOADED_HORIZON',
                        'REJECTED_BOUNDARY_AMBIGUOUS',
                        'RUNTIME_ONLY_FALLBACK',
                        'UNKNOWN'
                    )),
                boundary_ambiguous INTEGER NOT NULL DEFAULT 0
                    CHECK (boundary_ambiguous IN (0, 1)),
                ambiguous_member_count INTEGER NOT NULL DEFAULT 0,
                manifest_hash TEXT,
                provenance_json TEXT NOT NULL DEFAULT '{}',
                authority TEXT NOT NULL DEFAULT 'VERIFIED'
                    CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, members_unit TEXT NOT NULL DEFAULT 'degC', members_precision REAL, local_day_start_utc TEXT, step_horizon_hours REAL, unit TEXT,
                UNIQUE(city, target_date, temperature_metric, issue_time, data_version)
            )
```
### `calibration_pairs_v2`

```sql
CREATE TABLE calibration_pairs_v2 (
                pair_id INTEGER PRIMARY KEY AUTOINCREMENT,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL
                    CHECK (temperature_metric IN ('high', 'low')),
                observation_field TEXT NOT NULL
                    CHECK (observation_field IN ('high_temp', 'low_temp')),
                range_label TEXT NOT NULL,
                p_raw REAL NOT NULL,
                outcome INTEGER NOT NULL,
                lead_days REAL NOT NULL,
                season TEXT NOT NULL,
                cluster TEXT NOT NULL,
                forecast_available_at TEXT NOT NULL,
                settlement_value REAL,
                decision_group_id TEXT,
                bias_corrected INTEGER NOT NULL DEFAULT 0
                    CHECK (bias_corrected IN (0, 1)),
                authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
                    CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
                bin_source TEXT NOT NULL DEFAULT 'legacy',
                snapshot_id INTEGER REFERENCES ensemble_snapshots_v2(snapshot_id),
                data_version TEXT NOT NULL,
                training_allowed INTEGER NOT NULL DEFAULT 1
                    CHECK (training_allowed IN (0, 1)),
                causality_status TEXT NOT NULL DEFAULT 'OK',
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(city, target_date, temperature_metric, range_label, lead_days,
                       forecast_available_at, bin_source, data_version)
            )
```
### `data_coverage`

```sql
CREATE TABLE data_coverage (
            data_table  TEXT NOT NULL
                CHECK (data_table IN ('observations','observation_instants','solar_daily','forecasts')),
            city        TEXT NOT NULL,
            data_source TEXT NOT NULL,
            target_date TEXT NOT NULL,
            sub_key     TEXT NOT NULL DEFAULT '',
            status      TEXT NOT NULL
                CHECK (status IN ('WRITTEN','LEGITIMATE_GAP','FAILED','MISSING')),
            reason      TEXT,
            fetched_at  TEXT NOT NULL,
            expected_at TEXT,
            retry_after TEXT,
            PRIMARY KEY (data_table, city, data_source, target_date, sub_key)
        )
```


## Index and uniqueness findings

- `observations` uses `UNIQUE(city, target_date, source)`. This is inadequate if source rows can differ by metric, station, version, or authority. The table stores both high and low in one row, which is acceptable for daily station observation evidence but not for market-level settlement or high/low training eligibility.
- `settlements` uses `UNIQUE(city, target_date)`. This is structurally incompatible with high/low dual markets, multiple markets per city/date, or station/source changes. `settlements_v2` has the correct `(city,target_date,temperature_metric)` shape but is empty.
- `observation_instants_v2` uses `UNIQUE(city, source, utc_timestamp)`. This is reasonable for source-specific hourly evidence but omits station/version from uniqueness; if a source tag maps to a new station or parser, the table can replace semantically different rows unless provenance/version are checked before write.
- `hourly_observations` uses `UNIQUE(city, obs_date, obs_hour, source)` but lacks timezone and UTC timestamp, making DST fall-back duplicate hours impossible to represent safely.
- `ensemble_snapshots_v2` has a strong uniqueness key `(city,target_date,temperature_metric,issue_time,data_version)` and contains training/causality fields, but no rows exist.
- `calibration_pairs_v2` has training/causality fields and a snapshot reference, but no rows exist.

## Foreign-key-like relationships

SQLite foreign-key enforcement is sparse. `calibration_pairs_v2.snapshot_id` references `ensemble_snapshots_v2(snapshot_id)`, but both are empty. Important practical relationships are not enforced:

- settlements ↔ market_events/market_events_v2 by market identity: not available because settlement rows have null `market_slug` and market tables are empty.
- settlements ↔ observations by station/source/date: only heuristic station parsing works; direct source equality does not.
- calibration pairs ↔ forecasts/ensemble snapshots ↔ observations/settlements: empty in uploaded DB.
- hourly legacy ↔ observation_instants_v2: ETL-derived, but no row-level lineage retained in `hourly_observations`.

## Row-level forensic summary

- `observations`: 42,749 rows; no null high/low/unit/station/timezone/window/data-version/authority fields, but 39,431 empty provenance rows and 6 nonpositive collection windows.
- `settlements`: 1,561 rows; 1,561 null market slugs; 92 null winning bins; 49 null settlement values; high-only metric coverage.
- `observation_instants_v2`: 1,813,662 rows; timezone/local/UTC/unit/authority/data-version/provenance populated; no no-temp rows; but no metric/training/causality columns.
- `data_coverage`: 346,417 rows; useful evidence of missingness, including forecast missingness.
- forecast/model/replay/trading fact tables: zero rows across the critical causal spine.

## v1/v2 seams

The DB clearly has v2 intent but not v2 completion:

- `settlements` v1 populated; `settlements_v2` empty.
- `observation_instants_v2` populated; legacy `hourly_observations` still heavily populated and likely read by compatibility paths.
- `ensemble_snapshots_v2`, `historical_forecasts_v2`, and `calibration_pairs_v2` exist but are empty.
- Docs/code describe high/low metric laws; daily `observations` lacks metric identity fields because high/low are stored as separate columns rather than rows.

## Rows unsafe for live/runtime decisions

- Any row in `hourly_observations` because it lacks timezone/provenance/authority/station.
- Any WU daily `observations` row with empty `provenance_metadata` unless independently reconciled.
- Any fallback/source-mixed row from `observation_instants_current` unless the consumer checks source role and eligibility.
- Any settlement row with null market identity, null value, or null winning bin when replaying a specific market.

## Rows unsafe for training/calibration

- All forecast/ensemble/calibration rows by absence: they cannot support training.
- `observations` rows without provenance/training/causality flags.
- `observation_instants_v2` rows until source-role and metric eligibility are added.
- Settlement rows as generic labels because v1 schema cannot represent market/metric multiplicity.