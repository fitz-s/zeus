# Created: 2026-05-02
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-02_data_daemon_readiness/PLAN.md PR45b + PLAN_v4 cycle-profile contract.

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.data.release_calendar import (
    FetchDecision,
    cycle_profile_for_hour,
    evaluate_safe_fetch,
    get_entry,
    load_calendar_config,
    select_source_run_for_target_horizon,
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


def test_cycle_profiles_exist_for_00_12_full_and_06_18_short() -> None:
    entry = get_entry("ecmwf_open_data", "mx2t6_high")
    assert entry is not None

    full_profile = cycle_profile_for_hour(entry, 0)
    short_profile = cycle_profile_for_hour(entry, 6)

    assert full_profile is not None
    assert full_profile.cycle_hours_utc == (0, 12)
    assert full_profile.horizon_profile == "full"
    assert full_profile.live_max_step_hours == 240
    assert full_profile.live_authorization is True

    assert short_profile is not None
    assert short_profile.cycle_hours_utc == (6, 18)
    assert short_profile.horizon_profile == "short"
    assert short_profile.live_max_step_hours == 144
    assert short_profile.live_authorization is False


def test_full_horizon_selection_requires_00_or_12_cycle() -> None:
    decision, metadata = select_source_run_for_target_horizon(
        now_utc=_utc(9),
        source_id="ecmwf_open_data",
        track="mx2t6_high",
        required_max_step_hours=240,
    )

    assert decision is FetchDecision.FETCH_ALLOWED
    assert metadata["selected_cycle_time"] == _utc(0)
    assert metadata["horizon_profile"] == "full"


def test_06_18_cycle_blocks_target_requiring_step_over_144() -> None:
    decision, metadata = evaluate_safe_fetch(
        "ecmwf_open_data",
        "mx2t6_high",
        _utc(6),
        _utc(12),
        required_max_step_hours=150,
    )

    assert decision is FetchDecision.HORIZON_OUT_OF_RANGE
    assert metadata["live_max_step_hours"] == 144
    assert metadata["horizon_profile"] == "short"


def test_safe_fetch_blocks_before_default_lag() -> None:
    decision, metadata = evaluate_safe_fetch(
        "ecmwf_open_data",
        "mx2t6_high",
        _utc(0),
        _utc(6),
    )

    assert decision is FetchDecision.SKIPPED_NOT_RELEASED
    assert metadata["lag_minutes_required"] == 485


def test_safe_fetch_for_full_horizon_blocks_0730_before_0805() -> None:
    decision, metadata = evaluate_safe_fetch(
        "ecmwf_open_data",
        "mx2t6_high",
        _utc(0),
        _utc(7, 30),
        required_max_step_hours=240,
    )

    assert decision is FetchDecision.SKIPPED_NOT_RELEASED
    assert metadata["next_safe_fetch_at"] == _utc(8, 5)


def test_required_target_horizon_is_input_to_safe_fetch() -> None:
    decision, metadata = evaluate_safe_fetch(
        "ecmwf_open_data",
        "mx2t6_high",
        _utc(0),
        _utc(9),
        required_max_step_hours=246,
    )

    assert decision is FetchDecision.HORIZON_OUT_OF_RANGE
    assert metadata["required_max_step_hours"] == 246
    assert metadata["live_max_step_hours"] == 240


def test_short_horizon_can_fetch_but_not_live_full_horizon() -> None:
    entry = get_entry("ecmwf_open_data", "mx2t6_high")
    assert entry is not None
    profile = cycle_profile_for_hour(entry, 6)
    assert profile is not None

    decision, metadata = evaluate_safe_fetch(
        "ecmwf_open_data",
        "mx2t6_high",
        _utc(6),
        _utc(11),
        required_max_step_hours=120,
    )

    assert decision is FetchDecision.FETCH_ALLOWED
    assert metadata["horizon_profile"] == "short"
    assert profile.live_authorization is False


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
