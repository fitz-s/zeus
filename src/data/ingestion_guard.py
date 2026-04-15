"""Write-time IngestionGuard for temperature observations.

Any layer failure raises a typed IngestionRejected subclass, making the
corresponding ObservationAtom unconstructable.

Active layer order (executed by validate()):
  1. check_unit_consistency   — Earth record cross-check + city.settlement_unit
  2. check_physical_bounds    — TIGGE p01/p99 lookup (lat-band fallback if null)
  3. — DELETED — (was hemisphere-uniform seasonal envelope)
  4. check_collection_timing  — fetch must be after local peak_hour on target_date
  5. check_dst_boundary       — spring-forward missing hours are rejected

Layer 3 history: the original check_seasonal_plausibility used a single
_N_ENVELOPE / _S_ENVELOPE pair applied uniformly to every city in the
hemisphere. This was a category error — "Austin March" and "Seattle March"
are both N-hemisphere month 3 but have radically different climate
distributions, so one envelope either false-rejected Austin hot days or
let Seattle garbage through. Confirmed 22 false positives in a single
backfill run (Austin/Houston N-hemisphere heat, Sao Paulo/Buenos Aires
S-hemisphere autumn/winter warm days). Deleted 2026-04-13.

The replacement strategy is:
  - Layer 1 Earth records catches catastrophic magnitude errors (Houston 160°F)
  - Post-ingestion relationship tests alert on day-to-day delta >40°F or
    climate z-score >3 without silently dropping the row

Unit-consistency runs FIRST so mislabelled rows are caught before numeric
bounds checks, which would otherwise pass nonsensical values silently.

Part of K1-B packet. See .omc/plans/k1-freeze.md section 6.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from src.config import cities_by_name
from src.types.observation_atom import IngestionRejected


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

class PhysicalBoundsViolation(IngestionRejected):
    """Layer 2: value outside TIGGE-derived p01/p99 ± 2σ for city/month."""


class UnitConsistencyViolation(IngestionRejected):
    """Layer 1: unit mislabelling detected (Earth record check or city.settlement_unit mismatch)."""


class UnknownCityViolation(IngestionRejected):
    """City metadata is required before an observation can be validated."""


# Layer 3 (SeasonalPlausibilityViolation) deleted 2026-04-13 after 22 confirmed
# false-positive rejections in a single backfill run (Austin/Houston N-hemisphere
# heat, Sao Paulo/Buenos Aires S-hemisphere autumn/winter warm days). The
# hemisphere-uniform _N_ENVELOPE / _S_ENVELOPE applied one envelope to every
# city in the hemisphere regardless of climate zone, making Austin (hot subtropical)
# and Seattle (mild marine) share the same March upper bound of 95°F — which
# rejected legitimate Austin hot days. Root cause is "one-size-fits-all envelope
# for all cities" — a category error, not a tuning problem. Replaced by:
#   - Layer 1 Earth records (already catches magnitude errors like Houston 160°F)
#   - Post-ingestion relationship tests (day-to-day delta continuity, climate
#     z-score) that alert without silently dropping data


class CollectionTimingViolation(IngestionRejected):
    """Layer 4: fetch occurred before the target day's local peak_hour."""


class DstBoundaryViolation(IngestionRejected):
    """Layer 5: local_time falls in a spring-forward DST gap (non-existent hour)."""


# ---------------------------------------------------------------------------
# IngestionGuard
# ---------------------------------------------------------------------------

class IngestionGuard:
    """Write-time validation gate.  Any layer failure raises IngestionRejected subclass.

    Usage pattern (caller in wu_daily_collector.py)::

        guard = IngestionGuard()  # loads city_monthly_bounds.json once
        guard.validate(city, raw_value, raw_unit, fetch_utc, target_date,
                       peak_hour, local_time, hemisphere)
        atom = ObservationAtom(..., authority='VERIFIED', validation_pass=True)
    """

    #: Class-level counter incremented on every raised rejection.
    rejected_count: int = 0

    def __init__(self, bounds_path: Optional[Path] = None) -> None:
        if bounds_path is None:
            bounds_path = (
                Path(__file__).parent.parent.parent
                / "config"
                / "city_monthly_bounds.json"
            )
        with open(bounds_path) as f:
            self._bounds: dict = json.load(f)["cities"]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    def _increment_rejected(cls) -> None:
        cls.rejected_count += 1

    def _log_availability_failure(
        self,
        city: str,
        target_date: date,
        failure_type: str,
        details: dict,
    ) -> None:
        """Best-effort INSERT into availability_fact. Swallows exceptions silently."""
        try:
            from src.state.db import get_world_connection, log_availability_fact
            conn = get_world_connection()
            now_iso = datetime.now(timezone.utc).isoformat()
            log_availability_fact(
                conn,
                availability_id=str(uuid.uuid4()),
                scope_type="city_target",
                scope_key=f"{city}/{target_date.isoformat()}",
                failure_type=failure_type,
                started_at=now_iso,
                ended_at=now_iso,
                impact="block",
                details=details,
            )
            conn.commit()
            conn.close()  # K3.7: prevent fd leak on high-rejection backfills
        except Exception as exc:
            # Bug #31: log instead of silently swallowing
            import logging as _logging
            _logging.getLogger(__name__).debug(
                "availability fact write failed for %s/%s: %s",
                city, target_date, exc,
            )

    # ------------------------------------------------------------------
    # Layer 1 — unit consistency
    # ------------------------------------------------------------------

    def check_unit_consistency(
        self,
        city: str,
        raw_value: float,
        raw_unit: str,
        declared_unit: str,
        target_date: date | None = None,
    ) -> None:
        """Layer 1: detect unit mislabelling via Earth temperature records and city config.

        Two sub-checks (both run):

        a) Cross-check declared_unit against city.settlement_unit from config.
           Catches the Buenos Aires / Hong Kong / Toronto class of bug where
           raw F values are labelled with wrong unit for a C city.

        b) Earth-record absolute bounds on raw_value × raw_unit.
           Catches the Houston 160°F and Lagos 89°C class of bug.
        """
        log_date = target_date or datetime.now(timezone.utc).date()
        city_obj = cities_by_name.get(city)
        if raw_unit not in {"C", "F", "K"}:
            type(self)._increment_rejected()
            self._log_availability_failure(
                city,
                log_date,
                "UnitConsistencyViolation",
                {"raw_value": raw_value, "raw_unit": raw_unit, "check": "unknown_unit"},
            )
            raise UnitConsistencyViolation(
                f"{city}: raw_unit={raw_unit!r} is not one of 'F', 'C', 'K'"
            )
        if city_obj is None:
            type(self)._increment_rejected()
            self._log_availability_failure(
                city,
                log_date,
                "UnknownCityViolation",
                {"check": "unit_consistency"},
            )
            raise UnknownCityViolation(f"{city}: missing from config/cities.json")
        # Sub-check a: city.settlement_unit cross-check
        if declared_unit != city_obj.settlement_unit:
            type(self)._increment_rejected()
            self._log_availability_failure(
                city,
                log_date,
                "UnitConsistencyViolation",
                {
                    "declared_unit": declared_unit,
                    "settlement_unit": city_obj.settlement_unit,
                },
            )
            raise UnitConsistencyViolation(
                f"{city}: declared unit={declared_unit!r} but city.settlement_unit="
                f"{city_obj.settlement_unit!r} (config/cities.json)"
            )
        # Sub-check b: Earth record bounds
        if raw_unit == "C":
            if raw_value > 56.7:
                type(self)._increment_rejected()
                self._log_availability_failure(
                    city,
                    log_date,
                    "UnitConsistencyViolation",
                    {
                        "raw_value": raw_value,
                        "raw_unit": raw_unit,
                        "check": "earth_record_high_C",
                    },
                )
                raise UnitConsistencyViolation(
                    f"{city}: {raw_value}\u00b0C exceeds Earth record 56.7\u00b0C — "
                    f"likely mislabelled \u00b0F value"
                )
            if raw_value < -89.2:
                type(self)._increment_rejected()
                self._log_availability_failure(
                    city,
                    log_date,
                    "UnitConsistencyViolation",
                    {
                        "raw_value": raw_value,
                        "raw_unit": raw_unit,
                        "check": "earth_record_low_C",
                    },
                )
                raise UnitConsistencyViolation(
                    f"{city}: {raw_value}\u00b0C below Earth record -89.2\u00b0C"
                )
        elif raw_unit == "F":
            if raw_value > 134.0:
                type(self)._increment_rejected()
                self._log_availability_failure(
                    city,
                    log_date,
                    "UnitConsistencyViolation",
                    {
                        "raw_value": raw_value,
                        "raw_unit": raw_unit,
                        "check": "earth_record_high_F",
                    },
                )
                raise UnitConsistencyViolation(
                    f"{city}: {raw_value}\u00b0F exceeds Earth record 134\u00b0F"
                )
            if raw_value < -128.6:
                type(self)._increment_rejected()
                self._log_availability_failure(
                    city,
                    log_date,
                    "UnitConsistencyViolation",
                    {
                        "raw_value": raw_value,
                        "raw_unit": raw_unit,
                        "check": "earth_record_low_F",
                    },
                )
                raise UnitConsistencyViolation(
                    f"{city}: {raw_value}\u00b0F below Earth record -128.6\u00b0F"
                )

    # ------------------------------------------------------------------
    # Layer 2 — physical bounds (TIGGE-derived or lat-band fallback)
    # ------------------------------------------------------------------

    def check_physical_bounds(
        self,
        city: str,
        value_f: float,
        month: int,
        target_date: date | None = None,
    ) -> None:
        """Layer 2: value within p01-2σ … p99+2σ for city/month.

        Falls back to latitude-band heuristic when TIGGE bounds are null or
        absent (sample_count < 30, or city not in city_monthly_bounds.json).
        """
        city_data = self._bounds.get(city)
        if city_data is None:
            return self._check_lat_band_bounds(city, value_f, month, target_date=target_date)
        month_entry = city_data.get(str(month))
        if month_entry is None:
            return self._check_lat_band_bounds(city, value_f, month, target_date=target_date)

        p01 = month_entry["p01"]
        p99 = month_entry["p99"]
        # Convert stored percentiles to °F for comparison (value_f is already °F)
        if month_entry["unit"] == "C":
            p01 = p01 * 9 / 5 + 32
            p99 = p99 * 9 / 5 + 32
        # Estimate σ as (p99 - p01) / 4  (IQR-like approximation)
        sigma = (p99 - p01) / 4 if p99 > p01 else 5.0
        lower = p01 - 2 * sigma
        upper = p99 + 2 * sigma
        if not (lower <= value_f <= upper):
            type(self)._increment_rejected()
            self._log_availability_failure(
                city,
                target_date or datetime.now(timezone.utc).date(),
                "PhysicalBoundsViolation",
                {
                    "value_f": value_f,
                    "lower": lower,
                    "upper": upper,
                    "month": month,
                    "path": "tigge",
                },
            )
            raise PhysicalBoundsViolation(
                f"{city} month={month}: {value_f}\u00b0F outside "
                f"[{lower:.1f}, {upper:.1f}]\u00b0F "
                f"(TIGGE p01={month_entry['p01']}, p99={month_entry['p99']} "
                f"unit={month_entry['unit']})"
            )

    def _check_lat_band_bounds(
        self,
        city: str,
        value_f: float,
        month: int,
        target_date: date | None = None,
    ) -> None:
        """Fallback: crude climatological bounds by absolute latitude band (°F)."""
        city_obj = cities_by_name.get(city)
        if city_obj is None:
            type(self)._increment_rejected()
            self._log_availability_failure(
                city,
                target_date or datetime.now(timezone.utc).date(),
                "UnknownCityViolation",
                {"check": "lat_band_bounds", "month": month},
            )
            raise UnknownCityViolation(f"{city}: missing from config/cities.json")
        abs_lat = abs(city_obj.lat)
        if abs_lat < 23.5:      # tropical
            lower, upper = 50.0, 110.0
        elif abs_lat < 45:      # temperate
            lower, upper = -20.0, 120.0
        else:                   # high-latitude / sub-polar
            lower, upper = -60.0, 105.0
        if not (lower <= value_f <= upper):
            type(self)._increment_rejected()
            self._log_availability_failure(
                city,
                target_date or datetime.now(timezone.utc).date(),
                "PhysicalBoundsViolation",
                {
                    "value_f": value_f,
                    "lower": lower,
                    "upper": upper,
                    "month": month,
                    "path": "lat_band",
                },
            )
            raise PhysicalBoundsViolation(
                f"{city} month={month}: {value_f}\u00b0F outside lat-band "
                f"[{lower}, {upper}]\u00b0F (|lat|={abs_lat:.1f}\u00b0, fallback path)"
            )

    # Layer 3 (check_seasonal_plausibility) deleted 2026-04-13 — see comment
    # at module top. The hemisphere-uniform _N_ENVELOPE / _S_ENVELOPE was a
    # category error: Austin and Seattle shared the same March upper bound of
    # 95°F even though Austin commonly hits 97-100°F in March. Widening the
    # envelope would just shift the false-positive boundary to a different
    # (city, month) pair. Layer 1 Earth records + future relationship tests
    # are the replacement.

    # ------------------------------------------------------------------
    # Layer 4 — collection timing
    # ------------------------------------------------------------------

    def check_collection_timing(
        self,
        city: str,
        fetch_utc: datetime,
        target_date: date,
        peak_hour: float,
    ) -> None:
        """Layer 4: fetch must occur at or after local peak_hour on target_date.

        If the fetch happens *before* the day's expected temperature peak, the
        daily-max value cannot yet be observed.
        """
        city_obj = cities_by_name.get(city)
        if city_obj is None:
            type(self)._increment_rejected()
            self._log_availability_failure(
                city,
                target_date,
                "UnknownCityViolation",
                {"check": "collection_timing"},
            )
            raise UnknownCityViolation(f"{city}: missing from config/cities.json")
        tz = ZoneInfo(city_obj.timezone)
        fetch_local = fetch_utc.astimezone(tz)
        fetch_date_local = fetch_local.date()
        if fetch_date_local == target_date:
            peak_hour_int = int(peak_hour)
            peak_minute = int(round((float(peak_hour) - peak_hour_int) * 60))
            peak_minutes = peak_hour_int * 60 + peak_minute
            fetch_minutes = fetch_local.hour * 60 + fetch_local.minute
            if fetch_minutes < peak_minutes:
                type(self)._increment_rejected()
                self._log_availability_failure(
                    city,
                    target_date,
                    "CollectionTimingViolation",
                    {
                        "fetch_local_time": fetch_local.strftime("%H:%M"),
                        "peak_hour": peak_hour,
                        "target_date": target_date.isoformat(),
                    },
                )
                raise CollectionTimingViolation(
                    f"{city}: fetched at local time {fetch_local.strftime('%H:%M')} on "
                    f"{target_date}, but peak_hour={peak_hour} — daily max not yet observed"
                )
        elif fetch_date_local < target_date:
            type(self)._increment_rejected()
            self._log_availability_failure(
                city,
                target_date,
                "CollectionTimingViolation",
                {
                    "fetch_date_local": fetch_date_local.isoformat(),
                    "target_date": target_date.isoformat(),
                },
            )
            raise CollectionTimingViolation(
                f"{city}: fetched on {fetch_date_local} but target_date is "
                f"{target_date} (fetch is in the past relative to target)"
            )
        # fetch_date_local > target_date: fine (next-day or later collection)

    # ------------------------------------------------------------------
    # Layer 5 — DST boundary
    # ------------------------------------------------------------------

    def check_dst_boundary(self, city: str, local_time: datetime) -> None:
        """Layer 5: reject observations whose local_time is in a spring-forward DST gap.

        Ambiguous fall-back hours are allowed (they exist on the wall clock)
        but should be flagged by the caller via provenance_metadata.
        """
        city_obj = cities_by_name.get(city)
        if city_obj is None:
            type(self)._increment_rejected()
            _td = local_time.date() if hasattr(local_time, 'date') else datetime.now(timezone.utc).date()
            self._log_availability_failure(
                city,
                _td,
                "UnknownCityViolation",
                {"check": "dst_boundary"},
            )
            raise UnknownCityViolation(f"{city}: missing from config/cities.json")
        tz = ZoneInfo(city_obj.timezone)
        from src.signal.diurnal import _is_missing_local_hour  # avoid circular at module load
        if _is_missing_local_hour(local_time, tz):
            type(self)._increment_rejected()
            _td = local_time.date() if hasattr(local_time, 'date') else datetime.now(timezone.utc).date()
            self._log_availability_failure(
                city,
                _td,
                "DstBoundaryViolation",
                {
                    "local_time": (
                        local_time.isoformat()
                        if hasattr(local_time, 'isoformat')
                        else str(local_time)
                    ),
                    "timezone": city_obj.timezone,
                },
            )
            raise DstBoundaryViolation(
                f"{city}: local_time {local_time} falls in a spring-forward DST gap "
                f"(hour does not exist on the wall clock for {city_obj.timezone})"
            )

    # ------------------------------------------------------------------
    # Orchestrator
    # ------------------------------------------------------------------

    def validate(
        self,
        city: str,
        raw_value: float,
        raw_unit: str,
        fetch_utc: datetime,
        target_date: date,
        peak_hour: float,
        local_time: datetime,
        hemisphere: str,
    ) -> None:
        """Run all 5 layers in order.  Raises IngestionRejected on first failure.

        Caller is responsible for constructing ObservationAtom only AFTER this
        method returns without raising.

        Unit conversion for bounds/seasonal layers (all compare in °F)::

            raw_unit == 'C' → value_f = raw_value * 9/5 + 32
            raw_unit == 'K' → value_f = (raw_value - 273.15) * 9/5 + 32
            raw_unit == 'F' → value_f = raw_value
        """
        city_obj = cities_by_name.get(city)
        if city_obj is None:
            type(self)._increment_rejected()
            self._log_availability_failure(
                city,
                target_date,
                "UnknownCityViolation",
                {"check": "validate"},
            )
            raise UnknownCityViolation(f"{city}: missing from config/cities.json")
        declared_unit = city_obj.settlement_unit

        # Convert raw to °F for layers 2 & 3
        if raw_unit == "C":
            value_f = raw_value * 9 / 5 + 32
        elif raw_unit == "K":
            value_f = (raw_value - 273.15) * 9 / 5 + 32
        elif raw_unit == "F":
            value_f = raw_value
        else:
            type(self)._increment_rejected()
            self._log_availability_failure(
                city,
                target_date,
                "UnitConsistencyViolation",
                {"raw_value": raw_value, "raw_unit": raw_unit, "check": "unknown_unit"},
            )
            raise UnitConsistencyViolation(
                f"{city}: raw_unit={raw_unit!r} is not one of 'F', 'C', 'K'"
            )

        month = target_date.month

        # Layer 1 — unit consistency FIRST (catches mislabelled rows before numeric checks)
        self.check_unit_consistency(city, raw_value, raw_unit, declared_unit, target_date=target_date)
        # Layer 2 — physical bounds (TIGGE-derived or lat-band fallback)
        self.check_physical_bounds(city, value_f, month, target_date=target_date)
        # Layer 3 — DELETED (hemisphere-uniform envelope was a category error)
        # Layer 4 — collection timing
        self.check_collection_timing(city, fetch_utc, target_date, peak_hour)
        # Layer 5 — DST boundary
        self.check_dst_boundary(city, local_time)
