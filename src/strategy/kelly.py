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


# Per-city Kelly multiplier (asymmetric-loss policy layer, 2026-05-03).
# Authority: docs/reference/zeus_kelly_asymmetric_loss_handoff.md +
# RERUN_PLAN_v2.md §5 (D-A migration: Denver/Paris asymmetric loss moved out
# of DDD floor and into Kelly multiplier).
#
# Composition (live evaluator-side): final_kelly =
#   base_kelly × strategy_kelly_multiplier × city_kelly_multiplier × (1 - DDD_discount)
#
# Default 1.0× for cities not listed; explicit override per-city for asymmetric-
# loss preference. Operator can override via settings.json::sizing::city_kelly_multipliers
# without touching code.
DEFAULT_CITY_KELLY_MULTIPLIERS: dict[str, float] = {
    # Continental cities with strong cold-airmass penetration risk →
    # 3-hour outage at peak hour can mask large overnight bust.
    # Operator Ruling A 2026-05-03: asymmetric loss principle.
    "Denver": 0.7,
    # Paris is excluded from DDD until workstream A LFPB resync completes;
    # the multiplier is registered now so the wiring is ready when Paris
    # re-enters the universe. Same continental cold-snap exposure as Denver.
    "Paris": 0.7,
}

_CITY_KELLY_CACHE: dict[str, float] | None = None


def _load_city_kelly_overrides() -> dict[str, float]:
    """Read settings.json::sizing::city_kelly_multipliers (best-effort).

    Operator updates this section to bless or revoke per-city overrides
    without redeploying code. Absent / malformed → empty dict (defaults
    apply).
    """
    try:
        import json as _json
        from pathlib import Path as _Path

        cfg_path = _Path(__file__).resolve().parent.parent.parent / "config" / "settings.json"
        if not cfg_path.exists():
            return {}
        cfg = _json.loads(cfg_path.read_text())
        sizing = cfg.get("sizing") or {}
        overrides = sizing.get("city_kelly_multipliers") or {}
        # Sanitize: only float-like positive values
        clean: dict[str, float] = {}
        for city, val in overrides.items():
            try:
                f = float(val)
            except (TypeError, ValueError):
                continue
            if f >= 0.0 and f <= 2.0:  # sane range; refuse > 2× Kelly amplification
                clean[city] = f
        return clean
    except Exception as exc:  # noqa: BLE001 — fail-open to defaults
        logger.warning(
            "city_kelly_multiplier override load failed: %s; using DEFAULTS only", exc
        )
        return {}


def city_kelly_multiplier(city: str | None) -> float:
    """Return the per-city Kelly multiplier, fail-OPEN to 1.0× for unknown city.

    Asymmetric-loss policy lives here, NOT in the DDD floor (RERUN_PLAN_v2.md
    §5 D-A migration). Defaults from ``DEFAULT_CITY_KELLY_MULTIPLIERS``;
    operator can override via ``config/settings.json::sizing::city_kelly_multipliers``.

    Fail-open default (1.0×) is intentional: a missing entry means "no
    asymmetric-loss adjustment for this city" — which is the correct answer
    for the 44 of 46 cities without a documented override. Compare with the
    strategy multiplier which fails-CLOSED to 0.0 for unknown strategies; the
    semantics differ because strategy_key being unknown indicates a
    mis-routing bug, whereas a city without an entry is the normal case.

    Args:
        city: City name (e.g. "Denver", "NYC"). None or empty → 1.0×.

    Returns:
        Multiplier in [0.0, 2.0] range. Typical values are 0.7–1.0.
    """
    global _CITY_KELLY_CACHE
    if _CITY_KELLY_CACHE is None:
        merged = dict(DEFAULT_CITY_KELLY_MULTIPLIERS)
        merged.update(_load_city_kelly_overrides())
        _CITY_KELLY_CACHE = merged
    name = str(city or "").strip()
    if not name:
        return 1.0
    return _CITY_KELLY_CACHE.get(name, 1.0)


def dynamic_kelly_mult(
    base: float = 0.25,
    ci_width: float = 0.0,
    lead_days: float = 0.0,
    rolling_win_rate_20: float = 0.50,
    portfolio_heat: float = 0.0,
    drawdown_pct: float = 0.0,
    max_drawdown: float = 0.20,
    strategy_key: str | None = None,
    city: str | None = None,
) -> float:
    """Compute dynamic Kelly multiplier. Spec §5.2.

    Reduces base multiplier based on uncertainty and risk state.
    All adjustments are multiplicative (cumulative).

    The optional ``city`` parameter applies a per-city asymmetric-loss
    multiplier (default 1.0× for cities without an override). This is the
    D-A migration target from RERUN_PLAN_v2.md §5 — Denver/Paris ruling-A
    asymmetric loss preferences live here, NOT in the DDD floor. Default
    None preserves legacy behavior for tests and unwired tooling. Live
    callers should pass the city name from the edge/decision context.
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
    # Per-city asymmetric-loss multiplier (D-A migration target). Applied AFTER
    # strategy gate so a 0.0 strategy mult correctly zeros the result regardless
    # of city. Default 1.0× when city is None or has no override → no behavior
    # change for legacy callers.
    if city is not None:
        m *= city_kelly_multiplier(city)
    return m
