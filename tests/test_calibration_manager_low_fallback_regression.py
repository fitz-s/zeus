# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: docs/operations/task_2026-04-26_full_data_midstream_fix_plan/plan.md
#                  slice A2-fix1 (post-review BLOCKER from code-reviewer)
"""Slice A2 post-review regression test.

Pre-fix bug: A2 wrapped both `n = get_decision_group_count(...)` AND
`_, _, level3 = calibration_maturity_thresholds()` inside
`if temperature_metric == "high":`. The season-only fallback loop at
manager.py:225 reads `level3` unconditionally for any caller (HIGH or
LOW) that finds a non-None v2 fallback model in another cluster's
bucket. LOW callers therefore crashed with `UnboundLocalError`.

This test pins the fix: LOW caller with a v2 fallback model in a
different cluster MUST NOT raise UnboundLocalError. The fix is to
hoist the `level3` binding above the HIGH branch.

Detected by code-reviewer agent (a10124e75fab05ec6) after slice A4
landed; reproduced empirically before fix.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration import manager as mgr_module
from src.calibration.manager import get_calibrator
from src.config import City, calibration_clusters
from src.state.db import init_schema


def _city() -> City:
    """Minimal City fixture — cluster 'US-Northeast' is widely used in tests."""
    return City(
        name="NYC",
        lat=40.7,
        lon=-74.0,
        timezone="America/New_York",
        settlement_unit="F",
        cluster="US-Northeast",
        wu_station="KNYC",
    )


def test_get_calibrator_low_caller_with_v2_fallback_does_not_raise_unbound_level3(
    monkeypatch,
):
    """Pre-fix this raised UnboundLocalError: level3 not bound for LOW path.

    Setup: LOW caller, primary v2 returns None, but fallback v2 (different
    cluster) returns a populated model. The fallback loop at L225 reads
    `level3` to gate `model_data["n_samples"] >= level3`. If `level3` is
    only bound inside the HIGH branch, this read crashes.

    Acceptance: get_calibrator returns cleanly (cal+level tuple); the
    specific tuple values are not the point — absence of UnboundLocalError
    is the contract.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    city = _city()

    # Pick a fallback cluster guaranteed to differ from city.cluster.
    other_clusters = [c for c in calibration_clusters() if c != city.cluster]
    assert other_clusters, (
        "test setup requires at least one cluster other than the city's; "
        "calibration_clusters() returned only the city's cluster."
    )
    fallback_cluster = other_clusters[0]

    # Stub load_platt_model_v2: return a populated model only when queried
    # for the fallback cluster. This forces the fallback loop to enter the
    # `if model_data is not None and model_data["n_samples"] >= level3:`
    # branch — which is where pre-fix code crashed.
    # n_samples=0 ensures the `model_data["n_samples"] >= level3` comparison
    # at L225 evaluates to False (level3 thresholds are positive), so the
    # fallback loop continues without entering _model_data_to_calibrator.
    # The KEY thing this test guards is that L225 reads `level3` AT ALL —
    # pre-fix, that read crashed with UnboundLocalError before the
    # comparison could even evaluate.
    def fake_v2(conn, *, temperature_metric, cluster, season):
        if cluster == fallback_cluster:
            return {
                "n_samples": 0,
                "input_space": "width_normalized_density",
                "A": 1.0,
                "B": 0.0,
                "C": 0.0,
                "lead_days_min": 0,
                "lead_days_max": 14,
            }
        return None

    monkeypatch.setattr(mgr_module, "load_platt_model_v2", fake_v2)

    # Pre-fix: this raises UnboundLocalError inside the fallback loop.
    # Post-fix: returns cleanly.
    cal, level = get_calibrator(
        conn, city, "2026-01-15", temperature_metric="low"
    )
    # Don't pin specific cal/level — different bucket-routing implementations
    # may produce different fallback maturity. The contract is "no crash".
    assert cal is not None or cal is None  # always true; documents intent
    assert isinstance(level, int)


def test_get_calibrator_high_caller_unchanged_post_fix():
    """Sanity: HIGH callers continue to work after the level3 hoist.

    Empty DB → no model_data anywhere → both HIGH and LOW return (None, 0)
    or similar. This guards against the fix accidentally changing HIGH
    semantics.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    city = _city()

    # No raise; returns the natural empty-DB outcome.
    cal_high, level_high = get_calibrator(
        conn, city, "2026-01-15", temperature_metric="high"
    )
    cal_low, level_low = get_calibrator(
        conn, city, "2026-01-15", temperature_metric="low"
    )
    # Both should yield consistent shapes; values may differ but no crash.
    assert isinstance(level_high, int)
    assert isinstance(level_low, int)
