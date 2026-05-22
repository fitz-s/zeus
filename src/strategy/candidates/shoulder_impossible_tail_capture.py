# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §7
#                  + SHOULDER_SELL_EDGE_PROOF.md (refutation verdict)
#                  + docs/reference/zeus_strategy_spec.md §11.4 (proof_type=physical_impossible_tail)
#                  + src/strategy/candidates/__init__.py §19 DeterministicEdgeDecision

"""ShoulderImpossibleTailCapture — physical-bound deterministic shoulder candidate.

THEOREM (STRATEGY_TAXONOMY_DIRECTIVE.md §7):
  Upper shoulder B=[u,∞): buy NO iff physical upper bound U_t = H_t + Δ_phys⁺(t) < u
  (YES physically impossible) → Π = 1 − b_NO − phi pathwise.
  Lower shoulder symmetric: buy NO iff L_t = physical_lower_bound > shoulder_threshold.

DATA-GATED: Δ_phys⁺/Δ_phys⁻ physical envelope input is unwired.
  → Emits PHYSICAL_ENVELOPE_UNWIRED no_trade until envelope feed lands.

SHADOW-FIRST per operator directive 2026-05-22:
  Logs DeterministicEdgeDecision for observability.
  Does NOT change live routing/sizing — promotion is operator-gated.

The ex-ante retail-bias shoulder_sell is REFUTED (sign-reversed per SHOULDER_SELL_EDGE_PROOF.md).
This candidate supersedes it for open-shoulder buy_no routing.

Proof class: physics-bounded deterministic (§16.A) — deterministic promotion pipeline,
NOT Beta win-rate CI.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Union

from src.contracts.no_trade_reason import NoTradeReason

from . import (
    BaseStrategyCandidate,
    CandidateContext,
    CandidateDecision,
    CandidateMetadata,
    DeterministicEdgeDecision,
)

_STRATEGY_KEY = "shoulder_impossible_tail_capture"
_PROOF_TYPE = "physical_impossible_tail"

# Shares = 1 for the single-leg deterministic payoff (buy 1 NO share).
_ONE_SHARE = Decimal("1")
_ONE = Decimal("1")


def _compute_proof_inputs_hash(
    shoulder_side: str,
    physical_bound: Decimal,
    shoulder_threshold: Decimal,
    native_no_ask: Decimal,
) -> str:
    """SHA-256 hex of (shoulder_side, physical_bound, threshold, no_ask) — deterministic.

    Serialisation: pipe-separated decimal canonical strings, no floats.
    """
    payload = "|".join([
        shoulder_side,
        format(physical_bound, "f"),
        format(shoulder_threshold, "f"),
        format(native_no_ask, "f"),
    ])
    return hashlib.sha256(payload.encode()).hexdigest()


class ShoulderImpossibleTailCapture(BaseStrategyCandidate):
    """Physical-bound deterministic shadow candidate for open-shoulder buy_no.

    Emits DeterministicEdgeDecision when the physical envelope proves the tail
    is impossible. Emits no_trade(PHYSICAL_ENVELOPE_UNWIRED) when envelope data
    is not yet available (data-gated).

    Reads the following fields from context.analysis (injected by the caller):
      - native_no_ask: Decimal — NO ask price for the shoulder bin
      - physical_upper_bound: Optional[Decimal] — H_t + Δ_phys⁺(t); None = unwired
      - physical_lower_bound: Optional[Decimal] — L_t − Δ_phys⁻(t); None = unwired
      - shoulder_threshold: Decimal — the bin's open shoulder threshold (u)
      - shoulder_side: str — "upper" or "lower"
      - edge: BinEdge — carries bin topology for internal cross-check

    If analysis does not carry these fields (e.g. legacy context objects), the
    candidate fails-closed to PHYSICAL_ENVELOPE_UNWIRED.

    live_status: shadow. SHADOW-FIRST: does NOT execute live trades.
    """

    def __init__(self) -> None:
        super().__init__(
            CandidateMetadata(
                strategy_key=_STRATEGY_KEY,
                family="shoulder_impossible_tail_capture",
                description=(
                    "Shadow candidate: physical-bound deterministic open-shoulder buy_no. "
                    "Theorem: if H_t + Δ_phys⁺(t) < threshold (upper) or "
                    "L_t > threshold (lower), tail YES is physically impossible → "
                    "NO token payoff is deterministic. DATA-GATED until envelope wired."
                ),
                executable_alpha=False,
            )
        )

    def evaluate(
        self,
        *,
        context: CandidateContext,
        conn: sqlite3.Connection,
        decision_time: datetime,
    ) -> Union[CandidateDecision, DeterministicEdgeDecision]:
        """Evaluate physical-bound impossibility theorem for open shoulder.

        Returns:
          DeterministicEdgeDecision if the physical bound proves tail impossible
            and the no_ask price yields positive deterministic profit.
          CandidateDecision(no_trade, PHYSICAL_ENVELOPE_UNWIRED) when envelope
            data is not wired.
          CandidateDecision(no_trade, SHOULDER_PHYSICAL_BOUND_NOT_EXCLUDES_TAIL)
            when the bound is wired but the theorem fails (bound overlaps tail).
        """
        analysis = context.analysis

        # ── Read physical envelope inputs (may be absent on legacy contexts) ──
        native_no_ask: Decimal | None = getattr(analysis, "native_no_ask", None)
        physical_upper_bound: Decimal | None = getattr(analysis, "physical_upper_bound", None)
        physical_lower_bound: Decimal | None = getattr(analysis, "physical_lower_bound", None)
        shoulder_threshold: Decimal | None = getattr(analysis, "shoulder_threshold", None)
        shoulder_side: str = str(getattr(analysis, "shoulder_side", "upper"))

        # ── Data-gate: envelope inputs not wired ──────────────────────────────
        # Both physical bounds absent → DATA-GATED (not a topology failure).
        upper_present = physical_upper_bound is not None
        lower_present = physical_lower_bound is not None

        if shoulder_side == "upper":
            bound_present = upper_present
            physical_bound = physical_upper_bound
        else:
            # lower shoulder
            bound_present = lower_present
            physical_bound = physical_lower_bound

        if not bound_present or shoulder_threshold is None or native_no_ask is None:
            return CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.PHYSICAL_ENVELOPE_UNWIRED,
                reason_detail=(
                    f"shoulder_side={shoulder_side}; physical envelope Δ_phys not wired; "
                    "data-gated until empirical envelope feed lands"
                ),
            )

        # ── Theorem evaluation ─────────────────────────────────────────────────
        # Upper shoulder B=[u,∞): theorem holds iff physical_upper_bound < u
        # Lower shoulder B=(-∞,u]: theorem holds iff physical_lower_bound > u
        if shoulder_side == "upper":
            theorem_holds = physical_bound < shoulder_threshold  # type: ignore[operator]
        else:
            theorem_holds = physical_bound > shoulder_threshold  # type: ignore[operator]

        if not theorem_holds:
            return CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.SHOULDER_PHYSICAL_BOUND_NOT_EXCLUDES_TAIL,
                reason_detail=(
                    f"shoulder_side={shoulder_side}; "
                    f"physical_bound={physical_bound}; "
                    f"threshold={shoulder_threshold}; "
                    "physical bound does not exclude tail — theorem fails"
                ),
            )

        # ── Deterministic payoff: Π = 1 − b_NO − phi(b_NO) ───────────────────
        from src.strategy.fees import venue_fee_rate, phi

        fee_rate = venue_fee_rate()
        fee = phi(shares=_ONE_SHARE, price=native_no_ask, fee_rate=fee_rate)
        deterministic_payoff = _ONE  # NO pays $1 if tail does not hit (certain here)
        deterministic_profit = _ONE - native_no_ask - fee

        if deterministic_profit <= Decimal("0"):
            # Price + fee >= 1: no economic profit even with deterministic theorem.
            return CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.STRATEGY_ECONOMIC_FLOOR,
                reason_detail=(
                    f"deterministic_profit={deterministic_profit} <= 0; "
                    f"native_no_ask={native_no_ask}, fee={fee}"
                ),
            )

        # ── Proof inputs hash ─────────────────────────────────────────────────
        proof_hash = _compute_proof_inputs_hash(
            shoulder_side=shoulder_side,
            physical_bound=physical_bound,  # type: ignore[arg-type]
            shoulder_threshold=shoulder_threshold,
            native_no_ask=native_no_ask,
        )

        # ── Token identity: condition_id from edge.bin if available ───────────
        edge = getattr(analysis, "edge", None)
        token_id: str = ""
        if edge is not None:
            token_id = str(getattr(getattr(edge, "bin", None), "condition_id", "") or "")

        # ── Emit DeterministicEdgeDecision (shadow: no live execution) ────────
        return DeterministicEdgeDecision(
            strategy_key=_STRATEGY_KEY,
            proof_type=_PROOF_TYPE,
            side="buy_no",
            token_id=token_id,
            executable_price=native_no_ask,
            fee=fee,
            deterministic_payoff=deterministic_payoff,
            deterministic_profit=deterministic_profit,
            proof_inputs_hash=proof_hash,
        )
