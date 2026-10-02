# Created: 2026-10-01
# Lifecycle: created=2026-10-01; last_reviewed=2026-10-01; last_reused=never
# Purpose: Pin the possessed-response law: response bytes for one input identity
#          that failed to parse are not re-bought until an input changes.
# Reuse: Run when changing BPF single-runs memo, standard fallback or parser law.
# Authority basis: incident 2026-10-01 (NBM standard_meta_stamped, 3,624 reissued units).
"""A deterministic parse failure on bytes already held is final for those inputs.

Inputs: (model, city, target_date, run), the run's bytes marker (superseded, or
the provider's last_run_modification_time), the Day0 decision window and the
parser code revision. A transport error is not a possessed response.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx

from tests.test_bayes_precision_fusion_download import (  # noqa: F401 - autouse fixture
    _complete_hourly_local_day_payload,
    _forecast_db,
    _isolated_source_transports,
)


def _world(dl, client, monkeypatch, *, modified, now):
    """Latest NBM run whose bytes for an in-progress US day never parse."""

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0].astimezone(tz or UTC)

    monkeypatch.setattr(dl, "datetime", FixedDatetime)
    monkeypatch.setattr(dl, "_persist_exact_run_gap", lambda *_: None)
    run = datetime(2026, 10, 1, 0, tzinfo=UTC)

    def _requests(**_kwargs):
        return {"ncep_nbm_conus": dl._SourceClockSingleRunsRequest(
            run=run, source_available_at=(run + timedelta(minutes=50)).isoformat(),
            modification_time=modified[0],
        )}

    monkeypatch.setattr(dl, "_read_source_clock_single_runs_requests", _requests)
    seen: list[str] = []
    errors: list[Exception] = []

    def _fetch(_url, params, **_kwargs):
        seen.append(str(params["run"]))
        if errors:
            raise errors.pop(0)
        payload = _complete_hourly_local_day_payload(
            date(2026, 9, 30), utc_offset_seconds=-18000,
        )
        # One hour missing inside the owned window: no parse of these bytes succeeds.
        del payload["hourly"]["time"][22], payload["hourly"]["temperature_2m"][22]
        return payload

    monkeypatch.setattr(client, "fetch", _fetch)
    return run, seen, errors


def _kwargs(db):
    from src.data.bayes_precision_fusion_download import BayesPrecisionFusionDownloadTarget

    target = BayesPrecisionFusionDownloadTarget(
        city="Chicago", metric="high", target_date="2026-09-30", lead_days=0,
        latitude=41.79, longitude=-87.75, timezone_name="America/Chicago",
    )
    return dict(
        forecast_db=db, cycle=datetime(2026, 10, 1, 0, tzinfo=UTC), targets=[target],
        models=("ncep_nbm_conus",), include_previous_runs=False, prune_after=False,
        allow_single_runs_fallback=False,
    )


def test_unparseable_response_is_fetched_once_per_input_identity(tmp_path, monkeypatch):
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    dl._EXACT_RUN_UNMATERIALIZABLE_MEMO.clear()
    now = [datetime(2026, 10, 1, 1, 10, tzinfo=UTC)]
    modified = [datetime(2026, 10, 1, 0, 48, tzinfo=UTC)]
    _run, seen, _ = _world(dl, client, monkeypatch, modified=modified, now=now)
    kwargs = _kwargs(_forecast_db(tmp_path))

    first = dl.download_bayes_precision_fusion_extra_raw_inputs(**kwargs)
    assert len(seen) == 1 and first["written_row_count"] == 0
    [gap] = first["exact_run_unmaterializable"]
    assert gap["reason"].startswith(dl._POSSESSED_RESPONSE_GAP_PREFIX)
    assert "partial local-day coverage" in gap["reason"]
    # Proven for these inputs: the pass is complete, not a retry that re-wakes it.
    assert first["status"] == "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"

    for minute in (20, 40, 59):  # every later tick inside the same decision window
        now[0] = now[0].replace(minute=minute)
        repeat = dl.download_bayes_precision_fusion_extra_raw_inputs(**kwargs)
        assert repeat["exact_run_unmaterializable"] == first["exact_run_unmaterializable"]
    assert len(seen) == 1, "identical inputs must never re-buy the same bytes"


def test_changed_modification_parser_or_window_refetches(tmp_path, monkeypatch):
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    dl._EXACT_RUN_UNMATERIALIZABLE_MEMO.clear()
    now = [datetime(2026, 10, 1, 1, 10, tzinfo=UTC)]
    modified = [datetime(2026, 10, 1, 0, 48, tzinfo=UTC)]
    _run, seen, _ = _world(dl, client, monkeypatch, modified=modified, now=now)
    kwargs = _kwargs(_forecast_db(tmp_path))

    dl.download_bayes_precision_fusion_extra_raw_inputs(**kwargs)
    assert len(seen) == 1
    # The provider modified the run (more hours may have landed): re-read the bytes.
    modified[0] = datetime(2026, 10, 1, 1, 5, tzinfo=UTC)
    dl.download_bayes_precision_fusion_extra_raw_inputs(**kwargs)
    assert len(seen) == 2
    # A new parser code version is a new function of the same bytes.
    monkeypatch.setattr(dl, "_parser_revision", lambda: "next-parser")
    dl.download_bayes_precision_fusion_extra_raw_inputs(**kwargs)
    assert len(seen) == 3
    # The Day0 boundary crossing an hour changes which slots the run must own.
    now[0] = datetime(2026, 10, 1, 2, 10, tzinfo=UTC)
    dl.download_bayes_precision_fusion_extra_raw_inputs(**kwargs)
    assert len(seen) == 4
    dl.download_bayes_precision_fusion_extra_raw_inputs(**kwargs)
    assert len(seen) == 4


def test_new_run_refetches(tmp_path, monkeypatch):
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    dl._EXACT_RUN_UNMATERIALIZABLE_MEMO.clear()
    now = [datetime(2026, 10, 1, 1, 10, tzinfo=UTC)]
    modified = [datetime(2026, 10, 1, 0, 48, tzinfo=UTC)]
    run, seen, _ = _world(dl, client, monkeypatch, modified=modified, now=now)
    kwargs = _kwargs(_forecast_db(tmp_path))
    dl.download_bayes_precision_fusion_extra_raw_inputs(**kwargs)

    nxt = run + timedelta(hours=1)
    monkeypatch.setattr(dl, "_read_source_clock_single_runs_requests", lambda **_: {
        "ncep_nbm_conus": dl._SourceClockSingleRunsRequest(
            run=nxt, source_available_at=(nxt + timedelta(minutes=5)).isoformat(),
            modification_time=nxt + timedelta(minutes=4),
        ),
    })
    now[0] = datetime(2026, 10, 1, 1, 20, tzinfo=UTC)
    dl.download_bayes_precision_fusion_extra_raw_inputs(**{**kwargs, "cycle": nxt})
    assert seen == [run.strftime("%Y-%m-%dT%H:%M"), nxt.strftime("%Y-%m-%dT%H:%M")]


def test_standard_fallback_bytes_are_possessed_only_under_the_planned_modification(
    tmp_path, monkeypatch,
):
    """The 10-01 path: single-runs refuses run_not_published (the hourly NBM run is
    not on its archive grid), the metered standard endpoint serves the SAME run
    (its meta bracket refuses any other), and its bytes never parse."""
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    dl._EXACT_RUN_UNMATERIALIZABLE_MEMO.clear()
    now = [datetime(2026, 10, 1, 1, 10, tzinfo=UTC)]
    modified = [datetime(2026, 10, 1, 0, 48, tzinfo=UTC)]
    run, seen, errors = _world(dl, client, monkeypatch, modified=modified, now=now)
    kwargs = _kwargs(_forecast_db(tmp_path))
    bought: list[datetime] = []
    stamp_modified = [modified[0]]

    def _standard(**call):
        bought.append(call["run"])
        payload = _complete_hourly_local_day_payload(date(2026, 9, 30), utc_offset_seconds=-18000)
        del payload["hourly"]["time"][22], payload["hourly"]["temperature_2m"][22]
        return (payload,), dl._StandardMetaStampedTransport(
            run=call["run"], source_available_at=run + timedelta(minutes=50),
            modification_time=stamp_modified[0], forecast_hours=120,
        )

    monkeypatch.setattr(dl, "_fetch_standard_meta_stamped_payloads", _standard)
    refused = RuntimeError("Open-Meteo HTTP 400 (conditional:run_not_published)")

    # Bytes served under a modification other than the planned one pin nothing.
    stamp_modified[0] = datetime(2026, 10, 1, 0, 55, tzinfo=UTC)
    errors.append(refused)
    dl.download_bayes_precision_fusion_extra_raw_inputs(**kwargs)
    assert dl._EXACT_RUN_UNMATERIALIZABLE_MEMO == {} and bought == [run]

    stamp_modified[0] = modified[0]
    for _ in range(4):
        errors.append(refused)
        report = dl.download_bayes_precision_fusion_extra_raw_inputs(**kwargs)
        assert report["status"] == "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
    assert bought == [run, run], "possessed standard bytes must not be bought again"
    [reason] = dl._EXACT_RUN_UNMATERIALIZABLE_MEMO.values()
    assert reason.startswith(dl._POSSESSED_RESPONSE_GAP_PREFIX)


def test_durable_memo_survives_restart_and_prunes_old_runs(tmp_path, monkeypatch):
    import json

    import src.data.bayes_precision_fusion_download as dl

    path = tmp_path / "gap.json"
    monkeypatch.setattr(dl, "_exact_run_gap_memo_persistence_enabled", lambda: True)
    monkeypatch.setattr(dl, "_exact_run_gap_memo_path", lambda: path)
    dl._EXACT_RUN_UNMATERIALIZABLE_MEMO.clear()
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    identity = "bytes=modified=x;window=whole_day;parser=p"
    old = ("ncep_nbm_conus", "Chicago", "2026-01-01", (now - timedelta(days=9)).isoformat(), identity)
    new = ("ncep_nbm_conus", "Chicago", "2026-01-02", now.isoformat(), identity)
    path.write_text(json.dumps({"schema_version": dl._EXACT_RUN_GAP_MEMO_SCHEMA_VERSION,
        "entries": {"|".join(old): {"reason": "possessed_response:x"}}}))

    dl._memoize_exact_run_gap(new, "possessed_response:ValueError:partial local-day coverage")
    assert set(json.loads(path.read_text())["entries"]) == {"|".join(new)}
    dl._EXACT_RUN_UNMATERIALIZABLE_MEMO.clear()
    dl._load_persisted_exact_run_memo(force=True)
    assert set(dl._EXACT_RUN_UNMATERIALIZABLE_MEMO) == {new}


def test_unpinned_run_and_transport_error_stay_retryable(tmp_path, monkeypatch):
    """No modification marker: the provider may still add hours, so no memo.
    A transport error is never a possessed response."""
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    dl._EXACT_RUN_UNMATERIALIZABLE_MEMO.clear()
    now = [datetime(2026, 10, 1, 1, 10, tzinfo=UTC)]
    modified: list[datetime | None] = [None]
    _run, seen, errors = _world(dl, client, monkeypatch, modified=modified, now=now)
    kwargs = _kwargs(_forecast_db(tmp_path))

    unpinned = dl.download_bayes_precision_fusion_extra_raw_inputs(**kwargs)
    assert unpinned["status"] == "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE"
    assert dl._EXACT_RUN_UNMATERIALIZABLE_MEMO == {}

    modified[0] = datetime(2026, 10, 1, 0, 48, tzinfo=UTC)
    monkeypatch.setattr(dl, "_fetch_standard_meta_stamped_payloads",
        lambda **_: (_ for _ in ()).throw(RuntimeError("standard transport down")))
    errors.append(httpx.ConnectError("connection reset"))
    failed = dl.download_bayes_precision_fusion_extra_raw_inputs(**kwargs)
    assert failed["transport_errors"] and dl._EXACT_RUN_UNMATERIALIZABLE_MEMO == {}
    assert failed["status"] == "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE"
    dl.download_bayes_precision_fusion_extra_raw_inputs(**kwargs)
    assert len(seen) == 3, "a transport failure is retried on the next pass"
