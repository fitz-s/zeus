"""Decision-time truth with typed availability provenance.

D4 antibody (per packet 2026-04-27 §01 §5): replaces the doc-level
DIAGNOSTIC_REPLAY_REFERENCE_SOURCES frozenset in src/engine/replay.py with
a typed enum that callers must declare. Backtest purposes refuse provenance
tiers below their authority — F11 hindsight leakage becomes unconstructable
rather than discouraged.

ECMWF ENS dissemination schedule cited from
https://confluence.ecmwf.int/display/DAC/Dissemination+schedule
(verified 2026-04-27): Day N forecast for given base_time available at
base + 6h40min + N×4min.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from src.backtest.purpose import BacktestPurpose


class AvailabilityProvenance(str, Enum):
    FETCH_TIME = "fetch_time"
    RECORDED = "recorded"
    DERIVED_FROM_DISSEMINATION = "derived_dissemination"
    RECONSTRUCTED = "reconstructed"


_PROMOTION_GRADE = frozenset({
    AvailabilityProvenance.FETCH_TIME,
    AvailabilityProvenance.RECORDED,
})


@dataclass(frozen=True)
class DecisionTimeTruth:
    snapshot_id: str
    available_at: datetime
    provenance: AvailabilityProvenance

    def is_promotion_grade(self) -> bool:
        return self.provenance in _PROMOTION_GRADE

    def is_diagnostic_only(self) -> bool:
        return self.provenance not in _PROMOTION_GRADE


class HindsightLeakageRefused(RuntimeError):
    """Raised when a snapshot's provenance is too soft for the requested purpose."""


def gate_for_purpose(
    truth: DecisionTimeTruth,
    purpose: BacktestPurpose,
) -> DecisionTimeTruth:
    """Per-purpose acceptance gate.

    ECONOMICS: only FETCH_TIME or RECORDED — DERIVED is too soft for
        promotion-grade PnL claims.
    SKILL: rejects RECONSTRUCTED — heuristic timestamps corrupt forecast
        skill scoring.
    DIAGNOSTIC: accepts all — purpose is to surface code/history divergence,
        not to make PnL or skill claims.
    """
    if purpose is BacktestPurpose.ECONOMICS and not truth.is_promotion_grade():
        raise HindsightLeakageRefused(
            f"ECONOMICS purpose requires FETCH_TIME or RECORDED provenance; "
            f"got {truth.provenance.value} on snapshot {truth.snapshot_id}"
        )
    if (
        purpose is BacktestPurpose.SKILL
        and truth.provenance is AvailabilityProvenance.RECONSTRUCTED
    ):
        raise HindsightLeakageRefused(
            f"SKILL purpose refuses RECONSTRUCTED provenance "
            f"(heuristic timestamp corrupts skill scoring); "
            f"snapshot {truth.snapshot_id}"
        )
    return truth


def ecmwf_ens_available_at(base_time: datetime, lead_day: int) -> datetime:
    """ECMWF ENS dissemination schedule.

    Source (verified 2026-04-27):
    https://confluence.ecmwf.int/display/DAC/Dissemination+schedule

    Day 0 forecast available at base + 6h40min; each additional lead day
    adds ~4 minutes (e.g. Day 15 at base + 7h40min).
    """
    if lead_day < 0:
        raise ValueError(f"lead_day must be non-negative; got {lead_day}")
    return base_time + timedelta(hours=6, minutes=40 + 4 * lead_day)
