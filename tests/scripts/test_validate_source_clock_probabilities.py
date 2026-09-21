# Created: 2026-09-20
# Last reused/audited: 2026-09-21
# Purpose: Defend causal extraction and native-unit probability comparisons.
# Reuse: Run before changing source-basket validation; no live database needed.
from datetime import datetime, timezone
import json
import math
import sqlite3
from types import SimpleNamespace

import pytest

from scripts import validate_source_clock_probabilities as validation
from src.config import City


def city():
    return City(
        name="Chicago",
        lat=41.9742,
        lon=-87.9073,
        timezone="America/Chicago",
        settlement_unit="F",
        cluster="CONUS",
        wu_station="KORD",
        settlement_source_type="noaa",
    )


def bins():
    def c(f):
        return (f - 32) / 1.8

    return [
        dict(
            bin_id=str(i),
            lower_c=None if lo is None else c(lo),
            upper_c=None if hi is None else c(hi),
            settlement_unit="F",
            settlement_step_c=1 / 1.8,
            rounding_rule="wmo_half_up",
        )
        for i, (lo, hi) in enumerate(
            [(None, 75), (76, 76), (77, 77), (78, 78), (79, None)]
        )
    ]


def test_native_fahrenheit_partition_has_one_actual_winner():
    ordered, winner = validation.ordered_bins({"bin_topology": bins()}, city(), 78)
    assert winner == 3
    assert [b["bin_id"] for b in ordered] == [str(i) for i in range(5)]
    broken = bins()
    broken[1]["lower_c"] -= 1 / 1.8
    with pytest.raises(ValueError, match="overlapping"):
        validation.ordered_bins({"bin_topology": broken}, city(), 78)


def test_only_declared_sqlite_record_clock_accepts_naive_utc():
    with pytest.raises(ValueError, match="timezone"):
        validation.aware("2026-09-18 12:00:00")
    assert validation.aware("2026-09-18 12:00:00", sqlite_utc=True) == datetime(
        2026, 9, 18, 12, tzinfo=timezone.utc
    )
    assert validation.aware("2026-09-18T07:00:00-05:00") == datetime(
        2026, 9, 18, 12, tzinfo=timezone.utc
    )


def input_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE ensemble_snapshots(snapshot_id,city,target_date,temperature_metric,authority,boundary_ambiguous,members_unit,source_available_at,fetch_time,recorded_at,source_cycle_time,members_json)"
    )
    conn.execute(
        "INSERT INTO ensemble_snapshots VALUES(1,'Chicago','2026-09-19','high','VERIFIED',0,'C','2026-09-18T07:00:00Z','2026-09-18T07:00:01Z','2026-09-18 07:00:02','2026-09-18T06:00:00Z',?)",
        (json.dumps([25.0, 27.0] * 10),),
    )
    conn.execute(
        "CREATE TABLE raw_model_forecasts(raw_model_forecast_id,source_available_at,captured_at,recorded_at,endpoint,coverage_status,source_id,product_id,request_url_hash,raw_sha256,latitude_requested,longitude_requested,timezone_requested,model_name,request_params_json)"
    )
    for i in (1, 2):
        model = "icon_global" if i == 1 else "ukmo_global_deterministic_10km"
        conn.execute(
            "INSERT INTO raw_model_forecasts VALUES(?,'2026-09-18T07:00:00Z','2026-09-18T07:00:01Z','2026-09-18 07:00:02','single_runs','COVERED',?,?,'requesthash','rawhash',41.9742,-87.9073,'America/Chicago',?,?)",
            (
                i,
                model + "_single_runs",
                model + "::single_runs",
                model,
                json.dumps({"models": model, "temperature_unit": "celsius"}),
            ),
        )
    return conn


def served():
    return {
        model: SimpleNamespace(
            value_c=value, raw_model_forecast_id=i, served_cycle="2026-09-18T06:00:00Z"
        )
        for model, value, i in [
            ("icon_global", 25.0, 1),
            ("ukmo_global_deterministic_10km", 27.0, 2),
        ]
    }


def candidate_run(conn, monkeypatch, history=()):
    monkeypatch.setattr(
        validation, "read_current_instrument_values", lambda *_args, **_kwargs: served()
    )
    return validation.make_candidates(
        conn,
        dict(
            city="Chicago",
            target_date="2026-09-19",
            temperature_metric="high",
            source_cycle_time="2026-09-18T06:00:00Z",
        ),
        {"bayes_precision_fusion": {"current_evidence_shape": {"snapshot_id": 1}}},
        city(),
        datetime(2026, 9, 18, 12, tzinfo=timezone.utc),
        history,
        bins(),
    )


def test_candidate_uses_real_shape_and_settlement_integration(monkeypatch):
    candidates, values, _ = candidate_run(input_db(), monkeypatch)
    q = next(iter(candidates.values())).values
    # Two equally weighted providers and ENS: within²=1, between²=1,
    # ENS-center disagreement=0. Native F sigma is sqrt(2)*1.8.
    sigma = math.sqrt(2) * 1.8
    center = 26 * 1.8 + 32
    expected_below_75 = 0.5 * (1 + math.erf((75.5 - center) / (sigma * math.sqrt(2))))
    assert q[0] == pytest.approx(expected_below_75, abs=1e-12)
    assert sum(q) == pytest.approx(1)
    assert values == {"icon_global": 25, "ukmo_global_deterministic_10km": 27}


@pytest.mark.parametrize(
    "column,value",
    [
        ("captured_at", "2026-09-18T13:00:00Z"),
        ("recorded_at", "2026-09-18 13:00:00"),
        ("latitude_requested", 0),
        ("coverage_status", "PARTIAL"),
        ("request_url_hash", None),
    ],
)
def test_bad_source_proof_cannot_form_a_candidate(monkeypatch, column, value):
    conn = input_db()
    assert column in {
        "captured_at",
        "recorded_at",
        "latitude_requested",
        "coverage_status",
        "request_url_hash",
    }
    conn.execute(
        f"UPDATE raw_model_forecasts SET {column}=? WHERE raw_model_forecast_id=2",
        (value,),
    )
    assert not candidate_run(conn, monkeypatch)[0]


def test_future_label_never_changes_candidate_weights(monkeypatch):
    before = candidate_run(input_db(), monkeypatch)[0]
    history = [
        dict(
            target_date="2026-09-17",
            known_at=datetime(2026, 9, 18, 13, tzinfo=timezone.utc),
            values={"icon_global": -100, "ukmo_global_deterministic_10km": 25},
            settlement_c=25,
        )
    ] * 100
    assert candidate_run(input_db(), monkeypatch, history)[0] == before


def test_native_fahrenheit_ensemble_matches_celsius_counterpart(monkeypatch):
    before = next(iter(candidate_run(input_db(), monkeypatch)[0].values())).values
    conn = input_db()
    conn.execute(
        "UPDATE ensemble_snapshots SET members_unit='degF', members_json=?",
        (json.dumps([77.0, 80.6] * 10),),
    )
    after = next(iter(candidate_run(conn, monkeypatch)[0].values())).values
    assert after == pytest.approx(before, abs=1e-12)
