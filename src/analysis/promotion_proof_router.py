# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/reference/zeus_strategy_spec.md §20 + §22
#                  + docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §16
"""Proof-class router — map each strategy to Pipeline A or Pipeline B.

§16 (STRATEGY_TAXONOMY_DIRECTIVE): two evidence pipelines exist:
  A (deterministic):  neg_risk_basket, settlement_capture, resolution_window_maker,
                       center_sell YES/NO-parity sub-type,
                       stale_quote_detector FOK-latency sub-type.
  B (stochastic/CI):  opening_inertia, center_buy, center_sell model-NO sub-type,
                       shoulder_buy, weather_event_arbitrage,
                       liquidity_provision_with_heartbeat,
                       cross_market_correlation_hedge,
                       imminent_open_capture.

Sub-typed routing (center_sell, stale_quote_detector):
  The strategy_key alone is insufficient — both strategies have a sub-type that
  routes to A and a sub-type that routes to B. Callers supply the optional
  `proof_type` from the decision record (DeterministicEdgeDecision.proof_type /
  VectorEdgeDecision.proof_type). When proof_type is provided, it takes precedence
  over the strategy_key default. When proof_type is absent, the strategy_key
  default applies (center_sell → B, stale_quote_detector → B — the conservative
  fallback: route to stochastic CI when sub-type is unknown).

Proof-type identifiers for A-sub-types (from §19 / §22):
  center_sell parity sub-type:    "pair_parity"
  stale_quote FOK sub-type:       "fok_latency"

All other proof_type values leave the default routing unchanged.

Literal["A", "B"] is the return type. Unknown strategy_keys return "B" (fail-safe
to the existing stochastic pipeline rather than silently skipping evidence).
"""
from __future__ import annotations

from typing import Literal, Optional

# ---------------------------------------------------------------------------
# Routing table
# ---------------------------------------------------------------------------

# Pipeline A strategies (deterministic payoff-identity evidence)
_PIPELINE_A_STRATEGY_KEYS: frozenset[str] = frozenset({
    "neg_risk_basket",
    "settlement_capture",
    "resolution_window_maker",
})

# Sub-typed strategies: default to B; override to A on specific proof_type.
# Key: strategy_key; Value: set of proof_type values that map to A.
_PIPELINE_A_PROOF_TYPE_OVERRIDES: dict[str, frozenset[str]] = {
    "center_sell": frozenset({"pair_parity"}),
    "stale_quote_detector": frozenset({"fok_latency"}),
}

# Pipeline B strategies (calibrated stochastic CI evidence)
_PIPELINE_B_STRATEGY_KEYS: frozenset[str] = frozenset({
    "opening_inertia",
    "center_buy",
    "center_sell",        # default route (model-NO sub-type)
    "shoulder_buy",
    "weather_event_arbitrage",
    "liquidity_provision_with_heartbeat",
    "cross_market_correlation_hedge",
    "imminent_open_capture",
    "stale_quote_detector",  # default route (non-FOK sub-type)
})


# ---------------------------------------------------------------------------
# Public router
# ---------------------------------------------------------------------------

def route_proof_class(
    strategy_key: str,
    proof_type: Optional[str] = None,
) -> Literal["A", "B"]:
    """Return the promotion pipeline ("A" or "B") for a given strategy.

    Parameters
    ----------
    strategy_key:
        The strategy identifier (e.g. "neg_risk_basket", "center_sell").
    proof_type:
        Optional proof_type from the decision record. When supplied, used to
        resolve sub-typed strategies (center_sell, stale_quote_detector) that
        can route to either pipeline depending on sub-type.

    Returns
    -------
    Literal["A", "B"]
        "A" → use DeterministicEdgeVerifier (Pipeline A).
        "B" → use PromotionReadinessValidator / EvidenceReport (Pipeline B).

    Notes
    -----
    Unknown strategy_keys default to "B" (fail-safe: route to stochastic CI
    rather than skipping evidence entirely).
    """
    # Unconditional A keys
    if strategy_key in _PIPELINE_A_STRATEGY_KEYS:
        return "A"

    # Sub-typed: check proof_type override first
    if strategy_key in _PIPELINE_A_PROOF_TYPE_OVERRIDES:
        if proof_type is not None and proof_type in _PIPELINE_A_PROOF_TYPE_OVERRIDES[strategy_key]:
            return "A"
        # proof_type absent or not an A sub-type → fall through to B default
        return "B"

    # All other known B keys (and unknown keys) → B
    return "B"


def is_pipeline_a(
    strategy_key: str,
    proof_type: Optional[str] = None,
) -> bool:
    """Convenience predicate: True iff route_proof_class returns "A"."""
    return route_proof_class(strategy_key, proof_type) == "A"


def is_pipeline_b(
    strategy_key: str,
    proof_type: Optional[str] = None,
) -> bool:
    """Convenience predicate: True iff route_proof_class returns "B"."""
    return route_proof_class(strategy_key, proof_type) == "B"
