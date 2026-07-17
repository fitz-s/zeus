# Created: 2026-06-16
# Last reused/audited: 2026-07-17
# Authority basis: GOAL #83 continuous-fills limiter (docs/evidence/qkernel_rebuild/
#   fix_reactor_drain_budget_2026-06-16.md). The end-of-cycle substrate-refresh drain
#   (_drain_substrate_refreshes / _drain_one_bucket) refreshed EVERY blocked family with no
#   per-cycle bound; with ~49 blocked families that is ~49 /book network fetches per cycle, blowing
#   the reactor's 60s schedule and coalescing it into multi-minute gaps -> ~1 family decided/cycle.
#   These tests pin the BACKGROUND-I/O wall-clock budget (ZEUS_REACTOR_DRAIN_BUDGET_SECONDS) that
#   bounds the drain: it stops after the current family (never mid-network), retains the unreached
#   families for a later cycle, ALWAYS drains held-position families first (money at risk, never
#   budget-starved), and preserves fail-soft. This is NOT a money-path cap — decisions, the 30s
#   decision budget, the fair rotation, and every money-path gate are untouched.
"""Drain-budget tests for the #83 continuous-fills limiter.

The reactor's end-of-cycle substrate-refresh DRAIN is bounded by a per-cycle wall-clock budget so a
large blocked-family set can no longer overrun the 60s schedule. The budget is a background-I/O time
bound (identical in kind to the warm-cycle ZEUS_REACTOR_REFRESH_BUDGET_SECONDS), not a money-path
cap: held-position families are always drained first and the unreached tail is simply deferred to a
later cycle, where the fair-cursor rotation advances it toward the front (no starvation).
"""
from __future__ import annotations

import logging
import sqlite3

from src.events.event_store import EventStore
from src.events.reactor import (
    DEFAULT_REACTOR_DRAIN_BUDGET_SECONDS,
    OpportunityEventReactor,
    ReactorConfig,
    ReactorResult,
    _drain_budget_seconds,
)
from src.state.db import init_schema
from src.strategy.live_inference.no_trade_regret import NoTradeRegretLedger


def _store() -> tuple[sqlite3.Connection, EventStore]:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn, EventStore(conn)


def _reactor(store, *, refresher=None, held_family_provider=None, day0_hourly_refresher=None):
    return OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _dt: False,
        riskguard_gate=lambda _e: True,
        final_intent_submit=lambda _e, _dt: None,
        reject=lambda *_a: None,
        config=ReactorConfig(),
        regret_ledger=NoTradeRegretLedger(store.conn),
        family_snapshot_refresher=refresher,
        day0_hourly_refresher=day0_hourly_refresher,
        held_family_provider=held_family_provider,
    )


def test_runtime_schema_capability_skips_reactor_constructor_ddl(monkeypatch):
    conn, store = _store()

    def _unexpected_schema_ensure(*_args, **_kwargs):
        raise AssertionError("runtime reactor must not execute schema DDL")

    monkeypatch.setattr(
        "src.decision_kernel.ledger.DecisionCertificateLedger.ensure_schema",
        _unexpected_schema_ensure,
    )
    monkeypatch.setattr(
        "src.events.live_cap.ensure_table",
        _unexpected_schema_ensure,
    )

    OpportunityEventReactor(
        store,
        source_truth_gate=lambda _e: True,
        executable_snapshot_gate=lambda _e, _dt: False,
        riskguard_gate=lambda _e: True,
        final_intent_submit=lambda _e, _dt: None,
        reject=lambda *_a: None,
        world_schema_initialized=True,
    )
    conn.close()


# ---------------------------------------------------------------------------
# Budget reader (env contract — same shape as _cycle_budget_seconds)
# ---------------------------------------------------------------------------
def test_drain_budget_default_and_env_override(monkeypatch):
    monkeypatch.delenv("ZEUS_REACTOR_DRAIN_BUDGET_SECONDS", raising=False)
    assert _drain_budget_seconds() == DEFAULT_REACTOR_DRAIN_BUDGET_SECONDS == 10.0
    monkeypatch.setenv("ZEUS_REACTOR_DRAIN_BUDGET_SECONDS", "3.5")
    assert _drain_budget_seconds() == 3.5
    # 0 / negative disables the budget (unbounded legacy drain); malformed -> default.
    monkeypatch.setenv("ZEUS_REACTOR_DRAIN_BUDGET_SECONDS", "0")
    assert _drain_budget_seconds() is None
    monkeypatch.setenv("ZEUS_REACTOR_DRAIN_BUDGET_SECONDS", "-1")
    assert _drain_budget_seconds() is None
    monkeypatch.setenv("ZEUS_REACTOR_DRAIN_BUDGET_SECONDS", "not-a-number")
    assert _drain_budget_seconds() == DEFAULT_REACTOR_DRAIN_BUDGET_SECONDS


# ---------------------------------------------------------------------------
# THE BUDGET (the #83 fix): drain stops after the budget; the rest stay pending
# ---------------------------------------------------------------------------
def test_drain_stops_after_budget_rest_remain_pending_for_next_cycle(monkeypatch):
    """A SLOW refresher + a small budget: the drain refreshes only the families that fit the budget
    and STOPS after the current family (never mid-network); the unreached families REMAIN in
    _pending_snapshot_refreshes for the next cycle, and drained_truncated counts them."""
    conn, store = _store()
    monkeypatch.setenv("ZEUS_REACTOR_DRAIN_BUDGET_SECONDS", "10")

    # Synthetic monotonic clock advanced ONLY by the (slow) refresher: each refresh costs 4s of
    # virtual wall time. Budget 10s -> 1st family at t=0 (<10), 2nd at t=4 (<10), 3rd at t=8 (<10);
    # after the 3rd, t=12 >= 10 -> the 4th and 5th are deferred. (idx>n_held gate -> first non-held
    # always attempted, then truncate once the deadline passes.)
    clock = {"t": 0.0}
    monkeypatch.setattr("src.events.reactor.time.monotonic", lambda: clock["t"])

    drained: list[str] = []

    def _slow_refresher(*, city, target_date, metric, **_kw):
        drained.append(city)
        clock["t"] += 4.0  # virtual 4s per /book fetch
        return True

    reactor = _reactor(store, refresher=_slow_refresher)
    families = [
        ("A", "2026-06-17", "high"),
        ("B", "2026-06-17", "high"),
        ("C", "2026-06-17", "high"),
        ("D", "2026-06-17", "high"),
        ("E", "2026-06-17", "high"),
    ]
    reactor._pending_snapshot_refreshes = list(families)
    reactor._pending_cycle_advances = []
    reactor._family_refresh_cursor = 0  # deterministic: rotation starts at A

    res = ReactorResult()
    reactor._drain_substrate_refreshes(result=res)

    # Only the budget-fitting prefix drained; the snapshot counter matches.
    assert drained == ["A", "B", "C"]
    assert res.snapshot_refreshes == 3
    # The rest are DEFERRED, not dropped: retained in the pending bucket for the next cycle.
    assert res.drained_truncated == 2
    assert reactor._pending_snapshot_refreshes == [("D", "2026-06-17", "high"), ("E", "2026-06-17", "high")]


def test_unbounded_when_budget_disabled_drains_all(monkeypatch):
    """Budget disabled (0) -> legacy unbounded drain: EVERY family is refreshed, none deferred,
    even with a slow refresher (the no-drop-cap behavior is preserved when the budget is off)."""
    conn, store = _store()
    monkeypatch.setenv("ZEUS_REACTOR_DRAIN_BUDGET_SECONDS", "0")
    clock = {"t": 0.0}
    monkeypatch.setattr("src.events.reactor.time.monotonic", lambda: clock["t"])

    drained: list[str] = []

    def _slow_refresher(*, city, target_date, metric, **_kw):
        drained.append(city)
        clock["t"] += 100.0  # absurdly slow; with no budget it must still cover all
        return True

    reactor = _reactor(store, refresher=_slow_refresher)
    families = [("A", "2026-06-17", "high"), ("B", "2026-06-17", "high"), ("C", "2026-06-17", "high")]
    reactor._pending_snapshot_refreshes = list(families)
    reactor._pending_cycle_advances = []
    reactor._family_refresh_cursor = 0

    res = ReactorResult()
    reactor._drain_substrate_refreshes(result=res)
    assert set(drained) == {"A", "B", "C"}
    assert res.snapshot_refreshes == 3
    assert res.drained_truncated == 0
    assert reactor._pending_snapshot_refreshes == []  # full drain -> bucket cleared


def test_held_position_family_never_budget_starved(monkeypatch):
    """HELD-FIRST under budget pressure: even with the budget ALREADY spent before the drain
    starts, every held-position family (money at risk) is refreshed FIRST and is NEVER truncated;
    only the non-held rotation tail is deferred."""
    conn, store = _store()
    monkeypatch.setenv("ZEUS_REACTOR_DRAIN_BUDGET_SECONDS", "5")

    # Clock starts PAST the deadline immediately after the first refresh, so the budget is spent at
    # once. Held families must still all drain; non-held tail (beyond the first) must defer.
    clock = {"t": 0.0}
    monkeypatch.setattr("src.events.reactor.time.monotonic", lambda: clock["t"])

    order: list[str] = []

    def _refresher(*, city, target_date, metric, **_kw):
        order.append(city)
        clock["t"] += 100.0  # one refresh blows the 5s budget
        return True

    held = frozenset({("HELD1", "2026-06-17", "high"), ("HELD2", "2026-06-17", "high")})
    reactor = _reactor(store, refresher=_refresher, held_family_provider=lambda: held)
    reactor._pending_snapshot_refreshes = [
        ("N1", "2026-06-17", "high"),  # non-held
        ("HELD1", "2026-06-17", "high"),  # held
        ("N2", "2026-06-17", "high"),  # non-held
        ("HELD2", "2026-06-17", "high"),  # held
        ("N3", "2026-06-17", "high"),  # non-held
    ]
    reactor._pending_cycle_advances = []
    reactor._family_refresh_cursor = 0

    res = ReactorResult()
    reactor._drain_substrate_refreshes(result=res)

    # Both held families refreshed FIRST and in full (never starved), then exactly ONE non-held
    # (the first-non-held always-attempt guarantee). The remaining non-held tail is deferred.
    assert order[:2] == ["HELD1", "HELD2"]
    assert set(order) >= {"HELD1", "HELD2"}
    held_cities = {"HELD1", "HELD2"}
    assert held_cities.issubset(set(order))  # no held family ever truncated
    # The deferred remainder is non-held only.
    deferred = {c for c, _d, _m in reactor._pending_snapshot_refreshes}
    assert deferred.isdisjoint(held_cities)
    assert res.drained_truncated == len(reactor._pending_snapshot_refreshes)


def test_day0_hourly_drain_precedes_snapshot_under_shared_budget(monkeypatch):
    """Day0 carrier repair must not wait behind executable-snapshot I/O."""

    conn, store = _store()
    monkeypatch.setenv("ZEUS_REACTOR_DRAIN_BUDGET_SECONDS", "5")
    clock = {"t": 0.0}
    monkeypatch.setattr("src.events.reactor.time.monotonic", lambda: clock["t"])

    order: list[tuple[str, str]] = []

    def _snapshot_refresher(*, city, target_date, metric, **_kw):
        order.append(("snapshot", city))
        clock["t"] += 100.0
        return True

    def _day0_refresher(*, city, target_date, metric, **_kw):
        order.append(("day0-hourly", city))
        clock["t"] += 100.0
        return True

    reactor = _reactor(
        store,
        refresher=_snapshot_refresher,
        day0_hourly_refresher=_day0_refresher,
    )
    reactor._pending_snapshot_refreshes = [
        ("StaleBook", "2026-07-02", "high"),
        ("SecondStaleBook", "2026-07-02", "high"),
    ]
    reactor._pending_day0_hourly_refreshes = [("Hong Kong", "2026-07-02", "high")]
    reactor._pending_cycle_advances = []

    res = ReactorResult()
    reactor._drain_substrate_refreshes(result=res)

    assert order[0] == ("day0-hourly", "Hong Kong")
    assert res.day0_hourly_refreshes == 1
    assert res.snapshot_refreshes >= 1


def test_failsoft_preserved_under_budget_one_warning_no_raise(monkeypatch, caplog):
    """FAIL-SOFT preserved with the budget active: a refresher that RAISES logs ONE warning and the
    drain continues (never raises); a failed refresh still consumes its budget slot (the debounce
    mark is set before the call)."""
    conn, store = _store()
    monkeypatch.setenv("ZEUS_REACTOR_DRAIN_BUDGET_SECONDS", "100")  # generous: isolate fail-soft

    def _boom_refresher(*, city, target_date, metric, **_kw):
        raise RuntimeError("clob /book fetch boom")

    reactor = _reactor(store, refresher=_boom_refresher)
    reactor._pending_snapshot_refreshes = [("A", "2026-06-17", "high"), ("B", "2026-06-17", "high")]
    reactor._pending_cycle_advances = []

    res = ReactorResult()
    with caplog.at_level(logging.WARNING, logger="zeus.events.reactor"):
        reactor._drain_substrate_refreshes(result=res)  # must NOT raise

    assert res.snapshot_refreshes == 0  # both failed -> none counted
    refresh_warnings = [
        rec for rec in caplog.records
        if "always-decidable" in rec.getMessage() and "refresh failed" in rec.getMessage()
    ]
    assert len(refresh_warnings) == 2  # one per failed family, never raised
