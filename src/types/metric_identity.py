"""MetricIdentity: first-class type for temperature-track identity.

This is the single typed representation of "high" vs "low" temperature markets.
The one legal string→MetricIdentity conversion point is MetricIdentity.from_raw().
All signal classes (Day0Signal, EnsembleSignal, day0_window) accept only MetricIdentity,
never bare str.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Union


@dataclass(frozen=True)
class MetricIdentity:
    """First-class identity for a temperature-market family.

    Immutable. Validated at construction: cross-pairings (high metric + low_temp
    observation field, or vice versa) raise ValueError immediately.

    The one legal str→MetricIdentity conversion is MetricIdentity.from_raw(value).
    """

    temperature_metric: Literal["high", "low"]
    physical_quantity: str
    observation_field: Literal["high_temp", "low_temp"]
    data_version: str

    def __post_init__(self) -> None:
        if self.temperature_metric == "high" and self.observation_field != "high_temp":
            raise ValueError(
                f"Cross-pairing: temperature_metric='high' requires observation_field='high_temp', "
                f"got {self.observation_field!r}"
            )
        if self.temperature_metric == "low" and self.observation_field != "low_temp":
            raise ValueError(
                f"Cross-pairing: temperature_metric='low' requires observation_field='low_temp', "
                f"got {self.observation_field!r}"
            )

    def is_high(self) -> bool:
        """True if this identity tracks the daily high (max) temperature."""
        return self.temperature_metric == "high"

    def is_low(self) -> bool:
        """True if this identity tracks the daily low (min) temperature."""
        return self.temperature_metric == "low"

    @classmethod
    def from_raw(cls, value: Union[str, "MetricIdentity"]) -> "MetricIdentity":
        """Normalize a raw string or passthrough a MetricIdentity instance.

        This is the ONLY legal str→MetricIdentity conversion point in the codebase.
        Callers outside _normalize_temperature_metric in evaluator.py must import
        this classmethod explicitly — no implicit coercion anywhere.

        Args:
            value: "high" → HIGH_LOCALDAY_MAX, "low" → LOW_LOCALDAY_MIN,
                   MetricIdentity → returned unchanged.

        Raises:
            ValueError: if value is a string not in {"high", "low"}.
        """
        if isinstance(value, MetricIdentity):
            return value
        if value == "high":
            return HIGH_LOCALDAY_MAX
        if value == "low":
            return LOW_LOCALDAY_MIN
        raise ValueError(
            f"Unknown temperature metric string: {value!r}. "
            f"Expected 'high' or 'low', or pass a MetricIdentity instance."
        )


# Canonical module-level constants. These are the two legal metric identities.
# data_version strings match zeus_dual_track_architecture.md §2.2.
HIGH_LOCALDAY_MAX = MetricIdentity(
    temperature_metric="high",
    physical_quantity="mx2t6_local_calendar_day_max",
    observation_field="high_temp",
    data_version="tigge_mx2t6_local_calendar_day_max_v1",
)

LOW_LOCALDAY_MIN = MetricIdentity(
    temperature_metric="low",
    physical_quantity="mn2t6_local_calendar_day_min",
    observation_field="low_temp",
    data_version="tigge_mn2t6_local_calendar_day_min_v1",
)
