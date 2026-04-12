"""5-layer write-time IngestionGuard for temperature observations.

Any layer failure raises a typed IngestionRejected subclass, making the
corresponding ObservationAtom unconstructable.

Layer order (executed by validate()):
  1. check_unit_consistency   — Earth record cross-check + city.settlement_unit cross-check
  2. check_physical_bounds    — TIGGE p01/p99 lookup (lat-band fallback if null)
  3. check_seasonal_plausibility — hemisphere-aware monthly envelope
  4. check_collection_timing  — fetch must be after local peak_hour on target_date
  5. check_dst_boundary       — spring-forward missing hours are rejected

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


class SeasonalPlausibilityViolation(IngestionRejected):
    """Layer 3: value outside hemisphere-aware seasonal envelope for city/month."""


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
        except Exception:
            pass  # Observability is best-effort; never block the guard itself

    # ------------------------------------------------------------------
    # Layer 1 — unit consistency
    # ------------------------------------------------------------------

    def check_unit_consistency(
        self,
        city: str,
        raw_value: float,
        raw_unit: str,
        declared_unit: str,
    ) -> None:
        """Layer 1: detect unit mislabelling via Earth temperature records and city config.

        Two sub-checks (both run):

        a) Cross-check declared_unit against city.settlement_unit from config.
           Catches the Buenos Aires / Hong Kong / Toronto class of bug where
           raw F values are labelled with wrong unit for a C city.

        b) Earth-record absolute bounds on raw_value × raw_unit.
           Catches the Houston 160°F and Lagos 89°C class of bug.
        """
        today = date.today()
        city_obj = cities_by_name.get(city)
        # Sub-check a: city.settlement_unit cross-check
        if city_obj is not None and declared_unit != city_obj.settlement_unit:
            type(self)._increment_rejected()
            self._log_availability_failure(city, today, "UnitConsistencyViolation", {"declared_unit": declared_unit, "settlement_unit": city_obj.settlement_unit})
            raise UnitConsistencyViolation(
                f"{city}: declared unit={declared_unit!r} but city.settlement_unit="
                f"{city_obj.settlement_unit!r} (config/cities.json)"
            )
        # Sub-check b: Earth record bounds
        if raw_unit == "C":
            if raw_value > 56.7:
                type(self)._increment_rejected()
                self._log_availability_failure(city, today, "UnitConsistencyViolation", {"raw_value": raw_value, "raw_unit": raw_unit, "check": "earth_record_high_C"})
                raise UnitConsistencyViolation(
                    f"{city}: {raw_value}\u00b0C exceeds Earth record 56.7\u00b0C — "
                    f"likely mislabelled \u00b0F value"
                )
            if raw_value < -89.2:
                type(self)._increment_rejected()
                self._log_availability_failure(city, today, "UnitConsistencyViolation", {"raw_value": raw_value, "raw_unit": raw_unit, "check": "earth_record_low_C"})
                raise UnitConsistencyViolation(
                    f"{city}: {raw_value}\u00b0C below Earth record -89.2\u00b0C"
                )
        elif raw_unit == "F":
            if raw_value > 134.0:
                type(self)._increment_rejected()
                self._log_availability_failure(city, today, "UnitConsistencyViolation", {"raw_value": raw_value, "raw_unit": raw_unit, "check": "earth_record_high_F"})
                raise UnitConsistencyViolation(
                    f"{city}: {raw_value}\u00b0F exceeds Earth record 134\u00b0F"
                )
            if raw_value < -128.6:
                type(self)._increment_rejected()
                self._log_availability_failure(city, today, "UnitConsistencyViolation", {"raw_value": raw_value, "raw_unit": raw_unit, "check": "earth_record_low_F"})
                raise UnitConsistencyViolation(
                    f"{city}: {raw_value}\u00b0F below Earth record -128.6\u00b0F"
                )

    # ------------------------------------------------------------------
    # Layer 2 — physical bounds (TIGGE-derived or lat-band fallback)
    # ------------------------------------------------------------------

    def check_physical_bounds(self, city: str, value_f: float, month: int) -> None:
        """Layer 2: value within p01-2σ … p99+2σ for city/month.

        Falls back to latitude-band heuristic when TIGGE bounds are null or
        absent (sample_count < 30, or city not in city_monthly_bounds.json).
        """
        city_data = self._bounds.get(city)
        if city_data is None:
            return self._check_lat_band_bounds(city, value_f, month)
        month_entry = city_data.get(str(month))
        if month_entry is None:
            return self._check_lat_band_bounds(city, value_f, month)

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
            self._log_availability_failure(city, date.today(), "PhysicalBoundsViolation", {"value_f": value_f, "lower": lower, "upper": upper, "month": month, "path": "tigge"})
            raise PhysicalBoundsViolation(
                f"{city} month={month}: {value_f}\u00b0F outside "
                f"[{lower:.1f}, {upper:.1f}]\u00b0F "
                f"(TIGGE p01={month_entry['p01']}, p99={month_entry['p99']} "
                f"unit={month_entry['unit']})"
            )

    def _check_lat_band_bounds(self, city: str, value_f: float, month: int) -> None:
        """Fallback: crude climatological bounds by absolute latitude band (°F)."""
        city_obj = cities_by_name.get(city)
        if city_obj is None:
            return  # unknown city — cannot validate; pass through
        abs_lat = abs(city_obj.lat)
        if abs_lat < 23.5:      # tropical
            lower, upper = 50.0, 110.0
        elif abs_lat < 45:      # temperate
            lower, upper = -20.0, 120.0
        else:                   # high-latitude / sub-polar
            lower, upper = -60.0, 105.0
        if not (lower <= value_f <= upper):
            type(self)._increment_rejected()
            self._log_availability_failure(city, date.today(), "PhysicalBoundsViolation", {"value_f": value_f, "lower": lower, "upper": upper, "month": month, "path": "lat_band"})
            raise PhysicalBoundsViolation(
                f"{city} month={month}: {value_f}\u00b0F outside lat-band "
                f"[{lower}, {upper}]\u00b0F (|lat|={abs_lat:.1f}\u00b0, fallback path)"
            )

    # ------------------------------------------------------------------
    # Layer 3 — seasonal plausibility (hemisphere-aware)
    # ------------------------------------------------------------------

    #: Northern hemisphere monthly envelope (°F, generous bounds).
    _N_ENVELOPE: dict[int, tuple[float, float]] = {
        1: (-20.0, 90.0),  2: (-15.0, 92.0),  3: (-10.0, 95.0),
        4: (0.0, 100.0),   5: (20.0, 105.0),  6: (35.0, 112.0),
        7: (45.0, 115.0),  8: (45.0, 115.0),  9: (30.0, 110.0),
        10: (10.0, 100.0), 11: (-5.0, 92.0),  12: (-15.0, 88.0),
    }

    #: Southern hemisphere monthly envelope (°F) — months are flipped seasons.
    _S_ENVELOPE: dict[int, tuple[float, float]] = {
        1: (35.0, 112.0),  2: (35.0, 112.0),  3: (25.0, 105.0),
        4: (15.0, 95.0),   5: (5.0, 85.0),    6: (-5.0, 78.0),
        7: (-10.0, 75.0),  8: (-5.0, 78.0),   9: (5.0, 85.0),
        10: (15.0, 95.0),  11: (25.0, 105.0), 12: (35.0, 110.0),
    }

    def check_seasonal_plausibility(
        self,
        city: str,
        value_f: float,
        month: int,
        hemisphere: str,
    ) -> None:
        """Layer 3: hemisphere-aware seasonal envelope check (conservative fallback).

        This layer is intentionally broad — city-specific precision comes from
        Layer 2.  It catches gross seasonal inversions (e.g. Wellington -40°C
        in July labelled as southern hemisphere but layer 2 fallback allowed it).
        """
        envelope = self._N_ENVELOPE if hemisphere == "N" else self._S_ENVELOPE
        lower, upper = envelope[month]
        if not (lower <= value_f <= upper):
            type(self)._increment_rejected()
            self._log_availability_failure(city, date.today(), "SeasonalPlausibilityViolation", {"value_f": value_f, "lower": lower, "upper": upper, "month": month, "hemisphere": hemisphere})
            raise SeasonalPlausibilityViolation(
                f"{city} (hemisphere={hemisphere!r}) month={month}: "
                f"{value_f}\u00b0F outside seasonal envelope [{lower}, {upper}]\u00b0F"
            )

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
            return
        tz = ZoneInfo(city_obj.timezone)
        fetch_local = fetch_utc.astimezone(tz)
        fetch_date_local = fetch_local.date()
        if fetch_date_local == target_date:
            if fetch_local.hour < peak_hour:
                type(self)._increment_rejected()
                self._log_availability_failure(city, target_date, "CollectionTimingViolation", {"fetch_local_hour": fetch_local.hour, "peak_hour": peak_hour, "target_date": target_date.isoformat()})
                raise CollectionTimingViolation(
                    f"{city}: fetched at local hour {fetch_local.hour:02d}:00 on "
                    f"{target_date}, but peak_hour={peak_hour} — daily max not yet observed"
                )
        elif fetch_date_local < target_date:
            type(self)._increment_rejected()
            self._log_availability_failure(city, target_date, "CollectionTimingViolation", {"fetch_date_local": fetch_date_local.isoformat(), "target_date": target_date.isoformat()})
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
            return
        tz = ZoneInfo(city_obj.timezone)
        from src.signal.diurnal import _is_missing_local_hour  # avoid circular at module load
        if _is_missing_local_hour(local_time, tz):
            type(self)._increment_rejected()
            _td = local_time.date() if hasattr(local_time, 'date') else date.today()
            self._log_availability_failure(city, _td, "DstBoundaryViolation", {"local_time": local_time.isoformat() if hasattr(local_time, 'isoformat') else str(local_time), "timezone": city_obj.timezone})
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
        declared_unit = city_obj.settlement_unit if city_obj is not None else raw_unit

        # Convert raw to °F for layers 2 & 3
        if raw_unit == "C":
            value_f = raw_value * 9 / 5 + 32
        elif raw_unit == "K":
            value_f = (raw_value - 273.15) * 9 / 5 + 32
        else:  # "F"
            value_f = raw_value

        month = target_date.month

        # Layer 1 — unit consistency FIRST (catches mislabelled rows before numeric checks)
        self.check_unit_consistency(city, raw_value, raw_unit, declared_unit)
        # Layer 2 — physical bounds (TIGGE-derived or lat-band fallback)
        self.check_physical_bounds(city, value_f, month)
        # Layer 3 — seasonal plausibility
        self.check_seasonal_plausibility(city, value_f, month, hemisphere)
        # Layer 4 — collection timing
        self.check_collection_timing(city, fetch_utc, target_date, peak_hour)
        # Layer 5 — DST boundary
        self.check_dst_boundary(city, local_time)
