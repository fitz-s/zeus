# Created: 2026-06-04
# Last reused or audited: 2026-06-08 (S4: doc-sync — the cheap-NO market-disagreement
#   scalar gate it referenced is removed; "bin selection.md" §6/§13 + operator directive)
# Authority basis: Task #102 (BEST-ORDER SELECTION) CRITIC REVISE (aab33d99);
#   docs/operations/BEST_ORDER_SELECTION_ROOT_2026-06-01.md §4.1/§4.3;
#   operator GOAL 2026-06-04 (settlement-grounded edge lives ONLY in the
#   market-uncertain mid-range ~0.5-0.8; confident tails lose after cost).
"""Book-wide edge-zone admission predicate (the LAST selection seam).

Root being fixed (Task #102 ROOT A + B, both verified live):

  ROOT A  There is NO book-wide best-order selection. The reactor processes
          ``fetch_pending`` events one at a time, in *arrival order*, and the
          first event to clear every gate is the order that fires -- quality
          ignored. The only cross-candidate ``max()`` (event_reactor_adapter
          ``_selected_candidate_proof``) ranks WITHIN one family's <=2 tokens.

  ROOT B  ``robust_trade_score`` is a binary admission gate mis-used as a
          continuous ranker; it systematically buries high-confidence winners
          and prefers thin speculative bins (Spearman 0.66 vs realized PnL).

Why this module is the K<<N structural fix, not the full two-phase rewrite:

  A *pure* predicate over the candidate's OWN (q_lcb, cost) is **order-
  independent by construction**. It never compares two candidates, so it can
  NEVER admit a negative-after-cost-EV candidate ahead of a positive one -- it
  tests every candidate against an ABSOLUTE honest-EV bar. That makes the bug
  "a lower-EV order fires while a higher-EV one is available" structurally
  unconstructable at the admission boundary, which is exactly the antibody the
  task asks for. The reactor's per-event-commit / WAL-mutex machinery
  (split around the network submit, #95) is left completely untouched -- a
  collect-then-rank cycle would have to rewrite that machinery and is far
  riskier for a SHADOW system that must stay byte-identical when OFF.

Three deliberate, adversarial design choices:

  1. EV-per-dollar = (q_lcb - cost) / cost, NOT kelly_size * (q - cost).
     The design doc's headline (expected_PnL = kelly x edge) DOUBLE-COUNTS
     edge: kelly_size is itself ~proportional to edge, so ranking/gating by it
     ranks by edge^2 and over-concentrates in near-certain whales (the CRITIC
     REVISE point). EV-per-dollar (return on capital at risk) is the design's
     own section 4.3 key and naturally concentrates on the market-uncertain
     mid-range while demoting the confident tails.

  2. q_lcb (5th-pct conservative posterior), NOT point q. An overconfident
     point q cannot game this gate: the admission EV is computed on the lower
     confidence bound. This is the adversarial requirement -- the gate cannot
     be widened by a calibrator that simply reports a higher central estimate.

  3. It is a TIGHTENING. It can only REJECT (return ``admits=False``); it never
     admits anything the legacy gates already rejected. It trades fewer, only
     where honest after-cost edge is real. It is therefore safe to add to a
     system that is producing zero trades: a wrong-but-tight gate produces zero
     trades (the status quo), never a fabricated wrong-side trade (iron-rule-2).

Coordination with the cheap-NO loser demotion:

  1. THIS gate (edge_zone_admits) — the EV-PER-DOLLAR floor. Demotes the
     low-information-density tail: a confident-favorite at cost 0.92 has
     near-zero/negative after-cost EV-per-dollar on q_lcb. It is symmetric
     across direction and keys purely on the candidate's OWN after-cost economics.
     This is the ONE place the EV-per-dollar tail penalty lives — do NOT stack a
     second independent EV-per-dollar penalty that double-counts the SAME demotion.

  2. The cheap-NO-overconfidence loser (buy_no on a bin the MARKET prices as
     likely / cheap NO) is now killed STRUCTURALLY by the marginal-utility ranker,
     NOT a separate scalar gate. REMOVED 2026-06-08 (S4; "bin selection.md"
     §6/§9 Hidden #3/#10/§13): the standalone ``_market_disagreement_demotes_buy_no``
     antibody is gone. A NO candidate is scored with its OWN honest robust NO
     q_lcb = 1 - q_ucb_yes; when the market is confident YES (cheap NO) that honest
     q_lcb_no is low, so q_lcb_no < the cheap NO all-in cost -> negative robust
     edge -> ΔU <= 0 -> the §13 no-trade gate fires inside the ranker. The cheap-NO
     loser is UNCONSTRUCTABLE without a separate scalar gate.

These two remain orthogonal: THIS EV-per-dollar floor keys on a candidate's OWN
after-cost economics; the cheap-NO loser is now handled by the ΔU ranker's robust
edge. A redundant scalar gate is the regression disease the directive abolishes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EdgeZoneVerdict:
    """Result of the edge-zone admission test for one candidate.

    ``admits``        True iff the candidate's honest (q_lcb-based) after-cost
                      EV-per-dollar clears ``min_ev_per_dollar``.
    ``ev_per_dollar`` the conservative return-on-capital actually computed
                      ((q_lcb - cost) / cost), or None when inputs are missing.
    ``reason``        a stable machine code for the receipt / regret ledger when
                      ``admits`` is False (empty string when admitted).
    """

    admits: bool
    ev_per_dollar: float | None
    reason: str


# Default conservative floor: require the honest after-cost return on capital to
# be strictly positive (> 0). A confident-favorite tail (high cost) has its
# margin bounded by (1 - cost), so on q_lcb its EV-per-dollar collapses toward
# zero/negative -- it is demoted automatically. This default reproduces "admit
# only where honest edge exists" without any city/price special-casing.
DEFAULT_MIN_EV_PER_DOLLAR = 0.0


def edge_zone_admits(
    *,
    q_lcb: float | None,
    cost: float | None,
    min_ev_per_dollar: float = DEFAULT_MIN_EV_PER_DOLLAR,
) -> EdgeZoneVerdict:
    """Pure, order-independent admission predicate over ONE candidate.

    ``q_lcb``  the directional conservative (5th-pct) posterior win-probability
               for THIS candidate's direction (already direction-resolved
               upstream; buy_no carries its own independent grounding per the
               asymmetry law #106). NEVER the point estimate.
    ``cost``   the fee-adjusted executable cost basis for the order
               (``execution_price.value`` == receipt ``c_fee_adjusted``), i.e.
               the dollars at risk per share.

    Returns an ``EdgeZoneVerdict``. FAIL-CLOSED: if either input is missing or
    the cost is non-positive (no real price at risk), the candidate is NOT
    admitted -- a candidate with no honest cost basis cannot prove positive
    after-cost EV, so it must not reach the venue. This is intentionally
    conservative for a SHADOW-arming system.

    The verdict depends ONLY on this candidate's own (q_lcb, cost) -- it is a
    total function with no reference to any other candidate, the arrival order,
    the cycle, or wall-clock. Two candidates with identical (q_lcb, cost) get
    identical verdicts no matter when or in what order they are evaluated. This
    is the structural property the antibody test pins: a positive-EV candidate
    is admitted and a negative-EV candidate is rejected regardless of which one
    arrived first.
    """

    if q_lcb is None or cost is None:
        return EdgeZoneVerdict(admits=False, ev_per_dollar=None, reason="EDGE_ZONE_INPUTS_MISSING")
    cost_f = float(cost)
    if cost_f <= 0.0:
        # No real capital at risk -> cannot compute a return on capital ->
        # cannot prove honest after-cost edge. Fail closed.
        return EdgeZoneVerdict(admits=False, ev_per_dollar=None, reason="EDGE_ZONE_COST_NONPOSITIVE")
    ev_per_dollar = (float(q_lcb) - cost_f) / cost_f
    if ev_per_dollar > float(min_ev_per_dollar):
        return EdgeZoneVerdict(admits=True, ev_per_dollar=ev_per_dollar, reason="")
    return EdgeZoneVerdict(
        admits=False,
        ev_per_dollar=ev_per_dollar,
        reason="EDGE_ZONE_EV_PER_DOLLAR_BELOW_FLOOR",
    )
