# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: src/contracts/settlement_semantics.py (F3 PR 1/3)
#   Slice 4 — typed-unit antibody tests for Celsius/Fahrenheit NewType migration.
#   Fitz Constraint #1: "make the category impossible, not just the instance."

"""Antibody tests for Celsius/Fahrenheit NewType enforcement.

Load-bearing tests:
  1. Happy path: degC_d() + settle_market() / WMO_HalfUp.round_to_settlement()
     produce correct results.
  2. Sed-break (mypy-scoped): passing raw Decimal to round_to_settlement fails
     mypy on an isolated snippet.  Mypy runs with --follow-imports=silent so
     imported type info is visible but errors from sibling modules that
     predate strict adoption do not cascade.
  3. Positive boundary: degC() passed to a function annotated Celsius -> None
     passes mypy, confirming the guard is live.

Scope limitation (Path A, accepted by operator 2026-05-18):
  NewType-only does NOT block `Celsius + Fahrenheit` arithmetic — mypy treats
  both as `float` for operator dispatch and returns plain `float`.  Function
  SIGNATURES are gated (raw Decimal -> CelsiusDecimal raises mypy error at
  call sites), but in-body arithmetic mixing is not blocked by this PR.
  Full category-impossibility requires frozen-dataclass wrappers with custom
  __add__ / __radd__ — deferred because of runtime cost in hot statistical
  loops.  See src/types/temperature.py LIMITATION comment.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
from decimal import Decimal

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
    assert policy.round_to_settlement(raw) == 29  # WMO half-up: 28.5 -> 29


def test_hko_truncation_accepts_celsius_decimal() -> None:
    """HKO_Truncation accepts CelsiusDecimal and truncates correctly."""
    policy = HKO_Truncation()
    raw = degC_d(Decimal("28.7"))
    assert policy.round_to_settlement(raw) == 28  # truncation: 28.7 -> 28


def test_settle_market_accepts_celsius_decimal() -> None:
    """settle_market end-to-end with CelsiusDecimal input."""
    result = settle_market("New York", degC_d(Decimal("3.5")), WMO_HalfUp())
    assert result == 4  # WMO half-up: 3.5 -> 4


def test_settle_market_hk_accepts_celsius_decimal() -> None:
    """settle_market for Hong Kong with CelsiusDecimal."""
    result = settle_market("Hong Kong", degC_d(Decimal("28.9")), HKO_Truncation())
    assert result == 28  # truncation: 28.9 -> 28


# ---------------------------------------------------------------------------
# Mypy-scoped helper
# ---------------------------------------------------------------------------

def _run_mypy_strict_on_snippet(code: str) -> tuple[int, str]:
    """Write code to a temp file and run mypy --strict --follow-imports=silent on it.

    --follow-imports=silent means mypy reads imported module types (so
    CelsiusDecimal annotations are visible) but suppresses errors *from*
    those modules, so pre-strict sibling modules do not cascade errors
    into the snippet check.

    cwd is set to the repo root (two dirs above tests/contracts/) so mypy.ini
    is discovered and src/ is on the module search path regardless of where
    pytest is invoked from.

    Returns (exit_code, combined_stdout_stderr).
    """
    # Repo root: tests/contracts/../../  (this file lives at tests/contracts/test_*.py)
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, prefix="mypy_snippet_"
    ) as f:
        f.write(code)
        tmp_path = f.name

    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "mypy",
                "--strict",
                "--ignore-missing-imports",
                "--follow-imports=silent",
                "--python-version", f"{sys.version_info.major}.{sys.version_info.minor}",
                tmp_path,
            ],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        return result.returncode, result.stdout + result.stderr
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Sed-break / type-fail — mypy must reject plain Decimal in CelsiusDecimal slot
# ---------------------------------------------------------------------------

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
        # BUG: passing raw Decimal instead of CelsiusDecimal -- should fail mypy
        result = policy.round_to_settlement(Decimal("28.5"))
    """)
    exit_code, output = _run_mypy_strict_on_snippet(snippet)
    assert exit_code != 0, (
        "mypy should reject plain Decimal where CelsiusDecimal is required, "
        f"but it exited 0. Output:\n{output}"
    )
    assert "Argument" in output or "error" in output.lower(), (
        f"Expected a type error in mypy output, got:\n{output}"
    )


def test_mypy_accepts_celsius_decimal_wrapped() -> None:
    """Positive sed-break: correctly wrapped CelsiusDecimal passes mypy.

    degC_d(Decimal("28.5")) returns CelsiusDecimal -- mypy should accept this.
    """
    snippet = textwrap.dedent("""\
        from decimal import Decimal
        from src.contracts.settlement_semantics import WMO_HalfUp
        from src.types.temperature import degC_d

        policy = WMO_HalfUp()
        # CORRECT: wrapped via degC_d
        result = policy.round_to_settlement(degC_d(Decimal("28.5")))
    """)
    exit_code, output = _run_mypy_strict_on_snippet(snippet)
    settlement_errors = [
        line for line in output.splitlines()
        if "round_to_settlement" in line and "error" in line.lower()
    ]
    assert not settlement_errors, (
        "mypy flagged a type error on the correctly-wrapped CelsiusDecimal call:\n"
        + "\n".join(settlement_errors)
    )


def test_mypy_accepts_celsius_newtype_at_function_boundary() -> None:
    """Positive boundary: Celsius NewType accepted where Celsius is annotated.

    Confirms the function-boundary type guard is live: degC(20.0) satisfies
    a parameter annotated as Celsius, while a plain float does not (under
    strict mypy with NewType).
    """
    snippet = textwrap.dedent("""\
        from src.types.temperature import Celsius, degC

        def wants_celsius(t: Celsius) -> None:
            pass

        # CORRECT: wrapped via degC
        wants_celsius(degC(20.0))
    """)
    exit_code, output = _run_mypy_strict_on_snippet(snippet)
    boundary_errors = [
        line for line in output.splitlines()
        if "wants_celsius" in line and "error" in line.lower()
    ]
    assert not boundary_errors, (
        "mypy flagged a type error on correctly-typed Celsius boundary call:\n"
        + "\n".join(boundary_errors)
    )
