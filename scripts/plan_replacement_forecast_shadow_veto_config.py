#!/usr/bin/env python3
"""Plan or apply the replacement forecast shadow/veto config switch."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.replacement_forecast_config_switch import (  # noqa: E402
    apply_replacement_forecast_config_switch,
    read_replacement_forecast_config_switch_plan,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings-json", default="config/settings.json", help="Settings JSON path to inspect.")
    parser.add_argument("--apply", action="store_true", help="Persist the planned shadow/veto-only feature flag patch.")
    parser.add_argument("--stdout", action="store_true", help="Print the JSON report to stdout.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    settings_path = Path(args.settings_json)
    try:
        plan = (
            apply_replacement_forecast_config_switch(settings_path)
            if args.apply
            else read_replacement_forecast_config_switch_plan(settings_path)
        )
    except Exception as exc:
        payload = {
            "status": "INVALID_CONFIG",
            "settings_json": str(settings_path),
            "applied": False,
            "error": str(exc),
        }
        print(json.dumps(payload, indent=2, sort_keys=True), file=sys.stderr)
        return 2

    payload = plan.as_dict()
    payload["settings_json"] = str(settings_path)
    payload["applied"] = bool(args.apply)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if plan.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
