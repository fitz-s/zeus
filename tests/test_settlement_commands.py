# Created: 2026-04-27
# Last reused/audited: 2026-05-18
# Authority basis: R3 R1 settlement/redeem command ledger packet
# Lifecycle: created=2026-04-27; last_reviewed=2026-04-30; last_reused=2026-05-18
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

    def redeem(self, condition_id: str, *, index_sets=None):
        # PR-I.5.c: caller now passes index_sets kw — mock accepts and records.
        self.calls.append((condition_id, list(index_sets) if index_sets else None))
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

        def redeem(self, condition_id: str, *, index_sets=None):
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
        INSERT INTO position_current (position_id, phase, trade_id, strategy_key, updated_at, temperature_metric)
        VALUES ('pos-r1', 'active', 'trade-r1', 'center_buy', ?, 'high')
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


def test_harvester_settlement_close_requires_redeem_enqueue_success(conn, monkeypatch):
    import src.execution.exit_lifecycle as exit_lifecycle
    import src.execution.harvester as harvester

    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, strategy_key, city, target_date, updated_at, temperature_metric
        )
        VALUES (
            'pos-karachi-redeem-gate', 'day0_window', 'trade-karachi-redeem-gate',
            'opening_inertia', 'Karachi', '2026-05-17', ?, 'high'
        )
        """,
        (NOW.isoformat(),),
    )
    pos = SimpleNamespace(
        trade_id="trade-karachi-redeem-gate",
        city="Karachi",
        target_date="2026-05-17",
        direction="buy_yes",
        condition_id="cond-karachi",
        token_id="yes-karachi",
        no_token_id=None,
        entry_price=0.37,
        shares=1.5873,
        p_posterior=0.71,
        bin_label="37C or higher",
        exit_price=None,
        entry_method="model",
        selected_method="model",
        decision_snapshot_id="",
        edge_source="opening_inertia",
        strategy="opening_inertia",
        last_exit_at="",
        market_id="market-karachi",
        state="day0_window",
        exit_state="",
        chain_state="",
        temperature_metric="high",
    )
    portfolio = SimpleNamespace(positions=[pos], ignored_tokens=[])
    settlement_records = []
    mark_settled_calls = []
    log_event_calls = []
    suppress_calls = []

    monkeypatch.setattr(harvester, "_get_canonical_exit_flag", lambda: True)
    monkeypatch.setattr(
        harvester,
        "_settlement_economics_for_position",
        lambda position: (position.shares, position.entry_price * position.shares),
    )
    monkeypatch.setattr(
        harvester,
        "enqueue_redeem_command",
        lambda *args, **kwargs: {
            "status": "error",
            "command_id": None,
            "reason": "no such column: winning_index_set",
        },
    )
    monkeypatch.setattr(
        exit_lifecycle,
        "mark_settled",
        lambda *args, **kwargs: mark_settled_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(harvester, "log_event", lambda *args, **kwargs: log_event_calls.append((args, kwargs)))
    monkeypatch.setattr(
        harvester,
        "record_token_suppression",
        lambda *args, **kwargs: suppress_calls.append((args, kwargs)) or {"status": "written"},
    )

    settled = harvester._settle_positions(
        conn,
        portfolio,
        city="Karachi",
        target_date="2026-05-17",
        winning_label="37C or higher",
        settlement_records=settlement_records,
        settlement_authority="VERIFIED",
        settlement_truth_source="WU",
        settlement_market_slug="highest-temperature-in-karachi-on-may-17-2026",
        settlement_temperature_metric="high",
    )

    assert settled == 0
    assert settlement_records == []
    assert mark_settled_calls == []
    assert log_event_calls == []
    assert suppress_calls == []


def test_harvester_question_label_matches_canonical_winning_bin_for_redeem(conn, monkeypatch):
    import src.execution.exit_lifecycle as exit_lifecycle
    import src.execution.harvester as harvester

    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, strategy_key, city, target_date, updated_at, temperature_metric
        )
        VALUES (
            'pos-karachi-question-label', 'day0_window', 'trade-karachi-question-label',
            'opening_inertia', 'Karachi', '2026-05-17', ?, 'high'
        )
        """,
        (NOW.isoformat(),),
    )
    pos = SimpleNamespace(
        trade_id="trade-karachi-question-label",
        city="Karachi",
        target_date="2026-05-17",
        direction="buy_yes",
        condition_id="cond-karachi",
        token_id="yes-karachi",
        no_token_id="no-karachi",
        entry_price=0.37,
        shares=1.5873,
        p_posterior=0.71,
        bin_label="Will the highest temperature in Karachi be 37°C or higher on May 17?",
        exit_price=None,
        entry_method="model",
        selected_method="model",
        decision_snapshot_id="snapshot-karachi",
        edge_source="opening_inertia",
        strategy="opening_inertia",
        last_exit_at="",
        market_id="market-karachi",
        state="day0_window",
        exit_state="",
        chain_state="synced",
        temperature_metric="high",
    )
    portfolio = SimpleNamespace(positions=[pos], ignored_tokens=[])
    settlement_records = []
    enqueue_calls = []
    mark_settled_calls = []

    monkeypatch.setattr(harvester, "_get_canonical_exit_flag", lambda: True)
    monkeypatch.setattr(
        harvester,
        "_settlement_economics_for_position",
        lambda position: (position.shares, position.entry_price * position.shares),
    )

    def _enqueue(*args, **kwargs):
        enqueue_calls.append((args, kwargs))
        return {"status": "queued", "command_id": "cmd-karachi", "reason": None}

    def _mark_settled(portfolio_arg, trade_id, settlement_price, reason):
        mark_settled_calls.append((trade_id, settlement_price, reason))
        return SimpleNamespace(
            trade_id=trade_id,
            bin_label=pos.bin_label,
            direction=pos.direction,
            p_posterior=pos.p_posterior,
            pnl=round(pos.shares * settlement_price - pos.entry_price * pos.shares, 2),
            decision_snapshot_id=pos.decision_snapshot_id,
            edge_source=pos.edge_source,
            strategy=pos.strategy,
            last_exit_at=NOW.isoformat(),
            exit_price=settlement_price,
            exit_reason=reason,
        )

    monkeypatch.setattr(harvester, "enqueue_redeem_command", _enqueue)
    monkeypatch.setattr(exit_lifecycle, "mark_settled", _mark_settled)
    monkeypatch.setattr(harvester, "record_token_suppression", lambda *a, **k: {"status": "written"})
    monkeypatch.setattr(harvester, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(harvester, "log_settlement_event", lambda *a, **k: None)
    monkeypatch.setattr(harvester, "_dual_write_canonical_settlement_if_available", lambda *a, **k: None)

    settled = harvester._settle_positions(
        conn,
        portfolio,
        city="Karachi",
        target_date="2026-05-17",
        winning_label="37°C or higher",
        settlement_records=settlement_records,
        settlement_authority="VERIFIED",
        settlement_truth_source="forecasts.settlements",
        settlement_market_slug="highest-temperature-in-karachi-on-may-17-2026",
        settlement_temperature_metric="high",
        settlement_source="WU",
        settlement_value=37.0,
    )

    assert settled == 1
    assert mark_settled_calls == [("trade-karachi-question-label", 1.0, "SETTLEMENT")]
    assert len(enqueue_calls) == 1
    _, enqueue_kwargs = enqueue_calls[0]
    assert enqueue_kwargs["payout_asset"] == "pUSD"
    assert enqueue_kwargs["market_id"] == "market-karachi"
    assert enqueue_kwargs["pusd_amount_micro"] == 1_587_300
    assert enqueue_kwargs["token_amounts"] == {"yes-karachi": 1.5873}
    assert enqueue_kwargs["winning_index_set"] == '["2"]'


def test_v1_legacy_unresolved_classified_separately_from_v2_pusd_payout(conn, monkeypatch):
    from src.execution.settlement_commands import SettlementState, request_redeem

    monkeypatch.delenv("ZEUS_PUSD_FX_CLASSIFIED", raising=False)
    legacy_id = request_redeem("condition-legacy", "USDC_E", market_id="market-legacy", conn=conn, requested_at=NOW)
    row = command(conn, legacy_id)

    assert row["payout_asset"] == "USDC_E"
    assert row["state"] == SettlementState.REDEEM_REVIEW_REQUIRED.value
    assert json.loads(row["error_payload"])["reason"] == "legacy_usdc_e_payout_requires_operator_review"


def test_enqueue_backfills_existing_redeem_winning_index_set_before_settlement_close(conn):
    from src.execution.harvester import enqueue_redeem_command

    first = enqueue_redeem_command(
        conn,
        condition_id="condition-backfill-index-set",
        payout_asset="pUSD",
        market_id="market-backfill-index-set",
        token_amounts={"yes-token": "1.5"},
        winning_index_set=None,
    )
    assert first["status"] == "queued"
    assert command(conn, first["command_id"])["winning_index_set"] is None

    second = enqueue_redeem_command(
        conn,
        condition_id="condition-backfill-index-set",
        payout_asset="pUSD",
        market_id="market-backfill-index-set",
        token_amounts={"yes-token": "1.5"},
        winning_index_set='["2"]',
    )

    assert second == {"status": "already_exists", "command_id": first["command_id"], "reason": None}
    assert command(conn, first["command_id"])["winning_index_set"] == '["2"]'
    assert "REDEEM_INDEX_SET_BACKFILLED" in states(conn, first["command_id"])


def test_redeem_submit_blocked_until_q_fx_1_classified(conn, monkeypatch):
    from src.execution.settlement_commands import request_redeem, submit_redeem

    monkeypatch.delenv("ZEUS_PUSD_FX_CLASSIFIED", raising=False)
    command_id = request_redeem("condition-gated", "pUSD", market_id="market-gated", conn=conn, requested_at=NOW)
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
