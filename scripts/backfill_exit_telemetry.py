#!/usr/bin/env python3
"""Backfill structured exit telemetry into recent_exits from existing fields."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

POSITIONS_PATH = PROJECT_ROOT / "state" / "positions-paper.json"
SCORE_RE = re.compile(r"Model-Market divergence score ([0-9.]+)")


def run_backfill(path: Path = POSITIONS_PATH) -> dict:
    state = json.loads(path.read_text())
    updated = 0
    for ex in state.get("recent_exits", []):
        changed = False
        reason = str(ex.get("exit_reason", ""))
        edge_ctx_json = ex.get("edge_context_json")
        edge_ctx = json.loads(edge_ctx_json) if edge_ctx_json else {}

        if not ex.get("exit_trigger"):
            if reason.startswith("Model-Market divergence score"):
                ex["exit_trigger"] = "MODEL_DIVERGENCE_PANIC"
                changed = True
            elif reason.startswith("Adverse market velocity"):
                ex["exit_trigger"] = "FLASH_CRASH_PANIC"
                changed = True
            elif reason.startswith("Settlement in"):
                ex["exit_trigger"] = "SETTLEMENT_IMMINENT"
                changed = True

        if ex.get("exit_divergence_score") in (None, ""):
            match = SCORE_RE.search(reason)
            if match:
                ex["exit_divergence_score"] = float(match.group(1))
                changed = True
            elif edge_ctx.get("divergence_score") is not None:
                ex["exit_divergence_score"] = float(edge_ctx.get("divergence_score", 0.0) or 0.0)
                changed = True

        if ex.get("exit_market_velocity_1h") in (None, "") and edge_ctx.get("market_velocity_1h") is not None:
            ex["exit_market_velocity_1h"] = float(edge_ctx.get("market_velocity_1h", 0.0) or 0.0)
            changed = True

        if ex.get("exit_forward_edge") in (None, "") and edge_ctx.get("forward_edge") is not None:
            ex["exit_forward_edge"] = float(edge_ctx.get("forward_edge", 0.0) or 0.0)
            changed = True

        if changed:
            updated += 1

    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"updated_exits": updated}


if __name__ == "__main__":
    print(json.dumps(run_backfill(), ensure_ascii=False, indent=2))
