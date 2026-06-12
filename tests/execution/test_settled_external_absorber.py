# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: docs/evidence/settlement_guard/2026-06-11_settled_external_absorber_plan.md
#   — operator redeem-abandonment directive 2026-06-10 (third-party auto-redeem is the
#   standing policy); HK 06-09 incident: settled NO x19 swept off the shared wallet,
#   position_drift finding 6a477c8d unresolved 11h, M5 submit latch frozen, zero orders.
"""RELATIONSHIP tests: settled-class external close -> suppression -> latch freedom.

Cross-module invariant (reconcile resolver -> token_suppression -> finding resolution):
  A position_drift token whose market's target LOCAL day ended >= 24h ago (canonical
  registry authority), with venue size 0, a confirmed journal long, and no open sell
  locks, is the EXPECTED footprint of the operator's standing third-party auto-redeemer
  — it must be auto-registered in token_suppression ('settled_position') and its finding
  resolved, WITHOUT a per-token operator acknowledgment. Every other shape (market not
  terminal, venue still holding, open sell lock, registry unavailable) must stay an OPEN
  finding: the operator-ack door remains the only path for non-settled disappearances.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

import src.execution.exchange_reconcile as xr
import src.state.db as state_db
from src.execution.exchange_reconcile import (
    _SETTLED_EXTERNAL_RESOLUTION,
    _market_calendar_terminal_evidence,
    _resolve_position_drift_tokens_from_current_truth,
    init_exchange_reconcile_schema,
    record_finding,
)

NOW = datetime(2026, 6, 11, 10, 30, tzinfo=timezone.utc)
TOKEN = "43002927367061661305591090516749828572523174830019673318541620671727"  # the held NO side
YES_TOKEN = "37002767290866925317834458295773494445422665081252227034849232828492"
CONDITION = "0x70b824aa5fd4f3355cea55a681bd6ec8006a945ba813f1d2d95861d1bda30c45"

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
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES ('snap1', ?, ?, ?, ?)",
        (CONDITION, YES_TOKEN, TOKEN, TOKEN),
    )
    return conn


def _forecasts_db(tmp_path, *, target_date: str) -> str:
    path = tmp_path / "forecasts.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE market_events (token_id TEXT, market_slug TEXT, city TEXT,"
        " target_date TEXT, condition_id TEXT)"
    )
    # Production truth: the registry row carries ONLY the YES-side token. The held NO
    # token is reachable exclusively through the condition_id bridge — the exact shape
    # that kept the HK 06-09 sweep unresolvable when matching was token-only.
    conn.execute(
        "INSERT INTO market_events VALUES (?, 'highest-temperature-in-hong-kong', 'Hong Kong', ?, ?)",
        (YES_TOKEN, target_date, CONDITION),
    )
    conn.commit()
    conn.close()
    return str(path)


def _record_drift(conn: sqlite3.Connection) -> None:
    record_finding(
        conn,
        kind="position_drift",
        subject_id=TOKEN,
        context="ws_gap",
        evidence={"token_id": TOKEN, "exchange_size": "0", "journal_size": "19"},
        recorded_at=NOW,
    )


def _wire_sizes(monkeypatch, *, journal: str = "19", sell_locked: str = "0") -> None:
    monkeypatch.setattr(
        xr, "_journal_positions_by_token", lambda conn, states: {TOKEN: Decimal(journal)}
    )
    monkeypatch.setattr(
        xr, "_settlement_command_token_holdings_by_token", lambda conn: {}
    )
    monkeypatch.setattr(
        xr, "_closed_position_token_holdings_by_token", lambda conn: {}
    )
    monkeypatch.setattr(
        xr,
        "_live_open_sell_locked_tokens_by_token",
        lambda conn, open_orders: {TOKEN: Decimal(sell_locked)} if sell_locked != "0" else {},
    )


def _resolve(conn: sqlite3.Connection, *, positions: list) -> None:
    _resolve_position_drift_tokens_from_current_truth(
        conn,
        token_ids=(TOKEN,),
        positions=positions,
        open_orders=[],
        observed_at=NOW,
    )


def _finding_row(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT resolved_at, resolution FROM exchange_reconcile_findings WHERE subject_id = ?",
        (TOKEN,),
    ).fetchone()


def test_terminal_swept_winner_is_suppressed_and_resolved(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        state_db, "ZEUS_FORECASTS_DB_PATH", _forecasts_db(tmp_path, target_date="2026-06-09")
    )
    conn = _trades_conn()
    _record_drift(conn)
    _wire_sizes(monkeypatch)
    _resolve(conn, positions=[])  # venue holds nothing

    row = _finding_row(conn)
    assert row["resolved_at"] is not None
    assert row["resolution"] == _SETTLED_EXTERNAL_RESOLUTION
    suppression = conn.execute(
        "SELECT suppression_reason, source_module FROM token_suppression WHERE token_id = ?",
        (TOKEN,),
    ).fetchone()
    assert suppression["suppression_reason"] == "settled_position"
    assert suppression["source_module"] == "exchange_reconcile.settled_external_absorber"
    history = conn.execute(
        "SELECT COUNT(*) FROM token_suppression_history WHERE token_id = ?", (TOKEN,)
    ).fetchone()[0]
    assert history == 1

    # Future sweeps resolve through the suppression door (idempotent, no re-register).
    _record_drift(conn)
    _resolve(conn, positions=[])
    rows = conn.execute(
        "SELECT resolution FROM exchange_reconcile_findings WHERE subject_id = ? AND resolved_at IS NOT NULL",
        (TOKEN,),
    ).fetchall()
    assert len(rows) == 2
    assert rows[1]["resolution"] == "position_drift_token_suppressed_external"


def test_non_terminal_market_stays_open_finding(monkeypatch, tmp_path) -> None:
    # Market's target day is TODAY — not terminal; the disappearance could be theft/bug.
    monkeypatch.setattr(
        state_db, "ZEUS_FORECASTS_DB_PATH", _forecasts_db(tmp_path, target_date="2026-06-11")
    )
    conn = _trades_conn()
    _record_drift(conn)
    _wire_sizes(monkeypatch)
    _resolve(conn, positions=[])

    row = _finding_row(conn)
    assert row["resolved_at"] is None, (
        "a NON-terminal disappearance must stay an open finding (operator-ack only)"
    )
    assert (
        conn.execute("SELECT COUNT(*) FROM token_suppression").fetchone()[0] == 0
    )


def test_open_sell_lock_blocks_absorption(monkeypatch, tmp_path) -> None:
    # PARTIAL sell lock (5 of 19): books do not match (available 14 vs venue 0) and the
    # settled-class branch must NOT absorb while any of the long is venue-live in a sell
    # order — that shape is an in-flight exit, not an external sweep. (A FULL lock is
    # legitimately resolved earlier by the size-match path: venue 0 == available 0.)
    monkeypatch.setattr(
        state_db, "ZEUS_FORECASTS_DB_PATH", _forecasts_db(tmp_path, target_date="2026-06-09")
    )
    conn = _trades_conn()
    _record_drift(conn)
    _wire_sizes(monkeypatch, sell_locked="5")
    _resolve(conn, positions=[])
    assert _finding_row(conn)["resolved_at"] is None
    assert conn.execute("SELECT COUNT(*) FROM token_suppression").fetchone()[0] == 0


def test_registry_unavailable_fails_closed(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        state_db, "ZEUS_FORECASTS_DB_PATH", str(tmp_path / "missing.db")
    )
    assert _market_calendar_terminal_evidence((TOKEN,), observed_at=NOW) == {}
    conn = _trades_conn()
    _record_drift(conn)
    _wire_sizes(monkeypatch)
    _resolve(conn, positions=[])
    assert _finding_row(conn)["resolved_at"] is None


def test_terminal_evidence_respects_local_day_plus_buffer(monkeypatch, tmp_path) -> None:
    # HK local day 06-09 ends 2026-06-09T16:00Z; +24h buffer => terminal from 06-10T16:00Z.
    monkeypatch.setattr(
        state_db, "ZEUS_FORECASTS_DB_PATH", _forecasts_db(tmp_path, target_date="2026-06-09")
    )
    before = datetime(2026, 6, 10, 15, 0, tzinfo=timezone.utc)
    after = datetime(2026, 6, 10, 17, 0, tzinfo=timezone.utc)
    bridge = {TOKEN: CONDITION}
    assert (
        _market_calendar_terminal_evidence((TOKEN,), observed_at=before, conditions_by_token=bridge)
        == {}
    )
    evidence = _market_calendar_terminal_evidence(
        (TOKEN,), observed_at=after, conditions_by_token=bridge
    )
    assert TOKEN in evidence
    assert evidence[TOKEN]["city"] == "Hong Kong"
    assert evidence[TOKEN]["condition_id"] == CONDITION
    assert evidence[TOKEN]["matched_via"] == "condition_id_bridge"
    # YES-side token still matches directly (registry row token).
    direct = _market_calendar_terminal_evidence((YES_TOKEN,), observed_at=after)
    assert YES_TOKEN in direct and "matched_via" not in direct[YES_TOKEN]
    # Without the bridge, the NO side is unreachable — fail-closed, not guessed.
    assert _market_calendar_terminal_evidence((TOKEN,), observed_at=after) == {}
