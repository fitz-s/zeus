from __future__ import annotations

import argparse
import json
import sys

from src.config import state_path
from src.control.control_plane import enqueue_commands


STATUS_CONTROL_REQUIRED_KEYS = (
    "recommended_auto_commands",
    "review_required_commands",
    "recommended_commands",
)


def _missing_status_contract_keys(status: dict) -> list[str]:
    control = (status or {}).get("control")
    if not isinstance(control, dict):
        return ["control"]
    missing: list[str] = []
    for key in STATUS_CONTROL_REQUIRED_KEYS:
        if key not in control:
            missing.append(f"control.{key}")
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enqueue recommended Zeus control-plane actions from status truth.")
    parser.add_argument(
        "--include-review-required",
        action="store_true",
        help="Also enqueue review-required commands such as strategy gate changes.",
    )
    args = parser.parse_args(argv)

    status_path = state_path("status_summary.json")
    if not status_path.exists():
        print(json.dumps({"ok": False, "reason": "missing_status_summary"}))
        return 1

    with open(status_path) as f:
        status = json.load(f)

    missing_keys = _missing_status_contract_keys(status)
    if missing_keys:
        print(
            json.dumps(
                {
                    "ok": False,
                    "reason": "stale_status_contract",
                    "missing_keys": missing_keys,
                }
            )
        )
        return 1

    control = status.get("control", {})
    commands = list(
        control["recommended_commands"]
        if args.include_review_required
        else control["recommended_auto_commands"]
    )
    added = enqueue_commands(commands)
    print(
        json.dumps(
            {
                "ok": True,
                "include_review_required": args.include_review_required,
                "commands": commands,
                "added": added,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
