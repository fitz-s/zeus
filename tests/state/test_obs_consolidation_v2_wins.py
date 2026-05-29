# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: /Users/leofitz/.claude/jobs/9ea6f95c/OBS_V2_CONSOLIDATION_PLAN.md task 8
#   (Fitz relationship test); replaces the now-obsolete dual-writer freshness
#   antibody (tests/state/test_dual_writer_observation_instants_invariant.py) whose
#   premise — TWO observation tables — the consolidation deliberately eliminates.
"""Relationship antibody: observation_instants_v2 → observation_instants v2-wins.

This is a RELATIONSHIP test (Fitz Core Methodology), not a function test. It does
not verify "given input X, output Y" on one module; it verifies a cross-module
INVARIANT that spans the live-migration and the settlement-read boundary:

    When a legacy (DST-WRONG) row and a v2 (DST-correct) row share the same
    natural key (city, source, utc_timestamp), the consolidation migration MUST
    keep the v2 value — and the downstream settlement reader
    (day0_observation_reader.read_day0_observed_extrema_v2) MUST then return the
    v2 (correct) running_max, NOT the legacy (wrong) one.

Why this category of bug matters (Fitz #4 — data provenance over code correctness):
  observation_instants_v2 carries the London-spring-forward DST fix. The legacy
  observation_instants may hold a DST-wrong value for the same instant. A naive
  UNION / INSERT-OR-REPLACE merge that let legacy win would be CODE-correct (it
  runs, it dedups) but DATA-wrong (it silently reintroduces the settlement bug
  for every DST city, every summer). The only acceptable merge is v2-wins, and
  the only way to KNOW it held is to read the post-merge value through the same
  reader settlement uses.

ANTIBODY PROOF:
  Regression injection: flip the migration's INSERT OR IGNORE to INSERT OR
  REPLACE (legacy-wins) — test_v2_value_survives_overlap_key FAILS because the
  surviving running_max becomes the legacy DST-wrong value.
  Green path: the shipped INSERT OR IGNORE keeps the v2 row; the reader returns
  the v2 running_max.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Same-key collision fixture: London spring-forward instant.
_CITY = "London"
_SOURCE = "wu_icao_history"
_UTC = "2025-03-30T01:00:00+00:00"
_TARGET_DATE = "2025-03-30"
_LEGACY_DST_WRONG_MAX = 9.9   # what a DST-wrong legacy row would store
_V2_CORRECT_MAX = 5.5         # the DST-corrected v2 value that MUST survive


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "_obs_consolidation_migration",
        _REPO_ROOT / "scripts" / "migrations" / "202605_consolidate_observation_instants_v2.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_premigration_world(conn: sqlite3.Connection) -> None:
    """Create the pre-consolidation world surface: legacy subset + v2 superset."""
    conn.execute(
        """
        CREATE TABLE observation_instants (
            id INTEGER PRIMARY KEY AUTOINCREMENT, city TEXT NOT NULL,
            target_date TEXT NOT NULL, source TEXT NOT NULL, timezone_name TEXT NOT NULL,
            local_hour REAL, local_timestamp TEXT NOT NULL, utc_timestamp TEXT NOT NULL,
            utc_offset_minutes INTEGER NOT NULL, dst_active INTEGER NOT NULL DEFAULT 0,
            is_ambiguous_local_hour INTEGER NOT NULL DEFAULT 0,
            is_missing_local_hour INTEGER NOT NULL DEFAULT 0, time_basis TEXT NOT NULL,
            temp_current REAL, running_max REAL, delta_rate_per_h REAL, temp_unit TEXT NOT NULL,
            station_id TEXT, observation_count INTEGER, raw_response TEXT, source_file TEXT,
            imported_at TEXT NOT NULL, UNIQUE(city, source, utc_timestamp)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE observation_instants_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT, city TEXT NOT NULL,
            target_date TEXT NOT NULL, source TEXT NOT NULL, timezone_name TEXT NOT NULL,
            local_hour REAL, local_timestamp TEXT NOT NULL, utc_timestamp TEXT NOT NULL,
            utc_offset_minutes INTEGER NOT NULL, dst_active INTEGER NOT NULL DEFAULT 0,
            is_ambiguous_local_hour INTEGER NOT NULL DEFAULT 0,
            is_missing_local_hour INTEGER NOT NULL DEFAULT 0, time_basis TEXT NOT NULL,
            temp_current REAL, running_max REAL, running_min REAL, delta_rate_per_h REAL,
            temp_unit TEXT NOT NULL, station_id TEXT, observation_count INTEGER,
            raw_response TEXT, source_file TEXT, imported_at TEXT NOT NULL,
            authority TEXT NOT NULL DEFAULT 'UNVERIFIED',
            data_version TEXT NOT NULL DEFAULT 'v1',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            temperature_metric TEXT, physical_quantity TEXT, observation_field TEXT,
            training_allowed INTEGER DEFAULT 1, causality_status TEXT DEFAULT 'OK',
            source_role TEXT, UNIQUE(city, source, utc_timestamp)
        )
        """
    )
    conn.execute(
        "CREATE INDEX idx_observation_instants_v2_city_ts "
        "ON observation_instants_v2(city, target_date, utc_timestamp)"
    )
    conn.execute("CREATE TABLE zeus_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO zeus_meta VALUES ('observation_data_version', 'v0')")
    conn.execute(
        """
        CREATE TABLE observation_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, table_name TEXT NOT NULL,
            city TEXT, source TEXT, utc_timestamp TEXT, recorded_at TEXT
        )
        """
    )

    # Legacy DST-WRONG row on the collision key.
    conn.execute(
        """
        INSERT INTO observation_instants
            (city, target_date, source, timezone_name, local_hour, local_timestamp,
             utc_timestamp, utc_offset_minutes, time_basis, temp_current, running_max,
             temp_unit, imported_at)
        VALUES (?, ?, ?, 'Europe/London', 1.0, '2025-03-30T01:00:00+00:00', ?, 0,
                'utc_hour_aligned', ?, ?, 'C', '2025-03-30T02:00:00+00:00')
        """,
        (_CITY, _TARGET_DATE, _SOURCE, _UTC, _LEGACY_DST_WRONG_MAX, _LEGACY_DST_WRONG_MAX),
    )
    # v2 DST-CORRECT row on the SAME key, VERIFIED (reader-trusted authority).
    conn.execute(
        """
        INSERT INTO observation_instants_v2
            (city, target_date, source, timezone_name, local_hour, local_timestamp,
             utc_timestamp, utc_offset_minutes, time_basis, temp_current, running_max,
             temp_unit, imported_at, authority, data_version, provenance_json)
        VALUES (?, ?, ?, 'Europe/London', 2.0, '2025-03-30T02:00:00+01:00', ?, 60,
                'utc_hour_bucket_extremum', ?, ?, 'C', '2025-03-30T03:00:00+00:00',
                'VERIFIED', 'v1.wu-native', '{"tier":"WU_ICAO"}')
        """,
        (_CITY, _TARGET_DATE, _SOURCE, _UTC, _V2_CORRECT_MAX, _V2_CORRECT_MAX),
    )
    conn.commit()


@pytest.fixture()
def migrated_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    _build_premigration_world(conn)
    mig = _load_migration()
    # Pre-merge receipts confirm the overlap exists (the relationship under test).
    receipts = mig.compute_receipts(conn)
    assert receipts["overlap_keys_v2_wins"] == 1, receipts
    mig.up(conn)
    conn.commit()
    return conn


def test_v2_value_survives_overlap_key(migrated_conn: sqlite3.Connection):
    """After migration the single surviving row carries the v2 (correct) value."""
    rows = migrated_conn.execute(
        "SELECT running_max, authority, data_version FROM observation_instants "
        "WHERE city = ? AND source = ? AND utc_timestamp = ?",
        (_CITY, _SOURCE, _UTC),
    ).fetchall()
    assert len(rows) == 1, f"expected exactly one merged row, got {rows}"
    running_max, authority, data_version = rows[0]
    assert running_max == _V2_CORRECT_MAX, (
        f"V2-WINS VIOLATED: surviving running_max={running_max} "
        f"(expected v2-correct {_V2_CORRECT_MAX}, NOT legacy-DST-wrong "
        f"{_LEGACY_DST_WRONG_MAX}). A legacy-wins merge silently reintroduces the "
        "London spring-forward settlement bug."
    )
    assert authority == "VERIFIED", f"expected VERIFIED (v2), got {authority!r}"
    assert data_version == "v1.wu-native", f"expected v2 lineage tag, got {data_version!r}"


def test_settlement_replay_reads_v2_running_max(migrated_conn: sqlite3.Connection):
    """Settlement replay via day0_observation_reader returns the v2 running_max.

    This closes the relationship across the migration→reader boundary: the value
    that survives the merge is exactly the value settlement would read.
    """
    from src.data.day0_observation_reader import read_day0_observed_extrema_v2

    result = read_day0_observed_extrema_v2(
        migrated_conn,
        city=_CITY,
        target_date=_TARGET_DATE,
        timezone_name="Europe/London",
        decision_time_utc=datetime(2025, 3, 30, 23, 0, tzinfo=timezone.utc),
        source_priority=(_SOURCE,),
    )
    assert result.chosen_source == _SOURCE
    assert result.high_so_far == _V2_CORRECT_MAX, (
        f"settlement replay read running_max={result.high_so_far} "
        f"(expected v2-correct {_V2_CORRECT_MAX}). The reader queries the canonical "
        "observation_instants; if the merge had let legacy win, settlement would "
        f"resolve on the DST-wrong {_LEGACY_DST_WRONG_MAX}."
    )


def test_no_v2_table_after_migration(migrated_conn: sqlite3.Connection):
    """The split is gone: observation_instants_v2 no longer exists post-merge."""
    exists = migrated_conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='observation_instants_v2'"
    ).fetchone()[0]
    assert exists == 0, "observation_instants_v2 must be dropped by the consolidation"
