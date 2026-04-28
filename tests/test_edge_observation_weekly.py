# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: round3_verdict.md §1 #2 + ULTIMATE_PLAN.md L297-301 +
# EDGE_OBSERVATION packet BATCH 3 dispatch (end-to-end runner tests). Per Fitz
# "test relationships, not just functions" — these tests verify the
# CROSS-MODULE wire-up: synthetic DB → run_weekly() → JSON report shape +
# correct verdict propagation from compute_realized_edge_per_strategy +
# detect_alpha_decay.
"""End-to-end tests for scripts/edge_observation_weekly.py.

Four tests covering: report structural shape, decay-verdict propagation,
empty-DB graceful, custom report-out path round-trip.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

# Make scripts/ importable for the run_weekly module.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from src.state.db import init_schema  # noqa: E402

# Import the runner module via importlib (it's in scripts/, not on PYTHONPATH
# as a package). Python 3.14 dataclass requires sys.modules registration.
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location(
    "edge_observation_weekly_mod",
    REPO_ROOT / "scripts" / "edge_observation_weekly.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["edge_observation_weekly_mod"] = _mod
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
run_weekly = _mod.run_weekly


# Reuse the BATCH 1 test helper for inserting SETTLED events.
from tests.test_edge_observation import _insert_settled  # noqa: E402


def _make_temp_db(tmp_path: Path) -> Path:
    """Create a temp Zeus state DB at tmp_path/state.db with the canonical schema."""
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
    sub-shape. This is the contract downstream readers consume."""
    db_path = _make_temp_db(tmp_path)
    report = run_weekly(db_path, end_date=date(2026, 4, 28), window_days=7, n_windows=4)

    # Top-level keys.
    for k in ("report_kind", "report_version", "generated_at", "end_date",
              "window_days", "n_windows_for_decay", "db_path",
              "current_window", "decay_verdicts"):
        assert k in report, f"missing top-level key: {k}"
    assert report["report_kind"] == "edge_observation_weekly"
    assert report["report_version"] == "1"
    assert report["end_date"] == "2026-04-28"
    assert report["window_days"] == 7
    assert report["n_windows_for_decay"] == 4

    # All 4 strategies present in both current_window and decay_verdicts.
    expected = {"settlement_capture", "shoulder_sell", "center_buy", "opening_inertia"}
    assert set(report["current_window"].keys()) == expected
    assert set(report["decay_verdicts"].keys()) == expected
    # Per-strategy verdict shape.
    for sk, v in report["decay_verdicts"].items():
        assert "kind" in v
        assert "evidence" in v
        # severity present (None when not decay)
        assert "severity" in v


def test_empty_db_graceful_no_crash(tmp_path: Path):
    """RELATIONSHIP: empty DB → all 4 strategies report n_trades=0 + verdict
    insufficient_data; runner does not crash; JSON is well-formed."""
    db_path = _make_temp_db(tmp_path)
    report = run_weekly(db_path, end_date=date(2026, 4, 28), window_days=7, n_windows=4)

    for sk in ("settlement_capture", "shoulder_sell", "center_buy", "opening_inertia"):
        snap = report["current_window"][sk]
        assert snap["n_trades"] == 0
        assert snap["edge_realized"] is None
        verdict = report["decay_verdicts"][sk]
        assert verdict["kind"] == "insufficient_data"

    # Round-trip through JSON to confirm serializability.
    serialized = json.dumps(report, default=str)
    re_loaded = json.loads(serialized)
    assert re_loaded["report_kind"] == "edge_observation_weekly"


def test_decay_verdict_propagates_for_strategy_with_clear_decay(tmp_path: Path):
    """RELATIONSHIP: when a strategy has a clear decay pattern across the
    n_windows of trailing data, run_weekly's decay_verdicts must surface
    alpha_decay_detected for that strategy.

    Setup: 4 windows × 7 days. Trailing 3 windows have realized edge ≈ 0.10
    (every trade outcome=1 with p_posterior=0.9 → per-trade edge = 0.1).
    Current window has edge ≈ 0.01 (outcome=1 with p_posterior=0.99 → edge =
    0.01). Per-window: 30 trades for sample_quality='adequate'.

    Window-end days for n_windows=4 with window_days=7:
      end-21d (trailing), end-14d (trailing), end-7d (trailing), end (current).

    KEY DETAIL: each settled_at must fall inside its window
    [win_end - 7d, win_end]. Place each trade at win_end-3d (mid-window).
    Use one shared connection (transient connections per insert can lose
    dedup state under WAL).
    """
    import datetime as _dt
    db_path = _make_temp_db(tmp_path)
    end = date(2026, 4, 28)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        def fill_window(win_end: date, win_idx: int, n_trades: int, edge_target: float):
            settled_day = win_end - _dt.timedelta(days=3)   # mid-window
            settled_at = f"{settled_day.isoformat()}T12:00:00+00:00"
            p_post = max(0.0, min(1.0, 1.0 - edge_target))
            for i in range(n_trades):
                _insert_settled(
                    conn, position_id=f"w{win_idx}p{i}", strategy="settlement_capture",
                    settled_at=settled_at, outcome=1, p_posterior=p_post,
                )
        fill_window(end - _dt.timedelta(days=21), 0, 30, 0.10)
        fill_window(end - _dt.timedelta(days=14), 1, 30, 0.10)
        fill_window(end - _dt.timedelta(days=7),  2, 30, 0.10)
        fill_window(end,                           3, 30, 0.01)
        conn.commit()
    finally:
        conn.close()

    report = run_weekly(db_path, end_date=end, window_days=7, n_windows=4)

    sc_verdict = report["decay_verdicts"]["settlement_capture"]
    assert sc_verdict["kind"] == "alpha_decay_detected", \
        f"expected decay; got {sc_verdict['kind']}; evidence={sc_verdict['evidence']}"
    # Severity: ratio = 0.01 / 0.10 = 0.1 < 0.3 critical cutoff → critical.
    assert sc_verdict["severity"] == "critical", \
        f"ratio 0.1 should be critical; got {sc_verdict['severity']!r}"

    # Other strategies remain insufficient_data (no trades).
    for sk in ("shoulder_sell", "center_buy", "opening_inertia"):
        assert report["decay_verdicts"][sk]["kind"] == "insufficient_data"


def test_custom_report_out_round_trip(tmp_path: Path):
    """RELATIONSHIP: report written to disk reads back as the same dict.

    Validates the JSON round-trip + the file IO path, not just in-memory
    behavior.
    """
    db_path = _make_temp_db(tmp_path)
    report = run_weekly(db_path, end_date=date(2026, 4, 28), window_days=7, n_windows=4)

    out_path = tmp_path / "custom_report.json"
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n")

    re_loaded = json.loads(out_path.read_text())
    # Top-level keys round-trip.
    assert re_loaded["report_kind"] == report["report_kind"]
    assert re_loaded["end_date"] == report["end_date"]
    assert re_loaded["window_days"] == report["window_days"]
    # Per-strategy snapshot keys round-trip.
    for sk in re_loaded["current_window"]:
        assert re_loaded["current_window"][sk]["n_trades"] == report["current_window"][sk]["n_trades"]
