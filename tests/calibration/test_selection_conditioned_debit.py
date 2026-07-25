# Lifecycle: created=2026-07-25; last_reviewed=2026-07-25; last_reused=never
# Purpose: Unit + walk-forward-boundary tests for the selection-conditioned
#   overconfidence debit (src/calibration/selection_conditioned_debit.py) — the
#   margin-slot fix for the measured mid-price-band winner's-curse regression.
"""Tests for src/calibration/selection_conditioned_debit.py."""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from src.calibration.selection_conditioned_debit import (
    DISAGREEMENT_HIGH_THRESHOLD,
    SHRINKAGE_N0,
    cluster_residuals_by_state,
    compute_selection_debit,
    decision_state,
    load_walk_forward_selection_debit,
)


# ---------------------------------------------------------------------------
# decision_state
# ---------------------------------------------------------------------------

def test_decision_state_ordinary_below_threshold():
    assert decision_state(0.55, 0.50) == "ordinary"


def test_decision_state_high_at_threshold_boundary():
    # Exactly at threshold classifies HIGH (>=), matching the module's >= contract.
    assert decision_state(0.5 + DISAGREEMENT_HIGH_THRESHOLD, 0.5) == "high"


def test_decision_state_symmetric_in_sign():
    # |q_decision - price| is symmetric; sign of the disagreement never matters.
    assert decision_state(0.9, 0.4) == decision_state(0.4, 0.9) == "high"


# ---------------------------------------------------------------------------
# compute_selection_debit (pure shrinkage/clamp)
# ---------------------------------------------------------------------------

def test_compute_selection_debit_empty_residuals_is_zero():
    debit = compute_selection_debit([], state="high")
    assert debit.d_t == 0.0
    assert debit.effective_n == 0
    assert debit.mean_residual == 0.0


def test_compute_selection_debit_one_sided_never_a_bonus():
    # Mean residual negative (underconfident: q_lcb understated the win rate) ->
    # d_t clamps to 0.0, never a negative "debit" (which would act as a bonus).
    residuals = [-0.3, -0.2, -0.25, -0.1] * 10  # n=40, mean=-0.2125
    debit = compute_selection_debit(residuals, state="ordinary")
    assert debit.mean_residual < 0.0
    assert debit.d_t == 0.0


def test_compute_selection_debit_shrinks_toward_zero_at_small_n():
    residuals = [0.2]  # n=1
    debit = compute_selection_debit(residuals, state="high", n0=SHRINKAGE_N0)
    lam = 1.0 / (1.0 + SHRINKAGE_N0)
    assert debit.d_t == pytest.approx(lam * 0.2)
    assert debit.d_t < 0.2  # thin evidence shrinks well below the raw mean


def test_compute_selection_debit_trusts_local_mean_at_large_n():
    residuals = [0.2] * 2000  # n=2000, mean_residual=0.2
    debit = compute_selection_debit(residuals, state="high", n0=SHRINKAGE_N0)
    lam = 2000.0 / (2000.0 + SHRINKAGE_N0)
    assert debit.d_t == pytest.approx(lam * 0.2)
    assert debit.d_t > 0.19  # thick evidence trusts the local mean almost fully


def test_compute_selection_debit_deterministic():
    residuals = [0.1, 0.2, -0.05, 0.3, 0.0]
    first = compute_selection_debit(residuals, state="ordinary")
    second = compute_selection_debit(list(residuals), state="ordinary")
    assert first == second


# ---------------------------------------------------------------------------
# DB-backed walk-forward query
# ---------------------------------------------------------------------------

def _make_world_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE settlement_attribution (
            attribution_id TEXT PRIMARY KEY,
            position_id TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            q_live REAL,
            avg_fill_price REAL,
            q_lcb_5pct REAL,
            won INTEGER,
            settled_at TEXT
        )
        """
    )
    return conn


def _insert_row(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    q_live: float,
    avg_fill_price: float,
    q_lcb_5pct: float,
    won: int,
    settled_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO settlement_attribution (
            attribution_id, position_id, city, target_date, temperature_metric,
            q_live, avg_fill_price, q_lcb_5pct, won, settled_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()), str(uuid.uuid4()), city, target_date, metric,
            q_live, avg_fill_price, q_lcb_5pct, won, settled_at,
        ),
    )
    conn.commit()


def test_cluster_residuals_by_state_deduplicates_correlated_rows():
    """Two rows on the SAME (city, target_date, metric) cluster contribute ONE
    residual observation, not two — the single-count-cluster contract."""
    conn = _make_world_db()
    # Same cluster, two sibling-bin positions on the same market instance.
    _insert_row(
        conn, city="Denver", target_date="2026-07-01", metric="high",
        q_live=0.90, avg_fill_price=0.50, q_lcb_5pct=0.80, won=0,
        settled_at="2026-07-02T00:00:00Z",
    )
    _insert_row(
        conn, city="Denver", target_date="2026-07-01", metric="high",
        q_live=0.85, avg_fill_price=0.45, q_lcb_5pct=0.70, won=1,
        settled_at="2026-07-02T00:00:00Z",
    )
    by_state = cluster_residuals_by_state(conn, decision_time_iso="2026-07-10T00:00:00Z")
    # Both rows belong to one cluster -> exactly one residual observation total.
    assert sum(len(v) for v in by_state.values()) == 1


def test_cluster_residuals_by_state_walk_forward_boundary_excludes_future_settlement():
    """A settlement AFTER decision_time must not contribute (no look-ahead)."""
    conn = _make_world_db()
    _insert_row(
        conn, city="Miami", target_date="2026-07-01", metric="high",
        q_live=0.90, avg_fill_price=0.50, q_lcb_5pct=0.80, won=0,
        settled_at="2026-07-15T00:00:00Z",  # settles AFTER the decision below
    )
    by_state = cluster_residuals_by_state(conn, decision_time_iso="2026-07-05T00:00:00Z")
    assert sum(len(v) for v in by_state.values()) == 0


def test_cluster_residuals_by_state_splits_ordinary_vs_high():
    conn = _make_world_db()
    # Ordinary: small disagreement, q_lcb overstates the loss slightly.
    _insert_row(
        conn, city="Paris", target_date="2026-07-01", metric="high",
        q_live=0.55, avg_fill_price=0.50, q_lcb_5pct=0.50, won=0,
        settled_at="2026-07-02T00:00:00Z",
    )
    # High: large disagreement (>= 0.40), q_lcb badly overstates the win.
    _insert_row(
        conn, city="Tokyo", target_date="2026-07-01", metric="high",
        q_live=0.90, avg_fill_price=0.50, q_lcb_5pct=0.80, won=0,
        settled_at="2026-07-02T00:00:00Z",
    )
    by_state = cluster_residuals_by_state(conn, decision_time_iso="2026-07-10T00:00:00Z")
    assert by_state["ordinary"] == pytest.approx([0.50])
    assert by_state["high"] == pytest.approx([0.80])


def test_load_walk_forward_selection_debit_end_to_end():
    conn = _make_world_db()
    for i in range(30):
        _insert_row(
            conn, city=f"City{i}", target_date="2026-07-01", metric="high",
            q_live=0.90, avg_fill_price=0.50, q_lcb_5pct=0.80, won=0,
            settled_at="2026-07-02T00:00:00Z",
        )
    result = load_walk_forward_selection_debit(conn, decision_time_iso="2026-07-10T00:00:00Z")
    high = result["high"]
    assert high.effective_n == 30
    assert high.mean_residual == pytest.approx(0.80)
    lam = 30.0 / (30.0 + SHRINKAGE_N0)
    assert high.d_t == pytest.approx(lam * 0.80)
    assert result["ordinary"].effective_n == 0
    assert result["ordinary"].d_t == 0.0


def test_load_walk_forward_selection_debit_deterministic_same_history():
    conn = _make_world_db()
    _insert_row(
        conn, city="Rome", target_date="2026-07-01", metric="high",
        q_live=0.90, avg_fill_price=0.50, q_lcb_5pct=0.80, won=0,
        settled_at="2026-07-02T00:00:00Z",
    )
    first = load_walk_forward_selection_debit(conn, decision_time_iso="2026-07-10T00:00:00Z")
    second = load_walk_forward_selection_debit(conn, decision_time_iso="2026-07-10T00:00:00Z")
    assert first == second
