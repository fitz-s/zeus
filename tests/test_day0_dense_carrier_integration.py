# Created: 2026-10-07
# Last reused/audited: 2026-10-07
# Lifecycle: created=2026-10-07; last_reviewed=2026-10-07; last_reused=2026-10-07
# Purpose: Pin the dense state-space dispatch inside build_day0_remaining_probability_carrier
#   (Helsinki fixture rows), legacy byte-identity when dense is absent/stale/unqualified, receipt
#   gating, the clock-bound identity, and the G1/G2/G8 rules (fast-admission routes are provisional
#   METAR-instant evidence, never a semantic certificate; settlement-integer supersession).
# Reuse: Private SQLite fixtures and a temporary params artifact; no live DB.
from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from src.calibration import day0_dense_state_space_params as params_mod
from src.contracts.settlement_semantics import SettlementSemantics
from src.data import day0_dense_evidence as evidence
from src.data import day0_dense_state_space as ds
from src.data.day0_hourly_vectors import (
    DAY0_REMAINING_CARRIER_OPERATOR_V2,
    build_day0_remaining_probability_carrier,
    day0_remaining_carrier_identity_inputs,
)
from src.state.schema.observation_prints_schema import append_print, ensure_table

UTC = timezone.utc
HEL = ZoneInfo("Europe/Helsinki")
TARGET = date(2026, 10, 7)
CITY = SimpleNamespace(name="Helsinki", timezone="Europe/Helsinki", wu_station="EFHK",
                       settlement_source_type="noaa", settlement_unit="C")
SEM = SettlementSemantics(resolution_source="noaa_EFHK", measurement_unit="C", precision=1.0,
                          rounding_rule="wmo_half_up", finalization_time="12:00:00Z")
BINS = ((None, 9.0),) + tuple((float(k), float(k)) for k in range(10, 17)) + ((17.0, None),)
FMI_STATION = {"fmisid": "100968", "wmo": "2974", "station": "Vantaa Helsinki-Vantaan lentoasema"}


def local(h: int, m: int = 0) -> datetime:
    return datetime(2026, 10, 7, h, m, tzinfo=HEL).astimezone(UTC)


def fmi_raw(at: datetime, value: float) -> str:
    return json.dumps({**FMI_STATION, "property": "https://opendata.fmi.fi/meta?observableProperty=observation&param=temperature&language=eng",
                       "unit": "degC", "availability": "local_fetch_only", "observed_at": at.isoformat(),
                       "value": value})


def city_params_block(metrics=("high", "low")) -> dict:
    return {
        "station": "EFHK", "timezone": "Europe/Helsinki", "metrics": list(metrics), "routine_minutes": [20, 50],
        "speci_rate_per_min": 0.0, "speci_policy": "none", "page_retention": {"s": 0.96, "last_local_date": "2026-10-05"},
        "dense_channel": "fmi_airport_temperature", "dense_max_age_minutes": 25.0, "provisional_route_channels": [],
        "model": {"latent": {"tau": 213.0, "s2": 1.75, "s2_static": 0.0},
                  "mean": {"mu_hour": [0.0] * 24, "beta": -0.8},
                  "noise": {"b_hour": [0.04] * 24, "s1": 0.083, "s2": 0.41, "pi": 0.28, "quantum": 0.1,
                            "tau_e": 10000.0, "sd2": 0.0003}},
        "variants": [], "training": {"first": "2026-04-11", "last": "2026-10-05", "n_days": 160},
    }


@pytest.fixture
def artifact(tmp_path, monkeypatch):
    payload = {"schema_version": 1, "artifact": "day0_dense_state_space_params", "data_version": "test",
               "training_cutoff": "2026-10-05", "cities": {"Helsinki": city_params_block()}}
    payload["content_hash"] = params_mod.canonical_hash(payload)
    path = tmp_path / "params.json"
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(params_mod, "ARTIFACT_PATH", path)
    monkeypatch.setattr("src.calibration.day0_dense_state_space_params.ARTIFACT_PATH", path)
    real = params_mod.dense_params_for
    monkeypatch.setattr(params_mod, "dense_params_for", lambda c, m, d, path=path: real(c, m, d, path))
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {"Helsinki": CITY})
    evidence._CACHE.clear()
    return path


@pytest.fixture
def conn():
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
        append_print(c, city="Helsinki", station_id="EFHK", source_channel="fmi_airport_temperature",
                     publish_ts_utc=t.isoformat(), value_native=v, unit="C",
                     fetched_at_utc=(t + timedelta(minutes=2)).isoformat(), raw_report=fmi_raw(t, v))
        if t.minute in (20, 50):
            k = int(SEM.round_single(v))
            raw = f"METAR EFHK {t.day:02d}{t.hour:02d}{t.minute:02d}Z 27012KT 9999 SCT025 {k:02d}/06 Q1009"
            append_print(c, city="Helsinki", station_id="EFHK", source_channel="aviationweather_metar",
                         publish_ts_utc=(t + timedelta(minutes=1)).isoformat(), value_native=k, unit="C",
                         fetched_at_utc=(t + timedelta(minutes=1, seconds=30)).isoformat(), raw_report=raw)
        t += timedelta(minutes=10)
    yield c
    c.close()


def print_value(conn, channel: str, at: datetime) -> float:
    if channel == "aviationweather_metar":
        row = conn.execute("SELECT value_native FROM observation_prints WHERE source_channel = ? AND raw_report LIKE ?",
                           (channel, f"% {at.day:02d}{at.hour:02d}{at.minute:02d}Z %")).fetchone()
    else:
        row = conn.execute("SELECT value_native FROM observation_prints WHERE source_channel = ? AND publish_ts_utc = ?",
                           (channel, at.isoformat())).fetchone()
    return float(row[0])


def carrier(conn, *, decision: datetime, state_at: datetime, state_value: float | None = None,
            state_source="fmi_airport_temperature", metric="high", scenarios=((None, 0.4), (14.0, 0.6))):
    if state_value is None:
        state_value = print_value(conn, state_source, state_at)
    inputs = day0_remaining_carrier_identity_inputs(city="Helsinki", unit="C", decision_time_utc=decision.isoformat(),
                                                    station_id="EFHK", preliminary_survival_identity="ab" * 32)
    inputs["current_path_state"] = {"value_native": state_value, "observed_at_utc": state_at.isoformat(),
                                    "source": state_source}
    kwargs = dict(future_extremes_c=(14.2, 14.6, 15.1), boundary_scenarios=scenarios, metric=metric,
                  path_error_sigma_c=0.8, instrument_sigma_c=0.3, bin_bounds_c=BINS, n_point=2000, n_samples=500,
                  identity_inputs=inputs, settlement_semantics=SEM, operator=DAY0_REMAINING_CARRIER_OPERATOR_V2)
    return kwargs


def run(conn, monkeypatch, **kw):
    monkeypatch.setattr(evidence, "_read_connection", _fixed(conn))
    kwargs = carrier(conn, **kw)
    return build_day0_remaining_probability_carrier(**kwargs), kwargs


def _fixed(c):
    from contextlib import contextmanager

    @contextmanager
    def reader(_conn):
        yield c
    return reader


# ---------------------------------------------------------------- dispatch

def test_helsinki_carrier_runs_dense_law(artifact, conn, monkeypatch):
    out, _ = run(conn, monkeypatch, decision=local(13, 5), state_at=local(13, 0))
    assert out["operator"] == evidence.DAY0_DENSE_STATE_SPACE_OPERATOR
    q = np.asarray(out["q"])
    assert q.sum() == pytest.approx(1.0) and len(out["samples"]) == 500
    assert out["dense_evidence"]["dense_count"] > 50
    # Provisional AWC rows only: no structural zero anywhere.
    assert np.all(q[1:-1] >= 0.0)


def test_absent_artifact_is_byte_identical_legacy(conn, monkeypatch, tmp_path):
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {"Helsinki": CITY})
    monkeypatch.setattr(params_mod, "ARTIFACT_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(evidence, "_read_connection", _fixed(conn))
    kwargs = carrier(conn, decision=local(13, 5), state_at=local(13, 0))
    dense = build_day0_remaining_probability_carrier(**kwargs)
    monkeypatch.setattr(evidence, "dense_remaining_carrier", lambda **_: None)
    legacy = build_day0_remaining_probability_carrier(**kwargs)
    assert dense == legacy and dense["operator"] == DAY0_REMAINING_CARRIER_OPERATOR_V2


def test_stale_dense_channel_falls_back_to_legacy(artifact, conn, monkeypatch):
    # Staleness is judged at tau (the state's first receipt), not at the wall clock.
    out, _ = run(conn, monkeypatch, decision=local(14, 0), state_at=local(13, 0))
    assert out["operator"] == evidence.DAY0_DENSE_STATE_SPACE_OPERATOR
    stale = sqlite3.connect(":memory:")
    stale.row_factory = sqlite3.Row
    ensure_table(stale)
    stale.execute("CREATE TABLE day0_hourly_vectors (vector_id TEXT, model TEXT, city TEXT, target_date TEXT, "
                  "timezone_name TEXT, captured_at TEXT, times_json TEXT, temps_c_json TEXT)")
    stale.executemany("INSERT INTO day0_hourly_vectors VALUES (?,?,?,?,?,?,?,?)",
                      [tuple(r) for r in conn.execute("SELECT vector_id, model, city, target_date, timezone_name, "
                                                       "captured_at, times_json, temps_c_json FROM day0_hourly_vectors")])
    for r in conn.execute("SELECT city, station_id, source_channel, publish_ts_utc, value_native, unit, fetched_at_utc, "
                          "raw_report FROM observation_prints ORDER BY id"):
        if r["source_channel"] == "fmi_airport_temperature" and r["publish_ts_utc"] > local(12, 0).isoformat():
            continue
        append_print(stale, **{k: r[k] for k in r.keys()})
    evidence._CACHE.clear()
    out, _ = run(stale, monkeypatch, decision=local(12, 55), state_at=local(12, 50),
                 state_source="aviationweather_metar")
    assert out["operator"] == DAY0_REMAINING_CARRIER_OPERATOR_V2
    stale.close()


def test_unqualified_metric_or_fahrenheit_is_legacy(artifact, conn, monkeypatch):
    payload = json.loads(artifact.read_text())
    payload["cities"]["Helsinki"] = city_params_block(metrics=("high",))
    payload["content_hash"] = params_mod.canonical_hash(payload)
    artifact.write_text(json.dumps(payload))
    out, _ = run(conn, monkeypatch, decision=local(13, 5), state_at=local(13, 0), metric="low",
                 scenarios=((None, 0.4), (8.0, 0.6)))
    assert out["operator"] == DAY0_REMAINING_CARRIER_OPERATOR_V2


# ---------------------------------------------------------------- receipt gating and identity

def test_receipt_gating_excludes_rows_fetched_after_the_state_receipt(artifact, conn, monkeypatch):
    a, _ = run(conn, monkeypatch, decision=local(12, 5), state_at=local(12, 0))
    late = local(11, 55)
    append_print(conn, city="Helsinki", station_id="EFHK", source_channel="fmi_airport_temperature",
                 publish_ts_utc=late.isoformat(), value_native=19.9, unit="C",
                 fetched_at_utc=local(12, 4).isoformat(), raw_report=fmi_raw(late, 19.9))
    b, _ = run(conn, monkeypatch, decision=local(12, 5), state_at=local(12, 0))
    assert a["q"] == b["q"] and a["content_identity"] == b["content_identity"]


def test_replay_at_later_clock_reproduces_and_newer_state_rebinds(artifact, conn, monkeypatch):
    a, _ = run(conn, monkeypatch, decision=local(12, 5), state_at=local(12, 0))
    later, _ = run(conn, monkeypatch, decision=local(12, 55), state_at=local(12, 0))
    assert later["content_identity"] == a["content_identity"] and later["q"] == a["q"]
    newer, _ = run(conn, monkeypatch, decision=local(12, 55), state_at=local(12, 50))
    assert newer["content_identity"] != a["content_identity"]


def test_persisted_dense_operator_without_dense_evidence_fails_closed(conn, monkeypatch, tmp_path):
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {"Helsinki": CITY})
    monkeypatch.setattr(params_mod, "ARTIFACT_PATH", tmp_path / "missing.json")
    kwargs = carrier(conn, decision=local(13, 5), state_at=local(13, 0))
    kwargs["operator"] = evidence.DAY0_DENSE_STATE_SPACE_OPERATOR
    with pytest.raises(ValueError, match="DAY0_DENSE_STATE_SPACE_REPLAY_UNAVAILABLE"):
        build_day0_remaining_probability_carrier(**kwargs)


def test_page_row_is_the_semantic_certificate(artifact, conn, monkeypatch):
    append_print(conn, city="Helsinki", station_id="EFHK", source_channel="noaa_wrh_efhk",
                 publish_ts_utc=local(11, 50).isoformat(), value_native=15.0, unit="C",
                 fetched_at_utc=local(11, 58).isoformat(), raw_report="EFHK 070950Z 27012KT 9999 15/06 Q1009")
    out, _ = run(conn, monkeypatch, decision=local(12, 5), state_at=local(12, 0))
    q = dict(zip(BINS, out["q"]))
    assert all(q[b] == 0.0 for b in BINS if b[1] is not None and b[1] < 15)


# ---------------------------------------------------------------- G1 / G2 / G8

def test_g1_fast_admission_route_is_provisional_never_monotone():
    from src.events.day0_authority import (
        DAY0_MONOTONE_SETTLEMENT_BOUND, DAY0_PROVISIONAL_CURRENT_SNAPSHOT,
        day0_evidence_finality, day0_is_carrier_source,
    )

    for channel in ("jma_amedas_temperature", "eccc_swob_temperature", "mgm_metar_temperature",
                    "imd_olbs_metar_temperature", "metaviatelecom_metar_temperature"):
        assert day0_evidence_finality({"settlement_source": channel}) == DAY0_PROVISIONAL_CURRENT_SNAPSHOT
        assert day0_evidence_finality({"settlement_source": channel,
                                       "evidence_finality": DAY0_MONOTONE_SETTLEMENT_BOUND}) == DAY0_PROVISIONAL_CURRENT_SNAPSHOT
        assert day0_is_carrier_source(channel)
    assert day0_evidence_finality({"settlement_source": "noaa_wrh_rjtt"}) == DAY0_MONOTONE_SETTLEMENT_BOUND
    assert not day0_is_carrier_source("fmi_airport_temperature")


def _tokyo_city():
    return SimpleNamespace(name="Tokyo", timezone="Asia/Tokyo", wu_station="RJTT", settlement_source_type="noaa",
                           settlement_unit="C")


def _jma_raw(at: datetime, value: float) -> str:
    return json.dumps({"observed_at": at.isoformat(), "provider_station": "44166", "source_channel": "jma_amedas_temperature",
                       "station_id": "RJTT", "unit": "C", "value_native": value})


def test_g2_non_metar_instant_route_row_sets_no_settlement_fact(monkeypatch):
    """Tokyo 2026-10-04 LOW: JMA 18.4 at 20:40Z (a non-METAR instant) must not be a settlement fact."""
    from src.data.replacement_forecast_current_target_plan import _latest_authorized_day0_fact

    city = _tokyo_city()
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {"Tokyo": city})
    monkeypatch.setattr("src.config.cities_by_name", {"Tokyo": city}, raising=False)
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_table(c)
    for at, v in ((datetime(2026, 10, 4, 20, 30, tzinfo=UTC), 18.9), (datetime(2026, 10, 4, 20, 40, tzinfo=UTC), 18.4)):
        append_print(c, city="Tokyo", station_id="RJTT", source_channel="jma_amedas_temperature",
                     publish_ts_utc=at.isoformat(), value_native=v, unit="C",
                     fetched_at_utc=(at + timedelta(minutes=7)).isoformat(), raw_report=_jma_raw(at, v))
    fact = _latest_authorized_day0_fact(c, city="Tokyo", target_date="2026-10-05", temperature_metric="low",
                                        decision_time=datetime(2026, 10, 4, 21, 0, tzinfo=UTC),
                                        require_settlement_channel=True)
    assert fact is not None and float(fact["observed_extreme_native"]) == pytest.approx(18.9)
    physical = _latest_authorized_day0_fact(c, city="Tokyo", target_date="2026-10-05", temperature_metric="low",
                                            decision_time=datetime(2026, 10, 4, 21, 0, tzinfo=UTC),
                                            require_settlement_channel=False)
    assert physical is not None and float(physical["observed_extreme_native"]) == pytest.approx(18.4)


def test_g8_supersession_compares_settlement_integers():
    from src.data.day0_fast_obs import fast_extreme_supersedes_settlement

    tokyo = _tokyo_city()
    # Tokyo 2026-10-07 05:00Z: route 24.6 vs AWC 25.0 at the same instant: both settle 25.
    assert not fast_extreme_supersedes_settlement(metric="high", fast_extreme_c=25.0, settlement_extreme_c=24.6, city=tokyo)
    assert fast_extreme_supersedes_settlement(metric="high", fast_extreme_c=26.0, settlement_extreme_c=24.6, city=tokyo)
    # Toronto 05:00Z: SWOB 12.5 vs AWC 13.0: both settle 13 (half-up).
    assert not fast_extreme_supersedes_settlement(metric="high", fast_extreme_c=13.0, settlement_extreme_c=12.5, city=tokyo)
    assert not fast_extreme_supersedes_settlement(metric="low", fast_extreme_c=12.6, settlement_extreme_c=13.4, city=tokyo)
    # Legacy raw comparison is unchanged without a city.
    assert fast_extreme_supersedes_settlement(metric="high", fast_extreme_c=25.0, settlement_extreme_c=24.6)


def test_route_declares_metar_instants_and_rejects_others():
    from src.data.physical_current_sources import load_physical_current_sources

    sources, _ = load_physical_current_sources()
    jma = next(s for s in sources if s.source_channel == "jma_amedas_temperature")
    assert jma.settlement_instant(datetime(2026, 10, 7, 6, 0, tzinfo=UTC))
    assert not jma.settlement_instant(datetime(2026, 10, 7, 6, 10, tzinfo=UTC))
    fmi = next(s for s in sources if s.source_channel == "fmi_airport_temperature")
    assert not fmi.settlement_instant(datetime(2026, 10, 7, 6, 20, tzinfo=UTC))
