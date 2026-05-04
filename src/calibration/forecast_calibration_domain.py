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


# ----------------------------------------------------------------------
# PR #55 helpers (preserved through PR #56 merge): standalone utilities
# that don't depend on the domain class above. Used by evaluator and
# monitor_refresh to thread Phase-2 stratification keys from ens_result
# into get_calibrator. Kept here so callers have one canonical import
# site for forecast-domain-related helpers.
# ----------------------------------------------------------------------

import re as _re
from typing import Optional

_ISO_HHMM_RE = _re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})")


def parse_cycle_from_issue_time(issue_time_iso: Optional[str]) -> Optional[str]:
    """Extract cycle_hour_utc (2-char HH) from an ISO-8601 issue_time string.

    Returns None for unparseable input.  Tolerates trailing timezone
    designators including 'Z' and '+HH:MM'.
    """
    if not isinstance(issue_time_iso, str):
        return None
    match = _ISO_HHMM_RE.match(issue_time_iso)
    if match is None:
        return None
    return match.group(4)


def derive_phase2_keys_from_ens_result(
    ens_result: Optional[dict],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Derive (cycle_hour_utc, source_id, horizon_profile) from an ens_result.

    Phase 2 stratification needs these three keys to look up the right Platt
    bucket. ens_result producers populate ``issue_time`` (str OR datetime,
    depending on whether the registered-ingest path was used) and
    ``source_id``; ``horizon_profile`` is *not* populated upstream as of
    2026-05-04, so we derive it from cycle (00/12 → 'full', else 'short').

    Copilot review #4 + #5 (2026-05-04): horizon_profile derivation when
    ``ens_result['horizon_profile']`` is absent.
    Codex P1 review #7 (2026-05-04): handle ``datetime`` issue_time in
    addition to str — the registered-ingest path puts a datetime here.

    Returns (None, None, None) on malformed input.
    """
    if not isinstance(ens_result, dict):
        return None, None, None
    cycle: Optional[str] = None
    source_id: Optional[str] = None
    horizon_profile: Optional[str] = None
    try:
        it = ens_result.get("issue_time")
        if isinstance(it, str) and len(it) >= 13:
            cycle = it[11:13]
        elif hasattr(it, "hour"):
            cycle = f"{int(it.hour):02d}"
        sid = ens_result.get("source_id")
        if isinstance(sid, str) and sid:
            source_id = sid
        hp = ens_result.get("horizon_profile")
        if isinstance(hp, str) and hp:
            horizon_profile = hp
        if horizon_profile is None and cycle is not None:
            # 00/12 → full-horizon TIGGE/OpenData runs; other cycles → short.
            horizon_profile = "full" if cycle in ("00", "12") else "short"
    except (TypeError, AttributeError, KeyError):
        return None, None, None
    return cycle, source_id, horizon_profile


def derive_source_id_from_data_version(data_version: Optional[str]) -> Optional[str]:
    """Map a data_version string to its canonical source_id.

    'tigge_*'           → 'tigge_mars'
    'ecmwf_opendata_*'  → 'ecmwf_open_data'
    anything else       → None  (caller should reject as
                                 UNKNOWN_FORECAST_SOURCE_FAMILY)
    """
    if not isinstance(data_version, str) or not data_version:
        return None
    if data_version.startswith("tigge_"):
        return "tigge_mars"
    if data_version.startswith("ecmwf_opendata_"):
        return "ecmwf_open_data"
    return None
