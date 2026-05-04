# Created: 2026-04-28
# Last reused/audited: 2026-05-04
# Authority basis: round3_verdict.md §1 #2 + ULTIMATE_PLAN.md L305-308 +
# ATTRIBUTION_DRIFT packet BATCH 3 dispatch (end-to-end runner tests). Per
# Fitz "test relationships, not just functions" — these tests verify the
# CROSS-MODULE wire-up: synthetic DB → run_weekly() → JSON report shape +
# exit-code behavior + drift_positions evidence + custom report-out path.
#
# A6 audit (2026-05-04, rebuild fixes branch): like test_attribution_drift,
# this file exercises the legacy-mode-axis dispatch detector and must pin
# ZEUS_MARKET_PHASE_DISPATCH=0. See test_attribution_drift.py docstring
# for the structural rationale.
"""End-to-end tests for scripts/attribution_drift_weekly.py.

Four tests covering: report structural shape, decay-verdict propagation
with appropriate exit-code semantics, empty-DB graceful, custom
report-out path round-trip.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Load the runner module via importlib (it's in scripts/, not on PYTHONPATH
# as a package). Python 3.14 dataclass requires sys.modules registration
# (per Tier 2 Phase 4 lesson + EDGE_OBSERVATION BATCH 3 precedent).
_spec = importlib.util.spec_from_file_location(
    "attribution_drift_weekly_mod",
    REPO_ROOT / "scripts" / "attribution_drift_weekly.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["attribution_drift_weekly_mod"] = _mod
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
run_weekly = _mod.run_weekly
main = _mod.main

from src.state.db import init_schema  # noqa: E402
# Reuse the helper from BATCH 1 of EDGE_OBSERVATION test bed.
from tests.test_edge_observation import _insert_settled  # noqa: E402


@pytest.fixture(autouse=True)
def _pin_legacy_mode_axis_dispatch(monkeypatch):
    """Pin every test in this file to ZEUS_MARKET_PHASE_DISPATCH=0 — see
    file docstring for the A6 rationale."""
    monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")


def _make_temp_db(tmp_path: Path) -> Path:
    """Create a temp Zeus state DB at tmp_path/state.db with canonical schema."""
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    init_schema(conn)
    conn.commit()
    conn.close()
    return db_path


def test_report_structural_shape(tmp_path: Path):
    """RELATIONSHIP: report has all required top-level keys + per-strategy
    sub-shape. Contract for downstream readers."""
    db_path = _make_temp_db(tmp_path)
    report = run_weekly(db_path, end_date=date(2026, 4, 28), window_days=7)

    for k in ("report_kind", "report_version", "generated_at", "end_date",
              "window_days", "db_path", "per_strategy", "drift_positions"):
        assert k in report, f"missing top-level key: {k}"
    assert report["report_kind"] == "attribution_drift_weekly"
    assert report["report_version"] == "1"
    assert report["end_date"] == "2026-04-28"
    assert report["window_days"] == 7

    expected = {"settlement_capture", "shoulder_sell", "center_buy", "opening_inertia"}
    assert set(report["per_strategy"].keys()) == expected
    # Per-strategy sub-shape contract.
    for sk, rec in report["per_strategy"].items():
        for k in ("drift_rate", "n_positions", "n_drift", "n_matches",
                  "n_insufficient", "n_decidable", "sample_quality",
                  "window_start", "window_end"):
            assert k in rec, f"missing per-strategy field {k} for {sk}"
    # drift_positions is a list (can be empty on empty DB).
    assert isinstance(report["drift_positions"], list)


def test_empty_db_graceful_no_crash(tmp_path: Path):
    """RELATIONSHIP: empty DB → all 4 strategies report n_positions=0;
    drift_positions=[]; runner does not crash; JSON well-formed."""
    db_path = _make_temp_db(tmp_path)
    report = run_weekly(db_path, end_date=date(2026, 4, 28), window_days=7)

    for sk in ("settlement_capture", "shoulder_sell", "center_buy", "opening_inertia"):
        rec = report["per_strategy"][sk]
        assert rec["n_positions"] == 0
        assert rec["drift_rate"] is None
    assert report["drift_positions"] == []

    # JSON serializability round-trip.
    serialized = json.dumps(report, default=str)
    re_loaded = json.loads(serialized)
    assert re_loaded["report_kind"] == "attribution_drift_weekly"


def test_drift_propagation_and_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture):
    """RELATIONSHIP: when synthetic DB has clear drifts AND drift_rate
    exceeds threshold, main() returns 1 (cron-friendly). drift_positions
    list contains the evidence for each detected drift.

    Setup: 5 shoulder_sell positions on finite_range bins (helper default
    bin_label='39-40°F'). All 5 → drift_detected (label says shoulder but
    bin is finite_range → inferred=center_buy). drift_rate=5/5=1.0,
    well above default threshold 0.05 → exit 1.
    """
    db_path = _make_temp_db(tmp_path)
    base = "2026-04-23T12:00:00+00:00"
    for i in range(5):
        _insert_settled(
            db_path_or_conn := sqlite3.connect(str(db_path)),
            position_id=f"ss{i}", strategy="shoulder_sell",
            settled_at=base, outcome=1, p_posterior=0.5,
        )
        db_path_or_conn.close()

    report = run_weekly(db_path, end_date=date(2026, 4, 28), window_days=7)
    ss = report["per_strategy"]["shoulder_sell"]
    assert ss["n_drift"] == 5
    assert ss["drift_rate"] == 1.0

    # drift_positions contains the 5 verdicts.
    assert len(report["drift_positions"]) == 5
    for v in report["drift_positions"]:
        assert v["kind"] == "drift_detected"
        # Each verdict serializes its signature (dataclass-asdict'd).
        assert "signature" in v
        assert v["signature"]["label_strategy"] == "shoulder_sell"
        assert v["signature"]["inferred_strategy"] == "center_buy"

    # main() should exit 1 (drift_rate 1.0 > default threshold 0.05).
    out_path = tmp_path / "report.json"
    rc = main([
        "--db-path", str(db_path),
        "--end-date", "2026-04-28",
        "--report-out", str(out_path),
    ])
    assert rc == 1, "drift_rate above threshold → exit 1 expected"
    captured = capsys.readouterr()
    assert "EXCEEDS" in captured.out
    assert "shoulder_sell" in captured.out


def test_custom_report_out_round_trip(tmp_path: Path):
    """RELATIONSHIP: report written to custom --report-out path reads back
    as the same dict. Validates file IO + JSON round-trip + the
    exit-code-0 path (no drift in empty DB → exit 0)."""
    db_path = _make_temp_db(tmp_path)
    out_path = tmp_path / "custom_report.json"
    rc = main([
        "--db-path", str(db_path),
        "--end-date", "2026-04-28",
        "--report-out", str(out_path),
    ])
    assert rc == 0, "empty DB → no drift → exit 0 expected"
    re_loaded = json.loads(out_path.read_text())
    assert re_loaded["report_kind"] == "attribution_drift_weekly"
    assert re_loaded["end_date"] == "2026-04-28"
    assert re_loaded["window_days"] == 7
    assert re_loaded["drift_positions"] == []
