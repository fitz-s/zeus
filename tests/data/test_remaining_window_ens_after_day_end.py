# Created: 2026-10-02
# Last reused/audited: 2026-10-02
# Authority basis: finite_evidence_probability_symmetry/PLAN.md 2026-10-02
#   full-target ENS quantity containment; broad remaining extrema do not prove
#   either full-day or narrower last-observation suffix extrema.
"""Remaining-window debt candidates never authorize a full-target ENS shape."""

from __future__ import annotations

from datetime import datetime
import sqlite3

import pytest

from src.data import replacement_forecast_materializer as m
from src.data.forecast_extrema_authority import (
    REMAINING_WINDOW_ATTRIBUTION_STATUS,
    current_evidence_ensemble_eligibility_sql,
)

DAY_START = "2026-06-06T16:00:00+00:00"
DAY_END = "2026-06-07T16:00:00+00:00"
TAU = "2026-06-07T15:05:56+00:00"


def _conn(window_start: str, *, status: str = REMAINING_WINDOW_ATTRIBUTION_STATUS,
          causality: str = "OK", window_end: str = DAY_END) -> sqlite3.Connection:
    from tests.test_ens_boundary_interval_admission import _remaining_db
    conn, _ = _remaining_db(window_start)
    conn.execute("UPDATE ensemble_snapshots SET forecast_window_attribution_status=?,"
        "causality_status=?,local_day_start_utc=?,forecast_window_end_utc=?",
        (status, causality, DAY_START, window_end))
    return conn


def _admits(conn: sqlite3.Connection, tau: str, decision: str) -> bool:
    from src.data.replacement_input_hwm import _latest_eligible_ensemble_input_mark
    try:
        return _latest_eligible_ensemble_input_mark(conn, city="Shanghai", target_date="2026-06-07",
            metric="high", decision_time=datetime.fromisoformat(decision), day0_remaining_from_iso=tau) is not None
    finally:
        conn.close()


def test_post_day_broad_row_never_becomes_full_target_or_narrow_suffix_shape():
    assert not _admits(_conn("2026-06-07T06:00:00+00:00"), TAU, "2026-06-07T16:30:00+00:00")


@pytest.mark.parametrize("decision", ["2026-06-07T15:59:59+00:00", "2026-06-07T12:00:00+00:00"])
def test_before_day_end_the_remaining_row_stays_out(decision):
    assert not _admits(_conn("2026-06-07T06:00:00+00:00"), TAU, decision)


@pytest.mark.parametrize("tau", [
    "2026-06-07T05:00:00+00:00",   # window starts after tau: the [tau, start) hours are uncovered
    "2026-06-07T16:00:00+00:00",   # tau at day end: no unobserved suffix
    "2026-06-06T15:00:00+00:00",   # tau before the local day
])
def test_tau_outside_the_window_or_day_is_never_admitted(tau):
    assert not _admits(_conn("2026-06-07T06:00:00+00:00"), tau, "2026-06-07T16:30:00+00:00")


@pytest.mark.parametrize("kwargs", [
    {"status": "FULLY_INSIDE_TARGET_LOCAL_DAY"},
    {"causality": "REJECTED_BOUNDARY_AMBIGUOUS"},
    {"window_end": "2026-06-07T12:00:00+00:00"},
])
def test_only_a_causal_remaining_row_reaching_day_end(kwargs):
    assert not _admits(_conn("2026-06-07T06:00:00+00:00", **kwargs), TAU, "2026-06-07T16:30:00+00:00")


def test_shared_current_evidence_predicate_is_unchanged():
    sql = current_evidence_ensemble_eligibility_sql()
    assert REMAINING_WINDOW_ATTRIBUTION_STATUS not in sql
    assert "?" not in sql


def test_materializer_selector_does_not_promote_remaining_debt_to_full_target_shape():
    from dataclasses import replace
    from tests.test_ens_boundary_interval_admission import _remaining_db
    conn, request = _remaining_db()
    request = replace(request, source_cycle_time=datetime.fromisoformat("2026-06-07T00:00:00+00:00"),
        computed_at=datetime.fromisoformat("2026-06-07T16:30:00+00:00"),
        day0_observed_extreme_observation_time="2026-06-07T15:05:00+00:00")
    assert m._current_evidence_snapshot_row(conn, request, metric="high", select_sql="snapshot_id") is None
    conn.close()
