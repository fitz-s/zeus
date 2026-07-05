"""Tests for K4 Slice J — Time & Quarantine fixes (Bugs #18, #57).

P0b (2026-07-04): Bug #57's four position-quarantine-timer tests below were
retired along with the 48h timer itself
(src.state.chain_reconciliation.check_quarantine_timeouts) — see
docs/rebuild/chain_mirror_state_model_2026-07-04.md §5 follow-up. "A 48h
timer on an invented state" (audit verdict); the chain-mirror reconciler's
two-consecutive-mirror-runs force-resolve (runs every ~10 minutes) replaces
it. check_quarantine_timeouts is retained ONLY for its unrelated ChainOnlyFact
48h review escalation — see tests/state/test_inv_part3_followups.py.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest


# ---------------------------------------------------------------------------
# Bug #18: obs_settled dead variable removed from day0_signal.py
# ---------------------------------------------------------------------------


def test_obs_settled_not_used():
    """Verify obs_settled dead variable is gone from Day0Signal.p_vector."""
    from src.signal import day0_signal

    source = textwrap.dedent(inspect.getsource(day0_signal.Day0Signal.p_vector))
    tree = ast.parse(source)

    # Check no assignment target named 'obs_settled' exists
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "obs_settled":
                    pytest.fail("obs_settled dead variable still exists in p_vector")
