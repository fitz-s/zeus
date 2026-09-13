# Created: 2026-09-13
# Authority basis: docs/operations/... diurnal-residual NOAA re-pin (this fix). The
#   fitter's ledger-selection and grid-rounding law must both be pinned by test, since
#   a silent regression here starves training data or misaligns the served histogram
#   cell exactly the way the pre-fix hardcoded WU pin and banker's-rounding grid did.
"""Per-(city, day) settlement-ledger selection and settlement-grid rounding for the
Day0 diurnal-residual fitter (``scripts/fit_day0_diurnal_residual.py``)."""

from __future__ import annotations

import sqlite3

from scripts.fit_day0_diurnal_residual import _hourly_days, build_records

_OBS_SCHEMA = """
CREATE TABLE observation_instants (
    city TEXT, target_date TEXT, source TEXT, station_id TEXT,
    local_hour REAL, running_max REAL, running_min REAL,
    temp_current REAL, temp_unit TEXT
)
"""
_SETTLE_SCHEMA = """
CREATE TABLE settlement_outcomes (
    city TEXT, target_date TEXT, temperature_metric TEXT, authority TEXT,
    settlement_value REAL, settlement_unit TEXT
)
"""


def _make_world_db(path: str, rows: list[tuple]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(_OBS_SCHEMA)
    conn.executemany(
        "INSERT INTO observation_instants "
        "(city, target_date, source, station_id, local_hour, running_max, "
        " running_min, temp_current, temp_unit) VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def _make_forecast_db(path: str, settlements: list[tuple]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(_SETTLE_SCHEMA)
    conn.executemany(
        "INSERT INTO settlement_outcomes "
        "(city, target_date, temperature_metric, authority, settlement_value, "
        " settlement_unit) VALUES (?,?,?,'VERIFIED',?,?)",
        settlements,
    )
    conn.commit()
    conn.close()


def _hourly_rows(city: str, day: str, source: str, station: str, values: dict) -> list[tuple]:
    """One row per {hour: (high, low)}."""

    return [
        (city, day, source, station, float(hour), hi, lo, None, unit)
        for hour, (hi, lo, unit) in values.items()
    ]


def test_noaa_city_with_only_ogimet_ledger_day_is_kept_and_gridded(tmp_path) -> None:
    """Tel Aviv is OGIMET_METAR-tier from the start of history (no WU era). A day
    with only ``ogimet_metar_llbg`` rows, at floor or above, must be KEPT and its
    cumulative envelope gridded against ``round_wmo_half_up_value``, not dropped for
    lack of a ``wu_icao_history`` bucket that never existed for this city."""

    day = "2026-08-25"
    # 22 hours, native ogimet tenths precision, cumulative high peaks at 29.6.
    values = {h: (20.0 + h * 0.4, 15.0, "C") for h in range(22)}
    values[21] = (29.6, 15.0, "C")
    world = tmp_path / "world.db"
    _make_world_db(str(world), _hourly_rows("Tel Aviv", day, "ogimet_metar_llbg", "LLBG", values))
    forecast = tmp_path / "forecast.db"
    _make_forecast_db(str(forecast), [])

    kept, unit, source_used = _hourly_days(str(world))
    assert ("Tel Aviv", day) in kept
    assert source_used[("Tel Aviv", day)] == "ogimet_metar_llbg"
    assert unit["Tel Aviv"] == "C"

    records, _unit, _src = build_records(str(world), str(forecast))
    last_hour_high = [
        r for r in records if r["city"] == "Tel Aviv" and r["metric"] == "high" and r["h"] == 21
    ]
    assert len(last_hour_high) == 1
    # No VERIFIED settlement -> final falls back to the day's own cumulative extreme
    # (29.6), gridded the same way: round_wmo_half_up(29.6) == 30, so D == 0 at the
    # hour that attains the day's extreme.
    assert last_hour_high[0]["D"] == 0


def test_day_with_both_ledgers_uses_only_the_eras_settlement_ledger(tmp_path) -> None:
    """Atlanta switched WU -> NOAA settlement authority on 2026-08-23. A day AFTER
    that switch with rows in BOTH ``wu_icao_history`` and ``ogimet_metar_katl`` (e.g.
    WU history that had not yet been cut off) must use ONLY the OGIMET_METAR-era
    ledger -- never merge the two envelopes into one day."""

    day = "2026-08-25"  # after the 2026-08-23 effective_date -> OGIMET_METAR era
    # WU rows: whole-degree, easily distinguished from the ogimet tenths below.
    wu_values = {h: (80.0, 60.0, "F") for h in range(22)}
    wu_values[15] = (90.0, 60.0, "F")  # WU-only peak, must NOT appear in the result
    # Ogimet rows: native tenths, era-correct primary, meets the 20h floor.
    ogimet_values = {h: (80.5, 60.5, "F") for h in range(22)}
    ogimet_values[16] = (82.3, 60.5, "F")  # the era-correct peak

    world = tmp_path / "world.db"
    rows = _hourly_rows("Atlanta", day, "wu_icao_history", "KATL", wu_values)
    rows += _hourly_rows("Atlanta", day, "ogimet_metar_katl", "KATL", ogimet_values)
    _make_world_db(str(world), rows)

    kept, unit, source_used = _hourly_days(str(world))
    assert source_used[("Atlanta", day)] == "ogimet_metar_katl"
    bucket = kept[("Atlanta", day)]
    # Every hour's high in the surviving bucket must come from the ogimet ledger
    # (tenths), never the WU whole-degree ledger's 80.0/90.0 values.
    assert all(hi not in (80.0, 90.0) for hi, _lo in bucket.values())
    assert bucket[16][0] == 82.3
    assert unit["Atlanta"] == "F"


def test_ogimet_tenths_cumulative_grids_against_the_served_anchor(tmp_path) -> None:
    """A cumulative high of 78.6 with a VERIFIED final of 79 must grid to j=0 (not 1):
    round_wmo_half_up_value(78.6) == 79, so D = 79 - 79 == 0, matching exactly the
    anchor DiurnalResidualNowcast.bin_probability rounds the running extreme to at
    serve time (src/calibration/day0_diurnal_residual.py)."""

    day = "2026-08-25"
    # Monotonically increasing so the running-max cumulative at the last hour equals
    # that hour's own reading exactly (78.6) -- not swamped by an earlier, higher one.
    values = {h: (60.0 + h, 55.0, "F") for h in range(19)}
    values[19] = (78.6, 55.0, "F")
    world = tmp_path / "world.db"
    _make_world_db(str(world), _hourly_rows("Tel Aviv", day, "ogimet_metar_llbg", "LLBG", values))
    forecast = tmp_path / "forecast.db"
    _make_forecast_db(str(forecast), [("Tel Aviv", day, "high", 79.0, "F")])

    records, _unit, _src = build_records(str(world), str(forecast))
    last_hour = [
        r for r in records if r["city"] == "Tel Aviv" and r["metric"] == "high" and r["h"] == 19
    ]
    assert len(last_hour) == 1
    assert last_hour[0]["D"] == 0
