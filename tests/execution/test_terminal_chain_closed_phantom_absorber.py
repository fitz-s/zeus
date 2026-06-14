# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: docs/evidence/investigation_2026-06-13/b1_swept_winner_latch_fix.md
#   — live regression 2026-06-13: M5 ws-gap submit latch frozen allow_submit=False since
#   2026-06-12T22:58Z by ONE unresolved position_drift finding 5bbc2be2 (token swept off the
#   shared wallet by the third-party auto-redeemer; venue size 0 against a terminal
#   voided/synced position_current chain-holding of 17.05). The settled-external absorber
#   (task #31, commit 6629d35a) lives ONLY on the refresh path AND is blind during the window
#   before the market's target local day is +24h past — exactly the window this freeze fell in.
"""RELATIONSHIP tests: terminal-chain-closed swept-winner phantom -> suppression -> latch freedom.

Cross-module invariant (reconcile drift detector -> token_suppression -> finding resolution
-> ws_gap submit latch):
  A position_drift token whose external close is already proven ON-CHAIN — venue size 0
  against a terminal (voided/settled/admin_closed) chain-holdings row, with no live sell lock —
  is the EXPECTED footprint of the operator's standing third-party auto-redeemer. It must be
  auto-absorbed (token_suppression 'settled_position') and its finding resolved IMMEDIATELY,
  WITHOUT waiting for the market-calendar +24h terminal buffer and WITHOUT a per-token operator
  acknowledgment — on BOTH the full-sweep path (run_reconcile_sweep -> _record_position_drift_findings,
  the path run_ws_gap_reconcile_and_clear actually runs) AND the 1-minute refresh path
  (_resolve_position_drift_tokens_from_current_truth).

RED-on-revert: deleting either absorber call site (or the _absorb_terminal_chain_closed_phantom
helper) leaves the finding OPEN, which keeps the M5 submit latch frozen — these tests fail.

Honest-gate preserved: a disappearance with NO terminal chain-holdings row never matches and still
routes to the operator-ack path (theft/bug surface intact); an open sell lock blocks absorption.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

import src.execution.exchange_reconcile as xr
import src.state.db as state_db
from src.control import ws_gap_guard
from src.execution.exchange_reconcile import (
    _TERMINAL_CHAIN_CLOSED_RESOLUTION,
    _record_position_drift_findings,
    _resolve_position_drift_tokens_from_current_truth,
    init_exchange_reconcile_schema,
    list_unresolved_findings,
    record_finding,
)

# Live shape: held NO side of the Denver 06-12 market, swept off-chain. NOW is 06-13T12:00Z:
# Denver local day 06-12 ENDED at 06-13T06:00Z (so day-end terminal evidence IS available) but
# the +24h buffer (06-14T06:00Z) has NOT elapsed — exactly the blind window of task #31's
# calendar absorber that froze the live latch. The fix must fire from day-end alone.
NOW = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)
TARGET_DATE = "2026-06-12"
TOKEN = "25998072565711727698258544609688934677406873903623466853003437606533488235694"  # held NO side
YES_TOKEN = "37002767290866925317834458295773494445422665081252227034849232828492"
CONDITION = "0xaa77bbce1087350c1115fac14acd06163fd01fac864daf4b100eadab2aa1f9a2"
JOURNAL = Decimal("17.05")
CLOSED_HOLDING = Decimal("17.05")

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
CREATE TABLE executable_market_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  condition_id TEXT,
  yes_token_id TEXT,
  no_token_id TEXT,
  selected_outcome_token_id TEXT
);
"""


def _trades_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_exchange_reconcile_schema(conn)
    conn.executescript(_SUPPRESSION_DDL)
    # Registry carries ONLY the YES-side token; the held NO token is reachable only via the
    # condition_id bridge — the production shape (and the live Denver NO-side phantom).
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES ('snap1', ?, ?, ?, ?)",
        (CONDITION, YES_TOKEN, TOKEN, TOKEN),
    )
    return conn


def _forecasts_db(tmp_path, *, target_date: str) -> str:
    """A canonical market_events registry whose row carries the YES-side token only."""

    path = tmp_path / "forecasts.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE market_events (token_id TEXT, market_slug TEXT, city TEXT,"
        " target_date TEXT, condition_id TEXT)"
    )
    conn.execute(
        "INSERT INTO market_events VALUES (?, 'highest-temperature-in-denver', 'Denver', ?, ?)",
        (YES_TOKEN, target_date, CONDITION),
    )
    conn.commit()
    conn.close()
    return str(path)


def _wire_terminal_chain_closed_phantom(
    monkeypatch,
    tmp_path,
    *,
    closed_holding: Decimal = CLOSED_HOLDING,
    sell_locked: Decimal = Decimal("0"),
    target_date: str = TARGET_DATE,
) -> None:
    """Inject the swept-winner phantom shape: venue 0, confirmed journal long, a terminal
    chain-holding, on a market whose target local day has ENDED but is NOT yet +24h past.

    The market-calendar absorber (task #31, +24h buffer) is therefore still BLIND here — the
    ONLY thing that can clear the finding is the terminal-chain-closed absorber under test,
    which trusts day-end because the on-chain close is already proven."""

    monkeypatch.setattr(
        state_db, "ZEUS_FORECASTS_DB_PATH", _forecasts_db(tmp_path, target_date=target_date)
    )
    monkeypatch.setattr(
        xr, "_journal_positions_by_token", lambda conn, states: {TOKEN: JOURNAL}
    )
    monkeypatch.setattr(xr, "_settlement_command_token_holdings_by_token", lambda conn: {})
    monkeypatch.setattr(
        xr,
        "_closed_position_token_holdings_by_token",
        lambda conn: {TOKEN: closed_holding} if closed_holding > 0 else {},
    )
    monkeypatch.setattr(
        xr,
        "_live_open_sell_locked_tokens_by_token",
        lambda conn, open_orders: {TOKEN: sell_locked} if sell_locked > 0 else {},
    )


def _record_drift(conn: sqlite3.Connection) -> None:
    record_finding(
        conn,
        kind="position_drift",
        subject_id=TOKEN,
        context="ws_gap",
        evidence={
            "token_id": TOKEN,
            "exchange_size": "0",
            "confirmed_wallet_size": str(JOURNAL),
            "closed_position_token_size": str(CLOSED_HOLDING),
            "expected_wallet_size": str(JOURNAL + CLOSED_HOLDING),
            "closed_position_evidence_class": "terminal_position_current_chain_holdings",
        },
        recorded_at=NOW,
    )


def _finding_row(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT resolved_at, resolution FROM exchange_reconcile_findings WHERE subject_id = ?",
        (TOKEN,),
    ).fetchone()


def _suppression_row(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT suppression_reason, source_module FROM token_suppression WHERE token_id = ?",
        (TOKEN,),
    ).fetchone()


# ---- Full-sweep path (run_reconcile_sweep -> _record_position_drift_findings) -----------------


def test_full_sweep_absorbs_terminal_chain_closed_phantom(monkeypatch, tmp_path) -> None:
    """The path run_ws_gap_reconcile_and_clear actually runs must NOT record a blocking
    finding for a terminal-chain-closed phantom — it must absorb + suppress it instead."""

    conn = _trades_conn()
    _wire_terminal_chain_closed_phantom(monkeypatch, tmp_path)

    findings = _record_position_drift_findings(
        conn,
        positions=[],  # venue holds nothing for the token
        open_orders=[],
        context="ws_gap",
        observed_at=NOW,
    )

    # (a) latch freedom: NO unresolved position_drift finding is produced for this token.
    assert all(f.subject_id != TOKEN for f in findings), (
        "terminal-chain-closed phantom must NOT record a blocking position_drift finding"
    )
    assert not any(
        f.subject_id == TOKEN for f in list_unresolved_findings(conn)
    ), "no unresolved finding may remain -> ws_gap zero-findings latch can clear"

    # (b) absorbed: token registered in the suppression registry as a settled winner.
    suppression = _suppression_row(conn)
    assert suppression is not None
    assert suppression["suppression_reason"] == "settled_position"
    assert (
        suppression["source_module"]
        == "exchange_reconcile.terminal_chain_closed_phantom_absorber"
    )


def test_full_sweep_resolves_preexisting_stuck_finding(monkeypatch, tmp_path) -> None:
    """The EXISTING stuck finding (the live 5bbc2be2 shape) is re-evaluated and resolved by
    the corrected sweep — by absorption, never by a manual resolved_at write."""

    conn = _trades_conn()
    _record_drift(conn)
    assert _finding_row(conn)["resolved_at"] is None  # starts frozen/open
    _wire_terminal_chain_closed_phantom(monkeypatch, tmp_path)

    _record_position_drift_findings(
        conn, positions=[], open_orders=[], context="ws_gap", observed_at=NOW
    )

    row = _finding_row(conn)
    assert row["resolved_at"] is not None, "the stuck finding must be resolved by re-evaluation"
    assert row["resolution"] == _TERMINAL_CHAIN_CLOSED_RESOLUTION


# ---- Refresh path (_resolve_position_drift_tokens_from_current_truth) -------------------------


def test_refresh_path_resolves_terminal_chain_closed_phantom(monkeypatch, tmp_path) -> None:
    conn = _trades_conn()
    _record_drift(conn)
    _wire_terminal_chain_closed_phantom(monkeypatch, tmp_path)

    _resolve_position_drift_tokens_from_current_truth(
        conn,
        token_ids=(TOKEN,),
        positions=[],
        open_orders=[],
        observed_at=NOW,
    )

    row = _finding_row(conn)
    assert row["resolved_at"] is not None
    assert row["resolution"] == _TERMINAL_CHAIN_CLOSED_RESOLUTION
    assert _suppression_row(conn)["suppression_reason"] == "settled_position"


def test_idempotent_via_suppression_door(monkeypatch, tmp_path) -> None:
    """Once suppressed, a future sweep resolves through the suppression door — no churn."""

    conn = _trades_conn()
    _record_drift(conn)
    _wire_terminal_chain_closed_phantom(monkeypatch, tmp_path)
    _record_position_drift_findings(
        conn, positions=[], open_orders=[], context="ws_gap", observed_at=NOW
    )
    assert _suppression_row(conn) is not None

    # Re-record (a fresh sweep produced another drift row) and resolve again.
    _record_drift(conn)
    _resolve_position_drift_tokens_from_current_truth(
        conn, token_ids=(TOKEN,), positions=[], open_orders=[], observed_at=NOW
    )
    resolved = conn.execute(
        "SELECT resolution FROM exchange_reconcile_findings "
        "WHERE subject_id = ? AND resolved_at IS NOT NULL ORDER BY recorded_at",
        (TOKEN,),
    ).fetchall()
    assert len(resolved) == 2
    # The second resolution comes through the suppression registry door, not a re-register.
    assert resolved[-1]["resolution"] == "position_drift_token_suppressed_external"
    # Exactly one suppression history row — the absorber did not re-register.
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM token_suppression_history WHERE token_id = ?", (TOKEN,)
        ).fetchone()[0]
        == 1
    )


# ---- Honest-gate preservation (the theft/bug surface must stay armed) -------------------------


def test_no_terminal_holding_stays_open_finding(monkeypatch, tmp_path) -> None:
    """A venue-zero disappearance with NO terminal chain-holdings row is NOT proven closed —
    it must stay an OPEN finding (operator-ack path only), keeping the kill switch armed."""

    conn = _trades_conn()
    _wire_terminal_chain_closed_phantom(monkeypatch, tmp_path, closed_holding=Decimal("0"))

    findings = _record_position_drift_findings(
        conn, positions=[], open_orders=[], context="ws_gap", observed_at=NOW
    )
    assert any(f.subject_id == TOKEN for f in findings), (
        "no terminal chain-holding -> unproven disappearance -> must stay an open finding"
    )
    assert _suppression_row(conn) is None


def test_open_sell_lock_blocks_absorption(monkeypatch, tmp_path) -> None:
    """A live SELL lock means an in-flight exit, not an external sweep — do not absorb."""

    conn = _trades_conn()
    _wire_terminal_chain_closed_phantom(monkeypatch, tmp_path, sell_locked=Decimal("5"))

    findings = _record_position_drift_findings(
        conn, positions=[], open_orders=[], context="ws_gap", observed_at=NOW
    )
    assert any(f.subject_id == TOKEN for f in findings)
    assert _suppression_row(conn) is None


def test_open_market_disappearance_stays_open_finding(monkeypatch, tmp_path) -> None:
    """The operator-external-close separation property: a terminal chain-holding with venue 0
    on a market that is NOT yet settled (target day is TODAY) is NOT a settled-winner sweep —
    it must stay an OPEN finding and route to the strict operator-ack path. This is exactly
    what keeps test_reconcile_operator_external_close's open-market negative controls valid."""

    conn = _trades_conn()
    # Target day is NOW's date -> the local day has NOT ended -> no day-end terminal evidence.
    _wire_terminal_chain_closed_phantom(monkeypatch, tmp_path, target_date="2026-06-13")

    findings = _record_position_drift_findings(
        conn, positions=[], open_orders=[], context="ws_gap", observed_at=NOW
    )
    assert any(f.subject_id == TOKEN for f in findings), (
        "an unsettled (open-market) disappearance must stay fail-closed (operator-ack only)"
    )
    assert _suppression_row(conn) is None


def test_registry_unavailable_stays_open_finding(monkeypatch, tmp_path) -> None:
    """No canonical registry -> no day-end evidence -> fail-closed (operator-ack only)."""

    conn = _trades_conn()
    _wire_terminal_chain_closed_phantom(monkeypatch, tmp_path)
    # Override the registry to a non-existent DB so calendar evidence cannot resolve.
    monkeypatch.setattr(state_db, "ZEUS_FORECASTS_DB_PATH", str(tmp_path / "missing.db"))

    findings = _record_position_drift_findings(
        conn, positions=[], open_orders=[], context="ws_gap", observed_at=NOW
    )
    assert any(f.subject_id == TOKEN for f in findings)
    assert _suppression_row(conn) is None


# ---- End-to-end latch-freedom proof (the absorbed token does not block submit) ----------------


def test_suppressed_token_does_not_keep_ws_gap_latch_closed(monkeypatch, tmp_path) -> None:
    """After absorption there are zero unresolved findings, which is the exact precondition
    clear_after_m5_reconcile requires to reopen the submit latch."""

    conn = _trades_conn()
    _record_drift(conn)
    _wire_terminal_chain_closed_phantom(monkeypatch, tmp_path)
    _record_position_drift_findings(
        conn, positions=[], open_orders=[], context="ws_gap", observed_at=NOW
    )
    unresolved = list_unresolved_findings(conn)
    assert len(unresolved) == 0

    # With zero unresolved findings + a healthy subscription, the latch clears (does not raise).
    ws_gap_guard.clear_for_test(observed_at=NOW)
    ws_gap_guard.record_gap("ws_gap_test", subscription_state="SUBSCRIBED", observed_at=NOW)
    ws_gap_guard.record_message(subscription_state="SUBSCRIBED", observed_at=NOW)
    status = ws_gap_guard.clear_after_m5_reconcile(
        observed_at=NOW,
        findings_count=len(unresolved),
        unresolved_findings_count=len(unresolved),
    )
    assert status.m5_reconcile_required is False
    assert status.blocks_market(TOKEN, now=NOW) is False
