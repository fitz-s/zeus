# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Watermark (source-time not write-time) + bounded backfill + UMA era-guard default.
# Reuse: Inspect docs/operations/current/plans/data_temporal_kernel/PLAN.md + the target module before relying on it.
# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: docs/operations/current/plans/data_temporal_kernel/PLAN.md (PR5);
#   operator spec §7 (Watermarks, Backfill planner) + §"Backfill can look fresh".
"""PR5: source watermarks (in-memory) + bounded backfill planner + UMA era guard default."""
from __future__ import annotations

import sqlite3

import pytest


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE source_run (source_id TEXT, track TEXT, target_local_date TEXT, "
        "status TEXT, observed_members INTEGER, captured_at TEXT)"
    )
    return c


def _ins(c: sqlite3.Connection, date: str, status: str, members: int, captured: str) -> None:
    c.execute(
        "INSERT INTO source_run (source_id, track, target_local_date, status, observed_members, captured_at) "
        "VALUES ('ecmwf_open_data','mx2t6_high',?,?,?,?)",
        (date, status, members, captured),
    )


def test_watermark_tracks_partitions_by_source_date_not_write_time() -> None:
    """ANTIBODY: a catch-up writing a FRESH captured_at for an OLD partition must not advance
    the successful watermark — partitions order by target_local_date, not write time."""
    from src.data.source_watermarks import compute_watermark

    c = _conn()
    _ins(c, "2026-05-20", "ok", 51, "2026-05-20T09:00:00Z")        # real latest success
    _ins(c, "2026-05-18", "ok", 51, "2026-05-24T11:59:00Z")        # OLD partition, FRESH write (catch-up)
    _ins(c, "2026-05-21", "failed", 0, "2026-05-21T09:00:00Z")     # newer date but failed

    wm = compute_watermark(c, "ecmwf_open_data", "mx2t6_high")
    assert wm.last_attempted_partition == "2026-05-21"            # latest attempt (incl failed)
    assert wm.last_successful_partition == "2026-05-20"           # NOT 2026-05-18 (fresh write ignored)
    assert wm.last_non_empty_partition == "2026-05-20"
    assert wm.successful_count == 2


def test_watermark_empty_on_missing_table() -> None:
    from src.data.source_watermarks import compute_watermark

    c = sqlite3.connect(":memory:")  # no source_run table
    wm = compute_watermark(c, "ecmwf_open_data", "mx2t6_high")
    assert wm.last_successful_partition is None
    assert wm.attempted_count == 0


def test_backfill_planner_bounds_windows() -> None:
    from src.data.backfill_planner import UnboundedBackfillRefused, plan_backfill

    tasks = plan_backfill("tigge", "backfill", "2026-05-01", "2026-05-03")
    assert len(tasks) == 1 and tasks[0].partition_start == "2026-05-01"

    with pytest.raises(UnboundedBackfillRefused):
        plan_backfill("tigge", "backfill", None, "2026-05-03")          # missing start
    with pytest.raises(UnboundedBackfillRefused):
        plan_backfill("tigge", "backfill", "2026-05-03", "2026-05-01")  # end < start
    with pytest.raises(UnboundedBackfillRefused):
        plan_backfill("tigge", "backfill", None, None, allow_unbounded=True)  # allowed but no bounds


def test_backfill_is_never_live() -> None:
    from src.data.backfill_planner import (
        UnboundedBackfillRefused,
        assert_backfill_not_live,
        plan_backfill,
    )

    # A live role can never request backfill.
    with pytest.raises(UnboundedBackfillRefused):
        plan_backfill("ecmwf_open_data", "live", "2026-05-01", "2026-05-03")

    task = plan_backfill("tigge", "backfill", "2026-05-01", "2026-05-03")[0]
    assert task.live_authorization is False
    assert_backfill_not_live(task)  # does not raise


def test_uma_era_guard_default_is_behavior_preserving() -> None:
    """era_end_block defaults to 0 (disabled) → the UMA listener scans exactly as before PR5."""
    from src.ingest_main import _uma_era_end_block

    assert _uma_era_end_block() == 0
