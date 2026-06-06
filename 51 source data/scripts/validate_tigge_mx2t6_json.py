#!/usr/bin/env python3
"""Wrapper for mx2t6 local-calendar-day JSON validation."""
from __future__ import annotations

import sys

from validate_tigge_local_calendar_day_json import main as generic_main


if __name__ == "__main__":
    raise SystemExit(generic_main(["--track", "mx2t6_high", *sys.argv[1:]]))
