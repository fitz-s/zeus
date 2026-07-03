# Created: 2026-07-02
# Last reused or audited: 2026-07-02
# Authority basis: docs/rebuild/order_engine_implementation_architecture_2026-07-02.md
#   §1 row "CTF convert/split/merge" (W2.4 packet).
"""Tests for src/execution/ctf_conversion_commands.py (W2.4 — inert this
packet, no production caller).

Covers: persist-before-side-effect (INV-28), ack/reject/unknown mapping,
fail-closed UNKNOWN on ambiguity, the SPLIT/MERGE vs CONVERT column-shape
CHECK constraint, and receipt-driven reconciliation.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.execution.ctf_conversion_commands import (
    ConversionCommandStateError,
    ConversionState,
    enqueue_convert,
    enqueue_merge,
    enqueue_split,
    execute_conversion,
    get_command,
    list_commands,
    reconcile_pending_conversions,
)

CONDITION_ID = "ab" * 32
MARKET_ID = "cd" * 32
AMOUNT_MICRO = 2_000_000


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    yield c
    c.close()


class FakeAdapter:
    """Fake adapter stub — the boundary execute_conversion is allowed to mock
    (it is NOT one of the module's own new functions under test)."""

    def __init__(self, response=None, raise_exc: Exception | None = None) -> None:
        self.response = response
        self.raise_exc = raise_exc
        self.calls: list[tuple[str, tuple, dict]] = []

    def _call(self, name, args, kwargs):
        self.calls.append((name, args, kwargs))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response

    def split_positions(self, *args, **kwargs):
        return self._call("split_positions", args, kwargs)

    def merge_positions(self, *args, **kwargs):
        return self._call("merge_positions", args, kwargs)

    def convert_positions(self, *args, **kwargs):
        return self._call("convert_positions", args, kwargs)


# ── Persist-before-side-effect (INV-28) ──────────────────────────────────────

def test_enqueue_split_persists_intent_created_row(conn: sqlite3.Connection) -> None:
    command_id = enqueue_split(CONDITION_ID, AMOUNT_MICRO, conn=conn)
    row = get_command(conn, command_id)
    assert row["state"] == ConversionState.INTENT_CREATED.value
    assert row["operation_type"] == "SPLIT"
    assert row["condition_id"] == CONDITION_ID
    assert row["market_id"] is None
    assert row["amount_micro"] == AMOUNT_MICRO

    events = conn.execute(
        "SELECT event_type FROM ctf_conversion_command_events WHERE command_id = ?",
        (command_id,),
    ).fetchall()
    assert [e["event_type"] for e in events] == [ConversionState.INTENT_CREATED.value]


def test_enqueue_merge_persists_intent_created_row(conn: sqlite3.Connection) -> None:
    command_id = enqueue_merge(CONDITION_ID, AMOUNT_MICRO, neg_risk=True, conn=conn)
    row = get_command(conn, command_id)
    assert row["state"] == ConversionState.INTENT_CREATED.value
    assert row["operation_type"] == "MERGE"
    assert row["neg_risk"] == 1


def test_enqueue_convert_persists_intent_created_row(conn: sqlite3.Connection) -> None:
    command_id = enqueue_convert(MARKET_ID, 3, AMOUNT_MICRO, conn=conn)
    row = get_command(conn, command_id)
    assert row["state"] == ConversionState.INTENT_CREATED.value
    assert row["operation_type"] == "CONVERT"
    assert row["market_id"] == MARKET_ID
    assert row["condition_id"] is None
    assert row["index_set"] == 3
    assert row["neg_risk"] == 1  # convert is always neg-risk


def test_enqueue_rejects_non_positive_amount(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        enqueue_split(CONDITION_ID, 0, conn=conn)


def test_enqueue_convert_rejects_non_positive_index_set(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        enqueue_convert(MARKET_ID, 0, AMOUNT_MICRO, conn=conn)


# ── Column-shape CHECK constraint (SPLIT/MERGE vs CONVERT identifier space) ──

def test_split_row_cannot_carry_market_id_or_index_set(conn: sqlite3.Connection) -> None:
    from src.execution.ctf_conversion_commands import ensure_ctf_conversion_schema_ready

    ensure_ctf_conversion_schema_ready(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO ctf_conversion_commands (
              command_id, state, operation_type, neg_risk, condition_id,
              market_id, index_set, amount_micro, requested_at
            ) VALUES ('x', 'CTF_CONVERSION_INTENT_CREATED', 'SPLIT', 0, ?, ?, ?, ?, '2026-07-02T00:00:00Z')
            """,
            (CONDITION_ID, MARKET_ID, 1, AMOUNT_MICRO),
        )


def test_convert_row_cannot_carry_condition_id(conn: sqlite3.Connection) -> None:
    from src.execution.ctf_conversion_commands import ensure_ctf_conversion_schema_ready

    ensure_ctf_conversion_schema_ready(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO ctf_conversion_commands (
              command_id, state, operation_type, neg_risk, condition_id,
              market_id, index_set, amount_micro, requested_at
            ) VALUES ('x', 'CTF_CONVERSION_INTENT_CREATED', 'CONVERT', 1, ?, ?, ?, ?, '2026-07-02T00:00:00Z')
            """,
            (CONDITION_ID, MARKET_ID, 1, AMOUNT_MICRO),
        )


# ── ack/reject/unknown mapping (execute_conversion) ──────────────────────────

def test_execute_conversion_ack_maps_to_tx_hashed(conn: sqlite3.Connection) -> None:
    command_id = enqueue_split(CONDITION_ID, AMOUNT_MICRO, conn=conn)
    adapter = FakeAdapter(response={"success": True, "tx_hash": "0x" + "11" * 32})
    result = execute_conversion(
        conn, adapter, command_id, safe_address="0xsafe", signer_eoa="0xeoa",
    )
    assert result["success"] is True
    assert result["state"] == ConversionState.TX_HASHED.value
    row = get_command(conn, command_id)
    assert row["state"] == ConversionState.TX_HASHED.value
    assert row["tx_hash"] == "0x" + "11" * 32
    # exactly one adapter call, with the persisted row's own fields threaded through
    assert adapter.calls[0][0] == "split_positions"
    assert adapter.calls[0][1][0] == CONDITION_ID


def test_execute_conversion_clean_reject_maps_to_failed(conn: sqlite3.Connection) -> None:
    command_id = enqueue_merge(CONDITION_ID, AMOUNT_MICRO, conn=conn)
    adapter = FakeAdapter(response={
        "success": False, "errorCode": "CTF_CONVERSION_SAFE_OWNER_MISMATCH",
        "errorMessage": "not an owner",
    })
    result = execute_conversion(
        conn, adapter, command_id, safe_address="0xsafe", signer_eoa="0xeoa",
    )
    assert result["success"] is False
    assert result["state"] == ConversionState.FAILED.value
    row = get_command(conn, command_id)
    assert row["state"] == ConversionState.FAILED.value
    assert row["terminal_at"] is not None


@pytest.mark.parametrize(
    "error_code",
    ["CTF_CONVERSION_BROADCAST_FAILED", "CTF_CONVERSION_INVALID_TX_HASH"],
)
def test_execute_conversion_ambiguous_broadcast_maps_to_unknown(
    conn: sqlite3.Connection, error_code: str,
) -> None:
    command_id = enqueue_convert(MARKET_ID, 2, AMOUNT_MICRO, conn=conn)
    adapter = FakeAdapter(response={"success": False, "errorCode": error_code})
    result = execute_conversion(
        conn, adapter, command_id, safe_address="0xsafe", signer_eoa="0xeoa",
    )
    assert result["success"] is False
    assert result["state"] == ConversionState.UNKNOWN.value
    row = get_command(conn, command_id)
    assert row["state"] == ConversionState.UNKNOWN.value
    # UNKNOWN is non-terminal — needs operator/reconciler follow-up, not a dead end.
    assert row["terminal_at"] is None


def test_execute_conversion_adapter_exception_fails_closed_to_unknown(
    conn: sqlite3.Connection,
) -> None:
    command_id = enqueue_split(CONDITION_ID, AMOUNT_MICRO, conn=conn)
    adapter = FakeAdapter(raise_exc=RuntimeError("network blew up mid-call"))
    result = execute_conversion(
        conn, adapter, command_id, safe_address="0xsafe", signer_eoa="0xeoa",
    )
    assert result["success"] is False
    assert result["state"] == ConversionState.UNKNOWN.value
    row = get_command(conn, command_id)
    assert row["state"] == ConversionState.UNKNOWN.value


def test_execute_conversion_success_without_tx_hash_fails_closed_to_unknown(
    conn: sqlite3.Connection,
) -> None:
    command_id = enqueue_split(CONDITION_ID, AMOUNT_MICRO, conn=conn)
    adapter = FakeAdapter(response={"success": True})  # malformed: no tx_hash
    result = execute_conversion(
        conn, adapter, command_id, safe_address="0xsafe", signer_eoa="0xeoa",
    )
    assert result["state"] == ConversionState.UNKNOWN.value


def test_execute_conversion_rejects_row_not_in_intent_created(conn: sqlite3.Connection) -> None:
    command_id = enqueue_split(CONDITION_ID, AMOUNT_MICRO, conn=conn)
    adapter = FakeAdapter(response={"success": True, "tx_hash": "0x" + "22" * 32})
    execute_conversion(conn, adapter, command_id, safe_address="0xsafe", signer_eoa="0xeoa")
    # Row is now TX_HASHED — a second call must refuse, not re-drive the side effect.
    with pytest.raises(ConversionCommandStateError):
        execute_conversion(conn, adapter, command_id, safe_address="0xsafe", signer_eoa="0xeoa")
    assert len(adapter.calls) == 1  # no second adapter call was made


# ── Reconciliation (chain receipt -> CONFIRMED/FAILED) ───────────────────────

class FakeReceipt(dict):
    pass


class FakeW3:
    def __init__(self, receipts: dict[str, dict | None]) -> None:
        self._receipts = receipts
        self.eth = self

    def get_transaction_receipt(self, tx_hash):
        if tx_hash not in self._receipts:
            raise Exception("not found")
        receipt = self._receipts[tx_hash]
        if receipt is None:
            return None
        return FakeReceipt(receipt)


def test_reconcile_confirms_on_successful_receipt(conn: sqlite3.Connection) -> None:
    command_id = enqueue_split(CONDITION_ID, AMOUNT_MICRO, conn=conn)
    adapter = FakeAdapter(response={"success": True, "tx_hash": "0x" + "33" * 32})
    execute_conversion(conn, adapter, command_id, safe_address="0xsafe", signer_eoa="0xeoa")

    w3 = FakeW3({"0x" + "33" * 32: {"status": 1, "blockNumber": 42}})
    results = reconcile_pending_conversions(w3, conn)
    assert len(results) == 1
    assert results[0]["state"] == ConversionState.CONFIRMED.value
    assert results[0]["block_number"] == 42


def test_reconcile_fails_on_reverted_receipt(conn: sqlite3.Connection) -> None:
    command_id = enqueue_merge(CONDITION_ID, AMOUNT_MICRO, conn=conn)
    adapter = FakeAdapter(response={"success": True, "tx_hash": "0x" + "44" * 32})
    execute_conversion(conn, adapter, command_id, safe_address="0xsafe", signer_eoa="0xeoa")

    w3 = FakeW3({"0x" + "44" * 32: {"status": 0, "blockNumber": 43}})
    results = reconcile_pending_conversions(w3, conn)
    assert len(results) == 1
    assert results[0]["state"] == ConversionState.FAILED.value


def test_reconcile_leaves_row_alone_when_receipt_not_yet_landed(conn: sqlite3.Connection) -> None:
    command_id = enqueue_convert(MARKET_ID, 1, AMOUNT_MICRO, conn=conn)
    adapter = FakeAdapter(response={"success": True, "tx_hash": "0x" + "55" * 32})
    execute_conversion(conn, adapter, command_id, safe_address="0xsafe", signer_eoa="0xeoa")

    w3 = FakeW3({"0x" + "55" * 32: None})
    results = reconcile_pending_conversions(w3, conn)
    assert results == []
    row = get_command(conn, command_id)
    assert row["state"] == ConversionState.TX_HASHED.value


# ── list_commands ─────────────────────────────────────────────────────────────

def test_list_commands_filters_by_state(conn: sqlite3.Connection) -> None:
    id1 = enqueue_split(CONDITION_ID, AMOUNT_MICRO, conn=conn)
    id2 = enqueue_merge(CONDITION_ID, AMOUNT_MICRO, conn=conn)
    adapter = FakeAdapter(response={"success": True, "tx_hash": "0x" + "66" * 32})
    execute_conversion(conn, adapter, id1, safe_address="0xsafe", signer_eoa="0xeoa")

    intent_created = list_commands(conn, state=ConversionState.INTENT_CREATED)
    tx_hashed = list_commands(conn, state=ConversionState.TX_HASHED)
    assert [r["command_id"] for r in intent_created] == [id2]
    assert [r["command_id"] for r in tx_hashed] == [id1]
    assert len(list_commands(conn)) == 2
