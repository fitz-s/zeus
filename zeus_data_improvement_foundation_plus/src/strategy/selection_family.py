"""Family-wise hypothesis testing for Zeus.

The key correction here is conceptual, not just algorithmic:
FDR must run over the full tested hypothesis family, not only the prefiltered positive edges.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class HypothesisRecord:
    family_id: str
    hypothesis_id: str
    city: str
    target_date: str
    range_label: str
    direction: str
    p_value: float
    edge: float
    ci_lower: float
    ci_upper: float
    tested: bool = True
    passed_prefilter: bool = False


def benjamini_hochberg_mask(p_values: np.ndarray, q: float) -> np.ndarray:
    """Return a boolean mask of discoveries under BH."""
    p_values = np.asarray(p_values, dtype=float)
    n = len(p_values)
    if n == 0:
        return np.zeros(0, dtype=bool)

    order = np.argsort(p_values)
    ranked = p_values[order]
    thresholds = q * (np.arange(1, n + 1) / n)
    passed = ranked <= thresholds

    if not np.any(passed):
        return np.zeros(n, dtype=bool)

    k = np.where(passed)[0].max()
    cutoff = ranked[k]
    return p_values <= cutoff


def apply_familywise_fdr(df: pd.DataFrame, q: float = 0.10) -> pd.DataFrame:
    """Apply BH separately to each hypothesis family.

    Expected columns:
        family_id, p_value, tested
    Optional columns:
        edge, ci_lower, ci_upper, passed_prefilter
    """
    required = {"family_id", "p_value", "tested"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")

    work = df.copy()
    work["selected_post_fdr"] = 0
    work["q_value"] = np.nan

    for family_id, family_df in work.groupby("family_id"):
        mask = family_df["tested"].astype(bool).to_numpy()
        tested_idx = family_df.index[mask]
        if len(tested_idx) == 0:
            continue

        p_vals = family_df.loc[tested_idx, "p_value"].to_numpy(dtype=float)
        selected = benjamini_hochberg_mask(p_vals, q=q)

        # Simple BH-style monotone q-value estimate for reporting.
        order = np.argsort(p_vals)
        ranked = p_vals[order]
        n = len(ranked)
        q_vals = ranked * n / np.arange(1, n + 1)
        q_vals = np.minimum.accumulate(q_vals[::-1])[::-1]
        q_vals_full = np.empty_like(q_vals)
        q_vals_full[order] = q_vals

        work.loc[tested_idx, "q_value"] = q_vals_full
        work.loc[tested_idx[selected], "selected_post_fdr"] = 1

    return work


def make_family_id(
    *,
    cycle_mode: str,
    city: str,
    target_date: str,
    strategy_key: str,
    discovery_mode: str,
    decision_snapshot_id: str | None = None,
) -> str:
    parts = [cycle_mode, city, target_date, strategy_key, discovery_mode]
    if decision_snapshot_id:
        parts.append(decision_snapshot_id)
    return "|".join(parts)


def from_market_analysis_rows(rows: Iterable[dict]) -> pd.DataFrame:
    """Normalize raw market-analysis candidates into a family-aware frame."""
    frame = pd.DataFrame(list(rows))
    expected = {"family_id", "hypothesis_id", "city", "target_date", "range_label", "direction", "p_value"}
    missing = expected - set(frame.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    if "tested" not in frame.columns:
        frame["tested"] = True
    if "passed_prefilter" not in frame.columns:
        frame["passed_prefilter"] = False
    return frame
