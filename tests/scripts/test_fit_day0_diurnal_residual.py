# Created: 2026-09-13
# Authority basis: docs/operations/... diurnal-residual NOAA re-pin (this fix). The
#   fitter's ledger-selection and grid-rounding law must both be pinned by test, since
#   a silent regression here starves training data or misaligns the served histogram
#   cell exactly the way the pre-fix hardcoded WU pin and banker's-rounding grid did.
"""Per-(city, day) settlement-ledger selection and settlement-grid rounding for the
Day0 diurnal-residual fitter (``scripts/fit_day0_diurnal_residual.py``)."""

from __future__ import annotations

import json
import os
import sqlite3
from unittest.mock import patch

import pytest

from scripts.fit_day0_diurnal_residual import (
    MIN_HOURS_ALT,
    MIN_HOURS_WU,
    _hourly_days,
    _write_artifact_atomic,
    build_records,
)

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


def test_short_ogimet_day_falls_back_to_full_wu_history_same_station(tmp_path) -> None:
    """Atlanta, an OGIMET_METAR-era day whose ogimet_metar_katl bucket is short of
    MIN_HOURS_ALT, but whose wu_icao_history bucket for the SAME day is full (>=
    MIN_HOURS_WU) and carries the SAME station_id as city.wu_station (KATL): the
    fitter must fall back to the WU day whole, never mix the two ledgers' hours."""

    day = "2026-08-25"  # OGIMET_METAR era (after the 2026-08-23 effective_date)
    ogimet_short = {h: (80.5 + h * 0.1, 60.0, "F") for h in range(MIN_HOURS_ALT - 5)}
    wu_full = {h: (81.0 + h * 0.1, 60.0, "F") for h in range(MIN_HOURS_WU)}

    world = tmp_path / "world.db"
    rows = _hourly_rows("Atlanta", day, "ogimet_metar_katl", "KATL", ogimet_short)
    rows += _hourly_rows("Atlanta", day, "wu_icao_history", "KATL", wu_full)
    _make_world_db(str(world), rows)

    kept, unit, source_used = _hourly_days(str(world))
    assert source_used[("Atlanta", day)] == "wu_icao_history"
    bucket = kept[("Atlanta", day)]
    assert len(bucket) == MIN_HOURS_WU
    # Every surviving hour comes from the WU bucket's value law (81.0 + 0.1*h), never
    # the ogimet bucket's (80.5 + 0.1*h) -- confirms a whole-bucket swap, not a merge.
    for hour, (hi, _lo) in bucket.items():
        assert hi == 81.0 + hour * 0.1
    assert unit["Atlanta"] == "F"


def test_wu_fallback_refused_when_station_id_does_not_match_city_icao(tmp_path) -> None:
    """Same short-ogimet / full-WU shape as above, but the WU rows carry a DIFFERENT
    station_id than Atlanta's configured settlement ICAO (KATL). This must never be
    treated as a same-station fallback -- the day is dropped entirely, not served
    from the wrong physical station and not mixed with the (also short) ogimet day."""

    day = "2026-08-25"
    ogimet_short = {h: (80.5 + h * 0.1, 60.0, "F") for h in range(MIN_HOURS_ALT - 5)}
    wu_full_wrong_station = {h: (81.0 + h * 0.1, 60.0, "F") for h in range(MIN_HOURS_WU)}

    world = tmp_path / "world.db"
    rows = _hourly_rows("Atlanta", day, "ogimet_metar_katl", "KATL", ogimet_short)
    rows += _hourly_rows(
        "Atlanta", day, "wu_icao_history", "KXYZ", wu_full_wrong_station
    )
    _make_world_db(str(world), rows)

    kept, unit, source_used = _hourly_days(str(world))
    assert ("Atlanta", day) not in kept
    assert ("Atlanta", day) not in source_used
    assert "Atlanta" not in unit


def test_short_ogimet_and_short_wu_day_is_dropped(tmp_path) -> None:
    """Both ledgers present for the day, both short of their own floor: no fallback
    rescues it, and the day contributes no records at all (no cross-ledger merge to
    reach a combined floor)."""

    day = "2026-08-25"
    ogimet_short = {h: (80.5 + h * 0.1, 60.0, "F") for h in range(MIN_HOURS_ALT - 5)}
    wu_short = {h: (81.0 + h * 0.1, 60.0, "F") for h in range(MIN_HOURS_WU - 5)}

    world = tmp_path / "world.db"
    rows = _hourly_rows("Atlanta", day, "ogimet_metar_katl", "KATL", ogimet_short)
    rows += _hourly_rows("Atlanta", day, "wu_icao_history", "KATL", wu_short)
    _make_world_db(str(world), rows)

    kept, unit, source_used = _hourly_days(str(world))
    assert ("Atlanta", day) not in kept
    assert ("Atlanta", day) not in source_used
    assert "Atlanta" not in unit


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


def test_write_artifact_atomic_leaves_prior_artifact_untouched_on_mid_write_failure(
    tmp_path,
) -> None:
    """The daemon-scheduled refit (src/ingest_main.py
    ``_day0_diurnal_residual_refit_tick``) can be killed mid-run; a half-written file
    must never replace the live artifact the loader reads
    (src/calibration/day0_diurnal_residual.py). Simulate a crash mid-``json.dump`` and
    assert the prior artifact's bytes are unchanged: the write went to a ``.tmp``
    sibling and ``os.replace`` (the only thing that can touch the live path) never
    ran because the exception fired first."""

    out_path = tmp_path / "day0_diurnal_residual.json"
    prior_bytes = b'{"fit_date": "2026-09-04", "schema": "day0_diurnal_residual"}'
    out_path.write_bytes(prior_bytes)

    with patch("scripts.fit_day0_diurnal_residual.json.dump", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError, match="boom"):
            _write_artifact_atomic({"fit_date": "2026-09-11"}, str(out_path))

    assert out_path.read_bytes() == prior_bytes


def test_write_artifact_atomic_replaces_prior_artifact_on_success(tmp_path) -> None:
    """A clean write DOES replace the prior artifact, and leaves no ``.tmp`` residue."""

    out_path = tmp_path / "day0_diurnal_residual.json"
    out_path.write_bytes(b'{"fit_date": "2026-09-04"}')

    _write_artifact_atomic({"fit_date": "2026-09-11", "schema": "day0_diurnal_residual"}, str(out_path))

    assert json.loads(out_path.read_text()) == {
        "fit_date": "2026-09-11",
        "schema": "day0_diurnal_residual",
    }
    assert not os.path.exists(f"{out_path}.tmp")


# ------------------------- 2026-09-29: in-q mixture -------------------------


def test_hong_kong_residual_is_gridded_by_truncation(tmp_path) -> None:
    """Hong Kong settles on a truncating grid. A running high of 29.6 with a verified
    final of 29 is D = 0 on that grid; half-up would anchor at 30 and file the row in a
    different cell than the server reads."""

    day = "2026-08-25"
    values = {h: (20.0 + h * 0.4, 15.0, "C") for h in range(22)}
    values[21] = (29.6, 15.0, "C")
    world = tmp_path / "world.db"
    _make_world_db(
        str(world), _hourly_rows("Hong Kong", day, "hko_hourly_accumulator", "HKO", values)
    )
    forecast = tmp_path / "forecast.db"
    _make_forecast_db(str(forecast), [("Hong Kong", day, "high", 29.0, "C")])

    records, _unit, _src = build_records(str(world), str(forecast))
    last = [
        r for r in records if r["city"] == "Hong Kong" and r["metric"] == "high" and r["h"] == 21
    ]
    assert len(last) == 1
    assert last[0]["D"] == 0


def _counts_artifact() -> dict:
    from scripts.fit_day0_diurnal_residual import J_MAX, SCHEMA_VERSION

    counts = [0] * (J_MAX + 1)
    counts[0], counts[1], counts[2] = 50, 35, 15
    return {
        "schema_version": SCHEMA_VERSION, "fit_date": "2026-07-01", "j_max": J_MAX,
        "peak_hours": {"Tel Aviv": 12.0}, "trough_hours": {}, "unit": {"Tel Aviv": "C"},
        "pooled": {"high|C|2": counts}, "city": {}, "weights": {},
    }


def _synthetic_posteriors(w_true: float, n: int) -> list[dict]:
    """Rows whose winner is drawn from the operator at ``w_true``, so the maximum
    likelihood weight must recover it."""

    import random

    from scripts.fit_day0_diurnal_residual import DiurnalResidualNowcast, _city_grid

    rng = random.Random(7)
    bounds = [(None, 29.0), (30.0, 30.0), (31.0, 31.0), (32.0, None)]
    q = [0.0, 0.9, 0.08, 0.02]
    truth = DiurnalResidualNowcast(_counts_artifact()).mixture(
        city="Tel Aviv", metric="high", unit="C", local_hour=10.0, running_extreme=30.0,
        bin_bounds=bounds, round_to_grid=_city_grid("Tel Aviv"), weight=w_true,
    ).apply(q)
    rows = []
    for _ in range(n):
        u, acc, winner = rng.random(), 0.0, len(truth) - 1
        for index, p in enumerate(truth):
            acc += p
            if u <= acc:
                winner = index
                break
        rows.append({
            "pid": 1, "city": "Tel Aviv", "date": "2026-08-01", "metric": "high", "unit": "C",
            "local_hour": 10.0, "running": 30.0, "bounds": bounds, "q": q, "winner": winner,
        })
    return rows


def test_weight_fit_recovers_the_generating_weight() -> None:
    from scripts.fit_day0_diurnal_residual import fit_weight, weight_rows

    cells = weight_rows(_synthetic_posteriors(0.4, 4000), counts=_counts_artifact())
    assert list(cells) == ["high|2"]
    assert fit_weight(cells["high|2"]) == pytest.approx(0.4, abs=0.06)
    # A carrier that is already right earns no weight.
    right = weight_rows(_synthetic_posteriors(0.0, 4000), counts=_counts_artifact())
    assert fit_weight(right["high|2"]) < 0.05


def test_artifact_weights_use_only_the_lagged_window(monkeypatch) -> None:
    """Weights for fit_date T come from posteriors dated [T-31, T-2], scored on counts
    before T-32; a cell under MIN_WEIGHT_ROWS serves no weight."""

    import scripts.fit_day0_diurnal_residual as fit

    captured = {}

    def fake_rows(window, *, counts):
        captured["dates"] = sorted({row["date"] for row in window})
        captured["counts_fit_date"] = counts["fit_date"]
        return {"high|2": [(0.5, 0.5)] * fit.MIN_WEIGHT_ROWS, "high|3": [(0.5, 0.5)] * 3}

    monkeypatch.setattr(fit, "weight_rows", fake_rows)
    records = [{"city": "Tel Aviv", "date": "2026-07-01", "metric": "high", "h": 10,
                "cum": 30.0, "D": 1, "unit": "C"}]
    posteriors = [{"date": d} for d in ("2026-07-30", "2026-07-31", "2026-08-29", "2026-08-30")]
    artifact = fit.build_artifact(
        records, unit={"Tel Aviv": "C"}, fit_date="2026-08-31", posteriors=posteriors
    )

    assert captured["dates"] == ["2026-07-31", "2026-08-29"]
    assert captured["counts_fit_date"] == "2026-07-30"
    assert set(artifact["weights"]) == {"high|2"}
    assert artifact["fit_date"] == "2026-08-31"


def test_pooled_counts_are_keyed_by_settlement_unit() -> None:
    """A Fahrenheit degree and a Celsius degree are different residual grids; the
    pooled histogram must never mix them."""

    from scripts.fit_day0_diurnal_residual import build_counts

    records = [
        {"city": "Tel Aviv", "date": "2026-07-01", "metric": "high", "h": 10,
         "cum": 30.0, "D": 1, "unit": "C"},
        {"city": "Atlanta", "date": "2026-07-01", "metric": "high", "h": 10,
         "cum": 90.0, "D": 3, "unit": "F"},
    ]
    counts = build_counts(records, unit={"Tel Aviv": "C", "Atlanta": "F"}, fit_date="2026-07-02")
    assert counts["peak_hours"] == {"Tel Aviv": 10, "Atlanta": 10}
    assert counts["pooled"]["high|C|0"][1] == 1 and sum(counts["pooled"]["high|C|0"]) == 1
    assert counts["pooled"]["high|F|0"][3] == 1 and sum(counts["pooled"]["high|F|0"]) == 1
