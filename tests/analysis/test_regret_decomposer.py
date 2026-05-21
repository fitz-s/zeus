# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase6_evidence_ladder/PHASE_6_PLAN.md §T3
"""T3 invariant tests for RegretDecomposer."""
from __future__ import annotations

import sqlite3

import pytest

from src.analysis.regret_decomposer import (
    RegretComponents,
    decompose_regret,
    write_regret_decomposition,
)
from src.state.db import init_schema
from src.state.shadow_experiment_registry import register_shadow_experiment


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def world_conn() -> sqlite3.Connection:
    """In-memory world DB with full schema."""
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# T3-1: 7-component sum == total within 1e-9
# ---------------------------------------------------------------------------

def test_t3_sum_equals_total() -> None:
    """7 components sum to total_regret_usd within 1e-9."""
    # components sum: -0.10 + -0.05 + -0.02 + 0.00 + -0.01 + -0.03 + -0.04 = -0.25
    # realized=1.00, counterfactual=1.25 → total = -0.25
    components = decompose_regret(
        forecast_error_usd=-0.10,
        observation_error_usd=-0.05,
        quote_error_usd=-0.02,
        non_fill_error_usd=0.00,
        fee_error_usd=-0.01,
        timing_error_usd=-0.03,
        settlement_ambiguity_error_usd=-0.04,
        realized_pnl_usd=1.00,
        counterfactual_pnl_usd=1.25,   # total_regret = 1.00 - 1.25 = -0.25
    )
    expected_total = 1.00 - 1.25
    assert abs(components.total_regret_usd - expected_total) < 1e-9
    component_sum = (
        components.forecast_error_usd
        + components.observation_error_usd
        + components.quote_error_usd
        + components.non_fill_error_usd
        + components.fee_error_usd
        + components.timing_error_usd
        + components.settlement_ambiguity_error_usd
    )
    assert abs(component_sum - components.total_regret_usd) < 1e-9


def test_t3_sum_positive_regret() -> None:
    """Positive total regret (realized > counterfactual) sums correctly."""
    components = decompose_regret(
        forecast_error_usd=0.30,
        observation_error_usd=0.00,
        quote_error_usd=0.00,
        non_fill_error_usd=0.00,
        fee_error_usd=0.00,
        timing_error_usd=0.00,
        settlement_ambiguity_error_usd=0.00,
        realized_pnl_usd=1.50,
        counterfactual_pnl_usd=1.20,   # total_regret = +0.30
    )
    assert abs(components.total_regret_usd - 0.30) < 1e-9
    components.verify_sum()


# ---------------------------------------------------------------------------
# T3-2: zero-alpha → near-zero components
# ---------------------------------------------------------------------------

def test_t3_zero_alpha_near_zero() -> None:
    """Zero regret (realized == counterfactual) → all components zero."""
    components = decompose_regret(
        realized_pnl_usd=1.00,
        counterfactual_pnl_usd=1.00,
    )
    assert abs(components.total_regret_usd) < 1e-9
    components.verify_sum()
    assert abs(components.forecast_error_usd) < 1e-9
    assert abs(components.settlement_ambiguity_error_usd) < 1e-9


# ---------------------------------------------------------------------------
# T3-3: non-fill → non_fill_error_usd non-zero
# ---------------------------------------------------------------------------

def test_t3_non_fill_error_nonzero() -> None:
    """Non-fill scenario: non_fill_error_usd is non-zero."""
    components = decompose_regret(
        non_fill_error_usd=-0.50,   # missed fill = negative regret (we avoided loss)
        timing_error_usd=0.10,
        realized_pnl_usd=0.50,
        counterfactual_pnl_usd=0.90,  # total = -0.40 = -0.50 + 0.10
    )
    assert components.non_fill_error_usd != 0.0
    components.verify_sum()


# ---------------------------------------------------------------------------
# T3-4: column named settlement_ambiguity_error_usd exists in DB schema
# ---------------------------------------------------------------------------

def test_t3_column_settlement_ambiguity_error_usd_exists(world_conn) -> None:
    """regret_decompositions table has settlement_ambiguity_error_usd column."""
    columns = {
        row[1]
        for row in world_conn.execute(
            "PRAGMA table_info(regret_decompositions)"
        ).fetchall()
    }
    assert "settlement_ambiguity_error_usd" in columns, (
        f"Expected settlement_ambiguity_error_usd in {columns}"
    )


# ---------------------------------------------------------------------------
# T3-5: write_regret_decomposition roundtrip
# ---------------------------------------------------------------------------

def test_t3_write_roundtrip(world_conn) -> None:
    """write_regret_decomposition inserts a row; values roundtrip correctly."""
    from datetime import datetime, timezone

    # Register a shadow experiment to satisfy the FK-like reference
    from src.state.shadow_experiment_registry import register_shadow_experiment
    experiment_id = register_shadow_experiment(
        "shoulder_sell", {"kelly": 0.0}, "cohort_t3",
        started_at=datetime(2026, 5, 21, 10, 0, 0, tzinfo=timezone.utc),
        conn=world_conn,
    )

    # components sum: -0.10 + -0.05 + 0.00 + 0.00 + -0.01 + -0.04 + -0.05 = -0.25
    # realized=1.00, counterfactual=1.25 → total = -0.25
    components = decompose_regret(
        forecast_error_usd=-0.10,
        observation_error_usd=-0.05,
        quote_error_usd=0.00,
        non_fill_error_usd=0.00,
        fee_error_usd=-0.01,
        timing_error_usd=-0.04,
        settlement_ambiguity_error_usd=-0.05,
        realized_pnl_usd=1.00,
        counterfactual_pnl_usd=1.25,
    )

    rowid = write_regret_decomposition(
        experiment_id=experiment_id,
        decision_event_id="de_test_001",
        components=components,
        conn=world_conn,
        computed_at=datetime(2026, 5, 21, 11, 0, 0, tzinfo=timezone.utc),
    )
    assert rowid is not None and rowid > 0

    row = world_conn.execute(
        "SELECT settlement_ambiguity_error_usd, total_regret_usd FROM regret_decompositions WHERE id = ?",
        (rowid,),
    ).fetchone()
    assert abs(row[0] - (-0.05)) < 1e-9
    assert abs(row[1] - (1.00 - 1.25)) < 1e-9


# ---------------------------------------------------------------------------
# T3-6: verify_sum raises ValueError on imbalanced components
# ---------------------------------------------------------------------------

def test_t3_verify_sum_raises_on_imbalance() -> None:
    """RegretComponents.verify_sum raises ValueError if sum != total."""
    # Manually construct an imbalanced record
    bad = RegretComponents(
        forecast_error_usd=1.0,
        observation_error_usd=0.0,
        quote_error_usd=0.0,
        non_fill_error_usd=0.0,
        fee_error_usd=0.0,
        timing_error_usd=0.0,
        settlement_ambiguity_error_usd=0.0,
        total_regret_usd=2.0,   # deliberately wrong
    )
    with pytest.raises(ValueError, match="sum="):
        bad.verify_sum()
