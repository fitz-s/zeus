# Created: 2026-05-31
# Last reused/audited: 2026-07-09
# Authority basis: /tmp/edli_submit_gate_trace.md (EDLI submit gate: allocator_not_configured
#   root) + src/engine/cycle_runner.py:705-728 legacy refresh_global_allocator contract.
"""Relationship test for the EDLI live-path risk-allocator refresh seam.

Cross-module invariant under test (Fitz methodology — test the boundary, not a
function):

    WHEN the EDLI event-reactor cycle runs in ``live_path``, the process
    singletons ``_GLOBAL_ALLOCATOR`` / ``_GLOBAL_GOVERNOR_STATE`` in
    ``src.risk_allocator.governor`` MUST be configured at the moment the live
    submit path calls ``select_global_order_type`` — i.e. a qualifying candidate
    must NOT receive ``AllocationDenied("allocator_not_configured")``.

Background: the live ``_live_order`` submit path
(``src/execution/executor.py``) calls ``select_global_order_type`` which raises
``AllocationDenied("allocator_not_configured")`` whenever those singletons are
None. In EDLI/canary mode the daemon runs ``_edli_event_reactor_cycle`` (NOT the
legacy discover cycle that calls ``refresh_global_allocator``), so before the fix
the singletons stay None and every canary order silently blocks. The fix wires a
live-path refresh into the EDLI cycle.

The refresh is factored into the importable helper
``src.main._edli_refresh_global_allocator`` so this relationship
test can drive the real cross-module seam without booting the full daemon.

The tests reset the singletons to None in setup to reproduce the unconfigured
(blocked) state, prove the block exists, then prove the fix removes it while
publishing the TRUE drawdown (not a fake 0.0) and failing CLOSED when drawdown is
unsourceable.
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest


def _world_conn() -> sqlite3.Connection:
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    return conn


def _reset_global_allocator() -> None:
    """Reproduce the unconfigured in-process state the running daemon starts in."""
    from src.risk_allocator import configure_global_allocator

    configure_global_allocator(None, None)


def _empty_snapshot():
    # select_global_order_type accepts any snapshot (or None); a minimal namespace
    # avoids depth-based maker/taker NO_TRADE branches so the test isolates the
    # allocator_not_configured guard.
    return SimpleNamespace()


@pytest.fixture(autouse=True)
def _isolate_global_allocator():
    # Ensure neither the unconfigured-block nor a prior test's configured state
    # leaks across tests; this is a process singleton.
    _reset_global_allocator()
    yield
    _reset_global_allocator()


def test_unconfigured_singletons_block_submit_path_with_allocator_not_configured():
    """Baseline: prove the BLOCK exists when singletons are None (pre-fix state)."""
    from src.risk_allocator import select_global_order_type
    from src.risk_allocator.governor import AllocationDenied

    with pytest.raises(AllocationDenied) as excinfo:
        select_global_order_type(_empty_snapshot())
    assert excinfo.value.decision.reason == "allocator_not_configured"


def test_allocator_refresh_configures_singletons_so_submit_path_does_not_deny(monkeypatch):
    """RELATIONSHIP: after the EDLI live-path refresh, the submit path's
    ``select_global_order_type`` no longer raises ``allocator_not_configured``.

    This is the exact cross-module boundary the live canary order crosses.
    """
    import src.main as main
    import src.control.heartbeat_supervisor as heartbeat_supervisor
    import src.control.ws_gap_guard as ws_gap_guard
    import src.runtime.bankroll_provider as bankroll_provider
    from src.risk_allocator import select_global_order_type
    from src.risk_allocator.governor import AllocationDenied
    from src.runtime.bankroll_provider import BankrollOfRecord

    conn = _world_conn()

    # Source the TRUE current drawdown from baseline (capital metadata) vs the
    # on-chain wallet bankroll. baseline=1000, bankroll=995 -> 0.5% drawdown, which
    # is well under the default 10% kill-switch so the governor stays tradeable and
    # the test isolates the allocator_not_configured boundary (not the drawdown gate).
    monkeypatch.setattr(
        main,
        "load_portfolio",
        lambda *a, **k: SimpleNamespace(daily_baseline_total=1000.0, bankroll=995.0),
    )
    monkeypatch.setattr(
        bankroll_provider,
        "cached",
        lambda *a, **k: BankrollOfRecord(value_usd=995.0, fetched_at="2026-05-31T00:00:00+00:00"),
    )
    # Healthy control-plane so the governor does not deny on heartbeat_lost/ws_gap;
    # this test isolates the allocator_not_configured boundary that the fix removes.
    monkeypatch.setattr(heartbeat_supervisor, "summary", lambda: {"health": "HEALTHY"})
    monkeypatch.setattr(ws_gap_guard, "summary", lambda *, now=None: {})

    # Pre-condition: the unconfigured guard fires with allocator_not_configured.
    with pytest.raises(AllocationDenied) as pre:
        select_global_order_type(_empty_snapshot())
    assert pre.value.decision.reason == "allocator_not_configured"

    summary = main._edli_refresh_global_allocator(conn)

    # The boundary invariant: the submit path no longer denies for LACK OF CONFIG.
    # A configured-but-healthy governor returns a concrete order type.
    order_type = select_global_order_type(_empty_snapshot())
    assert order_type in {"GTC", "FOK"}

    # And the refresh published a CONFIGURED governor state.
    assert summary.get("configured") is True
    assert summary.get("entry", {}).get("reason") != "allocator_not_configured"


def test_allocator_refresh_publishes_true_drawdown_when_baseline_positive(monkeypatch):
    """MATH-UNIT: when baseline IS positive, the drawdown driving the governor
    kill-switch must be the REAL value (baseline vs on-chain bankroll), not a
    hardcoded 0.0.  This test monkeypatches a positive baseline specifically to
    exercise the formula path; it is NOT the primary unblock test (baseline is
    structurally 0.0 system-wide — see test_allocator_refresh_zero_baseline_proceeds).
    """
    import src.main as main
    import src.runtime.bankroll_provider as bankroll_provider
    from src.risk_allocator.governor import _GLOBAL_GOVERNOR_STATE  # noqa: F401  (re-read below)
    from src.runtime.bankroll_provider import BankrollOfRecord

    conn = _world_conn()

    # baseline=1000, bankroll=850 -> 15% drawdown (well above any fake 0.0).
    monkeypatch.setattr(
        main,
        "load_portfolio",
        lambda *a, **k: SimpleNamespace(daily_baseline_total=1000.0, bankroll=850.0),
    )
    monkeypatch.setattr(
        bankroll_provider,
        "cached",
        lambda *a, **k: BankrollOfRecord(value_usd=850.0, fetched_at="2026-05-31T00:00:00+00:00"),
    )

    main._edli_refresh_global_allocator(conn)

    import src.risk_allocator.governor as governor

    assert governor._GLOBAL_GOVERNOR_STATE is not None
    assert governor._GLOBAL_GOVERNOR_STATE.current_drawdown_pct == pytest.approx(15.0)


def test_allocator_refresh_reuses_supplied_portfolio_snapshot(monkeypatch):
    """The EDLI reactor already loads PortfolioState for sizing; allocator refresh
    must reuse that cycle snapshot instead of loading the live DB a second time."""
    import src.control.heartbeat_supervisor as heartbeat_supervisor
    import src.control.ws_gap_guard as ws_gap_guard
    import src.main as main
    import src.runtime.bankroll_provider as bankroll_provider
    from src.runtime.bankroll_provider import BankrollOfRecord

    conn = _world_conn()

    def _unexpected_load_portfolio(*_args, **_kwargs):
        raise AssertionError("portfolio_snapshot should avoid a second load_portfolio()")

    monkeypatch.setattr(main, "load_portfolio", _unexpected_load_portfolio)
    monkeypatch.setattr(
        bankroll_provider,
        "cached",
        lambda *a, **k: BankrollOfRecord(value_usd=995.0, fetched_at="2026-05-31T00:00:00+00:00"),
    )
    monkeypatch.setattr(heartbeat_supervisor, "summary", lambda: {"health": "HEALTHY"})
    monkeypatch.setattr(ws_gap_guard, "summary", lambda *, now=None: {})

    summary = main._edli_refresh_global_allocator(
        conn,
        portfolio_snapshot=SimpleNamespace(daily_baseline_total=1000.0, bankroll=995.0),
    )

    assert summary.get("configured") is True


def test_allocator_refresh_fails_closed_when_bankroll_unavailable(monkeypatch):
    """FAIL-CLOSED: if the on-chain bankroll cache is None (wallet unreachable),
    drawdown is untrustworthy. The refresh must NOT configure an
    allow-everything allocator and must signal the live submit to skip — i.e.
    the submit path's ``select_global_order_type`` must STILL raise
    ``allocator_not_configured`` (never fail-open with a fake 0.0 drawdown)."""
    import src.main as main
    import src.runtime.bankroll_provider as bankroll_provider
    from src.risk_allocator import select_global_order_type
    from src.risk_allocator.governor import AllocationDenied

    conn = _world_conn()

    monkeypatch.setattr(
        main,
        "load_portfolio",
        lambda *a, **k: SimpleNamespace(daily_baseline_total=1000.0, bankroll=0.0),
    )
    # Wallet unreachable -> cached() returns None.
    monkeypatch.setattr(bankroll_provider, "cached", lambda *a, **k: None)

    summary = main._edli_refresh_global_allocator(conn)

    assert summary.get("configured") is False
    assert summary.get("fail_closed") is True
    with pytest.raises(AllocationDenied) as excinfo:
        select_global_order_type(_empty_snapshot())
    assert excinfo.value.decision.reason == "allocator_not_configured"


def test_allocator_refresh_zero_baseline_proceeds_with_zero_drawdown(monkeypatch):
    """MATH-UNIT: when daily_baseline_total is 0.0 (structurally true system-wide,
    verified live 2026-05-31), the helper mirrors the legacy cycle's behaviour
    (cycle_runner.py:711: ``_drawdown_pct = ... if _baseline > 0 else 0.0``) —
    it passes drawdown=0.0 to the allocator and PROCEEDS to configure (NOT fail-closed).

    This test uses the REAL load_portfolio() with NO monkeypatch on daily_baseline_total,
    confirming the unblock works under production-accurate loader output.
    """
    import src.control.heartbeat_supervisor as heartbeat_supervisor
    import src.control.ws_gap_guard as ws_gap_guard
    import src.main as main
    import src.runtime.bankroll_provider as bankroll_provider
    from src.risk_allocator import select_global_order_type
    from src.risk_allocator.governor import AllocationDenied
    from src.runtime.bankroll_provider import BankrollOfRecord

    conn = _world_conn()

    # NO monkeypatch on load_portfolio — let it return the real 0.0 baseline.
    # A real bankroll value (the on-chain wallet) — non-None so the bankroll gate passes.
    monkeypatch.setattr(
        bankroll_provider,
        "cached",
        lambda *a, **k: BankrollOfRecord(value_usd=850.0, fetched_at="2026-05-31T00:00:00+00:00"),
    )
    # Healthy control-plane so governor does not deny on heartbeat_lost.
    monkeypatch.setattr(heartbeat_supervisor, "summary", lambda: {"health": "HEALTHY"})
    monkeypatch.setattr(ws_gap_guard, "summary", lambda *, now=None: {})

    # Pre-condition: singletons None → block.
    with pytest.raises(AllocationDenied) as pre:
        select_global_order_type(_empty_snapshot())
    assert pre.value.decision.reason == "allocator_not_configured"

    # Run with REAL load_portfolio() → daily_baseline_total=0.0.
    summary = main._edli_refresh_global_allocator(conn)

    # Must configure (not fail-closed), mirroring legacy drawdown=0.0 tolerance.
    assert summary.get("configured") is True, (
        f"Expected configured=True with real loader (baseline=0.0 system-wide); "
        f"got: {summary}"
    )

    # And the submit path must unblock.
    order_type = select_global_order_type(_empty_snapshot())
    assert order_type in {"GTC", "FOK"}, (
        f"Expected concrete order type, got {order_type!r} — allocator still blocked"
    )


def test_allocator_refresh_fails_closed_when_load_portfolio_raises(monkeypatch):
    """FAIL-CLOSED: any exception while sourcing drawdown must degrade to
    no-submit, never proceed to a live submit with an unconfigured-but-allowing
    allocator."""
    import src.main as main
    import src.runtime.bankroll_provider as bankroll_provider
    from src.risk_allocator import select_global_order_type
    from src.risk_allocator.governor import AllocationDenied
    from src.runtime.bankroll_provider import BankrollOfRecord

    conn = _world_conn()

    def _boom(*a, **k):
        raise RuntimeError("capital metadata load failed")

    monkeypatch.setattr(main, "load_portfolio", _boom)
    monkeypatch.setattr(
        bankroll_provider,
        "cached",
        lambda *a, **k: BankrollOfRecord(value_usd=900.0, fetched_at="2026-05-31T00:00:00+00:00"),
    )

    summary = main._edli_refresh_global_allocator(conn)

    assert summary.get("configured") is False
    assert summary.get("fail_closed") is True
    with pytest.raises(AllocationDenied) as excinfo:
        select_global_order_type(_empty_snapshot())
    assert excinfo.value.decision.reason == "allocator_not_configured"


def test_main_edli_cycle_wires_live_path_allocator_refresh_source():
    """The sole live cycle refreshes allocation before building its adapter."""
    from pathlib import Path

    # R4-b3 (2026-07-08): the EDLI cycle body (including this call site) moved
    # from src/main.py to src.events.reactor.run_edli_event_reactor_cycle.
    source = Path("src/events/reactor.py").read_text()

    refresh_call = source.index("_alloc_refresh = _edli_refresh_global_allocator(")
    adapter_build = source.index("submit_adapter = event_bound_live_adapter_from_trade_conn(")
    assert refresh_call < adapter_build
    assert 'if not _alloc_refresh.get("configured")' in source[refresh_call:adapter_build]


def test_held_position_monitor_refreshes_allocator_before_exit_monitor():
    """Held-position exits must not run before the risk allocator singleton is configured.

    R4-b (2026-07-08): the exit-monitor job BODY moved from src.main._exit_monitor_cycle
    (now a thin scheduler-hook delegate) to src.execution.exit_lifecycle.run_exit_monitor_cycle
    (its owning module — same P1 order-daemon process, same scheduled job). The ordering
    invariant this test protects now lives there.
    """
    import inspect

    from src.execution import exit_lifecycle

    source = inspect.getsource(exit_lifecycle.run_exit_monitor_cycle)

    assert "_refresh_global_allocator_for_held_position_monitor(" in source
    assert source.index("_refresh_global_allocator_for_held_position_monitor(") < source.index(
        "_execute_monitoring_phase("
    )


def test_chain_sync_read_lane_cannot_submit_exits():
    """Restart safety: the chain-sync read lane cannot submit exits.

    R4-b (2026-07-08): exit-monitor body moved to exit_lifecycle.run_exit_monitor_cycle
    (see test_held_position_monitor_refreshes_allocator_before_exit_monitor above).
    """
    import inspect

    from src.execution import exit_lifecycle, post_trade_capital

    chain_source = inspect.getsource(post_trade_capital.chain_sync_read_cycle)
    exit_source = inspect.getsource(exit_lifecycle.run_exit_monitor_cycle)

    assert "_run_chain_sync(portfolio, clob, conn)" in chain_source
    assert "conn.commit()" in chain_source
    assert "_execute_monitoring_phase(" not in chain_source
    retired_switch = "exit_order_" + "submit_enabled"
    assert retired_switch not in chain_source

    assert 'mode="exit_monitor"' in exit_source
    assert "_execute_monitoring_phase(" in exit_source
    assert retired_switch not in exit_source
    assert "_run_chain_sync(" not in exit_source
