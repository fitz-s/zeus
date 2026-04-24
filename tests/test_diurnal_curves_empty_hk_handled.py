# Created: 2026-04-23
# Last reused/audited: 2026-04-23
# Authority basis: .omc/plans/observation-instants-migration-iter3.md AC11
#                  ("must run BEFORE Phase 2 cutover"); step4_phase2_cutover.md
#                  PW3.
"""Antibody AC11 — HK empty-diurnal-curves does not crash signal layer.

Hong Kong is plan-v3 Option A: `observation_instants_v2` for HK is empty
pre-accumulator-start, so `diurnal_curves` has 0 rows for city='Hong Kong'
after Phase 3 ETL rebuild. The signal layer must fall back gracefully
(confidence = 0.0, reason = diagnosable) rather than crash or return NaN.

This test runs before Phase 2 atomic flip (UPDATE zeus_meta SET value=
'v1.wu-native') so the first post-cutover signal tick does not hit an
uncovered code path.

Pinned contract:

- `get_peak_hour_context('Hong Kong', any_date, any_hour)` returns
  (None, 0.0, <reason string>) without raising.
- `post_peak_confidence('Hong Kong', any_date, any_hour)` returns 0.0
  without raising.

Non-HK city with populated diurnal_curves returns non-null peak_hour +
non-zero confidence — sanity guard so the empty-case does not silently
eat every city.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Iterator

import pytest


@pytest.fixture
def db_with_hk_empty(monkeypatch) -> Iterator[sqlite3.Connection]:
    """In-memory DB where 'Hong Kong' has zero diurnal_curves rows and one
    populated city (Chicago) has enough rows to pass the len>=12 gate."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE diurnal_curves (
            city TEXT NOT NULL,
            season TEXT NOT NULL,
            hour INTEGER NOT NULL,
            avg_temp REAL NOT NULL,
            std_temp REAL NOT NULL,
            n_samples INTEGER NOT NULL,
            p_high_set REAL,
            UNIQUE(city, season, hour)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE diurnal_peak_prob (
            city TEXT NOT NULL,
            month INTEGER NOT NULL,
            hour INTEGER NOT NULL,
            p_high_set REAL NOT NULL,
            n_obs INTEGER NOT NULL,
            UNIQUE(city, month, hour)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE solar_daily (
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            timezone TEXT NOT NULL,
            sunrise_local TEXT NOT NULL,
            sunset_local TEXT NOT NULL,
            sunrise_utc TEXT NOT NULL,
            sunset_utc TEXT NOT NULL,
            utc_offset_minutes INTEGER NOT NULL,
            dst_active INTEGER NOT NULL DEFAULT 0,
            UNIQUE(city, target_date)
        )
        """
    )
    # Populate Chicago with 24 seasonal rows to pass the len(season_rows) < 12 gate.
    # Summer peak at hour 15 with a simple bell curve.
    for hour in range(24):
        # JJA season-like curve peaking at 15
        avg_temp = 60.0 + 20.0 * max(0.0, 1.0 - abs(hour - 15) / 10.0)
        p_high_set = 0.95 if hour >= 15 else 0.15
        conn.execute(
            "INSERT INTO diurnal_curves (city, season, hour, avg_temp, std_temp, n_samples, p_high_set) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("Chicago", "JJA", hour, avg_temp, 3.0, 120, p_high_set),
        )
    conn.commit()

    # Monkeypatch src.state.db.get_world_connection and friends.
    # diurnal.get_solar_day calls `get_world_connection` AND closes the conn
    # after fetchone — return a fresh connection each call so the first call
    # does not invalidate subsequent ones.
    def _fake_get_world_connection():
        # Return a fresh cursor-capable connection that shares the same schema
        # but lives for a single call. We return the same in-memory conn because
        # :memory: is process-local; closing it here would invalidate fixtures
        # on the next call. Use a wrapper that no-ops close().
        class _NoCloseConn:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, *args, **kwargs):
                return self._inner.execute(*args, **kwargs)

            def close(self):
                pass  # keep the real conn alive for the whole test

            def __getattr__(self, name):
                return getattr(self._inner, name)

        return _NoCloseConn(conn)

    monkeypatch.setattr("src.state.db.get_world_connection", _fake_get_world_connection)

    try:
        yield conn
    finally:
        conn.close()


def test_hk_peak_hour_context_returns_null_without_raising(db_with_hk_empty):
    """HK with zero diurnal_curves rows must not crash get_peak_hour_context."""
    from src.signal.diurnal import get_peak_hour_context

    peak_hour, confidence, reason = get_peak_hour_context(
        "Hong Kong", date(2026, 7, 15), 14
    )
    assert peak_hour is None, f"expected None peak_hour for empty HK, got {peak_hour}"
    assert confidence == 0.0, f"expected 0.0 confidence, got {confidence}"
    assert isinstance(reason, str) and len(reason) > 0, "reason must be a non-empty string"


def test_hk_post_peak_confidence_returns_zero_without_raising(db_with_hk_empty):
    """HK with zero diurnal_curves rows must return 0.0 from post_peak_confidence."""
    from src.signal.diurnal import post_peak_confidence

    result = post_peak_confidence("Hong Kong", date(2026, 7, 15), 14)
    assert result == 0.0, f"expected 0.0 for empty HK, got {result}"


def test_hk_peak_hour_at_multiple_hours_still_null(db_with_hk_empty):
    """Every clock-hour must produce graceful fallback, not just one."""
    from src.signal.diurnal import get_peak_hour_context

    for hour in (0, 6, 9, 12, 15, 18, 21):
        peak_hour, confidence, reason = get_peak_hour_context(
            "Hong Kong", date(2026, 7, 15), hour
        )
        assert peak_hour is None, (
            f"hour={hour}: expected None peak_hour, got {peak_hour}"
        )
        assert confidence == 0.0, (
            f"hour={hour}: expected 0.0 confidence, got {confidence}"
        )


def test_non_hk_city_with_populated_rows_is_unaffected(db_with_hk_empty):
    """Sanity: the empty-case fallback does not swallow a city with real data.

    If this ever starts returning (None, 0.0, ...) for Chicago, the empty-case
    guard has regressed into something that eats every city.
    """
    from src.signal.diurnal import get_peak_hour_context

    peak_hour, confidence, reason = get_peak_hour_context(
        "Chicago", date(2026, 7, 15), 15
    )
    assert peak_hour is not None, "Chicago must return a non-null peak_hour"
    assert peak_hour == 15, f"expected peak_hour=15 at JJA, got {peak_hour}"
    assert confidence > 0.0, f"expected >0 confidence, got {confidence}"
    assert isinstance(reason, str)
