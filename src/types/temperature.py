"""
Temperature and TemperatureDelta typed containers.

Prevents the #1 class of legacy-predecessor bugs: comparing/combining values in
different units (°F vs °C) without conversion.

Design decisions (per Fitz):
  - NOT a single-unit refactor. Values stay in their native unit.
  - Polymarket bins are native-unit (Dallas °F, London °C).
  - All historical calibration data is native-unit.
  - Cross-unit operations raise UnitMismatchError at runtime.
  - Conversions are explicit via .to(target_unit).

Two distinct types:
  - Temperature: absolute values (forecast=72°F). Conversion has offset (+32).
  - TemperatureDelta: differences, std devs, biases, thresholds.
    Conversion is scale-only (no offset). 1°C delta = 1.8°F delta.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from scipy.stats import norm

Unit = Literal["F", "C"]


class UnitMismatchError(TypeError):
    """Raised when an operation mixes incompatible temperature units."""
    pass


def _check_unit(a_unit: str, b_unit: str, op: str) -> None:
    if a_unit != b_unit:
        raise UnitMismatchError(
            f"Cannot {op} values with different units: {a_unit} vs {b_unit}. "
            f"Convert to the same unit first with .to()."
        )


@dataclass(frozen=True, slots=True)
class Temperature:
    """An absolute temperature value with an explicit unit.

    Immutable. All operations that change the value return a new instance.
    """

    value: float
    unit: Unit

    def to(self, target: Unit) -> Temperature:
        """Convert to target unit. No-op if already in that unit."""
        if self.unit == target:
            return self
        if target == "F":
            return Temperature(self.value * 9.0 / 5.0 + 32.0, "F")
        return Temperature((self.value - 32.0) * 5.0 / 9.0, "C")

    def __sub__(self, other: object) -> TemperatureDelta:
        if not isinstance(other, Temperature):
            return NotImplemented
        _check_unit(self.unit, other.unit, "subtract")
        return TemperatureDelta(self.value - other.value, self.unit)

    def __add__(self, other: object) -> Temperature:
        if not isinstance(other, TemperatureDelta):
            return NotImplemented
        _check_unit(self.unit, other.unit, "add")
        return Temperature(self.value + other.value, self.unit)

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Temperature):
            return NotImplemented
        _check_unit(self.unit, other.unit, "compare")
        return self.value > other.value

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Temperature):
            return NotImplemented
        _check_unit(self.unit, other.unit, "compare")
        return self.value < other.value

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Temperature):
            return NotImplemented
        _check_unit(self.unit, other.unit, "compare")
        return self.value >= other.value

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Temperature):
            return NotImplemented
        _check_unit(self.unit, other.unit, "compare")
        return self.value <= other.value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Temperature):
            return NotImplemented
        if self.unit != other.unit:
            raise UnitMismatchError(
                f"Cannot compare Temperature({self.value}{self.unit}) "
                f"with Temperature({other.value}{other.unit}). "
                f"Convert to the same unit first with .to()."
            )
        return self.value == other.value

    def __hash__(self) -> int:
        return hash((self.value, self.unit))

    def __repr__(self) -> str:
        return f"Temperature({self.value}, '{self.unit}')"

    def __str__(self) -> str:
        return f"{self.value:.1f}\u00b0{self.unit}"


@dataclass(frozen=True, slots=True)
class TemperatureDelta:
    """A temperature difference, std dev, bias, or threshold.

    Scale-only conversion: 1°C delta = 1.8°F delta (no +32 offset).
    """

    value: float
    unit: Unit

    def to(self, target: Unit) -> TemperatureDelta:
        """Convert delta to target unit. Scale only, no offset."""
        if self.unit == target:
            return self
        if target == "F":
            return TemperatureDelta(self.value * 9.0 / 5.0, "F")
        return TemperatureDelta(self.value * 5.0 / 9.0, "C")

    def __add__(self, other: object) -> TemperatureDelta:
        if not isinstance(other, TemperatureDelta):
            return NotImplemented
        _check_unit(self.unit, other.unit, "add")
        return TemperatureDelta(self.value + other.value, self.unit)

    def __sub__(self, other: object) -> TemperatureDelta:
        if not isinstance(other, TemperatureDelta):
            return NotImplemented
        _check_unit(self.unit, other.unit, "subtract")
        return TemperatureDelta(self.value - other.value, self.unit)

    def __neg__(self) -> TemperatureDelta:
        return TemperatureDelta(-self.value, self.unit)

    def __abs__(self) -> TemperatureDelta:
        return TemperatureDelta(abs(self.value), self.unit)

    def __mul__(self, scalar: object) -> TemperatureDelta:
        if not isinstance(scalar, (int, float)):
            return NotImplemented
        return TemperatureDelta(self.value * scalar, self.unit)

    def __rmul__(self, scalar: object) -> TemperatureDelta:
        return self.__mul__(scalar)

    def __truediv__(self, other: object):
        """Divide a delta by a scalar or another delta.

        delta / scalar → TemperatureDelta (e.g., spread std over N samples)
        delta / delta  → float (e.g., z-score = error / std)
        """
        if isinstance(other, TemperatureDelta):
            _check_unit(self.unit, other.unit, "divide")
            if other.value == 0:
                raise ZeroDivisionError("Cannot divide by TemperatureDelta with value 0")
            return self.value / other.value
        if isinstance(other, (int, float)):
            if other == 0:
                raise ZeroDivisionError("Cannot divide TemperatureDelta by zero")
            return TemperatureDelta(self.value / other, self.unit)
        return NotImplemented

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, TemperatureDelta):
            return NotImplemented
        _check_unit(self.unit, other.unit, "compare")
        return self.value > other.value

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, TemperatureDelta):
            return NotImplemented
        _check_unit(self.unit, other.unit, "compare")
        return self.value < other.value

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, TemperatureDelta):
            return NotImplemented
        _check_unit(self.unit, other.unit, "compare")
        return self.value >= other.value

    def __le__(self, other: object) -> bool:
        if not isinstance(other, TemperatureDelta):
            return NotImplemented
        _check_unit(self.unit, other.unit, "compare")
        return self.value <= other.value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TemperatureDelta):
            return NotImplemented
        if self.unit != other.unit:
            raise UnitMismatchError(
                f"Cannot compare TemperatureDelta({self.value}{self.unit}) "
                f"with TemperatureDelta({other.value}{other.unit})."
            )
        return self.value == other.value

    def __hash__(self) -> int:
        return hash((self.value, self.unit))

    def __repr__(self) -> str:
        return f"TemperatureDelta({self.value}, '{self.unit}')"

    def __str__(self) -> str:
        sign = "+" if self.value >= 0 else ""
        return f"{sign}{self.value:.1f}\u00b0{self.unit}"


# ── Scipy boundary wrapper ─────────────────────────────────────────

def cdf_probability(
    threshold: Temperature,
    mean: Temperature,
    std: TemperatureDelta,
) -> float:
    """Compute P(X <= threshold) for X ~ N(mean, std^2).

    All three arguments must have the same unit. Raises UnitMismatchError
    if they don't — this is the primary safety gate for probability
    calculations.
    """
    if not (threshold.unit == mean.unit == std.unit):
        raise UnitMismatchError(
            f"cdf_probability unit mismatch: threshold={threshold.unit}, "
            f"mean={mean.unit}, std={std.unit}. All must be the same unit."
        )
    return float(norm.cdf(threshold.value, mean.value, std.value))
