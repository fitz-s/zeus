# Created: 2026-06-29
# Lifecycle: created=2026-06-29; last_reviewed=2026-09-30; last_reused=2026-09-30
# Purpose: Lock config-driven station forecast ingest, dual-metric HKO capture, and reseed wiring.
# Reuse: Run for station forecast source, dispatcher, cadence, or replacement reseed changes.
# Last reused/audited: 2026-09-30
# Authority basis: operator directive "加数据" (add CWA/HKO station-forecast data to the
#   live forecast cycle); src/data/station_forecast_adapter.py single_runs persist contract;
#   config/station_forecast_sources.json adapter_kind dispatch seam.
"""Config-driven live station-forecast ingest dispatcher wiring.

The adapter already exposes per-source live ingest functions (``ingest_cwa_township_live``,
``ingest_hko_fnd_live``). The MISSING seam is the one the live download cycle calls: a
config-driven dispatcher that ingests every ENABLED station source, routes by ``adapter_kind``,
and is per-source fail-soft so one provider outage never starves the others.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.data import station_forecast_adapter as adapter


def _write_config(root: Path, sources: dict) -> None:
    cfg_dir = root / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "station_forecast_sources.json").write_text(
        json.dumps({"sources": sources}), encoding="utf-8"
    )


_CWA_SPEC = {
    "enabled": True,
    "adapter_kind": "cwa_township_json",
    "city": "Taipei",
    "metric": "high",
    "location_name": "松山區",
    "element_name": "最高溫度",
    "endpoint": "https://example.invalid/cwa",
}
_HKO_SPEC = {
    "enabled": True,
    "adapter_kind": "hko_fnd_json",
    "city": "Hong Kong",
    "metrics": ["high", "low"],
    "endpoint": "https://example.invalid/hko",
}
_CWA_HOURLY_LOW_SPEC = {
    "enabled": True,
    "adapter_kind": "cwa_township_hourly_xml",
    "city": "Taipei",
    "metric": "low",
    "location_name": "松山區",
    "location_geocode": "63000010",
    "location_latitude": 25.051608,
    "location_longitude": 121.568983,
    "endpoint": "https://example.invalid/cwa-fileapi",
}
_CWA_HOURLY_HIGH_SPEC = {
    **_CWA_HOURLY_LOW_SPEC,
    "metric": "high",
    "shared_fetch_group": "cwa_township_hourly_061",
}
_CWA_HOURLY_LOW_SHARED_SPEC = {
    **_CWA_HOURLY_LOW_SPEC,
    "shared_fetch_group": "cwa_township_hourly_061",
}

_CONN = object()  # sentinel; ingest fns are monkeypatched so the conn is never touched


def test_dispatch_routes_only_enabled_sources_by_adapter_kind(monkeypatch, tmp_path):
    calls: list[str] = []
    monkeypatch.setattr(
        adapter, "ingest_cwa_township_live",
        lambda conn, **kw: (calls.append("cwa"), 7)[1],
    )
    monkeypatch.setattr(
        adapter, "ingest_hko_fnd_live",
        lambda conn, **kw: (calls.append("hko"), 9)[1],
    )
    _write_config(tmp_path, {"cwa_township": dict(_CWA_SPEC), "hko_fnd": {**_HKO_SPEC, "enabled": False}})

    result = adapter.ingest_enabled_station_sources_live(_CONN, root=tmp_path)

    assert result == {"cwa_township": 7}
    assert calls == ["cwa"]  # disabled hko never dispatched


def test_dispatch_passes_city_and_metric_from_spec(monkeypatch, tmp_path):
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        adapter, "ingest_cwa_township_live",
        lambda conn, **kw: (seen.update(kw), 3)[1],
    )
    _write_config(tmp_path, {"cwa_township": dict(_CWA_SPEC)})

    adapter.ingest_enabled_station_sources_live(_CONN, root=tmp_path)

    assert seen.get("city") == "Taipei"
    assert seen.get("metric") == "high"


def test_dispatch_routes_ungrouped_hourly_low_product_with_township_identity(monkeypatch, tmp_path):
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        adapter,
        "ingest_cwa_township_hourly_extrema_live",
        lambda conn, **kw: (seen.update(kw), 1)[1],
    )
    _write_config(tmp_path, {"cwa_township_hourly_low": dict(_CWA_HOURLY_LOW_SPEC)})

    assert adapter.ingest_enabled_station_sources_live(_CONN, root=tmp_path) == {
        "cwa_township_hourly_low": 1
    }
    assert seen == {
        "city": "Taipei",
        "metrics": ("low",),
        "location_name": "松山區",
        "location_geocode": "63000010",
        "location_latitude": 25.051608,
        "location_longitude": 121.568983,
        "endpoint": "https://example.invalid/cwa-fileapi",
    }


def test_dispatch_passes_both_hko_metrics_from_spec(monkeypatch, tmp_path):
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        adapter,
        "ingest_hko_fnd_live",
        lambda conn, **kw: (seen.update(kw), 18)[1],
    )
    _write_config(tmp_path, {"hko_fnd": dict(_HKO_SPEC)})

    adapter.ingest_enabled_station_sources_live(_CONN, root=tmp_path)

    assert seen["city"] == "Hong Kong"
    assert seen["metrics"] == ("high", "low")


def test_dispatch_can_poll_only_one_due_source(monkeypatch, tmp_path):
    calls: list[str] = []
    monkeypatch.setattr(
        adapter,
        "ingest_cwa_township_live",
        lambda conn, **kw: (calls.append("cwa"), 7)[1],
    )
    monkeypatch.setattr(
        adapter,
        "ingest_hko_fnd_live",
        lambda conn, **kw: (calls.append("hko"), 9)[1],
    )
    _write_config(
        tmp_path,
        {"cwa_township": dict(_CWA_SPEC), "hko_fnd": dict(_HKO_SPEC)},
    )

    result = adapter.ingest_enabled_station_sources_live(
        _CONN,
        root=tmp_path,
        source_ids=("hko_fnd",),
    )

    assert result == {"hko_fnd": 9}
    assert calls == ["hko"]


def test_hko_multi_metric_ingest_fetches_once_and_persists_both(monkeypatch):
    payload = {
        "updateTime": "2026-07-23T11:30:00+08:00",
        "weatherForecast": [
            {
                "forecastDate": "20260724",
                "forecastMaxtemp": {"value": 33, "unit": "C"},
                "forecastMintemp": {"value": 28, "unit": "C"},
            },
            {
                "forecastDate": "20260725",
                "forecastMaxtemp": {"value": 34, "unit": "C"},
                "forecastMintemp": {"value": 27, "unit": "C"},
            },
        ],
    }
    fetches = {"count": 0}
    captured: list[adapter.StationForecastRow] = []

    def _fetch(**_kwargs):
        fetches["count"] += 1
        return adapter.HkoFndProduct(json.dumps(payload).encode(), "2026-07-23T03:35:00+00:00")

    def _persist(_conn, rows, **_kwargs):
        captured.extend(rows)
        return len(rows)

    monkeypatch.setattr(adapter, "fetch_hko_fnd_product", _fetch)
    monkeypatch.setattr(adapter, "persist_station_forecast_rows", _persist)

    written = adapter.ingest_hko_fnd_live(
        _CONN,
        metrics=("high", "low"),
    )

    assert fetches["count"] == 1
    assert written == len(captured)
    assert {row.metric for row in captured} == {"high", "low"}
    assert {
        (row.target_date, row.metric) for row in captured
    } == {
        (row.target_date, metric)
        for row in adapter.parse_hko_fnd_payload(payload, metric="high")
        for metric in ("high", "low")
    }
    values = {
        (row.target_date, row.metric): row.forecast_value_c
        for row in captured
    }
    assert values == {
        ("2026-07-24", "high"): 33.0,
        ("2026-07-24", "low"): 28.0,
        ("2026-07-25", "high"): 34.0,
        ("2026-07-25", "low"): 27.0,
    }


@pytest.mark.parametrize("metrics", [(), ("high", "high"), ("high", "median"), "high"])
def test_hko_multi_metric_ingest_rejects_invalid_metrics_before_fetch(
    monkeypatch,
    metrics,
):
    monkeypatch.setattr(
        adapter,
        "fetch_hko_fnd_product",
        lambda **_kwargs: pytest.fail("invalid metrics must fail before network I/O"),
    )

    with pytest.raises(ValueError):
        adapter.ingest_hko_fnd_live(_CONN, metrics=metrics)


# ---------------------------------------------------------------------------
# CWA F-D0047-061: a complete raw XML product is required for a LOW.  The
# JSON REST projection omits DatasetInfo IssueTime/Update, so fixtures exercise
# the fileapi shape and the separate publisher/local clocks directly.
# ---------------------------------------------------------------------------
def _hourly_low_xml(
    *,
    issue_time: str = "2026-07-23T17:00:00+08:00",
    update_time: str = "2026-07-23T18:14:00+08:00",
    sent_time: str | None = "2026-07-23T18:14:00+08:00",
    points: list[tuple[str, str]] | None = None,
    element_name: str = "溫度",
    location_name: str = "松山區",
    location_geocode: str = "63000010",
    location_latitude: str = "25.051608",
    location_longitude: str = "121.568983",
) -> bytes:
    samples = points or [
        (f"2026-07-24T{hour:02d}:00:00+08:00", str(29 - (hour % 7)))
        for hour in range(24)
    ]
    sent = "" if sent_time is None else f"<Sent>{sent_time}</Sent>"
    times = "".join(
        "<Time><DataTime>" + when + "</DataTime><ElementValue><Temperature>"
        + value + "</Temperature></ElementValue></Time>"
        for when, value in samples
    )
    return (
        "<cwaopendata>"
        + sent
        + "<Dataid>D0047-061</Dataid>"
        + "<Dataset><DatasetInfo><IssueTime>" + issue_time
        + "</IssueTime><Update>" + update_time
        + "</Update></DatasetInfo><Locations><Location><LocationName>"
        + location_name + "</LocationName><Geocode>" + location_geocode
        + "</Geocode><Latitude>" + location_latitude + "</Latitude><Longitude>"
        + location_longitude + "</Longitude><WeatherElement><ElementName>" + element_name
        + "</ElementName>" + times
        + "</WeatherElement></Location></Locations></Dataset></cwaopendata>"
    ).encode("utf-8")


def _hourly_product(*, captured_at="2026-07-23T10:15:00+00:00", **kwargs) -> adapter.CwaHourlyProduct:
    return adapter.parse_cwa_township_hourly_product(
        _hourly_low_xml(**kwargs), captured_at=captured_at
    )


def test_hourly_low_requires_complete_unique_finite_whole_hour_dplus1_grid():
    product = _hourly_product()
    rows = adapter.parse_cwa_township_hourly_low_product(product)

    assert len(rows) == 1
    row = rows[0]
    assert (row.model, row.city, row.metric, row.target_date, row.lead_days) == (
        "cwa_township_hourly_low", "Taipei", "low", "2026-07-24", 1,
    )
    assert row.forecast_value_c == 23.0
    assert row.source_cycle_time == "2026-07-23T10:14:00+00:00"
    assert row.source_available_at == "2026-07-23T10:15:00+00:00"


def test_hourly_high_uses_the_same_complete_dplus1_grid_with_max_aggregation():
    rows = adapter.parse_cwa_township_hourly_extreme_product(
        _hourly_product(),
        metric="high",
        model="cwa_township_hourly_high",
    )

    assert len(rows) == 1
    assert (rows[0].model, rows[0].metric, rows[0].forecast_value_c) == (
        "cwa_township_hourly_high", "high", 29.0,
    )


def test_hourly_cross_midnight_revision_keeps_complete_issue_dplus1_identity():
    product = adapter.parse_cwa_township_hourly_product(
        _hourly_low_xml(
            issue_time="2026-07-23T23:00:00+08:00",
            update_time="2026-07-24T00:14:00+08:00",
            sent_time="2026-07-24T00:14:00+08:00",
        ),
        captured_at="2026-07-24T00:15:00+08:00",
    )

    rows = adapter.parse_cwa_township_hourly_extreme_product(
        product,
        metric="high",
        model="cwa_township_hourly_high",
    )

    assert [(row.target_date, row.lead_days, row.source_cycle_time, row.source_available_at) for row in rows] == [
        (
            "2026-07-24",
            1,
            "2026-07-23T16:14:00+00:00",
            "2026-07-23T16:15:00+00:00",
        )
    ]


@pytest.mark.parametrize(
    ("points", "element_name"),
    [
        ([(f"2026-07-24T{hour:02d}:00:00+08:00", "20") for hour in range(23)], "溫度"),
        ([(f"2026-07-24T{hour:02d}:00:00+08:00", "20") for hour in range(24)] + [("2026-07-24T02:00:00+08:00", "19")], "溫度"),
        ([(f"2026-07-24T{hour:02d}:30:00+08:00", "20") for hour in range(24)], "溫度"),
        ([(f"2026-07-24T{hour:02d}:00:00+08:00", "NaN" if hour == 9 else "20") for hour in range(24)], "溫度"),
        ([(f"2026-07-24T{hour:02d}:00:00+08:00", "20") for hour in range(24)], "最低溫度"),
    ],
)
def test_hourly_low_rejects_incomplete_ambiguous_or_wrong_product(points, element_name):
    # A MinT-only/cross-midnight 12-hour product cannot be re-labelled LOW.
    assert adapter.parse_cwa_township_hourly_low_product(
        _hourly_product(points=points, element_name=element_name)
    ) == ()


def test_hourly_low_rejects_unexpected_township_identity():
    with pytest.raises(ValueError, match="exactly one configured township"):
        adapter.parse_cwa_township_hourly_low_product(
            _hourly_product(location_name="大安區", location_geocode="63000011")
        )


def test_hourly_low_rejects_unexpected_township_coordinates():
    with pytest.raises(ValueError, match="coordinates do not match"):
        adapter.parse_cwa_township_hourly_low_product(
            _hourly_product(location_latitude="25.000000")
        )


def _hourly_schema_conn(db_path=None) -> sqlite3.Connection:
    from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema

    conn = sqlite3.connect(":memory:" if db_path is None else db_path)
    ensure_replacement_forecast_live_schema(conn)
    return conn


def _station_body_writer(monkeypatch, provider, db_path=None):
    """Only HTTP and wall clock are fake; producer and authority readers are real."""
    import urllib.request
    from datetime import datetime, timezone

    now = datetime(2026, 7, 23, 10, 15, tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    if provider == "hko":
        body = (json.dumps({"updateTime": "2026-07-23T18:14:00+08:00", "weatherForecast": [
            {"forecastDate": "20260724", "forecastMaxtemp": {"value": 33, "unit": "C"},
             "forecastMintemp": {"value": 27, "unit": "C"}},
            {"forecastDate": "20260725", "forecastMaxtemp": {"value": 34, "unit": "C"},
             "forecastMintemp": {"value": 28, "unit": "C"}},
        ]}, indent=2) + "\n").encode()
    else:
        body = _hourly_low_xml()
    requests = []
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            pass
        def read(self):
            return body
    def http(request, **_kwargs):
        requests.append(request.full_url)
        return Response()
    monkeypatch.setattr(urllib.request, "urlopen", http)
    monkeypatch.setattr(adapter, "datetime", Clock)
    monkeypatch.setattr("src.data.bayes_precision_fusion_download.datetime", Clock)
    conn = _hourly_schema_conn(db_path)
    if provider == "hko":
        assert adapter.ingest_hko_fnd_live(conn, metrics=("high", "low")) == 4
        city, models = "Hong Kong", {"high": "hko_fnd", "low": "hko_fnd"}
    else:
        assert adapter.ingest_cwa_township_hourly_extrema_live(conn, api_key="private-test-key") == 2
        city, models = "Taipei", {metric: f"cwa_township_hourly_{metric}" for metric in ("high", "low")}
    assert len(requests) == 1
    return conn, body, city, models


def _station_grid_cohort(
    monkeypatch, conn, db_path, city, *, target_dates=("2026-07-24", "2026-07-25"),
    cycle=None, captured=None,
):
    """Actual grid producer supplies the second provider family for a cohort."""
    from datetime import date, datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    from src.config import runtime_cities_by_name
    from src.data import bayes_precision_fusion_download as dl

    captured = captured or datetime(2026, 7, 23, 10, 15, tzinfo=timezone.utc)
    cycle = cycle or captured.replace(hour=6, minute=0)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return captured.astimezone(tz) if tz else captured.replace(tzinfo=None)
    def fetch(_url, params, **kwargs):
        start = datetime.fromisoformat(min(target_dates))
        hours = (date.fromisoformat(max(target_dates)) - start.date()).days * 24 + 24
        payload = {"latitude": float(str(params["latitude"]).split(",")[0]),
                   "longitude": float(str(params["longitude"]).split(",")[0]), "elevation": 32.0,
                   "timezone": str(params["timezone"]).split(",")[0],
                   "utc_offset_seconds": int(start.replace(tzinfo=ZoneInfo(str(params["timezone"]).split(",")[0])).utcoffset().total_seconds()),
                   "hourly_units": {"temperature_2m": "°C"},
                   "hourly": {"time": [(start + timedelta(hours=i)).isoformat(timespec="minutes") for i in range(hours)],
                              "temperature_2m": [26.0 + i % 7 for i in range(hours)]}}
        body = (json.dumps(payload, indent=2) + "\n").encode()
        kwargs["capture_entity_body"](body, captured.timestamp())
        kwargs["capture_network_response"](body, captured.timestamp(), {"content-type": "application/json"})
        return json.loads(body)
    dl._SINGLE_RUNS_PAYLOAD_CACHE.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEX.clear()
    monkeypatch.setattr(dl, "datetime", Clock)
    monkeypatch.setattr("src.data.openmeteo_client.fetch", fetch)
    point = runtime_cities_by_name()[city]
    models = ("icon_global", "ukmo_global_deterministic_10km")
    targets = [dl.BayesPrecisionFusionDownloadTarget(
        city=city, target_date=target, metric=metric, latitude=point.lat,
        longitude=point.lon, timezone_name=point.timezone,
        lead_days=(date.fromisoformat(target) - captured.astimezone(ZoneInfo(point.timezone)).date()).days,
    ) for target in target_dates for metric in ("high", "low")]
    conn.commit()
    report = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db_path, cycle=cycle, targets=targets, models=models,
        frozen_source_runs={model: (cycle, captured) for model in models},
        include_previous_runs=False, prune_after=False,
    )
    assert report["written_row_count"] == len(targets) * len(models)
    return models


@pytest.mark.parametrize("provider", ("hko", "cwa"))
def test_actual_station_http_body_writer_serves_both_typed_metrics_and_frontier(monkeypatch, tmp_path, provider):
    import hashlib
    from src.data.replacement_current_value_serving import (
        current_value_serving_schema, read_current_instrument_values,
        read_current_instrument_frontier_identity, read_freshest_coherent_instrument_values,
    )

    db_path = tmp_path / "forecast.db"
    conn, body, city, models = _station_body_writer(monkeypatch, provider, db_path)
    digest = hashlib.sha256(body).hexdigest()
    artifacts = conn.execute(
        "SELECT artifact_path, sha256, data_version, source_cycle_time, source_available_at, "
        "captured_at, request_url, request_params_json, artifact_metadata_json FROM raw_forecast_artifacts"
    ).fetchall()
    assert len(artifacts) == (1 if provider == "hko" else 2)
    for row in artifacts:
        assert Path(row[0]).read_bytes() == body and row[1] == digest
        assert row[2:6] == ("station_forecast_entity_body_v1", "2026-07-23T10:14:00+00:00",
                             "2026-07-23T10:15:00+00:00", "2026-07-23T10:15:00+00:00")
        assert "private-test-key" not in row[6] + row[7] + row[8]
        evidence = json.loads(row[8])["station_response"]
        assert len(evidence["items"]) == (4 if provider == "hko" else 2)
        for item in evidence["items"]:
            assert adapter.reextract_station_response_value(body, item) == item["forecast_value_c"]
    grid_models = _station_grid_cohort(monkeypatch, conn, db_path, city)
    for metric, model in models.items():
        dates = ("2026-07-24", "2026-07-25") if provider == "hko" else ("2026-07-24",)
        for target_date in dates:
            scope = dict(city=city, metric=metric, target_date=target_date)
            timing = dict(source_cycle_time_iso="2026-07-23T10:14:00+00:00",
                          decision_time_iso="2026-07-23T10:16:00+00:00")
            served = read_current_instrument_values(conn, **scope, **timing, include_station_sources=True)
            assert set(served) == {model, *grid_models}
            coherent = read_freshest_coherent_instrument_values(
                conn, **scope, decision_time_iso=timing["decision_time_iso"], models=(model, *grid_models), cohort_window_hours=6,
                include_station_sources=True,
            )
            assert set(coherent) == {model, *grid_models}
            frontier = dict(read_current_instrument_frontier_identity(
                conn, **scope, decision_time_iso=timing["decision_time_iso"], models=(model,),
                schema=current_value_serving_schema(conn),
            ))
            assert frontier[model] == served[model].raw_model_forecast_id
    conn.close()


@pytest.mark.parametrize("provider", ("hko", "cwa"))
@pytest.mark.parametrize("damage", ("city", "station_id", "metric", "quantity", "source_cycle_time", "source_available_at"))
def test_original_station_body_cannot_reproduce_foreign_product_or_reissued_clock(monkeypatch, provider, damage):
    conn, body, _city, _models = _station_body_writer(monkeypatch, provider)
    metadata = json.loads(conn.execute("SELECT artifact_metadata_json FROM raw_forecast_artifacts LIMIT 1").fetchone()[0])
    proof = dict(metadata["station_response"]["items"][0])
    assert adapter.reextract_station_response_value(body, proof) is not None
    proof[damage] = {"city": "Shanghai", "station_id": "OTHER", "metric": "low" if proof["metric"] == "high" else "high",
                     "quantity": "settlement_daily_extreme", "source_cycle_time": "2026-07-23T10:16:00+00:00",
                     "source_available_at": "2026-07-23T10:16:00+00:00"}[damage]
    assert adapter.reextract_station_response_value(body, proof) is None
    conn.close()


@pytest.mark.parametrize("provider", ("hko", "cwa"))
def test_same_station_body_recapture_does_not_renew_possession_or_source_clock(monkeypatch, provider):
    from datetime import datetime, timezone

    conn, _body, _city, _models = _station_body_writer(monkeypatch, provider)
    before = conn.execute(
        "SELECT model,metric,source_cycle_time,source_available_at,captured_at,raw_sha256,artifact_id "
        "FROM raw_model_forecasts ORDER BY model,metric,target_date"
    ).fetchall()
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 7, 23, 11, 15, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    monkeypatch.setattr(adapter, "datetime", Later)
    written = (adapter.ingest_hko_fnd_live(conn, metrics=("high", "low")) if provider == "hko"
               else adapter.ingest_cwa_township_hourly_extrema_live(conn, api_key="private-test-key"))
    assert written == 0
    assert conn.execute(
        "SELECT model,metric,source_cycle_time,source_available_at,captured_at,raw_sha256,artifact_id "
        "FROM raw_model_forecasts ORDER BY model,metric,target_date"
    ).fetchall() == before
    assert {row[0] for row in conn.execute("SELECT captured_at FROM raw_forecast_artifacts")} == {
        "2026-07-23T10:15:00+00:00"
    }
    conn.close()


@pytest.mark.parametrize("first", ("high", "low"))
def test_hko_single_metric_then_sibling_reuses_complete_body_proof_without_clock_renewal(monkeypatch, first):
    """A full possessed body proves both lawful quantities, not only first dispatch."""
    import urllib.request
    from datetime import datetime, timedelta, timezone
    from src.data.replacement_current_value_serving import read_current_instrument_values

    initial = datetime(2026, 7, 23, 10, 15, tzinfo=timezone.utc)
    clock = [initial]
    body = (json.dumps({"updateTime":"2026-07-23T10:14:00+00:00","weatherForecast":[{
        "forecastDate":"20260724","forecastMaxtemp":{"value":33,"unit":"C"},
        "forecastMintemp":{"value":27,"unit":"C"}}]},indent=2)+"\n").encode()
    class Response:
        def __enter__(self): return self
        def __exit__(self,*_args): pass
        def read(self): return body
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None): return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)
    monkeypatch.setattr(urllib.request,"urlopen",lambda *_a,**_k: Response())
    monkeypatch.setattr(adapter,"datetime",Clock)
    monkeypatch.setattr("src.data.bayes_precision_fusion_download.datetime",Clock)
    conn = _hourly_schema_conn()
    try:
        assert adapter.ingest_hko_fnd_live(conn,metrics=(first,)) == 1
        artifact = conn.execute("SELECT * FROM raw_forecast_artifacts").fetchone()
        columns = [row[1] for row in conn.execute("PRAGMA table_info(raw_forecast_artifacts)")]
        proof = dict(zip(columns,artifact))
        assert Path(proof["artifact_path"]).read_bytes() == body
        assert {item["metric"] for item in json.loads(proof["artifact_metadata_json"])["station_response"]["items"]} == {"high","low"}
        second = "low" if first == "high" else "high"
        clock[0] = initial+timedelta(hours=1)
        assert adapter.ingest_hko_fnd_live(conn,metrics=(second,)) == 1
        assert conn.execute("SELECT * FROM raw_forecast_artifacts").fetchone() == artifact
        rows = conn.execute("SELECT metric,source_cycle_time,source_available_at,captured_at,artifact_id,recorded_at "
                            "FROM raw_model_forecasts ORDER BY metric").fetchall()
        assert {row[4] for row in rows} == {proof["artifact_id"]}
        assert {row[1:4] for row in rows} == {("2026-07-23T10:14:00+00:00",initial.isoformat(),initial.isoformat())}
        for metric in (first,second):
            for cutoff, expected in ((initial+timedelta(minutes=1),metric==first),
                                      (clock[0]+timedelta(minutes=1),True)):
                served = read_current_instrument_values(conn,city="Hong Kong",metric=metric,target_date="2026-07-24",
                    include_station_sources=True,source_cycle_time_iso="2026-07-23T10:14:00+00:00",
                    decision_time_iso=cutoff.isoformat())
                assert ("hko_fnd" in served) is expected
        clock[0] += timedelta(hours=1)
        assert adapter.ingest_hko_fnd_live(conn,metrics=("high","low")) == 0
        assert conn.execute("SELECT * FROM raw_forecast_artifacts").fetchone() == artifact
        assert conn.execute("SELECT metric,source_cycle_time,source_available_at,captured_at,artifact_id,recorded_at "
                            "FROM raw_model_forecasts ORDER BY metric").fetchall() == rows
    finally:
        conn.close()


@pytest.mark.parametrize("provider", ("hko", "cwa"))
def test_shared_station_selector_and_frontier_reject_corrupt_body_without_hiding_sibling(monkeypatch, provider):
    from src.data.replacement_current_value_serving import (
        current_value_serving_schema, read_current_instrument_values, read_current_instrument_frontier_identity,
    )

    conn, _body, city, models = _station_body_writer(monkeypatch, provider)
    scope = dict(city=city, metric="high", target_date="2026-07-24")
    timing = dict(source_cycle_time_iso="2026-07-23T10:14:00+00:00", decision_time_iso="2026-07-23T10:16:00+00:00")
    assert models["high"] in read_current_instrument_values(conn, **scope, **timing, include_station_sources=True)
    artifact_id = conn.execute("SELECT artifact_id FROM raw_model_forecasts WHERE metric='high' LIMIT 1").fetchone()[0]
    raw = conn.execute("SELECT artifact_metadata_json FROM raw_forecast_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()[0]
    metadata = json.loads(raw)
    for item in metadata["station_response"]["items"]:
        if item["metric"] == "high":
            item["quantity"] = "township_hourly_sample_min" if provider == "cwa" else "agency_daily_forecast_low"
    conn.execute("UPDATE raw_forecast_artifacts SET artifact_metadata_json=? WHERE artifact_id=?", (json.dumps(metadata), artifact_id))
    assert read_current_instrument_values(conn, **scope, **timing, include_station_sources=True) == {}
    assert dict(read_current_instrument_frontier_identity(
        conn, **scope, decision_time_iso=timing["decision_time_iso"], models=(models["high"],),
        schema=current_value_serving_schema(conn),
    ))[models["high"]] is None
    assert models["low"] in read_current_instrument_values(
        conn, city=city, metric="low", target_date="2026-07-24", **timing, include_station_sources=True,
    )
    conn.close()


@pytest.mark.parametrize("bad_metric", ("high", "low"))
@pytest.mark.parametrize("value,unit,valid", ((True, "C", False), (float("nan"), "C", False),
                                            (33, "°C", True), (33, "CELSIUS", True),
                                            (33, "F", False), (33, None, False)))
def test_normal_hko_dual_metric_writer_preserves_valid_sibling_and_original_bad_row(monkeypatch, bad_metric, value, unit, valid):
    import hashlib
    import urllib.request
    from datetime import datetime, timezone
    from src.data.replacement_current_value_serving import read_current_instrument_values

    temperatures = {"high": {"value": 33, "unit": "C"}, "low": {"value": 27, "unit": "C"}}
    temperatures[bad_metric] = {"value": value, "unit": unit}
    body = (json.dumps({"updateTime": "2026-07-23T18:14:00+08:00", "weatherForecast": [{
        "forecastDate": "20260724", "forecastMaxtemp": temperatures["high"], "forecastMintemp": temperatures["low"],
    }]}, indent=2) + "\n").encode()
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            pass
        def read(self):
            return body
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            when = datetime(2026, 7, 23, 10, 15, tzinfo=timezone.utc)
            return when.astimezone(tz) if tz else when.replace(tzinfo=None)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(adapter, "datetime", Clock)
    monkeypatch.setattr("src.data.bayes_precision_fusion_download.datetime", Clock)
    conn = _hourly_schema_conn()
    assert adapter.ingest_hko_fnd_live(conn, metrics=("high", "low")) == (2 if valid else 1)
    expected = {"high", "low"} if valid else {"low" if bad_metric == "high" else "high"}
    assert {row[0] for row in conn.execute("SELECT metric FROM raw_model_forecasts")} == expected
    artifact = conn.execute("SELECT artifact_path,sha256,artifact_metadata_json FROM raw_forecast_artifacts").fetchone()
    assert Path(artifact[0]).read_bytes() == body and artifact[1] == hashlib.sha256(body).hexdigest()
    assert {item["metric"] for item in json.loads(artifact[2])["station_response"]["items"]} == expected
    for metric in ("high", "low"):
        served = read_current_instrument_values(
            conn, city="Hong Kong", metric=metric, target_date="2026-07-24", include_station_sources=True,
            source_cycle_time_iso="2026-07-23T10:14:00+00:00", decision_time_iso="2026-07-23T10:16:00+00:00",
        )
        assert ("hko_fnd" in served) is (metric in expected)
    conn.close()


def test_hourly_low_same_publisher_revision_is_idempotent_and_retains_raw_hash(monkeypatch):
    product = _hourly_product()
    monkeypatch.setattr(adapter, "fetch_cwa_township_hourly_product", lambda **_kw: product)
    conn = _hourly_schema_conn()

    assert adapter.ingest_cwa_township_hourly_low_live(conn, api_key="test") == 1
    assert adapter.ingest_cwa_township_hourly_low_live(conn, api_key="test") == 0
    row = conn.execute(
        "SELECT source_cycle_time, source_available_at, captured_at, raw_sha256, request_params_json "
        "FROM raw_model_forecasts"
    ).fetchone()
    assert row[:4] == (
        "2026-07-23T10:14:00+00:00", "2026-07-23T10:15:00+00:00",
        "2026-07-23T10:15:00+00:00", product.raw_sha256,
    )
    provenance = json.loads(row[4])
    assert provenance["issue_time"] == "2026-07-23T09:00:00+00:00"
    assert provenance["response_sha256"] == product.raw_sha256


def test_hourly_low_same_update_changed_body_is_loud_conflict(monkeypatch):
    from src.data.bayes_precision_fusion_download import RawModelForecastRequestConflict

    first = _hourly_product()
    changed = _hourly_product(points=[
        (f"2026-07-24T{hour:02d}:00:00+08:00", "18" if hour == 4 else "20")
        for hour in range(24)
    ])
    products = iter((first, changed))
    monkeypatch.setattr(adapter, "fetch_cwa_township_hourly_product", lambda **_kw: next(products))
    conn = _hourly_schema_conn()

    assert adapter.ingest_cwa_township_hourly_low_live(conn, api_key="test") == 1
    with pytest.raises(RawModelForecastRequestConflict):
        adapter.ingest_cwa_township_hourly_low_live(conn, api_key="test")
    assert conn.execute("SELECT COUNT(*) FROM raw_model_forecasts").fetchone()[0] == 1


def test_hourly_low_same_issue_new_update_is_new_official_revision(monkeypatch):
    first = _hourly_product()
    revised = _hourly_product(
        captured_at="2026-07-23T10:30:00+00:00",
        update_time="2026-07-23T18:29:00+08:00",
        sent_time="2026-07-23T18:29:00+08:00",
        points=[
            (f"2026-07-24T{hour:02d}:00:00+08:00", "17" if hour == 4 else "20")
            for hour in range(24)
        ],
    )
    products = iter((first, revised))
    monkeypatch.setattr(adapter, "fetch_cwa_township_hourly_product", lambda **_kw: next(products))
    conn = _hourly_schema_conn()

    assert adapter.ingest_cwa_township_hourly_low_live(conn, api_key="test") == 1
    assert adapter.ingest_cwa_township_hourly_low_live(conn, api_key="test") == 1
    rows = conn.execute(
        "SELECT source_cycle_time, forecast_value_c FROM raw_model_forecasts ORDER BY source_cycle_time"
    ).fetchall()
    assert rows == [
        ("2026-07-23T10:14:00+00:00", 23.0),
        ("2026-07-23T10:29:00+00:00", 17.0),
    ]


def test_hourly_cwa_shared_due_batch_fetches_once_and_writes_both_models(monkeypatch):
    """The real own-clock path dispatches HIGH/LOW as one F-D0047-061 fetch."""
    from src.data import replacement_forecast_production as production

    fetches = {"count": 0}
    product = _hourly_product()

    def _fetch(**_kwargs):
        fetches["count"] += 1
        return product

    monkeypatch.setattr(adapter, "resolve_cwa_api_key", lambda **_kwargs: "test")
    monkeypatch.setattr(adapter, "fetch_cwa_township_hourly_product", _fetch)
    monkeypatch.setattr(
        production,
        "_station_forecast_poll_intervals",
        lambda: {
            "cwa_township_hourly_high": 300.0,
            "cwa_township_hourly_low": 300.0,
        },
    )
    monkeypatch.setattr(production, "_last_station_ingest_monotonic_by_source", {})
    conn = _hourly_schema_conn()
    monkeypatch.setattr(
        production,
        "_ingest_station_forecasts_live",
        lambda _cfg, *, source_ids: adapter.ingest_enabled_station_sources_live(
            conn, source_ids=source_ids
        ),
    )

    report = production._ingest_station_forecasts_if_due({})

    assert fetches["count"] == 1
    assert report == {
        "cwa_township_hourly_high": 1,
        "cwa_township_hourly_low": 1,
    }
    assert conn.execute(
        "SELECT model, metric, forecast_value_c FROM raw_model_forecasts ORDER BY model"
    ).fetchall() == [
        ("cwa_township_hourly_high", "high", 29.0),
        ("cwa_township_hourly_low", "low", 23.0),
    ]


def test_dispatch_fail_soft_one_source_error_does_not_abort_others(monkeypatch, tmp_path):
    def _boom(conn, **kw):
        raise RuntimeError("CWA network down")

    monkeypatch.setattr(adapter, "ingest_cwa_township_live", _boom)
    monkeypatch.setattr(adapter, "ingest_hko_fnd_live", lambda conn, **kw: 9)
    _write_config(tmp_path, {"cwa_township": dict(_CWA_SPEC), "hko_fnd": dict(_HKO_SPEC)})

    result = adapter.ingest_enabled_station_sources_live(_CONN, root=tmp_path)

    assert result.get("hko_fnd") == 9  # surviving source still ran
    assert "cwa_township" not in result  # errored source omitted, not crashing the cycle


def test_dispatch_fail_soft_invalid_hko_metrics_do_not_abort_cwa(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, "ingest_cwa_township_live", lambda conn, **kw: 7)
    monkeypatch.setattr(adapter, "ingest_hko_fnd_live", lambda conn, **kw: 9)
    _write_config(
        tmp_path,
        {
            "hko_fnd": {**_HKO_SPEC, "metrics": "high,low"},
            "cwa_township": dict(_CWA_SPEC),
        },
    )

    result = adapter.ingest_enabled_station_sources_live(_CONN, root=tmp_path)

    assert result == {"cwa_township": 7}


def test_dispatch_unknown_adapter_kind_is_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, "ingest_cwa_township_live", lambda conn, **kw: 1)
    monkeypatch.setattr(adapter, "ingest_hko_fnd_live", lambda conn, **kw: 1)
    _write_config(tmp_path, {"mystery": {"enabled": True, "adapter_kind": "nonexistent_kind", "city": "X", "metric": "high"}})

    result = adapter.ingest_enabled_station_sources_live(_CONN, root=tmp_path)

    assert result == {}  # no dispatch, no crash


def test_dispatch_empty_or_all_disabled_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, "ingest_cwa_township_live", lambda conn, **kw: 1)
    monkeypatch.setattr(adapter, "ingest_hko_fnd_live", lambda conn, **kw: 1)
    _write_config(tmp_path, {"cwa_township": {**_CWA_SPEC, "enabled": False}})

    result = adapter.ingest_enabled_station_sources_live(_CONN, root=tmp_path)

    assert result == {}


# ---------------------------------------------------------------------------
# Download-cycle helper seam (replacement_forecast_production._ingest_station_forecasts_live):
# opens the forecast-DB conn from cfg, delegates to the dispatcher, fail-soft.
# ---------------------------------------------------------------------------


def test_cycle_helper_returns_none_when_forecast_db_missing():
    from src.data import replacement_forecast_production as prod

    assert prod._ingest_station_forecasts_live({"forecast_db": None}) is None


def test_cycle_helper_delegates_to_dispatcher_and_closes_conn(monkeypatch):
    from src.data import replacement_forecast_production as prod

    closed = {"v": False}

    class _FakeConn:
        isolation_level = ""

        def close(self):
            closed["v"] = True

    monkeypatch.setattr("src.state.db._connect", lambda p, **kw: _FakeConn())
    monkeypatch.setattr(
        "src.data.station_forecast_adapter.ingest_enabled_station_sources_live",
        lambda conn, **kw: {"cwa_township": 5, "hko_fnd": 9},
    )

    out = prod._ingest_station_forecasts_live({"forecast_db": "/tmp/does_not_matter.db"})

    assert out == {"cwa_township": 5, "hko_fnd": 9}
    assert closed["v"] is True


def test_cycle_helper_fail_soft_on_connect_error(monkeypatch):
    from src.data import replacement_forecast_production as prod

    def _boom(p, **kw):
        raise RuntimeError("db open failed")

    monkeypatch.setattr("src.state.db._connect", _boom)

    # Must swallow and return None, never propagate into the download cycle.
    assert prod._ingest_station_forecasts_live({"forecast_db": "/tmp/x.db"}) is None


# ---------------------------------------------------------------------------
# CWA key resolution tolerance: the secret file key was silently mis-cased once
# (CWA_API_KEY vs documented cwa_api_key) -> CWA went to a silent 0-row no-op.
# Resolver must accept either casing from the file so it never silently no-ops again.
# ---------------------------------------------------------------------------


def _write_secret(root: Path, blob: dict) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "cwa_secret.json").write_text(json.dumps(blob), encoding="utf-8")


def test_resolve_cwa_key_accepts_documented_lowercase_file_key(tmp_path):
    _write_secret(tmp_path, {"cwa_api_key": "FAKE-LOWER"})
    assert adapter.resolve_cwa_api_key(environ={}, root=tmp_path) == "FAKE-LOWER"


def test_resolve_cwa_key_accepts_uppercase_file_key(tmp_path):
    _write_secret(tmp_path, {"CWA_API_KEY": "FAKE-UPPER"})
    assert adapter.resolve_cwa_api_key(environ={}, root=tmp_path) == "FAKE-UPPER"


# ---------------------------------------------------------------------------
# Re-home guard (2026-07-20): the 2026-06-11 download-lane migration orphaned the station ingest
# call (it lived only in the descheduled forecast-live _replacement_forecast_download_cycle, so
# cwa_township/hko_fnd went dark 2026-07-17). It is now re-homed onto ingest_main's availability
# poll via the independent due-gated station source-clock job. These guard both.
# ---------------------------------------------------------------------------


def test_due_gate_honors_each_station_source_clock(monkeypatch):
    from src.data import replacement_forecast_production as prod

    calls: list[tuple[str, ...] | None] = []

    def ingest(_cfg, *, source_ids=None):
        calls.append(source_ids)
        return {source_id: 1 for source_id in source_ids or ()}

    monkeypatch.setattr(prod, "_ingest_station_forecasts_live", ingest)
    monkeypatch.setattr(
        prod,
        "_station_forecast_poll_intervals",
        lambda: {
            "cwa_township": 10800.0,
            "cwa_township_hourly_low": 300.0,
            "hko_fnd": 15.0,
        },
    )
    monkeypatch.setattr(prod, "_last_station_ingest_monotonic_by_source", {})

    first = prod._ingest_station_forecasts_if_due({})
    gated = prod._ingest_station_forecasts_if_due({})
    prod._last_station_ingest_monotonic_by_source["hko_fnd"] -= 16.0
    hko_again = prod._ingest_station_forecasts_if_due({})
    prod._last_station_ingest_monotonic_by_source["cwa_township_hourly_low"] -= 301.0
    low_again = prod._ingest_station_forecasts_if_due({})

    assert first == {"cwa_township": 1, "cwa_township_hourly_low": 1, "hko_fnd": 1}
    assert gated is None
    assert hko_again == {"hko_fnd": 1}
    assert low_again == {"cwa_township_hourly_low": 1}
    assert calls == [
        ("cwa_township", "cwa_township_hourly_low", "hko_fnd"),
        ("hko_fnd",),
        ("cwa_township_hourly_low",),
    ]


def test_due_gate_does_not_reseed_unchanged_fast_poll(monkeypatch):
    from src.data import replacement_forecast_production as prod

    monkeypatch.setattr(
        prod,
        "_station_forecast_poll_intervals",
        lambda: {"hko_fnd": 15.0},
    )
    monkeypatch.setattr(prod, "_last_station_ingest_monotonic_by_source", {})
    monkeypatch.setattr(
        prod,
        "_ingest_station_forecasts_live",
        lambda _cfg, *, source_ids=None: {"hko_fnd": 0},
    )

    assert prod._ingest_station_forecasts_if_due({}) == {"hko_fnd": 0}
    prod._last_station_ingest_monotonic_by_source["hko_fnd"] -= 16.0
    assert prod._ingest_station_forecasts_if_due({}) is None


def test_independent_station_source_clock_is_wired():
    """Station fetch must not wait behind the heavier gridded availability job."""
    import inspect

    from src import ingest_main

    station_src = inspect.getsource(ingest_main._station_forecast_source_clock_tick)
    gridded_src = inspect.getsource(ingest_main._replacement_availability_poll_tick)
    assert "_ingest_station_forecasts_if_due" in station_src
    assert "_ingest_station_forecasts_if_due" not in gridded_src


def test_station_scheduler_cadence_is_independent_of_gridded_override(monkeypatch):
    from src import ingest_main
    from src.data import replacement_forecast_production as prod

    monkeypatch.setenv("ZEUS_REPLACEMENT_AVAILABILITY_POLL_SECONDS", "300")
    monkeypatch.setattr(
        prod,
        "_station_forecast_poll_intervals",
        lambda: {"hko_fnd": 15.0, "cwa_township": 10800.0},
    )

    assert ingest_main._replacement_availability_poll_seconds() == 300
    assert ingest_main._station_forecast_source_clock_poll_seconds() == 15


@pytest.mark.parametrize(
    ("station_report", "expected_reseeds", "expected_changed_sources"),
    [
        ({"hko_fnd": 18}, 1, ("hko_fnd",)),
        ({"hko_fnd": 0}, 1, ("hko_fnd",)),
        (
            {"cwa_township": 6, "hko_fnd": 0},
            1,
            ("cwa_township", "hko_fnd"),
        ),
        (None, 0, None),
    ],
)
def test_station_writes_reseed_even_when_openmeteo_clock_is_current(
    monkeypatch,
    station_report,
    expected_reseeds,
    expected_changed_sources,
):
    from src import ingest_main
    from src.data import replacement_forecast_production as prod

    reseeds = {"count": 0}
    changed_sources: list[tuple[str, ...] | None] = []

    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    monkeypatch.setattr(
        prod,
        "_ingest_station_forecasts_if_due",
        lambda _cfg: station_report,
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg, **_kwargs: (
            reseeds.__setitem__("count", reseeds["count"] + 1),
            changed_sources.append(_kwargs.get("changed_sources")),
            {"status": "ENQUEUED", "seeds_enqueued": 1},
        )[2],
    )
    report = ingest_main._station_forecast_source_clock_tick()

    assert reseeds["count"] == expected_reseeds
    if expected_reseeds:
        assert changed_sources == [expected_changed_sources]
        assert report["fusion_upgrade_status"] == "ENQUEUED"
    else:
        assert report["status"] == "STATION_FORECAST_SOURCE_CURRENT"


def test_diagnostic_download_cycle_does_not_duplicate_station_ingest():
    """Only the due-gated availability poll may fetch station forecasts."""
    import inspect

    from src.data import replacement_forecast_production as prod

    src = inspect.getsource(prod._replacement_forecast_download_cycle)
    assert "_ingest_station_forecasts_live(cfg)" not in src


def test_hourly_product_rejects_revision_after_possession():
    with pytest.raises(ValueError, match="Update <= captured_at"):
        _hourly_product(update_time="2026-07-23T18:16:00+08:00")


@pytest.mark.parametrize("sent", ["2026-07-23T18:13:00+08:00", "2026-07-23T18:14:30+08:00"])
def test_hourly_nominal_issue_and_xml_generation_are_not_revision_order(sent):
    product = _hourly_product(
        issue_time="2026-07-23T19:00:00+08:00",
        sent_time=sent,
    )
    assert product.issue_time == "2026-07-23T11:00:00+00:00"
    assert product.update_time == "2026-07-23T10:14:00+00:00"
    assert product.captured_at == "2026-07-23T10:15:00+00:00"


def test_hourly_product_accepts_equal_clocks_and_optional_sent():
    for sent in (None, "2026-07-23T10:15:00+00:00"):
        product = _hourly_product(
            issue_time="2026-07-23T18:15:00+08:00",
            update_time="2026-07-23T10:15:00+00:00",
            sent_time=sent,
        )
        assert product.issue_time == product.update_time == product.captured_at


@pytest.mark.parametrize("metric", ["high", "low"])
@pytest.mark.parametrize("fault", [None, "value", "city", "metric", "issue", "timezone", "body"])
def test_hko_same_issue_entity_recovers_only_its_immutable_legacy_context(monkeypatch, tmp_path, metric, fault):
    import urllib.request
    from datetime import datetime, timedelta, timezone
    from src.data import replacement_current_value_serving as serving
    from src.data.replacement_forecast_current_target_plan import _fusion_current_value_count
    from src import config

    private_state = config.validate_test_state_root(tmp_path)
    monkeypatch.setenv(config.TEST_STATE_ROOT_ENV, str(private_state))
    monkeypatch.setattr(config, "_TEST_STATE_ROOT", private_state)
    monkeypatch.setattr(config, "STATE_DIR", private_state)

    clock = [datetime(2026, 7, 23, 10, 15, tzinfo=timezone.utc)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)
    body = (json.dumps({"updateTime": "2026-07-23T10:14:00+00:00", "weatherForecast": [{
        "forecastDate": "20260724", "forecastMaxtemp": {"value": 33, "unit": "C"},
        "forecastMintemp": {"value": 27, "unit": "C"}}]}, indent=2) + "\n").encode()
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def read(self): return body
    calls = []
    def http(request, **_kwargs):
        calls.append(request.full_url)
        return Response()
    monkeypatch.setattr(urllib.request, "urlopen", http)
    monkeypatch.setattr(adapter, "datetime", Clock)
    monkeypatch.setattr("src.data.bayes_precision_fusion_download.datetime", Clock)
    conn = _hourly_schema_conn(tmp_path / "legacy-fnd.db")
    try:
        rows = adapter.parse_hko_fnd_payload(json.loads(body), city="Hong Kong", metric=metric,
            city_timezone="Asia/Hong_Kong", captured_at=clock[0].isoformat())
        assert adapter.persist_station_forecast_rows(conn, rows, provider="hong_kong_observatory",
            endpoint=adapter._HKO_ENDPOINT, city_timezone="Asia/Hong_Kong", captured_at=clock[0].isoformat()) == 1
        conn.commit()
        original = conn.execute("SELECT * FROM raw_model_forecasts").fetchall()
        assert conn.execute("SELECT artifact_id,raw_sha256,latitude_requested,longitude_requested FROM raw_model_forecasts").fetchone() == (None, None, None, None)
        issue = rows[0].source_cycle_time
        def read(cut):
            return serving.read_current_instrument_values(conn, city="Hong Kong", metric=metric,
                target_date="2026-07-24", source_cycle_time_iso=issue,
                decision_time_iso=cut.isoformat(), include_station_sources=True)
        oldcut = clock[0] + timedelta(seconds=1)
        assert read(oldcut) == {}
        clock[0] += timedelta(minutes=5)
        assert adapter.ingest_hko_fnd_live(conn, metrics=(metric,)) == 0
        conn.commit()
        cut = clock[0] + timedelta(seconds=1)
        current = read(cut)
        assert current["hko_fnd"].value_c == (33 if metric == "high" else 27)
        assert conn.execute("SELECT * FROM raw_model_forecasts").fetchall() == original
        assert read(oldcut) == {}
        assert _fusion_current_value_count(conn, city="Hong Kong", target_date="2026-07-24",
            temperature_metric=metric, source_cycle_time=issue, decision_time=cut) == 1
        artifacts = conn.execute("SELECT * FROM raw_forecast_artifacts").fetchall()
        clock[0] += timedelta(minutes=5)
        assert adapter.ingest_hko_fnd_live(conn, metrics=(metric,)) == 0
        conn.commit()
        assert conn.execute("SELECT * FROM raw_forecast_artifacts").fetchall() == artifacts
        assert conn.execute("SELECT * FROM raw_model_forecasts").fetchall() == original
        assert len(calls) == 2
        if fault is None:
            return
        schema = serving.current_value_serving_schema(conn)
        raw = conn.execute(f"SELECT {serving._product_identity_select(schema, decision_iso=cut.isoformat())} FROM raw_model_forecasts").fetchone()[0]
        typed = json.loads(serving._read_product_identity_at_cutoff(conn, raw))
        if fault == "value": typed["forecast_value_c"] += 1
        elif fault == "city": typed["city"] = "Shanghai"
        elif fault == "metric": typed["metric"] = "low" if metric == "high" else "high"
        elif fault == "issue": typed["source_cycle_time"] = "2026-07-23T10:13:00+00:00"
        elif fault == "timezone": typed["timezone_requested"] = "UTC"
        else: Path(typed["physical_artifact"]["artifact_path"]).write_bytes(b"{}")
        assert not serving._source_clock_product_has_authority(json.dumps(typed), lead_days=1)
        assert conn.execute("SELECT * FROM raw_model_forecasts").fetchall() == original
    finally:
        conn.close()
