# Lifecycle: created=2026-04-27; last_reviewed=2026-04-27; last_reused=2026-04-27
# Purpose: Lock R3 Z1 CutoverGuard fail-closed live-money gate behavior.
# Reuse: Run when cutover state machine, executor submit preflight, or cycle entry-blocking changes.
# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z1.yaml; docs/operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md
"""R3 Z1 CutoverGuard antibodies.

These tests lock the minimal fail-closed behavior required before Zeus can
advance from the corrected CLOB V2 plan into live-money runtime changes.
They intentionally avoid real Polymarket SDK calls and defer reconciliation
semantics owned by later Z2/M5/T1 phases.
"""

from __future__ import annotations

import sqlite3
import hashlib
import hmac
from pathlib import Path

import pytest

from src.contracts import Direction, ExecutionIntent
from src.contracts.slippage_bps import SlippageBps
from src.execution.command_bus import IntentKind
from src.state.db import init_schema


_TEST_OPERATOR_SECRET = "test-cutover-operator-secret"
_READINESS_PASS_JSON = (
    '{"status":"PASS","gate_count":17,"passed_gates":17,'
    '"staged_smoke_status":"PASS","live_deploy_authorized":false}'
)


def _state_path(tmp_path: Path) -> Path:
    return tmp_path / "cutover_guard.json"


def _operator_token(operator_id: str = "test-operator", nonce: str = "nonce-20260427") -> str:
    message = f"v1.{operator_id}.{nonce}".encode()
    signature = hmac.new(_TEST_OPERATOR_SECRET.encode(), message, hashlib.sha256).hexdigest()
    return f"v1.{operator_id}.{nonce}.{signature}"


def _entry_intent() -> ExecutionIntent:
    return ExecutionIntent(
        direction=Direction("buy_yes"),
        target_size_usd=10.0,
        limit_price=0.50,
        toxicity_budget=0.05,
        max_slippage=SlippageBps(value_bps=200.0, direction="adverse"),
        is_sandbox=False,
        market_id="test-market",
        token_id="test-token",
        timeout_seconds=3600,
        decision_edge=0.10,
    )


@pytest.fixture
def mem_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def cutover_operator_secret(monkeypatch):
    monkeypatch.setenv("ZEUS_CUTOVER_OPERATOR_TOKEN_SECRET", _TEST_OPERATOR_SECRET)


def test_initial_state_is_normal_or_prior_persisted(tmp_path):
    from src.control import cutover_guard

    path = _state_path(tmp_path)
    assert cutover_guard.current_state(path=path) is cutover_guard.CutoverState.NORMAL

    decision = cutover_guard.gate_for_intent(IntentKind.ENTRY, path=path)
    assert decision.allow_submit is False
    assert "NORMAL" in (decision.block_reason or "")

    cutover_guard.transition(
        cutover_guard.CutoverState.PRE_CUTOVER_FREEZE,
        operator_token=_operator_token(),
        path=path,
    )
    assert cutover_guard.current_state(path=path) is cutover_guard.CutoverState.PRE_CUTOVER_FREEZE


def test_cannot_transition_without_operator_token(tmp_path):
    from src.control import cutover_guard

    path = _state_path(tmp_path)
    with pytest.raises(cutover_guard.OperatorTokenRequired):
        cutover_guard.transition(cutover_guard.CutoverState.PRE_CUTOVER_FREEZE, operator_token="", path=path)

    assert cutover_guard.current_state(path=path) is cutover_guard.CutoverState.NORMAL
    assert cutover_guard.read_transition_events(path=path) == []


def test_live_enabled_rejects_unsigned_operator_token(tmp_path):
    from src.control import cutover_guard

    path = _state_path(tmp_path)
    evidence = tmp_path / "live_readiness_report_2026-04-27.json"
    evidence.write_text(_READINESS_PASS_JSON)

    for state in (
        cutover_guard.CutoverState.PRE_CUTOVER_FREEZE,
        cutover_guard.CutoverState.CUTOVER_DOWNTIME,
        cutover_guard.CutoverState.POST_CUTOVER_RECONCILE,
    ):
        cutover_guard.transition(state, operator_token=_operator_token(), path=path)

    with pytest.raises(cutover_guard.OperatorTokenInvalid):
        cutover_guard.transition(
            cutover_guard.CutoverState.LIVE_ENABLED,
            operator_token="test-token",
            path=path,
            operator_evidence_path=evidence,
        )

    assert cutover_guard.current_state(path=path) is cutover_guard.CutoverState.POST_CUTOVER_RECONCILE


def test_live_enabled_rejects_generic_operator_note_evidence(tmp_path):
    from src.control import cutover_guard

    path = _state_path(tmp_path)
    evidence = tmp_path / "cutover_runbook_2026-04-27.md"
    evidence.write_text("operator note without 17/17 readiness JSON\n")

    for state in (
        cutover_guard.CutoverState.PRE_CUTOVER_FREEZE,
        cutover_guard.CutoverState.CUTOVER_DOWNTIME,
        cutover_guard.CutoverState.POST_CUTOVER_RECONCILE,
    ):
        cutover_guard.transition(state, operator_token=_operator_token(), path=path)

    with pytest.raises(cutover_guard.OperatorEvidenceInvalid):
        cutover_guard.transition(
            cutover_guard.CutoverState.LIVE_ENABLED,
            operator_token=_operator_token(),
            path=path,
            operator_evidence_path=evidence,
        )

    assert cutover_guard.current_state(path=path) is cutover_guard.CutoverState.POST_CUTOVER_RECONCILE


def test_live_enabled_rejects_failing_readiness_report(tmp_path):
    from src.control import cutover_guard

    path = _state_path(tmp_path)
    evidence = tmp_path / "live_readiness_report_2026-04-27.json"
    evidence.write_text(
        '{"status":"FAIL","gate_count":17,"passed_gates":16,'
        '"staged_smoke_status":"FAIL","live_deploy_authorized":false}'
    )

    for state in (
        cutover_guard.CutoverState.PRE_CUTOVER_FREEZE,
        cutover_guard.CutoverState.CUTOVER_DOWNTIME,
        cutover_guard.CutoverState.POST_CUTOVER_RECONCILE,
    ):
        cutover_guard.transition(state, operator_token=_operator_token(), path=path)

    with pytest.raises(cutover_guard.OperatorEvidenceInvalid):
        cutover_guard.transition(
            cutover_guard.CutoverState.LIVE_ENABLED,
            operator_token=_operator_token(),
            path=path,
            operator_evidence_path=evidence,
        )

    assert cutover_guard.current_state(path=path) is cutover_guard.CutoverState.POST_CUTOVER_RECONCILE


def test_transition_table_rejects_illegal_jumps(tmp_path):
    from src.control import cutover_guard

    path = _state_path(tmp_path)
    with pytest.raises(cutover_guard.IllegalTransition):
        cutover_guard.transition(
            cutover_guard.CutoverState.CUTOVER_DOWNTIME,
            operator_token=_operator_token(),
            path=path,
        )

    assert cutover_guard.current_state(path=path) is cutover_guard.CutoverState.NORMAL

    cutover_guard.transition(
        cutover_guard.CutoverState.PRE_CUTOVER_FREEZE,
        operator_token=_operator_token(),
        path=path,
    )
    cutover_guard.transition(
        cutover_guard.CutoverState.CUTOVER_DOWNTIME,
        operator_token=_operator_token(),
        path=path,
    )
    assert cutover_guard.current_state(path=path) is cutover_guard.CutoverState.CUTOVER_DOWNTIME


def test_pre_cutover_freeze_blocks_new_submit_allows_cancel(tmp_path):
    from src.control import cutover_guard

    path = _state_path(tmp_path)
    cutover_guard.transition(
        cutover_guard.CutoverState.PRE_CUTOVER_FREEZE,
        operator_token=_operator_token(),
        path=path,
    )

    entry = cutover_guard.gate_for_intent(IntentKind.ENTRY, path=path)
    exit_ = cutover_guard.gate_for_intent(IntentKind.EXIT, path=path)
    cancel = cutover_guard.gate_for_intent(IntentKind.CANCEL, path=path)

    assert entry.allow_submit is False
    assert exit_.allow_submit is False
    assert cancel.allow_cancel is True
    assert "PRE_CUTOVER_FREEZE" in (entry.block_reason or "")


def test_cutover_downtime_blocks_all(tmp_path):
    from src.control import cutover_guard

    path = _state_path(tmp_path)
    cutover_guard.transition(
        cutover_guard.CutoverState.PRE_CUTOVER_FREEZE,
        operator_token=_operator_token(),
        path=path,
    )
    cutover_guard.transition(
        cutover_guard.CutoverState.CUTOVER_DOWNTIME,
        operator_token=_operator_token(),
        path=path,
    )

    for intent in IntentKind:
        decision = cutover_guard.gate_for_intent(intent, path=path)
        assert decision.allow_submit is False
        assert decision.allow_cancel is False
        assert decision.allow_redemption is False


def test_live_enabled_allows_normal_v2_operation(tmp_path):
    from src.control import cutover_guard

    path = _state_path(tmp_path)
    evidence = tmp_path / "live_readiness_report_2026-04-27.json"
    evidence.write_text(_READINESS_PASS_JSON)

    for state in (
        cutover_guard.CutoverState.PRE_CUTOVER_FREEZE,
        cutover_guard.CutoverState.CUTOVER_DOWNTIME,
        cutover_guard.CutoverState.POST_CUTOVER_RECONCILE,
        cutover_guard.CutoverState.LIVE_ENABLED,
    ):
        cutover_guard.transition(
            state,
            operator_token=_operator_token(),
            path=path,
            operator_evidence_path=evidence if state is cutover_guard.CutoverState.LIVE_ENABLED else None,
        )

    assert cutover_guard.gate_for_intent(IntentKind.ENTRY, path=path).allow_submit is True
    assert cutover_guard.gate_for_intent(IntentKind.EXIT, path=path).allow_submit is True
    assert cutover_guard.gate_for_intent(IntentKind.CANCEL, path=path).allow_cancel is True
    assert cutover_guard.redemption_decision(path=path).allow_redemption is True


def test_live_enabled_transition_requires_operator_evidence(tmp_path):
    from src.control import cutover_guard

    path = _state_path(tmp_path)
    for state in (
        cutover_guard.CutoverState.PRE_CUTOVER_FREEZE,
        cutover_guard.CutoverState.CUTOVER_DOWNTIME,
        cutover_guard.CutoverState.POST_CUTOVER_RECONCILE,
    ):
        cutover_guard.transition(state, operator_token=_operator_token(), path=path)

    with pytest.raises(cutover_guard.OperatorEvidenceRequired):
        cutover_guard.transition(
            cutover_guard.CutoverState.LIVE_ENABLED,
            operator_token=_operator_token(),
            path=path,
        )

    assert cutover_guard.current_state(path=path) is cutover_guard.CutoverState.POST_CUTOVER_RECONCILE


def test_blocked_kill_switch_blocks_everything(tmp_path):
    from src.control import cutover_guard

    path = _state_path(tmp_path)
    cutover_guard.transition(
        cutover_guard.CutoverState.BLOCKED,
        operator_token=_operator_token(),
        path=path,
    )

    for intent in IntentKind:
        decision = cutover_guard.gate_for_intent(intent, path=path)
        assert decision.allow_submit is False
        assert decision.allow_cancel is False
        assert decision.allow_redemption is False
        assert "BLOCKED" in (decision.block_reason or "")


def test_atomic_write_no_partial_state_visible_during_transition(tmp_path, monkeypatch):
    from src.control import cutover_guard

    path = _state_path(tmp_path)
    cutover_guard.transition(
        cutover_guard.CutoverState.PRE_CUTOVER_FREEZE,
        operator_token=_operator_token(),
        path=path,
    )

    def fail_write(*args, **kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr(cutover_guard, "_atomic_write_json", fail_write)
    with pytest.raises(OSError):
        cutover_guard.transition(
            cutover_guard.CutoverState.CUTOVER_DOWNTIME,
            operator_token=_operator_token(),
            path=path,
        )

    assert cutover_guard.current_state(path=path) is cutover_guard.CutoverState.PRE_CUTOVER_FREEZE
    assert cutover_guard.gate_for_intent(IntentKind.ENTRY, path=path).allow_submit is False


def test_executor_raises_cutover_pending_when_freeze(monkeypatch, tmp_path, mem_conn):
    from src.control import cutover_guard
    from src.execution import executor

    path = _state_path(tmp_path)
    cutover_guard.transition(
        cutover_guard.CutoverState.PRE_CUTOVER_FREEZE,
        operator_token=_operator_token(),
        path=path,
    )
    monkeypatch.setattr(cutover_guard, "CUTOVER_STATE_PATH", path)

    class MustNotConstruct:
        def __init__(self):
            raise AssertionError("PolymarketClient must not be constructed when CutoverGuard blocks")

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", MustNotConstruct)

    with pytest.raises(cutover_guard.CutoverPending):
        executor._live_order(
            trade_id="trade-cutover-freeze",
            intent=_entry_intent(),
            shares=10.0,
            conn=mem_conn,
            decision_id="dec-cutover-freeze",
        )

    assert mem_conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 0


def test_cycle_summary_exposes_cutover_state_and_blocks_discovery(monkeypatch, tmp_path):
    from src.control import cutover_guard
    import src.engine.cycle_runner as cycle_runner
    from src.engine.discovery_mode import DiscoveryMode
    from src.riskguard.risk_level import RiskLevel
    from src.state.portfolio import PortfolioState
    from src.state.strategy_tracker import StrategyTracker

    path = _state_path(tmp_path)
    cutover_guard.transition(
        cutover_guard.CutoverState.PRE_CUTOVER_FREEZE,
        operator_token=_operator_token(),
        path=path,
    )
    monkeypatch.setattr(cutover_guard, "CUTOVER_STATE_PATH", path)

    db_path = tmp_path / "zeus.db"
    conn = sqlite3.connect(db_path)
    init_schema(conn)
    conn.close()

    monkeypatch.setattr(cycle_runner, "get_connection", lambda: sqlite3.connect(db_path))
    monkeypatch.setattr(cycle_runner, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(cycle_runner, "load_portfolio", lambda: PortfolioState())
    monkeypatch.setattr(cycle_runner, "get_tracker", lambda: StrategyTracker())
    monkeypatch.setattr(cycle_runner, "save_tracker", lambda tracker: None)
    monkeypatch.setattr(cycle_runner, "_reconcile_pending_positions", lambda *args, **kwargs: {"entered": 0, "voided": 0, "dirty": False, "tracker_dirty": False})
    monkeypatch.setattr(cycle_runner, "_run_chain_sync", lambda portfolio, clob, conn: ({}, True))
    monkeypatch.setattr(cycle_runner, "_cleanup_orphan_open_orders", lambda portfolio, clob, conn=None: 0)
    monkeypatch.setattr(cycle_runner, "_entry_bankroll_for_cycle", lambda portfolio, clob: (100.0, {}))
    monkeypatch.setattr(cycle_runner, "_execute_monitoring_phase", lambda *args, **kwargs: (False, False))

    def fail_discovery(*args, **kwargs):
        raise AssertionError("discovery must be blocked by CutoverGuard")

    monkeypatch.setattr(cycle_runner, "_execute_discovery_phase", fail_discovery)
    monkeypatch.setattr("src.control.control_plane.process_commands", lambda: [])
    monkeypatch.setattr("src.observability.status_summary.write_status", lambda cycle_summary=None: None)
    monkeypatch.setattr(cycle_runner, "PolymarketClient", lambda: type("DummyClob", (), {"get_balance": lambda self: 100.0})())

    summary = cycle_runner.run_cycle(DiscoveryMode.OPENING_HUNT)

    assert summary["cutover_guard"]["state"] == "PRE_CUTOVER_FREEZE"
    assert summary["entries_blocked_reason"] == "cutover_guard=PRE_CUTOVER_FREEZE"


@pytest.mark.skip(reason="M5 exchange reconciliation findings table owns cutover-wipe classification.")
def test_post_cutover_reconcile_marks_v1_orphans_as_VENUE_WIPED_REVIEW():
    pass


@pytest.mark.skip(reason="T1/G1 live-money integration harness owns full cutover-wipe simulation.")
def test_simulate_cutover_open_orders_wipe_no_silent_resting():
    pass
