# Created: 2026-06-08
# Lifecycle: created=2026-06-08; last_reviewed=2026-07-17; last_reused=2026-07-17
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
    conn = sqlite3.connect(str(db)); conn.row_factory = sqlite3.Row
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
    conn.commit(); conn.close()
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
    from src.forecast.model_selection import ANCHOR_MODEL

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
    assert len(single_calls) == 1
    assert previous_calls == []


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
    conn = sqlite3.connect(str(db)); conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT model, source_cycle_time, source_available_at FROM raw_model_forecasts"
        " WHERE endpoint='single_runs'"
    ).fetchone()
    conn.close()
    assert row["model"] == "ecmwf_ifs"
    assert row["source_cycle_time"] == public_run.isoformat()
    assert row["source_available_at"] == available_at


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
    monkeypatch.setattr(om.httpx, "get", lambda *_args, **_kwargs: _Resp())

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

    monkeypatch.setattr(om.httpx, "get", _fake_get)

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
