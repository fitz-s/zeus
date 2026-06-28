import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

from src.contracts import Direction, ExecutionIntent
from src.contracts.slippage_bps import SlippageBps
from src.state import db as state_db
from src.execution.executor import _entry_actionable_certificate_component, _live_order
from src.state.decision_integrity_quarantine import (
    DECISION_CERTIFICATES_TABLE,
    REASON_INVALID_LIVE_ACTIONABLE,
    REASON_INVALID_LIVE_PARENT_MODE,
)


def _conn_with_world_cert_table() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ':memory:' AS world")
    conn.execute(
        """
        CREATE TABLE world.decision_certificates (
            certificate_hash TEXT NOT NULL,
            certificate_type TEXT NOT NULL,
            mode TEXT NOT NULL,
            verifier_status TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE decision_integrity_quarantine (
            table_name TEXT NOT NULL,
            row_id TEXT NOT NULL,
            reason_code TEXT NOT NULL
        )
        """
    )
    return conn


def _valid_actionable_payload() -> dict:
    return {
        "event_id": "event-1",
        "event_type": "FORECAST_SNAPSHOT_READY",
        "causal_snapshot_id": "snap-1",
        "family_id": "family-1",
        "candidate_id": "candidate-1",
        "condition_id": "condition-1",
        "token_id": "yes-1",
        "direction": "buy_yes",
        "strategy_key": "center_buy",
        "executable_snapshot_id": "exec-1",
        "q_live": 0.7,
        "q_lcb_5pct": 0.6,
        "c_fee_adjusted": 0.4,
        "c_cost_95pct": 0.45,
        "p_fill_lcb": 0.1,
        "trade_score": 0.2,
        "action_score": 0.2,
        "min_entry_price": 0.05,
        "selection_authority_applied": "qkernel_spine",
        "qkernel_execution_economics": {
            "source": "qkernel_spine",
            "side": "YES",
            "payoff_q_point": 0.7,
            "payoff_q_lcb": 0.6,
            "cost": 0.4,
            "edge_lcb": 0.2,
            "optimal_delta_u": 0.01,
            "false_edge_rate": 0.01,
            "direction_law_ok": True,
            "coherence_allows": True,
        },
        "fdr_family_id": "family-1",
        "kelly_decision_id": "kelly-1",
        "kelly_size_usd": 3.0,
        "risk_decision_id": "risk-1",
        "live_cap_usage_id": "cap-1",
        "final_intent_id": "intent-1",
        "side_effect_status": "ACTIONABLE_NOT_SUBMITTED",
        "native_quote_available": True,
        "submitted": False,
    }


def _insert_actionable(conn: sqlite3.Connection, *, payload: dict | None = None) -> None:
    conn.execute(
        """
        INSERT INTO world.decision_certificates (
            certificate_hash, certificate_type, mode, verifier_status, payload_json
        ) VALUES (?, 'ActionableTradeCertificate', 'LIVE', 'VERIFIED', ?)
        """,
        ("h1", json.dumps(payload or _valid_actionable_payload())),
    )


def _submit_intent(**overrides) -> ExecutionIntent:
    payload = {
        "direction": Direction("buy_yes"),
        "target_size_usd": 9.0,
        "limit_price": 0.4,
        "toxicity_budget": 0.05,
        "max_slippage": SlippageBps(value_bps=0.0, direction="zero"),
        "is_sandbox": False,
        "market_id": "market-1",
        "token_id": "yes-1",
        "timeout_seconds": 60,
        "executable_snapshot_id": "exec-1",
        "executable_snapshot_min_tick_size": "0.01",
        "executable_snapshot_min_order_size": "0.01",
        "executable_snapshot_neg_risk": False,
        "q_live": 0.7,
        "q_lcb_5pct": 0.6,
        "expected_edge": 0.2,
        "min_entry_price": 0.05,
        "min_expected_profit_usd": 0.05,
        "min_submit_edge_density": 0.02,
        "qkernel_execution_economics": {
            "source": "qkernel_spine",
            "side": "YES",
            "payoff_q_point": 0.7,
            "payoff_q_lcb": 0.6,
            "cost": 0.4,
            "edge_lcb": 0.2,
            "optimal_delta_u": 0.01,
            "false_edge_rate": 0.01,
            "direction_law_ok": True,
            "coherence_allows": True,
        },
        "actionable_certificate_hash": "h1",
    }
    payload.update(overrides)
    return ExecutionIntent(**payload)


def test_entry_actionable_certificate_guard_requires_live_verified_row():
    conn = _conn_with_world_cert_table()
    intent = SimpleNamespace(actionable_certificate_hash="h1")

    component = _entry_actionable_certificate_component(conn, intent)

    assert component["allowed"] is False
    assert component["reason"] == "actionable_certificate_not_persisted_live_verified"


def test_entry_actionable_certificate_guard_allows_persisted_live_verified_row():
    conn = _conn_with_world_cert_table()
    conn.execute(
        """
        INSERT INTO world.decision_certificates (
            certificate_hash, certificate_type, mode, verifier_status, payload_json
        ) VALUES (?, 'ActionableTradeCertificate', 'LIVE', 'VERIFIED', ?)
        """,
        ("h1", json.dumps(_valid_actionable_payload())),
    )
    intent = SimpleNamespace(actionable_certificate_hash="h1")

    component = _entry_actionable_certificate_component(conn, intent)

    assert component["allowed"] is True
    assert component["details"]["certificate_schema"] == "world"


def test_entry_actionable_certificate_guard_rejects_intent_payload_mismatch():
    conn = _conn_with_world_cert_table()
    _insert_actionable(conn)
    intent = _submit_intent(token_id="other-token")

    component = _entry_actionable_certificate_component(conn, intent)

    assert component["allowed"] is False
    assert component["reason"] == "actionable_certificate_fails_current_verifier"
    assert "token_mismatch" in component["details"]["verification_error"]


def test_entry_actionable_certificate_guard_allows_recaptured_current_snapshot():
    conn = _conn_with_world_cert_table()
    _insert_actionable(conn)
    intent = _submit_intent(
        executable_snapshot_id="fresh-recaptured-snapshot",
        actionable_executable_snapshot_id="exec-1",
    )

    component = _entry_actionable_certificate_component(conn, intent)

    assert component["allowed"] is True


def test_entry_actionable_certificate_guard_rejects_authorized_snapshot_mismatch():
    conn = _conn_with_world_cert_table()
    _insert_actionable(conn)
    intent = _submit_intent(
        executable_snapshot_id="fresh-recaptured-snapshot",
        actionable_executable_snapshot_id="wrong-authorized-snapshot",
    )

    component = _entry_actionable_certificate_component(conn, intent)

    assert component["allowed"] is False
    assert component["reason"] == "actionable_certificate_fails_current_verifier"
    assert "snapshot_mismatch" in component["details"]["verification_error"]


def test_entry_actionable_certificate_guard_binds_edli_execution_identity():
    conn = _conn_with_world_cert_table()
    _insert_actionable(conn)
    intent = _submit_intent()

    component = _entry_actionable_certificate_component(
        conn,
        intent,
        decision_id="edli_exec_cmd:event-1:intent-1:yes-1:buy_yes",
    )

    assert component["allowed"] is True


def test_entry_actionable_certificate_guard_rejects_edli_identity_mismatch():
    conn = _conn_with_world_cert_table()
    _insert_actionable(conn)
    intent = _submit_intent()

    component = _entry_actionable_certificate_component(
        conn,
        intent,
        decision_id="edli_exec_cmd:event-1:intent-1:other-token:buy_yes",
    )

    assert component["allowed"] is False
    assert component["reason"] == "actionable_certificate_fails_current_verifier"
    assert "edli_token_mismatch" in component["details"]["verification_error"]


def test_live_entry_rejects_synthetic_decision_id_before_command_persist(
    monkeypatch,
    tmp_path,
):
    conn = sqlite3.connect(tmp_path / "trades.db")
    conn.row_factory = sqlite3.Row
    state_db.init_schema(conn)
    monkeypatch.setattr(
        "src.execution.executor._assert_cutover_allows_submit",
        lambda _intent_kind: {"component": "cutover", "allowed": True},
    )
    monkeypatch.setattr(
        "src.execution.executor._assert_risk_allocator_allows_submit",
        lambda _intent: {"component": "risk_allocator", "allowed": True},
    )

    with patch(
        "src.data.polymarket_client.PolymarketClient",
        side_effect=AssertionError("venue client must not be constructed"),
    ):
        result = _live_order(
            "trade-synthetic-decision",
            _submit_intent(),
            shares=10.0,
            conn=conn,
            decision_id="",
        )

    assert result.status == "rejected"
    assert result.command_state == "REJECTED"
    assert "missing_durable_live_entry_decision_id" in (result.reason or "")
    assert conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 0


def test_entry_actionable_certificate_guard_attaches_world_from_trade_main(
    tmp_path,
    monkeypatch,
):
    world_path = tmp_path / "zeus-world.db"
    trade_path = tmp_path / "zeus_trades.db"
    world_conn = sqlite3.connect(world_path)
    world_conn.execute(
        """
        CREATE TABLE decision_certificates (
            certificate_hash TEXT NOT NULL,
            certificate_type TEXT NOT NULL,
            mode TEXT NOT NULL,
            verifier_status TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    world_conn.execute(
        """
        INSERT INTO decision_certificates (
            certificate_hash, certificate_type, mode, verifier_status, payload_json
        ) VALUES (?, 'ActionableTradeCertificate', 'LIVE', 'VERIFIED', ?)
        """,
        ("h1", json.dumps(_valid_actionable_payload())),
    )
    world_conn.commit()
    world_conn.close()
    monkeypatch.setattr(state_db, "ZEUS_WORLD_DB_PATH", world_path)
    conn = sqlite3.connect(trade_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE decision_certificates (
            certificate_hash TEXT NOT NULL,
            certificate_type TEXT NOT NULL,
            mode TEXT NOT NULL,
            verifier_status TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE decision_integrity_quarantine (
            table_name TEXT NOT NULL,
            row_id TEXT NOT NULL,
            reason_code TEXT NOT NULL
        )
        """
    )
    intent = SimpleNamespace(actionable_certificate_hash="h1")

    component = _entry_actionable_certificate_component(conn, intent)

    assert component["allowed"] is True
    assert component["details"]["certificate_schema"] == "world"


def test_entry_actionable_certificate_guard_rejects_invalid_current_payload():
    conn = _conn_with_world_cert_table()
    payload = _valid_actionable_payload()
    payload["q_live"] = 0.01
    payload["q_lcb_5pct"] = 0.003
    payload["qkernel_execution_economics"]["payoff_q_point"] = 0.2
    payload["qkernel_execution_economics"]["payoff_q_lcb"] = 0.05
    payload["qkernel_execution_economics"]["direction_law_ok"] = False
    _insert_actionable(conn, payload=payload)
    intent = SimpleNamespace(actionable_certificate_hash="h1")

    component = _entry_actionable_certificate_component(conn, intent)

    assert component["allowed"] is False
    assert component["reason"] == "actionable_certificate_fails_current_verifier"
    assert "payoff_q_point exceeds" in component["details"]["verification_error"]


def test_entry_actionable_certificate_guard_rejects_quarantined_certificate():
    conn = _conn_with_world_cert_table()
    _insert_actionable(conn)
    conn.execute(
        """
        INSERT INTO decision_integrity_quarantine (table_name, row_id, reason_code)
        VALUES (?, ?, ?)
        """,
        (DECISION_CERTIFICATES_TABLE, "h1", REASON_INVALID_LIVE_ACTIONABLE),
    )
    intent = SimpleNamespace(actionable_certificate_hash="h1")

    component = _entry_actionable_certificate_component(conn, intent)

    assert component["allowed"] is False
    assert component["reason"] == "actionable_certificate_quarantined"


def test_entry_actionable_certificate_guard_rejects_parent_mode_quarantine():
    conn = _conn_with_world_cert_table()
    _insert_actionable(conn)
    conn.execute(
        """
        INSERT INTO decision_integrity_quarantine (table_name, row_id, reason_code)
        VALUES (?, ?, ?)
        """,
        (DECISION_CERTIFICATES_TABLE, "h1", REASON_INVALID_LIVE_PARENT_MODE),
    )
    intent = SimpleNamespace(actionable_certificate_hash="h1")

    component = _entry_actionable_certificate_component(conn, intent)

    assert component["allowed"] is False
    assert component["reason"] == "actionable_certificate_quarantined"
