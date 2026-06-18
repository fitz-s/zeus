#!/usr/bin/env python3
"""Compatibility entrypoint for the renamed live replacement materializer."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.materialize_replacement_forecast_live import main


if __name__ == "__main__":
    raise SystemExit(main())
