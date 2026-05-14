-- Pre-P2 sqlite_master baseline: generated from :memory: init_schema_world_only + init_schema_forecasts
-- Purpose: byte-equivalence anchor for P2 DDL refactor (acceptance gate #3)
-- Generated: 2026-05-14
-- Branch tip: 0c10a326e4 (P1 complete)

-- === WORLD DB (init_schema_world_only) ===
-- table: availability_fact
CREATE TABLE availability_fact (
            availability_id TEXT PRIMARY KEY,
            scope_type TEXT NOT NULL CHECK (scope_type IN ('cycle', 'candidate', 'city_target', 'order', 'chain')),
            scope_key TEXT NOT NULL,
            failure_type TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            impact TEXT NOT NULL CHECK (impact IN ('skip', 'degrade', 'retry', 'block')),
            details_json TEXT NOT NULL
);
-- table: calibration_decision_group
CREATE TABLE calibration_decision_group (
            group_id TEXT PRIMARY KEY,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            forecast_available_at TEXT NOT NULL,
            cluster TEXT NOT NULL,
            season TEXT NOT NULL,
            lead_days REAL NOT NULL,
            settlement_value REAL,
            winning_range_label TEXT,
            bias_corrected INTEGER NOT NULL DEFAULT 0 CHECK (bias_corrected IN (0, 1)),
            n_pair_rows INTEGER NOT NULL,
            n_positive_rows INTEGER NOT NULL,
            recorded_at TEXT NOT NULL
        );
-- table: calibration_pairs
CREATE TABLE calibration_pairs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            range_label TEXT NOT NULL,
            p_raw REAL NOT NULL,
            outcome INTEGER NOT NULL,
            lead_days REAL NOT NULL,
            season TEXT NOT NULL,
            cluster TEXT NOT NULL,
            forecast_available_at TEXT NOT NULL,
            settlement_value REAL,
            decision_group_id TEXT,
            bias_corrected INTEGER NOT NULL DEFAULT 0 CHECK (bias_corrected IN (0, 1)),
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED' CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
            bin_source TEXT NOT NULL DEFAULT 'legacy'
        );
-- table: chronicle
CREATE TABLE chronicle (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            trade_id INTEGER,
            timestamp TEXT NOT NULL,
            details_json TEXT NOT NULL
        , env TEXT NOT NULL DEFAULT 'live');
-- table: collateral_ledger_snapshots
CREATE TABLE collateral_ledger_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pusd_balance_micro INTEGER NOT NULL,
  pusd_allowance_micro INTEGER NOT NULL,
  usdc_e_legacy_balance_micro INTEGER NOT NULL,
  ctf_token_balances_json TEXT NOT NULL,
  ctf_token_allowances_json TEXT NOT NULL,
  reserved_pusd_for_buys_micro INTEGER NOT NULL DEFAULT 0,
  reserved_tokens_for_sells_json TEXT NOT NULL DEFAULT '{}',
  captured_at TEXT NOT NULL,
  authority_tier TEXT NOT NULL CHECK (authority_tier IN ('CHAIN','VENUE','DEGRADED')),
  raw_balance_payload_hash TEXT
);
-- table: collateral_reservations
CREATE TABLE collateral_reservations (
  command_id TEXT PRIMARY KEY,
  reservation_type TEXT NOT NULL CHECK (reservation_type IN ('PUSD_BUY','CTF_SELL')),
  token_id TEXT,
  amount INTEGER NOT NULL CHECK (amount >= 0),
  created_at TEXT NOT NULL,
  released_at TEXT,
  release_reason TEXT,
  CHECK (
    (reservation_type = 'PUSD_BUY' AND token_id IS NULL)
    OR (reservation_type = 'CTF_SELL' AND token_id IS NOT NULL)
  )
);
-- table: control_overrides_history
CREATE TABLE control_overrides_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    override_id TEXT NOT NULL,
    target_type TEXT NOT NULL CHECK (target_type IN ('strategy', 'global', 'position')),
    target_key TEXT NOT NULL,
    action_type TEXT NOT NULL,
    value TEXT NOT NULL,
    issued_by TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    effective_until TEXT,
    reason TEXT NOT NULL,
    precedence INTEGER NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('upsert', 'expire', 'migrated', 'revoke')),
    recorded_at TEXT NOT NULL
);
-- table: daily_observation_revisions
CREATE TABLE daily_observation_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            natural_key_json TEXT NOT NULL DEFAULT '{}',
            existing_row_id INTEGER NOT NULL,
            existing_combined_payload_hash TEXT,
            incoming_combined_payload_hash TEXT NOT NULL,
            existing_high_payload_hash TEXT,
            existing_low_payload_hash TEXT,
            incoming_high_payload_hash TEXT NOT NULL,
            incoming_low_payload_hash TEXT NOT NULL,
            reason TEXT NOT NULL CHECK (
                reason IN ('payload_hash_mismatch', 'missing_existing_payload_hash')
            ),
            writer TEXT NOT NULL,
            existing_row_json TEXT NOT NULL,
            incoming_row_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
-- table: data_coverage
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
        );
-- table: day0_metric_fact
CREATE TABLE day0_metric_fact (
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
            );
-- table: decision_log
CREATE TABLE decision_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            artifact_json TEXT NOT NULL,
            timestamp TEXT NOT NULL
        , env TEXT NOT NULL DEFAULT 'live');
-- table: diurnal_curves
CREATE TABLE diurnal_curves (
            city TEXT NOT NULL,
            season TEXT NOT NULL,
            hour INTEGER NOT NULL,
            avg_temp REAL NOT NULL,
            std_temp REAL NOT NULL,
            n_samples INTEGER NOT NULL,
            p_high_set REAL,
            UNIQUE(city, season, hour)
        );
-- table: diurnal_peak_prob
CREATE TABLE diurnal_peak_prob (
            city TEXT NOT NULL,
            month INTEGER NOT NULL,
            hour INTEGER NOT NULL,
            p_high_set REAL NOT NULL,
            n_obs INTEGER NOT NULL,
            UNIQUE(city, month, hour)
        );
-- table: ensemble_snapshots
CREATE TABLE ensemble_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
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
            authority TEXT NOT NULL DEFAULT 'VERIFIED',
            temperature_metric TEXT NOT NULL DEFAULT 'high',
            -- Slice P2-B1 (PR #19 phase 2, 2026-04-26): bias_corrected
            -- declared explicitly. Pre-fix, the column was added only via
            -- the ALTER TABLE migration block below, so fresh init_schema
            -- DBs (CI, dev, in-memory test fixtures) lacked it while
            -- _store_snapshot_p_raw silently expected it. Cross-environment
            -- fragility surfaced as runtime_guards test failures.
            bias_corrected INTEGER NOT NULL DEFAULT 0 CHECK (bias_corrected IN (0, 1)),
            UNIQUE(city, target_date, issue_time, data_version)
        );
-- table: exchange_reconcile_findings
CREATE TABLE exchange_reconcile_findings (
          finding_id TEXT PRIMARY KEY,
          kind TEXT NOT NULL CHECK (kind IN (
            'exchange_ghost_order','local_orphan_order','unrecorded_trade',
            'position_drift','heartbeat_suspected_cancel','cutover_wipe'
          )),
          subject_id TEXT NOT NULL,
          context TEXT NOT NULL CHECK (context IN (
            'periodic','ws_gap','heartbeat_loss','cutover','operator'
          )),
          evidence_json TEXT NOT NULL,
          recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          resolved_at TEXT,
          resolution TEXT,
          resolved_by TEXT
        );
-- table: executable_market_snapshots
CREATE TABLE executable_market_snapshots (
          snapshot_id TEXT PRIMARY KEY,
          gamma_market_id TEXT NOT NULL,
          event_id TEXT NOT NULL,
          event_slug TEXT,
          condition_id TEXT NOT NULL,
          question_id TEXT NOT NULL,
          yes_token_id TEXT NOT NULL,
          no_token_id TEXT NOT NULL,
          selected_outcome_token_id TEXT,
          outcome_label TEXT CHECK (outcome_label IN ('YES','NO') OR outcome_label IS NULL),
          enable_orderbook INTEGER NOT NULL CHECK (enable_orderbook IN (0,1)),
          active INTEGER NOT NULL CHECK (active IN (0,1)),
          closed INTEGER NOT NULL CHECK (closed IN (0,1)),
          accepting_orders INTEGER CHECK (accepting_orders IN (0,1) OR accepting_orders IS NULL),
          market_start_at TEXT,
          market_end_at TEXT,
          market_close_at TEXT,
          sports_start_at TEXT,
          min_tick_size TEXT NOT NULL,
          min_order_size TEXT NOT NULL,
          fee_details_json TEXT NOT NULL,
          token_map_json TEXT NOT NULL,
          rfqe INTEGER CHECK (rfqe IN (0,1) OR rfqe IS NULL),
          neg_risk INTEGER NOT NULL CHECK (neg_risk IN (0,1)),
          orderbook_top_bid TEXT NOT NULL,
          orderbook_top_ask TEXT NOT NULL,
          orderbook_depth_json TEXT NOT NULL,
          raw_gamma_payload_hash TEXT NOT NULL,
          raw_clob_market_info_hash TEXT NOT NULL,
          raw_orderbook_hash TEXT NOT NULL,
          authority_tier TEXT NOT NULL CHECK (authority_tier IN ('GAMMA','DATA','CLOB','CHAIN')),
          captured_at TEXT NOT NULL,
          freshness_deadline TEXT NOT NULL,
          UNIQUE (snapshot_id)
        );
-- table: execution_fact
CREATE TABLE execution_fact (
    intent_id TEXT PRIMARY KEY,
    position_id TEXT,
    decision_id TEXT,
    order_role TEXT NOT NULL CHECK (order_role IN ('entry', 'exit')),
    strategy_key TEXT CHECK (strategy_key IN (
        'settlement_capture',
        'shoulder_sell',
        'center_buy',
        'opening_inertia'
    )),
    posted_at TEXT,
    filled_at TEXT,
    voided_at TEXT,
    submitted_price REAL,
    fill_price REAL,
    shares REAL,
    fill_quality REAL,
    latency_seconds REAL,
    venue_status TEXT,
    terminal_exec_status TEXT
);
-- table: exit_mutex_holdings
CREATE TABLE exit_mutex_holdings (
          mutex_key TEXT PRIMARY KEY,
          command_id TEXT NOT NULL REFERENCES venue_commands(command_id) DEFERRABLE INITIALLY DEFERRED,
          acquired_at TEXT NOT NULL,
          released_at TEXT,
          release_reason TEXT
        );
-- table: forecast_skill
CREATE TABLE forecast_skill (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            lead_days INTEGER NOT NULL,
            forecast_temp REAL NOT NULL,
            actual_temp REAL NOT NULL,
            error REAL NOT NULL,
            temp_unit TEXT NOT NULL,
            season TEXT NOT NULL,
            available_at TEXT NOT NULL,
            UNIQUE(city, target_date, source, lead_days)
        );
-- table: forecasts
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
            source_id TEXT,
            raw_payload_hash TEXT,
            captured_at TEXT,
            authority_tier TEXT,
            rebuild_run_id TEXT,
            data_source_version TEXT,
            availability_provenance TEXT
                CHECK (availability_provenance IS NULL
                       OR availability_provenance IN ('derived_dissemination', 'fetch_time', 'reconstructed', 'recorded')),
            UNIQUE(city, target_date, source, forecast_basis_date)
        );
-- table: historical_forecasts_v2
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
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
                    CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
                data_version TEXT NOT NULL DEFAULT 'v1',
                provenance_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(city, target_date, source, temperature_metric, lead_days)
            );
-- index: idx_calibration_bucket
CREATE INDEX idx_calibration_bucket
            ON calibration_pairs(cluster, season);
-- index: idx_calibration_decision_group_bucket
CREATE INDEX idx_calibration_decision_group_bucket
            ON calibration_decision_group(cluster, season, lead_days);
-- index: idx_calibration_pairs_decision_group
CREATE INDEX idx_calibration_pairs_decision_group ON calibration_pairs(decision_group_id);
-- index: idx_calibration_pairs_group_lookup
CREATE INDEX idx_calibration_pairs_group_lookup ON calibration_pairs(city, target_date, forecast_available_at);
-- index: idx_calibration_pairs_group_lookup_lead
CREATE INDEX idx_calibration_pairs_group_lookup_lead ON calibration_pairs(city, target_date, forecast_available_at, lead_days);
-- index: idx_chronicle_dedup
CREATE INDEX idx_chronicle_dedup
          ON chronicle(trade_id, event_type);
-- index: idx_control_overrides_history_id_time
CREATE INDEX idx_control_overrides_history_id_time
    ON control_overrides_history(override_id, history_id DESC);
-- index: idx_daily_observation_revisions_lookup
CREATE INDEX idx_daily_observation_revisions_lookup
            ON daily_observation_revisions(city, target_date, source, recorded_at);
-- index: idx_data_coverage_retry
CREATE INDEX idx_data_coverage_retry
            ON data_coverage(status, retry_after) WHERE status = 'FAILED';
-- index: idx_data_coverage_scan
CREATE INDEX idx_data_coverage_scan
            ON data_coverage(data_table, city, data_source, target_date);
-- index: idx_data_coverage_status
CREATE INDEX idx_data_coverage_status
            ON data_coverage(status, data_table);
-- index: idx_day0_metric_fact_city_ts
CREATE INDEX idx_day0_metric_fact_city_ts
                ON day0_metric_fact(city, target_date, temperature_metric, utc_timestamp)
        ;
-- index: idx_decision_log_ts
CREATE INDEX idx_decision_log_ts ON decision_log(timestamp);
-- index: idx_ensemble_city_date
CREATE INDEX idx_ensemble_city_date
            ON ensemble_snapshots(city, target_date, available_at);
-- index: idx_envelope_events_subject
CREATE INDEX idx_envelope_events_subject ON provenance_envelope_events (subject_type, subject_id, observed_at);
-- index: idx_findings_unresolved
CREATE INDEX idx_findings_unresolved
          ON exchange_reconcile_findings (resolved_at)
          WHERE resolved_at IS NULL;
-- index: idx_forecasts_city_date
CREATE INDEX idx_forecasts_city_date
            ON forecasts(city, target_date);
-- index: idx_historical_forecasts_v2_lookup
CREATE INDEX idx_historical_forecasts_v2_lookup
                ON historical_forecasts_v2(city, target_date, source, temperature_metric, lead_days)
        ;
-- index: idx_job_run_job_window
CREATE INDEX idx_job_run_job_window
            ON job_run(job_name, scheduled_for);
-- index: idx_job_run_plane_status
CREATE INDEX idx_job_run_plane_status
            ON job_run(plane, status, scheduled_for);
-- index: idx_job_run_source_run
CREATE INDEX idx_job_run_source_run
            ON job_run(source_run_id);
-- index: idx_market_events_slug
CREATE INDEX idx_market_events_slug
            ON market_events(market_slug);
-- index: idx_market_price_history_condition_recorded
CREATE INDEX idx_market_price_history_condition_recorded
                ON market_price_history(condition_id, recorded_at)
        ;
-- index: idx_market_price_history_slug_recorded
CREATE INDEX idx_market_price_history_slug_recorded
                ON market_price_history(market_slug, recorded_at)
        ;
-- index: idx_market_price_history_snapshot
CREATE INDEX idx_market_price_history_snapshot
                ON market_price_history(snapshot_id, recorded_at)
        ;
-- index: idx_market_price_history_token_recorded
CREATE INDEX idx_market_price_history_token_recorded
                ON market_price_history(token_id, recorded_at)
        ;
-- index: idx_market_topology_scope
CREATE INDEX idx_market_topology_scope
            ON market_topology_state(city_id, city_timezone, target_local_date, temperature_metric, market_family, condition_id);
-- index: idx_market_topology_status_expiry
CREATE INDEX idx_market_topology_status_expiry
            ON market_topology_state(status, expires_at);
-- index: idx_observation_instants_city_date
CREATE INDEX idx_observation_instants_city_date
            ON observation_instants(city, target_date, utc_timestamp);
-- index: idx_observation_instants_source
CREATE INDEX idx_observation_instants_source
            ON observation_instants(source, city, target_date);
-- index: idx_observation_instants_v2_city_ts
CREATE INDEX idx_observation_instants_v2_city_ts
                ON observation_instants_v2(city, target_date, utc_timestamp)
        ;
-- index: idx_observation_revisions_obs_v2_lookup
CREATE INDEX idx_observation_revisions_obs_v2_lookup
                ON observation_revisions(table_name, city, source, utc_timestamp, recorded_at)
        ;
-- index: idx_observations_city_date
CREATE INDEX idx_observations_city_date
            ON observations(city, target_date, source);
-- index: idx_order_facts_command
CREATE INDEX idx_order_facts_command ON venue_order_facts (command_id, observed_at);
-- index: idx_order_facts_state
CREATE INDEX idx_order_facts_state ON venue_order_facts (state, observed_at);
-- index: idx_platt_models_v2_lookup
CREATE INDEX idx_platt_models_v2_lookup
                ON platt_models_v2(temperature_metric, cluster, season, data_version, input_space, is_active)
        ;
-- index: idx_position_lots_state
CREATE INDEX idx_position_lots_state ON position_lots (state, position_id);
-- index: idx_position_lots_trade
CREATE INDEX idx_position_lots_trade ON position_lots (source_trade_fact_id);
-- index: idx_probability_trace_city_target
CREATE INDEX idx_probability_trace_city_target
            ON probability_trace_fact(city, target_date, recorded_at);
-- index: idx_probability_trace_market_phase
CREATE INDEX idx_probability_trace_market_phase ON probability_trace_fact(market_phase);
-- index: idx_probability_trace_phase_source
CREATE INDEX idx_probability_trace_phase_source ON probability_trace_fact(market_phase_source);
-- index: idx_probability_trace_snapshot
CREATE INDEX idx_probability_trace_snapshot
            ON probability_trace_fact(decision_snapshot_id);
-- index: idx_readiness_state_entry_scope
CREATE INDEX idx_readiness_state_entry_scope
            ON readiness_state(city_id, city_timezone, target_local_date, temperature_metric, strategy_key, market_family, condition_id);
-- index: idx_readiness_state_status_expiry
CREATE INDEX idx_readiness_state_status_expiry
            ON readiness_state(status, expires_at);
-- index: idx_refit_bucket_failures_ts
CREATE INDEX idx_refit_bucket_failures_ts
                ON refit_bucket_failures(ts)
        ;
-- index: idx_rescue_events_v2_metric_causality
CREATE INDEX idx_rescue_events_v2_metric_causality
                ON rescue_events_v2(temperature_metric, causality_status, recorded_at)
        ;
-- index: idx_rescue_events_v2_trade_time
CREATE INDEX idx_rescue_events_v2_trade_time
                ON rescue_events_v2(trade_id, recorded_at)
        ;
-- index: idx_selection_hypothesis_family
CREATE INDEX idx_selection_hypothesis_family
            ON selection_hypothesis_fact(family_id, selected_post_fdr, p_value);
-- index: idx_settlement_command_events_command
CREATE INDEX idx_settlement_command_events_command
  ON settlement_command_events (command_id, recorded_at);
-- index: idx_settlement_commands_condition
CREATE INDEX idx_settlement_commands_condition
  ON settlement_commands (condition_id, market_id);
-- index: idx_settlement_commands_state
CREATE INDEX idx_settlement_commands_state
  ON settlement_commands (state, requested_at);
-- index: idx_settlements_city_date
CREATE INDEX idx_settlements_city_date
            ON settlements(city, target_date);
-- index: idx_snapshots_condition_captured
CREATE INDEX idx_snapshots_condition_captured
          ON executable_market_snapshots (condition_id, captured_at DESC);
-- index: idx_source_contract_audit_city_date
CREATE INDEX idx_source_contract_audit_city_date
            ON source_contract_audit_events(city, target_date, temperature_metric, checked_at_utc);
-- index: idx_source_contract_audit_status
CREATE INDEX idx_source_contract_audit_status
            ON source_contract_audit_events(source_contract_status, severity, checked_at_utc);
-- index: idx_source_run_coverage_scope
CREATE INDEX idx_source_run_coverage_scope
            ON source_run_coverage(city_id, city_timezone, target_local_date, temperature_metric, source_id, source_transport, data_version);
-- index: idx_source_run_coverage_status
CREATE INDEX idx_source_run_coverage_status
            ON source_run_coverage(readiness_status, completeness_status, computed_at);
-- index: idx_source_run_scope
CREATE INDEX idx_source_run_scope
            ON source_run(city_id, city_timezone, target_local_date, temperature_metric, data_version);
-- index: idx_source_run_source_cycle
CREATE INDEX idx_source_run_source_cycle
            ON source_run(source_id, track, source_cycle_time);
-- index: idx_source_run_status
CREATE INDEX idx_source_run_status
            ON source_run(status, completeness_status, source_cycle_time);
-- index: idx_token_price_token
CREATE INDEX idx_token_price_token
            ON token_price_log(token_id, timestamp);
-- index: idx_token_suppression_history_id_time
CREATE INDEX idx_token_suppression_history_id_time
    ON token_suppression_history(token_id, history_id DESC);
-- index: idx_token_suppression_reason
CREATE INDEX idx_token_suppression_reason
    ON token_suppression(suppression_reason, updated_at);
-- index: idx_trade_facts_command
CREATE INDEX idx_trade_facts_command ON venue_trade_facts (command_id, observed_at);
-- index: idx_trade_facts_trade
CREATE INDEX idx_trade_facts_trade ON venue_trade_facts (trade_id, observed_at);
-- index: idx_uma_resolution_condition
CREATE INDEX idx_uma_resolution_condition ON uma_resolution(condition_id);
-- index: idx_validated_transfers_route
CREATE INDEX idx_validated_transfers_route
                ON validated_calibration_transfers(target_source_id, target_cycle, season, cluster, metric)
        ;
-- index: idx_venue_command_events_command
CREATE INDEX idx_venue_command_events_command ON venue_command_events(command_id);
-- index: idx_venue_command_events_type
CREATE INDEX idx_venue_command_events_type ON venue_command_events(event_type);
-- index: idx_venue_commands_decision
CREATE INDEX idx_venue_commands_decision ON venue_commands(decision_id);
-- index: idx_venue_commands_envelope
CREATE INDEX idx_venue_commands_envelope ON venue_commands(envelope_id);
-- index: idx_venue_commands_position
CREATE INDEX idx_venue_commands_position ON venue_commands(position_id);
-- index: idx_venue_commands_snapshot
CREATE INDEX idx_venue_commands_snapshot ON venue_commands(snapshot_id);
-- index: idx_venue_commands_state
CREATE INDEX idx_venue_commands_state ON venue_commands(state);
-- table: job_run
CREATE TABLE job_run (
            job_run_id TEXT PRIMARY KEY,
            job_run_key TEXT NOT NULL UNIQUE,
            job_name TEXT NOT NULL,
            plane TEXT NOT NULL CHECK (plane IN (
                'forecast','observation','solar_aux','market_topology',
                'quote','settlement_truth','source_health','hole_backfill','telemetry_control'
            )),
            scheduled_for TEXT NOT NULL,
            missed_from TEXT,
            started_at TEXT,
            finished_at TEXT,
            lock_key TEXT,
            lock_acquired_at TEXT,
            status TEXT NOT NULL CHECK (status IN (
                'RUNNING','SUCCESS','FAILED','PARTIAL','SKIPPED_NOT_RELEASED','SKIPPED_LOCK_HELD'
            )),
            reason_code TEXT,
            rows_written INTEGER NOT NULL DEFAULT 0,
            rows_failed INTEGER NOT NULL DEFAULT 0,
            source_run_id TEXT,
            source_id TEXT,
            track TEXT,
            release_calendar_key TEXT,
            safe_fetch_not_before TEXT,
            expected_scope_json TEXT NOT NULL DEFAULT '{}',
            affected_scope_json TEXT NOT NULL DEFAULT '{}',
            readiness_impacts_json TEXT NOT NULL DEFAULT '[]',
            readiness_recomputed_at TEXT,
            meta_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(job_name, scheduled_for, source_id, track)
        );
-- table: market_events
CREATE TABLE market_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            condition_id TEXT,
            token_id TEXT,
            range_label TEXT,
            range_low REAL,
            range_high REAL,
            outcome TEXT,
            created_at TEXT,
            UNIQUE(market_slug, condition_id)
        );
-- table: market_price_history
CREATE TABLE market_price_history (
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
            );
-- table: market_topology_state
CREATE TABLE market_topology_state (
            topology_id TEXT PRIMARY KEY,
            scope_key TEXT NOT NULL UNIQUE,
            market_family TEXT NOT NULL,
            event_id TEXT,
            condition_id TEXT NOT NULL,
            question_id TEXT,
            city_id TEXT,
            city_timezone TEXT,
            target_local_date TEXT,
            temperature_metric TEXT CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            physical_quantity TEXT,
            observation_field TEXT,
            data_version TEXT,
            token_ids_json TEXT NOT NULL DEFAULT '[]',
            bin_topology_hash TEXT,
            gamma_captured_at TEXT,
            gamma_updated_at TEXT,
            source_contract_status TEXT NOT NULL CHECK (source_contract_status IN (
                'MATCH','MISMATCH','UNKNOWN','QUARANTINED'
            )),
            source_contract_reason TEXT,
            authority_status TEXT NOT NULL CHECK (authority_status IN (
                'VERIFIED','STALE','EMPTY_FALLBACK','UNKNOWN'
            )),
            status TEXT NOT NULL CHECK (status IN (
                'CURRENT','STALE','EMPTY_FALLBACK','MISMATCH','UNKNOWN'
            )),
            expires_at TEXT,
            provenance_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(market_family, condition_id, city_id, target_local_date, temperature_metric, data_version)
        );
-- table: model_bias
CREATE TABLE model_bias (
            city TEXT NOT NULL,
            season TEXT NOT NULL,
            source TEXT NOT NULL,
            bias REAL NOT NULL,
            mae REAL NOT NULL,
            n_samples INTEGER NOT NULL,
            discount_factor REAL DEFAULT 0.7,
            UNIQUE(city, season, source)
        );
-- table: observation_instants
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
        );
-- table: observation_instants_v2
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
                imported_at TEXT NOT NULL,
                authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
                    CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED', 'ICAO_STATION_NATIVE')),
                data_version TEXT NOT NULL DEFAULT 'v1',
                provenance_json TEXT NOT NULL DEFAULT '{}', temperature_metric TEXT, physical_quantity TEXT, observation_field TEXT, training_allowed INTEGER DEFAULT 1, causality_status TEXT DEFAULT 'OK', source_role TEXT,
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
            );
-- table: observation_revisions
CREATE TABLE observation_revisions (
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
            );
-- table: observations
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
            high_raw_value REAL,
            high_raw_unit TEXT CHECK (high_raw_unit IN ('F', 'C', 'K')),
            high_target_unit TEXT CHECK (high_target_unit IN ('F', 'C')),
            low_raw_value REAL,
            low_raw_unit TEXT CHECK (low_raw_unit IN ('F', 'C', 'K')),
            low_target_unit TEXT CHECK (low_target_unit IN ('F', 'C')),
            -- K1 additions: temporal provenance
            high_fetch_utc TEXT,
            high_local_time TEXT,
            high_collection_window_start_utc TEXT,
            high_collection_window_end_utc TEXT,
            low_fetch_utc TEXT,
            low_local_time TEXT,
            low_collection_window_start_utc TEXT,
            low_collection_window_end_utc TEXT,
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
            high_provenance_metadata TEXT,  -- JSON
            low_provenance_metadata TEXT,  -- JSON
            UNIQUE(city, target_date, source)
        );
-- table: opportunity_fact
CREATE TABLE opportunity_fact (
    decision_id TEXT PRIMARY KEY,
    candidate_id TEXT,
    city TEXT,
    target_date TEXT,
    range_label TEXT,
    direction TEXT CHECK (direction IN ('buy_yes', 'buy_no', 'unknown')),
    strategy_key TEXT CHECK (strategy_key IN (
        'settlement_capture',
        'shoulder_sell',
        'center_buy',
        'opening_inertia'
    )),
    discovery_mode TEXT,
    entry_method TEXT,
    snapshot_id TEXT,
    p_raw REAL,
    p_cal REAL,
    p_market REAL,
    alpha REAL,
    best_edge REAL,
    ci_width REAL,
    rejection_stage TEXT,
    rejection_reason_json TEXT,
    availability_status TEXT CHECK (availability_status IN (
        'ok',
        'missing',
        'stale',
        'rate_limited',
        'unavailable',
        'chain_unavailable'
    )),
    should_trade INTEGER NOT NULL CHECK (should_trade IN (0, 1)),
    recorded_at TEXT NOT NULL
);
-- table: outcome_fact
CREATE TABLE outcome_fact (
    position_id TEXT PRIMARY KEY,
    strategy_key TEXT CHECK (strategy_key IN (
        'settlement_capture',
        'shoulder_sell',
        'center_buy',
        'opening_inertia'
    )),
    entered_at TEXT,
    exited_at TEXT,
    settled_at TEXT,
    exit_reason TEXT,
    admin_exit_reason TEXT,
    decision_snapshot_id TEXT,
    pnl REAL,
    outcome INTEGER CHECK (outcome IN (0, 1)),
    hold_duration_hours REAL,
    monitor_count INTEGER,
    chain_corrections_count INTEGER
);
-- table: platt_models
CREATE TABLE platt_models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bucket_key TEXT NOT NULL UNIQUE,
            param_A REAL NOT NULL,
            param_B REAL NOT NULL,
            param_C REAL NOT NULL DEFAULT 0.0,
            bootstrap_params_json TEXT NOT NULL,
            n_samples INTEGER NOT NULL,
            brier_insample REAL,
            fitted_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            input_space TEXT NOT NULL DEFAULT 'raw_probability',
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED' CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED'))
        );
-- table: platt_models_v2
CREATE TABLE platt_models_v2 (
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
            );
-- table: position_current
CREATE TABLE position_current (
    position_id TEXT PRIMARY KEY,
    phase TEXT NOT NULL CHECK (phase IN (
        'pending_entry',
        'active',
        'day0_window',
        'pending_exit',
        'economically_closed',
        'settled',
        'voided',
        'quarantined',
        'admin_closed'
    )),
    trade_id TEXT,
    market_id TEXT,
    city TEXT,
    cluster TEXT,
    target_date TEXT,
    bin_label TEXT,
    direction TEXT CHECK (direction IS NULL OR direction IN ('buy_yes', 'buy_no', 'unknown')),
    unit TEXT CHECK (unit IS NULL OR unit IN ('F', 'C')),
    size_usd REAL,
    shares REAL,
    cost_basis_usd REAL,
    entry_price REAL,
    p_posterior REAL,
    last_monitor_prob REAL,
    last_monitor_edge REAL,
    last_monitor_market_price REAL,
    decision_snapshot_id TEXT,
    entry_method TEXT,
    strategy_key TEXT NOT NULL CHECK (strategy_key IN (
        'settlement_capture',
        'shoulder_sell',
        'center_buy',
        'opening_inertia'
    )),
    edge_source TEXT,
    discovery_mode TEXT,
    chain_state TEXT,
    token_id TEXT,
    no_token_id TEXT,
    condition_id TEXT,
    order_id TEXT,
    order_status TEXT,
    updated_at TEXT NOT NULL,
    temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low'))
);
-- table: position_events
CREATE TABLE position_events (
    event_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    event_version INTEGER NOT NULL DEFAULT 1 CHECK (event_version >= 1),
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 1),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'POSITION_OPEN_INTENT',
        'ENTRY_ORDER_POSTED',
        'ENTRY_ORDER_FILLED',
        'ENTRY_ORDER_VOIDED',
        'ENTRY_ORDER_REJECTED',
        'DAY0_WINDOW_ENTERED',
        'CHAIN_SYNCED',
        'CHAIN_SIZE_CORRECTED',
        'CHAIN_QUARANTINED',
        'MONITOR_REFRESHED',
        'EXIT_INTENT',
        'EXIT_ORDER_POSTED',
        'EXIT_ORDER_FILLED',
        'EXIT_ORDER_VOIDED',
        'EXIT_ORDER_REJECTED',
        'SETTLED',
        'ADMIN_VOIDED',
        'MANUAL_OVERRIDE_APPLIED'
    )),
    occurred_at TEXT NOT NULL,
    phase_before TEXT CHECK (phase_before IS NULL OR phase_before IN (
        'pending_entry',
        'active',
        'day0_window',
        'pending_exit',
        'economically_closed',
        'settled',
        'voided',
        'quarantined',
        'admin_closed'
    )),
    phase_after TEXT CHECK (phase_after IS NULL OR phase_after IN (
        'pending_entry',
        'active',
        'day0_window',
        'pending_exit',
        'economically_closed',
        'settled',
        'voided',
        'quarantined',
        'admin_closed'
    )),
    strategy_key TEXT NOT NULL CHECK (strategy_key IN (
        'settlement_capture',
        'shoulder_sell',
        'center_buy',
        'opening_inertia'
    )),
    decision_id TEXT,
    snapshot_id TEXT,
    order_id TEXT,
    command_id TEXT,
    caused_by TEXT,
    idempotency_key TEXT UNIQUE,
    venue_status TEXT,
    source_module TEXT NOT NULL,
    env TEXT NOT NULL CHECK (env IN ('live','test','replay','backtest','shadow')),
    payload_json TEXT NOT NULL,
    UNIQUE(position_id, sequence_no)
);
-- table: position_lots
CREATE TABLE position_lots (
          lot_id INTEGER PRIMARY KEY AUTOINCREMENT,
          position_id INTEGER NOT NULL,
          state TEXT NOT NULL CHECK (state IN (
            'OPTIMISTIC_EXPOSURE','CONFIRMED_EXPOSURE',
            'EXIT_PENDING','ECONOMICALLY_CLOSED_OPTIMISTIC',
            'ECONOMICALLY_CLOSED_CONFIRMED','SETTLED','QUARANTINED'
          )),
          shares TEXT NOT NULL,
          entry_price_avg TEXT NOT NULL,
          exit_price_avg TEXT,
          source_command_id TEXT REFERENCES venue_commands(command_id),
          source_trade_fact_id INTEGER REFERENCES venue_trade_facts(trade_fact_id),
          captured_at TEXT NOT NULL,
          state_changed_at TEXT NOT NULL,
          source TEXT NOT NULL CHECK (source IN ('REST','WS_USER','WS_MARKET','DATA_API','CHAIN','OPERATOR','FAKE_VENUE')),
          observed_at TEXT NOT NULL,
          venue_timestamp TEXT,
          ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          local_sequence INTEGER NOT NULL,
          raw_payload_hash TEXT NOT NULL,
          raw_payload_json TEXT,
          UNIQUE (position_id, local_sequence)
        );
-- table: probability_trace_fact
CREATE TABLE probability_trace_fact (
            trace_id TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL UNIQUE,
            decision_snapshot_id TEXT,
            candidate_id TEXT,
            city TEXT,
            target_date TEXT,
            range_label TEXT,
            direction TEXT CHECK (direction IN ('buy_yes', 'buy_no', 'unknown')),
            mode TEXT,
            strategy_key TEXT CHECK (strategy_key IN (
                'settlement_capture',
                'shoulder_sell',
                'center_buy',
                'opening_inertia'
            )),
            discovery_mode TEXT,
            entry_method TEXT,
            selected_method TEXT,
            trace_status TEXT NOT NULL CHECK (trace_status IN (
                'complete',
                'degraded_decision_context',
                'degraded_missing_vectors',
                'pre_vector_unavailable'
            )),
            missing_reason_json TEXT NOT NULL DEFAULT '[]',
            bin_labels_json TEXT,
            p_raw_json TEXT,
            p_cal_json TEXT,
            p_market_json TEXT,
            p_posterior_json TEXT,
            p_posterior REAL,
            alpha REAL,
            agreement TEXT,
            n_edges_found INTEGER,
            n_edges_after_fdr INTEGER,
            rejection_stage TEXT,
            availability_status TEXT,
            -- P2 (PLAN_v3 §6.P2 stage 3): MarketPhase axis A tag for
            -- decision-time cohort attribution. Additive, default NULL
            -- for legacy rows; legacy-DB ALTER TABLE migration below.
            market_phase TEXT,
            -- A5 (PLAN.md §A5 + Bug review Finding F): MarketPhaseEvidence
            -- provenance fields. ``market_phase_source`` distinguishes
            -- verified_gamma / fallback_f1 / onchain_resolved / unknown so
            -- attribution reports can stratify by determination quality.
            -- The 3 timestamp columns capture WHICH boundaries the phase
            -- was computed against — so a future cohort report can detect
            -- a midnight-straddle drift without re-running the cycle.
            -- ``uma_resolved_source`` carries the on-chain Settle tx hash
            -- when phase_source == "onchain_resolved", NULL otherwise.
            market_phase_source TEXT,
            market_start_at TEXT,
            market_end_at TEXT,
            settlement_day_entry_utc TEXT,
            uma_resolved_source TEXT,
            recorded_at TEXT NOT NULL
        );
-- table: provenance_envelope_events
CREATE TABLE provenance_envelope_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          subject_type TEXT NOT NULL CHECK (subject_type IN ('command','order','trade','lot','settlement','wrap_unwrap','heartbeat')),
          subject_id TEXT NOT NULL,
          event_type TEXT NOT NULL,
          payload_hash TEXT NOT NULL,
          payload_json TEXT,
          source TEXT NOT NULL CHECK (source IN ('REST','WS_USER','WS_MARKET','DATA_API','CHAIN','OPERATOR','FAKE_VENUE')),
          observed_at TEXT NOT NULL,
          venue_timestamp TEXT,
          ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          local_sequence INTEGER NOT NULL,
          UNIQUE (subject_type, subject_id, local_sequence)
        );
-- table: readiness_state
CREATE TABLE readiness_state (
            readiness_id TEXT PRIMARY KEY,
            scope_key TEXT NOT NULL UNIQUE,
            scope_type TEXT NOT NULL CHECK (scope_type IN (
                'global','source','city_metric','market','strategy','quote'
            )),
            city_id TEXT,
            city TEXT,
            city_timezone TEXT,
            target_local_date TEXT,
            metric TEXT,
            temperature_metric TEXT CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            physical_quantity TEXT,
            observation_field TEXT,
            data_version TEXT,
            source_id TEXT,
            track TEXT,
            source_run_id TEXT,
            market_family TEXT,
            event_id TEXT,
            condition_id TEXT,
            token_ids_json TEXT NOT NULL DEFAULT '[]',
            strategy_key TEXT,
            status TEXT NOT NULL CHECK (status IN (
                'LIVE_ELIGIBLE','SHADOW_ONLY','BLOCKED','DEGRADED_LOG_ONLY','UNKNOWN_BLOCKED'
            )),
            reason_codes_json TEXT NOT NULL DEFAULT '[]',
            computed_at TEXT NOT NULL,
            expires_at TEXT,
            dependency_json TEXT NOT NULL DEFAULT '{}',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(
                scope_type, city_id, city_timezone, target_local_date,
                temperature_metric, physical_quantity, observation_field,
                data_version, strategy_key, market_family, source_id, track,
                condition_id
            )
        );
-- table: refit_bucket_failures
CREATE TABLE refit_bucket_failures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cluster TEXT NOT NULL,
                season TEXT NOT NULL,
                cycle TEXT,
                source_id TEXT,
                error_class TEXT NOT NULL,
                error_text TEXT NOT NULL,
                ts TEXT NOT NULL
            );
-- table: rescue_events_v2
CREATE TABLE rescue_events_v2 (
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
            );
-- table: risk_actions
CREATE TABLE risk_actions (
    action_id TEXT PRIMARY KEY,
    strategy_key TEXT NOT NULL CHECK (strategy_key IN (
        'settlement_capture',
        'shoulder_sell',
        'center_buy',
        'opening_inertia'
    )),
    action_type TEXT NOT NULL CHECK (action_type IN (
        'gate',
        'allocation_multiplier',
        'threshold_multiplier',
        'exit_only'
    )),
    value TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    effective_until TEXT,
    reason TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('riskguard', 'manual', 'system')),
    precedence INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'expired', 'revoked'))
);
-- table: selection_family_fact
CREATE TABLE selection_family_fact (
            family_id TEXT PRIMARY KEY,
            cycle_mode TEXT NOT NULL,
            decision_snapshot_id TEXT,
            city TEXT,
            target_date TEXT,
            strategy_key TEXT,
            discovery_mode TEXT,
            created_at TEXT NOT NULL,
            meta_json TEXT NOT NULL,
            decision_time_status TEXT
        );
-- table: selection_hypothesis_fact
CREATE TABLE selection_hypothesis_fact (
            hypothesis_id TEXT PRIMARY KEY,
            family_id TEXT NOT NULL,
            decision_id TEXT,
            candidate_id TEXT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            range_label TEXT NOT NULL,
            direction TEXT NOT NULL CHECK (direction IN ('buy_yes', 'buy_no', 'unknown')),
            p_value REAL,
            q_value REAL,
            ci_lower REAL,
            ci_upper REAL,
            edge REAL,
            tested INTEGER NOT NULL DEFAULT 1 CHECK (tested IN (0, 1)),
            passed_prefilter INTEGER NOT NULL DEFAULT 0 CHECK (passed_prefilter IN (0, 1)),
            selected_post_fdr INTEGER NOT NULL DEFAULT 0 CHECK (selected_post_fdr IN (0, 1)),
            rejection_stage TEXT,
            recorded_at TEXT NOT NULL,
            meta_json TEXT NOT NULL,
            FOREIGN KEY(family_id) REFERENCES selection_family_fact(family_id)
        );
-- table: settlement_command_events
CREATE TABLE settlement_command_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  command_id TEXT NOT NULL REFERENCES settlement_commands(command_id),
  event_type TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  payload_json TEXT,
  recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- table: settlement_commands
CREATE TABLE settlement_commands (
  command_id TEXT PRIMARY KEY,
  state TEXT NOT NULL CHECK (state IN (
    'REDEEM_INTENT_CREATED','REDEEM_SUBMITTED','REDEEM_TX_HASHED',
    'REDEEM_CONFIRMED','REDEEM_FAILED','REDEEM_RETRYING','REDEEM_REVIEW_REQUIRED'
  )),
  condition_id TEXT NOT NULL,
  market_id TEXT NOT NULL,
  payout_asset TEXT NOT NULL CHECK (payout_asset IN ('pUSD','USDC','USDC_E')),
  pusd_amount_micro INTEGER,
  token_amounts_json TEXT,
  tx_hash TEXT,
  block_number INTEGER,
  confirmation_count INTEGER DEFAULT 0,
  requested_at TEXT NOT NULL,
  submitted_at TEXT,
  terminal_at TEXT,
  error_payload TEXT
);
-- table: settlements
CREATE TABLE settlements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            market_slug TEXT,
            winning_bin TEXT,
            settlement_value REAL,
            settlement_source TEXT,
            settled_at TEXT,
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED' CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
            pm_bin_lo REAL,
            pm_bin_hi REAL,
            unit TEXT,
            settlement_source_type TEXT,
            -- REOPEN-2 inline: INV-14 identity spine is part of the fresh-DB
            -- schema so UNIQUE(city, target_date, temperature_metric) can
            -- reference temperature_metric without a second migration pass.
            -- Legacy DBs that predate these columns get them via the ALTER
            -- loop below, and their UNIQUE constraint is upgraded via the
            -- REOPEN-2 table-rebuild migration that runs between the ALTERs
            -- and the trigger reinstall.
            temperature_metric TEXT
                CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            physical_quantity TEXT,
            observation_field TEXT
                CHECK (observation_field IS NULL OR observation_field IN ('high_temp','low_temp')),
            data_version TEXT,
            provenance_json TEXT,
            UNIQUE(city, target_date, temperature_metric)
        );
-- table: shadow_signals
CREATE TABLE shadow_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            decision_snapshot_id TEXT,
            p_raw_json TEXT NOT NULL,
            p_cal_json TEXT,
            edges_json TEXT,
            lead_hours REAL NOT NULL
        );
-- table: solar_daily
CREATE TABLE solar_daily (
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            timezone TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            sunrise_local TEXT NOT NULL,
            sunset_local TEXT NOT NULL,
            sunrise_utc TEXT NOT NULL,
            sunset_utc TEXT NOT NULL,
            utc_offset_minutes INTEGER NOT NULL,
            dst_active INTEGER NOT NULL,
            UNIQUE(city, target_date)
        );
-- table: source_contract_audit_events
CREATE TABLE source_contract_audit_events (
            audit_id TEXT PRIMARY KEY,
            checked_at_utc TEXT NOT NULL,
            scan_authority TEXT NOT NULL CHECK (scan_authority IN (
                'VERIFIED','FIXTURE','STALE_CACHE','EMPTY_FALLBACK','NEVER_FETCHED'
            )),
            report_status TEXT CHECK (report_status IS NULL OR report_status IN (
                'OK','WARN','ALERT','DATA_UNAVAILABLE'
            )),
            severity TEXT NOT NULL CHECK (severity IN ('OK','WARN','ALERT','DATA_UNAVAILABLE')),
            event_id TEXT,
            slug TEXT,
            title TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            source_contract_status TEXT NOT NULL CHECK (source_contract_status IN (
                'MATCH','MISSING','AMBIGUOUS','MISMATCH','UNSUPPORTED','UNKNOWN','QUARANTINED'
            )),
            source_contract_reason TEXT,
            configured_source_family TEXT,
            configured_station_id TEXT,
            observed_source_family TEXT,
            observed_station_id TEXT,
            resolution_sources_json TEXT NOT NULL DEFAULT '[]',
            source_contract_json TEXT NOT NULL DEFAULT '{}',
            payload_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
-- table: source_run
CREATE TABLE source_run (
            source_run_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            track TEXT NOT NULL,
            release_calendar_key TEXT NOT NULL,
            ingest_mode TEXT NOT NULL CHECK (ingest_mode IN (
                'SCHEDULED_LIVE','BOOT_CATCHUP','HOLE_BACKFILL','ARCHIVE_BACKFILL'
            )),
            origin_mode TEXT NOT NULL CHECK (origin_mode IN (
                'SCHEDULED_LIVE','BOOT_CATCHUP','HOLE_BACKFILL','ARCHIVE_BACKFILL'
            )),
            source_cycle_time TEXT NOT NULL,
            source_issue_time TEXT,
            source_release_time TEXT,
            source_available_at TEXT,
            fetch_started_at TEXT,
            fetch_finished_at TEXT,
            captured_at TEXT,
            imported_at TEXT,
            valid_time_start TEXT,
            valid_time_end TEXT,
            target_local_date TEXT,
            city_id TEXT,
            city_timezone TEXT,
            temperature_metric TEXT CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            physical_quantity TEXT,
            observation_field TEXT,
            data_version TEXT,
            expected_members INTEGER,
            observed_members INTEGER,
            expected_steps_json TEXT NOT NULL DEFAULT '[]',
            observed_steps_json TEXT NOT NULL DEFAULT '[]',
            expected_count INTEGER,
            observed_count INTEGER,
            completeness_status TEXT NOT NULL CHECK (completeness_status IN (
                'COMPLETE','PARTIAL','MISSING','NOT_RELEASED'
            )),
            partial_run INTEGER NOT NULL DEFAULT 0 CHECK (partial_run IN (0,1)),
            raw_payload_hash TEXT,
            manifest_hash TEXT,
            status TEXT NOT NULL CHECK (status IN (
                'RUNNING','SUCCESS','FAILED','PARTIAL','SKIPPED_NOT_RELEASED'
            )),
            reason_code TEXT,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (partial_run = 0 OR completeness_status = 'PARTIAL')
        );
-- table: source_run_coverage
CREATE TABLE source_run_coverage (
            coverage_id TEXT PRIMARY KEY,
            source_run_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_transport TEXT NOT NULL,
            release_calendar_key TEXT NOT NULL,
            track TEXT NOT NULL,
            city_id TEXT NOT NULL,
            city TEXT NOT NULL,
            city_timezone TEXT NOT NULL,
            target_local_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high','low')),
            physical_quantity TEXT NOT NULL,
            observation_field TEXT NOT NULL,
            data_version TEXT NOT NULL,
            expected_members INTEGER NOT NULL,
            observed_members INTEGER NOT NULL,
            expected_steps_json TEXT NOT NULL,
            observed_steps_json TEXT NOT NULL,
            snapshot_ids_json TEXT NOT NULL DEFAULT '[]',
            target_window_start_utc TEXT NOT NULL,
            target_window_end_utc TEXT NOT NULL,
            completeness_status TEXT NOT NULL CHECK (completeness_status IN (
                'COMPLETE','PARTIAL','MISSING','HORIZON_OUT_OF_RANGE','NOT_RELEASED'
            )),
            readiness_status TEXT NOT NULL CHECK (readiness_status IN (
                'LIVE_ELIGIBLE','SHADOW_ONLY','BLOCKED','UNKNOWN_BLOCKED'
            )),
            reason_code TEXT,
            computed_at TEXT NOT NULL,
            expires_at TEXT,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(
                source_run_id, source_id, source_transport, release_calendar_key,
                track, city_id, city_timezone, target_local_date,
                temperature_metric, data_version
            )
        );
-- table: sqlite_sequence
CREATE TABLE sqlite_sequence(name,seq);
-- table: strategy_health
CREATE TABLE strategy_health (
            strategy_key TEXT NOT NULL CHECK (strategy_key IN (
                'settlement_capture',
                'shoulder_sell',
                'center_buy',
                'opening_inertia'
            )),
            as_of TEXT NOT NULL,
            open_exposure_usd REAL NOT NULL DEFAULT 0,
            settled_trades_30d INTEGER NOT NULL DEFAULT 0,
            realized_pnl_30d REAL NOT NULL DEFAULT 0,
            unrealized_pnl REAL NOT NULL DEFAULT 0,
            win_rate_30d REAL,
            brier_30d REAL,
            fill_rate_14d REAL,
            edge_trend_30d REAL,
            risk_level TEXT,
            execution_decay_flag INTEGER NOT NULL DEFAULT 0 CHECK (execution_decay_flag IN (0, 1)),
            edge_compression_flag INTEGER NOT NULL DEFAULT 0 CHECK (edge_compression_flag IN (0, 1)),
            PRIMARY KEY (strategy_key, as_of)
        );
-- table: temp_persistence
CREATE TABLE temp_persistence (
            city TEXT NOT NULL,
            season TEXT NOT NULL,
            delta_bucket TEXT NOT NULL,
            frequency REAL NOT NULL,
            avg_next_day_reversion REAL,
            n_samples INTEGER NOT NULL,
            UNIQUE(city, season, delta_bucket)
        );
-- table: token_price_log
CREATE TABLE token_price_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_id TEXT NOT NULL,
            city TEXT,
            target_date TEXT,
            range_label TEXT,
            price REAL NOT NULL,
            volume REAL,
            bid REAL,
            ask REAL,
            spread REAL,
            source_timestamp TEXT,
            timestamp TEXT NOT NULL
        );
-- table: token_suppression
CREATE TABLE token_suppression (
    token_id TEXT PRIMARY KEY,
    condition_id TEXT,
    suppression_reason TEXT NOT NULL CHECK (suppression_reason IN (
        'operator_quarantine_clear',
        'chain_only_quarantined',
        'settled_position'
    )),
    source_module TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}'
);
-- table: token_suppression_history
CREATE TABLE token_suppression_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id TEXT NOT NULL,
    condition_id TEXT,
    suppression_reason TEXT NOT NULL CHECK (suppression_reason IN (
        'operator_quarantine_clear',
        'chain_only_quarantined',
        'settled_position'
    )),
    source_module TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    operation TEXT NOT NULL DEFAULT 'record' CHECK (operation IN ('record', 'migrated')),
    recorded_at TEXT NOT NULL
);
-- table: trade_decisions
CREATE TABLE trade_decisions (
            trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            bin_label TEXT NOT NULL,
            direction TEXT NOT NULL,
            size_usd REAL NOT NULL,
            price REAL NOT NULL,
            timestamp TEXT NOT NULL,
            forecast_snapshot_id INTEGER REFERENCES ensemble_snapshots(snapshot_id),
            calibration_model_version TEXT,
            p_raw REAL NOT NULL,
            p_calibrated REAL,
            p_posterior REAL NOT NULL,
            edge REAL NOT NULL,
            ci_lower REAL NOT NULL,
            ci_upper REAL NOT NULL,
            kelly_fraction REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            filled_at TEXT,
            fill_price REAL,
            runtime_trade_id TEXT,
            order_id TEXT,
            order_status_text TEXT,
            order_posted_at TEXT,
            entered_at_ts TEXT,
            chain_state TEXT,
            -- Attribution fields (CLAUDE.md: mandatory on every trade)
            strategy TEXT,
            edge_source TEXT,
            bin_type TEXT,
            discovery_mode TEXT,
            market_hours_open REAL,
            fill_quality REAL,
            entry_method TEXT,
            selected_method TEXT,
            applied_validations_json TEXT,
            exit_trigger TEXT,
            exit_reason TEXT,
            admin_exit_reason TEXT,
            exit_divergence_score REAL DEFAULT 0.0,
            exit_market_velocity_1h REAL DEFAULT 0.0,
            exit_forward_edge REAL DEFAULT 0.0,
            -- Phase 2 Domain Object Snapshots (JSON flattened blobs)
            settlement_semantics_json TEXT,
            epistemic_context_json TEXT,
            edge_context_json TEXT,
            -- Phase 3: Shadow Proof True Attribution
            entry_alpha_usd REAL DEFAULT 0.0,
            execution_slippage_usd REAL DEFAULT 0.0,
            exit_timing_usd REAL DEFAULT 0.0,
            risk_throttling_usd REAL DEFAULT 0.0,
            settlement_edge_usd REAL DEFAULT 0.0
        , env TEXT NOT NULL DEFAULT 'live');
-- table: uma_resolution
CREATE TABLE uma_resolution (
            condition_id TEXT NOT NULL,
            tx_hash TEXT NOT NULL,
            block_number INTEGER NOT NULL,
            resolved_value INTEGER NOT NULL,
            resolved_at_utc TEXT NOT NULL,
            raw_log_json TEXT NOT NULL,
            observed_at_utc TEXT NOT NULL,
            PRIMARY KEY (condition_id, tx_hash)
        );
-- index: ux_daily_observation_revisions_payload
CREATE UNIQUE INDEX ux_daily_observation_revisions_payload
            ON daily_observation_revisions(
                city, target_date, source, incoming_combined_payload_hash, reason
            );
-- index: ux_findings_unresolved_subject
CREATE UNIQUE INDEX ux_findings_unresolved_subject
          ON exchange_reconcile_findings (kind, subject_id, context)
          WHERE resolved_at IS NULL;
-- index: ux_observation_revisions_payload
CREATE UNIQUE INDEX ux_observation_revisions_payload
                ON observation_revisions(
                    table_name, city, source, target_date, utc_timestamp,
                    incoming_payload_hash, reason
                )
        ;
-- index: ux_settlement_commands_active_condition_asset
CREATE UNIQUE INDEX ux_settlement_commands_active_condition_asset
  ON settlement_commands (condition_id, market_id, payout_asset)
  WHERE state NOT IN ('REDEEM_CONFIRMED','REDEEM_FAILED');
-- table: validated_calibration_transfers
CREATE TABLE validated_calibration_transfers (
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
            );
-- table: venue_command_events
CREATE TABLE venue_command_events (
            event_id TEXT PRIMARY KEY,
            command_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            payload_json TEXT,
            state_after TEXT NOT NULL,
            UNIQUE (command_id, sequence_no)
        );
-- table: venue_commands
CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            -- U1 (INV-NEW-E): every persisted venue command cites an
            -- executable-market snapshot. Freshness/tradability are enforced
            -- in src/state/venue_command_repo.py because they depend on now().
            snapshot_id TEXT NOT NULL,
            -- U2 (INV-NEW-F): every venue command cites a pre-side-effect
            -- submission provenance envelope.
            envelope_id TEXT NOT NULL,
            -- Identity
            position_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            intent_kind TEXT NOT NULL,
            -- Order shape
            market_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            side TEXT NOT NULL,
            size REAL NOT NULL,
            price REAL NOT NULL,
            -- Venue identity (NULL until first ACK)
            venue_order_id TEXT,
            -- Lifecycle
            state TEXT NOT NULL,
            last_event_id TEXT,
            -- Timestamps
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            -- Optional review
            review_required_reason TEXT
        );
-- table: venue_order_facts
CREATE TABLE venue_order_facts (
          fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
          venue_order_id TEXT NOT NULL,
          command_id TEXT NOT NULL REFERENCES venue_commands(command_id),
          state TEXT NOT NULL CHECK (state IN (
            'LIVE','RESTING','MATCHED','PARTIALLY_MATCHED',
            'CANCEL_REQUESTED','CANCEL_CONFIRMED','CANCEL_UNKNOWN','CANCEL_FAILED',
            'EXPIRED','VENUE_WIPED','HEARTBEAT_CANCEL_SUSPECTED'
          )),
          remaining_size TEXT,
          matched_size TEXT,
          source TEXT NOT NULL CHECK (source IN ('REST','WS_USER','WS_MARKET','DATA_API','CHAIN','OPERATOR','FAKE_VENUE')),
          observed_at TEXT NOT NULL,
          venue_timestamp TEXT,
          ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          local_sequence INTEGER NOT NULL,
          raw_payload_hash TEXT NOT NULL,
          raw_payload_json TEXT,
          UNIQUE (venue_order_id, local_sequence)
        );
-- table: venue_submission_envelopes
CREATE TABLE venue_submission_envelopes (
          envelope_id TEXT PRIMARY KEY,
          schema_version INTEGER NOT NULL DEFAULT 1,
          sdk_package TEXT NOT NULL,
          sdk_version TEXT NOT NULL,
          host TEXT NOT NULL,
          chain_id INTEGER NOT NULL,
          funder_address TEXT NOT NULL,
          condition_id TEXT NOT NULL,
          question_id TEXT NOT NULL,
          yes_token_id TEXT NOT NULL,
          no_token_id TEXT NOT NULL,
          selected_outcome_token_id TEXT NOT NULL,
          outcome_label TEXT NOT NULL CHECK (outcome_label IN ('YES','NO')),
          side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
          price TEXT NOT NULL,
          size TEXT NOT NULL,
          order_type TEXT NOT NULL CHECK (order_type IN ('GTC','GTD','FOK','FAK')),
          post_only INTEGER NOT NULL CHECK (post_only IN (0,1)),
          tick_size TEXT NOT NULL,
          min_order_size TEXT NOT NULL,
          neg_risk INTEGER NOT NULL CHECK (neg_risk IN (0,1)),
          fee_details_json TEXT NOT NULL,
          canonical_pre_sign_payload_hash TEXT NOT NULL,
          signed_order_blob BLOB,
          signed_order_hash TEXT,
          raw_request_hash TEXT NOT NULL,
          raw_response_json TEXT,
          order_id TEXT,
          trade_ids_json TEXT NOT NULL DEFAULT '[]',
          transaction_hashes_json TEXT NOT NULL DEFAULT '[]',
          error_code TEXT,
          error_message TEXT,
          captured_at TEXT NOT NULL
        );
-- table: venue_trade_facts
CREATE TABLE venue_trade_facts (
          trade_fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
          trade_id TEXT NOT NULL,
          venue_order_id TEXT NOT NULL,
          command_id TEXT NOT NULL REFERENCES venue_commands(command_id),
          state TEXT NOT NULL CHECK (state IN ('MATCHED','MINED','CONFIRMED','RETRYING','FAILED')),
          filled_size TEXT NOT NULL,
          fill_price TEXT NOT NULL,
          fee_paid_micro INTEGER,
          tx_hash TEXT,
          block_number INTEGER,
          confirmation_count INTEGER DEFAULT 0,
          source TEXT NOT NULL CHECK (source IN ('REST','WS_USER','WS_MARKET','DATA_API','CHAIN','OPERATOR','FAKE_VENUE')),
          observed_at TEXT NOT NULL,
          venue_timestamp TEXT,
          ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          local_sequence INTEGER NOT NULL,
          raw_payload_hash TEXT NOT NULL,
          raw_payload_json TEXT,
          UNIQUE (trade_id, local_sequence)
        );
-- table: wrap_unwrap_commands
CREATE TABLE wrap_unwrap_commands (
          command_id TEXT PRIMARY KEY,
          state TEXT NOT NULL,
          direction TEXT NOT NULL CHECK (direction IN ('WRAP','UNWRAP')),
          amount_micro INTEGER NOT NULL,
          tx_hash TEXT,
          block_number INTEGER,
          confirmation_count INTEGER DEFAULT 0,
          requested_at TEXT NOT NULL,
          terminal_at TEXT,
          error_payload TEXT
        );
-- table: wrap_unwrap_events
CREATE TABLE wrap_unwrap_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          command_id TEXT NOT NULL REFERENCES wrap_unwrap_commands(command_id),
          event_type TEXT NOT NULL,
          payload_json TEXT,
          recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
-- table: zeus_meta
CREATE TABLE zeus_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

-- === FORECASTS DB (init_schema_forecasts) ===
-- table: calibration_pairs_v2
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
            cycle TEXT NOT NULL DEFAULT '00',
            source_id TEXT NOT NULL DEFAULT 'tigge_mars',
            horizon_profile TEXT NOT NULL DEFAULT 'full',
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(city, target_date, temperature_metric, range_label, lead_days,
                   forecast_available_at, bin_source, data_version)
        );
-- table: ensemble_snapshots_v2
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
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, members_unit TEXT NOT NULL DEFAULT 'degC', members_precision REAL, local_day_start_utc TEXT, step_horizon_hours REAL, unit TEXT,
            UNIQUE(city, target_date, temperature_metric, issue_time, data_version)
        );
-- index: idx_calibration_pairs_v2_bucket
CREATE INDEX idx_calibration_pairs_v2_bucket
            ON calibration_pairs_v2(temperature_metric, cluster, season, lead_days)
    ;
-- index: idx_calibration_pairs_v2_city_date_metric
CREATE INDEX idx_calibration_pairs_v2_city_date_metric
            ON calibration_pairs_v2(city, target_date, temperature_metric)
    ;
-- index: idx_calibration_pairs_v2_refit_core
CREATE INDEX idx_calibration_pairs_v2_refit_core
            ON calibration_pairs_v2(temperature_metric, data_version, training_allowed, authority)
    ;
-- index: idx_ens_v2_entry_lookup
CREATE INDEX idx_ens_v2_entry_lookup
            ON ensemble_snapshots_v2(
                city,
                target_date,
                temperature_metric,
                source_id,
                source_transport,
                data_version,
                source_run_id
            )
    ;
-- index: idx_ens_v2_source_run
CREATE INDEX idx_ens_v2_source_run
            ON ensemble_snapshots_v2(source_id, source_transport, source_run_id)
    ;
-- index: idx_ensemble_snapshots_v2_lookup
CREATE INDEX idx_ensemble_snapshots_v2_lookup
            ON ensemble_snapshots_v2(city, target_date, temperature_metric, available_at)
    ;
-- index: idx_market_events_v2_city_date_metric
CREATE INDEX idx_market_events_v2_city_date_metric
            ON market_events_v2(city, target_date, temperature_metric)
    ;
-- index: idx_market_events_v2_open
CREATE INDEX idx_market_events_v2_open
            ON market_events_v2(city, target_date, temperature_metric)
            WHERE outcome IS NULL
    ;
-- index: idx_observations_city_date
CREATE INDEX idx_observations_city_date
            ON observations(city, target_date, source)
    ;
-- index: idx_settlements_city_date
CREATE INDEX idx_settlements_city_date
            ON settlements(city, target_date)
    ;
-- index: idx_settlements_v2_city_date_metric
CREATE INDEX idx_settlements_v2_city_date_metric
            ON settlements_v2(city, target_date, temperature_metric)
    ;
-- index: idx_settlements_v2_settled_at
CREATE INDEX idx_settlements_v2_settled_at
            ON settlements_v2(settled_at)
    ;
-- index: idx_source_run_scope
CREATE INDEX idx_source_run_scope
            ON source_run(city_id, city_timezone, target_local_date,
                          temperature_metric, data_version)
    ;
-- index: idx_source_run_source_cycle
CREATE INDEX idx_source_run_source_cycle
            ON source_run(source_id, track, source_cycle_time)
    ;
-- index: idx_source_run_status
CREATE INDEX idx_source_run_status
            ON source_run(status, completeness_status, source_cycle_time)
    ;
-- table: market_events_v2
CREATE TABLE market_events_v2 (
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
        );
-- table: observations
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
            high_raw_value REAL,
            high_raw_unit TEXT CHECK (high_raw_unit IN ('F', 'C', 'K')),
            high_target_unit TEXT CHECK (high_target_unit IN ('F', 'C')),
            low_raw_value REAL,
            low_raw_unit TEXT CHECK (low_raw_unit IN ('F', 'C', 'K')),
            low_target_unit TEXT CHECK (low_target_unit IN ('F', 'C')),
            high_fetch_utc TEXT,
            high_local_time TEXT,
            high_collection_window_start_utc TEXT,
            high_collection_window_end_utc TEXT,
            low_fetch_utc TEXT,
            low_local_time TEXT,
            low_collection_window_start_utc TEXT,
            low_collection_window_end_utc TEXT,
            timezone TEXT,
            utc_offset_minutes INTEGER,
            dst_active INTEGER CHECK (dst_active IN (0, 1)),
            is_ambiguous_local_hour INTEGER CHECK (is_ambiguous_local_hour IN (0, 1)),
            is_missing_local_hour INTEGER CHECK (is_missing_local_hour IN (0, 1)),
            hemisphere TEXT CHECK (hemisphere IN ('N', 'S')),
            season TEXT CHECK (season IN ('DJF', 'MAM', 'JJA', 'SON')),
            month INTEGER CHECK (month BETWEEN 1 AND 12),
            rebuild_run_id TEXT,
            data_source_version TEXT,
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
                CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
            high_provenance_metadata TEXT,
            low_provenance_metadata TEXT,
            UNIQUE(city, target_date, source)
        );
-- table: settlements
CREATE TABLE settlements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            market_slug TEXT,
            winning_bin TEXT,
            settlement_value REAL,
            settlement_source TEXT,
            settled_at TEXT,
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
                CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
            pm_bin_lo REAL,
            pm_bin_hi REAL,
            unit TEXT,
            settlement_source_type TEXT,
            temperature_metric TEXT
                CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            physical_quantity TEXT,
            observation_field TEXT
                CHECK (observation_field IS NULL OR observation_field IN ('high_temp','low_temp')),
            data_version TEXT,
            provenance_json TEXT,
            UNIQUE(city, target_date, temperature_metric)
        );
-- table: settlements_v2
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
        );
-- table: source_run
CREATE TABLE source_run (
            source_run_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            track TEXT NOT NULL,
            release_calendar_key TEXT NOT NULL,
            ingest_mode TEXT NOT NULL CHECK (ingest_mode IN (
                'SCHEDULED_LIVE','BOOT_CATCHUP','HOLE_BACKFILL','ARCHIVE_BACKFILL'
            )),
            origin_mode TEXT NOT NULL CHECK (origin_mode IN (
                'SCHEDULED_LIVE','BOOT_CATCHUP','HOLE_BACKFILL','ARCHIVE_BACKFILL'
            )),
            source_cycle_time TEXT NOT NULL,
            source_issue_time TEXT,
            source_release_time TEXT,
            source_available_at TEXT,
            fetch_started_at TEXT,
            fetch_finished_at TEXT,
            captured_at TEXT,
            imported_at TEXT,
            valid_time_start TEXT,
            valid_time_end TEXT,
            target_local_date TEXT,
            city_id TEXT,
            city_timezone TEXT,
            temperature_metric TEXT
                CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
            physical_quantity TEXT,
            observation_field TEXT,
            data_version TEXT,
            expected_members INTEGER,
            observed_members INTEGER,
            expected_steps_json TEXT NOT NULL DEFAULT '[]',
            observed_steps_json TEXT NOT NULL DEFAULT '[]',
            expected_count INTEGER,
            observed_count INTEGER,
            completeness_status TEXT NOT NULL CHECK (completeness_status IN (
                'COMPLETE','PARTIAL','MISSING','NOT_RELEASED'
            )),
            partial_run INTEGER NOT NULL DEFAULT 0 CHECK (partial_run IN (0,1)),
            raw_payload_hash TEXT,
            manifest_hash TEXT,
            status TEXT NOT NULL CHECK (status IN (
                'RUNNING','SUCCESS','FAILED','PARTIAL','SKIPPED_NOT_RELEASED'
            )),
            reason_code TEXT,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (partial_run = 0 OR completeness_status = 'PARTIAL')
        );
-- table: sqlite_sequence
CREATE TABLE sqlite_sequence(name,seq);
