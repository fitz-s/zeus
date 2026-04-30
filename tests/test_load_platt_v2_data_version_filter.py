# Created: 2026-04-30
# Last reused/audited: 2026-04-30
# Lifecycle: created=2026-04-30; last_reviewed=2026-04-30; last_reused=never
# Purpose: Antibody for BLOCKER #1 (architect audit 2026-04-30) — load_platt_model_v2
#          must filter by data_version when one is supplied so multiple
#          data_versions per (metric, cluster, season) bucket cannot silently
#          shadow each other.
# Reuse: Re-run when Platt v2 model selection, MetricIdentity data_version, or
#        calibration manager fallback lookup semantics change.
# Authority basis: docs/reference/zeus_calibration_weighting_authority.md +
#                  src/types/metric_identity.py:78-90 (canonical data_version
#                  per MetricIdentity).
"""Antibody — Platt v2 read seam respects data_version.

Pre-fix (architect audit 2026-04-30): load_platt_model_v2 SELECT picked
ORDER BY fitted_at DESC LIMIT 1 with no data_version filter. UNIQUE constraint
on platt_models_v2 includes data_version, so multiple versions per (metric,
cluster, season) could coexist; the lookup would silently pick the newest by
fitted_at regardless of version. Today's data has one version per metric so
the lookup was correct, but the contract was implicit.

Post-fix: load_platt_model_v2(data_version=...) filters explicitly. This
antibody pins:
  - When two rows differ ONLY by data_version, the requested version wins.
  - When data_version=None is passed, legacy behavior is preserved (latest
    by fitted_at).
  - get_calibrator threads MetricIdentity.data_version automatically — no
    caller needs to remember.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.calibration.store import load_platt_model_v2, save_platt_model_v2
from src.state.db import init_schema
from src.state.schema.v2_schema import apply_v2_schema
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN, MetricIdentity


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test_platt_v2.db"
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    init_schema(c)
    apply_v2_schema(c)
    yield c
    c.close()


def _save_dummy(conn, *, metric_identity: MetricIdentity, fitted_at: str, A: float):
    """Save a Platt v2 row, then patch fitted_at directly so the test can
    pin ORDER BY behavior. save_platt_model_v2 sets fitted_at internally."""
    save_platt_model_v2(
        conn,
        metric_identity=metric_identity,
        cluster="NYC",
        season="DJF",
        data_version=metric_identity.data_version,
        param_A=A,
        param_B=0.05,
        param_C=-0.5,
        bootstrap_params=[(A, 0.05, -0.5)],
        n_samples=1500,
        brier_insample=0.009,
        input_space="width_normalized_density",
        authority="VERIFIED",
    )
    conn.execute(
        """
        UPDATE platt_models_v2
        SET fitted_at = ?
        WHERE temperature_metric = ?
          AND cluster = 'NYC'
          AND season = 'DJF'
          AND data_version = ?
        """,
        (fitted_at, metric_identity.temperature_metric, metric_identity.data_version),
    )
    conn.commit()


def test_data_version_filter_picks_correct_row(conn):
    """Two rows differing only by data_version — requested version wins."""
    _save_dummy(conn, metric_identity=HIGH_LOCALDAY_MAX, fitted_at="2026-04-29T00:00:00Z", A=1.0)
    # Synthesize a hypothetical future v2 metric upgrade with a different data_version.
    HIGH_V2 = MetricIdentity(
        temperature_metric="high",
        physical_quantity="mx2t6_local_calendar_day_max",
        observation_field="high_temp",
        data_version="tigge_mx2t6_local_calendar_day_max_v2",
    )
    _save_dummy(conn, metric_identity=HIGH_V2, fitted_at="2026-04-30T00:00:00Z", A=2.0)

    legacy = load_platt_model_v2(
        conn,
        temperature_metric="high",
        cluster="NYC",
        season="DJF",
        data_version=HIGH_LOCALDAY_MAX.data_version,
    )
    upgrade = load_platt_model_v2(
        conn,
        temperature_metric="high",
        cluster="NYC",
        season="DJF",
        data_version=HIGH_V2.data_version,
    )
    assert legacy is not None and upgrade is not None
    assert legacy["A"] == 1.0, f"v1 lookup got A={legacy['A']} (expected 1.0)"
    assert upgrade["A"] == 2.0, f"v2 lookup got A={upgrade['A']} (expected 2.0)"


def test_data_version_none_falls_back_to_fitted_at_ordering(conn):
    """data_version=None preserves legacy ORDER BY fitted_at DESC behavior."""
    _save_dummy(conn, metric_identity=HIGH_LOCALDAY_MAX, fitted_at="2026-04-29T00:00:00Z", A=1.0)
    HIGH_V2 = MetricIdentity(
        temperature_metric="high",
        physical_quantity="mx2t6_local_calendar_day_max",
        observation_field="high_temp",
        data_version="tigge_mx2t6_local_calendar_day_max_v2",
    )
    _save_dummy(conn, metric_identity=HIGH_V2, fitted_at="2026-04-30T00:00:00Z", A=2.0)

    no_filter = load_platt_model_v2(
        conn, temperature_metric="high", cluster="NYC", season="DJF", data_version=None,
    )
    assert no_filter is not None
    assert no_filter["A"] == 2.0, "data_version=None should pick newest by fitted_at"


def test_data_version_filter_returns_none_when_no_match(conn):
    """If filter is set but no row matches, return None (not silently fall back)."""
    _save_dummy(conn, metric_identity=HIGH_LOCALDAY_MAX, fitted_at="2026-04-29T00:00:00Z", A=1.0)
    miss = load_platt_model_v2(
        conn,
        temperature_metric="high",
        cluster="NYC",
        season="DJF",
        data_version="tigge_does_not_exist_vX",
    )
    assert miss is None, "explicit data_version filter must not silently match a different version"


def test_get_calibrator_threads_metric_identity_data_version():
    """get_calibrator must pass MetricIdentity.data_version into the v2 read seam.

    Static check: grep src/calibration/manager.py for the data_version kwarg
    threading. If a future refactor accidentally drops it, this fires.
    """
    from pathlib import Path

    src_path = Path(__file__).resolve().parents[1] / "src" / "calibration" / "manager.py"
    txt = src_path.read_text(encoding="utf-8")

    # The fix introduces an `expected_data_version` resolution + threads it
    # into BOTH load_platt_model_v2 call sites (primary + season-pool fallback).
    assert "expected_data_version" in txt, (
        "get_calibrator must resolve expected_data_version from MetricIdentity"
    )
    assert txt.count("data_version=expected_data_version") >= 2, (
        "Both load_platt_model_v2 call sites in get_calibrator must thread "
        "data_version=expected_data_version (primary bucket + season-pool fallback)"
    )
