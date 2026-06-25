"""Zeus World DB v2 schema migration.

Single public function: apply_canonical_schema(conn).

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


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1] if not isinstance(row, sqlite3.Row) else row["name"])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _create_settlement_outcomes(conn: sqlite3.Connection) -> None:
    """Create settlement_outcomes table + indexes. Idempotent. K1 forecast-class table.

    B3cont (2026-05-28): collapsed from settlement_outcomes (dead bare settlements shell dropped).

    D-S1 (TRIBUNAL P2, 2026-05-29): first-class settlement identity columns. Pre-D-S1 the
    forecast↔settlement pairing contract parsed the station from the settlement_source URL
    (which FAILS for HKO's climat.htm — no station code in the URL) and took the settlement
    UNIT from the forecast's unverifiable CLAIM (making the pairing gate's unit dimension
    tautological — it could never catch a degC/degF mis-scale). ``settlement_station`` and
    ``settlement_unit`` make both VERIFIED truth. Both are NULLABLE: NULL = not-yet-backfilled,
    and the contract falls back to the URL/claim heuristic on NULL so legacy rows behave
    exactly as before (never fail-closed on a missing column). The unit CHECK mirrors the
    ensemble_snapshots {'F','C'} vocabulary so assert_same_target compares like-for-like.
    Live forecasts DB is migrated by scripts/migrations/202605_add_settlement_outcomes_station_unit.py.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settlement_outcomes (
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
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
            outcome_type INTEGER,
            settlement_station TEXT,
            settlement_unit TEXT
                CHECK (settlement_unit IS NULL OR settlement_unit IN ('F', 'C')),
            UNIQUE(city, target_date, temperature_metric)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_settlement_outcomes_city_date_metric
            ON settlement_outcomes(city, target_date, temperature_metric)
    """)
    # Architect refinement: index on settled_at for harvest scans
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_settlement_outcomes_settled_at
            ON settlement_outcomes(settled_at)
    """)
    # W2 (2026-06-03): VERIFIED rows must carry settlement_unit.
    # Two triggers cover INSERT and UPDATE so the invariant cannot be bypassed
    # via INSERT(UNVERIFIED, NULL) → UPDATE authority='VERIFIED'.
    # Legacy/UNVERIFIED/QUARANTINED rows are not constrained so existing rows
    # are unaffected. All callers that write VERIFIED rows are updated in the
    # same change (ordering-trap mitigation: no caller-update-lag window).
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS _settlement_outcomes_verified_unit_check
        BEFORE INSERT ON settlement_outcomes
        FOR EACH ROW
        WHEN NEW.authority = 'VERIFIED' AND NEW.settlement_unit IS NULL
        BEGIN
            SELECT RAISE(ABORT, 'VERIFIED_SETTLEMENT_REQUIRES_UNIT');
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS _settlement_outcomes_verified_unit_check_update
        BEFORE UPDATE ON settlement_outcomes
        FOR EACH ROW
        WHEN NEW.authority = 'VERIFIED' AND NEW.settlement_unit IS NULL
        BEGIN
            SELECT RAISE(ABORT, 'VERIFIED_SETTLEMENT_REQUIRES_UNIT');
        END
    """)


def _create_market_events(conn: sqlite3.Connection) -> None:
    """Create market_events table + indexes. Idempotent. K1 forecast-class table.

    Collapsed from market_events in B3cont (PR3): dead v1 shell on world.db
    had 0 rows; v2 was the only live table (17,256 rows on zeus-forecasts.db).
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_events (
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
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
            UNIQUE(market_slug, condition_id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_market_events_city_date_metric
            ON market_events(city, target_date, temperature_metric)
    """)
    # Architect refinement: partial index on open markets
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_market_events_open
            ON market_events(city, target_date, temperature_metric)
            WHERE outcome IS NULL
    """)


def _create_ensemble_snapshots(conn: sqlite3.Connection) -> None:
    """Create ensemble_snapshots table + indexes + ALTERs. Idempotent.

    K1 forecast-class table (moves to zeus-forecasts.db). Contains CREATE,
    4 indexes, and 27 idempotent ALTER TABLE statements for additive columns
    added across schema versions. All ALTERs suppress 'duplicate column' errors.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ensemble_snapshots (
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
            dataset_id TEXT NOT NULL,
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
            bin_schema_id TEXT,
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
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
            UNIQUE(city, target_date, temperature_metric, issue_time, dataset_id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ensemble_snapshots_lookup
            ON ensemble_snapshots(city, target_date, temperature_metric, available_at)
    """)
    # 4A.2: members_unit / members_precision — idempotent ADD COLUMN
    for alter_sql in [
        "ALTER TABLE ensemble_snapshots ADD COLUMN members_unit TEXT NOT NULL DEFAULT 'degC'",
        "ALTER TABLE ensemble_snapshots ADD COLUMN members_precision REAL",
        # 4.5: R-L provenance fields for local-calendar-day extractor
        "ALTER TABLE ensemble_snapshots ADD COLUMN local_day_start_utc TEXT",
        "ALTER TABLE ensemble_snapshots ADD COLUMN step_horizon_hours REAL",
        # Phase 7A: unit column for metric-aware backfill. Formerly-accompanying
        # contract_version + boundary_min_value columns dropped in P7B (no live
        # consumer; P8 will re-add if needed when shadow-activation consumers land).
        "ALTER TABLE ensemble_snapshots ADD COLUMN unit TEXT",
        # PLAN_v4 executable forecast-entry linkage. NULL means no live consumer.
        "ALTER TABLE ensemble_snapshots ADD COLUMN source_id TEXT",
        "ALTER TABLE ensemble_snapshots ADD COLUMN source_transport TEXT",
        "ALTER TABLE ensemble_snapshots ADD COLUMN source_run_id TEXT",
        "ALTER TABLE ensemble_snapshots ADD COLUMN release_calendar_key TEXT",
        "ALTER TABLE ensemble_snapshots ADD COLUMN source_cycle_time TEXT",
        "ALTER TABLE ensemble_snapshots ADD COLUMN source_release_time TEXT",
        "ALTER TABLE ensemble_snapshots ADD COLUMN source_available_at TEXT",
        # 2026-05-07 LOW/HIGH alignment recovery: nullable evidence columns for
        # contract-object and explicit forecast-window evidence. These columns
        # only make evidence persistable; they do not relax training_allowed or
        # change live decision authority.
        "ALTER TABLE ensemble_snapshots ADD COLUMN city_timezone TEXT",
        "ALTER TABLE ensemble_snapshots ADD COLUMN settlement_source_type TEXT",
        "ALTER TABLE ensemble_snapshots ADD COLUMN settlement_station_id TEXT",
        "ALTER TABLE ensemble_snapshots ADD COLUMN settlement_unit TEXT CHECK (settlement_unit IS NULL OR settlement_unit IN ('F', 'C'))",
        "ALTER TABLE ensemble_snapshots ADD COLUMN settlement_rounding_policy TEXT",
        "ALTER TABLE ensemble_snapshots ADD COLUMN bin_grid_id TEXT",
        "ALTER TABLE ensemble_snapshots ADD COLUMN bin_schema_id TEXT",
        "ALTER TABLE ensemble_snapshots ADD COLUMN forecast_window_start_utc TEXT",
        "ALTER TABLE ensemble_snapshots ADD COLUMN forecast_window_end_utc TEXT",
        "ALTER TABLE ensemble_snapshots ADD COLUMN forecast_window_start_local TEXT",
        "ALTER TABLE ensemble_snapshots ADD COLUMN forecast_window_end_local TEXT",
        "ALTER TABLE ensemble_snapshots ADD COLUMN forecast_window_local_day_overlap_hours REAL",
        "ALTER TABLE ensemble_snapshots ADD COLUMN forecast_window_attribution_status TEXT",
        "ALTER TABLE ensemble_snapshots ADD COLUMN contributes_to_target_extrema INTEGER CHECK (contributes_to_target_extrema IS NULL OR contributes_to_target_extrema IN (0, 1))",
        "ALTER TABLE ensemble_snapshots ADD COLUMN forecast_window_block_reasons_json TEXT",
        # PR 6 (2026-05-19): alpha-proxy timing chain fields
        "ALTER TABLE ensemble_snapshots ADD COLUMN first_member_observed_time TEXT",
        "ALTER TABLE ensemble_snapshots ADD COLUMN run_complete_time TEXT",
        "ALTER TABLE ensemble_snapshots ADD COLUMN raw_orderbook_hash_transition_delta_ms INTEGER",
    ]:
        try:
            conn.execute(alter_sql)
        except Exception as exc:
            if "duplicate column" not in str(exc).lower():
                raise
    conn.execute("DROP INDEX IF EXISTS idx_ens_v2_source_run")
    conn.execute("DROP INDEX IF EXISTS idx_ens_v2_entry_lookup")
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ens_source_run
            ON ensemble_snapshots(source_id, source_transport, source_run_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ens_entry_lookup
            ON ensemble_snapshots(
                city,
                target_date,
                temperature_metric,
                source_id,
                source_transport,
                dataset_id,
                source_run_id
            )
    """)


def _ensure_forecast_posteriors_runtime_layer_compatibility(conn: sqlite3.Connection) -> None:
    """Ensure forecast_posteriors carries the runtime-layer column."""

    columns = _table_columns(conn, "forecast_posteriors")
    if not columns:
        return
    if "runtime_layer" not in columns:
        conn.execute(
            """
            ALTER TABLE forecast_posteriors
            ADD COLUMN runtime_layer TEXT
                CHECK (runtime_layer IS NULL OR runtime_layer IN ('live'))
            """
        )
        columns.add("runtime_layer")
    if "trade_authority_status" in columns:
        conn.execute(
            """
            UPDATE forecast_posteriors
               SET runtime_layer = 'live'
             WHERE runtime_layer IS NULL
               AND trade_authority_status = 'LIVE_AUTHORITY'
            """
        )
    conn.execute("""
        DELETE FROM forecast_posteriors
         WHERE runtime_layer IS NULL
            OR runtime_layer != 'live'
    """)
    if "trade_authority_status" in columns:
        conn.execute("ALTER TABLE forecast_posteriors DROP COLUMN trade_authority_status")
        columns.remove("trade_authority_status")
    if {"runtime_layer", "city", "target_date", "temperature_metric", "computed_at"}.issubset(columns):
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_forecast_posteriors_runtime_layer_target
                ON forecast_posteriors(runtime_layer, city, target_date, temperature_metric, computed_at)
        """)


def _ensure_observation_hourly_extrema_compatibility(conn: sqlite3.Connection) -> None:
    """Repair legacy observation extrema schema so later ALTER TABLE can run."""

    columns = _table_columns(conn, "observation_instants")
    if not columns:
        return
    if "running_min" not in columns:
        conn.execute("ALTER TABLE observation_instants ADD COLUMN running_min REAL")
        columns.add("running_min")
    conn.execute("DROP VIEW IF EXISTS observation_hourly_extrema_v2")
    conn.execute("DROP VIEW IF EXISTS observation_hourly_extrema")
    conn.execute("""
        CREATE VIEW observation_hourly_extrema AS
            SELECT
                o.*,
                o.running_max AS hour_bucket_max,
                o.running_min AS hour_bucket_min
            FROM observation_instants o
    """)


def _create_replacement_forecast_live_tables(conn: sqlite3.Connection) -> None:
    """Create replacement forecast live-support/provenance tables."""

    _ensure_observation_hourly_extrema_compatibility(conn)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_forecast_artifacts (
            artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            product_id TEXT NOT NULL,
            data_version TEXT NOT NULL,
            source_cycle_time TEXT NOT NULL,
            source_available_at TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            artifact_path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
            request_url TEXT,
            request_params_json TEXT NOT NULL DEFAULT '{}',
            artifact_metadata_json TEXT NOT NULL DEFAULT '{}',
            training_allowed INTEGER NOT NULL DEFAULT 0
                CHECK (training_allowed = 0),
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
            UNIQUE(source_id, product_id, data_version, source_cycle_time, sha256)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_raw_forecast_artifacts_product_cycle
            ON raw_forecast_artifacts(source_id, product_id, source_cycle_time)
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS deterministic_forecast_anchors (
            anchor_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            product_id TEXT NOT NULL,
            data_version TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low')),
            anchor_value_c REAL NOT NULL,
            source_cycle_time TEXT NOT NULL,
            source_available_at TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            artifact_id INTEGER REFERENCES raw_forecast_artifacts(artifact_id),
            model TEXT NOT NULL,
            native_grid TEXT,
            delivery_grid_resolution TEXT,
            interpolation_method TEXT,
            contributing_times_json TEXT NOT NULL DEFAULT '[]',
            anchor_identity_hash TEXT,
            provenance_json TEXT NOT NULL DEFAULT '{}',
            training_allowed INTEGER NOT NULL DEFAULT 0
                CHECK (training_allowed = 0),
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_deterministic_forecast_anchors_target
            ON deterministic_forecast_anchors(city, target_date, temperature_metric, source_id, product_id)
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_deterministic_forecast_anchors_identity_hash
            ON deterministic_forecast_anchors(anchor_identity_hash)
            WHERE anchor_identity_hash IS NOT NULL
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            product_id TEXT NOT NULL,
            data_version TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low')),
            source_cycle_time TEXT NOT NULL,
            source_available_at TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            q_json TEXT NOT NULL,
            q_lcb_json TEXT,
            q_ucb_json TEXT,
            posterior_method TEXT NOT NULL,
            openmeteo_anchor_id INTEGER REFERENCES deterministic_forecast_anchors(anchor_id),
            dependency_source_run_ids_json TEXT NOT NULL DEFAULT '[]',
            family_id TEXT,
            bin_topology_hash TEXT,
            dependency_hash TEXT,
            posterior_config_hash TEXT,
            posterior_identity_hash TEXT,
            provenance_json TEXT NOT NULL DEFAULT '{}',
            runtime_layer TEXT NOT NULL DEFAULT 'live'
                CHECK (runtime_layer IN ('live')),
            training_allowed INTEGER NOT NULL DEFAULT 0
                CHECK (training_allowed = 0),
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_forecast_posteriors_target
            ON forecast_posteriors(city, target_date, temperature_metric, product_id, computed_at)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_forecast_posteriors_topology
            ON forecast_posteriors(city, target_date, temperature_metric, bin_topology_hash, computed_at)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_forecast_posteriors_live_family_cycle
            ON forecast_posteriors(product_id, city, target_date, temperature_metric,
                                   source_cycle_time, computed_at, posterior_id)
            WHERE runtime_layer = 'live'
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_forecast_posteriors_identity_hash
            ON forecast_posteriors(posterior_identity_hash)
            WHERE posterior_identity_hash IS NOT NULL
    """)
    _ensure_forecast_posteriors_runtime_layer_compatibility(conn)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS replacement_shadow_decisions (
            decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
            posterior_id INTEGER NOT NULL REFERENCES forecast_posteriors(posterior_id),
            baseline_source_run_id TEXT,
            market_snapshot_id TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            decision_time TEXT NOT NULL,
            baseline_direction TEXT NOT NULL,
            candidate_direction TEXT NOT NULL,
            allowed_direction TEXT NOT NULL,
            baseline_q_lcb REAL NOT NULL CHECK (baseline_q_lcb >= 0.0 AND baseline_q_lcb <= 1.0),
            candidate_q_lcb REAL NOT NULL CHECK (candidate_q_lcb >= 0.0 AND candidate_q_lcb <= 1.0),
            allowed_q_lcb REAL NOT NULL CHECK (allowed_q_lcb >= 0.0 AND allowed_q_lcb <= 1.0),
            baseline_kelly_fraction REAL NOT NULL CHECK (baseline_kelly_fraction >= 0.0),
            candidate_kelly_fraction REAL NOT NULL CHECK (candidate_kelly_fraction >= 0.0),
            allowed_kelly_fraction REAL NOT NULL CHECK (allowed_kelly_fraction >= 0.0),
            veto INTEGER NOT NULL CHECK (veto IN (0, 1)),
            veto_reason TEXT,
            dependency_source_run_ids_json TEXT NOT NULL DEFAULT '[]',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
            UNIQUE(posterior_id, market_snapshot_id, condition_id, token_id, decision_time)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_replacement_shadow_decisions_market_time
            ON replacement_shadow_decisions(condition_id, token_id, decision_time)
    """)

    # ------------------------------------------------------------------------
    # raw_model_forecasts  (BAYES_PRECISION_FUSION_SPEC.md §6 F1 raw capture)
    # ------------------------------------------------------------------------
    # Multi-model walk-forward capture table. One row per
    # (model, city, target_date, metric, source_cycle_time, endpoint): the decorrelated
    # globals (gfs_global/icon_global/gem_global/jma_seamless/icon_eu) + in-domain regionals
    # (icon_d2/arome) fetched ALONGSIDE the single ECMWF anchor. forecast_value_c is ALWAYS
    # degC (SPEC §7 "C/F unit mix" antibody — the residual against settlement is taken in C).
    # endpoint distinguishes single_runs (live capture, variable-lead, replay) from
    # previous_runs (fixed-lead, the ONLY rows that train walk-forward history; SPEC §3
    # causality run_time != source_available_at). training_allowed=0 is
    # CHECK-pinned exactly like raw_forecast_artifacts: this is an experiment-accrual surface,
    # never an order/training truth table. Lives ONLY on zeus-forecasts.db (FORECAST_CLASS,
    # INV-37 single-DB). The walk-forward history JOIN (src/data/bayes_precision_fusion_history_provider.py)
    # reads endpoint='previous_runs' rows JOINed to settlement_outcomes (same DB) with
    # target_date < decision_date and authority='VERIFIED' (no-leak, IRON RULE #3).
    # BLOCKER 4 (live-money data provenance, Fitz Constraint #4): the original capture columns
    # could NOT prove the PHYSICAL product behind forecast_value_c. The product-identity columns
    # below make a stored value reconstructable to its exact Open-Meteo product:
    #   source_id/source_family/product_id/provider/model_name — WHICH feed/model id served it
    #     (e.g. anchor stored model='ecmwf_ifs' but model_name='ecmwf_ifs025' is the OM product);
    #   request_params_json/request_url_hash — the exact request that produced the value;
    #   latitude_requested/longitude_requested/timezone_requested — requested point (city vs
    #     station) and the tz the local-day window was taken in;
    #   cell_selection/elevation_param/downscaling_policy — OM grid-cell + elevation/downscaling
    #     choices that change the returned 2m temperature (a different cell = a different product);
    #   endpoint_mode — daily vs hourly-agg / single_runs vs previous_runs physical endpoint;
    #   model_domain_hash — fingerprint binding (provider, model_name, cell_selection,
    #     elevation_param, downscaling_policy, endpoint_mode) so two physical cells never conflate;
    #   coverage_status — whether the requested point was actually covered by the product;
    #   raw_sha256 / artifact_id (both NULLABLE) — link to the immutable raw artifact when present
    #     (capture may precede artifact persistence).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_model_forecasts (
            raw_model_forecast_id INTEGER PRIMARY KEY AUTOINCREMENT,
            model TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            metric TEXT NOT NULL CHECK (metric IN ('high', 'low')),
            source_cycle_time TEXT NOT NULL,
            source_available_at TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            lead_days INTEGER NOT NULL CHECK (lead_days >= 0),
            forecast_value_c REAL NOT NULL,
            endpoint TEXT NOT NULL CHECK (endpoint IN ('single_runs', 'previous_runs')),
            source_id TEXT,
            source_family TEXT,
            product_id TEXT,
            provider TEXT,
            model_name TEXT,
            request_params_json TEXT NOT NULL DEFAULT '{}',
            request_url_hash TEXT,
            raw_sha256 TEXT,
            latitude_requested REAL,
            longitude_requested REAL,
            timezone_requested TEXT,
            cell_selection TEXT,
            elevation_param TEXT,
            downscaling_policy TEXT,
            endpoint_mode TEXT,
            model_domain_hash TEXT,
            coverage_status TEXT,
            artifact_id INTEGER REFERENCES raw_forecast_artifacts(artifact_id),
            training_allowed INTEGER NOT NULL DEFAULT 0
                CHECK (training_allowed = 0),
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
            -- BLOCKER 4 (operator-sharpened): the uniqueness MUST include the physical request
            -- identity (product_id, request_url_hash). The pre-fix UNIQUE keyed ONLY on the
            -- logical (model,city,target_date,metric,source_cycle_time,endpoint) tuple, so a Run-2
            -- with the SAME logical key but a DIFFERENT request (changed timezone/cell_selection/
            -- elevation/product_id) collided with the stale row and -- under INSERT OR IGNORE --
            -- was silently discarded, leaving a wrong forecast_value_c to contaminate
            -- bias/MAE/sigma/covariance/q in the walk-forward history JOIN. Including
            -- product_id + request_url_hash makes a changed request a NEW row, never an ignore.
            -- The persist layer additionally REJECTS a same-logical-key/different-request_hash
            -- insert (RawModelForecastRequestConflict + an audit row) so a corrected request is a
            -- LOUD, attributable event rather than two silently-coexisting rows the history JOIN
            -- (which keys on model/city/metric/lead/endpoint/target_date, NOT on the hash) would
            -- conflate. See src/data/bayes_precision_fusion_download.py::_persist_rows.
            UNIQUE(model, product_id, request_url_hash, city, target_date, metric,
                   source_cycle_time, endpoint)
        )
    """)
    # Idempotent forward-only migration for pre-existing DBs created before the product-identity
    # extension: ADD each column only if absent (guards on PRAGMA table_info). Forward-only, no
    # DROP. New columns are nullable (or DEFAULT '{}') so existing rows remain valid.
    _existing_rmf_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(raw_model_forecasts)").fetchall()
    }
    if "trade_authority_status" in _existing_rmf_cols:
        # raw_model_forecasts is a raw current/history input table, not a posterior authority table.
        # Old live DBs carried a DIAGNOSTIC_ONLY-only column that made live production rows look
        # experimental and invited downstream authority confusion. The executable authority lives
        # on forecast_posteriors.runtime_layer/q_mode; raw rows keep training_allowed=0.
        conn.execute("ALTER TABLE raw_model_forecasts DROP COLUMN trade_authority_status")
        _existing_rmf_cols.remove("trade_authority_status")
    _rmf_product_identity_alters = (
        ("source_id", "TEXT"),
        ("source_family", "TEXT"),
        ("product_id", "TEXT"),
        ("provider", "TEXT"),
        ("model_name", "TEXT"),
        ("request_params_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("request_url_hash", "TEXT"),
        ("raw_sha256", "TEXT"),
        ("latitude_requested", "REAL"),
        ("longitude_requested", "REAL"),
        ("timezone_requested", "TEXT"),
        ("cell_selection", "TEXT"),
        ("elevation_param", "TEXT"),
        ("downscaling_policy", "TEXT"),
        ("endpoint_mode", "TEXT"),
        ("model_domain_hash", "TEXT"),
        ("coverage_status", "TEXT"),
        ("artifact_id", "INTEGER"),
    )
    for _col, _decl in _rmf_product_identity_alters:
        if _col not in _existing_rmf_cols:
            conn.execute(f"ALTER TABLE raw_model_forecasts ADD COLUMN {_col} {_decl}")
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_raw_model_forecasts_history_join
            ON raw_model_forecasts(city, metric, lead_days, endpoint, model, target_date)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_raw_model_forecasts_captured_at
            ON raw_model_forecasts(captured_at)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_raw_model_forecasts_product_identity
            ON raw_model_forecasts(city, metric, lead_days, endpoint, model_domain_hash, target_date)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_raw_model_forecasts_current_family_cycle_members
            ON raw_model_forecasts(city, target_date, metric, source_cycle_time,
                                   source_available_at, model)
            WHERE endpoint = 'single_runs' AND forecast_value_c IS NOT NULL
    """)
    # BLOCKER 4 (operator-sharpened) — forward-only widened uniqueness for PRE-EXISTING DBs.
    # SQLite cannot ALTER the table-level UNIQUE constraint of an already-created table without a
    # full table rebuild; a CREATE UNIQUE INDEX IF NOT EXISTS adds the SAME widened uniqueness
    # (logical key + product_id + request_url_hash) forward-only, no DROP/rebuild. On a fresh DB
    # this is redundant with the table-level UNIQUE above (both pin the identical column set); on
    # a legacy DB it is the only way the widened key reaches the physical table. The persist layer
    # (src/data/bayes_precision_fusion_download.py::_persist_rows) is the PRIMARY antibody — it REJECTS a
    # same-logical-key/different-request-hash insert before it is attempted — and this index is
    # defense-in-depth for any write path that bypasses _persist_rows.
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_model_forecasts_logical_plus_request
            ON raw_model_forecasts(model, product_id, request_url_hash, city, target_date,
                                   metric, source_cycle_time, endpoint)
    """)
    # BLOCKER 4 audit lane — the conflict ledger. When a same-logical-key insert arrives with a
    # DIFFERENT physical request identity (a corrected request: changed timezone / cell_selection
    # / elevation / product_id / request_url_hash), the persist layer raises
    # RawModelForecastRequestConflict AND writes one row here, recording BOTH the existing and the
    # incoming request identity. This converts the pre-fix SILENT INSERT-OR-IGNORE drop into a
    # loud, forensically-attributable event (Fitz Constraint #3 immune system: an antibody, not an
    # alert). This is a non-execution audit ledger, never an order/training truth table.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_model_forecast_request_conflicts (
            conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
            detected_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
            model TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            metric TEXT NOT NULL,
            source_cycle_time TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            existing_product_id TEXT,
            incoming_product_id TEXT,
            existing_request_url_hash TEXT,
            incoming_request_url_hash TEXT,
            existing_forecast_value_c REAL,
            incoming_forecast_value_c REAL,
            existing_cell_selection TEXT,
            incoming_cell_selection TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_rmf_request_conflicts_logical_key
            ON raw_model_forecast_request_conflicts(model, city, target_date, metric,
                                                    source_cycle_time, endpoint)
    """)
    # Task #32 (PARTIAL-fusion upgrade trigger) idempotency marker. When the fusion-upgrade
    # trigger (src/data/replacement_fusion_upgrade_trigger.py) detects that a scope's latest
    # posterior was fused from a STRICTLY SMALLER decorrelated-provider family set than is now
    # capturable at the SAME source_cycle_time, it enqueues ONE re-materialization seed and writes
    # one row here. The UNIQUE index on (city, target_date, metric, source_cycle_time,
    # capturable_family_set) is the bound: a scope is re-enqueued AT MOST ONCE per
    # (cycle, capturable-family-superset) transition, so a still-missing 5th provider (gfs HTTP
    # 400, jma off the 06Z single_runs cadence) can never loop the queue. SHADOW research surface
    # only — never an order/training truth table.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fusion_upgrade_enqueues (
            enqueue_id INTEGER PRIMARY KEY AUTOINCREMENT,
            enqueued_at TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            metric TEXT NOT NULL,
            source_cycle_time TEXT NOT NULL,
            served_family_set TEXT NOT NULL,
            capturable_family_set TEXT NOT NULL,
            seed_file TEXT
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_fusion_upgrade_enqueues_scope_cycle_superset
            ON fusion_upgrade_enqueues(city, target_date, metric, source_cycle_time,
                                       capturable_family_set)
    """)
    # U5 step 2a (newer-cycle-triggered re-materialization, freshness investigation 2026-06-12)
    # idempotency marker. Sibling of fusion_upgrade_enqueues: when the cycle-advance trigger
    # (src/data/replacement_cycle_advance_trigger.py) detects that a scope's latest posterior
    # consumed a model cycle OLDER than the freshest in-universe cycle now ingested, it enqueues ONE
    # re-materialization seed and writes one row here. The UNIQUE index on
    # (city, target_date, metric, target_cycle_time) is the bound: a scope is re-enqueued AT MOST
    # ONCE per (target-cycle) advance, so a still-unmaterialized seed (manifest not yet on disk,
    # day0 guard) can never loop the queue — it heals on the next tick once the seed drains, and
    # the NEXT fresher cycle gets its own distinct marker. SHADOW research surface only.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cycle_advance_enqueues (
            enqueue_id INTEGER PRIMARY KEY AUTOINCREMENT,
            enqueued_at TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            metric TEXT NOT NULL,
            consumed_cycle_time TEXT NOT NULL,
            target_cycle_time TEXT NOT NULL,
            held_position INTEGER NOT NULL DEFAULT 0,
            seed_file TEXT,
            reason TEXT
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_cycle_advance_enqueues_scope_target_cycle
            ON cycle_advance_enqueues(city, target_date, metric, target_cycle_time)
    """)
    # FINDING 2 (external review 2026-06-12): per-family leg-artifact gap visibility. When a held
    # family's freshest materializable cycle is blocked because ONE raw leg's artifact is missing
    # for THAT (city, target_date) scope, the trigger records a typed reason row here (e.g.
    # CYCLE_LEG_ARTIFACT_MISSING:<source>:<cycle>) instead of silently incrementing manifest_missing
    # — making the ALWAYS-DECIDABLE-violating gap visible rather than an invisible skip. Idempotent
    # ADD COLUMN for existing DBs.
    # day0_observed_extreme_observation_time (same-day exit-blindness fix 2026-06-23): the OBSERVATION
    # VERSION the marker was last enqueued at. The model cycle (target_cycle_time) does NOT advance
    # intraday on the settlement day, so model-cycle idempotency alone freezes the day0-conditioned
    # posterior (Toronto NO@24 -98.94% incident). Recording the observation version lets the held/day0
    # reseed re-materialize on each fresh observed running-max version (climb OR plateau) without
    # touching the non-day0 model-cycle idempotency. See
    # docs/evidence/same_day_exit_blindness/2026-06-23_toronto_total_loss.md.
    for alter_sql in [
        "ALTER TABLE cycle_advance_enqueues ADD COLUMN reason TEXT",
        "ALTER TABLE cycle_advance_enqueues ADD COLUMN day0_observed_extreme_observation_time TEXT",
    ]:
        try:
            conn.execute(alter_sql)
        except Exception as exc:  # noqa: BLE001
            if "duplicate column" not in str(exc).lower():
                raise


def _create_calibration_pairs(conn: sqlite3.Connection) -> None:
    """Create calibration_pairs table + indexes + ALTERs. Idempotent.

    K1 forecast-class table (moves to zeus-forecasts.db). Architect refinement:
    UNIQUE on the full dedup key. Phase 2 ALTERs for cycle/source_id/horizon_profile.
    Collapsed from calibration_pairs_v2 (B3 rename — bare v2 shell dropped).
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS calibration_pairs (
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
            -- PHASE0-PR4: decision_group_id NOT NULL enforcement is LIVE (PR 4 production).
            -- Canonical enforcement: TRIGGER-mode (default, disk-safe).
            --   scripts/migrate_calibration_pairs_not_null.py --apply --mode trigger
            --   Two BEFORE INSERT + BEFORE UPDATE triggers per table enforce NOT NULL.
            --   Idempotent (CREATE TRIGGER IF NOT EXISTS). Zero disk overhead.
            --   PRAGMA table_info still shows notnull=0 (column DDL unchanged).
            -- Optional canonical DDL rebuild (requires ~50 GiB free disk):
            --   scripts/migrate_calibration_pairs_not_null.py --apply --mode rebuild
            --   Produces notnull=1 in PRAGMA table_info but requires disk headroom.
            --   BLOCKED at current 22 GiB free — operator-coordinated separately.
            -- Preflight confirmed: 0 NULL rows as of 2026-05-17. See:
            --   docs/archive/2026-Q2/task_2026-05-17_strategy_vnext_phase0/preflight/migration_dry_runs.json
            -- Column DDL unchanged until rebuild-mode migration runs:
            decision_group_id TEXT,
            bias_corrected INTEGER NOT NULL DEFAULT 0
                CHECK (bias_corrected IN (0, 1)),
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
                CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
            bin_source TEXT NOT NULL DEFAULT 'legacy',
            snapshot_id INTEGER REFERENCES ensemble_snapshots(snapshot_id),
            dataset_id TEXT NOT NULL,
            training_allowed INTEGER NOT NULL DEFAULT 1
                CHECK (training_allowed IN (0, 1)),
            causality_status TEXT NOT NULL DEFAULT 'OK',
            cycle TEXT NOT NULL DEFAULT '00',
            source_id TEXT NOT NULL DEFAULT 'tigge_mars',
            horizon_profile TEXT NOT NULL DEFAULT 'full',
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
            UNIQUE(city, target_date, temperature_metric, range_label, lead_days,
                   forecast_available_at, bin_source, dataset_id)
        )
    """)
    # Phase 2 (2026-05-04): cycle/source_id/horizon_profile stratification —
    # idempotent ALTER so legacy DBs migrated via
    # scripts/migrate_phase2_cycle_stratification.py converge with fresh DBs
    # built from this canonical schema. Defaults match the migration script.
    # error_model_family (2026-05-24): predictive-error provenance tag. 'none'
    # means raw uncorrected pairs (byte-identical to pre-error-model rebuilds);
    # e.g. 'full_transport_v1' means the universal location+scale+gate+transport
    # correction was applied pre-MC. Stamped per-row so the Platt refit can key
    # its bucket model_key on the exact correction family the pairs were built
    # under (train/serve consistency, INV-bias-state). Not in UNIQUE — SQLite
    # cannot ALTER an existing UNIQUE, and a single rebuild scope only ever
    # writes ONE family; the destructive delete is keyed on bin_source.
    for alter_sql in [
        "ALTER TABLE calibration_pairs ADD COLUMN cycle TEXT NOT NULL DEFAULT '00'",
        "ALTER TABLE calibration_pairs ADD COLUMN source_id TEXT NOT NULL DEFAULT 'tigge_mars'",
        "ALTER TABLE calibration_pairs ADD COLUMN horizon_profile TEXT NOT NULL DEFAULT 'full'",
        "ALTER TABLE calibration_pairs ADD COLUMN error_model_family TEXT NOT NULL DEFAULT 'none'",
    ]:
        try:
            conn.execute(alter_sql)
        except Exception as exc:
            if "duplicate column" not in str(exc).lower():
                raise
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_calibration_pairs_bucket
            ON calibration_pairs(temperature_metric, cluster, season, lead_days)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_calibration_pairs_city_date_metric
            ON calibration_pairs(city, target_date, temperature_metric)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_calibration_pairs_refit_core
            ON calibration_pairs(temperature_metric, dataset_id, training_allowed, authority)
    """)
    # PHASE0-PR4: Install NOT NULL triggers on fresh DBs so the enforcement invariant
    # holds from the first INSERT, not only after the operator runs the migration script.
    # Idempotent (CREATE TRIGGER IF NOT EXISTS).
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS calibration_pairs_dgid_not_null_ins
        BEFORE INSERT ON calibration_pairs
        WHEN NEW.decision_group_id IS NULL
        BEGIN
            SELECT RAISE(ABORT, 'NOT NULL: calibration_pairs.decision_group_id');
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS calibration_pairs_dgid_not_null_upd
        BEFORE UPDATE OF decision_group_id ON calibration_pairs
        WHEN NEW.decision_group_id IS NULL
        BEGIN
            SELECT RAISE(ABORT, 'NOT NULL: calibration_pairs.decision_group_id');
        END
    """)


def _create_settlement_capture_verifications(conn: sqlite3.Connection) -> None:
    """Create settlement_capture_verifications table + index. Idempotent.

    K1 forecast-class only table. NOT in world_src.sqlite_master — always
    created via this static helper (same pattern as _create_market_microstructure_snapshots).

    Phase 7 T3 — SCHEMA_FORECASTS_VERSION 6 (2026-05-21).
    One row per (city, target_date, temperature_metric) with 3-valued
    coherence_verdict: COHERENT | INCOHERENT | INCOMPLETE.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settlement_capture_verifications (
            verification_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL
                CHECK (temperature_metric IN ('high', 'low')),
            fact_known_time TEXT,
            source_published_time TEXT,
            venue_resolved_time TEXT,
            redeemed_time TEXT,
            coherence_verdict TEXT NOT NULL
                CHECK (coherence_verdict IN ('COHERENT', 'INCOHERENT', 'INCOMPLETE')),
            incoherence_reason TEXT,
            evidence_tier TEXT,
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
            UNIQUE(city, target_date, temperature_metric)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_scv_city_date_metric
            ON settlement_capture_verifications(city, target_date, temperature_metric)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_scv_verdict
            ON settlement_capture_verifications(coherence_verdict)
    """)


def ensure_replacement_forecast_live_schema(conn: sqlite3.Connection) -> None:
    """Create only the replacement forecast live-support tables on a forecast DB.

    This is the targeted simple-switch initializer for the Open-Meteo ECMWF IFS
    9km + Bayes fusion path. It deliberately avoids the broader canonical
    schema migration surface and creates no world/trade truth tables.
    """

    nested_transaction = conn.in_transaction
    if nested_transaction:
        conn.execute("SAVEPOINT replacement_forecast_live_schema")
    else:
        conn.execute("BEGIN")
    try:
        _create_replacement_forecast_live_tables(conn)
        if nested_transaction:
            conn.execute("RELEASE SAVEPOINT replacement_forecast_live_schema")
        else:
            conn.execute("COMMIT")
    except Exception:
        try:
            if nested_transaction:
                conn.execute("ROLLBACK TO SAVEPOINT replacement_forecast_live_schema")
                conn.execute("RELEASE SAVEPOINT replacement_forecast_live_schema")
            else:
                conn.execute("ROLLBACK")
        except Exception:
            pass
        raise


def apply_canonical_schema(conn: sqlite3.Connection, *, forecast_tables: bool = True) -> None:
    """Apply the Zeus World DB v2 schema to *conn*.

    Safe to call on both zeus-world.db and zeus_trades.db.
    Safe to call multiple times — all DDL uses IF NOT EXISTS / IF EXISTS.

    Args:
        forecast_tables: When True (default), create the 4 forecast-class v2
            tables (settlement_outcomes, market_events, ensemble_snapshots,
            calibration_pairs). Set to False for init_schema_world_only so
            world conn does not recreate tables that live on zeus-forecasts.db
            post-K1 migration. K1 split 2026-05-11.

    # Fix (task #200, 2026-05-10): Re-apply PRAGMA busy_timeout at the start of
    # this function. Python's sqlite3.executescript() resets the C-level busy
    # handler that sqlite3.connect(timeout=N) installs, so any subsequent
    # conn.execute() on the same connection has no wait budget and fails
    # immediately on lock contention. Restoring busy_timeout here makes
    # apply_canonical_schema robust regardless of what ran on *conn* before it.
    # ZEUS_DB_BUSY_TIMEOUT_MS default matches db.py _db_busy_timeout_s() (30 s).
    """
    _busy_timeout_ms = int(os.environ.get("ZEUS_DB_BUSY_TIMEOUT_MS", "30000"))
    conn.execute(f"PRAGMA busy_timeout = {_busy_timeout_ms}")

    # Save foreign_keys state before touching anything. SQLite ignores
    # PRAGMA foreign_keys changes once a caller-owned transaction has begun, so
    # the nested path must avoid FK-sensitive cleanup rather than pretending it
    # has a foreign-key-off migration envelope.
    (fk_before,) = conn.execute("PRAGMA foreign_keys").fetchone()

    nested_transaction = conn.in_transaction
    may_run_fk_sensitive_cleanup = not nested_transaction or fk_before == 0

    try:
        if nested_transaction:
            conn.execute("SAVEPOINT zeus_schema")
        else:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN")

        # ----------------------------------------------------------------
        # Drop 3 dead tables (D2 — 0 rows, no writers)
        # model_skill is intentionally excluded: etl_historical_forecasts.py
        # writes to it actively. Cleanup deferred to a later phase.
        # ----------------------------------------------------------------
        if may_run_fk_sensitive_cleanup:
            conn.execute("DROP TABLE IF EXISTS promotion_registry")
            conn.execute("DROP TABLE IF EXISTS model_eval_point")
            conn.execute("DROP TABLE IF EXISTS model_eval_run")

        if forecast_tables:
            # ----------------------------------------------------------------
            # settlement_outcomes  (K1 forecast-class: moves to zeus-forecasts.db)
            # B3cont (2026-05-28): collapsed from settlement_outcomes.
            # ----------------------------------------------------------------
            _create_settlement_outcomes(conn)
            # Phase 7 T1 — ALTER for existing settlement_outcomes rows on migrated DBs.
            # _create_settlement_outcomes adds outcome_type only in CREATE TABLE IF NOT EXISTS;
            # existing DBs need an explicit ALTER. Guard for duplicate column.
            try:
                conn.execute("ALTER TABLE settlement_outcomes ADD COLUMN outcome_type INTEGER")
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise

            # ----------------------------------------------------------------
            # market_events  (K1 forecast-class: moves to zeus-forecasts.db)
            # Collapsed from market_events in B3cont (PR3).
            # ----------------------------------------------------------------
            _create_market_events(conn)

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

        if forecast_tables:
            # ----------------------------------------------------------------
            # ensemble_snapshots  (K1 forecast-class: moves to zeus-forecasts.db)
            # ----------------------------------------------------------------
            _create_ensemble_snapshots(conn)

            # ----------------------------------------------------------------
            # Replacement forecast live-support provenance tables.
            # ----------------------------------------------------------------
            _create_replacement_forecast_live_tables(conn)

            # ----------------------------------------------------------------
            # calibration_pairs  (K1 forecast-class: moves to zeus-forecasts.db)
            # ----------------------------------------------------------------
            _create_calibration_pairs(conn)

        # ----------------------------------------------------------------
        # platt_models
        # ----------------------------------------------------------------
        conn.execute("""
            CREATE TABLE IF NOT EXISTS platt_models (
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
                training_cutoff TEXT,
                recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
                -- 2026-05-05 critic-opus Blocker 1: UNIQUE extended with
                -- stratification keys so cross-cycle Platt rows do not collide
                -- on insert. Legacy DBs must be rebuilt via
                -- scripts/migrate_phase2_cycle_stratification.py to converge.
                UNIQUE(temperature_metric, cluster, season, data_version,
                       input_space, is_active, cycle, source_id, horizon_profile)
            )
        """)
        # Phase 2 (2026-05-04): cycle/source_id/horizon_profile stratification —
        # idempotent ALTER for legacy DBs. Mirror of the calibration_pairs
        # block above; defaults match scripts/migrate_phase2_cycle_stratification.py.
        # error_model_family (2026-05-24): mirror of the calibration_pairs
        # column. A Platt model fit on pairs built under family F MUST advertise
        # F so the live serving guard (assert_bias_state_consistent) can refuse a
        # train/serve mismatch (live bias-correction enabled while the active
        # Platt was fit on a different family's input space). Concatenated into
        # model_key in save_platt_model. Not in UNIQUE (same rationale as
        # calibration_pairs): SQLite cannot ALTER an existing UNIQUE.
        for alter_sql in [
            "ALTER TABLE platt_models ADD COLUMN cycle TEXT NOT NULL DEFAULT '00'",
            "ALTER TABLE platt_models ADD COLUMN source_id TEXT NOT NULL DEFAULT 'tigge_mars'",
            "ALTER TABLE platt_models ADD COLUMN horizon_profile TEXT NOT NULL DEFAULT 'full'",
            "ALTER TABLE platt_models ADD COLUMN training_cutoff TEXT",
            "ALTER TABLE platt_models ADD COLUMN error_model_family TEXT NOT NULL DEFAULT 'none'",
            # identity_full_transport_v1 (Zeus #64, 2026-05-25): explicit route for
            # full_transport p_raw when ECE is already low — no Platt transform applied.
            # 'platt' is the default for all existing rows (backward-compatible).
            "ALTER TABLE platt_models ADD COLUMN calibration_method TEXT NOT NULL DEFAULT 'platt'",
        ]:
            try:
                conn.execute(alter_sql)
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        platt_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(platt_models)").fetchall()
        }
        if {
            "temperature_metric",
            "cluster",
            "season",
            "data_version",
            "input_space",
            "is_active",
        }.issubset(platt_columns):
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_platt_models_lookup
                    ON platt_models(temperature_metric, cluster, season, data_version, input_space, is_active)
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
        # observation_instants  (CANONICAL — superset of the former legacy
        # observation_instants subset; consolidated 2026-05-29 from
        # observation_instants_v2. The legacy subset DDL in db.py:1409 is
        # DELETED so this is the single source of truth for the table.)
        # Architect refinement: running_min column for low-track obs support
        # ----------------------------------------------------------------
        # B4 (2026-04-26): physical-bounds CHECK on temp columns. Applies to
        # NEW DBs only — SQLite ALTER cannot add CHECK retroactively (db.py
        # comment at L330-333 same pattern). Writer-level validation in
        # observation_instants_writer._validate() is the load-bearing
        # antibody for legacy DBs. Bounds: -90/60 °C inclusive, -130/140 °F
        # inclusive. NULL passes through (fields are nullable per schema).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS observation_instants (
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
            CREATE INDEX IF NOT EXISTS idx_observation_instants_city_ts
                ON observation_instants(city, target_date, utc_timestamp)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS observation_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name TEXT NOT NULL
                    CHECK (table_name IN ('observation_instants', 'observations')),
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
                recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_observation_revisions_obs_lookup
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
        # observation_instants with INV-14 identity spine (temperature_metric
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
            "ALTER TABLE observation_instants ADD COLUMN authority TEXT NOT NULL DEFAULT 'UNVERIFIED'",
            "ALTER TABLE observation_instants ADD COLUMN data_version TEXT NOT NULL DEFAULT 'v1'",
            "ALTER TABLE observation_instants ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}'",
            "ALTER TABLE observation_instants ADD COLUMN running_min REAL",
            "ALTER TABLE observation_instants ADD COLUMN temperature_metric TEXT",
            "ALTER TABLE observation_instants ADD COLUMN physical_quantity TEXT",
            "ALTER TABLE observation_instants ADD COLUMN observation_field TEXT",
            "ALTER TABLE observation_instants ADD COLUMN training_allowed INTEGER DEFAULT 1",
            "ALTER TABLE observation_instants ADD COLUMN causality_status TEXT DEFAULT 'OK'",
            "ALTER TABLE observation_instants ADD COLUMN source_role TEXT",
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
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
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
                FROM observation_instants o
                JOIN zeus_meta m
                  ON m.key = 'observation_data_version'
                 AND o.data_version = m.value
        """)

        # ----------------------------------------------------------------
        # observation_hourly_extrema (PR-C compatibility view)
        # Aliases running_max / running_min to hour_bucket_max / hour_bucket_min
        # so call-sites can make the non-monotonic semantics explicit without
        # any schema migration on the live table.  The original column names
        # are preserved (no DROP / RENAME); this view is additive-only.
        # Created AFTER the ADD COLUMN block so o.* includes all columns.
        # Consolidation 2026-05-29: dropped the legacy _v2-suffixed alias view
        # too, so no stale view points at the now-renamed table.
        # ----------------------------------------------------------------
        _ensure_observation_hourly_extrema_compatibility(conn)

        # ----------------------------------------------------------------
        # historical_forecasts — DROPPED in B3 (PR3).
        # No writers existed (no INSERT in src/ as of PR-S4b audit 2026-05-18).
        # Readers in replay.py / status_summary.py / verify_truth_surfaces.py
        # are guarded by _table_exists(); they will skip gracefully on live DBs
        # where the table has not been created. Live DB migration: ALTER/DROP
        # handled by pr3_b3_live_table_rename.py (operator-run, not committed).
        # ----------------------------------------------------------------

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
        # rescue_events — B063: durable audit row for chain-rescue events.
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
            CREATE TABLE IF NOT EXISTS rescue_events (
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
                recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
                UNIQUE(trade_id, occurred_at)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rescue_events_trade_time
                ON rescue_events(trade_id, recorded_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rescue_events_metric_causality
                ON rescue_events(temperature_metric, causality_status, recorded_at)
        """)

        # Fix D (golden-knitting-wand.md Phase 1): per-bucket failure ledger
        # for refit_platt.py. Written when per-bucket SAVEPOINT rolls back
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

        if nested_transaction:
            conn.execute("RELEASE SAVEPOINT zeus_schema")
        else:
            conn.execute("COMMIT")

    except Exception:
        try:
            if nested_transaction:
                conn.execute("rollback to savepoint zeus_schema")
                conn.execute("release savepoint zeus_schema")
            else:
                conn.execute("rollback")
        except Exception:
            pass
        raise
    finally:
        # Restore foreign_keys to whatever it was before we touched it
        if not nested_transaction:
            conn.execute(f"PRAGMA foreign_keys = {fk_before}")
