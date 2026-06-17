# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: FIX C for incident 0b5c305e26524042 (Milan 24C first fill;
#   docs/evidence/2026_06_10_milan_24c_first_fill_rootcause.md §3) + operator
#   directive 2026-06-10: mode-consistent evaluation. The system is structurally
#   a maker; evaluation must price the mode it will actually execute.
#   2026-06-10 deep verify (/tmp/deep_verify_report.md Verification B): added the
#   TAKER_OVER_MAKER_MARGIN hysteresis (knife-edge defaults MAKER) to kill the 93%
#   SUBMIT_ABORTED_MODE_FLIPPED churn, and marked p_fill_maker basis=GUESS with a
#   recalibration trigger. Refines the comparison; does NOT weaken FIX C's ratification.
"""Mode-consistent EV: explicit taker and maker formulas, selected per candidate.

The pre-incident hybrid evaluated EVERY candidate at TAKER cost (depth-walked ask
+ taker fee) multiplied by a visible-depth p_fill (~1.0 for crossing) while the
execution design is maker-resting primary. That hybrid (a) overstated cost for
maker entries, (b) ignored maker fill probability (measured live: ~10.8% resting
fill rate, ZERO fills at p 0.30-0.80), and (c) ignored adverse selection — a
resting buy fills disproportionately when the news moved AGAINST us (q|fill < q:
a selection effect the q_lcb does NOT cover, because the LCB bounds parameter
uncertainty of q, not the conditioning event "we got filled").

Two explicit per-share EV formulas (same probability units as robust_trade_score,
penalty included for cross-candidate comparability):

  EV_taker = p_fill_taker x (q_lcb - taker_all_in_cost - penalty)
      (today's crossing formula; admissible ONLY when the relative-spread guard
       passes — crossing a wide spread is forbidden regardless of edge).

  EV_maker = p_fill_maker x (q_fill_adj - maker_limit - penalty)
      maker_limit = tick_down(min(bid + tick, ask - tick, reservation))
          (bid-improving; the ask - tick cap makes a crossing maker limit
           UNCONSTRUCTABLE at the price level even where the venue ignores
           post_only — at a one-tick spread the order joins the bid instead of
           lifting the ask)
      q_fill_adj = max(0, q_lcb - lambda x half_spread)
          (first-order microstructure adverse-selection haircut: a fill on our
           bid-side rest implies the mid moved toward us by ~half the spread of
           bad news; lambda = 1.0 until the settlement loop measures the real
           haircut from fill_tracker facts)
      p_fill_maker: a conservative resting-fill prior (NOT the visible-depth
          taker coverage). Provenance is recorded so settlement can recalibrate.

Mode selection: compute BOTH, choose the max admissible. Both EVs always travel
on the receipt so the settlement loop can learn the real fill/haircut parameters.

Pure module: no I/O, no settings reads, no engine imports.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# Crossing a book whose relative spread (ask-bid)/mid exceeds this is forbidden
# regardless of edge: a wide spread IS the illiquidity signal, and the measured
# "edge" against such a book is dominated by model error (incident: 56% relative
# spread, edge measured with an unlicensed tail q). Maker resting stays allowed.
TAKER_MAX_RELATIVE_SPREAD = 0.25

# Conservative resting-fill prior for maker EV. PROVENANCE (2026-06-10 deep verify
# /tmp/deep_verify_report.md Verification B): this 0.10 is an UNCONDITIONED point prior
# (basis=GUESS), NOT a recalibrated bid+tick fill rate. The live resting facts are still too
# thin to certify it — 90 post_only-GTC orders matched to a book snapshot give a 17.8% overall
# any-fill rate, but bucketed by distance-from-touch the bid+tick bucket is n=13 and noisy
# (7.7%), so neither 0.10 nor a higher value is statistically licensed. RECALIBRATION TRIGGER:
# when fill_tracker accumulates N>=MAKER_FILL_RECALIBRATION_MIN_FACTS resting facts at bid+tick,
# the settlement loop must replace this prior with the measured conditional rate and flip the
# source off "GUESS". Until then it stays a documented guess fed into a MARGINED comparison
# (see TAKER_OVER_MAKER_MARGIN) so a thin prior cannot produce knife-edge mode churn.
MAKER_FILL_PROBABILITY_PRIOR = 0.10
MAKER_FILL_PROBABILITY_SOURCE = "fee_study_2026_06_prior:basis=GUESS"
# Minimum bid+tick resting facts before the GUESS prior may be recalibrated by the settlement loop.
MAKER_FILL_RECALIBRATION_MIN_FACTS = 30

# Mode-decision hysteresis margin (2026-06-10 deep verify Verification B). TAKER is chosen ONLY
# when EV_taker >= EV_maker * (1 + this margin); a knife-edge (EV gap within the margin) defaults
# MAKER. WHY: the maker/taker EVs are scaled ~10:1 by the un-recalibrated p_fill_maker guess, so
# a bare ev_taker >= ev_maker comparison is knife-edge on tight books — a 1-tick book wobble
# between proof-time and submit-time flips the winner, producing the 93% SUBMIT_ABORTED_MODE_
# FLIPPED waste (Mission 3) and a survivor bias toward the most taker-aggressive crosses. The
# margin makes the mode decision STABLE under sub-margin perturbation (proof_mode == fresh_mode
# holds across a 1-tick wobble), converting knife-edge aborts into stable maker rests. This does
# NOT weaken any honest gate: it makes the TAKER route STRICTER (taker must clear a margin, not
# merely tie), fully consistent with FIX C's "tight-spread favorite where EV_taker > EV_maker
# routes taker" ratification — a genuine favorite (Paris: EV_taker 9.15x EV_maker) clears any
# sane margin; only the wobble-band ties flip. Refining the comparison, never weakening a gate.
TAKER_OVER_MAKER_MARGIN = 0.15

# Full half-spread = the standard first-order adverse-selection estimate.
MAKER_ADVERSE_SELECTION_LAMBDA = 1.0

TAKER_SPREAD_GUARD_REASON = "TAKER_FORBIDDEN_RELATIVE_SPREAD"

PLACEMENT_MAKER = "maker_bid_improve"
PLACEMENT_TAKER = "taker_cross"

# =============================================================================
# K4.0 REST-THEN-CROSS (consolidated overhaul 2026-06-11, operator escalation
# 2026-06-10 ~22:45Z; evidence + KM measurement:
# docs/evidence/maker_taker/2026-06-10_taker_only_root_cause.md)
#
# THE DESIGN FAILURE: the one-shot maker-XOR-taker EV comparison above cannot
# represent the true option structure. All 6 live fills were FOK crosses paying
# 4.0% of notional to spread (books up to 8c wide) because p_fill_maker=0.10
# (GUESS) handicapped the maker lane ~10x. The fix is NOT a better point prior —
# it is the POLICY: default entry RESTS post_only GTC at the maker limit with a
# measured escalation deadline; the cross happens at the deadline (after the
# edge re-certifies through the FULL standard pipeline) or immediately in the
# declared exception lanes only.
#
# MEASUREMENT (Kaplan-Meier, n=108 right-censored GTC/post_only resting facts):
# cumulative fill 0.188@15min, 0.214@60min, 0.390@120min, 0.530@240min;
# 9/9 filled in the [0.40,1.00) price band. The old 0.10 was conditioned on
# ~25-minute rests of deep-longshot quotes — the wrong population (Fitz #4).
# =============================================================================

# Escalation deadline for a resting maker entry. 2026-06-16: 120 -> 20 min.
# RATIONALE (settlement-graded): the KM fill curve is nearly FLAT 15-60 min
# (0.188@15, 0.214@60, 0.390@120) — waiting to 120 min buys little extra maker
# fill but forfeits the cross for 2h. A settlement counterfactual on 49 settled
# day-ahead buy_no picks (NO won 41/49 = 84%, +$88.33 at $10/order vs $0 actually
# captured because all rested unfilled) proves the ADMISSIBLE cross (ask+fee <=
# q_lcb) of the unfilled remainder is POSITIVE after cost. The objective is
# FILLS-fast (maker OR cross), NOT maker-fill-rate. 20 min keeps a real
# maker-first window (captures the ~0.19 fast-fill cohort, honoring the
# Denver/Karachi rest-first antibody — this is NOT an immediate cross) then
# escalates to the settlement-proven +EV cross. basis=SETTLEMENT-EVIDENCE
# (interim; fit the optimal deadline from the KM curve x settlement EV per #64).
# Registry-tracked in src/contracts/time_semantics.py as
# maker_rest_escalation_deadline (now ~0.33 h).
MAKER_REST_ESCALATION_DEADLINE_MINUTES = 20.0

# Maker fill probability AT the escalation-deadline horizon. basis=MEASURED
# (KM @120min, all-band). Used for the recorded EV provenance of a REST
# decision — the EV of the policy's first leg. NOT a one-shot point prior;
# the policy, not this number, decides the mode.
MAKER_FILL_PROBABILITY_AT_ESCALATION_DEADLINE = 0.19
MAKER_FILL_PROBABILITY_DEADLINE_SOURCE = (
    "km_2026_06_10_resting_facts_n108@~20min(0.188@15min):basis=MEASURED"
)

# Taker-immediate exception lane 1: event end too near for the rest-then-cross
# plan to complete. basis=DERIVED: escalation deadline + 60 min slack for the
# escalation job cadence + re-certification cycle. Relation pinned in tests:
# MUST exceed the escalation deadline.
TAKER_IMMEDIATE_EVENT_END_FLOOR_MINUTES = 180.0

# Taker-immediate exception lane 2: a fleeting edge — an edge so large the
# market will likely correct it before the deadline, so resting forfeits it.
# basis=GUESS (honest): no measurement yet licenses a threshold. MEASUREMENT
# PLAN: escalation receipts record edge-at-rest vs edge-at-deadline; once the
# settled cohort is thick enough, replace with the measured edge-decay
# quantile. Until then 0.15 (~2x the largest fill edge tonight) keeps the lane
# narrow — resting stays the default for everything we actually traded.
TAKER_IMMEDIATE_FLEETING_EDGE_THRESHOLD = 0.15

# OPERATOR DIRECTIVE 2026-06-11 (Denver first fill: crossed a 5-cent spread on a
# 26h-to-settlement book under this lane, paying $0.43 mark-to-mid): a LARGE edge
# on a weather book is STRUCTURAL (favorite-longshot mispricing that persists for
# hours), not fleeting — the coverage-licensed harvest class itself carries
# +0.15..+0.40 edges, so an unconditional 0.15 trigger inverted REST_DEFAULT for
# every trade we actually want. Lane 2 is therefore admissible ONLY near the
# event end, where books genuinely reprice fast enough for an edge to vanish
# inside one rest deadline. Nesting relation (pinned in tests):
#   < EVENT_END_FLOOR (180m)            -> lane 4 crosses unconditionally
#   [180m, FLEETING_MAX (360m))         -> lane 5 crosses only on a huge edge
#   >= 360m or horizon unknown          -> REST_DEFAULT (rest post_only, escalate
#                                          at the measured 120m deadline, 39%
#                                          measured deadline fill rate)
TAKER_FLEETING_EDGE_MAX_MINUTES_TO_EVENT_END = 360.0

# Policy verdicts (travel on receipts; the settlement loop groups by these).
POLICY_REST_DEFAULT = "REST_DEFAULT"
POLICY_HOLD_REST_IN_PROGRESS = "HOLD_REST_IN_PROGRESS"
POLICY_TAKER_ESCALATED_AFTER_REST = "TAKER_ESCALATED_AFTER_REST"
POLICY_TAKER_EVENT_END_NEAR = "TAKER_EVENT_END_NEAR"
POLICY_TAKER_FLEETING_EDGE = "TAKER_FLEETING_EDGE"
POLICY_TAKER_MAKER_INADMISSIBLE = "TAKER_MAKER_INADMISSIBLE"
POLICY_MAKER_TAKER_FORBIDDEN = "MAKER_TAKER_FORBIDDEN"


def _finite(value: float | int | None) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def tick_round_down(price: float, tick_size: float) -> float:
    if tick_size <= 0:
        return price
    return round(math.floor(price / tick_size + 1e-9) * tick_size, 10)


def relative_spread(best_bid: float | None, best_ask: float | None) -> float | None:
    """(ask - bid) / mid; None when the two-sided book is absent/invalid."""
    bid = _finite(best_bid)
    ask = _finite(best_ask)
    if bid is None or ask is None or bid <= 0.0 or ask <= 0.0 or ask < bid:
        return None
    mid = (ask + bid) / 2.0
    if mid <= 0.0:
        return None
    return (ask - bid) / mid


def taker_spread_guard_reason(
    best_bid: float | None,
    best_ask: float | None,
    *,
    max_relative_spread: float = TAKER_MAX_RELATIVE_SPREAD,
) -> str | None:
    """Reason the TAKER lane is forbidden, or None when crossing is allowed.

    Fail-CLOSED for crossing: a book with no measurable two-sided spread (missing
    bid or ask) is the extreme illiquidity case — taker is forbidden there too.
    """
    spread = relative_spread(best_bid, best_ask)
    if spread is None:
        return f"{TAKER_SPREAD_GUARD_REASON}:spread=unmeasurable:max={max_relative_spread:.2f}"
    if spread > max_relative_spread:
        return f"{TAKER_SPREAD_GUARD_REASON}:spread={spread:.4f}:max={max_relative_spread:.2f}"
    return None


def maker_limit_price(
    *,
    best_bid: float | None,
    best_ask: float | None,
    tick_size: float,
    reservation: float,
) -> float | None:
    """Bid-improving, structurally non-crossing maker limit.

    tick_down(min(bid + tick, ask - tick, reservation)). Missing bid -> rest at
    min(ask - tick, reservation); missing ask -> min(bid + tick, reservation);
    both missing -> reservation. Returns None when the result is not a positive
    price (no maker placement exists).
    """
    bid = _finite(best_bid)
    ask = _finite(best_ask)
    tick = max(float(tick_size), 0.0)
    bound = float(reservation)
    if bid is not None:
        bound = min(bound, bid + tick)
    if ask is not None:
        bound = min(bound, ask - tick)
    limit = tick_round_down(bound, tick)
    if not math.isfinite(limit) or limit <= 0.0:
        return None
    return limit


TAKER_FORBIDDEN_NO_ASK_EMPTY = "NO_ASK_EMPTY"


def complementary_maker_quote_reservation(
    *,
    direction: str,
    q_lcb: float,
    complement_best_bid: float | None,
    tick_size: float,
    penalty: float = 0.0,
) -> float | None:
    """Reservation price for a MAKER quote into an EMPTY native ask book.

    A certified candidate whose OWN native ask side is empty/thin is NOT dead: the
    system is structurally a maker, so it QUOTES into the empty book — a resting
    NO bid at price ``p`` is economically matched (mint/merge) by buyers of the
    complementary YES outcome at ``1 - p``. Crossing the complementary book is
    therefore forbidden the same way crossing the own ask is: the resting limit
    must stay strictly BEHIND the complement's best bid so the rest never lifts
    the complementary side via mint.

    Bound (mission spec): ``limit <= min(reservation_belief, 1 - comp_best_bid - tick)``.
      * ``reservation_belief = q_lcb - penalty`` — the candidate's robust
        willingness-to-pay (with no native ask, the belief lower bound is the only
        cost anchor; the same q_lcb leg the taker score uses). Capping the
        reservation at the belief keeps the maker EV non-positive unless the quote
        genuinely sits below the certified edge.
      * ``1 - comp_best_bid - tick`` — the complementary non-crossing cap. With no
        complementary bid the cap is absent and the reservation is the belief alone.

    Returns the reservation price (a positive probability-units scalar) or ``None``
    when no admissible quote exists (belief non-positive, or the complementary cap
    forces the price to/below zero — a book with no resting room).

    Pure: no I/O. ``direction`` is accepted for symmetry / future buy_yes empty-ask
    quoting; today only ``buy_no`` reaches this path (buy_yes empty-ask stays a
    no-trade until its complementary NO-bid bound is exercised by a test).
    """
    belief = _finite(q_lcb)
    if belief is None:
        return None
    reservation = belief - float(penalty)
    comp_bid = _finite(complement_best_bid)
    tick = max(float(tick_size), 0.0)
    if comp_bid is not None:
        reservation = min(reservation, 1.0 - comp_bid - tick)
    if not math.isfinite(reservation) or reservation <= 0.0:
        return None
    return reservation


def maker_adverse_selection_haircut(
    *,
    best_bid: float | None,
    best_ask: float | None,
    maker_limit: float,
    lambda_adverse: float = MAKER_ADVERSE_SELECTION_LAMBDA,
) -> float:
    """lambda x half-spread; with no bid, the limit acts as our bid side."""
    ask = _finite(best_ask)
    bid = _finite(best_bid)
    if ask is None:
        return 0.0
    reference_bid = bid if bid is not None else float(maker_limit)
    half_spread = max(0.0, (ask - reference_bid) / 2.0)
    return float(lambda_adverse) * half_spread


@dataclass(frozen=True)
class ModeConsistentEv:
    """Per-candidate mode decision with BOTH EVs (settlement-loop provenance)."""

    chosen_mode: str  # "MAKER" | "TAKER"
    chosen_ev: float
    ev_taker: float | None
    ev_maker: float | None
    maker_limit_price: float | None
    relative_spread: float | None
    taker_forbidden_reason: str | None
    maker_fill_probability: float
    maker_fill_probability_source: str
    placement: str  # PLACEMENT_MAKER | PLACEMENT_TAKER
    taker_over_maker_margin: float = TAKER_OVER_MAKER_MARGIN  # hysteresis margin applied
    # K4.0 REST-THEN-CROSS provenance (None on the legacy one-shot path):
    policy: str | None = None  # POLICY_* verdict that produced chosen_mode
    escalation_deadline_minutes: float | None = None  # set on REST_DEFAULT decisions


def select_mode_consistent_ev(
    *,
    q_lcb: float,
    taker_all_in_cost: float | None,
    p_fill_taker: float,
    best_bid: float | None,
    best_ask: float | None,
    tick_size: float,
    reservation: float,
    p_fill_maker: float = MAKER_FILL_PROBABILITY_PRIOR,
    p_fill_maker_source: str = MAKER_FILL_PROBABILITY_SOURCE,
    lambda_adverse: float = MAKER_ADVERSE_SELECTION_LAMBDA,
    max_relative_spread: float = TAKER_MAX_RELATIVE_SPREAD,
    taker_over_maker_margin: float = TAKER_OVER_MAKER_MARGIN,
    penalty: float = 0.0,
) -> ModeConsistentEv:
    """Compute EV_taker and EV_maker; choose the better ADMISSIBLE one.

    Taker is admissible only when the relative-spread guard passes AND a taker
    cost exists. Maker is admissible whenever a positive non-crossing limit
    exists. When neither is admissible the result is a MAKER decision with
    chosen_ev = -inf: the candidate cannot be priced in either mode, and the
    non-positive EV blocks it at the trade-score gate.

    HYSTERESIS (Verification B): TAKER is chosen over an admissible MAKER only when
    ``ev_taker >= ev_maker * (1 + taker_over_maker_margin)``. A knife-edge (the two EVs
    within the margin) defaults MAKER, so a 1-tick book wobble between proof-time and
    submit-time cannot flip the mode (kills the 93% SUBMIT_ABORTED_MODE_FLIPPED waste).
    This only makes the taker route STRICTER (never weakens a gate): a genuine favorite
    clears any sane margin, consistent with FIX C's tight-spread-taker ratification.
    """
    q = float(q_lcb)
    spread = relative_spread(best_bid, best_ask)
    taker_forbidden = taker_spread_guard_reason(
        best_bid, best_ask, max_relative_spread=max_relative_spread
    )
    cost = _finite(taker_all_in_cost)
    ev_taker: float | None = None
    if cost is not None:
        ev_taker = max(0.0, min(1.0, float(p_fill_taker))) * (q - cost - float(penalty))

    limit = maker_limit_price(
        best_bid=best_bid, best_ask=best_ask, tick_size=tick_size, reservation=reservation
    )
    ev_maker: float | None = None
    if limit is not None:
        haircut = maker_adverse_selection_haircut(
            best_bid=best_bid,
            best_ask=best_ask,
            maker_limit=limit,
            lambda_adverse=lambda_adverse,
        )
        q_fill_adj = max(0.0, q - haircut)
        ev_maker = max(0.0, min(1.0, float(p_fill_maker))) * (
            q_fill_adj - limit - float(penalty)
        )

    taker_allowed = ev_taker is not None and taker_forbidden is None
    maker_allowed = ev_maker is not None
    # HYSTERESIS: taker must CLEAR the maker EV by the margin (not merely tie). A knife-edge
    # ties to MAKER -> mode is stable under a sub-margin (1-tick) book wobble. The margin scales
    # the maker leg so a negative/zero EV_maker still lets a positive EV_taker win (1+margin on a
    # non-positive number does not raise the bar above a positive taker EV).
    _margin = max(0.0, float(taker_over_maker_margin))
    # Guard the margin comparison so it never dereferences a None ev_taker: a
    # candidate with no taker cost at all (taker_all_in_cost None -> ev_taker None,
    # e.g. the maker-quote-into-empty-ask lane where taker is structurally
    # impossible) has taker_allowed False, and the comparison must be skipped
    # entirely rather than coercing None to float.
    _taker_clears_maker = taker_allowed and (
        (not maker_allowed)
        or (float(ev_taker) >= float(ev_maker) * (1.0 + _margin))
    )
    if taker_allowed and _taker_clears_maker:
        chosen_mode, chosen_ev, placement = "TAKER", float(ev_taker), PLACEMENT_TAKER
    elif maker_allowed:
        chosen_mode, chosen_ev, placement = "MAKER", float(ev_maker), PLACEMENT_MAKER
    else:
        chosen_mode, chosen_ev, placement = "MAKER", float("-inf"), PLACEMENT_MAKER
    return ModeConsistentEv(
        chosen_mode=chosen_mode,
        chosen_ev=chosen_ev,
        ev_taker=ev_taker,
        ev_maker=ev_maker,
        maker_limit_price=limit,
        relative_spread=spread,
        taker_forbidden_reason=taker_forbidden,
        maker_fill_probability=float(p_fill_maker),
        maker_fill_probability_source=str(p_fill_maker_source),
        placement=placement,
        taker_over_maker_margin=_margin,
    )


def select_rest_then_cross_mode(
    *,
    q_lcb: float,
    taker_all_in_cost: float | None,
    p_fill_taker: float,
    best_bid: float | None,
    best_ask: float | None,
    tick_size: float,
    reservation: float,
    minutes_to_event_end: float | None = None,
    unexpired_family_rest: bool = False,
    escalated_after_rest: bool = False,
    p_fill_maker: float = MAKER_FILL_PROBABILITY_AT_ESCALATION_DEADLINE,
    p_fill_maker_source: str = MAKER_FILL_PROBABILITY_DEADLINE_SOURCE,
    lambda_adverse: float = MAKER_ADVERSE_SELECTION_LAMBDA,
    max_relative_spread: float = TAKER_MAX_RELATIVE_SPREAD,
    taker_over_maker_margin: float = TAKER_OVER_MAKER_MARGIN,
    penalty: float = 0.0,
    escalation_deadline_minutes: float = MAKER_REST_ESCALATION_DEADLINE_MINUTES,
    event_end_floor_minutes: float = TAKER_IMMEDIATE_EVENT_END_FLOOR_MINUTES,
    fleeting_edge_threshold: float = TAKER_IMMEDIATE_FLEETING_EDGE_THRESHOLD,
) -> ModeConsistentEv:
    """K4.0 REST-THEN-CROSS policy (supersedes the one-shot EV comparison).

    Policy order (each verdict travels on the receipt as ``policy``):

    1. HOLD_REST_IN_PROGRESS — the ANTIBODY lane: an unexpired same-family maker
       rest exists, so NO new order of either mode may be constructed
       (chosen_ev=-inf forces the trade-score gate to reject). The operator
       relationship: "no taker cross may exist while an unexpired same-family
       maker rest exists" — pinned by
       tests/strategy/live_inference/test_rest_then_cross_policy.py.
    2. TAKER_MAKER_INADMISSIBLE — no bid to rest behind (one-sided book): the
       taker lane stays lawful exactly as before.
    3. TAKER_ESCALATED_AFTER_REST — the deadline cross: a prior rest for this
       family was cancelled UNFILLED after >= the escalation deadline, and the
       edge re-certified through the FULL standard pipeline (this call IS the
       re-certification — the caller only reaches it with certified q_lcb).
    4. TAKER_EVENT_END_NEAR — the rest-then-cross plan cannot complete before
       the event ends; immediate cross while taker is admissible.
    5. TAKER_FLEETING_EDGE — raw taker edge >= the fleeting threshold; resting
       would likely forfeit it.
    6. REST_DEFAULT — everything else rests post_only GTC at the maker limit
       with the measured escalation deadline. THIS is the default the operator
       ordered: a fresh-book EV preference for crossing is NOT a license to
       cross.

    EV provenance: both EVs are still computed (with the MEASURED deadline-
    horizon fill probability, not the retired 0.10 guess) and travel on the
    receipt so the settlement loop recalibrates the hazard curve and lambda.
    The taker spread guard and the hysteresis margin remain lawful and
    untouched inside the EV kernel.
    """
    mode_ev = select_mode_consistent_ev(
        q_lcb=q_lcb,
        taker_all_in_cost=taker_all_in_cost,
        p_fill_taker=p_fill_taker,
        best_bid=best_bid,
        best_ask=best_ask,
        tick_size=tick_size,
        reservation=reservation,
        p_fill_maker=p_fill_maker,
        p_fill_maker_source=p_fill_maker_source,
        lambda_adverse=lambda_adverse,
        max_relative_spread=max_relative_spread,
        taker_over_maker_margin=taker_over_maker_margin,
        penalty=penalty,
    )
    from dataclasses import replace as _replace

    # FIX B (#127, 2026-06-15) — SETTLEMENT-HONEST q_lcb CAP on EVERY cross lane.
    # HARD LAW: a taker cross may NEVER execute above the conservative q_lcb. A
    # marketable taker is admissible ONLY when the FRESH all-in taker cost (best
    # ask + fee, or the certified sweep cost passed as taker_all_in_cost) clears
    # the conservative bound — i.e. <= q_lcb. When the fresh ask sits above q_lcb
    # (the Chengdu 0.73-ask vs 0.72-q_lcb case) the taker lane is INADMISSIBLE and
    # the policy stays MAKER / no-trade — a correct outcome, not a forced fill, and
    # NOT a taker the downstream cert builder would have to reject (that produced a
    # MODE_FLIPPED / TOUCH_EXCEEDS_RESERVATION churn loop instead of a clean rest).
    # This gate does not LOOSEN anything: it makes every taker lane STRICTER, fully
    # consistent with the conservative-entry law and the existing cert-builder cap
    # (event_reactor_adapter TAKER_BUY_TOUCH_EXCEEDS_RESERVATION). The wide-spread
    # guard and the REST_DEFAULT doctrine (a favorable all-in alone does NOT license
    # an immediate cross — the Karachi antibody) remain in force above this.
    _q = float(q_lcb)
    _taker_cost = _finite(taker_all_in_cost)
    taker_clears_conservative_bound = (
        _taker_cost is not None and _taker_cost <= _q + 1e-9
    )
    taker_admissible = (
        mode_ev.ev_taker is not None
        and mode_ev.taker_forbidden_reason is None
        and taker_clears_conservative_bound
    )
    maker_admissible = (
        mode_ev.ev_maker is not None and mode_ev.maker_limit_price is not None
    )

    def _as_maker(policy: str, *, chosen_ev: float | None = None, deadline: float | None = None) -> ModeConsistentEv:
        ev = chosen_ev if chosen_ev is not None else (
            float(mode_ev.ev_maker) if mode_ev.ev_maker is not None else float("-inf")
        )
        return _replace(
            mode_ev,
            chosen_mode="MAKER",
            chosen_ev=ev,
            placement=PLACEMENT_MAKER,
            policy=policy,
            escalation_deadline_minutes=deadline,
        )

    def _as_taker(policy: str) -> ModeConsistentEv:
        return _replace(
            mode_ev,
            chosen_mode="TAKER",
            chosen_ev=float(mode_ev.ev_taker),
            placement=PLACEMENT_TAKER,
            policy=policy,
            escalation_deadline_minutes=None,
        )

    # 1. ANTIBODY: an unexpired same-family rest forbids ANY new order.
    if unexpired_family_rest:
        return _as_maker(POLICY_HOLD_REST_IN_PROGRESS, chosen_ev=float("-inf"))

    # 2. One-sided book: maker structurally impossible; taker lane stays lawful.
    if not maker_admissible:
        if taker_admissible:
            return _as_taker(POLICY_TAKER_MAKER_INADMISSIBLE)
        return _as_maker(POLICY_MAKER_TAKER_FORBIDDEN, chosen_ev=float("-inf"))

    # 3. Deadline escalation: rest expired unfilled + edge re-certified -> cross.
    if escalated_after_rest and taker_admissible:
        return _as_taker(POLICY_TAKER_ESCALATED_AFTER_REST)

    # 4. Event end too near for rest-then-cross to complete.
    if (
        taker_admissible
        and minutes_to_event_end is not None
        and float(minutes_to_event_end) < float(event_end_floor_minutes)
    ):
        return _as_taker(POLICY_TAKER_EVENT_END_NEAR)

    # 5. Fleeting edge: resting would likely forfeit it. OPERATOR DIRECTIVE
    #    2026-06-11 (Denver $0.43 spread cross): admissible ONLY near the event
    #    end — a structural weather edge hours from settlement is NOT fleeting,
    #    and the licensed harvest class itself exceeds the edge threshold, so an
    #    unconditional trigger would invert REST_DEFAULT for every good trade.
    #    Unknown horizon is conservative: REST.
    if (
        taker_admissible
        and taker_all_in_cost is not None
        and minutes_to_event_end is not None
        and float(minutes_to_event_end) < TAKER_FLEETING_EDGE_MAX_MINUTES_TO_EVENT_END
    ):
        raw_taker_edge = float(q_lcb) - float(taker_all_in_cost)
        if raw_taker_edge >= float(fleeting_edge_threshold):
            return _as_taker(POLICY_TAKER_FLEETING_EDGE)

    # 6. THE DEFAULT: rest post_only GTC with the measured escalation deadline.
    #    (Also the escalated/taker-forbidden case: the spread guard stays lawful;
    #    the rest re-posts and the next escalation re-evaluates.)
    policy = POLICY_MAKER_TAKER_FORBIDDEN if (
        escalated_after_rest and not taker_admissible
    ) else POLICY_REST_DEFAULT
    return _as_maker(policy, deadline=float(escalation_deadline_minutes))
