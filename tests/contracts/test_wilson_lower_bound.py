# Created: 2026-09-29
# Authority basis: src/contracts/probability_arithmetic.py wilson_lower_bound;
#   live 2026-09-28: a 7e-18 float residue at zero fills minted a maker witness
#   and failed every auction cut for 3 hours.
"""One Wilson lower bound, exact at zero successes, and no second copy in src/."""

from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

from src.contracts.probability_arithmetic import (
    Z_ONE_SIDED_95,
    Z_TWO_SIDED_95,
    wilson_lower_bound,
)

ROOT = Path(__file__).resolve().parents[2]
OWNER = ROOT / "src" / "contracts" / "probability_arithmetic.py"


def _closed_form(k: float, n: float, z: float) -> float:
    p = k / n
    z2 = z * z
    return (p + z2 / (2 * n) - z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / (1 + z2 / n)


@pytest.mark.parametrize("z", (Z_ONE_SIDED_95, Z_TWO_SIDED_95))
@pytest.mark.parametrize("trials", range(1, 501))
def test_zero_successes_is_exactly_zero_for_every_trial_count(trials, z):
    assert wilson_lower_bound(0, trials, z=z) == 0.0


def test_the_closed_form_itself_leaves_residue_at_zero_successes():
    """The case the guard exists for: centre - margin is not zero in floats."""
    residues = [
        n for n in range(1, 101) if _closed_form(0, n, Z_TWO_SIDED_95) != 0.0
    ]
    assert {3, 6, 7, 12, 24, 25, 28, 35, 48}.issubset(residues)


@pytest.mark.parametrize("z", (Z_ONE_SIDED_95, Z_TWO_SIDED_95, 1.645))
@pytest.mark.parametrize(
    ("successes", "trials"),
    ((1, 1), (1, 3), (7, 10), (43, 182), (25, 112), (2, 71), (700, 1000), (70, 104)),
)
def test_positive_successes_follow_the_closed_form(successes, trials, z):
    expected = min(max(_closed_form(successes, trials, z), 0.0), successes / trials)
    assert wilson_lower_bound(successes, trials, z=z) == pytest.approx(expected, abs=1e-15)


@pytest.mark.parametrize(
    ("successes", "trials"),
    ((5, 0), (0, 0), (3, -1), (-2, 10), (12, 10), (1, float("nan")), (1, float("inf"))),
)
def test_degenerate_inputs_are_bounded(successes, trials):
    value = wilson_lower_bound(successes, trials, z=Z_TWO_SIDED_95)
    assert 0.0 <= value <= 1.0


def test_fractional_evidence_count_is_supported():
    # The visible-depth fill bound uses the depth cushion as a fractional n.
    thin = wilson_lower_bound(0.5 * 0.5, 0.5, z=1.645)
    deep = wilson_lower_bound(1.0 * 20.0, 20.0, z=1.645)
    assert 0.0 <= thin < 0.5
    assert 0.8 < deep < 1.0


def _is_four_n_squared(node: ast.AST) -> bool:
    """Match the Wilson radius term ``4 * n * n`` (any literal 4 times a squared name)."""
    text = ast.unparse(node).replace(" ", "")
    return text.startswith(("4*", "4.0*")) and text.count("*") >= 2


def test_no_second_wilson_formula_in_src():
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        if path == OWNER:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and "wilson" in node.name.lower():
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}: def {node.name}")
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult) and _is_four_n_squared(node):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}: {ast.unparse(node)}")
    assert not offenders, (
        "A Wilson bound is computed outside src/contracts/probability_arithmetic.py; "
        "call wilson_lower_bound instead:\n" + "\n".join(sorted(offenders))
    )
