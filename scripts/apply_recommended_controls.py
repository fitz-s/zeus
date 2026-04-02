from __future__ import annotations

import argparse
import json
import sys

from src.config import state_path
from src.control.control_plane import enqueue_commands, recommended_commands_from_status


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

    commands = recommended_commands_from_status(
        status,
        include_review_required=args.include_review_required,
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
