# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/execution/negrisk_routes.py" block lines 654-732: RouteCost 658-676,
#   NegRiskRouteSet 677-685, route rules 686-699, arbitrage checks 701-720, route
#   dominance 722-726, venue-primitive verification 728-732) reconciled against
#   docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md (VENUE-PRIMITIVE VERDICT
#   §7-19 BLOCKER: convert/merge/split ABSENT -> CONVERSION_SELL_BASKET /
#   conversion_routes omitted; DIRECT_*/SYNTHETIC_*/*_ARB proceed live via
#   independent native submit(); GREENFIELD — new file only; RouteLeg drift-resolved as
#   one independent native order).
"""RED-on-revert contract tests for the family route engine (Stage 7c negrisk_routes).

Three spec-named tests fail if the corrected transformation is reverted to the broken
behavior the spec replaces:

  * ``test_synthetic_yes_basket_dominates_expensive_direct_no`` — route dominance
    (spec lines 722-726): for a NO_i buy on a neg-risk market, the chosen cost is
    ``min(direct_no_cost(i, s), synthetic_yes_basket_cost(i, s))`` priced SIZE-AWARE on
    the executable ladders of BOTH routes. The synthetic route buys equal shares of
    every OTHER sibling's YES — exactly the NO_i payoff basket ``1 - e_i``. RED-on-revert:
    if a NO is priced off the direct NO ladder WITHOUT comparing the cheaper sibling-YES
    basket (the broken "direct-only" transform the spec replaces), the expensive direct
    NO would be returned. The test builds a family where the direct NO ask is expensive
    but the sibling-YES basket is cheaper, and asserts ``best_no_route`` selects the
    SYNTHETIC route at the strictly lower per-share cost.

  * ``test_negrisk_routes_disabled_when_flag_false`` — the neg-risk route engine is
    behind ``enable_negrisk_routes``. RED-on-revert: if the flag is ignored and the
    neg-risk routes are always built, the synthetic / arb / conversion maps would be
    non-empty when the flag is False. The test asserts that with the flag OFF the
    ``synthetic_not_i``, ``pair_arbs``, ``full_basket_arbs``, and ``conversion_routes``
    are EMPTY (direct-only routing, the negRisk=False fallback) while the direct YES /
    direct NO routes are still built; and that with the flag ON the synthetic basket
    reappears — so the flag genuinely gates the neg-risk surface.

  * ``test_conversion_routes_omitted_when_venue_primitive_absent`` — the
    venue-primitive BLOCKER (spec lines 728-732; drift-ledger §7-19): on-chain
    convert/merge/split is ABSENT, so a ``CONVERSION_SELL_BASKET`` route has no venue
    primitive to execute and is not emitted. RED-on-revert: if a conversion route is
    emitted before the primitive exists, this fails. The test also re-runs the
    spec-mandated grep (spec line 730) to confirm the primitive is in fact absent.
"""
from __future__ import annotations

import subprocess
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from src.config import City
from src.execution.family_book import (
    ExecutableLadder,
    FamilyBook,
    MarketBook,
    build_family_book,
)
from src.execution.negrisk_routes import (
    CONVERSION_VENUE_PRIMITIVE_ABSENT,
    NegRiskRouteError,
    NegRiskRouteSet,
    RouteCost,
    build_negrisk_route_set,
)
from src.probability.event_resolution import EventResolution, event_resolution_for_city
from src.probability.outcome_space import (
    OutcomeBin,
    OutcomeSpace,
    compute_topology_hash,
)
from src.strategy.live_inference.executable_cost import QuoteLevel

_CAPTURED = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures — a SMALL complete neg-risk Omega (3 tradeable bins) with per-bin,
# per-side control over the executable ladder so a scenario can make the direct
# NO expensive and the sibling-YES basket cheap.
# ---------------------------------------------------------------------------

def _resolution(city_name: str = "Tokyo", metric: str = "high") -> EventResolution:
    city = City(
        name=city_name,
        lat=35.68,
        lon=139.69,
        timezone="Asia/Tokyo",
        settlement_unit="C",
        cluster="asia",
        wu_station="RJTT",
        settlement_source_type="wu_icao",
    )
    return event_resolution_for_city(city, date(2026, 6, 14), metric)


def _bin(bin_id: str, lo, hi, label: str, rule: str) -> OutcomeBin:
    return OutcomeBin(
        bin_id=bin_id,
        condition_id=f"cond-{bin_id}",
        label=label,
        lower_native=lo,
        upper_native=hi,
        yes_token_id=f"yes-{bin_id}",
        no_token_id=f"no-{bin_id}",
        executable=True,
        rounding_rule=rule,
    )


def _three_bin_space(family_id: str = "tokyo-high-3") -> OutcomeSpace:
    """A complete 3-bin °C partition: (-inf,24], 25, [26,+inf).

    Kept deliberately small so the synthetic NO_i = sibling-YES basket (every OTHER bin)
    is just two legs — easy to make the basket cheaper than an expensive direct NO.
    """
    resolution = _resolution()
    rule = resolution.rounding_rule
    bins = (
        _bin("b_low", None, 24.0, "24°C or below", rule),
        _bin("b25", 25.0, 25.0, "25°C", rule),
        _bin("b_high", 26.0, None, "26°C or above", rule),
    )
    space = OutcomeSpace(
        family_id=family_id,
        resolution=resolution,
        bins=bins,
        topology_hash=compute_topology_hash(family_id, resolution, bins),
    )
    space.validate()
    return space


def _ladder(side: str, *levels: tuple[str, str]) -> ExecutableLadder:
    """An executable ladder from (price, size) string pairs (best-first)."""
    return ExecutableLadder(
        levels=tuple(QuoteLevel(Decimal(p), Decimal(s)) for p, s in levels),
        side=side,  # type: ignore[arg-type]
        fee_rate=0.05,
        min_tick_size=Decimal("0.01"),
        min_order_size=Decimal("1.0"),
    )


def _market_book(
    bin_id: str,
    *,
    neg_risk: bool,
    yes_ask: str,
    no_ask: str,
    yes_bid: str = "0.10",
    no_bid: str = "0.10",
    depth: str = "10000",
) -> MarketBook:
    """A MarketBook with controllable per-side best prices (single deep level).

    A single deep level per ladder keeps the size-aware average equal to the level price
    (so the test can reason about exact costs), with ``depth`` shares of room so a small
    requested size never starves the ladder.
    """
    return MarketBook(
        condition_id=f"cond-{bin_id}",
        bin_id=bin_id,
        yes_token_id=f"yes-{bin_id}",
        no_token_id=f"no-{bin_id}",
        yes_asks=_ladder("ask", (yes_ask, depth)),
        yes_bids=_ladder("bid", (yes_bid, depth)),
        no_asks=_ladder("ask", (no_ask, depth)),
        no_bids=_ladder("bid", (no_bid, depth)),
        neg_risk=neg_risk,
    )


def _family(markets: dict[str, MarketBook], space: OutcomeSpace) -> FamilyBook:
    return build_family_book(omega=space, markets=markets, captured_at_utc=_CAPTURED)


# ---------------------------------------------------------------------------
# SPEC RED-on-revert #1: route dominance picks the cheaper sibling-YES basket
# over an expensive direct NO (spec lines 722-726).
# ---------------------------------------------------------------------------

def test_synthetic_yes_basket_dominates_expensive_direct_no():
    """A NO_i is priced ``min(direct_no, synthetic_yes_basket)`` — the cheaper wins.

    Scenario (neg-risk family, target bin = ``b25``):
      * direct NO_25 ask is EXPENSIVE: 0.80 per share.
      * the two sibling YES asks (b_low, b_high) are CHEAP: 0.20 + 0.30 = 0.50 per NO
        unit, so the synthetic sibling-YES basket costs ~0.50 + fees (still well below the
        0.80 direct NO).

    The synthetic basket buys equal shares of every OTHER sibling's YES — its payoff is
    ``1 - e_25`` (it wins iff b_low OR b_high settles), EXACTLY the NO_25 payoff. So the
    correct NO_25 cost is the cheaper of the two routes.

    RED-on-revert: if the engine reverts to pricing a NO off the direct NO ladder WITHOUT
    comparing the sibling-YES basket (the "direct-only" transform the spec replaces),
    ``best_no_route`` would return the 0.80 direct NO. This test asserts it returns the
    SYNTHETIC route at a strictly lower per-share cost than the direct NO — so a
    direct-only revert fails.
    """
    space = _three_bin_space()
    markets = {
        # Expensive direct NO on the TARGET bin; its own YES is irrelevant to NO routing.
        "b25": _market_book("b25", neg_risk=True, yes_ask="0.50", no_ask="0.80"),
        # CHEAP sibling YES asks -> a cheap synthetic NO_25 basket.
        "b_low": _market_book("b_low", neg_risk=True, yes_ask="0.20", no_ask="0.85"),
        "b_high": _market_book("b_high", neg_risk=True, yes_ask="0.30", no_ask="0.75"),
    }
    fb = _family(markets, space)
    shares = Decimal("100")

    routes = build_negrisk_route_set(fb, shares=shares)

    direct_no = routes.direct_no["b25"]
    synthetic = routes.synthetic_not_i["b25"]

    # Both routes are executable and priced size-aware.
    assert direct_no.executable is True
    assert synthetic.executable is True
    assert direct_no.route_type == "DIRECT_NO"
    assert synthetic.route_type == "SYNTHETIC_NOT_I_YES_BASKET"

    # The direct NO is genuinely expensive (~0.80 + fee), the synthetic genuinely cheaper
    # (~0.20 + 0.30 summed sibling-YES + fees ~= 0.51). The basket is the cheaper route.
    assert float(synthetic.avg_cost) < float(direct_no.avg_cost), (
        "the sibling-YES basket must be cheaper than the expensive direct NO here"
    )
    assert float(direct_no.avg_cost) > 0.80  # 0.80 ask + taker fee
    assert float(synthetic.avg_cost) < 0.60  # ~0.51 summed + small fees

    # The synthetic route buys equal shares of EVERY other sibling's YES — two legs
    # (b_low, b_high), each buy_yes at the requested size. It is the NO_25 payoff basket.
    assert {leg.bin_id for leg in synthetic.legs} == {"b_low", "b_high"}
    assert all(leg.direction == "buy_yes" for leg in synthetic.legs)
    assert all(leg.shares == shares for leg in synthetic.legs)
    assert synthetic.instrument.side == "NO"
    assert synthetic.instrument.bin_id == "b25"

    # max_shares is the MINIMUM depth across sibling YES asks (spec line 699).
    assert synthetic.max_shares == Decimal("10000")

    # ROUTE DOMINANCE (spec 722-726): best_no_route returns the cheaper route — the
    # SYNTHETIC basket, NOT the expensive direct NO. This is the load-bearing assertion:
    # a direct-only revert returns direct_no here and fails.
    best = routes.best_no_route("b25")
    assert best.route_type == "SYNTHETIC_NOT_I_YES_BASKET", (
        "route dominance min(direct_no, synthetic_yes_basket) must pick the cheaper "
        "sibling-YES basket; a direct-only revert would return the 0.80 direct NO"
    )
    assert best is synthetic
    assert float(best.avg_cost) < float(direct_no.avg_cost)


# ---------------------------------------------------------------------------
# SPEC RED-on-revert #2: the neg-risk route engine is gated by the flag.
# ---------------------------------------------------------------------------

def test_negrisk_routes_disabled_when_flag_false():
    """With ``enable_negrisk_routes=False`` only the direct YES/NO routes are built.

    The neg-risk route engine (synthetic sibling-YES basket + family arbitrage +
    conversion routes) is behind ``enable_negrisk_routes``. With the flag OFF the engine
    falls back to direct-only routing — exactly the negRisk=False rule (spec line 694):
    no synthetic basket is considered.

    RED-on-revert: if the flag is ignored and the neg-risk routes are built unconditionally,
    ``synthetic_not_i`` / ``pair_arbs`` / ``full_basket_arbs`` / ``conversion_routes`` would
    be non-empty when the flag is False. This test asserts they are EMPTY with the flag OFF
    (while the direct routes are still built), and that flipping the flag ON re-populates
    the synthetic basket — so the flag genuinely gates the surface.
    """
    space = _three_bin_space()
    markets = {
        "b25": _market_book("b25", neg_risk=True, yes_ask="0.50", no_ask="0.80"),
        "b_low": _market_book("b_low", neg_risk=True, yes_ask="0.20", no_ask="0.85"),
        "b_high": _market_book("b_high", neg_risk=True, yes_ask="0.30", no_ask="0.75"),
    }
    fb = _family(markets, space)
    shares = Decimal("100")

    # Flag OFF — direct-only routing.
    off = build_negrisk_route_set(fb, shares=shares, enable_negrisk_routes=False)
    assert off.synthetic_not_i == {}, "flag OFF: no synthetic sibling-YES basket built"
    assert off.pair_arbs == (), "flag OFF: no pair arbitrage routes built"
    assert off.full_basket_arbs == (), "flag OFF: no full-YES-basket arbitrage built"
    assert off.conversion_routes == (), "flag OFF: no conversion routes built"
    # The plain direct routes are ALWAYS built (they are not a neg-risk feature).
    assert set(off.direct_yes.keys()) == {"b25", "b_low", "b_high"}
    assert set(off.direct_no.keys()) == {"b25", "b_low", "b_high"}
    assert all(r.executable for r in off.direct_yes.values())
    assert all(r.executable for r in off.direct_no.values())

    # Flag ON — the synthetic basket (and arb/conversion surface) reappears. This is the
    # killer for a "flag ignored" revert: the ON/OFF results must differ.
    on = build_negrisk_route_set(fb, shares=shares, enable_negrisk_routes=True)
    assert "b25" in on.synthetic_not_i, "flag ON: synthetic sibling-YES basket built"
    assert on.conversion_routes == (), "flag ON: conversion routes omitted until venue primitive exists"
    assert on.synthetic_not_i != off.synthetic_not_i, (
        "the flag must change the route set; if OFF and ON produce the same neg-risk "
        "surface, the flag is being ignored"
    )


# ---------------------------------------------------------------------------
# SPEC RED-on-revert #3: conversion routes are omitted because the venue
# primitive is absent (spec lines 728-732; drift-ledger §7-19 BLOCKER).
# ---------------------------------------------------------------------------

def test_conversion_routes_omitted_when_venue_primitive_absent():
    """No CONVERSION_SELL_BASKET routes are emitted until the venue primitive exists."""
    space = _three_bin_space()
    markets = {
        "b25": _market_book("b25", neg_risk=True, yes_ask="0.50", no_ask="0.55"),
        "b_low": _market_book("b_low", neg_risk=True, yes_ask="0.20", no_ask="0.85"),
        "b_high": _market_book("b_high", neg_risk=True, yes_ask="0.30", no_ask="0.75"),
    }
    fb = _family(markets, space)
    shares = Decimal("100")

    routes = build_negrisk_route_set(fb, shares=shares)

    assert routes.conversion_routes == ()

    # Direct/synthetic/arb routes use independent native submit() and require no conversion.
    assert all(r.executable for r in routes.direct_yes.values())
    assert all(r.executable for r in routes.direct_no.values())

    # GROUND TRUTH: re-run the spec-mandated grep (spec line 730). The convert/merge/split
    # venue primitive must be ABSENT for the omitted route surface to be correct.
    proc = subprocess.run(
        [
            "grep",
            "-rnE",
            r"def convert|mergePositions\(|convertPositions\(|splitPosition\(|def merge_positions|def split_position",
            "src/venue/",
            "src/execution/",
        ],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1 and proc.stdout.strip() == "", (
        "convert/merge/split venue primitive appears to be WIRED now "
        f"(grep hit:\n{proc.stdout}\n) — the venue-primitive BLOCKER must be "
        "re-evaluated and conversion routes re-enabled via the new primitive."
    )


# ---------------------------------------------------------------------------
# Supporting contract checks (route-rule / size-aware / fail-closed invariants).
# ---------------------------------------------------------------------------

def test_non_negrisk_market_has_no_synthetic_route():
    """negRisk=False -> only the direct NO_i route (spec line 694); no synthetic basket.

    The synthetic sibling-YES basket exists ONLY where the venue's neg-risk structure
    makes the basket and the NO economically identical. On a non-neg-risk market there is
    no synthetic route to offer, and best_no_route returns the direct NO.
    """
    space = _three_bin_space()
    markets = {
        "b25": _market_book("b25", neg_risk=False, yes_ask="0.50", no_ask="0.80"),
        "b_low": _market_book("b_low", neg_risk=False, yes_ask="0.20", no_ask="0.85"),
        "b_high": _market_book("b_high", neg_risk=False, yes_ask="0.30", no_ask="0.75"),
    }
    fb = _family(markets, space)
    routes = build_negrisk_route_set(fb, shares=Decimal("100"))

    assert "b25" not in routes.synthetic_not_i, (
        "negRisk=False market has NO synthetic sibling-YES basket route"
    )
    # No conversion route for a non-neg-risk sibling either (conversion is a neg-risk op).
    assert routes.conversion_routes == ()
    best = routes.best_no_route("b25")
    assert best.route_type == "DIRECT_NO", "negRisk=False -> only the direct NO is usable"


def test_pair_arb_executable_only_when_combined_ask_below_one():
    """Pair arb: ``ask_yes_i + ask_no_i + fees < 1.0`` size-aware (spec lines 703-707)."""
    space = _three_bin_space()
    # b25: yes 0.40 + no 0.40 = 0.80 (+fees) < 1.0 -> arb clears.
    # b_low: yes 0.60 + no 0.55 = 1.15 >= 1.0 -> no arb.
    markets = {
        "b25": _market_book("b25", neg_risk=True, yes_ask="0.40", no_ask="0.40"),
        "b_low": _market_book("b_low", neg_risk=True, yes_ask="0.60", no_ask="0.55"),
        "b_high": _market_book("b_high", neg_risk=True, yes_ask="0.30", no_ask="0.30"),
    }
    fb = _family(markets, space)
    routes = build_negrisk_route_set(fb, shares=Decimal("100"))

    by_bin = {r.instrument.bin_id: r for r in routes.pair_arbs}
    assert by_bin["b25"].executable is True, "0.40+0.40+fees < 1.0 -> pair arb clears"
    assert by_bin["b_low"].executable is False, "0.60+0.55 >= 1.0 -> no pair arb"
    # Each cleared pair arb crosses BOTH sides at the requested size.
    arb = by_bin["b25"]
    assert {leg.direction for leg in arb.legs} == {"buy_yes", "buy_no"}
    assert all(leg.shares == Decimal("100") for leg in arb.legs)


def test_full_yes_basket_arb_size_aware():
    """Full YES basket arb: ``Σ_i ask_yes_i + fees < 1.0`` size-aware (spec 709-713)."""
    space = _three_bin_space()
    # Σ yes asks = 0.20 + 0.30 + 0.25 = 0.75 (+fees) < 1.0 -> basket arb clears.
    markets = {
        "b25": _market_book("b25", neg_risk=True, yes_ask="0.30", no_ask="0.75"),
        "b_low": _market_book("b_low", neg_risk=True, yes_ask="0.20", no_ask="0.85"),
        "b_high": _market_book("b_high", neg_risk=True, yes_ask="0.25", no_ask="0.80"),
    }
    fb = _family(markets, space)
    routes = build_negrisk_route_set(fb, shares=Decimal("100"))

    assert len(routes.full_basket_arbs) == 1
    basket = routes.full_basket_arbs[0]
    assert basket.executable is True, "summed YES asks 0.75 + fees < 1.0 -> arb clears"
    assert {leg.bin_id for leg in basket.legs} == {"b25", "b_low", "b_high"}
    assert all(leg.direction == "buy_yes" for leg in basket.legs)


def test_depth_starved_route_is_non_executable_not_clamped():
    """A route that cannot fill the requested size is non-executable, NOT clamped down.

    Operator law: no silent clamp. A direct NO ladder with only 50 shares of depth, asked
    to fill 100, is honestly ``executable=False`` with a NO_DEPTH reason — the engine does
    NOT quietly fill 50 and report success.
    """
    space = _three_bin_space()
    markets = {
        "b25": _market_book("b25", neg_risk=True, yes_ask="0.50", no_ask="0.80", depth="50"),
        "b_low": _market_book("b_low", neg_risk=True, yes_ask="0.20", no_ask="0.85"),
        "b_high": _market_book("b_high", neg_risk=True, yes_ask="0.30", no_ask="0.75"),
    }
    fb = _family(markets, space)
    routes = build_negrisk_route_set(fb, shares=Decimal("100"))

    dn = routes.direct_no["b25"]
    assert dn.executable is False
    assert dn.reason is not None and "NO_DEPTH" in dn.reason
    # The synthetic basket (deep sibling YES) still fills, so best_no_route picks IT.
    assert routes.synthetic_not_i["b25"].executable is True
    assert routes.best_no_route("b25").route_type == "SYNTHETIC_NOT_I_YES_BASKET"


def test_non_positive_shares_refused():
    """A non-positive requested size fails closed (no route can be priced)."""
    space = _three_bin_space()
    markets = {
        "b25": _market_book("b25", neg_risk=True, yes_ask="0.50", no_ask="0.80"),
        "b_low": _market_book("b_low", neg_risk=True, yes_ask="0.20", no_ask="0.85"),
        "b_high": _market_book("b_high", neg_risk=True, yes_ask="0.30", no_ask="0.75"),
    }
    fb = _family(markets, space)
    with pytest.raises(NegRiskRouteError):
        build_negrisk_route_set(fb, shares=Decimal("0"))


def _repo_root() -> str:
    """The worktree repo root (two levels up from tests/execution/)."""
    import os

    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", ".."))
