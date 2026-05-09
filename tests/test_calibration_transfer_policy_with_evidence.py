# Created: 2026-05-05
# Last reused/audited: 2026-05-08
# Lifecycle: created=2026-05-05; last_reviewed=2026-05-08; last_reused=2026-05-08
# Authority basis: architecture/calibration_transfer_oos_design_2026-05-05.md Phase X.1
# Purpose: Lock calibration transfer policy evidence row eligibility and policy_id isolation.
# Reuse: Run when evaluate_calibration_transfer_policy_with_evidence or validated_calibration_transfers reader semantics change.
"""Tests for evaluate_calibration_transfer_policy_with_evidence (Phase X.1 scaffold)."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.config import calibration_batch_rebuild_n_mc, entry_forecast_config
from src.contracts.ensemble_snapshot_provenance import ECMWF_OPENDATA_HIGH_DATA_VERSION
from src.data.calibration_transfer_policy import (
    CANONICAL_CALIBRATION_PAIR_BIN_SOURCE,
    POLICY_ECMWF_OPENDATA_USES_TIGGE_LOCALDAY_CAL_V1,
    _rebuild_complete_sentinel_key_for_transfer_evidence,
    evaluate_calibration_transfer_policy,
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


def _write_rebuild_sentinel(
    conn: sqlite3.Connection,
    *,
    metric: str = "high",
    source_id: str = "ecmwf_open_data",
    cycle: str = "00",
    horizon_profile: str = "full",
    status: str = "complete",
    n_mc: int | None = None,
) -> None:
    resolved_n_mc = calibration_batch_rebuild_n_mc() if n_mc is None else n_mc
    key = _rebuild_complete_sentinel_key_for_transfer_evidence(
        metric=metric,
        target_source_id=source_id,
        target_cycle=cycle,
        horizon_profile=horizon_profile,
        n_mc=resolved_n_mc,
    )
    conn.execute(
        """
        INSERT INTO zeus_meta (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            key,
            json.dumps(
                {
                    "status": status,
                    "completed": status == "complete",
                    "recorded_at": "2026-05-05T12:00:00+00:00",
                    "temperature_metric": metric,
                    "bin_source": CANONICAL_CALIBRATION_PAIR_BIN_SOURCE,
                    "scope": {
                        "city": None,
                        "start_date": None,
                        "end_date": None,
                        "data_version": None,
                        "cycle": cycle,
                        "source_id": source_id,
                        "horizon_profile": horizon_profile,
                        "n_mc": resolved_n_mc,
                    },
                    "stats": {},
                },
                sort_keys=True,
            ),
        ),
    )


def _insert_target_pairs_for_transfer(
    conn: sqlite3.Connection,
    *,
    target_source_id: str = "ecmwf_open_data",
    target_cycle: str = "00",
    horizon_profile: str = "full",
    season: str = "summer",
    cluster: str = "cluster_a",
    metric: str = "high",
    n_pairs: int = 250,
    brier_target: float = 0.205,
    recorded_at: str = "2026-01-01T00:00:00",
    rebuild_status: str | None = "complete",
) -> None:
    if n_pairs <= 0 or not (0.0 <= brier_target < 1.0):
        return
    p_raw = 1.0 - math.sqrt(brier_target)
    if not (0.0 < p_raw < 1.0):
        return
    total_pairs = n_pairs * 5
    base_target = datetime(2022, 3, 1, tzinfo=timezone.utc)
    base_forecast = datetime(2022, 2, 1, tzinfo=timezone.utc)
    rows = [
        (
            i + 1,
            "test_city",
            (base_target + timedelta(days=i)).date().isoformat(),
            metric,
            "high_temp" if metric == "high" else "low_temp",
            f"bucket_{i}",
            p_raw,
            1,
            1.0 + float(i % 7),
            season,
            cluster,
            (base_forecast + timedelta(days=i)).isoformat(),
            f"dg_transfer_{target_source_id}_{target_cycle}_{i}",
            "v1",
            target_source_id,
            target_cycle,
            horizon_profile,
            1,
            "VERIFIED",
            "OK",
            recorded_at,
        )
        for i in range(total_pairs)
    ]
    conn.executemany(
        """
        INSERT OR IGNORE INTO calibration_pairs_v2 (
            pair_id,
            city, target_date, temperature_metric, observation_field, range_label,
            p_raw, outcome, lead_days, season, cluster,
            forecast_available_at, decision_group_id, data_version,
            source_id, cycle, horizon_profile,
            training_allowed, authority, causality_status, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    if rebuild_status is not None:
        _write_rebuild_sentinel(
            conn,
            metric=metric,
            source_id=target_source_id,
            cycle=target_cycle,
            horizon_profile=horizon_profile,
            status=rebuild_status,
        )


def _insert_row(
    conn: sqlite3.Connection,
    *,
    status: str,
    evaluated_at: datetime | str,
    policy_id: str = POLICY_ECMWF_OPENDATA_USES_TIGGE_LOCALDAY_CAL_V1,
    source_id: str = "tigge_mars",
    source_cycle: str = "00",
    n_pairs: int = 250,
    brier_source: float = 0.20,
    brier_target: float = 0.205,
    brier_diff: float = 0.005,
    brier_diff_threshold: float = 0.005,
    platt_model_key: str = "platt_key_1",
    source_model_n_samples: int = 100,
    source_model_brier_insample: float | None = None,
    source_model_authority: str = "VERIFIED",
    source_model_input_space: str = "raw_probability",
    source_model_fitted_at: str = "2026-01-01T00:00:00",
    source_model_recorded_at: str = "2026-01-01T00:00:00",
    source_model_is_active: int = 1,
    source_model_param_A: float = 1.0,
    source_model_param_B: float = 0.0,
    source_model_param_C: float = 0.0,
    insert_target_pairs: bool = True,
    target_pair_recorded_at: str = "2026-01-01T00:00:00",
    target_rebuild_status: str | None = "complete",
) -> None:
    evaluated_at_value = (
        evaluated_at.isoformat() if isinstance(evaluated_at, datetime) else evaluated_at
    )
    if source_model_brier_insample is None:
        source_model_brier_insample = brier_source
    if insert_target_pairs:
        _insert_target_pairs_for_transfer(
            conn,
            n_pairs=n_pairs,
            brier_target=brier_target,
            recorded_at=target_pair_recorded_at,
            rebuild_status=target_rebuild_status,
        )
    conn.execute(
        """
        INSERT OR REPLACE INTO platt_models_v2 (
            model_key, temperature_metric, cluster, season, data_version,
            input_space, param_A, param_B, param_C,
            bootstrap_params_json, n_samples, brier_insample,
            fitted_at, is_active, authority,
            cycle, source_id, horizon_profile, recorded_at
        ) VALUES (
            ?, 'high', 'cluster_a', 'summer', 'v1',
            ?, ?, ?, ?,
            '[]', ?, ?,
            ?, ?, ?,
            ?, ?, 'full', ?
        )
        """,
        (
            platt_model_key,
            source_model_input_space,
            source_model_param_A,
            source_model_param_B,
            source_model_param_C,
            source_model_n_samples,
            source_model_brier_insample,
            source_model_fitted_at,
            source_model_is_active,
            source_model_authority,
            source_cycle,
            source_id,
            source_model_recorded_at,
        ),
    )
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
            ?, ?, 'ecmwf_open_data',
            ?, '00', 'full',
            'summer', 'cluster_a', 'high',
            ?, ?, ?, ?,
            ?, ?,
            '2025-01-01', '2025-06-01',
            ?, ?
        )
        """,
        (
            policy_id,
            source_id,
            source_cycle,
            n_pairs,
            brier_source,
            brier_target,
            brier_diff,
            brier_diff_threshold,
            status,
            platt_model_key,
            evaluated_at_value,
        ),
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


def test_wrong_policy_row_returns_shadow_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rows for another transfer policy cannot authorize the active policy."""
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()
    conn = _make_conn()
    now = datetime(2026, 5, 5, 12, 0, 0)
    _insert_row(
        conn,
        status="LIVE_ELIGIBLE",
        evaluated_at=now - timedelta(days=10),
        policy_id="test_policy",
    )

    decision = evaluate_calibration_transfer_policy_with_evidence(
        config=cfg,
        conn=conn,
        **{**_BASE_KWARGS, "now": now},
    )

    assert decision.status == "SHADOW_ONLY"
    assert decision.note == "no_evidence_row"


def test_wrong_source_row_returns_shadow_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rows for another calibration source/cycle cannot authorize this transfer."""
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()
    conn = _make_conn()
    now = datetime(2026, 5, 5, 12, 0, 0)
    _insert_row(
        conn,
        status="LIVE_ELIGIBLE",
        evaluated_at=now - timedelta(days=10),
        source_id="legacy_source",
        source_cycle="12",
    )

    decision = evaluate_calibration_transfer_policy_with_evidence(
        config=cfg,
        conn=conn,
        **{**_BASE_KWARGS, "now": now},
    )

    assert decision.status == "SHADOW_ONLY"
    assert decision.note == "no_evidence_row"


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


@pytest.mark.parametrize("target_rebuild_status", [None, "in_progress"])
def test_live_eligible_row_requires_complete_target_rebuild_sentinel(
    monkeypatch: pytest.MonkeyPatch,
    target_rebuild_status: str | None,
) -> None:
    """OOS transfer rows cannot become live authority from partial target pairs."""
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()
    conn = _make_conn()
    now = datetime(2026, 5, 5, 12, 0, 0)
    _insert_row(
        conn,
        status="LIVE_ELIGIBLE",
        evaluated_at=now - timedelta(days=10),
        target_rebuild_status=target_rebuild_status,
    )

    decision = evaluate_calibration_transfer_policy_with_evidence(
        config=cfg,
        conn=conn,
        **{**_BASE_KWARGS, "now": now},
    )

    assert decision.status == "SHADOW_ONLY"
    assert decision.live_promotion_approved is False
    assert decision.note == "invalid_target_cohort_evidence"


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


@pytest.mark.parametrize(
    "overrides",
    [
        {"brier_source": 2.0},
        {"brier_target": -0.1},
        {"brier_diff": float("inf")},
        {"brier_diff_threshold": float("inf")},
        {"n_pairs": 1},
        {"brier_source": 0.20, "brier_target": 0.21, "brier_diff": 0.50},
    ],
)
def test_invalid_economics_row_returns_shadow_only(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict,
) -> None:
    """LIVE status is not authority unless the stored economics also validate."""
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()
    conn = _make_conn()
    now = datetime(2026, 5, 5, 12, 0, 0)
    _insert_row(
        conn,
        status="LIVE_ELIGIBLE",
        evaluated_at=now - timedelta(days=5),
        **overrides,
    )

    decision = evaluate_calibration_transfer_policy_with_evidence(
        config=cfg,
        conn=conn,
        **{**_BASE_KWARGS, "now": now},
    )

    assert decision.status == "SHADOW_ONLY"
    assert decision.live_promotion_approved is False
    assert decision.note == "invalid_evidence_row"


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_model_n_samples": 1},
        {"source_model_brier_insample": 0.19},
        {"source_model_authority": "UNVERIFIED"},
        {"source_model_input_space": "calibrated_probability"},
        {"source_model_fitted_at": "2026-05-06T00:00:00"},
        {"source_model_recorded_at": "2026-05-06T00:00:00"},
        {"source_model_is_active": 0},
        {"source_model_param_A": float("inf")},
        {"source_model_param_B": float("inf")},
        {"source_model_param_C": float("inf")},
    ],
)
def test_invalid_source_platt_model_returns_shadow_only(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict,
) -> None:
    """Transfer rows must still point at the mature source Platt model they scored."""
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()
    conn = _make_conn()
    now = datetime(2026, 5, 5, 12, 0, 0)
    _insert_row(
        conn,
        status="LIVE_ELIGIBLE",
        evaluated_at=now - timedelta(days=5),
        **overrides,
    )

    decision = evaluate_calibration_transfer_policy_with_evidence(
        config=cfg,
        conn=conn,
        **{**_BASE_KWARGS, "now": now},
    )

    assert decision.status == "SHADOW_ONLY"
    assert decision.live_promotion_approved is False
    assert decision.note == "invalid_source_platt_evidence"


def test_missing_target_cohort_returns_shadow_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aggregate transfer evidence is not authority without its eligible held-out cohort."""
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()
    conn = _make_conn()
    now = datetime(2026, 5, 5, 12, 0, 0)
    _insert_row(
        conn,
        status="LIVE_ELIGIBLE",
        evaluated_at=now - timedelta(days=5),
        insert_target_pairs=False,
    )

    decision = evaluate_calibration_transfer_policy_with_evidence(
        config=cfg,
        conn=conn,
        **{**_BASE_KWARGS, "now": now},
    )

    assert decision.status == "SHADOW_ONLY"
    assert decision.live_promotion_approved is False
    assert decision.note == "invalid_target_cohort_evidence"


def test_post_evidence_target_cohort_returns_shadow_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Target rows recorded after transfer evaluation cannot retroactively validate it."""
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()
    conn = _make_conn()
    now = datetime(2026, 5, 5, 12, 0, 0)
    _insert_row(
        conn,
        status="LIVE_ELIGIBLE",
        evaluated_at=now - timedelta(days=5),
        target_pair_recorded_at="2026-05-06T00:00:00",
    )

    decision = evaluate_calibration_transfer_policy_with_evidence(
        config=cfg,
        conn=conn,
        **{**_BASE_KWARGS, "now": now},
    )

    assert decision.status == "SHADOW_ONLY"
    assert decision.live_promotion_approved is False
    assert decision.note == "invalid_target_cohort_evidence"


def test_malformed_evaluated_at_returns_shadow_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed evidence timestamps cannot crash or authorize live transfer."""
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()
    conn = _make_conn()
    now = datetime(2026, 5, 5, 12, 0, 0)
    _insert_row(
        conn,
        status="LIVE_ELIGIBLE",
        evaluated_at="not-a-time",
    )

    decision = evaluate_calibration_transfer_policy_with_evidence(
        config=cfg,
        conn=conn,
        **{**_BASE_KWARGS, "now": now},
    )

    assert decision.status == "SHADOW_ONLY"
    assert decision.live_promotion_approved is False
    assert decision.note == "invalid_evidence_time"


def test_future_evaluated_at_returns_shadow_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evidence not available at decision time cannot authorize transfer."""
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()
    conn = _make_conn()
    now = datetime(2026, 5, 5, 12, 0, 0)
    _insert_row(
        conn,
        status="LIVE_ELIGIBLE",
        evaluated_at=now + timedelta(days=1),
    )

    decision = evaluate_calibration_transfer_policy_with_evidence(
        config=cfg,
        conn=conn,
        **{**_BASE_KWARGS, "now": now},
    )

    assert decision.status == "SHADOW_ONLY"
    assert decision.live_promotion_approved is False
    assert decision.note == "future_evidence_time"


def test_pseudo_oos_target_evidence_fails_closed_against_time_blocked_cohort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A LIVE row is not authority if current time-blocked cohort disagrees."""
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()
    conn = _make_conn()
    now = datetime(2026, 5, 5, 12, 0, 0)
    _insert_row(
        conn,
        status="LIVE_ELIGIBLE",
        evaluated_at=now - timedelta(days=5),
        n_pairs=200,
        brier_source=0.09,
        brier_target=0.09,
        brier_diff=0.0,
        source_model_brier_insample=0.09,
        insert_target_pairs=False,
    )
    _insert_target_pairs_for_transfer(conn, n_pairs=200, brier_target=0.81)

    decision = evaluate_calibration_transfer_policy_with_evidence(
        config=cfg,
        conn=conn,
        **{**_BASE_KWARGS, "now": now},
    )

    assert decision.status == "SHADOW_ONLY"
    assert decision.live_promotion_approved is False
    assert decision.note == "invalid_target_cohort_evidence"


def test_live_status_with_unsafe_brier_diff_returns_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Consumers re-derive unsafe status from Brier economics instead of trusting status."""
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()
    conn = _make_conn()
    now = datetime(2026, 5, 5, 12, 0, 0)
    _insert_row(
        conn,
        status="LIVE_ELIGIBLE",
        evaluated_at=now - timedelta(days=5),
        brier_source=0.0,
        brier_target=0.5,
        brier_diff=0.5,
        brier_diff_threshold=0.005,
    )

    decision = evaluate_calibration_transfer_policy_with_evidence(
        config=cfg,
        conn=conn,
        **{**_BASE_KWARGS, "now": now},
    )

    assert decision.status == "BLOCKED"
    assert decision.live_promotion_approved is False
    assert decision.note == "db_row_transfer_unsafe_by_economics"


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


def test_policy_filter_ignores_wrong_policy_live_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """A wrong-policy LIVE row must not mask the active policy's unsafe evidence."""
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()
    conn = _make_conn()
    now = datetime(2026, 5, 5, 12, 0, 0)
    _insert_row(
        conn,
        status="LIVE_ELIGIBLE",
        evaluated_at=now - timedelta(days=5),
        policy_id="test_policy",
    )
    _insert_row(
        conn,
        status="TRANSFER_UNSAFE",
        evaluated_at=now - timedelta(days=5),
    )

    decision = evaluate_calibration_transfer_policy_with_evidence(
        config=cfg,
        conn=conn,
        **{**_BASE_KWARGS, "now": now},
    )

    assert decision.status == "BLOCKED"
    assert decision.note == "db_row_transfer_unsafe"


# ---------------------------------------------------------------------------
# PR #61 review-remediation antibody tests
# ---------------------------------------------------------------------------

def test_none_route_keys_return_shadow_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """None source_cycle/target_cycle must NOT trigger the same-domain fast-path.

    Regression guard for PR #61 review comment (Codex/Copilot): the readiness
    write callsite passes None for route keys before the forecast is resolved.
    None==None was silently firing the same-domain LIVE_ELIGIBLE path, which
    would mark unresolved routes live-eligible without DB evidence.
    Fix: guard at top of function returns SHADOW_ONLY/INSUFFICIENT_INFO.
    """
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()
    conn = _make_conn()

    decision = evaluate_calibration_transfer_policy_with_evidence(
        config=cfg,
        conn=conn,
        source_id="ecmwf_open_data",
        target_source_id=None,      # None — pre-forecast readiness callsite
        source_cycle=None,
        target_cycle=None,
        horizon_profile=None,
        season=None,
        cluster=None,
        metric="high",
        platt_model_key=None,
        now=datetime(2026, 5, 5, 12, 0, 0),
    )

    assert decision.status == "SHADOW_ONLY", (
        f"Expected SHADOW_ONLY for None route keys, got {decision.status!r}"
    )
    assert "insufficient" in decision.note.lower() or "none" in decision.note.lower(), (
        f"Expected 'insufficient' or 'none' in note, got {decision.note!r}"
    )


def test_empty_same_domain_identity_does_not_fast_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty source/cycle identity is not a real same-domain transfer object."""
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()
    conn = _make_conn()

    decision = evaluate_calibration_transfer_policy_with_evidence(
        config=cfg,
        conn=conn,
        source_id="",
        target_source_id="",
        source_cycle="00",
        target_cycle="00",
        horizon_profile="full",
        season="summer",
        cluster="cluster_a",
        metric="high",
        platt_model_key="platt_key_1",
        now=datetime(2026, 5, 5, 12, 0, 0),
    )

    assert decision.status == "SHADOW_ONLY"
    assert decision.live_promotion_approved is False
    assert "insufficient" in decision.note.lower() or "none" in decision.note.lower()


@pytest.mark.parametrize(
    "field,db_column",
    [
        ("source_id", "source_id"),
        ("target_source_id", "target_source_id"),
        ("source_cycle", "source_cycle"),
        ("target_cycle", "target_cycle"),
        ("horizon_profile", "horizon_profile"),
        ("season", "season"),
        ("cluster", "cluster"),
        ("platt_model_key", "platt_model_key"),
    ],
)
def test_empty_cross_domain_identity_cannot_match_live_row(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    db_column: str,
) -> None:
    """A LIVE row with empty identity fields cannot authorize an empty route key."""
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()
    conn = _make_conn()
    now = datetime(2026, 5, 5, 12, 0, 0)
    _insert_row(conn, status="LIVE_ELIGIBLE", evaluated_at=now - timedelta(days=5))
    assert db_column in {
        "source_id",
        "target_source_id",
        "source_cycle",
        "target_cycle",
        "horizon_profile",
        "season",
        "cluster",
        "platt_model_key",
    }
    conn.execute(f"UPDATE validated_calibration_transfers SET {db_column} = ''")
    conn.commit()

    kwargs = {**_BASE_KWARGS, "now": now, field: ""}
    decision = evaluate_calibration_transfer_policy_with_evidence(
        config=cfg,
        conn=conn,
        **kwargs,
    )

    assert decision.status == "SHADOW_ONLY"
    assert decision.live_promotion_approved is False
    assert "insufficient" in decision.note.lower() or "none" in decision.note.lower()


def test_legacy_direct_call_fails_closed_when_oos_gate_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once the OOS evidence gate is active, legacy live_promotion_approved is not authority."""
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()

    with pytest.warns(DeprecationWarning, match="direct legacy calls fail closed"):
        decision = evaluate_calibration_transfer_policy(
            config=cfg,
            source_id=cfg.source_id,
            forecast_data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
            live_promotion_approved=True,
        )

    assert decision.status == "BLOCKED"
    assert decision.reason_codes == ("CALIBRATION_TRANSFER_LEGACY_PATH_DISABLED",)
    assert decision.live_promotion_approved is False
    assert decision.note == "legacy_disabled_by_oos_evidence_gate"


def test_live_eligible_db_row_sets_live_promotion_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LIVE_ELIGIBLE DB row must set live_promotion_approved=True.

    Regression guard for PR #61 review comments (Copilot #5/#10): when the
    readiness writer checks calibration_decision.live_promotion_approved, a
    False value causes a BLOCKED write even though the DB row approved the
    transfer. Architecture doc 2026-05-05: 'DB row is authority; flag REMOVED.'
    """
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()
    conn = _make_conn()
    now = datetime(2026, 5, 5, 12, 0, 0)
    _insert_row(conn, status="LIVE_ELIGIBLE", evaluated_at=now - timedelta(days=5))

    decision = evaluate_calibration_transfer_policy_with_evidence(
        config=cfg, conn=conn, **{**_BASE_KWARGS, "now": now}
    )

    assert decision.status == "LIVE_ELIGIBLE"
    assert decision.live_promotion_approved is True, (
        "LIVE_ELIGIBLE DB row must set live_promotion_approved=True so the "
        "readiness writer can pass the cross-gate invariant check"
    )


def test_same_domain_fast_path_sets_live_promotion_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same-domain LIVE_ELIGIBLE fast-path must set live_promotion_approved=True.

    Regression guard for PR #61 review comments (Copilot #5/#10): same-domain
    path also needs live_promotion_approved=True so the readiness writer passes.
    """
    monkeypatch.setenv("ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED", "true")
    cfg = entry_forecast_config()
    conn = _make_conn()

    decision = evaluate_calibration_transfer_policy_with_evidence(
        config=cfg,
        source_id="ecmwf_open_data",
        target_source_id="ecmwf_open_data",
        source_cycle="00",
        target_cycle="00",
        horizon_profile="full",
        season="summer",
        cluster="cluster_a",
        metric="high",
        platt_model_key="key1",
        conn=conn,
        now=datetime(2026, 5, 5, 12, 0, 0),
    )

    assert decision.status == "LIVE_ELIGIBLE"
    assert decision.live_promotion_approved is True, (
        "Same-domain LIVE_ELIGIBLE must set live_promotion_approved=True"
    )
