# Created: 2026-06-27
# Last reused/audited: 2026-06-27
"""HKO runtime-monitoring repair script antibodies."""

import sqlite3

from scripts.repair_hko_runtime_monitoring_observations import (
    repair_hko_runtime_monitoring_observations,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE observation_instants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT,
            target_date TEXT,
            source TEXT,
            utc_timestamp TEXT,
            authority TEXT,
            source_role TEXT,
            training_allowed INTEGER,
            causality_status TEXT
        )
        """
    )
    return conn


def test_repair_hko_runtime_monitoring_observations_updates_only_hko_native_rows():
    conn = _conn()
    conn.execute(
        """
        INSERT INTO observation_instants (
            city, target_date, source, utc_timestamp, authority,
            source_role, training_allowed, causality_status
        ) VALUES
            ('Hong Kong', '2026-06-26', 'hko_hourly_accumulator',
             '2026-06-25T23:00Z', 'ICAO_STATION_NATIVE',
             'fallback_evidence', 0, 'REQUIRES_SOURCE_REAUDIT'),
            ('Hong Kong', '2026-06-26', 'openmeteo_archive_hourly',
             '2026-06-25T23:00Z', 'UNVERIFIED',
             'fallback_evidence', 0, 'OK')
        """
    )

    dry = repair_hko_runtime_monitoring_observations(conn, apply=False)
    applied = repair_hko_runtime_monitoring_observations(conn, apply=True)

    assert dry["candidates_found"] == 1
    assert dry["rows_updated"] == 0
    assert applied["rows_updated"] == 1
    hk = conn.execute(
        """
        SELECT source_role, training_allowed, causality_status
          FROM observation_instants
         WHERE source = 'hko_hourly_accumulator'
        """
    ).fetchone()
    other = conn.execute(
        """
        SELECT source_role, training_allowed, causality_status
          FROM observation_instants
         WHERE source = 'openmeteo_archive_hourly'
        """
    ).fetchone()
    assert hk == ("runtime_monitoring", 0, "OK")
    assert other == ("fallback_evidence", 0, "OK")
