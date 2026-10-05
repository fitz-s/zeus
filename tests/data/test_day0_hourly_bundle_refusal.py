# Created: 2026-10-05
# Last reused/audited: 2026-10-05
# Authority basis: Day0 carrier coverage task (F3): 3,061 VECTOR_MISSING blocks on
#   2026-10-04 carried no cause, so absence, staleness and an incomplete remaining
#   window were indistinguishable to the producer and to operators.
"""An empty strict Day0 bundle read names why it is empty."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from src.data.day0_hourly_vectors import (
    Day0HourlyBundleRefusal,
    Day0HourlyVector,
    select_ready_day0_hourly_vectors,
)

UTC = timezone.utc
DECISION = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
TARGET = "2026-10-05"


def _vector(model: str, *, captured: datetime, first_hour: int = 0) -> Day0HourlyVector:
    times = tuple(f"{TARGET}T{hour:02d}:00" for hour in range(first_hour, 24))
    return Day0HourlyVector(
        model=model, city="Warsaw", target_date=TARGET, timezone_name="UTC",
        captured_at=captured.isoformat(), times=times,
        temps_c=tuple(15.0 for _ in times),
        source_run_meta_json=json.dumps({
            "fetch_started_at": captured.isoformat(),
            "fetch_finished_at": (captured + timedelta(seconds=5)).isoformat(),
        }),
    )


def _read(vectors):
    return select_ready_day0_hourly_vectors(
        vectors,
        target_date=TARGET,
        now=DECISION,
        expected_models=["ecmwf_ifs", "icon_global"],
        require_expected=True,
        remaining_window_start=DECISION - timedelta(minutes=20),
        require_complete_remaining_window=True,
    )


def test_missing_expected_model_is_absent():
    result = _read([_vector("ecmwf_ifs", captured=DECISION - timedelta(minutes=10))])
    assert result == []
    assert result.refusal is Day0HourlyBundleRefusal.ABSENT


def test_rows_outside_the_capture_horizon_are_stale():
    old = DECISION - timedelta(hours=4)
    result = _read([_vector("ecmwf_ifs", captured=old), _vector("icon_global", captured=old)])
    assert result == []
    assert result.refusal is Day0HourlyBundleRefusal.STALE


def test_current_rows_missing_the_remaining_window_are_window_incomplete():
    fresh = DECISION - timedelta(minutes=10)
    result = _read([
        _vector("ecmwf_ifs", captured=fresh),
        _vector("icon_global", captured=fresh, first_hour=11),
    ])
    assert result == []
    assert result.refusal is Day0HourlyBundleRefusal.WINDOW_INCOMPLETE


def test_complete_bundle_carries_no_refusal():
    fresh = DECISION - timedelta(minutes=10)
    result = _read([_vector("ecmwf_ifs", captured=fresh), _vector("icon_global", captured=fresh)])
    assert [vector.model for vector in result] == ["ecmwf_ifs", "icon_global"]
    assert result.refusal is None
