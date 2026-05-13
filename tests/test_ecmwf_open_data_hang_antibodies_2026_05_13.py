# Created: 2026-05-13
# Last reused or audited: 2026-05-13
# Authority basis: ECMWF hang antibody bundle
#   - /tmp/zeus_ecmwf_critic_review.md (WAL=0 disproves inner-loop hang)
#   - /tmp/zeus_module_audit.md (3 orthogonal antibody recommendations)
#   - ~/.claude/CLAUDE.md "Test relationships, not just functions": relationship
#     tests come BEFORE function tests.
#
# Relationship invariants under test:
#
#   #1 Import-locality:
#      Importing ``src.data.ecmwf_open_data`` MUST NOT block on any I/O the
#      BULK writer-lock would otherwise serialize. Specifically:
#      ``from ingest_grib_to_snapshots import ...`` must be eagerly resolved
#      at module-load time (outside the BULK ``with`` block), so the first
#      ``collect_open_ens_cycle`` call cannot hang on first-time module init
#      while holding the forecasts.db BULK flock.
#
#   #2 rglob timeout-guard:
#      ``run_with_timeout(..., seconds=30, label="rglob_json_scan")`` must
#      raise ``TimeoutError`` when the wrapped callable does not return
#      within the budget. Without this antibody, a stale ``51 source data``
#      mount would let ``Path.rglob`` block indefinitely while holding the
#      BULK writer-lock (witnessed 2026-05-12 13:31 PDT).
#
#   #3 Boundary INFO logs:
#      ``collect_open_ens_cycle`` must emit at least one INFO log line at
#      each of the critical boundaries inside the BULK ``with`` block
#      (lock acquired, schema asserted, rglob start, rglob end, commit
#      start, commit end). Without these, the next hang produces 12h of
#      silence and we re-run this diagnostic chase.
"""Antibody regression tests for the 2026-05-12 ECMWF hang."""
from __future__ import annotations

import logging
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Antibody #1 — imports moved outside BULK lock (module-top eager-import)
# ---------------------------------------------------------------------------


def test_antibody1_ingest_grib_imported_at_module_top():
    """``ecmwf_open_data`` must have already resolved
    ``ingest_grib_to_snapshots.ingest_track`` and ``SourceRunContext``
    at module-load time — NOT lazily inside the BULK ``with`` block.

    If this fails, the first ``collect_open_ens_cycle`` call still pays
    a first-time import cost (and module-load lock) while holding the
    forecasts.db BULK flock — the exact failure shape of the 2026-05-12
    daemon hang (WAL=0 bytes, no SQL frame ever written).
    """
    import src.data.ecmwf_open_data as ed
    # The eager-import antibody binds these as module attributes.
    assert hasattr(ed, "_ingest_grib_ingest_track"), (
        "ecmwf_open_data must expose _ingest_grib_ingest_track at module top "
        "(eager-import antibody for ECMWF hang 2026-05-12)."
    )
    assert hasattr(ed, "_ingest_grib_SourceRunContext"), (
        "ecmwf_open_data must expose _ingest_grib_SourceRunContext at "
        "module top (eager-import antibody for ECMWF hang 2026-05-12)."
    )
    assert hasattr(ed, "_ingest_grib_module"), (
        "ecmwf_open_data must expose _ingest_grib_module at module top "
        "for in-process _TRACK_CONFIGS rebind (eager-import antibody)."
    )
    assert hasattr(ed, "assert_schema_current_forecasts"), (
        "ecmwf_open_data must re-export assert_schema_current_forecasts "
        "at module top (eager-import antibody)."
    )


def test_antibody1_no_lazy_import_inside_collect_open_ens_cycle():
    """The textual body of ``collect_open_ens_cycle`` must contain no
    ``from ingest_grib_to_snapshots import ...`` statement. Source-level
    grep-gate: prevents a future edit from re-introducing the lazy import
    pattern that caused the 2026-05-12 hang.
    """
    import inspect

    import src.data.ecmwf_open_data as ed

    source = inspect.getsource(ed.collect_open_ens_cycle)
    assert "from ingest_grib_to_snapshots import" not in source, (
        "collect_open_ens_cycle MUST NOT lazy-import ingest_grib_to_snapshots "
        "inside the BULK lock. Move the import to module-top (antibody #1)."
    )
    assert "import ingest_grib_to_snapshots" not in source, (
        "collect_open_ens_cycle MUST NOT lazy-import ingest_grib_to_snapshots "
        "module reference inside the BULK lock (antibody #1)."
    )


# ---------------------------------------------------------------------------
# Antibody #2 — run_with_timeout fires when wrapped callable stalls
# ---------------------------------------------------------------------------


def test_antibody2_timeout_guard_fires_on_blocked_callable():
    """``run_with_timeout`` must raise ``TimeoutError`` when the inner
    callable does not return within the budget. This is the relationship
    that lets us fail loud on a stale ``51 source data`` mount instead
    of holding the BULK flock for 12h.
    """
    from src.runtime.timeout_guard import run_with_timeout

    def _blocker():
        time.sleep(5.0)  # > our 0.3s budget below
        return "should_never_return"

    with pytest.raises(TimeoutError, match="rglob_json_scan exceeded 0.3s"):
        run_with_timeout(_blocker, seconds=0.3, label="rglob_json_scan")


def test_antibody2_timeout_guard_passes_fast_callable():
    """Sanity: when the inner callable returns within the budget, the
    helper returns the callable's value unchanged."""
    from src.runtime.timeout_guard import run_with_timeout

    result = run_with_timeout(lambda: 42, seconds=2.0, label="unit_test")
    assert result == 42


def test_antibody2_timeout_guard_propagates_inner_exception():
    """Sanity: a non-timeout exception from the inner callable must
    propagate unchanged (NOT be re-wrapped as TimeoutError)."""
    from src.runtime.timeout_guard import run_with_timeout

    class _MarkerError(RuntimeError):
        pass

    def _raiser():
        raise _MarkerError("boom")

    with pytest.raises(_MarkerError, match="boom"):
        run_with_timeout(_raiser, seconds=2.0, label="unit_test")


def test_antibody2_ingest_track_rglob_is_timeout_guarded():
    """``scripts/ingest_grib_to_snapshots.ingest_track`` must wrap its
    ``rglob`` scan in ``run_with_timeout`` so a stale mount fails loud
    instead of holding the BULK flock for 12h.

    Source-level grep-gate: prevents future edits from undoing the
    antibody. Citation: 2026-05-12 hang in ``_opendata_startup_catch_up``,
    WAL=0 bytes ⇒ hang happened before any SQL write, candidate is the
    rglob walk of the FIFTY_ONE_ROOT mount.
    """
    import inspect

    # Re-use the same sys.path bootstrap as production callers.
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import ingest_grib_to_snapshots as igs  # noqa: E402

    source = inspect.getsource(igs.ingest_track)
    assert "run_with_timeout" in source, (
        "ingest_track must wrap subdir.rglob(...) in run_with_timeout "
        "(antibody #2). If you renamed the helper, update this test."
    )
    assert "rglob_json_scan" in source, (
        "ingest_track must use label='rglob_json_scan' so the timeout "
        "appears with a known string in production logs (antibody #2)."
    )


# ---------------------------------------------------------------------------
# Antibody #3 — boundary INFO logs inside collect_open_ens_cycle ingest stage
# ---------------------------------------------------------------------------


def test_antibody3_ingest_stage_emits_boundary_info_logs(tmp_path, caplog, monkeypatch):
    """When ``collect_open_ens_cycle`` enters its ingest stage it MUST
    emit at least one INFO log line for each of the following boundaries
    (case-insensitive substring match against the log message):

      * "ingest_stage": lock_acquired
      * "ingest_stage": schema_ok
      * "ingest_stage": rglob_start
      * "ingest_stage": rglob_end
      * "ingest_stage": commit_start
      * "ingest_stage": commit_end

    Without these, a future hang inside the BULK lock produces 12h of
    silence and we re-run the 2026-05-12 diagnostic chase.
    """
    from src.data import ecmwf_open_data as ed
    from src.state.db import init_schema_forecasts

    monkeypatch.setattr(ed, "FIFTY_ONE_ROOT", tmp_path / "51 source data")
    monkeypatch.setattr(ed, "STEP_HOURS", [3])

    # Pre-create the extract output dir with zero JSON files so ingest
    # short-circuits cleanly (we just want to observe the boundary logs).
    extract_subdir = (
        tmp_path
        / "51 source data"
        / "raw"
        / "open_ens_mx2t6_localday_max"
    )
    extract_subdir.mkdir(parents=True, exist_ok=True)

    import sqlite3

    db_path = tmp_path / "forecasts.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)

    def _ok_fetch_impl(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
        canonical = output_dir / f".step{step:03d}_{param}.grib2"
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_bytes(b"\x00" * 16)
        return ("OK", canonical)

    caplog.set_level(logging.INFO, logger="src.data.ecmwf_open_data")

    ed.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=date(2026, 5, 13),
        run_hour=0,
        now_utc=datetime(2026, 5, 13, 9, 0, tzinfo=timezone.utc),
        _fetch_impl=_ok_fetch_impl,
        skip_extract=True,  # we hand-created the JSON dir
        conn=conn,
    )

    # Gather every INFO+ message emitted by ecmwf_open_data.
    messages = [
        rec.getMessage()
        for rec in caplog.records
        if rec.name == "src.data.ecmwf_open_data"
        and rec.levelno >= logging.INFO
    ]
    joined = " | ".join(messages).lower()

    expected_markers = (
        "lock_acquired",
        "schema_ok",
        "rglob_start",
        "rglob_end",
        "commit_start",
        "commit_end",
    )
    missing = [m for m in expected_markers if m not in joined]
    assert not missing, (
        f"collect_open_ens_cycle missing boundary INFO logs: {missing}. "
        f"Saw: {messages}"
    )
