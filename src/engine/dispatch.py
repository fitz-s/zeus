# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_strategy_redesign_day0_endgame/PLAN_v3.md §6.P3 + §6.P4 (D-B mode→phase migration + D-A two-clock unification; v3 per §0.1).
"""Current market-phase dispatch helpers."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Optional, TYPE_CHECKING
from zoneinfo import ZoneInfo

from src.engine.discovery_mode import DiscoveryMode

if TYPE_CHECKING:
    from src.engine.evaluator import MarketCandidate
    from src.strategy.market_phase import MarketPhase


class PhaseAuthorityViolation(RuntimeError):
    pass


def _venue_bool(value: object) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "closed"}:
            return True
        if normalized in {"0", "false", "no", "n", "open"}:
            return False
    return None


def _payload_explicitly_venue_closed(market: Optional[dict]) -> bool:
    if not market:
        return False
    closed = _venue_bool(market.get("closed"))
    if closed is None:
        closed = _venue_bool(market.get("market_closed"))
    accepting = _venue_bool(market.get("accepting_orders"))
    if accepting is None:
        accepting = _venue_bool(market.get("acceptingOrders"))
    return closed is True and accepting is False


def _is_target_local_day_active(
    *,
    target_date_str: str,
    city_timezone: str,
    decision_time_utc,
) -> Optional[bool]:
    try:
        target_local_date = date.fromisoformat(target_date_str)
        tz = ZoneInfo(city_timezone)
        dt = decision_time_utc
        if not isinstance(dt, datetime):
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        decision_local = dt.astimezone(tz)
        start = datetime.combine(target_local_date, time.min, tzinfo=tz)
        end = datetime.combine(target_local_date, time.max, tzinfo=tz)
    except Exception:
        return None
    return start <= decision_local <= end


def is_settlement_day_dispatch(
    candidate: "MarketCandidate", *, strict: bool = False
) -> bool:
    """Single dispatch question: at this candidate, should the daemon
    take the SETTLEMENT_DAY-class strategy path?

    The candidate phase is the only authority. Missing phase returns False for
    ordinary screening and raises for strict money-path callers.
    """
    market_phase = getattr(candidate, "market_phase", None)
    if market_phase is None:
        if strict:
            raise PhaseAuthorityViolation(
                f"market_phase=None for candidate "
                f"{getattr(candidate, 'condition_id', '<unknown>')}; "
                f"strict caller refuses absent phase authority"
            )
        return False

    # str-Enum equality: ``MarketPhase.SETTLEMENT_DAY == "settlement_day"``.
    return market_phase == "settlement_day"


def is_day0_capture_mode(mode: DiscoveryMode) -> bool:
    """Return whether a cycle was explicitly scheduled for Day0 capture."""
    return mode == DiscoveryMode.DAY0_CAPTURE


# ---------------------------------------------------------------------- #
# P4 D-A day0 dispatch unification (PLAN_v3 §6.P4, corrected 2026-06-26)
#
# These helpers replace the ad-hoc clock checks at:
#   1. cycle_runtime.py candidate filter (was: hours_to_resolution <
#      params['max_hours_to_resolution'], anchored on UTC endDate-now)
#   2. cycle_runtime.py DAY0_WINDOW transition (was:
#      lead_hours_to_settlement_close <= 6.0, anchored on city-local
#      end-of-target_date)
#
# Both clocks pre-P4 disagreed by (24h - city.utc_offset). Under the current
# live rule they unify on the city-local target day. Gamma endDate/F1 12:00Z is
# not venue-close proof.
# ---------------------------------------------------------------------- #


def _is_settlement_day_phase(
    *,
    market: Optional[dict],
    target_date_str: str,
    city_timezone: str,
    decision_time_utc,
) -> Optional[bool]:
    """Return ``True`` iff the city-local target date is active now.

    Gamma ``endDate``/the old F1 12:00Z anchor is a resolution timestamp, not
    order-entry closure. Day0/monitor dispatch must keep evaluating through the
    local target day unless venue payload explicitly says ``closed=true`` and
    ``acceptingOrders=false``.

    Parse failure remains distinct as ``None`` so callers can fail closed and
    report missing phase authority.
    """
    if _payload_explicitly_venue_closed(market):
        return False
    return _is_target_local_day_active(
        target_date_str=target_date_str,
        city_timezone=city_timezone,
        decision_time_utc=decision_time_utc,
    )


def filter_market_to_settlement_day(
    *,
    market: dict,
    decision_time_utc,
) -> bool:
    """P4 site 2 dispatch decision (PLAN_v3 §6.P4): does this market dict
    pass the SETTLEMENT_DAY candidate filter?

    Returns ``True`` iff the market's city-local target day is active
    and payload does not explicitly prove venue closure. Replaces the legacy
    "hours-to-Polymarket-endDate < 6" filter, which silently underruns for
    west-of-UTC cities and over-closes same-day markets after 12:00Z.

    Parse failure returns ``False``. Genuine
    not-in-SETTLEMENT_DAY phases also return ``False`` (the desired
    filter behavior).

    """
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
        import logging
        logging.getLogger(__name__).warning(
            "filter_market_to_settlement_day fail-soft excluded "
            "%s/%s — phase tag could not be computed",
            getattr(city, "name", "<unknown>"),
            target_date_str,
        )
    return result is True


def should_enter_day0_window(
    *,
    target_date_str: str,
    city_timezone: str,
    decision_time_utc,
) -> bool:
    """P4 site 1 dispatch decision (PLAN_v3 §6.P4): should this position
    transition into ``LifecyclePhase.DAY0_WINDOW`` at ``decision_time``?

    Position transitions when its city-local target date is active. The whole
    target day matches the operator framing
    "day 0 应该交易所有当地市场 0 点前的 24 个小时" and aligns
    with PLAN_v3 §2 axis A semantics.

    Missing or invalid phase authority fails closed.
    """
    result = _is_settlement_day_phase(
        market=None,
        target_date_str=target_date_str,
        city_timezone=city_timezone,
        decision_time_utc=decision_time_utc,
    )
    if result is True:
        return True
    if result is False:
        # Day0 window cleanly says inactive — respect that. Falling back
        # to the legacy 6h threshold here would re-open positions outside
        # their local target day.
        return False
    return False
