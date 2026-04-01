#!/usr/bin/env python3
"""Backfill trade_decisions attribution from current paper state and recent exits."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_connection, init_schema

POSITIONS_PATH = PROJECT_ROOT / "state" / "positions-paper.json"


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _distance_seconds(a: str | None, b: str | None) -> float:
    da = _parse_ts(a)
    db = _parse_ts(b)
    if da is None or db is None:
        return 1e18
    return abs((da - db).total_seconds())


def _best_row(conn, *, market_id: str, bin_label: str, direction: str, status: str, target_ts: str | None):
    rows = conn.execute(
        """
        SELECT trade_id, timestamp
        FROM trade_decisions
        WHERE market_id = ? AND bin_label = ? AND direction = ? AND status = ?
        """,
        (market_id, bin_label, direction, status),
    ).fetchall()
    if not rows:
        return None
    return min(rows, key=lambda r: _distance_seconds(r["timestamp"], target_ts))


def _recover_market_hours_from_active_markets(conn) -> int:
    pending = conn.execute(
        """
        SELECT trade_id, market_id, timestamp
        FROM trade_decisions
        WHERE market_hours_open IS NULL
        """
    ).fetchall()
    if not pending:
        return 0

    try:
        from src.data.market_scanner import _extract_outcomes, _get_active_events
    except Exception:
        return 0

    try:
        events = _get_active_events()
    except Exception:
        return 0

    created_lookup: dict[str, datetime] = {}
    for event in events:
        created_raw = event.get("createdAt") or event.get("created_at")
        created_dt = _parse_ts(created_raw)
        if created_dt is None:
            continue
        for outcome in _extract_outcomes(event):
            market_id = str(outcome.get("market_id", ""))
            if market_id:
                created_lookup[market_id] = created_dt

    recovered = 0
    for row in pending:
        created_dt = created_lookup.get(str(row["market_id"]))
        trade_dt = _parse_ts(row["timestamp"])
        if created_dt is None or trade_dt is None:
            continue
        hours_open = max(0.0, (trade_dt - created_dt).total_seconds() / 3600.0)
        conn.execute(
            "UPDATE trade_decisions SET market_hours_open = ? WHERE trade_id = ?",
            (hours_open, row["trade_id"]),
        )
        recovered += 1

    return recovered


def run_backfill(path: Path = POSITIONS_PATH) -> dict:
    state = json.loads(path.read_text())
    positions = state.get("positions", [])
    recent_exits = [r for r in state.get("recent_exits", []) if not str(r.get("market_id", "")).startswith("mock_")]

    conn = get_connection()
    init_schema(conn)
    updated = 0

    for pos in positions:
        row = _best_row(
            conn,
            market_id=pos["market_id"],
            bin_label=pos["bin_label"],
            direction=pos["direction"],
            status="entered",
            target_ts=pos.get("entered_at"),
        )
        if row is None:
            continue
        conn.execute(
            """
            UPDATE trade_decisions
            SET runtime_trade_id = COALESCE(runtime_trade_id, ?),
                forecast_snapshot_id = COALESCE(forecast_snapshot_id, ?),
                strategy = CASE WHEN strategy IS NULL OR strategy = '' THEN ? ELSE strategy END,
                discovery_mode = CASE WHEN discovery_mode IS NULL OR discovery_mode = '' THEN ? ELSE discovery_mode END,
                market_hours_open = COALESCE(market_hours_open, ?),
                fill_quality = COALESCE(fill_quality, ?),
                entry_method = CASE WHEN entry_method IS NULL OR entry_method = '' THEN ? ELSE entry_method END,
                selected_method = CASE WHEN selected_method IS NULL OR selected_method = '' THEN ? ELSE selected_method END,
                applied_validations_json = CASE WHEN applied_validations_json IS NULL OR applied_validations_json = '' THEN ? ELSE applied_validations_json END
            WHERE trade_id = ?
            """,
            (
                pos.get("trade_id", ""),
                int(pos["decision_snapshot_id"]) if str(pos.get("decision_snapshot_id", "")).isdigit() else None,
                pos.get("strategy", ""),
                pos.get("discovery_mode", ""),
                pos.get("market_hours_open"),
                pos.get("fill_quality"),
                pos.get("entry_method", ""),
                pos.get("selected_method", ""),
                json.dumps(pos.get("applied_validations", []) or []),
                row["trade_id"],
            ),
        )
        updated += 1

    for ex in recent_exits:
        row = _best_row(
            conn,
            market_id=ex["market_id"],
            bin_label=ex["bin_label"],
            direction=ex["direction"],
            status="exited",
            target_ts=ex.get("exited_at"),
        )
        if row is None:
            continue
        conn.execute(
            """
            UPDATE trade_decisions
            SET runtime_trade_id = COALESCE(runtime_trade_id, ?),
                forecast_snapshot_id = COALESCE(forecast_snapshot_id, ?),
                strategy = CASE WHEN strategy IS NULL OR strategy = '' THEN ? ELSE strategy END,
                discovery_mode = CASE WHEN discovery_mode IS NULL OR discovery_mode = '' THEN ? ELSE discovery_mode END,
                market_hours_open = COALESCE(market_hours_open, ?),
                fill_quality = COALESCE(fill_quality, ?),
                entry_method = CASE WHEN entry_method IS NULL OR entry_method = '' THEN ? ELSE entry_method END,
                selected_method = CASE WHEN selected_method IS NULL OR selected_method = '' THEN ? ELSE selected_method END,
                applied_validations_json = CASE WHEN applied_validations_json IS NULL OR applied_validations_json = '' THEN ? ELSE applied_validations_json END,
                exit_reason = CASE WHEN exit_reason IS NULL OR exit_reason = '' THEN ? ELSE exit_reason END,
                admin_exit_reason = CASE WHEN admin_exit_reason IS NULL OR admin_exit_reason = '' THEN ? ELSE admin_exit_reason END
            WHERE trade_id = ?
            """,
            (
                ex.get("trade_id", ""),
                int(ex["decision_snapshot_id"]) if str(ex.get("decision_snapshot_id", "")).isdigit() else None,
                ex.get("strategy", ""),
                ex.get("discovery_mode", ""),
                ex.get("market_hours_open"),
                ex.get("fill_quality"),
                ex.get("entry_method", ""),
                ex.get("selected_method", ""),
                json.dumps(ex.get("applied_validations", []) or []),
                ex.get("exit_reason", ""),
                ex.get("admin_exit_reason", ""),
                row["trade_id"],
            ),
        )
        updated += 1

    # decision_log trade_cases can restore entry-time provenance for rows that
    # predate the richer trade_decisions schema.
    decision_rows = conn.execute(
        "SELECT artifact_json, started_at, mode FROM decision_log ORDER BY id DESC LIMIT 500"
    ).fetchall()
    recovered_cases = []
    for row in decision_rows:
        try:
            artifact = json.loads(row["artifact_json"])
        except Exception:
            continue
        for tc in artifact.get("trade_cases", []):
            recovered_cases.append({
                "range_label": tc.get("range_label", ""),
                "direction": tc.get("direction", ""),
                "edge_source": tc.get("edge_source", ""),
                "strategy": tc.get("strategy", tc.get("edge_source", "")),
                "discovery_mode": artifact.get("mode", row["mode"]),
                "market_hours_open": tc.get("market_hours_open"),
                "decision_snapshot_id": tc.get("decision_snapshot_id"),
                "selected_method": tc.get("selected_method", ""),
                "applied_validations_json": json.dumps(tc.get("applied_validations", []) or []),
                "timestamp": row["started_at"],
            })

    for case in recovered_cases:
        rows = conn.execute(
            """
            SELECT trade_id, timestamp FROM trade_decisions
            WHERE bin_label = ? AND direction = ? AND edge_source = ?
              AND (
                strategy IS NULL OR strategy = '' OR
                discovery_mode IS NULL OR discovery_mode = '' OR
                selected_method IS NULL OR selected_method = '' OR
                forecast_snapshot_id IS NULL
              )
            """,
            (case["range_label"], case["direction"], case["edge_source"]),
        ).fetchall()
        if not rows:
            continue
        row = min(rows, key=lambda r: _distance_seconds(r["timestamp"], case["timestamp"]))
        conn.execute(
            """
            UPDATE trade_decisions
            SET strategy = CASE WHEN strategy IS NULL OR strategy = '' THEN ? ELSE strategy END,
                discovery_mode = CASE WHEN discovery_mode IS NULL OR discovery_mode = '' THEN ? ELSE discovery_mode END,
                market_hours_open = COALESCE(market_hours_open, ?),
                forecast_snapshot_id = COALESCE(forecast_snapshot_id, ?),
                selected_method = CASE WHEN selected_method IS NULL OR selected_method = '' THEN ? ELSE selected_method END,
                applied_validations_json = CASE WHEN applied_validations_json IS NULL OR applied_validations_json = '' THEN ? ELSE applied_validations_json END
            WHERE trade_id = ?
            """,
            (
                case["strategy"],
                case["discovery_mode"],
                case["market_hours_open"],
                int(case["decision_snapshot_id"]) if str(case.get("decision_snapshot_id", "")).isdigit() else None,
                case["selected_method"],
                case["applied_validations_json"],
                row["trade_id"],
            ),
        )

    # Final pass: if an older row has the same market/bin/direction/edge_source as a
    # newer row that is already complete, copy the missing attribution across.
    incomplete_rows = conn.execute(
        """
        SELECT trade_id, market_id, bin_label, direction, edge_source
        FROM trade_decisions
        WHERE strategy IS NULL OR strategy = '' OR discovery_mode IS NULL OR discovery_mode = ''
           OR entry_method IS NULL OR entry_method = '' OR forecast_snapshot_id IS NULL
        """
    ).fetchall()
    for row in incomplete_rows:
        donor = conn.execute(
            """
            SELECT strategy, discovery_mode, market_hours_open, fill_quality,
                   entry_method, selected_method, applied_validations_json,
                   forecast_snapshot_id, exit_reason, admin_exit_reason
            FROM trade_decisions
            WHERE market_id = ? AND bin_label = ? AND direction = ? AND edge_source = ?
              AND trade_id != ?
              AND strategy IS NOT NULL AND strategy != ''
            ORDER BY trade_id DESC LIMIT 1
            """,
            (row["market_id"], row["bin_label"], row["direction"], row["edge_source"], row["trade_id"]),
        ).fetchone()
        if donor is None:
            continue
        conn.execute(
            """
            UPDATE trade_decisions
            SET strategy = CASE WHEN strategy IS NULL OR strategy = '' THEN ? ELSE strategy END,
                discovery_mode = CASE WHEN discovery_mode IS NULL OR discovery_mode = '' THEN ? ELSE discovery_mode END,
                market_hours_open = COALESCE(market_hours_open, ?),
                fill_quality = COALESCE(fill_quality, ?),
                entry_method = CASE WHEN entry_method IS NULL OR entry_method = '' THEN ? ELSE entry_method END,
                selected_method = CASE WHEN selected_method IS NULL OR selected_method = '' THEN ? ELSE selected_method END,
                applied_validations_json = CASE WHEN applied_validations_json IS NULL OR applied_validations_json = '' THEN ? ELSE applied_validations_json END,
                forecast_snapshot_id = COALESCE(forecast_snapshot_id, ?),
                exit_reason = CASE WHEN exit_reason IS NULL OR exit_reason = '' THEN ? ELSE exit_reason END,
                admin_exit_reason = CASE WHEN admin_exit_reason IS NULL OR admin_exit_reason = '' THEN ? ELSE admin_exit_reason END
            WHERE trade_id = ?
            """,
            (
                donor["strategy"],
                donor["discovery_mode"],
                donor["market_hours_open"],
                donor["fill_quality"],
                donor["entry_method"],
                donor["selected_method"],
                donor["applied_validations_json"],
                donor["forecast_snapshot_id"],
                donor["exit_reason"],
                donor["admin_exit_reason"],
                row["trade_id"],
            ),
        )

    conn.execute(
        """
        UPDATE trade_decisions
        SET selected_method = entry_method
        WHERE (selected_method IS NULL OR selected_method = '')
          AND entry_method IS NOT NULL AND entry_method != ''
        """
    )

    conn.execute(
        """
        UPDATE trade_decisions
        SET fill_quality = 0.0
        WHERE env = 'paper' AND fill_quality IS NULL
        """
    )

    recovered_market_hours = _recover_market_hours_from_active_markets(conn)

    conn.commit()
    remaining_null_strategy = conn.execute(
        "SELECT COUNT(*) FROM trade_decisions WHERE strategy IS NULL OR strategy = ''"
    ).fetchone()[0]
    remaining_null_market_hours = conn.execute(
        "SELECT COUNT(*) FROM trade_decisions WHERE market_hours_open IS NULL"
    ).fetchone()[0]
    conn.close()
    return {
        "updated_rows": updated,
        "remaining_null_strategy_rows": remaining_null_strategy,
        "recovered_market_hours_rows": recovered_market_hours,
        "remaining_null_market_hours_rows": remaining_null_market_hours,
    }


if __name__ == "__main__":
    print(json.dumps(run_backfill(), ensure_ascii=False, indent=2))
