# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: operator directive 2026-06-08 — preflight must not present bare >51%
#   win-rate as the capital objective for prediction-market tokens.
"""
Operator-named test: assert the fully-armed terminal message in
scripts/preflight_restart_check.py does NOT present raw win-rate >51% as the
capital objective, and DOES name after-cost EV/PnL and q_lcb coverage.

Why: for prediction-market tokens raw win-rate is not the capital objective.
  A 0.90 token at 60% win = -0.30 EV; a 0.20 token at 40% win = +0.20 EV.
Correct objective: after-cost EV / PnL / log-growth + q_lcb coverage +
drawdown + fill quality + price-bucketed win-rate.
"""
from __future__ import annotations

import re

import pytest


# ---------------------------------------------------------------------------
# Import the message constant directly from the module under test.
# We do NOT exec the CLI; we extract the fully-armed message from source.
# ---------------------------------------------------------------------------

def _get_fully_armed_message() -> str:
    """
    Return the second element of the ``nxt`` tuple for the fully-armed branch
    by importing and inspecting the script.  We locate the source so that the
    test stays coupled to the real message even if it moves.
    """
    import importlib.util, pathlib, ast, textwrap

    src_path = pathlib.Path(__file__).parent.parent / "scripts" / "preflight_restart_check.py"
    source = src_path.read_text()

    # Parse AST and find the assignment  nxt = ("(none)", "<message>")  inside
    # the ``else`` branch at the end of the NEXT FLIP ladder.
    tree = ast.parse(source)

    # Walk all Assign nodes; look for `nxt = ("(none)", …)`
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        # LHS must be a single Name "nxt"
        if len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not (isinstance(target, ast.Name) and target.id == "nxt"):
            continue
        # RHS must be a Tuple of at least 2 string constants
        val = node.value
        if not isinstance(val, ast.Tuple) or len(val.elts) < 2:
            continue
        first = val.elts[0]
        if not isinstance(first, ast.Constant) or first.value != "(none)":
            continue
        second = val.elts[1]
        if isinstance(second, ast.Constant) and isinstance(second.value, str):
            return second.value

    raise RuntimeError("Could not locate the fully-armed nxt message in preflight_restart_check.py")


FULLY_ARMED_MSG = _get_fully_armed_message()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFullyArmedMessageObjective:
    """The fully-armed message must describe the real capital objective."""

    def test_does_not_present_bare_winrate_as_objective(self):
        """
        The message must NOT contain a bare '>51%' (or '> 51%') win-rate claim
        as the primary monitoring objective.
        """
        bare_winrate_pattern = re.compile(r"win.?rate\s*>[\s]*51\s*%", re.IGNORECASE)
        assert not bare_winrate_pattern.search(FULLY_ARMED_MSG), (
            f"Fully-armed message still names raw >51% win-rate as the capital "
            f"objective. Message: {FULLY_ARMED_MSG!r}"
        )

    def test_names_after_cost_ev_or_pnl(self):
        """
        The message MUST reference after-cost EV and/or PnL / log-growth as
        the primary monitoring metric.
        """
        pattern = re.compile(
            r"(after.?cost\s*(ev|pnl|log.?growth)|ev\s*/\s*pnl|pnl\s*/\s*log.?growth)",
            re.IGNORECASE,
        )
        assert pattern.search(FULLY_ARMED_MSG), (
            f"Fully-armed message does not name after-cost EV/PnL. "
            f"Message: {FULLY_ARMED_MSG!r}"
        )

    def test_names_q_lcb_coverage(self):
        """
        The message MUST reference q_lcb coverage as a monitoring dimension.
        """
        assert re.search(r"q_lcb", FULLY_ARMED_MSG, re.IGNORECASE), (
            f"Fully-armed message does not mention q_lcb coverage. "
            f"Message: {FULLY_ARMED_MSG!r}"
        )

    def test_names_price_bucketed_winrate(self):
        """
        Win-rate is still a valid *secondary* metric when price-bucketed.
        The message should reference price-bucketed win-rate (not the bare form).
        """
        assert re.search(r"price.?bucket", FULLY_ARMED_MSG, re.IGNORECASE), (
            f"Fully-armed message does not mention price-bucketed win-rate. "
            f"Message: {FULLY_ARMED_MSG!r}"
        )
