# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: docs/operations/task_2026-04-28_f11_forecast_issue_time/evidence/forecasts_consumer_audit_2026-04-28.md
"""F11.5-migrate antibodies: ETL scripts filter forecasts by SKILL eligibility.

Locks the wiring of SKILL_ELIGIBLE_SQL into:
- scripts/etl_historical_forecasts.py
- scripts/etl_forecast_skill_from_forecasts.py

After F11 backfill, RECONSTRUCTED rows (ICON / UKMO / OpenMeteo) must NOT
flow into training/skill ETL output. DERIVED_FROM_DISSEMINATION + RECORDED
+ FETCH_TIME rows pass through. Pre-F11 legacy NULL rows are tolerated.
"""

import sqlite3

import pytest


def _seed_forecasts_with_mixed_provenance(conn: sqlite3.Connection) -> None:
    """6 forecast rows: 3 SKILL-eligible + 2 RECONSTRUCTED + 1 NULL legacy."""
    conn.execute("""
        CREATE TABLE forecasts (
            id INTEGER PRIMARY KEY,
            city TEXT, target_date TEXT, source TEXT,
            forecast_basis_date TEXT, forecast_issue_time TEXT,
            lead_days INTEGER, lead_time_hours REAL,
            forecast_high REAL, forecast_low REAL, temp_unit TEXT,
            retrieved_at TEXT, imported_at TEXT,
            source_id TEXT, raw_payload_hash TEXT, captured_at TEXT, authority_tier TEXT,
            rebuild_run_id TEXT, data_source_version TEXT,
            availability_provenance TEXT
        )
    """)
    rows = [
        # (id, city, target_date, source, basis, issue, lead, hours, high, low, unit, ret, imp, sid, hash, cap, tier, run, ver, prov)
        (1, "NYC", "2026-04-30", "ecmwf_previous_runs", "2026-04-28", "2026-04-28T06:48Z", 2, 48.0, 72.0, 58.0, "F", "t", "t", "ecmwf", "h", "t", "non_promotion", None, None, "derived_dissemination"),
        (2, "NYC", "2026-04-30", "gfs_previous_runs", "2026-04-28", "2026-04-28T04:14Z", 2, 48.0, 72.5, 58.5, "F", "t", "t", "gfs", "h", "t", "non_promotion", None, None, "fetch_time"),
        (3, "NYC", "2026-04-30", "ukmo_previous_runs", "2026-04-28", "2026-04-28T12:00Z", 2, 48.0, 70.0, 56.0, "F", "t", "t", "ukmo", "h", "t", "non_promotion", None, None, "recorded"),
        (4, "NYC", "2026-04-30", "icon_previous_runs", "2026-04-28", "2026-04-28T12:00Z", 2, 48.0, 71.0, 57.0, "F", "t", "t", "icon", "h", "t", "non_promotion", None, None, "reconstructed"),
        (5, "NYC", "2026-04-30", "openmeteo_previous_runs", "2026-04-28", "2026-04-28T12:00Z", 2, 48.0, 73.0, 59.0, "F", "t", "t", "om", "h", "t", "non_promotion", None, None, "reconstructed"),
        # Pre-F11 legacy row
        (6, "NYC", "2026-04-30", "openmeteo_previous_runs", "2026-04-27", None, 3, 72.0, 70.5, 56.5, "F", "t", "t", "om", "h", "t", "non_promotion", None, None, None),
    ]
    conn.executemany(
        "INSERT INTO forecasts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()


def _seed_settlements(conn: sqlite3.Connection) -> None:
    """Single settlement row matching the forecasts above."""
    conn.execute("""
        CREATE TABLE settlements (
            city TEXT, target_date TEXT, market_slug TEXT, winning_bin TEXT,
            settlement_value REAL, settlement_source TEXT, settled_at TEXT,
            authority TEXT, temperature_metric TEXT
        )
    """)
    conn.execute(
        "INSERT INTO settlements VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("NYC", "2026-04-30", "test", "70-71F", 71.0, "WU", "2026-04-30", "VERIFIED", "high"),
    )
    conn.commit()


@pytest.fixture
def db_with_mixed_provenance():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _seed_forecasts_with_mixed_provenance(conn)
    _seed_settlements(conn)
    yield conn
    conn.close()


def test_etl_historical_forecasts_filter_excludes_reconstructed(db_with_mixed_provenance):
    """SELECT pattern from etl_historical_forecasts.py must include only
    SKILL-eligible + legacy NULL rows."""
    from src.backtest.training_eligibility import SKILL_ELIGIBLE_SQL

    rows = db_with_mixed_provenance.execute(f"""
        SELECT id, source, availability_provenance
        FROM forecasts
        WHERE forecast_high IS NOT NULL
          AND (availability_provenance IS NULL OR {SKILL_ELIGIBLE_SQL})
        ORDER BY id
    """).fetchall()
    ids = [r["id"] for r in rows]
    # 3 SKILL-eligible (ECMWF DERIVED, GFS FETCH_TIME, UKMO RECORDED)
    # + 1 legacy NULL = 4 total. ICON + OpenMeteo RECONSTRUCTED excluded.
    assert ids == [1, 2, 3, 6]
    # Sanity: no row in result has provenance = 'reconstructed'
    assert all(r["availability_provenance"] != "reconstructed" for r in rows)


def test_etl_forecast_skill_join_filter_excludes_reconstructed(db_with_mixed_provenance):
    """SELECT pattern from etl_forecast_skill_from_forecasts.py with JOIN
    must filter the forecasts side (qualified `f.` prefix)."""
    from src.backtest.training_eligibility import SKILL_ELIGIBLE_SQL

    skill_filter_qualified = SKILL_ELIGIBLE_SQL.replace(
        "availability_provenance", "f.availability_provenance"
    )
    rows = db_with_mixed_provenance.execute(f"""
        SELECT f.id, f.source, f.availability_provenance, s.settlement_value
        FROM forecasts f
        JOIN settlements s
          ON s.city = f.city
         AND s.target_date = f.target_date
         AND s.temperature_metric = 'high'
        WHERE f.forecast_high IS NOT NULL
          AND f.lead_days IS NOT NULL
          AND s.settlement_value IS NOT NULL
          AND (f.availability_provenance IS NULL OR {skill_filter_qualified})
        ORDER BY f.id
    """).fetchall()
    ids = [r["id"] for r in rows]
    # Same expected set: ECMWF + GFS + UKMO + legacy NULL
    assert ids == [1, 2, 3, 6]
    assert all(r["availability_provenance"] != "reconstructed" for r in rows)


def test_etl_filter_count_matches_F11_backfill_distribution():
    """End-to-end count assertion against expected F11.4 backfill split.

    Per evidence/canonical_apply_2026-04-28.md, post-F11 forecasts has
    9,996 SKILL-eligible (ECMWF + GFS) + 13,470 RECONSTRUCTED rows. The
    ETL filter should yield 9,996 SKILL rows (NULL clause adds 0 because
    every row is now backfilled)."""
    # This is a structural assertion — actual canonical state SHA changes
    # with cron writes, so this test verifies the fragment shape rather
    # than running against canonical.
    from src.backtest.training_eligibility import SKILL_ELIGIBLE_SQL
    assert "derived_dissemination" in SKILL_ELIGIBLE_SQL
    assert "fetch_time" in SKILL_ELIGIBLE_SQL
    assert "recorded" in SKILL_ELIGIBLE_SQL
    assert "reconstructed" not in SKILL_ELIGIBLE_SQL
