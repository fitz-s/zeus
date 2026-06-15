# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/execution/family_book.py" block lines 619-650: ExecutableLadder
#   623-629, MarketBook 631-642, FamilyBook 643-650; Stage 7 block 1146-1164 — the
#   executable family route surface over all sibling markets, native cost stays leaf)
#   reconciled against docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md
#   (GREENFIELD ONLY — no live edits; executable_cost is ALREADY leaf-only and is
#   REUSED unchanged; neg_risk threaded per market from snapshot.neg_risk; complete_book
#   true only when every Omega bin has a MarketBook).
"""RED-on-revert contract tests for the family route surface (Stage 7b family_book).

Two spec-named tests fail if the corrected transformation is reverted to the broken
behavior the spec replaces:

  * ``test_family_book_complete_requires_all_sibling_markets`` — ``complete_book`` is
    TRUE iff EVERY bin of the COMPLETE Omega (including the non-executable tail/shoulder
    bins) has a ``MarketBook``. RED-on-revert: if completeness regresses to "has at least
    one market", "covers the EXECUTABLE subset" (drops the tail bins from the
    denominator), or a hardcoded ``True``, a family that is missing a sibling — or that
    only covers the tradeable middle — would be called complete. A NO basket / full-YES
    basket arb priced on such a surface silently omits a sibling leg and is mispriced.
    The test asserts complete is false while ANY sibling is absent, flips to true at the
    exact moment the last sibling (including a non-executable tail bin) is added, and that
    a passed ``complete_book`` disagreeing with the structural coverage is refused.

  * ``test_family_book_threads_neg_risk_per_market`` — ``neg_risk`` is a PER-MARKET venue
    fact threaded onto each ``MarketBook`` from its own ``ExecutableMarketSnapshot.neg_risk``.
    RED-on-revert: if neg_risk is collapsed to a single family-wide scalar (e.g. read off
    the first/any market and applied to all), a family with mixed per-market neg_risk loses
    the distinction the Stage 7 NO route rule branches on ("if negRisk=False only direct NO;
    if negRisk=True compare direct NO vs synthetic sibling-YES basket"). The test builds a
    family with DIFFERING per-market neg_risk and asserts each MarketBook (and the
    NativeQuoteBook it hands the leaf walker) carries ITS OWN flag — so a single-scalar
    revert, which would make them all equal, fails.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

import pytest

from src.config import City
from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot
from src.execution.family_book import (
    ExecutableLadder,
    FamilyBook,
    FamilyBookError,
    MarketBook,
    build_family_book,
    compute_book_hash,
    family_book_from_snapshots,
    market_book_from_snapshot,
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
# Fixtures — a real complete Omega (built the SAME way the joint_q / instruments
# contract tests build it) and real ExecutableMarketSnapshot rows per sibling.
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


def _complete_bins(rule: str) -> tuple[OutcomeBin, ...]:
    """A complete °C integer partition: (-inf,20], 21..29, [30,+inf).

    The two shoulder bins (b_low / b_high) are executable=False — they are KEPT in the
    family so the partition stays complete. complete_book must require a MarketBook for
    THESE too (the full Omega), not only the tradeable middle.
    """
    bins = [_bin("b_low", None, 20.0, "20°C or below", rule, executable=False)]
    for t in range(21, 30):
        bins.append(_bin(f"b{t}", float(t), float(t), f"{t}°C", rule))
    bins.append(_bin("b_high", 30.0, None, "30°C or above", rule, executable=False))
    return tuple(bins)


def _outcome_space(family_id: str = "tokyo-high") -> OutcomeSpace:
    resolution = _resolution()
    rule = resolution.rounding_rule
    bins = _complete_bins(rule)
    space = OutcomeSpace(
        family_id=family_id,
        resolution=resolution,
        bins=bins,
        topology_hash=compute_topology_hash(family_id, resolution, bins),
    )
    space.validate()
    return space


def _depth_jsonb() -> str:
    """A small two-sided native YES/NO depth payload keyed by outcome label.

    Shape consumed by quote_book_from_executable_snapshot via _depth_for_token_or_label:
    a dict keyed by "YES"/"NO" each with native "asks"/"bids" price/size levels.
    """
    return json.dumps(
        {
            "YES": {
                "asks": [{"price": "0.40", "size": "500"}, {"price": "0.41", "size": "500"}],
                "bids": [{"price": "0.39", "size": "500"}, {"price": "0.38", "size": "500"}],
            },
            "NO": {
                "asks": [{"price": "0.60", "size": "500"}, {"price": "0.61", "size": "500"}],
                "bids": [{"price": "0.59", "size": "500"}, {"price": "0.58", "size": "500"}],
            },
        }
    )


def _snapshot(bin_id: str, *, neg_risk: bool) -> ExecutableMarketSnapshot:
    """A real per-sibling ExecutableMarketSnapshot carrying its OWN neg_risk."""
    return ExecutableMarketSnapshot(
        snapshot_id=f"snap-{bin_id}",
        gamma_market_id=f"gamma-{bin_id}",
        event_id="event-tokyo-high",
        event_slug="tokyo-high",
        condition_id=f"cond-{bin_id}",
        question_id=f"q-{bin_id}",
        yes_token_id=f"yes-{bin_id}",
        no_token_id=f"no-{bin_id}",
        selected_outcome_token_id=None,
        outcome_label=None,
        enable_orderbook=True,
        active=True,
        closed=False,
        accepting_orders=True,
        market_start_at=None,
        market_end_at=None,
        market_close_at=None,
        sports_start_at=None,
        min_tick_size=Decimal("0.01"),
        min_order_size=Decimal("1.0"),
        fee_details={"fee_rate_fraction": 0.05},
        token_map_raw={"YES": f"yes-{bin_id}", "NO": f"no-{bin_id}"},
        rfqe=None,
        neg_risk=neg_risk,
        orderbook_top_bid=Decimal("0.39"),
        orderbook_top_ask=Decimal("0.40"),
        orderbook_depth_jsonb=_depth_jsonb(),
        raw_gamma_payload_hash="a" * 64,
        raw_clob_market_info_hash="b" * 64,
        raw_orderbook_hash="c" * 64,
        authority_tier="CLOB",
        captured_at=_CAPTURED,
        freshness_deadline=_CAPTURED.replace(minute=1),
    )


def _ladder(side: str) -> ExecutableLadder:
    return ExecutableLadder(
        levels=(QuoteLevel(Decimal("0.40"), Decimal("500")),),
        side=side,  # type: ignore[arg-type]
        fee_rate=0.05,
        min_tick_size=Decimal("0.01"),
        min_order_size=Decimal("1.0"),
    )


def _market_book(bin_id: str, *, neg_risk: bool) -> MarketBook:
    return MarketBook(
        condition_id=f"cond-{bin_id}",
        bin_id=bin_id,
        yes_token_id=f"yes-{bin_id}",
        no_token_id=f"no-{bin_id}",
        yes_asks=_ladder("ask"),
        yes_bids=_ladder("bid"),
        no_asks=_ladder("ask"),
        no_bids=_ladder("bid"),
        neg_risk=neg_risk,
    )


# ---------------------------------------------------------------------------
# SPEC RED-on-revert #1: complete_book requires EVERY sibling market in the Omega.
# ---------------------------------------------------------------------------

def test_family_book_complete_requires_all_sibling_markets():
    """complete_book is TRUE iff every bin of the COMPLETE Omega has a MarketBook.

    The load-bearing contract (spec lines 643-650): ``complete_book`` is the structural
    set-equality between the per-sibling MarketBooks and the FULL Omega — including the
    non-executable tail/shoulder bins kept so the partition is complete. A family missing
    even one sibling is NOT complete.

    RED-on-revert: if completeness regresses to "has at least one market", to "covers the
    executable (tradeable) subset" (dropping the tail bins from the denominator), or a
    hardcoded ``True``, then an incomplete family — or one that only covers the tradeable
    middle — would be called complete and a NO / full-YES basket arb would silently omit a
    sibling leg. This test:

      1. asserts complete is FALSE while ANY sibling is absent (covering only the
         tradeable middle is NOT complete — the tail bins count);
      2. asserts complete flips to TRUE only when the LAST sibling (a non-executable tail
         bin) is added — so a denominator that drops the tail would have flipped early;
      3. asserts a hand-built FamilyBook whose passed complete_book disagrees with the
         structural coverage is REFUSED (so a hardcoded True cannot stand).
    """
    space = _outcome_space()
    all_bin_ids = [b.bin_id for b in space.bins]
    tail_bin_ids = {b.bin_id for b in space.bins if not b.executable}
    assert tail_bin_ids == {"b_low", "b_high"}, "fixture: the two shoulder bins are the tail"

    # (1) Only the tradeable MIDDLE (executable bins) — every non-tail sibling present,
    #     but the two non-executable tail bins absent. This is NOT complete: complete_book
    #     measures coverage of the FULL Omega, not the executable subset. A revert that
    #     used the executable subset as the denominator would WRONGLY call this complete.
    middle_ids = [b.bin_id for b in space.bins if b.executable]
    middle_markets = {bid: _market_book(bid, neg_risk=False) for bid in middle_ids}
    fb_middle = build_family_book(
        omega=space, markets=middle_markets, captured_at_utc=_CAPTURED
    )
    assert fb_middle.complete_book is False, (
        "covering only the executable (tradeable) middle is NOT a complete book — the "
        "non-executable tail bins are siblings of the family and must each have a MarketBook"
    )
    assert set(fb_middle.missing_bin_ids()) == tail_bin_ids

    # Build up sibling-by-sibling: complete stays FALSE for every proper subset.
    markets: dict[str, MarketBook] = {}
    for idx, bid in enumerate(all_bin_ids[:-1]):
        markets[bid] = _market_book(bid, neg_risk=False)
        fb = build_family_book(omega=space, markets=dict(markets), captured_at_utc=_CAPTURED)
        assert fb.complete_book is False, (
            f"after {idx + 1}/{len(all_bin_ids)} siblings the book must NOT be complete"
        )
        assert len(fb.missing_bin_ids()) == len(all_bin_ids) - (idx + 1)

    # (2) Add the LAST sibling (b_high — a non-executable tail bin). ONLY now is the book
    #     complete. A denominator that dropped the tail would have flipped to complete
    #     before this final tail market was added — this catches that revert.
    last = all_bin_ids[-1]
    assert last in tail_bin_ids, "fixture: the last bin added is a non-executable tail bin"
    markets[last] = _market_book(last, neg_risk=False)
    fb_full = build_family_book(omega=space, markets=dict(markets), captured_at_utc=_CAPTURED)
    assert fb_full.complete_book is True, "every Omega bin now has a MarketBook -> complete"
    assert fb_full.missing_bin_ids() == ()
    assert set(fb_full.markets.keys()) == set(all_bin_ids)

    # (3) A hand-built FamilyBook whose passed complete_book disagrees with the actual
    #     structural coverage is REFUSED — so a hardcoded True (the crudest revert) cannot
    #     stand: complete_book is forced to equal the membership.
    with pytest.raises(FamilyBookError):
        FamilyBook(
            omega=space,
            markets=dict(middle_markets),  # tail bins missing
            captured_at_utc=_CAPTURED,
            book_hash=compute_book_hash(
                omega=space, markets=middle_markets, captured_at_utc=_CAPTURED
            ),
            complete_book=True,  # the lie: claims complete while two siblings are absent
        )
    # And the structurally-correct hand build (complete_book=False) is accepted.
    FamilyBook(
        omega=space,
        markets=dict(middle_markets),
        captured_at_utc=_CAPTURED,
        book_hash=compute_book_hash(
            omega=space, markets=middle_markets, captured_at_utc=_CAPTURED
        ),
        complete_book=False,
    )

    # A market for a bin NOT in the Omega is a stranger — refused (so complete is a true
    # "every sibling present and no stranger" statement).
    stranger = dict(middle_markets)
    stranger["b_not_in_omega"] = _market_book("b_not_in_omega", neg_risk=False)
    with pytest.raises(FamilyBookError):
        build_family_book(omega=space, markets=stranger, captured_at_utc=_CAPTURED)


# ---------------------------------------------------------------------------
# SPEC RED-on-revert #2: neg_risk is threaded PER MARKET, not a family-wide scalar.
# ---------------------------------------------------------------------------

def test_family_book_threads_neg_risk_per_market():
    """Each MarketBook carries its OWN neg_risk, threaded from its snapshot.neg_risk.

    The load-bearing contract (spec line 641; Stage 7 NO route rule): neg_risk is a
    PER-MARKET venue fact. The route rule branches on it per sibling ("if negRisk=False
    only direct NO; if negRisk=True compare direct NO vs the synthetic sibling-YES
    basket"), so the flag must live on each MarketBook — never one family-wide scalar.

    RED-on-revert: if neg_risk is collapsed to a single value (read off the first/any
    market, or a family-level field) and applied to all, a family with MIXED per-market
    neg_risk loses the distinction. This test builds a family whose siblings have
    DIFFERING neg_risk and asserts:

      1. each MarketBook carries ITS OWN flag (built from that sibling's snapshot);
      2. the NativeQuoteBook each MarketBook hands the LEAF executable_cost walker carries
         that same per-market flag (so the leaf prices each leg under the right venue
         primitive);
      3. the family does NOT collapse to a single scalar — both True and False are present
         across the siblings. A single-scalar revert would make them all equal, failing
         this.
    """
    space = _outcome_space()
    bin_ids = [b.bin_id for b in space.bins]

    # MIXED per-market neg_risk: alternate True / False across the siblings so a
    # single-scalar revert (all equal) is impossible to satisfy.
    expected_neg_risk = {bid: (idx % 2 == 0) for idx, bid in enumerate(bin_ids)}
    snapshots = {bid: _snapshot(bid, neg_risk=expected_neg_risk[bid]) for bid in bin_ids}

    fb = family_book_from_snapshots(
        omega=space, snapshots_by_bin_id=snapshots, captured_at_utc=_CAPTURED
    )

    # The family is complete (every sibling has a snapshot) and threads each flag through.
    assert fb.complete_book is True

    # (1) Each MarketBook carries the EXACT neg_risk of its own snapshot.
    for bid in bin_ids:
        assert fb.markets[bid].neg_risk == expected_neg_risk[bid], (
            f"MarketBook[{bid}] neg_risk must be threaded from its OWN snapshot "
            f"({expected_neg_risk[bid]!r}), not a family-wide scalar"
        )

    # (2) The leaf NativeQuoteBook each MarketBook hands executable_cost carries the same
    #     per-market flag — so the leaf walker prices each leg under the right primitive.
    for bid in bin_ids:
        native = fb.markets[bid].native_quote_book()
        assert native.neg_risk == expected_neg_risk[bid], (
            f"NativeQuoteBook for {bid} must carry the per-market neg_risk for the leaf walker"
        )

    # (3) The family genuinely has BOTH neg_risk values present — proof the per-market
    #     thread is real, and the killer of any single-scalar collapse.
    distinct = {fb.markets[bid].neg_risk for bid in bin_ids}
    assert distinct == {True, False}, (
        "the test family must carry BOTH neg_risk values across its siblings; a revert to "
        "a single family-wide scalar would force them all equal and fail here"
    )

    # The direct MarketBook builder threads the flag identically (no snapshot-only path).
    mb_true = market_book_from_snapshot(_snapshot("b25", neg_risk=True), bin_id="b25")
    mb_false = market_book_from_snapshot(_snapshot("b25", neg_risk=False), bin_id="b25")
    assert mb_true.neg_risk is True and mb_false.neg_risk is False
    assert mb_true.native_quote_book().neg_risk is True
    assert mb_false.native_quote_book().neg_risk is False


# ---------------------------------------------------------------------------
# Supporting contract checks (carrier / leaf-reuse invariants).
# ---------------------------------------------------------------------------

def test_market_book_native_quote_book_is_leaf_walkable():
    """A MarketBook reassembles the leaf NativeQuoteBook the cost walker prices against.

    Native cost STAYS LEAF: the MarketBook is a carrier; executable_cost (the leaf) does
    the only walking. This asserts the four captured ladders round-trip into the leaf book
    and the leaf walker prices a buy_no leg off them WITHOUT family_book computing a cost.
    """
    from src.contracts.execution_price import ExecutionPrice
    from src.strategy.live_inference.executable_cost import executable_cost

    snap = _snapshot("b25", neg_risk=True)
    mb = market_book_from_snapshot(snap, bin_id="b25")
    native = mb.native_quote_book()

    # Ladders round-trip from the snapshot depth.
    assert native.no_asks[0].price == Decimal("0.60")
    assert native.yes_asks[0].price == Decimal("0.40")
    assert native.neg_risk is True

    # The leaf walker prices a buy_no leg off the carrier's book (leaf-only cost path).
    # The price_type / fee_deducted labels are the LEAF's (a buy applies the taker fee
    # via with_taker_fee) — family_book asserts only that the leaf did the walking.
    price = executable_cost(native, direction="buy_no", shares=Decimal("100"))
    assert isinstance(price, ExecutionPrice)
    # Best-ask NO is 0.60; the taker fee makes the executable cost strictly above it.
    assert price.value > 0.60


def test_market_book_rejects_misdeclared_ladder_side():
    """A ladder placed in the wrong slot (a bid in an asks slot) is refused.

    Typing invariant on the carrier: the leaf walker selects a side by direction, so an
    asks slot MUST hold an 'ask' ladder. This is a shape guarantee, not a cost gate.
    """
    with pytest.raises(FamilyBookError):
        MarketBook(
            condition_id="cond-b25",
            bin_id="b25",
            yes_token_id="yes-b25",
            no_token_id="no-b25",
            yes_asks=_ladder("bid"),
            yes_bids=_ladder("bid"),
            no_asks=_ladder("ask"),
            no_bids=_ladder("bid"),
            neg_risk=False,
        )


def test_family_book_hash_is_deterministic_and_sensitive():
    """book_hash is stable across builds and changes when the captured surface changes."""
    space = _outcome_space()
    bin_ids = [b.bin_id for b in space.bins]
    snaps = {bid: _snapshot(bid, neg_risk=False) for bid in bin_ids}

    fb1 = family_book_from_snapshots(omega=space, snapshots_by_bin_id=snaps, captured_at_utc=_CAPTURED)
    fb2 = family_book_from_snapshots(omega=space, snapshots_by_bin_id=snaps, captured_at_utc=_CAPTURED)
    assert fb1.book_hash == fb2.book_hash, "same surface + instant -> same hash"

    # A different per-market neg_risk changes the hash (it is part of the captured surface).
    flipped = dict(snaps)
    flipped["b25"] = _snapshot("b25", neg_risk=True)
    fb3 = family_book_from_snapshots(omega=space, snapshots_by_bin_id=flipped, captured_at_utc=_CAPTURED)
    assert fb3.book_hash != fb1.book_hash

    # A different capture instant changes the hash.
    later = _CAPTURED.replace(second=30)
    fb4 = family_book_from_snapshots(omega=space, snapshots_by_bin_id=snaps, captured_at_utc=later)
    assert fb4.book_hash != fb1.book_hash


def test_family_book_requires_utc_capture_instant():
    """A naive capture instant is coerced to UTC; complete coverage still derived."""
    space = _outcome_space()
    bin_ids = [b.bin_id for b in space.bins]
    markets = {bid: _market_book(bid, neg_risk=False) for bid in bin_ids}
    naive = datetime(2026, 6, 14, 12, 0, 0)  # no tzinfo
    fb = build_family_book(omega=space, markets=markets, captured_at_utc=naive)
    assert fb.captured_at_utc.tzinfo is not None
    assert fb.captured_at_utc == _CAPTURED
    assert fb.complete_book is True
