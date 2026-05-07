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
) -> float:
    """Compute position size using fractional Kelly criterion. Spec §5.1.

    Returns: size in USD. Returns 0.0 if no positive edge.
    entry_price: MUST be a typed ExecutionPrice (DT#5 / INV-21 strict —
        assert_kelly_safe() is called unconditionally, raising
        ExecutionPriceContractError if the price is not suitable for Kelly
        sizing). Bare floats are forbidden at this boundary (P10E).

    Per-trade safety-cap authority was removed 2026-05-04. Per-cycle
    exposure discipline now lives in posture / RiskGuard / max-exposure gates
    only (see ``config/settings.json::_bankroll_doctrine_2026_05_04``).
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
    return f_star * kelly_mult * bankroll


def strategy_kelly_multiplier(strategy_key: str | None) -> float:
    """Return the live sizing multiplier for a strategy key, fail-closed.

    Pre-A4: read from a hardcoded ``STRATEGY_KELLY_MULTIPLIERS`` dict
    defined in this file. Post-A4: read through
    ``src.strategy.strategy_profile.kelly_default_multiplier`` which
    delegates to ``architecture/strategy_profile_registry.yaml`` (single
    source of truth — see PLAN.md §A4 + Bug review §D). Fail-closed
    behavior unchanged: unknown / empty key returns 0.0.

    Post-A6: ``phase_aware_kelly_multiplier`` is the canonical entry-time
    resolver (PLAN.md §A6). This function remains for back-compat with
    callers that don't yet have phase / oracle / decision_time in scope
    (e.g., dynamic_kelly_mult cascade-floor checks). New sites should
    prefer ``phase_aware_kelly_multiplier``.
    """
    from src.strategy.strategy_profile import kelly_default_multiplier as _kdm
    return _kdm(str(strategy_key or "").strip())


def observed_target_day_fraction(
    *,
    decision_time_utc,
    target_local_date,
    city_timezone: str,
) -> float:
    """Fraction of the city-local target day that has elapsed at
    ``decision_time_utc``, clamped to [0.0, 1.0].

    PLAN.md §A6 layer of the phase-aware Kelly resolver. The
    settlement_capture strategy's edge depends on how much of the
    target day has been observed: at city-local 00:00 of target_date
    the fraction is 0 (the day hasn't started — pure forecast play);
    at city-local 24:00 of target_date the fraction is 1 (the day
    is fully observed — peak alpha). The resolver scales Kelly
    proportionally so the bot doesn't bet at full size against an
    incomplete observation window.

    East/west asymmetry (per PLAN_v3 §3 + Bug review §6.7): at a
    fixed UTC instant, eastward-of-UTC cities (Wellington) are
    further into their local day than westward-of-UTC cities (LA).
    The fraction captures this asymmetry directly — it does NOT need
    a separate east/west tag.

    Implementation: compute target_local_start (city-local 00:00 of
    target_local_date) and target_local_end (city-local 00:00 of the
    next day). Convert decision_time_utc to the city's local clock
    via astimezone, then compute (decision_local - target_local_start)
    / (target_local_end - target_local_start) and clamp.

    Note on DST: target_local_end - target_local_start in wall-clock
    is 23h, 24h, or 25h depending on whether the city had a DST
    transition during target_date. This is the CORRECT denominator
    for computing "fraction of the local day elapsed" — the local
    day's actual length, not a fixed 24h window. Anchoring on a fixed
    24h would introduce a ±1h fraction skew on DST days.
    """
    from datetime import datetime, time, timedelta, timezone
    from zoneinfo import ZoneInfo

    if decision_time_utc.tzinfo is None:
        raise ValueError(
            f"decision_time_utc must be tz-aware; got naive {decision_time_utc!r}"
        )

    tz = ZoneInfo(city_timezone)
    target_local_start = datetime.combine(target_local_date, time(0, 0, 0), tzinfo=tz)
    target_local_end = datetime.combine(
        target_local_date + timedelta(days=1), time(0, 0, 0), tzinfo=tz
    )

    # Convert all three endpoints to UTC before subtracting. Python's
    # ZoneInfo-aware datetime arithmetic returns the WALL-CLOCK
    # difference, NOT the actual elapsed UTC duration: a tz-aware
    # 23h-DST-day from 00:00 to 24:00 yields a 24h timedelta, which
    # would skew the fraction by ~4% on DST transition days. Going
    # through UTC fixes this.
    start_utc = target_local_start.astimezone(timezone.utc)
    end_utc = target_local_end.astimezone(timezone.utc)
    decision_utc = decision_time_utc.astimezone(timezone.utc)

    elapsed = (decision_utc - start_utc).total_seconds()
    total = (end_utc - start_utc).total_seconds()
    if total <= 0:
        return 0.0
    fraction = elapsed / total
    if fraction < 0.0:
        return 0.0
    if fraction > 1.0:
        return 1.0
    return fraction


# A6 phase-aware Kelly resolver factor floors / overrides.
#
# Why each floor exists:
#
# - ``OBSERVED_FRACTION_MIN = 0.3``: prevents Wellington-style cities at
#   12:00 UTC from getting Kelly=0 just because their local target_day
#   has barely started. Operator-tunable; below 0.3 the day-start case
#   becomes too punitive on early-Day0 settlement_capture entries.
# - ``FALLBACK_F1_HAIRCUT = 0.7``: F1 anchor is verified across 13
#   cities (INVESTIGATION_EXTERNAL Q3 = 7 + CRITIC_REVIEW_R2 spot-check
#   = 6) but not infallible — a Polymarket schema change could move
#   endDate. The 0.7× haircut applies until the fallback is verified
#   for the specific market in question (i.e., explicit market_end_at
#   parsed cleanly = phase_source==verified_gamma).
OBSERVED_FRACTION_MIN: float = 0.3
FALLBACK_F1_HAIRCUT: float = 0.7


def phase_aware_kelly_multiplier(
    *,
    strategy_key: str,
    market_phase: str | None,
    city,
    temperature_metric: str,
    decision_time_utc,
    target_local_date,
    phase_source: str | None,
) -> float:
    """Resolve the live Kelly multiplier from four authority sources.

    Resolver formula (PLAN.md §A6, written to
    ``decision_chain.kelly_multiplier_used`` at open-time)::

        m_strategy_phase    = registry.get(key).kelly_for_phase(market_phase)
        m_oracle            = oracle_penalty.get_oracle_info(city, metric).penalty_multiplier
        m_observed_fraction = max(0.3, observed_target_day_fraction(...))
        m_phase_source      = 0.7 if phase_source == "fallback_f1" else 1.0
        kelly_multiplier    = product of the four

    Migration policy (PLAN_v3 §6.P5 OD7): existing positions retain
    whatever multiplier was on ``decision_chain.kelly_multiplier_used``
    at THEIR open-time (already persisted). This function is called
    only at NEW open-time for new candidates. No retroactive recompute.

    Failure modes:
    - Unknown ``strategy_key``: registry returns 0.0 (fail-closed).
    - ``market_phase`` is None: ``kelly_for_phase(None)`` returns the
      strategy's default multiplier — preserves the pre-A5 fail-soft
      Kelly path. Strict callers should reject phase=None at the
      dispatch layer (see ``PhaseAuthorityViolation``) before reaching
      this resolver.
    - Oracle MISSING / METRIC_UNSUPPORTED: penalty_multiplier=0.5 / 0.0
      respectively (PLAN.md §A3 multiplier table).
    """
    from src.strategy.oracle_penalty import get_oracle_info
    from src.strategy.strategy_profile import try_get

    profile = try_get(strategy_key)
    if profile is None:
        return 0.0
    m_strategy_phase = profile.kelly_for_phase(market_phase)
    if m_strategy_phase <= 0.0:
        # Phase-blocked strategy — short-circuit so we don't spend time
        # on oracle / fraction lookups when the answer is already 0.
        return 0.0

    oracle_info = get_oracle_info(getattr(city, "name", ""), temperature_metric)
    m_oracle = oracle_info.penalty_multiplier
    if m_oracle <= 0.0:
        return 0.0

    m_observed_fraction = max(
        OBSERVED_FRACTION_MIN,
        observed_target_day_fraction(
            decision_time_utc=decision_time_utc,
            target_local_date=target_local_date,
            city_timezone=getattr(city, "timezone", ""),
        ),
    )

    m_phase_source = FALLBACK_F1_HAIRCUT if phase_source == "fallback_f1" else 1.0

    return m_strategy_phase * m_oracle * m_observed_fraction * m_phase_source


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
    # disable shadow, dormant, or unknown strategies via the registry's
    # kelly_default_multiplier (was STRATEGY_KELLY_MULTIPLIERS pre-A4).
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
