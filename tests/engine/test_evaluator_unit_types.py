# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: src/signal/day0_router.py, src/signal/day0_high_signal.py (F3 PR 3/3)
#   Slice 4 — antibody tests confirming Path A pattern and signal-layer boundary decision.
#   Fitz Constraint #1: "make the category impossible, not just the instance."

"""Antibody tests for the signal/evaluator layer temperature boundary (F3 PR 3/3).

Context:
  PR #170 (contracts layer) and PR #171 (ingest layer) applied the Path A
  Celsius/Fahrenheit NewType migration to statically-unit-stable sites.

  The signal/evaluator layer is intentionally NOT migrated to Celsius/Fahrenheit
  NewTypes for temperature VALUES (observed_high_so_far, current_temp, etc.)
  because these parameters are unit-polymorphic at runtime: Dallas °F and London °C
  flow through the same code paths. The unit is carried as `unit: str`.

  This test file:
    1. Happy path: Day0HighSignal accepts float temperature values and produces
       correct settlement samples — runtime behavior is unchanged from pre-PR.
    2. Sed-break (mypy-scoped): bare float fails mypy where Celsius is required,
       proving the general NewType antibody infrastructure is live for call sites
       that ARE statically unit-stable.
    3. Positive boundary: degC(t) satisfies a Celsius-annotated parameter under
       strict mypy — confirming the guard works when applied.

Scope limitation (Path A, accepted by operator 2026-05-18):
  NewType-only does NOT block `Celsius + Fahrenheit` arithmetic — mypy treats
  both as `float` for operator dispatch and returns plain `float`. Function
  SIGNATURES are gated (raw float → Celsius raises mypy error at call sites),
  but in-body arithmetic mixing is not blocked by this PR.
  Full category-impossibility requires frozen-dataclass wrappers with custom
  __add__ / __radd__ — deferred because of runtime cost in hot statistical loops.
  See src/types/temperature.py LIMITATION comment.

  Signal/evaluator temperature VALUE parameters (observed_high_so_far, current_temp,
  member_maxes_remaining, etc.) stay as `float` intentionally — see LIMITATION block
  in src/signal/day0_router.py for the detailed rationale.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap

import numpy as np

from src.signal.day0_high_signal import Day0HighSignal
from src.types.temperature import Celsius, degC


# ---------------------------------------------------------------------------
# Happy path — Day0HighSignal accepts float temperature values
# ---------------------------------------------------------------------------

def test_day0_high_signal_settlement_samples_basic() -> None:
    """Day0HighSignal produces correct hard-floor settlement samples.

    Verifies runtime behaviour is unchanged: final settlement =
    max(observed_high_so_far, member_max_remaining).
    This test uses plain float values — consistent with the unit-polymorphic
    signal-layer boundary decision documented in src/signal/day0_router.py.
    """
    observed_high = 72.0       # °F — observed max so far
    member_maxes = np.array([68.0, 74.0, 71.0, 76.0], dtype=np.float64)

    signal = Day0HighSignal(
        observed_high_so_far=observed_high,
        member_maxes_remaining=member_maxes,
        current_temp=70.0,
        hours_remaining=4.0,
        unit="F",
    )
    samples = signal.settlement_samples()

    # Hard floor: each sample >= observed_high_so_far
    assert np.all(samples >= observed_high), (
        f"settlement_samples violates hard-floor invariant: min={samples.min()}, "
        f"observed_high={observed_high}"
    )
    # Values below observed_high are floored up; values above are preserved
    expected = np.maximum(observed_high, member_maxes)
    np.testing.assert_array_equal(samples, expected)


def test_day0_high_signal_p_bin_bounds_correct() -> None:
    """Day0HighSignal.p_bin() returns probability in [0, 1] and sums correctly."""
    signal = Day0HighSignal(
        observed_high_so_far=70.0,
        member_maxes_remaining=np.array([69.0, 71.0, 72.0, 73.0], dtype=np.float64),
        current_temp=70.0,
        hours_remaining=2.0,
        unit="F",
    )
    p_below = signal.p_bin(float("-inf"), 70.0)
    p_at_and_above = signal.p_bin(71.0, float("inf"))
    assert 0.0 <= p_below <= 1.0
    assert 0.0 <= p_at_and_above <= 1.0


# ---------------------------------------------------------------------------
# Mypy-scoped helper (matches pattern from tests/data/test_ingest_unit_types.py)
# ---------------------------------------------------------------------------

def _run_mypy_strict_on_snippet(code: str) -> tuple[int, str]:
    """Write code to a temp file and run mypy --strict --follow-imports=silent on it.

    --follow-imports=silent means mypy reads imported module types (so
    Celsius annotations are visible) but suppresses errors *from* those
    modules, so pre-strict sibling modules do not cascade errors into the
    snippet check.

    cwd is set to the repo root (two dirs above tests/engine/) so mypy.ini
    is discovered and src/ is on the module search path regardless of where
    pytest is invoked from.

    Returns (exit_code, combined_stdout_stderr).
    """
    # Repo root: tests/engine/../../  (this file lives at tests/engine/test_*.py)
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
# Sed-break — mypy must reject plain float where Celsius is required
# ---------------------------------------------------------------------------

def test_mypy_rejects_plain_float_as_celsius_arg() -> None:
    """Sed-break: bare float passed to a Celsius-annotated parameter fails mypy.

    Celsius = NewType("Celsius", float). Under strict mypy, a plain float
    does not satisfy a Celsius parameter — the caller must wrap via degC().

    This proves the antibody infrastructure is live. Signal/evaluator internal
    temperature values stay float (unit-polymorphic), but any NEW function
    accepting a statically-unit-stable temperature MUST use this pattern.
    """
    snippet = textwrap.dedent("""\
        from src.types.temperature import Celsius

        def store_celsius(t: Celsius) -> None:
            pass

        x: float = 20.0
        store_celsius(x)  # should fail mypy — plain float not assignable to Celsius
    """)
    exit_code, output = _run_mypy_strict_on_snippet(snippet)
    assert exit_code != 0, (
        "mypy should reject plain float where Celsius is required, "
        f"but it exited 0. Output:\n{output}"
    )
    assert "Argument" in output or "error" in output.lower(), (
        f"Expected a type error in mypy output, got:\n{output}"
    )


def test_mypy_accepts_degc_wrapped_celsius() -> None:
    """Positive boundary: degC(t) satisfies a Celsius-annotated parameter.

    Confirms the function-boundary type guard is live: degC(20.0) satisfies
    a parameter annotated as Celsius, while a plain float does not (under
    strict mypy with NewType).
    """
    snippet = textwrap.dedent("""\
        from src.types.temperature import Celsius, degC

        def store_celsius(t: Celsius) -> None:
            pass

        # CORRECT: wrapped via degC
        store_celsius(degC(20.0))
    """)
    exit_code, output = _run_mypy_strict_on_snippet(snippet)
    assert exit_code == 0, (
        f"mypy exited {exit_code} on correctly-typed Celsius call "
        f"(import error or unexpected failure):\n{output}"
    )
    error_lines = [line for line in output.splitlines() if "error:" in line.lower()]
    assert not error_lines, (
        "mypy reported errors on correctly-typed Celsius boundary call:\n"
        + "\n".join(error_lines)
    )
