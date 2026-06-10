# Created: 2026-06-08
# Last reused or audited: 2026-06-10
# Audit note 2026-06-10: GATE 2 PRICE_MOVED is now a TAKER-only protection +
#   bounded slippage tolerance. A resting MAKER order pays its own admitted limit
#   and never chases the recaptured ask, so RecaptureInputs.order_rests_at_admitted_price
#   skips the ceiling for makers; a TAKER ceiling admits up to max_acceptable +
#   min(max(one_tick, 5%), 1¢). GATE 3 (edge on recaptured cost) is UNCHANGED and
#   still aborts EDGE_REVERSED on a tolerated/rested move that flips edge negative —
#   the tolerance never admits a negative-edge submit. Fixes the live sub-3¢
#   false-abort churn (Lucknow/Seoul/Singapore 2026-06-10).
# Audit note 2026-06-09: added SUBMIT_ABORTED_BELOW_MIN_ORDER lifecycle state +
#   MIN_ORDER reversal reason. Antibody for the false-EDGE_REVERSED regression where
#   a positive-edge candidate whose fractional-Kelly stake fell below the venue min
#   order was mislabeled "edge reversed". EDGE_REVERSED now means exactly: ΔU ≤ 0 at
#   EVERY admissible stake including min order.
# Authority basis: "bin selection.md" §7 (state machine + reversal table +
#   hysteresis) + §3 (reversal-type table) + §5 submit-recapture pseudocode +
#   §6 (fallback queue rule) + §9 Hidden #7 (fallback is WATCH-only) +
#   §11 Phase 5 + §13 no-trade gates + §14.9 / §14.10 + operator directive 2026-06-08.
"""RedecisionEngine — Phase-5 candidate lifecycle / reversal state machine.

Spec §11 Phase 5, §7, §3, §5, §6, §9 Hidden #7, §14.9-14.10.

WHY THIS OBJECT EXISTS (spec §10 redecision/reversal gap, Hidden #7):
  Pre-bin-selection Zeus recaptured the selected leg's price at submit and
  carried fallback rank/count *metadata*, but reversals (forecast / bin / side /
  edge / price / family-rank / submit / portfolio) were NOT first-class states.
  Two failure modes followed:

    1. A stale fallback could become execution authority the moment the primary
       failed, submitting the wrong side/bin without a fresh re-rank (Hidden #7).
    2. Submit-time checks *validated* the selected leg's price instead of
       *recomputing* utility, so an edge reversal that held price constant
       (q fell, cost held) could slip through.

  RedecisionEngine makes the reversal taxonomy explicit and fail-closed:

    * SUBMIT_RECAPTURE_REQUIRED is mandatory and aborts on price-moved /
      edge-reversed / family-reversed (§5 submit pseudocode, §7 transition table).
    * A fallback is WATCH-only and CANNOT auto-submit; it must transition through
      a full re-rank before it can become primary (§6 fallback rule, Hidden #7).
    * A rank/side/bin switch requires BOTH a utility margin beyond η_switch AND
      a no-churn window beyond T_no_churn (§7 hysteresis), preventing flip-flop
      on small price/forecast noise.

DEFAULT-OFF / SHADOW (operator directive 2026-06-08):
  This is a PURE state machine plus pure transition functions. No DB, no live
  wiring, no import side effects. Importing it changes NO live trading behavior;
  the live decision path is not routed through it here (that is a later
  integration phase, §11 Phase 5 rollback: "keep current submit recapture
  fail-closed behavior"). No existing gate is weakened by its presence.

The engine reads only structural facts off the peer contracts
(:class:`NativeSideCandidate`, :class:`ExecutableCostCurve`): it asks a
recaptured curve for its depth-walked all-in cost at the chosen stake and
compares scalars. It never reaches into private internals.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Mapping, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.contracts.executable_cost_curve import ExecutableCostCurve
    from src.contracts.native_side_candidate import NativeSideCandidate


# ---------------------------------------------------------------------------
# §7 candidate lifecycle states (verbatim ordering from the spec state machine).
# ---------------------------------------------------------------------------
class CandidateLifecycleState(StrEnum):
    """Every state a candidate may occupy (spec §7 + §14.9).

    Forward path (happy):
        DISCOVERED -> FORECAST_DISTRIBUTION_BUILT -> FAMILY_GRAPH_BUILT
        -> CANDIDATES_PRICED -> ROBUST_SCORED -> WATCH / READY_TO_SUBMIT
        -> SUBMIT_RECAPTURE_REQUIRED -> SUBMITTED -> ACKED
        -> PARTIAL_FILL -> FILLED -> LEARNING_INCLUDED

    Abort branches off SUBMIT_RECAPTURE_REQUIRED (mandatory fail-closed, §5/§7):
        -> SUBMIT_ABORTED_PRICE_MOVED
        -> SUBMIT_ABORTED_EDGE_REVERSED
        -> SUBMIT_ABORTED_FAMILY_REVERSED
        (then WATCH or NO_TRADE)

    No-fill branches off SUBMITTED:
        -> NO_FILL / CANCELLED
        -> LEARNING_EXCLUDED / LEARNING_INCLUDED_AS_OPPORTUNITY_ONLY

    The terminal LEARNING_* split (§14.12) keeps aborts / no-fills out of
    realized trade-loss PnL: they are learning events, not trade losses.
    """

    DISCOVERED = auto()
    FORECAST_DISTRIBUTION_BUILT = auto()
    FAMILY_GRAPH_BUILT = auto()
    CANDIDATES_PRICED = auto()
    ROBUST_SCORED = auto()
    WATCH = auto()
    READY_TO_SUBMIT = auto()
    SUBMIT_RECAPTURE_REQUIRED = auto()

    # Submit aborts (recapture failed a hard gate). Mandatory fail-closed (§5/§7).
    SUBMIT_ABORTED_PRICE_MOVED = auto()
    SUBMIT_ABORTED_EDGE_REVERSED = auto()
    SUBMIT_ABORTED_FAMILY_REVERSED = auto()
    # Edge is GENUINELY POSITIVE at the venue min order, but the fractional-Kelly
    # haircut shrank the chosen stake below that min order AND it could not be bumped
    # to min order (ΔU(min_order) ≤ 0 — impossible by definition here, so this state is
    # only reached via the bankroll-cap guard: min_order_usd would exceed the operator
    # single-order bankroll cap). DISTINCT from EDGE_REVERSED: this is a sizing/venue
    # floor abort, NOT a "no edge" verdict. Keeps the regret ledger honest — a candidate
    # with real edge that we declined for sizing reasons must never be recorded as
    # "edge reversed". Antibody for the 2026-06-09 false-EDGE_REVERSED regression.
    SUBMIT_ABORTED_BELOW_MIN_ORDER = auto()

    SUBMITTED = auto()
    ACKED = auto()
    PARTIAL_FILL = auto()
    FILLED = auto()
    NO_FILL = auto()
    CANCELLED = auto()

    NO_TRADE = auto()

    # Terminal learning states (§14.12 four-truth separation).
    LEARNING_INCLUDED = auto()
    LEARNING_EXCLUDED = auto()
    LEARNING_INCLUDED_AS_OPPORTUNITY_ONLY = auto()


# Submit-abort states a recapture may resolve to (used by callers to branch
# learning attribution). Kept as a frozenset so membership is a structural test.
SUBMIT_ABORT_STATES: frozenset[CandidateLifecycleState] = frozenset(
    {
        CandidateLifecycleState.SUBMIT_ABORTED_PRICE_MOVED,
        CandidateLifecycleState.SUBMIT_ABORTED_EDGE_REVERSED,
        CandidateLifecycleState.SUBMIT_ABORTED_FAMILY_REVERSED,
        CandidateLifecycleState.SUBMIT_ABORTED_BELOW_MIN_ORDER,
    }
)


# ---------------------------------------------------------------------------
# §3 / §7 reversal taxonomy.
# ---------------------------------------------------------------------------
class ReversalReason(StrEnum):
    """The reversal types Zeus models (spec §3 reversal table / §7).

    Direct-model reversals (acted on):
        FORECAST     — F_t changed enough to move q / q_lcb / utility order.
        BIN          — best bin changed within the same family.
        SIDE         — best native side switched YES <-> NO.
        EDGE         — robust edge / utility crossed zero or a threshold.
        PRICE        — executable cost curve crossed max_acceptable_price.
        FAMILY_RANK  — primary / fallback ordering changed (with hysteresis).
        SUBMIT       — decision candidate failed submit recapture (fail-closed).
        PORTFOLIO    — existing / pending exposure changed marginal utility.
        MIN_ORDER    — edge positive at min order but the sized stake could not clear
                       the venue floor within the bankroll cap (sizing abort, NOT an
                       edge reversal). Keeps EDGE distinct from venue-floor declines.
    """

    FORECAST = auto()
    BIN = auto()
    SIDE = auto()
    EDGE = auto()
    PRICE = auto()
    FAMILY_RANK = auto()
    SUBMIT = auto()
    PORTFOLIO = auto()
    MIN_ORDER = auto()


# ---------------------------------------------------------------------------
# Hysteresis policy (§7).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class HysteresisPolicy:
    """The two-condition anti-churn policy (spec §7 hysteresis).

    A family-rank / side / bin switch is permitted ONLY when BOTH hold::

        ΔU_new > ΔU_old + eta_switch        (utility margin, not noise)
        t - t_last_switch > t_no_churn      (no-churn window expired)

    * ``eta_switch``  — minimum robust-marginal-utility margin the challenger
      must beat the incumbent by. Suppresses sub-noise flip-flops between
      adjacent bins or YES/NO sides.
    * ``t_no_churn``  — minimum time since the last switch. Suppresses rapid
      oscillation even when the margin is genuine.
    """

    eta_switch: float = 0.0
    t_no_churn: timedelta = timedelta(0)

    def __post_init__(self) -> None:
        if not (self.eta_switch >= 0.0):
            raise ValueError(f"eta_switch must be >= 0, got {self.eta_switch}")
        if self.t_no_churn < timedelta(0):
            raise ValueError(f"t_no_churn must be >= 0, got {self.t_no_churn}")

    def churn_window_seconds(self) -> float:
        return self.t_no_churn.total_seconds()


# ---------------------------------------------------------------------------
# Inputs / results.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RecaptureInputs:
    """Fresh-at-submit recapture inputs (spec §5 submit pseudocode / §7).

    These are the recomputed-not-just-validated quantities the submit path must
    rebuild from a FRESH snapshot (§14.10): a rebuilt cost curve, the stake under
    consideration, the max acceptable all-in price, the recomputed robust
    probability lower bound, whether the forecast is still current, and whether
    the family rank reversed.

    Note the asymmetry the spec demands: ``recaptured_cost_curve`` is the FRESH
    book (the engine walks it for the all-in cost at ``stake_usd``). A stale or
    failed recapture is signalled by passing ``recaptured_cost_curve=None``,
    which the engine treats as a hard fail-closed submit abort (§13 "Snapshot
    stale and recapture fails").
    """

    recaptured_cost_curve: Optional["ExecutableCostCurve"]
    stake_usd: Decimal
    max_acceptable_price: Decimal
    recaptured_q_lcb: float
    forecast_still_current: bool
    family_rank_reversed: bool
    # Maker/taker fill semantics at submit (2026-06-10). The PRICE_MOVED ceiling is
    # a TAKER protection: a taker order crosses the fresh book and PAYS the recaptured
    # all-in cost, so a recaptured cost above the admitted ceiling is a real adverse
    # cost the gate must bound. A MAKER (resting GTC/GTD limit) order pays its OWN
    # limit (computed downstream as min(held_prob, ask) - offset, i.e. AT the admitted
    # price) and RESTS when the ask moves away — it never chases, never pays the
    # recaptured ask. For the maker path the recaptured ask drifting up is therefore
    # NOT a price move we pay, and aborting PRICE_MOVED contradicts the maker design
    # (the verifier even requires would_cross_book=false for maker/post-only).
    #
    #   * ``order_rests_at_admitted_price=True``  (MAKER): skip the PRICE_MOVED
    #     ceiling abort entirely — the order rests at the admitted limit. GATE 3
    #     (edge on the recaptured cost) STILL runs (conservative: the recaptured
    #     cost is >= what a resting maker pays, so a passing edge here is a fortiori
    #     positive at the limit we actually pay) and the chosen stake/price the
    #     intent carries is the admitted boundary, never the chased ask.
    #   * ``order_rests_at_admitted_price=False`` (TAKER): the order crosses and
    #     pays the recaptured cost — the BOUNDED slippage tolerance (one tick / 5%
    #     / 1¢ cap) governs the ceiling so a microscopic tick of drift does not
    #     false-abort, but an unbounded chase still aborts PRICE_MOVED.
    # Defaults False (taker-style ceiling) so callers that do not yet supply the
    # mode keep the strict pre-change behavior — fail-closed on missing provenance.
    order_rests_at_admitted_price: bool = False


@dataclass(frozen=True)
class SubmitRecaptureDecision:
    """Outcome of the mandatory submit-recapture gate (spec §14.9 object).

    ``may_submit`` is the single fail-closed authority: it is True ONLY when the
    recaptured candidate cleared every hard gate (price, edge, forecast
    currency, family rank, depth). Any abort sets ``may_submit=False`` and a
    terminal ``state`` in :data:`SUBMIT_ABORT_STATES` plus the triggering
    ``reversal_reason``.
    """

    state: CandidateLifecycleState
    may_submit: bool
    reversal_reason: Optional[ReversalReason] = None
    recaptured_all_in_cost: Optional[float] = None
    recaptured_edge_lcb: Optional[float] = None
    detail: str = ""
    # Bounded slippage-tolerance provenance (2026-06-10). When the recaptured
    # all-in cost is STRICTLY WORSE than the admitted ``max_acceptable_price`` but
    # within the bounded tolerance (one venue tick / 5% relative / 1¢ absolute cap,
    # whichever binds), GATE 4 proceeds AT THE RECAPTURED PRICE rather than aborting
    # PRICE_MOVED. These fields record that a tolerance was consumed so settlement
    # attribution can measure whether tolerated entries underperform. ``admitted_price``
    # is the decision-time ceiling; ``price_moved_within_tolerance`` is True only on the
    # tolerated path (False on a clean no-move recapture and on a beyond-tolerance abort).
    price_moved_within_tolerance: bool = False
    admitted_price: Optional[float] = None
    price_move_tolerance: Optional[float] = None


@dataclass(frozen=True)
class RankDecision:
    """Outcome of a WATCH-set re-rank under hysteresis (spec §6 / §7).

    ``primary_bin_id`` is who is primary AFTER applying hysteresis. ``switched``
    is True iff the incumbent was replaced. A switch sets ``reversal_reason`` to
    the trigger (FAMILY_RANK by default, or the supplied trigger e.g. FORECAST).
    """

    primary_bin_id: str
    switched: bool
    reversal_reason: Optional[ReversalReason] = None
    challenger_bin_id: Optional[str] = None
    delta_utility: float = 0.0
    detail: str = ""


@dataclass(frozen=True)
class FallbackPromotion:
    """Outcome of asking the engine to act on a fallback after a primary abort.

    Hidden #7 antibody: a fallback is WATCH-only. This object always reports
    ``state == WATCH``, ``may_submit == False``, and ``requires_full_rerank ==
    True`` — there is no construction in which a primary abort alone promotes a
    fallback to submit authority.
    """

    candidate: "NativeSideCandidate"
    state: CandidateLifecycleState
    may_submit: bool
    requires_full_rerank: bool
    detail: str = ""


# ---------------------------------------------------------------------------
# The engine.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RedecisionEngine:
    """Pure re-decision / reversal state machine (spec §7 / §11 Phase 5).

    Stateless across calls: every transition is a pure function of its inputs.
    The only configuration it carries is the :class:`HysteresisPolicy`. It holds
    NO mutable state, NO DB handle, NO clock — callers pass times explicitly so
    the machine is deterministic and replayable (spec §12.E relationship tests).
    """

    hysteresis: HysteresisPolicy = field(default_factory=HysteresisPolicy)

    # ------------------------------------------------------------------
    # Submit recapture — mandatory fail-closed (spec §5 / §7 / §14.10).
    # ------------------------------------------------------------------
    def evaluate_submit_recapture(
        self,
        primary: "NativeSideCandidate",
        inputs: RecaptureInputs,
    ) -> SubmitRecaptureDecision:
        """Recompute (not just validate) the primary on a FRESH snapshot.

        Order of hard gates mirrors the §5 submit pseudocode and §7 transition
        table. Each is fail-closed: the FIRST failing gate aborts; submission is
        permitted only when ALL pass.

          1. Recapture present — a missing/stale fresh snapshot is a hard abort
             (§13 "Snapshot stale and recapture fails"). Mapped to PRICE_MOVED
             with detail, since no executable price could be re-established.
          2. Price moved — recaptured all-in cost at ``stake_usd`` exceeds
             ``max_acceptable_price`` -> SUBMIT_ABORTED_PRICE_MOVED (§7 price row).
          3. Edge reversed — recaptured ``edge_lcb = q_lcb - all_in_cost <= 0``
             (or forecast no longer current) -> SUBMIT_ABORTED_EDGE_REVERSED
             (§7 edge row; §5 "if recaptured_score.utility <= 0: Abort").
          4. Family reversed — primary/fallback order flipped at recapture ->
             SUBMIT_ABORTED_FAMILY_REVERSED. The spec's
             AbortOrSwitchOnlyAfterFullRerank: the switch is NOT taken inline;
             the engine aborts THIS submit and defers to a full re-rank.

        ``recaptured_all_in_cost`` / ``recaptured_edge_lcb`` are surfaced on the
        decision for audit even on the success path.
        """
        # Gate 1: recapture must exist (fail-closed, §13).
        curve = inputs.recaptured_cost_curve
        if curve is None:
            return SubmitRecaptureDecision(
                state=CandidateLifecycleState.SUBMIT_ABORTED_PRICE_MOVED,
                may_submit=False,
                reversal_reason=ReversalReason.SUBMIT,
                detail=(
                    "recapture failed: no fresh executable snapshot; fail closed "
                    "(§13 'Snapshot stale and recapture fails')"
                ),
            )

        # Recaptured all-in cost at the chosen stake. avg_cost walks the fresh
        # book and returns a typed, fee-adjusted ExecutionPrice in
        # probability_units; we read its scalar value for the gate comparisons.
        # A book that cannot fill the stake (depth exhausted / below min order)
        # raises ValueError inside avg_cost — that is itself a fail-closed abort.
        try:
            all_in_price = curve.avg_cost(Decimal(inputs.stake_usd))
            all_in_cost = float(all_in_price.value)
        except ValueError as exc:
            return SubmitRecaptureDecision(
                state=CandidateLifecycleState.SUBMIT_ABORTED_PRICE_MOVED,
                may_submit=False,
                reversal_reason=ReversalReason.PRICE,
                detail=f"recaptured book cannot fill stake (fail closed): {exc}",
            )

        edge_lcb = float(inputs.recaptured_q_lcb) - all_in_cost

        # Gate 2: price moved through max acceptable (§7 price row).
        #
        # The §7 'price row' is a TAKER protection — it bounds what an immediate
        # crossing fill PAYS. Whether it fires at all depends on the fill semantics
        # the order will use at submit (``order_rests_at_admitted_price``):
        #
        #   MAKER (rests at admitted price): a resting GTC/GTD limit pays its OWN
        #   limit (downstream ``compute_native_limit_price`` = min(held_prob, ask) -
        #   offset, i.e. AT the admitted boundary) and RESTS when the ask moves away;
        #   it never crosses, never chases, never pays the recaptured ask. A
        #   recaptured ask drifting up is therefore NOT a price we pay. Aborting
        #   PRICE_MOVED here would contradict the maker design (the verifier requires
        #   would_cross_book=false for maker/post-only) and produces exactly the
        #   live false-abort churn observed 2026-06-10 on sub-3¢ books. SKIP the
        #   ceiling abort. GATE 3 below still runs on the recaptured cost — which is
        #   >= what the resting maker pays, so a passing edge there is a fortiori
        #   positive at the admitted limit (the iron-rule economic check is preserved,
        #   conservatively). The chosen stake/price the intent carries stays the
        #   admitted boundary (caller leaves execution_price = S1 boundary on the
        #   tolerated-rest path), never a chased ask.
        #
        #   TAKER (crosses to fill): the order pays the recaptured all-in cost, so
        #   the ceiling must bound it — but with a BOUNDED slippage tolerance, not
        #   zero tolerance. A zero-tolerance ceiling false-aborts on a single tick of
        #   drift (the scoring snapshot is up to ~60s stale; the next cycle re-admits
        #   at the new price anyway → lag+churn, not protection). Admit up to
        #   ``max_acceptable_price + tolerance``, tolerance = min(max(one_tick,
        #   0.05*max_acceptable), 0.01) — one venue tick / 5% relative / 1¢ absolute
        #   chase cap, whichever binds. A move beyond the ceiling is a genuine price
        #   move -> PRICE_MOVED. The downstream taker touch-vs-reservation check is
        #   defense-in-depth on the actual crossing price.
        max_acceptable = Decimal(inputs.max_acceptable_price)
        recaptured_cost_dec = Decimal(str(all_in_cost))
        _ABS_CHASE_CAP = Decimal("0.01")
        one_tick = curve.min_tick if curve.min_tick > Decimal("0") else Decimal("0.001")
        relative_tol = max_acceptable * Decimal("0.05")
        tolerance = min(max(one_tick, relative_tol), _ABS_CHASE_CAP)
        price_ceiling = max_acceptable + tolerance
        # Provenance: the recapture priced STRICTLY WORSE than admitted, yet the order
        # proceeds (maker rests at admitted price, or taker within bounded tolerance).
        price_moved_but_admitted = recaptured_cost_dec > max_acceptable

        if not inputs.order_rests_at_admitted_price and recaptured_cost_dec > price_ceiling:
            return SubmitRecaptureDecision(
                state=CandidateLifecycleState.SUBMIT_ABORTED_PRICE_MOVED,
                may_submit=False,
                reversal_reason=ReversalReason.PRICE,
                recaptured_all_in_cost=all_in_cost,
                recaptured_edge_lcb=edge_lcb,
                admitted_price=float(max_acceptable),
                price_move_tolerance=float(tolerance),
                detail=(
                    f"recaptured all-in cost {all_in_cost:.6f} exceeds "
                    f"max_acceptable_price {inputs.max_acceptable_price} + bounded "
                    f"tolerance {float(tolerance):.6f} (ceiling {float(price_ceiling):.6f}); "
                    f"taker chase bounded (no rest at admitted price)"
                ),
            )

        # Gate 3: edge reversed — utility nonpositive (§7 edge row; §5 abort).
        # A stale forecast is treated as an edge reversal too: utility computed
        # on a no-longer-current distribution is not trustworthy, fail closed.
        if edge_lcb <= 0.0 or not inputs.forecast_still_current:
            # CRITICAL INVARIANT: the bounded price tolerance NEVER admits a
            # negative-edge submit. GATE 3 re-checks the edge on the SAME recaptured
            # all-in cost the tolerance just admitted — a tolerated price move that
            # kills the edge still aborts EDGE_REVERSED (not PRICE_MOVED). The
            # tolerance only governs the PRICE ceiling, never the edge sign.
            return SubmitRecaptureDecision(
                state=CandidateLifecycleState.SUBMIT_ABORTED_EDGE_REVERSED,
                may_submit=False,
                reversal_reason=ReversalReason.EDGE,
                recaptured_all_in_cost=all_in_cost,
                recaptured_edge_lcb=edge_lcb,
                admitted_price=float(max_acceptable),
                detail=(
                    f"recaptured edge_lcb {edge_lcb:.6f} <= 0"
                    if edge_lcb <= 0.0
                    else "forecast no longer current at recapture"
                ),
            )

        # Gate 4: family rank reversed — abort this submit, defer to full re-rank
        # (§5 AbortOrSwitchOnlyAfterFullRerank). The engine NEVER switches inline.
        if inputs.family_rank_reversed:
            return SubmitRecaptureDecision(
                state=CandidateLifecycleState.SUBMIT_ABORTED_FAMILY_REVERSED,
                may_submit=False,
                reversal_reason=ReversalReason.FAMILY_RANK,
                recaptured_all_in_cost=all_in_cost,
                recaptured_edge_lcb=edge_lcb,
                detail=(
                    "family rank reversed at recapture; abort and defer to full "
                    "re-rank (no inline switch)"
                ),
            )

        # All gates passed: the candidate remains primary with positive utility.
        # Record price-move provenance so settlement attribution can measure whether
        # entries that proceeded despite an adverse recapture (maker rested at the
        # admitted price, or taker filled within bounded tolerance) underperform.
        if price_moved_but_admitted:
            _rest = inputs.order_rests_at_admitted_price
            _detail = (
                "recapture clean: primary holds, positive edge; recaptured cost "
                f"{all_in_cost:.6f} worse than admitted "
                f"{float(max_acceptable):.6f} but "
                + (
                    "MAKER rests at admitted price (no chase)"
                    if _rest
                    else f"within bounded taker tolerance {float(tolerance):.6f}"
                )
            )
        else:
            _detail = "recapture clean: primary holds, positive edge, price in band"
        return SubmitRecaptureDecision(
            state=CandidateLifecycleState.READY_TO_SUBMIT,
            may_submit=True,
            reversal_reason=None,
            recaptured_all_in_cost=all_in_cost,
            recaptured_edge_lcb=edge_lcb,
            price_moved_within_tolerance=price_moved_but_admitted,
            admitted_price=float(max_acceptable),
            price_move_tolerance=float(tolerance),
            detail=_detail,
        )

    # ------------------------------------------------------------------
    # WATCH-set re-rank under hysteresis (spec §6 / §7).
    # ------------------------------------------------------------------
    def rank_watch_set(
        self,
        utilities: Mapping[str, float],
        *,
        primary_bin_id: str,
        now_seconds: float,
        last_switch_seconds: float,
        trigger: ReversalReason = ReversalReason.FAMILY_RANK,
    ) -> RankDecision:
        """Re-rank the WATCH set, switching primary ONLY if hysteresis allows.

        ``utilities`` maps bin_id -> robust marginal expected log utility ΔU.
        The challenger is the argmax over non-incumbent bins. A switch occurs iff
        BOTH §7 hysteresis conditions hold::

            ΔU_challenger > ΔU_incumbent + eta_switch
            (now_seconds - last_switch_seconds) > t_no_churn_seconds

        Times are passed explicitly (no wall clock) so the transition is pure and
        replayable. ``trigger`` lets the caller tag WHY the re-rank ran (e.g.
        FORECAST for a forecast update, FAMILY_RANK for a routine rank refresh);
        on an actual switch this becomes the decision's ``reversal_reason``.
        """
        if primary_bin_id not in utilities:
            raise ValueError(
                f"primary_bin_id {primary_bin_id!r} not present in utilities "
                f"{sorted(utilities)}"
            )
        incumbent_u = float(utilities[primary_bin_id])

        # Challenger = best non-incumbent bin by utility.
        challenger_bin: Optional[str] = None
        challenger_u = float("-inf")
        for bin_id, u in utilities.items():
            if bin_id == primary_bin_id:
                continue
            fu = float(u)
            if fu > challenger_u:
                challenger_u = fu
                challenger_bin = bin_id

        if challenger_bin is None:
            # Nothing to switch to.
            return RankDecision(
                primary_bin_id=primary_bin_id,
                switched=False,
                reversal_reason=None,
                detail="no challenger in watch set",
            )

        delta = challenger_u - incumbent_u
        margin_ok = delta > self.hysteresis.eta_switch
        elapsed = now_seconds - last_switch_seconds
        churn_ok = elapsed > self.hysteresis.churn_window_seconds()

        if margin_ok and churn_ok:
            return RankDecision(
                primary_bin_id=challenger_bin,
                switched=True,
                reversal_reason=trigger,
                challenger_bin_id=challenger_bin,
                delta_utility=delta,
                detail=(
                    f"switch: ΔU {delta:.6f} > eta_switch "
                    f"{self.hysteresis.eta_switch} and elapsed {elapsed:.3f}s > "
                    f"t_no_churn {self.hysteresis.churn_window_seconds()}s"
                ),
            )

        # Hysteresis blocked the switch. Report which condition failed.
        if not margin_ok:
            why = (
                f"sub-eta: ΔU {delta:.6f} <= eta_switch "
                f"{self.hysteresis.eta_switch} (noise-level margin)"
            )
        else:
            why = (
                f"in-churn: elapsed {elapsed:.3f}s <= t_no_churn "
                f"{self.hysteresis.churn_window_seconds()}s (anti flip-flop)"
            )
        return RankDecision(
            primary_bin_id=primary_bin_id,
            switched=False,
            reversal_reason=None,
            challenger_bin_id=challenger_bin,
            delta_utility=delta,
            detail=f"hysteresis suppressed switch ({why})",
        )

    # ------------------------------------------------------------------
    # Fallback discipline — Hidden #7 antibody (spec §6 / §9 Hidden #7).
    # ------------------------------------------------------------------
    def promote_fallback_on_primary_abort(
        self,
        fallback: "NativeSideCandidate",
    ) -> FallbackPromotion:
        """A primary abort does NOT promote a fallback to submit (Hidden #7).

        The fallback is WATCH-only. It can become primary ONLY by passing a FULL
        re-rank (fresh book capture + probability validation + FDR validation +
        risk validation + family re-rank, §6). This method makes that structural:
        it always returns WATCH / ``may_submit=False`` / ``requires_full_rerank
        =True``. There is no parameter that flips it to submit — a primary abort
        alone is never sufficient authority.
        """
        return FallbackPromotion(
            candidate=fallback,
            state=CandidateLifecycleState.WATCH,
            may_submit=False,
            requires_full_rerank=True,
            detail=(
                "fallback is WATCH-only (Hidden #7): a primary abort does not "
                "grant submit authority; a full re-rank is required to become "
                "primary"
            ),
        )

    def require_submit_recapture(
        self,
        candidate: "NativeSideCandidate",
        *,
        became_primary_via_rerank: bool,
    ) -> SubmitRecaptureDecision:
        """Move a candidate to SUBMIT_RECAPTURE_REQUIRED — only if it is primary.

        A candidate may enter SUBMIT_RECAPTURE_REQUIRED (the mandatory recapture
        gate) ONLY when it is genuinely primary. A fallback that has NOT passed a
        full re-rank (``became_primary_via_rerank=False``) is rejected with a
        hard error — this is the Hidden #7 antibody at the state-transition
        boundary, complementing :meth:`promote_fallback_on_primary_abort`.

        On success the candidate is staged for recapture (no submission yet —
        the recapture gate :meth:`evaluate_submit_recapture` still runs and can
        still fail closed). ``may_submit`` is False here because reaching the
        recapture-required state is NOT itself submit authorization.
        """
        if not became_primary_via_rerank:
            raise ValueError(
                "cannot require submit recapture for a non-primary (fallback) "
                "candidate without a full re-rank (Hidden #7): a fallback is "
                "WATCH-only until a full re-rank makes it primary"
            )
        return SubmitRecaptureDecision(
            state=CandidateLifecycleState.SUBMIT_RECAPTURE_REQUIRED,
            may_submit=False,
            reversal_reason=None,
            detail=(
                "candidate became primary via full re-rank; staged for mandatory "
                "fresh-at-submit recapture"
            ),
        )
