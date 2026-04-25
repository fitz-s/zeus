# Created: 2026-04-21
# Lifecycle: created=2026-04-21; last_reviewed=2026-04-25; last_reused=2026-04-25
# Last reused/audited: 2026-04-25
# Authority basis: plan v3 antibodies A1/A2; P1 obs_v2 provenance identity packet.
# Purpose: Pin observation_instants_v2 writer provenance and source-role semantics.
# Reuse: Inspect P1.2 packet, tier_resolver registry, and test topology first.
# Authority basis: plan v3 antibodies A1/A2 (.omc/plans/observation-instants-
#                  migration-iter3.md L119-120); step2 Phase 0 file #9.
"""Antibody A1 (missing provenance) + A2 (source-tier consistency) for the
observation_instants_v2 writer.

HK-specific A6 lives in ``tests/test_hk_rejects_vhhh_source.py`` so the
regression for the exact VHHH/WU category error is pinned separately.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.data.observation_instants_v2_writer import (
    InvalidObsV2RowError,
    ObsV2Row,
    insert_rows,
)
from src.data.tier_resolver import (
    SOURCE_ROLE_FALLBACK_EVIDENCE,
    SOURCE_ROLE_HISTORICAL_HOURLY,
    Tier,
    tier_for_city,
)
from src.state.schema.v2_schema import apply_v2_schema


def _valid_provenance(**overrides) -> str:
    data = {
        "tier": "WU_ICAO",
        "station_id": "KORD",
        "payload_hash": "sha256:" + "a" * 64,
        "source_url": "https://api.weather.com/v1/location/KORD:9:US/observations/historical.json?apiKey=REDACTED",
        "parser_version": "test_obs_v2_writer_v1",
    }
    data.update(overrides)
    return json.dumps(data, sort_keys=True)


def _minimal_valid_kwargs(**overrides) -> dict:
    """A Chicago WU row that passes all validators; tests override one field."""
    base = dict(
        city="Chicago",
        target_date="2024-01-15",
        source="wu_icao_history",
        timezone_name="America/Chicago",
        local_timestamp="2024-01-15T08:00:00-06:00",
        utc_timestamp="2024-01-15T14:00:00+00:00",
        utc_offset_minutes=-360,
        time_basis="utc_hour_aligned",
        temp_unit="F",
        imported_at="2026-04-21T23:30:00+00:00",
        authority="VERIFIED",
        data_version="v1.wu-native.pilot",
        provenance_json=_valid_provenance(),
        temp_current=32.0,
        running_max=34.0,
        station_id="KORD",
    )
    base.update(overrides)
    return base


def _make_row(**overrides) -> ObsV2Row:
    return ObsV2Row(**_minimal_valid_kwargs(**overrides))


def _make_row_with_payload_hash(payload_hash: str, **overrides) -> ObsV2Row:
    return _make_row(
        provenance_json=_valid_provenance(payload_hash=payload_hash),
        **overrides,
    )


def _source_semantics(
    conn: sqlite3.Connection,
    *,
    city: str,
    source: str,
) -> tuple[str | None, int | None, str | None]:
    row = conn.execute(
        """
        SELECT source_role, training_allowed, causality_status
        FROM observation_instants_v2
        WHERE city=? AND source=?
        """,
        (city, source),
    ).fetchone()
    assert row is not None, f"missing row for city={city!r}, source={source!r}"
    return row


# ----------------------------------------------------------------------
# Baseline: minimal valid row passes
# ----------------------------------------------------------------------


def test_minimal_valid_row_constructs_without_error():
    row = _make_row()
    assert row.city == "Chicago"
    assert row.authority == "VERIFIED"


# ----------------------------------------------------------------------
# A1: missing / wrong authority
# ----------------------------------------------------------------------


@pytest.mark.parametrize("bad_authority", ["UNVERIFIED", "QUARANTINED", "", "random"])
def test_a1_rejects_non_write_authority(bad_authority):
    with pytest.raises(InvalidObsV2RowError, match="A1 violation.*authority"):
        _make_row(authority=bad_authority)


def test_a1_accepts_icao_station_native():
    """HK forward-only rows use ICAO_STATION_NATIVE; writer must allow it.

    This is the write-side counterpart to A4 reader filter.
    """
    # Construct an HK row with valid HK-specific source so we exercise the
    # authority check specifically.
    row = ObsV2Row(
        **_minimal_valid_kwargs(
            city="Hong Kong",
            source="hko_hourly_accumulator",
            authority="ICAO_STATION_NATIVE",
            provenance_json=_valid_provenance(
                tier="HKO_NATIVE",
                station_id="HKO",
                source_file="hko_hourly_accumulator",
                source_url="",
            ),
            station_id="HKO",
            timezone_name="Asia/Hong_Kong",
            local_timestamp="2024-01-15T22:00:00+08:00",
            utc_timestamp="2024-01-15T14:00:00+00:00",
            utc_offset_minutes=480,
        )
    )
    assert row.authority == "ICAO_STATION_NATIVE"


# ----------------------------------------------------------------------
# A1: data_version
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_version",
    [
        "",  # empty
        "v0",  # pre-cutover sentinel — MUST NOT appear on a row
        "v2.future",  # wrong family
        "wu-native",  # missing prefix
        "V1.wu-native",  # case-sensitive
    ],
)
def test_a1_rejects_bad_data_version(bad_version):
    with pytest.raises(InvalidObsV2RowError, match="A1 violation.*data_version"):
        _make_row(data_version=bad_version)


@pytest.mark.parametrize(
    "good_version",
    ["v1.wu-native.pilot", "v1.wu-native", "v1.hk-accumulator.forward"],
)
def test_a1_accepts_v1_family(good_version):
    _make_row(data_version=good_version)  # must not raise


# ----------------------------------------------------------------------
# A1: provenance_json
# ----------------------------------------------------------------------


def test_a1_rejects_empty_provenance():
    with pytest.raises(InvalidObsV2RowError, match="A1 violation.*provenance_json"):
        _make_row(provenance_json="")


def test_a1_rejects_default_provenance():
    with pytest.raises(InvalidObsV2RowError, match="A1 violation.*provenance_json"):
        _make_row(provenance_json="{}")


def test_a1_rejects_non_json_provenance():
    with pytest.raises(InvalidObsV2RowError, match="A1 violation.*not.*valid JSON"):
        _make_row(provenance_json="{not json")


def test_a1_rejects_non_object_provenance():
    with pytest.raises(InvalidObsV2RowError, match="A1 violation.*must be.*JSON object"):
        _make_row(provenance_json=json.dumps([1, 2, 3]))


def test_a1_rejects_provenance_without_tier_key():
    with pytest.raises(InvalidObsV2RowError, match="A1 violation.*tier.*key"):
        _make_row(provenance_json=json.dumps({"icao": "KORD"}))


@pytest.mark.parametrize(
    "provenance, missing",
    [
        ({"tier": "WU_ICAO", "station_id": "KORD", "source_url": "url", "parser_version": "parser"}, "payload_hash"),
        ({"tier": "WU_ICAO", "station_id": "KORD", "payload_hash": "sha256:x", "parser_version": "parser"}, "source_url\\|source_file"),
        ({"tier": "WU_ICAO", "payload_hash": "sha256:x", "source_url": "url", "parser_version": "parser"}, "station_id\\|station_registry_version\\|station_registry_hash"),
        ({"tier": "WU_ICAO", "station_id": "KORD", "payload_hash": "sha256:x", "source_url": "url"}, "parser_version"),
    ],
)
def test_a1_rejects_provenance_missing_payload_identity(provenance, missing):
    with pytest.raises(InvalidObsV2RowError, match=missing):
        _make_row(provenance_json=json.dumps(provenance))


# ----------------------------------------------------------------------
# A2: source-tier consistency
# ----------------------------------------------------------------------


def test_a2_wu_city_rejects_ogimet_source():
    with pytest.raises(InvalidObsV2RowError, match="A2 violation.*WU_ICAO"):
        _make_row(city="Chicago", source="ogimet_metar_uuww")


def test_a2_wu_city_rejects_openmeteo_source():
    """The Day-0 ghost-trade root: openmeteo_archive_hourly rows leaking in."""
    with pytest.raises(InvalidObsV2RowError, match="A2 violation"):
        _make_row(source="openmeteo_archive_hourly")


def test_a2_ogimet_city_accepts_correct_source():
    row = _make_row(
        city="Moscow",
        source="ogimet_metar_uuww",
        timezone_name="Europe/Moscow",
        local_timestamp="2024-01-15T17:00:00+03:00",
        utc_timestamp="2024-01-15T14:00:00+00:00",
        utc_offset_minutes=180,
        station_id="UUWW",
        provenance_json=_valid_provenance(
            tier="OGIMET_METAR",
            station_id="UUWW",
            source_url="https://www.ogimet.com/cgi-bin/getmetar?icao=UUWW",
            parser_version="test_obs_v2_writer_ogimet_v1",
        ),
    )
    assert tier_for_city(row.city) is Tier.OGIMET_METAR


def test_a2_ogimet_city_rejects_wrong_station_source():
    """Moscow (UUWW) cannot be written with LLBG (Tel Aviv) source tag."""
    with pytest.raises(InvalidObsV2RowError, match="A2 violation"):
        _make_row(
            city="Moscow",
            source="ogimet_metar_llbg",  # wrong station for Moscow
            timezone_name="Europe/Moscow",
            local_timestamp="2024-01-15T17:00:00+03:00",
            utc_timestamp="2024-01-15T14:00:00+00:00",
            utc_offset_minutes=180,
        )


def test_a2_unknown_city_rejected():
    with pytest.raises(InvalidObsV2RowError, match="A2 violation.*no tier mapping"):
        _make_row(city="Atlantis")


# ----------------------------------------------------------------------
# Structural sanity
# ----------------------------------------------------------------------


def test_rejects_bad_time_basis():
    with pytest.raises(InvalidObsV2RowError, match="time_basis"):
        _make_row(time_basis="random")


def test_rejects_bad_temp_unit():
    with pytest.raises(InvalidObsV2RowError, match="temp_unit"):
        _make_row(temp_unit="K")


def test_rejects_bad_target_date():
    with pytest.raises(InvalidObsV2RowError, match="target_date"):
        _make_row(target_date="2024/01/15")


def test_rejects_bad_utc_timestamp():
    with pytest.raises(InvalidObsV2RowError, match="utc_timestamp"):
        _make_row(utc_timestamp="not a timestamp")


# ----------------------------------------------------------------------
# Batch insert against an in-memory DB
# ----------------------------------------------------------------------


@pytest.fixture
def mem_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_v2_schema(conn)
    yield conn
    conn.close()


def test_insert_rows_writes_expected_row(mem_db):
    row = _make_row()
    n = insert_rows(mem_db, [row])
    assert n == 1
    (count,) = mem_db.execute(
        "SELECT COUNT(*) FROM observation_instants_v2 WHERE city=?",
        (row.city,),
    ).fetchone()
    assert count == 1


def test_insert_rows_empty_batch_is_noop(mem_db):
    n = insert_rows(mem_db, [])
    assert n == 0


def test_apply_v2_schema_creates_obs_v2_revision_surfaces(mem_db):
    tables = {
        row[0]
        for row in mem_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    indexes = {
        row[0]
        for row in mem_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }

    assert "observation_revisions" in tables
    assert "idx_observation_revisions_obs_v2_lookup" in indexes
    assert "ux_observation_revisions_payload" in indexes


def test_apply_v2_schema_is_idempotent_with_existing_obs_v2_revision_rows(mem_db):
    mem_db.execute(
        """
        INSERT INTO observation_revisions (
            table_name, city, target_date, source, utc_timestamp,
            existing_row_id, existing_payload_hash, incoming_payload_hash,
            reason, writer, existing_row_json, incoming_row_json
        ) VALUES (
            'observation_instants_v2', 'Chicago', '2024-01-15',
            'wu_icao_history', '2024-01-15T14:00:00+00:00',
            1, 'sha256:old', 'sha256:new',
            'payload_hash_mismatch', 'unit-test', '{}', '{}'
        )
        """
    )
    mem_db.commit()

    apply_v2_schema(mem_db)

    (count,) = mem_db.execute(
        "SELECT COUNT(*) FROM observation_revisions"
    ).fetchone()
    assert count == 1


def test_insert_rows_duplicate_payload_hash_is_noop(mem_db):
    """Same natural key + same payload hash is an idempotent rerun."""
    payload_hash = "sha256:" + "a" * 64
    r1 = _make_row_with_payload_hash(
        payload_hash,
        temp_current=30.0,
        imported_at="2026-04-21T23:30:00+00:00",
    )
    r2 = _make_row_with_payload_hash(
        payload_hash,
        temp_current=30.0,
        imported_at="2026-04-22T00:00:00+00:00",
    )
    assert insert_rows(mem_db, [r1]) == 1
    (first_id,) = mem_db.execute(
        "SELECT id FROM observation_instants_v2"
    ).fetchone()
    assert insert_rows(mem_db, [r2]) == 0

    row = mem_db.execute(
        "SELECT id, temp_current, imported_at FROM observation_instants_v2"
    ).fetchone()
    (revision_count,) = mem_db.execute(
        "SELECT COUNT(*) FROM observation_revisions"
    ).fetchone()

    assert row == (first_id, 30.0, "2026-04-21T23:30:00+00:00")
    assert revision_count == 0


def test_insert_rows_changed_payload_hash_records_revision_without_overwrite(mem_db):
    """Different hash on the same key is history, not a silent overwrite."""
    r1 = _make_row_with_payload_hash("sha256:" + "a" * 64, temp_current=30.0)
    r2 = _make_row_with_payload_hash("sha256:" + "b" * 64, temp_current=35.0)
    assert insert_rows(mem_db, [r1]) == 1
    assert insert_rows(mem_db, [r2]) == 0

    (count,) = mem_db.execute(
        "SELECT COUNT(*) FROM observation_instants_v2"
    ).fetchone()
    assert count == 1
    current = mem_db.execute(
        "SELECT temp_current, provenance_json FROM observation_instants_v2"
    ).fetchone()
    assert current[0] == 30.0
    assert json.loads(current[1])["payload_hash"] == "sha256:" + "a" * 64

    revision = mem_db.execute(
        """
        SELECT existing_payload_hash, incoming_payload_hash,
               existing_row_json, incoming_row_json
        FROM observation_revisions
        WHERE table_name='observation_instants_v2'
        """
    ).fetchone()
    assert revision is not None
    assert revision[0] == "sha256:" + "a" * 64
    assert revision[1] == "sha256:" + "b" * 64
    assert json.loads(revision[2])["temp_current"] == 30.0
    assert json.loads(revision[3])["temp_current"] == 35.0


def test_insert_rows_rejects_reused_payload_hash_with_changed_material_fields(mem_db):
    payload_hash = "sha256:" + "a" * 64
    r1 = _make_row_with_payload_hash(payload_hash, temp_current=30.0)
    r2 = _make_row_with_payload_hash(payload_hash, temp_current=35.0)

    insert_rows(mem_db, [r1])
    with pytest.raises(InvalidObsV2RowError, match="payload_hash.*material"):
        insert_rows(mem_db, [r2])

    (temp,) = mem_db.execute(
        "SELECT temp_current FROM observation_instants_v2"
    ).fetchone()
    (revision_count,) = mem_db.execute(
        "SELECT COUNT(*) FROM observation_revisions"
    ).fetchone()
    assert temp == 30.0
    assert revision_count == 0


def test_insert_rows_rolls_back_revision_history_on_failure(mem_db):
    r1 = _make_row_with_payload_hash("sha256:" + "a" * 64, temp_current=30.0)
    r2 = _make_row_with_payload_hash("sha256:" + "b" * 64, temp_current=35.0)

    insert_rows(mem_db, [r1])
    mem_db.execute(
        """
        CREATE TRIGGER fail_revision_insert
        BEFORE INSERT ON observation_revisions
        BEGIN
            SELECT RAISE(FAIL, 'forced revision failure');
        END
        """
    )

    with pytest.raises(sqlite3.Error, match="forced revision failure"):
        insert_rows(mem_db, [r2])

    (temp,) = mem_db.execute(
        "SELECT temp_current FROM observation_instants_v2"
    ).fetchone()
    (revision_count,) = mem_db.execute(
        "SELECT COUNT(*) FROM observation_revisions"
    ).fetchone()
    assert temp == 30.0
    assert revision_count == 0


def test_obs_v2_writer_source_contains_no_insert_or_replace():
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "data"
        / "observation_instants_v2_writer.py"
    ).read_text(encoding="utf-8")
    assert "INSERT OR REPLACE" not in source.upper()


def test_insert_rows_round_trip_preserves_provenance(mem_db):
    row = _make_row()
    insert_rows(mem_db, [row])
    (auth, dv, prov) = mem_db.execute(
        "SELECT authority, data_version, provenance_json FROM observation_instants_v2"
    ).fetchone()
    assert auth == "VERIFIED"
    assert dv == "v1.wu-native.pilot"
    parsed = json.loads(prov)
    assert parsed["tier"] == "WU_ICAO"
    assert parsed["station_id"] == "KORD"
    assert parsed["payload_hash"].startswith("sha256:")
    assert parsed["parser_version"] == "test_obs_v2_writer_v1"


def test_insert_rows_round_trip_primary_wu_persists_source_role_training_ok(mem_db):
    row = _make_row()
    insert_rows(mem_db, [row])

    assert _source_semantics(mem_db, city=row.city, source=row.source) == (
        SOURCE_ROLE_HISTORICAL_HOURLY,
        1,
        "OK",
    )


def test_insert_rows_round_trip_primary_ogimet_city_persists_source_role_training_ok(
    mem_db,
):
    row = _make_row(
        city="Moscow",
        source="ogimet_metar_uuww",
        timezone_name="Europe/Moscow",
        local_timestamp="2024-01-15T17:00:00+03:00",
        utc_timestamp="2024-01-15T14:00:00+00:00",
        utc_offset_minutes=180,
        station_id="UUWW",
        provenance_json=_valid_provenance(
            tier="OGIMET_METAR",
            station_id="UUWW",
            source_url="https://www.ogimet.com/cgi-bin/getmetar?icao=UUWW",
            parser_version="test_obs_v2_writer_ogimet_v1",
        ),
    )
    insert_rows(mem_db, [row])

    assert _source_semantics(mem_db, city=row.city, source=row.source) == (
        SOURCE_ROLE_HISTORICAL_HOURLY,
        1,
        "OK",
    )


@pytest.mark.parametrize(
    "fallback_source",
    ["ogimet_metar_kord", "meteostat_bulk_kord"],
)
def test_insert_rows_round_trip_wu_fallback_persists_runtime_only_source_role(
    mem_db,
    fallback_source,
):
    row = _make_row(
        source=fallback_source,
        provenance_json=_valid_provenance(
            fallback=fallback_source,
            source_url=f"https://example.invalid/{fallback_source}",
        ),
    )
    insert_rows(mem_db, [row])

    assert _source_semantics(mem_db, city=row.city, source=row.source) == (
        SOURCE_ROLE_FALLBACK_EVIDENCE,
        0,
        "RUNTIME_ONLY_FALLBACK",
    )


def test_insert_rows_round_trip_hko_persists_source_reaudit_status(mem_db):
    row = ObsV2Row(
        **_minimal_valid_kwargs(
            city="Hong Kong",
            source="hko_hourly_accumulator",
            authority="ICAO_STATION_NATIVE",
            data_version="v1.hk-accumulator.forward",
            provenance_json=_valid_provenance(
                tier="HKO_NATIVE",
                station_id="HKO",
                source_file="hko_hourly_accumulator",
                source_url="",
                parser_version="test_obs_v2_writer_hko_v1",
            ),
            station_id="HKO",
            timezone_name="Asia/Hong_Kong",
            local_timestamp="2024-01-15T22:00:00+08:00",
            utc_timestamp="2024-01-15T14:00:00+00:00",
            utc_offset_minutes=480,
            time_basis="hourly_accumulator",
            temp_unit="C",
        )
    )
    insert_rows(mem_db, [row])

    assert _source_semantics(mem_db, city=row.city, source=row.source) == (
        SOURCE_ROLE_FALLBACK_EVIDENCE,
        0,
        "REQUIRES_SOURCE_REAUDIT",
    )


def test_view_stays_empty_until_zeus_meta_flips(mem_db):
    """observation_instants_current VIEW returns 0 rows pre-cutover.

    Pilot data_version='v1.wu-native.pilot' does NOT match
    zeus_meta.observation_data_version='v0', so the VIEW hides it.
    This is the core atomic-cutover invariant — Phase 2 flips this.
    """
    row = _make_row()  # data_version='v1.wu-native.pilot'
    insert_rows(mem_db, [row])
    (view_count,) = mem_db.execute(
        "SELECT COUNT(*) FROM observation_instants_current"
    ).fetchone()
    assert view_count == 0, "VIEW must be empty pre-cutover"
    (table_count,) = mem_db.execute(
        "SELECT COUNT(*) FROM observation_instants_v2"
    ).fetchone()
    assert table_count == 1, "Base table has the row"


def test_view_activates_after_zeus_meta_flip(mem_db):
    """Phase 2 simulation: flipping zeus_meta instantly exposes rows."""
    row = _make_row()
    insert_rows(mem_db, [row])
    mem_db.execute(
        "UPDATE zeus_meta SET value='v1.wu-native.pilot' "
        "WHERE key='observation_data_version'"
    )
    (view_count,) = mem_db.execute(
        "SELECT COUNT(*) FROM observation_instants_current"
    ).fetchone()
    assert view_count == 1
