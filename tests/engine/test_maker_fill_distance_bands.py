# Created: 2026-09-17
# Last reused/audited: 2026-09-30
# Authority basis: distance-conditioned maker fill bands; thin early market
#   maker price menu (one near-edge price per band, operator law 2026-09-30).
"""A maker fill probability that ignores distance turns the objective into an edge sort.

EV = p_fill x edge. With p_fill constant the ranking depends only on edge, so the winner is
always the most extreme longshot — the quote least likely to ever fill. Measured on the live
book that is exactly what happened: every winner rested in a book whose spread was 22x the
market median, and 46 of 46 entries went unfilled.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.contracts.probability_arithmetic import Z_ONE_SIDED_95, Z_TWO_SIDED_95, wilson_lower_bound
from src.engine.global_batch_runtime import (
    _CurrentMakerFillSample,
    _maker_fill_distance_band,
    _monotone_band_bounds,
)


def test_bands_order_by_distance():
    assert _maker_fill_distance_band(Decimal("0.01")) == 0
    assert _maker_fill_distance_band(Decimal("0.02")) == 0
    assert _maker_fill_distance_band(Decimal("0.03")) == 1
    assert _maker_fill_distance_band(Decimal("0.10")) == 2
    assert _maker_fill_distance_band(Decimal("0.30")) == 3
    assert _maker_fill_distance_band(Decimal("0.90")) == 4


def test_wilson_preserves_the_ordering_dkw_erased():
    """The measured band counts, whose ordering the pooled DKW radius destroyed."""
    bounds = [
        wilson_lower_bound(43, 182, z=Z_TWO_SIDED_95),
        wilson_lower_bound(25, 112, z=Z_TWO_SIDED_95),
        wilson_lower_bound(7, 51, z=Z_TWO_SIDED_95),
        wilson_lower_bound(2, 71, z=Z_TWO_SIDED_95),
        wilson_lower_bound(0, 62, z=Z_TWO_SIDED_95),
    ]
    assert bounds == sorted(bounds, reverse=True), bounds
    assert bounds[0] > 0.1, "the nearest band must keep a usable rate"
    assert bounds[-1] == 0.0, "62 rests with no fill is a measurement, not noise"


def test_a_band_that_never_filled_reads_as_zero_not_as_the_pooled_rate():
    sample = _CurrentMakerFillSample(
        action="BUY",
        fill_fractions=tuple(Decimal("1") for _ in range(40)),
        fill_probability_lcb=Decimal("0.0699"),
        sample_identity="test",
        training_cutoff_at_utc=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
        rest_deadline_minutes=20.0,
        fill_probability_lcb_by_band=((0, Decimal("0.1775")), (4, Decimal("0"))),
    )
    assert sample.band_fill_probability_lcb(Decimal("0.01")) == Decimal("0.1775")
    assert sample.band_fill_probability_lcb(Decimal("0.90")) == Decimal("0")


def test_a_band_with_no_rests_has_no_bound_not_the_pooled_one():
    """A distance never rested at has no evidence: no bound, so no proposal."""
    sample = _CurrentMakerFillSample(
        action="BUY",
        fill_fractions=tuple(Decimal("1") for _ in range(40)),
        fill_probability_lcb=Decimal("0.0699"),
        sample_identity="test",
        training_cutoff_at_utc=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
        rest_deadline_minutes=20.0,
        fill_probability_lcb_by_band=((0, Decimal("0.1775")),),
    )
    assert sample.band_fill_probability_lcb(Decimal("0.30")) == Decimal("0")


@pytest.mark.parametrize("trials", [0, -1])
def test_no_trials_is_no_bound(trials):
    assert wilson_lower_bound(0, trials, z=Z_TWO_SIDED_95) == 0.0


def _sample(bands):
    import datetime as _dt

    return _CurrentMakerFillSample(
        action="BUY",
        fill_fractions=tuple(Decimal("1") for _ in range(40)),
        fill_probability_lcb=Decimal("0.0699"),
        sample_identity="test",
        training_cutoff_at_utc=_dt.datetime.now(_dt.timezone.utc),
        rest_deadline_minutes=20.0,
        fill_probability_lcb_by_band=bands,
    )


def test_a_zero_rate_band_withdraws_the_maker_proposal_instead_of_stating_zero():
    """A zero-probability outcome is rejected by MakerFillOutcome and would fail the auction.

    The honest statement for a distance that never filled is that no maker witness exists, so
    the taker competes alone. Live 2026-09-17: emitting the zero instead produced 32
    `GLOBAL_AUCTION_FAILED:ValueError:maker fill outcome is invalid` in 30 minutes.
    """
    from src.engine.global_batch_runtime import _maker_fill_outcomes

    sample = _sample(((0, Decimal("0.1775")), (4, Decimal("0"))))
    assert _maker_fill_outcomes(
        sample, limit_price=Decimal("0.09"), counterparty_price=Decimal("0.99")
    ) == ()


def test_a_reachable_band_still_states_its_distribution():
    from src.engine.global_batch_runtime import _maker_fill_outcomes

    sample = _sample(((0, Decimal("0.1775")), (4, Decimal("0"))))
    outcomes = _maker_fill_outcomes(
        sample, limit_price=Decimal("0.50"), counterparty_price=Decimal("0.51")
    )
    assert outcomes
    assert sum(row.probability for row in outcomes) == Decimal("1")
    assert all(row.probability > 0 for row in outcomes)


def test_without_a_counterparty_price_the_pooled_bound_still_applies():
    from src.engine.global_batch_runtime import _maker_fill_outcomes

    sample = _sample(((4, Decimal("0")),))
    outcomes = _maker_fill_outcomes(sample, limit_price=Decimal("0.50"))
    assert outcomes
    assert sum(row.probability for row in outcomes) == Decimal("1")


@pytest.mark.parametrize("trials", [24, 25, 28, 35, 48, 50, 63, 100, 250])
def test_a_band_with_no_fill_is_exactly_zero_not_float_residue(trials):
    """0/48 read as 6.938893903907228e-18 live and minted a maker witness from it."""
    assert wilson_lower_bound(0, trials, z=Z_TWO_SIDED_95) == 0.0


def _menu(bid, ask, tick="0.01"):
    from src.engine.global_batch_runtime import _MAKER_FILL_DISTANCE_BAND_EDGES
    from src.solve.solver import maker_buy_price_menu

    return maker_buy_price_menu(
        best_bid=None if bid is None else Decimal(bid),
        best_ask=Decimal(ask),
        tick=Decimal(tick),
        band_edges=_MAKER_FILL_DISTANCE_BAND_EDGES,
        band_of=_maker_fill_distance_band,
    )


@pytest.mark.parametrize(
    ("bid", "ask", "tick", "expected"),
    (
        # Each band's near edge ceil_tick(ask - previous_edge) - tick, clipped into
        # (bid, ask); band 3's 0.24 falls under the bid and leaves its band.
        ("0.30", "0.40", "0.01", ("0.39", "0.37", "0.34")),
        # Thin early book with no bid: every band is reachable at its near edge.
        (None, "0.40", "0.01", ("0.39", "0.37", "0.34", "0.24")),
        # The live band ceiling 0.95 binds hi; 0.94 / 0.91 / 0.81 are near edges.
        (None, "0.97", "0.01", ("0.95", "0.94", "0.91", "0.81")),
        # A bid below the band floor never lowers lo under 0.05.
        ("0.02", "0.90", "0.001", ("0.899", "0.879", "0.849", "0.749")),
        # Clipping to lo can leave a band: 0.05 on a 0.06 ask is band 0 only.
        (None, "0.06", "0.01", ("0.05",)),
        # One-tick spread: no price strictly inside it.
        ("0.39", "0.40", "0.01", ()),
    ),
)
def test_maker_menu_is_one_near_edge_price_per_reachable_band(bid, ask, tick, expected):
    menu = _menu(bid, ask, tick)
    assert menu == tuple(Decimal(price) for price in expected)
    assert len(set(menu)) == len(menu)
    for price in menu:
        assert Decimal("0.05") <= price <= Decimal("0.95")
        assert price < Decimal(ask)
        assert bid is None or price > Decimal(bid)
    bands = [_maker_fill_distance_band(Decimal(ask) - price) for price in menu]
    assert bands == sorted(set(bands))


def test_maker_menu_near_edge_is_the_band_price_nearest_the_ask():
    """A band's bound is measured across the band and fill odds fall with distance, so
    the bound holds only at the band's nearest price: one tick nearer leaves the band."""

    ask = Decimal("0.40")
    for price in _menu(None, "0.40"):
        band = _maker_fill_distance_band(ask - price)
        nearer = price + Decimal("0.01")
        assert nearer >= ask or _maker_fill_distance_band(ask - nearer) != band


def _live_shaped_conn(bands):
    """A venue-command fixture whose BUY rests land in the given (distance, fills, n) bands."""

    import datetime as _dt
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE venue_commands (command_id TEXT PRIMARY KEY, envelope_id TEXT,
          snapshot_id TEXT, intent_kind TEXT, side TEXT, size REAL, price REAL,
          venue_order_id TEXT, state TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE venue_submission_envelopes (envelope_id TEXT PRIMARY KEY,
          order_type TEXT, post_only INTEGER);
        CREATE TABLE executable_market_snapshots (snapshot_id TEXT PRIMARY KEY,
          orderbook_top_bid TEXT, orderbook_top_ask TEXT, min_tick_size TEXT,
          authority_tier TEXT, wide_spread_display_substitution INTEGER);
        CREATE TABLE venue_order_facts (fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
          command_id TEXT, matched_size TEXT, observed_at TEXT);
        INSERT INTO executable_market_snapshots VALUES ('no-bid', 'ABSENT', '0.90', '0.01', 'CLOB', 0);
        """
    )
    cut = _dt.datetime(2026, 9, 30, 12, tzinfo=_dt.timezone.utc)
    created = cut - _dt.timedelta(days=1)
    index = 0
    for distance, fills, n in bands:
        price = Decimal("0.90") - Decimal(distance)
        for k in range(n):
            index += 1
            cid = f"buy-{index}"
            conn.execute("INSERT INTO venue_submission_envelopes VALUES (?, 'GTC', 1)", (f"e-{cid}",))
            conn.execute(
                "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (cid, f"e-{cid}", "no-bid", "ENTRY", "BUY", 10.0, float(price),
                 f"v-{cid}", "FILLED" if k < fills else "CANCELLED",
                 created.isoformat(), (created + _dt.timedelta(minutes=11)).isoformat()),
            )
            conn.execute(
                "INSERT INTO venue_order_facts(command_id,matched_size,observed_at) VALUES (?,?,?)",
                (cid, "10" if k < fills else "0",
                 (created + _dt.timedelta(minutes=10)).isoformat()),
            )
    return conn, cut


# Live 30-day shape (2026-09-30): band0 300 rests, band1 214, band2 219, band3 17 (2 fills).
_LIVE_SHAPE = (("0.01", 110, 300), ("0.04", 60, 214), ("0.10", 40, 219), ("0.30", 2, 17))


def test_a_thin_band_uses_its_own_wilson_bound_not_the_pooled_rate():
    from src.engine.global_batch_runtime import _load_current_maker_fill_samples

    conn, cut = _live_shaped_conn(_LIVE_SHAPE)
    sample = _load_current_maker_fill_samples(conn, selection_cut_at_utc=cut)["BUY"]
    own = Decimal(str(wilson_lower_bound(2, 17, z=Z_TWO_SIDED_95)))
    assert sample.band_fill_probability_lcb(Decimal("0.30")) == own
    assert own < Decimal("0.05") < sample.fill_probability_lcb


def test_a_band_with_zero_rows_yields_no_witness_and_no_proposal():
    import datetime as _dt
    from types import SimpleNamespace

    from src.engine import global_batch_runtime as g

    conn, cut = _live_shaped_conn(_LIVE_SHAPE[:3])  # no band-3 rests at all
    sample = g._load_current_maker_fill_samples(conn, selection_cut_at_utc=cut)["BUY"]
    assert sample.band_fill_probability_lcb(Decimal("0.30")) == Decimal("0")
    assert g._maker_fill_outcomes(
        sample, limit_price=Decimal("0.60"), counterparty_price=Decimal("0.90")
    ) == ()


@pytest.mark.parametrize(
    ("own", "expected"),
    (
        # Noise ranks band 2 above band 1: band 2 is held at band 1's bound.
        ({0: "0.30", 1: "0.20", 2: "0.25", 3: "0.05"}, ("0.30", "0.20", "0.20", "0.05")),
        # A band above everything nearer is held at the nearest envelope.
        ({0: "0.10", 3: "0.40", 4: "0.0"}, ("0.10", "0.10", "0.0")),
        # Already ordered: unchanged.
        ({0: "0.31", 1: "0.23", 2: "0.15", 3: "0.03"}, ("0.31", "0.23", "0.15", "0.03")),
    ),
)
def test_band_bounds_come_out_non_increasing_in_distance(own, expected):
    bounds = _monotone_band_bounds({band: Decimal(v) for band, v in own.items()})
    assert tuple(str(bound) for _band, bound in bounds) == expected
    values = [bound for _band, bound in bounds]
    assert values == sorted(values, reverse=True)
    assert [band for band, _ in bounds] == sorted(own)


def test_no_bid_book_prices_its_band_three_rest_with_band_three_evidence():
    """The menu's farthest rest on a thin no-bid book carries its own thin evidence."""

    import datetime as _dt
    from types import SimpleNamespace

    from src.contracts.executable_cost_curve import BookLevel, ExecutableCostCurve, FeeModel
    from src.engine import global_batch_runtime as g
    from src.engine.global_auction_universe import (
        CurrentGlobalBookAsset,
        CurrentGlobalBookEpoch,
        current_global_book_epoch_identity,
    )

    conn, cut = _live_shaped_conn(_LIVE_SHAPE)
    sample = g._load_current_maker_fill_samples(conn, selection_cut_at_utc=cut)["BUY"]
    curve = ExecutableCostCurve(
        token_id="tok", side="YES", snapshot_id="snap", book_hash="hash",
        levels=(BookLevel(price=Decimal("0.60"), size=Decimal("100")),),
        fee_model=FeeModel(fee_rate=Decimal("0")), min_tick=Decimal("0.01"),
        min_order_size=Decimal("5"), quote_ttl=_dt.timedelta(seconds=30),
    )
    asset = CurrentGlobalBookAsset(
        family_key="fam", bin_id="bin", condition_id="cond", gamma_market_id="g",
        market_event_id="e", side="YES", token_id="tok", curve=curve,
        captured_at_utc=cut, neg_risk=False, bid_levels=(),
    )
    states = (("fam", "bin", "cond", "YES", "tok", "EXECUTABLE", "hash", "e", "g", "False"),)
    epoch = CurrentGlobalBookEpoch(
        assets=(asset,), asset_states=states, captured_at_utc=cut,
        max_age=_dt.timedelta(seconds=30),
        witness_identity=current_global_book_epoch_identity(asset_states=states, captured_at_utc=cut),
    )
    from src.engine.qkernel_spine_bridge import PreparedGlobalFamily

    prepared = PreparedGlobalFamily(
        decision_id="decision",
        probability_witness=SimpleNamespace(family_key="fam"),
        candidate_seeds=(),
    )
    rebound, _epoch = g._bind_current_maker_fill_witnesses(
        {"event": prepared}, book_epoch=epoch,
        wealth_witness=SimpleNamespace(ledger_snapshot_id="ledger", spendable_cash_usd=Decimal("12")),
        samples={"BUY": sample}, issued_at_utc=cut,
    )
    by_limit = {key[5]: witness for key, witness in rebound["event"].maker_fill_witnesses.items()}
    # Ask 0.60, no bid: band-3 near edge ceil(0.60 - 0.15) - 0.01 = 0.44.
    band_three = by_limit[Decimal("0.44")]
    own = Decimal(str(wilson_lower_bound(2, 17, z=Z_TWO_SIDED_95)))
    assert band_three.fill_probability == pytest.approx(float(own))
    band_two = by_limit[Decimal("0.54")]
    assert band_three.fill_probability < band_two.fill_probability
