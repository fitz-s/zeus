# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: operator staleness/cycle-physics directive 2026-06-10. Single source of
#   truth for (a) the bounded source-cycle staleness horizon shared by the materialization
#   fail-closed gate AND the live-admission belt-and-suspenders gate, and (b) the model-cycle
#   PHASE classification used as provenance for the 4-cycle download schedule. Evidence:
#   (computed_at - source_cycle_time) over
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

  2. CYCLE PHASE — operator policy has promoted all four standard UTC cycles
     (00Z/06Z/12Z/18Z) to live-eligible replacement cycles. Phase remains provenance only;
     it must not downgrade 06Z/18Z rows or route them into an experiment-only state.
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
#         = 2 x 12h (replacement live refresh cadence)
#           + 6h   (MEASURED anchor publication lag, healthy: open-meteo bucket meta
#                   showed 06-10 06Z run completed +5.9h; see
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
LIVE_CYCLE_REFRESH_INTERVAL_HOURS = 12.0
MEASURED_P50_PUBLICATION_LAG_HOURS = 6.0  # basis=MEASURED 2026-06-11 (see derivation above)
REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS_DEFAULT = (
    2.0 * LIVE_CYCLE_REFRESH_INTERVAL_HOURS + MEASURED_P50_PUBLICATION_LAG_HOURS
)
_MAX_AGE_ENV = "ZEUS_REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS"

# Cycle-phase labels (provenance_json.cycle_phase). All standard 00Z/06Z/12Z/18Z
# cycles are live-eligible under current operator policy.
CYCLE_PHASE_SYNOPTIC = "synoptic"
CYCLE_PHASE_INTERMEDIATE = "experiment"
_SYNOPTIC_CYCLE_HOURS = frozenset({0, 6, 12, 18})
_INTERMEDIATE_CYCLE_HOURS = frozenset()


# ---------------------------------------------------------------------------
# TRADEABLE-GRADE COVERAGE PREDICATE — SINGLE AUTHORITY (2026-06-12).
#
# Created: 2026-06-12
# Authority basis: /tmp/qlcb_coverage_fix_report.md. When the no-fusion path began
#   carrying a promoted legacy q_lcb instead of
#   NULL, the three mask-and-starve antibody sites that proxied "tradeable-grade coverage" as
#   `q_lcb_json IS NOT NULL` (live_materialization_queue / seed_discovery / current_target_plan)
#   would have WRONGLY counted a soft-anchor row as covered — re-introducing the exact mask-and-
#   starve disease they were built to prevent (an untradeable, no-current-capture row marking its
#   scope "done forever" and blocking its own fusion repair). The proxy was only ever valid because
#   NULL ⟺ non-fused; promoting the bound broke that biconditional.
#
# THE REAL PREDICATE: tradeable-grade coverage = a posterior whose q_lcb is the CERTIFIED fused-
#   center bootstrap bound. That is keyed by provenance_json.q_lcb_basis EXACTLY equal to the
#   bootstrap marker — the SAME predicate the live calibration-credential reader pins
#   (event_reactor_adapter._FUSED_BOOTSTRAP_QLCB_BASIS). Defining it ONCE here (the module both the
#   materializer and the readers already import, no cycle) makes all four sites share one definition.
TRADEABLE_GRADE_QLCB_BASIS = "fused_center_bootstrap_p05"


def tradeable_grade_coverage_sql(*, posterior_columns, alias: str = "") -> str:
    """SQL fragment selecting ONLY tradeable-grade (certified-bootstrap-bounded) posteriors.

    Replaces the broken ``AND <alias>q_lcb_json IS NOT NULL`` proxy at the mask-and-starve
    antibody sites. A soft-anchor Wilson-bounded row (non-NULL q_lcb but basis != bootstrap) is
    NOT tradeable-grade, so it does NOT count as coverage and correctly re-seeds for fusion repair.

    Schema-conditional (same convention as the existing clauses): when forecast_posteriors lacks
    ``provenance_json`` the fragment is empty (no narrowing) rather than erroring. ``alias`` is the
    table alias with a trailing dot already applied by the caller's existing convention (e.g. "p.").
    """
    cols = set(posterior_columns)
    fragments: list[str] = []
    if "q_lcb_json" in cols:
        fragments.append(f"AND {alias}q_lcb_json IS NOT NULL")
    if "q_ucb_json" in cols:
        fragments.append(f"AND {alias}q_ucb_json IS NOT NULL")
    if "provenance_json" not in cols:
        return "\n              ".join(fragments)
    fragments.append(
        f"AND json_extract({alias}provenance_json, '$.q_lcb_basis') = "
        f"'{TRADEABLE_GRADE_QLCB_BASIS}'"
    )
    return "\n              ".join(fragments)


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


def replacement_readiness_expires_at(source_cycle_time: datetime) -> datetime:
    """THE single readiness-expiry derivation (operator directive 2026-06-11 RULE-1 incident).

    Readiness previously expired at ``computed_at + 3h`` (a GUESS, stamped identically at
    TWO sites — materializer + request builder), while the staleness law above says the
    cycle's data is lawful for ``max_age_hours`` after the CYCLE time. Two freshness clocks
    ⇒ the 3h clock re-killed data the 30h law declared lawful: on 2026-06-11 the 06Z rows'
    readiness died at ~06:31Z while the cycle was only ~26h old.

    ONE clock now: readiness expires exactly when the cycle's staleness bound expires.
    The H3 expires_at gate and the cycle-age gate in the bundle reader thereby verify the
    SAME bound from two directions (belt-and-suspenders on one number, never two numbers).
    tests/data/test_cycle_staleness_derivation.py pins both stamp sites to this function.
    """
    from datetime import timedelta  # noqa: PLC0415

    cycle = source_cycle_time if source_cycle_time.tzinfo else source_cycle_time.replace(tzinfo=UTC)
    return cycle.astimezone(UTC) + timedelta(hours=replacement_source_cycle_max_age_hours())


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
    """Classify a model cycle by UTC hour.

    Any hour that is not exactly a 6-hourly cycle hour (defensive: clock skew, sub-hour
    timestamps) is bucketed by nearest 6h cycle. The four standard cycles all return
    ``synoptic`` so 06Z/18Z cannot be downgraded by provenance classification.
    """
    hour = source_cycle_time.astimezone(UTC).hour
    if hour in _SYNOPTIC_CYCLE_HOURS:
        return CYCLE_PHASE_SYNOPTIC
    if hour in _INTERMEDIATE_CYCLE_HOURS:
        return CYCLE_PHASE_INTERMEDIATE
    # Off-cadence hour: snap to the nearest lower 6h cycle and reclassify.
    snapped = (hour // 6) * 6
    return CYCLE_PHASE_SYNOPTIC if snapped in _SYNOPTIC_CYCLE_HOURS else CYCLE_PHASE_INTERMEDIATE
