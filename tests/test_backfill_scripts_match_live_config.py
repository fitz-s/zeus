# Created: 2026-04-21
# Lifecycle: created=2026-04-21; last_reviewed=2026-10-04; last_reused=2026-10-04
# Purpose: Keep backfill scripts aligned with live config and obs_v2 provenance identity contracts.
# Reuse: Inspect config/cities.json, tier_resolver, script manifest, and current source-validity posture first.
# Last reused/audited: 2026-10-04
# Authority basis: plan v3 antibody A7; P1 obs_v2 provenance identity packet.
"""Antibody A7: backfill scripts must match the live config.

Phase -1 (commit d9c998f) removed 4 stale entries whose tier no longer
matched cities.json after the 2026-04-15 Tel Aviv (wu_icao→noaa) and
Taipei (cwa_station→wu_icao) migrations. This antibody prevents that
class of drift from reappearing: a city can only live in a backfill
script's map if its settlement_source_type agrees.

The tests import the backfill scripts as modules (no subprocess, no
HTTP) and compare their hard-coded maps against ``cities_by_name``
filtered by ``settlement_source_type``.

If these tests fail after a cities.json edit, the fix is NOT to relax
the assertion — it is to update the backfill script to match the new
source-of-truth. Failure = migration-not-completed; assertion relaxation
re-opens the exact DRIFT category Phase -1 closed.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config import cities_by_name
from src.data.hole_scanner import _source_applies_to_city
from src.data.wu_hourly_client import HourlyObservation
from src.state.schema.v2_schema import apply_canonical_schema


REPO_ROOT = Path(__file__).resolve().parent.parent
WU_BACKFILL_PATH = REPO_ROOT / "scripts" / "backfill_wu_daily_all.py"
OGIMET_BACKFILL_PATH = REPO_ROOT / "scripts" / "backfill_ogimet_metar.py"
OBS_V2_BACKFILL_PATH = REPO_ROOT / "scripts" / "backfill_obs.py"
HKO_DAILY_BACKFILL_PATH = REPO_ROOT / "scripts" / "backfill_hko_daily.py"
OBS_V2_DST_GAP_FILL_PATH = REPO_ROOT / "scripts" / "fill_obs_dst_gaps.py"
HKO_INGEST_TICK_PATH = REPO_ROOT / "scripts" / "hko_ingest_tick.py"
OBS_V2_PRODUCER_PATHS = [
    OBS_V2_BACKFILL_PATH,
    OBS_V2_DST_GAP_FILL_PATH,
    HKO_INGEST_TICK_PATH,
]
COMPLETENESS_GUARDED_BACKFILL_PATHS = [
    OBS_V2_BACKFILL_PATH,
    WU_BACKFILL_PATH,
    HKO_DAILY_BACKFILL_PATH,
    OGIMET_BACKFILL_PATH,
]


def _load_module_by_path(path: Path, name: str):
    """Load a script module without adding it to sys.modules permanently.

    Scripts in ``scripts/`` are not a package; use spec loader.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"failed to load spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    # Register transiently so relative imports inside the script work.
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
        return mod
    finally:
        # Leave the module registered so subsequent tests can reuse;
        # pytest isolates tests enough that this is safe.
        pass


@pytest.fixture(scope="module")
def wu_backfill_module():
    return _load_module_by_path(WU_BACKFILL_PATH, "zeus_backfill_wu_daily_all")


@pytest.fixture(scope="module")
def ogimet_backfill_module():
    return _load_module_by_path(OGIMET_BACKFILL_PATH, "zeus_backfill_ogimet_metar")


@pytest.fixture(scope="module")
def obs_v2_backfill_module():
    return _load_module_by_path(OBS_V2_BACKFILL_PATH, "zeus_backfill_obs_identity")


@pytest.fixture(scope="module")
def hko_ingest_tick_module():
    return _load_module_by_path(HKO_INGEST_TICK_PATH, "zeus_hko_ingest_tick_identity")


@pytest.fixture(scope="module")
def obs_v2_dst_gap_fill_module():
    return _load_module_by_path(
        OBS_V2_DST_GAP_FILL_PATH,
        "zeus_fill_obs_dst_gaps_identity",
    )


def _hourly_observation(
    *,
    city: str,
    station_id: str,
    target_date: str = "2026-04-23",
) -> SimpleNamespace:
    return SimpleNamespace(
        city=city,
        target_date=target_date,
        local_hour=8.0,
        local_timestamp=f"{target_date}T08:00:00-05:00",
        utc_timestamp=f"{target_date}T13:00:00+00:00",
        utc_offset_minutes=-300,
        dst_active=1,
        is_ambiguous_local_hour=0,
        is_missing_local_hour=0,
        time_basis="utc_hour_bucket_extremum",
        hour_max_temp=71.0,
        hour_min_temp=69.0,
        hour_max_raw_ts=f"{target_date}T13:45:00+00:00",
        hour_min_raw_ts=f"{target_date}T13:05:00+00:00",
        temp_unit="F",
        station_id=station_id,
        observation_count=4,
    )


# ----------------------------------------------------------------------
# A7: WU backfill map matches sstype=='wu_icao' city set
# ----------------------------------------------------------------------


def test_wu_backfill_city_stations_matches_wu_history_cities(wu_backfill_module):
    """WU backfill includes current WU cities and dated WU predecessors."""
    live_wu_icao = {
        c.name
        for c in cities_by_name.values()
        if c.settlement_source_type == "wu_icao"
        or c.previous_settlement_source_type == "wu_icao"
    }
    backfill_keys = set(wu_backfill_module.CITY_STATIONS.keys())
    missing_in_backfill = live_wu_icao - backfill_keys
    extra_in_backfill = backfill_keys - live_wu_icao
    assert not missing_in_backfill, (
        f"cities.json has current/historical wu_icao cities not in "
        f"backfill_wu_daily_all.CITY_STATIONS: {sorted(missing_in_backfill)}. "
        "The WU backfill will skip these cities."
    )
    assert not extra_in_backfill, (
        f"backfill_wu_daily_all.CITY_STATIONS has cities not in "
        f"cities.json current/historical WU set: {sorted(extra_in_backfill)}. "
        "This is the DRIFT pattern Phase -1 (commit d9c998f) closed."
    )


def test_wu_backfill_icao_matches_cities_json(wu_backfill_module):
    """For every wu_icao city, CITY_STATIONS[name][0] == city.wu_station."""
    mismatches: list[tuple[str, str, str]] = []
    for city in cities_by_name.values():
        if city.settlement_source_type != "wu_icao":
            continue
        entry = wu_backfill_module.CITY_STATIONS.get(city.name)
        if entry is None:
            continue  # covered by the keys test above
        icao_in_backfill = entry[0]
        if icao_in_backfill != city.wu_station:
            mismatches.append((city.name, city.wu_station, icao_in_backfill))
    assert not mismatches, (
        "ICAO drift between cities.json and backfill_wu_daily_all:\n"
        + "\n".join(f"  {n}: cities.json={j!r}, backfill={b!r}" for n, j, b in mismatches)
    )


# ----------------------------------------------------------------------
# A7: Ogimet backfill map matches sstype=='noaa' city set
# ----------------------------------------------------------------------


def test_ogimet_backfill_targets_matches_noaa_cities(ogimet_backfill_module):
    """OGIMET_TARGETS keys == set of NOAA cities from cities.json."""
    live_noaa = {
        c.name for c in cities_by_name.values() if c.settlement_source_type == "noaa"
    }
    backfill_keys = set(ogimet_backfill_module.OGIMET_TARGETS.keys())
    assert backfill_keys == live_noaa, (
        f"Drift between cities.json noaa-sstype and "
        f"backfill_ogimet_metar.OGIMET_TARGETS:\n"
        f"  only in cities.json: {sorted(live_noaa - backfill_keys)}\n"
        f"  only in backfill:    {sorted(backfill_keys - live_noaa)}"
    )


def test_ogimet_backfill_station_tags_match_tier_resolver(ogimet_backfill_module):
    """Per-city source tag in Ogimet backfill == tier_resolver expected source.

    Complements A2 at the backfill-script level: if someone edits the
    source_tag for Moscow from 'ogimet_metar_uuww' to something else,
    the v2 writer would reject the write; this test catches it earlier.
    """
    from src.data.tier_resolver import EXPECTED_SOURCE_BY_CITY

    mismatches: list[tuple[str, str, str]] = []
    for name, target in ogimet_backfill_module.OGIMET_TARGETS.items():
        expected = EXPECTED_SOURCE_BY_CITY.get(name)
        actual = target.source_tag
        if expected is None:
            continue  # would already fail the keys test above
        if expected != actual:
            mismatches.append((name, expected, actual))
    assert not mismatches, (
        "source_tag drift between tier_resolver and Ogimet backfill:\n"
        + "\n".join(
            f"  {n}: tier_resolver={e!r}, backfill={a!r}" for n, e, a in mismatches
        )
    )


# ----------------------------------------------------------------------
# Regression pin: Phase -1 DRIFT targets
# ----------------------------------------------------------------------


def test_tel_aviv_not_in_wu_backfill(wu_backfill_module):
    """Phase -1 deleted Tel Aviv from CITY_STATIONS; must stay gone."""
    assert "Tel Aviv" not in wu_backfill_module.CITY_STATIONS


@pytest.mark.parametrize("stale_city", ["Taipei"])
def test_stale_cities_not_in_ogimet_backfill(ogimet_backfill_module, stale_city):
    """Cities whose current resolver remains WU must stay out of Ogimet."""
    assert stale_city not in ogimet_backfill_module.OGIMET_TARGETS


@pytest.mark.parametrize("migrated_city", ["Cape Town", "Lucknow"])
def test_migrated_noaa_cities_are_in_ogimet_backfill(
    ogimet_backfill_module, migrated_city
):
    """A current Gamma NOAA resolver is executable, not a stale deny-list."""
    assert migrated_city in ogimet_backfill_module.OGIMET_TARGETS


def test_obs_v2_backfill_splits_provider_transition(obs_v2_backfill_module):
    segments = obs_v2_backfill_module._effective_tier_segments(
        "Chicago", date(2026, 8, 22), date(2026, 8, 24)
    )
    assert [(tier.value, start.isoformat(), end.isoformat()) for tier, start, end in segments] == [
        ("wu_icao", "2026-08-22", "2026-08-22"),
        ("ogimet_metar", "2026-08-23", "2026-08-24"),
    ]


def test_wu_backfill_refuses_post_transition_dates(wu_backfill_module):
    result = wu_backfill_module.backfill_city(
        "Chicago",
        2,
        object(),
        start_date=date(2026, 8, 23),
        end_date=date(2026, 8, 24),
    )
    assert result["collected"] == 0
    assert result["skip"] == 2


def test_ogimet_backfill_refuses_pre_transition_dates(ogimet_backfill_module):
    city = cities_by_name["Chicago"]
    result = ogimet_backfill_module.backfill_city(
        object(),
        city,
        ogimet_backfill_module.OGIMET_TARGETS["Chicago"],
        date(2026, 8, 21),
        date(2026, 8, 22),
        dry_run=True,
        run_id="test",
    )
    assert result == {"city": "Chicago", "days_written": 0, "days_skipped": 2}


def test_ogimet_backfill_converts_raw_celsius_to_city_unit(ogimet_backfill_module):
    captured: list[tuple] = []

    class _Connection:
        def execute(self, _sql, params):
            captured.append(tuple(params))

    bucket = {
        "temps": [10.0, 20.0],
        "count": 2,
        "first_utc": datetime(2026, 8, 24, 12, tzinfo=timezone.utc),
        "last_utc": datetime(2026, 8, 24, 13, tzinfo=timezone.utc),
    }
    ogimet_backfill_module._write_day(
        _Connection(),
        cities_by_name["Chicago"],
        ogimet_backfill_module.OGIMET_TARGETS["Chicago"],
        date(2026, 8, 24),
        bucket,
        "test-unit-conversion",
    )

    params = captured[0]
    assert params[3] == pytest.approx(68.0)
    assert params[4] == pytest.approx(50.0)
    assert params[8] == pytest.approx(20.0)
    assert params[9:11] == ("C", "F")


def test_ogimet_backfill_uses_shared_request_governor(
    ogimet_backfill_module,
    monkeypatch,
):
    governed: list[bool] = []
    monkeypatch.setattr(
        ogimet_backfill_module,
        "wait_for_ogimet_request_slot",
        lambda: governed.append(True),
    )
    monkeypatch.setattr(
        ogimet_backfill_module.requests,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=200, text="ok"),
    )

    result = ogimet_backfill_module._fetch_window(
        ogimet_backfill_module.OGIMET_TARGETS["Chicago"],
        datetime(2026, 8, 24, tzinfo=timezone.utc),
        datetime(2026, 8, 25, tzinfo=timezone.utc),
    )

    assert result == "ok"
    assert governed == [True]


def test_hole_scanner_expected_source_is_target_date_scoped():
    chicago = cities_by_name["Chicago"]
    assert _source_applies_to_city("wu_icao_history", chicago, date(2026, 8, 22))
    assert not _source_applies_to_city("ogimet_metar_kord", chicago, date(2026, 8, 22))
    assert _source_applies_to_city("ogimet_metar_kord", chicago, date(2026, 8, 23))
    assert not _source_applies_to_city("wu_icao_history", chicago, date(2026, 8, 23))


def test_persistence_etl_preserves_both_sides_of_transition():
    from scripts.etl_temp_persistence import _is_canonical_daily_observation

    assert _is_canonical_daily_observation(
        "Chicago", "wu_icao_history", "KORD:US", target_date="2026-08-22"
    )
    assert not _is_canonical_daily_observation(
        "Chicago", "ogimet_metar_kord", "KORD", target_date="2026-08-22"
    )
    assert _is_canonical_daily_observation(
        "Chicago", "ogimet_metar_kord", "KORD", target_date="2026-08-23"
    )
    assert not _is_canonical_daily_observation(
        "Chicago", "wu_icao_history", "KORD:US", target_date="2026-08-23"
    )


@pytest.mark.parametrize("path", OBS_V2_PRODUCER_PATHS, ids=lambda p: p.name)
def test_obs_v2_producers_stamp_payload_identity_keys(path):
    source = path.read_text(encoding="utf-8")
    for required in (
        '"payload_hash"',
        '"parser_version"',
        '"payload_scope"',
    ):
        assert required in source, f"{path.name} must stamp {required}"
    assert (
        '"source_url"' in source or '"source_file"' in source
    ), f"{path.name} must stamp source_url or source_file"
    assert (
        '"station_id"' in source
        or '"station_registry_version"' in source
        or '"station_registry_hash"' in source
    ), f"{path.name} must stamp station identity"


@pytest.mark.parametrize(
    "path",
    COMPLETENESS_GUARDED_BACKFILL_PATHS,
    ids=lambda p: p.name,
)
def test_p2_backfill_scripts_declare_completeness_guardrails(path):
    source = path.read_text(encoding="utf-8")
    for required in (
        "add_completeness_args",
        "COMPLETENESS_MANIFEST_PREFIX",
        "backfill_manifest_",
    ):
        assert required in source, f"{path.name} must declare {required}"


def test_p2_backfill_completeness_helper_declares_cli_flags():
    source = (REPO_ROOT / "scripts" / "backfill_completeness.py").read_text(
        encoding="utf-8"
    )
    for required in (
        "--completeness-manifest",
        "--expected-count",
        "--fail-threshold-percent",
    ):
        assert required in source


def test_obs_v2_backfill_row_stamps_provenance_identity(obs_v2_backfill_module):
    row = obs_v2_backfill_module._hourly_obs_to_v2_row(
        HourlyObservation(
            city="Chicago",
            target_date="2026-04-23",
            local_hour=8.0,
            local_timestamp="2026-04-23T08:00:00-05:00",
            utc_timestamp="2026-04-23T13:00:00+00:00",
            utc_offset_minutes=-300,
            dst_active=1,
            is_ambiguous_local_hour=0,
            is_missing_local_hour=0,
            time_basis="utc_hour_bucket_extremum",
            hour_max_temp=71.0,
            hour_min_temp=69.0,
            hour_max_raw_ts="2026-04-23T13:45:00+00:00",
            hour_min_raw_ts="2026-04-23T13:05:00+00:00",
            temp_unit="F",
            station_id="KORD",
            observation_count=4,
        ),
        data_version="v1.wu-native.pilot",
        imported_at="2026-04-25T12:00:00+00:00",
        tier_name="WU_ICAO",
    )

    provenance = json.loads(row.provenance_json)
    assert provenance["payload_hash"].startswith("sha256:")
    assert provenance["payload_scope"] == "obs_v2_hour_bucket_source_identity"
    assert provenance["parser_version"] == "obs_v2_backfill_hourly_extremum_v2"
    assert provenance["station_id"] == "KORD"
    assert "apiKey=REDACTED" in provenance["source_url"]


def test_obs_v2_backfill_rerun_reports_zero_rows_written(
    obs_v2_backfill_module,
    tmp_path,
    monkeypatch,
):
    """Writer no-op reruns must not inflate backfill rows_written counters."""
    fetch_result = SimpleNamespace(
        failed=False,
        retryable=False,
        failure_reason=None,
        raw_observation_count=1,
        observations=[_hourly_observation(city="Chicago", station_id="KORD")],
    )
    monkeypatch.setattr(
        obs_v2_backfill_module,
        "fetch_wu_hourly",
        lambda **_kwargs: fetch_result,
    )
    monkeypatch.setattr(obs_v2_backfill_module.time, "sleep", lambda _seconds: None)
    conn = sqlite3.connect(":memory:")
    try:
        apply_canonical_schema(conn)
        first = obs_v2_backfill_module._backfill_wu_city(
            conn,
            "Chicago",
            date(2026, 4, 23),
            date(2026, 4, 23),
            "v1.wu-native.pilot",
            tmp_path / "obs-v2-log.jsonl",
            dry_run=False,
        )
        second = obs_v2_backfill_module._backfill_wu_city(
            conn,
            "Chicago",
            date(2026, 4, 23),
            date(2026, 4, 23),
            "v1.wu-native.pilot",
            tmp_path / "obs-v2-log.jsonl",
            dry_run=False,
        )
    finally:
        conn.close()

    assert first.rows_written == 1
    assert second.rows_written == 0
    assert second.rows_ready == 1


def test_hko_ingest_row_stamps_provenance_identity(hko_ingest_tick_module):
    snapshot = hko_ingest_tick_module.HkoExtremaSnapshot(
        target_date="2026-04-23",
        observed_at_utc="2026-04-23T13:05:00+00:00",
        high_c=33.8,
        low_c=24.1,
        fetched_at_utc="2026-04-23T13:05:10+00:00",
    )
    row = hko_ingest_tick_module._build_hko_extrema_row(
        snapshot,
        temperature_c=24.5,
        accumulator_fetched_at="2026-04-23T13:00:05+00:00",
        data_version="v1.hk-accumulator.forward",
        imported_at="2026-04-25T12:00:00+00:00",
    )

    provenance = json.loads(row.provenance_json)
    assert provenance["payload_hash"].startswith("sha256:")
    assert provenance["payload_scope"] == "hko_current_and_since_midnight_extrema"
    assert provenance["parser_version"] == "hko_since_midnight_extrema"
    assert provenance["observation_basis"] == "hko_since_midnight_extrema_1min_mean"
    assert provenance["source_file"].endswith("latest_since_midnight_maxmin.csv")
    assert provenance["station_id"] == "HKO"
    assert row.temp_current == 24.5
    assert row.running_max == 33.8
    assert row.running_min == 24.1


def test_hko_spot_reading_remains_diagnostic_not_official_extreme(hko_ingest_tick_module):
    """HKO current temperature and official 1-minute-mean max are different
    statistics. A higher spot reading stays diagnostic and cannot fabricate an
    absorbing official cumulative maximum."""
    snapshot = hko_ingest_tick_module.HkoExtremaSnapshot(
        target_date="2026-07-15",
        observed_at_utc="2026-07-15T02:20:00+00:00",
        high_c=28.8,
        low_c=24.0,
        fetched_at_utc="2026-07-15T02:20:10+00:00",
    )
    row = hko_ingest_tick_module._build_hko_extrema_row(
        snapshot,
        temperature_c=29.0,
        accumulator_fetched_at="2026-07-15T02:20:05+00:00",
        data_version="v1.hk-accumulator.forward",
        imported_at="2026-07-15T02:20:15+00:00",
    )
    assert row.temp_current == 29.0
    assert row.running_max == 28.8
    assert row.running_min == 24.0  # low side untouched, spot is above it
    provenance = json.loads(row.provenance_json)
    assert provenance["official_running_high_c"] == 28.8
    assert provenance["diagnostic_current_temperature_c"] == 29.0


def test_hko_low_spot_reading_remains_diagnostic_not_official_extreme(hko_ingest_tick_module):
    """LOW mirror: a colder spot reading cannot replace HKO's official
    since-midnight 1-minute-mean minimum."""
    snapshot = hko_ingest_tick_module.HkoExtremaSnapshot(
        target_date="2026-07-15",
        observed_at_utc="2026-07-15T14:20:00+00:00",  # 22:20 HK local (UTC+8), still 07-15
        high_c=28.8,
        low_c=18.0,
        fetched_at_utc="2026-07-15T14:20:10+00:00",
    )
    row = hko_ingest_tick_module._build_hko_extrema_row(
        snapshot,
        temperature_c=16.5,
        accumulator_fetched_at="2026-07-15T14:20:05+00:00",
        data_version="v1.hk-accumulator.forward",
        imported_at="2026-07-15T14:20:15+00:00",
    )
    assert row.temp_current == 16.5
    assert row.running_min == 18.0
    assert row.running_max == 28.8  # high side untouched, spot is below it


def test_hko_no_regression_official_extreme_higher_than_spot_stays(hko_ingest_tick_module):
    """No-regression case: when HKO's official since-midnight extrema are
    ALREADY beyond the current spot reading (the normal case — a spot
    reading rarely exceeds the day's already-established peak), the
    official values must pass through unchanged, not get pulled toward the
    spot reading."""
    snapshot = hko_ingest_tick_module.HkoExtremaSnapshot(
        target_date="2026-07-15",
        observed_at_utc="2026-07-15T06:00:00+00:00",
        high_c=35.0,
        low_c=22.0,
        fetched_at_utc="2026-07-15T06:00:10+00:00",
    )
    row = hko_ingest_tick_module._build_hko_extrema_row(
        snapshot,
        temperature_c=29.0,
        accumulator_fetched_at="2026-07-15T06:00:05+00:00",
        data_version="v1.hk-accumulator.forward",
        imported_at="2026-07-15T06:00:15+00:00",
    )
    assert row.temp_current == 29.0
    assert row.running_max == 35.0
    assert row.running_min == 22.0


def test_hko_ingest_parses_official_since_midnight_extrema(hko_ingest_tick_module):
    payload = """Date time,Automatic Weather Station,Maximum Air Temperature Since Midnight(degree Celsius),Minimum Air Temperature Since Midnight(degree Celsius)
202607132350,Chek Lap Kok,34.3,29.1
202607132350,HK Observatory,33.8,29.0
"""

    snapshot = hko_ingest_tick_module._parse_hko_extrema_csv(
        payload,
        fetched_at_utc="2026-07-13T15:50:10+00:00",
    )

    assert snapshot.target_date == "2026-07-13"
    assert snapshot.observed_at_utc == "2026-07-13T15:50:00+00:00"
    assert snapshot.high_c == 33.8
    assert snapshot.low_c == 29.0


def _native_hko_snapshot(module, native="202610050730"):
    body = (b"\xef\xbb\xbfDate time,Automatic Weather Station,Maximum Air Temperature Since Midnight(degree Celsius),"
        b"Minimum Air Temperature Since Midnight(degree Celsius)\r\n"
        + native.encode() + b",HK Observatory,26.6,25.1\r\n"
        + native.encode() + b",Chek Lap Kok,27.3,25.4\r\n")
    return module._parse_hko_extrema_csv(body, fetched_at_utc="2026-10-04T23:38:12+00:00",
        capture_started_at_utc="2026-10-04T23:38:11+00:00",
        response_headers={"Date":"Sun, 04 Oct 2026 23:38:12 GMT", "Authorization":"secret"})


@pytest.mark.parametrize("metric,expected", [("high", 26.6), ("low", 25.1)])
def test_hko_original_entity_roundtrips_canonical_row(hko_ingest_tick_module, tmp_path, metric, expected):
    import base64
    import hashlib
    module = hko_ingest_tick_module
    snapshot = _native_hko_snapshot(module)
    assert hashlib.sha256(snapshot.raw_body).hexdigest() == "9fbb2a7a335966d10f6e2b4b2feaaf31a6de5afb30e2ead185245deb1631faae"
    conn = sqlite3.connect(tmp_path / "private-world.db")
    try:
        apply_canonical_schema(conn)
        row = module._build_hko_extrema_row(snapshot, temperature_c=25.0,
            accumulator_fetched_at=None, data_version="v1.wu-native",
            imported_at="2026-10-04T23:38:13+00:00")
        assert module.insert_rows(conn, [row]) == 1
        conn.commit()
        saved = conn.execute("SELECT raw_response,provenance_json,running_max,running_min FROM observation_instants").fetchone()
        proof = json.loads(saved[1])
        body = base64.b64decode(proof["raw_body_base64"])
        assert saved[0].encode("utf-8") == snapshot.raw_body == body
        assert hashlib.sha256(body).hexdigest() == proof["raw_body_sha256"]
        replay = module._parse_hko_extrema_csv(body, fetched_at_utc=proof["capture_completed_at_utc"],
            capture_started_at_utc=proof["capture_started_at_utc"])
        assert getattr(replay, "high_c" if metric == "high" else "low_c") == expected
        assert proof["source_issued_at_utc"] is None
        assert proof["written_at_utc"] == row.imported_at != snapshot.fetched_at_utc
        assert "authorization" not in proof["response_headers"]
    finally:
        conn.close()


@pytest.mark.parametrize("change", ["date", "high", "low", "native", "fetched", "missing_finish", "start_after_finish", "early_write", "naive_write"])
def test_hko_native_capture_tamper_is_rejected(hko_ingest_tick_module, change):
    from dataclasses import replace
    module = hko_ingest_tick_module
    snapshot = _native_hko_snapshot(module)
    edits = {"date":{"target_date":"2026-10-04"}, "high":{"high_c":27.0}, "low":{"low_c":25.0},
        "native":{"native_datetime":"202610050731"}, "fetched":{"fetched_at_utc":"2026-10-04T23:39:00+00:00"},
        "missing_finish":{"capture_completed_at_utc":None},
        "start_after_finish":{"capture_started_at_utc":"2026-10-04T23:39:00+00:00"}}
    snapshot = replace(snapshot, **edits.get(change,{}))
    imported = "2026-10-04T23:38:13+00:00"
    if change == "early_write": imported = "2026-10-04T23:38:11+00:00"
    if change == "naive_write": imported = "2026-10-04T23:38:13"
    with pytest.raises(ValueError):
        module._build_hko_extrema_row(snapshot, temperature_c=None, accumulator_fetched_at=None,
            data_version="v1.wu-native", imported_at=imported)


@pytest.mark.parametrize("damage", ["wrong_station", "duplicate_station", "future_native", "malformed", "nan"])
def test_hko_original_entity_rejects_invalid_native_row(hko_ingest_tick_module, damage):
    module = hko_ingest_tick_module
    body = _native_hko_snapshot(module).raw_body
    if damage == "wrong_station": body = body.replace(b"HK Observatory",b"Other Station")
    elif damage == "duplicate_station": body += b"202610050730,HK Observatory,26.6,25.1\r\n"
    elif damage == "future_native": body = body.replace(b"202610050730",b"202610050800")
    elif damage == "nan": body = body.replace(b"26.6",b"nan")
    else: body = b"bad csv"
    with pytest.raises(ValueError):
        module._parse_hko_extrema_csv(body, fetched_at_utc="2026-10-04T23:38:12+00:00",
            capture_started_at_utc="2026-10-04T23:38:11+00:00")


def test_hko_forward_capture_does_not_rewrite_same_asof_legacy_row(hko_ingest_tick_module, tmp_path):
    module = hko_ingest_tick_module
    conn = sqlite3.connect(tmp_path / "private-world.db")
    try:
        apply_canonical_schema(conn)
        conn.execute("CREATE TABLE hko_hourly_accumulator(target_date TEXT,hour_utc TEXT,temperature REAL,fetched_at TEXT)")
        legacy = module.HkoExtremaSnapshot("2026-10-05","2026-10-04T23:30:00+00:00",26.6,25.1,"2026-10-04T23:35:00+00:00")
        oldrow = module._build_hko_extrema_row(legacy, temperature_c=None, accumulator_fetched_at=None,
            data_version="v1.wu-native", imported_at="2026-10-04T23:35:01+00:00")
        prior = module.HkoExtremaSnapshot("2026-10-04","2026-10-04T15:59:00+00:00",30.6,26.1,"2026-10-04T15:59:01+00:00")
        priorrow = module._build_hko_extrema_row(prior, temperature_c=None, accumulator_fetched_at=None,
            data_version="v1.wu-native", imported_at="2026-10-04T15:59:02+00:00")
        assert module.insert_rows(conn, [priorrow,oldrow]) == 2
        conn.commit()
        before = conn.execute("SELECT raw_response,provenance_json,imported_at FROM observation_instants WHERE utc_timestamp=?",(legacy.observed_at_utc,)).fetchone()
        result = module.project_accumulator_to_v2(conn,"v1.wu-native",tmp_path/"log.jsonl",snapshot=_native_hko_snapshot(module))
        assert result["written"] == 0
        assert conn.execute("SELECT raw_response,provenance_json,imported_at FROM observation_instants WHERE utc_timestamp=?",(legacy.observed_at_utc,)).fetchone() == before
        from src.data.observation_instants_writer import InvalidObsV2RowError
        same_asof_raw = module._build_hko_extrema_row(_native_hko_snapshot(module), temperature_c=None,
            accumulator_fetched_at=None, data_version="v1.wu-native", imported_at="2026-10-04T23:38:13+00:00")
        with pytest.raises(InvalidObsV2RowError, match="payload_hash reused"):
            module.insert_rows(conn,[same_asof_raw])
        newer = _native_hko_snapshot(module,"202610050731")
        result = module.project_accumulator_to_v2(conn,"v1.wu-native",tmp_path/"log.jsonl",snapshot=newer)
        assert result["written"] == 1  # unchanged extrema with an advanced native coverage cut
        saved = conn.execute("SELECT raw_response,imported_at FROM observation_instants WHERE utc_timestamp=?",(newer.observed_at_utc,)).fetchone()
        assert saved[0].encode("utf-8") == newer.raw_body
        assert datetime.fromisoformat(saved[1]) >= datetime.fromisoformat(newer.capture_completed_at_utc)
        assert not conn.in_transaction
    finally:
        conn.close()


def test_hko_native_forward_capture_outer_rollback_leaves_no_row(hko_ingest_tick_module, tmp_path):
    module = hko_ingest_tick_module
    conn = sqlite3.connect(tmp_path / "private-world.db")
    try:
        apply_canonical_schema(conn)
        snapshot = _native_hko_snapshot(module)
        row = module._build_hko_extrema_row(snapshot, temperature_c=None, accumulator_fetched_at=None,
            data_version="v1.wu-native", imported_at="2026-10-04T23:38:13+00:00")
        conn.execute("BEGIN")
        assert module.insert_rows(conn,[row]) == 1
        assert conn.in_transaction  # writer savepoint is not a canonical commit or validator ACK
        conn.rollback()
        assert conn.execute("SELECT COUNT(*) FROM observation_instants").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.parametrize("damage", ["raw_missing", "fetch_mismatch", "completion_mismatch", "start_after_finish",
    "write_before_fetch", "written_mismatch", "future_import", "future_fetch", "foreign_receipt",
    "subsecond_future_import", "microsecond_future_import", "subsecond_receipt_mismatch",
    "subsecond_completion_mismatch", "subsecond_reversed_start", "subsecond_written_mismatch",
    "naive_import", "invalid_capture", "exact_subsecond_cut", "subsecond_observation_mismatch",
    "subsecond_first_fetch_reversed", "legacy_subsecond_future_import", "offset_equivalent_clocks",
    "subsecond_first_observation"])
def test_hko_rollover_ordered_clock_requires_this_original_capture(hko_ingest_tick_module, tmp_path, monkeypatch, damage):
    from src.data.day0_observation_reader import hko_rollover_carryover_status
    module = hko_ingest_tick_module
    class WriterClock(datetime):
        @classmethod
        def now(cls, tz=None): return datetime(2026,10,4,23,39,tzinfo=timezone.utc).astimezone(tz or timezone.utc)
    monkeypatch.setattr(module,"datetime",WriterClock)
    conn = sqlite3.connect(tmp_path / "private-world.db")
    try:
        apply_canonical_schema(conn)
        conn.execute("CREATE TABLE hko_hourly_accumulator(target_date TEXT,hour_utc TEXT,temperature REAL,fetched_at TEXT)")
        conn.commit()
        first = _native_hko_snapshot(module)
        assert module.project_accumulator_to_v2(conn,"v1.wu-native",tmp_path/"log.jsonl",snapshot=first)["source_not_ready"] == 1
        body = first.raw_body.replace(b"202610050730",b"202610050731").replace(b"26.6",b"26.7")
        second = module._parse_hko_extrema_csv(body,fetched_at_utc="2026-10-04T23:38:14+00:00",
            capture_started_at_utc="2026-10-04T23:38:13+00:00")
        assert module.project_accumulator_to_v2(conn,"v1.wu-native",tmp_path/"log.jsonl",snapshot=second)["written"] == 1
        cut = datetime(2026,10,4,23,40,tzinfo=timezone.utc)
        assert hko_rollover_carryover_status(conn,target_date="2026-10-05",decision_time=cut) == "RESET_CONFIRMED"
        raw,prov,imported = conn.execute("SELECT raw_response,provenance_json,imported_at FROM observation_instants").fetchone()
        p = json.loads(prov)
        if damage == "raw_missing": raw = None
        elif damage == "fetch_mismatch": p["extrema_fetched_at"] = "2026-10-04T23:38:12+00:00"
        elif damage == "completion_mismatch": p["capture_completed_at_utc"] = "2026-10-04T23:38:12+00:00"
        elif damage == "start_after_finish": p["capture_started_at_utc"] = "2026-10-04T23:38:15+00:00"
        elif damage == "write_before_fetch": imported = "2026-10-04T23:38:12+00:00"
        elif damage == "written_mismatch": p["written_at_utc"] = "2026-10-04T23:39:01+00:00"
        elif damage == "future_import": imported = "2026-10-04T23:50:00+00:00"; p["written_at_utc"] = imported
        elif damage == "future_fetch":
            p["extrema_fetched_at"] = p["capture_completed_at_utc"] = p["rollover_reset_confirmation"]["confirmed_fetched_at_utc"] = "2026-10-04T23:50:00+00:00"
        elif damage == "foreign_receipt": p["rollover_reset_confirmation"]["confirmed_observed_at_utc"] = "2026-10-04T23:30:00+00:00"
        elif damage in {"subsecond_future_import", "microsecond_future_import", "exact_subsecond_cut"}:
            cut = datetime(2026,10,4,23,39,0,100000,tzinfo=timezone.utc)
            imported = "2026-10-04T23:39:00.900000+00:00" if damage == "subsecond_future_import" else "2026-10-04T23:39:00.100001+00:00"
            if damage == "exact_subsecond_cut": imported = cut.isoformat()
            p["written_at_utc"] = imported
        elif damage == "subsecond_receipt_mismatch": p["rollover_reset_confirmation"]["confirmed_fetched_at_utc"] = "2026-10-04T23:38:14.000001+00:00"
        elif damage == "subsecond_completion_mismatch": p["capture_completed_at_utc"] = "2026-10-04T23:38:14.000001+00:00"
        elif damage == "subsecond_reversed_start": p["capture_started_at_utc"] = "2026-10-04T23:38:14.000001+00:00"
        elif damage == "subsecond_written_mismatch": p["written_at_utc"] = "2026-10-04T23:39:00.000001+00:00"
        elif damage == "naive_import": imported = "2026-10-04T23:39:00"; p["written_at_utc"] = imported
        elif damage == "invalid_capture": p["capture_started_at_utc"] = "not-a-clock"
        elif damage == "subsecond_observation_mismatch": p["rollover_reset_confirmation"]["confirmed_observed_at_utc"] = "2026-10-04T23:31:00.000001+00:00"
        elif damage == "subsecond_first_fetch_reversed": p["rollover_reset_confirmation"]["first_probe_fetched_at_utc"] = "2026-10-04T23:38:14.000001+00:00"
        elif damage == "legacy_subsecond_future_import":
            raw = None
            cut = datetime(2026,10,4,23,39,0,100000,tzinfo=timezone.utc)
            imported = "2026-10-04T23:39:00.100001+00:00"
            p["rollover_reset_confirmation"]["confirmed_fetched_at_utc"] = imported
        elif damage == "offset_equivalent_clocks":
            p["extrema_fetched_at"] = "2026-10-05T07:38:14+08:00"
            p["capture_completed_at_utc"] = "2026-10-04T18:38:14-05:00"
            p["written_at_utc"] = "2026-10-05T07:39:00+08:00"
        elif damage == "subsecond_first_observation": p["rollover_reset_confirmation"]["first_probe_observed_at_utc"] = "2026-10-04T23:30:59.999999+00:00"
        conn.execute("UPDATE observation_instants SET raw_response=?,provenance_json=?,imported_at=?",(raw,json.dumps(p),imported))
        expected = "RESET_CONFIRMED" if damage in {"exact_subsecond_cut", "offset_equivalent_clocks", "subsecond_first_observation"} else "UNPROVEN"
        assert hko_rollover_carryover_status(conn,target_date="2026-10-05",decision_time=cut) == expected
    finally:
        conn.close()


def test_hko_ingest_repeated_provider_snapshot_is_idempotent(hko_ingest_tick_module):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE observation_instants (
            id INTEGER PRIMARY KEY,
            city TEXT,
            source TEXT,
            utc_timestamp TEXT,
            running_max REAL,
            running_min REAL,
            causality_status TEXT,
            provenance_json TEXT,
            imported_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO observation_instants VALUES (
            1, 'Hong Kong', 'hko_hourly_accumulator',
            '2026-07-13T15:50:00+00:00', 33.8, 29.0, 'OK',
            '{"observation_basis":"hko_since_midnight_extrema_1min_mean","official_running_high_c":33.8,"official_running_low_c":29.0}',
            '2026-07-13T15:51:00+00:00'
        )
        """
    )
    snapshot = hko_ingest_tick_module.HkoExtremaSnapshot(
        target_date="2026-07-13",
        observed_at_utc="2026-07-13T15:50:00+00:00",
        high_c=33.8,
        low_c=29.0,
        fetched_at_utc="2026-07-13T15:51:00+00:00",
    )

    assert hko_ingest_tick_module._same_extrema_already_materialized(conn, snapshot)
    assert not hko_ingest_tick_module._same_extrema_already_materialized(
        conn,
        hko_ingest_tick_module.HkoExtremaSnapshot(
            target_date=snapshot.target_date,
            observed_at_utc=snapshot.observed_at_utc,
            high_c=34.0,
            low_c=snapshot.low_c,
            fetched_at_utc=snapshot.fetched_at_utc,
        ),
    )
    conn.close()


def _hko_projection_transaction_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE hko_hourly_accumulator (
            target_date TEXT NOT NULL,
            hour_utc TEXT NOT NULL,
            temperature REAL NOT NULL,
            fetched_at TEXT NOT NULL
        );
        CREATE TABLE observation_instants (
            id INTEGER PRIMARY KEY,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            utc_timestamp TEXT NOT NULL,
            running_max REAL,
            running_min REAL,
            causality_status TEXT,
            provenance_json TEXT,
            imported_at TEXT
        );
        CREATE TABLE projection_probe (value TEXT NOT NULL);
        CREATE TABLE outer_probe (value TEXT NOT NULL);
        INSERT INTO hko_hourly_accumulator VALUES
            ('2026-07-19', '2026-07-19T01:00Z', 31.0, '2026-07-19T01:01:00+00:00');
        INSERT INTO observation_instants VALUES
            (1, 'Hong Kong', '2026-07-19', 'hko_hourly_accumulator',
             '2026-07-19T00:00:00+00:00', 30.0, 25.0, 'OK',
             '{"observation_basis":"hko_since_midnight_extrema_1min_mean"}',
             '2026-07-19T00:00:05+00:00');
        INSERT INTO observation_instants VALUES
            (2, 'Hong Kong', '2026-07-18', 'hko_hourly_accumulator',
             '2026-07-18T15:50:00+00:00', 29.0, 24.0, 'OK',
             '{"observation_basis":"hko_since_midnight_extrema_1min_mean",
               "official_running_high_c":29.0,
               "official_running_low_c":24.0}',
             '2026-07-18T15:50:05+00:00');
        """
    )
    conn.commit()
    return conn


def _hko_projection_snapshot(hko_ingest_tick_module):
    return hko_ingest_tick_module.HkoExtremaSnapshot(
        target_date="2026-07-19",
        observed_at_utc="2026-07-19T01:00:00+00:00",
        high_c=31.0,
        low_c=25.0,
        fetched_at_utc="2026-07-19T01:01:00+00:00",
    )


def test_hko_projection_rejects_previous_day_rollover_carryover(
    hko_ingest_tick_module,
    monkeypatch,
    tmp_path,
):
    conn = _hko_projection_transaction_conn()
    conn.execute("DELETE FROM observation_instants")
    conn.execute(
        """
        INSERT INTO observation_instants (
            id, city, target_date, source, utc_timestamp,
            running_max, running_min, causality_status, provenance_json,
            imported_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            "Hong Kong",
            "2026-07-18",
            "hko_hourly_accumulator",
            "2026-07-18T15:50:00+00:00",
            31.0,
            25.0,
            "OK",
            json.dumps(
                {
                    "observation_basis": (
                        "hko_since_midnight_extrema_1min_mean"
                    ),
                    "official_running_high_c": 31.0,
                    "official_running_low_c": 25.0,
                }
            ),
            "2026-07-18T15:51:00+00:00",
        ),
    )
    conn.commit()
    inserted = []
    monkeypatch.setattr(
        hko_ingest_tick_module,
        "_fetch_hko_extrema",
        lambda: _hko_projection_snapshot(hko_ingest_tick_module),
    )
    monkeypatch.setattr(
        hko_ingest_tick_module,
        "insert_rows",
        lambda *_args, **_kwargs: inserted.append(True),
    )
    try:
        result = hko_ingest_tick_module.project_accumulator_to_v2(
            conn,
            "v1.wu-native",
            tmp_path / "hko.jsonl",
        )
        assert result == {
            "candidates": 1,
            "written": 0,
            "build_errors": 0,
            "source_not_ready": 1,
            "retired": 0,
        }
        assert inserted == []
        log = json.loads((tmp_path / "hko.jsonl").read_text().strip())
        assert log["reason"] == "HKO_NEW_DAY_ROLLOVER_CARRYOVER"
    finally:
        conn.close()


def test_hko_projection_rejects_unproven_rollover_schema(
    hko_ingest_tick_module,
    monkeypatch,
    tmp_path,
):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE observation_instants (
            city TEXT,
            target_date TEXT,
            source TEXT,
            utc_timestamp TEXT
        )
        """
    )
    inserted = []
    monkeypatch.setattr(
        hko_ingest_tick_module,
        "_fetch_hko_extrema",
        lambda: _hko_projection_snapshot(hko_ingest_tick_module),
    )
    monkeypatch.setattr(
        hko_ingest_tick_module,
        "insert_rows",
        lambda *_args, **_kwargs: inserted.append(True),
    )
    try:
        result = hko_ingest_tick_module.project_accumulator_to_v2(
            conn,
            "v1.wu-native",
            tmp_path / "hko.jsonl",
        )
        assert result["source_not_ready"] == 1
        assert result["written"] == 0
        assert inserted == []
        log = json.loads((tmp_path / "hko.jsonl").read_text().strip())
        assert log["reason"] == "HKO_NEW_DAY_ROLLOVER_UNPROVEN"
    finally:
        conn.close()


def test_hko_projection_unproven_probe_drains_after_source_pair_changes(
    hko_ingest_tick_module,
    tmp_path,
    monkeypatch,
):
    from src.data.day0_observation_reader import (
        hko_provisional_revision_likelihood,
        hko_rollover_carryover_status,
    )

    class WriterClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 19, 1, 11, 5, tzinfo=timezone.utc).astimezone(tz or timezone.utc)
    monkeypatch.setattr(hko_ingest_tick_module, "datetime", WriterClock)

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE hko_hourly_accumulator (
            target_date TEXT NOT NULL,
            hour_utc TEXT NOT NULL,
            temperature REAL NOT NULL,
            fetched_at TEXT NOT NULL
        );
        CREATE TABLE observation_instants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            timezone_name TEXT NOT NULL,
            local_hour REAL,
            local_timestamp TEXT NOT NULL,
            utc_timestamp TEXT NOT NULL,
            utc_offset_minutes INTEGER NOT NULL DEFAULT 0,
            dst_active INTEGER NOT NULL DEFAULT 0,
            is_ambiguous_local_hour INTEGER NOT NULL DEFAULT 0,
            is_missing_local_hour INTEGER NOT NULL DEFAULT 0,
            time_basis TEXT NOT NULL DEFAULT 'observation',
            temp_current REAL,
            running_max REAL,
            running_min REAL,
            delta_rate_per_h REAL,
            temp_unit TEXT NOT NULL DEFAULT 'C',
            station_id TEXT,
            observation_count INTEGER,
            raw_response TEXT,
            source_file TEXT,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED',
            data_version TEXT NOT NULL DEFAULT 'v1',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            training_allowed INTEGER DEFAULT 1,
            causality_status TEXT DEFAULT 'OK',
            source_role TEXT
        );
        INSERT INTO hko_hourly_accumulator VALUES
            ('2026-07-19', '2026-07-19T01:00Z', 31.0,
             '2026-07-19T01:01:00+00:00');
        """
    )
    for target_date, observed_at, high_c, low_c in (
        ("2026-07-16", "2026-07-16T01:00:00+00:00", 29.0, 25.0),
        ("2026-07-16", "2026-07-16T01:10:00+00:00", 29.2, 24.9),
        ("2026-07-17", "2026-07-17T01:00:00+00:00", 30.0, 25.5),
        ("2026-07-17", "2026-07-17T01:10:00+00:00", 30.2, 25.4),
    ):
        conn.execute(
            """
            INSERT INTO observation_instants (
                city, target_date, source, timezone_name, local_hour,
                local_timestamp, utc_timestamp, temp_unit, station_id,
                observation_count, imported_at, authority, data_version,
                provenance_json, training_allowed, causality_status,
                source_role
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Hong Kong",
                target_date,
                "hko_hourly_accumulator",
                "Asia/Hong_Kong",
                9.0,
                datetime.fromisoformat(observed_at)
                .astimezone(timezone(timedelta(hours=8)))
                .isoformat(),
                observed_at,
                "C",
                "HKO",
                1,
                observed_at,
                "ICAO_STATION_NATIVE",
                "v1.wu-native",
                json.dumps(
                    {
                        "observation_basis": (
                            "hko_since_midnight_extrema_1min_mean"
                        ),
                        "official_running_high_c": high_c,
                        "official_running_low_c": low_c,
                    }
                ),
                0,
                "OK",
                "runtime_monitoring",
            ),
        )
    conn.commit()
    log_path = tmp_path / "hko.jsonl"
    try:
        first = hko_ingest_tick_module.project_accumulator_to_v2(
            conn,
            "v1.wu-native",
            log_path,
            snapshot=_hko_projection_snapshot(hko_ingest_tick_module),
        )
        assert first["source_not_ready"] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM observation_instants "
            "WHERE target_date = '2026-07-19'"
        ).fetchone()[0] == 0

        changed = hko_ingest_tick_module._parse_hko_extrema_csv(
            b"Date time,Automatic Weather Station,Maximum Air Temperature Since Midnight(degree Celsius),Minimum Air Temperature Since Midnight(degree Celsius)\r\n202607190910,HK Observatory,31.2,25.0\r\n",
            fetched_at_utc="2026-07-19T01:11:00+00:00",
            capture_started_at_utc="2026-07-19T01:10:59+00:00",
        )
        second = hko_ingest_tick_module.project_accumulator_to_v2(
            conn,
            "v1.wu-native",
            log_path,
            snapshot=changed,
        )
        assert second["written"] == 1, (second, log_path.read_text())
        assert second.get("source_not_ready", 0) == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM observation_instants "
            "WHERE target_date = '2026-07-19'"
        ).fetchone()[0] == 1
        decision_time = datetime(
            2026,
            7,
            19,
            1,
            12,
            tzinfo=timezone.utc,
        )
        assert hko_rollover_carryover_status(
            conn,
            target_date="2026-07-19",
            decision_time=decision_time,
        ) == "RESET_CONFIRMED"
        likelihood = hko_provisional_revision_likelihood(
            conn,
            target_date="2026-07-19",
            temperature_metric="high",
            decision_time=decision_time,
        )
        assert likelihood["transition_count"] == 2
        assert (
            0.0
            < likelihood["boundary_survival_probability"]
            < 1.0
        )
    finally:
        conn.close()


def test_hko_projection_standalone_owns_atomic_transaction(
    hko_ingest_tick_module,
    monkeypatch,
    tmp_path,
):
    conn = _hko_projection_transaction_conn()
    observed_in_transaction = []
    monkeypatch.setattr(
        hko_ingest_tick_module,
        "_fetch_hko_extrema",
        lambda: _hko_projection_snapshot(hko_ingest_tick_module),
    )

    def fake_insert_rows(insert_conn, _rows):
        observed_in_transaction.append(insert_conn.in_transaction)
        insert_conn.execute("INSERT INTO projection_probe VALUES ('written')")
        return 1

    monkeypatch.setattr(hko_ingest_tick_module, "insert_rows", fake_insert_rows)
    try:
        result = hko_ingest_tick_module.project_accumulator_to_v2(
            conn, "v1.wu-native", tmp_path / "hko.jsonl"
        )
        assert result == {
            "candidates": 1,
            "written": 1,
            "build_errors": 0,
            "retired": 1,
        }
        assert observed_in_transaction == [True]
        assert not conn.in_transaction
        assert conn.execute("SELECT value FROM projection_probe").fetchall() == [("written",)]
        assert conn.execute(
            "SELECT causality_status FROM observation_instants WHERE id = 1"
        ).fetchone() == ("REQUIRES_SOURCE_REAUDIT",)
    finally:
        conn.close()


def test_hko_projection_standalone_rolls_back_on_write_failure(
    hko_ingest_tick_module,
    monkeypatch,
    tmp_path,
):
    conn = _hko_projection_transaction_conn()
    monkeypatch.setattr(
        hko_ingest_tick_module,
        "_fetch_hko_extrema",
        lambda: _hko_projection_snapshot(hko_ingest_tick_module),
    )

    def fail_insert_rows(_insert_conn, _rows):
        raise sqlite3.OperationalError("injected write failure")

    monkeypatch.setattr(hko_ingest_tick_module, "insert_rows", fail_insert_rows)
    try:
        with pytest.raises(sqlite3.OperationalError, match="injected write failure"):
            hko_ingest_tick_module.project_accumulator_to_v2(
                conn, "v1.wu-native", tmp_path / "hko.jsonl"
            )
        assert not conn.in_transaction
        assert conn.execute(
            "SELECT causality_status FROM observation_instants WHERE id = 1"
        ).fetchone() == ("OK",)
    finally:
        conn.close()


def test_hko_projection_savepoint_preserves_caller_transaction(
    hko_ingest_tick_module,
    monkeypatch,
    tmp_path,
):
    conn = _hko_projection_transaction_conn()
    observed_in_transaction = []
    monkeypatch.setattr(
        hko_ingest_tick_module,
        "_fetch_hko_extrema",
        lambda: _hko_projection_snapshot(hko_ingest_tick_module),
    )

    def fake_insert_rows(insert_conn, _rows):
        observed_in_transaction.append(insert_conn.in_transaction)
        insert_conn.execute("INSERT INTO projection_probe VALUES ('written')")
        return 1

    monkeypatch.setattr(hko_ingest_tick_module, "insert_rows", fake_insert_rows)
    try:
        conn.execute("BEGIN")
        conn.execute("INSERT INTO outer_probe VALUES ('caller-work')")
        result = hko_ingest_tick_module.project_accumulator_to_v2(
            conn, "v1.wu-native", tmp_path / "hko.jsonl"
        )
        assert result["written"] == 1
        assert observed_in_transaction == [True]
        assert conn.in_transaction
        conn.rollback()
        assert conn.execute("SELECT value FROM outer_probe").fetchall() == []
        assert conn.execute("SELECT value FROM projection_probe").fetchall() == []
        assert conn.execute(
            "SELECT causality_status FROM observation_instants WHERE id = 1"
        ).fetchone() == ("OK",)
        assert not (tmp_path / "hko.jsonl").exists()
    finally:
        conn.close()


def test_hko_projection_commit_failure_rolls_back_every_write(
    hko_ingest_tick_module,
    monkeypatch,
    tmp_path,
):
    conn = _hko_projection_transaction_conn()
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE projection_parent (id INTEGER PRIMARY KEY);
        CREATE TABLE projection_child (
            parent_id INTEGER,
            FOREIGN KEY(parent_id) REFERENCES projection_parent(id)
                DEFERRABLE INITIALLY DEFERRED
        );
        """
    )
    monkeypatch.setattr(
        hko_ingest_tick_module,
        "_fetch_hko_extrema",
        lambda: _hko_projection_snapshot(hko_ingest_tick_module),
    )

    def insert_invalid_child(insert_conn, _rows):
        insert_conn.execute("INSERT INTO projection_probe VALUES ('written')")
        insert_conn.execute("INSERT INTO projection_child VALUES (99)")
        return 1

    monkeypatch.setattr(hko_ingest_tick_module, "insert_rows", insert_invalid_child)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            hko_ingest_tick_module.project_accumulator_to_v2(
                conn, "v1.wu-native", tmp_path / "hko.jsonl"
            )
        assert not conn.in_transaction
        assert conn.execute("SELECT value FROM projection_probe").fetchall() == []
        assert conn.execute(
            "SELECT causality_status FROM observation_instants WHERE id = 1"
        ).fetchone() == ("OK",)
        assert not (tmp_path / "hko.jsonl").exists()
    finally:
        conn.close()


def test_hko_tick_commits_caller_visible_ledger_before_logging(
    hko_ingest_tick_module,
    monkeypatch,
    tmp_path,
):
    db_path = tmp_path / "hko.db"
    log_path = tmp_path / "hko.jsonl"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE ledger_probe (value TEXT NOT NULL)")
    conn.commit()

    def append_uncommitted_ledger(tick_conn):
        tick_conn.execute("INSERT INTO ledger_probe VALUES ('durable')")
        return True

    monkeypatch.setattr(
        hko_ingest_tick_module,
        "_accumulate_hko_reading",
        append_uncommitted_ledger,
    )
    result = hko_ingest_tick_module.tick_accumulator(conn, log_path)
    assert result == {"tick_ok": True}
    assert not conn.in_transaction
    conn.close()

    reopened = sqlite3.connect(db_path)
    try:
        assert reopened.execute("SELECT value FROM ledger_probe").fetchall() == [
            ("durable",)
        ]
    finally:
        reopened.close()
    assert json.loads(log_path.read_text().strip())["tick_ok"] is True


def test_hko_tick_rejects_caller_owned_transaction(
    hko_ingest_tick_module,
    monkeypatch,
    tmp_path,
):
    conn = _hko_projection_transaction_conn()
    called = False

    def should_not_run(_conn):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(
        hko_ingest_tick_module,
        "_accumulate_hko_reading",
        should_not_run,
    )
    try:
        conn.execute("BEGIN")
        conn.execute("INSERT INTO outer_probe VALUES ('caller-work')")
        with pytest.raises(RuntimeError, match="transaction-free"):
            hko_ingest_tick_module.tick_accumulator(conn, tmp_path / "hko.jsonl")
        assert called is False
        assert conn.in_transaction
        assert conn.execute("SELECT value FROM outer_probe").fetchall() == [
            ("caller-work",)
        ]
        assert not (tmp_path / "hko.jsonl").exists()
    finally:
        conn.rollback()
        conn.close()


def test_hko_projection_rollback_failure_requires_outer_rollback(
    hko_ingest_tick_module,
    monkeypatch,
    tmp_path,
):
    raw_conn = _hko_projection_transaction_conn()
    released = False

    class RollbackFailureProxy:
        @property
        def in_transaction(self):
            return raw_conn.in_transaction

        def execute(self, sql, parameters=()):
            nonlocal released
            if str(sql).startswith("ROLLBACK TO SAVEPOINT"):
                raise sqlite3.OperationalError("injected rollback cleanup failure")
            if str(sql).startswith("RELEASE SAVEPOINT"):
                released = True
            return raw_conn.execute(sql, parameters)

        def rollback(self):
            return raw_conn.rollback()

    monkeypatch.setattr(
        hko_ingest_tick_module,
        "_fetch_hko_extrema",
        lambda: _hko_projection_snapshot(hko_ingest_tick_module),
    )

    def fail_after_write(insert_conn, _rows):
        insert_conn.execute("INSERT INTO projection_probe VALUES ('failed-write')")
        raise RuntimeError("injected body failure")

    monkeypatch.setattr(hko_ingest_tick_module, "insert_rows", fail_after_write)
    raw_conn.execute("BEGIN")
    raw_conn.execute("INSERT INTO outer_probe VALUES ('caller-work')")
    try:
        with pytest.raises(RuntimeError, match="caller must roll back") as exc_info:
            hko_ingest_tick_module.project_accumulator_to_v2(
                RollbackFailureProxy(),
                "v1.wu-native",
                tmp_path / "hko.jsonl",
            )
        assert "injected body failure" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, sqlite3.OperationalError)
        assert released is False
        assert raw_conn.in_transaction
        raw_conn.rollback()
        assert raw_conn.execute("SELECT value FROM outer_probe").fetchall() == []
        assert raw_conn.execute("SELECT value FROM projection_probe").fetchall() == []
        assert raw_conn.execute(
            "SELECT causality_status FROM observation_instants WHERE id = 1"
        ).fetchone() == ("OK",)
    finally:
        if raw_conn.in_transaction:
            raw_conn.rollback()
        raw_conn.close()


def test_hko_committed_projection_survives_log_failure(
    hko_ingest_tick_module,
    monkeypatch,
    tmp_path,
):
    conn = _hko_projection_transaction_conn()
    monkeypatch.setattr(
        hko_ingest_tick_module,
        "_fetch_hko_extrema",
        lambda: _hko_projection_snapshot(hko_ingest_tick_module),
    )

    def insert_projection(insert_conn, _rows):
        insert_conn.execute("INSERT INTO projection_probe VALUES ('written')")
        return 1

    monkeypatch.setattr(hko_ingest_tick_module, "insert_rows", insert_projection)
    monkeypatch.setattr(
        hko_ingest_tick_module,
        "_append_log",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    try:
        result = hko_ingest_tick_module.project_accumulator_to_v2(
            conn, "v1.wu-native", tmp_path / "hko.jsonl"
        )
        assert result["written"] == 1
        assert not conn.in_transaction
        assert conn.execute("SELECT value FROM projection_probe").fetchall() == [
            ("written",)
        ]
    finally:
        conn.close()


def test_dst_gap_fill_row_stamps_provenance_identity(
    obs_v2_dst_gap_fill_module,
    tmp_path,
    monkeypatch,
):
    captured_rows = []

    def fake_fetch_ogimet_hourly(**_kwargs):
        return SimpleNamespace(
            failed=False,
            failure_reason=None,
            error=None,
            raw_metar_count=1,
            observations=[_hourly_observation(city="Chicago", station_id="KORD")],
        )

    def fake_insert_rows(_conn, rows):
        captured_rows.extend(rows)
        return len(rows)

    monkeypatch.setattr(
        obs_v2_dst_gap_fill_module,
        "fetch_ogimet_hourly",
        fake_fetch_ogimet_hourly,
    )
    monkeypatch.setattr(obs_v2_dst_gap_fill_module, "insert_rows", fake_insert_rows)
    conn = sqlite3.connect(":memory:")
    try:
        written = obs_v2_dst_gap_fill_module._fill_one_date(
            conn,
            "Chicago",
            date(2026, 4, 23),
            "v1.wu-native.pilot",
            tmp_path / "dst-gap-log.jsonl",
            dry_run=False,
        )
    finally:
        conn.close()

    assert written == 1
    provenance = json.loads(captured_rows[0].provenance_json)
    assert provenance["payload_hash"].startswith("sha256:")
    assert provenance["payload_scope"] == "obs_v2_dst_gap_hour_bucket_source_identity"
    assert provenance["parser_version"] == "obs_v2_dst_gap_fill_ogimet_v2"
    assert provenance["station_id"] == "KORD"
    assert provenance["source_url"].startswith("https://www.ogimet.com/")


def test_dst_gap_fill_tier_is_target_date_scoped(
    obs_v2_dst_gap_fill_module,
    tmp_path,
    monkeypatch,
):
    captured_rows = []

    def fake_fetch_ogimet_hourly(**kwargs):
        target = kwargs["start_date"] + timedelta(days=1)
        return SimpleNamespace(
            failed=False,
            failure_reason=None,
            error=None,
            raw_metar_count=1,
            observations=[
                _hourly_observation(
                    city="Chicago",
                    station_id="KORD",
                    target_date=target.isoformat(),
                )
            ],
        )

    monkeypatch.setattr(
        obs_v2_dst_gap_fill_module,
        "fetch_ogimet_hourly",
        fake_fetch_ogimet_hourly,
    )
    monkeypatch.setattr(
        obs_v2_dst_gap_fill_module,
        "insert_rows",
        lambda _conn, rows: captured_rows.extend(rows) or len(rows),
    )
    conn = sqlite3.connect(":memory:")
    try:
        for target in (date(2026, 8, 22), date(2026, 8, 23)):
            assert obs_v2_dst_gap_fill_module._fill_one_date(
                conn,
                "Chicago",
                target,
                "v1.wu-native.pilot",
                tmp_path / "dst-gap-transition.jsonl",
                dry_run=False,
            ) == 1
    finally:
        conn.close()

    tiers = [json.loads(row.provenance_json)["tier"] for row in captured_rows]
    assert tiers == [
        "WU_ICAO_OGIMET_FALLBACK",
        "OGIMET_METAR_BOUNDARY_FILL",
    ]
