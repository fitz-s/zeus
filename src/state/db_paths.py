# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_1_ULTRAPLAN.md v3 §4.8 (W3 critic focus #7); operator directive 2026-05-19 "paths按main写入"
"""Production DB path resolvers — always return PRIMARY worktree state/, never local."""
from pathlib import Path

_PRIMARY_ROOT = Path("/Users/leofitz/.openclaw/workspace-venus/zeus")


def primary_world_db_path() -> Path:
    return _PRIMARY_ROOT / "state" / "zeus-world.db"


def primary_forecasts_db_path() -> Path:
    return _PRIMARY_ROOT / "state" / "zeus-forecasts.db"


def primary_trade_db_path() -> Path:
    return _PRIMARY_ROOT / "state" / "zeus_trades.db"
