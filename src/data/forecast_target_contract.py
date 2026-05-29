"""Future target-local-date forecast coverage contracts.

This module contains pure relationship helpers for PLAN_v4. It does not fetch
or write forecast data; producer and evaluator code use these helpers to avoid
collapsing source-cycle freshness into live market coverage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import ceil
from zoneinfo import ZoneInfo

UTC = timezone.utc
LIVE_ELIGIBLE = "LIVE_ELIGIBLE"
BLOCKED = "BLOCKED"

# Polymarket weather markets cap at 5 days, so ECMWF Open Data forecast steps
# beyond 144h are no longer consumed. The OpenData required step set therefore
# caps at the 3h-dissemination ceiling of 144h. TIGGE (the historical archive)
# is on its own full range and is NOT capped here.
OPENDATA_MAX_STEP_HOURS = 144


def aggregation_window_hours_for_data_version(data_version: str) -> int:
    """Map a forecast product (by data_version param token) to its physical
    temperature-aggregation window in hours. Fail-closed on unknown products.

    This is the single source of truth that makes the *wrong* aggregation window
    UNCONSTRUCTABLE: the window is keyed on the product token, never on a shared
    scalar. ECMWF definitions (architecture/data_sources_registry_2026_05_08.yaml
    :86,91; ECMWF Open Data step144 mn2t3 field verified lengthOfTimeRange=3 on
    2026-05-29):
        mx2t3 / mn2t3 -> 3h  (ECMWF Open Data, "min/max 2m temp in the last 3h")
        mx2t6 / mn2t6 -> 6h  (TIGGE archive,    "min/max 2m temp in the last 6h")

    Note: a data_version *string* carrying a legacy ``mx2t6``/``mn2t6`` token is
    treated as 6h here; the live Open Data versions are ``ecmwf_opendata_mx2t3_*``
    / ``ecmwf_opendata_mn2t3_*`` (3h tokens), so the live path resolves to 3h.
    Token precedence checks the 3h tokens first.
    """
    dv = str(data_version)
    if "mx2t3" in dv or "mn2t3" in dv:
        return 3
    if "mx2t6" in dv or "mn2t6" in dv:
        return 6
    raise ValueError(
        f"aggregation_window_hours_for_data_version: unknown product, cannot "
        f"derive aggregation window from data_version {data_version!r}. "
        f"Register the product's param token (m[xn]2t3 -> 3h, m[xn]2t6 -> 6h)."
    )


def opendata_step_cap_for_data_version(data_version: str) -> int | None:
    """Return the max required step (hours) for a product, or None for uncapped.

    ECMWF Open Data products (``ecmwf_opendata_*``) cap at OPENDATA_MAX_STEP_HOURS
    (144h) because Polymarket markets cap at 5 days. The TIGGE archive is uncapped.
    """
    dv = str(data_version)
    if dv.startswith("ecmwf_opendata_"):
        return OPENDATA_MAX_STEP_HOURS
    return None


@dataclass(frozen=True)
class TargetLocalDayWindow:
    city_timezone: str
    target_local_date: date
    start_utc: datetime
    end_utc: datetime


@dataclass(frozen=True)
class ForecastTargetScope:
    city_id: str
    city_name: str
    city_timezone: str
    target_local_date: date
    temperature_metric: str
    source_cycle_time: datetime
    data_version: str
    target_window_start_utc: datetime
    target_window_end_utc: datetime
    required_step_hours: tuple[int, ...]
    market_refs: tuple[str, ...]


@dataclass(frozen=True)
class CoverageDecision:
    status: str
    reason_codes: tuple[str, ...]


def _to_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def compute_target_local_day_window_utc(
    *,
    city_timezone: str,
    target_local_date: date,
) -> TargetLocalDayWindow:
    zone = ZoneInfo(city_timezone)
    start_local = datetime.combine(target_local_date, time.min, tzinfo=zone)
    end_local = datetime.combine(target_local_date + timedelta(days=1), time.min, tzinfo=zone)
    return TargetLocalDayWindow(
        city_timezone=city_timezone,
        target_local_date=target_local_date,
        start_utc=start_local.astimezone(UTC),
        end_utc=end_local.astimezone(UTC),
    )


def required_period_end_steps(
    *,
    source_cycle_time: datetime,
    target_window_start_utc: datetime,
    target_window_end_utc: datetime,
    period_hours: int = 6,
    max_step_hours: int | None = None,
) -> tuple[int, ...]:
    """Period-end step set covering the target window.

    ``period_hours`` is the product's aggregation window (3h for OpenData
    mx2t3/mn2t3, 6h for TIGGE mx2t6/mn2t6). ``max_step_hours``, when set, caps
    the returned steps (ECMWF Open Data caps at 144h per the Polymarket 5-day
    market window); None leaves the set uncapped (TIGGE archive).
    """
    if period_hours <= 0:
        raise ValueError("period_hours must be positive")
    source_cycle_utc = _to_utc(source_cycle_time, "source_cycle_time")
    window_start = _to_utc(target_window_start_utc, "target_window_start_utc")
    window_end = _to_utc(target_window_end_utc, "target_window_end_utc")
    if window_end <= window_start:
        raise ValueError("target window end must be after start")

    latest_needed_hours = (window_end - source_cycle_utc).total_seconds() / 3600
    last_step = max(period_hours, ceil(latest_needed_hours / period_hours) * period_hours + period_hours)
    required_steps: list[int] = []
    for step_hour in range(period_hours, last_step + period_hours, period_hours):
        if max_step_hours is not None and step_hour > max_step_hours:
            break
        valid_time = source_cycle_utc + timedelta(hours=step_hour)
        period_start = valid_time - timedelta(hours=period_hours)
        if valid_time > window_start and period_start < window_end:
            required_steps.append(step_hour)
    return tuple(required_steps)


def build_forecast_target_scope(
    *,
    city_id: str,
    city_name: str,
    city_timezone: str,
    target_local_date: date,
    temperature_metric: str,
    source_cycle_time: datetime,
    data_version: str,
    market_refs: tuple[str, ...] = (),
) -> ForecastTargetScope:
    if temperature_metric not in {"high", "low"}:
        raise ValueError("temperature_metric must be 'high' or 'low'")
    source_cycle_utc = _to_utc(source_cycle_time, "source_cycle_time")
    window = compute_target_local_day_window_utc(
        city_timezone=city_timezone,
        target_local_date=target_local_date,
    )
    # Aggregation window + horizon cap are PRODUCT-DERIVED: OpenData mx2t3/mn2t3
    # are 3h products capped at 144h; TIGGE mx2t6/mn2t6 are 6h, uncapped. This
    # makes the wrong (6h-on-3h-product) window unconstructable.
    period_hours = aggregation_window_hours_for_data_version(data_version)
    max_step_hours = opendata_step_cap_for_data_version(data_version)
    required_steps = required_period_end_steps(
        source_cycle_time=source_cycle_utc,
        target_window_start_utc=window.start_utc,
        target_window_end_utc=window.end_utc,
        period_hours=period_hours,
        max_step_hours=max_step_hours,
    )
    return ForecastTargetScope(
        city_id=city_id,
        city_name=city_name,
        city_timezone=city_timezone,
        target_local_date=target_local_date,
        temperature_metric=temperature_metric,
        source_cycle_time=source_cycle_utc,
        data_version=data_version,
        target_window_start_utc=window.start_utc,
        target_window_end_utc=window.end_utc,
        required_step_hours=required_steps,
        market_refs=market_refs,
    )


def evaluate_horizon_coverage(
    *,
    required_steps: tuple[int, ...],
    live_max_step_hours: int,
) -> CoverageDecision:
    if not required_steps:
        return CoverageDecision(BLOCKED, ("MISSING_REQUIRED_STEPS",))
    if max(required_steps) > live_max_step_hours:
        return CoverageDecision(BLOCKED, ("SOURCE_RUN_HORIZON_OUT_OF_RANGE",))
    return CoverageDecision(LIVE_ELIGIBLE, ("HORIZON_COVERED",))


def evaluate_producer_coverage(
    *,
    city_id: str,
    city_timezone: str,
    target_local_date: date,
    temperature_metric: str,
    source_id: str,
    source_transport: str,
    source_run_status: str,
    source_run_completeness: str,
    snapshot_target_date: date | None,
    snapshot_metric: str | None,
    expected_steps: tuple[int, ...],
    observed_steps: tuple[int, ...],
    expected_members: int,
    observed_members: int,
    has_source_linkage: bool,
) -> CoverageDecision:
    del city_id, city_timezone, source_id
    reason_codes: list[str] = []

    if source_transport != "ensemble_snapshots_db_reader":
        reason_codes.append("DIRECT_FETCH_ENTRY_PATH_BLOCKED")
    if source_run_status not in {"SUCCESS", "PARTIAL"}:
        reason_codes.append(f"SOURCE_RUN_{source_run_status}")
    if source_run_completeness not in {"COMPLETE", "PARTIAL"}:
        reason_codes.append(f"SOURCE_RUN_{source_run_completeness}")
    if snapshot_target_date is None:
        reason_codes.append("FUTURE_TARGET_DATE_NOT_COVERED")
    elif snapshot_target_date != target_local_date:
        reason_codes.append("SNAPSHOT_TARGET_DATE_MISMATCH")
    if snapshot_metric is None:
        reason_codes.append("SNAPSHOT_METRIC_MISSING")
    elif snapshot_metric != temperature_metric:
        reason_codes.append("SNAPSHOT_METRIC_MISMATCH")

    missing_steps = set(expected_steps) - set(observed_steps)
    if missing_steps:
        reason_codes.append("MISSING_REQUIRED_STEPS")
    if observed_members < expected_members:
        reason_codes.append("MISSING_EXPECTED_MEMBERS")
    if not has_source_linkage:
        reason_codes.append("SNAPSHOT_SOURCE_LINKAGE_MISSING")

    if reason_codes:
        return CoverageDecision(BLOCKED, tuple(reason_codes))
    return CoverageDecision(LIVE_ELIGIBLE, ("FUTURE_TARGET_DATE_COVERED",))
