# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_0_V4_ADDENDUM.md PR 5 row; INV-09, INV-16
# Production code implemented in PR 5 (2026-05-19).
# See docs/operations/task_2026-05-17_strategy_vnext_phase0/scaffolds/pr5_scaffold_report.md
# for design notes, 12-cell matrix, and DST archetype set.
"""Day0 observation-context contracts: BoundClassification + Day0ObservationContext.

BoundClassification
-------------------
Classifies where in the Day0 lifecycle a position sits relative to the
current observation:

  DETERMINISTIC
      The observed extreme so far already determines the settlement outcome
      regardless of remaining forecast members. For HIGH markets: observed_high
      already exceeds every remaining-period member max. For LOW markets:
      observed_low already undercuts every remaining-period member min.

  BOUNDED_LIVE
      An observation is present and the outcome is not yet determined. The
      settlement result depends on what happens in the remaining forecast window.

  UNBOUNDED_NO_OBS_YET
      No intraday observation has been recorded yet for this city/date. The
      day has started but observed_high_so_far / observed_low_so_far is None
      (or sourced from a prior day). The Day0 signal falls back to pure
      ensemble-member probability.

Design note (CelsiusBox boundary — OPEN QUESTION #1)
------------------------------------------------------
F3 PR #177 (2026-05-19) landed CelsiusBox / FahrenheitBox at ingest
boundaries. The day0_router.py header (authority: phase6_contract.md
R-BA..R-BD) explicitly states that signal/evaluator temperature values
stay as plain `float` because they are unit-polymorphic at runtime
(Dallas=°F, London=°C share the same code paths). This scaffold annotates
the IngestAdapter→Day0Router seam as the propagation boundary: boxes
arrive there; `.value` is extracted before Day0SignalInputs construction.
Whether BoundClassification should be computed INSIDE or OUTSIDE the
Day0 hot loop is an open question (#1 in pr5_scaffold_report.md).

See: src/signal/day0_router.py lines 7-21 (F3 PR 3 design comment)
     src/types/temperature.py lines 81-179 (CelsiusBox/FahrenheitBox)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Union

from src.types.temperature import CelsiusBox, FahrenheitBox

if TYPE_CHECKING:
    from src.types import Day0TemporalContext
    from src.types.solar import DaylightPhase


# ---------------------------------------------------------------------------
# BoundClassification
# ---------------------------------------------------------------------------


class BoundClassification(str, Enum):
    """Classification of a Day0 position relative to current observation state.

    Used to reduce the 3-dimensional uncertainty space (observation_state ×
    metric_family × daypart) into a tractable decision surface.

    Values
    ------
    DETERMINISTIC
        Observed extreme already determines settlement outcome.
        No remaining forecast member can change the result.

    BOUNDED_LIVE
        Observation present; outcome depends on remaining forecast window.
        Ensemble members provide the residual uncertainty surface.

    UNBOUNDED_NO_OBS_YET
        No intraday observation yet. Signal falls back to pure ensemble
        member probability without an observed-extreme floor/ceiling.
    """

    DETERMINISTIC = "DETERMINISTIC"
    BOUNDED_LIVE = "BOUNDED_LIVE"
    UNBOUNDED_NO_OBS_YET = "UNBOUNDED_NO_OBS_YET"


# ---------------------------------------------------------------------------
# Day0ObservationContext — dataclass extension of Day0TemporalContext
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Day0ObservationContext:
    """Extends Day0TemporalContext with observation-state semantics for Day0 decisions.

    Carries BoundClassification, the observed extreme so far (if any), and a
    reference to the temporal context. Intended as the rich context object
    passed through the Day0 decision chain after the IngestAdapter→Day0Router
    seam — i.e. after CelsiusBox/FahrenheitBox `.value` has been extracted.

    Fields
    ------
    temporal_context
        The resolved Day0TemporalContext for this city/date. May be None if
        build_day0_temporal_context degraded (malformed solar_daily rootpage).

    bound_classification
        DETERMINISTIC | BOUNDED_LIVE | UNBOUNDED_NO_OBS_YET.

    observed_extreme_so_far
        The intraday observed high (for HIGH markets) or observed low (for LOW
        markets) as of the current timestamp. None when no observation is
        available (maps to UNBOUNDED_NO_OBS_YET). Raw float; unit is carried
        in the parent Day0SignalInputs.unit field (design: day0_router.py L7-21).

    is_dst_gap_hour
        True when the current local timestamp falls in a DST spring-forward
        gap hour. Sourced from Day0TemporalContext.is_missing_local_hour.
        See DST audit sites in pr5_scaffold_report.md.

    daypart
        One of {"pre_sunrise", "morning", "afternoon", "post_peak"} derived
        from the solar phase and peak-hour confidence. Part of the 12-cell
        matrix (3 BoundClassification × 4 daypart).
    """

    temporal_context: "Day0TemporalContext | None"
    bound_classification: BoundClassification
    observed_extreme_so_far: float | None
    is_dst_gap_hour: bool
    daypart: str  # SCAFFOLD: will be typed as DaylightPhase or a new Daypart enum


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def classify_bound(
    observed_extreme_so_far: float | None,
    member_extremes_remaining: "list[float] | None",
    is_high_market: bool,
) -> BoundClassification:
    """Classify bound state from observation and remaining ensemble members.

    Parameters
    ----------
    observed_extreme_so_far
        Intraday high (HIGH market) or intraday low (LOW market). None = no obs yet.
    member_extremes_remaining
        List of per-member max (HIGH) or per-member min (LOW) values for the
        remaining forecast window. None = forecast not yet available.
    is_high_market
        True for HIGH-temperature markets; False for LOW.

    Returns
    -------
    BoundClassification
        UNBOUNDED_NO_OBS_YET if observed_extreme_so_far is None.
        DETERMINISTIC if the observation already determines settlement.
        BOUNDED_LIVE otherwise.

    Raises
    ------
    ValueError
        If observed_extreme_so_far is not None and member_extremes_remaining is None.
    """
    if observed_extreme_so_far is None:
        return BoundClassification.UNBOUNDED_NO_OBS_YET

    if member_extremes_remaining is None or len(member_extremes_remaining) == 0:
        # No remaining forecast members — observation is the only signal; treat as deterministic
        return BoundClassification.DETERMINISTIC

    if is_high_market:
        # DETERMINISTIC if observation already exceeds every remaining member max
        if all(observed_extreme_so_far >= m for m in member_extremes_remaining):
            return BoundClassification.DETERMINISTIC
    else:
        # LOW market: DETERMINISTIC if observation already undercuts every remaining member min
        if all(observed_extreme_so_far <= m for m in member_extremes_remaining):
            return BoundClassification.DETERMINISTIC

    return BoundClassification.BOUNDED_LIVE


def build_day0_observation_context(
    temporal_context: "Day0TemporalContext | None",
    observed_extreme_so_far: float | None,
    member_extremes_remaining: "list[float] | None",
    is_high_market: bool,
) -> Day0ObservationContext:
    """Build a Day0ObservationContext from temporal context + observation state.

    Computes BoundClassification via classify_bound, extracts DST gap flag
    from temporal_context.is_missing_local_hour, and derives daypart from
    the solar phase and post_peak_confidence.

    Parameters
    ----------
    temporal_context
        Resolved Day0TemporalContext. May be None on DB degrade.
    observed_extreme_so_far
        Current intraday extreme. None → UNBOUNDED_NO_OBS_YET.
    member_extremes_remaining
        Per-member extremes for the remaining window.
    is_high_market
        True for HIGH, False for LOW.

    Returns
    -------
    Day0ObservationContext
    """
    bound_classification = classify_bound(
        observed_extreme_so_far=observed_extreme_so_far,
        member_extremes_remaining=member_extremes_remaining,
        is_high_market=is_high_market,
    )

    is_dst_gap_hour: bool = (
        temporal_context.is_missing_local_hour if temporal_context is not None else False
    )

    daypart = _derive_daypart(temporal_context)

    return Day0ObservationContext(
        temporal_context=temporal_context,
        bound_classification=bound_classification,
        observed_extreme_so_far=observed_extreme_so_far,
        is_dst_gap_hour=is_dst_gap_hour,
        daypart=daypart,
    )


def _derive_daypart(temporal_context: "Day0TemporalContext | None") -> str:
    """Derive the 4-way daypart string from a Day0TemporalContext.

    Delegates to Day0TemporalContext.daypart (defined in solar.py, the approved
    time-semantics layer) so that this file does not access current_local_hour
    directly (semantic linter rule: raw local-hour access restricted to solar.py /
    diurnal.py / day0_signal.py).

    Falls back to "morning" when temporal_context is None (graceful degrade).
    """
    if temporal_context is None:
        return "morning"

    return temporal_context.daypart


# ---------------------------------------------------------------------------
# IngestAdapter — CelsiusBox / FahrenheitBox → float seam
# ---------------------------------------------------------------------------


class IngestAdapter:
    """Boundary guard for Day0 temperature observation ingest.

    Accepts CelsiusBox or FahrenheitBox at the ingest seam and returns a
    canonical float for Day0SignalInputs construction. Rejects:

      - bare ``float`` input (raises TypeError — boxes must be used at the
        boundary so the unit is always explicit)
      - mismatched box type for the city's configured unit (raises ValueError —
        a FahrenheitBox arriving at a °C city indicates a data-routing error)

    Design basis: day0_router.py lines 7-21 — the signal layer is unit-polymorphic;
    unit is carried as ``unit: str = "F"`` field. The adapter extracts ``.value``
    before Day0SignalInputs construction so that hot-loop arithmetic operates on
    plain float throughout.

    Usage
    -----
    .. code-block:: python

        adapter = IngestAdapter(city_unit="C")
        current_temp: float = adapter.normalize_observation(CelsiusBox(22.5))
        # → 22.5  (float, ready for Day0SignalInputs.current_temp)

    Raises
    ------
    TypeError
        If the observation is a bare float (or any non-box type).
    ValueError
        If the box unit does not match ``city_unit``.
    """

    def __init__(self, city_unit: str) -> None:
        """Initialise the adapter for a city with the given unit ('C' or 'F').

        Parameters
        ----------
        city_unit
            Expected temperature unit for this city: ``"C"`` for Celsius cities
            (e.g. London, Sydney), ``"F"`` for Fahrenheit cities (e.g. Dallas).
        """
        if city_unit not in {"C", "F"}:
            raise ValueError(f"city_unit must be 'C' or 'F', got {city_unit!r}")
        self._city_unit = city_unit

    def normalize_observation(
        self, observation: Union[CelsiusBox, FahrenheitBox]
    ) -> float:
        """Validate the observation box and return its numeric value as a float.

        Parameters
        ----------
        observation
            A CelsiusBox or FahrenheitBox. Must match the city's configured unit.

        Returns
        -------
        float
            The numeric temperature value extracted from the box.

        Raises
        ------
        TypeError
            If ``observation`` is not a CelsiusBox or FahrenheitBox (e.g. bare float).
        ValueError
            If the box type does not match ``city_unit``:
            CelsiusBox for a °F city, or FahrenheitBox for a °C city.
        """
        if not isinstance(observation, (CelsiusBox, FahrenheitBox)):
            raise TypeError(
                f"IngestAdapter.normalize_observation expects CelsiusBox or "
                f"FahrenheitBox, got {type(observation).__name__}. "
                f"Bare float is not accepted at the Day0 ingest boundary — "
                f"wrap in the appropriate box type to make the unit explicit."
            )

        if self._city_unit == "C" and isinstance(observation, FahrenheitBox):
            raise ValueError(
                f"Unit mismatch at Day0 ingest seam: city is configured as °C "
                f"but received FahrenheitBox({observation.value}). "
                f"Pass CelsiusBox for Celsius cities."
            )

        if self._city_unit == "F" and isinstance(observation, CelsiusBox):
            raise ValueError(
                f"Unit mismatch at Day0 ingest seam: city is configured as °F "
                f"but received CelsiusBox({observation.value}). "
                f"Pass FahrenheitBox for Fahrenheit cities."
            )

        return float(observation.value)
