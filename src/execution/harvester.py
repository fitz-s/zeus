"""Settlement harvester: detects settlements, generates calibration pairs, logs P&L.

Spec §8.1: Hourly cycle:
1. Poll Gamma API for recently settled weather markets
2. Determine which bin won
3. Generate calibration pairs (1 per bin per settlement)
4. Log P&L for held positions that settled
5. Remove settled positions from portfolio
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from src.calibration.manager import season_from_date
from src.calibration.store import add_calibration_pair
from src.config import City, cities_by_name
from src.data.market_scanner import _match_city, _parse_temp_range, GAMMA_BASE
from src.state.chronicler import log_event
from src.state.db import get_connection
from src.state.portfolio import (
    PortfolioState, load_portfolio, save_portfolio, remove_position,
)

logger = logging.getLogger(__name__)


def run_harvester() -> dict:
    """Run one harvester cycle. Polls for settled markets.

    Returns: {"settlements_found": int, "pairs_created": int, "positions_settled": int}
    """
    conn = get_connection()
    portfolio = load_portfolio()

    settled_events = _fetch_settled_events()
    logger.info("Harvester: found %d settled events", len(settled_events))

    total_pairs = 0
    positions_settled = 0

    for event in settled_events:
        try:
            city = _match_city(
                (event.get("title") or "").lower(),
                event.get("slug", ""),
            )
            if city is None:
                continue

            target_date = _extract_target_date(event)
            if target_date is None:
                continue

            winning_label, winning_range = _find_winning_bin(event)
            if winning_label is None:
                continue

            # Extract all bin labels and check for ENS snapshot
            all_labels = _extract_all_bin_labels(event)
            p_raw_vector = _get_stored_p_raw(conn, city.name, target_date)

            # Generate calibration pairs
            n = harvest_settlement(
                conn, city, target_date, winning_label,
                all_labels, p_raw_vector,
            )
            total_pairs += n

            # Settle held positions in this market
            n_settled = _settle_positions(
                conn, portfolio, city.name, target_date, winning_label
            )
            positions_settled += n_settled

        except Exception as e:
            logger.error("Harvester error for event %s: %s",
                         event.get("slug", "?"), e)

    if positions_settled > 0:
        save_portfolio(portfolio)

    conn.commit()
    conn.close()

    return {
        "settlements_found": len(settled_events),
        "pairs_created": total_pairs,
        "positions_settled": positions_settled,
    }


def _fetch_settled_events() -> list[dict]:
    """Poll Gamma API for recently settled weather markets."""
    events = []
    offset = 0

    while True:
        try:
            resp = httpx.get(f"{GAMMA_BASE}/events", params={
                "closed": "true",
                "limit": 200,
                "offset": offset,
            }, timeout=15.0)
            resp.raise_for_status()
            batch = resp.json()
        except httpx.HTTPError as e:
            logger.warning("Gamma API fetch failed: %s", e)
            break

        if not batch:
            break

        # Filter to temperature events only
        for event in batch:
            title = (event.get("title") or "").lower()
            if any(kw in title for kw in ("temperature", "°f", "°c")):
                events.append(event)

        if len(batch) < 200:
            break
        offset += 200

    return events


def _find_winning_bin(event: dict) -> tuple[Optional[str], Optional[str]]:
    """Determine which bin won from a settled event.

    Returns: (winning_label, winning_range) or (None, None)
    Primary: market["winningOutcome"] == "Yes"
    Fallback: outcomePrices[0] >= 0.95
    """
    for market in event.get("markets", []):
        winning = market.get("winningOutcome", "").lower()

        if winning == "yes":
            label = market.get("question") or market.get("groupItemTitle", "")
            low, high = _parse_temp_range(label)
            range_str = _format_range(low, high)
            return label, range_str

        # Fallback: check outcome prices
        prices_raw = market.get("outcomePrices", "[]")
        if isinstance(prices_raw, str):
            try:
                prices = json.loads(prices_raw)
            except (json.JSONDecodeError, TypeError):
                continue
        else:
            prices = prices_raw

        if prices and len(prices) > 0 and float(prices[0]) >= 0.95:
            label = market.get("question") or market.get("groupItemTitle", "")
            low, high = _parse_temp_range(label)
            range_str = _format_range(low, high)
            return label, range_str

    return None, None


def _format_range(low: Optional[float], high: Optional[float]) -> str:
    """Format parsed range as settlement-style string."""
    if low is None and high is not None:
        return f"-999-{int(high)}"
    elif high is None and low is not None:
        return f"{int(low)}-999"
    elif low is not None and high is not None:
        return f"{int(low)}-{int(high)}"
    return "unknown"


def _extract_all_bin_labels(event: dict) -> list[str]:
    """Extract all bin labels from a settled event."""
    labels = []
    for market in event.get("markets", []):
        label = market.get("question") or market.get("groupItemTitle", "")
        if label:
            labels.append(label)
    return labels


def _extract_target_date(event: dict) -> Optional[str]:
    """Extract target date from event."""
    from src.data.market_scanner import _parse_target_date
    return _parse_target_date(event)


def _get_stored_p_raw(conn, city: str, target_date: str) -> Optional[list[float]]:
    """Get stored P_raw vector from ensemble_snapshots."""
    row = conn.execute("""
        SELECT p_raw_json FROM ensemble_snapshots
        WHERE city = ? AND target_date = ?
        ORDER BY fetch_time DESC LIMIT 1
    """, (city, target_date)).fetchone()

    if row and row["p_raw_json"]:
        try:
            return json.loads(row["p_raw_json"])
        except (json.JSONDecodeError, TypeError):
            pass
    return None


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

    Creates one pair per bin. Winning bin gets outcome=1, others get outcome=0.
    Returns: number of pairs created.
    """
    season = season_from_date(target_date)
    now = forecast_available_at or datetime.now(timezone.utc).isoformat()

    count = 0
    for i, label in enumerate(bin_labels):
        outcome = 1 if label == winning_bin_label else 0
        p_raw = p_raw_vector[i] if p_raw_vector and i < len(p_raw_vector) else None

        if p_raw is None:
            continue

        add_calibration_pair(
            conn, city=city.name, target_date=target_date,
            range_label=label, p_raw=p_raw, outcome=outcome,
            lead_days=lead_days, season=season, cluster=city.cluster,
            forecast_available_at=now, settlement_value=settlement_value,
        )
        count += 1

    logger.info("Harvested %d pairs for %s %s (winner: %s)",
                count, city.name, target_date, winning_bin_label)
    return count


def _settle_positions(
    conn, portfolio: PortfolioState,
    city: str, target_date: str, winning_label: str,
) -> int:
    """Settle held positions that match this market. Log P&L."""
    settled = 0
    for pos in list(portfolio.positions):
        if pos.city != city or pos.target_date != target_date:
            continue

        # Determine P&L — correct formula: shares × exit_price - cost_basis
        # Rainstorm comparison found the old formula underestimated winning P&L
        won = pos.bin_label == winning_label
        shares = pos.size_usd / pos.entry_price if pos.entry_price > 0 else 0
        if pos.direction == "buy_yes":
            exit_price = 1.0 if won else 0.0
        else:
            exit_price = 1.0 if not won else 0.0
        pnl = shares * exit_price - pos.size_usd

        log_event(conn, "SETTLEMENT", pos.trade_id, {
            "city": city, "target_date": target_date,
            "winning_bin": winning_label, "position_bin": pos.bin_label,
            "direction": pos.direction, "won": won,
            "pnl": round(pnl, 2), "entry_price": pos.entry_price,
        })

        remove_position(portfolio, pos.trade_id)
        settled += 1

        logger.info("SETTLED %s: %s %s %s — PnL=$%.2f",
                     pos.trade_id, "WON" if won else "LOST",
                     pos.direction, pos.bin_label, pnl)

    return settled
