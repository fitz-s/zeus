# Created: 2026-09-04
# Last reused or audited: 2026-09-23
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   items 26-32 — served-center de-bias, live wiring; current-resolver
#   settlement and point-in-time forecast eligibility verified 2026-09-23.
"""Tests for src/calibration/center_debias_live_fit.py.

Two things decide whether this module is safe on the money path: that the shift
it returns is the empirical-Bayes estimate it claims to be, and that every case
it cannot serve degrades to None instead of a guess. These pin both, plus the
walk-forward exclusion and the cross-process determinism of the window cutoff.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.calibration import center_debias_live_fit as mod
from src.calibration.center_debias_live_fit import (
    MAX_ABS_SHIFT_C,
    CenterDebiasFitProvider,
    fit,
    load_residual_rows,
    window_cutoff,
)

NOW = datetime(2027, 6, 4, 3, 0, tzinfo=timezone.utc)
CUTOFF = "2027-06-04T00:00:00Z"


def _cities():
    return {"Chicago": SimpleNamespace(
        settlement_source_type="noaa", previous_settlement_source_type="wu_icao",
        settlement_source_type_effective_date="2026-08-23", wu_station="KORD",
        settlement_unit="F", settlement_page_view="hourly", timezone="America/Chicago",
    )}


@pytest.fixture(autouse=True)
def _city_contract(monkeypatch):
    monkeypatch.setattr(mod, "runtime_cities_by_name", _cities)


def _rows(spec: dict[str, list[float]]) -> list[tuple[str, float]]:
    return [(city, value) for city, values in spec.items() for value in values]


def _balanced(mean: float, count: int, spread: float) -> list[float]:
    """``count`` values whose mean is exactly ``mean``, symmetric about it."""

    half = count // 2
    values = [mean + spread * (i + 1) for i in range(half)]
    values += [mean - spread * (i + 1) for i in range(half)]
    if count % 2:
        values.append(mean)
    return values


def _memory_db(
    posteriors: list[dict], settlements: list[dict]
) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            runtime_layer TEXT NOT NULL DEFAULT 'live',
            computed_at TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            provenance_json TEXT NOT NULL DEFAULT '{}',
            -- Mirrors production (v2_schema._ensure_forecast_posteriors_bundle_identity):
            -- the fit reads these as columns, not out of the blob. A fixture that
            -- omits them passes against SQL production cannot run.
            q_shape TEXT GENERATED ALWAYS AS (
                json_extract(provenance_json, '$.q_shape')
            ) VIRTUAL,
            anchor_value_c REAL GENERATED ALWAYS AS (
                json_extract(provenance_json, '$.anchor_value_c')
            ) VIRTUAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE settlement_outcomes (
            settlement_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            winning_bin TEXT,
            settlement_value REAL,
            settlement_unit TEXT,
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED',
            settled_at TEXT,
            settlement_source TEXT, provenance_json TEXT, recorded_at TEXT,
            outcome_type INTEGER, resolution_state TEXT
        )
        """
    )
    conn.execute("""CREATE TABLE observations (
        id INTEGER PRIMARY KEY, city TEXT, target_date TEXT, source TEXT,
        station_id TEXT, unit TEXT, data_source_version TEXT,
        high_temp REAL, low_temp REAL, high_fetch_utc TEXT, low_fetch_utc TEXT,
        high_provenance_metadata TEXT, low_provenance_metadata TEXT
    )""")
    for outcome_id, settlement in enumerate(settlements, 1):
        provenance = json.loads(settlement["provenance_json"])
        provenance["obs_id"] = outcome_id
        settlement["provenance_json"] = json.dumps(provenance)
    conn.executemany(
        """
        INSERT INTO forecast_posteriors (
            city, target_date, temperature_metric, runtime_layer,
            computed_at, recorded_at, provenance_json
        ) VALUES (:city, :target_date, :metric, :runtime_layer,
                  :computed_at, :recorded_at, :provenance_json)
        """,
        posteriors,
    )
    conn.executemany(
        """
        INSERT INTO settlement_outcomes (
            city, target_date, temperature_metric, winning_bin, settlement_value,
            settlement_unit, authority, settled_at, settlement_source, provenance_json, recorded_at
        ) VALUES (
            :city, :target_date, :metric, '70°F', :settlement_value, :settlement_unit,
            :authority, :settled_at, :settlement_source, :provenance_json, :recorded_at
        )
        """,
        settlements,
    )
    meta = json.dumps({"settlement_page_view": "hourly", "station": "KORD"})
    conn.executemany("""INSERT INTO observations (
        id, city, target_date, source, station_id, unit, data_source_version,
        high_temp, low_temp, high_fetch_utc, low_fetch_utc,
        high_provenance_metadata, low_provenance_metadata
    ) VALUES (?, ?, ?, 'noaa_wrh_kord', 'KORD', 'F', 'noaa_wrh_timeseries_v1',
              ?, NULL, ?, NULL, ?, ?)""", [
        (i, row["city"], row["target_date"], row["settlement_value"],
         row["observation_fetched_at"], meta, meta)
        for i, row in enumerate(settlements, 1)
    ])
    conn.commit()
    return conn


def _cell(
    index: int,
    *,
    center_c: float,
    settled_c: float,
    city: str = "Chicago",
    settled_at: str | None = None,
    q_shape: str = "fused_normal_direct",
    authority: str = "VERIFIED",
) -> tuple[dict, dict]:
    """One (posterior, settlement) pair at lead 1 with a distinct target_date.

    ``index`` walks real calendar days so every cell is its own
    (city, target_date, lead) group — a repeated target_date would be deduped
    down to one row and quietly shrink the sample under test.
    """

    decision_day = date(2026, 8, 22) + timedelta(days=index)
    target_date = (decision_day + timedelta(days=1)).isoformat()
    resolved = settled_at or f"{(decision_day + timedelta(days=2)).isoformat()}T06:00:00+00:00"
    observation_id = index + 1
    posterior = {
        "city": city,
        "target_date": target_date,
        "metric": "high",
        "runtime_layer": "live",
        # lead 1: computed the calendar day before the target date.
        "computed_at": f"{decision_day.isoformat()}T12:00:00+00:00",
        "recorded_at": f"{decision_day.isoformat()}T12:01:00+00:00",
        "provenance_json": json.dumps(
            {"q_shape": q_shape, "anchor_value_c": float(center_c)}
        ),
    }
    settlement = {
        "city": city,
        "target_date": target_date,
        "metric": "high",
        "settlement_value": float(settled_c),
        "settlement_unit": "F",
        "authority": authority,
        "settled_at": resolved,
        "recorded_at": resolved,
        "observation_fetched_at": resolved,
        "settlement_source": "https://www.weather.gov/wrh/timeseries?site=KORD",
        "provenance_json": json.dumps({
            "obs_id": observation_id, "era": "internal_resolver_post_2026_02_21",
            "era_start_date_utc": "2026-02-21", "source_family": "NOAA",
            "settlement_source_type": "NOAA", "rounding_rule": "wmo_half_up",
            "obs_source": "noaa_wrh_kord", "data_version": "noaa_wrh_timeseries_v1",
        }),
    }
    return posterior, settlement


def _db_with_residual(
    count: int, *, residual: float, city: str = "Chicago", **kwargs
) -> sqlite3.Connection:
    posteriors, settlements = [], []
    for index in range(count):
        posterior, settlement = _cell(
            index, center_c=(70.0 - 32.0) * 5.0 / 9.0 - residual,
            settled_c=70.0, city=city, **kwargs
        )
        posteriors.append(posterior)
        settlements.append(settlement)
    return _memory_db(posteriors, settlements)


# --- the EB math ------------------------------------------------------------


def test_eb_recovers_known_per_city_offsets():
    """A well-measured city keeps its own mean; a thin one collapses to the pool."""

    rows = _rows(
        {
            "Guangzhou": _balanced(1.60, 90, 0.02),
            "Chicago": _balanced(-0.80, 90, 0.02),
            "Seoul": _balanced(1.30, 90, 0.02),
            "Milan": _balanced(-0.50, 90, 0.02),
            # n >= N_MIN but noisy: its own mean is barely trusted.
            "Jinan": _balanced(3.00, 10, 3.0),
        }
    )

    artifact = fit(rows, metric="high", training_cutoff=CUTOFF)

    assert artifact.n_cities_activated == 5
    for city, expected in (
        ("Guangzhou", 1.60),
        ("Chicago", -0.80),
        ("Seoul", 1.30),
        ("Milan", -0.50),
    ):
        assert artifact.by_city[city] == pytest.approx(expected, abs=0.02)
    # Noisy city: pulled far off its own +3.00 mean toward the pool.
    assert artifact.by_city["Jinan"] < 2.0
    assert artifact.by_city["Jinan"] > artifact.global_mean


def test_city_below_threshold_takes_the_global_mean():
    rows = _rows(
        {
            "Guangzhou": _balanced(1.60, 90, 0.02),
            "Chicago": _balanced(-0.80, 90, 0.02),
            "Zhengzhou": [5.0],  # n = 1 < N_MIN_CITY
        }
    )

    artifact = fit(rows, metric="high", training_cutoff=CUTOFF)

    assert artifact.n_cities_activated == 2
    assert artifact.by_city["Zhengzhou"] == pytest.approx(artifact.global_mean)
    assert artifact.by_city["Zhengzhou"] != pytest.approx(5.0)


def test_unfitted_city_falls_back_to_the_global_mean():
    rows = _rows({"Guangzhou": _balanced(1.60, 90, 0.02)})

    artifact = fit(rows, metric="high", training_cutoff=CUTOFF)

    assert artifact.shift_for("NeverSeen") == pytest.approx(artifact.global_mean)


def test_absurd_shift_is_clamped_and_warned(caplog: pytest.LogCaptureFixture):
    rows = _rows(
        {
            "Broken": _balanced(9.0, 90, 0.02),
            "Normal": _balanced(0.2, 90, 0.02),
            "AlsoNormal": _balanced(0.3, 90, 0.02),
        }
    )

    with caplog.at_level(logging.WARNING, logger="zeus.center_debias_live_fit"):
        artifact = fit(rows, metric="high", training_cutoff=CUTOFF)

    assert artifact.by_city["Broken"] == pytest.approx(MAX_ABS_SHIFT_C)
    assert any("clamped" in record.message for record in caplog.records)


def test_param_hash_tracks_the_fitted_values():
    base = _rows({"A": _balanced(1.0, 90, 0.02), "B": _balanced(-1.0, 90, 0.02)})
    moved = _rows({"A": _balanced(1.5, 90, 0.02), "B": _balanced(-1.0, 90, 0.02)})

    assert (
        fit(base, metric="high", training_cutoff=CUTOFF).param_hash
        == fit(base, metric="high", training_cutoff=CUTOFF).param_hash
    )
    assert (
        fit(base, metric="high", training_cutoff=CUTOFF).param_hash
        != fit(moved, metric="high", training_cutoff=CUTOFF).param_hash
    )


def test_artifact_is_frozen():
    artifact = fit(
        _rows({"A": _balanced(1.0, 90, 0.02)}), metric="high", training_cutoff=CUTOFF
    )

    with pytest.raises(AttributeError):
        artifact.global_mean = 0.0


# --- row extraction ---------------------------------------------------------


def test_load_residual_rows_computes_settled_minus_center():
    conn = _db_with_residual(3, residual=0.75)

    rows = load_residual_rows(conn, metric="high", training_cutoff=CUTOFF)

    assert len(rows) == 3
    assert all(city == "Chicago" for city, _ in rows)
    assert all(value == pytest.approx(0.75) for _, value in rows)


def test_fahrenheit_settlements_convert_to_celsius():
    posterior, settlement = _cell(0, center_c=0.0, settled_c=0.0)
    settlement["settlement_value"] = 32.0
    settlement["settlement_unit"] = "F"
    conn = _memory_db([posterior], [settlement])

    rows = load_residual_rows(conn, metric="high", training_cutoff=CUTOFF)

    assert rows == [("Chicago", pytest.approx(0.0))]


def test_rows_settling_after_the_cutoff_never_train():
    """The walk-forward law: an outcome that had not resolved cannot inform."""

    conn = _db_with_residual(
        3, residual=0.75, settled_at="2027-06-04T06:00:00+00:00"
    )

    assert load_residual_rows(conn, metric="high", training_cutoff=CUTOFF) == []


def test_negative_offset_row_settling_after_cutoff_is_excluded():
    """A -05:00 row must compare on its true UTC instant, not local wall time.

    Local wall clock ``23:30:00`` reads as "before" the cutoff's ``00:00:00``
    prefix, but the true UTC instant (``04:30:00Z`` the next day) is AFTER
    the cutoff and must not train — the walk-forward law this module exists
    to enforce.
    """

    posterior, settlement = _cell(
        0, center_c=20.0, settled_c=70.0, settled_at="2027-06-03T23:30:00-05:00"
    )
    conn = _memory_db([posterior], [settlement])

    assert load_residual_rows(conn, metric="high", training_cutoff=CUTOFF) == []


def test_positive_offset_row_settling_before_cutoff_is_included():
    """A +08:00 row must compare on its true UTC instant, not local wall time.

    Local wall clock ``07:00:00`` on the cutoff's own calendar day reads as
    "after" the ``00:00:00`` prefix, but the true UTC instant
    (``23:00:00Z`` the prior day) settled well before the cutoff and must
    train.
    """

    posterior, settlement = _cell(
        0, center_c=(70 - 32) * 5 / 9 - 0.75,
        settled_c=70.0, settled_at="2027-06-04T07:00:00+08:00"
    )
    conn = _memory_db([posterior], [settlement])

    rows = load_residual_rows(conn, metric="high", training_cutoff=CUTOFF)

    assert rows == [("Chicago", pytest.approx(0.75))]


def test_unverified_settlements_never_train():
    conn = _db_with_residual(3, residual=0.75, authority="DISPUTED")

    assert load_residual_rows(conn, metric="high", training_cutoff=CUTOFF) == []


def test_day0_shapes_never_train():
    conn = _db_with_residual(
        3, residual=0.75, q_shape="fused_day0_conditioned_normal"
    )

    assert load_residual_rows(conn, metric="high", training_cutoff=CUTOFF) == []


def test_only_the_last_posterior_of_a_lead_day_trains():
    """The decision proxy is the lead day's FINAL posterior, whatever its shape."""

    early, settlement = _cell(0, center_c=20.0, settled_c=21.0)
    late = dict(early)
    late["computed_at"] = early["computed_at"].replace("T12:", "T21:")
    late["provenance_json"] = (
        '{"q_shape": "fused_day0_conditioned_normal", "anchor_value_c": 20.0}'
    )
    conn = _memory_db([early, late], [settlement])

    # The day's winner is the day0-conditioned row, which the shape filter then
    # drops — the earlier pre-day0 row does NOT get promoted in its place.
    assert load_residual_rows(conn, metric="high", training_cutoff=CUTOFF) == []


def test_old_resolver_label_does_not_train_even_when_date_joins():
    old_p, old_s = _cell(0, center_c=10.0, settled_c=70.0)
    old_s["provenance_json"] = old_s["provenance_json"].replace(
        '"source_family": "NOAA"', '"source_family": "WU"'
    )
    current_p, current_s = _cell(1, center_c=20.0, settled_c=70.0)
    conn = _memory_db([old_p, current_p], [old_s, current_s])

    assert load_residual_rows(conn, metric="high", training_cutoff=CUTOFF) == [
        ("Chicago", pytest.approx((70 - 32) * 5 / 9 - 20))
    ]


@pytest.mark.parametrize("late_field", ["recorded_at", "observation_fetched_at"])
def test_label_not_known_at_cutoff_does_not_train(late_field):
    posterior, settlement = _cell(0, center_c=20.0, settled_c=70.0)
    settlement[late_field] = "2027-06-04T00:01:00+00:00"
    conn = _memory_db([posterior], [settlement])

    assert load_residual_rows(conn, metric="high", training_cutoff=CUTOFF) == []


@pytest.mark.parametrize("late_field", ["computed_at", "recorded_at"])
def test_forecast_not_known_at_cutoff_does_not_train(late_field):
    posterior, settlement = _cell(0, center_c=20.0, settled_c=70.0)
    posterior[late_field] = "2027-06-04T00:01:00+00:00"
    conn = _memory_db([posterior], [settlement])

    assert load_residual_rows(conn, metric="high", training_cutoff=CUTOFF) == []


def test_sqlite_utc_recorded_default_is_eligible_but_late_default_is_not():
    first, first_label = _cell(0, center_c=20.0, settled_c=70.0)
    first["recorded_at"] = "2026-08-22 12:01:00"
    late, late_label = _cell(1, center_c=20.0, settled_c=70.0)
    late["recorded_at"] = "2027-06-04 00:01:00"
    conn = _memory_db([first, late], [first_label, late_label])

    assert load_residual_rows(conn, metric="high", training_cutoff=CUTOFF) == [
        ("Chicago", pytest.approx((70 - 32) * 5 / 9 - 20))
    ]


def test_late_recorded_revision_does_not_hide_earlier_forecast():
    early, settlement = _cell(0, center_c=20.0, settled_c=70.0)
    late = dict(early, computed_at="2026-08-22T13:00:00+00:00",
                recorded_at="2027-06-04 00:01:00",
                provenance_json=json.dumps({"q_shape": "fused_day0_conditioned_normal",
                                            "anchor_value_c": 10.0}))
    conn = _memory_db([early, late], [settlement])

    assert load_residual_rows(conn, metric="high", training_cutoff=CUTOFF) == [
        ("Chicago", pytest.approx((70 - 32) * 5 / 9 - 20))
    ]


def test_later_offline_posterior_does_not_replace_served_lead():
    live, settlement = _cell(0, center_c=20.0, settled_c=70.0)
    offline = dict(live, computed_at="2026-08-22T13:00:00+00:00",
                   recorded_at="2026-08-22T13:01:00+00:00",
                   runtime_layer="offline",
                   provenance_json=json.dumps({"q_shape": "fused_normal_direct",
                                               "anchor_value_c": 10.0}))
    conn = _memory_db([live, offline], [settlement])

    assert load_residual_rows(conn, metric="high", training_cutoff=CUTOFF) == [
        ("Chicago", pytest.approx((70 - 32) * 5 / 9 - 20))
    ]


@pytest.mark.parametrize(
    ("last_layer", "last_recorded"),
    [("live", "2027-06-04 00:01:00"),
     ("offline", "2026-08-22T12:04:00+00:00")],
)
def test_fallback_preserves_posterior_id_tie_order(last_layer, last_recorded):
    first, settlement = _cell(0, center_c=11.0, settled_c=70.0)
    first["recorded_at"] = "2026-08-22T12:03:00+00:00"
    second = dict(first, recorded_at="2026-08-22T12:01:00+00:00",
                  provenance_json=json.dumps({"q_shape": "fused_normal_direct",
                                              "anchor_value_c": 22.0}))
    last = dict(first, recorded_at=last_recorded, runtime_layer=last_layer,
                provenance_json=json.dumps({"q_shape": "fused_normal_direct",
                                            "anchor_value_c": 33.0}))
    conn = _memory_db([first, second, last], [settlement])

    assert load_residual_rows(conn, metric="high", training_cutoff=CUTOFF) == [
        ("Chicago", pytest.approx((70 - 32) * 5 / 9 - 22))
    ]


@pytest.mark.parametrize(
    ("target", "computed"),
    [
        ("2026-11-01", "2026-11-01T04:30:00+00:00"),  # fall DST, local Oct 31
        ("2027-03-15", "2027-03-15T04:30:00+00:00"),  # spring DST, local Mar 14
    ],
)
def test_utc_midnight_forecast_uses_local_lead(target, computed):
    index = (date.fromisoformat(target) - date(2026, 8, 23)).days
    posterior, settlement = _cell(index, center_c=20.0, settled_c=70.0)
    posterior["computed_at"] = computed
    posterior["recorded_at"] = computed
    conn = _memory_db([posterior], [settlement])

    assert load_residual_rows(conn, metric="high", training_cutoff=CUTOFF) == [
        ("Chicago", pytest.approx((70 - 32) * 5 / 9 - 20))
    ]


def test_local_day0_does_not_replace_previous_local_lead():
    index = (date(2026, 11, 1) - date(2026, 8, 23)).days
    early, settlement = _cell(index, center_c=20.0, settled_c=70.0)
    early["computed_at"] = early["recorded_at"] = "2026-11-01T04:30:00+00:00"
    day0 = dict(early, computed_at="2026-11-01T05:30:00+00:00",
                recorded_at="2026-11-01T05:31:00+00:00")
    conn = _memory_db([early, day0], [settlement])

    assert load_residual_rows(conn, metric="high", training_cutoff=CUTOFF) == [
        ("Chicago", pytest.approx((70 - 32) * 5 / 9 - 20))
    ]


# --- the provider -----------------------------------------------------------


def test_provider_serves_the_fitted_shift():
    conn = _db_with_residual(240, residual=0.75)

    correction = mod.CenterDebiasFitProvider().correction(
        conn, city="Chicago", metric="high", now=NOW
    )

    assert correction is not None
    assert correction.shift_c == pytest.approx(0.75, abs=1e-9)
    assert correction.training_cutoff == CUTOFF
    assert correction.n_rows == 240
    assert correction.param_hash


def test_too_few_rows_fail_open_to_none():
    conn = _db_with_residual(mod.MIN_ROWS - 1, residual=0.75)

    assert (
        CenterDebiasFitProvider().correction(
            conn, city="Shanghai", metric="high", now=NOW
        )
        is None
    )


def test_disabled_metric_returns_none_without_reading_the_database():
    conn = _db_with_residual(240, residual=0.75)
    conn.close()  # any read would raise; a disabled metric must not read.

    assert (
        CenterDebiasFitProvider().correction(
            conn, city="Shanghai", metric="low", now=NOW
        )
        is None
    )


def test_low_is_not_enabled():
    assert mod.ENABLED_METRICS == ("high",)


def test_broken_connection_returns_none_and_does_not_raise():
    conn = _db_with_residual(240, residual=0.75)
    conn.close()

    assert (
        CenterDebiasFitProvider().correction(
            conn, city="Shanghai", metric="high", now=NOW
        )
        is None
    )


def test_missing_tables_return_none_and_do_not_raise(
    caplog: pytest.LogCaptureFixture,
):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    with caplog.at_level(logging.WARNING, logger="zeus.center_debias_live_fit"):
        correction = CenterDebiasFitProvider().correction(
            conn, city="Shanghai", metric="high", now=NOW
        )

    assert correction is None
    assert any("OperationalError" in record.message for record in caplog.records)


def test_a_failed_fit_is_cached_for_the_window():
    """An unreadable database is not re-dialed once per candidate."""

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    provider = CenterDebiasFitProvider()
    calls: list[str] = []
    real_load = mod.load_residual_rows

    def counting_load(connection, **kwargs):
        calls.append(kwargs["training_cutoff"])
        return real_load(connection, **kwargs)

    mod.load_residual_rows = counting_load
    try:
        for _ in range(4):
            assert (
                provider.correction(conn, city="Shanghai", metric="high", now=NOW)
                is None
            )
    finally:
        mod.load_residual_rows = real_load

    assert len(calls) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [("settlement_source_type_effective_date", "2027-07-01"),
     ("wu_station", "KMDW"), ("settlement_page_view", "all"),
     ("settlement_unit", "C"), ("timezone", "Pacific/Kiritimati")],
)
def test_same_window_source_contract_change_invalidates_cache(monkeypatch, field, value):
    conn = _db_with_residual(240, residual=0.75)
    provider = CenterDebiasFitProvider()
    assert provider.correction(conn, city="Chicago", metric="high", now=NOW) is not None
    changed = _cities()
    setattr(changed["Chicago"], field, value)
    monkeypatch.setattr(mod, "runtime_cities_by_name", lambda: changed)

    assert provider.correction(conn, city="Chicago", metric="high", now=NOW) is None


# --- deterministic window cutoff -------------------------------------------


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 9, 4, 0, 0, tzinfo=timezone.utc), "2026-09-04T00:00:00Z"),
        (datetime(2026, 9, 4, 5, 59, 59, tzinfo=timezone.utc), "2026-09-04T00:00:00Z"),
        (datetime(2026, 9, 4, 6, 0, tzinfo=timezone.utc), "2026-09-04T06:00:00Z"),
        (datetime(2026, 9, 4, 23, 59, tzinfo=timezone.utc), "2026-09-04T18:00:00Z"),
    ],
)
def test_window_cutoff_floors_to_the_six_hour_boundary(now, expected):
    assert window_cutoff(now) == expected


def test_non_utc_now_is_converted_before_flooring():
    tokyo = timezone.utc
    aware = datetime(2027, 6, 4, 3, 0, tzinfo=tokyo)

    assert window_cutoff(aware) == CUTOFF


def test_same_window_refits_nothing_and_serves_one_identity():
    conn = _db_with_residual(240, residual=0.75)
    provider = CenterDebiasFitProvider()

    first = provider.correction(
        conn,
        city="Chicago",
        metric="high",
        now=datetime(2027, 6, 4, 0, 1, tzinfo=timezone.utc),
    )
    second = provider.correction(
        conn,
        city="Chicago",
        metric="high",
        now=datetime(2027, 6, 4, 5, 58, tzinfo=timezone.utc),
    )

    assert first is not None and second is not None
    assert first.param_hash == second.param_hash
    assert first.training_cutoff == second.training_cutoff == CUTOFF


def test_a_new_window_refits():
    conn = _db_with_residual(240, residual=0.75)
    provider = CenterDebiasFitProvider()

    first = provider.correction(
        conn,
        city="Chicago",
        metric="high",
        now=datetime(2027, 6, 4, 3, 0, tzinfo=timezone.utc),
    )
    second = provider.correction(
        conn,
        city="Chicago",
        metric="high",
        now=datetime(2027, 6, 4, 7, 0, tzinfo=timezone.utc),
    )

    assert first is not None and second is not None
    assert first.training_cutoff == "2027-06-04T00:00:00Z"
    assert second.training_cutoff == "2027-06-04T06:00:00Z"
    assert first.param_hash != second.param_hash


def test_two_providers_in_the_same_window_agree():
    """Cross-process determinism: the cutoff, not a wall clock, sets the fit."""

    conn = _db_with_residual(240, residual=0.75)

    early = CenterDebiasFitProvider().correction(
        conn,
        city="Chicago",
        metric="high",
        now=datetime(2027, 6, 4, 0, 5, tzinfo=timezone.utc),
    )
    late = CenterDebiasFitProvider().correction(
        conn,
        city="Chicago",
        metric="high",
        now=datetime(2027, 6, 4, 5, 55, tzinfo=timezone.utc),
    )

    assert early is not None and late is not None
    assert early.param_hash == late.param_hash
    assert early.shift_c == pytest.approx(late.shift_c)
