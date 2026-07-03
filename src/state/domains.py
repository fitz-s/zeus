"""Single ownership source for Zeus tables over the EXISTING three DBs — in-place cleanup, not a rebuild.

Created: 2026-06-30
Authority basis: docs/operations/current/reports/market_structure_code_atlas_2026-06-30.md §6;
  data-grounded row-probe audit 2026-06-30 (fixes the 19 registry inversions IN PLACE).

PURPOSE: collapse the THREE drifting ownership sources (init-code CREATE-lists / the 3092-line
db_table_ownership.yaml / runtime writers) onto ONE typed declaration, so drift/ghosts/inversions become
structurally impossible. This does NOT change the physical DB count: the existing world/forecasts/trade
files stay. It only makes ownership single-sourced + data-correct. CANONICAL_OWNER records the CORRECT
owning DB (the one whose data the table actually holds); it equals the current registry EXCEPT the
CORRECTED_FROM_REGISTRY set — the 19 tables whose registry is inverted, which the in-place migration
converges (move each table's CREATE + registry entry to the DB it already occupies, then drop the empty
shell). ZERO new DBs, ZERO data rip-and-replace.

Runtime effect today: NONE (nothing imports this for schema-init/writer-routing/the boot gate yet).
"""

from __future__ import annotations

from enum import Enum


class Domain(Enum):
    """The existing physical DBs (unchanged by this cleanup)."""
    WORLD = "world"            # state/zeus-world.db
    FORECASTS = "forecasts"    # state/zeus-forecasts.db
    TRADE = "trade"            # state/zeus_trades.db
    RISK_STATE = "risk_state"  # state/risk_state.db
    BACKTEST = "backtest"      # state/zeus_backtest.db (derived; never runtime authority)


# Tables whose data-grounded owner DIFFERS from the current registry (the in-place migration worklist).
CORRECTED_FROM_REGISTRY: frozenset[str] = frozenset({
})

# CANONICAL owner DB per live (non-legacy) table — the SINGLE source of ownership truth.
CANONICAL_OWNER: dict[str, Domain] = {
    '_migrations_applied': Domain.TRADE,
    'availability_fact': Domain.WORLD,
    'book_hash_transitions': Domain.TRADE,
    'calibration_decision_group': Domain.WORLD,
    'calibration_pairs': Domain.FORECASTS,
    'chronicle': Domain.WORLD,
    'collateral_ledger_snapshots': Domain.TRADE,
    'collateral_reservations': Domain.TRADE,
    'collateral_unsettled_proceeds': Domain.TRADE,
    'control_overrides_history': Domain.WORLD,
    'cycle_advance_enqueues': Domain.FORECASTS,
    'daily_observation_revisions': Domain.WORLD,
    'data_coverage': Domain.WORLD,
    'day0_horizon_platt_fits': Domain.FORECASTS,
    'day0_metric_fact': Domain.WORLD,
    'day0_nowcast_runs': Domain.FORECASTS,
    'day0_oracle_anomaly_flags': Domain.WORLD,
    'db_chunk_boundary_events': Domain.WORLD,
    'decision_certificate_edges': Domain.WORLD,
    'decision_certificate_supersessions': Domain.WORLD,
    'decision_certificates': Domain.WORLD,
    'decision_compile_failures': Domain.WORLD,
    'decision_events': Domain.WORLD,
    'decision_integrity_quarantine': Domain.TRADE,
    'decision_log': Domain.TRADE,
    'deterministic_forecast_anchors': Domain.FORECASTS,
    'diurnal_curves': Domain.WORLD,
    'diurnal_peak_prob': Domain.WORLD,
    'edli_fill_bridge_dispositions': Domain.WORLD,
    'edli_live_cap_day_slots': Domain.WORLD,
    'edli_live_cap_rate_window': Domain.WORLD,
    'edli_live_cap_usage': Domain.WORLD,
    'edli_live_order_events': Domain.WORLD,
    'edli_live_order_projection': Domain.WORLD,
    'edli_live_profit_audit': Domain.WORLD,
    'edli_no_submit_receipts': Domain.WORLD,
    'edli_user_channel_inbox': Domain.WORLD,
    'edli_user_channel_message_dedup': Domain.WORLD,
    'ensemble_snapshots': Domain.FORECASTS,
    'event_dead_letters': Domain.WORLD,
    'evidence_tier_assignments': Domain.WORLD,
    'exchange_reconcile_findings': Domain.TRADE,
    'executable_market_snapshot_invalidations': Domain.TRADE,
    'executable_market_snapshot_latest': Domain.TRADE,
    'executable_market_snapshots': Domain.TRADE,
    'execution_fact': Domain.TRADE,
    'execution_feasibility_evidence': Domain.TRADE,
    'execution_feasibility_latest': Domain.TRADE,
    'exit_mutex_holdings': Domain.TRADE,
    'exit_timing_attribution': Domain.WORLD,
    'family_rebalance_intents': Domain.WORLD,
    'forecast_posteriors': Domain.FORECASTS,
    'forecast_skill': Domain.WORLD,
    'forecasts': Domain.WORLD,
    'fusion_upgrade_enqueues': Domain.FORECASTS,
    'historical_forecasts': Domain.WORLD,
    'hko_hourly_accumulator': Domain.WORLD,
    'job_run': Domain.FORECASTS,
    'market_events': Domain.FORECASTS,
    'market_microstructure_snapshots': Domain.FORECASTS,
    'market_price_history': Domain.TRADE,
    'market_topology_state': Domain.WORLD,
    'model_bias': Domain.WORLD,
    'model_bias_ens': Domain.WORLD,
    'no_trade_events': Domain.WORLD,
    'no_trade_regret_events': Domain.WORLD,
    'observation_instants': Domain.WORLD,
    'observation_revisions': Domain.WORLD,
    'observations': Domain.FORECASTS,
    'opportunity_event_processing': Domain.WORLD,
    'opportunity_events': Domain.WORLD,
    'opportunity_fact': Domain.TRADE,
    'outcome_fact': Domain.TRADE,
    'platt_models': Domain.WORLD,
    'position_current': Domain.TRADE,
    'position_events': Domain.TRADE,
    'position_lots': Domain.TRADE,
    'probability_trace_fact': Domain.WORLD,
    'provenance_envelope_events': Domain.TRADE,
    'raw_forecast_artifacts': Domain.FORECASTS,
    'raw_model_forecast_request_conflicts': Domain.FORECASTS,
    'raw_model_forecasts': Domain.FORECASTS,
    'readiness_state': Domain.FORECASTS,
    'refit_bucket_failures': Domain.WORLD,
    'regime_correlation_cache': Domain.WORLD,
    'regret_decompositions': Domain.WORLD,
    'rescue_events': Domain.WORLD,
    'risk_actions': Domain.TRADE,
    'selection_family_fact': Domain.WORLD,
    'selection_hypothesis_fact': Domain.WORLD,
    'settlement_attribution': Domain.WORLD,
    'settlement_capture_verifications': Domain.FORECASTS,
    'settlement_command_events': Domain.TRADE,
    'settlement_commands': Domain.TRADE,
    'settlement_day_observation_authority': Domain.TRADE,
    'settlement_outcomes': Domain.FORECASTS,
    'settlement_schema_migrations': Domain.TRADE,
    'shoulder_exposure_ledger': Domain.WORLD,
    'solar_daily': Domain.WORLD,
    'source_contract_audit_events': Domain.WORLD,
    'source_run': Domain.FORECASTS,
    'source_run_coverage': Domain.FORECASTS,
    'source_time_frontier': Domain.FORECASTS,
    'strategy_health': Domain.TRADE,
    'temp_persistence': Domain.WORLD,
    'token_price_log': Domain.TRADE,
    'token_suppression': Domain.TRADE,
    'token_suppression_history': Domain.TRADE,
    'trade_decisions': Domain.TRADE,
    'uma_resolution': Domain.WORLD,
    'validated_calibration_transfers': Domain.WORLD,
    'venue_command_events': Domain.TRADE,
    'venue_commands': Domain.TRADE,
    'venue_order_facts': Domain.TRADE,
    'venue_submission_envelopes': Domain.TRADE,
    'venue_trade_facts': Domain.TRADE,
    'wrap_unwrap_commands': Domain.WORLD,
    'wrap_unwrap_events': Domain.WORLD,
    'zeus_meta': Domain.WORLD,
}


def owner_domain(table_name: str) -> Domain | None:
    return CANONICAL_OWNER.get(table_name)


def tables_for(domain: Domain) -> frozenset[str]:
    return frozenset(n for n, d in CANONICAL_OWNER.items() if d is domain)


def live_tables() -> frozenset[str]:
    return frozenset(CANONICAL_OWNER)
