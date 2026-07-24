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


# One global fractional-Kelly fraction (ultimate_alpha_2026-07-23 COLLISION.md
# group B / FINAL_SPEC.md §What remains). Set to the value the dominant live
# tier (kelly_default_multiplier: 1.0) already used, so cutover day has no
# sizing regime jump; tuning κ later is a separate operator decision.
GLOBAL_KELLY_FRACTION: float = 1.0

# Lifecycle phases in which NO market can accept a new entry — a universal
# market-state fact (not tradeable), not a per-strategy permission. Replaces
# the per-key kelly_phase_overrides zeros the registry used to carry.
_NON_TRADING_PHASES: frozenset[str] = frozenset({"pre_trading", "post_trading", "resolved"})


def strategy_kelly_multiplier(strategy_key: str | None) -> float:
    """Return the sizing fraction for a strategy-identity key, fail-closed.

    One-law form (ultimate_alpha 2026-07-23): the label no longer owns
    economics — every LIVE key sizes at ``GLOBAL_KELLY_FRACTION``. What
    survives of the registry lookup is identity/permission, not economics:
    unknown/empty key → 0.0 (mis-routing bug upstream), and a non-live
    key (blocked/refuted, e.g. shoulder_sell) → 0.0 (an operator
    prohibition, not a multiplier).
    """
    from src.strategy.strategy_profile import try_get
    profile = try_get(str(strategy_key or "").strip())
    if profile is None or getattr(profile, "live_status", None) != "live":
        return 0.0
    return GLOBAL_KELLY_FRACTION


# observed_target_day_fraction DELETED (ultimate_alpha_2026-07-23 group B):
# elapsed wall-clock fraction of the target day is not the fraction of
# information observed — the Day0-conditioned posterior already carries the
# remaining-day distribution, so scaling Kelly by it taxed the same
# uncertainty twice (FINAL_SPEC.md §What remains: observed-day-fraction row).


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
    """Resolve the entry-time Kelly fraction under the one decision law.

    One-law form (ultimate_alpha_2026-07-23 COLLISION.md group B): the
    per-strategy multiplier, per-phase override, observed-day-fraction
    scaling, phase-source haircut, and oracle soft-penalty are all retired
    as sizing inputs — they double-counted uncertainty that the robust
    q_lcb bound (and hard authority gates) already carry. What remains:

    - identity fail-closed: unknown strategy_key returns 0.0 (mis-routing
      bug upstream, same posture as before);
    - lifecycle validity: a market in a non-trading phase
      (pre_trading / post_trading / resolved) can accept no entry — a
      universal market-state fact, kept here because the registry's
      per-key phase zeros were the only enforcement at this boundary;
    - hard oracle veto: a 0.0 oracle penalty means settlement truth for
      (city, metric) is UNAVAILABLE — that is an authority failure, not a
      soft haircut, so it still zeroes the size. Soft penalties (0 < m < 1)
      no longer scale size.
    - otherwise ``GLOBAL_KELLY_FRACTION``.

    Migration policy unchanged (PLAN_v3 §6.P5 OD7): existing positions
    retain their persisted open-time multiplier; no retroactive recompute.
    ``decision_time_utc`` / ``target_local_date`` / ``phase_source`` are
    retained in the signature for call-site stability; they no longer
    influence the fraction.
    """
    from src.strategy.oracle_penalty import get_oracle_info

    del decision_time_utc, target_local_date, phase_source  # no longer sizing inputs

    if strategy_kelly_multiplier(strategy_key) <= 0.0:
        return 0.0  # unknown identity or operator-blocked key — fail closed
    if market_phase is not None and str(market_phase) in _NON_TRADING_PHASES:
        return 0.0

    oracle_info = get_oracle_info(getattr(city, "name", ""), temperature_metric)
    if oracle_info.penalty_multiplier <= 0.0:
        return 0.0

    return GLOBAL_KELLY_FRACTION


# Per-city Kelly multiplier machinery DELETED (ultimate_alpha_2026-07-23
# group B): the Denver/Paris asymmetric-loss preference is model uncertainty
# that belongs in the walk-forward robust q_lcb, not a second tax at the
# sizer (FINAL_SPEC.md §What remains: "city and lead-time Kelly multipliers —
# Delete once their uncertainty is represented in walk-forward q⁻").


_ENV_UNIFIED_UNCERTAINTY_BUDGET = "ZEUS_UNIFIED_UNCERTAINTY_BUDGET"
_ENV_EVALUATOR_EQE_ENABLED = "ZEUS_EVALUATOR_ENTRY_QUOTE_EVIDENCE_ENABLED"


def _unified_uncertainty_budget_enabled() -> bool:
    """Wave 6 (2026-05-27, INV-40) feature gate.

    When OFF (default), dynamic_kelly_mult applies the legacy ci_width
    multiplicative haircuts AND ``_size_at_execution_price_boundary``
    multiplies ``effective_context.haircut()`` into the Kelly multiplier
    — even though the same uncertainty also reaches edge_ci_lower when
    Wave 5+5.5 are wired (double-count, conservative).

    When ON, the multiplicative haircuts are SKIPPED so the soft-uncertainty
    contribution enters Kelly EXACTLY ONCE via edge_LCB (per INV-40). Hard
    vetoes (oracle_penalty=0, strategy_phase=0, executable_mask=0) stay
    multiplicative.

    SAFETY DIRECTION (corrected after Copilot review of PR #348):
        On the multiplier basis alone, flag ON produces equal-or-LARGER
        multipliers than flag OFF (because it REMOVES haircuts ≤ 1.0).
        The compensating SMALLER edge comes from edge_LCB widening via
        σ_market once Wave 5.5 (EntryQuoteEvidence) is also active in the
        evaluator. Net sizing under the staged-promotion path:

            Stage 0 (both flags OFF, default):   baseline
            Stage 1 (Wave 5.5 ON only):          size ≤ Stage 0 (more conservative)
            Stage 2 (both ON):                   size validated by replay to
                                                 stay within Stage-0 envelope
                                                 (math spec §15.8 acceptance
                                                 criterion size_unified /
                                                 size_legacy ∈ [1.0, 1.2]).

    HARD ORDERING GUARD: flipping ``_ENV_UNIFIED_UNCERTAINTY_BUDGET=1``
    while ``_ENV_EVALUATOR_EQE_ENABLED=0`` removes multipliers WITHOUT the
    σ_market widening that compensates them. This combination is what the
    staged-promotion contract explicitly forbids (Stage 2 without Stage 1).
    Pre-Wave-6-post-Copilot-review fix (2026-05-27): this function
    REFUSES to report enabled=True unless the Wave 5.5 flag is also set.
    Operator must promote in order 0 → 1 → 2.
    """
    import os
    own = os.environ.get(_ENV_UNIFIED_UNCERTAINTY_BUDGET, "0") in ("1", "true", "TRUE")
    if not own:
        return False
    prereq = os.environ.get(_ENV_EVALUATOR_EQE_ENABLED, "0") in ("1", "true", "TRUE")
    if not prereq:
        # K5#5 + X10 fix: hard ordering guard so flag-flip ordering bug
        # cannot silently expose live capital to single-count math without
        # the compensating σ_market widening from Wave 5.5.
        import logging
        logging.getLogger(__name__).warning(
            "ZEUS_UNIFIED_UNCERTAINTY_BUDGET=1 ignored because "
            "ZEUS_EVALUATOR_ENTRY_QUOTE_EVIDENCE_ENABLED is not set. "
            "Staged-promotion contract requires Wave 5.5 (EQE wiring) BEFORE "
            "Wave 6 (unified budget) — see math spec §15.8 + plan §Wave 5.5."
        )
        return False
    return True


def dynamic_kelly_mult(
    base: float = 0.25,
    ci_width: float = 0.0,
    lead_days: float = 0.0,
    portfolio_heat: float = 0.0,
    strategy_key: str | None = None,
    city: str | None = None,
    *,
    market_uncertainty_in_lcb: bool = False,
) -> float:
    """Kelly fraction under the one decision law (ultimate_alpha 2026-07-23).

    One-law form: the CI-width, lead-time, per-strategy, and per-city
    multiplicative stages are DELETED — each rescaled uncertainty that the
    robust q_lcb bound already carries into the edge (the same double-count
    INV-40 flagged for ci_width when the unified budget is on; the one law
    consumes q_lcb everywhere, so the collapse is now unconditional).
    ``ci_width`` / ``lead_days`` / ``city`` / ``market_uncertainty_in_lcb``
    remain in the signature for call-site stability; they no longer scale
    the fraction. ``strategy_key`` survives only as the identity fail-closed
    gate (unknown key → 0.0 upstream via strategy_kelly_multiplier).

    NAMED PR-1 EXCEPTION — ``portfolio_heat`` STAYS: it is the only
    portfolio-correlation pressure control until the PR-2 joint allocator
    (structural-Σ simultaneous Kelly) replaces it. Deleting it here would
    open a window with correlated same-day bets sized fully independently.
    PR-2 removes this stage when joint sizing lands (COLLISION.md group B
    named decision; FINAL_SPEC.md dissolves it "into joint sizing", which
    does not exist yet in PR-1).
    """
    # C1/INV-13: provenance check — kelly_mult is registered in provenance_registry.yaml
    require_provenance("kelly_mult")

    del ci_width, lead_days, city, market_uncertainty_in_lcb  # no longer sizing inputs

    m = base

    # Portfolio concentration: positive heat → reduce marginal sizing (soft
    # reciprocal attenuation, not a hard cap). PR-1 survivor — see docstring.
    if portfolio_heat > 0.0:
        m *= 1.0 / (1.0 + portfolio_heat)

    # INV-05 / §P9.7: risk inputs must never collapse to zero or NaN here;
    # a legitimate 0.0 only enters via the strategy identity gate below.
    if not (m == m):  # NaN check: NaN != NaN
        raise ValueError(
            f"dynamic_kelly_mult produced NaN (base={base}, portfolio_heat={portfolio_heat})"
        )
    if m <= 0.0:
        raise ValueError(
            f"dynamic_kelly_mult collapsed to {m} — refusing to fabricate a floor value"
        )
    if strategy_key is not None:
        m *= strategy_kelly_multiplier(strategy_key)
    return m
