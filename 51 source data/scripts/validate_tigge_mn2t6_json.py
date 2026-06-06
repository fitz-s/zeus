#!/usr/bin/env python3
"""Wrapper for mn2t6 local-calendar-day JSON validation."""
from __future__ import annotations

import sys

from validate_tigge_local_calendar_day_json import main as generic_main


if __name__ == "__main__":
    raise SystemExit(generic_main(["--track", "mn2t6_low", *sys.argv[1:]]))
