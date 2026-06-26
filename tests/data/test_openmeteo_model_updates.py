# Created: 2026-06-25
# Last reused/audited: 2026-06-25

from datetime import UTC, datetime

from src.data.openmeteo_model_updates import (
    OpenMeteoModelUpdate,
    fetch_model_updates,
    metadata_model_id,
    parse_model_updates_payload,
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
    assert source_clock_scoped_download_allows_cursor_advance(
        {"status": "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"}
    )
    assert advance_source_clock_cursor(report) == ("ecmwf_ifs",)
    assert cursor_path.exists()


def test_source_clock_probe_filters_nbm_metadata_runs_not_served_by_single_runs(tmp_path) -> None:
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

    assert report.status == "SOURCE_CLOCK_NO_PUBLICLY_USABLE_CHANGE"
    assert report.updated_sources == ()
    assert not cursor_path.exists()
    assert not source_clock_metadata_run_is_single_runs_served("ncep_nbm_conus", 5)
    assert source_clock_metadata_run_is_single_runs_served("ncep_nbm_conus", 6)
    assert source_clock_metadata_run_is_single_runs_served("gfs_hrrr", 5)


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

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self._payload

    class _Session:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def get(self, url, timeout):
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
