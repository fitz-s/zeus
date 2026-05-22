# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §7
#                  + SHOULDER_SELL_EDGE_PROOF.md (refutation verdict)
#                  + docs/reference/zeus_strategy_spec.md §11.4 (proof_type=physical_impossible_tail)
#                  + src/strategy/candidates/__init__.py §19 DeterministicEdgeDecision

"""Relationship tests for shoulder_impossible_tail_capture.

METHODOLOGY: relationship tests FIRST — cross-module invariants before implementation.

Relationships tested:
  R1. shoulder_impossible_tail_capture candidate is in the deterministic family:
      DeterministicEdgeDecision flows out, not CandidateDecision(enter).
  R2. When physical envelope is UNWIRED, candidate emits no_trade(PHYSICAL_ENVELOPE_UNWIRED)
      — data-gated, not enter and not a generic gate.
  R3. If physical_upper_bound < shoulder_threshold (upper), deterministic theorem holds:
      DeterministicEdgeDecision.deterministic_profit > 0 and proof_type == "physical_impossible_tail".
  R4. If physical_upper_bound >= shoulder_threshold (upper), no deterministic theorem:
      no_trade emitted; does NOT enter.
  R5. Symmetric lower shoulder: physical_lower_bound > shoulder_threshold (lower)
      → DeterministicEdgeDecision; else no_trade.
  R6. shoulder_sell refuted path is REMOVED — routing does not produce "shoulder_sell" strategy key
      for open-shoulder buy_no edges from the new candidate.
  R7. DeterministicEdgeDecision carries correct payoff identity: 1 - b_NO - fee,
      and deterministic_profit == 1 - b_NO - fee (all Decimal, consistent with §19).
  R8. proof_inputs_hash is deterministic (same inputs → same hash).
  R9. strategy_key in DeterministicEdgeDecision is "shoulder_impossible_tail_capture".
  R10. PHYSICAL_ENVELOPE_UNWIRED is a registered NoTradeReason member (not missing).
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.contracts.no_trade_reason import NoTradeReason
from src.strategy.candidates import DeterministicEdgeDecision


# ---------------------------------------------------------------------------
# R10: PHYSICAL_ENVELOPE_UNWIRED exists in NoTradeReason
# ---------------------------------------------------------------------------

def test_r10_physical_envelope_unwired_in_no_trade_reason():
    """R10: PHYSICAL_ENVELOPE_UNWIRED is a NoTradeReason member (data-gate for envelope input)."""
    assert hasattr(NoTradeReason, "PHYSICAL_ENVELOPE_UNWIRED"), (
        "NoTradeReason.PHYSICAL_ENVELOPE_UNWIRED missing — "
        "required for shoulder_impossible_tail_capture data-gate"
    )
    # value must be a valid enum member
    member = NoTradeReason.PHYSICAL_ENVELOPE_UNWIRED
    assert member.value is not None


# ---------------------------------------------------------------------------
# Helpers: minimal objects the candidate accepts
# ---------------------------------------------------------------------------

def _make_upper_shoulder_context(
    native_no_ask: float = 0.92,
    physical_upper_bound: float | None = None,
    shoulder_threshold: float = 95.0,
):
    """Build a CandidateContext-like SimpleNamespace for an upper shoulder.

    physical_upper_bound: if None → envelope unwired.
    If set to a value < shoulder_threshold → theorem holds.
    If set to a value >= shoulder_threshold → theorem fails.
    """
    from src.types.market import Bin, BinEdge
    from src.contracts.execution_price import ExecutionPrice

    b = Bin(low=shoulder_threshold, high=None, unit="F", label=f"{shoulder_threshold}°F or higher")
    edge = BinEdge(
        bin=b,
        direction="buy_no",
        edge=0.05,
        ci_lower=0.80,
        ci_upper=0.95,
        p_model=0.08,
        p_market=0.08,
        p_posterior=0.08,
        entry_price=1.0 - native_no_ask,  # YES ask = 1 - NO ask
        p_value=0.10,
        vwmp=0.08,
    )
    analysis = SimpleNamespace(
        native_no_ask=Decimal(str(native_no_ask)),
        physical_upper_bound=(
            Decimal(str(physical_upper_bound)) if physical_upper_bound is not None else None
        ),
        physical_lower_bound=None,
        shoulder_threshold=Decimal(str(shoulder_threshold)),
        shoulder_side="upper",
    )
    city = SimpleNamespace(name="Chicago", timezone="America/Chicago")
    candidate_ns = SimpleNamespace(
        city=city,
        target_date="2026-07-15",
        temperature_metric="high",
        slug="chicago-high-2026-07-15",
        event_id="",
    )
    from src.strategy.candidates import CandidateContext
    from src.contracts.decision_natural_key import make_decision_natural_key
    nk = make_decision_natural_key(
        market_slug="chicago-high-2026-07-15",
        temperature_metric="high",
        target_date="2026-07-15",
        observation_time="2026-07-15T12:00:00Z",
        decision_seq=0,
    )
    # Attach what the candidate reads
    analysis.edge = edge
    analysis.candidate = candidate_ns
    return CandidateContext(
        natural_key=nk,
        observed_at="2026-07-15T12:00:00Z",
        analysis=analysis,
    )


def _make_lower_shoulder_context(
    native_no_ask: float = 0.92,
    physical_lower_bound: float | None = None,
    shoulder_threshold: float = 20.0,
):
    """Build a CandidateContext-like SimpleNamespace for a lower shoulder."""
    from src.types.market import Bin, BinEdge
    from src.contracts.execution_price import ExecutionPrice

    b = Bin(low=None, high=shoulder_threshold, unit="F", label=f"{shoulder_threshold}°F or below")
    edge = BinEdge(
        bin=b,
        direction="buy_no",
        edge=0.05,
        ci_lower=0.80,
        ci_upper=0.95,
        p_model=0.08,
        p_market=0.08,
        p_posterior=0.08,
        entry_price=1.0 - native_no_ask,
        p_value=0.10,
        vwmp=0.08,
    )
    analysis = SimpleNamespace(
        native_no_ask=Decimal(str(native_no_ask)),
        physical_upper_bound=None,
        physical_lower_bound=(
            Decimal(str(physical_lower_bound)) if physical_lower_bound is not None else None
        ),
        shoulder_threshold=Decimal(str(shoulder_threshold)),
        shoulder_side="lower",
    )
    city = SimpleNamespace(name="Chicago", timezone="America/Chicago")
    candidate_ns = SimpleNamespace(
        city=city,
        target_date="2026-07-15",
        temperature_metric="low",
        slug="chicago-low-2026-07-15",
        event_id="",
    )
    from src.strategy.candidates import CandidateContext
    from src.contracts.decision_natural_key import make_decision_natural_key
    nk = make_decision_natural_key(
        market_slug="chicago-low-2026-07-15",
        temperature_metric="low",
        target_date="2026-07-15",
        observation_time="2026-07-15T12:00:00Z",
        decision_seq=0,
    )
    analysis.edge = edge
    analysis.candidate = candidate_ns
    return CandidateContext(
        natural_key=nk,
        observed_at="2026-07-15T12:00:00Z",
        analysis=analysis,
    )


def _invoke_candidate(context, conn=None):
    """Instantiate ShoulderImpossibleTailCapture and call evaluate()."""
    from src.strategy.candidates.shoulder_impossible_tail_capture import ShoulderImpossibleTailCapture
    from datetime import datetime, timezone
    candidate = ShoulderImpossibleTailCapture()
    import sqlite3
    if conn is None:
        conn = sqlite3.connect(":memory:")
    return candidate.evaluate(
        context=context,
        conn=conn,
        decision_time=datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# R2: Unwired physical envelope → no_trade(PHYSICAL_ENVELOPE_UNWIRED)
# ---------------------------------------------------------------------------

def test_r2_unwired_envelope_upper_emits_no_trade():
    """R2: physical_upper_bound=None → no_trade(PHYSICAL_ENVELOPE_UNWIRED), not enter."""
    ctx = _make_upper_shoulder_context(physical_upper_bound=None, shoulder_threshold=95.0)
    result = _invoke_candidate(ctx)
    assert result.outcome == "no_trade"
    assert result.reason == NoTradeReason.PHYSICAL_ENVELOPE_UNWIRED


def test_r2_unwired_envelope_lower_emits_no_trade():
    """R2: physical_lower_bound=None → no_trade(PHYSICAL_ENVELOPE_UNWIRED), not enter."""
    ctx = _make_lower_shoulder_context(physical_lower_bound=None, shoulder_threshold=20.0)
    result = _invoke_candidate(ctx)
    assert result.outcome == "no_trade"
    assert result.reason == NoTradeReason.PHYSICAL_ENVELOPE_UNWIRED


# ---------------------------------------------------------------------------
# R3: physical_upper_bound < shoulder_threshold → DeterministicEdgeDecision
# ---------------------------------------------------------------------------

def test_r3_upper_bound_below_threshold_yields_deterministic_edge():
    """R3: physical_upper_bound=90.0 < threshold=95.0 → DeterministicEdgeDecision."""
    ctx = _make_upper_shoulder_context(
        native_no_ask=0.92,
        physical_upper_bound=90.0,
        shoulder_threshold=95.0,
    )
    result = _invoke_candidate(ctx)
    assert isinstance(result, DeterministicEdgeDecision), (
        f"Expected DeterministicEdgeDecision, got {type(result).__name__}: {result}"
    )


# ---------------------------------------------------------------------------
# R4: physical_upper_bound >= shoulder_threshold → no_trade
# ---------------------------------------------------------------------------

def test_r4_upper_bound_at_threshold_no_trade():
    """R4: physical_upper_bound=95.0 == threshold=95.0 → no_trade (theorem fails)."""
    ctx = _make_upper_shoulder_context(
        physical_upper_bound=95.0,
        shoulder_threshold=95.0,
    )
    result = _invoke_candidate(ctx)
    assert result.outcome == "no_trade"


def test_r4_upper_bound_above_threshold_no_trade():
    """R4: physical_upper_bound=97.0 > threshold=95.0 → no_trade (theorem fails)."""
    ctx = _make_upper_shoulder_context(
        physical_upper_bound=97.0,
        shoulder_threshold=95.0,
    )
    result = _invoke_candidate(ctx)
    assert result.outcome == "no_trade"


# ---------------------------------------------------------------------------
# R5: Symmetric lower shoulder
# ---------------------------------------------------------------------------

def test_r5_lower_bound_above_threshold_yields_deterministic_edge():
    """R5: physical_lower_bound=25.0 > threshold=20.0 → DeterministicEdgeDecision."""
    ctx = _make_lower_shoulder_context(
        native_no_ask=0.91,
        physical_lower_bound=25.0,
        shoulder_threshold=20.0,
    )
    result = _invoke_candidate(ctx)
    assert isinstance(result, DeterministicEdgeDecision), (
        f"Expected DeterministicEdgeDecision, got {type(result).__name__}: {result}"
    )


def test_r5_lower_bound_at_threshold_no_trade():
    """R5: physical_lower_bound=20.0 == threshold=20.0 → no_trade."""
    ctx = _make_lower_shoulder_context(
        physical_lower_bound=20.0,
        shoulder_threshold=20.0,
    )
    result = _invoke_candidate(ctx)
    assert result.outcome == "no_trade"


def test_r5_lower_bound_below_threshold_no_trade():
    """R5: physical_lower_bound=18.0 < threshold=20.0 → no_trade (theorem fails)."""
    ctx = _make_lower_shoulder_context(
        physical_lower_bound=18.0,
        shoulder_threshold=20.0,
    )
    result = _invoke_candidate(ctx)
    assert result.outcome == "no_trade"


# ---------------------------------------------------------------------------
# R7: Payoff identity: deterministic_profit == 1 - b_NO - fee (Decimal, §19)
# ---------------------------------------------------------------------------

def test_r7_payoff_identity_upper():
    """R7: deterministic_profit == 1 - b_NO - phi(b_NO) for upper shoulder."""
    b_no = Decimal("0.92")
    ctx = _make_upper_shoulder_context(
        native_no_ask=float(b_no),
        physical_upper_bound=90.0,
        shoulder_threshold=95.0,
    )
    result = _invoke_candidate(ctx)
    assert isinstance(result, DeterministicEdgeDecision)

    from src.strategy.fees import phi, venue_fee_rate
    fee_rate = venue_fee_rate()
    expected_fee = phi(Decimal("1"), b_no, fee_rate)
    expected_profit = Decimal("1") - b_no - expected_fee
    assert float(result.deterministic_profit) == pytest.approx(float(expected_profit), abs=1e-9)
    assert float(result.fee) == pytest.approx(float(expected_fee), abs=1e-9)


# ---------------------------------------------------------------------------
# R8: proof_inputs_hash is deterministic
# ---------------------------------------------------------------------------

def test_r8_proof_inputs_hash_deterministic():
    """R8: same inputs → same proof_inputs_hash (deterministic serialisation)."""
    ctx1 = _make_upper_shoulder_context(
        native_no_ask=0.92,
        physical_upper_bound=90.0,
        shoulder_threshold=95.0,
    )
    ctx2 = _make_upper_shoulder_context(
        native_no_ask=0.92,
        physical_upper_bound=90.0,
        shoulder_threshold=95.0,
    )
    r1 = _invoke_candidate(ctx1)
    r2 = _invoke_candidate(ctx2)
    assert isinstance(r1, DeterministicEdgeDecision)
    assert isinstance(r2, DeterministicEdgeDecision)
    assert r1.proof_inputs_hash == r2.proof_inputs_hash


def test_r8_different_inputs_different_hash():
    """R8: different physical_upper_bound → different hash."""
    ctx_a = _make_upper_shoulder_context(physical_upper_bound=90.0, shoulder_threshold=95.0)
    ctx_b = _make_upper_shoulder_context(physical_upper_bound=88.0, shoulder_threshold=95.0)
    r_a = _invoke_candidate(ctx_a)
    r_b = _invoke_candidate(ctx_b)
    assert isinstance(r_a, DeterministicEdgeDecision)
    assert isinstance(r_b, DeterministicEdgeDecision)
    assert r_a.proof_inputs_hash != r_b.proof_inputs_hash


# ---------------------------------------------------------------------------
# R9: strategy_key == "shoulder_impossible_tail_capture"
# ---------------------------------------------------------------------------

def test_r9_strategy_key():
    """R9: DeterministicEdgeDecision.strategy_key == 'shoulder_impossible_tail_capture'."""
    ctx = _make_upper_shoulder_context(
        physical_upper_bound=90.0,
        shoulder_threshold=95.0,
    )
    result = _invoke_candidate(ctx)
    assert isinstance(result, DeterministicEdgeDecision)
    assert result.strategy_key == "shoulder_impossible_tail_capture"


def test_r9_proof_type():
    """R9: proof_type == 'physical_impossible_tail' per zeus_strategy_spec §11.4."""
    ctx = _make_upper_shoulder_context(
        physical_upper_bound=90.0,
        shoulder_threshold=95.0,
    )
    result = _invoke_candidate(ctx)
    assert isinstance(result, DeterministicEdgeDecision)
    assert result.proof_type == "physical_impossible_tail"


# ---------------------------------------------------------------------------
# R1: Candidate is in deterministic family — evaluator output never CandidateDecision(enter)
# ---------------------------------------------------------------------------

def test_r1_no_candidate_decision_enter_on_deterministic_path():
    """R1: When theorem holds, result is DeterministicEdgeDecision, NOT CandidateDecision(enter)."""
    from src.strategy.candidates import CandidateDecision
    ctx = _make_upper_shoulder_context(
        physical_upper_bound=90.0,
        shoulder_threshold=95.0,
    )
    result = _invoke_candidate(ctx)
    # Must NOT be a CandidateDecision(enter) — that would misclassify as stochastic
    assert not (isinstance(result, CandidateDecision) and result.outcome == "enter"), (
        "shoulder_impossible_tail_capture must never emit CandidateDecision(enter); "
        "use DeterministicEdgeDecision for the physical bound path."
    )


# ---------------------------------------------------------------------------
# R6: shoulder_sell routing is retired — evaluator does not label new candidate edges "shoulder_sell"
# ---------------------------------------------------------------------------

def test_r6_shoulder_sell_routing_retired_in_registry():
    """R6: shoulder_sell profile in registry still exists (for history) but is REFUTED.

    New open-shoulder buy_no edges must route to shoulder_impossible_tail_capture,
    not shoulder_sell. Verify _classify_via_registry("shoulder_sell") returns None
    (blocked / topology restricted from the new routing).

    Note: shoulder_sell registry entry is preserved as tombstone with live_status=retired.
    """
    from src.strategy.strategy_profile import _classify_via_registry
    from src.types.market import Bin, BinEdge

    b = Bin(low=95.0, high=None, unit="F", label="95°F or higher")
    edge = BinEdge(
        bin=b, direction="buy_no", edge=0.05,
        ci_lower=0.80, ci_upper=0.95, p_model=0.08, p_market=0.08,
        p_posterior=0.08, entry_price=0.08, p_value=0.10, vwmp=0.08,
    )
    from types import SimpleNamespace
    ctx = SimpleNamespace(edge=edge, candidate=None, market_phase=None, conn=None)
    # shoulder_sell classify should return None (retired/blocked)
    result = _classify_via_registry("shoulder_sell", ctx)
    assert result is None, (
        "shoulder_sell _classify_via_registry must return None after retirement; "
        f"got {result!r}"
    )


def test_r6_shoulder_impossible_tail_registered():
    """R6: shoulder_impossible_tail_capture is registered in strategy_profile_registry."""
    from src.strategy.strategy_profile import try_get
    profile = try_get("shoulder_impossible_tail_capture")
    assert profile is not None, (
        "shoulder_impossible_tail_capture not found in strategy_profile_registry.yaml"
    )
