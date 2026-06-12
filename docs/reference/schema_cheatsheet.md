# Zeus live-DB schema cheatsheet

Generated: `2026-06-12T20:39:46.454610+00:00`
Generator: `.venv/bin/python scripts/generate_schema_cheatsheet.py`

> Regenerate after schema changes. The schema-fingerprint test pins the SCHEMA; this file pins the NAMES (column names + types) for humans/agents, to kill the PRAGMA-trial-and-error tax. Row counts are cheap estimates (max(rowid)); tables over 1M rows show `rows≈-`. READ-ONLY (mode=ro).

## zeus-world.db

_102 base tables_

- **asos_wu_offsets**  (rows≈0, cols=5)
    city:TEXT, season:TEXT, offset:REAL, std:REAL, n_samples:INTEGER
- **availability_fact**  (rows≈6414, cols=8)
    availability_id:TEXT, scope_type:TEXT, scope_key:TEXT, failure_type:TEXT, started_at:TEXT,
    ended_at:TEXT, impact:TEXT, details_json:TEXT
- **book_hash_transitions**  (rows≈0, cols=8)
    market_slug:TEXT, observed_at:TEXT, transition_seq:INTEGER, prev_hash:TEXT, new_hash:TEXT,
    delta_ms:INTEGER, cycle_id:TEXT, schema_version:INTEGER
- **calibration_decision_group**  (rows≈0, cols=13)
    group_id:TEXT, city:TEXT, target_date:TEXT, forecast_available_at:TEXT, cluster:TEXT, season:TEXT,
    lead_days:REAL, settlement_value:REAL, winning_range_label:TEXT, bias_corrected:INTEGER,
    n_pair_rows:INTEGER, n_positive_rows:INTEGER, recorded_at:TEXT
- **chronicle**  (rows≈1, cols=6)
    id:INTEGER, event_type:TEXT, trade_id:INTEGER, timestamp:TEXT, details_json:TEXT, env:TEXT
- **collateral_ledger_snapshots**  (rows≈0, cols=11)
    id:INTEGER, pusd_balance_micro:INTEGER, pusd_allowance_micro:INTEGER,
    usdc_e_legacy_balance_micro:INTEGER, ctf_token_balances_json:TEXT, ctf_token_allowances_json:TEXT,
    reserved_pusd_for_buys_micro:INTEGER, reserved_tokens_for_sells_json:TEXT, captured_at:TEXT,
    authority_tier:TEXT, raw_balance_payload_hash:TEXT
- **collateral_reservations**  (rows≈0, cols=7)
    command_id:TEXT, reservation_type:TEXT, token_id:TEXT, amount:INTEGER, created_at:TEXT,
    released_at:TEXT, release_reason:TEXT
- **control_overrides_history**  (rows≈431, cols=13)
    history_id:INTEGER, override_id:TEXT, target_type:TEXT, target_key:TEXT, action_type:TEXT,
    value:TEXT, issued_by:TEXT, issued_at:TEXT, effective_until:TEXT, reason:TEXT, precedence:INTEGER,
    operation:TEXT, recorded_at:TEXT
- **daily_observation_revisions**  (rows≈3129, cols=17)
    id:INTEGER, city:TEXT, target_date:TEXT, source:TEXT, natural_key_json:TEXT,
    existing_row_id:INTEGER, existing_combined_payload_hash:TEXT, incoming_combined_payload_hash:TEXT,
    existing_high_payload_hash:TEXT, existing_low_payload_hash:TEXT, incoming_high_payload_hash:TEXT,
    incoming_low_payload_hash:TEXT, reason:TEXT, writer:TEXT, existing_row_json:TEXT,
    incoming_row_json:TEXT, recorded_at:TEXT
- **data_coverage**  (rows≈665382, cols=10)
    data_table:TEXT, city:TEXT, data_source:TEXT, target_date:TEXT, sub_key:TEXT, status:TEXT,
    reason:TEXT, fetched_at:TEXT, expected_at:TEXT, retry_after:TEXT
- **day0_metric_fact**  (rows≈0, cols=22)
    fact_id:TEXT, city:TEXT, target_date:TEXT, temperature_metric:TEXT, source:TEXT,
    local_timestamp:TEXT, utc_timestamp:TEXT, local_hour:REAL, temp_current:REAL, running_extreme:REAL,
    delta_rate_per_h:REAL, daylight_progress:REAL, obs_age_minutes:REAL, extreme_confidence:REAL,
    ens_q50_remaining_extreme:REAL, ens_q90_remaining_extreme:REAL, ens_spread:REAL,
    settlement_value:REAL, residual_to_settlement:REAL, fact_status:TEXT, missing_reason_json:TEXT,
    recorded_at:TEXT
- **day0_oracle_anomaly_flags**  (rows≈141, cols=5)
    city:TEXT, target_date:TEXT, flagged_at:TEXT, ttl_hours:REAL, detail:TEXT
- **db_chunk_boundary_events**  (rows≈9, cols=7)
    event_id:TEXT, occurred_at:TEXT, caller_module:TEXT, db_path:TEXT, rows_processed:INTEGER,
    duration_ms:INTEGER, split_reason:TEXT
- **decision_certificate_edges**  (rows≈-, cols=6)
    child_certificate_id:TEXT, parent_role:TEXT, parent_certificate_hash:TEXT,
    parent_certificate_type:TEXT, required:INTEGER, created_at:TEXT
- **decision_certificate_supersessions**  (rows≈0, cols=5)
    supersession_id:TEXT, old_certificate_hash:TEXT, new_certificate_hash:TEXT, reason:TEXT,
    created_at:TEXT
- **decision_certificates**  (rows≈-, cols=25)
    certificate_id:TEXT, certificate_type:TEXT, schema_version:INTEGER, canonicalization_version:TEXT,
    semantic_key:TEXT, claim_type:TEXT, mode:TEXT, decision_time:TEXT, source_available_at:TEXT,
    agent_received_at:TEXT, persisted_at:TEXT, max_parent_source_available_at:TEXT,
    max_parent_agent_received_at:TEXT, max_parent_persisted_at:TEXT, authority_id:TEXT,
    authority_version:TEXT, algorithm_id:TEXT, algorithm_version:TEXT, config_hash:TEXT,
    model_version_hash:TEXT, payload_json:TEXT, payload_hash:TEXT, certificate_hash:TEXT,
    verifier_status:TEXT, created_at:TEXT
- **decision_compile_failures**  (rows≈267132, cols=10)
    failure_id:TEXT, event_id:TEXT, decision_time:TEXT, mode:TEXT, claim_type:TEXT, stage:TEXT,
    reason_code:TEXT, reason_detail:TEXT, parent_hashes_json:TEXT, created_at:TEXT
- **decision_events**  (rows≈0, cols=31)
    market_slug:TEXT, temperature_metric:TEXT, target_date:TEXT, observation_time:TEXT,
    decision_seq:INTEGER, condition_id:TEXT, decision_event_id:TEXT, decision_time:TEXT, outcome:TEXT,
    side:TEXT, strategy_key:TEXT, cycle_id:TEXT, cycle_iteration:INTEGER, p_posterior:REAL, edge:REAL,
    target_size_usd:REAL, target_price:REAL, forecast_time:TEXT, provider_reported_time:TEXT,
    observation_available_at:TEXT, polymarket_end_anchor_source:TEXT, first_member_observed_time:TEXT,
    run_complete_time:TEXT, zeus_submit_intent_time:TEXT, venue_ack_time:TEXT,
    first_inclusion_block_time:TEXT, finality_confirmed_time:TEXT,
    clock_skew_estimate_ms_at_submit:INTEGER, raw_orderbook_hash_transition_delta_ms:INTEGER,
    schema_version:INTEGER, source:TEXT
- **decision_log**  (rows≈0, cols=7)
    id:INTEGER, mode:TEXT, started_at:TEXT, completed_at:TEXT, artifact_json:TEXT, timestamp:TEXT,
    env:TEXT
- **diurnal_curves**  (rows≈4942, cols=7)
    city:TEXT, season:TEXT, hour:INTEGER, avg_temp:REAL, std_temp:REAL, n_samples:INTEGER,
    p_high_set:REAL
- **diurnal_peak_prob**  (rows≈14530, cols=5)
    city:TEXT, month:INTEGER, hour:INTEGER, p_high_set:REAL, n_obs:INTEGER
- **edli_fill_bridge_dispositions**  (rows≈1, cols=7)
    aggregate_id:TEXT, disposition:TEXT, reason:TEXT, attempt_count:INTEGER, last_error:TEXT,
    created_at:TEXT, updated_at:TEXT
- **edli_live_cap_day_slots**  (rows≈23, cols=7)
    cap_scope:TEXT, cap_date:TEXT, slot:INTEGER, usage_id:TEXT, event_id:TEXT, created_at:TEXT,
    schema_version:INTEGER
- **edli_live_cap_rate_window**  (rows≈0, cols=7)
    cap_scope:TEXT, window_key:TEXT, slot:INTEGER, usage_id:TEXT, event_id:TEXT, created_at:TEXT,
    schema_version:INTEGER
- **edli_live_cap_usage**  (rows≈441, cols=13)
    usage_id:TEXT, event_id:TEXT, decision_time:TEXT, cap_scope:TEXT, max_notional_usd:REAL,
    max_orders_per_day:INTEGER, reserved_notional_usd:REAL, order_count:INTEGER,
    reservation_status:TEXT, final_intent_id:TEXT, execution_command_id:TEXT, created_at:TEXT,
    schema_version:INTEGER
- **edli_live_order_events**  (rows≈3138, cols=12)
    aggregate_event_id:TEXT, aggregate_id:TEXT, event_sequence:INTEGER, event_type:TEXT,
    parent_event_hash:TEXT, event_hash:TEXT, payload_json:TEXT, payload_hash:TEXT,
    source_authority:TEXT, occurred_at:TEXT, created_at:TEXT, schema_version:INTEGER
- **edli_live_order_projection**  (rows≈441, cols=13)
    aggregate_id:TEXT, event_id:TEXT, final_intent_id:TEXT, current_state:TEXT, last_sequence:INTEGER,
    last_event_type:TEXT, last_event_hash:TEXT, pending_reconcile:INTEGER, venue_order_id:TEXT,
    updated_at:TEXT, schema_version:INTEGER, posterior_id:INTEGER, probability_authority:TEXT
- **edli_live_profit_audit**  (rows≈714, cols=45)
    audit_id:TEXT, event_id:TEXT, aggregate_id:TEXT, final_intent_id:TEXT, execution_command_id:TEXT,
    condition_id:TEXT, token_id:TEXT, direction:TEXT, side:TEXT, q_live:REAL, q_lcb_5pct:REAL,
    expected_cost_basis:REAL, expected_fee:REAL, expected_spread_cost:REAL, visible_depth_fill_lcb:REAL,
    order_policy:TEXT, native_token_side:TEXT, expected_edge:REAL, kelly_size_usd:REAL,
    live_cap_notional:REAL, quote_seen_at:TEXT, quote_age_ms:INTEGER, best_bid:REAL, best_ask:REAL,
    limit_price:REAL, order_type:TEXT, time_in_force:TEXT, venue_order_id:TEXT,
    order_lifecycle_state:TEXT, avg_fill_price:REAL, filled_size:REAL, fees:REAL, post_fill_mark:REAL,
    settlement_outcome:TEXT, realized_edge:REAL, edge_value_usd:REAL, pnl_usd:REAL, reject_reason:TEXT,
    expected_edge_source_certificate_hash:TEXT, cost_basis_source_certificate_hash:TEXT,
    fill_source_event_hash:TEXT, settlement_source_event_hash:TEXT, promotion_eligible:INTEGER,
    created_at:TEXT, schema_version:INTEGER
- **edli_no_submit_receipts**  (rows≈62874, cols=41)
    receipt_id:TEXT, event_id:TEXT, causal_snapshot_id:TEXT, decision_time:TEXT, family_id:TEXT,
    candidate_id:TEXT, condition_id:TEXT, token_id:TEXT, direction:TEXT, executable_snapshot_id:TEXT,
    final_intent_id:TEXT, side_effect_status:TEXT, q_live:REAL, q_lcb_5pct:REAL, c_fee_adjusted:REAL,
    c_cost_95pct:REAL, p_fill_lcb:REAL, trade_score:REAL, fdr_family_id:TEXT,
    fdr_hypothesis_count:INTEGER, kelly_cost_basis_id:TEXT, kelly_decision_id:TEXT,
    risk_decision_id:TEXT, kelly_size_usd:REAL, projection_hash:TEXT, receipt_json:TEXT,
    receipt_hash:TEXT, created_at:TEXT, schema_version:INTEGER, mainstream_agreement_pass:INTEGER,
    mainstream_agreement_fail_reason:TEXT, mainstream_point:REAL, mainstream_delta:REAL,
    mainstream_bin_label:TEXT, mainstream_source:TEXT, mainstream_fetched_at_utc:TEXT, alpha_gap:REAL,
    posterior_id:INTEGER, probability_authority:TEXT, q_lcb_calibration_source:TEXT, envelope_json:TEXT
- **edli_user_channel_inbox**  (rows≈0, cols=14)
    message_hash:TEXT, source_authority:TEXT, message_type:TEXT, aggregate_id:TEXT, event_id:TEXT,
    final_intent_id:TEXT, venue_order_id:TEXT, payload_json:TEXT, occurred_at:TEXT, received_at:TEXT,
    processed_at:TEXT, processing_status:TEXT, processing_error:TEXT, schema_version:INTEGER
- **edli_user_channel_message_dedup**  (rows≈36, cols=6)
    message_hash:TEXT, aggregate_id:TEXT, venue_order_id:TEXT, message_type:TEXT, observed_at:TEXT,
    created_at:TEXT
- **event_dead_letters**  (rows≈20110, cols=8)
    dead_letter_id:TEXT, consumer_name:TEXT, event_id:TEXT, failure_stage:TEXT, error_message:TEXT,
    event_payload_json:TEXT, created_at:TEXT, schema_version:INTEGER
- **evidence_tier_assignments**  (rows≈0, cols=15)
    id:INTEGER, strategy_id:TEXT, tier:INTEGER, assigned_at:TEXT, rationale:TEXT, operator_ref:TEXT,
    verdict_reason:TEXT, schema_version:INTEGER, assignment_source:TEXT, verdict_kind:TEXT,
    effective_from:TEXT, effective_until:TEXT, revoked_at:TEXT, revoked_by:TEXT,
    supersedes_assignment_id:INTEGER
- **exchange_reconcile_findings**  (rows≈0, cols=9)
    finding_id:TEXT, kind:TEXT, subject_id:TEXT, context:TEXT, evidence_json:TEXT, recorded_at:TEXT,
    resolved_at:TEXT, resolution:TEXT, resolved_by:TEXT
- **executable_market_snapshots**  (rows≈0, cols=36)
    snapshot_id:TEXT, gamma_market_id:TEXT, event_id:TEXT, event_slug:TEXT, condition_id:TEXT,
    question_id:TEXT, yes_token_id:TEXT, no_token_id:TEXT, selected_outcome_token_id:TEXT,
    outcome_label:TEXT, enable_orderbook:INTEGER, active:INTEGER, closed:INTEGER,
    accepting_orders:INTEGER, market_start_at:TEXT, market_end_at:TEXT, market_close_at:TEXT,
    sports_start_at:TEXT, min_tick_size:TEXT, min_order_size:TEXT, fee_details_json:TEXT,
    token_map_json:TEXT, rfqe:INTEGER, neg_risk:INTEGER, orderbook_top_bid:TEXT, orderbook_top_ask:TEXT,
    orderbook_depth_json:TEXT, raw_gamma_payload_hash:TEXT, raw_clob_market_info_hash:TEXT,
    raw_orderbook_hash:TEXT, authority_tier:TEXT, captured_at:TEXT, freshness_deadline:TEXT,
    wide_spread_display_substitution:INTEGER, depth_at_best_ask:INTEGER, tradeability_status_json:TEXT
- **execution_fact**  (rows≈0, cols=15)
    intent_id:TEXT, position_id:TEXT, decision_id:TEXT, order_role:TEXT, strategy_key:TEXT,
    posted_at:TEXT, filled_at:TEXT, voided_at:TEXT, submitted_price:REAL, fill_price:REAL, shares:REAL,
    fill_quality:REAL, latency_seconds:REAL, venue_status:TEXT, terminal_exec_status:TEXT
- **execution_feasibility_evidence**  (rows≈-, cols=26)
    evidence_id:TEXT, event_id:TEXT, condition_id:TEXT, token_id:TEXT, outcome_label:TEXT,
    direction:TEXT, quote_seen_at:TEXT, book_hash_before:TEXT, best_bid_before:REAL,
    best_ask_before:REAL, depth_before_json:TEXT, order_intent_time:TEXT, submit_time:TEXT,
    accepted_or_rejected:TEXT, venue_order_id:TEXT, fok_full_fill:INTEGER, fak_partial_fill:INTEGER,
    filled_shares:REAL, fill_price:REAL, cancel_remainder_status:TEXT, book_hash_after:TEXT,
    latency_ms:INTEGER, maker_cancel_before_submit:INTEGER, would_have_edge_after_fee:INTEGER,
    created_at:TEXT, schema_version:INTEGER
- **exit_mutex_holdings**  (rows≈0, cols=5)
    mutex_key:TEXT, command_id:TEXT, acquired_at:TEXT, released_at:TEXT, release_reason:TEXT
- **forecast_skill**  (rows≈23590, cols=11)
    id:INTEGER, city:TEXT, target_date:TEXT, source:TEXT, lead_days:INTEGER, forecast_temp:REAL,
    actual_temp:REAL, error:REAL, temp_unit:TEXT, season:TEXT, available_at:TEXT
- **forecasts**  (rows≈187751, cols=20)
    id:INTEGER, city:TEXT, target_date:TEXT, source:TEXT, forecast_basis_date:TEXT,
    forecast_issue_time:TEXT, lead_days:INTEGER, lead_time_hours:REAL, forecast_high:REAL,
    forecast_low:REAL, temp_unit:TEXT, retrieved_at:TEXT, imported_at:TEXT, rebuild_run_id:TEXT,
    data_source_version:TEXT, source_id:TEXT, raw_payload_hash:TEXT, captured_at:TEXT,
    authority_tier:TEXT, availability_provenance:TEXT
- **historical_forecasts**  (rows≈69660, cols=8)
    id:INTEGER, city:TEXT, target_date:TEXT, source:TEXT, forecast_high:REAL, temp_unit:TEXT,
    lead_days:INTEGER, available_at:TEXT
- **hko_hourly_accumulator**  (rows≈421, cols=4)
    target_date:TEXT, hour_utc:TEXT, temperature:REAL, fetched_at:TEXT
- **hourly_observations**  (rows≈-, cols=7)
    id:INTEGER, city:TEXT, obs_date:TEXT, obs_hour:INTEGER, temp:REAL, temp_unit:TEXT, source:TEXT
- **job_run**  (rows≈0, cols=25)
    job_run_id:TEXT, job_run_key:TEXT, job_name:TEXT, plane:TEXT, scheduled_for:TEXT, missed_from:TEXT,
    started_at:TEXT, finished_at:TEXT, lock_key:TEXT, lock_acquired_at:TEXT, status:TEXT,
    reason_code:TEXT, rows_written:INTEGER, rows_failed:INTEGER, source_run_id:TEXT, source_id:TEXT,
    track:TEXT, release_calendar_key:TEXT, safe_fetch_not_before:TEXT, expected_scope_json:TEXT,
    affected_scope_json:TEXT, readiness_impacts_json:TEXT, readiness_recomputed_at:TEXT, meta_json:TEXT,
    recorded_at:TEXT
- **market_events**  (rows≈0, cols=11)
    id:INTEGER, market_slug:TEXT, city:TEXT, target_date:TEXT, condition_id:TEXT, token_id:TEXT,
    range_label:TEXT, range_low:REAL, range_high:REAL, outcome:TEXT, created_at:TEXT
- **market_price_history**  (rows≈0, cols=14)
    id:INTEGER, market_slug:TEXT, token_id:TEXT, price:REAL, recorded_at:TEXT, hours_since_open:REAL,
    hours_to_resolution:REAL, market_price_linkage:TEXT, source:TEXT, best_bid:REAL, best_ask:REAL,
    raw_orderbook_hash:TEXT, snapshot_id:TEXT, condition_id:TEXT
- **market_topology_state**  (rows≈3938, cols=24)
    topology_id:TEXT, scope_key:TEXT, market_family:TEXT, event_id:TEXT, condition_id:TEXT,
    question_id:TEXT, city_id:TEXT, city_timezone:TEXT, target_local_date:TEXT, temperature_metric:TEXT,
    physical_quantity:TEXT, observation_field:TEXT, data_version:TEXT, token_ids_json:TEXT,
    bin_topology_hash:TEXT, gamma_captured_at:TEXT, gamma_updated_at:TEXT, source_contract_status:TEXT,
    source_contract_reason:TEXT, authority_status:TEXT, status:TEXT, expires_at:TEXT,
    provenance_json:TEXT, recorded_at:TEXT
- **model_bias**  (rows≈165, cols=7)
    city:TEXT, season:TEXT, source:TEXT, bias:REAL, mae:REAL, n_samples:INTEGER, discount_factor:REAL
- **model_bias_ens**  (rows≈153, cols=38)
    city:TEXT, season:TEXT, month:INTEGER, metric:TEXT, live_source_id:TEXT, live_data_version:TEXT,
    prior_source_id:TEXT, prior_data_version:TEXT, contributor_policy:TEXT, bias_unit:TEXT,
    posterior_bias_c:REAL, posterior_sd_c:REAL, n_live:INTEGER, n_prior:INTEGER, n_paired:INTEGER,
    weight_live:REAL, paired_delta_c:REAL, v0_c2:REAL, vo_c2:REAL, estimator:TEXT, training_cutoff:TEXT,
    recorded_at:TEXT, lead_bucket:TEXT, error_model_family:TEXT, error_model_key:TEXT,
    transport_delta_policy:TEXT, bias_c:REAL, bias_sd_c:REAL, residual_sd_c:REAL,
    heterogeneity_var_c2:REAL, correction_strength:REAL, effective_bias_c:REAL,
    total_residual_sd_c:REAL, code_commit:TEXT, fit_signature_hash:TEXT, authority:TEXT,
    gate_set_hash:TEXT, coverage_months:TEXT
- **no_trade_events**  (rows≈2952, cols=13)
    market_slug:TEXT, temperature_metric:TEXT, target_date:TEXT, observation_time:TEXT,
    decision_seq:INTEGER, reason:TEXT, reason_detail:TEXT, strategy_key:TEXT, event_source:TEXT,
    shadow_runtime:INTEGER, observed_at:TEXT, schema_version:INTEGER, schema_compatibility:TEXT
- **no_trade_regret_events**  (rows≈259657, cols=36)
    regret_event_id:TEXT, event_id:TEXT, rejection_stage:TEXT, rejection_reason:TEXT,
    regret_bucket:TEXT, market_slug:TEXT, condition_id:TEXT, token_id:TEXT, outcome_label:TEXT,
    decision_time:TEXT, city:TEXT, target_date:TEXT, metric:TEXT, family_id:TEXT, bin_label:TEXT,
    direction:TEXT, q_live:REAL, q_lcb_5pct:REAL, c_fee_adjusted:REAL, c_cost_95pct:REAL,
    p_fill_lcb:REAL, trade_score:REAL, native_quote_available:INTEGER, source_status:TEXT,
    family_complete:INTEGER, hypothetical_order_type:TEXT, hypothetical_fill_status:TEXT,
    hypothetical_fill_price:REAL, causal_snapshot_id:TEXT, executable_snapshot_id:TEXT,
    later_outcome:TEXT, would_have_won:INTEGER, would_have_filled:INTEGER, created_at:TEXT,
    schema_version:INTEGER, envelope_json:TEXT
- **observation_instants**  (rows≈-, cols=32)
    id:INTEGER, city:TEXT, target_date:TEXT, source:TEXT, timezone_name:TEXT, local_hour:REAL,
    local_timestamp:TEXT, utc_timestamp:TEXT, utc_offset_minutes:INTEGER, dst_active:INTEGER,
    is_ambiguous_local_hour:INTEGER, is_missing_local_hour:INTEGER, time_basis:TEXT, temp_current:REAL,
    running_max:REAL, running_min:REAL, delta_rate_per_h:REAL, temp_unit:TEXT, station_id:TEXT,
    observation_count:INTEGER, raw_response:TEXT, source_file:TEXT, imported_at:TEXT, authority:TEXT,
    data_version:TEXT, provenance_json:TEXT, temperature_metric:TEXT, physical_quantity:TEXT,
    observation_field:TEXT, training_allowed:INTEGER, causality_status:TEXT, source_role:TEXT
- **observation_revisions**  (rows≈134250, cols=15)
    id:INTEGER, table_name:TEXT, city:TEXT, target_date:TEXT, source:TEXT, utc_timestamp:TEXT,
    natural_key_json:TEXT, existing_row_id:INTEGER, existing_payload_hash:TEXT,
    incoming_payload_hash:TEXT, reason:TEXT, writer:TEXT, existing_row_json:TEXT,
    incoming_row_json:TEXT, recorded_at:TEXT
- **observations**  (rows≈164, cols=36)
    id:INTEGER, city:TEXT, target_date:TEXT, source:TEXT, high_temp:REAL, low_temp:REAL, unit:TEXT,
    station_id:TEXT, fetched_at:TEXT, high_raw_value:REAL, high_raw_unit:TEXT, high_target_unit:TEXT,
    low_raw_value:REAL, low_raw_unit:TEXT, low_target_unit:TEXT, high_fetch_utc:TEXT,
    high_local_time:TEXT, high_collection_window_start_utc:TEXT, high_collection_window_end_utc:TEXT,
    low_fetch_utc:TEXT, low_local_time:TEXT, low_collection_window_start_utc:TEXT,
    low_collection_window_end_utc:TEXT, timezone:TEXT, utc_offset_minutes:INTEGER, dst_active:INTEGER,
    is_ambiguous_local_hour:INTEGER, is_missing_local_hour:INTEGER, hemisphere:TEXT, season:TEXT,
    month:INTEGER, rebuild_run_id:TEXT, data_source_version:TEXT, authority:TEXT,
    high_provenance_metadata:TEXT, low_provenance_metadata:TEXT
- **opportunity_event_processing**  (rows≈-, cols=8)
    consumer_name:TEXT, event_id:TEXT, processing_status:TEXT, attempt_count:INTEGER, claimed_at:TEXT,
    processed_at:TEXT, last_error:TEXT, updated_at:TEXT
- **opportunity_events**  (rows≈-, cols=15)
    event_id:TEXT, event_type:TEXT, entity_key:TEXT, source:TEXT, observed_at:TEXT, available_at:TEXT,
    received_at:TEXT, causal_snapshot_id:TEXT, payload_hash:TEXT, idempotency_key:TEXT,
    priority:INTEGER, expires_at:TEXT, payload_json:TEXT, schema_version:INTEGER, created_at:TEXT
- **opportunity_fact**  (rows≈0, cols=21)
    decision_id:TEXT, candidate_id:TEXT, city:TEXT, target_date:TEXT, range_label:TEXT, direction:TEXT,
    strategy_key:TEXT, discovery_mode:TEXT, entry_method:TEXT, snapshot_id:TEXT, p_raw:REAL, p_cal:REAL,
    p_market:REAL, alpha:REAL, best_edge:REAL, ci_width:REAL, rejection_stage:TEXT,
    rejection_reason_json:TEXT, availability_status:TEXT, should_trade:INTEGER, recorded_at:TEXT
- **outcome_fact**  (rows≈0, cols=13)
    position_id:TEXT, strategy_key:TEXT, entered_at:TEXT, exited_at:TEXT, settled_at:TEXT,
    exit_reason:TEXT, admin_exit_reason:TEXT, decision_snapshot_id:TEXT, pnl:REAL, outcome:INTEGER,
    hold_duration_hours:REAL, monitor_count:INTEGER, chain_corrections_count:INTEGER
- **platt_models**  (rows≈981, cols=23)
    model_key:TEXT, temperature_metric:TEXT, cluster:TEXT, season:TEXT, data_version:TEXT,
    input_space:TEXT, param_A:REAL, param_B:REAL, param_C:REAL, bootstrap_params_json:TEXT,
    n_samples:INTEGER, brier_insample:REAL, fitted_at:TEXT, is_active:INTEGER, authority:TEXT,
    bucket_key:TEXT, cycle:TEXT, source_id:TEXT, horizon_profile:TEXT, recorded_at:TEXT,
    error_model_family:TEXT, calibration_method:TEXT, training_cutoff:TEXT
- **position_current**  (rows≈0, cols=46)
    position_id:TEXT, phase:TEXT, trade_id:TEXT, market_id:TEXT, city:TEXT, cluster:TEXT,
    target_date:TEXT, bin_label:TEXT, direction:TEXT, unit:TEXT, size_usd:REAL, shares:REAL,
    cost_basis_usd:REAL, entry_price:REAL, p_posterior:REAL, last_monitor_prob:REAL,
    last_monitor_edge:REAL, last_monitor_market_price:REAL, decision_snapshot_id:TEXT,
    entry_method:TEXT, strategy_key:TEXT, edge_source:TEXT, discovery_mode:TEXT, chain_state:TEXT,
    token_id:TEXT, no_token_id:TEXT, condition_id:TEXT, order_id:TEXT, order_status:TEXT,
    updated_at:TEXT, temperature_metric:TEXT, fill_authority:TEXT, recovery_authority:TEXT,
    chain_shares:REAL, chain_seen_at:TEXT, chain_absence_at:TEXT, chain_avg_price:REAL,
    chain_cost_basis_usd:REAL, realized_pnl_usd:REAL, exit_price:REAL, settlement_price:REAL,
    settled_at:TEXT, exit_reason:TEXT, entry_ci_width:REAL, exit_retry_count:INTEGER,
    next_exit_retry_at:TEXT
- **position_events**  (rows≈0, cols=19)
    event_id:TEXT, position_id:TEXT, event_version:INTEGER, sequence_no:INTEGER, event_type:TEXT,
    occurred_at:TEXT, phase_before:TEXT, phase_after:TEXT, strategy_key:TEXT, decision_id:TEXT,
    snapshot_id:TEXT, order_id:TEXT, command_id:TEXT, caused_by:TEXT, idempotency_key:TEXT,
    venue_status:TEXT, source_module:TEXT, env:TEXT, payload_json:TEXT
- **position_lots**  (rows≈0, cols=17)
    lot_id:INTEGER, position_id:INTEGER, state:TEXT, shares:INTEGER, entry_price_avg:TEXT,
    exit_price_avg:TEXT, source_command_id:TEXT, source_trade_fact_id:INTEGER, captured_at:TEXT,
    state_changed_at:TEXT, source:TEXT, observed_at:TEXT, venue_timestamp:TEXT, ingested_at:TEXT,
    local_sequence:INTEGER, raw_payload_hash:TEXT, raw_payload_json:TEXT
- **probability_trace_fact**  (rows≈5780, cols=50)
    trace_id:TEXT, decision_id:TEXT, decision_snapshot_id:TEXT, candidate_id:TEXT, city:TEXT,
    target_date:TEXT, range_label:TEXT, direction:TEXT, mode:TEXT, strategy_key:TEXT,
    discovery_mode:TEXT, entry_method:TEXT, selected_method:TEXT, trace_status:TEXT,
    missing_reason_json:TEXT, bin_labels_json:TEXT, p_raw_json:TEXT, p_cal_json:TEXT,
    p_market_json:TEXT, p_posterior_json:TEXT, p_posterior:REAL, alpha:REAL, agreement:TEXT,
    n_edges_found:INTEGER, n_edges_after_fdr:INTEGER, rejection_stage:TEXT, availability_status:TEXT,
    recorded_at:TEXT, market_phase:TEXT, market_phase_source:TEXT, market_start_at:TEXT,
    market_end_at:TEXT, settlement_day_entry_utc:TEXT, uma_resolved_source:TEXT,
    prob_tail_mass_cal:REAL, prob_tail_mass_market:REAL, prob_tail_entropy:REAL,
    probability_sanity_mode:TEXT, probability_sanity_reason:TEXT, edge_bin_idx:INTEGER,
    edge_bin_label:TEXT, edge_bin_p_raw:REAL, edge_bin_p_cal:REAL, edge_bin_p_market:REAL,
    edge_bin_member_support:REAL, edge_bin_odds_ratio:REAL, near_tail_p_cal:REAL,
    near_tail_p_market:REAL, p_raw_domain:TEXT, condition_ids_json:TEXT
- **provenance_envelope_events**  (rows≈0, cols=11)
    id:INTEGER, subject_type:TEXT, subject_id:TEXT, event_type:TEXT, payload_hash:TEXT,
    payload_json:TEXT, source:TEXT, observed_at:TEXT, venue_timestamp:TEXT, ingested_at:TEXT,
    local_sequence:INTEGER
- **readiness_state**  (rows≈577, cols=27)
    readiness_id:TEXT, scope_key:TEXT, scope_type:TEXT, city_id:TEXT, city:TEXT, city_timezone:TEXT,
    target_local_date:TEXT, metric:TEXT, temperature_metric:TEXT, physical_quantity:TEXT,
    observation_field:TEXT, data_version:TEXT, source_id:TEXT, track:TEXT, source_run_id:TEXT,
    market_family:TEXT, event_id:TEXT, condition_id:TEXT, token_ids_json:TEXT, strategy_key:TEXT,
    status:TEXT, reason_codes_json:TEXT, computed_at:TEXT, expires_at:TEXT, dependency_json:TEXT,
    provenance_json:TEXT, recorded_at:TEXT
- **refit_bucket_failures**  (rows≈0, cols=8)
    id:INTEGER, cluster:TEXT, season:TEXT, cycle:TEXT, source_id:TEXT, error_class:TEXT,
    error_text:TEXT, ts:TEXT
- **regime_correlation_cache**  (rows≈0, cols=7)
    regime:TEXT, cities_json:TEXT, matrix_json:TEXT, fitted_at:TEXT, n_observations:INTEGER,
    intensity:REAL, schema_version:INTEGER
- **regret_decompositions**  (rows≈0, cols=12)
    id:INTEGER, experiment_id:TEXT, decision_event_id:TEXT, forecast_error_usd:REAL,
    observation_error_usd:REAL, quote_error_usd:REAL, non_fill_error_usd:REAL, fee_error_usd:REAL,
    timing_error_usd:REAL, settlement_ambiguity_error_usd:REAL, total_regret_usd:REAL, computed_at:TEXT
- **replay_results**  (rows≈0, cols=20)
    id:INTEGER, replay_run_id:TEXT, mode:TEXT, city:TEXT, target_date:TEXT, settlement_value:REAL,
    winning_bin:TEXT, replay_direction:TEXT, replay_edge:REAL, replay_p_posterior:REAL,
    replay_size_usd:REAL, replay_should_trade:INTEGER, replay_rejection_stage:TEXT,
    actual_direction:TEXT, actual_edge:REAL, actual_should_trade:INTEGER, replay_pnl:REAL,
    actual_pnl:REAL, overrides_json:TEXT, timestamp:TEXT
- **rescue_events**  (rows≈0, cols=12)
    rescue_event_id:INTEGER, trade_id:TEXT, position_id:TEXT, decision_snapshot_id:TEXT,
    temperature_metric:TEXT, causality_status:TEXT, authority:TEXT, authority_source:TEXT,
    chain_state:TEXT, reason:TEXT, occurred_at:TEXT, recorded_at:TEXT
- **risk_actions**  (rows≈0, cols=10)
    action_id:TEXT, strategy_key:TEXT, action_type:TEXT, value:TEXT, issued_at:TEXT,
    effective_until:TEXT, reason:TEXT, source:TEXT, precedence:INTEGER, status:TEXT
- **selection_family_fact**  (rows≈0, cols=10)
    family_id:TEXT, cycle_mode:TEXT, decision_snapshot_id:TEXT, city:TEXT, target_date:TEXT,
    strategy_key:TEXT, discovery_mode:TEXT, created_at:TEXT, meta_json:TEXT, decision_time_status:TEXT
- **selection_hypothesis_fact**  (rows≈0, cols=19)
    hypothesis_id:TEXT, family_id:TEXT, decision_id:TEXT, candidate_id:TEXT, city:TEXT,
    target_date:TEXT, range_label:TEXT, direction:TEXT, p_value:REAL, q_value:REAL, ci_lower:REAL,
    ci_upper:REAL, edge:REAL, tested:INTEGER, passed_prefilter:INTEGER, selected_post_fdr:INTEGER,
    rejection_stage:TEXT, recorded_at:TEXT, meta_json:TEXT
- **settlement_attribution**  (rows≈0, cols=37)
    attribution_id:TEXT, position_id:TEXT, condition_id:TEXT, city:TEXT, target_date:TEXT,
    temperature_metric:TEXT, direction:TEXT, traded_bin_label:TEXT, category:TEXT, won:INTEGER,
    counts_as_skill_win:INTEGER, avg_fill_price:REAL, q_live:REAL, q_lcb_5pct:REAL, q_in_bin:REAL,
    market_in_bin_prob:REAL, market_q_ratio:REAL, decision_posterior_id:TEXT,
    decision_posterior_computed_at:TEXT, decision_posterior_age_hours:REAL, fresh_posterior_id:TEXT,
    fresh_posterior_computed_at:TEXT, fresh_q_supports_position:INTEGER, fresh_q_in_bin:REAL,
    fresh_input_identity:TEXT, fresh_input_age_hours:REAL, settled_value:REAL, settlement_unit:TEXT,
    settled_in_bin:INTEGER, settled_at:TEXT, freshness_budget_hours:REAL,
    fresher_cycle_existed_at_decision:INTEGER, large_factor_threshold:REAL, derivation_note:TEXT,
    rationale:TEXT, graded_at:TEXT, schema_version:INTEGER
- **settlement_command_events**  (rows≈0, cols=6)
    id:INTEGER, command_id:TEXT, event_type:TEXT, payload_hash:TEXT, payload_json:TEXT, recorded_at:TEXT
- **settlement_commands**  (rows≈0, cols=19)
    command_id:TEXT, state:TEXT, condition_id:TEXT, market_id:TEXT, payout_asset:TEXT,
    pusd_amount_micro:INTEGER, token_amounts_json:TEXT, tx_hash:TEXT, block_number:INTEGER,
    confirmation_count:INTEGER, requested_at:TEXT, submitted_at:TEXT, terminal_at:TEXT,
    error_payload:TEXT, polymarket_end_anchor_source:TEXT, zeus_submit_intent_time:TEXT,
    venue_ack_time:TEXT, clock_skew_estimate_ms_at_submit:INTEGER, autoretry_eligible:INTEGER
- **settlement_schema_migrations**  (rows≈1, cols=2)
    migration_key:TEXT, applied_at:TEXT
- **settlements**  (rows≈0, cols=18)
    id:INTEGER, city:TEXT, target_date:TEXT, market_slug:TEXT, winning_bin:TEXT, settlement_value:REAL,
    settlement_source:TEXT, settled_at:TEXT, authority:TEXT, pm_bin_lo:REAL, pm_bin_hi:REAL, unit:TEXT,
    settlement_source_type:TEXT, temperature_metric:TEXT, physical_quantity:TEXT,
    observation_field:TEXT, data_version:TEXT, provenance_json:TEXT
- **shadow_experiments**  (rows≈0, cols=7)
    experiment_id:TEXT, strategy_id:TEXT, config_hash:TEXT, started_at:TEXT, closed_at:TEXT,
    cohort_tag:TEXT, immutable:INTEGER
- **shadow_signals**  (rows≈4123, cols=9)
    id:INTEGER, city:TEXT, target_date:TEXT, timestamp:TEXT, decision_snapshot_id:TEXT, p_raw_json:TEXT,
    p_cal_json:TEXT, edges_json:TEXT, lead_hours:REAL
- **shoulder_exposure_ledger**  (rows≈0, cols=11)
    id:INTEGER, shoulder_side:TEXT, weather_system_cluster:TEXT, city:TEXT, target_date:TEXT,
    source:TEXT, regime:TEXT, notional_usd:REAL, decision_event_id:TEXT, observed_at:TEXT,
    schema_version:INTEGER
- **solar_daily**  (rows≈42362, cols=11)
    city:TEXT, target_date:TEXT, timezone:TEXT, lat:REAL, lon:REAL, sunrise_local:TEXT,
    sunset_local:TEXT, sunrise_utc:TEXT, sunset_utc:TEXT, utc_offset_minutes:INTEGER, dst_active:INTEGER
- **source_contract_audit_events**  (rows≈0, cols=21)
    audit_id:TEXT, checked_at_utc:TEXT, scan_authority:TEXT, report_status:TEXT, severity:TEXT,
    event_id:TEXT, slug:TEXT, title:TEXT, city:TEXT, target_date:TEXT, temperature_metric:TEXT,
    source_contract_status:TEXT, source_contract_reason:TEXT, configured_source_family:TEXT,
    configured_station_id:TEXT, observed_source_family:TEXT, observed_station_id:TEXT,
    resolution_sources_json:TEXT, source_contract_json:TEXT, payload_hash:TEXT, created_at:TEXT
- **source_run**  (rows≈0, cols=36)
    source_run_id:TEXT, source_id:TEXT, track:TEXT, release_calendar_key:TEXT, ingest_mode:TEXT,
    origin_mode:TEXT, source_cycle_time:TEXT, source_issue_time:TEXT, source_release_time:TEXT,
    source_available_at:TEXT, fetch_started_at:TEXT, fetch_finished_at:TEXT, captured_at:TEXT,
    imported_at:TEXT, valid_time_start:TEXT, valid_time_end:TEXT, target_local_date:TEXT, city_id:TEXT,
    city_timezone:TEXT, temperature_metric:TEXT, physical_quantity:TEXT, observation_field:TEXT,
    dataset_id:TEXT, expected_members:INTEGER, observed_members:INTEGER, expected_steps_json:TEXT,
    observed_steps_json:TEXT, expected_count:INTEGER, observed_count:INTEGER, completeness_status:TEXT,
    partial_run:INTEGER, raw_payload_hash:TEXT, manifest_hash:TEXT, status:TEXT, reason_code:TEXT,
    recorded_at:TEXT
- **source_run_coverage**  (rows≈1087, cols=27)
    coverage_id:TEXT, source_run_id:TEXT, source_id:TEXT, source_transport:TEXT,
    release_calendar_key:TEXT, track:TEXT, city_id:TEXT, city:TEXT, city_timezone:TEXT,
    target_local_date:TEXT, temperature_metric:TEXT, physical_quantity:TEXT, observation_field:TEXT,
    data_version:TEXT, expected_members:INTEGER, observed_members:INTEGER, expected_steps_json:TEXT,
    observed_steps_json:TEXT, snapshot_ids_json:TEXT, target_window_start_utc:TEXT,
    target_window_end_utc:TEXT, completeness_status:TEXT, readiness_status:TEXT, reason_code:TEXT,
    computed_at:TEXT, expires_at:TEXT, recorded_at:TEXT
- **strategy_health**  (rows≈0, cols=13)
    strategy_key:TEXT, as_of:TEXT, open_exposure_usd:REAL, settled_trades_30d:INTEGER,
    realized_pnl_30d:REAL, unrealized_pnl:REAL, win_rate_30d:REAL, brier_30d:REAL, fill_rate_14d:REAL,
    edge_trend_30d:REAL, risk_level:TEXT, execution_decay_flag:INTEGER, edge_compression_flag:INTEGER
- **tail_stress_scenarios**  (rows≈0, cols=9)
    market_slug:TEXT, temperature_metric:TEXT, target_date:TEXT, observation_time:TEXT,
    decision_seq:INTEGER, scenarios:TEXT, max_loss_pct:REAL, tail_probability_stressed:REAL,
    schema_version:INTEGER
- **temp_persistence**  (rows≈0, cols=6)
    city:TEXT, season:TEXT, delta_bucket:TEXT, frequency:REAL, avg_next_day_reversion:REAL,
    n_samples:INTEGER
- **token_price_log**  (rows≈0, cols=12)
    id:INTEGER, token_id:TEXT, city:TEXT, target_date:TEXT, range_label:TEXT, price:REAL, volume:REAL,
    bid:REAL, ask:REAL, spread:REAL, source_timestamp:TEXT, timestamp:TEXT
- **token_suppression**  (rows≈0, cols=7)
    token_id:TEXT, condition_id:TEXT, suppression_reason:TEXT, source_module:TEXT, created_at:TEXT,
    updated_at:TEXT, evidence_json:TEXT
- **token_suppression_history**  (rows≈0, cols=10)
    history_id:INTEGER, token_id:TEXT, condition_id:TEXT, suppression_reason:TEXT, source_module:TEXT,
    created_at:TEXT, updated_at:TEXT, evidence_json:TEXT, operation:TEXT, recorded_at:TEXT
- **trade_decisions**  (rows≈4, cols=49)
    trade_id:INTEGER, market_id:TEXT, bin_label:TEXT, direction:TEXT, size_usd:REAL, price:REAL,
    timestamp:TEXT, forecast_snapshot_id:INTEGER, calibration_model_version:TEXT, p_raw:REAL,
    p_calibrated:REAL, p_posterior:REAL, edge:REAL, ci_lower:REAL, ci_upper:REAL, kelly_fraction:REAL,
    status:TEXT, filled_at:TEXT, fill_price:REAL, runtime_trade_id:TEXT, order_id:TEXT,
    order_status_text:TEXT, order_posted_at:TEXT, entered_at_ts:TEXT, chain_state:TEXT, strategy:TEXT,
    edge_source:TEXT, bin_type:TEXT, discovery_mode:TEXT, market_hours_open:REAL, fill_quality:REAL,
    entry_method:TEXT, selected_method:TEXT, applied_validations_json:TEXT, exit_trigger:TEXT,
    exit_reason:TEXT, admin_exit_reason:TEXT, exit_divergence_score:REAL, exit_market_velocity_1h:REAL,
    exit_forward_edge:REAL, settlement_semantics_json:TEXT, epistemic_context_json:TEXT,
    edge_context_json:TEXT, entry_alpha_usd:REAL, execution_slippage_usd:REAL, exit_timing_usd:REAL,
    risk_throttling_usd:REAL, settlement_edge_usd:REAL, env:TEXT
- **uma_resolution**  (rows≈1333, cols=10)
    condition_id:TEXT, tx_hash:TEXT, block_number:INTEGER, resolved_value:INTEGER, resolved_at_utc:TEXT,
    raw_log_json:TEXT, observed_at_utc:TEXT, confirmations_count:INTEGER,
    confirmations_required:INTEGER, is_valid:INTEGER
- **validated_calibration_transfers**  (rows≈591, cols=20)
    id:INTEGER, policy_id:TEXT, source_id:TEXT, target_source_id:TEXT, source_cycle:TEXT,
    target_cycle:TEXT, horizon_profile:TEXT, season:TEXT, cluster:TEXT, metric:TEXT, n_pairs:INTEGER,
    brier_source:REAL, brier_target:REAL, brier_diff:REAL, brier_diff_threshold:REAL, status:TEXT,
    evidence_window_start:TEXT, evidence_window_end:TEXT, platt_model_key:TEXT, evaluated_at:TEXT
- **venue_command_events**  (rows≈0, cols=7)
    event_id:TEXT, command_id:TEXT, sequence_no:INTEGER, event_type:TEXT, occurred_at:TEXT,
    payload_json:TEXT, state_after:TEXT
- **venue_commands**  (rows≈4, cols=18)
    command_id:TEXT, position_id:TEXT, decision_id:TEXT, idempotency_key:TEXT, intent_kind:TEXT,
    market_id:TEXT, token_id:TEXT, side:TEXT, size:REAL, price:REAL, venue_order_id:TEXT, state:TEXT,
    last_event_id:TEXT, created_at:TEXT, updated_at:TEXT, review_required_reason:TEXT, envelope_id:TEXT,
    snapshot_id:TEXT
- **venue_order_facts**  (rows≈4, cols=13)
    fact_id:INTEGER, venue_order_id:TEXT, command_id:TEXT, state:TEXT, remaining_size:TEXT,
    matched_size:TEXT, source:TEXT, observed_at:TEXT, venue_timestamp:TEXT, ingested_at:TEXT,
    local_sequence:INTEGER, raw_payload_hash:TEXT, raw_payload_json:TEXT
- **venue_submission_envelopes**  (rows≈0, cols=33)
    envelope_id:TEXT, schema_version:INTEGER, sdk_package:TEXT, sdk_version:TEXT, host:TEXT,
    chain_id:INTEGER, funder_address:TEXT, condition_id:TEXT, question_id:TEXT, yes_token_id:TEXT,
    no_token_id:TEXT, selected_outcome_token_id:TEXT, outcome_label:TEXT, side:TEXT, price:TEXT,
    size:TEXT, order_type:TEXT, post_only:INTEGER, tick_size:TEXT, min_order_size:TEXT,
    neg_risk:INTEGER, fee_details_json:TEXT, canonical_pre_sign_payload_hash:TEXT,
    signed_order_blob:BLOB, signed_order_hash:TEXT, raw_request_hash:TEXT, raw_response_json:TEXT,
    order_id:TEXT, trade_ids_json:TEXT, transaction_hashes_json:TEXT, error_code:TEXT,
    error_message:TEXT, captured_at:TEXT
- **venue_trade_facts**  (rows≈0, cols=18)
    trade_fact_id:INTEGER, trade_id:TEXT, venue_order_id:TEXT, command_id:TEXT, state:TEXT,
    filled_size:TEXT, fill_price:TEXT, fee_paid_micro:INTEGER, tx_hash:TEXT, block_number:INTEGER,
    confirmation_count:INTEGER, source:TEXT, observed_at:TEXT, venue_timestamp:TEXT, ingested_at:TEXT,
    local_sequence:INTEGER, raw_payload_hash:TEXT, raw_payload_json:TEXT
- **wrap_unwrap_commands**  (rows≈6, cols=13)
    command_id:TEXT, state:TEXT, direction:TEXT, amount_micro:INTEGER, tx_hash:TEXT,
    block_number:INTEGER, confirmation_count:INTEGER, requested_at:TEXT, terminal_at:TEXT,
    error_payload:TEXT, first_inclusion_block_time:TEXT, finality_confirmed_time:TEXT, tx_kind:TEXT
- **wrap_unwrap_events**  (rows≈25, cols=5)
    id:INTEGER, command_id:TEXT, event_type:TEXT, payload_json:TEXT, recorded_at:TEXT
- **zeus_meta**  (rows≈1, cols=3)
    key:TEXT, value:TEXT, updated_at:TEXT

## zeus_trades.db

_75 base tables_

- **_migrations_applied**  (rows≈3, cols=2)
    name:TEXT, applied_at:TEXT
- **availability_fact**  (rows≈24390, cols=8)
    availability_id:TEXT, scope_type:TEXT, scope_key:TEXT, failure_type:TEXT, started_at:TEXT,
    ended_at:TEXT, impact:TEXT, details_json:TEXT
- **book_hash_transitions**  (rows≈-, cols=8)
    market_slug:TEXT, observed_at:TEXT, transition_seq:INTEGER, prev_hash:TEXT, new_hash:TEXT,
    delta_ms:INTEGER, cycle_id:TEXT, schema_version:INTEGER
- **calibration_decision_group**  (rows≈0, cols=13)
    group_id:TEXT, city:TEXT, target_date:TEXT, forecast_available_at:TEXT, cluster:TEXT, season:TEXT,
    lead_days:REAL, settlement_value:REAL, winning_range_label:TEXT, bias_corrected:INTEGER,
    n_pair_rows:INTEGER, n_positive_rows:INTEGER, recorded_at:TEXT
- **calibration_pairs**  (rows≈0, cols=15)
    id:INTEGER, city:TEXT, target_date:TEXT, range_label:TEXT, p_raw:REAL, outcome:INTEGER,
    lead_days:REAL, season:TEXT, cluster:TEXT, forecast_available_at:TEXT, settlement_value:REAL,
    decision_group_id:TEXT, bias_corrected:INTEGER, authority:TEXT, bin_source:TEXT
- **chronicle**  (rows≈40, cols=6)
    id:INTEGER, event_type:TEXT, trade_id:INTEGER, timestamp:TEXT, details_json:TEXT, env:TEXT
- **collateral_ledger_snapshots**  (rows≈46847, cols=11)
    id:INTEGER, pusd_balance_micro:INTEGER, pusd_allowance_micro:INTEGER,
    usdc_e_legacy_balance_micro:INTEGER, ctf_token_balances_json:TEXT, ctf_token_allowances_json:TEXT,
    reserved_pusd_for_buys_micro:INTEGER, reserved_tokens_for_sells_json:TEXT, captured_at:TEXT,
    authority_tier:TEXT, raw_balance_payload_hash:TEXT
- **collateral_reservations**  (rows≈166, cols=7)
    command_id:TEXT, reservation_type:TEXT, token_id:TEXT, amount:INTEGER, created_at:TEXT,
    released_at:TEXT, release_reason:TEXT
- **control_overrides_history**  (rows≈0, cols=13)
    history_id:INTEGER, override_id:TEXT, target_type:TEXT, target_key:TEXT, action_type:TEXT,
    value:TEXT, issued_by:TEXT, issued_at:TEXT, effective_until:TEXT, reason:TEXT, precedence:INTEGER,
    operation:TEXT, recorded_at:TEXT
- **daily_observation_revisions**  (rows≈0, cols=17)
    id:INTEGER, city:TEXT, target_date:TEXT, source:TEXT, natural_key_json:TEXT,
    existing_row_id:INTEGER, existing_combined_payload_hash:TEXT, incoming_combined_payload_hash:TEXT,
    existing_high_payload_hash:TEXT, existing_low_payload_hash:TEXT, incoming_high_payload_hash:TEXT,
    incoming_low_payload_hash:TEXT, reason:TEXT, writer:TEXT, existing_row_json:TEXT,
    incoming_row_json:TEXT, recorded_at:TEXT
- **data_coverage**  (rows≈0, cols=10)
    data_table:TEXT, city:TEXT, data_source:TEXT, target_date:TEXT, sub_key:TEXT, status:TEXT,
    reason:TEXT, fetched_at:TEXT, expected_at:TEXT, retry_after:TEXT
- **day0_metric_fact**  (rows≈0, cols=22)
    fact_id:TEXT, city:TEXT, target_date:TEXT, temperature_metric:TEXT, source:TEXT,
    local_timestamp:TEXT, utc_timestamp:TEXT, local_hour:REAL, temp_current:REAL, running_extreme:REAL,
    delta_rate_per_h:REAL, daylight_progress:REAL, obs_age_minutes:REAL, extreme_confidence:REAL,
    ens_q50_remaining_extreme:REAL, ens_q90_remaining_extreme:REAL, ens_spread:REAL,
    settlement_value:REAL, residual_to_settlement:REAL, fact_status:TEXT, missing_reason_json:TEXT,
    recorded_at:TEXT
- **decision_integrity_quarantine**  (rows≈0, cols=7)
    id:INTEGER, table_name:TEXT, row_id:TEXT, reason_code:TEXT, forecast_snapshot_id:TEXT,
    recorded_at:TEXT, meta_json:TEXT
- **decision_log**  (rows≈10534, cols=7)
    id:INTEGER, mode:TEXT, started_at:TEXT, completed_at:TEXT, artifact_json:TEXT, timestamp:TEXT,
    env:TEXT
- **diurnal_curves**  (rows≈0, cols=7)
    city:TEXT, season:TEXT, hour:INTEGER, avg_temp:REAL, std_temp:REAL, n_samples:INTEGER,
    p_high_set:REAL
- **diurnal_peak_prob**  (rows≈0, cols=5)
    city:TEXT, month:INTEGER, hour:INTEGER, p_high_set:REAL, n_obs:INTEGER
- **edli_live_order_events**  (rows≈0, cols=12)
    aggregate_event_id:TEXT, aggregate_id:TEXT, event_sequence:INTEGER, event_type:TEXT,
    parent_event_hash:TEXT, event_hash:TEXT, payload_json:TEXT, payload_hash:TEXT,
    source_authority:TEXT, occurred_at:TEXT, created_at:TEXT, schema_version:INTEGER
- **edli_live_order_projection**  (rows≈0, cols=13)
    aggregate_id:TEXT, event_id:TEXT, final_intent_id:TEXT, current_state:TEXT, last_sequence:INTEGER,
    last_event_type:TEXT, last_event_hash:TEXT, pending_reconcile:INTEGER, venue_order_id:TEXT,
    updated_at:TEXT, schema_version:INTEGER, posterior_id:INTEGER, probability_authority:TEXT
- **edli_user_channel_inbox**  (rows≈0, cols=14)
    message_hash:TEXT, source_authority:TEXT, message_type:TEXT, aggregate_id:TEXT, event_id:TEXT,
    final_intent_id:TEXT, venue_order_id:TEXT, payload_json:TEXT, occurred_at:TEXT, received_at:TEXT,
    processed_at:TEXT, processing_status:TEXT, processing_error:TEXT, schema_version:INTEGER
- **edli_user_channel_message_dedup**  (rows≈0, cols=6)
    message_hash:TEXT, aggregate_id:TEXT, venue_order_id:TEXT, message_type:TEXT, observed_at:TEXT,
    created_at:TEXT
- **ensemble_snapshots**  (rows≈0, cols=17)
    snapshot_id:INTEGER, city:TEXT, target_date:TEXT, issue_time:TEXT, valid_time:TEXT,
    available_at:TEXT, fetch_time:TEXT, lead_hours:REAL, members_json:TEXT, p_raw_json:TEXT,
    spread:REAL, is_bimodal:INTEGER, model_version:TEXT, data_version:TEXT, authority:TEXT,
    temperature_metric:TEXT, bias_corrected:INTEGER
- **exchange_reconcile_findings**  (rows≈1349, cols=9)
    finding_id:TEXT, kind:TEXT, subject_id:TEXT, context:TEXT, evidence_json:TEXT, recorded_at:TEXT,
    resolved_at:TEXT, resolution:TEXT, resolved_by:TEXT
- **executable_market_snapshots**  (rows≈-, cols=36)
    snapshot_id:TEXT, gamma_market_id:TEXT, event_id:TEXT, event_slug:TEXT, condition_id:TEXT,
    question_id:TEXT, yes_token_id:TEXT, no_token_id:TEXT, selected_outcome_token_id:TEXT,
    outcome_label:TEXT, enable_orderbook:INTEGER, active:INTEGER, closed:INTEGER,
    accepting_orders:INTEGER, market_start_at:TEXT, market_end_at:TEXT, market_close_at:TEXT,
    sports_start_at:TEXT, min_tick_size:TEXT, min_order_size:TEXT, fee_details_json:TEXT,
    token_map_json:TEXT, rfqe:INTEGER, neg_risk:INTEGER, orderbook_top_bid:TEXT, orderbook_top_ask:TEXT,
    orderbook_depth_json:TEXT, raw_gamma_payload_hash:TEXT, raw_clob_market_info_hash:TEXT,
    raw_orderbook_hash:TEXT, authority_tier:TEXT, captured_at:TEXT, freshness_deadline:TEXT,
    wide_spread_display_substitution:INTEGER, depth_at_best_ask:INTEGER, tradeability_status_json:TEXT
- **execution_fact**  (rows≈225, cols=17)
    intent_id:TEXT, position_id:TEXT, decision_id:TEXT, order_role:TEXT, strategy_key:TEXT,
    posted_at:TEXT, filled_at:TEXT, voided_at:TEXT, submitted_price:REAL, fill_price:REAL, shares:REAL,
    fill_quality:REAL, latency_seconds:REAL, venue_status:TEXT, terminal_exec_status:TEXT,
    command_id:TEXT, posterior_id:INTEGER
- **exit_mutex_holdings**  (rows≈19, cols=5)
    mutex_key:TEXT, command_id:TEXT, acquired_at:TEXT, released_at:TEXT, release_reason:TEXT
- **forecast_skill**  (rows≈0, cols=11)
    id:INTEGER, city:TEXT, target_date:TEXT, source:TEXT, lead_days:INTEGER, forecast_temp:REAL,
    actual_temp:REAL, error:REAL, temp_unit:TEXT, season:TEXT, available_at:TEXT
- **forecasts**  (rows≈0, cols=20)
    id:INTEGER, city:TEXT, target_date:TEXT, source:TEXT, forecast_basis_date:TEXT,
    forecast_issue_time:TEXT, lead_days:INTEGER, lead_time_hours:REAL, forecast_high:REAL,
    forecast_low:REAL, temp_unit:TEXT, retrieved_at:TEXT, imported_at:TEXT, source_id:TEXT,
    raw_payload_hash:TEXT, captured_at:TEXT, authority_tier:TEXT, rebuild_run_id:TEXT,
    data_source_version:TEXT, availability_provenance:TEXT
- **hourly_observations**  (rows≈0, cols=7)
    id:INTEGER, city:TEXT, obs_date:TEXT, obs_hour:INTEGER, temp:REAL, temp_unit:TEXT, source:TEXT
- **job_run**  (rows≈0, cols=25)
    job_run_id:TEXT, job_run_key:TEXT, job_name:TEXT, plane:TEXT, scheduled_for:TEXT, missed_from:TEXT,
    started_at:TEXT, finished_at:TEXT, lock_key:TEXT, lock_acquired_at:TEXT, status:TEXT,
    reason_code:TEXT, rows_written:INTEGER, rows_failed:INTEGER, source_run_id:TEXT, source_id:TEXT,
    track:TEXT, release_calendar_key:TEXT, safe_fetch_not_before:TEXT, expected_scope_json:TEXT,
    affected_scope_json:TEXT, readiness_impacts_json:TEXT, readiness_recomputed_at:TEXT, meta_json:TEXT,
    recorded_at:TEXT
- **market_events**  (rows≈0, cols=11)
    id:INTEGER, market_slug:TEXT, city:TEXT, target_date:TEXT, condition_id:TEXT, token_id:TEXT,
    range_label:TEXT, range_low:REAL, range_high:REAL, outcome:TEXT, created_at:TEXT
- **market_price_history**  (rows≈622649, cols=14)
    id:INTEGER, market_slug:TEXT, token_id:TEXT, price:REAL, recorded_at:TEXT, hours_since_open:REAL,
    hours_to_resolution:REAL, market_price_linkage:TEXT, source:TEXT, best_bid:REAL, best_ask:REAL,
    raw_orderbook_hash:TEXT, snapshot_id:TEXT, condition_id:TEXT
- **market_topology_state**  (rows≈6490, cols=24)
    topology_id:TEXT, scope_key:TEXT, market_family:TEXT, event_id:TEXT, condition_id:TEXT,
    question_id:TEXT, city_id:TEXT, city_timezone:TEXT, target_local_date:TEXT, temperature_metric:TEXT,
    physical_quantity:TEXT, observation_field:TEXT, data_version:TEXT, token_ids_json:TEXT,
    bin_topology_hash:TEXT, gamma_captured_at:TEXT, gamma_updated_at:TEXT, source_contract_status:TEXT,
    source_contract_reason:TEXT, authority_status:TEXT, status:TEXT, expires_at:TEXT,
    provenance_json:TEXT, recorded_at:TEXT
- **model_bias**  (rows≈0, cols=7)
    city:TEXT, season:TEXT, source:TEXT, bias:REAL, mae:REAL, n_samples:INTEGER, discount_factor:REAL
- **observation_instants**  (rows≈0, cols=22)
    id:INTEGER, city:TEXT, target_date:TEXT, source:TEXT, timezone_name:TEXT, local_hour:REAL,
    local_timestamp:TEXT, utc_timestamp:TEXT, utc_offset_minutes:INTEGER, dst_active:INTEGER,
    is_ambiguous_local_hour:INTEGER, is_missing_local_hour:INTEGER, time_basis:TEXT, temp_current:REAL,
    running_max:REAL, delta_rate_per_h:REAL, temp_unit:TEXT, station_id:TEXT, observation_count:INTEGER,
    raw_response:TEXT, source_file:TEXT, imported_at:TEXT
- **observation_revisions**  (rows≈0, cols=15)
    id:INTEGER, table_name:TEXT, city:TEXT, target_date:TEXT, source:TEXT, utc_timestamp:TEXT,
    natural_key_json:TEXT, existing_row_id:INTEGER, existing_payload_hash:TEXT,
    incoming_payload_hash:TEXT, reason:TEXT, writer:TEXT, existing_row_json:TEXT,
    incoming_row_json:TEXT, recorded_at:TEXT
- **observations**  (rows≈0, cols=36)
    id:INTEGER, city:TEXT, target_date:TEXT, source:TEXT, high_temp:REAL, low_temp:REAL, unit:TEXT,
    station_id:TEXT, fetched_at:TEXT, high_raw_value:REAL, high_raw_unit:TEXT, high_target_unit:TEXT,
    low_raw_value:REAL, low_raw_unit:TEXT, low_target_unit:TEXT, high_fetch_utc:TEXT,
    high_local_time:TEXT, high_collection_window_start_utc:TEXT, high_collection_window_end_utc:TEXT,
    low_fetch_utc:TEXT, low_local_time:TEXT, low_collection_window_start_utc:TEXT,
    low_collection_window_end_utc:TEXT, timezone:TEXT, utc_offset_minutes:INTEGER, dst_active:INTEGER,
    is_ambiguous_local_hour:INTEGER, is_missing_local_hour:INTEGER, hemisphere:TEXT, season:TEXT,
    month:INTEGER, rebuild_run_id:TEXT, data_source_version:TEXT, authority:TEXT,
    high_provenance_metadata:TEXT, low_provenance_metadata:TEXT
- **opportunity_fact**  (rows≈38555, cols=23)
    decision_id:TEXT, candidate_id:TEXT, city:TEXT, target_date:TEXT, range_label:TEXT, direction:TEXT,
    strategy_key:TEXT, discovery_mode:TEXT, entry_method:TEXT, snapshot_id:TEXT, p_raw:REAL, p_cal:REAL,
    p_market:REAL, alpha:REAL, best_edge:REAL, ci_width:REAL, rejection_stage:TEXT,
    rejection_reason_json:TEXT, availability_status:TEXT, should_trade:INTEGER, recorded_at:TEXT,
    observation_authority_id:TEXT, day0_context_json:TEXT
- **outcome_fact**  (rows≈40, cols=13)
    position_id:TEXT, strategy_key:TEXT, entered_at:TEXT, exited_at:TEXT, settled_at:TEXT,
    exit_reason:TEXT, admin_exit_reason:TEXT, decision_snapshot_id:TEXT, pnl:REAL, outcome:INTEGER,
    hold_duration_hours:REAL, monitor_count:INTEGER, chain_corrections_count:INTEGER
- **platt_models**  (rows≈0, cols=12)
    id:INTEGER, bucket_key:TEXT, param_A:REAL, param_B:REAL, param_C:REAL, bootstrap_params_json:TEXT,
    n_samples:INTEGER, brier_insample:REAL, fitted_at:TEXT, is_active:INTEGER, input_space:TEXT,
    authority:TEXT
- **position_current**  (rows≈154, cols=46)
    position_id:TEXT, phase:TEXT, trade_id:TEXT, market_id:TEXT, city:TEXT, cluster:TEXT,
    target_date:TEXT, bin_label:TEXT, direction:TEXT, unit:TEXT, size_usd:REAL, shares:REAL,
    cost_basis_usd:REAL, entry_price:REAL, p_posterior:REAL, last_monitor_prob:REAL,
    last_monitor_edge:REAL, last_monitor_market_price:REAL, decision_snapshot_id:TEXT,
    entry_method:TEXT, strategy_key:TEXT, edge_source:TEXT, discovery_mode:TEXT, chain_state:TEXT,
    token_id:TEXT, no_token_id:TEXT, condition_id:TEXT, order_id:TEXT, order_status:TEXT,
    updated_at:TEXT, temperature_metric:TEXT, fill_authority:TEXT, recovery_authority:TEXT,
    chain_shares:REAL, chain_seen_at:TEXT, chain_absence_at:TEXT, chain_avg_price:REAL,
    chain_cost_basis_usd:REAL, realized_pnl_usd:REAL, exit_price:REAL, settlement_price:REAL,
    settled_at:TEXT, exit_reason:TEXT, entry_ci_width:REAL, exit_retry_count:INTEGER,
    next_exit_retry_at:TEXT
- **position_events**  (rows≈26009, cols=19)
    event_id:TEXT, position_id:TEXT, event_version:INTEGER, sequence_no:INTEGER, event_type:TEXT,
    occurred_at:TEXT, phase_before:TEXT, phase_after:TEXT, strategy_key:TEXT, decision_id:TEXT,
    snapshot_id:TEXT, order_id:TEXT, command_id:TEXT, caused_by:TEXT, idempotency_key:TEXT,
    venue_status:TEXT, source_module:TEXT, payload_json:TEXT, env:TEXT
- **position_lots**  (rows≈74, cols=17)
    lot_id:INTEGER, position_id:INTEGER, state:TEXT, shares:INTEGER, entry_price_avg:TEXT,
    exit_price_avg:TEXT, source_command_id:TEXT, source_trade_fact_id:INTEGER, captured_at:TEXT,
    state_changed_at:TEXT, source:TEXT, observed_at:TEXT, venue_timestamp:TEXT, ingested_at:TEXT,
    local_sequence:INTEGER, raw_payload_hash:TEXT, raw_payload_json:TEXT
- **probability_trace_fact**  (rows≈33203, cols=34)
    trace_id:TEXT, decision_id:TEXT, decision_snapshot_id:TEXT, candidate_id:TEXT, city:TEXT,
    target_date:TEXT, range_label:TEXT, direction:TEXT, mode:TEXT, strategy_key:TEXT,
    discovery_mode:TEXT, entry_method:TEXT, selected_method:TEXT, trace_status:TEXT,
    missing_reason_json:TEXT, bin_labels_json:TEXT, p_raw_json:TEXT, p_cal_json:TEXT,
    p_market_json:TEXT, p_posterior_json:TEXT, p_posterior:REAL, alpha:REAL, agreement:TEXT,
    n_edges_found:INTEGER, n_edges_after_fdr:INTEGER, rejection_stage:TEXT, availability_status:TEXT,
    recorded_at:TEXT, market_phase:TEXT, market_phase_source:TEXT, market_start_at:TEXT,
    market_end_at:TEXT, settlement_day_entry_utc:TEXT, uma_resolved_source:TEXT
- **provenance_envelope_events**  (rows≈1395, cols=11)
    id:INTEGER, subject_type:TEXT, subject_id:TEXT, event_type:TEXT, payload_hash:TEXT,
    payload_json:TEXT, source:TEXT, observed_at:TEXT, venue_timestamp:TEXT, ingested_at:TEXT,
    local_sequence:INTEGER
- **readiness_state**  (rows≈402, cols=27)
    readiness_id:TEXT, scope_key:TEXT, scope_type:TEXT, city_id:TEXT, city:TEXT, city_timezone:TEXT,
    target_local_date:TEXT, metric:TEXT, temperature_metric:TEXT, physical_quantity:TEXT,
    observation_field:TEXT, data_version:TEXT, source_id:TEXT, track:TEXT, source_run_id:TEXT,
    market_family:TEXT, event_id:TEXT, condition_id:TEXT, token_ids_json:TEXT, strategy_key:TEXT,
    status:TEXT, reason_codes_json:TEXT, computed_at:TEXT, expires_at:TEXT, dependency_json:TEXT,
    provenance_json:TEXT, recorded_at:TEXT
- **refit_bucket_failures**  (rows≈0, cols=8)
    id:INTEGER, cluster:TEXT, season:TEXT, cycle:TEXT, source_id:TEXT, error_class:TEXT,
    error_text:TEXT, ts:TEXT
- **risk_actions**  (rows≈0, cols=10)
    action_id:TEXT, strategy_key:TEXT, action_type:TEXT, value:TEXT, issued_at:TEXT,
    effective_until:TEXT, reason:TEXT, source:TEXT, precedence:INTEGER, status:TEXT
- **selection_family_fact**  (rows≈412, cols=10)
    family_id:TEXT, cycle_mode:TEXT, decision_snapshot_id:TEXT, city:TEXT, target_date:TEXT,
    strategy_key:TEXT, discovery_mode:TEXT, created_at:TEXT, meta_json:TEXT, decision_time_status:TEXT
- **selection_hypothesis_fact**  (rows≈3029, cols=19)
    hypothesis_id:TEXT, family_id:TEXT, decision_id:TEXT, candidate_id:TEXT, city:TEXT,
    target_date:TEXT, range_label:TEXT, direction:TEXT, p_value:REAL, q_value:REAL, ci_lower:REAL,
    ci_upper:REAL, edge:REAL, tested:INTEGER, passed_prefilter:INTEGER, selected_post_fdr:INTEGER,
    rejection_stage:TEXT, recorded_at:TEXT, meta_json:TEXT
- **settlement_command_events**  (rows≈169, cols=6)
    id:INTEGER, command_id:TEXT, event_type:TEXT, payload_hash:TEXT, payload_json:TEXT, recorded_at:TEXT
- **settlement_commands**  (rows≈45, cols=20)
    command_id:TEXT, state:TEXT, condition_id:TEXT, market_id:TEXT, payout_asset:TEXT,
    pusd_amount_micro:INTEGER, token_amounts_json:TEXT, tx_hash:TEXT, block_number:INTEGER,
    confirmation_count:INTEGER, requested_at:TEXT, submitted_at:TEXT, terminal_at:TEXT,
    error_payload:TEXT, winning_index_set:TEXT, polymarket_end_anchor_source:TEXT,
    zeus_submit_intent_time:TEXT, venue_ack_time:TEXT, clock_skew_estimate_ms_at_submit:INTEGER,
    autoretry_eligible:INTEGER
- **settlement_day_observation_authority**  (rows≈56, cols=22)
    authority_id:TEXT, city:TEXT, target_date:TEXT, temperature_metric:TEXT, decision_time_utc:TEXT,
    market_phase:TEXT, source:TEXT, station_id:TEXT, observation_time_utc:TEXT,
    first_sample_time_utc:TEXT, last_sample_time_utc:TEXT, high_so_far:REAL, low_so_far:REAL,
    current_temp:REAL, sample_count:INTEGER, coverage_status:TEXT, freshness_status:TEXT,
    local_date_matches_target:INTEGER, source_authorized_for_settlement:INTEGER,
    persisted_surface_available:INTEGER, payload_json:TEXT, recorded_at:TEXT
- **settlement_schema_migrations**  (rows≈1, cols=2)
    migration_key:TEXT, applied_at:TEXT
- **settlements**  (rows≈0, cols=18)
    id:INTEGER, city:TEXT, target_date:TEXT, market_slug:TEXT, winning_bin:TEXT, settlement_value:REAL,
    settlement_source:TEXT, settled_at:TEXT, authority:TEXT, pm_bin_lo:REAL, pm_bin_hi:REAL, unit:TEXT,
    settlement_source_type:TEXT, temperature_metric:TEXT, physical_quantity:TEXT,
    observation_field:TEXT, data_version:TEXT, provenance_json:TEXT
- **shadow_signals**  (rows≈27090, cols=9)
    id:INTEGER, city:TEXT, target_date:TEXT, timestamp:TEXT, decision_snapshot_id:TEXT, p_raw_json:TEXT,
    p_cal_json:TEXT, edges_json:TEXT, lead_hours:REAL
- **solar_daily**  (rows≈0, cols=11)
    city:TEXT, target_date:TEXT, timezone:TEXT, lat:REAL, lon:REAL, sunrise_local:TEXT,
    sunset_local:TEXT, sunrise_utc:TEXT, sunset_utc:TEXT, utc_offset_minutes:INTEGER, dst_active:INTEGER
- **source_contract_audit_events**  (rows≈0, cols=21)
    audit_id:TEXT, checked_at_utc:TEXT, scan_authority:TEXT, report_status:TEXT, severity:TEXT,
    event_id:TEXT, slug:TEXT, title:TEXT, city:TEXT, target_date:TEXT, temperature_metric:TEXT,
    source_contract_status:TEXT, source_contract_reason:TEXT, configured_source_family:TEXT,
    configured_station_id:TEXT, observed_source_family:TEXT, observed_station_id:TEXT,
    resolution_sources_json:TEXT, source_contract_json:TEXT, payload_hash:TEXT, created_at:TEXT
- **source_run**  (rows≈0, cols=36)
    source_run_id:TEXT, source_id:TEXT, track:TEXT, release_calendar_key:TEXT, ingest_mode:TEXT,
    origin_mode:TEXT, source_cycle_time:TEXT, source_issue_time:TEXT, source_release_time:TEXT,
    source_available_at:TEXT, fetch_started_at:TEXT, fetch_finished_at:TEXT, captured_at:TEXT,
    imported_at:TEXT, valid_time_start:TEXT, valid_time_end:TEXT, target_local_date:TEXT, city_id:TEXT,
    city_timezone:TEXT, temperature_metric:TEXT, physical_quantity:TEXT, observation_field:TEXT,
    data_version:TEXT, expected_members:INTEGER, observed_members:INTEGER, expected_steps_json:TEXT,
    observed_steps_json:TEXT, expected_count:INTEGER, observed_count:INTEGER, completeness_status:TEXT,
    partial_run:INTEGER, raw_payload_hash:TEXT, manifest_hash:TEXT, status:TEXT, reason_code:TEXT,
    recorded_at:TEXT
- **source_run_coverage**  (rows≈0, cols=27)
    coverage_id:TEXT, source_run_id:TEXT, source_id:TEXT, source_transport:TEXT,
    release_calendar_key:TEXT, track:TEXT, city_id:TEXT, city:TEXT, city_timezone:TEXT,
    target_local_date:TEXT, temperature_metric:TEXT, physical_quantity:TEXT, observation_field:TEXT,
    data_version:TEXT, expected_members:INTEGER, observed_members:INTEGER, expected_steps_json:TEXT,
    observed_steps_json:TEXT, snapshot_ids_json:TEXT, target_window_start_utc:TEXT,
    target_window_end_utc:TEXT, completeness_status:TEXT, readiness_status:TEXT, reason_code:TEXT,
    computed_at:TEXT, expires_at:TEXT, recorded_at:TEXT
- **strategy_health**  (rows≈3, cols=13)
    strategy_key:TEXT, as_of:TEXT, open_exposure_usd:REAL, settled_trades_30d:INTEGER,
    realized_pnl_30d:REAL, unrealized_pnl:REAL, win_rate_30d:REAL, brier_30d:REAL, fill_rate_14d:REAL,
    edge_trend_30d:REAL, risk_level:TEXT, execution_decay_flag:INTEGER, edge_compression_flag:INTEGER
- **temp_persistence**  (rows≈0, cols=6)
    city:TEXT, season:TEXT, delta_bucket:TEXT, frequency:REAL, avg_next_day_reversion:REAL,
    n_samples:INTEGER
- **token_price_log**  (rows≈80047, cols=12)
    id:INTEGER, token_id:TEXT, city:TEXT, target_date:TEXT, range_label:TEXT, price:REAL, volume:REAL,
    bid:REAL, ask:REAL, spread:REAL, source_timestamp:TEXT, timestamp:TEXT
- **token_suppression**  (rows≈70, cols=7)
    token_id:TEXT, condition_id:TEXT, suppression_reason:TEXT, source_module:TEXT, created_at:TEXT,
    updated_at:TEXT, evidence_json:TEXT
- **token_suppression_history**  (rows≈15038, cols=10)
    history_id:INTEGER, token_id:TEXT, condition_id:TEXT, suppression_reason:TEXT, source_module:TEXT,
    created_at:TEXT, updated_at:TEXT, evidence_json:TEXT, operation:TEXT, recorded_at:TEXT
- **trade_decisions**  (rows≈2019, cols=49)
    trade_id:INTEGER, market_id:TEXT, bin_label:TEXT, direction:TEXT, size_usd:REAL, price:REAL,
    timestamp:TEXT, forecast_snapshot_id:INTEGER, calibration_model_version:TEXT, p_raw:REAL,
    p_calibrated:REAL, p_posterior:REAL, edge:REAL, ci_lower:REAL, ci_upper:REAL, kelly_fraction:REAL,
    status:TEXT, filled_at:TEXT, fill_price:REAL, runtime_trade_id:TEXT, order_id:TEXT,
    order_status_text:TEXT, order_posted_at:TEXT, entered_at_ts:TEXT, chain_state:TEXT, strategy:TEXT,
    edge_source:TEXT, bin_type:TEXT, discovery_mode:TEXT, market_hours_open:REAL, fill_quality:REAL,
    entry_method:TEXT, selected_method:TEXT, applied_validations_json:TEXT, exit_trigger:TEXT,
    exit_reason:TEXT, admin_exit_reason:TEXT, exit_divergence_score:REAL, exit_market_velocity_1h:REAL,
    exit_forward_edge:REAL, settlement_semantics_json:TEXT, epistemic_context_json:TEXT,
    edge_context_json:TEXT, entry_alpha_usd:REAL, execution_slippage_usd:REAL, exit_timing_usd:REAL,
    risk_throttling_usd:REAL, settlement_edge_usd:REAL, env:TEXT
- **uma_resolution**  (rows≈0, cols=10)
    condition_id:TEXT, tx_hash:TEXT, block_number:INTEGER, resolved_value:INTEGER, resolved_at_utc:TEXT,
    raw_log_json:TEXT, observed_at_utc:TEXT, confirmations_count:INTEGER,
    confirmations_required:INTEGER, is_valid:INTEGER
- **validated_calibration_transfers**  (rows≈0, cols=20)
    id:INTEGER, policy_id:TEXT, source_id:TEXT, target_source_id:TEXT, source_cycle:TEXT,
    target_cycle:TEXT, horizon_profile:TEXT, season:TEXT, cluster:TEXT, metric:TEXT, n_pairs:INTEGER,
    brier_source:REAL, brier_target:REAL, brier_diff:REAL, brier_diff_threshold:REAL, status:TEXT,
    evidence_window_start:TEXT, evidence_window_end:TEXT, platt_model_key:TEXT, evaluated_at:TEXT
- **venue_command_events**  (rows≈202, cols=7)
    event_id:TEXT, command_id:TEXT, sequence_no:INTEGER, event_type:TEXT, occurred_at:TEXT,
    payload_json:TEXT, state_after:TEXT
- **venue_commands**  (rows≈50, cols=18)
    command_id:TEXT, snapshot_id:TEXT, envelope_id:TEXT, position_id:TEXT, decision_id:TEXT,
    idempotency_key:TEXT, intent_kind:TEXT, market_id:TEXT, token_id:TEXT, side:TEXT, size:REAL,
    price:REAL, venue_order_id:TEXT, state:TEXT, last_event_id:TEXT, created_at:TEXT, updated_at:TEXT,
    review_required_reason:TEXT
- **venue_order_facts**  (rows≈54, cols=13)
    fact_id:INTEGER, venue_order_id:TEXT, command_id:TEXT, state:TEXT, remaining_size:TEXT,
    matched_size:TEXT, source:TEXT, observed_at:TEXT, venue_timestamp:TEXT, ingested_at:TEXT,
    local_sequence:INTEGER, raw_payload_hash:TEXT, raw_payload_json:TEXT
- **venue_submission_envelopes**  (rows≈387, cols=33)
    envelope_id:TEXT, schema_version:INTEGER, sdk_package:TEXT, sdk_version:TEXT, host:TEXT,
    chain_id:INTEGER, funder_address:TEXT, condition_id:TEXT, question_id:TEXT, yes_token_id:TEXT,
    no_token_id:TEXT, selected_outcome_token_id:TEXT, outcome_label:TEXT, side:TEXT, price:TEXT,
    size:TEXT, order_type:TEXT, post_only:INTEGER, tick_size:TEXT, min_order_size:TEXT,
    neg_risk:INTEGER, fee_details_json:TEXT, canonical_pre_sign_payload_hash:TEXT,
    signed_order_blob:BLOB, signed_order_hash:TEXT, raw_request_hash:TEXT, raw_response_json:TEXT,
    order_id:TEXT, trade_ids_json:TEXT, transaction_hashes_json:TEXT, error_code:TEXT,
    error_message:TEXT, captured_at:TEXT
- **venue_trade_facts**  (rows≈95, cols=18)
    trade_fact_id:INTEGER, trade_id:TEXT, venue_order_id:TEXT, command_id:TEXT, state:TEXT,
    filled_size:TEXT, fill_price:TEXT, fee_paid_micro:INTEGER, tx_hash:TEXT, block_number:INTEGER,
    confirmation_count:INTEGER, source:TEXT, observed_at:TEXT, venue_timestamp:TEXT, ingested_at:TEXT,
    local_sequence:INTEGER, raw_payload_hash:TEXT, raw_payload_json:TEXT
- **wrap_unwrap_commands**  (rows≈0, cols=12)
    command_id:TEXT, state:TEXT, direction:TEXT, amount_micro:INTEGER, tx_hash:TEXT,
    block_number:INTEGER, confirmation_count:INTEGER, requested_at:TEXT, terminal_at:TEXT,
    error_payload:TEXT, first_inclusion_block_time:TEXT, finality_confirmed_time:TEXT
- **wrap_unwrap_events**  (rows≈0, cols=5)
    id:INTEGER, command_id:TEXT, event_type:TEXT, payload_json:TEXT, recorded_at:TEXT
- **zeus_meta**  (rows≈1, cols=3)
    key:TEXT, value:TEXT, updated_at:TEXT

## zeus-forecasts.db

_116 base tables_

- **_migrations_applied**  (rows≈1, cols=2)
    name:TEXT, applied_at:TEXT
- **availability_fact**  (rows≈0, cols=8)
    availability_id:TEXT, scope_type:TEXT, scope_key:TEXT, failure_type:TEXT, started_at:TEXT,
    ended_at:TEXT, impact:TEXT, details_json:TEXT
- **book_hash_transitions**  (rows≈0, cols=8)
    market_slug:TEXT, observed_at:TEXT, transition_seq:INTEGER, prev_hash:TEXT, new_hash:TEXT,
    delta_ms:INTEGER, cycle_id:TEXT, schema_version:INTEGER
- **calibration_decision_group**  (rows≈0, cols=13)
    group_id:TEXT, city:TEXT, target_date:TEXT, forecast_available_at:TEXT, cluster:TEXT, season:TEXT,
    lead_days:REAL, settlement_value:REAL, winning_range_label:TEXT, bias_corrected:INTEGER,
    n_pair_rows:INTEGER, n_positive_rows:INTEGER, recorded_at:TEXT
- **calibration_pairs**  (rows≈-, cols=26)
    pair_id:INTEGER, city:TEXT, target_date:TEXT, temperature_metric:TEXT, observation_field:TEXT,
    range_label:TEXT, p_raw:REAL, outcome:INTEGER, lead_days:REAL, season:TEXT, cluster:TEXT,
    forecast_available_at:TEXT, settlement_value:REAL, decision_group_id:TEXT, bias_corrected:INTEGER,
    authority:TEXT, bin_source:TEXT, snapshot_id:INTEGER, dataset_id:TEXT, training_allowed:INTEGER,
    causality_status:TEXT, recorded_at:TEXT, cycle:TEXT, source_id:TEXT, horizon_profile:TEXT,
    error_model_family:TEXT
- **chronicle**  (rows≈0, cols=6)
    id:INTEGER, event_type:TEXT, trade_id:INTEGER, timestamp:TEXT, details_json:TEXT, env:TEXT
- **collateral_ledger_snapshots**  (rows≈0, cols=11)
    id:INTEGER, pusd_balance_micro:INTEGER, pusd_allowance_micro:INTEGER,
    usdc_e_legacy_balance_micro:INTEGER, ctf_token_balances_json:TEXT, ctf_token_allowances_json:TEXT,
    reserved_pusd_for_buys_micro:INTEGER, reserved_tokens_for_sells_json:TEXT, captured_at:TEXT,
    authority_tier:TEXT, raw_balance_payload_hash:TEXT
- **collateral_reservations**  (rows≈0, cols=7)
    command_id:TEXT, reservation_type:TEXT, token_id:TEXT, amount:INTEGER, created_at:TEXT,
    released_at:TEXT, release_reason:TEXT
- **control_overrides_history**  (rows≈0, cols=13)
    history_id:INTEGER, override_id:TEXT, target_type:TEXT, target_key:TEXT, action_type:TEXT,
    value:TEXT, issued_by:TEXT, issued_at:TEXT, effective_until:TEXT, reason:TEXT, precedence:INTEGER,
    operation:TEXT, recorded_at:TEXT
- **cycle_advance_enqueues**  (rows≈0, cols=9)
    enqueue_id:INTEGER, enqueued_at:TEXT, city:TEXT, target_date:TEXT, metric:TEXT,
    consumed_cycle_time:TEXT, target_cycle_time:TEXT, held_position:INTEGER, seed_file:TEXT
- **daily_observation_revisions**  (rows≈0, cols=17)
    id:INTEGER, city:TEXT, target_date:TEXT, source:TEXT, natural_key_json:TEXT,
    existing_row_id:INTEGER, existing_combined_payload_hash:TEXT, incoming_combined_payload_hash:TEXT,
    existing_high_payload_hash:TEXT, existing_low_payload_hash:TEXT, incoming_high_payload_hash:TEXT,
    incoming_low_payload_hash:TEXT, reason:TEXT, writer:TEXT, existing_row_json:TEXT,
    incoming_row_json:TEXT, recorded_at:TEXT
- **data_coverage**  (rows≈794, cols=10)
    data_table:TEXT, city:TEXT, data_source:TEXT, target_date:TEXT, sub_key:TEXT, status:TEXT,
    reason:TEXT, fetched_at:TEXT, expected_at:TEXT, retry_after:TEXT
- **day0_horizon_platt_fits**  (rows≈0, cols=15)
    fit_run_id:TEXT, fit_version:TEXT, alpha:REAL, beta:REAL, gamma_morning:REAL, gamma_afternoon:REAL,
    gamma_post_peak:REAL, delta:REAL, epsilon:REAL, fit_date:TEXT, n_obs:INTEGER,
    sample_period_start:TEXT, sample_period_end:TEXT, schema_version:INTEGER, source:TEXT
- **day0_hourly_vectors**  (rows≈3020, cols=12)
    vector_id:TEXT, model:TEXT, city:TEXT, target_date:TEXT, timezone_name:TEXT, captured_at:TEXT,
    provider:TEXT, endpoint:TEXT, request_hash:TEXT, times_json:TEXT, temps_c_json:TEXT,
    source_run_meta_json:TEXT
- **day0_metric_fact**  (rows≈0, cols=22)
    fact_id:TEXT, city:TEXT, target_date:TEXT, temperature_metric:TEXT, source:TEXT,
    local_timestamp:TEXT, utc_timestamp:TEXT, local_hour:REAL, temp_current:REAL, running_extreme:REAL,
    delta_rate_per_h:REAL, daylight_progress:REAL, obs_age_minutes:REAL, extreme_confidence:REAL,
    ens_q50_remaining_extreme:REAL, ens_q90_remaining_extreme:REAL, ens_spread:REAL,
    settlement_value:REAL, residual_to_settlement:REAL, fact_status:TEXT, missing_reason_json:TEXT,
    recorded_at:TEXT
- **day0_nowcast_runs**  (rows≈0, cols=18)
    market_slug:TEXT, temperature_metric:TEXT, target_date:TEXT, observation_time:TEXT, run_seq:INTEGER,
    nowcast_event_id:TEXT, fit_run_id:TEXT, p_nowcast_json:TEXT, p_now_raw_json:TEXT,
    hours_remaining:REAL, daypart:TEXT, schema_version:INTEGER, source:TEXT, bin_grid_id:TEXT,
    bin_schema_version:TEXT, bin_schema_id:TEXT, observation_available_at:TEXT,
    obs_availability_provenance:TEXT
- **db_chunk_boundary_events**  (rows≈0, cols=7)
    event_id:TEXT, occurred_at:TEXT, caller_module:TEXT, db_path:TEXT, rows_processed:INTEGER,
    duration_ms:INTEGER, split_reason:TEXT
- **decision_certificate_edges**  (rows≈0, cols=6)
    child_certificate_id:TEXT, parent_role:TEXT, parent_certificate_hash:TEXT,
    parent_certificate_type:TEXT, required:INTEGER, created_at:TEXT
- **decision_certificate_supersessions**  (rows≈0, cols=5)
    supersession_id:TEXT, old_certificate_hash:TEXT, new_certificate_hash:TEXT, reason:TEXT,
    created_at:TEXT
- **decision_certificates**  (rows≈0, cols=25)
    certificate_id:TEXT, certificate_type:TEXT, schema_version:INTEGER, canonicalization_version:TEXT,
    semantic_key:TEXT, claim_type:TEXT, mode:TEXT, decision_time:TEXT, source_available_at:TEXT,
    agent_received_at:TEXT, persisted_at:TEXT, max_parent_source_available_at:TEXT,
    max_parent_agent_received_at:TEXT, max_parent_persisted_at:TEXT, authority_id:TEXT,
    authority_version:TEXT, algorithm_id:TEXT, algorithm_version:TEXT, config_hash:TEXT,
    model_version_hash:TEXT, payload_json:TEXT, payload_hash:TEXT, certificate_hash:TEXT,
    verifier_status:TEXT, created_at:TEXT
- **decision_compile_failures**  (rows≈0, cols=10)
    failure_id:TEXT, event_id:TEXT, decision_time:TEXT, mode:TEXT, claim_type:TEXT, stage:TEXT,
    reason_code:TEXT, reason_detail:TEXT, parent_hashes_json:TEXT, created_at:TEXT
- **decision_events**  (rows≈0, cols=31)
    market_slug:TEXT, temperature_metric:TEXT, target_date:TEXT, observation_time:TEXT,
    decision_seq:INTEGER, condition_id:TEXT, decision_event_id:TEXT, decision_time:TEXT, outcome:TEXT,
    side:TEXT, strategy_key:TEXT, cycle_id:TEXT, cycle_iteration:INTEGER, p_posterior:REAL, edge:REAL,
    target_size_usd:REAL, target_price:REAL, forecast_time:TEXT, provider_reported_time:TEXT,
    observation_available_at:TEXT, polymarket_end_anchor_source:TEXT, first_member_observed_time:TEXT,
    run_complete_time:TEXT, zeus_submit_intent_time:TEXT, venue_ack_time:TEXT,
    first_inclusion_block_time:TEXT, finality_confirmed_time:TEXT,
    clock_skew_estimate_ms_at_submit:INTEGER, raw_orderbook_hash_transition_delta_ms:INTEGER,
    schema_version:INTEGER, source:TEXT
- **decision_log**  (rows≈0, cols=7)
    id:INTEGER, mode:TEXT, started_at:TEXT, completed_at:TEXT, artifact_json:TEXT, timestamp:TEXT,
    env:TEXT
- **deterministic_forecast_anchors**  (rows≈1187, cols=22)
    anchor_id:INTEGER, source_id:TEXT, product_id:TEXT, data_version:TEXT, city:TEXT, target_date:TEXT,
    temperature_metric:TEXT, anchor_value_c:REAL, source_cycle_time:TEXT, source_available_at:TEXT,
    captured_at:TEXT, artifact_id:INTEGER, model:TEXT, native_grid:TEXT, delivery_grid_resolution:TEXT,
    interpolation_method:TEXT, contributing_times_json:TEXT, provenance_json:TEXT,
    trade_authority_status:TEXT, training_allowed:INTEGER, recorded_at:TEXT, anchor_identity_hash:TEXT
- **deterministic_forecast_anchors_legacy_coarse_unique_20260607T131448Z**  (rows≈306, cols=21)
    anchor_id:INTEGER, source_id:TEXT, product_id:TEXT, data_version:TEXT, city:TEXT, target_date:TEXT,
    temperature_metric:TEXT, anchor_value_c:REAL, source_cycle_time:TEXT, source_available_at:TEXT,
    captured_at:TEXT, artifact_id:INTEGER, model:TEXT, native_grid:TEXT, delivery_grid_resolution:TEXT,
    interpolation_method:TEXT, contributing_times_json:TEXT, provenance_json:TEXT,
    trade_authority_status:TEXT, training_allowed:INTEGER, recorded_at:TEXT
- **diurnal_curves**  (rows≈0, cols=7)
    city:TEXT, season:TEXT, hour:INTEGER, avg_temp:REAL, std_temp:REAL, n_samples:INTEGER,
    p_high_set:REAL
- **diurnal_peak_prob**  (rows≈0, cols=5)
    city:TEXT, month:INTEGER, hour:INTEGER, p_high_set:REAL, n_obs:INTEGER
- **edli_live_cap_day_slots**  (rows≈0, cols=7)
    cap_scope:TEXT, cap_date:TEXT, slot:INTEGER, usage_id:TEXT, event_id:TEXT, created_at:TEXT,
    schema_version:INTEGER
- **edli_live_cap_usage**  (rows≈0, cols=13)
    usage_id:TEXT, event_id:TEXT, decision_time:TEXT, cap_scope:TEXT, max_notional_usd:REAL,
    max_orders_per_day:INTEGER, reserved_notional_usd:REAL, order_count:INTEGER,
    reservation_status:TEXT, final_intent_id:TEXT, execution_command_id:TEXT, created_at:TEXT,
    schema_version:INTEGER
- **edli_live_order_events**  (rows≈0, cols=12)
    aggregate_event_id:TEXT, aggregate_id:TEXT, event_sequence:INTEGER, event_type:TEXT,
    parent_event_hash:TEXT, event_hash:TEXT, payload_json:TEXT, payload_hash:TEXT,
    source_authority:TEXT, occurred_at:TEXT, created_at:TEXT, schema_version:INTEGER
- **edli_live_order_projection**  (rows≈0, cols=11)
    aggregate_id:TEXT, event_id:TEXT, final_intent_id:TEXT, current_state:TEXT, last_sequence:INTEGER,
    last_event_type:TEXT, last_event_hash:TEXT, pending_reconcile:INTEGER, venue_order_id:TEXT,
    updated_at:TEXT, schema_version:INTEGER
- **edli_live_profit_audit**  (rows≈0, cols=45)
    audit_id:TEXT, event_id:TEXT, aggregate_id:TEXT, final_intent_id:TEXT, execution_command_id:TEXT,
    condition_id:TEXT, token_id:TEXT, direction:TEXT, side:TEXT, q_live:REAL, q_lcb_5pct:REAL,
    expected_cost_basis:REAL, expected_fee:REAL, expected_spread_cost:REAL, visible_depth_fill_lcb:REAL,
    order_policy:TEXT, native_token_side:TEXT, expected_edge:REAL, kelly_size_usd:REAL,
    live_cap_notional:REAL, quote_seen_at:TEXT, quote_age_ms:INTEGER, best_bid:REAL, best_ask:REAL,
    limit_price:REAL, order_type:TEXT, time_in_force:TEXT, venue_order_id:TEXT,
    order_lifecycle_state:TEXT, avg_fill_price:REAL, filled_size:REAL, fees:REAL, post_fill_mark:REAL,
    settlement_outcome:TEXT, realized_edge:REAL, edge_value_usd:REAL, pnl_usd:REAL, reject_reason:TEXT,
    expected_edge_source_certificate_hash:TEXT, cost_basis_source_certificate_hash:TEXT,
    fill_source_event_hash:TEXT, settlement_source_event_hash:TEXT, promotion_eligible:INTEGER,
    created_at:TEXT, schema_version:INTEGER
- **edli_no_submit_receipts**  (rows≈0, cols=29)
    receipt_id:TEXT, event_id:TEXT, causal_snapshot_id:TEXT, decision_time:TEXT, family_id:TEXT,
    candidate_id:TEXT, condition_id:TEXT, token_id:TEXT, direction:TEXT, executable_snapshot_id:TEXT,
    final_intent_id:TEXT, side_effect_status:TEXT, q_live:REAL, q_lcb_5pct:REAL, c_fee_adjusted:REAL,
    c_cost_95pct:REAL, p_fill_lcb:REAL, trade_score:REAL, fdr_family_id:TEXT,
    fdr_hypothesis_count:INTEGER, kelly_cost_basis_id:TEXT, kelly_decision_id:TEXT,
    risk_decision_id:TEXT, kelly_size_usd:REAL, projection_hash:TEXT, receipt_json:TEXT,
    receipt_hash:TEXT, created_at:TEXT, schema_version:INTEGER
- **edli_user_channel_inbox**  (rows≈0, cols=14)
    message_hash:TEXT, source_authority:TEXT, message_type:TEXT, aggregate_id:TEXT, event_id:TEXT,
    final_intent_id:TEXT, venue_order_id:TEXT, payload_json:TEXT, occurred_at:TEXT, received_at:TEXT,
    processed_at:TEXT, processing_status:TEXT, processing_error:TEXT, schema_version:INTEGER
- **edli_user_channel_message_dedup**  (rows≈0, cols=6)
    message_hash:TEXT, aggregate_id:TEXT, venue_order_id:TEXT, message_type:TEXT, observed_at:TEXT,
    created_at:TEXT
- **ensemble_snapshots**  (rows≈-, cols=57)
    snapshot_id:INTEGER, city:TEXT, target_date:TEXT, temperature_metric:TEXT, physical_quantity:TEXT,
    observation_field:TEXT, issue_time:TEXT, valid_time:TEXT, available_at:TEXT, fetch_time:TEXT,
    lead_hours:REAL, members_json:TEXT, p_raw_json:TEXT, spread:REAL, is_bimodal:INTEGER,
    model_version:TEXT, dataset_id:TEXT, training_allowed:INTEGER, causality_status:TEXT,
    boundary_ambiguous:INTEGER, ambiguous_member_count:INTEGER, manifest_hash:TEXT,
    provenance_json:TEXT, authority:TEXT, recorded_at:TEXT, members_unit:TEXT, members_precision:REAL,
    local_day_start_utc:TEXT, step_horizon_hours:REAL, unit:TEXT, source_id:TEXT, source_transport:TEXT,
    source_run_id:TEXT, release_calendar_key:TEXT, source_cycle_time:TEXT, source_release_time:TEXT,
    source_available_at:TEXT, city_timezone:TEXT, settlement_source_type:TEXT,
    settlement_station_id:TEXT, settlement_unit:TEXT, settlement_rounding_policy:TEXT, bin_grid_id:TEXT,
    bin_schema_version:TEXT, forecast_window_start_utc:TEXT, forecast_window_end_utc:TEXT,
    forecast_window_start_local:TEXT, forecast_window_end_local:TEXT,
    forecast_window_local_day_overlap_hours:REAL, forecast_window_attribution_status:TEXT,
    contributes_to_target_extrema:INTEGER, forecast_window_block_reasons_json:TEXT, ingest_backend:TEXT,
    first_member_observed_time:TEXT, run_complete_time:TEXT,
    raw_orderbook_hash_transition_delta_ms:INTEGER, bin_schema_id:TEXT
- **event_dead_letters**  (rows≈0, cols=8)
    dead_letter_id:TEXT, consumer_name:TEXT, event_id:TEXT, failure_stage:TEXT, error_message:TEXT,
    event_payload_json:TEXT, created_at:TEXT, schema_version:INTEGER
- **evidence_tier_assignments**  (rows≈0, cols=15)
    id:INTEGER, strategy_id:TEXT, tier:INTEGER, assigned_at:TEXT, rationale:TEXT, operator_ref:TEXT,
    verdict_reason:TEXT, schema_version:INTEGER, assignment_source:TEXT, verdict_kind:TEXT,
    effective_from:TEXT, effective_until:TEXT, revoked_at:TEXT, revoked_by:TEXT,
    supersedes_assignment_id:INTEGER
- **evidence_tier_assignments_new**  (rows≈0, cols=15)
    id:INTEGER, strategy_id:TEXT, tier:INTEGER, assigned_at:TEXT, rationale:TEXT, operator_ref:TEXT,
    verdict_reason:TEXT, schema_version:INTEGER, assignment_source:TEXT, verdict_kind:TEXT,
    effective_from:TEXT, effective_until:TEXT, revoked_at:TEXT, revoked_by:TEXT,
    supersedes_assignment_id:INTEGER
- **exchange_reconcile_findings**  (rows≈0, cols=9)
    finding_id:TEXT, kind:TEXT, subject_id:TEXT, context:TEXT, evidence_json:TEXT, recorded_at:TEXT,
    resolved_at:TEXT, resolution:TEXT, resolved_by:TEXT
- **executable_market_snapshots**  (rows≈0, cols=36)
    snapshot_id:TEXT, gamma_market_id:TEXT, event_id:TEXT, event_slug:TEXT, condition_id:TEXT,
    question_id:TEXT, yes_token_id:TEXT, no_token_id:TEXT, selected_outcome_token_id:TEXT,
    outcome_label:TEXT, enable_orderbook:INTEGER, active:INTEGER, closed:INTEGER,
    accepting_orders:INTEGER, market_start_at:TEXT, market_end_at:TEXT, market_close_at:TEXT,
    sports_start_at:TEXT, min_tick_size:TEXT, min_order_size:TEXT, fee_details_json:TEXT,
    token_map_json:TEXT, rfqe:INTEGER, neg_risk:INTEGER, orderbook_top_bid:TEXT, orderbook_top_ask:TEXT,
    orderbook_depth_json:TEXT, raw_gamma_payload_hash:TEXT, raw_clob_market_info_hash:TEXT,
    raw_orderbook_hash:TEXT, authority_tier:TEXT, captured_at:TEXT, freshness_deadline:TEXT,
    tradeability_status_json:TEXT, wide_spread_display_substitution:INTEGER, depth_at_best_ask:INTEGER
- **execution_fact**  (rows≈0, cols=16)
    intent_id:TEXT, position_id:TEXT, decision_id:TEXT, order_role:TEXT, strategy_key:TEXT,
    posted_at:TEXT, filled_at:TEXT, voided_at:TEXT, submitted_price:REAL, fill_price:REAL, shares:REAL,
    fill_quality:REAL, latency_seconds:REAL, venue_status:TEXT, terminal_exec_status:TEXT,
    command_id:TEXT
- **execution_feasibility_evidence**  (rows≈0, cols=26)
    evidence_id:TEXT, event_id:TEXT, condition_id:TEXT, token_id:TEXT, outcome_label:TEXT,
    direction:TEXT, quote_seen_at:TEXT, book_hash_before:TEXT, best_bid_before:REAL,
    best_ask_before:REAL, depth_before_json:TEXT, order_intent_time:TEXT, submit_time:TEXT,
    accepted_or_rejected:TEXT, venue_order_id:TEXT, fok_full_fill:INTEGER, fak_partial_fill:INTEGER,
    filled_shares:REAL, fill_price:REAL, cancel_remainder_status:TEXT, book_hash_after:TEXT,
    latency_ms:INTEGER, maker_cancel_before_submit:INTEGER, would_have_edge_after_fee:INTEGER,
    created_at:TEXT, schema_version:INTEGER
- **exit_mutex_holdings**  (rows≈0, cols=5)
    mutex_key:TEXT, command_id:TEXT, acquired_at:TEXT, released_at:TEXT, release_reason:TEXT
- **forecast_posteriors**  (rows≈2500, cols=26)
    posterior_id:INTEGER, source_id:TEXT, product_id:TEXT, data_version:TEXT, city:TEXT,
    target_date:TEXT, temperature_metric:TEXT, source_cycle_time:TEXT, source_available_at:TEXT,
    computed_at:TEXT, q_json:TEXT, q_lcb_json:TEXT, q_ucb_json:TEXT, posterior_method:TEXT,
    aifs_source_run_id:TEXT, openmeteo_anchor_id:INTEGER, dependency_source_run_ids_json:TEXT,
    family_id:TEXT, bin_topology_hash:TEXT, dependency_hash:TEXT, posterior_config_hash:TEXT,
    posterior_identity_hash:TEXT, provenance_json:TEXT, trade_authority_status:TEXT,
    training_allowed:INTEGER, recorded_at:TEXT
- **forecast_posteriors_legacy_coarse_unique_20260607T131448Z**  (rows≈306, cols=20)
    posterior_id:INTEGER, source_id:TEXT, product_id:TEXT, data_version:TEXT, city:TEXT,
    target_date:TEXT, temperature_metric:TEXT, source_cycle_time:TEXT, source_available_at:TEXT,
    computed_at:TEXT, q_json:TEXT, q_lcb_json:TEXT, posterior_method:TEXT, aifs_source_run_id:TEXT,
    openmeteo_anchor_id:INTEGER, dependency_source_run_ids_json:TEXT, provenance_json:TEXT,
    trade_authority_status:TEXT, training_allowed:INTEGER, recorded_at:TEXT
- **forecast_skill**  (rows≈0, cols=11)
    id:INTEGER, city:TEXT, target_date:TEXT, source:TEXT, lead_days:INTEGER, forecast_temp:REAL,
    actual_temp:REAL, error:REAL, temp_unit:TEXT, season:TEXT, available_at:TEXT
- **forecasts**  (rows≈0, cols=20)
    id:INTEGER, city:TEXT, target_date:TEXT, source:TEXT, forecast_basis_date:TEXT,
    forecast_issue_time:TEXT, lead_days:INTEGER, lead_time_hours:REAL, forecast_high:REAL,
    forecast_low:REAL, temp_unit:TEXT, retrieved_at:TEXT, imported_at:TEXT, source_id:TEXT,
    raw_payload_hash:TEXT, captured_at:TEXT, authority_tier:TEXT, rebuild_run_id:TEXT,
    data_source_version:TEXT, availability_provenance:TEXT
- **fusion_upgrade_enqueues**  (rows≈96, cols=9)
    enqueue_id:INTEGER, enqueued_at:TEXT, city:TEXT, target_date:TEXT, metric:TEXT,
    source_cycle_time:TEXT, served_family_set:TEXT, capturable_family_set:TEXT, seed_file:TEXT
- **hko_hourly_accumulator**  (rows≈467, cols=4)
    target_date:TEXT, hour_utc:TEXT, temperature:REAL, fetched_at:TEXT
- **job_run**  (rows≈108, cols=25)
    job_run_id:TEXT, job_run_key:TEXT, job_name:TEXT, plane:TEXT, scheduled_for:TEXT, missed_from:TEXT,
    started_at:TEXT, finished_at:TEXT, lock_key:TEXT, lock_acquired_at:TEXT, status:TEXT,
    reason_code:TEXT, rows_written:INTEGER, rows_failed:INTEGER, source_run_id:TEXT, source_id:TEXT,
    track:TEXT, release_calendar_key:TEXT, safe_fetch_not_before:TEXT, expected_scope_json:TEXT,
    affected_scope_json:TEXT, readiness_impacts_json:TEXT, readiness_recomputed_at:TEXT, meta_json:TEXT,
    recorded_at:TEXT
- **market_events**  (rows≈-, cols=13)
    event_id:INTEGER, market_slug:TEXT, city:TEXT, target_date:TEXT, temperature_metric:TEXT,
    condition_id:TEXT, token_id:TEXT, range_label:TEXT, range_low:REAL, range_high:REAL, outcome:TEXT,
    created_at:TEXT, recorded_at:TEXT
- **market_microstructure_snapshots**  (rows≈0, cols=13)
    id:INTEGER, snapshot_id:TEXT, event_slug:TEXT, condition_id:TEXT, captured_at_iso:TEXT,
    wide_spread_display_substitution:INTEGER, spread_observed_window_ms:INTEGER,
    depth_at_best_ask:INTEGER, polymarket_end_anchor_source:TEXT, bin_grid_id:TEXT,
    bin_schema_version:TEXT, schema_version:INTEGER, recorded_at:TEXT
- **market_price_history**  (rows≈10621, cols=14)
    id:INTEGER, market_slug:TEXT, token_id:TEXT, price:REAL, recorded_at:TEXT, hours_since_open:REAL,
    hours_to_resolution:REAL, market_price_linkage:TEXT, source:TEXT, best_bid:REAL, best_ask:REAL,
    raw_orderbook_hash:TEXT, snapshot_id:TEXT, condition_id:TEXT
- **market_topology_state**  (rows≈0, cols=24)
    topology_id:TEXT, scope_key:TEXT, market_family:TEXT, event_id:TEXT, condition_id:TEXT,
    question_id:TEXT, city_id:TEXT, city_timezone:TEXT, target_local_date:TEXT, temperature_metric:TEXT,
    physical_quantity:TEXT, observation_field:TEXT, data_version:TEXT, token_ids_json:TEXT,
    bin_topology_hash:TEXT, gamma_captured_at:TEXT, gamma_updated_at:TEXT, source_contract_status:TEXT,
    source_contract_reason:TEXT, authority_status:TEXT, status:TEXT, expires_at:TEXT,
    provenance_json:TEXT, recorded_at:TEXT
- **model_bias**  (rows≈0, cols=7)
    city:TEXT, season:TEXT, source:TEXT, bias:REAL, mae:REAL, n_samples:INTEGER, discount_factor:REAL
- **no_trade_events**  (rows≈0, cols=13)
    market_slug:TEXT, temperature_metric:TEXT, target_date:TEXT, observation_time:TEXT,
    decision_seq:INTEGER, reason:TEXT, reason_detail:TEXT, strategy_key:TEXT, event_source:TEXT,
    shadow_runtime:INTEGER, observed_at:TEXT, schema_version:INTEGER, schema_compatibility:TEXT
- **no_trade_regret_events**  (rows≈0, cols=35)
    regret_event_id:TEXT, event_id:TEXT, rejection_stage:TEXT, rejection_reason:TEXT,
    regret_bucket:TEXT, market_slug:TEXT, condition_id:TEXT, token_id:TEXT, outcome_label:TEXT,
    decision_time:TEXT, city:TEXT, target_date:TEXT, metric:TEXT, family_id:TEXT, bin_label:TEXT,
    direction:TEXT, q_live:REAL, q_lcb_5pct:REAL, c_fee_adjusted:REAL, c_cost_95pct:REAL,
    p_fill_lcb:REAL, trade_score:REAL, native_quote_available:INTEGER, source_status:TEXT,
    family_complete:INTEGER, hypothetical_order_type:TEXT, hypothetical_fill_status:TEXT,
    hypothetical_fill_price:REAL, causal_snapshot_id:TEXT, executable_snapshot_id:TEXT,
    later_outcome:TEXT, would_have_won:INTEGER, would_have_filled:INTEGER, created_at:TEXT,
    schema_version:INTEGER
- **observation_instants**  (rows≈0, cols=31)
    id:INTEGER, city:TEXT, target_date:TEXT, source:TEXT, timezone_name:TEXT, local_hour:REAL,
    local_timestamp:TEXT, utc_timestamp:TEXT, utc_offset_minutes:INTEGER, dst_active:INTEGER,
    is_ambiguous_local_hour:INTEGER, is_missing_local_hour:INTEGER, time_basis:TEXT, temp_current:REAL,
    running_max:REAL, delta_rate_per_h:REAL, temp_unit:TEXT, station_id:TEXT, observation_count:INTEGER,
    raw_response:TEXT, source_file:TEXT, imported_at:TEXT, authority:TEXT, data_version:TEXT,
    provenance_json:TEXT, temperature_metric:TEXT, physical_quantity:TEXT, observation_field:TEXT,
    training_allowed:INTEGER, causality_status:TEXT, source_role:TEXT
- **observation_revisions**  (rows≈0, cols=15)
    id:INTEGER, table_name:TEXT, city:TEXT, target_date:TEXT, source:TEXT, utc_timestamp:TEXT,
    natural_key_json:TEXT, existing_row_id:INTEGER, existing_payload_hash:TEXT,
    incoming_payload_hash:TEXT, reason:TEXT, writer:TEXT, existing_row_json:TEXT,
    incoming_row_json:TEXT, recorded_at:TEXT
- **observations**  (rows≈47007, cols=45)
    id:INTEGER, city:TEXT, target_date:TEXT, source:TEXT, high_temp:REAL, low_temp:REAL, unit:TEXT,
    station_id:TEXT, fetched_at:TEXT, raw_value:REAL, raw_unit:TEXT, target_unit:TEXT, value_type:TEXT,
    fetch_utc:TEXT, local_time:TEXT, collection_window_start_utc:TEXT, collection_window_end_utc:TEXT,
    timezone:TEXT, utc_offset_minutes:INTEGER, dst_active:INTEGER, is_ambiguous_local_hour:INTEGER,
    is_missing_local_hour:INTEGER, hemisphere:TEXT, season:TEXT, month:INTEGER, rebuild_run_id:TEXT,
    data_source_version:TEXT, authority:TEXT, provenance_metadata:TEXT, high_raw_value:REAL,
    high_raw_unit:TEXT, high_target_unit:TEXT, low_raw_value:REAL, low_raw_unit:TEXT,
    low_target_unit:TEXT, high_fetch_utc:TEXT, high_local_time:TEXT,
    high_collection_window_start_utc:TEXT, high_collection_window_end_utc:TEXT, low_fetch_utc:TEXT,
    low_local_time:TEXT, low_collection_window_start_utc:TEXT, low_collection_window_end_utc:TEXT,
    high_provenance_metadata:TEXT, low_provenance_metadata:TEXT
- **opportunity_event_processing**  (rows≈0, cols=8)
    consumer_name:TEXT, event_id:TEXT, processing_status:TEXT, attempt_count:INTEGER, claimed_at:TEXT,
    processed_at:TEXT, last_error:TEXT, updated_at:TEXT
- **opportunity_events**  (rows≈0, cols=15)
    event_id:TEXT, event_type:TEXT, entity_key:TEXT, source:TEXT, observed_at:TEXT, available_at:TEXT,
    received_at:TEXT, causal_snapshot_id:TEXT, payload_hash:TEXT, idempotency_key:TEXT,
    priority:INTEGER, expires_at:TEXT, payload_json:TEXT, schema_version:INTEGER, created_at:TEXT
- **opportunity_fact**  (rows≈0, cols=23)
    decision_id:TEXT, candidate_id:TEXT, city:TEXT, target_date:TEXT, range_label:TEXT, direction:TEXT,
    strategy_key:TEXT, discovery_mode:TEXT, entry_method:TEXT, snapshot_id:TEXT, p_raw:REAL, p_cal:REAL,
    p_market:REAL, alpha:REAL, best_edge:REAL, ci_width:REAL, rejection_stage:TEXT,
    rejection_reason_json:TEXT, availability_status:TEXT, should_trade:INTEGER,
    observation_authority_id:TEXT, day0_context_json:TEXT, recorded_at:TEXT
- **outcome_fact**  (rows≈0, cols=13)
    position_id:TEXT, strategy_key:TEXT, entered_at:TEXT, exited_at:TEXT, settled_at:TEXT,
    exit_reason:TEXT, admin_exit_reason:TEXT, decision_snapshot_id:TEXT, pnl:REAL, outcome:INTEGER,
    hold_duration_hours:REAL, monitor_count:INTEGER, chain_corrections_count:INTEGER
- **platt_models**  (rows≈0, cols=23)
    model_key:TEXT, temperature_metric:TEXT, cluster:TEXT, season:TEXT, data_version:TEXT,
    input_space:TEXT, param_A:REAL, param_B:REAL, param_C:REAL, bootstrap_params_json:TEXT,
    n_samples:INTEGER, brier_insample:REAL, fitted_at:TEXT, is_active:INTEGER, authority:TEXT,
    bucket_key:TEXT, cycle:TEXT, source_id:TEXT, horizon_profile:TEXT, training_cutoff:TEXT,
    recorded_at:TEXT, error_model_family:TEXT, calibration_method:TEXT
- **position_current**  (rows≈0, cols=38)
    position_id:TEXT, phase:TEXT, trade_id:TEXT, market_id:TEXT, city:TEXT, cluster:TEXT,
    target_date:TEXT, bin_label:TEXT, direction:TEXT, unit:TEXT, size_usd:REAL, shares:REAL,
    cost_basis_usd:REAL, entry_price:REAL, p_posterior:REAL, last_monitor_prob:REAL,
    last_monitor_edge:REAL, last_monitor_market_price:REAL, decision_snapshot_id:TEXT,
    entry_method:TEXT, strategy_key:TEXT, edge_source:TEXT, discovery_mode:TEXT, chain_state:TEXT,
    token_id:TEXT, no_token_id:TEXT, condition_id:TEXT, order_id:TEXT, order_status:TEXT,
    updated_at:TEXT, temperature_metric:TEXT, fill_authority:TEXT, recovery_authority:TEXT,
    chain_shares:REAL, chain_avg_price:REAL, chain_cost_basis_usd:REAL, chain_seen_at:TEXT,
    chain_absence_at:TEXT
- **position_events**  (rows≈0, cols=19)
    event_id:TEXT, position_id:TEXT, event_version:INTEGER, sequence_no:INTEGER, event_type:TEXT,
    occurred_at:TEXT, phase_before:TEXT, phase_after:TEXT, strategy_key:TEXT, decision_id:TEXT,
    snapshot_id:TEXT, order_id:TEXT, command_id:TEXT, caused_by:TEXT, idempotency_key:TEXT,
    venue_status:TEXT, source_module:TEXT, env:TEXT, payload_json:TEXT
- **position_lots**  (rows≈0, cols=17)
    lot_id:INTEGER, position_id:INTEGER, state:TEXT, shares:TEXT, entry_price_avg:TEXT,
    exit_price_avg:TEXT, source_command_id:TEXT, source_trade_fact_id:INTEGER, captured_at:TEXT,
    state_changed_at:TEXT, source:TEXT, observed_at:TEXT, venue_timestamp:TEXT, ingested_at:TEXT,
    local_sequence:INTEGER, raw_payload_hash:TEXT, raw_payload_json:TEXT
- **probability_trace_fact**  (rows≈0, cols=48)
    trace_id:TEXT, decision_id:TEXT, decision_snapshot_id:TEXT, candidate_id:TEXT, city:TEXT,
    target_date:TEXT, range_label:TEXT, direction:TEXT, mode:TEXT, strategy_key:TEXT,
    discovery_mode:TEXT, entry_method:TEXT, selected_method:TEXT, trace_status:TEXT,
    missing_reason_json:TEXT, bin_labels_json:TEXT, p_raw_json:TEXT, p_cal_json:TEXT,
    p_market_json:TEXT, p_posterior_json:TEXT, p_posterior:REAL, alpha:REAL, agreement:TEXT,
    n_edges_found:INTEGER, n_edges_after_fdr:INTEGER, rejection_stage:TEXT, availability_status:TEXT,
    market_phase:TEXT, market_phase_source:TEXT, market_start_at:TEXT, market_end_at:TEXT,
    settlement_day_entry_utc:TEXT, uma_resolved_source:TEXT, prob_tail_mass_cal:REAL,
    prob_tail_mass_market:REAL, prob_tail_entropy:REAL, recorded_at:TEXT, probability_sanity_mode:TEXT,
    probability_sanity_reason:TEXT, edge_bin_idx:INTEGER, edge_bin_label:TEXT, edge_bin_p_raw:REAL,
    edge_bin_p_cal:REAL, edge_bin_p_market:REAL, edge_bin_member_support:REAL, edge_bin_odds_ratio:REAL,
    near_tail_p_cal:REAL, near_tail_p_market:REAL
- **provenance_envelope_events**  (rows≈0, cols=11)
    id:INTEGER, subject_type:TEXT, subject_id:TEXT, event_type:TEXT, payload_hash:TEXT,
    payload_json:TEXT, source:TEXT, observed_at:TEXT, venue_timestamp:TEXT, ingested_at:TEXT,
    local_sequence:INTEGER
- **raw_forecast_artifacts**  (rows≈2438, cols=16)
    artifact_id:INTEGER, source_id:TEXT, product_id:TEXT, data_version:TEXT, source_cycle_time:TEXT,
    source_available_at:TEXT, captured_at:TEXT, artifact_path:TEXT, sha256:TEXT, byte_size:INTEGER,
    request_url:TEXT, request_params_json:TEXT, artifact_metadata_json:TEXT,
    trade_authority_status:TEXT, training_allowed:INTEGER, recorded_at:TEXT
- **raw_model_forecast_request_conflicts**  (rows≈0, cols=16)
    conflict_id:INTEGER, detected_at:TEXT, model:TEXT, city:TEXT, target_date:TEXT, metric:TEXT,
    source_cycle_time:TEXT, endpoint:TEXT, existing_product_id:TEXT, incoming_product_id:TEXT,
    existing_request_url_hash:TEXT, incoming_request_url_hash:TEXT, existing_forecast_value_c:REAL,
    incoming_forecast_value_c:REAL, existing_cell_selection:TEXT, incoming_cell_selection:TEXT
- **raw_model_forecasts**  (rows≈573318, cols=32)
    raw_model_forecast_id:INTEGER, model:TEXT, city:TEXT, target_date:TEXT, metric:TEXT,
    source_cycle_time:TEXT, source_available_at:TEXT, captured_at:TEXT, lead_days:INTEGER,
    forecast_value_c:REAL, endpoint:TEXT, trade_authority_status:TEXT, training_allowed:INTEGER,
    recorded_at:TEXT, source_id:TEXT, source_family:TEXT, product_id:TEXT, provider:TEXT,
    model_name:TEXT, request_params_json:TEXT, request_url_hash:TEXT, raw_sha256:TEXT,
    latitude_requested:REAL, longitude_requested:REAL, timezone_requested:TEXT, cell_selection:TEXT,
    elevation_param:TEXT, downscaling_policy:TEXT, endpoint_mode:TEXT, model_domain_hash:TEXT,
    coverage_status:TEXT, artifact_id:INTEGER
- **readiness_state**  (rows≈4817, cols=27)
    readiness_id:TEXT, scope_key:TEXT, scope_type:TEXT, city_id:TEXT, city:TEXT, city_timezone:TEXT,
    target_local_date:TEXT, metric:TEXT, temperature_metric:TEXT, physical_quantity:TEXT,
    observation_field:TEXT, data_version:TEXT, source_id:TEXT, track:TEXT, source_run_id:TEXT,
    market_family:TEXT, event_id:TEXT, condition_id:TEXT, token_ids_json:TEXT, strategy_key:TEXT,
    status:TEXT, reason_codes_json:TEXT, computed_at:TEXT, expires_at:TEXT, dependency_json:TEXT,
    provenance_json:TEXT, recorded_at:TEXT
- **readiness_state_legacy_no_ready_20260607T131810Z**  (rows≈-, cols=27)
    readiness_id:TEXT, scope_key:TEXT, scope_type:TEXT, city_id:TEXT, city:TEXT, city_timezone:TEXT,
    target_local_date:TEXT, metric:TEXT, temperature_metric:TEXT, physical_quantity:TEXT,
    observation_field:TEXT, data_version:TEXT, source_id:TEXT, track:TEXT, source_run_id:TEXT,
    market_family:TEXT, event_id:TEXT, condition_id:TEXT, token_ids_json:TEXT, strategy_key:TEXT,
    status:TEXT, reason_codes_json:TEXT, computed_at:TEXT, expires_at:TEXT, dependency_json:TEXT,
    provenance_json:TEXT, recorded_at:TEXT
- **refit_bucket_failures**  (rows≈0, cols=8)
    id:INTEGER, cluster:TEXT, season:TEXT, cycle:TEXT, source_id:TEXT, error_class:TEXT,
    error_text:TEXT, ts:TEXT
- **regime_correlation_cache**  (rows≈0, cols=7)
    regime:TEXT, cities_json:TEXT, matrix_json:TEXT, fitted_at:TEXT, n_observations:INTEGER,
    intensity:REAL, schema_version:INTEGER
- **regret_decompositions**  (rows≈0, cols=12)
    id:INTEGER, experiment_id:TEXT, decision_event_id:TEXT, forecast_error_usd:REAL,
    observation_error_usd:REAL, quote_error_usd:REAL, non_fill_error_usd:REAL, fee_error_usd:REAL,
    timing_error_usd:REAL, settlement_ambiguity_error_usd:REAL, total_regret_usd:REAL, computed_at:TEXT
- **replacement_shadow_decisions**  (rows≈0, cols=22)
    decision_id:INTEGER, posterior_id:INTEGER, baseline_source_run_id:TEXT, market_snapshot_id:TEXT,
    condition_id:TEXT, token_id:TEXT, decision_time:TEXT, baseline_direction:TEXT,
    candidate_direction:TEXT, allowed_direction:TEXT, baseline_q_lcb:REAL, candidate_q_lcb:REAL,
    allowed_q_lcb:REAL, baseline_kelly_fraction:REAL, candidate_kelly_fraction:REAL,
    allowed_kelly_fraction:REAL, veto:INTEGER, veto_reason:TEXT, dependency_source_run_ids_json:TEXT,
    provenance_json:TEXT, trade_authority_status:TEXT, recorded_at:TEXT
- **replacement_shadow_decisions_legacy_coarse_unique_20260607T131448Z**  (rows≈1, cols=22)
    decision_id:INTEGER, posterior_id:INTEGER, baseline_source_run_id:TEXT, market_snapshot_id:TEXT,
    condition_id:TEXT, token_id:TEXT, decision_time:TEXT, baseline_direction:TEXT,
    candidate_direction:TEXT, allowed_direction:TEXT, baseline_q_lcb:REAL, candidate_q_lcb:REAL,
    allowed_q_lcb:REAL, baseline_kelly_fraction:REAL, candidate_kelly_fraction:REAL,
    allowed_kelly_fraction:REAL, veto:INTEGER, veto_reason:TEXT, dependency_source_run_ids_json:TEXT,
    provenance_json:TEXT, trade_authority_status:TEXT, recorded_at:TEXT
- **rescue_events**  (rows≈0, cols=12)
    rescue_event_id:INTEGER, trade_id:TEXT, position_id:TEXT, decision_snapshot_id:TEXT,
    temperature_metric:TEXT, causality_status:TEXT, authority:TEXT, authority_source:TEXT,
    chain_state:TEXT, reason:TEXT, occurred_at:TEXT, recorded_at:TEXT
- **risk_actions**  (rows≈0, cols=10)
    action_id:TEXT, strategy_key:TEXT, action_type:TEXT, value:TEXT, issued_at:TEXT,
    effective_until:TEXT, reason:TEXT, source:TEXT, precedence:INTEGER, status:TEXT
- **selection_family_fact**  (rows≈0, cols=10)
    family_id:TEXT, cycle_mode:TEXT, decision_snapshot_id:TEXT, city:TEXT, target_date:TEXT,
    strategy_key:TEXT, discovery_mode:TEXT, created_at:TEXT, meta_json:TEXT, decision_time_status:TEXT
- **selection_hypothesis_fact**  (rows≈0, cols=19)
    hypothesis_id:TEXT, family_id:TEXT, decision_id:TEXT, candidate_id:TEXT, city:TEXT,
    target_date:TEXT, range_label:TEXT, direction:TEXT, p_value:REAL, q_value:REAL, ci_lower:REAL,
    ci_upper:REAL, edge:REAL, tested:INTEGER, passed_prefilter:INTEGER, selected_post_fdr:INTEGER,
    rejection_stage:TEXT, recorded_at:TEXT, meta_json:TEXT
- **settlement_capture_verifications**  (rows≈0, cols=12)
    verification_id:INTEGER, city:TEXT, target_date:TEXT, temperature_metric:TEXT, fact_known_time:TEXT,
    source_published_time:TEXT, venue_resolved_time:TEXT, redeemed_time:TEXT, coherence_verdict:TEXT,
    incoherence_reason:TEXT, evidence_tier:TEXT, recorded_at:TEXT
- **settlement_command_events**  (rows≈0, cols=6)
    id:INTEGER, command_id:TEXT, event_type:TEXT, payload_hash:TEXT, payload_json:TEXT, recorded_at:TEXT
- **settlement_commands**  (rows≈0, cols=20)
    command_id:TEXT, state:TEXT, autoretry_eligible:INTEGER, condition_id:TEXT, market_id:TEXT,
    payout_asset:TEXT, pusd_amount_micro:INTEGER, token_amounts_json:TEXT, winning_index_set:TEXT,
    tx_hash:TEXT, block_number:INTEGER, confirmation_count:INTEGER, requested_at:TEXT,
    submitted_at:TEXT, terminal_at:TEXT, error_payload:TEXT, polymarket_end_anchor_source:TEXT,
    zeus_submit_intent_time:TEXT, venue_ack_time:TEXT, clock_skew_estimate_ms_at_submit:INTEGER
- **settlement_outcomes**  (rows≈7282, cols=15)
    settlement_id:INTEGER, city:TEXT, target_date:TEXT, temperature_metric:TEXT, market_slug:TEXT,
    winning_bin:TEXT, settlement_value:REAL, settlement_source:TEXT, settled_at:TEXT, authority:TEXT,
    provenance_json:TEXT, recorded_at:TEXT, outcome_type:INTEGER, settlement_station:TEXT,
    settlement_unit:TEXT
- **settlement_schema_migrations**  (rows≈1, cols=2)
    migration_key:TEXT, applied_at:TEXT
- **settlements**  (rows≈7282, cols=18)
    id:INTEGER, city:TEXT, target_date:TEXT, market_slug:TEXT, winning_bin:TEXT, settlement_value:REAL,
    settlement_source:TEXT, settled_at:TEXT, authority:TEXT, pm_bin_lo:REAL, pm_bin_hi:REAL, unit:TEXT,
    settlement_source_type:TEXT, temperature_metric:TEXT, physical_quantity:TEXT,
    observation_field:TEXT, data_version:TEXT, provenance_json:TEXT
- **shadow_experiments**  (rows≈0, cols=7)
    experiment_id:TEXT, strategy_id:TEXT, config_hash:TEXT, started_at:TEXT, closed_at:TEXT,
    cohort_tag:TEXT, immutable:INTEGER
- **shadow_signals**  (rows≈0, cols=9)
    id:INTEGER, city:TEXT, target_date:TEXT, timestamp:TEXT, decision_snapshot_id:TEXT, p_raw_json:TEXT,
    p_cal_json:TEXT, edges_json:TEXT, lead_hours:REAL
- **shoulder_exposure_ledger**  (rows≈0, cols=11)
    id:INTEGER, shoulder_side:TEXT, weather_system_cluster:TEXT, city:TEXT, target_date:TEXT,
    source:TEXT, regime:TEXT, notional_usd:REAL, decision_event_id:TEXT, observed_at:TEXT,
    schema_version:INTEGER
- **solar_daily**  (rows≈0, cols=11)
    city:TEXT, target_date:TEXT, timezone:TEXT, lat:REAL, lon:REAL, sunrise_local:TEXT,
    sunset_local:TEXT, sunrise_utc:TEXT, sunset_utc:TEXT, utc_offset_minutes:INTEGER, dst_active:INTEGER
- **source_contract_audit_events**  (rows≈0, cols=21)
    audit_id:TEXT, checked_at_utc:TEXT, scan_authority:TEXT, report_status:TEXT, severity:TEXT,
    event_id:TEXT, slug:TEXT, title:TEXT, city:TEXT, target_date:TEXT, temperature_metric:TEXT,
    source_contract_status:TEXT, source_contract_reason:TEXT, configured_source_family:TEXT,
    configured_station_id:TEXT, observed_source_family:TEXT, observed_station_id:TEXT,
    resolution_sources_json:TEXT, source_contract_json:TEXT, payload_hash:TEXT, created_at:TEXT
- **source_run**  (rows≈112, cols=36)
    source_run_id:TEXT, source_id:TEXT, track:TEXT, release_calendar_key:TEXT, ingest_mode:TEXT,
    origin_mode:TEXT, source_cycle_time:TEXT, source_issue_time:TEXT, source_release_time:TEXT,
    source_available_at:TEXT, fetch_started_at:TEXT, fetch_finished_at:TEXT, captured_at:TEXT,
    imported_at:TEXT, valid_time_start:TEXT, valid_time_end:TEXT, target_local_date:TEXT, city_id:TEXT,
    city_timezone:TEXT, temperature_metric:TEXT, physical_quantity:TEXT, observation_field:TEXT,
    dataset_id:TEXT, expected_members:INTEGER, observed_members:INTEGER, expected_steps_json:TEXT,
    observed_steps_json:TEXT, expected_count:INTEGER, observed_count:INTEGER, completeness_status:TEXT,
    partial_run:INTEGER, raw_payload_hash:TEXT, manifest_hash:TEXT, status:TEXT, reason_code:TEXT,
    recorded_at:TEXT
- **source_run_coverage**  (rows≈-, cols=27)
    coverage_id:TEXT, source_run_id:TEXT, source_id:TEXT, source_transport:TEXT,
    release_calendar_key:TEXT, track:TEXT, city_id:TEXT, city:TEXT, city_timezone:TEXT,
    target_local_date:TEXT, temperature_metric:TEXT, physical_quantity:TEXT, observation_field:TEXT,
    data_version:TEXT, expected_members:INTEGER, observed_members:INTEGER, expected_steps_json:TEXT,
    observed_steps_json:TEXT, snapshot_ids_json:TEXT, target_window_start_utc:TEXT,
    target_window_end_utc:TEXT, completeness_status:TEXT, readiness_status:TEXT, reason_code:TEXT,
    computed_at:TEXT, expires_at:TEXT, recorded_at:TEXT
- **source_time_frontier**  (rows≈0, cols=11)
    source_id:TEXT, family:TEXT, partition_key:TEXT, track:TEXT, role:TEXT, latest_event_time:TEXT,
    freshness_state:TEXT, live_blocker:TEXT, authority_tier:TEXT, computed_at:TEXT, data_version:INTEGER
- **strategy_health**  (rows≈0, cols=13)
    strategy_key:TEXT, as_of:TEXT, open_exposure_usd:REAL, settled_trades_30d:INTEGER,
    realized_pnl_30d:REAL, unrealized_pnl:REAL, win_rate_30d:REAL, brier_30d:REAL, fill_rate_14d:REAL,
    edge_trend_30d:REAL, risk_level:TEXT, execution_decay_flag:INTEGER, edge_compression_flag:INTEGER
- **tail_stress_scenarios**  (rows≈0, cols=9)
    market_slug:TEXT, temperature_metric:TEXT, target_date:TEXT, observation_time:TEXT,
    decision_seq:INTEGER, scenarios:TEXT, max_loss_pct:REAL, tail_probability_stressed:REAL,
    schema_version:INTEGER
- **temp_persistence**  (rows≈0, cols=6)
    city:TEXT, season:TEXT, delta_bucket:TEXT, frequency:REAL, avg_next_day_reversion:REAL,
    n_samples:INTEGER
- **token_price_log**  (rows≈0, cols=12)
    id:INTEGER, token_id:TEXT, city:TEXT, target_date:TEXT, range_label:TEXT, price:REAL, volume:REAL,
    bid:REAL, ask:REAL, spread:REAL, source_timestamp:TEXT, timestamp:TEXT
- **token_suppression**  (rows≈0, cols=7)
    token_id:TEXT, condition_id:TEXT, suppression_reason:TEXT, source_module:TEXT, created_at:TEXT,
    updated_at:TEXT, evidence_json:TEXT
- **token_suppression_history**  (rows≈0, cols=10)
    history_id:INTEGER, token_id:TEXT, condition_id:TEXT, suppression_reason:TEXT, source_module:TEXT,
    created_at:TEXT, updated_at:TEXT, evidence_json:TEXT, operation:TEXT, recorded_at:TEXT
- **trade_decisions**  (rows≈0, cols=49)
    trade_id:INTEGER, market_id:TEXT, bin_label:TEXT, direction:TEXT, size_usd:REAL, price:REAL,
    timestamp:TEXT, forecast_snapshot_id:INTEGER, calibration_model_version:TEXT, p_raw:REAL,
    p_calibrated:REAL, p_posterior:REAL, edge:REAL, ci_lower:REAL, ci_upper:REAL, kelly_fraction:REAL,
    status:TEXT, filled_at:TEXT, fill_price:REAL, runtime_trade_id:TEXT, order_id:TEXT,
    order_status_text:TEXT, order_posted_at:TEXT, entered_at_ts:TEXT, chain_state:TEXT, strategy:TEXT,
    edge_source:TEXT, bin_type:TEXT, discovery_mode:TEXT, market_hours_open:REAL, fill_quality:REAL,
    entry_method:TEXT, selected_method:TEXT, applied_validations_json:TEXT, exit_trigger:TEXT,
    exit_reason:TEXT, admin_exit_reason:TEXT, exit_divergence_score:REAL, exit_market_velocity_1h:REAL,
    exit_forward_edge:REAL, settlement_semantics_json:TEXT, epistemic_context_json:TEXT,
    edge_context_json:TEXT, entry_alpha_usd:REAL, execution_slippage_usd:REAL, exit_timing_usd:REAL,
    risk_throttling_usd:REAL, settlement_edge_usd:REAL, env:TEXT
- **uma_resolution**  (rows≈0, cols=10)
    condition_id:TEXT, tx_hash:TEXT, block_number:INTEGER, resolved_value:INTEGER, resolved_at_utc:TEXT,
    raw_log_json:TEXT, observed_at_utc:TEXT, confirmations_count:INTEGER,
    confirmations_required:INTEGER, is_valid:INTEGER
- **validated_calibration_transfers**  (rows≈0, cols=20)
    id:INTEGER, policy_id:TEXT, source_id:TEXT, target_source_id:TEXT, source_cycle:TEXT,
    target_cycle:TEXT, horizon_profile:TEXT, season:TEXT, cluster:TEXT, metric:TEXT, n_pairs:INTEGER,
    brier_source:REAL, brier_target:REAL, brier_diff:REAL, brier_diff_threshold:REAL, status:TEXT,
    evidence_window_start:TEXT, evidence_window_end:TEXT, platt_model_key:TEXT, evaluated_at:TEXT
- **venue_command_events**  (rows≈0, cols=7)
    event_id:TEXT, command_id:TEXT, sequence_no:INTEGER, event_type:TEXT, occurred_at:TEXT,
    payload_json:TEXT, state_after:TEXT
- **venue_commands**  (rows≈0, cols=18)
    command_id:TEXT, snapshot_id:TEXT, envelope_id:TEXT, position_id:TEXT, decision_id:TEXT,
    idempotency_key:TEXT, intent_kind:TEXT, market_id:TEXT, token_id:TEXT, side:TEXT, size:REAL,
    price:REAL, venue_order_id:TEXT, state:TEXT, last_event_id:TEXT, created_at:TEXT, updated_at:TEXT,
    review_required_reason:TEXT
- **venue_order_facts**  (rows≈0, cols=13)
    fact_id:INTEGER, venue_order_id:TEXT, command_id:TEXT, state:TEXT, remaining_size:TEXT,
    matched_size:TEXT, source:TEXT, observed_at:TEXT, venue_timestamp:TEXT, ingested_at:TEXT,
    local_sequence:INTEGER, raw_payload_hash:TEXT, raw_payload_json:TEXT
- **venue_submission_envelopes**  (rows≈0, cols=33)
    envelope_id:TEXT, schema_version:INTEGER, sdk_package:TEXT, sdk_version:TEXT, host:TEXT,
    chain_id:INTEGER, funder_address:TEXT, condition_id:TEXT, question_id:TEXT, yes_token_id:TEXT,
    no_token_id:TEXT, selected_outcome_token_id:TEXT, outcome_label:TEXT, side:TEXT, price:TEXT,
    size:TEXT, order_type:TEXT, post_only:INTEGER, tick_size:TEXT, min_order_size:TEXT,
    neg_risk:INTEGER, fee_details_json:TEXT, canonical_pre_sign_payload_hash:TEXT,
    signed_order_blob:BLOB, signed_order_hash:TEXT, raw_request_hash:TEXT, raw_response_json:TEXT,
    order_id:TEXT, trade_ids_json:TEXT, transaction_hashes_json:TEXT, error_code:TEXT,
    error_message:TEXT, captured_at:TEXT
- **venue_trade_facts**  (rows≈0, cols=18)
    trade_fact_id:INTEGER, trade_id:TEXT, venue_order_id:TEXT, command_id:TEXT, state:TEXT,
    filled_size:TEXT, fill_price:TEXT, fee_paid_micro:INTEGER, tx_hash:TEXT, block_number:INTEGER,
    confirmation_count:INTEGER, source:TEXT, observed_at:TEXT, venue_timestamp:TEXT, ingested_at:TEXT,
    local_sequence:INTEGER, raw_payload_hash:TEXT, raw_payload_json:TEXT
- **wrap_unwrap_commands**  (rows≈0, cols=12)
    command_id:TEXT, state:TEXT, direction:TEXT, amount_micro:INTEGER, tx_hash:TEXT,
    block_number:INTEGER, confirmation_count:INTEGER, requested_at:TEXT, terminal_at:TEXT,
    error_payload:TEXT, first_inclusion_block_time:TEXT, finality_confirmed_time:TEXT
- **wrap_unwrap_events**  (rows≈0, cols=5)
    id:INTEGER, command_id:TEXT, event_type:TEXT, payload_json:TEXT, recorded_at:TEXT
- **zeus_meta**  (rows≈10, cols=3)
    key:TEXT, value:TEXT, updated_at:TEXT

