# Created: 2026-04-27
# Last reused/audited: 2026-05-21
# Authority basis: R3 R1 settlement/redeem command ledger packet
# Lifecycle: created=2026-04-27; last_reviewed=2026-05-21; last_reused=2026-05-21
# Purpose: Lock R3 R1 redeem command durability, Q-FX-1 gating, and tx-hash recovery.
# Reuse: Run for settlement/redeem, harvester redemption, collateral FX gate, or payout-asset changes.
# Authority basis update: docs/operations/task_2026-05-21_live_side_effect_risk_boundaries/task.md P0-2 redeem side-effect transaction boundary.
#                         docs/operations/task_2026-05-21_live_side_effect_risk_boundaries/task.md P2-1 required live ATTACH seam.
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
    # Attach an in-memory world DB so submit_redeem's world.executable_market_snapshots
    # query does not raise OperationalError and trigger the REDEEM_NEGRISK_FACT_MISSING
    # fail-closed path on every test that passes a plain conn (Thread 3 fix requires
    # world schema to be present; absent world ATTACH → exception → neg_risk_row=None).
    db.execute("ATTACH DATABASE ':memory:' AS world")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS world.executable_market_snapshots (
          snapshot_id TEXT PRIMARY KEY,
          gamma_market_id TEXT NOT NULL DEFAULT '',
          event_id TEXT NOT NULL DEFAULT '',
          condition_id TEXT NOT NULL,
          question_id TEXT NOT NULL DEFAULT '',
          yes_token_id TEXT NOT NULL DEFAULT '',
          no_token_id TEXT NOT NULL DEFAULT '',
          enable_orderbook INTEGER NOT NULL DEFAULT 0,
          active INTEGER NOT NULL DEFAULT 1,
          closed INTEGER NOT NULL DEFAULT 0,
          min_tick_size TEXT NOT NULL DEFAULT '0.01',
          min_order_size TEXT NOT NULL DEFAULT '5',
          fee_details_json TEXT NOT NULL DEFAULT '{}',
          token_map_json TEXT NOT NULL DEFAULT '{}',
          neg_risk INTEGER NOT NULL DEFAULT 0,
          orderbook_top_bid TEXT NOT NULL DEFAULT '0',
          orderbook_top_ask TEXT NOT NULL DEFAULT '1',
          orderbook_depth_json TEXT NOT NULL DEFAULT '{}',
          raw_gamma_payload_hash TEXT NOT NULL DEFAULT '',
          raw_clob_market_info_hash TEXT NOT NULL DEFAULT '',
          raw_orderbook_hash TEXT NOT NULL DEFAULT '',
          authority_tier TEXT NOT NULL DEFAULT 'GAMMA',
          captured_at TEXT NOT NULL DEFAULT (datetime('now')),
          freshness_deadline TEXT NOT NULL DEFAULT (datetime('now', '+1 day'))
        )
        """
    )
    yield db
    db.close()


def _insert_world_snapshot(
    conn: sqlite3.Connection,
    condition_id: str,
    *,
    neg_risk: int = 0,
    yes_token_id: str = "yes-token-dummy",
    no_token_id: str = "no-token-dummy",
) -> None:
    """Insert a minimal world.executable_market_snapshots row so submit_redeem
    can look up neg_risk facts (Thread 1+3 fix: world schema qualification +
    fail-closed on missing row). Tests that want standard CTF routing pass
    neg_risk=0 (default); negRisk tests pass neg_risk=1."""
    conn.execute(
        """
        INSERT OR REPLACE INTO world.executable_market_snapshots (
          snapshot_id, gamma_market_id, event_id, condition_id, question_id,
          yes_token_id, no_token_id, neg_risk, captured_at, freshness_deadline
        ) VALUES (?, '', '', ?, '', ?, ?, ?, datetime('now'), datetime('now', '+1 day'))
        """,
        (f"snap-{condition_id}", condition_id, yes_token_id, no_token_id, neg_risk),
    )


class FakeRedeemAdapter:
    def __init__(self, response=None, *, exc: Exception | None = None):
        self.response = response if response is not None else {"success": True, "tx_hash": "0xredeem"}
        self.exc = exc
        self.calls = []

    def redeem(self, condition_id: str, *, index_sets=None, **_ignored):
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
    _insert_world_snapshot(conn, "condition-r1")  # Thread 3 fix: world schema required

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
    _insert_world_snapshot(conn, "condition-capability")  # Thread 3 fix: world schema required
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

        def redeem(self, condition_id: str, *, index_sets=None, **_ignored):
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


def test_submit_redeem_external_conn_commits_submitted_before_adapter_contact(tmp_path, monkeypatch):
    from src.execution.settlement_commands import SettlementState, request_redeem, submit_redeem

    db_path = tmp_path / "trades.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    conn.execute("ATTACH DATABASE ':memory:' AS world")
    conn.execute(
        """
        CREATE TABLE world.executable_market_snapshots (
          snapshot_id TEXT PRIMARY KEY,
          gamma_market_id TEXT NOT NULL DEFAULT '',
          event_id TEXT NOT NULL DEFAULT '',
          condition_id TEXT NOT NULL,
          question_id TEXT NOT NULL DEFAULT '',
          yes_token_id TEXT NOT NULL DEFAULT '',
          no_token_id TEXT NOT NULL DEFAULT '',
          enable_orderbook INTEGER NOT NULL DEFAULT 0,
          active INTEGER NOT NULL DEFAULT 1,
          closed INTEGER NOT NULL DEFAULT 0,
          min_tick_size TEXT NOT NULL DEFAULT '0.01',
          min_order_size TEXT NOT NULL DEFAULT '5',
          fee_details_json TEXT NOT NULL DEFAULT '{}',
          token_map_json TEXT NOT NULL DEFAULT '{}',
          neg_risk INTEGER NOT NULL DEFAULT 0,
          orderbook_top_bid TEXT NOT NULL DEFAULT '0',
          orderbook_top_ask TEXT NOT NULL DEFAULT '1',
          orderbook_depth_json TEXT NOT NULL DEFAULT '{}',
          raw_gamma_payload_hash TEXT NOT NULL DEFAULT '',
          raw_clob_market_info_hash TEXT NOT NULL DEFAULT '',
          raw_orderbook_hash TEXT NOT NULL DEFAULT '',
          authority_tier TEXT NOT NULL DEFAULT 'GAMMA',
          captured_at TEXT NOT NULL DEFAULT (datetime('now')),
          freshness_deadline TEXT NOT NULL DEFAULT (datetime('now', '+1 day'))
        )
        """
    )
    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.FX_LINE_ITEM.value)
    allow_redemption(monkeypatch)
    _insert_world_snapshot(conn, "condition-external-submitted")
    command_id = request_redeem(
        "condition-external-submitted",
        "pUSD",
        market_id="market-external-submitted",
        token_amounts={"yes-token": "1"},
        winning_index_set='["2"]',
        conn=conn,
        requested_at=NOW,
    )
    conn.commit()
    observed: list[str] = []

    class InspectingAdapter:
        def redeem(self, condition_id: str, *, index_sets=None, **_ignored):
            read_conn = sqlite3.connect(db_path)
            read_conn.row_factory = sqlite3.Row
            try:
                row = read_conn.execute(
                    "SELECT state FROM settlement_commands WHERE command_id = ?",
                    (command_id,),
                ).fetchone()
                event = read_conn.execute(
                    """
                    SELECT event_type
                      FROM settlement_command_events
                     WHERE command_id = ?
                     ORDER BY id DESC
                     LIMIT 1
                    """,
                    (command_id,),
                ).fetchone()
            finally:
                read_conn.close()
            observed.append(row["state"])
            assert event["event_type"] == "REDEEM_SUBMITTED"
            assert condition_id == "condition-external-submitted"
            return {"success": True, "tx_hash": "0xexternal-submitted"}

    result = submit_redeem(command_id, InspectingAdapter(), object(), conn=conn, submitted_at=NOW)

    assert observed == [SettlementState.REDEEM_SUBMITTED.value]
    assert result.state is SettlementState.REDEEM_TX_HASHED
    conn.close()


def test_submit_redeem_adapter_exception_after_external_submit_does_not_rollback_submitted(
    tmp_path,
    monkeypatch,
):
    from src.execution.settlement_commands import SettlementState, request_redeem, submit_redeem

    db_path = tmp_path / "trades.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    conn.execute("ATTACH DATABASE ':memory:' AS world")
    conn.execute(
        """
        CREATE TABLE world.executable_market_snapshots (
          snapshot_id TEXT PRIMARY KEY,
          gamma_market_id TEXT NOT NULL DEFAULT '',
          event_id TEXT NOT NULL DEFAULT '',
          condition_id TEXT NOT NULL,
          question_id TEXT NOT NULL DEFAULT '',
          yes_token_id TEXT NOT NULL DEFAULT '',
          no_token_id TEXT NOT NULL DEFAULT '',
          enable_orderbook INTEGER NOT NULL DEFAULT 0,
          active INTEGER NOT NULL DEFAULT 1,
          closed INTEGER NOT NULL DEFAULT 0,
          min_tick_size TEXT NOT NULL DEFAULT '0.01',
          min_order_size TEXT NOT NULL DEFAULT '5',
          fee_details_json TEXT NOT NULL DEFAULT '{}',
          token_map_json TEXT NOT NULL DEFAULT '{}',
          neg_risk INTEGER NOT NULL DEFAULT 0,
          orderbook_top_bid TEXT NOT NULL DEFAULT '0',
          orderbook_top_ask TEXT NOT NULL DEFAULT '1',
          orderbook_depth_json TEXT NOT NULL DEFAULT '{}',
          raw_gamma_payload_hash TEXT NOT NULL DEFAULT '',
          raw_clob_market_info_hash TEXT NOT NULL DEFAULT '',
          raw_orderbook_hash TEXT NOT NULL DEFAULT '',
          authority_tier TEXT NOT NULL DEFAULT 'GAMMA',
          captured_at TEXT NOT NULL DEFAULT (datetime('now')),
          freshness_deadline TEXT NOT NULL DEFAULT (datetime('now', '+1 day'))
        )
        """
    )
    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.FX_LINE_ITEM.value)
    allow_redemption(monkeypatch)
    _insert_world_snapshot(conn, "condition-external-crash")
    command_id = request_redeem(
        "condition-external-crash",
        "pUSD",
        market_id="market-external-crash",
        token_amounts={"yes-token": "1"},
        winning_index_set='["2"]',
        conn=conn,
        requested_at=NOW,
    )
    conn.commit()
    adapter = FakeRedeemAdapter(exc=RuntimeError("simulated adapter crash after side effect seam"))

    result = submit_redeem(command_id, adapter, object(), conn=conn, submitted_at=NOW)

    assert result.state is SettlementState.REDEEM_RETRYING
    read_conn = sqlite3.connect(db_path)
    read_conn.row_factory = sqlite3.Row
    try:
        row = read_conn.execute(
            "SELECT state FROM settlement_commands WHERE command_id = ?",
            (command_id,),
        ).fetchone()
        events = [
            event["event_type"]
            for event in read_conn.execute(
                "SELECT event_type FROM settlement_command_events WHERE command_id = ? ORDER BY id",
                (command_id,),
            ).fetchall()
        ]
    finally:
        read_conn.close()
        conn.close()
    assert row["state"] == SettlementState.REDEEM_RETRYING.value
    assert events == ["REDEEM_INTENT_CREATED", "REDEEM_SUBMITTED", "REDEEM_RETRYING"]


def test_submit_redeem_external_conn_attach_failure_fails_before_adapter(
    tmp_path,
    monkeypatch,
):
    import src.state.db as db_module
    from src.execution.settlement_commands import (
        SettlementCommandStateError,
        request_redeem,
        submit_redeem,
    )

    db_path = tmp_path / "redeem-no-world.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    monkeypatch.setattr(
        db_module,
        "ZEUS_WORLD_DB_PATH",
        tmp_path / "missing-parent" / "zeus-world.db",
    )
    allow_redemption(monkeypatch)
    command_id = request_redeem(
        "condition-no-world",
        "USDC",
        market_id="market-no-world",
        token_amounts={"yes-token": "1"},
        winning_index_set='["1"]',
        conn=conn,
        requested_at=NOW,
    )
    adapter = FakeRedeemAdapter()

    with pytest.raises(
        SettlementCommandStateError,
        match="requires world ATTACH before live side-effect boundary",
    ):
        submit_redeem(command_id, adapter, object(), conn=conn, submitted_at=NOW)

    assert adapter.calls == []
    conn.close()


def test_redeem_crash_after_tx_hash_recovers_by_chain_receipt(conn, monkeypatch):
    from src.execution.settlement_commands import SettlementState, reconcile_pending_redeems, request_redeem, submit_redeem

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.TRADING_PNL_INFLOW.value)
    allow_redemption(monkeypatch)
    _insert_world_snapshot(conn, "condition-crash")  # Thread 3 fix: world schema required
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
    _insert_world_snapshot(conn, "condition-fail")  # Thread 3 fix: world schema required
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


# ── Thread 4 tests: negRisk routing through submit_redeem ────────────────────
# Added for PR #187 thread 4 (Copilot): coverage for snapshot-present+token_amounts_json
# → adapter.redeem(neg_risk=True, amount_per_slot=<correct>), YES/NO token-id selection,
# and missing-snapshot-row fail-closed path (REDEEM_NEGRISK_FACT_MISSING).


class _CapturingAdapter:
    """Records all redeem() kwargs for assertions."""

    def __init__(self, response=None):
        self.response = response or {"success": True, "tx_hash": "0xnr"}
        self.calls: list[dict] = []

    def redeem(self, condition_id: str, *, index_sets=None, neg_risk=False, amount_per_slot=None, **_kw):
        self.calls.append(
            {
                "condition_id": condition_id,
                "index_sets": list(index_sets) if index_sets else None,
                "neg_risk": neg_risk,
                "amount_per_slot": amount_per_slot,
            }
        )
        return self.response


def test_submit_redeem_negrisk_snapshot_yes_amount_per_slot(conn, monkeypatch):
    """Thread 4a: snapshot present + token_amounts_json → adapter.redeem() called with
    neg_risk=True and the correct amount_per_slot derived from yes_token_id."""
    from src.execution.settlement_commands import (
        SettlementState,
        request_redeem,
        submit_redeem,
    )

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.FX_LINE_ITEM.value)
    allow_redemption(monkeypatch)

    yes_tok = "0xyes-karachi"
    no_tok = "0xno-karachi"
    _insert_world_snapshot(
        conn, "condition-nr-yes",
        neg_risk=1,
        yes_token_id=yes_tok,
        no_token_id=no_tok,
    )
    token_amounts = {yes_tok: "1.587297"}  # 1.587297 USDC → 1_587_297 micro

    command_id = request_redeem(
        "condition-nr-yes",
        "pUSD",
        market_id="market-nr-yes",
        token_amounts=token_amounts,
        winning_index_set='["2"]',  # YES won
        conn=conn,
        requested_at=NOW,
    )
    adapter = _CapturingAdapter()
    result = submit_redeem(command_id, adapter, object(), conn=conn)

    assert result.state is SettlementState.REDEEM_TX_HASHED, result
    assert len(adapter.calls) == 1
    call = adapter.calls[0]
    assert call["neg_risk"] is True, "neg_risk must be True when snapshot says neg_risk=1"
    assert call["amount_per_slot"] == 1_587_297, (
        f"expected 1_587_297 micro-units from token_amounts_json, got {call['amount_per_slot']}"
    )
    assert call["index_sets"] == [2]


def test_submit_redeem_negrisk_snapshot_no_side_amount(conn, monkeypatch):
    """Thread 4b: NO side (index_set=1) uses no_token_id to look up amount_per_slot."""
    from src.execution.settlement_commands import (
        SettlementState,
        request_redeem,
        submit_redeem,
    )

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.FX_LINE_ITEM.value)
    allow_redemption(monkeypatch)

    yes_tok = "0xyes-no-side"
    no_tok = "0xno-no-side"
    _insert_world_snapshot(
        conn, "condition-nr-no",
        neg_risk=1,
        yes_token_id=yes_tok,
        no_token_id=no_tok,
    )
    token_amounts = {no_tok: "2.5"}  # 2.5 USDC → 2_500_000 micro

    command_id = request_redeem(
        "condition-nr-no",
        "pUSD",
        market_id="market-nr-no",
        token_amounts=token_amounts,
        winning_index_set='["1"]',  # NO won
        conn=conn,
        requested_at=NOW,
    )
    adapter = _CapturingAdapter()
    result = submit_redeem(command_id, adapter, object(), conn=conn)

    assert result.state is SettlementState.REDEEM_TX_HASHED, result
    call = adapter.calls[0]
    assert call["neg_risk"] is True
    assert call["amount_per_slot"] == 2_500_000, (
        f"expected 2_500_000 from no_token_id in token_amounts_json, got {call['amount_per_slot']}"
    )
    assert call["index_sets"] == [1]


def test_submit_redeem_negrisk_missing_snapshot_fails_closed(conn, monkeypatch):
    """Thread 4c: missing world snapshot row → REDEEM_OPERATOR_REQUIRED with
    REDEEM_NEGRISK_FACT_MISSING (topology.yaml:4193 fail-closed law).
    adapter.redeem() must NOT be called."""
    from src.execution.settlement_commands import (
        SettlementState,
        request_redeem,
        submit_redeem,
    )

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.FX_LINE_ITEM.value)
    allow_redemption(monkeypatch)

    # Deliberately do NOT insert a world snapshot for this condition_id
    command_id = request_redeem(
        "condition-nr-missing",
        "pUSD",
        market_id="market-nr-missing",
        token_amounts={"some-token": "1.0"},
        winning_index_set='["2"]',
        conn=conn,
        requested_at=NOW,
    )
    adapter = _CapturingAdapter()
    result = submit_redeem(command_id, adapter, object(), conn=conn)

    assert result.state is SettlementState.REDEEM_OPERATOR_REQUIRED, (
        f"expected REDEEM_OPERATOR_REQUIRED (fail-closed), got {result.state}"
    )
    assert adapter.calls == [], "adapter.redeem() must NOT be called when snapshot is missing"
    assert result.error_payload is not None
    assert result.error_payload.get("errorCode") == "REDEEM_NEGRISK_FACT_MISSING", (
        f"expected REDEEM_NEGRISK_FACT_MISSING in error_payload, got {result.error_payload}"
    )
