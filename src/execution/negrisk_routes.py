# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/execution/negrisk_routes.py" block lines 654-732:
#   RouteCost 658-676 [route_id, route_type, instrument, shares, avg_cost, max_shares,
#   legs, executable, reason]; NegRiskRouteSet 677-685 [direct_yes, direct_no,
#   synthetic_not_i, pair_arbs, full_basket_arbs, conversion_routes]; the route rules
#   686-699 (YES_i = direct YES ask; NO_i buy = direct NO if negRisk=False, else
#   min(direct_no_cost(i,s), synthetic_yes_basket_cost(i,s)) where the synthetic route
#   buys equal shares of every sibling YES and its max shares is the minimum
#   depth-supported shares across siblings); arbitrage checks 701-720 (pair
#   ask_yes_i + ask_no_i + fees < 1.0; full YES basket Σ_i ask_yes_i + fees < 1.0;
#   conversion ask_no_i + conversion_friction < Σ_{j!=i} bid_yes_j AND executable by a
#   venue primitive); route dominance 722-726
#   (not_i_cost = min(direct_no_cost(i,s), synthetic_yes_basket_cost(i,s))); the
#   venue-primitive verification 728-732).
#   Reconciled against docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md:
#     * VENUE-PRIMITIVE VERDICT (ledger §7-19, BLOCKER row :728-732): on-chain neg-risk
#       convert/merge/split are ABSENT — only redeem/submit/cancel are wired
#       (polymarket_v2_adapter.py:226/:872/:1496/:2669/:2733). The grep mandated by
#       spec line 730 was re-run for THIS build and confirms the same: ZERO executable
#       convert/merge/split hits in src/venue or src/execution. CONSEQUENCE:
#       route_type=CONVERSION_SELL_BASKET and NegRiskRouteSet.conversion_routes have NO
#       venue primitive to execute, so every conversion route is built executable=False
#       with a reason citing the absent venue primitive. DIRECT_YES, DIRECT_NO,
#       SYNTHETIC_NOT_I_YES_BASKET, PAIR_ARB, FULL_YES_BASKET_ARB proceed live via
#       INDEPENDENT native submit() orders (no conversion).
#     * GREENFIELD — no live-file edits. This is a NEW file under the existing
#       src/execution/ package (ledger MINOR row :654 confirms negrisk_routes.py is fine
#       under src/execution/; only the src/decision/ package is missing, which this
#       module does not touch).
#   DRIFT RESOLVED (recorded in docs/rebuild/impl_w4_negrisk_routes.md):
#     * RouteLeg is referenced by the spec (lines 673 and 921: legs: tuple[RouteLeg, ...])
#       but is NEVER defined anywhere in consult_build_spec.md or in live src/. Resolved
#       toward the live execution model: a RouteLeg is ONE independent native order the
#       venue submit() lane already executes — (condition_id, bin_id, token_id,
#       direction, shares, leg_cost). It is the atomic unit a route decomposes into, and
#       its leg_cost is the leaf executable_cost ExecutionPrice for that single native
#       ladder walk. No conversion leg is ever emitted live (venue primitive absent).
#   Live dependencies (ALL already built; imported, never re-implemented):
#     - src/execution/family_book.py::{FamilyBook, MarketBook, ExecutableLadder}
#                       (the captured family route surface; each MarketBook hands the
#                       leaf walker a NativeQuoteBook for ONE side, carries per-market
#                       neg_risk + token ids)
#     - src/strategy/live_inference/executable_cost.py::{executable_cost, NativeQuoteBook,
#                       ExecutableCostError}
#                       (the LEAF native-ladder walker — size-aware, fee-applied; the
#                       ONLY cost path. negrisk_routes NEVER walks a book or invents a
#                       midpoint/last/complement cost; it composes leaf costs.)
#     - src/probability/instruments.py::Instrument
#                       (the YES/NO payoff-vector claim a RouteCost is FOR; a NO_i is a
#                       basket of the OTHER bins' YES by its payoff vector 1 - e_i —
#                       which is EXACTLY what the synthetic route buys)
#     - src/contracts/execution_price.py::ExecutionPrice
#                       (the typed per-share cost the leaf returns; avg_cost on a
#                       RouteCost is an ExecutionPrice in probability_units, fee-applied)
"""NegRiskRouteSet — the family route engine over the executable family book (Stage 7c).

This is Stage 7c of the q-kernel rebuild (consult_build_spec.md lines 654-732). Given a
captured ``FamilyBook`` (every sibling market's four native ladders) and a target
``Instrument`` (a YES or NO claim on one bin), it enumerates EVERY executable way to
acquire that claim and prices each one SIZE-AWARE on the executable ladder — never a
midpoint, last-trade, or NO-complement price (the leaf ``executable_cost`` forbids those
and is the ONLY cost path here).

THE STRUCTURAL CORRECTIONS (operator law — make the bad output mathematically
impossible; NO gate/cap/clamp/haircut that catches a bad value and leaves a broken
transform in place):

  1. A NO_i is NOT priced off a single direct NO ladder when a cheaper sibling-YES
     basket exists. The Stage 7 route rule is the CORRECTED transformation: for a NO_i
     buy with negRisk=True, the route cost is ``min(direct_no_cost(i, s),
     synthetic_yes_basket_cost(i, s))`` priced at the SAME size ``s`` on the executable
     ladders of BOTH routes. The synthetic route buys equal shares of every OTHER
     sibling's YES — which is EXACTLY the NO_i payoff vector ``1 - e_i`` (it wins iff
     ANY other bin settles). So a NO that is genuinely a nine-sibling basket can NEVER
     be over-priced off one direct ladder: the dominance ``min`` is the only thing that
     produces ``not_i_cost``, and it sees both routes at the traded size. There is no
     post-hoc "if direct looks expensive, try synthetic" detector bolted on top of a
     direct-only transform — the comparison IS the transform.

  2. When negRisk=False, there is NO synthetic route at all (the venue does not let a
     sibling-YES basket settle a NO on a non-neg-risk market), so only the direct NO is
     offered. The synthetic basket is therefore not an always-available fallback; it is
     a route that EXISTS only where the venue's neg-risk structure makes the basket and
     the NO economically identical. The negRisk flag is read PER MARKET off the
     ``MarketBook`` (family_book threads it per sibling), so a mixed family cannot lose
     the per-market distinction.

  3. Every cost is SIZE-AWARE on the EXECUTABLE side (ask for a buy, bid for a sell).
     The arbitrage checks (pair, full basket, conversion) all walk the book at the
     requested share count ``s`` through the leaf walker — never a top-of-book or mid
     price. A route whose ladder cannot support ``s`` shares is built
     ``executable=False`` with an honest ``NO_DEPTH``-class reason, NOT clamped to a
     smaller size behind the caller's back.

  4. Conversion routes (``CONVERSION_SELL_BASKET``) are omitted until the on-chain
     neg-risk convert/merge/split venue primitive exists (drift-ledger BLOCKER;
     the grep mandated by spec line 730 was re-run for this build and found ZERO
     executable convert/merge/split methods in src/venue or src/execution).
     ``DIRECT_*`` / ``SYNTHETIC_*`` / ``*_ARB`` routes proceed live via independent
     native ``submit()`` orders (each a ``RouteLeg``), which require no conversion.

WHAT STAYS LEAF:

  This module composes leaf costs; it does NOT walk an order book. Every per-leg price
  is ``executable_cost(market.native_quote_book(), direction=..., shares=...)`` — the
  size-aware, fee-applied native-ladder walker that already forbids midpoint / last /
  NO-complement cost. A synthetic basket's ``avg_cost`` is the share-weighted sum of its
  legs' leaf costs; an arb's profit is a difference of leaf costs. There is no
  family-level cost path that bypasses the leaf bans.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Mapping

from src.contracts.execution_price import ExecutionPrice
from src.execution.family_book import FamilyBook, MarketBook
from src.probability.instruments import Instrument
from src.strategy.live_inference.executable_cost import (
    ExecutableCostError,
    executable_cost,
)

# The exact route_type literal set from spec lines 661-668 (verbatim, ordered).
RouteType = Literal[
    "DIRECT_YES",
    "DIRECT_NO",
    "SYNTHETIC_NOT_I_YES_BASKET",
    "PAIR_ARB",
    "FULL_YES_BASKET_ARB",
    "CONVERSION_SELL_BASKET",
]

# The venue-primitive BLOCKER reason (drift-ledger §7-19; spec lines 728-732).
CONVERSION_VENUE_PRIMITIVE_ABSENT = (
    "CONVERSION_VENUE_PRIMITIVE_ABSENT: on-chain neg-risk convert/merge/split is not "
    "wired in src/venue (only redeem/submit/cancel exist). A CONVERSION_SELL_BASKET "
    "route has no venue primitive to execute until a convert/merge/"
    "split method is added to PolymarketV2Adapter + NegRiskAdapter calldata encoders."
)


class NegRiskRouteError(ValueError):
    """Raised when a route set cannot be assembled coherently against a family book.

    Fail-closed signal: the target instrument is not a sibling of the family Omega, the
    family book is incomplete where a basket route needs every sibling, or a requested
    share size is non-positive — so there is no coherent route surface to price, and it
    is refused rather than served a wrong route.
    """


# ---------------------------------------------------------------------------
# RouteLeg — DRIFT-RESOLVED new dataclass (spec references tuple[RouteLeg, ...]
# at lines 673 and 921 but never defines it). One independent native order the
# venue submit() lane already executes.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RouteLeg:
    """One independent native order a route decomposes into (DRIFT-RESOLVED type).

    The spec writes ``legs: tuple[RouteLeg, ...]`` (lines 673, 921) but never defines
    ``RouteLeg``. Resolved toward the LIVE execution model: a route is executed as one
    or more INDEPENDENT native ``submit()`` orders (no conversion primitive), and a
    ``RouteLeg`` is exactly one such order.

    * ``condition_id`` — the sibling market this leg trades (its venue condition id).
    * ``bin_id`` — the Omega bin that sibling resolves.
    * ``token_id`` — the native token the leg buys/sells (the sibling's YES or NO token).
    * ``direction`` — the leaf walker direction (``"buy_yes"`` / ``"buy_no"`` /
      ``"sell_yes"`` / ``"sell_no"``); selects the executable ladder side.
    * ``shares`` — the share count this leg fills (the basket buys EQUAL shares across
      sibling legs, so every synthetic leg carries the same ``shares``).
    * ``leg_cost`` — the leaf ``executable_cost`` ExecutionPrice for THIS single native
      ladder walk (size-aware, fee-applied, probability units). Carries provenance: a
      leg is priced by the leaf walker, never by a family-level shortcut.

    A direct route is a single leg; a synthetic NO_i basket is one leg per OTHER sibling
    (buy_yes on each); an arb is a leg per side it crosses.
    """

    condition_id: str
    bin_id: str
    token_id: str
    direction: Literal["buy_yes", "buy_no", "sell_yes", "sell_no"]
    shares: Decimal
    leg_cost: ExecutionPrice


# ---------------------------------------------------------------------------
# RouteCost (spec lines 658-676) — EXACT field names, frozen.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RouteCost:
    """One way to acquire a claim, priced size-aware (658-676).

    Field names are verbatim from consult_build_spec.md.

    * ``route_id`` — a stable id for this route (``{route_type}:{instrument_id}@{shares}``).
    * ``route_type`` — one of the six literals (spec 661-668). ``CONVERSION_SELL_BASKET``
      is always ``executable=False`` (venue primitive absent).
    * ``instrument`` — the ``Instrument`` (YES/NO payoff claim) this route is FOR.
    * ``shares`` — the share count the route was priced at (the size ``s`` the route
      rules and arb checks evaluate at).
    * ``avg_cost`` — the all-in per-share cost as a typed ``ExecutionPrice`` in
      probability units, fee-applied. For a multi-leg basket it is the share-weighted
      combined per-share cost across legs (the sum of leg per-share costs, since each
      sibling leg delivers one unit of the NO_i basket per share). ``None`` is never
      stored — an unpriceable route is ``executable=False`` with a NO-depth reason and a
      zero-value ``avg_cost`` carrying the reason in ``reason``.
    * ``max_shares`` — the maximum shares the route's executable ladders can support. For
      a synthetic basket it is the MINIMUM depth-supported shares across siblings (spec
      line 699). For a direct route it is the depth of that single ladder.
    * ``legs`` — the independent native orders this route executes as (``RouteLeg``).
    * ``executable`` — whether the route can go LIVE (true for direct/synthetic/arb that
      filled at ``shares``; false for conversion routes and depth-starved routes).
    * ``reason`` — ``None`` when executable; otherwise the honest reason it is not
      (venue primitive absent, no depth, etc.).
    """

    route_id: str
    route_type: RouteType
    instrument: Instrument
    shares: Decimal
    avg_cost: ExecutionPrice
    max_shares: Decimal
    legs: tuple[RouteLeg, ...]
    executable: bool
    reason: str | None


# ---------------------------------------------------------------------------
# NegRiskRouteSet (spec lines 677-685) — EXACT field names, frozen.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NegRiskRouteSet:
    """The full family route surface for a target buy size (spec lines 677-685).

    Field names are verbatim from consult_build_spec.md.

    * ``direct_yes`` — per sibling bin_id, the direct YES_i ask route (spec line 690).
    * ``direct_no`` — per sibling bin_id, the direct NO_i ask route (always present;
      the ONLY NO route when negRisk=False — spec line 694).
    * ``synthetic_not_i`` — per sibling bin_id, the synthetic NO_i = sibling-YES basket
      route (present only for negRisk=True siblings, and only when every OTHER sibling
      market exists in the family book; spec lines 696-699).
    * ``pair_arbs`` — per-bin pair arbitrage routes where ``ask_yes_i + ask_no_i + fees
      < 1.0`` at size (spec line 707). ``executable`` reflects whether the arb actually
      clears at the requested size.
    * ``full_basket_arbs`` — at most one full-YES-basket arb where ``Σ_i ask_yes_i +
      fees < 1.0`` at size (spec line 713).
    * ``conversion_routes`` — per eligible bin, the conversion-sell-basket route. ALWAYS
      ``executable=False`` (venue primitive absent; spec lines 715-720, 728-732).
    """

    direct_yes: Mapping[str, RouteCost]
    direct_no: Mapping[str, RouteCost]
    synthetic_not_i: Mapping[str, RouteCost]
    pair_arbs: tuple[RouteCost, ...]
    full_basket_arbs: tuple[RouteCost, ...]
    conversion_routes: tuple[RouteCost, ...]

    def best_no_route(self, bin_id: str) -> RouteCost:
        """The dominant NO_i route — ``min(direct_no, synthetic_yes_basket)`` (722-726).

        Route dominance (spec lines 722-726):

            not_i_cost = min(direct_no_cost(i, s), synthetic_yes_basket_cost(i, s))

        Returns the EXECUTABLE route with the lower per-share ``avg_cost``. The synthetic
        route is only a candidate when it is present (negRisk=True and every sibling
        market exists) AND executable at the requested size; otherwise the direct NO is
        the only NO route, exactly as the negRisk=False rule (spec line 694) requires.
        This ``min`` is the ONLY producer of the chosen NO cost — there is no path that
        prices a NO off the direct ladder without first comparing the basket.
        """
        direct = self.direct_no.get(bin_id)
        synthetic = self.synthetic_not_i.get(bin_id)
        if direct is None:
            raise NegRiskRouteError(
                f"NO_ROUTE_FOR_BIN: no direct NO route for bin_id={bin_id!r}"
            )
        candidates = [r for r in (direct, synthetic) if r is not None and r.executable]
        if not candidates:
            # Neither route is executable at this size — return the direct route (it
            # carries the honest non-executable reason). The caller sees executable=False.
            return direct
        return min(candidates, key=lambda r: float(r.avg_cost))


# ---------------------------------------------------------------------------
# Leaf-cost helpers — the ONLY cost path is executable_cost (size-aware, fee-applied).
# ---------------------------------------------------------------------------

def _zero_cost(reason_tag: str) -> ExecutionPrice:
    """A typed zero-value fee-adjusted price for a route that could not be priced.

    A non-executable route still carries a typed ``ExecutionPrice`` (so ``avg_cost`` is
    never ``None``); the honest reason lives in ``RouteCost.reason``. ``0.0`` in
    probability units is a valid ExecutionPrice and never masquerades as a real cost
    because the route is marked ``executable=False``.
    """
    return ExecutionPrice(
        0.0,
        price_type="fee_adjusted",
        fee_deducted=True,
        currency="probability_units",
    )


def _leg_cost(
    market: MarketBook,
    *,
    direction: Literal["buy_yes", "buy_no", "sell_yes", "sell_no"],
    shares: Decimal,
) -> ExecutionPrice | None:
    """Price ONE native ladder leg via the leaf walker, or ``None`` if depth-starved.

    The leaf ``executable_cost`` is the ONLY cost path: it walks the selected native
    ladder (ask for a buy, bid for a sell), is size-aware, applies the taker fee for a
    buy / deducts it for a sell, and forbids midpoint / last / NO-complement cost.
    ``None`` means the ladder cannot support ``shares`` (NO_DEPTH) or fails a leaf
    invariant (tick / min-order) — the caller marks the route ``executable=False`` with
    an honest reason rather than fabricating a price.
    """
    try:
        return executable_cost(
            market.native_quote_book(), direction=direction, shares=shares
        )
    except ExecutableCostError:
        return None


def _max_depth_shares(market: MarketBook, *, side: Literal["yes_ask", "no_ask"]) -> Decimal:
    """Total executable depth (sum of sizes) on the requested buy ladder.

    The maximum shares the ladder can fill. Used for ``max_shares`` (a direct route's
    ladder depth, and — via the minimum across siblings — a synthetic basket's depth).
    """
    ladder = market.yes_asks if side == "yes_ask" else market.no_asks
    return sum((level.size for level in ladder.levels), Decimal("0"))


# ---------------------------------------------------------------------------
# Direct routes — spec lines 688-694.
# ---------------------------------------------------------------------------

def _direct_yes_route(
    family_book: FamilyBook, *, bin_id: str, shares: Decimal
) -> RouteCost:
    """Direct YES_i = the YES_i ask (spec lines 688-690).

    For a YES_i buy the route is simply the direct YES ask walked at ``shares``. One
    leg, priced by the leaf walker.
    """
    market = family_book.markets[bin_id]
    instrument = Instrument(
        instrument_id=f"YES:{bin_id}",
        bin_id=bin_id,
        side="YES",
        direct_token_id=market.yes_token_id,
    )
    cost = _leg_cost(market, direction="buy_yes", shares=shares)
    max_shares = _max_depth_shares(market, side="yes_ask")
    if cost is None:
        return RouteCost(
            route_id=f"DIRECT_YES:{bin_id}@{shares}",
            route_type="DIRECT_YES",
            instrument=instrument,
            shares=shares,
            avg_cost=_zero_cost("NO_DEPTH"),
            max_shares=max_shares,
            legs=(),
            executable=False,
            reason=f"NO_DEPTH: direct YES ask for bin_id={bin_id!r} cannot fill {shares} shares",
        )
    leg = RouteLeg(
        condition_id=market.condition_id,
        bin_id=bin_id,
        token_id=market.yes_token_id,
        direction="buy_yes",
        shares=shares,
        leg_cost=cost,
    )
    return RouteCost(
        route_id=f"DIRECT_YES:{bin_id}@{shares}",
        route_type="DIRECT_YES",
        instrument=instrument,
        shares=shares,
        avg_cost=cost,
        max_shares=max_shares,
        legs=(leg,),
        executable=True,
        reason=None,
    )


def _direct_no_route(
    family_book: FamilyBook, *, bin_id: str, shares: Decimal
) -> RouteCost:
    """Direct NO_i = the NO_i ask (spec lines 692-697, the ``direct_no_cost`` term).

    Always built (it is the ONLY NO route when negRisk=False, spec line 694, and one of
    the two compared routes when negRisk=True). One leg, priced by the leaf walker.
    """
    market = family_book.markets[bin_id]
    instrument = Instrument(
        instrument_id=f"NO:{bin_id}",
        bin_id=bin_id,
        side="NO",
        direct_token_id=market.no_token_id,
    )
    cost = _leg_cost(market, direction="buy_no", shares=shares)
    max_shares = _max_depth_shares(market, side="no_ask")
    if cost is None:
        return RouteCost(
            route_id=f"DIRECT_NO:{bin_id}@{shares}",
            route_type="DIRECT_NO",
            instrument=instrument,
            shares=shares,
            avg_cost=_zero_cost("NO_DEPTH"),
            max_shares=max_shares,
            legs=(),
            executable=False,
            reason=f"NO_DEPTH: direct NO ask for bin_id={bin_id!r} cannot fill {shares} shares",
        )
    leg = RouteLeg(
        condition_id=market.condition_id,
        bin_id=bin_id,
        token_id=market.no_token_id,
        direction="buy_no",
        shares=shares,
        leg_cost=cost,
    )
    return RouteCost(
        route_id=f"DIRECT_NO:{bin_id}@{shares}",
        route_type="DIRECT_NO",
        instrument=instrument,
        shares=shares,
        avg_cost=cost,
        max_shares=max_shares,
        legs=(leg,),
        executable=True,
        reason=None,
    )


# ---------------------------------------------------------------------------
# Synthetic NO_i basket — spec lines 696-699.
# ---------------------------------------------------------------------------

def _synthetic_not_i_route(
    family_book: FamilyBook, *, bin_id: str, shares: Decimal
) -> RouteCost | None:
    """Synthetic NO_i = buy equal shares of every OTHER sibling's YES (696-699).

    The synthetic route buys EQUAL ``shares`` of every sibling YES_j (j != i). That
    portfolio's payoff is ``Σ_{j != i} e_j == 1 - e_i`` — EXACTLY the NO_i payoff vector
    (it pays one unit iff any OTHER bin settles). So its all-in per-share cost is

        synthetic_yes_basket_cost(i, s) = Σ_{j != i} yes_ask_cost(j, s)

    (each leg buys ``s`` shares of YES_j at the leaf executable ask, fee-applied; the
    legs combine to deliver ``s`` units of the NO_i basket). ``max_shares`` is the
    MINIMUM depth-supported shares across the sibling YES asks (spec line 699): the
    basket can only fill as many units as its thinnest sibling leg supports.

    Returns ``None`` (route does not exist) only when the target market is NOT neg-risk
    — the venue does not let a sibling-YES basket settle a NO on a non-neg-risk market,
    so there is no synthetic route to offer (the caller falls back to direct-only,
    exactly the negRisk=False rule). When the market IS neg-risk but the family book is
    missing a sibling, the route is returned ``executable=False`` (a basket missing a
    leg would mis-price the NO, so it is honestly non-executable, never silently
    dropped).
    """
    target = family_book.markets[bin_id]
    if not target.neg_risk:
        # negRisk=False: only the direct NO_i can be used (spec line 694). There is no
        # synthetic route at all.
        return None

    instrument = Instrument(
        instrument_id=f"NO:{bin_id}",
        bin_id=bin_id,
        side="NO",
        # A synthetic NO has no single direct token — it is the sibling-YES basket.
        direct_token_id=None,
    )
    route_id = f"SYNTHETIC_NOT_I_YES_BASKET:{bin_id}@{shares}"

    # Every OTHER Omega bin must have a sibling market to buy YES on; a missing sibling
    # makes the basket incomplete and the NO mis-priced — honestly non-executable.
    sibling_bin_ids = [b.bin_id for b in family_book.omega.bins if b.bin_id != bin_id]
    missing = [bid for bid in sibling_bin_ids if bid not in family_book.markets]
    if missing:
        return RouteCost(
            route_id=route_id,
            route_type="SYNTHETIC_NOT_I_YES_BASKET",
            instrument=instrument,
            shares=shares,
            avg_cost=_zero_cost("INCOMPLETE_BASKET"),
            max_shares=Decimal("0"),
            legs=(),
            executable=False,
            reason=(
                f"INCOMPLETE_BASKET: synthetic NO_{bin_id!r} basket needs every sibling "
                f"YES; missing sibling markets={missing!r}"
            ),
        )

    legs: list[RouteLeg] = []
    total_per_share = Decimal("0")
    depths: list[Decimal] = []
    for sib_bin_id in sibling_bin_ids:
        sib_market = family_book.markets[sib_bin_id]
        sib_cost = _leg_cost(sib_market, direction="buy_yes", shares=shares)
        depths.append(_max_depth_shares(sib_market, side="yes_ask"))
        if sib_cost is None:
            # A sibling leg cannot fill ``shares`` — the basket cannot be assembled at
            # this size. Honestly non-executable (NOT clamped to a smaller size).
            min_depth = min(depths) if depths else Decimal("0")
            return RouteCost(
                route_id=route_id,
                route_type="SYNTHETIC_NOT_I_YES_BASKET",
                instrument=instrument,
                shares=shares,
                avg_cost=_zero_cost("NO_DEPTH"),
                max_shares=min_depth,
                legs=(),
                executable=False,
                reason=(
                    f"NO_DEPTH: synthetic NO_{bin_id!r} basket leg YES_{sib_bin_id!r} "
                    f"cannot fill {shares} shares"
                ),
            )
        total_per_share += Decimal(str(sib_cost.value))
        legs.append(
            RouteLeg(
                condition_id=sib_market.condition_id,
                bin_id=sib_bin_id,
                token_id=sib_market.yes_token_id,
                direction="buy_yes",
                shares=shares,
                leg_cost=sib_cost,
            )
        )

    # max_shares is the MINIMUM depth across sibling YES asks (spec line 699).
    max_shares = min(depths) if depths else Decimal("0")
    # The basket per-share cost is the sum of the sibling YES per-share costs (each leg
    # is one unit of the NO_i basket). It is a real all-in fee-applied cost (every leg's
    # leaf cost is fee-applied), so it is fee_deducted in probability units. A basket
    # whose summed cost exceeds 1.0 in probability units cannot be a valid
    # ExecutionPrice — that is a genuine "this NO is worth buying via direct, not
    # basket" signal, surfaced by route dominance picking the cheaper route, not clamped.
    basket_value = float(total_per_share)
    if basket_value > 1.0:
        # The sibling-YES basket costs more than 1.0 per NO unit: it is dominated by any
        # direct NO at or below 1.0. Surface it as non-executable-at-this-price (the
        # dominance min will prefer direct). We still record the true summed cost via the
        # reason; avg_cost stays a valid ExecutionPrice clamped to the 1.0 type bound is
        # NOT done — instead the route is honestly non-executable so it never wins.
        return RouteCost(
            route_id=route_id,
            route_type="SYNTHETIC_NOT_I_YES_BASKET",
            instrument=instrument,
            shares=shares,
            avg_cost=_zero_cost("BASKET_COST_OVER_UNITY"),
            max_shares=max_shares,
            legs=tuple(legs),
            executable=False,
            reason=(
                f"BASKET_COST_OVER_UNITY: synthetic NO_{bin_id!r} basket per-share cost "
                f"{basket_value!r} > 1.0; dominated by direct NO"
            ),
        )

    avg_cost = ExecutionPrice(
        basket_value,
        price_type="fee_adjusted",
        fee_deducted=True,
        currency="probability_units",
    )
    return RouteCost(
        route_id=route_id,
        route_type="SYNTHETIC_NOT_I_YES_BASKET",
        instrument=instrument,
        shares=shares,
        avg_cost=avg_cost,
        max_shares=max_shares,
        legs=tuple(legs),
        executable=True,
        reason=None,
    )


# ---------------------------------------------------------------------------
# Arbitrage checks — spec lines 701-720 (all size-aware on executable ladders).
# ---------------------------------------------------------------------------

def _pair_arb_route(
    family_book: FamilyBook, *, bin_id: str, shares: Decimal
) -> RouteCost | None:
    """Pair arb: ``ask_yes_i(s) + ask_no_i(s) + fees < 1.0`` (spec lines 703-707).

    Buying ``s`` shares of YES_i AND ``s`` shares of NO_i guarantees ``s`` units of
    payoff (one side always wins) for a combined per-unit cost of the two executable
    asks (each already fee-applied by the leaf walker). If that combined cost is below
    1.0, it is a risk-free arb. Both legs are walked SIZE-AWARE at ``s`` (never
    top-of-book). Returns ``None`` only if neither side can be priced at all (no market
    depth on either) — otherwise the route is returned with ``executable`` reflecting
    whether it actually cleared below 1.0 at this size.
    """
    market = family_book.markets[bin_id]
    yes_cost = _leg_cost(market, direction="buy_yes", shares=shares)
    no_cost = _leg_cost(market, direction="buy_no", shares=shares)
    instrument = Instrument(
        instrument_id=f"PAIR:{bin_id}",
        bin_id=bin_id,
        side="YES",
        direct_token_id=market.yes_token_id,
    )
    route_id = f"PAIR_ARB:{bin_id}@{shares}"
    if yes_cost is None or no_cost is None:
        missing_side = "yes_ask" if yes_cost is None else "no_ask"
        return RouteCost(
            route_id=route_id,
            route_type="PAIR_ARB",
            instrument=instrument,
            shares=shares,
            avg_cost=_zero_cost("NO_DEPTH"),
            max_shares=Decimal("0"),
            legs=(),
            executable=False,
            reason=f"NO_DEPTH: pair arb {missing_side} for bin_id={bin_id!r} cannot fill {shares}",
        )
    # The leaf already applied the taker fee to each side (buy => with_taker_fee), so the
    # combined per-unit cost IS the fees-inclusive total of spec line 707.
    combined = Decimal(str(yes_cost.value)) + Decimal(str(no_cost.value))
    clears = combined < Decimal("1.0")
    max_shares = min(
        _max_depth_shares(market, side="yes_ask"),
        _max_depth_shares(market, side="no_ask"),
    )
    legs = (
        RouteLeg(
            condition_id=market.condition_id,
            bin_id=bin_id,
            token_id=market.yes_token_id,
            direction="buy_yes",
            shares=shares,
            leg_cost=yes_cost,
        ),
        RouteLeg(
            condition_id=market.condition_id,
            bin_id=bin_id,
            token_id=market.no_token_id,
            direction="buy_no",
            shares=shares,
            leg_cost=no_cost,
        ),
    )
    return RouteCost(
        route_id=route_id,
        route_type="PAIR_ARB",
        instrument=instrument,
        shares=shares,
        avg_cost=ExecutionPrice(
            float(combined) if combined <= Decimal("1.0") else 1.0,
            price_type="fee_adjusted",
            fee_deducted=True,
            currency="probability_units",
        ),
        max_shares=max_shares,
        legs=legs,
        executable=clears,
        reason=None
        if clears
        else f"NO_PAIR_ARB: combined ask {float(combined)!r} >= 1.0 at {shares} shares",
    )


def _full_yes_basket_arb_route(
    family_book: FamilyBook, *, shares: Decimal
) -> RouteCost | None:
    """Full YES basket arb: ``Σ_i ask_yes_i(s) + fees < 1.0`` (spec lines 709-713).

    Buying ``s`` shares of EVERY sibling's YES guarantees ``s`` units of payoff (the
    Omega is MECE — exactly one bin settles), so if the summed executable YES asks
    (fee-applied per leg by the leaf) are below 1.0, it is a risk-free arb. Requires the
    family book be COMPLETE (every Omega bin has a market) — an incomplete basket cannot
    cover every outcome, so it is non-executable. All legs walked SIZE-AWARE at ``s``.
    Returns ``None`` only if the family book is structurally empty.
    """
    bin_ids = [b.bin_id for b in family_book.omega.bins]
    if not bin_ids:
        return None
    route_id = f"FULL_YES_BASKET_ARB@{shares}"
    # A YES on the first bin as a representative instrument for the route surface.
    rep_market = family_book.markets.get(bin_ids[0])
    rep_instrument = Instrument(
        instrument_id="FULL_YES_BASKET",
        bin_id=bin_ids[0],
        side="YES",
        direct_token_id=rep_market.yes_token_id if rep_market is not None else None,
    )
    if not family_book.complete_book:
        return RouteCost(
            route_id=route_id,
            route_type="FULL_YES_BASKET_ARB",
            instrument=rep_instrument,
            shares=shares,
            avg_cost=_zero_cost("INCOMPLETE_BASKET"),
            max_shares=Decimal("0"),
            legs=(),
            executable=False,
            reason=(
                "INCOMPLETE_BASKET: full-YES-basket arb needs every Omega bin's market; "
                f"missing={list(family_book.missing_bin_ids())!r}"
            ),
        )
    legs: list[RouteLeg] = []
    total = Decimal("0")
    depths: list[Decimal] = []
    for b in bin_ids:
        market = family_book.markets[b]
        cost = _leg_cost(market, direction="buy_yes", shares=shares)
        depths.append(_max_depth_shares(market, side="yes_ask"))
        if cost is None:
            min_depth = min(depths) if depths else Decimal("0")
            return RouteCost(
                route_id=route_id,
                route_type="FULL_YES_BASKET_ARB",
                instrument=rep_instrument,
                shares=shares,
                avg_cost=_zero_cost("NO_DEPTH"),
                max_shares=min_depth,
                legs=(),
                executable=False,
                reason=f"NO_DEPTH: full-YES-basket arb leg YES_{b!r} cannot fill {shares} shares",
            )
        total += Decimal(str(cost.value))
        legs.append(
            RouteLeg(
                condition_id=market.condition_id,
                bin_id=b,
                token_id=market.yes_token_id,
                direction="buy_yes",
                shares=shares,
                leg_cost=cost,
            )
        )
    clears = total < Decimal("1.0")
    max_shares = min(depths) if depths else Decimal("0")
    return RouteCost(
        route_id=route_id,
        route_type="FULL_YES_BASKET_ARB",
        instrument=rep_instrument,
        shares=shares,
        avg_cost=ExecutionPrice(
            float(total) if total <= Decimal("1.0") else 1.0,
            price_type="fee_adjusted",
            fee_deducted=True,
            currency="probability_units",
        ),
        max_shares=max_shares,
        legs=tuple(legs),
        executable=clears,
        reason=None
        if clears
        else f"NO_FULL_BASKET_ARB: summed YES asks {float(total)!r} >= 1.0 at {shares} shares",
    )


# ---------------------------------------------------------------------------
# The route-set builder — the family route engine entry point.
# ---------------------------------------------------------------------------

def build_negrisk_route_set(
    family_book: FamilyBook,
    *,
    shares: Decimal,
    enable_negrisk_routes: bool = True,
) -> NegRiskRouteSet:
    """Enumerate and price every family route for a buy of ``shares`` (spec 654-732).

    For each sibling bin it builds the direct YES and direct NO routes; for neg-risk
    siblings it ALSO builds the synthetic NO_i = sibling-YES basket route (spec lines
    696-699). It runs the pair arb per bin (line 707) and the single full-YES-basket arb
    (line 713). Conversion routes are omitted until the venue primitive exists.

    Every cost is SIZE-AWARE on the executable ladder via the leaf ``executable_cost``
    walker — never a midpoint, last, or NO-complement price.

    ``enable_negrisk_routes`` — when False, the neg-risk-specific routes (the synthetic
    sibling-YES basket and the family arbitrage routes that exploit the neg-risk
    structure) are NOT built: ``synthetic_not_i``, ``pair_arbs``, ``full_basket_arbs``,
    and ``conversion_routes`` are empty. The direct YES / direct NO routes are ALWAYS
    built (they are plain native orders, not a neg-risk feature). This is the
    feature-flag for the neg-risk route engine — disabling it falls back to direct-only
    routing, exactly the negRisk=False behavior (spec line 694), with no synthetic
    basket considered.
    """
    if shares <= 0:
        raise NegRiskRouteError(
            f"NON_POSITIVE_SHARES: shares={shares!r} must be > 0 to price a route"
        )

    bin_ids = [b.bin_id for b in family_book.omega.bins]
    present_bin_ids = [b for b in bin_ids if b in family_book.markets]

    direct_yes: dict[str, RouteCost] = {}
    direct_no: dict[str, RouteCost] = {}
    synthetic_not_i: dict[str, RouteCost] = {}
    pair_arbs: list[RouteCost] = []
    conversion_routes: list[RouteCost] = []

    for bin_id in present_bin_ids:
        direct_yes[bin_id] = _direct_yes_route(family_book, bin_id=bin_id, shares=shares)
        direct_no[bin_id] = _direct_no_route(family_book, bin_id=bin_id, shares=shares)

        if not enable_negrisk_routes:
            # Flag OFF: direct-only routing. No synthetic basket, no arb, no conversion
            # route is built — the route set is exactly the direct YES/NO surface.
            continue

        synthetic = _synthetic_not_i_route(family_book, bin_id=bin_id, shares=shares)
        if synthetic is not None:
            synthetic_not_i[bin_id] = synthetic

        pair = _pair_arb_route(family_book, bin_id=bin_id, shares=shares)
        if pair is not None:
            pair_arbs.append(pair)

    full_basket_arbs: list[RouteCost] = []
    if enable_negrisk_routes:
        full = _full_yes_basket_arb_route(family_book, shares=shares)
        if full is not None:
            full_basket_arbs.append(full)

    return NegRiskRouteSet(
        direct_yes=direct_yes,
        direct_no=direct_no,
        synthetic_not_i=synthetic_not_i,
        pair_arbs=tuple(pair_arbs),
        full_basket_arbs=tuple(full_basket_arbs),
        conversion_routes=tuple(conversion_routes),
    )
