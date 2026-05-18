# Created: 2026-05-17
# Last reused/audited: 2026-05-17
# Authority basis: STRUCTURAL_PLAN.md v3 §2 PR-S3 + B_patch_plan.md
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

import sqlite3
import pytest
from src.state.portfolio import (
    has_same_token_open,
    has_same_token_open_db,
    has_inflight_exit_for_token,
    PortfolioState,
    Position,
)
from src.engine.evaluator import _layer7_dedup_fires

TOKEN_X = "0xabc123_token_yes"
TOKEN_X_NO = "0xabc123_token_no"
OTHER_TOKEN = "0xother_token_yes"


@pytest.fixture
def mem_db():
    """In-memory sqlite: position_current + venue_trade_facts + venue_commands.
    Schema matches live NOT NULL constraints (direction, local_sequence, intent_kind).
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT NOT NULL,
            trade_id TEXT,
            market_id TEXT,
            city TEXT,
            bin_label TEXT,
            direction TEXT NOT NULL DEFAULT 'buy_yes',
            token_id TEXT,
            no_token_id TEXT,
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
            state TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def _insert_position(conn, position_id, phase, token_id, direction="buy_yes", no_token_id=None):
    conn.execute(
        """INSERT INTO position_current
           (position_id, phase, trade_id, market_id, city, bin_label,
            direction, token_id, no_token_id, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            position_id, phase, "trade-" + position_id, "mkt-1",
            "London", "18°C", direction, token_id,
            no_token_id or TOKEN_X_NO, "2026-05-17T22:13:38",
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
