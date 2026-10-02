# Created: 2026-10-02
# Last reused/audited: 2026-10-02
# Authority basis: Day0 law (60f1f591b, c27684d0c) keyed on the LAST OBSERVATION tau
#   (7e9dec5ea). Live 2026-10-02: held Chongqing 10-02's only post-midnight ENS run
#   (02T06Z, snapshot 1396761) is REMAINING_WINDOW_TARGET_LOCAL_DAY [06Z, 16Z) and no
#   current-evidence predicate admitted it after local-day end (16Z).
"""A remaining-window ENS row is current evidence after day end only over [tau, day end)."""

from __future__ import annotations

import inspect
import sqlite3

import pytest

from src.data import replacement_forecast_materializer as m
from src.data.forecast_extrema_authority import (
    REMAINING_WINDOW_ATTRIBUTION_STATUS,
    current_evidence_ensemble_eligibility_sql,
    remaining_window_after_day_end_sql,
)

DAY_START = "2026-10-01T16:00:00+00:00"   # Chongqing 10-02 local midnight
DAY_END = "2026-10-02T16:00:00+00:00"
TAU = "2026-10-02T15:05:56+00:00"


def _conn(window_start: str, *, status: str = REMAINING_WINDOW_ATTRIBUTION_STATUS,
          causality: str = "OK", window_end: str = DAY_END) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE ensemble_snapshots (snapshot_id INTEGER, forecast_window_attribution_status TEXT,
        causality_status TEXT, boundary_ambiguous INTEGER, contributes_to_target_extrema INTEGER,
        local_day_start_utc TEXT, forecast_window_start_utc TEXT, forecast_window_end_utc TEXT)""")
    conn.execute("INSERT INTO ensemble_snapshots VALUES (1, ?, ?, 0, 0, ?, ?, ?)",
                 (status, causality, DAY_START, window_start, window_end))
    return conn


def _admits(conn: sqlite3.Connection, tau: str, decision: str) -> bool:
    return conn.execute(f"SELECT COUNT(*) FROM ensemble_snapshots WHERE {remaining_window_after_day_end_sql()}",
                        (tau, tau, tau, decision)).fetchone()[0] == 1


def test_post_day_06z_row_is_current_over_the_unobserved_suffix():
    assert _admits(_conn("2026-10-02T06:00:00+00:00"), TAU, "2026-10-02T16:30:00+00:00")


@pytest.mark.parametrize("decision", ["2026-10-02T15:59:59+00:00", "2026-10-02T12:00:00+00:00"])
def test_before_day_end_the_remaining_row_stays_out(decision):
    assert not _admits(_conn("2026-10-02T06:00:00+00:00"), TAU, decision)


@pytest.mark.parametrize("tau", [
    "2026-10-02T05:00:00+00:00",   # window starts after tau: the [tau, start) hours are uncovered
    "2026-10-02T16:00:00+00:00",   # tau at day end: no unobserved suffix
    "2026-10-01T15:00:00+00:00",   # tau before the local day
])
def test_tau_outside_the_window_or_day_is_never_admitted(tau):
    assert not _admits(_conn("2026-10-02T06:00:00+00:00"), tau, "2026-10-02T16:30:00+00:00")


@pytest.mark.parametrize("kwargs", [
    {"status": "FULLY_INSIDE_TARGET_LOCAL_DAY"},
    {"causality": "REJECTED_BOUNDARY_AMBIGUOUS"},
    {"window_end": "2026-10-02T12:00:00+00:00"},
])
def test_only_a_causal_remaining_row_reaching_day_end(kwargs):
    assert not _admits(_conn("2026-10-02T06:00:00+00:00", **kwargs), TAU, "2026-10-02T16:30:00+00:00")


def test_shared_current_evidence_predicate_is_unchanged():
    sql = current_evidence_ensemble_eligibility_sql()
    assert REMAINING_WINDOW_ATTRIBUTION_STATUS not in sql
    assert "?" not in sql


def test_materializer_selector_reads_remaining_rows_only_with_tau():
    source = inspect.getsource(m._current_evidence_snapshot_row)
    assert "tau = _day0_remaining_from_iso(request)" in source
    assert "if tau is not None:" in source
    assert "eligibility=remaining_window_after_day_end_sql()" in source
    assert "(*params[:4], tau, tau, tau, decision_at, *params[4:])" in source
