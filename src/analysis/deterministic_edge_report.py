# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/reference/zeus_strategy_spec.md §16 + §20.2
#                  + docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §16
"""DeterministicEdgeVerifier — Pipeline A promotion evidence for deterministic strategies.

§16 (STRATEGY_TAXONOMY_DIRECTIVE): deterministic strategies are promoted via payoff-identity
evidence, NOT win-rate CI. Two clauses must both hold:

  Clause 1 (reconciliation):
      computed_profit ≈ realized_profit within tolerance  (per settled record)

  Clause 2 (aggregate profitability):
      Σ realized_payoff − Σ realized_cost − Σ realized_fee > 0  (across population)

This module provides:
  - RealizedOutcome: caller-supplied settled data paired with a decision record.
  - RealizedVectorOutcome: settled data for a VectorEdgeDecision basket.
  - DeterministicEdgeReport: composite verdict object (mirrors PromotionReadinessReport).
  - DeterministicEdgeVerifier: verifies the two clauses and emits a read-only advisory.

HARD CONSTRAINTS (mirrors PromotionReadinessValidator):
  - NEVER writes a tier; never calls adjudicate() with a live conn.
  - A READY recommendation targeting a live tier (>= LIVE_PILOT_TINY) MUST have a
    non-empty operator_ref; otherwise ValueError is raised.
  - No imports from src.engine, src.runtime, or any live-trading surface.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence, Union

from src.analysis.promotion_readiness import ReadinessVerdict, SignalResult
from src.contracts.evidence_tier import EvidenceTier
from src.strategy.candidates import DeterministicEdgeDecision, VectorEdgeDecision


# ---------------------------------------------------------------------------
# Realized outcome types — separate from decision types (advisor guidance)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RealizedOutcome:
    """Settled result for a single-leg DeterministicEdgeDecision.

    Fields mirror the cost model: payoff, cost (= fill price × quantity), fee.
    These come from the settled chain: what actually happened after execution.

    Attributes
    ----------
    realized_payoff:
        Gross payoff received upon settlement (e.g., $1.00 per share for YES win).
    realized_cost:
        Actual fill cost paid (executable_price × quantity, or sum of sweep levels).
    realized_fee:
        Actual taker fee paid (phi at fill price and quantity).
    """
    realized_payoff: Decimal
    realized_cost: Decimal
    realized_fee: Decimal

    @property
    def realized_profit(self) -> Decimal:
        """realized_payoff − realized_cost − realized_fee."""
        return self.realized_payoff - self.realized_cost - self.realized_fee


@dataclass(frozen=True)
class RealizedVectorOutcome:
    """Settled result for a multi-leg VectorEdgeDecision basket.

    Aggregates across all legs — callers sum per-leg actuals before constructing.

    Attributes
    ----------
    realized_payoff:
        Total basket payoff received upon settlement.
    realized_cost:
        Total fill cost across all legs (Σ sweep notional at actual fill prices).
    realized_fee:
        Total fee paid across all legs (Σ phi per leg).
    """
    realized_payoff: Decimal
    realized_cost: Decimal
    realized_fee: Decimal

    @property
    def realized_profit(self) -> Decimal:
        """realized_payoff − realized_cost − realized_fee."""
        return self.realized_payoff - self.realized_cost - self.realized_fee


AnyRealizedOutcome = Union[RealizedOutcome, RealizedVectorOutcome]
AnyDecision = Union[DeterministicEdgeDecision, VectorEdgeDecision]


# ---------------------------------------------------------------------------
# Report object
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeterministicEdgeReport:
    """Operator-reviewable verdict for a single deterministic strategy.

    Mirrors PromotionReadinessReport so callers can handle both uniformly.

    Fields
    ------
    strategy_id:
        Strategy key assessed.
    verdict:
        READY (both clauses pass) or NOT_READY (any clause fails).
    tier_current:
        EvidenceTier at time of assessment.
    tier_target:
        Recommended next tier on READY; tier_current on NOT_READY.
    n_records:
        Number of settled (decision, realized) pairs evaluated.
    clause1_mismatch_count:
        Number of records where |computed_profit − realized_profit| > tolerance.
    clause2_aggregate_profit:
        Σ realized_payoff − Σ realized_cost − Σ realized_fee across all records.
    signals:
        Per-clause breakdown.
    operator_ref_required:
        True when tier_target >= LIVE_PILOT_TINY.
    summary:
        Human-readable one-line summary.
    """
    strategy_id: str
    verdict: ReadinessVerdict
    tier_current: EvidenceTier
    tier_target: EvidenceTier
    n_records: int
    clause1_mismatch_count: int
    clause2_aggregate_profit: Decimal
    signals: tuple[SignalResult, ...]
    operator_ref_required: bool
    summary: str


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------

class DeterministicEdgeVerifier:
    """Verify deterministic-promotion evidence (Pipeline A).

    Parameters
    ----------
    tier_required_for_live:
        Minimum EvidenceTier at which the strategy is live-eligible.
        REQUIRED — must be supplied from StrategyProfile (no default).
    reconciliation_tolerance:
        Maximum allowable |computed_profit − realized_profit| for clause 1
        to pass for a single record. Default Decimal('0.0001') (0.01 cent).
    min_records:
        Minimum settled records required before a READY verdict can be emitted.
        Default 1 — callers should supply a meaningful threshold.
    """

    def __init__(
        self,
        *,
        tier_required_for_live: EvidenceTier,
        reconciliation_tolerance: Decimal = Decimal("0.0001"),
        min_records: int = 1,
    ) -> None:
        self._tier_required_for_live = tier_required_for_live
        self._tolerance = reconciliation_tolerance
        self._min_records = min_records

    def verify(
        self,
        strategy_id: str,
        tier_current: EvidenceTier,
        records: Sequence[tuple[AnyDecision, AnyRealizedOutcome]],
        *,
        operator_ref: Optional[str] = None,
    ) -> DeterministicEdgeReport:
        """Verify deterministic promotion evidence.

        Parameters
        ----------
        strategy_id:
            Strategy key being assessed.
        tier_current:
            Current EvidenceTier from the strategy registry.
        records:
            Sequence of (decision, realized) pairs for settled trades.
            DeterministicEdgeDecision → RealizedOutcome.
            VectorEdgeDecision        → RealizedVectorOutcome.
        operator_ref:
            Must be a non-empty string when tier_target would be >= LIVE_PILOT_TINY.
            Raises ValueError if missing/whitespace in that case.

        Returns
        -------
        DeterministicEdgeReport
            Recommendation only. No tier written; no DB row inserted.

        Raises
        ------
        ValueError
            If tier_target would be >= LIVE_PILOT_TINY and operator_ref is missing.
        TypeError
            If a decision/realized pair has incompatible types.
        """
        n_records = len(records)

        # ── Clause 1: reconciliation ─────────────────────────────────────────
        clause1_passes, clause1_rationale, clause1_mismatch_count = (
            self._eval_clause1(records)
        )

        # ── Clause 2: aggregate profitability ────────────────────────────────
        clause2_passes, clause2_rationale, aggregate_profit = (
            self._eval_clause2(records, n_records)
        )

        signals = (
            SignalResult("reconciliation", clause1_passes, clause1_rationale),
            SignalResult("aggregate_profit", clause2_passes, clause2_rationale),
        )

        all_pass = clause1_passes and clause2_passes

        # Determine tier_target
        if all_pass and tier_current < self._tier_required_for_live:
            tier_target = EvidenceTier(min(7, tier_current.value + 1))
        else:
            tier_target = tier_current

        verdict = ReadinessVerdict.READY if all_pass else ReadinessVerdict.NOT_READY

        # Operator-ref guard: same pattern as PromotionReadinessValidator
        crossing_into_live = (
            tier_target >= EvidenceTier.LIVE_PILOT_TINY
            and tier_target > tier_current
        )
        if crossing_into_live and not (operator_ref or "").strip():
            raise ValueError(
                f"Operator-gate violation: DeterministicEdgeVerifier recommends "
                f"tier_target={tier_target.name} (>= LIVE_PILOT_TINY) but "
                f"operator_ref is missing or blank. Supply operator_ref= to confirm "
                f"operator approval."
            )

        operator_ref_required = crossing_into_live
        failing = [s.signal_name for s in signals if not s.passed]
        if verdict == ReadinessVerdict.READY:
            summary = (
                f"READY: both clauses pass; recommend {tier_current.name} → "
                f"{tier_target.name}"
                + (f" (operator_ref={operator_ref!r})" if operator_ref_required else "")
            )
        else:
            summary = (
                f"NOT_READY: failing clauses: {', '.join(failing)}; "
                f"n_records={n_records}, "
                f"clause1_mismatches={clause1_mismatch_count}, "
                f"aggregate_profit={aggregate_profit}"
            )

        return DeterministicEdgeReport(
            strategy_id=strategy_id,
            verdict=verdict,
            tier_current=tier_current,
            tier_target=tier_target,
            n_records=n_records,
            clause1_mismatch_count=clause1_mismatch_count,
            clause2_aggregate_profit=aggregate_profit,
            signals=signals,
            operator_ref_required=operator_ref_required,
            summary=summary,
        )

    # ── Private clause evaluators ─────────────────────────────────────────

    def _computed_profit(self, decision: AnyDecision) -> Decimal:
        """Extract the computed profit from either decision type."""
        if isinstance(decision, DeterministicEdgeDecision):
            return decision.deterministic_profit
        if isinstance(decision, VectorEdgeDecision):
            return decision.vector_profit
        raise TypeError(
            f"Unsupported decision type: {type(decision).__name__}. "
            "Expected DeterministicEdgeDecision or VectorEdgeDecision."
        )

    def _check_pair_types(
        self, decision: AnyDecision, realized: AnyRealizedOutcome
    ) -> None:
        """Raise TypeError if decision/realized pair types are mismatched."""
        if isinstance(decision, DeterministicEdgeDecision) and not isinstance(
            realized, RealizedOutcome
        ):
            raise TypeError(
                f"DeterministicEdgeDecision must be paired with RealizedOutcome; "
                f"got {type(realized).__name__}"
            )
        if isinstance(decision, VectorEdgeDecision) and not isinstance(
            realized, RealizedVectorOutcome
        ):
            raise TypeError(
                f"VectorEdgeDecision must be paired with RealizedVectorOutcome; "
                f"got {type(realized).__name__}"
            )

    def _eval_clause1(
        self,
        records: Sequence[tuple[AnyDecision, AnyRealizedOutcome]],
    ) -> tuple[bool, str, int]:
        """Clause 1: computed_profit ≈ realized_profit within tolerance."""
        if len(records) < self._min_records:
            rationale = (
                f"FAIL: n_records={len(records)} < min_records={self._min_records}; "
                "insufficient settled evidence for reconciliation"
            )
            return False, rationale, 0

        mismatch_count = 0
        for decision, realized in records:
            self._check_pair_types(decision, realized)
            computed = self._computed_profit(decision)
            diff = abs(computed - realized.realized_profit)
            if diff > self._tolerance:
                mismatch_count += 1

        if mismatch_count == 0:
            return (
                True,
                f"PASS: all {len(records)} records reconcile within tolerance "
                f"{self._tolerance}",
                0,
            )
        return (
            False,
            f"FAIL: {mismatch_count}/{len(records)} records exceed reconciliation "
            f"tolerance {self._tolerance} (computed_profit ≠ realized_profit)",
            mismatch_count,
        )

    def _eval_clause2(
        self,
        records: Sequence[tuple[AnyDecision, AnyRealizedOutcome]],
        n_records: int,
    ) -> tuple[bool, str, Decimal]:
        """Clause 2: Σ realized_payoff − Σ realized_cost − Σ realized_fee > 0."""
        if n_records < self._min_records:
            return (
                False,
                f"FAIL: n_records={n_records} < min_records={self._min_records}",
                Decimal(0),
            )

        total_payoff = Decimal(0)
        total_cost = Decimal(0)
        total_fee = Decimal(0)
        for _decision, realized in records:
            total_payoff += realized.realized_payoff
            total_cost += realized.realized_cost
            total_fee += realized.realized_fee

        aggregate_profit = total_payoff - total_cost - total_fee

        if aggregate_profit > Decimal(0):
            return (
                True,
                f"PASS: Σ(payoff − cost − fee) = {aggregate_profit} > 0 "
                f"across {n_records} records",
                aggregate_profit,
            )
        return (
            False,
            f"FAIL: Σ(payoff − cost − fee) = {aggregate_profit} ≤ 0 "
            f"across {n_records} records; aggregate not profitable",
            aggregate_profit,
        )
