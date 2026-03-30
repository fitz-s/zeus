"""Settlement harvester: detects settlements, generates calibration pairs, logs P&L.

Spec §8.1: After each settlement:
1. Determine which bin won (from Polymarket/Gamma API)
2. Generate 11 calibration pairs (1 outcome=1 per winning bin, 10 outcome=0)
3. If bucket milestone hit, trigger Platt refit
4. Log P&L for held positions in this market
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from src.calibration.manager import season_from_date, route_to_bucket
from src.calibration.store import add_calibration_pair, get_pairs_count
from src.config import City
from src.state.db import get_connection

logger = logging.getLogger(__name__)


def harvest_settlement(
    conn,
    city: City,
    target_date: str,
    winning_bin_label: str,
    bin_labels: list[str],
    p_raw_vector: Optional[list[float]] = None,
    lead_days: float = 3.0,
    forecast_available_at: Optional[str] = None,
    settlement_value: Optional[float] = None,
) -> int:
    """Generate calibration pairs from a settled market.

    Creates one pair per bin (11 total for standard market).
    Winning bin gets outcome=1, all others get outcome=0.

    Returns: number of pairs created.
    """
    season = season_from_date(target_date)
    now = forecast_available_at or datetime.now(timezone.utc).isoformat()

    count = 0
    for i, label in enumerate(bin_labels):
        outcome = 1 if label == winning_bin_label else 0
        p_raw = p_raw_vector[i] if p_raw_vector and i < len(p_raw_vector) else None

        if p_raw is None:
            continue  # Can't create calibration pair without P_raw

        add_calibration_pair(
            conn,
            city=city.name,
            target_date=target_date,
            range_label=label,
            p_raw=p_raw,
            outcome=outcome,
            lead_days=lead_days,
            season=season,
            cluster=city.cluster,
            forecast_available_at=now,
            settlement_value=settlement_value,
        )
        count += 1

    logger.info(
        "Harvested %d calibration pairs for %s %s (winner: %s)",
        count, city.name, target_date, winning_bin_label,
    )

    return count
