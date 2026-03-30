"""Position monitor: variable-frequency exit trigger checking. Spec §6.3.

Frequency scales with time to settlement:
  >48h:  every 6h
  24-48h: every 2h
  12-24h: every 1h
  4-12h:  every 30min
  <4h:    every 15min

ONLY checks exit triggers. Does NOT re-evaluate entry.
"""

import logging
from datetime import datetime, timezone

from src.config import settings
from src.execution.exit_triggers import evaluate_exit_triggers, clear_reversal_state
from src.state.chronicler import log_event
from src.state.db import get_connection
from src.state.portfolio import (
    load_portfolio, save_portfolio, remove_position, PortfolioState,
)

logger = logging.getLogger(__name__)


def get_check_interval_minutes(hours_to_settlement: float) -> int:
    """Determine check interval based on time to settlement."""
    if hours_to_settlement < 4:
        return 15
    elif hours_to_settlement < 12:
        return 30
    elif hours_to_settlement < 24:
        return 60
    elif hours_to_settlement < 48:
        return 120
    else:
        return 360


def run_monitor() -> int:
    """Check exit triggers on all held positions. Returns number of exits."""
    portfolio = load_portfolio()

    if not portfolio.positions:
        return 0

    conn = get_connection()
    exits = 0

    for pos in list(portfolio.positions):
        try:
            # TODO: Recompute current_p_posterior from fresh ENS + calibration
            # Current p_market refreshed via VWMP in update_reaction.
            # Monitor uses stored posterior — conservative for non-ENS-update windows.
            # EDGE_REVERSAL still fires if market moved significantly past posterior.
            signal = evaluate_exit_triggers(
                position=pos,
                current_p_posterior=pos.p_posterior,
                current_p_market=pos.entry_price,
            )

            if signal is not None:
                logger.info("MONITOR EXIT %s: %s — %s",
                            pos.trade_id, signal.trigger, signal.reason)
                remove_position(portfolio, pos.trade_id)
                clear_reversal_state(pos.trade_id)
                log_event(conn, "EXIT", pos.trade_id, {
                    "trigger": signal.trigger, "reason": signal.reason,
                    "source": "monitor",
                })
                conn.commit()
                exits += 1

        except Exception as e:
            logger.error("Monitor check failed for %s: %s", pos.trade_id, e)

    if exits > 0:
        save_portfolio(portfolio)

    conn.close()
    return exits
