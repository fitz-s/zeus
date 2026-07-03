from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from src.state.db import init_schema
from src.state.venue_command_repo import append_event


NOW = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _insert_command(conn: sqlite3.Connection, *, intent_kind: str = "ENTRY") -> None:
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason
        ) VALUES (
            'cmd-entry', 'snap-1', 'env-1', 'pos-1', 'decision-1',
            'idem-1', ?, 'market-1', 'token-1', 'BUY', 20.0, 0.5,
            NULL, 'INTENT_CREATED', NULL, ?, ?, NULL
        )
        """,
        (intent_kind, NOW, NOW),
    )
    conn.commit()


def _valid_entry_payload() -> dict[str, object]:
    return {
        "execution_capability": {
            "allowed": True,
            "components": [
                {"component": "cutover_guard", "allowed": True, "reason": "allowed"},
                {
                    "component": "entry_economics",
                    "allowed": True,
                    "reason": "allowed",
                    "details": {
                        "q_live": 0.72,
                        "q_lcb_5pct": 0.62,
                        "expected_edge": 0.12,
                        "limit_price": 0.5,
                        "submit_edge": 0.12,
                        "expected_profit_usd": 2.4,
                        "min_entry_price": 0.05,
                        "min_expected_profit_usd": 0.05,
                        "submit_edge_density": 0.24,
                        "min_submit_edge_density": 0.04,
                        "shares": 20.0,
                        "qkernel_side": "YES",
                    },
                },
                {
                    "component": "entry_actionable_certificate",
                    "allowed": True,
                    "reason": "allowed",
                    "details": {"certificate_hash": "a" * 64},
                },
            ],
        }
    }


def test_entry_submit_requested_requires_live_economics_and_certificate_proof() -> None:
    conn = _conn()
    _insert_command(conn)

    with pytest.raises(ValueError, match="missing live submit proof components"):
        append_event(
            conn,
            command_id="cmd-entry",
            event_type="SUBMIT_REQUESTED",
            occurred_at=NOW,
            payload={
                "execution_capability": {
                    "allowed": True,
                    "components": [
                        {"component": "cutover_guard", "allowed": True, "reason": "allowed"}
                    ],
                }
            },
        )

    assert (
        conn.execute(
            "SELECT COUNT(*) FROM venue_command_events WHERE command_id='cmd-entry'"
        ).fetchone()[0]
        == 0
    )
    assert conn.execute("SELECT state FROM venue_commands").fetchone()[0] == "INTENT_CREATED"


def test_entry_submit_requested_accepts_current_live_proof_payload() -> None:
    conn = _conn()
    _insert_command(conn)

    append_event(
        conn,
        command_id="cmd-entry",
        event_type="SUBMIT_REQUESTED",
        occurred_at=NOW,
        payload=_valid_entry_payload(),
    )

    row = conn.execute(
        "SELECT state, payload_json FROM venue_commands vc "
        "JOIN venue_command_events vce ON vce.command_id=vc.command_id "
        "WHERE vce.event_type='SUBMIT_REQUESTED'"
    ).fetchone()
    assert row["state"] == "SUBMITTING"
    payload = json.loads(row["payload_json"])
    components = payload["execution_capability"]["components"]
    assert {component["component"] for component in components} >= {
        "entry_economics",
        "entry_actionable_certificate",
    }


def test_exit_submit_requested_is_not_bound_to_entry_proof_shape() -> None:
    conn = _conn()
    _insert_command(conn, intent_kind="EXIT")

    append_event(
        conn,
        command_id="cmd-entry",
        event_type="SUBMIT_REQUESTED",
        occurred_at=NOW,
    )

    assert conn.execute("SELECT state FROM venue_commands").fetchone()[0] == "SUBMITTING"
