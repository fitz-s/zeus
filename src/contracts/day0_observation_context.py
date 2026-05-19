# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_0_V4_ADDENDUM.md PR 5 row; INV-09, INV-16
# SCAFFOLD ONLY — production logic pending. All function bodies are stubs.
# See docs/operations/task_2026-05-17_strategy_vnext_phase0/scaffolds/pr5_scaffold_report.md
# for open questions, 12-cell matrix, and DST archetype set.
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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.types import Day0TemporalContext


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
# Factory functions (SCAFFOLD — bodies are stubs)
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
    NotImplementedError
        SCAFFOLD — production logic pending.
    """
    raise NotImplementedError("SCAFFOLD: classify_bound not yet implemented (PR 5 production code pending)")


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

    Raises
    ------
    NotImplementedError
        SCAFFOLD — production logic pending.
    """
    raise NotImplementedError("SCAFFOLD: build_day0_observation_context not yet implemented (PR 5 production code pending)")
