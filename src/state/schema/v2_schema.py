"""Zeus World DB v2 schema migration.

Single public function: apply_v2_schema(conn).

Contract:
- Idempotent (CREATE TABLE IF NOT EXISTS, DROP TABLE IF EXISTS).
- Runs inside one explicit BEGIN / COMMIT transaction.
- Saves and restores the PRAGMA foreign_keys state so the caller's connection
  is not left with foreign-key enforcement disabled.
- DROPs 3 dead tables (0 rows, no writers):
    promotion_registry, model_eval_point, model_eval_run
  NOTE: model_skill is NOT dropped here — scripts/etl_historical_forecasts.py
  writes to it actively. model_skill cleanup is deferred to a later phase.
- Creates 8 v2 tables per the DDL sketch + architect refinements from
  docs/operations/task_2026-04-16_dual_track_metric_spine/phase2_evidence/opener_digest.md

# Created: 2026-05-10
# Last reused or audited: 2026-05-10
# Authority basis: task #200 (Fix SQLite live-vs-ingest contention design failure)
"""
from __future__ import annotations

import os
import sqlite3


def apply_v2_schema(conn: sqlite3.Connection) -> None:
    """Apply the Zeus World DB v2 schema to *conn*.

    Safe to call on both zeus-world.db and zeus_trades.db.
    Safe to call multiple times — all DDL uses IF NOT EXISTS / IF EXISTS.

    # Fix (task #200, 2026-05-10): Re-apply PRAGMA busy_timeout at the start of
    # this function. Python's sqlite3.executescript() resets the C-level busy
    # handler that sqlite3.connect(timeout=N) installs, so any subsequent
    # conn.execute() on the same connection has no wait budget and fails
    # immediately on lock contention. Restoring busy_timeout here makes
    # apply_v2_schema robust regardless of what ran on *conn* before it.
    # ZEUS_DB_BUSY_TIMEOUT_MS default matches db.py _db_busy_timeout_s() (30 s).
    """
    _busy_timeout_ms = int(os.environ.get("ZEUS_DB_BUSY_TIMEOUT_MS", "30000"))
    conn.execute(f"PRAGMA busy_timeout = {_busy_timeout_ms}")

    # Save foreign_keys state before touching anything
    (fk_before,) = conn.execute("PRAGMA foreign_keys").fetchone()

    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN")

        # ----------------------------------------------------------------
        # Drop 3 dead tables (D2 — 0 rows, no writers)
        # model_skill is intentionally excluded: etl_historical_forecasts.py
        # writes to it actively. Cleanup deferred to a later phase.
        # ----------------------------------------------------------------
        conn.execute("DROP TABLE IF EXISTS promotion_registry")
        conn.execute("DROP TABLE IF EXISTS model_eval_point")
        conn.execute("DROP TABLE IF EXISTS model_eval_run")

        # ----------------------------------------------------------------
        # settlements_v2
        # ----------------------------------------------------------------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settlements_v2 (
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
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_settlements_v2_city_date_metric
                ON settlements_v2(city, target_date, temperature_metric)
        """)
        # Architect refinement: index on settled_at for harvest scans
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_settlements_v2_settled_at
                ON settlements_v2(settled_at)
        """)

        # ----------------------------------------------------------------
        # market_events_v2
        # ----------------------------------------------------------------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_events_v2 (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_slug TEXT NOT NULL,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL
                    CHECK (temperature_metric IN ('high', 'low')),
                condition_id TEXT,
                token_id TEXT,
                range_label TEXT,
                range_low REAL,
                range_high REAL,
                outcome TEXT,
                created_at TEXT,
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(market_slug, condition_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_market_events_v2_city_date_metric
                ON market_events_v2(city, target_date, temperature_metric)
        """)
        # Architect refinement: partial index on open markets
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_market_events_v2_open
                ON market_events_v2(city, target_date, temperature_metric)
                WHERE outcome IS NULL
        """)

        # ----------------------------------------------------------------
        # market_price_history
        # ----------------------------------------------------------------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_slug TEXT NOT NULL,
                token_id TEXT NOT NULL,
                price REAL NOT NULL CHECK (price >= 0.0 AND price <= 1.0),
                recorded_at TEXT NOT NULL,
                hours_since_open REAL,
                hours_to_resolution REAL,
                market_price_linkage TEXT NOT NULL DEFAULT 'price_only'
                    CHECK (market_price_linkage IN ('price_only', 'full')),
                source TEXT NOT NULL DEFAULT 'GAMMA_SCANNER',
                best_bid REAL CHECK (best_bid IS NULL OR (best_bid >= 0.0 AND best_bid <= 1.0)),
                best_ask REAL CHECK (best_ask IS NULL OR (best_ask >= 0.0 AND best_ask <= 1.0)),
                raw_orderbook_hash TEXT,
                snapshot_id TEXT,
                condition_id TEXT,
                UNIQUE(token_id, recorded_at)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_market_price_history_slug_recorded
                ON market_price_history(market_slug, recorded_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_market_price_history_token_recorded
                ON market_price_history(token_id, recorded_at)
        """)
        for alter_sql in [
            "ALTER TABLE market_price_history ADD COLUMN market_price_linkage TEXT NOT NULL DEFAULT 'price_only' CHECK (market_price_linkage IN ('price_only', 'full'))",
            "ALTER TABLE market_price_history ADD COLUMN source TEXT NOT NULL DEFAULT 'GAMMA_SCANNER'",
            "ALTER TABLE market_price_history ADD COLUMN best_bid REAL CHECK (best_bid IS NULL OR (best_bid >= 0.0 AND best_bid <= 1.0))",
            "ALTER TABLE market_price_history ADD COLUMN best_ask REAL CHECK (best_ask IS NULL OR (best_ask >= 0.0 AND best_ask <= 1.0))",
            "ALTER TABLE market_price_history ADD COLUMN raw_orderbook_hash TEXT",
            "ALTER TABLE market_price_history ADD COLUMN snapshot_id TEXT",
            "ALTER TABLE market_price_history ADD COLUMN condition_id TEXT",
        ]:
            try:
                conn.execute(alter_sql)
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_market_price_history_snapshot
                ON market_price_history(snapshot_id, recorded_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_market_price_history_condition_recorded
                ON market_price_history(condition_id, recorded_at)
        """)

        # ----------------------------------------------------------------
        # ensemble_snapshots_v2
        # ----------------------------------------------------------------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ensemble_snapshots_v2 (
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
                source_id TEXT,
                source_transport TEXT,
                source_run_id TEXT,
                release_calendar_key TEXT,
                source_cycle_time TEXT,
                source_release_time TEXT,
                source_available_at TEXT,
                city_timezone TEXT,
                settlement_source_type TEXT,
                settlement_station_id TEXT,
                settlement_unit TEXT
                    CHECK (settlement_unit IS NULL OR settlement_unit IN ('F', 'C')),
                settlement_rounding_policy TEXT,
                bin_grid_id TEXT,
                bin_schema_version TEXT,
                forecast_window_start_utc TEXT,
                forecast_window_end_utc TEXT,
                forecast_window_start_local TEXT,
                forecast_window_end_local TEXT,
                forecast_window_local_day_overlap_hours REAL,
                forecast_window_attribution_status TEXT,
                contributes_to_target_extrema INTEGER
                    CHECK (contributes_to_target_extrema IS NULL OR contributes_to_target_extrema IN (0, 1)),
                forecast_window_block_reasons_json TEXT,
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
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(city, target_date, temperature_metric, issue_time, data_version)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ensemble_snapshots_v2_lookup
                ON ensemble_snapshots_v2(city, target_date, temperature_metric, available_at)
        """)
        # 4A.2: members_unit / members_precision — idempotent ADD COLUMN
        for alter_sql in [
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN members_unit TEXT NOT NULL DEFAULT 'degC'",
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN members_precision REAL",
            # 4.5: R-L provenance fields for local-calendar-day extractor
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN local_day_start_utc TEXT",
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN step_horizon_hours REAL",
            # Phase 7A: unit column for metric-aware backfill. Formerly-accompanying
            # contract_version + boundary_min_value columns dropped in P7B (no live
            # consumer; P8 will re-add if needed when shadow-activation consumers land).
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN unit TEXT",
            # PLAN_v4 executable forecast-entry linkage. NULL means legacy/shadow-only.
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN source_id TEXT",
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN source_transport TEXT",
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN source_run_id TEXT",
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN release_calendar_key TEXT",
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN source_cycle_time TEXT",
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN source_release_time TEXT",
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN source_available_at TEXT",
            # 2026-05-07 LOW/HIGH alignment recovery: nullable shadow columns for
            # contract-object and explicit forecast-window evidence. These columns
            # only make evidence persistable; they do not relax training_allowed or
            # change live decision authority.
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN city_timezone TEXT",
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN settlement_source_type TEXT",
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN settlement_station_id TEXT",
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN settlement_unit TEXT CHECK (settlement_unit IS NULL OR settlement_unit IN ('F', 'C'))",
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN settlement_rounding_policy TEXT",
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN bin_grid_id TEXT",
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN bin_schema_version TEXT",
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN forecast_window_start_utc TEXT",
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN forecast_window_end_utc TEXT",
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN forecast_window_start_local TEXT",
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN forecast_window_end_local TEXT",
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN forecast_window_local_day_overlap_hours REAL",
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN forecast_window_attribution_status TEXT",
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN contributes_to_target_extrema INTEGER CHECK (contributes_to_target_extrema IS NULL OR contributes_to_target_extrema IN (0, 1))",
            "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN forecast_window_block_reasons_json TEXT",
        ]:
            try:
                conn.execute(alter_sql)
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ens_v2_source_run
                ON ensemble_snapshots_v2(source_id, source_transport, source_run_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ens_v2_entry_lookup
                ON ensemble_snapshots_v2(
                    city,
                    target_date,
                    temperature_metric,
                    source_id,
                    source_transport,
                    data_version,
                    source_run_id
                )
        """)

        # ----------------------------------------------------------------
        # calibration_pairs_v2
        # Architect refinement: add UNIQUE on the full dedup key
        # ----------------------------------------------------------------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS calibration_pairs_v2 (
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
                cycle TEXT NOT NULL DEFAULT '00',
                source_id TEXT NOT NULL DEFAULT 'tigge_mars',
                horizon_profile TEXT NOT NULL DEFAULT 'full',
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(city, target_date, temperature_metric, range_label, lead_days,
                       forecast_available_at, bin_source, data_version)
            )
        """)
        # Phase 2 (2026-05-04): cycle/source_id/horizon_profile stratification —
        # idempotent ALTER so legacy DBs migrated via
        # scripts/migrate_phase2_cycle_stratification.py converge with fresh DBs
        # built from this canonical schema. Defaults match the migration script.
        for alter_sql in [
            "ALTER TABLE calibration_pairs_v2 ADD COLUMN cycle TEXT NOT NULL DEFAULT '00'",
            "ALTER TABLE calibration_pairs_v2 ADD COLUMN source_id TEXT NOT NULL DEFAULT 'tigge_mars'",
            "ALTER TABLE calibration_pairs_v2 ADD COLUMN horizon_profile TEXT NOT NULL DEFAULT 'full'",
        ]:
            try:
                conn.execute(alter_sql)
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_calibration_pairs_v2_bucket
                ON calibration_pairs_v2(temperature_metric, cluster, season, lead_days)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_calibration_pairs_v2_city_date_metric
                ON calibration_pairs_v2(city, target_date, temperature_metric)
        """)

        # ----------------------------------------------------------------
        # platt_models_v2
        # ----------------------------------------------------------------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS platt_models_v2 (
                model_key TEXT PRIMARY KEY,
                temperature_metric TEXT NOT NULL
                    CHECK (temperature_metric IN ('high', 'low')),
                cluster TEXT NOT NULL,
                season TEXT NOT NULL,
                data_version TEXT NOT NULL,
                input_space TEXT NOT NULL DEFAULT 'raw_probability',
                param_A REAL NOT NULL,
                param_B REAL NOT NULL,
                param_C REAL NOT NULL DEFAULT 0.0,
                bootstrap_params_json TEXT NOT NULL,
                n_samples INTEGER NOT NULL,
                brier_insample REAL,
                fitted_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
                    CHECK (is_active IN (0, 1)),
                authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
                    CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
                bucket_key TEXT,
                cycle TEXT NOT NULL DEFAULT '00',
                source_id TEXT NOT NULL DEFAULT 'tigge_mars',
                horizon_profile TEXT NOT NULL DEFAULT 'full',
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                -- 2026-05-05 critic-opus Blocker 1: UNIQUE extended with
                -- stratification keys so cross-cycle Platt rows do not collide
                -- on insert. Legacy DBs must be rebuilt via
                -- scripts/migrate_phase2_cycle_stratification.py to converge.
                UNIQUE(temperature_metric, cluster, season, data_version,
                       input_space, is_active, cycle, source_id, horizon_profile)
            )
        """)
        # Phase 2 (2026-05-04): cycle/source_id/horizon_profile stratification —
        # idempotent ALTER for legacy DBs. Mirror of the calibration_pairs_v2
        # block above; defaults match scripts/migrate_phase2_cycle_stratification.py.
        for alter_sql in [
            "ALTER TABLE platt_models_v2 ADD COLUMN cycle TEXT NOT NULL DEFAULT '00'",
            "ALTER TABLE platt_models_v2 ADD COLUMN source_id TEXT NOT NULL DEFAULT 'tigge_mars'",
            "ALTER TABLE platt_models_v2 ADD COLUMN horizon_profile TEXT NOT NULL DEFAULT 'full'",
        ]:
            try:
                conn.execute(alter_sql)
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_platt_models_v2_lookup
                ON platt_models_v2(temperature_metric, cluster, season, data_version, input_space, is_active)
        """)

        # ----------------------------------------------------------------
        # validated_calibration_transfers
        # Phase X.1 (2026-05-05): OOS evidence scaffold for calibration-
        # transfer gate. Rows written by Phase X.2 OOS evaluator; used by
        # evaluate_calibration_transfer_policy_with_evidence when feature flag
        # ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED=true (default: false).
        # ----------------------------------------------------------------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS validated_calibration_transfers (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                policy_id             TEXT NOT NULL,
                source_id             TEXT NOT NULL,
                target_source_id      TEXT NOT NULL,
                source_cycle          TEXT NOT NULL,
                target_cycle          TEXT NOT NULL,
                horizon_profile       TEXT NOT NULL,
                season                TEXT NOT NULL,
                cluster               TEXT NOT NULL,
                metric                TEXT NOT NULL CHECK (metric IN ('high', 'low')),
                n_pairs               INTEGER NOT NULL,
                brier_source          REAL NOT NULL,
                brier_target          REAL NOT NULL,
                brier_diff            REAL NOT NULL,
                brier_diff_threshold  REAL NOT NULL,
                status                TEXT NOT NULL
                    CHECK (status IN ('LIVE_ELIGIBLE', 'TRANSFER_UNSAFE',
                                      'INSUFFICIENT_SAMPLE', 'same_domain_no_transfer')),
                evidence_window_start TEXT NOT NULL,
                evidence_window_end   TEXT NOT NULL,
                platt_model_key       TEXT NOT NULL,
                evaluated_at          TEXT NOT NULL,
                UNIQUE (policy_id, target_source_id, target_cycle, season, cluster, metric,
                        horizon_profile, platt_model_key)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_validated_transfers_route
                ON validated_calibration_transfers(target_source_id, target_cycle, season, cluster, metric)
        """)

        # ----------------------------------------------------------------
        # observation_instants_v2
        # Architect refinement: running_min column for low-track obs support
        # ----------------------------------------------------------------
        # B4 (2026-04-26): physical-bounds CHECK on temp columns. Applies to
        # NEW DBs only — SQLite ALTER cannot add CHECK retroactively (db.py
        # comment at L330-333 same pattern). Writer-level validation in
        # observation_instants_v2_writer._validate() is the load-bearing
        # antibody for legacy DBs. Bounds: -90/60 °C inclusive, -130/140 °F
        # inclusive. NULL passes through (fields are nullable per schema).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS observation_instants_v2 (
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
                imported_at TEXT NOT NULL,
                authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
                    CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED', 'ICAO_STATION_NATIVE')),
                data_version TEXT NOT NULL DEFAULT 'v1',
                provenance_json TEXT NOT NULL DEFAULT '{}',
                CHECK (
                    (temp_unit = 'C' AND
                        (temp_current IS NULL OR temp_current BETWEEN -90 AND 60) AND
                        (running_max  IS NULL OR running_max  BETWEEN -90 AND 60) AND
                        (running_min  IS NULL OR running_min  BETWEEN -90 AND 60))
                    OR
                    (temp_unit = 'F' AND
                        (temp_current IS NULL OR temp_current BETWEEN -130 AND 140) AND
                        (running_max  IS NULL OR running_max  BETWEEN -130 AND 140) AND
                        (running_min  IS NULL OR running_min  BETWEEN -130 AND 140))
                ),
                UNIQUE(city, source, utc_timestamp)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_observation_instants_v2_city_ts
                ON observation_instants_v2(city, target_date, utc_timestamp)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS observation_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name TEXT NOT NULL
                    CHECK (table_name IN ('observation_instants_v2', 'observations')),
                city TEXT NOT NULL,
                target_date TEXT,
                source TEXT NOT NULL,
                utc_timestamp TEXT,
                natural_key_json TEXT NOT NULL DEFAULT '{}',
                existing_row_id INTEGER,
                existing_payload_hash TEXT,
                incoming_payload_hash TEXT NOT NULL,
                reason TEXT NOT NULL,
                writer TEXT NOT NULL,
                existing_row_json TEXT NOT NULL,
                incoming_row_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_observation_revisions_obs_v2_lookup
                ON observation_revisions(table_name, city, source, utc_timestamp, recorded_at)
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_observation_revisions_payload
                ON observation_revisions(
                    table_name, city, source, target_date, utc_timestamp,
                    incoming_payload_hash, reason
                )
        """)
        # Gate F Step 2 / Phase 0: authority + data_version + provenance_json
        # columns for existing DBs (idempotent).
        #
        # ICAO_STATION_NATIVE authority value is for HK hko_hourly_accumulator
        # rows per plan v3 L95 and reader filter in antibody A4. The CHECK
        # constraint is only applied to NEW DBs (SQLite ALTER cannot add CHECK);
        # live tables rely on writer-level A6 enforcement.
        # Pairs with Gap A closure in step1_schema_audit.md.
        #
        # A4/C7 (2026-04-24, data-readiness-tail forensic closure): extend
        # observation_instants_v2 with INV-14 identity spine (temperature_metric
        # + physical_quantity + observation_field) + training_allowed +
        # causality_status + source_role. Previously only authority +
        # data_version + provenance_json were present. Per critic-opus P0.2
        # finding C7: without these fields, Day0 features can train on
        # fallback-mixed rows (e.g., `wu_icao` canonical + `openmeteo` fallback
        # share data_version='v1'). Adding the columns unblocks the per-row
        # identity check at the training-input boundary. All columns nullable
        # on ALTER path (SQLite limitation); writer-side enforcement catches
        # future INSERTs; existing 1.8M rows remain NULL until backfill.
        for alter_sql in [
            "ALTER TABLE observation_instants_v2 ADD COLUMN authority TEXT NOT NULL DEFAULT 'UNVERIFIED'",
            "ALTER TABLE observation_instants_v2 ADD COLUMN data_version TEXT NOT NULL DEFAULT 'v1'",
            "ALTER TABLE observation_instants_v2 ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}'",
            "ALTER TABLE observation_instants_v2 ADD COLUMN temperature_metric TEXT",
            "ALTER TABLE observation_instants_v2 ADD COLUMN physical_quantity TEXT",
            "ALTER TABLE observation_instants_v2 ADD COLUMN observation_field TEXT",
            "ALTER TABLE observation_instants_v2 ADD COLUMN training_allowed INTEGER DEFAULT 1",
            "ALTER TABLE observation_instants_v2 ADD COLUMN causality_status TEXT DEFAULT 'OK'",
            "ALTER TABLE observation_instants_v2 ADD COLUMN source_role TEXT",
        ]:
            try:
                conn.execute(alter_sql)
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise

        # ----------------------------------------------------------------
        # zeus_meta — runtime-switch registry for atomic data-version cutover
        # ----------------------------------------------------------------
        # Phase 0 creates the table + observation_data_version='v0' so the
        # observation_instants_current VIEW returns 0 rows until Phase 2
        # fleet-atomic flip sets value='v1.wu-native'.
        #
        # Rationale: downstream readers (diurnal_curves, temp_persistence,
        # monitor_refresh) modify to SELECT FROM
        # observation_instants_current in Phase 1. Pre-Phase-2 the view is
        # empty, so readers fall back to legacy observation_instants. Phase 2
        # is a single UPDATE zeus_meta SET value='v1.wu-native' — atomic
        # cutover without per-reader coordination.
        # ----------------------------------------------------------------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS zeus_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO zeus_meta (key, value)
            VALUES ('observation_data_version', 'v0')
        """)

        # ----------------------------------------------------------------
        # observation_instants_current VIEW — atomic cutover indirection
        # ----------------------------------------------------------------
        # Returns only rows whose data_version matches zeus_meta. Pre-Phase-2
        # zeus_meta.observation_data_version='v0', and no rows carry that
        # data_version (pilot uses 'v1.wu-native.pilot', fleet uses
        # 'v1.wu-native'). Phase 2 flips the meta value, instantly activating
        # whichever corpus is desired.
        #
        # Must be created AFTER the ADD COLUMN block so `o.*` includes
        # provenance_json.
        # ----------------------------------------------------------------
        conn.execute("DROP VIEW IF EXISTS observation_instants_current")
        conn.execute("""
            CREATE VIEW observation_instants_current AS
                SELECT o.*
                FROM observation_instants_v2 o
                JOIN zeus_meta m
                  ON m.key = 'observation_data_version'
                 AND o.data_version = m.value
        """)

        # ----------------------------------------------------------------
        # historical_forecasts_v2
        # ----------------------------------------------------------------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS historical_forecasts_v2 (
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
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
                    CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
                data_version TEXT NOT NULL DEFAULT 'v1',
                provenance_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(city, target_date, source, temperature_metric, lead_days)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_historical_forecasts_v2_lookup
                ON historical_forecasts_v2(city, target_date, source, temperature_metric, lead_days)
        """)
        # Gate F Step 2: authority + data_version + provenance_json for existing DBs
        # (idempotent). Pairs with Gap B closure in step1_schema_audit.md.
        for alter_sql in [
            "ALTER TABLE historical_forecasts_v2 ADD COLUMN authority TEXT NOT NULL DEFAULT 'UNVERIFIED'",
            "ALTER TABLE historical_forecasts_v2 ADD COLUMN data_version TEXT NOT NULL DEFAULT 'v1'",
            "ALTER TABLE historical_forecasts_v2 ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}'",
        ]:
            try:
                conn.execute(alter_sql)
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise

        # ----------------------------------------------------------------
        # day0_metric_fact
        # Architect refinement: add UNIQUE on the natural key
        # ----------------------------------------------------------------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS day0_metric_fact (
                fact_id TEXT PRIMARY KEY,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL
                    CHECK (temperature_metric IN ('high', 'low')),
                source TEXT NOT NULL,
                local_timestamp TEXT NOT NULL,
                utc_timestamp TEXT NOT NULL,
                local_hour REAL,
                temp_current REAL,
                running_extreme REAL,
                delta_rate_per_h REAL,
                daylight_progress REAL,
                obs_age_minutes REAL,
                extreme_confidence REAL,
                ens_q50_remaining_extreme REAL,
                ens_q90_remaining_extreme REAL,
                ens_spread REAL,
                settlement_value REAL,
                residual_to_settlement REAL,
                fact_status TEXT NOT NULL
                    CHECK (fact_status IN ('complete', 'missing_inputs')),
                missing_reason_json TEXT NOT NULL DEFAULT '[]',
                recorded_at TEXT NOT NULL,
                UNIQUE(city, target_date, temperature_metric, utc_timestamp, source)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_day0_metric_fact_city_ts
                ON day0_metric_fact(city, target_date, temperature_metric, utc_timestamp)
        """)

        # ----------------------------------------------------------------
        # rescue_events_v2 — B063: durable audit row for chain-rescue events.
        #
        # `chain_reconciliation._emit_rescue_event` already logs an INFO line
        # and inserts a `CHAIN_RESCUE_AUDIT` row into position_events, but
        # that row has no temperature_metric, no causality_status, and no
        # provenance authority — so post-mortem cannot distinguish:
        #   (a) a legitimate N/A_CAUSAL_DAY_ALREADY_STARTED low-lane skip,
        #   (b) a rescue that silently failed to record, or
        #   (c) a quarantine placeholder whose track identity was never set.
        #
        # Per SD-1 (MetricIdentity is binary) and SD-H (provenance authority
        # tagging), temperature_metric stays {'high','low'} and `authority`
        # carries the tri-state confidence. Consumer branches that already
        # assume binary high/low (evaluator.py, day0_signal.py, etc.) remain
        # correct — an UNVERIFIED rescue row carries a concrete high/low
        # tag plus an explicit authority_source explaining how it was
        # inferred.
        #
        # Exempt from the DT#1 commit_then_export choke point — this is an
        # authoritative audit record, not a derived export, and must be
        # durable across crash recovery (same rule as CHAIN_RESCUE_AUDIT
        # in position_events per chain_reconciliation.py:276-282).
        # ----------------------------------------------------------------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rescue_events_v2 (
                rescue_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT NOT NULL,
                position_id TEXT,
                decision_snapshot_id TEXT,
                temperature_metric TEXT NOT NULL
                    CHECK (temperature_metric IN ('high', 'low')),
                causality_status TEXT NOT NULL DEFAULT 'OK'
                    CHECK (causality_status IN (
                        'OK',
                        'N/A_CAUSAL_DAY_ALREADY_STARTED',
                        'N/A_REQUIRED_STEP_BEYOND_DOWNLOADED_HORIZON',
                        'REJECTED_BOUNDARY_AMBIGUOUS',
                        'UNKNOWN'
                    )),
                authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
                    CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'RECONSTRUCTED')),
                authority_source TEXT,
                chain_state TEXT NOT NULL,
                reason TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(trade_id, occurred_at)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rescue_events_v2_trade_time
                ON rescue_events_v2(trade_id, recorded_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rescue_events_v2_metric_causality
                ON rescue_events_v2(temperature_metric, causality_status, recorded_at)
        """)

        # Fix D (golden-knitting-wand.md Phase 1): per-bucket failure ledger
        # for refit_platt_v2.py. Written when per-bucket SAVEPOINT rolls back
        # so the operator can triage which buckets failed without losing
        # the successful buckets' rows. Separate from refit logic so the table
        # is available before the first refit run.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS refit_bucket_failures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cluster TEXT NOT NULL,
                season TEXT NOT NULL,
                cycle TEXT,
                source_id TEXT,
                error_class TEXT NOT NULL,
                error_text TEXT NOT NULL,
                ts TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_refit_bucket_failures_ts
                ON refit_bucket_failures(ts)
        """)

        conn.execute("COMMIT")

    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        # Restore foreign_keys to whatever it was before we touched it
        conn.execute(f"PRAGMA foreign_keys = {fk_before}")
