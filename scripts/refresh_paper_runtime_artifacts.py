"""Refresh persisted paper runtime artifacts from current code truth."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _apply_state_dir_override(state_dir: Path, *, mode: str) -> dict[str, Any]:
    """Mutate module-level path globals. Returns a snapshot for restore."""
    import src.config as config_module
    import src.state.db as db_module
    import src.state.portfolio as portfolio_module
    import src.state.strategy_tracker as tracker_module
    import src.observability.status_summary as status_module

    snapshot = {
        "config.STATE_DIR": config_module.STATE_DIR,
        "db.STATE_DIR": db_module.STATE_DIR,
        "db.ZEUS_DB_PATH": db_module.ZEUS_DB_PATH,
        "db.ZEUS_SHARED_DB_PATH": db_module.ZEUS_SHARED_DB_PATH,
        "db.RISK_DB_PATH": db_module.RISK_DB_PATH,
        "portfolio.POSITIONS_PATH": portfolio_module.POSITIONS_PATH,
        "tracker.TRACKER_PATH": tracker_module.TRACKER_PATH,
        "tracker._TRACKER_SINGLETON": tracker_module._TRACKER_SINGLETON,
        "status.STATUS_PATH": status_module.STATUS_PATH,
    }

    config_module.STATE_DIR = state_dir
    db_module.STATE_DIR = state_dir
    db_module.ZEUS_DB_PATH = state_dir / "zeus.db"
    db_module.ZEUS_SHARED_DB_PATH = state_dir / "zeus-shared.db"
    db_module.RISK_DB_PATH = state_dir / f"risk_state-{mode}.db"
    portfolio_module.POSITIONS_PATH = state_dir / f"positions-{mode}.json"
    tracker_module.TRACKER_PATH = state_dir / f"strategy_tracker-{mode}.json"
    tracker_module._TRACKER_SINGLETON = None
    status_module.STATUS_PATH = state_dir / f"status_summary-{mode}.json"

    return snapshot


def _restore_state_dir(snapshot: dict[str, Any]) -> None:
    """Restore module-level path globals from a snapshot."""
    import src.config as config_module
    import src.state.db as db_module
    import src.state.portfolio as portfolio_module
    import src.state.strategy_tracker as tracker_module
    import src.observability.status_summary as status_module

    config_module.STATE_DIR = snapshot["config.STATE_DIR"]
    db_module.STATE_DIR = snapshot["db.STATE_DIR"]
    db_module.ZEUS_DB_PATH = snapshot["db.ZEUS_DB_PATH"]
    db_module.ZEUS_SHARED_DB_PATH = snapshot["db.ZEUS_SHARED_DB_PATH"]
    db_module.RISK_DB_PATH = snapshot["db.RISK_DB_PATH"]
    portfolio_module.POSITIONS_PATH = snapshot["portfolio.POSITIONS_PATH"]
    tracker_module.TRACKER_PATH = snapshot["tracker.TRACKER_PATH"]
    tracker_module._TRACKER_SINGLETON = snapshot["tracker._TRACKER_SINGLETON"]
    status_module.STATUS_PATH = snapshot["status.STATUS_PATH"]


def refresh_paper_runtime_artifacts(*, state_dir: str | Path | None = None) -> dict[str, Any]:
    """Run one bounded paper-mode artifact refresh.

    Order matters:
    1. refresh RiskGuard snapshot
    2. refresh status summary from the new risk snapshot
    """
    prior_mode = os.environ.get("ZEUS_MODE")
    os.environ["ZEUS_MODE"] = "paper"
    snapshot: dict[str, Any] | None = None
    try:
        target_state_dir = Path(state_dir).expanduser().resolve() if state_dir is not None else None
        if target_state_dir is not None:
            snapshot = _apply_state_dir_override(target_state_dir, mode="paper")
        from src.riskguard.riskguard import tick
        from src.observability.status_summary import write_status

        level = tick()
        write_status({
            "mode": "paper",
            "artifact_refresh": True,
            "risk_level": getattr(level, "value", str(level)),
        })
        return {
            "mode": "paper",
            "risk_level": getattr(level, "value", str(level)),
            "status": "refreshed",
            "state_dir": str(target_state_dir) if target_state_dir is not None else None,
        }
    finally:
        if snapshot is not None:
            _restore_state_dir(snapshot)
        if prior_mode is None:
            os.environ.pop("ZEUS_MODE", None)
        else:
            os.environ["ZEUS_MODE"] = prior_mode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh paper runtime artifacts from current code truth.")
    parser.add_argument("--state-dir", help="Override the state directory to refresh (defaults to current repo state/).")
    args = parser.parse_args(argv)

    result = refresh_paper_runtime_artifacts(state_dir=args.state_dir)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
