"""Oracle penalty multiplier for Kelly sizing.

Applies a per-city sizing penalty based on historical oracle error rate.
Data lives in the shadow storage file ``data/oracle_error_rates.json``
and is loaded lazily on first access (no DB dependency).

Thresholds (user-defined 2026-04-15):
  - <3%  : incidental (偶发) — no penalty
  - 3–10%: caution   (疑虑) — proportional penalty
  - >10% : blacklist (拉黑) — trading blocked
"""

from __future__ import annotations

import json
import logging
from enum import Enum
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_ORACLE_FILE = _DATA_DIR / "oracle_error_rates.json"

# ── thresholds ────────────────────────────────────────────────────────
INCIDENTAL_THRESHOLD = 0.03   # < 3% → no penalty
CAUTION_THRESHOLD    = 0.10   # 3–10% → proportional penalty
# > 10% → blacklist


class OracleStatus(str, Enum):
    OK        = "OK"         # 0% error rate
    INCIDENTAL = "INCIDENTAL" # >0% but <3%
    CAUTION   = "CAUTION"    # 3–10%
    BLACKLIST = "BLACKLIST"  # >10%


class OracleInfo(NamedTuple):
    error_rate: float
    status: OracleStatus
    penalty_multiplier: float   # 1.0 = no penalty, 0.0 = blocked


# ── cache ─────────────────────────────────────────────────────────────
_cache: dict[str, OracleInfo] | None = None


def _load() -> dict[str, OracleInfo]:
    """Load oracle error rates from shadow storage JSON."""
    if not _ORACLE_FILE.exists():
        logger.warning("oracle_error_rates.json not found at %s — all cities OK", _ORACLE_FILE)
        return {}

    with open(_ORACLE_FILE) as f:
        raw = json.load(f)

    result: dict[str, OracleInfo] = {}
    for city, data in raw.items():
        rate = data.get("oracle_error_rate", 0.0)

        if rate > CAUTION_THRESHOLD:
            status = OracleStatus.BLACKLIST
            mult = 0.0
        elif rate > INCIDENTAL_THRESHOLD:
            status = OracleStatus.CAUTION
            # Linear penalty: 3% → 0.97×, 10% → 0.90×
            mult = 1.0 - rate
        elif rate > 0.0:
            status = OracleStatus.INCIDENTAL
            mult = 1.0  # no penalty for <3%
        else:
            status = OracleStatus.OK
            mult = 1.0

        result[city] = OracleInfo(
            error_rate=rate,
            status=status,
            penalty_multiplier=mult,
        )
    return result


def reload() -> None:
    """Force reload of oracle error rates (e.g. after bridge script runs)."""
    global _cache
    _cache = _load()
    logger.info("oracle_penalty reloaded: %d cities, %d blacklisted",
                len(_cache),
                sum(1 for v in _cache.values() if v.status == OracleStatus.BLACKLIST))


def get_oracle_info(city_name: str) -> OracleInfo:
    """Return oracle info for a city. Unknown cities default to OK."""
    global _cache
    if _cache is None:
        _cache = _load()
    return _cache.get(city_name, OracleInfo(0.0, OracleStatus.OK, 1.0))


def is_blacklisted(city_name: str) -> bool:
    """Check if a city is oracle-blacklisted (error rate >10%)."""
    return get_oracle_info(city_name).status == OracleStatus.BLACKLIST
