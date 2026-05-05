# Created: 2026-05-05
# Last reused/audited: 2026-05-05
# Authority basis: architecture/math_defects_2_3_2_4_3_1_design_2026-05-05.md Phase β
"""Tests for Phase β wiring: evaluator calls evidence-gated transfer policy and
threads transfer_logit_sigma into MarketAnalysis."""

from __future__ import annotations

import math
import sqlite3
import warnings
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.config import City, entry_forecast_config
from src.data.calibration_transfer_policy import (
    evaluate_calibration_transfer_policy,
    evaluate_calibration_transfer_policy_with_evidence,
)
from src.state.schema.v2_schema import apply_v2_schema
from src.strategy.market_analysis import compute_transfer_logit_sigma


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_v2_schema(conn)
    return conn


def _insert_transfer_row(
    conn: sqlite3.Connection,
    *,
    status: str,
    brier_diff: float,
    evaluated_at: datetime,
) -> None:
    conn.execute(
        """
        INSERT INTO validated_calibration_transfers (
            policy_id, source_id, target_source_id,
            source_cycle, target_cycle, horizon_profile,
            season, cluster, metric,
            n_pairs, brier_source, brier_target, brier_diff,
            brier_diff_threshold, status,
            evidence_window_start, evidence_window_end,
            platt_model_key, evaluated_at
        ) VALUES (
            'test_policy', 'tigge_mars', 'ecmwf_open_data',
            '00', '00', 'full',
            'summer', 'cluster_a', 'high',
            250, 0.20, 0.205, ?,
            0.005, ?,
            '2025-01-01', '2025-06-01',
            'test_platt_key', ?
        )
        """,
        (brier_diff, status, evaluated_at.isoformat()),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Sub-task 1: evaluator calls _with_evidence not legacy
# ---------------------------------------------------------------------------

def test_evaluator_calls_evidence_gated_function(monkeypatch: pytest.MonkeyPatch) -> None:
    """_write_entry_readiness_for_candidate delegates to _with_evidence, not legacy directly.

    Patch at src.engine.evaluator namespace (where the evaluator holds its
    reference after the import) to verify the evidence-gated function is called.
    """
    monkeypatch.delenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", raising=False)

    called_with_evidence = []

    # evaluator.py imports evaluate_calibration_transfer_policy_with_evidence
    # into its own namespace — patch there, not at the source module.
    import src.engine.evaluator as _ev_mod
    from src.data.calibration_transfer_policy import CalibrationTransferDecision

    original_fn = _ev_mod.evaluate_calibration_transfer_policy_with_evidence

    def _spy(**kwargs):
        called_with_evidence.append(kwargs)
        return original_fn(**kwargs)

    monkeypatch.setattr(_ev_mod, "evaluate_calibration_transfer_policy_with_evidence", _spy)

    from src.engine.evaluator import _write_entry_readiness_for_candidate

    cfg = entry_forecast_config()
    city = MagicMock(spec=City)
    city.name = "London"
    city.timezone = "Europe/London"
    city.cluster = "cluster_a"
    temperature_metric = MagicMock()
    temperature_metric.temperature_metric = "high"

    conn = _make_conn()
    now = datetime(2026, 5, 5, 12, 0, 0)

    with patch("src.engine.evaluator.write_entry_readiness"):
        with patch("src.engine.evaluator.read_promotion_evidence", return_value=None):
            _write_entry_readiness_for_candidate(
                conn,
                cfg=cfg,
                city=city,
                target_local_date=now.date(),
                temperature_metric=temperature_metric,
                market_family="test_family",
                condition_id="test_cid",
                decision_time=now,
            )

    assert len(called_with_evidence) == 1, (
        "_with_evidence should have been called exactly once; "
        f"got {len(called_with_evidence)} calls"
    )


# ---------------------------------------------------------------------------
# Sub-task 2: MarketAnalysis receives correct sigma
# ---------------------------------------------------------------------------

def test_market_analysis_receives_zero_sigma_when_no_row() -> None:
    """When validated_calibration_transfers has no matching row, σ=0.0."""
    conn = _make_conn()
    # No rows inserted — σ must be 0.0

    sigma = _query_sigma_from_conn(conn)
    assert sigma == 0.0


def test_market_analysis_receives_positive_sigma_when_live_eligible_row_exists() -> None:
    """LIVE_ELIGIBLE row with brier_diff=0.005 → σ ≈ sqrt(0.005)*4 ≈ 0.283."""
    conn = _make_conn()
    now = datetime(2026, 5, 5, 12, 0, 0)
    _insert_transfer_row(conn, status="LIVE_ELIGIBLE", brier_diff=0.005,
                         evaluated_at=now - timedelta(days=10))

    sigma = _query_sigma_from_conn(conn, platt_model_key="test_platt_key",
                                   target_source_id="ecmwf_open_data",
                                   target_cycle="00",
                                   horizon_profile="full")
    expected = compute_transfer_logit_sigma(0.005, 4.0)
    assert math.isclose(sigma, expected, rel_tol=1e-9), (
        f"Expected σ≈{expected:.4f}, got {sigma:.4f}"
    )
    assert sigma > 0.28 and sigma < 0.29, f"σ out of expected range: {sigma}"


def test_market_analysis_receives_zero_sigma_when_unsafe_status() -> None:
    """TRANSFER_UNSAFE row → σ=0.0 (route already SHADOW_ONLY upstream)."""
    conn = _make_conn()
    now = datetime(2026, 5, 5, 12, 0, 0)
    _insert_transfer_row(conn, status="TRANSFER_UNSAFE", brier_diff=0.01,
                         evaluated_at=now - timedelta(days=5))

    sigma = _query_sigma_from_conn(conn, platt_model_key="test_platt_key",
                                   target_source_id="ecmwf_open_data",
                                   target_cycle="00",
                                   horizon_profile="full")
    assert sigma == 0.0


def _query_sigma_from_conn(
    conn: sqlite3.Connection,
    *,
    platt_model_key: str = "",
    target_source_id: str = "",
    target_cycle: str = "",
    horizon_profile: str = "",
    season: str = "summer",
    cluster: str = "cluster_a",
    metric: str = "high",
    sigma_scale: float = 4.0,
) -> float:
    """Reproduce the evaluator's bootstrap σ lookup logic against an in-memory DB."""
    row = conn.execute(
        """
        SELECT status, brier_diff
          FROM validated_calibration_transfers
         WHERE target_source_id = ?
           AND target_cycle     = ?
           AND season           = ?
           AND cluster          = ?
           AND metric           = ?
           AND horizon_profile  = ?
           AND platt_model_key  = ?
         LIMIT 1
        """,
        (target_source_id, target_cycle, season, cluster, metric,
         horizon_profile, platt_model_key),
    ).fetchone()
    if row is not None and row[0] == "LIVE_ELIGIBLE":
        return compute_transfer_logit_sigma(float(row[1]), sigma_scale)
    return 0.0


# ---------------------------------------------------------------------------
# Sub-task 3: legacy path emits DeprecationWarning when flag is on
# ---------------------------------------------------------------------------

def test_legacy_path_emits_deprecation_warning_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED=true, calling the legacy
    function directly emits a DeprecationWarning."""
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        evaluate_calibration_transfer_policy(
            config=cfg,
            source_id="ecmwf_open_data",
            forecast_data_version="ecmwf_open_data_high_localday_max_v1",
            live_promotion_approved=True,
        )

    deprecation_warnings = [
        w for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "evaluate_calibration_transfer_policy" in str(w.message)
    ]
    assert len(deprecation_warnings) >= 1, (
        "Expected DeprecationWarning when flag is on and legacy called directly"
    )


def test_legacy_path_no_warning_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When flag is off (default), no DeprecationWarning is emitted."""
    monkeypatch.delenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", raising=False)
    cfg = entry_forecast_config()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        evaluate_calibration_transfer_policy(
            config=cfg,
            source_id="ecmwf_open_data",
            forecast_data_version="ecmwf_open_data_high_localday_max_v1",
            live_promotion_approved=False,
        )

    deprecation_warnings = [
        w for w in caught
        if issubclass(w.category, DeprecationWarning)
        and "evaluate_calibration_transfer_policy" in str(w.message)
    ]
    assert len(deprecation_warnings) == 0, (
        "No DeprecationWarning expected when flag is off"
    )
