#!/usr/bin/env python3
"""Compatibility entrypoint for the renamed live replacement materializer."""

from scripts.materialize_replacement_forecast_live import main


if __name__ == "__main__":
    raise SystemExit(main())
