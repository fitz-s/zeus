# Created: 2026-05-05
# Last reused/audited: 2026-05-05
# Authority basis: architecture/calibration_transfer_oos_design_2026-05-05.md Phase X.2
"""Tests for scripts/evaluate_calibration_transfer_oos.py (Phase X.2)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.state.schema.v2_schema import apply_v2_schema
from scripts.evaluate_calibration_transfer_oos import (
    DEFAULT_BRIER_DIFF_THRESHOLD,
    DEFAULT_POLICY_ID,
    MIN_PAIRS,
    run_oos_evaluation,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_v2_schema(conn)
    return conn


_NOW = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)


def _insert_platt_model(
    conn: sqlite3.Connection,
    *,
    model_key: str = "m1",
    metric: str = "high",
    cluster: str = "cl_a",
    season: str = "summer",
    source_id: str = "tigge_mars",
    cycle: str = "00",
    horizon_profile: str = "full",
    param_A: float = 1.0,
    param_B: float = 0.0,
    param_C: float = 0.0,
    brier_insample: float = 0.20,
    is_active: int = 1,
) -> None:
    conn.execute(
        """
        INSERT INTO platt_models_v2 (
            model_key, temperature_metric, cluster, season, data_version,
            input_space, param_A, param_B, param_C,
            bootstrap_params_json, n_samples, brier_insample,
            fitted_at, is_active, authority,
            cycle, source_id, horizon_profile
        ) VALUES (
            ?, ?, ?, ?, 'v1',
            'raw_probability', ?, ?, ?,
            '[]', 100, ?,
            '2026-01-01T00:00:00', ?, 'VERIFIED',
            ?, ?, ?
        )
        """,
        (model_key, metric, cluster, season,
         param_A, param_B, param_C,
         brier_insample, is_active,
         cycle, source_id, horizon_profile),
    )
    conn.commit()


def _insert_pairs(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    cycle: str,
    season: str,
    cluster: str,
    metric: str,
    horizon_profile: str = "full",
    n: int,
    p_raw: float = 0.6,
    outcome: int = 1,
    start_pair_id: int = 0,
    target_date: str = "2026-03-01",
) -> None:
    """Insert n pairs with deterministic pair_ids starting at start_pair_id.

    Every 5th row (pair_id % 5 == 0) is held-out per the OOS convention.
    Uses real calibration_pairs_v2 column names: temperature_metric, target_date.
    """
    rows = [
        (
            start_pair_id + i,
            "test_city",                # city NOT NULL
            f"2020-01-01",              # target_date (same for all — uniqueness from lead_days below)
            metric,                     # temperature_metric
            "high_temp",                # observation_field
            "bucket_a",                 # range_label
            p_raw,
            outcome,
            float(start_pair_id + i),   # lead_days — globally unique per pair_id
            season,
            cluster,
            "2020-01-01T00:00:00",      # forecast_available_at NOT NULL
            "v1",                       # data_version NOT NULL
            source_id,
            cycle,
            horizon_profile,
        )
        for i in range(n)
    ]
    conn.executemany(
        """
        INSERT INTO calibration_pairs_v2 (
            pair_id,
            city, target_date, temperature_metric, observation_field, range_label,
            p_raw, outcome, lead_days, season, cluster,
            forecast_available_at, data_version,
            source_id, cycle, horizon_profile
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()


def _count_rows(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM validated_calibration_transfers"
    ).fetchone()[0]


def _fetch_row(conn: sqlite3.Connection, model_key: str) -> dict | None:
    cur = conn.execute(
        "SELECT * FROM validated_calibration_transfers WHERE platt_model_key = ?",
        (model_key,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


# ---------------------------------------------------------------------------
# test_same_domain_skipped
# ---------------------------------------------------------------------------

def test_same_domain_skipped() -> None:
    """Model (tigge_mars, 00z) with only (tigge_mars, 00z) pairs → no row written."""
    conn = _make_conn()
    _insert_platt_model(conn, model_key="m_same", source_id="tigge_mars", cycle="00")
    # Insert pairs in same domain
    _insert_pairs(
        conn, source_id="tigge_mars", cycle="00",
        season="summer", cluster="cl_a", metric="high",
        n=500,
    )

    summary = run_oos_evaluation(conn, now=_NOW)

    assert summary["same_domain_skipped"] >= 1
    assert summary["rows_written"] == 0
    assert _count_rows(conn) == 0


# ---------------------------------------------------------------------------
# test_cross_domain_writes_row
# ---------------------------------------------------------------------------

def test_cross_domain_writes_row() -> None:
    """Cross-domain target (ecmwf_open_data, 00z) → row written."""
    conn = _make_conn()
    _insert_platt_model(conn, model_key="m_cross", source_id="tigge_mars", cycle="00",
                        param_A=1.0, param_B=0.0, param_C=0.0, brier_insample=0.20)
    # 500 pairs in target domain; 100 are held-out (pair_id % 5 == 0 → every 5th)
    _insert_pairs(
        conn, source_id="ecmwf_open_data", cycle="00",
        season="summer", cluster="cl_a", metric="high",
        n=500, p_raw=0.7, outcome=1,
    )

    summary = run_oos_evaluation(conn, now=_NOW)

    assert summary["rows_written"] == 1
    assert summary["candidate_routes_evaluated"] == 1
    assert _count_rows(conn) == 1
    row = _fetch_row(conn, "m_cross")
    assert row is not None
    assert row["target_source_id"] == "ecmwf_open_data"
    assert row["status"] in ("LIVE_ELIGIBLE", "TRANSFER_UNSAFE", "INSUFFICIENT_SAMPLE")


# ---------------------------------------------------------------------------
# test_insufficient_sample_status
# ---------------------------------------------------------------------------

def test_insufficient_sample_status() -> None:
    """Fewer than MIN_PAIRS held-out target pairs → INSUFFICIENT_SAMPLE."""
    conn = _make_conn()
    _insert_platt_model(conn, model_key="m_insuff", source_id="tigge_mars", cycle="00",
                        brier_insample=0.20)
    # Insert fewer than MIN_PAIRS * 5 total (so held-out < MIN_PAIRS)
    # MIN_PAIRS=200 held-out means need 1000 total; insert 100 → 20 held-out
    _insert_pairs(
        conn, source_id="ecmwf_open_data", cycle="00",
        season="summer", cluster="cl_a", metric="high",
        n=100,
    )

    summary = run_oos_evaluation(conn, now=_NOW)

    assert summary["rows_written"] == 1
    assert summary["status_distribution"]["INSUFFICIENT_SAMPLE"] == 1
    row = _fetch_row(conn, "m_insuff")
    assert row["status"] == "INSUFFICIENT_SAMPLE"


# ---------------------------------------------------------------------------
# test_transfer_unsafe_status
# ---------------------------------------------------------------------------

def test_transfer_unsafe_status() -> None:
    """High OOS Brier (outcome always 0, p_raw=0.9) → TRANSFER_UNSAFE."""
    conn = _make_conn()
    # brier_insample near zero; target brier will be ~0.81 (p=0.9, outcome=0)
    _insert_platt_model(
        conn, model_key="m_unsafe", source_id="tigge_mars", cycle="00",
        param_A=1.0, param_B=0.0, param_C=0.0, brier_insample=0.001,
    )
    # 1000 pairs → 200 held-out; p_raw=0.9, outcome=0 → high Brier
    _insert_pairs(
        conn, source_id="ecmwf_open_data", cycle="00",
        season="summer", cluster="cl_a", metric="high",
        n=1000, p_raw=0.9, outcome=0,
    )

    summary = run_oos_evaluation(conn, now=_NOW)

    assert summary["rows_written"] == 1
    assert summary["status_distribution"]["TRANSFER_UNSAFE"] == 1
    row = _fetch_row(conn, "m_unsafe")
    assert row["status"] == "TRANSFER_UNSAFE"
    assert row["brier_diff"] > DEFAULT_BRIER_DIFF_THRESHOLD


# ---------------------------------------------------------------------------
# test_live_eligible_status
# ---------------------------------------------------------------------------

def test_live_eligible_status() -> None:
    """Tiny Brier diff → LIVE_ELIGIBLE."""
    conn = _make_conn()
    # A=1, B=0, C=0; p_raw=0.7, outcome=1 → p_cal=0.7, brier≈0.09
    # brier_insample also ~0.09 → diff ≈ 0 → LIVE_ELIGIBLE
    _insert_platt_model(
        conn, model_key="m_eligible", source_id="tigge_mars", cycle="00",
        param_A=1.0, param_B=0.0, param_C=0.0, brier_insample=0.09,
    )
    _insert_pairs(
        conn, source_id="ecmwf_open_data", cycle="00",
        season="summer", cluster="cl_a", metric="high",
        n=1000, p_raw=0.7, outcome=1,
    )

    summary = run_oos_evaluation(conn, now=_NOW)

    assert summary["rows_written"] == 1
    assert summary["status_distribution"]["LIVE_ELIGIBLE"] == 1
    row = _fetch_row(conn, "m_eligible")
    assert row["status"] == "LIVE_ELIGIBLE"
    assert row["brier_diff"] <= DEFAULT_BRIER_DIFF_THRESHOLD


# ---------------------------------------------------------------------------
# test_upsert_overwrites_existing_row
# ---------------------------------------------------------------------------

def test_upsert_overwrites_existing_row() -> None:
    """Re-running updates the existing row (n_pairs, status, evaluated_at)."""
    conn = _make_conn()
    _insert_platt_model(
        conn, model_key="m_upsert", source_id="tigge_mars", cycle="00",
        param_A=1.0, param_B=0.0, param_C=0.0, brier_insample=0.09,
    )
    _insert_pairs(
        conn, source_id="ecmwf_open_data", cycle="00",
        season="summer", cluster="cl_a", metric="high",
        n=1000, p_raw=0.7, outcome=1,
    )

    now1 = datetime(2026, 5, 5, 10, 0, 0, tzinfo=timezone.utc)
    now2 = datetime(2026, 5, 5, 14, 0, 0, tzinfo=timezone.utc)

    run_oos_evaluation(conn, now=now1)
    assert _count_rows(conn) == 1
    row1 = _fetch_row(conn, "m_upsert")

    run_oos_evaluation(conn, now=now2)
    assert _count_rows(conn) == 1  # still 1 row, not 2
    row2 = _fetch_row(conn, "m_upsert")

    assert row2["evaluated_at"] != row1["evaluated_at"]
    assert row2["evaluated_at"] == "2026-05-05T14:00:00Z"


# ---------------------------------------------------------------------------
# test_dry_run_no_writes
# ---------------------------------------------------------------------------

def test_dry_run_no_writes() -> None:
    """dry_run=True computes evidence but writes nothing to DB."""
    conn = _make_conn()
    _insert_platt_model(
        conn, model_key="m_dry", source_id="tigge_mars", cycle="00",
        brier_insample=0.20,
    )
    _insert_pairs(
        conn, source_id="ecmwf_open_data", cycle="00",
        season="summer", cluster="cl_a", metric="high",
        n=1000, p_raw=0.7, outcome=1,
    )

    summary = run_oos_evaluation(conn, dry_run=True, now=_NOW)

    assert summary["dry_run"] is True
    assert summary["rows_written"] == 1   # counted but not committed
    assert _count_rows(conn) == 0          # nothing persisted
