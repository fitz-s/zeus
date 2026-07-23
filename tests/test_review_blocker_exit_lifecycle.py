# Created: 2026-07-20
# Lifecycle: created=2026-07-20; last_reviewed=2026-07-20
# Authority basis: money-path review blockers C2-C5 (three GPT-5.6 Pro merge
#   reviews + local topology verifier), scratchpad/FIX_PLAN.md.
# Purpose: Behavioral antibodies for exit-lifecycle settlement-close correctness,
#   the restart re-decision reconciliation witness, and held-exit freshness.
#   Each test FAILS on the pre-fix head and PASSES after the fix.
"""Antibody tests for review blockers C2-C5 in src/execution/exit_lifecycle.py.

- C2: an EXIT-command underfill must NOT be classified as a full close. The
      full-close target is the exact command size, not a 0.011 dust tolerance,
      so a partial fill preserves the positive residual and does not fabricate
      a chain-zero.
- C3: fill economics accumulate as exact Decimal atoms, never SQLite REAL
      (binary float) sums, on the close/settlement boundary.
- C4: a backoff-exhausted pending_exit returns to live re-decision only when the
      latest EXIT venue command is absent or terminal; an ACTIVE/OPEN command or
      an UNKNOWN side effect keeps the position in pending_exit (reconciliation,
      never a second sell).
- C5: a null/expired executable snapshot cannot suppress a held exit/re-decision;
      it releases to fresh re-decision. A fresh snapshot proving the size is
      executable overrides stale historical dust text.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tests.test_exchange_reconcile import append_trade_fact, seed_command
from tests.test_exit_safety import _ensure_snapshot

TOKEN = "yes-token-m5"
NO_TOKEN = "yes-token-m5-no"
NOW = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
# Far-future deadline for "fresh" snapshots exercised through helpers that read
# the real wall clock via _utcnow() (no now-injection). Stays fresh regardless
# of the run date.
FRESH_DEADLINE = datetime(2099, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    from src.state.collateral_ledger import init_collateral_schema
    from src.state.db import init_schema, init_schema_trade_only

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    init_schema_trade_only(c)
    init_collateral_schema(c)
    yield c
    c.close()


def _position(
    *,
    trade_id: str,
    shares: float = 1.0,
    chain_shares: float = 1.0,
    exit_state: str = "backoff_exhausted",
    exit_reason: str = "FAMILY_DIRECT_SELL_DOMINATES_HOLD",
    last_exit_error: str = "previous_order_attempt_budget_exhausted",
):
    from src.state.portfolio import Position

    return Position(
        trade_id=trade_id,
        market_id="condition-test",
        condition_id="condition-test",
        city="Paris",
        cluster="Paris",
        target_date="2026-07-08",
        bin_label="34C",
        direction="buy_yes",
        unit="C",
        token_id=TOKEN,
        no_token_id=NO_TOKEN,
        entry_price=0.52,
        size_usd=float(shares) * 0.52,
        shares=shares,
        chain_shares=chain_shares,
        cost_basis_usd=float(shares) * 0.52,
        state="pending_exit",
        pre_exit_state="day0_window",
        chain_state="synced",
        strategy_key="settlement_capture",
        exit_state=exit_state,
        order_status="backoff_exhausted",
        exit_reason=exit_reason,
        last_exit_error=last_exit_error,
        env="live",
        entered_at="2026-07-08T09:40:58+00:00",
        last_monitor_at="2026-07-08T11:00:00+00:00",
    )


def _set_command_state(conn: sqlite3.Connection, command_id: str, state: str) -> None:
    conn.execute(
        "UPDATE venue_commands SET state = ? WHERE command_id = ?",
        (state, command_id),
    )


# ---------------------------------------------------------------------------
# C2 — settlement dust tolerance must not zero chain holdings on an underfill
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filled", ["0.999", "0.990", "0.989", "0.988"])
def test_c2_underfill_of_full_close_command_is_not_a_close(conn, filled):
    """A partial fill of a 1.000 full-close command must NOT close (no chain-zero).

    Pre-fix: EXIT_FULL_CLOSE_DUST_TOLERANCE=0.011 classified fills within 0.011 of
    the target (0.999/0.990/0.989) as a full close -> chain fields zeroed, real
    residual lost. Post-fix: exact-atomics comparison vs the command target -> any
    positive shortfall preserves the residual (candidate returns None).
    """
    from src.execution import exit_lifecycle

    seed_command(
        conn,
        command_id="cmd-underfill",
        venue_order_id="ord-underfill",
        position_id="pos-underfill",
        token_id=TOKEN,
        side="SELL",
        size=1.0,
        price=0.60,
        state="ACKED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-underfill",
        venue_order_id="ord-underfill",
        token_id=TOKEN,
        trade_id="trade-underfill",
        size=filled,
        fill_price="0.61",
        state="CONFIRMED",
    )
    pos = _position(trade_id="pos-underfill", shares=1.0, chain_shares=1.0)

    candidate = exit_lifecycle._exit_trade_fact_close_candidate(conn, pos)

    assert candidate is None, (
        f"underfill {filled} of 1.000 must not be a full-close candidate; "
        "a positive residual may not be zeroed absent a chain-confirmed-zero"
    )


def test_c2_exact_full_fill_of_command_still_closes(conn):
    """Guard: the exact-atomics fix must not strand a genuine full fill."""
    from src.execution import exit_lifecycle

    seed_command(
        conn,
        command_id="cmd-full",
        venue_order_id="ord-full",
        position_id="pos-full",
        token_id=TOKEN,
        side="SELL",
        size=1.0,
        price=0.60,
        state="ACKED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-full",
        venue_order_id="ord-full",
        token_id=TOKEN,
        trade_id="trade-full",
        size="1.0",
        fill_price="0.61",
        state="CONFIRMED",
    )
    pos = _position(trade_id="pos-full", shares=1.0, chain_shares=1.0)

    candidate = exit_lifecycle._exit_trade_fact_close_candidate(conn, pos)

    assert candidate is not None
    assert candidate["closes_position"] is True
    assert candidate["filled_size"] == Decimal("1.0")


def test_c2_underfill_check_pending_exits_preserves_chain_shares(conn):
    """End-to-end: an underfill leaves chain_shares untouched (not fabricated-zero)."""
    from src.execution import exit_lifecycle
    from src.state.portfolio import PortfolioState

    seed_command(
        conn,
        command_id="cmd-e2e",
        venue_order_id="ord-e2e",
        position_id="pos-e2e",
        token_id=TOKEN,
        side="SELL",
        size=1.0,
        price=0.60,
        state="ACKED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-e2e",
        venue_order_id="ord-e2e",
        token_id=TOKEN,
        trade_id="trade-e2e",
        size="0.990",
        fill_price="0.61",
        state="CONFIRMED",
    )
    # retry_pending so the position is an active pending-exit scan candidate that
    # reaches _exit_trade_fact_close_candidate; next_exit_retry_at empty => no poll.
    pos = _position(
        trade_id="pos-e2e", shares=1.0, chain_shares=1.0, exit_state="retry_pending"
    )
    pos.last_exit_order_id = "ord-e2e"

    class FakeClob:
        def get_order_status(self, order_id):
            return {"status": "OPEN"}

        def cancel_order(self, order_id):
            return {"status": "CANCELLED"}

    stats = exit_lifecycle.check_pending_exits(
        PortfolioState(positions=[pos]), FakeClob(), conn=conn
    )

    assert stats.get("filled_from_trade_fact", 0) == 0
    assert pos.chain_shares == 1.0, "underfill must not zero chain_shares"


# ---------------------------------------------------------------------------
# C3 — settlement fill economics must be exact Decimal, never SQLite REAL
# ---------------------------------------------------------------------------


def test_c3_multi_fill_accumulates_exact_decimal_not_float(conn):
    """Two 0.1 + 0.2 fills must sum to EXACT Decimal('0.3'), not float 0.30000...4.

    Pre-fix: SUM(CAST(... AS REAL)) over the fills yields 0.30000000000000004 on
    the settlement boundary. Post-fix: exact Decimal accumulation of the TEXT
    atoms yields Decimal('0.3').
    """
    from src.execution import exit_lifecycle

    seed_command(
        conn,
        command_id="cmd-exact",
        venue_order_id="ord-exact",
        position_id="pos-exact",
        token_id=TOKEN,
        side="SELL",
        size=0.3,
        price=0.50,
        state="ACKED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-exact",
        venue_order_id="ord-exact",
        token_id=TOKEN,
        trade_id="trade-exact-a",
        size="0.1",
        fill_price="0.5",
        state="CONFIRMED",
    )
    append_trade_fact(
        conn,
        command_id="cmd-exact",
        venue_order_id="ord-exact",
        token_id=TOKEN,
        trade_id="trade-exact-b",
        size="0.2",
        fill_price="0.5",
        state="CONFIRMED",
    )
    pos = _position(trade_id="pos-exact", shares=0.3, chain_shares=0.3)

    candidate = exit_lifecycle._exit_trade_fact_close_candidate(conn, pos)

    assert candidate is not None
    assert candidate["filled_size"] == Decimal("0.3")
    assert candidate["filled_size"] != Decimal("0.30000000000000004")
    assert candidate["fill_price"] == Decimal("0.5")


# ---------------------------------------------------------------------------
# C4 — restart re-decision requires an absent/terminal command witness
# ---------------------------------------------------------------------------


def test_c4_witness_absent_command_permits_release(conn):
    from src.execution import exit_lifecycle

    pos = _position(trade_id="pos-c4-absent", shares=5.0, chain_shares=5.0)
    witness = exit_lifecycle._latest_exit_command_release_witness(pos, conn=conn)
    assert witness == (True, "")


@pytest.mark.parametrize("blocking_state", ["ACKED", "POSTING", "SUBMIT_UNKNOWN_SIDE_EFFECT", "PARTIAL", "REVIEW_REQUIRED"])
def test_c4_witness_open_or_unknown_command_blocks_release(conn, blocking_state):
    from src.execution import exit_lifecycle

    seed_command(
        conn,
        command_id="cmd-c4-open",
        venue_order_id="ord-c4-open",
        position_id="pos-c4-open",
        token_id=TOKEN,
        side="SELL",
        size=5.0,
        price=0.60,
        state="ACKED",
    )
    _set_command_state(conn, "cmd-c4-open", blocking_state)
    pos = _position(trade_id="pos-c4-open", shares=5.0, chain_shares=5.0)

    permits, state = exit_lifecycle._latest_exit_command_release_witness(pos, conn=conn)
    assert permits is False
    assert state == blocking_state


@pytest.mark.parametrize("terminal_state", ["REJECTED", "CANCELLED", "EXPIRED", "FILLED"])
def test_c4_witness_terminal_command_permits_release(conn, terminal_state):
    from src.execution import exit_lifecycle

    seed_command(
        conn,
        command_id="cmd-c4-term",
        venue_order_id="ord-c4-term",
        position_id="pos-c4-term",
        token_id=TOKEN,
        side="SELL",
        size=5.0,
        price=0.60,
        state="ACKED",
    )
    _set_command_state(conn, "cmd-c4-term", terminal_state)
    pos = _position(trade_id="pos-c4-term", shares=5.0, chain_shares=5.0)

    assert exit_lifecycle._latest_exit_command_release_witness(pos, conn=conn) == (True, "")


def test_c4_release_blocked_while_exit_command_active(conn):
    """The full release entrypoint must keep an ACTIVE-command position held.

    Pre-fix: release_backoff_exhausted_pending_exit_for_redecision flipped the
    position back to re-decision without checking the venue command -> a second
    sell could be authorized while the original order is still ACKED/open.
    """
    from src.execution import exit_lifecycle

    seed_command(
        conn,
        command_id="cmd-c4-active",
        venue_order_id="ord-c4-active",
        position_id="pos-c4-active",
        token_id=TOKEN,
        side="SELL",
        size=5.0,
        price=0.60,
        state="ACKED",
    )
    pos = _position(trade_id="pos-c4-active", shares=5.0, chain_shares=5.0)

    released = exit_lifecycle.release_backoff_exhausted_pending_exit_for_redecision(
        pos, conn=conn
    )

    assert released is False
    assert pos.state == "pending_exit"
    assert str(getattr(pos.exit_state, "value", pos.exit_state)) == "backoff_exhausted"


def test_c4_release_allowed_when_command_terminal_rejected(conn):
    """A terminally-rejected sell command must release the held position."""
    from src.execution import exit_lifecycle

    seed_command(
        conn,
        command_id="cmd-c4-rej",
        venue_order_id="ord-c4-rej",
        position_id="pos-c4-rej",
        token_id=TOKEN,
        side="SELL",
        size=5.0,
        price=0.60,
        state="ACKED",
    )
    _set_command_state(conn, "cmd-c4-rej", "REJECTED")
    pos = _position(trade_id="pos-c4-rej", shares=5.0, chain_shares=5.0)

    released = exit_lifecycle.release_backoff_exhausted_pending_exit_for_redecision(
        pos, conn=conn
    )

    assert released is True
    assert pos.state != "pending_exit"


# ---------------------------------------------------------------------------
# C5 — held-exit suppression requires a non-null, unexpired snapshot
# ---------------------------------------------------------------------------


def test_c5_expired_snapshot_does_not_suppress_release(conn):
    """A stale (expired) min-order snapshot must not hold a live re-decision.

    Pre-fix: _is_below_latest_snapshot_min_order read the latest snapshot with no
    freshness gate -> an expired snapshot (min_order > shares) marked the position
    dust and suppressed release. Post-fix: freshness-strict -> expired is ignored,
    the position releases to re-decision.
    """
    from src.execution import exit_lifecycle

    _ensure_snapshot(
        conn,
        token_id=TOKEN,
        no_token_id=NO_TOKEN,
        selected_outcome_token_id=TOKEN,
        min_order_size="5",
        snapshot_id="snap-c5-expired",
        captured_at=NOW - timedelta(days=2),
        freshness_deadline=NOW - timedelta(days=1),
    )
    pos = _position(
        trade_id="pos-c5-expired",
        shares=1.0,
        chain_shares=1.0,
        exit_reason="FAMILY_DIRECT_SELL_DOMINATES_HOLD",
        last_exit_error="budget_exhausted",
    )

    assert exit_lifecycle._is_non_executable_dust_hold(pos, conn=conn) is False
    released = exit_lifecycle.release_backoff_exhausted_pending_exit_for_redecision(
        pos, conn=conn
    )
    assert released is True
    assert pos.state != "pending_exit"


def test_c5_fresh_executable_snapshot_overrides_stale_dust_text(conn):
    """A fresh snapshot proving the size is executable overrides stale dust text.

    Pre-fix: _dust_evidence_marks_non_executable let a historical '[DUST: ...]'
    reason suppress re-decision even when the size is now executable. Post-fix: a
    fresh snapshot with shares >= min_order is authoritative -> not dust.
    """
    from src.execution import exit_lifecycle

    _ensure_snapshot(
        conn,
        token_id=TOKEN,
        no_token_id=NO_TOKEN,
        selected_outcome_token_id=TOKEN,
        min_order_size="1",
        snapshot_id="snap-c5-fresh-exec",
        captured_at=NOW,
        freshness_deadline=FRESH_DEADLINE,
    )
    pos = _position(
        trade_id="pos-c5-fresh-exec",
        shares=5.0,
        chain_shares=5.0,
        exit_reason="DAY0_ZERO_PROBABILITY [DUST: executable_snapshot_gate: min_order_size 99]",
        last_exit_error="[DUST: below min_order_size]",
    )

    assert exit_lifecycle._is_non_executable_dust_hold(pos, conn=conn) is False


def test_c5_expired_snapshot_does_not_suppress_live_sell_canonical(conn):
    """An expired min-order snapshot must not suppress a live SELL (canonical path).

    _canonical_non_executable_dust_hold is the live-SELL suppression gate in
    _execute_live_exit. An expired snapshot cannot prove present dust, so it must
    release (return None) rather than block the sell. (freshness_deadline is
    schema NOT NULL, so the pre-fix `IS NULL -> fresh` disjunct was dead defence;
    the live freshness hazard is an EXPIRED deadline.)
    """
    from src.engine.lifecycle_events import build_position_current_projection
    from src.execution import exit_lifecycle
    from src.state.projection import upsert_position_current

    pos = _position(trade_id="pos-c5-canon-expired", shares=1.0, chain_shares=1.0)
    projection = build_position_current_projection(pos)
    projection["phase"] = "pending_exit"
    projection["order_status"] = "backoff_exhausted"
    upsert_position_current(conn, projection)

    _ensure_snapshot(
        conn,
        token_id=TOKEN,
        no_token_id=NO_TOKEN,
        selected_outcome_token_id=TOKEN,
        min_order_size="5",
        snapshot_id="snap-c5-canon-expired",
        captured_at=NOW - timedelta(days=2),
        freshness_deadline=NOW - timedelta(days=1),
    )

    result = exit_lifecycle._canonical_non_executable_dust_hold(pos, conn=conn, now=NOW)
    assert result is None
