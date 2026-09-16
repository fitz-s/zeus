# Created: 2026-09-16
# Purpose: The Day0 provisional-carrier raise must share the fusion gate's
#   precondition. A family whose fusion capture was never available skips the
#   whole carrier region (no exception, so no error string is captured) and must
#   degrade as BAYES_PRECISION_FUSION_CAPTURE_MISSING, not hard-error with a
#   generic string naming a carrier it never attempted.
# Reuse: Run when the fused-q region's gate at materializer.py:6187-6190 or the
#   carrier raise below it changes.
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from src.data import replacement_forecast_materializer as materializer_mod


def _carrier_raise_guard_conditions() -> list[str]:
    """Return the source of each condition in the carrier raise's `if` test."""

    source = Path(inspect.getsourcefile(materializer_mod)).read_text()
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        body = node.body
        if len(body) != 1 or not isinstance(body[0], ast.Raise):
            continue
        raised = ast.unparse(body[0])
        if "DAY0_PROVISIONAL_CARRIER_UNAVAILABLE" not in raised:
            continue
        test = node.test
        parts = test.values if isinstance(test, ast.BoolOp) else [test]
        return [ast.unparse(part) for part in parts]
    raise AssertionError("carrier raise not found")


def test_carrier_raise_requires_the_fusion_gate_it_depends_on() -> None:
    """The raise cannot fire for a family that never entered the fusion region.

    `_day0_shared_carrier` is assigned in exactly one place, inside
    `if bayes_precision_fusion_override is not None and
    bayes_precision_fusion_override.predictive_sigma_c is not None:`. If the
    raise below does not carry that same condition, then a missing fusion
    capture — a designed, already-labelled non-live degrade — becomes a hard
    ValueError blaming the carrier. Both halves of the gate must appear.
    """
    conditions = _carrier_raise_guard_conditions()
    joined = " | ".join(conditions)

    assert "bayes_precision_fusion_override is not None" in joined, (
        "carrier raise must require that fusion was attempted; "
        f"conditions were: {conditions}"
    )
    assert "predictive_sigma_c is not None" in joined, (
        "carrier raise must require the fusion sigma that gates the carrier "
        f"region; conditions were: {conditions}"
    )
    # The pre-existing conditions must survive: a genuinely failed carrier on a
    # started local day with a provisional observation still has to raise.
    assert any("_day0_shared_carrier is None" in c for c in conditions)
    assert any("_target_local_day_has_started" in c for c in conditions)


def test_shared_carrier_is_assigned_only_inside_the_fusion_gate() -> None:
    """Pin the premise: one assignment site, and it is fusion-gated.

    If a second assignment site appears outside that gate, the guard coupling
    this test enforces would become wrong rather than merely incomplete.
    """
    source = Path(inspect.getsourcefile(materializer_mod)).read_text()
    assignments = [
        line
        for line in source.splitlines()
        if "_day0_shared_carrier," in line and "=" in line
    ]
    assert len(assignments) == 1, f"expected one assignment, got {assignments}"
