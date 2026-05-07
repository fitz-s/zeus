# Lifecycle: created=2026-04-18; last_reviewed=2026-04-18; last_reused=never
# Purpose: Phase 7B — central home for CalibrationMetricSpec dataclass and METRIC_SPECS tuple
#          (previously lived in scripts/rebuild_calibration_pairs_v2.py, cross-script-imported
#          by refit_platt_v2.py and backfill_tigge_snapshot_p_raw_v2.py)
# Reuse: source of truth for calibration per-metric iteration

from __future__ import annotations

from dataclasses import dataclass

from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
    ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
    TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION,
)
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN, MetricIdentity


@dataclass(frozen=True)
class CalibrationMetricSpec:
    identity: MetricIdentity
    allowed_data_version: str
    allowed_data_versions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.allowed_data_versions:
            object.__setattr__(
                self,
                "allowed_data_versions",
                (self.allowed_data_version,),
            )
        elif self.allowed_data_version not in self.allowed_data_versions:
            object.__setattr__(
                self,
                "allowed_data_versions",
                (self.allowed_data_version, *self.allowed_data_versions),
            )

    def allows_data_version(self, data_version: str) -> bool:
        return data_version in self.allowed_data_versions


METRIC_SPECS: tuple[CalibrationMetricSpec, ...] = (
    CalibrationMetricSpec(
        HIGH_LOCALDAY_MAX,
        HIGH_LOCALDAY_MAX.data_version,
        (
            HIGH_LOCALDAY_MAX.data_version,
            ECMWF_OPENDATA_HIGH_DATA_VERSION,
        ),
    ),
    CalibrationMetricSpec(
        LOW_LOCALDAY_MIN,
        LOW_LOCALDAY_MIN.data_version,
        (
            LOW_LOCALDAY_MIN.data_version,
            ECMWF_OPENDATA_LOW_DATA_VERSION,
            TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION,
            ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
        ),
    ),
)
