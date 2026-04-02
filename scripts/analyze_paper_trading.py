"""Paper trading analysis: first real feedback on Zeus edge detection.

Analyzes daemon output: trades, directions, edge sources, settlements,
ENS growth, bias impact on trade selection.
"""

import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.truth_files import current_mode, read_mode_truth_json


def analyze():
    mode = current_mode()
    conn = sqlite3.connect(str(PROJECT_ROOT / "state" / "zeus.db"))
    conn.row_factory = sqlite3.Row

    result = {}

    # 1. Trade summary
    entries = conn.execute("SELECT * FROM chronicle WHERE env = ? AND event_type='ENTRY'", (mode,)).fetchall()
    exits = conn.execute("SELECT * FROM chronicle WHERE env = ? AND event_type='EXIT'", (mode,)).fetchall()
    settlements = conn.execute("SELECT * FROM chronicle WHERE env = ? AND event_type='SETTLEMENT'", (mode,)).fetchall()

    directions = Counter()
    cities = Counter()
    edges = []
    bin_types = Counter()

    for e in entries:
        d = json.loads(e["details_json"])
        directions[d.get("direction", "?")] += 1
        cities[d.get("city", "?")] += 1
        edges.append(d.get("edge", 0))

        bl = d.get("bin", "").lower()
        if "below" in bl or "lower" in bl:
            bin_types["shoulder_low"] += 1
        elif "higher" in bl or "above" in bl:
            bin_types["shoulder_high"] += 1
        else:
            bin_types["center"] += 1

    result["trades"] = {
        "mode": mode,
        "entries": len(entries), "exits": len(exits), "settlements": len(settlements),
        "directions": dict(directions),
        "cities": dict(cities),
        "bin_types": dict(bin_types),
        "avg_edge": float(np.mean(edges)) if edges else 0,
        "max_edge": float(max(edges)) if edges else 0,
    }

    # 2. Warm bias analysis
    sh_high_buy_no = sum(1 for e in entries
                         if json.loads(e["details_json"]).get("direction") == "buy_no"
                         and ("higher" in json.loads(e["details_json"]).get("bin", "").lower()
                              or "above" in json.loads(e["details_json"]).get("bin", "").lower()))
    sh_low_buy_no = sum(1 for e in entries
                        if json.loads(e["details_json"]).get("direction") == "buy_no"
                        and ("below" in json.loads(e["details_json"]).get("bin", "").lower()
                             or "lower" in json.loads(e["details_json"]).get("bin", "").lower()))

    result["bias_analysis"] = {
        "shoulder_high_buy_no": sh_high_buy_no,
        "shoulder_low_buy_no": sh_low_buy_no,
        "total_buy_no": directions.get("buy_no", 0),
        "total_buy_yes": directions.get("buy_yes", 0),
        "interpretation": (
            "ECMWF warm bias inflates high-temp P_raw, which should REDUCE buy_no "
            "edges on shoulder_high. But edges remain large (avg 0.34), meaning "
            "market FLB overpricing dominates over model warm bias. The bias is "
            "actually conservative — true edges may be larger than computed."
        ),
    }

    # 3. ENS collection
    ens_total = conn.execute("SELECT COUNT(*) FROM ensemble_snapshots").fetchone()[0]
    ens_live = conn.execute("SELECT COUNT(*) FROM ensemble_snapshots WHERE data_version='live_v1'").fetchone()[0]
    result["ens_snapshots"] = {"total": ens_total, "live": ens_live}

    # 4. Portfolio state
    try:
        pf, truth = read_mode_truth_json("positions.json")
        status, status_truth = read_mode_truth_json("status_summary.json")
        result["portfolio"] = {
            "positions": int(status["portfolio"]["open_positions"]),
            "total_exposure_usd": float(status["portfolio"]["total_exposure_usd"]),
            "heat_pct": float(status["portfolio"]["heat_pct"]),
            "bankroll": float(status["portfolio"]["bankroll"]),
            "realized_pnl": float(status["portfolio"]["realized_pnl"]),
            "unrealized_pnl": float(status["portfolio"]["unrealized_pnl"]),
            "total_pnl": float(status["portfolio"]["total_pnl"]),
            "truth_mode": truth.get("mode"),
            "truth_generated_at": truth.get("generated_at"),
            "truth_source_path": truth.get("source_path"),
            "truth_stale_age_seconds": truth.get("stale_age_seconds"),
            "status_source_path": status_truth.get("source_path"),
            "status_generated_at": status_truth.get("generated_at"),
            "status_stale_age_seconds": status_truth.get("stale_age_seconds"),
        }
    except FileNotFoundError:
        result["portfolio"] = {"positions": 0}

    # 5. Warnings
    warnings = []
    if result["trades"]["avg_edge"] > 0.25:
        warnings.append(
            f"CAUTION: avg edge {result['trades']['avg_edge']:.3f} is very high. "
            f"Rainstorm autopsy: model was most wrong when most confident (avg edge 0.317). "
            f"FDR filter may need tightening."
        )
    if result["portfolio"].get("heat_pct", 0) > 40:
        warnings.append(
            f"Portfolio heat at {result['portfolio']['heat_pct']}% — approaching 50% cap."
        )
    result["warnings"] = warnings

    conn.close()

    # Save
    output_path = PROJECT_ROOT / "state" / "paper_trading_analysis.json"
    rendered = json.dumps(result, indent=2)
    try:
        with open(output_path, "w") as f:
            f.write(rendered)
    except OSError as exc:
        result["output_write_error"] = str(exc)
        rendered = json.dumps(result, indent=2)

    # Print summary
    print(rendered)
    return result


if __name__ == "__main__":
    analyze()
