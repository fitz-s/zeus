# Created: 2026-06-02
# Last reused or audited: 2026-06-02
# Authority basis: EMOS-CI LIVE WIRING (Option B, /tmp/design_emos_ci.md §6);
#   per-city license gate for the live emos_q_lcb override; operator CI-honesty law.
"""EMOS-CI per-city license loader.

The live emos_q_lcb override (event_reactor_adapter._canonical_probability_and_fdr_proof)
replaces the MC q_5pct (lcb_by_direction) with the coverage-honest EMOS analytic CI
ONLY for cities present in this license file AND only when the live flag
(edli_v1.edli_emos_ci_live_enabled) is True.

The file is operator-armed: it is intentionally NOT created by this build. Absent file
→ empty license → no city licensed → the override never fires (flag-OFF / no-city is the
default and the override is byte-identical to the MC path).

Schema (state/emos_ci_license.json):
    {
      "_meta": {... free-form provenance ...},
      "cities": {
        "Sao Paulo": {
          "k_cov": 1.0,          # sigma-inflation factor (>= 1.0; clamped on read)
          "cov90": 0.883,        # forward-derived coverage (provenance only)
          "bin_bias_24": 0.32,   # provenance only
          "licensed_at": "..."   # ISO-8601 (provenance only)
        },
        ...
      }
    }

FAIL-OPEN on load: file absent / malformed / unreadable → {} → no city licensed.
The override is the ONLY consumer; an empty license simply means the live decision is
unchanged (the MC lcb stands). This is the safe direction.

Public API:
  load_emos_ci_license()           — cached dict[city -> {"k_cov": float, ...}].
  emos_ci_k_cov(city)              — k_cov for a licensed city, or None if not licensed.
  reset_emos_ci_license_cache()    — test hook; clears the process cache.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_STATE_DIR = Path(__file__).parent.parent.parent / "state"
_LICENSE_PATH = _STATE_DIR / "emos_ci_license.json"

# k_cov is a sigma-inflation factor and must never tighten sigma (operator CI-honesty
# law: a tighter CI that under-covers is forbidden). Any value < 1.0 in the file is
# clamped up to 1.0 on read so a fat-fingered license can never produce an optimistic CI.
_K_COV_FLOOR = 1.0

_license_cache: dict | None = None
_license_lock = threading.Lock()


def reset_emos_ci_license_cache() -> None:
    """Clear the process-level license cache (test hook).

    The license is cached once per process; tests that write a temp license file
    must call this to force a re-read.
    """
    global _license_cache
    with _license_lock:
        _license_cache = None


def load_emos_ci_license() -> dict[str, dict]:
    """Return the cached per-city EMOS-CI license map.

    Returns dict[city_name -> cell] where cell is the raw per-city dict
    (at minimum {"k_cov": float}). FAIL-OPEN: file missing / malformed / unreadable
    → {} (no city licensed → override never fires).

    The license is loaded once per process. Use reset_emos_ci_license_cache() in tests
    to force a re-read after writing a temp file.
    """
    global _license_cache
    if _license_cache is not None:
        return _license_cache
    with _license_lock:
        if _license_cache is not None:
            return _license_cache
        cities: dict[str, dict] = {}
        try:
            raw = _LICENSE_PATH.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                logger.warning("emos_ci_license.json is not a dict — treating as empty")
                data = {}
            cells = data.get("cities", {})
            if isinstance(cells, dict):
                for city, cell in cells.items():
                    if isinstance(cell, dict):
                        cities[str(city)] = cell
                    else:
                        logger.warning(
                            "emos_ci_license: city %r cell is not a dict — skipping", city
                        )
        except FileNotFoundError:
            logger.debug("state/emos_ci_license.json not found; EMOS-CI live override disabled")
        except Exception as exc:  # malformed JSON, permissions, etc.
            logger.warning("Failed to load emos_ci_license.json: %s", exc)
        _license_cache = cities
    return _license_cache


def emos_ci_k_cov(city: str) -> Optional[float]:
    """Return the licensed k_cov for ``city``, or None if the city is not licensed.

    The returned k_cov is clamped to >= 1.0 (sigma is never tightened). A licensed
    cell missing/with a malformed ``k_cov`` defaults to 1.0 (the honest k_cov=1 band).

    Args:
        city: City name (must match the family.city / calibration-table key exactly).

    Returns:
        float >= 1.0 if licensed, else None (override does not fire for this city).
    """
    cell = load_emos_ci_license().get(city)
    if cell is None:
        return None
    try:
        k = float(cell.get("k_cov", _K_COV_FLOOR))
    except (TypeError, ValueError):
        k = _K_COV_FLOOR
    if not (k >= _K_COV_FLOOR):  # NaN-safe: NaN fails the comparison → floor
        k = _K_COV_FLOOR
    return k
