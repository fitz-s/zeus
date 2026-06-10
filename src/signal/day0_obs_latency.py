# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: day0 first-principles review 2026-06-10 (operator charge #1).
#   Measured artifact: config/wu_obs_latency.json (scripts/measure_wu_obs_latency.py,
#   sources: zeus-world.db observation_instants wu_icao_history raw METAR ts +
#   zeus_trades.db settlement_day_observation_authority wu_api poll ages).
"""Per-city WU observation latency model for day0 decisions.

First principles: the day0 running extreme is a LOWER bound (HIGH) / UPPER
bound (LOW) whose trustworthiness decays with the age of the underlying
station report. WU publishes station obs with a city-specific cadence
(30 or 60 min METAR grid, specific minute-marks) plus a publication delay.
An observation snapshot older than the city's *staleness budget*
(report interval + publication delay) means at least one station report is
missing/unseen — the true running extreme may already have moved past the
stale one, and any bin whose life depends on the boundary within the
plausible-move envelope is UNKNOWN, not alive.

This module is read-only and fail-soft: a missing/corrupt JSON degrades to
conservative defaults (slowest observed cadence + operator-stated delay),
never to "assume fresh".

Consumers:
- src/engine/event_reactor_adapter.py day0 absorbing-mask lane
  (stale-obs boundary guard on q_lcb).
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PATH = _REPO_ROOT / "config" / "wu_obs_latency.json"

# Conservative fallbacks (fail-closed direction: assume SLOW publication).
DEFAULT_STALENESS_BUDGET_MIN = 100.0  # 60 min cadence + 40 min delay

# Plausible intraday temperature move rate used to size the boundary
# uncertainty zone for a stale running extreme. Upper-bound on sustained
# warming/cooling rate; deliberately generous (fail-closed: a larger margin
# only SUPPRESSES boundary-adjacent submits, never enables one).
_MAX_MOVE_PER_HOUR = {"C": 2.5, "F": 4.5}
# Cap the widening window: beyond this the day0 lane should not be trusted
# for boundary-adjacent bins at all (margin saturates, all near-boundary
# bins stay suppressed).
_MAX_WIDENING_HOURS = 6.0


@lru_cache(maxsize=1)
def _load_model(path_str: str = str(_DEFAULT_PATH)) -> dict:
    try:
        with open(path_str, "r", encoding="utf-8") as fh:
            model = json.load(fh)
        if not isinstance(model, dict) or not isinstance(model.get("cities"), dict):
            raise ValueError("wu_obs_latency.json malformed: missing 'cities' dict")
        return model
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("WU_OBS_LATENCY_MODEL_UNAVAILABLE path=%s exc=%s — conservative defaults", path_str, exc)
        return {"cities": {}, "defaults": {"staleness_budget_min": DEFAULT_STALENESS_BUDGET_MIN}}


def staleness_budget_minutes(city: str, *, path: Path | None = None) -> float:
    """Max age (minutes) at which a WU obs snapshot still plausibly reflects the
    CURRENT running extreme for this city (one report interval + publication delay).

    Unknown city / missing model -> conservative default (slow cadence).
    """
    model = _load_model(str(path) if path is not None else str(_DEFAULT_PATH))
    entry = model.get("cities", {}).get(str(city)) or {}
    budget = entry.get("staleness_budget_min")
    if budget is None:
        budget = (model.get("defaults") or {}).get("staleness_budget_min", DEFAULT_STALENESS_BUDGET_MIN)
    try:
        budget_f = float(budget)
    except (TypeError, ValueError):
        return DEFAULT_STALENESS_BUDGET_MIN
    if not budget_f > 0.0:
        return DEFAULT_STALENESS_BUDGET_MIN
    return budget_f


def stale_extreme_uncertainty_margin(
    *,
    unit: str,
    obs_age_minutes: float | None,
    budget_minutes: float,
) -> float:
    """Native-unit margin past the stale running extreme within which a bin's
    dead/alive state is UNKNOWN.

    0.0 when the obs is within the city's staleness budget (snapshot is as
    fresh as the station cadence allows — the running extreme is current
    truth up to normal cadence).

    When the obs is OLDER than the budget (missing reports), the true extreme
    may have moved by up to rate x excess_age. obs_age None/-invalid is treated
    as maximally stale (fail-closed).
    """
    rate = _MAX_MOVE_PER_HOUR.get(str(unit).upper(), _MAX_MOVE_PER_HOUR["F"])
    if obs_age_minutes is None:
        return rate * _MAX_WIDENING_HOURS
    try:
        age = float(obs_age_minutes)
    except (TypeError, ValueError):
        return rate * _MAX_WIDENING_HOURS
    if not age >= 0.0:  # NaN or negative
        return rate * _MAX_WIDENING_HOURS
    excess_hours = max(0.0, (age - float(budget_minutes)) / 60.0)
    return rate * min(excess_hours, _MAX_WIDENING_HOURS)
