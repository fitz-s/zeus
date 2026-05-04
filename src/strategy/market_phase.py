# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_strategy_redesign_day0_endgame/PLAN_v2.md §2 + §6.P2 (v3 per §0.1).
"""``MarketPhase`` axis — market-time lifecycle of a Polymarket weather market.

Per PLAN_v2 §2 (axis A), ``MarketPhase`` is computed from
``(target_local_date, city.timezone, decision_time_utc,
polymarket_start_utc, polymarket_end_utc, uma_resolved)``. It is the
same for every position on the same market — orthogonal to the
per-position ``LifecyclePhase`` axis B at
``src/state/lifecycle_manager.py``.

Critical invariant (per critic R1 C5): phase MUST be computed from the
cycle's frozen ``decision_time_utc``, NEVER from ``datetime.now(UTC)``
at point-of-use. A 50-candidate cycle that straddles a boundary must
see the SAME phase for every candidate of the same market — otherwise
midnight-straddle pricing would split.

Boundary anchors (locked from PLAN_v2 §1.E1+§2):

- ``PRE_TRADING → PRE_SETTLEMENT_DAY`` at ``polymarket_start_utc``
  (Polymarket ``startDate``, T-2 days before target)
- ``PRE_SETTLEMENT_DAY → SETTLEMENT_DAY`` at city-local
  end-of-target_date − 24h (UTC instant)
- ``SETTLEMENT_DAY → POST_TRADING`` at ``polymarket_end_utc``
  (uniformly 12:00 UTC of ``target_date`` per F1 — verified across 13
  cities via Gamma API; see INVESTIGATION_EXTERNAL Q1)
- ``POST_TRADING → RESOLVED`` at UMA proposePrice settlement (variable;
  caller passes ``uma_resolved=True`` once observed on-chain)

All boundaries are inclusive on the **later** side: decision_time
equal to a boundary belongs to the LATER phase (e.g., decision_time ==
settlement_day_entry_utc → SETTLEMENT_DAY; decision_time ==
polymarket_end_utc → POST_TRADING). This pins T3 in PLAN_v2 §8.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo


class MarketPhase(Enum):
    PRE_TRADING = "pre_trading"
    PRE_SETTLEMENT_DAY = "pre_settlement_day"
    SETTLEMENT_DAY = "settlement_day"
    POST_TRADING = "post_trading"
    RESOLVED = "resolved"


SETTLEMENT_DAY_LOOKBACK_HOURS: int = 24
"""Hours before city-local end-of-target_date at which SETTLEMENT_DAY
begins. Per PLAN_v2 §2 boundary 2 + operator framing
"day 0 应该交易所有当地市场 0 点前的 24 个小时" (STRATEGIES_AND_GAPS §3.1).
"""


def settlement_day_entry_utc(
    *,
    target_local_date: date,
    city_timezone: str,
) -> datetime:
    """UTC instant at which a city's market enters ``SETTLEMENT_DAY``.

    Defined as 24h before city-local end-of-target_date — i.e., 24h
    before city-local 00:00 of ``target_date + 1`` day.

    DST-correct because ``ZoneInfo(city_timezone)`` resolves the local
    offset for the *target boundary date*, not the *current* host
    offset. Spring-forward / fall-back days produce a 23h or 25h
    SETTLEMENT_DAY window; downstream code must tolerate this rather
    than assume exactly 24h.
    """
    end_of_target_local = datetime.combine(
        target_local_date + timedelta(days=1),
        time(0, 0, 0),
        tzinfo=ZoneInfo(city_timezone),
    )
    end_of_target_utc = end_of_target_local.astimezone(timezone.utc)
    return end_of_target_utc - timedelta(hours=SETTLEMENT_DAY_LOOKBACK_HOURS)


def market_phase_for_decision(
    *,
    target_local_date: date,
    city_timezone: str,
    decision_time_utc: datetime,
    polymarket_start_utc: Optional[datetime],
    polymarket_end_utc: datetime,
    uma_resolved: bool = False,
) -> MarketPhase:
    """Compute ``MarketPhase`` at ``decision_time_utc`` given market
    boundaries.

    All datetime arguments MUST be timezone-aware. ``polymarket_start_utc``
    may be ``None`` when the start time is unknown (e.g., during
    pre-discovery when only target_date and city are known); in that
    case the function returns ``PRE_SETTLEMENT_DAY`` whenever
    ``decision_time_utc`` is before the SETTLEMENT_DAY anchor (the
    market is treated as already trading by default — the caller must
    upstream-filter PRE_TRADING markets when start time is unavailable).

    See PLAN_v2 §2 for the boundary table. T3 in §8 pins inclusive-late
    semantics; T4 pins the 12:00 UTC POST_TRADING anchor.
    """
    if decision_time_utc.tzinfo is None:
        raise ValueError(
            "decision_time_utc must be timezone-aware (UTC). Naive "
            "datetimes silently drift across host tz; per critic R1 C5 "
            "and operator directive 2026-05-04 (UTC-strict execution)."
        )
    if polymarket_end_utc.tzinfo is None:
        raise ValueError("polymarket_end_utc must be timezone-aware (UTC).")
    if polymarket_start_utc is not None and polymarket_start_utc.tzinfo is None:
        raise ValueError("polymarket_start_utc must be timezone-aware (UTC).")

    if uma_resolved:
        return MarketPhase.RESOLVED

    if decision_time_utc >= polymarket_end_utc:
        return MarketPhase.POST_TRADING

    sd_entry = settlement_day_entry_utc(
        target_local_date=target_local_date,
        city_timezone=city_timezone,
    )
    if decision_time_utc >= sd_entry:
        return MarketPhase.SETTLEMENT_DAY

    if polymarket_start_utc is not None and decision_time_utc < polymarket_start_utc:
        return MarketPhase.PRE_TRADING

    return MarketPhase.PRE_SETTLEMENT_DAY


# ----------------------------------------------------------------------- #
# Adapter from src.data.market_scanner market dict shape.
# Owned here so cycle_runtime can call a single function rather than
# threading parsing logic through the discovery loop.
# ----------------------------------------------------------------------- #


def _parse_utc(value: str) -> datetime:
    """Parse an ISO 8601 string from Gamma into a UTC datetime.
    Accepts the ``Z`` suffix variant and the ``+HH:MM`` offset variant.
    """
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(
            f"unexpected naive datetime from Gamma payload: {value!r}; "
            "every external timestamp must carry tz info per UTC-strict "
            "directive (operator 2026-05-04)."
        )
    return parsed.astimezone(timezone.utc)


def _f1_fallback_end_utc(target_local_date: date) -> datetime:
    """F1 invariant: Polymarket weather endDate is uniformly 12:00 UTC of
    target_date (verified across 13 cities via Gamma API; see
    INVESTIGATION_EXTERNAL Q1). When the market dict does not carry an
    explicit ``market_end_at``, fall back to this derived anchor.
    """
    return datetime.combine(target_local_date, time(12, 0, 0), tzinfo=timezone.utc)


def market_phase_from_market_dict(
    *,
    market: dict,
    city_timezone: str,
    target_date_str: str,
    decision_time_utc: datetime,
    uma_resolved: bool = False,
) -> MarketPhase:
    """Adapter from ``src.data.market_scanner``'s market dict shape to
    ``MarketPhase``.

    Reads ``market["market_end_at"]`` and ``market["market_start_at"]``
    when present; falls back to F1's uniform 12:00 UTC anchor for
    end_utc and ``None`` for start_utc when absent. The fallback is
    intentional: per F1, every Polymarket weather market settles at
    12:00 UTC of its target_date, so a missing endDate is recoverable
    without a hard error.
    """
    target_local_date = date.fromisoformat(target_date_str)

    end_str = market.get("market_end_at") or market.get("endDate") or market.get("end_date")
    polymarket_end_utc = (
        _parse_utc(end_str) if end_str else _f1_fallback_end_utc(target_local_date)
    )

    start_str = market.get("market_start_at") or market.get("startDate") or market.get("start_date")
    polymarket_start_utc = _parse_utc(start_str) if start_str else None

    return market_phase_for_decision(
        target_local_date=target_local_date,
        city_timezone=city_timezone,
        decision_time_utc=decision_time_utc,
        polymarket_start_utc=polymarket_start_utc,
        polymarket_end_utc=polymarket_end_utc,
        uma_resolved=uma_resolved,
    )
