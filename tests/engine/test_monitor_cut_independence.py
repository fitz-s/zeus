# Created: 2026-09-30
# Last reused or audited: 2026-09-30
# Authority basis: cut-cancel throughput task (2026-09-30): live 03:34-04:05Z
#   lost 11 of 36 incomplete global cuts to ``monitor_handoff``; the monitor
#   only acquired-and-released the reactor lock (src/main.py _exit_monitor_cycle)
#   and needs nothing an in-flight cut holds. A handoff is not a fact change.
"""The held-position monitor and the global auction cut never cancel each other.

The monitor protects held capital and must run on its cadence; the cut is a
replayable 10-45 s read-heavy comparison. Neither needs the other's state:
SQLite writes are serialized by the write coordinator, and a cut is ended only
by the facts ``cut_invalidating_wakes`` names (plus exact held-SELL debt,
capital recovery, and its deadline). These tests run the real scheduler hook
and the real reactor lock on real threads.
"""

from __future__ import annotations

import ast
import threading
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _scope_event():
    from dataclasses import asdict

    from src.events.opportunity_event import (
        ForecastSnapshotReadyPayload,
        make_opportunity_event,
    )

    at = "2026-07-10T08:00:00+00:00"
    payload = ForecastSnapshotReadyPayload(
        city="Alpha",
        target_date="2026-07-11",
        metric="high",
        source_id="replacement_0_1",
        source_run_id="run-alpha",
        cycle="2026-07-10T00:00:00+00:00",
        track="replacement_0_1_openmeteo_bayes_fusion",
        snapshot_id="rmf-Alpha|2026-07-11|high|2026-07-10",
        snapshot_hash="run-alpha",
        captured_at=at,
        available_at=at,
        required_fields_present=True,
        required_steps_present=True,
        member_count=3,
        min_members_floor=3,
        completeness_status="COMPLETE",
        required_steps=[],
        observed_steps=[],
        expected_members=3,
        source_run_status="COMPLETE",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )
    body = asdict(payload)
    body["city_timezone"] = "UTC"
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Alpha|2026-07-11|high",
        source="global-auction-current-scope",
        observed_at=at,
        available_at=at,
        received_at=at,
        payload=body,
        causal_snapshot_id=payload.snapshot_id,
    )


@pytest.fixture
def monitor_env(monkeypatch):
    import src.main as main
    from src.execution import exit_lifecycle

    runs: list[float] = []

    def run_core(**kwargs):
        runs.append(time.monotonic())
        kwargs["mark_held_position_monitor_complete"]()
        return True

    monkeypatch.setattr(exit_lifecycle, "run_exit_monitor_cycle", run_core)
    monkeypatch.setattr(main, "_current_periodic_monitor_obligation_count", lambda: 1)
    monkeypatch.setattr(main, "_day0_exit_monitor_priority_pending", lambda: False)
    monkeypatch.setattr(main, "_held_position_monitor_recovery_evidence", lambda: {})
    monkeypatch.setattr(
        main, "_held_position_monitor_recovery_counts", lambda _e: (0, 0, {})
    )
    monkeypatch.setattr(main, "_held_position_monitor_claim", threading.Lock())
    monkeypatch.setattr(main, "_held_position_monitor_active", threading.Event())
    monkeypatch.setattr(main, "_held_position_monitor_canonical_debt", threading.Event())
    main._periodic_exit_monitor_urgent_yielded.clear()
    return main, runs


def test_monitor_runs_within_cadence_while_a_cut_holds_the_reactor(monitor_env):
    """Capital monitoring is never starved by an in-flight cut.

    On base the monitor waited up to 29 s for the reactor lock and, on timeout,
    deferred with fairness debt: exit latency was bounded by the cut, not the
    cadence. Now it enters its core run immediately.
    """

    main, runs = monitor_env
    reactor_lock = main._edli_reactor_active_lock
    cut_started = threading.Event()
    release_cut = threading.Event()

    def cut():
        with reactor_lock:
            cut_started.set()
            release_cut.wait(30.0)

    holder = threading.Thread(target=cut, daemon=True)
    holder.start()
    try:
        assert cut_started.wait(2.0)
        started = time.monotonic()
        assert main._exit_monitor_cycle() is True
        latency = time.monotonic() - started
        assert len(runs) == 1
        # Well inside one 30 s recovery interval, while the cut still holds.
        assert latency < 1.0
        assert reactor_lock.locked()
    finally:
        release_cut.set()
        holder.join(5.0)


def test_in_flight_cut_sees_no_cancel_signal_from_a_claiming_monitor(
    monitor_env, monkeypatch
):
    """A monitor claim mid-cut no longer discards the cut's prepared state.

    Runs the real ``main._edli_event_reactor_cycle`` wiring. The stubbed cycle
    body plays the cut: it holds the real reactor lock (as scope_scan /
    book_epoch do), lets a periodic monitor claim on another thread, then
    evaluates every cancellation callback main handed it. On base the
    monitor's handoff event made ``held_position_monitor_pending`` true, which
    the cut's probes turned into ``source=monitor_handoff`` (11 of 36
    incomplete cuts, 03:34-04:05Z 09-30).
    """

    import src.events.reactor as reactor_module

    main, runs = monitor_env
    monkeypatch.setattr(main, "_start_edli_reactor_wake_listener", lambda: None)
    monkeypatch.setattr(main, "_edli_live_entry_readiness_block", lambda _c: (None, {}))
    monkeypatch.setattr(main, "_held_position_monitor_entry_block_reason", lambda: None)
    monkeypatch.setattr(main, "_consume_live_control_commands", lambda: None)
    monkeypatch.setattr(
        "src.control.control_plane.recover_deploy_live_restart_guard",
        lambda: {"status": "noop"},
    )
    main._held_position_monitor_bootstrap_complete.set()
    observed: dict[str, object] = {}
    cut_kwargs: dict[str, object] = {}
    from src.execution import exit_lifecycle

    def monitor_core(**kwargs):
        # Sample the cut's cancel callbacks while the monitor owns its claim.
        runs.append(time.monotonic())
        observed["signals"] = {
            name: bool(value())
            for name, value in cut_kwargs.items()
            if callable(value) and name.endswith("_pending")
        }
        observed["admission_deferred"] = main._defer_for_held_position_monitor(
            "edli_event_reactor"
        )
        kwargs["mark_held_position_monitor_complete"]()
        return True

    monkeypatch.setattr(exit_lifecycle, "run_exit_monitor_cycle", monitor_core)

    def cut(**kwargs):
        cut_kwargs.update(kwargs)
        lock = kwargs["active_lock"]
        assert lock.acquire(blocking=False)
        try:
            monitor = threading.Thread(target=main._exit_monitor_cycle, daemon=True)
            monitor.start()
            monitor.join(3.0)
            observed["monitor_finished"] = not monitor.is_alive()
        finally:
            lock.release()
        return True

    monkeypatch.setattr(reactor_module, "run_edli_event_reactor_cycle", cut)
    assert main._edli_event_reactor_cycle() is True
    assert observed["monitor_finished"] is True
    assert runs, "the monitor ran while the cut held the reactor"
    assert observed["signals"] and not any(observed["signals"].values()), observed
    assert observed["admission_deferred"] is False


def test_reactor_admission_is_not_deferred_by_an_active_monitor(monitor_env):
    main, _runs = monitor_env
    main._held_position_monitor_bootstrap_complete.set()
    assert main._held_position_monitor_claim.acquire(blocking=False)
    main._held_position_monitor_active.set()
    try:
        assert main._defer_for_held_position_monitor("edli_event_reactor") is False
    finally:
        main._held_position_monitor_active.clear()
        main._held_position_monitor_claim.release()


def test_exact_held_sell_debt_still_preempts_an_ordinary_cut():
    """The one capital signal that must end a cut still does."""

    from src.events import reactor

    reactor._EXACT_EXECUTABLE_HELD_SELL_PENDING.clear()
    try:
        assert reactor._exact_held_sell_preempts(exact_turn=False) is False
        reactor._EXACT_EXECUTABLE_HELD_SELL_PENDING.set()
        assert reactor._exact_held_sell_preempts(exact_turn=False) is True
        assert reactor._exact_held_sell_preempts(exact_turn=True) is False
    finally:
        reactor._EXACT_EXECUTABLE_HELD_SELL_PENDING.clear()


_CUT_CANCEL_LABELS = frozenset(
    {"urgent_wake", "generic_completion_latch", "exact_held_sell_pending"}
)


def _labels(source: str) -> set[str]:
    labels: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "_first_cancel_label"
        ):
            for arg in node.args:
                if (
                    isinstance(arg, ast.Tuple)
                    and arg.elts
                    and isinstance(arg.elts[0], ast.Constant)
                ):
                    labels.add(arg.elts[0].value)
    return labels


def test_cut_cancel_sources_are_facts_never_monitor_schedule():
    """Structural antibody: no cut-cancel leaf may name the monitor.

    Every labelled leaf that can cancel a reactor cut is enumerated from source.
    A new leaf must be a fact (judged by ``cut_invalidating_wakes``), exact
    held-SELL debt, or a bounded completion latch -- never monitor scheduling.
    The monitor side is pinned too: its claim path never touches the reactor
    lock, so a cut can never make it wait.
    """

    reactor_src = (_ROOT / "src/events/reactor.py").read_text()
    labels = _labels(reactor_src)
    assert labels == _CUT_CANCEL_LABELS
    assert not any("monitor" in label for label in labels)

    main_tree = ast.parse((_ROOT / "src/main.py").read_text())
    exit_cycle = next(
        node
        for node in main_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_exit_monitor_cycle"
    )
    assert "_edli_reactor_active_lock" not in ast.unparse(exit_cycle)
