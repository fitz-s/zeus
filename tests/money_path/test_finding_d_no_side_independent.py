# Created: 2026-06-12
# Last reused/audited: 2026-06-12
# Authority basis: external deep code review 2026-06-12 FINDING-D (operator direct-fix
#   order). The legacy canonical builder forced the buy-NO p_value/prefilter to
#   non-actionable (1.0/False) because scan_full_hypothesis_family never emits a NO
#   hypothesis -- so a real native-NO edge could never be admitted, regardless of the NO
#   posterior. The NO posterior (1 - q_ucb_yes) is a native authority available
#   independent of the YES token's market state.
"""FINDING-D relationship invariant: the buy-NO leg's admissibility is reconciled to its
OWN native NO cost and OWN native NO robust lower bound (q_lcb_no), INDEPENDENT of YES
executability. When the YES quote is absent/unexecutable but a native NO ask is present
and q_lcb_no > no_ask, a buy-NO candidate must exist (prefilter True, p_value 0.0). A
non-edge NO still gets p=1.0; a missing native NO ask is still non-actionable.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.engine.event_reactor_adapter import _native_no_edge_positivity


def _no_cost_entry(no_ask: float | None):
    """A native_costs tuple shaped (raw|None, ExecutionPrice|None, cost, ev|None, src|None).
    Only index [1].value (the native NO ask/VWMP price) is read by the invariant."""
    exec_price = SimpleNamespace(value=no_ask) if no_ask is not None else None
    return (None, exec_price, 0.0, None, None)


def test_no_present_yes_absent_edge_admits_buy_no():
    """YES quote absent (no buy_yes cost entry at all), native NO ask present at 0.72, and
    q_lcb_no = 0.86 > 0.72: a buy-NO candidate MUST exist (admissible, prefilter True)."""
    native_costs = {("cond-1", "buy_no"): _no_cost_entry(0.72)}
    p_value, prefilter = _native_no_edge_positivity(
        native_costs=native_costs,
        condition_id="cond-1",
        q_lcb_no=0.86,
    )
    assert p_value == 0.0
    assert prefilter is True


def test_no_present_but_non_edge_is_rejected():
    """Native NO ask present at 0.90 but q_lcb_no = 0.86 <= 0.90: honest non-edge, p=1.0,
    prefilter False (reconciliation never weakens the gate for a true non-edge)."""
    native_costs = {("cond-1", "buy_no"): _no_cost_entry(0.90)}
    p_value, prefilter = _native_no_edge_positivity(
        native_costs=native_costs,
        condition_id="cond-1",
        q_lcb_no=0.86,
    )
    assert p_value == 1.0
    assert prefilter is False


def test_no_ask_absent_is_non_actionable():
    """No native NO ask present at all: non-actionable (p=1.0/False) regardless of a strong
    NO posterior — the leg is gated by its OWN execution data (no complement price)."""
    native_costs = {}  # no NO cost entry
    p_value, prefilter = _native_no_edge_positivity(
        native_costs=native_costs,
        condition_id="cond-1",
        q_lcb_no=0.99,
    )
    assert p_value == 1.0
    assert prefilter is False


def test_admissibility_is_independent_of_yes_cost_presence():
    """The NO leg's verdict must NOT change based on whether a YES cost entry exists: the
    NO posterior is a native authority, not a function of the YES token's market state."""
    base = {("cond-1", "buy_no"): _no_cost_entry(0.70)}
    with_yes = dict(base)
    with_yes[("cond-1", "buy_yes")] = _no_cost_entry(0.30)  # YES present
    out_without_yes = _native_no_edge_positivity(
        native_costs=base, condition_id="cond-1", q_lcb_no=0.85
    )
    out_with_yes = _native_no_edge_positivity(
        native_costs=with_yes, condition_id="cond-1", q_lcb_no=0.85
    )
    assert out_without_yes == out_with_yes == (0.0, True)
