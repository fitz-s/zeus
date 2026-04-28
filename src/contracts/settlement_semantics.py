from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import ClassVar, Literal

import logging
import numpy as np

from src.contracts.exceptions import SettlementPrecisionError

logger = logging.getLogger(__name__)

RoundingRule = Literal["wmo_half_up", "floor", "ceil", "oracle_truncate"]


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


def apply_settlement_rounding(values, round_fn, precision: float = 1.0) -> np.ndarray:
    """B081: shared settlement-rounding dispatch.

    Uses injected round_fn if provided (e.g., oracle_truncate for HKO),
    otherwise falls back to WMO asymmetric half-up: floor(x + 0.5).
    Result is float, not int - callers use >= / <= comparisons on Bin bounds.

    Consolidates duplicated logic previously in
    `src/strategy/market_analysis.py::MarketAnalysis._settle` and
    `src/signal/day0_signal.py::Day0Signal._settle`. Flagged YELLOW because
    a future unification with EnsembleSignal's SettlementSemantics-injected
    round_values() path should route through here too.
    """
    if round_fn is not None:
        return round_fn(values)
    return round_wmo_half_up_values(values, precision)


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
        elif self.rounding_rule in ("floor", "oracle_truncate"):
            # DANGER: oracle_truncate 仅限 HKO 等受到 UMA 截断偏见污染
            # 的合约使用！严禁用于正常的气象学 P_raw 模拟！
            #
            # UMA voters treat decimal °C as truncated: "28.7 hasn't
            # reached 29, so it's 28". Empirically verified: floor()
            # achieves 14/14 (100%) match on HKO same-source settlement
            # days vs 5/14 (36%) with wmo_half_up.
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
        source_type = city.settlement_source_type
        if source_type == "wu_icao":
            # WU-based settlement (default path)
            if city.settlement_unit == "C":
                return cls.default_wu_celsius(city.wu_station)
            return cls.default_wu_fahrenheit(city.wu_station)

        # Non-WU settlement sources
        if source_type == "hko":
            # DANGER: oracle_truncate 仅限 HKO！严禁用于其他城市！
            # HKO reports 0.1°C precision. UMA voters apply truncation
            # ("28.7 → 28"), not WMO half-up rounding ("28.7 → 29").
            # Verified: floor() achieves 14/14 (100%) match on HKO
            # same-source days vs 5/14 (36%) with wmo_half_up.
            return cls(
                resolution_source="HKO_HQ",
                measurement_unit="C",
                precision=1.0,
                rounding_rule="oracle_truncate",
                finalization_time="12:00:00Z",
            )

        # CWA, NOAA, etc. — default to WMO half-up
        return cls(
            resolution_source=f"{source_type}_{city.wu_station}",
            measurement_unit=city.settlement_unit,
            precision=1.0,
            rounding_rule="wmo_half_up",
            finalization_time="12:00:00Z",
        )


# Created: 2026-04-27 (BATCH C of 2026-04-27 harness debate executor work)
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-27_harness_debate/round2_verdict.md
#   §1.1 #4 + §4.1 #4 (both proponent + opponent endorsed type-encoded HK HKO
#   antibody; opponent §3.1 has the template). Per Fitz Constraint #1 "make the
#   category impossible, not just the instance".
#
# This block APPENDS a parallel type-encoded settlement-rounding policy. It does
# NOT replace the existing SettlementSemantics.round_values() string-dispatch
# path; that migration is Tier 3 P8 territory. New code paths SHOULD use this
# policy ABC; existing callers continue working unchanged.

class SettlementRoundingPolicy(ABC):
    """Type-encoded settlement-rounding policy. Replaces YAML antibody for
    HK HKO truncation vs WMO half-up cross-city mixing with a TypeError at
    the call site (per Fitz Constraint #1: make the category impossible).

    Subclasses MUST set the ClassVar `name` to a stable string identifier and
    implement `round_to_settlement` + `source_authority`. Mixing policies
    across incompatible markets raises TypeError in `settle_market`.
    """
    name: ClassVar[str]

    @abstractmethod
    def round_to_settlement(self, raw_temp_c: Decimal) -> int:
        """Round a raw temperature to the integer settlement value."""

    @abstractmethod
    def source_authority(self) -> str:
        """Return the authority string for this policy (e.g., 'WMO', 'HKO')."""


class WMO_HalfUp(SettlementRoundingPolicy):
    """WMO half-up: 74.45 → 74; 74.50 → 75. WU/NOAA/CWA settlement chains."""
    name: ClassVar[str] = "wmo_half_up"

    def round_to_settlement(self, raw_temp_c: Decimal) -> int:
        return int(raw_temp_c.quantize(Decimal('1'), rounding=ROUND_HALF_UP))

    def source_authority(self) -> str:
        return "WMO"


class HKO_Truncation(SettlementRoundingPolicy):
    """HKO truncation: 74.99 → 74. Hong Kong settlement chain ONLY.

    UMA voters treat decimal °C as truncated ('28.7 hasn't reached 29, so it's
    28'). Empirically verified: floor() achieves 14/14 (100%) match on HKO
    same-source settlement days vs 5/14 (36%) with WMO half-up.
    """
    name: ClassVar[str] = "hko_truncation"

    def round_to_settlement(self, raw_temp_c: Decimal) -> int:
        return int(raw_temp_c.quantize(Decimal('1'), rounding=ROUND_DOWN))

    def source_authority(self) -> str:
        return "HKO"


def settle_market(city_name: str, raw_temp_c: Decimal,
                  policy: SettlementRoundingPolicy) -> int:
    """Apply the rounding policy to raw °C, with type-encoded city/policy match.

    HK markets REQUIRE HKO_Truncation; non-HK markets REQUIRE non-HKO policy.
    Mismatch raises TypeError BEFORE any rounding happens — i.e., the wrong
    rounding for the wrong city is structurally unconstructable. Per Fitz
    Constraint #1 (make the category impossible).
    """
    if not isinstance(policy, SettlementRoundingPolicy):
        raise TypeError(
            f"settle_market requires a SettlementRoundingPolicy instance; "
            f"got {type(policy).__name__}"
        )
    if city_name == "Hong Kong" and not isinstance(policy, HKO_Truncation):
        raise TypeError(
            f"Hong Kong markets require HKO_Truncation policy; "
            f"got {type(policy).__name__}"
        )
    if city_name != "Hong Kong" and isinstance(policy, HKO_Truncation):
        raise TypeError(
            f"HKO_Truncation policy is valid for Hong Kong only; "
            f"got city={city_name!r}"
        )
    return policy.round_to_settlement(raw_temp_c)
