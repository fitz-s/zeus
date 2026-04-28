# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/F1.yaml
"""Typed forecast ingest protocol for R3 F1.

This module is deliberately a K2 data-surface protocol, not a K0 contract:
F1 wires forecast-source provenance and dormant source gates without changing
settlement/source authority, calibration training, or signal mathematics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, Sequence


ForecastAuthorityTier = Literal["GROUND_TRUTH", "FORECAST", "DERIVED"]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ForecastSourceHealth:
    """Read-only health result for a forecast source."""

    source_id: str
    ok: bool
    checked_at: datetime
    message: str = ""

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("ForecastSourceHealth.source_id is required")


@dataclass(frozen=True)
class ForecastBundle:
    """Source-stamped forecast payload handed across ingest boundaries."""

    source_id: str
    run_init_utc: datetime
    lead_hours: Sequence[int]
    captured_at: datetime
    raw_payload_hash: str
    authority_tier: ForecastAuthorityTier
    ensemble_members: Sequence[Any] = field(default_factory=tuple)
    raw_payload: Any | None = None

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("ForecastBundle.source_id is required")
        if not _SHA256_RE.match(self.raw_payload_hash):
            raise ValueError(
                "ForecastBundle.raw_payload_hash must be a lowercase sha256 hex digest"
            )
        if self.authority_tier not in {"GROUND_TRUTH", "FORECAST", "DERIVED"}:
            raise ValueError(
                "ForecastBundle.authority_tier must be one of "
                "GROUND_TRUTH, FORECAST, DERIVED"
            )
        if self.run_init_utc.tzinfo is None:
            raise ValueError("ForecastBundle.run_init_utc must be timezone-aware")
        if self.captured_at.tzinfo is None:
            raise ValueError("ForecastBundle.captured_at must be timezone-aware")


class ForecastIngestProtocol(Protocol):
    """Minimal protocol every forecast source adapter must satisfy."""

    source_id: str

    def fetch(
        self,
        run_init_utc: datetime,
        lead_hours: Sequence[int],
    ) -> ForecastBundle:
        """Fetch a source-stamped forecast bundle."""

    def health_check(self) -> ForecastSourceHealth:
        """Return a read-only source health result."""
