# Lifecycle: created=2026-04-18; last_reviewed=2026-04-18; last_reused=never
# Purpose: Phase 7B — central home for CalibrationMetricSpec dataclass and METRIC_SPECS tuple
#          (previously lived in scripts/rebuild_calibration_pairs_v2.py, cross-script-imported
#          by refit_platt_v2.py and backfill_tigge_snapshot_p_raw_v2.py)
# Reuse: source of truth for calibration per-metric iteration

from __future__ import annotations

from dataclasses import dataclass

from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN, MetricIdentity


@dataclass(frozen=True)
class CalibrationMetricSpec:
    identity: MetricIdentity
    allowed_data_version: str


METRIC_SPECS: tuple[CalibrationMetricSpec, ...] = (
    CalibrationMetricSpec(HIGH_LOCALDAY_MAX, HIGH_LOCALDAY_MAX.data_version),
    CalibrationMetricSpec(LOW_LOCALDAY_MIN, LOW_LOCALDAY_MIN.data_version),
)
