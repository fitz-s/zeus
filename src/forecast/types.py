# Created: 2026-06-14
# Last audited: 2026-06-21
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Forecast center mu*" section, src/forecast/types.py — verbatim dataclass
#   fields). Foundation forecast data contracts for the q-kernel rebuild Stage 1.
#   + Option C raw-precision representativeness center warming (consult
#   REQ-20260621-033315; forecast-gap-is-data-precision): RawModelMember carries
#   ``representativeness_m2_native`` (grid sigma_repr², native unit²) threaded into
#   the RAW diagonal precision denominator that produces the served center.
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

    RAW SECOND-MOMENT PRECISION (2026-06-18 RAW diagonal fusion, single-serving-rule
    flow §1-§2): ``walk_forward_raw_m2_native`` is the strictly-prior,
    date-aligned ``Ê[(x_raw − Y)²]`` for THIS model at the family's (city, metric,
    lead) — the RAW second moment of the residual (forecast minus settlement),
    SQUARED then averaged over settlements with target_date < decision date. It is
    the precision BASIS ``center.walk_forward_model_weights`` reads to form
    ``w_m ∝ 1/max(Ê[(x_m−Y)²], SIGMA_FLOOR²)``. Under the RAW law this is the
    deployable basis (it INCLUDES the bias² — NOT the demeaned variance ``np.var``
    that ``shrink_cov``/``diag_cov`` estimate and that discards bias²).
    ``walk_forward_n`` is the count of walk-forward residuals (drives the
    shrink-to-equal-at-low-n rule). Both default to None/0 — absent history ⇒ the
    weight collapses to equal 1/n (the conservative shrink-to-equal posture), never
    EB, never demeaned var.
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
    # RAW diagonal precision basis (single-serving-rule flow §1-§2). The raw
    # second moment Ê[(x−Y)²] (bias² INCLUDED) and its walk-forward count. None/0 ⇒
    # no precision signal ⇒ equal 1/n for this member.
    walk_forward_raw_m2_native: float | None = None
    walk_forward_n: int = 0
    # Option C (2026-06-21): grid-representativeness variance sigma_repr² for THIS
    # model's native cell at the family's (city, model), in the SAME native unit² as
    # ``walk_forward_raw_m2_native`` (the producer converts the loader's degC² by the
    # (9/5)² serving-unit scale for F). It is ADDED to the floored residual second
    # moment in the precision denominator (Form A: max(m2, floor) + repr) so a
    # coarse/distant cell is down-weighted in the served center. 0.0 ⇒ no geometry
    # penalty (byte-identical) — the fail-soft default for a city/model absent from
    # the grid-representativeness table. Enters the MEAN weights ONLY, never the
    # predictive σ / Kelly width (no double-count).
    representativeness_m2_native: float = 0.0


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
