# Created: 2026-05-08
# Last reused/audited: 2026-05-08
# Authority basis: Track A.6 plan (.omc/plans/track_a6_daemon_path_retrofit_2026_05_08.md)
#   Relationship tests R-1, R-2, R-3 verifying daemon-path ingest calls acquire
#   BULK lock on zeus-world.db before writing (plan §Test Additions).
"""Relationship tests: daemon ingest paths acquire BULK lock.

R-1: tigge_pipeline._ingest_track() acquires db_writer_lock(ZEUS_WORLD_DB_PATH, BULK)
     before opening the world DB connection.
R-2: ecmwf_open_data.collect_open_ens_cycle() acquires db_writer_lock(ZEUS_WORLD_DB_PATH, BULK)
     when own_conn=True (conn=None, production path).
R-3: ecmwf_open_data.collect_open_ens_cycle() does NOT acquire db_writer_lock
     when conn is injected (test-seam path with in-memory sqlite).
"""
from __future__ import annotations

import sqlite3
import sys
import types
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import ZEUS_WORLD_DB_PATH
from src.state.db_writer_lock import WriteClass


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_fake_ingest_module() -> types.ModuleType:
    """Build a minimal ingest_grib_to_snapshots stub."""
    mod = types.ModuleType("ingest_grib_to_snapshots")

    class _FakeSRC:
        def __init__(self, **kwargs):
            pass

    mod.SourceRunContext = _FakeSRC  # type: ignore[attr-defined]
    mod.ingest_track = lambda **kwargs: {"written": 1, "skipped": 0, "errors": 0}  # type: ignore[attr-defined]
    mod._TRACK_CONFIGS = {  # type: ignore[attr-defined]
        "mx2t6_high": {"json_subdir": "open_ens_mx2t6_localday_max"},
        "mn2t6_low":  {"json_subdir": "open_ens_mn2t6_localday_min"},
    }
    return mod


# ---------------------------------------------------------------------------
# R-1: tigge_pipeline._ingest_track acquires BULK lock before opening conn
# ---------------------------------------------------------------------------


def test_tigge_ingest_track_acquires_bulk_lock():
    """BULK lock acquired with correct args inside _ingest_track (daemon path)."""
    from src.data import tigge_pipeline

    # Shared event log to verify ordering: lock must be entered BEFORE
    # get_world_connection() is called (plan §Change 1 ordering constraint).
    event_log: list = []

    @contextmanager
    def _fake_lock(db_path, write_class):
        event_log.append(("lock_enter", db_path, write_class))
        yield
        event_log.append(("lock_exit",))

    def _fake_get_world_connection():
        event_log.append(("conn_opened",))
        return sqlite3.connect(":memory:")

    def _fake_apply_v2_schema(conn):
        pass

    fake_mod = _make_fake_ingest_module()

    import src.state.schema.v2_schema as v2_schema_mod

    with (
        patch("src.data.tigge_pipeline.db_writer_lock", _fake_lock),
        patch("src.data.tigge_pipeline.ZEUS_WORLD_DB_PATH", ZEUS_WORLD_DB_PATH),
        patch("src.data.tigge_pipeline.WriteClass", WriteClass),
        patch.dict("sys.modules", {"ingest_grib_to_snapshots": fake_mod}),
        patch.object(v2_schema_mod, "apply_v2_schema", _fake_apply_v2_schema),
        patch("src.state.db.get_world_connection", _fake_get_world_connection),
    ):
        result = tigge_pipeline._ingest_track(
            "mx2t6_high",
            date_from=date(2026, 5, 1),
            date_to=date(2026, 5, 1),
        )

    assert result.get("ok") is True, f"_ingest_track returned: {result}"

    # Verify lock was acquired with correct args.
    lock_entries = [e for e in event_log if isinstance(e, tuple) and e[0] == "lock_enter"]
    assert len(lock_entries) == 1, f"Expected 1 lock acquisition, got {event_log}"
    _, db_path_arg, wc_arg = lock_entries[0]
    assert db_path_arg == ZEUS_WORLD_DB_PATH, f"Wrong DB path locked: {db_path_arg}"
    assert wc_arg == WriteClass.BULK, f"Expected BULK, got {wc_arg}"

    # Verify lock was entered BEFORE the connection was opened (ordering constraint).
    event_types = [e[0] for e in event_log]
    lock_idx = event_types.index("lock_enter")
    conn_idx = event_types.index("conn_opened")
    assert lock_idx < conn_idx, (
        f"Ordering violation: lock entered at position {lock_idx} but conn "
        f"opened at position {conn_idx}. Full log: {event_log}"
    )
    # Verify conn was opened BEFORE the lock was released (conn closed inside lock).
    lock_exit_idx = event_types.index("lock_exit")
    assert conn_idx < lock_exit_idx, (
        f"Ordering violation: conn opened at {conn_idx} but lock released at "
        f"{lock_exit_idx}. Full log: {event_log}"
    )


# ---------------------------------------------------------------------------
# R-2: collect_open_ens_cycle acquires BULK lock when conn=None (production)
# ---------------------------------------------------------------------------


def test_opendata_collect_ens_cycle_acquires_bulk_lock():
    """BULK lock acquired on the production path (conn=None)."""
    from src.data import ecmwf_open_data
    from src.data.release_calendar import FetchDecision

    lock_calls: list = []

    @contextmanager
    def _fake_lock(db_path, write_class):
        lock_calls.append((db_path, write_class))
        yield

    fake_mod = _make_fake_ingest_module()

    mock_source_spec = MagicMock()
    mock_source_spec.degradation_level = "none"

    fixed_cycle = datetime(2026, 5, 8, 0, tzinfo=timezone.utc)
    mock_sel_meta = {
        "selected_cycle_time": fixed_cycle,
        "next_safe_fetch_at": fixed_cycle,
    }

    with (
        patch("src.data.ecmwf_open_data.db_writer_lock", _fake_lock),
        patch("src.data.ecmwf_open_data.ZEUS_WORLD_DB_PATH", ZEUS_WORLD_DB_PATH),
        patch("src.data.ecmwf_open_data.WriteClass", WriteClass),
        patch.dict("sys.modules", {"ingest_grib_to_snapshots": fake_mod}),
        patch(
            "src.data.ecmwf_open_data.get_connection",
            return_value=sqlite3.connect(":memory:"),
        ),
        patch("src.state.db.init_schema"),
        patch(
            "src.data.ecmwf_open_data._write_source_authority_chain",
            return_value={},
        ),
        patch("src.data.ecmwf_open_data.write_source_run", return_value="fake_run_id"),
        patch("src.data.ecmwf_open_data.write_source_run_coverage"),
        patch("src.data.ecmwf_open_data.gate_source", return_value=mock_source_spec),
        patch("src.data.ecmwf_open_data.gate_source_role"),
        patch(
            "src.data.ecmwf_open_data.select_source_run_for_target_horizon",
            return_value=(FetchDecision.FETCH_ALLOWED, mock_sel_meta),
        ),
        patch("src.data.ecmwf_open_data.build_forecast_target_scope", return_value=MagicMock()),
        patch("src.data.ecmwf_open_data.evaluate_horizon_coverage", return_value=MagicMock()),
        patch("src.data.ecmwf_open_data.evaluate_producer_coverage", return_value=MagicMock()),
        patch("src.data.ecmwf_open_data.build_producer_readiness_for_scope", return_value=MagicMock()),
    ):
        ecmwf_open_data.collect_open_ens_cycle(
            track="mx2t6_high",
            run_date=date(2026, 5, 8),
            run_hour=0,
            skip_download=True,
            skip_extract=True,
            conn=None,
        )

    assert len(lock_calls) == 1, f"Expected 1 BULK lock call, got {lock_calls}"
    db_path_arg, wc_arg = lock_calls[0]
    assert db_path_arg == ZEUS_WORLD_DB_PATH, f"Wrong DB path: {db_path_arg}"
    assert wc_arg == WriteClass.BULK, f"Expected BULK, got {wc_arg}"


# ---------------------------------------------------------------------------
# R-3: collect_open_ens_cycle skips lock when conn is injected (test seam)
# ---------------------------------------------------------------------------


def test_opendata_collect_ens_cycle_skips_lock_for_injected_conn():
    """No db_writer_lock call when in-memory conn is injected (test-seam path)."""
    from src.data import ecmwf_open_data
    from src.data.release_calendar import FetchDecision

    lock_calls: list = []

    @contextmanager
    def _fake_lock(db_path, write_class):
        lock_calls.append((db_path, write_class))
        yield

    fake_mod = _make_fake_ingest_module()
    injected_conn = sqlite3.connect(":memory:")

    mock_source_spec = MagicMock()
    mock_source_spec.degradation_level = "none"

    fixed_cycle = datetime(2026, 5, 8, 0, tzinfo=timezone.utc)
    mock_sel_meta = {
        "selected_cycle_time": fixed_cycle,
        "next_safe_fetch_at": fixed_cycle,
    }

    with (
        patch("src.data.ecmwf_open_data.db_writer_lock", _fake_lock),
        patch("src.data.ecmwf_open_data.ZEUS_WORLD_DB_PATH", ZEUS_WORLD_DB_PATH),
        patch("src.data.ecmwf_open_data.WriteClass", WriteClass),
        patch.dict("sys.modules", {"ingest_grib_to_snapshots": fake_mod}),
        patch("src.state.db.init_schema"),
        patch(
            "src.data.ecmwf_open_data._write_source_authority_chain",
            return_value={},
        ),
        patch("src.data.ecmwf_open_data.write_source_run", return_value="fake_run_id"),
        patch("src.data.ecmwf_open_data.write_source_run_coverage"),
        patch("src.data.ecmwf_open_data.gate_source", return_value=mock_source_spec),
        patch("src.data.ecmwf_open_data.gate_source_role"),
        patch(
            "src.data.ecmwf_open_data.select_source_run_for_target_horizon",
            return_value=(FetchDecision.FETCH_ALLOWED, mock_sel_meta),
        ),
        patch("src.data.ecmwf_open_data.build_forecast_target_scope", return_value=MagicMock()),
        patch("src.data.ecmwf_open_data.evaluate_horizon_coverage", return_value=MagicMock()),
        patch("src.data.ecmwf_open_data.evaluate_producer_coverage", return_value=MagicMock()),
        patch("src.data.ecmwf_open_data.build_producer_readiness_for_scope", return_value=MagicMock()),
    ):
        ecmwf_open_data.collect_open_ens_cycle(
            track="mx2t6_high",
            run_date=date(2026, 5, 8),
            run_hour=0,
            skip_download=True,
            skip_extract=True,
            conn=injected_conn,
        )

    injected_conn.close()

    assert len(lock_calls) == 0, (
        f"Expected no BULK lock for injected conn (test-seam path), got {lock_calls}"
    )
