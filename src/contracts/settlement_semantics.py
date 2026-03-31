from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class SettlementSemantics:
    """Every market's unique resolution rules. Drifts in rounding/precision are fatal errors.
    
    Replaces the global assumption of "WU integer rounding" 
    with a typed, per-market object.
    """
    resolution_source: str  # e.g., "WU_LaGuardia", "CWA_Taipei"
    measurement_unit: Literal["F", "C"]
    precision: float        # 1.0 = whole degrees, 0.1 = one decimal
    rounding_rule: Literal["round_half_to_even", "floor", "ceil"]
    finalization_time: str  # "12:00:00Z"
    
    @classmethod
    def default_wu_fahrenheit(cls, city_code: str) -> "SettlementSemantics":
        """Fallback for legacy Polymarket USA city contracts."""
        return cls(
            resolution_source=f"WU_{city_code}",
            measurement_unit="F",
            precision=1.0,
            rounding_rule="round_half_to_even",
            finalization_time="12:00:00Z"
        )
