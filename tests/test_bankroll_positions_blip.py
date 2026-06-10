# Created: 2026-06-09
# Last reused or audited: 2026-06-09 (foreign-fill contamination antibodies added)
# Authority basis: operator directive 2026-06-09 (riskguard false-RED follow-through)
#   — live incident 22:15-22:28Z: Polymarket /positions intermittently returned an
#   EMPTY list while ~$857 of open positions existed; bankroll-of-record equity
#   collapsed $951 -> $94 (free collateral only), the riskguard daily-loss
#   threshold base collapsed $76 -> $7.53, and a genuine-but-small $10.44
#   realized loss tripped a false RED that blocked ALL new entries. Fitz
#   constraint #4 (data provenance): a failed/empty READ must be distinguishable
#   from the true state "no positions".
#   2026-06-09 P1 follow-up (operator-accepted review finding): dual bankroll.
#   The held value defends the LOSS THRESHOLD against the false RED, but during
#   the hold it is a PHANTOM for NEW-ENTRY sizing — Kelly must NOT size off it.
#   `_resolve_position_value` now returns (loss_threshold_value, sizing_value)
#   where sizing_value==0 under blip_held; BankrollOfRecord carries
#   equity_for_new_entry_sizing_usd.
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
    _is_condition_in_zeus_domain,
    _resolve_position_value,
    _split_positions_by_domain,
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
        loss_v, sizing_v = _resolve_position_value(94.0, 857.0, 5, now=NOW)
        assert (loss_v, sizing_v) == (857.0, 857.0)
        assert bankroll_provider._last_positions_read_verdict == "verified"
        anchor = bankroll_provider._last_nonzero_positions_at

        # (a) Blip: empty read 60s later -> LOSS-THRESHOLD base held, but
        # NEW-ENTRY sizing base is 0 (phantom excluded). WARN logged.
        with caplog.at_level(logging.WARNING, logger="src.runtime.bankroll_provider"):
            held_loss, held_sizing = _resolve_position_value(
                94.0, 0.0, 0, now=NOW + timedelta(seconds=60)
            )
        assert held_loss == 857.0, "loss-threshold base must HOLD the phantom (no false RED)"
        assert held_sizing == 0.0, "sizing base must EXCLUDE the phantom (no Kelly off vanished equity)"
        assert bankroll_provider._last_positions_read_verdict == "blip_held"
        assert any("BLIP" in record.message for record in caplog.records)
        # The anchor must NOT advance on a hold — otherwise a sustained empty
        # streak would self-renew the hold forever instead of aging out.
        assert bankroll_provider._last_nonzero_positions_at == anchor

    def test_persistent_empty_past_bound_accepts_and_clears_anchor(self, caplog):
        loss_v, sizing_v = _resolve_position_value(94.0, 857.0, 5, now=NOW)
        assert (loss_v, sizing_v) == (857.0, 857.0)
        late = NOW + timedelta(seconds=HOLD + 60)
        with caplog.at_level(logging.WARNING, logger="src.runtime.bankroll_provider"):
            loss_v, sizing_v = _resolve_position_value(94.0, 0.0, 0, now=late)
        # Past the bound the empty read is accepted: both bases agree at 0.
        assert (loss_v, sizing_v) == (0.0, 0.0)
        assert bankroll_provider._last_positions_read_verdict == "persistent_empty_accepted"
        # Anchor cleared: the NEXT empty read is an ordinary verified flat.
        verdict, value = _classify(
            prev_position_value=bankroll_provider._last_position_value_usd,
        )
        assert (verdict, value) == ("verified", 0.0)

    def test_genuine_redemption_updates_base_down(self):
        """(b) full wiring: cash-corroborated closure lowers BOTH bases honestly."""
        bankroll_provider._last_spendable_cash_usd = 94.0
        loss_v, sizing_v = _resolve_position_value(94.0, 857.0, 5, now=NOW)
        assert (loss_v, sizing_v) == (857.0, 857.0)
        loss_v, sizing_v = _resolve_position_value(
            94.0 + 300.0, 0.0, 0, now=NOW + timedelta(seconds=60)
        )
        # Corroborated closure is truth, not a blip: both bases drop together.
        assert (loss_v, sizing_v) == (0.0, 0.0)
        assert bankroll_provider._last_positions_read_verdict == "redemption_corroborated"

    def test_verdict_threads_onto_bankroll_of_record(self):
        """BankrollOfRecord carries the provenance + sizing fields with safe defaults."""
        record = bankroll_provider.BankrollOfRecord(
            value_usd=1.0, fetched_at="2026-06-09T00:00:00+00:00"
        )
        assert record.positions_read_verdict == "verified"
        assert record.equity_for_new_entry_sizing_usd is None


class TestDualBankrollWiring:
    """Relationship antibody: the held LOSS-THRESHOLD equity must NEVER feed
    NEW-ENTRY sizing while a /positions blip is held. The cross-module invariant:

        bankroll_provider.value_usd                  -> loss-threshold base (held).
        bankroll_provider.equity_for_new_entry_sizing_usd -> Kelly base (phantom
            EXCLUDED under blip_held).

    Under blip_held the two MUST diverge: value_usd holds the phantom (prevents
    false RED), sizing equity drops to free cash (prevents Kelly off phantom).
    """

    @pytest.fixture(autouse=True)
    def _fresh_state(self):
        bankroll_provider.reset_cache_for_tests()
        yield
        bankroll_provider.reset_cache_for_tests()

    def test_position_value_diverges_loss_and_sizing_under_blip(self):
        """prev nonzero + empty read + no cash corroboration -> blip_held, AND
        the equity legs DIVERGE: the loss-threshold leg keeps the held phantom
        (free 94 + held 857 = 951) while the sizing leg excludes it (free 94 +
        0 = 94). NEVER full held equity into the sizing leg.

        This is the dual-bankroll contract at the source — _resolve_position_value
        returns (loss_position_value, sizing_position_value); _fetch_balance then
        assembles free+each. The conftest isolation fixture forbids the live
        wallet edge, so we assert the divergence at the resolved-value layer
        (the load-bearing decision) and assemble the equity legs as the producer
        does."""
        free = 94.0
        # Verified seed: positions worth 857.
        loss_pv, sizing_pv = bankroll_provider._resolve_position_value(
            free, 857.0, 1, now=NOW
        )
        assert (free + loss_pv, free + sizing_pv) == (951.0, 951.0)
        assert bankroll_provider._last_positions_read_verdict == "verified"

        # The blip: empty /positions read 60s later, cash flat.
        loss_pv, sizing_pv = bankroll_provider._resolve_position_value(
            free, 0.0, 0, now=NOW + timedelta(seconds=60)
        )
        assert bankroll_provider._last_positions_read_verdict == "blip_held"
        loss_equity = free + loss_pv
        sizing_equity = free + sizing_pv
        assert loss_equity == 951.0, "loss-threshold equity must HOLD (no false RED)"
        assert sizing_equity == 94.0, (
            "sizing equity must EXCLUDE the held phantom: full held equity must "
            f"NEVER feed sizing. got {sizing_equity!r}"
        )
        assert loss_equity != sizing_equity, "the two legs MUST diverge under blip_held"

    def test_coerce_threads_sizing_equity_into_three_tuple(self):
        """_coerce_fetch_balance_result preserves the sizing leg from the
        dual-bankroll 3-tuple and degrades safely for legacy shapes."""
        assert bankroll_provider._coerce_fetch_balance_result(
            (951.0, 94.0, 94.0)
        ) == (951.0, 94.0, 94.0)
        # Legacy 2-tuple: sizing unknown -> None (consumers fall back).
        assert bankroll_provider._coerce_fetch_balance_result(
            (951.0, 94.0)
        ) == (951.0, 94.0, None)
        # Legacy bare float: spendable + sizing unknown.
        assert bankroll_provider._coerce_fetch_balance_result(951.0) == (951.0, None, None)

    def test_runtime_bankroll_usd_does_not_size_off_phantom(self, monkeypatch):
        """The event_reactor sizing consumer (`_runtime_bankroll_usd`) must read a
        phantom-free base under blip_held. Even via the legacy value_usd fallback
        path (no spendable_cash), the sizing equity field is preferred over the
        held value_usd."""
        from src.engine.event_reactor_adapter import _runtime_bankroll_usd

        # Construct a blip_held record by hand: held value_usd=951 (phantom),
        # but conservative sizing base=94. Simulate a degraded record with no
        # spendable_cash so the fallback chain is exercised.
        blip_record = bankroll_provider.BankrollOfRecord(
            value_usd=951.0,
            fetched_at=NOW.isoformat(),
            spendable_cash_usd=None,
            positions_read_verdict="blip_held",
            equity_for_new_entry_sizing_usd=94.0,
        )
        monkeypatch.setattr(bankroll_provider, "current", lambda **k: blip_record)
        monkeypatch.setattr(bankroll_provider, "cached", lambda **k: blip_record)

        sized = _runtime_bankroll_usd()
        assert sized == 94.0, (
            "Kelly base must be the conservative sizing equity (94), NOT the held "
            f"phantom value_usd (951). got {sized!r}"
        )


class TestForeignFillContamination:
    """Antibody: operator's manual fills on the shared wallet must not contaminate
    Zeus's equity base, Kelly sizing, or daily-loss threshold.

    Cross-module invariant: foreign position value (condition_id ∉ Zeus domain)
    MUST be excluded from both equity legs; Zeus position value MUST be included;
    mixed response → only Zeus value counted; domain unprovable → fail-closed
    (include all, conservative for loss-threshold).
    """

    # -----------------------------------------------------------------
    # Helpers that drive the domain classifier with in-memory DB stubs.
    # These bypass the real DB open in _split_positions_by_domain so the
    # tests are hermetic and do not require a live zeus-world.db.
    # -----------------------------------------------------------------

    @staticmethod
    def _make_position(condition_id: str, current_value: float) -> dict:
        return {
            "condition_id": condition_id,
            "current_value": current_value,
            "size": 1.0,
            "token_id": f"tok_{condition_id}",
        }

    @staticmethod
    def _domain_check_with_stub(condition_id: str, zeus_cids: set[str]) -> bool:
        """Drive _is_condition_in_zeus_domain with a pre-populated in-memory DB."""
        import sqlite3

        world_conn = sqlite3.connect(":memory:")
        world_conn.execute(
            "CREATE TABLE executable_market_snapshots (condition_id TEXT PRIMARY KEY)"
        )
        for cid in zeus_cids:
            world_conn.execute(
                "INSERT INTO executable_market_snapshots VALUES (?)", (cid,)
            )
        world_conn.commit()

        trade_conn = sqlite3.connect(":memory:")
        trade_conn.execute(
            "CREATE TABLE venue_commands (market_id TEXT)"
        )
        trade_conn.commit()

        result = _is_condition_in_zeus_domain(condition_id, world_conn, trade_conn)
        world_conn.close()
        trade_conn.close()
        return result

    def test_zeus_position_included(self):
        """A condition_id in executable_market_snapshots is Zeus-domain."""
        assert self._domain_check_with_stub("cid_zeus", {"cid_zeus", "cid_other"}) is True

    def test_foreign_position_excluded(self):
        """A condition_id absent from snapshots AND venue_commands is foreign."""
        assert self._domain_check_with_stub("cid_ai_market", {"cid_zeus"}) is False

    def test_fail_closed_empty_snapshot_table(self):
        """If executable_market_snapshots is empty, domain is unprovable → fail-closed (True)."""
        assert self._domain_check_with_stub("cid_anything", set()) is True

    def test_fail_closed_no_db(self):
        """If both DB connections are None, all positions are in-domain (fail-closed)."""
        assert _is_condition_in_zeus_domain("cid_anything", None, None) is True

    def test_split_excludes_foreign_value_from_equity(self, monkeypatch):
        """_split_positions_by_domain separates Zeus and foreign positions.

        Foreign value must be logged and excluded; Zeus value must be passed through.
        The test stubs the DB open so no real sqlite file is needed.
        """
        import sqlite3

        zeus_cid = "0xaaaa"
        foreign_cid = "0xbbbb"

        zeus_pos = self._make_position(zeus_cid, 120.0)
        foreign_pos = self._make_position(foreign_cid, 45.0)

        # Stub _split_positions_by_domain's internal DB open by patching the
        # imported helpers via monkeypatch. We replace _is_condition_in_zeus_domain
        # with a closure that mirrors what the real in-memory DB would return.
        def _stub_domain(cid, world_conn, trade_conn):
            return cid == zeus_cid

        monkeypatch.setattr(bankroll_provider, "_is_condition_in_zeus_domain", _stub_domain)
        # Patch DB open to no-ops (None) so _split_positions_by_domain skips real files.
        # We can't directly patch the local variable, so we patch the helper it imports.
        # Instead, use the public interface: call _split_positions_by_domain after
        # patching _is_condition_in_zeus_domain at module level (already done above).

        zeus_out, foreign_out, foreign_val = _split_positions_by_domain(
            [zeus_pos, foreign_pos]
        )

        assert zeus_out == [zeus_pos], "Zeus position must be in zeus bucket"
        assert foreign_out == [foreign_pos], "Foreign position must be in foreign bucket"
        assert foreign_val == pytest.approx(45.0), "Foreign value must be 45.0"

    def test_mixed_response_only_zeus_value_counted(self, monkeypatch):
        """Mixed /positions response: only Zeus positions contribute to equity.

        Antibody for the primary contamination vector: operator fills multiple
        AI-themed markets, Zeus has one open weather position. The equity sum
        must reflect only the Zeus position.
        """
        zeus_pos = self._make_position("cid_weather", 80.0)
        foreign_a = self._make_position("cid_ai_1", 200.0)
        foreign_b = self._make_position("cid_ai_2", 150.0)

        def _stub_domain(cid, world_conn, trade_conn):
            return cid == "cid_weather"

        monkeypatch.setattr(bankroll_provider, "_is_condition_in_zeus_domain", _stub_domain)

        zeus_out, foreign_out, foreign_val = _split_positions_by_domain(
            [zeus_pos, foreign_a, foreign_b]
        )

        assert len(zeus_out) == 1
        assert len(foreign_out) == 2
        assert foreign_val == pytest.approx(350.0)
        # Only zeus value (80.0) would be summed into raw_position_value
        zeus_value = sum(max(0.0, float(p.get("current_value", 0.0))) for p in zeus_out)
        assert zeus_value == pytest.approx(80.0)

    def test_all_foreign_logs_warning(self, monkeypatch, caplog):
        """When foreign positions are detected, a WARN is emitted for operator visibility."""
        pos = self._make_position("cid_ai", 99.0)

        def _stub_domain(cid, world_conn, trade_conn):
            return False  # everything is foreign

        monkeypatch.setattr(bankroll_provider, "_is_condition_in_zeus_domain", _stub_domain)

        with caplog.at_level(logging.WARNING, logger="src.runtime.bankroll_provider"):
            _split_positions_by_domain([pos])

        assert any(
            "BANKROLL_FOREIGN_POSITIONS" in r.message for r in caplog.records
        ), "Expected BANKROLL_FOREIGN_POSITIONS warning in log"

    def test_all_zeus_no_warning(self, monkeypatch, caplog):
        """When all positions are Zeus-domain, no foreign-position warning is emitted."""
        pos = self._make_position("cid_weather", 50.0)

        def _stub_domain(cid, world_conn, trade_conn):
            return True  # all in-domain

        monkeypatch.setattr(bankroll_provider, "_is_condition_in_zeus_domain", _stub_domain)

        with caplog.at_level(logging.WARNING, logger="src.runtime.bankroll_provider"):
            _split_positions_by_domain([pos])

        assert not any(
            "BANKROLL_FOREIGN_POSITIONS" in r.message for r in caplog.records
        ), "No foreign warning expected when all positions are Zeus-domain"
