# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/reference/zeus_strategy_spec.md §16 + §20.2
#                  + docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §16
"""Tests for DeterministicEdgeVerifier (Pipeline A) and promotion_proof_router.

Relationship invariants tested:

Pipeline A — DeterministicEdgeVerifier:
  R1: READY iff clause 1 (reconciliation) AND clause 2 (aggregate profit) both pass.
  R2: NOT_READY when clause 1 fails alone (mismatch count > 0, profit OK).
  R3: NOT_READY when clause 2 fails alone (reconciliation OK, aggregate ≤ 0).
  R4: NOT_READY when both clauses fail.
  R5: NOT_READY when n_records < min_records.
  R6: operator_ref required (ValueError) when tier_target >= LIVE_PILOT_TINY.
  R7: operator_ref NOT required when tier_target < LIVE_PILOT_TINY.
  R8: VectorEdgeDecision / RealizedVectorOutcome pairs accepted correctly.
  R9: Mixed wrong pair types raise TypeError.

Proof-class router:
  R10: A-set keys each resolve to "A".
  R11: B-set keys each resolve to "B" (including unknown keys).
  R12: center_sell with proof_type="pair_parity" → "A".
  R13: center_sell with no proof_type → "B".
  R14: stale_quote_detector with proof_type="fok_latency" → "A".
  R15: stale_quote_detector with no proof_type → "B".
  R16: All 13 strategies from §22 resolve without error.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.analysis.deterministic_edge_report import (
    DeterministicEdgeReport,
    DeterministicEdgeVerifier,
    RealizedOutcome,
    RealizedVectorOutcome,
)
from src.analysis.promotion_proof_router import (
    is_pipeline_a,
    is_pipeline_b,
    route_proof_class,
)
from src.analysis.promotion_readiness import ReadinessVerdict
from src.contracts.evidence_tier import EvidenceTier
from src.strategy.candidates import (
    DeterministicEdgeDecision,
    LegIntent,
    VectorEdgeDecision,
)


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

def _make_det_decision(
    strategy_key: str = "settlement_capture",
    proof_type: str = "physical_interval_subset",
    side: str = "buy_yes",
    token_id: str = "tok-abc",
    executable_price: Decimal = Decimal("0.80"),
    fee: Decimal = Decimal("0.002"),
    deterministic_payoff: Decimal = Decimal("1.00"),
    # profit is computed: payoff - price - fee = 0.198 by default
) -> DeterministicEdgeDecision:
    profit = deterministic_payoff - executable_price - fee
    return DeterministicEdgeDecision(
        strategy_key=strategy_key,
        proof_type=proof_type,
        side=side,  # type: ignore[arg-type]
        token_id=token_id,
        executable_price=executable_price,
        fee=fee,
        deterministic_payoff=deterministic_payoff,
        deterministic_profit=profit,
        proof_inputs_hash="a" * 64,
    )


def _make_realized(
    payoff: Decimal = Decimal("1.00"),
    cost: Decimal = Decimal("0.80"),
    fee: Decimal = Decimal("0.002"),
) -> RealizedOutcome:
    return RealizedOutcome(
        realized_payoff=payoff,
        realized_cost=cost,
        realized_fee=fee,
    )


def _make_vector_decision(
    vector_profit: Decimal = Decimal("0.05"),
) -> VectorEdgeDecision:
    leg = LegIntent(
        side="buy_yes",
        condition_id="cond-001",
        quantity=Decimal("1"),
        price_limit=Decimal("0.50"),
    )
    return VectorEdgeDecision(
        strategy_key="neg_risk_basket",
        proof_type="complete_family_basket",
        basket_execution_id="",
        legs=(leg,),
        q_star=Decimal("1"),
        vector_cost=Decimal("0.50"),
        vector_fee=Decimal("0.002"),
        vector_payoff=Decimal("0.552") + vector_profit,
        vector_profit=vector_profit,
        proof_inputs_hash="b" * 64,
    )


def _make_vector_realized(
    payoff: Decimal = Decimal("0.60"),
    cost: Decimal = Decimal("0.50"),
    fee: Decimal = Decimal("0.002"),
) -> RealizedVectorOutcome:
    return RealizedVectorOutcome(
        realized_payoff=payoff,
        realized_cost=cost,
        realized_fee=fee,
    )


def _verifier(
    tier_required: EvidenceTier = EvidenceTier.LIVE_PILOT_TINY,
    tolerance: Decimal = Decimal("0.001"),
    min_records: int = 1,
) -> DeterministicEdgeVerifier:
    return DeterministicEdgeVerifier(
        tier_required_for_live=tier_required,
        reconciliation_tolerance=tolerance,
        min_records=min_records,
    )


# ---------------------------------------------------------------------------
# R1: READY when both clauses pass
# ---------------------------------------------------------------------------

def test_r1_ready_when_both_clauses_pass() -> None:
    """Pipeline A READY iff clause 1 AND clause 2 both pass."""
    decision = _make_det_decision()
    realized = _make_realized()  # profit matches: 1.00 - 0.80 - 0.002 = 0.198
    records = [(decision, realized)]

    report = _verifier(tier_required=EvidenceTier.LIVE_PILOT_TINY).verify(
        "settlement_capture",
        EvidenceTier.SHADOW_PASS,
        records,
        operator_ref="test-operator-ref-1",
    )

    assert report.verdict == ReadinessVerdict.READY
    assert report.clause1_mismatch_count == 0
    assert report.clause2_aggregate_profit > Decimal(0)
    assert all(s.passed for s in report.signals)


# ---------------------------------------------------------------------------
# R2: NOT_READY when clause 1 fails alone (reconciliation fails, profit OK)
# ---------------------------------------------------------------------------

def test_r2_not_ready_clause1_fails_alone() -> None:
    """NOT_READY when computed_profit ≠ realized_profit, even if aggregate > 0."""
    decision = _make_det_decision(
        deterministic_payoff=Decimal("1.00"),
        executable_price=Decimal("0.80"),
        fee=Decimal("0.002"),
    )
    # realized profit deliberately mismatched (1.00 - 0.70 - 0.002 = 0.298, computed = 0.198)
    realized = _make_realized(payoff=Decimal("1.00"), cost=Decimal("0.70"), fee=Decimal("0.002"))
    records = [(decision, realized)]

    report = _verifier(tolerance=Decimal("0.01")).verify(
        "settlement_capture",
        EvidenceTier.SHADOW_PASS,
        records,
    )

    assert report.verdict == ReadinessVerdict.NOT_READY
    assert report.clause1_mismatch_count == 1
    # clause 2 (aggregate) should still pass since profit > 0
    clause2 = next(s for s in report.signals if s.signal_name == "aggregate_profit")
    assert clause2.passed is True
    clause1 = next(s for s in report.signals if s.signal_name == "reconciliation")
    assert clause1.passed is False


# ---------------------------------------------------------------------------
# R3: NOT_READY when clause 2 fails alone (aggregate ≤ 0, reconciliation OK)
# ---------------------------------------------------------------------------

def test_r3_not_ready_clause2_fails_alone() -> None:
    """NOT_READY when Σ(payoff − cost − fee) ≤ 0, even if reconciliation passes."""
    # Build decisions where computed profit matches realized, but realized aggregate is negative.
    # realized_profit = 1.00 - 1.10 - 0.002 = -0.102 (loss)
    # computed_profit should match: we set deterministic_profit explicitly.
    # But DeterministicEdgeDecision requires profit > 0 — so construct via the cost asymmetry:
    # give the *decision* a profitable computed profit, but provide realized that has realized loss.
    # The realized loss should trip clause 2 while reconciliation is within tolerance.

    # Decision: profit = 0.198 (> 0, valid)
    decision = _make_det_decision(
        executable_price=Decimal("0.80"),
        fee=Decimal("0.002"),
        deterministic_payoff=Decimal("1.00"),
    )
    # Realized: also profit = 0.198 (reconciliation passes), but we need multiple records
    # with aggregate ≤ 0. Use two records: one positive, one large negative to flip aggregate.
    realized_pos = _make_realized(
        payoff=Decimal("1.00"), cost=Decimal("0.80"), fee=Decimal("0.002")
    )  # profit = 0.198
    realized_neg = _make_realized(
        payoff=Decimal("1.00"), cost=Decimal("1.10"), fee=Decimal("0.002")
    )  # profit = -0.102

    # For clause 1, decision.deterministic_profit = 0.198. realized_pos.realized_profit = 0.198
    # (passes). realized_neg.realized_profit = -0.102 — large mismatch.
    # To isolate clause 2 from clause 1, we need realized profits all within tolerance.
    # Strategy: use a wide tolerance so clause 1 passes despite mismatch.
    records = [
        (decision, realized_pos),
        (decision, realized_neg),
    ]

    # With tolerance=1.0 (wider than any mismatch here), clause 1 should pass.
    report = _verifier(tolerance=Decimal("1.0")).verify(
        "settlement_capture",
        EvidenceTier.SHADOW_PASS,
        records,
    )

    # Aggregate: (1.00 + 1.00) - (0.80 + 1.10) - (0.002 + 0.002) = 2.00 - 1.90 - 0.004 = 0.096
    # That's actually positive... Use numbers that guarantee aggregate ≤ 0:
    # We need Σ(payoff - cost - fee) ≤ 0, so Σcost + Σfee >= Σpayoff.
    # With two records: pos=(1.00, 0.80, 0.002), neg=(1.00, 1.25, 0.002):
    # aggregate = (1.00+1.00) - (0.80+1.25) - (0.002+0.002) = 2.00 - 2.05 - 0.004 = -0.054 (< 0)
    realized_neg2 = _make_realized(
        payoff=Decimal("1.00"), cost=Decimal("1.25"), fee=Decimal("0.002")
    )
    records2 = [
        (decision, realized_pos),
        (decision, realized_neg2),
    ]
    report = _verifier(tolerance=Decimal("1.0")).verify(
        "settlement_capture",
        EvidenceTier.SHADOW_PASS,
        records2,
    )

    assert report.verdict == ReadinessVerdict.NOT_READY
    assert report.clause2_aggregate_profit < Decimal(0)
    clause1 = next(s for s in report.signals if s.signal_name == "reconciliation")
    clause2 = next(s for s in report.signals if s.signal_name == "aggregate_profit")
    assert clause1.passed is True   # wide tolerance
    assert clause2.passed is False


# ---------------------------------------------------------------------------
# R4: NOT_READY when both clauses fail
# ---------------------------------------------------------------------------

def test_r4_not_ready_both_clauses_fail() -> None:
    """NOT_READY when both reconciliation and aggregate both fail."""
    decision = _make_det_decision()
    # Mismatch AND aggregate loss
    realized = _make_realized(
        payoff=Decimal("1.00"), cost=Decimal("1.20"), fee=Decimal("0.002")
    )  # realized profit = -0.202 (loss); computed = 0.198 → large mismatch
    records = [(decision, realized)]

    report = _verifier(tolerance=Decimal("0.01")).verify(
        "settlement_capture",
        EvidenceTier.SHADOW_PASS,
        records,
    )

    assert report.verdict == ReadinessVerdict.NOT_READY
    assert report.clause1_mismatch_count >= 1
    assert report.clause2_aggregate_profit < Decimal(0)
    assert all(not s.passed for s in report.signals)


# ---------------------------------------------------------------------------
# R5: NOT_READY when n_records < min_records
# ---------------------------------------------------------------------------

def test_r5_not_ready_insufficient_records() -> None:
    """NOT_READY when n_records < min_records, regardless of record quality."""
    decision = _make_det_decision()
    realized = _make_realized()
    records = [(decision, realized)]

    report = _verifier(min_records=5).verify(
        "settlement_capture",
        EvidenceTier.SHADOW_PASS,
        records,
    )

    assert report.verdict == ReadinessVerdict.NOT_READY
    assert report.n_records == 1


# ---------------------------------------------------------------------------
# R6: operator_ref required when tier_target >= LIVE_PILOT_TINY
# ---------------------------------------------------------------------------

def test_r6_operator_ref_required_for_live_tier() -> None:
    """ValueError raised when recommended tier_target >= LIVE_PILOT_TINY and no operator_ref."""
    decision = _make_det_decision()
    realized = _make_realized()
    records = [(decision, realized)]

    # tier_current = SHADOW_PASS(3), tier_required = LIVE_PILOT_TINY(5)
    # All pass → tier_target = PAPER_COHORT(4) < LIVE_PILOT_TINY → no error yet
    report = _verifier(tier_required=EvidenceTier.LIVE_PILOT_TINY).verify(
        "settlement_capture", EvidenceTier.SHADOW_PASS, records
    )
    # PAPER_COHORT < LIVE_PILOT_TINY, so no ValueError
    assert report.tier_target == EvidenceTier.PAPER_COHORT

    # Now tier_current = PAPER_COHORT(4), tier_required = LIVE_PILOT_TINY(5)
    # All pass → tier_target = LIVE_PILOT_TINY(5) → must require operator_ref
    with pytest.raises(ValueError, match="operator_ref"):
        _verifier(tier_required=EvidenceTier.LIVE_PILOT_TINY).verify(
            "settlement_capture",
            EvidenceTier.PAPER_COHORT,
            records,
        )


# ---------------------------------------------------------------------------
# R7: operator_ref NOT required when tier_target < LIVE_PILOT_TINY
# ---------------------------------------------------------------------------

def test_r7_operator_ref_not_required_below_live() -> None:
    """No ValueError when recommended tier_target < LIVE_PILOT_TINY."""
    decision = _make_det_decision()
    realized = _make_realized()
    records = [(decision, realized)]

    # tier_current = SHADOW_PASS(3), tier_required = LIVE_PILOT_TINY(5)
    # Passes → tier_target = PAPER_COHORT(4) — no operator_ref needed
    report = _verifier(tier_required=EvidenceTier.LIVE_PILOT_TINY).verify(
        "settlement_capture",
        EvidenceTier.SHADOW_PASS,
        records,
        # No operator_ref — should be fine
    )
    assert report.verdict == ReadinessVerdict.READY
    assert report.operator_ref_required is False


# ---------------------------------------------------------------------------
# R8: VectorEdgeDecision + RealizedVectorOutcome accepted correctly
# ---------------------------------------------------------------------------

def test_r8_vector_decision_accepted() -> None:
    """VectorEdgeDecision / RealizedVectorOutcome pairs route through verifier correctly."""
    decision = _make_vector_decision(vector_profit=Decimal("0.05"))
    # realized profit = 0.60 - 0.50 - 0.002 = 0.098; computed = 0.05 → within tolerance=0.1
    realized = _make_vector_realized(
        payoff=Decimal("0.60"), cost=Decimal("0.50"), fee=Decimal("0.002")
    )
    records = [(decision, realized)]

    report = _verifier(tolerance=Decimal("0.1")).verify(
        "neg_risk_basket",
        EvidenceTier.SHADOW_PASS,
        records,
        operator_ref="test-operator-ref",
    )

    assert report.verdict == ReadinessVerdict.READY


# ---------------------------------------------------------------------------
# R9: Wrong pair types raise TypeError
# ---------------------------------------------------------------------------

def test_r9_wrong_pair_types_raise_type_error() -> None:
    """TypeError when DeterministicEdgeDecision paired with RealizedVectorOutcome."""
    decision = _make_det_decision()
    wrong_realized = _make_vector_realized()
    records = [(decision, wrong_realized)]  # type: ignore[list-item]

    with pytest.raises(TypeError, match="DeterministicEdgeDecision must be paired with RealizedOutcome"):
        _verifier().verify("settlement_capture", EvidenceTier.SHADOW_PASS, records)


def test_r9b_vector_with_wrong_realized_type_raises() -> None:
    """TypeError when VectorEdgeDecision paired with RealizedOutcome."""
    decision = _make_vector_decision()
    wrong_realized = _make_realized()
    records = [(decision, wrong_realized)]  # type: ignore[list-item]

    with pytest.raises(TypeError, match="VectorEdgeDecision must be paired with RealizedVectorOutcome"):
        _verifier().verify("neg_risk_basket", EvidenceTier.SHADOW_PASS, records)


# ===========================================================================
# Proof-class router tests
# ===========================================================================

# ---------------------------------------------------------------------------
# R10: A-set strategy_keys → "A"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("strategy_key", [
    "neg_risk_basket",
    "settlement_capture",
    "resolution_window_maker",
])
def test_r10_a_set_routes_to_a(strategy_key: str) -> None:
    """All pure Pipeline-A strategies route to 'A' without proof_type."""
    assert route_proof_class(strategy_key) == "A"
    assert is_pipeline_a(strategy_key)
    assert not is_pipeline_b(strategy_key)


# ---------------------------------------------------------------------------
# R11: B-set strategy_keys → "B" (including unknown keys)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("strategy_key", [
    "opening_inertia",
    "center_buy",
    "shoulder_buy",
    "weather_event_arbitrage",
    "liquidity_provision_with_heartbeat",
    "cross_market_correlation_hedge",
    "imminent_open_capture",
    "totally_unknown_strategy",
])
def test_r11_b_set_routes_to_b(strategy_key: str) -> None:
    """All Pipeline-B strategies (and unknowns) route to 'B'."""
    assert route_proof_class(strategy_key) == "B"
    assert is_pipeline_b(strategy_key)
    assert not is_pipeline_a(strategy_key)


# ---------------------------------------------------------------------------
# R12: center_sell + proof_type="pair_parity" → "A"
# ---------------------------------------------------------------------------

def test_r12_center_sell_parity_subtype_routes_to_a() -> None:
    """center_sell with proof_type='pair_parity' (parity sub-type) routes to Pipeline A."""
    assert route_proof_class("center_sell", proof_type="pair_parity") == "A"
    assert is_pipeline_a("center_sell", proof_type="pair_parity")


# ---------------------------------------------------------------------------
# R13: center_sell without proof_type → "B"
# ---------------------------------------------------------------------------

def test_r13_center_sell_default_routes_to_b() -> None:
    """center_sell without proof_type (model-NO sub-type default) routes to Pipeline B."""
    assert route_proof_class("center_sell") == "B"
    assert route_proof_class("center_sell", proof_type="model_no") == "B"
    assert route_proof_class("center_sell", proof_type="unknown_subtype") == "B"


# ---------------------------------------------------------------------------
# R14: stale_quote_detector + proof_type="fok_latency" → "A"
# ---------------------------------------------------------------------------

def test_r14_stale_quote_fok_subtype_routes_to_a() -> None:
    """stale_quote_detector with proof_type='fok_latency' routes to Pipeline A."""
    assert route_proof_class("stale_quote_detector", proof_type="fok_latency") == "A"
    assert is_pipeline_a("stale_quote_detector", proof_type="fok_latency")


# ---------------------------------------------------------------------------
# R15: stale_quote_detector without proof_type → "B"
# ---------------------------------------------------------------------------

def test_r15_stale_quote_default_routes_to_b() -> None:
    """stale_quote_detector without proof_type (non-FOK default) routes to Pipeline B."""
    assert route_proof_class("stale_quote_detector") == "B"
    assert route_proof_class("stale_quote_detector", proof_type="some_other") == "B"


# ---------------------------------------------------------------------------
# R16: All 13 strategies from §22 resolve without error
# ---------------------------------------------------------------------------

_SPEC_22_STRATEGIES = [
    # A-set (unconditional)
    ("settlement_capture", None),
    ("neg_risk_basket", None),
    ("resolution_window_maker", None),
    # A-set (sub-typed)
    ("center_sell", "pair_parity"),
    ("stale_quote_detector", "fok_latency"),
    # B-set
    ("center_buy", None),
    ("opening_inertia", None),
    ("imminent_open_capture", None),
    ("shoulder_buy", None),
    ("weather_event_arbitrage", None),
    ("liquidity_provision_with_heartbeat", None),
    ("cross_market_correlation_hedge", None),
    # B-set (sub-typed defaults)
    ("center_sell", None),
    ("stale_quote_detector", None),
]

_SPEC_22_EXPECTED: dict[tuple[str, str | None], str] = {
    ("settlement_capture", None): "A",
    ("neg_risk_basket", None): "A",
    ("resolution_window_maker", None): "A",
    ("center_sell", "pair_parity"): "A",
    ("stale_quote_detector", "fok_latency"): "A",
    ("center_buy", None): "B",
    ("opening_inertia", None): "B",
    ("imminent_open_capture", None): "B",
    ("shoulder_buy", None): "B",
    ("weather_event_arbitrage", None): "B",
    ("liquidity_provision_with_heartbeat", None): "B",
    ("cross_market_correlation_hedge", None): "B",
    ("center_sell", None): "B",
    ("stale_quote_detector", None): "B",
}


@pytest.mark.parametrize("strategy_key,proof_type", _SPEC_22_STRATEGIES)
def test_r16_spec22_all_strategies_resolve(
    strategy_key: str, proof_type: str | None
) -> None:
    """All 13 §22 strategies resolve to exactly one pipeline without error."""
    result = route_proof_class(strategy_key, proof_type=proof_type)
    expected = _SPEC_22_EXPECTED[(strategy_key, proof_type)]
    assert result == expected, (
        f"strategy_key={strategy_key!r} proof_type={proof_type!r}: "
        f"expected {expected!r}, got {result!r}"
    )
