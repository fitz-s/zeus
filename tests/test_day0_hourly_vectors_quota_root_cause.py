# Created: 2026-09-05
# Last reused or audited: 2026-09-10
# Lifecycle: created=2026-09-05; last_reviewed=2026-09-10; last_reused=2026-09-10
# Purpose: Regression tests for the round-3 quota root-cause fixes in
#   src/data/day0_hourly_vectors.py: a monotone per-model provider-run HWM pin (Open-
#   Meteo's meta.json is served from more than one replica; replicas have been observed
#   disagreeing about which run is current, and about run_availability_time for the SAME
#   run), plus the shared single-runs payload cache (src/data/bayes_precision_fusion_
#   download.py) that makes an all-or-nothing incomplete-bundle retry cheap.
"""TDD for the day0 provider-run HWM pin and the incomplete-bundle retry cost."""
from __future__ import annotations

import json as _json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import src.data.day0_hourly_vectors as day0
from src.data.day0_hourly_vectors import (
    Day0HourlyVector,
    Day0ProviderRunHwm,
    select_ready_day0_hourly_vectors,
)


def _hwm(model: str, init: datetime, avail: datetime) -> Day0ProviderRunHwm:
    return Day0ProviderRunHwm(
        model=model, run_initialisation_time=init, run_availability_time=avail
    )


def test_provider_run_hwm_pin_ignores_stale_replica_older_run(monkeypatch) -> None:
    """Alternating 12Z/18Z probes for the SAME model: once 18Z is accepted, a later
    probe reporting 12Z again (a stale meta.json replica) must never displace it."""
    day0._DAY0_PROVIDER_RUN_HWM_PIN.clear()
    monkeypatch.setattr(day0, "_day0_provider_run_hwm_pin_persistence_enabled", lambda: False)

    run_12z = datetime(2026, 9, 5, 12, tzinfo=UTC)
    run_18z = datetime(2026, 9, 5, 18, tzinfo=UTC)
    avail_12z = datetime(2026, 9, 5, 18, 19, 59, tzinfo=UTC)
    avail_18z = datetime(2026, 9, 6, 0, 27, 39, tzinfo=UTC)

    sequence = [run_18z, run_12z, run_18z, run_12z, run_18z, run_18z]
    for run in sequence:
        avail = avail_18z if run == run_18z else avail_12z
        probed = {"ecmwf_ifs": _hwm("ecmwf_ifs", run, avail)}
        pinned = day0._apply_day0_provider_run_hwm_pin(probed)
        assert pinned["ecmwf_ifs"].run_initialisation_time == run_18z, (
            "the pin must never regress to an older run once the newer one is accepted"
        )


def test_provider_run_hwm_pin_keeps_earliest_availability_for_same_run(monkeypatch) -> None:
    """Same run, two different availability replicas: the pin keeps the EARLIEST
    availability_time seen so a later replica cannot push public-usability backwards."""
    day0._DAY0_PROVIDER_RUN_HWM_PIN.clear()
    monkeypatch.setattr(day0, "_day0_provider_run_hwm_pin_persistence_enabled", lambda: False)

    run = datetime(2026, 9, 5, 18, tzinfo=UTC)
    early_avail = datetime(2026, 9, 6, 0, 27, 39, tzinfo=UTC)
    late_avail = datetime(2026, 9, 6, 0, 54, 11, tzinfo=UTC)

    first = day0._apply_day0_provider_run_hwm_pin(
        {"ecmwf_ifs": _hwm("ecmwf_ifs", run, early_avail)}
    )
    assert first["ecmwf_ifs"].run_availability_time == early_avail

    second = day0._apply_day0_provider_run_hwm_pin(
        {"ecmwf_ifs": _hwm("ecmwf_ifs", run, late_avail)}
    )
    assert second["ecmwf_ifs"].run_availability_time == early_avail

    third = day0._apply_day0_provider_run_hwm_pin(
        {"ecmwf_ifs": _hwm("ecmwf_ifs", run, early_avail)}
    )
    assert third["ecmwf_ifs"].run_availability_time == early_avail


def test_provider_run_hwm_pin_advances_on_genuinely_newer_run(monkeypatch) -> None:
    day0._DAY0_PROVIDER_RUN_HWM_PIN.clear()
    monkeypatch.setattr(day0, "_day0_provider_run_hwm_pin_persistence_enabled", lambda: False)

    run_18z = datetime(2026, 9, 5, 18, tzinfo=UTC)
    run_00z = datetime(2026, 9, 6, 0, tzinfo=UTC)
    avail_18z = datetime(2026, 9, 6, 0, 27, 39, tzinfo=UTC)
    avail_00z = datetime(2026, 9, 6, 6, 30, 0, tzinfo=UTC)

    day0._apply_day0_provider_run_hwm_pin({"ecmwf_ifs": _hwm("ecmwf_ifs", run_18z, avail_18z)})
    advanced = day0._apply_day0_provider_run_hwm_pin(
        {"ecmwf_ifs": _hwm("ecmwf_ifs", run_00z, avail_00z)}
    )
    assert advanced["ecmwf_ifs"].run_initialisation_time == run_00z
    assert advanced["ecmwf_ifs"].run_availability_time == avail_00z


def test_current_provider_bundle_already_persisted_ignores_availability_replica_skew(
    monkeypatch,
) -> None:
    """The exact defect: _current_provider_bundle_already_persisted compared the full
    (init, availability) pair, so a persisted bundle for a run already captured failed
    this check -- and re-triggered a full re-fetch -- whenever the HWM probe's
    availability_time differed from the persisted row's, even for the identical run."""
    run = datetime(2026, 9, 5, 18, tzinfo=UTC)
    persisted_avail = datetime(2026, 9, 6, 0, 27, 39, tzinfo=UTC)
    hwm_avail = datetime(2026, 9, 6, 0, 54, 11, tzinfo=UTC)  # a different replica

    persisted_meta = {
        "model": "ecmwf_ifs",
        "provider": "openmeteo",
        "provider_source_cycle_time_utc": run.isoformat(),
        "provider_source_available_at_utc": persisted_avail.isoformat(),
    }

    class _Vector:
        def __init__(self, model: str, meta: dict) -> None:
            self.model = model
            import json as _json

            self.source_run_meta_json = _json.dumps(meta)

    def _fake_read_freshest(**kwargs):
        return [_Vector("ecmwf_ifs", persisted_meta)]

    monkeypatch.setattr(day0, "read_freshest_day0_hourly_vectors", _fake_read_freshest)

    class _FakeConn:
        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "src.state.db.get_forecasts_connection_read_only", lambda: _FakeConn()
    )

    required_hwm = {"ecmwf_ifs": _hwm("ecmwf_ifs", run, hwm_avail)}
    already_persisted = day0._current_provider_bundle_already_persisted(
        city="Singapore",
        target_dates=("2026-09-06",),
        expected_models=("ecmwf_ifs",),
        required_hwm=required_hwm,
        decision_time=datetime(2026, 9, 6, 1, 0, tzinfo=UTC),
        remaining_window_starts={"2026-09-06": datetime(2026, 9, 6, 0, 0, tzinfo=UTC)},
    )
    assert already_persisted is True, (
        "a bundle already persisted for the SAME run must not be re-fetched merely "
        "because a different meta.json replica reports a different availability_time"
    )


def test_incomplete_bundle_retry_reuses_cached_models_via_shared_payload_cache(
    monkeypatch,
) -> None:
    """QUOTA round 3: day0's all-or-nothing bundle (fetch_day0_hourly_vectors) discards
    every already-fetched model's payload when a LATER model in the loop fails, and the
    45s-cadence incomplete-bundle retry re-requests the whole bundle. The shared payload
    cache in _fetch_single_runs_hourly_payloads_batched must serve the models that
    already succeeded from cache on the retry: a 4-model bundle where one model fails on
    the first pass and succeeds on the second must issue 4 + 1 HTTP calls total, not 8."""
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client

    dl._SINGLE_RUNS_PAYLOAD_CACHE.clear()
    models = ["ecmwf_ifs", "icon_global", "ukmo_global_deterministic_10km", "gem_hrdps_continental"]
    location = (1.35019, 103.994003, "Asia/Singapore", (date(2026, 9, 6),))
    run = datetime(2026, 9, 5, 18, tzinfo=UTC)

    call_count = {"n": 0}
    fail_once_for = "gem_hrdps_continental"
    failed_already = {"done": False}

    def _payload() -> dict:
        return {
            "hourly": {
                "time": ["2026-09-06T00:00", "2026-09-06T21:00"],
                "temperature_2m": [24.0, 30.0],
            },
            "hourly_units": {"temperature_2m": "°C"},
        }

    def _fetch(_url, params, **kwargs):
        call_count["n"] += 1
        model_param = str(params.get("models", ""))
        if (
            fail_once_for in model_param
            and not failed_already["done"]
        ):
            failed_already["done"] = True
            raise RuntimeError("synthetic transport failure for gem_hrdps_continental")
        return _payload()

    monkeypatch.setattr(client, "fetch", _fetch)

    def _one_bundle_pass() -> list[str]:
        """Mirror _day0_exact_run_payloads' per-model loop shape: one model at a time,
        the whole pass raises (and its results are discarded) if any model fails."""
        succeeded: list[str] = []
        for model in models:
            dl._fetch_single_runs_hourly_payloads_batched(
                models=[model], locations=[location], run=run, forecast_hours=72,
            )
            succeeded.append(model)
        return succeeded

    # Pass 1: gem_hrdps_continental fails; its own AND the bundle's other successes
    # are discarded by the all-or-nothing contract (matches fetch_day0_hourly_vectors'
    # fail-soft try/except around _day0_exact_run_payloads).
    try:
        _one_bundle_pass()
        raised = False
    except RuntimeError:
        raised = True
    assert raised, "the synthetic failure must propagate like a real transport error"
    assert call_count["n"] == 4, "pass 1 attempts every model exactly once"

    # Pass 2 (retry): the 3 models that already succeeded must be cache hits; only
    # gem_hrdps_continental (now succeeding) issues a real HTTP call.
    succeeded = _one_bundle_pass()
    assert succeeded == models
    assert call_count["n"] == 5, "pass 2 must add exactly ONE new HTTP call, not 4"


def _det_vector(city, model: str, decision_time: datetime) -> Day0HourlyVector:
    tz = ZoneInfo(city.timezone)
    local_day = decision_time.astimezone(tz).date()
    times = tuple(
        f"{(local_day + timedelta(days=offset)).isoformat()}T{hour:02d}:00"
        for offset in (0, 1)
        for hour in range(24)
    )
    return Day0HourlyVector(
        model=model, city=city.name, target_date=local_day.isoformat(),
        timezone_name=city.timezone, captured_at=decision_time.isoformat(),
        times=times, temps_c=tuple(15.0 for _ in times),
    )


def _ensemble_member_vector(
    city, member: str, run: datetime, available: datetime, decision_time: datetime
) -> Day0HourlyVector:
    tz = ZoneInfo(city.timezone)
    local_day = decision_time.astimezone(tz).date()
    times = tuple(
        f"{(local_day + timedelta(days=offset)).isoformat()}T{hour:02d}:00"
        for offset in (0, 1)
        for hour in range(24)
    )
    meta = {
        "model": member,
        "provider": "openmeteo",
        "provider_source_cycle_time_utc": run.isoformat(),
        "provider_source_available_at_utc": available.isoformat(),
    }
    return Day0HourlyVector(
        model=member, city=city.name, target_date="",
        timezone_name=city.timezone, captured_at=decision_time.isoformat(),
        times=times, temps_c=tuple(15.0 for _ in times),
        source_run_meta_json=_json.dumps(meta),
    )


def _strict_ensemble_member_vector(
    city,
    member: str,
    run: datetime,
    available: datetime,
    decision_time: datetime,
    fetch_started: datetime,
    fetch_finished: datetime,
) -> Day0HourlyVector:
    vector = _ensemble_member_vector(city, member, run, available, decision_time)
    meta = _json.loads(vector.source_run_meta_json or "{}")
    meta.update(
        fetch_started_at=fetch_started.isoformat(),
        fetch_finished_at=fetch_finished.isoformat(),
    )
    return replace(vector, source_run_meta_json=_json.dumps(meta))


def test_deterministic_ready_still_fetches_required_ens_then_composite_dedups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deterministic hit cannot hide a missing required ENS carrier."""
    from src.data.openmeteo_quota import OpenMeteoQuotaTracker

    city = SimpleNamespace(name="Paris", timezone="Europe/Paris", lat=48.8, lon=2.3)
    decision = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
    target_date = decision.astimezone(ZoneInfo(city.timezone)).date().isoformat()
    run = decision - timedelta(hours=2)
    available = decision - timedelta(minutes=30)
    members = day0.day0_source_clock_ensemble_member_models()
    ens_vectors = [
        _strict_ensemble_member_vector(
            city,
            member,
            run,
            available,
            decision,
            decision + timedelta(minutes=1),
            decision + timedelta(minutes=3),
        )
        for member in members
    ]
    clock = iter(
        (
            decision + timedelta(minutes=4),
            decision + timedelta(minutes=5),
            decision + timedelta(minutes=6),
        )
    )
    monkeypatch.setattr(day0, "_day0_utc_now", lambda: next(clock))
    monkeypatch.setattr(day0, "quota_tracker", OpenMeteoQuotaTracker())
    monkeypatch.setattr(day0, "day0_hourly_models_for_city", lambda _city: ["ecmwf_ifs"])
    monkeypatch.setattr(
        day0, "day0_source_clock_ensemble_target_dates", lambda **_kwargs: (target_date,)
    )
    monkeypatch.setattr(
        day0, "_current_provider_bundle_already_persisted", lambda **_kwargs: True
    )
    ens_ready = {"value": False}
    hwm_probes = {"n": 0}
    ens_fetches = {"n": 0}
    persisted = {"n": 0}

    monkeypatch.setattr(
        day0,
        "_probe_day0_source_clock_ensemble_run_hwm",
        lambda **_kwargs: hwm_probes.__setitem__("n", hwm_probes["n"] + 1)
        or Day0ProviderRunHwm(
            model=day0.DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL,
            run_initialisation_time=run,
            run_availability_time=available,
        ),
    )
    monkeypatch.setattr(
        day0,
        "_current_ensemble_bundle_already_persisted",
        lambda **_kwargs: ens_ready["value"],
    )
    monkeypatch.setattr(
        day0,
        "fetch_day0_hourly_vectors",
        lambda **_kwargs: pytest.fail("deterministic payload must not be re-fetched"),
    )

    def fetch_ens(*_args, **_kwargs):
        ens_fetches["n"] += 1
        return ens_vectors, "sha256:ens"

    monkeypatch.setattr(day0, "fetch_day0_source_clock_ensemble_vectors", fetch_ens)

    def persist(rows, *, endpoint=None, **_kwargs):
        if endpoint == day0.OPENMETEO_ENSEMBLE_URL:
            persisted["n"] += len(rows)
            ens_ready["value"] = True
        return len(rows)

    monkeypatch.setattr(day0, "persist_day0_hourly_vectors", persist)
    monkeypatch.setattr(
        day0,
        "read_freshest_day0_hourly_vectors",
        lambda **_kwargs: ens_vectors if persisted["n"] else [],
    )
    day0._LAST_REFRESH_MONOTONIC.clear()
    day0._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.clear()
    day0._INCOMPLETE_RETRY_STREAK.clear()

    first = day0.maybe_refresh_day0_hourly_vectors(
        [city], decision_time=decision, interval_s=0.0, quota_priority_cities=1,
        return_stats=True,
    )
    second = day0.maybe_refresh_day0_hourly_vectors(
        [city], decision_time=decision, interval_s=0.0, quota_priority_cities=1,
        return_stats=True,
    )

    assert first.vectors_written == 51
    assert second.vectors_written == 0
    assert ens_fetches["n"] == 1
    assert persisted["n"] == 51
    assert hwm_probes["n"] == 2
    assert day0._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC == {}


def test_ens_ready_fetches_only_missing_deterministic_and_release_due_refetches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ENS hit cannot suppress a needed deterministic fetch or release refresh."""
    from src.data.openmeteo_quota import OpenMeteoQuotaTracker

    city = SimpleNamespace(name="Paris", timezone="Europe/Paris", lat=48.8, lon=2.3)
    decision = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
    target_date = decision.astimezone(ZoneInfo(city.timezone)).date().isoformat()
    det_fetches = {"n": 0}
    ens_fetches = {"n": 0}
    monkeypatch.setattr(day0, "quota_tracker", OpenMeteoQuotaTracker())
    monkeypatch.setattr(day0, "day0_hourly_models_for_city", lambda _city: ["ecmwf_ifs"])
    monkeypatch.setattr(
        day0, "day0_source_clock_ensemble_target_dates", lambda **_kwargs: (target_date,)
    )
    monkeypatch.setattr(day0, "_current_provider_bundle_already_persisted", lambda **_kwargs: False)
    monkeypatch.setattr(
        day0,
        "_probe_day0_source_clock_ensemble_run_hwm",
        lambda **_kwargs: Day0ProviderRunHwm(
            model=day0.DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL,
            run_initialisation_time=decision - timedelta(hours=2),
            run_availability_time=decision - timedelta(minutes=30),
        ),
    )
    monkeypatch.setattr(day0, "_current_ensemble_bundle_already_persisted", lambda **_kwargs: True)

    def fetch_det(*_args, **_kwargs):
        det_fetches["n"] += 1
        return [], ""

    monkeypatch.setattr(day0, "fetch_day0_hourly_vectors", fetch_det)
    monkeypatch.setattr(
        day0,
        "fetch_day0_source_clock_ensemble_vectors",
        lambda *_args, **_kwargs: ens_fetches.__setitem__("n", ens_fetches["n"] + 1)
        or ([], ""),
    )
    monkeypatch.setattr(day0, "persist_day0_hourly_vectors", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(day0, "read_freshest_day0_hourly_vectors", lambda **_kwargs: [])
    day0._LAST_REFRESH_MONOTONIC.clear()

    stats = day0.maybe_refresh_day0_hourly_vectors(
        [city], decision_time=decision, interval_s=0.0, quota_priority_cities=1,
        return_stats=True,
    )
    assert stats.cities_attempted == 1
    assert det_fetches["n"] == 1
    assert ens_fetches["n"] == 0

    day0._LAST_REFRESH_MONOTONIC.clear()
    day0._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.clear()
    day0._INCOMPLETE_RETRY_STREAK.clear()
    stats = day0.maybe_refresh_day0_hourly_vectors(
        [city], decision_time=decision, interval_s=0.0, quota_critical_cities=1,
        provider_run_hwm={
            "ecmwf_ifs": Day0ProviderRunHwm(
                model="ecmwf_ifs",
                run_initialisation_time=decision - timedelta(hours=2),
                run_availability_time=decision - timedelta(minutes=30),
            )
        },
        release_due_city_dates={(city.name, target_date)},
        return_stats=True,
    )
    assert stats.cities_attempted == 1
    assert det_fetches["n"] == 2
    assert ens_fetches["n"] == 0


def test_ens_failure_marks_retry_and_does_not_probe_before_retry(monkeypatch) -> None:
    """ENS strict/readback failure uses the existing bounded retry gate."""
    from src.data.openmeteo_quota import OpenMeteoQuotaTracker

    city = SimpleNamespace(name="Paris", timezone="Europe/Paris", lat=48.8, lon=2.3)
    decision = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
    target_date = decision.astimezone(ZoneInfo(city.timezone)).date().isoformat()
    run = decision - timedelta(hours=2)
    available = decision - timedelta(minutes=30)
    members = day0.day0_source_clock_ensemble_member_models()
    ens_vectors = [
        _strict_ensemble_member_vector(
            city, member, run, available, decision,
            decision + timedelta(minutes=1), decision + timedelta(minutes=3),
        )
        for member in members
    ]
    counts = {"probe": 0, "fetch": 0}
    monotonic = {"now": 100.0}
    monkeypatch.setattr(day0, "quota_tracker", OpenMeteoQuotaTracker())
    monkeypatch.setattr(day0.time, "monotonic", lambda: monotonic["now"])
    monkeypatch.setattr(day0, "day0_hourly_models_for_city", lambda _city: ["ecmwf_ifs"])
    monkeypatch.setattr(day0, "day0_source_clock_ensemble_target_dates", lambda **_kwargs: (target_date,))
    monkeypatch.setattr(day0, "_current_provider_bundle_already_persisted", lambda **_kwargs: True)
    monkeypatch.setattr(
        day0,
        "_probe_day0_source_clock_ensemble_run_hwm",
        lambda **_kwargs: counts.__setitem__("probe", counts["probe"] + 1)
        or Day0ProviderRunHwm(
            model=day0.DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL,
            run_initialisation_time=run,
            run_availability_time=available,
        ),
    )
    monkeypatch.setattr(day0, "_current_ensemble_bundle_already_persisted", lambda **_kwargs: False)
    monkeypatch.setattr(
        day0,
        "fetch_day0_hourly_vectors",
        lambda **_kwargs: pytest.fail("deterministic payload must not be fetched"),
    )
    monkeypatch.setattr(
        day0,
        "fetch_day0_source_clock_ensemble_vectors",
        lambda *_args, **_kwargs: counts.__setitem__("fetch", counts["fetch"] + 1)
        or (ens_vectors, "sha256:ens"),
    )
    monkeypatch.setattr(day0, "persist_day0_hourly_vectors", lambda *_args, **_kwargs: 51)
    monkeypatch.setattr(day0, "read_freshest_day0_hourly_vectors", lambda **_kwargs: [])
    day0._LAST_REFRESH_MONOTONIC.clear()
    day0._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.clear()
    day0._INCOMPLETE_RETRY_STREAK.clear()

    first = day0.maybe_refresh_day0_hourly_vectors(
        [city], decision_time=decision, interval_s=0.0, quota_priority_cities=1,
        return_stats=True,
    )
    second = day0.maybe_refresh_day0_hourly_vectors(
        [city], decision_time=decision, interval_s=0.0, quota_priority_cities=1,
        return_stats=True,
    )
    assert first.incomplete_expected_bundles == 1
    assert second.cities_skipped_throttle == 1
    assert counts == {"probe": 1, "fetch": 1}
    assert day0._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC[
        "Paris|2026-09-10|ens=2026-09-10"
    ] > 100.0
    assert "Paris|2026-09-10" not in day0._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC


def test_complete_ens_persists_when_deterministic_fetch_fails(monkeypatch) -> None:
    """Both missing carriers retain the complete ENS physical writes and retry debt."""
    from src.data.openmeteo_quota import OpenMeteoQuotaTracker

    city = SimpleNamespace(name="Paris", timezone="Europe/Paris", lat=48.8, lon=2.3)
    decision = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
    target_date = decision.astimezone(ZoneInfo(city.timezone)).date().isoformat()
    run = decision - timedelta(hours=2)
    available = decision - timedelta(minutes=30)
    members = day0.day0_source_clock_ensemble_member_models()
    ens_vectors = [
        _strict_ensemble_member_vector(
            city, member, run, available, decision,
            decision + timedelta(minutes=1), decision + timedelta(minutes=3),
        )
        for member in members
    ]
    counts = {"det": 0, "ens": 0, "persisted": 0}
    clock = iter(
        (
            decision + timedelta(minutes=4),
            decision + timedelta(minutes=5),
            decision + timedelta(minutes=6),
        )
    )
    monkeypatch.setattr(day0, "_day0_utc_now", lambda: next(clock))
    monkeypatch.setattr(day0, "quota_tracker", OpenMeteoQuotaTracker())
    monkeypatch.setattr(day0, "day0_hourly_models_for_city", lambda _city: ["ecmwf_ifs"])
    monkeypatch.setattr(day0, "day0_source_clock_ensemble_target_dates", lambda **_kwargs: (target_date,))
    monkeypatch.setattr(day0, "_current_provider_bundle_already_persisted", lambda **_kwargs: False)
    monkeypatch.setattr(
        day0,
        "_probe_day0_source_clock_ensemble_run_hwm",
        lambda **_kwargs: Day0ProviderRunHwm(
            model=day0.DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL,
            run_initialisation_time=run,
            run_availability_time=available,
        ),
    )
    monkeypatch.setattr(day0, "_current_ensemble_bundle_already_persisted", lambda **_kwargs: False)
    monkeypatch.setattr(
        day0,
        "fetch_day0_hourly_vectors",
        lambda *_args, **_kwargs: counts.__setitem__("det", counts["det"] + 1)
        or ([], ""),
    )
    monkeypatch.setattr(
        day0,
        "fetch_day0_source_clock_ensemble_vectors",
        lambda *_args, **_kwargs: counts.__setitem__("ens", counts["ens"] + 1)
        or (ens_vectors, "sha256:ens"),
    )
    monkeypatch.setattr(
        day0,
        "persist_day0_hourly_vectors",
        lambda rows, **_kwargs: counts.__setitem__("persisted", counts["persisted"] + len(rows))
        or len(rows),
    )
    monkeypatch.setattr(day0, "read_freshest_day0_hourly_vectors", lambda **_kwargs: ens_vectors)
    day0._LAST_REFRESH_MONOTONIC.clear()
    day0._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.clear()
    day0._INCOMPLETE_RETRY_STREAK.clear()

    stats = day0.maybe_refresh_day0_hourly_vectors(
        [city], decision_time=decision, interval_s=0.0, quota_priority_cities=1,
        return_stats=True,
    )
    assert counts == {"det": 1, "ens": 1, "persisted": 51}
    assert stats.incomplete_expected_bundles == 1
    assert stats.unavailable_bundles[0].reason == "DAY0_HOURLY_BUNDLE_FETCH_UNAVAILABLE"


def test_ens_failure_keeps_deterministic_write_and_next_due_fetches_only_ens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ENS debt must not discard a complete deterministic carrier or re-fetch it."""
    from src.data.openmeteo_quota import OpenMeteoQuotaTracker

    city = SimpleNamespace(name="Paris", timezone="Europe/Paris", lat=48.8, lon=2.3)
    decision = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
    target_date = decision.astimezone(ZoneInfo(city.timezone)).date().isoformat()
    det_model = "ecmwf_ifs"
    run = decision - timedelta(hours=2)
    available = decision - timedelta(minutes=30)
    det_vector = replace(
        _det_vector(city, det_model, decision),
        source_run_meta_json=_json.dumps(
            {
                "fetch_started_at": (decision + timedelta(minutes=1)).isoformat(),
                "fetch_finished_at": (decision + timedelta(minutes=3)).isoformat(),
            }
        ),
    )
    members = day0.day0_source_clock_ensemble_member_models()
    ens_vectors = [
        _strict_ensemble_member_vector(
            city, member, run, available, decision,
            decision + timedelta(minutes=1), decision + timedelta(minutes=3),
        )
        for member in members
    ]
    clock = iter(
        tuple(decision + timedelta(minutes=offset) for offset in (4, 5, 6, 7, 8, 9))
    )
    monotonic = {"now": 100.0}
    counts = {"det_fetch": 0, "ens_fetch": 0, "ens_persist": 0, "det_persist": 0}
    det_ready = {"value": False}
    monkeypatch.setattr(day0, "_day0_utc_now", lambda: next(clock))
    monkeypatch.setattr(day0.time, "monotonic", lambda: monotonic["now"])
    monkeypatch.setattr(day0, "quota_tracker", OpenMeteoQuotaTracker())
    monkeypatch.setattr(day0, "day0_hourly_models_for_city", lambda _city: [det_model])
    monkeypatch.setattr(
        day0, "day0_source_clock_ensemble_target_dates", lambda **_kwargs: (target_date,)
    )
    monkeypatch.setattr(
        day0, "_current_provider_bundle_already_persisted", lambda **_kwargs: det_ready["value"]
    )
    monkeypatch.setattr(
        day0,
        "_probe_day0_source_clock_ensemble_run_hwm",
        lambda **_kwargs: Day0ProviderRunHwm(
            model=day0.DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL,
            run_initialisation_time=run,
            run_availability_time=available,
        ),
    )
    monkeypatch.setattr(day0, "_current_ensemble_bundle_already_persisted", lambda **_kwargs: False)
    monkeypatch.setattr(
        day0,
        "fetch_day0_hourly_vectors",
        lambda *_args, **_kwargs: counts.__setitem__("det_fetch", counts["det_fetch"] + 1)
        or ([det_vector], "sha256:det"),
    )
    monkeypatch.setattr(
        day0,
        "fetch_day0_source_clock_ensemble_vectors",
        lambda *_args, **_kwargs: counts.__setitem__("ens_fetch", counts["ens_fetch"] + 1)
        or (ens_vectors, "sha256:ens"),
    )

    def persist(rows, *, endpoint=None, **_kwargs):
        if endpoint == day0.OPENMETEO_ENSEMBLE_URL:
            counts["ens_persist"] += len(rows)
        else:
            counts["det_persist"] += len(rows)
            if counts["det_persist"] == 2:
                det_ready["value"] = True
        return len(rows)

    monkeypatch.setattr(day0, "persist_day0_hourly_vectors", persist)

    def readback(**kwargs):
        expected = tuple(kwargs.get("expected_models") or ())
        if set(expected) == set(members):
            return ens_vectors if counts["ens_persist"] >= 102 else []
        if expected == (det_model,):
            return [det_vector]
        return []

    monkeypatch.setattr(day0, "read_freshest_day0_hourly_vectors", readback)
    day0._LAST_REFRESH_MONOTONIC.clear()
    day0._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.clear()
    day0._INCOMPLETE_RETRY_STREAK.clear()

    first = day0.maybe_refresh_day0_hourly_vectors(
        [city], decision_time=decision, interval_s=0.0, quota_priority_cities=1,
        return_stats=True,
    )
    retry_at = day0._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC[
        "Paris|2026-09-10|ens=2026-09-10"
    ]
    retry_streak = day0._INCOMPLETE_RETRY_STREAK[
        "Paris|2026-09-10|ens=2026-09-10"
    ]
    monotonic["now"] = retry_at
    critical = day0.maybe_refresh_day0_hourly_vectors(
        [city], decision_time=decision, interval_s=0.0,
        quota_critical_cities=1, quota_priority_cities=0,
        return_stats=True,
    )
    assert critical.vectors_written == 0
    assert counts == {"det_fetch": 1, "ens_fetch": 1, "ens_persist": 51, "det_persist": 2}
    assert day0._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC[
        "Paris|2026-09-10|ens=2026-09-10"
    ] == retry_at
    assert day0._INCOMPLETE_RETRY_STREAK[
        "Paris|2026-09-10|ens=2026-09-10"
    ] == retry_streak
    second = day0.maybe_refresh_day0_hourly_vectors(
        [city], decision_time=decision, interval_s=0.0, quota_priority_cities=1,
        return_stats=True,
    )

    assert first.vectors_written == 53
    assert first.incomplete_expected_bundles == 1
    assert second.vectors_written == 51
    assert counts == {
        "det_fetch": 1,
        "ens_fetch": 2,
        "ens_persist": 102,
        "det_persist": 2,
    }
    assert day0._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC == {}


def test_ensemble_persists_when_deterministic_fetch_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deterministic transport exception cannot discard a complete ENS carrier."""
    from src.data.openmeteo_quota import OpenMeteoQuotaTracker

    city = SimpleNamespace(name="Paris", timezone="Europe/Paris", lat=48.8, lon=2.3)
    decision = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
    target_date = decision.astimezone(ZoneInfo(city.timezone)).date().isoformat()
    run = decision - timedelta(hours=2)
    available = decision - timedelta(minutes=30)
    members = day0.day0_source_clock_ensemble_member_models()
    ens_vectors = [
        _strict_ensemble_member_vector(
            city, member, run, available, decision,
            decision + timedelta(minutes=1), decision + timedelta(minutes=3),
        )
        for member in members
    ]
    clock = iter(
        tuple(decision + timedelta(minutes=offset) for offset in (4, 5, 6))
    )
    counts = {"det": 0, "ens": 0, "persisted": 0}
    monkeypatch.setattr(day0, "_day0_utc_now", lambda: next(clock))
    monkeypatch.setattr(day0, "quota_tracker", OpenMeteoQuotaTracker())
    monkeypatch.setattr(day0, "day0_hourly_models_for_city", lambda _city: ["ecmwf_ifs"])
    monkeypatch.setattr(
        day0, "day0_source_clock_ensemble_target_dates", lambda **_kwargs: (target_date,)
    )
    monkeypatch.setattr(day0, "_current_provider_bundle_already_persisted", lambda **_kwargs: False)
    monkeypatch.setattr(
        day0,
        "_probe_day0_source_clock_ensemble_run_hwm",
        lambda **_kwargs: Day0ProviderRunHwm(
            model=day0.DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL,
            run_initialisation_time=run,
            run_availability_time=available,
        ),
    )
    monkeypatch.setattr(day0, "_current_ensemble_bundle_already_persisted", lambda **_kwargs: False)

    def fetch_det(*_args, **_kwargs):
        counts["det"] += 1
        raise RuntimeError("deterministic transport failure")

    monkeypatch.setattr(day0, "fetch_day0_hourly_vectors", fetch_det)
    monkeypatch.setattr(
        day0,
        "fetch_day0_source_clock_ensemble_vectors",
        lambda *_args, **_kwargs: counts.__setitem__("ens", counts["ens"] + 1)
        or (ens_vectors, "sha256:ens"),
    )
    monkeypatch.setattr(
        day0,
        "persist_day0_hourly_vectors",
        lambda rows, **_kwargs: counts.__setitem__("persisted", counts["persisted"] + len(rows))
        or len(rows),
    )
    monkeypatch.setattr(
        day0,
        "read_freshest_day0_hourly_vectors",
        lambda **kwargs: ens_vectors
        if set(kwargs.get("expected_models") or ()) == set(members)
        else [],
    )
    day0._LAST_REFRESH_MONOTONIC.clear()
    day0._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.clear()
    day0._INCOMPLETE_RETRY_STREAK.clear()

    stats = day0.maybe_refresh_day0_hourly_vectors(
        [city], decision_time=decision, interval_s=0.0, quota_priority_cities=1,
        return_stats=True,
    )

    assert counts == {"det": 1, "ens": 1, "persisted": 51}
    assert stats.vectors_written == 51
    assert stats.incomplete_expected_bundles == 1
    assert day0._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC


def test_deterministic_persists_when_ensemble_fetch_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ENS transport exception cannot discard a complete deterministic carrier."""
    from src.data.openmeteo_quota import OpenMeteoQuotaTracker

    city = SimpleNamespace(name="Paris", timezone="Europe/Paris", lat=48.8, lon=2.3)
    decision = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
    target_date = decision.astimezone(ZoneInfo(city.timezone)).date().isoformat()
    det_model = "ecmwf_ifs"
    det_vector = replace(
        _det_vector(city, det_model, decision),
        source_run_meta_json=_json.dumps(
            {
                "fetch_started_at": (decision + timedelta(minutes=1)).isoformat(),
                "fetch_finished_at": (decision + timedelta(minutes=3)).isoformat(),
            }
        ),
    )
    run = decision - timedelta(hours=2)
    available = decision - timedelta(minutes=30)
    clock = iter(
        tuple(decision + timedelta(minutes=offset) for offset in (4, 5, 6))
    )
    counts = {"det": 0, "ens": 0, "det_persisted": 0}
    monkeypatch.setattr(day0, "_day0_utc_now", lambda: next(clock))
    monkeypatch.setattr(day0, "quota_tracker", OpenMeteoQuotaTracker())
    monkeypatch.setattr(day0, "day0_hourly_models_for_city", lambda _city: [det_model])
    monkeypatch.setattr(
        day0, "day0_source_clock_ensemble_target_dates", lambda **_kwargs: (target_date,)
    )
    monkeypatch.setattr(day0, "_current_provider_bundle_already_persisted", lambda **_kwargs: False)
    monkeypatch.setattr(
        day0,
        "_probe_day0_source_clock_ensemble_run_hwm",
        lambda **_kwargs: Day0ProviderRunHwm(
            model=day0.DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL,
            run_initialisation_time=run,
            run_availability_time=available,
        ),
    )
    monkeypatch.setattr(day0, "_current_ensemble_bundle_already_persisted", lambda **_kwargs: False)
    monkeypatch.setattr(
        day0,
        "fetch_day0_hourly_vectors",
        lambda *_args, **_kwargs: counts.__setitem__("det", counts["det"] + 1)
        or ([det_vector], "sha256:det"),
    )

    def fetch_ens(*_args, **_kwargs):
        counts["ens"] += 1
        raise RuntimeError("ensemble transport failure")

    monkeypatch.setattr(day0, "fetch_day0_source_clock_ensemble_vectors", fetch_ens)

    def persist(rows, *, endpoint=None, **_kwargs):
        if endpoint != day0.OPENMETEO_ENSEMBLE_URL:
            counts["det_persisted"] += len(rows)
        return len(rows)

    monkeypatch.setattr(day0, "persist_day0_hourly_vectors", persist)
    monkeypatch.setattr(
        day0,
        "read_freshest_day0_hourly_vectors",
        lambda **kwargs: [det_vector]
        if tuple(kwargs.get("expected_models") or ()) == (det_model,)
        else [],
    )
    day0._LAST_REFRESH_MONOTONIC.clear()
    day0._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.clear()
    day0._INCOMPLETE_RETRY_STREAK.clear()

    stats = day0.maybe_refresh_day0_hourly_vectors(
        [city], decision_time=decision, interval_s=0.0, quota_priority_cities=1,
        return_stats=True,
    )

    assert counts == {"det": 1, "ens": 1, "det_persisted": 2}
    assert stats.vectors_written == 2
    assert stats.incomplete_expected_bundles == 1
    assert day0._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC


def test_producer_uses_completion_clock_for_deterministic_strict_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fetch that crosses D is rejected at D and accepted at completion."""
    from src.data.openmeteo_model_updates import OpenMeteoModelUpdate

    city = SimpleNamespace(name="Clock City", timezone="UTC", lat=0.0, lon=0.0)
    decision = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
    fetch_started = decision + timedelta(minutes=1)
    fetch_finished = decision + timedelta(minutes=3)
    materialized = decision + timedelta(minutes=4)
    persisted_readback = decision + timedelta(minutes=5)
    clock = iter((fetch_started, fetch_finished, materialized, persisted_readback))
    monkeypatch.setattr(day0, "_day0_utc_now", lambda: next(clock))

    times = [
        f"{(decision.date() + timedelta(days=offset)).isoformat()}T{hour:02d}:00"
        for offset in (0, 1)
        for hour in range(24)
    ]
    payload = {"hourly": {"time": times, "temperature_2m": [20.0] * len(times)}}
    update = OpenMeteoModelUpdate(
        model="icon_d2",
        last_run_initialisation_time=decision - timedelta(hours=2),
        last_run_availability_time=decision - timedelta(minutes=30),
        last_run_modification_time=decision - timedelta(minutes=25),
    )
    monkeypatch.setattr(
        "src.data.openmeteo_model_updates.fetch_model_updates",
        lambda models, **_kwargs: tuple(update for _model in models),
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download._fetch_single_runs_hourly_payloads_batched",
        lambda **_kwargs: (payload,),
    )

    vectors, request_hash = day0.fetch_day0_hourly_vectors(
        city, models=["icon_d2"], now=decision
    )
    assert request_hash.startswith("sha256:")
    assert len(vectors) == 1
    meta = _json.loads(vectors[0].source_run_meta_json or "{}")
    assert vectors[0].captured_at == decision.isoformat()
    assert meta["fetch_started_at"] == fetch_started.isoformat()
    assert meta["fetch_finished_at"] == fetch_finished.isoformat()

    strict = dict(
        target_date=decision.date().isoformat(),
        expected_models=["icon_d2"],
        require_expected=True,
        remaining_window_start=decision,
        require_complete_remaining_window=True,
    )
    assert select_ready_day0_hourly_vectors(vectors, now=decision, **strict) == []
    assert select_ready_day0_hourly_vectors(
        vectors, now=materialized, **strict
    ) == vectors

    stored: list[Day0HourlyVector] = []
    readback_times: list[datetime] = []
    monkeypatch.setattr(day0, "day0_hourly_models_for_city", lambda _city: ["icon_d2"])
    monkeypatch.setattr(
        day0, "_current_provider_bundle_already_persisted", lambda **_kwargs: False
    )
    monkeypatch.setattr(
        day0,
        "persist_day0_hourly_vectors",
        lambda rows, **_kwargs: stored.extend(rows) or len(rows),
    )

    def strict_readback(**kwargs):
        readback_times.append(kwargs["now"])
        return select_ready_day0_hourly_vectors(
            stored,
            target_date=kwargs["target_date"],
            now=kwargs["now"],
            expected_models=kwargs["expected_models"],
            require_expected=kwargs["require_expected"],
            max_bundle_skew_minutes=kwargs["max_bundle_skew_minutes"],
            remaining_window_start=kwargs["remaining_window_start"],
            require_complete_remaining_window=kwargs["require_complete_remaining_window"],
        )

    monkeypatch.setattr(day0, "read_freshest_day0_hourly_vectors", strict_readback)
    day0._LAST_REFRESH_MONOTONIC.clear()
    clock = iter((fetch_started, fetch_finished, materialized, persisted_readback))
    stats = day0.maybe_refresh_day0_hourly_vectors(
        [city], decision_time=decision, interval_s=0.0, return_stats=True
    )
    assert stats.vectors_written == 2
    assert readback_times == [persisted_readback, persisted_readback]
    for target_date, window_start in (
        (decision.date().isoformat(), decision),
        ((decision.date() + timedelta(days=1)).isoformat(), decision + timedelta(days=1)),
    ):
        assert select_ready_day0_hourly_vectors(
            stored,
            target_date=target_date,
            now=decision,
            expected_models=["icon_d2"],
            require_expected=True,
            remaining_window_start=window_start,
            require_complete_remaining_window=True,
        ) == []


def test_ensemble_fetch_uses_completion_clock_for_strict_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The source-clock 51-member twin obeys the same possession boundary."""
    from src.data.openmeteo_model_updates import OpenMeteoModelUpdate

    city = SimpleNamespace(name="ENS Clock City", timezone="UTC", lat=0.0, lon=0.0)
    decision = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
    fetch_started = decision + timedelta(minutes=1)
    fetch_finished = decision + timedelta(minutes=3)
    clock = iter((fetch_started, fetch_finished))
    monkeypatch.setattr(day0, "_day0_utc_now", lambda: next(clock))
    times = [
        f"{(decision.date() + timedelta(days=offset)).isoformat()}T{hour:02d}:00"
        for offset in (0, 1)
        for hour in range(24)
    ]
    hourly = {"time": times, "temperature_2m": [20.0] * len(times)}
    for index in range(1, 51):
        hourly[f"temperature_2m_member{index:02d}"] = [20.0] * len(times)
    update = OpenMeteoModelUpdate(
        model=day0.DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL,
        last_run_initialisation_time=decision - timedelta(hours=2),
        last_run_availability_time=decision - timedelta(minutes=30),
        last_run_modification_time=decision - timedelta(minutes=25),
    )
    monkeypatch.setattr(
        "src.data.openmeteo_model_updates.fetch_model_updates",
        lambda models, **_kwargs: tuple(update for _model in models),
    )
    monkeypatch.setattr(
        "src.data.openmeteo_client.fetch",
        lambda *_args, **_kwargs: {"hourly": hourly},
    )

    vectors, request_hash = day0.fetch_day0_source_clock_ensemble_vectors(
        city, now=decision
    )
    assert request_hash.startswith("sha256:")
    assert len(vectors) == day0.DAY0_SOURCE_CLOCK_ENSEMBLE_MEMBER_COUNT
    meta = _json.loads(vectors[0].source_run_meta_json or "{}")
    assert vectors[0].captured_at == decision.isoformat()
    assert meta["fetch_started_at"] == fetch_started.isoformat()
    assert meta["fetch_finished_at"] == fetch_finished.isoformat()

    strict = dict(
        target_date=decision.date().isoformat(),
        now=decision,
        expected_models=day0.day0_source_clock_ensemble_member_models(),
        require_expected=True,
        max_bundle_skew_minutes=day0.DAY0_HOURLY_BUNDLE_MAX_SKEW_MINUTES,
        remaining_window_start=decision,
        require_complete_remaining_window=True,
    )
    assert select_ready_day0_hourly_vectors(vectors, **strict) == []
    strict["now"] = fetch_finished
    assert len(select_ready_day0_hourly_vectors(vectors, **strict)) == 51


def test_current_ensemble_bundle_already_persisted_matches_a_single_run_hwm_across_51_members(
    monkeypatch,
) -> None:
    """_current_ensemble_bundle_already_persisted checks every persisted member row
    against ONE probed run_hwm (one provider run backs all 51 ecmwf_ifs025 members),
    ignoring availability replica skew exactly like the deterministic-model check."""
    city = SimpleNamespace(name="Singapore", timezone="Asia/Singapore", lat=1.35, lon=103.99)
    decision_time = datetime(2026, 9, 6, 1, 0, tzinfo=UTC)
    run = datetime(2026, 9, 5, 18, tzinfo=UTC)
    persisted_avail = datetime(2026, 9, 6, 0, 27, 39, tzinfo=UTC)
    hwm_avail = datetime(2026, 9, 6, 0, 54, 11, tzinfo=UTC)  # a different replica
    members = day0.day0_source_clock_ensemble_member_models()

    def fake_read_freshest(**_kwargs):
        return [
            _ensemble_member_vector(city, member, run, persisted_avail, decision_time)
            for member in members
        ]

    monkeypatch.setattr(day0, "read_freshest_day0_hourly_vectors", fake_read_freshest)

    class _FakeConn:
        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "src.state.db.get_forecasts_connection_read_only", lambda: _FakeConn()
    )

    already_persisted = day0._current_ensemble_bundle_already_persisted(
        city="Singapore",
        target_dates=("2026-09-06",),
        run_hwm=Day0ProviderRunHwm(
            model=day0.DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL,
            run_initialisation_time=run,
            run_availability_time=hwm_avail,
        ),
        decision_time=decision_time,
        remaining_window_starts={"2026-09-06": datetime(2026, 9, 6, 0, 0, tzinfo=UTC)},
    )
    assert already_persisted is True, (
        "a 51-member bundle already persisted for the SAME run must not be re-fetched "
        "merely because a different meta.json replica reports a different availability_time"
    )


def test_ensemble_fetch_skips_http_when_current_run_already_persisted(monkeypatch) -> None:
    """QUOTA (round 3 residual): fetch_day0_source_clock_ensemble_vectors was called
    unconditionally on every priority/recovery pass whenever ensemble_target_dates was
    non-empty, re-reserving already-successful keys for a run already fully persisted.
    Two refresh passes for the SAME provider run must issue exactly one ensemble HTTP
    call; a genuinely newer run must issue exactly one more."""
    from src.data.openmeteo_quota import OpenMeteoQuotaTracker

    city = SimpleNamespace(name="Paris", timezone="Europe/Paris", lat=48.8566, lon=2.3522)
    model_det = "ecmwf_ifs"
    decision_time = datetime(2026, 9, 6, 13, 16, 20, tzinfo=UTC)
    target_date = decision_time.astimezone(ZoneInfo(city.timezone)).date().isoformat()
    members = day0.day0_source_clock_ensemble_member_models()

    run_a = datetime(2026, 9, 5, 18, tzinfo=UTC)
    avail_a = datetime(2026, 9, 6, 0, 27, 39, tzinfo=UTC)
    run_b = datetime(2026, 9, 6, 0, tzinfo=UTC)
    avail_b = datetime(2026, 9, 6, 6, 10, 0, tzinfo=UTC)

    current_run = {"init": run_a, "avail": avail_a}
    persisted = {"init": None, "avail": None}
    ensemble_calls = {"n": 0}

    day0._LAST_REFRESH_MONOTONIC.clear()
    day0._DAY0_PROVIDER_RUN_HWM_PIN.clear()
    monkeypatch.setattr(day0, "_day0_provider_run_hwm_pin_persistence_enabled", lambda: False)
    monkeypatch.setattr(day0, "quota_tracker", OpenMeteoQuotaTracker())
    monkeypatch.setattr(day0, "day0_hourly_models_for_city", lambda _city: [model_det])
    monkeypatch.setattr(
        day0, "day0_source_clock_ensemble_target_dates", lambda **_kwargs: (target_date,)
    )
    monkeypatch.setattr(
        day0,
        "fetch_day0_hourly_vectors",
        lambda city_arg, *, models=None, now=None, timeout_s=None: (
            [_det_vector(city_arg, model_det, now)],
            "sha256:det",
        ),
    )

    def fake_probe_hwm(*, decision_time, timeout_s):
        return Day0ProviderRunHwm(
            model=day0.DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL,
            run_initialisation_time=current_run["init"],
            run_availability_time=current_run["avail"],
        )

    monkeypatch.setattr(day0, "_probe_day0_source_clock_ensemble_run_hwm", fake_probe_hwm)

    def fake_fetch_ensemble(city_arg, *, now=None, timeout_s=None):
        ensemble_calls["n"] += 1
        return (
            [
                _ensemble_member_vector(
                    city_arg, member, current_run["init"], current_run["avail"], now
                )
                for member in members
            ],
            f"sha256:ens-{current_run['init'].isoformat()}",
        )

    monkeypatch.setattr(day0, "fetch_day0_source_clock_ensemble_vectors", fake_fetch_ensemble)

    def fake_persist(vectors, *, target_date, request_hash, endpoint=None, **_kwargs):
        if endpoint == day0.OPENMETEO_ENSEMBLE_URL:
            persisted["init"] = current_run["init"]
            persisted["avail"] = current_run["avail"]
        return len(vectors)

    monkeypatch.setattr(day0, "persist_day0_hourly_vectors", fake_persist)

    def fake_read_freshest(**kwargs):
        expected = tuple(kwargs.get("expected_models") or ())
        if expected == (model_det,):
            return [object()]
        if set(expected) == set(members) and persisted["init"] is not None:
            return [
                _ensemble_member_vector(
                    city, member, persisted["init"], persisted["avail"], decision_time
                )
                for member in members
            ]
        return []

    monkeypatch.setattr(day0, "read_freshest_day0_hourly_vectors", fake_read_freshest)

    class _FakeConn:
        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "src.state.db.get_forecasts_connection_read_only", lambda: _FakeConn()
    )

    def _refresh() -> None:
        day0.maybe_refresh_day0_hourly_vectors(
            [city], decision_time=decision_time, interval_s=0.0, quota_priority_cities=1,
        )

    _refresh()
    assert ensemble_calls["n"] == 1, "first pass must fetch the ensemble carrier once"

    _refresh()
    assert ensemble_calls["n"] == 1, (
        "second pass for the SAME run must reuse the persisted bundle, not re-fetch"
    )

    current_run["init"] = run_b
    current_run["avail"] = avail_b
    _refresh()
    assert ensemble_calls["n"] == 2, "a genuinely newer run must fetch exactly once more"


def test_complete_ensemble_bundle_persists_when_deterministic_bundle_is_unavailable(
    monkeypatch,
) -> None:
    """QUOTA (round 6, 2026-09-06): the ENS carrier was persisted only after the
    deterministic bundle passed every completeness gate, so a deterministic
    ``continue`` discarded an already-paid complete 51-member bundle and the same
    ensemble request re-issued on every pass (Los Angeles / Seattle / San Francisco /
    Lucknow: zero member rows ever persisted). A complete ensemble bundle must persist
    on the pass that fetched it, whatever the deterministic bundle did; the next pass
    for the same run must then not fetch the ensemble again."""
    from src.data.openmeteo_quota import OpenMeteoQuotaTracker

    city = SimpleNamespace(
        name="Los Angeles", timezone="America/Los_Angeles", lat=33.94, lon=-118.41
    )
    model_det = "ncep_nbm_conus"
    decision_time = datetime(2026, 9, 6, 6, 25, 0, tzinfo=UTC)
    target_date = decision_time.astimezone(ZoneInfo(city.timezone)).date().isoformat()
    members = day0.day0_source_clock_ensemble_member_models()
    run = datetime(2026, 9, 5, 18, tzinfo=UTC)
    avail = datetime(2026, 9, 6, 1, 8, 57, tzinfo=UTC)

    ensemble_calls = {"n": 0}
    persisted_endpoints: list[tuple[str | None, int]] = []
    persisted = {"done": False}

    day0._LAST_REFRESH_MONOTONIC.clear()
    day0._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.clear()
    day0._INCOMPLETE_RETRY_STREAK.clear()
    day0._DAY0_PROVIDER_RUN_HWM_PIN.clear()
    monkeypatch.setattr(day0, "_day0_provider_run_hwm_pin_persistence_enabled", lambda: False)
    monkeypatch.setattr(day0, "quota_tracker", OpenMeteoQuotaTracker())
    monkeypatch.setattr(day0, "day0_hourly_models_for_city", lambda _city: [model_det])
    monkeypatch.setattr(
        day0, "day0_source_clock_ensemble_target_dates", lambda **_kwargs: (target_date,)
    )
    # Deterministic carrier unavailable on every pass (the live Los Angeles shape:
    # DAY0_HOURLY_BUNDLE_FETCH_UNAVAILABLE missing=ncep_nbm_conus).
    monkeypatch.setattr(
        day0,
        "fetch_day0_hourly_vectors",
        lambda city_arg, *, models=None, now=None, timeout_s=None: ([], ""),
    )
    monkeypatch.setattr(
        day0,
        "_probe_day0_source_clock_ensemble_run_hwm",
        lambda *, decision_time, timeout_s: Day0ProviderRunHwm(
            model=day0.DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL,
            run_initialisation_time=run,
            run_availability_time=avail,
        ),
    )

    def fake_fetch_ensemble(city_arg, *, now=None, timeout_s=None):
        ensemble_calls["n"] += 1
        return (
            [_ensemble_member_vector(city_arg, member, run, avail, now) for member in members],
            "sha256:ens",
        )

    monkeypatch.setattr(day0, "fetch_day0_source_clock_ensemble_vectors", fake_fetch_ensemble)

    def fake_persist(vectors, *, target_date, request_hash, endpoint=None, **_kwargs):
        persisted_endpoints.append((endpoint, len(vectors)))
        if endpoint == day0.OPENMETEO_ENSEMBLE_URL:
            persisted["done"] = True
        return len(vectors)

    monkeypatch.setattr(day0, "persist_day0_hourly_vectors", fake_persist)

    def fake_read_freshest(**kwargs):
        expected = tuple(kwargs.get("expected_models") or ())
        if set(expected) == set(members) and persisted["done"]:
            return [
                _ensemble_member_vector(city, member, run, avail, decision_time)
                for member in members
            ]
        return []

    monkeypatch.setattr(day0, "read_freshest_day0_hourly_vectors", fake_read_freshest)

    class _FakeConn:
        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "src.state.db.get_forecasts_connection_read_only", lambda: _FakeConn()
    )

    def _refresh() -> None:
        day0.maybe_refresh_day0_hourly_vectors(
            [city], decision_time=decision_time, interval_s=0.0, quota_priority_cities=1,
        )

    _refresh()
    assert ensemble_calls["n"] == 1
    assert persisted_endpoints == [(day0.OPENMETEO_ENSEMBLE_URL, len(members))], (
        "a complete ensemble bundle must persist on the pass that fetched it even "
        "though the deterministic bundle was unavailable"
    )

    day0._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.clear()
    day0._INCOMPLETE_RETRY_STREAK.clear()
    _refresh()
    assert ensemble_calls["n"] == 1, (
        "the next pass for the same run must reuse the persisted bundle, not re-fetch"
    )


def test_priority_probe_window_start_is_the_newest_metric_boundary(monkeypatch) -> None:
    """QUOTA (round 7, 2026-09-06): the probe folded the per-city window start to the
    OLDEST metric boundary. Once one metric's boundary aged past the request's
    past_hours (Los Angeles LOW frozen at 16:53 local while HIGH advanced hourly), no
    fetch could cover it, the pass failed REMAINING_WINDOW_INCOMPLETE, both carriers
    were discarded and re-fetched every pass, and the coverable metric went dark. The
    gate's window is the newest boundary; consumers prove their own on read."""
    import sqlite3
    import src.config as config_module
    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.events.reactor as reactor
    import src.state.db as db_module

    city = SimpleNamespace(
        name="Los Angeles", timezone="America/Los_Angeles", lat=33.94, lon=-118.41
    )
    now = datetime(2026, 9, 6, 6, 25, 0, tzinfo=UTC)
    target_date = now.astimezone(ZoneInfo(city.timezone)).date().isoformat()
    newest = now - timedelta(minutes=32)
    oldest = now - timedelta(hours=6, minutes=32)

    class _Conn:
        def close(self) -> None:
            return None

    monkeypatch.setattr(config_module, "runtime_cities_by_name", lambda: {city.name: city})
    monkeypatch.setattr(db_module, "get_world_connection_read_only", lambda: _Conn())
    monkeypatch.setattr(db_module, "get_forecasts_connection_read_only", lambda: _Conn())
    monkeypatch.setattr(
        target_plan,
        "_latest_authorized_day0_fact",
        lambda *_args, temperature_metric, **_kwargs: {
            "observation_time": (
                newest if temperature_metric == "high" else oldest
            ).isoformat()
        },
    )
    monkeypatch.setattr(day0, "day0_hourly_models_for_city", lambda _city: ["ncep_nbm_conus"])
    monkeypatch.setattr(day0, "read_freshest_day0_hourly_vectors", lambda **_kwargs: [])

    probe = reactor._edli_day0_hourly_refresh_due_families(cities=[city], decision_time=now)

    assert probe.proved is True
    assert dict(
        ((c, td), ws) for c, td, ws in probe.window_starts
    ) == {(city.name, target_date): newest}, (
        "the fetch gate's window start must be the newest metric boundary, not the oldest"
    )
    assert probe.refresh_due_families == frozenset(
        {(city.name, target_date, "high"), (city.name, target_date, "low")}
    )
