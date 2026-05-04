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

This module is the single locus for the per-candidate strategy +
DAY0_WINDOW + obs-fetch + candidate-filter dispatch decisions — six
call sites (3 in evaluator.py + 1 obs-fetch gate + 2 D-A sites in
cycle_runtime.py) all read the same flag and the same logic.

SITE 5 (critic R5 ATTACK 3 / A3-M1) — MIGRATED in §A4:
``src/engine/evaluator.py:1416`` (``is_day0_mode = ...``) drives
``EntryMethod`` selection (DAY0_OBSERVATION vs ENS_MEMBER_COUNTING) and
several downstream rejection branches. Pre-§A4 this read
``candidate.discovery_mode == "day0_capture"`` directly — under flag ON
that produced a phase/method incoherence (e.g.,
``discovery_mode=opening_hunt`` + ``market_phase=settlement_day``:
strategy dispatch routes to ``settlement_capture`` but EntryMethod
stayed on ENS_MEMBER_COUNTING). §A4 routes the line through
``is_settlement_day_dispatch(candidate)`` so flag OFF preserves legacy
behavior byte-equal AND flag ON resolves the 7th site coherently.
The dispatch helper is now the SINGLE locus for the phase-vs-mode
axis decision across all 5 callers.

KNOWN OBSERVABILITY-ONLY GAPS (critic R5 A5-M2 / A6-M3 / A7-M4):
- ``_is_settlement_day_phase`` hardcodes ``uma_resolved=False`` — UMA
  on-chain resolved truth is not wired today. POST_TRADING and RESOLVED
  collapse to the same dispatch behavior. ``task_2026-05-04_oracle_kelly_evidence_rebuild``
  §A5 ships the UMA ``SettlementResolved`` listener.
- F1 fallback (12:00 UTC of target_date) is the only endDate source
  for site 1 (monitor loop has no Gamma payload). With flag ON this
  becomes silent live authority. ``task_2026-05-04_oracle_kelly_evidence_rebuild``
  §A5 introduces ``MarketPhaseEvidence.phase_source ∈ {verified_gamma,
  fallback_f1, unknown, onchain_resolved}`` so callers can distinguish
  + degrade.
- ``market_phase=None`` collapses MISSING + PARSE_FAILED + PRE_FLAG_FLIP
  into a single state. Finding A's "missing = OK" pattern for the
  phase axis. ``task_2026-05-04_oracle_kelly_evidence_rebuild`` §A5
  separates these.

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


class PhaseAuthorityViolation(RuntimeError):
    """Raised by strict-dispatch sites when ``candidate.market_phase``
    is ``None`` under flag ON (PLAN.md §A5 + Bug review Finding F).

    The fail-soft default (``strict=False``) lets dispatch callers
    fall back to the legacy cycle-axis rule when a candidate slips
    through without a phase tag — preserving the migration's
    "no behavior change on flag flip" property. The strict variant
    is for LIVE-AUTHORITY callers (Kelly resolver, entry executor,
    settlement attribution) where silent legacy fallback would mask
    a tag-failure as a successful determination. Strict callers
    catch this exception, log the failure_reason, and reject the
    candidate — never trade against an undetermined phase.
    """


_TRUTHY_FLAG_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on"})
_FALSY_FLAG_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off"})

# PR #56 review (Copilot MEDIUM, 2026-05-04): explicit one-shot guard for
# unrecognized env-var values. Pre-fix the warn-on-unrecognized path
# fired on every call; in dispatch hot loops this turned a misspelled
# env var into log spam. Standard Python logging does NOT dedupe by
# message content (the original code comment claimed it did — incorrect).
# Module-level set tracks already-warned-about values so each typo
# generates exactly one warning per process lifetime.
_warned_unrecognized_dispatch_values: set[str] = set()


def market_phase_dispatch_enabled() -> bool:
    """Return True iff ``ZEUS_MARKET_PHASE_DISPATCH`` is enabled.

    PRE-A6 default: ``"0"`` (OFF). T6 byte-equal invariant required that
    when this was OFF every dispatch site behaved byte-equal to pre-P3.

    POST-A6 default: ``"1"`` (ON). PLAN.md §A6 + operator directive
    "做就做到位" (2026-05-04) — phase-axis dispatch becomes the live
    default; flag remains as an emergency kill-switch via env override
    (set ``ZEUS_MARKET_PHASE_DISPATCH=0`` to revert to legacy cycle-axis
    behavior). The legacy branches stay in dispatch.py until a follow-up
    cleanup PR excises them after ≥1 stable week of phase-axis live.

    Recognized values:
      truthy (case-insensitive): "1", "true", "yes", "on"
      falsy (case-insensitive):  "0", "false", "no", "off"

    Unrecognized non-empty values (e.g., a typo like ``"garbase"`` or
    ``"enabled"``) keep the post-A6 default ON and emit a one-shot
    warning per misspelling (PR #56 review). Critic R6 M3 fix
    (2026-05-04): pre-fix, unrecognized values silently flipped to OFF —
    operator typo became a kill-switch by accident. Remain-on is the
    conservative direction since the default is ON; the one-shot warning
    surfaces the typo without spamming logs in tight dispatch loops.

    Empty / whitespace falls back to the default.
    """
    raw = os.environ.get(_DISPATCH_FLAG_ENV, "").strip().lower()
    if raw == "":
        return True  # post-A6 default
    if raw in _TRUTHY_FLAG_VALUES:
        return True
    if raw in _FALSY_FLAG_VALUES:
        return False
    # Unrecognized: warn once per distinct bad value, then default ON.
    if raw not in _warned_unrecognized_dispatch_values:
        _warned_unrecognized_dispatch_values.add(raw)
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "Unrecognized %s=%r — expected one of %s (truthy) or %s "
            "(falsy). Remaining on post-A6 default (phase-axis ON). "
            "Fix the env var to silence this warning.",
            _DISPATCH_FLAG_ENV, raw,
            sorted(_TRUTHY_FLAG_VALUES), sorted(_FALSY_FLAG_VALUES),
        )
    return True


def _reset_dispatch_flag_warning_cache_for_test() -> None:
    """Clear the one-shot warning set. NOT public API; only for test
    fixtures that need to assert warning emission across multiple calls.
    """
    _warned_unrecognized_dispatch_values.clear()


def is_settlement_day_dispatch(
    candidate: "MarketCandidate", *, strict: bool = False
) -> bool:
    """Single dispatch question: at this candidate, should the daemon
    take the SETTLEMENT_DAY-class strategy path?

    Flag OFF (default, byte-equal to pre-P3): legacy
    ``candidate.discovery_mode == DAY0_CAPTURE.value``. ``strict`` is
    ignored when the flag is OFF — the legacy axis is fully resolvable
    without a phase tag.

    Flag ON: ``candidate.market_phase == MarketPhase.SETTLEMENT_DAY``.

    Phase=None handling under flag ON:
      - ``strict=False`` (default, fail-soft): falls back to legacy
        cycle-axis rule. Preserves the "flag flip changes nothing
        observable when phase tagging is incomplete" property used by
        test fixtures and off-cycle direct construction.
      - ``strict=True`` (PLAN.md §A5 Finding F floor): raises
        ``PhaseAuthorityViolation``. Live-authority callers (Kelly
        resolver, entry executor, settlement attribution) opt into this
        so a silent fallback never masks a tag-failure as a successful
        determination.
    """
    if not market_phase_dispatch_enabled():
        return _is_day0_capture_legacy(candidate)

    market_phase = getattr(candidate, "market_phase", None)
    if market_phase is None:
        if strict:
            raise PhaseAuthorityViolation(
                f"market_phase=None under flag ON for candidate "
                f"{getattr(candidate, 'condition_id', '<unknown>')}; "
                f"strict caller refuses silent legacy fallback"
            )
        # Fail-soft path: defer to legacy.
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
    target_date (verified across 13 cities — INVESTIGATION_EXTERNAL
    Q3 contributes 7 cities, CRITIC_REVIEW_R2 spot-check contributes
    6; full breakdown in
    docs/operations/task_2026-05-04_strategy_redesign_day0_endgame/
    CRITIC_REVIEW_R2.md).

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
    if result is None:
        # Critic R5 A5-L6: log when site 2 silently drops a candidate
        # under flag ON due to phase-tag parse failure. Without this,
        # operators flipping ZEUS_MARKET_PHASE_DISPATCH=1 see a
        # candidate-count drop with no audit trail tying it to Gamma
        # payload corruption.
        import logging
        logging.getLogger(__name__).warning(
            "filter_market_to_settlement_day fail-soft excluded "
            "%s/%s — phase tag could not be computed",
            getattr(city, "name", "<unknown>"),
            target_date_str,
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
