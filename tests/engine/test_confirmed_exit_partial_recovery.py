# Created: 2026-10-08
# Last reused/audited: 2026-10-08
# Authority basis: canonical confirmed partial EXIT recovery; INV-01/28/31.
"""Focused command-fold controls; scheduled replay separately proves submit.

The existing command fixture supplies an initial acknowledged command through
canonical writers. These tests exercise fact ingestion and command recovery,
not source selection, signing, position reduction, or cash confirmation.
"""
from datetime import datetime, timezone
import hashlib
import json
import sqlite3

import pytest

from tests.test_command_recovery import conn, _insert, _advance_to_acked  # noqa: F401
from tests.test_fill_synchronizer import FakeSyncAdapter, _trade

NOW = datetime(2026, 4, 26, 0, 3, tzinfo=timezone.utc)


def _acknowledged_exit(conn, *, token="tok-001", state="ACKED", intent="EXIT", side="SELL"):
    _insert(conn, intent_kind=intent, side=side, selected_token_id=token)
    _advance_to_acked(conn)
    # The state matrix below declares initial unit-test states. The scheduled
    # acceptance never performs this setup or changes command state directly.
    if state != "ACKED":
        conn.execute("UPDATE venue_commands SET state=? WHERE command_id='cmd-001'", (state,))
    conn.commit()


def _native_trade(*, token="tok-001", size="2", price=".50", status="CONFIRMED"):
    return {**_trade(trade_id="partial-native", order_id="ord-001", size=size, price=price, status=status),
        "event_type": "trade", "market": "condition-test", "asset_id": token, "side": "SELL",
        "timestamp": NOW.isoformat()}


def _facts(conn, raw, *, source="REST", order="ord-001"):
    from src.state.venue_command_repo import append_trade_fact
    append_trade_fact(conn, trade_id=raw["id"], venue_order_id=order, command_id="cmd-001",
        state=raw["status"], filled_size=raw["size"], fill_price=raw["price"], source=source,
        observed_at=NOW.isoformat(), raw_payload_hash=hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest(),
        raw_payload_json=raw)
    conn.commit()


def _partial_events(conn):
    return conn.execute("SELECT * FROM venue_command_events WHERE event_type='PARTIAL_FILL_OBSERVED'").fetchall()


@pytest.mark.parametrize("token", ["tok-001", "tok-001-no"])
@pytest.mark.parametrize("resume", ["REST", "WS"])
def test_rest_first_duplicate_delivery_repairs_only_missing_partial_command_fold(conn, monkeypatch, token, resume):
    from src.execution import command_recovery as recovery
    from src.ingest import fill_synchronizer as sync
    from src.ingest.polymarket_user_channel import PolymarketUserChannelIngestor, WSAuth

    _acknowledged_exit(conn, token=token)
    raw = _native_trade(token=token)
    adapter = FakeSyncAdapter([raw])
    # Model the older writer, which persisted the immutable fill/observation
    # but omitted this command fold. No position/receipt/decision is injected.
    with monkeypatch.context() as old_writer:
        old_writer.setattr(recovery, "reconcile_confirmed_partial_exit_command", lambda *_: False)
        result = sync.sync_fills(conn, adapter, observed_at=NOW)
    assert result["appended"] == 1
    assert not _partial_events(conn)
    before = tuple(tuple(row) for row in conn.execute("SELECT * FROM venue_trade_facts"))
    prepared = sync._prepare_fill_sync(conn, adapter, source=sync.DEFAULT_SOURCE, observed_at=NOW)
    pending, _ = sync._pending_fill_sync_writes(conn, prepared)
    assert len(pending.trades) == 1, "durable fill must not hide unfinished command fold"

    if resume == "REST":
        result = sync.sync_fills(conn, adapter, observed_at=NOW)
        assert result["appended"] == 0 and result["skipped_idempotent"] == 1
    else:
        handler = PolymarketUserChannelIngestor(None, ["condition-test"],
            auth=WSAuth("public-test-key", "public-test-secret", "public-test-pass"),
            conn_factory=lambda: conn, own_connection=False)
        result = handler.handle_message(raw)
        assert result["reason"] == "duplicate_trade_fact"
        assert result["command_event"] == "PARTIAL_FILL_OBSERVED"
        assert handler.handle_message(raw)["command_event"] is None
    assert conn.execute("SELECT state FROM venue_commands").fetchone()[0] == "PARTIAL"
    assert len(_partial_events(conn)) == 1
    assert tuple(tuple(row) for row in conn.execute("SELECT * FROM venue_trade_facts")) == before
    assert recovery.reconcile_confirmed_partial_exit_command(conn, "cmd-001") is False
    prepared = sync._prepare_fill_sync(conn, adapter, source=sync.DEFAULT_SOURCE, observed_at=NOW)
    pending, _ = sync._pending_fill_sync_writes(conn, prepared)
    assert pending.trades == (), "completed duplicate must avoid another writer tranche"


@pytest.mark.parametrize("fault", ["matched", "mined", "failed", "zero", "full", "overfill",
    "below_limit", "wrong_order", "wrong_token", "wrong_side", "missing_token", "entry", "review", "terminal", "already_partial"])
def test_partial_command_fold_requires_exact_incomplete_confirmed_exit(conn, fault):
    from src.execution import command_recovery as recovery

    states = {"review": "REVIEW_REQUIRED", "terminal": "EXPIRED", "already_partial": "PARTIAL"}
    _acknowledged_exit(conn, state=states.get(fault, "ACKED"),
        intent="ENTRY" if fault == "entry" else "EXIT", side="BUY" if fault == "entry" else "SELL")
    raw = _native_trade(size={"zero": "0", "full": "10", "overfill": "11"}.get(fault, "2"),
        status={"matched": "MATCHED", "mined": "MINED", "failed": "FAILED"}.get(fault, "CONFIRMED"),
        price=".49" if fault == "below_limit" else ".50", token="foreign" if fault == "wrong_token" else "tok-001")
    if fault == "wrong_side":
        raw["side"] = "BUY"
    if fault == "missing_token":
        raw.pop("asset_id")
    if fault == "zero":
        with pytest.raises(ValueError, match="positive finite fill economics"):
            _facts(conn, raw)
    else:
        _facts(conn, raw, order="foreign-order" if fault == "wrong_order" else "ord-001")
    before = tuple(tuple(row) for row in conn.execute("SELECT * FROM venue_command_events"))
    assert recovery.confirmed_partial_exit_command_pending(conn, "cmd-001") is False
    assert recovery.reconcile_confirmed_partial_exit_command(conn, "cmd-001") is False
    assert tuple(tuple(row) for row in conn.execute("SELECT * FROM venue_command_events")) == before


@pytest.mark.parametrize("layout", ["maker", "taker"])
@pytest.mark.parametrize("state", ["ACKED", "POST_ACKED"])
def test_partial_fold_reproduces_selected_native_fill_leg(conn, layout, state):
    from src.execution import command_recovery as recovery

    _acknowledged_exit(conn, state=state)
    raw = _native_trade()
    if layout == "maker":
        raw.update(asset_id="counterparty-token", side="BUY", taker_order_id="counterparty-order",
            maker_orders=[{"order_id": "ord-001", "asset_id": "tok-001", "side": "SELL", "matched_amount": "2", "price": ".50"}])
    else:
        raw.update(trader_side="TAKER", taker_order_id="ord-001",
            maker_orders=[{"order_id": "counterparty-order", "asset_id": "tok-001", "side": "BUY", "matched_amount": "2", "price": ".50"}])
    _facts(conn, raw)
    assert recovery.reconcile_confirmed_partial_exit_command(conn, "cmd-001") is True
    assert conn.execute("SELECT state FROM venue_commands").fetchone()[0] == "PARTIAL"
    assert len(_partial_events(conn)) == 1


def test_partial_command_fold_and_fact_rollback_together(conn):
    from src.execution import command_recovery as recovery
    from src.state.venue_command_repo import append_trade_fact

    _acknowledged_exit(conn)
    raw = _native_trade()
    conn.execute("BEGIN")
    append_trade_fact(conn, trade_id=raw["id"], venue_order_id="ord-001", command_id="cmd-001",
        state="CONFIRMED", filled_size="2", fill_price=".50", source="REST", observed_at=NOW.isoformat(),
        raw_payload_hash="a"*64, raw_payload_json=raw)
    assert recovery.reconcile_confirmed_partial_exit_command(conn, "cmd-001") is True
    conn.rollback()
    assert conn.execute("SELECT state FROM venue_commands").fetchone()[0] == "ACKED"
    assert not _partial_events(conn)
    assert conn.execute("SELECT COUNT(*) FROM venue_trade_facts").fetchone()[0] == 0


def test_concurrent_duplicate_fold_retries_without_second_partial_event(conn, tmp_path, monkeypatch):
    from src.execution import command_recovery as recovery

    _acknowledged_exit(conn)
    _facts(conn, _native_trade())
    path = tmp_path / "concurrent-partial.db"
    with sqlite3.connect(path) as target:
        conn.backup(target)
        target.execute("PRAGMA journal_mode=WAL")
    first = sqlite3.connect(path, timeout=0)
    second = sqlite3.connect(path, timeout=0)
    first.row_factory = second.row_factory = sqlite3.Row
    original = recovery._confirmed_partial_exit_command_proof
    interleaved = []

    def fold_after_first_read(reader, command_id):
        proof = original(reader, command_id)
        if reader is first and not interleaved:
            interleaved.append(True)
            assert recovery.reconcile_confirmed_partial_exit_command(second, command_id) is True
            second.commit()
        return proof

    monkeypatch.setattr(recovery, "_confirmed_partial_exit_command_proof", fold_after_first_read)
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            recovery.reconcile_confirmed_partial_exit_command(first, "cmd-001")
        assert first.in_transaction is False
        assert recovery.reconcile_confirmed_partial_exit_command(first, "cmd-001") is False
        assert len(_partial_events(first)) == 1
        assert first.execute("SELECT COUNT(*) FROM venue_trade_facts").fetchone()[0] == 1
    finally:
        first.close()
        second.close()
