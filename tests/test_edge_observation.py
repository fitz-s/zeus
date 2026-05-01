# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: round3_verdict.md §1 #2 (FIRST edge packet) + ULTIMATE_PLAN.md
# L297-301 (alpha-decay tracker per strategy_key, weekly drift assertion). Per
# Fitz "test relationships, not just functions" — these tests verify the
# K1-compliant cross-module read path: canonical position_events → dedup +
# normalize via query_authoritative_settlement_rows → per-strategy aggregation
# in compute_realized_edge_per_strategy. The dedup invariant (no phantom-PnL
# trap) is exercised by test_per_strategy_aggregation_correctness which inserts
# the same trade_id twice and asserts only one settlement is counted.
"""BATCH 1 tests for edge_observation.compute_realized_edge_per_strategy.

Six relationship tests (per dispatch §"BATCH 1" + boot §2):

  1. test_per_strategy_aggregation_correctness — synthetic in-memory DB with
     known outcomes/p_posterior; verify mean-edge math + win_rate
  2. test_sample_quality_boundaries — exactly 10/30/100 trade boundaries
  3. test_empty_result_safety — no rows → all 4 strategies return n_trades=0
  4. test_degraded_rows_excluded — degraded row should not contribute
  5. test_window_filter — settled_at outside window → excluded
  6. test_strategy_filter_only_4_known — unknown strategy_key → quarantined
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from src.state.db import init_schema
from src.state.edge_observation import (
    STRATEGY_KEYS,
    _classify_sample_quality,
    compute_realized_edge_per_strategy,
)


# --- Helpers ---------------------------------------------------------------


def _make_conn() -> sqlite3.Connection:
    """Create an in-memory DB with the canonical schema applied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    init_schema(conn)
    return conn


def _insert_settled(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    strategy: str,
    settled_at: str,
    outcome: int,
    p_posterior: float,
    won: bool | None = None,
    pnl: float = 1.0,
    is_degraded_payload: bool = False,
    seq_no: int = 1,
) -> None:
    """Insert one SETTLED position_event row (canonical surface)."""
    if won is None:
        won = (outcome == 1)
    payload = {
        "contract_version": "position_settled.v1",
        "winning_bin": "39-40°F",
        "position_bin": "39-40°F" if not is_degraded_payload else None,
        "won": won,
        "outcome": outcome,
        "p_posterior": p_posterior,
        "exit_price": 1.0,
        "pnl": pnl,
        "exit_reason": "SETTLEMENT",
    }
    if is_degraded_payload:
        # Make the row degraded by removing required field outcome.
        # _normalize_position_settlement_event marks rows missing required
        # fields (p_posterior, outcome, pnl, won, etc.) as is_degraded=True.
        payload.pop("outcome", None)
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, strategy_key, source_module, payload_json
        ) VALUES (?, ?, 1, ?, 'SETTLED', ?, ?, 'tests', ?)
        """,
        (
            f"{position_id}:settled:{seq_no}", position_id, seq_no,
            settled_at, strategy, json.dumps(payload),
        ),
    )
    # position_current row is needed for the LEFT JOIN in query_settlement_events
    # to surface city / target_date / bin_label / direction.
    conn.execute(
        """
        INSERT OR IGNORE INTO position_current
            (position_id, phase, strategy_key, updated_at, city, target_date,
             bin_label, direction, market_id, edge_source, size_usd, shares,
             cost_basis_usd, entry_price, unit, temperature_metric)
        VALUES (?, 'settled', ?, ?, 'TestCity', '2026-04-15', '39-40°F',
                'buy_yes', 'm-test', '', 10.0, 100, 10.0, 0.5, 'F', 'high')
        """,
        (position_id, strategy, settled_at),
    )
    conn.commit()


# --- Tests -----------------------------------------------------------------


def test_per_strategy_aggregation_correctness():
    """RELATIONSHIP: per-strategy edge_realized = mean(outcome - p_posterior).

    Three settlement_capture trades: (outcome=1, p=0.6), (outcome=0, p=0.4),
    (outcome=1, p=0.5). Edge sum = 0.4 + (-0.4) + 0.5 = 0.5; mean = 0.5/3.
    Two center_buy trades: (outcome=1, p=0.7), (outcome=0, p=0.3). Edge sum =
    0.3 + (-0.3) = 0.0; mean = 0.0. Win rates: settlement_capture 2/3, center_buy 1/2.

    Inserts one trade twice (same position_id) → query_authoritative_settlement_rows
    dedupe via ROW_NUMBER() must keep only the most-recent. This guards the
    phantom-PnL trap from strategy_tracker.py:8.
    """
    conn = _make_conn()
    base = "2026-04-20T12:00:00+00:00"
    _insert_settled(conn, position_id="p1", strategy="settlement_capture",
                    settled_at=base, outcome=1, p_posterior=0.6)
    _insert_settled(conn, position_id="p2", strategy="settlement_capture",
                    settled_at=base, outcome=0, p_posterior=0.4)
    _insert_settled(conn, position_id="p3", strategy="settlement_capture",
                    settled_at=base, outcome=1, p_posterior=0.5)
    _insert_settled(conn, position_id="p4", strategy="center_buy",
                    settled_at=base, outcome=1, p_posterior=0.7)
    _insert_settled(conn, position_id="p5", strategy="center_buy",
                    settled_at=base, outcome=0, p_posterior=0.3)
    # Same trade twice (sequence_no=2 supersedes sequence_no=1) — dedupe must keep one.
    _insert_settled(conn, position_id="p1", strategy="settlement_capture",
                    settled_at=base, outcome=1, p_posterior=0.6, seq_no=2)

    result = compute_realized_edge_per_strategy(conn, window_days=14, end_date="2026-04-28")
    sc = result["settlement_capture"]
    assert sc["n_trades"] == 3, f"phantom-PnL drift: dedup failed; got {sc['n_trades']}, expected 3"
    assert sc["n_wins"] == 2
    assert abs(sc["win_rate"] - 2/3) < 1e-9
    assert abs(sc["edge_realized"] - 0.5/3) < 1e-9, f"edge math; got {sc['edge_realized']}"

    cb = result["center_buy"]
    assert cb["n_trades"] == 2
    assert abs(cb["edge_realized"] - 0.0) < 1e-9
    # Other strategies untouched.
    assert result["shoulder_sell"]["n_trades"] == 0
    assert result["opening_inertia"]["n_trades"] == 0


def test_sample_quality_boundaries():
    """RELATIONSHIP: classifier crosses tier boundaries at exactly 10, 30, 100."""
    assert _classify_sample_quality(0) == "insufficient"
    assert _classify_sample_quality(9) == "insufficient"
    assert _classify_sample_quality(10) == "low"
    assert _classify_sample_quality(29) == "low"
    assert _classify_sample_quality(30) == "adequate"
    assert _classify_sample_quality(99) == "adequate"
    assert _classify_sample_quality(100) == "high"
    assert _classify_sample_quality(1000) == "high"


def test_empty_result_safety():
    """RELATIONSHIP: empty DB → all 4 strategies present with n_trades=0."""
    conn = _make_conn()
    result = compute_realized_edge_per_strategy(conn, end_date="2026-04-28")
    assert set(result.keys()) == set(STRATEGY_KEYS)
    for sk, rec in result.items():
        assert rec["n_trades"] == 0
        assert rec["n_wins"] == 0
        assert rec["edge_realized"] is None
        assert rec["win_rate"] is None
        assert rec["sample_quality"] == "insufficient"
        assert rec["window_start"] == "2026-04-21"  # 2026-04-28 - 7 days
        assert rec["window_end"] == "2026-04-28"


def test_degraded_rows_excluded():
    """RELATIONSHIP: metric_ready=False rows must not enter edge math.

    Per K0_frozen_kernel + db.py:3345-3346 distinction: rows missing required
    fields (outcome, p_posterior, etc.) have metric_ready=False — they
    structurally cannot be measured. Rows missing only decision_snapshot_id
    have metric_ready=True (degraded for LEARNING but valid for MEASUREMENT).
    Insert one good row + one degraded row (missing outcome). Only the good
    row counts.
    """
    conn = _make_conn()
    base = "2026-04-20T12:00:00+00:00"
    _insert_settled(conn, position_id="g1", strategy="opening_inertia",
                    settled_at=base, outcome=1, p_posterior=0.5)
    _insert_settled(conn, position_id="d1", strategy="opening_inertia",
                    settled_at=base, outcome=0, p_posterior=0.0,
                    is_degraded_payload=True)
    result = compute_realized_edge_per_strategy(conn, window_days=14, end_date="2026-04-28")
    rec = result["opening_inertia"]
    assert rec["n_trades"] == 1, f"degraded row leaked into edge: n_trades={rec['n_trades']}"
    assert abs(rec["edge_realized"] - 0.5) < 1e-9


def test_window_filter():
    """RELATIONSHIP: settled_at outside [end - window_days, end] excluded."""
    conn = _make_conn()
    # In-window: settled 5 days before end_date.
    _insert_settled(conn, position_id="in1", strategy="shoulder_sell",
                    settled_at="2026-04-23T12:00:00+00:00", outcome=1, p_posterior=0.4)
    # Out-of-window (too old): 30 days before end_date.
    _insert_settled(conn, position_id="old1", strategy="shoulder_sell",
                    settled_at="2026-03-29T12:00:00+00:00", outcome=0, p_posterior=0.5)
    # Out-of-window (after end_date).
    _insert_settled(conn, position_id="future1", strategy="shoulder_sell",
                    settled_at="2026-04-29T12:00:00+00:00", outcome=1, p_posterior=0.4)
    result = compute_realized_edge_per_strategy(conn, window_days=7, end_date="2026-04-28")
    rec = result["shoulder_sell"]
    assert rec["n_trades"] == 1, f"window filter; got {rec['n_trades']}, expected 1"
    assert abs(rec["edge_realized"] - 0.6) < 1e-9


def test_strategy_filter_only_4_known():
    """RELATIONSHIP: only the 4 governed strategy_keys appear in output.

    A position with strategy_key not in the canonical 4 cannot be inserted into
    position_events (CHECK constraint at schema:53-58 enforces). This test
    confirms the schema contract holds AND the function still returns all 4
    keys when only a subset has trades.
    """
    conn = _make_conn()
    base = "2026-04-20T12:00:00+00:00"
    _insert_settled(conn, position_id="x1", strategy="opening_inertia",
                    settled_at=base, outcome=1, p_posterior=0.5)
    # Schema CHECK rejects unknown strategy_key — this is the antibody.
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        _insert_settled(conn, position_id="bad1", strategy="not_a_real_strategy",
                        settled_at=base, outcome=1, p_posterior=0.5)
    result = compute_realized_edge_per_strategy(conn, window_days=14, end_date="2026-04-28")
    # All 4 always present.
    assert set(result.keys()) == set(STRATEGY_KEYS)
    # Only opening_inertia has trades.
    assert result["opening_inertia"]["n_trades"] == 1
    for sk in ["settlement_capture", "shoulder_sell", "center_buy"]:
        assert result[sk]["n_trades"] == 0


# =====================================================================
# BATCH 2 — detect_alpha_decay tests
# =====================================================================
# Per dispatch §"BATCH 2" + boot §2: 6-8 tests covering steady/sudden/gradual
# decay patterns, threshold boundary, insufficient-history graceful, per-
# strategy threshold override, negative-trailing-mean handling.

from src.state.edge_observation import (
    DEFAULT_DECAY_RATIO_THRESHOLD,
    DriftVerdict,
    detect_alpha_decay,
)


def _window(edge: float | None, n_trades: int = 50) -> dict:
    """Build one synthetic window record as compute_realized_edge_per_strategy
    would emit. n_trades=50 → sample_quality='adequate' (>=30, <100)."""
    if edge is None:
        return {
            "edge_realized": None, "n_trades": 0, "n_wins": 0, "win_rate": None,
            "sample_quality": "insufficient", "window_start": "x", "window_end": "x",
        }
    # Approximate sample_quality from n_trades using same boundaries.
    if n_trades < 10:
        sq = "insufficient"
    elif n_trades < 30:
        sq = "low"
    elif n_trades < 100:
        sq = "adequate"
    else:
        sq = "high"
    return {
        "edge_realized": edge, "n_trades": n_trades,
        "n_wins": int(n_trades * (0.5 + edge / 2)),
        "win_rate": 0.5 + edge / 2, "sample_quality": sq,
        "window_start": "x", "window_end": "x",
    }


def test_decay_detected_on_sudden_drop():
    """RELATIONSHIP: 4 trailing windows at edge=0.10, current at edge=0.02 →
    ratio = 0.02/0.10 = 0.2 < threshold 0.5 AND < critical cutoff 0.3 →
    alpha_decay_detected with critical severity."""
    history = [_window(0.10), _window(0.10), _window(0.10), _window(0.10), _window(0.02)]
    v = detect_alpha_decay(history, "settlement_capture")
    assert isinstance(v, DriftVerdict)
    assert v.kind == "alpha_decay_detected"
    assert v.severity == "critical"
    assert v.strategy_key == "settlement_capture"
    assert abs(v.evidence["ratio"] - 0.2) < 1e-9
    assert abs(v.evidence["trailing_mean"] - 0.10) < 1e-9


def test_decay_detected_warn_severity_at_intermediate_ratio():
    """RELATIONSHIP: ratio between critical cutoff (0.3) and threshold (0.5)
    yields warn severity, not critical. trailing_mean=0.10, current=0.04 →
    ratio=0.4 → warn."""
    history = [_window(0.10), _window(0.10), _window(0.10), _window(0.10), _window(0.04)]
    v = detect_alpha_decay(history, "shoulder_sell")
    assert v.kind == "alpha_decay_detected"
    assert v.severity == "warn"
    assert abs(v.evidence["ratio"] - 0.4) < 1e-9


def test_within_normal_range_when_steady():
    """RELATIONSHIP: 4 trailing at 0.10, current at 0.09 → ratio=0.9 → within."""
    history = [_window(0.10), _window(0.10), _window(0.10), _window(0.10), _window(0.09)]
    v = detect_alpha_decay(history, "center_buy")
    assert v.kind == "within_normal_range"
    assert v.severity is None
    assert abs(v.evidence["ratio"] - 0.9) < 1e-9


def test_threshold_boundary_exactly_at_cutoff():
    """RELATIONSHIP: at ratio == decay_ratio_threshold the verdict is
    within_normal_range (strict less-than triggers decay). trailing=0.10,
    current=0.05 → ratio=0.5 == threshold → within."""
    history = [_window(0.10), _window(0.10), _window(0.10), _window(0.10), _window(0.05)]
    v = detect_alpha_decay(history, "opening_inertia")
    assert v.kind == "within_normal_range", \
        f"strict less-than should NOT trigger at exactly threshold; got {v.kind}"


def test_critical_cutoff_boundary_exactly_at_0_3():
    """RELATIONSHIP: at ratio == CRITICAL_RATIO_CUTOFF (0.3) severity is
    'warn', not 'critical' (strict less-than for the critical/warn split:
    `severity = "critical" if ratio < CRITICAL_RATIO_CUTOFF else "warn"`).
    Symmetric to test_threshold_boundary_exactly_at_cutoff for the
    decay/normal split. Per critic-harness BATCH 2 review LOW-CAVEAT-EO-2-2.

    trailing=0.10, current=0.03 → ratio=0.3 → alpha_decay_detected
    (because 0.3 < 0.5 threshold) with severity=warn (because 0.3 is NOT
    < 0.3 critical cutoff).
    """
    history = [_window(0.10), _window(0.10), _window(0.10), _window(0.10), _window(0.03)]
    v = detect_alpha_decay(history, "settlement_capture")
    assert v.kind == "alpha_decay_detected", \
        f"ratio 0.3 < threshold 0.5 should trigger decay; got {v.kind}"
    assert v.severity == "warn", \
        f"strict less-than at critical cutoff 0.3 should yield 'warn' not 'critical'; got {v.severity!r}"
    assert abs(v.evidence["ratio"] - 0.3) < 1e-9


def test_insufficient_history_below_min_windows():
    """RELATIONSHIP: 3 windows when min_windows=4 → insufficient_data."""
    history = [_window(0.10), _window(0.10), _window(0.10)]
    v = detect_alpha_decay(history, "settlement_capture")
    assert v.kind == "insufficient_data"
    assert v.evidence["reason"] == "n_windows_below_min"
    assert v.evidence["n_windows"] == 3
    assert v.evidence["min_required"] == 4


def test_insufficient_when_too_many_low_sample_windows():
    """RELATIONSHIP: 5 windows but 3 are sample_quality='insufficient' (n<10)
    → only 2 usable, below min_windows=4 → insufficient_data."""
    history = [
        _window(0.10, n_trades=5),    # insufficient
        _window(0.10, n_trades=5),    # insufficient
        _window(0.10, n_trades=5),    # insufficient
        _window(0.10, n_trades=50),   # usable
        _window(0.05, n_trades=50),   # usable
    ]
    v = detect_alpha_decay(history, "shoulder_sell")
    assert v.kind == "insufficient_data"
    assert v.evidence["reason"] == "usable_windows_below_min"
    assert v.evidence["n_usable"] == 2


def test_insufficient_when_trailing_mean_non_positive():
    """RELATIONSHIP: trailing edges average to <=0 → ratio undefined →
    insufficient_data (a strategy that never had positive edge cannot
    meaningfully 'decay')."""
    history = [_window(-0.05), _window(-0.02), _window(0.0), _window(-0.01), _window(-0.10)]
    v = detect_alpha_decay(history, "center_buy")
    assert v.kind == "insufficient_data"
    assert v.evidence["reason"] == "trailing_mean_non_positive"
    assert v.evidence["trailing_mean"] <= 0


def test_per_call_threshold_override():
    """RELATIONSHIP: caller can pass strict-er threshold to detect smaller
    decays. trailing=0.10, current=0.07 → ratio=0.7. Default threshold 0.5
    → within_normal. Override threshold=0.8 → alpha_decay_detected."""
    history = [_window(0.10), _window(0.10), _window(0.10), _window(0.10), _window(0.07)]
    v_default = detect_alpha_decay(history, "opening_inertia")
    assert v_default.kind == "within_normal_range"
    v_strict = detect_alpha_decay(history, "opening_inertia", decay_ratio_threshold=0.8)
    assert v_strict.kind == "alpha_decay_detected"
    assert v_strict.severity == "warn"  # 0.7 > 0.3 critical cutoff
    assert abs(v_strict.evidence["decay_ratio_threshold"] - 0.8) < 1e-9
