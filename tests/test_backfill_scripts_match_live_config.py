# Created: 2026-04-21
# Lifecycle: created=2026-04-21; last_reviewed=2026-07-20; last_reused=2026-07-20
# Purpose: Keep backfill scripts aligned with live config and obs_v2 provenance identity contracts.
# Reuse: Inspect config/cities.json, tier_resolver, script manifest, and current source-validity posture first.
# Last reused/audited: 2026-07-20
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
            provenance_json TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO observation_instants VALUES (
            1, 'Hong Kong', 'hko_hourly_accumulator',
            '2026-07-13T15:50:00+00:00', 33.8, 29.0, 'OK',
            '{"observation_basis":"hko_since_midnight_extrema_1min_mean","official_running_high_c":33.8,"official_running_low_c":29.0}'
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
            provenance_json TEXT
        );
        CREATE TABLE projection_probe (value TEXT NOT NULL);
        CREATE TABLE outer_probe (value TEXT NOT NULL);
        INSERT INTO hko_hourly_accumulator VALUES
            ('2026-07-19', '2026-07-19T01:00Z', 31.0, '2026-07-19T01:01:00+00:00');
        INSERT INTO observation_instants VALUES
            (1, 'Hong Kong', '2026-07-19', 'hko_hourly_accumulator',
             '2026-07-19T00:00:00+00:00', 30.0, 25.0, 'OK',
             '{"observation_basis":"hko_since_midnight_extrema_1min_mean"}');
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
