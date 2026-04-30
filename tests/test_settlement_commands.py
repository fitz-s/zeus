# Created: 2026-04-27
# Last reused/audited: 2026-04-30
# Authority basis: R3 R1 settlement/redeem command ledger packet
# Lifecycle: created=2026-04-27; last_reviewed=2026-04-30; last_reused=2026-04-30
# Purpose: Lock R3 R1 redeem command durability, Q-FX-1 gating, and tx-hash recovery.
# Reuse: Run for settlement/redeem, harvester redemption, collateral FX gate, or payout-asset changes.
"""Regression tests for R3 R1 durable settlement/redeem commands."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.contracts.fx_classification import FXClassification, FXClassificationPending
from src.state.db import init_schema

NOW = datetime(2026, 4, 27, 20, 10, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    init_schema(db)
    yield db
    db.close()


class FakeRedeemAdapter:
    def __init__(self, response=None, *, exc: Exception | None = None):
        self.response = response if response is not None else {"success": True, "tx_hash": "0xredeem"}
        self.exc = exc
        self.calls = []

    def redeem(self, condition_id: str):
        self.calls.append(condition_id)
        if self.exc is not None:
            raise self.exc
        return self.response


class FakeEth:
    block_number = 110

    def __init__(self, receipts):
        self.receipts = receipts

    def get_transaction_receipt(self, tx_hash):
        return self.receipts.get(tx_hash)


class FakeWeb3:
    def __init__(self, receipts):
        self.eth = FakeEth(receipts)


def states(conn, command_id):
    return [
        row["event_type"]
        for row in conn.execute(
            "SELECT event_type FROM settlement_command_events WHERE command_id = ? ORDER BY id",
            (command_id,),
        ).fetchall()
    ]


def command(conn, command_id):
    return conn.execute("SELECT * FROM settlement_commands WHERE command_id = ?", (command_id,)).fetchone()


def allow_redemption(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.execution.settlement_commands.redemption_decision",
        lambda: SimpleNamespace(allow_redemption=True, block_reason=None, state="LIVE_ENABLED"),
    )


def test_redeem_lifecycle_atomic_states(conn, monkeypatch):
    from src.execution.settlement_commands import (
        SettlementState,
        reconcile_pending_redeems,
        request_redeem,
        submit_redeem,
    )

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.FX_LINE_ITEM.value)
    allow_redemption(monkeypatch)

    command_id = request_redeem(
        "condition-r1",
        "pUSD",
        market_id="market-r1",
        pusd_amount_micro=1_250_000,
        token_amounts={"yes-token": "12.5"},
        conn=conn,
        requested_at=NOW,
    )
    assert command(conn, command_id)["state"] == SettlementState.REDEEM_INTENT_CREATED.value

    result = submit_redeem(command_id, FakeRedeemAdapter({"success": True, "tx_hash": "0xabc"}), object(), conn=conn)
    assert result.state is SettlementState.REDEEM_TX_HASHED
    assert command(conn, command_id)["tx_hash"] == "0xabc"

    [confirmed] = reconcile_pending_redeems(
        FakeWeb3({"0xabc": {"status": 1, "blockNumber": 100, "transactionHash": "0xabc"}}),
        conn,
    )
    row = command(conn, command_id)
    assert confirmed.state is SettlementState.REDEEM_CONFIRMED
    assert row["state"] == SettlementState.REDEEM_CONFIRMED.value
    assert row["block_number"] == 100
    assert row["confirmation_count"] == 11
    assert row["terminal_at"] is not None
    assert states(conn, command_id) == [
        "REDEEM_INTENT_CREATED",
        "REDEEM_SUBMITTED",
        "REDEEM_TX_HASHED",
        "REDEEM_CONFIRMED",
    ]
    event_hashes = conn.execute(
        "SELECT payload_hash FROM settlement_command_events WHERE command_id = ?",
        (command_id,),
    ).fetchall()
    assert all(len(row["payload_hash"]) == 64 for row in event_hashes)


def test_redeem_submitted_persists_execution_capability_before_adapter_contact(conn, monkeypatch):
    from src.execution.settlement_commands import SettlementState, request_redeem, submit_redeem

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.FX_LINE_ITEM.value)
    allow_redemption(monkeypatch)
    command_id = request_redeem(
        "condition-capability",
        "pUSD",
        market_id="market-capability",
        pusd_amount_micro=2_000_000,
        token_amounts={"yes-token": "2"},
        conn=conn,
        requested_at=NOW,
    )
    seen: list[str] = []

    class InspectingRedeemAdapter:
        calls: list[str]

        def __init__(self) -> None:
            self.calls = []

        def redeem(self, condition_id: str):
            row = conn.execute(
                """
                SELECT payload_json
                  FROM settlement_command_events
                 WHERE command_id = ?
                   AND event_type = 'REDEEM_SUBMITTED'
                 ORDER BY id DESC
                 LIMIT 1
                """,
                (command_id,),
            ).fetchone()
            assert row is not None
            payload = json.loads(row["payload_json"])
            capability = payload["execution_capability"]
            assert payload["pre_side_effect"] is True
            assert condition_id == "condition-capability"
            assert capability["schema_version"] == 1
            assert capability["action"] == "REDEEM"
            assert capability["intent_kind"] == "REDEEM"
            assert capability["mode"] == "redeem"
            assert capability["allowed"] is True
            assert len(capability["capability_id"]) == 32
            assert capability["command_id"] == command_id
            assert capability["condition_id"] == "condition-capability"
            assert capability["market_id"] == "market-capability"
            assert capability["payout_asset"] == "pUSD"
            assert {component["component"] for component in capability["components"]} >= {
                "redeem_command_state",
                "payout_asset_fx_classification",
                "cutover_guard",
            }
            seen.append(capability["capability_id"])
            self.calls.append(condition_id)
            return {"success": True, "tx_hash": "0xcapability"}

    adapter = InspectingRedeemAdapter()
    result = submit_redeem(command_id, adapter, object(), conn=conn, submitted_at=NOW)

    assert adapter.calls == ["condition-capability"]
    assert len(seen) == 1
    assert result.state is SettlementState.REDEEM_TX_HASHED


def test_redeem_crash_after_tx_hash_recovers_by_chain_receipt(conn, monkeypatch):
    from src.execution.settlement_commands import SettlementState, reconcile_pending_redeems, request_redeem, submit_redeem

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.TRADING_PNL_INFLOW.value)
    allow_redemption(monkeypatch)
    command_id = request_redeem("condition-crash", "pUSD", market_id="market-crash", conn=conn, requested_at=NOW)
    submit_redeem(command_id, FakeRedeemAdapter({"success": True, "transaction_hash": "0xcrash"}), object(), conn=conn)

    # Simulated process crash/restart: recovery only needs the durable tx_hash anchor.
    assert command(conn, command_id)["state"] == SettlementState.REDEEM_TX_HASHED.value
    results = reconcile_pending_redeems(FakeWeb3({"0xcrash": {"status": 1, "blockNumber": 109}}), conn)

    assert [result.state for result in results] == [SettlementState.REDEEM_CONFIRMED]
    assert command(conn, command_id)["confirmation_count"] == 2


def test_redeem_failure_does_not_mark_position_settled(conn, monkeypatch):
    from src.execution.settlement_commands import SettlementState, request_redeem, submit_redeem

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.CARRY_COST.value)
    allow_redemption(monkeypatch)
    conn.execute(
        """
        INSERT INTO position_current (position_id, phase, trade_id, strategy_key, updated_at)
        VALUES ('pos-r1', 'active', 'trade-r1', 'center_buy', ?)
        """,
        (NOW.isoformat(),),
    )

    command_id = request_redeem("condition-fail", "pUSD", market_id="market-fail", conn=conn, requested_at=NOW)
    result = submit_redeem(
        command_id,
        FakeRedeemAdapter({"success": False, "errorCode": "CHAIN_REVERT", "errorMessage": "reverted"}),
        object(),
        conn=conn,
    )

    assert result.state is SettlementState.REDEEM_FAILED
    assert command(conn, command_id)["state"] == SettlementState.REDEEM_FAILED.value
    assert conn.execute("SELECT phase FROM position_current WHERE position_id = 'pos-r1'").fetchone()["phase"] == "active"


def test_v1_legacy_unresolved_classified_separately_from_v2_pusd_payout(conn, monkeypatch):
    from src.execution.settlement_commands import SettlementState, request_redeem

    monkeypatch.delenv("ZEUS_PUSD_FX_CLASSIFIED", raising=False)
    legacy_id = request_redeem("condition-legacy", "USDC_E", market_id="market-legacy", conn=conn, requested_at=NOW)
    row = command(conn, legacy_id)

    assert row["payout_asset"] == "USDC_E"
    assert row["state"] == SettlementState.REDEEM_REVIEW_REQUIRED.value
    assert json.loads(row["error_payload"])["reason"] == "legacy_usdc_e_payout_requires_operator_review"


def test_redeem_blocked_until_q_fx_1_classified(conn, monkeypatch):
    from src.execution.settlement_commands import request_redeem, submit_redeem

    monkeypatch.delenv("ZEUS_PUSD_FX_CLASSIFIED", raising=False)
    with pytest.raises(FXClassificationPending):
        request_redeem("condition-gated", "pUSD", market_id="market-gated", conn=conn, requested_at=NOW)
    assert conn.execute("SELECT COUNT(*) FROM settlement_commands").fetchone()[0] == 0

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.FX_LINE_ITEM.value)
    command_id = request_redeem("condition-gated", "pUSD", market_id="market-gated", conn=conn, requested_at=NOW)
    monkeypatch.delenv("ZEUS_PUSD_FX_CLASSIFIED", raising=False)
    adapter = FakeRedeemAdapter({"success": True, "tx_hash": "0xmust-not-call"})

    with pytest.raises(FXClassificationPending):
        submit_redeem(command_id, adapter, object(), conn=conn)

    assert adapter.calls == []
    assert command(conn, command_id)["state"] == "REDEEM_INTENT_CREATED"
    assert states(conn, command_id) == ["REDEEM_INTENT_CREATED"]


def test_redeem_submit_blocks_before_adapter_when_cutover_disallows(conn, monkeypatch):
    from src.control.cutover_guard import CutoverPending
    from src.execution.settlement_commands import request_redeem, submit_redeem

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.FX_LINE_ITEM.value)
    monkeypatch.setattr(
        "src.execution.settlement_commands.redemption_decision",
        lambda: SimpleNamespace(allow_redemption=False, block_reason="BLOCKED:REDEEM", state="BLOCKED"),
    )
    command_id = request_redeem("condition-cutover-block", "pUSD", market_id="market-cutover-block", conn=conn, requested_at=NOW)
    adapter = FakeRedeemAdapter({"success": True, "tx_hash": "0xmust-not-call"})

    with pytest.raises(CutoverPending, match="BLOCKED:REDEEM"):
        submit_redeem(command_id, adapter, object(), conn=conn)

    assert adapter.calls == []
    assert command(conn, command_id)["state"] == "REDEEM_INTENT_CREATED"
    assert states(conn, command_id) == ["REDEEM_INTENT_CREATED"]


def test_payout_asset_constraint_enforced(conn, monkeypatch):
    from src.execution.settlement_commands import request_redeem

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.FX_LINE_ITEM.value)
    with pytest.raises(ValueError, match="unsupported payout_asset"):
        request_redeem("condition-bad", "DAI", market_id="market-bad", conn=conn, requested_at=NOW)
