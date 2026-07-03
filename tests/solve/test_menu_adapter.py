# Created: 2026-07-03
# Last reused/audited: 2026-07-03
"""build_solve_menu — pure reshaping of a priced NegRiskRouteSet into a SolveMenu (atom axis).

Duck-typed venue fakes (the adapter reads only documented attributes). Verifies the typed
atom payoff projector, per-item tick/min-size, sell-holding conservative bid floor, conversion
pass-through, hold_cash placement, and deterministic menu_hash.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import numpy as np

from src.solve.menu_adapter import build_solve_menu
from tests.solve import support as F


class FakeInstrument:
    """Mirror of the real Instrument: e_i for YES, 1 - e_i for NO, aligned to omega.bins."""

    def __init__(self, bin_id, side):
        self.bin_id = bin_id
        self.side = side

    def payoff_vector(self, omega):
        ids = [b.bin_id for b in omega.bins]
        e = np.zeros(len(ids))
        i = ids.index(self.bin_id)
        if self.side == "YES":
            e[i] = 1.0
        else:
            e[:] = 1.0
            e[i] = 0.0
        return e


def _route(route_id, bin_id, side, cost, max_shares, *, shares=100, executable=True, reason=None):
    return SimpleNamespace(
        route_id=route_id,
        route_type="DIRECT_YES" if side == "YES" else "DIRECT_NO",
        instrument=FakeInstrument(bin_id, side),
        shares=Decimal(str(shares)),
        avg_cost=SimpleNamespace(value=float(cost)),
        max_shares=Decimal(str(max_shares)),
        legs=(SimpleNamespace(bin_id=bin_id, token_id=f"tok_{side}_{bin_id}"),),
        executable=executable,
        reason=reason,
    )


def _ladder(levels, tick="0.01", min_order="0.01"):
    return SimpleNamespace(
        levels=tuple(SimpleNamespace(price=Decimal(p), size=Decimal(s)) for p, s in levels),
        min_tick_size=Decimal(tick),
        min_order_size=Decimal(min_order),
    )


def _market(bin_id, bids=(), tick="0.01", min_order="0.01", no_ask_min_order=None):
    return SimpleNamespace(
        bin_id=bin_id,
        yes_asks=_ladder([], tick, min_order),
        yes_bids=_ladder(bids, tick, min_order),
        no_asks=_ladder([], tick, no_ask_min_order or min_order),
        no_bids=_ladder([]),
    )


def _family_book(bin_ids, markets):
    omega = SimpleNamespace(bins=[SimpleNamespace(bin_id=b) for b in bin_ids])
    return SimpleNamespace(omega=omega, markets=markets)


def _route_set(direct_yes=None, direct_no=None, synthetic_not_i=None, pair_arbs=(), full_basket_arbs=(), conversion_routes=()):
    return SimpleNamespace(
        direct_yes=direct_yes or {},
        direct_no=direct_no or {},
        synthetic_not_i=synthetic_not_i or {},
        pair_arbs=tuple(pair_arbs),
        full_basket_arbs=tuple(full_basket_arbs),
        conversion_routes=tuple(conversion_routes),
    )


AY = F.atom_id("y")
AN = F.atom_id("n")


def test_direct_yes_route_becomes_atom_projector():
    fb = _family_book(("y", "n"), {"y": _market("y"), "n": _market("n")})
    rs = _route_set(direct_yes={"y": _route("r_y", "y", "YES", 0.4, 500)})
    menu = build_solve_menu(rs, family_key="fam", family_book=fb, holdings_by_bin_id={})
    item = next(it for it in menu.items if it.item_id == "r_y")
    assert item.kind == "buy_yes"
    assert item.executable is True
    assert item.unit_payoff.payoff_by_atom_id == {AY: 0.6, AN: -0.4}
    assert item.unit_payoff.unit_cost_usd == 0.4
    # depth cap = min(max_shares=500, priced shares=100) — never size past the priced depth
    assert item.max_units == Decimal("100")
    assert item.min_tick_size == Decimal("0.01")


def test_direct_no_route_payoff_shape():
    fb = _family_book(("y", "n"), {"y": _market("y"), "n": _market("n")})
    rs = _route_set(direct_no={"y": _route("r_no", "y", "NO", 0.3, 200)})
    menu = build_solve_menu(rs, family_key="fam", family_book=fb, holdings_by_bin_id={})
    item = next(it for it in menu.items if it.item_id == "r_no")
    assert item.kind == "buy_no"
    assert item.unit_payoff.payoff_by_atom_id == {AY: -0.3, AN: 0.7}


def test_non_executable_route_kept_with_reason():
    fb = _family_book(("y", "n"), {"y": _market("y"), "n": _market("n")})
    rs = _route_set(direct_yes={"y": _route("dead", "y", "YES", 0.4, 0, executable=False, reason="NO_DEPTH: y")})
    menu = build_solve_menu(rs, family_key="fam", family_book=fb, holdings_by_bin_id={})
    dead = next(it for it in menu.items if it.item_id == "dead")
    assert dead.executable is False
    assert dead.non_executable_reason == "NO_DEPTH: y"


def test_per_item_tick_min_from_route_market():
    # y market has a coarse tick; n market a fine one -> items carry their own
    fb = _family_book(("y", "n"), {"y": _market("y", tick="0.05", min_order="5"), "n": _market("n")})
    rs = _route_set(
        direct_yes={"y": _route("r_y", "y", "YES", 0.4, 500)},
        direct_no={"n": _route("r_n", "n", "NO", 0.4, 500)},
    )
    menu = build_solve_menu(rs, family_key="fam", family_book=fb, holdings_by_bin_id={})
    ry = next(it for it in menu.items if it.item_id == "r_y")
    rn = next(it for it in menu.items if it.item_id == "r_n")
    assert ry.min_tick_size == Decimal("0.05")
    assert ry.min_order_size == Decimal("5")
    assert rn.min_tick_size == Decimal("0.01")


def test_sell_holding_lane_prices_conservative_bid_floor():
    market_y = _market("y", bids=[("0.60", "30"), ("0.55", "40")])
    fb = _family_book(("y", "n"), {"y": market_y, "n": _market("n")})
    menu = build_solve_menu(_route_set(), family_key="fam", family_book=fb, holdings_by_bin_id={"y": Decimal("50")})
    sell = next(it for it in menu.items if it.kind == "sell_holding")
    assert sell.executable is True
    # proceeds floored at worst bid 0.55: +0.55 every atom, minus surrendered YES claim in y
    assert sell.unit_payoff.payoff_by_atom_id == {AY: 0.55 - 1.0, AN: 0.55}
    assert sell.unit_payoff.unit_cost_usd == -0.55  # cash received, not spent
    assert sell.max_units == Decimal("50")


def test_conversion_routes_pass_through_empty():
    fb = _family_book(("y", "n"), {"y": _market("y"), "n": _market("n")})
    menu = build_solve_menu(_route_set(), family_key="fam", family_book=fb, holdings_by_bin_id={})
    assert [it.kind for it in menu.items] == ["hold_cash"]
    assert all("convert" not in it.kind and "basket" not in it.kind for it in menu.items)


def test_hold_cash_is_last_and_zero_payoff():
    fb = _family_book(("y", "n"), {"y": _market("y"), "n": _market("n")})
    rs = _route_set(direct_yes={"y": _route("r_y", "y", "YES", 0.4, 500)})
    w = F.flat_wealth_state(("y", "n"), 250.0)
    menu = build_solve_menu(rs, family_key="fam", family_book=fb, holdings_by_bin_id={}, wealth=w)
    assert menu.items[-1].kind == "hold_cash"
    assert menu.items[-1].max_units == Decimal("250.0")
    assert set(menu.items[-1].unit_payoff.payoff_by_atom_id.values()) == {0.0}


def test_maker_lane_disabled_even_when_requested():
    fb = _family_book(("y", "n"), {"y": _market("y"), "n": _market("n")})
    rs = _route_set(direct_yes={"y": _route("r_y", "y", "YES", 0.4, 500)})
    menu = build_solve_menu(rs, family_key="fam", family_book=fb, holdings_by_bin_id={}, include_maker_lane=True)
    assert all(it.kind != "maker_quote" for it in menu.items)


def test_depth_cap_at_priced_shares():
    # avg_cost was walked at route.shares=10; max_shares=110 exposes deeper (unpriced) depth.
    # The menu item must cap at min(max_shares, shares)=10 so the solver never sizes past the
    # priced depth (consult REV-2 follow-up blocker).
    fb = _family_book(("y", "n"), {"y": _market("y"), "n": _market("n")})
    rs = _route_set(direct_yes={"y": _route("r_y", "y", "YES", 0.4, 110, shares=10)})
    menu = build_solve_menu(rs, family_key="fam", family_book=fb, holdings_by_bin_id={})
    item = next(it for it in menu.items if it.item_id == "r_y")
    assert item.max_units == Decimal("10")


def test_non_direct_routes_non_executable_in_phase1():
    # synthetic / pair-arb / full-basket routes are menu-visible but NOT executable in phase 1
    # (their single-instrument payoff projection is wrong and multi-leg atomicity would be lost).
    fb = _family_book(("y", "n"), {"y": _market("y"), "n": _market("n")})
    rs = _route_set(
        direct_yes={"y": _route("direct", "y", "YES", 0.4, 500)},
        synthetic_not_i={"y": _route("synth", "y", "NO", 0.4, 500)},
        pair_arbs=(_route("pair", "y", "YES", 0.3, 500),),
        full_basket_arbs=(_route("basket", "y", "YES", 0.2, 500),),
    )
    menu = build_solve_menu(rs, family_key="fam", family_book=fb, holdings_by_bin_id={})
    by_id = {it.item_id: it for it in menu.items}
    assert by_id["direct"].executable is True
    for nid in ("synth", "pair", "basket"):
        assert by_id[nid].executable is False
        assert by_id[nid].non_executable_reason == "PHASE1_NON_DIRECT_ROUTE"


def test_direct_no_route_quantizes_on_no_ladder():
    # a direct NO buy must inherit tick/min-size from the NO ask ladder, not yes_asks.
    fb = _family_book(("y", "n"), {"y": _market("y", no_ask_min_order="7"), "n": _market("n")})
    rs = _route_set(direct_no={"y": _route("r_no", "y", "NO", 0.3, 200)})
    menu = build_solve_menu(rs, family_key="fam", family_book=fb, holdings_by_bin_id={})
    item = next(it for it in menu.items if it.item_id == "r_no")
    assert item.min_order_size == Decimal("7")  # from no_asks, not yes_asks (min_order 0.01)


def test_menu_hash_deterministic_and_sensitive():
    fb = _family_book(("y", "n"), {"y": _market("y"), "n": _market("n")})
    rs1 = _route_set(direct_yes={"y": _route("r_y", "y", "YES", 0.4, 500)})
    rs2 = _route_set(direct_yes={"y": _route("r_y", "y", "YES", 0.5, 500)})  # different cost
    h1 = build_solve_menu(rs1, family_key="fam", family_book=fb, holdings_by_bin_id={}).menu_hash
    h1b = build_solve_menu(rs1, family_key="fam", family_book=fb, holdings_by_bin_id={}).menu_hash
    h2 = build_solve_menu(rs2, family_key="fam", family_book=fb, holdings_by_bin_id={}).menu_hash
    assert h1 == h1b
    assert h1 != h2
