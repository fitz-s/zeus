"""Family-wise hypothesis selection helpers.

The active evaluator uses this module after the full-family scan. Family scope
is determined by `family_id`: currently one candidate/market/snapshot family
across strategy keys, not one whole-cycle family across all markets.

Two scope-aware family-id helpers — the only canonical entry points:
  - make_hypothesis_family_id() — per-candidate BH budget, no strategy_key
  - make_edge_family_id()       — per-strategy BH budget, requires strategy_key

Phase 1 (2026-04-16) introduced these alongside a deprecated `make_family_id()`
wrapper for migration. ultrareview25_remediation 2026-05-01 P1-6 retired the
wrapper after `tests/test_no_deprecated_make_family_id_calls.py` confirmed
zero production callers remain. INV-22 ("one canonical family grammar") is now
satisfied structurally — the wrapper is gone, not just unused.

If you find yourself wanting to re-add `make_family_id()`, route through the
two scope-aware helpers instead — they are the canonical contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class HypothesisRecord:
    family_id: str
    hypothesis_id: str
    p_value: float
    tested: bool = True
    passed_prefilter: bool = False


def make_hypothesis_family_id(
    *,
    cycle_mode: str,
    city: str,
    target_date: str,
    temperature_metric: Literal["high", "low"],
    discovery_mode: str,
    decision_snapshot_id: str = "",
) -> str:
    """Canonical family ID for the per-candidate (hypothesis) scope.

    BH discovery budget is shared across all hypotheses for a single candidate
    × snapshot. Does NOT carry strategy_key — scope is per-candidate, not
    per-strategy.

    Encodes scope explicitly via "hyp|" prefix so IDs are always distinguishable
    from edge-scope IDs even when all other fields match.

    S4 R9 P10B: temperature_metric is a required kwarg inserted after target_date
    so HIGH and LOW candidates never share a family budget.
    """
    parts = ["hyp", cycle_mode, city, target_date, temperature_metric, discovery_mode]
    if decision_snapshot_id:
        parts.append(decision_snapshot_id)
    return "|".join(parts)


def make_edge_family_id(
    *,
    cycle_mode: str,
    city: str,
    target_date: str,
    temperature_metric: Literal["high", "low"],
    strategy_key: str,
    discovery_mode: str,
    decision_snapshot_id: str = "",
) -> str:
    """Canonical family ID for the per-strategy (edge) scope.

    BH discovery budget is scoped to a single (candidate × strategy × snapshot).
    Carries strategy_key — a different strategy_key always produces a different ID.

    Encodes scope explicitly via "edge|" prefix so IDs are always distinguishable
    from hypothesis-scope IDs even when all other fields match.

    S4 R9 P10B: temperature_metric is a required kwarg inserted after target_date
    so HIGH and LOW edges never share a family budget.

    Raises:
        ValueError: if strategy_key is falsy (empty string or None). An edge
            family requires a real strategy to prevent silent scope collapse.
    """
    if not strategy_key:
        raise ValueError(
            f"make_edge_family_id requires a non-empty strategy_key; "
            f"got {strategy_key!r}. Use make_hypothesis_family_id for per-candidate scope."
        )
    parts = ["edge", cycle_mode, city, target_date, temperature_metric, strategy_key, discovery_mode]
    if decision_snapshot_id:
        parts.append(decision_snapshot_id)
    return "|".join(parts)


def benjamini_hochberg_mask(p_values: list[float], q: float) -> list[bool]:
    """Return discovery mask under Benjamini-Hochberg."""
    n = len(p_values)
    if n == 0:
        return []
    ordered = sorted(enumerate(p_values), key=lambda item: item[1])
    threshold_index = -1
    for rank, (_idx, p_value) in enumerate(ordered, start=1):
        if float(p_value) <= q * rank / n:
            threshold_index = rank - 1
    if threshold_index < 0:
        return [False] * n
    cutoff = float(ordered[threshold_index][1])
    return [float(p_value) <= cutoff for p_value in p_values]


def _bh_q_values(p_values: list[float]) -> list[float]:
    n = len(p_values)
    if n == 0:
        return []
    ordered = sorted(enumerate(p_values), key=lambda item: item[1])
    ranked_q = [0.0] * n
    running = 1.0
    for reverse_rank, (idx, p_value) in enumerate(reversed(ordered), start=1):
        rank = n - reverse_rank + 1
        running = min(running, float(p_value) * n / rank)
        ranked_q[idx] = running
    return ranked_q


def apply_familywise_fdr(rows: list[dict], q: float = 0.10) -> list[dict]:
    """Apply BH independently per `family_id` over all tested hypotheses."""
    out = [dict(row) for row in rows]
    by_family: dict[str, list[int]] = {}
    for idx, row in enumerate(out):
        if not bool(row.get("tested", True)):
            row["q_value"] = None
            row["selected_post_fdr"] = 0
            continue
        by_family.setdefault(str(row["family_id"]), []).append(idx)

    for indices in by_family.values():
        p_values = [float(out[idx]["p_value"]) for idx in indices]
        selected = benjamini_hochberg_mask(p_values, q)
        q_values = _bh_q_values(p_values)
        for local_idx, row_idx in enumerate(indices):
            out[row_idx]["q_value"] = q_values[local_idx]
            out[row_idx]["selected_post_fdr"] = int(selected[local_idx])

    return out
