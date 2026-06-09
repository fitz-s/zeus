# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: operator directive 2026-06-09 (riskguard false-RED follow-through)
#   — live incident 22:15-22:28Z: Polymarket /positions intermittently returned an
#   EMPTY list while ~$857 of open positions existed; bankroll-of-record equity
#   collapsed $951 -> $94 (free collateral only), the riskguard daily-loss
#   threshold base collapsed $76 -> $7.53, and a genuine-but-small $10.44
#   realized loss tripped a false RED that blocked ALL new entries. Fitz
#   constraint #4 (data provenance): a failed/empty READ must be distinguishable
#   from the true state "no positions".
"""Relationship antibody: the bankroll equity base must be invariant to a
transient empty /positions read that contradicts recent verified holdings,
while every AFFIRMATIVE venue report (positions present, any value) passes
through verbatim so genuine drawdowns still tighten gates.

The cross-module invariant this pins (relationship test, not a function test):

    bankroll_provider.value_usd -> riskguard daily/weekly loss THRESHOLD BASE.
    A single contradicted empty read MUST NOT collapse that base (false RED =
    total entry blockage); a venue-reported value collapse MUST collapse it
    (honest tightening); a cash-corroborated redemption MUST collapse it
    (settlement pays winners into free cash); a PERSISTENT empty beyond the
    hold bound MUST collapse it (genuine closure persists, blips do not).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from src.runtime import bankroll_provider
from src.runtime.bankroll_provider import (
    _DEFAULT_POSITIONS_EMPTY_HOLD_SECONDS,
    _classify_positions_read,
    _resolve_position_value,
)

NOW = datetime(2026, 6, 9, 22, 20, 0, tzinfo=timezone.utc)
HOLD = _DEFAULT_POSITIONS_EMPTY_HOLD_SECONDS


def _classify(**overrides):
    kwargs = dict(
        free_pusd=94.0,
        raw_position_value=0.0,
        positions_count=0,
        prev_spendable_cash=94.0,
        prev_position_value=857.0,
        prev_nonzero_positions_at=NOW - timedelta(seconds=60),
        now=NOW,
        hold_bound_seconds=HOLD,
    )
    kwargs.update(overrides)
    return _classify_positions_read(**kwargs)


class TestClassifier:
    def test_blip_empty_read_contradicting_recent_holdings_holds_base(self):
        """(a) The live incident: empty list, recent $857 verified, cash flat
        -> HOLD the last-known-good value. The equity base does not collapse."""
        verdict, value = _classify()
        assert verdict == "blip_held"
        assert value == 857.0

    def test_genuine_redemption_cash_corroborated_accepts_zero(self):
        """(b) Positions truly closed via settlement/redemption: the closure
        pays winners into free cash. Cash jump >= 25% of the vanished value
        corroborates the empty list -> base updates down honestly, no hold."""
        verdict, value = _classify(free_pusd=94.0 + 0.25 * 857.0)
        assert verdict == "redemption_corroborated"
        assert value == 0.0

    def test_venue_reported_value_collapse_is_honest_no_hold(self):
        """NO GATE WEAKENING: positions PRESENT with collapsed value is an
        affirmative venue report (mark-to-market drawdown). It must pass
        through verbatim and tighten the threshold base."""
        verdict, value = _classify(positions_count=3, raw_position_value=12.5)
        assert verdict == "verified"
        assert value == 12.5
        # Even a venue-reported total wipeout (positions present, all worth 0)
        # is honest truth, not a blip.
        verdict, value = _classify(positions_count=3, raw_position_value=0.0)
        assert verdict == "verified"
        assert value == 0.0

    def test_persistent_empty_beyond_hold_bound_accepts_zero(self):
        """Genuine closure persists; blips do not. Past the hold bound the
        empty read becomes the accepted truth — the hold cannot defend a
        stale anchor forever."""
        verdict, value = _classify(
            prev_nonzero_positions_at=NOW - timedelta(seconds=HOLD + 1)
        )
        assert verdict == "persistent_empty_accepted"
        assert value == 0.0

    def test_cold_start_or_genuinely_flat_account_accepts_empty(self):
        """Empty with nothing contradicted (no prior nonzero value) is a
        verified flat account — no hold, no warning."""
        for prev in (None, 0.0, 0.005):
            verdict, value = _classify(prev_position_value=prev)
            assert verdict == "verified"
            assert value == 0.0
        verdict, value = _classify(prev_nonzero_positions_at=None)
        assert verdict == "verified"
        assert value == 0.0

    def test_small_cash_drift_does_not_corroborate(self):
        """A few dollars of cash drift is not a redemption of $857."""
        verdict, value = _classify(free_pusd=94.0 + 5.0)
        assert verdict == "blip_held"
        assert value == 857.0


class TestStateWiring:
    """_resolve_position_value: module-state anchors + WARN emission."""

    @pytest.fixture(autouse=True)
    def _fresh_state(self):
        bankroll_provider.reset_cache_for_tests()
        yield
        bankroll_provider.reset_cache_for_tests()

    def test_blip_holds_base_and_warns_and_does_not_advance_anchor(self, caplog):
        # Verified nonzero read seeds the anchor.
        assert _resolve_position_value(94.0, 857.0, 5, now=NOW) == 857.0
        assert bankroll_provider._last_positions_read_verdict == "verified"
        anchor = bankroll_provider._last_nonzero_positions_at

        # (a) Blip: empty read 60s later -> base held, WARN logged.
        with caplog.at_level(logging.WARNING, logger="src.runtime.bankroll_provider"):
            held = _resolve_position_value(
                94.0, 0.0, 0, now=NOW + timedelta(seconds=60)
            )
        assert held == 857.0
        assert bankroll_provider._last_positions_read_verdict == "blip_held"
        assert any("BLIP" in record.message for record in caplog.records)
        # The anchor must NOT advance on a hold — otherwise a sustained empty
        # streak would self-renew the hold forever instead of aging out.
        assert bankroll_provider._last_nonzero_positions_at == anchor

    def test_persistent_empty_past_bound_accepts_and_clears_anchor(self, caplog):
        assert _resolve_position_value(94.0, 857.0, 5, now=NOW) == 857.0
        late = NOW + timedelta(seconds=HOLD + 60)
        with caplog.at_level(logging.WARNING, logger="src.runtime.bankroll_provider"):
            value = _resolve_position_value(94.0, 0.0, 0, now=late)
        assert value == 0.0
        assert bankroll_provider._last_positions_read_verdict == "persistent_empty_accepted"
        # Anchor cleared: the NEXT empty read is an ordinary verified flat.
        verdict, value = _classify(
            prev_position_value=bankroll_provider._last_position_value_usd,
        )
        assert (verdict, value) == ("verified", 0.0)

    def test_genuine_redemption_updates_base_down(self):
        """(b) full wiring: cash-corroborated closure lowers the base honestly."""
        bankroll_provider._last_spendable_cash_usd = 94.0
        assert _resolve_position_value(94.0, 857.0, 5, now=NOW) == 857.0
        value = _resolve_position_value(
            94.0 + 300.0, 0.0, 0, now=NOW + timedelta(seconds=60)
        )
        assert value == 0.0
        assert bankroll_provider._last_positions_read_verdict == "redemption_corroborated"

    def test_verdict_threads_onto_bankroll_of_record(self):
        """BankrollOfRecord carries the provenance field with a safe default."""
        record = bankroll_provider.BankrollOfRecord(
            value_usd=1.0, fetched_at="2026-06-09T00:00:00+00:00"
        )
        assert record.positions_read_verdict == "verified"
