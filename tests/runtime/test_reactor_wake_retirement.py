# Created: 2026-09-29
# Last reused or audited: 2026-10-06
# Authority basis: cut-cancel throughput task (2026-09-29): the wake backlog is
#   bounded by the one family-reachability law (src.data.forecast_retention),
#   and consumption, never by age. 2026-10-06: Day0 and HKO-print hints retire
#   under the same law, so the hint queue cannot grow without bound.
"""A queued hint no consumer can still serve is retired through the one ack path."""

from __future__ import annotations

import datetime as _dt
import json
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
        "CREATE TABLE position_events (position_id TEXT, event_type TEXT,"
        " sequence_no INTEGER, occurred_at TEXT)"
    )
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
    familyless = _publish(path, "forecast_posterior_advanced", (), at=at)

    assert reactor_wake.retire_served_wakes(now=NOW, path=path, trade_db=trade) == 2

    queued = _queued_ids(path)
    assert past_forecast.wake_id not in queued
    assert past_substrate.wake_id not in queued
    # A reachable family or an unscoped hint is never retired.
    assert {mixed.wake_id, familyless.wake_id} <= queued


def _world_db(path, *events):
    """(event_id, event_type, city, target_date, metric) rows, as the world DB holds them."""

    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE opportunity_events (event_id TEXT PRIMARY KEY, event_type TEXT,"
        " payload_json TEXT)"
    )
    conn.executemany(
        "INSERT INTO opportunity_events VALUES (?,?,?)",
        [
            (
                event_id,
                event_type,
                json.dumps({"city": city, "target_date": target_date, "metric": metric}),
            )
            for event_id, event_type, city, target_date, metric in events
        ],
    )
    conn.commit()
    conn.close()
    return path


def _retire(path, trade, world):
    return reactor_wake.retire_served_wakes(
        now=NOW, path=path, trade_db=trade, world_db=world
    )


def test_day0_wake_for_a_past_unreachable_family_is_retired(tmp_path, trade):
    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    world = _world_db(tmp_path / "world.db", ("e-past", "DAY0_EXTREME_UPDATED", *PAST))
    day0 = _publish(
        path,
        "day0_extreme_event_committed",
        (PAST,),
        at=NOW - _dt.timedelta(hours=130),
        event_ids=("e-past",),
    )

    assert _retire(path, trade, world) == 1
    assert day0.wake_id not in _queued_ids(path)


def test_day0_wake_names_its_families_through_events_not_only_its_declaration(
    tmp_path, trade
):
    """A declared past family cannot hide a current one a committed event names."""

    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    world = _world_db(tmp_path / "world.db", ("e-current", "DAY0_EXTREME_UPDATED", *CURRENT))
    day0 = _publish(
        path,
        "day0_extreme_event_committed",
        (PAST,),
        at=NOW - _dt.timedelta(hours=130),
        event_ids=("e-current",),
    )

    assert _retire(path, trade, world) == 0
    assert day0.wake_id in _queued_ids(path)


def test_day0_catchup_wake_without_declared_families_retires_by_its_events(
    tmp_path, trade
):
    """The reactor's catch-up producer publishes event ids and no families."""

    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    world = _world_db(
        tmp_path / "world.db",
        ("e-past", "DAY0_EXTREME_UPDATED", *PAST),
        ("e-current", "DAY0_EXTREME_UPDATED", *CURRENT),
    )
    at = NOW - _dt.timedelta(hours=130)
    past = _publish(
        path, "day0_extreme_event_committed", (), at=at, event_ids=("e-past",)
    )
    current = _publish(
        path, "day0_extreme_event_committed", (), at=at, event_ids=("e-current",)
    )

    assert _retire(path, trade, world) == 1
    queued = _queued_ids(path)
    assert past.wake_id not in queued
    assert current.wake_id in queued


@pytest.mark.parametrize("phase", ("active", "day0_window", "pending_exit"))
def test_day0_wake_for_a_held_family_is_kept(tmp_path, phase):
    trade = _trade_db(tmp_path / "trade.db", ("p1", phase, *HELD))
    world = _world_db(tmp_path / "world.db", ("e-held", "DAY0_EXTREME_UPDATED", *HELD))
    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    held = _publish(
        path,
        "day0_extreme_event_committed",
        (HELD,),
        at=NOW - _dt.timedelta(hours=130),
        event_ids=("e-held",),
    )

    assert _retire(path, trade, world) == 0
    assert held.wake_id in _queued_ids(path)


def test_day0_wake_for_an_open_entry_rest_is_kept(tmp_path, trade):
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
    world = _world_db(tmp_path / "world.db", ("e-rest", "DAY0_EXTREME_UPDATED", *PAST))
    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    rested = _publish(
        path,
        "day0_extreme_event_committed",
        (PAST,),
        at=NOW - _dt.timedelta(hours=130),
        event_ids=("e-rest",),
    )

    assert _retire(path, trade, world) == 0
    assert rested.wake_id in _queued_ids(path)


@pytest.mark.parametrize(
    "fault",
    (
        "unknown_event",
        "wrong_event_type",
        "malformed_payload",
        "no_event_ids",
        "no_world_db",
    ),
)
def test_day0_wake_whose_events_cannot_be_resolved_is_kept(tmp_path, trade, fault):
    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    world = _world_db(tmp_path / "world.db", ("e-typed", "MARKET_BOOK", *PAST))
    conn = sqlite3.connect(world)
    conn.execute(
        "INSERT INTO opportunity_events VALUES ('e-bad', 'DAY0_EXTREME_UPDATED', '{not json')"
    )
    conn.commit()
    conn.close()
    event_ids = {
        "unknown_event": ("e-unknown",),
        "wrong_event_type": ("e-typed",),
        "malformed_payload": ("e-bad",),
        "no_event_ids": (),
        "no_world_db": ("e-typed",),
    }[fault]
    world_db = tmp_path / "absent.db" if fault == "no_world_db" else world
    day0 = _publish(
        path,
        "day0_extreme_event_committed",
        (PAST,),
        at=NOW - _dt.timedelta(hours=130),
        event_ids=event_ids,
    )

    assert _retire(path, trade, world_db) == 0
    assert day0.wake_id in _queued_ids(path)


def test_a_cut_never_serves_a_reachable_day0_wake():
    """Consumption by a completed cut retires a forecast hint, never a Day0 fact."""

    wake = reactor_wake.ReactorWake(
        wake_id="w",
        published_at=(NOW - _dt.timedelta(hours=1)).isoformat(),
        source="t",
        reason="day0_extreme_event_committed",
        event_ids=("e",),
        forecast_families=(CURRENT,),
    )
    assert not reactor_wake.wake_is_served(
        wake,
        reachable=lambda _family: True,
        consumed={CURRENT: NOW},
        event_families=lambda _ids: frozenset({CURRENT}),
    )


def test_hko_print_wake_is_retired_for_a_past_day_and_kept_for_today(tmp_path, trade):
    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    hk_past = (("Hong Kong", "2026-09-26", "high"), ("Hong Kong", "2026-09-26", "low"))
    hk_today = (("Hong Kong", "2026-09-29", "high"), ("Hong Kong", "2026-09-29", "low"))
    at = NOW - _dt.timedelta(hours=100)
    past_print = _publish(path, "current_temperature_print_committed", hk_past, at=at)
    today_print = _publish(path, "current_temperature_print_committed", hk_today, at=at)
    straddle = _publish(
        path, "current_temperature_print_committed", (*hk_past, *hk_today[:1]), at=at
    )
    nameless = _publish(path, "current_temperature_print_committed", (), at=at)

    assert reactor_wake.retire_served_wakes(now=NOW, path=path, trade_db=trade) == 1

    queued = _queued_ids(path)
    assert past_print.wake_id not in queued
    assert {today_print.wake_id, straddle.wake_id, nameless.wake_id} <= queued


def test_hko_print_wake_for_a_held_past_day_is_kept(tmp_path):
    trade = _trade_db(
        tmp_path / "trade.db", ("p1", "active", "Hong Kong", "2026-09-26", "high")
    )
    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    held = _publish(
        path,
        "current_temperature_print_committed",
        (("Hong Kong", "2026-09-26", "high"), ("Hong Kong", "2026-09-26", "low")),
        at=NOW - _dt.timedelta(hours=100),
    )

    assert reactor_wake.retire_served_wakes(now=NOW, path=path, trade_db=trade) == 0
    assert held.wake_id in _queued_ids(path)


def test_unreadable_world_keeps_day0_wakes_but_still_retires_the_rest(tmp_path, trade):
    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    at = NOW - _dt.timedelta(hours=130)
    day0 = _publish(
        path, "day0_extreme_event_committed", (PAST,), at=at, event_ids=("e",)
    )
    forecast = _publish(path, "forecast_posterior_advanced", (PAST,), at=at)

    assert _retire(path, trade, tmp_path / "absent.db") == 1
    queued = _queued_ids(path)
    assert day0.wake_id in queued and forecast.wake_id not in queued


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


def test_a_wake_after_prepare_keeps_its_debt_when_the_cut_completes(tmp_path, trade):
    """A cut that ignored a non-winner family's newer fact cannot retire it.

    The cut decided HOLD/CASH on prepare-time q; its consumed-scope watermark
    is its scan instant, before the wake, so the wake's redecision debt stays
    queued for the next cut. A current print is never retired by consumption.
    """

    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    scan_at = NOW - _dt.timedelta(minutes=10)
    posterior = _publish(
        path, "forecast_posterior_advanced", (CURRENT,), at=scan_at + _dt.timedelta(seconds=5)
    )
    printed = _publish(
        path, "current_temperature_print_committed", (CURRENT,), at=scan_at + _dt.timedelta(seconds=6)
    )
    reactor_wake.record_consumed_scope((CURRENT,), consumed_at=scan_at)

    assert reactor_wake.retire_served_wakes(now=NOW, path=path, trade_db=trade) == 0
    assert {posterior.wake_id, printed.wake_id} <= _queued_ids(path)

    reactor_wake.record_consumed_scope(
        (CURRENT,), consumed_at=scan_at + _dt.timedelta(seconds=10)
    )
    assert reactor_wake.retire_served_wakes(now=NOW, path=path, trade_db=trade) == 1
    assert printed.wake_id in _queued_ids(path)


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


@pytest.fixture
def poll(monkeypatch, tmp_path):
    """The real Day0 poll over a real tmp queue; only monitors and cuts are stubbed."""

    import src.main as main

    monkeypatch.setattr("src.config.state_path", lambda name: tmp_path / name)
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_paused_forecast_carrier_priority_allowed", lambda **_k: False)
    monkeypatch.setattr(main, "_edli_event_reactor_cycle", lambda **_k: True)
    monkeypatch.setattr(main, "_day0_wake_target_families", lambda _ids: frozenset({HELD}))
    monkeypatch.setattr(main, "_day0_wake_requires_exit_monitor", lambda _fams: True)
    monkeypatch.setattr(
        main,
        "_dispatch_day0_exit_monitor",
        lambda *_args: pytest.fail("the monitor outcome is seeded, never dispatched"),
    )
    monkeypatch.setattr(
        main,
        "_reactor_wake_event_state",
        lambda ids: main._ReactorWakeEventState(
            ready=ids != ("e-served",),
            finished=ids == ("e-served",),
            terminal=ids == ("e-served",),
        ),
    )
    main._edli_initialize_reactor_wake_cursor()
    main._day0_exit_monitor_attempts.clear()
    yield main
    main._edli_initialize_reactor_wake_cursor()
    main._day0_exit_monitor_attempts.clear()


def _strict_marker(path, at):
    return reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=path,
        published_at=at,
        forecast_families=(HELD,),
    )


def test_day0_wake_whose_drain_completed_is_acknowledged_beside_a_strict_marker(
    tmp_path, poll
):
    """The held monitor republishes strict family markers continuously. A Day0
    wake whose monitor succeeded and whose events finished used to yield to
    them and return unacknowledged on every selection, forever."""

    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    day0 = _publish(
        path, "day0_extreme_event_committed", (HELD,), at=NOW, event_ids=("e-served",)
    )
    marker = _strict_marker(path, NOW + _dt.timedelta(seconds=1))
    poll._day0_exit_monitor_attempts[day0.wake_id] = True

    assert poll._edli_reactor_wake_poll_once() is True

    queued = _queued_ids(path)
    assert day0.wake_id not in queued
    assert marker.wake_id in queued
    # The one-turn baton still lets the strict marker run next.
    assert poll._edli_family_completion_post_monitor_yield.wake_ids == {day0.wake_id}
    assert poll._edli_reactor_wake_poll_once() is True
    assert marker.wake_id not in _queued_ids(path)


@pytest.mark.parametrize("fault", ("monitor_failed", "events_unfinished"))
def test_day0_wake_whose_drain_failed_or_is_unfinished_stays_queued(
    tmp_path, poll, fault
):
    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    event = "e-served" if fault == "monitor_failed" else "e-pending"
    day0 = _publish(
        path, "day0_extreme_event_committed", (HELD,), at=NOW, event_ids=(event,)
    )
    _strict_marker(path, NOW + _dt.timedelta(seconds=1))
    poll._day0_exit_monitor_attempts[day0.wake_id] = fault != "monitor_failed"

    # A failed attempt yields its one turn to the strict marker (True); an
    # unfinished event holds the Day0 wake itself (False). Neither acks it.
    poll._edli_reactor_wake_poll_once()
    assert day0.wake_id in _queued_ids(path)


def test_served_day0_hints_no_longer_monopolise_selection(tmp_path, trade, poll):
    """Old Day0 and HKO hints coexist with a new forecast wake and a held family.

    The retirement pass drops the unreachable hints, the poll acknowledges the
    served reachable one, and read_reactor_wake then returns the forecast wake.
    The held Day0 hint whose events are still unfinished stays queued.
    """

    held_trade = _trade_db(tmp_path / "held.db", ("p1", "active", *HELD))
    world = _world_db(
        tmp_path / "world.db",
        ("e-past", "DAY0_EXTREME_UPDATED", *PAST),
        ("e-served", "DAY0_EXTREME_UPDATED", *HELD),
        ("e-pending", "DAY0_EXTREME_UPDATED", *HELD),
    )
    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    old = NOW - _dt.timedelta(hours=130)
    past_day0 = _publish(
        path, "day0_extreme_event_committed", (PAST,), at=old, event_ids=("e-past",)
    )
    past_hko = _publish(
        path,
        "current_temperature_print_committed",
        (("Hong Kong", "2026-09-26", "high"),),
        at=old,
    )
    unresolved = _publish(
        path,
        "day0_extreme_event_committed",
        (HELD,),
        at=NOW - _dt.timedelta(hours=3),
        event_ids=("e-pending",),
    )
    served = _publish(
        path,
        "day0_extreme_event_committed",
        (HELD,),
        at=NOW - _dt.timedelta(hours=2),
        event_ids=("e-served",),
    )
    forecast = _publish(
        path, "forecast_posterior_advanced", (CURRENT,), at=NOW - _dt.timedelta(hours=1)
    )
    _strict_marker(path, NOW - _dt.timedelta(minutes=30))
    poll._day0_exit_monitor_attempts[served.wake_id] = True
    # The unresolved held hint's monitor attempt is still in flight, so the
    # existing fairness exclusion keeps it out of selection until it finishes.
    poll._day0_exit_monitor_attempts[unresolved.wake_id] = None

    # Before: the newest Day0 hint is selected ahead of the forecast wake.
    assert reactor_wake.read_reactor_wake(path=path).wake_id == served.wake_id

    assert (
        reactor_wake.retire_served_wakes(
            now=NOW, path=path, trade_db=held_trade, world_db=world
        )
        == 2
    )
    assert {past_day0.wake_id, past_hko.wake_id}.isdisjoint(_queued_ids(path))
    assert poll._edli_reactor_wake_poll_once() is True  # served Day0 acked
    assert poll._edli_reactor_wake_poll_once() is True  # strict marker's baton turn

    queued = _queued_ids(path)
    assert served.wake_id not in queued
    assert {unresolved.wake_id, forecast.wake_id} <= queued
    excluded = poll._exit_monitor_excluded_wake_ids()
    assert unresolved.wake_id in excluded
    assert (
        reactor_wake.read_reactor_wake(path=path, exclude_wake_ids=excluded).wake_id
        == forecast.wake_id
    )


_CUT_DEPENDENCY = reactor_wake.CutDependency(
    published=True, hard_family_keys=None, belief_family_keys=None
)


@pytest.mark.parametrize(
    "reason",
    (
        "forecast_posterior_advanced",
        "day0_extreme_event_committed",
        "market_price_advanced",
        "money_path_substrate_refreshed",
        "position_fill_projected",
        reactor_wake.COLLATERAL_AUTHORITY_REFRESHED_WAKE_REASON,
        reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        "a_reason_no_table_names",
    ),
)
def test_every_wake_that_can_invalidate_an_auction_cut_advances_the_revision(
    tmp_path, reason
):
    """One table: a wake the predicate can count against an auction cut (which
    rebinds books and wealth) moves the urgent-marker revision, so a
    revision-keyed verdict cannot go stale. Of the rest, only a price quote
    (which must reach a paused carrier promptly) moves it, as on base."""

    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    wake = _publish(path, reason, (CURRENT,), at=NOW)
    verdict = reactor_wake.cut_invalidating_wakes((wake,), _CUT_DEPENDENCY)
    can_invalidate = bool(verdict.hard or verdict.epoch)
    advanced = reactor_wake.reactor_urgent_wake_revision(path=path) is not None
    assert advanced is (can_invalidate or reason == "market_price_advanced")
    assert can_invalidate is (
        reason
        not in {
            "market_price_advanced",
            "money_path_substrate_refreshed",
            reactor_wake.COLLATERAL_AUTHORITY_REFRESHED_WAKE_REASON,
            reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        }
    )


def test_retirement_runs_at_most_once_per_interval(monkeypatch):
    """The listener polls on every notification; the reachability read and
    queue scan run once per interval."""

    import src.data.forecast_retention as fr

    calls = []
    monkeypatch.setattr(
        fr, "build_reachability", lambda **kwargs: calls.append(kwargs) or fr.Reachability("9999", frozenset())
    )
    monkeypatch.setattr(reactor_wake, "_queued_wakes", lambda *_a, **_k: [])
    monkeypatch.setattr(reactor_wake, "_RETIRE_LAST_RUN_MONOTONIC", [None])
    clock = [1000.0]

    def poll():
        return reactor_wake.retire_served_wakes_if_due(monotonic=lambda: clock[0])

    poll()
    clock[0] += 1.0
    poll()
    assert len(calls) == 1
    clock[0] += reactor_wake.RETIRE_SERVED_WAKES_INTERVAL_S
    poll()
    assert len(calls) == 2


# --- finished Day0 hints: events terminal for the reactor + held capital monitored ---


def _book_db(path, *positions):
    """A trade DB whose venue_commands carries ``side``, so an entry-rest probe is exact."""

    _trade_db(path, *positions)
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE venue_commands ADD COLUMN side TEXT")
    conn.commit()
    conn.close()
    return path


def _processing(world, *rows):
    """(event_id, status) rows for edli_reactor_v1 in the real processing schema."""

    from src.state.schema.opportunity_event_processing_schema import CREATE_TABLE_SQL

    conn = sqlite3.connect(world)
    conn.execute(CREATE_TABLE_SQL)
    conn.executemany(
        "INSERT INTO opportunity_event_processing"
        " (consumer_name, event_id, processing_status, updated_at)"
        " VALUES ('edli_reactor_v1', ?, ?, '2026-09-29T00:00:00+00:00')",
        rows,
    )
    conn.commit()
    conn.close()


def _monitored(trade, position_id, at):
    conn = sqlite3.connect(trade)
    conn.execute(
        "INSERT INTO position_events SELECT ?, 'MONITOR_REFRESHED',"
        " COALESCE(MAX(sequence_no), 0) + 1, ? FROM position_events WHERE position_id = ?",
        (position_id, at.isoformat(), position_id),
    )
    conn.commit()
    conn.close()


def _finished_day0(tmp_path, *, count, family=CURRENT, status="expired"):
    """``count`` Day0 hints (one event each, all ``status``) plus a forecast wake."""

    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    ids = [f"e-{index}" for index in range(count)]
    world = _world_db(
        tmp_path / "world.db",
        *((event_id, "DAY0_EXTREME_UPDATED", *family) for event_id in ids),
    )
    _processing(world, *((event_id, status) for event_id in ids))
    hints = [
        _publish(
            path,
            "day0_extreme_event_committed",
            (family,),
            at=NOW - _dt.timedelta(hours=3, seconds=index),
            event_ids=(event_id,),
        )
        for index, event_id in enumerate(ids)
    ]
    forecast = _publish(
        path,
        "forecast_posterior_advanced",
        (family,),
        at=NOW - _dt.timedelta(hours=1),
    )
    return path, world, hints, forecast


@pytest.fixture
def book(tmp_path):
    return _book_db(tmp_path / "book.db")


class _Poll:
    """The real Day0 poll over real tmp DBs; only monitor dispatch and the cut are stubbed."""

    def __init__(self, main, monkeypatch):
        import src.state.db as state_db

        self.main = main
        self.dispatched: list[str] = []
        self._monkeypatch = monkeypatch
        self._state_db = state_db

    def bind(self, trade, world):
        self._monkeypatch.setattr(
            self.main,
            "get_world_connection_read_only",
            lambda: sqlite3.connect(f"file:{world}?mode=ro", uri=True),
        )
        self._monkeypatch.setattr(
            self._state_db,
            "get_trade_connection_read_only",
            lambda: sqlite3.connect(f"file:{trade}?mode=ro", uri=True),
        )

    def once(self):
        return self.main._edli_reactor_wake_poll_once()


@pytest.fixture
def real_poll(monkeypatch, tmp_path):
    import src.main as main

    def no_risk_read():
        raise RuntimeError("test: RiskGuard is not under test")

    monkeypatch.setattr("src.config.state_path", lambda name: tmp_path / name)
    monkeypatch.setattr("src.riskguard.riskguard.get_current_level", no_risk_read)
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_paused_forecast_carrier_priority_allowed", lambda **_k: False)
    monkeypatch.setattr(main, "_edli_event_reactor_cycle", lambda **_k: True)
    harness = _Poll(main, monkeypatch)
    monkeypatch.setattr(
        main,
        "_dispatch_day0_exit_monitor",
        lambda wake_id, _families: harness.dispatched.append(wake_id) or True,
    )
    main._edli_initialize_reactor_wake_cursor()
    main._day0_exit_monitor_attempts.clear()
    yield harness
    main._edli_initialize_reactor_wake_cursor()
    main._day0_exit_monitor_attempts.clear()


def test_finished_day0_hints_no_longer_outrank_a_forecast_wake(tmp_path, book, real_poll):
    """N finished Day0 hints (their family unheld) plus one forecast wake: the
    retire pass drains its share, the take path acknowledges the rest one per
    selection, and the forecast wake is then what read_reactor_wake returns."""

    path, world, _hints, forecast = _finished_day0(tmp_path, count=5)
    real_poll.bind(book, world)
    assert reactor_wake.read_reactor_wake(path=path).reason == "day0_extreme_event_committed"

    assert (
        reactor_wake.retire_served_wakes(
            now=NOW, path=path, trade_db=book, world_db=world, limit=2
        )
        == 2
    )
    assert len(_queued_ids(path)) == 4  # 3 hints + the forecast wake

    for _ in range(3):
        assert real_poll.once() is True
    assert _queued_ids(path) == {forecast.wake_id}
    assert reactor_wake.read_reactor_wake(path=path).wake_id == forecast.wake_id
    assert real_poll.dispatched == []  # no monitor was owed, none ran


@pytest.mark.parametrize("status", ("pending", "processing"))
def test_day0_hint_with_an_unfinished_event_stays(tmp_path, book, real_poll, status):
    path, world, hints, _forecast = _finished_day0(tmp_path, count=2)
    conn = sqlite3.connect(world)
    conn.execute(
        "UPDATE opportunity_event_processing SET processing_status = ? WHERE event_id = 'e-0'",
        (status,),
    )
    conn.commit()
    conn.close()
    real_poll.bind(book, world)

    assert (
        reactor_wake.retire_served_wakes(
            now=NOW, path=path, trade_db=book, world_db=world
        )
        == 1
    )
    assert hints[0].wake_id in _queued_ids(path)
    assert hints[1].wake_id not in _queued_ids(path)

    real_poll.once()  # selects the unfinished hint; no ack path may take it
    assert hints[0].wake_id in _queued_ids(path)


def test_finished_day0_hint_for_a_held_family_needs_post_publish_monitor_proof(
    tmp_path, real_poll
):
    trade = _book_db(tmp_path / "held.db", ("p1", "day0_window", *CURRENT))
    path, world, (hint,), _forecast = _finished_day0(tmp_path, count=1)
    published = NOW - _dt.timedelta(hours=3)
    real_poll.bind(trade, world)

    def retire():
        return reactor_wake.retire_served_wakes(
            now=NOW, path=path, trade_db=trade, world_db=world
        )

    assert retire() == 0  # no MONITOR_REFRESHED at all: held capital may be owed this fact
    _monitored(trade, "p1", published - _dt.timedelta(seconds=1))
    assert retire() == 0  # one before the publish time covers nothing
    real_poll.once()  # the take path agrees: it routes to the monitor and never acks
    assert hint.wake_id in _queued_ids(path)
    assert real_poll.dispatched == [hint.wake_id]

    _monitored(trade, "p1", published)  # at the publish time is the proof
    assert retire() == 1
    assert hint.wake_id not in _queued_ids(path)


def test_finished_day0_hint_is_acked_at_take_when_monitor_proof_exists(tmp_path, real_poll):
    """The take path itself acknowledges a covered hint instead of rescuing."""

    trade = _book_db(tmp_path / "held.db", ("p1", "day0_window", *CURRENT))
    path, world, _hints, forecast = _finished_day0(tmp_path, count=1)
    _monitored(trade, "p1", NOW - _dt.timedelta(minutes=5))
    real_poll.bind(trade, world)

    assert real_poll.once() is True
    assert _queued_ids(path) == {forecast.wake_id}
    assert real_poll.dispatched == []


def test_an_open_entry_rest_blocks_coverage_only_for_its_own_family(tmp_path):
    """A resting ENTRY order leaves no monitor event, so it proves nothing."""

    other = ("Austin", "2026-09-30", "high")
    trade = _book_db(
        tmp_path / "rest.db", ("p9", "settled", *CURRENT), ("p8", "settled", *other)
    )
    conn = sqlite3.connect(trade)
    conn.execute(
        "INSERT INTO venue_commands VALUES ('c1', 'p9', 'o1', 'tok', 'snap', 'ACKED', 'ENTRY', 'BUY')"
    )
    conn.commit()
    conn.close()
    book = reactor_wake.HeldMonitorBook(sqlite3.connect(trade))

    assert not book.covered((CURRENT,), since=NOW)
    assert book.covered((other,), since=NOW)


def test_missing_processing_rows_count_as_terminal(tmp_path, book):
    """A producer crash leaves ids with no canonical row; the hint is finished."""

    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    world = _world_db(tmp_path / "world.db", ("e-gone", "DAY0_EXTREME_UPDATED", *CURRENT))
    _processing(world)  # the table exists; no row for any event
    at = NOW - _dt.timedelta(hours=3)
    gone = _publish(
        path, "day0_extreme_event_committed", (CURRENT,), at=at, event_ids=("e-gone",)
    )
    # Events no committed row resolves and no processing row names: the declared family speaks.
    nameless = _publish(
        path, "day0_extreme_event_committed", (CURRENT,), at=at, event_ids=("e-unknown",)
    )

    assert (
        reactor_wake.retire_served_wakes(
            now=NOW, path=path, trade_db=book, world_db=world
        )
        == 2
    )
    assert not ({gone.wake_id, nameless.wake_id} & _queued_ids(path))


def test_unresolvable_events_with_a_real_terminal_row_stay(tmp_path, book):
    path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    world = _world_db(tmp_path / "world.db")  # no committed event resolves
    _processing(world, ("e-seen", "processed"))
    hint = _publish(
        path,
        "day0_extreme_event_committed",
        (CURRENT,),
        at=NOW - _dt.timedelta(hours=3),
        event_ids=("e-seen", "e-unknown"),
    )

    assert (
        reactor_wake.retire_served_wakes(
            now=NOW, path=path, trade_db=book, world_db=world
        )
        == 0
    )
    assert hint.wake_id in _queued_ids(path)


def test_a_hint_carrying_a_held_sell_request_is_never_finished_here():
    wake = reactor_wake.ReactorWake(
        wake_id="w",
        published_at=NOW.isoformat(),
        source="t",
        reason="day0_extreme_event_committed",
        event_ids=("e",),
        forecast_families=(CURRENT,),
        held_sell_reauction_requests=(object(),),
    )
    assert not reactor_wake.day0_hint_finished(
        wake,
        all_terminal=True,
        all_missing=False,
        resolved=frozenset({CURRENT}),
        held=None,
    )


def test_event_state_query_drives_the_composite_key_not_a_status_scan(tmp_path):
    """EXPLAIN QUERY PLAN on the real schema: one primary-key seek per event id,
    never a SCAN of opportunity_event_processing (a plain IN timed out live)."""

    from src.state.schema import opportunity_event_processing_schema as schema

    conn = sqlite3.connect(tmp_path / "plan.db")
    conn.execute(schema.CREATE_TABLE_SQL)
    for index_sql in (
        schema.CREATE_STATUS_INDEX_SQL,
        schema.CREATE_PENDING_RETRY_FLOOR_INDEX_SQL,
        schema.CREATE_STALE_CLAIM_INDEX_SQL,
    ):
        conn.execute(index_sql)
    conn.executemany(
        "INSERT INTO opportunity_event_processing"
        " (consumer_name, event_id, processing_status, updated_at)"
        " VALUES ('edli_reactor_v1', ?, ?, '2026-09-29T00:00:00+00:00')",
        [(f"e-{n}", "expired" if n % 4 else "processed") for n in range(4000)],
    )
    conn.execute("ANALYZE")
    executed: list[str] = []
    conn.set_trace_callback(executed.append)
    rows = reactor_wake.event_processing_rows(conn, ("e-1", "e-2", "e-missing", "e-1"))
    conn.set_trace_callback(None)

    assert sorted(rows) == [
        ("e-1", "expired", None),
        ("e-2", "expired", None),
        ("e-missing", None, None),
    ]
    assert reactor_wake.event_rows_state(rows) == (True, False)
    select = next(sql for sql in executed if "requested" in sql)
    details = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + select)]
    assert not any(d.startswith("SCAN opportunity_event_processing") for d in details)
    assert any(
        "SEARCH p USING INDEX sqlite_autoindex_opportunity_event_processing_1"
        " (consumer_name=? AND event_id=?)" in d
        for d in details
    )
