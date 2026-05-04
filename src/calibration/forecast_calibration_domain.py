"""Forecast calibration domain identity.

This is the Phase 2.5 base contract from may4math: source/cycle/metric/domain
identity is data, not prose. Runtime wiring can adopt it later without changing
the object shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Metric = Literal["high", "low"]


class ForecastCalibrationDomainMismatch(ValueError):
    """Raised when a forecast and calibrator describe different domains."""


@dataclass(frozen=True, slots=True)
class ForecastCalibrationDomain:
    source_id: str
    data_version: str
    source_cycle_hour_utc: int
    horizon_profile: str
    metric: Metric
    cluster: str
    season: str
    input_space: str
    city_local_cycle_hour: int | None = None

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("source_id is required")
        if not self.data_version.strip():
            raise ValueError("data_version is required")
        if not 0 <= self.source_cycle_hour_utc <= 23:
            raise ValueError(
                f"source_cycle_hour_utc must be in [0, 23], got {self.source_cycle_hour_utc}"
            )
        if self.metric not in ("high", "low"):
            raise ValueError(f"metric must be 'high' or 'low', got {self.metric!r}")
        if not self.cluster.strip():
            raise ValueError("cluster is required")
        if not self.season.strip():
            raise ValueError("season is required")
        if not self.input_space.strip():
            raise ValueError("input_space is required")
        if self.city_local_cycle_hour is not None and not 0 <= self.city_local_cycle_hour <= 23:
            raise ValueError(
                f"city_local_cycle_hour must be in [0, 23], got {self.city_local_cycle_hour}"
            )

    @property
    def key(self) -> str:
        local_hour = "na" if self.city_local_cycle_hour is None else f"{self.city_local_cycle_hour:02d}"
        return ":".join((
            self.source_id,
            self.data_version,
            f"cycle{self.source_cycle_hour_utc:02d}z",
            self.horizon_profile,
            self.metric,
            self.cluster,
            self.season,
            self.input_space,
            f"local{local_hour}",
        ))

    def mismatch_fields(self, other: "ForecastCalibrationDomain") -> tuple[str, ...]:
        fields = (
            "source_id",
            "data_version",
            "source_cycle_hour_utc",
            "horizon_profile",
            "metric",
            "cluster",
            "season",
            "input_space",
            "city_local_cycle_hour",
        )
        return tuple(field for field in fields if getattr(self, field) != getattr(other, field))

    def matches(self, other: "ForecastCalibrationDomain") -> bool:
        return not self.mismatch_fields(other)

    def assert_matches(self, other: "ForecastCalibrationDomain") -> None:
        mismatches = self.mismatch_fields(other)
        if mismatches:
            raise ForecastCalibrationDomainMismatch(
                "CALIBRATION_DOMAIN_MISMATCH: " + ",".join(mismatches)
            )
