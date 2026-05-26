# Created: 2026-05-26
# Last reused/audited: 2026-05-26
# Authority basis: PR332 EDLI live profit-audit promotion package.
from __future__ import annotations

import json
import sqlite3

import pytest

from src.events.live_profit_audit import LiveProfitAuditLedger, promotion_summary, write_promotion_artifact


def test_profit_audit_requires_event_bound_identity_fields():
    ledger = LiveProfitAuditLedger(_conn())

    with pytest.raises(ValueError, match="EDLI_LIVE_PROFIT_AUDIT_REQUIRED_FIELDS_MISSING"):
        ledger.insert_record(
            aggregate_id="event-1:intent-1",
            condition_id="condition-1",
            token_id="token-1",
            order_lifecycle_state="CONFIRMED",
        )


def test_profit_audit_records_terminal_and_unknown_lifecycle_states(tmp_path):
    conn = _conn()
    ledger = LiveProfitAuditLedger(conn)
    ledger.insert_record(
        event_id="event-1",
        aggregate_id="event-1:intent-1",
        final_intent_id="intent-1",
        execution_command_id="command-1",
        condition_id="condition-1",
        token_id="token-1",
        direction="YES",
        side="BUY",
        expected_edge=0.02,
        realized_edge=0.01,
        order_lifecycle_state="CONFIRMED",
    )
    ledger.insert_record(
        event_id="event-2",
        aggregate_id="event-2:intent-2",
        final_intent_id="intent-2",
        execution_command_id="command-2",
        condition_id="condition-2",
        token_id="token-2",
        direction="YES",
        side="BUY",
        expected_edge=0.01,
        order_lifecycle_state="POST_SUBMIT_UNKNOWN",
    )

    summary = promotion_summary(conn)
    assert summary.canary_count == 1
    assert summary.unresolved_unknowns == 1
    assert summary.realized_edge_bps == 100.0

    artifact_path = tmp_path / "promotion.json"
    write_promotion_artifact(conn, str(artifact_path))
    assert json.loads(artifact_path.read_text()) == {
        "canary_count": 1,
        "realized_edge_bps": 100.0,
        "unresolved_unknowns": 1,
    }


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn
