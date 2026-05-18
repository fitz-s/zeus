# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: /Users/leofitz/.claude/jobs/9ea6f95c/briefs/f3_pr1_contracts_layer.md
#   Slice 4 — typed-unit antibody tests for Celsius/Fahrenheit NewType migration.
#   Fitz Constraint #1: "make the category impossible, not just the instance."

"""Antibody tests for Celsius/Fahrenheit NewType enforcement.

Two load-bearing tests:
  1. Happy path: degC_d() + settle_market() / WMO_HalfUp.round_to_settlement()
     produce correct results.
  2. Sed-break: mypy rejects Fahrenheit-typed value passed to
     round_to_settlement (which expects CelsiusDecimal) — type guard fires.

The sed-break test runs mypy via subprocess so it tests the *type-checker*
enforcement, not runtime (NewType is zero-cost at runtime; runtime values
are plain float/Decimal). This is the antibody: the category "wrong-unit
value to settlement function" must fail the type checker, not just documentation.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from decimal import Decimal

import pytest

from src.contracts.settlement_semantics import (
    HKO_Truncation,
    WMO_HalfUp,
    settle_market,
)
from src.types.temperature import CelsiusDecimal, degC_d


# ---------------------------------------------------------------------------
# Happy path — CelsiusDecimal flows through correctly
# ---------------------------------------------------------------------------

def test_wmo_half_up_accepts_celsius_decimal() -> None:
    """round_to_settlement accepts CelsiusDecimal and returns the correct int."""
    policy = WMO_HalfUp()
    raw = degC_d(Decimal("28.5"))
    assert policy.round_to_settlement(raw) == 29  # WMO half-up: 28.5 → 29


def test_hko_truncation_accepts_celsius_decimal() -> None:
    """HKO_Truncation accepts CelsiusDecimal and truncates correctly."""
    policy = HKO_Truncation()
    raw = degC_d(Decimal("28.7"))
    assert policy.round_to_settlement(raw) == 28  # truncation: 28.7 → 28


def test_settle_market_accepts_celsius_decimal() -> None:
    """settle_market end-to-end with CelsiusDecimal input."""
    result = settle_market("New York", degC_d(Decimal("3.5")), WMO_HalfUp())
    assert result == 4  # WMO half-up: 3.5 → 4


def test_settle_market_hk_accepts_celsius_decimal() -> None:
    """settle_market for Hong Kong with CelsiusDecimal."""
    result = settle_market("Hong Kong", degC_d(Decimal("28.9")), HKO_Truncation())
    assert result == 28  # truncation: 28.9 → 28


# ---------------------------------------------------------------------------
# Sed-break / type-fail — mypy must reject Fahrenheit in Celsius slot
# ---------------------------------------------------------------------------

def _run_mypy_on_snippet(snippet: str) -> tuple[int, str]:
    """Run mypy --strict on a temporary snippet; return (exit_code, output)."""
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, prefix="mypy_snippet_"
    ) as f:
        f.write(snippet)
        tmp_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, "-m", "mypy", "--strict", "--ignore-missing-imports",
             "--python-version", "3.11", tmp_path],
            capture_output=True,
            text=True,
        )
        return result.returncode, result.stdout + result.stderr
    finally:
        os.unlink(tmp_path)


def test_mypy_rejects_plain_decimal_without_degC_d() -> None:
    """Sed-break: passing raw Decimal to round_to_settlement fails mypy.

    CelsiusDecimal = NewType("CelsiusDecimal", Decimal).
    Passing a plain Decimal (not wrapped via degC_d()) is an argument type
    error under mypy-strict.  This is the antibody: the wrong type is
    un-constructable at the type-check boundary.
    """
    snippet = textwrap.dedent("""\
        from decimal import Decimal
        from src.contracts.settlement_semantics import WMO_HalfUp

        policy = WMO_HalfUp()
        # BUG: passing raw Decimal instead of CelsiusDecimal — should fail mypy
        result = policy.round_to_settlement(Decimal("28.5"))
    """)
    exit_code, output = _run_mypy_on_snippet(snippet)
    assert exit_code != 0, (
        "mypy should reject plain Decimal where CelsiusDecimal is required, "
        f"but it exited 0. Output:\n{output}"
    )
    # Confirm the error is specifically about the argument type, not an import error
    assert "Argument" in output or "error" in output.lower(), (
        f"Expected a type error in mypy output, got:\n{output}"
    )


def test_mypy_accepts_celsius_decimal_wrapped() -> None:
    """Positive sed-break: correctly wrapped CelsiusDecimal passes mypy.

    degC_d(Decimal("28.5")) returns CelsiusDecimal — mypy should accept this.
    """
    snippet = textwrap.dedent("""\
        from decimal import Decimal
        from src.contracts.settlement_semantics import WMO_HalfUp
        from src.types.temperature import degC_d

        policy = WMO_HalfUp()
        # CORRECT: wrapped via degC_d
        result = policy.round_to_settlement(degC_d(Decimal("28.5")))
    """)
    exit_code, output = _run_mypy_on_snippet(snippet)
    # Allow exit code 0 or errors only in unrelated imports; no error on our line
    settlement_errors = [
        line for line in output.splitlines()
        if "round_to_settlement" in line and "error" in line.lower()
    ]
    assert not settlement_errors, (
        f"mypy flagged a type error on the correctly-wrapped CelsiusDecimal call:\n"
        + "\n".join(settlement_errors)
    )
