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
# S2 R4 P10B: keyed by (city, temperature_metric) to prevent LOW from
# inheriting HIGH oracle error rates. Legacy flat JSON shape is migrated
# on load as (city, "high") entries for backward-compat.
_cache: dict[tuple[str, str], OracleInfo] | None = None

_DEFAULT_OK = OracleInfo(0.0, OracleStatus.OK, 1.0)


def _classify_rate(rate: float) -> OracleInfo:
    """Classify a single oracle error rate into OracleInfo."""
    if rate > CAUTION_THRESHOLD:
        return OracleInfo(error_rate=rate, status=OracleStatus.BLACKLIST, penalty_multiplier=0.0)
    elif rate > INCIDENTAL_THRESHOLD:
        # Linear penalty: 3% → 0.97×, 10% → 0.90×
        return OracleInfo(error_rate=rate, status=OracleStatus.CAUTION, penalty_multiplier=1.0 - rate)
    elif rate > 0.0:
        return OracleInfo(error_rate=rate, status=OracleStatus.INCIDENTAL, penalty_multiplier=1.0)
    else:
        return OracleInfo(error_rate=rate, status=OracleStatus.OK, penalty_multiplier=1.0)


def _load() -> dict[tuple[str, str], OracleInfo]:
    """Load oracle error rates from shadow storage JSON.

    Supports two JSON shapes:
      - Nested (current): {city: {high: {oracle_error_rate: N, ...}, low: {...}}}
      - Legacy flat:      {city: {oracle_error_rate: N, ...}}

    Legacy flat shape is loaded as (city, "high") entries only; LOW starts empty.
    """
    if not _ORACLE_FILE.exists():
        logger.warning("oracle_error_rates.json not found at %s — all cities OK", _ORACLE_FILE)
        return {}

    with open(_ORACLE_FILE) as f:
        raw = json.load(f)

    result: dict[tuple[str, str], OracleInfo] = {}
    for city, data in raw.items():
        if not isinstance(data, dict):
            continue
        # Detect nested shape: value dict has "high" or "low" sub-keys
        if "high" in data or "low" in data:
            for metric in ("high", "low"):
                metric_data = data.get(metric)
                if isinstance(metric_data, dict):
                    rate = float(metric_data.get("oracle_error_rate", 0.0))
                    result[(city, metric)] = _classify_rate(rate)
        else:
            # Legacy flat shape: treat entire dict as (city, "high")
            rate = float(data.get("oracle_error_rate", 0.0))
            result[(city, "high")] = _classify_rate(rate)

    return result


def reload() -> None:
    """Force reload of oracle error rates (e.g. after bridge script runs).

    Non-fatal on error. If the file is malformed, mid-write, or unreadable,
    we keep the previous _cache (or fall through to empty/_DEFAULT_OK) and
    log a warning. The evaluator calls this every cycle; a crash here would
    halt every candidate evaluation, the exact failure mode PR #40 was opened
    to remove. Oracle is a sizing modifier, not a truth gate.
    """
    global _cache
    try:
        new_cache = _load()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning(
            "oracle_penalty reload failed (%s: %s); keeping previous cache "
            "(or empty if first load)", type(exc).__name__, exc,
        )
        if _cache is None:
            _cache = {}
        return
    _cache = new_cache
    logger.info("oracle_penalty reloaded: %d entries, %d blacklisted",
                len(_cache),
                sum(1 for v in _cache.values() if v.status == OracleStatus.BLACKLIST))


def get_oracle_info(city_name: str, temperature_metric: str = "high") -> OracleInfo:
    """Return oracle info for a (city, metric) pair. Unknown pairs default to OK.

    S2 R4 P10B: keyed by (city, temperature_metric) to prevent LOW from
    inheriting HIGH oracle error rates.
    """
    global _cache
    if _cache is None:
        _cache = _load()
    return _cache.get((city_name, temperature_metric), _DEFAULT_OK)


def is_blacklisted(city_name: str, temperature_metric: str = "high") -> bool:
    """Check if a (city, metric) pair is oracle-blacklisted (error rate >10%)."""
    return get_oracle_info(city_name, temperature_metric).status == OracleStatus.BLACKLIST
