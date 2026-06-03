# Created: 2026-06-02
# Last reused or audited: 2026-06-02
# Authority basis: docs/operations/FORECAST_COLD_ROOT_UNIVERSAL_2026-06-02.md
#   Per-(city,season) grid→point representativeness offset loader.
#   Table fit by scripts/fit_grid_representativeness_offset.py.
#   Lead-invariant, OOS-validated, shrunk (×0.85) bias toward settlement station.
"""Grid→point representativeness offset loader (read-only, no DB, no I/O at call time).

Loads ``state/grid_representativeness_offset.json`` once (module-level cache) and
exposes a single query helper.

Schema of the JSON (see fit script for provenance):
  {
    "_meta": {...},
    "cities": {
      "<CityName>": {
        "<SEASON>": {
          "offset_c": float,   # mean(ENS_member_mean - obs) × shrink; degC
          "activated": bool,   # True iff passes OOS gate (|offset|≥MIN and OOS residual better)
          ...
        },
        ...
      }
    }
  }

CONVENTION (from spec):
  corrected_member = raw_member - offset_native
  where offset_native = offset_c × 1.8 for F-settled cities, else offset_c.
  offset_c is negative for cold-biased ENS (subtracting a negative warms the members).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

_LOG = logging.getLogger("zeus.grid_representativeness")

_OFFSET_TABLE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "state",
    "grid_representativeness_offset.json",
)

_cache: dict[str, Any] | None = None
_cache_lock = threading.Lock()


def _load_table() -> dict[str, Any]:
    """Load the offset table from disk (cached after first call, thread-safe)."""
    global _cache
    if _cache is not None:
        return _cache
    with _cache_lock:
        if _cache is not None:
            return _cache
        try:
            with open(_OFFSET_TABLE_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            _cache = data
            return data
        except Exception as exc:
            _LOG.warning("grid_representativeness: failed to load offset table: %s", exc)
            _cache = {}
            return {}


def load_offset_table() -> dict[str, Any]:
    """Return the raw parsed table dict (for inspection / testing)."""
    return _load_table()


def get_offset(city: str, season: str, metric: str = "high") -> dict[str, Any] | None:
    """Return the offset entry for (city, season) if activated, else None.

    The current table is fit for ``metric='high'`` ONLY. The ``metric`` parameter
    is now LOAD-BEARING (codex P1, 2026-06-02): any non-'high' metric FAILS CLOSED
    (returns None), because applying a HIGH-derived city/season offset to a LOW
    member array would mix the high/low tracks and shift LOW-market p_raw by the
    wrong physical quantity. To support LOW, fit a separate low-offset table and
    relax this gate explicitly.

    Returns:
        A dict with at least ``{"offset_c": float, "activated": bool}`` if the entry
        exists, is activated, AND metric == 'high'; otherwise ``None`` (FAIL-CLOSED).
    """
    try:
        if str(metric).lower() != "high":
            # Fail closed: no LOW (or other) offsets are fit; never apply a HIGH
            # offset to a non-high family.
            return None
        table = _load_table()
        city_data = table.get("cities", {}).get(city)
        if not city_data:
            return None
        entry = city_data.get(season)
        if not entry:
            return None
        if not entry.get("activated", False):
            return None
        return entry
    except Exception as exc:
        _LOG.warning("grid_representativeness.get_offset(%s,%s): %s", city, season, exc)
        return None
