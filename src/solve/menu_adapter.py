# Created: 2026-07-03
# Last reused or audited: 2026-07-03
# Authority basis: architecture doc §1 SOLVE row (menu from negrisk_routes, REUSE-grade);
#   W3.MATH brief (RouteCost/NegRiskRouteSet field inventory, conversion_routes stays ());
#   CONSULT REV-2 rulings 2026-07-03 (typed atom payoff projector; per-leg tick/min-size;
#   maker lane disabled for W3 taker-only).
"""Menu adapter — NegRiskRouteSet (+ holdings + cash lane) → SolveMenu.

``negrisk_routes.build_negrisk_route_set`` already enumerates direct/synthetic/pair/
full-basket routes size-aware on executable ladders, marking depth-starved legs
``executable=False`` (never clamping). This adapter is a PURE reshaping layer: it must not
re-price, must not drop non-executable items (audit trail), and must not invent conversion
routes — ``conversion_routes`` is unconditionally ``()`` today because no conversion-route
BUILDER exists in negrisk_routes. Flipping conversions executable = writing that builder
(packet §5), NOT this adapter silently synthesizing routes.

NET-PAYOFF DERIVATION (generalizes payoff_vector's per-candidate ``g_y``): every route is FOR
one ``Instrument`` whose ``payoff_vector(omega)`` is the structural Arrow-Debreu claim (``e_i``
for YES, ``1 - e_i`` for NO), aligned 1:1 with ``omega.bins``. One unit costs ``avg_cost`` in
EVERY outcome, so the after-cost unit payoff is ``payoff_vector - avg_cost`` projected onto the
joint outcome atoms (single-family: atom_id = ``family=bin``). The result is a typed
``AtomPayoffProjector`` (consult REV-2: not a bare ``Mapping[str, float]``).

PER-LEG TICK/MIN (consult REV-2): tick/min-size are carried PER MenuItem from the route's own
market ladder (a heterogeneous multi-leg menu cannot share one tick/min); discrete repair
rounds each order on its own grid.

SELL LANE: selling one held YES unit on bin ``b`` gives up the ``e_b`` claim and receives the
bid proceeds in cash (every atom), so its unit payoff is ``proceeds - e_b``. Proceeds are
floored at the WORST resting bid level (a conservative bid-depth floor), pending the size-aware
bid walk that lands with the exits/W_a sub-slice.

MAKER LANE DISABLED (consult REV-2 ruling 6): W3 is taker-only; a maker quote is a contingent
asset (fill-state/latency/cancel-risk) the current MenuItem cannot value, so no maker item is
emitted regardless of ``include_maker_lane``.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import TYPE_CHECKING, Mapping, Optional

from src.solve.types import (
    AtomPayoffProjector,
    JointOutcomeAtom,
    MenuItem,
    SolveMenu,
    WealthStateByAtom,
)

if TYPE_CHECKING:
    from src.execution.family_book import FamilyBook
    from src.execution.negrisk_routes import NegRiskRouteSet, RouteCost

_DEFAULT_TICK = Decimal("0.01")
_DEFAULT_MIN_ORDER = Decimal("0.01")


def _ladder_tick_min(ladder) -> tuple[Decimal, Decimal]:
    if ladder is None:
        return _DEFAULT_TICK, _DEFAULT_MIN_ORDER
    return Decimal(ladder.min_tick_size), Decimal(ladder.min_order_size)


def _route_menu_item(
    route: "RouteCost", *, family_key: str, bin_ids: tuple[str, ...], omega, markets
) -> MenuItem:
    """One RouteCost → one MenuItem (net after-cost unit payoff over the joint atoms)."""
    instrument = route.instrument
    payoff_vec = instrument.payoff_vector(omega)
    cost = float(route.avg_cost.value)
    payoff_by_atom = {
        JointOutcomeAtom.canonical_id({family_key: bin_ids[j]}): float(payoff_vec[j]) - cost
        for j in range(len(bin_ids))
    }
    kind = "buy_yes" if instrument.side == "YES" else "buy_no"
    market = markets.get(instrument.bin_id)
    tick, min_order = _ladder_tick_min(getattr(market, "yes_asks", None))
    return MenuItem(
        item_id=route.route_id,
        kind=kind,  # type: ignore[arg-type]
        family_key=family_key,
        bin_id=instrument.bin_id,
        route=route,
        executable=bool(route.executable),
        non_executable_reason=route.reason,
        unit_payoff=AtomPayoffProjector(payoff_by_atom_id=payoff_by_atom, unit_cost_usd=cost),
        max_units=Decimal(route.max_shares),
        min_tick_size=tick,
        min_order_size=min_order,
    )


def _sell_holding_item(
    *, family_key: str, bin_id: str, held_shares: Decimal, bin_ids: tuple[str, ...], market
) -> MenuItem:
    """A held YES claim on ``bin_id`` → a sell_holding item along conservative bid depth."""
    bids = tuple(getattr(getattr(market, "yes_bids", None), "levels", ()) or ())
    if bids:
        proceeds = float(bids[-1].price)  # worst resting bid = conservative proceeds floor
        depth = sum(float(lvl.size) for lvl in bids)
        max_units = min(float(held_shares), depth)
        executable = max_units > 0.0
        reason = None if executable else "NO_BID_DEPTH"
    else:
        proceeds = 0.0
        max_units = 0.0
        executable = False
        reason = "NO_BID_DEPTH"
    held_atom = JointOutcomeAtom.canonical_id({family_key: bin_id})
    payoff_by_atom = {
        JointOutcomeAtom.canonical_id({family_key: b}): proceeds for b in bin_ids
    }
    payoff_by_atom[held_atom] = proceeds - 1.0  # the YES claim on bin_id is surrendered
    tick, min_order = _ladder_tick_min(getattr(market, "yes_bids", None))
    return MenuItem(
        item_id=f"sell_holding:{bin_id}",
        kind="sell_holding",
        family_key=family_key,
        bin_id=bin_id,
        route=None,
        executable=executable,
        non_executable_reason=reason,
        unit_payoff=AtomPayoffProjector(payoff_by_atom_id=payoff_by_atom, unit_cost_usd=-proceeds),
        max_units=Decimal(str(max_units)),
        min_tick_size=tick,
        min_order_size=min_order,
    )


def _menu_hash(family_key: str, items: tuple[MenuItem, ...]) -> str:
    digest = hashlib.sha256()
    digest.update(family_key.encode())
    for it in items:
        digest.update(it.item_id.encode())
        digest.update(it.kind.encode())
        digest.update(str(it.executable).encode())
        digest.update(str(it.max_units).encode())
        digest.update(str(it.min_tick_size).encode())
        digest.update(str(it.min_order_size).encode())
        proj = it.unit_payoff
        digest.update(repr(round(proj.unit_cost_usd, 12)).encode())
        for a in sorted(proj.payoff_by_atom_id):
            digest.update(a.encode())
            digest.update(repr(round(proj.payoff_by_atom_id[a], 12)).encode())
        digest.update(b"\x1f")
    return digest.hexdigest()


def build_solve_menu(
    route_set: "NegRiskRouteSet",
    *,
    family_key: str,
    family_book: "FamilyBook",
    holdings_by_bin_id: Mapping[str, Decimal],
    wealth: Optional[WealthStateByAtom] = None,
    include_maker_lane: bool = False,
) -> SolveMenu:
    """Adapt the priced route menu into the solver's SolveMenu.

    Contract (math core / sub-slice 2 implements):
    * every RouteCost in direct_yes/direct_no/synthetic_not_i/pair_arbs/full_basket_arbs
      becomes one MenuItem with a typed atom payoff projector (payoff_vector − avg_cost);
    * held positions add sell_holding items along BID depth, max_units = held shares;
    * conversion_routes pass through as-is (empty today; items appear when the builder lands,
      still executable=False until W2.4 dry-run gate clears);
    * maker lane stays DISABLED (W3 taker-only, consult REV-2 ruling 6);
    * hold_cash is always the last item (max_units = spendable);
    * per-item tick/min-size come from the route's own market ladder;
    * menu_hash is deterministic over item identity + pricing inputs.
    """
    omega = family_book.omega
    bin_ids = tuple(b.bin_id for b in omega.bins)
    markets = family_book.markets

    items: list[MenuItem] = []
    for bucket in (route_set.direct_yes, route_set.direct_no, route_set.synthetic_not_i):
        for route in bucket.values():
            items.append(_route_menu_item(route, family_key=family_key, bin_ids=bin_ids, omega=omega, markets=markets))
    for route in (*route_set.pair_arbs, *route_set.full_basket_arbs, *route_set.conversion_routes):
        items.append(_route_menu_item(route, family_key=family_key, bin_ids=bin_ids, omega=omega, markets=markets))

    for bin_id, shares in holdings_by_bin_id.items():
        if Decimal(shares) > 0 and bin_id in bin_ids:
            items.append(
                _sell_holding_item(
                    family_key=family_key,
                    bin_id=bin_id,
                    held_shares=Decimal(shares),
                    bin_ids=bin_ids,
                    market=markets.get(bin_id),
                )
            )

    # include_maker_lane is accepted for signature stability but never emits a maker item in
    # W3 (taker-only). W4 wires the REST_ELIGIBLE predicate + contingent-asset valuation.
    _ = include_maker_lane

    spendable = float(wealth.cash_usd) if wealth is not None else 0.0
    cash_payoff = {JointOutcomeAtom.canonical_id({family_key: b}): 0.0 for b in bin_ids}
    items.append(
        MenuItem(
            item_id="hold_cash",
            kind="hold_cash",
            family_key=family_key,
            bin_id=None,
            route=None,
            executable=True,
            non_executable_reason=None,
            unit_payoff=AtomPayoffProjector(payoff_by_atom_id=cash_payoff, unit_cost_usd=0.0),
            max_units=Decimal(str(spendable)),
            min_tick_size=_DEFAULT_TICK,
            min_order_size=_DEFAULT_MIN_ORDER,
        )
    )

    items_tuple = tuple(items)
    return SolveMenu(
        family_key=family_key,
        items=items_tuple,
        menu_hash=_menu_hash(family_key, items_tuple),
    )
