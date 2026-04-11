from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import STATE_DIR
from src.state.db import backfill_open_legacy_paper_positions, get_connection
from src.state.portfolio import _load_portfolio_from_json_data, _load_portfolio_json_payload


DEFAULT_DB = STATE_DIR / "zeus.db"
DEFAULT_POSITIONS = STATE_DIR / "positions-paper.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--positions", type=Path, default=DEFAULT_POSITIONS)
    args = parser.parse_args()

    if not args.positions.exists():
        print(json.dumps({
            "status": "missing_positions_file",
            "positions": str(args.positions),
            "db": str(args.db),
        }, indent=2))
        return 0

    os.environ["ZEUS_MODE"] = "paper"
    # This support script is explicitly pointed at a legacy JSON positions
    # file. Do not use load_portfolio() here: its DB-first behavior can read
    # the active repo DB instead of the supplied fixture/operator file.
    portfolio = _load_portfolio_from_json_data(
        _load_portfolio_json_payload(args.positions),
        current_mode="paper",
    )
    conn = get_connection(args.db)
    try:
        report = backfill_open_legacy_paper_positions(
            conn,
            portfolio.positions,
            source_module="scripts.backfill_open_positions_canonical",
        )
        report.update({
            "db": str(args.db),
            "positions": str(args.positions),
        })
        print(json.dumps(report, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
