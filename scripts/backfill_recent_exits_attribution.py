#!/usr/bin/env python3
"""Backfill recent_exits attribution fields from trade_decisions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_connection

POSITIONS_PATH = PROJECT_ROOT / "state" / "positions-paper.json"


def run_backfill(positions_path: Path = POSITIONS_PATH) -> dict:
    if not positions_path.exists():
        return {"updated_exits": 0, "matched_rows": 0, "skipped": 0, "error": "positions file missing"}

    state = json.loads(positions_path.read_text())
    recent_exits = state.get("recent_exits", [])
    if not recent_exits:
        return {"updated_exits": 0, "matched_rows": 0, "skipped": 0}

    conn = get_connection()
    rows = conn.execute(
        """
        SELECT market_id, forecast_snapshot_id, strategy, direction, timestamp,
               selected_method, market_hours_open, fill_quality,
               applied_validations_json, admin_exit_reason,
               settlement_semantics_json, epistemic_context_json, edge_context_json
        FROM trade_decisions
        WHERE status = 'exited'
        """
    ).fetchall()
    decision_logs = conn.execute("SELECT artifact_json FROM decision_log").fetchall()
    conn.close()

    index = {
        (
            row["market_id"],
            str(row["forecast_snapshot_id"] or ""),
            row["strategy"] or "",
            row["direction"] or "",
            row["timestamp"] or "",
        ): row
        for row in rows
    }
    trade_case_index: dict[tuple[str, str, str, str], dict] = {}
    for row in decision_logs:
        try:
            artifact = json.loads(row["artifact_json"])
        except Exception:
            continue
        for case in artifact.get("trade_cases", []) or []:
            key = (
                str(case.get("decision_snapshot_id") or ""),
                case.get("range_label") or "",
                case.get("direction") or "",
                case.get("edge_source") or "",
            )
            if key not in trade_case_index:
                trade_case_index[key] = case

    updated_exits = 0
    matched_rows = 0
    skipped = 0

    for ex in recent_exits:
        key = (
            ex.get("market_id", ""),
            str(ex.get("decision_snapshot_id", "") or ""),
            ex.get("strategy", "") or "",
            ex.get("direction", "") or "",
            ex.get("exited_at", "") or "",
        )
        row = index.get(key)
        if row is None:
            skipped += 1
            continue

        matched_rows += 1
        changed = False
        trade_case = trade_case_index.get(
            (
                str(ex.get("decision_snapshot_id", "") or ""),
                ex.get("bin_label", "") or "",
                ex.get("direction", "") or "",
                ex.get("edge_source", "") or "",
            )
        )

        for field in ["selected_method", "market_hours_open", "fill_quality", "admin_exit_reason"]:
            if field not in ex or ex.get(field) in ("", None):
                value = row[field]
                if value not in ("", None):
                    ex[field] = value
                    changed = True

        if "applied_validations" not in ex or not ex.get("applied_validations"):
            applied = []
            if row["applied_validations_json"]:
                applied = json.loads(row["applied_validations_json"])
            if (not applied) and trade_case is not None:
                applied = list(trade_case.get("applied_validations") or [])
            if applied:
                ex["applied_validations"] = applied
                changed = True

        if ("selected_method" not in ex or not ex.get("selected_method")) and trade_case is not None:
            selected_method = trade_case.get("selected_method")
            if selected_method:
                ex["selected_method"] = selected_method
                changed = True

        for json_field in ["settlement_semantics_json", "epistemic_context_json", "edge_context_json"]:
            if json_field not in ex or not ex.get(json_field):
                if row[json_field]:
                    ex[json_field] = row[json_field]
                    changed = True

        if changed:
            updated_exits += 1

    if updated_exits:
        positions_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {"updated_exits": updated_exits, "matched_rows": matched_rows, "skipped": skipped}


if __name__ == "__main__":
    print(json.dumps(run_backfill(), ensure_ascii=False, indent=2))
