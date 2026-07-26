# Created: 2026-05-05
# Last reused or audited: 2026-07-25
# Authority basis: docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1C/phase.json
# Purpose: Guard that settlement persistence (record_settlement_result) behaves
#   correctly on its own, now that on-chain redemption is decoupled entirely.
# Reuse: Run when harvester settlement side effects change.
# 2026-07-25 update: Zeus no longer submits on-chain redemption transactions
#   (Polymarket settles win/loss on our behalf). `enqueue_redeem_command` was
#   deleted from src/execution/harvester.py; the T2/T2b/T4/T5 tests that
#   asserted redeem-enqueue behavior and settlement/redeem independence were
#   removed (there is no redeem enqueue left to be independent from). T1/T1b/T3
#   survive unchanged — they test record_settlement_result in isolation.
#   test_settle_positions_closes_losing_retry_pending_position was re-homed
#   here from the deleted tests/test_settle_positions_uses_enqueue_redeem.py
#   (whose whole T2G premise — routing redeem through enqueue_redeem_command —
#   no longer exists), stripped of its enqueue_redeem_command references; the
#   underlying Hong Kong 2026-06-12 regression (stale retry_pending exit_state
#   must not block a venue-verified settlement) is unrelated to redeem and
#   still needs coverage.
"""record_settlement_result writes settlement facts to decision_log.

T1: record_settlement_result writes rows to decision_log.
T1b: record_settlement_result with an empty record list is a no-op.
T3: record_settlement_result with missing decision_log table returns 0 (legacy skip).
"""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from src.execution.harvester import record_settlement_result
from src.state.decision_chain import SettlementRecord


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_trade_conn() -> sqlite3.Connection:
    """In-memory trade DB with decision_log table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decision_log (
            trade_id TEXT PRIMARY KEY,
            city TEXT,
            target_date TEXT,
            bin_label TEXT,
            direction TEXT,
            entry_price REAL,
            exit_price REAL,
            pnl REAL,
            strategy TEXT,
            source TEXT,
            settled_at TEXT,
            decision_snapshot_id INTEGER,
            edge_source TEXT
        )
    """)
    conn.commit()
    return conn


def _make_settlement_record(trade_id: str = "trade-001") -> SettlementRecord:
    return SettlementRecord(
        trade_id=trade_id,
        city="London",
        target_date="2026-05-01",
        range_label="16-17°C",
        direction="buy_yes",
        p_posterior=0.65,
        outcome=1,
        pnl=6.5,
        strategy="default",
        settled_at="2026-05-01T18:00:00Z",
        decision_snapshot_id="",
        edge_source="model",
    )


def _make_stage2_ready() -> dict:
    return {
        "stage2_status": "ready",
        "stage2_missing_trade_tables": [],
        "stage2_missing_shared_tables": [],
    }


def _make_stage2_missing_decision_log() -> dict:
    return {
        "stage2_status": "degraded",
        "stage2_missing_trade_tables": ["decision_log"],
        "stage2_missing_shared_tables": [],
    }


# ---------------------------------------------------------------------------
# T1: record_settlement_result writes decision_log rows
# ---------------------------------------------------------------------------

def test_T1_record_settlement_result_writes_decision_log():
    """record_settlement_result() writes the settlement fact."""
    conn = _make_trade_conn()
    records = [_make_settlement_record("trade-001"), _make_settlement_record("trade-002")]

    with patch("src.execution.harvester.store_settlement_records") as mock_store:
        n = record_settlement_result(conn, records, _make_stage2_ready())

    assert n == len(records)
    mock_store.assert_called_once()
    call_args = mock_store.call_args
    # Correct DB connection passed
    assert call_args[0][0] is conn
    # Correct records passed
    assert call_args[0][1] == records
    # source tag is "harvester"
    assert call_args[1].get("source") == "harvester"


def test_T1b_record_settlement_result_empty_list_returns_zero():
    conn = _make_trade_conn()
    with patch("src.execution.harvester.store_settlement_records") as mock_store:
        n = record_settlement_result(conn, [], _make_stage2_ready())
    assert n == 0
    mock_store.assert_not_called()


# ---------------------------------------------------------------------------
# T3: record_settlement_result with missing decision_log skips and returns 0
# ---------------------------------------------------------------------------

def test_T3_record_settlement_result_skips_when_decision_log_missing(caplog):
    """When decision_log is in stage2_missing_trade_tables, no write occurs and
    legacy_skip count is correct."""
    import logging
    conn = _make_trade_conn()
    records = [_make_settlement_record()]

    with patch("src.execution.harvester.store_settlement_records") as mock_store:
        with caplog.at_level(logging.WARNING, logger="src.execution.harvester"):
            n = record_settlement_result(conn, records, _make_stage2_missing_decision_log())

    assert n == 0
    mock_store.assert_not_called()
    assert any("decision_log missing" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Re-homed regression: stale retry_pending exit_state must not block a
# venue-verified settlement close (redeem-independent; see header note).
# ---------------------------------------------------------------------------

def _make_settlement_close_conn() -> sqlite3.Connection:
    """In-memory SQLite with the tables _settle_positions reads."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS position_current (
            trade_id TEXT PRIMARY KEY,
            city TEXT,
            target_date TEXT,
            phase TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decision_log (
            trade_id TEXT PRIMARY KEY,
            city TEXT,
            target_date TEXT,
            bin_label TEXT,
            direction TEXT,
            entry_price REAL,
            exit_price REAL,
            pnl REAL,
            strategy TEXT,
            source TEXT,
            settled_at TEXT,
            decision_snapshot_id INTEGER,
            edge_source TEXT
        )
    """)
    conn.commit()
    return conn


def _make_mock_portfolio_with_position(
    trade_id="trade-t2g-001",
    city="London",
    target_date="2026-05-01",
    direction="buy_yes",
    condition_id="cond-abc123",
    token_id="tok-yes-001",
    shares=100.0,
    entry_price=0.6,
):
    """Return a minimal mock PortfolioState with one position.

    Attributes are set so the position passes all the skip-guards in
    _settle_positions (state not in skip-set, direction valid, etc.).
    """
    pos = MagicMock()
    pos.trade_id = trade_id
    pos.city = city
    pos.target_date = target_date
    pos.direction = direction
    pos.condition_id = condition_id
    pos.token_id = token_id
    pos.no_token_id = None
    pos.entry_price = entry_price
    pos.shares = shares
    pos.p_posterior = 0.7
    pos.bin_label = "16-17°C"
    pos.exit_price = None
    pos.entry_method = "model"
    pos.selected_method = "model"
    pos.decision_snapshot_id = ""
    pos.edge_source = "model"
    pos.strategy = "default"
    pos.last_exit_at = "2026-05-01T18:00:00Z"
    pos.market_id = condition_id
    pos.state = "active"
    pos.exit_state = ""
    pos.chain_state = ""
    pos.temperature_metric = "high"

    portfolio = MagicMock()
    portfolio.positions = [pos]
    portfolio.ignored_tokens = []

    return portfolio, pos


def test_settle_positions_closes_losing_retry_pending_position(monkeypatch):
    """A venue-verified settlement outranks stale exit retry state.

    Regression coverage for the live Hong Kong 2026-06-12 failure mode: the
    position was buy_no, the YES bin won, and a stale retry_pending exit_state
    kept the harvester from writing SETTLED even though position_current.phase
    was still an open canonical phase.
    """
    import src.execution.harvester as hv
    import src.execution.exit_lifecycle as el

    conn = _make_settlement_close_conn()
    portfolio, pos = _make_mock_portfolio_with_position(
        trade_id="trade-hk-retry-loser",
        city="Hong Kong",
        target_date="2026-06-12",
        direction="buy_no",
        condition_id="0xhk",
        token_id="",
        shares=5.0,
        entry_price=0.72,
    )
    pos.no_token_id = "no-token"
    pos.bin_label = "Will the highest temperature in Hong Kong be 30°C on June 12?"
    pos.exit_state = "retry_pending"
    pos.chain_state = "synced"

    conn.execute(
        "INSERT INTO position_current (trade_id, city, target_date, phase) VALUES (?, ?, ?, ?)",
        (pos.trade_id, pos.city, pos.target_date, "active"),
    )
    conn.commit()

    monkeypatch.setattr(hv, "log_event", lambda *a, **kw: None)
    monkeypatch.setattr(hv, "log_settlement_event", lambda *a, **kw: None)
    monkeypatch.setattr(hv, "_dual_write_canonical_settlement_if_available", lambda *a, **kw: False)
    monkeypatch.setattr(hv, "record_token_suppression", lambda *a, **kw: {"status": "written"})
    monkeypatch.setattr(hv, "_settlement_economics_for_position", lambda p: (p.shares, p.entry_price * p.shares))

    closed = MagicMock()
    closed.trade_id = pos.trade_id
    closed.pnl = -3.6
    closed.bin_label = pos.bin_label
    closed.direction = pos.direction
    closed.p_posterior = pos.p_posterior
    closed.decision_snapshot_id = ""
    closed.edge_source = "model"
    closed.strategy = "default"
    closed.last_exit_at = "2026-06-17T10:46:18+00:00"
    closed.exit_price = 0.0
    mark_settled = MagicMock(return_value=closed)
    monkeypatch.setattr(el, "mark_settled", mark_settled)

    records = []
    settled = hv._settle_positions(
        conn,
        portfolio,
        city="Hong Kong",
        target_date="2026-06-12",
        winning_label="30°C",
        settlement_records=records,
        settlement_authority="VERIFIED",
        settlement_truth_source="forecasts.settlement_outcomes",
        settlement_temperature_metric="high",
    )

    assert settled == 1
    mark_settled.assert_called_once_with(portfolio, pos.trade_id, 0.0, "SETTLEMENT")
    assert len(records) == 1
    assert records[0].outcome == 0
    assert records[0].pnl == -3.6
