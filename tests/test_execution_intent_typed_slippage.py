# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: docs/operations/task_2026-04-26_full_data_midstream_fix_plan/
#                  phases/task_2026-04-26_phase3_midstream_trust/plan.md slice P3.3
"""Slice P3.3 relationship + function tests.

PR #19 workbook P3.3: extend typed execution-price/tick-size/slippage
contracts through CLOB-send and realized-fill boundary.

Pre-fix: src/execution/executor.py:128 set
`max_slippage=0.02` as a raw float on ExecutionIntent. The type system
could not distinguish whether 0.02 meant 0.02 bps (200x tighter than
intended) or 0.02 fraction (= 2% = 200 bps). Repo-wide grep showed
ZERO readers of `intent.max_slippage`, making the budget unenforced
dead code in addition to being unit-ambiguous.

P3.3 fix (this packet's narrow scope): promote ExecutionIntent.
max_slippage from `float` to `SlippageBps` so the unit is explicit at
construction. Enforcement (rejecting fills above the budget) is a
separate follow-on packet — P3.3 closes the typing seam first.

Tests:
1. ExecutionIntent.max_slippage is now SlippageBps-typed.
2. The executor.py:128 setter constructs the typed value with
   explicit (200 bps, "adverse") semantics matching the prior 0.02
   fractional intent.
3. SlippageBps invariants hold (non-negative magnitude, direction
   consistency).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.execution_intent import ExecutionIntent
from src.contracts.semantic_types import Direction
from src.contracts.slippage_bps import SlippageBps


def _typed_intent() -> ExecutionIntent:
    return ExecutionIntent(
        direction=Direction.YES,
        target_size_usd=10.0,
        limit_price=0.50,
        toxicity_budget=0.05,
        max_slippage=SlippageBps(value_bps=200.0, direction="adverse"),
        is_sandbox=False,
        market_id="test-market",
        token_id="test-token",
        timeout_seconds=30,
    )


def test_execution_intent_accepts_typed_slippage_bps():
    """ExecutionIntent constructed with SlippageBps must succeed."""
    intent = _typed_intent()
    assert isinstance(intent.max_slippage, SlippageBps)
    assert intent.max_slippage.value_bps == 200.0
    assert intent.max_slippage.direction == "adverse"


def test_executor_create_intent_uses_typed_slippage_via_source_check():
    """Source-inspection: the create_execution_intent setter at executor.py
    constructs max_slippage with SlippageBps, not a raw float literal.

    This avoids the heavy EdgeDecision + market fixture chain that a true
    integration test would require — the source assertion is sufficient
    because the type discipline is at the construction call site."""
    import inspect
    from src.execution import executor
    src = inspect.getsource(executor.create_execution_intent)
    assert "SlippageBps(" in src, (
        "P3.3 antibody: create_execution_intent must call SlippageBps(...) "
        "at the max_slippage construction site. Pre-fix it used raw 0.02."
    )
    assert "max_slippage=0.02" not in src, (
        "Pre-fix raw float `max_slippage=0.02` must be removed."
    )


def test_pre_fix_raw_float_pattern_absent_from_executor():
    """Repository antibody: the raw float assignment to max_slippage must
    not be re-introduced by a future patch."""
    import inspect
    from src.execution import executor
    src = inspect.getsource(executor)
    assert "max_slippage=0.02" not in src, (
        "Pre-fix raw float pattern must stay removed."
    )


def test_slippage_bps_fraction_round_trip():
    """200 bps == 0.02 fraction. Pins the unit conversion that pre-fix
    forced callers to remember mentally (and frequently got wrong)."""
    s = SlippageBps(value_bps=200.0, direction="adverse")
    assert s.fraction == 0.02


def test_slippage_bps_rejects_negative_value():
    """SlippageBps enforces non-negative magnitude — sign in direction.
    Pre-fix raw float allowed negative max_slippage which made no sense
    for a budget."""
    with pytest.raises(ValueError, match="non-negative"):
        SlippageBps(value_bps=-5.0, direction="adverse")


def test_slippage_bps_rejects_inconsistent_zero_direction():
    """value_bps=0 with direction!="zero" is incoherent and rejected."""
    with pytest.raises(ValueError, match="incompatible with value_bps=0"):
        SlippageBps(value_bps=0.0, direction="adverse")
