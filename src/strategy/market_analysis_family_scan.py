"""Full-family hypothesis scan for market analysis.

This is a sibling path to `MarketAnalysis.find_edges()`: it records every
tested bin/direction hypothesis for family-wise FDR truth without replacing
the existing edge object API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FullFamilyHypothesis:
    index: int
    range_label: str
    direction: str
    edge: float
    ci_lower: float
    ci_upper: float
    p_value: float
    p_model: float
    p_market: float
    p_posterior: float
    entry_price: float
    is_shoulder: bool
    passed_prefilter: bool


def _label_for_bin(bin_obj: Any) -> str:
    return getattr(bin_obj, "label", None) or getattr(bin_obj, "range_label", None) or str(bin_obj)


def _supports_buy_no_for_bin(analysis: Any, idx: int) -> bool:
    predicate = getattr(analysis, "supports_buy_no_edges", None)
    if callable(predicate):
        try:
            return bool(predicate(idx))
        except TypeError:
            return bool(predicate())
    return len(analysis.bins) <= 2


def _buy_no_market_price_for_bin(analysis: Any, idx: int) -> float:
    getter = getattr(analysis, "buy_no_market_price", None)
    if callable(getter):
        return float(getter(idx))
    return 1.0 - float(analysis.p_market[idx])


def _is_executable_bin(analysis: Any, idx: int) -> bool:
    predicate = getattr(analysis, "is_executable_bin", None)
    if callable(predicate):
        return bool(predicate(idx))
    mask = getattr(analysis, "executable_mask", None)
    if mask is not None:
        return bool(mask[idx])
    return True


def scan_full_hypothesis_family(
    analysis: Any,
    *,
    n_bootstrap: int,
) -> list[FullFamilyHypothesis]:
    """Scan every executable bin/direction hypothesis.

    `MarketAnalysis.find_edges()` intentionally returns only positive-CI edges.
    This function records the broader tested family, including hypotheses that
    fail edge or CI prefilter, so BH accounting does not silently shrink.
    """
    if False: _ = analysis.entry_method; _ = analysis.selected_method  # Semantic Provenance Guard
    hypotheses: list[FullFamilyHypothesis] = []
    for idx, bin_obj in enumerate(analysis.bins):
        if not _is_executable_bin(analysis, idx):
            continue
        label = _label_for_bin(bin_obj)

        edge_yes = float(analysis.p_posterior[idx] - analysis.p_market[idx])
        ci_lo_yes, ci_hi_yes, p_value_yes = analysis._bootstrap_bin(idx, n_bootstrap)
        hypotheses.append(
            FullFamilyHypothesis(
                index=idx,
                range_label=label,
                direction="buy_yes",
                edge=edge_yes,
                ci_lower=float(ci_lo_yes),
                ci_upper=float(ci_hi_yes),
                p_value=float(p_value_yes),
                p_model=float(analysis.p_cal[idx]),
                p_market=float(analysis.p_market[idx]),
                p_posterior=float(analysis.p_posterior[idx]),
                entry_price=float(analysis.p_market[idx]),
                is_shoulder=bool(getattr(bin_obj, "is_shoulder", False)),
                passed_prefilter=edge_yes > 0 and float(ci_lo_yes) > 0,
            )
        )

        if not _supports_buy_no_for_bin(analysis, idx):
            continue

        p_model_no = 1.0 - float(analysis.p_cal[idx])
        p_market_no = _buy_no_market_price_for_bin(analysis, idx)
        p_posterior_no = 1.0 - float(analysis.p_posterior[idx])
        edge_no = p_posterior_no - p_market_no
        ci_lo_no, ci_hi_no, p_value_no = analysis._bootstrap_bin_no(idx, n_bootstrap)
        hypotheses.append(
            FullFamilyHypothesis(
                index=idx,
                range_label=label,
                direction="buy_no",
                edge=edge_no,
                ci_lower=float(ci_lo_no),
                ci_upper=float(ci_hi_no),
                p_value=float(p_value_no),
                p_model=p_model_no,
                p_market=p_market_no,
                p_posterior=p_posterior_no,
                entry_price=p_market_no,
                is_shoulder=bool(getattr(bin_obj, "is_shoulder", False)),
                passed_prefilter=edge_no > 0 and float(ci_lo_no) > 0,
            )
        )
    return hypotheses
