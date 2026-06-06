from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.check_live_release_gate import PASS, _check_paper_proof
from scripts.emit_live_release_paper_proof import build_proof


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _seed_forecasts(path: Path, now: datetime) -> None:
    with _connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE market_events (
                market_slug TEXT,
                city TEXT,
                target_date TEXT,
                temperature_metric TEXT,
                condition_id TEXT,
                created_at TEXT,
                recorded_at TEXT
            );
            CREATE TABLE readiness_state (
                readiness_id TEXT PRIMARY KEY,
                scope_key TEXT,
                scope_type TEXT,
                city_id TEXT,
                city TEXT,
                status TEXT,
                computed_at TEXT,
                expires_at TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO market_events VALUES (?,?,?,?,?,?,?)",
            (
                "weather-london",
                "London",
                "2026-06-07",
                "high",
                "cond-1",
                now.isoformat(),
                now.isoformat(),
            ),
        )
        conn.execute(
            "INSERT INTO readiness_state VALUES (?,?,?,?,?,?,?,?)",
            (
                "ready-1",
                "scope-1",
                "city_metric",
                "LONDON",
                "London",
                "LIVE_ELIGIBLE",
                now.isoformat(),
                (now + timedelta(hours=3)).isoformat(),
            ),
        )


def _seed_world(path: Path, now: datetime, *, pending_reconcile: int = 0) -> None:
    with _connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE edli_no_submit_receipts (
                receipt_id TEXT PRIMARY KEY,
                condition_id TEXT,
                side_effect_status TEXT,
                q_live REAL,
                q_lcb_5pct REAL,
                trade_score REAL,
                receipt_json TEXT,
                created_at TEXT
            );
            CREATE TABLE edli_live_order_events (
                aggregate_event_id TEXT PRIMARY KEY,
                event_type TEXT,
                created_at TEXT
            );
            CREATE TABLE decision_certificates (
                certificate_id TEXT PRIMARY KEY,
                certificate_type TEXT,
                persisted_at TEXT
            );
            CREATE TABLE edli_live_order_projection (
                aggregate_id TEXT PRIMARY KEY,
                pending_reconcile INTEGER,
                updated_at TEXT
            );
            CREATE TABLE exchange_reconcile_findings (
                finding_id TEXT PRIMARY KEY,
                resolved_at TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO edli_no_submit_receipts VALUES (?,?,?,?,?,?,?,?)",
            (
                "receipt-1",
                "cond-1",
                "NO_SUBMIT",
                0.8,
                0.7,
                0.1,
                '{"city":"London"}',
                now.isoformat(),
            ),
        )
        for event_type in (
            "DecisionProofAccepted",
            "SubmitPlanBuilt",
            "PreSubmitRevalidated",
            "LiveCapReserved",
            "ExecutionCommandCreated",
            "CapTransitioned",
        ):
            conn.execute(
                "INSERT INTO edli_live_order_events VALUES (?,?,?)",
                (f"event-{event_type}", event_type, now.isoformat()),
            )
        for certificate_type in (
            "FinalIntentCertificate",
            "PreSubmitRevalidationCertificate",
            "ExecutorExpressibilityCertificate",
            "ExecutionCommandCertificate",
            "ExecutionReceiptCertificate",
            "LiveCapCertificate",
            "LiveCapTransitionCertificate",
        ):
            conn.execute(
                "INSERT INTO decision_certificates VALUES (?,?,?)",
                (f"cert-{certificate_type}", certificate_type, now.isoformat()),
            )
        conn.execute(
            "INSERT INTO edli_live_order_projection VALUES (?,?,?)",
            ("agg-1", pending_reconcile, now.isoformat()),
        )


def _seed_trade(path: Path) -> None:
    with _connect(path) as conn:
        conn.execute(
            "CREATE TABLE settlement_commands (command_id TEXT PRIMARY KEY, state TEXT)"
        )


def test_emit_paper_proof_is_accepted_by_release_gate(tmp_path: Path) -> None:
    now = datetime(2026, 6, 6, 16, 0, tzinfo=timezone.utc)
    world = tmp_path / "world.db"
    forecasts = tmp_path / "forecasts.db"
    trade = tmp_path / "trade.db"
    proof_path = tmp_path / "paper_money_path_proof.json"

    _seed_world(world, now)
    _seed_forecasts(forecasts, now)
    _seed_trade(trade)
    proof = build_proof(world_db=world, forecasts_db=forecasts, trade_db=trade, now=now)
    proof_path.write_text(__import__("json").dumps(proof), encoding="utf-8")

    assert proof["status"] == PASS
    assert _check_paper_proof(proof_path).status == PASS


def test_emit_paper_proof_fails_on_pending_reconcile(tmp_path: Path) -> None:
    now = datetime(2026, 6, 6, 16, 0, tzinfo=timezone.utc)
    world = tmp_path / "world.db"
    forecasts = tmp_path / "forecasts.db"
    trade = tmp_path / "trade.db"

    _seed_world(world, now, pending_reconcile=1)
    _seed_forecasts(forecasts, now)
    _seed_trade(trade)

    proof = build_proof(world_db=world, forecasts_db=forecasts, trade_db=trade, now=now)

    assert proof["status"] == "FAIL"
    assert proof["reconcile"] is False
    assert proof["probes"]["reconcile"]["evidence"]["pending_reconcile"] == 1
