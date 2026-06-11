# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: Task #40 external code review — freshness/row-selection antibody.
#   Two defects in replacement_current_value_serving._rows():
#     (1) ORDER BY ... raw_model_forecast_id ASC + first-row-wins means a later corrected row
#         (higher raw_model_forecast_id, or later captured_at) for the same natural key is silently
#         ignored: the OLDEST row wins instead of the FRESHEST.
#     (2) captured_at NULL/unparseable FAILS OPEN on the single_runs path: the row is branded
#         age_hours=0.0 (maximally fresh) and admitted ahead of a stamped sibling, which creates a
#         ghost-freshness precedence inversion.
#   These tests are antibodies: they pin the CORRECT relationship (later row wins; NULL
#   captured_at never outranks a stamped sibling) and must go RED before the fix, GREEN after.
"""Antibody relationship tests: freshness/row-selection correctness.

Relationship pins:
  (A) Inserting a later corrected row for the same natural key changes what is served — the
      row with the higher raw_model_forecast_id (later insert) wins, not the oldest.
  (B) A row with NULL captured_at never outranks a stamped sibling on the SAME natural key
      and is never branded age_hours=0.0 when a stamped sibling exists. NULL must be treated
      as stale (fail-CLOSED), not maximally fresh (fail-OPEN).
"""
from __future__ import annotations

import sqlite3

from src.data.replacement_current_value_serving import (
    SERVED_VIA_PREVIOUS_RUNS,
    SERVED_VIA_SINGLE_RUNS,
    read_current_instrument_values,
)

CYCLE = "2026-06-11T06:00:00+00:00"
CAPTURE_EARLY = "2026-06-11T08:00:00+00:00"   # 2h after cycle — earlier capture
CAPTURE_LATE  = "2026-06-11T12:00:00+00:00"   # 6h after cycle — later (corrected) capture


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE raw_model_forecasts (
            raw_model_forecast_id INTEGER PRIMARY KEY,
            model TEXT, forecast_value_c REAL,
            city TEXT, metric TEXT, target_date TEXT, lead_days INTEGER,
            source_cycle_time TEXT, endpoint TEXT, captured_at TEXT
        )
        """
    )
    return conn


def _insert(conn, rid, model, value, endpoint, captured):
    conn.execute(
        "INSERT INTO raw_model_forecasts VALUES (?,?,?,?,?,?,?,?,?,?)",
        (rid, model, value, "Tokyo", "high", "2026-06-12", 1,
         CYCLE, endpoint, captured),
    )


def _read(conn, endpoint_filter=None):
    """Thin wrapper — returns the served dict for the fixed natural key."""
    return read_current_instrument_values(
        conn, city="Tokyo", metric="high", target_date="2026-06-12",
        source_cycle_time_iso=CYCLE,
    )


# ============================================================
# (A) Later corrected row must win (freshest-row-per-natural-key)
# ============================================================

def test_later_corrected_single_runs_row_wins_over_earlier_row() -> None:
    """A retry/repair that inserts a corrected row (higher rid, later captured_at) for the same
    natural key must replace the earlier row — the FRESHEST wins, not the oldest (ASC first-row
    bug)."""
    conn = _conn()
    # rid=1 inserted first (older), rid=2 inserted second (corrected value, later captured_at)
    _insert(conn, 1, "gfs_global", 30.0, "single_runs", CAPTURE_EARLY)
    _insert(conn, 2, "gfs_global", 31.5, "single_runs", CAPTURE_LATE)
    out = _read(conn)
    assert "gfs_global" in out
    assert out["gfs_global"].value_c == 31.5, (
        "the later corrected row (rid=2, higher captured_at) must win — the oldest row "
        "(rid=1) must NOT be served (ASC-first-row bug)"
    )
    assert out["gfs_global"].raw_model_forecast_id == 2, (
        "raw_model_forecast_id must be 2 (the corrected/later row), not 1"
    )


def test_later_corrected_previous_runs_row_wins_over_earlier_row() -> None:
    """Same freshest-wins relationship holds for the previous_runs substitution path."""
    conn = _conn()
    _insert(conn, 10, "jma_seamless", 28.0, "previous_runs", CAPTURE_EARLY)
    _insert(conn, 11, "jma_seamless", 29.5, "previous_runs", CAPTURE_LATE)
    out = _read(conn)
    assert "jma_seamless" in out
    assert out["jma_seamless"].value_c == 29.5, (
        "the later corrected previous_runs row (rid=11) must win — oldest must NOT be served"
    )
    assert out["jma_seamless"].raw_model_forecast_id == 11


def test_higher_rid_wins_as_deterministic_tiebreak_when_captured_at_equal() -> None:
    """When captured_at is identical (same-second captures), raw_model_forecast_id DESC is the
    deterministic tiebreak — the row with the higher id was inserted later."""
    conn = _conn()
    _insert(conn, 5, "icon_global", 25.0, "single_runs", CAPTURE_EARLY)
    _insert(conn, 9, "icon_global", 26.0, "single_runs", CAPTURE_EARLY)   # same captured_at
    out = _read(conn)
    assert out["icon_global"].raw_model_forecast_id == 9, (
        "when captured_at is equal, the higher raw_model_forecast_id (9 > 5) is the "
        "deterministic tiebreak — the later-inserted row wins"
    )
    assert out["icon_global"].value_c == 26.0


# ============================================================
# (B) NULL captured_at must fail CLOSED, not maximally fresh
# ============================================================

def test_null_captured_at_row_does_not_outrank_stamped_sibling_single_runs() -> None:
    """A NULL-captured_at row for the same natural key must NEVER win over a stamped sibling.
    The fail-OPEN behaviour (age_hours=0.0 = brand maximally fresh) is the defect: an
    unstamped row appearing first in ASC order would displace the stamped corrected row."""
    conn = _conn()
    # rid=1: NULL captured_at (older insert, lower rid) — must LOSE
    _insert(conn, 1, "ecmwf_ifs025", 20.0, "single_runs", None)
    # rid=2: stamped, later captured_at — must WIN
    _insert(conn, 2, "ecmwf_ifs025", 21.5, "single_runs", CAPTURE_LATE)
    out = _read(conn)
    assert out["ecmwf_ifs025"].raw_model_forecast_id == 2, (
        "the stamped sibling (rid=2) must win over the NULL-captured_at row (rid=1): "
        "NULL captured_at must be treated as stale (fail-CLOSED), not maximally fresh (age=0.0)"
    )
    assert out["ecmwf_ifs025"].value_c == 21.5


def test_null_captured_at_row_does_not_outrank_stamped_sibling_previous_runs() -> None:
    """Same fail-CLOSED requirement on the previous_runs substitution path."""
    conn = _conn()
    _insert(conn, 3, "gem_global", 19.0, "previous_runs", None)          # NULL — must lose
    _insert(conn, 4, "gem_global", 20.5, "previous_runs", CAPTURE_LATE)  # stamped — must win
    out = _read(conn)
    assert out["gem_global"].raw_model_forecast_id == 4, (
        "on the previous_runs path: stamped sibling (rid=4) must outrank NULL-captured_at "
        "row (rid=3) — NULL must be treated as stale, not maximally fresh"
    )


def test_null_captured_at_row_is_not_branded_age_zero_when_stamped_sibling_exists() -> None:
    """When a stamped sibling is present and wins, the served row reports its honest age —
    the NULL-captured_at row must not contaminate the served result with age_hours=0.0."""
    conn = _conn()
    _insert(conn, 1, "ecmwf_ifs025", 20.0, "single_runs", None)
    _insert(conn, 2, "ecmwf_ifs025", 21.5, "single_runs", CAPTURE_LATE)
    out = _read(conn)
    served = out["ecmwf_ifs025"]
    assert served.age_hours > 0.0, (
        "served row's age_hours must reflect the stamped sibling's honest age (>0), not 0.0 "
        "from the NULL-captured_at ghost"
    )
    # 2026-06-11T12:00Z − 2026-06-11T06:00Z = 6.0h
    assert abs(served.age_hours - 6.0) < 0.01, (
        f"expected age_hours ~6.0h for CAPTURE_LATE, got {served.age_hours}"
    )


def test_solo_null_captured_at_row_still_serves_when_no_stamped_sibling_exists() -> None:
    """When there is NO stamped sibling, a NULL-captured_at row is the only data available.
    It should still be served (fail-OPEN on absence of alternative), branded age_hours=0.0
    per the existing documented behaviour — this test confirms the solo-NULL case is unchanged."""
    conn = _conn()
    _insert(conn, 1, "ecmwf_ifs025", 20.0, "single_runs", None)
    out = _read(conn)
    assert "ecmwf_ifs025" in out, (
        "a solo NULL-captured_at row with no stamped sibling should still be served "
        "(fail-OPEN on absence of alternative is acceptable; fail-CLOSED only when a "
        "stamped sibling exists to outrank it)"
    )
    assert out["ecmwf_ifs025"].age_hours == 0.0, (
        "solo NULL captured_at correctly brands age_hours=0.0 (unknowable, no alternative)"
    )
