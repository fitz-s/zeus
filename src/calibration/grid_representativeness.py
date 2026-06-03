# Created: 2026-06-02
# Last reused or audited: 2026-06-02
# Authority basis: docs/operations/FORECAST_COLD_ROOT_UNIVERSAL_2026-06-02.md
#   Per-(city,season) grid→point representativeness offset loader.
#   Table fit by scripts/fit_grid_representativeness_offset.py.
#   Lead-invariant, OOS-validated, shrunk (×0.85) bias toward settlement station.
"""Grid→point representativeness offset loader (read-only, no DB, no I/O at call time).

Loads ``state/grid_representativeness_offset.json`` once (module-level cache) and
exposes a single query helper.

Two JSON schemas are accepted (see fit script / _meta.authority for provenance):

  v1 season-nested (authority grid_point_representativeness_offset_v1):
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

  recency_v2 flat (authority grid_point_representativeness_recency_v2,
  _meta.estimator='recency_trailing'): offset_c lives at the city level and is
  season-agnostic (a trailing-window estimate is not season-keyed):
  {
    "_meta": {...},
    "cities": {
      "<CityName>": { "offset_c": float, "activated": bool, ... }
    }
  }

get_offset() detects the shape per city. state/grid_representativeness_offset.json
is a gitignored generated artifact; its producer (hence schema) can drift, so the
loader tolerates both rather than silently returning None for a whole valid table.

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

    Two table schemas are accepted (SCHEMA-VERSION TOLERANCE, 2026-06-02):

      * ``v1`` season-nested  — ``cities[city][season] = {offset_c, activated, ...}``
        (producer: ``scripts/fit_grid_representativeness_offset.py``, authority
        ``grid_point_representativeness_offset_v1``). Resolved by ``season``.
      * ``recency_v2`` flat   — ``cities[city] = {offset_c, activated, ...}`` with
        ``offset_c`` at the city level and no season subkeys (authority
        ``grid_point_representativeness_recency_v2``, ``_meta.estimator=
        'recency_trailing'``). A trailing-window offset is season-agnostic by
        construction, so the city-level entry applies to every ``season``.

    The loader detects the shape per city (``'offset_c' in city_data`` ⇒ flat) so a
    live state-file regenerated under either producer reads correctly. Before this
    tolerance the loader did ``city_data.get(season)`` unconditionally and silently
    returned None for an ENTIRE flat table — disabling the grid correction whenever
    the recency producer had last written ``state/grid_representativeness_offset.json``
    (the file is a gitignored generated artifact whose producer can drift).

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
        if not isinstance(city_data, dict) or not city_data:
            return None
        # Flat recency_v2 schema: offset lives at the city level (season-agnostic).
        # Season-nested v1 schema: resolve the per-season entry.
        if "offset_c" in city_data:
            entry = city_data
        else:
            entry = city_data.get(season)
        if not isinstance(entry, dict) or not entry:
            return None
        if not entry.get("activated", False):
            return None
        return entry
    except Exception as exc:
        _LOG.warning("grid_representativeness.get_offset(%s,%s): %s", city, season, exc)
        return None
