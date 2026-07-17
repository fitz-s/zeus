# Created: 2026-07-17
# Last reused/audited: 2026-07-17
# Authority basis: operator go 2026-07-17 ("除了需要累积的内容都执行 使用数学和统计就能
#   证明一切") + docs/authority/replacement_final_form_2026_06_09.md §4a (staleness degrade
#   ladder). The GREEN/AMBER/RED age boundaries below are DERIVED from settled history, not
#   guessed: see docs/evidence/upstream_physical_2026_07_17/staleness_ladder_derivation.md.
"""Posterior-staleness DEGRADE LADDER classification (pure, side-effect-free).

Replaces the binary fresh / fail-closed regime with a GRADED band keyed on the
posterior AGE served at a decision:

    age = decision_time − source_cycle_time    (of the served posterior's carrier cycle)

  GREEN   (age ≤ 18h): full trading, unchanged. The posterior is plausibly the
          current freshly-served cycle — MEASURED fresh serving age is p50 7.19h /
          p99 15.75h, so ≤18h covers the fresh-serving tail with margin.
  AMBER   (18h < age ≤ 24h): entry continues but the predictive sigma is inflated
          by a settlement-FITTED age-band variance (src/forecast/posterior_age_inflation.py).
          MEASURED: the aged-vs-fresh center-error variance increment is +0.36 degC²
          (high, p=6e-8) / +0.24 degC² (low) — spread-dominated, so a symmetric
          sigma widening honestly prices it.
  RED     (age > 24h, OR a newer live-eligible cycle is detected-but-not-yet-active):
          NO new entries for the family; resting makers cancel; held-position
          monitoring/exit lanes stay FULLY ACTIVE. MEASURED: past 24h the systematic
          center BIAS (|drift| ≥0.47C) becomes a large fraction of total error and is
          UNCORRECTABLE by variance inflation, and only ~6h remain to the EXPIRED wall.
          This is the operator's per-city ENTRY isolation ("对应舱位对应城市隔离,而不是
          硬着头皮用过期概率") — isolation of ENTRY, never of monitoring.
  EXPIRED (age ≥ 30h): existing fail-closed law (replacement_source_cycle_max_age_hours),
          UNCHANGED. The ladder never weakens this gate.

UNKNOWN: an unparseable / missing source_cycle_time yields UNKNOWN, and the caller
falls back to its EXISTING binary law (current behavior) — the ladder must never turn
a classification failure into a NEW block (fail-open to today's behavior).

The EXPIRED horizon is read from replacement_forecast_cycle_policy so the ladder and
the fail-closed materialization/admission gate can never drift on that one number.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from src.data.replacement_forecast_cycle_policy import (
    cycle_age_hours,
    replacement_source_cycle_max_age_hours,
)


# DERIVED boundaries (see module docstring + evidence file). Held as module constants,
# NOT env knobs — the operator law fixes the ladder shape; only the fitted inflation
# artifact is a runtime input (minimal machinery: no new config surface).
GREEN_MAX_AGE_HOURS = 18.0
AMBER_MAX_AGE_HOURS = 24.0


class StalenessBand(str, Enum):
    """Ladder band for a served posterior's age. ``str`` mixin => JSON/log friendly."""

    GREEN = "GREEN"
    AMBER = "AMBER"
    RED = "RED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LadderClassification:
    """Band + the age (hours) it was computed from. ``age_hours`` is None for UNKNOWN."""

    band: StalenessBand
    age_hours: float | None
    newer_cycle_detected: bool = False

    @property
    def blocks_entry(self) -> bool:
        """True iff NEW entries must be refused for the family (RED or EXPIRED)."""
        return self.band in (StalenessBand.RED, StalenessBand.EXPIRED)

    @property
    def inflates_sigma(self) -> bool:
        """True iff the AMBER settlement-fitted sigma inflation applies."""
        return self.band is StalenessBand.AMBER


def classify_posterior_staleness(
    decision_time: datetime,
    source_cycle_time: datetime | None,
    *,
    newer_cycle_detected: bool = False,
    green_max_age_hours: float = GREEN_MAX_AGE_HOURS,
    amber_max_age_hours: float = AMBER_MAX_AGE_HOURS,
    expired_max_age_hours: float | None = None,
) -> LadderClassification:
    """Classify a served posterior's staleness band.

    ``newer_cycle_detected`` — a newer live-eligible cycle exists for the family but its
    posterior is not yet the served carrier (the caller resolves this from the existing
    raw-input high-water-mark / availability signal; the grace period is already baked
    into that signal, which only counts inputs available by ``decision_time``). When
    True the family is RED regardless of age (unless already EXPIRED): trading on the
    older carrier while a fresher one is materializing is exactly the adverse-selection
    window the isolation ask targets.

    ``source_cycle_time`` None / unparseable => UNKNOWN (caller keeps its binary law).
    """
    if source_cycle_time is None:
        return LadderClassification(StalenessBand.UNKNOWN, None, newer_cycle_detected)
    try:
        age = cycle_age_hours(decision_time, source_cycle_time)
    except (AttributeError, TypeError, ValueError):
        return LadderClassification(StalenessBand.UNKNOWN, None, newer_cycle_detected)
    expired_bound = (
        replacement_source_cycle_max_age_hours()
        if expired_max_age_hours is None
        else float(expired_max_age_hours)
    )
    # A negative age (future-dated cycle) is not a staleness question — leave it to the
    # caller's existing future-posterior gate; classify as UNKNOWN so no ladder action fires.
    if age < 0.0:
        return LadderClassification(StalenessBand.UNKNOWN, age, newer_cycle_detected)
    if age >= expired_bound:
        return LadderClassification(StalenessBand.EXPIRED, age, newer_cycle_detected)
    if newer_cycle_detected or age > amber_max_age_hours:
        return LadderClassification(StalenessBand.RED, age, newer_cycle_detected)
    if age > green_max_age_hours:
        return LadderClassification(StalenessBand.AMBER, age, newer_cycle_detected)
    return LadderClassification(StalenessBand.GREEN, age, newer_cycle_detected)
