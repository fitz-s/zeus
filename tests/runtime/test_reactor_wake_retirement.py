# Created: 2026-09-29
# Last reused or audited: 2026-09-29
# Authority basis: cut-cancel throughput task (2026-09-29): the wake backlog is
#   bounded by reachability and consumption, not age.
"""A queued hint no consumer can still serve is retired through the one ack path."""

from __future__ import annotations

import datetime as _dt

import pytest

from src.runtime import reactor_wake
from src.strategy.market_phase import earliest_reachable_target_date

NOW = _dt.datetime(2026, 9, 29, 12, 0, tzinfo=_dt.timezone.utc)
PAST = ("Dallas", "2026-09-26", "high")  # below the 2026-09-28 floor
CURRENT = ("Dallas", "2026-09-30", "high")


@pytest.fixture(autouse=True)
def _empty_ledger():
    reactor_wake._CONSUMED_SCOPE.clear()
    yield
    reactor_wake._CONSUMED_SCOPE.clear()


def _publish(path, reason, families, *, at, event_ids=()):
    return reactor_wake.publish_reactor_wake(
        source="test",
        reason=reason,
        path=path,
        published_at=at,
        forecast_families=families,
        event_ids=event_ids,
    )


def _queued_ids(path):
    return {wake.wake_id for _file, wake in reactor_wake._queued_wakes(path)}


def test_reachable_floor_is_utc_yesterday():
    assert earliest_reachable_target_date(NOW) == "2026-09-28"
    with pytest.raises(ValueError):
        earliest_reachable_target_date(NOW.replace(tzinfo=None))


def test_unreachable_forecast_and_substrate_wakes_are_retired(tmp_path):
    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    at = NOW - _dt.timedelta(hours=30)
    past_forecast = _publish(path, "forecast_posterior_advanced", (PAST,), at=at)
    past_substrate = _publish(path, "money_path_substrate_refreshed", (PAST,), at=at)
    mixed = _publish(path, "forecast_posterior_advanced", (PAST, CURRENT), at=at)
    past_day0 = _publish(
        path, "day0_extreme_event_committed", (PAST,), at=at, event_ids=("e",)
    )
    familyless = _publish(path, "forecast_posterior_advanced", (), at=at)

    assert reactor_wake.retire_served_wakes(now=NOW, path=path) == 2

    queued = _queued_ids(path)
    assert past_forecast.wake_id not in queued
    assert past_substrate.wake_id not in queued
    # A reachable family, a hard fact or an unscoped hint is never retired.
    assert {mixed.wake_id, past_day0.wake_id, familyless.wake_id} <= queued


def test_forecast_wake_consumed_by_a_later_completed_cut_is_retired(tmp_path):
    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    published = NOW - _dt.timedelta(minutes=10)
    wake = _publish(path, "forecast_posterior_advanced", (CURRENT,), at=published)
    substrate = _publish(
        path, "money_path_substrate_refreshed", (CURRENT,), at=published
    )

    # A cut whose scope scan began before the wake cannot have absorbed it.
    reactor_wake.record_consumed_scope(
        (CURRENT,), consumed_at=published - _dt.timedelta(seconds=1)
    )
    assert reactor_wake.retire_served_wakes(now=NOW, path=path) == 0

    reactor_wake.record_consumed_scope(
        (CURRENT,), consumed_at=published + _dt.timedelta(seconds=1)
    )
    assert reactor_wake.retire_served_wakes(now=NOW, path=path) == 1
    queued = _queued_ids(path)
    assert wake.wake_id not in queued
    # A substrate hint asks for a redecision screen, which a cut does not run.
    assert substrate.wake_id in queued


def test_retirement_is_bounded_per_call(tmp_path):
    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    for index in range(5):
        _publish(
            path,
            "forecast_posterior_advanced",
            (PAST,),
            at=NOW - _dt.timedelta(hours=30, seconds=index),
        )
    assert reactor_wake.retire_served_wakes(now=NOW, path=path, limit=2) == 2
    assert len(_queued_ids(path)) == 3
