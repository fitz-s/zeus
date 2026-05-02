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

DT#5 / INV-21 (Phase 10E strict enforcement):
  `entry_price` MUST be a typed `ExecutionPrice`. Bare float callers are
  forbidden at this boundary — `kelly_size` calls `assert_kelly_safe()`
  unconditionally. See `docs/authority/zeus_current_architecture.md §20`
  for the law.
"""

import logging

import numpy as np

from src.contracts.execution_price import ExecutionPrice
from src.contracts.provenance_registry import require_provenance

logger = logging.getLogger(__name__)


def kelly_size(
    p_posterior: float,
    entry_price: ExecutionPrice,
    bankroll: float,
    kelly_mult: float = 0.25,
    safety_cap_usd: float | None = None,
) -> float:
    """Compute position size using fractional Kelly criterion. Spec §5.1.

    Returns: size in USD. Returns 0.0 if no positive edge.
    entry_price: MUST be a typed ExecutionPrice (DT#5 / INV-21 strict —
        assert_kelly_safe() is called unconditionally, raising
        ExecutionPriceContractError if the price is not suitable for Kelly
        sizing). Bare floats are forbidden at this boundary (P10E).
    safety_cap_usd: optional hard ceiling in USD. When provided, clips output
        and emits a structured log record with the original pre-clip size.
    """
    # DT#5 P10E: strict — assert_kelly_safe() runs unconditionally.
    entry_price.assert_kelly_safe()
    price_value = entry_price.value

    if price_value <= 0.0 or price_value >= 1.0:
        return 0.0
    if bankroll <= 0.0:
        return 0.0
    if not (0.0 <= p_posterior <= 1.0):
        return 0.0
    if p_posterior <= price_value:
        return 0.0

    f_star = (p_posterior - price_value) / (1.0 - price_value)
    raw_proposal = f_star * kelly_mult * bankroll

    if safety_cap_usd is not None and raw_proposal > safety_cap_usd:
        logger.info(
            "kelly_sized",
            extra={"capped_by_safety_cap": True, "raw_proposal": raw_proposal},
        )
        return safety_cap_usd
    return raw_proposal


STRATEGY_KELLY_MULTIPLIERS = {
    "settlement_capture": 1.0,
    "center_buy": 1.0,
    "opening_inertia": 0.5,
    "shoulder_sell": 0.0,
    "shoulder_buy": 0.0,
    "center_sell": 0.0,
}


def strategy_kelly_multiplier(strategy_key: str | None) -> float:
    """Return the live sizing multiplier for a strategy key, fail-closed."""

    return STRATEGY_KELLY_MULTIPLIERS.get(str(strategy_key or "").strip(), 0.0)


def dynamic_kelly_mult(
    base: float = 0.25,
    ci_width: float = 0.0,
    lead_days: float = 0.0,
    rolling_win_rate_20: float = 0.50,
    portfolio_heat: float = 0.0,
    drawdown_pct: float = 0.0,
    max_drawdown: float = 0.20,
    strategy_key: str | None = None,
) -> float:
    """Compute dynamic Kelly multiplier. Spec §5.2.

    Reduces base multiplier based on uncertainty and risk state.
    All adjustments are multiplicative (cumulative).
    """
    # C1/INV-13: provenance check — kelly_mult is registered in provenance_registry.yaml
    require_provenance("kelly_mult")

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

    # INV-05 / §P9.7: cascade floor — risk inputs must never collapse to zero or NaN.
    # Note: This check applies to the upstream Kelly computation before per-strategy
    # gating. The final multiplier step (below) can legitimately produce 0.0 to
    # disable shadow, dormant, or unknown strategies via STRATEGY_KELLY_MULTIPLIERS.
    if not (m == m):  # NaN check: NaN != NaN
        raise ValueError(
            f"dynamic_kelly_mult produced NaN (base={base}, ci_width={ci_width}, "
            f"lead_days={lead_days}, rolling_win_rate_20={rolling_win_rate_20}, "
            f"portfolio_heat={portfolio_heat}, drawdown_pct={drawdown_pct})"
        )
    if m <= 0.0:
        raise ValueError(
            f"dynamic_kelly_mult collapsed to {m} — all sizing gates triggered, "
            f"refusing to fabricate a floor value"
        )
    if strategy_key is not None:
        m *= strategy_kelly_multiplier(strategy_key)
    return m
