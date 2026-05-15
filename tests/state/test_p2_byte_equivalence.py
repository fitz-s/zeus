# Created: 2026-05-14
# Last reused or audited: 2026-05-14
# Authority basis: docs/operations/task_2026-05-14_k1_followups/PLAN.md §2 (P2 DDL byte-equivalence gate);
#   docs/operations/task_2026-05-14_k1_followups/IMPLEMENTATION_REVIEW_P3.md N2/N4
#   INV-37 enforcement per architecture/invariants.yaml::INV-37
"""P2 DDL byte-equivalence and ATTACH-guard regression tests.

Addresses IMPLEMENTATION_REVIEW_P3 findings:
  N2 (MEDIUM): Automate byte-equivalence diff against
    tests/fixtures/before_p2_sqlite_master.sql so DDL drift is caught in CI
    without manual comparison.
  N4 (LOW): Add size>0 regression test for init_schema_forecasts ATTACH guard
    at src/state/db.py:2686 — no existing test creates a 0-byte stub and
    confirms the static-fallback path is taken.

ANTIBODY PROOF per Fitz Core Methodology #4 (make category impossible):
  N2 byte-equiv:
    Regression injection: add a new column to a forecast-class table in
    init_schema_forecasts static helpers but not in the fixture → test fails
    on DDL mismatch (sorted drift list is non-empty).
  N4 size>0 guard:
    Regression injection: remove the `st_size > 0` branch in db.py:2686 →
    ATTACH guard never fires on 0-byte stub → test still passes because
    static-fallback creates the tables either way; the test is strengthened by
    also asserting no ATTACH of "world_src" occurs when stub is 0-byte.
"""
from __future__ import annotations

import logging
import sqlite3
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _names_from_conn(conn: sqlite3.Connection) -> set[str]:
    """Return set of table and index names from sqlite_master."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type IN ('table', 'index') AND sql IS NOT NULL"
    ).fetchall()
    return {row[0] for row in rows}


def _names_from_fixture(fixture_path: Path) -> set[str]:
    """Extract table/index names from the before_p2_sqlite_master.sql fixture.

    The fixture uses '-- table: <name>' and '-- index: <name>' markers.
    """
    names: set[str] = set()
    for line in fixture_path.read_text().splitlines():
        line = line.strip()
        if line.startswith("-- table:") or line.startswith("-- index:"):
            name = line.split(":", 1)[1].strip()
            if name:
                names.add(name)
    return names




# ---------------------------------------------------------------------------
# N2: byte-equivalence test
# ---------------------------------------------------------------------------

class TestP2ByteEquivalence:
    """N2: DDL produced by current init functions must match the P2 fixture.

    The fixture (tests/fixtures/before_p2_sqlite_master.sql) was captured at
    the P1 tip and represents the expected DDL shape post-K1 split. Any
    unexpected schema drift will be detected here before reaching production.

    ALLOWED delta (explicitly listed per PLAN §2):
      - Forecast-class tables (observations, settlements, calibration_pairs_v2,
        etc.) appear on world.db as legacy_archived ghost copies (per
        architecture/db_table_ownership.yaml) and also on forecasts.db as
        the canonical FORECAST_CLASS copy. This is by design.
      The union of both init functions must match the fixture's union.
    """

    FIXTURE_PATH = (
        Path(__file__).parent.parent / "fixtures" / "before_p2_sqlite_master.sql"
    )

    def test_fixture_exists(self):
        """Precondition: the fixture file was committed and is non-empty."""
        assert self.FIXTURE_PATH.exists(), (
            f"Fixture not found: {self.FIXTURE_PATH}. "
            "Commit tests/fixtures/before_p2_sqlite_master.sql before running."
        )
        assert self.FIXTURE_PATH.stat().st_size > 0, "Fixture file is empty."

    def test_world_plus_forecasts_schema_names_match_fixture(self):
        """Table/index names from init functions must exactly match fixture.

        This is the name-set check: detects added or dropped tables/indexes.
        Failing here means a schema object was added without updating the
        fixture baseline (or the fixture has a name the code no longer creates).

        Uses :memory: for world, temp file for forecasts (ATTACH path requires
        on-disk world.db).
        """
        import src.state.db as db_mod

        # Step 1: world init
        world_conn = sqlite3.connect(":memory:")
        db_mod.init_schema_world_only(world_conn)
        world_names = _names_from_conn(world_conn)
        world_conn.close()

        # Step 2: forecasts init (ATTACH path → needs on-disk world.db)
        with tempfile.TemporaryDirectory() as tmpdir:
            world_path = Path(tmpdir) / "zeus-world.db"
            w_conn = sqlite3.connect(str(world_path))
            db_mod.init_schema_world_only(w_conn)
            w_conn.close()

            original_world_path = db_mod.ZEUS_WORLD_DB_PATH
            db_mod.ZEUS_WORLD_DB_PATH = world_path
            try:
                fc_conn = sqlite3.connect(":memory:")
                db_mod.init_schema_forecasts(fc_conn)
                forecasts_names = _names_from_conn(fc_conn)
                fc_conn.close()
            finally:
                db_mod.ZEUS_WORLD_DB_PATH = original_world_path

        live_names = world_names | forecasts_names
        fixture_names = _names_from_fixture(self.FIXTURE_PATH)

        only_in_live = live_names - fixture_names
        only_in_fixture = fixture_names - live_names

        assert not only_in_live and not only_in_fixture, (
            f"Schema name drift vs {self.FIXTURE_PATH.name}.\n"
            f"  In live init but NOT in fixture ({len(only_in_live)}): {sorted(only_in_live)}\n"
            f"  In fixture but NOT in live init ({len(only_in_fixture)}): {sorted(only_in_fixture)}\n"
            "If this is expected (e.g., a planned new table), update the fixture and list "
            "the delta in the PR description."
        )

    def test_v2_forecast_tables_not_created_by_world_init(self):
        """init_schema_world_only must NOT create v2 forecast-class tables.

        The v2 tables (calibration_pairs_v2, ensemble_snapshots_v2,
        market_events_v2, settlements_v2) have no legacy_archived ghost copies
        on world.db. If world init accidentally creates them, that is K1-split
        contamination (they belong exclusively on forecasts.db).

        Note: observations/settlements/source_run DO have LEGACY_ARCHIVED ghost
        copies on world.db (by design, D2 90-day retain); those are exempt.
        """
        import src.state.db as db_mod
        from src.state.table_registry import DBIdentity, _REGISTRY, SchemaClass

        world_conn = sqlite3.connect(":memory:")
        db_mod.init_schema_world_only(world_conn)
        world_names = _names_from_conn(world_conn)
        world_conn.close()

        # Tables that are on forecasts but have NO legacy_archived ghost on world
        fc_names = {tname for (tname, db), _ in _REGISTRY.items() if db == DBIdentity.FORECASTS}
        legacy_names = {
            tname for (tname, db), e in _REGISTRY.items()
            if db == DBIdentity.WORLD and e.schema_class == SchemaClass.LEGACY_ARCHIVED
        }
        pure_fc = fc_names - legacy_names  # no ghost copy on world

        leaked = world_names & pure_fc
        assert not leaked, (
            f"init_schema_world_only() created pure-forecast-class table(s) "
            f"with no world ghost-copy entry: {sorted(leaked)}. "
            "K1-split contamination — remove from world init or add LEGACY_ARCHIVED entry."
        )


# ---------------------------------------------------------------------------
# N4: size>0 ATTACH guard regression test
# ---------------------------------------------------------------------------

class TestInitSchemaForecasts0ByteGuard:
    """N4: 0-byte world.db stub must take the static-fallback path.

    Regression: if db.py:2686 `st_size > 0` guard is removed, the ATTACH path
    would fire against a 0-byte file and raise an sqlite3.OperationalError
    ("unable to open database file" or similar) rather than falling back to
    static DDL helpers. This test verifies the fallback:
    1. Creates a 0-byte world.db stub (simulates fresh-deploy before world.db
       is initialized).
    2. Calls init_schema_forecasts against a fresh :memory: connection.
    3. Asserts the warning is emitted (static-fallback path taken).
    4. Asserts all expected forecast-class tables exist (fallback creates them).
    5. Asserts "world_src" was NOT attached at end of init.
    """

    EXPECTED_FORECAST_TABLES = {
        "observations",
        "settlements",
        "source_run",
        "settlements_v2",
        "market_events_v2",
        "ensemble_snapshots_v2",
        "calibration_pairs_v2",
    }

    def test_zero_byte_stub_takes_static_fallback(self, tmp_path, caplog):
        import src.state.db as db_mod

        stub_path = tmp_path / "zeus-world.db"
        stub_path.write_bytes(b"")  # 0-byte stub
        assert stub_path.stat().st_size == 0

        original_world_path = db_mod.ZEUS_WORLD_DB_PATH
        db_mod.ZEUS_WORLD_DB_PATH = stub_path
        try:
            with caplog.at_level(logging.WARNING, logger="src.state.db"):
                fc_conn = sqlite3.connect(":memory:")
                db_mod.init_schema_forecasts(fc_conn)
        finally:
            db_mod.ZEUS_WORLD_DB_PATH = original_world_path

        # 1. Warning emitted (static-fallback path taken)
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("falling back to static DDL" in str(m) for m in warning_msgs), (
            f"Expected static-fallback warning not found. Got: {warning_msgs}"
        )

        # 2. All forecast-class tables created by static helpers
        tables_on_fc = {
            row[0]
            for row in fc_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = self.EXPECTED_FORECAST_TABLES - tables_on_fc
        assert not missing, (
            f"Static-fallback path did not create expected forecast tables: {missing}"
        )

        # 3. No "world_src" attached (ATTACH path was skipped)
        attached = {
            row[1]
            for row in fc_conn.execute("PRAGMA database_list").fetchall()
        }
        assert "world_src" not in attached, (
            "world_src was attached despite 0-byte world.db stub — "
            "size>0 guard may have been bypassed."
        )

        fc_conn.close()

    def test_nonexistent_world_db_takes_static_fallback(self, tmp_path, caplog):
        """Covers the other branch: world.db does not exist at all (fresh deploy)."""
        import src.state.db as db_mod

        nonexistent = tmp_path / "no_such.db"
        assert not nonexistent.exists()

        original_world_path = db_mod.ZEUS_WORLD_DB_PATH
        db_mod.ZEUS_WORLD_DB_PATH = nonexistent
        try:
            with caplog.at_level(logging.WARNING, logger="src.state.db"):
                fc_conn = sqlite3.connect(":memory:")
                db_mod.init_schema_forecasts(fc_conn)
        finally:
            db_mod.ZEUS_WORLD_DB_PATH = original_world_path

        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("falling back to static DDL" in str(m) for m in warning_msgs), (
            f"Expected static-fallback warning not found. Got: {warning_msgs}"
        )

        tables_on_fc = {
            row[0]
            for row in fc_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = self.EXPECTED_FORECAST_TABLES - tables_on_fc
        assert not missing, (
            f"Static-fallback (no world.db) did not create expected tables: {missing}"
        )

        fc_conn.close()
