"""Kelly criterion sizing with dynamic multiplier.

Spec §5.1-5.2: Per-bin Kelly with guardrails.
Base formula: f* = (p_posterior - entry_price) / (1 - entry_price)
Size = f* × kelly_mult × bankroll

Dynamic multiplier reduces sizing when:
- CI is wide (uncertain edge)
- Lead time is long (forecast decays)
- Recent win rate is poor
- Portfolio is concentrated
- In drawdown
"""

import numpy as np


def kelly_size(
    p_posterior: float,
    entry_price: float,
    bankroll: float,
    kelly_mult: float = 0.25,
) -> float:
    """Compute position size using fractional Kelly criterion. Spec §5.1.

    Returns: size in USD. Returns 0.0 if no positive edge.
    entry_price: cost per share in [0.01, 0.99].
    """
    if p_posterior <= entry_price:
        return 0.0
    if entry_price >= 1.0:
        return 0.0

    f_star = (p_posterior - entry_price) / (1.0 - entry_price)
    return f_star * kelly_mult * bankroll


def dynamic_kelly_mult(
    base: float = 0.25,
    ci_width: float = 0.0,
    lead_days: float = 0.0,
    rolling_win_rate_20: float = 0.50,
    portfolio_heat: float = 0.0,
    drawdown_pct: float = 0.0,
    max_drawdown: float = 0.20,
) -> float:
    """Compute dynamic Kelly multiplier. Spec §5.2.

    Reduces base multiplier based on uncertainty and risk state.
    All adjustments are multiplicative (cumulative).
    """
    m = base

    # CI width: wider CI → less confident → smaller size
    if ci_width > 0.10:
        m *= 0.7
    if ci_width > 0.15:
        m *= 0.5  # Cumulative: 0.25 * 0.7 * 0.5 = 0.0875

    # Lead time: longer lead → less reliable forecast
    if lead_days >= 5:
        m *= 0.6
    elif lead_days >= 3:
        m *= 0.8

    # Recent performance: losing streak → reduce exposure
    if rolling_win_rate_20 < 0.40:
        m *= 0.5
    elif rolling_win_rate_20 < 0.45:
        m *= 0.7

    # Portfolio concentration: high heat → reduce marginal sizing
    if portfolio_heat > 0.40:
        m *= max(0.1, 1.0 - portfolio_heat)

    # Drawdown: proportional reduction
    if drawdown_pct > 0 and max_drawdown > 0:
        m *= max(0.0, 1.0 - drawdown_pct / max_drawdown)

    return m
