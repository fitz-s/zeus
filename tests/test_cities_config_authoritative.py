"""Authoritative city config + hemisphere/season invariants (K0 packet).

These tests pin the K0 freeze: every city metadata field is correct,
hemisphere/season logic is canonical, upstream DST bugs are fixed,
and the availability_fact schema is kernel-accurate.

Any failure here indicates a regression in K0 or in a field that K0 froze.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.config import cities_by_name
from src.calibration.manager import season_from_date, season_from_month, hemisphere_for_lat


REPO_ROOT = Path(__file__).parent.parent


# ==================== City config completeness ====================

def test_all_46_cities_present():
    assert len(cities_by_name) == 46


def test_every_city_has_all_critical_fields():
    """Every city has: name, lat, lon, timezone, settlement_unit, wu_station, cluster,
    country_code, historical_peak_hour, settlement_source_type, aliases, slug_names.
    """
    for name, city in cities_by_name.items():
        assert city.name, f"{name}: empty name"
        assert isinstance(city.lat, float) and -90 <= city.lat <= 90, f"{name}: lat={city.lat}"
        assert isinstance(city.lon, float) and -180 <= city.lon <= 180, f"{name}: lon={city.lon}"
        assert city.timezone, f"{name}: empty timezone"
        assert city.settlement_unit in ("F", "C"), f"{name}: unit={city.settlement_unit!r}"
        assert city.wu_station, f"{name}: empty wu_station"
        assert city.cluster, f"{name}: empty cluster"
        assert city.country_code, f"{name}: empty country_code"
        assert 10 <= city.historical_peak_hour <= 20, f"{name}: historical_peak_hour={city.historical_peak_hour}"
        assert city.settlement_source_type in ("wu_icao", "hko"), f"{name}: {city.settlement_source_type!r}"
        assert city.aliases, f"{name}: empty aliases"
        assert city.slug_names, f"{name}: empty slug_names"


def test_timezone_is_iana_loadable():
    """Every city.timezone loads via ZoneInfo."""
    for name, city in cities_by_name.items():
        try:
            ZoneInfo(city.timezone)
        except Exception as e:
            pytest.fail(f"{name}: ZoneInfo({city.timezone!r}) failed: {e}")


def test_wu_station_is_icao_format():
    """ICAO codes are 4 uppercase letters."""
    pattern = re.compile(r'^[A-Z]{4}$')
    for name, city in cities_by_name.items():
        assert pattern.match(city.wu_station), f"{name}: wu_station={city.wu_station!r}"


def test_country_code_is_iso2_uppercase():
    for name, city in cities_by_name.items():
        assert len(city.country_code) == 2, f"{name}: country_code={city.country_code!r}"
        assert city.country_code.isupper(), f"{name}: country_code={city.country_code!r}"


def test_cities_json_matches_city_stations():
    """scripts/backfill_wu_daily_all.py CITY_STATIONS must match cities.json for all 46 cities.

    Invariant: config and backfill script cannot drift.
    """
    from scripts.backfill_wu_daily_all import CITY_STATIONS
    for name, city in cities_by_name.items():
        assert name in CITY_STATIONS, f"{name} missing from CITY_STATIONS"
        icao, cc, unit = CITY_STATIONS[name]
        assert city.wu_station == icao, f"{name}: wu_station mismatch {city.wu_station} vs {icao}"
        assert city.country_code == cc, f"{name}: country_code mismatch {city.country_code} vs {cc}"
        assert city.settlement_unit == unit, f"{name}: unit mismatch {city.settlement_unit} vs {unit}"


# ==================== Hemisphere / season canonical logic ====================

def test_hemisphere_for_lat_is_canonical():
    """hemisphere_for_lat covers all edge cases."""
    assert hemisphere_for_lat(40.0) == "N"
    assert hemisphere_for_lat(-41.3) == "S"
    assert hemisphere_for_lat(0.0) == "N"  # equator folds to N (lat >= 0)
    assert hemisphere_for_lat(1.35) == "N"  # Singapore
    assert hemisphere_for_lat(-6.13) == "S"  # Jakarta


def test_hemisphere_matches_lat_sign_for_all_cities():
    for name, city in cities_by_name.items():
        expected = "N" if city.lat >= 0 else "S"
        assert hemisphere_for_lat(city.lat) == expected, f"{name}: lat={city.lat}"


def test_sh_season_flip_january_all_cities():
    """January 15: SH cities get JJA (southern summer), NH cities get DJF."""
    for name, city in cities_by_name.items():
        expected = "JJA" if city.lat < 0 else "DJF"
        actual = season_from_date("2026-01-15", lat=city.lat)
        assert actual == expected, f"{name} (lat={city.lat}): expected {expected}, got {actual}"


def test_sh_season_flip_july_all_cities():
    """July 15: SH cities get DJF (southern winter), NH cities get JJA."""
    for name, city in cities_by_name.items():
        expected = "DJF" if city.lat < 0 else "JJA"
        actual = season_from_date("2026-07-15", lat=city.lat)
        assert actual == expected, f"{name}: {actual}"


def test_wellington_january_is_southern_summer():
    assert season_from_date("2026-01-15", lat=-41.33) == "JJA"


def test_wellington_july_is_southern_winter():
    assert season_from_date("2026-07-15", lat=-41.33) == "DJF"


def test_singapore_january_is_djf_equatorial_nh():
    """Singapore is marginally NH (lat=1.35); January should return DJF, not JJA."""
    assert season_from_date("2026-01-15", lat=1.35) == "DJF"


def test_exact_equator_is_djf_january():
    """lat=0.0 folds to NH via the lat >= 0 threshold."""
    assert season_from_date("2026-01-15", lat=0.0) == "DJF"


def test_season_from_month_canonical_location():
    """season_from_month lives only in src/calibration/manager.py."""
    from src.calibration.manager import season_from_month as canonical
    assert callable(canonical)
    # Wellington January should return JJA via season_from_month too
    assert canonical(1, lat=-41.3) == "JJA"
    assert canonical(7, lat=-41.3) == "DJF"
    assert canonical(1, lat=40.0) == "DJF"


def test_no_duplicate_sh_flip_in_diurnal():
    """src/signal/diurnal.py must NOT define its own _SH_FLIP — must import."""
    diurnal_path = REPO_ROOT / "src" / "signal" / "diurnal.py"
    content = diurnal_path.read_text()
    # _SH_FLIP as an assignment (not as a comment or docstring reference)
    assert "_SH_FLIP =" not in content, "Duplicate _SH_FLIP assignment in diurnal.py"
    assert "_SH_FLIP: " not in content, "Duplicate _SH_FLIP type annotation in diurnal.py"
    assert "def season_from_month" not in content, "Duplicate season_from_month definition in diurnal.py"


def test_replay_season_calls_pass_lat():
    """src/engine/replay.py every season_from_month call must include lat= kwarg."""
    replay_path = REPO_ROOT / "src" / "engine" / "replay.py"
    content = replay_path.read_text()
    calls = re.findall(r'season_from_month\([^)]*\)', content)
    for call in calls:
        assert 'lat=' in call, f"season_from_month call missing lat= kwarg: {call}"


# ==================== DST gap detection (Bug 2 antibody) ====================

def test_is_missing_local_hour_london_spring_forward():
    """London 2025-03-30 01:30 is inside the spring-forward gap."""
    from src.signal.diurnal import _is_missing_local_hour
    assert _is_missing_local_hour(datetime(2025, 3, 30, 1, 30), ZoneInfo("Europe/London")) == True


def test_is_missing_local_hour_normal_summer_hour():
    from src.signal.diurnal import _is_missing_local_hour
    assert _is_missing_local_hour(datetime(2025, 6, 15, 1, 30), ZoneInfo("Europe/London")) == False


def test_is_missing_local_hour_fall_back_is_not_missing():
    """London 2025-10-26 01:30 is AMBIGUOUS (fold) but NOT missing."""
    from src.signal.diurnal import _is_missing_local_hour
    assert _is_missing_local_hour(datetime(2025, 10, 26, 1, 30), ZoneInfo("Europe/London")) == False


# ==================== Schema: availability_fact kernel-accurate ====================

def test_availability_fact_table_present_after_init():
    """After init_schema, availability_fact exists with kernel-accurate columns."""
    from src.state.db import get_world_connection, init_schema
    conn = get_world_connection()
    init_schema(conn)
    # Kernel schema has EXACTLY these 8 columns (NOT 9 — no recorded_at)
    expected_cols = {
        'availability_id',
        'scope_type',
        'scope_key',
        'failure_type',
        'started_at',
        'ended_at',
        'impact',
        'details_json',
    }
    cols = {row[1] for row in conn.execute("PRAGMA table_info(availability_fact)")}
    missing = expected_cols - cols
    unexpected = cols - expected_cols
    assert not missing, f"availability_fact missing columns: {missing}"
    assert not unexpected, f"availability_fact has unexpected columns: {unexpected}"


def test_chronicle_env_roundtrip():
    """Chronicle env column: INSERT + SELECT with env='live' round-trips."""
    from src.state.db import get_world_connection, init_schema
    conn = get_world_connection()
    init_schema(conn)
    conn.execute(
        "INSERT INTO chronicle (event_type, timestamp, details_json, env) VALUES (?, ?, ?, ?)",
        ("K0_SMOKE_TEST", "2026-04-12T00:00:00Z", "{}", "live"),
    )
    row = conn.execute(
        "SELECT event_type, env FROM chronicle WHERE event_type = 'K0_SMOKE_TEST'"
    ).fetchone()
    assert row is not None
    assert row[1] == "live"
    # Cleanup
    conn.execute("DELETE FROM chronicle WHERE event_type = 'K0_SMOKE_TEST'")
    conn.commit()


# ==================== HKO settlement routing ====================

def test_hong_kong_is_hko_source_type():
    hk = cities_by_name["Hong Kong"]
    assert hk.settlement_source_type == "hko"


def test_all_non_hk_cities_are_wu_icao():
    for name, city in cities_by_name.items():
        if name == "Hong Kong":
            continue
        assert city.settlement_source_type == "wu_icao", f"{name}: {city.settlement_source_type}"


# ==================== Route-to-bucket smoke test ====================

def test_route_to_bucket_works_for_all_cities():
    """route_to_bucket is callable for all 46 cities without error.

    This is a smoke test that the cluster + season derivation chain is
    wired up correctly after K0. K3 will collapse cluster to city name;
    at K0 time we only assert the chain does not raise.
    """
    from src.calibration.manager import route_to_bucket
    for name, city in cities_by_name.items():
        bucket = route_to_bucket(city, "2026-07-15")
        assert bucket, f"{name}: empty bucket key"
        # After K0 the cluster may still be a region name; that's fine, K3 fixes it
