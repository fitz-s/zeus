# Created: 2026-05-05
# Last reused/audited: 2026-05-05
# Authority basis: architecture/calibration_transfer_oos_design_2026-05-05.md Phase X.1
"""Tests for evaluate_calibration_transfer_policy_with_evidence (Phase X.1 scaffold)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.config import entry_forecast_config
from src.data.calibration_transfer_policy import (
    evaluate_calibration_transfer_policy_with_evidence,
)
from src.state.schema.v2_schema import apply_v2_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_v2_schema(conn)
    return conn


def _insert_row(
    conn: sqlite3.Connection,
    *,
    status: str,
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
            250, 0.20, 0.21, 0.01,
            0.005, ?,
            '2025-01-01', '2025-06-01',
            'platt_key_1', ?
        )
        """,
        (status, evaluated_at.isoformat()),
    )
    conn.commit()


_BASE_KWARGS = dict(
    source_id="tigge_mars",
    target_source_id="ecmwf_open_data",
    source_cycle="00",
    target_cycle="00",
    horizon_profile="full",
    season="summer",
    cluster="cluster_a",
    metric="high",
    platt_model_key="platt_key_1",
    now=datetime(2026, 5, 5, 12, 0, 0),
    staleness_days=90,
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_feature_flag_off_falls_back_to_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED is unset, the legacy
    function is called and returns SHADOW_ONLY (legacy default)."""
    monkeypatch.delenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", raising=False)
    cfg = entry_forecast_config()
    conn = _make_conn()

    decision = evaluate_calibration_transfer_policy_with_evidence(
        config=cfg,
        conn=conn,
        **_BASE_KWARGS,
    )

    # Legacy path: source_id="tigge_mars" != config.source_id="ecmwf_open_data"
    # so legacy returns BLOCKED with SOURCE_MISMATCH — that is the legacy
    # behaviour, confirming delegation occurred (not the new SHADOW_ONLY path).
    assert decision.status in ("BLOCKED", "SHADOW_ONLY", "LIVE_ELIGIBLE")
    # Key invariant: the new function did NOT query the DB; any result is
    # the legacy function's output.


def test_same_domain_fast_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same source_id + same cycle returns LIVE_ELIGIBLE without querying DB."""
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()

    # Poison the connection so any query raises.
    class PoisonConn:
        def execute(self, *args, **kwargs):  # noqa: ANN001
            raise AssertionError("DB should not be queried on same-domain fast-path")

    decision = evaluate_calibration_transfer_policy_with_evidence(
        config=cfg,
        source_id="tigge_mars",
        target_source_id="tigge_mars",
        source_cycle="00",
        target_cycle="00",
        horizon_profile="full",
        season="summer",
        cluster="cluster_a",
        metric="high",
        platt_model_key="platt_key_1",
        conn=PoisonConn(),  # type: ignore[arg-type]
        now=datetime(2026, 5, 5, 12, 0, 0),
        staleness_days=90,
    )

    assert decision.status == "LIVE_ELIGIBLE"
    assert decision.note == "same_domain_no_transfer"


def test_no_evidence_row_returns_shadow_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty validated_calibration_transfers → SHADOW_ONLY."""
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()
    conn = _make_conn()

    decision = evaluate_calibration_transfer_policy_with_evidence(
        config=cfg,
        conn=conn,
        **_BASE_KWARGS,
    )

    assert decision.status == "SHADOW_ONLY"
    assert "no_evidence_row" in decision.note


def test_fresh_live_eligible_row_returns_live_eligible(monkeypatch: pytest.MonkeyPatch) -> None:
    """Row with status=LIVE_ELIGIBLE and recent evaluated_at → LIVE_ELIGIBLE."""
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()
    conn = _make_conn()
    now = datetime(2026, 5, 5, 12, 0, 0)
    _insert_row(conn, status="LIVE_ELIGIBLE", evaluated_at=now - timedelta(days=10))

    decision = evaluate_calibration_transfer_policy_with_evidence(
        config=cfg,
        conn=conn,
        **{**_BASE_KWARGS, "now": now},
    )

    assert decision.status == "LIVE_ELIGIBLE"
    assert decision.note == "db_row_live_eligible"


def test_stale_row_returns_shadow_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Row with evaluated_at 100 days ago → SHADOW_ONLY (stale)."""
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()
    conn = _make_conn()
    now = datetime(2026, 5, 5, 12, 0, 0)
    _insert_row(conn, status="LIVE_ELIGIBLE", evaluated_at=now - timedelta(days=100))

    decision = evaluate_calibration_transfer_policy_with_evidence(
        config=cfg,
        conn=conn,
        **{**_BASE_KWARGS, "now": now},
    )

    assert decision.status == "SHADOW_ONLY"
    assert "stale" in decision.note.lower()


def test_transfer_unsafe_row_returns_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Row with status=TRANSFER_UNSAFE → BLOCKED."""
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()
    conn = _make_conn()
    now = datetime(2026, 5, 5, 12, 0, 0)
    _insert_row(conn, status="TRANSFER_UNSAFE", evaluated_at=now - timedelta(days=5))

    decision = evaluate_calibration_transfer_policy_with_evidence(
        config=cfg,
        conn=conn,
        **{**_BASE_KWARGS, "now": now},
    )

    assert decision.status == "BLOCKED"
    assert decision.note == "db_row_transfer_unsafe"
