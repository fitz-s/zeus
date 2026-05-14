# Created: 2026-05-14
# Last reused or audited: 2026-05-14
# Authority basis: docs/operations/task_2026-05-14_k1_followups/PLAN.md §2 P0
#   IMPLEMENTATION_REVIEW_P0.md Pass D Option (a) / Pass C gap
"""Cross-DB SAVEPOINT atomicity smoke test for K1 P0 daily-obs routing fix.

Fitz #3 relationship test: when _write_atom_with_coverage(conn, ...) is called,
the SAVEPOINT must write to BOTH forecasts.observations AND world.data_coverage
atomically, or roll back BOTH.

Required by IMPLEMENTATION_REVIEW_P0.md Pass C gap: the ROT-1/ROT-2 routing
tests stub data_coverage on the in-memory forecasts conn, masking the production
crash (B-1: no data_coverage table on a bare forecasts.db connection).

This test uses real on-disk DBs backed by tmp_path so both DBs have their actual
schemas without stubs. It verifies:

1. NEGATIVE: bare get_forecasts_connection fails with OperationalError because
   data_coverage does not exist on forecasts.db.
2. POSITIVE: get_forecasts_connection_with_world succeeds — SAVEPOINT writes land
   on forecasts.observations AND world.data_coverage atomically.
3. ROLLBACK: a forced exception inside the SAVEPOINT rolls back BOTH DBs.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def dual_db(tmp_path: Path):
    """Return (forecasts_path, world_path) with both schemas initialised."""
    from src.state.db import (
        ZEUS_FORECASTS_DB_PATH,
        ZEUS_WORLD_DB_PATH,
        init_schema,
        init_schema_forecasts,
    )
    import src.state.db as db_module

    forecasts_path = tmp_path / "zeus-forecasts.db"
    world_path = tmp_path / "zeus-world.db"

    # Temporarily redirect path constants so init_schema_forecasts can
    # ATTACH world_path when replicating schema from it.
    orig_f = db_module.ZEUS_FORECASTS_DB_PATH
    orig_w = db_module.ZEUS_WORLD_DB_PATH
    try:
        db_module.ZEUS_WORLD_DB_PATH = world_path
        db_module.ZEUS_FORECASTS_DB_PATH = forecasts_path

        # Build world.db first (init_schema_forecasts ATTACHes it for schema copy).
        wc = sqlite3.connect(str(world_path))
        init_schema(wc)
        wc.commit()
        wc.close()

        # Build forecasts.db (copies table DDL from world.db via ATTACH).
        fc = sqlite3.connect(str(forecasts_path))
        init_schema_forecasts(fc)
        fc.commit()
        fc.close()
    finally:
        db_module.ZEUS_WORLD_DB_PATH = orig_w
        db_module.ZEUS_FORECASTS_DB_PATH = orig_f

    return forecasts_path, world_path


def _make_atom_pair(city: str = "TestCity", target_date: date = date(2026, 5, 14)):
    """Return a minimal high/low ObservationAtom pair for smoke testing.

    All required fields are populated with valid stub values so ObservationAtom
    __post_init__ validation passes without touching real data APIs.
    """
    from datetime import datetime, timezone as dt_tz
    from zoneinfo import ZoneInfo
    from src.types.observation_atom import ObservationAtom

    tz = ZoneInfo("America/Chicago")
    fetch_utc = datetime(2026, 5, 14, 18, 0, 0, tzinfo=dt_tz.utc)
    # local_time.date() must match target_date; no DST ambiguity in May.
    local_time = datetime(2026, 5, 14, 13, 0, 0, tzinfo=tz)
    window_start = datetime(2026, 5, 14, 5, 0, 0, tzinfo=dt_tz.utc)
    window_end = datetime(2026, 5, 14, 18, 0, 0, tzinfo=dt_tz.utc)

    # provenance_metadata must include payload_hash so that
    # _require_incoming_payload_hashes (daily_observation_writer) can build
    # the combined hash and allow write_daily_observation_with_revision to proceed.
    high_meta = {"payload_hash": "sha256:aaaa0001"}
    low_meta = {"payload_hash": "sha256:bbbb0002"}

    common = dict(
        city=city,
        target_date=target_date,
        target_unit="F",
        raw_unit="F",
        source="WU",
        station_id=None,
        api_endpoint="https://stub/api",
        fetch_utc=fetch_utc,
        local_time=local_time,
        collection_window_start_utc=window_start,
        collection_window_end_utc=window_end,
        timezone="America/Chicago",
        utc_offset_minutes=-300,
        dst_active=True,
        is_ambiguous_local_hour=False,
        is_missing_local_hour=False,
        hemisphere="N",
        season="MAM",
        month=5,
        rebuild_run_id="smoke_test",
        data_source_version="wu_v1_smoke",
        authority="VERIFIED",
        validation_pass=True,
    )
    high = ObservationAtom(
        value_type="high", value=75.0, raw_value=75.0,
        provenance_metadata=high_meta, **common,
    )
    low = ObservationAtom(
        value_type="low", value=60.0, raw_value=60.0,
        provenance_metadata=low_meta, **common,
    )
    return high, low


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCrossDbSavepointAtomicity:
    """Relationship test: _write_atom_with_coverage SAVEPOINT spans both DBs."""

    def test_negative_bare_forecasts_conn_crashes_on_data_coverage(
        self, dual_db: tuple[Path, Path]
    ):
        """NEGATIVE: bare forecasts connection raises OperationalError because
        data_coverage does not exist on forecasts.db.

        This is the B-1 crash the critic identified. The test MUST fail before
        the get_forecasts_connection_with_world helper is in place, and MUST
        pass (confirming crash happens) with only a bare forecasts conn.
        """
        from src.data.daily_obs_append import _write_atom_with_coverage
        forecasts_path, _world_path = dual_db

        conn = sqlite3.connect(str(forecasts_path))
        conn.execute("PRAGMA journal_mode=WAL")
        high, low = _make_atom_pair()
        try:
            with pytest.raises(sqlite3.OperationalError, match="no such table"):
                _write_atom_with_coverage(conn, high, low, data_source="WU")
        finally:
            conn.close()

    def test_positive_attach_conn_writes_both_dbs(
        self, dual_db: tuple[Path, Path]
    ):
        """POSITIVE: get_forecasts_connection_with_world yields a conn that
        _write_atom_with_coverage can use to write observations (forecasts.db)
        AND data_coverage (world.db) in one SAVEPOINT.
        """
        import src.state.db as db_module
        from src.data.daily_obs_append import _write_atom_with_coverage

        forecasts_path, world_path = dual_db
        orig_f = db_module.ZEUS_FORECASTS_DB_PATH
        orig_w = db_module.ZEUS_WORLD_DB_PATH
        try:
            db_module.ZEUS_FORECASTS_DB_PATH = forecasts_path
            db_module.ZEUS_WORLD_DB_PATH = world_path

            high, low = _make_atom_pair()
            from src.state.db import get_forecasts_connection_with_world
            with get_forecasts_connection_with_world(write_class="bulk") as conn:
                _write_atom_with_coverage(conn, high, low, data_source="WU")
                conn.commit()
        finally:
            db_module.ZEUS_FORECASTS_DB_PATH = orig_f
            db_module.ZEUS_WORLD_DB_PATH = orig_w

        # Verify: observation landed on forecasts.db
        fc = sqlite3.connect(str(forecasts_path))
        obs_count = fc.execute(
            "SELECT COUNT(*) FROM observations WHERE city='TestCity'"
        ).fetchone()[0]
        fc.close()
        assert obs_count == 1, (
            f"Expected 1 row in forecasts.observations; got {obs_count}"
        )

        # Verify: data_coverage row landed on world.db
        wc = sqlite3.connect(str(world_path))
        cov_count = wc.execute(
            "SELECT COUNT(*) FROM data_coverage WHERE city='TestCity'"
        ).fetchone()[0]
        wc.close()
        assert cov_count == 1, (
            f"Expected 1 row in world.data_coverage; got {cov_count}"
        )

    def test_savepoint_rollback_undoes_both_dbs(
        self, dual_db: tuple[Path, Path]
    ):
        """ROLLBACK: a forced exception inside _write_atom_with_coverage rolls
        back the observations INSERT and data_coverage UPSERT together.

        Verifies SAVEPOINT atomicity across both physical DBs via ATTACH.
        """
        import src.state.db as db_module
        from src.data.daily_obs_append import _write_atom_with_coverage
        from unittest.mock import patch

        forecasts_path, world_path = dual_db
        orig_f = db_module.ZEUS_FORECASTS_DB_PATH
        orig_w = db_module.ZEUS_WORLD_DB_PATH
        try:
            db_module.ZEUS_FORECASTS_DB_PATH = forecasts_path
            db_module.ZEUS_WORLD_DB_PATH = world_path

            high, low = _make_atom_pair(city="RollbackCity")
            from src.state.db import get_forecasts_connection_with_world

            # Force record_written to raise after the observations insert.
            # Patch at the call site (daily_obs_append module-level import),
            # not at the definition (data_coverage), since the reference is
            # already bound at import time.
            with get_forecasts_connection_with_world(write_class="bulk") as conn:
                with patch(
                    "src.data.daily_obs_append.record_written",
                    side_effect=RuntimeError("forced rollback"),
                ):
                    with pytest.raises(RuntimeError, match="forced rollback"):
                        _write_atom_with_coverage(
                            conn, high, low, data_source="WU"
                        )
                conn.commit()
        finally:
            db_module.ZEUS_FORECASTS_DB_PATH = orig_f
            db_module.ZEUS_WORLD_DB_PATH = orig_w

        # Both DBs must be empty — SAVEPOINT rolled back both sides.
        fc = sqlite3.connect(str(forecasts_path))
        obs_count = fc.execute(
            "SELECT COUNT(*) FROM observations WHERE city='RollbackCity'"
        ).fetchone()[0]
        fc.close()
        assert obs_count == 0, (
            f"SAVEPOINT rollback failed: {obs_count} rows in forecasts.observations"
        )

        wc = sqlite3.connect(str(world_path))
        cov_count = wc.execute(
            "SELECT COUNT(*) FROM data_coverage WHERE city='RollbackCity'"
        ).fetchone()[0]
        wc.close()
        assert cov_count == 0, (
            f"SAVEPOINT rollback failed: {cov_count} rows in world.data_coverage"
        )
