# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: docs/operations/task_2026-05-16_deep_alignment_audit/FIX_PLAN.md §PR-M'
#                  src/data/tier_resolver.py (EXPECTED_SOURCE_BY_CITY — canonical city→source map)
#                  src/main.py:1583 (S1_S2_SLA_HOURS = 4)
"""Antibody for F18 dual-writer freshness invariant.

For ALL tier-1 settlement cities (WU_ICAO / OGIMET_METAR / HKO_NATIVE) as
enumerated by tier_resolver.EXPECTED_SOURCE_BY_CITY:
  v2 MUST have a row within 4h of v1's latest row.
  v2-pinned-while-v1-advances = writer dead, SEV.

The city set is derived from EXPECTED_SOURCE_BY_CITY — the canonical per-city
source mapping in tier_resolver.py. Hard-coded subsets are explicitly rejected;
adding a new city to tier_resolver automatically extends this antibody.

ANTIBODY PROOF:
  Regression injection: insert a tier-1 city row into v1 (fresh) and
  leave v2 stale (last row > 4h behind v1's latest). The test MUST fail.
  Green path: v2 row is within 4h of v1's latest row. The test MUST pass.

See docs/operations/task_2026-05-16_deep_alignment_audit/PR_M_REFRAME_BRIEF.md
for full analysis.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta

import pytest

# ---------------------------------------------------------------------------
# Canonical city → source mapping from tier_resolver (all tier-1 cities).
# This is the single source of truth; test coverage auto-extends as cities
# are added to tier_resolver.py.
# ---------------------------------------------------------------------------
from src.data.tier_resolver import EXPECTED_SOURCE_BY_CITY

# ---------------------------------------------------------------------------
# S1_S2_SLA_HOURS is the dual-writer freshness budget (from src/main.py:1583)
# ---------------------------------------------------------------------------
S1_S2_SLA_HOURS = 4

# ---------------------------------------------------------------------------
# Minimal DDL: observation_instants (v1), observation_instants_v2, zeus_meta
# Only the columns needed for the freshness invariant check.
# ---------------------------------------------------------------------------
_V1_DDL = """
CREATE TABLE observation_instants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT NOT NULL,
    source TEXT NOT NULL,
    utc_timestamp TEXT NOT NULL
)
"""

_V2_DDL = """
CREATE TABLE observation_instants_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT NOT NULL,
    source TEXT NOT NULL,
    data_version TEXT NOT NULL,
    utc_timestamp TEXT NOT NULL,
    target_date TEXT NOT NULL,
    local_hour INTEGER NOT NULL
)
"""

_META_DDL = """
CREATE TABLE zeus_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

_VIEW_DDL = """
CREATE VIEW observation_instants_current AS
    SELECT o.*
    FROM observation_instants_v2 o
    JOIN zeus_meta m
      ON m.key = 'observation_data_version'
     AND o.data_version = m.value
"""


def _make_conn(active_data_version: str = "v1.wu-native") -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(_V1_DDL)
    conn.execute(_V2_DDL)
    conn.execute(_META_DDL)
    conn.execute(_VIEW_DDL)
    conn.execute(
        "INSERT INTO zeus_meta VALUES ('observation_data_version', ?)",
        (active_data_version,),
    )
    return conn


def _ts(offset_hours: int = 0) -> str:
    """UTC ISO timestamp relative to now."""
    return (
        datetime.now(timezone.utc) + timedelta(hours=offset_hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Core invariant checker (shared by both test cases)
# ---------------------------------------------------------------------------

def _check_freshness_invariant(conn: sqlite3.Connection, active_version: str) -> None:
    """Assert: for EVERY tier-1 city in EXPECTED_SOURCE_BY_CITY with v1 rows,
    v2 has a row within SLA.

    Raises AssertionError with a descriptive message if any city violates the
    dual-writer freshness contract.
    """
    for city, source in sorted(EXPECTED_SOURCE_BY_CITY.items()):
        v1_latest_row = conn.execute(
            """
            SELECT utc_timestamp FROM observation_instants
            WHERE city = ? AND source = ?
            ORDER BY utc_timestamp DESC LIMIT 1
            """,
            (city, source),
        ).fetchone()
        if v1_latest_row is None:
            # No v1 rows for this city in the fixture — skip
            continue

        v1_latest = datetime.fromisoformat(
            v1_latest_row[0].replace("Z", "+00:00")
        )

        v2_latest_row = conn.execute(
            """
            SELECT utc_timestamp FROM observation_instants_v2
            WHERE city = ? AND source = ? AND data_version = ?
            ORDER BY utc_timestamp DESC LIMIT 1
            """,
            (city, source, active_version),
        ).fetchone()

        if v2_latest_row is None:
            raise AssertionError(
                f"DUAL-WRITER SEV: city={city!r} source={source!r} — "
                f"v2 has NO rows but v1's latest is {v1_latest.isoformat()}. "
                f"v2 writer is dead or never ran for this city."
            )

        v2_latest = datetime.fromisoformat(
            v2_latest_row[0].replace("Z", "+00:00")
        )
        lag = v1_latest - v2_latest
        if lag > timedelta(hours=S1_S2_SLA_HOURS):
            raise AssertionError(
                f"DUAL-WRITER SEV: city={city!r} source={source!r} — "
                f"v2 is {lag} behind v1 (SLA={S1_S2_SLA_HOURS}h). "
                f"v1_latest={v1_latest.isoformat()}, "
                f"v2_latest={v2_latest.isoformat()}. "
                f"v2 writer has stalled while v1 advances."
            )


# ---------------------------------------------------------------------------
# GREEN: v2 is fresh (within SLA) — test must PASS
# ---------------------------------------------------------------------------

class TestDualWriterFreshnessSLA:
    """Antibody suite: dual-write freshness invariant."""

    def test_green_v2_fresh_within_sla(self):
        """v2 rows are within 4h of v1's latest for ALL tier-1 cities — invariant holds.

        Iterates every city in EXPECTED_SOURCE_BY_CITY (all 52 tier-1 cities).
        """
        conn = _make_conn()
        now_ts = _ts(0)
        minus_1h = _ts(-1)

        for city, source in EXPECTED_SOURCE_BY_CITY.items():
            # v1: row 1h ago
            conn.execute(
                "INSERT INTO observation_instants (city, source, utc_timestamp) VALUES (?, ?, ?)",
                (city, source, minus_1h),
            )
            # v2: row at now (fresher than v1, well within SLA)
            conn.execute(
                """
                INSERT INTO observation_instants_v2
                    (city, source, data_version, utc_timestamp, target_date, local_hour)
                VALUES (?, ?, 'v1.wu-native', ?, '2026-05-18', 10)
                """,
                (city, source, now_ts),
            )

        # Must not raise
        _check_freshness_invariant(conn, "v1.wu-native")

    def test_red_v2_stale_exceeds_sla(self):
        """v2 is 6h behind v1 for Chicago (WU_ICAO) — invariant MUST fail.

        This is the regression-injection scenario: v1 writer is running, v2
        writer has stalled. The test verifies the antibody catches this for
        a representative WU_ICAO city.
        """
        conn = _make_conn()
        now_ts = _ts(0)
        minus_6h = _ts(-6)

        city, source = "Chicago", EXPECTED_SOURCE_BY_CITY["Chicago"]
        # v1: row at now (fresh)
        conn.execute(
            "INSERT INTO observation_instants (city, source, utc_timestamp) VALUES (?, ?, ?)",
            (city, source, now_ts),
        )
        # v2: last row was 6h ago (exceeds 4h SLA)
        conn.execute(
            """
            INSERT INTO observation_instants_v2
                (city, source, data_version, utc_timestamp, target_date, local_hour)
            VALUES (?, ?, 'v1.wu-native', ?, '2026-05-18', 4)
            """,
            (city, source, minus_6h),
        )

        with pytest.raises(AssertionError, match="DUAL-WRITER SEV"):
            _check_freshness_invariant(conn, "v1.wu-native")

    def test_red_v2_stale_ogimet_city(self):
        """v2 is stale for Istanbul (OGIMET_METAR) — invariant MUST fail.

        Covers the Ogimet sub-tier specifically; Istanbul has source
        'ogimet_metar_ltfm' which was excluded from the original hard-coded
        3-city sample in some test configurations.
        """
        conn = _make_conn()
        now_ts = _ts(0)
        minus_6h = _ts(-6)

        city, source = "Istanbul", EXPECTED_SOURCE_BY_CITY["Istanbul"]
        conn.execute(
            "INSERT INTO observation_instants (city, source, utc_timestamp) VALUES (?, ?, ?)",
            (city, source, now_ts),
        )
        conn.execute(
            """
            INSERT INTO observation_instants_v2
                (city, source, data_version, utc_timestamp, target_date, local_hour)
            VALUES (?, ?, 'v1.wu-native', ?, '2026-05-18', 4)
            """,
            (city, source, minus_6h),
        )

        with pytest.raises(AssertionError, match="DUAL-WRITER SEV"):
            _check_freshness_invariant(conn, "v1.wu-native")

    def test_red_v2_absent_for_tier1_city(self):
        """v2 has zero rows for Moscow (OGIMET_METAR) with active v1 rows — invariant MUST fail.

        Moscow was missing from the original hard-coded 3-city TIER1_SAMPLE.
        """
        conn = _make_conn()
        city, source = "Moscow", EXPECTED_SOURCE_BY_CITY["Moscow"]
        conn.execute(
            "INSERT INTO observation_instants (city, source, utc_timestamp) VALUES (?, ?, ?)",
            (city, source, _ts(-1)),
        )
        # v2 has no rows for Moscow at all

        with pytest.raises(AssertionError, match="DUAL-WRITER SEV"):
            _check_freshness_invariant(conn, "v1.wu-native")

    def test_view_routes_to_active_version_only(self):
        """observation_instants_current VIEW filters to active data_version.

        Rows with a different data_version are invisible through the VIEW.
        This validates the cutover indirection: switching zeus_meta flips
        which corpus is active without touching any reader.
        """
        conn = _make_conn(active_data_version="v1.wu-native")
        city = "Chicago"
        source = EXPECTED_SOURCE_BY_CITY[city]

        # Insert v2 row with the active version
        conn.execute(
            """
            INSERT INTO observation_instants_v2
                (city, source, data_version, utc_timestamp, target_date, local_hour)
            VALUES (?, ?, 'v1.wu-native', ?, '2026-05-18', 12)
            """,
            (city, source, _ts(0)),
        )
        # Insert v2 row with an inactive version
        conn.execute(
            """
            INSERT INTO observation_instants_v2
                (city, source, data_version, utc_timestamp, target_date, local_hour)
            VALUES (?, ?, 'v2.wu-native', ?, '2026-05-18', 13)
            """,
            (city, source, _ts(0)),
        )

        view_count = conn.execute(
            "SELECT COUNT(*) FROM observation_instants_current WHERE city = ? AND source = ?",
            (city, source),
        ).fetchone()[0]
        raw_count = conn.execute(
            "SELECT COUNT(*) FROM observation_instants_v2 WHERE city = ? AND source = ?",
            (city, source),
        ).fetchone()[0]

        assert view_count == 1, (
            f"VIEW should show 1 row (active version only), got {view_count}"
        )
        assert raw_count == 2, (
            f"v2 raw table should show 2 rows (both versions), got {raw_count}"
        )
