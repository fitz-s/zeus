# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Forecast center mu*" section, src/forecast/types.py — verbatim dataclass
#   fields). Foundation forecast data contracts for the q-kernel rebuild Stage 1.
"""Forecast foundation types: ForecastCase, RawModelMember, FreshModelSet.

These are the frozen-dataclass contracts that feed the new forecast spine
(``EventResolution -> OutcomeSpace -> FreshModelSet -> DebiasAuthority -> ...``).
They carry the EXACT fields the build spec lists, including the settlement
provenance (``station_id``, ``settlement_source_type``) and full source-cycle
identity (``source_cycle_time_utc``, ``available_at_utc``, ``raw_forecast_artifact_id``)
needed so a live candidate receipt can reconstruct the forecast center from
source inputs.

``ForecastCase.resolution`` is the versioned ``EventResolution`` (the one
settlement identity), so the rounding rule and station id thread through the
forecast layer the same way they thread through the q layer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import numpy as np

from src.probability.event_resolution import EventResolution


@dataclass(frozen=True)
class ForecastCase:
    """The full identity of one forecast target family.

    Fields are verbatim from consult_build_spec.md "Forecast center mu*".
    """

    city: str
    city_id: str
    station_id: str
    settlement_source_type: str
    target_local_date: date
    metric: Literal["high", "low"]
    issue_time_utc: datetime
    lead_hours: float
    season: str
    regime_key: str
    unit: Literal["C", "F"]
    resolution: EventResolution
    family_id: str
    source_cycle_time_utc: datetime


@dataclass(frozen=True)
class RawModelMember:
    """One raw model member forecast before any de-bias or unit normalization.

    Carries full source provenance (run id, cycle time, availability time,
    station mapping, raw artifact id, data version) so a member can be audited
    against the settlement station and product set in DebiasAuthority.
    """

    model_id: str
    product_id: str
    source_run_id: str
    source_cycle_time_utc: datetime
    available_at_utc: datetime
    value_native: float
    station_mapping_id: str
    raw_forecast_artifact_id: str
    data_version: str


@dataclass(frozen=True)
class FreshModelSet:
    """The fresh member set for one ForecastCase, with cached spread bounds.

    ``member_values_native`` is the vector of member values in the settlement
    unit; ``min_native`` / ``max_native`` are the consensus envelope bounds the
    center invariant (INV-C1) clamps mu* into. ``model_set_hash`` identifies the
    exact member set a debias artifact must product-match against.
    """

    case: ForecastCase
    members: tuple[RawModelMember, ...]
    member_values_native: np.ndarray
    min_native: float
    max_native: float
    model_set_hash: str
