# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_strategy_redesign_day0_endgame/PLAN_v3.md §2 + §6.P2 (v3 per §0.1).
"""``MarketPhase`` axis — market-time lifecycle of a Polymarket weather market.

Per PLAN_v3 §2 (axis A), ``MarketPhase`` is computed from
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

Boundary anchors (locked from PLAN_v3 §1.E1+§2):

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
polymarket_end_utc → POST_TRADING). This pins T3 in PLAN_v3 §8.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo


class MarketPhase(str, Enum):
    """``str``-valued enum so SQL/JSON serialization is uniform with the
    rest of the project's state-like enums (e.g. ``LifecyclePhase`` at
    ``src/state/lifecycle_manager.py:9``). Inheriting from ``str`` makes
    ``MarketPhase.SETTLEMENT_DAY == "settlement_day"`` true and lets
    SQLite bind the value directly.
    """

    PRE_TRADING = "pre_trading"
    PRE_SETTLEMENT_DAY = "pre_settlement_day"
    SETTLEMENT_DAY = "settlement_day"
    POST_TRADING = "post_trading"
    RESOLVED = "resolved"


def _require_zero_utc_offset(value: datetime, name: str) -> datetime:
    """Validate that ``value`` is a tz-aware datetime carrying a
    zero-offset timezone. Rejects naive datetimes and non-zero-offset
    zones (the canonical error case: ``ZoneInfo("America/Chicago")``).

    DELIBERATE LOOSENESS — accepts any zero-offset zone, including:
      - ``timezone.utc`` (canonical)
      - ``ZoneInfo("UTC")``
      - ``timezone(timedelta(0))``
      - ``ZoneInfo("Europe/London")`` IN WINTER (offset 0 for half the year)
      - ``ZoneInfo("Atlantic/Reykjavik")`` (offset 0 year-round)

    These all produce identical UTC instants and any subsequent UTC
    arithmetic is correct. The function is named ``_require_zero_utc_offset``
    rather than ``_require_utc`` to make this explicit: a future caller
    expecting "the result is literally ``timezone.utc``" must check
    additionally. Production today calls with ``datetime.now(timezone.utc)``
    so the looseness is unobservable; the rename guards against future
    misreading per critic R3 ATTACK 6 (PR #53 review).
    """
    if value.tzinfo is None:
        raise ValueError(
            f"{name} must be timezone-aware. Naive datetimes silently drift "
            f"across host tz; per critic R1 C5 and operator directive "
            f"2026-05-04 (UTC-strict execution)."
        )
    if value.utcoffset() != timedelta(0):
        raise ValueError(
            f"{name} must carry zero UTC offset; got {value.tzinfo!r} with "
            f"offset {value.utcoffset()!r}. Per UTC-strict directive, "
            f"callers convert at the boundary before passing — silent "
            f"astimezone() here would hide the bug."
        )
    return value


def settlement_day_entry_utc(
    *,
    target_local_date: date,
    city_timezone: str,
) -> datetime:
    """UTC instant at which a city's market enters ``SETTLEMENT_DAY``.

    Defined as **city-local 00:00 of ``target_local_date``** — i.e., the
    start of the local calendar day for ``target_date``. The
    SETTLEMENT_DAY window equals the LOCAL calendar day (per operator
    framing "day 0 应该交易所有当地市场 0 点前的 24 个小时" =
    "trade all 24 hours before midnight of the local market" =
    the local target_date day itself, anchored at local midnight).

    On DST-transition target dates this yields a 23h or 25h window in
    UTC wall-clock terms (because the local day itself is 23h or 25h
    long), which is the *correct* behavior — downstream code reasons
    about local-calendar-day geometry, not fixed UTC intervals.
    Anchoring at ``end_of_target_utc - 24h`` would silently shift the
    boundary by ±1h on DST days and misphase decisions; caught in
    PR #53 review (Copilot comment 3179345263).
    """
    sd_entry_local = datetime.combine(
        target_local_date,
        time(0, 0, 0),
        tzinfo=ZoneInfo(city_timezone),
    )
    return sd_entry_local.astimezone(timezone.utc)


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

    All datetime arguments MUST carry zero UTC offset (``utcoffset() ==
    timedelta(0)``). Naive or non-zero-offset datetimes raise
    ``ValueError`` rather than being silently coerced — see
    ``_require_zero_utc_offset`` for the rationale.

    ``polymarket_start_utc`` may be ``None`` when the start time is
    unknown (e.g., during pre-discovery when only target_date and city
    are known); in that case the function returns ``PRE_SETTLEMENT_DAY``
    whenever ``decision_time_utc`` is before the SETTLEMENT_DAY
    anchor (the market is treated as already trading by default — the
    caller must upstream-filter PRE_TRADING markets when start time is
    unavailable).

    See PLAN_v3 §2 for the boundary table. T3 in §8 pins inclusive-late
    semantics; T4 pins the 12:00 UTC POST_TRADING anchor.
    """
    decision_time_utc = _require_zero_utc_offset(decision_time_utc, "decision_time_utc")
    polymarket_end_utc = _require_zero_utc_offset(polymarket_end_utc, "polymarket_end_utc")
    if polymarket_start_utc is not None:
        polymarket_start_utc = _require_zero_utc_offset(polymarket_start_utc, "polymarket_start_utc")

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
