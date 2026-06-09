#!/usr/bin/env python3
"""Plan current-market replacement forecast downloads and materialization."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.replacement_forecast_current_target_plan import (  # noqa: E402
    build_replacement_forecast_current_target_plan,
    replacement_forecast_download_plan_from_current_targets,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan replacement forecast downloads for current market targets")
    parser.add_argument("--forecast-db", type=Path, default=ROOT / "state" / "zeus-forecasts.db")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)
    try:
        plan = build_replacement_forecast_current_target_plan(
            args.forecast_db,
            limit=args.limit,
        )
        payload = {
            "coverage": plan.as_dict(),
            "download_plan": replacement_forecast_download_plan_from_current_targets(plan),
        }
        if args.output_json is not None:
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            args.output_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error_type": exc.__class__.__name__, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    if args.stdout:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(plan.status)
    return 0 if plan.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
