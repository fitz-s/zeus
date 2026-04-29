# Created: 2026-04-29
# Last reused/audited: 2026-04-29
# Authority basis: round3_verdict.md §1 #2 + ULTIMATE_PLAN.md L312-314 +
# WS_OR_POLL_TIGHTENING packet BATCH 3 dispatch (end-to-end runner tests). Per
# Fitz "test relationships, not just functions" — these tests verify the
# CROSS-MODULE wire-up: synthetic DB → run_weekly() → JSON report shape +
# exit-code behavior + per-strategy threshold override + custom report-out
# round-trip + negative_latency_count surfacing.
"""End-to-end tests for scripts/ws_poll_reaction_weekly.py.

Six tests covering:

  1. test_report_structural_shape — top-level + per-strategy + per-verdict
     fields stable for downstream readers
  2. test_empty_db_graceful_no_crash — no ticks, no positions → all 4
     strategies report n_signals=0; gap_verdicts insufficient_data; exit 0
  3. test_gap_detected_propagates_to_exit_1 — synthetic gap pattern in
     opening_inertia → main() returns 1 (cron-friendly); EXCEEDS surfaced
  4. test_per_strategy_threshold_override_actually_overrides — passing a
     tight kwarg flips a borderline-ratio strategy from within_normal to
     gap_detected
  5. test_custom_report_out_round_trip + --stdout flag works
  6. test_negative_latency_count_surfaced — upstream clock-skew rows are
     counted in the report (LOW caveat from cycle 22 carry-forward — the
     count is operator-visible, not silently swallowed)
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Load the runner module via importlib (it's in scripts/, not on PYTHONPATH
# as a package). Mirror EO BATCH 3 pattern.
_spec = importlib.util.spec_from_file_location(
    "ws_poll_reaction_weekly_mod",
    REPO_ROOT / "scripts" / "ws_poll_reaction_weekly.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ws_poll_reaction_weekly_mod"] = _mod
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
run_weekly = _mod.run_weekly
main = _mod.main
DEFAULT_PER_STRATEGY_THRESHOLDS = _mod.DEFAULT_PER_STRATEGY_THRESHOLDS

from src.state.db import init_schema  # noqa: E402
# Reuse helpers from BATCH 1 test bed.
from tests.test_ws_poll_reaction import (  # noqa: E402
    _insert_position_current,
    _insert_tick,
)


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


def _seed_window_ticks(
    conn: sqlite3.Connection,
    *,
    token_id: str,
    strategy_key: str,
    base_date: date,
    n_ticks: int,
    latency_ms: int,
    position_id_prefix: str,
):
    """Seed N ticks for one strategy on one window day with a fixed latency.

    Inserts one position_current row + n_ticks token_price_log rows, all
    timestamped at 12:00:00 UTC on base_date (so they fall inside any
    window whose end_date >= base_date and start <= base_date).
    """
    _insert_position_current(
        conn,
        position_id=f"{position_id_prefix}-pos",
        token_id=token_id,
        strategy_key=strategy_key,
        target_date=base_date.isoformat(),
    )
    # Ticks spaced a few ms apart on the same day; each with the SAME latency.
    base_zeus_secs = 43200  # 12:00:00 UTC
    for i in range(n_ticks):
        zeus_ms = base_zeus_secs * 1000 + i * 100  # 100ms apart in ms
        source_ms = zeus_ms - latency_ms  # constant latency_ms
        zeus_ts = f"{base_date.isoformat()}T12:00:{(zeus_ms // 1000) % 60:02d}.{zeus_ms % 1000:03d}+00:00"
        # Use full ms-precision ISO for source too.
        src_total_secs = source_ms // 1000
        src_h = (src_total_secs // 3600) % 24
        src_m = (src_total_secs // 60) % 60
        src_s = src_total_secs % 60
        src_ms_remain = source_ms % 1000
        source_ts = f"{base_date.isoformat()}T{src_h:02d}:{src_m:02d}:{src_s:02d}.{src_ms_remain:03d}+00:00"
        _insert_tick(
            conn,
            token_id=token_id,
            source_ts=source_ts,
            zeus_ts=zeus_ts,
        )


def test_report_structural_shape(tmp_path: Path):
    """RELATIONSHIP: report has all required top-level + per-strategy +
    per-verdict fields. Contract for downstream readers."""
    db_path = _make_temp_db(tmp_path)
    report = run_weekly(db_path, end_date=date(2026, 4, 28), window_days=7)

    for k in ("report_kind", "report_version", "generated_at", "end_date",
              "window_days", "n_windows_for_gap", "per_strategy_thresholds",
              "critical_ratio_cutoff", "negative_latency_count", "db_path",
              "current_window", "gap_verdicts"):
        assert k in report, f"missing top-level key: {k}"
    assert report["report_kind"] == "ws_poll_reaction_weekly"
    assert report["report_version"] == "1"
    assert report["end_date"] == "2026-04-28"
    assert report["window_days"] == 7

    # Per-strategy thresholds: 4 keys with sensible defaults; opening_inertia tighter.
    expected_keys = {"settlement_capture", "shoulder_sell", "center_buy", "opening_inertia"}
    assert set(report["per_strategy_thresholds"].keys()) == expected_keys
    assert report["per_strategy_thresholds"]["opening_inertia"] < report["per_strategy_thresholds"]["center_buy"]

    # current_window and gap_verdicts both keyed by all 4 strategies.
    assert set(report["current_window"].keys()) == expected_keys
    assert set(report["gap_verdicts"].keys()) == expected_keys
    for sk, snap in report["current_window"].items():
        for k in ("latency_p50_ms", "latency_p95_ms", "n_signals", "n_with_action",
                  "sample_quality", "window_start", "window_end"):
            assert k in snap, f"missing per-strategy current_window field {k} for {sk}"
    for sk, v in report["gap_verdicts"].items():
        for k in ("kind", "strategy_key", "severity", "evidence"):
            assert k in v, f"missing per-strategy gap_verdict field {k} for {sk}"
        assert v["strategy_key"] == sk

    assert isinstance(report["negative_latency_count"], int)


def test_empty_db_graceful_no_crash(tmp_path: Path):
    """RELATIONSHIP: empty DB → all 4 strategies report n_signals=0;
    all 4 gap_verdicts are insufficient_data; runner does not crash; JSON
    well-formed; main() exits 0."""
    db_path = _make_temp_db(tmp_path)
    report = run_weekly(db_path, end_date=date(2026, 4, 28), window_days=7)

    for sk in ("settlement_capture", "shoulder_sell", "center_buy", "opening_inertia"):
        snap = report["current_window"][sk]
        assert snap["n_signals"] == 0
        assert snap["latency_p95_ms"] is None
        assert report["gap_verdicts"][sk]["kind"] == "insufficient_data"
    assert report["negative_latency_count"] == 0

    # JSON serializability round-trip.
    serialized = json.dumps(report, default=str)
    re_loaded = json.loads(serialized)
    assert re_loaded["report_kind"] == "ws_poll_reaction_weekly"

    # main() with empty DB: no gap → exit 0.
    out_path = tmp_path / "report.json"
    rc = main([
        "--db-path", str(db_path),
        "--end-date", "2026-04-28",
        "--report-out", str(out_path),
    ])
    assert rc == 0


def test_gap_detected_propagates_to_exit_1(tmp_path: Path, capsys: pytest.CaptureFixture):
    """RELATIONSHIP: when synthetic ticks across 4 trailing windows show a
    clear latency gap in the current window for opening_inertia, main()
    returns 1 (cron-friendly). Per-strategy summary line shows EXCEEDS marker.

    Setup: 4 weekly windows ending 2026-04-28, window_days=7. For each of
    the 3 trailing weeks, seed 30 ticks with latency=50ms (low-latency
    baseline). For the current week, seed 30 ticks with latency=200ms
    (4x trailing). With opening_inertia threshold 1.2 (tight), ratio 4.0
    triggers gap_detected critical (>= critical_ratio_cutoff=2.0).
    """
    db_path = _make_temp_db(tmp_path)
    end = date(2026, 4, 28)

    # 4 windows of 7 days each, ending on 2026-04-28.
    # Each window's "base date" is window_end - 0..7d; we put ticks 1 day
    # before window_end so they fall inside the window.
    # Trailing windows: end - 7*3, end - 7*2, end - 7*1 → bases at
    # those end-days. Each trailing window: 30 ticks @ 50ms latency.
    # Current window: end → base end. 30 ticks @ 200ms latency.
    conn = sqlite3.connect(str(db_path))
    try:
        # Trailing windows (oldest to newest). Window-end days the runner
        # iterates: end - 21d, end - 14d, end - 7d, end (current).
        # Tick times must fall inside each window (window_start =
        # window_end - window_days). We put ticks one day inside the window
        # at window_end - 1d.
        for i, weeks_back in enumerate([3, 2, 1]):
            window_end = end - timedelta(days=7 * weeks_back)
            tick_day = window_end - timedelta(days=1)
            _seed_window_ticks(
                conn,
                token_id=f"oi-tok-{i}",
                strategy_key="opening_inertia",
                base_date=tick_day,
                n_ticks=30,
                latency_ms=50,
                position_id_prefix=f"oi-trail-{i}",
            )
        # Current window: tick day inside [end - 7, end].
        cur_tick_day = end - timedelta(days=1)
        _seed_window_ticks(
            conn,
            token_id="oi-tok-cur",
            strategy_key="opening_inertia",
            base_date=cur_tick_day,
            n_ticks=30,
            latency_ms=200,
            position_id_prefix="oi-cur",
        )
        conn.commit()
    finally:
        conn.close()

    report = run_weekly(db_path, end_date=end, window_days=7, n_windows=4)
    oi = report["gap_verdicts"]["opening_inertia"]
    assert oi["kind"] == "gap_detected", f"expected gap_detected, got {oi}"
    assert oi["severity"] == "critical", f"expected critical (ratio 4.0 >= 2.0), got {oi['severity']}"

    # main() should exit 1 due to opening_inertia gap_detected.
    out_path = tmp_path / "report.json"
    rc = main([
        "--db-path", str(db_path),
        "--end-date", end.isoformat(),
        "--n-windows", "4",
        "--report-out", str(out_path),
    ])
    assert rc == 1, "gap_detected → exit 1 expected"
    captured = capsys.readouterr()
    assert "EXCEEDS" in captured.out
    assert "opening_inertia" in captured.out


def test_per_strategy_threshold_override_actually_overrides(tmp_path: Path):
    """RELATIONSHIP: a borderline ratio that would be within_normal at the
    default threshold flips to gap_detected when a tighter override is
    passed. Verifies the override wiring all the way through main → CLI
    flag → run_weekly → detect_reaction_gap.

    Setup: shoulder_sell with current p95 = 1.45x trailing mean.
    - Default threshold for shoulder_sell = 1.4 → 1.45 > 1.4 → gap_detected
    - Tighter override 1.5 (above 1.45) → within_normal
    Just confirm the threshold flows through and changes the verdict.
    """
    db_path = _make_temp_db(tmp_path)
    end = date(2026, 4, 28)

    conn = sqlite3.connect(str(db_path))
    try:
        # 3 trailing windows @ 100ms latency.
        for i, weeks_back in enumerate([3, 2, 1]):
            window_end = end - timedelta(days=7 * weeks_back)
            tick_day = window_end - timedelta(days=1)
            _seed_window_ticks(
                conn,
                token_id=f"ss-tok-{i}",
                strategy_key="shoulder_sell",
                base_date=tick_day,
                n_ticks=30,
                latency_ms=100,
                position_id_prefix=f"ss-trail-{i}",
            )
        # Current window @ 145ms latency → ratio = 1.45.
        cur_tick_day = end - timedelta(days=1)
        _seed_window_ticks(
            conn,
            token_id="ss-tok-cur",
            strategy_key="shoulder_sell",
            base_date=cur_tick_day,
            n_ticks=30,
            latency_ms=145,
            position_id_prefix="ss-cur",
        )
        conn.commit()
    finally:
        conn.close()

    # Default thresholds (shoulder_sell=1.4): 1.45 > 1.4 → gap_detected.
    report_default = run_weekly(db_path, end_date=end, window_days=7, n_windows=4)
    ss_default = report_default["gap_verdicts"]["shoulder_sell"]
    assert ss_default["kind"] == "gap_detected", f"expected default gap, got {ss_default}"

    # Tighter override (shoulder_sell=1.5): 1.45 < 1.5 → within_normal.
    # Note: detect_reaction_gap uses STRICT > threshold (gap iff ratio > thr).
    report_override = run_weekly(
        db_path, end_date=end, window_days=7, n_windows=4,
        per_strategy_thresholds={"shoulder_sell": 1.5},
    )
    ss_override = report_override["gap_verdicts"]["shoulder_sell"]
    assert ss_override["kind"] == "within_normal", f"expected within_normal w/ thr=1.5, got {ss_override}"


def test_custom_report_out_and_stdout(tmp_path: Path, capsys: pytest.CaptureFixture):
    """RELATIONSHIP: --report-out writes to custom path; --stdout also
    prints JSON to stdout; both can be combined; round-trips through JSON."""
    db_path = _make_temp_db(tmp_path)
    out_path = tmp_path / "custom_wp_report.json"
    rc = main([
        "--db-path", str(db_path),
        "--end-date", "2026-04-28",
        "--report-out", str(out_path),
        "--stdout",
    ])
    assert rc == 0  # empty DB → no gap → exit 0
    re_loaded = json.loads(out_path.read_text())
    assert re_loaded["report_kind"] == "ws_poll_reaction_weekly"
    assert re_loaded["end_date"] == "2026-04-28"

    captured = capsys.readouterr()
    assert "ws_poll_reaction_weekly" in captured.out  # stdout dump present


def test_negative_latency_count_surfaced(tmp_path: Path):
    """RELATIONSHIP: when synthetic ticks have zeus_timestamp BEFORE
    source_timestamp (clock-skew), compute_reaction_latency_per_strategy
    silently clips them to 0 ms — but the runner's negative_latency_count
    SURFACES the count to the report (per cycle-22 LOW caveat carry-forward:
    operator visibility is required, not silent swallowing).

    Setup: 5 ticks with -50ms latency (zeus_ts before source_ts).
    Expected: negative_latency_count == 5.
    """
    db_path = _make_temp_db(tmp_path)
    end = date(2026, 4, 28)
    cur_tick_day = end - timedelta(days=1)

    conn = sqlite3.connect(str(db_path))
    try:
        _insert_position_current(
            conn,
            position_id="neg-pos",
            token_id="neg-tok",
            strategy_key="center_buy",
            target_date=cur_tick_day.isoformat(),
        )
        # 5 negative-latency ticks: source_ts AFTER zeus_ts by 50ms each.
        # zeus_ts at 12:00:00.000, source_ts at 12:00:00.050 → -50ms latency.
        for i in range(5):
            zeus_ts = f"{cur_tick_day.isoformat()}T12:00:0{i}.000+00:00"
            source_ts = f"{cur_tick_day.isoformat()}T12:00:0{i}.050+00:00"
            _insert_tick(
                conn,
                token_id="neg-tok",
                source_ts=source_ts,
                zeus_ts=zeus_ts,
            )
        conn.commit()
    finally:
        conn.close()

    report = run_weekly(db_path, end_date=end, window_days=7)
    assert report["negative_latency_count"] == 5


def test_canonical_cli_invocation_from_foreign_cwd(tmp_path: Path):
    """RELATIONSHIP regression for LOW-OPERATIONAL-WP-3-1: canonical CLI
    invocation `python3 scripts/<name>_weekly.py` MUST work from any cwd
    without requiring PYTHONPATH=. or `python -m scripts.X` workarounds.

    Pins the sys.path.insert(0, REPO_ROOT) bootstrap added at the top of
    all 3 sibling weekly runners. Subprocess-invokes each from /tmp (a
    foreign cwd) with --db-path pointing to a temp DB and --report-out
    to a temp file. Exit must be 0 (empty DB → no decay/drift/gap).

    Covers ALL 3 sibling weekly runners coherently in one test (one
    fix, one regression test). If any runner regresses to the
    pre-fix ModuleNotFoundError state, this single test fails and
    points to the load-bearing line.
    """
    import subprocess

    db_path = _make_temp_db(tmp_path)
    venv_python = REPO_ROOT / ".venv" / "bin" / "python"
    if not venv_python.exists():
        pytest.skip(f"venv python not found at {venv_python}")

    runners = [
        ("edge_observation_weekly.py",   ["--n-windows", "4"]),
        ("attribution_drift_weekly.py",  []),
        ("ws_poll_reaction_weekly.py",   ["--n-windows", "4"]),
    ]
    for runner_name, extra_flags in runners:
        runner = REPO_ROOT / "scripts" / runner_name
        out_file = tmp_path / f"{runner_name}.json"
        result = subprocess.run(
            [
                str(venv_python), str(runner),
                "--db-path", str(db_path),
                "--end-date", "2026-04-28",
                "--report-out", str(out_file),
                *extra_flags,
            ],
            cwd="/tmp",  # foreign cwd; the bug pre-fix only manifested off-repo-root
            capture_output=True, text=True,
        )
        # No ModuleNotFoundError; exit clean (empty DB → no decay/drift/gap → 0).
        assert "ModuleNotFoundError" not in result.stderr, (
            f"{runner_name} regressed LOW-OPERATIONAL-WP-3-1: {result.stderr[:500]}"
        )
        assert result.returncode == 0, (
            f"{runner_name} exit {result.returncode} (expected 0); "
            f"stderr={result.stderr[:500]}"
        )
        # Report written + parseable JSON.
        assert out_file.exists(), f"{runner_name} did not write report to {out_file}"
        re_loaded = json.loads(out_file.read_text())
        assert re_loaded.get("end_date") == "2026-04-28"
