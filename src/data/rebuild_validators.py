"""K4.5: Per-row content validators for rebuild scripts.

VERIFIED label is a CONTRACT not a STAMP. Every rebuild script must call
these validators BEFORE constructing the output atom.

Validators detect Kelvin and CONVERT (not reject), check unit consistency,
check NaN/Inf, check member count, log to availability_fact on failure.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional
from uuid import uuid4

if TYPE_CHECKING:
    from src.config import City

from src.state.db import log_availability_fact


# ---------------------------------------------------------------------------
# Physical-plausibility constants
# ---------------------------------------------------------------------------

KELVIN_DETECTION_THRESHOLD = 200.0   # Earth coldest ~184 K
MAX_CELSIUS_EARTH = 60.0             # Earth surface record ~56.7 C
MAX_FAHRENHEIT_EARTH = 134.0         # Furnace Creek 1913
MIN_KELVIN_EARTH = 180.0             # conservative
MAX_KELVIN_EARTH = 335.0             # ~62 C surface
MIN_KELVIN_VALID = 180.0             # alias for clarity


# ---------------------------------------------------------------------------
# Exception classes
# ---------------------------------------------------------------------------

class ImpossibleTemperatureError(ValueError):
    """Raised when a temperature value is physically impossible."""

    def __init__(self, raw_value, unit, *, city=None, target_date=None):
        self.raw_value = raw_value
        self.unit = unit
        self.city = city
        self.target_date = target_date
        super().__init__(
            f"Impossible temperature: {raw_value} {unit} for city={city}/{target_date}"
        )


class EnsembleIntegrityError(ValueError):
    """Raised when ensemble member count does not match ECMWF expected count."""

    def __init__(self, actual_count: int, expected_count: int = 51, *, city=None, target_date=None):
        self.actual_count = actual_count
        self.expected_count = expected_count
        self.city = city
        self.target_date = target_date
        super().__init__(
            f"Ensemble integrity error: got {actual_count} members, "
            f"expected {expected_count} for city={city}/{target_date}"
        )


class UnknownUnitError(ValueError):
    """Raised when unit string is not F/C/K."""

    def __init__(self, unit, *, city=None, target_date=None):
        self.unit = unit
        self.city = city
        self.target_date = target_date
        super().__init__(
            f"Unknown temperature unit: {unit!r} for city={city}/{target_date}"
        )


# ---------------------------------------------------------------------------
# Availability fact logging (best-effort)
# ---------------------------------------------------------------------------

def _log_validator_failure(
    conn: sqlite3.Connection,
    city,
    target_date: Optional[str],
    violation: Exception,
    raw_value,
    unit: str,
    details_extra: Optional[dict] = None,
) -> None:
    """Best-effort log to availability_fact. Swallows DB errors, never blocks the validator."""
    try:
        details = {
            "violation": str(violation),
            "raw_value": raw_value,
            "unit": unit,
        }
        if details_extra:
            details.update(details_extra)
        city_name = getattr(city, "name", str(city))
        now_iso = datetime.now(timezone.utc).isoformat()
        log_availability_fact(
            conn,
            availability_id=str(uuid4()),
            scope_type="city_target",
            scope_key=f"{city_name}/{target_date}" if target_date else city_name,
            failure_type=violation.__class__.__name__,
            started_at=now_iso,
            ended_at=now_iso,
            impact="block",
            details=details,  # NOT details_json — log_availability_fact serializes internally
        )
    except Exception:
        pass  # observability is best-effort


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _kelvin_to_celsius(k: float) -> float:
    return k - 273.15


def _kelvin_to_fahrenheit(k: float) -> float:
    return (k - 273.15) * 9.0 / 5.0 + 32.0


def _celsius_to_fahrenheit(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def _fahrenheit_to_celsius(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


def _validate_converted_value(value: float, settlement_unit: str, city, target_date) -> None:
    """Hard-reject converted values outside physically possible Earth surface range."""
    if not math.isfinite(value):
        raise ImpossibleTemperatureError(
            value, settlement_unit, city=getattr(city, "name", city), target_date=target_date
        )
    if settlement_unit == "C" and value > MAX_CELSIUS_EARTH:
        raise ImpossibleTemperatureError(
            value, settlement_unit, city=getattr(city, "name", city), target_date=target_date
        )
    if settlement_unit == "F" and value > MAX_FAHRENHEIT_EARTH:
        raise ImpossibleTemperatureError(
            value, settlement_unit, city=getattr(city, "name", city), target_date=target_date
        )


def _convert_single_value(raw: float, settlement_unit: str, city, target_date) -> float:
    """
    Convert raw value to settlement_unit.

    Detection logic:
    - If raw > KELVIN_DETECTION_THRESHOLD (200.0): treat as Kelvin.
      - Plausible Kelvin range: [MIN_KELVIN_VALID, MAX_KELVIN_EARTH] == [180, 335].
      - Outside that range: ImpossibleTemperatureError.
    - Dead zone for Celsius cities: raw > MAX_CELSIUS_EARTH AND raw < 200 -> impossible.
    - Dead zone for Fahrenheit cities: raw > MAX_FAHRENHEIT_EARTH AND raw < 200 -> impossible.
    """
    if not math.isfinite(raw):
        raise ImpossibleTemperatureError(
            raw, settlement_unit, city=getattr(city, "name", city), target_date=target_date
        )

    city_name = getattr(city, "name", city)

    if raw > KELVIN_DETECTION_THRESHOLD:
        # Treat as Kelvin
        if raw < MIN_KELVIN_VALID or raw > MAX_KELVIN_EARTH:
            raise ImpossibleTemperatureError(
                raw, "K", city=city_name, target_date=target_date
            )
        if settlement_unit == "C":
            return _kelvin_to_celsius(raw)
        else:  # "F"
            return _kelvin_to_fahrenheit(raw)
    else:
        # Raw value is in (or claimed to be in) native unit
        if settlement_unit == "C":
            if raw > MAX_CELSIUS_EARTH:
                # Dead zone: above max Celsius but not Kelvin
                raise ImpossibleTemperatureError(
                    raw, settlement_unit, city=city_name, target_date=target_date
                )
        elif settlement_unit == "F":
            if raw > MAX_FAHRENHEIT_EARTH:
                # Dead zone: above max Fahrenheit but not Kelvin
                raise ImpossibleTemperatureError(
                    raw, settlement_unit, city=city_name, target_date=target_date
                )
        return raw


# ---------------------------------------------------------------------------
# Validator function 1 — for ensemble snapshots
# ---------------------------------------------------------------------------

ECMWF_ENSEMBLE_MEMBER_COUNT = 51


def validate_ensemble_snapshot_for_calibration(
    members: list[dict],
    city: "City",
    conn: sqlite3.Connection,
    *,
    target_date: Optional[str] = None,
) -> list[float]:
    """
    Validates and converts ECMWF ensemble member values.

    - Asserts len(members) == 51 (ECMWF ensemble count). Raises EnsembleIntegrityError if not.
    - Extracts value_native_unit from each member dict.
    - Detects Kelvin via threshold (any value > 200.0 -> treat as Kelvin).
    - Converts Kelvin to city.settlement_unit:
      - Celsius cities: subtract 273.15
      - Fahrenheit cities: (v - 273.15) * 9/5 + 32
    - Hard-rejects values outside physically possible ranges.
    - Asserts no NaN/Inf in final converted values.
    - On any failure: log to availability_fact via _log_validator_failure() and re-raise.

    Returns: list[float] in city.settlement_unit.
    """
    city_name = getattr(city, "name", city)

    if len(members) != ECMWF_ENSEMBLE_MEMBER_COUNT:
        err = EnsembleIntegrityError(
            len(members), ECMWF_ENSEMBLE_MEMBER_COUNT,
            city=city_name, target_date=target_date,
        )
        _log_validator_failure(
            conn, city, target_date, err,
            raw_value=len(members), unit="count",
            details_extra={"expected_count": ECMWF_ENSEMBLE_MEMBER_COUNT},
        )
        raise err

    converted: list[float] = []
    for i, member in enumerate(members):
        if isinstance(member, dict):
            raw = float(member.get("value_native_unit", member.get("value", float("nan"))))
        else:
            raw = float(member)

        try:
            val = _convert_single_value(raw, city.settlement_unit, city, target_date)
        except ImpossibleTemperatureError as err:
            _log_validator_failure(
                conn, city, target_date, err,
                raw_value=raw, unit="native",
                details_extra={"member_index": i},
            )
            raise

        if not math.isfinite(val):
            err = ImpossibleTemperatureError(
                val, city.settlement_unit, city=city_name, target_date=target_date
            )
            _log_validator_failure(
                conn, city, target_date, err,
                raw_value=raw, unit=city.settlement_unit,
                details_extra={"member_index": i, "reason": "NaN/Inf after conversion"},
            )
            raise err

        converted.append(val)

    return converted


# ---------------------------------------------------------------------------
# Validator function 2 — for observations
# ---------------------------------------------------------------------------

def validate_observation_for_settlement(
    obs_row: dict,
    city: "City",
    conn: sqlite3.Connection,
) -> float:
    """
    Validates and converts a single observation row.

    - Cross-checks obs_row['unit'] vs city.settlement_unit.
      - If mismatched and convertible (F<->C), converts.
      - If obs_row['unit'] == 'K' or value > KELVIN_DETECTION_THRESHOLD, treats as Kelvin.
      - If unknown unit (not F/C/K), raises UnknownUnitError.
    - Hard-rejects impossible values (dead zone logic).
    - On failure: log to availability_fact and raise.

    Returns: float in city.settlement_unit.
    """
    raw_value = float(obs_row["high_temp"])
    obs_unit = str(obs_row.get("unit", "")).upper().strip()
    city_name = getattr(city, "name", city)
    target_date = obs_row.get("target_date")

    if not math.isfinite(raw_value):
        err = ImpossibleTemperatureError(
            raw_value, obs_unit, city=city_name, target_date=target_date
        )
        _log_validator_failure(conn, city, target_date, err, raw_value, obs_unit)
        raise err

    # Determine effective unit (Kelvin detection takes priority)
    if obs_unit == "K" or raw_value > KELVIN_DETECTION_THRESHOLD:
        # Treat as Kelvin, validate plausible range
        if raw_value > KELVIN_DETECTION_THRESHOLD:
            kelvin_val = raw_value
        else:
            kelvin_val = raw_value  # unit was explicitly 'K'

        if kelvin_val < MIN_KELVIN_VALID or kelvin_val > MAX_KELVIN_EARTH:
            err = ImpossibleTemperatureError(
                kelvin_val, "K", city=city_name, target_date=target_date
            )
            _log_validator_failure(conn, city, target_date, err, kelvin_val, "K")
            raise err

        if city.settlement_unit == "C":
            converted = _kelvin_to_celsius(kelvin_val)
        elif city.settlement_unit == "F":
            converted = _kelvin_to_fahrenheit(kelvin_val)
        else:
            err = UnknownUnitError(city.settlement_unit, city=city_name, target_date=target_date)
            _log_validator_failure(conn, city, target_date, err, raw_value, obs_unit)
            raise err

    elif obs_unit not in ("F", "C"):
        err = UnknownUnitError(obs_unit, city=city_name, target_date=target_date)
        _log_validator_failure(conn, city, target_date, err, raw_value, obs_unit)
        raise err

    elif obs_unit == city.settlement_unit:
        # Units match — validate range
        converted = raw_value
        if city.settlement_unit == "C" and raw_value > MAX_CELSIUS_EARTH:
            err = ImpossibleTemperatureError(
                raw_value, obs_unit, city=city_name, target_date=target_date
            )
            _log_validator_failure(conn, city, target_date, err, raw_value, obs_unit)
            raise err
        if city.settlement_unit == "F" and raw_value > MAX_FAHRENHEIT_EARTH:
            err = ImpossibleTemperatureError(
                raw_value, obs_unit, city=city_name, target_date=target_date
            )
            _log_validator_failure(conn, city, target_date, err, raw_value, obs_unit)
            raise err

    else:
        # Units mismatch but both are F or C — convert
        if obs_unit == "F" and city.settlement_unit == "C":
            converted = _fahrenheit_to_celsius(raw_value)
        elif obs_unit == "C" and city.settlement_unit == "F":
            converted = _celsius_to_fahrenheit(raw_value)
        else:
            err = UnknownUnitError(obs_unit, city=city_name, target_date=target_date)
            _log_validator_failure(conn, city, target_date, err, raw_value, obs_unit)
            raise err

        # Validate converted value
        try:
            _validate_converted_value(converted, city.settlement_unit, city, target_date)
        except ImpossibleTemperatureError as err:
            _log_validator_failure(conn, city, target_date, err, raw_value, obs_unit)
            raise

    return converted
