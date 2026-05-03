# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-02_live_entry_data_contract/PLAN_v4.md Phase 5A Open Data source-run selection contract.
"""Open Data producer source-run selection contract tests."""

from __future__ import annotations

from datetime import datetime, timezone

from src.data import ecmwf_open_data
from src.data.release_calendar import FetchDecision

UTC = timezone.utc


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 5, 2, hour, minute, tzinfo=UTC)


def test_opendata_no_longer_exposes_default_cycle_selector() -> None:
    assert not hasattr(ecmwf_open_data, "_default_cycle")


def test_opendata_selection_skips_06z_for_full_horizon_at_1400() -> None:
    decision, metadata = ecmwf_open_data._select_cycle_for_track(
        track="mx2t6_high",
        now_utc=_utc(14),
    )

    assert decision is FetchDecision.FETCH_ALLOWED
    assert metadata["selected_cycle_time"] == _utc(0)
    assert metadata["horizon_profile"] == "full"


def test_opendata_selection_uses_12z_after_full_horizon_release() -> None:
    decision, metadata = ecmwf_open_data._select_cycle_for_track(
        track="mn2t6_low",
        now_utc=_utc(21),
    )

    assert decision is FetchDecision.FETCH_ALLOWED
    assert metadata["selected_cycle_time"] == _utc(12)
    assert metadata["horizon_profile"] == "full"


def test_opendata_selection_rejects_unknown_track() -> None:
    try:
        ecmwf_open_data._select_cycle_for_track(track="unknown", now_utc=_utc(14))
    except ValueError as exc:
        assert "Unknown track" in str(exc)
    else:  # pragma: no cover - defensive assertion branch
        raise AssertionError("unknown Open Data track should fail closed")
