# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: post-peak harvester build 2026-06-14 (see
#   docs/evidence/investigation_2026-06-13/post_peak_harvester_build.md). This is
#   the EDGE-PROOF harness: as the surfaced BUY-NO positions settle, grade the
#   realized NO win-rate against the locked settled max. A genuine repricing-latency
#   edge shows a realized NO win-rate at/above the obs-conditioned P(NO).
"""Settlement back-test harness (stub) for the post-peak harvester.

This harness does NOT trade. It takes RECORDED ``HarvestOpportunity`` rows (the
scanner's ranked output) plus a settlement-value lookup, and grades each:

  - NO on a bin WINS iff the day's settled rounded max does NOT fall in that bin.
  - NO LOSES iff settlement lands in the bin.

It reports the realized NO win-rate vs the obs-conditioned P(NO) the scanner
predicted, and the realized P&L in cents per share (win → +(1 - ask - fee);
loss → -(ask + fee)). The win-rate gap is the edge proof: if realized win-rate
>= predicted obs P(NO) and P&L > 0 across settled positions, the latency edge is real.

Settlement truth is injected as a callable so this harness is DB-agnostic (the
live wiring passes a reader over the harvester settlement table; tests pass a dict).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from src.strategy.post_peak_harvester import HarvestOpportunity

logger = logging.getLogger(__name__)

#: (city, target_date) -> settled rounded max value (int), or None if unsettled.
SettlementLookup = Callable[[str, str], Optional[int]]


def _bin_contains(low: Optional[float], high: Optional[float], value: float) -> bool:
    """Inclusive integer-settlement membership (mirrors src.types.market.Bin.contains)."""
    lo = float("-inf") if low is None else float(low)
    hi = float("inf") if high is None else float(high)
    return lo <= float(value) <= hi


@dataclass(frozen=True)
class GradedPosition:
    city: str
    target_date: str
    bin_label: str
    no_ask: float
    fee_rate: float
    predicted_p_no: float          # obs-conditioned P(NO wins) the scanner used
    settled_max: Optional[int]
    settled: bool
    no_won: Optional[bool]
    pnl_cents_per_share: Optional[float]
    size_usd: float


@dataclass(frozen=True)
class BacktestReport:
    n_total: int
    n_settled: int
    n_no_won: int
    realized_no_win_rate: Optional[float]
    mean_predicted_p_no: Optional[float]
    edge_gap: Optional[float]          # realized - predicted (positive = edge confirmed)
    total_pnl_cents_per_share: float
    weighted_pnl_usd: float            # sum over settled of pnl_per_share_dollars * shares
    positions: tuple[GradedPosition, ...]


def grade_opportunity(
    opp: HarvestOpportunity,
    settlement_lookup: SettlementLookup,
) -> GradedPosition:
    """Grade one recorded opportunity against settlement truth."""
    settled_max = settlement_lookup(opp.city, opp.target_date)
    if settled_max is None:
        return GradedPosition(
            city=opp.city,
            target_date=opp.target_date,
            bin_label=opp.bin_label,
            no_ask=opp.no_ask,
            fee_rate=opp.fee_rate,
            predicted_p_no=opp.p_obs_no,
            settled_max=None,
            settled=False,
            no_won=None,
            pnl_cents_per_share=None,
            size_usd=opp.size_usd,
        )

    # NO on a bin WINS iff the settled rounded max does NOT fall in that bin.
    # Bin bounds are carried on every record this build produces, so membership
    # is exact (no threshold heuristic).
    in_bin = _bin_contains(opp.bin_low, opp.bin_high, settled_max)
    no_won = not in_bin

    fee_at_ask = float(opp.fee_rate) * opp.no_ask * (1.0 - opp.no_ask)
    if no_won:
        pnl_per_share = (1.0 - opp.no_ask - fee_at_ask) * 100.0
    else:
        pnl_per_share = -(opp.no_ask + fee_at_ask) * 100.0

    return GradedPosition(
        city=opp.city,
        target_date=opp.target_date,
        bin_label=opp.bin_label,
        no_ask=opp.no_ask,
        fee_rate=opp.fee_rate,
        predicted_p_no=opp.p_obs_no,
        settled_max=int(settled_max),
        settled=True,
        no_won=bool(no_won),
        pnl_cents_per_share=round(pnl_per_share, 3),
        size_usd=opp.size_usd,
    )


def run_backtest(
    opportunities: Iterable[HarvestOpportunity],
    settlement_lookup: SettlementLookup,
) -> BacktestReport:
    """Grade a batch of recorded opportunities and summarize the edge proof."""
    graded = [grade_opportunity(o, settlement_lookup) for o in opportunities]
    settled = [g for g in graded if g.settled]
    n_no_won = sum(1 for g in settled if g.no_won)
    realized = (n_no_won / len(settled)) if settled else None
    mean_pred = (
        sum(g.predicted_p_no for g in settled) / len(settled) if settled else None
    )
    edge_gap = (realized - mean_pred) if (realized is not None and mean_pred is not None) else None

    total_pnl = sum(g.pnl_cents_per_share or 0.0 for g in settled)
    weighted_usd = 0.0
    for g in settled:
        if g.pnl_cents_per_share is None or g.no_ask <= 0:
            continue
        shares = g.size_usd / g.no_ask
        weighted_usd += (g.pnl_cents_per_share / 100.0) * shares

    return BacktestReport(
        n_total=len(graded),
        n_settled=len(settled),
        n_no_won=n_no_won,
        realized_no_win_rate=realized,
        mean_predicted_p_no=mean_pred,
        edge_gap=edge_gap,
        total_pnl_cents_per_share=round(total_pnl, 3),
        weighted_pnl_usd=round(weighted_usd, 2),
        positions=tuple(graded),
    )
