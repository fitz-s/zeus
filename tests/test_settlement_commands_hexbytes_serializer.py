# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: live reconcile_pending_redeems HexBytes serializer fix (2026-05-19 alpha-loss postmortem)
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Antibody tests — _json_dumps / _jsonable HexBytes serialization fix.
# Reuse: Run when modifying _json_dumps, _jsonable, or reconcile_pending_redeems
#         in settlement_commands.py.
"""Antibody tests: _json_dumps handles HexBytes/bytes from web3 receipts.

Root cause (PR #196 alpha-loss): reconcile_pending_redeems calls _append_event and
_transition with receipt_payload dicts containing HexBytes (blockHash, transactionHash,
logsBloom, logs[].topics etc.) from web3.eth.get_transaction_receipt.  Prior to this
fix, _json_dumps had no custom encoder, causing TypeError: Object of type HexBytes is not
JSON serializable, leaving all REDEEM_TX_HASHED rows stuck indefinitely.

Antibody contracts:
  T1: HexBytes in top-level field → serializes with "0xabcd" substring.
  T2: raw bytes (non-HexBytes) in top-level field → serializes to hex-encoded form.
  T3: Nested structure with HexBytes in logs list → serializes without error.
  T4: Round-trip — fake receipt with HexBytes fields + negRisk market, run
      reconcile_pending_redeems, assert row transitions to REDEEM_OPERATOR_REQUIRED.
      (Sed-flip: removing default=_jsonable causes this test → RED.)
  T5: Unsupported type (object()) still raises TypeError — no silent corruption.
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.execution.settlement_commands import (
    SettlementState,
    _json_dumps,
    _jsonable,  # noqa: F401 — importability contract; antibody fails if symbol disappears
    init_settlement_command_schema,
    reconcile_pending_redeems,
    request_redeem,
)
from src.state.db import init_schema
from src.venue.polymarket_v2_adapter import (
    POLYGON_CTF_ADDRESS,
    POLYGON_NEGRISK_ADAPTER_ADDRESS,
)

# ---------------------------------------------------------------------------
# HexBytes shim — web3 is not a test dependency; simulate the type faithfully.
# HexBytes(b'\xab\xcd') should behave like bytes but .hex() returns 'abcd'.
# ---------------------------------------------------------------------------

class HexBytes(bytes):
    """Minimal HexBytes shim matching web3.types.HexBytes behaviour."""

    def __new__(cls, value):
        if isinstance(value, str):
            # Accept "0xabcd" or "abcd"
            stripped = value[2:] if value.startswith("0x") else value
            return super().__new__(cls, bytes.fromhex(stripped))
        return super().__new__(cls, value)

    def hex(self) -> str:  # type: ignore[override]
        return super().hex()


# ---------------------------------------------------------------------------
# T1: HexBytes in top-level dict field
# ---------------------------------------------------------------------------

def test_json_dumps_hexbytes_top_level():
    """T1: HexBytes value serializes to a string containing the hex data."""
    result = _json_dumps({"hash": HexBytes("0xabcd")})
    assert isinstance(result, str), "T1 FAIL: result must be a string"
    parsed = json.loads(result)
    assert "0xabcd" in parsed["hash"], (
        f"T1 FAIL: expected '0xabcd' in {parsed['hash']!r}"
    )


# ---------------------------------------------------------------------------
# T2: raw bytes (non-HexBytes) in top-level field
# ---------------------------------------------------------------------------

def test_json_dumps_raw_bytes_top_level():
    """T2: Plain bytes serialize to hex form (e.g. 0x010203)."""
    result = _json_dumps({"raw": b"\x01\x02\x03"})
    assert isinstance(result, str), "T2 FAIL: result must be a string"
    parsed = json.loads(result)
    hex_val = parsed["raw"]
    # Must be a hex string containing the byte values
    assert "010203" in hex_val, (
        f"T2 FAIL: expected '010203' in {hex_val!r}"
    )


# ---------------------------------------------------------------------------
# T3: Nested structure with HexBytes in logs list
# ---------------------------------------------------------------------------

def test_json_dumps_nested_hexbytes_in_logs():
    """T3: Deeply nested HexBytes (receipt logs[].blockHash pattern) serializes."""
    receipt = {
        "receipt": {
            "blockHash": HexBytes("0xabcd"),
            "transactionHash": HexBytes("0xdeadbeef"),
            "logsBloom": HexBytes(b"\xff" * 4),
            "logs": [
                {
                    "blockHash": HexBytes("0xabcd"),
                    "topics": [HexBytes("0x1234"), HexBytes("0x5678")],
                }
            ],
        }
    }
    result = _json_dumps(receipt)  # must not raise
    assert isinstance(result, str), "T3 FAIL: result must be a string"
    parsed = json.loads(result)
    assert isinstance(parsed["receipt"]["logs"][0]["topics"][0], str), (
        "T3 FAIL: topics[0] must be a string after deserialization"
    )


# ---------------------------------------------------------------------------
# T4: Full round-trip via reconcile_pending_redeems with HexBytes receipt
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)
_NEGRISK_CONDITION_ID = "0x" + "ab" * 32


@pytest.fixture()
def trade_conn_t4():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    init_schema(db)
    init_settlement_command_schema(db)
    yield db
    db.close()


@pytest.fixture()
def world_db_t4():
    """Temporary world DB with a negRisk=1 entry for _NEGRISK_CONDITION_ID."""
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
            "INSERT INTO executable_market_snapshots "
            "(snapshot_id, condition_id, neg_risk) VALUES (?, ?, 1)",
            ("snap-hexbytes-t4", _NEGRISK_CONDITION_ID),
        )
        wconn.commit()
        wconn.close()
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _make_hexbytes_receipt(tx_hash: str, status: int, to_address: str) -> dict:
    """Build a fake receipt with HexBytes fields as web3 would return."""
    tx_bytes = HexBytes(tx_hash) if tx_hash.startswith("0x") else HexBytes("0x" + tx_hash)
    return {
        "status": status,
        "to": to_address,
        "blockNumber": 200,
        "block_number": 200,
        "blockHash": HexBytes("0xaabbccdd"),
        "transactionHash": tx_bytes,
        "logsBloom": HexBytes(b"\x00" * 8),
        "logs": [
            {
                "blockHash": HexBytes("0xaabbccdd"),
                "transactionHash": tx_bytes,
                "topics": [HexBytes("0x1111"), HexBytes("0x2222")],
                "data": HexBytes(b"\xde\xad"),
            }
        ],
    }


def test_reconcile_pending_redeems_hexbytes_receipt_transitions(
    trade_conn_t4, world_db_t4, monkeypatch
):
    """T4 (primary round-trip antibody): reconcile_pending_redeems with a HexBytes receipt
    must not raise TypeError and must transition a negRisk+misrouted row to
    REDEEM_OPERATOR_REQUIRED.

    Sed-flip: removing default=_jsonable from _json_dumps causes this test → RED
    with 'TypeError: Object of type HexBytes is not JSON serializable'.
    """
    import src.execution.settlement_commands as sc
    import src.state.db as _db_mod

    monkeypatch.setattr(
        sc,
        "ZEUS_WORLD_DB_PATH",
        pathlib.Path(world_db_t4),
        raising=False,
    )
    orig_world = _db_mod.ZEUS_WORLD_DB_PATH
    _db_mod.ZEUS_WORLD_DB_PATH = pathlib.Path(world_db_t4)

    _TX_HASH = "0x" + "de" * 32

    try:
        # Insert a REDEEM_TX_HASHED row
        cmd_id = request_redeem(
            _NEGRISK_CONDITION_ID,
            "USDC",
            market_id=_NEGRISK_CONDITION_ID,
            token_amounts={"yes": "1.0"},
            winning_index_set='["2"]',
            conn=trade_conn_t4,
            requested_at=_NOW,
        )
        trade_conn_t4.execute(
            "UPDATE settlement_commands SET state = ?, tx_hash = ? WHERE command_id = ?",
            (SettlementState.REDEEM_TX_HASHED.value, _TX_HASH, cmd_id),
        )
        trade_conn_t4.commit()

        # Web3 mock returns a HexBytes-typed receipt
        receipt = _make_hexbytes_receipt(_TX_HASH, status=1, to_address=POLYGON_CTF_ADDRESS)
        eth_mock = SimpleNamespace(
            get_transaction_receipt=lambda _: receipt,
            block_number=210,
        )
        web3 = SimpleNamespace(eth=eth_mock)

        # Must not raise TypeError
        results = reconcile_pending_redeems(web3, trade_conn_t4)

        assert len(results) == 1, f"T4 FAIL: expected 1 result, got {len(results)}"
        result = results[0]

        # negRisk + to=CTF → must reset to REDEEM_OPERATOR_REQUIRED
        assert result.state == SettlementState.REDEEM_OPERATOR_REQUIRED, (
            f"T4 FAIL: result.state={result.state!r}, expected REDEEM_OPERATOR_REQUIRED"
        )
        assert result.tx_hash is None, (
            f"T4 FAIL: result.tx_hash={result.tx_hash!r}, expected None"
        )

        row = trade_conn_t4.execute(
            "SELECT state, tx_hash FROM settlement_commands WHERE command_id = ?",
            (cmd_id,),
        ).fetchone()
        assert row["state"] == SettlementState.REDEEM_OPERATOR_REQUIRED.value, (
            f"T4 FAIL: DB state={row['state']!r}"
        )
        assert row["tx_hash"] is None, f"T4 FAIL: DB tx_hash={row['tx_hash']!r}"

    finally:
        _db_mod.ZEUS_WORLD_DB_PATH = orig_world


# ---------------------------------------------------------------------------
# T5: Unsupported type still raises TypeError
# ---------------------------------------------------------------------------

def test_json_dumps_unsupported_type_raises():
    """T5: Non-bytes unsupported types still raise TypeError — no silent corruption."""
    with pytest.raises(TypeError, match="is not JSON serializable"):
        _json_dumps({"x": object()})


def test_T6_attributedict_coerced_to_plain_dict():
    """Antibody T6 (2026-05-19 live reconciler postmortem):
    web3 AttributeDict (used for nested log entries in transaction receipts)
    must serialize via the dict-coercion branch in `_jsonable`. Sed-flip:
    removing the `hasattr(o, "keys")` branch causes this test → RED.
    """
    # Reproduce web3.types.AttributeDict shape: dict-like with attribute access,
    # NOT a strict dict subclass on web3 7.x / Python 3.14.
    class FakeAttributeDict:
        def __init__(self, data):
            self._data = dict(data)
        def keys(self):
            return self._data.keys()
        def __getitem__(self, k):
            return self._data[k]
        def __iter__(self):
            return iter(self._data)
        def __len__(self):
            return len(self._data)
    log_entry = FakeAttributeDict({
        "blockHash": b"\x01" * 32,
        "transactionHash": b"\x02" * 32,
        "logIndex": 0,
    })
    payload = {"logs": [log_entry], "status": 1}
    js = _json_dumps(payload)
    assert '"logIndex":0' in js
    assert '"status":1' in js
    # bytes coerced via the bytes branch
    assert "0x" + "01" * 32 in js
    assert "0x" + "02" * 32 in js
