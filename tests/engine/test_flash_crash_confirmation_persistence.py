"""A "persistent catastrophe" must persist in TIME, not in sample count.

`flash_crash_should_fire`'s path (b) exits with no belief input at all — its only call site
(src/state/portfolio.py:1196-1205) hardcodes `has_probability_authority=False` and
`divergence_score=0.0`, so path (a) is unreachable and path (b) is the whole trigger. It then
overrides a HOLD verdict as a sibling `if` (portfolio.py:1245).

Its evidence was `_FLASH_CRASH_CONFIRMATION_MAX_GAP_SECONDS` only — a bound on how far back
confirmations could be sought, never a requirement that they be SEPARATED. Two quotes seconds
apart therefore proved "persistence". Measured on the two 2026-09-17/18 exits:

  Wellington 22888a80-963: confirming instants 00:59:44 and 00:59:59 — 15 s apart. ZERO of
    the 82 quotes in the preceding 20 minutes were at or below the 0.31 bid it sold on; the
    final 5 minutes were flat at 0.54. Our own posterior was 0.787.
  Seattle dd408cf3-b15: instants 22:53:51 and 22:53:58 — 7 s apart, bid 0.36 against a 0.52
    maximum in the same 20 minutes (2 of 54 samples at or below it).

Both bins then settled in our favour — Wellington observed 14.0 C against a 15 C bin, Seattle
71.96 F against a 74-75 F bin — so the trigger liquidated two winners on sub-minute quote
excursions.

Fixing this needs three coordinated changes, which is why they land together: require a
minimum span, deepen the candidate read so a separated instant is still reachable on a dense
quote stream, and apply the span filter while COLLECTING rather than after the candidate list
is already capped at `required - 1`.
"""
from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from src.engine.monitor_refresh import (
    _FLASH_CRASH_CONFIRMATION_CANDIDATE_LIMIT,
    _FLASH_CRASH_CONFIRMATION_MAX_GAP_SECONDS,
    _FLASH_CRASH_CONFIRMATION_MIN_SPAN_SECONDS,
    _causal_deep_market_catastrophe_evidence,
)
from src.state.portfolio import (
    flash_crash_catastrophe_velocity,
    flash_crash_confirmations,
)

NOW = dt.datetime(2026, 9, 18, 12, 0, tzinfo=dt.timezone.utc)


def _log(samples: list[tuple[float, float]]) -> sqlite3.Connection:
    """A price log from (minutes_before_now, bid) pairs."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE token_price_log (
            id INTEGER PRIMARY KEY, token_id TEXT, price REAL, bid REAL,
            ask REAL, source_timestamp TEXT, timestamp TEXT
        )
        """
    )
    rows = []
    for index, (minutes, bid) in enumerate(samples, start=1):
        stamp = (NOW - dt.timedelta(minutes=minutes)).isoformat()
        rows.append((index, "TOK", bid, bid, bid + 0.02, stamp, stamp))
    conn.executemany(
        "INSERT INTO token_price_log VALUES (?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    return conn


def _level(start_min: float, end_min: float, bid: float, step: float = 0.5):
    out = []
    cursor = start_min
    while cursor > end_min:
        out.append((cursor, bid))
        cursor -= step
    return out


def _fires(conn: sqlite3.Connection, bid: float) -> bool:
    velocity, count = _causal_deep_market_catastrophe_evidence(
        conn, token_id="TOK", current_bid=bid, observed_at=NOW.isoformat()
    )
    if velocity is None:
        return False
    return velocity <= flash_crash_catastrophe_velocity() and count >= (
        flash_crash_confirmations()
    )


def test_a_sustained_collapse_still_fires():
    """The protection this threshold was measured for must survive."""
    conn = _log(_level(150, 20, 0.60) + _level(18, -0.5, 0.20))
    assert _fires(conn, 0.20), "a real collapse held for 18 minutes must exit"


def test_a_single_cycle_excursion_does_not_fire():
    """The defect: one print seconds old counted as persistence."""
    conn = _log(_level(150, 1, 0.60) + [(10 / 60.0, 0.20)])
    assert not _fires(conn, 0.20)


def test_two_prints_seconds_apart_do_not_confirm_each_other():
    """The exact shape of both live false panics."""
    conn = _log(_level(150, 1, 0.60) + [(15 / 60.0, 0.20), (7 / 60.0, 0.20)])
    velocity, count = _causal_deep_market_catastrophe_evidence(
        conn, token_id="TOK", current_bid=0.20, observed_at=NOW.isoformat()
    )
    assert velocity is not None and velocity <= flash_crash_catastrophe_velocity()
    assert count < flash_crash_confirmations(), (
        "15 s and 7 s old prints are one market event sampled twice"
    )


def test_a_confirmation_just_past_the_span_counts():
    """The rule is a span, not an exclusion of everything recent."""
    span_minutes = (_FLASH_CRASH_CONFIRMATION_MIN_SPAN_SECONDS + 15.0) / 60.0
    conn = _log(_level(150, 5, 0.60) + [(span_minutes, 0.20), (0.1, 0.20)])
    assert _fires(conn, 0.20)


def test_a_shallow_move_never_fires_however_long_it_persists():
    """Depth and persistence are separate requirements; both must hold."""
    conn = _log(_level(150, 20, 0.60) + _level(18, -0.5, 0.50))
    assert not _fires(conn, 0.50)


def test_the_search_window_admits_a_separated_confirmation():
    """A span requirement inside too narrow a window can never be satisfied."""
    assert (
        _FLASH_CRASH_CONFIRMATION_MAX_GAP_SECONDS
        > _FLASH_CRASH_CONFIRMATION_MIN_SPAN_SECONDS * 2
    ), "the window must leave room to find a separated instant"
    assert _FLASH_CRASH_CONFIRMATION_CANDIDATE_LIMIT >= 100, (
        "a dense quote stream needs a deep candidate read or the span filter "
        "discards every candidate"
    )
