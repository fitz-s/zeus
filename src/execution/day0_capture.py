"""Day0 Capture: Mode C discovery cycle. Spec §6.2.

Every 15 minutes for markets within 6 hours of settlement:
1. Fetch ASOS/WU observation (hard floor for today's high)
2. Combine with ENS remaining-hours forecast
3. Same edge pipeline (FDR → Kelly → execute)

Key insight: observation sets a hard floor. If current observed high is already
above a shoulder-high boundary, that bin is nearly certain to win.
"""

import logging
from datetime import date, datetime, timezone

import numpy as np

from src.config import settings
from src.data.ensemble_client import fetch_ensemble, validate_ensemble
from src.data.market_scanner import find_weather_markets
from src.data.observation_client import get_current_observation
from src.riskguard.riskguard import get_current_level
from src.riskguard.risk_level import RiskLevel
from src.signal.ensemble_signal import EnsembleSignal

logger = logging.getLogger(__name__)


def run_day0_capture() -> int:
    """Run one Day0 Capture cycle. Returns trades placed."""
    level = get_current_level()
    if level == RiskLevel.RED:
        logger.info("Day0 Capture skipped: RiskGuard=RED")
        return 0

    # Find markets within 6 hours of settlement
    markets = find_weather_markets(min_hours_to_resolution=0.5)
    day0_markets = [m for m in markets if m["hours_to_resolution"] < 6.0]

    if not day0_markets:
        logger.info("Day0 Capture: no markets within 6h of settlement")
        return 0

    logger.info("Day0 Capture: %d markets within 6h", len(day0_markets))

    trades = 0
    for market in day0_markets:
        try:
            city = market["city"]

            # Get current observation
            obs = get_current_observation(city)
            if obs is None:
                logger.warning("No observation for %s — skipping Day0", city.name)
                continue

            logger.info(
                "Day0 %s: observed high=%.1f%s, current=%.1f%s (source=%s)",
                city.name, obs["high_so_far"], obs["unit"],
                obs["current_temp"], obs["unit"], obs["source"],
            )

            # TODO: Implement Day0Signal class that combines observation floor
            # with ENS remaining-hours forecast, then run through edge pipeline.
            # For now, this is a stub that logs the observation.

        except Exception as e:
            logger.error("Day0 error for %s: %s", market["city"].name, e)

    return trades
