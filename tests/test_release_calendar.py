# Created: 2026-05-02
# Last reused/audited: 2026-05-02
# Authority basis: docs/operations/task_2026-05-02_data_daemon_readiness/PLAN.md PR45b release-calendar contract.

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.data.release_calendar import (
    FetchDecision,
    evaluate_safe_fetch,
    get_entry,
    load_calendar_config,
    source_has_live_authorization,
)


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 5, 2, hour, minute, tzinfo=timezone.utc)


def test_calendar_loads_config_with_typed_entries() -> None:
    entries = load_calendar_config()

    ecmwf_high = entries[("ecmwf_open_data", "mx2t6_high")]
    assert ecmwf_high.default_lag_minutes == 485
    assert ecmwf_high.min_partial_lag_minutes == 400
    assert ecmwf_high.expected_members == 51
    assert ecmwf_high.partial_policy == "BLOCK_LIVE"


def test_safe_fetch_blocks_before_default_lag() -> None:
    decision, metadata = evaluate_safe_fetch(
        "ecmwf_open_data",
        "mx2t6_high",
        _utc(0),
        _utc(6),
    )

    assert decision is FetchDecision.SKIPPED_NOT_RELEASED
    assert metadata["lag_minutes_required"] == 485


def test_safe_fetch_allows_after_default_lag() -> None:
    decision, metadata = evaluate_safe_fetch(
        "ecmwf_open_data",
        "mx2t6_high",
        _utc(0),
        _utc(8, 6),
    )

    assert decision is FetchDecision.FETCH_ALLOWED
    assert metadata["live_authorization"] is True


def test_off_cycle_source_cycle_fails_closed() -> None:
    decision, metadata = evaluate_safe_fetch(
        "ecmwf_open_data",
        "mx2t6_high",
        _utc(3),
        _utc(12),
    )

    assert decision is FetchDecision.OFF_CYCLE_BLOCKED
    assert 3 not in metadata["configured_cycle_hours_utc"]


def test_partial_window_blocks_live_when_policy_blocks_partial() -> None:
    decision, metadata = evaluate_safe_fetch(
        "ecmwf_open_data",
        "mx2t6_high",
        _utc(0),
        _utc(6, 45),
        allow_partial=True,
    )

    assert decision is FetchDecision.PARTIAL_EXPECTED_RETRY
    assert metadata["next_safe_fetch_at"] == _utc(8, 5)


def test_backfill_only_track_never_authorizes_live_fetch() -> None:
    decision, metadata = evaluate_safe_fetch(
        "tigge",
        "archive",
        _utc(0),
        _utc(13),
    )

    assert decision is FetchDecision.BACKFILL_ONLY_BLOCKED
    assert "backfill-only" in metadata["reason"]
    assert source_has_live_authorization("tigge", "archive") is False


def test_unknown_calendar_entry_fails_closed() -> None:
    decision, metadata = evaluate_safe_fetch(
        "unknown_provider",
        "default",
        _utc(0),
        _utc(9),
    )

    assert decision is FetchDecision.CALENDAR_UNKNOWN_BLOCKED
    assert metadata["reason"] == "calendar entry missing"


def test_stale_source_cycle_blocks_even_after_release() -> None:
    decision, metadata = evaluate_safe_fetch(
        "ecmwf_open_data",
        "mx2t6_high",
        _utc(0),
        _utc(0) + timedelta(seconds=108001),
    )

    assert decision is FetchDecision.STALE_BLOCKED
    assert metadata["elapsed_seconds"] == 108001


def test_calendar_requires_aware_timestamps() -> None:
    with pytest.raises(ValueError, match="cycle_time must be timezone-aware"):
        evaluate_safe_fetch(
            "ecmwf_open_data",
            "mx2t6_high",
            datetime(2026, 5, 2, 0, 0),
            _utc(9),
        )


def test_get_entry_returns_none_for_missing_track() -> None:
    assert get_entry("ecmwf_open_data", "missing_track") is None
