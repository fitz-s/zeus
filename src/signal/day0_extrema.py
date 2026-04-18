# Lifecycle: created=2026-04-18; last_reviewed=2026-04-18; last_reused=never
# Authority basis: phase6_contract.md R-BF
"""RemainingMemberExtrema — typed container for Day0 window ENS arrays.

Replaces the bare np.ndarray return from remaining_member_maxes_for_day0.
HIGH path sets maxes, mins=None. LOW path sets mins, maxes=None.
Construction with both None raises — metric-mismatch is caught at Router boundary.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.types.metric_identity import MetricIdentity


@dataclass(frozen=True)
class RemainingMemberExtrema:
    maxes: np.ndarray | None
    mins: np.ndarray | None

    def __post_init__(self) -> None:
        if self.maxes is None and self.mins is None:
            raise ValueError(
                "RemainingMemberExtrema: both maxes and mins are None. "
                "Exactly one must be populated (HIGH sets maxes, LOW sets mins)."
            )

    @classmethod
    def for_metric(cls, arr: np.ndarray, temperature_metric: MetricIdentity) -> "RemainingMemberExtrema":
        if temperature_metric.is_low():
            return cls(maxes=None, mins=arr)
        return cls(maxes=arr, mins=None)
