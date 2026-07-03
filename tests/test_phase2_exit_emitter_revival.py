# Lifecycle: created=2026-06-20; last_reviewed=2026-07-03; last_reused=2026-07-03
# Purpose: RED-on-revert antibodies for the Phase 2 live exit-POST emitter revival
#   (exit_pending_missing re-stamp loop, day0 static-close deferral, canonical
#   EXIT_ORDER_POSTED dual-write, monitor-cadence watchdog).
# Reuse: pytest tests/test_phase2_exit_emitter_revival.py
# Created: 2026-06-20
# Last reused or audited: 2026-07-03
# Authority basis: /tmp/phase2_exit_emitter_diagnosis.md §4-§5 (Phase 2 of the
#   Zeus lifecycle-alpha fix). RANK 2 of /tmp/lifecycle_alpha_diagnosis_2026-06-20.md.
"""RED-on-revert antibodies for the Phase 2 live exit-POST emitter revival.

Each test MUST fail on the un-fixed tree and pass after the four fixes:

  FIX 2a — break the exit_pending_missing re-stamp loop.
    * test_still_held_routes_to_evaluate_not_restamp_retry  (still-held → evaluate)
    * test_rpc_fallthrough_dedupes_identical_reject          (≤1 reject / state-epoch)
  FIX 2b — defer the day0 static-close pre-emption when a bid still exists.
    * test_static_closed_with_bid_defers_terminal_stamp     (discriminator logic)
  FIX 2d — canonical EXIT_ORDER_POSTED dual-write from the spine emitter.
    * test_spine_post_writes_canonical_exit_order_posted     (source_module=exit_lifecycle)
  FIX 2c — monitor-cadence watchdog flags a >2x interval gap.
    * test_monitor_cadence_watchdog_flags_gap                (06-19 00:41→09:30 window)

All economic assertions use after-cost EV / executable-bid facts — never
would-have-won.
"""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.execution.exit_lifecycle import (
    _EXIT_LIFECYCLE_IN_FLIGHT_STATES,
    OrderResult,
    build_exit_intent,
    execute_exit,
    handle_exit_pending_missing,
)
from src.state.portfolio import ExitContext, Position, PortfolioState
from src.state.db import init_schema
from src.state.projection import upsert_position_current
from src.engine.lifecycle_events import build_position_current_projection


_SAFE_ADDRESS = "0x6a096d5042cba434521E2cdb95A1fBa789a09b7f"
_ASSET_ID = "113959433546428599583458171463964346033318046435676830124564125503733330054946"
_CONDITION_ID = "0xddb5c82d33579fbd3d47600a89438a1c6af5b1ac7ba48ed3a4099c6070c4df4d"


def _make_position(**kwargs) -> Position:
    defaults = dict(
        trade_id="t-phase2-001",
        market_id="m1",
        city="London",
        cluster="EU",
        target_date="2026-06-20",
        bin_label="30-31",
        direction="buy_yes",
        unit="C",
        temperature_metric="high",
        size_usd=5.0,
        shares=10.0,
        cost_basis_usd=5.0,
        entry_price=0.5,
        p_posterior=0.6,
        edge=0.1,
        entered_at="2026-06-01T00:00:00Z",
        token_id=_ASSET_ID,
        no_token_id="",
        condition_id=_CONDITION_ID,
        chain_state="exit_pending_missing",
        state="pending_exit",
        exit_state="",
        exit_retry_count=0,
        last_exit_error="",
        next_exit_retry_at="",
        strategy_key="opening_inertia",
        env="live",
    )
    defaults.update(kwargs)
    return Position(**defaults)


def _portfolio(position: Position) -> PortfolioState:
    return PortfolioState(positions=[position])


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _seed_position_current(conn: sqlite3.Connection, position: Position) -> None:
    proj = build_position_current_projection(position)
    upsert_position_current(conn, proj)
    conn.commit()


def _executable_snapshot_context(*, bid: str = "0.50", ask: str = "0.52") -> dict[str, object]:
    return {
        "executable_snapshot_id": "snapshot-test-1",
        "executable_snapshot_hash": "snapshot-hash-test-1",
        "executable_snapshot_min_tick_size": "0.01",
        "executable_snapshot_min_order_size": "0.01",
        "executable_snapshot_neg_risk": False,
        "executable_snapshot_orderbook_top_bid": bid,
        "executable_snapshot_orderbook_top_ask": ask,
    }


def _rpc_returning(balance_int: int):
    def _rpc(rpc_url, method, params):
        if method == "eth_call":
            return hex(balance_int)
        raise ValueError(f"unexpected method {method!r}")
    return _rpc


def _rpc_raising():
    def _rpc(rpc_url, method, params):
        raise ConnectionError("RPC unreachable (simulated)")
    return _rpc


def _count_chain_missing_rejects(conn: sqlite3.Connection, trade_id: str) -> int:
    rows = conn.execute(
        """
        SELECT payload_json
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'EXIT_ORDER_REJECTED'
        """,
        (trade_id,),
    ).fetchall()
    n = 0
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except (TypeError, ValueError):
            continue
        if str(payload.get("exit_reason") or "") == "EXIT_CHAIN_MISSING":
            n += 1
    return n


# ---------------------------------------------------------------------------
# FIX 2a — still-held routes to evaluate, NOT a re-stamp + cooldown skip
# ---------------------------------------------------------------------------

class TestStillHeldRoutesToEvaluate:
    """A genuinely-held position (balance>dust, no resting order) must reach the
    live exit lane this cycle instead of being re-stamped and skipped."""

    def test_still_held_returns_evaluate_action(self, monkeypatch):
        monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", _SAFE_ADDRESS)
        pos = _make_position(exit_state="")
        conn = _db()
        _seed_position_current(conn, pos)

        result = handle_exit_pending_missing(
            _portfolio(pos), pos, conn=conn, rpc_call=_rpc_returning(6_000_000)
        )

        # RED on un-fixed tree: action=="retry" (and a cooldown is armed).
        assert result["action"] == "evaluate", (
            f"still-held position must route to evaluate, got {result['action']!r}"
        )

    def test_still_held_releases_pending_exit_and_arms_no_cooldown(self, monkeypatch):
        monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", _SAFE_ADDRESS)
        pos = _make_position(exit_state="")
        conn = _db()
        _seed_position_current(conn, pos)

        handle_exit_pending_missing(
            _portfolio(pos), pos, conn=conn, rpc_call=_rpc_returning(6_000_000)
        )

        # Released from pending_exit so the full evaluate→execute lane runs.
        assert pos.state != "pending_exit", (
            f"still-held position must be released from pending_exit, got {pos.state!r}"
        )
        # No blocking cooldown was armed (that cooldown is what skipped the
        # position before it ever reached place_sell_order).
        assert not pos.next_exit_retry_at, (
            f"still-held route must NOT arm an exit cooldown, got {pos.next_exit_retry_at!r}"
        )

    def test_still_held_writes_no_exit_chain_missing_reject(self, monkeypatch):
        monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", _SAFE_ADDRESS)
        pos = _make_position(exit_state="")
        conn = _db()
        _seed_position_current(conn, pos)

        handle_exit_pending_missing(
            _portfolio(pos), pos, conn=conn, rpc_call=_rpc_returning(6_000_000)
        )
        conn.commit()

        # RED on un-fixed tree: _mark_exit_retry dual-writes an
        # EXIT_ORDER_REJECTED on the still-held branch every cycle (the prior
        # code stamped EXIT_CHAIN_MISSING_STILL_HELD). Count ALL rejects: the
        # still-held route must stamp NONE.
        total_rejects = conn.execute(
            """
            SELECT COUNT(*) FROM position_events
             WHERE position_id = ? AND event_type = 'EXIT_ORDER_REJECTED'
            """,
            (pos.trade_id,),
        ).fetchone()[0]
        assert total_rejects == 0, (
            f"still-held route must NOT stamp any EXIT_ORDER_REJECTED, got {total_rejects}"
        )

    def test_in_flight_resting_order_branch_is_non_mutating(self, monkeypatch):
        """BLOCKER-1: a position with a sell already on the book must be skipped
        WITHOUT mutating its exit state. The fill poller (check_pending_exits)
        owns the resting order and polls fills ONLY for exit_state in
        {sell_placed, sell_pending, exit_intent}; flipping to retry_pending would
        EVICT the order from that lane and risk a repost/cancel (the opposite of
        single-flight)."""
        monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", _SAFE_ADDRESS)
        for in_flight_state in sorted(_EXIT_LIFECYCLE_IN_FLIGHT_STATES):
            pos = _make_position(
                trade_id=f"inflight-{in_flight_state}",
                exit_state=in_flight_state,
                last_exit_order_id="ord-resting-1",
                order_status="placed",
                exit_retry_count=0,
                next_exit_retry_at="",
            )
            conn = _db()
            _seed_position_current(conn, pos)
            result = handle_exit_pending_missing(
                _portfolio(pos), pos, conn=conn, rpc_call=_rpc_returning(6_000_000)
            )
            conn.commit()

            # Skip (let the fill poller own it) — RED on un-fixed tree: "retry".
            assert result["action"] == "skip", (
                f"in-flight exit_state={in_flight_state!r} must non-mutating-skip, "
                f"got {result['action']!r}"
            )
            # State PRESERVED so check_pending_exits keeps polling the order.
            exit_state_value = getattr(pos.exit_state, "value", pos.exit_state)
            assert exit_state_value == in_flight_state, (
                f"in-flight branch must NOT mutate exit_state "
                f"(expected {in_flight_state!r}, got {exit_state_value!r})"
            )
            assert pos.last_exit_order_id == "ord-resting-1", (
                "in-flight branch must preserve last_exit_order_id"
            )
            assert not pos.next_exit_retry_at, (
                f"in-flight branch must NOT arm a cooldown, got {pos.next_exit_retry_at!r}"
            )
            assert pos.exit_retry_count == 0, (
                f"in-flight branch must NOT bump retry_count, got {pos.exit_retry_count}"
            )
            total_rejects = conn.execute(
                """
                SELECT COUNT(*) FROM position_events
                 WHERE position_id = ? AND event_type = 'EXIT_ORDER_REJECTED'
                """,
                (pos.trade_id,),
            ).fetchone()[0]
            assert total_rejects == 0, (
                f"in-flight branch must NOT write an EXIT_ORDER_REJECTED, got {total_rejects}"
            )

    def test_in_flight_resting_order_fills_with_exactly_one_submit(self, monkeypatch):
        """End-to-end single-flight: across two exit_pending_missing cycles with a
        resting sell that the venue fills on cycle 2, check_pending_exits detects
        the fill and place_sell_order is NEVER called (no second submit)."""
        from src.execution import exit_lifecycle as _el

        monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", _SAFE_ADDRESS)
        pos = _make_position(
            trade_id="inflight-fill-1",
            exit_state="sell_placed",
            exit_reason="EDGE_REVERSAL",
            last_exit_order_id="ord-resting-2",
            order_status="placed",
        )
        conn = _db()
        _seed_position_current(conn, pos)
        portfolio = _portfolio(pos)

        # Any call to the live emitter would be a DOUBLE-SUBMIT.
        submit_calls = {"n": 0}

        def _boom_place(*a, **k):
            submit_calls["n"] += 1
            raise AssertionError("place_sell_order must NOT be called for a resting order")

        monkeypatch.setattr(_el, "place_sell_order", _boom_place)

        # Cycle 1: chain-truth still-held, in-flight → non-mutating skip.
        r1 = handle_exit_pending_missing(
            portfolio, pos, conn=conn, rpc_call=_rpc_returning(6_000_000)
        )
        conn.commit()
        assert r1["action"] == "skip"
        assert getattr(pos.exit_state, "value", pos.exit_state) == "sell_placed"

        # Cycle 2: the resting order fills. The fill poller (check_pending_exits),
        # which runs in the exit-preflight BEFORE the monitor loop, owns it.
        class FillingClob:
            def get_order_status(self, order_id):
                return {"status": "CONFIRMED", "avgPrice": 0.5}

        stats = _el.check_pending_exits(portfolio, FillingClob(), conn=conn)
        conn.commit()

        assert stats["filled"] == 1, f"the resting order must fill exactly once, got {stats}"
        assert submit_calls["n"] == 0, "place_sell_order must never fire for a resting order"
        # The filled position is economically closed via the poller, not resubmitted.
        closed = stats["filled_positions"][0]
        assert getattr(closed.exit_state, "value", closed.exit_state) == "sell_filled"


# ---------------------------------------------------------------------------
# FIX 2a — RPC fall-through dedupes the identical reject (≤1 per state-epoch)
# ---------------------------------------------------------------------------

class TestRpcFallThroughDedupe:
    """Two consecutive RPC-failure cycles on an unchanged position must NOT
    accrete two identical EXIT_ORDER_REJECTED rows."""

    def test_two_cycles_emit_at_most_one_identical_reject(self, monkeypatch):
        monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", _SAFE_ADDRESS)
        pos = _make_position(exit_state="")
        conn = _db()
        _seed_position_current(conn, pos)

        # Cycle 1: RPC fails → legacy fall-through writes the reject.
        handle_exit_pending_missing(
            _portfolio(pos), pos, conn=conn, rpc_call=_rpc_raising()
        )
        conn.commit()
        after_first = _count_chain_missing_rejects(conn, pos.trade_id)

        # Cycle 2: same position, no intervening state change, RPC still fails.
        handle_exit_pending_missing(
            _portfolio(pos), pos, conn=conn, rpc_call=_rpc_raising()
        )
        conn.commit()
        after_second = _count_chain_missing_rejects(conn, pos.trade_id)

        assert after_first == 1, f"first cycle should stamp exactly 1 reject, got {after_first}"
        # RED on un-fixed tree: after_second == 2 (the re-stamp loop).
        assert after_second == 1, (
            f"second identical cycle must be deduped (≤1 reject per state-epoch), "
            f"got {after_second}"
        )

    def test_intervening_state_change_reopens_the_epoch(self, monkeypatch):
        """The dedupe must not hide a genuine escalation: an intervening
        position event re-opens the epoch so the next reject is allowed."""
        from src.execution.exit_lifecycle import _dual_write_canonical_pending_exit_if_available

        monkeypatch.setenv("POLYMARKET_FUNDER_ADDRESS", _SAFE_ADDRESS)
        pos = _make_position(exit_state="")
        conn = _db()
        _seed_position_current(conn, pos)

        handle_exit_pending_missing(_portfolio(pos), pos, conn=conn, rpc_call=_rpc_raising())
        conn.commit()
        # Simulate an intervening state-change event (a DIFFERENT reject reason).
        _dual_write_canonical_pending_exit_if_available(
            conn, pos, reason="EXIT_OTHER", error="x", event_type="EXIT_ORDER_REJECTED"
        )
        conn.commit()
        handle_exit_pending_missing(_portfolio(pos), pos, conn=conn, rpc_call=_rpc_raising())
        conn.commit()

        # The epoch re-opened, so a 2nd EXIT_CHAIN_MISSING reject is permitted.
        assert _count_chain_missing_rejects(conn, pos.trade_id) == 2, (
            "an intervening state change must re-open the reject epoch"
        )


# ---------------------------------------------------------------------------
# FIX 2b — static-time close defers; venue-confirmed close stamps immediately
# ---------------------------------------------------------------------------

class TestDay0StaticClosedDefersTerminalStamp:
    """The day0 closed-market pre-emption must only defer for the static-time
    source, and the deferred terminal stamp must require NO executable bid."""

    # NOTE: the static-source-vs-venue-source discrimination is covered
    # BEHAVIORALLY by TestDay0StaticClosedBehavioral
    # (test_static_close_with_live_bid_is_not_pre_empted /
    # test_static_close_with_no_bid_still_stamps_terminal). A prior source-text
    # assertion that read cycle_runtime.__file__ was removed (PR #416 review
    # 2026-06-21): it was brittle to refactors and bypassed the behavioral contract.

    def test_deferred_stamp_requires_no_executable_bid(self):
        """The deferred terminal stamp fires only when best_bid is not finite —
        a finite executable bid keeps the position tradable (no stamp)."""
        # finite bid → tradable → must NOT be deemed untradeable
        assert ExitContext._is_finite(0.42) is True
        # missing bid → genuinely untradeable → terminal stamp is correct
        assert ExitContext._is_finite(None) is False


class TestDay0StaticClosedBehavioral:
    """Behavioral: a day0 position on a market past its STATIC market_close_at,
    with a live executable bid, must NOT be pre-empted into
    MARKET_CLOSED_AWAITING_SETTLEMENT before the exit lane runs."""

    def _seed_past_close_snapshot(self, conn: sqlite3.Connection, condition_id: str) -> None:
        conn.execute(
            """
            INSERT INTO executable_market_snapshots (
                snapshot_id, gamma_market_id, event_id, condition_id, question_id,
                yes_token_id, no_token_id, enable_orderbook, active, closed,
                min_tick_size, min_order_size, fee_details_json, token_map_json,
                neg_risk, orderbook_top_bid, orderbook_top_ask, orderbook_depth_json,
                raw_gamma_payload_hash, raw_clob_market_info_hash, raw_orderbook_hash,
                authority_tier, captured_at, freshness_deadline,
                market_end_at, market_close_at
            ) VALUES (
                ?, 'gm-1', 'ev-1', ?, 'q-1',
                ?, 'tok_no_1', 1, 1, 0,
                '0.01', '1', '{}', '{}',
                0, '0.42', '0.6', '{}',
                'h1', 'h2', 'h3',
                'CLOB', ?, ?,
                ?, ?
            )
            """,
            (
                "snap-static-close-1",
                condition_id,
                _ASSET_ID,
                "2026-06-19T20:00:00+00:00",
                "2026-06-20T23:59:00+00:00",
                "2026-06-19T23:59:00+00:00",  # market_end_at — in the past
                "2026-06-19T23:59:00+00:00",  # market_close_at — in the past vs _utcnow
            ),
        )
        conn.commit()

    def _run(self, monkeypatch, *, best_bid):
        import logging as _logging
        from datetime import datetime, timezone

        import numpy as np

        from src.contracts import EdgeContext, EntryMethod
        from src.engine import cycle_runtime

        monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
        conn = _db()
        condition_id = "0x" + "cd" * 32
        self._seed_past_close_snapshot(conn, condition_id)

        pos = _make_position(
            trade_id="day0-static-close-1",
            state="day0_window",
            chain_state="synced",
            direction="buy_yes",
            condition_id=condition_id,
            market_id=condition_id,
            no_token_id="tok_no_1",
            exit_state="",
        )
        _seed_position_current(conn, pos)
        portfolio = _portfolio(pos)

        # clob WITHOUT get_clob_market_info → falls to the static-time path.
        class StaticClob:
            def get_best_bid_ask(self, token_id):
                return (best_bid if best_bid is not None else None), 0.6, 100.0, 100.0

        class Tracker:
            def record_exit(self, position):
                pass

        def mock_refresh(conn_, clob_, position):
            # refresh_position normally sets last_monitor_best_bid; emulate it so
            # _build_exit_context observes the executable bid (or its absence).
            position.last_monitor_best_bid = best_bid
            position.last_monitor_market_price_is_fresh = True
            position.last_monitor_prob_is_fresh = True
            return EdgeContext(
                p_raw=np.array([]), p_cal=np.array([]),
                p_market=np.array([position.entry_price]),
                p_posterior=position.p_posterior,
                forward_edge=0.0, alpha=0.0,
                confidence_band_upper=0.0, confidence_band_lower=0.0,
                entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
                decision_snapshot_id="snap1", n_edges_found=1, n_edges_after_fdr=1,
                market_velocity_1h=0.0, divergence_score=0.0,
            )

        monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", mock_refresh)
        monkeypatch.setattr(
            cycle_runtime, "_emit_monitor_refreshed_canonical_if_available",
            lambda conn_, pos_, *, deps, **kwargs: True,
        )
        monkeypatch.setattr(
            "src.execution.day0_hard_fact_exit.evaluate_hard_fact_exit",
            lambda *, position, city, now=None, world_conn=None, **kwargs: None,
        )

        results = []

        class Artifact:
            def add_monitor_result(self, result):
                results.append(result)

            def add_exit(self, *a, **k):
                pass

        deps = type(
            "Deps", (),
            {
                "MonitorResult": type(
                    "MonitorResult", (),
                    {"__init__": lambda self, **kw: self.__dict__.update(kw)},
                ),
                "logger": _logging.getLogger("test_fix2b"),
                "cities_by_name": {
                    "London": type("City", (), {"timezone": "Europe/London"})()
                },
                "_utcnow": staticmethod(
                    lambda: datetime(2026, 6, 20, 6, 0, tzinfo=timezone.utc)
                ),
            },
        )
        summary = {"monitors": 0, "exits": 0}
        cycle_runtime.execute_monitoring_phase(
            conn, StaticClob(), portfolio, Artifact(), Tracker(), summary,
            deps=deps, exit_order_submit_enabled=False,
        )
        return results, summary, pos

    def test_static_close_with_live_bid_is_not_pre_empted(self, monkeypatch):
        results, summary, pos = self._run(monkeypatch, best_bid=0.42)

        # RED on un-fixed tree: the position is stamped MARKET_CLOSED and
        # `continue`d BEFORE refresh/evaluate (skipped count incremented, no
        # tradable-bid-preserved breadcrumb).
        assert summary.get("monitor_skipped_closed_market_pending_settlement", 0) == 0, (
            "a static-closed market with a live executable bid must NOT be "
            "pre-empted into MARKET_CLOSED before the exit lane runs"
        )
        assert summary.get("day0_static_closed_market_tradable_bid_preserved", 0) == 1, (
            "the deferred lane must preserve a tradable (finite-bid) static-closed position"
        )
        reasons = [str(getattr(r, "exit_reason", "")) for r in results]
        assert not any(
            "MARKET_CLOSED_AWAITING_SETTLEMENT" == r for r in reasons
        ), "no terminal MARKET_CLOSED monitor result while a bid still exists"

    def test_static_close_with_no_bid_still_stamps_terminal(self, monkeypatch):
        results, summary, pos = self._run(monkeypatch, best_bid=None)

        # No executable bid → genuinely untradeable → the deferred lane applies
        # the terminal stamp after evaluation (just later, not pre-emptively).
        assert summary.get("monitor_closed_market_pending_settlement_after_eval", 0) == 1, (
            "a static-closed market with NO executable bid must still receive the "
            "terminal MARKET_CLOSED stamp (post-eval)"
        )

    def test_venue_closed_still_records_day0_hard_fact(self, monkeypatch):
        """Venue closed blocks sell submission, not settlement-observation truth."""

        import logging as _logging
        from datetime import datetime, timezone

        from src.engine import cycle_runtime

        conn = _db()
        pos = _make_position(
            trade_id="day0-venue-closed-hard-fact",
            state="day0_window",
            chain_state="synced",
            direction="buy_no",
            condition_id=_CONDITION_ID,
            market_id=_CONDITION_ID,
            no_token_id=_ASSET_ID,
            token_id="yes-token",
            p_posterior=0.8,
            entry_price=0.77,
        )
        _seed_position_current(conn, pos)
        portfolio = _portfolio(pos)

        class ClosedClob:
            def get_clob_market_info(self, condition_id):
                return {"closed": True, "accepting_orders": False}

            def get_best_bid_ask(self, token_id):  # pragma: no cover - must not be reached
                raise AssertionError("closed hard-fact monitor should not need a quote")

        monkeypatch.setattr(
            "src.execution.day0_hard_fact_exit.evaluate_hard_fact_exit",
            lambda *, position, city, now=None, world_conn=None, **kwargs: SimpleNamespace(
                action="EXIT_DEAD_BIN",
                reason="observed high entered held NO bin",
                source="durable_observation_instants",
            ),
        )
        monkeypatch.setattr(
            cycle_runtime, "_emit_monitor_refreshed_canonical_if_available",
            lambda conn_, pos_, *, deps, **kwargs: True,
        )

        results = []

        class Artifact:
            def add_monitor_result(self, result):
                results.append(result)

            def add_exit(self, *a, **k):
                pass

        deps = type(
            "Deps",
            (),
            {
                "MonitorResult": type(
                    "MonitorResult",
                    (),
                    {"__init__": lambda self, **kw: self.__dict__.update(kw)},
                ),
                "logger": _logging.getLogger("test_venue_closed_hard_fact"),
                "cities_by_name": {
                    "London": type("City", (), {"timezone": "Europe/London"})()
                },
                "_utcnow": staticmethod(
                    lambda: datetime(2026, 6, 20, 6, 0, tzinfo=timezone.utc)
                ),
            },
        )
        summary = {"monitors": 0, "exits": 0}

        cycle_runtime.execute_monitoring_phase(
            conn,
            ClosedClob(),
            portfolio,
            Artifact(),
            type("Tracker", (), {"record_exit": lambda self, position: None})(),
            summary,
            deps=deps,
            exit_order_submit_enabled=False,
        )

        assert summary.get("monitor_skipped_closed_market_pending_settlement", 0) == 0
        assert summary.get("day0_hard_fact_closed_market_monitors", 0) == 1
        assert summary.get("day0_hard_fact_closed_market_hold_to_settlement", 0) == 1
        assert results and results[0].fresh_prob == pytest.approx(0.0)
        assert "DAY0_HARD_FACT_BIN_DEAD_MARKET_CLOSED" in results[0].exit_reason
        assert pos.last_monitor_prob == pytest.approx(0.0)
        assert pos.last_monitor_prob_is_fresh is True
        assert pos.last_monitor_market_price is None
        assert pos.last_monitor_market_price_is_fresh is False
        hold_event = conn.execute(
            """
            SELECT payload_json
              FROM position_events
             WHERE position_id = ?
               AND event_type = 'MONITOR_REFRESHED'
               AND caused_by = 'market_closed_hold_to_settlement'
             ORDER BY sequence_no DESC
             LIMIT 1
            """,
            (pos.trade_id,),
        ).fetchone()
        assert hold_event is not None
        assert json.loads(hold_event["payload_json"])["semantic_event"] == (
            "MARKET_CLOSED_HOLD_TO_SETTLEMENT"
        )


# ---------------------------------------------------------------------------
# FIX 2d — canonical EXIT_ORDER_POSTED from the spine emitter
# ---------------------------------------------------------------------------

class TestCanonicalExitOrderPostedProvenance:
    """A successful spine place_sell_order must append a canonical
    position_events.EXIT_ORDER_POSTED with source_module=src.execution.exit_lifecycle."""

    def test_spine_post_writes_canonical_exit_order_posted(self):
        pos = _make_position(state="pending_exit", exit_state="exit_intent")
        conn = _db()
        _seed_position_current(conn, pos)

        exit_context = ExitContext(
            current_market_price=0.5,
            current_market_price_is_fresh=True,
            best_bid=0.5,
            fresh_prob=0.4,
            hours_to_settlement=3.0,
            exit_reason="EDGE_REVERSAL",
        )
        exit_intent = build_exit_intent(pos, exit_context)

        placed = {"orderID": "ord-spine-1", "status": "placed", "price": 0.5, "shares": 10.0}

        with patch("src.execution.exit_lifecycle.place_sell_order", return_value=placed), \
             patch(
                 "src.execution.exit_lifecycle._latest_or_capture_exit_snapshot_context",
                 return_value=_executable_snapshot_context(bid="0.50", ask="0.52"),
             ), \
             patch(
                 "src.execution.exit_lifecycle.check_sell_collateral",
                 return_value=(True, ""),
             ), \
             patch(
                 "src.execution.exit_lifecycle._refresh_exit_collateral_snapshot_for_submit",
                 return_value=None,
             ):
            # clob=None so the quick-fill check is skipped (stays sell_pending).
            outcome = execute_exit(
                portfolio=_portfolio(pos),
                position=pos,
                exit_context=exit_context,
                clob=None,
                conn=conn,
                exit_intent=exit_intent,
            )
        conn.commit()

        assert (
            outcome.startswith("sell_placed")
            or outcome.startswith("sell_pending")
            or outcome.startswith("exit_filled")
        ), (
            f"spine post should report a placed/pending/filled sell, got {outcome!r}"
        )
        rows = conn.execute(
            """
            SELECT source_module
              FROM position_events
             WHERE position_id = ?
               AND event_type = 'EXIT_ORDER_POSTED'
            """,
            (pos.trade_id,),
        ).fetchall()
        # RED on un-fixed tree: 0 canonical EXIT_ORDER_POSTED rows (the spine
        # only wrote the legacy execution_fact row).
        assert rows, "spine place_sell_order must append a canonical EXIT_ORDER_POSTED row"
        assert any(
            str(r["source_module"]) == "src.execution.exit_lifecycle" for r in rows
        ), (
            "canonical EXIT_ORDER_POSTED must carry source_module="
            "src.execution.exit_lifecycle (not command_recovery)"
        )

    def test_exit_intent_writes_decision_evidence_payload(self):
        pos = _make_position(state="day0_window", exit_state="")
        conn = _db()
        _seed_position_current(conn, pos)

        exit_context = ExitContext(
            current_market_price=0.5,
            current_market_price_is_fresh=True,
            best_bid=0.48,
            best_ask=0.52,
            fresh_prob=0.4,
            fresh_prob_is_fresh=True,
            market_vig=0.04,
            hours_to_settlement=0.5,
            position_state="day0_window",
            day0_active=True,
            exit_reason="SETTLEMENT_IMMINENT",
        )
        exit_intent = build_exit_intent(pos, exit_context)
        placed = {"orderID": "ord-exit-intent-1", "status": "placed", "price": 0.48, "shares": 10.0}

        with patch("src.execution.exit_lifecycle.place_sell_order", return_value=placed), \
             patch(
                 "src.execution.exit_lifecycle._latest_or_capture_exit_snapshot_context",
                 return_value=_executable_snapshot_context(bid="0.48", ask="0.52"),
             ), \
             patch(
                 "src.execution.exit_lifecycle.check_sell_collateral",
                 return_value=(True, ""),
             ), \
             patch(
                 "src.execution.exit_lifecycle._refresh_exit_collateral_snapshot_for_submit",
                 return_value=None,
             ):
            execute_exit(
                portfolio=_portfolio(pos),
                position=pos,
                exit_context=exit_context,
                clob=None,
                conn=conn,
                exit_intent=exit_intent,
            )
        conn.commit()

        row = conn.execute(
            """
            SELECT payload_json
              FROM position_events
             WHERE position_id = ?
               AND event_type = 'EXIT_INTENT'
             ORDER BY sequence_no DESC
             LIMIT 1
            """,
            (pos.trade_id,),
        ).fetchone()
        assert row is not None
        payload = json.loads(row["payload_json"])
        assert payload["exit_intent_reason"] == "SETTLEMENT_IMMINENT"
        assert payload["exit_intent_current_market_price"] == pytest.approx(0.5)
        assert payload["exit_intent_best_bid"] == pytest.approx(0.48)
        assert payload["exit_intent_best_ask"] == pytest.approx(0.52)
        assert payload["exit_intent_market_vig"] == pytest.approx(0.04)
        assert payload["exit_intent_fresh_prob"] == pytest.approx(0.4)
        assert payload["exit_intent_fresh_prob_is_fresh"] is True
        assert payload["exit_intent_hours_to_settlement"] == pytest.approx(0.5)
        assert payload["exit_intent_position_state"] == "day0_window"
        assert payload["exit_intent_day0_active"] is True


# ---------------------------------------------------------------------------
# FIX 2c — monitor-cadence watchdog flags a >2x interval gap
# ---------------------------------------------------------------------------

class TestMonitorCadenceWatchdog:
    """The watchdog must flag a MONITOR_REFRESHED gap beyond ~2x the 2-min
    interval (the live 8.8h 06-19 00:41→09:30 whole-book silence)."""

    def _insert_monitor_refreshed(self, conn: sqlite3.Connection, occurred_at: str) -> None:
        conn.execute(
            """
            INSERT INTO position_events (
                event_id, position_id, event_version, sequence_no, event_type,
                occurred_at, strategy_key, idempotency_key, source_module, env,
                payload_json
            ) VALUES (?, ?, 1, 1, 'MONITOR_REFRESHED', ?, 'opening_inertia', ?, 'test', 'live', '{}')
            """,
            (f"e:{occurred_at}", "p1", occurred_at, f"i:{occurred_at}"),
        )
        conn.commit()

    def test_watchdog_flags_the_06_19_gap(self):
        from src.main import _check_monitor_cadence_watchdog

        conn = _db()
        # The live gap: last refresh at 00:41, no refresh until 09:30 (8.8h).
        self._insert_monitor_refreshed(conn, "2026-06-19T00:41:00+00:00")
        summary: dict = {}

        # observed_at is "now" inside the function; the 00:41 row is many hours
        # in the past relative to any real test run on/after 2026-06-19, so the
        # gap vastly exceeds the 4-min threshold.
        record = _check_monitor_cadence_watchdog(conn, summary)

        assert record is not None, "watchdog must flag the multi-hour cadence gap"
        assert "monitor_cadence_gap_flagged" in summary
        assert record["gap_factor"] >= 2.0
        assert summary["monitor_cadence_gap_flagged"]["gap_seconds"] > 240.0

    def test_watchdog_silent_within_cadence(self):
        from datetime import datetime, timezone

        from src.main import _check_monitor_cadence_watchdog

        conn = _db()
        # A refresh 60s ago is well within the 2-min interval → no flag.
        recent = datetime.now(timezone.utc).isoformat()
        self._insert_monitor_refreshed(conn, recent)
        summary: dict = {}

        record = _check_monitor_cadence_watchdog(conn, summary)
        assert record is None, "watchdog must NOT flag a healthy cadence"
        assert "monitor_cadence_gap_flagged" not in summary
