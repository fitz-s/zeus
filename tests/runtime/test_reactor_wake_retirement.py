# Created: 2026-09-29
# Last reused or audited: 2026-09-29
# Authority basis: cut-cancel throughput task (2026-09-29): the wake backlog is
#   bounded by the one family-reachability law (src.data.forecast_retention),
#   and consumption, never by age.
"""A queued hint no consumer can still serve is retired through the one ack path."""

from __future__ import annotations

import datetime as _dt
import sqlite3
from types import SimpleNamespace

import pytest

from src.runtime import reactor_wake

NOW = _dt.datetime(2026, 9, 29, 12, 0, tzinfo=_dt.timezone.utc)
PAST = ("Dallas", "2026-09-26", "high")  # older than today-2 (2026-09-27)
HELD = ("Hong Kong", "2026-09-20", "high")  # old, but a position is open on it
CURRENT = ("Dallas", "2026-09-30", "high")


@pytest.fixture(autouse=True)
def _empty_ledger():
    reactor_wake._CONSUMED_SCOPE.clear()
    yield
    reactor_wake._CONSUMED_SCOPE.clear()


def _trade_db(path, *positions):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE position_current (position_id TEXT PRIMARY KEY, phase TEXT,"
        " city TEXT, target_date TEXT, temperature_metric TEXT)"
    )
    conn.executemany("INSERT INTO position_current VALUES (?,?,?,?,?)", positions)
    conn.execute(
        "CREATE TABLE venue_commands (command_id TEXT, position_id TEXT, venue_order_id TEXT,"
        " token_id TEXT, snapshot_id TEXT, state TEXT, intent_kind TEXT)"
    )
    conn.execute(
        "CREATE TABLE venue_order_facts (venue_order_id TEXT, state TEXT, remaining_size REAL,"
        " local_sequence INTEGER)"
    )
    conn.execute(
        "CREATE TABLE executable_market_snapshots (snapshot_id TEXT, event_slug TEXT,"
        " selected_outcome_token_id TEXT, captured_at TEXT)"
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def trade(tmp_path):
    return _trade_db(tmp_path / "trade.db")


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


def test_unreachable_forecast_and_substrate_wakes_are_retired(tmp_path, trade):
    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    at = NOW - _dt.timedelta(hours=30)
    past_forecast = _publish(path, "forecast_posterior_advanced", (PAST,), at=at)
    past_substrate = _publish(path, "money_path_substrate_refreshed", (PAST,), at=at)
    mixed = _publish(path, "forecast_posterior_advanced", (PAST, CURRENT), at=at)
    past_day0 = _publish(
        path, "day0_extreme_event_committed", (PAST,), at=at, event_ids=("e",)
    )
    familyless = _publish(path, "forecast_posterior_advanced", (), at=at)

    assert reactor_wake.retire_served_wakes(now=NOW, path=path, trade_db=trade) == 2

    queued = _queued_ids(path)
    assert past_forecast.wake_id not in queued
    assert past_substrate.wake_id not in queued
    # A reachable family, a hard fact or an unscoped hint is never retired.
    assert {mixed.wake_id, past_day0.wake_id, familyless.wake_id} <= queued


@pytest.mark.parametrize("phase", ("active", "day0_window", "pending_exit", "economically_closed"))
def test_old_family_with_open_position_keeps_its_wake(tmp_path, phase):
    """A held old-date family's forecast wake is its durable monitor retry
    trigger; it stays queued until the position is terminal."""

    trade = _trade_db(tmp_path / "trade.db", ("p1", phase, *HELD))
    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    at = NOW - _dt.timedelta(hours=30)
    held = _publish(path, "forecast_posterior_advanced", (HELD,), at=at)

    assert reactor_wake.retire_served_wakes(now=NOW, path=path, trade_db=trade) == 0
    assert held.wake_id in _queued_ids(path)


def test_old_family_with_terminal_position_is_retired(tmp_path):
    trade = _trade_db(tmp_path / "trade.db", ("p1", "settled", *HELD))
    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    held = _publish(
        path, "forecast_posterior_advanced", (HELD,), at=NOW - _dt.timedelta(hours=30)
    )

    assert reactor_wake.retire_served_wakes(now=NOW, path=path, trade_db=trade) == 1
    assert held.wake_id not in _queued_ids(path)


def test_old_family_with_open_entry_rest_keeps_its_wake(tmp_path, trade):
    conn = sqlite3.connect(trade)
    conn.execute(
        "INSERT INTO venue_commands VALUES ('c1', '', 'o1', 'tok', 'snap', 'ACKED', 'ENTRY')"
    )
    conn.execute("INSERT INTO venue_order_facts VALUES ('o1', 'LIVE', 5.0, 1)")
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES"
        " ('snap', 'highest-temperature-in-dallas-on-september-26-2026', 'tok', '2026-09-25')"
    )
    conn.commit()
    conn.close()
    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    rested = _publish(
        path, "forecast_posterior_advanced", (PAST,), at=NOW - _dt.timedelta(hours=30)
    )

    assert reactor_wake.retire_served_wakes(now=NOW, path=path, trade_db=trade) == 0
    assert rested.wake_id in _queued_ids(path)


@pytest.mark.parametrize("fault", ("missing_db", "unnamed_open_position"))
def test_unknown_reachability_retires_nothing(tmp_path, fault):
    trade = tmp_path / "trade.db"
    if fault == "unnamed_open_position":
        _trade_db(trade, ("p1", "active", None, None, None))
    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    past = _publish(
        path, "forecast_posterior_advanced", (PAST,), at=NOW - _dt.timedelta(hours=30)
    )

    assert reactor_wake.retire_served_wakes(now=NOW, path=path, trade_db=trade) == 0
    assert past.wake_id in _queued_ids(path)


def test_forecast_wake_consumed_by_a_later_completed_cut_is_retired(tmp_path, trade):
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
    assert reactor_wake.retire_served_wakes(now=NOW, path=path, trade_db=trade) == 0

    reactor_wake.record_consumed_scope(
        (CURRENT,), consumed_at=published + _dt.timedelta(seconds=1)
    )
    assert reactor_wake.retire_served_wakes(now=NOW, path=path, trade_db=trade) == 1
    queued = _queued_ids(path)
    assert wake.wake_id not in queued
    # A substrate hint asks for a redecision screen, which a cut does not run.
    assert substrate.wake_id in queued


def test_retirement_is_bounded_per_call(tmp_path, trade):
    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    for index in range(5):
        _publish(
            path,
            "forecast_posterior_advanced",
            (PAST,),
            at=NOW - _dt.timedelta(hours=30, seconds=index),
        )
    assert (
        reactor_wake.retire_served_wakes(now=NOW, path=path, trade_db=trade, limit=2)
        == 2
    )
    assert len(_queued_ids(path)) == 3


@pytest.mark.parametrize(
    "reason",
    (
        "forecast_posterior_advanced",
        "day0_extreme_event_committed",
        "market_price_advanced",
        "money_path_substrate_refreshed",
        "position_fill_projected",
        reactor_wake.COLLATERAL_AUTHORITY_REFRESHED_WAKE_REASON,
        "a_reason_no_table_names",
    ),
)
def test_every_wake_that_can_invalidate_a_cut_advances_the_revision(tmp_path, reason):
    """One table: a wake the predicate could count against a cut always moves
    the urgent-marker revision, so a revision-keyed verdict cannot go stale."""

    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    before = reactor_wake.reactor_urgent_wake_revision(path=path)
    _publish(path, reason, (CURRENT,), at=NOW)
    assert reactor_wake.reactor_urgent_wake_revision(path=path) != before
    assert reactor_wake.reactor_urgent_wake_identity(path=path)[1] == reason


def test_only_a_request_leaves_the_revision_alone(tmp_path):
    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    _publish(path, reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON, (CURRENT,), at=NOW)
    assert reactor_wake.reactor_urgent_wake_revision(path=path) is None
    for reason in (*reactor_wake._WAKE_KIND_BY_REASON, "unknown"):
        assert reactor_wake.wake_advances_revision(
            SimpleNamespace(reason=reason, held_sell_reauction_requests=())
        ) is (reactor_wake.wake_kind(SimpleNamespace(reason=reason)) != "request")
