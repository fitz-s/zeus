# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/execution/liquidation_value.py" block lines 906-941: PositionVector
#   910-914, LiquidationRoute 916-922, LiquidationDecision 924-928, the direct/convert/
#   hold exit algorithm 930-938, and the line-940 contract that the current single-token
#   ExitIntent/place_sell_order path is ONE route under the engine, NOT the exit
#   authority) reconciled against docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md
#   (GREENFIELD; DIRECT_SELL + HOLD_TO_REDEEM real).
"""RED-on-revert contract tests for the LiquidationValueEngine (Stage 10).

Three spec-named tests, each of which FAILS if the corrected transformation is reverted
to the broken behavior the spec replaces:

  * ``test_direct_sell_is_one_route_not_authority`` — exit value is the MAX over the
    executable routes, NOT the direct sell of the current token. RED-on-revert: if
    DIRECT_SELL were treated as the exit authority (the live ExitIntent/place_sell_order
    behavior the spec replaces at line 940), the engine would return the direct route
    even when HOLD_TO_REDEEM is worth more. The test builds a family whose direct bid sell
    is a deep-discount loss while hold-to-redeem under the joint q is worth more, and
    asserts the chosen route is HOLD_TO_REDEEM (direct is recorded only as an
    alternative).

  * ``test_hold_to_redeem_selected_when_all_sell_routes_worse`` — when every SELL route
    is worth less than holding to resolution, the chosen route is HOLD_TO_REDEEM.
    RED-on-revert: if the engine fell back to direct sell whenever a sell was possible,
    it would liquidate at the
    deep-discount bid instead of holding the winning leg. The test asserts hold is chosen
    when the direct bid sell is far below the redeem value.
"""
from __future__ import annotations

from decimal import Decimal

import numpy as np
import pytest

from src.execution.family_book import (
    ExecutableLadder,
    FamilyBook,
    MarketBook,
    build_family_book,
)
from src.execution.liquidation_value import (
    LiquidationValueEngine,
    PositionVector,
    direct_sell_value,
    hold_to_redeem_value,
    liquidation_decision,
    position_vectors_from_portfolio,
)
from src.probability.event_resolution import EventResolution, event_resolution_for_city
from src.probability.joint_q import JointQ
from src.probability.outcome_space import (
    OutcomeBin,
    OutcomeSpace,
    compute_topology_hash,
)
from src.strategy.live_inference.executable_cost import QuoteLevel

from datetime import date, datetime, timezone

from src.config import City


_CAPTURED = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures — a real complete Omega, real MarketBooks with controllable bid
# ladders, and a real JointQ over the same Omega.
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


def _bin(bin_id: str, lo, hi, label: str, rule: str, *, executable: bool = True) -> OutcomeBin:
    return OutcomeBin(
        bin_id=bin_id,
        condition_id=f"cond-{bin_id}",
        label=label,
        lower_native=lo,
        upper_native=hi,
        yes_token_id=f"yes-{bin_id}",
        no_token_id=f"no-{bin_id}",
        executable=executable,
        rounding_rule=rule,
    )


def _bins(rule: str) -> tuple[OutcomeBin, ...]:
    """A small complete °C partition: (-inf,21], 22, 23, [24,+inf).

    Two tradeable middle bins (b22, b23) plus the two non-executable shoulders, so the
    Omega is a valid complete MECE family and the joint q has 4 aligned masses.
    """
    return (
        _bin("b_low", None, 21.0, "21°C or below", rule, executable=False),
        _bin("b22", 22.0, 22.0, "22°C", rule),
        _bin("b23", 23.0, 23.0, "23°C", rule),
        _bin("b_high", 24.0, None, "24°C or above", rule, executable=False),
    )


def _outcome_space(family_id: str = "tokyo-high") -> OutcomeSpace:
    resolution = _resolution()
    rule = resolution.rounding_rule
    bins = _bins(rule)
    space = OutcomeSpace(
        family_id=family_id,
        resolution=resolution,
        bins=bins,
        topology_hash=compute_topology_hash(family_id, resolution, bins),
    )
    space.validate()
    return space


def _ladder(side: str, levels: tuple[tuple[str, str], ...]) -> ExecutableLadder:
    return ExecutableLadder(
        levels=tuple(QuoteLevel(Decimal(p), Decimal(s)) for p, s in levels),
        side=side,  # type: ignore[arg-type]
        fee_rate=0.0,  # zero fee in tests so realized bid value is exactly the price.
        min_tick_size=Decimal("0.01"),
        min_order_size=Decimal("1.0"),
    )


def _market_book(
    bin_id: str,
    *,
    yes_bid: str,
    no_bid: str,
    neg_risk: bool = False,
) -> MarketBook:
    """A MarketBook whose YES/NO bid ladders sit at controllable prices.

    The asks are placeholders (never walked by a sell); the bids carry the price a
    direct sell realizes. Generous size so a small share sell fills at the top level.
    """
    return MarketBook(
        condition_id=f"cond-{bin_id}",
        bin_id=bin_id,
        yes_token_id=f"yes-{bin_id}",
        no_token_id=f"no-{bin_id}",
        yes_asks=_ladder("ask", (("0.99", "1000"),)),
        yes_bids=_ladder("bid", ((yes_bid, "1000"),)),
        no_asks=_ladder("ask", (("0.99", "1000"),)),
        no_bids=_ladder("bid", ((no_bid, "1000"),)),
        neg_risk=neg_risk,
    )


def _family_book(markets: dict[str, MarketBook]) -> FamilyBook:
    omega = _outcome_space()
    return build_family_book(
        omega=omega,
        markets=markets,
        captured_at_utc=_CAPTURED,
    )


def _joint_q(masses: dict[str, float]) -> JointQ:
    """A real JointQ over the test Omega with the given per-bin masses (auto-normalized)."""
    omega = _outcome_space()
    q = np.array([masses[b.bin_id] for b in omega.bins], dtype=float)
    q = q / q.sum()
    q_by_bin_id = {b.bin_id: float(m) for b, m in zip(omega.bins, q)}
    jq = JointQ(
        omega=omega,
        q=q,
        q_by_bin_id=q_by_bin_id,
        predictive_distribution_id="pd-test",
        q_source="SETTLEMENT_STATION_NORMAL_V1",
        q_sum=float(q.sum()),
        identity_hash="qhash-test",
    )
    jq.assert_valid()
    return jq


def _yes_payoff(bin_id: str) -> np.ndarray:
    """Arrow-Debreu payoff row for a buy_yes leg on ``bin_id`` (1.0 on its own bin)."""
    omega = _outcome_space()
    return np.array([1.0 if b.bin_id == bin_id else 0.0 for b in omega.bins], dtype=float)


def _no_payoff(bin_id: str) -> np.ndarray:
    """Arrow-Debreu payoff row for a buy_no leg on ``bin_id`` (1.0 on every OTHER bin)."""
    omega = _outcome_space()
    return np.array([0.0 if b.bin_id == bin_id else 1.0 for b in omega.bins], dtype=float)


# ---------------------------------------------------------------------------
# SPEC RED-on-revert #1 — DIRECT_SELL is one route, not the exit authority.
# ---------------------------------------------------------------------------

def test_direct_sell_is_one_route_not_authority():
    """Exit value is max over executable routes; DIRECT_SELL is NOT the authority.

    The defect this replaces (spec line 940): the live exit path builds an ExitIntent for
    the CURRENT token and calls place_sell_order — i.e. the direct sell of that one
    position IS the exit. The corrected transform makes DIRECT_SELL one route among three,
    chosen only when it is the argmax.

    Scenario: a single buy_yes leg on b22. The native YES bid is a deep-discount 0.10
    (direct sell realizes 0.10 * shares), but the joint q puts 0.80 mass on b22, so
    hold-to-redeem is worth 0.80 * shares — far more. If DIRECT_SELL were the exit
    authority the engine would return the 0.10 direct route; the corrected transform
    chooses HOLD_TO_REDEEM.

    RED-on-revert: revert the argmax to "always return the direct route" (or score routes
    without comparing direct to hold) and the chosen route flips back to DIRECT_SELL —
    this assertion fails.
    """
    shares = Decimal("100")
    markets = {"b22": _market_book("b22", yes_bid="0.10", no_bid="0.90")}
    fb = _family_book(markets)
    jq = _joint_q({"b_low": 0.05, "b22": 0.80, "b23": 0.10, "b_high": 0.05})

    position = PositionVector(
        family_id="tokyo-high",
        quantities_by_instrument={"b22": shares},
        payoff_vector_by_instrument={"b22": _yes_payoff("b22")},
        directions_by_instrument={"b22": "buy_yes"},
    )

    # Sanity: the two route values are as engineered (direct deep-discount < hold).
    direct = direct_sell_value(position, fb)
    hold = hold_to_redeem_value(position, jq)
    assert direct.route_type == "DIRECT_SELL" and direct.executable
    assert hold.route_type == "HOLD_TO_REDEEM" and hold.executable
    assert direct.value_usd == pytest.approx(Decimal("10.0"))  # 100 * 0.10
    assert float(hold.value_usd) == pytest.approx(80.0)        # 100 * 0.80
    assert hold.value_usd > direct.value_usd

    decision = liquidation_decision(position, family_book=fb, joint_q=jq)

    # The corrected transform: HOLD_TO_REDEEM is chosen because it is the max — direct is
    # NOT the authority. Direct survives only as an alternative on the receipt.
    assert decision.chosen.route_type == "HOLD_TO_REDEEM"
    assert decision.chosen.value_usd > direct.value_usd
    alt_types = {r.route_type for r in decision.alternatives}
    assert "DIRECT_SELL" in alt_types
    assert decision.position_vector_hash  # receipt anchor present


# ---------------------------------------------------------------------------
# SPEC RED-on-revert #2 — HOLD_TO_REDEEM selected when direct sell is worse.
# ---------------------------------------------------------------------------

def test_hold_to_redeem_selected_when_all_sell_routes_worse():
    """Hold to resolution is chosen when every sell route is worth less.

    Scenario: a family with two held legs (buy_yes on b22, buy_yes on b23). Both native
    YES bids are deep-discount (0.05 each), so the direct sell realizes only 0.05 * shares
    per leg. The joint q puts the mass on b22 (0.70) and b23 (0.20), so hold-to-redeem is
    worth 0.70 + 0.20 = far more than the 0.05 + 0.05 sell. Direct sell is worse than
    holding, and the engine chooses HOLD_TO_REDEEM.

    RED-on-revert: if the engine fell back to the direct sell whenever a sell was possible
    (the per-token place_sell_order behavior), it would liquidate the winning legs at the
    0.05 bid. The test asserts HOLD_TO_REDEEM wins.
    """
    shares = Decimal("100")
    markets = {
        "b22": _market_book("b22", yes_bid="0.05", no_bid="0.95"),
        "b23": _market_book("b23", yes_bid="0.05", no_bid="0.95"),
    }
    fb = _family_book(markets)
    jq = _joint_q({"b_low": 0.05, "b22": 0.70, "b23": 0.20, "b_high": 0.05})
    position = PositionVector(
        family_id="tokyo-high",
        quantities_by_instrument={"b22": shares, "b23": shares},
        payoff_vector_by_instrument={
            "b22": _yes_payoff("b22"),
            "b23": _yes_payoff("b23"),
        },
        directions_by_instrument={"b22": "buy_yes", "b23": "buy_yes"},
    )

    direct = direct_sell_value(position, fb)
    hold = hold_to_redeem_value(position, jq)
    # Direct sell realizes ~0.05*100 + 0.05*100 = 10; hold realizes ~0.70*100 + 0.20*100 = 90.
    assert float(direct.value_usd) == pytest.approx(10.0)
    assert float(hold.value_usd) == pytest.approx(90.0)
    assert hold.value_usd > direct.value_usd

    decision = liquidation_decision(position, family_book=fb, joint_q=jq)
    assert decision.chosen.route_type == "HOLD_TO_REDEEM"
    # Direct sell is strictly worse and recorded as an alternative.
    alt_types = {r.route_type for r in decision.alternatives}
    assert "DIRECT_SELL" in alt_types


# ---------------------------------------------------------------------------
# Supporting contract — the position vector is CREATED by family_key grouping.
# ---------------------------------------------------------------------------

def test_position_vector_assembled_by_family_key_grouping():
    """position_vectors_from_portfolio groups positions into ONE vector per family.

    The family position-vector does NOT exist at the exit site today (drift ledger MAJOR
    :33). This asserts it is CREATED by grouping on the family_exclusive_dedup family key:
    two positions in the SAME (city, date, metric) family land in ONE PositionVector with
    both instruments; a position in a different family is a separate vector.
    """
    class _Pos:
        def __init__(self, bin_id, shares, direction):
            self.city = "Tokyo"
            self.target_date = "2026-06-14"
            self.temperature_metric = "high"
            self.market_family_id = "tokyo-high"
            self.bin_id = bin_id
            self.shares = shares
            self.direction = direction

    class _OtherPos(_Pos):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.city = "Paris"
            self.market_family_id = "paris-high"

    payoffs = {"b22": _yes_payoff("b22"), "b23": _yes_payoff("b23")}
    positions = [
        _Pos("b22", Decimal("100"), "buy_yes"),
        _Pos("b23", Decimal("50"), "buy_yes"),
        _OtherPos("b22", Decimal("10"), "buy_yes"),
    ]
    vectors = position_vectors_from_portfolio(
        positions, payoff_vectors_by_instrument=payoffs
    )
    # Two distinct families → two vectors.
    assert len(vectors) == 2
    tokyo = [v for v in vectors.values() if v.family_id.startswith("Tokyo")][0]
    assert set(tokyo.quantities_by_instrument.keys()) == {"b22", "b23"}
    assert tokyo.quantities_by_instrument["b22"] == Decimal("100")
    assert tokyo.quantities_by_instrument["b23"] == Decimal("50")
