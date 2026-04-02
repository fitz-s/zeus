from __future__ import annotations

import json
import sys

from src.config import state_path
from src.control.control_plane import enqueue_commands, recommended_commands_from_status


def main() -> int:
    status_path = state_path("status_summary.json")
    if not status_path.exists():
        print(json.dumps({"ok": False, "reason": "missing_status_summary"}))
        return 1

    with open(status_path) as f:
        status = json.load(f)

    commands = recommended_commands_from_status(status)
    added = enqueue_commands(commands)
    print(json.dumps({"ok": True, "commands": commands, "added": added}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
