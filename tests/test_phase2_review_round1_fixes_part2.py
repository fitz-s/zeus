# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: PR #55 review round 1 — Copilot reviews #2/#3,
#                  Codex P1 review #6
"""Relationship tests for round-1 PR review fixes (part 2).

Covers:
  * Copilot #2 — store loader fail-closed on missing stratification keys
    when data_version is OpenData; legacy defaults applied for TIGGE.
  * Copilot #3 — ensemble_client refuses ecmwf_open_data for ALL roles
    when ingest_class is None (not just entry_primary).
  * Codex P1 #6 — load_platt_model_v2 returns the loaded bucket's
    identity, _model_data_to_calibrator attaches it to the calibrator
    object, and the evaluator's transfer gate constructs
    calibrator_domain from the actual bucket (not hardcoded TIGGE).

Round-1 part-1 (#1, #4, #5, #7) lives in
test_phase2_review_round1_fixes.py.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest


# ---- Copilot review #2 — store loader missing-stratification policy ------


def _make_v2_table(conn: sqlite3.Connection) -> None:
    """Build a Phase-2-stratified platt_models_v2 table."""
    conn.execute(
        """
        CREATE TABLE platt_models_v2 (
            model_key TEXT PRIMARY KEY,
            temperature_metric TEXT NOT NULL,
            cluster TEXT NOT NULL,
            season TEXT NOT NULL,
            data_version TEXT NOT NULL,
            input_space TEXT NOT NULL DEFAULT 'width_normalized_density',
            param_A REAL, param_B REAL, param_C REAL,
            bootstrap_params_json TEXT NOT NULL DEFAULT '[]',
            n_samples INTEGER, brier_insample REAL,
            fitted_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            authority TEXT NOT NULL DEFAULT 'VERIFIED',
            recorded_at TEXT,
            cycle TEXT NOT NULL DEFAULT '00',
            source_id TEXT NOT NULL DEFAULT 'tigge_mars',
            horizon_profile TEXT NOT NULL DEFAULT 'full'
        )
        """
    )


def test_load_platt_model_v2_raises_on_opendata_missing_keys():
    """OpenData data_version with no cycle/source_id/horizon_profile must
    fail-closed (ValueError) — Copilot #2.  Pre-fix: silently picked
    schema-default 00z TIGGE bucket for an OpenData forecast.
    """
    from src.calibration.store import load_platt_model_v2

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _make_v2_table(conn)
    with pytest.raises(ValueError, match="OpenData data_version"):
        load_platt_model_v2(
            conn,
            temperature_metric="high",
            cluster="London",
            season="winter",
            data_version="ecmwf_opendata_mx2t6_local_calendar_day_max_v1",
            # cycle/source_id/horizon_profile all None — not allowed for OpenData
        )


def test_load_platt_model_v2_tigge_missing_keys_uses_legacy_defaults():
    """TIGGE data_version with None stratification keys defaults to legacy
    schema (00/tigge_mars/full) and continues working — backward compat.
    """
    from src.calibration.store import load_platt_model_v2

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _make_v2_table(conn)
    conn.execute(
        """
        INSERT INTO platt_models_v2
        (model_key, temperature_metric, cluster, season, data_version,
         input_space, param_A, param_B, param_C, bootstrap_params_json,
         n_samples, brier_insample, fitted_at, is_active, authority,
         cycle, source_id, horizon_profile)
        VALUES ('mk-1','high','London','winter',
                'tigge_mx2t6_local_calendar_day_max_v1',
                'width_normalized_density', 1.0, 0.0, 0.0, '[]',
                100, 0.2, '2026-05-04', 1, 'VERIFIED',
                '00','tigge_mars','full')
        """
    )
    result = load_platt_model_v2(
        conn,
        temperature_metric="high",
        cluster="London",
        season="winter",
        data_version="tigge_mx2t6_local_calendar_day_max_v1",
        # No cycle/source_id/horizon_profile — should default to 00/tigge_mars/full
    )
    assert result is not None, (
        "Copilot #2 regression: TIGGE missing-keys should fall back to legacy "
        "defaults, not silently fail"
    )
    assert result["bucket_cycle"] == "00"
    assert result["bucket_source_id"] == "tigge_mars"


def test_load_platt_model_v2_returns_bucket_identity():
    """load_platt_model_v2 must include bucket_cycle/source_id/horizon_profile
    /data_version in the returned dict — Codex P1 #6.
    """
    from src.calibration.store import load_platt_model_v2

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _make_v2_table(conn)
    conn.execute(
        """
        INSERT INTO platt_models_v2
        (model_key, temperature_metric, cluster, season, data_version,
         input_space, param_A, param_B, param_C, bootstrap_params_json,
         n_samples, brier_insample, fitted_at, is_active, authority,
         cycle, source_id, horizon_profile)
        VALUES ('mk-2','high','London','winter',
                'ecmwf_opendata_mx2t6_local_calendar_day_max_v1',
                'width_normalized_density', 1.0, 0.0, 0.0, '[]',
                100, 0.2, '2026-05-04', 1, 'VERIFIED',
                '12','ecmwf_open_data','full')
        """
    )
    result = load_platt_model_v2(
        conn,
        temperature_metric="high",
        cluster="London",
        season="winter",
        data_version="ecmwf_opendata_mx2t6_local_calendar_day_max_v1",
        cycle="12",
        source_id="ecmwf_open_data",
        horizon_profile="full",
    )
    assert result is not None
    assert result["bucket_cycle"] == "12"
    assert result["bucket_source_id"] == "ecmwf_open_data"
    assert result["bucket_horizon_profile"] == "full"
    assert result["bucket_data_version"] == "ecmwf_opendata_mx2t6_local_calendar_day_max_v1"


# ---- Copilot review #3 — ensemble_client guard for all roles -------------


def test_ensemble_client_guard_fires_for_all_roles_when_ingest_class_none():
    """The original guard only fired for role='entry_primary'; all other
    roles (crosscheck, diagnostic, monitor) silently routed Open-Meteo
    broker payloads while labeling them ecmwf_open_data.  Fix drops the
    role gate; guard must fire for any role when ingest_class is None.

    This test exercises the structural shape of the guard rather than the
    full fetch_ensemble call (which has many surrounding dependencies);
    pin that the source-file no longer carries `role == "entry_primary"`
    inside the guard.  A dedicated functional test for the guard belongs
    in the live-route integration suite, not here.
    """
    src = (
        Path(__file__).resolve().parents[1] / "src" / "data" / "ensemble_client.py"
    ).read_text(encoding="utf-8")
    # Locate the SourceNotEnabled raise that mentions ecmwf_open_data.
    # The guard's predicate must NOT include `role == "entry_primary"`.
    guard_pattern = re.compile(
        r"if\s*\(\s*\n\s*source_id\s*==\s*\"ecmwf_open_data\"\s*\n"
        r"\s*and\s+source_spec\.ingest_class\s+is\s+None\s*\n\s*\):",
        re.MULTILINE,
    )
    assert guard_pattern.search(src) is not None, (
        "Copilot #3 regression: ensemble_client guard for ecmwf_open_data + "
        "no-ingest_class must NOT branch on role; pre-fix it was gated on "
        "role == 'entry_primary' which left non-entry roles silently "
        "mis-provenanced."
    )
    # Belt-and-braces: the role-gated form must not appear anywhere near
    # the guard.
    bad_pattern = re.compile(
        r"role\s*==\s*\"entry_primary\"[\s\S]{0,300}?ingest_class\s+is\s+None",
        re.MULTILINE,
    )
    assert bad_pattern.search(src) is None, (
        "Copilot #3 regression: role-gated form re-introduced near the guard"
    )


# ---- Codex P1 #6 — transfer gate uses actual bucket domain ----------------


_EVALUATOR_PATH = Path(__file__).resolve().parents[1] / "src" / "engine" / "evaluator.py"
_EVALUATOR_SRC = _EVALUATOR_PATH.read_text(encoding="utf-8")
_MANAGER_PATH = Path(__file__).resolve().parents[1] / "src" / "calibration" / "manager.py"
_MANAGER_SRC = _MANAGER_PATH.read_text(encoding="utf-8")


def test_calibrator_object_carries_bucket_attrs():
    """_model_data_to_calibrator must attach bucket_* attrs onto the
    calibrator object — these are what evaluator's transfer gate reads.
    """
    from src.calibration.manager import _model_data_to_calibrator

    md = {
        "A": 1.0, "B": 0.0, "C": 0.0, "n_samples": 100,
        "bootstrap_params": [], "input_space": "width_normalized_density",
        "bucket_cycle": "12",
        "bucket_source_id": "ecmwf_open_data",
        "bucket_horizon_profile": "full",
        "bucket_data_version": "ecmwf_opendata_mx2t6_local_calendar_day_max_v1",
    }
    cal = _model_data_to_calibrator(md)
    assert cal._bucket_cycle == "12"
    assert cal._bucket_source_id == "ecmwf_open_data"
    assert cal._bucket_horizon_profile == "full"
    assert cal._bucket_data_version == (
        "ecmwf_opendata_mx2t6_local_calendar_day_max_v1"
    )


def test_calibrator_object_legacy_fallback_has_none_bucket_attrs():
    """When load_platt_model (legacy table) is the source, bucket_* must
    be None so the evaluator gate falls into the cross-domain rejection
    branch — not silently treated as a matched bucket.
    """
    from src.calibration.manager import _model_data_to_calibrator

    md = {
        "A": 1.0, "B": 0.0, "C": 0.0, "n_samples": 100,
        "bootstrap_params": [], "input_space": "width_normalized_density",
        "bucket_cycle": None,
        "bucket_source_id": None,
        "bucket_horizon_profile": None,
        "bucket_data_version": None,
    }
    cal = _model_data_to_calibrator(md)
    assert cal._bucket_cycle is None
    assert cal._bucket_source_id is None
    assert cal._bucket_horizon_profile is None
    assert cal._bucket_data_version is None


def test_evaluator_transfer_gate_uses_loaded_bucket_attrs():
    """Structural assert: evaluator's transfer-gate construction must
    branch on cal._bucket_* attrs, not hardcoded TIGGE.  Codex P1 #6
    regression test.
    """
    # The reordered code: get_calibrator runs FIRST, then transfer gate
    # reads cal._bucket_source_id (etc.).  Pin both signals.
    assert "_bucket_source_id" in _EVALUATOR_SRC, (
        "Codex P1 #6 regression: evaluator no longer reads cal._bucket_source_id"
    )
    # The cal call must come BEFORE the transfer gate.  Find the line numbers.
    src_lines = _EVALUATOR_SRC.splitlines()
    transfer_gate_idx = None
    cal_load_idx = None
    for idx, line in enumerate(src_lines):
        if cal_load_idx is None and re.search(
            r"cal,\s*cal_level\s*=\s*get_calibrator\(", line
        ):
            cal_load_idx = idx
        if transfer_gate_idx is None and "evaluate_calibration_transfer(" in line:
            transfer_gate_idx = idx
    assert cal_load_idx is not None, "evaluator must call get_calibrator"
    assert transfer_gate_idx is not None, "evaluator must call evaluate_calibration_transfer"
    assert cal_load_idx < transfer_gate_idx, (
        f"Codex P1 #6 regression: get_calibrator (line {cal_load_idx+1}) must "
        f"precede evaluate_calibration_transfer (line {transfer_gate_idx+1}) "
        f"so the gate sees the loaded bucket's actual identity"
    )


def test_evaluator_no_longer_hardcodes_tigge_calibrator_domain():
    """Pre-fix the gate constructed:
        calibrator_domain = ForecastCalibrationDomain(
            source_id="tigge_mars", cycle_hour_utc="00", horizon_profile="full", ...)
    unconditionally.  Post-fix that branch is reached ONLY in the legacy
    fallback (else branch), guarded by `_bucket_sid` falsiness.  Pin the
    structure so a regression that drops the guard re-introduces the
    false-rejection bug.
    """
    # The hardcoded TIGGE domain construction must be inside an else / fallback.
    # Look for a pattern: `if _bucket_sid` ... `else:` ... `tigge_mars`.
    assert re.search(
        r"if\s+_bucket_sid\s+and\s+_bucket_cyc",
        _EVALUATOR_SRC,
    ), (
        "Codex P1 #6 regression: evaluator no longer guards the hardcoded "
        "TIGGE domain construction behind a `_bucket_sid` check"
    )
