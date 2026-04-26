# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: docs/operations/task_2026-04-26_full_data_midstream_fix_plan/plan.md
#                  slice A1 (in-place metric kwarg on legacy calibration_pairs reads)
"""Slice A1 relationship + function tests.

Pins the structural invariant that legacy `calibration_pairs` reads are
HIGH-only by Phase 9C L3 convention. The legacy schema has no
`temperature_metric` column, so `metric="low"` reads must fail loudly
rather than silently returning HIGH-mixed-with-whatever rows.

Covers `get_pairs_for_bucket`, `get_pairs_count`, `get_decision_group_count`
in `src/calibration/store.py`.

All tests use :memory: SQLite. No production DB writes.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.store import (
    get_decision_group_count,
    get_pairs_count,
    get_pairs_for_bucket,
)
from src.state.db import init_schema


def _seeded_conn() -> sqlite3.Connection:
    """In-memory DB with calibration_pairs schema and a few VERIFIED rows.

    Three VERIFIED canonical_v1 rows under (cluster, season) = ("US-Northeast", "DJF"),
    distinct decision_group_ids, distinct target_dates.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    rows = [
        ("NYC", "2026-01-05", "30-31°F", 0.42, 1, 1.5,
         "DJF", "US-Northeast", "2026-01-04T12:00:00Z",
         30.5, "dg1", 0, "VERIFIED", "canonical_v1"),
        ("NYC", "2026-01-12", "32-33°F", 0.55, 0, 1.5,
         "DJF", "US-Northeast", "2026-01-11T12:00:00Z",
         32.5, "dg2", 0, "VERIFIED", "canonical_v1"),
        ("BOS", "2026-01-20", "28-29°F", 0.48, 1, 1.0,
         "DJF", "US-Northeast", "2026-01-19T12:00:00Z",
         28.5, "dg3", 0, "VERIFIED", "canonical_v1"),
    ]
    conn.executemany(
        """
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days,
         season, cluster, forecast_available_at, settlement_value,
         decision_group_id, bias_corrected, authority, bin_source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return conn


# -----------------------------------------------------------------------------
# Relationship test: LOW reads on legacy calibration_pairs are unwritable
# -----------------------------------------------------------------------------


def test_get_pairs_for_bucket_rejects_low_metric():
    """Asking the legacy reader for LOW must fail loudly, not silently mix."""
    conn = _seeded_conn()
    with pytest.raises(NotImplementedError, match="HIGH-only"):
        get_pairs_for_bucket(conn, "US-Northeast", "DJF", metric="low")


def test_get_pairs_count_rejects_low_metric():
    conn = _seeded_conn()
    with pytest.raises(NotImplementedError, match="HIGH-only"):
        get_pairs_count(conn, "US-Northeast", "DJF", metric="low")


def test_get_decision_group_count_rejects_low_metric():
    conn = _seeded_conn()
    with pytest.raises(NotImplementedError, match="HIGH-only"):
        get_decision_group_count(conn, "US-Northeast", "DJF", metric="low")


# -----------------------------------------------------------------------------
# Behavior preservation: metric="high" and metric=None return identical results
# -----------------------------------------------------------------------------


def test_get_pairs_for_bucket_high_matches_default():
    """metric='high' must equal metric=None (backward-compat preserved)."""
    conn = _seeded_conn()
    default = get_pairs_for_bucket(conn, "US-Northeast", "DJF",
                                    bin_source_filter="canonical_v1")
    high = get_pairs_for_bucket(conn, "US-Northeast", "DJF",
                                 bin_source_filter="canonical_v1", metric="high")
    assert len(default) == len(high) == 3
    # Order is target_date ascending in both calls; compare row-wise.
    for d, h in zip(default, high):
        assert dict(d) == dict(h)


def test_get_pairs_count_high_matches_default():
    conn = _seeded_conn()
    assert get_pairs_count(conn, "US-Northeast", "DJF") == \
        get_pairs_count(conn, "US-Northeast", "DJF", metric="high") == 3


def test_get_decision_group_count_high_matches_default():
    conn = _seeded_conn()
    assert get_decision_group_count(conn, "US-Northeast", "DJF") == \
        get_decision_group_count(conn, "US-Northeast", "DJF", metric="high") == 3


# -----------------------------------------------------------------------------
# Signature contract: metric is keyword-only
# -----------------------------------------------------------------------------


def test_get_pairs_for_bucket_metric_is_keyword_only():
    """Positional metric must raise TypeError (preserve kwarg-only contract)."""
    conn = _seeded_conn()
    # 5 positional args = (conn, cluster, season, authority_filter, bin_source_filter)
    # Adding a 6th positional must fail because `metric` is keyword-only.
    with pytest.raises(TypeError):
        get_pairs_for_bucket(conn, "US-Northeast", "DJF", "VERIFIED",
                              "canonical_v1", "high")  # type: ignore[misc]


def test_get_pairs_count_metric_is_keyword_only():
    conn = _seeded_conn()
    with pytest.raises(TypeError):
        get_pairs_count(conn, "US-Northeast", "DJF", "VERIFIED", "high")  # type: ignore[misc]


def test_get_decision_group_count_metric_is_keyword_only():
    conn = _seeded_conn()
    with pytest.raises(TypeError):
        get_decision_group_count(conn, "US-Northeast", "DJF", "VERIFIED", "high")  # type: ignore[misc]
