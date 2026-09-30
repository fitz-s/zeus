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


# ---------------------------------------------------------------------------
# Capital safety with a concurrent monitor and cut (review F1/F2).
# ---------------------------------------------------------------------------


def _held_trade_db(path):
    """A canonical trade DB with one held family and current CHAIN collateral."""

    import datetime as _dt
    import json

    from src.engine.lifecycle_events import build_position_current_projection
    from src.state.collateral_ledger import init_collateral_schema
    from src.state.db import get_connection, init_schema, init_schema_trade_only
    from src.state.portfolio import Position
    from src.state.projection import upsert_position_current

    now = _dt.datetime.now(_dt.timezone.utc)
    conn = get_connection(path)
    init_schema(conn)
    init_schema_trade_only(conn)
    init_collateral_schema(conn)
    position = Position(
        trade_id="held-1",
        market_id="m1",
        city="NYC",
        cluster="NYC",
        target_date="2026-10-01",
        bin_label="39-40°F",
        direction="buy_yes",
        env="live",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.60,
        edge=0.20,
        shares=25.0,
        cost_basis_usd=10.0,
        entered_at=now.isoformat(),
        token_id="yes-1",
        no_token_id="no-1",
        state="entered",
        edge_source="center_buy",
        strategy="center_buy",
        strategy_key="center_buy",
        condition_id="cond-1",
        decision_snapshot_id="snap-1",
        chain_state="synced",
        chain_shares=25.0,
        chain_avg_price=0.40,
        chain_cost_basis_usd=10.0,
        chain_verified_at=now.isoformat(),
    )
    upsert_position_current(conn, build_position_current_projection(position))
    conn.execute(
        "INSERT INTO collateral_ledger_snapshots ("
        "pusd_balance_micro,pusd_allowance_micro,usdc_e_legacy_balance_micro,"
        "ctf_token_balances_json,ctf_token_allowances_json,"
        "reserved_pusd_for_buys_micro,reserved_tokens_for_sells_json,"
        "captured_at,authority_tier,raw_balance_payload_hash"
        ") VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            100_000_000,
            1_000_000_000,
            0,
            json.dumps({"yes-1": 25_000_000}),
            json.dumps({}),
            0,
            json.dumps({}),
            now.isoformat(),
            "CHAIN",
            "held-wallet",
        ),
    )
    conn.commit()
    return conn, now


def test_monitor_exit_mid_cut_refuses_the_cut_buy_at_actuation(tmp_path):
    """F1: an exit committed while a cut is in flight vetoes the cut's BUY.

    With no monitor cancel, a cut sealed on the pre-exit portfolio can reach
    actuation after the monitor moved a held position to ``pending_exit``. The
    submit-time guard in ``_actuate_preflighted`` rebuilds current wealth
    (``_global_actuation_current_wealth_block_reason``); the witness's
    ``position_set_hash`` binds every held position's lifecycle state, so the
    exit supersedes the sealed economic identity and the BUY refuses by name
    before any venue call.
    """

    import datetime as _dt
    from types import SimpleNamespace

    from src.engine import event_reactor_adapter as era
    from src.engine.global_auction_universe import current_portfolio_wealth_witness

    conn, now = _held_trade_db(tmp_path / "trades.db")
    sealed = current_portfolio_wealth_witness(
        conn, decision_at_utc=now, max_age=_dt.timedelta(seconds=300)
    )
    actuation = SimpleNamespace(wealth_economic_identity=sealed.economic_identity)
    assert era._global_actuation_current_wealth_block_reason(
        conn, global_actuation=actuation, decision_time=now
    ) is None

    # The monitor's exit decision commits mid-cut (EXIT_INTENT -> pending_exit).
    conn.execute(
        "UPDATE position_current SET phase = 'pending_exit' "
        "WHERE position_id = 'held-1'"
    )
    conn.commit()

    reason = era._global_actuation_current_wealth_block_reason(
        conn, global_actuation=actuation, decision_time=now
    )
    assert reason is not None
    assert reason.startswith("GLOBAL_PREFLIGHT_WEALTH_SUPERSEDED:")


def test_actuate_preflighted_runs_the_current_wealth_guard_before_submit():
    """F1 wiring: the BUY actuation path consults current wealth before
    ``_submit_inner`` and returns its reason without submitting."""

    tree = ast.parse((_ROOT / "src/engine/event_reactor_adapter.py").read_text())
    actuate = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_actuate_preflighted"
    )
    body = ast.unparse(actuate)
    guard = body.index("_global_actuation_current_wealth_block_reason(")
    submit = body.index("_submit_inner(")
    assert guard < submit
    assert "reason=wealth_block" in body


def test_exact_sell_debt_still_ends_a_cut_while_the_monitor_runs(monitor_env):
    """F2 (from the deleted exact-completion tests): a queued exact held-SELL
    is capital already decided; it keeps ending ordinary cuts at the stage
    predicate, under its own label, while a monitor owns its claim."""

    from src.events import reactor

    main, _runs = monitor_env
    reactor._EXACT_EXECUTABLE_HELD_SELL_PENDING.set()
    main._held_position_monitor_active.set()
    try:
        cancelled = reactor._process_pending_cancelled(
            committed_day0_wake=False,
            producer_fast_path=False,
            urgent_wake_pending=lambda: False,
            urgent_day0_pending=None,
        )
        assert cancelled() is True
    finally:
        main._held_position_monitor_active.clear()
        reactor._EXACT_EXECUTABLE_HELD_SELL_PENDING.clear()


def test_monitor_cadence_debt_still_scopes_buy_at_cut_admission(monkeypatch):
    """F2 (from the deleted wrapper tests): canonical cadence debt still blocks
    BUY for exactly the overdue families at cut admission -- the capital law
    the monitor handoff never owned and that stays in force without it."""

    import src.events.reactor as reactor_module
    import src.main as main

    captured: dict[str, object] = {}
    bootstrap = threading.Event()
    bootstrap.set()
    debt = threading.Event()
    monkeypatch.setattr(main, "_held_position_monitor_bootstrap_complete", bootstrap)
    monkeypatch.setattr(main, "_held_position_monitor_canonical_debt", debt)
    monkeypatch.setattr(main, "_start_edli_reactor_wake_listener", lambda: None)
    monkeypatch.setattr(main, "_consume_live_control_commands", lambda: None)
    monkeypatch.setattr(main, "_edli_live_entry_readiness_block", lambda _c: (None, {}))
    monkeypatch.setattr(
        main,
        "_held_position_monitor_entry_block_reason",
        lambda: "held_position_monitor_cadence_overdue",
    )
    monkeypatch.setattr(
        main,
        "_canonical_monitor_entry_block_scope",
        lambda reason: (None, {"overdue-family": reason}),
    )
    monkeypatch.setattr(
        "src.control.control_plane.recover_deploy_live_restart_guard",
        lambda: {"status": "noop"},
    )
    monkeypatch.setattr(
        reactor_module,
        "run_edli_event_reactor_cycle",
        lambda **kwargs: captured.update(kwargs) or True,
    )

    assert main._edli_event_reactor_cycle() is True
    assert captured["live_entry_family_block_reasons"] == {
        "overdue-family": "held_position_monitor_cadence_overdue"
    }
    assert debt.is_set()


def test_full_book_monitor_clears_cadence_debt_only_on_fresh_coverage(
    monitor_env, monkeypatch
):
    """F2 (from the deleted recovery/handoff tests): a completed full-book pass
    clears canonical debt only when the canonical re-read shows no overdue
    position -- with a cut holding the reactor lock throughout."""

    main, runs = monitor_env
    counts = [(1, 0, {})]
    monkeypatch.setattr(
        main, "_held_position_monitor_recovery_counts", lambda _e: counts[0]
    )
    main._held_position_monitor_canonical_debt.set()
    with main._edli_reactor_active_lock:
        assert main._exit_monitor_cycle() is True
        assert main._held_position_monitor_canonical_debt.is_set()
        counts[0] = (0, 0, {})
        assert main._exit_monitor_cycle() is True
        assert not main._held_position_monitor_canonical_debt.is_set()
    assert len(runs) == 2
