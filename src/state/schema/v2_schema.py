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
"""
from __future__ import annotations

import sqlite3


def apply_v2_schema(conn: sqlite3.Connection) -> None:
    """Apply the Zeus World DB v2 schema to *conn*.

    Safe to call on both zeus-world.db and zeus_trades.db.
    Safe to call multiple times — all DDL uses IF NOT EXISTS / IF EXISTS.
    """
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
        ]:
            try:
                conn.execute(alter_sql)
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise

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
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(city, target_date, temperature_metric, range_label, lead_days,
                       forecast_available_at, bin_source, data_version)
            )
        """)
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
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(temperature_metric, cluster, season, data_version, input_space, is_active)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_platt_models_v2_lookup
                ON platt_models_v2(temperature_metric, cluster, season, data_version, input_space, is_active)
        """)

        # ----------------------------------------------------------------
        # observation_instants_v2
        # Architect refinement: running_min column for low-track obs support
        # ----------------------------------------------------------------
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
                    CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
                data_version TEXT NOT NULL DEFAULT 'v1',
                UNIQUE(city, source, utc_timestamp)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_observation_instants_v2_city_ts
                ON observation_instants_v2(city, target_date, utc_timestamp)
        """)
        # Gate F Step 2: authority + data_version columns for existing DBs (idempotent).
        # Pairs with Gap A closure in step1_schema_audit.md.
        for alter_sql in [
            "ALTER TABLE observation_instants_v2 ADD COLUMN authority TEXT NOT NULL DEFAULT 'UNVERIFIED'",
            "ALTER TABLE observation_instants_v2 ADD COLUMN data_version TEXT NOT NULL DEFAULT 'v1'",
        ]:
            try:
                conn.execute(alter_sql)
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise

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
