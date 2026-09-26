# Created: 2026-09-26
# Last audited: 2026-09-26
# Authority basis: docs/operations/current/plans/openmeteo_refetch_guard_2026-09-25.md
"""Antibodies for the Open-Meteo refetch law, enforced at ``openmeteo_client.fetch``.

LAW: a metered request is sent only when its answer can differ from one already held.
Each test drives the real ``fetch()`` against a fake provider (meta.json + data
endpoints) and counts the metered sends the provider actually receives.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
import pytest

import src.data.openmeteo_client as om
import src.data.openmeteo_quota as om_quota
import src.data.openmeteo_response_store as om_store
from src.data.bayes_precision_fusion_capture import OPENMETEO_MODEL_IDS
from src.data.openmeteo_model_updates import OPENMETEO_MODEL_METADATA_IDS
from src.data.openmeteo_quota import OpenMeteoQuotaTracker
from src.data.openmeteo_response_store import OpenMeteoResponseStore

SINGLE_RUNS = "https://single-runs-api.open-meteo.com/v1/forecast"
PREVIOUS_RUNS = "https://previous-runs-api.open-meteo.com/v1/forecast"
RUN = datetime(2026, 9, 25, 18, tzinfo=timezone.utc)
RUN_EPOCH = int(RUN.timestamp())


def _utc(hours_after_run: float) -> int:
    return int(RUN_EPOCH + hours_after_run * 3600)


class Provider:
    """A fake Open-Meteo: per-slug meta.json state and a data answer per call."""

    def __init__(self) -> None:
        self.meta = {
            "dwd_icon": [_utc(0), _utc(4), _utc(4.1)],
            "dwd_icon_eu": [_utc(0), _utc(3), _utc(3.2)],
        }
        self.data_calls = 0
        self.meta_calls = 0
        self.unpublished = False
        self.release: threading.Event | None = None
        self.started = threading.Event()
        self.lock = threading.Lock()

    def get(self, url, *, params=None, timeout=None):  # noqa: ARG002
        request = httpx.Request("GET", url)
        parts = urlsplit(url)
        if parts.path.endswith("/static/meta.json"):
            slug = parts.path.split("/")[2]
            init, modification, availability = self.meta[slug]
            with self.lock:
                self.meta_calls += 1
            return httpx.Response(
                200,
                json={
                    "last_run_initialisation_time": init,
                    "last_run_modification_time": modification,
                    "last_run_availability_time": availability,
                },
                request=request,
            )
        with self.lock:
            self.data_calls += 1
            call = self.data_calls
        self.started.set()
        if self.release is not None:
            assert self.release.wait(5.0)
        if self.unpublished:
            return httpx.Response(
                400,
                json={"error": True, "reason": "The requested model run is not available."},
                request=request,
            )
        # The Madrid/Tel Aviv/Warsaw repro: a successful answer whose target-day
        # tail is null (past the run's current horizon) and therefore writes 0 rows.
        return httpx.Response(
            200,
            json={"hourly": {"time": ["2026-09-27T00:00"], "temperature_2m": [None]}, "call": call},
            request=request,
        )


@pytest.fixture()
def world(monkeypatch, tmp_path):
    """Shared quota file + shared answer store, as two daemons on one host see them."""

    # pytest re-sets PYTEST_CURRENT_TEST per phase; share the quota file explicitly.
    monkeypatch.setattr(
        OpenMeteoQuotaTracker, "_shared_enabled", lambda self: self._state_path is not None
    )
    monkeypatch.setattr(om, "IN_FLIGHT_POLL_SECONDS", 0.01)
    provider = Provider()
    quota_path = tmp_path / "openmeteo_quota.json"
    store_path = tmp_path / "openmeteo_response_store.db"
    now = {"t": float(_utc(5))}
    monkeypatch.setattr(om_store.time, "time", lambda: now["t"])

    class World:
        def __init__(self) -> None:
            self.provider = provider
            self.now = now

        def process(self):
            """A fresh daemon: its own tracker and store objects on the shared files."""

            return OpenMeteoQuotaTracker(state_path=quota_path), OpenMeteoResponseStore(store_path)

        def fetch(self, proc, url=SINGLE_RUNS, params=None, **kwargs):
            tracker, store = proc
            return om.fetch(
                url,
                dict(params or _madrid()),
                max_retries=1,
                quota=tracker,
                client=provider,
                store=store,
                endpoint_label=kwargs.pop("label", "bayes_precision_fusion_single_runs_batched"),
                **kwargs,
            )

    return World()


def _madrid(**extra) -> dict:
    params = {
        "latitude": 40.4719,
        "longitude": -3.5626,
        "hourly": "temperature_2m",
        "models": "icon_global,icon_eu",
        "run": RUN.strftime("%Y-%m-%dT%H:%M"),
        "forecast_hours": 120,
        "temperature_unit": "celsius",
        "timezone": "Europe/Madrid",
    }
    params.update(extra)
    return params


def test_a_same_exact_run_request_twice_meters_one_send(world) -> None:
    proc = world.process()
    first = world.fetch(proc)
    second = world.fetch(proc)

    assert world.provider.data_calls == 1
    assert second == first
    assert proc[0].calls_today() == 1


def test_b_repeat_after_restart_meters_nothing(world) -> None:
    world.fetch(world.process())
    world.now["t"] += 30.0

    restarted = world.process()
    world.fetch(restarted)

    assert world.provider.data_calls == 1
    assert restarted[0].calls_today() == 1


def test_c_modification_advance_allows_one_reissue(world) -> None:
    proc = world.process()
    world.fetch(proc)
    world.provider.meta["dwd_icon_eu"] = [_utc(0), _utc(5.5), _utc(5.6)]
    world.now["t"] = float(_utc(6))

    world.fetch(proc)
    world.fetch(proc)

    assert world.provider.data_calls == 2


def test_d_gap_on_latest_run_with_unchanged_stamp_is_never_reissued(world) -> None:
    """The 09-26 00Z repro: 6 actionable targets re-requested every 15 s, 0 rows."""

    proc = world.process()
    for poll in range(240):  # one hour of 15 s source-clock polls
        world.now["t"] = float(_utc(5)) + 15.0 * poll
        payload = world.fetch(proc)
        assert payload["hourly"]["temperature_2m"] == [None]

    assert world.provider.data_calls == 1
    assert proc[0].calls_today() == 1
    burn = proc[1].burn(world.now["t"])
    assert burn["metered"] == 1 and burn["served"] == 239


def test_d_superseded_run_is_served_without_rereading_meta(world) -> None:
    proc = world.process()
    world.fetch(proc)
    world.provider.meta["dwd_icon"] = [_utc(6), _utc(10), _utc(10.1)]
    world.provider.meta["dwd_icon_eu"] = [_utc(3), _utc(6), _utc(6.1)]
    world.now["t"] = float(_utc(11))
    world.fetch(proc)  # reads the new runs: both models superseded
    meta_calls = world.provider.meta_calls
    world.now["t"] = float(_utc(30))

    world.fetch(proc)

    assert world.provider.data_calls == 1
    assert world.provider.meta_calls == meta_calls


def test_e_two_concurrent_callers_meter_one_send(world) -> None:
    world.provider.release = threading.Event()
    first, second = world.process(), world.process()
    results: list[dict] = []
    worker = threading.Thread(target=lambda: results.append(world.fetch(first)))
    worker.start()
    assert world.provider.started.wait(5.0)

    waiter = threading.Thread(target=lambda: results.append(world.fetch(second)))
    waiter.start()
    time.sleep(0.05)
    world.provider.release.set()
    worker.join(5.0)
    waiter.join(5.0)

    assert world.provider.data_calls == 1
    assert len(results) == 2 and results[0] == results[1]
    assert first[0].calls_today() == 1


def test_d_off_grid_run_refusal_is_held_like_any_other_answer(world) -> None:
    """knmi 02Z, 09-26: the same "run not available" 400 re-requested every poll."""

    world.provider.unpublished = True
    proc = world.process()
    for poll in range(40):
        world.now["t"] = float(_utc(5)) + 15.0 * poll
        with pytest.raises(om.OpenMeteoHTTPStatusError) as caught:
            world.fetch(proc, conditional_status_codes=frozenset({400}))
        assert caught.value.outcome.reason == "run_not_published"
        assert caught.value.outcome.retry_class is om.OpenMeteoRetryClass.CONDITIONAL

    assert world.provider.data_calls == 1


def test_untyped_client_error_is_never_held(world, monkeypatch) -> None:
    """Only the provider's typed run refusal is an answer; any other 4xx is not held."""

    def bad_request(url, *, params=None, timeout=None):  # noqa: ARG001
        world.provider.data_calls += 1
        return httpx.Response(400, json={"error": True, "reason": "Parameter x invalid"},
                              request=httpx.Request("GET", url))

    proc = world.process()
    monkeypatch.setattr(world.provider, "get", lambda url, **kw: (
        Provider.get(world.provider, url, **kw) if url.endswith("meta.json") else bad_request(url, **kw)
    ))
    for _ in range(3):
        with pytest.raises(om.OpenMeteoHTTPStatusError):
            world.fetch(proc, conditional_status_codes=frozenset({400}))
        proc[0]._shared(lambda state, _now: (state["requests"].clear(), True))

    assert world.provider.data_calls == 3
    assert proc[1]._db().execute("SELECT COUNT(*) FROM responses").fetchone()[0] == 0


def test_unpublished_future_run_refusal_is_never_held(world) -> None:
    world.provider.unpublished = True
    world.provider.meta["dwd_icon"] = [_utc(-6), _utc(-2), _utc(-1.9)]
    world.provider.meta["dwd_icon_eu"] = [_utc(-3), _utc(-1), _utc(-0.9)]
    proc = world.process()
    for _ in range(3):
        with pytest.raises(om.OpenMeteoHTTPStatusError):
            world.fetch(proc)
        world.now["t"] += 30.0
        proc[0]._shared(lambda state, _now: (state["requests"].clear(), True))

    assert world.provider.data_calls == 3


def test_previous_runs_answer_follows_every_models_latest_run(world) -> None:
    proc = world.process()
    params = {
        "latitude": 40.4719,
        "longitude": -3.5626,
        "start_date": "2026-09-27",
        "end_date": "2026-09-27",
        "hourly": "temperature_2m_previous_day1",
        "models": "icon_global,icon_eu",
        "timezone": "Europe/Madrid",
    }
    world.fetch(proc, PREVIOUS_RUNS, params)
    world.fetch(proc, PREVIOUS_RUNS, params)
    assert world.provider.data_calls == 1

    world.provider.meta["dwd_icon_eu"] = [_utc(3), _utc(6), _utc(6.1)]
    world.now["t"] = float(_utc(7))
    world.fetch(proc, PREVIOUS_RUNS, params)
    assert world.provider.data_calls == 2


def test_unpropagated_or_unconfirmed_state_is_never_proof(world) -> None:
    proc = world.process()
    # Modified after its last availability: the run is still being written.
    world.provider.meta["dwd_icon_eu"] = [_utc(0), _utc(4.9), _utc(3.2)]
    world.fetch(proc)
    world.fetch(proc)
    assert world.provider.data_calls == 2


def test_non_versioned_requests_take_the_network_path(world) -> None:
    proc = world.process()
    standard = "https://api.open-meteo.com/v1/forecast"
    params = {"latitude": 1, "longitude": 2, "hourly": "temperature_2m", "models": "icon_global"}
    world.fetch(proc, standard, params)
    world.fetch(proc, standard, params)
    legacy = _madrid(models="best_match")
    world.fetch(proc, SINGLE_RUNS, legacy)
    world.fetch(proc, SINGLE_RUNS, legacy)
    assert world.provider.data_calls == 4


def test_replica_lag_never_regresses_the_pinned_run(world) -> None:
    proc = world.process()
    world.fetch(proc)
    lagging = [_utc(-6), _utc(-2), _utc(-1.9)]
    proc[1].record_meta("dwd_icon_eu", {
        "last_run_initialisation_time": lagging[0],
        "last_run_modification_time": lagging[1],
        "last_run_availability_time": lagging[2],
    })
    world.now["t"] += 20.0
    world.fetch(proc)
    assert world.provider.data_calls == 1
    assert proc[1].run_state("dwd_icon_eu").init == _utc(0)


def test_replica_lag_never_regresses_a_runs_modification(world) -> None:
    proc = world.process()
    world.fetch(proc)
    init, modification, availability = world.provider.meta["dwd_icon_eu"]
    proc[1].record_meta("dwd_icon_eu", {
        "last_run_initialisation_time": init,
        "last_run_modification_time": modification - 600,
        "last_run_availability_time": availability - 600,
    })
    world.now["t"] += 20.0

    world.fetch(proc)

    assert world.provider.data_calls == 1
    assert proc[1].run_state("dwd_icon_eu").modification == modification


def test_lagging_replica_cannot_vouch_that_a_latest_run_is_unchanged(world) -> None:
    proc = world.process()
    world.fetch(proc)
    current = list(world.provider.meta["dwd_icon_eu"])
    world.provider.meta["dwd_icon_eu"] = [current[0], current[1] - 600, current[2] - 600]
    world.now["t"] += 90.0  # past META_FRESH_SECONDS: only the lagging replica answers

    world.fetch(proc)
    assert world.provider.data_calls == 2  # unprovable, so paid

    world.provider.meta["dwd_icon_eu"] = current
    world.now["t"] += 90.0
    world.fetch(proc)
    world.fetch(proc)
    assert world.provider.data_calls == 2  # a current replica confirms the held answer


def test_f_alarm_names_the_looping_job_within_the_hour(world, caplog) -> None:
    """A new instance of the class surfaces within an hour, naming its job."""

    _tracker, store = world.process()
    caplog.set_level(logging.WARNING, logger=om_store.__name__)
    looping = "bayes_precision_fusion_single_runs_locations_batched"
    start = float(_utc(8))  # 02:00Z: the day still has 22 hours to burn
    fired_at = None
    for minute in range(60):
        world.now["t"] = start + 60.0 * minute
        store.note_metered("same-identity", looping, 25, now=world.now["t"])
        store.note_success("same-identity", now=world.now["t"])
        store.note_metered(f"archive-{minute}", "archive_hourly", 1, now=world.now["t"])
        if fired_at is None and any("burn alarm" in r.getMessage() for r in caplog.records):
            fired_at = minute

    alarms = [r.getMessage() for r in caplog.records if "burn alarm" in r.getMessage()]
    assert len(alarms) == 1  # once per hour, across every process sharing the store
    assert fired_at is not None and fired_at < 60
    assert alarms[0].split("top3=[", 1)[1].startswith(f"{looping} rate_1h=")
    assert "reissued_same_day=" in alarms[0]
    burn = store.burn(world.now["t"])
    assert burn["jobs"][0][0] == looping
    assert burn["jobs"][0][3] == 25 * 59  # every send after the first was a same-day repeat


def test_f_alarm_silent_under_budget(world, caplog) -> None:
    _tracker, store = world.process()
    caplog.set_level(logging.WARNING, logger=om_store.__name__)
    start = float(_utc(8))
    for minute in range(60):
        world.now["t"] = start + 60.0 * minute
        store.note_metered(f"id-{minute}", "archive_hourly", 5, now=world.now["t"])
    assert store.burn(world.now["t"])["projected"] < om_store.ALARM_DAILY_LIMIT
    assert not [r for r in caplog.records if "burn alarm" in r.getMessage()]


def test_store_retention_evicts_unreachable_answers(world) -> None:
    proc = world.process()
    world.fetch(proc)
    store = proc[1]
    store._last_evicted = 0.0
    far = float(_utc(120 + 49))
    store._evict(far)
    assert store._db().execute("SELECT COUNT(*) FROM responses").fetchone()[0] == 0


def test_meta_slugs_cover_every_single_runs_model_id() -> None:
    for model, api_id in OPENMETEO_MODEL_IDS.items():
        assert api_id in om_store.META_SLUGS, api_id
        slug = OPENMETEO_MODEL_METADATA_IDS.get(model, model)
        assert om_store.META_SLUGS[api_id] == slug, (model, api_id, slug)


def test_store_is_disabled_inside_test_processes() -> None:
    assert om.response_store is None
    assert om_store.runtime_response_store() is None


def test_quota_in_flight_read_is_quiet(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    tracker = OpenMeteoQuotaTracker(state_path=tmp_path / "q.json")
    allowed, _reason, _lease = tracker.acquire_request("rid", endpoint="e", job="j")
    assert allowed
    assert tracker.request_in_flight("rid") is True
    assert tracker.request_in_flight("other") is False
    assert om_quota.REQUEST_STATE_SCHEMA_VERSION == 2
