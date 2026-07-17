# Created: 2026-07-03
# Last reused or audited: 2026-07-13
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

SELL LANE: a ledger-bound native holding supplies exact position/side/token identity. Selling
YES on bin ``b`` gives up ``e_b``; selling NO gives up ``1-e_b``. Both receive bid proceeds in
cash in every atom. Proceeds use the holding side's own ladder, current executable holding/depth
cap, and fee-deducted full-size VWAP. That linear VWAP is conservative for smaller sizes because
the native bid ladder is best-first.

MAKER LANE DISABLED (consult REV-2 ruling 6): W3 is taker-only; a maker quote is a contingent
asset (fill-state/latency/cancel-risk) the current MenuItem cannot value, so no maker item is
emitted regardless of ``include_maker_lane``.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from src.contracts.execution_price import polymarket_fee
from src.solve.types import (
    AtomPayoffProjector,
    JointOutcomeAtom,
    MenuItem,
    NativeHolding,
    NativeHoldingsSnapshot,
    SolveMenu,
    WealthStateByAtom,
)

if TYPE_CHECKING:
    from src.execution.family_book import FamilyBook
    from src.execution.negrisk_routes import NegRiskRouteSet, RouteCost

_DEFAULT_TICK = Decimal("0.01")
_DEFAULT_MIN_ORDER = Decimal("0.01")


def native_holdings_snapshot_from_positions(
    *,
    family_key: str,
    omega,
    positions,
    ledger_snapshot_id: str,
) -> NativeHoldingsSnapshot:
    """Bind canonical open positions to the exact native claims in ``omega``.

    The wealth vector cannot reveal whether exposure came from YES or NO.  This adapter uses
    the position's condition, direction, token and chain shares, and refuses any mismatch with
    the current settlement topology.  Foreign-family positions are irrelevant to this family's
    menu and are skipped; the caller owns the complete-family scope check.
    """

    bindings = {str(outcome.condition_id or ""): outcome for outcome in omega.bins}
    if not family_key.strip() or not ledger_snapshot_id.strip() or not bindings:
        raise ValueError("native holdings snapshot requires family, ledger, and omega identities")
    holdings: list[NativeHolding] = []
    for position in tuple(positions or ()):
        condition_id = str(getattr(position, "condition_id", "") or "")
        outcome = bindings.get(condition_id)
        if outcome is None:
            continue
        shares = Decimal(str(getattr(position, "chain_shares", 0) or 0))
        if not shares.is_finite() or shares < 0:
            raise ValueError(
                f"position {getattr(position, 'trade_id', '')!r} has invalid "
                f"chain_shares {shares}"
            )
        if shares == 0:
            continue
        direction_raw = getattr(position, "direction", "")
        direction = str(getattr(direction_raw, "value", direction_raw) or "").lower()
        if direction == "buy_yes":
            side = "YES"
            token_id = str(getattr(position, "token_id", "") or "")
            expected_token = str(getattr(outcome, "yes_token_id", "") or "")
        elif direction == "buy_no":
            side = "NO"
            token_id = str(getattr(position, "no_token_id", "") or "")
            expected_token = str(getattr(outcome, "no_token_id", "") or "")
        else:
            raise ValueError(
                f"position {getattr(position, 'trade_id', '')!r} has unsupported direction "
                f"{direction!r}"
            )
        if not token_id or token_id != expected_token:
            raise ValueError(
                f"position {getattr(position, 'trade_id', '')!r} token does not match "
                f"current omega for {condition_id}"
            )
        position_id = str(
            getattr(position, "position_id", "")
            or getattr(position, "trade_id", "")
            or ""
        )
        holdings.append(
            NativeHolding(
                position_id=position_id,
                family_key=family_key,
                bin_id=str(outcome.bin_id),
                side=side,  # type: ignore[arg-type]
                token_id=token_id,
                shares=shares,
            )
        )
    return NativeHoldingsSnapshot(
        family_key=family_key,
        ledger_snapshot_id=ledger_snapshot_id,
        holdings=tuple(holdings),
    )


def _ladder_tick_min(ladder) -> tuple[Decimal, Decimal]:
    if ladder is None:
        return _DEFAULT_TICK, _DEFAULT_MIN_ORDER
    return Decimal(ladder.min_tick_size), Decimal(ladder.min_order_size)


def _route_menu_item(
    route: "RouteCost", *, family_key: str, bin_ids: tuple[str, ...], omega, markets, phase1_executable: bool
) -> MenuItem:
    """One RouteCost → one MenuItem (net after-cost unit payoff over the joint atoms).

    ``phase1_executable=False`` (non-direct routes: synthetic/pair/basket/conversion) forces the
    item non-executable — their single-instrument payoff projection is WRONG (a riskfree basket
    is constant across atoms, not a YES claim) and their multi-leg execution loses leg atomicity
    when collapsed to one order (consult REV-2 follow-up HIGH). They stay menu-visible for audit.
    """
    instrument = route.instrument
    payoff_vec = instrument.payoff_vector(omega)
    cost = float(route.avg_cost.value)
    payoff_by_atom = {
        JointOutcomeAtom.canonical_id({family_key: bin_ids[j]}): float(payoff_vec[j]) - cost
        for j in range(len(bin_ids))
    }
    kind = "buy_yes" if instrument.side == "YES" else "buy_no"
    market = markets.get(instrument.bin_id)
    # side-correct ladder: YES buys walk yes_asks, NO buys walk no_asks (consult REV-2 follow-up)
    ladder_attr = "yes_asks" if instrument.side == "YES" else "no_asks"
    tick, min_order = _ladder_tick_min(getattr(market, ladder_attr, None) or getattr(market, "yes_asks", None))
    # A proof-native maker quote is still a direct instrument, but its acquisition
    # is contingent on fill/latency/cancel state.  Phase 1 has no such state model,
    # so treating the claim as already acquired would overstate expected utility.
    maker_contingent = str(getattr(route.avg_cost, "price_type", "") or "").lower() == "bid"
    executable = bool(route.executable) and phase1_executable and not maker_contingent
    if route.reason is not None:
        reason = route.reason
    elif maker_contingent:
        reason = "PHASE1_MAKER_CONTINGENT_UNMODELED"
    else:
        reason = None if executable else "PHASE1_NON_DIRECT_ROUTE"
    # Depth cap: avg_cost was walked at route.shares, so never size past the priced depth in
    # phase 1 (consult REV-2 follow-up blocker) — a per-level cost curve is phase-2.
    max_units = min(Decimal(route.max_shares), Decimal(route.shares))
    return MenuItem(
        item_id=route.route_id,
        kind=kind,  # type: ignore[arg-type]
        family_key=family_key,
        bin_id=instrument.bin_id,
        route=route,
        executable=executable,
        non_executable_reason=reason,
        unit_payoff=AtomPayoffProjector(payoff_by_atom_id=payoff_by_atom, unit_cost_usd=cost),
        max_units=max_units,
        min_tick_size=tick,
        min_order_size=min_order,
        token_id=getattr(route.legs[0], "token_id", None) if getattr(route, "legs", ()) else None,
    )


def _sell_holding_item(
    *, holding: NativeHolding, bin_ids: tuple[str, ...], market
) -> MenuItem:
    """One exact YES/NO holding → a conservative size-aware native SELL item.

    The linear menu price is the fee-deducted VWAP for selling the whole executable
    ``max_units`` through current bid depth.  Because bids are best-first, this full-size
    VWAP is a lower bound for every smaller size the solver may choose; using the ladder's
    worst displayed bid would be needlessly pessimistic whenever the holding does not reach
    that level.
    """
    ladder_name = "yes_bids" if holding.side == "YES" else "no_bids"
    ladder = getattr(market, ladder_name, None)
    bids = tuple(getattr(ladder, "levels", ()) or ())
    if bids:
        depth = sum((Decimal(lvl.size) for lvl in bids), Decimal("0"))
        max_units = min(Decimal(holding.shares), depth)
        _, min_order = _ladder_tick_min(ladder)
        executable = max_units >= min_order
        reason = None if executable else "HOLDING_OR_BID_DEPTH_BELOW_MIN_ORDER"
        remaining = max_units
        net_proceeds = Decimal("0")
        fee_rate = float(getattr(ladder, "fee_rate", 0.0) or 0.0)
        for level in bids:
            take = min(remaining, Decimal(level.size))
            price = Decimal(level.price)
            fee = (
                Decimal(str(polymarket_fee(float(price), fee_rate)))
                if fee_rate > 0.0 and Decimal("0") < price < Decimal("1")
                else Decimal("0")
            )
            net_price = price - fee
            net_proceeds += take * max(net_price, Decimal("0"))
            remaining -= take
            if remaining <= 0:
                break
        proceeds = float(net_proceeds / max_units) if max_units > 0 else 0.0
    else:
        proceeds = 0.0
        max_units = Decimal("0")
        executable = False
        reason = "NO_BID_DEPTH"
    payoff_by_atom = {}
    for bin_id in bin_ids:
        token_wins = (
            bin_id == holding.bin_id
            if holding.side == "YES"
            else bin_id != holding.bin_id
        )
        atom_id = JointOutcomeAtom.canonical_id({holding.family_key: bin_id})
        payoff_by_atom[atom_id] = proceeds - (1.0 if token_wins else 0.0)
    tick, min_order = _ladder_tick_min(ladder)
    return MenuItem(
        item_id=(
            f"sell_holding:{holding.position_id}:{holding.side}:{holding.bin_id}"
        ),
        kind="sell_holding",
        family_key=holding.family_key,
        bin_id=holding.bin_id,
        route=None,
        executable=executable,
        non_executable_reason=reason,
        unit_payoff=AtomPayoffProjector(payoff_by_atom_id=payoff_by_atom, unit_cost_usd=-proceeds),
        max_units=max_units,
        min_tick_size=tick,
        min_order_size=min_order,
        token_id=holding.token_id,
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
        digest.update(str(it.token_id or "").encode())
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
    holdings: Optional[NativeHoldingsSnapshot] = None,
    wealth: Optional[WealthStateByAtom] = None,
    include_maker_lane: bool = False,
) -> SolveMenu:
    """Adapt the priced route menu into the solver's SolveMenu.

    Contract (math core / sub-slice 2 implements):
    * every RouteCost in direct_yes/direct_no/synthetic_not_i/pair_arbs/full_basket_arbs
      becomes one MenuItem with a typed atom payoff projector (payoff_vector − avg_cost);
    * ledger-bound YES/NO holdings add sell_holding items on their native BID depth;
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
    # Phase-1 executable = DIRECT NATIVE routes only (single-leg direct_yes / direct_no).
    # synthetic/pair/basket/conversion routes are menu-visible but non-executable in phase 1.
    for bucket in (route_set.direct_yes, route_set.direct_no):
        for route in bucket.values():
            items.append(_route_menu_item(route, family_key=family_key, bin_ids=bin_ids, omega=omega, markets=markets, phase1_executable=True))
    for route in (
        *route_set.synthetic_not_i.values(),
        *route_set.pair_arbs,
        *route_set.full_basket_arbs,
        *route_set.conversion_routes,
    ):
        items.append(_route_menu_item(route, family_key=family_key, bin_ids=bin_ids, omega=omega, markets=markets, phase1_executable=False))

    if holdings is not None:
        if holdings.family_key != family_key:
            raise ValueError(
                f"holdings family {holdings.family_key} does not match solve family {family_key}"
            )
        if wealth is None or not wealth.ledger_snapshot_id:
            raise ValueError("ledger-bound holdings require ledger-bound wealth")
        if holdings.ledger_snapshot_id != wealth.ledger_snapshot_id:
            raise ValueError(
                "holdings/wealth ledger snapshot mismatch: "
                f"{holdings.ledger_snapshot_id} != {wealth.ledger_snapshot_id}"
            )
        for holding in holdings.holdings:
            if holding.bin_id not in bin_ids:
                raise ValueError(
                    f"holding {holding.position_id} bin {holding.bin_id} is outside current omega"
                )
            items.append(
                _sell_holding_item(
                    holding=holding,
                    bin_ids=bin_ids,
                    market=markets.get(holding.bin_id),
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
