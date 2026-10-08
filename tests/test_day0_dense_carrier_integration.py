# Created: 2026-10-07
# Last reused/audited: 2026-10-08
# Lifecycle: created=2026-10-07; last_reviewed=2026-10-08; last_reused=2026-10-08
# Authority basis: coordinator R1/R2 (typed SELECT/REPLAY seam, no live dense certificate) and D2/D3/D4
#   (lifecycle marks, cut-time information set, sealed evidence, one prepared request), 2026-10-08.
# Purpose: Pin the carrier seam on Helsinki/Tokyo fixture rows: evaluation=None is byte-identical legacy for every
#   operator; SELECT serves the dense law at the cut from sealed evidence; REPLAY reproduces it with no DB read and
#   fails closed without sealed evidence or parameters; unknown operators are rejected; the one prepared request
#   decides dispatch, absent/stale/wrong-station/missing-forecast/unqualified alike; later page corrections win;
#   Tokyo 760918.
# Reuse: Private SQLite fixtures and a temporary params artifact; no live DB.
from __future__ import annotations

import gzip
import json
import math
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from src.calibration import day0_dense_state_space_params as params_mod
from src.contracts.settlement_semantics import SettlementSemantics
from src.data import day0_dense_evidence as evidence
from src.data.day0_hourly_vectors import (
    DAY0_REMAINING_CARRIER_OPERATOR_V2,
    DAY0_REMAINING_CARRIER_OPERATOR_V3,
    Day0CarrierEvaluation,
    build_day0_remaining_probability_carrier,
    day0_remaining_carrier_identity_inputs,
)
from src.state.schema.observation_prints_schema import append_print, ensure_table

UTC = timezone.utc
HEL = ZoneInfo("Europe/Helsinki")
CITY = SimpleNamespace(name="Helsinki", timezone="Europe/Helsinki", wu_station="EFHK",
                       settlement_source_type="noaa", settlement_unit="C")
SEM = SettlementSemantics(resolution_source="noaa_EFHK", measurement_unit="C", precision=1.0,
                          rounding_rule="wmo_half_up", finalization_time="12:00:00Z")
BINS = ((None, 9.0),) + tuple((float(k), float(k)) for k in range(10, 17)) + ((17.0, None),)
FMI_STATION = {"fmisid": "100968", "wmo": "2974", "station": "Vantaa Helsinki-Vantaan lentoasema"}
SELECT, REPLAY = Day0CarrierEvaluation.SELECT, Day0CarrierEvaluation.REPLAY


def local(h: int, m: int = 0) -> datetime:
    return datetime(2026, 10, 7, h, m, tzinfo=HEL).astimezone(UTC)


def fmi_raw(at: datetime, value: float) -> str:
    return json.dumps({**FMI_STATION, "property": "https://opendata.fmi.fi/meta?observableProperty=observation&param=temperature&language=eng",
                       "unit": "degC", "availability": "local_fetch_only", "observed_at": at.isoformat(),
                       "value": value})


def lifecycle_block(last="2026-10-05"):
    return {"kept": {"routine": 0.97, "speci": 0.9}, "corrected": {"routine": 0.003, "speci": 0.01},
            "removed": {"routine": 0.012, "speci": 0.05}, "gross": {"routine": 0.015, "speci": 0.04},
            "outage_prior": 0.02, "delta": [[-1, 0.5], [1, 0.5]],
            "visibility": [[10.0, 0.4], [20.0, 0.8], [40.0, 0.95], [180.0, 0.98]], "last_local_date": last}


def city_params_block(metrics=("high", "low"), **over) -> dict:
    block = {
        "station": "EFHK", "timezone": "Europe/Helsinki", "metrics": list(metrics), "routine_minutes": [20, 50],
        "speci_rate_per_min": 0.0, "lifecycle": lifecycle_block(),
        "dense_channel": "fmi_airport_temperature", "dense_max_age_minutes": 25.0,
        "model": {"latent": {"tau": 213.0, "s2": 1.75, "s2_static": 0.0},
                  "mean": {"mu_hour": [0.0] * 24, "beta": -0.8},
                  "noise": {"b_hour": [0.04] * 24, "s1": 0.083, "s2": 0.41, "pi": 0.28, "quantum": 0.1,
                            "tau_e": 10000.0, "sd2": 0.0003}},
        "variants": [], "training": {"first": "2026-04-11", "last": "2026-10-05", "n_days": 160},
    }
    block.update(over)
    return block


def write_artifact(path: Path, cities: dict) -> Path:
    payload = {"schema_version": params_mod.SCHEMA_VERSION, "artifact": params_mod.ARTIFACT_KIND, "data_version": "test",
               "training_cutoff": "2026-10-05", "qualification_hash": "q" * 64, "cities": cities}
    payload["content_hash"] = params_mod.canonical_hash(payload)
    path.write_text(json.dumps(payload))
    return path


@pytest.fixture
def artifact(tmp_path, monkeypatch):
    path = write_artifact(tmp_path / "params.json", {"Helsinki": city_params_block()})
    monkeypatch.setattr(params_mod, "ARTIFACT_PATH", path)
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {"Helsinki": CITY})
    return path


@pytest.fixture
def conn(request):
    """Helsinki fixture ledger.  ``request.param`` (indirect) drops or relabels the FMI rows at write
    time, since the ledger is append-only."""
    variant = getattr(request, "param", None)
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_table(c)
    c.execute("CREATE TABLE day0_hourly_vectors (vector_id TEXT, model TEXT, city TEXT, target_date TEXT, "
              "timezone_name TEXT, captured_at TEXT, times_json TEXT, temps_c_json TEXT)")
    start = datetime(2026, 10, 6, 18, tzinfo=HEL)
    times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(36)]
    temps = [12.0 + 3.0 * math.sin(2 * math.pi * ((i - 6) % 24 - 9) / 24) for i in range(36)]
    c.execute("INSERT INTO day0_hourly_vectors VALUES (?,?,?,?,?,?,?,?)",
              ("d0hv-test", "ecmwf_ifs", "Helsinki", "2026-10-07", "Europe/Helsinki",
               datetime(2026, 10, 6, 21, tzinfo=UTC).isoformat(), json.dumps(times), json.dumps(temps)))
    t = local(0, 0) - timedelta(hours=3)
    while t <= local(13, 0):
        lt = t.astimezone(HEL)
        v = round(12.0 + 3.0 * math.sin(2 * math.pi * (lt.hour + lt.minute / 60 - 9) / 24), 1)
        if variant != "absent":
            append_print(c, city="Helsinki", station_id="EFHF" if variant == "wrong_station" else "EFHK",
                         source_channel="fmi_airport_temperature", publish_ts_utc=t.isoformat(), value_native=v,
                         unit="C", fetched_at_utc=(t + timedelta(minutes=2)).isoformat(), raw_report=fmi_raw(t, v))
        if t.minute in (20, 50):
            k = int(SEM.round_single(v))
            raw = f"METAR EFHK {t.day:02d}{t.hour:02d}{t.minute:02d}Z 27012KT 9999 SCT025 {k:02d}/06 Q1009"
            append_print(c, city="Helsinki", station_id="EFHK", source_channel="aviationweather_metar",
                         publish_ts_utc=(t + timedelta(minutes=1)).isoformat(), value_native=k, unit="C",
                         fetched_at_utc=(t + timedelta(minutes=1, seconds=30)).isoformat(), raw_report=raw)
        t += timedelta(minutes=10)
    yield c
    c.close()


def page_row(c, at: datetime, value: float, fetched: datetime):
    raw = json.dumps({"observed_at": at.isoformat(), "source_channel": "noaa_wrh_efhk", "station_id": "EFHK",
                      "unit": "C", "value_native": value})
    append_print(c, city="Helsinki", station_id="EFHK", source_channel="noaa_wrh_efhk", publish_ts_utc=at.isoformat(),
                 value_native=value, unit="C", fetched_at_utc=fetched.isoformat(), raw_report=raw)


def kwargs(*, cut: datetime, state_at: datetime, metric="high", operator=None, final=()):
    inputs = day0_remaining_carrier_identity_inputs(city="Helsinki", unit="C", decision_time_utc=cut.isoformat(),
                                                    station_id="EFHK", preliminary_survival_identity="ab" * 32)
    inputs["current_path_state"] = {"value_native": 14.0, "observed_at_utc": state_at.isoformat(),
                                    "source": "fmi_airport_temperature"}
    return dict(future_extremes_c=(14.2, 14.6, 15.1), final_extreme_centers_c=final,
                boundary_scenarios=((None, 0.4), (14.0, 0.6)), metric=metric,
                path_error_sigma_c=0.8, instrument_sigma_c=0.3, bin_bounds_c=BINS, n_point=2000, n_samples=500,
                identity_inputs=inputs, settlement_semantics=SEM, operator=operator)


def use_conn(monkeypatch, c):
    from contextlib import contextmanager

    @contextmanager
    def reader(_conn):
        yield c

    monkeypatch.setattr(evidence, "_read_connection", reader)


def build(c, monkeypatch, **kw):
    use_conn(monkeypatch, c)
    return build_day0_remaining_probability_carrier(**kw)


# ---------------------------------------------------------------- R1: legacy default

@pytest.mark.parametrize("operator,final", [(None, ()), (DAY0_REMAINING_CARRIER_OPERATOR_V2, ()),
                                            (DAY0_REMAINING_CARRIER_OPERATOR_V3, (14.8,))])
def test_no_evaluation_is_legacy_and_never_reads_dense(artifact, conn, monkeypatch, operator, final):
    kw = kwargs(cut=local(13, 5), state_at=local(13, 0), operator=operator, final=final)
    legacy = build(conn, monkeypatch, **kw)
    monkeypatch.setattr(evidence, "prepare_dense_request", lambda *a, **k: pytest.fail("legacy path read dense"))
    assert build(conn, monkeypatch, **kw) == legacy
    assert legacy["operator"] != evidence.DAY0_DENSE_STATE_SPACE_OPERATOR


def test_select_with_explicit_legacy_operator_runs_that_operator(artifact, conn, monkeypatch):
    kw = kwargs(cut=local(13, 5), state_at=local(13, 0), operator=DAY0_REMAINING_CARRIER_OPERATOR_V2)
    assert build(conn, monkeypatch, **kw, evaluation=SELECT) == build(conn, monkeypatch, **kw)


def test_unknown_operator_and_stray_sealed_evidence_are_rejected(artifact, conn, monkeypatch):
    with pytest.raises(ValueError, match="DAY0_REMAINING_CARRIER_OPERATOR_UNKNOWN"):
        build(conn, monkeypatch, **kwargs(cut=local(13, 5), state_at=local(13, 0), operator="bogus_v9"),
              evaluation=SELECT)
    with pytest.raises(ValueError, match="DAY0_DENSE_SEALED_EVIDENCE_WITHOUT_EVALUATION"):
        build(conn, monkeypatch, **kwargs(cut=local(13, 5), state_at=local(13, 0)), sealed_dense={"x": 1})


# ---------------------------------------------------------------- SELECT and REPLAY

def test_select_serves_dense_at_the_cut_and_replay_reproduces_without_db(artifact, conn, monkeypatch):
    kw = kwargs(cut=local(13, 5), state_at=local(13, 0))
    out = build(conn, monkeypatch, **kw, evaluation=SELECT)
    assert out["operator"] == evidence.DAY0_DENSE_STATE_SPACE_OPERATOR
    sealed = out["dense_evidence"]["sealed"]
    assert sealed["probability_cutoff_utc"] == local(13, 5).isoformat() and sealed["dense_count"] > 50
    assert np.asarray(out["q"]).sum() == pytest.approx(1.0) and len(out["samples"]) == 500
    monkeypatch.setattr(evidence, "_read_connection", lambda *_: pytest.fail("replay read the DB"))
    later = {**kw["identity_inputs"], "probability_cutoff_utc": local(18, 0).isoformat(),
             "decision_time_utc": local(18, 0).isoformat()}
    replay = build_day0_remaining_probability_carrier(
        **{**kw, "identity_inputs": later, "operator": evidence.DAY0_DENSE_STATE_SPACE_OPERATOR},
        evaluation=REPLAY, sealed_dense=sealed)
    assert replay["q"] == out["q"] and replay["samples"] == out["samples"]
    assert replay["content_identity"] == out["content_identity"]


def test_replay_fails_closed_without_sealed_evidence_or_parameters(artifact, conn, monkeypatch):
    kw = kwargs(cut=local(13, 5), state_at=local(13, 0), operator=evidence.DAY0_DENSE_STATE_SPACE_OPERATOR)
    with pytest.raises(ValueError, match="DAY0_DENSE_REPLAY_SEALED_EVIDENCE_MISSING"):
        build_day0_remaining_probability_carrier(**kw, evaluation=REPLAY)
    sealed = build(conn, monkeypatch, **kwargs(cut=local(13, 5), state_at=local(13, 0)),
                   evaluation=SELECT)["dense_evidence"]["sealed"]
    model = {**city_params_block()["model"], "latent": {"tau": 300.0, "s2": 1.0}}
    write_artifact(artifact, {"Helsinki": city_params_block(model=model)})
    with pytest.raises(ValueError, match="DAY0_DENSE_REPLAY_PARAMS_MISSING"):
        build_day0_remaining_probability_carrier(**kw, evaluation=REPLAY, sealed_dense=sealed)


def test_requalification_alone_keeps_sealed_certificates_replayable(artifact, conn, monkeypatch):
    sealed = build(conn, monkeypatch, **kwargs(cut=local(13, 5), state_at=local(13, 0)),
                   evaluation=SELECT)["dense_evidence"]["sealed"]
    write_artifact(artifact, {"Helsinki": city_params_block(metrics=())})
    kw = kwargs(cut=local(13, 5), state_at=local(13, 0), operator=evidence.DAY0_DENSE_STATE_SPACE_OPERATOR)
    out = build_day0_remaining_probability_carrier(**kw, evaluation=REPLAY, sealed_dense=sealed)
    assert out["operator"] == evidence.DAY0_DENSE_STATE_SPACE_OPERATOR


def test_cut_admits_rows_received_by_it_and_nothing_later(artifact, conn, monkeypatch):
    a = build(conn, monkeypatch, **kwargs(cut=local(12, 5), state_at=local(12, 0)), evaluation=SELECT)
    late = local(11, 55)
    append_print(conn, city="Helsinki", station_id="EFHK", source_channel="fmi_airport_temperature",
                 publish_ts_utc=late.isoformat(), value_native=19.9, unit="C",
                 fetched_at_utc=local(12, 6).isoformat(), raw_report=fmi_raw(late, 19.9))
    b = build(conn, monkeypatch, **kwargs(cut=local(12, 5), state_at=local(12, 0)), evaluation=SELECT)
    assert a["q"] == b["q"] and a["content_identity"] == b["content_identity"]
    c = build(conn, monkeypatch, **kwargs(cut=local(12, 10), state_at=local(12, 0)), evaluation=SELECT)
    assert c["content_identity"] != a["content_identity"]


# ---------------------------------------------------------------- D4: one prepared request

def _prepared(conn, cut, city="Helsinki", metric="high", target=date(2026, 10, 7)):
    return evidence.prepare_dense_request(conn, city=city, metric=metric, target=target, cut=cut, semantics=SEM)


def test_prepared_request_reasons(artifact, conn):
    assert _prepared(conn, local(13, 5)).serves
    assert _prepared(conn, local(15, 0)).reason == "DENSE_STALE"
    assert _prepared(conn, local(13, 5), target=date(2026, 10, 5)).reason == "NOT_QUALIFIED"
    write_artifact(artifact, {"Helsinki": city_params_block(metrics=("high",))})
    assert _prepared(conn, local(13, 5), metric="low").reason == "NOT_QUALIFIED"
    write_artifact(artifact, {"Helsinki": city_params_block(station="EFHF")})
    assert _prepared(conn, local(13, 5)).reason == "STATION_MISMATCH"
    write_artifact(artifact, {"Helsinki": city_params_block()})
    conn.execute("DELETE FROM day0_hourly_vectors")
    assert _prepared(conn, local(13, 5)).reason == "VECTOR_WINDOW_INCOMPLETE"


def test_dispatch_and_seed_suppression_read_one_prepared_request(artifact, conn, monkeypatch):
    calls = []
    real = evidence.prepare_dense_request
    monkeypatch.setattr(evidence, "prepare_dense_request", lambda *a, **k: calls.append(k["cut"]) or real(*a, **k))
    assert evidence.dense_serves(conn, city="Helsinki", metric="high", target_date="2026-10-07", decision=local(13, 5))
    out = build(conn, monkeypatch, **kwargs(cut=local(13, 5), state_at=local(13, 0)), evaluation=SELECT)
    assert out["operator"] == evidence.DAY0_DENSE_STATE_SPACE_OPERATOR and calls == [local(13, 5), local(13, 5)]
    assert not evidence.dense_serves(conn, city="Helsinki", metric="high", target_date="2026-10-07",
                                     decision=local(15, 0))
    stale = build(conn, monkeypatch, **kwargs(cut=local(15, 0), state_at=local(13, 0)), evaluation=SELECT)
    assert stale["operator"] != evidence.DAY0_DENSE_STATE_SPACE_OPERATOR


@pytest.mark.parametrize("conn,case", [("absent", "absent"), (None, "stale"), ("wrong_station", "wrong_station"),
                                       (None, "missing_forecast"), (None, "unqualified")], indirect=["conn"])
def test_unavailable_dense_select_is_byte_identical_legacy(artifact, conn, monkeypatch, case):
    cut = local(13, 5)
    if case == "stale":
        cut = local(15, 0)
    elif case == "missing_forecast":
        conn.execute("DELETE FROM day0_hourly_vectors")
    else:
        write_artifact(artifact, {"Helsinki": city_params_block(metrics=())})
    kw = kwargs(cut=cut, state_at=local(13, 0))
    assert build(conn, monkeypatch, **kw, evaluation=SELECT) == build(conn, monkeypatch, **kw)


# ---------------------------------------------------------------- D2 evidence: page versions and visibility

def test_later_page_correction_wins(artifact, conn):
    at = local(11, 50)
    page_row(conn, at, 16.0, local(11, 58))
    page_row(conn, at, 15.0, local(12, 30))
    sealed = _prepared(conn, local(13, 5)).sealed
    t = round((at - local(0, 0)).total_seconds() / 60, 6)
    assert [k for tt, k in sealed["page"] if tt == t] == [15]


def test_fetch_without_the_row_is_visibility_evidence_not_deletion(artifact, conn):
    """An intraday page fetch at 12:30 local covers 09:30..12:30 and lacks the 12:20 report: kept/corrected
    weights carry (1 - a(10 min)); the mark stays on the tape."""
    page_row(conn, local(12, 0), 14.0, local(12, 30))
    sealed = _prepared(conn, local(12, 35)).sealed
    t = round((local(12, 20) - local(0, 0)).total_seconds() / 60, 6)
    mark = [m for m in sealed["marks"] if m[0] == t]
    assert len(mark) == 1
    assert mark[0][2] == pytest.approx(0.97 * (1 - 0.4))
    assert mark[0][4] == pytest.approx(0.012)


def test_page_row_is_the_semantic_certificate(artifact, conn, monkeypatch):
    page_row(conn, local(11, 50), 15.0, local(11, 58))
    out = build(conn, monkeypatch, **kwargs(cut=local(12, 5), state_at=local(12, 0)), evaluation=SELECT)
    q = dict(zip(BINS, out["q"]))
    assert all(q[b] == 0.0 for b in BINS if b[1] is not None and b[1] < 15)
    assert out["dense_evidence"]["semantic_boundary"] == 15


# ---------------------------------------------------------------- live-shaped regression

TOKYO = SimpleNamespace(name="Tokyo", timezone="Asia/Tokyo", wu_station="RJTT", settlement_source_type="noaa",
                        settlement_unit="C")
TOKYO_SEM = SettlementSemantics.for_city(TOKYO)
TOKYO_BINS = ((None, 20.0),) + tuple((float(k), float(k)) for k in range(21, 30)) + ((30.0, None),)


def _tokyo_conn():
    blob = json.loads(gzip.open(Path(__file__).parent / "fixtures" / "day0_dense" / "tokyo_2026-10-07.json.gz",
                                "rt").read())
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_table(c)
    c.execute("CREATE TABLE day0_hourly_vectors (vector_id TEXT, model TEXT, city TEXT, target_date TEXT, "
              "timezone_name TEXT, captured_at TEXT, times_json TEXT, temps_c_json TEXT)")
    c.executemany("INSERT INTO day0_hourly_vectors VALUES (?,?,?,?,?,?,?,?)", [tuple(v) for v in blob["vectors"]])
    for city, station, channel, published, value, unit, fetched, raw in blob["prints"]:
        append_print(c, city=city, station_id=station, source_channel=channel, publish_ts_utc=published,
                     value_native=value, unit=unit, fetched_at_utc=fetched, raw_report=raw)
    return c


def test_tokyo_760918_route_value_moves_mass_without_structural_zero(tmp_path, monkeypatch):
    """Tokyo 2026-10-07 HIGH (posterior 760918, 07:59Z): JMA 25.2 at 06:00Z, AWC 25 at 05:00/05:30/06:00Z, no page
    row yet.  Served q gave 0.64 to bins <= 24.  The dense law puts nearly all mass at >= 25; only a page row
    makes a bin exactly zero."""
    block = city_params_block(station="RJTT", timezone="Asia/Tokyo", routine_minutes=[0, 30],
                              dense_channel="jma_amedas_temperature")
    block["model"] = {"latent": {"tau": 354.0, "s2": 1.47, "s2_static": 0.07},
                      "mean": {"mu_hour": [0.0] * 24, "beta": -1.26},
                      "noise": {"b_hour": [0.05] * 24, "s1": 0.02, "s2": 0.02, "pi": 0.0, "quantum": 0.1,
                                "tau_e": 1.0, "sd2": 0.0}}
    monkeypatch.setattr(params_mod, "ARTIFACT_PATH", write_artifact(tmp_path / "params.json", {"Tokyo": block}))
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {"Tokyo": TOKYO})
    c = _tokyo_conn()
    cut = datetime(2026, 10, 7, 7, 59, 35, tzinfo=UTC)

    def run():
        inputs = day0_remaining_carrier_identity_inputs(city="Tokyo", unit="C", decision_time_utc=cut.isoformat(),
                                                        station_id="RJTT", preliminary_survival_identity="cd" * 32)
        inputs["current_path_state"] = {"value_native": 25.2, "observed_at_utc": "2026-10-07T07:40:00+00:00",
                                        "source": "jma_amedas_temperature"}
        return build(c, monkeypatch, future_extremes_c=(23.0, 23.5), boundary_scenarios=((None, 0.4), (25.0, 0.6)),
                     metric="high", path_error_sigma_c=0.8, instrument_sigma_c=0.3, bin_bounds_c=TOKYO_BINS,
                     n_point=2000, n_samples=500, identity_inputs=inputs, settlement_semantics=TOKYO_SEM,
                     evaluation=SELECT)

    out = run()
    assert out["operator"] == evidence.DAY0_DENSE_STATE_SPACE_OPERATOR
    q = dict(zip(TOKYO_BINS, out["q"]))
    assert sum(p for b, p in q.items() if b[1] is not None and b[1] <= 24) < 0.05
    assert all(out["dense_evidence"]["semantic_support"]) and out["dense_evidence"]["semantic_boundary"] is None
    append_print(c, city="Tokyo", station_id="RJTT", source_channel="noaa_wrh_rjtt",
                 publish_ts_utc="2026-10-07T06:00:00+00:00", value_native=25.0, unit="C",
                 fetched_at_utc="2026-10-07T07:40:00+00:00", raw_report="RJTT 070600Z 35005KT 9999 25/10 Q1013")
    paged = run()
    pq = dict(zip(TOKYO_BINS, paged["q"]))
    support = dict(zip(TOKYO_BINS, paged["dense_evidence"]["semantic_support"]))
    assert paged["dense_evidence"]["semantic_boundary"] == 25
    assert all(not support[b] and pq[b] == 0.0 for b in TOKYO_BINS if b[1] is not None and b[1] <= 24)
    c.close()
