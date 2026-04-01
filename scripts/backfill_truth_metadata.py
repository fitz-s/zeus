#!/usr/bin/env python3
"""Backfill truth metadata onto existing mode-suffixed state files."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.state.truth_files import backfill_truth_metadata_for_modes


def run() -> list[dict]:
    return backfill_truth_metadata_for_modes()


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
