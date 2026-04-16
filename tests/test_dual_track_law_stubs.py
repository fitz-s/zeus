"""Enforcement stubs for Dual-Track Metric Spine invariants (INV-18..INV-22)
and negative constraints (NC-11..NC-15).

Each test is a skeleton that skips with a message indicating which Phase will
activate the real enforcement. When the enforcement work lands, replace the
pytest.skip() body with the actual assertion.
"""
from __future__ import annotations

import pytest


# NC-11 / INV-14
def test_no_daily_low_on_legacy_table():
    """NC-11: No writing of daily-low rows on legacy (non-v2) tables."""
    pytest.skip("pending: enforced in Phase 2 when v2 schema lands")


# NC-12 / INV-16
def test_no_high_low_mix_in_platt_or_bins():
    """NC-12: No mixing of high and low rows in Platt model, calibration pair set, bin lookup, or settlement identity."""
    pytest.skip("pending: enforced in Phase 7 rebuild")


# NC-13 / INV-17
def test_json_export_after_db_commit():
    """NC-13 / INV-17: JSON export writes must occur only after the corresponding DB commit returns."""
    pytest.skip("pending: enforced in Phase 2 state-authority work")


# NC-14 / INV-21
def test_kelly_input_carries_distributional_info():
    """NC-14 / INV-21: kelly_size() must receive a distributional price object, not a bare entry_price scalar."""
    pytest.skip("pending: enforced pre-Phase 9 activation")


# NC-15 / INV-22
def test_fdr_family_key_is_canonical():
    """NC-15 / INV-22: Phase 1 enforcement — scope-aware FDR family grammar.

    make_hypothesis_family_id and make_edge_family_id produce distinct IDs for
    the same candidate inputs, and both are deterministic within their scope.
    This prevents BH discovery budgets from silently merging across scopes.
    """
    from src.strategy.selection_family import (
        make_hypothesis_family_id,
        make_edge_family_id,
    )

    cand = dict(
        cycle_mode="opening_hunt",
        city="NYC",
        target_date="2026-04-01",
        discovery_mode="opening_hunt",
        decision_snapshot_id="snap-1",
    )
    h_id = make_hypothesis_family_id(**cand)
    e_id = make_edge_family_id(**cand, strategy_key="center_buy")

    # Scope separation: same candidate inputs must produce different IDs
    assert h_id != e_id, "hypothesis and edge family IDs must differ for same candidate inputs"

    # Determinism within each scope
    assert h_id == make_hypothesis_family_id(**cand), "hypothesis family ID must be deterministic"
    assert e_id == make_edge_family_id(**cand, strategy_key="center_buy"), "edge family ID must be deterministic"


# INV-19
def test_red_triggers_active_position_sweep():
    """INV-19: RED risk level must cancel all pending orders and sweep active positions toward exit; entry-block-only RED is forbidden."""
    pytest.skip("pending: enforced in risk phase before Phase 9")


# INV-18
def test_chain_reconciliation_three_state_machine():
    """INV-18: Chain reconciliation state is three-valued (CHAIN_SYNCED / CHAIN_EMPTY / CHAIN_UNKNOWN); void decisions require CHAIN_EMPTY, not CHAIN_UNKNOWN."""
    pytest.skip("pending: enforced with Phase 2 state authority")


# INV-20
def test_load_portfolio_degrades_gracefully_on_authority_loss():
    """INV-20: Authority-loss must preserve monitor/exit/reconciliation paths in read-only mode; RuntimeError that kills the full cycle on authority-loss is forbidden."""
    pytest.skip("pending: enforced with Phase 6 runtime split")
