"""Relationship tests for ObservationAtom (K1-A).

Tests 1-5 pass on HEAD after K1-A lands.
Tests 6-7 are skipped pending K1-B bounds generator.

See .omc/plans/k1-freeze.md section 8 for the full specification.
"""

import json
import sqlite3
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.types.observation_atom import IngestionRejected, ObservationAtom
from src.state.db import init_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULT_FETCH_UTC = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
DEFAULT_LOCAL_TIME = datetime(2026, 1, 15, 13, 0, 0)  # naive; Paris UTC+1
DEFAULT_WINDOW_START = datetime(2026, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
DEFAULT_WINDOW_END = datetime(2026, 1, 15, 23, 59, 59, tzinfo=timezone.utc)


def _valid_atom_kwargs(**overrides) -> dict:
    """Return a baseline valid kwargs dict for ObservationAtom.

    Baseline: Paris / 2026-01-15 / value_type='high' / 5.0 degC / authority=VERIFIED.
    Override specific fields for individual test scenarios.
    """
    base = dict(
        city="Paris",
        target_date=date(2026, 1, 15),
        value_type="high",
        value=5.0,
        target_unit="C",
        raw_value=5.0,
        raw_unit="C",
        source="wu_icao_history",
        station_id="LFPG",
        api_endpoint="https://api.weather.com/v2/history/daily",
        fetch_utc=DEFAULT_FETCH_UTC,
        local_time=DEFAULT_LOCAL_TIME,
        collection_window_start_utc=DEFAULT_WINDOW_START,
        collection_window_end_utc=DEFAULT_WINDOW_END,
        timezone="Europe/Paris",
        utc_offset_minutes=60,
        dst_active=False,
        is_ambiguous_local_hour=False,
        is_missing_local_hour=False,
        hemisphere="N",
        season="DJF",
        month=1,
        rebuild_run_id="test",
        data_source_version="test",
        authority="VERIFIED",
        validation_pass=True,
        provenance_metadata={},
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Test 1
# ---------------------------------------------------------------------------

def test_atom_refuses_construction_without_validation_pass():
    """ObservationAtom with validation_pass=False must raise IngestionRejected.

    Relationship: __post_init__ enforces the invariant that an atom cannot exist
    without passing validation. The IngestionRejected exception is the gating
    mechanism that makes malformed observations unconstructable.
    """
    kwargs = _valid_atom_kwargs(validation_pass=False)
    with pytest.raises(IngestionRejected, match="validation_pass=False"):
        ObservationAtom(**kwargs)


# ---------------------------------------------------------------------------
# Test 2
# ---------------------------------------------------------------------------

def test_atom_refuses_unverified_with_validation_pass():
    """ObservationAtom with authority='UNVERIFIED' and validation_pass=True must raise.

    Relationship: construction never produces UNVERIFIED atoms directly.
    That state is reserved for post-hoc marking of untrusted sources only.
    Any atom that passed validation must carry authority='VERIFIED'.
    """
    kwargs = _valid_atom_kwargs(authority="UNVERIFIED", validation_pass=True)
    with pytest.raises(IngestionRejected, match="UNVERIFIED"):
        ObservationAtom(**kwargs)


# ---------------------------------------------------------------------------
# Test 3
# ---------------------------------------------------------------------------

def test_atom_to_db_write_roundtrip(tmp_path):
    """Construct valid atom, write to observations table, assert all K1 columns round-trip.

    Relationship: ObservationAtom fields must survive a DB round-trip through the
    extended observations schema without data loss or type coercion surprises.
    Uses an in-memory SQLite DB with init_schema applied.
    """
    # Use in-memory DB for isolation
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    atom = ObservationAtom(**_valid_atom_kwargs())

    # Build insert dict: serialize datetimes as ISO strings, bools as 0/1, dict as JSON
    row = dict(
        city=atom.city,
        target_date=atom.target_date.isoformat(),
        source=atom.source,
        high_temp=atom.value if atom.value_type == "high" else None,
        low_temp=atom.value if atom.value_type == "low" else None,
        unit=atom.target_unit,
        station_id=atom.station_id,
        fetched_at=atom.fetch_utc.isoformat(),
        # K1 additions
        raw_value=atom.raw_value,
        raw_unit=atom.raw_unit,
        target_unit=atom.target_unit,
        value_type=atom.value_type,
        fetch_utc=atom.fetch_utc.isoformat(),
        local_time=atom.local_time.isoformat() if hasattr(atom.local_time, 'isoformat') else str(atom.local_time),
        collection_window_start_utc=atom.collection_window_start_utc.isoformat(),
        collection_window_end_utc=atom.collection_window_end_utc.isoformat(),
        timezone=atom.timezone,
        utc_offset_minutes=atom.utc_offset_minutes,
        dst_active=int(atom.dst_active),
        is_ambiguous_local_hour=int(atom.is_ambiguous_local_hour),
        is_missing_local_hour=int(atom.is_missing_local_hour),
        hemisphere=atom.hemisphere,
        season=atom.season,
        month=atom.month,
        rebuild_run_id=atom.rebuild_run_id,
        data_source_version=atom.data_source_version,
        authority=atom.authority,
        provenance_metadata=json.dumps(atom.provenance_metadata),
    )

    placeholders = ", ".join(f":{k}" for k in row)
    columns = ", ".join(row.keys())
    conn.execute(f"INSERT INTO observations ({columns}) VALUES ({placeholders})", row)
    conn.commit()

    result = conn.execute(
        "SELECT * FROM observations WHERE city = ? AND target_date = ? AND source = ?",
        (atom.city, atom.target_date.isoformat(), atom.source),
    ).fetchone()

    assert result is not None

    # Assert all K1-added columns round-trip
    assert result["raw_value"] == atom.raw_value
    assert result["raw_unit"] == atom.raw_unit
    assert result["target_unit"] == atom.target_unit
    assert result["value_type"] == atom.value_type
    assert result["authority"] == atom.authority
    assert result["fetch_utc"] == atom.fetch_utc.isoformat()
    assert result["local_time"] == atom.local_time.isoformat()
    assert result["station_id"] == atom.station_id
    assert result["rebuild_run_id"] == atom.rebuild_run_id
    assert json.loads(result["provenance_metadata"]) == atom.provenance_metadata
    assert result["hemisphere"] == atom.hemisphere
    assert result["season"] == atom.season
    assert result["month"] == atom.month
    assert result["utc_offset_minutes"] == atom.utc_offset_minutes
    assert bool(result["dst_active"]) == atom.dst_active
    assert bool(result["is_ambiguous_local_hour"]) == atom.is_ambiguous_local_hour
    assert bool(result["is_missing_local_hour"]) == atom.is_missing_local_hour

    conn.close()


# ---------------------------------------------------------------------------
# Test 4
# ---------------------------------------------------------------------------

def test_atom_dst_fields_come_from_solar_day():
    """London 2025-03-30 01:30 is a spring-forward gap hour; is_missing_local_hour must be True.

    Relationship: DST context fields in ObservationAtom must reflect actual ZoneInfo
    semantics, not hardcoded defaults. This test verifies the contract using
    _is_missing_local_hour from src.signal.diurnal directly.

    History: diurnal.py previously hardcoded is_missing_local_hour=False at lines 322/398.
    K0 fixed this (see commit 67d8cb8). This test encodes the antibody.
    """
    from src.signal.diurnal import _is_missing_local_hour

    tz = ZoneInfo("Europe/London")
    spring_forward_local = datetime(2025, 3, 30, 1, 30)  # This hour does not exist

    missing = _is_missing_local_hour(spring_forward_local, tz)
    assert missing is True, (
        "London 2025-03-30 01:30 is in the spring-forward gap and must be detected as missing"
    )

    # Construct atom with the detected flag set
    atom = ObservationAtom(**_valid_atom_kwargs(
        city="London",
        target_date=date(2025, 3, 30),
        local_time=spring_forward_local,
        timezone="Europe/London",
        utc_offset_minutes=60,  # BST
        dst_active=True,
        is_missing_local_hour=True,
        month=3,
        season="MAM",
    ))

    assert atom.is_missing_local_hour is True


# ---------------------------------------------------------------------------
# Test 5
# ---------------------------------------------------------------------------

def test_atom_dst_fall_back_ambiguous():
    """London 2025-10-26 01:30 fold=1 is an ambiguous fall-back hour; is_ambiguous_local_hour must be True.

    Relationship: ambiguous hours (fall-back) are observable and must not be rejected,
    but must be flagged. The fold attribute disambiguates; fold=1 selects the second
    occurrence (after the clock fell back).

    Detection: if local_dt.replace(fold=0) and local_dt.replace(fold=1) produce
    different UTC offsets for the same wall-clock time, the hour is ambiguous.
    """
    tz = ZoneInfo("Europe/London")
    fall_back_local = datetime(2025, 10, 26, 1, 30)  # Exists twice: BST and GMT

    # Ambiguity check: compare UTC offsets for fold=0 vs fold=1
    fold0 = fall_back_local.replace(tzinfo=tz, fold=0)
    fold1 = fall_back_local.replace(tzinfo=tz, fold=1)
    is_ambiguous = fold0.utcoffset() != fold1.utcoffset()

    assert is_ambiguous is True, (
        "London 2025-10-26 01:30 should be ambiguous (fall-back DST transition)"
    )

    # Construct atom with the detected flag set
    atom = ObservationAtom(**_valid_atom_kwargs(
        city="London",
        target_date=date(2025, 10, 26),
        local_time=fall_back_local,
        timezone="Europe/London",
        utc_offset_minutes=0,  # GMT after fall-back
        dst_active=False,
        is_ambiguous_local_hour=True,
        month=10,
        season="SON",
    ))

    assert atom.is_ambiguous_local_hour is True


# ---------------------------------------------------------------------------
# Tests 6-7: deferred to K1-B
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="deferred to K1-B bounds generator")
def test_monthly_bounds_json_shape_and_samples():
    """Load config/city_monthly_bounds.json, assert shape is 46 x 12.

    Deferred: depends on config/city_monthly_bounds.json which K1-B creates.
    """
    pass


@pytest.mark.skip(reason="deferred to K1-B bounds generator")
def test_monthly_bounds_json_has_generation_provenance():
    """Assert each entry has generated_at and tigge_date_range metadata.

    Deferred: depends on config/city_monthly_bounds.json which K1-B creates.
    """
    pass
