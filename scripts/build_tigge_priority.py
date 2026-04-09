"""Build TIGGE download priority index for the data agent.

Ranks settlements without ENS data by forecast uncertainty (ensemble_std).
High-std settlements cover the distribution's tails — most valuable for Platt.

Also recommends: prioritize JJA/SON/DJF dates (Zeus has 0 Platt models for those seasons).
"""

import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.manager import season_from_date, lat_for_city
from src.state.db import get_shared_connection as get_connection


RAINSTORM_DB = Path.home() / ".openclaw/workspace-venus/rainstorm/state/rainstorm.db"


def build_priority():
    zeus = get_connection()
    rs = sqlite3.connect(str(RAINSTORM_DB))
    rs.row_factory = sqlite3.Row

    # Settlements without ENS snapshots
    all_settlements = zeus.execute("""
        SELECT s.city, s.target_date
        FROM settlements s
        LEFT JOIN ensemble_snapshots e ON s.city = e.city AND s.target_date = e.target_date
        WHERE e.snapshot_id IS NULL
    """).fetchall()

    print(f"Settlements without ENS: {len(all_settlements)}")

    # Get max ensemble_std from forecast_log for each
    priorities = []
    for s in all_settlements:
        city = s["city"]
        date = s["target_date"]
        season = season_from_date(date, lat=lat_for_city(city))

        # Check forecast_log for std
        row = rs.execute("""
            SELECT MAX(ensemble_std) as max_std
            FROM forecast_log
            WHERE city = ? AND target_date = ?
        """, (city, date)).fetchone()

        max_std = row["max_std"] if row and row["max_std"] else 0.0

        # Bonus for underrepresented seasons
        season_bonus = 0.0
        if season in ("JJA", "SON"):
            season_bonus = 5.0  # 0 Platt models for these
        elif season == "DJF":
            season_bonus = 3.0  # Very few pairs for DJF

        score = max_std + season_bonus
        priorities.append({
            "city": city, "target_date": date, "season": season,
            "max_std": round(max_std, 2), "season_bonus": season_bonus,
            "score": round(score, 2),
        })

    # Sort by score descending
    priorities.sort(key=lambda x: -x["score"])

    # Summary
    from collections import Counter
    seasons = Counter(p["season"] for p in priorities)
    print(f"\nBy season: {dict(seasons)}")
    print(f"\nTop 20 priorities:")
    for p in priorities[:20]:
        print(f"  {p['city']:15s} {p['target_date']} {p['season']} "
              f"std={p['max_std']:.2f} bonus={p['season_bonus']:.0f} score={p['score']:.2f}")

    # Recommend seasons to prioritize
    print("\n=== RECOMMENDATION FOR DATA AGENT ===")
    print("Priority: JJA dates > SON dates > DJF dates > MAM dates")
    print(f"JJA: {seasons.get('JJA', 0)} settlements need ENS")
    print(f"SON: {seasons.get('SON', 0)} settlements need ENS")
    print(f"DJF: {seasons.get('DJF', 0)} settlements need ENS")
    print(f"MAM: {seasons.get('MAM', 0)} settlements need ENS (taxonomy-driven Platt coverage)")

    output = {
        "total_needed": len(priorities),
        "by_season": dict(seasons),
        "top_50": priorities[:50],
    }

    output_path = PROJECT_ROOT / "state" / "tigge_priority.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nSaved to {output_path}")

    rs.close()
    zeus.close()
    return output


if __name__ == "__main__":
    build_priority()
