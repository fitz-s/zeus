# Lifecycle: created=2026-04-26; last_reviewed=2026-04-26; last_reused=never
# Purpose: Canonical home for meteorological-season helpers (season_from_date,
#          season_from_month, hemisphere_for_lat). Extracted from
#          src.calibration.manager per G10 calibration-fence (con-nyx
#          NICE-TO-HAVE #4) so the ingest lane (scripts/ingest/*) can call
#          season helpers without transitively pulling in src.calibration —
#          which writes to ensemble_snapshots_v2 and platt_models, surfaces
#          the ingest lane should not depend on.
# Reuse: This module is in `src.contracts.*` (allowed for both ingest +
#        engine + calibration lanes). When adding new calendar/season
#        helpers, prefer this module over src.calibration.manager.
# Authority basis: docs/operations/task_2026-04-26_g10_nice_to_have_batch/plan.md
#   §2 + parent docs/operations/task_2026-04-26_live_readiness_completion/plan.md
#   K3.G10 + con-nyx G10-scaffold NICE-TO-HAVE #4.
"""Meteorological-season helpers (calendar-only, no calibration logic).

Pure mapping helpers: month → season, date-string → season,
lat → hemisphere. No calibration, no DB, no I/O. Hemisphere-aware
(southern hemisphere flips DJF↔JJA / MAM↔SON so DJF always means
"cold season" and JJA always means "warm season").

src.calibration.manager re-exports these for back-compat — existing
callers (harvester.py, observation_client.py, replay.py, internal
calibration usage) continue to work unchanged. NEW callers should
import directly from src.contracts.season.
"""

from __future__ import annotations

_SH_FLIP = {"DJF": "JJA", "JJA": "DJF", "MAM": "SON", "SON": "MAM"}


def season_from_month(month: int, lat: float = 90.0) -> str:
    """Map month integer to meteorological season code, hemisphere-aware."""
    if month in (12, 1, 2):
        season = "DJF"
    elif month in (3, 4, 5):
        season = "MAM"
    elif month in (6, 7, 8):
        season = "JJA"
    else:
        season = "SON"
    return _SH_FLIP[season] if lat < 0 else season


def hemisphere_for_lat(lat: float) -> str:
    """Return 'N' for Northern Hemisphere, 'S' for Southern (equator = N)."""
    return "N" if lat >= 0 else "S"


def season_from_date(date_str: str, lat: float = 90.0) -> str:
    """Map date string to meteorological season code, hemisphere-aware.

    For Southern Hemisphere (lat < 0), labels are flipped so that
    DJF always means "cold season" and JJA always means "warm season",
    regardless of hemisphere.
    """
    month = int(date_str.split("-")[1])
    if month in (12, 1, 2):
        season = "DJF"
    elif month in (3, 4, 5):
        season = "MAM"
    elif month in (6, 7, 8):
        season = "JJA"
    else:
        season = "SON"
    return _SH_FLIP[season] if lat < 0 else season
