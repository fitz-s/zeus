from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class FullFamilyHypothesis:
    index: int
    range_label: str
    direction: str
    edge: float
    ci_lower: float
    ci_upper: float
    p_value: float
    passed_prefilter: bool



def _label_for_bin(bin_obj: Any) -> str:
    return getattr(bin_obj, "label", None) or getattr(bin_obj, "range_label", None) or str(bin_obj)



def scan_full_hypothesis_family(analysis: Any, n_bootstrap: int | None = None) -> list[FullFamilyHypothesis]:
    """Scan the full tested family, not only the positive-CI subset.

    Expected interface from the existing Zeus MarketAnalysis object:
    - bins
    - p_posterior
    - p_market
    - _bootstrap_bin(index, n_bootstrap)
    - _bootstrap_bin_no(index, n_bootstrap)
    """
    if n_bootstrap is None:
        n_bootstrap = 5000

    results: list[FullFamilyHypothesis] = []
    for i, b in enumerate(analysis.bins):
        range_label = _label_for_bin(b)
        edge_yes = float(analysis.p_posterior[i] - analysis.p_market[i])
        ci_lo_yes, ci_hi_yes, p_yes = analysis._bootstrap_bin(i, n_bootstrap)
        results.append(
            FullFamilyHypothesis(
                index=i,
                range_label=range_label,
                direction="buy_yes",
                edge=edge_yes,
                ci_lower=float(ci_lo_yes),
                ci_upper=float(ci_hi_yes),
                p_value=float(p_yes),
                passed_prefilter=(edge_yes > 0 and ci_lo_yes > 0),
            )
        )

        edge_no = float((1.0 - analysis.p_posterior[i]) - (1.0 - analysis.p_market[i]))
        ci_lo_no, ci_hi_no, p_no = analysis._bootstrap_bin_no(i, n_bootstrap)
        results.append(
            FullFamilyHypothesis(
                index=i,
                range_label=range_label,
                direction="buy_no",
                edge=edge_no,
                ci_lower=float(ci_lo_no),
                ci_upper=float(ci_hi_no),
                p_value=float(p_no),
                passed_prefilter=(edge_no > 0 and ci_lo_no > 0),
            )
        )
    return results
