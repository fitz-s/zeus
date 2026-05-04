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

    @classmethod
    def for_high_localday_max(cls, source_family: str) -> "MetricIdentity":
        """Phase 2.6 factory: source-family-aware HIGH_LOCALDAY_MAX MetricIdentity.

        Returns a MetricIdentity whose data_version reflects the forecast
        source actually being used at runtime (TIGGE archive vs ECMWF Open Data
        vs etc.), instead of the legacy hardcoded TIGGE data_version.

        Args:
            source_family: 'tigge' (TIGGE archive) or 'ecmwf_opendata' (ECMWF
                Open Data live feed). Other values raise ValueError; callers
                must reject UNKNOWN_FORECAST_SOURCE_FAMILY upstream.

        Raises:
            ValueError: if source_family is not in the registry below.
        """
        dv = _HIGH_DATA_VERSION_BY_SOURCE_FAMILY.get(source_family)
        if dv is None:
            raise ValueError(
                f"Unknown source_family for high-localday-max: {source_family!r}. "
                f"Expected one of {sorted(_HIGH_DATA_VERSION_BY_SOURCE_FAMILY.keys())!r}."
            )
        return cls(
            temperature_metric="high",
            physical_quantity="mx2t6_local_calendar_day_max",
            observation_field="high_temp",
            data_version=dv,
        )

    @classmethod
    def for_low_localday_min(cls, source_family: str) -> "MetricIdentity":
        """Phase 2.6 factory: source-family-aware LOW_LOCALDAY_MIN MetricIdentity."""
        dv = _LOW_DATA_VERSION_BY_SOURCE_FAMILY.get(source_family)
        if dv is None:
            raise ValueError(
                f"Unknown source_family for low-localday-min: {source_family!r}. "
                f"Expected one of {sorted(_LOW_DATA_VERSION_BY_SOURCE_FAMILY.keys())!r}."
            )
        return cls(
            temperature_metric="low",
            physical_quantity="mn2t6_local_calendar_day_min",
            observation_field="low_temp",
            data_version=dv,
        )

    @classmethod
    def for_metric_with_source_family(
        cls, temperature_metric: str, source_family: str
    ) -> "MetricIdentity":
        """Convenience dispatcher used at the live evaluator boundary."""
        if temperature_metric == "high":
            return cls.for_high_localday_max(source_family)
        if temperature_metric == "low":
            return cls.for_low_localday_min(source_family)
        raise ValueError(
            f"Unknown temperature_metric: {temperature_metric!r}. "
            f"Expected 'high' or 'low'."
        )


# Phase 2.6: source-family → data_version registry.
# Adding a new source family = add an entry here AND ensure ingest pipeline
# writes ensemble_snapshots_v2.data_version with the matching string.
_HIGH_DATA_VERSION_BY_SOURCE_FAMILY: dict[str, str] = {
    "tigge": "tigge_mx2t6_local_calendar_day_max_v1",
    "ecmwf_opendata": "ecmwf_opendata_mx2t6_local_calendar_day_max_v1",
}
_LOW_DATA_VERSION_BY_SOURCE_FAMILY: dict[str, str] = {
    "tigge": "tigge_mn2t6_local_calendar_day_min_v1",
    "ecmwf_opendata": "ecmwf_opendata_mn2t6_local_calendar_day_min_v1",
}


def source_family_from_data_version(data_version: str) -> str | None:
    """Reverse-lookup helper: data_version → source_family.

    Returns None if the data_version doesn't match any registered source.
    Callers (evaluator) treat None as UNKNOWN_FORECAST_SOURCE_FAMILY rejection.
    """
    if not isinstance(data_version, str) or not data_version:
        return None
    if data_version.startswith("tigge_"):
        return "tigge"
    if data_version.startswith("ecmwf_opendata_"):
        return "ecmwf_opendata"
    return None


# Phase 2.6 hardening (2026-05-04, critic-opus BLOCKER 1): map runtime
# source_id strings to source_family keys so live-fetch paths (where the
# data_version isn't yet known at parse time) can populate a typed
# data_version into ens_result. Without this, _parse_response returned
# ens_result without data_version, causing evaluator's
# UNKNOWN_FORECAST_SOURCE_FAMILY gate to silently skip every fetch.
_SOURCE_ID_TO_SOURCE_FAMILY: dict[str, str] = {
    "tigge": "tigge",
    "tigge_mars": "tigge",
    "ecmwf_open_data": "ecmwf_opendata",
}


def source_family_from_source_id(source_id: str) -> str | None:
    """Map a runtime source_id (as written into ens_result['source_id']) to
    the source_family key used by the data_version factories.

    Returns None for unrecognized sources; callers treat None as
    UNKNOWN_FORECAST_SOURCE_FAMILY rejection (see evaluator gate).
    """
    if not isinstance(source_id, str) or not source_id:
        return None
    return _SOURCE_ID_TO_SOURCE_FAMILY.get(source_id)


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
