"""Single typed ownership source for Zeus physical storage (Phase 0 of the 2-DB redesign).

Created: 2026-06-30
Authority basis: docs/operations/current/reports/market_structure_code_atlas_2026-06-30.md §6B;
  first-principles design panel wf_e2c56a31 (verified: 8-process WAL topology + .bulk/.live lock axis).

THIS MODULE IS THE INTENDED SINGLE SOURCE OF OWNERSHIP TRUTH. Today it merely MIRRORS the
hand-maintained architecture/db_table_ownership.yaml (proven byte-equivalent for every non-legacy
table by tests/state/test_domains_reproduces_registry.py) and has ZERO runtime effect — nothing
imports it for schema-init, writer-routing, or the boot gate yet. The migration (§6B Phases 1-5)
moves those three derivations onto this module so the 3 drifting ownership sources collapse to 1,
making inversions / ghosts / duals / naming-schism structurally unrepresentable.

Two axes are recorded per table:
  - CURRENT_DB[name]  -> the DB the current registry+init treat as canonical owner (world|forecasts|
    trade). Transitional; the boot gate still reads the YAML.
  - target_domain(name) -> BULK | MONEY, the redesign target (2 physical WAL files split on the
    PROVEN write-contention axis: forecast-BULK ingest process vs the LIVE money-path).

The BULK set is PROVISIONAL pending the per-daemon write-set audit (design open-risk #3); do not move
any data on its basis until that audit confirms each table's writer/reader schedule.
"""

from __future__ import annotations

from enum import Enum


class Domain(Enum):
    """The two target physical write-domains (2-DB redesign)."""
    BULK = "bulk"    # forecast-live process: high-volume model/observation ingest -> state/zeus_bulk.db
    MONEY = "money"  # the live causal chain (position/venue/settlement/learning) -> state/zeus_money.db

    @property
    def db_filename(self) -> str:
        return {Domain.BULK: "zeus_bulk.db", Domain.MONEY: "zeus_money.db"}[self]


# Provisional BULK-domain tables (forecast/observation bulk ingest). PROVISIONAL — see module docstring.
BULK_TABLES: frozenset[str] = frozenset({
    'cycle_advance_enqueues',
    'daily_observation_revisions',
    'deterministic_forecast_anchors',
    'diurnal_curves',
    'diurnal_peak_prob',
    'ensemble_snapshots',
    'fusion_upgrade_enqueues',
    'job_run',
    'market_microstructure_snapshots',
    'observation_instants',
    'observation_revisions',
    'observations',
    'raw_forecast_artifacts',
    'raw_model_forecast_request_conflicts',
    'raw_model_forecasts',
    'readiness_state',
    'solar_daily',
    'source_run',
    'source_run_coverage',
    'temp_persistence',
})

# CURRENT canonical owner per live (non-legacy) table, mirrored from db_table_ownership.yaml.
# The reproduction test asserts this stays byte-equivalent to the YAML until the YAML is retired.
CURRENT_DB: dict[str, str] = {
    '_migrations_applied': 'trade',
    'availability_fact': 'world',
    'book_hash_transitions': 'trade',
    'calibration_decision_group': 'world',
    'calibration_pairs': 'forecasts',
    'chronicle': 'world',
    'collateral_ledger_snapshots': 'world',
    'collateral_reservations': 'world',
    'control_overrides_history': 'world',
    'cycle_advance_enqueues': 'forecasts',
    'daily_observation_revisions': 'world',
    'data_coverage': 'world',
    'day0_horizon_platt_fits': 'forecasts',
    'day0_metric_fact': 'world',
    'day0_nowcast_runs': 'forecasts',
    'day0_oracle_anomaly_flags': 'world',
    'db_chunk_boundary_events': 'world',
    'decision_certificate_edges': 'world',
    'decision_certificate_supersessions': 'world',
    'decision_certificates': 'world',
    'decision_compile_failures': 'world',
    'decision_events': 'world',
    'decision_integrity_quarantine': 'trade',
    'decision_log': 'world',
    'deterministic_forecast_anchors': 'forecasts',
    'diurnal_curves': 'world',
    'diurnal_peak_prob': 'world',
    'edli_fill_bridge_dispositions': 'world',
    'edli_live_cap_day_slots': 'world',
    'edli_live_cap_rate_window': 'world',
    'edli_live_cap_usage': 'world',
    'edli_live_order_events': 'world',
    'edli_live_order_projection': 'world',
    'edli_live_profit_audit': 'world',
    'edli_no_submit_receipts': 'world',
    'edli_user_channel_inbox': 'world',
    'edli_user_channel_message_dedup': 'world',
    'ensemble_snapshots': 'forecasts',
    'event_dead_letters': 'world',
    'evidence_tier_assignments': 'world',
    'exchange_reconcile_findings': 'world',
    'executable_market_snapshot_invalidations': 'trade',
    'executable_market_snapshot_latest': 'trade',
    'executable_market_snapshots': 'trade',
    'execution_fact': 'trade',
    'execution_feasibility_evidence': 'trade',
    'execution_feasibility_latest': 'trade',
    'exit_mutex_holdings': 'world',
    'exit_timing_attribution': 'world',
    'family_rebalance_intents': 'world',
    'forecast_posteriors': 'forecasts',
    'forecast_skill': 'world',
    'forecasts': 'world',
    'fusion_upgrade_enqueues': 'forecasts',
    'job_run': 'forecasts',
    'market_events': 'forecasts',
    'market_microstructure_snapshots': 'forecasts',
    'market_price_history': 'world',
    'market_topology_state': 'world',
    'model_bias_ens': 'world',
    'no_trade_events': 'world',
    'no_trade_regret_events': 'world',
    'observation_instants': 'world',
    'observation_revisions': 'world',
    'observations': 'forecasts',
    'opportunity_event_processing': 'world',
    'opportunity_events': 'world',
    'opportunity_fact': 'world',
    'outcome_fact': 'trade',
    'platt_models': 'world',
    'position_current': 'trade',
    'position_events': 'trade',
    'position_lots': 'trade',
    'probability_trace_fact': 'world',
    'provenance_envelope_events': 'world',
    'raw_forecast_artifacts': 'forecasts',
    'raw_model_forecast_request_conflicts': 'forecasts',
    'raw_model_forecasts': 'forecasts',
    'readiness_state': 'forecasts',
    'refit_bucket_failures': 'world',
    'regime_correlation_cache': 'world',
    'regret_decompositions': 'world',
    'rescue_events': 'world',
    'risk_actions': 'world',
    'selection_family_fact': 'world',
    'selection_hypothesis_fact': 'world',
    'settlement_attribution': 'world',
    'settlement_capture_verifications': 'forecasts',
    'settlement_command_events': 'trade',
    'settlement_commands': 'trade',
    'settlement_day_observation_authority': 'trade',
    'settlement_outcomes': 'forecasts',
    'settlement_schema_migrations': 'trade',
    'shoulder_exposure_ledger': 'world',
    'solar_daily': 'world',
    'source_contract_audit_events': 'world',
    'source_run': 'forecasts',
    'source_run_coverage': 'forecasts',
    'source_time_frontier': 'forecasts',
    'strategy_health': 'world',
    'temp_persistence': 'world',
    'token_price_log': 'world',
    'token_suppression': 'world',
    'token_suppression_history': 'world',
    'uma_resolution': 'world',
    'validated_calibration_transfers': 'world',
    'venue_command_events': 'trade',
    'venue_commands': 'trade',
    'venue_order_facts': 'trade',
    'venue_submission_envelopes': 'trade',
    'venue_trade_facts': 'trade',
    'wrap_unwrap_commands': 'world',
    'wrap_unwrap_events': 'world',
    'zeus_meta': 'world',
}


def target_domain(table_name: str) -> Domain:
    """Target 2-DB domain for a table: BULK for forecast-ingest, MONEY otherwise."""
    return Domain.BULK if table_name in BULK_TABLES else Domain.MONEY


def current_owner_db(table_name: str) -> str | None:
    """The DB the current registry treats as canonical owner (transitional)."""
    return CURRENT_DB.get(table_name)


def live_tables() -> frozenset[str]:
    return frozenset(CURRENT_DB)

