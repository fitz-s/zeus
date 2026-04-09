"""Refresh persisted paper runtime artifacts from current code truth."""

from __future__ import annotations

import json
import os
from typing import Any


def refresh_paper_runtime_artifacts() -> dict[str, Any]:
    """Run one bounded paper-mode artifact refresh.

    Order matters:
    1. refresh RiskGuard snapshot
    2. refresh status summary from the new risk snapshot
    """
    prior_mode = os.environ.get("ZEUS_MODE")
    os.environ["ZEUS_MODE"] = "paper"
    try:
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
        }
    finally:
        if prior_mode is None:
            os.environ.pop("ZEUS_MODE", None)
        else:
            os.environ["ZEUS_MODE"] = prior_mode


def main() -> int:
    result = refresh_paper_runtime_artifacts()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
