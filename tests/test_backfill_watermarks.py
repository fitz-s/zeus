# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Watermark against REAL source_run shape (NULL target_local_date / source_issue_time
#   cycle partition, horizon-expanded track) + bounded backfill (ISO guard) + UMA era latch.
# Reuse: Inspect docs/operations/current/plans/data_temporal_kernel/PLAN.md + the target module before relying on it.
"""PR5 + PR review #329 F3/F11: watermarks on real OpenData source_run shape; backfill ISO guard."""
from __future__ import annotations

import sqlite3

import pytest

_SR_COLS = [
    "source_id", "track", "release_calendar_key", "source_issue_time", "source_cycle_time",
    "target_local_date", "status", "observed_members",
]


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute(f"CREATE TABLE source_run ({' TEXT, '.join(_SR_COLS)} TEXT)")
    return c


def _ins(c: sqlite3.Connection, **kw: object) -> None:
    c.execute(
        f"INSERT INTO source_run ({', '.join(_SR_COLS)}) VALUES ({', '.join('?' for _ in _SR_COLS)})",
        tuple(kw.get(col) for col in _SR_COLS),
    )


# Real OpenData source-level identity: horizon-expanded track, release_calendar_key, NO target_local_date.
def _high(issue: str, status: str, members: int) -> dict:
    return dict(
        source_id="ecmwf_open_data", track="mx2t6_high_full_horizon",
        release_calendar_key="ecmwf_open_data:mx2t6_high:full",
        source_issue_time=issue, source_cycle_time=issue, target_local_date=None,
        status=status, observed_members=members,
    )


def test_opendata_watermark_uses_source_issue_time_when_target_local_date_null() -> None:
    """F3: OpenData source_run has NULL target_local_date — watermark must still advance using
    source_issue_time (the cycle partition), and match the horizon-expanded track."""
    from src.data.source_watermarks import compute_watermark

    c = _conn()
    _ins(c, **_high("2026-05-20T00:00:00Z", "SUCCESS", 51))
    _ins(c, **_high("2026-05-22T00:00:00Z", "SUCCESS", 51))
    _ins(c, **_high("2026-05-23T12:00:00Z", "FAILED", 0))

    wm = compute_watermark(c, "ecmwf_open_data", "mx2t6_high")   # calendar track
    assert wm.last_attempted_partition == "2026-05-23T12:00:00Z"
    assert wm.last_successful_partition == "2026-05-22T00:00:00Z"   # NOT empty (the F3 bug)
    assert wm.successful_count == 2


def test_catch_up_cannot_mask_staleness_by_issue_time() -> None:
    """An old cycle re-attempted does not advance the successful watermark past a newer cycle."""
    from src.data.source_watermarks import compute_watermark

    c = _conn()
    _ins(c, **_high("2026-05-23T00:00:00Z", "SUCCESS", 51))
    _ins(c, **_high("2026-05-18T00:00:00Z", "SUCCESS", 51))   # old cycle, inserted later
    wm = compute_watermark(c, "ecmwf_open_data", "mx2t6_high")
    assert wm.last_successful_partition == "2026-05-23T00:00:00Z"   # ordered by issue, not insert/write


def test_watermark_empty_on_missing_table() -> None:
    from src.data.source_watermarks import compute_watermark

    wm = compute_watermark(sqlite3.connect(":memory:"), "ecmwf_open_data", "mx2t6_high")
    assert wm.last_successful_partition is None and wm.attempted_count == 0


def test_backfill_planner_bounds_windows() -> None:
    from src.data.backfill_planner import UnboundedBackfillRefused, plan_backfill

    assert len(plan_backfill("tigge", "backfill", "2026-05-01", "2026-05-03")) == 1
    with pytest.raises(UnboundedBackfillRefused):
        plan_backfill("tigge", "backfill", None, "2026-05-03")
    with pytest.raises(UnboundedBackfillRefused):
        plan_backfill("tigge", "backfill", "2026-05-03", "2026-05-01")
    with pytest.raises(UnboundedBackfillRefused):
        plan_backfill("tigge", "backfill", None, None, allow_unbounded=True)


def test_backfill_planner_rejects_non_iso_partitions() -> None:
    """F11: lexicographic window compare is only valid for ISO dates — non-ISO (e.g. block
    numbers) must be refused, not silently mis-ordered."""
    from src.data.backfill_planner import UnboundedBackfillRefused, plan_backfill

    with pytest.raises(UnboundedBackfillRefused):
        plan_backfill("uma", "backfill", "9", "100")          # block numbers, not ISO
    with pytest.raises(UnboundedBackfillRefused):
        plan_backfill("tigge", "backfill", "2026-05-01", "20260503")  # non-ISO end


def test_backfill_is_never_live() -> None:
    from src.data.backfill_planner import UnboundedBackfillRefused, assert_backfill_not_live, plan_backfill

    with pytest.raises(UnboundedBackfillRefused):
        plan_backfill("ecmwf_open_data", "live", "2026-05-01", "2026-05-03")
    task = plan_backfill("tigge", "backfill", "2026-05-01", "2026-05-03")[0]
    assert task.live_authorization is False
    assert_backfill_not_live(task)


def test_uma_era_guard_default_is_behavior_preserving() -> None:
    from src.ingest_main import _uma_era_end_block, _uma_era_exhausted

    assert _uma_era_end_block() == 0
    assert _uma_era_exhausted is False


def test_short_horizon_success_does_not_advance_full_watermark() -> None:
    """F5: a 06/18 short-horizon success must not advance the live FULL-horizon watermark."""
    from src.data.source_watermarks import compute_watermark

    c = _conn()
    _ins(c, source_id="ecmwf_open_data", track="mx2t6_high_full_horizon",
         release_calendar_key="ecmwf_open_data:mx2t6_high:full",
         source_issue_time="2026-05-22T00:00:00Z", source_cycle_time="2026-05-22T00:00:00Z",
         target_local_date=None, status="SUCCESS", observed_members="51")
    _ins(c, source_id="ecmwf_open_data", track="mx2t6_high_short_horizon",
         release_calendar_key="ecmwf_open_data:mx2t6_high:short",
         source_issue_time="2026-05-23T06:00:00Z", source_cycle_time="2026-05-23T06:00:00Z",
         target_local_date=None, status="SUCCESS", observed_members="51")
    full = compute_watermark(c, "ecmwf_open_data", "mx2t6_high", horizon_profile="full")
    assert full.last_successful_partition == "2026-05-22T00:00:00Z"   # NOT advanced by short
