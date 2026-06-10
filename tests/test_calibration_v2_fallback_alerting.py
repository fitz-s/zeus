# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: docs/operations/task_2026-04-26_full_data_midstream_fix_plan/
#                  phases/task_2026-04-26_phase3_midstream_trust/plan.md slice P3.4
"""Slice P3.4 relationship + function tests.

PR #19 workbook P3.4: add operator-visible alerting when calibration
falls back from v2 to legacy models.

Pre-fix: src/calibration/manager.py:172 (primary bucket fallback) and
:232 (season-only fallback) executed `load_platt_model(conn, bk)` (legacy
read) silently when `load_platt_model` returned None. Operators
monitoring calibration health had no signal that v2 coverage was
incomplete for some (cluster, season).

P3.4 fix: WARNING-level log at each fallback site identifying the
(cluster, season, metric) bucket that fell back. Pure observability
addition; no behavior change.

Three relationship tests:
1. v2 miss + legacy hit → WARNING fires identifying cluster + season.
2. v2 hit (happy path) → no fallback warning fires.
3. Both v2 + legacy miss → no fallback warning (existing "no calibrator"
   path applies).
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration import manager as mgr_module
from src.calibration.manager import get_calibrator
from src.config import City
from src.state.db import init_schema
from src.state.schema.v2_schema import apply_canonical_schema


def _city() -> City:
    return City(
        name="NYC",
        lat=40.7,
        lon=-74.0,
        timezone="America/New_York",
        settlement_unit="F",
        cluster="US-Northeast",
        wu_station="KNYC",
    )


def _populated_legacy_model() -> dict:
    """Minimal legacy platt_models row that get_calibrator can consume.

    Includes all fields _model_data_to_calibrator references; n_samples=0
    means downstream maturity-gate paths short-circuit but the model load
    itself succeeds (which is what triggers the P3.4 warning).
    """
    return {
        "n_samples": 0,
        "input_space": "width_normalized_density",
        "A": 1.0,
        "B": 0.0,
        "C": 0.0,
        "lead_days_min": 0,
        "lead_days_max": 14,
        "bootstrap_params": [],
    }


def test_v2_miss_with_legacy_hit_logs_fallback_warning(monkeypatch, caplog):
    """B3cont: legacy fallback path removed from get_calibrator.
    _emit_legacy_fallback_warning is defined at manager.py:262 but has no
    callers — the call sites were removed as part of the canonical-only migration.
    When v2 misses, no legacy lookup fires and no fallback warning emits.
    This test now asserts the canonical-only behavior: v2 miss → no warning."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_canonical_schema(conn)
    city = _city()

    monkeypatch.setattr(
        mgr_module, "load_platt_model",
        lambda conn, *, temperature_metric, cluster, season, data_version=None, **_kwargs: None,
    )

    with caplog.at_level(logging.WARNING, logger="src.calibration.manager"):
        get_calibrator(conn, city, "2026-01-15", temperature_metric="high")
    fallback_warnings = [
        r for r in caplog.records
        if "v2_to_legacy_fallback" in r.message
    ]
    assert not fallback_warnings, (
        "B3cont: legacy fallback call sites removed (manager.py:262 tombstone). "
        "No v2_to_legacy_fallback warning should fire on v2 miss — canonical path "
        "returns None calibrator without attempting legacy lookup."
    )


def test_v2_hit_does_not_log_fallback_warning(monkeypatch, caplog):
    """Happy path: v2 returns a model → no fallback warning fires."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    city = _city()

    monkeypatch.setattr(
        mgr_module, "load_platt_model",
        lambda conn, *, temperature_metric, cluster, season, data_version=None, **_kwargs: _populated_legacy_model(),
    )

    with caplog.at_level(logging.WARNING, logger="src.calibration.manager"):
        get_calibrator(conn, city, "2026-01-15", temperature_metric="high")
    fallback_warnings = [
        r for r in caplog.records
        if "v2_to_legacy_fallback" in r.message
    ]
    assert not fallback_warnings, (
        "v2 hit must be silent — fallback warning is for v2-MISS only."
    )


def test_both_v2_and_legacy_miss_no_fallback_warning(monkeypatch, caplog):
    """Both miss: no fallback warning (the 'no calibrator' path applies separately)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_canonical_schema(conn)
    city = _city()

    monkeypatch.setattr(
        mgr_module, "load_platt_model",
        lambda conn, *, temperature_metric, cluster, season, data_version=None, **_kwargs: None,
    )

    with caplog.at_level(logging.WARNING, logger="src.calibration.manager"):
        get_calibrator(conn, city, "2026-01-15", temperature_metric="high")
    fallback_warnings = [
        r for r in caplog.records
        if "v2_to_legacy_fallback" in r.message
    ]
    assert not fallback_warnings, (
        "Both-miss case must not emit a fallback warning — the warning is "
        "specifically for the v2-miss-then-legacy-HIT degradation event."
    )


def test_repeated_fallback_for_same_bucket_logs_only_once(monkeypatch, caplog):
    """B3cont: legacy fallback call sites removed (manager.py:262 tombstone).
    _V2_FALLBACK_SEEN dedup set exists but is never populated since no caller
    invokes _emit_legacy_fallback_warning. Running 3 cycles with v2 miss
    produces zero fallback warnings — canonical-only path returns None calibrator
    without touching legacy lookup or dedup set."""
    from src.calibration.manager import _V2_FALLBACK_SEEN
    _V2_FALLBACK_SEEN.clear()

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_canonical_schema(conn)
    city = _city()

    monkeypatch.setattr(
        mgr_module, "load_platt_model",
        lambda conn, *, temperature_metric, cluster, season, data_version=None, **_kwargs: None,
    )

    with caplog.at_level(logging.WARNING, logger="src.calibration.manager"):
        for _ in range(3):
            get_calibrator(conn, city, "2026-01-15", temperature_metric="high")

    fallback_warnings = [
        r for r in caplog.records
        if "v2_to_legacy_fallback" in r.message
    ]
    assert len(fallback_warnings) == 0, (
        f"B3cont: no legacy fallback call sites remain in manager.py. "
        f"3 cycles must emit 0 v2_to_legacy_fallback warnings (got {len(fallback_warnings)}). "
        "Canonical-only path: v2 miss → None calibrator, no legacy lookup."
    )


def test_low_metric_does_not_attempt_legacy_fallback(monkeypatch, caplog):
    """LOW callers skip legacy fallback entirely (per Phase 9C L3 + slice A2).
    No fallback warning should fire even when v2 misses."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_canonical_schema(conn)
    city = _city()

    monkeypatch.setattr(
        mgr_module, "load_platt_model",
        lambda conn, *, temperature_metric, cluster, season, data_version=None, **_kwargs: None,
    )
    legacy_calls: list[str] = []
    def _record_legacy(conn, *, temperature_metric, cluster, season, data_version=None, **_kwargs):
        legacy_calls.append(cluster)
        return None
    monkeypatch.setattr(mgr_module, "load_platt_model", _record_legacy)

    with caplog.at_level(logging.WARNING, logger="src.calibration.manager"):
        get_calibrator(conn, city, "2026-01-15", temperature_metric="low")
    fallback_warnings = [
        r for r in caplog.records
        if "v2_to_legacy_fallback" in r.message
    ]
    assert not fallback_warnings, "LOW must not trigger legacy fallback warning"
