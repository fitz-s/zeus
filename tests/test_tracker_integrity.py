import json
import sqlite3
import pytest
from pathlib import Path
from src.config import PROJECT_ROOT

DB_PATH = PROJECT_ROOT / "state" / "zeus.db"

def test_tracker_no_phantoms():
    """Regression test ensuring NO phantom trades exist in the strategy trackers.
    Every trade recorded in the analytical JSON MUST have complete DB provenance.
    """
    if not DB_PATH.exists():
        pytest.skip(f"DB not found at {DB_PATH}, skipping tracker integrity test.")
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    trackers = [
        PROJECT_ROOT / "state" / "strategy_tracker.json",
    ]
    
    phantom_count = 0
    phantoms_detected = []
    
    for tracker_path in trackers:
        if not tracker_path.exists():
            continue
            
        with open(tracker_path) as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                continue
            
        for strategy, strat_data in data.get("strategies", {}).items():
            for trade in strat_data.get("trades", []):
                trade_id = trade.get("trade_id")
                if not trade_id:
                    continue
                
                # Verify DB Provenance: Check trade_decisions existence against runtime mapping
                row = conn.execute("SELECT runtime_trade_id FROM trade_decisions WHERE runtime_trade_id = ?", (trade_id,)).fetchone()
                if not row:
                    phantoms_detected.append(f"{trade_id} ({tracker_path.name}) Pnl: {trade.get('pnl')}")
                    phantom_count += 1
                    
    conn.close()
    
    if phantom_count > 0:
        error_msg = f"INTEGRITY VIOLATION: {phantom_count} Phantom trades detected!\n"
        error_msg += "Found '赚钱数字' (profit metrics) with NO DB provenance:\n"
        error_msg += "\n".join(phantoms_detected)
        error_msg += "\nFail: The Strategy Tracker contains ghost data unaccounted for by the Evidence Spine."
        pytest.fail(error_msg)
