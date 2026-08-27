# Created: 2026-08-24
# Last reused/audited: 2026-08-27
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 6. Integration wiring for src/strategy/tier0_policy.py's admission
#   gate inside event_bound_live_adapter_from_trade_conn's
#   _current_entry_candidate_policy. Harness pattern copied from
#   test_event_reactor_adapter_family_scoped_entry_block.py::
#   test_current_statistical_maker_reaches_capital_policy (same monkeypatch
#   shape: fake process_current_global_batch calls the adapter's
#   candidate_policy_rejection_resolver directly and captures the reason).
"""Tier-0 research mode admission and fixed-proposal selection wiring."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import src.engine.event_reactor_adapter as era
from src.contracts.executable_cost_curve import BookLevel, ExecutableCostCurve, FeeModel
from src.engine import global_batch_runtime
from src.events.candidate_binding import weather_family_id
from src.events.opportunity_event import OpportunityEvent
from src.solve.solver import (
    GlobalSingleOrderCandidate,
    _score_global_single_order_buy_expected,
    executable_curve_identity,
)

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
FAMILY_DALLAS_HIGH = weather_family_id(city="Dallas", target_date="2026-08-25", metric="high")


def _make_event(*, city="Dallas", target_date="2026-08-25", metric="high", event_id="evt-1"):
    payload = {"city": city, "target_date": target_date, "metric": metric}
    return OpportunityEvent(
        event_id=event_id,
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"{city}:{target_date}:{metric}",
        source="test",
        observed_at=NOW.isoformat(),
        available_at=NOW.isoformat(),
        received_at=NOW.isoformat(),
        causal_snapshot_id="snap-1",
        payload_hash="payload-hash",
        idempotency_key="idem-1",
        priority=0,
        expires_at=None,
        payload_json=json.dumps(payload),
        schema_version=1,
        created_at=NOW.isoformat(),
    )


def _drive_candidate_policies(monkeypatch, candidates, *, held_families=()):
    """Wire one adapter and return the policy reason for each candidate."""

    captured = {}

    def fake_prepare(_event, **_kwargs):
        return SimpleNamespace(
            probability_witness=SimpleNamespace(
                family_key=FAMILY_DALLAS_HIGH,
                witness_identity="current-statistical-q",
            ),
            day0_payoff_truth_by_bin_side=(),
            candidate_seeds=(),
        )

    def fake_process(events, **kwargs):
        receipt = kwargs["prepare_event"](events[0], NOW)
        assert receipt.prepared_global_family is not None
        resolve = kwargs["candidate_policy_rejection_resolver"]
        captured["reasons"] = [resolve(candidate) for candidate in candidates]
        return SimpleNamespace(events=tuple(events), winner_event_id=None, receipts={})

    monkeypatch.setattr(era, "_prepare_current_global_probability_family", fake_prepare)
    monkeypatch.setattr(era, "_entry_global_submit_suppression_reason", lambda: None)
    monkeypatch.setattr(era, "_edli_forecast_lane_phase_evidence", lambda **_kwargs: SimpleNamespace())
    monkeypatch.setattr(era, "_forecast_lane_phase_admits", lambda _evidence: True)
    monkeypatch.setattr(era, "_event_bound_strategy_key", lambda **_kwargs: "test")
    monkeypatch.setattr(era, "_global_current_entry_feasibility_rejection_reason", lambda *_a, **_kw: None)
    monkeypatch.setattr(global_batch_runtime, "process_current_global_batch", fake_process)
    monkeypatch.setattr(
        global_batch_runtime,
        "_current_held_weather_families",
        lambda _conn: tuple(held_families),
    )

    adapter = era.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: era.RiskLevel.GREEN,
        forecast_conn=sqlite3.connect(":memory:"),
        topology_conn=sqlite3.connect(":memory:"),
        calibration_conn=sqlite3.connect(":memory:"),
        auction_capital_authority=SimpleNamespace(),
    )
    adapter.process_global_batch((_make_event(),), NOW)
    return captured["reasons"]


def _drive_candidate_policy(monkeypatch, candidate, *, held_families=()):
    return _drive_candidate_policies(
        monkeypatch,
        (candidate,),
        held_families=held_families,
    )[0]


class _Curve:
    def __init__(self, price: float, min_order_size: str = "5") -> None:
        self.levels = (
            SimpleNamespace(price=Decimal(str(price)), size=Decimal("100")),
        )
        self.min_order_size = Decimal(min_order_size)
        self.fee_model = SimpleNamespace(all_in_price=lambda value: value)

    def avg_cost_for_shares(self, _shares: Decimal):
        return SimpleNamespace(value=float(self.levels[0].price))


def _candidate(*, execution_mode="TAKER_LIMIT", limit_price=0.15):
    # Mirrors GlobalSingleOrderCandidate's real surface: the decision price
    # lives on economic_cost_curve.levels[0].price and there is NO limit_price
    # attribute (the 2026-08-25 wiring bug read a nonexistent field and
    # rejected every BUY with inputs=missing; this fixture carried the
    # phantom field, which is why it never caught that).
    return SimpleNamespace(
        action="BUY",
        execution_mode=execution_mode,
        family_key=FAMILY_DALLAS_HIGH,
        bin_id="bin-a",
        side="YES",
        economic_cost_curve=_Curve(limit_price),
    )


def test_flag_off_maker_rest_reaches_capital_policy_unblocked(monkeypatch):
    monkeypatch.setattr(era, "tier0_research_mode_enabled", lambda: False)
    reason = _drive_candidate_policy(
        monkeypatch,
        _candidate(execution_mode="MAKER_REST", limit_price=0.90),
    )
    assert reason is None


def test_flag_on_maker_rest_rejected_typed(monkeypatch):
    monkeypatch.setattr(era, "tier0_research_mode_enabled", lambda: True)
    reason = _drive_candidate_policy(
        monkeypatch,
        _candidate(execution_mode="MAKER_REST", limit_price=0.15),
    )
    assert reason is not None
    assert reason.startswith("TIER0_MAKER_REST_DISALLOWED")


def test_flag_on_price_above_cap_rejected_typed(monkeypatch):
    # 2026-08-26 window correction: cap is the venue band ceiling (0.95).
    monkeypatch.setattr(era, "tier0_research_mode_enabled", lambda: True)
    reason = _drive_candidate_policy(
        monkeypatch,
        _candidate(execution_mode="TAKER_LIMIT", limit_price=0.96),
    )
    assert reason is not None
    assert reason.startswith("TIER0_MAX_ENTRY_PRICE_EXCEEDED")


def test_flag_on_rich_taker_now_admitted(monkeypatch):
    # price>0.75 is the only positive-edge class in the fills audit; it must
    # pass the tier0 price gate (risk bounded by flat venue-min stake).
    monkeypatch.setattr(era, "tier0_research_mode_enabled", lambda: True)
    reason = _drive_candidate_policy(
        monkeypatch,
        _candidate(execution_mode="TAKER_LIMIT", limit_price=0.90),
        held_families=(),
    )
    assert reason is None


def test_flag_on_cheap_taker_unoccupied_cluster_reaches_capital_policy(monkeypatch):
    monkeypatch.setattr(era, "tier0_research_mode_enabled", lambda: True)
    reason = _drive_candidate_policy(
        monkeypatch,
        _candidate(execution_mode="TAKER_LIMIT", limit_price=0.15),
        held_families=(),
    )
    assert reason is None


def test_flag_on_occupied_cluster_rejected_typed(monkeypatch):
    monkeypatch.setattr(era, "tier0_research_mode_enabled", lambda: True)
    # Held family is a DIFFERENT metric (low) for the SAME city/date — proves
    # the cluster key is (city, target_date), coarser than family_key.
    reason = _drive_candidate_policy(
        monkeypatch,
        _candidate(execution_mode="TAKER_LIMIT", limit_price=0.15),
        held_families=(("Dallas", "2026-08-25", "low"),),
    )
    assert reason is not None
    assert reason.startswith("TIER0_CLUSTER_OCCUPIED")


def test_flag_on_different_cluster_not_blocked(monkeypatch):
    monkeypatch.setattr(era, "tier0_research_mode_enabled", lambda: True)
    reason = _drive_candidate_policy(
        monkeypatch,
        _candidate(execution_mode="TAKER_LIMIT", limit_price=0.15),
        held_families=(("Miami", "2026-08-25", "high"),),
    )
    assert reason is None


def test_same_cluster_candidates_all_reach_global_capital_comparison(monkeypatch):
    """A preliminary pass is not a fill and must not reserve the cluster."""

    monkeypatch.setattr(era, "tier0_research_mode_enabled", lambda: True)
    reasons = _drive_candidate_policies(
        monkeypatch,
        (
            _candidate(execution_mode="TAKER_LIMIT", limit_price=0.15),
            _candidate(execution_mode="TAKER_LIMIT", limit_price=0.30),
        ),
        held_families=(),
    )
    assert reasons == [None, None]


def _drive_selection_sizing_contract(
    monkeypatch,
    candidates,
    *,
    tier0_enabled: bool,
    current_open_cost_usd: float = 4.10,
    bankroll_usd: float = 265.21,
):
    captured = {}

    def fake_prepare(_event, **_kwargs):
        return SimpleNamespace(
            probability_witness=SimpleNamespace(
                family_key=FAMILY_DALLAS_HIGH,
                witness_identity="current-statistical-q",
            ),
            day0_payoff_truth_by_bin_side=(),
            candidate_seeds=(),
        )

    def fake_process(events, **kwargs):
        receipt = kwargs["prepare_event"](events[0], NOW)
        assert receipt.prepared_global_family is not None
        resolve = kwargs["current_capital_limit_resolver"]
        captured["limits"] = [
            resolve(candidate, "market-1", "event-1", events[0].event_id)
            for candidate in candidates
        ]
        captured["multiplier"] = kwargs["fractional_kelly_multiplier"]
        return SimpleNamespace(events=tuple(events), winner_event_id=None, receipts={})

    monkeypatch.setattr(era, "tier0_research_mode_enabled", lambda: tier0_enabled)
    monkeypatch.setattr(era, "_runtime_kelly_multiplier", lambda: 0.125)
    monkeypatch.setattr(era, "_runtime_bankroll_usd", lambda **_kwargs: bankroll_usd)
    monkeypatch.setattr(era, "_prepare_current_global_probability_family", fake_prepare)
    monkeypatch.setattr(era, "_entry_global_submit_suppression_reason", lambda: None)
    monkeypatch.setattr(era, "_edli_forecast_lane_phase_evidence", lambda **_kwargs: SimpleNamespace())
    monkeypatch.setattr(era, "_forecast_lane_phase_admits", lambda _evidence: True)
    monkeypatch.setattr(era, "_event_bound_strategy_key", lambda **_kwargs: "test")
    monkeypatch.setattr(era, "_global_current_entry_feasibility_rejection_reason", lambda *_a, **_kw: None)
    monkeypatch.setattr(global_batch_runtime, "process_current_global_batch", fake_process)
    monkeypatch.setattr(
        "src.state.portfolio.load_runtime_open_portfolio",
        lambda _conn: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "src.state.portfolio.total_exposure_usd",
        lambda _state: current_open_cost_usd,
    )

    authority = SimpleNamespace(
        capacity_usd=lambda **_kwargs: Decimal("100"),
    )
    adapter = era.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: era.RiskLevel.GREEN,
        forecast_conn=sqlite3.connect(":memory:"),
        topology_conn=sqlite3.connect(":memory:"),
        calibration_conn=sqlite3.connect(":memory:"),
        auction_capital_authority=authority,
    )
    adapter.process_global_batch((_make_event(),), NOW)
    return captured


def test_tier0_selection_compares_actual_flat_proposals_inside_open_loss_ceiling(
    monkeypatch,
):
    result = _drive_selection_sizing_contract(
        monkeypatch,
        (
            _candidate(limit_price=0.35),  # 5 shares cost $1.75 > $1.2042 headroom
            # At 0.08 the separate $1 marketable-notional floor requires
            # 12.5 shares; that exact fixed proposal still fits the headroom.
            _candidate(limit_price=0.08),
        ),
        tier0_enabled=True,
    )

    assert result["limits"] == [Decimal("0"), Decimal("1.000")]
    assert result["multiplier"] == Decimal("0.125")


def test_flat_cap_becomes_the_exact_positive_fixed_proposal_without_kelly_change():
    curve = ExecutableCostCurve(
        token_id="token-cheap",
        side="YES",
        snapshot_id="book-now",
        book_hash="book-hash",
        levels=(BookLevel(price=Decimal("0.08"), size=Decimal("100")),),
        fee_model=FeeModel(Decimal("0")),
        min_tick=Decimal("0.01"),
        min_order_size=Decimal("5"),
        quote_ttl=timedelta(seconds=30),
    )
    candidate = GlobalSingleOrderCandidate(
        candidate_id="cheap-positive",
        family_key=FAMILY_DALLAS_HIGH,
        bin_id="bin-a",
        condition_id="condition-a",
        side="YES",
        token_id=curve.token_id,
        probability_witness_identity="q-now",
        book_snapshot_id=curve.snapshot_id,
        book_captured_at_utc=NOW,
        execution_curve_identity=executable_curve_identity(curve),
        ledger_snapshot_id="ledger-now",
        executable_cost_curve=curve,
        resolution_identity="resolution-a",
        neg_risk=False,
    )

    score = _score_global_single_order_buy_expected(
        candidate,
        payoff_probability_mean=0.18,
        sample_count=51,
        band_alpha=0.05,
        wealth_floor_usd=Decimal("265.21"),
        wealth_ceiling_usd=Decimal("265.21"),
        spendable_cash_usd=Decimal("265.21"),
        capital_limit_usd=Decimal("1.00"),
        fractional_kelly_multiplier=Decimal("0.125"),
        current_token_shares=Decimal("0"),
    )

    assert score.candidate is candidate
    assert score.shares == Decimal("12.5")
    assert score.cost_usd == Decimal("1.000")
    assert score.max_spend_usd == Decimal("1.000")
    assert score.expected_terminal_wealth is not None
    assert score.expected_terminal_wealth.expected_delta_log_wealth > 0
    assert score.expected_terminal_wealth.expected_ev_usd > 0


def test_tier0_off_preserves_allocator_capacity_and_runtime_kelly(monkeypatch):
    result = _drive_selection_sizing_contract(
        monkeypatch,
        (_candidate(limit_price=0.35),),
        tier0_enabled=False,
    )

    assert result["limits"] == [Decimal("100")]
    assert result["multiplier"] == Decimal("0.125")


# Once-per-cycle start-equity seed / drawdown-kill hook (reversal_plan_tier0
# item 6 follow-up). Gating happens at adapter CONSTRUCTION time (a plain
# statement in event_bound_live_adapter_from_trade_conn's body), before any
# per-candidate closures run -- so building the adapter is enough to observe
# whether the hook fired, with no event/candidate drive needed.
def test_flag_off_start_equity_hook_not_called(monkeypatch):
    monkeypatch.setattr(era, "tier0_research_mode_enabled", lambda: False)
    calls = []
    monkeypatch.setattr(
        "src.engine.tier0_drawdown_hook.tier0_seed_and_check_drawdown_kill",
        lambda *a, **kw: calls.append((a, kw)),
    )

    era.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: era.RiskLevel.GREEN,
        auction_capital_authority=SimpleNamespace(),
    )

    assert calls == []


def test_flag_on_start_equity_hook_called_once_per_adapter_construction(monkeypatch):
    monkeypatch.setattr(era, "tier0_research_mode_enabled", lambda: True)
    calls = []
    monkeypatch.setattr(
        "src.engine.tier0_drawdown_hook.tier0_seed_and_check_drawdown_kill",
        lambda *a, **kw: calls.append((a, kw)),
    )
    trade_conn = sqlite3.connect(":memory:")

    era.event_bound_live_adapter_from_trade_conn(
        trade_conn,
        get_current_level=lambda: era.RiskLevel.GREEN,
        auction_capital_authority=SimpleNamespace(),
    )

    assert len(calls) == 1
    (args, kwargs) = calls[0]
    assert args == (trade_conn,)
    assert "bankroll_usd_provider" in kwargs
