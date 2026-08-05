# Created: 2026-06-25
# Last reused/audited: 2026-08-05

import json
from datetime import UTC, datetime

from src.data.openmeteo_model_updates import (
    OpenMeteoModelUpdate,
    fetch_model_updates,
    metadata_model_id,
    parse_model_updates_payload,
    read_model_updates_jsonl,
    write_model_updates_jsonl,
)
from src.data.bayes_precision_fusion_capture import OPENMETEO_MODEL_IDS
from src.data.bayes_precision_fusion_download import (
    MODEL_PUBLISH_CYCLE_HOURS,
    SINGLE_RUNS_UNSERVABLE_MODELS,
    source_clock_metadata_run_is_single_runs_served,
)
from src.data.source_clock_update_probe import (
    advance_source_clock_cursor,
    probe_openmeteo_source_clock_updates,
    source_clock_scoped_download_allows_cursor_advance,
    source_clock_scoped_download_cursor_sources,
)
from src.strategy.live_inference.source_clock_vnext import source_publicly_usable_at


def test_parse_model_updates_payload_and_source_clock_wait() -> None:
    updates = parse_model_updates_payload(
        {
            "models": [
                {
                    "model": "ecmwf_ifs",
                    "last_run_initialisation_time": "2026-06-25T06:00:00Z",
                    "last_run_availability_time": "2026-06-25T10:30:00Z",
                    "update_interval_seconds": 21600,
                    "temporal_resolution_seconds": 3600,
                }
            ]
        }
    )

    assert len(updates) == 1
    update = updates[0]
    assert update.model == "ecmwf_ifs"
    assert update.last_run_availability_time == datetime(2026, 6, 25, 10, 30, tzinfo=UTC)
    run = update.to_source_run_clock()
    assert source_publicly_usable_at(run) == datetime(2026, 6, 25, 10, 40, tzinfo=UTC)


def test_parse_mapping_payload_shape() -> None:
    updates = parse_model_updates_payload(
        {
            "kma_ldps": {
                "last_run_initialisation_time": "2026-06-25T12:00:00+00:00",
                "last_run_availability_time": "2026-06-25T13:15:00+00:00",
            }
        }
    )

    assert len(updates) == 1
    assert updates[0].model == "kma_ldps"


def test_model_update_jsonl_round_trip_does_not_recursively_nest_raw(tmp_path) -> None:
    path = tmp_path / "updates.jsonl"
    provider_payload = {
        "last_run_initialisation_time": 1785888000,
        "last_run_availability_time": 1785901728,
        "update_interval_seconds": 21600,
    }
    update = parse_model_updates_payload(
        {"models": [{"model": "icon_global", **provider_payload}]}
    )[0]

    write_model_updates_jsonl(path, [update])
    first = read_model_updates_jsonl(path)[0]
    write_model_updates_jsonl(path, [first])
    second = read_model_updates_jsonl(path)[0]

    assert first.raw == {"model": "icon_global", **provider_payload}
    assert second.raw == first.raw
    assert "raw" not in second.raw


def test_model_update_jsonl_flattens_existing_recursive_raw(tmp_path) -> None:
    path = tmp_path / "updates.jsonl"
    provider_payload = {
        "last_run_initialisation_time": 1785888000,
        "last_run_availability_time": 1785901728,
        "update_interval_seconds": 21600,
    }
    nested = {
        "model": "icon_global",
        **provider_payload,
        "raw": {"model": "icon_global", **provider_payload, "raw": provider_payload},
    }
    path.write_text(json.dumps(nested) + "\n", encoding="utf-8")

    update = read_model_updates_jsonl(path)[0]
    write_model_updates_jsonl(path, [update])
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert update.raw == provider_payload
    assert persisted["raw"] == provider_payload


def test_source_clock_probe_does_not_advance_cursor_before_public_availability(tmp_path) -> None:
    updates_path = tmp_path / "updates.jsonl"
    cursor_path = tmp_path / "cursor.json"
    write_model_updates_jsonl(
        updates_path,
        [
            OpenMeteoModelUpdate(
                model="ecmwf_ifs",
                last_run_initialisation_time=datetime(2099, 1, 1, 0, 0, tzinfo=UTC),
                last_run_availability_time=datetime(2099, 1, 1, 4, 0, tzinfo=UTC),
            )
        ],
    )

    report = probe_openmeteo_source_clock_updates(
        model_updates_path=updates_path,
        cursor_path=cursor_path,
        use_network=False,
    )

    assert report.status == "SOURCE_CLOCK_NO_PUBLICLY_USABLE_CHANGE"
    assert report.updated_sources == ()
    assert not cursor_path.exists()

    write_model_updates_jsonl(
        updates_path,
        [
            OpenMeteoModelUpdate(
                model="ecmwf_ifs",
                last_run_initialisation_time=datetime(2000, 1, 1, 0, 0, tzinfo=UTC),
                last_run_availability_time=datetime(2000, 1, 1, 4, 0, tzinfo=UTC),
            )
        ],
    )

    report = probe_openmeteo_source_clock_updates(
        model_updates_path=updates_path,
        cursor_path=cursor_path,
        use_network=False,
    )

    assert report.status == "SOURCE_CLOCK_UPDATES_CHANGED"
    assert report.updated_sources == ("ecmwf_ifs",)
    assert cursor_path.exists()


def test_source_clock_probe_can_defer_cursor_until_download_success(tmp_path) -> None:
    updates_path = tmp_path / "updates.jsonl"
    cursor_path = tmp_path / "cursor.json"
    write_model_updates_jsonl(
        updates_path,
        [
            OpenMeteoModelUpdate(
                model="ecmwf_ifs",
                last_run_initialisation_time=datetime(2000, 1, 1, 0, 0, tzinfo=UTC),
                last_run_availability_time=datetime(2000, 1, 1, 4, 0, tzinfo=UTC),
            )
        ],
    )

    report = probe_openmeteo_source_clock_updates(
        model_updates_path=updates_path,
        cursor_path=cursor_path,
        use_network=False,
        advance_cursor=False,
    )

    assert report.status == "SOURCE_CLOCK_UPDATES_CHANGED"
    assert report.updated_sources == ("ecmwf_ifs",)
    assert not cursor_path.exists()
    assert not source_clock_scoped_download_allows_cursor_advance(
        {"status": "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE"}
    )
    assert not source_clock_scoped_download_allows_cursor_advance(
        {"status": "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_TIMEBOXED_INCOMPLETE"}
    )
    assert not source_clock_scoped_download_allows_cursor_advance(
        {"status": "SOURCE_CLOCK_BPF_SCOPED_QUOTA_COOLDOWN_SKIPPED"}
    )
    assert source_clock_scoped_download_allows_cursor_advance(
        {"status": "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"}
    )
    assert source_clock_scoped_download_cursor_sources(
        {
            "status": "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE",
            "source_results": {
                "ecmwf_ifs": {
                    "status": "SOURCE_CLOCK_SOURCE_RAW_INPUTS_DOWNLOADED"
                },
                "icon_global": {
                    "status": "SOURCE_CLOCK_SOURCE_TRANSPORT_RETRYABLE"
                },
            },
        }
    ) == ("ecmwf_ifs",)
    assert source_clock_scoped_download_cursor_sources(
        {
            "status": "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE",
            "source_results": {
                "ukmo_uk_deterministic_2km": {
                    "status": "SOURCE_CLOCK_SOURCE_PERMANENT_FAILURE"
                },
                "icon_global": {
                    "status": "SOURCE_CLOCK_SOURCE_TRANSPORT_RETRYABLE"
                },
            },
        }
    ) == ("ukmo_uk_deterministic_2km",)
    assert source_clock_scoped_download_allows_cursor_advance(
        {"status": "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_PERMANENT_FAILURE"}
    )
    frozen = {
        "source_runs": {
            "ukmo_uk_deterministic_2km": {
                "initialisation_time": "2000-01-01T07:00:00+00:00"
            }
        }
    }
    terminal = {
        "source_results": {
            "ukmo_uk_deterministic_2km": {
                "status": "SOURCE_CLOCK_SOURCE_PERMANENT_FAILURE",
                "cycle": "2000-01-01T08:00:00+00:00",
            }
        }
    }
    assert source_clock_scoped_download_cursor_sources(
        terminal,
        source_clock_report=frozen,
    ) == ()
    terminal["source_results"]["ukmo_uk_deterministic_2km"]["cycle"] = (
        "2000-01-01T07:00:00+00:00"
    )
    assert source_clock_scoped_download_cursor_sources(
        terminal,
        source_clock_report=frozen,
    ) == ("ukmo_uk_deterministic_2km",)
    assert source_clock_scoped_download_cursor_sources(
        {
            "status": "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "updated_sources": ["icon_global", "ecmwf_ifs"],
        }
    ) == ("ecmwf_ifs", "icon_global")
    assert advance_source_clock_cursor(report) == ("ecmwf_ifs",)
    assert cursor_path.exists()


def test_source_clock_cursor_replays_current_run_when_city_route_changes(
    tmp_path, monkeypatch
) -> None:
    import src.data.source_clock_update_probe as probe

    updates_path = tmp_path / "updates.jsonl"
    cursor_path = tmp_path / "cursor.json"
    write_model_updates_jsonl(
        updates_path,
        [
            OpenMeteoModelUpdate(
                model="ecmwf_ifs",
                last_run_initialisation_time=datetime(2000, 1, 1, 0, 0, tzinfo=UTC),
                last_run_availability_time=datetime(2000, 1, 1, 4, 0, tzinfo=UTC),
            )
        ],
    )
    routes = [("Paris",)]
    monkeypatch.setattr(
        probe,
        "affected_cities_for_source_updates",
        lambda _sources: routes[0],
    )

    first = probe_openmeteo_source_clock_updates(
        model_updates_path=updates_path,
        cursor_path=cursor_path,
        use_network=False,
    )
    unchanged = probe_openmeteo_source_clock_updates(
        model_updates_path=updates_path,
        cursor_path=cursor_path,
        use_network=False,
    )
    routes[0] = ("Paris", "Seoul")
    expanded = probe_openmeteo_source_clock_updates(
        model_updates_path=updates_path,
        cursor_path=cursor_path,
        use_network=False,
        advance_cursor=False,
    )

    assert first.updated_sources == ("ecmwf_ifs",)
    assert unchanged.updated_sources == ()
    assert expanded.updated_sources == ("ecmwf_ifs",)
    assert expanded.affected_cities == ("Paris", "Seoul")


def test_source_clock_cursor_commits_exact_probe_token_not_newer_metadata(
    tmp_path, monkeypatch
) -> None:
    import src.data.source_clock_update_probe as probe

    updates_path = tmp_path / "updates.jsonl"
    cursor_path = tmp_path / "cursor.json"
    monkeypatch.setattr(probe, "all_configured_source_ids", lambda: ("ecmwf_ifs",))
    monkeypatch.setattr(
        probe,
        "affected_cities_for_source_updates",
        lambda _sources: ("Paris",),
    )
    first = OpenMeteoModelUpdate(
        model="ecmwf_ifs",
        last_run_initialisation_time=datetime(2000, 1, 1, 0, 0, tzinfo=UTC),
        last_run_availability_time=datetime(2000, 1, 1, 4, 0, tzinfo=UTC),
    )
    write_model_updates_jsonl(updates_path, [first])
    report = probe_openmeteo_source_clock_updates(
        model_updates_path=updates_path,
        cursor_path=cursor_path,
        use_network=False,
        advance_cursor=False,
    )

    later = OpenMeteoModelUpdate(
        model="ecmwf_ifs",
        last_run_initialisation_time=datetime(2000, 1, 1, 6, 0, tzinfo=UTC),
        last_run_availability_time=datetime(2000, 1, 1, 10, 0, tzinfo=UTC),
    )
    write_model_updates_jsonl(updates_path, [later])

    assert advance_source_clock_cursor(report) == ("ecmwf_ifs",)
    assert json.loads(cursor_path.read_text())["ecmwf_ifs"] == dict(report.cursor_values)[
        "ecmwf_ifs"
    ]
    replay = probe_openmeteo_source_clock_updates(
        model_updates_path=updates_path,
        cursor_path=cursor_path,
        use_network=False,
        advance_cursor=False,
    )
    assert replay.updated_sources == ("ecmwf_ifs",)


def test_source_clock_cursor_compare_and_set_rejects_changed_preimage(
    tmp_path, monkeypatch
) -> None:
    import src.data.source_clock_update_probe as probe

    updates_path = tmp_path / "updates.jsonl"
    cursor_path = tmp_path / "cursor.json"
    monkeypatch.setattr(probe, "all_configured_source_ids", lambda: ("ecmwf_ifs",))
    monkeypatch.setattr(
        probe,
        "affected_cities_for_source_updates",
        lambda _sources: ("Paris",),
    )
    update = OpenMeteoModelUpdate(
        model="ecmwf_ifs",
        last_run_initialisation_time=datetime(2000, 1, 1, 0, 0, tzinfo=UTC),
        last_run_availability_time=datetime(2000, 1, 1, 4, 0, tzinfo=UTC),
    )
    write_model_updates_jsonl(updates_path, [update])
    report = probe_openmeteo_source_clock_updates(
        model_updates_path=updates_path,
        cursor_path=cursor_path,
        use_network=False,
        advance_cursor=False,
    )
    cursor_path.write_text(json.dumps({"ecmwf_ifs": "newer-token"}), encoding="utf-8")

    assert advance_source_clock_cursor(report) == ()
    assert json.loads(cursor_path.read_text()) == {"ecmwf_ifs": "newer-token"}


def test_source_clock_cursor_allows_newer_terminal_run_after_concurrent_older_commit(
    tmp_path, monkeypatch
) -> None:
    import src.data.source_clock_update_probe as probe

    updates_path = tmp_path / "updates.jsonl"
    cursor_path = tmp_path / "cursor.json"
    monkeypatch.setattr(probe, "all_configured_source_ids", lambda: ("ecmwf_ifs",))
    monkeypatch.setattr(
        probe,
        "affected_cities_for_source_updates",
        lambda _sources: ("Paris",),
    )
    older = OpenMeteoModelUpdate(
        model="ecmwf_ifs",
        last_run_initialisation_time=datetime(2000, 1, 1, 0, 0, tzinfo=UTC),
        last_run_availability_time=datetime(2000, 1, 1, 4, 0, tzinfo=UTC),
    )
    newer = OpenMeteoModelUpdate(
        model="ecmwf_ifs",
        last_run_initialisation_time=datetime(2000, 1, 1, 6, 0, tzinfo=UTC),
        last_run_availability_time=datetime(2000, 1, 1, 10, 0, tzinfo=UTC),
    )
    write_model_updates_jsonl(updates_path, [older])
    older_report = probe_openmeteo_source_clock_updates(
        model_updates_path=updates_path,
        cursor_path=cursor_path,
        use_network=False,
        advance_cursor=False,
    )
    write_model_updates_jsonl(updates_path, [newer])
    newer_report = probe_openmeteo_source_clock_updates(
        model_updates_path=updates_path,
        cursor_path=cursor_path,
        use_network=False,
        advance_cursor=False,
    )

    assert advance_source_clock_cursor(older_report) == ("ecmwf_ifs",)
    assert advance_source_clock_cursor(newer_report) == ("ecmwf_ifs",)
    assert json.loads(cursor_path.read_text())["ecmwf_ifs"] == dict(
        newer_report.cursor_values
    )["ecmwf_ifs"]
    assert advance_source_clock_cursor(older_report) == ()


def test_source_clock_cursor_identity_includes_initialisation_time(
    tmp_path, monkeypatch
) -> None:
    import src.data.source_clock_update_probe as probe

    updates_path = tmp_path / "updates.jsonl"
    cursor_path = tmp_path / "cursor.json"
    monkeypatch.setattr(probe, "all_configured_source_ids", lambda: ("ecmwf_ifs",))
    monkeypatch.setattr(
        probe,
        "affected_cities_for_source_updates",
        lambda _sources: ("Paris",),
    )
    availability = datetime(2000, 1, 1, 10, 0, tzinfo=UTC)
    first = OpenMeteoModelUpdate(
        model="ecmwf_ifs",
        last_run_initialisation_time=datetime(2000, 1, 1, 0, 0, tzinfo=UTC),
        last_run_availability_time=availability,
    )
    write_model_updates_jsonl(updates_path, [first])
    initial = probe_openmeteo_source_clock_updates(
        model_updates_path=updates_path,
        cursor_path=cursor_path,
        use_network=False,
    )
    assert initial.updated_sources == ("ecmwf_ifs",)

    corrected = OpenMeteoModelUpdate(
        model="ecmwf_ifs",
        last_run_initialisation_time=datetime(2000, 1, 1, 6, 0, tzinfo=UTC),
        last_run_availability_time=availability,
    )
    write_model_updates_jsonl(updates_path, [corrected])
    changed = probe_openmeteo_source_clock_updates(
        model_updates_path=updates_path,
        cursor_path=cursor_path,
        use_network=False,
        advance_cursor=False,
    )
    assert changed.updated_sources == ("ecmwf_ifs",)


def test_source_clock_probe_admits_nbm_hourly_run_for_standard_fallback(tmp_path) -> None:
    updates_path = tmp_path / "updates.jsonl"
    cursor_path = tmp_path / "cursor.json"
    write_model_updates_jsonl(
        updates_path,
        [
            OpenMeteoModelUpdate(
                model="ncep_nbm_conus",
                last_run_initialisation_time=datetime(2000, 1, 1, 5, 0, tzinfo=UTC),
                last_run_availability_time=datetime(2000, 1, 1, 6, 7, tzinfo=UTC),
            )
        ],
    )

    report = probe_openmeteo_source_clock_updates(
        model_updates_path=updates_path,
        cursor_path=cursor_path,
        use_network=False,
        advance_cursor=False,
    )

    assert report.status == "SOURCE_CLOCK_UPDATES_CHANGED"
    assert report.updated_sources == ("ncep_nbm_conus",)
    assert not cursor_path.exists()
    # NBM is hourly. Its off-grid runs use the metadata-stamped standard API
    # fallback when Single Runs has not archived the declared run yet.
    assert source_clock_metadata_run_is_single_runs_served("ncep_nbm_conus", 5)
    assert source_clock_metadata_run_is_single_runs_served("ncep_nbm_conus", 6)
    assert not source_clock_metadata_run_is_single_runs_served("gfs_hrrr", 1)
    assert source_clock_metadata_run_is_single_runs_served("gfs_hrrr", 3)
    assert not source_clock_metadata_run_is_single_runs_served("met_nordic", 2)
    assert source_clock_metadata_run_is_single_runs_served("met_nordic", 3)


def test_source_clock_openmeteo_model_ids_match_api_parameters() -> None:
    assert OPENMETEO_MODEL_IDS["dmi_harmonie_europe"] == "dmi_harmonie_arome_europe"
    assert OPENMETEO_MODEL_IDS["knmi_harmonie_netherlands"] == "knmi_harmonie_arome_netherlands"
    assert OPENMETEO_MODEL_IDS["met_nordic"] == "metno_nordic"
    assert OPENMETEO_MODEL_IDS["nam_conus"] == "ncep_nam_conus"
    assert OPENMETEO_MODEL_IDS["italiameteo_icon_2i"] == "italia_meteo_arpae_icon_2i"
    assert "kma_gdps" in SINGLE_RUNS_UNSERVABLE_MODELS
    assert "kma_ldps" in SINGLE_RUNS_UNSERVABLE_MODELS
    assert MODEL_PUBLISH_CYCLE_HOURS["italiameteo_icon_2i"] == frozenset({0, 12})


def test_fetch_model_updates_uses_static_metadata_urls() -> None:
    class _Response:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200
            self.content = b"{}"

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self._payload

    class _Session:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def get(self, url, params, timeout):
            assert params == {}
            self.urls.append(url)
            return _Response(
                {
                    "last_run_initialisation_time": 1782367200,
                    "last_run_availability_time": 1782381600,
                    "update_interval_seconds": 21600,
                    "temporal_resolution_seconds": 3600,
                }
            )

    session = _Session()

    updates = fetch_model_updates(
        ["icon_global", "met_nordic"],
        endpoint_url="https://api.open-meteo.com/data/{model}/static/meta.json",
        session=session,
    )

    assert session.urls == [
        "https://api.open-meteo.com/data/dwd_icon/static/meta.json",
        "https://api.open-meteo.com/data/metno_nordic_pp/static/meta.json",
    ]
    assert [update.model for update in updates] == ["icon_global", "met_nordic"]
    assert metadata_model_id("gem_hrdps_continental") == "cmc_gem_hrdps"


def test_fetch_model_updates_uses_shared_quota_client_in_production(monkeypatch) -> None:
    import src.data.openmeteo_model_updates as model_updates

    calls = []

    def tracked_fetch(url, params, **kwargs):
        calls.append((url, params, kwargs, model_updates.quota_tracker._is_priority()))
        return {
            "last_run_initialisation_time": 1782367200,
            "last_run_availability_time": 1782381600,
            "update_interval_seconds": 21600,
        }

    monkeypatch.setattr(model_updates, "_fetch_openmeteo", tracked_fetch)
    updates = fetch_model_updates(["icon_global"], max_workers=1, priority=True)

    assert [update.model for update in updates] == ["icon_global"]
    assert calls[0][0].endswith("/data/dwd_icon/static/meta.json")
    assert calls[0][1] == {}
    assert calls[0][2]["endpoint_label"] == "source_clock_model_meta_icon_global"
    assert calls[0][3] is True


def test_fetch_model_updates_aggregate_endpoint_is_quota_tracked(monkeypatch) -> None:
    import src.data.openmeteo_model_updates as model_updates

    calls = []

    def tracked_fetch(url, params, **kwargs):
        calls.append((url, params, kwargs, model_updates.quota_tracker._is_priority()))
        return {
            "models": [
                {
                    "model": "icon_global",
                    "last_run_initialisation_time": 1782367200,
                    "last_run_availability_time": 1782381600,
                }
            ]
        }

    monkeypatch.setattr(model_updates, "_fetch_openmeteo", tracked_fetch)
    updates = fetch_model_updates(
        ["icon_global"],
        endpoint_url="https://metadata.example.test/updates",
        priority=True,
    )

    assert [update.model for update in updates] == ["icon_global"]
    assert calls == [
        (
            "https://metadata.example.test/updates?models=icon_global",
            {},
            {
                "timeout": 30.0,
                "max_retries": 1,
                "endpoint_label": "source_clock_model_meta_batch",
                "client": None,
            },
            True,
        )
    ]


def test_source_clock_metadata_poll_backs_off_without_missing_off_cycle_update(
    tmp_path, monkeypatch
) -> None:
    import src.data.source_clock_update_probe as probe

    updates_path = tmp_path / "updates.jsonl"
    cursor_path = tmp_path / "cursor.json"
    cached = (
        OpenMeteoModelUpdate(
            model="ecmwf_ifs",
            last_run_initialisation_time=datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
            last_run_availability_time=datetime(2026, 8, 4, 12, 30, tzinfo=UTC),
            update_interval_seconds=3600,
        ),
        OpenMeteoModelUpdate(
            model="icon_global",
            last_run_initialisation_time=datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
            last_run_availability_time=datetime(2026, 8, 4, 12, 30, tzinfo=UTC),
            update_interval_seconds=3600,
        ),
    )
    write_model_updates_jsonl(updates_path, cached)
    monkeypatch.setattr(
        probe,
        "all_configured_source_ids",
        lambda: ("ecmwf_ifs", "icon_global"),
    )
    calls = []

    changed = {"value": False}

    def fetch(models, **_kwargs):
        calls.append(tuple(models))
        if changed["value"]:
            return tuple(
                OpenMeteoModelUpdate(
                    model=update.model,
                    last_run_initialisation_time=datetime(
                        2026, 8, 4, 12, 30, tzinfo=UTC
                    ),
                    last_run_availability_time=datetime(
                        2026, 8, 4, 12, 44, tzinfo=UTC
                    ),
                    update_interval_seconds=3600,
                )
                for update in cached
                if update.model in models
            )
        return tuple(update for update in cached if update.model in models)

    monkeypatch.setattr(probe, "fetch_model_updates", fetch)
    namespace = str(updates_path.resolve())
    for model in ("ecmwf_ifs", "icon_global"):
        probe._MODEL_UPDATE_NEXT_POLL_MONOTONIC.pop((namespace, model), None)
        probe._MODEL_UPDATE_UNCHANGED_STREAK.pop((namespace, model), None)

    probe_openmeteo_source_clock_updates(
        model_updates_path=updates_path,
        cursor_path=cursor_path,
        advance_cursor=False,
        decision_time=datetime(2026, 8, 4, 12, 30, tzinfo=UTC),
        now_monotonic=100.0,
    )
    assert calls == [("ecmwf_ifs", "icon_global")]

    deferred = probe_openmeteo_source_clock_updates(
        model_updates_path=updates_path,
        cursor_path=cursor_path,
        advance_cursor=False,
        decision_time=datetime(2026, 8, 4, 12, 30, 14, tzinfo=UTC),
        now_monotonic=114.0,
    )
    assert len(calls) == 1
    assert deferred.status == "SOURCE_CLOCK_POLL_DEFERRED_BACKOFF"

    changed["value"] = True
    probe_openmeteo_source_clock_updates(
        model_updates_path=updates_path,
        cursor_path=cursor_path,
        advance_cursor=False,
        decision_time=datetime(2026, 8, 4, 12, 30, 15, tzinfo=UTC),
        now_monotonic=115.0,
    )
    assert len(calls) == 2
    assert {
        update.last_run_initialisation_time
        for update in probe.read_model_updates_jsonl(updates_path)
    } == {datetime(2026, 8, 4, 12, 30, tzinfo=UTC)}
