# Created: 2026-05-17
# Last reused/audited: 2026-07-23
# Lifecycle: created=2026-05-17; last_reviewed=2026-07-23; last_reused=2026-07-23
# Purpose: Protect same-token entry deduplication and certified global increments.
# Reuse: Run when entry dedup, fill materialization, or increment admission changes.
# Authority basis: first-principles global marginal-increment execution repair
#
# Relationship test: when Module A (position_current DB state) shows a non-terminal
# position for token X, Module B (evaluator anti-churn gate) must reject a new
# candidate with the same token_id. The invariant that holds across the boundary:
#   position_current.phase NOT IN terminal_phases → new entry blocked.
#
# DQ-2 branch: PR-S1 SKIPS pending_exit from LIFO walk → test_phantom_not_on_chain
# uses a non-pending_exit phantom fixture (state="phantom_not_on_chain").
# phantom_not_on_chain is not in _TERMINAL_PHASES → DB query returns True regardless
# of whether the rest of the system treats it as a formal lifecycle state.

import json
import math
import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pytest
from src.state.portfolio import (
    has_same_token_open,
    has_same_token_open_db,
    has_inflight_exit_for_token,
    PortfolioState,
    Position,
)
from src.engine.evaluator import _layer7_dedup_fires
from src.execution.executor import (
    _ENTRY_SAME_TOKEN_COOLDOWN_SECONDS,
    _ENTRY_TERMINAL_NO_FILL_REPRICE_COOLDOWN_SECONDS,
    _abort_global_increment_admission,
    _certified_global_increment_authorized,
    _current_global_increment_wealth_component,
    _entry_duplicate_same_token_component,
    _entry_increment_fact_backing_component,
    _entry_same_token_cooldown_component,
)

TOKEN_X = "0xabc123_token_yes"
TOKEN_X_NO = "0xabc123_token_no"
OTHER_TOKEN = "0xother_token_yes"


@pytest.fixture
def mem_db():
    """In-memory sqlite: position_current + venue_trade_facts + venue_commands.
    Schema matches live NOT NULL constraints (direction, local_sequence, intent_kind).
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT NOT NULL,
            trade_id TEXT,
            market_id TEXT,
            city TEXT,
            bin_label TEXT,
            direction TEXT NOT NULL DEFAULT 'buy_yes',
            shares REAL DEFAULT 0,
            chain_shares REAL DEFAULT 0,
            cost_basis_usd REAL DEFAULT 0,
            token_id TEXT,
            no_token_id TEXT,
            order_id TEXT,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE venue_trade_facts (
            trade_fact_id INTEGER PRIMARY KEY,
            trade_id TEXT NOT NULL,
            venue_order_id TEXT NOT NULL,
            command_id TEXT NOT NULL,
            state TEXT NOT NULL,
            filled_size TEXT NOT NULL DEFAULT '0',
            observed_at TEXT NOT NULL,
            local_sequence INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            intent_kind TEXT NOT NULL DEFAULT 'EXIT',
            side TEXT NOT NULL DEFAULT 'BUY',
            size REAL DEFAULT 0,
            price REAL DEFAULT 0,
            venue_order_id TEXT,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT '2026-05-17T22:13:38',
            updated_at TEXT NOT NULL DEFAULT '2026-05-17T22:13:38'
        )
    """)
    conn.execute("""
        CREATE TABLE venue_command_events (
            event_id TEXT PRIMARY KEY,
            command_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            state_after TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE venue_order_facts (
            fact_id INTEGER PRIMARY KEY,
            venue_order_id TEXT NOT NULL,
            command_id TEXT NOT NULL,
            state TEXT NOT NULL,
            remaining_size TEXT,
            matched_size TEXT,
            source TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            local_sequence INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE execution_fact (
            intent_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            command_id TEXT,
            order_role TEXT NOT NULL,
            filled_at TEXT,
            posted_at TEXT,
            fill_price REAL,
            shares REAL,
            terminal_exec_status TEXT,
            venue_status TEXT
        )
    """)
    conn.commit()
    return conn


def _insert_position(
    conn,
    position_id,
    phase,
    token_id,
    direction="buy_yes",
    no_token_id=None,
    *,
    shares=0.0,
    chain_shares=0.0,
    cost_basis_usd=0.0,
):
    conn.execute(
        """INSERT INTO position_current
           (position_id, phase, trade_id, market_id, city, bin_label,
            direction, shares, chain_shares, cost_basis_usd, token_id, no_token_id, order_id, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            position_id, phase, "trade-" + position_id, "mkt-1",
            "London", "18°C", direction, shares, chain_shares, cost_basis_usd, token_id,
            no_token_id or TOKEN_X_NO, "order-" + position_id, "2026-05-17T22:13:38",
        ),
    )
    conn.commit()


def _make_position(**kwargs):
    """Minimal Position fixture with required fields."""
    defaults = dict(
        trade_id="t1", market_id="m1", city="London",
        cluster="EU-West", target_date="2026-05-17",
        bin_label="18°C", direction="buy_yes",
        size_usd=10.0, entry_price=0.40, p_posterior=0.60,
        edge=0.20, entered_at="2026-05-17T20:04:00Z",
    )
    defaults.update(kwargs)
    return Position(**defaults)


# ── Primary: pending_exit blocks re-entry (the London 22:13 → 22:24 race) ──────

def test_pending_exit_blocks_same_token(mem_db):
    """
    GIVEN: position 0a0e3b72-46e with token_id=TOKEN_X in phase pending_exit
           (EXIT_ORDER_REJECTED, retry in progress — London 22:13 scenario)
    WHEN:  has_same_token_open_db(conn, TOKEN_X)
    THEN:  returns True → evaluator rejects new candidate ALREADY_HELD_SAME_TOKEN
    """
    _insert_position(mem_db, "0a0e3b72-46e", "pending_exit", TOKEN_X)
    assert has_same_token_open_db(mem_db, TOKEN_X) is True


def test_pending_exit_does_not_block_different_token(mem_db):
    """Gate is token-specific: pending_exit on TOKEN_X must not block OTHER_TOKEN."""
    _insert_position(mem_db, "0a0e3b72-46e", "pending_exit", TOKEN_X)
    assert has_same_token_open_db(mem_db, OTHER_TOKEN) is False


def test_economically_closed_allows_reentry(mem_db):
    """
    GIVEN: prior position exited cleanly → phase economically_closed
           (London 20:04 scenario — 3a6f0728-c50)
    WHEN:  has_same_token_open_db(conn, TOKEN_X)
    THEN:  returns False → evaluator allows re-entry
    """
    _insert_position(mem_db, "3a6f0728-c50", "economically_closed", TOKEN_X)
    assert has_same_token_open_db(mem_db, TOKEN_X) is False


def test_active_position_blocks_reentry(mem_db):
    """Active position (standard case) must block."""
    _insert_position(mem_db, "active-pos-01", "active", TOKEN_X)
    assert has_same_token_open_db(mem_db, TOKEN_X) is True


def test_voided_position_allows_reentry(mem_db):
    """Voided positions are terminal — must not block."""
    _insert_position(mem_db, "cee5fc85-3dd", "voided", TOKEN_X)
    assert has_same_token_open_db(mem_db, TOKEN_X) is False


def test_terminal_local_phase_with_positive_chain_shares_blocks_reentry(mem_db):
    """Chain-backed exposure remains live even if local lifecycle projection is terminal.

    This protects the Munich/Istanbul class: a local void/quarantine label cannot
    make a chain-held token available to the fresh-entry selector.
    """
    _insert_position(
        mem_db,
        "voided-but-chain-held",
        "voided",
        TOKEN_X,
        chain_shares=12.5,
    )
    assert has_same_token_open_db(mem_db, TOKEN_X) is True


def test_economically_closed_positive_chain_projection_does_not_block_reentry(mem_db):
    _insert_position(
        mem_db,
        "closed-stale-chain-projection",
        "economically_closed",
        TOKEN_X,
        chain_shares=12.5,
    )
    assert has_same_token_open_db(mem_db, TOKEN_X) is False


def test_quarantined_positive_chain_shares_blocks_reentry(mem_db):
    _insert_position(
        mem_db,
        "quarantine-chain-held",
        "quarantined",
        TOKEN_X,
        chain_shares=3.0,
    )
    assert has_same_token_open_db(mem_db, TOKEN_X) is True


# ── buy_no token coverage (PR #143 bot review fix) ───────────────────────────────

def test_buy_no_dedup_blocks_same_no_token(mem_db):
    """
    CATASTROPHIC fix: position_current.token_id stores the YES token.
    For buy_no positions, the relevant token is no_token_id.
    Gate must match on no_token_id to block duplicate NO entries.

    GIVEN: buy_no position with no_token_id=TOKEN_X in pending_exit
    WHEN:  has_same_token_open_db(conn, TOKEN_X)
    THEN:  returns True → gate correctly blocks buy_no duplicate
    """
    # buy_no position: token_id=TOKEN_X_NO (YES side), no_token_id=TOKEN_X (NO side)
    _insert_position(
        mem_db, "buyno-pos-01", "pending_exit",
        token_id=TOKEN_X_NO, direction="buy_no", no_token_id=TOKEN_X,
    )
    assert has_same_token_open_db(mem_db, TOKEN_X) is True, (
        "buy_no position's no_token_id must be matched by the dedup gate"
    )


def test_buy_yes_does_not_block_existing_buy_no_on_different_token(mem_db):
    """
    Specificity: a buy_no position on TOKEN_X (as no_token_id) must NOT block
    a buy_yes candidate for a different token (OTHER_TOKEN).
    """
    _insert_position(
        mem_db, "buyno-pos-02", "pending_exit",
        token_id=TOKEN_X_NO, direction="buy_no", no_token_id=TOKEN_X,
    )
    assert has_same_token_open_db(mem_db, OTHER_TOKEN) is False, (
        "buy_no position on TOKEN_X must not block a candidate for OTHER_TOKEN"
    )


# ── phantom_not_on_chain (Bug #3 state) ─────────────────────────────────────────

@pytest.mark.xfail(
    reason=(
        "Bug #3 will add phantom_not_on_chain to kernel SQL CHECK constraint "
        "(architecture/2026_04_02_architecture_kernel.sql:94). "
        "Production INSERT raises IntegrityError until that ships. "
        "mem_db fixture lacks the CHECK so this passes in-test; "
        "marking xfail documents the forward-compat contract."
    )
)
def test_phantom_not_on_chain_blocks_reentry(mem_db):
    """
    GIVEN: position in phantom_not_on_chain (non-pending_exit — DQ-2 branch per N5)
           PR-S1 SKIPS pending_exit from LIFO walk → fixture uses state="phantom_not_on_chain"
           phantom_not_on_chain is NOT in _TERMINAL_PHASES → query returns True
    WHEN:  has_same_token_open_db(conn, TOKEN_X)
    THEN:  returns True → gate blocks re-entry (opening duplicate on unresolved phantom
           compounds chain-reconciliation confusion)
    NOTE:  xfail until Bug #3 ships — kernel SQL CHECK absent; production INSERT
           would raise IntegrityError before this path is exercisable live.
    """
    _insert_position(mem_db, "phantom-pos-01", "phantom_not_on_chain", TOKEN_X)
    assert has_same_token_open_db(mem_db, TOKEN_X) is True


# ── In-flight exit gate (belt-and-suspenders) ────────────────────────────────────

def test_inflight_matched_exit_blocks_reentry(mem_db):
    """
    GIVEN: position in pending_exit AND venue_trade_facts shows MATCHED exit order
           (exit submitted to chain, not yet CONFIRMED — 5-30s settlement window)
    WHEN:  has_inflight_exit_for_token(conn, TOKEN_X)
    THEN:  returns True → evaluator blocks re-entry during settlement window
    """
    _insert_position(mem_db, "pos-in-flight", "pending_exit", TOKEN_X)
    # venue_commands bridges command_id → token_id (UUID namespace, not short-ID)
    mem_db.execute(
        "INSERT INTO venue_commands (command_id, position_id, token_id, intent_kind, state)"
        " VALUES ('cmd-001', 'pos-in-flight', ?, 'EXIT', 'open')",
        (TOKEN_X,),
    )
    mem_db.execute(
        "INSERT INTO venue_trade_facts"
        " (trade_fact_id, trade_id, venue_order_id, command_id, state, observed_at, local_sequence)"
        " VALUES (1, 'trade-pos-in-flight', 'order-001', 'cmd-001', 'MATCHED', '2026-05-17T22:13:38', 1)"
    )
    mem_db.commit()
    assert has_inflight_exit_for_token(mem_db, TOKEN_X) is True


def test_confirmed_exit_does_not_block_via_inflight_check(mem_db):
    """CONFIRMED exits are terminal — must not trigger the in-flight gate."""
    _insert_position(mem_db, "pos-confirmed", "economically_closed", TOKEN_X)
    mem_db.execute(
        "INSERT INTO venue_commands (command_id, position_id, token_id, intent_kind, state)"
        " VALUES ('cmd-001', 'pos-confirmed', ?, 'EXIT', 'closed')",
        (TOKEN_X,),
    )
    mem_db.execute(
        "INSERT INTO venue_trade_facts"
        " (trade_fact_id, trade_id, venue_order_id, command_id, state, observed_at, local_sequence)"
        " VALUES (1, 'trade-pos-confirmed', 'order-001', 'cmd-001', 'CONFIRMED', '2026-05-17T22:10:00', 1)"
    )
    mem_db.commit()
    assert has_inflight_exit_for_token(mem_db, TOKEN_X) is False


def test_buy_entry_matched_does_not_trigger_inflight_gate(mem_db):
    """
    Fix #2: BUY confirmations (MATCHED/MINED) must NOT trigger the inflight gate.
    Only EXIT-intent commands should block re-entry.

    GIVEN: active position with a BUY command showing MATCHED in venue_trade_facts
           (entry confirmation in-flight — not an exit)
    WHEN:  has_inflight_exit_for_token(conn, TOKEN_X)
    THEN:  returns False — ENTRY intents must not block new entries
    """
    _insert_position(mem_db, "pos-buying", "active", TOKEN_X)
    mem_db.execute(
        "INSERT INTO venue_commands (command_id, position_id, token_id, intent_kind, state)"
        " VALUES ('cmd-buy-01', 'pos-buying', ?, 'ENTRY', 'open')",
        (TOKEN_X,),
    )
    mem_db.execute(
        "INSERT INTO venue_trade_facts"
        " (trade_fact_id, trade_id, venue_order_id, command_id, state, observed_at, local_sequence)"
        " VALUES (1, 'trade-buying', 'order-buy-01', 'cmd-buy-01', 'MATCHED', '2026-05-18T10:00:00', 1)"
    )
    mem_db.commit()
    assert has_inflight_exit_for_token(mem_db, TOKEN_X) is False, (
        "BUY-intent MATCHED fact must not trigger inflight exit gate"
    )


def test_historical_matched_row_superseded_by_confirmed_does_not_block(mem_db):
    """
    Fix #3: venue_trade_facts is append-only. An older MATCHED row for the same
    command_id coexists with a newer CONFIRMED row. The NOT EXISTS subquery must
    suppress the stale MATCHED row so the gate does not fire forever.

    GIVEN: EXIT command with TWO rows — older MATCHED + newer CONFIRMED
    WHEN:  has_inflight_exit_for_token(conn, TOKEN_X)
    THEN:  returns False — CONFIRMED supersedes the historical MATCHED row
    """
    _insert_position(mem_db, "pos-settled", "economically_closed", TOKEN_X)
    mem_db.execute(
        "INSERT INTO venue_commands (command_id, position_id, token_id, intent_kind, state)"
        " VALUES ('cmd-exit-02', 'pos-settled', ?, 'EXIT', 'closed')",
        (TOKEN_X,),
    )
    # Insert MATCHED row first (seq 1), then CONFIRMED row (seq 2)
    mem_db.execute(
        "INSERT INTO venue_trade_facts"
        " (trade_fact_id, trade_id, venue_order_id, command_id, state, observed_at, local_sequence)"
        " VALUES (1, 'trade-settled', 'order-exit-02', 'cmd-exit-02', 'MATCHED', '2026-05-18T09:00:00', 1)"
    )
    mem_db.execute(
        "INSERT INTO venue_trade_facts"
        " (trade_fact_id, trade_id, venue_order_id, command_id, state, observed_at, local_sequence)"
        " VALUES (2, 'trade-settled', 'order-exit-02', 'cmd-exit-02', 'CONFIRMED', '2026-05-18T09:00:30', 2)"
    )
    mem_db.commit()
    assert has_inflight_exit_for_token(mem_db, TOKEN_X) is False, (
        "Historical MATCHED row superseded by CONFIRMED must not re-trigger inflight gate"
    )


# ── OR-branch end-to-end: must fail when `or _inflight_exit` is removed ──────────

def test_evaluator_rejects_when_only_inflight_exit_present(mem_db):
    """
    OR-branch relationship test: _layer7_dedup_fires must return True when
    ONLY has_inflight_exit_for_token returns True (has_same_token_open_db returns
    False — position already promoted to terminal economically_closed).

    Scenario: position promoted to economically_closed (Bug #2 ran), but exit order
    still shows MATCHED in venue_trade_facts during the 5-30s settlement window.
    has_same_token_open_db → False (terminal phase); has_inflight_exit_for_token → True.

    Meta-verify contract: removing `or has_inflight_exit_for_token(conn, token_id)`
    from _layer7_dedup_fires in evaluator.py causes this test to FAIL because
    token_held=False, inflight not checked → returns False → assertion below fails.

    Verified by sed-break: sed 's/or has_inflight_exit_for_token//' evaluator.py
    → this test FAILS. Restore → PASSES.
    """
    # Position promoted to terminal — has_same_token_open_db returns False
    _insert_position(mem_db, "pos-promoted", "economically_closed", TOKEN_X)
    assert has_same_token_open_db(mem_db, TOKEN_X) is False, (
        "Precondition: economically_closed must not block (terminal phase)"
    )

    # Exit order still MATCHED in venue_trade_facts via venue_commands bridge
    mem_db.execute(
        "INSERT INTO venue_commands (command_id, position_id, token_id, intent_kind, state)"
        " VALUES ('cmd-settle', 'pos-promoted', ?, 'EXIT', 'closing')",
        (TOKEN_X,),
    )
    mem_db.execute(
        "INSERT INTO venue_trade_facts"
        " (trade_fact_id, trade_id, venue_order_id, command_id, state, observed_at, local_sequence)"
        " VALUES (1, 'trade-promoted', 'order-settle', 'cmd-settle', 'MATCHED', '2026-05-17T22:24:00', 1)"
    )
    mem_db.commit()

    assert has_inflight_exit_for_token(mem_db, TOKEN_X) is True, (
        "Precondition: inflight exit must be detected via venue_commands join"
    )

    # _layer7_dedup_fires contains `has_same_token_open_db(...) or has_inflight_exit_for_token(...)`.
    # This call is load-bearing: removing the `or ...` branch from that function returns False here.
    assert _layer7_dedup_fires(mem_db, None, TOKEN_X) is True, (
        "OR gate must reject: inflight-only scenario → ALREADY_HELD_SAME_TOKEN. "
        "If this fails, `or has_inflight_exit_for_token` was removed from _layer7_dedup_fires."
    )


def test_executor_duplicate_gate_allows_cancelled_pending_entry_without_fill(mem_db):
    _insert_position(
        mem_db,
        "stale-pending",
        "pending_entry",
        token_id=TOKEN_X_NO,
        direction="buy_no",
        no_token_id=TOKEN_X,
    )
    mem_db.execute(
        """INSERT INTO venue_commands
           (command_id, position_id, token_id, intent_kind, side, venue_order_id,
            state, created_at, updated_at)
           VALUES ('cmd-cancelled', 'stale-pending', ?, 'ENTRY', 'BUY',
                   'order-stale-pending', 'CANCELLED',
                   '2026-06-18T09:15:14', '2026-06-18T09:20:22')""",
        (TOKEN_X,),
    )
    mem_db.execute(
        """INSERT INTO venue_order_facts
           (venue_order_id, command_id, state, remaining_size, matched_size, source,
            observed_at, local_sequence)
           VALUES ('order-stale-pending', 'cmd-cancelled', 'CANCEL_CONFIRMED',
                   '0', '0', 'REST', '2026-06-18T09:20:22', 1)"""
    )
    mem_db.commit()

    result = _entry_duplicate_same_token_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
    )

    assert result["allowed"] is True


def test_executor_duplicate_gate_allows_cancelled_pending_entry_with_stale_live_order_fact(mem_db):
    _insert_position(
        mem_db,
        "stale-pending",
        "pending_entry",
        token_id=TOKEN_X_NO,
        direction="buy_no",
        no_token_id=TOKEN_X,
    )
    mem_db.execute(
        """INSERT INTO venue_commands
           (command_id, position_id, token_id, intent_kind, side, venue_order_id,
            state, created_at, updated_at)
           VALUES ('cmd-cancelled', 'stale-pending', ?, 'ENTRY', 'BUY',
                   'order-stale-pending', 'CANCELLED',
                   '2026-06-18T09:15:14', '2026-06-18T09:20:22')""",
        (TOKEN_X,),
    )
    mem_db.execute(
        """INSERT INTO venue_order_facts
           (venue_order_id, command_id, state, remaining_size, matched_size, source,
            observed_at, local_sequence)
           VALUES ('order-stale-pending', 'cmd-cancelled', 'CANCEL_CONFIRMED',
                   '0', '0', 'REST', '2026-06-18T09:20:22', 1)"""
    )
    mem_db.execute(
        """INSERT INTO venue_order_facts
           (venue_order_id, command_id, state, remaining_size, matched_size, source,
            observed_at, local_sequence)
           VALUES ('order-stale-pending', 'cmd-cancelled', 'LIVE',
                   '10', '0', 'REST', '2026-06-18T09:15:44', 2)"""
    )
    mem_db.commit()

    result = _entry_duplicate_same_token_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
    )

    assert result["allowed"] is True


def test_executor_duplicate_gate_blocks_cancelled_pending_entry_with_fill(mem_db):
    _insert_position(
        mem_db,
        "filled-pending",
        "pending_entry",
        token_id=TOKEN_X_NO,
        direction="buy_no",
        no_token_id=TOKEN_X,
    )
    mem_db.execute(
        """INSERT INTO venue_commands
           (command_id, position_id, token_id, intent_kind, side, venue_order_id,
            state, created_at, updated_at)
           VALUES ('cmd-filled', 'filled-pending', ?, 'ENTRY', 'BUY',
                   'order-filled-pending', 'CANCELLED',
                   '2026-06-18T09:15:14', '2026-06-18T09:20:22')""",
        (TOKEN_X,),
    )
    mem_db.execute(
        """INSERT INTO venue_order_facts
           (venue_order_id, command_id, state, remaining_size, matched_size, source,
            observed_at, local_sequence)
           VALUES ('order-filled-pending', 'cmd-filled', 'CANCEL_CONFIRMED',
                   '4', '1', 'REST', '2026-06-18T09:20:22', 1)"""
    )
    mem_db.execute(
        """INSERT INTO venue_trade_facts
           (trade_fact_id, trade_id, venue_order_id, command_id, state, filled_size,
            observed_at, local_sequence)
           VALUES (2, 'trade-filled-pending', 'order-filled-pending', 'cmd-filled',
                   'MATCHED', '1', '2026-06-18T09:20:22', 1)"""
    )
    mem_db.commit()

    result = _entry_duplicate_same_token_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
    )

    assert result["allowed"] is False
    assert result["reason"] == "open_position_same_token"


def test_executor_duplicate_gate_does_not_let_stale_pending_hide_active_position(mem_db):
    _insert_position(
        mem_db,
        "stale-pending",
        "pending_entry",
        token_id=TOKEN_X_NO,
        direction="buy_no",
        no_token_id=TOKEN_X,
    )
    _insert_position(
        mem_db,
        "active-position",
        "active",
        token_id=TOKEN_X_NO,
        direction="buy_no",
        no_token_id=TOKEN_X,
    )
    mem_db.execute(
        """INSERT INTO venue_commands
           (command_id, position_id, token_id, intent_kind, side, venue_order_id,
            state, created_at, updated_at)
           VALUES ('cmd-cancelled', 'stale-pending', ?, 'ENTRY', 'BUY',
                   'order-stale-pending', 'CANCELLED',
                   '2026-06-18T09:15:14', '2026-06-18T09:20:22')""",
        (TOKEN_X,),
    )
    mem_db.execute(
        """INSERT INTO venue_order_facts
           (venue_order_id, command_id, state, remaining_size, matched_size, source,
            observed_at, local_sequence)
           VALUES ('order-stale-pending', 'cmd-cancelled', 'CANCEL_CONFIRMED',
                   '0', '0', 'REST', '2026-06-18T09:20:22', 1)"""
    )
    mem_db.commit()

    result = _entry_duplicate_same_token_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
    )

    assert result["allowed"] is False
    assert result["existing_position_id"] == "active-position"


def test_executor_certified_global_increment_reuses_reconciled_position_but_not_open_command(
    mem_db,
):
    _insert_position(
        mem_db,
        "active-position",
        "active",
        token_id=TOKEN_X_NO,
        direction="buy_no",
        no_token_id=TOKEN_X,
        shares=24.0,
        cost_basis_usd=16.08,
    )
    mem_db.execute(
        """INSERT INTO venue_commands
           (command_id, position_id, token_id, intent_kind, side, venue_order_id,
            state, created_at, updated_at)
           VALUES ('cmd-filled', 'active-position', ?, 'ENTRY', 'BUY',
                   'order-filled', 'FILLED',
                   '2026-07-14T05:00:00+00:00', '2026-07-14T05:01:00+00:00')""",
        (TOKEN_X,),
    )
    mem_db.execute(
        """INSERT INTO execution_fact
           (intent_id, position_id, command_id, order_role, filled_at, posted_at,
            fill_price, shares, terminal_exec_status, venue_status)
           VALUES ('active-position:entry', 'active-position', 'cmd-filled',
                   'entry', '2026-07-14T05:01:00+00:00',
                   '2026-07-14T05:00:00+00:00', 0.67, 24.0, 'filled', 'FILLED')"""
    )
    mem_db.commit()

    allowed = _entry_duplicate_same_token_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        allow_reconciled_position_increment=True,
    )

    assert allowed["allowed"] is True
    assert allowed["reason"] == "allowed_reconciled_position_increment"
    assert allowed["increment_position_id"] == "active-position"

    mem_db.execute(
        """INSERT INTO venue_commands
           (command_id, position_id, token_id, intent_kind, side, venue_order_id,
            state, created_at, updated_at)
           VALUES ('cmd-filled-unmaterialized', 'active-position', ?, 'ENTRY', 'BUY',
                   'order-filled-unmaterialized', 'FILLED',
                   '2026-07-14T05:01:10+00:00', '2026-07-14T05:01:11+00:00')""",
        (TOKEN_X,),
    )
    mem_db.commit()
    unmaterialized = _entry_duplicate_same_token_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        allow_reconciled_position_increment=True,
    )
    assert unmaterialized["allowed"] is False
    assert unmaterialized["reason"] == "filled_entry_command_not_materialized"
    assert unmaterialized["existing_command_id"] == "cmd-filled-unmaterialized"
    mem_db.execute(
        "DELETE FROM venue_commands WHERE command_id='cmd-filled-unmaterialized'"
    )
    mem_db.commit()

    mem_db.execute(
        "UPDATE position_current SET shares=25.0 WHERE position_id='active-position'"
    )
    drifted = _entry_duplicate_same_token_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        allow_reconciled_position_increment=True,
    )
    assert drifted["allowed"] is False
    assert drifted["reason"] == "position_economics_not_reconciled_for_increment"
    mem_db.execute(
        "UPDATE position_current SET shares=24.0 WHERE position_id='active-position'"
    )

    mem_db.execute(
        "UPDATE position_current SET cost_basis_usd=16.0799 "
        "WHERE position_id='active-position'"
    )
    rounded_cost = _entry_duplicate_same_token_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        allow_reconciled_position_increment=True,
    )
    assert rounded_cost["allowed"] is True
    assert rounded_cost["reason"] == "allowed_reconciled_position_increment"

    mem_db.execute(
        "UPDATE execution_fact SET fill_price=0.6700000000000001 "
        "WHERE position_id='active-position'"
    )
    transport_noisy_cost = _entry_duplicate_same_token_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        allow_reconciled_position_increment=True,
    )
    assert transport_noisy_cost["allowed"] is True
    assert transport_noisy_cost["reason"] == "allowed_reconciled_position_increment"
    mem_db.execute(
        "UPDATE execution_fact SET fill_price=0.67 "
        "WHERE position_id='active-position'"
    )

    mem_db.execute(
        "UPDATE position_current SET cost_basis_usd=15.9905 "
        "WHERE position_id='active-position'"
    )
    chain_summary_cost = _entry_duplicate_same_token_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        allow_reconciled_position_increment=True,
    )
    assert chain_summary_cost["allowed"] is True
    assert chain_summary_cost["reason"] == "allowed_reconciled_position_increment"
    assert chain_summary_cost["increment_position_generation"] == allowed[
        "increment_position_generation"
    ]
    fact_backing = _entry_increment_fact_backing_component(
        mem_db,
        position_id="active-position",
        shares=24.0,
        cost_basis_usd=15.9905,
    )
    assert fact_backing["allowed"] is True
    assert fact_backing["details"] == {
        "position_id": "active-position",
        "shares": "24.0",
        "cost_basis_usd": "16.08",
        "projection_cost_basis_usd": "15.9905",
        "projection_cost_delta_usd": "-0.0895",
        "cost_basis_authority": "command_deduped_terminal_execution_fact",
        "execution_fact_count": 1,
    }
    mem_db.execute(
        "UPDATE position_current SET cost_basis_usd=16.08 "
        "WHERE position_id='active-position'"
    )

    mem_db.execute(
        """INSERT INTO venue_commands
           (command_id, position_id, token_id, intent_kind, side, venue_order_id,
            state, created_at, updated_at)
           VALUES ('cmd-open', 'another-attempt', ?, 'ENTRY', 'BUY',
                   'order-open', 'ACKED',
                   '2026-07-14T05:02:00+00:00', '2026-07-14T05:02:01+00:00')""",
        (TOKEN_X,),
    )
    mem_db.commit()

    blocked = _entry_duplicate_same_token_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        allow_reconciled_position_increment=True,
    )

    assert blocked["allowed"] is False
    assert blocked["reason"] == "open_or_filled_entry_command_same_token"
    assert blocked["existing_command_id"] == "cmd-open"

    mem_db.execute("DELETE FROM venue_commands WHERE command_id = 'cmd-open'")
    mem_db.execute(
        """INSERT INTO venue_commands
           (command_id, position_id, token_id, intent_kind, side, venue_order_id,
            state, created_at, updated_at)
           VALUES ('cmd-orphan-fill', 'missing-position', ?, 'ENTRY', 'BUY',
                   'order-orphan-fill', 'FILLED',
                   '2026-07-14T05:03:00+00:00', '2026-07-14T05:03:01+00:00')""",
        (TOKEN_X,),
    )
    mem_db.commit()

    orphan_fill = _entry_duplicate_same_token_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        allow_reconciled_position_increment=True,
    )

    assert orphan_fill["allowed"] is False
    assert orphan_fill["existing_command_id"] == "cmd-orphan-fill"


def test_certified_increment_uses_fills_when_projection_cost_differs(mem_db):
    fills = (
        ("cmd-1", 16.2, 0.75),
        ("cmd-2", 21.0, 0.77),
        ("cmd-3", 35.306664, 0.7443353470041803),
        ("cmd-4", 34.0, 0.74),
    )
    _insert_position(
        mem_db,
        "active-position",
        "active",
        token_id=TOKEN_X_NO,
        direction="buy_no",
        no_token_id=TOKEN_X,
        shares=106.506664,
        cost_basis_usd=79.7598,
    )
    for index, (command_id, shares, price) in enumerate(fills, start=1):
        mem_db.execute(
            """INSERT INTO venue_commands
               (command_id, position_id, token_id, intent_kind, side,
                venue_order_id, state, created_at, updated_at)
               VALUES (?, 'active-position', ?, 'ENTRY', 'BUY', ?, 'FILLED', ?, ?)""",
            (
                command_id,
                TOKEN_X,
                f"order-{index}",
                f"2026-07-14T05:0{index}:00+00:00",
                f"2026-07-14T05:0{index}:01+00:00",
            ),
        )
        mem_db.execute(
            """INSERT INTO execution_fact
               (intent_id, position_id, command_id, order_role, filled_at,
                posted_at, fill_price, shares, terminal_exec_status, venue_status)
               VALUES (?, 'active-position', ?, 'entry', ?, ?, ?, ?, 'filled', 'FILLED')""",
            (
                f"active-position:entry:{command_id}",
                command_id,
                f"2026-07-14T05:0{index}:01+00:00",
                f"2026-07-14T05:0{index}:00+00:00",
                price,
                shares,
            ),
        )
    mem_db.commit()

    allowed = _entry_duplicate_same_token_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        allow_reconciled_position_increment=True,
    )

    assert allowed["allowed"] is True
    assert allowed["reason"] == "allowed_reconciled_position_increment"

    mem_db.execute(
        "UPDATE position_current SET cost_basis_usd=79.7594 "
        "WHERE position_id='active-position'"
    )
    blocked = _entry_duplicate_same_token_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        allow_reconciled_position_increment=True,
    )

    assert blocked["allowed"] is True
    assert blocked["reason"] == "allowed_reconciled_position_increment"


def test_certified_global_increment_requires_materialized_existing_economics(mem_db):
    _insert_position(
        mem_db,
        "zero-position",
        "active",
        token_id=TOKEN_X_NO,
        direction="buy_no",
        no_token_id=TOKEN_X,
    )

    blocked = _entry_duplicate_same_token_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        allow_reconciled_position_increment=True,
    )

    assert blocked["allowed"] is False
    assert blocked["reason"] == "open_position_same_token"
    assert blocked["existing_position_id"] == "zero-position"


def test_global_increment_authority_requires_atomic_wealth_bound():
    economics = {
        "global_optimum_semantics": "CUT_TIME_GLOBAL_OPTIMUM",
        "global_actuation_identity": "actuation",
        "global_economic_identity": "economics",
        "global_universe_witness_identity": "universe",
        "global_wealth_witness_identity": "wealth",
        "global_wealth_economic_identity": "wealth-economics",
        "global_selection_epoch_identity": "epoch",
        "global_candidate_id": "candidate",
        "global_target_shares": "19.25",
        "global_max_spend_usd": "12.54",
    }
    component = {
        "allowed": True,
        "details": {"global_limit_bound_authorized": True},
    }

    assert _certified_global_increment_authorized(
        {"qkernel_execution_economics": economics},
        component,
        order_type="FOK",
    )
    fak_economics = {
        **economics,
        "side": "NO",
        "global_target_shares": "5",
        "global_limit_price": "0.4",
        "global_terminal_win_probability_lcb": "0.8",
        "global_terminal_loss_probability_ucb": "0.2",
        "global_terminal_loss_payoff_usd": "0",
        "global_terminal_win_payoff_usd": "0",
        "global_terminal_wealth_after_loss_usd": "100",
        "global_terminal_wealth_after_win_usd": "100",
        "global_jit_execution_curve_identity": "curve",
        "global_buy_fak_prefix_semantics": (
            "CONCAVE_WORST_LIMIT_ALL_NONZERO_PREFIXES_POSITIVE"
        ),
        "global_buy_fak_fee_rate_source": "CURRENT_EXECUTABLE_CURVE",
        "global_buy_fak_execution_curve_identity": "curve",
        "global_buy_fak_fee_rate": "0",
        "global_buy_fak_fee_rounding_bound": (
            "ROUNDED_FEE_AT_MOST_TWO_X_UNROUNDED"
        ),
        "global_buy_fak_worst_fee_shape": "0.24",
        "global_buy_fak_worst_fee_per_share": "0",
        "global_buy_fak_worst_unit_cost": "0.4",
        "global_buy_fak_full_worst_cost_usd": "2",
        "global_buy_fak_full_robust_delta_log_wealth": (
            0.2 * math.log(0.98) + 0.8 * math.log(1.03)
        ),
        "global_buy_fak_full_robust_ev_usd": "2",
    }
    assert _certified_global_increment_authorized(
        {"direction": "buy_no", "qkernel_execution_economics": fak_economics},
        component,
        order_type="FAK",
    )
    assert not _certified_global_increment_authorized(
        {
            "direction": "buy_no",
            "qkernel_execution_economics": {
                **fak_economics,
                "global_buy_fak_full_robust_ev_usd": "-1",
            }
        },
        component,
        order_type="FAK",
    )
    assert not _certified_global_increment_authorized(
        {"direction": "buy_yes", "qkernel_execution_economics": fak_economics},
        component,
        order_type="FAK",
    )
    assert _certified_global_increment_authorized(
        {"qkernel_execution_economics": economics},
        component,
        order_type="GTC",
        post_only=True,
    )
    assert not _certified_global_increment_authorized(
        {"qkernel_execution_economics": economics},
        component,
        order_type="GTC",
        post_only=False,
    )
    assert not _certified_global_increment_authorized(
        {"qkernel_execution_economics": {**economics, "global_wealth_witness_identity": ""}},
        component,
        order_type="FOK",
    )


def test_global_increment_locked_wealth_recheck_matches_exact_economic_identity(
    mem_db,
    monkeypatch,
):
    import src.engine.global_auction_universe as universe

    monkeypatch.setattr(
        universe,
        "current_portfolio_wealth_witness",
        lambda *_args, **_kwargs: SimpleNamespace(economic_identity="wealth-economics"),
    )
    matched = _current_global_increment_wealth_component(
        mem_db,
        {"global_wealth_economic_identity": "wealth-economics"},
    )
    superseded = _current_global_increment_wealth_component(
        mem_db,
        {"global_wealth_economic_identity": "older-wealth"},
    )

    assert matched["allowed"] is True
    assert superseded["allowed"] is False
    assert superseded["reason"] == "wealth_economic_identity_superseded"


def test_global_increment_binding_rejection_ends_writer_transaction(tmp_path):
    db_path = tmp_path / "increment-binding.db"
    first = sqlite3.connect(db_path, timeout=0.1)
    second = sqlite3.connect(db_path, timeout=0.1)
    try:
        first.execute("CREATE TABLE admission_fact (value TEXT)")
        first.commit()
        first.execute("INSERT INTO admission_fact VALUES ('envelope')")
        assert first.in_transaction is True

        _abort_global_increment_admission(first)

        assert first.in_transaction is False
        assert first.execute("SELECT COUNT(*) FROM admission_fact").fetchone()[0] == 0
        second.execute("INSERT INTO admission_fact VALUES ('second-writer')")
        second.commit()
    finally:
        first.close()
        second.close()


def test_terminal_no_fill_no_exposure_still_obeys_same_token_cooldown(mem_db):
    _insert_position(
        mem_db,
        "stale-pending",
        "pending_entry",
        token_id=TOKEN_X_NO,
        direction="buy_no",
        no_token_id=TOKEN_X,
    )
    mem_db.execute(
        """INSERT INTO venue_commands
           (command_id, position_id, token_id, intent_kind, side, size, price, venue_order_id,
            state, created_at, updated_at)
           VALUES ('cmd-cancelled', 'stale-pending', ?, 'ENTRY', 'BUY',
                   12.7, 0.73, 'order-stale-pending', 'CANCELLED',
                   '2026-06-18T09:15:14+00:00', '2026-06-18T09:59:00+00:00')""",
        (TOKEN_X,),
    )
    mem_db.execute(
        """INSERT INTO venue_order_facts
           (venue_order_id, command_id, state, remaining_size, matched_size, source,
            observed_at, local_sequence)
           VALUES ('order-stale-pending', 'cmd-cancelled', 'CANCEL_CONFIRMED',
                   '12.7', '0', 'WS_USER', '2026-06-18T09:59:00+00:00', 1)"""
    )
    mem_db.commit()

    result = _entry_same_token_cooldown_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        limit_price=0.73,
        shares=12.7,
        now=datetime.fromisoformat("2026-06-18T10:00:00+00:00"),
    )

    assert result["allowed"] is False
    assert result["reason"] == "same_token_terminal_no_fill_cooling_down"
    assert (
        result["remaining_seconds"]
        == _ENTRY_TERMINAL_NO_FILL_REPRICE_COOLDOWN_SECONDS - 60
    )
    assert result["existing_command_id"] == "cmd-cancelled"
    assert result["candidate_price"] == "0.73"
    assert result["candidate_shares"] == "12.7"


def test_terminal_no_fill_rest_pull_reprice_bypasses_same_token_cooldown(mem_db):
    _insert_position(
        mem_db,
        "stale-pending",
        "pending_entry",
        token_id=TOKEN_X_NO,
        direction="buy_no",
        no_token_id=TOKEN_X,
    )
    mem_db.execute(
        """INSERT INTO venue_commands
           (command_id, position_id, token_id, intent_kind, side, size, price,
            venue_order_id, state, created_at, updated_at)
           VALUES ('cmd-cancelled', 'stale-pending', ?, 'ENTRY', 'BUY',
                   12.7, 0.73, 'order-stale-pending', 'CANCELLED',
                   '2026-06-18T09:15:14+00:00', '2026-06-18T09:59:00+00:00')""",
        (TOKEN_X,),
    )
    mem_db.execute(
        """INSERT INTO venue_order_facts
           (venue_order_id, command_id, state, remaining_size, matched_size, source,
            observed_at, local_sequence)
           VALUES ('order-stale-pending', 'cmd-cancelled', 'CANCEL_CONFIRMED',
                   '12.7', '0', 'WS_USER', '2026-06-18T09:59:00+00:00', 1)"""
    )
    mem_db.execute(
        """INSERT INTO venue_command_events
           (event_id, command_id, sequence_no, event_type, occurred_at,
            payload_json, state_after)
           VALUES ('evt-cancel-acked', 'cmd-cancelled', 2, 'CANCEL_ACKED',
                   '2026-06-18T09:59:00+00:00',
                   '{"cancel_reason":"CONFIRMED_VALUE_REFRESH"}',
                   'CANCELLED')"""
    )
    mem_db.commit()

    result = _entry_same_token_cooldown_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        limit_price=0.74,
        shares=12.7,
        now=datetime.fromisoformat("2026-06-18T10:00:00+00:00"),
    )

    assert result["allowed"] is True
    assert result["reason"] == "allowed_terminal_no_fill_rest_pull_reprice"
    assert result["existing_command_id"] == "cmd-cancelled"
    assert result["rest_pull_cancel_reason"] == "CONFIRMED_VALUE_REFRESH"
    assert result["reprice_delta"] == "0.01"


def test_terminal_no_fill_rest_pull_still_requires_actual_reprice(mem_db):
    _insert_position(
        mem_db,
        "stale-pending",
        "pending_entry",
        token_id=TOKEN_X_NO,
        direction="buy_no",
        no_token_id=TOKEN_X,
    )
    mem_db.execute(
        """INSERT INTO venue_commands
           (command_id, position_id, token_id, intent_kind, side, size, price,
            venue_order_id, state, created_at, updated_at)
           VALUES ('cmd-cancelled', 'stale-pending', ?, 'ENTRY', 'BUY',
                   12.7, 0.73, 'order-stale-pending', 'CANCELLED',
                   '2026-06-18T09:15:14+00:00', '2026-06-18T09:59:00+00:00')""",
        (TOKEN_X,),
    )
    mem_db.execute(
        """INSERT INTO venue_order_facts
           (venue_order_id, command_id, state, remaining_size, matched_size, source,
            observed_at, local_sequence)
           VALUES ('order-stale-pending', 'cmd-cancelled', 'CANCEL_CONFIRMED',
                   '12.7', '0', 'WS_USER', '2026-06-18T09:59:00+00:00', 1)"""
    )
    mem_db.execute(
        """INSERT INTO venue_command_events
           (event_id, command_id, sequence_no, event_type, occurred_at,
            payload_json, state_after)
           VALUES ('evt-cancel-acked', 'cmd-cancelled', 2, 'CANCEL_ACKED',
                   '2026-06-18T09:59:00+00:00',
                   '{"cancel_reason":"CONFIRMED_VALUE_REFRESH"}',
                   'CANCELLED')"""
    )
    mem_db.commit()

    result = _entry_same_token_cooldown_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        limit_price=0.73,
        shares=12.7,
        now=datetime.fromisoformat("2026-06-18T10:00:00+00:00"),
    )

    assert result["allowed"] is False
    assert result["reason"] == "same_token_terminal_no_fill_requires_reprice"
    assert result["existing_command_id"] == "cmd-cancelled"
    assert result["rest_pull_cancel_reason"] == "CONFIRMED_VALUE_REFRESH"
    assert result["reprice_delta"] == "0.00"


def test_terminal_no_fill_redecision_allowed_after_same_token_cooldown(mem_db):
    _insert_position(
        mem_db,
        "stale-pending",
        "pending_entry",
        token_id=TOKEN_X_NO,
        direction="buy_no",
        no_token_id=TOKEN_X,
    )
    mem_db.execute(
        """INSERT INTO venue_commands
           (command_id, position_id, token_id, intent_kind, side, size, price,
            venue_order_id, state, created_at, updated_at)
           VALUES ('cmd-cancelled', 'stale-pending', ?, 'ENTRY', 'BUY',
                   12.7, 0.73, 'order-stale-pending', 'CANCELLED',
                   '2026-06-18T09:15:14+00:00', '2026-06-18T09:59:00+00:00')""",
        (TOKEN_X,),
    )
    mem_db.execute(
        """INSERT INTO venue_order_facts
           (venue_order_id, command_id, state, remaining_size, matched_size, source,
            observed_at, local_sequence)
           VALUES ('order-stale-pending', 'cmd-cancelled', 'CANCEL_CONFIRMED',
                   '12.7', '0', 'WS_USER', '2026-06-18T09:59:00+00:00', 1)"""
    )
    mem_db.commit()

    result = _entry_same_token_cooldown_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        limit_price=0.74,
        shares=12.7,
        now=datetime.fromisoformat("2026-06-18T10:02:01+00:00"),
    )

    assert result["allowed"] is True
    assert result["reason"] == "allowed_terminal_no_fill_no_exposure_cooldown_elapsed"
    assert result["cooldown_seconds"] == _ENTRY_TERMINAL_NO_FILL_REPRICE_COOLDOWN_SECONDS
    assert result["existing_command_id"] == "cmd-cancelled"


def test_terminal_no_fill_redecision_after_cooldown_requires_actual_reprice(mem_db):
    _insert_position(
        mem_db,
        "stale-pending",
        "pending_entry",
        token_id=TOKEN_X_NO,
        direction="buy_no",
        no_token_id=TOKEN_X,
    )
    mem_db.execute(
        """INSERT INTO venue_commands
           (command_id, position_id, token_id, intent_kind, side, size, price,
            venue_order_id, state, created_at, updated_at)
           VALUES ('cmd-cancelled', 'stale-pending', ?, 'ENTRY', 'BUY',
                   12.7, 0.73, 'order-stale-pending', 'CANCELLED',
                   '2026-06-18T09:15:14+00:00', '2026-06-18T09:59:00+00:00')""",
        (TOKEN_X,),
    )
    mem_db.execute(
        """INSERT INTO venue_order_facts
           (venue_order_id, command_id, state, remaining_size, matched_size, source,
            observed_at, local_sequence)
           VALUES ('order-stale-pending', 'cmd-cancelled', 'CANCEL_CONFIRMED',
                   '12.7', '0', 'WS_USER', '2026-06-18T09:59:00+00:00', 1)"""
    )
    mem_db.commit()

    result = _entry_same_token_cooldown_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        limit_price=0.73,
        shares=12.7,
        now=datetime.fromisoformat("2026-06-18T10:02:01+00:00"),
    )

    assert result["allowed"] is False
    assert result["reason"] == "same_token_terminal_no_fill_requires_reprice"
    assert result["existing_command_id"] == "cmd-cancelled"
    assert result["existing_price"] == "0.73"
    assert result["candidate_price"] == "0.73"


@pytest.mark.parametrize("token_id", [TOKEN_X, TOKEN_X_NO])
def test_terminal_fok_no_fill_redecision_allows_same_price_after_cooldown(
    mem_db,
    token_id,
):
    mem_db.execute(
        """INSERT INTO venue_commands
           (command_id, position_id, token_id, intent_kind, side, size, price,
            venue_order_id, state, created_at, updated_at)
           VALUES ('cmd-fok-killed', 'prior-candidate', ?, 'ENTRY', 'BUY',
                   1018, 0.005, NULL, 'REJECTED',
                   '2026-07-13T15:20:29+00:00', '2026-07-13T15:20:32+00:00')""",
        (token_id,),
    )
    mem_db.execute(
        """INSERT INTO venue_command_events
           (event_id, command_id, sequence_no, event_type, occurred_at,
            payload_json, state_after)
           VALUES ('evt-fok-killed', 'cmd-fok-killed', 3, 'SUBMIT_REJECTED',
                   '2026-07-13T15:20:32+00:00', ?, 'REJECTED')""",
        (
            '{"proof_class":"deterministic_venue_400",'
            '"venue_order_created":false,'
            '"exception_message":"order couldn\'t be fully filled. '
            'FOK orders are fully filled or killed."}',
        ),
    )
    mem_db.commit()

    cooling = _entry_same_token_cooldown_component(
        mem_db,
        token_id=token_id,
        candidate_position_id="fresh-candidate",
        limit_price=0.005,
        shares=1016,
        now=datetime.fromisoformat("2026-07-13T15:21:32+00:00"),
    )
    ready = _entry_same_token_cooldown_component(
        mem_db,
        token_id=token_id,
        candidate_position_id="fresh-candidate",
        limit_price=0.005,
        shares=1016,
        now=datetime.fromisoformat("2026-07-13T15:22:33+00:00"),
    )

    assert cooling["allowed"] is False
    assert cooling["reason"] == "same_token_terminal_no_fill_cooling_down"
    assert ready["allowed"] is True
    assert ready["reason"] == "allowed_terminal_fok_no_fill_redecision"
    assert ready["existing_price"] == "0.005"
    assert ready["candidate_price"] == "0.005"
    assert ready["existing_size"] == "1018.0"
    assert ready["candidate_shares"] == "1016"


def test_pre_submit_db_lock_redecision_bypasses_terminal_no_fill_cooldown(mem_db):
    mem_db.execute(
        """INSERT INTO venue_commands
           (command_id, position_id, token_id, intent_kind, side, size, price,
            venue_order_id, state, created_at, updated_at)
           VALUES ('cmd-pre-submit-lock', 'prior-candidate', ?, 'ENTRY', 'BUY',
                   253, 0.10, NULL, 'REJECTED',
                   '2026-07-23T08:07:47+00:00', '2026-07-23T08:07:50+00:00')""",
        (TOKEN_X,),
    )
    mem_db.execute(
        """INSERT INTO venue_command_events
           (event_id, command_id, sequence_no, event_type, occurred_at,
            payload_json, state_after)
           VALUES ('evt-pre-submit-lock', 'cmd-pre-submit-lock', 3,
                   'SUBMIT_REJECTED', '2026-07-23T08:07:50+00:00', ?,
                   'REJECTED')""",
        (
            json.dumps(
                {
                    "reason": "V2_PRE_SUBMIT_EXCEPTION",
                    "detail": "database is locked",
                    "final_submission_envelope_stage": "post_submit_result",
                }
            ),
        ),
    )
    mem_db.commit()

    ready = _entry_same_token_cooldown_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        limit_price=0.10,
        shares=254,
        now=datetime.fromisoformat("2026-07-23T08:07:51+00:00"),
    )

    assert ready["allowed"] is True
    assert ready["reason"] == (
        "allowed_terminal_pre_submit_db_lock_no_fill_redecision"
    )
    assert ready["terminal_no_fill_redecision_proof"] == "pre_submit_db_lock"
    assert ready["cooldown_seconds"] == 0
    assert ready["existing_price"] == "0.1"
    assert ready["candidate_price"] == "0.1"


def test_terminal_fak_no_match_redecision_allows_same_price_after_cooldown(mem_db):
    venue_order_id = "0x" + "8e" * 32
    mem_db.execute(
        """INSERT INTO venue_commands
           (command_id, position_id, token_id, intent_kind, side, size, price,
            venue_order_id, state, created_at, updated_at)
           VALUES ('cmd-fak-no-match', 'prior-candidate', ?, 'ENTRY', 'BUY',
                   6, 0.73, ?, 'SUBMIT_REJECTED',
                   '2026-07-17T03:16:43+00:00', '2026-07-17T03:24:38+00:00')""",
        (TOKEN_X, venue_order_id),
    )
    mem_db.execute(
        """INSERT INTO venue_command_events
           (event_id, command_id, sequence_no, event_type, occurred_at,
            payload_json, state_after)
           VALUES ('evt-fak-no-match', 'cmd-fak-no-match', 4, 'SUBMIT_REJECTED',
                   '2026-07-17T03:24:38+00:00', ?, 'SUBMIT_REJECTED')""",
        (
            json.dumps(
                {
                    "reason": "venue_rejected_fak_no_match_400",
                    "venue_order_id": venue_order_id,
                    "proof_class": "deterministic_venue_fak_no_match_400",
                    "terminal_no_fill": True,
                    "exposure_created": False,
                    "required_predicates": {
                        "exception_message_fak_no_match_400": True,
                        "final_envelope_command_matches": True,
                        "final_envelope_is_fak": True,
                        "deterministic_order_id_matches": True,
                        "no_order_facts": True,
                        "no_trade_facts": True,
                    },
                }
            ),
        ),
    )
    mem_db.commit()

    cooling = _entry_same_token_cooldown_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        limit_price=0.73,
        shares=10,
        now=datetime.fromisoformat("2026-07-17T03:25:38+00:00"),
    )
    ready = _entry_same_token_cooldown_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        limit_price=0.73,
        shares=10,
        now=datetime.fromisoformat("2026-07-17T03:26:39+00:00"),
    )

    assert cooling["allowed"] is False
    assert cooling["reason"] == "same_token_terminal_no_fill_cooling_down"
    assert ready["allowed"] is True
    assert ready["reason"] == "allowed_terminal_fak_no_fill_redecision"
    assert ready["terminal_no_fill_redecision_proof"] == "fak"


def test_terminal_fak_no_match_redecision_rejects_exposure_claim(mem_db):
    venue_order_id = "0x" + "9a" * 32
    mem_db.execute(
        """INSERT INTO venue_commands
           (command_id, position_id, token_id, intent_kind, side, size, price,
            venue_order_id, state, created_at, updated_at)
           VALUES ('cmd-fak-exposure', 'prior-candidate', ?, 'ENTRY', 'BUY',
                   6, 0.73, ?, 'SUBMIT_REJECTED',
                   '2026-07-17T03:16:43+00:00', '2026-07-17T03:24:38+00:00')""",
        (TOKEN_X, venue_order_id),
    )
    mem_db.execute(
        """INSERT INTO venue_command_events
           (event_id, command_id, sequence_no, event_type, occurred_at,
            payload_json, state_after)
           VALUES ('evt-fak-exposure', 'cmd-fak-exposure', 4, 'SUBMIT_REJECTED',
                   '2026-07-17T03:24:38+00:00', ?, 'SUBMIT_REJECTED')""",
        (
            json.dumps(
                {
                    "reason": "venue_rejected_fak_no_match_400",
                    "venue_order_id": venue_order_id,
                    "proof_class": "deterministic_venue_fak_no_match_400",
                    "terminal_no_fill": True,
                    "exposure_created": True,
                    "required_predicates": {
                        "exception_message_fak_no_match_400": True,
                        "final_envelope_command_matches": True,
                        "final_envelope_is_fak": True,
                        "deterministic_order_id_matches": True,
                        "no_order_facts": True,
                        "no_trade_facts": True,
                    },
                }
            ),
        ),
    )
    mem_db.commit()

    result = _entry_same_token_cooldown_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        limit_price=0.73,
        shares=10,
        now=datetime.fromisoformat("2026-07-17T03:26:39+00:00"),
    )

    assert result["allowed"] is False
    assert result["reason"] == "same_token_terminal_no_fill_requires_reprice"


def test_other_deterministic_rejection_still_requires_reprice(mem_db):
    mem_db.execute(
        """INSERT INTO venue_commands
           (command_id, position_id, token_id, intent_kind, side, size, price,
            venue_order_id, state, created_at, updated_at)
           VALUES ('cmd-invalid', 'prior-candidate', ?, 'ENTRY', 'BUY',
                   12.7, 0.73, NULL, 'REJECTED',
                   '2026-06-18T09:15:14+00:00', '2026-06-18T09:59:00+00:00')""",
        (TOKEN_X,),
    )
    mem_db.execute(
        """INSERT INTO venue_command_events
           (event_id, command_id, sequence_no, event_type, occurred_at,
            payload_json, state_after)
           VALUES ('evt-invalid', 'cmd-invalid', 3, 'SUBMIT_REJECTED',
                   '2026-06-18T09:59:00+00:00', ?, 'REJECTED')""",
        (
            '{"proof_class":"deterministic_venue_invalid_amount_400",'
            '"venue_order_created":false,'
            '"exception_message":"invalid amounts"}',
        ),
    )
    mem_db.commit()

    result = _entry_same_token_cooldown_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        limit_price=0.73,
        shares=12.7,
        now=datetime.fromisoformat("2026-06-18T10:02:01+00:00"),
    )

    assert result["allowed"] is False
    assert result["reason"] == "same_token_terminal_no_fill_requires_reprice"


def test_fok_rejection_with_venue_order_id_still_requires_reprice(mem_db):
    mem_db.execute(
        """INSERT INTO venue_commands
           (command_id, position_id, token_id, intent_kind, side, size, price,
            venue_order_id, state, created_at, updated_at)
           VALUES ('cmd-fok-order-id', 'prior-candidate', ?, 'ENTRY', 'BUY',
                   12.7, 0.73, 'unexpected-order-id', 'REJECTED',
                   '2026-06-18T09:15:14+00:00', '2026-06-18T09:59:00+00:00')""",
        (TOKEN_X,),
    )
    mem_db.execute(
        """INSERT INTO venue_command_events
           (event_id, command_id, sequence_no, event_type, occurred_at,
            payload_json, state_after)
           VALUES ('evt-fok-order-id', 'cmd-fok-order-id', 3, 'SUBMIT_REJECTED',
                   '2026-06-18T09:59:00+00:00', ?, 'REJECTED')""",
        (
            '{"proof_class":"deterministic_venue_400",'
            '"venue_order_created":false,'
            '"exception_message":"order couldn\'t be fully filled. '
            'FOK orders are fully filled or killed."}',
        ),
    )
    mem_db.commit()

    result = _entry_same_token_cooldown_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        limit_price=0.73,
        shares=12.7,
        now=datetime.fromisoformat("2026-06-18T10:02:01+00:00"),
    )

    assert result["allowed"] is False
    assert result["reason"] == "same_token_terminal_no_fill_requires_reprice"


def test_cancelled_entry_without_zero_fill_fact_still_blocks_redecision(mem_db):
    mem_db.execute(
        """INSERT INTO venue_commands
           (command_id, position_id, token_id, intent_kind, side, size, price,
            venue_order_id, state, created_at, updated_at)
           VALUES ('cmd-cancelled', 'stale-pending', ?, 'ENTRY', 'BUY',
                   12.7, 0.73, 'order-stale-pending', 'CANCELLED',
                   '2026-06-18T09:15:14+00:00', '2026-06-18T09:59:30+00:00')""",
        (TOKEN_X,),
    )
    mem_db.commit()

    result = _entry_same_token_cooldown_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        limit_price=0.73,
        shares=12.7,
        now=datetime.fromisoformat("2026-06-18T10:00:00+00:00"),
    )

    assert result["allowed"] is False
    assert result["reason"] == "same_token_entry_cooling_down"
    assert result["remaining_seconds"] == _ENTRY_SAME_TOKEN_COOLDOWN_SECONDS - 30


def test_executor_cooldown_still_blocks_when_active_command_exists(mem_db):
    mem_db.execute(
        """INSERT INTO venue_commands
           (command_id, position_id, token_id, intent_kind, side, venue_order_id,
            state, created_at, updated_at)
           VALUES ('cmd-cancelled', 'stale-pending', ?, 'ENTRY', 'BUY',
                   'order-stale-pending', 'CANCELLED',
                   '2026-06-18T09:15:14+00:00', '2026-06-18T09:59:00+00:00')""",
        (TOKEN_X,),
    )
    mem_db.execute(
        """INSERT INTO venue_order_facts
           (venue_order_id, command_id, state, remaining_size, matched_size, source,
            observed_at, local_sequence)
           VALUES ('order-stale-pending', 'cmd-cancelled', 'CANCEL_CONFIRMED',
                   '12.7', '0', 'WS_USER', '2026-06-18T09:59:00+00:00', 1)"""
    )
    mem_db.execute(
        """INSERT INTO venue_commands
           (command_id, position_id, token_id, intent_kind, side, venue_order_id,
            state, created_at, updated_at)
           VALUES ('cmd-acked', 'active-entry', ?, 'ENTRY', 'BUY',
                   'order-active-entry', 'ACKED',
                   '2026-06-18T09:58:30+00:00', '2026-06-18T09:58:30+00:00')""",
        (TOKEN_X,),
    )
    mem_db.commit()

    result = _entry_same_token_cooldown_component(
        mem_db,
        token_id=TOKEN_X,
        candidate_position_id="fresh-candidate",
        now=datetime.fromisoformat("2026-06-18T10:00:00+00:00"),
    )

    assert result["allowed"] is False
    assert result["reason"] == "same_token_entry_cooling_down"
    assert result["remaining_seconds"] == _ENTRY_SAME_TOKEN_COOLDOWN_SECONDS - 90

# ── Snapshot fallback (anti-rot for the dual-path) ───────────────────────────────

def test_snapshot_fallback_when_conn_none():
    """
    GIVEN: conn is None (paper mode / test fixture without DB)
    WHEN:  has_same_token_open(portfolio_snapshot, TOKEN_X) called directly
    THEN:  returns True for a portfolio containing TOKEN_X in pending_exit,
           False for a portfolio with TOKEN_X in economically_closed.
    """
    pos_open = _make_position(
        trade_id="snap-open", token_id=TOKEN_X, state="pending_exit",
    )
    # PortfolioState.state field vs Position.state: Position uses lifecycle state
    # stored as string on the dataclass. Build PortfolioState with one open position.
    portfolio_open = PortfolioState(
        bankroll=100.0,
        daily_baseline_total=100.0,
        weekly_baseline_total=100.0,
        positions=[pos_open],
    )
    assert has_same_token_open(portfolio_open, TOKEN_X) is True

    pos_closed = _make_position(
        trade_id="snap-closed", token_id=TOKEN_X, state="economically_closed",
    )
    portfolio_closed = PortfolioState(
        bankroll=100.0,
        daily_baseline_total=100.0,
        weekly_baseline_total=100.0,
        positions=[pos_closed],
    )
    assert has_same_token_open(portfolio_closed, TOKEN_X) is False
