# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_strategy_redesign_day0_endgame/PLAN_v3.md §6.P3 + §6.P4 (D-B mode→phase migration + D-A two-clock unification; v3 per §0.1).
"""Phase-axis dispatch helpers.

PLAN_v3 §6.P3 migrates strategy/observation dispatch from
``DiscoveryMode.DAY0_CAPTURE`` (cycle-axis) to
``MarketPhase.SETTLEMENT_DAY`` (market-axis). PLAN_v3 §6.P4 unifies the
two D-A clocks (``cycle_runtime.py`` candidate filter on UTC ``endDate
- now`` vs DAY0_WINDOW lifecycle transition on city-local
end-of-target_date) through the same MarketPhase axis.

Both migrations are **flag-gated by ``ZEUS_MARKET_PHASE_DISPATCH``,
default OFF**: with the flag unset, dispatch is byte-equal to pre-P3
(T6 invariant in PLAN_v3 §8). With the flag set, dispatch reads
``candidate.market_phase`` (P3) and computes the position/market phase
inline (P4) instead of using the legacy clocks.

Why a single flag for both P3 and P4: the D-A and D-B drifts share one
root cause (no per-market-phase axis) and one fix (compute MarketPhase
at decision-time). Splitting the flag would let an operator activate
P3 without P4 and create a NEW two-clock split (per-candidate dispatch
on phase, candidate filter on hours_to_resolution). The single flag
guarantees the entire phase-axis migration activates as one unit.

Why a flag rather than a hard cutover:

- Critic R2 C6 + R1 C5 require that no single PR flips dispatch for
  all 51 cities at once without an evidence cohort. The flag lets P3+P4
  ship the migration scaffolding while keeping production on the
  legacy path until an explicit ON/OFF decision and supporting evidence
  bundle (per ``docs/operations/activation/UNLOCK_CRITERIA.md`` precedent).
- Once the flag is ON, ``MarketPhase`` becomes the dispatch axis. Once
  it is locked ON for ≥1 stable week with no regressions, P3.5 can
  excise the legacy branch.

This module is the single locus for the dispatch decision so the six
call sites (3 in evaluator.py + 1 obs-fetch gate + 2 D-A sites in
cycle_runtime.py) all read the same flag and the same logic.
Cycle-axis sites (cycle_runner.py:_classify_edge_source / freshness
short-circuit) are NOT migrated by P3/P4 because they operate before
per-candidate phase is available — see
``settlement_day_dispatch_for_mode`` for the legacy fallback used at
those sites.
"""
from __future__ import annotations

import os
from typing import Optional, TYPE_CHECKING

from src.engine.discovery_mode import DiscoveryMode

if TYPE_CHECKING:
    from src.engine.evaluator import MarketCandidate
    from src.strategy.market_phase import MarketPhase


_DISPATCH_FLAG_ENV = "ZEUS_MARKET_PHASE_DISPATCH"


def market_phase_dispatch_enabled() -> bool:
    """Return True iff ``ZEUS_MARKET_PHASE_DISPATCH`` is set to a truthy
    value. Default OFF; T6 byte-equal invariant requires that when this
    is OFF every dispatch site behaves byte-equal to pre-P3.
    """
    return os.environ.get(_DISPATCH_FLAG_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


def is_settlement_day_dispatch(candidate: "MarketCandidate") -> bool:
    """Single dispatch question: at this candidate, should the daemon
    take the SETTLEMENT_DAY-class strategy path?

    Flag OFF (default, byte-equal to pre-P3): legacy
    ``candidate.discovery_mode == DAY0_CAPTURE.value``.

    Flag ON: ``candidate.market_phase == MarketPhase.SETTLEMENT_DAY``.
    Falls back to legacy logic if ``candidate.market_phase`` is None
    (untagged / off-cycle / test fixture) — fail-soft so the migration
    never trades silent misclassification for a hard fault.
    """
    if not market_phase_dispatch_enabled():
        return _is_day0_capture_legacy(candidate)

    market_phase = getattr(candidate, "market_phase", None)
    if market_phase is None:
        # Untagged candidate — defer to legacy. This is the path taken
        # by test fixtures and any off-cycle direct construction; the
        # production cycle_runtime always tags at construction.
        return _is_day0_capture_legacy(candidate)

    # str-Enum equality: ``MarketPhase.SETTLEMENT_DAY == "settlement_day"``.
    return market_phase == "settlement_day"


def settlement_day_dispatch_for_mode(mode: DiscoveryMode) -> bool:
    """Mode-axis fallback for cycle-level callers (e.g.,
    ``cycle_runner._classify_edge_source``) that don't have a candidate
    in scope. Always uses the legacy ``DiscoveryMode`` axis regardless
    of the flag — these sites are explicitly NOT migrated by P3 because
    cycle-level decisions happen before per-candidate phase is known.

    Kept here for symmetry so future cleanup passes can find every
    "is this DAY0_CAPTURE-class?" site through one grep.
    """
    return mode == DiscoveryMode.DAY0_CAPTURE


def _is_day0_capture_legacy(candidate: "MarketCandidate") -> bool:
    return getattr(candidate, "discovery_mode", "") == DiscoveryMode.DAY0_CAPTURE.value


def should_fetch_settlement_day_observation(
    *,
    mode: DiscoveryMode,
    market_phase: Optional["MarketPhase"],
) -> bool:
    """P3 site 4 dispatch decision: should ``cycle_runtime`` fetch a
    Day0 observation for this market in this cycle?

    Same flag-gated semantics as ``is_settlement_day_dispatch`` but
    operates on a (mode, market_phase) tuple because the obs-fetch site
    fires BEFORE the ``MarketCandidate`` is constructed (it gates the
    ``observation`` field that goes INTO the ctor). Extracted from an
    inline ``if/else`` block in cycle_runtime per critic R4 A7-M2 so
    the contract is independently testable.

    Flag OFF (default, byte-equal to pre-P3): legacy
    ``mode == DiscoveryMode.DAY0_CAPTURE``.

    Flag ON + ``market_phase`` tagged:
    ``market_phase == MarketPhase.SETTLEMENT_DAY``.

    Flag ON + ``market_phase is None`` (Gamma parse error / off-cycle):
    fall back to legacy ``mode == DAY0_CAPTURE`` — fail-soft so a
    payload tz error never silently disables the obs fetch when the
    cycle nominally targets Day0.
    """
    if not market_phase_dispatch_enabled():
        return mode == DiscoveryMode.DAY0_CAPTURE
    if market_phase is None:
        return mode == DiscoveryMode.DAY0_CAPTURE
    return market_phase == "settlement_day"


# ---------------------------------------------------------------------- #
# P4 D-A two-clock unification (PLAN_v3 §6.P4)
#
# These helpers replace the ad-hoc clock checks at:
#   1. cycle_runtime.py candidate filter (was: hours_to_resolution <
#      params['max_hours_to_resolution'], anchored on UTC endDate-now)
#   2. cycle_runtime.py DAY0_WINDOW transition (was:
#      lead_hours_to_settlement_close <= 6.0, anchored on city-local
#      end-of-target_date)
#
# Both clocks pre-P4 disagreed by (24h - city.utc_offset). Under flag
# ON they unify on MarketPhase.SETTLEMENT_DAY = [city-local 00:00 of
# target_date, 12:00 UTC of target_date) — i.e., entry uses city-local
# anchor, exit uses Polymarket endDate (uniformly 12:00 UTC per F1).
# ---------------------------------------------------------------------- #


def _is_settlement_day_phase(
    *,
    market: Optional[dict],
    target_date_str: str,
    city_timezone: str,
    decision_time_utc,
) -> Optional[bool]:
    """Return ``True`` iff the (target_date, city_timezone,
    decision_time) triple resolves to ``MarketPhase.SETTLEMENT_DAY`` at
    this instant. Returns ``False`` for any other genuine phase
    (PRE_TRADING, PRE_SETTLEMENT_DAY, POST_TRADING, RESOLVED). Returns
    ``None`` on parse / arg failure so callers can distinguish "phase
    says no" (respect it) from "could not determine phase" (fall back
    to legacy).

    When ``market`` is provided, use its ``market_end_at`` /
    ``market_start_at`` keys (Polymarket Gamma payload-derived).
    When ``market`` is ``None`` (P4 site 1 — monitor loop has only
    ``pos.target_date`` + ``city.timezone``, no Gamma payload), fall
    back to F1: Polymarket weather endDate uniformly 12:00 UTC of
    target_date (verified across 13 cities).

    The tri-state return is critical: collapsing parse-failure to
    ``False`` would silently let a corrupt target_date row exit the
    flag-ON path with the legacy threshold; collapsing parse-failure
    to ``True`` would let it enter Day0 incorrectly. Letting the
    caller see ``None`` and pick the right fallback is the only
    correctness-preserving option.
    """
    from datetime import date

    from src.strategy.market_phase import (
        MarketPhase,
        _f1_fallback_end_utc,
        market_phase_for_decision,
        market_phase_from_market_dict,
    )

    try:
        if market is not None:
            phase = market_phase_from_market_dict(
                market=market,
                city_timezone=city_timezone,
                target_date_str=target_date_str,
                decision_time_utc=decision_time_utc,
            )
        else:
            target_local_date = date.fromisoformat(target_date_str)
            phase = market_phase_for_decision(
                target_local_date=target_local_date,
                city_timezone=city_timezone,
                decision_time_utc=decision_time_utc,
                polymarket_start_utc=None,
                polymarket_end_utc=_f1_fallback_end_utc(target_local_date),
                uma_resolved=False,
            )
        return phase == MarketPhase.SETTLEMENT_DAY
    except Exception:
        return None


def filter_market_to_settlement_day(
    *,
    market: dict,
    decision_time_utc,
) -> bool:
    """P4 site 2 dispatch decision (PLAN_v3 §6.P4): does this market dict
    pass the SETTLEMENT_DAY candidate filter?

    Flag OFF (default, byte-equal to pre-P4): caller retains its legacy
    ``hours_to_resolution`` filter — this function returns ``True``
    so the caller's existing filter is the authority.

    Flag ON: returns ``True`` iff the market is currently in
    ``MarketPhase.SETTLEMENT_DAY`` per (market_end_at, city.timezone,
    target_date, decision_time). Replaces the legacy "hours-to-Polymarket-
    endDate < 6" filter, which silently underran for west-of-UTC cities
    (LA endDate is 12:00 UTC = 04:00 local of target_date — the legacy
    filter opened the DAY0_CAPTURE window at 06:00 UTC = before LA's
    target_date even started locally).

    Fail-soft on parse failure: returns ``False`` when flag is ON and
    phase cannot be determined. The legacy filter would have included
    such a market; excluding under flag-ON is more conservative
    (missed candidate vs. wrong-phase entry) and consistent with the
    obs-fetch gate's fail-soft semantics at site 4. Genuine
    not-in-SETTLEMENT_DAY phases also return ``False`` (the desired
    filter behavior).

    Caller MUST still gate on the legacy ``hours_to_resolution`` filter
    when flag is OFF; this function does not subsume it.
    """
    if not market_phase_dispatch_enabled():
        return True

    city = market.get("city")
    if city is None:
        return False
    target_date_str = market.get("target_date")
    if not target_date_str:
        return False
    result = _is_settlement_day_phase(
        market=market,
        target_date_str=target_date_str,
        city_timezone=getattr(city, "timezone", ""),
        decision_time_utc=decision_time_utc,
    )
    # None (parse error) collapses to False — fail-soft toward
    # excluding the candidate when flag is ON.
    return result is True


def should_enter_day0_window(
    *,
    target_date_str: str,
    city_timezone: str,
    decision_time_utc,
    legacy_hours_to_settlement: Optional[float],
    legacy_threshold_hours: float = 6.0,
) -> bool:
    """P4 site 1 dispatch decision (PLAN_v3 §6.P4): should this position
    transition into ``LifecyclePhase.DAY0_WINDOW`` at ``decision_time``?

    Flag OFF (default, byte-equal to pre-P4): legacy
    ``legacy_hours_to_settlement <= legacy_threshold_hours``. Caller
    is responsible for computing ``legacy_hours_to_settlement`` via
    ``lead_hours_to_settlement_close`` so this helper is pure with
    respect to the time semantic.

    Flag ON: position transitions when its market is in
    ``MarketPhase.SETTLEMENT_DAY`` (city-local 00:00 of target_date
    onward, until 12:00 UTC of target_date). This BROADENS the
    DAY0_WINDOW from the legacy 6h to up to 24h depending on city
    timezone — the wider window matches the operator framing
    "day 0 应该交易所有当地市场 0 点前的 24 个小时" and aligns
    with PLAN_v3 §2 axis A semantics.

    Fail-soft: tag failure under flag ON falls back to the legacy
    threshold. Without this, a single corrupt ``target_date`` string
    would silently freeze a position out of the DAY0_WINDOW state and
    leave its exit logic on pre-Day0 thresholds.
    """
    if not market_phase_dispatch_enabled():
        if legacy_hours_to_settlement is None:
            return False
        return legacy_hours_to_settlement <= legacy_threshold_hours

    result = _is_settlement_day_phase(
        market=None,
        target_date_str=target_date_str,
        city_timezone=city_timezone,
        decision_time_utc=decision_time_utc,
    )
    if result is True:
        return True
    if result is False:
        # Phase cleanly says NOT SETTLEMENT_DAY — respect that. Falling
        # back to the legacy 6h threshold here would re-fire DAY0_WINDOW
        # for west-of-UTC cities AFTER Polymarket endDate (POST_TRADING),
        # which is exactly the D-A bug P4 is closing.
        return False
    # result is None — phase computation failed. Fail-soft to legacy
    # threshold so a corrupt target_date string doesn't silently freeze
    # the position out of DAY0 transitions.
    if legacy_hours_to_settlement is None:
        return False
    return legacy_hours_to_settlement <= legacy_threshold_hours
