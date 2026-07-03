# Created: 2026-07-02
# Authority basis: docs/rebuild/order_engine_implementation_architecture_2026-07-02.md
#   line 53 (self-trade guard: BUILD, nothing exists) +
#   docs/operations/current/plans/order_engine_rebuild_execution_plan_2026-07-02.md
#   W2.2 (lands inert; unit + venue-sandbox acceptance).
#
# Truth table for check_self_trade: a proposed Zeus order self-crosses an
# existing Zeus resting order iff same token_id, OPPOSITE side, and price
# overlap in the crossing direction:
#   BUY  crosses resting SELL when candidate_price >= resting_price
#   SELL crosses resting BUY  when candidate_price <= resting_price
# Same-side resting orders never cross (a second BUY does not trade against
# another BUY). Different token_id never crosses (per-token scope; family-
# level exclusivity is a separate concern owned by family_exclusive_dedup.py).
# own_open_orders=None means "unavailable" -> INDETERMINATE (fail-closed at
# the future call site; this packet lands inert, no call site wired yet).

import sqlite3

import pytest
from src.execution.self_trade_guard import (
    RestingOrder,
    SelfTradeVerdict,
    check_self_trade,
    load_own_open_resting_orders,
)

TOKEN_YES = "0xabc123_token_yes"
TOKEN_NO = "0xabc123_token_no"


def _resting(command_id="cmd-1", token_id=TOKEN_YES, side="SELL", price="0.55"):
    return RestingOrder(command_id=command_id, token_id=token_id, side=side, price=price)


# --------------------------------------------------------------------------- #
# CLEAR — no opposite-side resting order on the same token                    #
# --------------------------------------------------------------------------- #


def test_clear_when_no_resting_orders():
    result = check_self_trade(
        token_id=TOKEN_YES, side="BUY", price="0.50", own_open_orders=[]
    )
    assert result.verdict == SelfTradeVerdict.CLEAR
    assert result.crossing_command_ids == ()


def test_clear_when_only_same_side_resting_order():
    resting = _resting(side="BUY", price="0.60")
    result = check_self_trade(
        token_id=TOKEN_YES, side="BUY", price="0.65", own_open_orders=[resting]
    )
    assert result.verdict == SelfTradeVerdict.CLEAR


def test_clear_when_opposite_side_but_different_token():
    resting = _resting(token_id=TOKEN_NO, side="SELL", price="0.40")
    result = check_self_trade(
        token_id=TOKEN_YES, side="BUY", price="0.90", own_open_orders=[resting]
    )
    assert result.verdict == SelfTradeVerdict.CLEAR


def test_clear_when_buy_below_resting_sell_price_no_overlap():
    resting = _resting(side="SELL", price="0.55")
    result = check_self_trade(
        token_id=TOKEN_YES, side="BUY", price="0.54", own_open_orders=[resting]
    )
    assert result.verdict == SelfTradeVerdict.CLEAR


def test_clear_when_sell_above_resting_buy_price_no_overlap():
    resting = _resting(side="BUY", price="0.45")
    result = check_self_trade(
        token_id=TOKEN_YES, side="SELL", price="0.46", own_open_orders=[resting]
    )
    assert result.verdict == SelfTradeVerdict.CLEAR


# --------------------------------------------------------------------------- #
# WOULD_SELF_CROSS — opposite side, same token, price overlaps               #
# --------------------------------------------------------------------------- #


def test_buy_crosses_resting_sell_strictly_above():
    resting = _resting(command_id="cmd-sell", side="SELL", price="0.55")
    result = check_self_trade(
        token_id=TOKEN_YES, side="BUY", price="0.60", own_open_orders=[resting]
    )
    assert result.verdict == SelfTradeVerdict.WOULD_SELF_CROSS
    assert result.crossing_command_ids == ("cmd-sell",)


def test_buy_crosses_resting_sell_exact_price_touch():
    resting = _resting(command_id="cmd-sell", side="SELL", price="0.55")
    result = check_self_trade(
        token_id=TOKEN_YES, side="BUY", price="0.55", own_open_orders=[resting]
    )
    assert result.verdict == SelfTradeVerdict.WOULD_SELF_CROSS
    assert result.crossing_command_ids == ("cmd-sell",)


def test_sell_crosses_resting_buy_strictly_below():
    resting = _resting(command_id="cmd-buy", side="BUY", price="0.45")
    result = check_self_trade(
        token_id=TOKEN_YES, side="SELL", price="0.40", own_open_orders=[resting]
    )
    assert result.verdict == SelfTradeVerdict.WOULD_SELF_CROSS
    assert result.crossing_command_ids == ("cmd-buy",)


def test_sell_crosses_resting_buy_exact_price_touch():
    resting = _resting(command_id="cmd-buy", side="BUY", price="0.45")
    result = check_self_trade(
        token_id=TOKEN_YES, side="SELL", price="0.45", own_open_orders=[resting]
    )
    assert result.verdict == SelfTradeVerdict.WOULD_SELF_CROSS
    assert result.crossing_command_ids == ("cmd-buy",)


def test_multiple_crossing_resting_orders_all_reported():
    resting_a = _resting(command_id="cmd-a", side="SELL", price="0.50")
    resting_b = _resting(command_id="cmd-b", side="SELL", price="0.52")
    non_crossing = _resting(command_id="cmd-c", side="SELL", price="0.90")
    result = check_self_trade(
        token_id=TOKEN_YES,
        side="BUY",
        price="0.60",
        own_open_orders=[resting_a, resting_b, non_crossing],
    )
    assert result.verdict == SelfTradeVerdict.WOULD_SELF_CROSS
    assert set(result.crossing_command_ids) == {"cmd-a", "cmd-b"}


def test_mixed_tokens_only_matching_token_reported():
    same_token = _resting(command_id="cmd-match", token_id=TOKEN_YES, side="SELL", price="0.50")
    other_token = _resting(command_id="cmd-other", token_id=TOKEN_NO, side="SELL", price="0.10")
    result = check_self_trade(
        token_id=TOKEN_YES,
        side="BUY",
        price="0.60",
        own_open_orders=[same_token, other_token],
    )
    assert result.verdict == SelfTradeVerdict.WOULD_SELF_CROSS
    assert result.crossing_command_ids == ("cmd-match",)


# --------------------------------------------------------------------------- #
# INDETERMINATE — fail-closed on missing/invalid inputs                       #
# --------------------------------------------------------------------------- #


def test_indeterminate_when_own_open_orders_unavailable():
    result = check_self_trade(token_id=TOKEN_YES, side="BUY", price="0.50", own_open_orders=None)
    assert result.verdict == SelfTradeVerdict.INDETERMINATE
    assert result.crossing_command_ids == ()


def test_indeterminate_on_missing_token_id():
    result = check_self_trade(token_id="", side="BUY", price="0.50", own_open_orders=[])
    assert result.verdict == SelfTradeVerdict.INDETERMINATE


def test_indeterminate_on_invalid_side():
    result = check_self_trade(
        token_id=TOKEN_YES, side="HOLD", price="0.50", own_open_orders=[]
    )
    assert result.verdict == SelfTradeVerdict.INDETERMINATE


def test_indeterminate_on_invalid_price():
    result = check_self_trade(
        token_id=TOKEN_YES, side="BUY", price="not-a-price", own_open_orders=[]
    )
    assert result.verdict == SelfTradeVerdict.INDETERMINATE


def test_resting_order_with_unparseable_price_is_skipped_not_fatal():
    bad_resting = _resting(command_id="cmd-bad", side="SELL", price="garbage")
    good_resting = _resting(command_id="cmd-good", side="SELL", price="0.50")
    result = check_self_trade(
        token_id=TOKEN_YES,
        side="BUY",
        price="0.60",
        own_open_orders=[bad_resting, good_resting],
    )
    assert result.verdict == SelfTradeVerdict.WOULD_SELF_CROSS
    assert result.crossing_command_ids == ("cmd-good",)


# --------------------------------------------------------------------------- #
# load_own_open_resting_orders — thin DB loader (excluded from pure predicate) #
# --------------------------------------------------------------------------- #


@pytest.fixture
def mem_db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            token_id TEXT NOT NULL,
            side TEXT NOT NULL,
            price REAL NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE venue_order_facts (
            fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_order_id TEXT NOT NULL,
            command_id TEXT NOT NULL,
            state TEXT NOT NULL,
            remaining_size TEXT,
            matched_size TEXT,
            source TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            local_sequence INTEGER NOT NULL
        )
        """
    )
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def _seed_command(conn, command_id, token_id, side, price, state="LIVE"):
    conn.execute(
        """INSERT INTO venue_commands
           (command_id, token_id, side, price, state, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, '2026-07-02T00:00:00', '2026-07-02T00:00:00')""",
        (command_id, token_id, side, price, state),
    )


def _seed_order_fact(conn, venue_order_id, command_id, state, local_sequence):
    conn.execute(
        """INSERT INTO venue_order_facts
           (venue_order_id, command_id, state, remaining_size, matched_size, source,
            observed_at, local_sequence)
           VALUES (?, ?, ?, '10', '0', 'REST', '2026-07-02T00:00:00', ?)""",
        (venue_order_id, command_id, state, local_sequence),
    )


def test_loader_returns_live_resting_order(mem_db):
    _seed_command(mem_db, "cmd-live", TOKEN_YES, "SELL", 0.55)
    _seed_order_fact(mem_db, "order-live", "cmd-live", "LIVE", 1)
    mem_db.commit()

    result = load_own_open_resting_orders(mem_db, token_id=TOKEN_YES)

    assert result is not None
    assert len(result) == 1
    assert result[0].command_id == "cmd-live"
    assert result[0].side == "SELL"


def test_loader_excludes_cancelled_order(mem_db):
    _seed_command(mem_db, "cmd-cancelled", TOKEN_YES, "SELL", 0.55)
    _seed_order_fact(mem_db, "order-cancelled", "cmd-cancelled", "LIVE", 1)
    _seed_order_fact(mem_db, "order-cancelled", "cmd-cancelled", "CANCEL_CONFIRMED", 2)
    mem_db.commit()

    result = load_own_open_resting_orders(mem_db, token_id=TOKEN_YES)

    assert result == []


def test_loader_includes_partially_matched_order(mem_db):
    _seed_command(mem_db, "cmd-partial", TOKEN_YES, "BUY", 0.45)
    _seed_order_fact(mem_db, "order-partial", "cmd-partial", "PARTIALLY_MATCHED", 1)
    mem_db.commit()

    result = load_own_open_resting_orders(mem_db, token_id=TOKEN_YES)

    assert result is not None
    assert len(result) == 1
    assert result[0].command_id == "cmd-partial"


def test_loader_excludes_candidate_command_id(mem_db):
    _seed_command(mem_db, "cmd-self", TOKEN_YES, "SELL", 0.55)
    _seed_order_fact(mem_db, "order-self", "cmd-self", "LIVE", 1)
    mem_db.commit()

    result = load_own_open_resting_orders(
        mem_db, token_id=TOKEN_YES, exclude_command_id="cmd-self"
    )

    assert result == []


def test_loader_filters_by_token_id(mem_db):
    _seed_command(mem_db, "cmd-yes", TOKEN_YES, "SELL", 0.55)
    _seed_order_fact(mem_db, "order-yes", "cmd-yes", "LIVE", 1)
    _seed_command(mem_db, "cmd-no", TOKEN_NO, "SELL", 0.10)
    _seed_order_fact(mem_db, "order-no", "cmd-no", "LIVE", 1)
    mem_db.commit()

    result = load_own_open_resting_orders(mem_db, token_id=TOKEN_YES)

    assert result is not None
    assert [r.command_id for r in result] == ["cmd-yes"]


def test_loader_returns_none_when_tables_missing():
    conn = sqlite3.connect(":memory:")
    result = load_own_open_resting_orders(conn, token_id=TOKEN_YES)
    assert result is None
    conn.close()


def test_loader_feeds_check_self_trade_end_to_end(mem_db):
    _seed_command(mem_db, "cmd-resting-sell", TOKEN_YES, "SELL", 0.55)
    _seed_order_fact(mem_db, "order-resting-sell", "cmd-resting-sell", "LIVE", 1)
    mem_db.commit()

    own_open_orders = load_own_open_resting_orders(mem_db, token_id=TOKEN_YES)
    result = check_self_trade(
        token_id=TOKEN_YES, side="BUY", price="0.60", own_open_orders=own_open_orders
    )

    assert result.verdict == SelfTradeVerdict.WOULD_SELF_CROSS
    assert result.crossing_command_ids == ("cmd-resting-sell",)
