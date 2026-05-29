# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: docs/archive/2026-Q2/task_2026-05-16_deep_alignment_audit/FIX_PLAN.md §5 PR-A (F4)
#   POST_K1_DELTA.md F4 row (2,112 stranded rows; writer corrected on main via PR #121).
#   Antibody: make world.market_events existence structurally impossible after migration.
"""F4 antibody: world.db must never own market_events.

Antibody proof (Fitz Methodology #4 — make category impossible):
  Category: forecast-class table erroneously created / retained on world.db.
  F4-specific: market_events writer was pre-K1; 2,112 orphaned rows remained on
  world.db after PR #121 retargeted writes to forecasts.db. Migration drops the table.

Assertions:
  A. market_events is NOT in world.db schema after init_schema (structural impossibility).
  B. Registry maps market_events → FORECAST_CLASS, NOT legacy_archived on world.
  C. No source file under src/ contains the string 'world.market_events'
     (would indicate an accidental world-db reader/writer).

Sed-break verification (antibody-recursion):
  Inject 'CREATE TABLE IF NOT EXISTS market_events (id INTEGER PRIMARY KEY)'
  into init_schema's executescript block → test A fails.
  This was verified during development; see commit message for evidence.
"""
from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# A — world.db schema must NOT contain market_events after init_schema
# ---------------------------------------------------------------------------

class TestNoWorldMarketEventsV2Schema:
    """market_events must be absent from world.db after init_schema.

    Regression injection: if init_schema is modified to CREATE TABLE market_events
    (e.g. a future engineer accidentally adds it), this test fails immediately.
    """

    def test_market_events_absent_from_world_init_schema(self):
        """init_schema_world_only must NOT create market_events on world.db.

        Directly queries sqlite_master on an :memory: world DB to confirm
        the table does not exist after schema initialization.
        """
        from src.state.db import init_schema_world_only

        conn = sqlite3.connect(":memory:")
        init_schema_world_only(conn)

        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='market_events'"
        ).fetchone()
        conn.close()

        assert row is None, (
            "F4 ANTIBODY FAIL: init_schema_world_only() created 'market_events' "
            "on world.db — this table belongs ONLY on zeus-forecasts.db. "
            "Remove the CREATE TABLE from the world-init path."
        )


# ---------------------------------------------------------------------------
# B — Registry maps market_events → FORECAST_CLASS only (no world entry)
# ---------------------------------------------------------------------------

class TestRegistryMarketEventsV2ForecastOnly:
    """market_events must appear in registry ONLY as forecast_class on forecasts.db.

    After the F4 migration the legacy_archived world.db ghost entry is removed,
    so the registry should have exactly ONE entry for market_events: the
    forecasts.forecast_class canonical entry.
    """

    def test_registry_maps_market_events_to_forecast_class_only(self):
        """market_events has exactly ONE registry entry: (forecasts, forecast_class).

        Regression injection: if the world legacy_archived entry is re-added
        OR if the forecast_class entry is reclassified, this test fails.
        """
        from src.state.table_registry import DBIdentity, SchemaClass, _REGISTRY

        entries = [
            (db_id, entry.schema_class)
            for (name, db_id), entry in _REGISTRY.items()
            if name == "market_events"
            and entry.schema_class != SchemaClass.LEGACY_ARCHIVED
        ]

        assert len(entries) == 1, (
            f"F4 ANTIBODY FAIL: expected exactly 1 registry entry for "
            f"'market_events', found {len(entries)}: {entries}. "
            f"The world.db legacy_archived entry must be removed after the F4 migration."
        )

        db_id, schema_class = entries[0]
        assert db_id == DBIdentity.FORECASTS, (
            f"F4 ANTIBODY FAIL: market_events registry entry must be on "
            f"DBIdentity.FORECASTS, found {db_id!r}."
        )
        assert schema_class == SchemaClass.FORECAST_CLASS, (
            f"F4 ANTIBODY FAIL: market_events registry entry must have "
            f"SchemaClass.FORECAST_CLASS, found {schema_class!r}."
        )

    def test_is_forecast_class_true_for_market_events(self):
        """is_forecast_class('market_events') returns True."""
        from src.state.table_registry import is_forecast_class

        assert is_forecast_class("market_events"), (
            "F4 ANTIBODY FAIL: is_forecast_class('market_events') returned False. "
            "The table must be registered as forecast_class in db_table_ownership.yaml."
        )

    def test_tables_for_world_does_not_include_market_events(self):
        """tables_for(DBIdentity.WORLD) must NOT include market_events.

        tables_for() excludes legacy_archived entries. After F4 migration removes the
        world legacy_archived entry, this is doubly ensured: no world entry at all.
        """
        from src.state.table_registry import DBIdentity, tables_for

        world_tables = tables_for(DBIdentity.WORLD)
        assert "market_events" not in world_tables, (
            f"F4 ANTIBODY FAIL: tables_for(DBIdentity.WORLD) includes "
            f"'market_events'. It must only be in tables_for(DBIdentity.FORECASTS). "
            f"World tables: {sorted(world_tables)}"
        )


# ---------------------------------------------------------------------------
# C — No src/ file contains 'world.market_events'
# ---------------------------------------------------------------------------

class TestNoWorldMarketEventsV2InSource:
    """No source file under src/ may contain 'world.market_events'.

    This would indicate an accidental reader or writer targeting the orphaned
    world.db copy rather than the canonical forecasts.db copy.

    The migrate_world_observations_to_forecasts.py script in scripts/ is exempt
    (it's a one-time migration utility that explicitly bridges both DBs).
    """

    def test_no_world_market_events_in_src(self):
        """grep src/ for 'world.market_events' — must return no matches.

        Exemption: scripts/migrate_world_observations_to_forecasts.py is a
        one-time migration that explicitly uses ATTACH to bridge world→forecasts.
        Only src/ is checked here.
        """
        result = subprocess.run(
            ["grep", "-rn", "world\\.market_events"],
            cwd=str(_REPO_ROOT / "src"),
            capture_output=True,
            text=True,
        )
        matches = [line for line in result.stdout.strip().splitlines() if line]

        assert not matches, (
            f"F4 ANTIBODY FAIL: found 'world.market_events' references in src/: "
            f"{matches}. All market_events access must target forecasts.db; "
            f"the world.db copy was dropped by the F4 migration."
        )
