#!/usr/bin/env python3
"""Audit current replay / attribution completeness from Zeus state."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_connection

POSITIONS_PATH = PROJECT_ROOT / "state" / "positions-paper.json"


def run_audit() -> dict:
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) FROM trade_decisions").fetchone()[0]

    def count(q: str) -> int:
        return conn.execute(q).fetchone()[0]

    trade_decisions = {
        "total_rows": total,
        "null_strategy": count("SELECT COUNT(*) FROM trade_decisions WHERE strategy IS NULL OR strategy = ''"),
        "null_discovery_mode": count("SELECT COUNT(*) FROM trade_decisions WHERE discovery_mode IS NULL OR discovery_mode = ''"),
        "null_market_hours_open": count("SELECT COUNT(*) FROM trade_decisions WHERE market_hours_open IS NULL"),
        "null_fill_quality": count("SELECT COUNT(*) FROM trade_decisions WHERE fill_quality IS NULL"),
        "null_entry_method": count("SELECT COUNT(*) FROM trade_decisions WHERE entry_method IS NULL OR entry_method = ''"),
        "null_selected_method": count("SELECT COUNT(*) FROM trade_decisions WHERE selected_method IS NULL OR selected_method = ''"),
        "null_runtime_trade_id": count("SELECT COUNT(*) FROM trade_decisions WHERE runtime_trade_id IS NULL OR runtime_trade_id = ''"),
        "null_snapshot": count("SELECT COUNT(*) FROM trade_decisions WHERE forecast_snapshot_id IS NULL"),
        "null_applied_validations": count("SELECT COUNT(*) FROM trade_decisions WHERE applied_validations_json IS NULL OR applied_validations_json = ''"),
        "null_settlement_semantics": count("SELECT COUNT(*) FROM trade_decisions WHERE settlement_semantics_json IS NULL OR settlement_semantics_json = ''"),
        "null_epistemic_context": count("SELECT COUNT(*) FROM trade_decisions WHERE epistemic_context_json IS NULL OR epistemic_context_json = ''"),
        "null_edge_context": count("SELECT COUNT(*) FROM trade_decisions WHERE edge_context_json IS NULL OR edge_context_json = ''"),
        "null_exit_reason_on_exited": count("SELECT COUNT(*) FROM trade_decisions WHERE status = 'exited' AND (exit_reason IS NULL OR exit_reason = '')"),
        "entered_rows": count("SELECT COUNT(*) FROM trade_decisions WHERE status = 'entered'"),
        "exited_rows": count("SELECT COUNT(*) FROM trade_decisions WHERE status = 'exited'"),
    }

    latest_rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT trade_id, market_id, bin_label, direction, status, strategy, edge_source,
                   discovery_mode, market_hours_open, fill_quality, entry_method,
                   selected_method, runtime_trade_id, forecast_snapshot_id, exit_reason
            FROM trade_decisions
            ORDER BY trade_id DESC LIMIT 20
            """
        ).fetchall()
    ]
    conn.close()

    positions_state = json.loads(POSITIONS_PATH.read_text()) if POSITIONS_PATH.exists() else {}
    current_positions = positions_state.get("positions", [])
    recent_exits = positions_state.get("recent_exits", [])

    current_position_completeness = {
        "open_positions": len(current_positions),
        "recent_exits": len(recent_exits),
        "open_positions_with_strategy": sum(1 for p in current_positions if p.get("strategy")),
        "open_positions_with_snapshot": sum(1 for p in current_positions if p.get("decision_snapshot_id")),
        "open_positions_with_entry_method": sum(1 for p in current_positions if p.get("entry_method")),
        "recent_exits_with_strategy": sum(1 for r in recent_exits if r.get("strategy")),
        "recent_exits_with_snapshot": sum(1 for r in recent_exits if r.get("decision_snapshot_id")),
        "recent_exits_with_entry_method": sum(1 for r in recent_exits if r.get("entry_method")),
        "recent_exits_with_selected_method": sum(1 for r in recent_exits if r.get("selected_method")),
        "recent_exits_with_market_hours_open": sum(1 for r in recent_exits if r.get("market_hours_open") is not None),
        "recent_exits_with_fill_quality": sum(1 for r in recent_exits if r.get("fill_quality") is not None),
        "recent_exits_with_applied_validations": sum(1 for r in recent_exits if r.get("applied_validations")),
        "recent_exits_with_semantic_snapshots": sum(
            1
            for r in recent_exits
            if r.get("settlement_semantics_json") and r.get("epistemic_context_json") and r.get("edge_context_json")
        ),
    }

    return {
        "trade_decisions": trade_decisions,
        "current_position_completeness": current_position_completeness,
        "latest_trade_decisions_sample": latest_rows,
    }


if __name__ == "__main__":
    print(json.dumps(run_audit(), ensure_ascii=False, indent=2))
