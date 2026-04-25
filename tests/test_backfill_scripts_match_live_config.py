# Created: 2026-04-21
# Lifecycle: created=2026-04-21; last_reviewed=2026-04-25; last_reused=2026-04-25
# Purpose: Keep backfill scripts aligned with live config and obs_v2 provenance identity contracts.
# Reuse: Inspect config/cities.json, tier_resolver, script manifest, and current source-validity posture first.
# Last reused/audited: 2026-04-25
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
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config import cities_by_name
from src.data.wu_hourly_client import HourlyObservation
from src.state.schema.v2_schema import apply_v2_schema


REPO_ROOT = Path(__file__).resolve().parent.parent
WU_BACKFILL_PATH = REPO_ROOT / "scripts" / "backfill_wu_daily_all.py"
OGIMET_BACKFILL_PATH = REPO_ROOT / "scripts" / "backfill_ogimet_metar.py"
OBS_V2_BACKFILL_PATH = REPO_ROOT / "scripts" / "backfill_obs_v2.py"
HKO_DAILY_BACKFILL_PATH = REPO_ROOT / "scripts" / "backfill_hko_daily.py"
OBS_V2_DST_GAP_FILL_PATH = REPO_ROOT / "scripts" / "fill_obs_v2_dst_gaps.py"
OBS_V2_METEOSTAT_FILL_PATH = REPO_ROOT / "scripts" / "fill_obs_v2_meteostat.py"
HKO_INGEST_TICK_PATH = REPO_ROOT / "scripts" / "hko_ingest_tick.py"
OBS_V2_PRODUCER_PATHS = [
    OBS_V2_BACKFILL_PATH,
    OBS_V2_DST_GAP_FILL_PATH,
    OBS_V2_METEOSTAT_FILL_PATH,
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
    return _load_module_by_path(OBS_V2_BACKFILL_PATH, "zeus_backfill_obs_v2_identity")


@pytest.fixture(scope="module")
def hko_ingest_tick_module():
    return _load_module_by_path(HKO_INGEST_TICK_PATH, "zeus_hko_ingest_tick_identity")


@pytest.fixture(scope="module")
def obs_v2_dst_gap_fill_module():
    return _load_module_by_path(
        OBS_V2_DST_GAP_FILL_PATH,
        "zeus_fill_obs_v2_dst_gaps_identity",
    )


@pytest.fixture(scope="module")
def obs_v2_meteostat_fill_module():
    return _load_module_by_path(
        OBS_V2_METEOSTAT_FILL_PATH,
        "zeus_fill_obs_v2_meteostat_identity",
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


def test_wu_backfill_city_stations_matches_wu_icao_cities(wu_backfill_module):
    """CITY_STATIONS keys == set of wu_icao cities from cities.json."""
    live_wu_icao = {
        c.name for c in cities_by_name.values() if c.settlement_source_type == "wu_icao"
    }
    backfill_keys = set(wu_backfill_module.CITY_STATIONS.keys())
    missing_in_backfill = live_wu_icao - backfill_keys
    extra_in_backfill = backfill_keys - live_wu_icao
    assert not missing_in_backfill, (
        f"cities.json has wu_icao cities not in "
        f"backfill_wu_daily_all.CITY_STATIONS: {sorted(missing_in_backfill)}. "
        "The WU backfill will skip these cities."
    )
    assert not extra_in_backfill, (
        f"backfill_wu_daily_all.CITY_STATIONS has cities not in "
        f"cities.json (or flipped sstype): {sorted(extra_in_backfill)}. "
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
    """OGIMET_TARGETS keys == set of noaa cities from cities.json.

    After Phase -1 cleanup: both sides should be {Istanbul, Moscow, Tel Aviv}.
    """
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


@pytest.mark.parametrize("stale_city", ["Taipei", "Cape Town", "Lucknow"])
def test_stale_cities_not_in_ogimet_backfill(ogimet_backfill_module, stale_city):
    """Phase -1 deleted Taipei/Cape Town/Lucknow; must stay gone."""
    assert stale_city not in ogimet_backfill_module.OGIMET_TARGETS


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
        apply_v2_schema(conn)
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
    row = hko_ingest_tick_module._build_v2_row(
        target_date="2026-04-23",
        hour_utc="2026-04-23T13:00Z",
        temperature_c=24.5,
        fetched_at="2026-04-23T13:05:00Z",
        data_version="v1.hk-accumulator.forward",
        imported_at="2026-04-25T12:00:00+00:00",
    )

    provenance = json.loads(row.provenance_json)
    assert provenance["payload_hash"].startswith("sha256:")
    assert provenance["payload_scope"] == "hko_accumulator_row_source_identity"
    assert provenance["parser_version"] == "hko_hourly_accumulator_projection_v2"
    assert provenance["source_file"] == "hko_hourly_accumulator"
    assert provenance["station_id"] == "HKO"


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


def test_meteostat_fill_row_stamps_provenance_identity(
    obs_v2_meteostat_fill_module,
    tmp_path,
    monkeypatch,
):
    captured_rows = []

    def fake_fetch_meteostat_bulk(**_kwargs):
        return SimpleNamespace(
            failed=False,
            failure_reason=None,
            raw_row_count=1,
            observations=[_hourly_observation(city="Amsterdam", station_id="EHAM")],
        )

    def fake_insert_rows(_conn, rows):
        captured_rows.extend(rows)
        return len(rows)

    monkeypatch.setattr(
        obs_v2_meteostat_fill_module,
        "fetch_meteostat_bulk",
        fake_fetch_meteostat_bulk,
    )
    monkeypatch.setattr(obs_v2_meteostat_fill_module, "insert_rows", fake_insert_rows)
    conn = sqlite3.connect(":memory:")
    try:
        written, raw, failure = obs_v2_meteostat_fill_module._fill_one_city(
            conn,
            "Amsterdam",
            date(2026, 4, 23),
            date(2026, 4, 23),
            "v1.wu-native.pilot",
            tmp_path / "meteostat-log.jsonl",
            dry_run=False,
        )
    finally:
        conn.close()

    assert (written, raw, failure) == (1, 1, None)
    provenance = json.loads(captured_rows[0].provenance_json)
    assert provenance["payload_hash"].startswith("sha256:")
    assert provenance["payload_scope"] == "obs_v2_meteostat_hour_bucket_source_identity"
    assert provenance["parser_version"] == "obs_v2_meteostat_bulk_fill_v2"
    assert provenance["station_id"] == "EHAM"
    assert provenance["source_url"].startswith("https://bulk.meteostat.net/")
