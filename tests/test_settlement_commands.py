# Created: 2026-04-27
# Last reused/audited: 2026-05-24
# Authority basis: R3 R1 settlement/redeem command ledger packet
# Lifecycle: created=2026-04-27; last_reviewed=2026-05-24; last_reused=2026-05-24
# Purpose: Lock R3 R1 redeem command durability, Q-FX-1 gating, and tx-hash recovery.
# Reuse: Run for settlement/redeem, collateral FX gate, or payout-asset changes.
# Authority basis update: docs/operations/task_2026-05-21_live_side_effect_risk_boundaries/task.md P0-2 redeem side-effect transaction boundary.
#                         docs/operations/task_2026-05-21_live_side_effect_risk_boundaries/task.md P2-1 required live ATTACH seam.
# 2026-07-25 update: Zeus no longer submits on-chain redemption transactions;
#   Polymarket settles win/loss on our behalf. harvester.enqueue_redeem_command
#   was deleted (settlement close never depends on a redeem command).
#   settlement_commands.py itself is RETAINED — it is still read by
#   payout_observer.py (condition coverage), state_census.py, instrumented by
#   executor.py (timing columns), and its request_redeem/reconcile_pending_redeems
#   primitives remain callable by operator-invoked scripts. The three tests that
#   asserted harvester's enqueue wrapper behavior were removed; the winning
#   winning_index_set backfill coverage was re-homed to call request_redeem()
#   directly (see test_request_redeem_backfills_existing_winning_index_set).
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


STANDARD_CTF_PAYOUT_TOPIC = "0x2682012a4a4f1973119f1c9b90745d1bd91fa2bab387344f044cb3586864d18d"
STANDARD_CTF_ADDRESS = "0x4d97dcd97ec945f40cf65f87097ace5ea0476045"


def standard_ctf_receipt(
    condition_id: str,
    *,
    tx_hash: str,
    payout: int = 1_000_000,
    block_number: int = 100,
    data_condition_id: str | None = None,
    index_sets: tuple[int, ...] = (1,),
) -> dict:
    condition_word = (data_condition_id or condition_id).lower().removeprefix("0x").rjust(64, "0")
    words = [
        condition_word,
        f"{96:064x}",
        f"{payout:064x}",
        f"{len(index_sets):064x}",
        *(f"{index_set:064x}" for index_set in index_sets),
    ]
    return {
        "status": 1,
        "blockNumber": block_number,
        "transactionHash": tx_hash,
        "logs": [
            {
                "address": STANDARD_CTF_ADDRESS,
                "topics": [
                    STANDARD_CTF_PAYOUT_TOPIC,
                ],
                "data": "0x" + "".join(words),
            }
        ],
    }


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


def mark_externally_redeemed(conn, command_id: str, *, tx_hash: str) -> None:
    """Test-only fixture setup: put a command directly into REDEEM_TX_HASHED.

    R6-a (2026-07-08): submit_redeem was DELETED (Zeus never submits redeem tx,
    operator law 2026-06-10) -- REDEEM_TX_HASHED is now reached ONLY via an
    operator manually recording an externally-observed redemption
    (scripts/operator_record_redeem.py, which uses this same
    _atomic_transition building block). reconcile_pending_redeems' chain-receipt
    classification is still live READ-PATH code and still needs a
    REDEEM_TX_HASHED row to recover/classify against -- this helper produces
    that row without going through the deleted submit machinery.
    """
    from src.execution.settlement_commands import SettlementState, _atomic_transition

    transitioned = _atomic_transition(
        conn,
        command_id,
        from_state=SettlementState.REDEEM_INTENT_CREATED,
        to_state=SettlementState.REDEEM_TX_HASHED,
        tx_hash=tx_hash,
        recorded_at=NOW.isoformat(),
    )
    assert transitioned, f"fixture setup failed: {command_id} was not in REDEEM_INTENT_CREATED"
    conn.commit()


def test_redeem_crash_after_tx_hash_recovers_by_chain_receipt(conn, monkeypatch):
    from src.execution.settlement_commands import SettlementState, reconcile_pending_redeems, request_redeem

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.TRADING_PNL_INFLOW.value)
    condition_id = "0x" + "b" * 64
    _insert_world_snapshot(conn, condition_id)  # Thread 3 fix: world schema required
    command_id = request_redeem(
        condition_id,
        "pUSD",
        market_id="market-crash",
        pusd_amount_micro=1_000_000,
        conn=conn,
        requested_at=NOW,
    )
    mark_externally_redeemed(conn, command_id, tx_hash="0xcrash")

    # Simulated process crash/restart: recovery only needs the durable tx_hash anchor.
    assert command(conn, command_id)["state"] == SettlementState.REDEEM_TX_HASHED.value
    results = reconcile_pending_redeems(
        FakeWeb3({
            "0xcrash": standard_ctf_receipt(
                condition_id,
                tx_hash="0xcrash",
                block_number=109,
            )
        }),
        conn,
    )

    assert [result.state for result in results] == [SettlementState.REDEEM_CONFIRMED]
    assert command(conn, command_id)["confirmation_count"] == 2


def test_standard_ctf_receipt_without_payout_log_requires_review(conn, monkeypatch):
    from src.execution.settlement_commands import SettlementState, reconcile_pending_redeems, request_redeem

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.TRADING_PNL_INFLOW.value)
    _insert_world_snapshot(conn, "condition-standard-no-proof", neg_risk=0)
    command_id = request_redeem(
        "condition-standard-no-proof",
        "pUSD",
        market_id="market-standard-no-proof",
        conn=conn,
        requested_at=NOW,
    )
    mark_externally_redeemed(conn, command_id, tx_hash="0xstandardnop")

    [result] = reconcile_pending_redeems(
        FakeWeb3({
            "0xstandardnop": {
                "status": 1,
                "blockNumber": 109,
                "transactionHash": "0xstandardnop",
                "logs": [],
            }
        }),
        conn,
    )

    assert result.state is SettlementState.REDEEM_REVIEW_REQUIRED
    assert result.error_payload is not None
    assert result.error_payload["errorCode"] == "REDEEM_STANDARD_CTF_REVIEW_REQUIRED"
    assert command(conn, command_id)["state"] == SettlementState.REDEEM_REVIEW_REQUIRED.value


def test_standard_ctf_receipt_wrong_condition_requires_review(conn, monkeypatch):
    from src.execution.settlement_commands import SettlementState, reconcile_pending_redeems, request_redeem

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.TRADING_PNL_INFLOW.value)
    condition_id = "0x" + "c" * 64
    _insert_world_snapshot(conn, condition_id, neg_risk=0)
    command_id = request_redeem(
        condition_id,
        "pUSD",
        market_id="market-standard-wrong",
        conn=conn,
        requested_at=NOW,
    )
    mark_externally_redeemed(conn, command_id, tx_hash="0xstandardwrong")

    [result] = reconcile_pending_redeems(
        FakeWeb3({
            "0xstandardwrong": standard_ctf_receipt(
                condition_id,
                tx_hash="0xstandardwrong",
                data_condition_id="0x" + "9" * 64,
            )
        }),
        conn,
    )

    assert result.state is SettlementState.REDEEM_REVIEW_REQUIRED
    assert result.error_payload is not None
    assert result.error_payload["errorCode"] == "REDEEM_STANDARD_CTF_WRONG_CONDITION"


def test_standard_ctf_receipt_scans_past_unrelated_wrong_condition_log(conn, monkeypatch):
    from src.execution.settlement_commands import SettlementState, reconcile_pending_redeems, request_redeem

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.TRADING_PNL_INFLOW.value)
    condition_id = "0x" + "e" * 64
    _insert_world_snapshot(conn, condition_id, neg_risk=0)
    command_id = request_redeem(
        condition_id,
        "pUSD",
        market_id="market-standard-multilog",
        pusd_amount_micro=1_000_000,
        conn=conn,
        requested_at=NOW,
    )
    mark_externally_redeemed(conn, command_id, tx_hash="0xstandardmulti")
    wrong_first = standard_ctf_receipt(
        condition_id,
        tx_hash="0xstandardmulti",
        data_condition_id="0x" + "9" * 64,
    )
    correct_second = standard_ctf_receipt(condition_id, tx_hash="0xstandardmulti")
    receipt = dict(wrong_first)
    receipt["logs"] = [wrong_first["logs"][0], correct_second["logs"][0]]

    [result] = reconcile_pending_redeems(
        FakeWeb3({"0xstandardmulti": receipt}),
        conn,
    )

    assert result.state is SettlementState.REDEEM_CONFIRMED
    assert result.error_payload is None


def test_standard_ctf_receipt_amount_mismatch_requires_review(conn, monkeypatch):
    from src.execution.settlement_commands import SettlementState, reconcile_pending_redeems, request_redeem

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.TRADING_PNL_INFLOW.value)
    condition_id = "0x" + "f" * 64
    _insert_world_snapshot(conn, condition_id, neg_risk=0)
    command_id = request_redeem(
        condition_id,
        "pUSD",
        market_id="market-standard-underpaid",
        pusd_amount_micro=2_520_000,
        conn=conn,
        requested_at=NOW,
    )
    mark_externally_redeemed(conn, command_id, tx_hash="0xstandardunder")

    [result] = reconcile_pending_redeems(
        FakeWeb3({
            "0xstandardunder": standard_ctf_receipt(
                condition_id,
                tx_hash="0xstandardunder",
                payout=1,
            )
        }),
        conn,
    )

    assert result.state is SettlementState.REDEEM_REVIEW_REQUIRED
    assert result.error_payload is not None
    assert result.error_payload["errorCode"] == "REDEEM_STANDARD_CTF_AMOUNT_MISMATCH"
    assert result.error_payload["expected_payout_micro"] == 2_520_000
    assert result.error_payload["payout_from_receipt"] == 1


def test_standard_ctf_receipt_amount_within_tolerance_confirms(conn, monkeypatch):
    from src.execution.settlement_commands import SettlementState, reconcile_pending_redeems, request_redeem

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.TRADING_PNL_INFLOW.value)
    condition_id = "0x" + "2" * 64
    _insert_world_snapshot(conn, condition_id, neg_risk=0)
    command_id = request_redeem(
        condition_id,
        "pUSD",
        market_id="market-standard-tolerance",
        pusd_amount_micro=2_520_000,
        conn=conn,
        requested_at=NOW,
    )
    mark_externally_redeemed(conn, command_id, tx_hash="0xstandardtol")

    [result] = reconcile_pending_redeems(
        FakeWeb3({
            "0xstandardtol": standard_ctf_receipt(
                condition_id,
                tx_hash="0xstandardtol",
                payout=2_519_000,
            )
        }),
        conn,
    )

    assert result.state is SettlementState.REDEEM_CONFIRMED
    assert result.error_payload is None


def test_standard_ctf_receipt_missing_expected_amount_requires_review(conn, monkeypatch):
    from src.execution.settlement_commands import SettlementState, reconcile_pending_redeems, request_redeem

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.TRADING_PNL_INFLOW.value)
    condition_id = "0x" + "3" * 64
    _insert_world_snapshot(conn, condition_id, neg_risk=0)
    command_id = request_redeem(
        condition_id,
        "pUSD",
        market_id="market-standard-missing-expected",
        conn=conn,
        requested_at=NOW,
    )
    mark_externally_redeemed(conn, command_id, tx_hash="0xstandardmissing")

    [result] = reconcile_pending_redeems(
        FakeWeb3({
            "0xstandardmissing": standard_ctf_receipt(
                condition_id,
                tx_hash="0xstandardmissing",
                payout=1_000_000,
            )
        }),
        conn,
    )

    assert result.state is SettlementState.REDEEM_REVIEW_REQUIRED
    assert result.error_payload is not None
    assert result.error_payload["errorCode"] == "REDEEM_STANDARD_CTF_AMOUNT_MISSING"
    assert result.error_payload["expected_payout_micro"] is None


def test_standard_ctf_receipt_zero_payout_requires_review(conn, monkeypatch):
    from src.execution.settlement_commands import SettlementState, reconcile_pending_redeems, request_redeem

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.TRADING_PNL_INFLOW.value)
    condition_id = "0x" + "d" * 64
    _insert_world_snapshot(conn, condition_id, neg_risk=0)
    command_id = request_redeem(
        condition_id,
        "pUSD",
        market_id="market-standard-zero",
        conn=conn,
        requested_at=NOW,
    )
    mark_externally_redeemed(conn, command_id, tx_hash="0xstandardzero")

    [result] = reconcile_pending_redeems(
        FakeWeb3({
            "0xstandardzero": standard_ctf_receipt(
                condition_id,
                tx_hash="0xstandardzero",
                payout=0,
            )
        }),
        conn,
    )

    assert result.state is SettlementState.REDEEM_REVIEW_REQUIRED
    assert result.error_payload is not None
    assert result.error_payload["errorCode"] == "REDEEM_STANDARD_CTF_ZERO_PAYOUT"


def test_standard_ctf_real_abi_zero_payout_with_nonzero_index_set_requires_review(conn, monkeypatch):
    from src.execution.settlement_commands import SettlementState, reconcile_pending_redeems, request_redeem

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.TRADING_PNL_INFLOW.value)
    condition_id = "0x" + "1" * 64
    _insert_world_snapshot(conn, condition_id, neg_risk=0)
    command_id = request_redeem(
        condition_id,
        "pUSD",
        market_id="market-standard-abi-zero",
        conn=conn,
        requested_at=NOW,
    )
    mark_externally_redeemed(conn, command_id, tx_hash="0xstandardabizero")

    [result] = reconcile_pending_redeems(
        FakeWeb3({
            "0xstandardabizero": standard_ctf_receipt(
                condition_id,
                tx_hash="0xstandardabizero",
                payout=0,
                index_sets=(1,),
            )
        }),
        conn,
    )

    assert result.state is SettlementState.REDEEM_REVIEW_REQUIRED
    assert result.error_payload is not None
    assert result.error_payload["errorCode"] == "REDEEM_STANDARD_CTF_ZERO_PAYOUT"


def test_v1_legacy_unresolved_classified_separately_from_v2_pusd_payout(conn, monkeypatch):
    from src.execution.settlement_commands import SettlementState, request_redeem

    monkeypatch.delenv("ZEUS_PUSD_FX_CLASSIFIED", raising=False)
    legacy_id = request_redeem("condition-legacy", "USDC_E", market_id="market-legacy", conn=conn, requested_at=NOW)
    row = command(conn, legacy_id)

    assert row["payout_asset"] == "USDC_E"
    assert row["state"] == SettlementState.REDEEM_REVIEW_REQUIRED.value
    assert json.loads(row["error_payload"])["reason"] == "legacy_usdc_e_payout_requires_operator_review"


def test_request_redeem_backfills_existing_winning_index_set(conn, monkeypatch):
    """request_redeem() backfills a NULL winning_index_set on an existing row
    (was tested via the now-deleted harvester.enqueue_redeem_command wrapper;
    the backfill behavior itself lives in request_redeem, so it is re-tested
    calling request_redeem directly)."""
    from src.execution.settlement_commands import request_redeem

    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", FXClassification.TRADING_PNL_INFLOW.value)

    first_id = request_redeem(
        "condition-backfill-index-set",
        "pUSD",
        market_id="market-backfill-index-set",
        token_amounts={"yes-token": "1.5"},
        conn=conn,
        requested_at=NOW,
        winning_index_set=None,
    )
    assert command(conn, first_id)["winning_index_set"] is None

    second_id = request_redeem(
        "condition-backfill-index-set",
        "pUSD",
        market_id="market-backfill-index-set",
        token_amounts={"yes-token": "1.5"},
        conn=conn,
        requested_at=NOW,
        winning_index_set='["2"]',
    )

    assert second_id == first_id
    assert command(conn, first_id)["winning_index_set"] == '["2"]'
    assert "REDEEM_INDEX_SET_BACKFILLED" in states(conn, first_id)


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
