# Created: 2026-06-07
# Last reused/audited: 2026-06-07
# Authority basis: PR_SPEC.md §2 FIX-5a + §0.2 (an antibody must catch value-identical
#   rewrites, not just one banned syntax). Companion to the AST guard.
"""Value-level antibody for the (1/x - 1) * x complement obfuscation.

§0.2 LAW: commit 16c35e7445 defeated the AST complement guard by rewriting ``1 - x``
as the value-identical ``(1.0 / x - 1.0) * x``. A grep/AST guard alone is therefore
insufficient — it matches shape, not value. This test:

  1. proves the obfuscated shape is numerically identical to 1 - x (the math fact the
     guard cannot see), so the reader understands WHY it is banned; and
  2. scans the live sites that carried the obfuscation and fails if the
     ``(1.0 / x - 1.0) * x`` shape (a value == 1 - x written multiplicatively) is
     present anywhere in them — forcing the named one_minus()/payout_odds() helper.

Removing the FIX-5a production change (reintroducing the obfuscation) fails this test,
so it is a real antibody.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.contracts.probability_arithmetic import one_minus, payout_odds


ROOT = Path(__file__).resolve().parents[1]


# The exact live sites 16c35e7445 obfuscated with (1/x - 1) * x.
DEOBFUSCATED_SITES = (
    "src/engine/evaluator.py",
    "src/engine/monitor_refresh.py",
    "src/events/candidate_evaluation.py",
)


def test_obfuscated_shape_is_numerically_one_minus_x() -> None:
    """The math fact: (1/x - 1) * x == 1 - x for all x != 0."""

    for x in (0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99):
        obfuscated = (1.0 / x - 1.0) * x
        assert obfuscated == pytest.approx(1.0 - x, abs=1e-12)
        assert one_minus(x) == pytest.approx(1.0 - x, abs=1e-12)
        assert one_minus(x) == pytest.approx(obfuscated, abs=1e-12)


def test_payout_odds_is_not_a_complement_of_one() -> None:
    """payout_odds(price) = (1-price)/price is genuine odds, distinct from 1 - price."""

    for p in (0.1, 0.25, 0.5, 0.75, 0.9):
        assert payout_odds(p) == pytest.approx((1.0 - p) / p, abs=1e-12)
        # It must NOT collapse to the complement except at the algebraic fixed point.
        if p != pytest.approx((1.0 - p) / p):
            assert payout_odds(p) != pytest.approx(1.0 - p, abs=1e-9) or p == pytest.approx(0.5)


def _is_one_over_x_minus_one(node: ast.AST) -> bool:
    """Match (1 / x) - 1  (in any 1-like / 1-like form)."""

    if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub)):
        return False
    left, right = node.left, node.right
    if not _is_one_like(right):
        return False
    return isinstance(left, ast.BinOp) and isinstance(left.op, ast.Div) and _is_one_like(left.left)


def _is_one_like(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value in (1, 1.0)


def _obfuscated_complement_findings(tree: ast.AST, label: str) -> list[str]:
    """Find ((1/x) - 1) * x  — the multiplicative shape that equals 1 - x."""

    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            if _is_one_over_x_minus_one(node.left) or _is_one_over_x_minus_one(node.right):
                findings.append(f"{label}:{node.lineno}: {ast.unparse(node)}")
    return findings


def test_live_sites_do_not_carry_the_one_minus_x_obfuscation() -> None:
    findings: list[str] = []
    for rel in DEOBFUSCATED_SITES:
        path = ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        findings.extend(_obfuscated_complement_findings(tree, rel))

    assert not findings, (
        "found (1/x - 1) * x complement obfuscation (value == 1 - x); "
        "use one_minus()/payout_odds():\n" + "\n".join(findings)
    )
