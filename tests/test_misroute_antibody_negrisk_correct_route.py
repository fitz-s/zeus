# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: .omc/plans/2026-05-19-kill-switch-and-misroute-antibody-fix.md (defect #2)
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Antibody — misroute detection must NOT false-positive when a negRisk
#          redeem is correctly routed through NegRiskAdapter and the
#          NegRiskAdapter internally calls Standard CTF (causing Standard CTF
#          to emit PayoutRedemption). Detection key is "did NegRiskAdapter
#          address appear as a log emitter", not "is Standard CTF in logs".
"""Antibody: NegRisk redeem with correct routing must transition to CONFIRMED.

Root cause (2026-05-19 Karachi GS013 retry loop): the 4th-iteration misroute
antibody checked which addresses emitted `PayoutRedemption`
(topic 0x2682012a...). NegRiskAdapter does NOT emit that topic; it emits
its own redemption event (topic 0x9140a6a270ef945260c03894b3c6b3b2695e9d5101feef0ff24fec960cfd3224).
NegRiskAdapter internally calls Standard CTF to move underlying positions,
so Standard CTF emits PayoutRedemption EVEN WHEN routing is correct.

Karachi tx 0xe08e03334f25... block 87135584:
  - log[7] address=0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296 (NegRiskAdapter),
    topics[0]=0x9140a6a... (NegRiskAdapter's custom event)
  - log[4] address=0x4D97DCd97eC945f40cF65F87097ACe5EA0476045 (Standard CTF),
    topics[0]=0x2682012a... (PayoutRedemption from Standard CTF)
The 4th-iter check saw Standard CTF in PayoutRedemption emitters AND
NegRiskAdapter NOT in PayoutRedemption emitters → MISROUTED → reseat →
GS013 retry (position already redeemed).

Fix: for negRisk markets, the route is correct iff NegRiskAdapter address
appears as a log emitter anywhere in receipt.logs. Its presence proves the
contract was called.

Antibody contracts (sed-flip verifiable):
  C1: NegRiskAdapter emits its custom event (correct Karachi-shape receipt)
      → state advances to REDEEM_CONFIRMED, NOT OPERATOR_REQUIRED.
  C2: NegRiskAdapter not in logs (true misroute — tx went directly to
      Standard CTF) → state goes to OPERATOR_REQUIRED with errorCode=
      REDEEM_NEGRISK_MISROUTED. The real misroute detection still works.
  C3: Standard CTF market (non-negRisk) routed correctly → CONFIRMED.

Sed-flip: revert detection to require NegRiskAdapter on PayoutRedemption
topic only → C1 fails (false positive on the correct Karachi case).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.execution.settlement_commands import (
    SettlementState,
    init_settlement_command_schema,
    reconcile_pending_redeems,
    request_redeem,
)
from src.state.db import init_schema
from src.venue.polymarket_v2_adapter import (
    POLYGON_CTF_ADDRESS,
    POLYGON_NEGRISK_ADAPTER_ADDRESS,
)


NOW = datetime(2026, 5, 19, 22, 0, 0, tzinfo=timezone.utc)

KARACHI_COND = "0xc5faddf4810e0c14659dbdf170599dcb8304ef42afcccb84992b4d8fcb0f44ae"
KARACHI_TX = "0xe08e03334f25328d3c993fb7e7e266d732edcaa02532f2d9ce3ca5feec38d74f"

_PAYOUT_REDEMPTION_TOPIC = (
    "0x2682012a4a4f1973119f1c9b90745d1bd91fa2bab387344f044cb3586864d18d"
)
_NEGRISK_REDEMPTION_TOPIC = (
    "0x9140a6a270ef945260c03894b3c6b3b2695e9d5101feef0ff24fec960cfd3224"
)


@pytest.fixture()
def conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    init_schema(db)
    init_settlement_command_schema(db)
    yield db
    db.close()


def _patch_negrisk_lookup(monkeypatch, neg_risk_by_cond: dict[str, bool]):
    """Replace the 3-tier negRisk lookup with a dict lookup. Avoids needing
    world.db attach or Gamma HTTP at test time."""
    import src.execution.settlement_commands as sc

    def _fake_lookup(conn, condition_id):  # noqa: ARG001
        return neg_risk_by_cond.get(condition_id)

    monkeypatch.setattr(sc, "_lookup_market_neg_risk_authoritative", _fake_lookup)


def _seed_tx_hashed_command(conn, command_id: str, condition_id: str, tx_hash: str):
    """Directly seed a settlement_commands row in REDEEM_TX_HASHED state.
    Bypasses request_redeem() because we want a deterministic command_id."""
    conn.execute(
        """
        INSERT INTO settlement_commands
          (command_id, state, condition_id, market_id, payout_asset, requested_at, tx_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            command_id,
            SettlementState.REDEEM_TX_HASHED.value,
            condition_id,
            condition_id,
            "USDC",
            NOW.isoformat(),
            tx_hash,
        ),
    )
    conn.commit()


class _StubWeb3:
    """Minimal web3 stub returning a hardcoded receipt for the given tx_hash."""

    def __init__(self, receipt):
        self._receipt = receipt
        self.eth = self

    def get_transaction_receipt(self, tx_hash):  # noqa: ARG002
        return self._receipt

    @property
    def block_number(self):
        return self._receipt["blockNumber"] + 12


def _karachi_correct_receipt():
    """Karachi-shape receipt: routes through NegRiskAdapter (log[7]) AND
    Standard CTF emits PayoutRedemption (log[4]) because NegRiskAdapter
    internally calls Standard CTF."""
    return {
        "status": 1,
        "transactionHash": KARACHI_TX,
        "blockNumber": 87135584,
        "blockHash": "0x7b6ba43c5807e7633f18723b102815cb9ea009f0078650e91e6ae92ac2ef1199",
        "from": "0xB19Ce122089237025aD046a0eA61E66a5Fa4cc8b",
        "to": "0x6a096d5042cba434521E2cdb95A1fBa789a09b7f",
        "logs": [
            {
                # log[0]: Safe ExecutionSuccess
                "address": "0x6a096d5042cba434521e2cdb95a1fba789a09b7f",
                "topics": ["0x66753cd2356569ee081232e3be8909b950e0a76c1f8460c3a5e3c2be32b11bed"],
            },
            {
                # log[4]: Standard CTF emits PayoutRedemption
                # (because NegRiskAdapter internally called it)
                "address": POLYGON_CTF_ADDRESS.lower(),
                "topics": [_PAYOUT_REDEMPTION_TOPIC],
            },
            {
                # log[7]: NegRiskAdapter emits its OWN redemption event
                "address": POLYGON_NEGRISK_ADAPTER_ADDRESS.lower(),
                "topics": [_NEGRISK_REDEMPTION_TOPIC],
            },
        ],
    }


def _true_misroute_receipt():
    """Tx went directly to Standard CTF without NegRiskAdapter — true misroute."""
    return {
        "status": 1,
        "transactionHash": "0xMISROUTE",
        "blockNumber": 87135584,
        "logs": [
            {
                "address": POLYGON_CTF_ADDRESS.lower(),
                "topics": [_PAYOUT_REDEMPTION_TOPIC],
            },
        ],
    }


def test_c1_negrisk_correct_route_transitions_to_confirmed(conn, monkeypatch):
    """C1: Karachi-shape receipt (NegRiskAdapter address in logs + Standard
    CTF emits PayoutRedemption) MUST advance to REDEEM_CONFIRMED, not flag
    as MISROUTED. Sed-flip target — 4th-iter logic regresses this to
    OPERATOR_REQUIRED."""
    _patch_negrisk_lookup(monkeypatch, {KARACHI_COND: True})
    _seed_tx_hashed_command(conn, "karachi-c1", KARACHI_COND, KARACHI_TX)
    web3 = _StubWeb3(_karachi_correct_receipt())

    results = reconcile_pending_redeems(web3, conn)

    row = conn.execute(
        "SELECT state FROM settlement_commands WHERE command_id = ?",
        ("karachi-c1",),
    ).fetchone()
    assert row["state"] == SettlementState.REDEEM_CONFIRMED.value, (
        f"C1 antibody FAIL: state={row['state']!r}; expected REDEEM_CONFIRMED. "
        f"Karachi correct-routing case is being false-positive MISROUTED again. "
        f"results={[r.state for r in results]}"
    )


def test_c2_true_misroute_still_caught(conn, monkeypatch):
    """C2: a tx that went DIRECTLY to Standard CTF (no NegRiskAdapter in
    logs) on a negRisk market MUST still be flagged as MISROUTED. The fix
    must not weaken the real misroute detection."""
    _patch_negrisk_lookup(monkeypatch, {KARACHI_COND: True})
    _seed_tx_hashed_command(conn, "true-misroute", KARACHI_COND, "0xMISROUTE")
    web3 = _StubWeb3(_true_misroute_receipt())

    reconcile_pending_redeems(web3, conn)

    row = conn.execute(
        "SELECT state, error_payload FROM settlement_commands WHERE command_id = ?",
        ("true-misroute",),
    ).fetchone()
    assert row["state"] == SettlementState.REDEEM_OPERATOR_REQUIRED.value, (
        f"C2 FAIL: a true misroute (no NegRiskAdapter in logs at all) was "
        f"NOT caught. state={row['state']!r}; expected OPERATOR_REQUIRED. "
        f"The fix has over-permissioned the detection."
    )
    import json
    payload = json.loads(row["error_payload"]) if row["error_payload"] else {}
    assert payload.get("errorCode") == "REDEEM_NEGRISK_MISROUTED"


def test_c3_standard_ctf_market_correct_route_confirms(conn, monkeypatch):
    """C3: a NON-negRisk market (Standard CTF only) routed correctly through
    Standard CTF must transition to CONFIRMED. The misroute path is gated by
    `is_negrisk_market`."""
    cond = "0x" + "ab" * 32
    _patch_negrisk_lookup(monkeypatch, {cond: False})
    _seed_tx_hashed_command(conn, "stdctf-c3", cond, "0xSTDCTF")
    web3 = _StubWeb3({
        "status": 1,
        "transactionHash": "0xSTDCTF",
        "blockNumber": 87135500,
        "logs": [
            {
                "address": POLYGON_CTF_ADDRESS.lower(),
                "topics": [_PAYOUT_REDEMPTION_TOPIC],
            },
        ],
    })

    reconcile_pending_redeems(web3, conn)

    row = conn.execute(
        "SELECT state FROM settlement_commands WHERE command_id = ?",
        ("stdctf-c3",),
    ).fetchone()
    assert row["state"] == SettlementState.REDEEM_CONFIRMED.value, (
        f"C3 FAIL: standard-CTF market with correct routing went to "
        f"{row['state']!r}; expected CONFIRMED. The misroute path should "
        f"only gate on negRisk markets."
    )
