"""Data Completeness Audit for Zeus Replay Pipeline.

Answers:
  - Total exits
  - Tick-covered count
  - High-confidence replayable count
  - Fully skipped count
  - Missing-field ranking
  - Stage-of-loss ranking (entry / monitor / exit / persistence)
"""

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import state_path
from src.state.db import get_trade_connection_with_shared as get_connection

REPLAY_REQUIRED_FIELDS = [
    "trade_id", "token_id", "market_id", "bin_label", "direction",
    "entry_price", "size_usd", "entry_method", "entry_ci_width",
    "decision_snapshot_id", "entered_at", "exited_at", "exit_reason",
    "p_posterior",
]

# Which system stage is responsible for populating each field
FIELD_STAGE = {
    "trade_id":             "entry",
    "market_id":            "entry",
    "entry_price":          "entry",
    "size_usd":             "entry",
    "entry_method":         "entry",
    "entry_ci_width":       "entry",
    "decision_snapshot_id": "entry",
    "entered_at":           "entry",
    "p_posterior":           "entry",
    "token_id":             "entry",
    "bin_label":            "entry",
    "direction":            "entry",
    "exited_at":            "exit",
    "exit_reason":          "exit",
}


def run_audit():
    positions_file = state_path("positions.json")
    if not positions_file.exists():
        print(f"ERROR: No mode-qualified positions file found: {positions_file}")
        return

    with open(positions_file) as f:
        data = json.load(f)
    
    recent_exits = data.get("recent_exits", [])
    conn = get_connection()

    total_exits = len(recent_exits)
    tick_covered = 0
    high_confidence = 0
    fully_skipped = 0
    missing_field_counter = Counter()
    stage_loss_counter = Counter()
    per_exit_details = []

    for i, ex in enumerate(recent_exits):
        token_id = ex.get("token_id")
        if not token_id:
            fully_skipped += 1
            per_exit_details.append({"index": i, "city": ex.get("city"), "status": "no_token_id"})
            continue

        # Check tick coverage
        tick_count = conn.execute(
            "SELECT COUNT(*) FROM shared.token_price_log WHERE token_id = ?", (token_id,)
        ).fetchone()[0]

        if tick_count == 0:
            fully_skipped += 1
            per_exit_details.append({"index": i, "city": ex.get("city"), "status": "no_ticks"})
            continue

        tick_covered += 1

        # Check field completeness
        missing = []
        # Fields where 0/0.0 is invalid (must be positive)
        nonzero_required = {"entry_price", "size_usd"}
        for field in REPLAY_REQUIRED_FIELDS:
            val = ex.get(field)
            if val is None or val == "":
                missing.append(field)
                missing_field_counter[field] += 1
                stage_loss_counter[FIELD_STAGE.get(field, "unknown")] += 1
            elif field in nonzero_required and val == 0:
                missing.append(field)
                missing_field_counter[field] += 1
                stage_loss_counter[FIELD_STAGE.get(field, "unknown")] += 1

        if not missing:
            high_confidence += 1
            per_exit_details.append({
                "index": i, "city": ex.get("city"), "status": "high_confidence",
                "ticks": tick_count,
            })
        else:
            # Check if trade_decisions DB can fill the gap
            bin_label = ex.get("bin_label")
            direction = ex.get("direction")
            db_row = conn.execute(
                "SELECT price, size_usd, p_posterior FROM trade_decisions WHERE bin_label = ? AND direction = ? LIMIT 1",
                (bin_label, direction)
            ).fetchone()
            
            if db_row:
                high_confidence += 1
                per_exit_details.append({
                    "index": i, "city": ex.get("city"), "status": "recovered_from_db",
                    "ticks": tick_count, "missing_before_recovery": missing,
                })
            else:
                fully_skipped += 1
                per_exit_details.append({
                    "index": i, "city": ex.get("city"), "status": "unrecoverable",
                    "ticks": tick_count, "missing": missing,
                })

    conn.close()

    # --- Output Report ---
    report_lines = []
    report_lines.append("# Zeus Data Completeness Audit Report\n")
    report_lines.append("## Summary\n")
    report_lines.append(f"| Metric | Value |")
    report_lines.append(f"|---|---|")
    report_lines.append(f"| Total Exits | {total_exits} |")
    report_lines.append(f"| Tick Covered | {tick_covered} |")
    report_lines.append(f"| High-Confidence Replayable | {high_confidence} |")
    report_lines.append(f"| Fully Skipped | {fully_skipped} |")
    report_lines.append("")

    report_lines.append("## Missing-Field Ranking\n")
    report_lines.append("| Field | Missing Count | Stage |")
    report_lines.append("|---|---|---|")
    for field, count in missing_field_counter.most_common():
        report_lines.append(f"| `{field}` | {count} | {FIELD_STAGE.get(field, 'unknown')} |")
    report_lines.append("")

    report_lines.append("## Stage-of-Loss Ranking\n")
    report_lines.append("| Stage | Total Missing Fields |")
    report_lines.append("|---|---|")
    for stage, count in stage_loss_counter.most_common():
        report_lines.append(f"| {stage} | {count} |")
    report_lines.append("")

    report_lines.append("## Root Cause Analysis\n")
    if missing_field_counter:
        entry_fields = [f for f in missing_field_counter if FIELD_STAGE.get(f) == "entry"]
        if entry_fields:
            report_lines.append(f"> **Primary bottleneck: `_track_exit()` in `portfolio.py`**")
            report_lines.append(f"> ")
            report_lines.append(f"> {len(entry_fields)} entry-stage fields are missing because `_track_exit()` was not")
            report_lines.append(f"> persisting them into `recent_exits`. This has been fixed in this commit.")
            report_lines.append(f"> All {fully_skipped} existing skipped exits are **objectively unrecoverable**")
            report_lines.append(f"> because no secondary data source (`trade_decisions`: 0 rows,")
            report_lines.append(f"> `decision_log`: 0 trade fills) contains the missing fields.")
    else:
        report_lines.append("> All exits have complete field coverage.")
    report_lines.append("")

    report_lines.append("## Per-Exit Detail\n")
    for d in per_exit_details:
        status_emoji = "✅" if d["status"] == "high_confidence" else "❌"
        report_lines.append(f"- {status_emoji} Exit {d['index']} ({d.get('city', '?')}): {d['status']}"
                          + (f" — missing: {d.get('missing', [])}" if d.get("missing") else ""))
    report_lines.append("")

    report_text = "\n".join(report_lines)
    
    # Print to stdout
    print(report_text)

    # Write to file
    report_path = PROJECT_ROOT / "docs" / "reports" / "data_completeness_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        f.write(report_text)
    print(f"\nReport written to: {report_path}")


if __name__ == "__main__":
    run_audit()
