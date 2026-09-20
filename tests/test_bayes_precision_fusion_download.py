# Created: 2026-06-08
# Lifecycle: created=2026-06-08; last_reviewed=2026-09-20; last_reused=2026-09-20
# Purpose: Regression tests for BPF raw forecast download and persistence semantics.
# Reuse: Run when changing Bayes precision fusion raw-input capture or scheduler health.
# Authority basis: BAYES_PRECISION_FUSION_SPEC.md §6 F1 (raw capture: previous_runs + single_runs ->
#   raw_model_forecasts), §5 (~6mo retention); CONTINUITY_AND_WIRING.md §4 steps 2-3 + 9
#   (forward+history daily download/persist + 180d prune). IRON RULE #4 (one-builder: reuse
#   the OM fetchers, single persist conn), INV-37 (single zeus-forecasts.db connection).
"""TDD for the BAYES_PRECISION_FUSION multi-model forward+history download/persist job.

Relationship under test (download job -> raw_model_forecasts persistence boundary):
  (a) when capture-flag ON it WRITES raw_model_forecasts rows (single_runs FORWARD +
      previous_runs fixed-lead) for the surviving models; (b) it persists NOTHING when there
      are no targets / no surviving fetches; (c) FAIL-SOFT per model (a raising fetch drops
      only that model); (d) forecast_value_c is degC and training_allowed=0;
      (e) retention prunes rows older than the cutoff; (f) idempotent re-run (UNIQUE upsert).
All fetchers are injected (NO network).
"""
from __future__ import annotations

import sqlite3
import time
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema


def _forecast_db(tmp_path: Path) -> Path:
    db = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(str(db))
    ensure_replacement_forecast_live_schema(conn)
    conn.commit()
    conn.close()
    return db


def _targets():
    # (city, metric, target_date, lead_days, lat, lon, timezone)
    from src.data.bayes_precision_fusion_download import BayesPrecisionFusionDownloadTarget
    return [
        BayesPrecisionFusionDownloadTarget(city="Paris", metric="high", target_date="2026-06-09",
                          lead_days=1, latitude=48.967, longitude=2.428, timezone_name="Europe/Paris"),
    ]


def _complete_hourly_local_day_payload(
    target_date: date,
    *,
    base: float = 0.0,
    missing_hour: int | None = None,
) -> dict[str, object]:
    """A full hourly local-day axis; an optional defect stays internal to that axis."""
    hours = list(range(24))
    temperatures: list[float | None] = [
        base - 1.0 if hour == 0 else base + 2.0 if hour == 12 else base + 1.0
        for hour in hours
    ]
    if missing_hour is not None:
        temperatures[hours.index(missing_hour)] = None
    return {
        "hourly": {
            "time": [f"{target_date.isoformat()}T{hour:02d}:00" for hour in hours],
            "temperature_2m": temperatures,
        },
        "hourly_units": {"temperature_2m": "C"},
    }


def _two_city_targets():
    from src.data.bayes_precision_fusion_download import BayesPrecisionFusionDownloadTarget
    return [
        BayesPrecisionFusionDownloadTarget(
            city="Paris", metric="high", target_date="2026-06-09",
            lead_days=1, latitude=48.967, longitude=2.428,
            timezone_name="Europe/Paris",
        ),
        BayesPrecisionFusionDownloadTarget(
            city="Berlin", metric="high", target_date="2026-06-09",
            lead_days=1, latitude=52.520, longitude=13.405,
            timezone_name="Europe/Berlin",
        ),
    ]


def _count(db: Path, **where) -> int:
    conn = sqlite3.connect(str(db))
    clause = " AND ".join(f"{k}=?" for k in where)
    sql = "SELECT COUNT(*) FROM raw_model_forecasts" + (f" WHERE {clause}" if where else "")
    n = conn.execute(sql, tuple(where.values())).fetchone()[0]
    conn.close()
    return int(n)


# =====================================================================================
# (a) capture-flag ON: writes single_runs (forward) + previous_runs (fixed-lead) rows
# =====================================================================================
def test_download_writes_single_and_previous_runs(tmp_path) -> None:
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)
    cycle = datetime(2026, 6, 8, 0, 0, tzinfo=UTC)

    # Inject deterministic fetchers (no network). single_runs -> one degC value;
    # previous_runs -> a degC value for the fixed-lead.
    def _single(*, model, latitude, longitude, timezone_name, run, target_local_date, metric, forecast_hours):
        return 20.0 + len(model) * 0.01  # model-specific degC

    def _previous(*, model, latitude, longitude, timezone_name, target_date, lead_days, metric):
        return 19.5 + len(model) * 0.01

    report = download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=cycle, targets=_targets(),
        single_runs_fetch=_single, previous_runs_fetch=_previous,
    )
    assert report["status"] == "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
    assert report["committed_families"] == (("Paris", "2026-06-09", "high"),)
    # Current selected model set x {single_runs, previous_runs} for the one target.
    n_single = _count(db, endpoint="single_runs")
    n_prev = _count(db, endpoint="previous_runs")
    assert n_single > 0, f"expected in-domain forward single_runs rows, got {n_single}"
    assert n_prev > 0, f"expected previous_runs fixed-lead rows, got {n_prev}"
    # forecast_value_c is degC; training_allowed=0 is pinned.
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM raw_model_forecasts LIMIT 1").fetchone()
    assert row["training_allowed"] == 0
    assert 15.0 <= row["forecast_value_c"] <= 25.0
    conn.close()


# =====================================================================================
# (b) no targets -> no writes
# =====================================================================================
def test_download_no_targets_writes_nothing(tmp_path) -> None:
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)
    report = download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=datetime(2026, 6, 8, 0, tzinfo=UTC), targets=[],
        single_runs_fetch=lambda **k: 20.0, previous_runs_fetch=lambda **k: 20.0,
    )
    assert _count(db) == 0
    assert report["written_row_count"] == 0
    assert report["committed_families"] == ()


def test_download_timebox_returns_incomplete_without_fetching_targets(tmp_path, monkeypatch) -> None:
    """Source-clock fast path can be retried next tick; it must not run past its wall budget."""
    import src.data.bayes_precision_fusion_download as dl
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)

    def _unexpected_single(*_args, **_kwargs):
        raise AssertionError("timeboxed download must stop before the HTTP fetch")

    def _unexpected_previous(*_args, **_kwargs):
        raise AssertionError("timeboxed download must stop before the HTTP fetch")

    monkeypatch.setattr(dl, "_default_live_fetch_batched", _unexpected_single)
    monkeypatch.setattr(dl, "_default_previous_runs_fetch_batched", _unexpected_previous)

    report = download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=datetime(2026, 6, 8, 0, tzinfo=UTC),
        targets=_two_city_targets(),
        models=("ecmwf_ifs",),
        include_previous_runs=False,
        prune_after=False,
        max_wall_clock_seconds=0,
    )

    assert report["status"] == "BAYES_PRECISION_FUSION_EXTRA_TIMEBOXED_INCOMPLETE"
    assert report["timeboxed_incomplete"] is True
    assert report["timebox_unattempted_target_groups"] == 2
    assert report["attempted_target_group_count"] == 0
    assert report["written_row_count"] == 0


def test_download_scopes_persisted_key_reads_to_requested_batch(
    tmp_path, monkeypatch
) -> None:
    import src.state.db as state_db
    from src.data.bayes_precision_fusion_download import (
        download_bayes_precision_fusion_extra_raw_inputs,
    )

    statements: list[tuple[str, tuple[object, ...]]] = []

    class _ReadConn:
        def execute(self, sql, params=()):
            statements.append((str(sql), tuple(params)))
            return ()

        def close(self):
            pass

    monkeypatch.setattr(state_db, "_connect", lambda *_args, **_kwargs: _ReadConn())
    report = download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=tmp_path / "forecasts.db",
        cycle=datetime(2026, 6, 8, 0, tzinfo=UTC),
        targets=_targets(),
        models=("ecmwf_ifs",),
        include_previous_runs=False,
        prune_after=False,
        single_runs_fetch=lambda **_kwargs: 20.0,
        max_wall_clock_seconds=0,
    )

    assert report["timeboxed_incomplete"] is True
    assert len(statements) == 1
    sql, params = statements[0]
    assert "WHERE endpoint = 'single_runs'" in sql
    assert "model IN (" in sql
    assert "city IN (" in sql
    assert "target_date IN (" in sql
    assert "source_cycle_time IN (" in sql
    assert "previous_runs" not in sql
    assert params == (
        "ecmwf_ifs",
        "Paris",
        "2026-06-09",
        "2026-06-08T00:00:00+00:00",
    )


def test_source_clock_fetch_uses_remaining_deadline_without_retries(monkeypatch) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    captured: dict[str, object] = {}

    def _fetch(*_args, **kwargs):
        captured.update(kwargs)
        raise RuntimeError("synthetic transport stop")

    monkeypatch.setattr(client, "fetch", _fetch)
    deadline = time.monotonic() + 0.2
    report = dl._default_live_fetch_batched(
        models=["ecmwf_ifs"],
        latitude=48.967,
        longitude=2.428,
        timezone_name="Europe/Paris",
        run=datetime(2026, 6, 8, 0, tzinfo=UTC),
        target_local_date=date(2026, 6, 9),
        forecast_hours=120,
        allow_per_model_fallback=False,
        deadline_monotonic=deadline,
    )

    assert report[dl._BATCH_TRANSPORT_ERROR_KEY][0] == "synthetic transport stop"
    assert captured["max_retries"] == 1
    assert 0.0 < float(captured["timeout"]) <= 0.2


def test_openmeteo_default_transport_reuses_one_bounded_client(
    monkeypatch, tmp_path
) -> None:
    import src.data.openmeteo_client as client

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    calls: list[tuple[str, dict[str, object]]] = []

    class _Client:
        def get(self, url, *, params, timeout):
            calls.append((str(url), dict(params)))
            return httpx.Response(
                200,
                json={"ok": True},
                request=httpx.Request("GET", url, params=params),
            )

    shared = _Client()
    monkeypatch.setattr(client, "_SHARED_HTTP_CLIENT", shared)
    tracker = client.OpenMeteoQuotaTracker(
        state_path=tmp_path / "openmeteo-quota.json"
    )

    for latitude in (1.0, 2.0):
        assert client.fetch(
            "https://api.open-meteo.com/v1/forecast",
            {"latitude": latitude, "longitude": 3.0},
            max_retries=1,
            quota=tracker,
        ) == {"ok": True}

    assert len(calls) == 2
    assert tracker.calls_today() == 2


def test_source_clock_fetch_batches_multiple_locations_into_one_request(monkeypatch) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    captured: dict[str, object] = {}

    def _payload(base: float) -> dict[str, object]:
        return {
            "hourly": {
                "time": [
                    "2026-06-09T00:00",
                    "2026-06-09T03:00",
                    "2026-06-09T12:00",
                    "2026-06-09T21:00",
                ],
                "temperature_2m": [base - 1.0, base, base + 2.0, base + 1.0],
            },
            "hourly_units": {"temperature_2m": "°C"},
        }

    def _fetch(_url, params, **kwargs):
        captured["params"] = params
        captured.update(kwargs)
        return [_payload(20.0), _payload(30.0)]

    monkeypatch.setattr(client, "fetch", _fetch)
    got = dl._default_live_fetch_locations_batched(
        models=["ecmwf_ifs"],
        locations=[
            (48.967, 2.428, "Europe/Paris", (date(2026, 6, 9),)),
            (52.520, 13.405, "Europe/Berlin", (date(2026, 6, 9),)),
        ],
        run=datetime(2026, 6, 8, 0, tzinfo=UTC),
        forecast_hours=120,
    )

    params = captured["params"]
    assert isinstance(params, dict)
    assert params["latitude"] == "48.967,52.52"
    assert params["longitude"] == "2.428,13.405"
    assert params["timezone"] == "Europe/Paris,Europe/Berlin"
    assert params["cell_selection"] == dl.BAYES_PRECISION_FUSION_CELL_SELECTION
    assert got == [
        {date(2026, 6, 9): {"ecmwf_ifs": (22.0, 19.0)}},
        {date(2026, 6, 9): {"ecmwf_ifs": (32.0, 29.0)}},
    ]


@pytest.mark.parametrize(
    ("target_date", "decision_at", "first_hour", "last_hour", "expected"),
    (
        # Sao Paulo 19:30 local: the 06:00 prefix gap is already elapsed and
        # the current run still covers the unresolved evening.
        (date(2026, 8, 18), datetime(2026, 8, 18, 22, 30, tzinfo=UTC), 6, 23, True),
        # A clipped suffix can omit the unresolved peak and remains forbidden.
        (date(2026, 8, 18), datetime(2026, 8, 18, 22, 30, tzinfo=UTC), 6, 18, False),
        # A run that starts after the decision does not cover the current-to-end window.
        (date(2026, 8, 18), datetime(2026, 8, 18, 22, 30, tzinfo=UTC), 21, 23, False),
        # The nominal evening threshold is not enough when it is already behind the decision.
        (date(2026, 8, 18), datetime(2026, 8, 19, 1, 30, tzinfo=UTC), 6, 20, False),
        # Missing-prefix relaxation is Day0-only; future days still need full coverage.
        (date(2026, 8, 19), datetime(2026, 8, 18, 22, 30, tzinfo=UTC), 6, 23, False),
        (date(2026, 8, 19), datetime(2026, 8, 18, 22, 30, tzinfo=UTC), 0, 23, True),
    ),
)
def test_single_runs_partial_day_requires_elapsed_prefix_and_remaining_coverage(
    target_date: date,
    decision_at: datetime,
    first_hour: int,
    last_hour: int,
    expected: bool,
) -> None:
    from src.data.bayes_precision_fusion_download import (
        _parse_batched_single_runs_payload,
    )

    hours = list(range(first_hour, last_hour + 1))
    payload = {
        "hourly": {
            "time": [f"{target_date.isoformat()}T{hour:02d}:00" for hour in hours],
            "temperature_2m": [float(hour) for hour in hours],
        },
        "hourly_units": {"temperature_2m": "C"},
    }
    parsed = _parse_batched_single_runs_payload(
        payload,
        ["ecmwf_ifs"],
        target_date,
        "America/Sao_Paulo",
        decision_at=decision_at,
    )

    assert ("ecmwf_ifs" in parsed) is expected
    if expected:
        assert parsed["ecmwf_ifs"] == (float(last_hour), float(first_hour))


def test_single_runs_partial_future_day_receipts_exact_run_as_unmaterializable() -> None:
    import src.data.bayes_precision_fusion_download as dl

    target_date = date(2026, 9, 4)
    payload = {
        "hourly": {
            "time": [
                f"{target_date.isoformat()}T{hour:02d}:00" for hour in range(6, 21)
            ],
            "temperature_2m": [float(hour) for hour in range(6, 21)],
        },
        "hourly_units": {"temperature_2m": "C"},
    }

    parsed = dl._parse_batched_single_runs_payload(
        payload,
        ["icon_eu"],
        target_date,
        "Europe/Moscow",
        decision_at=datetime(2026, 9, 3, 15, tzinfo=UTC),
    )

    assert "icon_eu" not in parsed
    gaps = parsed[dl._BATCH_EXACT_RUN_UNMATERIALIZABLE_KEY]
    assert gaps["icon_eu"].startswith("ValueError:partial local-day coverage")


def test_single_runs_rejects_internal_hourly_nonfinite_value() -> None:
    """A daily extreme cannot silently skip a missing value between finite samples."""
    from src.data import bayes_precision_fusion_download as dl

    target_date = date(2026, 9, 4)
    parsed = dl._parse_batched_single_runs_payload(
        _complete_hourly_local_day_payload(
            target_date, missing_hour=12
        ),
        ["ecmwf_ifs"],
        target_date,
        "UTC",
        decision_at=datetime(2026, 9, 3, 15, tzinfo=UTC),
    )

    assert "ecmwf_ifs" not in parsed
    assert (
        "internal hourly" in parsed[dl._BATCH_EXACT_RUN_UNMATERIALIZABLE_KEY]["ecmwf_ifs"]
    )


def test_source_clock_fetch_isolates_location_response_failure(monkeypatch) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    calls: list[int] = []

    def _payload(base: float) -> dict[str, object]:
        return {
            "hourly": {
                "time": [
                    "2026-06-09T00:00",
                    "2026-06-09T03:00",
                    "2026-06-09T12:00",
                    "2026-06-09T21:00",
                ],
                "temperature_2m": [base - 1.0, base, base + 2.0, base + 1.0],
            },
            "hourly_units": {"temperature_2m": "°C"},
        }

    def _fetch(_url, params, **_kwargs):
        location_count = len(str(params["latitude"]).split(","))
        calls.append(location_count)
        if location_count == 4:
            raise RuntimeError("Open-Meteo multi-location response count mismatch")
        bases = [float(value) for value in str(params["latitude"]).split(",")]
        return [_payload(base) for base in bases]

    monkeypatch.setattr(client, "fetch", _fetch)
    got = dl._default_live_fetch_locations_batched(
        models=["ecmwf_ifs"],
        locations=[
            (20.0, 1.0, "UTC", (date(2026, 6, 9),)),
            (30.0, 2.0, "UTC", (date(2026, 6, 9),)),
            (40.0, 3.0, "UTC", (date(2026, 6, 9),)),
            (50.0, 4.0, "UTC", (date(2026, 6, 9),)),
        ],
        run=datetime(2026, 6, 8, 0, tzinfo=UTC),
        forecast_hours=120,
        deadline_monotonic=time.monotonic() + 1.0,
    )

    assert calls == [4, 2, 2]
    assert [
        result[date(2026, 6, 9)]["ecmwf_ifs"] for result in got
    ] == [
        (22.0, 19.0),
        (32.0, 29.0),
        (42.0, 39.0),
        (52.0, 49.0),
    ]


@pytest.mark.parametrize(
    "error",
    (
        httpx.ConnectError("DNS lookup failed"),
        httpx.ConnectTimeout("TLS handshake timed out"),
        httpx.ReadTimeout("read timed out"),
        OSError(8, "nodename nor servname provided, or not known"),
        TimeoutError("request timed out"),
    ),
)
def test_source_clock_fetch_does_not_split_global_transport_failure(
    monkeypatch,
    error: Exception,
) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    calls: list[int] = []

    def _fetch(_url, params, **_kwargs):
        calls.append(len(str(params["latitude"]).split(",")))
        raise error

    monkeypatch.setattr(client, "fetch", _fetch)
    target_date = date(2026, 6, 9)
    got = dl._default_live_fetch_locations_batched(
        models=["ecmwf_ifs"],
        locations=[
            (float(index), float(index), "UTC", (target_date,))
            for index in range(25)
        ],
        run=datetime(2026, 6, 8, 0, tzinfo=UTC),
        forecast_hours=120,
        deadline_monotonic=time.monotonic() + 1.0,
    )

    assert calls == [25]
    assert all(
        dl._BATCH_TRANSPORT_ERROR_KEY in result[target_date]
        for result in got
    )


def test_source_clock_fetch_does_not_split_quota_failure(monkeypatch) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    calls: list[int] = []

    def _fetch(_url, params, **_kwargs):
        calls.append(len(str(params["latitude"]).split(",")))
        raise RuntimeError("Open-Meteo quota exhausted (570 calls today)")

    monkeypatch.setattr(client, "fetch", _fetch)
    got = dl._default_live_fetch_locations_batched(
        models=["ecmwf_ifs"],
        locations=[
            (20.0, 1.0, "UTC", (date(2026, 6, 9),)),
            (30.0, 2.0, "UTC", (date(2026, 6, 9),)),
            (40.0, 3.0, "UTC", (date(2026, 6, 9),)),
            (50.0, 4.0, "UTC", (date(2026, 6, 9),)),
        ],
        run=datetime(2026, 6, 8, 0, tzinfo=UTC),
        forecast_hours=120,
        deadline_monotonic=time.monotonic() + 1.0,
    )

    assert calls == [4]
    assert all(
        dl._BATCH_TRANSPORT_ERROR_KEY in result[date(2026, 6, 9)]
        for result in got
    )


def test_source_clock_fetch_does_not_split_typed_http_outcome(monkeypatch) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    calls: list[int] = []
    error = RuntimeError("synthetic typed retryable response")
    error.outcome = client.OpenMeteoHTTPOutcome(
        status_code=503,
        retry_class=client.OpenMeteoRetryClass.RETRYABLE,
        retry_after_seconds=None,
        reason="http_503",
        body_sha256="synthetic",
    )

    def _fetch(_url, params, **_kwargs):
        calls.append(len(str(params["latitude"]).split(",")))
        raise error

    monkeypatch.setattr(client, "fetch", _fetch)
    got = dl._default_live_fetch_locations_batched(
        models=["ecmwf_ifs"],
        locations=[
            (20.0, 1.0, "UTC", (date(2026, 6, 9),)),
            (30.0, 2.0, "UTC", (date(2026, 6, 9),)),
        ],
        run=datetime(2026, 6, 8, 0, tzinfo=UTC),
        forecast_hours=120,
        deadline_monotonic=time.monotonic() + 1.0,
    )

    assert calls == [2]
    assert all(
        result[date(2026, 6, 9)][dl._BATCH_TRANSPORT_ERROR_KEY][1]
        == error.outcome.persisted()
        for result in got
    )


def test_source_clock_fetch_does_not_split_expired_deadline(monkeypatch) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    calls: list[object] = []
    monkeypatch.setattr(client, "fetch", lambda *_args, **_kwargs: calls.append(object()))
    got = dl._default_live_fetch_locations_batched(
        models=["ecmwf_ifs"],
        locations=[
            (20.0, 1.0, "UTC", (date(2026, 6, 9),)),
            (30.0, 2.0, "UTC", (date(2026, 6, 9),)),
        ],
        run=datetime(2026, 6, 8, 0, tzinfo=UTC),
        forecast_hours=120,
        deadline_monotonic=time.monotonic() - 0.01,
    )

    assert calls == []
    assert all(
        dl._BATCH_TRANSPORT_ERROR_KEY in result[date(2026, 6, 9)]
        for result in got
    )


def test_nbm_hourly_run_falls_back_to_atomic_meta_stamped_standard_api(
    monkeypatch,
) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client
    import src.data.openmeteo_model_updates as metadata

    run = datetime(2026, 7, 27, 15, tzinfo=UTC)
    available = datetime(2026, 7, 27, 16, 4, tzinfo=UTC)
    modified = datetime(2026, 7, 27, 16, 3, tzinfo=UTC)
    update = metadata.OpenMeteoModelUpdate(
        model="ncep_nbm_conus",
        last_run_initialisation_time=run,
        last_run_availability_time=available,
        last_run_modification_time=modified,
    )
    metadata_reads: list[object] = []
    fetches: list[tuple[str, dict[str, object]]] = []

    def _metadata(*_args, **_kwargs):
        metadata_reads.append(object())
        return (update,)

    def _payload(base: float) -> dict[str, object]:
        return {
            "hourly": {
                "time": [
                    "2026-07-28T00:00",
                    "2026-07-28T03:00",
                    "2026-07-28T12:00",
                    "2026-07-28T21:00",
                ],
                "temperature_2m": [base - 1.0, base, base + 2.0, base + 1.0],
            },
            "hourly_units": {"temperature_2m": "°C"},
        }

    def _fetch(url, params, **_kwargs):
        fetches.append((url, params))
        if "single-runs" in url:
            raise RuntimeError("Client error '400 Bad Request'")
        return [_payload(30.0), _payload(25.0)]

    monkeypatch.setattr(metadata, "fetch_model_updates", _metadata)
    monkeypatch.setattr(client, "fetch", _fetch)
    got = dl._default_live_fetch_locations_batched(
        models=["ncep_nbm_conus"],
        locations=[
            (30.267, -97.743, "America/Chicago", (date(2026, 7, 28),)),
            (40.713, -74.006, "America/New_York", (date(2026, 7, 28),)),
        ],
        run=run,
        source_available_at=available,
        forecast_hours=120,
    )

    assert len(metadata_reads) == 2
    assert len(fetches) == 2
    assert fetches[1][0].endswith("/v1/forecast")
    assert "run" not in fetches[1][1]
    assert got[0][date(2026, 7, 28)]["ncep_nbm_conus"] == (32.0, 29.0)
    stamp = got[0][date(2026, 7, 28)][dl._BATCH_TRANSPORT_PROVENANCE_KEY][
        "ncep_nbm_conus"
    ]
    assert stamp.run == run
    assert stamp.modification_time == modified


def test_single_runs_quota_uses_atomic_meta_stamped_standard_api_for_same_model(
    monkeypatch,
) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client
    import src.data.openmeteo_model_updates as metadata

    model = "ecmwf_ifs"
    run = datetime(2026, 8, 18, 6, tzinfo=UTC)
    available = datetime(2026, 8, 18, 13, 1, 20, tzinfo=UTC)
    modified = datetime(2026, 8, 18, 12, 31, 9, tzinfo=UTC)
    update = metadata.OpenMeteoModelUpdate(
        model=model,
        last_run_initialisation_time=run,
        last_run_availability_time=available,
        last_run_modification_time=modified,
    )
    fetches: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(metadata, "fetch_model_updates", lambda *_args, **_kwargs: (update,))

    def _fetch(url, params, **_kwargs):
        fetches.append((url, params))
        if "single-runs" in url:
            raise RuntimeError("429 Too Many Requests")
        return {
            "hourly": {
                "time": ["2026-08-19T00:00", "2026-08-19T12:00", "2026-08-19T21:00"],
                "temperature_2m": [20.0, 31.0, 24.0],
            }
        }

    monkeypatch.setattr(client, "fetch", _fetch)
    got = dl._default_live_fetch_locations_batched(
        models=[model],
        locations=[(32.0853, 34.7818, "Asia/Jerusalem", (date(2026, 8, 19),))],
        run=run,
        source_available_at=available,
        forecast_hours=120,
    )

    result = got[0][date(2026, 8, 19)]
    assert result[model] == (31.0, 20.0)
    assert result[dl._BATCH_TRANSPORT_PROVENANCE_KEY][model].run == run
    assert len(fetches) == 2
    assert "single-runs" in fetches[0][0]
    assert fetches[1][0].endswith("/v1/forecast")
    assert fetches[1][1]["models"] == dl.OPENMETEO_MODEL_IDS.get(model, model)
    assert "run" not in fetches[1][1]


def test_single_runs_quota_rejects_standard_payload_when_frozen_run_mismatches(
    monkeypatch,
) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client
    import src.data.openmeteo_model_updates as metadata

    model = "icon_global"
    frozen_run = datetime(2026, 8, 18, 6, tzinfo=UTC)
    # availability is held identical to the frozen run on purpose: only initialisation_time
    # differs, so this pins refusal to a run-identity mismatch, not an availability replica skew.
    available = datetime(2026, 8, 18, 15, 44, 39, tzinfo=UTC)
    update = metadata.OpenMeteoModelUpdate(
        model=model,
        last_run_initialisation_time=datetime(2026, 8, 18, 12, tzinfo=UTC),
        last_run_availability_time=available,
        last_run_modification_time=datetime(2026, 8, 18, 15, 30, tzinfo=UTC),
    )
    monkeypatch.setattr(metadata, "fetch_model_updates", lambda *_args, **_kwargs: (update,))

    def _fetch(url, _params, **_kwargs):
        if "single-runs" in url:
            raise RuntimeError("429 Too Many Requests")
        raise AssertionError("mismatched metadata must reject before standard fetch")

    monkeypatch.setattr(client, "fetch", _fetch)
    got = dl._default_live_fetch_locations_batched(
        models=[model],
        locations=[(32.0853, 34.7818, "Asia/Jerusalem", (date(2026, 8, 19),))],
        run=frozen_run,
        source_available_at=available,
        forecast_hours=120,
    )

    result = got[0][date(2026, 8, 19)]
    assert model not in result
    assert "provider metadata no longer matches frozen run" in result[
        dl._BATCH_TRANSPORT_ERROR_KEY
    ][0]


def test_bpf_preflight_waits_only_when_both_current_transports_are_blocked(
    monkeypatch,
) -> None:
    import src.data.bayes_precision_fusion_download as dl

    waits = {
        "single-runs-api.open-meteo.com/v1/forecast": 300,
        "api.open-meteo.com/v1/forecast": 0,
    }
    monkeypatch.setattr(
        dl._BPF_OPENMETEO_QUOTA_TRACKER,
        "retry_after_seconds",
        lambda endpoint: waits[endpoint],
    )

    assert dl.bayes_precision_fusion_quota_cooldown_seconds() == 0
    waits["api.open-meteo.com/v1/forecast"] = 40
    assert dl.bayes_precision_fusion_quota_cooldown_seconds() == 40


def test_nbm_standard_fallback_discards_payload_when_metadata_changes(
    monkeypatch,
) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client
    import src.data.openmeteo_model_updates as metadata

    run = datetime(2026, 7, 27, 15, tzinfo=UTC)
    available = datetime(2026, 7, 27, 16, 4, tzinfo=UTC)
    updates = iter(
        (
            metadata.OpenMeteoModelUpdate(
                model="ncep_nbm_conus",
                last_run_initialisation_time=run,
                last_run_availability_time=available,
                last_run_modification_time=datetime(2026, 7, 27, 16, 3, tzinfo=UTC),
            ),
            metadata.OpenMeteoModelUpdate(
                model="ncep_nbm_conus",
                last_run_initialisation_time=run,
                last_run_availability_time=available,
                last_run_modification_time=datetime(2026, 7, 27, 16, 5, tzinfo=UTC),
            ),
        )
    )
    monkeypatch.setattr(
        metadata,
        "fetch_model_updates",
        lambda *_args, **_kwargs: (next(updates),),
    )

    def _fetch(url, _params, **_kwargs):
        if "single-runs" in url:
            raise RuntimeError("Client error '400 Bad Request'")
        return {
            "hourly": {
                "time": ["2026-07-28T00:00", "2026-07-28T12:00"],
                "temperature_2m": [20.0, 30.0],
            }
        }

    monkeypatch.setattr(client, "fetch", _fetch)
    got = dl._default_live_fetch_locations_batched(
        models=["ncep_nbm_conus"],
        locations=[
            (30.267, -97.743, "America/Chicago", (date(2026, 7, 28),)),
        ],
        run=run,
        source_available_at=available,
        forecast_hours=120,
    )

    result = got[0][date(2026, 7, 28)]
    assert "ncep_nbm_conus" not in result
    assert dl._BATCH_TRANSPORT_ERROR_KEY in result
    assert "metadata changed mid-fetch" in result[dl._BATCH_TRANSPORT_ERROR_KEY][0]


def test_standard_fallback_accepts_availability_skew_across_meta_replicas(
    monkeypatch,
) -> None:
    """Replicas behind meta.json disagree on availability for the same run; run identity
    is (model, last_run_initialisation_time) only, so an availability mismatch (before vs
    after, and vs the frozen expectation) must not refuse or discard the fetch."""
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client
    import src.data.openmeteo_model_updates as metadata

    model = "ncep_nbm_conus"
    run = datetime(2026, 7, 27, 15, tzinfo=UTC)
    modified = datetime(2026, 7, 27, 16, 3, tzinfo=UTC)
    expected_available = datetime(2026, 7, 27, 16, 4, tzinfo=UTC)
    before_available = datetime(2026, 7, 27, 16, 4, 11, tzinfo=UTC)
    after_available = datetime(2026, 7, 27, 16, 27, 39, tzinfo=UTC)
    updates = iter(
        (
            metadata.OpenMeteoModelUpdate(
                model=model,
                last_run_initialisation_time=run,
                last_run_availability_time=before_available,
                last_run_modification_time=modified,
            ),
            metadata.OpenMeteoModelUpdate(
                model=model,
                last_run_initialisation_time=run,
                last_run_availability_time=after_available,
                last_run_modification_time=modified,
            ),
        )
    )
    monkeypatch.setattr(
        metadata,
        "fetch_model_updates",
        lambda *_args, **_kwargs: (next(updates),),
    )

    def _fetch(_url, _params, **_kwargs):
        return {
            "hourly": {
                "time": ["2026-07-28T00:00", "2026-07-28T12:00"],
                "temperature_2m": [20.0, 30.0],
            }
        }

    monkeypatch.setattr(client, "fetch", _fetch)
    payloads, stamp = dl._fetch_standard_meta_stamped_payloads(
        model=model,
        locations=((30.267, -97.743, "America/Chicago", (date(2026, 7, 28),)),),
        run=run,
        source_available_at=expected_available,
        forecast_hours=120,
        deadline_monotonic=None,
    )

    assert len(payloads) == 1
    assert stamp.run == run
    assert stamp.modification_time == modified
    assert stamp.source_available_at == before_available


def test_nbm_meta_stamped_transport_is_persisted_as_physical_identity(
    tmp_path,
    monkeypatch,
) -> None:
    import src.data.bayes_precision_fusion_download as dl

    db = _forecast_db(tmp_path)
    run = datetime(2026, 7, 27, 15, tzinfo=UTC)
    available = datetime(2026, 7, 27, 16, 4, tzinfo=UTC)
    stamp = dl._StandardMetaStampedTransport(
        run=run,
        source_available_at=available,
        modification_time=datetime(2026, 7, 27, 16, 3, tzinfo=UTC),
        forecast_hours=120,
    )

    monkeypatch.setattr(
        dl,
        "_default_live_fetch_locations_batched",
        lambda **kwargs: [
            {
                target_date: {
                    "ncep_nbm_conus": (32.0, 20.0),
                    dl._BATCH_TRANSPORT_PROVENANCE_KEY: {
                        "ncep_nbm_conus": stamp,
                    },
                }
                for target_date in location[3]
            }
            for location in kwargs["locations"]
        ],
    )
    monkeypatch.setattr(dl, "_model_in_domain", lambda *_args, **_kwargs: True)
    report = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=run,
        targets=_two_city_targets(),
        models=("ncep_nbm_conus",),
        include_previous_runs=False,
        prune_after=False,
        allow_single_runs_fallback=False,
        frozen_source_runs={"ncep_nbm_conus": (run, available)},
    )

    assert report["written_row_count"] == 2
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT endpoint, endpoint_mode, source_family, product_id, request_params_json"
        " FROM raw_model_forecasts ORDER BY city"
    ).fetchall()
    conn.close()
    assert {row[0] for row in rows} == {"single_runs"}
    assert {row[1] for row in rows} == {"standard_api_meta_stamped"}
    assert {row[2] for row in rows} == {"openmeteo_standard_meta_stamped"}
    assert all("modified=2026-07-27T16:03:00+00:00" in row[3] for row in rows)
    assert all('"forecast_hours":120' in row[4] for row in rows)


def test_hourly_transports_forward_real_provider_past_hour_anchor(monkeypatch) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client
    import src.data.openmeteo_model_updates as metadata
    from src.data.openmeteo_model_updates import OpenMeteoModelUpdate

    run = datetime(2026, 9, 3, 0, tzinfo=UTC)
    available = datetime(2026, 9, 3, 3, tzinfo=UTC)
    modified = datetime(2026, 9, 3, 3, 1, tzinfo=UTC)
    seen: list[dict[str, object]] = []

    def fetch(_url, params, **_kwargs):
        seen.append(dict(params))
        return {"hourly": {}}

    monkeypatch.setattr(client, "fetch", fetch)
    dl._fetch_single_runs_hourly_payloads_batched(
        models=("ecmwf_ifs",),
        locations=((41.9, -87.6, "America/Chicago", (date(2026, 9, 3),)),),
        run=run,
        forecast_hours=72,
        past_hours=1,
    )

    monkeypatch.setattr(
        metadata,
        "fetch_model_updates",
        lambda *_args, **_kwargs: (
            OpenMeteoModelUpdate(
                model="ncep_nbm_conus",
                last_run_initialisation_time=run,
                last_run_availability_time=available,
                last_run_modification_time=modified,
            ),
        ),
    )
    dl._fetch_standard_meta_stamped_payloads(
        model="ncep_nbm_conus",
        locations=((41.9, -87.6, "America/Chicago", (date(2026, 9, 3),)),),
        run=run,
        source_available_at=available,
        forecast_hours=72,
        deadline_monotonic=None,
        past_hours=1,
    )

    assert [params["past_hours"] for params in seen] == [1, 1]


def test_source_clock_download_reuses_one_multi_location_response(
    tmp_path, monkeypatch
) -> None:
    import src.data.bayes_precision_fusion_download as dl

    db = _forecast_db(tmp_path)
    calls: list[list[tuple[float, float, str, date]]] = []

    def _locations(**kwargs):
        calls.append(list(kwargs["locations"]))
        return [
            {date(2026, 6, 9): {"ecmwf_ifs": (22.0, 10.0)}},
            {date(2026, 6, 9): {"ecmwf_ifs": (24.0, 12.0)}},
        ]

    monkeypatch.setattr(dl, "_default_live_fetch_locations_batched", _locations)
    monkeypatch.setattr(
        dl,
        "_default_live_fetch_batched",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("per-location fetch must not run")
        ),
    )
    monkeypatch.setattr(dl, "_read_source_clock_single_runs_requests", lambda **_kwargs: {})

    report = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=datetime(2026, 6, 8, 0, tzinfo=UTC),
        targets=_two_city_targets(),
        models=("ecmwf_ifs",),
        include_previous_runs=False,
        prune_after=False,
        allow_single_runs_fallback=False,
    )

    assert len(calls) == 1
    assert len(calls[0]) == 2
    assert report["written_row_count"] == 2
    assert report["single_runs_location_batch_count"] == 1
    assert report["single_runs_location_count"] == 2
    assert report["single_runs_location_target_date_count"] == 2


def test_source_clock_high_only_target_does_not_refetch_absent_low_sibling(
    tmp_path, monkeypatch
) -> None:
    import src.data.bayes_precision_fusion_download as dl

    db = _forecast_db(tmp_path)
    calls: list[object] = []

    def _locations(**kwargs):
        calls.append(kwargs["locations"])
        return [
            {
                target_date: {"ecmwf_ifs": (22.0, 10.0)}
                for target_date in location[3]
            }
            for location in kwargs["locations"]
        ]

    monkeypatch.setattr(dl, "_default_live_fetch_locations_batched", _locations)
    monkeypatch.setattr(dl, "_read_source_clock_single_runs_requests", lambda **_kwargs: {})
    kwargs = {
        "forecast_db": db,
        "cycle": datetime(2026, 6, 8, 0, tzinfo=UTC),
        "targets": _two_city_targets(),
        "models": ("ecmwf_ifs",),
        "include_previous_runs": False,
        "prune_after": False,
        "allow_single_runs_fallback": False,
    }

    first = dl.download_bayes_precision_fusion_extra_raw_inputs(**kwargs)
    second = dl.download_bayes_precision_fusion_extra_raw_inputs(**kwargs)

    assert first["written_row_count"] == 2
    assert second["written_row_count"] == 0
    assert len(calls) == 1
    assert _count(db, endpoint="single_runs", metric="high") == 2
    assert _count(db, endpoint="single_runs", metric="low") == 0


def test_source_clock_download_reuses_city_payload_across_target_dates(
    tmp_path, monkeypatch
) -> None:
    import src.data.bayes_precision_fusion_download as dl

    db = _forecast_db(tmp_path)
    targets = [
        dl.BayesPrecisionFusionDownloadTarget(
            city="Paris",
            metric="high",
            target_date=target_date,
            lead_days=lead_days,
            latitude=48.967,
            longitude=2.428,
            timezone_name="Europe/Paris",
        )
        for target_date, lead_days in (("2026-06-09", 1), ("2026-06-10", 2))
    ]
    calls: list[object] = []

    def _locations(**kwargs):
        calls.append(kwargs["locations"])
        return [
            {
                date(2026, 6, 9): {"ecmwf_ifs": (22.0, 10.0)},
                date(2026, 6, 10): {"ecmwf_ifs": (23.0, 11.0)},
            }
        ]

    monkeypatch.setattr(dl, "_default_live_fetch_locations_batched", _locations)
    monkeypatch.setattr(dl, "_read_source_clock_single_runs_requests", lambda **_kwargs: {})

    report = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=datetime(2026, 6, 8, 0, tzinfo=UTC),
        targets=targets,
        models=("ecmwf_ifs",),
        include_previous_runs=False,
        prune_after=False,
        allow_single_runs_fallback=False,
    )

    assert len(calls) == 1
    assert len(calls[0]) == 1
    assert calls[0][0][3] == (date(2026, 6, 9), date(2026, 6, 10))
    assert report["written_row_count"] == 2
    assert report["single_runs_location_count"] == 1
    assert report["single_runs_location_target_date_count"] == 2


def test_source_clock_multi_location_429_remains_retryable(tmp_path, monkeypatch) -> None:
    import src.data.bayes_precision_fusion_download as dl

    db = _forecast_db(tmp_path)
    error = {
        dl._BATCH_TRANSPORT_ERROR_KEY: (
            "Open-Meteo 429 Too Many Requests",
            None,
        )
    }
    monkeypatch.setattr(
        dl,
        "_default_live_fetch_locations_batched",
        lambda **kwargs: [
            {target_date: dict(error) for target_date in location[3]}
            for location in kwargs["locations"]
        ],
    )
    monkeypatch.setattr(dl, "_read_source_clock_single_runs_requests", lambda **_kwargs: {})

    report = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=datetime(2026, 6, 8, 0, tzinfo=UTC),
        targets=_two_city_targets(),
        models=("ecmwf_ifs",),
        include_previous_runs=False,
        prune_after=False,
        allow_single_runs_fallback=False,
    )

    assert report["status"] == "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE"
    assert report["transport_aborted_remaining_targets"] is True
    assert report["written_row_count"] == 0
    assert report["single_runs_location_batch_count"] == 1


def test_source_clock_exact_run_geometry_gap_is_terminal_for_that_run(
    tmp_path, monkeypatch
) -> None:
    import src.data.bayes_precision_fusion_download as dl

    db = _forecast_db(tmp_path)
    run = datetime(2026, 9, 3, 9, tzinfo=UTC)
    gap = {
        dl._BATCH_EXACT_RUN_UNMATERIALIZABLE_KEY: {
            "icon_eu": "ValueError:partial local-day coverage"
        }
    }
    monkeypatch.setattr(
        dl,
        "_default_live_fetch_locations_batched",
        lambda **kwargs: [
            {target_date: dict(gap) for target_date in location[3]}
            for location in kwargs["locations"]
        ],
    )
    monkeypatch.setattr(dl, "_read_source_clock_single_runs_requests", lambda **_kwargs: {})

    report = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=run,
        targets=_two_city_targets(),
        models=("icon_eu",),
        include_previous_runs=False,
        prune_after=False,
        allow_single_runs_fallback=False,
        frozen_source_runs={"icon_eu": (run, run)},
    )

    assert report["status"] == (
        "BAYES_PRECISION_FUSION_EXTRA_EXACT_RUN_UNMATERIALIZABLE"
    )
    assert report["written_row_count"] == 0
    assert len(report["exact_run_unmaterializable"]) == 2
    assert {row["city"] for row in report["exact_run_unmaterializable"]} == {
        "Paris",
        "Berlin",
    }


def test_source_clock_internal_nonfinite_gap_remains_retryable(tmp_path, monkeypatch) -> None:
    """A recoverable payload hole must not advance the source cursor as a permanent gap."""
    import src.data.bayes_precision_fusion_download as dl

    db = _forecast_db(tmp_path)
    run = datetime(2026, 9, 3, 9, tzinfo=UTC)
    gap = {
        dl._BATCH_EXACT_RUN_UNMATERIALIZABLE_KEY: {
            "icon_eu": "ValueError:internal hourly temperature is non-finite"
        }
    }
    monkeypatch.setattr(
        dl,
        "_default_live_fetch_locations_batched",
        lambda **kwargs: [
            {target_date: dict(gap) for target_date in location[3]}
            for location in kwargs["locations"]
        ],
    )
    monkeypatch.setattr(dl, "_read_source_clock_single_runs_requests", lambda **_kwargs: {})
    dl._EXACT_RUN_UNMATERIALIZABLE_MEMO.clear()

    report = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=run,
        targets=_two_city_targets(),
        models=("icon_eu",),
        include_previous_runs=False,
        prune_after=False,
        allow_single_runs_fallback=False,
        frozen_source_runs={"icon_eu": (run, run)},
    )

    assert report["status"] == "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE"
    assert report["written_row_count"] == 0
    assert len(report["exact_run_unmaterializable"]) == 2
    assert dl._EXACT_RUN_UNMATERIALIZABLE_MEMO == {}


def test_source_clock_exact_run_gap_does_not_discard_other_city_progress(
    tmp_path, monkeypatch
) -> None:
    import src.data.bayes_precision_fusion_download as dl

    db = _forecast_db(tmp_path)
    gap = {
        dl._BATCH_EXACT_RUN_UNMATERIALIZABLE_KEY: {
            "ecmwf_ifs": "ValueError:partial local-day coverage"
        }
    }
    monkeypatch.setattr(
        dl,
        "_default_live_fetch_locations_batched",
        lambda **_kwargs: [
            {date(2026, 6, 9): dict(gap)},
            {date(2026, 6, 9): {"ecmwf_ifs": (24.0, 12.0)}},
        ],
    )
    monkeypatch.setattr(dl, "_read_source_clock_single_runs_requests", lambda **_kwargs: {})

    report = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=datetime(2026, 6, 8, 0, tzinfo=UTC),
        targets=_two_city_targets(),
        models=("ecmwf_ifs",),
        include_previous_runs=False,
        prune_after=False,
        allow_single_runs_fallback=False,
    )

    assert report["status"] == "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
    assert report["written_row_count"] == 1
    assert report["committed_families"] == (("Berlin", "2026-06-09", "high"),)
    assert report["exact_run_unmaterializable"][0]["city"] == "Paris"


def test_persist_lock_obeys_source_clock_deadline(tmp_path) -> None:
    from src.data.bayes_precision_fusion_download import (
        _PersistDeadlineExceeded,
        _persist_chunk_with_lock_retry,
    )

    db = _forecast_db(tmp_path)
    locker = sqlite3.connect(str(db))
    locker.execute("PRAGMA journal_mode=WAL")
    locker.execute("BEGIN IMMEDIATE")
    started = time.monotonic()
    try:
        with pytest.raises(_PersistDeadlineExceeded):
            _persist_chunk_with_lock_retry(
                db,
                (),
                deadline_monotonic=started + 0.08,
                ensure_schema=False,
            )
    finally:
        locker.rollback()
        locker.close()

    assert time.monotonic() - started < 1.0


def test_persist_declares_live_writer_intent_before_schema_work(tmp_path, monkeypatch) -> None:
    import src.state.schema.v2_schema as schema
    from src.data.bayes_precision_fusion_download import _persist_chunk_with_lock_retry
    from src.state.db_writer_lock import WriteClass, db_writer_lock

    db = _forecast_db(tmp_path)
    original = schema.ensure_replacement_forecast_live_schema

    def _ensure(conn):
        with pytest.raises(BlockingIOError):
            with db_writer_lock(db, WriteClass.LIVE, blocking=False):
                pass
        original(conn)

    monkeypatch.setattr(schema, "ensure_replacement_forecast_live_schema", _ensure)
    assert _persist_chunk_with_lock_retry(db, ()) == (0, 0)


def test_download_initializes_persist_schema_once_per_fanout(tmp_path, monkeypatch) -> None:
    import src.data.bayes_precision_fusion_download as dl

    db = _forecast_db(tmp_path)
    ensure_flags: list[bool] = []

    def _persist(_forecast_db, rows, **kwargs):
        ensure_flags.append(bool(kwargs["ensure_schema"]))
        return len(rows), 0

    monkeypatch.setattr(dl, "_persist_chunk_with_lock_retry", _persist)
    report = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=datetime(2026, 6, 8, 0, tzinfo=UTC),
        targets=_two_city_targets(),
        models=("ecmwf_ifs",),
        include_previous_runs=False,
        prune_after=False,
        single_runs_fetch=lambda **_kwargs: 20.0,
    )

    assert report["written_row_count"] == 2
    assert ensure_flags == [True, False]


# =====================================================================================
# (c) FAIL-SOFT per model: a raising fetch drops only that model
# =====================================================================================
def test_download_failsoft_per_model(tmp_path) -> None:
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)

    def _single(*, model, **k):
        if model == "gfs_global":
            raise RuntimeError("simulated network blowup for gfs_global")
        return 20.0

    report = download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=datetime(2026, 6, 8, 0, tzinfo=UTC), targets=_targets(),
        single_runs_fetch=_single, previous_runs_fetch=lambda **k: 19.0,
    )
    assert report["status"] == "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
    # gfs_global single_runs dropped; others present.
    assert _count(db, endpoint="single_runs", model="gfs_global") == 0
    assert _count(db, endpoint="single_runs", model="icon_global") == 1


# =====================================================================================
# (d) None fetch -> model simply absent (fail-soft drop, no crash)
# =====================================================================================
def test_download_none_fetch_drops_model(tmp_path) -> None:
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)

    def _single(*, model, **k):
        return None if model == "jma_seamless" else 20.0

    download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=datetime(2026, 6, 8, 0, tzinfo=UTC), targets=_targets(),
        single_runs_fetch=_single, previous_runs_fetch=lambda **k: None,
    )
    assert _count(db, endpoint="single_runs", model="jma_seamless") == 0
    assert _count(db, endpoint="previous_runs") == 0  # all previous_runs returned None


# =====================================================================================
# (e) retention prune: rows older than the cutoff are deleted
# =====================================================================================
def test_download_prunes_old_rows(tmp_path) -> None:
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs, RETENTION_DAYS

    db = _forecast_db(tmp_path)
    # Seed one ancient row (captured ~200d ago) and one recent row.
    conn = sqlite3.connect(str(db))
    old_captured = (datetime(2026, 6, 8, tzinfo=UTC).timestamp() - (RETENTION_DAYS + 20) * 86400)
    old_iso = datetime.fromtimestamp(old_captured, tz=UTC).isoformat()
    conn.execute(
        """INSERT INTO raw_model_forecasts
           (model, city, target_date, metric, source_cycle_time, source_available_at,
            captured_at, lead_days, forecast_value_c, endpoint)
           VALUES ('gfs_global','Paris','2025-11-01','high','x','y',?,1,20.0,'previous_runs')""",
        (old_iso,),
    )
    conn.commit()
    conn.close()
    assert _count(db) == 1

    report = download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=datetime(2026, 6, 8, 0, tzinfo=UTC), targets=[],
        single_runs_fetch=lambda **k: 20.0, previous_runs_fetch=lambda **k: 20.0,
    )
    assert report["pruned_row_count"] == 1
    assert _count(db) == 0, "rows older than the retention cutoff must be pruned"


# =====================================================================================
# (f) idempotent re-run: UNIQUE(model,city,target_date,metric,source_cycle_time,endpoint)
# =====================================================================================
def test_download_is_idempotent(tmp_path) -> None:
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)
    cycle = datetime(2026, 6, 8, 0, tzinfo=UTC)
    kwargs = dict(forecast_db=db, cycle=cycle, targets=_targets(),
                  single_runs_fetch=lambda **k: 20.0, previous_runs_fetch=lambda **k: 19.0)
    download_bayes_precision_fusion_extra_raw_inputs(**kwargs)
    n1 = _count(db)
    download_bayes_precision_fusion_extra_raw_inputs(**kwargs)  # same cycle -> no duplicate rows
    n2 = _count(db)
    assert n1 == n2, "re-running the same cycle must not duplicate rows (UNIQUE upsert)"


# =====================================================================================
# (g) FIX 1 — the ANCHOR (ecmwf_ifs) MUST be captured, else its walk-forward history is
#     forever empty (have_anchor=False -> fusion stuck EQUAL_WEIGHT). The download set
#     MUST include ANCHOR_MODEL, and the PERSISTED `model` column MUST be the anchor
#     identity "ecmwf_ifs" (the key BayesPrecisionFusionHistoryProvider / capture join on), NOT the
#     Open-Meteo model id. The OM previous-runs fetch uses the OM ECMWF id (ecmwf_ifs025),
#     but the STORED model col = "ecmwf_ifs".
# =====================================================================================
def test_download_includes_anchor_and_stores_ecmwf_ifs_identity(tmp_path) -> None:
    from src.data.bayes_precision_fusion_download import (
        BAYES_PRECISION_FUSION_EXTRA_MODELS,
        download_bayes_precision_fusion_extra_raw_inputs,
    )
    from src.forecast.model_selection import ANCHOR_MODEL

    # The download set MUST include the anchor (ecmwf_ifs); otherwise no anchor history
    # ever accrues and the fusion can never leave EQUAL_WEIGHT.
    assert ANCHOR_MODEL in BAYES_PRECISION_FUSION_EXTRA_MODELS, (
        "the anchor (ecmwf_ifs) must be in the BAYES_PRECISION_FUSION capture set so its previous_runs "
        "history accrues -> have_anchor=True -> T2_BAYES"
    )

    db = _forecast_db(tmp_path)
    cycle = datetime(2026, 6, 8, 0, 0, tzinfo=UTC)

    seen_prev_models: list[str] = []

    def _single(*, model, **k):
        return 20.0

    def _previous(*, model, **k):
        seen_prev_models.append(model)
        return 19.0

    download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=cycle, targets=_targets(),
        single_runs_fetch=_single, previous_runs_fetch=_previous,
    )

    # The previous_runs fetch was asked for the anchor under its STORED identity
    # (ecmwf_ifs) — the OM-id translation is the fetch's internal concern.
    assert ANCHOR_MODEL in seen_prev_models, "anchor previous_runs fetch must be invoked"

    # A previous_runs row keyed model='ecmwf_ifs' (the anchor identity) was persisted.
    assert _count(db, endpoint="previous_runs", model="ecmwf_ifs") == 1, (
        "anchor previous_runs row must be stored keyed model='ecmwf_ifs' (the join key)"
    )
    # And a forward single_runs anchor row too (the anchor is captured both ways).
    assert _count(db, endpoint="single_runs", model="ecmwf_ifs") == 1
    # The OM model id (ecmwf_ifs025) MUST NOT leak into the stored model column.
    assert _count(db, model="ecmwf_ifs025") == 0, (
        "the OM model id must never be the stored model col; store the anchor identity"
    )


# =====================================================================================
# DOMAIN GATE RELATIONSHIP TESTS (FAULT-B fix: no constructable 400 for regional models)
#
# Relationship under test: for each configured model the download request is only built
# and sent when the city coordinate is inside the model's geographic domain, so "No data
# is available for this location" HTTP 400s become structurally impossible. A
# domain-excluded model is recorded in domain_excluded (loud), not silently absent.
# =====================================================================================

def _tokyo_target():
    """A city outside icon_eu/icon_d2/arome domains (Tokyo)."""
    from src.data.bayes_precision_fusion_download import BayesPrecisionFusionDownloadTarget
    return [
        BayesPrecisionFusionDownloadTarget(city="Tokyo", metric="high", target_date="2026-06-09",
                          lead_days=1, latitude=35.553, longitude=139.781,
                          timezone_name="Asia/Tokyo"),
    ]


def _paris_target():
    """A city inside all EU domains (Paris)."""
    from src.data.bayes_precision_fusion_download import BayesPrecisionFusionDownloadTarget
    return [
        BayesPrecisionFusionDownloadTarget(city="Paris", metric="high", target_date="2026-06-09",
                          lead_days=1, latitude=48.967, longitude=2.428,
                          timezone_name="Europe/Paris"),
    ]


def test_domain_gate_no_request_for_regional_at_out_of_domain_city(tmp_path) -> None:
    """icon_eu, icon_d2, meteofrance_arome_france_hd MUST NOT be fetched for Tokyo.

    The single-runs and previous-runs APIs return HTTP 400 for out-of-domain coords;
    this gate prevents those requests from ever being built, making the 400 category
    structurally impossible.
    """
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)
    fetched_models: list[str] = []

    def _single(*, model, **k):
        fetched_models.append(model)
        return 20.0

    def _previous(*, model, **k):
        fetched_models.append(model)
        return 19.0

    report = download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=datetime(2026, 6, 9, 0, tzinfo=UTC),
        targets=_tokyo_target(),
        single_runs_fetch=_single, previous_runs_fetch=_previous,
    )

    # No request must be built for domain-limited models at Tokyo.
    assert "icon_eu" not in fetched_models, (
        "icon_eu must not be requested for Tokyo (out-of-EU domain)"
    )
    assert "icon_d2" not in fetched_models, (
        "icon_d2 must not be requested for Tokyo (out-of-Central-EU domain)"
    )
    assert "meteofrance_arome_france_hd" not in fetched_models, (
        "meteofrance_arome_france_hd must not be requested for Tokyo (out-of-France domain)"
    )

    # The skips must be loudly recorded in domain_excluded.
    excluded_set = set(report["domain_excluded"])
    assert any("icon_eu" in e and "Tokyo" in e for e in excluded_set), (
        "icon_eu:Tokyo must appear in domain_excluded"
    )
    assert any("icon_d2" in e and "Tokyo" in e for e in excluded_set), (
        "icon_d2:Tokyo must appear in domain_excluded"
    )
    assert any("meteofrance_arome_france_hd" in e and "Tokyo" in e for e in excluded_set), (
        "meteofrance_arome_france_hd:Tokyo must appear in domain_excluded"
    )

    # Global models (gfs_global, icon_global, gem_global, jma_seamless, ecmwf_ifs) MUST
    # still be fetched — the domain gate must not touch globals.
    from src.forecast.model_selection import ANCHOR_MODEL, DECORR_GLOBALS
    for global_model in list(DECORR_GLOBALS) + [ANCHOR_MODEL]:
        assert global_model in fetched_models, (
            f"global model {global_model} must still be fetched for out-of-domain city"
        )


def test_domain_gate_all_models_fetched_for_in_domain_city(tmp_path) -> None:
    """All configured models including icon_eu/icon_d2/arome MUST be fetched for Paris.

    The gate must not over-exclude: in-domain cities should request every model.
    """
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)
    fetched_models: list[str] = []

    def _single(*, model, **k):
        fetched_models.append(model)
        return 20.0

    def _previous(*, model, **k):
        return 19.0

    download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=datetime(2026, 6, 9, 0, tzinfo=UTC),
        targets=_paris_target(),
        single_runs_fetch=_single, previous_runs_fetch=_previous,
    )

    for regional in ("icon_eu", "icon_d2", "meteofrance_arome_france_hd"):
        assert regional in fetched_models, (
            f"{regional} must be fetched for Paris (in-domain)"
        )


def test_domain_gate_unavailable_global_is_loud_not_silent(tmp_path) -> None:
    """A global model (always in-domain) that fails to fetch must appear in `dropped`,
    NOT in `domain_excluded`. The ensemble-completeness report must flag it.

    This guards STEP 4: real upstream failures on global models are distinguishable
    from expected domain-exclusion absences.
    """
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs
    db = _forecast_db(tmp_path)

    def _single(*, model, **k):
        if model == "icon_global":
            return None  # simulate upstream unavailability
        return 20.0

    report = download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=datetime(2026, 6, 9, 0, tzinfo=UTC),
        targets=_paris_target(),
        single_runs_fetch=_single, previous_runs_fetch=lambda **k: 19.0,
    )

    # icon_global (worldwide global, always in-domain) failure must be in `dropped`. (2026-06-17:
    # gfs_global was dropped from the fusion; icon_global is the canonical worldwide global here.)
    assert "icon_global:single_runs" in report["dropped"], (
        "unavailable global model must be in dropped, not silently absent"
    )
    # It must NOT be in domain_excluded (that is reserved for domain-geographic skips).
    excluded_set = set(report["domain_excluded"])
    assert not any("icon_global" in e for e in excluded_set), (
        "icon_global must never appear in domain_excluded (it is a global model)"
    )
    # global_models_unavailable must flag it.
    assert "icon_global" in report["global_models_unavailable"], (
        "icon_global single_runs failure must appear in global_models_unavailable"
    )


def test_domain_limited_model_is_not_misclassified_as_global(tmp_path) -> None:
    """An in-domain NBM gap is scoped source coverage, not a worldwide model outage."""
    from src.data.bayes_precision_fusion_download import (
        BayesPrecisionFusionDownloadTarget,
        download_bayes_precision_fusion_extra_raw_inputs,
    )

    db = _forecast_db(tmp_path)
    target = BayesPrecisionFusionDownloadTarget(
        city="Denver",
        metric="high",
        target_date="2026-06-09",
        lead_days=1,
        latitude=39.856,
        longitude=-104.676,
        timezone_name="America/Denver",
    )

    report = download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=datetime(2026, 6, 9, 0, tzinfo=UTC),
        targets=[target],
        models=("ncep_nbm_conus",),
        include_previous_runs=False,
        single_runs_fetch=lambda **_kwargs: None,
    )

    assert "ncep_nbm_conus:single_runs" in report["dropped"]
    assert report["domain_excluded"] == ()
    assert report["global_models_expected"] == 0
    assert report["global_models_unavailable"] == []


def test_scoped_global_drop_does_not_fail_cycle_when_model_succeeds_elsewhere(tmp_path) -> None:
    """A global model with at least one successful row for the cycle is degraded by scope,
    not unavailable for the whole BPF capture job.

    This guards the live preflight deadlock where one residual target gap marked
    bayes_precision_fusion_capture FAILED even though the same global model had already
    landed rows for other targets in that cycle.
    """
    from src.data.bayes_precision_fusion_download import (
        BayesPrecisionFusionDownloadTarget,
        download_bayes_precision_fusion_extra_raw_inputs,
    )

    db = _forecast_db(tmp_path)
    targets = [
        BayesPrecisionFusionDownloadTarget(
            city="Paris",
            metric="high",
            target_date="2026-06-09",
            lead_days=1,
            latitude=48.967,
            longitude=2.428,
            timezone_name="Europe/Paris",
        ),
        BayesPrecisionFusionDownloadTarget(
            city="Berlin",
            metric="high",
            target_date="2026-06-09",
            lead_days=1,
            latitude=52.520,
            longitude=13.405,
            timezone_name="Europe/Berlin",
        ),
    ]

    def _single(*, model, latitude, **_k):
        if model == "icon_global" and float(latitude) < 50.0:
            return None
        return 20.0

    report = download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=datetime(2026, 6, 9, 0, tzinfo=UTC),
        targets=targets,
        single_runs_fetch=_single,
        previous_runs_fetch=lambda **_k: 19.0,
    )

    assert "icon_global:single_runs" in report["dropped"]
    assert "icon_global" in report["global_models_dropped_scoped"]
    assert "icon_global" not in report["global_models_unavailable"]


def test_batched_single_runs_transport_failure_is_retryable_not_empty_success(tmp_path, monkeypatch) -> None:
    """A process-local Open-Meteo quota/cooldown failure must not flatten to a successful empty pass."""
    import src.data.bayes_precision_fusion_download as dl
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)
    single_calls: list[tuple[str, ...]] = []
    previous_calls: list[tuple[str, ...]] = []

    def _single_batch_fail(**kwargs):
        single_calls.append(tuple(kwargs["models"]))
        return {
            dl._BATCH_TRANSPORT_ERROR_KEY: (
                "Open-Meteo quota exhausted (2 calls today)",
                None,
            )
        }

    monkeypatch.setattr(dl, "_default_live_fetch_batched", _single_batch_fail)
    monkeypatch.setattr(dl, "_read_source_clock_single_runs_requests", lambda **_kwargs: {})

    def _previous_batch(**kwargs):
        previous_calls.append(tuple(kwargs["models"]))
        return {}

    monkeypatch.setattr(dl, "_default_previous_runs_fetch_batched", _previous_batch)

    report = download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=datetime(2026, 6, 9, 12, tzinfo=UTC),
        targets=_two_city_targets(),
    )

    assert report["status"] == "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE"
    assert report["written_row_count"] == 0
    assert report["transport_errors"]
    assert "Open-Meteo quota exhausted" in report["transport_errors"][0]
    assert report["transport_aborted_remaining_targets"] is True
    assert report["attempted_target_group_count"] == 1
    assert len(single_calls) == 1
    assert previous_calls == []


def test_later_group_transport_abort_reports_exact_attempted_prefix(
    tmp_path,
    monkeypatch,
) -> None:
    """A quota abort on group two durably receipts both attempted groups, not one/all."""
    import src.data.bayes_precision_fusion_download as dl

    db = _forecast_db(tmp_path)
    calls: list[str] = []

    def _single_batch(**kwargs):
        city = "Paris" if float(kwargs["latitude"]) < 50.0 else "Berlin"
        calls.append(city)
        if city == "Berlin":
            return {
                dl._BATCH_TRANSPORT_ERROR_KEY: (
                    "Open-Meteo quota exhausted after first group",
                    None,
                )
            }
        return {"ecmwf_ifs": (20.0, 10.0)}

    monkeypatch.setattr(dl, "_default_live_fetch_batched", _single_batch)
    monkeypatch.setattr(
        dl,
        "_read_source_clock_single_runs_requests",
        lambda **_kwargs: {},
    )

    report = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=datetime(2026, 6, 9, 12, tzinfo=UTC),
        targets=_two_city_targets(),
        models=("ecmwf_ifs",),
        include_previous_runs=False,
        prune_after=False,
    )

    assert calls == ["Paris", "Berlin"]
    assert report["status"] == "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE"
    assert report["transport_aborted_remaining_targets"] is True
    assert report["attempted_target_group_count"] == 2


def test_scoped_transport_gap_with_progress_is_downloaded_not_failed(tmp_path, monkeypatch) -> None:
    """One scoped transport gap must not mark the whole BPF lane failed after durable progress.

    Live capture is row-idempotent and coverage-healed. If a pass writes rows for later
    city/date scopes, the residual scoped gap is handled by the fixpoint/coverage gate instead of
    poisoning forecast-pipeline health as a total transport failure.
    """
    import src.data.bayes_precision_fusion_download as dl
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)
    calls = 0

    def _single_batch(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                dl._BATCH_TRANSPORT_ERROR_KEY: (
                    "Client error '400 Bad Request' for scoped regional model",
                    None,
                )
            }
        return {model: (20.0, 10.0) for model in kwargs["models"]}

    monkeypatch.setattr(dl, "_default_live_fetch_batched", _single_batch)
    monkeypatch.setattr(dl, "_default_previous_runs_fetch_batched", lambda **_kwargs: {})

    report = download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=datetime(2026, 6, 9, 12, tzinfo=UTC),
        targets=_two_city_targets(),
    )

    assert report["transport_errors"]
    assert report["transport_aborted_remaining_targets"] is False
    assert report["written_row_count"] > 0
    assert report["status"] == "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"

    monkeypatch.setattr(
        dl,
        "_default_live_fetch_batched",
        lambda **_kwargs: {
            dl._BATCH_TRANSPORT_ERROR_KEY: (
                "Client error '400 Bad Request' for residual scoped model",
                None,
            )
        },
    )
    rerun = download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=datetime(2026, 6, 9, 12, tzinfo=UTC),
        targets=_two_city_targets(),
    )

    assert rerun["transport_errors"]
    assert rerun["written_row_count"] == 0
    assert rerun["status"] == "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"


def test_batched_single_runs_uses_source_clock_public_run(tmp_path, monkeypatch) -> None:
    """Do not request an unpublished anchor cycle for a model with an older public run."""
    import src.data.bayes_precision_fusion_download as dl
    from src.data.bayes_precision_fusion_download import download_bayes_precision_fusion_extra_raw_inputs

    db = _forecast_db(tmp_path)
    public_run = datetime(2026, 6, 25, 6, tzinfo=UTC)
    available_at = "2026-06-25T12:46:33+00:00"
    seen_runs: list[datetime] = []

    monkeypatch.setattr(dl, "BAYES_PRECISION_FUSION_EXTRA_MODELS", ("ecmwf_ifs",))
    monkeypatch.setattr(dl, "BAYES_PRECISION_FUSION_CANDIDATE_ACCRUAL_MODELS", ())
    monkeypatch.setattr(
        dl,
        "_read_source_clock_single_runs_requests",
        lambda *, decision_time: {
            "ecmwf_ifs": dl._SourceClockSingleRunsRequest(
                run=public_run,
                source_available_at=available_at,
            )
        },
    )

    def _single_batch(**kwargs):
        seen_runs.append(kwargs["run"])
        return {"ecmwf_ifs": (22.0, 10.0)}

    monkeypatch.setattr(dl, "_default_live_fetch_batched", _single_batch)
    monkeypatch.setattr(dl, "_default_previous_runs_fetch_batched", lambda **_kwargs: {})

    report = download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=datetime(2026, 6, 25, 12, tzinfo=UTC),
        targets=_targets(),
    )

    assert report["status"] == "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
    assert seen_runs == [public_run]
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT model, source_cycle_time, source_available_at FROM raw_model_forecasts"
        " WHERE endpoint='single_runs'"
    ).fetchone()
    conn.close()
    assert row["model"] == "ecmwf_ifs"
    assert row["source_cycle_time"] == public_run.isoformat()
    assert row["source_available_at"] == available_at


def test_batched_single_runs_uses_frozen_run_without_rereading_metadata(
    tmp_path, monkeypatch
) -> None:
    import src.data.bayes_precision_fusion_download as dl

    db = _forecast_db(tmp_path)
    frozen_run = datetime(2026, 6, 25, 0, tzinfo=UTC)
    frozen_available = datetime(2026, 6, 25, 4, tzinfo=UTC)
    seen_runs: list[datetime] = []

    monkeypatch.setattr(
        dl,
        "_read_source_clock_single_runs_requests",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("mutable metadata reread")),
    )

    def _single_batch(**kwargs):
        seen_runs.append(kwargs["run"])
        return {"ecmwf_ifs": (22.0, 10.0)}

    monkeypatch.setattr(dl, "_default_live_fetch_batched", _single_batch)

    report = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=frozen_run,
        targets=_targets(),
        models=("ecmwf_ifs",),
        include_previous_runs=False,
        frozen_source_runs={
            "ecmwf_ifs": (frozen_run, frozen_available),
        },
    )

    assert seen_runs == [frozen_run]
    assert report["single_runs_request_cycles"] == {
        "ecmwf_ifs": frozen_run.isoformat()
    }
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT source_cycle_time, source_available_at FROM raw_model_forecasts"
        " WHERE endpoint='single_runs'"
    ).fetchone()
    conn.close()
    assert row == (frozen_run.isoformat(), frozen_available.isoformat())


def test_bpf_batched_fetch_uses_injected_quota_tracker(monkeypatch) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as om
    from src.data.openmeteo_quota import OpenMeteoQuotaTracker

    class _Resp:
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "hourly": {
                    "temperature_2m_previous_day1_icon_global": [18.0, 19.5, 17.25],
                }
            }

    tracker = OpenMeteoQuotaTracker()
    monkeypatch.setattr(dl, "_BPF_OPENMETEO_QUOTA_TRACKER", tracker)

    class _Client:
        def get(self, *_args, **_kwargs):
            return _Resp()

    monkeypatch.setattr(om, "_SHARED_HTTP_CLIENT", _Client())

    got = dl._default_previous_runs_fetch_batched(
        models=["icon_global"],
        latitude=48.967,
        longitude=2.428,
        timezone_name="Europe/Paris",
        target_date="2026-06-09",
        lead_days=1,
    )

    assert got == {"icon_global": (19.5, 17.25)}
    assert tracker.calls_today() == 1


def test_default_previous_runs_batched_uses_comma_model_param(monkeypatch) -> None:
    """Batched Open-Meteo requests must use the documented comma-separated models value."""
    import src.data.openmeteo_client as om
    from src.data import bayes_precision_fusion_download as dl
    from src.forecast.model_selection import ANCHOR_MODEL

    captured: dict[str, object] = {}

    class _Resp:
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "hourly": {
                    "temperature_2m_previous_day1_icon_global": [18.0, 19.5, 17.25],
                    "temperature_2m_previous_day1_ecmwf_ifs025": [20.0, 21.0, 19.0],
                }
            }

    def _fake_get(_url, *, params, **_kwargs):
        captured["models"] = params["models"]
        return _Resp()

    class _Client:
        get = staticmethod(_fake_get)

    monkeypatch.setattr(om, "_SHARED_HTTP_CLIENT", _Client())

    got = dl._default_previous_runs_fetch_batched(
        models=["icon_global", ANCHOR_MODEL],
        latitude=48.967,
        longitude=2.428,
        timezone_name="Europe/Paris",
        target_date="2026-06-09",
        lead_days=1,
    )

    assert captured["models"] == "icon_global,ecmwf_ifs025"
    assert got == {"icon_global": (19.5, 17.25), ANCHOR_MODEL: (21.0, 19.0)}


def test_default_single_runs_batched_400_falls_back_per_model(monkeypatch) -> None:
    """A provider 400 on the combined models request must not erase the whole live cycle.

    Open-Meteo sometimes rejects a batched models combination even though the individual model
    requests are valid. The live current-capture lane must preserve the valid per-model rows so
    replacement posterior materialization can still advance on fresh evidence.
    """
    import src.data.openmeteo_client as om
    from src.data import bayes_precision_fusion_download as dl

    requested_models: list[object] = []

    def _payload(value: float) -> dict:
        return {
            "hourly": {
                "time": [
                    "2026-06-09T00:00",
                    "2026-06-09T03:00",
                    "2026-06-09T12:00",
                    "2026-06-09T21:00",
                ],
                "temperature_2m": [value - 1.0, value, value + 2.0, value + 1.0],
            },
            "hourly_units": {"temperature_2m": "°C"},
        }

    def _fake_fetch(_url, params, **_kwargs):
        requested_models.append(params["models"])
        if params["models"] == "icon_global,ecmwf_ifs":
            raise RuntimeError("Client error '400 Bad Request' for batched models")
        if params["models"] == "icon_global":
            return _payload(20.0)
        if params["models"] == "ecmwf_ifs":
            return _payload(22.0)
        raise RuntimeError("unexpected model")

    monkeypatch.setattr(om, "fetch", _fake_fetch)

    got = dl._default_live_fetch_batched(
        models=["icon_global", "ecmwf_ifs"],
        latitude=48.967,
        longitude=2.428,
        timezone_name="Europe/Paris",
        run=datetime(2026, 6, 8, 0, tzinfo=UTC),
        target_local_date=date(2026, 6, 9),
        forecast_hours=120,
    )

    assert requested_models == ["icon_global,ecmwf_ifs", "icon_global", "ecmwf_ifs"]
    assert got["icon_global"] == (22.0, 19.0)
    assert got["ecmwf_ifs"] == (24.0, 21.0)
    assert dl._BATCH_TRANSPORT_ERROR_KEY in got
    assert "400 Bad Request" in got[dl._BATCH_TRANSPORT_ERROR_KEY][0]


def test_default_previous_runs_fetch_uses_om_ecmwf_id_for_anchor(monkeypatch) -> None:
    """FIX 1 mapping: the DEFAULT previous-runs fetch for the anchor must send the OM
    ECMWF previous-runs model id (ecmwf_ifs025) to the OM API, even though the stored
    model col is the anchor identity 'ecmwf_ifs'. Guards the fetch-vs-store split so a
    future edit cannot send 'ecmwf_ifs' (no OM previous-runs entry) and silently drop
    the anchor history fail-soft.
    """
    import src.data.openmeteo_client as om
    from src.data.bayes_precision_fusion_download import _default_previous_runs_fetch
    from src.forecast.model_selection import ANCHOR_MODEL

    captured: dict[str, object] = {}

    def _fake_fetch(url, params, *, endpoint_label):
        captured["models"] = params["models"]
        return {"hourly": {params["hourly"]: [18.0, 19.0, 17.5]}}

    monkeypatch.setattr(om, "fetch", _fake_fetch)

    value = _default_previous_runs_fetch(
        model=ANCHOR_MODEL, latitude=48.967, longitude=2.428,
        timezone_name="Europe/Paris", target_date="2026-06-09",
        lead_days=1, metric="high",
    )
    assert value == 19.0  # max over the local-day window for 'high'
    assert captured["models"] == "ecmwf_ifs025", (
        "anchor previous-runs OM fetch must use the OM ECMWF id ecmwf_ifs025"
    )


_MEMO_GAP_REASON = (
    "ValueError:partial local-day coverage is not an elapsed-prefix-only "
    "Day0 slice with remaining-day coverage"
)


def _one_city_two_date_targets(date_a: str, date_b: str):
    from src.data.bayes_precision_fusion_download import BayesPrecisionFusionDownloadTarget

    return [
        BayesPrecisionFusionDownloadTarget(
            city="Paris", metric=metric, target_date=target_date,
            lead_days=lead_days, latitude=48.967, longitude=2.428,
            timezone_name="Europe/Paris",
        )
        for target_date, lead_days in ((date_a, 0), (date_b, 4))
        for metric in ("high", "low")
    ]


def _memo_locations_stub(dl, calls, date_a, gap_reason):
    """Location-batched stub: date_a materializes, every other date reports a run gap."""

    def _locations(**kwargs):
        calls.append([tuple(location[3]) for location in kwargs["locations"]])
        return [
            {
                target_local_date: (
                    {"icon_eu": (30.0, 20.0)}
                    if target_local_date == date_a
                    else {
                        dl._BATCH_EXACT_RUN_UNMATERIALIZABLE_KEY: {"icon_eu": gap_reason}
                    }
                )
                for target_local_date in location[3]
            }
            for location in kwargs["locations"]
        ]

    return _locations


def test_memoized_exact_run_gap_is_not_refetched_for_the_same_run(
    tmp_path, monkeypatch
) -> None:
    """QUOTA (2026-09-04): 3,132 of 4,114 single_runs units that day were the SAME
    (model, latitude, run) re-issued, because an unmaterializable scope never persists a
    row and so was never skipped. A run is immutable: prove it empty once, never ask again.
    """
    import src.data.bayes_precision_fusion_download as dl

    dl._EXACT_RUN_UNMATERIALIZABLE_MEMO.clear()
    db = _forecast_db(tmp_path)
    run = datetime(2026, 9, 3, 18, tzinfo=UTC)
    date_a, date_b = date(2026, 9, 3), date(2026, 9, 7)
    targets = _one_city_two_date_targets(date_a.isoformat(), date_b.isoformat())
    calls: list[list[tuple]] = []

    monkeypatch.setattr(dl, "_model_in_domain", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        dl,
        "_default_live_fetch_locations_batched",
        _memo_locations_stub(dl, calls, date_a, _MEMO_GAP_REASON),
    )
    monkeypatch.setattr(
        dl,
        "_default_live_fetch_batched",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("per-city single_runs fetch must not be reached")
        ),
    )

    first = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=run, targets=targets, models=("icon_eu",),
        include_previous_runs=False, prune_after=False,
        allow_single_runs_fallback=False,
        frozen_source_runs={"icon_eu": (run, run)},
    )

    assert first["written_row_count"] == 2
    assert _count(db, target_date=date_a.isoformat()) == 2
    assert [row["target_date"] for row in first["exact_run_unmaterializable"]] == [
        date_b.isoformat()
    ]
    assert calls == [[(date_a, date_b)]]

    second = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=run, targets=targets, models=("icon_eu",),
        include_previous_runs=False, prune_after=False,
        allow_single_runs_fallback=False,
        frozen_source_runs={"icon_eu": (run, run)},
    )

    # A persisted, B memoized -> nothing left to request, so NO HTTP call at all.
    assert calls == [[(date_a, date_b)]]
    assert second["written_row_count"] == 0
    assert second["single_runs_location_batch_count"] == 0
    assert second["exact_run_unmaterializable"] == (
        {
            "model": "icon_eu",
            "city": "Paris",
            "target_date": date_b.isoformat(),
            "source_cycle_time": run.isoformat(),
            "reason": _MEMO_GAP_REASON,
        },
    )
    assert second["status"] != "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE"
    assert second["status"] == "BAYES_PRECISION_FUSION_EXTRA_EXACT_RUN_UNMATERIALIZABLE"


def test_memoized_exact_run_gap_is_scoped_to_the_run_not_the_target(
    tmp_path, monkeypatch
) -> None:
    """A NEW run may well cover the target local day the old run could not — the memo must
    be keyed on the run, never on (model, city, target_date) alone."""
    import src.data.bayes_precision_fusion_download as dl

    dl._EXACT_RUN_UNMATERIALIZABLE_MEMO.clear()
    db = _forecast_db(tmp_path)
    date_a, date_b = date(2026, 9, 3), date(2026, 9, 7)
    targets = _one_city_two_date_targets(date_a.isoformat(), date_b.isoformat())
    calls: list[list[tuple]] = []

    monkeypatch.setattr(dl, "_model_in_domain", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        dl,
        "_default_live_fetch_locations_batched",
        _memo_locations_stub(dl, calls, date_a, _MEMO_GAP_REASON),
    )

    for run in (
        datetime(2026, 9, 3, 18, tzinfo=UTC),
        datetime(2026, 9, 4, 0, tzinfo=UTC),
    ):
        dl.download_bayes_precision_fusion_extra_raw_inputs(
            forecast_db=db, cycle=run, targets=targets, models=("icon_eu",),
            include_previous_runs=False, prune_after=False,
            allow_single_runs_fallback=False,
            frozen_source_runs={"icon_eu": (run, run)},
        )

    assert calls == [[(date_a, date_b)], [(date_a, date_b)]]
    assert set(dl._EXACT_RUN_UNMATERIALIZABLE_MEMO) == {
        ("icon_eu", "Paris", date_b.isoformat(), "2026-09-03T18:00:00+00:00"),
        ("icon_eu", "Paris", date_b.isoformat(), "2026-09-04T00:00:00+00:00"),
    }


def test_exact_run_memo_prunes_runs_older_than_three_days(tmp_path, monkeypatch) -> None:
    import src.data.bayes_precision_fusion_download as dl

    dl._EXACT_RUN_UNMATERIALIZABLE_MEMO.clear()
    db = _forecast_db(tmp_path)
    cycle = datetime(2026, 6, 8, 0, tzinfo=UTC)
    stale = ("icon_eu", "Paris", "2026-06-05", "2026-06-04T00:00:00+00:00")
    fresh = ("icon_eu", "Paris", "2026-06-09", "2026-06-07T00:00:00+00:00")
    dl._EXACT_RUN_UNMATERIALIZABLE_MEMO[stale] = _MEMO_GAP_REASON
    dl._EXACT_RUN_UNMATERIALIZABLE_MEMO[fresh] = _MEMO_GAP_REASON

    monkeypatch.setattr(dl, "_read_source_clock_single_runs_requests", lambda **_kwargs: {})
    monkeypatch.setattr(
        dl, "_default_live_fetch_batched", lambda **_kwargs: {"ecmwf_ifs": (22.0, 10.0)}
    )

    dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db, cycle=cycle, targets=_targets(), models=("ecmwf_ifs",),
        include_previous_runs=False, prune_after=False,
    )

    assert set(dl._EXACT_RUN_UNMATERIALIZABLE_MEMO) == {fresh}


def test_transport_shaped_gap_reason_is_never_memoized(tmp_path, monkeypatch) -> None:
    """temperature_series_missing can flip to present on the next response — memoizing it
    would silently starve a model of an obtainable row."""
    import src.data.bayes_precision_fusion_download as dl

    dl._EXACT_RUN_UNMATERIALIZABLE_MEMO.clear()
    db = _forecast_db(tmp_path)
    run = datetime(2026, 9, 3, 18, tzinfo=UTC)
    date_a, date_b = date(2026, 9, 3), date(2026, 9, 7)
    targets = _one_city_two_date_targets(date_a.isoformat(), date_b.isoformat())
    calls: list[list[tuple]] = []

    monkeypatch.setattr(dl, "_model_in_domain", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        dl,
        "_default_live_fetch_locations_batched",
        _memo_locations_stub(dl, calls, date_a, "temperature_series_missing"),
    )

    for _ in range(2):
        dl.download_bayes_precision_fusion_extra_raw_inputs(
            forecast_db=db, cycle=run, targets=targets, models=("icon_eu",),
            include_previous_runs=False, prune_after=False,
            allow_single_runs_fallback=False,
            frozen_source_runs={"icon_eu": (run, run)},
        )

    assert dl._EXACT_RUN_UNMATERIALIZABLE_MEMO == {}
    # A is persisted after call 1, so call 2 re-requests exactly the unmemoizable B.
    assert calls == [[(date_a, date_b)], [(date_b,)]]


def test_memoized_exact_run_gap_survives_process_restart(tmp_path, monkeypatch) -> None:
    """QUOTA (2026-09-05, round 2): _EXACT_RUN_UNMATERIALIZABLE_MEMO was in-process-memory
    ONLY. Two long-lived daemons (src.main / src.ingest_main) each independently rediscover
    the SAME proven-immutable gap after any restart of either process, paying the HTTP cost
    again (owner_pid alternates on the same request hash in state/openmeteo_quota.json).
    A gap that is memoized must survive clearing the in-memory dict (simulating a restart)
    by round-tripping through the durable, lock-guarded state file.
    """
    import src.data.bayes_precision_fusion_download as dl

    memo_path = tmp_path / "bayes_precision_fusion_run_gap_memo.json"
    monkeypatch.setattr(dl, "_exact_run_gap_memo_persistence_enabled", lambda: True)
    monkeypatch.setattr(dl, "_exact_run_gap_memo_path", lambda: memo_path)
    dl._EXACT_RUN_UNMATERIALIZABLE_MEMO.clear()

    scope = ("icon_eu", "Paris", "2026-09-07", "2026-09-03T18:00:00+00:00")
    dl._memoize_exact_run_gap(scope, _MEMO_GAP_REASON)

    assert memo_path.exists(), "a memoized immutable gap must be written durably"

    # Simulate a process restart: the in-process memo is gone, but a fresh process
    # (or the same process after restart) must recover it from disk.
    dl._EXACT_RUN_UNMATERIALIZABLE_MEMO.clear()
    assert dl._EXACT_RUN_UNMATERIALIZABLE_MEMO == {}
    dl._load_persisted_exact_run_memo(force=True)

    assert dl._EXACT_RUN_UNMATERIALIZABLE_MEMO == {scope: _MEMO_GAP_REASON}


def test_transport_shaped_gap_reason_is_not_persisted_to_disk(tmp_path, monkeypatch) -> None:
    """The whitelist decision (which reasons are immutable) must hold for the durable
    memo too -- a transport-shaped reason must never be written to the state file."""
    import src.data.bayes_precision_fusion_download as dl

    memo_path = tmp_path / "bayes_precision_fusion_run_gap_memo.json"
    monkeypatch.setattr(dl, "_exact_run_gap_memo_persistence_enabled", lambda: True)
    monkeypatch.setattr(dl, "_exact_run_gap_memo_path", lambda: memo_path)
    dl._EXACT_RUN_UNMATERIALIZABLE_MEMO.clear()

    scope = ("icon_eu", "Paris", "2026-09-07", "2026-09-03T18:00:00+00:00")
    dl._memoize_exact_run_gap(scope, "temperature_series_missing")

    assert dl._EXACT_RUN_UNMATERIALIZABLE_MEMO == {}
    assert not memo_path.exists(), "a retry-able gap must never be persisted durably"


def test_previous_runs_unservable_models_are_dropped_from_the_batched_leg(
    tmp_path, monkeypatch
) -> None:
    """QUOTA (2026-09-04): 1,123 single-model kma_gdps previous-runs calls in one day. The
    provider answers HTTP 200 with an all-null series, so nothing persists, so the R4a
    immutable skip never matches and the leg re-fires forever. Never request it."""
    import src.data.bayes_precision_fusion_download as dl

    db = _forecast_db(tmp_path)
    seen_models: list[list[str]] = []

    def _previous_batch(**kwargs):
        seen_models.append(list(kwargs["models"]))
        return {"ecmwf_ifs": (30.0, 20.0)}

    monkeypatch.setattr(dl, "_model_in_domain", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(dl, "_read_source_clock_single_runs_requests", lambda **_kwargs: {})
    monkeypatch.setattr(dl, "_default_previous_runs_fetch_batched", _previous_batch)
    monkeypatch.setattr(
        dl, "_default_live_fetch_batched", lambda **_kwargs: {"ecmwf_ifs": (24.0, 12.0)}
    )

    report = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=datetime(2026, 6, 8, 0, tzinfo=UTC),
        targets=_targets(),
        models=("kma_gdps", "ecmwf_ifs"),
        include_previous_runs=True,
        prune_after=False,
    )

    assert seen_models == [["ecmwf_ifs"]]
    assert "kma_gdps:previous_runs_unservable" in report["dropped"]
    assert _count(db, model="kma_gdps") == 0
    assert _count(db, model="ecmwf_ifs", endpoint="previous_runs") == 1


def test_previous_runs_leg_is_not_requested_at_all_for_an_unservable_only_basket(
    tmp_path, monkeypatch
) -> None:
    import src.data.bayes_precision_fusion_download as dl

    db = _forecast_db(tmp_path)

    monkeypatch.setattr(dl, "_model_in_domain", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(dl, "_read_source_clock_single_runs_requests", lambda **_kwargs: {})
    monkeypatch.setattr(
        dl,
        "_default_previous_runs_fetch_batched",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("previous_runs must not be requested for an unservable-only basket")
        ),
    )
    monkeypatch.setattr(dl, "_default_live_fetch_batched", lambda **_kwargs: {})

    report = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=datetime(2026, 6, 8, 0, tzinfo=UTC),
        targets=_targets(),
        models=("kma_gdps",),
        include_previous_runs=True,
        prune_after=False,
    )

    assert report["written_row_count"] == 0
    assert "kma_gdps:previous_runs_unservable" in report["dropped"]
    assert "kma_gdps:single_runs_unservable" in report["dropped"]


def test_previous_runs_unservable_models_are_dropped_from_the_legacy_leg(
    tmp_path, monkeypatch
) -> None:
    """The legacy per-model path is a separate injection surface — the same provider fact
    holds there, so the same leg must be skipped."""
    import src.data.bayes_precision_fusion_download as dl

    db = _forecast_db(tmp_path)
    seen_models: list[str] = []

    def _previous(*, model, **_kwargs):
        seen_models.append(model)
        return 19.5

    monkeypatch.setattr(dl, "_model_in_domain", lambda *_args, **_kwargs: True)

    report = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=datetime(2026, 6, 8, 0, tzinfo=UTC),
        targets=_targets(),
        models=("kma_gdps", "ecmwf_ifs"),
        include_previous_runs=True,
        prune_after=False,
        single_runs_fetch=lambda **_kwargs: 20.0,
        previous_runs_fetch=_previous,
    )

    assert seen_models == ["ecmwf_ifs"]
    assert "kma_gdps:previous_runs_unservable" in report["dropped"]
    assert _count(db, model="kma_gdps", endpoint="previous_runs") == 0


def _two_day_single_runs_payload() -> dict:
    """A forecast_hours=120 single-runs payload genuinely covering two local days."""
    return {
        "hourly": {
            "time": [
                "2026-09-06T00:00", "2026-09-06T03:00", "2026-09-06T12:00", "2026-09-06T21:00",
                "2026-09-07T00:00", "2026-09-07T03:00", "2026-09-07T12:00", "2026-09-07T21:00",
            ],
            "temperature_2m": [24.0, 23.0, 31.0, 26.0, 25.0, 24.0, 32.0, 27.0],
        },
        "hourly_units": {"temperature_2m": "°C"},
    }


def test_shared_single_runs_cache_retries_an_internal_hourly_hole(monkeypatch) -> None:
    """The day0/BPF shared raw cache cannot make a recoverable hole permanent."""
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    dl._SINGLE_RUNS_PAYLOAD_CACHE.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEX.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEXED_KEYS.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_RECORDED_AT.clear()
    target_date = date(2026, 9, 6)
    location = (1.35019, 103.994003, "UTC", (target_date,))
    run = datetime(2026, 9, 5, 18, tzinfo=UTC)
    calls: list[dict[str, object]] = []

    def _fetch(_url, params, **_kwargs):
        calls.append(dict(params))
        if len(calls) == 1:
            return _complete_hourly_local_day_payload(target_date, missing_hour=12)
        return _complete_hourly_local_day_payload(target_date)

    monkeypatch.setattr(client, "fetch", _fetch)
    first_payload, = dl._fetch_single_runs_hourly_payloads_batched(
        models=["ecmwf_ifs"], locations=[location], run=run, forecast_hours=120
    )
    first = dl._parse_batched_single_runs_payload(first_payload, ["ecmwf_ifs"], target_date, "UTC")
    second_payload, = dl._fetch_single_runs_hourly_payloads_batched(
        models=["ecmwf_ifs"], locations=[location], run=run, forecast_hours=120
    )
    second = dl._parse_batched_single_runs_payload(second_payload, ["ecmwf_ifs"], target_date, "UTC")

    assert "ecmwf_ifs" not in first
    assert len(calls) == 2
    assert second["ecmwf_ifs"] == (2.0, -1.0)


def test_shared_single_runs_cache_bypasses_a_preexisting_internal_hole(monkeypatch) -> None:
    """An old exact-cache entry is a miss when its requested target slice is incomplete."""
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    dl._SINGLE_RUNS_PAYLOAD_CACHE.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEX.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEXED_KEYS.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_RECORDED_AT.clear()
    target_date = date(2026, 9, 6)
    latitude, longitude, timezone_name = 1.3503, 103.9942, "UTC"
    location = (latitude, longitude, timezone_name, (target_date,))
    run = datetime(2026, 9, 5, 18, tzinfo=UTC)
    run_iso = run.strftime("%Y-%m-%dT%H:%M")
    om_ids = (dl.OPENMETEO_MODEL_IDS.get("ecmwf_ifs", "ecmwf_ifs"),)
    cache_key = dl._single_runs_payload_cache_key(
        om_ids=om_ids,
        run_iso=run_iso,
        latitude=latitude,
        longitude=longitude,
        timezone_name=timezone_name,
        forecast_hours=120,
        past_hours=0,
    )
    dl._store_single_runs_payload_cache(
        cache_key,
        _complete_hourly_local_day_payload(target_date, missing_hour=12),
        identity_key=dl._single_runs_payload_identity_key(
            run_iso=run_iso, latitude=latitude, longitude=longitude, timezone_name=timezone_name
        ),
        forecast_hours=120,
        past_hours=0,
    )
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        client,
        "fetch",
        lambda _url, params, **_kwargs: calls.append(dict(params))
        or _complete_hourly_local_day_payload(target_date),
    )

    payload, = dl._fetch_single_runs_hourly_payloads_batched(
        models=["ecmwf_ifs"], locations=[location], run=run, forecast_hours=120
    )

    assert len(calls) == 1
    assert dl._parse_batched_single_runs_payload(
        payload, ["ecmwf_ifs"], target_date, timezone_name
    )["ecmwf_ifs"] == (2.0, -1.0)


def test_shared_single_runs_cache_reuses_normal_trailing_horizon_null(monkeypatch) -> None:
    """A horizon suffix remains the existing coverage gate, not a retry-loop cache miss."""
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    dl._SINGLE_RUNS_PAYLOAD_CACHE.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEX.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEXED_KEYS.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_RECORDED_AT.clear()
    target_date = date(2026, 9, 6)
    location = (1.3502, 103.9941, "UTC", (target_date,))
    run = datetime(2026, 9, 5, 18, tzinfo=UTC)
    payload = _complete_hourly_local_day_payload(target_date)
    payload["hourly"]["temperature_2m"][20:] = [None, None, None, None]
    calls: list[dict[str, object]] = []

    def _fetch(_url, params, **_kwargs):
        calls.append(dict(params))
        return payload

    monkeypatch.setattr(client, "fetch", _fetch)
    first, = dl._fetch_single_runs_hourly_payloads_batched(
        models=["ecmwf_ifs"], locations=[location], run=run, forecast_hours=120
    )
    second, = dl._fetch_single_runs_hourly_payloads_batched(
        models=["ecmwf_ifs"], locations=[location], run=run, forecast_hours=120
    )

    assert len(calls) == 1
    assert first == second


def test_single_runs_payload_cache_serves_second_target_date_without_http(
    monkeypatch,
) -> None:
    """QUOTA round 3 (shape 1): a forecast_hours=120 payload for one (model, run,
    location) genuinely covers several target dates. The per-(city, target_date) BPF
    loop calls _default_live_fetch_batched once per date with a byte-identical URL --
    the second call must be served from the payload cache, not a second HTTP GET."""
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    calls: list[dict] = []

    def _fetch(_url, params, **kwargs):
        calls.append(dict(params))
        return _two_day_single_runs_payload()

    monkeypatch.setattr(client, "fetch", _fetch)

    common = dict(
        models=["ecmwf_ifs"],
        latitude=1.35019,
        longitude=103.994003,
        timezone_name="Asia/Singapore",
        run=datetime(2026, 9, 5, 18, tzinfo=UTC),
        forecast_hours=120,
    )
    first = dl._default_live_fetch_batched(target_local_date=date(2026, 9, 6), **common)
    second = dl._default_live_fetch_batched(target_local_date=date(2026, 9, 7), **common)

    assert len(calls) == 1, "second target_date must be a cache hit, not a second HTTP GET"
    assert first["ecmwf_ifs"] == (31.0, 23.0)
    assert second["ecmwf_ifs"] == (32.0, 24.0)


def test_single_runs_payload_cache_shared_between_locations_batched_and_single_caller(
    monkeypatch,
) -> None:
    """QUOTA round 3 (shape 3): _fetch_single_runs_hourly_payloads_batched is the ONE
    choke point both the BPF location-batched fast path and day0_hourly_vectors use.
    Two callers requesting the identical (model, run, location) must issue ONE HTTP
    call between them, regardless of which caller goes first."""
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    calls: list[dict] = []

    def _fetch(_url, params, **kwargs):
        calls.append(dict(params))
        return _two_day_single_runs_payload()

    monkeypatch.setattr(client, "fetch", _fetch)

    location = (1.35019, 103.994003, "Asia/Singapore", (date(2026, 9, 6),))
    run = datetime(2026, 9, 5, 18, tzinfo=UTC)

    # Caller 1: the BPF location-batched fast path (multi-location shaped call).
    first = dl._fetch_single_runs_hourly_payloads_batched(
        models=["ecmwf_ifs"], locations=[location], run=run, forecast_hours=120,
    )
    # Caller 2: day0_hourly_vectors' one-model-one-location shape, same identity.
    second = dl._fetch_single_runs_hourly_payloads_batched(
        models=["ecmwf_ifs"], locations=[location], run=run, forecast_hours=120,
    )

    assert len(calls) == 1, "the second caller must be served from the shared cache"
    assert first == second
    assert first[0]["hourly"]["temperature_2m"] == _two_day_single_runs_payload()["hourly"]["temperature_2m"]


def test_single_runs_payload_cache_persists_across_process_restart(
    monkeypatch, tmp_path,
) -> None:
    """QUOTA round 3: a second process (or the same process after a restart) reading
    the durable payload cache must issue zero HTTP calls for an already-cached
    (model, run, location, forecast_hours)."""
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    cache_path = tmp_path / "bayes_precision_fusion_single_runs_payload_cache.json"
    monkeypatch.setattr(dl, "_single_runs_payload_cache_persistence_enabled", lambda: True)
    monkeypatch.setattr(dl, "_single_runs_payload_cache_path", lambda: cache_path)
    dl._SINGLE_RUNS_PAYLOAD_CACHE.clear()

    calls: list[dict] = []

    def _fetch(_url, params, **kwargs):
        calls.append(dict(params))
        return _two_day_single_runs_payload()

    monkeypatch.setattr(client, "fetch", _fetch)

    location = (1.35019, 103.994003, "Asia/Singapore", (date(2026, 9, 6),))
    run = datetime(2026, 9, 5, 18, tzinfo=UTC)
    dl._fetch_single_runs_hourly_payloads_batched(
        models=["ecmwf_ifs"], locations=[location], run=run, forecast_hours=120,
    )
    assert len(calls) == 1
    assert cache_path.exists(), "a fetched payload must be durably cached"

    # Simulate a fresh process: the in-process cache is empty, but the durable file
    # (a second daemon, or this same daemon after a restart) must still hit.
    dl._SINGLE_RUNS_PAYLOAD_CACHE.clear()
    dl._load_persisted_single_runs_payload_cache(force=True)
    second = dl._fetch_single_runs_hourly_payloads_batched(
        models=["ecmwf_ifs"], locations=[location], run=run, forecast_hours=120,
    )

    assert len(calls) == 1, "a restarted/second process must serve from the durable cache"
    assert second[0]["hourly"]["temperature_2m"] == _two_day_single_runs_payload()["hourly"]["temperature_2m"]


# =====================================================================================
# QUOTA round 3 follow-up (2026-09-06): the exact cache key is keyed on
# (om_ids, run, lat, lon, tz, forecast_hours, past_hours), so day0's forecast_hours=72,
# past_hours=1 request and the BPF extra-raw-inputs forecast_hours=120, past_hours=0
# request for the SAME (model, run, location) never hit each other's cache entry --
# 227 + 167 of ~800 daily calls on this label were this exact pair (measured
# 2026-09-06). These two real payloads (from the live single-runs payload cache file,
# public Open-Meteo weather data) are a genuine identity pair: same model, run, and
# location -- one fetched with forecast_hours=72, the other with forecast_hours=120 --
# so the 72-h payload is byte-identical to the correct slice of the 120-h payload,
# which is the equality proof the superset rule must satisfy.
# =====================================================================================
import json as _json  # noqa: E402

_KOLKATA_120H_PAYLOAD = _json.loads(
    '{"elevation": 123.0, "generationtime_ms": 0.08666515350341797, "hourly": '
    '{"temperature_2m": [24.8, 24.6, 25.3, 26.6, 27.9, 28.2, 29.4, 29.8, 30.3, 26.6, '
    '26.1, 27.0, 26.7, 26.1, 26.0, 26.0, 26.0, 25.9, 25.8, 25.7, 25.8, 25.6, 25.4, '
    '25.1, 24.6, 24.5, 25.0, 25.8, 26.5, 28.2, 29.1, 30.1, 31.0, 31.1, 30.8, 30.3, '
    '29.1, 27.7, 27.1, 26.7, 26.5, 26.1, 25.7, 25.6, 25.2, 25.3, 25.2, 25.1, 25.1, '
    '25.4, 25.5, 26.6, 28.5, 29.5, 30.4, 30.6, 31.0, 31.0, 30.7, 26.7, 27.2, 27.0, '
    '26.8, 26.8, 26.5, 25.5, 25.3, 24.8, 24.8, 24.5, 24.4, 24.3, 24.2, 24.5, 25.4, '
    '26.7, 27.9, 29.1, 30.2, 30.4, 30.2, 28.0, 26.9, 27.0, 26.0, 25.5, 25.6, 25.7, '
    '25.6, 25.4, 25.4, 25.4, 25.4, 25.5, 25.4, 25.3, 25.4, 25.5, 25.8, 26.4, 27.4, '
    '28.6, 29.6, 30.2, 30.5, 30.5, 29.9, 28.9, 28.1, 27.7, 27.4, 27.1, 26.6, 26.2, '
    '25.8, 25.4, 25.1, 25.0, 25.0, 25.2], "time": ["2026-09-07T05:30", '
    '"2026-09-07T06:30", "2026-09-07T07:30", "2026-09-07T08:30", "2026-09-07T09:30", '
    '"2026-09-07T10:30", "2026-09-07T11:30", "2026-09-07T12:30", "2026-09-07T13:30", '
    '"2026-09-07T14:30", "2026-09-07T15:30", "2026-09-07T16:30", "2026-09-07T17:30", '
    '"2026-09-07T18:30", "2026-09-07T19:30", "2026-09-07T20:30", "2026-09-07T21:30", '
    '"2026-09-07T22:30", "2026-09-07T23:30", "2026-09-08T00:30", "2026-09-08T01:30", '
    '"2026-09-08T02:30", "2026-09-08T03:30", "2026-09-08T04:30", "2026-09-08T05:30", '
    '"2026-09-08T06:30", "2026-09-08T07:30", "2026-09-08T08:30", "2026-09-08T09:30", '
    '"2026-09-08T10:30", "2026-09-08T11:30", "2026-09-08T12:30", "2026-09-08T13:30", '
    '"2026-09-08T14:30", "2026-09-08T15:30", "2026-09-08T16:30", "2026-09-08T17:30", '
    '"2026-09-08T18:30", "2026-09-08T19:30", "2026-09-08T20:30", "2026-09-08T21:30", '
    '"2026-09-08T22:30", "2026-09-08T23:30", "2026-09-09T00:30", "2026-09-09T01:30", '
    '"2026-09-09T02:30", "2026-09-09T03:30", "2026-09-09T04:30", "2026-09-09T05:30", '
    '"2026-09-09T06:30", "2026-09-09T07:30", "2026-09-09T08:30", "2026-09-09T09:30", '
    '"2026-09-09T10:30", "2026-09-09T11:30", "2026-09-09T12:30", "2026-09-09T13:30", '
    '"2026-09-09T14:30", "2026-09-09T15:30", "2026-09-09T16:30", "2026-09-09T17:30", '
    '"2026-09-09T18:30", "2026-09-09T19:30", "2026-09-09T20:30", "2026-09-09T21:30", '
    '"2026-09-09T22:30", "2026-09-09T23:30", "2026-09-10T00:30", "2026-09-10T01:30", '
    '"2026-09-10T02:30", "2026-09-10T03:30", "2026-09-10T04:30", "2026-09-10T05:30", '
    '"2026-09-10T06:30", "2026-09-10T07:30", "2026-09-10T08:30", "2026-09-10T09:30", '
    '"2026-09-10T10:30", "2026-09-10T11:30", "2026-09-10T12:30", "2026-09-10T13:30", '
    '"2026-09-10T14:30", "2026-09-10T15:30", "2026-09-10T16:30", "2026-09-10T17:30", '
    '"2026-09-10T18:30", "2026-09-10T19:30", "2026-09-10T20:30", "2026-09-10T21:30", '
    '"2026-09-10T22:30", "2026-09-10T23:30", "2026-09-11T00:30", "2026-09-11T01:30", '
    '"2026-09-11T02:30", "2026-09-11T03:30", "2026-09-11T04:30", "2026-09-11T05:30", '
    '"2026-09-11T06:30", "2026-09-11T07:30", "2026-09-11T08:30", "2026-09-11T09:30", '
    '"2026-09-11T10:30", "2026-09-11T11:30", "2026-09-11T12:30", "2026-09-11T13:30", '
    '"2026-09-11T14:30", "2026-09-11T15:30", "2026-09-11T16:30", "2026-09-11T17:30", '
    '"2026-09-11T18:30", "2026-09-11T19:30", "2026-09-11T20:30", "2026-09-11T21:30", '
    '"2026-09-11T22:30", "2026-09-11T23:30", "2026-09-12T00:30", "2026-09-12T01:30", '
    '"2026-09-12T02:30", "2026-09-12T03:30", "2026-09-12T04:30"]}, "hourly_units": '
    '{"temperature_2m": "\\u00b0C", "time": "iso8601"}, "latitude": 26.74868, '
    '"longitude": 80.840706, "timezone": "Asia/Kolkata", "timezone_abbreviation": '
    '"GMT+5:30", "utc_offset_seconds": 19800}'
)

# The native 72-h payload for the SAME (model, run, location) -- fetched separately by
# day0's own request shape. Differs from a slice of the 120-h payload above in exactly
# one field: generationtime_ms (server-side per-request processing time, never a
# function of the request's own parameters -- present on every Open-Meteo response and
# never reused across two genuinely separate HTTP responses for the same identity).
_KOLKATA_72H_PAYLOAD = _json.loads(
    '{"elevation": 123.0, "generationtime_ms": 0.8851289749145508, "hourly": '
    '{"temperature_2m": [24.8, 24.6, 25.3, 26.6, 27.9, 28.2, 29.4, 29.8, 30.3, 26.6, '
    '26.1, 27.0, 26.7, 26.1, 26.0, 26.0, 26.0, 25.9, 25.8, 25.7, 25.8, 25.6, 25.4, '
    '25.1, 24.6, 24.5, 25.0, 25.8, 26.5, 28.2, 29.1, 30.1, 31.0, 31.1, 30.8, 30.3, '
    '29.1, 27.7, 27.1, 26.7, 26.5, 26.1, 25.7, 25.6, 25.2, 25.3, 25.2, 25.1, 25.1, '
    '25.4, 25.5, 26.6, 28.5, 29.5, 30.4, 30.6, 31.0, 31.0, 30.7, 26.7, 27.2, 27.0, '
    '26.8, 26.8, 26.5, 25.5, 25.3, 24.8, 24.8, 24.5, 24.4, 24.3], "time": '
    '["2026-09-07T05:30", "2026-09-07T06:30", "2026-09-07T07:30", "2026-09-07T08:30", '
    '"2026-09-07T09:30", "2026-09-07T10:30", "2026-09-07T11:30", "2026-09-07T12:30", '
    '"2026-09-07T13:30", "2026-09-07T14:30", "2026-09-07T15:30", "2026-09-07T16:30", '
    '"2026-09-07T17:30", "2026-09-07T18:30", "2026-09-07T19:30", "2026-09-07T20:30", '
    '"2026-09-07T21:30", "2026-09-07T22:30", "2026-09-07T23:30", "2026-09-08T00:30", '
    '"2026-09-08T01:30", "2026-09-08T02:30", "2026-09-08T03:30", "2026-09-08T04:30", '
    '"2026-09-08T05:30", "2026-09-08T06:30", "2026-09-08T07:30", "2026-09-08T08:30", '
    '"2026-09-08T09:30", "2026-09-08T10:30", "2026-09-08T11:30", "2026-09-08T12:30", '
    '"2026-09-08T13:30", "2026-09-08T14:30", "2026-09-08T15:30", "2026-09-08T16:30", '
    '"2026-09-08T17:30", "2026-09-08T18:30", "2026-09-08T19:30", "2026-09-08T20:30", '
    '"2026-09-08T21:30", "2026-09-08T22:30", "2026-09-08T23:30", "2026-09-09T00:30", '
    '"2026-09-09T01:30", "2026-09-09T02:30", "2026-09-09T03:30", "2026-09-09T04:30", '
    '"2026-09-09T05:30", "2026-09-09T06:30", "2026-09-09T07:30", "2026-09-09T08:30", '
    '"2026-09-09T09:30", "2026-09-09T10:30", "2026-09-09T11:30", "2026-09-09T12:30", '
    '"2026-09-09T13:30", "2026-09-09T14:30", "2026-09-09T15:30", "2026-09-09T16:30", '
    '"2026-09-09T17:30", "2026-09-09T18:30", "2026-09-09T19:30", "2026-09-09T20:30", '
    '"2026-09-09T21:30", "2026-09-09T22:30", "2026-09-09T23:30", "2026-09-10T00:30", '
    '"2026-09-10T01:30", "2026-09-10T02:30", "2026-09-10T03:30", "2026-09-10T04:30"]}, '
    '"hourly_units": {"temperature_2m": "\\u00b0C", "time": "iso8601"}, "latitude": '
    '26.74868, "longitude": 80.840706, "timezone": "Asia/Kolkata", '
    '"timezone_abbreviation": "GMT+5:30", "utc_offset_seconds": 19800}'
)

# Both real payloads' first hourly row is run+0h local ("2026-09-07T05:30" local,
# utc_offset_seconds=19800 => run = 2026-09-07T00:00 UTC) even though the 72-h payload
# came from a past_hours=1 request -- the requested past hour is never present in the
# stored time axis. This is the live confirmation of the past_hours evidence: the
# superset rule is forecast_hours-only.
_KOLKATA_RUN = datetime(2026, 9, 7, 0, 0, tzinfo=UTC)
_KOLKATA_LOCATION = (26.74868, 80.840706, "Asia/Kolkata", (date(2026, 9, 8),))


def test_single_runs_superset_slice_is_byte_identical_to_native_small_payload(
    monkeypatch,
) -> None:
    """(equality proof) A cached forecast_hours=120 payload must serve a
    forecast_hours=72, past_hours=1 request with a slice that is byte-identical, field
    by field, to the REAL 72-h payload fetched independently for the same identity --
    except the one field that legitimately differs between any two separate HTTP
    responses: generationtime_ms."""
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    calls: list[dict] = []

    def _fetch(_url, params, **kwargs):
        calls.append(dict(params))
        return _KOLKATA_120H_PAYLOAD

    monkeypatch.setattr(client, "fetch", _fetch)

    # Warm the cache with the 120-h superset (the BPF extra-raw-inputs shape).
    dl._fetch_single_runs_hourly_payloads_batched(
        models=["ecmwf_ifs"],
        locations=[_KOLKATA_LOCATION],
        run=_KOLKATA_RUN,
        forecast_hours=120,
    )
    assert len(calls) == 1

    # day0's shape: forecast_hours=72, past_hours=1, same model/run/location. Must be
    # served from the superset -- zero further HTTP calls.
    (sliced,) = dl._fetch_single_runs_hourly_payloads_batched(
        models=["ecmwf_ifs"],
        locations=[_KOLKATA_LOCATION],
        run=_KOLKATA_RUN,
        forecast_hours=72,
        past_hours=1,
    )

    assert len(calls) == 1, "the 72-h request must be a superset hit, not a second HTTP GET"

    expected = _KOLKATA_72H_PAYLOAD
    all_keys = set(sliced) | set(expected)
    differing_fields = {k for k in all_keys if sliced.get(k) != expected.get(k)}
    assert differing_fields == {"generationtime_ms"}, (
        f"unexpected field differences between the superset slice and the native "
        f"72-h payload: {differing_fields}"
    )
    assert sliced["hourly"]["time"] == expected["hourly"]["time"]
    assert sliced["hourly"]["temperature_2m"] == expected["hourly"]["temperature_2m"]
    assert sliced["hourly_units"] == expected["hourly_units"]
    for field in ("latitude", "longitude", "timezone", "timezone_abbreviation", "elevation", "utc_offset_seconds"):
        assert sliced[field] == expected[field]


def test_single_runs_superset_never_served_from_a_smaller_cached_window(
    monkeypatch,
) -> None:
    """(b) A cached forecast_hours=72 payload must NOT serve a forecast_hours=120
    request -- a smaller cached window is not a superset of a larger request."""
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    calls: list[dict] = []

    def _fetch(_url, params, **kwargs):
        calls.append(dict(params))
        if int(params.get("forecast_hours", 0)) == 72:
            return _KOLKATA_72H_PAYLOAD
        return _KOLKATA_120H_PAYLOAD

    monkeypatch.setattr(client, "fetch", _fetch)

    dl._fetch_single_runs_hourly_payloads_batched(
        models=["ecmwf_ifs"],
        locations=[_KOLKATA_LOCATION],
        run=_KOLKATA_RUN,
        forecast_hours=72,
        past_hours=1,
    )
    assert len(calls) == 1

    (payload_120,) = dl._fetch_single_runs_hourly_payloads_batched(
        models=["ecmwf_ifs"],
        locations=[_KOLKATA_LOCATION],
        run=_KOLKATA_RUN,
        forecast_hours=120,
    )

    assert len(calls) == 2, "a smaller cached window must not serve the larger request"
    assert len(payload_120["hourly"]["time"]) == 120


def test_single_runs_superset_hit_does_not_create_a_new_cache_entry(monkeypatch) -> None:
    """The FIX spec requires one immutable superset per identity: a superset-derived
    slice must never be stored as its own cache entry."""
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    monkeypatch.setattr(client, "fetch", lambda *_a, **_k: _KOLKATA_120H_PAYLOAD)

    dl._fetch_single_runs_hourly_payloads_batched(
        models=["ecmwf_ifs"], locations=[_KOLKATA_LOCATION], run=_KOLKATA_RUN, forecast_hours=120,
    )
    before = set(dl._SINGLE_RUNS_PAYLOAD_CACHE)

    dl._fetch_single_runs_hourly_payloads_batched(
        models=["ecmwf_ifs"], locations=[_KOLKATA_LOCATION], run=_KOLKATA_RUN,
        forecast_hours=72, past_hours=1,
    )
    after = set(dl._SINGLE_RUNS_PAYLOAD_CACHE)

    assert after == before, "a superset slice must not be persisted as a new cache entry"


def test_single_runs_payload_cache_persisted_format_unchanged_by_superset(
    monkeypatch, tmp_path,
) -> None:
    """(c) The durable JSON format (schema_version, entries{key: {...}}) keeps its
    original fields -- payload/recorded_at -- and ADDITIVELY carries the request
    identity (identity_key/forecast_hours/past_hours) so a fresh load in another
    process can index this entry without recovering it from the payload's echoed,
    grid-snapped coordinates (2026-09-07 identity fix)."""
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    cache_path = tmp_path / "single_runs_payload_cache.json"
    monkeypatch.setattr(dl, "_single_runs_payload_cache_persistence_enabled", lambda: True)
    monkeypatch.setattr(dl, "_single_runs_payload_cache_path", lambda: cache_path)

    monkeypatch.setattr(client, "fetch", lambda *_a, **_k: _KOLKATA_120H_PAYLOAD)
    dl._fetch_single_runs_hourly_payloads_batched(
        models=["ecmwf_ifs"], locations=[_KOLKATA_LOCATION], run=_KOLKATA_RUN, forecast_hours=120,
    )

    on_disk = _json.loads(cache_path.read_text(encoding="utf-8"))
    assert set(on_disk) == {"schema_version", "entries"}
    assert on_disk["schema_version"] == dl._SINGLE_RUNS_PAYLOAD_CACHE_SCHEMA_VERSION
    assert len(on_disk["entries"]) == 1
    (entry,) = on_disk["entries"].values()
    assert {"payload", "recorded_at"} <= set(entry), "original fields must survive unchanged"
    assert set(entry) == {"payload", "recorded_at", "identity_key", "forecast_hours", "past_hours"}
    assert entry["payload"] == _KOLKATA_120H_PAYLOAD
    assert entry["forecast_hours"] == 120
    assert entry["past_hours"] == 0
    lat, lon, tz, _dates = _KOLKATA_LOCATION
    run_iso = _KOLKATA_RUN.strftime("%Y-%m-%dT%H:%M")
    assert entry["identity_key"] == dl._single_runs_payload_identity_key(
        run_iso=run_iso, latitude=lat, longitude=lon, timezone_name=tz,
    )


def test_single_runs_payload_cache_superset_index_rebuilt_on_load(
    monkeypatch, tmp_path,
) -> None:
    """(d) A second process (fresh in-memory index) must rebuild the superset index
    purely from a reload of the persisted file -- using the identity_key/forecast_hours/
    past_hours fields _store_single_runs_payload_cache persisted onto the entry, not
    recovered from the payload's echoed coordinates (2026-09-07 identity fix) -- and
    serve a smaller request from the previously-persisted larger payload with zero HTTP
    calls."""
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    cache_path = tmp_path / "single_runs_payload_cache.json"
    monkeypatch.setattr(dl, "_single_runs_payload_cache_persistence_enabled", lambda: True)
    monkeypatch.setattr(dl, "_single_runs_payload_cache_path", lambda: cache_path)

    calls: list[dict] = []

    def _fetch(_url, params, **kwargs):
        calls.append(dict(params))
        return _KOLKATA_120H_PAYLOAD

    monkeypatch.setattr(client, "fetch", _fetch)
    dl._fetch_single_runs_hourly_payloads_batched(
        models=["ecmwf_ifs"], locations=[_KOLKATA_LOCATION], run=_KOLKATA_RUN, forecast_hours=120,
    )
    assert len(calls) == 1

    # Simulate a fresh process: clear every in-memory structure, keep only the file.
    dl._SINGLE_RUNS_PAYLOAD_CACHE.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEX.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEXED_KEYS.clear()
    dl._load_persisted_single_runs_payload_cache(force=True)

    assert dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEX, "the index must be rebuilt from the loaded file"

    (sliced,) = dl._fetch_single_runs_hourly_payloads_batched(
        models=["ecmwf_ifs"], locations=[_KOLKATA_LOCATION], run=_KOLKATA_RUN,
        forecast_hours=72, past_hours=1,
    )

    assert len(calls) == 1, "the rebuilt index must serve the smaller request without HTTP"
    assert sliced["hourly"]["time"] == _KOLKATA_72H_PAYLOAD["hourly"]["time"]


# =====================================================================================
# Identity fix (2026-09-07): the superset index was populated at LOAD time by recovering
# identity_key from the payload's own echoed latitude/longitude
# (_recover_single_runs_payload_identity), but Open-Meteo echoes GRID-SNAPPED
# coordinates -- never the request's own -- so every entry loaded from a persisted file
# written by a sibling process landed under a phantom identity and the cross-process
# case (an ingest daemon stores a 120-h payload, a live daemon asks for 72 h) never hit.
# Verified on the live 400-entry cache: 0 correct recoveries. These two real payloads
# (from the live single-runs payload cache file, public Open-Meteo weather data) are the
# genuine Chengdu/ecmwf_ifs identity pair the defect was found on: the request used
# latitude=30.558257, longitude=103.945966, but both payloads echo the grid-snapped
# 30.544815, 103.97647 -- proving the echoed-coordinate mismatch is real, not
# hypothetical.
# =====================================================================================
_CHENGDU_120H_PAYLOAD = _json.loads(
    '{"elevation": 492.0, "generationtime_ms": 0.07033348083496094, "hourly": {"t'
    'emperature_2m": [25.5, 25.5, 26.6, 28.0, 28.7, 30.1, 32.1, 33.2, 33.5, 33.3,'
    ' 32.3, 31.3, 30.4, 29.7, 28.9, 28.2, 28.0, 27.9, 27.7, 27.0, 26.3, 25.5, 24.'
    '8, 24.4, 24.7, 26.2, 27.6, 29.3, 30.3, 31.1, 32.1, 33.2, 33.5, 33.7, 33.0, 3'
    '1.3, 30.3, 29.7, 29.3, 28.6, 27.6, 26.2, 25.3, 25.3, 23.8, 23.4, 23.1, 23.0,'
    ' 22.9, 23.0, 22.8, 22.8, 24.0, 24.6, 25.3, 24.6, 23.7, 23.2, 21.6, 21.0, 20.'
    '6, 21.0, 20.7, 20.4, 20.1, 20.0, 20.0, 19.7, 19.5, 19.4, 19.4, 19.4, 19.5, 1'
    '9.8, 20.5, 20.7, 21.1, 21.7, 22.0, 22.4, 22.1, 21.7, 21.4, 20.8, 20.5, 20.3,'
    ' 20.2, 20.1, 19.9, 19.8, 19.5, 19.2, 18.9, 18.7, 18.5, 18.4, 18.7, 19.6, 20.'
    '8, 22.1, 23.2, 24.3, 25.0, 25.2, 25.1, 24.8, 24.1, 23.1, 22.3, 21.4, 20.7, 2'
    '0.1, 19.6, 19.4, 19.2, 19.2, 19.3, 19.5, 19.4, 19.4], "time": ["2026-09-07T0'
    '8:00", "2026-09-07T09:00", "2026-09-07T10:00", "2026-09-07T11:00", "2026-09-'
    '07T12:00", "2026-09-07T13:00", "2026-09-07T14:00", "2026-09-07T15:00", "2026'
    '-09-07T16:00", "2026-09-07T17:00", "2026-09-07T18:00", "2026-09-07T19:00", "'
    '2026-09-07T20:00", "2026-09-07T21:00", "2026-09-07T22:00", "2026-09-07T23:00'
    '", "2026-09-08T00:00", "2026-09-08T01:00", "2026-09-08T02:00", "2026-09-08T0'
    '3:00", "2026-09-08T04:00", "2026-09-08T05:00", "2026-09-08T06:00", "2026-09-'
    '08T07:00", "2026-09-08T08:00", "2026-09-08T09:00", "2026-09-08T10:00", "2026'
    '-09-08T11:00", "2026-09-08T12:00", "2026-09-08T13:00", "2026-09-08T14:00", "'
    '2026-09-08T15:00", "2026-09-08T16:00", "2026-09-08T17:00", "2026-09-08T18:00'
    '", "2026-09-08T19:00", "2026-09-08T20:00", "2026-09-08T21:00", "2026-09-08T2'
    '2:00", "2026-09-08T23:00", "2026-09-09T00:00", "2026-09-09T01:00", "2026-09-'
    '09T02:00", "2026-09-09T03:00", "2026-09-09T04:00", "2026-09-09T05:00", "2026'
    '-09-09T06:00", "2026-09-09T07:00", "2026-09-09T08:00", "2026-09-09T09:00", "'
    '2026-09-09T10:00", "2026-09-09T11:00", "2026-09-09T12:00", "2026-09-09T13:00'
    '", "2026-09-09T14:00", "2026-09-09T15:00", "2026-09-09T16:00", "2026-09-09T1'
    '7:00", "2026-09-09T18:00", "2026-09-09T19:00", "2026-09-09T20:00", "2026-09-'
    '09T21:00", "2026-09-09T22:00", "2026-09-09T23:00", "2026-09-10T00:00", "2026'
    '-09-10T01:00", "2026-09-10T02:00", "2026-09-10T03:00", "2026-09-10T04:00", "'
    '2026-09-10T05:00", "2026-09-10T06:00", "2026-09-10T07:00", "2026-09-10T08:00'
    '", "2026-09-10T09:00", "2026-09-10T10:00", "2026-09-10T11:00", "2026-09-10T1'
    '2:00", "2026-09-10T13:00", "2026-09-10T14:00", "2026-09-10T15:00", "2026-09-'
    '10T16:00", "2026-09-10T17:00", "2026-09-10T18:00", "2026-09-10T19:00", "2026'
    '-09-10T20:00", "2026-09-10T21:00", "2026-09-10T22:00", "2026-09-10T23:00", "'
    '2026-09-11T00:00", "2026-09-11T01:00", "2026-09-11T02:00", "2026-09-11T03:00'
    '", "2026-09-11T04:00", "2026-09-11T05:00", "2026-09-11T06:00", "2026-09-11T0'
    '7:00", "2026-09-11T08:00", "2026-09-11T09:00", "2026-09-11T10:00", "2026-09-'
    '11T11:00", "2026-09-11T12:00", "2026-09-11T13:00", "2026-09-11T14:00", "2026'
    '-09-11T15:00", "2026-09-11T16:00", "2026-09-11T17:00", "2026-09-11T18:00", "'
    '2026-09-11T19:00", "2026-09-11T20:00", "2026-09-11T21:00", "2026-09-11T22:00'
    '", "2026-09-11T23:00", "2026-09-12T00:00", "2026-09-12T01:00", "2026-09-12T0'
    '2:00", "2026-09-12T03:00", "2026-09-12T04:00", "2026-09-12T05:00", "2026-09-'
    '12T06:00", "2026-09-12T07:00"]}, "hourly_units": {"temperature_2m": "\\u00b0C'
    '", "time": "iso8601"}, "latitude": 30.544815, "longitude": 103.97647, "timez'
    'one": "Asia/Shanghai", "timezone_abbreviation": "GMT+8", "utc_offset_seconds'
    '": 28800}'
)

# The native 72-h payload for the SAME (model, run, location) -- a genuinely separate
# HTTP fetch (day0's own request shape, with the REQUEST's own past_hours=1, which --
# per the past_hours evidence above -- never appears in the returned time axis). Differs
# from the first 72 hours of the 120-h payload above in exactly one field:
# generationtime_ms.
_CHENGDU_72H_PAYLOAD = _json.loads(
    '{"elevation": 492.0, "generationtime_ms": 1.2230873107910156, "hourly": {"te'
    'mperature_2m": [25.5, 25.5, 26.6, 28.0, 28.7, 30.1, 32.1, 33.2, 33.5, 33.3, '
    '32.3, 31.3, 30.4, 29.7, 28.9, 28.2, 28.0, 27.9, 27.7, 27.0, 26.3, 25.5, 24.8'
    ', 24.4, 24.7, 26.2, 27.6, 29.3, 30.3, 31.1, 32.1, 33.2, 33.5, 33.7, 33.0, 31'
    '.3, 30.3, 29.7, 29.3, 28.6, 27.6, 26.2, 25.3, 25.3, 23.8, 23.4, 23.1, 23.0, '
    '22.9, 23.0, 22.8, 22.8, 24.0, 24.6, 25.3, 24.6, 23.7, 23.2, 21.6, 21.0, 20.6'
    ', 21.0, 20.7, 20.4, 20.1, 20.0, 20.0, 19.7, 19.5, 19.4, 19.4, 19.4], "time":'
    ' ["2026-09-07T08:00", "2026-09-07T09:00", "2026-09-07T10:00", "2026-09-07T11'
    ':00", "2026-09-07T12:00", "2026-09-07T13:00", "2026-09-07T14:00", "2026-09-0'
    '7T15:00", "2026-09-07T16:00", "2026-09-07T17:00", "2026-09-07T18:00", "2026-'
    '09-07T19:00", "2026-09-07T20:00", "2026-09-07T21:00", "2026-09-07T22:00", "2'
    '026-09-07T23:00", "2026-09-08T00:00", "2026-09-08T01:00", "2026-09-08T02:00"'
    ', "2026-09-08T03:00", "2026-09-08T04:00", "2026-09-08T05:00", "2026-09-08T06'
    ':00", "2026-09-08T07:00", "2026-09-08T08:00", "2026-09-08T09:00", "2026-09-0'
    '8T10:00", "2026-09-08T11:00", "2026-09-08T12:00", "2026-09-08T13:00", "2026-'
    '09-08T14:00", "2026-09-08T15:00", "2026-09-08T16:00", "2026-09-08T17:00", "2'
    '026-09-08T18:00", "2026-09-08T19:00", "2026-09-08T20:00", "2026-09-08T21:00"'
    ', "2026-09-08T22:00", "2026-09-08T23:00", "2026-09-09T00:00", "2026-09-09T01'
    ':00", "2026-09-09T02:00", "2026-09-09T03:00", "2026-09-09T04:00", "2026-09-0'
    '9T05:00", "2026-09-09T06:00", "2026-09-09T07:00", "2026-09-09T08:00", "2026-'
    '09-09T09:00", "2026-09-09T10:00", "2026-09-09T11:00", "2026-09-09T12:00", "2'
    '026-09-09T13:00", "2026-09-09T14:00", "2026-09-09T15:00", "2026-09-09T16:00"'
    ', "2026-09-09T17:00", "2026-09-09T18:00", "2026-09-09T19:00", "2026-09-09T20'
    ':00", "2026-09-09T21:00", "2026-09-09T22:00", "2026-09-09T23:00", "2026-09-1'
    '0T00:00", "2026-09-10T01:00", "2026-09-10T02:00", "2026-09-10T03:00", "2026-'
    '09-10T04:00", "2026-09-10T05:00", "2026-09-10T06:00", "2026-09-10T07:00"]}, '
    '"hourly_units": {"temperature_2m": "\\u00b0C", "time": "iso8601"}, "latitude"'
    ': 30.544815, "longitude": 103.97647, "timezone": "Asia/Shanghai", "timezone_'
    'abbreviation": "GMT+8", "utc_offset_seconds": 28800}'
)

# The REQUEST's own coordinates -- distinct from the echoed, grid-snapped coordinates
# baked into both payloads above. A defect that recovers identity from echoed
# coordinates can never reconstruct this request identity; the fix persists it directly.
_CHENGDU_REQUEST_LATITUDE = 30.558257
_CHENGDU_REQUEST_LONGITUDE = 103.945966
_CHENGDU_RUN_ISO = "2026-09-07T00:00"
_CHENGDU_RUN = datetime(2026, 9, 7, 0, 0, tzinfo=UTC)
_CHENGDU_TIMEZONE = "Asia/Shanghai"
_CHENGDU_LOCATION = (
    _CHENGDU_REQUEST_LATITUDE,
    _CHENGDU_REQUEST_LONGITUDE,
    _CHENGDU_TIMEZONE,
    (date(2026, 9, 8),),
)


def test_single_runs_payload_cache_serves_cross_process_superset_hit_from_persisted_identity(
    monkeypatch, tmp_path,
) -> None:
    """(a) The defect this fix closes: a payload persisted by one process (a 120-h
    donor) must serve a smaller (72,1) request in a DIFFERENT process with ZERO
    uncached fetches, using the identity _store_single_runs_payload_cache persisted
    onto the entry -- never the payload's echoed, grid-snapped coordinates, which never
    equal the request's own (see _CHENGDU_120H_PAYLOAD's echoed 30.544815,103.97647 vs
    the request's real 30.558257,103.945966)."""
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    cache_path = tmp_path / "single_runs_payload_cache.json"
    monkeypatch.setattr(dl, "_single_runs_payload_cache_persistence_enabled", lambda: True)
    monkeypatch.setattr(dl, "_single_runs_payload_cache_path", lambda: cache_path)

    calls: list[dict] = []

    def _fetch(_url, params, **kwargs):
        calls.append(dict(params))
        return _CHENGDU_120H_PAYLOAD

    monkeypatch.setattr(client, "fetch", _fetch)

    # Process 1 (e.g. the ingest daemon): fetches and durably persists the 120-h donor.
    dl._fetch_single_runs_hourly_payloads_batched(
        models=["ecmwf_ifs"], locations=[_CHENGDU_LOCATION], run=_CHENGDU_RUN, forecast_hours=120,
    )
    assert len(calls) == 1

    # Process 2 (e.g. the live daemon): fresh in-memory state, only the persisted file
    # survives -- exactly the cross-process case the defect broke.
    dl._SINGLE_RUNS_PAYLOAD_CACHE.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEX.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEXED_KEYS.clear()
    dl._load_persisted_single_runs_payload_cache(force=True)

    expected_identity = dl._single_runs_payload_identity_key(
        run_iso=_CHENGDU_RUN_ISO,
        latitude=_CHENGDU_REQUEST_LATITUDE,
        longitude=_CHENGDU_REQUEST_LONGITUDE,
        timezone_name=_CHENGDU_TIMEZONE,
    )
    assert expected_identity in dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEX, (
        "the request identity (not the payload's echoed, grid-snapped coordinates) "
        "must be recoverable from the persisted file after a fresh load"
    )

    (sliced,) = dl._fetch_single_runs_hourly_payloads_batched(
        models=["ecmwf_ifs"], locations=[_CHENGDU_LOCATION], run=_CHENGDU_RUN,
        forecast_hours=72, past_hours=1,
    )

    assert len(calls) == 1, (
        "the cross-process (72,1) request must be served from the persisted (120,0) "
        "donor with ZERO uncached fetches"
    )
    assert sliced["hourly"]["time"] == _CHENGDU_72H_PAYLOAD["hourly"]["time"]
    assert sliced["hourly"]["temperature_2m"] == _CHENGDU_72H_PAYLOAD["hourly"]["temperature_2m"]


def test_single_runs_payload_cache_legacy_entry_loaded_but_not_indexed(
    monkeypatch, tmp_path,
) -> None:
    """(b) An entry persisted before this fix (no identity_key/forecast_hours/
    past_hours fields) is loaded into the payload cache -- still servable by an exact
    cache-key hit -- but must NOT be indexed for superset lookups: its only
    identity-recovery path relies on the payload's echoed, grid-snapped coordinates,
    which never equal a live request's own (an unindexed entry only costs a missed
    superset hit, never a wrong one)."""
    import json as json_module

    from datetime import UTC, datetime

    import src.data.bayes_precision_fusion_download as dl

    cache_path = tmp_path / "single_runs_payload_cache.json"
    monkeypatch.setattr(dl, "_single_runs_payload_cache_persistence_enabled", lambda: True)
    monkeypatch.setattr(dl, "_single_runs_payload_cache_path", lambda: cache_path)

    legacy_key = "legacy0123456789"
    cache_path.write_text(
        json_module.dumps({
            "schema_version": dl._SINGLE_RUNS_PAYLOAD_CACHE_SCHEMA_VERSION,
            "entries": {
                legacy_key: {
                    "payload": _CHENGDU_120H_PAYLOAD,
                    "recorded_at": datetime.now(UTC).isoformat(),  # within the 24 h bound: the subject here is indexing, not age
                    # deliberately no identity_key/forecast_hours/past_hours -- this is
                    # the pre-fix, on-disk shape.
                },
            },
        }),
        encoding="utf-8",
    )

    dl._SINGLE_RUNS_PAYLOAD_CACHE.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEX.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEXED_KEYS.clear()
    dl._load_persisted_single_runs_payload_cache(force=True)

    assert legacy_key in dl._SINGLE_RUNS_PAYLOAD_CACHE, (
        "a legacy entry must still be loaded into the payload cache"
    )
    assert dl._SINGLE_RUNS_PAYLOAD_CACHE[legacy_key] == _CHENGDU_120H_PAYLOAD
    assert not dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEX, (
        "a legacy entry without a persisted identity must not be indexed for "
        "superset lookups"
    )
    assert legacy_key not in dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEXED_KEYS


# =====================================================================================
# QUOTA round 5 (2026-09-06): previous_runs beyond a short-range model's horizon is
# HTTP 200 + all-null, so nothing persists and the immutable skip re-fires forever.
# =====================================================================================
def _lead2_targets(metrics=("high", "low")):
    from src.data.bayes_precision_fusion_download import BayesPrecisionFusionDownloadTarget
    return [
        BayesPrecisionFusionDownloadTarget(
            city="Munich", metric=met, target_date="2026-09-08", lead_days=2,
            latitude=48.354, longitude=11.788, timezone_name="Europe/Berlin",
        )
        for met in metrics
    ]


def test_previous_runs_beyond_model_horizon_is_not_requested_batched(
    tmp_path, monkeypatch
) -> None:
    import src.data.bayes_precision_fusion_download as dl

    db = _forecast_db(tmp_path)
    seen_models: list[list[str]] = []

    def _previous_batch(**kwargs):
        seen_models.append(list(kwargs["models"]))
        return {"ecmwf_ifs": (30.0, 20.0)}

    monkeypatch.setattr(dl, "_model_in_domain", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(dl, "_read_source_clock_single_runs_requests", lambda **_kwargs: {})
    monkeypatch.setattr(dl, "_default_previous_runs_fetch_batched", _previous_batch)
    monkeypatch.setattr(dl, "_default_live_fetch_batched", lambda **_kwargs: {})

    report = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=datetime(2026, 9, 6, 0, tzinfo=UTC),
        targets=_lead2_targets(),
        models=("icon_d2", "ecmwf_ifs"),
        include_previous_runs=True,
        prune_after=False,
    )

    assert seen_models == [["ecmwf_ifs"]]
    assert "icon_d2:previous_runs_beyond_horizon" in report["dropped"]
    assert _count(db, model="icon_d2", endpoint="previous_runs") == 0
    assert _count(db, model="ecmwf_ifs", endpoint="previous_runs") == 2


def test_previous_runs_leg_is_not_requested_for_a_beyond_horizon_only_basket(
    tmp_path, monkeypatch
) -> None:
    import src.data.bayes_precision_fusion_download as dl

    db = _forecast_db(tmp_path)
    monkeypatch.setattr(dl, "_model_in_domain", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(dl, "_read_source_clock_single_runs_requests", lambda **_kwargs: {})
    monkeypatch.setattr(
        dl,
        "_default_previous_runs_fetch_batched",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("previous_runs must not be requested beyond the model horizon")
        ),
    )
    monkeypatch.setattr(dl, "_default_live_fetch_batched", lambda **_kwargs: {})

    report = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=datetime(2026, 9, 6, 0, tzinfo=UTC),
        targets=_lead2_targets(),
        models=("icon_d2", "gfs_hrrr"),
        include_previous_runs=True,
        prune_after=False,
    )

    assert report["written_row_count"] == 0
    assert "icon_d2:previous_runs_beyond_horizon" in report["dropped"]
    assert "gfs_hrrr:previous_runs_beyond_horizon" in report["dropped"]


def test_previous_runs_need_is_measured_on_the_metrics_the_pass_carries(
    tmp_path, monkeypatch
) -> None:
    """A city with only a HIGH market never carries a LOW target, so a fixed (high, low)
    need-check could never be satisfied and re-issued the same request on every pass."""
    import src.data.bayes_precision_fusion_download as dl

    db = _forecast_db(tmp_path)
    calls: list[list[str]] = []

    def _previous_batch(**kwargs):
        calls.append(list(kwargs["models"]))
        return {"ecmwf_ifs": (30.0, 20.0)}

    monkeypatch.setattr(dl, "_model_in_domain", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(dl, "_read_source_clock_single_runs_requests", lambda **_kwargs: {})
    monkeypatch.setattr(dl, "_default_previous_runs_fetch_batched", _previous_batch)
    monkeypatch.setattr(dl, "_default_live_fetch_batched", lambda **_kwargs: {})

    kwargs = dict(
        forecast_db=db,
        cycle=datetime(2026, 9, 6, 0, tzinfo=UTC),
        targets=_lead2_targets(metrics=("high",)),
        models=("ecmwf_ifs",),
        include_previous_runs=True,
        prune_after=False,
    )
    dl.download_bayes_precision_fusion_extra_raw_inputs(**kwargs)
    assert calls == [["ecmwf_ifs"]]
    assert _count(db, model="ecmwf_ifs", endpoint="previous_runs", metric="high") == 1
    assert _count(db, model="ecmwf_ifs", endpoint="previous_runs", metric="low") == 0

    dl.download_bayes_precision_fusion_extra_raw_inputs(**kwargs)
    assert calls == [["ecmwf_ifs"]], "second pass re-issued a satisfied previous_runs request"


def test_previous_runs_beyond_model_horizon_is_not_requested_legacy(
    tmp_path, monkeypatch
) -> None:
    import src.data.bayes_precision_fusion_download as dl

    db = _forecast_db(tmp_path)
    seen_models: list[str] = []

    def _previous(*, model, **_kwargs):
        seen_models.append(model)
        return 19.5

    monkeypatch.setattr(dl, "_model_in_domain", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(dl, "_read_source_clock_single_runs_requests", lambda **_kwargs: {})

    report = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=datetime(2026, 9, 6, 0, tzinfo=UTC),
        targets=_lead2_targets(metrics=("high",)),
        models=("icon_d2", "ecmwf_ifs"),
        include_previous_runs=True,
        prune_after=False,
        single_runs_fetch=lambda **_kwargs: 20.0,
        previous_runs_fetch=_previous,
    )

    assert seen_models == ["ecmwf_ifs"]
    assert "icon_d2:previous_runs_beyond_horizon" in report["dropped"]
    assert _count(db, model="icon_d2", endpoint="previous_runs") == 0


def _fresh_single_runs_cache_process(dl) -> None:
    """Drop every in-memory cache structure so only the persisted file survives."""
    dl._SINGLE_RUNS_PAYLOAD_CACHE.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEX.clear()
    dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEXED_KEYS.clear()
    dl._load_persisted_single_runs_payload_cache(force=True)


def test_single_location_120h_payload_donates_to_cross_process_72h_request(
    monkeypatch, tmp_path,
) -> None:
    """The BPF per-(city, target_date) path (_default_live_fetch_batched) stores the
    120-h payloads; until it persisted the request identity none of them could serve a
    day0 (72,1) request in another process (2026-09-07: 4 of the last 6 day0 fetches had
    a series-identical 120-h payload already on disk from this path)."""
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    cache_path = tmp_path / "single_runs_payload_cache.json"
    monkeypatch.setattr(dl, "_single_runs_payload_cache_persistence_enabled", lambda: True)
    monkeypatch.setattr(dl, "_single_runs_payload_cache_path", lambda: cache_path)
    _fresh_single_runs_cache_process(dl)

    calls: list[dict] = []

    def _fetch(_url, params, **kwargs):
        calls.append(dict(params))
        return _CHENGDU_120H_PAYLOAD

    monkeypatch.setattr(client, "fetch", _fetch)

    dl._default_live_fetch_batched(
        models=["ecmwf_ifs"],
        latitude=_CHENGDU_REQUEST_LATITUDE,
        longitude=_CHENGDU_REQUEST_LONGITUDE,
        timezone_name=_CHENGDU_TIMEZONE,
        run=_CHENGDU_RUN,
        target_local_date=date(2026, 9, 7),
        forecast_hours=120,
    )
    assert len(calls) == 1

    _fresh_single_runs_cache_process(dl)
    (sliced,) = dl._fetch_single_runs_hourly_payloads_batched(
        models=["ecmwf_ifs"], locations=[_CHENGDU_LOCATION], run=_CHENGDU_RUN,
        forecast_hours=72, past_hours=1,
    )
    assert len(calls) == 1, "the (72,1) request must be served from the single-location 120-h donor"
    assert sliced["hourly"]["time"] == _CHENGDU_72H_PAYLOAD["hourly"]["time"]
    assert sliced["hourly"]["temperature_2m"] == _CHENGDU_72H_PAYLOAD["hourly"]["temperature_2m"]


def test_single_location_72h_request_is_served_from_a_cross_process_120h_donor(
    monkeypatch, tmp_path,
) -> None:
    """The reverse direction: a 120-h donor persisted by the locations-batched path
    serves a smaller single-location request, and the parsed local-day values equal a
    native 72-h fetch's."""
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    cache_path = tmp_path / "single_runs_payload_cache.json"
    monkeypatch.setattr(dl, "_single_runs_payload_cache_persistence_enabled", lambda: True)
    monkeypatch.setattr(dl, "_single_runs_payload_cache_path", lambda: cache_path)
    _fresh_single_runs_cache_process(dl)

    calls: list[dict] = []

    def _fetch(_url, params, **kwargs):
        calls.append(dict(params))
        return _CHENGDU_120H_PAYLOAD

    monkeypatch.setattr(client, "fetch", _fetch)

    dl._fetch_single_runs_hourly_payloads_batched(
        models=["ecmwf_ifs"], locations=[_CHENGDU_LOCATION], run=_CHENGDU_RUN, forecast_hours=120,
    )
    assert len(calls) == 1

    _fresh_single_runs_cache_process(dl)
    served = dl._default_live_fetch_batched(
        models=["ecmwf_ifs"],
        latitude=_CHENGDU_REQUEST_LATITUDE,
        longitude=_CHENGDU_REQUEST_LONGITUDE,
        timezone_name=_CHENGDU_TIMEZONE,
        run=_CHENGDU_RUN,
        target_local_date=date(2026, 9, 7),
        forecast_hours=72,
    )
    assert len(calls) == 1, "the single-location 72-h request must be served from the 120-h donor"
    native = dl._parse_batched_single_runs_payload(
        _CHENGDU_72H_PAYLOAD, ["ecmwf_ifs"], date(2026, 9, 7), _CHENGDU_TIMEZONE,
    )
    assert served == native


def test_single_runs_payload_cache_in_process_honours_entry_bound(monkeypatch) -> None:
    """T_ingestmem (2026-09-14): the durable mirror was bounded to 400 entries but the
    in-process dict, its superset index and the indexed-key set only ever grew for the
    daemon's lifetime. The same overflow rule (oldest-first) now applies in-process."""
    import src.data.bayes_precision_fusion_download as dl

    monkeypatch.setattr(dl, "_single_runs_payload_cache_persistence_enabled", lambda: False)
    monkeypatch.setattr(dl, "_SINGLE_RUNS_PAYLOAD_CACHE_MAX_ENTRIES", 3)
    for i in range(5):
        dl._store_single_runs_payload_cache(
            f"k{i}", {"hourly": {"time": [i]}}, identity_key=f"id{i}", forecast_hours=24, past_hours=0
        )
    assert set(dl._SINGLE_RUNS_PAYLOAD_CACHE) == {"k2", "k3", "k4"}
    assert dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEXED_KEYS == {"k2", "k3", "k4"}
    assert set(dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEX) == {"id2", "id3", "id4"}
    assert set(dl._SINGLE_RUNS_PAYLOAD_CACHE_RECORDED_AT) == {"k2", "k3", "k4"}


def test_single_runs_payload_cache_in_process_honours_age_bound(monkeypatch) -> None:
    """An entry older than the durable max age is evicted from every in-process
    structure on the next store, exactly as the file would drop it."""
    from datetime import UTC, datetime, timedelta

    import src.data.bayes_precision_fusion_download as dl

    monkeypatch.setattr(dl, "_single_runs_payload_cache_persistence_enabled", lambda: False)
    dl._store_single_runs_payload_cache(
        "k_old", {"hourly": {"time": [0]}}, identity_key="id_old", forecast_hours=24, past_hours=0
    )
    dl._SINGLE_RUNS_PAYLOAD_CACHE_RECORDED_AT["k_old"] = datetime.now(UTC) - timedelta(
        hours=dl._SINGLE_RUNS_PAYLOAD_CACHE_MAX_AGE_HOURS + 1
    )
    dl._store_single_runs_payload_cache(
        "k_new", {"hourly": {"time": [1]}}, identity_key="id_new", forecast_hours=24, past_hours=0
    )
    assert set(dl._SINGLE_RUNS_PAYLOAD_CACHE) == {"k_new"}
    assert dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEXED_KEYS == {"k_new"}
    assert set(dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEX) == {"id_new"}


def test_single_runs_payload_cache_load_drops_expired_durable_entries(tmp_path, monkeypatch) -> None:
    """A durable file written yesterday can carry entries past the max age when it is
    loaded today; they must not be resurrected in-process."""
    import json
    from datetime import UTC, datetime, timedelta

    import src.data.bayes_precision_fusion_download as dl

    cache_path = tmp_path / "single_runs_payload_cache.json"
    fresh_at = datetime.now(UTC).isoformat()
    stale_at = (datetime.now(UTC) - timedelta(hours=dl._SINGLE_RUNS_PAYLOAD_CACHE_MAX_AGE_HOURS + 1)).isoformat()
    cache_path.write_text(
        json.dumps(
            {
                "schema_version": dl._SINGLE_RUNS_PAYLOAD_CACHE_SCHEMA_VERSION,
                "entries": {
                    "k_fresh": {"payload": {"hourly": {"time": [1]}}, "recorded_at": fresh_at,
                                "identity_key": "id_fresh", "forecast_hours": 24, "past_hours": 0},
                    "k_stale": {"payload": {"hourly": {"time": [0]}}, "recorded_at": stale_at,
                                "identity_key": "id_stale", "forecast_hours": 24, "past_hours": 0},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dl, "_single_runs_payload_cache_persistence_enabled", lambda: True)
    monkeypatch.setattr(dl, "_single_runs_payload_cache_path", lambda: cache_path)
    dl._load_persisted_single_runs_payload_cache(force=True)
    assert set(dl._SINGLE_RUNS_PAYLOAD_CACHE) == {"k_fresh"}
    assert dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEXED_KEYS == {"k_fresh"}
    assert set(dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEX) == {"id_fresh"}
    assert set(dl._SINGLE_RUNS_PAYLOAD_CACHE_RECORDED_AT) == {"k_fresh"}


def test_single_runs_payload_cache_load_skips_entries_without_a_stamp(tmp_path, monkeypatch) -> None:
    """The durable rule treats a missing or unparsable recorded_at as expired; the load
    path must apply the same rule, or the entry enters the process with no stamp and can
    never age out (R-CE, 2026-09-14)."""
    import json

    import src.data.bayes_precision_fusion_download as dl

    cache_path = tmp_path / "single_runs_payload_cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "schema_version": dl._SINGLE_RUNS_PAYLOAD_CACHE_SCHEMA_VERSION,
                "entries": {
                    "k_unstamped": {"payload": {"hourly": {"time": [1]}},
                                    "identity_key": "id_u", "forecast_hours": 24, "past_hours": 0},
                    "k_badstamp": {"payload": {"hourly": {"time": [2]}}, "recorded_at": "not-a-date",
                                   "identity_key": "id_b", "forecast_hours": 24, "past_hours": 0},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dl, "_single_runs_payload_cache_persistence_enabled", lambda: True)
    monkeypatch.setattr(dl, "_single_runs_payload_cache_path", lambda: cache_path)
    dl._load_persisted_single_runs_payload_cache(force=True)
    assert dl._SINGLE_RUNS_PAYLOAD_CACHE == {}
    assert dl._SINGLE_RUNS_PAYLOAD_CACHE_RECORDED_AT == {}
    assert dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEXED_KEYS == set()
    assert dl._SINGLE_RUNS_PAYLOAD_CACHE_INDEX == {}


def test_candidate_recovery_lane_never_enters_source_clock_reserve(tmp_path, monkeypatch) -> None:
    """Candidate accrual is background work: its single-runs request may use only
    recovery quota, leaving both source-clock and held-position reserves intact."""
    from contextlib import contextmanager

    import src.data.bayes_precision_fusion_download as dl

    entered: list[str] = []

    @contextmanager
    def _recovery_lane():
        entered.append("recovery")
        yield

    @contextmanager
    def _source_clock_lane():
        raise AssertionError("candidate accrual must not enter source-clock quota")
        yield

    cycle = datetime(2026, 9, 20, 12, tzinfo=UTC)
    monkeypatch.setattr(dl, "bayes_precision_fusion_recovery_quota_priority", _recovery_lane)
    monkeypatch.setattr(dl, "bayes_precision_fusion_source_clock_quota_priority", _source_clock_lane)
    monkeypatch.setattr(dl, "_model_in_domain", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(dl, "_read_source_clock_single_runs_requests", lambda **_kwargs: {})
    monkeypatch.setattr(
        dl,
        "_default_live_fetch_batched",
        lambda **_kwargs: {"met_nordic": (16.6, 11.0)},
    )

    report = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=_forecast_db(tmp_path),
        cycle=cycle,
        targets=_targets(),
        models=("met_nordic",),
        include_previous_runs=False,
        prune_after=False,
        frozen_source_runs={"met_nordic": (cycle, cycle)},
        quota_lane="recovery",
    )

    assert report["written_row_count"] > 0
    assert entered == ["recovery"]


def test_candidate_accrual_never_fetches_outside_its_declared_domain(tmp_path, monkeypatch) -> None:
    """The candidate-only caller relies on the downloader's existing domain gate;
    an out-of-domain Met Nordic target must be recorded without any HTTP attempt."""
    import src.data.bayes_precision_fusion_download as dl

    cycle = datetime(2026, 9, 20, 12, tzinfo=UTC)
    target = dl.BayesPrecisionFusionDownloadTarget(
        city="Fiji",
        metric="high",
        target_date="2026-09-21",
        lead_days=1,
        latitude=-18.1248,
        longitude=178.4501,
        timezone_name="Pacific/Fiji",
    )
    monkeypatch.setattr(dl, "_read_source_clock_single_runs_requests", lambda **_kwargs: {})
    monkeypatch.setattr(
        dl,
        "_default_live_fetch_batched",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("out-of-domain candidate must not issue HTTP")
        ),
    )

    report = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=_forecast_db(tmp_path),
        cycle=cycle,
        targets=(target,),
        models=("met_nordic",),
        include_previous_runs=False,
        prune_after=False,
        frozen_source_runs={"met_nordic": (cycle, cycle)},
        quota_lane="recovery",
    )

    assert report["written_row_count"] == 0
    assert "met_nordic:Fiji" in report["domain_excluded"]


def test_candidate_accrual_uses_its_own_newer_public_metadata_run(tmp_path, monkeypatch) -> None:
    """A candidate's exact current capture is keyed to its own published run, not
    the older cycle that selected the current target plan."""
    import src.data.bayes_precision_fusion_download as dl

    anchor_cycle = datetime(2026, 9, 20, 6, tzinfo=UTC)
    met_cycle = datetime(2026, 9, 20, 12, tzinfo=UTC)
    target = dl.BayesPrecisionFusionDownloadTarget(
        city="Helsinki",
        metric="high",
        target_date="2026-09-21",
        lead_days=1,
        latitude=60.1699,
        longitude=24.9384,
        timezone_name="Europe/Helsinki",
    )
    monkeypatch.setattr(
        dl,
        "_read_source_clock_single_runs_requests",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("candidate capture must use frozen metadata, not probe cache")
        ),
    )
    seen_runs: list[datetime] = []

    def _fetch(**kwargs):
        seen_runs.append(kwargs["run"])
        return {"met_nordic": (16.6, 11.0)}

    monkeypatch.setattr(dl, "_default_live_fetch_batched", _fetch)
    monkeypatch.setattr(
        dl,
        "_default_previous_runs_fetch_batched",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("candidate current-only capture must not request previous_runs")
        ),
    )
    forecast_db = _forecast_db(tmp_path)
    report = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=forecast_db,
        cycle=anchor_cycle,
        targets=(target,),
        models=("met_nordic",),
        include_previous_runs=False,
        prune_after=False,
        frozen_source_runs={"met_nordic": (met_cycle, met_cycle)},
        quota_lane="recovery",
    )

    assert report["written_row_count"] > 0
    assert seen_runs == [met_cycle]
    with sqlite3.connect(str(forecast_db)) as conn:
        endpoints = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT endpoint FROM raw_model_forecasts"
            )
        }
    assert endpoints == {"single_runs"}


@pytest.mark.parametrize(
    "failure_kind",
    ("partial", "transport"),
)
def test_derived_offgrid_run_needs_complete_pair_before_possession_write(
    tmp_path,
    monkeypatch,
    failure_kind,
) -> None:
    """A derived run writes both target metrics only after complete parsed possession."""
    import src.data.bayes_precision_fusion_download as dl

    run = datetime(2026, 9, 20, 12, tzinfo=UTC)
    metadata_available = datetime(2026, 9, 20, 14, 32, 44, tzinfo=UTC)
    targets = [
        dl.BayesPrecisionFusionDownloadTarget(
            city="Helsinki", metric=metric, target_date="2026-09-21", lead_days=1,
            latitude=60.1699, longitude=24.9384, timezone_name="Europe/Helsinki",
        )
        for metric in ("high", "low")
    ]
    db = _forecast_db(tmp_path)
    seen: list[dict[str, object]] = []

    def _failed_fetch(**kwargs):
        seen.append(kwargs)
        if failure_kind == "partial":
            return {"met_nordic": (16.6, None)}
        return {dl._BATCH_TRANSPORT_ERROR_KEY: ("HTTP 400", {})}

    monkeypatch.setattr(dl, "_default_live_fetch_batched", _failed_fetch)
    first = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=metadata_available,
        targets=targets,
        models=("met_nordic",),
        include_previous_runs=False,
        prune_after=False,
        frozen_source_runs={"met_nordic": dl._DerivedOffGridSingleRunsRun(run=run)},
    )
    assert first["written_row_count"] == 0
    assert _count(db, endpoint="single_runs") == 0
    assert seen[0]["run"] == run
    assert seen[0]["source_available_at"] is None
    assert seen[0]["allow_standard_meta_fallback"] is False

    fetch_started_at = datetime.now(UTC)
    completed_at: list[datetime] = []
    successful_requests: list[dict[str, object]] = []

    def _success_fetch(**kwargs):
        successful_requests.append(kwargs)
        completed_at.append(datetime.now(UTC))
        return {"met_nordic": (16.6, 11.0)}

    def _unexpected_previous_runs(**_kwargs):
        raise AssertionError("derived current-run recovery must not request previous_runs")

    monkeypatch.setattr(dl, "_default_live_fetch_batched", _success_fetch)
    monkeypatch.setattr(
        dl,
        "_default_previous_runs_fetch_batched",
        _unexpected_previous_runs,
    )
    second = dl.download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=metadata_available,
        targets=targets,
        models=("met_nordic",),
        include_previous_runs=False,
        prune_after=False,
        frozen_source_runs={"met_nordic": dl._DerivedOffGridSingleRunsRun(run=run)},
    )
    assert second["written_row_count"] == 2
    assert successful_requests[0]["run"] == run
    assert successful_requests[0]["source_available_at"] is None
    assert successful_requests[0]["allow_standard_meta_fallback"] is False
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT metric, source_cycle_time, source_available_at, captured_at, recorded_at, endpoint, product_id "
        "FROM raw_model_forecasts ORDER BY metric"
    ).fetchall()
    conn.close()
    assert [(row[0], row[1], row[5], row[6]) for row in rows] == [
        ("high", run.isoformat(), "single_runs", "metno_nordic::single_runs"),
        ("low", run.isoformat(), "single_runs", "metno_nordic::single_runs"),
    ]
    availability = datetime.fromisoformat(rows[0][2])
    assert availability >= completed_at[0]
    assert availability > fetch_started_at
    assert availability != metadata_available
    assert rows[0][2] == rows[1][2]
    assert rows[0][2] == rows[0][3]
    assert availability <= datetime.fromisoformat(rows[0][4])
