# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: architecture/market_cost_seam_executable_uncertainty_2026_05_27.md §Wave3 +
# Lifecycle: created=2026-05-27; last_reviewed=2026-05-27; last_reused=never
# Purpose: Wave 3 — EntryQuoteEvidence dataclass + factory tests
# Reuse: Construction from two-sided book, fee inclusion, ExecutionPrice conversion + assert_kelly_safe, reliability transitions, cost_uncertainty RSS composition (X4-corrected), schema-packet contract.
#                  src/contracts/entry_quote_evidence.py docstring
"""Wave 3: EntryQuoteEvidence dataclass + factory.

Tests that the typed value object:
  - constructs from a normal two-sided orderbook with depth-walked fill.
  - marks reliability correctly across LIVE_OK / STALE / THIN_BOOK / ASK_ONLY /
    CROSSED states.
  - derives ``all_in_entry_price`` = fill_price_walk + Polymarket fee.
  - derives ``cost_uncertainty`` = max(spread/2, slippage_bps/10000) (Wave 3
    conservative formula; Wave 5 will refine).
  - converts to an ExecutionPrice that passes ``assert_kelly_safe()`` directly
    (price_type='fee_adjusted', fee_deducted=True).
"""
from __future__ import annotations

import pytest

from src.contracts.entry_quote_evidence import (
    DEFAULT_STALE_THRESHOLD_MS,
    EntryQuoteEvidence,
    entry_quote_evidence_from_orderbook,
)
from src.contracts.execution_price import (
    ExecutionPrice,
    ExecutionPriceContractError,
    polymarket_fee,
)


def _ob(bids: list | None = None, asks: list | None = None) -> dict:
    return {"bids": bids or [], "asks": asks or []}


# ---------------------------------------------------------------------------
# Happy path: two-sided book, depth-sufficient
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_constructs_from_two_sided_book(self):
        ob = _ob(
            bids=[{"price": 0.40, "size": 100.0}],
            asks=[{"price": 0.42, "size": 100.0}],
        )
        ev = entry_quote_evidence_from_orderbook(
            token_id="tok_yes",
            side="yes",
            orderbook=ob,
            target_shares=50.0,
            fee_rate=0.05,
            quote_age_ms=500,
        )
        assert isinstance(ev, EntryQuoteEvidence)
        assert ev.best_bid == 0.40
        assert ev.best_ask == 0.42
        assert ev.spread_usd == pytest.approx(0.02)
        assert ev.fill_price_walk == pytest.approx(0.42)
        assert ev.slippage_bps == 0.0
        assert ev.reliability_status == "LIVE_OK"

    def test_all_in_entry_price_includes_polymarket_fee(self):
        ob = _ob(
            bids=[{"price": 0.40, "size": 100.0}],
            asks=[{"price": 0.42, "size": 100.0}],
        )
        ev = entry_quote_evidence_from_orderbook(
            token_id="tok_yes",
            side="yes",
            orderbook=ob,
            target_shares=50.0,
            fee_rate=0.05,
            quote_age_ms=0,
        )
        expected_fee = polymarket_fee(0.42, 0.05)
        assert ev.fee_per_share == pytest.approx(expected_fee)
        assert ev.all_in_entry_price == pytest.approx(0.42 + expected_fee)

    def test_to_execution_price_passes_assert_kelly_safe(self):
        ob = _ob(
            bids=[{"price": 0.40, "size": 100.0}],
            asks=[{"price": 0.42, "size": 100.0}],
        )
        ev = entry_quote_evidence_from_orderbook(
            token_id="tok_yes",
            side="yes",
            orderbook=ob,
            target_shares=50.0,
            fee_rate=0.05,
        )
        ep = ev.to_execution_price()
        assert isinstance(ep, ExecutionPrice)
        # Should not raise — already fee-adjusted at construction.
        ep.assert_kelly_safe()
        assert ep.price_type == "fee_adjusted"
        assert ep.fee_deducted is True

    def test_zero_fee_market_has_no_fee_component(self):
        ob = _ob(
            bids=[{"price": 0.40, "size": 100.0}],
            asks=[{"price": 0.42, "size": 100.0}],
        )
        ev = entry_quote_evidence_from_orderbook(
            token_id="tok_yes",
            side="yes",
            orderbook=ob,
            target_shares=50.0,
            fee_rate=0.0,
        )
        assert ev.fee_per_share == 0.0
        assert ev.all_in_entry_price == pytest.approx(ev.fill_price_walk)


# ---------------------------------------------------------------------------
# Reliability status transitions
# ---------------------------------------------------------------------------

class TestReliabilityStatus:
    def test_ask_only_when_bids_empty(self):
        ob = _ob(bids=[], asks=[{"price": 0.42, "size": 100.0}])
        ev = entry_quote_evidence_from_orderbook(
            token_id="tok_yes",
            side="yes",
            orderbook=ob,
            target_shares=50.0,
            fee_rate=0.05,
        )
        assert ev.reliability_status == "ASK_ONLY"
        assert ev.best_bid is None
        assert ev.spread_usd == 0.0

    def test_stale_when_quote_age_exceeds_threshold(self):
        ob = _ob(
            bids=[{"price": 0.40, "size": 100.0}],
            asks=[{"price": 0.42, "size": 100.0}],
        )
        ev = entry_quote_evidence_from_orderbook(
            token_id="tok_yes",
            side="yes",
            orderbook=ob,
            target_shares=50.0,
            fee_rate=0.05,
            quote_age_ms=DEFAULT_STALE_THRESHOLD_MS + 1,
        )
        assert ev.reliability_status == "STALE"

    def test_thin_book_when_depth_below_target(self):
        ob = _ob(
            bids=[{"price": 0.40, "size": 100.0}],
            asks=[{"price": 0.42, "size": 10.0}],
        )
        ev = entry_quote_evidence_from_orderbook(
            token_id="tok_yes",
            side="yes",
            orderbook=ob,
            target_shares=100.0,
            fee_rate=0.05,
        )
        assert ev.reliability_status == "THIN_BOOK"
        assert ev.depth_at_target_size == 10.0

    def test_crossed_when_bid_meets_or_exceeds_ask(self):
        ob = _ob(
            bids=[{"price": 0.42, "size": 100.0}],
            asks=[{"price": 0.42, "size": 100.0}],
        )
        ev = entry_quote_evidence_from_orderbook(
            token_id="tok_yes",
            side="yes",
            orderbook=ob,
            target_shares=50.0,
            fee_rate=0.05,
        )
        assert ev.reliability_status == "CROSSED"


# ---------------------------------------------------------------------------
# σ_market (Wave 3 conservative formula)
# ---------------------------------------------------------------------------

class TestCostUncertainty:
    def test_wide_spread_dominates_cost_uncertainty_when_no_slippage(self):
        # Top-of-book deep enough so slippage = 0; spread/2 = 0.025
        ob = _ob(
            bids=[{"price": 0.37, "size": 1000.0}],
            asks=[{"price": 0.42, "size": 1000.0}],
        )
        ev = entry_quote_evidence_from_orderbook(
            token_id="tok_yes",
            side="yes",
            orderbook=ob,
            target_shares=50.0,
            fee_rate=0.0,
        )
        assert ev.slippage_bps == 0.0
        assert ev.spread_usd == pytest.approx(0.05)
        assert ev.cost_uncertainty == pytest.approx(0.025)

    def test_slippage_dominates_cost_uncertainty_on_thin_top(self):
        # Top-of-book very thin so walking the ladder produces large slippage.
        # X4 fix (Copilot review of PR #348): cost_uncertainty is the RSS
        # composition sqrt(spread^2/4 + slippage^2 + ...), not max(...).
        import math
        ob = _ob(
            bids=[{"price": 0.41, "size": 100.0}],
            asks=[
                {"price": 0.42, "size": 5.0},
                {"price": 0.60, "size": 1000.0},
            ],
        )
        ev = entry_quote_evidence_from_orderbook(
            token_id="tok_yes",
            side="yes",
            orderbook=ob,
            target_shares=100.0,
            fee_rate=0.0,
        )
        slip_unit = ev.slippage_bps / 10_000.0
        expected_rss = math.sqrt((ev.spread_usd / 2.0) ** 2 + slip_unit ** 2)
        assert ev.cost_uncertainty == pytest.approx(expected_rss, rel=1e-6)
        assert ev.cost_uncertainty >= slip_unit


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

class TestInputValidation:
    def test_missing_asks_raises(self):
        ob = _ob(bids=[{"price": 0.40, "size": 100.0}], asks=[])
        with pytest.raises(ValueError, match="no asks"):
            entry_quote_evidence_from_orderbook(
                token_id="tok_yes",
                side="yes",
                orderbook=ob,
                target_shares=50.0,
                fee_rate=0.05,
            )

    def test_invalid_fee_rate_raises(self):
        ob = _ob(asks=[{"price": 0.42, "size": 100.0}])
        with pytest.raises(ValueError, match="fee_rate"):
            entry_quote_evidence_from_orderbook(
                token_id="tok_yes",
                side="yes",
                orderbook=ob,
                target_shares=50.0,
                fee_rate=-0.01,
            )

    def test_negative_quote_age_raises(self):
        ob = _ob(asks=[{"price": 0.42, "size": 100.0}])
        with pytest.raises(ValueError, match="quote_age_ms"):
            entry_quote_evidence_from_orderbook(
                token_id="tok_yes",
                side="yes",
                orderbook=ob,
                target_shares=50.0,
                fee_rate=0.05,
                quote_age_ms=-1,
            )


# ---------------------------------------------------------------------------
# Schema packet contract (downstream consumers)
# ---------------------------------------------------------------------------

class TestSchemaPacket:
    def test_schema_packet_lists_all_fields(self):
        packet = EntryQuoteEvidence.schema_packet()
        assert packet["type"] == "EntryQuoteEvidence"
        required = set(packet["required_fields"])
        # Sanity: every dataclass field is listed exactly once.
        expected = {
            "token_id", "side", "best_bid", "best_ask", "spread_usd",
            "top_of_book_size", "depth_at_target_size", "fill_price_walk",
            "slippage_bps", "quote_age_ms", "book_hash",
            "fee_rate", "fee_per_share", "all_in_entry_price",
            "cost_uncertainty", "reliability_status",
        }
        assert required == expected
