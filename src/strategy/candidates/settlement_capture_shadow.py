# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §1
#                  + src/strategy/candidates/__init__.py §19 (DeterministicEdgeDecision)
#                  + src/strategy/fees.py (phi)
"""SettlementCaptureShadow — shadow candidate: physical-interval-state deterministic theorem.

STRATEGY_TAXONOMY_DIRECTIVE §1 theorem:
  Physical possible interval I_t = [L_t, U_t] (daily-high: L_t = H_t = high-so-far floor,
  U_t = H_t + Δ_phys⁺ physical envelope). Bin B_i = [bin_low, bin_high] (inclusive).

  Enter YES  iff I_t ⊆ B_i  → Π = 1 − a − phi(a), positive iff a + phi(a) < 1.
  Enter NO   iff I_t ∩ B_i = ∅ → Π = 1 − b − phi(b), positive iff b + phi(b) < 1.
  No-trade otherwise (overlap-not-subset).

Evidence = timestamp/source coherence (source_available_at + QC state), NOT win-rate.

SCOPE (C-1): settlement_capture key only (observation-LOCKED edge).
  day0_nowcast_entry (unlocked forecast-upside, stochastic pipeline) is OUT OF SCOPE.
  Gate: if the calling context indicates the edge is not observation-locked, emit
  SETTLEMENT_CAPTURE_NOT_LOCKED no_trade. Caller is responsible for routing; this
  candidate self-rejects non-locked edges as an antibody.

Δ_phys⁺ envelope: input via analysis.physical_interval_bound (PhysicalIntervalBound).
  When absent → PHYSICAL_INTERVAL_DATA_GATED no_trade. The envelope computation
  (solar radiation, remaining daylight, boundary-layer mixing, advection bound,
  historical station-season-hour transition envelope) is future work; this candidate
  implements the theorem + DeterministicEdgeDecision structure now, data-gated until
  physical_interval_bound is wired.

live_status: shadow (NEVER live; operator-gated promotion).
  Does NOT modify evaluator.py live routing — promotion-time only.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from src.contracts.no_trade_reason import NoTradeReason
from src.state.decision_events import write_shadow_decision_event
from src.strategy.fees import phi, venue_fee_rate

from . import (
    BaseStrategyCandidate,
    CandidateContext,
    CandidateDecision,
    CandidateMetadata,
    DeterministicEdgeDecision,
    write_candidate_no_trade_row,
)


# ---------------------------------------------------------------------------
# PhysicalIntervalBound — the Δ_phys⁺ / Δ_phys⁻ input type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PhysicalIntervalBound:
    """Physical possible interval I_t = [floor, ceiling] for the settlement value.

    For daily-high:  floor = H_t (high-so-far; monotonically non-decreasing floor).
                     ceiling = H_t + delta_phys_plus (meteorological upper envelope).
    For daily-low:   floor = L_t - delta_phys_minus (lower envelope).
                     ceiling = L_t (low-so-far; monotonically non-increasing ceiling).

    source_available_at: ISO-8601 UTC timestamp when the observation became available
        (carries provenance per STRATEGY_TAXONOMY_DIRECTIVE §1).
    qc_state: observation QC flag from NWS/official source. Accepted states gate entry.
    delta_phys: the physical envelope bound in the same temperature unit as the bin.
        For daily-high this is Δ_phys⁺; for daily-low this is Δ_phys⁻.
    observation_value: the high_so_far (or low_so_far) value used to anchor the interval.
    temperature_metric: 'high' or 'low' — determines which side is the locked floor/ceiling.
    """

    floor: float                 # L_t or lower-envelope for daily-low
    ceiling: float               # U_t or upper-envelope for daily-high
    source_available_at: str     # ISO-8601 UTC; required for proof_inputs_hash
    qc_state: str                # QC flag ('OK', 'SUSPECT', 'MISSING', …)
    delta_phys: float            # Δ_phys⁺ or Δ_phys⁻ applied
    observation_value: float     # H_t or L_t anchor
    temperature_metric: str      # 'high' or 'low'

    def __post_init__(self) -> None:
        if self.floor > self.ceiling:
            raise ValueError(
                f"PhysicalIntervalBound.floor={self.floor} > ceiling={self.ceiling}"
            )
        if self.temperature_metric not in {"high", "low"}:
            raise ValueError(
                f"PhysicalIntervalBound.temperature_metric must be 'high' or 'low', "
                f"got {self.temperature_metric!r}"
            )


# ---------------------------------------------------------------------------
# QC states that are acceptable for deterministic entry
# ---------------------------------------------------------------------------

_ACCEPTED_QC_STATES: frozenset[str] = frozenset({"OK", "PASSED", "VERIFIED"})


# ---------------------------------------------------------------------------
# Proof inputs hash
# ---------------------------------------------------------------------------

def _proof_inputs_hash(
    bound: PhysicalIntervalBound,
    bin_low: Optional[float],
    bin_high: Optional[float],
    token_id: str,
    side: str,
    executable_price: float,
) -> str:
    """Stable SHA-256 of proof inputs for DeterministicEdgeDecision.proof_inputs_hash.

    Includes all fields that uniquely identify this deterministic proof:
    the physical interval, bin boundaries, token identity, and execution price.
    """
    payload = json.dumps(
        {
            "floor": bound.floor,
            "ceiling": bound.ceiling,
            "source_available_at": bound.source_available_at,
            "qc_state": bound.qc_state,
            "delta_phys": bound.delta_phys,
            "observation_value": bound.observation_value,
            "temperature_metric": bound.temperature_metric,
            "bin_low": bin_low,
            "bin_high": bin_high,
            "token_id": token_id,
            "side": side,
            "executable_price": executable_price,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Interval-subset and disjoint helpers
# ---------------------------------------------------------------------------

def _interval_subset(floor: float, ceiling: float, bin_low: float, bin_high: float) -> bool:
    """Return True if [floor, ceiling] ⊆ [bin_low, bin_high] (inclusive both ends)."""
    return floor >= bin_low and ceiling <= bin_high


def _interval_disjoint(floor: float, ceiling: float, bin_low: float, bin_high: float) -> bool:
    """Return True if [floor, ceiling] ∩ [bin_low, bin_high] = ∅."""
    return ceiling < bin_low or floor > bin_high


# ---------------------------------------------------------------------------
# Candidate
# ---------------------------------------------------------------------------

class SettlementCaptureShadow(BaseStrategyCandidate):
    """Shadow candidate: physical-interval-state deterministic theorem.

    Implements STRATEGY_TAXONOMY_DIRECTIVE §1 as a DeterministicEdgeDecision
    shadow path. Does NOT modify evaluator.py live routing — promotion-time only.

    live_status: shadow (NEVER live in current phase).
    """

    def __init__(self) -> None:
        super().__init__(
            CandidateMetadata(
                strategy_key="settlement_capture",
                family="settlement_capture",
                description=(
                    "Shadow candidate: physical-interval-state deterministic theorem. "
                    "Enter YES iff I_t ⊆ B_i; enter NO iff I_t ∩ B_i = ∅. "
                    "Evidence = timestamp/source coherence, not win-rate. "
                    "Data-gated until Δ_phys⁺ envelope is wired."
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
    ) -> "CandidateDecision | DeterministicEdgeDecision":
        """Evaluate the physical-interval theorem against the market context.

        Enter path (YES): I_t ⊆ B_i AND (1 − ask − phi(ask, shares=1)) > 0
          → DeterministicEdgeDecision(side='buy_yes', proof_type='physical_interval_subset')
          Writes shadow decision_events row.

        Enter path (NO): I_t ∩ B_i = ∅ AND (1 − no_ask − phi(no_ask, shares=1)) > 0
          → DeterministicEdgeDecision(side='buy_no', proof_type='physical_interval_disjoint')
          Writes shadow decision_events row.

        No-trade paths:
          SETTLEMENT_CAPTURE_NOT_LOCKED — edge is not observation-locked (day0_nowcast scope).
          PHYSICAL_INTERVAL_DATA_GATED  — PhysicalIntervalBound absent or QC unacceptable.
          PHYSICAL_INTERVAL_OVERLAP     — I_t overlaps B_i but neither ⊆ nor disjoint.
          PHYSICAL_INTERVAL_UNPROFITABLE — theorem condition met but ask+phi ≥ 1.

        Never returns None.
        """
        analysis = context.analysis

        # ── C-1 antibody: self-reject non-locked edges ────────────────────────
        # If the caller is routing a day0_nowcast_entry edge here (unlocked), reject.
        # observation_lock_status is set by the caller (or absent → assume locked
        # when physical_interval_bound is present, since the bound itself carries
        # the locked observation value).
        observation_lock_status: Optional[str] = getattr(
            analysis, "observation_lock_status", None
        )
        if observation_lock_status is not None and observation_lock_status != "observation_locked":
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.SETTLEMENT_CAPTURE_NOT_LOCKED,
                reason_detail=(
                    f"settlement_capture_shadow: observation_lock_status="
                    f"{observation_lock_status!r}; scope is observation-locked only "
                    "(day0_nowcast_entry is the stochastic pipeline)."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # ── Data gate: PhysicalIntervalBound required ─────────────────────────
        bound: Optional[PhysicalIntervalBound] = getattr(
            analysis, "physical_interval_bound", None
        )
        if bound is None:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.PHYSICAL_INTERVAL_DATA_GATED,
                reason_detail=(
                    "settlement_capture_shadow: analysis.physical_interval_bound absent. "
                    "Δ_phys⁺ envelope not yet wired; emitting data-gated no_trade. "
                    "Wire PhysicalIntervalBound to analysis before promotion."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # ── QC gate ───────────────────────────────────────────────────────────
        if bound.qc_state not in _ACCEPTED_QC_STATES:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.PHYSICAL_INTERVAL_DATA_GATED,
                reason_detail=(
                    f"settlement_capture_shadow: observation QC state={bound.qc_state!r} "
                    f"not in accepted set {sorted(_ACCEPTED_QC_STATES)}; "
                    "data-gated until QC passes."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        # ── Bin geometry ──────────────────────────────────────────────────────
        # Pull bin boundaries from analysis. Caller provides via
        # analysis.bin_low / analysis.bin_high and token IDs.
        bin_low: Optional[float] = getattr(analysis, "bin_low", None)
        bin_high: Optional[float] = getattr(analysis, "bin_high", None)
        yes_ask: Optional[float] = getattr(analysis, "yes_ask", None)
        no_ask: Optional[float] = getattr(analysis, "no_ask", None)
        token_id: str = str(getattr(analysis, "token_id", "") or "")
        no_token_id: str = str(getattr(analysis, "no_token_id", "") or "")

        # Shoulder bins (open-ended) are not eligible for the interval theorem —
        # the theorem requires finite [bin_low, bin_high] boundaries.
        if bin_low is None or bin_high is None:
            decision = CandidateDecision(
                outcome="no_trade",
                reason=NoTradeReason.PHYSICAL_INTERVAL_DATA_GATED,
                reason_detail=(
                    "settlement_capture_shadow: bin_low or bin_high absent or open-ended "
                    "(shoulder bin); interval theorem requires finite bin boundaries."
                ),
            )
            write_candidate_no_trade_row(conn, context, decision)
            return decision

        floor = bound.floor
        ceiling = bound.ceiling
        fee_rate = venue_fee_rate()
        shares_one = Decimal("1")

        # ── YES path: I_t ⊆ B_i ──────────────────────────────────────────────
        if _interval_subset(floor, ceiling, bin_low, bin_high):
            if yes_ask is None or not token_id:
                decision = CandidateDecision(
                    outcome="no_trade",
                    reason=NoTradeReason.PHYSICAL_INTERVAL_DATA_GATED,
                    reason_detail=(
                        "settlement_capture_shadow: I_t ⊆ B_i (YES condition met) but "
                        "yes_ask or token_id absent from analysis; data-gated."
                    ),
                )
                write_candidate_no_trade_row(conn, context, decision)
                return decision

            ask_dec = Decimal(str(yes_ask))
            fee_dec = phi(shares_one, ask_dec, fee_rate)
            profit = Decimal("1") - ask_dec - fee_dec

            if profit <= Decimal("0"):
                decision = CandidateDecision(
                    outcome="no_trade",
                    reason=NoTradeReason.PHYSICAL_INTERVAL_UNPROFITABLE,
                    reason_detail=(
                        f"settlement_capture_shadow: I_t⊆B_i but "
                        f"1 − ask({yes_ask}) − phi({float(fee_dec):.6f}) = "
                        f"{float(profit):.6f} ≤ 0; no positive profit."
                    ),
                )
                write_candidate_no_trade_row(conn, context, decision)
                return decision

            decision_time_iso = _to_utc_iso(decision_time)
            write_shadow_decision_event(
                context.natural_key,
                decision_time=decision_time_iso,
                side="buy_yes",
                strategy_key=self.strategy_key,
                conn=conn,
                edge=float(profit),
            )

            return DeterministicEdgeDecision(
                strategy_key=self.strategy_key,
                proof_type="physical_interval_subset",
                side="buy_yes",
                token_id=token_id,
                executable_price=ask_dec,
                fee=fee_dec,
                deterministic_payoff=Decimal("1"),
                deterministic_profit=profit,
                proof_inputs_hash=_proof_inputs_hash(
                    bound, bin_low, bin_high, token_id, "buy_yes", yes_ask
                ),
            )

        # ── NO path: I_t ∩ B_i = ∅ ───────────────────────────────────────────
        if _interval_disjoint(floor, ceiling, bin_low, bin_high):
            if no_ask is None or not no_token_id:
                decision = CandidateDecision(
                    outcome="no_trade",
                    reason=NoTradeReason.PHYSICAL_INTERVAL_DATA_GATED,
                    reason_detail=(
                        "settlement_capture_shadow: I_t ∩ B_i = ∅ (NO condition met) but "
                        "no_ask or no_token_id absent from analysis; data-gated."
                    ),
                )
                write_candidate_no_trade_row(conn, context, decision)
                return decision

            no_ask_dec = Decimal(str(no_ask))
            fee_dec = phi(shares_one, no_ask_dec, fee_rate)
            profit = Decimal("1") - no_ask_dec - fee_dec

            if profit <= Decimal("0"):
                decision = CandidateDecision(
                    outcome="no_trade",
                    reason=NoTradeReason.PHYSICAL_INTERVAL_UNPROFITABLE,
                    reason_detail=(
                        f"settlement_capture_shadow: I_t∩B_i=∅ but "
                        f"1 − no_ask({no_ask}) − phi({float(fee_dec):.6f}) = "
                        f"{float(profit):.6f} ≤ 0; no positive profit."
                    ),
                )
                write_candidate_no_trade_row(conn, context, decision)
                return decision

            decision_time_iso = _to_utc_iso(decision_time)
            write_shadow_decision_event(
                context.natural_key,
                decision_time=decision_time_iso,
                side="buy_no",
                strategy_key=self.strategy_key,
                conn=conn,
                edge=float(profit),
            )

            return DeterministicEdgeDecision(
                strategy_key=self.strategy_key,
                proof_type="physical_interval_disjoint",
                side="buy_no",
                token_id=no_token_id,
                executable_price=no_ask_dec,
                fee=fee_dec,
                deterministic_payoff=Decimal("1"),
                deterministic_profit=profit,
                proof_inputs_hash=_proof_inputs_hash(
                    bound, bin_low, bin_high, no_token_id, "buy_no", no_ask
                ),
            )

        # ── Overlap: neither ⊆ nor disjoint ──────────────────────────────────
        decision = CandidateDecision(
            outcome="no_trade",
            reason=NoTradeReason.PHYSICAL_INTERVAL_OVERLAP,
            reason_detail=(
                f"settlement_capture_shadow: I_t=[{floor},{ceiling}] overlaps "
                f"B_i=[{bin_low},{bin_high}] but neither ⊆ nor disjoint; "
                "theorem does not determine outcome."
            ),
        )
        write_candidate_no_trade_row(conn, context, decision)
        return decision


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).isoformat()
    return dt.astimezone(timezone.utc).isoformat()
