# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: operator staleness/cycle-physics directive 2026-06-10. Single source of
#   truth for (a) the bounded source-cycle staleness horizon shared by the materialization
#   fail-closed gate AND the live-admission belt-and-suspenders gate, and (b) the model-cycle
#   PHASE classification (synoptic 00/12Z vs intermediate 06/18Z) introduced by the 4-cycle
#   download schedule (commit efff09c643). Evidence: (computed_at - source_cycle_time) over
#   forecast_posteriors ran min 9.5h / avg 18.9h / max 28.8h in healthy operation (n=1168),
#   so a 30h bound admits all healthy operation with margin while rejecting multi-day laundering.
"""Replacement-forecast cycle policy: bounded staleness horizon + cycle-phase classification.

Two structural invariants live here so neither can be re-implemented divergently (Fitz #2:
encode the invariant in shared structure, not in N parallel checks):

  1. BOUNDED STALENESS — re-materializing the SAME persisted source cycle re-stamps
     ``computed_at`` and grants a fresh readiness TTL. Unbounded, this launders an
     arbitrarily-old cycle into "current" trading inputs forever. The bound caps
     ``computed_at - source_cycle_time`` (at materialization, fail-closed) AND
     ``decision_time - source_cycle_time`` (at live admission, belt-and-suspenders) at
     ``MAX_CYCLE_AGE`` hours. Expired-but-rematerializable: re-stamping the same cycle is
     allowed ONLY while still within the bound.

  2. CYCLE PHASE — 00Z/12Z are the full synoptic cycles (complete radiosonde assimilation);
     06Z/18Z are intermediate cycles with different skill/bias characteristics. The
     walk-forward de-bias + fusion weights were trained on history that is ~99% 00Z-cycle
     (settlement-graded residual substrate: 00Z 133,837 rows vs 06/12/18Z 1,012 combined),
     so a bias correction fit on synoptic phase is misapplied to intermediate phase. We tag
     each posterior's phase in provenance and let the live gate hold intermediate-phase
     posteriors to SHADOW-ONLY by default (never weaken a gate) until a settlement-graded
     comparison licenses them.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone


UTC = timezone.utc


# H3 / operator directives 2026-06-10 + 2026-06-11 ("这个数字取决于该发布频率的tolerance
# 而不是瞎猜"): fail-closed staleness horizon, DERIVED from the measured publication
# rhythm — serve the last fetched data until the provider has had the chance to deliver
# TWO newer live-eligible cycles and we still hold nothing newer; only then is the old
# data "extremely stale" and refused.
#
#   bound = 2 x LIVE_REFRESH_INTERVAL + P50 publication lag
#         = 2 x 12h (live-eligible cycles are 00Z/12Z only, operator cycle policy)
#           + 6h   (MEASURED anchor publication lag, healthy: open-meteo bucket meta
#                   showed 06-10 06Z run completed +5.9h; AIFS open-data index 8-10h;
#                   p50 of the binding healthy leg ~= 6h — see
#                   docs/evidence/anchor_channels/ + rule1_audits/2026-06-10)
#         = 30h
#
# Cross-checks: empirical healthy cycle age over forecast_posteriors ran min 9.5h /
# avg 18.9h / max 28.8h (n=1168) — all admitted; the 2026-06-10 single-cycle provider
# skip (12Z never published) kept the 00Z row served at 26.8h — correctly within bound;
# a SECOND consecutive miss crosses 30h and fails closed. The availability poll
# (replacement_cycle_availability) eliminated our own fetch delay (publication + <=15min),
# so publication lag is the only stochastic term left in the derivation.
# tests/data/test_cycle_staleness_derivation.py pins the formula to these inputs.
LIVE_CYCLE_REFRESH_INTERVAL_HOURS = 12.0  # 00Z/12Z live-eligible cadence (operator policy)
MEASURED_P50_PUBLICATION_LAG_HOURS = 6.0  # basis=MEASURED 2026-06-11 (see derivation above)
REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS_DEFAULT = (
    2.0 * LIVE_CYCLE_REFRESH_INTERVAL_HOURS + MEASURED_P50_PUBLICATION_LAG_HOURS
)
_MAX_AGE_ENV = "ZEUS_REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS"

# Cycle-phase labels (provenance_json.cycle_phase). Synoptic = the full 00Z/12Z assimilation;
# intermediate = the 06Z/18Z partial-assimilation cycles.
CYCLE_PHASE_SYNOPTIC = "synoptic"
CYCLE_PHASE_INTERMEDIATE = "intermediate"
_SYNOPTIC_CYCLE_HOURS = frozenset({0, 12})
_INTERMEDIATE_CYCLE_HOURS = frozenset({6, 18})


def replacement_source_cycle_max_age_hours() -> float:
    """The active staleness horizon in hours (env-overridable, fail-closed).

    A non-positive or unparseable override is IGNORED — it would disable the gate, and the
    gate must never be silently disabled (iron rule: never weaken a gate).
    """
    raw = os.environ.get(_MAX_AGE_ENV)
    if raw is None or not raw.strip():
        return REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS_DEFAULT
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS_DEFAULT
    return value if value > 0.0 else REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS_DEFAULT


def cycle_age_hours(reference_time: datetime, source_cycle_time: datetime) -> float:
    """``(reference_time - source_cycle_time)`` in hours, both coerced to UTC.

    ``reference_time`` is ``computed_at`` at materialization and ``decision_time`` at live
    admission — the two moments at which a stale cycle would be laundered into "current".
    """
    ref = reference_time.astimezone(UTC)
    cycle = source_cycle_time.astimezone(UTC)
    return (ref - cycle).total_seconds() / 3600.0


def cycle_age_exceeds_bound(
    reference_time: datetime,
    source_cycle_time: datetime,
    *,
    max_age_hours: float | None = None,
) -> bool:
    """True iff the source cycle is older than the staleness bound relative to ``reference_time``."""
    bound = replacement_source_cycle_max_age_hours() if max_age_hours is None else float(max_age_hours)
    return cycle_age_hours(reference_time, source_cycle_time) > bound


def classify_cycle_phase(source_cycle_time: datetime) -> str:
    """Classify a model cycle as synoptic (00/12Z) or intermediate (06/18Z) by its UTC hour.

    Any hour that is not exactly a 6-hourly cycle hour (defensive: clock skew, sub-hour
    timestamps) is bucketed by nearest 6h cycle; off-cadence hours fall through to
    intermediate (the MORE conservative label — it cannot accidentally grant a non-00/12Z
    cycle the synoptic free pass).
    """
    hour = source_cycle_time.astimezone(UTC).hour
    if hour in _SYNOPTIC_CYCLE_HOURS:
        return CYCLE_PHASE_SYNOPTIC
    if hour in _INTERMEDIATE_CYCLE_HOURS:
        return CYCLE_PHASE_INTERMEDIATE
    # Off-cadence hour: snap to the nearest lower 6h cycle and reclassify. Conservative
    # fallthrough to intermediate for anything that is not cleanly 00/12Z.
    snapped = (hour // 6) * 6
    return CYCLE_PHASE_SYNOPTIC if snapped in _SYNOPTIC_CYCLE_HOURS else CYCLE_PHASE_INTERMEDIATE
