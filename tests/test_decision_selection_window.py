# Created: 2026-06-16
# Last reused or audited: 2026-06-16
# Authority basis: fix_decision_selection_window_2026-06-16.md — decision-SELECTION price
#   freshness is a wide window (execution price-freshness is enforced at submission via the
#   fresh JIT witness). Operator design law 2026-05-30: freshness 针对价格不针对市场; price
#   freshness for the traded bin is enforced at SUBMISSION.
"""The DECISION selection gate admits minutes-fresh rows; execution stays the submit authority."""

from datetime import datetime, timedelta, timezone

from src.engine.event_reactor_adapter import (
    _DECISION_SELECTION_PRICE_WINDOW_SECONDS,
    _snapshot_price_stale_reason,
)

NOW = datetime(2026, 6, 16, 4, 0, 0, tzinfo=timezone.utc)


def _row(captured_minutes_ago=None, deadline_seconds_from_now=None):
    row = {}
    if captured_minutes_ago is not None:
        row["captured_at"] = (NOW - timedelta(minutes=captured_minutes_ago)).isoformat()
    if deadline_seconds_from_now is not None:
        row["freshness_deadline"] = (NOW + timedelta(seconds=deadline_seconds_from_now)).isoformat()
    return row


def test_window_is_wide_enough_to_span_capture_cadence():
    # ~5.4min warm-capture cadence must fit inside the selection window.
    assert _DECISION_SELECTION_PRICE_WINDOW_SECONDS >= 324.0


def test_six_minute_old_row_is_selectable_not_stale():
    # The Qingdao class: a 6-min-old row with real edge must NOT be selection-stale
    # (it was "blocked all day" by the 30s gate). Execution re-validates at submit.
    assert _snapshot_price_stale_reason(_row(captured_minutes_ago=6), decision_time=NOW) is None


def test_row_past_selection_window_is_stale():
    reason = _snapshot_price_stale_reason(_row(captured_minutes_ago=20), decision_time=NOW)
    assert reason is not None and reason.startswith("EXECUTABLE_SNAPSHOT_STALE:selection_deadline")


def test_fresh_30s_row_still_passes_pre_change_behaviour():
    # A genuinely-fresh row (captured 10s ago) is selectable, as before.
    assert _snapshot_price_stale_reason(_row(captured_minutes_ago=0), decision_time=NOW) is None


def test_missing_captured_at_falls_back_to_stored_execution_deadline():
    # Fail-safe: no captured_at → use the stored (tight) execution deadline, never looser.
    assert _snapshot_price_stale_reason(_row(deadline_seconds_from_now=-5), decision_time=NOW) is not None
    assert _snapshot_price_stale_reason(_row(deadline_seconds_from_now=+5), decision_time=NOW) is None
