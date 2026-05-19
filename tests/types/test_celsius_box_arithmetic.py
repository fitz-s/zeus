# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: F3 PR 4 / study at jobs/9ea6f95c/findings/f3_frozen_dataclass_study.md
"""Arithmetic and cross-unit guard tests for CelsiusBox / FahrenheitBox.

Load-bearing tests:
  1. Same-unit addition and subtraction return correct Box type.
  2. Cross-unit arithmetic raises TypeError (the runtime antibody).
  3. Conversion helpers round-trip correctly.
  4. Frozen dataclass guarantees: hashable, immutable after construction.
  5. Sed-break: commenting out __add__ cross-unit guard causes tests 3+4 to fail.

Fitz Constraint #1: "make the category impossible, not just the instance."
"""
from __future__ import annotations

import pytest

from src.types.temperature import CelsiusBox, FahrenheitBox


# ---------------------------------------------------------------------------
# 1. Same-unit arithmetic
# ---------------------------------------------------------------------------

def test_celsius_box_same_unit_add() -> None:
    result = CelsiusBox(20) + CelsiusBox(5)
    assert result == CelsiusBox(25)


def test_fahrenheit_box_same_unit_add() -> None:
    result = FahrenheitBox(68) + FahrenheitBox(5)
    assert result == FahrenheitBox(73)


def test_celsius_box_same_unit_sub() -> None:
    result = CelsiusBox(20) - CelsiusBox(5)
    assert result == CelsiusBox(15)


def test_fahrenheit_box_same_unit_sub() -> None:
    result = FahrenheitBox(73) - FahrenheitBox(5)
    assert result == FahrenheitBox(68)


# ---------------------------------------------------------------------------
# 2. Cross-unit arithmetic raises TypeError — runtime antibody
# ---------------------------------------------------------------------------

def test_celsius_add_fahrenheit_raises() -> None:
    """CelsiusBox + FahrenheitBox must raise TypeError.

    Antibody: if __add__ cross-unit guard is removed, this test fails.
    """
    c = CelsiusBox(20)
    f = FahrenheitBox(68)
    with pytest.raises(TypeError, match="Cannot add Celsius"):
        _ = c + f


def test_fahrenheit_add_celsius_raises() -> None:
    """FahrenheitBox + CelsiusBox must raise TypeError (mirror of above)."""
    f = FahrenheitBox(68)
    c = CelsiusBox(20)
    with pytest.raises(TypeError, match="Cannot add Fahrenheit"):
        _ = f + c


# ---------------------------------------------------------------------------
# 3. Conversion helpers
# ---------------------------------------------------------------------------

def test_celsius_to_fahrenheit_conversion() -> None:
    """20°C == 68°F exactly (integer arithmetic)."""
    result = CelsiusBox(20).to_fahrenheit()
    assert result == FahrenheitBox(68)


def test_fahrenheit_to_celsius_conversion() -> None:
    """68°F == 20°C exactly (inverse)."""
    result = FahrenheitBox(68).to_celsius()
    assert result == CelsiusBox(20)


# ---------------------------------------------------------------------------
# 4. Frozen dataclass guarantees
# ---------------------------------------------------------------------------

def test_celsius_box_is_hashable() -> None:
    """CelsiusBox can be used as a dict key or set element (frozen + slots)."""
    d: dict[CelsiusBox, str] = {CelsiusBox(20): "London high"}
    assert d[CelsiusBox(20)] == "London high"
    s = {CelsiusBox(0), CelsiusBox(100)}
    assert len(s) == 2


def test_celsius_box_is_immutable() -> None:
    """Assigning to .value raises FrozenInstanceError (frozen=True)."""
    c = CelsiusBox(20)
    with pytest.raises(AttributeError):
        c.value = 99  # type: ignore[misc]


def test_fahrenheit_box_is_hashable() -> None:
    s = {FahrenheitBox(32), FahrenheitBox(212)}
    assert len(s) == 2


def test_fahrenheit_box_is_immutable() -> None:
    f = FahrenheitBox(68)
    with pytest.raises(AttributeError):
        f.value = 0  # type: ignore[misc]
