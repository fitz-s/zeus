# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: docs/operations/task_2026-04-26_full_data_midstream_fix_plan/plan.md
#                  slice A3 (snapshot stamping fail-closed +
#                  _normalize_temperature_metric fail-closed root fix)
"""Slice A3 relationship + function tests.

PR #19 finding F7: snapshot stamping silently defaults missing identity
to `high`, hiding LOW writers and malformed upstream as HIGH in the
canonical ensemble_snapshots table that calibration_pairs replay reads
back.

A3 closes this in two places:

1. _normalize_temperature_metric (single legal str→MetricIdentity
   conversion point) was the silent-default ROOT — None / "" / garbage
   would all silently become HIGH. Now raises ValueError on invalid input.

2. _store_ens_snapshot writer seam now refuses to INSERT when
   ens.temperature_metric is missing or malformed, rather than stamping
   the row 'high'.

L91 MarketCandidate.temperature_metric default = "high" is intentionally
kept (legitimate explicit default for callers; high blast radius to
remove). A3b future packet may revisit if needed.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.engine.evaluator import _normalize_temperature_metric, _store_ens_snapshot
from src.types.metric_identity import MetricIdentity


# -----------------------------------------------------------------------------
# Normalizer fail-closed: invalid inputs raise instead of silently HIGHing
# -----------------------------------------------------------------------------


def test_normalize_temperature_metric_raises_on_none():
    with pytest.raises(ValueError, match="must be 'high' or 'low'"):
        _normalize_temperature_metric(None)


def test_normalize_temperature_metric_raises_on_empty_string():
    with pytest.raises(ValueError, match="must be 'high' or 'low'"):
        _normalize_temperature_metric("")


def test_normalize_temperature_metric_raises_on_whitespace_only():
    with pytest.raises(ValueError, match="must be 'high' or 'low'"):
        _normalize_temperature_metric("   ")


def test_normalize_temperature_metric_raises_on_garbage():
    with pytest.raises(ValueError, match="must be 'high' or 'low'"):
        _normalize_temperature_metric("medium")
    with pytest.raises(ValueError, match="must be 'high' or 'low'"):
        _normalize_temperature_metric("hgih")  # typo


# -----------------------------------------------------------------------------
# Behavior preservation: valid inputs still work, case + whitespace tolerant
# -----------------------------------------------------------------------------


def test_normalize_temperature_metric_accepts_high_low():
    high = _normalize_temperature_metric("high")
    low = _normalize_temperature_metric("low")
    assert isinstance(high, MetricIdentity)
    assert isinstance(low, MetricIdentity)
    assert high.temperature_metric == "high"
    assert low.temperature_metric == "low"


def test_normalize_temperature_metric_case_and_whitespace_tolerant():
    assert _normalize_temperature_metric("HIGH").temperature_metric == "high"
    assert _normalize_temperature_metric(" Low ").temperature_metric == "low"
    assert _normalize_temperature_metric("HiGh").temperature_metric == "high"


# -----------------------------------------------------------------------------
# Snapshot writer fail-closed: missing/malformed ens.temperature_metric raises
# -----------------------------------------------------------------------------


def _fake_city(name: str = "NYC"):
    """Minimal city stand-in (only .name attribute is referenced)."""
    return SimpleNamespace(name=name)


def _fake_ens_with_metric(metric_value: str | None = "high"):
    """Build a minimal ENS object the writer reads from.

    Mirrors the production shape: ens.temperature_metric is a MetricIdentity,
    plus member_extrema (numpy array), spread_float(), is_bimodal(), etc.
    The writer references all of these; we only need it to get past the
    metric assertion to test the fail-closed gate.
    """
    if metric_value is None:
        metric = None
    elif metric_value == "missing_inner":
        metric = SimpleNamespace()  # has no .temperature_metric attr
    else:
        metric = MetricIdentity.from_raw(metric_value)
    import numpy as np
    return SimpleNamespace(
        temperature_metric=metric,
        member_extrema=np.array([72.0, 73.0]),
        spread_float=lambda: 1.0,
        is_bimodal=lambda: 0,
    )


def _snapshot_row_count(conn, city: str = "NYC") -> int:
    """Count rows currently in ensemble_snapshots for a city."""
    return conn.execute(
        "SELECT COUNT(*) FROM ensemble_snapshots WHERE city = ?", (city,)
    ).fetchone()[0]


def test_store_ens_snapshot_does_not_write_when_metric_missing(caplog):
    """ens with temperature_metric=None: writer must NOT INSERT a HIGH-stamped row.

    The writer is best-effort and wrapped in try/except (so snapshot failure
    doesn't crash the evaluation flow). The A3 antibody asserts the missing
    -metric branch raises internally → row count stays zero AND a warning
    surfaces in the log. Pre-A3 behavior would silently INSERT a row stamped
    `temperature_metric='high'`.
    """
    import logging
    import sqlite3
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    ens = _fake_ens_with_metric(metric_value=None)
    ens_result = {
        "fetch_time": "2026-04-15T12:00:00Z",
        "model": "ecmwf_ens",
        "issue_time": "2026-04-15T00:00:00Z",
    }
    assert _snapshot_row_count(conn) == 0
    with caplog.at_level(logging.WARNING, logger="src.engine.evaluator"):
        _store_ens_snapshot(conn, _fake_city(), "2026-04-16", ens, ens_result)
    assert _snapshot_row_count(conn) == 0, (
        "snapshot must NOT be written when ens.temperature_metric is missing; "
        "pre-A3 would have silently stamped 'high' here."
    )
    assert any(
        "requires ens.temperature_metric" in rec.message
        for rec in caplog.records
    ), "fail-closed warning must surface in evaluator logs"


def test_store_ens_snapshot_does_not_write_when_inner_metric_missing(caplog):
    """ens.temperature_metric present but lacks .temperature_metric attr: still fail-closed."""
    import logging
    import sqlite3
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    ens = _fake_ens_with_metric(metric_value="missing_inner")
    ens_result = {
        "fetch_time": "2026-04-15T12:00:00Z",
        "model": "ecmwf_ens",
        "issue_time": "2026-04-15T00:00:00Z",
    }
    with caplog.at_level(logging.WARNING, logger="src.engine.evaluator"):
        _store_ens_snapshot(conn, _fake_city(), "2026-04-16", ens, ens_result)
    assert _snapshot_row_count(conn) == 0, (
        "snapshot must NOT be written when ens.temperature_metric is malformed."
    )
    assert any(
        "requires ens.temperature_metric" in rec.message
        for rec in caplog.records
    )


# -----------------------------------------------------------------------------
# No-silent-HIGH antibody: previously, a missing metric would have been
# silently stamped HIGH. Now, the writer refuses rather than mis-stamping.
# This test pins the behavior change so a future "convenience" patch that
# restores the silent fallback would fail loudly.
# -----------------------------------------------------------------------------


def test_no_silent_high_default_path_remains_in_normalizer():
    """Belt-and-braces: confirm the normalizer's body has no silent HIGH path.

    Inspects function source so a future regression that re-introduces the
    `raw = "low" if text == "low" else "high"` pattern will fail this test
    without requiring runtime invocation.
    """
    import inspect
    from src.engine import evaluator
    src = inspect.getsource(evaluator._normalize_temperature_metric)
    assert "raise ValueError" in src, (
        "_normalize_temperature_metric must raise on invalid input; "
        "the silent-HIGH fallback is a known-bad antipattern (PR #19 F7)."
    )
    # Specifically reject the prior silent pattern.
    assert 'raw = "low" if text == "low" else "high"' not in src, (
        "_normalize_temperature_metric must NOT silently default unknown "
        "inputs to 'high'. See slice A3 commit message for context."
    )
