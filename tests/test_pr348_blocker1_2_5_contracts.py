# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: PR #348 second-pass operator review — Blockers 1, 2, 5b, 5c
# Lifecycle: created=2026-05-27; last_reviewed=2026-05-27; last_reused=never
# Purpose: antibodies for the 3 structural decisions from the second-pass review
# Reuse: K-A (ExecutionPrice equality/hash contract), K-B (σ_market absolute-unit
#        RSS + ASK_ONLY floor), K-C (reliability severity monotonicity).
"""PR #348 second-pass blocker antibodies — 3 K decisions.

The operator's second-pass review (2026-05-27, against parent commit
eefcafb86e) flagged seven blockers. Blockers 3/4/6/7 landed in the prior
P0 commit (b23f5f0c36). The remaining open defects reduce to three
structural decisions (Fitz §1):

  K-A (Blocker 1): ExecutionPrice equality must obey Python's
       equality/hash/transitivity contract. ``EP == float`` combined with a
       full-provenance ``__hash__`` corrupts any set/dict that mixes the two
       and breaks transitivity. Equality is ExecutionPrice-to-ExecutionPrice
       only; float comparison goes through ``float(ep)`` explicitly.

  K-B (Blocker 2 + 5c): every σ_market RSS term must be in ABSOLUTE price
       units. The old formula added an absolute half-spread to a RELATIVE
       slippage ratio (``slippage_bps / 10_000``). Fix: ``slippage_abs =
       fill_price_walk - best_ask``. ASK_ONLY books (no bid → spread unknown)
       get a conservative absolute uncertainty floor.

  K-C (Blocker 5b): reliability severity must be monotone —
       CROSSED > THIN_BOOK > STALE > ASK_ONLY > LIVE_OK. The old order
       returned STALE for a stale+thin book, slipping past the
       market_analysis hard-veto (``status in ("THIN_BOOK", "CROSSED")``).
"""
from __future__ import annotations

import math

import pytest

from src.contracts.entry_quote_evidence import (
    ASK_ONLY_COST_UNCERTAINTY_FLOOR,
    entry_quote_evidence_from_orderbook,
)
from src.contracts.execution_price import ExecutionPrice


# ---------------------------------------------------------------------------
# K-A — ExecutionPrice equality / hash contract (Blocker 1)
# ---------------------------------------------------------------------------


class TestKA_ExecutionPriceEqualityContract:
    def _ep(self, value=0.5, price_type="ask"):
        return ExecutionPrice(
            value=value, price_type=price_type,
            fee_deducted=False, currency="probability_units",
        )

    def test_execution_price_not_equal_to_float(self):
        ep = self._ep(0.5)
        # Equality MUST NOT cross the type boundary (provenance loss / hash break).
        assert (ep == 0.5) is False
        assert ep != 0.5
        assert (0.5 == ep) is False

    def test_float_escape_hatch_still_works(self):
        ep = self._ep(0.5)
        # Explicit scalar comparison remains available for legacy readers.
        assert float(ep) == 0.5
        assert ep.value == 0.5

    def test_ordering_and_arithmetic_dunders_unaffected(self):
        ep = self._ep(0.5)
        assert ep < 0.6 and ep > 0.4 and ep <= 0.5 and ep >= 0.5
        assert (ep + 0.1) == pytest.approx(0.6)
        assert (1.0 - ep) == pytest.approx(0.5)

    def test_execution_price_hash_contract_no_float_equality(self):
        ep = self._ep(0.5)
        # If EP == float were allowed, hash(ep) != hash(0.5) would violate the
        # hash invariant. Equality is float-free, so a set distinguishes them.
        s = {ep, 0.5}
        assert len(s) == 2
        assert 0.5 in s and ep in s

    def test_full_field_equality_and_hash_consistency(self):
        a = self._ep(0.5, "ask")
        b = self._ep(0.5, "ask")
        assert a == b
        assert hash(a) == hash(b)  # equal objects → equal hashes

    def test_transitivity_preserved_no_float_bridge(self):
        # Two EPs same value, different provenance must NOT be equal, and
        # neither bridges through a float (which previously made A==0.5,
        # B==0.5, A!=B — a transitivity violation).
        a = self._ep(0.5, "ask")
        b = self._ep(0.5, "vwmp")
        assert a != b
        assert (a == 0.5) is False
        assert (b == 0.5) is False


# ---------------------------------------------------------------------------
# K-B — σ_market dimensional consistency + ASK_ONLY floor (Blockers 2 + 5c)
# ---------------------------------------------------------------------------


class TestKB_CostUncertaintyDimensions:
    def test_cost_uncertainty_uses_absolute_slippage_not_bps_ratio(self):
        # Book: 10 shares @ 0.30, then deep @ 0.40. target=20 → walk both.
        # fill = (10*0.30 + 10*0.40)/20 = 0.35; best_ask=0.30.
        eqe = entry_quote_evidence_from_orderbook(
            token_id="t", side="yes",
            orderbook={
                "asks": [{"price": 0.30, "size": 10}, {"price": 0.40, "size": 100}],
                "bids": [{"price": 0.29, "size": 50}],
            },
            target_shares=20.0,
            fee_rate=0.0,
        )
        slippage_abs = eqe.fill_price_walk - eqe.best_ask  # absolute price units
        half_spread = eqe.spread_usd / 2.0
        expected_abs = math.sqrt(half_spread ** 2 + slippage_abs ** 2)
        # σ_market matches the ABSOLUTE-unit composition.
        assert eqe.cost_uncertainty == pytest.approx(expected_abs, abs=1e-9)

        # And it must NOT match the dimensionally-invalid bps-ratio formula
        # (which inflates the slippage term by 1/best_ask since ask < 1).
        slippage_unit_bps = eqe.slippage_bps / 10_000.0
        wrong_bps = math.sqrt(half_spread ** 2 + slippage_unit_bps ** 2)
        assert not math.isclose(eqe.cost_uncertainty, wrong_bps, abs_tol=1e-6), (
            f"cost_uncertainty {eqe.cost_uncertainty:.6f} still uses the "
            f"relative bps-ratio slippage term ({wrong_bps:.6f})"
        )

    def test_no_slippage_book_cost_uncertainty_is_half_spread(self):
        # Top level covers the full order → fill == best_ask → slippage_abs=0.
        eqe = entry_quote_evidence_from_orderbook(
            token_id="t", side="yes",
            orderbook={
                "asks": [{"price": 0.30, "size": 500}],
                "bids": [{"price": 0.28, "size": 500}],
            },
            target_shares=10.0,
            fee_rate=0.0,
        )
        assert eqe.fill_price_walk == pytest.approx(0.30)
        assert eqe.cost_uncertainty == pytest.approx(eqe.spread_usd / 2.0, abs=1e-9)

    def test_ask_only_has_uncertainty_floor(self):
        # No bids → ASK_ONLY → spread_usd=0 but σ_market must not collapse.
        eqe = entry_quote_evidence_from_orderbook(
            token_id="t", side="yes",
            orderbook={"asks": [{"price": 0.30, "size": 500}], "bids": []},
            target_shares=10.0,
            fee_rate=0.0,
        )
        assert eqe.reliability_status == "ASK_ONLY"
        assert eqe.spread_usd == 0.0
        assert eqe.cost_uncertainty >= ASK_ONLY_COST_UNCERTAINTY_FLOOR - 1e-12, (
            "ASK_ONLY book must carry a conservative cost-uncertainty floor"
        )


# ---------------------------------------------------------------------------
# K-C — reliability severity monotonicity (Blocker 5b)
# ---------------------------------------------------------------------------


class TestKC_ReliabilitySeverityMonotonicity:
    def test_stale_thin_book_returns_thin_book_not_stale(self):
        # Stale (huge age) AND thin (5 shares < 100 target). THIN_BOOK is
        # hard-veto-eligible and must dominate the soft STALE status.
        eqe = entry_quote_evidence_from_orderbook(
            token_id="t", side="yes",
            orderbook={
                "asks": [{"price": 0.30, "size": 5}],
                "bids": [{"price": 0.29, "size": 50}],
            },
            target_shares=100.0,
            quote_age_ms=10_000_000,
            fee_rate=0.0,
        )
        assert eqe.reliability_status == "THIN_BOOK", (
            "stale+thin book must report THIN_BOOK (hard-veto-eligible), "
            "not STALE — otherwise the market_analysis veto is bypassed"
        )

    def test_pure_stale_book_returns_stale(self):
        # Depth sufficient, two-sided, but stale → soft STALE (no hard veto).
        eqe = entry_quote_evidence_from_orderbook(
            token_id="t", side="yes",
            orderbook={
                "asks": [{"price": 0.30, "size": 500}],
                "bids": [{"price": 0.29, "size": 500}],
            },
            target_shares=10.0,
            quote_age_ms=10_000_000,
            fee_rate=0.0,
        )
        assert eqe.reliability_status == "STALE"

    def test_crossed_book_dominates_thin(self):
        # bid >= ask (degenerate) AND thin → CROSSED has highest severity.
        eqe = entry_quote_evidence_from_orderbook(
            token_id="t", side="yes",
            orderbook={
                "asks": [{"price": 0.30, "size": 5}],
                "bids": [{"price": 0.31, "size": 50}],
            },
            target_shares=100.0,
            fee_rate=0.0,
        )
        assert eqe.reliability_status == "CROSSED"

    def test_live_ok_when_two_sided_fresh_deep(self):
        eqe = entry_quote_evidence_from_orderbook(
            token_id="t", side="yes",
            orderbook={
                "asks": [{"price": 0.30, "size": 500}],
                "bids": [{"price": 0.29, "size": 500}],
            },
            target_shares=10.0,
            quote_age_ms=0,
            fee_rate=0.0,
        )
        assert eqe.reliability_status == "LIVE_OK"
