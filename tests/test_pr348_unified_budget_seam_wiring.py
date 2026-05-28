# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: PR #348 pre-merge critic — SEV-1 dead unified-budget collapse seam
# Lifecycle: created=2026-05-27; last_reviewed=2026-05-27; last_reused=never
# Purpose: structural antibody — every production haircut call site MUST forward
#          the per-edge market_uncertainty_in_lcb gate, or the Wave-6 collapse is
#          dead from the live path (INV-40 double-count when the flag is flipped).
# Reuse: AST wiring contract over evaluator/replay/cycle_runtime. Refactor-surviving
#        (not line-anchored). Catches the PR #309-class "green unit tests, dead prod
#        seam" regression: Wave-6 function-level tests pass the flag DIRECTLY to
#        dynamic_kelly_mult / _size_at_execution_price_boundary and therefore can
#        never detect a call site that fails to forward it.
"""Unified-budget seam wiring antibody (PR #348, SEV-1).

The pre-merge critic found that ``market_uncertainty_in_lcb`` was wired at
only one of the five sites that apply a soft-uncertainty haircut. Every
Wave-6 unit test passed the flag straight into the function under test, so
the dead production seam was invisible to them.

These tests assert the cross-module CONTRACT directly: in the live + replay
sizing paths, every call to the two haircut-bearing functions forwards the
per-edge gate. If a future refactor adds a new call site (or drops the
kwarg from an existing one), this fails — independent of line numbers.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_GATE_KWARG = "market_uncertainty_in_lcb"


def _call_name(call: ast.Call) -> str | None:
    fn = call.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return getattr(fn, "id", None)


def _calls_missing_gate(rel_path: str, func_name: str) -> list[int]:
    """Return line numbers of ``func_name(...)`` calls that omit the gate kwarg."""
    tree = ast.parse((_REPO_ROOT / rel_path).read_text())
    missing: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) == func_name:
            kwargs = {kw.arg for kw in node.keywords if kw.arg is not None}
            if _GATE_KWARG not in kwargs:
                missing.append(node.lineno)
    return missing


def _count_calls(rel_path: str, func_name: str) -> int:
    tree = ast.parse((_REPO_ROOT / rel_path).read_text())
    return sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node) == func_name
    )


class TestDynamicKellyMultGateForwarded:
    """The ci_width haircut lives inside dynamic_kelly_mult; the live (evaluator)
    and replay sizing paths must forward the per-edge gate."""

    @pytest.mark.parametrize("rel_path", [
        "src/engine/evaluator.py",
        "src/engine/replay.py",
    ])
    def test_every_dynamic_kelly_mult_call_forwards_gate(self, rel_path):
        # Guard against the test silently passing because the call vanished.
        assert _count_calls(rel_path, "dynamic_kelly_mult") >= 1, (
            f"no dynamic_kelly_mult call found in {rel_path}; "
            "update this antibody if the seam moved"
        )
        missing = _calls_missing_gate(rel_path, "dynamic_kelly_mult")
        assert missing == [], (
            f"{rel_path}: dynamic_kelly_mult call(s) at lines {missing} omit "
            f"'{_GATE_KWARG}'. Wave-6 ci_width collapse is dead at those sites "
            "(INV-40 double-count when the flag is flipped)."
        )


class TestBoundaryGateForwarded:
    """The EffectiveKellyContext haircut fires inside
    _size_at_execution_price_boundary at the LIVE microstructure path
    (cycle_runtime W2/W3/W4). All such calls must forward the per-edge gate."""

    def test_every_cycle_runtime_boundary_call_forwards_gate(self):
        rel = "src/engine/cycle_runtime.py"
        n = _count_calls(rel, "_size_at_execution_price_boundary")
        assert n >= 3, (
            f"expected >=3 _size_at_execution_price_boundary calls in {rel} "
            f"(W2/W3/W4), found {n}; update this antibody if the seam moved"
        )
        missing = _calls_missing_gate(rel, "_size_at_execution_price_boundary")
        assert missing == [], (
            f"{rel}: _size_at_execution_price_boundary call(s) at lines "
            f"{missing} omit '{_GATE_KWARG}'. The live EKC haircut collapse is "
            "dead at those sites (INV-40 double-count when the flag is flipped)."
        )
