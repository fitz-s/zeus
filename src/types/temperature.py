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
from decimal import Decimal
from typing import Literal, NewType

from scipy.stats import norm

# ---------------------------------------------------------------------------
# Celsius / Fahrenheit NewTypes (zero runtime cost — type-checker-only)
#
# Purpose: gate function SIGNATURES so callers cannot pass a raw float or a
# wrongly-typed temperature where a specific unit is expected.  Satisfies
# Fitz Constraint #1: "make the category impossible, not just the instance"
# for the function-boundary case.
#
# Design: NewType over float (for float-valued paths) and over Decimal
# (for settlement-precision paths where arithmetic must stay exact).
# The Temperature class below remains for objects that carry unit as a runtime
# field; NewTypes are for function-boundary annotation where the unit is
# statically known.
#
# RESOLVED (F3 PR 4, 2026-05-19): NewType arithmetic gap closed at ingest boundary
# via CelsiusBox/FahrenheitBox frozen dataclasses with cross-unit __add__ → TypeError.
# Hot signal/evaluator loops continue to use bare float (documented design in
# day0_router.py). Constraint #1 ("make wrong code unwritable") achieved at the
# source-of-truth layer.
# ---------------------------------------------------------------------------

Celsius = NewType("Celsius", float)
Fahrenheit = NewType("Fahrenheit", float)

# Decimal variants for settlement math (SettlementRoundingPolicy paths).
CelsiusDecimal = NewType("CelsiusDecimal", Decimal)
FahrenheitDecimal = NewType("FahrenheitDecimal", Decimal)


def degC(value: float) -> Celsius:
    """Wrap a float as Celsius — makes unit intent explicit at call sites."""
    return Celsius(value)


def degF(value: float) -> Fahrenheit:
    """Wrap a float as Fahrenheit — makes unit intent explicit at call sites."""
    return Fahrenheit(value)


def degC_d(value: Decimal) -> CelsiusDecimal:
    """Wrap a Decimal as CelsiusDecimal for settlement-precision paths."""
    return CelsiusDecimal(value)


def f_to_c(value: Fahrenheit) -> Celsius:
    """Convert Fahrenheit to Celsius.  Typed so mixed-unit addition fails mypy."""
    return Celsius((float(value) - 32.0) * 5.0 / 9.0)


def c_to_f(value: Celsius) -> Fahrenheit:
    """Convert Celsius to Fahrenheit.  Typed so mixed-unit addition fails mypy."""
    return Fahrenheit(float(value) * 9.0 / 5.0 + 32.0)


# ---------------------------------------------------------------------------
# CelsiusBox / FahrenheitBox — runtime-enforced unit wrappers
#
# Purpose: frozen dataclasses with cross-unit __add__ → TypeError.
# Use at ingest boundary where source unit is statically known.
# Hot loops extract `.value` before entering tight iteration — see
# day0_router.py rationale. Coexists with Celsius/Fahrenheit NewTypes
# (which serve typed-signature contracts at function boundaries).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CelsiusBox:
    """Runtime-enforced °C value. Cross-unit arithmetic raises TypeError.

    Use at ingest boundary where source unit is statically known
    (Ogimet/HKO/Meteostat=°C). Hot loops should extract `.value`
    before entering tight iteration — see day0_router.py rationale.
    """

    value: float

    def __add__(self, other: object) -> "CelsiusBox":
        if isinstance(other, CelsiusBox):
            return CelsiusBox(self.value + other.value)
        if isinstance(other, FahrenheitBox):
            raise TypeError("Cannot add Celsius + Fahrenheit; convert first via f_to_c")
        return NotImplemented  # bare int/float rejected — unit unknown

    def __radd__(self, other: object) -> "CelsiusBox":
        return self.__add__(other)

    def __sub__(self, other: object) -> "CelsiusBox":
        if isinstance(other, CelsiusBox):
            return CelsiusBox(self.value - other.value)
        if isinstance(other, FahrenheitBox):
            raise TypeError("Cannot subtract Fahrenheit from Celsius; convert first")
        return NotImplemented  # bare int/float rejected — unit unknown

    def add_delta(self, delta: float) -> "CelsiusBox":
        """Add a same-unit scalar delta. Use only when the delta is unit-confirmed."""
        return CelsiusBox(self.value + float(delta))

    def sub_delta(self, delta: float) -> "CelsiusBox":
        """Subtract a same-unit scalar delta. Use only when the delta is unit-confirmed."""
        return CelsiusBox(self.value - float(delta))

    def to_fahrenheit(self) -> "FahrenheitBox":
        return FahrenheitBox(self.value * 9.0 / 5.0 + 32.0)


@dataclass(frozen=True, slots=True)
class FahrenheitBox:
    """Runtime-enforced °F value. Cross-unit arithmetic raises TypeError.

    Use at ingest boundary where source unit is statically known
    (WU/IEM ASOS=°F). Hot loops should extract `.value` before entering
    tight iteration — see day0_router.py rationale.
    """

    value: float

    def __add__(self, other: object) -> "FahrenheitBox":
        if isinstance(other, FahrenheitBox):
            return FahrenheitBox(self.value + other.value)
        if isinstance(other, CelsiusBox):
            raise TypeError("Cannot add Fahrenheit + Celsius; convert first via c_to_f")
        return NotImplemented  # bare int/float rejected — unit unknown

    def __radd__(self, other: object) -> "FahrenheitBox":
        return self.__add__(other)

    def __sub__(self, other: object) -> "FahrenheitBox":
        if isinstance(other, FahrenheitBox):
            return FahrenheitBox(self.value - other.value)
        if isinstance(other, CelsiusBox):
            raise TypeError("Cannot subtract Celsius from Fahrenheit; convert first")
        return NotImplemented  # bare int/float rejected — unit unknown

    def add_delta(self, delta: float) -> "FahrenheitBox":
        """Add a same-unit scalar delta. Use only when the delta is unit-confirmed."""
        return FahrenheitBox(self.value + float(delta))

    def sub_delta(self, delta: float) -> "FahrenheitBox":
        """Subtract a same-unit scalar delta. Use only when the delta is unit-confirmed."""
        return FahrenheitBox(self.value - float(delta))

    def to_celsius(self) -> CelsiusBox:
        return CelsiusBox((self.value - 32.0) * 5.0 / 9.0)


def degC_boxed(value: float) -> CelsiusBox:
    """Wrap a float as CelsiusBox — runtime-enforced unit witness at ingest boundaries."""
    return CelsiusBox(float(value))


def degF_boxed(value: float) -> FahrenheitBox:
    """Wrap a float as FahrenheitBox — runtime-enforced unit witness at ingest boundaries."""
    return FahrenheitBox(float(value))


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

    def __truediv__(self, other: object) -> "TemperatureDelta | float":
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
