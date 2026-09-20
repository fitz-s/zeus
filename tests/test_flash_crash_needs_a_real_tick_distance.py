# Created: 2026-09-19
# Lifecycle: created=2026-09-19; last_reviewed=2026-09-19; last_reused=never
# Purpose: Lock the tick-distance floor on the deep-catastrophe witness so a percent
#   threshold cannot become a price-level filter on a discrete grid.
# Reuse: Run when flash-crash velocity, confirmations, or the tick regimes change.
# Authority basis: BUG#127 evidence gate; measured trip-rate artifact (112,857 quotes).
"""A deep RATIO is only a catastrophe if it also covers a real tick DISTANCE.

Measured over 112,857 quotes from 2026-09-12 onward, the -0.40/hr catastrophe
bound was satisfied by 25.4% of one-hour windows at bid <= 0.10 against 0.3%
above 0.60 — an 85x spread produced by tick granularity, not market behaviour.
One 0.01 tick is 12.5% of a 0.08 bid and 1.3% of a 0.75 bid.

Hong Kong 09-20 low 27C (2026-09-19 23:19Z) tripped it on a 0.10 -> 0.06 slide:
four ticks, with the ask never leaving 0.10-0.12. These tests hold the witness to
a real absolute move while leaving every other part of the gate — including the
law that a confirmed deep collapse outranks a fresh belief — untouched.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.engine.monitor_refresh import (
    _causal_deep_market_catastrophe_evidence,
    _causal_market_velocity_1h,
    _deep_drawdown_clears_tick_floor,
    _flash_crash_token_tick,
    _FLASH_CRASH_MIN_TICK_DISTANCE,
)
from src.state.portfolio import flash_crash_confirmations


def _conn(*, with_ask: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ask_col = ", ask REAL" if with_ask else ""
    conn.execute(
        f"""
        CREATE TABLE token_price_log (
            id INTEGER PRIMARY KEY,
            token_id TEXT NOT NULL,
            price REAL NOT NULL,
            bid REAL{ask_col},
            source_timestamp TEXT,
            timestamp TEXT NOT NULL
        )
        """
    )
    return conn


def _quotes(conn, rows, *, with_ask: bool = False) -> None:
    if with_ask:
        conn.executemany(
            "INSERT INTO token_price_log(token_id, price, bid, ask, source_timestamp,"
            " timestamp) VALUES ('held', ?, ?, ?, ?, ?)",
            [(b, b, a, t, t) for b, a, t in rows],
        )
    else:
        conn.executemany(
            "INSERT INTO token_price_log(token_id, price, bid, source_timestamp,"
            " timestamp) VALUES ('held', ?, ?, ?, ?)",
            [(b, b, t, t) for b, t in rows],
        )


def test_a_three_tick_slide_at_a_low_bid_is_not_a_catastrophe():
    """0.09 -> 0.06 is -0.33 by ratio but only three ticks: the artifact case.

    This is the class the floor removes. The 2026-09-19 live incident
    (0.10 -> 0.06) was exactly FOUR ticks and still clears the floor — see
    test_the_live_incident_still_clears_the_floor. The floor removes the bulk of
    the low-price artifact (trip rate at bid <= 0.10 falls 9.3% -> 1.3%), not
    every instance of it.
    """
    conn = _conn()
    _quotes(conn, [
        (0.09, "2026-09-19T22:00:00+00:00"),
        (0.06, "2026-09-19T23:17:58+00:00"),
    ])

    velocity, confirmations = _causal_deep_market_catastrophe_evidence(
        conn,
        token_id="held",
        current_bid=0.06,
        observed_at="2026-09-19T23:18:57+00:00",
    )

    assert velocity is not None
    assert confirmations == 0


def test_the_live_incident_still_clears_the_floor():
    """Honest bound on this fix: 0.10 -> 0.06 is four ticks and remains admissible.

    The tick floor corrects the price-level ARTIFACT (a ratio whose denominator
    carries the tick count). It does not resolve whether a four-tick slide with a
    static ask should outrank a calibrated belief — that is the authority-order
    question, which the ledger cannot currently adjudicate (see
    scratchpad/flash_crash_rule_verdict.md: the flash-crash cohort has n=1 settled
    position with a recorded q, and last_monitor_best_bid is live state, so no
    (q, bid, outcome) triple exists).
    """
    conn = _conn()
    assert _deep_drawdown_clears_tick_floor(
        conn, token_id="held", current_bid=0.06, velocity=-0.40
    ) is True


def test_the_same_ratio_at_a_high_bid_still_fires():
    """0.75 -> 0.45 is the same -0.40 ratio and thirty ticks: a real collapse."""
    conn = _conn()
    _quotes(conn, [
        (0.75, "2026-09-19T22:00:00+00:00"),
        (0.45, "2026-09-19T23:17:00+00:00"),
        (0.45, "2026-09-19T23:18:00+00:00"),
    ])

    velocity, confirmations = _causal_deep_market_catastrophe_evidence(
        conn,
        token_id="held",
        current_bid=0.45,
        observed_at="2026-09-19T23:18:57+00:00",
    )

    assert velocity == pytest.approx(-0.40)
    assert confirmations >= 1


def test_velocity_measurement_itself_is_never_altered():
    """The floor bounds the WITNESS, not the observation."""
    conn = _conn()
    _quotes(conn, [
        (0.09, "2026-09-01T14:54:40+00:00"),
        (0.05, "2026-09-01T15:54:05+00:00"),
    ])

    velocity = _causal_market_velocity_1h(
        conn,
        token_id="held",
        current_bid=0.05,
        observed_at="2026-09-01T15:54:50+00:00",
    )

    assert velocity == pytest.approx((0.05 / 0.09) - 1.0)


def test_exactly_the_floor_clears_it():
    """0.09 -> 0.05 is exactly four coarse ticks; the boundary must admit it.

    The reference is recovered through a division, so this lands a half-ULP short
    of 0.04 and would fail a bare >= comparison.
    """
    conn = _conn()
    assert _deep_drawdown_clears_tick_floor(
        conn, token_id="held", current_bid=0.05, velocity=(0.05 / 0.09) - 1.0
    ) is True


def test_three_ticks_does_not_clear_it():
    conn = _conn()
    # 0.09 -> 0.06 is three coarse ticks.
    assert _deep_drawdown_clears_tick_floor(
        conn, token_id="held", current_bid=0.06, velocity=(0.06 / 0.09) - 1.0
    ) is False


def test_a_collapse_to_zero_clears_any_grid():
    conn = _conn()
    assert _deep_drawdown_clears_tick_floor(
        conn, token_id="held", current_bid=0.0, velocity=-1.0
    ) is True


def test_a_fine_grid_token_needs_only_four_fine_ticks():
    """On the 0.001 regime, four ticks is 0.004 — the floor scales with the grid."""
    conn = _conn(with_ask=True)
    _quotes(conn, [(0.050, 0.051, "2026-09-19T22:00:00+00:00")], with_ask=True)

    assert _flash_crash_token_tick(conn, token_id="held") == 0.001
    # 0.050 -> 0.045 is five fine ticks: clears. It is only half a coarse tick.
    assert _deep_drawdown_clears_tick_floor(
        conn, token_id="held", current_bid=0.045, velocity=(0.045 / 0.050) - 1.0
    ) is True


def test_an_unknown_grid_uses_the_conservative_coarse_tick():
    """No quoted spread must not re-admit the artifact."""
    conn = _conn(with_ask=True)
    assert _flash_crash_token_tick(conn, token_id="held") == 0.01
    assert _flash_crash_token_tick(None, token_id="held") == 0.01


def test_a_rising_bid_never_clears_the_floor():
    conn = _conn()
    assert _deep_drawdown_clears_tick_floor(
        conn, token_id="held", current_bid=0.20, velocity=+0.40
    ) is False


def test_the_floor_is_four_ticks_as_measured():
    """The constant is the swept value: 4 removes the artifact, 8+ over-suppresses."""
    assert _FLASH_CRASH_MIN_TICK_DISTANCE == 4.0


def test_confirmations_still_required_above_the_floor():
    """Clearing the floor does not bypass the persistence requirement."""
    conn = _conn()
    _quotes(conn, [
        (0.75, "2026-09-19T22:00:00+00:00"),
        (0.45, "2026-09-19T23:18:50+00:00"),
    ])

    _velocity, confirmations = _causal_deep_market_catastrophe_evidence(
        conn,
        token_id="held",
        current_bid=0.45,
        observed_at="2026-09-19T23:18:57+00:00",
    )

    assert confirmations < flash_crash_confirmations()
