# Lifecycle: created=2026-07-04; last_reviewed=2026-07-14; last_reused=2026-07-14
# Purpose: Regression tests for REVIEW_REQUIRED no-venue-exposure repair triage.
# Reuse: Run when command recovery REVIEW_REQUIRED clearance or operator repair scripts change.
# Authority basis: AGENTS.md position/execution proof gates; scripts/AGENTS.md repair contract.

from __future__ import annotations

import sqlite3

import pytest

from scripts import repair_review_required_no_venue_exposure as repair


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            intent_kind TEXT,
            state TEXT,
            position_id TEXT,
            token_id TEXT,
            side TEXT,
            size TEXT,
            price TEXT,
            venue_order_id TEXT,
            decision_id TEXT,
            updated_at TEXT,
            created_at TEXT,
            envelope_id TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE venue_command_events (
            event_id TEXT PRIMARY KEY,
            command_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            payload_json TEXT,
            state_after TEXT NOT NULL,
            UNIQUE (command_id, sequence_no)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE venue_order_facts (
            fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_order_id TEXT NOT NULL,
            command_id TEXT NOT NULL,
            state TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE venue_trade_facts (
            trade_fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT NOT NULL,
            venue_order_id TEXT NOT NULL,
            command_id TEXT NOT NULL,
            state TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE position_events (
            event_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            command_id TEXT,
            event_type TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE position_lots (
            lot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id TEXT NOT NULL,
            source_command_id TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT NOT NULL
        )
        """
    )
    return conn


def _insert_review_required(
    conn: sqlite3.Connection,
    *,
    command_id: str = "cmd-safe",
    position_id: str = "pos-safe",
    token_id: str = "tok-safe",
    size: str = "8.25",
    price: str = "0.56",
    venue_order_id: str = "",
    reason: str = "recovery_no_venue_order_id",
) -> None:
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, intent_kind, state, position_id, token_id, side, size, price,
            venue_order_id, decision_id, updated_at, created_at, envelope_id
        ) VALUES (?, 'ENTRY', 'REVIEW_REQUIRED', ?, ?, 'BUY', ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            command_id,
            position_id,
            token_id,
            size,
            price,
            venue_order_id,
            f"decision-{command_id}",
            "2026-07-04T04:00:00+00:00",
            "2026-07-04T03:55:00+00:00",
        ),
    )
    conn.execute(
        """
        INSERT INTO venue_command_events (
            event_id, command_id, sequence_no, event_type, occurred_at, payload_json, state_after
        ) VALUES (?, ?, 1, 'INTENT_CREATED', '2026-07-04T03:55:00+00:00', '{}', 'INTENT_CREATED')
        """,
        (f"{command_id}-1", command_id),
    )
    conn.execute(
        """
        INSERT INTO venue_command_events (
            event_id, command_id, sequence_no, event_type, occurred_at, payload_json, state_after
        ) VALUES (?, ?, 2, 'REVIEW_REQUIRED', '2026-07-04T04:00:00+00:00', ?, 'REVIEW_REQUIRED')
        """,
        (f"{command_id}-2", command_id, f'{{"reason":"{reason}"}}'),
    )


class FakeAdapter:
    def __init__(self, *, open_orders=None, trades=None):
        self._open_orders = list(open_orders or [])
        self._trades = list(trades or [])

    def get_open_orders(self):
        return list(self._open_orders)

    def get_trades(self):
        return list(self._trades)


def test_find_candidates_blocks_position_projection_and_venue_id() -> None:
    conn = _conn()
    _insert_review_required(conn, command_id="cmd-safe", position_id="pos-safe")
    _insert_review_required(conn, command_id="cmd-position", position_id="pos-position")
    conn.execute("INSERT INTO position_current (position_id, phase) VALUES ('pos-position', 'quarantined')")
    _insert_review_required(
        conn,
        command_id="cmd-venue",
        position_id="pos-venue",
        venue_order_id="0xorder",
    )

    candidates = {candidate.command_id: candidate for candidate in repair.find_candidates(conn)}

    assert candidates["cmd-safe"].local_clearance_eligible is True
    assert candidates["cmd-position"].blockers == ("position_current_present",)
    assert "venue_order_id_present" in candidates["cmd-venue"].blockers


def test_run_with_venue_proof_marks_only_zero_match_candidate_clear(monkeypatch) -> None:
    conn = _conn()
    _insert_review_required(conn)
    monkeypatch.setattr(repair, "get_trade_connection_read_only", lambda: conn)

    result = repair.run(
        apply=False,
        venue_proof=True,
        adapter=FakeAdapter(
            open_orders=[{"id": "other", "asset_id": "other-token", "status": "LIVE"}],
            trades=[{"id": "old", "asset_id": "tok-safe", "match_time": "1"}],
        ),
    )

    assert result["local_clearance_eligible_count"] == 1
    assert result["venue_absence_clear_count"] == 1
    assert result["candidates"][0]["venue_absence_clear"] is True
    assert result["candidates"][0]["venue_absence_proof"]["matching_trade_count"] == 0
    assert result["venue_action"] is False
    assert result["db_backup_created"] is False


def test_run_with_matching_trade_keeps_candidate_unclear(monkeypatch) -> None:
    conn = _conn()
    _insert_review_required(conn)
    monkeypatch.setattr(repair, "get_trade_connection_read_only", lambda: conn)

    result = repair.run(
        apply=False,
        venue_proof=True,
        adapter=FakeAdapter(
            trades=[
                {
                    "id": "trade-safe",
                    "status": "CONFIRMED",
                    "trader_side": "TAKER",
                    "asset_id": "tok-safe",
                    "side": "BUY",
                    "price": "0.56",
                    "size": "8.25",
                    "taker_order_id": "ord-safe",
                    "match_time": "2026-07-04T04:01:00+00:00",
                }
            ]
        ),
    )

    assert result["venue_absence_clear_count"] == 0
    assert result["confirmed_trade_recoverable_count"] == 1
    assert result["candidates"][0]["venue_absence_clear"] is False
    assert result["candidates"][0]["venue_absence_proof"]["matching_trade_count"] == 1
    assert result["candidates"][0]["confirmed_trade_recoverable"] is True
    assert result["candidates"][0]["confirmed_trade_proof"]["venue_order_id"] == "ord-safe"


def test_apply_requires_authenticated_venue_proof() -> None:
    with pytest.raises(ValueError, match="--apply requires --venue-proof"):
        repair.run(apply=True, venue_proof=False, adapter=FakeAdapter())


def test_confirmed_trade_apply_requires_command_id() -> None:
    with pytest.raises(ValueError, match="--apply-confirmed-trade requires --command-id"):
        repair.run(
            apply=False,
            apply_confirmed_trade=True,
            venue_proof=True,
            adapter=FakeAdapter(),
        )


def test_apply_calls_existing_clearance_only_for_clear_candidate(monkeypatch) -> None:
    conn = _conn()
    _insert_review_required(conn)
    monkeypatch.setattr(repair, "get_trade_connection", lambda write_class: conn)
    monkeypatch.setattr(repair, "_source_commit", lambda: "test-commit")
    cleared: list[tuple[str, dict]] = []

    def fake_clear(conn_arg, command_id, *, venue_absence_proof, **kwargs):
        assert conn_arg is conn
        cleared.append((command_id, venue_absence_proof))
        return {"command_id": command_id, "reason": "review_cleared_no_venue_exposure"}

    monkeypatch.setattr(repair, "clear_review_required_no_venue_exposure", fake_clear)

    result = repair.run(
        apply=True,
        venue_proof=True,
        adapter=FakeAdapter(),
        reviewed_by="pytest",
    )

    assert [item[0] for item in cleared] == ["cmd-safe"]
    assert result["applied"][0]["result"] == "review_cleared_no_venue_exposure"
    assert result["venue_action"] is False
    assert result["db_backup_created"] is False


def test_apply_confirmed_trade_targets_one_command(monkeypatch) -> None:
    conn = _conn()
    _insert_review_required(conn)
    _insert_review_required(
        conn,
        command_id="cmd-other",
        position_id="pos-other",
        token_id="tok-other",
    )
    monkeypatch.setattr(repair, "get_trade_connection", lambda write_class: conn)
    monkeypatch.setattr(repair.VenueCommand, "from_row", lambda row: row)
    calls: list[str] = []

    def fake_recover(conn_arg, cmd, adapter):
        assert conn_arg is conn
        calls.append(cmd["command_id"])
        return "advanced"

    monkeypatch.setattr(repair, "_review_required_confirmed_trade_recovery", fake_recover)

    result = repair.run(
        apply=False,
        apply_confirmed_trade=True,
        command_id="cmd-safe",
        venue_proof=True,
        adapter=FakeAdapter(
            trades=[
                {
                    "id": "trade-safe",
                    "status": "CONFIRMED",
                    "trader_side": "TAKER",
                    "asset_id": "tok-safe",
                    "side": "BUY",
                    "price": "0.56",
                    "size": "8.25",
                    "taker_order_id": "ord-safe",
                    "match_time": "2026-07-04T04:01:00+00:00",
                }
            ]
        ),
    )

    assert calls == ["cmd-safe"]
    assert result["selected_candidate_count"] == 1
    assert result["confirmed_trade_applied"][0]["result"] == "advanced"
    assert result["venue_action"] is False
    assert result["db_backup_created"] is False


def test_review_required_confirmed_trade_match_accepts_top_level_taker_fill() -> None:
    from src.execution.command_recovery import _review_required_trade_maker_match

    command = {
        "command_id": "081eacce14894cc5",
        "token_id": "no-token",
        "side": "BUY",
        "price": "0.56",
        "size": "8.25",
    }
    trade = {
        "id": "trade-1",
        "status": "CONFIRMED",
        "asset_id": "no-token",
        "side": "BUY",
        "price": "0.56",
        "size": "8.25",
        "taker_order_id": "0xtaker",
        "trader_side": "TAKER",
        "maker_orders": [
            {
                "asset_id": "yes-token",
                "side": "BUY",
                "price": "0.44",
                "matched_amount": "8.25",
                "order_id": "0xmaker",
            }
        ],
    }

    match = _review_required_trade_maker_match(command, trade)

    assert match == {
        "order_id": "0xtaker",
        "matched_size": "8.25",
        "fill_price": "0.56",
        "maker_order": {
            "asset_id": "no-token",
            "side": "BUY",
            "price": "0.56",
            "matched_amount": "8.25",
            "order_id": "0xtaker",
            "source": "top_level_taker_trade",
        },
    }


def test_review_required_confirmed_trade_match_accepts_buy_price_improvement() -> None:
    from src.execution.command_recovery import _review_required_trade_maker_match

    match = _review_required_trade_maker_match(
        {
            "token_id": "no-token",
            "side": "BUY",
            "price": "0.012",
            "size": "90",
        },
        {
            "trader_side": "TAKER",
            "asset_id": "no-token",
            "side": "BUY",
            "price": "0.011",
            "size": "99.726666",
            "taker_order_id": "0xtaker",
        },
    )

    assert match == {
        "order_id": "0xtaker",
        "matched_size": "99.726666",
        "fill_price": "0.011",
        "maker_order": {
            "asset_id": "no-token",
            "side": "BUY",
            "price": "0.011",
            "matched_amount": "99.726666",
            "order_id": "0xtaker",
            "source": "top_level_taker_trade",
        },
    }


def test_review_required_confirmed_trade_match_rejects_worse_buy_price() -> None:
    from src.execution.command_recovery import _review_required_trade_maker_match

    match = _review_required_trade_maker_match(
        {
            "token_id": "no-token",
            "side": "BUY",
            "price": "0.012",
            "size": "90",
        },
        {
            "asset_id": "no-token",
            "side": "BUY",
            "price": "0.013",
            "size": "90",
            "taker_order_id": "0xtaker",
        },
    )

    assert match is None


def test_review_required_confirmed_trade_match_rejects_unbound_counterparty_maker() -> None:
    from src.execution.command_recovery import _review_required_trade_maker_match

    match = _review_required_trade_maker_match(
        {
            "token_id": "yes-token",
            "side": "BUY",
            "price": "0.995",
            "size": "28",
        },
        {
            "trader_side": "TAKER",
            "asset_id": "no-token",
            "side": "BUY",
            "price": "0.005",
            "size": "28",
            "taker_order_id": "0xours",
            "maker_orders": [
                {
                    "asset_id": "yes-token",
                    "side": "BUY",
                    "price": "0.99",
                    "matched_amount": "28.36",
                    "order_id": "0xcounterparty",
                }
            ],
        },
    )

    assert match is None


@pytest.mark.parametrize(
    ("side", "limit_price", "fill_price", "expected"),
    [
        ("BUY", "0.012", "0.011", True),
        ("BUY", "0.012", "0.013", False),
        ("SELL", "0.400", "0.410", True),
        ("SELL", "0.400", "0.390", False),
        ("SELL", "0.400", "1.100", False),
        ("SELL", "1.100", "0.900", False),
        ("BUY", "0.012", "0", False),
        ("BUY", "0.012", "NaN", False),
    ],
)
def test_fill_price_respects_side_specific_limit(
    side: str,
    limit_price: str,
    fill_price: str,
    expected: bool,
) -> None:
    from src.execution.command_recovery import _fill_price_respects_limit

    assert (
        _fill_price_respects_limit(fill_price, limit_price, side=side)
        is expected
    )


@pytest.mark.parametrize(
    ("side", "command_size", "filled_size", "expected"),
    [
        ("BUY", "90", "99.726666", True),
        ("BUY", "90", "89.98", False),
        ("SELL", "90", "90", True),
        ("SELL", "90", "89.995", True),
        ("SELL", "90", "90.000001", True),
        ("SELL", "90", "90.01", False),
        ("SELL", "90", "99.726666", False),
    ],
)
def test_fill_size_completion_is_side_specific(
    side: str,
    command_size: str,
    filled_size: str,
    expected: bool,
) -> None:
    from src.execution.command_recovery import _fill_size_completes_limit_order

    assert (
        _fill_size_completes_limit_order(filled_size, command_size, side=side)
        is expected
    )


def test_point_order_from_trade_payloads_accepts_top_level_taker_order() -> None:
    from src.execution.command_recovery import _point_order_from_maker_trade_payloads

    point_order = _point_order_from_maker_trade_payloads(
        [
            {
                "id": "trade-exit",
                "status": "CONFIRMED",
                "asset_id": "yes-token",
                "outcome": "Yes",
                "market": "condition-1",
                "side": "SELL",
                "price": "0.037",
                "size": "85.17",
                "taker_order_id": "ord-exit",
                "transaction_hash": "0xtx",
                "trader_side": "TAKER",
                "maker_orders": [
                    {
                        "asset_id": "yes-token",
                        "matched_amount": "31",
                        "order_id": "other-maker",
                        "price": "0.038",
                        "side": "BUY",
                    }
                ],
            }
        ],
        order_id="ord-exit",
    )

    assert point_order is not None
    assert point_order["id"] == "ord-exit"
    assert point_order["matched_size"] == "85.17"
    assert point_order["price"] == "0.037"
    assert point_order["tradeIDs"] == ["trade-exit"]
    assert point_order["transactionsHashes"] == ["0xtx"]
    assert point_order["source"] == "account_trades_taker_order"
