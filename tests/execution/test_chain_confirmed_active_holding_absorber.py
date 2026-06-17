# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: live regression 2026-06-16/17 — the M5 ws-gap submit latch was frozen
#   allow_submit=False for hours by ONE unresolved position_drift finding 3c7427cf (Seoul
#   buy_no 10.86): position_current chain_state='synced'/chain_shares=10.86 (on-chain
#   balanceOf truth) vs confirmed_journal=0 — a fill that arrived during a user-channel ws_gap
#   and was confirmed ONLY on-chain, never written as a journaled trade. The reconciler's
#   wallet-truth basis is the journal, so the exchange position (10.86) stayed permanently
#   unexplained, re-recording the drift every sweep. Every new submit failed
#   "EDLI_LIVE_CERTIFICATE_BUILD_FAILED: PreSubmitRevalidated requires user_ws_status=OK".
"""RELATIONSHIP tests: on-chain-confirmed ACTIVE holding -> finding resolution -> latch freedom.

Cross-module invariant (chain reconciler "chain is truth" -> position_drift detector ->
finding resolution -> ws_gap submit latch):
  A position_drift token whose exchange (CLOB) position MATCHES the independently-verified
  on-chain CTF balance (position_current chain_state='synced', chain_shares) is NOT an
  unexplained drift — both venue surfaces agree, so there is no missing exposure. It must be
  resolved (not re-recorded) on BOTH the full-sweep path (run_reconcile_sweep ->
  _record_position_drift_findings, the path run_ws_gap_reconcile_and_clear actually runs) AND
  the 1-minute refresh path (_resolve_position_drift_tokens_from_current_truth), so the M5
  zero-findings precondition can clear the submit latch.

RED-on-revert: deleting either absorber call site (or the helper) leaves the chain-confirmed
finding OPEN, which keeps the M5 submit latch frozen -> the chain-confirmed tests fail.

Honest-gate preserved: a token whose exchange position does NOT match an on-chain holding
(no holding, or a size mismatch) is a genuine unexplained drift and must stay an OPEN finding
(the theft/bug/loss surface), keeping the kill switch armed.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

import src.execution.exchange_reconcile as xr
from src.execution.exchange_reconcile import (
    _record_position_drift_findings,
    _resolve_position_drift_tokens_from_current_truth,
    init_exchange_reconcile_schema,
    list_unresolved_findings,
    record_finding,
)

NOW = datetime(2026, 6, 17, 5, 30, tzinfo=timezone.utc)
# Live shape: held NO side of the Seoul 06-18 market, filled during a ws_gap.
TOKEN = "8804511921994781915341839487724159686730490015520120342964759402579900432593"
EXCHANGE = Decimal("10.86")
CHAIN = Decimal("10.86")
_RESOLUTION = "position_drift_chain_confirmed_active_holding"

_SUPPRESSION_DDL = """
CREATE TABLE token_suppression_history (
  history_id INTEGER PRIMARY KEY AUTOINCREMENT,
  token_id TEXT NOT NULL,
  condition_id TEXT,
  suppression_reason TEXT NOT NULL,
  source_module TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  evidence_json TEXT,
  operation TEXT NOT NULL,
  recorded_at TEXT NOT NULL
);
CREATE TABLE token_suppression (
  token_id TEXT PRIMARY KEY,
  condition_id TEXT,
  suppression_reason TEXT NOT NULL,
  source_module TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  evidence_json TEXT
);
CREATE TABLE venue_commands (
  command_id TEXT PRIMARY KEY,
  token_id TEXT,
  state TEXT,
  updated_at TEXT
);
"""


def _trades_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_exchange_reconcile_schema(conn)
    conn.executescript(_SUPPRESSION_DDL)
    return conn


def _wire(monkeypatch, *, chain_confirmed: Decimal = CHAIN, exchange: Decimal = EXCHANGE) -> None:
    """Inject the ws_gap journal-gap shape: venue holds `exchange`, the on-chain reconciler
    confirms `chain_confirmed`, the confirmed-trade-facts journal is EMPTY (the fill was never
    journaled), and there is no settlement/closed/sell-lock evidence."""

    monkeypatch.setattr(
        xr, "_exchange_positions_by_token",
        lambda positions: ({TOKEN: exchange} if exchange > 0 else {}),
    )
    monkeypatch.setattr(xr, "_journal_positions_by_token", lambda conn, states: {})
    monkeypatch.setattr(xr, "_settlement_command_token_holdings_by_token", lambda conn: {})
    monkeypatch.setattr(xr, "_closed_position_token_holdings_by_token", lambda conn: {})
    monkeypatch.setattr(xr, "_live_open_sell_locked_tokens_by_token", lambda conn, open_orders: {})
    monkeypatch.setattr(
        xr, "_chain_confirmed_active_holdings_by_token",
        lambda conn: ({TOKEN: chain_confirmed} if chain_confirmed > 0 else {}),
    )
    # Refresh-path-only top-of-function calendar lookups — neutralise (the absorber under test
    # fires before calendar evidence is consulted).
    monkeypatch.setattr(xr, "_market_calendar_terminal_evidence", lambda *a, **k: {})
    monkeypatch.setattr(xr, "_condition_ids_for_tokens", lambda conn, tokens: {})


def _record_drift(conn: sqlite3.Connection) -> None:
    record_finding(
        conn,
        kind="position_drift",
        subject_id=TOKEN,
        context="ws_gap",
        evidence={
            "token_id": TOKEN,
            "exchange_size": str(EXCHANGE),
            "confirmed_journal_size": "0",
            "reason": "exchange_position_differs_from_confirmed_trade_facts",
        },
        recorded_at=NOW,
    )


def _finding_row(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT resolved_at, resolution FROM exchange_reconcile_findings WHERE subject_id = ?",
        (TOKEN,),
    ).fetchone()


# ---- Full-sweep path (run_reconcile_sweep -> _record_position_drift_findings) ----------------


def test_full_sweep_does_not_record_chain_confirmed_active_holding(monkeypatch) -> None:
    conn = _trades_conn()
    _wire(monkeypatch)

    findings = _record_position_drift_findings(
        conn, positions=[object()], open_orders=[], context="ws_gap", observed_at=NOW
    )

    assert all(f.subject_id != TOKEN for f in findings), (
        "exchange position matching the on-chain holding must NOT record a blocking drift"
    )
    assert not any(f.subject_id == TOKEN for f in list_unresolved_findings(conn)), (
        "no unresolved finding may remain -> ws_gap zero-findings latch can clear"
    )


def test_full_sweep_resolves_preexisting_stuck_finding(monkeypatch) -> None:
    conn = _trades_conn()
    _record_drift(conn)
    assert _finding_row(conn)["resolved_at"] is None  # starts frozen/open
    _wire(monkeypatch)

    _record_position_drift_findings(
        conn, positions=[object()], open_orders=[], context="ws_gap", observed_at=NOW
    )

    row = _finding_row(conn)
    assert row["resolved_at"] is not None, "the stuck finding must be resolved by re-evaluation"
    assert row["resolution"] == _RESOLUTION


# ---- Refresh path (_resolve_position_drift_tokens_from_current_truth) ------------------------


def test_refresh_path_resolves_chain_confirmed_active_holding(monkeypatch) -> None:
    conn = _trades_conn()
    _record_drift(conn)
    _wire(monkeypatch)

    _resolve_position_drift_tokens_from_current_truth(
        conn, token_ids=(TOKEN,), positions=[object()], open_orders=[], observed_at=NOW
    )

    row = _finding_row(conn)
    assert row["resolved_at"] is not None
    assert row["resolution"] == _RESOLUTION


# ---- Honest-gate preservation (the unexplained-drift surface must stay armed) ----------------


def test_no_chain_holding_stays_open_finding(monkeypatch) -> None:
    """Venue holds 10.86 but the on-chain reconciler confirms NOTHING -> genuine unexplained
    exposure -> must stay an OPEN finding (do not blanket-resolve on exchange presence alone)."""

    conn = _trades_conn()
    _wire(monkeypatch, chain_confirmed=Decimal("0"))

    findings = _record_position_drift_findings(
        conn, positions=[object()], open_orders=[], context="ws_gap", observed_at=NOW
    )
    assert any(f.subject_id == TOKEN for f in findings), (
        "no on-chain holding -> unexplained exposure -> finding must stay open"
    )


def test_size_mismatch_stays_open_finding(monkeypatch) -> None:
    """Exchange 10.86 vs on-chain 5.0 do NOT agree -> a real partial drift -> stay open."""

    conn = _trades_conn()
    _wire(monkeypatch, chain_confirmed=Decimal("5.0"))

    findings = _record_position_drift_findings(
        conn, positions=[object()], open_orders=[], context="ws_gap", observed_at=NOW
    )
    assert any(f.subject_id == TOKEN for f in findings), (
        "exchange/on-chain size mismatch must stay a fail-closed open finding"
    )


# ---- End-to-end latch-freedom proof ----------------------------------------------------------


def test_zero_unresolved_after_absorption(monkeypatch) -> None:
    conn = _trades_conn()
    _record_drift(conn)
    _wire(monkeypatch)
    _record_position_drift_findings(
        conn, positions=[object()], open_orders=[], context="ws_gap", observed_at=NOW
    )
    assert len(list_unresolved_findings(conn)) == 0, (
        "zero unresolved findings is the exact precondition clear_after_m5_reconcile requires"
    )
