# Created: 2026-08-24
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 6, operator-amended (flat micro, cheap-only, taker-only, one-per-cluster).
"""Tier-0 admission/sizing is a pure predicate layer: no DB, no q/edge influence
on stake, one entry per (city, target_date) cluster, taker-only, price < 0.25."""
from __future__ import annotations

import pytest

from src.strategy.tier0_policy import (
    TIER0_MAX_ENTRY_PRICE,
    TIER0_REJECT_AGGREGATE_CEILING,
    TIER0_REJECT_CLUSTER_OCCUPIED,
    TIER0_REJECT_LIMIT_CROSSES_CAP,
    TIER0_REJECT_MAKER_REST,
    TIER0_REJECT_PRICE_TOO_HIGH,
    Tier0CandidateFacts,
    check_tier0_drawdown_kill,
    load_tier0_risk_ceilings,
    tier0_admission_reason,
    tier0_drawdown_kill_breached,
    tier0_flat_stake_shares,
)


def _facts(**over) -> Tier0CandidateFacts:
    kw = dict(
        execution_price=0.15,
        limit_price=0.15,
        execution_mode="TAKER_LIMIT",
        cluster_key=("chicago", "2026-08-25"),
    )
    kw.update(over)
    return Tier0CandidateFacts(**kw)


def _admit(**over):
    kw = dict(
        enabled=True,
        candidate=_facts(),
        occupied_clusters=frozenset(),
        current_open_cost_usd=0.0,
        candidate_open_cost_usd=1.0,
        conservative_settled_bankroll_usd=1000.0,
        aggregate_open_loss_pct_ceiling=0.02,
    )
    kw.update(over)
    return tier0_admission_reason(**kw)


# (a) price 0.30 candidate rejected typed
def test_price_above_cap_rejected_typed():
    reason = _admit(candidate=_facts(execution_price=0.30, limit_price=0.30))
    assert reason is not None
    assert reason.startswith(TIER0_REJECT_PRICE_TOO_HIGH)


def test_price_at_cap_rejected():
    reason = _admit(candidate=_facts(execution_price=TIER0_MAX_ENTRY_PRICE, limit_price=TIER0_MAX_ENTRY_PRICE))
    assert reason is not None
    assert reason.startswith(TIER0_REJECT_PRICE_TOO_HIGH)


def test_limit_price_crossing_cap_rejected_even_if_execution_price_ok():
    reason = _admit(candidate=_facts(execution_price=0.10, limit_price=0.30))
    assert reason is not None
    assert reason.startswith(TIER0_REJECT_LIMIT_CROSSES_CAP)


# (b) price 0.15 admitted
def test_cheap_taker_candidate_admitted():
    assert _admit() is None


# (c) MAKER_REST entry excluded when flag on
def test_maker_rest_entry_rejected_typed():
    reason = _admit(candidate=_facts(execution_mode="MAKER_REST"))
    assert reason is not None
    assert reason.startswith(TIER0_REJECT_MAKER_REST)


def test_unknown_execution_mode_rejected():
    reason = _admit(candidate=_facts(execution_mode=""))
    assert reason is not None
    assert reason.startswith(TIER0_REJECT_MAKER_REST)


# (d) property: stake identical for q=0.3 vs q=0.9 (flat) — no q parameter exists,
# so this is checked at the signature level: the stake depends only on venue facts.
def test_flat_stake_is_price_and_edge_independent_by_construction():
    stake_a = tier0_flat_stake_shares(min_order_size_shares=5.0)
    stake_b = tier0_flat_stake_shares(min_order_size_shares=5.0)
    assert stake_a == stake_b == 5.0
    # No q/edge/|q-p| parameter is even accepted — changing "belief" cannot
    # change the call shape, let alone the result.
    import inspect

    params = set(inspect.signature(tier0_flat_stake_shares).parameters)
    assert not params & {"q", "q_posterior", "edge", "q_lcb", "p", "price"}


def test_flat_stake_rounds_up_to_granularity():
    assert tier0_flat_stake_shares(min_order_size_shares=5.0, share_granularity=2.0) == 6.0
    assert tier0_flat_stake_shares(min_order_size_shares=4.0, share_granularity=2.0) == 4.0


def test_flat_stake_rejects_nonpositive_min_order():
    with pytest.raises(ValueError):
        tier0_flat_stake_shares(min_order_size_shares=0.0)


# (e) second entry same cluster rejected
def test_second_entry_same_cluster_rejected_typed():
    reason = _admit(occupied_clusters=frozenset({("chicago", "2026-08-25")}))
    assert reason is not None
    assert reason.startswith(TIER0_REJECT_CLUSTER_OCCUPIED)


# (f) different cluster admitted
def test_different_cluster_admitted():
    reason = _admit(occupied_clusters=frozenset({("miami", "2026-08-25")}))
    assert reason is None


def test_different_metric_same_city_date_is_same_cluster_rejected():
    # Cluster key is (city, target_date) only — a temp-high position and a
    # temp-low candidate for the same city/date share one cluster.
    reason = _admit(
        candidate=_facts(cluster_key=("chicago", "2026-08-25")),
        occupied_clusters=frozenset({("chicago", "2026-08-25")}),
    )
    assert reason is not None
    assert reason.startswith(TIER0_REJECT_CLUSTER_OCCUPIED)


# (g) aggregate ceiling: candidate pushing open cost past ceiling rejected
def test_aggregate_ceiling_breach_rejected_typed():
    reason = _admit(
        current_open_cost_usd=19.5,
        candidate_open_cost_usd=1.0,
        conservative_settled_bankroll_usd=1000.0,
        aggregate_open_loss_pct_ceiling=0.02,  # ceiling = $20
    )
    assert reason is not None
    assert reason.startswith(TIER0_REJECT_AGGREGATE_CEILING)


def test_aggregate_ceiling_exactly_at_limit_admitted():
    reason = _admit(
        current_open_cost_usd=19.0,
        candidate_open_cost_usd=1.0,
        conservative_settled_bankroll_usd=1000.0,
        aggregate_open_loss_pct_ceiling=0.02,  # ceiling == $20, projected == $20
    )
    assert reason is None


def test_aggregate_ceiling_nonpositive_bankroll_rejects_fail_closed():
    reason = _admit(conservative_settled_bankroll_usd=0.0)
    assert reason is not None
    assert reason.startswith(TIER0_REJECT_AGGREGATE_CEILING)


# (h) flag OFF -> all Tier-0 checks bypassed (today's behavior)
def test_flag_off_bypasses_every_check_even_for_an_otherwise_rejected_candidate():
    reason = _admit(
        enabled=False,
        candidate=_facts(execution_price=0.99, limit_price=0.99, execution_mode="MAKER_REST"),
        occupied_clusters=frozenset({("chicago", "2026-08-25")}),
        current_open_cost_usd=1_000_000.0,
        aggregate_open_loss_pct_ceiling=0.02,
    )
    assert reason is None


# (i) drawdown-kill triggers the pause request (mock control-plane call, assert invoked once)
def test_drawdown_kill_breached_pure_predicate():
    assert tier0_drawdown_kill_breached(
        tier0_start_equity_usd=100.0,
        tier0_realized_pnl_usd=-15.0,
        drawdown_kill_pct=0.10,
    )
    assert not tier0_drawdown_kill_breached(
        tier0_start_equity_usd=100.0,
        tier0_realized_pnl_usd=-5.0,
        drawdown_kill_pct=0.10,
    )


def test_drawdown_kill_positive_pnl_never_breaches():
    assert not tier0_drawdown_kill_breached(
        tier0_start_equity_usd=100.0,
        tier0_realized_pnl_usd=50.0,
        drawdown_kill_pct=0.10,
    )


def test_drawdown_kill_calls_pause_fn_exactly_once_on_breach():
    calls: list[str] = []

    def _pause(reason_code: str) -> None:
        calls.append(reason_code)

    fired = check_tier0_drawdown_kill(
        tier0_start_equity_usd=100.0,
        tier0_realized_pnl_usd=-20.0,
        drawdown_kill_pct=0.10,
        pause_fn=_pause,
    )
    assert fired is True
    assert calls == ["reversal_plan_tier0_drawdown_kill_breached"]


def test_drawdown_kill_does_not_call_pause_fn_when_not_breached():
    calls: list[str] = []
    fired = check_tier0_drawdown_kill(
        tier0_start_equity_usd=100.0,
        tier0_realized_pnl_usd=-1.0,
        drawdown_kill_pct=0.10,
        pause_fn=lambda reason: calls.append(reason),
    )
    assert fired is False
    assert calls == []


# Risk-ceiling artifact loader.
def test_load_tier0_risk_ceilings_reads_tracked_yaml(tmp_path):
    artifact = tmp_path / "risk_policy.yaml"
    artifact.write_text(
        "policy_version: \"2\"\n"
        "kelly_multiplier_ceiling: 0.125\n"
        "tier0:\n"
        "  aggregate_open_loss_pct_ceiling: 0.02\n"
        "  drawdown_kill_pct: 0.10\n"
    )
    ceilings = load_tier0_risk_ceilings(path=artifact)
    assert ceilings == {
        "aggregate_open_loss_pct_ceiling": 0.02,
        "drawdown_kill_pct": 0.10,
    }


def test_load_tier0_risk_ceilings_missing_file_falls_back_to_documented_defaults(tmp_path):
    ceilings = load_tier0_risk_ceilings(path=tmp_path / "does_not_exist.yaml")
    assert ceilings == {
        "aggregate_open_loss_pct_ceiling": 0.02,
        "drawdown_kill_pct": 0.10,
    }


def test_load_tier0_risk_ceilings_malformed_tier0_block_fails_closed(tmp_path):
    artifact = tmp_path / "risk_policy.yaml"
    artifact.write_text(
        "policy_version: \"2\"\n"
        "tier0:\n"
        "  aggregate_open_loss_pct_ceiling: not_a_number\n"
    )
    with pytest.raises(ValueError):
        load_tier0_risk_ceilings(path=artifact)


def test_load_tier0_risk_ceilings_out_of_range_fails_closed(tmp_path):
    artifact = tmp_path / "risk_policy.yaml"
    artifact.write_text(
        "policy_version: \"2\"\n"
        "tier0:\n"
        "  aggregate_open_loss_pct_ceiling: 1.5\n"
        "  drawdown_kill_pct: 0.10\n"
    )
    with pytest.raises(ValueError):
        load_tier0_risk_ceilings(path=artifact)
