# Created: 2026-05-05
# Last reused/audited: 2026-05-08
# Lifecycle: created=2026-05-05; last_reviewed=2026-05-08; last_reused=2026-05-08
# Authority basis: architecture/calibration_transfer_oos_design_2026-05-05.md Phase X.2
# Purpose: Lock OOS calibration-transfer evidence eligibility and non-promotion behavior.
# Reuse: Run when calibration_pairs_v2 eligibility, validated_calibration_transfers, or OOS transfer policy evidence changes.
"""Tests for scripts/evaluate_calibration_transfer_oos.py (Phase X.2)."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from scripts import evaluate_calibration_transfer_oos as oos_script
from src.config import calibration_batch_rebuild_n_mc
from src.data.calibration_transfer_policy import (
    CANONICAL_CALIBRATION_PAIR_BIN_SOURCE,
    _rebuild_complete_sentinel_key_for_transfer_evidence,
)
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
                    "recorded_at": _NOW.isoformat(),
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
    brier_insample: float | None = 0.20,
    n_samples: int = 100,
    is_active: int = 1,
    authority: str = "VERIFIED",
    input_space: str = "raw_probability",
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
            '[]', ?, ?,
            '2026-01-01T00:00:00', ?, ?,
            ?, ?, ?
        )
        """,
        (model_key, metric, cluster, season,
         param_A, param_B, param_C,
         n_samples, brier_insample, is_active,
         authority, cycle, source_id, horizon_profile),
    )
    if input_space != "raw_probability":
        conn.execute(
            "UPDATE platt_models_v2 SET input_space = ? WHERE model_key = ?",
            (input_space, model_key),
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
    training_allowed: int = 1,
    authority: str = "VERIFIED",
    causality_status: str = "OK",
    lead_days: float | None = None,
    rebuild_status: str | None = "complete",
) -> None:
    """Insert n pairs with deterministic pair_ids starting at start_pair_id.

    The latest 20% of decision groups are held out chronologically for OOS.
    Uses real calibration_pairs_v2 column names: temperature_metric, target_date.
    """
    base_target_date = date.fromisoformat(target_date)
    rows = [
        (
            start_pair_id + i,
            "test_city",                # city NOT NULL
            (base_target_date + timedelta(days=start_pair_id + i)).isoformat(),
            metric,                     # temperature_metric
            "high_temp",                # observation_field
            "bucket_a",                 # range_label
            p_raw,
            outcome,
            1.0 + float((start_pair_id + i) % 7) if lead_days is None else lead_days,
            season,
            cluster,
            "2020-01-01T00:00:00",      # forecast_available_at NOT NULL
            f"dg_{source_id}_{cycle}_{start_pair_id + i}",
            "v1",                       # data_version NOT NULL
            source_id,
            cycle,
            horizon_profile,
            training_allowed,
            authority,
            causality_status,
        )
        for i in range(n)
    ]
    conn.executemany(
        """
        INSERT INTO calibration_pairs_v2 (
            pair_id,
            city, target_date, temperature_metric, observation_field, range_label,
            p_raw, outcome, lead_days, season, cluster,
            forecast_available_at, decision_group_id, data_version,
            source_id, cycle, horizon_profile,
            training_allowed, authority, causality_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    if rebuild_status is not None:
        _write_rebuild_sentinel(
            conn,
            metric=metric,
            source_id=source_id,
            cycle=cycle,
            horizon_profile=horizon_profile,
            status=rebuild_status,
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
    # 500 pairs in target domain; 100 are held out as the latest time block.
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
    # MIN_PAIRS=200 held-out means need 1000 total; insert 100 -> 20 held-out.
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


def test_live_write_skips_transfer_when_rebuild_sentinel_incomplete() -> None:
    """OOS writer cannot stamp evidence from a partially rebuilt target cohort."""
    conn = _make_conn()
    _insert_platt_model(
        conn, model_key="m_incomplete_rebuild", source_id="tigge_mars", cycle="00",
        param_A=1.0, param_B=0.0, param_C=0.0, brier_insample=0.09,
    )
    _insert_pairs(
        conn, source_id="ecmwf_open_data", cycle="00",
        season="summer", cluster="cl_a", metric="high",
        n=1000, p_raw=0.7, outcome=1, rebuild_status="in_progress",
    )

    summary = run_oos_evaluation(conn, now=_NOW, dry_run=False)

    assert summary["rows_written"] == 0
    assert summary["candidate_routes_evaluated"] == 0
    assert summary["rebuild_incomplete_skipped"] == 1
    assert _count_rows(conn) == 0


def test_time_blocked_holdout_uses_latest_decision_groups_not_pair_id_modulo() -> None:
    """OOS evidence uses the latest time block, not row-id modulo sampling."""
    conn = _make_conn()
    _insert_platt_model(
        conn, model_key="m_time_block", source_id="tigge_mars", cycle="00",
        param_A=1.0, param_B=0.0, param_C=0.0, brier_insample=0.09,
    )
    _insert_pairs(
        conn, source_id="ecmwf_open_data", cycle="00",
        season="summer", cluster="cl_a", metric="high",
        start_pair_id=1, n=800, p_raw=0.9, outcome=0,
    )
    _insert_pairs(
        conn, source_id="ecmwf_open_data", cycle="00",
        season="summer", cluster="cl_a", metric="high",
        start_pair_id=801, n=200, p_raw=0.7, outcome=1,
    )

    summary = run_oos_evaluation(conn, now=_NOW)

    assert summary["rows_written"] == 1
    assert summary["status_distribution"]["LIVE_ELIGIBLE"] == 1
    row = _fetch_row(conn, "m_time_block")
    assert row["status"] == "LIVE_ELIGIBLE"
    assert row["n_pairs"] == MIN_PAIRS
    assert date.fromisoformat(row["evidence_window_start"]) >= date(2028, 5, 10)


def test_missing_decision_group_cannot_write_transfer_evidence() -> None:
    """Rows without decision_group_id cannot prove chronological OOS basis."""
    conn = _make_conn()
    _insert_platt_model(conn, model_key="m_no_group", source_id="tigge_mars", cycle="00")
    _insert_pairs(
        conn, source_id="ecmwf_open_data", cycle="00",
        season="summer", cluster="cl_a", metric="high",
        n=1000, p_raw=0.7, outcome=1,
    )
    conn.execute("UPDATE calibration_pairs_v2 SET decision_group_id = NULL")

    summary = run_oos_evaluation(conn, now=_NOW)

    assert summary["rows_written"] == 0
    assert summary["candidate_routes_evaluated"] == 0


@pytest.mark.parametrize(
    "field,overrides",
    [
        ("training_allowed", {"training_allowed": 0}),
        ("authority", {"authority": "UNVERIFIED"}),
        ("causality_status", {"causality_status": "RUNTIME_ONLY_FALLBACK"}),
    ],
)
def test_non_eligible_target_pairs_do_not_write_transfer_evidence(field: str, overrides: dict) -> None:
    """Target evidence must be training-eligible before it can authorize transfer."""
    conn = _make_conn()
    _insert_platt_model(
        conn, model_key=f"m_ineligible_{field}", source_id="tigge_mars", cycle="00",
        param_A=1.0, param_B=0.0, param_C=0.0, brier_insample=0.09,
    )
    _insert_pairs(
        conn, source_id="ecmwf_open_data", cycle="00",
        season="summer", cluster="cl_a", metric="high",
        n=1000, p_raw=0.7, outcome=1, **overrides,
    )

    summary = run_oos_evaluation(conn, now=_NOW)

    assert summary["rows_written"] == 0
    assert summary["candidate_routes_evaluated"] == 0
    assert _count_rows(conn) == 0


@pytest.mark.parametrize("p_raw", [0.0, 1.0, -0.1, 2.0, float("inf")])
def test_invalid_probability_target_pairs_do_not_write_transfer_evidence(p_raw: float) -> None:
    """Target probabilities must already be valid evidence; Platt clamp is not authority."""
    conn = _make_conn()
    _insert_platt_model(
        conn, model_key=f"m_invalid_praw_{p_raw}", source_id="tigge_mars", cycle="00",
        param_A=1.0, param_B=0.0, param_C=0.0, brier_insample=0.09,
    )
    _insert_pairs(
        conn, source_id="ecmwf_open_data", cycle="00",
        season="summer", cluster="cl_a", metric="high",
        n=1000, p_raw=p_raw, outcome=1,
    )

    summary = run_oos_evaluation(conn, now=_NOW)

    assert summary["rows_written"] == 0
    assert summary["candidate_routes_evaluated"] == 0
    assert _count_rows(conn) == 0


@pytest.mark.parametrize("lead_days", [-0.1, 0.0, 8.0, 999999.0, float("inf")])
def test_invalid_lead_days_target_pairs_do_not_write_transfer_evidence(lead_days: float) -> None:
    """Target lead_days is the OOS time basis; invalid values cannot authorize evidence."""
    conn = _make_conn()
    _insert_platt_model(
        conn, model_key=f"m_invalid_lead_{lead_days}", source_id="tigge_mars", cycle="00",
        param_A=1.0, param_B=0.0, param_C=0.0, brier_insample=0.09,
    )
    _insert_pairs(
        conn, source_id="ecmwf_open_data", cycle="00",
        season="summer", cluster="cl_a", metric="high",
        n=1, p_raw=0.7, outcome=1, lead_days=lead_days,
    )

    summary = run_oos_evaluation(conn, now=_NOW)

    assert summary["rows_written"] == 0
    assert summary["candidate_routes_evaluated"] == 0
    assert _count_rows(conn) == 0


@pytest.mark.parametrize(
    "field,overrides",
    [
        ("source_id", {"source_id": ""}),
        ("cycle", {"cycle": ""}),
        ("season", {"season": ""}),
        ("cluster", {"cluster": ""}),
        ("horizon_profile", {"horizon_profile": ""}),
    ],
)
def test_empty_target_identity_pairs_do_not_write_transfer_evidence(
    field: str,
    overrides: dict,
) -> None:
    """Target evidence rows require non-empty route identity before OOS scoring."""
    conn = _make_conn()
    _insert_platt_model(
        conn, model_key=f"m_empty_target_{field}", source_id="tigge_mars", cycle="00",
        param_A=1.0, param_B=0.0, param_C=0.0, brier_insample=0.09,
    )
    pair_kwargs = dict(
        source_id="ecmwf_open_data", cycle="00",
        season="summer", cluster="cl_a", metric="high",
        n=1000, p_raw=0.7, outcome=1,
    )
    pair_kwargs.update(overrides)
    _insert_pairs(conn, **pair_kwargs)

    summary = run_oos_evaluation(conn, now=_NOW)

    assert summary["rows_written"] == 0
    assert summary["candidate_routes_evaluated"] == 0
    assert _count_rows(conn) == 0


@pytest.mark.parametrize("authority", ["UNVERIFIED", "QUARANTINED"])
def test_non_verified_source_platt_models_do_not_write_transfer_evidence(authority: str) -> None:
    """Source Platt models must be verified before they can generate OOS evidence."""
    conn = _make_conn()
    _insert_platt_model(
        conn, model_key=f"m_source_{authority}", source_id="tigge_mars", cycle="00",
        param_A=1.0, param_B=0.0, param_C=0.0, brier_insample=0.09,
        authority=authority,
    )
    _insert_pairs(
        conn, source_id="ecmwf_open_data", cycle="00",
        season="summer", cluster="cl_a", metric="high",
        n=1000, p_raw=0.7, outcome=1,
    )

    summary = run_oos_evaluation(conn, now=_NOW)

    assert summary["active_platt_models_iterated"] == 0
    assert summary["rows_written"] == 0
    assert _count_rows(conn) == 0


@pytest.mark.parametrize(
    "field,overrides",
    [
        ("model_key", {"model_key": ""}),
        ("source_id", {"source_id": ""}),
        ("cycle", {"cycle": ""}),
        ("season", {"season": ""}),
        ("cluster", {"cluster": ""}),
        ("horizon_profile", {"horizon_profile": ""}),
    ],
)
def test_empty_source_platt_identity_does_not_write_transfer_evidence(
    field: str,
    overrides: dict,
) -> None:
    """Source Platt rows need non-empty route identity before becoming scoring authority."""
    conn = _make_conn()
    _insert_platt_model(
        conn, authority="VERIFIED", input_space="raw_probability", **overrides,
    )
    _insert_pairs(
        conn, source_id="ecmwf_open_data", cycle="00",
        season="summer", cluster="cl_a", metric="high",
        n=1000, p_raw=0.7, outcome=1,
    )

    summary = run_oos_evaluation(conn, now=_NOW)

    assert summary["active_platt_models_iterated"] == 0
    assert summary["rows_written"] == 0
    assert _count_rows(conn) == 0


def test_immature_source_platt_model_does_not_write_transfer_evidence() -> None:
    """A VERIFIED source Platt row still needs mature sample authority for transfer evidence."""
    conn = _make_conn()
    _insert_platt_model(
        conn, model_key="m_immature_source", source_id="tigge_mars", cycle="00",
        authority="VERIFIED", input_space="raw_probability", n_samples=1,
        param_A=1.0, param_B=0.0, param_C=0.0, brier_insample=0.09,
    )
    _insert_pairs(
        conn, source_id="ecmwf_open_data", cycle="00",
        season="summer", cluster="cl_a", metric="high",
        n=1000, p_raw=0.7, outcome=1,
    )

    summary = run_oos_evaluation(conn, now=_NOW)

    assert summary["active_platt_models_iterated"] == 0
    assert summary["rows_written"] == 0
    assert _count_rows(conn) == 0


@pytest.mark.parametrize(
    "field,overrides",
    [
        ("param_A", {"param_A": float("inf")}),
        ("param_B", {"param_B": float("inf")}),
        ("param_C", {"param_C": float("inf")}),
        ("brier_insample_null", {"brier_insample": None}),
        ("brier_insample_inf", {"brier_insample": float("inf")}),
        ("brier_insample_gt_one", {"brier_insample": 2.0}),
    ],
)
def test_invalid_source_platt_economics_do_not_write_transfer_evidence(
    field: str,
    overrides: dict,
) -> None:
    """Source Platt rows need finite parameters and Brier before scoring target economics."""
    conn = _make_conn()
    _insert_platt_model(
        conn, model_key=f"m_invalid_source_{field}", source_id="tigge_mars", cycle="00",
        authority="VERIFIED", input_space="raw_probability", **overrides,
    )
    _insert_pairs(
        conn, source_id="ecmwf_open_data", cycle="00",
        season="summer", cluster="cl_a", metric="high",
        n=1000, p_raw=0.7, outcome=1,
    )

    summary = run_oos_evaluation(conn, now=_NOW)

    assert summary["active_platt_models_iterated"] == 0
    assert summary["rows_written"] == 0
    assert _count_rows(conn) == 0


@pytest.mark.parametrize("threshold", [-0.1, 2.0, "nan", "inf"])
def test_invalid_brier_threshold_config_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    threshold: object,
) -> None:
    """Producer must not stamp LIVE_ELIGIBLE/TRANSFER_UNSAFE from invalid policy economics."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(
        json.dumps(
            {"calibration_transfer_brier_diff_threshold": {DEFAULT_POLICY_ID: threshold}}
        )
    )
    monkeypatch.setattr(oos_script, "ZEUS_ROOT", tmp_path)

    conn = _make_conn()
    _insert_platt_model(
        conn, model_key=f"m_invalid_threshold_{threshold}", source_id="tigge_mars", cycle="00",
        param_A=1.0, param_B=0.0, param_C=0.0, brier_insample=0.09,
    )
    _insert_pairs(
        conn, source_id="ecmwf_open_data", cycle="00",
        season="summer", cluster="cl_a", metric="high",
        n=1000, p_raw=0.7, outcome=1,
    )

    with pytest.raises(ValueError, match="calibration_transfer_brier_diff_threshold"):
        run_oos_evaluation(conn, now=_NOW)
    assert _count_rows(conn) == 0


def test_unsupported_platt_input_space_does_not_write_transfer_evidence() -> None:
    """OOS scoring is raw-probability only until a typed transform is added."""
    conn = _make_conn()
    _insert_platt_model(
        conn, model_key="m_width_space", source_id="tigge_mars", cycle="00",
        param_A=1.0, param_B=0.0, param_C=0.0, brier_insample=0.09,
        input_space="width_normalized_density",
    )
    _insert_pairs(
        conn, source_id="ecmwf_open_data", cycle="00",
        season="summer", cluster="cl_a", metric="high",
        n=1000, p_raw=0.7, outcome=1,
    )

    summary = run_oos_evaluation(conn, now=_NOW)

    assert summary["active_platt_models_iterated"] == 0
    assert summary["rows_written"] == 0
    assert _count_rows(conn) == 0


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
