"""A withdrawn order book is a liquidity fact, not a -100%/h price move.

`_held_token_quote_from_book` deliberately carries an empty bid side forward as
0.0 (the exit/submit boundary still refuses it as non-executable SELL
authority). Feeding that 0.0 into `_causal_market_velocity_1h`, a price-CHANGE
measure, reads the withdrawal as a total collapse.

Live case 2026-09-18: the held NO bid for Shanghai sat at 0.998-0.999 for 25
minutes, then BOTH book sides went absent at 16:30 and flash-crash fired
`velocity=-1.00/hr` on a position whose settlement was already a win (local-day
low 25.0C against a shorted 24C bin). Only `NO_EXECUTABLE_BID` prevented the
sale. Replayed on those exact logged quotes: -1.000 before, None after.

A genuinely worthless market still quotes an ask (someone sells the worthless
side), so "bid absent AND ask absent" is the discriminator.
"""
import sqlite3

import pytest

from src.engine.monitor_refresh import (
    _book_side_absent_for_token,
    _causal_market_velocity_1h,
)

TOKEN = "tok-withdrawn"


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        """
        CREATE TABLE token_price_log (
            id INTEGER PRIMARY KEY, token_id TEXT, city TEXT, target_date TEXT,
            range_label TEXT, price REAL, volume REAL, bid REAL, ask REAL,
            spread REAL, source_timestamp TEXT, timestamp TEXT
        )
        """
    )
    return c


def _log(c, *, bid, ask, at):
    c.execute(
        "INSERT INTO token_price_log (token_id, bid, ask, source_timestamp, timestamp) "
        "VALUES (?,?,?,?,?)",
        (TOKEN, bid, ask, at, at.replace("T", " ")[:19]),
    )


def _seed_sustained_high(c):
    """A real, two-sided 0.90 bid across the 1-2h reference window."""
    for minute in (0, 20, 40, 55):
        _log(c, bid=0.90, ask=0.92, at=f"2026-09-18T14:{minute:02d}:00+00:00")


def test_withdrawn_book_withdraws_market_path_evidence(conn):
    _seed_sustained_high(conn)
    _log(conn, bid=0.0, ask=None, at="2026-09-18T16:30:00+00:00")
    assert _book_side_absent_for_token(
        conn, token_id=TOKEN, observed_at="2026-09-18T16:30:00+00:00"
    ) is True
    assert (
        _causal_market_velocity_1h(
            conn, token_id=TOKEN, current_bid=0.0,
            observed_at="2026-09-18T16:30:00+00:00",
        )
        is None
    )


def test_a_real_zero_bid_with_a_live_ask_still_reports_the_collapse(conn):
    """The boundary: a two-sided book at 0.0 IS a price fact and must fire."""
    _seed_sustained_high(conn)
    _log(conn, bid=0.0, ask=0.02, at="2026-09-18T16:30:00+00:00")
    assert _book_side_absent_for_token(
        conn, token_id=TOKEN, observed_at="2026-09-18T16:30:00+00:00"
    ) is False
    velocity = _causal_market_velocity_1h(
        conn, token_id=TOKEN, current_bid=0.0,
        observed_at="2026-09-18T16:30:00+00:00",
    )
    assert velocity == pytest.approx(-1.0)


def test_a_positive_bid_is_unaffected_by_the_guard(conn):
    _seed_sustained_high(conn)
    _log(conn, bid=0.45, ask=None, at="2026-09-18T16:30:00+00:00")
    velocity = _causal_market_velocity_1h(
        conn, token_id=TOKEN, current_bid=0.45,
        observed_at="2026-09-18T16:30:00+00:00",
    )
    assert velocity == pytest.approx(0.45 / 0.90 - 1.0)


def test_an_unreadable_price_log_keeps_the_existing_behaviour(conn):
    """Fail-open on the DISCRIMINATOR so a real collapse is never suppressed."""
    conn.execute("DROP TABLE token_price_log")
    assert _book_side_absent_for_token(
        conn, token_id=TOKEN, observed_at="2026-09-18T16:30:00+00:00"
    ) is False


def test_no_log_row_at_all_is_not_treated_as_withdrawn(conn):
    assert _book_side_absent_for_token(
        conn, token_id=TOKEN, observed_at="2026-09-18T16:30:00+00:00"
    ) is False
