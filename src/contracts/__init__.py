"""Semantic boundary contracts for cross-module invariants."""

from importlib import import_module
from typing import Any

from src.contracts.semantic_types import (
    Direction,
    DecisionSnapshotRef,
    EntryMethod,
    HeldSideProbability,
    NativeSidePrice,
    StrategyAttribution,
    compute_forward_edge,
    compute_native_limit_price,
    recompute_native_probability,
)
from src.contracts.execution_intent import (
    CORRECTED_PRICING_SEMANTICS_VERSION,
    ClobSweepResult,
    DecisionSourceContext,
    ExecutableCostBasis,
    ExecutableTradeHypothesis,
    ExecutionIntent,
    FinalExecutionIntent,
    simulate_clob_sweep,
)

_LAZY_EXPORTS = {
    "ExpiringAssumption": ("src.contracts.expiring_assumption", "ExpiringAssumption"),
    "EdgeContext": ("src.contracts.edge_context", "EdgeContext"),
    "EpistemicContext": ("src.contracts.epistemic_context", "EpistemicContext"),
    "FXClassification": ("src.contracts.fx_classification", "FXClassification"),
    "FXClassificationPending": (
        "src.contracts.fx_classification",
        "FXClassificationPending",
    ),
    "SettlementSemantics": (
        "src.contracts.settlement_semantics",
        "SettlementSemantics",
    ),
    "ExecutableMarketSnapshot": (
        "src.contracts.executable_market_snapshot",
        "ExecutableMarketSnapshot",
    ),
    "MarketSnapshotError": (
        "src.contracts.executable_market_snapshot",
        "MarketSnapshotError",
    ),
    "StaleMarketSnapshotError": (
        "src.contracts.executable_market_snapshot",
        "StaleMarketSnapshotError",
    ),
}


def __getattr__(name: str) -> Any:
    """Preserve package exports without importing unrelated contract stacks."""

    try:
        module_name, attribute_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))

__all__ = [
    "Direction",
    "DecisionSourceContext",
    "DecisionSnapshotRef",
    "EntryMethod",
    "HeldSideProbability",
    "NativeSidePrice",
    "StrategyAttribution",
    "compute_forward_edge",
    "compute_native_limit_price",
    "recompute_native_probability",
    "CORRECTED_PRICING_SEMANTICS_VERSION",
    "ClobSweepResult",
    "ExecutableCostBasis",
    "ExecutableTradeHypothesis",
    "ExecutionIntent",
    "FinalExecutionIntent",
    "simulate_clob_sweep",
    "ExpiringAssumption",
    "EdgeContext",
    "EpistemicContext",
    "FXClassification",
    "FXClassificationPending",
    "SettlementSemantics",
    "ExecutableMarketSnapshot",
    "MarketSnapshotError",
    "StaleMarketSnapshotError",
]
