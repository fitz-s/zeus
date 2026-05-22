# Created: 2026-05-19
# Last reused or audited: 2026-05-22
# Authority basis: codereview-may19-2.md P1-2
"""Antibody: NegRisk redeem payout proof — route presence is not enough.

P1-2 (codereview-may19-2.md): the previous implementation confirmed a negRisk
redeem as correct if the NegRiskAdapter address appeared as a log emitter
ANYWHERE in the receipt.  That proves the call path but NOT:

  - that the log's conditionId matches the command row's condition_id
  - that the payout amount is nonzero
  - that the payout amount matches token_amounts_json
  - that the winning slot is correct

This file tests the three cases required by the spec:

  T1: receipt contains NegRiskAdapter log for an UNRELATED condition +
      Standard CTF log for the target condition
      → must classify REVIEW_REQUIRED or REDEEM_NEGRISK_WRONG_CONDITION
      (not CONFIRMED; the adapter did not pay out this condition)

  T2: receipt contains NegRiskAdapter log for target condition but
      payout word in data == 0
      → must NOT confirm (REDEEM_NEGRISK_ZERO_PAYOUT or REVIEW_REQUIRED)

  T3: receipt contains NegRiskAdapter log for target condition with
      payout > 0 and matching amount
      → REDEEM_CONFIRMED

Sed-flip targets:
  T1: strip the condition_id check (accept any NegRisk log) → T1 fails
  T2: strip the payout>0 check → T2 confirms incorrectly
  T3: strip the whole proof block → T3 may still pass but T1/T2 fail

NegRiskAdapter PayoutRedemption ABI (NegRiskAdapter.sol INegRiskAdapterEE):
  event PayoutRedemption(
    address indexed redeemer,
    bytes32 indexed conditionId,
    uint256[] amounts,
    uint256 payout
  )
  topics[0] = 0x9140a6a270ef945260c03894b3c6b3b2695e9d5101feef0ff24fec960cfd3224
  topics[1] = redeemer (32-byte padded address)
  topics[2] = conditionId (indexed bytes32)
  data      = ABI(uint256[] amounts, uint256 payout):
    word0 (bytes 0-31):  offset to amounts array (0x40)
    word1 (bytes 32-63): payout (second word)
    word2 (bytes 64-95): array length
    word3+ (bytes 96+):  array items
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from src.execution.settlement_commands import (
    SettlementState,
    ensure_settlement_schema_ready,
    reconcile_pending_redeems,
)
from src.state.db import init_schema
from src.venue.polymarket_v2_adapter import (
    POLYGON_CTF_ADDRESS,
    POLYGON_NEGRISK_ADAPTER_ADDRESS,
)

NOW = datetime(2026, 5, 19, 22, 0, 0, tzinfo=timezone.utc)

TARGET_COND = "0xaaaa0000000000000000000000000000000000000000000000000000000000bb"
OTHER_COND  = "0xbbbb0000000000000000000000000000000000000000000000000000000000cc"
TARGET_TX   = "0x" + "ab" * 32

_NEGRISK_REDEMPTION_TOPIC = (
    "0x9140a6a270ef945260c03894b3c6b3b2695e9d5101feef0ff24fec960cfd3224"
)
_PAYOUT_REDEMPTION_TOPIC = (
    "0x2682012a4a4f1973119f1c9b90745d1bd91fa2bab387344f044cb3586864d18d"
)
_REDEEMER_TOPIC = "0x000000000000000000000000b19ce122089237025ad046a0ea61e66a5fa4cc8b"

_PAYOUT_MICRO = 1_000_000  # 1 USDC in micro-units


def _abi_encode_payout(payout: int) -> str:
    """ABI-encode (uint256[] amounts, uint256 payout) with a single-element amounts array."""
    w0 = "0000000000000000000000000000000000000000000000000000000000000040"  # offset=64
    w1 = f"{payout:064x}"                                                    # payout (2nd word)
    w2 = "0000000000000000000000000000000000000000000000000000000000000001"  # len=1
    w3 = f"{payout:064x}"                                                    # amounts[0]
    return "0x" + w0 + w1 + w2 + w3


@pytest.fixture()
def conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    init_schema(db)
    ensure_settlement_schema_ready(db)
    yield db
    db.close()


def _patch_negrisk_lookup(monkeypatch, neg_risk_by_cond: dict):
    import src.execution.settlement_commands as sc
    def _fake(conn, condition_id):  # noqa: ARG001
        return neg_risk_by_cond.get(condition_id)
    monkeypatch.setattr(sc, "_lookup_market_neg_risk_authoritative", _fake)


def _seed_tx_hashed(conn, command_id: str, condition_id: str, tx_hash: str,
                    token_amounts_json: str | None = None,
                    state: str = SettlementState.REDEEM_TX_HASHED.value,
                    error_payload: dict | None = None) -> None:
    conn.execute(
        """
        INSERT INTO settlement_commands
          (command_id, state, condition_id, market_id, payout_asset,
           requested_at, tx_hash, token_amounts_json, error_payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            command_id,
            state,
            condition_id,
            condition_id,
            "USDC",
            NOW.isoformat(),
            tx_hash,
            token_amounts_json,
            json.dumps(error_payload, sort_keys=True) if error_payload else None,
        ),
    )
    conn.commit()


class _StubWeb3:
    def __init__(self, receipt):
        self._receipt = receipt
        self.eth = self

    def get_transaction_receipt(self, tx_hash):  # noqa: ARG002
        return self._receipt

    @property
    def block_number(self):
        return self._receipt.get("blockNumber", 87000000) + 12


class _ReceiptMapWeb3:
    def __init__(self, receipts_by_hash):
        self._receipts_by_hash = receipts_by_hash
        self.eth = self

    def get_transaction_receipt(self, tx_hash):
        return self._receipts_by_hash.get(tx_hash)

    @property
    def block_number(self):
        return 87200020


def test_t1_unrelated_condition_not_confirmed(conn, monkeypatch):
    """T1: NegRiskAdapter log is for a DIFFERENT condition_id; Standard CTF
    log is present for target condition but that's the wrong adapter.
    Must NOT confirm; must classify REVIEW_REQUIRED or WRONG_CONDITION."""
    _patch_negrisk_lookup(monkeypatch, {TARGET_COND: True})
    _seed_tx_hashed(conn, "t1-cmd", TARGET_COND, TARGET_TX)

    receipt = {
        "status": 1,
        "transactionHash": TARGET_TX,
        "blockNumber": 87200000,
        "logs": [
            {
                # Standard CTF log for target condition (wrong path — not adapter)
                "address": POLYGON_CTF_ADDRESS.lower(),
                "topics": [_PAYOUT_REDEMPTION_TOPIC],
                "data": "0x",
            },
            {
                # NegRiskAdapter log but for a DIFFERENT condition (unrelated activity)
                "address": POLYGON_NEGRISK_ADAPTER_ADDRESS.lower(),
                "topics": [_NEGRISK_REDEMPTION_TOPIC, _REDEEMER_TOPIC, OTHER_COND],
                "data": _abi_encode_payout(_PAYOUT_MICRO),
            },
        ],
    }
    reconcile_pending_redeems(_StubWeb3(receipt), conn)

    row = conn.execute(
        "SELECT state, error_payload FROM settlement_commands WHERE command_id = ?",
        ("t1-cmd",),
    ).fetchone()
    assert row["state"] != SettlementState.REDEEM_CONFIRMED.value, (
        f"T1 FAIL: unrelated-condition adapter log was accepted as proof. "
        f"state={row['state']!r}; expected REVIEW_REQUIRED or WRONG_CONDITION"
    )
    payload = json.loads(row["error_payload"]) if row["error_payload"] else {}
    assert payload.get("errorCode") in {
        "REDEEM_NEGRISK_WRONG_CONDITION",
        "REDEEM_NEGRISK_REVIEW_REQUIRED",
    }, f"T1 FAIL: unexpected errorCode={payload.get('errorCode')!r}"


def test_t2_zero_payout_not_confirmed(conn, monkeypatch):
    """T2: NegRiskAdapter log is for the correct condition but payout word = 0.
    Must NOT confirm."""
    _patch_negrisk_lookup(monkeypatch, {TARGET_COND: True})
    _seed_tx_hashed(conn, "t2-cmd", TARGET_COND, TARGET_TX)

    receipt = {
        "status": 1,
        "transactionHash": TARGET_TX,
        "blockNumber": 87200001,
        "logs": [
            {
                "address": POLYGON_NEGRISK_ADAPTER_ADDRESS.lower(),
                "topics": [_NEGRISK_REDEMPTION_TOPIC, _REDEEMER_TOPIC, TARGET_COND],
                "data": _abi_encode_payout(0),  # zero payout
            },
        ],
    }
    reconcile_pending_redeems(_StubWeb3(receipt), conn)

    row = conn.execute(
        "SELECT state, error_payload FROM settlement_commands WHERE command_id = ?",
        ("t2-cmd",),
    ).fetchone()
    assert row["state"] != SettlementState.REDEEM_CONFIRMED.value, (
        f"T2 FAIL: zero-payout log was accepted as proof. state={row['state']!r}"
    )
    payload = json.loads(row["error_payload"]) if row["error_payload"] else {}
    assert payload.get("errorCode") in {
        "REDEEM_NEGRISK_ZERO_PAYOUT",
        "REDEEM_NEGRISK_REVIEW_REQUIRED",
    }, f"T2 FAIL: unexpected errorCode={payload.get('errorCode')!r}"


def test_t3_correct_payout_confirmed(conn, monkeypatch):
    """T3: NegRiskAdapter log for target condition with payout > 0 and
    no token_amounts_json to cross-check → REDEEM_CONFIRMED."""
    _patch_negrisk_lookup(monkeypatch, {TARGET_COND: True})
    _seed_tx_hashed(conn, "t3-cmd", TARGET_COND, TARGET_TX)

    receipt = {
        "status": 1,
        "transactionHash": TARGET_TX,
        "blockNumber": 87200002,
        "logs": [
            {
                # NegRiskAdapter internally calls Standard CTF — present but not the proof
                "address": POLYGON_CTF_ADDRESS.lower(),
                "topics": [_PAYOUT_REDEMPTION_TOPIC],
                "data": "0x",
            },
            {
                # NegRiskAdapter PayoutRedemption with correct condition + payout
                "address": POLYGON_NEGRISK_ADAPTER_ADDRESS.lower(),
                "topics": [_NEGRISK_REDEMPTION_TOPIC, _REDEEMER_TOPIC, TARGET_COND],
                "data": _abi_encode_payout(_PAYOUT_MICRO),
            },
        ],
    }
    reconcile_pending_redeems(_StubWeb3(receipt), conn)

    row = conn.execute(
        "SELECT state FROM settlement_commands WHERE command_id = ?",
        ("t3-cmd",),
    ).fetchone()
    assert row["state"] == SettlementState.REDEEM_CONFIRMED.value, (
        f"T3 FAIL: correct NegRisk payout proof did not confirm. "
        f"state={row['state']!r}"
    )


def test_recheckable_amount_mismatch_review_with_correct_payout_confirms(conn, monkeypatch):
    """A previous parser can leave a valid redeem terminally parked in
    REDEEM_REVIEW_REQUIRED. If the stored tx_hash now proves the exact
    NegRisk payout, reconcile must heal that row instead of requiring a
    manual force transition.
    """
    _patch_negrisk_lookup(monkeypatch, {TARGET_COND: True})
    _seed_tx_hashed(
        conn,
        "review-cmd",
        TARGET_COND,
        TARGET_TX,
        token_amounts_json=json.dumps({"winning-position": 1.0}),
        state=SettlementState.REDEEM_REVIEW_REQUIRED.value,
        error_payload={
            "errorCode": "REDEEM_NEGRISK_AMOUNT_MISMATCH",
            "payout_from_receipt": 2**255,
            "expected_amount_per_slot": _PAYOUT_MICRO,
        },
    )

    receipt = {
        "status": 1,
        "transactionHash": TARGET_TX,
        "blockNumber": 87200003,
        "logs": [
            {
                "address": POLYGON_NEGRISK_ADAPTER_ADDRESS.lower(),
                "topics": [_NEGRISK_REDEMPTION_TOPIC, _REDEEMER_TOPIC, TARGET_COND],
                "data": _abi_encode_payout(_PAYOUT_MICRO),
            },
        ],
    }
    results = reconcile_pending_redeems(_StubWeb3(receipt), conn)

    row = conn.execute(
        "SELECT state, error_payload FROM settlement_commands WHERE command_id = ?",
        ("review-cmd",),
    ).fetchone()
    assert len(results) == 1
    assert results[0].state == SettlementState.REDEEM_CONFIRMED
    assert results[0].error_payload is None
    assert row["state"] == SettlementState.REDEEM_CONFIRMED.value
    assert row["error_payload"] is None


def test_non_recheckable_review_rows_do_not_starve_tx_hashed_redeems(conn, monkeypatch):
    """Non-healable review rows must not fill the batch window ahead of
    ordinary tx-hashed rows. The SQL candidate set filters review rows before
    LIMIT so a tiny cap still reaches the actionable redeem.
    """
    monkeypatch.setenv("ZEUS_REDEEM_RECONCILE_BATCH_CAP", "1")
    _patch_negrisk_lookup(monkeypatch, {TARGET_COND: True})
    _seed_tx_hashed(
        conn,
        "aaa-review-cmd",
        TARGET_COND,
        "0x" + "aa" * 32,
        state=SettlementState.REDEEM_REVIEW_REQUIRED.value,
        error_payload={"errorCode": "REDEEM_OPERATOR_ACTION_REQUIRED"},
    )
    _seed_tx_hashed(conn, "zzz-tx-cmd", TARGET_COND, TARGET_TX)

    receipt = {
        "status": 1,
        "transactionHash": TARGET_TX,
        "blockNumber": 87200004,
        "logs": [
            {
                "address": POLYGON_NEGRISK_ADAPTER_ADDRESS.lower(),
                "topics": [_NEGRISK_REDEMPTION_TOPIC, _REDEEMER_TOPIC, TARGET_COND],
                "data": _abi_encode_payout(_PAYOUT_MICRO),
            },
        ],
    }
    results = reconcile_pending_redeems(_StubWeb3(receipt), conn)

    review_row = conn.execute(
        "SELECT state FROM settlement_commands WHERE command_id = ?",
        ("aaa-review-cmd",),
    ).fetchone()
    tx_row = conn.execute(
        "SELECT state FROM settlement_commands WHERE command_id = ?",
        ("zzz-tx-cmd",),
    ).fetchone()
    assert [result.command_id for result in results] == ["zzz-tx-cmd"]
    assert review_row["state"] == SettlementState.REDEEM_REVIEW_REQUIRED.value
    assert tx_row["state"] == SettlementState.REDEEM_CONFIRMED.value


def test_recheckable_review_rows_do_not_starve_tx_hashed_redeems(conn, monkeypatch):
    monkeypatch.setenv("ZEUS_REDEEM_RECONCILE_BATCH_CAP", "1")
    _patch_negrisk_lookup(monkeypatch, {TARGET_COND: True})
    _seed_tx_hashed(
        conn,
        "aaa-review-cmd",
        TARGET_COND,
        "0x" + "aa" * 32,
        state=SettlementState.REDEEM_REVIEW_REQUIRED.value,
        error_payload={"errorCode": "REDEEM_NEGRISK_AMOUNT_MISMATCH"},
    )
    _seed_tx_hashed(conn, "zzz-tx-cmd", TARGET_COND, TARGET_TX)

    receipt = {
        "status": 1,
        "transactionHash": TARGET_TX,
        "blockNumber": 87200004,
        "logs": [
            {
                "address": POLYGON_NEGRISK_ADAPTER_ADDRESS.lower(),
                "topics": [_NEGRISK_REDEMPTION_TOPIC, _REDEEMER_TOPIC, TARGET_COND],
                "data": _abi_encode_payout(_PAYOUT_MICRO),
            },
        ],
    }
    results = reconcile_pending_redeems(_ReceiptMapWeb3({TARGET_TX: receipt}), conn)

    review_row = conn.execute(
        "SELECT state FROM settlement_commands WHERE command_id = ?",
        ("aaa-review-cmd",),
    ).fetchone()
    tx_row = conn.execute(
        "SELECT state FROM settlement_commands WHERE command_id = ?",
        ("zzz-tx-cmd",),
    ).fetchone()
    assert [result.command_id for result in results] == ["zzz-tx-cmd"]
    assert review_row["state"] == SettlementState.REDEEM_REVIEW_REQUIRED.value
    assert tx_row["state"] == SettlementState.REDEEM_CONFIRMED.value
