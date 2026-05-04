# Created: 2026-04-29
# Last reused/audited: 2026-04-29
# Authority basis: round3_verdict.md §1 #2 (FIFTH and FINAL edge packet) +
# ULTIMATE_PLAN.md §4 #4 + LEARNING_LOOP packet BATCH 3 dispatch (end-to-end
# runner tests + cross-module orchestration). Per Fitz "test relationships,
# not just functions" — these tests verify the CROSS-MODULE wire-up:
# synthetic DB → run_weekly() (which orchestrates BATCH 1 + BATCH 2 +
# CALIBRATION BATCH 1 + CALIBRATION BATCH 2 detector) → JSON report shape +
# exit-code behavior + per-bucket override + cross-packet drift integration.
"""End-to-end tests for scripts/learning_loop_observation_weekly.py.

7 tests covering:

  1. test_report_structural_shape — top-level + per-bucket + per-verdict
     + per-bucket-thresholds 3-tuple shape stable for downstream readers
  2. test_empty_db_graceful_no_crash — no models → empty current_window +
     empty stall_verdicts; exit 0
  3. test_stall_detected_propagates_to_exit_1 — synthetic stall scenario
     → main() exit 1 + STALL marker in stdout
  4. test_per_bucket_threshold_default_high_vs_low — HIGH metric gets
     tighter thresholds (1.3/20/10) vs LOW (1.5/30/14)
  5. test_per_bucket_threshold_override_actually_overrides — explicit
     KEY=FIELD=VALUE override flips threshold for matching bucket
  6. test_override_bucket_validation_errors — 4-validation paths each
     raise ArgumentTypeError (LOW-DESIGN-WP-2-2 pattern carry-forward)
  7. test_cross_module_orchestration_drift_detected_map — runner correctly
     invokes CALIBRATION BATCH 1+2 and feeds drift_detected into stall
     detector; per-bucket drift map surfaced in JSON report
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Load the runner module via importlib (sibling EO/AD/WP/CALIBRATION pattern).
_spec = importlib.util.spec_from_file_location(
    "learning_loop_observation_weekly_mod",
    REPO_ROOT / "scripts" / "learning_loop_observation_weekly.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["learning_loop_observation_weekly_mod"] = _mod
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
run_weekly = _mod.run_weekly
main = _mod.main
_parse_override_bucket = _mod._parse_override_bucket
DEFAULT_HIGH_PAIR_GROWTH = _mod.DEFAULT_HIGH_PAIR_GROWTH
DEFAULT_HIGH_PAIRS_READY = _mod.DEFAULT_HIGH_PAIRS_READY
DEFAULT_HIGH_DRIFT = _mod.DEFAULT_HIGH_DRIFT
DEFAULT_LOW_PAIR_GROWTH = _mod.DEFAULT_LOW_PAIR_GROWTH
DEFAULT_LOW_PAIRS_READY = _mod.DEFAULT_LOW_PAIRS_READY

from src.calibration.store import save_platt_model_v2  # noqa: E402
from src.state.db import init_schema  # noqa: E402
from src.state.schema.v2_schema import apply_v2_schema  # noqa: E402
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN  # noqa: E402


def _make_temp_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    init_schema(conn)
    apply_v2_schema(conn)
    conn.commit()
    conn.close()
    return db_path


def _seed_v2_model(
    db_path: Path,
    *,
    metric_identity,
    cluster: str,
    season: str,
    data_version: str,
    A: float = 1.5,
    n_samples: int = 60,
):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        save_platt_model_v2(
            conn, metric_identity=metric_identity, cluster=cluster,
            season=season, data_version=data_version,
            param_A=A, param_B=0.3, param_C=0.0,
            bootstrap_params=[(A, 0.3, 0.0)] * 30, n_samples=n_samples,
            input_space="width_normalized_density",
        )
        conn.commit()
    finally:
        conn.close()


def _seed_calibration_pairs(
    db_path: Path,
    *,
    cluster: str,
    season: str,
    n_pairs: int,
    decision_group_prefix: str = "dg",
):
    """Insert N canonical_v1 VERIFIED calibration pairs."""
    conn = sqlite3.connect(str(db_path))
    try:
        for i in range(n_pairs):
            conn.execute(
                """
                INSERT INTO calibration_pairs
                (city, target_date, range_label, p_raw, outcome, lead_days,
                 season, cluster, forecast_available_at, settlement_value,
                 decision_group_id, bias_corrected, bin_source, authority)
                VALUES ('TestCity', '2026-04-23', '50-51°F', 0.5, 1, 1.0,
                        ?, ?, '2026-04-22T12:00:00+00:00', 50.0,
                        ?, 0, 'canonical_v1', 'VERIFIED')
                """,
                (season, cluster, f"{decision_group_prefix}-{i}"),
            )
        conn.commit()
    finally:
        conn.close()


# --- Tests -----------------------------------------------------------------


def test_report_structural_shape(tmp_path: Path):
    """RELATIONSHIP: report has all required top-level + per-bucket-thresholds
    3-tuple + per-verdict fields. Contract for downstream readers."""
    db_path = _make_temp_db(tmp_path)
    report = run_weekly(db_path, end_date=date(2026, 4, 28), window_days=7)

    for k in ("report_kind", "report_version", "generated_at", "end_date",
              "window_days", "n_windows_for_stall", "per_bucket_thresholds",
              "drift_detected_map", "db_path", "current_window",
              "stall_verdicts"):
        assert k in report, f"missing top-level key: {k}"
    assert report["report_kind"] == "learning_loop_observation_weekly"
    assert report["report_version"] == "1"
    assert report["end_date"] == "2026-04-28"
    assert isinstance(report["per_bucket_thresholds"], dict)
    assert isinstance(report["drift_detected_map"], dict)


def test_empty_db_graceful_no_crash(tmp_path: Path):
    """RELATIONSHIP: empty DB → empty current_window + stall_verdicts;
    runner does not crash; main() exits 0."""
    db_path = _make_temp_db(tmp_path)
    report = run_weekly(db_path, end_date=date(2026, 4, 28), window_days=7)
    assert report["current_window"] == {}
    assert report["stall_verdicts"] == {}

    # JSON serializability round-trip.
    serialized = json.dumps(report, default=str)
    re_loaded = json.loads(serialized)
    assert re_loaded["report_kind"] == "learning_loop_observation_weekly"

    # main() exits 0.
    out_path = tmp_path / "report.json"
    rc = main([
        "--db-path", str(db_path),
        "--end-date", "2026-04-28",
        "--report-out", str(out_path),
    ])
    assert rc == 0


def test_stall_detected_propagates_to_exit_1(tmp_path: Path, capsys: pytest.CaptureFixture):
    """RELATIONSHIP: synthetic stall scenario (n_pairs=100 verified canonical
    + never_promoted bucket) → pairs_ready_no_retrain fires → main() exit 1
    + STALL marker in stdout."""
    db_path = _make_temp_db(tmp_path)
    _seed_v2_model(
        db_path, metric_identity=HIGH_LOCALDAY_MAX, cluster="StallCity",
        season="DJF", data_version="tigge_v3", A=1.5, n_samples=60,
    )
    # 100 canonical pairs → sample_quality='high' → eligible for stall detection
    _seed_calibration_pairs(db_path, cluster="StallCity", season="DJF", n_pairs=100)

    report = run_weekly(
        db_path, end_date=date(2026, 4, 28), window_days=7, n_windows=4,
    )
    bucket_key = "high:StallCity:DJF:tigge_v3:00:tigge_mars:full:width_normalized_density"
    rec = report["stall_verdicts"][bucket_key]
    # never_promoted + canonical pairs ready → pairs_ready_no_retrain fires
    assert rec["kind"] == "stall_detected"
    assert "pairs_ready_no_retrain" in rec["stall_kinds"]
    assert rec["severity"] == "critical"  # never_promoted → critical per LOW-DESIGN-LL-2-1

    # main() exits 1.
    out_path = tmp_path / "report.json"
    rc = main([
        "--db-path", str(db_path),
        "--end-date", "2026-04-28",
        "--n-windows", "4",
        "--report-out", str(out_path),
    ])
    assert rc == 1
    captured = capsys.readouterr()
    assert "STALL" in captured.out


def test_per_bucket_threshold_default_high_vs_low(tmp_path: Path):
    """RELATIONSHIP: HIGH metric gets tighter thresholds (1.3/20/10);
    LOW gets standard (1.5/30/14) per dispatch §Per-bucket threshold defaults."""
    db_path = _make_temp_db(tmp_path)
    _seed_v2_model(db_path, metric_identity=HIGH_LOCALDAY_MAX, cluster="HiCity",
                    season="DJF", data_version="tigge_v3", A=1.5, n_samples=60)
    _seed_v2_model(db_path, metric_identity=LOW_LOCALDAY_MIN, cluster="LoCity",
                    season="JJA", data_version="tigge_v3", A=1.5, n_samples=60)
    report = run_weekly(db_path, end_date=date(2026, 4, 28), window_days=7)

    high_key = next(k for k, snap in report["current_window"].items()
                    if snap.get("temperature_metric") == "high")
    low_key = next(k for k, snap in report["current_window"].items()
                   if snap.get("temperature_metric") == "low")

    assert report["per_bucket_thresholds"][high_key]["pair_growth"] == DEFAULT_HIGH_PAIR_GROWTH
    assert report["per_bucket_thresholds"][high_key]["pairs_ready"] == DEFAULT_HIGH_PAIRS_READY
    assert report["per_bucket_thresholds"][high_key]["drift"] == DEFAULT_HIGH_DRIFT
    assert report["per_bucket_thresholds"][low_key]["pair_growth"] == DEFAULT_LOW_PAIR_GROWTH
    assert report["per_bucket_thresholds"][low_key]["pairs_ready"] == DEFAULT_LOW_PAIRS_READY
    # HIGH < LOW (tighter)
    assert DEFAULT_HIGH_PAIR_GROWTH < DEFAULT_LOW_PAIR_GROWTH
    assert DEFAULT_HIGH_PAIRS_READY < DEFAULT_LOW_PAIRS_READY


def test_per_bucket_threshold_override_actually_overrides(tmp_path: Path):
    """RELATIONSHIP: --override-bucket flag flips threshold for the matching
    bucket per the FIELD specified."""
    db_path = _make_temp_db(tmp_path)
    _seed_v2_model(db_path, metric_identity=HIGH_LOCALDAY_MAX, cluster="OverCity",
                    season="DJF", data_version="tigge_v3", A=1.5, n_samples=60)
    bucket_key = "high:OverCity:DJF:tigge_v3:00:tigge_mars:full:width_normalized_density"
    report = run_weekly(
        db_path, end_date=date(2026, 4, 28), window_days=7,
        overrides={bucket_key: {"pair_growth": 1.1, "pairs_ready": 7}},
    )
    thresholds = report["per_bucket_thresholds"][bucket_key]
    assert thresholds["pair_growth"] == 1.1
    assert thresholds["pairs_ready"] == 7
    # Non-overridden field stays at HIGH default.
    assert thresholds["drift"] == DEFAULT_HIGH_DRIFT


def test_override_bucket_validation_errors():
    """RELATIONSHIP: _parse_override_bucket validates 4 input paths
    (LOW-DESIGN-WP-2-2 carry-forward pattern). Each malformed input raises
    argparse.ArgumentTypeError."""
    # Empty list → empty dict (no error)
    assert _parse_override_bucket([]) == {}
    # Missing equals (need 2) → error
    with pytest.raises(argparse.ArgumentTypeError, match="3 parts"):
        _parse_override_bucket(["only_one_part"])
    with pytest.raises(argparse.ArgumentTypeError, match="3 parts"):
        _parse_override_bucket(["only=two"])
    # Empty key → error
    with pytest.raises(argparse.ArgumentTypeError, match="KEY is empty"):
        _parse_override_bucket(["=pair_growth=1.5"])
    # Invalid FIELD → error
    with pytest.raises(argparse.ArgumentTypeError, match="FIELD must be one of"):
        _parse_override_bucket(["bucket=unknown_field=1.5"])
    # Non-float value → error
    with pytest.raises(argparse.ArgumentTypeError, match="not a float"):
        _parse_override_bucket(["bucket=pair_growth=not_a_number"])
    # Non-positive value → error
    with pytest.raises(argparse.ArgumentTypeError, match="must be positive"):
        _parse_override_bucket(["bucket=pair_growth=-1.5"])
    with pytest.raises(argparse.ArgumentTypeError, match="must be positive"):
        _parse_override_bucket(["bucket=pair_growth=0"])
    # Valid input → parsed correctly
    result = _parse_override_bucket(["b1=pair_growth=1.1", "b1=pairs_ready=15", "b2=drift=8"])
    assert result == {"b1": {"pair_growth": 1.1, "pairs_ready": 15}, "b2": {"drift": 8.0}}


def test_cross_module_orchestration_drift_detected_map(tmp_path: Path):
    """RELATIONSHIP: CROSS-MODULE ARCHITECTURE — runner correctly invokes
    CALIBRATION BATCH 1 (compute_platt_parameter_snapshot_per_bucket) +
    BATCH 2 (detect_parameter_drift) per bucket and feeds drift_detected
    into LEARNING BATCH 2 (detect_learning_loop_stall). Per-bucket drift
    map surfaced in JSON report.

    On HEAD substrate (no append-only Platt history per bucket — same active
    row N times in the historical-window query), CALIBRATION's
    detect_parameter_drift returns insufficient_data, which the runner
    correctly reflects as drift_detected=None in the map (NOT False).
    """
    db_path = _make_temp_db(tmp_path)
    _seed_v2_model(db_path, metric_identity=HIGH_LOCALDAY_MAX, cluster="OrchCity",
                    season="DJF", data_version="tigge_v3", A=1.5, n_samples=60)
    _seed_calibration_pairs(db_path, cluster="OrchCity", season="DJF", n_pairs=50)

    report = run_weekly(db_path, end_date=date(2026, 4, 28), window_days=7, n_windows=4)
    bucket_key = "high:OrchCity:DJF:tigge_v3:00:tigge_mars:full:width_normalized_density"
    # drift_detected_map present + entry for our bucket
    assert bucket_key in report["drift_detected_map"]
    drift_value = report["drift_detected_map"][bucket_key]
    # On HEAD substrate (no append-only Platt history per bucket), CALIBRATION
    # returns insufficient → runner records None (NOT False; honest).
    assert drift_value is None
    # The stall verdict's drift_no_refit kind reflects insufficient_data
    # (caller didn't pass True/False; passed None per cross-module orchestration).
    rec = report["stall_verdicts"][bucket_key]
    assert rec["evidence"]["per_kind"]["drift_no_refit"]["status"] == "insufficient_data"
