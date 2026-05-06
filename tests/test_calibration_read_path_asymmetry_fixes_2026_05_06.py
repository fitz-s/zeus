# Created: 2026-05-06
# Last reused/audited: 2026-05-06
# Authority basis: /Users/leofitz/.claude/plans/golden-knitting-wand.md
"""Regression tests for 6 READ-path asymmetry fixes (cycle-stratified Platt).

Covers:
  P0 #1 — season-pool fallback passes cycle/source_id/horizon_profile
  P0 #2 — _resolve_pin_for_bucket key includes cycle axis
  P1 #3 — _write_entry_readiness_for_candidate derives route keys
  P2 #5 — derive_phase2_keys_from_ens_result uses parse_cycle_from_issue_time
  P2 #6 — deactivate_model_v2 uses 9-tuple WHERE (matches legacy-keyed rows)

P1 #4 (_bucket_model_key threading) tested implicitly via load→calibrator round-trip.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone, date
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.forecast_calibration_domain import (
    derive_phase2_keys_from_ens_result,
    parse_cycle_from_issue_time,
)
from src.calibration import manager as mgr_module
from src.calibration.manager import _resolve_pin_for_bucket, get_calibrator
from src.calibration.store import (
    deactivate_model_v2,
    load_platt_model_v2,
    save_platt_model_v2,
)
from src.config import City
from src.state.db import init_schema
from src.state.schema.v2_schema import apply_v2_schema
from src.types.metric_identity import HIGH_LOCALDAY_MAX

# Canonical data_version for HIGH + tigge_mars source
_HIGH_TIGGE_DATA_VERSION = HIGH_LOCALDAY_MAX.data_version


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_v2_schema(conn)
    return conn


def _city(cluster: str = "US-Northeast") -> City:
    return City(
        name="TestCity",
        lat=40.7,
        lon=-74.0,
        timezone="America/New_York",
        settlement_unit="F",
        cluster=cluster,
        wu_station="KTST",
    )


def _save_v2(conn, cluster: str, season: str, cycle: str = "00",
             source_id: str = "tigge_mars", n_samples: int = 50) -> None:
    save_platt_model_v2(
        conn,
        metric_identity=HIGH_LOCALDAY_MAX,
        cluster=cluster,
        season=season,
        data_version=_HIGH_TIGGE_DATA_VERSION,
        param_A=1.5,
        param_B=0.3,
        param_C=0.0,
        bootstrap_params=[(1.5, 0.3, 0.0)] * 20,
        n_samples=n_samples,
        input_space="width_normalized_density",
        cycle=cycle,
        source_id=source_id,
        horizon_profile="full",
    )


# ---------------------------------------------------------------------------
# P0 #1 — Season-pool fallback passes cycle/source_id/horizon_profile
# ---------------------------------------------------------------------------

def test_season_pool_fallback_uses_correct_cycle():
    """P0 #1: fallback cluster load uses caller's cycle, not store default (00).

    Insert a cycle='12' row in cluster='NYC' (a real calibration_clusters() member).
    Request get_calibrator for a city whose own cluster='London' has no row.
    The fallback loop iterates real clusters; 'NYC' is tried and found.
    Assert the returned calibrator has _bucket_cycle='12', not '00'.
    """
    conn = _make_conn()
    # NYC is in calibration_clusters(); London is also a real cluster.
    _save_v2(conn, cluster="NYC", season="JJA", cycle="12", n_samples=50)

    city = _city(cluster="London")
    # 2026-07-15 → JJA season
    cal, level = get_calibrator(
        conn,
        city,
        "2026-07-15",
        "high",
        cycle="12",
        source_id="tigge_mars",
        horizon_profile="full",
    )
    assert cal is not None, "Expected calibrator from season-pool fallback"
    assert getattr(cal, "_bucket_cycle", None) == "12", (
        f"Expected _bucket_cycle='12' from fallback, got {cal._bucket_cycle!r}"
    )


def test_season_pool_fallback_does_not_return_wrong_cycle():
    """P0 #1: when only cycle='00' exists in fallback cluster and caller asks
    cycle='12', no calibrator is returned (not silently miscalibrated)."""
    conn = _make_conn()
    _save_v2(conn, cluster="NYC", season="JJA", cycle="00", n_samples=50)

    city = _city(cluster="London")
    cal, level = get_calibrator(
        conn,
        city,
        "2026-07-15",
        "high",
        cycle="12",
        source_id="tigge_mars",
        horizon_profile="full",
    )
    assert cal is None, (
        "Should not return a cycle='00' calibrator when caller requests cycle='12'"
    )


# ---------------------------------------------------------------------------
# P0 #2 — _resolve_pin_for_bucket key includes cycle axis
# ---------------------------------------------------------------------------

def test_resolve_pin_for_bucket_cycle_in_key(monkeypatch, tmp_path):
    """P0 #2: pin keys now include cycle; '00' and '12' resolve independently."""
    fake_root = tmp_path / "fake_root"
    (fake_root / "src" / "calibration").mkdir(parents=True)
    (fake_root / "config").mkdir()
    (fake_root / "config" / "settings.json").write_text(json.dumps({
        "calibration": {
            "pin": {
                "frozen_as_of": None,
                "model_keys": {
                    "high:NYC:DJF:00": "key_00z",
                    "high:NYC:DJF:12": "key_12z",
                }
            }
        }
    }))
    mgr_module._PIN_CONFIG_CACHE = None
    monkeypatch.setattr(
        mgr_module, "__file__",
        str(fake_root / "src" / "calibration" / "manager.py")
    )

    _, k00 = _resolve_pin_for_bucket("high", "NYC", "DJF", "00")
    _, k12 = _resolve_pin_for_bucket("high", "NYC", "DJF", "12")
    assert k00 == "key_00z", f"Expected key_00z, got {k00!r}"
    assert k12 == "key_12z", f"Expected key_12z, got {k12!r}"
    # Pinning 00z does not auto-pin 12z
    assert k00 != k12

    mgr_module._PIN_CONFIG_CACHE = None


# ---------------------------------------------------------------------------
# P1 #3 — _write_entry_readiness_for_candidate derives route keys
# ---------------------------------------------------------------------------

def test_write_entry_readiness_derives_cycle_from_decision_time():
    """P1 #3: decision_time at 03:00Z → cycle='12' (yesterday's 12z).
    evaluate_calibration_transfer_policy_with_evidence called with
    source_cycle='12', target_cycle='12', season derived, cluster=city.cluster.
    """
    from src.engine.evaluator import _write_entry_readiness_for_candidate
    from src.types.metric_identity import HIGH_LOCALDAY_MAX

    city = _city(cluster="US-Northeast")
    decision_time = datetime(2026, 6, 15, 3, 0, 0, tzinfo=timezone.utc)
    target_local_date = date(2026, 6, 15)

    mock_cfg = MagicMock()
    mock_cfg.source_id = "tigge_mars"

    with patch(
        "src.engine.evaluator.evaluate_calibration_transfer_policy_with_evidence"
    ) as mock_policy, \
    patch("src.engine.evaluator.evaluate_entry_forecast_rollout_gate"), \
    patch("src.engine.evaluator.read_promotion_evidence", return_value=None), \
    patch("src.engine.evaluator.write_entry_readiness"):
        mock_policy.return_value = MagicMock()
        _write_entry_readiness_for_candidate(
            MagicMock(),  # conn
            cfg=mock_cfg,
            city=city,
            target_local_date=target_local_date,
            temperature_metric=HIGH_LOCALDAY_MAX,
            market_family="high_test",
            condition_id="cid_test",
            decision_time=decision_time,
        )

    assert mock_policy.called
    kwargs = mock_policy.call_args.kwargs
    assert kwargs["source_cycle"] == "12", f"Expected '12', got {kwargs['source_cycle']!r}"
    assert kwargs["target_cycle"] == "12", f"Expected '12', got {kwargs['target_cycle']!r}"
    assert kwargs["cluster"] == "US-Northeast"
    # JJA for June in NH
    assert kwargs["season"] == "JJA", f"Expected 'JJA', got {kwargs['season']!r}"
    assert kwargs["horizon_profile"] == "full"


def test_write_entry_readiness_midday_gets_cycle_00():
    """P1 #3: decision_time at 12:00Z → cycle='00'."""
    from src.engine.evaluator import _write_entry_readiness_for_candidate
    from src.types.metric_identity import HIGH_LOCALDAY_MAX

    city = _city(cluster="US-Northeast")
    decision_time = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

    mock_cfg = MagicMock()
    mock_cfg.source_id = "tigge_mars"

    with patch(
        "src.engine.evaluator.evaluate_calibration_transfer_policy_with_evidence"
    ) as mock_policy, \
    patch("src.engine.evaluator.evaluate_entry_forecast_rollout_gate"), \
    patch("src.engine.evaluator.read_promotion_evidence", return_value=None), \
    patch("src.engine.evaluator.write_entry_readiness"):
        mock_policy.return_value = MagicMock()
        _write_entry_readiness_for_candidate(
            MagicMock(),
            cfg=mock_cfg,
            city=city,
            target_local_date=date(2026, 6, 15),
            temperature_metric=HIGH_LOCALDAY_MAX,
            market_family="high_test",
            condition_id="cid_test",
            decision_time=decision_time,
        )

    kwargs = mock_policy.call_args.kwargs
    assert kwargs["source_cycle"] == "00"


# ---------------------------------------------------------------------------
# P1 #4 — _bucket_model_key threaded from load → calibrator
# ---------------------------------------------------------------------------

def test_model_key_threaded_onto_calibrator():
    """P1 #4: calibrator built from load_platt_model_v2 carries _bucket_model_key."""
    conn = _make_conn()
    _save_v2(conn, cluster="Test-Cluster", season="DJF", cycle="12")

    model_data = load_platt_model_v2(
        conn,
        temperature_metric="high",
        cluster="Test-Cluster",
        season="DJF",
        data_version=_HIGH_TIGGE_DATA_VERSION,
        input_space="width_normalized_density",
        cycle="12",
        source_id="tigge_mars",
        horizon_profile="full",
    )
    assert model_data is not None
    assert model_data.get("model_key") is not None, "model_key must be returned by load_platt_model_v2"

    from src.calibration.manager import _model_data_to_calibrator
    cal = _model_data_to_calibrator(model_data)
    assert hasattr(cal, "_bucket_model_key"), "calibrator must have _bucket_model_key"
    assert cal._bucket_model_key == model_data["model_key"]


# ---------------------------------------------------------------------------
# P2 #5 — derive_phase2_keys_from_ens_result uses robust regex parser
# ---------------------------------------------------------------------------

def test_derive_phase2_keys_standard_iso():
    """P2 #5: standard ISO-T string extracts cycle correctly."""
    cycle, _, _ = derive_phase2_keys_from_ens_result({"issue_time": "2026-05-06T12:00:00Z"})
    assert cycle == "12"


def test_derive_phase2_keys_single_digit_hour_returns_none():
    """P2 #5: '2026-05-06T9:00:00Z' has no leading zero — regex requires HH (2 digits).
    parse_cycle_from_issue_time returns None; derive returns None cycle.
    """
    cycle, _, _ = derive_phase2_keys_from_ens_result({"issue_time": "2026-05-06T9:00:00Z"})
    # _ISO_HHMM_RE requires 2-digit hour; single digit won't match
    assert cycle is None, f"Expected None for single-digit hour, got {cycle!r}"


def test_derive_phase2_keys_space_separator_returns_none():
    """P2 #5: space-separated datetime (no 'T') → regex won't match → None."""
    cycle, _, _ = derive_phase2_keys_from_ens_result({"issue_time": "2026-05-06 12:00:00"})
    assert cycle is None, f"Expected None for space-separator, got {cycle!r}"


def test_derive_phase2_keys_datetime_object():
    """P2 #5: datetime object uses .hour path (unchanged)."""
    dt = datetime(2026, 5, 6, 12, 0, 0)
    cycle, _, _ = derive_phase2_keys_from_ens_result({"issue_time": dt})
    assert cycle == "12"


# ---------------------------------------------------------------------------
# P2 #6 — deactivate_model_v2 uses 9-tuple WHERE (matches legacy-keyed rows)
# ---------------------------------------------------------------------------

def test_deactivate_model_v2_matches_legacy_keyed_row():
    """P2 #6: row with OLD-format model_key (no cycle in string) is deleted by
    9-tuple WHERE matching on column values, not reconstructed key string.
    """
    conn = _make_conn()
    # Insert a row with a LEGACY-format model_key (pre-Phase-2, no cycle/source/horizon).
    legacy_key = f"high:Test-Cluster:DJF:{_HIGH_TIGGE_DATA_VERSION}:width_normalized_density"
    conn.execute(
        """
        INSERT INTO platt_models_v2
        (model_key, temperature_metric, cluster, season, data_version,
         input_space, param_A, param_B, param_C, bootstrap_params_json,
         n_samples, brier_insample, fitted_at, is_active, authority,
         cycle, source_id, horizon_profile)
        VALUES (?, 'high', 'Test-Cluster', 'DJF', ?,
                'width_normalized_density', 1.5, 0.3, 0.0, '[]',
                50, NULL, '2026-01-01T00:00:00', 1, 'VERIFIED',
                '00', 'tigge_mars', 'full')
        """,
        (legacy_key, _HIGH_TIGGE_DATA_VERSION),
    )
    conn.commit()

    count = deactivate_model_v2(
        conn,
        metric_identity=HIGH_LOCALDAY_MAX,
        cluster="Test-Cluster",
        season="DJF",
        data_version=_HIGH_TIGGE_DATA_VERSION,
        input_space="width_normalized_density",
        cycle="00",
        source_id="tigge_mars",
        horizon_profile="full",
    )
    assert count == 1, f"Expected 1 row deleted, got {count}"
    row = conn.execute(
        "SELECT COUNT(*) FROM platt_models_v2 WHERE is_active = 1"
    ).fetchone()[0]
    assert row == 0, "Row should be deleted"


def test_deactivate_model_v2_new_format_key_also_works():
    """P2 #6: new-format model_key row is also deleted by the 9-tuple WHERE."""
    conn = _make_conn()
    _save_v2(conn, cluster="Test-Cluster", season="DJF", cycle="00")

    count = deactivate_model_v2(
        conn,
        metric_identity=HIGH_LOCALDAY_MAX,
        cluster="Test-Cluster",
        season="DJF",
        data_version=_HIGH_TIGGE_DATA_VERSION,
        input_space="width_normalized_density",
        cycle="00",
        source_id="tigge_mars",
        horizon_profile="full",
    )
    assert count == 1, f"Expected 1 row deleted, got {count}"
