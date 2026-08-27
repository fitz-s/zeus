# Created: 2026-08-24
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 6, operator-amended (flat micro, cheap-only, taker-only, one-per-cluster).
"""Tier-0 admission/sizing is a pure predicate layer: no DB, no q/edge influence
on stake, one entry per (city, target_date) cluster, taker-only, price < 0.25."""
from __future__ import annotations

import pytest

from src.strategy.tier0_policy import (
    TIER0_MAX_ENTRY_PRICE,
    tier0_decision_price,
    tier0_price_rejection_reason,
    TIER0_REJECT_AGGREGATE_CEILING,
    TIER0_REJECT_CLUSTER_OCCUPIED,
    TIER0_REJECT_LIMIT_CROSSES_CAP,
    TIER0_REJECT_MAKER_REST,
    TIER0_REJECT_PRICE_TOO_HIGH,
    Tier0CandidateFacts,
    Tier0ClosedPositionFacts,
    build_tier0_seed_value,
    check_tier0_drawdown_kill,
    load_tier0_risk_ceilings,
    parse_tier0_seed,
    tier0_admission_reason,
    tier0_drawdown_kill_breached,
    tier0_flat_stake_notional_cap_usd,
    tier0_flat_stake_shares,
    tier0_realized_pnl_usd,
    tier0_start_equity_override_id,
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
    # 2026-08-26 window correction: the cap is the venue band ceiling (0.95),
    # not the refuted 0.25 tail-lottery prior. Above-band still rejects typed.
    reason = _admit(candidate=_facts(execution_price=0.96, limit_price=0.96))
    assert reason is not None
    assert reason.startswith(TIER0_REJECT_PRICE_TOO_HIGH)


def test_price_at_cap_rejected():
    reason = _admit(candidate=_facts(execution_price=TIER0_MAX_ENTRY_PRICE, limit_price=TIER0_MAX_ENTRY_PRICE))
    assert reason is not None
    assert reason.startswith(TIER0_REJECT_PRICE_TOO_HIGH)


def test_mid_and_rich_prices_now_admitted():
    # The fills audit's only positive-edge class (price>0.75) must be
    # admissible; risk is bounded by flat venue-min stake, not by price.
    for price in (0.30, 0.60, 0.85, 0.94):
        assert _admit(candidate=_facts(execution_price=price, limit_price=price)) is None


def test_limit_price_crossing_cap_rejected_even_if_execution_price_ok():
    reason = _admit(candidate=_facts(execution_price=0.10, limit_price=0.96))
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
        "  epoch: 3\n"
    )
    ceilings = load_tier0_risk_ceilings(path=artifact)
    assert ceilings == {
        "aggregate_open_loss_pct_ceiling": 0.02,
        "drawdown_kill_pct": 0.10,
        "epoch": 3,
        "policy_version": "2",
    }


def test_load_tier0_risk_ceilings_epoch_defaults_to_one_when_absent(tmp_path):
    artifact = tmp_path / "risk_policy.yaml"
    artifact.write_text(
        "policy_version: \"2\"\n"
        "tier0:\n"
        "  aggregate_open_loss_pct_ceiling: 0.02\n"
        "  drawdown_kill_pct: 0.10\n"
    )
    ceilings = load_tier0_risk_ceilings(path=artifact)
    assert ceilings["epoch"] == 1


def test_load_tier0_risk_ceilings_missing_file_falls_back_to_documented_defaults(tmp_path):
    ceilings = load_tier0_risk_ceilings(path=tmp_path / "does_not_exist.yaml")
    assert ceilings == {
        "aggregate_open_loss_pct_ceiling": 0.02,
        "drawdown_kill_pct": 0.10,
        "epoch": 1,
        "policy_version": "",
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


# Start-equity override_id (episode selector).
def test_start_equity_override_id_includes_epoch():
    assert tier0_start_equity_override_id(1) == "tier0:start_equity:epoch:1"
    assert tier0_start_equity_override_id(2) == "tier0:start_equity:epoch:2"
    assert tier0_start_equity_override_id("3") == "tier0:start_equity:epoch:3"


def test_start_equity_override_id_differs_across_epochs():
    # A bumped epoch is a DIFFERENT override_id -- no seed found under it,
    # so the caller's SELECT-before-write naturally re-seeds. This is the
    # whole "new episode = re-seed" mechanism; no other code path needed.
    assert tier0_start_equity_override_id(1) != tier0_start_equity_override_id(2)


# Seed encode/decode round trip.
def test_build_and_parse_tier0_seed_round_trips():
    value = build_tier0_seed_value(
        started_at_utc="2026-08-24T09:00:00+00:00",
        start_equity_usd=268.0,
        policy_version="3",
        epoch=1,
    )
    seed = parse_tier0_seed(value)
    assert seed == {
        "started_at_utc": "2026-08-24T09:00:00+00:00",
        "start_equity_usd": 268.0,
        "policy_version": "3",
        "epoch": 1,
    }


def test_build_tier0_seed_value_rejects_nonpositive_equity():
    with pytest.raises(ValueError):
        build_tier0_seed_value(
            started_at_utc="2026-08-24T09:00:00+00:00",
            start_equity_usd=0.0,
            policy_version="3",
            epoch=1,
        )


def test_build_tier0_seed_value_rejects_empty_started_at():
    with pytest.raises(ValueError):
        build_tier0_seed_value(
            started_at_utc="",
            start_equity_usd=100.0,
            policy_version="3",
            epoch=1,
        )


def test_parse_tier0_seed_rejects_malformed_json():
    with pytest.raises(ValueError):
        parse_tier0_seed("not json at all")


def test_parse_tier0_seed_rejects_missing_fields():
    with pytest.raises(ValueError):
        parse_tier0_seed('{"started_at_utc": "2026-08-24T09:00:00+00:00"}')


def test_parse_tier0_seed_rejects_nonpositive_equity():
    with pytest.raises(ValueError):
        parse_tier0_seed(
            '{"started_at_utc": "2026-08-24T09:00:00+00:00", '
            '"start_equity_usd": -5.0, "epoch": 1}'
        )


# Realized-P&L reducer: recomputes fresh via compute_realized_pnl_usd, never
# reads a pre-stored realized_pnl_usd aggregate.
def test_tier0_realized_pnl_usd_sums_local_economics():
    positions = [
        Tier0ClosedPositionFacts(shares=100.0, exit_price=1.0, cost_basis_usd=15.0, entry_price=0.15),
        Tier0ClosedPositionFacts(shares=50.0, exit_price=0.0, cost_basis_usd=10.0, entry_price=0.20),
    ]
    # position 1: 100*1.0 - 15.0 = 85.0 (won); position 2: 50*0.0 - 10.0 = -10.0 (lost)
    assert tier0_realized_pnl_usd(closed_positions=positions) == 75.0


def test_tier0_realized_pnl_usd_prefers_chain_economics_over_local():
    positions = [
        Tier0ClosedPositionFacts(
            shares=100.0,
            exit_price=1.0,
            cost_basis_usd=999.0,  # wrong local bookkeeping
            entry_price=0.15,
            chain_shares=100.0,
            chain_cost_basis_usd=15.0,  # correct chain-verified basis
            chain_avg_price=0.15,
        ),
    ]
    assert tier0_realized_pnl_usd(closed_positions=positions) == 85.0


def test_tier0_realized_pnl_usd_excludes_unsettled_position():
    positions = [
        Tier0ClosedPositionFacts(shares=100.0, exit_price=None, cost_basis_usd=15.0, entry_price=0.15),
    ]
    assert tier0_realized_pnl_usd(closed_positions=positions) == 0.0


def test_tier0_realized_pnl_usd_empty_is_zero():
    assert tier0_realized_pnl_usd(closed_positions=()) == 0.0


class _Level:
    def __init__(self, price):
        self.price = price


class _Curve:
    def __init__(self, prices):
        self.levels = tuple(_Level(p) for p in prices)


class _Candidate:
    """GlobalSingleOrderCandidate surface as seen by tier0_decision_price:
    economic_cost_curve only -- deliberately NO limit_price attribute, the
    exact shape the live wiring bug read against."""

    def __init__(self, curve):
        self.economic_cost_curve = curve


def test_decision_price_is_cheapest_ask_and_admits_cheap_taker():
    price = tier0_decision_price(_Candidate(_Curve((0.12, 0.14))))
    assert price == 0.12
    assert (
        tier0_price_rejection_reason(execution_price=price, limit_price=price)
        is None
    )


def test_decision_price_none_without_curve_rejects_inputs_missing():
    for cand in (_Candidate(None), _Candidate(_Curve(()))):
        price = tier0_decision_price(cand)
        assert price is None
        reason = tier0_price_rejection_reason(
            execution_price=price, limit_price=price
        )
        assert reason == f"{TIER0_REJECT_PRICE_TOO_HIGH}:inputs=missing"


def test_decision_price_never_reads_limit_price_attribute():
    class _Trap(_Candidate):
        @property
        def limit_price(self):
            raise AssertionError("gate must not read candidate.limit_price")

    assert tier0_decision_price(_Trap(_Curve((0.2,)))) == 0.2


# Flat-stake capital envelope: the solver-side seam that makes W3 itself size
# the flat micro order (live 2026-08-26/27: the downstream _robust_stake_usd
# override never reached global_decision.shares — 28-share and 12-share
# Kelly-sized fills burned the 2% aggregate ceiling in two orders).
class _SizedCurve(_Curve):
    def __init__(self, prices, min_order_size):
        super().__init__(prices)
        self.min_order_size = min_order_size


def test_flat_stake_notional_cap_is_min_order_cost_with_headroom():
    cand = _Candidate(_SizedCurve((0.11, 0.15), min_order_size=5.0))
    cap = tier0_flat_stake_notional_cap_usd(cand)
    # 5 shares x $0.11 = $0.55, plus 1e-6 relative headroom.
    assert cap == pytest.approx(0.55 * (1.0 + 1e-6), rel=1e-12)
    # Envelope always affords the venue-min order the solver must express.
    assert cap >= 5.0 * 0.11


def test_flat_stake_notional_cap_none_for_sell_action():
    cand = _Candidate(_SizedCurve((0.11,), min_order_size=5.0))
    cand.action = "SELL"
    assert tier0_flat_stake_notional_cap_usd(cand) is None


def test_flat_stake_notional_cap_none_without_usable_curve():
    assert tier0_flat_stake_notional_cap_usd(_Candidate(None)) is None
    assert tier0_flat_stake_notional_cap_usd(_Candidate(_Curve(()))) is None
    # Curve with levels but no min_order_size attribute (defensive duck-typing).
    assert tier0_flat_stake_notional_cap_usd(_Candidate(_Curve((0.11,)))) is None


def test_flat_stake_notional_cap_is_price_and_edge_independent():
    import inspect

    params = set(inspect.signature(tier0_flat_stake_notional_cap_usd).parameters)
    assert not params & {"q", "q_posterior", "edge", "q_lcb", "kelly_size_usd"}
