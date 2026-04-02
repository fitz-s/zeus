#!/usr/bin/env python3
"""Archive and tombstone unsuffixed legacy state files."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.state.truth_files import deprecate_legacy_truth_files


def run() -> list[dict]:
    return deprecate_legacy_truth_files()


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
