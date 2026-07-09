# Lifecycle: created=2026-07-09; last_reviewed=2026-07-09; last_reused=2026-07-09
# Purpose: Regression tests for active ENTRY q_version repair.
# Reuse: pytest tests/test_repair_active_entry_q_versions.py
# Authority basis: AGENTS.md probability/execution proof gates.

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts import repair_active_entry_q_versions as repair


def _init_trade_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE venue_commands (
                command_id TEXT PRIMARY KEY,
                position_id TEXT,
                decision_id TEXT,
                snapshot_id TEXT,
                intent_kind TEXT,
                state TEXT,
                created_at TEXT,
                q_version TEXT,
                price REAL,
                size REAL
            );
            CREATE TABLE position_current (
                position_id TEXT PRIMARY KEY,
                phase TEXT,
                order_status TEXT,
                shares REAL,
                chain_shares REAL,
                decision_snapshot_id TEXT,
                city TEXT,
                target_date TEXT,
                bin_label TEXT,
                direction TEXT,
                p_posterior REAL
            );
            CREATE TABLE decision_certificates (
                certificate_id TEXT PRIMARY KEY,
                certificate_hash TEXT,
                certificate_type TEXT,
                semantic_key TEXT,
                decision_time TEXT,
                created_at TEXT,
                payload_json TEXT
            );
            CREATE TABLE decision_certificate_edges (
                child_certificate_id TEXT,
                parent_role TEXT,
                parent_certificate_hash TEXT
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _insert_active_entry(
    db_path: Path,
    *,
    command_id: str = "cmd-entry",
    position_id: str = "pos-entry",
    snapshot_id: str = "snap-entry",
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, order_status, shares, chain_shares,
                decision_snapshot_id, city, target_date, bin_label, direction,
                p_posterior
            ) VALUES (?, 'active', 'filled', 5.0, 5.0, ?, 'Paris', '2026-07-09',
                      'Will the lowest temperature in Paris be 20C on July 9?',
                      'buy_no', 0.84)
            """,
            (position_id, snapshot_id),
        )
        conn.execute(
            """
            INSERT INTO venue_commands (
                command_id, position_id, decision_id, snapshot_id, intent_kind,
                state, created_at, q_version, price, size
            ) VALUES (?, ?, 'decision-entry', ?, 'ENTRY', 'FILLED',
                      '2026-07-09T00:00:00+00:00', NULL, 0.61, 5.0)
            """,
            (command_id, position_id, snapshot_id),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_final_intent(
    db_path: Path,
    *,
    certificate_id: str = "fc-1",
    certificate_hash: str = "hash-fc-1",
    snapshot_id: str = "snap-entry",
    q_version: str = "posterior-q-1",
) -> None:
    payload = {
        "executable_snapshot_id": snapshot_id,
        "q_live": 0.841,
        "q_lcb_5pct": 0.803,
        "selection_authority_applied": "replacement_qkernel",
        "decision_source_context": {
            "snapshot_id": snapshot_id,
            "posterior_identity_hash": q_version,
            "forecast_source_id": "forecast-source-1",
            "source_available_at": "2026-07-08T23:00:00+00:00",
            "forecast_available_at": "2026-07-08T23:05:00+00:00",
        },
    }
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO decision_certificates (
                certificate_id, certificate_hash, certificate_type, semantic_key,
                decision_time, created_at, payload_json
            ) VALUES (?, ?, 'FinalIntentCertificate', 'final-intent',
                      '2026-07-09T00:00:00+00:00',
                      '2026-07-09T00:00:01+00:00', ?)
            """,
            (certificate_id, certificate_hash, json.dumps(payload, sort_keys=True)),
        )
        conn.commit()
    finally:
        conn.close()


def _q_version(db_path: Path, command_id: str = "cmd-entry") -> str | None:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT q_version FROM venue_commands WHERE command_id = ?",
            (command_id,),
        ).fetchone()
        return row[0]
    finally:
        conn.close()


def test_dry_run_reconstructs_active_entry_without_writing(tmp_path: Path) -> None:
    db_path = tmp_path / "zeus_trades.db"
    _init_trade_db(db_path)
    _insert_active_entry(db_path)
    _insert_final_intent(db_path)

    result = repair.run(db_path=db_path, apply=False)

    assert result["ok"] is True
    assert result["apply"] is False
    assert result["candidate_count"] == 1
    assert result["blocked_count"] == 0
    assert result["candidates"][0]["q_version"] == "posterior-q-1"
    assert result["venue_action"] is False
    assert result["db_backup_created"] is False
    assert _q_version(db_path) is None


def test_apply_writes_only_missing_entry_q_version(tmp_path: Path) -> None:
    db_path = tmp_path / "zeus_trades.db"
    _init_trade_db(db_path)
    _insert_active_entry(db_path)
    _insert_final_intent(db_path)

    result = repair.run(db_path=db_path, apply=True)

    assert result["ok"] is True
    assert result["applied_count"] == 1
    assert result["applied"] == [
        {"command_id": "cmd-entry", "q_version": "posterior-q-1"}
    ]
    assert _q_version(db_path) == "posterior-q-1"
    second = repair.run(db_path=db_path, apply=False)
    assert second["active_missing_count"] == 0
    assert second["candidate_count"] == 0


def test_apply_refuses_when_certificate_is_ambiguous(tmp_path: Path) -> None:
    db_path = tmp_path / "zeus_trades.db"
    _init_trade_db(db_path)
    _insert_active_entry(db_path)
    _insert_final_intent(
        db_path,
        certificate_id="fc-1",
        certificate_hash="hash-fc-1",
        q_version="posterior-q-1",
    )
    _insert_final_intent(
        db_path,
        certificate_id="fc-2",
        certificate_hash="hash-fc-2",
        q_version="posterior-q-2",
    )

    result = repair.run(db_path=db_path, apply=True)

    assert result["ok"] is False
    assert result["issue"] == "Q_VERSION_REPAIR_BLOCKED:n=1"
    assert result["blocked_count"] == 1
    assert result["blocked"][0]["reason"] == "ambiguous_final_intent_certificate"
    assert result["applied_count"] == 0
    assert _q_version(db_path) is None
