# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PR #(this PR) — 4th iteration Karachi antibody, logs[*].address inspection
"""Antibody tests: negRisk-misroute guard via logs[*].address PayoutRedemption scan.

Root cause (Karachi tx 0x0c85d9…, 4th iteration):
  receipt.to is NEVER the adapter in Polymarket relay-style submissions; it's the
  relay proxy (0x6a096d…).  The Standard CTF adapter address appears in
  logs[*].address for logs whose topic[0] == PayoutRedemption selector.
  Prior guard (PR #192) checked receipt.to == POLYGON_CTF_ADDRESS — always False.

Fix (4th iteration, settlement_commands.py reconcile_pending_redeems):
  Scan receipt["logs"] for entries with topic[0] == PayoutRedemption.
  Build adapter_addrs from those log entries' address fields.
  Fire antibody when: is_negrisk_market AND POLYGON_CTF_ADDRESS in adapter_addrs
                                        AND POLYGON_NEGRISK_ADAPTER_ADDRESS NOT in adapter_addrs.

Antibody contracts (sed-flip verifiable):
  T1 (Karachi pattern): negRisk + logs has Standard CTF + PayoutRedemption, NOT NegRiskAdapter
      → guard fires → row reset to REDEEM_OPERATOR_REQUIRED, tx_hash cleared.
  T2 (correct routing): negRisk + logs has NegRiskAdapter + PayoutRedemption
      → guard does NOT fire → row marks REDEEM_CONFIRMED.
  T3 (standard market): non-negRisk + logs has Standard CTF + PayoutRedemption
      → no antibody concern → row marks REDEEM_CONFIRMED.
  T4 (no PayoutRedemption topic): receipt has logs but none with PayoutRedemption topic
      → adapter_addrs empty → antibody does not fire → row marks REDEEM_CONFIRMED.
  T5 (sed-flip): comment out the antibody condition → T1 wrongly marks REDEEM_CONFIRMED → RED.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest

from src.state.db import init_schema
from src.execution.settlement_commands import (
    SettlementState,
    init_settlement_command_schema,
    request_redeem,
)
from src.venue.polymarket_v2_adapter import (
    POLYGON_CTF_ADDRESS,
    POLYGON_NEGRISK_ADAPTER_ADDRESS,
)

NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)

_NEGRISK_CONDITION_ID = "0xnegrisk_logs_" + "a" * 50
_STANDARD_CONDITION_ID = "0xstandard_logs" + "b" * 50
_TX_HASH_MISROUTED = "0x0c85d94640d33f382b1081b5141ecb5371c9faa363791d29df7d742434e9a560"
_TX_HASH_NEGRISK_OK = "0xnegrisk_ok_logs" + "0" * 47
_TX_HASH_STANDARD_OK = "0xstandard_ok_logs" + "1" * 46
_TX_HASH_NO_TOPIC = "0xno_payout_topic_" + "2" * 46

# PayoutRedemption event topic selector (keccak256 of event signature)
_PAYOUT_REDEMPTION_TOPIC = (
    "0x2682012a4a4f1973119f1c9b90745d1bd91fa2bab387344f044cb3586864d18d"
)

# Polymarket relay proxy — this is what receipt.to actually shows
_RELAY_PROXY_ADDRESS = "0x6a096d5042cba434521E2cdb95A1fBa789a09b7f"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def trade_conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    init_schema(db)
    init_settlement_command_schema(db)
    yield db
    db.close()


@pytest.fixture()
def world_tmp_db():
    """Temporary world DB with negRisk=1 for _NEGRISK_CONDITION_ID, negRisk=0 for standard."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        wconn = sqlite3.connect(path)
        wconn.execute(
            """
            CREATE TABLE executable_market_snapshots (
              snapshot_id TEXT PRIMARY KEY,
              condition_id TEXT NOT NULL,
              neg_risk INTEGER NOT NULL DEFAULT 0,
              captured_at TEXT NOT NULL DEFAULT (datetime('now')),
              freshness_deadline TEXT NOT NULL DEFAULT (datetime('now', '+1 day'))
            )
            """
        )
        wconn.execute(
            "INSERT INTO executable_market_snapshots VALUES (?,?,1,datetime('now'),datetime('now','+1 day'))",
            ("snap-nr-logs", _NEGRISK_CONDITION_ID),
        )
        wconn.execute(
            "INSERT INTO executable_market_snapshots VALUES (?,?,0,datetime('now'),datetime('now','+1 day'))",
            ("snap-std-logs", _STANDARD_CONDITION_ID),
        )
        wconn.commit()
        wconn.close()
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_tx_hashed(conn, condition_id: str, tx_hash: str) -> str:
    cmd_id = request_redeem(
        condition_id,
        "USDC",
        market_id=condition_id,
        token_amounts={"yes-token": "2.0"},
        winning_index_set='["2"]',
        conn=conn,
        requested_at=NOW,
    )
    conn.execute(
        "UPDATE settlement_commands SET state=?, tx_hash=? WHERE command_id=?",
        (SettlementState.REDEEM_TX_HASHED.value, tx_hash, cmd_id),
    )
    conn.commit()
    return cmd_id


def _make_web3_with_logs(tx_hash: str, status: int, logs: list[dict]) -> SimpleNamespace:
    """Mock web3 whose receipt.to is the relay proxy (NOT an adapter)."""
    receipt = {
        "status": status,
        "to": _RELAY_PROXY_ADDRESS,  # relay proxy — NOT the adapter
        "blockNumber": 200,
        "block_number": 200,
        "logs": logs,
    }
    eth_mock = SimpleNamespace(
        get_transaction_receipt=lambda _tx: receipt,
        block_number=205,
    )
    return SimpleNamespace(eth=eth_mock)


def _read_command(conn, cmd_id: str) -> sqlite3.Row:
    return conn.execute(
        "SELECT * FROM settlement_commands WHERE command_id=?", (cmd_id,)
    ).fetchone()


def _standard_ctf_log() -> dict:
    return {
        "address": POLYGON_CTF_ADDRESS,
        "topics": [_PAYOUT_REDEMPTION_TOPIC, "0x" + "aa" * 32],
        "data": "0x",
    }


def _negrisk_adapter_log() -> dict:
    return {
        "address": POLYGON_NEGRISK_ADAPTER_ADDRESS,
        "topics": [_PAYOUT_REDEMPTION_TOPIC, "0x" + "bb" * 32],
        "data": "0x",
    }


def _no_payout_topic_log() -> dict:
    return {
        "address": POLYGON_CTF_ADDRESS,
        "topics": ["0xdeadbeef" + "00" * 28],
        "data": "0x",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _patch_world_db(monkeypatch, world_tmp_db: str) -> None:
    """Patch both settlement_commands and state.db ZEUS_WORLD_DB_PATH to the tmp world DB."""
    import pathlib
    import src.state.db as _db_mod
    monkeypatch.setattr(
        "src.execution.settlement_commands.ZEUS_WORLD_DB_PATH",
        pathlib.Path(world_tmp_db),
        raising=False,
    )
    monkeypatch.setattr(
        _db_mod,
        "ZEUS_WORLD_DB_PATH",
        pathlib.Path(world_tmp_db),
        raising=False,
    )


def test_t1_negrisk_market_standard_ctf_in_logs_fires_antibody(
    trade_conn, world_tmp_db, monkeypatch
):
    """T1 (Karachi pattern): negRisk + PayoutRedemption emitted from Standard CTF (not NegRiskAdapter)
    → antibody fires → REDEEM_OPERATOR_REQUIRED, tx_hash cleared, error_payload REDEEM_NEGRISK_MISROUTED.

    This reproduces the exact on-chain structure of Karachi tx 0x0c85d9…:
    receipt.to = relay proxy, logs[1].address = Standard CTF + PayoutRedemption topic.
    """
    import src.execution.settlement_commands as sc

    _patch_world_db(monkeypatch, world_tmp_db)

    cmd_id = _insert_tx_hashed(trade_conn, _NEGRISK_CONDITION_ID, _TX_HASH_MISROUTED)
    web3 = _make_web3_with_logs(
        _TX_HASH_MISROUTED,
        status=1,
        logs=[
            {"address": "0xsomeirrelevantcontract", "topics": ["0xaabbcc"], "data": "0x"},
            _standard_ctf_log(),  # Standard CTF emitted PayoutRedemption
        ],
    )

    results = sc.reconcile_pending_redeems(web3, trade_conn)

    assert len(results) == 1, f"T1: expected 1 result, got {len(results)}"
    result = results[0]
    assert result.state == SettlementState.REDEEM_OPERATOR_REQUIRED, (
        f"T1: expected REDEEM_OPERATOR_REQUIRED, got {result.state}"
    )
    assert result.tx_hash is None, f"T1: tx_hash should be cleared, got {result.tx_hash}"
    assert result.error_payload is not None, "T1: error_payload must be set"
    assert result.error_payload.get("errorCode") == "REDEEM_NEGRISK_MISROUTED", (
        f"T1: wrong errorCode {result.error_payload}"
    )

    row = _read_command(trade_conn, cmd_id)
    assert row["state"] == SettlementState.REDEEM_OPERATOR_REQUIRED.value, (
        f"T1 DB: expected REDEEM_OPERATOR_REQUIRED, got {row['state']}"
    )
    assert row["tx_hash"] is None, f"T1 DB: tx_hash should be NULL, got {row['tx_hash']}"


def test_t2_negrisk_market_negrisk_adapter_in_logs_does_not_fire(
    trade_conn, world_tmp_db, monkeypatch
):
    """T2 (correct routing): negRisk + PayoutRedemption emitted from NegRiskAdapter
    → antibody does NOT fire → row marks REDEEM_CONFIRMED.
    """
    import src.execution.settlement_commands as sc

    _patch_world_db(monkeypatch, world_tmp_db)

    cmd_id = _insert_tx_hashed(trade_conn, _NEGRISK_CONDITION_ID, _TX_HASH_NEGRISK_OK)
    web3 = _make_web3_with_logs(
        _TX_HASH_NEGRISK_OK,
        status=1,
        logs=[_negrisk_adapter_log()],  # NegRiskAdapter emitted PayoutRedemption
    )

    results = sc.reconcile_pending_redeems(web3, trade_conn)

    assert len(results) == 1, f"T2: expected 1 result, got {len(results)}"
    result = results[0]
    assert result.state == SettlementState.REDEEM_CONFIRMED, (
        f"T2: expected REDEEM_CONFIRMED, got {result.state}"
    )

    row = _read_command(trade_conn, cmd_id)
    assert row["state"] == SettlementState.REDEEM_CONFIRMED.value, (
        f"T2 DB: expected REDEEM_CONFIRMED, got {row['state']}"
    )


def test_t3_standard_market_standard_ctf_in_logs_marks_confirmed(
    trade_conn, world_tmp_db, monkeypatch
):
    """T3 (standard market): non-negRisk market + Standard CTF in logs
    → no antibody concern → row marks REDEEM_CONFIRMED.
    """
    import src.execution.settlement_commands as sc

    _patch_world_db(monkeypatch, world_tmp_db)

    cmd_id = _insert_tx_hashed(trade_conn, _STANDARD_CONDITION_ID, _TX_HASH_STANDARD_OK)
    web3 = _make_web3_with_logs(
        _TX_HASH_STANDARD_OK,
        status=1,
        logs=[_standard_ctf_log()],  # Standard CTF is CORRECT for standard market
    )

    results = sc.reconcile_pending_redeems(web3, trade_conn)

    assert len(results) == 1, f"T3: expected 1 result, got {len(results)}"
    result = results[0]
    assert result.state == SettlementState.REDEEM_CONFIRMED, (
        f"T3: expected REDEEM_CONFIRMED, got {result.state}"
    )

    row = _read_command(trade_conn, cmd_id)
    assert row["state"] == SettlementState.REDEEM_CONFIRMED.value, (
        f"T3 DB: expected REDEEM_CONFIRMED, got {row['state']}"
    )


def test_t4_no_payout_redemption_topic_does_not_fire_antibody(
    trade_conn, world_tmp_db, monkeypatch
):
    """T4 (no PayoutRedemption topic): logs present but none with PayoutRedemption topic
    → adapter_addrs empty → antibody does not fire → REDEEM_CONFIRMED.
    """
    import src.execution.settlement_commands as sc

    _patch_world_db(monkeypatch, world_tmp_db)

    cmd_id = _insert_tx_hashed(trade_conn, _NEGRISK_CONDITION_ID, _TX_HASH_NO_TOPIC)
    web3 = _make_web3_with_logs(
        _TX_HASH_NO_TOPIC,
        status=1,
        logs=[_no_payout_topic_log()],  # Standard CTF address but wrong topic
    )

    results = sc.reconcile_pending_redeems(web3, trade_conn)

    assert len(results) == 1, f"T4: expected 1 result, got {len(results)}"
    result = results[0]
    assert result.state == SettlementState.REDEEM_CONFIRMED, (
        f"T4: expected REDEEM_CONFIRMED (no PayoutRedemption → no antibody), got {result.state}"
    )

    row = _read_command(trade_conn, cmd_id)
    assert row["state"] == SettlementState.REDEEM_CONFIRMED.value, (
        f"T4 DB: expected REDEEM_CONFIRMED, got {row['state']}"
    )


@pytest.mark.xfail(
    reason="sed-flip: comment out the antibody condition → T1 wrongly marks REDEEM_CONFIRMED",
    strict=True,
)
def test_t5_sed_flip_antibody_condition_removed_causes_t1_to_fail(
    trade_conn, world_tmp_db, monkeypatch
):
    """T5 (sed-flip sentinel): demonstrates that T1 goes RED when the antibody is removed.

    How to verify manually:
      Comment out the antibody guard in reconcile_pending_redeems:
        # if is_negrisk_market and routed_to_standard_ctf and not routed_to_neg_risk_adapter:
      Then run this test — it will XPASS (which strict=True turns to FAIL).

    The xfail marker documents the antibody's detection capability without requiring
    the test suite to actually modify production code.
    """
    import src.execution.settlement_commands as sc

    _patch_world_db(monkeypatch, world_tmp_db)

    cmd_id = _insert_tx_hashed(trade_conn, _NEGRISK_CONDITION_ID, _TX_HASH_MISROUTED)
    web3 = _make_web3_with_logs(
        _TX_HASH_MISROUTED,
        status=1,
        logs=[_standard_ctf_log()],
    )

    results = sc.reconcile_pending_redeems(web3, trade_conn)

    # If the antibody is working (normal run), this assertion will FAIL (expected by xfail).
    # If antibody is removed (sed-flip), this assertion will PASS → test becomes XPASS → strict
    # turns it RED, making the broken antibody visible.
    assert len(results) == 1
    result = results[0]
    assert result.state == SettlementState.REDEEM_CONFIRMED, (
        "Antibody is active — T5 correctly fails (xfail expected)"
    )
