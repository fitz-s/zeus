from dataclasses import dataclass
from typing import Literal

import logging
import numpy as np

from src.contracts.exceptions import SettlementPrecisionError

logger = logging.getLogger(__name__)

RoundingRule = Literal["wmo_half_up", "floor", "ceil"]


def round_wmo_half_up_values(values, precision: float = 1.0) -> np.ndarray:
    """Round values using WMO asymmetric half-up semantics.

    WU/NWS integer temperature displays follow WMO half-up on the number line:
    floor(x + 0.5). This differs from Python/NumPy banker's rounding and from
    half-away-from-zero for negative values.
    """
    arr = np.asarray(values, dtype=float)
    inv = 1.0 / precision if precision > 0 else 1.0
    scaled = arr * inv
    return np.floor(scaled + 0.5) / inv


def round_wmo_half_up_value(value: float, precision: float = 1.0) -> float:
    """Round one value using WMO asymmetric half-up semantics."""
    return float(round_wmo_half_up_values([value], precision)[0])


@dataclass(frozen=True)
class SettlementSemantics:
    """Every market's unique resolution rules. Drifts in rounding/precision are fatal errors.
    
    Replaces the global assumption of "WU integer rounding" 
    with a typed, per-market object.
    """
    resolution_source: str  # e.g., "WU_LaGuardia", "CWA_Taipei"
    measurement_unit: Literal["F", "C"]
    precision: float        # 1.0 = whole degrees, 0.1 = one decimal
    rounding_rule: RoundingRule
    finalization_time: str  # "12:00:00Z"

    def round_values(self, values):
        """Apply settlement rounding according to this market contract."""
        arr = np.asarray(values, dtype=float)
        inv = 1.0 / self.precision if self.precision > 0 else 1.0
        scaled = arr * inv

        if self.rounding_rule == "wmo_half_up":
            rounded = np.floor(scaled + 0.5)
        elif self.rounding_rule == "floor":
            rounded = np.floor(scaled)
        elif self.rounding_rule == "ceil":
            rounded = np.ceil(scaled)
        else:
            raise ValueError(f"Unsupported settlement rounding rule: {self.rounding_rule}")

        return rounded / inv

    def round_single(self, value: float) -> float:
        """Round a single settlement value to contract precision.

        This is the MANDATORY gate for all settlement DB writes.
        No code path may store a settlement_value without calling this first.
        """
        return float(self.round_values([value])[0])

    def assert_settlement_value(self, value: float, *, context: str = "") -> float:
        """Validate and round a settlement value. Returns the rounded value.

        Raises SettlementPrecisionError if the raw value is NaN or infinite.
        Always rounds to contract precision (integer for all current markets).

        Usage at every DB write boundary:
            sem = SettlementSemantics.for_city(city)
            settlement_value = sem.assert_settlement_value(raw_temp, context="wu_daily_collector")
        """
        if not np.isfinite(value):
            raise SettlementPrecisionError(
                f"Settlement value is not finite: {value}. "
                f"Contract: {self.resolution_source}, unit={self.measurement_unit}. "
                f"Context: {context}"
            )
        rounded = self.round_single(value)
        delta = abs(value - rounded)
        if delta > 1e-9:
            logger.debug(
                "Settlement value %.1f rounded to %.0f (delta=%.1f) [%s] %s",
                value, rounded, delta, self.resolution_source, context,
            )
        return rounded
    
    @classmethod
    def default_wu_fahrenheit(cls, city_code: str) -> "SettlementSemantics":
        """Polymarket USA city contracts: WU integer °F with WMO half-up rounding."""
        return cls(
            resolution_source=f"WU_{city_code}",
            measurement_unit="F",
            precision=1.0,
            rounding_rule="wmo_half_up",
            finalization_time="12:00:00Z"
        )

    @classmethod
    def default_wu_celsius(cls, city_code: str) -> "SettlementSemantics":
        """Polymarket international city contracts: WU integer °C.

        Polymarket °C markets use 1°C point bins (e.g., "4°C", "5°C").
        Settlement rounds to integer °C, same WMO half-up rounding rule as °F.
        """
        return cls(
            resolution_source=f"WU_{city_code}",
            measurement_unit="C",
            precision=1.0,
            rounding_rule="wmo_half_up",
            finalization_time="12:00:00Z"
        )

    @classmethod
    def for_city(cls, city) -> "SettlementSemantics":
        """Construct appropriate SettlementSemantics from a City object.

        This is the single entry point. Do NOT call default_wu_fahrenheit
        for °C cities.
        """
        source = getattr(city, 'settlement_source', '')
        if not source or "wunderground.com" in source:
            # WU-based settlement (default path)
            if city.settlement_unit == "C":
                return cls.default_wu_celsius(city.wu_station)
            return cls.default_wu_fahrenheit(city.wu_station)

        # Non-WU settlement sources (e.g., HK Observatory)
        # Same precision/rounding contract, different resolution source
        return cls(
            resolution_source=city.wu_station,
            measurement_unit=city.settlement_unit,
            precision=1.0,
            rounding_rule="wmo_half_up",
            finalization_time="12:00:00Z",
        )
