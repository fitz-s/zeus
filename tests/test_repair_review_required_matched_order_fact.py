# Lifecycle: created=2026-07-04; last_reviewed=2026-07-04; last_reused=never
# Purpose: Regression tests for targeted REVIEW_REQUIRED matched-order repair.
# Reuse: Run when matched-order command recovery or the operator wrapper changes.
# Authority basis: AGENTS.md position/execution proof gates; scripts/AGENTS.md repair contract.

from __future__ import annotations

import pytest

from scripts import repair_review_required_matched_order_fact as repair


def _candidate() -> dict:
    return {
        "command_id": "cmd-exit",
        "intent_kind": "EXIT",
        "state": "REVIEW_REQUIRED",
        "venue_order_id": "0xabc",
        "order_fact_venue_order_id": "0xabc",
        "order_fact_state": "MATCHED",
        "order_fact_matched_size": "85.17",
        "order_fact_remaining_size": "0",
        "side": "SELL",
        "size": "85.17",
        "price": "0.037",
    }


def test_build_proofs_accepts_top_level_taker_trade() -> None:
    proof = repair.build_proofs(
        [_candidate()],
        [
            {
                "id": "trade-1",
                "status": "CONFIRMED",
                "taker_order_id": "0xabc",
                "size": "85.17",
                "price": "0.037",
                "transaction_hash": "0xtx",
            }
        ],
    )[0]

    assert proof["recoverable"] is True
    assert proof["point_source"] == "account_trades_taker_order"
    assert proof["event_type"] == "FILL_CONFIRMED"
    assert proof["order_fact_state"] == "MATCHED"
    assert proof["matched_size"] == "85.17"
    assert proof["remaining_size"] == "0"
    assert proof["trade_ids"] == ["trade-1"]
    assert proof["tx_hashes"] == ["0xtx"]


def test_run_apply_requires_venue_proof() -> None:
    with pytest.raises(ValueError, match="--apply requires --venue-proof"):
        repair.run(apply=True, command_id="cmd-exit", venue_proof=False)


def test_run_apply_requires_command_id(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="--apply requires --command-id"):
        repair.run(apply=True, command_id=None, venue_proof=True)


def test_run_apply_delegates_to_single_command(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeConn:
        row_factory = None

        def close(self) -> None:
            pass

        def commit(self) -> None:
            pass

    captured: dict[str, object] = {}

    monkeypatch.setattr(repair, "get_trade_connection_read_only", lambda: FakeConn())
    monkeypatch.setattr(repair, "get_trade_connection", lambda write_class: FakeConn())
    monkeypatch.setattr(repair, "_load_live_client", lambda: object())
    monkeypatch.setattr(
        repair,
        "_client_trade_payloads",
        lambda adapter: [
            {
                "id": "trade-1",
                "status": "CONFIRMED",
                "taker_order_id": "0xabc",
                "size": "85.17",
                "price": "0.037",
            }
        ],
    )
    monkeypatch.setattr(repair, "find_candidates", lambda conn, command_id=None: [_candidate()])

    def fake_reconcile(conn, adapter, *, command_id=None):
        captured["command_id"] = command_id
        return {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}

    monkeypatch.setattr(repair, "reconcile_matched_order_facts", fake_reconcile)

    result = repair.run(apply=True, command_id="cmd-exit", venue_proof=True)

    assert captured == {"command_id": "cmd-exit"}
    assert result["applied_summary"] == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
